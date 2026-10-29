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
            # This is the SAME V1 ranking array that populates DIGIT RANKING 0-9.
            # Never source the purchase digit from manual Current Digit, AI,
            # shadow/fusion prediction, or an earlier locked target.
            snapshot = engine._score_all_digits(sid)
            ranking = snapshot.get("ranking") or []
            digit = int(ranking[0]["digit"]) if ranking else None
            if digit is None or not snapshot.get("ready"):
                s.phase = "RANK1_WARMING"
                s.updated_at = datetime.utcnow()
                db.commit()
                logger.info("RANK1_WARMING sid=%s", sid)
                return False
            # ranking[0] is authoritative for this application's V1 target.
            # The scorer's selected_digit should agree; fail closed otherwise.
            selected = snapshot.get("selected_digit")
            if selected is None or int(selected) != int(digit):
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
            latest_snapshot = engine._score_all_digits(sid)
            latest_rows = latest_snapshot.get("ranking") or []
            if not latest_snapshot.get("ready") or not latest_rows:
                return False
            latest = int(latest_rows[0]["digit"])
            if latest != int(candidate["digit"]):
                continue
            # Freeze evidence of the exact ranking that authorized this BUY.
            engine.locked_target_snapshots[sid] = {
                "digit": latest,
                "snapshot": latest_snapshot,
                "locked_at": datetime.utcnow().isoformat(),
            }

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
        "enforcement": "purchase digit == DIGIT RANKING 0-9 ranking[0].digit from canonical V1 scorer",
        "scope": "active-contract digits unchanged; no guarantee of tick-perfect buys",
    }
