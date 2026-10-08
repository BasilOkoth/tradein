import os
from dataclasses import dataclass

@dataclass
class Settings:
    deriv_app_id: str = os.getenv("DERIV_APP_ID", "1089")
    deriv_token: str = os.getenv("DERIV_TOKEN", "")
    default_symbol: str = os.getenv("DEFAULT_SYMBOL", "R_10")
    default_basket_stake: float = float(os.getenv("DEFAULT_BASKET_STAKE", "10"))
    telegram_bot_token: str = os.getenv("TELEGRAM_BOT_TOKEN", "")
    telegram_chat_id: str = os.getenv("TELEGRAM_CHAT_ID", "")
    allow_real_execution: bool = False  # hard-disabled in this teaching build

settings = Settings()
