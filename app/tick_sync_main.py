"""TradeIn v2.5 — tick-owned execution coordinator.

Requires the existing (already committed) app.execution_v2 and app.rank1_main.
It replaces the v2.4 wakeup scheduling WITHOUT replacing secure OAuth,
purchase claims, V1 ranking, reconciliation or official Deriv settlement.
"""
import asyncio
import contextvars
import logging
import time
from collections import defaultdict

from .rank1_main import app
from .engine import engine
from . import execution_v2

log = logging.getLogger("tradein.tick_sync")
_owner = contextvars.ContextVar("dms_tick_owner", default=None)
_original_step = engine.step  # captured before v2.4's startup monkeypatch
_generations = defaultdict(int)
_executed = defaultdict(int)
_tasks = {}
_dispatched_at = {}
_installed = False


async def _tick_owned_step(sid):
    """Scanner calls are harmless; only the tick-owned task can buy."""
    sid = int(sid)
    if _owner.get() != sid:
        return
    return await _original_step(sid)


def _notify_from_tick(sid):
    """Coalesce ticks; never launch parallel steps for the same session."""
    sid = int(sid)
    _generations[sid] += 1
    previous = _tasks.get(sid)
    if previous and not previous.done():
        return

    async def run():
        while _executed[sid] < _generations[sid]:
            generation = _generations[sid]
            _executed[sid] = generation
            token = _owner.set(sid)
            try:
                _dispatched_at[sid] = time.monotonic()
                await engine._safe_step(sid)
            finally:
                _owner.reset(token)

    task = asyncio.create_task(run())
    _tasks[sid] = task

    def finished(done):
        if _tasks.get(sid) is done:
            _tasks.pop(sid, None)
        try:
            done.result()
        except asyncio.CancelledError:
            return
        except Exception:
            log.exception("DMS_TICK_WORKER_EXCEPTION sid=%s", sid)
        # A tick received just as the task completed must not disappear.
        if _executed[sid] < _generations[sid]:
            _schedule_existing_generation(sid)

    task.add_done_callback(finished)


def _schedule_existing_generation(sid):
    """Start a worker for an already recorded tick; don't fabricate a tick."""
    task = _tasks.get(sid)
    if task and not task.done():
        return

    async def run():
        while _executed[sid] < _generations[sid]:
            generation = _generations[sid]
            _executed[sid] = generation
            token = _owner.set(sid)
            try:
                _dispatched_at[sid] = time.monotonic()
                await engine._safe_step(sid)
            finally:
                _owner.reset(token)

    task = asyncio.create_task(run())
    _tasks[sid] = task

    def finished(done):
        if _tasks.get(sid) is done:
            _tasks.pop(sid, None)
        try:
            done.result()
        except asyncio.CancelledError:
            return
        except Exception:
            log.exception("DMS_TICK_WORKER_EXCEPTION sid=%s", sid)
        if _executed[sid] < _generations[sid]:
            _schedule_existing_generation(sid)

    task.add_done_callback(finished)


@app.on_event("startup")
async def install_tick_sync():
    """Runs after existing v2.4 startup hooks registered by imports."""
    global _installed
    if _installed:
        return
    if not execution_v2.installed:
        raise RuntimeError(
            "V2.4 controller failed to install before v2.5 tick synchronization"
        )
    # The old v2.4 tick handler looks up notify by global module name,
    # therefore this replaces its wakeup without changing its ranking,
    # local-match or settlement logic.
    execution_v2.notify = _notify_from_tick
    execution_v2.tick_permits.clear()
    engine.step = _tick_owned_step
    _installed = True
    log.info("DMS_TICK_SYNC_V25_READY: callback-owned buys; original claims retained")


@app.get("/execution/tick-sync-health")
async def tick_sync_health():
    now = time.monotonic()
    return {
        "controller": "tick-synchronized-v2.5",
        "installed": _installed,
        "ranking_guard_installed": True,
        "execution_path": "tick callback -> coalesced worker -> guarded BUY",
        "periodic_scanner_can_buy": False,
        "tick_notifications": {str(k): v for k, v in _generations.items()},
        "processed_notifications": {str(k): v for k, v in _executed.items()},
        "workers_in_flight": sum(not task.done() for task in _tasks.values()),
        "last_dispatch_age_seconds": {
            str(k): round(now - v, 2) for k, v in _dispatched_at.items()
        },
        "settlement_gate": "v2.4 authoritative Deriv settlement unchanged",
    }
