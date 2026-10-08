from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from pathlib import Path
import csv
import io
import json

from .engine import engine

BASE = Path(__file__).resolve().parent.parent

app = FastAPI(
    title="DigitMatchStar Top-4 Ranked Basket",
    version="1.0.0-demo-teaching",
)

app.mount("/static", StaticFiles(directory=BASE / "static"), name="static")

class ConfigureRequest(BaseModel):
    mode: str = "DEMO"
    symbol: str = "R_10"
    basket_stake: float = 10.0

@app.on_event("startup")
async def startup():
    try:
        await engine.start_stream()
    except Exception:
        # UI will display stream state; startup remains available even if
        # credentials/env are not configured yet.
        pass

@app.get("/")
def index():
    return FileResponse(BASE / "static" / "index.html")

@app.get("/api/state")
def state():
    return engine.state()

@app.post("/api/configure")
async def configure(req: ConfigureRequest):
    try:
        await engine.configure(
            mode=req.mode,
            symbol=req.symbol,
            basket_stake=req.basket_stake,
        )
        await engine.start_stream()
        return engine.state()
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))

@app.post("/api/basket/open")
async def open_basket():
    try:
        basket = await engine.open_next_basket()
        return basket.as_dict()
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))

@app.get("/api/history")
def history():
    return [b.as_dict() for b in engine.baskets]

@app.get("/api/history.csv")
def history_csv():
    out = io.StringIO()
    fields = [
        "basket_no","symbol","account_mode","source_epoch",
        "source_digit","top4","total_stake","outcome_epoch",
        "outcome_digit","gross_return","net_profit","status"
    ]
    w = csv.DictWriter(out, fieldnames=fields)
    w.writeheader()
    for b in engine.baskets:
        w.writerow({
            "basket_no": b.basket_no,
            "symbol": b.symbol,
            "account_mode": b.account_mode,
            "source_epoch": b.source_epoch,
            "source_digit": b.source_digit,
            "top4": " ".join(str(x.digit) for x in b.ranked),
            "total_stake": b.total_stake,
            "outcome_epoch": b.outcome_epoch,
            "outcome_digit": b.outcome_digit,
            "gross_return": b.gross_return,
            "net_profit": b.net_profit,
            "status": b.status,
        })
    return out.getvalue()
