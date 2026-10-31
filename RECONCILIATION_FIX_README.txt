RAPID PAIR RESTART RECONCILIATION

The /sessions/{sid}/start route now checks OPEN contract IDs against the authenticated Deriv contract-status API before allowing new buys. Settled trades are recorded exactly once, with P/L updated.

SUBMITTING / UNCERTAIN purchases without a confirmed contract ID are still BLOCKED and need manual Deriv transaction-history reconciliation. The bot will NOT guess whether a purchase took place or retry an uncertain purchase. OPEN contracts that have not yet settled remain blocked until their status is confirmed.

The Real batch authorization endpoint also reconciles known OPEN contracts. This is not a full restart-safe transaction ledger or proof of production readiness. Test in Demo before deploying and do not clear unresolved database records.
