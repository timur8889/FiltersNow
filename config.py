import os

# Токен бота
BOT_TOKEN = os.getenv("BOT_TOKEN", "YOUR_BOT_TOKEN_HERE")

# ID канала (начинается с @ или -100)
CHANNEL_ID = os.getenv("CHANNEL_ID", "@your_channel_username")

# ID администраторов
ADMIN_IDS = list(map(int, os.getenv("ADMIN_IDS", "123456789").split(',')))

# Настройки базы данных
DATABASE_URL = os.getenv("DATABASE_URL", "channel_bot.db")
