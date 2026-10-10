TRADEIN TOP-N SERVER WARM V7

Fixes DEMO Top-N stuck at Warming 0/10.

Root cause:
- Browser/public ticks do not populate server DigitScore.
- Previously only REAL had a Top-N server feed arm endpoint.
- DEMO could therefore stay at 0/10 indefinitely after a cold/restarted server.

Changes:
- Adds POST /sessions/{sid}/top4/arm for BOTH DEMO and REAL.
- Generic backend arm() attaches the canonical server tick subscription.
- Frontend auto-arms a cold Top-N feed when it sees DigitScore below readiness.
- Auto-arm is throttled to once per 10 seconds.
- Manual "Warm Top-N Feed" button works in DEMO and REAL.
- Execute Simultaneous also arms the feed first if it is cold.
- No proposal or purchase is sent by warming.

Replace:
  /bot.html
  /app/top4_basket.py
  /app/top4_main.py
