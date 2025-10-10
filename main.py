import os
import logging
import sqlite3
from datetime import datetime, timedelta
from aiogram import Bot, Dispatcher, types
from aiogram.contrib.fsm_storage.memory import MemoryStorage
from aiogram.dispatcher import FSMContext
from aiogram.dispatcher.filters.state import State, StatesGroup
from aiogram.utils import executor

# Безопасное получение токена
API_TOKEN = os.getenv('BOT_TOKEN', 'your_fallback_token_here')  # Используйте переменные окружения!
ADMIN_IDS = [int(x) for x in os.getenv('ADMIN_IDS', '5024165375').split(',')]

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Безопасное подключение к БД
def get_db_connection():
    conn = sqlite3.connect('filters.db', check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute('''CREATE TABLE IF NOT EXISTS filters (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER,
                    filter_type TEXT,
                    location TEXT,
                    last_change DATE,
                    expiry_date DATE,
                    lifetime_days INTEGER,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)''')
        conn.commit()
    except sqlite3.Error as e:
        logger.error(f"Database initialization error: {e}")
    finally:
        if conn:
            conn.close()

# Валидация даты
def validate_date(date_string):
    try:
        date = datetime.strptime(date_string, '%Y-%m-%d').date()
        if date > datetime.now().date():
            return None, "Дата не может быть в будущем"
        return date, None
    except ValueError:
        return None, "Неверный формат даты. Используйте ГГГГ-ММ-ДД"

# Безопасные операции с БД
def safe_db_operation(operation, *args):
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        result = operation(cur, *args)
        conn.commit()
        return result, None
    except sqlite3.Error as e:
        logger.error(f"Database operation failed: {e}")
        return None, str(e)
    finally:
        if conn:
            conn.close()

# Ваши состояния и хендлеры остаются, но с улучшенной обработкой ошибок
