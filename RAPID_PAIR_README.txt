RAPID PAIR EXPERIMENTAL - DEMO ONLY

When a V1 threshold is set and reached, the Demo engine requests two separate buy proposals and purchases both in succession without waiting for the first settlement. Settlement is polled independently by contract ID. Leave threshold BLANK to use the original trading path.

IMPORTANT LIMITATIONS:
- Real mode deliberately refuses rapid pair purchases: existing per-buy real confirmations and restart reconciliation do not yet support overlapping contracts safely.
- On process restart, in-memory outstanding tracking is lost; reconcile OPEN TradeLog rows with Deriv before trading again. Do not use this path in production without persistence/restart reconciliation.
- Trigger requires the configured V1 crossing; two consecutive purchases are not guaranteed to fall on consecutive ticks due to Deriv API latency.
- Test in a separate Demo deployment first.
