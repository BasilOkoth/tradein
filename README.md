# DigitMatchStar Top‑4 Ranked Basket

A separate branch-ready teaching build for a four-leg Digit Match basket.

## What it does

- Reads a canonical Deriv tick stream.
- Ranks digits 0–9.
- Freezes ranks #1–#4 from one source epoch.
- Splits a basket stake equally across four Digit Match legs.
- In **DEMO** mode, requests four Deriv proposals and buys four DEMO contracts.
- Uses the first future canonical tick as the teaching outcome and calculates basket P/L.
- Sends Telegram OPEN/SETTLED messages when `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` are configured.
- Exposes history and CSV export.
- Includes a classroom-friendly dashboard.

## REAL mode

REAL mode is intentionally **signal/read-only** in this build. It shows the same top-4 basket and can connect to the live stream, but does not send real-money orders.

## Economics example

For a $10 basket:

- 4 legs × $2.50 = $10 total.
- If one winning $2.50 leg has a total return multiplier of 7.929:
  - return = $2.50 × 7.929 = $19.8225
  - basket net = $19.8225 − $10 = **+$9.8225**

The running DEMO bot does not hard-code 7.929. It stores the actual payout returned by each Deriv proposal.

## Run locally

```bash
python -m venv .venv
# activate your virtual environment
pip install -r requirements.txt
cp .env.example .env
# fill in DERIV_APP_ID + DEMO token
uvicorn app.main:app --reload --port 8000
```

Open `http://127.0.0.1:8000`.

## Telegram

Create a Telegram bot with BotFather, then set:

```env
TELEGRAM_BOT_TOKEN=...
TELEGRAM_CHAT_ID=...
```

Messages are sent when a DEMO basket opens and settles, and when a REAL teaching signal is generated.

## Branch upload

Create a new branch such as:

`top4-ranked-basket`

and upload the contents of this ZIP to that branch. It is self-contained and does not need to overwrite your existing single-target branch.
