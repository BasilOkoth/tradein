TRADEIN TOP-4 SELF-REFRESH V25

Fixes Start Top-4 remaining disabled until manual browser refresh.

Root cause:
- /top4/arm could return a fresh READY DigitScore.
- The frontend did not copy that returned digit_score into SERVER_EXECUTION.state.
- Start Top-4 kept reading stale state until another full refresh/state cycle.

V25:
- arm response is applied immediately to live browser state.
- readiness is rendered immediately after arm.
- server polling warms first, then renders button from fresh state.
- capital/readiness caches are invalidated after a fresh arm.
- a lightweight 500ms UI-only refresh re-evaluates the button without extra network calls.
- Start Top-4 unlocks as soon as:
    score.ready == true
    ranking has >= 4 digits
    capital is sufficient
    no Top-4 execution is currently in flight

No manual page refresh should be required.

Replace:
  /bot.html
  /app/top4_basket.py
  /app/top4_main.py
  /app/deriv_ws.py
