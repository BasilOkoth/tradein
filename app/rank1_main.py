"""Strict V1 #1 target guard at TradeIn's existing BUY boundary.

Imports existing secured OAuth/routes/engine; NO raw token in browser.
The established engine retains duplicate-purchase and settlement controls.
"""
import logging
from datetime import datetime

from .execution_v2 import app
from .engine import engine

logger = logging.getLogger("tradein.rank1")
_INSTALLED = False
_MAX_RESCORES = 4


def install_rank1_buy_guard():
    global _INSTALLED
    if _INSTALLED:
        return
    original_execute_buy = engine._execute_buy

    async def strictly_ranked_buy(db, s, client, payload, *, prearmed):
        sid = int(s.id)
        # Let the existing engine enforce paused/running/REAL-confirm and claims.
        if not s.running or s.paused:
            return False
        if not isinstance(payload, dict):
            raise RuntimeError("RANK1_INVALID_PAYLOAD")
        if int(payload.get("trade_no") or -1) != int(s.current_trade or 0) + 1:
            raise RuntimeError("RANK1_TRADE_SEQUENCE_MISMATCH")

        # Do not interpret unscored history as a ranked recommendation.
        # Once a valid score is obtained, the proposal's digit must match.
        candidate = dict(payload)
        for attempt in range(_MAX_RESCORES):
            digit, snapshot = engine._choose_scored_digit(sid, lock_target=False)
            if digit is None or not snapshot.get("ready"):
                s.phase = "RANK1_WARMING"
                s.updated_at = datetime.utcnow()
                db.commit()
                logger.info("RANK1_WARMING sid=%s", sid)
                return False
            ranked = snapshot.get("ranking") or snapshot.get("ranked_digits")
            if ranked and isinstance(ranked[0], dict):
                top = ranked[0].get("digit")
                if top is not None and int(top) != int(digit):
                    logger.error("RANK1_SCORER_INCONSISTENCY sid=%s", sid)
                    return False
            digit = int(digit)
            if int(candidate.get("digit", -1)) != digit:
                # Old prearmed proposal is unsuitable. The active purchased
                # contract is never changed; only THIS future BUY is retargeted.
                logger.info("RANK1_REPRICE sid=%s trade=%s old=%s new=%s attempt=%s",
                            sid, candidate.get("trade_no"),candidate.get("digit"),digit,attempt+1)
                currency = await engine._currency_for(db, s)
                candidate = await engine._request_proposal_payload(
                    client, symbol=s.symbol, digit=digit,
                    stake=float(s.current_stake),
                    trade_no=int(s.current_trade)+1,
                    currency=currency,
                )
                # Re-check after the asynchronous request, because new ticks
                # may have changed the ranking while awaiting the proposal.
                continue

            # Take a final snapshot immediately before entering the legacy
            # purchase method. This is the latest processed canonical tick.
            latest, latest_snapshot = engine._choose_scored_digit(sid, lock_target=True)
            if latest is None or not latest_snapshot.get("ready"):
                return False
            if int(latest) != int(candidate["digit"]):
                continue

            s.candidate_digit = int(latest)
            s.phase = "RANK1_VERIFIED"
            s.updated_at = datetime.utcnow()
            db.commit()
            logger.info("RANK1_BUY_VERIFIED sid=%s trade=%s digit=%s stake=%.2f prearmed=%s",
                        sid,candidate.get("trade_no"),latest,float(candidate.get("stake",0)),prearmed)
            return await original_execute_buy(db,s,client,candidate,prearmed=prearmed)

        # Fail closed: a continuously changing top digit cannot be traded
        # with an earlier proposal. Preserve pending contract state.
        s.phase = "RANK1_REFRESH_REQUIRED"
        s.updated_at = datetime.utcnow()
        db.commit()
        logger.warning("RANK1_REFRESH_REQUIRED sid=%s trade=%s",sid,payload.get("trade_no"))
        return False

    engine._execute_buy = strictly_ranked_buy
    _INSTALLED = True


# Install at module import, before FastAPI startup starts the trading worker.
install_rank1_buy_guard()


@app.get("/execution/rank1-health")
def rank1_health():
    return {
        "rank1_guard_installed": _INSTALLED,
        "max_rescore_attempts": _MAX_RESCORES,
        "enforcement": "verify latest canonical V1 #1 before entering buy method",
        "scope": "active-contract digits unchanged; no guarantee of tick-perfect buys",
    }
