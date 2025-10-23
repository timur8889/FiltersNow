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
import time
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
import hashlib
import functools
from dataclasses import dataclass
from enum import Enum

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
            'colored': {
                'format': '%(asctime)s - %(levelname)s - %(message)s',
                'datefmt': '%H:%M:%S'
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
                'formatter': 'colored'
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

# ========== УЛУЧШЕНИЕ: КЭШИРОВАНИЕ ==========
class Cache:
    def __init__(self, ttl: int = 300):
        self.ttl = ttl
        self._cache = {}
    
    def get(self, key: str):
        """Получение значения из кэша"""
        if key in self._cache:
            value, expiry = self._cache[key]
            if time.time() < expiry:
                return value
            else:
                del self._cache[key]
        return None
    
    def set(self, key: str, value: Any):
        """Установка значения в кэш"""
        self._cache[key] = (value, time.time() + self.ttl)
    
    def clear(self):
        """Очистка кэша"""
        self._cache.clear()

# Глобальный кэш
cache = Cache(ttl=300)  # 5 минут

def cached(ttl: int = 300):
    """Декоратор для кэширования результатов функций"""
    def decorator(func):
        @functools.wraps(func)
        async def wrapper(*args, **kwargs):
            # Создаем ключ кэша на основе аргументов
            key = f"{func.__name__}:{hash(str(args) + str(kwargs))}"
            
            # Пробуем получить из кэша
            cached_result = cache.get(key)
            if cached_result is not None:
                return cached_result
            
            # Выполняем функцию и кэшируем результат
            result = await func(*args, **kwargs)
            cache.set(key, result)
            return result
        return wrapper
    return decorator

# ========== УЛУЧШЕНИЕ: РАСШИРЕННЫЙ МОНИТОРИНГ ==========
@dataclass
class BotMetrics:
    total_messages: int = 0
    successful_operations: int = 0
    failed_operations: int = 0
    user_sessions: Dict[int, int] = None
    command_usage: Dict[str, int] = None
    response_times: List[float] = None
    
    def __post_init__(self):
        if self.user_sessions is None:
            self.user_sessions = {}
        if self.command_usage is None:
            self.command_usage = {}
        if self.response_times is None:
            self.response_times = []

class EnhancedHealthMonitor:
    def __init__(self):
        self.start_time = datetime.now()
        self.metrics = BotMetrics()
        self.alert_threshold = 0.1  # 10% ошибок
        self.last_alert_sent = None
    
    def record_operation(self, success: bool, command: str = None, user_id: int = None):
        """Запись операции"""
        self.metrics.total_messages += 1
        if success:
            self.metrics.successful_operations += 1
        else:
            self.metrics.failed_operations += 1
        
        if command:
            self.metrics.command_usage[command] = self.metrics.command_usage.get(command, 0) + 1
        
        if user_id:
            self.metrics.user_sessions[user_id] = self.metrics.user_sessions.get(user_id, 0) + 1
    
    def record_response_time(self, response_time: float):
        """Запись времени ответа"""
        self.metrics.response_times.append(response_time)
        # Храним только последние 100 измерений
        if len(self.metrics.response_times) > 100:
            self.metrics.response_times.pop(0)
    
    async def check_health_status(self) -> Dict[str, Any]:
        """Проверка статуса здоровья"""
        uptime = datetime.now() - self.start_time
        error_rate = self.metrics.failed_operations / max(1, self.metrics.total_messages)
        
        # Расчет перцентилей времени ответа
        response_times = self.metrics.response_times
        if response_times:
            avg_response_time = sum(response_times) / len(response_times)
            p95_response_time = sorted(response_times)[int(len(response_times) * 0.95)]
        else:
            avg_response_time = p95_response_time = 0
        
        health_status = {
            'uptime': str(uptime),
            'total_operations': self.metrics.total_messages,
            'success_rate': (self.metrics.successful_operations / max(1, self.metrics.total_messages)) * 100,
            'error_rate': error_rate * 100,
            'active_users': len(self.metrics.user_sessions),
            'avg_response_time': avg_response_time,
            'p95_response_time': p95_response_time,
            'top_commands': dict(sorted(self.metrics.command_usage.items(), key=lambda x: x[1], reverse=True)[:5]),
            'status': 'HEALTHY' if error_rate < self.alert_threshold else 'UNHEALTHY'
        }
        
        return health_status
    
    async def should_send_alert(self) -> bool:
        """Проверка необходимости отправки алерта"""
        if self.metrics.total_messages < 10:  # Минимум операций для статистики
            return False
        
        error_rate = self.metrics.failed_operations / self.metrics.total_messages
        should_alert = error_rate >= self.alert_threshold
        
        # Ограничиваем частоту алертов (не чаще чем раз в 30 минут)
        if should_alert and (self.last_alert_sent is None or 
                           (datetime.now() - self.last_alert_sent).total_seconds() > 1800):
            self.last_alert_sent = datetime.now()
            return True
        
        return False

enhanced_monitor = EnhancedHealthMonitor()

# ========== УЛУЧШЕНИЕ: РАСШИРЕННЫЙ RATE LIMITING ==========
class EnhancedRateLimiter:
    def __init__(self, max_requests: int = 10, window: int = 60, burst: int = 3):
        self.max_requests = max_requests
        self.window = window
        self.burst = burst
        self.user_requests = {}
        self.user_penalties = {}
    
    def is_allowed(self, user_id: int) -> bool:
        """Проверка разрешения на обработку запроса"""
        now = time.time()
        
        # Проверяем штрафы
        if user_id in self.user_penalties:
            penalty_end = self.user_penalties[user_id]
            if now < penalty_end:
                return False
            else:
                del self.user_penalties[user_id]
        
        if user_id not in self.user_requests:
            self.user_requests[user_id] = []
        
        # Удаляем старые запросы
        self.user_requests[user_id] = [
            req_time for req_time in self.user_requests[user_id]
            if (now - req_time) < self.window
        ]
        
        # Проверяем лимит
        if len(self.user_requests[user_id]) >= self.max_requests:
            # Применяем штраф за превышение
            penalty_duration = min(300, (len(self.user_requests[user_id]) - self.max_requests) * 30)  # до 5 минут
            self.user_penalties[user_id] = now + penalty_duration
            logging.warning(f"Rate limit exceeded for user {user_id}. Penalty: {penalty_duration}s")
            return False
        
        self.user_requests[user_id].append(now)
        return True
    
    def get_user_stats(self, user_id: int) -> Dict[str, Any]:
        """Получение статистики пользователя"""
        if user_id not in self.user_requests:
            return {'requests': 0, 'penalty': None}
        
        now = time.time()
        recent_requests = [req for req in self.user_requests[user_id] if (now - req) < self.window]
        penalty_end = self.user_penalties.get(user_id)
        
        return {
            'requests': len(recent_requests),
            'penalty': penalty_end - now if penalty_end and penalty_end > now else None
        }

enhanced_rate_limiter = EnhancedRateLimiter(max_requests=15, window=60)

# ========== УЛУЧШЕНИЕ: РАСШИРЕННЫЙ MIDDLEWARE ==========
class EnhancedMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[types.TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: types.TelegramObject,
        data: Dict[str, Any]
    ) -> Any:
        start_time = time.time()
        
        # Проверяем, есть ли пользователь
        if hasattr(event, 'from_user') and event.from_user:
            user_id = event.from_user.id
            
            # Rate limiting
            if not enhanced_rate_limiter.is_allowed(user_id):
                if hasattr(event, 'answer'):
                    await event.answer(
                        "🚫 <b>Слишком много запросов!</b>\n\n"
                        "⚠️ Пожалуйста, подождите некоторое время перед отправкой следующего сообщения.",
                        parse_mode='HTML'
                    )
                return
            
            # Запись метрик
            enhanced_monitor.record_operation(True, user_id=user_id)
            
            if hasattr(event, 'text') and event.text:
                command = event.text.split()[0] if event.text else 'unknown'
                enhanced_monitor.metrics.command_usage[command] = enhanced_monitor.metrics.command_usage.get(command, 0) + 1
        
        try:
            result = await handler(event, data)
            return result
        except Exception as e:
            enhanced_monitor.record_operation(False)
            logging.error(f"Error in handler: {e}")
            raise
        finally:
            # Запись времени ответа
            response_time = time.time() - start_time
            enhanced_monitor.record_response_time(response_time)

# ========== УЛУЧШЕНИЕ: ВИЗУАЛЬНЫЕ ЭЛЕМЕНТЫ ==========
class MessageTemplates:
    """Шаблоны красивых сообщений"""
    
    @staticmethod
    def create_header(title: str, emoji: str = "✨") -> str:
        """Создание заголовка"""
        return f"{emoji} <b>{title}</b> {emoji}\n\n"
    
    @staticmethod
    def create_section(title: str, content: str, emoji: str = "•") -> str:
        """Создание секции"""
        return f"{emoji} <b>{title}:</b> {content}\n"
    
    @staticmethod
    def create_progress_bar(percentage: float, length: int = 10) -> str:
        """Создание прогресс-бара"""
        filled = int(length * percentage / 100)
        empty = length - filled
        return f"[{'█' * filled}{'░' * empty}] {percentage:.1f}%"
    
    @staticmethod
    def create_filter_card(filter_data: Dict) -> str:
        """Создание карточки фильтра"""
        expiry_date = datetime.strptime(str(filter_data['expiry_date']), '%Y-%m-%d').date()
        last_change = datetime.strptime(str(filter_data['last_change']), '%Y-%m-%d').date()
        today = datetime.now().date()
        days_until_expiry = (expiry_date - today).days
        
        icon, status = get_status_icon_and_text(days_until_expiry)
        
        # Расчет прогресса
        total_days = filter_data['lifetime_days']
        days_passed = (today - last_change).days
        progress_percentage = min(100, (days_passed / total_days) * 100)
        
        card = (
            f"{icon} <b>#{filter_data['id']} {filter_data['filter_type']}</b>\n"
            f"📍 {filter_data['location']}\n"
            f"📅 Заменен: {format_date_nice(last_change)}\n"
            f"🗓️ Годен до: {format_date_nice(expiry_date)}\n"
            f"⏱️ Прогресс: {MessageTemplates.create_progress_bar(progress_percentage)}\n"
            f"📊 Статус: <b>{status}</b> (<code>{days_until_expiry} дн.</code>)\n"
            f"{'─' * 30}\n"
        )
        return card
    
    @staticmethod
    def create_statistics_card(stats: Dict) -> str:
        """Создание карточки статистики"""
        return (
            "📊 <b>СТАТИСТИКА СИСТЕМЫ</b>\n\n"
            f"👥 <b>Пользователи:</b> {stats.get('total_users', 0)}\n"
            f"📦 <b>Фильтры:</b> {stats.get('total_filters', 0)}\n"
            f"🟢 <b>Норма:</b> {stats.get('normal_filters', 0)}\n"
            f"🟡 <b>Скоро истекают:</b> {stats.get('expiring_soon', 0)}\n"
            f"🔴 <b>Просрочено:</b> {stats.get('expired_filters', 0)}\n"
            f"⚡ <b>Производительность:</b> {stats.get('health_score', 100):.1f}%"
        )

# ========== УЛУЧШЕНИЕ: КРАСИВЫЕ КЛАВИАТУРЫ ==========
class KeyboardTemplates:
    """Шаблоны клавиатур"""
    
    @staticmethod
    def create_main_keyboard() -> types.ReplyKeyboardMarkup:
        """Главная клавиатура"""
        builder = ReplyKeyboardBuilder()
        buttons = [
            ("📋 Мои фильтры", "Показать все ваши фильтры"),
            ("✨ Добавить фильтр", "Добавить новый фильтр"),
            ("⚙️ Управление", "Управление фильтрами"),
            ("📊 Статистика", "Показать статистику"),
            ("🔄 Импорт/Экспорт", "Работа с файлами")
        ]
        
        for text, desc in buttons:
            builder.button(text=text)
        
        builder.adjust(2, 2, 1)
        return builder.as_markup(resize_keyboard=True, input_field_placeholder="Выберите действие...")
    
    @staticmethod
    def create_add_filter_keyboard() -> types.ReplyKeyboardMarkup:
        """Клавиатура добавления фильтров"""
        builder = ReplyKeyboardBuilder()
        buttons = [
            "➕ Один фильтр",
            "📝 Несколько",
            "🎯 Быстрый выбор",
            "🔙 Назад"
        ]
        
        for text in buttons:
            builder.button(text=text)
        
        builder.adjust(2, 1, 1)
        return builder.as_markup(resize_keyboard=True)
    
    @staticmethod
    def create_filter_type_keyboard() -> types.ReplyKeyboardMarkup:
        """Клавиатура выбора типа фильтра"""
        builder = ReplyKeyboardBuilder()
        popular_filters = [
            "🔧 Магистральный SL10",
            "🔧 Магистральный SL20", 
            "💧 Гейзер",
            "💧 Аквафор",
            "⚗️ Барьер",
            "🔍 Другой тип"
        ]
        
        for filter_type in popular_filters:
            builder.button(text=filter_type)
        
        builder.button(text="🔙 Назад")
        builder.adjust(2, 2, 2, 1)
        return builder.as_markup(resize_keyboard=True)
    
    @staticmethod
    def create_management_keyboard() -> types.ReplyKeyboardMarkup:
        """Клавиатура управления"""
        builder = ReplyKeyboardBuilder()
        buttons = [
            "✏️ Редактировать",
            "🗑️ Удалить", 
            "📊 Онлайн таблица",
            "🔄 Обновить все",
            "🔙 Назад"
        ]
        
        for text in buttons:
            builder.button(text=text)
        
        builder.adjust(2, 2, 1)
        return builder.as_markup(resize_keyboard=True)
    
    @staticmethod
    def create_import_export_keyboard() -> types.ReplyKeyboardMarkup:
        """Клавиатура импорта/экспорта"""
        builder = ReplyKeyboardBuilder()
        buttons = [
            "📤 Экспорт Excel",
            "📥 Импорт Excel", 
            "📋 Шаблон",
            "🔄 Синхронизация",
            "🔙 Назад"
        ]
        
        for text in buttons:
            builder.button(text=text)
        
        builder.adjust(2, 2, 1)
        return builder.as_markup(resize_keyboard=True)
    
    @staticmethod
    def create_quick_actions_keyboard(filter_id: int) -> types.InlineKeyboardMarkup:
        """Инлайн клавиатура быстрых действий для фильтра"""
        builder = InlineKeyboardBuilder()
        
        builder.button(
            text="✏️ Редактировать", 
            callback_data=f"edit_{filter_id}"
        )
        builder.button(
            text="🗑️ Удалить", 
            callback_data=f"delete_{filter_id}"
        )
        builder.button(
            text="🔄 Обновить", 
            callback_data=f"refresh_{filter_id}"
        )
        builder.button(
            text="⏩ Отложить на 7 дн.", 
            callback_data=f"postpone_{filter_id}"
        )
        
        builder.adjust(2, 2)
        return builder.as_markup()
    
    @staticmethod
    def create_pagination_keyboard(page: int, total_pages: int, prefix: str = "filters") -> types.InlineKeyboardMarkup:
        """Клавиатура пагинации"""
        builder = InlineKeyboardBuilder()
        
        if page > 1:
            builder.button(text="⬅️ Назад", callback_data=f"{prefix}_{page-1}")
        
        builder.button(text=f"{page}/{total_pages}", callback_data="current_page")
        
        if page < total_pages:
            builder.button(text="Вперед ➡️", callback_data=f"{prefix}_{page+1}")
        
        builder.adjust(3)
        return builder.as_markup()

# ========== ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ==========
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
    
    total = len(filters)
    if total == 0:
        return "📊 <b>Нет данных для отображения</b>"
    
    # Создаем текстовую визуализацию
    expired_bar = "█" * int(expired / total * 10) if total > 0 else ""
    soon_bar = "█" * int(expiring_soon / total * 10) if total > 0 else ""
    normal_bar = "█" * int(normal / total * 10) if total > 0 else ""
    
    return (
        f"📊 <b>СТАТУС ФИЛЬТРОВ:</b>\n\n"
        f"🟢 Норма: {normal} {normal_bar}\n"
        f"🟡 Скоро истечет: {expiring_soon} {soon_bar}\n"
        f"🔴 Просрочено: {expired} {expired_bar}\n\n"
        f"📈 <b>Всего:</b> {total} фильтров"
    )

def is_admin(user_id: int) -> bool:
    """Проверка прав администратора"""
    return user_id == ADMIN_ID

def backup_database() -> bool:
    """Создание резервной копии базы данных"""
    try:
        if os.path.exists('filters.db'):
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            backup_name = f'backups/filters_backup_{timestamp}.db'
            
            # Создаем папку для бэкапов если нет
            os.makedirs('backups', exist_ok=True)
            
            shutil.copy2('filters.db', backup_name)
            
            # Удаляем старые бэкапы (оставляем последние 10)
            backups = sorted([f for f in os.listdir('backups') if f.startswith('filters_backup_')])
            if len(backups) > 10:
                for old_backup in backups[:-10]:
                    os.remove(os.path.join('backups', old_backup))
            
            logging.info(f"Резервная копия создана: {backup_name}")
            return True
    except Exception as e:
        logging.error(f"Ошибка при создании резервной копии: {e}")
    return False

# ========== УЛУЧШЕНИЕ: РАСШИРЕННАЯ ВАЛИДАЦИЯ ==========
class ValidationResult:
    def __init__(self, is_valid: bool, message: str = "", value: Any = None):
        self.is_valid = is_valid
        self.message = message
        self.value = value

class EnhancedValidator:
    """Расширенная система валидации"""
    
    @staticmethod
    def validate_filter_type(filter_type: str) -> ValidationResult:
        """Валидация типа фильтра"""
        if not filter_type or len(filter_type.strip()) == 0:
            return ValidationResult(False, "❌ Тип фильтра не может быть пустым")
        
        if len(filter_type) > 100:
            return ValidationResult(False, "❌ Тип фильтра слишком длинный (макс. 100 символов)")
        
        # Проверка на запрещенные символы
        if re.search(r'[<>{}[\]$&]', filter_type):
            return ValidationResult(False, "❌ Тип фильтра содержит запрещенные символы")
        
        return ValidationResult(True, "✅ Корректный тип фильтра", filter_type.strip())
    
    @staticmethod
    def validate_location(location: str) -> ValidationResult:
        """Валидация местоположения"""
        if not location or len(location.strip()) == 0:
            return ValidationResult(False, "❌ Местоположение не может быть пустым")
        
        if len(location) > 50:
            return ValidationResult(False, "❌ Местоположение слишком длинное (макс. 50 символов)")
        
        if re.search(r'[<>{}[\]$&]', location):
            return ValidationResult(False, "❌ Местоположение содержит запрещенные символы")
        
        return ValidationResult(True, "✅ Корректное местоположение", location.strip())
    
    @staticmethod
    def validate_lifetime(lifetime: str) -> ValidationResult:
        """Валидация срока службы"""
        try:
            days = int(lifetime)
            if days <= 0:
                return ValidationResult(False, "❌ Срок службы должен быть положительным числом")
            if days > 3650:  # 10 лет
                return ValidationResult(False, "❌ Срок службы не может превышать 10 лет")
            return ValidationResult(True, "✅ Корректный срок службы", days)
        except ValueError:
            return ValidationResult(False, "❌ Срок службы должен быть числом (дни)")
    
    @staticmethod
    def validate_date(date_str: str) -> ValidationResult:
        """Улучшенная валидация даты"""
        try:
            date_obj = validate_date(date_str)  # Используем существующую функцию
            return ValidationResult(True, "✅ Корректная дата", date_obj)
        except ValueError as e:
            return ValidationResult(False, f"❌ {str(e)}")

# ========== УЛУЧШЕНИЕ: АСИНХРОННАЯ БАЗА ДАННЫХ С КЭШИРОВАНИЕМ ==========
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

@cached(ttl=60)  # Кэшируем на 1 минуту
async def get_user_filters(user_id: int) -> List[Dict]:
    """Асинхронное получение фильтров пользователя с кэшированием"""
    try:
        async with get_db_connection() as conn:
            cur = await conn.cursor()
            await cur.execute("SELECT * FROM filters WHERE user_id = ? ORDER BY expiry_date", (user_id,))
            rows = await cur.fetchall()
            return [dict(row) for row in rows]
    except Exception as e:
        logging.error(f"Ошибка при получении фильтров пользователя {user_id}: {e}")
        enhanced_monitor.record_operation(False, "get_user_filters", user_id)
        return []

@cached(ttl=30)  # Кэшируем на 30 секунд
async def get_filter_by_id(filter_id: int, user_id: int) -> Optional[Dict]:
    """Асинхронное получение фильтра по ID с кэшированием"""
    try:
        async with get_db_connection() as conn:
            cur = await conn.cursor()
            await cur.execute("SELECT * FROM filters WHERE id = ? AND user_id = ?", (filter_id, user_id))
            result = await cur.fetchone()
            return dict(result) if result else None
    except Exception as e:
        logging.error(f"Ошибка при получении фильтра {filter_id}: {e}")
        enhanced_monitor.record_operation(False, "get_filter_by_id", user_id)
        return None

@cached(ttl=120)  # Кэшируем на 2 минуты
async def get_all_users_stats() -> Dict:
    """Асинхронное получение статистики с кэшированием"""
    try:
        async with get_db_connection() as conn:
            cur = await conn.cursor()
            await cur.execute('''SELECT 
                COUNT(DISTINCT user_id) as total_users, 
                COUNT(*) as total_filters,
                SUM(CASE WHEN expiry_date <= date('now') THEN 1 ELSE 0 END) as expired_filters,
                SUM(CASE WHEN expiry_date BETWEEN date('now') AND date('now', '+7 days') THEN 1 ELSE 0 END) as expiring_soon,
                SUM(CASE WHEN expiry_date > date('now', '+7 days') THEN 1 ELSE 0 END) as normal_filters
                FROM filters''')
            result = await cur.fetchone()
            
            stats = dict(result) if result else {
                'total_users': 0, 'total_filters': 0, 'expired_filters': 0, 
                'expiring_soon': 0, 'normal_filters': 0
            }
            
            # Добавляем метрики здоровья
            health = await enhanced_monitor.check_health_status()
            stats['health_score'] = health['success_rate']
            
            return stats
    except Exception as e:
        logging.error(f"Ошибка при получении статистики: {e}")
        enhanced_monitor.record_operation(False, "get_all_users_stats")
        return {'total_users': 0, 'total_filters': 0, 'expired_filters': 0, 'expiring_soon': 0, 'normal_filters': 0}

async def add_filter_to_db(user_id: int, filter_type: str, location: str, last_change: str, expiry_date: str, lifetime_days: int) -> bool:
    """Добавление фильтра в БД с инвалидацией кэша"""
    try:
        async with get_db_connection() as conn:
            cur = await conn.cursor()
            await cur.execute('''INSERT INTO filters 
                              (user_id, filter_type, location, last_change, expiry_date, lifetime_days) 
                              VALUES (?, ?, ?, ?, ?, ?)''',
                              (user_id, filter_type, location, last_change, expiry_date, lifetime_days))
            
            # Инвалидируем кэш
            cache.clear()
            enhanced_monitor.record_operation(True, "add_filter", user_id)
            return True
    except Exception as e:
        logging.error(f"Ошибка при добавлении фильтра: {e}")
        enhanced_monitor.record_operation(False, "add_filter", user_id)
        return False

async def update_filter_in_db(filter_id: int, user_id: int, **kwargs) -> bool:
    """Обновление фильтра в БД с инвалидацией кэша"""
    try:
        if not kwargs:
            return False
        
        async with get_db_connection() as conn:
            cur = await conn.cursor()
            set_clause = ", ".join([f"{key} = ?" for key in kwargs.keys()])
            values = list(kwargs.values())
            values.extend([filter_id, user_id])
            
            await cur.execute(f"UPDATE filters SET {set_clause} WHERE id = ? AND user_id = ?", values)
            
            # Инвалидируем кэш
            cache.clear()
            enhanced_monitor.record_operation(True, "update_filter", user_id)
            return cur.rowcount > 0
    except Exception as e:
        logging.error(f"Ошибка при обновлении фильтра {filter_id}: {e}")
        enhanced_monitor.record_operation(False, "update_filter", user_id)
        return False

async def delete_filter_from_db(filter_id: int, user_id: int) -> bool:
    """Удаление фильтра из БД с инвалидацией кэша"""
    try:
        async with get_db_connection() as conn:
            cur = await conn.cursor()
            await cur.execute("DELETE FROM filters WHERE id = ? AND user_id = ?", (filter_id, user_id))
            
            # Инвалидируем кэш
            cache.clear()
            enhanced_monitor.record_operation(True, "delete_filter", user_id)
            return cur.rowcount > 0
    except Exception as e:
        logging.error(f"Ошибка при удалении фильтра {filter_id}: {e}")
        enhanced_monitor.record_operation(False, "delete_filter", user_id)
        return False

# ========== ИНИЦИАЛИЗАЦИЯ БОТА ==========
bot = Bot(
    token=API_TOKEN,
    default=DefaultBotProperties(parse_mode='HTML')
)
storage = MemoryStorage()
dp = Dispatcher(storage=storage)

# Регистрация middleware
dp.update.outer_middleware(EnhancedMiddleware())

# ========== УЛУЧШЕНИЕ: ФОНОВЫЕ ЗАДАЧИ ==========
async def background_tasks():
    """Фоновые задачи с улучшенной обработкой ошибок"""
    while True:
        try:
            now = datetime.now()
            
            # Ежедневные задачи в определенное время
            if now.hour == 8 and now.minute == 0:  # 8:00 утра
                await check_expired_filters()
                await send_daily_reports()
                
            # Резервное копирование в 3:00
            if now.hour == 3 and now.minute == 0:
                backup_database()
                
            # Проверка здоровья каждые 30 минут
            if now.minute % 30 == 0:
                await check_system_health()
                
            # Очистка кэша каждые 2 часа
            if now.hour % 2 == 0 and now.minute == 0:
                cache.clear()
                logging.info("Кэш очищен")
                
        except Exception as e:
            logging.error(f"Ошибка в фоновой задаче: {e}")
            enhanced_monitor.record_operation(False, "background_tasks")
        
        await asyncio.sleep(60)  # Проверяем каждую минуту

async def check_system_health():
    """Проверка здоровья системы"""
    try:
        health = await enhanced_monitor.check_health_status()
        
        if await enhanced_monitor.should_send_alert():
            await bot.send_message(
                ADMIN_ID,
                f"🚨 <b>СИСТЕМНОЕ УВЕДОМЛЕНИЕ</b>\n\n"
                f"⚠️ <b>Высокий уровень ошибок!</b>\n"
                f"📊 Статус: {health['status']}\n"
                f"❌ Уровень ошибок: {health['error_rate']:.1f}%\n"
                f"⚡ Производительность: {health['avg_response_time']:.2f}с\n\n"
                f"🕒 <i>Время: {datetime.now().strftime('%H:%M:%S')}</i>",
                parse_mode='HTML'
            )
            
    except Exception as e:
        logging.error(f"Ошибка при проверке здоровья системы: {e}")

async def send_daily_reports():
    """Ежедневные отчеты пользователям"""
    try:
        async with get_db_connection() as conn:
            cur = await conn.cursor()
            await cur.execute("SELECT DISTINCT user_id FROM filters")
            users = await cur.fetchall()
            
            for user_row in users:
                user_id = user_row['user_id']
                filters = await get_user_filters(user_id)
                
                if not filters:
                    continue
                
                expired_count = 0
                expiring_soon_count = 0
                
                for f in filters:
                    expiry_date = datetime.strptime(str(f['expiry_date']), '%Y-%m-%d').date()
                    days_until = (expiry_date - datetime.now().date()).days
                    
                    if days_until <= 0:
                        expired_count += 1
                    elif days_until <= 7:
                        expiring_soon_count += 1
                
                if expired_count > 0 or expiring_soon_count > 0:
                    report = (
                        f"📊 <b>ЕЖЕДНЕВНЫЙ ОТЧЕТ</b>\n\n"
                        f"🔍 <b>Статус ваших фильтров:</b>\n"
                        f"🟢 Норма: {len(filters) - expired_count - expiring_soon_count}\n"
                        f"🟡 Скоро истекают: {expiring_soon_count}\n"
                        f"🔴 Просрочено: {expired_count}\n\n"
                    )
                    
                    if expired_count > 0:
                        report += "🚨 <b>ВНИМАНИЕ!</b> У вас есть просроченные фильтры!\n"
                    
                    try:
                        await bot.send_message(user_id, report, parse_mode='HTML')
                        await asyncio.sleep(0.1)  # Защита от лимитов
                    except Exception as e:
                        logging.error(f"Не удалось отправить отчет пользователю {user_id}: {e}")
                        
    except Exception as e:
        logging.error(f"Ошибка при отправке ежедневных отчетов: {e}")

# ========== STATES ==========
class FilterStates(StatesGroup):
    waiting_filter_type = State()
    waiting_location = State()
    waiting_change_date = State()
    waiting_lifetime = State()

class MultipleFiltersStates(StatesGroup):
    waiting_filters_list = State()

class EditFilterStates(StatesGroup):
    waiting_filter_selection = State()
    waiting_field_selection = State()
    waiting_new_value = State()

class DeleteFilterStates(StatesGroup):
    waiting_filter_selection = State()

class ImportExportStates(StatesGroup):
    waiting_excel_file = State()

# ========== ОСНОВНЫЕ ОБРАБОТЧИКИ ==========
@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    """Улучшенный обработчик команды /start"""
    welcome_message = (
        "🌟 <b>Добро пожаловать в Фильтр-Трекер!</b> 🤖\n\n"
        "💧 <i>Умный помощник для своевременной замены фильтров</i>\n\n"
        "🚀 <b>Основные возможности:</b>\n"
        "• 📋 Просмотр всех ваших фильтров\n" 
        "• ✨ Добавление новых фильтров\n"
        "• ⏳ Контроль сроков замены\n"
        "• ⚙️ Полное управление базой\n"
        "• 📊 Детальная статистика\n"
        "• 📤 Импорт/экспорт Excel\n"
        "• 🔔 Автоматические напоминания\n\n"
        "💡 <i>Используйте кнопки ниже для навигации</i>"
    )
    
    await message.answer(welcome_message, reply_markup=KeyboardTemplates.create_main_keyboard())

@dp.message(Command("help"))
async def cmd_help(message: types.Message):
    """Улучшенный обработчик команды /help"""
    help_text = (
        "🆘 <b>Помощь по использованию бота</b>\n\n"
        
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
        "• 🔄 Импорт/Экспорт - работа с Excel\n\n"
        
        "❌ <b>Отмена операций:</b>\n"
        "Используйте кнопку '🔙 Назад' для отмены текущей операции"
    )
    await message.answer(help_text, parse_mode='HTML', reply_markup=KeyboardTemplates.create_main_keyboard())

@dp.message(F.text == "📋 Мои фильтры")
async def cmd_my_filters(message: types.Message):
    """Улучшенный показ фильтров с пагинацией"""
    filters = await get_user_filters(message.from_user.id)
    
    if not filters:
        await message.answer(
            "📭 <b>У вас пока нет фильтров</b>\n\n"
            "💫 <i>Добавьте первый фильтр с помощью кнопки '✨ Добавить фильтр'</i>",
            reply_markup=KeyboardTemplates.create_main_keyboard()
        )
        return
    
    # Показываем первую страницу
    await show_filters_page(message, filters, 1)

async def show_filters_page(message: types.Message, filters: List[Dict], page: int, page_size: int = 5):
    """Показать страницу с фильтрами"""
    start_idx = (page - 1) * page_size
    end_idx = start_idx + page_size
    page_filters = filters[start_idx:end_idx]
    
    total_pages = (len(filters) + page_size - 1) // page_size
    
    response = f"📋 <b>ВАШИ ФИЛЬТРЫ</b> (Страница {page}/{total_pages})\n\n"
    
    for f in page_filters:
        response += MessageTemplates.create_filter_card(f)
    
    # Добавляем инлайн-кнопки для пагинации и быстрых действий
    keyboard = KeyboardTemplates.create_pagination_keyboard(page, total_pages)
    
    if page == 1:  # На первой странице показываем общую статистику
        infographic = create_expiry_infographic(filters)
        await message.answer(infographic, parse_mode='HTML')
    
    await message.answer(response, parse_mode='HTML', reply_markup=keyboard)

# ========== ИНЛАЙН ОБРАБОТЧИКИ ==========
@dp.callback_query(F.data.startswith("filters_"))
async def handle_filters_pagination(callback: types.CallbackQuery):
    """Обработчик пагинации фильтров"""
    page = int(callback.data.split("_")[1])
    filters = await get_user_filters(callback.from_user.id)
    
    if not filters:
        await callback.answer("Нет фильтров для отображения")
        return
    
    await callback.message.edit_reply_markup(reply_markup=None)
    await show_filters_page(callback.message, filters, page)
    await callback.answer()

@dp.callback_query(F.data.startswith("edit_"))
async def handle_edit_filter(callback: types.CallbackQuery, state: FSMContext):
    """Обработчик редактирования фильтра через инлайн-кнопку"""
    filter_id = int(callback.data.split("_")[1])
    user_id = callback.from_user.id
    
    filter_data = await get_filter_by_id(filter_id, user_id)
    if not filter_data:
        await callback.answer("❌ Фильтр не найден")
        return
    
    # Сохраняем данные фильтра в состоянии
    await state.update_data(
        filter_id=filter_id,
        current_filter=filter_data
    )
    
    await state.set_state(EditFilterStates.waiting_field_selection)
    
    # Показываем клавиатуру выбора поля
    await callback.message.answer(
        f"✏️ <b>Редактирование фильтра #{filter_id}</b>\n\n"
        f"💧 <b>Тип:</b> {filter_data['filter_type']}\n"
        f"📍 <b>Место:</b> {filter_data['location']}\n"
        f"📅 <b>Замена:</b> {format_date_nice(datetime.strptime(str(filter_data['last_change']), '%Y-%m-%d'))}\n"
        f"⏱️ <b>Срок:</b> {filter_data['lifetime_days']} дней\n\n"
        "🔧 <b>Выберите поле для редактирования:</b>",
        reply_markup=KeyboardTemplates.create_management_keyboard(),
        parse_mode='HTML'
    )
    
    await callback.answer()

@dp.callback_query(F.data.startswith("delete_"))
async def handle_delete_filter(callback: types.CallbackQuery):
    """Обработчик удаления фильтра через инлайн-кнопку"""
    filter_id = int(callback.data.split("_")[1])
    user_id = callback.from_user.id
    
    filter_data = await get_filter_by_id(filter_id, user_id)
    if not filter_data:
        await callback.answer("❌ Фильтр не найден")
        return
    
    # Создаем клавиатуру подтверждения
    builder = InlineKeyboardBuilder()
    builder.button(text="✅ Да, удалить", callback_data=f"confirm_delete_{filter_id}")
    builder.button(text="❌ Нет, отмена", callback_data="cancel_delete")
    builder.adjust(2)
    
    await callback.message.answer(
        f"🗑️ <b>Подтверждение удаления</b>\n\n"
        f"Вы действительно хотите удалить фильтр?\n\n"
        f"💧 <b>Тип:</b> {filter_data['filter_type']}\n"
        f"📍 <b>Место:</b> {filter_data['location']}\n\n"
        f"⚠️ <i>Это действие нельзя отменить!</i>",
        parse_mode='HTML',
        reply_markup=builder.as_markup()
    )
    
    await callback.answer()

@dp.callback_query(F.data.startswith("confirm_delete_"))
async def handle_confirm_delete(callback: types.CallbackQuery):
    """Обработчик подтверждения удаления"""
    filter_id = int(callback.data.split("_")[2])
    user_id = callback.from_user.id
    
    success = await delete_filter_from_db(filter_id, user_id)
    
    if success:
        await callback.message.edit_text(
            f"✅ <b>ФИЛЬТР УДАЛЕН!</b>\n\n"
            f"Фильтр #{filter_id} был успешно удален.",
            parse_mode='HTML'
        )
    else:
        await callback.message.edit_text(
            "❌ <b>Ошибка при удалении фильтра!</b>",
            parse_mode='HTML'
        )
    
    await callback.answer()

@dp.callback_query(F.data == "cancel_delete")
async def handle_cancel_delete(callback: types.CallbackQuery):
    """Обработчик отмены удаления"""
    await callback.message.edit_text("❌ <b>Удаление отменено</b>", parse_mode='HTML')
    await callback.answer()

@dp.callback_query(F.data.startswith("refresh_"))
async def handle_refresh_filter(callback: types.CallbackQuery):
    """Обработчик обновления информации о фильтре"""
    filter_id = int(callback.data.split("_")[1])
    user_id = callback.from_user.id
    
    filter_data = await get_filter_by_id(filter_id, user_id)
    if not filter_data:
        await callback.answer("❌ Фильтр не найден")
        return
    
    # Обновляем сообщение с актуальной информацией
    card_text = MessageTemplates.create_filter_card(filter_data)
    
    await callback.message.edit_text(
        f"🔄 <b>ОБНОВЛЕНО</b>\n\n{card_text}",
        parse_mode='HTML',
        reply_markup=KeyboardTemplates.create_quick_actions_keyboard(filter_id)
    )
    
    await callback.answer("✅ Информация обновлена")

@dp.callback_query(F.data.startswith("postpone_"))
async def handle_postpone_filter(callback: types.CallbackQuery):
    """Обработчик откладывания напоминания"""
    filter_id = int(callback.data.split("_")[1])
    user_id = callback.from_user.id
    
    filter_data = await get_filter_by_id(filter_id, user_id)
    if not filter_data:
        await callback.answer("❌ Фильтр не найден")
        return
    
    # Обновляем дату истечения (+7 дней)
    current_expiry = datetime.strptime(str(filter_data['expiry_date']), '%Y-%m-%d').date()
    new_expiry = current_expiry + timedelta(days=7)
    
    success = await update_filter_in_db(
        filter_id, user_id, 
        expiry_date=new_expiry.strftime('%Y-%m-%d')
    )
    
    if success:
        await callback.message.edit_text(
            f"⏩ <b>НАПОМИНАНИЕ ОТЛОЖЕНО</b>\n\n"
            f"Фильтр #{filter_id} теперь будет напоминать о замене через 7 дней.\n"
            f"📅 Новая дата: {format_date_nice(new_expiry)}",
            parse_mode='HTML'
        )
    else:
        await callback.message.edit_text(
            "❌ <b>Ошибка при обновлении фильтра!</b>",
            parse_mode='HTML'
        )
    
    await callback.answer()

# ========== УЛУЧШЕНИЕ: КОМАНДЫ АДМИНИСТРАТОРА ==========
@dp.message(Command("status"))
async def cmd_status(message: types.Message):
    """Расширенная команда статуса для администратора"""
    if not is_admin(message.from_user.id):
        await message.answer("❌ <b>Доступ запрещен!</b>", parse_mode='HTML')
        return
    
    health = await enhanced_monitor.check_health_status()
    stats = await get_all_users_stats()
    cache_info = f"Записей в кэше: {len(cache._cache)}"
    
    status_report = (
        "🤖 <b>РАСШИРЕННЫЙ СТАТУС БОТА</b>\n\n"
        
        "📈 <b>Метрики производительности:</b>\n"
        f"⏰ Аптайм: {health['uptime']}\n"
        f"📨 Сообщений: {health['total_operations']}\n"
        f"✅ Успешных: {health['success_rate']:.1f}%\n"
        f"❌ Ошибок: {health['error_rate']:.1f}%\n"
        f"⚡ Среднее время: {health['avg_response_time']:.3f}с\n"
        f"📊 P95 время: {health['p95_response_time']:.3f}с\n\n"
        
        "👥 <b>Статистика пользователей:</b>\n"
        f"👤 Активных: {health['active_users']}\n"
        f"📦 Всего фильтров: {stats['total_filters']}\n"
        f"🟢 Норма: {stats['normal_filters']}\n"
        f"🟡 Скоро истекают: {stats['expiring_soon']}\n"
        f"🔴 Просрочено: {stats['expired_filters']}\n\n"
        
        f"💾 <b>Система:</b>\n{cache_info}\n"
        f"📊 Статус: <b>{health['status']}</b>"
    )
    
    # Добавляем топ команд
    if health['top_commands']:
        status_report += "\n\n🔝 <b>Топ команд:</b>\n"
        for cmd, count in health['top_commands'].items():
            status_report += f"• {cmd}: {count}\n"
    
    await message.answer(status_report, parse_mode='HTML')

@dp.message(Command("metrics"))
async def cmd_metrics(message: types.Message):
    """Детальные метрики системы"""
    if not is_admin(message.from_user.id):
        await message.answer("❌ <b>Доступ запрещен!</b>", parse_mode='HTML')
        return
    
    health = await enhanced_monitor.check_health_status()
    rate_limit_stats = enhanced_rate_limiter.get_user_stats(message.from_user.id)
    
    metrics_report = (
        "📊 <b>ДЕТАЛЬНЫЕ МЕТРИКИ СИСТЕМЫ</b>\n\n"
        
        "🎯 <b>Rate Limiting:</b>\n"
        f"📨 Запросов: {rate_limit_stats['requests']}/15\n"
        f"⏳ Штраф: {rate_limit_stats['penalty']:.1f}с\n\n"
        
        "📈 <b>Распределение времени ответа:</b>\n"
    )
    
    # Анализ времени ответа
    response_times = enhanced_monitor.metrics.response_times
    if response_times:
        metrics_report += f"• Мин: {min(response_times):.3f}с\n"
        metrics_report += f"• Макс: {max(response_times):.3f}с\n"
        metrics_report += f"• Медиана: {sorted(response_times)[len(response_times)//2]:.3f}с\n"
    
    await message.answer(metrics_report, parse_mode='HTML')

@dp.message(Command("cache"))
async def cmd_cache(message: types.Message):
    """Управление кэшем"""
    if not is_admin(message.from_user.id):
        await message.answer("❌ <b>Доступ запрещен!</b>", parse_mode='HTML')
        return
    
    cache.clear()
    await message.answer("✅ <b>Кэш очищен!</b>", parse_mode='HTML')

# ========== УЛУЧШЕНИЕ: ИНИЦИАЛИЗАЦИЯ БАЗЫ ДАННЫХ ==========
async def init_db():
    """Улучшенная инициализация базы данных"""
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
                # Создаем таблицу с улучшенной структурой
                await cur.execute('''
                    CREATE TABLE filters (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        user_id INTEGER NOT NULL,
                        filter_type TEXT NOT NULL,
                        location TEXT NOT NULL,
                        last_change DATE NOT NULL,
                        expiry_date DATE NOT NULL,
                        lifetime_days INTEGER NOT NULL,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        notes TEXT DEFAULT '',
                        is_active BOOLEAN DEFAULT 1
                    )
                ''')
                
                # Создаем индексы для ускорения запросов
                await cur.execute('''CREATE INDEX idx_user_id ON filters(user_id)''')
                await cur.execute('''CREATE INDEX idx_expiry_date ON filters(expiry_date)''')
                await cur.execute('''CREATE INDEX idx_user_expiry ON filters(user_id, expiry_date)''')
                
                logging.info("База данных успешно создана с улучшенной структурой")
            else:
                # Проверяем наличие новых колонок и добавляем их если нужно
                await cur.execute("PRAGMA table_info(filters)")
                columns = [column[1] for column in await cur.fetchall()]
                
                if 'notes' not in columns:
                    await cur.execute('''ALTER TABLE filters ADD COLUMN notes TEXT DEFAULT ''''')
                    logging.info("Добавлена колонка 'notes'")
                
                if 'is_active' not in columns:
                    await cur.execute('''ALTER TABLE filters ADD COLUMN is_active BOOLEAN DEFAULT 1''')
                    logging.info("Добавлена колонка 'is_active'")
                
                logging.info("База данных уже существует, структура проверена")
                
    except Exception as e:
        logging.error(f"Критическая ошибка инициализации БД: {e}")
        if backup_database():
            logging.info("Создана резервная копия при ошибке инициализации")
        raise

# ========== УЛУЧШЕНИЕ: ЗАПУСК И ОСТАНОВКА ==========
async def on_startup():
    """Улучшенные действия при запуске бота"""
    logging.info("🤖 Бот запускается...")
    
    try:
        # Инициализация БД
        await init_db()
        
        # Создаем резервную копию при запуске
        if backup_database():
            logging.info("✅ Резервная копия создана при запуске")
        
        # Запускаем фоновые задачи
        asyncio.create_task(background_tasks())
        
        # Уведомляем администратора о запуске
        health = await enhanced_monitor.check_health_status()
        
        await bot.send_message(
            ADMIN_ID, 
            f"🚀 <b>Бот успешно запущен!</b>\n\n"
            f"⏰ <b>Время запуска:</b> {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
            f"📊 <b>Начальные метрики:</b>\n"
            f"• Успешных операций: {health['success_rate']:.1f}%\n"
            f"• Время ответа: {health['avg_response_time']:.3f}с\n"
            f"• Статус: {health['status']}",
            parse_mode='HTML'
        )
        
        logging.info("✅ Бот успешно запущен и готов к работе")
        
    except Exception as e:
        logging.error(f"❌ Ошибка при запуске бота: {e}")
        raise

async def on_shutdown():
    """Действия при остановке бота"""
    logging.info("🛑 Бот останавливается...")
    
    try:
        # Создаем финальную резервную копию
        if backup_database():
            logging.info("✅ Финальная резервная копия создана")
        
        # Отправляем отчет администратору
        health = await enhanced_monitor.check_health_status()
        uptime = datetime.now() - enhanced_monitor.start_time
        
        await bot.send_message(
            ADMIN_ID,
            f"🛑 <b>Бот остановлен</b>\n\n"
            f"⏰ <b>Время работы:</b> {uptime}\n"
            f"📊 <b>Финальная статистика:</b>\n"
            f"• Всего операций: {health['total_operations']}\n"
            f"• Успешных: {health['success_rate']:.1f}%\n"
            f"• Активных пользователей: {health['active_users']}",
            parse_mode='HTML'
        )
        
        logging.info("✅ Бот успешно остановлен")
        
    except Exception as e:
        logging.error(f"❌ Ошибка при остановке бота: {e}")

# ========== ЗАПУСК БОТА ==========
async def main():
    """Главная функция запуска бота"""
    if not API_TOKEN:
        logging.error("❌ Токен бота не найден! Установите переменную TELEGRAM_BOT_TOKEN")
        exit(1)
    
    try:
        await on_startup()
        await dp.start_polling(bot)
    except KeyboardInterrupt:
        logging.info("⏹️ Бот остановлен пользователем")
    except Exception as e:
        logging.critical(f"💥 Критическая ошибка: {e}")
        await bot.send_message(
            ADMIN_ID, 
            f"💥 <b>Бот упал с критической ошибкой!</b>\n\n"
            f"🚨 Ошибка: {str(e)[:1000]}",
            parse_mode='HTML'
        )
        raise
    finally:
        await on_shutdown()

if __name__ == '__main__':
    # Создаем папку для логов если нет
    os.makedirs('backups', exist_ok=True)
    
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n⏹️ Бот остановлен")
    except Exception as e:
        logging.critical(f"💥 Критическая ошибка при запуске: {e}")
