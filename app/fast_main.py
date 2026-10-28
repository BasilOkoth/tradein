"""TradeIn event-driven settlement watchdog.

This module preserves the existing secure app.top4_main FastAPI routes and
app.engine purchase ownership logic. It adds a cautious independent settlement
watchdog and a coalesced immediate worker wakeup on confirmed settlement.

It does NOT manufacture losses, buy overlapping contracts, or promise orders
on every physical market tick.
"""
import asyncio
import logging
import time
from collections import defaultdict

from .top4_main import app
from .engine import engine
from .db import SessionLocal
from .models import TradingSession

log = logging.getLogger("tradein.event_driven")
POLL_INTERVAL = 0.5
CONTRACT_CHECK_INTERVAL = 1.5
MAX_CONCURRENT_STATUS_CHECKS = 3
STREAM_STALE_SECONDS = 10.0

_last_contract_check = defaultdict(float)
_last_stream_tick = defaultdict(float)
_inflight_checks = set()
_wakeups = {}
_watchdog_task = None
_installed = False
_status_slots = asyncio.Semaphore(MAX_CONCURRENT_STATUS_CHECKS)


def wake_worker(sid):
    """Schedule at most one immediate worker step; ownership stays in engine."""
    sid = int(sid)
    previous = _wakeups.get(sid)
    if previous and not previous.done():
        return
    task = asyncio.create_task(engine._safe_step(sid))
    _wakeups[sid] = task

    def on_done(done):
        if _wakeups.get(sid) is done:
            _wakeups.pop(sid, None)
    task.add_done_callback(on_done)


def install_settlement_wakeup():
    """Respond immediately after a genuine Deriv settlement callback."""
    global _installed
    if _installed:
        return
    _installed = True
    original = engine._handle_contract_update

    async def observed_settlement(*, sid, user_id, account_id, contract_id, data):
        poc = data.get("proposal_open_contract") or {}
        await original(
            sid=sid, user_id=user_id, account_id=account_id,
            contract_id=contract_id, data=data,
        )
        if poc.get("is_sold"):
            # The engine records the official result and stops on a win.
            # Wakeup merely re-runs its existing guarded worker step.
            wake_worker(sid)

    engine._handle_contract_update = observed_settlement
    original_tick = engine._handle_strategy_tick

    async def observed_tick(*, sid, user_id, account_id, symbol, data):
        if data.get("tick"):
            _last_stream_tick[int(sid)] = time.monotonic()
        await original_tick(
            sid=sid, user_id=user_id,
            account_id=account_id, symbol=symbol, data=data,
        )
        # Tick-driven wakeups: one coalesced execution attempt for an eligible
        # idle session, NOT a DB worker request on every tick while a contract
        # is outstanding. The underlying engine still enforces ownership.
        if data.get("tick") and not engine.fast_contracts.get(int(sid)):
            wake_worker(sid)

    engine._handle_strategy_tick = observed_tick


async def reconcile_contract(sid, user_id, account_id, contract_id):
    """Only Deriv's sold-contract response may release an active contract."""
    key = (sid, str(contract_id))
    if key in _inflight_checks:
        return
    _inflight_checks.add(key)
    try:
        async with _status_slots:
            client = await engine._client(user_id, account_id)
            result = await asyncio.wait_for(
                client.contract_status(str(contract_id)),
                timeout=8.0,
            )
            poc = result.get("proposal_open_contract") or {}
            if poc.get("is_sold"):
                log.info("DMS_WATCHDOG_SETTLED sid=%s contract=%s profit=%s",
                         sid, contract_id, poc.get("profit"))
                await engine._handle_contract_update(
                    sid=sid, user_id=user_id, account_id=account_id,
                    contract_id=str(contract_id), data=result,
                )
            else:
                log.debug("DMS_WATCHDOG_STILL_OPEN sid=%s contract=%s", sid, contract_id)
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        # Do NOT assume LOSS. A failed check is not evidence of settlement.
        log.warning("DMS_WATCHDOG_CHECK_FAILED sid=%s contract=%s error=%s",
                    sid, contract_id, exc)
    finally:
        _inflight_checks.discard(key)


async def monitor_sessions():
    while True:
        db = SessionLocal()
        try:
            sessions = [
                (int(row.id), str(row.user_id), str(row.account_id),
                 str(row.symbol), str(row.open_contract_id or ""))
                for row in db.query(TradingSession).filter(
                    TradingSession.running == True
                ).all()
            ]
        except Exception:
            log.exception("DMS_WATCHDOG_DATABASE_ERROR")
            sessions = []
        finally:
            db.close()

        now = time.monotonic()
        for sid, user_id, account_id, symbol, contract_id in sessions:
            if contract_id:
                key = (sid, contract_id)
                if now - _last_contract_check[key] >= CONTRACT_CHECK_INTERVAL:
                    _last_contract_check[key] = now
                    asyncio.create_task(
                        reconcile_contract(sid, user_id, account_id, contract_id)
                    )

            # The old _ensure_tick_subscription accepts a cached subscription
            # regardless of its age. Retire stale cache entries so it can
            # request a new subscription on the already authenticated client.
            last = _last_stream_tick.get(sid, 0.0)
            subscribed = engine.tick_subscriptions.get(sid)
            if subscribed and not last:
                _last_stream_tick[sid] = now
                last = now
            if subscribed and last and now - last > STREAM_STALE_SECONDS:
                engine.tick_subscriptions.pop(sid, None)
                old_id = subscribed.get("subscription_id")
                try:
                    client = await engine._client(user_id, account_id)
                    if old_id:
                        asyncio.create_task(engine._forget_quietly(client, old_id))
                    await engine._ensure_tick_subscription(
                        sid=sid, user_id=user_id, account_id=account_id,
                        symbol=symbol, client=client,
                    )
                    _last_stream_tick[sid] = time.monotonic()
                    log.warning("DMS_WATCHDOG_STREAM_RESUBSCRIBED sid=%s", sid)
                except Exception as exc:
                    log.warning("DMS_WATCHDOG_STREAM_RECOVERY_FAILED sid=%s error=%s",
                                sid, exc)

        await asyncio.sleep(POLL_INTERVAL)


@app.on_event("startup")
async def event_driven_recovery_startup():
    global _watchdog_task
    install_settlement_wakeup()
    if _watchdog_task is None or _watchdog_task.done():
        _watchdog_task = asyncio.create_task(monitor_sessions())
        log.info("DMS_WATCHDOG_STARTED")


@app.on_event("shutdown")
async def event_driven_recovery_shutdown():
    global _watchdog_task
    if _watchdog_task and not _watchdog_task.done():
        _watchdog_task.cancel()
        try:
            await _watchdog_task
        except asyncio.CancelledError:
            pass
    _watchdog_task = None


@app.get("/execution/watchdog")
async def watchdog_status():
    """Non-sensitive operational readiness; no account balances or credentials."""
    return {
        "running": bool(_watchdog_task and not _watchdog_task.done()),
        "pending_contract_checks": len(_inflight_checks),
        "active_wakeups": sum(1 for task in _wakeups.values() if not task.done()),
        "known_streams": len(_last_stream_tick),
        "note": "Every eligible tick, not guaranteed every physical tick.",
    }
