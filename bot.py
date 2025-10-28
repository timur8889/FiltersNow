import logging
import logging.config
import sqlite3
import os
import threading
import shutil
import traceback
import re
import sys
import json
import pandas as pd
import io
import time
import asyncio
from datetime import datetime, timedelta
from contextlib import contextmanager
from typing import Dict, List, Optional, Callable, Any, Union
from collections import OrderedDict
from aiogram import Bot, Dispatcher, types
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.context import FSMContext
from aiogram import F
from aiogram.utils.keyboard import ReplyKeyboardBuilder, InlineKeyboardBuilder
from aiogram.types import ReplyKeyboardRemove
from aiogram.filters import Command
from aiogram.client.default import DefaultBotProperties
from aiogram import BaseMiddleware
from dotenv import load_dotenv

# Предзагрузка модулей для ускорения
try:
    import gspread
    from google.oauth2.service_account import Credentials
    import psutil
except ImportError:
    pass

# Загрузка переменных окружения
try:
    load_dotenv()
except Exception as e:
    print(f"Ошибка загрузки .env файла: {e}")

# ========== УЛЬТРА-ОПТИМИЗИРОВАННАЯ КОНФИГУРАЦИЯ ==========
class UltraConfig:
    """Ультра-оптимизированная конфигурация"""
    
    def __init__(self):
        self.API_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
        if not self.API_TOKEN:
            raise ValueError("TELEGRAM_BOT_TOKEN не установен")
        
        self.ADMIN_ID = int(os.getenv('ADMIN_ID', '5024165375'))
        self.GOOGLE_SHEETS_CREDENTIALS = os.getenv('GOOGLE_SHEETS_CREDENTIALS')
        self.GOOGLE_SHEET_ID = os.getenv('GOOGLE_SHEET_ID')
        
        # Ультра-оптимизированные настройки БД
        self.DB_PATH = 'filters.db'
        self.BACKUP_ENABLED = True
        self.BACKUP_PATH = 'backups'
        
        # Агрессивные настройки производительности
        self.RATE_LIMIT_MAX_REQUESTS = 20  # Увеличено
        self.RATE_LIMIT_WINDOW = 30
        
        # Максимальная частота синхронизации
        self.REAL_TIME_SYNC_INTERVAL = 3  # 3 секунды!
        
        # Оптимизированные настройки кэша
        self.CACHE_TTL = 60  # 1 минута вместо 5
        
    def validate(self) -> bool:
        """Быстрая проверка конфигурации"""
        if not self.API_TOKEN:
            raise ValueError("TELEGRAM_BOT_TOKEN не установен")
        
        if self.BACKUP_ENABLED and not os.path.exists(self.BACKUP_PATH):
            os.makedirs(self.BACKUP_PATH)
            
        return True

config = UltraConfig()

# ========== УЛЬТРА-БЫСТРЫЙ КЭШ ==========
class UltraFastCache:
    """Ультра-оптимизированный кэш с фиксированным размером"""
    
    def __init__(self, max_size=2000):
        self._cache = {}
        self._timestamps = {}
        self._hits = 0
        self._misses = 0
        self._max_size = max_size
        self._lock = threading.Lock()
    
    def get(self, key):
        """Супер-быстрое получение из кэша"""
        with self._lock:
            if key in self._cache:
                data, timestamp, ttl = self._cache[key]
                if time.time() - timestamp < ttl:
                    self._hits += 1
                    self._timestamps[key] = time.time()
                    return data
            self._misses += 1
            return None
    
    def set(self, key, value, ttl=60):
        """Быстрая установка в кэш с автоматическим вытеснением"""
        with self._lock:
            if len(self._cache) >= self._max_size:
                self._evict_oldest()
            self._cache[key] = (value, time.time(), ttl)
            self._timestamps[key] = time.time()
    
    def _evict_oldest(self):
        """Вытеснение самых старых записей"""
        if not self._timestamps:
            return
        # Быстрый поиск 10 самых старых ключей
        oldest_keys = sorted(self._timestamps.keys(), 
                           key=lambda k: self._timestamps[k])[:10]
        for key in oldest_keys:
            self._cache.pop(key, None)
            self._timestamps.pop(key, None)
    
    def invalidate(self, key):
        """Мгновенная инвалидация"""
        with self._lock:
            self._cache.pop(key, None)
            self._timestamps.pop(key, None)
    
    def invalidate_pattern(self, pattern):
        """Инвалидация по шаблону"""
        with self._lock:
            keys_to_remove = [k for k in self._cache.keys() if pattern in k]
            for key in keys_to_remove:
                self._cache.pop(key, None)
                self._timestamps.pop(key, None)
    
    def clear(self):
        """Полная очистка кэша"""
        with self._lock:
            self._cache.clear()
            self._timestamps.clear()
    
    def get_stats(self):
        """Статистика кэша"""
        total = self._hits + self._misses
        return {
            'hits': self._hits,
            'misses': self._misses,
            'hit_rate': (self._hits / total * 100) if total > 0 else 0,
            'size': len(self._cache),
            'max_size': self._max_size
        }

# Глобальный ультра-кэш
ultra_cache = UltraFastCache(max_size=2000)

# ========== УЛЬТРА-ОПТИМИЗИРОВАННАЯ БАЗА ДАННЫХ ==========
class UltraDB:
    """Ультра-оптимизированное управление базой данных"""
    
    def __init__(self, db_path):
        self.db_path = db_path
        self._connections = threading.local()
        self._init_ultra_db()
    
    def _get_connection(self):
        """Получение соединения с оптимизированными настройками"""
        if not hasattr(self._connections, 'conn'):
            conn = sqlite3.connect(self.db_path, check_same_thread=False)
            conn.row_factory = sqlite3.Row
            
            # АГРЕССИВНЫЕ ОПТИМИЗАЦИИ SQLite
            conn.execute('PRAGMA journal_mode=WAL')  # Write-Ahead Logging
            conn.execute('PRAGMA synchronous=NORMAL')  # Баланс скорости/надежности
            conn.execute('PRAGMA cache_size=10000')   # Увеличенный кэш
            conn.execute('PRAGMA temp_store=MEMORY')  # Временные таблицы в памяти
            conn.execute('PRAGMA mmap_size=268435456')  # 256MB mmap
            conn.execute('PRAGMA optimize')  # Авто-оптимизация
            
            self._connections.conn = conn
        return self._connections.conn
    
    def _init_ultra_db(self):
        """Инициализация ультра-оптимизированной БД"""
        conn = self._get_connection()
        cursor = conn.cursor()
        
        # Оптимизированная таблица
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS filters (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                filter_type TEXT NOT NULL,
                location TEXT NOT NULL,
                last_change DATE NOT NULL,
                expiry_date DATE NOT NULL,
                lifetime_days INTEGER NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        # Минимально необходимые индексы
        cursor.execute('''
            CREATE INDEX IF NOT EXISTS idx_ultra_user_expiry 
            ON filters(user_id, expiry_date)
        ''')
        cursor.execute('''
            CREATE INDEX IF NOT EXISTS idx_ultra_expiry 
            ON filters(expiry_date)
        ''')
        
        conn.commit()
    
    def execute(self, query, params=()):
        """Быстрое выполнение запроса"""
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute(query, params)
        conn.commit()
        return cursor
    
    def fetch_all(self, query, params=()):
        """Быстрое получение всех строк"""
        cursor = self.execute(query, params)
        return [dict(row) for row in cursor.fetchall()]
    
    def fetch_one(self, query, params=()):
        """Быстрое получение одной строки"""
        cursor = self.execute(query, params)
        result = cursor.fetchone()
        return dict(result) if result else None

# Инициализация ультра-БД
ultra_db = UltraDB(config.DB_PATH)

# ========== ОПТИМИЗИРОВАННЫЕ ФУНКЦИИ БАЗЫ ДАННЫХ ==========
def get_user_filters_ultra(user_id: int) -> List[Dict]:
    """Ультра-быстрое получение фильтров пользователя"""
    cache_key = f"filters_{user_id}"
    cached = ultra_cache.get(cache_key)
    if cached is not None:
        return cached
    
    # Быстрый запрос к БД
    filters = ultra_db.fetch_all(
        "SELECT * FROM filters WHERE user_id = ? ORDER BY expiry_date", 
        (user_id,)
    )
    
    # Кэшируем на короткое время
    ultra_cache.set(cache_key, filters, ttl=config.CACHE_TTL)
    return filters

def add_filter_ultra(user_id: int, filter_data: Dict) -> bool:
    """Ультра-быстрое добавление фильтра"""
    try:
        ultra_db.execute('''
            INSERT INTO filters 
            (user_id, filter_type, location, last_change, expiry_date, lifetime_days) 
            VALUES (?, ?, ?, ?, ?, ?)
        ''', (
            user_id, 
            filter_data['filter_type'], 
            filter_data['location'],
            filter_data['last_change'], 
            filter_data['expiry_date'], 
            filter_data['lifetime_days']
        ))
        
        # МГНОВЕННАЯ инвалидация кэша
        ultra_cache.invalidate(f"filters_{user_id}")
        ultra_cache.invalidate(f"stats_{user_id}")
        ultra_cache.invalidate_pattern("global_stats")
        
        return True
    except Exception as e:
        logging.error(f"Ошибка добавления фильтра: {e}")
        return False

def update_filter_ultra(filter_id: int, user_id: int, **kwargs) -> bool:
    """Ультра-быстрое обновление фильтра"""
    if not kwargs:
        return False
    
    try:
        set_clause = ", ".join([f"{key} = ?" for key in kwargs.keys()])
        values = list(kwargs.values()) + [filter_id, user_id]
        
        ultra_db.execute(
            f"UPDATE filters SET {set_clause} WHERE id = ? AND user_id = ?",
            values
        )
        
        # МГНОВЕННАЯ инвалидация кэша
        ultra_cache.invalidate(f"filters_{user_id}")
        ultra_cache.invalidate(f"stats_{user_id}")
        
        return True
    except Exception as e:
        logging.error(f"Ошибка обновления фильтра: {e}")
        return False

def delete_filter_ultra(filter_id: int, user_id: int) -> bool:
    """Ультра-быстрое удаление фильтра"""
    try:
        ultra_db.execute(
            "DELETE FROM filters WHERE id = ? AND user_id = ?",
            (filter_id, user_id)
        )
        
        # МГНОВЕННАЯ инвалидация кэша
        ultra_cache.invalidate(f"filters_{user_id}")
        ultra_cache.invalidate(f"stats_{user_id}")
        ultra_cache.invalidate_pattern("global_stats")
        
        return True
    except Exception as e:
        logging.error(f"Ошибка удаления фильтра: {e}")
        return False

def get_all_users_stats_ultra() -> Dict:
    """Ультра-быстрое получение статистики"""
    cache_key = "global_stats"
    cached = ultra_cache.get(cache_key)
    if cached is not None:
        return cached
    
    stats = ultra_db.fetch_one('''
        SELECT 
            COUNT(DISTINCT user_id) as total_users,
            COUNT(*) as total_filters,
            SUM(CASE WHEN expiry_date <= date('now') THEN 1 ELSE 0 END) as expired_filters,
            SUM(CASE WHEN expiry_date BETWEEN date('now') AND date('now', '+7 days') THEN 1 ELSE 0 END) as expiring_soon
        FROM filters
    ''')
    
    result = stats or {'total_users': 0, 'total_filters': 0, 'expired_filters': 0, 'expiring_soon': 0}
    ultra_cache.set(cache_key, result, ttl=300)  # 5 минут для глобальной статистики
    return result

# ========== ОПТИМИЗИРОВАННЫЙ МЕНЕДЖЕР КЭША ==========
class UltraCacheManager:
    """Ультра-оптимизированный менеджер кэширования"""
    
    def __init__(self):
        self.user_stats_cache = {}
    
    def get_user_stats(self, user_id: int):
        """Быстрое получение статистики пользователя"""
        cache_key = f"stats_{user_id}"
        cached = ultra_cache.get(cache_key)
        if cached is not None:
            return cached
        
        filters = get_user_filters_ultra(user_id)
        stats = self._calculate_user_stats_fast(filters)
        
        ultra_cache.set(cache_key, stats, ttl=120)  # 2 минуты для статистики
        return stats
    
    def _calculate_user_stats_fast(self, filters: List[Dict]) -> Dict:
        """Супер-быстрый расчет статистики"""
        today = datetime.now().date()
        stats = {
            'total': len(filters),
            'expired': 0,
            'expiring_soon': 0,
            'normal': 0,
        }
        
        for f in filters:
            expiry_date = datetime.strptime(str(f['expiry_date']), '%Y-%m-%d').date()
            days_until = (expiry_date - today).days
            
            if days_until <= 0:
                stats['expired'] += 1
            elif days_until <= 7:
                stats['expiring_soon'] += 1
            else:
                stats['normal'] += 1
        
        if stats['total'] > 0:
            stats['health_percentage'] = (stats['normal'] / stats['total']) * 100
        else:
            stats['health_percentage'] = 0
            
        return stats
    
    def invalidate_user_cache(self, user_id: int):
        """Мгновенная инвалидация кэша пользователя"""
        ultra_cache.invalidate(f"filters_{user_id}")
        ultra_cache.invalidate(f"stats_{user_id}")

# Создаем экземпляр ультра-менеджера
ultra_cache_manager = UltraCacheManager()

# ========== ОПТИМИЗИРОВАННАЯ СИНХРОНИЗАЦИЯ GOOGLE SHEETS ==========
class UltraGoogleSync:
    """Ультра-оптимизированная синхронизация с Google Sheets"""
    
    def __init__(self):
        self.sheet_id = None
        self.auto_sync = False
        self.credentials = None
        self._client = None
        self._last_sync = {}
        self._load_settings()
    
    def _load_settings(self):
        """Быстрая загрузка настроек"""
        try:
            if os.path.exists('sheets_settings.json'):
                with open('sheets_settings.json', 'r', encoding='utf-8') as f:
                    settings = json.load(f)
                    self.sheet_id = settings.get('sheet_id')
                    self.auto_sync = settings.get('auto_sync', False)
        except Exception:
            pass
    
    def _get_client(self):
        """Быстрая инициализация клиента"""
        if self._client is None and config.GOOGLE_SHEETS_CREDENTIALS:
            try:
                credentials_info = json.loads(config.GOOGLE_SHEETS_CREDENTIALS)
                scope = ['https://spreadsheets.google.com/feeds', 
                        'https://www.googleapis.com/auth/drive']
                self.credentials = Credentials.from_service_account_info(credentials_info, scopes=scope)
                self._client = gspread.authorize(self.credentials)
            except Exception as e:
                logging.error(f"Ошибка инициализации Google API: {e}")
        return self._client
    
    def is_configured(self) -> bool:
        """Быстрая проверка конфигурации"""
        return bool(self.sheet_id and config.GOOGLE_SHEETS_CREDENTIALS)
    
    def sync_user_ultra(self, user_id: int) -> tuple[bool, str]:
        """Ультра-быстрая синхронизация пользователя"""
        if not self.auto_sync or not self.is_configured():
            return False, "Синхронизация отключена"
        
        # Проверяем необходимость синхронизации
        now = time.time()
        last_sync = self._last_sync.get(user_id, 0)
        if now - last_sync < config.REAL_TIME_SYNC_INTERVAL:
            return False, "Слишком рано для синхронизации"
        
        try:
            filters = get_user_filters_ultra(user_id)
            if not filters:
                return True, "Нет данных для синхронизации"
            
            client = self._get_client()
            if not client:
                return False, "Ошибка клиента Google"
            
            sheet = client.open_by_key(self.sheet_id)
            worksheet_name = f"User_{user_id}"
            
            try:
                worksheet = sheet.worksheet(worksheet_name)
                # Быстрая очистка
                worksheet.clear()
            except gspread.exceptions.WorksheetNotFound:
                worksheet = sheet.add_worksheet(title=worksheet_name, rows=100, cols=10)
            
            # СУПЕР-БЫСТРАЯ подготовка данных
            headers = ['ID', 'Тип фильтра', 'Местоположение', 'Дата замены', 'Годен до', 'Статус']
            data = [headers]
            
            today = datetime.now().date()
            for f in filters:
                expiry_date = datetime.strptime(str(f['expiry_date']), '%Y-%m-%d').date()
                days_until = (expiry_date - today).days
                status = "🔴 ПРОСРОЧЕН" if days_until <= 0 else "🟡 СКОРО" if days_until <= 7 else "🟢 АКТИВЕН"
                
                data.append([
                    f['id'], 
                    f['filter_type'], 
                    f['location'],
                    f['last_change'], 
                    f['expiry_date'], 
                    status
                ])
            
            # Быстрое обновление одним запросом
            worksheet.update(data, 'A1')
            
            self._last_sync[user_id] = now
            return True, f"Синхронизировано {len(filters)} фильтров"
            
        except Exception as e:
            error_msg = str(e)
            logging.error(f"Ошибка синхронизации: {error_msg}")
            return False, f"Ошибка: {error_msg}"
    
    def should_sync_user(self, user_id: int) -> bool:
        """Проверка необходимости синхронизации"""
        if not self.auto_sync or not self.is_configured():
            return False
        
        last_sync = self._last_sync.get(user_id)
        if not last_sync:
            return True
        
        time_since_last_sync = time.time() - last_sync
        return time_since_last_sync >= config.REAL_TIME_SYNC_INTERVAL

# Создаем экземпляр ультра-синхронизации
ultra_google_sync = UltraGoogleSync()

# ========== ОПТИМИЗИРОВАННЫЕ КОМПОНЕНТЫ AIOGRAM ==========
bot = Bot(
    token=config.API_TOKEN,
    default=DefaultBotProperties(parse_mode='HTML')
)
storage = MemoryStorage()
dp = Dispatcher(storage=storage)

# ========== ОПТИМИЗИРОВАННЫЕ КЛАВИАТУРЫ (КЭШИРОВАННЫЕ) ==========
_keyboard_cache = {}

def get_cached_keyboard(name, builder_func):
    """Кэширование клавиатур для мгновенного доступа"""
    if name not in _keyboard_cache:
        _keyboard_cache[name] = builder_func()
    return _keyboard_cache[name]

def create_main_kb():
    builder = ReplyKeyboardBuilder()
    builder.button(text="📋 Мои фильтры")
    builder.button(text="✨ Добавить фильтр")
    builder.button(text="⚙️ Управление")
    builder.button(text="📊 Статистика")
    builder.adjust(2)
    return builder.as_markup(resize_keyboard=True)

def create_back_kb():
    builder = ReplyKeyboardBuilder()
    builder.button(text="🔙 Назад")
    return builder.as_markup(resize_keyboard=True)

# Предварительное создание клавиатур при запуске
_keyboard_cache['main'] = create_main_kb()
_keyboard_cache['back'] = create_back_kb()

# ========== ОПТИМИЗИРОВАННЫЕ STATES ==========
class UltraStates(StatesGroup):
    waiting_filter_type = State()
    waiting_location = State()
    waiting_change_date = State()
    waiting_lifetime = State()

# ========== УЛЬТРА-ОПТИМИЗИРОВАННЫЕ ОБРАБОТЧИКИ ==========

@dp.message(Command("start"))
async def ultra_start(message: types.Message, state: FSMContext):
    """Ультра-оптимизированный старт"""
    await state.clear()
    
    sync_status = ""
    if ultra_google_sync.auto_sync and ultra_google_sync.is_configured():
        sync_status = "\n\n🔄 <b>УЛЬТРА-СИНХРОНИЗАЦИЯ АКТИВНА</b>\nДанные обновляются каждые 3 секунды!"
    
    await message.answer(
        "🏭 <b>Завод «Контакт»</b>\n"
        "🌟 <b>ФИЛЬТР-ТРЕКЕР ULTRA</b> ⚡\n\n"
        "<i>Самая быстрая система учета фильтров</i>"
        f"{sync_status}",
        reply_markup=_keyboard_cache['main'],
        parse_mode='HTML'
    )

@dp.message(F.text == "📋 Мои фильтры")
async def ultra_my_filters(message: types.Message):
    """Ультра-быстрое отображение фильтров"""
    user_id = message.from_user.id
    filters = get_user_filters_ultra(user_id)
    
    if not filters:
        await message.answer(
            "📭 <b>У вас пока нет фильтров</b>\n\n"
            "Добавьте первый фильтр через меню '✨ Добавить фильтр'",
            reply_markup=_keyboard_cache['main'],
            parse_mode='HTML'
        )
        return
    
    # СУПЕР-БЫСТРОЕ форматирование
    today = datetime.now().date()
    lines = ["📋 <b>ВАШИ ФИЛЬТРЫ:</b>\n"]
    
    for f in filters[:15]:  # Ограничиваем вывод для скорости
        expiry_date = datetime.strptime(str(f['expiry_date']), '%Y-%m-%d').date()
        days_until = (expiry_date - today).days
        
        if days_until <= 0:
            icon, status = "🔴", "ПРОСРОЧЕН"
        elif days_until <= 7:
            icon, status = "🟡", "СКОРО"
        else:
            icon, status = "🟢", "НОРМА"
        
        lines.append(
            f"{icon} <b>#{f['id']}</b> {f['filter_type']}\n"
            f"📍 {f['location']} | ⏰ {expiry_date.strftime('%d.%m.%Y')} | {status}\n"
        )
    
    if len(filters) > 15:
        lines.append(f"\n... и еще {len(filters) - 15} фильтров")
    
    # Статистика
    stats = ultra_cache_manager.get_user_stats(user_id)
    lines.append(f"\n📊 <b>СТАТИСТИКА:</b> 🟢{stats['normal']} 🟡{stats['expiring_soon']} 🔴{stats['expired']}")
    
    await message.answer("\n".join(lines), parse_mode='HTML')

@dp.message(F.text == "✨ Добавить фильтр")
async def ultra_add_filter_start(message: types.Message, state: FSMContext):
    """Ультра-оптимизированное начало добавления фильтра"""
    await state.clear()
    await state.set_state(UltraStates.waiting_filter_type)
    await message.answer(
        "💧 <b>ДОБАВЛЕНИЕ ФИЛЬТРА</b>\n\n"
        "Введите тип фильтра:",
        reply_markup=_keyboard_cache['back'],
        parse_mode='HTML'
    )

@dp.message(UltraStates.waiting_filter_type)
async def ultra_process_type(message: types.Message, state: FSMContext):
    """Быстрая обработка типа фильтра"""
    if message.text == "🔙 Назад":
        await state.clear()
        await message.answer("🔙 Главное меню", reply_markup=_keyboard_cache['main'])
        return
    
    await state.update_data(filter_type=message.text.strip())
    await state.set_state(UltraStates.waiting_location)
    await message.answer(
        "📍 <b>Введите местоположение:</b>\n"
        "Например: Кухня, Ванная, Офис",
        reply_markup=_keyboard_cache['back'],
        parse_mode='HTML'
    )

@dp.message(UltraStates.waiting_location)  
async def ultra_process_location(message: types.Message, state: FSMContext):
    """Быстрая обработка местоположения"""
    if message.text == "🔙 Назад":
        await state.set_state(UltraStates.waiting_filter_type)
        await message.answer("💧 Введите тип фильтра:", reply_markup=_keyboard_cache['back'])
        return
    
    await state.update_data(location=message.text.strip())
    await state.set_state(UltraStates.waiting_change_date)
    await message.answer(
        "📅 <b>Введите дату последней замены:</b>\n"
        "Формат: ДД.ММ.ГГГГ или ДД.ММ\n"
        "Пример: 15.12.2023 или 15.12",
        reply_markup=_keyboard_cache['back'],
        parse_mode='HTML'
    )

@dp.message(UltraStates.waiting_change_date)
async def ultra_process_date(message: types.Message, state: FSMContext):
    """Ультра-быстрая обработка даты"""
    if message.text == "🔙 Назад":
        await state.set_state(UltraStates.waiting_location)
        await message.answer("📍 Введите местоположение:", reply_markup=_keyboard_cache['back'])
        return
    
    try:
        # Упрощенная и быстрая валидация даты
        date_str = message.text.strip().replace('/', '.').replace('-', '.')
        parts = date_str.split('.')
        
        if len(parts) == 2:  # ДД.ММ
            day, month = map(int, parts)
            year = datetime.now().year
        elif len(parts) == 3:  # ДД.ММ.ГГ или ДД.ММ.ГГГГ
            day, month, year = map(int, parts)
            if year < 100:  # ГГ формате
                year += 2000
        else:
            raise ValueError("Неверный формат")
        
        change_date = datetime(year, month, day).date()
        
        # Проверка разумности даты
        today = datetime.now().date()
        if change_date > today:
            raise ValueError("Дата не может быть в будущем")
        
        await state.update_data(change_date=change_date.strftime('%Y-%m-%d'))
        await state.set_state(UltraStates.waiting_lifetime)
        await message.answer(
            "⏱️ <b>Введите срок службы в днях:</b>\n"
            "Пример: 180 (6 месяцев) или 365 (1 год)",
            reply_markup=_keyboard_cache['back'],
            parse_mode='HTML'
        )
        
    except Exception as e:
        await message.answer(
            f"❌ <b>Ошибка:</b> {str(e)}\n\n"
            "📅 <b>Введите дату еще раз:</b>",
            reply_markup=_keyboard_cache['back'],
            parse_mode='HTML'
        )

@dp.message(UltraStates.waiting_lifetime)
async def ultra_process_lifetime(message: types.Message, state: FSMContext):
    """Финальная обработка и ультра-быстрое сохранение"""
    if message.text == "🔙 Назад":
        await state.set_state(UltraStates.waiting_change_date)
        await message.answer("📅 Введите дату замены:", reply_markup=_keyboard_cache['back'])
        return
    
    try:
        lifetime = int(message.text.strip())
        if lifetime <= 0 or lifetime > 3650:
            raise ValueError("Срок службы должен быть от 1 до 3650 дней")
        
        data = await state.get_data()
        
        # Быстрый расчет даты истечения
        change_date = datetime.strptime(data['change_date'], '%Y-%m-%d').date()
        expiry_date = change_date + timedelta(days=lifetime)
        
        # УЛЬТРА-БЫСТРОЕ сохранение
        success = add_filter_ultra(message.from_user.id, {
            'filter_type': data['filter_type'],
            'location': data['location'], 
            'last_change': data['change_date'],
            'expiry_date': expiry_date.strftime('%Y-%m-%d'),
            'lifetime_days': lifetime
        })
        
        if success:
            # Мгновенная синхронизация если включена
            if ultra_google_sync.auto_sync:
                ultra_google_sync.sync_user_ultra(message.from_user.id)
            
            await message.answer(
                f"✅ <b>ФИЛЬТР ДОБАВЛЕН!</b>\n\n"
                f"💧 {data['filter_type']}\n"
                f"📍 {data['location']}\n" 
                f"📅 Годен до: {expiry_date.strftime('%d.%m.%Y')}\n\n"
                f"⚡ <i>Данные синхронизированы в реальном времени</i>",
                reply_markup=_keyboard_cache['main'],
                parse_mode='HTML'
            )
        else:
            await message.answer(
                "❌ <b>Ошибка при сохранении</b>\n\n"
                "Попробуйте еще раз",
                reply_markup=_keyboard_cache['main'],
                parse_mode='HTML'
            )
            
    except ValueError as e:
        await message.answer(
            f"❌ <b>Ошибка:</b> {str(e)}\n\n"
            "⏱️ <b>Введите срок службы еще раз:</b>",
            reply_markup=_keyboard_cache['back'],
            parse_mode='HTML'
        )
        return
    
    await state.clear()

@dp.message(F.text == "📊 Статистика")
async def ultra_statistics(message: types.Message):
    """Ультра-быстрая статистика"""
    user_id = message.from_user.id
    stats = ultra_cache_manager.get_user_stats(user_id)
    global_stats = get_all_users_stats_ultra()
    
    stats_text = f"""
📊 <b>УЛЬТРА-СТАТИСТИКА</b>

💧 <b>Ваши показатели:</b>
• Всего фильтров: {stats['total']}
• 🟢 В норме: {stats['normal']}
• 🟡 Скоро истекают: {stats['expiring_soon']}  
• 🔴 Просрочено: {stats['expired']}
• 📈 Здоровье системы: {stats['health_percentage']:.1f}%

🌐 <b>Общая статистика:</b>
• 👥 Пользователей: {global_stats['total_users']}
• 💧 Всего фильтров: {global_stats['total_filters']}
• ⚠️ Требуют внимания: {global_stats['expired_filters'] + global_stats['expiring_soon']}

⚡ <i>Обновлено в реальном времени</i>
    """
    
    await message.answer(stats_text, parse_mode='HTML')

@dp.message(F.text == "🔙 Назад")
async def ultra_back(message: types.Message, state: FSMContext):
    """Универсальный обработчик назад"""
    await state.clear()
    await message.answer("🔙 Главное меню", reply_markup=_keyboard_cache['main'])

# ========== УЛЬТРА-АГРЕССИВНАЯ ФОНОВАЯ СИНХРОНИЗАЦИЯ ==========
def ultra_sync_task():
    """Ультра-агрессивная фоновая синхронизация каждые 3 секунды"""
    logging.info("🚀 ЗАПУСК УЛЬТРА-СИНХРОНИЗАЦИИ (3 СЕКУНДЫ)")
    
    while True:
        try:
            if ultra_google_sync.auto_sync and ultra_google_sync.is_configured():
                # Быстрое получение всех пользователей
                users = ultra_db.fetch_all("SELECT DISTINCT user_id FROM filters")
                synced_count = 0
                
                for user_row in users:
                    user_id = user_row['user_id']
                    if ultra_google_sync.should_sync_user(user_id):
                        success, message = ultra_google_sync.sync_user_ultra(user_id)
                        if success:
                            synced_count += 1
                
                # Логируем каждые 10 циклов
                if hasattr(ultra_sync_task, 'cycle_count'):
                    ultra_sync_task.cycle_count += 1
                else:
                    ultra_sync_task.cycle_count = 1
                
                if ultra_sync_task.cycle_count % 10 == 0 and synced_count > 0:
                    logging.info(f"⚡ Ультра-синхронизация: {synced_count} пользователей")
            
            time.sleep(config.REAL_TIME_SYNC_INTERVAL)
            
        except Exception as e:
            logging.error(f"❌ Ошибка ультра-синхронизации: {e}")
            time.sleep(5)

# ========== ОПТИМИЗИРОВАННЫЙ ЗАПУСК ==========
def setup_ultra_logging():
    """Оптимизированная настройка логирования"""
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(levelname)s - %(message)s',
        handlers=[
            logging.StreamHandler(),
            logging.handlers.RotatingFileHandler(
                'bot_ultra.log', 
                maxBytes=5*1024*1024,  # 5MB
                backupCount=3
            )
        ]
    )

async def ultra_main():
    """Ультра-оптимизированный запуск"""
    try:
        # Быстрая настройка
        setup_ultra_logging()
        config.validate()
        
        # Запуск ультра-синхронизации в отдельном потоке
        sync_thread = threading.Thread(target=ultra_sync_task, daemon=True)
        sync_thread.start()
        
        logging.info("🚀 УЛЬТРА-ОПТИМИЗИРОВАННЫЙ БОТ ЗАПУЩЕН!")
        logging.info(f"⚡ Синхронизация: {config.REAL_TIME_SYNC_INTERVAL} секунд")
        logging.info(f"💾 Кэш: {ultra_cache.get_stats()}")
        
        # Запуск бота
        await dp.start_polling(bot)
        
    except Exception as e:
        logging.critical(f"💥 Критическая ошибка: {e}")
        raise

if __name__ == "__main__":
    try:
        asyncio.run(ultra_main())
    except KeyboardInterrupt:
        logging.info("⏹️ Бот остановлен")
    except Exception as e:
        logging.critical(f"💥 Фатальная ошибка: {e}")
        sys.exit(1)
