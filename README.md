# TradeIn strict V1 #1 at each BUY

Complete NEW integration module `app/rank1_main.py` and full `render.yaml`.
This module imports your V2.3 `app.execution_v2:app` and installs a guard
on the existing engine. It does not replace `app/engine.py`.

**Important:** This package **includes the V2.3 `app/execution_v2.py` controller** that
contains the previous contract ID type fix. The new startup imports it
so its settlement reconciliation and tick permits are retained.

The guard picks the latest canonical V1 rank #1 immediately before handing
the order to the existing buy routine. It invalidates stale prearmed proposals,
requests fresh proposals matching the ranked digit, and rechecks ranking after
network waits. If the rank keeps changing, it declines that purchase; it does
not change a purchased contract.

**Limitations:** There is still an asynchronous window inside the original
`_execute_buy` between the guard and the Deriv `buy()` call, so tick-perfect
synchronization cannot be guaranteed without editing the original engine's
actual `buy()` call. The guard enforces target consistency at its entry
boundary, not atomically with a remote Deriv purchase.

**Do not switch this on for live funds without DEMO validation.**

## Install
1. Upload both `app/rank1_main.py` and `app/execution_v2.py`, and replace root `render.yaml`.
2. Deploy to an isolated DEMO instance. Ensure the Render start command
   matches `uvicorn app.rank1_main:app --host 0.0.0.0 --port $PORT`.
3. Visit `/execution/rank1-health` and verify `rank1_guard_installed: true`.
4. Review `RANK1_REPRICE` and `RANK1_BUY_VERIFIED` in Render logs; correlate
   actual target digits against canonical rankings at purchase time.
5. Confirm STOP, settlements and no duplicate buys during 20-trade tests.
