from fastapi import Depends
from pydantic import BaseModel, Field

from .main import app
from .security import current_user_id
from .top4_basket import top4_baskets
from .db import SessionLocal


class TopNExecute(BaseModel):
    basket_stake: float = 7.0
    top_n: int = Field(default=7, ge=1, le=7)


class Top4RecoveryStart(BaseModel):
    starting_leg_stake: float = Field(default=0.35, ge=0.35)


class Top4CapitalPlan(BaseModel):
    starting_leg_stake: float = Field(default=0.35, ge=0.35)


@app.get("/sessions/{sid}/top4/status")
def top4_status(
    sid: int,
    user_id: str = Depends(current_user_id),
):
    return top4_baskets.status(
        user_id=user_id,
        sid=sid,
    )


@app.post("/sessions/{sid}/top4/arm")
async def topn_arm(
    sid: int,
    user_id: str = Depends(current_user_id),
):
    """
    Warm the server-side canonical Top-N tick feed for either DEMO or REAL.
    No proposal or purchase is sent.
    """
    return await top4_baskets.arm(
        user_id=user_id,
        sid=sid,
    )


@app.post("/sessions/{sid}/top4/recovery-demo")
async def top4_recovery_demo(
    sid: int,
    body: Top4RecoveryStart,
    user_id: str = Depends(current_user_id),
):
    return await top4_baskets.execute_demo_recovery_cycle(
        user_id=user_id,
        sid=sid,
        starting_leg_stake=body.starting_leg_stake,
    )


@app.post("/sessions/{sid}/top4/capital-plan")
def top4_capital_plan(
    sid: int,
    body: Top4CapitalPlan,
    user_id: str = Depends(current_user_id),
):
    db = SessionLocal()
    try:
        top4_baskets._owned_session(db, user_id, sid)
    finally:
        db.close()

    return top4_baskets.capital_plan(
        body.starting_leg_stake
    )


@app.post("/sessions/{sid}/top4/execute")
async def topn_execute_demo(
    sid: int,
    body: TopNExecute,
    user_id: str = Depends(current_user_id),
):
    return await top4_baskets.execute_now(
        user_id=user_id,
        sid=sid,
        basket_stake=body.basket_stake,
        top_n=body.top_n,
        execute_real_now=False,
    )


@app.post("/sessions/{sid}/top4/arm-real")
async def topn_arm_real(
    sid: int,
    user_id: str = Depends(current_user_id),
):
    # Kept for older frontends.
    return await top4_baskets.arm_real(
        user_id=user_id,
        sid=sid,
    )


@app.post("/sessions/{sid}/top4/execute-real")
async def topn_execute_real(
    sid: int,
    body: TopNExecute,
    user_id: str = Depends(current_user_id),
):
    return await top4_baskets.execute_now(
        user_id=user_id,
        sid=sid,
        basket_stake=body.basket_stake,
        top_n=body.top_n,
        execute_real_now=True,
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
        "service": "digitmatchstar-topn-api",
        "auth": "DERIV_OAUTH",
        "topn_mode": "TOP4_RECOVERY_V18_REAL_EXECUTION_STATE",
        "min_top_n": 1,
        "max_top_n": 7,
        "default_top_n": 7,
    }
