# TradeIn V2.3 — Deriv contract_id wire-format repair

## Confirmed root cause
`/execution-v2/health` showed `InputValidationFailed: Input validation failed: contract_id` for contract 18616106419.
`app/deriv_ws.py` sends `contract_id` as a string in both `contract_status` and `subscribe_contract`.
The installed controller V2.3 now wraps `DerivWS.request` and converts `contract_id` into a **positive integer** whenever it sends a `proposal_open_contract` request. The original database IDs remain unchanged.

## Install
Replace `app/execution_v2.py` and retain `render.yaml` with `uvicorn app.execution_v2:app` as configured. `bot.html` is included as the matching complete dashboard version; replacing it is not necessary for this specific fix.

1. Stop the bot and use a separate **DEMO** deployment (`ALLOW_REAL_MODE=false`).
2. Deploy, and confirm `/execution-v2/health` reports `settlement-authoritative-v2.3` and `contract_id_fix_installed: true`.
3. `contract_checks.session-1.state` should change from `CHECK_FAILED` to `SOLD` or `OPEN` if Deriv permits the query. Other API issues can still occur.
4. If the contract is `SOLD`, the existing reconciliation handler should attempt to clear `open_contract_id`. Inspect logs/DB to verify; do not manually clear it without authoritative evidence.
5. Confirm next eligible tick generates exactly one purchase only after settlement and account-mode checks; never force REAL purchases.

No authenticated connection or live trading tests were performed. Python syntax is validated. This package does not guarantee every physical tick results in a purchase.
