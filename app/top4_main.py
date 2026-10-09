from fastapi import Depends
from pydantic import BaseModel, Field

from .main import app
from .security import current_user_id
from .top4_basket import top4_baskets


class TopNExecute(BaseModel):
    basket_stake: float = 7.0
    top_n: int = Field(default=7, ge=1, le=7)


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
        "topn_mode": "SIMULTANEOUS_OAUTH_PRESERVED",
        "min_top_n": 1,
        "max_top_n": 7,
        "default_top_n": 7,
    }
