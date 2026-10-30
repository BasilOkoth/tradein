TradeIn optional V1 threshold patch - unvalidated prototype

1. Download repository ZIP at https://github.com/BasilOkoth/tradein and extract it.
2. Copy apply_v1_gate.py and v1_gate.py into the repository root (folder with bot.html).
3. Run `py apply_v1_gate.py` from that folder.
4. The patch creates timestamped backups of every changed file.
5. Inspect changes and run tests (`py test_v1_gate.py` if copied), then test in a staging DEMO deployment before pushing or deploying production.
6. Blank threshold preserves the existing trading behavior. 15 (or 11) activates gate: at most two purchase claims for each new crossing of the V1 score. Following two, score must fall below and reach threshold again. A cycle can finish after a win with fewer than 2 purchases.

IMPORTANT: GitHub connector did not grant write access. This ZIP does not change the repository or deployed bot. The new gate is not end-to-end verified on Deriv. It is a development patch and is NOT safe to deploy to real-money production without independent review. V1 ranking scores are not probabilities and do not guarantee profitable trades.
