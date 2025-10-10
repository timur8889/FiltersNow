import logging
import sqlite3
from datetime import datetime, timedelta
from aiogram import Bot, Dispatcher, types
from aiogram.contrib.fsm_storage.memory import MemoryStorage
from aiogram.dispatcher import FSMContext
from aiogram.dispatcher.filters.state import State, StatesGroup
from aiogram.utils import executor

# Настройки
API_TOKEN = '8278600298:AAGPjUhyU5HxXOaLRvu-FSRldBW_UCmwOME'
ADMIN_ID = 5024165375

# Инициализация бота
logging.basicConfig(level=logging.INFO)
bot = Bot(token=API_TOKEN)
storage = MemoryStorage()
dp = Dispatcher(bot, storage=storage)

# Инициализация БД
def init_db():
    conn = sqlite3.connect('filters.db')
    cur = conn.cursor()
    
    # Таблица фильтров пользователей
    cur.execute('''CREATE TABLE IF NOT EXISTS filters (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                filter_type TEXT,
                location TEXT,
                last_change DATE,
                expiry_date DATE,
                lifetime_days INTEGER)''')
    
    # Таблица стандартных сроков службы фильтров
    cur.execute('''CREATE TABLE IF NOT EXISTS filter_standards (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                filter_type TEXT UNIQUE,
                lifetime_days INTEGER,
                description TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)''')
    
    # Проверяем, есть ли уже стандартные значения
    cur.execute("SELECT COUNT(*) FROM filter_standards")
    count = cur.fetchone()[0]
    
    if count == 0:
        # Добавляем стандартные значения
        default_standards = [
            ("Магистральный SL10", 180, "Стандартный магистральный фильтр"),
            ("Магистральный SL20", 180, "Улучшенный магистральный фильтр"),
            ("Гейзер", 365, "Фильтр-кувшин Гейзер"),
            ("Аквафор", 365, "Фильтр-кувшин Аквафор")
        ]
        cur.executemany('''INSERT INTO filter_standards (filter_type, lifetime_days, description) 
                          VALUES (?, ?, ?)''', default_standards)
    
    # Создаем индексы для улучшения производительности
    cur.execute('''CREATE INDEX IF NOT EXISTS idx_filters_user_id ON filters(user_id)''')
    cur.execute('''CREATE INDEX IF NOT EXISTS idx_filters_expiry_date ON filters(expiry_date)''')
    cur.execute('''CREATE INDEX IF NOT EXISTS idx_standards_filter_type ON filter_standards(filter_type)''')
    
    conn.commit()
    conn.close()

# Определение срока службы по типу фильтра из базы данных
def get_lifetime_by_type(filter_type):
    conn = sqlite3.connect('filters.db')
    cur = conn.cursor()
    
    # Ищем точное совпадение
    cur.execute("SELECT lifetime_days FROM filter_standards WHERE filter_type = ?", (filter_type,))
    result = cur.fetchone()
    
    if result:
        conn.close()
        return result[0]
    
    # Ищем частичное совпадение (без учета регистра)
    cur.execute("SELECT filter_type, lifetime_days FROM filter_standards")
