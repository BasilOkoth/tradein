RAPID TWO-PURCHASE V1 THRESHOLD — EXPERIMENTAL

DEMO: available when a V1 threshold is set.
REAL: opt-in only. The server must explicitly set ALLOW_REAL_MODE=true and the user must type click OK in the Real purchase authorization dialog when prompted on Start. This authorizes at most two purchases for that session, and it expires in five minutes. Do not enable Real until independently tested.

Set the optional V1 Threshold field yourself. Leave blank for normal trading. When eligible the rapid path submits two separate proposals and purchases without awaiting the first settlement. Actual purchase speed depends on Deriv.

BUY uncertainty: an intent is written to the database before each purchase. If Deriv fails to return a confirmed contract ID, the session stops and requires MANUAL reconciliation with Deriv transaction history before attempting another trade. No automatic retry is performed.

Outstanding OPEN records prevent new batches and restarting the session. Background settlement polling is best-effort, and does NOT currently resume automatically after a Render restart. A restart can therefore leave OPEN records requiring manual reconciliation; this is a deliberate fail-closed limitation.

REAL MODE IS EXPERIMENTAL AND NOT VALIDATED AGAINST THE LIVE DERIV REAL API. Use a separate staging database, test DEMO, and do not enable real purchases until reconciliation, restart recovery, and two-purchase authorization have been independently verified.

The existing demo-only buy API remains unchanged; a separate real purchase method enforces the server-level opt-in.
