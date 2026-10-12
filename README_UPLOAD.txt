Tradein Top-4 full-file replacement | 2026-10-12

Root folder contains COMPLETE files with original repository paths.
Replace corresponding files in your repository and deploy in DEMO first.

Root cause addressed: four prepared proposal IDs were accumulated before any buy; all 4 buy requests then failed InvalidContractProposal/Unknown contract proposal.
DEMO now requests a fresh proposal and buys it immediately for each leg, with one buy attempt per leg. No automatic retry on a failed or ambiguous BUY. This changes the 4-leg basket from near-simultaneous purchase to sequential purchases, so legs can execute on different ticks. The system still reports partial opening and requires reconciliation. Do NOT interpret the four legs as simultaneous exposure.

REAL code path was not converted to this sequential approach. REAL purchases still require existing explicit user actions. Check server logs and live Deriv API separately before using real funds.

Verification: Python modules compile; JS syntax checks pass. No live trades/API validation was performed. Review deployment errors and inspect the account balance for any uncertain order before attempting again.
