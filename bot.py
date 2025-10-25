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

# ========== КОНФИГУРАЦИЯ ==========
class Config:
    """Класс конфигурации приложения"""
    
    def __init__(self):
        self.API_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
        self.ADMIN_ID = int(os.getenv('ADMIN_ID', '5024165375'))
        self.GOOGLE_SHEETS_CREDENTIALS = os.getenv('GOOGLE_SHEETS_CREDENTIALS')
        self.GOOGLE_SHEET_ID = os.getenv('GOOGLE_SHEET_ID')
        
        # Настройки базы данных
        self.DB_PATH = 'filters.db'
        self.BACKUP_ENABLED = True
        
        # Настройки rate limiting
        self.RATE_LIMIT_MAX_REQUESTS = 10
        self.RATE_LIMIT_WINDOW = 30
        
        # Настройки уведомлений
        self.REMINDER_CHECK_INTERVAL = 24 * 60 * 60  # 24 часа
        self.EARLY_REMINDER_DAYS = 7
        
    def validate(self) -> bool:
        """Проверка корректности конфигурации"""
        if not self.API_TOKEN:
            raise ValueError("TELEGRAM_BOT_TOKEN не установлен")
        return True

# Создаем экземпляр конфигурации
config = Config()

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

def get_recommended_lifetime_keyboard(default_lifetime: int):
    """Клавиатура с рекомендуемым сроком службы"""
    builder = ReplyKeyboardBuilder()
    builder.button(text=f"✅ Использовать рекомендуемый ({default_lifetime} дней)")
    builder.button(text="🔙 Назад")
    builder.adjust(1)
    return builder.as_markup(resize_keyboard=True)

def get_filters_selection_keyboard(filters: List[Dict], action: str):
    """Клавиатура для выбора фильтра"""
    builder = ReplyKeyboardBuilder()
    for f in filters:
        builder.button(text=f"#{f['id']} - {f['filter_type']} ({f['location']})")
    builder.button(text="🔙 Назад")
    builder.adjust(1)
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
    return user_id == config.ADMIN_ID

def backup_database() -> bool:
    """Создание резервной копии базы данных"""
    try:
        if os.path.exists(config.DB_PATH):
            backup_name = f'filters_backup_{datetime.now().strftime("%Y%m%d_%H%M%S")}.db'
            shutil.copy2(config.DB_PATH, backup_name)
            logging.info(f"Создана резервная копия: {backup_name}")
            return True
    except Exception as e:
        logging.error(f"Ошибка при создании резервной копии: {e}")
    return False

# ========== УЛУЧШЕНИЕ: ВАЛИДАЦИЯ И БЕЗОПАСНОСТЬ ==========
def sanitize_input(text: str) -> str:
    """Санитизация пользовательского ввода"""
    if not text:
        return text
    
    # Удаляем потенциально опасные символы
    sanitized = re.sub(r'[<>&\"\']', '', text)
    return sanitized.strip()

def validate_user_id(user_id: int) -> bool:
    """Валидация ID пользователя"""
    return isinstance(user_id, int) and user_id > 0

async def check_user_permission(user_id: int, filter_id: int) -> bool:
    """Проверка прав пользователя на фильтр"""
    try:
        filter_data = await get_filter_by_id(filter_id, user_id)
        return filter_data is not None
    except Exception:
        return False

def validate_filter_type(filter_type: str) -> tuple[bool, str]:
    """Валидация типа фильтра"""
    filter_type = sanitize_input(filter_type)
    
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
    location = sanitize_input(location)
    
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

# ========== УЛУЧШЕНИЕ: РЕТРИ МЕХАНИЗМЫ ==========
async def execute_with_retry(func: Callable, max_retries: int = 3, delay: float = 1.0, *args, **kwargs):
    """Выполнение функции с повторными попытками"""
    for attempt in range(max_retries):
        try:
            return await func(*args, **kwargs)
        except Exception as e:
            if attempt == max_retries - 1:
                raise e
            logging.warning(f"Попытка {attempt + 1} не удалась: {e}. Повтор через {delay} сек...")
            await asyncio.sleep(delay)

# ========== ИСПРАВЛЕННАЯ GOOGLE SHEETS ИНТЕГРАЦИЯ ==========
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
        return bool(self.sheet_id and config.GOOGLE_SHEETS_CREDENTIALS)
    
    async def initialize_credentials(self):
        """Инициализация учетных данных Google"""
        try:
            if not config.GOOGLE_SHEETS_CREDENTIALS:
                return False
            
            # Парсим JSON credentials из переменной окружения
            credentials_info = json.loads(config.GOOGLE_SHEETS_CREDENTIALS)
            
            # Импортируем здесь, чтобы не требовать установку если не используется
            try:
                import gspread
                from google.oauth2.service_account import Credentials
            except ImportError:
                logging.error("Библиотеки gspread или google-auth не установлены")
                return False
            
            # Создаем credentials с правильными scope
            scope = [
                'https://www.googleapis.com/auth/spreadsheets',
                'https://www.googleapis.com/auth/drive.file'
            ]
            self.credentials = Credentials.from_service_account_info(credentials_info, scopes=scope)
            return True
            
        except Exception as e:
            logging.error(f"Ошибка инициализации Google Sheets: {e}")
            return False
    
    async def create_new_spreadsheet(self, gc, title: str):
        """Создание новой таблицы"""
        try:
            spreadsheet = gc.create(title)
            
            # Даем доступ для чтения/записи всем, у кого есть ссылка
            spreadsheet.share(None, perm_type='anyone', role='writer')
            
            return spreadsheet
        except Exception as e:
            logging.error(f"Ошибка создания таблицы: {e}")
            return None
    
    async def sync_to_sheets(self, user_id: int, user_filters: List[Dict]) -> tuple[bool, str]:
        """Синхронизация данных с Google Sheets"""
        try:
            if not self.is_configured():
                return False, "Синхронизация не настроена. Укажите ID таблицы и настройте учетные данные."
            
            if not self.credentials:
                if not await self.initialize_credentials():
                    return False, "Ошибка инициализации Google API. Проверьте учетные данные."
            
            import gspread
            
            # Создаем клиент
            gc = gspread.authorize(self.credentials)
            
            try:
                # Пытаемся открыть таблицу
                sheet = gc.open_by_key(self.sheet_id)
            except gspread.exceptions.SpreadsheetNotFound:
                return False, "Таблица не найдена. Проверьте ID таблицы."
            except gspread.exceptions.APIError as e:
                error_msg = str(e)
                if "PERMISSION_DENIED" in error_msg:
                    return False, "Нет доступа к таблице. Убедитесь, что сервисный аккаунт имеет доступ к таблице."
                elif "NOT_FOUND" in error_msg:
                    return False, "Таблица не найдена. Проверьте ID таблицы."
                else:
                    return False, f"Ошибка доступа к таблице: {error_msg}"
            
            # Получаем или создаем лист для пользователя
            worksheet_name = f"User_{user_id}"
            try:
                worksheet = sheet.worksheet(worksheet_name)
            except gspread.exceptions.WorksheetNotFound:
                try:
                    worksheet = sheet.add_worksheet(title=worksheet_name, rows=100, cols=10)
                except Exception as e:
                    return False, f"Ошибка создания листа: {str(e)}"
                
                # Заголовки
                headers = ['ID', 'Тип фильтра', 'Местоположение', 'Дата замены', 
                          'Срок службы (дни)', 'Годен до', 'Статус', 'Осталось дней']
                try:
                    worksheet.append_row(headers)
                except Exception as e:
                    return False, f"Ошибка добавления заголовков: {str(e)}"
            
            # Очищаем старые данные (кроме заголовка)
            try:
                if worksheet.row_count > 1:
                    worksheet.delete_rows(2, worksheet.row_count)
            except Exception as e:
                logging.warning(f"Ошибка очистки данных: {e}")
            
            # Подготавливаем данные
            today = datetime.now().date()
            rows = []
            
            for f in user_filters:
                try:
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
                except Exception as e:
                    logging.error(f"Ошибка подготовки данных фильтра {f['id']}: {e}")
                    continue
            
            # Добавляем данные
            if rows:
                try:
                    worksheet.append_rows(rows)
                except Exception as e:
                    return False, f"Ошибка добавления данных: {str(e)}"
            
            # Форматируем таблицу
            try:
                # Заголовки жирным
                worksheet.format('A1:H1', {'textFormat': {'bold': True}})
                
                # Авто-ширина колонок
                worksheet.columns_auto_resize(0, 7)
            except Exception as format_error:
                logging.warning(f"Ошибка форматирования таблицы: {format_error}")
                # Не прерываем выполнение из-за ошибки форматирования
            
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
            try:
                sheet = gc.open_by_key(self.sheet_id)
            except gspread.exceptions.SpreadsheetNotFound:
                return False, "Таблица не найдена", 0
            except gspread.exceptions.APIError as e:
                return False, f"Ошибка доступа к таблице: {str(e)}", 0
            
            # Получаем лист пользователя
            worksheet_name = f"User_{user_id}"
            try:
                worksheet = sheet.worksheet(worksheet_name)
            except gspread.exceptions.WorksheetNotFound:
                return False, "Таблица для пользователя не найдена", 0
            
            # Читаем данные
            try:
                data = worksheet.get_all_records()
            except Exception as e:
                return False, f"Ошибка чтения данных: {str(e)}", 0
            
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

    async def test_connection(self) -> tuple[bool, str]:
        """Тестирование подключения к Google Sheets"""
        try:
            if not self.is_configured():
                return False, "Синхронизация не настроена"
            
            if not self.credentials:
                if not await self.initialize_credentials():
                    return False, "Ошибка инициализации Google API"
            
            import gspread
            
            # Создаем клиент
            gc = gspread.authorize(self.credentials)
            
            # Пытаемся открыть таблицу
            try:
                sheet = gc.open_by_key(self.sheet_id)
                # Пытаемся получить список листов
                worksheets = sheet.worksheets()
                return True, f"Подключение успешно. Найдено листов: {len(worksheets)}"
            except gspread.exceptions.SpreadsheetNotFound:
                return False, "Таблица не найдена. Проверьте ID таблицы."
            except gspread.exceptions.APIError as e:
                error_msg = str(e)
                if "PERMISSION_DENIED" in error_msg:
                    return False, "Нет доступа к таблице. Убедитесь, что сервисный аккаунт имеет доступ к таблице."
                else:
                    return False, f"Ошибка доступа: {error_msg}"
                    
        except Exception as e:
            return False, f"Ошибка подключения: {str(e)}"

# Создаем экземпляр синхронизации
google_sync = GoogleSheetsSync()

# ========== УЛУЧШЕННЫЙ МОНИТОРИНГ ЗДОРОВЬЯ ==========
class EnhancedHealthMonitor:
    def __init__(self):
        self.start_time = datetime.now()
        self.message_count = 0
        self.error_count = 0
        self.user_actions = {}
        self.db_operations = 0
        self.sync_operations = 0
        self.user_sessions = {}
    
    def record_message(self, user_id: int):
        """Запись сообщения пользователя"""
        self.message_count += 1
        if user_id not in self.user_actions:
            self.user_actions[user_id] = 0
        self.user_actions[user_id] += 1
    
    def record_error(self):
        """Запись ошибки"""
        self.error_count += 1
    
    def record_db_operation(self):
        """Запись операции с БД"""
        self.db_operations += 1
    
    def record_sync_operation(self):
        """Запись операции синхронизации"""
        self.sync_operations += 1
    
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
    
    async def get_detailed_status(self):
        """Получение детального статуса"""
        basic_status = await self.get_health_status()
        basic_status.update({
            'db_operations': self.db_operations,
            'sync_operations': self.sync_operations,
            'active_sessions': len(self.user_sessions),
            'database_size': await self.get_database_size(),
            'memory_usage': self.get_memory_usage()
        })
        return basic_status
    
    async def get_database_size(self):
        """Получение размера базы данных"""
        try:
            if os.path.exists(config.DB_PATH):
                return os.path.getsize(config.DB_PATH)
            return 0
        except:
            return 0
    
    def get_memory_usage(self):
        """Получение использования памяти"""
        try:
            import psutil
            process = psutil.Process()
            return process.memory_info().rss / 1024 / 1024  # MB
        except ImportError:
            return 0

health_monitor = EnhancedHealthMonitor()

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

rate_limiter = RateLimiter(max_requests=config.RATE_LIMIT_MAX_REQUESTS, window=config.RATE_LIMIT_WINDOW)

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
    token=config.API_TOKEN,
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
    conn = await aiosqlite.connect(config.DB_PATH)
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
            health_monitor.record_db_operation()
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
            health_monitor.record_db_operation()
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
            health_monitor.record_db_operation()
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
            
            health_monitor.record_db_operation()
            
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
            
            health_monitor.record_db_operation()
            
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
            
            health_monitor.record_db_operation()
            
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

# ========== УЛУЧШЕНИЕ: МИГРАЦИИ БАЗЫ ДАННЫХ ==========
async def check_and_update_schema():
    """Проверка и обновление схемы базы данных"""
    try:
        async with get_db_connection() as conn:
            # Проверяем существование колонок
            cur = await conn.cursor()
            await cur.execute("PRAGMA table_info(filters)")
            columns = [row[1] for row in await cur.fetchall()]
            
            # Добавляем недостающие колонки
            if 'created_at' not in columns:
                await cur.execute("ALTER TABLE filters ADD COLUMN created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP")
                logging.info("Добавлена колонка created_at")
            
            if 'updated_at' not in columns:
                await cur.execute("ALTER TABLE filters ADD COLUMN updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP")
                logging.info("Добавлена колонка updated_at")
            
            # Создаем недостающие индексы
            await cur.execute("CREATE INDEX IF NOT EXISTS idx_user_expiry ON filters(user_id, expiry_date)")
            
    except Exception as e:
        logging.error(f"Ошибка при обновлении схемы БД: {e}")

# ========== ЭКСПОРТ В EXCEL ==========
async def export_to_excel(user_id: int) -> io.BytesIO:
    """Экспорт фильтров в Excel"""
    filters = await get_user_filters(user_id)
    
    if not filters:
        raise ValueError("Нет данных для экспорта")
    
    # Создаем DataFrame
    df = pd.DataFrame(filters)
    
    # Удаляем служебные колонки
    columns_to_drop = ['user_id', 'created_at', 'updated_at']
    for col in columns_to_drop:
        if col in df.columns:
            df = df.drop(columns=[col])
    
    # Добавляем вычисляемые поля
    today = datetime.now().date()
    df['last_change'] = pd.to_datetime(df['last_change']).dt.strftime('%d.%m.%Y')
    df['expiry_date'] = pd.to_datetime(df['expiry_date']).dt.strftime('%d.%m.%Y')
    
    # Добавляем статус
    def calculate_status(expiry_date_str):
        expiry_date = datetime.strptime(expiry_date_str, '%d.%m.%Y').date()
        days_until = (expiry_date - today).days
        icon, status = get_status_icon_and_text(days_until)
        return f"{icon} {status} ({days_until} дней)"
    
    df['Статус'] = df['expiry_date'].apply(calculate_status)
    
    # Создаем Excel файл в памяти
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        df.to_excel(writer, sheet_name='Фильтры', index=False)
    
    output.seek(0)
    return output

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
        if os.path.exists(config.DB_PATH):
            backup_name = f'filters_backup_critical_{datetime.now().strftime("%Y%m%d_%H%M%S")}.db'
            try:
                shutil.copy2(config.DB_PATH, backup_name)
                logging.info(f"Создана критическая резервная копие: {backup_name}")
            except Exception as backup_error:
                logging.error(f"Не удалось создать резервную копию: {backup_error}")
        raise

# ========== ОБРАБОТЧИК ОШИБОК ==========
async def error_handler(update: types.Update, exception: Exception):
    """Глобальный обработчик ошибок"""
    try:
        # Логируем ошибку
        logging.error(f"Ошибка при обработке update {update}: {exception}")
        health_monitor.record_error()
        
        # Уведомляем администратора
        if config.ADMIN_ID:
            error_traceback = "".join(traceback.format_exception(None, exception, exception.__traceback__))
            short_error = str(exception)[:1000]
            
            await bot.send_message(
                config.ADMIN_ID,
                f"🚨 <b>КРИТИЧЕСКАЯ ОШИБКА</b>\n\n"
                f"💥 <b>Ошибка:</b> {short_error}\n"
                f"📱 <b>Update:</b> {update}\n\n"
                f"🔧 <i>Подробности в логаз</i>",
                parse_mode='HTML'
            )
        
        # Пользователю показываем дружелюбное сообщение
        if update.message:
            await update.message.answer(
                "😕 <b>Произошла непредвиденная ошибка</b>\n\n"
                "Пожалуйста, попробуйте еще раз или обратитесь к администратору.",
                reply_markup=get_main_keyboard(),
                parse_mode='HTML'
            )
            
    except Exception as e:
        logging.critical(f"Ошибка в обработчике ошибок: {e}")

# ========== СИСТЕМА НАПОМИНАНИЙ ==========
async def send_personalized_reminders():
    """Персонализированные напоминания с учетом времени суток"""
    while True:
        try:
            # Получаем текущий час для персонализации
            current_hour = datetime.now().hour
            greeting = "Доброе утро" if 5 <= current_hour < 12 else "Добрый день" if 12 <= current_hour < 18 else "Добрый вечер"
            
            async with get_db_connection() as conn:
                cur = await conn.cursor()
                await cur.execute('''
                    SELECT DISTINCT user_id FROM filters 
                    WHERE expiry_date BETWEEN date('now') AND date('now', '+7 days')
                    OR expiry_date <= date('now')
                ''')
                users_to_notify = await cur.fetchall()
                
                for user_row in users_to_notify:
                    user_id = user_row['user_id']
                    filters = await get_user_filters(user_id)
                    
                    expiring_filters = []
                    expired_filters = []
                    
                    for f in filters:
                        expiry_date = datetime.strptime(str(f['expiry_date']), '%Y-%m-%d').date()
                        days_until = (expiry_date - datetime.now().date()).days
                        
                        if days_until <= 0:
                            expired_filters.append((f, days_until))
                        elif days_until <= 7:
                            expiring_filters.append((f, days_until))
                    
                    if expired_filters or expiring_filters:
                        message = f"{greeting}! 🔔\n\n"
                        
                        if expired_filters:
                            message += "🔴 <b>ПРОСРОЧЕННЫЕ ФИЛЬТРЫ:</b>\n"
                            for f, days in expired_filters:
                                message += f"• {f['filter_type']} ({f['location']}) - ПРОСРОЧЕН\n"
                            message += "\n"
                        
                        if expiring_filters:
                            message += "🟡 <b>СКОРО ИСТЕКАЮТ:</b>\n"
                            for f, days in expiring_filters:
                                message += f"• {f['filter_type']} ({f['location']}) - {days} дней\n"
                        
                        message += f"\n💫 Всего фильтров: {len(filters)}"
                        
                        try:
                            await bot.send_message(user_id, message, parse_mode='HTML')
                            await asyncio.sleep(0.2)  # Увеличиваем задержку
                        except Exception as e:
                            logging.warning(f"Не удалось отправить напоминание пользователю {user_id}: {e}")
            
            await asyncio.sleep(23 * 60 * 60)  # Проверяем каждые 23 часа
            
        except Exception as e:
            logging.error(f"Ошибка в задаче напоминаний: {e}")
            await asyncio.sleep(60 * 60)

# ========== МОНИТОРИНГ ЗДОРОВЬЯ ==========
async def health_monitoring_task():
    """Фоновая задача мониторинга здоровья"""
    while True:
        try:
            health_status = await health_monitor.get_detailed_status()
            
            # Логируем каждые 30 минут
            if health_status['message_count'] % 30 == 0:
                logging.info(f"Статус здоровья: {health_status}")
            
            # Уведомляем администратора при низком health score
            if health_status['health_score'] < 80 and config.ADMIN_ID:
                await bot.send_message(
                    config.ADMIN_ID,
                    f"⚠️ <b>НИЗКИЙ HEALTH SCORE</b>\n\n"
                    f"📊 Текущий score: {health_status['health_score']:.1f}%\n"
                    f"💥 Ошибок: {health_status['error_count']}\n"
                    f"📨 Сообщений: {health_status['message_count']}",
                    parse_mode='HTML'
                )
            
            await asyncio.sleep(60 * 30)  # Проверяем каждые 30 минут
            
        except Exception as e:
            logging.error(f"Ошибка в задаче мониторинга: {e}")
            await asyncio.sleep(60 * 5)

# ========== ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ДЛЯ УПРАВЛЕНИЯ ==========
async def show_filters_for_selection(message: types.Message, filters: List[Dict], action: str):
    """Показать фильтры для выбора"""
    if not filters:
        await message.answer("❌ Нет фильтров для выбора")
        return
    
    text = f"📋 <b>ВЫБЕРИТЕ ФИЛЬТР ДЛЯ {action.upper()}:</b>\n\n"
    
    for f in filters:
        expiry_date = datetime.strptime(str(f['expiry_date']), '%Y-%m-%d').date()
        days_until = (expiry_date - datetime.now().date()).days
        icon, status = get_status_icon_and_text(days_until)
        
        text += (
            f"{icon} <b>#{f['id']}</b> - {f['filter_type']}\n"
            f"📍 {f['location']} | 📅 {format_date_nice(expiry_date)} | {status}\n\n"
        )
    
    await message.answer(
        text,
        reply_markup=get_filters_selection_keyboard(filters, action),
        parse_mode='HTML'
    )

# ========== БЕЗОПАСНАЯ СИНХРОНИЗАЦИЯ ==========
async def safe_sync_to_sheets(user_id: int, filters: List[Dict]) -> tuple[bool, str]:
    """Безопасная синхронизация с обработкой ошибок"""
    try:
        return await google_sync.sync_to_sheets(user_id, filters)
    except ImportError:
        return False, "Библиотеки Google не установлены. Установите: pip install gspread google-auth"
    except Exception as e:
        logging.error(f"Ошибка синхронизации: {e}")
        return False, f"Ошибка синхронизации: {str(e)}"

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

@dp.message(F.text == "🔙 Назад")
async def cmd_back(message: types.Message, state: FSMContext):
    """Обработка кнопки Назад"""
    current_state = await state.get_state()
    
    if current_state:
        await state.clear()
    
    await message.answer(
        "🔙 <b>Возврат в главное меню</b>",
        reply_markup=get_main_keyboard(),
        parse_mode='HTML'
    )

@dp.message(Command("admin"))
async def cmd_admin(message: types.Message):
    """Админ панель"""
    if not is_admin(message.from_user.id):
        await message.answer("❌ Доступ запрещен")
        return
    
    health_status = await health_monitor.get_detailed_status()
    stats = await get_all_users_stats()
    
    admin_text = (
        "👑 <b>АДМИН ПАНЕЛЬ</b>\n\n"
        f"📊 <b>Статистика системы:</b>\n"
        f"• 👥 Пользователей: {stats['total_users']}\n"
        f"• 💧 Фильтров: {stats['total_filters']}\n"
        f"• 🔴 Просрочено: {stats['expired_filters']}\n"
        f"• 🟡 Скоро истечет: {stats['expiring_soon']}\n\n"
        f"🖥️ <b>Статус бота:</b>\n"
        f"• ⏱ Аптайм: {health_status['uptime']}\n"
        f"• 📨 Сообщений: {health_status['message_count']}\n"
        f"• 💥 Ошибок: {health_status['error_count']}\n"
        f"• 🧠 Память: {health_status['memory_usage']:.1f} MB\n"
        f"• 💾 Размер БД: {health_status['database_size'] / 1024 / 1024:.2f} MB\n"
        f"• 🏥 Health: {health_status['health_score']:.1f}%\n\n"
        f"🔧 <b>Действия:</b>\n"
        f"/backup - Создать резервную копию\n"
        f"/stats - Детальная статистика"
    )
    
    await message.answer(admin_text, parse_mode='HTML')

@dp.message(Command("backup"))
async def cmd_backup(message: types.Message):
    """Создание резервной копии"""
    if not is_admin(message.from_user.id):
        return
    
    await message.answer("🔄 Создание резервной копии...")
    
    if backup_database():
        await message.answer("✅ Резервная копия создана успешно")
    else:
        await message.answer("❌ Ошибка при создании резервной копии")

@dp.message(F.text == "📋 Мои фильтры")
async def cmd_my_filters(message: types.Message):
    """Показать фильтры пользователя"""
    health_monitor.record_message(message.from_user.id)
    
    filters = await get_user_filters(message.from_user.id)
    
    if not filters:
        await message.answer(
            "📭 <b>У вас пока нет фильтров</b>\n\n"
            "Используйте кнопку '✨ Добавить фильтр' чтобы добавить первый фильтр.",
            reply_markup=get_main_keyboard(),
            parse_mode='HTML'
        )
        return
    
    today = datetime.now().date()
    response = ["📋 <b>ВАШИ ФИЛЬТРЫ:</b>\n"]
    
    for i, f in enumerate(filters, 1):
        expiry_date = datetime.strptime(str(f['expiry_date']), '%Y-%m-%d').date()
        days_until = (expiry_date - today).days
        icon, status = get_status_icon_and_text(days_until)
        
        response.append(
            f"{icon} <b>Фильтр #{f['id']}</b>\n"
            f"💧 Тип: {f['filter_type']}\n"
            f"📍 Место: {f['location']}\n"
            f"📅 Замена: {format_date_nice(datetime.strptime(str(f['last_change']), '%Y-%m-%d'))}\n"
            f"⏰ Годен до: {format_date_nice(expiry_date)}\n"
            f"📊 Статус: {status} ({days_until} дней)\n"
        )
    
    # Добавляем инфографику
    response.append("\n" + create_expiry_infographic(filters))
    
    await message.answer("\n".join(response), parse_mode='HTML')

@dp.message(F.text == "✨ Добавить фильтр")
async def cmd_add_filter(message: types.Message, state: FSMContext):
    """Начало добавления фильтра"""
    health_monitor.record_message(message.from_user.id)
    
    # Проверяем health_monitor.record_message(message.from_user.id)
    
    # Проверяем лимит фильтров
    filters = await get_user_filters(message.from_user.id)
    if len(filters) >= MAX_FILTERS_PER_USER:
        лимит фильтров
    filters = await get_user_filters(message.from_user.id)
    if len(filters) >= MAX_FILTERS_PER_USER await message.answer(
            f"❌ <b>Достигнут лимит филь:
        await message.answer(
            f"❌ <b>Достигнут лимиттров</b>\n\n"
            f"Максимальное количество фильтров: {MAX_FILTERS_PER_USER}\n"
            f" фильтров</b>\n\n"
            f"Максимальное количество фильтров: {MAX_FILTERS_PER_USER}\n"
            f"Удалите некоторые фильтры чтобы добавить новые.",
            replyУдалите некоторые фильтры чтобы добавить новые.",
            reply_markup=get_main_keyboard(),
            parse_markup=get_main_keyboard(),
            parse_mode='HTML'
        )
        return
    
    await state.set_state(FilterStates.waiting_filter_type)
    await message.answer(
        "💧 <b>ВЫБЕРИТЕ ТИП ФИЛЬТРА</b>\_mode='HTML'
        )
        return
    
    await state.set_state(FilterStates.waiting_filter_type)
    await message.answer(
        "💧 <b>ВЫБЕРИТЕ ТИП ФИЛЬТРА</b>\n\n"
        "n\n"
        "Вы можете выбрать из популярных типовВы можете выбрать из популярных типов или указать свой:",
        reply_mark или указать свой:",
        reply_markup=get_filter_type_keyboard(),
        parse_mode='HTML'
    )

@dp.message(FilterStates.waiting_filter_type)
async def process_filter_type(message: types.Message, state: FSMContext):
    """Обработка типа фильтра"""
    if message.text == "🔙 Назад":
        await state.clear()
        await message.answer(
            "🔙 <b>Возврат в главное меню</up=get_filter_type_keyboard(),
        parse_mode='HTML'
    )

@dp.message(FilterStates.waiting_filter_type)
async def process_filter_type(message: types.Message, state: FSMContext):
    """Обработка типа фильтра"""
    if message.text == "🔙 Назад":
        await state.clear()
        await message.answer(
            "🔙 <b>Возврат в главное меню</b>",
           b>",
            reply_markup=get_main_keyboard(),
 reply_markup=get_main_keyboard(),
            parse_mode='HTML'
        )
        return
    
    filter_type = message.text
    
    # Валидация
            parse_mode='HTML'
        )
        return
    
    filter_type = message.text
    
    # Валидация
    is    is_valid, error_msg = validate_filter_type(filter_type)
    if not is_valid:
        await message_valid, error_msg = validate_filter_type(filter_type)
    if not is_valid:
        await message.answer(
            f"❌ {error_msg}\n\nПожа.answer(
            f"❌ {error_msg}\n\nПожалуйста, влуйста, введите тип фильтра еще раз:",
            replyведите тип фильтра еще раз:",
            reply_markup=get_markup=get_filter_type_keyboard()
        )
        return
    
    await state.update_data(filter_type=filter_type)
    await state.set_state(Filter_filter_type_keyboard()
        )
        return
    
    await state.update_data(filter_type=filter_type)
    await state.set_state(FilterStates.waiting_location)
    
    await message.answer(
        "📍 <b>УКАЖИТЕ МЕСТОПОЛОЖЕНИЕ</b>\n\n"
        "Примеры:\n"
        "•States.waiting_location)
    
    await message.answer(
        "📍 <b>УКАЖИТЕ МЕСТОПОЛОЖЕНИЕ</b>\n\n"
        "Примеры:\n"
        "• Кухня\n"
        "• Офис кабинет 101\n"
        "• Производственный цех\n"
        "• Гостиная\n\n"
        " Кухня\n"
        "• Офис кабинет 101\n"
        "• Производственный цех\n"
        "• Гостиная\n\n"
        "✏️ <b>Введите местоположение:</b>✏️ <b>Введите местоположение:</b>",
        reply_markup=get_back_keyboard(),
       ",
        reply_markup=get_back_keyboard(),
        parse_mode='HTML'
    )

@dp.message(FilterStates.wait parse_mode='HTML'
    )

@dp.message(FilterStates.waiting_location)
async def process_location(message: types.Message, state: FSMContext):
ing_location)
async def process_location(message: types.Message, state: FSMContext):
    """Обработка местоположения"""
    if message.text == "🔙 Назад":
        await state.set_state(FilterStates.waiting_filter_type)
        await message.answer(
            "💧    """Обработка местоположения"""
    if message.text == "🔙 Назад":
        await state.set_state(FilterStates.waiting_filter_type)
        await message.answer(
            " <b>ВЫБЕРИТЕ ТИП ФИ💧 <b>ВЫБЕРИТЕ ТИП ФИЛЬТРА</b>",
            reply_markup=get_filter_type_keyboard(),
            parse_mode='ЛЬТРА</b>",
            reply_markup=get_filter_type_keyboard(),
            parse_mode='HTML'
        )
        return
    
    location = message.text
    
    # Валидация
    is_valid, error_msg = validate_location(location)
    if not is_valid:
        await message.answer(
            f"❌ {error_msg}\n\nПHTML'
        )
        return
    
    location = message.text
    
    # Валидация
    is_valid, error_msg = validate_location(location)
    if not is_valid:
        await message.answer(
            f"❌ {error_msg}\n\nПожалуйста, введите местоположение еще раз:",
ожалуйста, введите местоположение еще раз:",
            reply_markup=get_back_keyboard()
        )
        return
    
    await state.update_data(l            reply_markup=get_back_keyboard()
        )
        return
    
    await state.update_data(location=location)
    await state.set_stateocation=location)
    await state.set_state(FilterStates.waiting_change_date)
    
    await message(FilterStates.waiting_change_date)
    
    await message.answer.answer(
        "📅 <b>УКАЖИТ(
        "📅 <bЕ ДАТУ ПОСЛЕДНЕЙ ЗАМЕНЫ</b>\n\n"
        "Формат: <b>ДД.ММ.ГГГГ</b> или <b>ДД.ММ</b>\n"
        "Примеры:\n"
        "• 15.12.2023\n"
        "• 15.12 (те>УКАЖИТЕ ДАТУ ПОСЛЕДНЕЙ ЗАМЕНЫ</b>\n\n"
        "Формат: <b>ДД.ММ.ГГГГ</b> или <b>ДД.ММ</b>\n"
        "Примеры:\n"
        "• 15.12.2023\n"
        "• 15.12 (текущий год)\n"
        "• 15122023кущий год)\n"
        "• 15122023\n\n"
        "✏️ <b>Введите дату замены:</b>",
        reply_markup=get_back_keyboard\n\n"
        "✏️ <b>Введите дату замены:</b>",
        reply_markup=get_back_keyboard(),
(),
        parse_mode='HTML'
    )

@dp.message(FilterStates.waiting_change_date        parse_mode='HTML'
    )

@dp.message(FilterStates.waiting_change_date)
async def process_change_date(message)
async def process_change_date(message: types.Message, state:: types.Message, state: FSMContext):
    """Обработка даты замены"""
    if message.text == "🔙 Назад":
        await state.set_state(FilterStates.waiting_location)
        await message.answer(
            "📍 <b>У FSMContext):
    """Обработка даты замены"""
    if message.text == "🔙 Назад":
        await state.set_state(FilterStates.waiting_location)
        await message.answer(
            "📍 <b>УКАЖИТЕ МЕСТОПОЛОЖЕНИЕКАЖИТЕ МЕСТОПОЛ</b>",
            reply_markup=get_back_keyboard(),
            parse_mode='HTML'
        )
        returnОЖЕНИЕ</b>",
            reply_markup=get_back_keyboard(),
            parse_mode='HTML'
        )
        return
    
    try:

    
    try:
        change_date = validate_date(message.text)
        await state.update_data(last_change        change_date = validate_date(message.text)
        await state.update_data(last_change==change_date.strftime('%Y-%m-%d'))
        
        data = await state.get_data()
        filter_type = data.get('change_date.strftime('%Y-%m-%d'))
        
        data = await state.get_data()
        filter_type = data.get('filter_type', ''filter_type', '').lower()
        
        # Предлагаем срок службы по умолча).lower()
        
        # Предлагаем срок службы по умолчанию
        default_lifetime = DEFAULT_LIFETIMES.get(filter_type, 180)
        
        await state.update_data(lifetime_days=default_lifetime)
        await state.set_state(Filterнию
        default_lifetime = DEFAULT_LIFETIMES.get(filter_type, 180)
        
        await state.update_data(lifetime_days=default_lifetime)
        await state.set_state(FilterStates.waiting_lifetime)
        
        await message.answer(
            f"⏱️ <b>УКАЖИТЕ СРОК СЛУStates.waiting_lifetime)
        
        await message.answer(
            f"⏱️ <b>УКАЖИТЕ СРОК СЛУЖБЫ</b>\n\n"
            f"ЖБЫ</b>\n\n"
            f"Для типа '{data.get('filter_type', '')}' рекомендуемый срокДля типа '{data.get('filter_type', '')}' рекомендуемый: <b>{ срок: <b>{default_lifetime} дней</b>\n\n"
            f"✏️ <bdefault_lifetime} дней</b>\n\n"
            f"✏️ <b>Введите срок службы в днях:</b>\n"
            f"<i>>Введите срок службы в днях:</b>\n"
            f"<Или нажмите '✅ Использовать рекомендуемый'</i>i>Или нажмите '✅ Использовать рекомендуемый'</",
            reply_markup=get_recommended_lifetime_keyboard(default_lifetime),
            parse_mode='HTML'
        )
        
    except ValueError as e:
        await messagei>",
            reply_markup=get_recommended_lifetime_keyboard(default_lifetime),
            parse_mode='HTML'
        )
        
    except ValueError as e:
        await message.answer(
            f"❌ {str(e)}\n\nПожалуйста, введите дату в правильном.answer(
            f"❌ {str(e)}\n\nПожалуйста, введите формате:",
            reply_markup=get_back_keyboard()
        )

@dp.message(FilterStates.waiting_lifetime)
async def дату в правильном формате:",
            reply_markup=get_back_keyboard()
        )

@dp.message(FilterStates.waiting_lifetime)
async def process_lifetime(message: process_lifetime(message: types.Message, state: FSMContext):
    """Обработка срока службы"""
    types.Message, state: FSMContext):
    """Обработка срока службы"""
    if message.text == "🔙 Назад":
        await state.set_state(FilterStates if message.text == "🔙 Назад":
        await state.set_state(FilterStates.waiting_change_date)
        await message.answer(
            "📅.waiting_change_date)
        await message.answer(
            "📅 <b>УКАЖИТЕ ДАТУ ПОСЛЕДНЕЙ ЗАМЕН <b>УКАЖИТЕ ДАТУ ПОСЛЕДНЕЙ ЗАМЕНЫ</b>",
Ы</b>",
            reply_markup=get_back_keyboard(),
            reply_markup=get_back_keyboard(),
            parse_mode='HTML'
        )
        return
    
    data = await state.get_data()
    default_lifetime =            parse_mode='HTML'
        )
        return
    
    data = await state.get_data()
    default_lifetime = data data.get('lifetime_days', 180)
    
    if "Использовать рекомендуемый" in message.text:
        lifetime_days = default_lifetime
    else:
        is_valid, error_msg, lifetime_d.get('lifetime_days', 180)
    
    if "Использовать рекомендуемый" in message.text:
        lifetime_days = default_lifetime
    else:
        is_valid, error_msg, lifetime_days = validate_lifetime(message.text)
        if not is_valid:
ays = validate_lifetime(message.text)
        if not is_valid:
            await            await message.answer(
                f"❌ {error_msg}\n\nПожалуйста, введите корректный срок службы:",
 message.answer(
                f"❌ {error_msg}\n\nПожалуйста, введите корректный срок службы                reply_markup=get_recommended_lifetime_keyboard(default_lifetime)
            )
            return
    
    await state.update:",
                reply_markup=get_recommended_lifetime_keyboard(default_lifetime)
            )
            return
    
    await state.update_data(lifetime_days=lifetime_days)
_data(lifetime_days=lifetime_days)
    
    # Рассчитываем дату истечения
    last_change = datetime.strptime    
    # Рассчитываем дату истечения
    last_change = datetime.strptime(data['(data['last_change'], '%Y-%m-%d')
    expiry_date = last_change + timedelta(days=lifetime_days)
    await state.updatelast_change'], '%Y-%m-%d')
    expiry_date = last_change + timedelta(days=lifetime_days)
    await state.update_data(expiry_date=expiry_date.strftime('%Y-%_data(expiry_date=expiry_date.strftime('%Y-%m-%d'))
    
    # Показываем подтверждение
    datam-%d'))
    
    # Показываем подтверждение
    data = await state.get_data()
    await show_filter_confirmation(message, data)
 = await state.get_data()
    await show_filter_confirmation(message, data)
    await state.set_state(FilterStates.waiting    await state.set_state(FilterStates.waiting_confirmation)

async def show_filter_confirmation(message: types.Message, data: Dict):
_confirmation)

async def show_filter_confirmation(message: types.Message, data: Dict):
    """Показать подтверждение добавления фильтра"""
    last_change =    """Показать подтверждение добавления фильтра"""
 datetime.strptime(data['last_change'], '%Y-%m-%d')
    expiry_date = datetime.strptime    last_change = datetime.strptime(data['last_change'], '%Y-%m-%d')
    expiry_date = datetime.strptime(data['expiry_date'], '%Y-%m-%d')
   (data['expiry_date'], '%Y-%m-%d')
    days_until = (exp days_until = (expiry_date.date() - datetime.now().date()).days
    icon, status = get_status_icon_and_text(days_until)
    
    confirmation_text = (
iry_date.date() - datetime.now().date()).days
    icon, status = get_status_icon_and_text(days_until)
    
    confirmation_text = (
        f"✅ <b>ПОДТВЕР        f"✅ <b>ПОДТВЕРЖДЕНИЕ ДАННЫХЖДЕНИЕ ДАННЫХ</b>\n</b>\n\n"
        f"{icon} <b>Новый фильтр:</b\n"
        f"{icon} <b>Новый фильтр:</b>\n"
        f"💧 <b>Тип:</b> {>\n"
        f"💧 <b>Тип:</b> {data['filter_type']}\n"
        f"📍 <bdata['filter_type']}\n"
        f"📍 <>Местоположение:</b> {data['location']}\n"
        f"📅 <b>b>Местоположение:</b> {data['location']}\n"
        f"📅 <b>Дата замены:</b> {format_date_nice(last_change)}\n"
        f"⏱️ <b>Срок службы:</b> {data['lifetime_dДата замены:</b> {format_date_nice(last_change)}\n"
        f"⏱️ <b>Срок службы:</b> {data['lifetime_days']} дней\n"
        f"⏰ <b>ays']} дней\n"
        f"⏰ <b>ГоденГоден до:</b> {format_date_nice(expiry_date)}\n"
        f"📊 <b>Статус:</b до:</b> {format_date_nice(expiry_date)}\n"
        f"📊> {status} ({days_until} дней)\n\n"
        f"<i>Всё вер <b>Статус:</b> {status} ({days_until} дней)\n\n"
        f"<i>Всё верно?</i>"
    )
    
    await message.answer(confirmation_text, reply_markup=get_confirmation_keyно?</i>"
    )
    
    await message.answer(confirmation_text, reply_markup=get_confirmation_keyboard(), parse_mode='HTML')

@dp.message(FilterStates.waiting_confirmation)
async defboard(), parse_mode='HTML')

@dp.message(FilterStates.waiting_confirmation)
async def process process_confirmation(message: types.Message, state: FSMContext):
    """Обработка_confirmation(message: types.Message, state: FSMContext):
    """Обработка подтверждения"""
    if message.text == "✅ Да, всё верно":
        data = await подтверждения"""
    if message.text == "✅ Да, всё верно":
        data = await state.get_data()
        
        success = state.get_data()
        
        success = await add_filter_to_db(
            user_id=message.from_user.id,
            filter_type=data['filter_type'],
            location=data['location'],
            await add_filter_to_db(
            user_id=message.from_user.id,
            filter_type=data['filter_type'],
            location=data['location'],
            last_change=data[' last_change=data['last_change'],
            expiry_date=data['expiry_date'],
            lifetime_days=data['lifetime_days']
        )
        
last_change'],
            expiry_date=data['expiry_date'],
            lifetime_days=data['lifetime_days']
        )
        if success:
            await message.answer(
                "🎉 <b>ФИЛЬТР УСПЕШНО ДОБАВЛ        
        if success:
            await message.answer(
                "🎉 <b>ФИЛЬТР УСПЕШНО ДОБАВЕН!</b>\n\n"
                "💫 <i>Теперь он будет отслеживаться в системеЛЕН!</b>\n\n"
                "💫 <i>Теперь он будет отслеживаться в</i>",
                reply_markup=get_main_keyboard(),
                parse_mode=' системе</i>",
                reply_markup=get_main_keyboard(),
                parse_mode='HTML'
            )
        else:
           HTML'
            )
        else:
            await message.answer(
                "❌ <b>ОШИБКА ПРИ ДОБАВЛЕНИИ ФИЛЬТРА</b>\n await message.answer(
                "❌ <b>ОШИБКА ПРИ ДОБАВЛЕНИИ ФИЛЬТРА</b>\n\n"
                "Пожалуйста, попробу\n"
                "Пожалуйста, попробуйте еще раз.",
                reply_markйте еще раз.",
                reply_markup=get_main_keyboard(),
                parse_mode='HTML'
            )
        
        await state.clear()
up=get_main_keyboard(),
                parse_mode='HTML'
            )
        
        await state.clear()
        
    elif message.text == "❌ Нет, изменить":
               
    elif message.text == "❌ Нет, изменить":
        await state.set_state(FilterStates.waiting_filter_type)
        await message await state.set_state(FilterStates.waiting_filter_type)
        await message.answer(
            "💧 <b>ВЫБЕРИТЕ ТИ.answer(
            "💧 <b>ВЫБЕРИТЕ ТИП ФИЛЬТРА</b>",
            reply_markup=get_filter_type_keyboard(),
            parse_mode='HTMLП ФИЛЬТРА</b>",
            reply_markup=get_filter_type_keyboard(),
            parse_mode='HTML'
        )
    else:
        await message.answer(
            "'
        )
    else:
        await message.answer(
            "Пожалуйста, выбеПожалуйста, выберите вариант:",
            reply_markup=get_confirmation_keyboard()
        )

@dp.message(F.text == "📊 Статистика")
asyncрите вариант:",
            reply_markup=get_confirmation_keyboard()
        )

@dp.message(F.text == "📊 Статистика")
async def cmd_statistics(message: types.Message):
    """Показать статистику"""
    health_monitor.record_message(message.from_user.id)
    
 def cmd_statistics(message: types.Message):
    """Показать статистику"""
    health_monitor.record_message(message.from_user.id)
    
    # Статистика пользователя
    user_filters = await get_user_filters(message.from_user.id)
    
       # Статистика пользователя
    user_filters = await get_user_filters(message.from_user.id)
    
    if not user_filters:
        await message.answer(
            " if not user_filters:
        await message.answer(
📊 <b>СТАТИСТИКА</b>\n\n"
            "📭 <b>У вас пока нет фильтров</            "📊 <b>СТАТИСТИКА</b>\n\n"
            "📭 <b>У вас пока нет фильтровb>\n\n"
            "Добавьте первый фильтр чтобы увидеть статисти</b>\n\n"
            "Добавьте первый фильтр чтобы увидеть статистику.",
            reply_markup=get_main_keyboard(),
            parse_mode='HTML'
       ку.",
            reply_mark )
        return
    
    today = datetime.now().date()
    user_stats = {
        'total': len(user_filters),
        'expired': 0,
        'expiring_soonup=get_main_keyboard(),
            parse_mode='HTML'
        )
        return
    
    today = datetime.now().date()
    user_stats = {
        'total': len(user_filters),
        'expired': 0,
        'expiring_soon': 0,
       ': 0,
        'normal': 0 'normal': 0
    }
    
    for f in user_filters:
        expiry_date = datetime.strptime(str(f['expiry_date']), '%Y-%
    }
    
    for f in user_filters:
        expiry_date = datetime.strptime(str(f['expiry_date']), '%Y-%m-%d').date()
        days_until = (expiry_date -m-%d').date()
        days_until = (expiry_date - today).days
        
        if days_until <= today).days
        
        if days_until <= 0:
            user_stats['expired'] += 1
        elif days_until <= 7:
 0:
            user_stats['expired'] += 1
        elif days_until <= 7:
            user_stats['expiring_soon'] +=            user_stats['expiring_soon'] += 1
        else:
            user_stats['normal'] += 1
    
    # 1
        else:
            user_stats['normal'] += 1
    
    # Общая статистика (только для администратора)
    if is_admin Общая статистика (только для администратора)
    if is_admin(message.from_user.id):
        global_stats = await get_all_users_stats()
        stats(message.from_user.id):
        global_stats = await get_all_users_stats()
        stats_text = (
            f"📊 <b_text = (
            f"📊 <b>ОБЩА>ОБЩАЯ СТАТИСТИКАЯ СТАТИСТИКА СИСТЕМЫ</b>\n\n"
            f"👥 <b> СИСТЕМЫ</b>\n\n"
            f"Пользователей:</b> {global_stats['total_users']}\n"
            f"💧 <b>Всего фильтров:</b> {👥 <b>Пользователей:</b> {global_stats['total_users']}\n"
            f"💧 <b>Всего фильтров:</b> {global_stats['total_filters']}\n"
            f"🔴 <b>Просроченоglobal_stats['total_filters']}\n"
            f"🔴 <b>Просрочено:</b> {global_stats['expired_filters']}\n"
            f":</b> {global_stats['expired_filters']}\n"
            f"🟡 <b>Скоро исте🟡 <b>Скоро истекает:</b> {global_stats['expiring_soon']}\n\n"
кает:</b> {global_stats['expiring_soon']}\n\n"
        )
    else:
        stats_text = ""
    
    stats_text        )
    else:
        stats_text = ""
    
    stats_text += (
        f" += (
        f"📊 <b>ВАША СТАТИСТИКА📊 <b>ВАША СТАТИСТИКА</b>\n\n"
        f"</b>\n\n"
        f"💧 <b>Всего фильтров:</b> {user_stats['total']}\n"
        f"🟢💧 <b>Всего фильтров:</b> {user_stats['total']}\n"
        f"🟢 <b>В норме:</b> {user_stats['normal']}\n"
        f"🟡 <b>Скоро истекает:</ <b>В норме:</b> {user_stats['normal']}\n"
        f"🟡 <b>Скоро истекает:</b> {user_stats['expiring_soon']}\n"
        f"🔴b> {user_stats['expiring_soon']}\n"
        f"🔴 <b <b>Просрочено:</b> {user_stats['expired']}\n\n"
        f"📈 <b>Процент>Просрочено:</b> {user_stats['expired']}\n\n"
        f"📈 <b>Процент исправных:</b> {((user_stats['normal исправных:</b> {((user_stats['normal'] / user_stats['total']) * 100):.1f}%"
    )
    
    await message.answer(stats_text, reply_mark'] / user_stats['total']) * 100):.1f}%"
    )
    
    await message.answer(stats_text, reply_markup=get_main_keyboard(), parse_mode='HTML')

# ==========up=get_main_keyboard(), parse_mode='HTML')

# ========== УПРАВЛЕНИЕ ФИЛЬТРАМИ ==========
@dp.message УПРАВЛЕНИЕ ФИЛЬТРАМИ ==========
@dp.message(F.text == "⚙️ Управление фильтрами")
async def cmd_management(message: types.Message):
    """(F.text == "⚙️ Управление фильтрами")
async def cmd_management(message: types.Message):
    """Меню управления фильтрами"""
    health_monitor.recordМеню управления фильтрами"""
    health_monitor.record_message(message.from_user.id)
    
    filters = await get_user_filters(message.from_user.id)
_message(message.from_user.id)
    
    filters = await get_user_filters(message.from_user.id    
    if not filters:
        await message.)
    
    if not filters:
        await message.answeranswer(
            "⚙️ <(
            "⚙️ <b>УПРАВb>УПРАВЛЕНИЕ ФИЛЬТРАМИ</b>\n\n"
            "📭 <b>У вас пока нет фильтров</b>\n\n"
            "Добавьте первый фильтр чтобы использовать функции управления.",
            reply_markupЛЕНИЕ ФИЛЬТРАМИ</b>\n\n"
            "📭 <b>У вас пока нет фильтров</b>\n\n"
            "Добавьте первый фильтр чтобы использовать функции управления.",
            reply_markup=get_main_keyboard(),
            parse_mode='HTML'
        )
        return
    
    await message.answer(
        "⚙️ <b>УПРАВЛЕНИЕ ФИЛЬТРАМИ</=get_main_keyboard(),
            parse_mode='HTML'
        )
        return
    
    await message.answer(
        "⚙️ <b>УПРАВЛЕНИЕ ФИЛЬТРАМИ</b>\n\n"
        "💡 <b>Доступные операции:</b>\b>\n\n"
        "💡 <b>Доступныеn"
        "• ✏️ Редактировать фильтр - изменить данные фильтра\n"
        "• операции:</b>\n"
        "• ✏️ Редактировать фильтр - изменить данные фильтра\n"
        "• 🗑 🗑️ Удалить фильтр - удалить фильтр из системы\n"
        "• 📊 Онлайн Excel - работа с️ Удалить фильтр - удалить фильтр из системы\n"
        "• 📊 Онлайн Excel - работа с Excel файлами\n\n"
        f"📊 <b>Всего фильтров Excel файлами\n\n"
        f"📊 <b>Всего фильтров:</b> {len(filters)}",
        reply_markup=get_management_keyboard:</b> {len(filters)}",
        reply_markup=get_management_keyboard(),
        parse_mode='HTML'
(),
        parse_mode='HTML'
    )

@dp.message(F.text == "✏️ Редактировать фильтр")
async def    )

@dp.message(F.text == "✏️ Редактировать фильтр")
async def cmd_edit_filter(message: types.Message, state: FSMContext):
    """Начало редактирования фильтра"""
    filters = cmd_edit_filter(message: types.Message, state: FSMContext):
    """Начало редактирования фильтра"""
    filters = await await get_user_filters(message.from_user.id)
    if not filters:
        await message.answer("❌ Нет фильтров для редактирования")
 get_user_filters(message.from_user.id)
    if not filters:
        await message.answer("❌ Нет фильтров для редактирования")
               return
    
    await show_filters_for_selection(message, filters, "edit")
    await state.set_state(EditFilterStates.waiting_filter_selection)

@ return
    
    await show_filters_for_selection(message, filters, "edit")
    await state.set_state(EditFilterStates.waiting_filter_selection)

dp.message(F.text == "🗑️ Удалить фильтр")
async def cmd_delete_filter(message: types.Message, state: F@dp.message(F.text == "🗑️ Удалить фильтр")
async def cmd_delete_filter(message: types.Message, state: FSMContext):
    """Начало удаления фильтра"""
    filters = await get_user_fSMContext):
    """Начало удаления фильтра"""
    filters = await get_user_filters(message.from_user.id)
    if not filters:
        await message.answer("❌ Нilters(message.from_user.id)
    if not filters:
        await message.answer("❌ Нет фильтров для удает фильтров для удаления")
        return
    
    await show_filters_for_selection(message, filters, "delete")
    await state.set_state(DeleteFilterStates.waiting_filter_selection)

#ления")
        return
    
    await show_filters_for_selection(message, filters, "delete")
    await state.set_state(DeleteFilterStates.waiting_filter_selection)

 ========== ОБРАБОТЧИКИ РЕДАКТ# ========== ОБРАБОТЧИКИ РЕДАКТИРОВАНИЯ ФИЛЬТРОВ ==========
@dp.message(EditFilterStates.waiting_filter_selection)
async def process_edit_filter_selection(message: types.Message, state: FSMContext):
    """ИРОВАНИЯ ФИЛЬТРОВ ==========
@dp.message(EditFilterStates.waiting_filter_selection)
async def process_edit_filter_selection(message: types.Message, state: FSMContext):
    """Обработка выбора фильтра для редактирования"""
   Обработка выбора фильтра для редактирования"""
    if message.text == "🔙 Назад":
        await state.clear()
        await message. if message.text == "🔙 Назад":
        await state.clear()
        await messageanswer("🔙 Возврат в меню управления", reply_markup=get_management_keyboard())
        return
    
    # Извлека.answer("🔙 Возврат в меню управления", reply_markup=get_management_keyboard())
        return
    
    # Извлекаем ID фильтра из текста
    match = re.search(r'#(\d+)',ем ID фильтра из текста
    match = re.search(r'#(\d+)', message.text)
    if not match:
        await message.answer("❌ Не message.text)
    if not match:
        await message.answer("❌ Неверный формат. Выберите фильтр из списка:")
        return
    
верный формат. Выберите фильтр из списка:")
        return
    
    filter_id = int(match.group(1))
    
    # Проверяем существование фильтра
    filter_data = await get_filter_by_id(filter_id, message    filter_id = int(match.group(1))
    
    # Проверяем существование фильтра
    filter_data = await get_filter_by_id(filter_id, message.from_user.id)
    if not filter_data:
        await message.answer("❌ Фи.from_user.id)
    if not filter_data:
        await message.answer("❌ Фильтр не найден. Выберите другой:")
        return
    
    await state.update_data(editing_filter_id=filter_id, editing_filter_data=filter_data)
    await state.set_state(льтр не найден. Выберите другой:")
        return
    
    await state.update_data(editing_filter_id=filter_id, editing_filter_data=filter_data)
    await state.set_state(EditFilterStates.waiting_field_selection)
    
    await message.answer(
EditFilterStates.waiting_field_selection)
    
    await message.answer(
        f"✏️ <b>РЕДАКТИРОВАНИЕ ФИЛЬ        f"✏️ <b>РЕДАКТИРОВАНИЕ ФИЛЬТРА #{filter_id}</b>\n\n"
        f"ТРА #{filter_id}</b>\n\n"
        f"💧 Тип: {filter_data['filter_type']}\n"
        f"📍 Место: {filter_data['location']}\n"
💧 Тип: {filter_data['filter_type']}\n"
        f"📍 Место: {filter_data['location']}\n"
        f"📅 Дата замены: {format_date_nice(datetime.strptime        f"📅 Дата замены: {format_date_nice(datetime.strptime(str(str(filter_data['last_change']), '%Y(filter_data['last_change']), '%Y-%m-%d'))}\n\n"
        f"📝 <b>Выберите поле для редактирования-%m-%d'))}\n\n"
        f"📝 <b>Выберите поле для редактирования:</b>",
        reply_markup=get_edit_keyboard(),
        parse_mode='HTML'
    )

@dp:</b>",
        reply_markup=get_edit_keyboard(),
        parse_mode='HTML'
    )

@dp.message(EditFilterStates.waiting_field_selection)
async def process_edit_field_selection(message.message(EditFilterStates.waiting_field_selection)
async def process_edit_field_selection: types.Message, state: FSMContext):
    """Обработка выбора поля для редактирования"""
    if message.text == "(message: types.Message, state: FSMContext):
    """Обработка выбора поля для редактирования"""
    if message.text == "🔙 Назад":
        await state.clear()
        await cmd_management🔙 Назад":
        await state.clear()
        await(message)
        return
    
    field_mapping = {
        "💧 Тип фильтра": "filter_type",
        "📍 Местоположение": cmd_management(message)
        return
    
    field_mapping = {
        "💧 Тип фильтра": "filter_type",
        "📍 Местоположение": "location "location", 
        "📅 Дата замены": "last_change",
        "⏱️ Срок", 
        "📅 Дата замены": "last_change",
        "⏱️ Срок службы": "lifetime_days"
    }
    
    if message.text not in field_mapping:
        await службы": "lifetime_days"
    }
    
    if message.text not in field_mapping:
        await message.answer("❌ Выберите поле из списка:", reply_m message.answer("❌ Выберите поле из списка:", reply_markup=get_edit_keyboard())
        return
    
    field_name = field_mappingarkup=get_edit_keyboard())
        return
    
    field_name = field_mapping[message.text]
    await state.update_data(editing_field=field_name[message.text]
    await state.update_data(editing_field=field_name)
    await state.set_state(EditFilterStates.waiting_new_value)
    
    prompts)
    await state.set_state(EditFilterStates.waiting_new_value)
    
    prompts = {
        "filter_type": " = {
        "filter_type": "💧 Введите новый тип фильтра💧 Введите новый тип фильтра:",
        "location": "📍 Введите новое местоположение:",
        "last_change": "📅 В:",
        "location": "📍 Введите новое местоположение:",
        "last_change": "📅 Введите новую дату замены (ДД.ММ.ГГГГ):",
        "lifetime_daysведите новую дату замены (ДД.ММ.ГГГГ):",
        "lifetime_days": "⏱️ Введите новый срок службы (в днях):": "⏱️ Введите новый срок службы (в днях):"
    }
    
    await message.answer(prompts[field_name],"
    }
    
    await message.answer(prompts[field_name], reply_m reply_markup=get_back_keyboard())

@dp.message(EditFilterStates.waiting_new_value)
async def process_edit_new_value(message:arkup=get_back_keyboard())

@dp.message(EditFilterStates.waiting_new_value)
async def process_edit_new_value(message: types.Message, state: FSMContext):
    """Обработка нового types.Message, state: FSMContext):
    """Обработ значения для редактирования"""
    if message.text == "🔙 Назад":
        await state.set_state(EditFilterStates.waiting_field_selection)
        dataка нового значения для редактирования"""
    if message.text == "🔙 Назад":
        await state.set_state(EditFilterStates.waiting_field_selection)
        data = await state.get_data()
        filter_data = data['editing = await state.get_data()
        filter_data = data['editing_filter_data']
        
        await message.answer(
            f"✏️ <b>_filter_data']
        
        await message.РЕДАКТИРОВАНИЕ ФИЛЬТРА #{data['editing_filter_id']}</b>\answer(
            f"✏️ <b>РЕДАКТИРОВАНИЕ ФИЛЬТРА #{data['editing_filter_id']}</b>\n\n"
            f"📝 <b>Выберите поле для редактирования:</n\n"
            f"📝 <b>Выберите поле для редактирования:</b>",
            reply_markup=get_edit_keyboard(),
            parse_mode='HTML'
        )
        return
    
    data =b>",
            reply_markup=get_edit_keyboard(),
            parse_mode='HTML'
        )
        return
    
    data = await state.get_data()
    field_name = data['editing_field']
    filter_id = data['editing_filter_id']
    user_id = await state.get_data()
    field_name = data['editing_field']
    filter_id = data['editing_filter_id']
    user_id = message.from_user.id
    
    try:
        update_data = {}
        
        message.from_user.id
    
    try:
        update_data = {}
        
        if field_name == "filter_type":
            is_valid, error_msg = validate_filter_type if field_name == "filter_type":
            is_valid, error_msg(message.text)
            if not is_valid:
                await message.answer(f"❌ {error_msg}\n\nВведите корректный тип филь = validate_filter_type(message.text)
            if not is_valid:
                await message.answer(f"❌ {error_msg}\n\nВведите корректный тип фильтра:")
                return
            update_data['filter_type'] = message.text
            
тра:")
                return
            update_data['filter_type'] = message.text
            
        elif field_name == "location":
            is_valid, error_msg = validate_location(message.text)
        elif field_name == "location":
            is_valid, error_msg = validate_location(message.text)
            if not is_valid:
                await message.answer(f"❌ {error_msg}\n            if not is_valid:
\nВведите корректное местоположение:")
                return
            update_data['location'] = message.text
            
                await message.answer(f"❌ {error_msg}\n\nВведите корректное местоположение:")
                return
            update_data['location'] = message.text
            
        elif field_name == "last_change":
            try:
                change_date = validate_date(message.text)
                update        elif field_name == "last_change":
            try:
                change_date = validate_date(message.text)
                update_data['last_change'] = change_date.strftime('%Y-%m-%d')
_data['last_change'] = change_date.strftime('%Y-%m-%d')
                
                # Пересчитываем дату истечения                
                # Пересчитываем дату истечения
                filter_data = await get_filter_by_id(filter_id, user_id)
                if filter_data
                filter_data = await get_filter_by_id(filter_id, user_id)
                if filter_data:
                    expiry_date = change_date + timedelta(days=filter:
                    expiry_date = change_date + timedelta(days=filter_data['lifetime_days'])
                    update_data['expiry_date'] =_data['lifetime_days'])
                    update_data['expiry_date'] = expiry_date.strftime('%Y-%m-%d')
                    
            except ValueError as e:
                await message.answer(f"❌ {str(e)}\n\nВведите корре expiry_date.strftime('%Y-%m-%d')
                    
            except ValueError as e:
                await message.answer(f"❌ {str(e)}\n\nВведите корректную дату:")
                return
                
        elif field_nameктную дату:")
                return
                
        elif field_name == "lifetime_days":
            is_valid, error_msg, lifetime_days = validate_lifetime(message.text)
            == "lifetime_days":
            is_valid, error_msg, lifetime_days = validate_lifetime(message.text)
            if not is_valid:
                await message.answer(f"❌ {error_msg}\n\nВведите корректный срок службы:")
                return
            
            if not is_valid:
                await message.answer(f"❌ {error_msg}\n\nВведите корректный срок службы:")
                return
            
            update update_data['lifetime_days'] = lifetime_days
            
            # Пересчитываем дату истечения
            filter_data = await get_filter_by_id(filter_id,_data['lifetime_days'] = lifetime_days
            
            # Пересчитываем дату истечения
            filter_data = await get user_id)
            if filter_data:
                last_change = datetime.strptime(str(filter_data['last_change']), '%Y-%m-%d')
                expiry_date_filter_by_id(filter_id, user_id)
            if filter_data:
                last_change = datetime.strptime(str(filter_data['last_change']), '%Y-%m-%d')
                expiry_date = last_change + timedelta(days=lifetime_days)
                update_data['expiry_date = last_change + timedelta(days=lifetime_days)
                update_data['expiry_date'] = expiry_date.strftime('%Y-%m-%d')
        
        # Обновляем фильтр в БД
        success = await update_filter_in_db'] = expiry_date.strftime('%Y-%m-%d')
        
        # Обновляем фильтр в БД
        success = await update_filter_in(filter_id, user_id, **update_data)
        
        if success:
            await message.answer(
                f"✅ <b>ФИЛЬТР_db(filter_id, user_id, **update_data)
        
        if success:
            await message.answer(
                f"✅ <b>ФИЛЬТР УСПЕШНО ОБНОВЛЕН!</b>\n\n"
 УСПЕШНО ОБНОВЛЕН!</b>\n\n"
                f"💫 <i>Изменения сохранены в системе</i>",
                reply_markup=get_management_keyboard(),
                               f"💫 <i>Изменения сохранены в системе</i>",
                reply_markup=get_management_keyboard(),
                parse_mode='HTML'
            )
            
            # Показываем parse_mode='HTML'
            )
            
            # Показываем обнов обновленные данные
            updated_filter = await get_filter_by_id(filter_id, user_id)
            if updated_filter:
                expiry_date = datetime.strptime(str(updated_filter['expiry_date']), '%Y-%m-%d').date()
ленные данные
            updated_filter = await get_filter_by_id(filter_id, user_id)
            if updated_filter:
                expiry_date = datetime.strptime(str(updated_filter['expiry_date']), '%Y-%m-%d').date()
                days_until = (expiry_date - datetime.now().date()).days
                icon, status = get_status_icon_and_text(days_until)
                
                await message.answer(
                days_until = (expiry_date - datetime.now().date()).days
                icon, status = get_status_icon_and_text(days_until)
                
                await message.answer                    f"📋 <b>ОБНОВЛЕННЫЕ ДАННЫЕ:</b>\n\n"
                    f(
                    f"📋 <b>ОБНОВЛЕННЫЕ ДАННЫЕ:</b>\n\n"
                    f"{icon} <b>Фильтр #{filter_id}</b>\n"
                    f"💧 Тип: {updated_filter"{icon} <b>Фильтр #{filter_id}</b>\n"
                    f"💧 Тип: {updated_filter['filter_type']}\n"
                    f"📍 Место: {updated['filter_type']}\n"
                    f"📍 Место: {updated_filter['location']}\n"
                    f"📅 Замена: {format_date_nice(d_filter['location']}\n"
                    f"📅 Замена: {format_date_nice(datetime.strptime(str(updated_filter['last_change']), '%Y-%atetime.strptime(str(updated_filter['last_change']), '%Y-%m-%d'))}\n"
                    f"⏰ Годенm-%d'))}\n"
                    f"⏰ Годен до: до: {format_date_nice(expiry_date)}\n"
                    f"📊 Статус: {status} ({days_until} дней)",
                    parse_mode='HTML'
                {format_date_nice(expiry_date)}\n"
                    f"📊 Статус: {status} ({days_until} дней)",
                    parse_mode='HTML'
                )
        else )
        else:
            await message.answer(
                "❌ <b>ОШИБКА ПРИ ОБНОВЛЕНИИ Ф:
            await message.answer(
                "❌ <b>ОШИБКА ПРИ ОБНОВЛЕНИИ ФИЛЬИЛЬТРА</b>\n\n"
                "Пожалуйста, попробуйте еще раз.",
                reply_markup=get_management_keyboard(),
                parse_mode='HTML'
           ТРА</b>\n\n"
                "Пожалуйста, попробуйте еще раз.",
                reply_markup=get_management_keyboard(),
                parse_mode='HTML'
            )
        
        await state.clear()
        
    except Exception as e )
        
        await state.clear()
        
    except Exception:
        logging.error(f"Ошибка при редактировании фильтра as e:
        logging.error(f"Ошибка при реда: {e}")
        await message.answer(
            "❌ <b>ПРОИЗОШЛА ОШИБКА</b>\n\n"
            "Пожалуйста, попробуйте еще раз.",
            reply_mктировании фильтра: {e}")
        await message.answer(
            "❌ <b>ПРОИЗОШЛА ОШИБКА</b>\n\n"
            "Пожалуйста, попробуйте еще раз.",
            reply_markup=get_management_keyboard(),
            parse_mode='HTML'
        )
        await state.clear()

# =========arkup=get_management_keyboard(),
            parse_mode='HTML'
        )
        await state.clear()

# ========== ОБРАБОТЧИКИ УДАЛЕНИЯ ФИЛЬТРОВ == ОБРАБОТЧИКИ УДАЛЕНИЯ ФИЛЬТРОВ=========
@dp.message(DeleteFilterStates.waiting_filter_selection)
async def process_delete_filter_selection(message: types.Message, state: FSMContext):
    ==========
@dp.message(DeleteFilterStates.waiting_filter_selection)
async def process_delete_filter_selection(message: types.Message, state: FSMContext):
    """Обработка выбора фильтра для удаления"""
    if message.text == "🔙 Назад """Обработка выбора фильтра для удаления"""
    if message.text == "🔙 Назад":
        await state.clear()
        await message.answer("🔙 Возврат в мен":
        await state.clear()
        await message.answer("🔙 Возврат в меню управления", reply_markup=get_management_keyboard())
        return
    
    match = re.search(r'#(\d+)', message.text)
    if not matchю управления", reply_markup=get_management_keyboard())
        return
    
    match = re.search(r'#(\d+)', message.text)
    if not match:
        await message.answer("❌ Неверный формат. Выберите фильтр из спи:
        await message.answer("❌ Неверный формат. Выберите фильтр из списка:")
        return
    
    filter_id = int(match.group(1))
    filter_data =ска:")
        return
    
    filter_id = int(match.group(1))
    filter_data = await get_filter_by_id(filter_id, message.from_user.id)
    
    if not filter_data:
        await await get_filter_by_id(filter_id, message.from_user.id)
    
    if not filter_data:
        await message.answer("❌ Фильтр не найден. Вы message.answer("❌ Фильтр не найден. Выберите другой:")
        return
    
    await state.update_data(deleting_filter_id=filter_idберите другой:")
        return
    
    await state.update_data(deleting_filter_id=filter, deleting_filter_data=filter_data)
    await state.set_state(DeleteFilterStates.waiting_confirmation)
    
    expiry_date = datetime.strptime(str(filter_data_id, deleting_filter_data=filter_data)
    await state.set_state(DeleteFilterStates.waiting_confirmation)
    
    expiry_date = datetime.strptime(str(filter_data['expiry_date']), '%Y-%m-%d').date['expiry_date']), '%Y-%m-%d').()
    
    await message.answer(
        f"🗑️ <b>ПОДТВЕРЖДЕНИЕ УДАЛЕНИЯ</b>\n\n"
        f"❌ Вы действительно хотиdate()
    
    await message.answer(
        f"🗑️ <b>ПОДТВЕРЖДЕНИЕ УДАЛЕНИЯ</b>\n\n"
        f"❌ Вы действительно хотите удалить фильтр?\n\n"
        f"те удалить фильтр?\n\n"
        f"💧 Тип: {filter_data['filter_type']}\n"
        f"📍 Место: {💧 Тип: {filter_data['filter_type']}\n"
        f"📍 Место: {filter_data['location']}\n"
        f"📅 Годен до: {format_date_nice(expiry_date)}\n\n"
        f"filter_data['location']}\n"
        f"📅 Годен до: {format_date_nice(expiry_date)}\n\n"
        f"<i>Это действие нельзя отменить!</i>",
        reply_mark<i>Это действие нельзя отменить!</i>",
        reply_markup=get_confirmation_keyboard(),
        parse_mode='HTMLup=get_confirmation_keyboard(),
        parse_mode='HTML'
    )

@dp.message(DeleteFilterStates.waiting_confirmation)
async def process_delete_confirmation(message: types.Message, state: FSM'
    )

@dp.message(DeleteFilterStates.waiting_confirmation)
async def process_delete_confirmation(message: types.Message, state: FSMContext):
    """Обработка подтверждения удаления"""
    data = await state.get_data()
Context):
    """Обработка подтверждения удаления"""
    data = await state.get_data()
    filter_id = data['deleting_filter_id']
    filter_id = data['deleting_filter_id']
    user_id    user_id = message.from_user.id
    
    if message.text == "✅ Да, всё верно":
        success = await delete_filter_from_db(filter_id, user_id)
        
        if success:
            await = message.from_user.id
    
    if message.text == "✅ Да, всё верно":
        success = await delete_filter_from_db(filter_id, user_id)
        
        if success:
            await message.answer(
                message.answer(
                "🗑️ <b>ФИЛЬТР УСПЕШНО УДАЛЕН!</b>\n\n"
                "💫 <i>Филь "🗑️ <b>ФИЛЬТР УСПЕШНО УДАЛЕН!</b>\n\n"
                "💫 <i>Фильтр больше не отслеживается в системе</i>",
                reply_markup=getтр больше не отслеживается в системе</i>",
                reply_markup=get_management_key_management_keyboard(),
                parse_mode='HTML'
            )
        else:
            await message.answer(
                "❌ <b>ОШИБКА ПРИ УДАboard(),
                parse_mode='HTML'
            )
        else:
            await message.answer(
                "❌ <b>ОШИБКА ПРИ УДАЛЕНИИЛЕНИИ ФИЛЬТРА</b>\n\n"
                "Пожалуйста, попробуйте еще раз.",
                reply_markup=get_management_keyboard(),
                parse_mode='HTML'
            ФИЛЬТРА</b>\n\n"
                "Пожалуйста, попробуйте еще раз.",
                reply_markup=get_management_keyboard(),
                parse_mode='HTML'
            )
        
        await state.clear()
        
    elif message.text == " )
        
        await state.clear()
        
    elif message.text == "❌ Нет, изменить":
        await state.clear()
        await message.answer(
            "❌ <b>Удаление отменено</b>",
            reply_markup=get_❌ Нет, изменить":
        await state.clear()
        await message.answer(
            "❌ <b>Удаление отменено</b>",
            reply_markup=get_management_keyboard(),
            parse_mode='HTML'
        )
   management_keyboard(),
            parse_mode='HTML'
        )
    else:
        await message.answer(
            "Пожалуйста, подтвердите удаление:",
            reply else:
        await message.answer(
            "Пожалуйста, подтвердите удаление:",
            reply_markup=get_confirmation_keyboard()
        )

# ========== ИМПОРТ/ЭКСПОРТ_markup=get_confirmation_keyboard()
        )

# ========== ИМПОРТ/ ==========
@dp.message(F.text == "📤 Импорт/Экспорт")
async def cmd_import_export(message: types.Message):
   ЭКСПОРТ ==========
@dp.message(F.text == "📤 Импорт/Экспорт")
async def cmd_import_export(message: types.Message):
    """Мен """Меню импорта/экспорта"""
    health_monitor.record_message(message.from_user.id)
    
    await message.answer(
        "📤 <b>ИМПОРТ/ю импорта/экспорта"""
    health_monitor.record_message(message.from_user.id)
    
    await message.answer(
        "📤 <b>ИМПОРТ/ЭКЭКСПОРТ ДАННЫХ</b>\n\n"
        "💾 <b>Доступные операции:</b>\nСПОРТ ДАННЫХ</b>\n\n"
        "💾 <b>Доступные операции:</b>\n"
        "• 📤 Экспорт в Excel - выгрузка всех фильтров\n"
        "• 📥"
        "• 📤 Экспорт в Excel - выгрузка всех фильтров\n"
        "• 📥 Импорт из Excel - загрузка из файла\n"
        "• Импорт из Excel - загрузка из файла\n"
        "• 📋 Шаблон Excel - скачать шаблон для импорта\n"
        " 📋 Шаблон Excel - скачать шаблон для импорта\n"
        "• ☁️ Синхронизация с Google Sheets\n\n"
        "💡 <i>Поддерживается работа с Excel и Google Sheets</i>",
        reply_markup• ☁️ Синхронизация с Google Sheets\n\n"
        "💡 <i>Поддерживается работа с Excel и Google Sheets</i>",
        reply_markup=get_import_export_keyboard(),
        parse_mode='HTML'
    )

@dp.message(F=get_import_export_keyboard(),
        parse_mode='HTML'
    )

@dp.message(F.text == "📤 Экспорт в Excel")
async def cmd_export_excel(message: types.Message):
    """Эксп.text == "📤 Экспорт в Excel")
async def cmd_export_excel(message: types.Message):
   орт данных в Excel"""
    try:
        excel_file = await export_to_excel(message.from_user.id)
        
        await message.answer_document(
            types.Buffered """Экспорт данных в Excel"""
    try:
        excel_file = await export_to_excel(message.from_user.id)
        
        await message.answer_document(
            types.BufferedInputFile(
InputFile(
                excel_file.getvalue(),
                filename=f"фильтры_{datetime.now().strftime('%Y%m%d_%H%M                excel_file.getvalue(),
                filename=f"фильтры_{datetime.now().strftime('%Y%m%d_%H%M')}.xlsx')}.xlsx"
            ),
            caption="📊 <b>Ваши фильтры экспортированы в Excel</b>",
            parse_mode='HTML'
        )
    except ValueError as e:
        await message.answer(f"
            ),
            caption="📊 <b>Ваши фильтры экспортированы в Excel</b>",
            parse_mode='HTML'
        )
    except ValueError as e:
        await message.answer(f"❌ {str(e)}")
    except Exception as e:
"❌ {str(e)}")
    except Exception as e:
        logging.error(f"Ошибка при экспорте в Excel: {e}")
        await message.answer("❌        logging.error(f"Ошибка при экспорте в Excel: {e}")
        await message.answer("❌ Произошла Произошла ошибка при экспорте данных")

# ========== GOOGLE SHEETS СИНХРОНИЗАЦИЯ ==========
@dp.message(F.text == " ошибка при экспорте данных")

# ========== GOOGLE SHEETS СИНХРОНИЗАЦИЯ ==========
@dp.message(F☁️ Синхронизация с Google Sheets")
async def cmd_google_sheets_sync(message: types.Message):
    """Меню синхронизации с Google Sheets"""
    health.text == "☁️ Синхронизация с Google Sheets")
async def cmd_google_sheets_sync(message: types.Message):
    """Меню синхронизации с Google Sheets"""
    health_monitor.record_message(message.from_user.id)
    
    status_text = "_monitor.record_message(message.from_user.id)
    
    status_text = "☁️ <b>СИНХРОНИЗАЦИЯ С GOOGLE SHEETS</☁️ <b>СИНХРОНИЗАЦИЯ С GOOGLE SHEETS</b>\n\n"
    
    if not config.GOOGLE_SHEETS_CREDENTIALS:
        status_text +=b>\n\n"
    
    if not config.GOOGLE_SHEETS_CR "❌ <b>Статус:</b> Не настроены учетные данные\n"
        status_text += "💡 <i>Установите переменную GOOGLE_SHEETS_CREDENTIALEDENTIALS:
        status_text += "❌ <b>Статус:</b> Не настроены учетные данные\n"
        status_text += "💡 <i>Установите переменную GOOGLE_SHEETS_CREDENTIALS</i>\n\n"
    elif not google_sync.sheet_id:
        status_text += "🟡S</i>\n\n"
    elif not google_sync.sheet_id:
 <b>Статус:</b> Готов к настройке\n"
        status_text += "📝 <i>Укажите ID таблицы Google Sheets</i>\n        status_text += "🟡 <b>Статус:</b> Готов к настройке\n"
        status_text += "📝 <i>Укажите ID таблицы Google Sheets</i>\n\n"
    else:
        status_text += "🟢 <b>Стату\n"
    else:
        status_text += "🟢 <b>Статус:</b> Настроено\n"
        status_text += f"📊 <b>с:</b> Настроено\n"
        status_text += f"📊 <b>ID таблицы:</b> {google_sync.sheet_id}\n"
        statusID таблицы:</b> {google_sync.sheet_id}\n"
        status_text +=_text += f"🔄 <b>Автосинхронизация:</b> {'ВКЛ' if google_sync.auto_sync else 'ВЫКЛ'}\n f"🔄 <b>Автосинхронизация:</b> {'ВКЛ' if google_sync.auto_sync else 'ВЫКЛ'}\n\n"
    
    status_text += "💡 <b>Как получить ID таблицы:</b>\n\n"
    
    status_text += "💡 <b>Как получить ID таблицы:</b>\n"
    status_text += "1. Создайте таблицу в Google Sheets\n"
    status_text += "1. Создайте таблицу в Google Sheets\n"
    status_text += "2. Скопируйте ID из URL: https://docs"
    status_text += "2. Скопируйте ID из URL: https://docs.google.google.com/spreadsheets/d/<b>[ID]</b>/edit\n"
    status_text += "3. Используйте кнопку '📝.com/spreadsheets/d/<b>[ID]</b>/edit\n"
    status_text += "3. Используйте кнопку '📝 У Указать ID таблицы'"
    
    await message.answer(
       казать ID таблицы'"
    
    await message.answer(
        status_text,
        reply_markup=get_sync_keyboard(),
        parse_mode='HTML'
    )

@dp.message(F.text == "🔄 Синхронизировать с Google Sheets")
async def cmd_sync status_text,
        reply_markup=get_sync_keyboard(),
        parse_mode='HTML'
    )

@dp.message(F.text == "🔄 Синхронизировать с Google Sheets")
async def cmd_sync_to_sheets(message: types.Message):
    """Синхронизация данных с Google Sheets"""
    if not google_sync.is_configured():
       _to_sheets(message: types.Message):
    """Синхронизация данных с Google Sheets"""
    if not google_sync.is_configured():
        await message.answer(
            "❌ <b>Синхронизация не настроена await message.answer(
            "❌ <b>Синхронизация не настроена</b>\n\n"
            "Пожалуйста, сначала настрой</b>\n\n"
            "Пожалуйста, сначала настройте подклюте подключение к Google Sheets.",
            reply_markup=get_sync_keyboard(),
            parse_mode='HTML'
        )
        return
    
    await message.answer(
        "🔄 <b>Начинаю синхроничение к Google Sheets.",
            reply_markup=get_sync_keyboard(),
            parse_mode='HTML'
        )
        return
    
    await message.answer(
        "🔄 <b>Начинаю синхронизацию...</b>\n\n"
        "⏳зацию...</b>\n\n"
        "⏳ <i>Пожалуйста, подождите <i>Пожалуйста, подождите...</i>",
        reply_markup=get_back_keyboard(),
        parse_mode='HTML'
    )
    
    # Получаем фильтры пользователя
    filters = await get_user_filters(message.from_user...</i>",
        reply_markup=get_back_keyboard(),
        parse_mode='HTML'
    )
    
    # Получаем фильтры пользователя
    filters = await get_user_filters(message.from.id)
    
    if not filters:
        await message.answer(
            "📭 <b>Нет данных для синхронизации</b>\n\n"
            "У вас пока нет фильтров.",
            reply_user.id)
    
    if not filters:
        await message.answer(
            "📭 <b>Нет данных для синхронизации</b>\n\n"
            "У вас пока нет фильтров.",
            reply_markup=get_sync_keyboard(),
            parse_mode='HTML'
        )
        return
    
    # Выполняем синхронизацию
    success, result_message = await safe_sync_to_s_markup=get_sync_keyboard(),
            parse_mode='HTML'
        )
        return
    
    # Выполняем синхронизацию
    success, result_message = await safe_sync_to_sheets(message.from_user.id, filters)
    
    if success:
        await message.answer(
            f"✅ <b>heets(message.from_user.id, filters)
    
    if success:
        await message.answer(
            f"✅ <b>СИНХРОНИЗАЦИЯ УСПЕСИНХРОНИЗАЦИЯ УСПЕШНА!</b>\n\n"
            f"{result_message}\n\n"
            f"💫ШНА!</b>\n\n"
            f"{result_message}\n\n"
            f"💫 <i>Данные обновлены в Google Sheets</i>",
            reply <i>Данные обновлены в Google Sheets</i>",
            reply_markup=get_sync_keyboard(),
            parse_mode='HTML'
        )
    else:
        await message.answer(
            f"_markup=get_sync_keyboard(),
            parse_mode='HTML'
        )
    else:
        await message.answer(
            f"❌ <b>ОШИБКА СИНХРОНИЗАЦИ❌ <b>ОШИБКА СИНХРОНИЗАЦИИИ</b>\n\n"
            f"{result_message}\n\n"
            f"🔧 <i>Проверьте настройки подключения</i>",
</b>\n\n"
            f"{result_message}\n\n"
            f"🔧 <i>Проверьте настройки подключения</i>",
            reply_markup=get_sync_keyboard(),
            parse_mode='HTML'
        )

@dp.message(F.text == "📊 Ста            reply_markup=get_sync_keyboard(),
            parse_mode='HTML'
        )

@dp.message(F.text == "📊 Статус синхронизации")
async def cmd_sync_status(message: types.Message):
    """Провтус синхронизации")
async def cmd_sync_status(message: types.Message):
    """Проверка статуса синхронизации"""
   ерка статуса синхронизации"""
    health_monitor.record_message(message.from_user.id)
    
    if not config.GOOGLE_SHEETS_CREDENTIALS:
        await message health_monitor.record_message(message.from_user.id)
    
    if not config.GOOGLE_SHEETS_CREDENTIALS:
.answer(
            "❌ <b>Учетные данные не настроены</b>\n\n"
            "Для использования синхронизации необходимо настроить GOOGLE_SHEETS        await message.answer(
            "❌ <b>Учетные данные не настроены</b>\n\n"
            "Для использования синхронизации необходимо настроить GOOGLE_SHEETS_CREDENTIALS в переменных окружения.",
            reply_markup=get_sync_CREDENTIALS в переменных окружения.",
            reply_markup=get_sync_keyboard_keyboard(),
            parse_mode='HTML'
        )
        return
    
    if not google_sync.sheet_id:
        await message.answer(
            "🟡 <b>ID таблицы не(),
            parse_mode='HTML'
        )
        return
    
    if not google_sync.sheet_id:
        await message.answer(
            "🟡 <b>ID таблицы не указан указан</b>\n\n"
            "Используйте кнопку '📝 Указать ID таблицы' для настройки.",
            reply_markup=get_sync_keyboard(),
</b>\n\n"
            "Используйте кнопку '📝 Указать ID таблицы' для настройки.",
            reply_markup            parse_mode='HTML'
        )
        return
    
    await message.answer(
        "🔍 <b>Проверяю подключение к Google Sheets...</b>",
        reply_markup=get_back=get_sync_keyboard(),
            parse_mode='HTML'
        )
        return
    
    await message.answer(
        "🔍 <b>Проверяю подключение к Google Sheets...</b>",
        reply_markup=get_back_keyboard(),
        parse_mode='HTML'
    )
    
    # Тестируем подключение
    success, result_message = await google_keyboard(),
        parse_mode='HTML'
    )
    
    # Тестируем подключение
    success, result_message = await google_sync.test_connection()
    
    if success:
        status_icon = "🟢"
       _sync.test_connection()
    
    if success:
        status_icon = "🟢 status_text = "ПОДКЛ"
        status_text = "ПОДКЛЮЧЕНИЕ УСПЕШНО"
    else:
        status_icon = "🔴" 
        status_text = "ОШИБКА ПОДКЛЮЧЕНИЯ"
    
    status_info = (
        f"{status_icon} <b>СТАТУС ПОДКЛЮЧЕНИЯ:</ЮЧЕНИЕ УСПЕШНО"
    else:
        status_icon = "🔴" 
        status_text = "ОШИБКА ПОДКЛЮЧЕНИЯ"
    
    status_info = (
        f"{status_icon} <b>СТАТУС ПОДКЛЮЧЕНИЯ:</b> {status_text}\n\n"
        f"📊 <b>ID таблицы:</b>b> {status_text}\n\n"
        f"📊 <b>ID таблицы:</b> {google_sync.sheet_id}\n"
        f"🔄 <b>Автосинхронизация:</b> {google_sync.sheet_id}\n"
        f"🔄 <b>Автосинхронизация:</b> {'✅ ВКЛ' if google_sync.auto_sync else '❌ ВЫКЛ {'✅ ВКЛ' if google_sync.auto_sync else '❌ ВЫК'}\n\n"
        f"💡 <b>Результат проверки:</b>\n{result_message}"
    )
    
    await message.answer(
        status_info,
        reply_markЛ'}\n\n"
        f"💡 <b>Результат проверки:</b>\n{result_message}"
    )
    
    await message.answer(
        status_info,
        reply_markup=get_sync_keyboard(),
        parse_mode='HTML'
    )

@dp.message(F.textup=get_sync_keyboard(),
        parse_mode='HTML'
    )

@dp.message(F.text == "⚙️ Настройки синхронизации == "⚙️ Настройки синхронизации")
async def cmd_sync_settings(message: types.Message):
    """Настройки синхронизации"""
    if not config.GOOGLE_SHEETS_CREDENTIAL")
async def cmd_sync_settings(message: types.Message):
    """Настройки синхронизации"""
    if not config.GOOGLE_SHEETSS:
        await message.answer(
            "❌ <b>Учетные данные не настроены</b>\n\n"
            "Для использования синхронизации с Google_CREDENTIALS:
        await message.answer(
            "❌ <b>Учетные данные не настроены</b>\n\n"
            "Для использования синхронизации с Google Sheets необходимо:\n\n"
            "1. Создать сервисный аккаунт в Google Cloud Console Sheets необходимо:\n\n"
            "1. Создать сервисный аккаунт в Google Cloud Console\n"
           \n"
            "2. Скачать JSON файл с ключами\n"
            "3. Установить переменную GOOGLE_SHEETS_CREDENTIALS\n\n"
            " "2. Скачать JSON файл с ключами\n"
            "3. Установить переменную GOOGLE_SHEETS_CREDENTIALS\n\n"
            "💡 <i>Обратитесь к администратору для💡 <i>Обратитесь к администратору для настройки</i>",
            reply_markup=get_sync_keyboard(),
            parse_mode='HTML'
        )
        return
    
    # Тестируем подключение настройки</i>",
            reply_markup=get_sync_keyboard(),
            parse_mode='HTML'
        )
        return
    
    # Тестируем подключение для отображения статуса
    connection_status = "🔴 Не проверено"
    if google_sync.sheet_id:
        success, result = await google_sync.test_connection для отображения статуса
    connection_status = "🔴 Не проверено"
    if google_sync.sheet_id:
        success, result = await google_sync.test()
        connection_status = f"🟢 Успешно" if success else f"🔴 Ошибка: {result}"
    
    status_text = "⚙️_connection()
        connection_status = f"🟢 Успешно" if success else f"🔴 Ошибка: {result}"
    
    status_text = "⚙️ <b>НАСТРОЙКИ СИНХРОНИЗАЦИЯ</b>\n\n"
    
    if google_sync.sheet_id:
        status <b>НАСТРОЙКИ СИНХРОНИЗАЦИЯ</b>\n\n"
    
    if google_sync.sheet_id:
        status_text += f"📊 <b>ID таблицы:</b> {google_sync.sheet_id}\n"
        status_text += f"🔗 <b>Подключение:</b> {connection_status}\n"
    else:
        status_text += "📊 <b>ID таблицы:</b> Не указан\n"
    
    status_text += f"🔄 <b>Автосинхронизация:</b> {'✅ ВКЛ' if google_sync.auto_sync else '❌ ВЫКЛ'}\n\n"
    
    status_text += (
        "💡 <b>Инструкция по настройке:</b>\n"
        "1. Соз_text += f"📊 <b>ID таблицы:</b> {google_sync.sheet_id}\n"
        status_text += f"🔗 <b>Подключение:</b> {connection_status}\n"
    else:
        status_text += "📊 <b>ID таблицы:</b> Не указан\n"
    
    status_text += f"🔄 <b>Автосинхронизация:</b> {'✅ ВКЛ' if google_sync.auto_sync else '❌ ВЫКЛ'}\n\n"
    
    status_text += (
        "💡 <b>Инструкция по настройке:</b>\n"
        "1. Создайте таблицу в Google Sheets\n"
        "2. Скопируйте ID из URL: docs.google.com/spreadдайте таблицу в Google Sheets\n"
        "2. Скопируйте ID из URL: docs.google.com/spreadsheets/d/<b>[ID]</b>/edit\n"
        "3. Нажмите '📝 Указать ID таблицы'\n"
        "4. Проверьте подключение через '📊 Статус синхронизации'"
    )
    
    await message.answer(
        status_text,
        reply_markup=get_sync_settings_keyboard(),
        parse_mode='HTML'
    )

@dp.message(F.text == "📝 Указать ID таблицы")
async def cmd_set_sheet_id(message: types.Message, state: FSMContext):
    """Установка ID таблицы Google Sheets"""
    await state.set_state(GoogleSheetssheets/d/<b>[ID]</b>/edit\n"
        "3. Нажмите '📝 Указать ID таблицы'\n"
        "4. Проверьте подключение через '📊 Статус синхронизации'"
    )
    
    await message.answer(
        status_text,
        reply_markup=get_sync_settings_keyboard(),
        parse_mode='HTML'
    )

@dp.message(F.text == "📝 Указать ID таблицы")
async def cmd_set_sheet_id(message: types.Message, state: FSMContext):
    """Установка ID таблицы Google Sheets"""
    await state.set_state(GoogleSheetsStatesStates.waiting_sheet_id)
    
    await message.answer(
        "📝 <b>УКАЖИТЕ ID ТАБЛИЦЫ GOOGLE SHEETS</b>\n\n"
        "🔗 <b>Как получить ID:</b>\n"
        "1. Откройте вашу.waiting_sheet_id)
    
    await message.answer(
        "📝 <b>УКАЖИТЕ ID ТАБЛИЦЫ GOOGLE SHEETS</b>\n\n"
        "🔗 <b>Как получить ID:</b>\n"
        "1. Откройте вашу таблицу в Google Sheets\n"
        "2. Скопируйте ID из URL адреса:\n"
        "   <code>https://docs.google.com/spreadsheets/d/[ВАШ_ID_ТУТ]/edit</code>\n\n"
        "📎 <b>Пример ID:</b> <code таблицу в Google Sheets\n"
        "2. Скопируйте ID из URL адреса:\n"
        "   <code>https://docs.google.com/spreadsheets/d/[ВАШ_ID_ТУТ]/edit</code>\n\n"
        "📎 <b>Пример ID:</b> <code>1BxiMVs0XRA5nFMdKvBdBZjgmUUqptlbs74OgvE2upms</code>\>1BxiMVs0XRA5nFMdKvBdBZjgmUUqptlbs74OgvE2upms</code>\n\nn\n"
        "✏️ <b>Введите ID таблицы:</b>",
        reply_markup=get_cancel_keyboard(),
        parse_mode='HTML'
    )

@dp.message(GoogleSheetsStates.waiting_sheet_id)
async def process_sheet_id(message: types.Message, state: FSMContext):
    """Обработка ID таблицы"""
    if message.text == "❌ Отмена":
       "
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
    if len(sheet_id) < 10 or not await state.clear()
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
        f"📊 <b>ID:</b> {sheet_id}\n re.match(r'^[a-zA-Z0-9-_]+$', sheet_id):
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
        f"💫 <i>Теперь вы можете синх\n"
        f"💫 <i>Теперь вы можете синхронизировать данные</i>",
ронизировать данные</i>",
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
        "💫        reply_markup=get_sync_settings_keyboard(),
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
        "💫 <i>Теперь данные будут автоматически обновляться в Google Sheets при <i>Теперь данные будут автоматически обновляться в Google Sheets при любых изменениях фильтров</i>",
        reply_markup=get_sync_settings_keyboard(),
        parse_mode='HTML'
    )

@dp.message(F.text == "⏸️ Автосинхронизация ВЫКЛ")
async def cmd_auto_sync_off(message: types.Message):
    """Выключение автосинхронизации"""
    google_sync.auto_sync = False
    google_s любых изменениях фильтров</i>",
        reply_markup=get_sync_settings_keyboard(),
        parse_mode='HTML'
    )

@dp.message(F.text == "⏸️ Автосинхронизация ВЫКЛ")
async def cmd_auto_sync_off(message: types.Message):
    """Выключение автосинхронизации"""
    google_sync.auto_sync = Falseync.save_settings()
    
    await message.answer(
        "⏸️ <b>АВТОСИНХРОНИЗАЦИЯ ВЫКЛЮЧЕНА</b>\n\n"
        "💫 <i>
    google_sync.save_settings()
    
    await message.answer(
        "⏸️ <b>АВТОСИНХРОНИЗАЦИЯ ВЫКЛЮЧЕНА</b>\n\n"
        "💫 <iДанные больше не будут автоматически обновляться в Google Sheets</i>",
        reply_markup=get_sync_settings_keyboard(),
        parse_mode='HTML'
>Данные больше не будут автоматически обновляться в Google Sheets</i>",
        reply_markup=get_sync_settings_keyboard(),
        parse    )

@dp.message(F.text == "🗑️ Отключить синхронизацию")
async def cmd_disable_sync(message: types.Message):
    """Отключение синхронизации"""
    google_sync.sheet_id = None
_mode='HTML'
    )

@dp.message(F.text == "🗑️ Отключить синхронизацию")
async def cmd_disable_sync(message: types.Message):
    """Отключение синхронизации"""
    google_sync.sheet_id = None
    google_s    google_sync.auto_sync = False
    google_sync.save_settings()
    
    await message.answer(
        "🗑️ <b>СИНХРОНИЗАЦИЯ Оync.auto_sync = False
    google_sync.save_settings()
    
    await message.answer(
        "🗑️ <b>СИНХРОНИЗАТКЛЮЧЕНА</b>\n\n"
        "💫 <i>Все настройки синхронизации сброшены</iЦИЯ ОТКЛЮЧЕНА</b>\n\n"
        "💫 <i>Все настройки синхронизации сброшены</i>",
        reply_markup=get_sync>",
        reply_markup=get_sync_keyboard(),
        parse_mode='HTML'
    )

@dp.message(Command("sync_test"))
async def cmd_sync_test(message: types.Message):
    """Тестирование синхрони_keyboard(),
        parse_mode='HTML'
    )

@dp.message(Command("sync_test"))
async def cmd_sync_test(message: types.Message):
    """Тестированиезации (для админа)"""
    if not is_admin(message.from_user.id):
        return
    
    await message.answer("🔍 Тестирую подключение к Google Sheets...")
    
    success, result_message = await google_sync.test_connection()
 синхронизации (для админа)"""
    if not is_admin(message.from_user.id):
        return
    
    await message.answer("🔍 Тестирую подключение к Google Sheets...")
    
    success, result_message = await google_sync.test_connection()
    
    if success:
        await message.answer(f"✅ {result_message}")
    else:
        await message.answer(f"❌ {result_message}")

@dp.message(Command("create_s    
    if success:
        await message.answer(f"✅ {result_message}")
    else:
        await message.answer(f"❌ {result_message}")

@dp.message(Command("create_sheet"))
async def cmd_create_sheet(message: types.Message):
    """Создание новой таблицыheet"))
async def cmd_create_sheet(message: types.Message):
    """Создание новой таблицы ( (для админа)"""
    if not is_admin(message.from_user.id):
        return
    
    try:
        import gspread
        from google.oauth2.service_account import Credentials
        
        if not config.GOдля админа)"""
    if not is_admin(message.from_user.id):
        return
    
    try:
        import gspread
        from google.oauth2.service_account import Credentials
        
        if not config.GOOGLE_SHEETS_CREDOGLE_SHEETS_CREDENTIALS:
            await message.answer("❌ GOOGLE_SHEETS_CREDENTIALS не настроены")
            return
        
        credentials_info = json.loads(config.GOOGENTIALS:
            await message.answer("❌ GOOGLE_SHEETS_CREDENTIALS не настроены")
            return
        
        credentials_info = json.loads(config.GOOGLE_SHEETS_CREDENTIALS)
        scope = [
            'https://www.googleapis.com/auth/spreadLE_SHEETS_CREDENTIALS)
        scope = [
            'https://www.googleapis.com/auth/spreadsheets',
sheets',
            'https://www.googleapis.com/auth/drive'
        ]
        credentials = Credentials.from_service_account_info(credentials_info, scopes=scope)
        gc = gspread.authorize(credentials)
        
            'https://www.googleapis.com/auth/drive'
        ]
        credentials = Credentials.from_service_account_info(credentials_info, scopes=scope)
        gc = gspread.author        # Создаем новую таблицу
        spreadsheet = gc.create('Фильтр-Трекер Данные')
        
        # Даем доступ для чтения/записи
ize(credentials)
        
        # Создаем новую таблицу
        spreadsheet = gc.create('Фильтр-Трекер Данные')
        
        # Даем доступ для чтения/записи
        spreadsheet.share(None, perm_type='anyone', role='writer')
        
        spreadsheet.share(None, perm_type='anyone', role='writer        sheet_id = spreadsheet.id
        sheet_url = f"https://docs.google.com/spreadsheets/d/{sheet_id}/edit"
        
        await message.answer(
            f"✅ <b>НОВАЯ ТАБЛИЦА СОЗДАНА</b')
        
        sheet_id = spreadsheet.id
        sheet_url = f"https://docs.google.com/spreadsheets/d/{sheet_id}/edit"
        
        await message.answer(
            f"✅ <b>НОВАЯ ТАБЛИЦА СОЗДАНА</b>\n\n"
            f>\n\n"
            f"📊 <b>ID таблицы:</b> <code>{sheet_id}</code>\n"
            f"🔗 <b>Ссылка:</b> {"📊 <b>ID таблицы:</b> <code>{sheet_id}</code>\n"
            f"🔗 <b>Ссылка:</bsheet_url}\n\n"
            f"💡 <i>Используйте этот ID для настройки синхронизации</i>",
            parse_mode='HTML'
        )
        
    except Exception as e:
        await message.> {sheet_url}\n\n"
            f"💡 <i>Используйте этот ID для настройки синхронизации</i>",
            parse_mode='HTML'
        )
        
    except Exception as e:
        await message.answer(f"❌ Ошибка создания таблицы: {str(e)}")

# ========== ЗАПУanswer(f"❌ Ошибка создания таблицы: {str(e)}")

# ========== ЗСК ПРИЛОЖЕНИЯ ==========
async def main():
    """Основная функция запуска"""
    try:
        # Инициализация конфигурации
        config.validateАПУСК ПРИЛОЖЕНИЯ ==========
async def main():
    """Основная функция запуска"""
    try:
        # Инициализация конфигурации
        config.validate()
        
       ()
        
        # Настройка логирования
        setup_logging()
        
        # Инициализация базы данных
        await init_db()
        await check_and_update_schema()
        
        # Запуск фонов # Настройка логирования
        setup_logging()
        
        # Инициализация базы данных
        await init_db()
        await check_and_update_schema()
        
        # Запуск фоновых задач
        asyncio.create_task(send_personalized_reminders())
        asyncio.create_task(health_monitoringых задач
        asyncio.create_task(send_personalized_reminders())
        asyncio.create_task(health_task())
        
        # Настройка обработчика ошибок
        dp.errors.register(error_handler)
        
        # Запуск бота
        logging.info("Бот запу_monitoring_task())
        
        # Настройка обработчика ошибок
        dp.errors.register(error_handler)
        
        # Запуск бота
        logging.info("Бот запускается...")
        await dp.start_polling(bot)
        
    except Exception asскается...")
        await dp.start_polling(bot)
        
    except Exception as e:
        logging.critical(f"Критическая ошибка при запуске: {e}")
        # Уведомление администратора
        if config.ADMIN_ID:
            try e:
        logging.critical(f"Критическая ошибка при запуске: {e}")
        # Уведомление администратора
        if config.ADMIN_ID:
            try:
                await bot.send_message(config.ADMIN_ID, f"🚨 Бот упа:
                await bot.send_message(config.ADMIN_ID, f"🚨 Ботл: {e}")
            except:
                pass
        raise

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logging.info упал: {e}")
            except:
                pass
        raise

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logging.info("Бот остановлен пользователем")
    except Exception as e:
        logging.critical(f"Фатальная ошибка: {e}")
        sys.exit(1("Бот остановлен пользователем")
    except Exception as e:
        logging.critical(f"Фатальная ошибка: {e}")
        sys.exit(1)
