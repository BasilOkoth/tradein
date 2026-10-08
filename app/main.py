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


def _analytics_payload():
    settled = [b for b in engine.baskets if b.status == "SETTLED"]

    total = len(settled)
    hits = 0
    rank_wins = {1: 0, 2: 0, 3: 0, 4: 0}
    rows = []
    cumulative = 0.0

    for b in settled:
        winning_rank = None
        for leg in b.legs:
            if leg.result == "WIN":
                winning_rank = int(leg.rank)
                rank_wins[winning_rank] += 1
                hits += 1
                break

        cumulative = round(cumulative + float(b.net_profit), 2)

        rows.append({
            "basket_no": b.basket_no,
            "source_epoch": b.source_epoch,
            "source_digit": b.source_digit,
            "top4": [x.digit for x in b.ranked],
            "scores": [x.score for x in b.ranked],
            "outcome_epoch": b.outcome_epoch,
            "outcome_digit": b.outcome_digit,
            "hit": winning_rank is not None,
            "winning_rank": winning_rank,
            "total_stake": round(float(b.total_stake), 2),
            "gross_return": round(float(b.gross_return), 2),
            "net_profit": round(float(b.net_profit), 2),
            "cumulative_profit": cumulative,
        })

    misses = total - hits
    total_stake = round(sum(float(b.total_stake) for b in settled), 2)
    total_return = round(sum(float(b.gross_return) for b in settled), 2)
    net_profit = round(sum(float(b.net_profit) for b in settled), 2)

    hit_rate = round((hits / total) * 100.0, 2) if total else 0.0
    miss_rate = round((misses / total) * 100.0, 2) if total else 0.0
    roi = round((net_profit / total_stake) * 100.0, 2) if total_stake else 0.0

    profits = [float(b.net_profit) for b in settled]

    return {
        "settled_baskets": total,
        "hits": hits,
        "misses": misses,
        "hit_rate_pct": hit_rate,
        "miss_rate_pct": miss_rate,
        "rank_wins": rank_wins,
        "rank_win_rate_pct": {
            str(rank): round((count / total) * 100.0, 2) if total else 0.0
            for rank, count in rank_wins.items()
        },
        "total_stake": total_stake,
        "total_return": total_return,
        "net_profit": net_profit,
        "roi_pct": roi,
        "average_profit_per_basket": round(net_profit / total, 2) if total else 0.0,
        "best_basket_profit": round(max(profits), 2) if profits else 0.0,
        "worst_basket_profit": round(min(profits), 2) if profits else 0.0,
        "rows": rows,
    }


@app.get("/api/analytics")
def analytics():
    return _analytics_payload()


@app.get("/api/analytics.json")
def analytics_json():
    return _analytics_payload()


@app.get("/api/analytics.csv")
def analytics_csv():
    payload = _analytics_payload()
    out = io.StringIO()

    fields = [
        "basket_no","source_epoch","source_digit","top4","scores",
        "outcome_epoch","outcome_digit","hit","winning_rank",
        "total_stake","gross_return","net_profit","cumulative_profit"
    ]
    w = csv.DictWriter(out, fieldnames=fields)
    w.writeheader()

    for row in payload["rows"]:
        w.writerow({
            **row,
            "top4": " ".join(str(x) for x in row["top4"]),
            "scores": " ".join(str(x) for x in row["scores"]),
        })

    out.write("\nSUMMARY\n")
    summary = [
        ("settled_baskets", payload["settled_baskets"]),
        ("hits", payload["hits"]),
        ("misses", payload["misses"]),
        ("hit_rate_pct", payload["hit_rate_pct"]),
        ("miss_rate_pct", payload["miss_rate_pct"]),
        ("rank1_wins", payload["rank_wins"][1]),
        ("rank2_wins", payload["rank_wins"][2]),
        ("rank3_wins", payload["rank_wins"][3]),
        ("rank4_wins", payload["rank_wins"][4]),
        ("total_stake", payload["total_stake"]),
        ("total_return", payload["total_return"]),
        ("net_profit", payload["net_profit"]),
        ("roi_pct", payload["roi_pct"]),
        ("average_profit_per_basket", payload["average_profit_per_basket"]),
        ("best_basket_profit", payload["best_basket_profit"]),
        ("worst_basket_profit", payload["worst_basket_profit"]),
    ]
    out.write("metric,value\n")
    for key, value in summary:
        out.write(f"{key},{value}\n")

    return out.getvalue()


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
