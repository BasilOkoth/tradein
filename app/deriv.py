import asyncio
import json
import websockets
from .config import settings

class DerivClient:
    def __init__(self, token=None):
        self.token = token or settings.deriv_token
        self.ws = None
        self.req_id = 0
        self.pending = {}
        self.reader_task = None
        self.tick_callbacks = {}

    async def connect(self):
        if self.ws and not self.ws.closed:
            return
        url = f"wss://ws.derivws.com/websockets/v3?app_id={settings.deriv_app_id}"
        self.ws = await websockets.connect(url, ping_interval=20, ping_timeout=20)
        self.reader_task = asyncio.create_task(self._reader())
        if self.token:
            await self.request({"authorize": self.token})

    async def _reader(self):
        async for raw in self.ws:
            data = json.loads(raw)
            req_id = data.get("req_id")
            if req_id in self.pending:
                fut = self.pending.pop(req_id)
                if not fut.done():
                    fut.set_result(data)
                continue
            msg_type = data.get("msg_type")
            if msg_type == "tick":
                sub = (data.get("subscription") or {}).get("id")
                cb = self.tick_callbacks.get(sub)
                if cb:
                    await cb(data)

    async def request(self, payload):
        await self.connect()
        self.req_id += 1
        rid = self.req_id
        payload = dict(payload)
        payload["req_id"] = rid
        loop = asyncio.get_running_loop()
        fut = loop.create_future()
        self.pending[rid] = fut
        await self.ws.send(json.dumps(payload))
        data = await asyncio.wait_for(fut, timeout=15)
        if data.get("error"):
            raise RuntimeError(data["error"].get("message") or "Deriv error")
        return data

    async def subscribe_ticks(self, symbol, callback):
        data = await self.request({"ticks": symbol, "subscribe": 1})
        sub_id = (data.get("subscription") or {}).get("id")
        if not sub_id:
            raise RuntimeError("No tick subscription id")
        self.tick_callbacks[sub_id] = callback
        # Initial tick is part of the subscription response.
        if data.get("tick"):
            await callback(data)
        return sub_id

    async def proposal_digitmatch(self, symbol, digit, stake, currency="USD"):
        data = await self.request({
            "proposal": 1,
            "amount": round(float(stake), 2),
            "basis": "stake",
            "contract_type": "DIGITMATCH",
            "currency": currency,
            "duration": 1,
            "duration_unit": "t",
            "symbol": symbol,
            "barrier": str(int(digit)),
        })
        p = data.get("proposal") or {}
        return {
            "id": p.get("id"),
            "ask_price": float(p.get("ask_price") or stake),
            "payout": float(p.get("payout") or 0),
            "spot": p.get("spot"),
        }

    async def buy(self, proposal_id, max_price):
        data = await self.request({
            "buy": proposal_id,
            "price": float(max_price),
        })
        return data.get("buy") or {}
