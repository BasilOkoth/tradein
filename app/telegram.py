import httpx
from .config import settings

async def send_telegram(text: str):
    token = settings.telegram_bot_token.strip()
    chat_id = settings.telegram_chat_id.strip()
    if not token or not chat_id:
        return {"sent": False, "reason": "not_configured"}

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    async with httpx.AsyncClient(timeout=10) as client:
        r = await client.post(url, json={
            "chat_id": chat_id,
            "text": text,
            "disable_web_page_preview": True,
        })
        r.raise_for_status()
        return {"sent": True}
