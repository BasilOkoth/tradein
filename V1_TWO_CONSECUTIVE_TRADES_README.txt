OPTIONAL V1 SCORE THRESHOLD — TWO-TRADE BATCH

Blank V1 Threshold: existing normal trading behavior.
When filled, V1 rank #1 score must meet the user-defined threshold to START a two-purchase batch.
The batch may continue to its SECOND purchase after the V1 score drops below the threshold.
The existing settlement, Stop, pause, real confirmation, max-trade and BUY-claim safety logic remains in effect.
After purchase two, the bot waits until V1 drops below the threshold and reaches it again.
This version does not guarantee the two trades occur on adjacent ticks: official settlement and purchase controls can introduce gaps.

Changes from the previous zip:
- Removed the below-threshold reset from the middle of an active batch in engine.step.
- Buy boundary enforces the threshold on batch entry only, not on the second purchase.
- Rank re-alignment check only applies to batch entry, not the second purchase.

Test on DEMO before deploying to live trading. No predictive profitability is implied by the V1 score.
