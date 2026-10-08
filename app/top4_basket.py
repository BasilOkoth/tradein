import asyncio
import json
import os
import uuid
from datetime import datetime
from collections import defaultdict

import httpx
from fastapi import HTTPException

from .db import SessionLocal
from .models import TradingSession, DerivAccount, TradeLog
from .engine import engine


class Top4BasketService:
    """
    Manual Top-4 basket layer built on the existing DigitMatchStar engine.

    Invariants:
    - Uses the existing server-side DigitScore ranking.
    - Freezes ranks #1-#4 from one snapshot at PREPARE time.
    - Splits total basket stake equally across the four digits.
    - No basket is purchased automatically.
    - DEMO requires explicit APPROVE and then sends four DEMO buys.
    - REAL requires explicit APPROVE but does not submit real-money buys.
    - Existing single-target engine/session behavior is not modified.
    """

    VERSION = "TOP4_MANUAL_BASKET_V1"

    def __init__(self):
        self.pending = {}          # sid -> prepared basket dict
        self.live = {}             # basket_id -> runtime dict
        self._locks = defaultdict(asyncio.Lock)
        self._settlement_tasks = {}

    @staticmethod
    def _split_stake(total):
        total = round(float(total), 2)
        if total <= 0:
            raise HTTPException(400, "basket_stake must be > 0")
        a = round(total / 4.0, 2)
        stakes = [a, a, a, round(total - (3 * a), 2)]
        if min(stakes) <= 0:
            raise HTTPException(400, "basket stake is too small to split across four legs")
        return stakes

    @staticmethod
    def _owned_session(db, user_id, sid):
        s = db.get(TradingSession, int(sid))
        if not s or str(s.user_id) != str(user_id):
            raise HTTPException(404, "Session not found")
        return s

    @staticmethod
    def _ranking_snapshot(sid):
        # Use the existing score snapshot when possible. Calling the existing
        # scorer is safe/read-only and keeps this branch aligned with V1.
        score = engine._score_all_digits(int(sid))
        ranking = list(score.get("ranking") or [])
        ranking.sort(key=lambda row: float(row.get("score") or 0), reverse=True)
        if len(ranking) < 4:
            raise HTTPException(409, "DigitScore is still warming; Top-4 ranking is not ready")
        return score, ranking[:4]

    async def _currency(self, db, session):
        acct = (
            db.query(DerivAccount)
            .filter(
                DerivAccount.user_id == session.user_id,
                DerivAccount.account_id == session.account_id,
            )
            .first()
        )
        return str(acct.currency or "USD").upper() if acct else "USD"

    async def _telegram(self, text):
        token = str(os.getenv("TELEGRAM_BOT_TOKEN") or "").strip()
        chat_id = str(
            os.getenv("TELEGRAM_ADMIN_CHAT_ID")
            or os.getenv("TELEGRAM_CHAT_ID")
            or ""
        ).strip()
        if not token or not chat_id:
            return
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                await client.post(
                    f"https://api.telegram.org/bot{token}/sendMessage",
                    json={
                        "chat_id": chat_id,
                        "text": text,
                        "disable_web_page_preview": True,
                    },
                )
        except Exception:
            # Telegram must never break basket preparation/execution.
            pass

    async def prepare(self, *, user_id, sid, basket_stake):
        async with self._locks[int(sid)]:
            db = SessionLocal()
            try:
                s = self._owned_session(db, user_id, sid)

                if s.open_contract_id:
                    raise HTTPException(
                        409,
                        "The single-target session currently has an open contract. "
                        "Let it settle before preparing a Top-4 basket.",
                    )

                score, top4 = self._ranking_snapshot(s.id)
                stakes = self._split_stake(basket_stake)
                currency = await self._currency(db, s)
                client = await engine._client(
                    s.user_id,
                    s.account_id,
                )

                # Freeze exact server tick provenance visible at preparation.
                source_tick = dict(engine.latest_ticks.get(s.id) or {})
                source_epoch = int(
                    source_tick.get("epoch")
                    or engine.latest_tick_epoch.get(s.id)
                    or 0
                )

                legs = []
                # Proposals are read-only. The existing Deriv client serializes
                # these requests and handles RateLimit safely.
                for idx, (row, stake) in enumerate(zip(top4, stakes), start=1):
                    proposal = await client.proposal_digitmatch(
                        symbol=s.symbol,
                        digit=int(row["digit"]),
                        amount=float(stake),
                        duration=1,
                        currency=currency,
                    )
                    p = proposal.get("proposal") or {}
                    if not p.get("id"):
                        raise HTTPException(
                            502,
                            f"Deriv returned no proposal for rank #{idx}",
                        )

                    legs.append({
                        "rank": idx,
                        "digit": int(row["digit"]),
                        "score": float(row.get("score") or 0),
                        "stake": float(stake),
                        "proposal_id": str(p["id"]),
                        "ask_price": float(p.get("ask_price") or stake),
                        "payout": float(p.get("payout") or 0),
                        "spot": p.get("spot"),
                    })

                basket_id = uuid.uuid4().hex
                prepared = {
                    "version": self.VERSION,
                    "basket_id": basket_id,
                    "session_id": s.id,
                    "user_id": s.user_id,
                    "account_id": s.account_id,
                    "account_mode": str(s.account_mode).upper(),
                    "symbol": s.symbol,
                    "currency": currency,
                    "basket_stake": round(sum(x["stake"] for x in legs), 2),
                    "source_epoch": source_epoch,
                    "source_tick": source_tick,
                    "score_version": score.get("version"),
                    "history_count": score.get("history_count"),
                    "top_margin": score.get("top_margin"),
                    "shadow": score.get("shadow"),
                    "legs": legs,
                    "status": "AWAITING_APPROVAL",
                    "prepared_at": datetime.utcnow().isoformat(),
                }
                self.pending[s.id] = prepared

                await self._telegram(
                    "DigitMatchStar TOP-4 basket prepared\n"
                    f"Mode: {prepared['account_mode']}\n"
                    f"Basket: {basket_id[:10]}\n"
                    f"Digits: {', '.join(str(x['digit']) for x in legs)}\n"
                    f"Total: {prepared['basket_stake']:.2f} {currency}\n"
                    "Manual approval is required."
                )
                return prepared
            finally:
                db.close()

    async def reject(self, *, user_id, sid):
        async with self._locks[int(sid)]:
            db = SessionLocal()
            try:
                self._owned_session(db, user_id, sid)
                pending = self.pending.pop(int(sid), None)
                if not pending:
                    raise HTTPException(404, "No Top-4 basket is awaiting approval")
                pending["status"] = "REJECTED"
                pending["rejected_at"] = datetime.utcnow().isoformat()
                return pending
            finally:
                db.close()

    async def approve(self, *, user_id, sid, basket_id):
        async with self._locks[int(sid)]:
            db = SessionLocal()
            try:
                s = self._owned_session(db, user_id, sid)
                pending = self.pending.get(s.id)

                if not pending or pending.get("basket_id") != str(basket_id):
                    raise HTTPException(404, "Prepared basket was not found or has changed")

                if pending.get("status") != "AWAITING_APPROVAL":
                    raise HTTPException(409, "Basket is not awaiting approval")

                # Revalidate that no single-target contract opened after prepare.
                if s.open_contract_id:
                    raise HTTPException(
                        409,
                        "A single-target contract opened after preparation. "
                        "Reject and prepare a fresh Top-4 basket.",
                    )

                mode = str(s.account_mode).upper()

                if mode == "REAL":
                    # Manual approval is recorded, but this branch intentionally
                    # does not transmit real-money buy instructions.
                    pending["status"] = "REAL_APPROVED_NOT_SENT"
                    pending["approved_at"] = datetime.utcnow().isoformat()
                    pending["real_execution_sent"] = False
                    self.pending.pop(s.id, None)

                    await self._telegram(
                        "DigitMatchStar TOP-4 REAL basket approved\n"
                        f"Basket: {basket_id[:10]}\n"
                        f"Digits: {', '.join(str(x['digit']) for x in pending['legs'])}\n"
                        "Approval recorded. No real-money order was transmitted."
                    )
                    return pending

                client = await engine._client(
                    s.user_id,
                    s.account_id,
                )

                # Buy all four already-prepared DEMO proposals concurrently.
                # DerivWS.buy() itself hard-enforces demo=True.
                buy_calls = [
                    client.buy(
                        leg["proposal_id"],
                        leg["ask_price"],
                        demo=True,
                    )
                    for leg in pending["legs"]
                ]
                results = await asyncio.gather(
                    *buy_calls,
                    return_exceptions=True,
                )

                failures = [
                    str(result)
                    for result in results
                    if isinstance(result, Exception)
                ]
                if failures:
                    pending["status"] = "DEMO_BUY_PARTIAL_OR_FAILED"
                    pending["errors"] = failures
                    pending["approved_at"] = datetime.utcnow().isoformat()
                    # Never auto-retry BUY because duplicate contracts are worse.
                    self.pending.pop(s.id, None)
                    raise HTTPException(
                        502,
                        "One or more DEMO buys were uncertain/failed. "
                        "No automatic retry was attempted. "
                        + " | ".join(failures[:4]),
                    )

                opened = []
                for leg, result in zip(pending["legs"], results):
                    buy = (result or {}).get("buy") or {}
                    contract_id = str(buy.get("contract_id") or "")
                    if not contract_id:
                        raise HTTPException(
                            502,
                            "Deriv returned a DEMO buy without contract_id; "
                            "manual reconciliation is required.",
                        )

                    leg_runtime = dict(leg)
                    leg_runtime.update({
                        "contract_id": contract_id,
                        "buy_price": float(
                            buy.get("buy_price")
                            or leg["ask_price"]
                        ),
                        "purchase_time": int(
                            buy.get("start_time")
                            or buy.get("purchase_time")
                            or 0
                        ),
                        "status": "OPEN",
                        "profit": None,
                    })
                    opened.append(leg_runtime)

                    db.add(
                        TradeLog(
                            user_id=s.user_id,
                            trading_session_id=s.id,
                            trade_no=int(leg["rank"]),
                            account_mode=s.account_mode,
                            account_id=s.account_id,
                            symbol=s.symbol,
                            digit=int(leg["digit"]),
                            stake=float(leg["stake"]),
                            contract_id=contract_id,
                            status="OPEN",
                            buy_price=leg_runtime["buy_price"],
                            payout=float(leg["payout"]),
                            raw_json=json.dumps({
                                "kind": "TOP4_BASKET_LEG",
                                "basket_id": basket_id,
                                "rank": int(leg["rank"]),
                                "source_epoch": pending["source_epoch"],
                                "score": float(leg["score"]),
                                "score_version": pending["score_version"],
                                "top_margin": pending["top_margin"],
                                "shadow": pending["shadow"],
                                "deriv_buy": result,
                            }),
                        )
                    )

                db.commit()

                runtime = dict(pending)
                runtime["legs"] = opened
                runtime["status"] = "OPEN"
                runtime["approved_at"] = datetime.utcnow().isoformat()
                runtime["real_execution_sent"] = False
                self.live[basket_id] = runtime
                self.pending.pop(s.id, None)

                # Settlement runs in background and never opens another basket.
                task = asyncio.create_task(
                    self._settle_demo_basket(
                        sid=s.id,
                        user_id=s.user_id,
                        account_id=s.account_id,
                        basket_id=basket_id,
                    )
                )
                self._settlement_tasks[basket_id] = task

                await self._telegram(
                    "DigitMatchStar TOP-4 DEMO basket OPEN\n"
                    f"Basket: {basket_id[:10]}\n"
                    f"Digits: {', '.join(str(x['digit']) for x in opened)}\n"
                    f"Total stake: {runtime['basket_stake']:.2f} {runtime['currency']}"
                )
                return runtime
            finally:
                db.close()

    async def _settle_demo_basket(
        self,
        *,
        sid,
        user_id,
        account_id,
        basket_id,
    ):
        runtime = self.live.get(basket_id)
        if not runtime:
            return

        client = await engine._client(user_id, account_id)
        unresolved = {
            str(leg["contract_id"]): leg
            for leg in runtime["legs"]
        }

        deadline = asyncio.get_running_loop().time() + 30.0

        while unresolved and asyncio.get_running_loop().time() < deadline:
            for contract_id, leg in list(unresolved.items()):
                try:
                    data = await client.contract_status(contract_id)
                    contract = data.get("proposal_open_contract") or {}
                    if not contract.get("is_sold"):
                        continue

                    profit = float(contract.get("profit") or 0)
                    leg["status"] = "WIN" if profit > 0 else "LOSS"
                    leg["profit"] = profit
                    leg["sell_price"] = float(contract.get("sell_price") or 0)
                    leg["exit_tick"] = (
                        contract.get("exit_tick_display_value")
                        or contract.get("exit_tick")
                        or contract.get("current_spot")
                    )
                    leg["settled_at"] = datetime.utcnow().isoformat()

                    db = SessionLocal()
                    try:
                        log = (
                            db.query(TradeLog)
                            .filter(
                                TradeLog.trading_session_id == int(sid),
                                TradeLog.contract_id == str(contract_id),
                            )
                            .order_by(TradeLog.id.desc())
                            .first()
                        )
                        if log:
                            log.status = leg["status"]
                            log.profit = profit
                            log.settled_at = datetime.utcnow()
                            try:
                                raw = json.loads(log.raw_json or "{}")
                            except Exception:
                                raw = {}
                            raw["deriv_settlement"] = data
                            raw["basket_leg_result"] = leg["status"]
                            log.raw_json = json.dumps(raw)
                            db.commit()
                    finally:
                        db.close()

                    unresolved.pop(contract_id, None)
                except Exception:
                    continue

            if unresolved:
                await asyncio.sleep(0.35)

        runtime["status"] = (
            "SETTLED"
            if not unresolved
            else "SETTLEMENT_RECONCILE_REQUIRED"
        )
        runtime["settled_at"] = datetime.utcnow().isoformat()
        runtime["net_profit"] = round(
            sum(float(leg.get("profit") or 0) for leg in runtime["legs"]),
            2,
        )
        runtime["winning_ranks"] = [
            int(leg["rank"])
            for leg in runtime["legs"]
            if leg.get("status") == "WIN"
        ]

        await self._telegram(
            "DigitMatchStar TOP-4 DEMO basket SETTLED\n"
            f"Basket: {basket_id[:10]}\n"
            f"Winning rank(s): {runtime['winning_ranks'] or 'none'}\n"
            f"Net P/L: {runtime['net_profit']:+.2f} {runtime['currency']}\n"
            f"Status: {runtime['status']}"
        )

    def status(self, *, user_id, sid):
        db = SessionLocal()
        try:
            self._owned_session(db, user_id, sid)
        finally:
            db.close()

        pending = self.pending.get(int(sid))
        live = [
            value
            for value in self.live.values()
            if int(value.get("session_id") or 0) == int(sid)
        ]
        live.sort(key=lambda x: x.get("prepared_at") or "", reverse=True)

        return {
            "version": self.VERSION,
            "pending": pending,
            "latest": live[0] if live else None,
        }

    def export(self, *, user_id, sid):
        db = SessionLocal()
        try:
            self._owned_session(db, user_id, sid)

            logs = (
                db.query(TradeLog)
                .filter(TradeLog.trading_session_id == int(sid))
                .order_by(TradeLog.id.asc())
                .all()
            )

            baskets = {}
            for log in logs:
                try:
                    raw = json.loads(log.raw_json or "{}")
                except Exception:
                    continue

                if raw.get("kind") != "TOP4_BASKET_LEG":
                    continue

                basket_id = str(raw.get("basket_id") or "")
                if not basket_id:
                    continue

                b = baskets.setdefault(
                    basket_id,
                    {
                        "basket_id": basket_id,
                        "source_epoch": raw.get("source_epoch"),
                        "score_version": raw.get("score_version"),
                        "top_margin": raw.get("top_margin"),
                        "shadow": raw.get("shadow"),
                        "legs": [],
                    },
                )

                b["legs"].append({
                    "rank": raw.get("rank"),
                    "digit": log.digit,
                    "score": raw.get("score"),
                    "stake": log.stake,
                    "payout": log.payout,
                    "contract_id": log.contract_id,
                    "status": log.status,
                    "profit": log.profit,
                    "created_at": log.created_at.isoformat() if log.created_at else None,
                    "settled_at": log.settled_at.isoformat() if log.settled_at else None,
                })

            rows = []
            for basket_id, b in baskets.items():
                legs = sorted(
                    b["legs"],
                    key=lambda x: int(x.get("rank") or 99),
                )
                net = round(
                    sum(float(x.get("profit") or 0) for x in legs),
                    2,
                )
                winners = [
                    int(x["rank"])
                    for x in legs
                    if x.get("status") == "WIN"
                ]
                rows.append({
                    **b,
                    "legs": legs,
                    "net_profit": net,
                    "winning_ranks": winners,
                    "hit": bool(winners),
                })

            rows.sort(key=lambda x: x.get("source_epoch") or 0)

            settled = [
                row for row in rows
                if all(
                    leg.get("status") in {"WIN", "LOSS"}
                    for leg in row["legs"]
                )
                and len(row["legs"]) == 4
            ]

            total_stake = round(
                sum(
                    sum(float(x.get("stake") or 0) for x in row["legs"])
                    for row in settled
                ),
                2,
            )
            net_profit = round(
                sum(float(row["net_profit"]) for row in settled),
                2,
            )
            hits = sum(1 for row in settled if row["hit"])
            rank_wins = {
                str(rank): sum(
                    1 for row in settled
                    if rank in row["winning_ranks"]
                )
                for rank in range(1, 5)
            }

            return {
                "version": self.VERSION,
                "session_id": int(sid),
                "summary": {
                    "settled_baskets": len(settled),
                    "hits": hits,
                    "misses": len(settled) - hits,
                    "hit_rate_pct": round(
                        hits / len(settled) * 100, 2
                    ) if settled else 0.0,
                    "total_stake": total_stake,
                    "net_profit": net_profit,
                    "roi_pct": round(
                        net_profit / total_stake * 100, 2
                    ) if total_stake else 0.0,
                    "rank_wins": rank_wins,
                },
                "baskets": rows,
            }
        finally:
            db.close()


top4_baskets = Top4BasketService()
