# DigitMatchStar Top-4 Manual Basket

This repository is the **full DigitMatchStar production backend plus the Top-4 manual basket layer**.

It uses the existing secure OAuth/account/session architecture, PostgreSQL/SQLAlchemy models, encrypted Deriv credentials, server-side DigitScore ranking, Deriv WebSocket client, research tooling, and the existing DigitMatchStar frontend.

## Top-4 flow

1. The server ranks digits 0–9 with the same DigitScore engine used by the main DigitMatchStar backend.
2. **PREPARE TOP-4** freezes ranks #1, #2, #3 and #4 from one server score snapshot.
3. The total basket stake is divided across the four ranked digits.
4. Four Deriv proposals are requested.
5. The user reviews the prepared basket.
6. **APPROVE** or **REJECT** is required.
7. In **DEMO**, approval can submit four DEMO Digit Match contracts.
8. In **REAL**, this build records the manual approval/preview but does not transmit real-money BUY instructions.

There is no automatic next basket.

## Render deployment

Use Python **3.11.11**.

### Build Command

```bash
pip install --upgrade pip && pip install -r requirements.txt
```

### Start Command

```bash
uvicorn app.top4_main:app --host 0.0.0.0 --port $PORT
```

### Health Check Path

```text
/health
```

The repository also keeps a compatibility endpoint:

```text
GET /api/state
```

When `app.top4_main:app` is running, both `/health` and `/api/state` should return HTTP 200.

## Required Render environment variables

```text
DATABASE_URL
DERIV_CLIENT_ID
DERIV_REDIRECT_URI
TOKEN_ENCRYPTION_KEY
PLATFORM_JWT_SECRET
FRONTEND_URL
```

Recommended values:

```text
DERIV_SCOPE=trade
PLATFORM_JWT_ALGORITHM=HS256
TRUST_PLATFORM_USER_HEADER=false
ALLOW_REAL_MODE=true
PYTHON_VERSION=3.11.11
```

Optional:

```text
DERIV_LEGACY_APP_ID
TELEGRAM_BOT_TOKEN
TELEGRAM_CHAT_ID
TELEGRAM_ADMIN_CHAT_ID
```

## Top-4 API routes

```text
GET  /sessions/{sid}/top4/status
POST /sessions/{sid}/top4/prepare
POST /sessions/{sid}/top4/approve
POST /sessions/{sid}/top4/reject
GET  /sessions/{sid}/top4/export
```

The normal DigitMatchStar OAuth, account, session and health routes remain available because `app.top4_main` imports the production FastAPI application from `app.main`.

## Python version

The service is pinned to Python 3.11.11 because the current dependency set includes:

```text
pydantic==2.9.2
pydantic-core==2.23.4
```

Python 3.11 uses prebuilt wheels for these dependencies. This avoids the failed Python 3.14 Rust/maturin source build seen on Render.

The repository should contain:

```text
.python-version
runtime.txt
render.yaml
```

with Python 3.11.11 configured.

## Architecture

Active backend:

```text
app.main
    ↓
production OAuth/account/session backend

app.top4_main
    ↓
imports app.main
    +
adds Top-4 manual basket routes
    +
adds /api/state compatibility endpoint
```

The Render service **must start `app.top4_main:app`**, not the old standalone `app.main:app` command.

## Obsolete standalone instructions

Older instructions referring to these are no longer valid for this repository:

```text
DERIV_APP_ID
DERIV_TOKEN
/api/history
/api/analytics
uvicorn app.main:app --host 0.0.0.0 --port $PORT
```

Those belonged to the earlier simplified standalone Top-4 prototype.

## Deployment verification

A successful Render deployment should show:

```text
Build successful
GET /health HTTP/1.1 200 OK
```

or the compatibility health check:

```text
GET /api/state HTTP/1.1 200 OK
```

If `/api/state` returns 404, check the **actual Render service Start Command** and make sure it is:

```bash
uvicorn app.top4_main:app --host 0.0.0.0 --port $PORT
```
