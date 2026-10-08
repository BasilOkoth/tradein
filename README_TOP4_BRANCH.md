# DigitMatchStar Top-4 Manual Basket Branch Overlay

Base repository:
`BasilOkoth/derivmatchstars`

Base commit used for this overlay:
`78d6837b32e39b3492efea51ebb7d1f3ee39ac10`

## Purpose

This is **not** a replacement standalone application. It is an overlay for a
new branch created from the current DigitMatchStar `main` branch.

It deliberately reuses the current:

- FastAPI application and routes
- OAuth / PKCE flow
- Deriv account discovery
- PostgreSQL database and SQLAlchemy models
- encrypted Deriv credentials
- platform JWT authentication
- `MultiUserEngine`
- `DerivWS`
- DigitScore V1 ranking and Trigger Fusion shadow
- existing `bot.html`
- existing research suite
- existing Render environment structure

## Create the branch

Create a new branch from the current `main` branch, for example:

`top4-ranked-manual-basket`

Then copy the files from this ZIP into that branch.

Only these paths are added/replaced:

- `app/top4_basket.py` — NEW
- `app/top4_main.py` — NEW
- `top4-basket-ui.js` — NEW
- `research-suite-loader.js` — REPLACE
- `render.yaml` — REPLACE for the Top-4 Render service

Everything else stays exactly as it is on the base branch.

## How it works

The panel reads the same `st.digit_score.ranking` used by the existing app.

PREPARE:
1. freezes rank #1, #2, #3 and #4 from one server score snapshot;
2. divides the basket stake by four;
3. obtains four read-only Deriv proposals;
4. shows the exact digits, scores, stakes and payout previews;
5. places no trade.

APPROVE:
- DEMO: sends the four prepared DEMO buys together using the existing
  `DerivWS.buy(..., demo=True)` protection.
- REAL: records the approval and final basket preview, but does not send
  real-money BUY instructions.

There is no automatic next basket. Every basket starts with a fresh manual
PREPARE and APPROVE action.

## Exports

`GET /sessions/{sid}/top4/export`

The UI has **Export Top-4 JSON**.

It reports:
- frozen Top-4 digits and ranks
- score for each rank
- source epoch
- stake per leg
- contract IDs
- WIN/LOSS for each rank
- net P/L per basket
- Top-4 basket hit rate
- ROI
- rank #1/#2/#3/#4 win counts

## Telegram

Optional environment variables:

- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_ADMIN_CHAT_ID` or `TELEGRAM_CHAT_ID`

Notifications are sent when:
- a basket is prepared;
- a DEMO basket opens;
- a DEMO basket settles;
- a REAL preview is manually approved.

Telegram failure never blocks the trading/session engine.

## Render

The included `render.yaml` starts:

`uvicorn app.top4_main:app --host 0.0.0.0 --port $PORT`

Use a separate Render Blueprint/service for this branch so it does not replace
your existing single-target deployment.

## Important behavior

The Top-4 branch does not alter the existing single-target execution engine.
The single-target session must not have an open contract when a Top-4 basket is
prepared.

The four DEMO proposals are prepared first, and after approval their BUY
requests are submitted concurrently. Each Deriv contract's actual purchase and
settlement data is persisted separately, so you can verify whether the four
contracts truly aligned to the same market interval rather than assuming they did.
