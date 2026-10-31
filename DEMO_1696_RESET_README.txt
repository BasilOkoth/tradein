DEMO SESSION 1696 OPERATIONAL RESET

This script is ONLY for log 1696 owned consistently by a Demo account. It does NOT establish the real outcome of the Demo purchase, and keeps the historical record marked DEMO_ARCHIVED_UNRECONCILED. It does not affect Real accounts.

1) Deploy this whole project to your test/staging service (or place the script at the root of the same deployed repository).
2) In Render Shell with the SAME database connection as the API, run:
   python reset_stuck_demo_1696.py
   (read-only inspection)
3) Stop the bot. To archive an UNKNOWN demo purchase and clear this specific startup lock, run:
   python reset_stuck_demo_1696.py --reset-demo
4) Preserve the JSON backup file that script creates.
5) Refresh your browser and test Demo START.

The script refuses to run if a known contract ID exists, the session is running, another unresolved log exists, or the session/account/log are not all Demo. Never use this script to claim the trade was not executed.
