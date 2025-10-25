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

# Google Sheets настройки
GOOGLE_SHEETS_CREDENTIALS = os.getenv('GOOGLE_SHEETS_CREDENTIALS')
GOOGLE_SHEET_ID = os.getenv('GOOGLE_SHEET_ID')

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
    builder.button(text="☁️ Синхронизация с Google Sheets")
    builder.button(text="🔙 Назад")
    builder.adjust(2)
    return builder.as_markup(resize_keyboard=True)

def get_sync_keyboard():
    """Клавиатура синхронизации"""
    builder = ReplyKeyboardBuilder()
    builder.button(text="🔄 Синхронизировать с Google Sheets")
    builder.button(text="⚙️ Настройки синхронизации")
    builder.button(text="📊 Статус синхронизации")
    builder.button(text="🔙 Назад")
    builder.adjust(2)
    return builder.as_markup(resize_keyboard=True)

def get_sync_settings_keyboard():
    """Клавиатура настроек синхронизации"""
    builder = ReplyKeyboardBuilder()
    builder.button(text="📝 Указать ID таблицы")
    builder.button(text="🔄 Автосинхронизация ВКЛ")
    builder.button(text="⏸️ Автосинхронизация ВЫКЛ")
    builder.button(text="🗑️ Отключить синхронизацию")
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

# ========== GOOGLE SHEETS ИНТЕГРАЦИЯ ==========
class GoogleSheetsSync:
    def __init__(self):
        self.credentials = None
        self.sheet_id = None
        self.auto_sync = False
        self.load_settings()
    
    def load_settings(self):
        """Загрузка настроек из файла"""
        try:
            if os.path.exists('sheets_settings.json'):
                with open('sheets_settings.json', 'r', encoding='utf-8') as f:
                    settings = json.load(f)
                    self.sheet_id = settings.get('sheet_id')
                    self.auto_sync = settings.get('auto_sync', False)
        except Exception as e:
            logging.error(f"Ошибка загрузки настроек Google Sheets: {e}")
    
    def save_settings(self):
        """Сохранение настроек в файл"""
        try:
            settings = {
                'sheet_id': self.sheet_id,
                'auto_sync': self.auto_sync
            }
            with open('sheets_settings.json', 'w', encoding='utf-8') as f:
                json.dump(settings, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logging.error(f"Ошибка сохранения настроек Google Sheets: {e}")
    
    def is_configured(self) -> bool:
        """Проверка настройки синхронизации"""
        return bool(self.sheet_id and GOOGLE_SHEETS_CREDENTIALS)
    
    async def initialize_credentials(self):
        """Инициализация учетных данных Google"""
        try:
            if not GOOGLE_SHEETS_CREDENTIALS:
                return False
            
            # Парсим JSON credentials из переменной окружения
            credentials_info = json.loads(GOOGLE_SHEETS_CREDENTIALS)
            
            # Импортируем здесь, чтобы не требовать установку если не используется
            try:
                import gspread
                from google.oauth2.service_account import Credentials
            except ImportError:
                logging.error("Библиотеки gspread или google-auth не установлены")
                return False
            
            # Создаем credentials
            scope = ['https://spreadsheets.google.com/feeds', 
                    'https://www.googleapis.com/auth/drive']
            self.credentials = Credentials.from_service_account_info(credentials_info, scopes=scope)
            return True
            
        except Exception as e:
            logging.error(f"Ошибка инициализации Google Sheets: {e}")
            return False
    
    async def sync_to_sheets(self, user_id: int, user_filters: List[Dict]) -> tuple[bool, str]:
        """Синхронизация данных с Google Sheets"""
        try:
            if not self.is_configured():
                return False, "Синхронизация не настроена"
            
            if not self.credentials:
                if not await self.initialize_credentials():
                    return False, "Ошибка инициализации Google API"
            
            import gspread
            
            # Создаем клиент
            gc = gspread.authorize(self.credentials)
            
            # Открываем таблицу
            sheet = gc.open_by_key(self.sheet_id)
            
            # Получаем или создаем лист для пользователя
            worksheet_name = f"User_{user_id}"
            try:
                worksheet = sheet.worksheet(worksheet_name)
            except gspread.exceptions.WorksheetNotFound:
                worksheet = sheet.add_worksheet(title=worksheet_name, rows=100, cols=10)
                
                # Заголовки
                headers = ['ID', 'Тип фильтра', 'Местоположение', 'Дата замены', 
                          'Срок службы (дни)', 'Годен до', 'Статус', 'Осталось дней']
                worksheet.append_row(headers)
            
            # Очищаем старые данные (кроме заголовка)
            worksheet.batch_clear(['A2:H100'])
            
            # Подготавливаем данные
            today = datetime.now().date()
            rows = []
            
            for f in user_filters:
                expiry_date = datetime.strptime(str(f['expiry_date']), '%Y-%m-%d').date()
                last_change = datetime.strptime(str(f['last_change']), '%Y-%m-%d').date()
                days_until = (expiry_date - today).days
                
                icon, status = get_status_icon_and_text(days_until)
                
                row = [
                    f['id'],
                    f['filter_type'],
                    f['location'],
                    format_date_nice(last_change),
                    f['lifetime_days'],
                    format_date_nice(expiry_date),
                    status,
                    days_until
                ]
                rows.append(row)
            
            # Добавляем данные
            if rows:
                worksheet.append_rows(rows)
            
            # Форматируем таблицу
            try:
                # Заголовки жирным
                worksheet.format('A1:H1', {'textFormat': {'bold': True}})
                
                # Авто-ширина колонок
                worksheet.columns_auto_resize(0, 7)
            except Exception as format_error:
                logging.warning(f"Ошибка форматирования таблицы: {format_error}")
            
            return True, f"Успешно синхронизировано {len(rows)} фильтров"
            
        except Exception as e:
            logging.error(f"Ошибка синхронизации с Google Sheets: {e}")
            return False, f"Ошибка синхронизации: {str(e)}"
    
    async def sync_from_sheets(self, user_id: int) -> tuple[bool, str, int]:
        """Синхронизация данных из Google Sheets"""
        try:
            if not self.is_configured():
                return False, "Синхронизация не настроена", 0
            
            if not self.credentials:
                if not await self.initialize_credentials():
                    return False, "Ошибка инициализации Google API", 0
            
            import gspread
            
            # Создаем клиент
            gc = gspread.authorize(self.credentials)
            
            # Открываем таблицу
            sheet = gc.open_by_key(self.sheet_id)
            
            # Получаем лист пользователя
            worksheet_name = f"User_{user_id}"
            try:
                worksheet = sheet.worksheet(worksheet_name)
            except gspread.exceptions.WorksheetNotFound:
                return False, "Таблица для пользователя не найдена", 0
            
            # Читаем данные
            data = worksheet.get_all_records()
            
            if not data:
                return False, "Нет данных для импорта", 0
            
            # Обрабатываем данные
            imported_count = 0
            errors = []
            
            for index, row in enumerate(data, start=2):
                try:
                    # Пропускаем строки без основных данных
                    if not row.get('Тип фильтра') or not row.get('Местоположение'):
                        continue
                    
                    # Валидация типа фильтра
                    filter_type = str(row['Тип фильтра']).strip()
                    is_valid_type, error_msg = validate_filter_type(filter_type)
                    if not is_valid_type:
                        errors.append(f"Строка {index}: {error_msg}")
                        continue
                    
                    # Валидация местоположения
                    location = str(row['Местоположение']).strip()
                    is_valid_loc, error_msg = validate_location(location)
                    if not is_valid_loc:
                        errors.append(f"Строка {index}: {error_msg}")
                        continue
                    
                    # Валидация даты
                    date_str = str(row.get('Дата замены', ''))
                    if not date_str:
                        errors.append(f"Строка {index}: Отсутствует дата замены")
                        continue
                    
                    try:
                        change_date = validate_date(date_str)
                    except ValueError as e:
                        errors.append(f"Строка {index}: {str(e)}")
                        continue
                    
                    # Валидация срока службы
                    lifetime = row.get('Срок службы (дни)', 0)
                    is_valid_lt, error_msg, lifetime_days = validate_lifetime(str(lifetime))
                    if not is_valid_lt:
                        errors.append(f"Строка {index}: {error_msg}")
                        continue
                    
                    # Расчет даты истечения
                    expiry_date = change_date + timedelta(days=lifetime_days)
                    
                    # Добавление в БД
                    success = await add_filter_to_db(
                        user_id=user_id,
                        filter_type=filter_type,
                        location=location,
                        last_change=change_date.strftime('%Y-%m-%d'),
                        expiry_date=expiry_date.strftime('%Y-%m-%d'),
                        lifetime_days=lifetime_days
                    )
                    
                    if success:
                        imported_count += 1
                    else:
                        errors.append(f"Строка {index}: Ошибка базы данных")
                        
                except Exception as e:
                    errors.append(f"Строка {index}: Неизвестная ошибка")
                    logging.error(f"Ошибка импорта строки {index}: {e}")
            
            message = f"Импортировано {imported_count} фильтров"
            if errors:
                message += f"\nОшибки: {len(errors)}"
            
            return True, message, imported_count
            
        except Exception as e:
            logging.error(f"Ошибка синхронизации из Google Sheets: {e}")
            return False, f"Ошибка синхронизации: {str(e)}", 0

# Создаем экземпляр синхронизации
google_sync = GoogleSheetsSync()

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
            
            # Автосинхронизация при добавлении
            if google_sync.auto_sync and google_sync.is_configured():
                filters = await get_user_filters(user_id)
                asyncio.create_task(google_sync.sync_to_sheets(user_id, filters))
            
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
            
            # Автосинхронизация при обновлении
            if google_sync.auto_sync and google_sync.is_configured():
                filters = await get_user_filters(user_id)
                asyncio.create_task(google_sync.sync_to_sheets(user_id, filters))
            
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
            
            # Автосинхронизация при удалении
            if google_sync.auto_sync and google_sync.is_configured():
                filters = await get_user_filters(user_id)
                asyncio.create_task(google_sync.sync_to_sheets(user_id, filters))
            
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

class GoogleSheetsStates(StatesGroup):
    waiting_sheet_id = State()
    waiting_sync_confirmation = State()

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
        "• ☁️ Синхронизация с Google Sheets\n"
        "• 🔔 Автоматические напоминания",
        reply_markup=get_main_keyboard(),
        parse_mode='HTML'
    )

# ... (остальные обработчики остаются без изменений до раздела импорта/экспорта)

# ========== ИМПОРТ/ЭКСПОРТ ==========
@dp.message(F.text == "📤 Импорт/Экспорт")
async def cmd_import_export(message: types.Message):
    """Меню импорта/экспорта"""
    health_monitor.record_message(message.from_user.id)
    
    await message.answer(
        "📤 <b>ИМПОРТ/ЭКСПОРТ ДАННЫХ</b>\n\n"
        "💾 <b>Доступные операции:</b>\n"
        "• 📤 Экспорт в Excel - выгрузка всех фильтров\n"
        "• 📥 Импорт из Excel - загрузка из файла\n"
        "• 📋 Шаблон Excel - скачать шаблон для импорта\n"
        "• ☁️ Синхронизация с Google Sheets\n\n"
        "💡 <i>Поддерживается работа с Excel и Google Sheets</i>",
        reply_markup=get_import_export_keyboard(),
        parse_mode='HTML'
    )

# ... (обработчики Excel импорта/экспорта остаются без изменений)

# ========== GOOGLE SHEETS СИНХРОНИЗАЦИЯ ==========
@dp.message(F.text == "☁️ Синхронизация с Google Sheets")
async def cmd_google_sheets_sync(message: types.Message):
    """Меню синхронизации с Google Sheets"""
    health_monitor.record_message(message.from_user.id)
    
    status_text = "☁️ <b>СИНХРОНИЗАЦИЯ С GOOGLE SHEETS</b>\n\n"
    
    if not GOOGLE_SHEETS_CREDENTIALS:
        status_text += "❌ <b>Статус:</b> Не настроены учетные данные\n"
        status_text += "💡 <i>Установите переменную GOOGLE_SHEETS_CREDENTIALS</i>\n\n"
    elif not google_sync.sheet_id:
        status_text += "🟡 <b>Статус:</b> Готов к настройке\n"
        status_text += "📝 <i>Укажите ID таблицы Google Sheets</i>\n\n"
    else:
        status_text += "🟢 <b>Статус:</b> Настроено\n"
        status_text += f"📊 <b>ID таблицы:</b> {google_sync.sheet_id}\n"
        status_text += f"🔄 <b>Автосинхронизация:</b> {'ВКЛ' if google_sync.auto_sync else 'ВЫКЛ'}\n\n"
    
    status_text += "💡 <b>Как получить ID таблицы:</b>\n"
    status_text += "1. Создайте таблицу в Google Sheets\n"
    status_text += "2. Скопируйте ID из URL: https://docs.google.com/spreadsheets/d/<b>[ID]</b>/edit\n"
    status_text += "3. Используйте кнопку '📝 Указать ID таблицы'"
    
    await message.answer(
        status_text,
        reply_markup=get_sync_keyboard(),
        parse_mode='HTML'
    )

@dp.message(F.text == "🔄 Синхронизировать с Google Sheets")
async def cmd_sync_to_sheets(message: types.Message):
    """Синхронизация данных с Google Sheets"""
    if not google_sync.is_configured():
        await message.answer(
            "❌ <b>Синхронизация не настроена</b>\n\n"
            "Пожалуйста, сначала настройте подключение к Google Sheets.",
            reply_markup=get_sync_keyboard(),
            parse_mode='HTML'
        )
        return
    
    await message.answer(
        "🔄 <b>Начинаю синхронизацию...</b>\n\n"
        "⏳ <i>Пожалуйста, подождите...</i>",
        reply_markup=get_back_keyboard(),
        parse_mode='HTML'
    )
    
    # Получаем фильтры пользователя
    filters = await get_user_filters(message.from_user.id)
    
    if not filters:
        await message.answer(
            "📭 <b>Нет данных для синхронизации</b>\n\n"
            "У вас пока нет фильтров.",
            reply_markup=get_sync_keyboard(),
            parse_mode='HTML'
        )
        return
    
    # Выполняем синхронизацию
    success, result_message = await google_sync.sync_to_sheets(message.from_user.id, filters)
    
    if success:
        await message.answer(
            f"✅ <b>СИНХРОНИЗАЦИЯ УСПЕШНА!</b>\n\n"
            f"{result_message}\n\n"
            f"💫 <i>Данные обновлены в Google Sheets</i>",
            reply_markup=get_sync_keyboard(),
            parse_mode='HTML'
        )
    else:
        await message.answer(
            f"❌ <b>ОШИБКА СИНХРОНИЗАЦИИ</b>\n\n"
            f"{result_message}\n\n"
            f"🔧 <i>Проверьте настройки подключения</i>",
            reply_markup=get_sync_keyboard(),
            parse_mode='HTML'
        )

@dp.message(F.text == "⚙️ Настройки синхронизации")
async def cmd_sync_settings(message: types.Message):
    """Настройки синхронизации"""
    if not GOOGLE_SHEETS_CREDENTIALS:
        await message.answer(
            "❌ <b>Учетные данные не настроены</b>\n\n"
            "Для использования синхронизации с Google Sheets необходимо:\n\n"
            "1. Создать сервисный аккаунт в Google Cloud Console\n"
            "2. Скачать JSON файл с ключами\n"
            "3. Установить переменную GOOGLE_SHEETS_CREDENTIALS\n\n"
            "💡 <i>Обратитесь к администратору для настройки</i>",
            reply_markup=get_sync_keyboard(),
            parse_mode='HTML'
        )
        return
    
    status_text = "⚙️ <b>НАСТРОЙКИ СИНХРОНИЗАЦИИ</b>\n\n"
    
    if google_sync.sheet_id:
        status_text += f"📊 <b>ID таблицы:</b> {google_sync.sheet_id}\n"
    else:
        status_text += "📊 <b>ID таблицы:</b> Не указан\n"
    
    status_text += f"🔄 <b>Автосинхронизация:</b> {'✅ ВКЛ' if google_sync.auto_sync else '❌ ВЫКЛ'}\n\n"
    status_text += "💡 <b>Автосинхронизация</b> автоматически обновляет данные в Google Sheets при любых изменениях фильтров."
    
    await message.answer(
        status_text,
        reply_markup=get_sync_settings_keyboard(),
        parse_mode='HTML'
    )

@dp.message(F.text == "📝 Указать ID таблицы")
async def cmd_set_sheet_id(message: types.Message, state: FSMContext):
    """Установка ID таблицы Google Sheets"""
    await state.set_state(GoogleSheetsStates.waiting_sheet_id)
    
    await message.answer(
        "📝 <b>УКАЖИТЕ ID ТАБЛИЦЫ GOOGLE SHEETS</b>\n\n"
        "🔗 <b>Как получить ID:</b>\n"
        "1. Откройте вашу таблицу в Google Sheets\n"
        "2. Скопируйте ID из URL адреса:\n"
        "   <code>https://docs.google.com/spreadsheets/d/[ВАШ_ID_ТУТ]/edit</code>\n\n"
        "📎 <b>Пример ID:</b> <code>1BxiMVs0XRA5nFMdKvBdBZjgmUUqptlbs74OgvE2upms</code>\n\n"
        "✏️ <b>Введите ID таблицы:</b>",
        reply_markup=get_cancel_keyboard(),
        parse_mode='HTML'
    )

@dp.message(GoogleSheetsStates.waiting_sheet_id)
async def process_sheet_id(message: types.Message, state: FSMContext):
    """Обработка ID таблицы"""
    if message.text == "❌ Отмена":
        await state.clear()
        await message.answer(
            "❌ <b>Настройка отменена</b>",
            reply_markup=get_sync_settings_keyboard(),
            parse_mode='HTML'
        )
        return
    
    sheet_id = message.text.strip()
    
    # Базовая валидация ID
    if len(sheet_id) < 10 or not re.match(r'^[a-zA-Z0-9-_]+$', sheet_id):
        await message.answer(
            "❌ <b>Неверный формат ID</b>\n\n"
            "ID таблицы должен содержать только буквы, цифры, дефисы и подчеркивания.\n"
            "Пожалуйста, введите корректный ID:",
            reply_markup=get_cancel_keyboard(),
            parse_mode='HTML'
        )
        return
    
    # Сохраняем ID
    google_sync.sheet_id = sheet_id
    google_sync.save_settings()
    
    await state.clear()
    
    await message.answer(
        f"✅ <b>ID ТАБЛИЦЫ СОХРАНЕН!</b>\n\n"
        f"📊 <b>ID:</b> {sheet_id}\n\n"
        f"💫 <i>Теперь вы можете синхронизировать данные</i>",
        reply_markup=get_sync_settings_keyboard(),
        parse_mode='HTML'
    )

@dp.message(F.text == "🔄 Автосинхронизация ВКЛ")
async def cmd_auto_sync_on(message: types.Message):
    """Включение автосинхронизации"""
    if not google_sync.sheet_id:
        await message.answer(
            "❌ <b>Сначала укажите ID таблицы</b>",
            reply_markup=get_sync_settings_keyboard(),
            parse_mode='HTML'
        )
        return
    
    google_sync.auto_sync = True
    google_sync.save_settings()
    
    await message.answer(
        "✅ <b>АВТОСИНХРОНИЗАЦИЯ ВКЛЮЧЕНА</b>\n\n"
        "💫 <i>Теперь данные будут автоматически обновляться в Google Sheets при любых изменениях фильтров</i>",
        reply_markup=get_sync_settings_keyboard(),
        parse_mode='HTML'
    )

@dp.message(F.text == "⏸️ Автосинхронизация ВЫКЛ")
async def cmd_auto_sync_off(message: types.Message):
    """Выключение автосинхронизации"""
    google_sync.auto_sync = False
    google_sync.save_settings()
    
    await message.answer(
        "⏸️ <b>АВТОСИНХРОНИЗАЦИЯ ВЫКЛЮЧЕНА</b>\n\n"
        "ℹ️ <i>Данные больше не будут автоматически обновляться в Google Sheets</i>",
        reply_markup=get_sync_settings_keyboard(),
        parse_mode='HTML'
    )

@dp.message(F.text == "🗑️ Отключить синхронизацию")
async def cmd_disable_sync(message: types.Message):
    """Полное отключение синхронизации"""
    google_sync.sheet_id = None
    google_sync.auto_sync = False
    google_sync.save_settings()
    
    await message.answer(
        "🗑️ <b>СИНХРОНИЗАЦИЯ ОТКЛЮЧЕНА</b>\n\n"
        "ℹ️ <i>Все настройки синхронизации сброшены</i>",
        reply_markup=get_sync_settings_keyboard(),
        parse_mode='HTML'
    )

@dp.message(F.text == "📊 Статус синхронизации")
async def cmd_sync_status(message: types.Message):
    """Показать статус синхронизации"""
    status_text = "📊 <b>СТАТУС СИНХРОНИЗАЦИИ</b>\n\n"
    
    # Проверка учетных данных
    if not GOOGLE_SHEETS_CREDENTIALS:
        status_text += "❌ <b>Учетные данные:</b> Не настроены\n"
    else:
        status_text += "✅ <b>Учетные данные:</b> Настроены\n"
    
    # Проверка ID таблицы
    if not google_sync.sheet_id:
        status_text += "❌ <b>ID таблицы:</b> Не указан\n"
    else:
        status_text += f"✅ <b>ID таблицы:</b> {google_sync.sheet_id}\n"
    
    # Статус автосинхронизации
    status_text += f"🔄 <b>Автосинхронизация:</b> {'✅ ВКЛ' if google_sync.auto_sync else '❌ ВЫКЛ'}\n\n"
    
    # Тест подключения
    if google_sync.is_configured():
        status_text += "🔍 <b>Тестирование подключения...</b>\n"
        
        try:
            # Пробуем инициализировать credentials
            if await google_sync.initialize_credentials():
                status_text += "✅ <b>Подключение:</b> Успешно\n"
            else:
                status_text += "❌ <b>Подключение:</b> Ошибка инициализации\n"
        except Exception as e:
            status_text += f"❌ <b>Подключение:</b> Ошибка: {str(e)}\n"
    else:
        status_text += "🔍 <b>Подключение:</b> Требуется настройка\n"
    
    await message.answer(
        status_text,
        reply_markup=get_sync_keyboard(),
        parse_mode='HTML'
    )

# ========== ОБРАБОТЧИКИ КНОПОК НАЗАД И ОТМЕНА ==========
@dp.message(F.text == "🔙 Назад")
async def cmd_back(message: types.Message, state: FSMContext):
    """Возврат в предыдущее меню"""
    current_state = await state.get_state()
    
    if current_state:
        await state.clear()
    
    # Определяем предыдущее меню на основе текущего состояния
    if current_state and "GoogleSheetsStates" in str(current_state):
        await message.answer(
            "🔙 <b>Возврат в меню синхронизации</b>",
            reply_markup=get_sync_keyboard(),
            parse_mode='HTML'
        )
    else:
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
    
    # Инициализация Google Sheets (если настроено)
    if GOOGLE_SHEETS_CREDENTIALS and google_sync.sheet_id:
        logging.info("Инициализация Google Sheets синхронизации...")
        await google_sync.initialize_credentials()
    
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
        logging.critical(f"Критическая ошибка при запуске: {e}")import logging
import logging.config
import os
import asyncio
import shutil
import re
import aiosqlite
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
from aiogram.utils.keyboard import ReplyKeyboardBuilder
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

# ========== НАСТРОЙКА ЛОГИРОВАНИЯ ==========
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('bot.log', encoding='utf-8'),
        logging.StreamHandler()
    ]
)

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

# ========== БАЗА ДАННЫХ ==========
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

async def init_db():
    """Инициализация базы данных"""
    try:
        async with get_db_connection() as conn:
            await conn.execute('''
                CREATE TABLE IF NOT EXISTS filters (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER,
                    filter_type TEXT,
                    location TEXT,
                    last_change DATE,
                    expiry_date DATE,
                    lifetime_days INTEGER,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            logging.info("База данных инициализирована")
    except Exception as e:
        logging.error(f"Ошибка инициализации БД: {e}")

async def get_user_filters(user_id: int) -> List[Dict]:
    """Получение фильтров пользователя"""
    try:
        async with get_db_connection() as conn:
            cursor = await conn.execute(
                "SELECT * FROM filters WHERE user_id = ? ORDER BY expiry_date", 
                (user_id,)
            )
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]
    except Exception as e:
        logging.error(f"Ошибка при получении фильтров: {e}")
        return []

async def add_filter_to_db(user_id: int, filter_type: str, location: str, last_change: str, expiry_date: str, lifetime_days: int) -> bool:
    """Добавление фильтра в БД"""
    try:
        async with get_db_connection() as conn:
            await conn.execute('''
                INSERT INTO filters (user_id, filter_type, location, last_change, expiry_date, lifetime_days)
                VALUES (?, ?, ?, ?, ?, ?)
            ''', (user_id, filter_type, location, last_change, expiry_date, lifetime_days))
            return True
    except Exception as e:
        logging.error(f"Ошибка при добавлении фильтра: {e}")
        return False

async def get_all_users_stats() -> Dict:
    """Получение статистики"""
    try:
        async with get_db_connection() as conn:
            cursor = await conn.execute('''
                SELECT 
                    COUNT(DISTINCT user_id) as total_users,
                    COUNT(*) as total_filters,
                    SUM(CASE WHEN expiry_date <= date('now') THEN 1 ELSE 0 END) as expired_filters,
                    SUM(CASE WHEN expiry_date BETWEEN date('now') AND date('now', '+7 days') THEN 1 ELSE 0 END) as expiring_soon
                FROM filters
            ''')
            result = await cursor.fetchone()
            return dict(result) if result else {
                'total_users': 0, 
                'total_filters': 0, 
                'expired_filters': 0, 
                'expiring_soon': 0
            }
    except Exception as e:
        logging.error(f"Ошибка при получении статистики: {e}")
        return {'total_users': 0, 'total_filters': 0, 'expired_filters': 0, 'expiring_soon': 0}

# ========== ВАЛИДАЦИЯ ==========
def validate_filter_type(filter_type: str) -> tuple[bool, str]:
    """Валидация типа фильтра"""
    if not filter_type or len(filter_type.strip()) == 0:
        return False, "Тип фильтра не может быть пустым"
    if len(filter_type) > 100:
        return False, "Тип фильтра слишком длинный"
    return True, "OK"

def validate_location(location: str) -> tuple[bool, str]:
    """Валидация местоположения"""
    if not location or len(location.strip()) == 0:
        return False, "Местоположение не может быть пустым"
    if len(location) > 50:
        return False, "Местоположение слишком длинное"
    return True, "OK"

def validate_lifetime(lifetime: str) -> tuple[bool, str, int]:
    """Валидация срока службы"""
    try:
        days = int(lifetime)
        if days <= 0:
            return False, "Срок службы должен быть положительным числом", 0
        if days > 3650:
            return False, "Срок службы не может превышать 10 лет", 0
        return True, "OK", days
    except ValueError:
        return False, "Срок службы должен быть числом", 0

def validate_date(date_str: str) -> datetime.date:
    """Валидация даты"""
    date_str = date_str.strip()
    date_str = re.sub(r'[/\-]', '.', date_str)
    
    formats = ['%d.%m.%y', '%d.%m.%Y', '%d.%m']
    
    for fmt in formats:
        try:
            if fmt == '%d.%m':
                date_obj = datetime.strptime(date_str, fmt).date()
                date_obj = date_obj.replace(year=datetime.now().year)
            else:
                date_obj = datetime.strptime(date_str, fmt).date()
            
            today = datetime.now().date()
            if date_obj > today:
                raise ValueError("Дата не может быть в будущем")
            if date_obj < today - timedelta(days=365*5):
                raise ValueError("Дата слишком старая")
                
            return date_obj
        except ValueError:
            continue
    
    raise ValueError("Неверный формат даты. Используйте ДД.ММ.ГГ или ДД.ММ")

# ========== STATES ==========
class FilterStates(StatesGroup):
    waiting_filter_type = State()
    waiting_location = State()
    waiting_change_date = State()
    waiting_lifetime = State()
    waiting_confirmation = State()

# ========== ИНИЦИАЛИЗАЦИЯ БОТА ==========
bot = Bot(token=API_TOKEN, default=DefaultBotProperties(parse_mode='HTML'))
storage = MemoryStorage()
dp = Dispatcher(storage=storage)

# ========== ОСНОВНЫЕ ОБРАБОТЧИКИ ==========
@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    """Обработчик команды /start"""
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
    await message.answer(
        "🌟 <b>Фильтр-Трекер - Помощь</b>\n\n"
        "📋 <b>Основные команды:</b>\n"
        "• /start - Начать работу\n"
        "• /help - Показать справку\n\n"
        "💡 <b>Как использовать:</b>\n"
        "1. Добавьте фильтры через меню\n"
        "2. Следите за сроками замены\n"
        "3. Получайте уведомления",
        reply_markup=get_main_keyboard(),
        parse_mode='HTML'
    )

# ========== ОБРАБОТЧИКИ КНОПОК ГЛАВНОГО МЕНЮ ==========
@dp.message(F.text == "📋 Мои фильтры")
async def cmd_my_filters(message: types.Message):
    """Показать фильтры пользователя"""
    logging.info(f"Пользователь {message.from_user.id} запросил фильтры")
    
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
async def cmd_add_filter(message: types.Message):
    """Добавление фильтра - меню"""
    logging.info(f"Пользователь {message.from_user.id} начал добавление фильтра")
    await message.answer(
        "🔧 <b>Выберите тип добавления:</b>\n\n"
        "💡 <i>Добавьте новый фильтр в систему</i>",
        reply_markup=get_add_filter_keyboard(),
        parse_mode='HTML'
    )

@dp.message(F.text == "➕ Один фильтр")
async def cmd_add_single_filter(message: types.Message, state: FSMContext):
    """Начало добавления одного фильтра"""
    await state.set_state(FilterStates.waiting_filter_type)
    await message.answer(
        "💧 <b>Выберите тип фильтра:</b>\n\n"
        "💡 <i>Используйте кнопки для быстрого выбора или введите свой вариант</i>",
        reply_markup=get_filter_type_keyboard(),
        parse_mode='HTML'
    )

@dp.message(F.text == "⚙️ Управление фильтрами")
async def cmd_manage_filters(message: types.Message):
    """Управление фильтрами"""
    logging.info(f"Пользователь {message.from_user.id} открыл управление фильтрами")
    
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
        reply_markup=get_management_keyboard(),
        parse_mode='HTML'
    )

@dp.message(F.text == "📊 Статистика")
async def cmd_stats(message: types.Message):
    """Показать статистику"""
    logging.info(f"Пользователь {message.from_user.id} запросил статистику")
    
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

@dp.message(F.text == "📤 Импорт/Экспорт")
async def cmd_import_export(message: types.Message):
    """Меню импорта/экспорта"""
    await message.answer(
        "📤 <b>ИМПОРТ/ЭКСПОРТ ДАННЫХ</b>\n\n"
        "💾 <b>Доступные операции:</b>\n"
        "• 📤 Экспорт в Excel - выгрузка всех фильтров\n"
        "• 📥 Импорт из Excel - загрузка из файла\n"
        "• 📋 Шаблон Excel - скачать шаблон для импорта\n\n"
        "💡 <i>Поддерживается работа с Excel файлами</i>",
        reply_markup=get_import_export_keyboard(),
        parse_mode='HTML'
    )

# ========== ОБРАБОТЧИКИ ДОБАВЛЕНИЯ ФИЛЬТРА ==========
@dp.message(FilterStates.waiting_filter_type)
async def process_filter_type(message: types.Message, state: FSMContext):
    """Обработка типа фильтра"""
    if message.text == "🔙 Назад":
        await state.clear()
        await message.answer("🔙 <b>Возврат в меню добавления</b>", reply_markup=get_add_filter_keyboard(), parse_mode='HTML')
        return
    
    filter_type = message.text.strip()
    
    if filter_type == "Другой тип фильтра":
        await message.answer(
            "💧 <b>Введите свой тип фильтра:</b>\n\n"
            "💡 <i>Например: Барьер, Атолл, Брита и т.д.</i>",
            reply_markup=get_back_keyboard(),
            parse_mode='HTML'
        )
        return
    
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
        await state.set_state(FilterStates.waiting_lifetime)
        
        await message.answer(
            "⏱️ <b>Введите срок службы фильтра (в днях):</b>\n\n"
            "💡 <i>Например: 180 (6 месяцев) или 365 (1 год)</i>\n"
            "📝 <i>Стандартные сроки: Магистральные - 180 дней, Картриджи - 365 дней</i>",
            reply_markup=get_back_keyboard(),
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
    
    is_valid, error_msg, lifetime = validate_lifetime(message.text)
    if not is_valid:
        await message.answer(f"❌ {error_msg}\n\nПожалуйста, введите корректное число дней:")
        return
    
    # Расчет даты истечения
    change_date = user_data['change_date']
    expiry_date = change_date + timedelta(days=lifetime)
    
    # Сохраняем все данные
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

# ========== ОБРАБОТЧИКИ КНОПОК НАЗАД ==========
@dp.message(F.text == "🔙 Назад")
async def cmd_back(message: types.Message, state: FSMContext):
    """Возврат в главное меню"""
    current_state = await state.get_state()
    if current_state:
        await state.clear()
    
    await message.answer(
        "🔙 <b>Возврат в главное меню</b>",
        reply_markup=get_main_keyboard(),
        parse_mode='HTML'
    )

# ========== ЗАПУСК БОТА ==========
async def main():
    """Основная функция запуска бота"""
    if not API_TOKEN:
        logging.error("Токен бота не найден!")
        return
    
    # Инициализация БД
    await init_db()
    
    logging.info("Бот запускается...")
    
    try:
        await dp.start_polling(bot)
    except Exception as e:
        logging.error(f"Ошибка при запуске бота: {e}")

if __name__ == '__main__':
    asyncio.run(main())
