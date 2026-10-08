from fastapi import Depends
from pydantic import BaseModel

# Import the exact existing application. This preserves every current route,
# OAuth flow, account/session behavior, startup hook and CORS configuration.
from .main import app
from .security import current_user_id
from .top4_basket import top4_baskets


class Top4Prepare(BaseModel):
    basket_stake: float = 10.0


class Top4Approve(BaseModel):
    basket_id: str


@app.get("/sessions/{sid}/top4/status")
def top4_status(
    sid: int,
    user_id: str = Depends(current_user_id),
):
    return top4_baskets.status(
        user_id=user_id,
        sid=sid,
    )


@app.post("/sessions/{sid}/top4/prepare")
async def top4_prepare(
    sid: int,
    body: Top4Prepare,
    user_id: str = Depends(current_user_id),
):
    return await top4_baskets.prepare(
        user_id=user_id,
        sid=sid,
        basket_stake=body.basket_stake,
    )


@app.post("/sessions/{sid}/top4/approve")
async def top4_approve(
    sid: int,
    body: Top4Approve,
    user_id: str = Depends(current_user_id),
):
    return await top4_baskets.approve(
        user_id=user_id,
        sid=sid,
        basket_id=body.basket_id,
    )


@app.post("/sessions/{sid}/top4/reject")
async def top4_reject(
    sid: int,
    user_id: str = Depends(current_user_id),
):
    return await top4_baskets.reject(
        user_id=user_id,
        sid=sid,
    )


@app.get("/sessions/{sid}/top4/export")
def top4_export(
    sid: int,
    user_id: str = Depends(current_user_id),
):
    return top4_baskets.export(
        user_id=user_id,
        sid=sid,
    )


# ---------------------------------------------------------------------------
# RENDER / LEGACY HEALTH COMPATIBILITY
# ---------------------------------------------------------------------------

@app.get("/api/state")
def legacy_api_state():
    """
    Backward-compatible health endpoint.

    The previous simplified Top-4 service used /api/state as its Render health
    check. Keeping this endpoint prevents an existing Render service setting
    from marking the new full backend unhealthy during deployment.
    """
    return {
        "ok": True,
        "service": "digitmatchstar-top4-api",
        "backend": "DigitMatchStar Production OAuth Backend",
        "top4": True,
        "health_endpoint": "/health",
    }
