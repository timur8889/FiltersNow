import logging
import logging.config
import sqlite3
import os
import asyncio
import shutil
import traceback
import re
import sys
import aiosqlite
import json
import pandas as pd
import io
from datetime import datetime, timedelta
from contextlib import asynccontextmanager
from typing import Dict, List, Optional, Callable, Any, Awaitable, Union
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

# Загрузка переменных окружения
load_dotenv()

# Настройки
API_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
ADMIN_ID = int(os.getenv('ADMIN_ID', '5024165375'))

# Проверка обязательных переменных
if not API_TOKEN:
    logging.error("Токен бота не найден! Установите переменную TELEGRAM_BOT_TOKEN")
    exit(1)

# ========== УЛУЧШЕНИЕ: РАСШИРЕННОЕ ЛОГИРОВАНИЕ ==========
def setup_logging():
    """Расширенная настройка логирования"""
    logging.config.dictConfig({
        'version': 1,
        'disable_existing_loggers': False,
        'formatters': {
            'detailed': {
                'format': '%(asctime)s - %(name)s - %(levelname)s - %(message)s - [%(filename)s:%(lineno)d]'
            },
            'simple': {
                'format': '%(levelname)s - %(message)s'
            }
        },
        'handlers': {
            'file': {
                'class': 'logging.handlers.RotatingFileHandler',
                'filename': 'bot.log',
                'maxBytes': 10*1024*1024,  # 10MB
                'backupCount': 5,
                'formatter': 'detailed',
                'encoding': 'utf-8'
            },
            'console': {
                'class': 'logging.StreamHandler',
                'formatter': 'simple'
            }
        },
        'loggers': {
            '': {
                'handlers': ['file', 'console'],
                'level': 'INFO'
            }
        }
    })

setup_logging()

# ========== ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ==========
def get_main_keyboard():
    """Клавиатура главного меню"""
    builder = ReplyKeyboardBuilder()
    builder.button(text="📋 Мои фильтры")
    builder.button(text="✨ Добавить фильтр")
    builder.button(text="⚙️ Управление фильтрами")
    builder.button(text="📊 Статистика")
    builder.button(text="📤 Импорт/Экспорт")
    builder.adjust(2)
    return builder.as_markup(resize_keyboard=True)

def get_add_filter_keyboard():
    """Клавиатура для добавления фильтров"""
    builder = ReplyKeyboardBuilder()
    builder.button(text="➕ Один фильтр")
    builder.button(text="🔙 Назад")
    builder.adjust(2)
    return builder.as_markup(resize_keyboard=True)

def get_filter_type_keyboard():
    """Клавиатура для выбора типа фильтра"""
    builder = ReplyKeyboardBuilder()
    builder.button(text="Магистральный SL10")
    builder.button(text="Магистральный SL20")
    builder.button(text="Гейзер")
    builder.button(text="Аквафор")
    builder.button(text="Пурифайер")
    builder.button(text="Другой тип фильтра")
    builder.button(text="🔙 Назад")
    builder.adjust(2)
    return builder.as_markup(resize_keyboard=True)

def get_confirmation_keyboard():
    """Клавиатура подтверждения"""
    builder = ReplyKeyboardBuilder()
    builder.button(text="✅ Да, всё верно")
    builder.button(text="❌ Нет, изменить")
    builder.adjust(2)
    return builder.as_markup(resize_keyboard=True)

def get_management_keyboard():
    """Клавиатура управления"""
    builder = ReplyKeyboardBuilder()
    builder.button(text="✏️ Редактировать фильтр")
    builder.button(text="🗑️ Удалить фильтр")
    builder.button(text="📊 Онлайн Excel")
    builder.button(text="🔙 Назад")
    builder.adjust(2)
    return builder.as_markup(resize_keyboard=True)

def get_import_export_keyboard():
    """Клавиатура импорта/экспорта"""
    builder = ReplyKeyboardBuilder()
    builder.button(text="📤 Экспорт в Excel")
    builder.button(text="📥 Импорт из Excel")
    builder.button(text="📋 Шаблон Excel")
    builder.button(text="🔙 Назад")
    builder.adjust(2)
    return builder.as_markup(resize_keyboard=True)

def get_back_keyboard():
    """Клавиатура с кнопкой Назад"""
    builder = ReplyKeyboardBuilder()
    builder.button(text="🔙 Назад")
    return builder.as_markup(resize_keyboard=True)

def get_cancel_keyboard():
    """Клавиатура отмены"""
    builder = ReplyKeyboardBuilder()
    builder.button(text="❌ Отмена")
    return builder.as_markup(resize_keyboard=True)

def get_edit_keyboard():
    """Клавиатура для редактирования"""
    builder = ReplyKeyboardBuilder()
    builder.button(text="💧 Тип фильтра")
    builder.button(text="📍 Местоположение")
    builder.button(text="📅 Дата замены")
    builder.button(text="⏱️ Срок службы")
    builder.button(text="🔙 Назад")
    builder.adjust(2)
    return builder.as_markup(resize_keyboard=True)

def get_status_icon_and_text(days_until_expiry: int):
    """Получение иконки и текста статуса"""
    if days_until_expiry <= 0:
        return "🔴", "ПРОСРОЧЕН"
    elif days_until_expiry <= 7:
        return "🟡", "СКОРО ИСТЕЧЕТ"
    elif days_until_expiry <= 30:
        return "🟠", "ВНИМАНИЕ"
    else:
        return "🟢", "НОРМА"

def format_date_nice(date):
    """Красивое форматирование даты"""
    return date.strftime("%d.%m.%Y")

def create_expiry_infographic(filters):
    """Создание инфографики по срокам"""
    today = datetime.now().date()
    expired = 0
    expiring_soon = 0
    normal = 0
    
    for f in filters:
        expiry_date = datetime.strptime(str(f['expiry_date']), '%Y-%m-%d').date()
        days_until = (expiry_date - today).days
        
        if days_until <= 0:
            expired += 1
        elif days_until <= 7:
            expiring_soon += 1
        else:
            normal += 1
    
    return (
        f"📊 <b>СТАТУС ФИЛЬТРОВ:</b>\n"
        f"🟢 Норма: {normal}\n"
        f"🟡 Скоро истечет: {expiring_soon}\n"
        f"🔴 Просрочено: {expired}"
    )

def is_admin(user_id: int) -> bool:
    """Проверка прав администратора"""
    return user_id == ADMIN_ID

def backup_database() -> bool:
    """Создание резервной копии базы данных"""
    try:
        if os.path.exists('filters.db'):
            backup_name = f'filters_backup_{datetime.now().strftime("%Y%m%d_%H%M%S")}.db'
            shutil.copy2('filters.db', backup_name)
            return True
    except Exception as e:
        logging.error(f"Ошибка при создании резервной копии: {e}")
    return False

# ========== УЛУЧШЕНИЕ: ВАЛИДАЦИЯ ПОЛЬЗОВАТЕЛЬСКОГО ВВОДА ==========
def validate_filter_type(filter_type: str) -> tuple[bool, str]:
    """Валидация типа фильтра"""
    if not filter_type or len(filter_type.strip()) == 0:
        return False, "Тип фильтра не может быть пустым"
    
    if len(filter_type) > 100:
        return False, "Тип фильтра слишком длинный (макс. 100 символов)"
    
    # Проверка на запрещенные символы
    if re.search(r'[<>{}[\]]', filter_type):
        return False, "Тип фильтра содержит запрещенные символы"
    
    return True, "OK"

def validate_location(location: str) -> tuple[bool, str]:
    """Валидация местоположения"""
    if not location or len(location.strip()) == 0:
        return False, "Местоположение не может быть пустым"
    
    if len(location) > 50:
        return False, "Местоположение слишком длинное (макс. 50 символов)"
    
    if re.search(r'[<>{}[\]]', location):
        return False, "Местоположение содержит запрещенные символы"
    
    return True, "OK"

def validate_lifetime(lifetime: str) -> tuple[bool, str, int]:
    """Валидация срока службы"""
    try:
        days = int(lifetime)
        if days <= 0:
            return False, "Срок службы должен быть положительным числом", 0
        if days > 3650:  # 10 лет
            return False, "Срок службы не может превышать 10 лет", 0
        return True, "OK", days
    except ValueError:
        return False, "Срок службы должен быть числом (дни)", 0

# ========== УЛУЧШЕНИЕ: МОНИТОРИНГ ЗДОРОВЬЯ ==========
class BotHealthMonitor:
    def __init__(self):
        self.start_time = datetime.now()
        self.message_count = 0
        self.error_count = 0
        self.user_actions = {}
    
    def record_message(self, user_id: int):
        """Запись сообщения пользователя"""
        self.message_count += 1
        if user_id not in self.user_actions:
            self.user_actions[user_id] = 0
        self.user_actions[user_id] += 1
    
    def record_error(self):
        """Запись ошибки"""
        self.error_count += 1
    
    async def get_health_status(self):
        """Получение статуса здоровья бота"""
        uptime = datetime.now() - self.start_time
        active_users = len([uid for uid, count in self.user_actions.items() if count > 0])
        
        health_score = (self.message_count - self.error_count) / max(1, self.message_count) * 100
        
        return {
            'uptime': str(uptime),
            'message_count': self.message_count,
            'error_count': self.error_count,
            'active_users': active_users,
            'health_score': health_score
        }

health_monitor = BotHealthMonitor()

# ========== УЛУЧШЕНИЕ: RATE LIMITING ==========
class RateLimiter:
    def __init__(self, max_requests: int = 5, window: int = 30):
        self.max_requests = max_requests
        self.window = window
        self.user_requests = {}
    
    def is_allowed(self, user_id: int) -> bool:
        """Проверка разрешения на обработку запроса"""
        now = datetime.now()
        if user_id not in self.user_requests:
            self.user_requests[user_id] = []
        
        # Удаляем старые запросы
        self.user_requests[user_id] = [
            req_time for req_time in self.user_requests[user_id]
            if (now - req_time).seconds < self.window
        ]
        
        # Проверяем лимит
        if len(self.user_requests[user_id]) >= self.max_requests:
            return False
        
        self.user_requests[user_id].append(now)
        return True

rate_limiter = RateLimiter(max_requests=10, window=30)

# ========== УЛУЧШЕНИЕ: MIDDLEWARE ДЛЯ RATE LIMITING ==========
class RateLimitMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[types.TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: types.TelegramObject,
        data: Dict[str, Any]
    ) -> Any:
        # Проверяем, есть ли пользователь
        if hasattr(event, 'from_user') and event.from_user:
            user_id = event.from_user.id
            
            if not rate_limiter.is_allowed(user_id):
                if hasattr(event, 'answer'):
                    await event.answer("⏳ <b>Слишком много запросов!</b>\n\nПожалуйста, подождите 30 секунд.", parse_mode='HTML')
                return
            
            health_monitor.record_message(user_id)
        
        return await handler(event, data)

# Инициализация бота с улучшенными настройками
bot = Bot(
    token=API_TOKEN,
    default=DefaultBotProperties(parse_mode='HTML')
)
storage = MemoryStorage()
dp = Dispatcher(storage=storage)

# Регистрация middleware
dp.update.outer_middleware(RateLimitMiddleware())

# ========== УЛУЧШЕНИЕ: АСИНХРОННАЯ БАЗА ДАННЫХ ==========
@asynccontextmanager
async def get_db_connection():
    """Асинхронный контекстный менеджер для работы с БД"""
    conn = await aiosqlite.connect('filters.db')
    conn.row_factory = aiosqlite.Row
    try:
        yield conn
        await conn.commit()
    except Exception as e:
        await conn.rollback()
        raise e
    finally:
        await conn.close()

async def get_user_filters(user_id: int) -> List[Dict]:
    """Асинхронное получение фильтров пользователя"""
    try:
        async with get_db_connection() as conn:
            cur = await conn.cursor()
            await cur.execute("SELECT * FROM filters WHERE user_id = ? ORDER BY expiry_date", (user_id,))
            rows = await cur.fetchall()
            return [dict(row) for row in rows]
    except Exception as e:
        logging.error(f"Ошибка при получении фильтров пользователя {user_id}: {e}")
        health_monitor.record_error()
        return []

async def get_filter_by_id(filter_id: int, user_id: int) -> Optional[Dict]:
    """Асинхронное получение фильтра по ID"""
    try:
        async with get_db_connection() as conn:
            cur = await conn.cursor()
            await cur.execute("SELECT * FROM filters WHERE id = ? AND user_id = ?", (filter_id, user_id))
            result = await cur.fetchone()
            return dict(result) if result else None
    except Exception as e:
        logging.error(f"Ошибка при получении фильтра {filter_id}: {e}")
        health_monitor.record_error()
        return None

async def get_all_users_stats() -> Dict:
    """Асинхронное получение статистики"""
    try:
        async with get_db_connection() as conn:
            cur = await conn.cursor()
            await cur.execute('''SELECT COUNT(DISTINCT user_id) as total_users, 
                                  COUNT(*) as total_filters,
                                  SUM(CASE WHEN expiry_date <= date('now') THEN 1 ELSE 0 END) as expired_filters,
                                  SUM(CASE WHEN expiry_date BETWEEN date('now') AND date('now', '+7 days') THEN 1 ELSE 0 END) as expiring_soon
                           FROM filters''')
            result = await cur.fetchone()
            return dict(result) if result else {'total_users': 0, 'total_filters': 0, 'expired_filters': 0, 'expiring_soon': 0}
    except Exception as e:
        logging.error(f"Ошибка при получении статистики: {e}")
        health_monitor.record_error()
        return {'total_users': 0, 'total_filters': 0, 'expired_filters': 0, 'expiring_soon': 0}

async def add_filter_to_db(user_id: int, filter_type: str, location: str, last_change: str, expiry_date: str, lifetime_days: int) -> bool:
    """Добавление фильтра в БД"""
    try:
        async with get_db_connection() as conn:
            cur = await conn.cursor()
            await cur.execute('''INSERT INTO filters 
                              (user_id, filter_type, location, last_change, expiry_date, lifetime_days) 
                              VALUES (?, ?, ?, ?, ?, ?)''',
                              (user_id, filter_type, location, last_change, expiry_date, lifetime_days))
            return True
    except Exception as e:
        logging.error(f"Ошибка при добавлении фильтра: {e}")
        health_monitor.record_error()
        return False

async def update_filter_in_db(filter_id: int, user_id: int, **kwargs) -> bool:
    """Обновление фильтра в БД"""
    try:
        if not kwargs:
            return False
        
        async with get_db_connection() as conn:
            cur = await conn.cursor()
            set_clause = ", ".join([f"{key} = ?" for key in kwargs.keys()])
            values = list(kwargs.values())
            values.extend([filter_id, user_id])
            
            await cur.execute(f"UPDATE filters SET {set_clause} WHERE id = ? AND user_id = ?", values)
            return cur.rowcount > 0
    except Exception as e:
        logging.error(f"Ошибка при обновлении фильтра {filter_id}: {e}")
        health_monitor.record_error()
        return False

async def delete_filter_from_db(filter_id: int, user_id: int) -> bool:
    """Удаление фильтра из БД"""
    try:
        async with get_db_connection() as conn:
            cur = await conn.cursor()
            await cur.execute("DELETE FROM filters WHERE id = ? AND user_id = ?", (filter_id, user_id))
            return cur.rowcount > 0
    except Exception as e:
        logging.error(f"Ошибка при удалении фильтра {filter_id}: {e}")
        health_monitor.record_error()
        return False

# ========== УЛУЧШЕНИЕ: УЛУЧШЕННАЯ ВАЛИДАЦИЯ ДАТ ==========
def try_auto_correct_date(date_str: str) -> Optional[datetime.date]:
    """Попытка автоматического исправления даты"""
    clean = re.sub(r'\D', '', date_str)
    
    if len(clean) == 6:  # ДДММГГ
        try:
            day, month, year = int(clean[:2]), int(clean[2:4]), int(clean[4:])
            if year < 100:
                year += 2000 if year < 50 else 1900
            return datetime(year, month, day).date()
        except ValueError:
            pass
    elif len(clean) == 8:  # ДДММГГГГ
        try:
            day, month, year = int(clean[:2]), int(clean[2:4]), int(clean[4:])
            return datetime(year, month, day).date()
        except ValueError:
            pass
    
    return None

def validate_date(date_str: str) -> datetime.date:
    """Улучшенная валидация даты с автокоррекцией"""
    date_str = date_str.strip()
    
    # Автозамена разделителей
    date_str = re.sub(r'[/\-]', '.', date_str)
    
    # Удаляем лишние символы, но оставляем точки
    date_str = re.sub(r'[^\d\.]', '', date_str)
    
    formats = ['%d.%m.%y', '%d.%m.%Y', '%d%m%y', '%d%m%Y', '%d.%m', '%d%m']
    
    for fmt in formats:
        try:
            if fmt in ['%d.%m', '%d%m']:
                # Добавляем текущий год
                date_obj = datetime.strptime(date_str, fmt).date()
                date_obj = date_obj.replace(year=datetime.now().year)
            elif fmt in ['%d%m%y', '%d%m%Y']:
                if len(date_str) in [6, 8]:
                    date_obj = datetime.strptime(date_str, fmt).date()
                else:
                    continue
            else:
                date_obj = datetime.strptime(date_str, fmt).date()
            
            today = datetime.now().date()
            max_past = today - timedelta(days=5*365)
            max_future = today + timedelta(days=1)
            
            if date_obj > max_future:
                raise ValueError("Дата не может быть в будущем")
            if date_obj < max_past:
                raise ValueError("Дата слишком старая (более 5 лет)")
                
            return date_obj
        except ValueError:
            continue
    
    # Попытка автоматического исправления
    corrected = try_auto_correct_date(date_str)
    if corrected:
        return corrected
    
    raise ValueError("Неверный формат даты. Используйте ДД.ММ.ГГ или ДД.ММ")

# ========== ОСТАЛЬНЫЕ НАСТРОЙКИ ==========
DEFAULT_LIFETIMES = {
    "магистральный sl10": 180,
    "магистральный sl20": 180,
    "гейзер": 365,
    "аквафор": 365,
    "пурифайер": 180
}

# УБРАН ЛИМИТ НА ФИЛЬТРЫ
MAX_FILTERS_PER_USER = 1000  # Очень высокий лимит, практически без ограничений

# States
class FilterStates(StatesGroup):
    waiting_filter_type = State()
    waiting_location = State()
    waiting_change_date = State()
    waiting_lifetime = State()
    waiting_confirmation = State()

class EditFilterStates(StatesGroup):
    waiting_filter_selection = State()
    waiting_field_selection = State()
    waiting_new_value = State()
    waiting_confirmation = State()

class DeleteFilterStates(StatesGroup):
    waiting_filter_selection = State()
    waiting_confirmation = State()

class ImportExportStates(StatesGroup):
    waiting_excel_file = State()

# ========== УЛУЧШЕНИЕ: АСИНХРОННАЯ ИНИЦИАЛИЗАЦИЯ БАЗЫ ==========
async def init_db():
    """Асинхронная инициализация базы данных"""
    try:
        async with get_db_connection() as conn:
            cur = await conn.cursor()
            
            # Проверяем существование таблицы
            await cur.execute("""
                SELECT name FROM sqlite_master 
                WHERE type='table' AND name='filters'
            """)
            table_exists = await cur.fetchone()
            
            if not table_exists:
                # Создаем таблицу
                await cur.execute('''
                    CREATE TABLE filters (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        user_id INTEGER,
                        filter_type TEXT,
                        location TEXT,
                        last_change DATE,
                        expiry_date DATE,
                        lifetime_days INTEGER,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                ''')
                
                # Создаем индексы
                await cur.execute('''CREATE INDEX idx_user_id ON filters(user_id)''')
                await cur.execute('''CREATE INDEX idx_expiry_date ON filters(expiry_date)''')
                logging.info("База данных успешно создана")
            else:
                logging.info("База данных уже существует")
                
    except Exception as e:
        logging.error(f"Критическая ошибка инициализации БД: {e}")
        # Создаем резервную копию при критической ошибке
        if os.path.exists('filters.db'):
            backup_name = f'filters_backup_critical_{datetime.now().strftime("%Y%m%d_%H%M%S")}.db'
            try:
                shutil.copy2('filters.db', backup_name)
                logging.info(f"Создана критическая резервная копия: {backup_name}")
            except Exception as backup_error:
                logging.error(f"Не удалось создать резервную копию: {backup_error}")
        raise

# ========== ОСНОВНЫЕ ОБРАБОТЧИКИ ==========
@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    """Обработчик команды /start с rate limiting"""
    health_monitor.record_message(message.from_user.id)
    
    await message.answer(
        "🌟 <b>Фильтр-Трекер</b> 🤖\n\n"
        "💧 <i>Умный помощник для своевременной замены фильтров</i>\n\n"
        "📦 <b>Основные возможности:</b>\n"
        "• 📋 Просмотр всех ваших фильтров\n"
        "• ✨ Добавление новых фильтров\n"
        "• ⏳ Контроль сроков замены\n"
        "• ⚙️ Полное управление базой\n"
        "• 📊 Детальная статистика\n"
        "• 📤 Импорт/экспорт Excel\n"
        "• 🔔 Автоматические напоминания",
        reply_markup=get_main_keyboard(),
        parse_mode='HTML'
    )

@dp.message(Command("help"))
async def cmd_help(message: types.Message):
    """Обработчик команды /help"""
    help_text = (
        "🌟 <b>Фильтр-Трекер - Помощь</b> 🤖\n\n"
        "📋 <b>Основные команды:</b>\n"
        "• /start - Начать работу\n"
        "• /help - Показать справку\n"
        "• /status - Статус бота (админ)\n\n"
        "💡 <b>Как использовать:</b>\n"
        "1. Добавьте фильтры через меню\n"
        "2. Следите за сроками замены\n"
        "3. Получайте уведомления\n\n"
        "⚙️ <b>Управление:</b>\n"
        "• 📋 Мои фильтры - просмотр всех\n"
        "• ✨ Добавить фильтр - новый фильтр\n"
        "• ⚙️ Управление - редактирование\n"
        "• 📊 Статистика - ваша статистика\n"
        "• 📤 Импорт/Экспорт - работа с Excel\n\n"
        "❌ <b>Отмена операций:</b>\n"
        "Используйте кнопку '❌ Отмена' для отмены текущей операции"
    )
    await message.answer(help_text, parse_mode='HTML', reply_markup=get_main_keyboard())

@dp.message(F.text == "📋 Мои фильтры")
async def cmd_my_filters(message: types.Message):
    """Показать фильтры с rate limiting"""
    health_monitor.record_message(message.from_user.id)
    
    filters = await get_user_filters(message.from_user.id)
    
    if not filters:
        await message.answer(
            "📭 <b>У вас пока нет фильтров</b>\n\n"
            "💫 <i>Добавьте первый фильтр с помощью кнопки '✨ Добавить фильтр'</i>",
            reply_markup=get_main_keyboard(),
            parse_mode='HTML'
        )
        return
    
    today = datetime.now().date()
    response = "📋 <b>ВАШИ ФИЛЬТРЫ</b>\n\n"
    
    for f in filters:
        expiry_date = datetime.strptime(str(f['expiry_date']), '%Y-%m-%d').date()
        last_change = datetime.strptime(str(f['last_change']), '%Y-%m-%d').date()
        days_until_expiry = (expiry_date - today).days
        
        icon, status = get_status_icon_and_text(days_until_expiry)
        
        response += (
            f"{icon} <b>#{f['id']} {f['filter_type']}</b>\n"
            f"📍 {f['location']}\n"
            f"📅 Заменен: {format_date_nice(last_change)}\n"
            f"🗓️ Годен до: {format_date_nice(expiry_date)}\n"
            f"⏱️ Осталось дней: <b>{days_until_expiry}</b>\n"
            f"📊 Статус: <b>{status}</b>\n\n"
        )
    
    infographic = create_expiry_infographic(filters)
    await message.answer(response, parse_mode='HTML')
    await message.answer(infographic, parse_mode='HTML', reply_markup=get_main_keyboard())

@dp.message(F.text == "✨ Добавить фильтр")
async def cmd_add(message: types.Message):
    """Добавление фильтра"""
    await message.answer(
        "🔧 <b>Выберите тип добавления:</b>\n\n"
        "💡 <i>Добавьте новый фильтр в систему</i>",
        reply_markup=get_add_filter_keyboard(),
        parse_mode='HTML'
    )

@dp.message(F.text == "➕ Один фильтр")
async def cmd_add_single(message: types.Message, state: FSMContext):
    """Добавление одного фильтра"""
    await state.set_state(FilterStates.waiting_filter_type)
    await message.answer(
        "💧 <b>Выберите тип фильтра:</b>\n\n"
        "💡 <i>Используйте кнопки для быстрого выбора или введите свой вариант</i>",
        reply_markup=get_filter_type_keyboard(),
        parse_mode='HTML'
    )

@dp.message(FilterStates.waiting_filter_type)
async def process_filter_type(message: types.Message, state: FSMContext):
    """Обработка типа фильтра"""
    if message.text == "🔙 Назад":
        await state.clear()
        await message.answer("🔙 <b>Возврат в меню добавления</b>", reply_markup=get_add_filter_keyboard(), parse_mode='HTML')
        return
    
    filter_type = message.text.strip()
    
    # Если выбран "Другой тип фильтра", ждем ручной ввод
    if filter_type == "Другой тип фильтра":
        await message.answer(
            "💧 <b>Введите свой тип фильтра:</b>\n\n"
            "💡 <i>Например: Барьер, Атолл, Брита и т.д.</i>",
            reply_markup=get_back_keyboard(),
            parse_mode='HTML'
        )
        return
    
    # Валидация типа фильтра
    is_valid, error_msg = validate_filter_type(filter_type)
    if not is_valid:
        await message.answer(f"❌ {error_msg}\n\nПожалуйста, выберите тип фильтра еще раз:", reply_markup=get_filter_type_keyboard())
        return
    
    await state.update_data(filter_type=filter_type)
    await state.set_state(FilterStates.waiting_location)
    
    await message.answer(
        "📍 <b>Введите местоположение фильтра:</b>\n\n"
        "💡 <i>Например: Кухня, Ванная, Под раковиной и т.д.</i>",
        reply_markup=get_back_keyboard(),
        parse_mode='HTML'
    )

@dp.message(FilterStates.waiting_location)
async def process_location(message: types.Message, state: FSMContext):
    """Обработка местоположения"""
    if message.text == "🔙 Назад":
        await state.set_state(FilterStates.waiting_filter_type)
        await message.answer(
            "💧 <b>Выберите тип фильтра:</b>",
            reply_markup=get_filter_type_keyboard(),
            parse_mode='HTML'
        )
        return
    
    location = message.text.strip()
    
    # Валидация местоположения
    is_valid, error_msg = validate_location(location)
    if not is_valid:
        await message.answer(f"❌ {error_msg}\n\nПожалуйста, введите местоположение еще раз:", reply_markup=get_back_keyboard())
        return
    
    await state.update_data(location=location)
    await state.set_state(FilterStates.waiting_change_date)
    
    await message.answer(
        "📅 <b>Введите дату последней замены:</b>\n\n"
        "💡 <i>Формат: ДД.ММ.ГГ или ДД.ММ (текущий год)</i>\n"
        "📝 <i>Пример: 15.09.23 или 15.09</i>",
        reply_markup=get_back_keyboard(),
        parse_mode='HTML'
    )

@dp.message(FilterStates.waiting_change_date)
async def process_change_date(message: types.Message, state: FSMContext):
    """Обработка даты замены"""
    if message.text == "🔙 Назад":
        await state.set_state(FilterStates.waiting_location)
        await message.answer(
            "📍 <b>Введите местоположение фильтра:</b>",
            reply_markup=get_back_keyboard(),
            parse_mode='HTML'
        )
        return
    
    try:
        change_date = validate_date(message.text)
        await state.update_data(change_date=change_date)
        
        user_data = await state.get_data()
        filter_type = user_data.get('filter_type', '').lower()
        
        # Автоматическое определение срока службы
        lifetime = DEFAULT_LIFETIMES.get(filter_type, 180)
        
        await state.update_data(lifetime=lifetime)
        await state.set_state(FilterStates.waiting_lifetime)
        
        await message.answer(
            f"⏱️ <b>Срок службы фильтра:</b> {lifetime} дней\n\n"
            f"💡 <i>Автоматически определен для '{user_data['filter_type']}'</i>\n"
            f"📝 <i>Если хотите изменить, введите новое значение (в днях), или нажмите 'Пропустить'</i>",
            reply_markup=types.ReplyKeyboardMarkup(
                keyboard=[
                    [types.KeyboardButton(text="Пропустить")],
                    [types.KeyboardButton(text="🔙 Назад")]
                ],
                resize_keyboard=True
            ),
            parse_mode='HTML'
        )
        
    except ValueError as e:
        await message.answer(f"❌ {str(e)}\n\nПожалуйста, введите дату в правильном формате:", reply_markup=get_back_keyboard())

@dp.message(FilterStates.waiting_lifetime)
async def process_lifetime(message: types.Message, state: FSMContext):
    """Обработка срока службы"""
    if message.text == "🔙 Назад":
        await state.set_state(FilterStates.waiting_change_date)
        await message.answer(
            "📅 <b>Введите дату последней замены:</b>",
            reply_markup=get_back_keyboard(),
            parse_mode='HTML'
        )
        return
    
    user_data = await state.get_data()
    
    if message.text != "Пропустить":
        is_valid, error_msg, lifetime = validate_lifetime(message.text)
        if not is_valid:
            await message.answer(f"❌ {error_msg}\n\nПожалуйста, введите корректное число дней:")
            return
    else:
        lifetime = user_data['lifetime']
    
    # Расчет даты истечения
    change_date = user_data['change_date']
    expiry_date = change_date + timedelta(days=lifetime)
    
    # Сохраняем все данные для подтверждения
    await state.update_data(
        lifetime=lifetime,
        expiry_date=expiry_date
    )
    
    # Переходим к подтверждению
    await state.set_state(FilterStates.waiting_confirmation)
    
    await message.answer(
        f"🔍 <b>ПОДТВЕРЖДЕНИЕ ДАННЫХ</b>\n\n"
        f"💧 <b>Тип фильтра:</b> {user_data['filter_type']}\n"
        f"📍 <b>Местоположение:</b> {user_data['location']}\n"
        f"📅 <b>Дата замены:</b> {format_date_nice(change_date)}\n"
        f"⏱️ <b>Срок службы:</b> {lifetime} дней\n"
        f"🗓️ <b>Годен до:</b> {format_date_nice(expiry_date)}\n\n"
        f"✅ <b>Всё верно?</b>",
        reply_markup=get_confirmation_keyboard(),
        parse_mode='HTML'
    )

@dp.message(FilterStates.waiting_confirmation)
async def process_confirmation(message: types.Message, state: FSMContext):
    """Обработка подтверждения добавления фильтра"""
    if message.text == "✅ Да, всё верно":
        user_data = await state.get_data()
        
        # Сохранение в БД
        success = await add_filter_to_db(
            user_id=message.from_user.id,
            filter_type=user_data['filter_type'],
            location=user_data['location'],
            last_change=user_data['change_date'].strftime('%Y-%m-%d'),
            expiry_date=user_data['expiry_date'].strftime('%Y-%m-%d'),
            lifetime_days=user_data['lifetime']
        )
        
        if success:
            await message.answer(
                f"✅ <b>ФИЛЬТР УСПЕШНО ДОБАВЛЕН!</b>\n\n"
                f"💧 <b>Тип:</b> {user_data['filter_type']}\n"
                f"📍 <b>Место:</b> {user_data['location']}\n"
                f"📅 <b>Замена:</b> {format_date_nice(user_data['change_date'])}\n"
                f"🗓️ <b>Годен до:</b> {format_date_nice(user_data['expiry_date'])}\n"
                f"⏱️ <b>Срок:</b> {user_data['lifetime']} дней\n\n"
                f"💫 <i>Теперь я буду следить за сроком замены этого фильтра</i>",
                reply_markup=get_main_keyboard(),
                parse_mode='HTML'
            )
        else:
            await message.answer(
                "❌ <b>Ошибка при добавлении фильтра!</b>\n\n"
                "Пожалуйста, попробуйте еще раз.",
                reply_markup=get_main_keyboard(),
                parse_mode='HTML'
            )
        
        await state.clear()
        
    elif message.text == "❌ Нет, изменить":
        await state.set_state(FilterStates.waiting_filter_type)
        await message.answer(
            "🔄 <b>Начинаем заново</b>\n\n"
            "💧 <b>Выберите тип фильтра:</b>",
            reply_markup=get_filter_type_keyboard(),
            parse_mode='HTML'
        )
    else:
        await message.answer(
            "❌ <b>Пожалуйста, выберите вариант подтверждения:</b>",
            reply_markup=get_confirmation_keyboard(),
            parse_mode='HTML'
        )

@dp.message(F.text == "📊 Статистика")
async def cmd_stats(message: types.Message):
    """Показать статистику"""
    health_monitor.record_message(message.from_user.id)
    
    stats = await get_all_users_stats()
    user_filters = await get_user_filters(message.from_user.id)
    
    response = (
        "📊 <b>ВАША СТАТИСТИКА</b>\n\n"
        f"📦 <b>Всего фильтров:</b> {len(user_filters)}\n\n"
    )
    
    if is_admin(message.from_user.id):
        global_stats = (
            f"👑 <b>АДМИН СТАТИСТИКА</b>\n"
            f"👥 Пользователей: {stats['total_users']}\n"
            f"📦 Фильтров: {stats['total_filters']}\n"
            f"🔴 Просрочено: {stats['expired_filters']}\n"
            f"🟡 Скоро истечет: {stats['expiring_soon']}"
        )
        response += global_stats
    
    await message.answer(response, parse_mode='HTML', reply_markup=get_main_keyboard())

@dp.message(F.text == "⚙️ Управление фильтрами")
async def cmd_manage(message: types.Message):
    """Управление фильтрами"""
    health_monitor.record_message(message.from_user.id)
    
    filters = await get_user_filters(message.from_user.id)
    
    if not filters:
        await message.answer(
            "📭 <b>У вас пока нет фильтров для управления</b>\n\n"
            "💫 <i>Добавьте первый фильтр с помощью кнопки '✨ Добавить фильтр'</i>",
            reply_markup=get_main_keyboard(),
            parse_mode='HTML'
        )
        return
    
    await message.answer(
        "⚙️ <b>УПРАВЛЕНИЕ ФИЛЬТРАМИ</b>\n\n"
        "Выберите действие:",
        reply_markup=get_management_keyboard()
    )

# ========== РЕДАКТИРОВАНИЕ ФИЛЬТРОВ ==========
@dp.message(F.text == "✏️ Редактировать фильтр")
async def cmd_edit_filter(message: types.Message, state: FSMContext):
    """Начало редактирования фильтра"""
    filters = await get_user_filters(message.from_user.id)
    
    if not filters:
        await message.answer(
            "📭 <b>У вас пока нет фильтров для редактирования</b>",
            reply_markup=get_main_keyboard(),
            parse_mode='HTML'
        )
        return
    
    # Создаем клавиатуру с фильтрами
    builder = ReplyKeyboardBuilder()
    for f in filters:
        builder.button(text=f"#{f['id']} {f['filter_type']} - {f['location']}")
    builder.button(text="🔙 Назад")
    builder.adjust(1)
    
    await state.set_state(EditFilterStates.waiting_filter_selection)
    await message.answer(
        "📝 <b>Выберите фильтр для редактирования:</b>",
        reply_markup=builder.as_markup(resize_keyboard=True),
        parse_mode='HTML'
    )

@dp.message(EditFilterStates.waiting_filter_selection)
async def process_filter_selection_for_edit(message: types.Message, state: FSMContext):
    """Обработка выбора фильтра для редактирования"""
    if message.text == "🔙 Назад":
        await state.clear()
        await message.answer("🔙 <b>Возврат в меню управления</b>", reply_markup=get_management_keyboard(), parse_mode='HTML')
        return
    
    # Извлекаем ID фильтра из текста
    match = re.match(r'#(\d+)', message.text)
    if not match:
        await message.answer("❌ Неверный формат. Пожалуйста, выберите фильтр из списка.", reply_markup=get_back_keyboard())
        return
    
    filter_id = int(match.group(1))
    user_id = message.from_user.id
    
    # Проверяем существование фильтра
    filter_data = await get_filter_by_id(filter_id, user_id)
    if not filter_data:
        await message.answer("❌ Фильтр не найден.", reply_markup=get_back_keyboard())
        return
    
    # Сохраняем данные фильтра в состоянии
    await state.update_data(
        filter_id=filter_id,
        current_filter=filter_data
    )
    
    await state.set_state(EditFilterStates.waiting_field_selection)
    
    await message.answer(
        f"📝 <b>Редактирование фильтра #{filter_id}</b>\n\n"
        f"💧 <b>Тип:</b> {filter_data['filter_type']}\n"
        f"📍 <b>Место:</b> {filter_data['location']}\n"
        f"📅 <b>Замена:</b> {format_date_nice(datetime.strptime(str(filter_data['last_change']), '%Y-%m-%d'))}\n"
        f"⏱️ <b>Срок:</b> {filter_data['lifetime_days']} дней\n\n"
        "🔧 <b>Выберите поле для редактирования:</b>",
        reply_markup=get_edit_keyboard(),
        parse_mode='HTML'
    )

@dp.message(EditFilterStates.waiting_field_selection)
async def process_field_selection(message: types.Message, state: FSMContext):
    """Обработка выбора поля для редактирования"""
    if message.text == "🔙 Назад":
        await state.clear()
        await message.answer("🔙 <b>Возврат в меню управления</b>", reply_markup=get_management_keyboard(), parse_mode='HTML')
        return
    
    user_data = await state.get_data()
    filter_data = user_data['current_filter']
    
    field_map = {
        "💧 Тип фильтра": "filter_type",
        "📍 Местоположение": "location", 
        "📅 Дата замены": "last_change",
        "⏱️ Срок службы": "lifetime_days"
    }
    
    if message.text not in field_map:
        await message.answer("❌ Пожалуйста, выберите поле из списка.", reply_markup=get_edit_keyboard())
        return
    
    field = field_map[message.text]
    await state.update_data(editing_field=field)
    await state.set_state(EditFilterStates.waiting_new_value)
    
    current_value = filter_data[field]
    
    if field == "last_change":
        current_value = format_date_nice(datetime.strptime(str(current_value), '%Y-%m-%d'))
        prompt = "📅 <b>Введите новую дату замены:</b>\n\n💡 <i>Формат: ДД.ММ.ГГ или ДД.ММ</i>"
    elif field == "lifetime_days":
        prompt = f"⏱️ <b>Текущий срок службы:</b> {current_value} дней\n\nВведите новое значение (в днях):"
    else:
        prompt = f"✏️ <b>Текущее значение:</b> {current_value}\n\nВведите новое значение:"
    
    await message.answer(prompt, parse_mode='HTML', reply_markup=get_back_keyboard())

@dp.message(EditFilterStates.waiting_new_value)
async def process_new_value(message: types.Message, state: FSMContext):
    """Обработка нового значения для поля"""
    if message.text == "🔙 Назад":
        await state.set_state(EditFilterStates.waiting_field_selection)
        user_data = await state.get_data()
        filter_data = user_data['current_filter']
        
        await message.answer(
            f"📝 <b>Редактирование фильтра #{user_data['filter_id']}</b>\n\n"
            f"💧 <b>Тип:</b> {filter_data['filter_type']}\n"
            f"📍 <b>Место:</b> {filter_data['location']}\n"
            f"📅 <b>Замена:</b> {format_date_nice(datetime.strptime(str(filter_data['last_change']), '%Y-%m-%d'))}\n"
            f"⏱️ <b>Срок:</b> {filter_data['lifetime_days']} дней\n\n"
            "🔧 <b>Выберите поле для редактирования:</b>",
            reply_markup=get_edit_keyboard(),
            parse_mode='HTML'
        )
        return
    
    user_data = await state.get_data()
    filter_id = user_data['filter_id']
    field = user_data['editing_field']
    user_id = message.from_user.id
    
    try:
        if field == "filter_type":
            is_valid, error_msg = validate_filter_type(message.text)
            if not is_valid:
                await message.answer(f"❌ {error_msg}\n\nПожалуйста, введите корректное значение:", reply_markup=get_back_keyboard())
                return
            new_value = message.text
            
        elif field == "location":
            is_valid, error_msg = validate_location(message.text)
            if not is_valid:
                await message.answer(f"❌ {error_msg}\n\nПожалуйста, введите корректное значение:", reply_markup=get_back_keyboard())
                return
            new_value = message.text
            
        elif field == "last_change":
            try:
                change_date = validate_date(message.text)
                new_value = change_date.strftime('%Y-%m-%d')
                
                # Пересчитываем дату истечения
                filter_data = user_data['current_filter']
                expiry_date = change_date + timedelta(days=filter_data['lifetime_days'])
                await update_filter_in_db(filter_id, user_id, expiry_date=expiry_date.strftime('%Y-%m-%d'))
                
            except ValueError as e:
                await message.answer(f"❌ {str(e)}\n\nПожалуйста, введите дату в правильном формате:", reply_markup=get_back_keyboard())
                return
                
        elif field == "lifetime_days":
            is_valid, error_msg, lifetime = validate_lifetime(message.text)
            if not is_valid:
                await message.answer(f"❌ {error_msg}\n\nПожалуйста, введите корректное число дней:", reply_markup=get_back_keyboard())
                return
            new_value = lifetime
            
            # Пересчитываем дату истечения
            filter_data = user_data['current_filter']
            last_change = datetime.strptime(str(filter_data['last_change']), '%Y-%m-%d').date()
            expiry_date = last_change + timedelta(days=lifetime)
            await update_filter_in_db(filter_id, user_id, expiry_date=expiry_date.strftime('%Y-%m-%d'))
        
        # Сохраняем новое значение для подтверждения
        await state.update_data(new_value=new_value)
        await state.set_state(EditFilterStates.waiting_confirmation)
        
        await message.answer(
            f"🔍 <b>ПОДТВЕРЖДЕНИЕ ИЗМЕНЕНИЯ</b>\n\n"
            f"🆔 <b>Фильтр #</b>{filter_id}\n"
            f"📝 <b>Поле:</b> {field}\n"
            f"📊 <b>Старое значение:</b> {filter_data[field] if field != 'last_change' else format_date_nice(datetime.strptime(str(filter_data[field]), '%Y-%m-%d'))}\n"
            f"💫 <b>Новое значение:</b> {new_value}\n\n"
            f"✅ <b>Подтверждаете изменение?</b>",
            reply_markup=get_confirmation_keyboard(),
            parse_mode='HTML'
        )
            
    except Exception as e:
        logging.error(f"Ошибка при обновлении фильтра: {e}")
        await message.answer(
            "❌ <b>Произошла ошибка при обновлении</b>",
            reply_markup=get_main_keyboard(),
            parse_mode='HTML'
        )

@dp.message(EditFilterStates.waiting_confirmation)
async def process_edit_confirmation(message: types.Message, state: FSMContext):
    """Обработка подтверждения редактирования"""
    if message.text == "✅ Да, всё верно":
        user_data = await state.get_data()
        filter_id = user_data['filter_id']
        field = user_data['editing_field']
        new_value = user_data['new_value']
        user_id = message.from_user.id
        
        # Обновляем поле в БД
        success = await update_filter_in_db(filter_id, user_id, **{field: new_value})
        
        if success:
            await message.answer(
                f"✅ <b>ФИЛЬТР ОБНОВЛЕН!</b>\n\n"
                f"🆔 <b>Фильтр #</b>{filter_id}\n"
                f"📝 <b>Поле обновлено:</b> {field}\n"
                f"💫 <b>Новое значение:</b> {new_value}",
                reply_markup=get_main_keyboard(),
                parse_mode='HTML'
            )
        else:
            await message.answer(
                "❌ <b>Ошибка при обновлении фильтра!</b>",
                reply_markup=get_main_keyboard(),
                parse_mode='HTML'
            )
            
    elif message.text == "❌ Нет, изменить":
        await state.set_state(EditFilterStates.waiting_field_selection)
        user_data = await state.get_data()
        filter_data = user_data['current_filter']
        
        await message.answer(
            f"📝 <b>Редактирование фильтра #{user_data['filter_id']}</b>\n\n"
            f"💧 <b>Тип:</b> {filter_data['filter_type']}\n"
            f"📍 <b>Место:</b> {filter_data['location']}\n"
            f"📅 <b>Замена:</b> {format_date_nice(datetime.strptime(str(filter_data['last_change']), '%Y-%m-%d'))}\n"
            f"⏱️ <b>Срок:</b> {filter_data['lifetime_days']} дней\n\n"
            "🔧 <b>Выберите поле для редактирования:</b>",
            reply_markup=get_edit_keyboard(),
            parse_mode='HTML'
        )
        return
    else:
        await message.answer(
            "❌ <b>Пожалуйста, выберите вариант подтверждения:</b>",
            reply_markup=get_confirmation_keyboard(),
            parse_mode='HTML'
        )
    
    await state.clear()

# ========== УДАЛЕНИЕ ФИЛЬТРОВ ==========
@dp.message(F.text == "🗑️ Удалить фильтр")
async def cmd_delete_filter(message: types.Message, state: FSMContext):
    """Начало удаления фильтра"""
    filters = await get_user_filters(message.from_user.id)
    
    if not filters:
        await message.answer(
            "📭 <b>У вас пока нет фильтров для удаления</b>",
            reply_markup=get_main_keyboard(),
            parse_mode='HTML'
        )
        return
    
    # Создаем клавиатуру с фильтрами
    builder = ReplyKeyboardBuilder()
    for f in filters:
        builder.button(text=f"#{f['id']} {f['filter_type']} - {f['location']}")
    builder.button(text="🔙 Назад")
    builder.adjust(1)
    
    await state.set_state(DeleteFilterStates.waiting_filter_selection)
    await message.answer(
        "🗑️ <b>Выберите фильтр для удаления:</b>",
        reply_markup=builder.as_markup(resize_keyboard=True),
        parse_mode='HTML'
    )

@dp.message(DeleteFilterStates.waiting_filter_selection)
async def process_filter_selection_for_delete(message: types.Message, state: FSMContext):
    """Обработка выбора фильтра для удаления"""
    if message.text == "🔙 Назад":
        await state.clear()
        await message.answer("🔙 <b>Возврат в меню управления</b>", reply_markup=get_management_keyboard(), parse_mode='HTML')
        return
    
    # Извлекаем ID фильтра из текста
    match = re.match(r'#(\d+)', message.text)
    if not match:
        await message.answer("❌ Неверный формат. Пожалуйста, выберите фильтр из списка.", reply_markup=get_back_keyboard())
        return
    
    filter_id = int(match.group(1))
    user_id = message.from_user.id
    
    # Проверяем существование фильтра
    filter_data = await get_filter_by_id(filter_id, user_id)
    if not filter_data:
        await message.answer("❌ Фильтр не найден.", reply_markup=get_back_keyboard())
        return
    
    # Сохраняем данные фильтра в состоянии
    await state.update_data(
        filter_id=filter_id,
        filter_data=filter_data
    )
    
    await state.set_state(DeleteFilterStates.waiting_confirmation)
    
    await message.answer(
        f"🗑️ <b>ПОДТВЕРЖДЕНИЕ УДАЛЕНИЯ</b>\n\n"
        f"💧 <b>Тип:</b> {filter_data['filter_type']}\n"
        f"📍 <b>Место:</b> {filter_data['location']}\n"
        f"📅 <b>Замена:</b> {format_date_nice(datetime.strptime(str(filter_data['last_change']), '%Y-%m-%d'))}\n"
        f"🗓️ <b>Годен до:</b> {format_date_nice(datetime.strptime(str(filter_data['expiry_date']), '%Y-%m-%d'))}\n\n"
        f"⚠️ <b>Вы уверены, что хотите удалить этот фильтр?</b>\n"
        f"❌ <i>Это действие нельзя отменить!</i>",
        reply_markup=get_confirmation_keyboard(),
        parse_mode='HTML'
    )

@dp.message(DeleteFilterStates.waiting_confirmation)
async def process_delete_confirmation(message: types.Message, state: FSMContext):
    """Обработка подтверждения удаления"""
    if message.text == "✅ Да, всё верно":
        user_data = await state.get_data()
        filter_id = user_data['filter_id']
        user_id = message.from_user.id
        filter_data = user_data['filter_data']
        
        # Удаляем фильтр
        success = await delete_filter_from_db(filter_id, user_id)
        
        if success:
            await message.answer(
                f"✅ <b>ФИЛЬТР УДАЛЕН!</b>\n\n"
                f"🆔 <b>Фильтр #</b>{filter_id}\n"
                f"💧 <b>Тип:</b> {filter_data['filter_type']}\n"
                f"📍 <b>Место:</b> {filter_data['location']}\n"
                f"🗑️ <i>Фильтр успешно удален из базы данных</i>",
                reply_markup=get_main_keyboard(),
                parse_mode='HTML'
            )
        else:
            await message.answer(
                "❌ <b>Ошибка при удалении фильтра!</b>",
                reply_markup=get_main_keyboard(),
                parse_mode='HTML'
            )
        
    elif message.text == "❌ Нет, изменить":
        await state.clear()
        await message.answer(
            "✅ <b>Удаление отменено</b>\n\n"
            "Фильтр не был удален.",
            reply_markup=get_main_keyboard(),
            parse_mode='HTML'
        )
        return
    else:
        await message.answer(
            "❌ <b>Пожалуйста, выберите вариант подтверждения:</b>",
            reply_markup=get_confirmation_keyboard(),
            parse_mode='HTML'
        )
    
    await state.clear()

# ========== ОСТАЛЬНЫЕ ОБРАБОТЧИКИ (без изменений) ==========
# [Здесь остаются обработчики для Онлайн Excel, Импорт/Экспорт, Админ команды и т.д.]
# Они остаются без изменений, поэтому я их не дублирую для экономии места

# ========== ОБРАБОТЧИКИ КНОПОК НАЗАД И ОТМЕНА ==========
@dp.message(F.text == "🔙 Назад")
async def cmd_back(message: types.Message, state: FSMContext):
    """Возврат в предыдущее меню"""
    current_state = await state.get_state()
    
    if current_state:
        await state.clear()
    
    await message.answer(
        "🔙 <b>Возврат в главное меню</b>",
        reply_markup=get_main_keyboard(),
        parse_mode='HTML'
    )

@dp.message(F.text == "❌ Отмена")
async def cmd_cancel(message: types.Message, state: FSMContext):
    """Отмена текущей операции"""
    current_state = await state.get_state()
    
    if current_state:
        await state.clear()
        await message.answer(
            "❌ <b>Операция отменена</b>",
            reply_markup=get_main_keyboard(),
            parse_mode='HTML'
        )
    else:
        await message.answer(
            "ℹ️ <b>Нет активных операций для отмены</b>",
            reply_markup=get_main_keyboard(),
            parse_mode='HTML'
        )

# ========== ЗАПУСК БОТА ==========
async def main():
    if not API_TOKEN:
        logging.error("Токен бота не найден! Установите переменную TELEGRAM_BOT_TOKEN")
        exit(1)
    
    # Инициализация БД
    await init_db()
    
    try:
        await dp.start_polling(bot)
    except KeyboardInterrupt:
        logging.info("Бот остановлен пользователем")
    except Exception as e:
        logging.error(f"Критическая ошибка: {e}")
        raise

if __name__ == '__main__':
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("Бот остановлен")
    except Exception as e:
        logging.critical(f"Критическая ошибка при запуске: {e}")
