# TradeIn event-driven recovery package

These are **complete files**, not patches. Add `app/fast_main.py`, and replace
`render.yaml` with the provided full configuration. Existing `app/engine.py`,
`app/main.py` and your browser UI remain unchanged.

The Render start command becomes `uvicorn app.fast_main:app --host 0.0.0.0 --port $PORT`.
`app.fast_main` imports the existing app, retains its OAuth and trading routes,
and adds an independent official-contract settlement reconciliation watchdog.

The watchdog polls a still-open contract's authoritative Deriv status every
1.5 seconds with bounded concurrency. If sold, it uses the existing settlement
handler, then wakes the normal purchase worker. It never guesses losses or
submits its own BUY. Stale market-stream subscriptions may be renewed.

**Important**: this does not make it possible to purchase on every physical tick.
The existing engine controls active contracts and requires its original purchase
claims; Deriv's acceptance/settlement can lag behind the market feed.

This is a targeted backend recovery addition, not a full rewrite of
`app/engine.py`. The original purchase path and optional prefetch are unchanged.

### Test in DEMO
1. Use a separate branch and a non-production deployment.
2. Keep `ALLOW_REAL_MODE=false` until validated.
3. Confirm /health and /execution/watchdog respond successfully.
4. Watch logs for DMS_WATCHDOG_SETTLED, DMS_WATCHDOG_CHECK_FAILED,
   DMS_WATCHDOG_STREAM_RESUBSCRIBED and for actual contract IDs.
5. Check that no duplicate buys or post-win purchases occur.

No live Deriv session or deployment was tested here.
