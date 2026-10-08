import asyncio
from collections import deque
from dataclasses import asdict
from time import time
from typing import Optional

from .config import settings
from .deriv import DerivClient
from .models import Basket, RankedDigit, Leg
from .scoring import Top4DigitScorer
from .telegram import send_telegram


class Top4BasketEngine:
    def __init__(self):
        self.client = DerivClient()
        self.scorer = Top4DigitScorer()
        self.history = deque(maxlen=100)
        self.symbol = settings.default_symbol
        self.mode = "DEMO"
        self.basket_stake = settings.default_basket_stake
        self.currency = "USD"

        self.latest_tick = None
        self.latest_score = {"ready": False, "ranking": [], "top4": []}
        self.running = False
        self.subscription_id = None
        self.basket_no = 0
        self.open_basket: Optional[Basket] = None
        self.baskets = []
        self.total_profit = 0.0
        self.total_staked = 0.0
        self._lock = asyncio.Lock()

    @staticmethod
    def _last_digit(tick):
        quote = tick.get("quote")
        pip = tick.get("pip_size")
        if quote is None:
            return None
        try:
            text = f"{float(quote):.{int(pip)}f}" if pip is not None else str(quote)
        except Exception:
            text = str(quote)
        digits = [c for c in text if c.isdigit()]
        return int(digits[-1]) if digits else None

    async def start_stream(self):
        if self.subscription_id:
            return
        await self.client.connect()
        self.subscription_id = await self.client.subscribe_ticks(
            self.symbol, self._on_tick
        )

    async def _on_tick(self, data):
        tick = data.get("tick") or {}
        digit = self._last_digit(tick)
        if digit is None:
            return

        self.latest_tick = {
            "epoch": int(tick.get("epoch") or 0),
            "quote": tick.get("quote"),
            "pip_size": tick.get("pip_size"),
            "digit": digit,
        }
        self.history.append(digit)
        self.latest_score = self.scorer.rank(self.history)

        # Settle teaching/DEMO basket on the first canonical future tick.
        if self.open_basket and self.open_basket.status == "OPEN":
            if int(self.latest_tick["epoch"]) > int(self.open_basket.source_epoch):
                await self._settle_demo_basket(self.latest_tick)

    async def configure(self, mode=None, symbol=None, basket_stake=None):
        if mode:
            mode = str(mode).upper()
            if mode not in {"DEMO", "REAL"}:
                raise ValueError("mode must be DEMO or REAL")
            self.mode = mode
        if symbol:
            self.symbol = str(symbol)
        if basket_stake is not None:
            value = float(basket_stake)
            if value <= 0:
                raise ValueError("basket_stake must be > 0")
            self.basket_stake = round(value, 2)

    async def open_next_basket(self):
        await self.start_stream()
        async with self._lock:
            if self.open_basket and self.open_basket.status in {"ARMED", "OPEN"}:
                raise RuntimeError("A basket is already open")
            if not self.latest_score.get("ready"):
                raise RuntimeError("Scorer is still warming up")
            if not self.latest_tick:
                raise RuntimeError("No canonical tick available")

            top4 = self.latest_score["top4"]
            total = round(float(self.basket_stake), 2)
            per_leg = round(total / 4.0, 2)

            # Force exact cent accounting: final leg absorbs rounding remainder.
            leg_stakes = [per_leg, per_leg, per_leg, round(total - 3*per_leg, 2)]

            ranked = [
                RankedDigit(rank=i+1, digit=int(row["digit"]), score=float(row["score"]))
                for i, row in enumerate(top4)
            ]
            legs = [
                Leg(rank=r.rank, digit=r.digit, stake=leg_stakes[i])
                for i, r in enumerate(ranked)
            ]

            self.basket_no += 1
            basket = Basket(
                basket_no=self.basket_no,
                symbol=self.symbol,
                account_mode=self.mode,
                source_epoch=int(self.latest_tick["epoch"]),
                source_digit=int(self.latest_tick["digit"]),
                total_stake=total,
                stake_per_leg=per_leg,
                ranked=ranked,
                legs=legs,
            )

            if self.mode == "REAL":
                # REAL mode is deliberately read-only/signal-only in this build.
                basket.status = "REAL_SIGNAL_ONLY"
                self.baskets.append(basket)
                await send_telegram(
                    "DigitMatchStar TOP-4 REAL teaching signal\n"
                    f"Basket #{basket.basket_no}\n"
                    f"Top 4: {', '.join(str(x.digit) for x in ranked)}\n"
                    f"Total stake model: ${total:.2f}\n"
                    "No real-money orders were sent."
                )
                return basket

            # DEMO execution uses actual Deriv proposals/buys.
            for leg in basket.legs:
                prop = await self.client.proposal_digitmatch(
                    self.symbol, leg.digit, leg.stake, self.currency
                )
                leg.proposal_id = prop["id"]
                leg.payout = float(prop.get("payout") or 0)

            # Buy all 4 DEMO legs as one basket.
            for leg in basket.legs:
                buy = await self.client.buy(leg.proposal_id, leg.stake)
                leg.contract_id = str(buy.get("contract_id") or "")
                # Use actual Deriv buy price where returned.
                if buy.get("buy_price") is not None:
                    leg.stake = float(buy["buy_price"])

            basket.total_stake = round(sum(x.stake for x in basket.legs), 2)
            basket.status = "OPEN"
            self.open_basket = basket
            self.total_staked += basket.total_stake

            await send_telegram(
                "DigitMatchStar TOP-4 DEMO basket OPEN\n"
                f"Basket #{basket.basket_no} · {basket.symbol}\n"
                f"Top 4: {', '.join(str(x.digit) for x in ranked)}\n"
                f"Stake: ${basket.total_stake:.2f} "
                f"(${basket.total_stake/4:.2f} × 4)"
            )
            return basket

    async def _settle_demo_basket(self, tick):
        basket = self.open_basket
        if not basket or basket.status != "OPEN":
            return

        outcome_digit = int(tick["digit"])
        basket.outcome_digit = outcome_digit
        basket.outcome_epoch = int(tick["epoch"])

        gross = 0.0
        for leg in basket.legs:
            if leg.digit == outcome_digit:
                leg.result = "WIN"
                # For immediate teaching accounting, proposal payout is used.
                # Authoritative Deriv settlement reconciliation can be added
                # from proposal_open_contract if desired.
                gross += float(leg.payout or 0)
                leg.profit = round(float(leg.payout or 0) - leg.stake, 2)
            else:
                leg.result = "LOSS"
                leg.profit = round(-leg.stake, 2)

        basket.gross_return = round(gross, 2)
        basket.net_profit = round(gross - basket.total_stake, 2)
        basket.status = "SETTLED"
        basket.settled_at = time()

        self.total_profit = round(self.total_profit + basket.net_profit, 2)
        self.baskets.append(basket)
        self.open_basket = None

        winning = [x for x in basket.legs if x.result == "WIN"]
        winner_text = (
            f"rank #{winning[0].rank} digit {winning[0].digit}"
            if winning else "none"
        )

        await send_telegram(
            "DigitMatchStar TOP-4 DEMO basket SETTLED\n"
            f"Basket #{basket.basket_no}\n"
            f"Outcome digit: {outcome_digit}\n"
            f"Winner: {winner_text}\n"
            f"Basket net: {basket.net_profit:+.2f} USD\n"
            f"Session P/L: {self.total_profit:+.2f} USD"
        )

    def state(self):
        score = self.latest_score or {}
        return {
            "mode": self.mode,
            "symbol": self.symbol,
            "basket_stake": self.basket_stake,
            "running": self.running,
            "latest_tick": self.latest_tick,
            "score": score,
            "open_basket": self.open_basket.as_dict() if self.open_basket else None,
            "basket_count": len(self.baskets),
            "total_staked": round(self.total_staked, 2),
            "total_profit": round(self.total_profit, 2),
            "real_execution_enabled": False,
            "teaching_note": (
                "REAL is signal/read-only in this build. "
                "DEMO sends actual DEMO Digit Match orders."
            ),
        }

engine = Top4BasketEngine()
