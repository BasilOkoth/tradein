import base64
import hashlib
import json
import secrets
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode, quote

import jwt
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import RedirectResponse
from pydantic import BaseModel

from .config import settings
from .db import SessionLocal
from .models import OAuthPending, DerivCredential, DerivAccount
from .security import current_user_id, encrypt_token
from .deriv_rest import exchange_code, get_accounts

router = APIRouter(tags=["authentication"])
OAUTH_MODE_PREFIX = "__oauth_mode__:"
LOGIN_TICKET_PREFIX = "__login_ticket__:"
LOGIN_TICKET_TTL_MINUTES = 2
PLATFORM_SESSION_HOURS = 12

class TicketExchangeRequest(BaseModel):
    ticket: str

def _pkce():
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode()).digest()
    ).decode().rstrip("=")
    return verifier, challenge

def _ticket_db_key(ticket: str) -> str:
    return f"{LOGIN_TICKET_PREFIX}{hashlib.sha256(ticket.encode()).hexdigest()}"

def _requested_mode_from_pending(pending):
    value = str(pending.user_id or "")
    if value.startswith(OAUTH_MODE_PREFIX):
        mode = value[len(OAUTH_MODE_PREFIX):].upper()
        if mode in {"DEMO","REAL"}:
            return mode
    return "DEMO"

def _stable_user_id(accounts):
    ids = sorted(str(a.get("account_id")) for a in accounts if a.get("account_id"))
    if not ids:
        raise HTTPException(400, "No Deriv Options trading account was returned.")
    return "deriv:" + hashlib.sha256("|".join(ids).encode()).hexdigest()

def _issue_platform_jwt(user_id):
    if not settings.platform_jwt_secret:
        raise HTTPException(500, "PLATFORM_JWT_SECRET is not configured")
    now = datetime.now(timezone.utc)
    payload = {
        "sub": user_id,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(hours=PLATFORM_SESSION_HOURS)).timestamp()),
        "type": "digitmatchstar_session",
    }
    return jwt.encode(payload, settings.platform_jwt_secret, algorithm=settings.platform_jwt_algorithm)

def _frontend_url():
    if not settings.frontend_url:
        raise HTTPException(500, "FRONTEND_URL is not configured")
    return settings.frontend_url.rstrip("/")

@router.get("/auth/deriv/start")
def start_deriv_oauth(mode: str = "DEMO"):
    mode = str(mode or "DEMO").upper()
    if mode not in {"DEMO","REAL"}:
        raise HTTPException(400, "mode must be DEMO or REAL")
    if not settings.deriv_client_id:
        raise HTTPException(500, "DERIV_CLIENT_ID is not configured")
    if not settings.deriv_redirect_uri:
        raise HTTPException(500, "DERIV_REDIRECT_URI is not configured")
    verifier, challenge = _pkce()
    state = secrets.token_urlsafe(32)
    db = SessionLocal()
    try:
        db.add(OAuthPending(
            state=state,
            user_id=f"{OAUTH_MODE_PREFIX}{mode}",
            code_verifier=verifier,
            created_at=datetime.utcnow(),
        ))
        db.commit()
    finally:
        db.close()
    params = {
        "response_type":"code",
        "client_id":settings.deriv_client_id,
        "redirect_uri":settings.deriv_redirect_uri,
        "scope":settings.deriv_scope,
        "state":state,
        "code_challenge":challenge,
        "code_challenge_method":"S256",
    }
    if settings.deriv_legacy_app_id:
        params["app_id"] = settings.deriv_legacy_app_id
    return {"authorization_url":"https://auth.deriv.com/oauth2/auth?"+urlencode(params),"mode":mode}

@router.get("/auth/deriv/callback")
async def deriv_callback(code: str|None=None, state: str|None=None, error: str|None=None, error_description: str|None=None):
    frontend = _frontend_url()
    if error:
        return RedirectResponse(frontend + "/?auth_error=" + quote(str(error_description or error)))
    if not code or not state:
        return RedirectResponse(frontend + "/?auth_error=" + quote("Missing OAuth code or state"))
    db = SessionLocal()
    try:
        pending = db.query(OAuthPending).filter(OAuthPending.state == state).first()
        if not pending:
            return RedirectResponse(frontend + "/?auth_error=" + quote("Invalid or expired OAuth state"))
        if pending.created_at and datetime.utcnow() - pending.created_at > timedelta(minutes=15):
            db.delete(pending); db.commit()
            return RedirectResponse(frontend + "/?auth_error=" + quote("OAuth session expired. Please connect again."))
        requested_mode = _requested_mode_from_pending(pending)
        token_data = await exchange_code(code, pending.code_verifier)
        access_token = token_data.get("access_token")
        if not access_token:
            raise HTTPException(400, "Deriv token exchange returned no access token")
        payload = await get_accounts(access_token)
        accounts = payload.get("data", [])
        if isinstance(accounts, dict):
            accounts = [accounts]
        accounts = [a for a in accounts if a and a.get("account_id")] if isinstance(accounts, list) else []
        if not accounts:
            return RedirectResponse(frontend + "/?auth_error=" + quote("No Deriv Options trading account was returned for this login."))
        if requested_mode == "REAL" and not any(str(a.get("account_type","")).lower()=="real" for a in accounts):
            return RedirectResponse(frontend + "/?auth_error=" + quote("No REAL Deriv Options account is available for this login."))
        user_id = _stable_user_id(accounts)
        expires_at = None
        try:
            if token_data.get("expires_in"):
                expires_at = datetime.utcnow() + timedelta(seconds=int(token_data["expires_in"]))
        except Exception:
            pass
        cred = db.query(DerivCredential).filter(DerivCredential.user_id == user_id).first()
        encrypted = encrypt_token(access_token)
        if not cred:
            cred = DerivCredential(user_id=user_id, encrypted_access_token=encrypted)
            db.add(cred)
        else:
            cred.encrypted_access_token = encrypted
        cred.token_type = token_data.get("token_type","Bearer")
        cred.expires_at = expires_at
        cred.updated_at = datetime.utcnow()
        db.query(DerivAccount).filter(DerivAccount.user_id == user_id).delete()
        for a in accounts:
            bal = None
            try:
                if a.get("balance") is not None:
                    bal = float(a["balance"])
            except Exception:
                pass
            db.add(DerivAccount(
                user_id=user_id,
                account_id=str(a.get("account_id")),
                account_type=str(a.get("account_type","")).lower(),
                currency=a.get("currency"),
                status=a.get("status"),
                balance=bal,
                raw_json=json.dumps(a),
                updated_at=datetime.utcnow(),
            ))
        ticket = secrets.token_urlsafe(48)
        pending.state = _ticket_db_key(ticket)
        pending.user_id = user_id
        pending.code_verifier = requested_mode
        pending.created_at = datetime.utcnow()
        db.commit()
        return RedirectResponse(f"{frontend}/?{urlencode({'dms_ticket':ticket,'mode':requested_mode})}")
    except HTTPException:
        raise
    except Exception as exc:
        db.rollback()
        return RedirectResponse(frontend + "/?auth_error=" + quote(str(exc)))
    finally:
        db.close()

@router.post("/auth/platform/exchange")
def exchange_login_ticket(body: TicketExchangeRequest):
    ticket = str(body.ticket or "").strip()
    if not ticket:
        raise HTTPException(400, "Login ticket is required")
    db = SessionLocal()
    try:
        pending = db.query(OAuthPending).filter(OAuthPending.state == _ticket_db_key(ticket)).first()
        if not pending:
            raise HTTPException(401, "Invalid or already-used login ticket")
        if not pending.created_at or datetime.utcnow() - pending.created_at > timedelta(minutes=LOGIN_TICKET_TTL_MINUTES):
            db.delete(pending); db.commit()
            raise HTTPException(401, "Login ticket expired. Connect through Deriv again.")
        user_id = pending.user_id
        requested_mode = str(pending.code_verifier or "DEMO").upper()
        if requested_mode not in {"DEMO","REAL"}:
            requested_mode = "DEMO"
        db.delete(pending); db.commit()
        token = _issue_platform_jwt(user_id)
        accounts = db.query(DerivAccount).filter(DerivAccount.user_id == user_id).all()
        return {
            "token":token,"token_type":"Bearer","expires_in":PLATFORM_SESSION_HOURS*3600,
            "requested_mode":requested_mode,
            "accounts":[{"account_id":a.account_id,"account_type":a.account_type,"currency":a.currency,"status":a.status,"balance":a.balance} for a in accounts],
        }
    finally:
        db.close()

@router.get("/auth/platform/me")
def platform_me(user_id: str = Depends(current_user_id)):
    db = SessionLocal()
    try:
        accounts = db.query(DerivAccount).filter(DerivAccount.user_id == user_id).all()
        return {"authenticated":True,"user_id":user_id,"accounts":[{"account_id":a.account_id,"account_type":a.account_type,"currency":a.currency,"status":a.status,"balance":a.balance} for a in accounts]}
    finally:
        db.close()

@router.get("/auth/deriv/accounts")
def deriv_accounts(user_id: str = Depends(current_user_id)):
    db = SessionLocal()
    try:
        rows = db.query(DerivAccount).filter(DerivAccount.user_id == user_id).all()
        return [{"account_id":r.account_id,"account_type":r.account_type,"currency":r.currency,"status":r.status,"balance":r.balance} for r in rows]
    finally:
        db.close()
