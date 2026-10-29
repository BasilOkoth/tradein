# TradeIn live-stream and reconciliation integration fix

Full replacement files (not patches): `bot.html`, `app/execution_v2.py`, `render.yaml`.

## Main changes
- A public tick received by the browser updates Current Tick, Digit, and Ticks/sec even when the secure backend worker is running. This path is strictly display-only; it NEVER submits a Deriv order, changes trading decisions, or calls handleTick.
- The backend v2.1 checks tick subscription freshness starting from the moment a subscription is created. Previously the watchdog did not consider a subscription stale until at least one tick arrived, so a stream with *zero first ticks* could remain indefinitely silent.
- No contract is marked lost from a timeout. The open Deriv contract remains under authoritative reconciliation until a verified sold response is obtained.

## Installation
1. Upload `bot.html` to the root of the `tradein` repository.
2. Upload `app/execution_v2.py` to `app/` (replace the previous version).
3. Replace `render.yaml`, including its start command pointing to `app.execution_v2:app`.
4. Deploy a separate DEMO instance first; preserve the original database and existing contracts.
5. Visit `/execution-v2/health`, confirm Render startup logs and confirm live browser tick displays update. Do not press START until the previously unresolved contract has a verified settled status.

## Limits
This package was Python syntax-checked and its inline JavaScript was parsed by Node. Not live-tested on Deriv or Render. An unresolved contract still blocks a safe subsequent purchase; this is deliberate. A public UI tick does not establish that the authenticated backend received the same tick.
