"""TradeIn v2.3 execution controller for the existing tradein repository.

Deploy as uvicorn app.execution_v2:app instead of app.top4_main:app.
Existing app.engine is retained for OAuth, order validation and DB claims.
Only official sold-contract responses advance the progression.
"""
import asyncio
import logging
import time
from collections import defaultdict
from datetime import datetime

from .top4_main import app
from .engine import engine
from .deriv_ws import DerivWS
from .db import SessionLocal
from .models import TradingSession

log = logging.getLogger("tradein.execution_v2")
CHECK_EVERY = 1.0
STATUS_TIMEOUT = 6.0
MAX_CHECKS = 4
STREAM_TIMEOUT = 12.0

active_checks = set()
check_at = defaultdict(float)
stream_at = {}
contract_diagnostics = {}
subscribed_at = {}
recovery_tasks = {}
last_tick_epoch = {}
tick_permits = set()
watchdog_task = None
installed = False
contract_id_patch_installed = False
check_sem = asyncio.Semaphore(MAX_CHECKS)



def install_contract_id_wire_fix():
    """Send numeric Deriv contract IDs in BOTH status and subscription requests.

    Previous DerivWS.contract_status and subscribe_contract passed str(id),
    which Deriv rejects as InputValidationFailed: contract_id.
    Keep local DB/TradeLog IDs as strings; only alter the API payload type.
    """
    global contract_id_patch_installed
    if contract_id_patch_installed:
        return
    original_request = DerivWS.request

    async def corrected_request(self, payload, *args, **kwargs):
        if isinstance(payload, dict) and payload.get("proposal_open_contract") and "contract_id" in payload:
            wire = dict(payload)
            try:
                cid = int(str(wire["contract_id"]).strip())
            except (ValueError, TypeError) as exc:
                raise RuntimeError("Invalid Deriv contract ID; expected positive integer") from exc
            if cid <= 0:
                raise RuntimeError("Invalid Deriv contract ID; expected positive integer")
            wire["contract_id"] = cid
            payload = wire
        return await original_request(self, payload, *args, **kwargs)

    DerivWS.request = corrected_request
    contract_id_patch_installed = True
    log.info("DMS_V23_CONTRACT_ID_WIRE_FIX_ENABLED")


def notify(sid):
    """Coalesce worker wakeups without bypassing the original DB claim."""
    sid = int(sid)
    previous = recovery_tasks.get(sid)
    if previous and not previous.done():
        return
    task = asyncio.create_task(engine._safe_step(sid))
    recovery_tasks[sid] = task
    task.add_done_callback(
        lambda finished: recovery_tasks.pop(sid, None)
        if recovery_tasks.get(sid) is finished else None
    )


def install():
    global installed
    if installed:
        return
    installed = True
    original_settlement = engine._handle_contract_update
    original_step = engine.step

    async def tick_permitted_step(sid):
        # A periodic 20ms scan cannot authorize a purchase; only the
        # most recent subscribed tick issues a one-shot permit.
        sid = int(sid)
        if sid not in tick_permits:
            return
        tick_permits.discard(sid)
        await original_step(sid)

    engine.step = tick_permitted_step

    async def tick_handler(*, sid, user_id, account_id, symbol, data):
        """Rank ticks only. Never infer a paid WIN/LOSS from public ticks."""
        tick = data.get("tick") or {}
        if not tick:
            return
        sid = int(sid)
        epoch = int(tick.get("epoch") or 0)
        if epoch and epoch <= last_tick_epoch.get(sid, 0):
            return
        if epoch:
            last_tick_epoch[sid] = epoch
            engine.latest_tick_epoch[sid] = epoch
        engine.latest_ticks[sid] = dict(tick)
        stream_at[sid] = time.monotonic()
        digit = engine._tick_last_digit(tick)
        if digit is not None:
            engine._digit_history(sid).append(int(digit))
            snapshot = engine._score_all_digits(sid)
            if snapshot.get("ready"):
                engine._set_live_next_target(sid, snapshot.get("selected_digit"))
        # If eligible and idle, wake the same original guarded worker.
        # Actual purchase waits for an official previous settlement.
        db = SessionLocal()
        try:
            session = db.get(TradingSession, sid)
            if session and session.running and not session.paused and not session.open_contract_id:
                if sid not in engine.fast_contracts:
                    tick_permits.add(sid)
                    notify(sid)
        finally:
            db.close()

    async def settled_handler(*, sid, user_id, account_id, contract_id, data):
        poc = data.get("proposal_open_contract") or {}
        if not poc.get("is_sold"):
            return
        sid = int(sid)
        cid = str(contract_id)
        # Existing handler owns Deriv P/L ledger, settled TradeLog and
        # reconciliation. It is idempotent against already SETTLED logs.
        await original_settlement(
            sid=sid, user_id=user_id, account_id=account_id,
            contract_id=cid, data=data,
        )
        db = SessionLocal()
        try:
            session = db.get(TradingSession, sid)
            if session is None:
                return
            # A still-open field means another contract owns the execution slot.
            if session.open_contract_id:
                return
            owner = engine.fast_contracts.get(sid)
            if owner and str(owner.get("contract_id")) != cid:
                return
            if not owner:
                return
            if owner.get("v2_settled"):
                return
            owner["v2_settled"] = True
            profit = float(poc.get("profit") or 0.0)
            engine.fast_contracts.pop(sid, None)
            engine.prefetched_recovery.pop(sid, None)
            pending = engine.prefetch_tasks.pop(sid, None)
            if pending and not pending.done():
                pending.cancel()
            if profit > 0:
                session.running = False
                session.paused = False
                session.phase = "DERIV_WIN_STOPPED"
                log.info("DMS_V2_WIN_STOP sid=%s contract=%s", sid, cid)
            elif int(session.current_trade or 0) >= int(session.max_trades or 0):
                session.running = False
                session.paused = False
                session.phase = "MAX_TRADES_REACHED"
            elif session.running and not session.paused:
                session.current_stake = round(
                    float(session.current_stake) * float(session.multiplier), 2
                )
                selected = engine.live_next_target.get(sid)
                if selected is not None:
                    session.candidate_digit = int(selected)
                session.phase = "SETTLED_LOSS_READY_NEXT_TICK"
                log.info("DMS_V2_LOSS_READY sid=%s next=%s stake=%.2f",
                         sid, int(session.current_trade or 0) + 1,
                         float(session.current_stake))
            session.updated_at = datetime.utcnow()
            db.commit()
        finally:
            db.close()
        # No BUY here. The next live tick wakes the guarded worker.

    engine._handle_strategy_tick = tick_handler
    engine._handle_contract_update = settled_handler
    # The original 20ms scan is preserved for startup/bootstrap but the
    # settlement gate blocks overlapping contracts. Do not monkeypatch BUY.
    log.info("DMS_V2_INSTALLED settlement-authoritative progression")


async def verify(sid, user_id, account_id, contract_id):
    key = (sid, str(contract_id))
    if key in active_checks:
        return
    active_checks.add(key)
    try:
        async with check_sem:
            client = await engine._client(user_id, account_id)
            result = await asyncio.wait_for(
                client.contract_status(str(contract_id)), STATUS_TIMEOUT
            )
            poc = result.get("proposal_open_contract") or {}
            contract_diagnostics[key] = {
                "state": "SOLD" if poc.get("is_sold") else "OPEN",
                "last_check_monotonic": time.monotonic(),
                "error": None,
            }
            if poc.get("is_sold"):
                await engine._handle_contract_update(
                    sid=sid, user_id=user_id, account_id=account_id,
                    contract_id=str(contract_id), data=result,
                )
    except asyncio.CancelledError:
        raise
    except Exception as error:
        contract_diagnostics[key] = {
            "state": "CHECK_FAILED",
            "last_check_monotonic": time.monotonic(),
            "error": f"{type(error).__name__}: {error}"[:220],
        }
        log.warning("DMS_V2_CONTRACT_QUERY_FAILED sid=%s cid=%s error=%s",
                    sid, contract_id, error)
    finally:
        active_checks.discard(key)


async def monitor():
    while True:
        db = SessionLocal()
        try:
            # An unresolved contract must remain under observation EVEN if
            # a prior error or STOP has set running=False.
            rows = [
                (int(s.id), str(s.user_id), str(s.account_id), str(s.symbol),
                 str(s.open_contract_id or ""), bool(s.running))
                for s in db.query(TradingSession).all()
                if s.running or s.open_contract_id
            ]
        except Exception:
            log.exception("DMS_V2_MONITOR_DB_FAILED")
            rows = []
        finally:
            db.close()
        now = time.monotonic()
        for sid, uid, aid, symbol, contract, running in rows:
            if contract and now - check_at[(sid, contract)] >= CHECK_EVERY:
                check_at[(sid, contract)] = now
                asyncio.create_task(verify(sid, uid, aid, contract))
            # Do not restart trading or streams on a stopped session just to
            # reconcile an already-purchased contract.
            if not running:
                continue
            # A dead authenticated WS does not make cached subscription ids valid.
            cached_client = engine.clients.get((uid, aid))
            if cached_client is not None and not cached_client.is_open():
                engine.tick_subscriptions.pop(sid, None)
                try:
                    await engine._drop_client(uid, aid)
                except Exception as error:
                    log.warning("DMS_V2_SOCKET_RESET_FAILED sid=%s error=%s", sid, error)
            # Subscriptions must start even though the polling worker no
            # longer receives purchase permits by itself.
            if sid not in engine.tick_subscriptions:
                try:
                    client = await engine._client(uid, aid)
                    await engine._ensure_tick_subscription(
                        sid=sid, user_id=uid, account_id=aid,
                        symbol=symbol, client=client,
                    )
                    subscribed_at[sid] = time.monotonic()
                except Exception as error:
                    log.warning("DMS_V2_SUBSCRIBE_FAILED sid=%s error=%s", sid, error)
            sub = engine.tick_subscriptions.get(sid)
            if sub and now - stream_at.get(sid, subscribed_at.get(sid, now)) > STREAM_TIMEOUT:
                # Remove stale handle and use the engine's own subscription API.
                engine.tick_subscriptions.pop(sid, None)
                try:
                    client = await engine._client(uid, aid)
                    old = sub.get("subscription_id")
                    if old:
                        asyncio.create_task(engine._forget_quietly(client, old))
                    await engine._ensure_tick_subscription(
                        sid=sid, user_id=uid, account_id=aid,
                        symbol=symbol, client=client,
                    )
                    subscribed_at[sid] = time.monotonic()
                    log.warning("DMS_V2_STREAM_REJOIN sid=%s", sid)
                except Exception as error:
                    log.warning("DMS_V2_STREAM_REJOIN_FAILED sid=%s err=%s", sid, error)
        await asyncio.sleep(0.5)


@app.on_event("startup")
async def v2_start():
    global watchdog_task
    install_contract_id_wire_fix()
    install()
    if not watchdog_task or watchdog_task.done():
        watchdog_task = asyncio.create_task(monitor())


@app.on_event("shutdown")
async def v2_stop():
    global watchdog_task
    if watchdog_task:
        watchdog_task.cancel()
        try:
            await watchdog_task
        except asyncio.CancelledError:
            pass
        watchdog_task = None


@app.get("/execution-v2/health")
async def v2_health():
    now = time.monotonic()
    return {
        "controller": "settlement-authoritative-v2.3",
        "watchdog_running": bool(watchdog_task and not watchdog_task.done()),
        "contract_id_wire_type": "integer",
        "contract_id_fix_installed": contract_id_patch_installed,
        "outstanding_checks": len(active_checks),
        "stream_subscriptions": len(engine.tick_subscriptions),
        "last_tick_age_seconds": {
            str(k): round(now-v, 1) for k, v in stream_at.items()
        },
        "contract_checks": {
            f"session-{key[0]}": {
                "state": val.get("state"),
                "last_check_age_seconds": round(now-val["last_check_monotonic"],1),
                "error": val.get("error"),
            }
            for key, val in contract_diagnostics.items()
        },
    }
