# config.py
import os
from typing import List

class Config:
    BOT_TOKEN = os.getenv("BOT_TOKEN", "8278600298:AAGPjUhyU5HxXOaLRvu-FSRldBW_UCmwOME")
    CHANNEL_ID = os.getenv("CHANNEL_ID", "@timur_onion")
    ADMIN_IDS = [int(x) for x in os.getenv("ADMIN_IDS", "5024165375").split(",")]
    DATABASE_URL = os.getenv("DATABASE_URL", "channel_bot.db")
