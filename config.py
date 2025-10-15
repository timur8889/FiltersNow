import os
from dotenv import load_dotenv

load_dotenv()

TELEGRAM_BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
DEEPSEEK_API_KEY = os.getenv('DEEPSEEK_API_KEY')

if not TELEGRAM_BOT_TOKEN or not DEEPSEEK_API_KEY:
    raise ValueError("Необходимо установить TELEGRAM_BOT_TOKEN и DEEPSEEK_API_KEY в .env файле")
