DIGITMATCHSTAR / TRADEIN — FULL REPLACEMENT FILES

New optional setting: "Optional V1 Entry Threshold" directly under Max Trades in bot.html.

- Leave the field BLANK: original normal execution flow, no V1 threshold gating.
- Enter your own positive numeric value: the V1 score of rank #1 must meet or exceed it to activate a trigger.
- Each trigger permits at most TWO buy claims (not guaranteed fills or outcomes).
- Once exhausted, the score must go below the threshold, then meet or exceed it again to rearm.
- The two-trade cap is checked at the database BUY-claim boundary, including prearmed recovery buys.
- This is a score filter, not a prediction of winning trades.

DEPLOYMENT:
1. Back up your existing repository and deployment database.
2. Extract this ZIP to a new directory. This is a COMPLETE repository, not a patch.
3. Push the files to a TEST branch and deploy a separate DEMO instance, not your live production app.
4. The updated app/db.py attempts additive database columns on startup. Use a test database first, and verify database permissions.
5. On the GUI, leave threshold empty for existing behavior, or enter your desired threshold before configuring/starting the session.
6. Confirm first trigger, two purchases maximum, and rearm behavior on demo before considering any production deployment.

TESTING:
Python compilation and threshold state transitions checked. No live Deriv connection or production deployment tested. The GitHub remote was not modified.

NOTE: Render uses app.tick_sync_main from render.yaml; do not assume all client entry paths use exactly the same mode behavior without end-to-end testing.
