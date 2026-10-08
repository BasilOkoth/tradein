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
    Immediate Top-4 DEMO basket execution.

    Design:
    - One fresh server-side DigitScore snapshot freezes ranks #1-#4.
    - There is NO PREPARE state and NO APPROVE state.
    - On DEMO, one request starts four proposal->buy pipelines immediately.
    - Each leg requests its fresh proposal and buys it immediately.
    - REAL accounts remain non-executing: ranking/preview only, no BUY sent.
    - Settlement happens in the background and never opens another basket.
    """

    VERSION = "TOP4_IMMEDIATE_DEMO_V2"

    def __init__(self):
        self.live = {}              # basket_id -> runtime dict
        self._locks = defaultdict(asyncio.Lock)
        self._settlement_tasks = {}

    @staticmethod
    def _split_stake(total):
        total = round(float(total), 2)
        if total <= 0:
            raise HTTPException(400, "basket_stake must be > 0")

        each = round(total / 4.0, 2)
        stakes = [each, each, each, round(total - (3 * each), 2)]

        if min(stakes) <= 0:
            raise HTTPException(
                400,
                "basket stake is too small to split across four legs",
            )
        return stakes

    @staticmethod
    def _owned_session(db, user_id, sid):
        s = db.get(TradingSession, int(sid))
        if not s or str(s.user_id) != str(user_id):
            raise HTTPException(404, "Session not found")
        return s

    @staticmethod
    def _ranking_snapshot(sid):
        score = engine._score_all_digits(int(sid))
        ranking = list(score.get("ranking") or [])
        ranking.sort(
            key=lambda row: float(row.get("score") or 0),
            reverse=True,
        )

        if len(ranking) < 4:
            raise HTTPException(
                409,
                "DigitScore is still warming; Top-4 ranking is not ready",
            )

        return score, ranking[:4]

    @staticmethod
    def _currency(db, session):
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
            pass

    async def execute_now(self, *, user_id, sid, basket_stake):
        """
        Freeze the current Top-4 ranking and execute immediately on DEMO.

        REAL mode deliberately does not transmit BUY instructions.
        """
        async with self._locks[int(sid)]:
            db = SessionLocal()

            try:
                s = self._owned_session(db, user_id, sid)

                if s.open_contract_id:
                    raise HTTPException(
                        409,
                        "The single-target session has an open contract. "
                        "Let it settle before starting a Top-4 basket.",
                    )

                score, top4 = self._ranking_snapshot(s.id)
                stakes = self._split_stake(basket_stake)
                currency = self._currency(db, s)

                source_tick = dict(engine.latest_ticks.get(s.id) or {})
                source_epoch = int(
                    source_tick.get("epoch")
                    or engine.latest_tick_epoch.get(s.id)
                    or 0
                )

                basket_id = uuid.uuid4().hex
                mode = str(s.account_mode or "").upper()

                frozen_legs = [
                    {
                        "rank": idx,
                        "digit": int(row["digit"]),
                        "score": float(row.get("score") or 0),
                        "stake": float(stake),
                    }
                    for idx, (row, stake)
                    in enumerate(zip(top4, stakes), start=1)
                ]

                base = {
                    "version": self.VERSION,
                    "basket_id": basket_id,
                    "session_id": s.id,
                    "user_id": s.user_id,
                    "account_id": s.account_id,
                    "account_mode": mode,
                    "symbol": s.symbol,
                    "currency": currency,
                    "basket_stake": round(sum(stakes), 2),
                    "source_epoch": source_epoch,
                    "source_tick": source_tick,
                    "score_version": score.get("version"),
                    "history_count": score.get("history_count"),
                    "top_margin": score.get("top_margin"),
                    "shadow": score.get("shadow"),
                    "legs": frozen_legs,
                    "triggered_at": datetime.utcnow().isoformat(),
                }

                # REAL remains read-only / non-executing.
                if mode == "REAL":
                    base["status"] = "REAL_PREVIEW_ONLY_NOT_SENT"
                    base["real_execution_sent"] = False
                    self.live[basket_id] = base

                    await self._telegram(
                        "DigitMatchStar TOP-4 REAL preview\n"
                        f"Basket: {basket_id[:10]}\n"
                        f"Digits: {', '.join(str(x['digit']) for x in frozen_legs)}\n"
                        "No real-money order was transmitted."
                    )
                    return base

                client = await engine._client(
                    s.user_id,
                    s.account_id,
                )

                async def proposal_then_buy(leg):
                    # Fresh proposal and immediate DEMO buy for this frozen digit.
                    proposal = await client.proposal_digitmatch(
                        symbol=s.symbol,
                        digit=int(leg["digit"]),
                        amount=float(leg["stake"]),
                        duration=1,
                        currency=currency,
                    )

                    p = proposal.get("proposal") or {}
                    proposal_id = str(p.get("id") or "")
                    if not proposal_id:
                        raise RuntimeError(
                            f"Deriv returned no proposal for rank #{leg['rank']}"
                        )

                    ask_price = float(
                        p.get("ask_price")
                        or leg["stake"]
                    )

                    buy_result = await client.buy(
                        proposal_id,
                        ask_price,
                        demo=True,
                    )

                    return {
                        **leg,
                        "proposal_id": proposal_id,
                        "ask_price": ask_price,
                        "payout": float(p.get("payout") or 0),
                        "spot": p.get("spot"),
                        "proposal_received_at": datetime.utcnow().isoformat(),
                        "deriv_buy": buy_result,
                    }

                # All four proposal->buy pipelines are launched together.
                results = await asyncio.gather(
                    *[
                        proposal_then_buy(leg)
                        for leg in frozen_legs
                    ],
                    return_exceptions=True,
                )

                failures = [
                    f"rank #{frozen_legs[i]['rank']}: {result}"
                    for i, result in enumerate(results)
                    if isinstance(result, Exception)
                ]

                successful_results = [
                    result
                    for result in results
                    if not isinstance(result, Exception)
                ]

                opened = []

                for item in successful_results:
                    buy = (item.get("deriv_buy") or {}).get("buy") or {}
                    contract_id = str(buy.get("contract_id") or "")

                    if not contract_id:
                        failures.append(
                            f"rank #{item['rank']}: buy returned no contract_id"
                        )
                        continue

                    leg_runtime = {
                        **item,
                        "contract_id": contract_id,
                        "buy_price": float(
                            buy.get("buy_price")
                            or item["ask_price"]
                        ),
                        "purchase_time": int(
                            buy.get("start_time")
                            or buy.get("purchase_time")
                            or 0
                        ),
                        "status": "OPEN",
                        "profit": None,
                    }
                    opened.append(leg_runtime)

                    db.add(
                        TradeLog(
                            user_id=s.user_id,
                            trading_session_id=s.id,
                            trade_no=int(item["rank"]),
                            account_mode=s.account_mode,
                            account_id=s.account_id,
                            symbol=s.symbol,
                            digit=int(item["digit"]),
                            stake=float(item["stake"]),
                            contract_id=contract_id,
                            status="OPEN",
                            buy_price=leg_runtime["buy_price"],
                            payout=float(item.get("payout") or 0),
                            raw_json=json.dumps({
                                "kind": "TOP4_BASKET_LEG",
                                "version": self.VERSION,
                                "basket_id": basket_id,
                                "rank": int(item["rank"]),
                                "source_epoch": source_epoch,
                                "score": float(item["score"]),
                                "score_version": score.get("version"),
                                "top_margin": score.get("top_margin"),
                                "shadow": score.get("shadow"),
                                "triggered_at": base["triggered_at"],
                                "proposal_received_at": item.get(
                                    "proposal_received_at"
                                ),
                                "deriv_buy": item["deriv_buy"],
                            }),
                        )
                    )

                db.commit()

                runtime = dict(base)
                runtime["legs"] = opened
                runtime["status"] = (
                    "OPEN"
                    if len(opened) == 4
                    else "PARTIAL_OPEN_RECONCILE_REQUIRED"
                )
                runtime["execution_failures"] = failures
                runtime["opened_count"] = len(opened)
                runtime["real_execution_sent"] = False
                runtime["opened_at"] = datetime.utcnow().isoformat()

                self.live[basket_id] = runtime

                if opened:
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
                    "DigitMatchStar TOP-4 DEMO immediate execution\n"
                    f"Basket: {basket_id[:10]}\n"
                    f"Digits: {', '.join(str(x['digit']) for x in frozen_legs)}\n"
                    f"Opened: {len(opened)}/4\n"
                    f"Total stake requested: {base['basket_stake']:.2f} {currency}"
                )

                if not opened:
                    raise HTTPException(
                        502,
                        "No DEMO Top-4 contracts opened. "
                        + " | ".join(failures[:4]),
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
            for leg in runtime.get("legs", [])
            if leg.get("contract_id")
        }

        deadline = asyncio.get_running_loop().time() + 30.0

        while (
            unresolved
            and asyncio.get_running_loop().time() < deadline
        ):
            for contract_id, leg in list(unresolved.items()):
                try:
                    data = await client.contract_status(contract_id)
                    contract = (
                        data.get("proposal_open_contract")
                        or {}
                    )

                    if not contract.get("is_sold"):
                        continue

                    profit = float(contract.get("profit") or 0)

                    leg["status"] = (
                        "WIN"
                        if profit > 0
                        else "LOSS"
                    )
                    leg["profit"] = profit
                    leg["sell_price"] = float(
                        contract.get("sell_price")
                        or 0
                    )
                    leg["exit_tick"] = (
                        contract.get("exit_tick_display_value")
                        or contract.get("exit_tick")
                        or contract.get("current_spot")
                    )
                    leg["settled_at"] = (
                        datetime.utcnow().isoformat()
                    )

                    db = SessionLocal()
                    try:
                        log = (
                            db.query(TradeLog)
                            .filter(
                                TradeLog.trading_session_id
                                == int(sid),
                                TradeLog.contract_id
                                == str(contract_id),
                            )
                            .order_by(TradeLog.id.desc())
                            .first()
                        )

                        if log:
                            log.status = leg["status"]
                            log.profit = profit
                            log.settled_at = datetime.utcnow()

                            try:
                                raw = json.loads(
                                    log.raw_json or "{}"
                                )
                            except Exception:
                                raw = {}

                            raw["deriv_settlement"] = data
                            raw["basket_leg_result"] = (
                                leg["status"]
                            )

                            log.raw_json = json.dumps(raw)
                            db.commit()
                    finally:
                        db.close()

                    unresolved.pop(contract_id, None)

                except Exception:
                    continue

            if unresolved:
                await asyncio.sleep(0.25)

        runtime["status"] = (
            "SETTLED"
            if not unresolved
            else "SETTLEMENT_RECONCILE_REQUIRED"
        )
        runtime["settled_at"] = datetime.utcnow().isoformat()
        runtime["net_profit"] = round(
            sum(
                float(leg.get("profit") or 0)
                for leg in runtime.get("legs", [])
            ),
            2,
        )
        runtime["winning_ranks"] = [
            int(leg["rank"])
            for leg in runtime.get("legs", [])
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

        live = [
            value
            for value in self.live.values()
            if int(value.get("session_id") or 0) == int(sid)
        ]
        live.sort(
            key=lambda x:
                x.get("triggered_at")
                or x.get("opened_at")
                or "",
            reverse=True,
        )

        return {
            "version": self.VERSION,
            "pending": None,
            "latest": live[0] if live else None,
        }

    def export(self, *, user_id, sid):
        db = SessionLocal()

        try:
            self._owned_session(db, user_id, sid)

            logs = (
                db.query(TradeLog)
                .filter(
                    TradeLog.trading_session_id == int(sid)
                )
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

                basket_id = str(
                    raw.get("basket_id") or ""
                )
                if not basket_id:
                    continue

                basket = baskets.setdefault(
                    basket_id,
                    {
                        "basket_id": basket_id,
                        "source_epoch": raw.get("source_epoch"),
                        "triggered_at": raw.get("triggered_at"),
                        "legs": [],
                    },
                )

                basket["legs"].append({
                    "rank": raw.get("rank"),
                    "digit": log.digit,
                    "stake": float(log.stake or 0),
                    "contract_id": log.contract_id,
                    "status": log.status,
                    "profit": float(log.profit or 0),
                    "score": raw.get("score"),
                    "proposal_received_at": raw.get(
                        "proposal_received_at"
                    ),
                    "settled_at": (
                        log.settled_at.isoformat()
                        if log.settled_at
                        else None
                    ),
                })

            settled_baskets = 0
            hits = 0
            misses = 0
            total_stake = 0.0
            net_profit = 0.0
            rank_wins = {
                "1": 0,
                "2": 0,
                "3": 0,
                "4": 0,
            }

            ordered = []

            for basket in baskets.values():
                legs = sorted(
                    basket["legs"],
                    key=lambda x:
                        int(x.get("rank") or 99),
                )
                basket["legs"] = legs

                statuses = {
                    str(x.get("status") or "").upper()
                    for x in legs
                }

                complete = (
                    len(legs) > 0
                    and all(
                        status in {"WIN", "LOSS"}
                        for status in statuses
                    )
                )

                if complete:
                    settled_baskets += 1

                    winners = [
                        x
                        for x in legs
                        if str(x.get("status")).upper()
                        == "WIN"
                    ]

                    if winners:
                        hits += 1
                    else:
                        misses += 1

                    for winner in winners:
                        key = str(winner.get("rank"))
                        if key in rank_wins:
                            rank_wins[key] += 1

                basket_stake = sum(
                    float(x.get("stake") or 0)
                    for x in legs
                )
                basket_profit = sum(
                    float(x.get("profit") or 0)
                    for x in legs
                )

                basket["total_stake"] = round(
                    basket_stake,
                    2,
                )
                basket["net_profit"] = round(
                    basket_profit,
                    2,
                )
                basket["winning_ranks"] = [
                    int(x["rank"])
                    for x in legs
                    if str(x.get("status")).upper()
                    == "WIN"
                ]

                total_stake += basket_stake
                net_profit += basket_profit
                ordered.append(basket)

            ordered.sort(
                key=lambda x:
                    x.get("triggered_at") or "",
            )

            hit_rate = (
                (hits / settled_baskets) * 100.0
                if settled_baskets
                else 0.0
            )
            roi = (
                (net_profit / total_stake) * 100.0
                if total_stake
                else 0.0
            )

            return {
                "version": self.VERSION,
                "session_id": int(sid),
                "summary": {
                    "settled_baskets": settled_baskets,
                    "hits": hits,
                    "misses": misses,
                    "hit_rate_pct": round(hit_rate, 2),
                    "total_stake": round(total_stake, 2),
                    "net_profit": round(net_profit, 2),
                    "roi_pct": round(roi, 2),
                    "rank_wins": rank_wins,
                },
                "baskets": ordered,
            }

        finally:
            db.close()


top4_baskets = Top4BasketService()
