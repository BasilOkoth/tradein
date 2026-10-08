from enum import Enum
from fastapi import Depends
from pydantic import BaseModel, Field

from .main import app
from .security import current_user_id
from .top4_basket import top4_baskets


class ExecutionMode(str, Enum):
    DEMO = "demo"
    REAL = "real"


class Top4Execute(BaseModel):
    basket_stake: float = Field(default=10.0, gt=0)
    mode: ExecutionMode = Field(
        default=ExecutionMode.DEMO,
        description="Execution mode: 'demo' for simulated pipeline, 'real' for live Deriv trades."
    )


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
    One-step Top-4 action supporting both DEMO and REAL modes.

    DEMO:
      freezes the fresh Top-4 ranking and simulates the four proposal->buy pipelines.

    REAL:
      freezes the fresh Top-4 ranking and executes live Deriv WebSocket proposal
      and buy requests for real money trades.
    """
    return await top4_baskets.execute_now(
        user_id=user_id,
        sid=sid,
        basket_stake=body.basket_stake,
        mode=body.mode.value,
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
        "top4_mode": "HYBRID_DEMO_AND_REAL",
        "supported_modes": ["demo", "real"],
        "health_endpoint": "/health",
    }