TRADEIN FAST TOP-4 PIPELINE V23

Performance changes:
- Top-4 no longer uses the normal 900ms proposal lane four times.
- A dedicated Top-N proposal batch lane uses controlled 450ms spacing.
- If Deriv returns RateLimit, the batch falls back to conservative 900ms spacing.
- Exactly one proposal request is made per intended leg; no duplicate fallback storm.
- All 4 proposals are prepared BEFORE buys.
- Once all four valid proposals exist, all four BUY requests launch concurrently.
- This makes the actual contract openings much more synchronized.
- A proposal-preparation failure aborts before knowingly opening a partial basket.
- V22 REAL guided confirmation and V21 stable UI are retained.

Files to replace:
  /bot.html
  /app/top4_basket.py
  /app/top4_main.py
  /app/deriv_ws.py

Expected proposal preparation cadence for Top-4:
  old: approx 0.0s, 0.9s, 1.8s, 2.7s
  V23: approx 0.0s, 0.45s, 0.90s, 1.35s
before the concurrent BUY launch, absent rate limiting/network delay.
