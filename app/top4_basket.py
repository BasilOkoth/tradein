import asyncio
import json
import math
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
    """Configurable simultaneous Top-N DEMO/REAL execution (1..7)."""

    VERSION = "TOP4_RECOVERY_V16_STABLE_DUAL_MODE"
    MIN_TOP_N = 1
    MAX_TOP_N = 7
    MIN_LEG_STAKE = 0.35

    # Six-round Top-4 loss-recovery policy.
    RECOVERY_TOP_N = 4
    RECOVERY_MAX_ROUNDS = 6
    RECOVERY_TARGET_PROFIT = 1.00
    DIGITMATCH_TOTAL_RETURN = 8.93

    def __init__(self):
        self.live = {}
        self.cycles = {}
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

    @classmethod
    def _split_stake(cls, total, count):
        total = round(float(total), 2)
        count = int(count)

        minimum_total = round(cls.MIN_LEG_STAKE * count, 2)

        if total < minimum_total:
            raise HTTPException(
                400,
                f"Top-{count} requires a basket stake of at least "
                f"{minimum_total:.2f} ({cls.MIN_LEG_STAKE:.2f} per contract).",
            )

        # Split cents deterministically so every leg remains at or above
        # Deriv's minimum stake and the legs add up to the requested basket.
        total_cents = int(round(total * 100))
        min_leg_cents = int(round(cls.MIN_LEG_STAKE * 100))
        base_cents, remainder = divmod(total_cents, count)

        stakes_cents = [
            base_cents + (1 if i < remainder else 0)
            for i in range(count)
        ]

        if min(stakes_cents) < min_leg_cents:
            raise HTTPException(
                400,
                f"Top-{count} requires at least {cls.MIN_LEG_STAKE:.2f} "
                "per contract.",
            )

        return [round(cents / 100.0, 2) for cents in stakes_cents]

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


    def _session_baskets(self, db, sid):
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

            if raw.get("kind") not in {"TOPN_SIMULTANEOUS_LEG", "TOP4_BASKET_LEG"}:
                continue

            basket_id = str(raw.get("basket_id") or "")
            if not basket_id:
                continue

            basket = baskets.setdefault(
                basket_id,
                {
                    "basket_id": basket_id,
                    "top_n": int(raw.get("top_n") or 0),
                    "triggered_at": raw.get("triggered_at"),
                    "legs": [],
                },
            )

            basket["legs"].append(
                {
                    "rank": raw.get("rank"),
                    "stake": float(log.stake or 0),
                    "status": str(log.status or "").upper(),
                    "profit": float(log.profit or 0),
                }
            )

        ordered = []
        for basket in baskets.values():
            basket["total_stake"] = round(
                sum(float(x.get("stake") or 0) for x in basket["legs"]),
                2,
            )
            basket["net_profit"] = round(
                sum(float(x.get("profit") or 0) for x in basket["legs"]),
                2,
            )
            basket["settled"] = bool(basket["legs"]) and all(
                str(x.get("status") or "").upper() in {"WIN", "LOSS"}
                for x in basket["legs"]
            )
            ordered.append(basket)

        ordered.sort(key=lambda x: x.get("triggered_at") or "")
        return ordered

    def recovery_status(self, *, user_id, sid):
        db = SessionLocal()
        try:
            self._owned_session(db, user_id, sid)
            baskets = [
                basket
                for basket in self._session_baskets(db, sid)
                if int(basket.get("top_n") or 0) == self.RECOVERY_TOP_N
                and basket.get("settled")
            ]
        finally:
            db.close()

        # Split historical Top-4 baskets into complete trades. A Top-4 trade
        # ends as soon as its cumulative P/L becomes positive, or after 6 rounds.
        current_rounds = []
        current_trade_pnl = 0.0

        for basket in baskets:
            pnl = float(basket.get("net_profit") or 0)
            current_rounds.append(basket)
            current_trade_pnl = round(current_trade_pnl + pnl, 2)

            if (
                current_trade_pnl > 0
                or len(current_rounds) >= self.RECOVERY_MAX_ROUNDS
            ):
                current_rounds = []
                current_trade_pnl = 0.0

        recovery_loss = max(0.0, -current_trade_pnl)
        next_round = len(current_rounds) + 1

        denominator = (
            self.DIGITMATCH_TOTAL_RETURN
            - self.RECOVERY_TOP_N
        )

        required_leg = max(
            self.MIN_LEG_STAKE,
            (
                recovery_loss
                + self.RECOVERY_TARGET_PROFIT
            ) / denominator,
        )
        required_leg = math.ceil(required_leg * 100.0) / 100.0

        next_basket = round(
            required_leg * self.RECOVERY_TOP_N,
            2,
        )

        return {
            "enabled": True,
            "top_n": self.RECOVERY_TOP_N,
            "max_rounds": self.RECOVERY_MAX_ROUNDS,
            "target_profit": self.RECOVERY_TARGET_PROFIT,
            "return_multiplier": self.DIGITMATCH_TOTAL_RETURN,
            "waiting_for_settlement": False,
            "stopped_after_max_losses": False,
            "round": next_round,
            "completed_rounds": len(current_rounds),
            "trade_pnl": round(current_trade_pnl, 2),
            "accumulated_loss": round(recovery_loss, 2),
            "next_leg_stake": round(required_leg, 2),
            "next_basket_stake": next_basket,
        }

    async def arm(self, *, user_id, sid):
        """
        Attach the authenticated server canonical tick stream for Top-N scoring.

        Works for both DEMO and REAL sessions. This only warms DigitScore:
        it never sends a proposal or BUY.
        """
        async with self._locks[int(sid)]:
            db = SessionLocal()

            try:
                s = self._owned_session(
                    db,
                    user_id,
                    sid,
                )

                mode = str(
                    s.account_mode
                    or ""
                ).upper()

                if mode not in {"DEMO", "REAL"}:
                    raise HTTPException(
                        409,
                        "Top-N warming requires a DEMO or REAL Deriv account.",
                    )

                client = await engine._client(
                    s.user_id,
                    s.account_id,
                )

                await engine._ensure_tick_subscription(
                    sid=s.id,
                    user_id=s.user_id,
                    account_id=s.account_id,
                    symbol=s.symbol,
                    client=client,
                )

                # IMPORTANT: warming Top-N must never stop or rewrite the
                # single-target worker. Both features share the same canonical
                # tick stream, but their execution state is independent.
                score = engine._score_all_digits(
                    s.id
                )

                return {
                    "ok": True,
                    "session_id": s.id,
                    "account_id": s.account_id,
                    "account_mode": mode,
                    "symbol": s.symbol,
                    "phase": s.phase,
                    "digit_score": score,
                    "topn_ready": bool(
                        score.get("ready")
                    ),
                    "history_count": int(
                        score.get(
                            "history_count"
                        )
                        or 0
                    ),
                    "minimum_history": int(
                        score.get(
                            "minimum_history"
                        )
                        or 10
                    ),
                    "message": (
                        f"{mode} Top-N server feed armed. "
                        "No trade has been sent."
                    ),
                }

            finally:
                db.close()

    async def arm_real(self, *, user_id, sid):
        """Backward-compatible REAL-only Top-N warm endpoint."""
        db = SessionLocal()
        try:
            s = self._owned_session(db, user_id, sid)
            if str(s.account_mode or "").upper() != "REAL":
                raise HTTPException(
                    409,
                    "This session is not a REAL account.",
                )
        finally:
            db.close()

        return await self.arm(
            user_id=user_id,
            sid=sid,
        )

    async def execute_now(self, *, user_id, sid, basket_stake, top_n=7, execute_real_now=False, force_basket_stake=False):
        top_n = self._validate_top_n(top_n)

        async with self._locks[int(sid)]:
            db = SessionLocal()
            try:
                s = self._owned_session(db, user_id, sid)

                if s.running:
                    raise HTTPException(
                        409,
                        "Single-target DigitMatch is currently running. "
                        "Stop it before starting a Top-4 trade.",
                    )

                active_baskets = [
                    value
                    for value in self.live.values()
                    if int(value.get("session_id") or 0) == int(s.id)
                    and str(value.get("status") or "").upper() not in {
                        "SETTLED",
                        "SETTLEMENT_RECONCILE_REQUIRED",
                    }
                ]

                if active_baskets:
                    raise HTTPException(
                        409,
                        "The previous simultaneous basket is still settling. "
                        "Wait for settlement before opening the next recovery round.",
                    )

                if s.open_contract_id:
                    raise HTTPException(
                        409,
                        "The single-target session has an open contract. Let it settle first.",
                    )

                score, selected = self._ranking_snapshot(s.id, top_n)

                recovery = None
                if top_n == self.RECOVERY_TOP_N and not force_basket_stake:
                    recovery = self.recovery_status(
                        user_id=user_id,
                        sid=s.id,
                    )

                    # Manual Top-4 execution uses the server-calculated
                    # recovery amount. The automatic DEMO cycle supplies its
                    # own authoritative per-round basket stake.
                    basket_stake = float(
                        recovery["next_basket_stake"]
                    )

                # Validate basket sizing before any proposal is requested.
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
                    "recovery": recovery,
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
                        buy_result = await client.request(
                            {
                                "buy": proposal_id,
                                "price": float(ask_price),
                            }
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
                        f"No {mode or 'DEMO'} Top-{top_n} contracts opened. "
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

        # Top-N legs are one-tick contracts. Reconcile all outstanding legs in
        # parallel instead of issuing four sequential status requests.
        deadline = asyncio.get_running_loop().time() + 12.0

        async def fetch_one(contract_id):
            try:
                data = await client.contract_status(contract_id)
                return contract_id, data, None
            except Exception as exc:
                return contract_id, None, exc

        while unresolved and asyncio.get_running_loop().time() < deadline:
            contract_ids = list(unresolved.keys())

            results = await asyncio.gather(
                *[fetch_one(contract_id) for contract_id in contract_ids]
            )

            settled_any = False

            for contract_id, data, error in results:
                if error is not None or not data:
                    continue

                contract = data.get("proposal_open_contract") or {}
                if not contract.get("is_sold"):
                    continue

                leg = unresolved.get(contract_id)
                if not leg:
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
                settled_any = True

            if unresolved:
                # 100 ms keeps the UI responsive without serially blocking each
                # contract for another quarter second.
                await asyncio.sleep(0.10 if settled_any else 0.15)

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

        runtime["losing_ranks"] = [
            int(leg["rank"])
            for leg in runtime.get("legs", [])
            if leg.get("status") == "LOSS"
        ]


    async def _wait_for_basket_terminal(self, basket_id, timeout=15.0):
        deadline = asyncio.get_running_loop().time() + float(timeout)
        while asyncio.get_running_loop().time() < deadline:
            runtime = self.live.get(str(basket_id))
            if runtime and str(runtime.get("status") or "").upper() in {
                "SETTLED",
                "SETTLEMENT_RECONCILE_REQUIRED",
            }:
                return runtime
            await asyncio.sleep(0.05)
        return self.live.get(str(basket_id))

    async def execute_demo_recovery_cycle(self, *, user_id, sid):
        """
        Execute ONE DEMO Top-4 trade, with at most six recovery rounds.

        Trade P/L starts at 0.00 for each new call and accumulates only the
        rounds of this trade. Live progress is exposed via status().
        """
        db = SessionLocal()
        try:
            s = self._owned_session(db, user_id, sid)
            mode = str(s.account_mode or "").upper()
            single_running = bool(s.running)
        finally:
            db.close()

        if mode != "DEMO":
            raise HTTPException(
                409,
                "Automatic six-round recovery chaining is available in DEMO only.",
            )

        if single_running:
            raise HTTPException(
                409,
                "Single-target DigitMatch is currently running. "
                "Stop it before starting a Top-4 trade.",
            )

        cycle = {
            "status": "RUNNING",
            "round": 0,
            "max_rounds": self.RECOVERY_MAX_ROUNDS,
            "round_status": "STARTING",
            "trade_pnl": 0.0,
            "recovery_loss": 0.0,
            "stake_per_digit": self.MIN_LEG_STAKE,
            "basket_stake": round(
                self.MIN_LEG_STAKE * self.RECOVERY_TOP_N,
                2,
            ),
            "rounds": [],
            "latest": None,
            "started_at": datetime.utcnow().isoformat(),
        }
        self.cycles[int(sid)] = cycle

        for round_no in range(1, self.RECOVERY_MAX_ROUNDS + 1):
            recovery_loss = max(
                0.0,
                -float(cycle.get("trade_pnl") or 0),
            )

            denominator = (
                self.DIGITMATCH_TOTAL_RETURN
                - self.RECOVERY_TOP_N
            )

            leg_stake = max(
                self.MIN_LEG_STAKE,
                (
                    recovery_loss
                    + self.RECOVERY_TARGET_PROFIT
                ) / denominator,
            )
            leg_stake = math.ceil(leg_stake * 100.0) / 100.0
            basket_stake = round(
                leg_stake * self.RECOVERY_TOP_N,
                2,
            )

            cycle.update({
                "round": round_no,
                "round_status": "SUBMITTING",
                "recovery_loss": round(recovery_loss, 2),
                "stake_per_digit": round(leg_stake, 2),
                "basket_stake": basket_stake,
                "latest": None,
            })

            runtime = await self.execute_now(
                user_id=user_id,
                sid=sid,
                basket_stake=basket_stake,
                top_n=self.RECOVERY_TOP_N,
                execute_real_now=False,
                force_basket_stake=True,
            )

            basket_id = str(runtime.get("basket_id") or "")
            cycle["round_status"] = "OPEN"
            cycle["latest"] = runtime

            settled = await self._wait_for_basket_terminal(
                basket_id,
                timeout=15.0,
            )

            if not settled:
                cycle["status"] = "ERROR"
                cycle["round_status"] = "SETTLEMENT_TIMEOUT"
                cycle["finished_at"] = datetime.utcnow().isoformat()
                raise HTTPException(
                    504,
                    f"Top-4 round {round_no} did not settle in time.",
                )

            round_pnl = float(settled.get("net_profit") or 0)

            round_result = {
                "round": round_no,
                "basket_id": basket_id,
                "status": settled.get("status"),
                "basket_stake": float(
                    settled.get("basket_stake") or 0
                ),
                "net_profit": round_pnl,
                "winning_ranks": list(
                    settled.get("winning_ranks") or []
                ),
                "legs": list(
                    settled.get("legs") or []
                ),
            }

            cycle["rounds"].append(round_result)
            cycle["trade_pnl"] = round(
                float(cycle.get("trade_pnl") or 0)
                + round_pnl,
                2,
            )
            cycle["recovery_loss"] = round(
                max(
                    0.0,
                    -float(cycle["trade_pnl"]),
                ),
                2,
            )
            cycle["latest"] = settled
            cycle["round_status"] = (
                "ROUND_WIN"
                if round_pnl > 0
                else "ROUND_LOSS"
            )

            # The Top-4 TRADE is only a WIN when total trade P/L is positive.
            if float(cycle["trade_pnl"]) > 0:
                cycle["status"] = "WIN"
                cycle["winning_round"] = round_no
                cycle["finished_at"] = datetime.utcnow().isoformat()

                return {
                    "ok": True,
                    "cycle_status": "WIN",
                    "winning_round": round_no,
                    "rounds_completed": len(cycle["rounds"]),
                    "trade_pnl": cycle["trade_pnl"],
                    "rounds": list(cycle["rounds"]),
                    "latest": settled,
                }

            if round_no < self.RECOVERY_MAX_ROUNDS:
                cycle["round_status"] = "NEXT_ROUND"
                await asyncio.sleep(0.05)

        cycle["status"] = "LOSS"
        cycle["winning_round"] = None
        cycle["finished_at"] = datetime.utcnow().isoformat()

        return {
            "ok": True,
            "cycle_status": "LOSS",
            "winning_round": None,
            "rounds_completed": len(cycle["rounds"]),
            "trade_pnl": cycle["trade_pnl"],
            "rounds": list(cycle["rounds"]),
            "latest": cycle.get("latest"),
        }


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
            "min_leg_stake": self.MIN_LEG_STAKE,
            "latest": live[0] if live else None,
            "cycle": self.cycles.get(int(sid)),
            "recovery": self.recovery_status(
                user_id=user_id,
                sid=sid,
            ),
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
