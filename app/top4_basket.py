import asyncio
import json
import os
import uuid
from collections import defaultdict
from datetime import datetime

import httpx
from fastapi import HTTPException

from .db import SessionLocal
from .models import TradingSession, DerivAccount, TradeLog
from .engine import engine


class Top4BasketService:
    """Configurable simultaneous Top-N DEMO execution (1..7)."""

    VERSION = "TOPN_ONECLICK_REAL_V6"
    MIN_TOP_N = 1
    MAX_TOP_N = 7

    def __init__(self):
        self.live = {}
        self._locks = defaultdict(asyncio.Lock)
        self._settlement_tasks = {}

    @classmethod
    def _validate_top_n(cls, top_n):
        try:
            top_n = int(top_n)
        except Exception as exc:
            raise HTTPException(400, "top_n must be an integer") from exc
        if not cls.MIN_TOP_N <= top_n <= cls.MAX_TOP_N:
            raise HTTPException(400, "top_n must be between 1 and 7")
        return top_n

    @staticmethod
    def _split_stake(total, count):
        total = round(float(total), 2)
        count = int(count)
        if total <= 0:
            raise HTTPException(400, "basket_stake must be > 0")
        each = round(total / count, 2)
        stakes = [each] * count
        stakes[-1] = round(total - sum(stakes[:-1]), 2)
        if min(stakes) <= 0:
            raise HTTPException(400, "basket stake is too small for selected top_n")
        return stakes

    @staticmethod
    def _owned_session(db, user_id, sid):
        s = db.get(TradingSession, int(sid))
        if not s or str(s.user_id) != str(user_id):
            raise HTTPException(404, "Session not found")
        return s

    @staticmethod
    def _ranking_snapshot(sid, top_n):
        score = engine._score_all_digits(int(sid))
        ranking = sorted(
            list(score.get("ranking") or []),
            key=lambda row: float(row.get("score") or 0),
            reverse=True,
        )
        if len(ranking) < int(top_n):
            raise HTTPException(
                409,
                f"DigitScore is still warming; Top-{int(top_n)} ranking is not ready",
            )
        return score, ranking[: int(top_n)]

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
                    json={"chat_id": chat_id, "text": text, "disable_web_page_preview": True},
                )
        except Exception:
            pass

    async def execute_now(self, *, user_id, sid, basket_stake, top_n=7, execute_real_now=False):
        top_n = self._validate_top_n(top_n)

        async with self._locks[int(sid)]:
            db = SessionLocal()
            try:
                s = self._owned_session(db, user_id, sid)

                if s.open_contract_id:
                    raise HTTPException(
                        409,
                        "The single-target session has an open contract. Let it settle first.",
                    )

                score, selected = self._ranking_snapshot(s.id, top_n)
                stakes = self._split_stake(basket_stake, top_n)
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
                    for idx, (row, stake) in enumerate(zip(selected, stakes), start=1)
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
                    "top_n": top_n,
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

                if mode == "REAL" and execute_real_now is not True:
                    raise HTTPException(
                        400,
                        "REAL execution requires the explicit START/EXECUTE action."
                    )

                client = await engine._client(s.user_id, s.account_id)

                async def proposal_then_buy(leg):
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
                        raise RuntimeError(f"No proposal for rank #{leg['rank']}")

                    ask_price = float(p.get("ask_price") or leg["stake"])
                    if mode == "REAL":
                        buy_result = await client.buy_user_initiated_real(
                            proposal_id,
                            ask_price,
                            user_initiated=bool(execute_real_now),
                        )
                    else:
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

                # All selected ranks are launched concurrently.
                results = await asyncio.gather(
                    *[proposal_then_buy(leg) for leg in frozen_legs],
                    return_exceptions=True,
                )

                failures = []
                opened = []

                for index, result in enumerate(results):
                    if isinstance(result, Exception):
                        failures.append(
                            f"rank #{frozen_legs[index]['rank']}: {result}"
                        )
                        continue

                    buy = (result.get("deriv_buy") or {}).get("buy") or {}
                    contract_id = str(buy.get("contract_id") or "")
                    if not contract_id:
                        failures.append(
                            f"rank #{result['rank']}: buy returned no contract_id"
                        )
                        continue

                    leg = {
                        **result,
                        "contract_id": contract_id,
                        "buy_price": float(buy.get("buy_price") or result["ask_price"]),
                        "purchase_time": int(
                            buy.get("start_time") or buy.get("purchase_time") or 0
                        ),
                        "status": "OPEN",
                        "profit": None,
                    }
                    opened.append(leg)

                    db.add(
                        TradeLog(
                            user_id=s.user_id,
                            trading_session_id=s.id,
                            trade_no=int(result["rank"]),
                            account_mode=s.account_mode,
                            account_id=s.account_id,
                            symbol=s.symbol,
                            digit=int(result["digit"]),
                            stake=float(result["stake"]),
                            contract_id=contract_id,
                            status="OPEN",
                            buy_price=leg["buy_price"],
                            payout=float(result.get("payout") or 0),
                            raw_json=json.dumps(
                                {
                                    "kind": "TOPN_SIMULTANEOUS_LEG",
                                    "version": self.VERSION,
                                    "basket_id": basket_id,
                                    "top_n": top_n,
                                    "rank": int(result["rank"]),
                                    "source_epoch": source_epoch,
                                    "score": float(result["score"]),
                                    "score_version": score.get("version"),
                                    "triggered_at": base["triggered_at"],
                                    "proposal_received_at": result.get("proposal_received_at"),
                                    "deriv_buy": result["deriv_buy"],
                                }
                            ),
                        )
                    )

                db.commit()

                runtime = {
                    **base,
                    "legs": opened,
                    "status": (
                        "REAL_OPEN"
                        if mode == "REAL" and len(opened) == top_n
                        else (
                            "OPEN"
                            if len(opened) == top_n
                            else (
                                "REAL_PARTIAL_OPEN_RECONCILE_REQUIRED"
                                if mode == "REAL"
                                else "PARTIAL_OPEN_RECONCILE_REQUIRED"
                            )
                        )
                    ),
                    "execution_failures": failures,
                    "opened_count": len(opened),
                    "requested_count": top_n,
                    "real_execution_sent": bool(mode == "REAL" and opened),
                    "opened_at": datetime.utcnow().isoformat(),
                }
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

                if not opened:
                    raise HTTPException(
                        502,
                        f"No DEMO Top-{top_n} contracts opened. "
                        + " | ".join(failures[:top_n]),
                    )

                return runtime
            finally:
                db.close()

    async def _settle_demo_basket(self, *, sid, user_id, account_id, basket_id):
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
                await asyncio.sleep(0.25)

        runtime["status"] = (
            "SETTLED" if not unresolved else "SETTLEMENT_RECONCILE_REQUIRED"
        )
        runtime["settled_at"] = datetime.utcnow().isoformat()
        runtime["net_profit"] = round(
            sum(float(leg.get("profit") or 0) for leg in runtime.get("legs", [])),
            2,
        )
        runtime["winning_ranks"] = [
            int(leg["rank"])
            for leg in runtime.get("legs", [])
            if leg.get("status") == "WIN"
        ]

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
            key=lambda x: x.get("triggered_at") or x.get("opened_at") or "",
            reverse=True,
        )
        return {
            "version": self.VERSION,
            "min_top_n": self.MIN_TOP_N,
            "max_top_n": self.MAX_TOP_N,
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

                if raw.get("kind") not in {
                    "TOPN_SIMULTANEOUS_LEG",
                    "TOP4_BASKET_LEG",
                }:
                    continue

                basket_id = str(raw.get("basket_id") or "")
                if not basket_id:
                    continue

                basket = baskets.setdefault(
                    basket_id,
                    {
                        "basket_id": basket_id,
                        "top_n": raw.get("top_n"),
                        "source_epoch": raw.get("source_epoch"),
                        "triggered_at": raw.get("triggered_at"),
                        "version": raw.get("version"),
                        "legs": [],
                    },
                )
                basket["legs"].append(
                    {
                        "rank": raw.get("rank"),
                        "digit": log.digit,
                        "stake": float(log.stake or 0),
                        "contract_id": log.contract_id,
                        "status": log.status,
                        "profit": float(log.profit or 0),
                        "score": raw.get("score"),
                        "settled_at": (
                            log.settled_at.isoformat() if log.settled_at else None
                        ),
                    }
                )

            ordered = []
            for basket in baskets.values():
                basket["legs"] = sorted(
                    basket["legs"],
                    key=lambda x: int(x.get("rank") or 99),
                )
                basket["total_stake"] = round(
                    sum(float(x.get("stake") or 0) for x in basket["legs"]), 2
                )
                basket["net_profit"] = round(
                    sum(float(x.get("profit") or 0) for x in basket["legs"]), 2
                )
                basket["winning_ranks"] = [
                    int(x["rank"])
                    for x in basket["legs"]
                    if str(x.get("status") or "").upper() == "WIN"
                ]
                ordered.append(basket)

            ordered.sort(key=lambda x: x.get("triggered_at") or "")
            return {
                "version": self.VERSION,
                "session_id": int(sid),
                "baskets": ordered,
            }
        finally:
            db.close()


top4_baskets = Top4BasketService()
