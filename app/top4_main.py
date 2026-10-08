from fastapi import Depends
from pydantic import BaseModel

from .main import app
from .security import current_user_id
from .top4_basket import top4_baskets


class Top4Execute(BaseModel):
    basket_stake: float = 10.0


@app.get("/sessions/{sid}/top4/status")
def top4_status(
    sid: int,
    user_id: str = Depends(current_user_id),
):
    return top4_baskets.status(
        user_id=user_id,
        sid=sid,
    )


@app.post("/sessions/{sid}/top4/execute")
async def top4_execute(
    sid: int,
    body: Top4Execute,
    user_id: str = Depends(current_user_id),
):
    """
    One-step Top-4 action.

    DEMO:
      freezes the fresh Top-4 ranking and immediately starts four
      proposal->buy pipelines.

    REAL:
      returns a fresh preview only. No real-money BUY is transmitted.
    """
    return await top4_baskets.execute_now(
        user_id=user_id,
        sid=sid,
        basket_stake=body.basket_stake,
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


@app.get("/api/state")
def legacy_api_state():
    return {
        "ok": True,
        "service": "digitmatchstar-top4-api",
        "backend": "DigitMatchStar Production OAuth Backend",
        "top4": True,
        "top4_mode": "IMMEDIATE_DEMO",
        "health_endpoint": "/health",
    }
