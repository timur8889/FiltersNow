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

# Загрузка переменных окружения
try:
    load_dotenv()
except Exception as e:
    print(f"Ошибка загрузки .env файла: {e}")
    print("Проверьте формат файла .env - каждая переменная должна быть на отдельной строке")

# ========== КОНФИГУРАЦИЯ ==========
class Config:
    """Класс конфигурации приложения"""
    
    def __init__(self):
        self.API_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
        if not self.API_TOKEN:
            raise ValueError("TELEGRAM_BOT_TOKEN не установен в переменных окружения")
        
        self.ADMIN_ID = int(os.getenv('ADMIN_ID', '5024165375'))
        self.GOOGLE_SHEETS_CREDENTIALS = os.getenv('GOOGLE_SHEETS_CREDENTIALS')
        self.GOOGLE_SHEET_ID = os.getenv('GOOGLE_SHEET_ID')
        
        # Настройки базы данных
        self.DB_PATH = 'filters.db'
        self.BACKUP_ENABLED = True
        self.BACKUP_PATH = 'backups'
        
        # Настройки rate limiting
        self.RATE_LIMIT_MAX_REQUESTS = 10
        self.RATE_LIMIT_WINDOW = 30
        
        # Настройки уведомлений
        self.REMINDER_CHECK_INTERVAL = 24 * 60 * 60  # 24 часа
        self.EARLY_REMINDER_DAYS = 7
        
        # Настройки кэширования
        self.CACHE_TTL = 300  # 5 минут
        
        # Настройки реального времени - УВЕЛИЧЕНА ЧАСТОТА СИНХРОНИЗАЦИИ
        self.REAL_TIME_SYNC_INTERVAL = 5  # 5 секунд вместо 60
        
    def validate(self) -> bool:
        """Проверка корректности конфигурации"""
        if not self.API_TOKEN:
            raise ValueError("TELEGRAM_BOT_TOKEN не установен")
        
        # Создаем папку для бэкапов
        if self.BACKUP_ENABLED and not os.path.exists(self.BACKUP_PATH):
            os.makedirs(self.BACKUP_PATH)
            
        return True

# Создаем экземпляр конфигурации
config = Config()

# ========== УЛУЧШЕННАЯ БЕЗОПАСНОСТЬ И ОБРАБОТКА ОШИБОК ==========
def enhanced_sanitize_input(text: str) -> str:
    """Улучшенная санитизация ввода"""
    if not text:
        return text
    
    # Удаляем потенциально опасные символы и ограничиваем длину
    sanitized = re.sub(r'[<>&\"\'\\;]', '', text)
    sanitized = sanitized.strip()
    
    # Ограничение длины
    if len(sanitized) > 500:
        sanitized = sanitized[:500]
    
    return sanitized

def safe_db_query(query: str, params: tuple) -> List[Dict]:
    """Безопасное выполнение SQL запросов"""
    try:
        with get_db_connection() as conn:
            cur = conn.cursor()
            cur.execute(query, params)
            return [dict(row) for row in cur.fetchall()]
    except sqlite3.Error as e:
        logging.error(f"SQL error: {e}")
        return []

# ========== УЛУЧШЕННЫЙ МЕНЕДЖЕР СОСТОЯНИЙ ==========
class StateManager:
    """Менеджер состояний для лучшего управления FSM"""
    
    @staticmethod
    async def safe_clear_state(state: FSMContext, message: types.Message = None):
        """Безопасная очистка состояния с обработкой ошибок"""
        try:
            await state.clear()
            if message:
                await message.answer("Состояние сброшено", reply_markup=get_main_keyboard())
        except Exception as e:
            logging.error(f"Error clearing state: {e}")

    @staticmethod
    async def set_state_with_timeout(state: FSMContext, new_state, timeout_minutes=30):
        """Установка состояния с таймаутом"""
        await state.set_state(new_state)
        await state.update_data(state_set_time=datetime.now())

# ========== СИНХРОННАЯ БАЗА ДАННЫХ ==========
@contextmanager
def get_db_connection():
    """Синхронный контекстный менеджер для работы с БД"""
    conn = sqlite3.connect(config.DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    except Exception as e:
        conn.rollback()
        raise e
    finally:
        conn.close()

def get_user_filters_db(user_id: int) -> List[Dict]:
    """Синхронное получение фильтров пользователя из БД"""
    try:
        with get_db_connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT * FROM filters WHERE user_id = ? ORDER BY expiry_date", (user_id,))
            rows = cur.fetchall()
            health_monitor.record_db_operation()
            health_monitor.record_cache_miss()
            return [dict(row) for row in rows]
    except Exception as e:
        logging.error(f"Ошибка при получении фильтров пользователя {user_id}: {e}")
        health_monitor.record_error()
        return []

def get_filter_by_id(filter_id: int, user_id: int) -> Optional[Dict]:
    """Синхронное получение фильтра по ID"""
    try:
        # Сначала проверяем кэш
        filters = get_user_filters(user_id)
        for f in filters:
            if f['id'] == filter_id:
                health_monitor.record_cache_hit()
                return f
        
        # Если не найдено в кэше, ищем в БД
        with get_db_connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT * FROM filters WHERE id = ? AND user_id = ?", (filter_id, user_id))
            result = cur.fetchone()
            health_monitor.record_db_operation()
            health_monitor.record_cache_miss()
            return dict(result) if result else None
    except Exception as e:
        logging.error(f"Ошибка при получении фильтра {filter_id}: {e}")
        health_monitor.record_error()
        return None

def get_all_users_stats() -> Dict:
    """Синхронное получение статистики"""
    try:
        with get_db_connection() as conn:
            cur = conn.cursor()
            cur.execute('''SELECT COUNT(DISTINCT user_id) as total_users, 
                                  COUNT(*) as total_filters,
                                  SUM(CASE WHEN expiry_date <= date('now') THEN 1 ELSE 0 END) as expired_filters,
                                  SUM(CASE WHEN expiry_date BETWEEN date('now') AND date('now', '+7 days') THEN 1 ELSE 0 END) as expiring_soon
                           FROM filters''')
            result = cur.fetchone()
            health_monitor.record_db_operation()
            return dict(result) if result else {'total_users': 0, 'total_filters': 0, 'expired_filters': 0, 'expiring_soon': 0}
    except Exception as e:
        logging.error(f"Ошибка при получении статистики: {e}")
        health_monitor.record_error()
        return {'total_users': 0, 'total_filters': 0, 'expired_filters': 0, 'expiring_soon': 0}

def add_filter_to_db(user_id: int, filter_type: str, location: str, last_change: str, expiry_date: str, lifetime_days: int) -> bool:
    """Добавление фильтра в БД с принудительной инвалидацией кэша"""
    try:
        with get_db_connection() as conn:
            cur = conn.cursor()
            cur.execute('''INSERT INTO filters 
                          (user_id, filter_type, location, last_change, expiry_date, lifetime_days) 
                          VALUES (?, ?, ?, ?, ?, ?)''',
                          (user_id, filter_type, location, last_change, expiry_date, lifetime_days))
            
            health_monitor.record_db_operation()
            
            # ПРИНУДИТЕЛЬНАЯ инвалидация кэша пользователя
            cache_manager.invalidate_user_cache(user_id)
            
            # Очищаем LRU кэш полностью
            cache_manager.lru_cache.cache.clear()
            
            # Мгновенная синхронизация при добавлении
            if google_sync.auto_sync and google_sync.is_configured():
                # Получаем СВЕЖИЕ данные из БД, минуя кэш
                fresh_filters = get_user_filters_db(user_id)
                google_sync.sync_to_sheets(user_id, fresh_filters)
            
            return True
    except Exception as e:
        logging.error(f"Ошибка при добавлении фильтра: {e}")
        health_monitor.record_error()
        return False

def update_filter_in_db(filter_id: int, user_id: int, **kwargs) -> bool:
    """Обновление фильтра в БД"""
    try:
        if not kwargs:
            return False
        
        with get_db_connection() as conn:
            cur = conn.cursor()
            set_clause = ", ".join([f"{key} = ?" for key in kwargs.keys()])
            values = list(kwargs.values())
            values.extend([filter_id, user_id])
            
            cur.execute(f"UPDATE filters SET {set_clause} WHERE id = ? AND user_id = ?", values)
            
            health_monitor.record_db_operation()
            
            # Инвалидируем кэш пользователя
            cache_manager.invalidate_user_cache(user_id)
            
            # Мгновенная синхронизация при обновлении
            if google_sync.auto_sync and google_sync.is_configured():
                filters = get_user_filters(user_id)
                google_sync.sync_to_sheets(user_id, filters)
            
            return cur.rowcount > 0
    except Exception as e:
        logging.error(f"Ошибка при обновлении фильтра {filter_id}: {e}")
        health_monitor.record_error()
        return False

def delete_filter_from_db(filter_id: int, user_id: int) -> bool:
    """Удаление фильтра из БД"""
    try:
        with get_db_connection() as conn:
            cur = conn.cursor()
            cur.execute("DELETE FROM filters WHERE id = ? AND user_id = ?", (filter_id, user_id))
            
            health_monitor.record_db_operation()
            
            # Инвалидируем кэш пользователя
            cache_manager.invalidate_user_cache(user_id)
            
            # Мгновенная синхронизация при удалении
            if google_sync.auto_sync and google_sync.is_configured():
                filters = get_user_filters(user_id)
                google_sync.sync_to_sheets(user_id, filters)
            
            return cur.rowcount > 0
    except Exception as e:
        logging.error(f"Ошибка при удалении фильтра {filter_id}: {e}")
        health_monitor.record_error()
        return False

def init_db():
    """Синхронная инициализация базы данных"""
    try:
        with get_db_connection() as conn:
            cur = conn.cursor()
            
            # Создаем таблицу если не существует
            cur.execute('''
                CREATE TABLE IF NOT EXISTS filters (
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
            cur.execute('''CREATE INDEX IF NOT EXISTS idx_user_id ON filters(user_id)''')
            cur.execute('''CREATE INDEX IF NOT EXISTS idx_expiry_date ON filters(expiry_date)''')
            cur.execute('''CREATE INDEX IF NOT EXISTS idx_user_expiry ON filters(user_id, expiry_date)''')
            
            logging.info("База данных успешно инициализирована")
                
    except Exception as e:
        logging.error(f"Критическая ошибка инициализации БД: {e}")
        # Создаем резервную копию при критической ошибке
        if os.path.exists(config.DB_PATH):
            backup_name = f'filters_backup_critical_{datetime.now().strftime("%Y%m%d_%H%M%S")}.db'
            try:
                shutil.copy2(config.DB_PATH, backup_name)
                logging.info(f"Создана критическая резервная копия: {backup_name}")
            except Exception as backup_error:
                logging.error(f"Не удалось создать резервную копию: {backup_error}")
        raise

def check_and_update_schema():
    """Проверка и обновление схема базы данных"""
    try:
        with get_db_connection() as conn:
            # Проверяем существование колонок
            cur = conn.cursor()
            cur.execute("PRAGMA table_info(filters)")
            columns = [row[1] for row in cur.fetchall()]
            
            # Добавляем недостающие колонки
            if 'created_at' not in columns:
                cur.execute("ALTER TABLE filters ADD COLUMN created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP")
                logging.info("Добавлена колонка created_at")
            
            if 'updated_at' not in columns:
                cur.execute("ALTER TABLE filters ADD COLUMN updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP")
                logging.info("Добавлена колонка updated_at")
            
            # Создаем недостающие индексы
            cur.execute("CREATE INDEX IF NOT EXISTS idx_user_expiry ON filters(user_id, expiry_date)")
            
    except Exception as e:
        logging.error(f"Ошибка при обновлении схемы БД: {e}")

# ========== УЛУЧШЕННАЯ СИСТЕМА КЭШИРОВАНИЯ ==========
class LRUCache:
    """LRU кэш с ограничением по памяти"""
    
    def __init__(self, max_size: int = 1000):
        self.cache = OrderedDict()
        self.max_size = max_size
        self.lock = threading.Lock()
    
    def get(self, key):
        with self.lock:
            if key not in self.cache:
                return None
            self.cache.move_to_end(key)
            return self.cache[key]
    
    def set(self, key, value):
        with self.lock:
            if key in self.cache:
                self.cache.move_to_end(key)
            self.cache[key] = value
            if len(self.cache) > self.max_size:
                self.cache.popitem(last=False)

class EnhancedCacheManager:
    """Улучшенный менеджер кэширования для улучшения производительности"""
    
    def __init__(self):
        self._user_filters_cache = {}
        self._user_stats_cache = {}
        self._cache_ttl = {
            'filters': 300,  # 5 минут
            'stats': 60,     # 1 минута
            'general': 600   # 10 минут
        }
        self.lru_cache = LRUCache(max_size=500)
        self.hit_stats = {}
        self.miss_stats = {}
    
    def get_user_filters(self, user_id: int):
        """Получение фильтров с улучшенным кэшированием"""
        cache_key = f"filters_{user_id}"
        
        # Проверяем LRU кэш first
        cached = self.lru_cache.get(cache_key)
        if cached:
            data, timestamp = cached
            if time.time() - timestamp < self._cache_ttl['filters']:
                self._record_hit(user_id)
                return data
        
        # Проверяем обычный кэш
        if cache_key in self._user_filters_cache:
            data, timestamp = self._user_filters_cache[cache_key]
            if time.time() - timestamp < self._cache_ttl['filters']:
                self._record_hit(user_id)
                return data
        
        # Загрузка из БД
        filters = get_user_filters_db(user_id)
        self.lru_cache.set(cache_key, (filters, time.time()))
        self._user_filters_cache[cache_key] = (filters, time.time())
        self._record_miss(user_id)
        return filters
    
    def get_user_stats(self, user_id: int):
        """Получение статистики с кэшированием"""
        cache_key = f"stats_{user_id}"
        
        if cache_key in self._user_stats_cache:
            data, timestamp = self._user_stats_cache[cache_key]
            if time.time() - timestamp < self._cache_ttl['stats']:
                return data
        
        # Загрузка из БД
        filters = self.get_user_filters(user_id)
        stats = self._calculate_user_stats(filters)
        self._user_stats_cache[cache_key] = (stats, time.time())
        return stats
    
    def _calculate_user_stats(self, filters: List[Dict]) -> Dict:
        """Расчет статистики пользователя"""
        today = datetime.now().date()
        stats = {
            'total': len(filters),
            'expired': 0,
            'expiring_soon': 0,
            'normal': 0,
            'total_days_until_expiry': 0
        }
        
        for f in filters:
            expiry_date = datetime.strptime(str(f['expiry_date']), '%Y-%m-%d').date()
            days_until = (expiry_date - today).days
            stats['total_days_until_expiry'] += max(0, days_until)
            
            if days_until <= 0:
                stats['expired'] += 1
            elif days_until <= 7:
                stats['expiring_soon'] += 1
            else:
                stats['normal'] += 1
        
        if stats['total'] > 0:
            stats['avg_days_until_expiry'] = stats['total_days_until_expiry'] / stats['total']
            stats['health_percentage'] = (stats['normal'] / stats['total']) * 100
        else:
            stats['avg_days_until_expiry'] = 0
            stats['health_percentage'] = 0
            
        return stats
    
    def invalidate_user_cache(self, user_id: int):
        """Инвалидация кэша пользователя"""
        cache_key_filters = f"filters_{user_id}"
        cache_key_stats = f"stats_{user_id}"
        self._user_filters_cache.pop(cache_key_filters, None)
        self._user_stats_cache.pop(cache_key_stats, None)
        self.lru_cache.cache.pop(cache_key_filters, None)
    
    def clear_all_cache(self):
        """Очистка всего кэша"""
        self._user_filters_cache.clear()
        self._user_stats_cache.clear()
        self.lru_cache.cache.clear()
    
    def _record_hit(self, user_id: int):
        if user_id not in self.hit_stats:
            self.hit_stats[user_id] = 0
        self.hit_stats[user_id] += 1
    
    def _record_miss(self, user_id: int):
        if user_id not in self.miss_stats:
            self.miss_stats[user_id] = 0
        self.miss_stats[user_id] += 1
    
    def get_cache_stats(self, user_id: int) -> Dict:
        """Получение статистики кэша для пользователя"""
        hits = self.hit_stats.get(user_id, 0)
        misses = self.miss_stats.get(user_id, 0)
        total = hits + misses
        hit_rate = (hits / total * 100) if total > 0 else 0
        
        return {
            'hits': hits,
            'misses': misses,
            'total_requests': total,
            'hit_rate': hit_rate,
            'lru_cache_size': len(self.lru_cache.cache)
        }

# Создаем экземпляр улучшенного кэш менеджера
cache_manager = EnhancedCacheManager()

# Обертки для совместимости
def get_user_filters(user_id: int) -> List[Dict]:
    """Получение фильтров пользователя"""
    return cache_manager.get_user_filters(user_id)

def get_fresh_user_filters(user_id: int) -> List[Dict]:
    """Получение свежих данных из БД, минуя кэш"""
    cache_manager.invalidate_user_cache(user_id)
    return get_user_filters_db(user_id)

def force_refresh_user_cache(user_id: int):
    """Принудительное обновление кэша пользователя"""
    cache_manager.invalidate_user_cache(user_id)
    # Очищаем соответствующие ключи в LRU кэше
    cache_keys = [f"filters_{user_id}", f"stats_{user_id}"]
    for key in cache_keys:
        cache_manager.lru_cache.cache.pop(key, None)
    
    # Получаем свежие данные
    return get_user_filters_db(user_id)

# ========== УМНАЯ СИСТЕМА НАПОМИНАНИЙ ==========
class SmartReminderSystem:
    """Умная система напоминаний"""
    
    def __init__(self):
        self.user_preferences = {}
        self.load_user_preferences()
    
    def load_user_preferences(self):
        """Загрузка предпочтений пользователей"""
        try:
            if os.path.exists('user_preferences.json'):
                with open('user_preferences.json', 'r', encoding='utf-8') as f:
                    self.user_preferences = json.load(f)
        except Exception as e:
            logging.error(f"Error loading user preferences: {e}")
    
    def save_user_preferences(self):
        """Сохранение предпочтений пользователей"""
        try:
            with open('user_preferences.json', 'w', encoding='utf-8') as f:
                json.dump(self.user_preferences, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logging.error(f"Error saving user preferences: {e}")
    
    def get_user_reminder_time(self, user_id: int) -> str:
        """Получение предпочтительного времени напоминаний"""
        return self.user_preferences.get(str(user_id), {}).get('reminder_time', '10:00')
    
    def set_user_reminder_time(self, user_id: int, time_str: str):
        """Установка времени напоминаний"""
        if str(user_id) not in self.user_preferences:
            self.user_preferences[str(user_id)] = {}
        self.user_preferences[str(user_id)]['reminder_time'] = time_str
        self.save_user_preferences()

smart_reminders = SmartReminderSystem()

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
    """Обновленная клавиатура главного меню"""
    builder = ReplyKeyboardBuilder()
    builder.button(text="📋 Мои фильтры")
    builder.button(text="✨ Добавить фильтр")
    builder.button(text="⚙️ Управление фильтрами")
    builder.button(text="📊 Статистика")
    builder.button(text="📤 Импорт/Экспорт")
    builder.button(text="⏰ Настройка напоминаний")
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

def get_reminder_keyboard(filter_id: int):
    """Инлайн клавиатура для быстрых действий с напоминаниями"""
    builder = InlineKeyboardBuilder()
    builder.button(text="✅ Заменил", callback_data=f"replaced_{filter_id}")
    builder.button(text="🔄 Перенести на неделю", callback_data=f"postpone_{filter_id}")
    builder.button(text="📅 Посмотреть детали", callback_data=f"details_{filter_id}")
    builder.adjust(1)
    return builder.as_markup()

def get_reminder_settings_keyboard():
    """Клавиатура настроек напоминаний"""
    builder = ReplyKeyboardBuilder()
    builder.button(text="🕘 09:00")
    builder.button(text="🕙 10:00")
    builder.button(text="🕚 11:00")
    builder.button(text="🕛 12:00")
    builder.button(text="🕐 13:00")
    builder.button(text="🕑 14:00")
    builder.button(text="🕒 15:00")
    builder.button(text="🕓 16:00")
    builder.button(text="🔙 Назад")
    builder.adjust(3)
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

def create_progress_bar(percentage: float, length: int = 10) -> str:
    """Создание текстового прогресс-бара"""
    filled = int(length * percentage / 100)
    empty = length - filled
    return f"[{'█' * filled}{'░' * empty}] {percentage:.1f}%"

def format_filter_status_with_progress(filter_data: Dict) -> str:
    """Форматирование статуса с прогресс-баром"""
    expiry_date = datetime.strptime(str(filter_data['expiry_date']), '%Y-%m-%d').date()
    last_change = datetime.strptime(str(filter_data['last_change']), '%Y-%m-%d').date()
    days_total = filter_data['lifetime_days']
    days_passed = (datetime.now().date() - last_change).days
    percentage = min(100, max(0, (days_passed / days_total) * 100))
    
    progress_bar = create_progress_bar(percentage)
    days_until = (expiry_date - datetime.now().date()).days
    
    return f"{progress_bar} ({days_passed}/{days_total} дней, осталось: {days_until} дней)"

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
    if total > 0:
        health_percentage = (normal / total) * 100
        progress_bar = create_progress_bar(health_percentage)
    else:
        progress_bar = create_progress_bar(0)
    
    return (
        f"📊 <b>СТАТУС ФИЛЬТРОВ:</b>\n"
        f"{progress_bar}\n"
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
            backup_path = os.path.join(config.BACKUP_PATH, backup_name)
            shutil.copy2(config.DB_PATH, backup_path)
            logging.info(f"Создана резервная копия: {backup_path}")
            
            # Очистка старых бэкапов (оставляем последние 10)
            backup_files = [f for f in os.listdir(config.BACKUP_PATH) if f.startswith('filters_backup_')]
            backup_files.sort(reverse=True)
            for old_backup in backup_files[10:]:
                os.remove(os.path.join(config.BACKUP_PATH, old_backup))
                logging.info(f"Удален старый бэкап: {old_backup}")
                
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

def check_user_permission(user_id: int, filter_id: int) -> bool:
    """Проверка прав пользователя на фильтр"""
    try:
        filter_data = get_filter_by_id(filter_id, user_id)
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

# ========== УЛУЧШЕННАЯ ВАЛИДАЦИЯ ДАТ ==========
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

def enhanced_validate_date(date_str: str) -> datetime.date:
    """Улучшенная валидация даты с расширенной поддержкой форматов"""
    date_str = date_str.strip()
    
    if not date_str:
        raise ValueError("Дата не может быть пустой")
    
    # Автозамена различных разделителей
    date_str = re.sub(r'[/\-,\s]', '.', date_str)
    
    # Удаляем лишние символы, но оставляем точки
    date_str = re.sub(r'[^\d\.]', '', date_str)
    
    # Расширенный список форматов
    formats = [
        '%d.%m.%y', '%d.%m.%Y', 
        '%d%m%y', '%d%m%Y', 
        '%d.%m', '%d%m',
        '%Y.%m.%d', '%y.%m.%d'
    ]
    
    for fmt in formats:
        try:
            if fmt in ['%d.%m', '%d%m']:
                # Добавляем текущий год для форматов без года
                date_obj = datetime.strptime(date_str, fmt).date()
                date_obj = date_obj.replace(year=datetime.now().year)
            elif fmt in ['%d%m%y', '%d%m%Y']:
                # Проверяем длину для форматов без разделителей
                if len(date_str) in [6, 8]:
                    date_obj = datetime.strptime(date_str, fmt).date()
                else:
                    continue
            else:
                date_obj = datetime.strptime(date_str, fmt).date()
            
            # Проверяем разумность даты
            today = datetime.now().date()
            max_past = today - timedelta(days=10*365)  # 10 лет назад
            max_future = today + timedelta(days=30)    # 30 дней вперед
            
            if date_obj > max_future:
                raise ValueError("Дата не может быть более чем на 30 дней в будущем")
            if date_obj < max_past:
                raise ValueError("Дата слишком старая (более 10 лет)")
                
            return date_obj
        except ValueError:
            continue
    
    # Попытка автоматического исправления
    corrected = try_auto_correct_date(date_str)
    if corrected:
        today = datetime.now().date()
        if corrected <= today + timedelta(days=30) and corrected >= today - timedelta(days=10*365):
            return corrected
    
    raise ValueError(
        "Неверный формат даты. Используйте:\n"
        "• ДД.ММ.ГГ (например, 15.12.23)\n"
        "• ДД.ММ.ГГГГ (например, 15.12.2023)\n"
        "• ДД.ММ (текущий год будет автоматически)"
    )

def validate_date(date_str: str) -> datetime.date:
    """Обертка для обратной совместимости"""
    return enhanced_validate_date(date_str)

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
        self.cache_hits = 0
        self.cache_misses = 0
    
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
    
    def record_cache_hit(self):
        """Запись попадания в кэш"""
        self.cache_hits += 1
    
    def record_cache_miss(self):
        """Запись промаха кэша"""
        self.cache_misses += 1
    
    def get_cache_hit_rate(self) -> float:
        """Получение процента попаданий в кэш"""
        total = self.cache_hits + self.cache_misses
        return (self.cache_hits / total * 100) if total > 0 else 0
    
    def get_health_status(self):
        """Получение статуса здоровья бота"""
        uptime = datetime.now() - self.start_time
        active_users = len([uid for uid, count in self.user_actions.items() if count > 0])
        
        health_score = (self.message_count - self.error_count) / max(1, self.message_count) * 100
        
        return {
            'uptime': str(uptime),
            'message_count': self.message_count,
            'error_count': self.error_count,
            'active_users': active_users,
            'health_score': health_score,
            'cache_hit_rate': self.get_cache_hit_rate()
        }
    
    def get_detailed_status(self):
        """Получение детального статуса"""
        basic_status = self.get_health_status()
        basic_status.update({
            'db_operations': self.db_operations,
            'sync_operations': self.sync_operations,
            'active_sessions': len(self.user_sessions),
            'database_size': self.get_database_size(),
            'memory_usage': self.get_memory_usage(),
            'cache_hits': self.cache_hits,
            'cache_misses': self.cache_misses
        })
        return basic_status
    
    def get_database_size(self):
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

# ========== УЛУЧШЕНИЕ: MIDDLEWARE ДЛЯ RATE LIMITING И КЭШИРОВАНИЯ ==========
class EnhancedMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[types.TelegramObject, Dict[str, Any]], Any],
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
dp.update.outer_middleware(EnhancedMiddleware())

# ========== ЭКСПОРТ В EXCEL ==========
def export_to_excel(user_id: int) -> io.BytesIO:
    """Экспорт фильтров в Excel"""
    filters = get_user_filters(user_id)
    
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
    
    # Добавляем прогресс-бар
    def calculate_progress(row):
        expiry_date = datetime.strptime(row['expiry_date'], '%d.%m.%Y').date()
        last_change = datetime.strptime(row['last_change'], '%d.%m.%Y').date()
        days_total = row['lifetime_days']
        days_passed = (datetime.now().date() - last_change).days
        percentage = min(100, max(0, (days_passed / days_total) * 100))
        return create_progress_bar(percentage)
    
    df['Прогресс'] = df.apply(calculate_progress, axis=1)
    
    # Создаем Excel файл в памяти
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        df.to_excel(writer, sheet_name='Фильтры', index=False)
        
        # Получаем workbook и worksheet для форматирования
        workbook = writer.book
        worksheet = writer.sheets['Фильтры']
        
        # Настраиваем ширину колонок
        for column in worksheet.columns:
            max_length = 0
            column_letter = column[0].column_letter
            for cell in column:
                try:
                    if len(str(cell.value)) > max_length:
                        max_length = len(str(cell.value))
                except:
                    pass
            adjusted_width = min(max_length + 2, 50)
            worksheet.column_dimensions[column_letter].width = adjusted_width
    
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

# ========== УЛУЧШЕННАЯ СИНХРОННАЯ GOOGLE SHEETS ИНТЕГРАЦИЯ ==========
class GoogleSheetsSync:
    def __init__(self):
        self.credentials = None
        self.sheet_id = None
        self.auto_sync = False
        self.last_sync_time = {}
        self.sync_interval = config.REAL_TIME_SYNC_INTERVAL  # 5 секунд
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
    
    def initialize_credentials(self):
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
            
            # Создаем credentials
            scope = ['https://spreadsheets.google.com/feeds', 
                    'https://www.googleapis.com/auth/drive']
            self.credentials = Credentials.from_service_account_info(credentials_info, scopes=scope)
            return True
            
        except Exception as e:
            logging.error(f"Ошибка инициализации Google Sheets: {e}")
            return False
    
    def sync_to_sheets(self, user_id: int, user_filters: List[Dict]) -> tuple[bool, str]:
        """Синхронизация данных с Google Sheets с полной очисткой"""
        try:
            if not self.is_configured():
                return False, "Синхронизация не настроена"
            
            if not self.credentials:
                if not self.initialize_credentials():
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
                
                # ПОЛНОСТЬЮ очищаем весь лист
                worksheet.clear()
                
            except gspread.exceptions.WorksheetNotFound:
                worksheet = sheet.add_worksheet(title=worksheet_name, rows=100, cols=10)
            
            # Заголовки
            headers = ['ID', 'Тип фильтра', 'Местоположение', 'Дата замены', 
                      'Срок службы (дни)', 'Годен до', 'Статус', 'Осталось дней']
            worksheet.append_row(headers)
            
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
                sheet.batch_update({
                    "requests": [
                        {
                            "autoResizeDimensions": {
                                "dimensions": {
                                    "sheetId": worksheet.id,
                                    "dimension": "COLUMNS",
                                    "startIndex": 0,
                                    "endIndex": 8
                                }
                            }
                        }
                    ]
                })
            except Exception as format_error:
                logging.warning(f"Ошибка форматирования таблица: {format_error}")
            
            # Обновляем время последней синхронизации
            self.last_sync_time[user_id] = datetime.now()
            
            health_monitor.record_sync_operation()
            return True, f"Успешно синхронизировано {len(rows)} фильтров"
            
        except Exception as e:
            logging.error(f"Ошибка синхронизации с Google Sheets: {e}")
            health_monitor.record_error()
            return False, f"Ошибка синхронизации: {str(e)}"
    
    def should_sync_user(self, user_id: int) -> bool:
        """Проверка необходимости синхронизации для пользователя"""
        if not self.auto_sync or not self.is_configured():
            return False
        
        last_sync = self.last_sync_time.get(user_id)
        if not last_sync:
            return True
        
        time_since_last_sync = (datetime.now() - last_sync).total_seconds()
        return time_since_last_sync >= self.sync_interval

# Создаем экземпляр синхронизации
google_sync = GoogleSheetsSync()

# ========== СИНХРОННАЯ СИНХРОНИЗАЦИЯ ==========
def safe_sync_to_sheets(user_id: int, filters: List[Dict]) -> tuple[bool, str]:
    """Безопасная синхронизация с обработкой ошибок"""
    try:
        health_monitor.record_sync_operation()
        return google_sync.sync_to_sheets(user_id, filters)
    except ImportError:
        return False, "Библиотеки Google не установлены. Установите: pip install gspread google-auth"
    except Exception as e:
        logging.error(f"Ошибка синхронизации: {e}")
        return False, f"Ошибка синхронизации: {str(e)}"

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
                f"🔧 <i>Подробности в логах</i>",
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

# ========== СИНХРОННЫЕ ФОНОВЫЕ ЗАДАЧИ ==========
def send_personalized_reminders():
    """Персонализированные напоминания с учетом времени суток"""
    while True:
        try:
            # Получаем текущий час для персонализации
            current_hour = datetime.now().hour
            greeting = "Доброе утро" if 5 <= current_hour < 12 else "Добрый день" if 12 <= current_hour < 18 else "Добрый вечер"
            
            with get_db_connection() as conn:
                cur = conn.cursor()
                cur.execute('''
                    SELECT DISTINCT user_id FROM filters 
                    WHERE expiry_date BETWEEN date('now') AND date('now', '+7 days')
                    OR expiry_date <= date('now')
                ''')
                users_to_notify = cur.fetchall()
                
                for user_row in users_to_notify:
                    user_id = user_row[0]
                    filters = get_user_filters(user_id)
                    
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
                            # Отправляем сообщение с инлайн кнопками для быстрых действий
                            if expired_filters:
                                first_expired_id = expired_filters[0][0]['id']
                                # Используем асинхронный вызов для отправки сообщения
                                asyncio.create_task(bot.send_message(
                                    user_id, 
                                    message, 
                                    parse_mode='HTML',
                                    reply_markup=get_reminder_keyboard(first_expired_id)
                                ))
                            else:
                                asyncio.create_task(bot.send_message(user_id, message, parse_mode='HTML'))
                                
                        except Exception as e:
                            logging.warning(f"Не удалось отправить напоминание пользователю {user_id}: {e}")
            
            time.sleep(23 * 60 * 60)  # Проверяем каждые 23 часа
            
        except Exception as e:
            logging.error(f"Ошибка в задаче напоминаний: {e}")
            time.sleep(60 * 60)

def health_monitoring_task():
    """Фоновая задача мониторинга здоровья"""
    while True:
        try:
            health_status = health_monitor.get_detailed_status()
            
            # Логируем каждые 30 минут
            if health_status['message_count'] % 30 == 0:
                logging.info(f"Статус здоровья: {health_status}")
            
            # Уведомляем администратора при низком health score
            if health_status['health_score'] < 80 and config.ADMIN_ID:
                try:
                    asyncio.create_task(bot.send_message(
                        config.ADMIN_ID,
                        f"⚠️ <b>НИЗКИЙ HEALTH SCORE</b>\n\n"
                        f"📊 Текущий score: {health_status['health_score']:.1f}%\n"
                        f"💥 Ошибок: {health_status['error_count']}\n"
                        f"📨 Сообщений: {health_status['message_count']}\n"
                        f"💾 Hit Rate кэша: {health_status['cache_hit_rate']:.1f}%",
                        parse_mode='HTML'
                    ))
                except Exception as e:
                    logging.warning(f"Не удалось отправить уведомление администратору: {e}")
            
            # Очистка кэша каждые 6 часов
            if datetime.now().hour % 6 == 0 and datetime.now().minute < 5:
                cache_manager.clear_all_cache()
                logging.info("Выполнена очистка кэша")
            
            time.sleep(60 * 30)  # Проверяем каждые 30 минут
            
        except Exception as e:
            logging.error(f"Ошибка в задаче мониторинга: {e}")
            time.sleep(60 * 5)

def real_time_sync_task():
    """Улучшенная задача реального времени синхронизации"""
    logging.info("🚀 Запуск фоновой задачи автосинхронизации (интервал: 5 секунд)")
    
    while True:
        try:
            if google_sync.auto_sync and google_sync.is_configured():
                # Получаем всех пользователей с фильтрами
                with get_db_connection() as conn:
                    cur = conn.cursor()
                    cur.execute("SELECT DISTINCT user_id FROM filters")
                    users = cur.fetchall()
                    
                    synced_users = 0
                    total_users = len(users)
                    
                    for user_row in users:
                        user_id = user_row[0]
                        
                        # Проверяем необходимость синхронизации для этого пользователя
                        if google_sync.should_sync_user(user_id):
                            filters = get_user_filters(user_id)
                            if filters:
                                success, message = google_sync.sync_to_sheets(user_id, filters)
                                if success:
                                    logging.debug(f"✅ Автосинхронизация для пользователя {user_id}: {message}")
                                    synced_users += 1
                                else:
                                    logging.warning(f"❌ Ошибка автосинхронизации для пользователя {user_id}: {message}")
                    
                    # Логируем статистику каждые 10 циклов
                    if hasattr(real_time_sync_task, 'cycle_count'):
                        real_time_sync_task.cycle_count += 1
                    else:
                        real_time_sync_task.cycle_count = 1
                    
                    if real_time_sync_task.cycle_count % 10 == 0:
                        logging.info(f"📊 Статистика автосинхронизации: {synced_users}/{total_users} пользователей")
            
            time.sleep(config.REAL_TIME_SYNC_INTERVAL)  # Интервал синхронизации 5 секунд
            
        except Exception as e:
            logging.error(f"❌ Ошибка в задаче реального времени: {e}")
            time.sleep(10)  # При ошибке ждем 10 секунд перед повторной попыткой

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

# ========== ОБРАБОТЧИКИ INLINE КНОПОК ==========
@dp.callback_query(lambda c: c.data.startswith('replaced_'))
async def process_replaced_filter(callback_query: types.CallbackQuery):
    """Обработка кнопки 'Заменил'"""
    try:
        filter_id = int(callback_query.data.split('_')[1])
        user_id = callback_query.from_user.id
        
        # Обновляем дату замены на сегодня
        today = datetime.now().date()
        success = update_filter_in_db(
            filter_id, 
            user_id, 
            last_change=today.strftime('%Y-%m-%d'),
            expiry_date=(today + timedelta(days=180)).strftime('%Y-%m-%d')  # Стандартный срок 180 дней
        )
        
        if success:
            await callback_query.message.edit_text(
                f"✅ <b>ФИЛЬТР ОБНОВЛЕН!</b>\n\n"
                f"Дата замены установлена на сегодня.\n"
                f"Следующая замена через 180 дней.",
                parse_mode='HTML'
            )
        else:
            await callback_query.answer("❌ Ошибка при обновлении фильтра", show_alert=True)
            
    except Exception as e:
        logging.error(f"Ошибка при обработке replaced: {e}")
        await callback_query.answer("❌ Произошла ошибка", show_alert=True)

@dp.callback_query(lambda c: c.data.startswith('postpone_'))
async def process_postpone_filter(callback_query: types.CallbackQuery):
    """Обработка кнопки 'Перенести на неделю'"""
    try:
        filter_id = int(callback_query.data.split('_')[1])
        user_id = callback_query.from_user.id
        
        filter_data = get_filter_by_id(filter_id, user_id)
        if not filter_data:
            await callback_query.answer("❌ Фильтр не найден", show_alert=True)
            return
        
        # Переносим на 7 дней вперед
        current_expiry = datetime.strptime(str(filter_data['expiry_date']), '%Y-%m-%d').date()
        new_expiry = current_expiry + timedelta(days=7)
        
        success = update_filter_in_db(
            filter_id, 
            user_id, 
            expiry_date=new_expiry.strftime('%Y-%m-%d')
        )
        
        if success:
            await callback_query.message.edit_text(
                f"🔄 <b>СРОК ПЕРЕНЕСЕН!</b>\n\n"
                f"Новая дата замены: {format_date_nice(new_expiry)}",
                parse_mode='HTML'
            )
        else:
            await callback_query.answer("❌ Ошибка при переносе срока", show_alert=True)
            
    except Exception as e:
        logging.error(f"Ошибка при обработке postpone: {e}")
        await callback_query.answer("❌ Произошла ошибка", show_alert=True)

@dp.callback_query(lambda c: c.data.startswith('details_'))
async def process_details_filter(callback_query: types.CallbackQuery):
    """Обработка кнопки 'Посмотреть детали'"""
    try:
        filter_id = int(callback_query.data.split('_')[1])
        user_id = callback_query.from_user.id
        
        filter_data = get_filter_by_id(filter_id, user_id)
        if not filter_data:
            await callback_query.answer("❌ Фильтр не найден", show_alert=True)
            return
        
        expiry_date = datetime.strptime(str(filter_data['expiry_date']), '%Y-%m-%d').date()
        last_change = datetime.strptime(str(filter_data['last_change']), '%Y-%m-%d').date()
        days_until = (expiry_date - datetime.now().date()).days
        icon, status = get_status_icon_and_text(days_until)
        
        details_text = (
            f"🔍 <b>ДЕТАЛИ ФИЛЬТРА #{filter_id}</b>\n\n"
            f"{icon} <b>Статус:</b> {status}\n"
            f"💧 <b>Тип:</b> {filter_data['filter_type']}\n"
            f"📍 <b>Местоположение:</b> {filter_data['location']}\n"
            f"📅 <b>Последняя замена:</b> {format_date_nice(last_change)}\n"
            f"⏰ <b>Годен до:</b> {format_date_nice(expiry_date)}\n"
            f"⏱️ <b>Срок службы:</b> {filter_data['lifetime_days']} дней\n"
            f"📊 <b>Прогресс:</b> {format_filter_status_with_progress(filter_data)}"
        )
        
        await callback_query.message.edit_text(details_text, parse_mode='HTML')
        
    except Exception as e:
        logging.error(f"Ошибка при обработке details: {e}")
        await callback_query.answer("❌ Произошла ошибка", show_alert=True)

# ========== ОБРАБОТЧИКИ УПРАВЛЕНИЯ ФИЛЬТРАМИ ==========

@dp.message(F.text == "✏️ Редактировать фильтр")
async def cmd_edit_filter(message: types.Message, state: FSMContext):
    """Начало редактирования фильтра"""
    health_monitor.record_message(message.from_user.id)
    
    filters = get_user_filters(message.from_user.id)
    if not filters:
        await message.answer(
            "❌ <b>Нет фильтров для редактирования</b>\n\n"
            "Сначала добавьте фильтры через меню '✨ Добавить фильтр'",
            reply_markup=get_management_keyboard(),
            parse_mode='HTML'
        )
        return
    
    await show_filters_for_selection(message, filters, "редактирования")
    await state.set_state(EditFilterStates.waiting_filter_selection)

@dp.message(EditFilterStates.waiting_filter_selection)
async def process_edit_filter_selection(message: types.Message, state: FSMContext):
    """Обработка выбора фильтра для редактирования"""
    if message.text == "🔙 Назад":
        await state.clear()
        await message.answer("🔙 Возврат в меню управления", reply_markup=get_management_keyboard())
        return
    
    # Парсим ID фильтра из текста (формат: "#123 - Тип фильтра (Местоположение)")
    match = re.search(r'#(\d+)', message.text)
    if not match:
        await message.answer(
            "❌ <b>Неверный формат выбора</b>\n\n"
            "Пожалуйста, выберите фильтр из списка:",
            reply_markup=get_filters_selection_keyboard(get_user_filters(message.from_user.id), "редактирования")
        )
        return
    
    filter_id = int(match.group(1))
    user_id = message.from_user.id
    
    # Проверяем существование фильтра и права доступа
    filter_data = get_filter_by_id(filter_id, user_id)
    if not filter_data:
        await message.answer(
            "❌ <b>Фильтр не найден</b>\n\n"
            "Пожалуйста, выберите фильтр из списка:",
            reply_markup=get_filters_selection_keyboard(get_user_filters(user_id), "редактирования")
        )
        return
    
    # Сохраняем ID фильтра в состоянии
    await state.update_data(selected_filter_id=filter_id, filter_data=filter_data)
    
    await message.answer(
        f"✏️ <b>РЕДАКТИРОВАНИЕ ФИЛЬТРА #{filter_id}</b>\n\n"
        f"💧 <b>Текущие данные:</b>\n"
        f"• Тип: {filter_data['filter_type']}\n"
        f"• Местоположение: {filter_data['location']}\n"
        f"• Дата замены: {format_date_nice(datetime.strptime(str(filter_data['last_change']), '%Y-%m-%d'))}\n"
        f"• Срок службы: {filter_data['lifetime_days']} дней\n\n"
        f"<b>Выберите поле для редактирования:</b>",
        reply_markup=get_edit_keyboard(),
        parse_mode='HTML'
    )
    await state.set_state(EditFilterStates.waiting_field_selection)

@dp.message(EditFilterStates.waiting_field_selection)
async def process_edit_field_selection(message: types.Message, state: FSMContext):
    """Обработка выбора поля для редактирования"""
    if message.text == "🔙 Назад":
        filters = get_user_filters(message.from_user.id)
        await show_filters_for_selection(message, filters, "редактирования")
        await state.set_state(EditFilterStates.waiting_filter_selection)
        return
    
    field_mapping = {
        "💧 Тип фильтра": "filter_type",
        "📍 Местоположение": "location", 
        "📅 Дата замены": "last_change",
        "⏱️ Срок службы": "lifetime_days"
    }
    
    if message.text not in field_mapping:
        await message.answer(
            "❌ <b>Пожалуйста, выберите поле из списка:</b>",
            reply_markup=get_edit_keyboard()
        )
        return
    
    field_key = field_mapping[message.text]
    await state.update_data(editing_field=field_key)
    
    # Получаем текущее значение
    data = await state.get_data()
    filter_data = data.get('filter_data', {})
    current_value = filter_data.get(field_key, '')
    
    if field_key == "last_change" and current_value:
        current_value = format_date_nice(datetime.strptime(str(current_value), '%Y-%m-%d'))
    
    prompt_texts = {
        "filter_type": f"💧 <b>Введите новый тип фильтра:</b>\n\nТекущее значение: <i>{current_value}</i>",
        "location": f"📍 <b>Введите новое местоположение:</b>\n\nТекущее значение: <i>{current_value}</i>",
        "last_change": f"📅 <b>Введите новую дату замены:</b>\n\nТекущее значение: <i>{current_value}</i>\nФормат: ДД.ММ.ГГГГ или ДД.ММ",
        "lifetime_days": f"⏱️ <b>Введите новый срок службы (в днях):</b>\n\nТекущее значение: <i>{current_value}</i> дней"
    }
    
    await message.answer(
        prompt_texts[field_key],
        reply_markup=get_back_keyboard(),
        parse_mode='HTML'
    )
    await state.set_state(EditFilterStates.waiting_new_value)

@dp.message(EditFilterStates.waiting_new_value)
async def process_edit_new_value(message: types.Message, state: FSMContext):
    """Обработка нового значения для поля"""
    if message.text == "🔙 Назад":
        data = await state.get_data()
        filter_data = data.get('filter_data', {})
        filter_id = data.get('selected_filter_id')
        
        await message.answer(
            f"✏️ <b>РЕДАКТИРОВАНИЕ ФИЛЬТРА #{filter_id}</b>\n\n"
            f"💧 <b>Текущие данные:</b>\n"
            f"• Тип: {filter_data['filter_type']}\n"
            f"• Местоположение: {filter_data['location']}\n"
            f"• Дата замены: {format_date_nice(datetime.strptime(str(filter_data['last_change']), '%Y-%m-%d'))}\n"
            f"• Срок службы: {filter_data['lifetime_days']} дней\n\n"
            f"<b>Выберите поле для редактирования:</b>",
            reply_markup=get_edit_keyboard(),
            parse_mode='HTML'
        )
        await state.set_state(EditFilterStates.waiting_field_selection)
        return
    
    data = await state.get_data()
    field_key = data.get('editing_field')
    filter_id = data.get('selected_filter_id')
    user_id = message.from_user.id
    filter_data = data.get('filter_data', {})
    
    try:
        if field_key == "filter_type":
            is_valid, error_msg = validate_filter_type(message.text)
            if not is_valid:
                await message.answer(f"❌ {error_msg}\n\nВведите новое значение:")
                return
            new_value = sanitize_input(message.text)
            
        elif field_key == "location":
            is_valid, error_msg = validate_location(message.text)
            if not is_valid:
                await message.answer(f"❌ {error_msg}\n\nВведите новое значение:")
                return
            new_value = sanitize_input(message.text)
            
        elif field_key == "last_change":
            new_date = enhanced_validate_date(message.text)
            new_value = new_date.strftime('%Y-%m-%d')
            
            # Пересчитываем expiry_date при изменении last_change
            lifetime_days = filter_data['lifetime_days']
            new_expiry = new_date + timedelta(days=lifetime_days)
            
            # Обновляем оба поля
            success = update_filter_in_db(
                filter_id, user_id,
                last_change=new_value,
                expiry_date=new_expiry.strftime('%Y-%m-%d')
            )
            
            if success:
                await message.answer(
                    f"✅ <b>ДАТА ЗАМЕНЫ ОБНОВЛЕНА!</b>\n\n"
                    f"Новая дата замены: {format_date_nice(new_date)}\n"
                    f"Новая дата истечения: {format_date_nice(new_expiry)}",
                    reply_markup=get_management_keyboard(),
                    parse_mode='HTML'
                )
            else:
                await message.answer(
                    "❌ <b>Ошибка при обновлении даты</b>",
                    reply_markup=get_management_keyboard(),
                    parse_mode='HTML'
                )
            
            await state.clear()
            return
            
        elif field_key == "lifetime_days":
            is_valid, error_msg, lifetime_days = validate_lifetime(message.text)
            if not is_valid:
                await message.answer(f"❌ {error_msg}\n\nВведите новое значение:")
                return
            new_value = lifetime_days
            
            # Пересчитываем expiry_date при изменении lifetime
            last_change = datetime.strptime(str(filter_data['last_change']), '%Y-%m-%d').date()
            new_expiry = last_change + timedelta(days=lifetime_days)
            
            # Обновляем оба поля
            success = update_filter_in_db(
                filter_id, user_id,
                lifetime_days=new_value,
                expiry_date=new_expiry.strftime('%Y-%m-%d')
            )
            
            if success:
                await message.answer(
                    f"✅ <b>СРОК СЛУЖБЫ ОБНОВЛЕН!</b>\n\n"
                    f"Новый срок службы: {lifetime_days} дней\n"
                    f"Новая дата истечения: {format_date_nice(new_expiry)}",
                    reply_markup=get_management_keyboard(),
                    parse_mode='HTML'
                )
            else:
                await message.answer(
                    "❌ <b>Ошибка при обновлении срока службы</b>",
                    reply_markup=get_management_keyboard(),
                    parse_mode='HTML'
                )
            
            await state.clear()
            return
        
        # Обновляем поле в БД
        success = update_filter_in_db(filter_id, user_id, **{field_key: new_value})
        
        if success:
            field_names = {
                "filter_type": "тип фильтра",
                "location": "местоположение",
                "last_change": "дата замены", 
                "lifetime_days": "срок службы"
            }
            
            await message.answer(
                f"✅ <b>{field_names[field_key].upper()} ОБНОВЛЕН!</b>\n\n"
                f"Новое значение: {new_value}",
                reply_markup=get_management_keyboard(),
                parse_mode='HTML'
            )
        else:
            await message.answer(
                "❌ <b>Ошибка при обновлении</b>",
                reply_markup=get_management_keyboard(),
                parse_mode='HTML'
            )
        
        await state.clear()
        
    except ValueError as e:
        await message.answer(f"❌ {str(e)}\n\nПожалуйста, введите корректное значение:")
    except Exception as e:
        logging.error(f"Ошибка при редактировании фильтра: {e}")
        await message.answer(
            "❌ <b>Произошла ошибка при редактировании</b>",
            reply_markup=get_management_keyboard(),
            parse_mode='HTML'
        )
        await state.clear()

@dp.message(F.text == "🗑️ Удалить фильтр")
async def cmd_delete_filter(message: types.Message, state: FSMContext):
    """Начало удаления фильтра"""
    health_monitor.record_message(message.from_user.id)
    
    filters = get_user_filters(message.from_user.id)
    if not filters:
        await message.answer(
            "❌ <b>Нет фильтров для удаления</b>\n\n"
            "Сначала добавьте фильтры через меню '✨ Добавить фильтр'",
            reply_markup=get_management_keyboard(),
            parse_mode='HTML'
        )
        return
    
    await show_filters_for_selection(message, filters, "удаления")
    await state.set_state(DeleteFilterStates.waiting_filter_selection)

@dp.message(DeleteFilterStates.waiting_filter_selection)
async def process_delete_filter_selection(message: types.Message, state: FSMContext):
    """Обработка выбора фильтра для удаления"""
    if message.text == "🔙 Назад":
        await state.clear()
        await message.answer("🔙 Возврат в меню управления", reply_markup=get_management_keyboard())
        return
    
    # Парсим ID фильтра из текста
    match = re.search(r'#(\d+)', message.text)
    if not match:
        await message.answer(
            "❌ <b>Неверный формат выбора</b>\n\n"
            "Пожалуйста, выберите фильтр из списка:",
            reply_markup=get_filters_selection_keyboard(get_user_filters(message.from_user.id), "удаления")
        )
        return
    
    filter_id = int(match.group(1))
    user_id = message.from_user.id
    
    # Проверяем существование фильтра
    filter_data = get_filter_by_id(filter_id, user_id)
    if not filter_data:
        await message.answer(
            "❌ <b>Фильтр не найден</b>\n\n"
            "Пожалуйста, выберите фильтр из списка:",
            reply_markup=get_filters_selection_keyboard(get_user_filters(user_id), "удаления")
        )
        return
    
    # Сохраняем ID фильтра в состоянии
    await state.update_data(selected_filter_id=filter_id, filter_data=filter_data)
    
    expiry_date = datetime.strptime(str(filter_data['expiry_date']), '%Y-%m-%d').date()
    days_until = (expiry_date - datetime.now().date()).days
    icon, status = get_status_icon_and_text(days_until)
    
    await message.answer(
        f"🗑️ <b>ПОДТВЕРЖДЕНИЕ УДАЛЕНИЯ</b>\n\n"
        f"{icon} <b>Фильтр #{filter_id}</b>\n"
        f"💧 Тип: {filter_data['filter_type']}\n"
        f"📍 Место: {filter_data['location']}\n"
        f"📅 Годен до: {format_date_nice(expiry_date)}\n"
        f"📊 Статус: {status} ({days_until} дней)\n\n"
        f"<b>Вы уверены, что хотите удалить этот фильтр?</b>\n"
        f"<i>Это действие нельзя отменить!</i>",
        reply_markup=get_confirmation_keyboard(),
        parse_mode='HTML'
    )
    await state.set_state(DeleteFilterStates.waiting_confirmation)

@dp.message(DeleteFilterStates.waiting_confirmation)
async def process_delete_confirmation(message: types.Message, state: FSMContext):
    """Обработка подтверждения удаления"""
    if message.text == "✅ Да, всё верно":
        data = await state.get_data()
        filter_id = data.get('selected_filter_id')
        user_id = message.from_user.id
        filter_data = data.get('filter_data', {})
        
        # Удаляем фильтр из БД
        success = delete_filter_from_db(filter_id, user_id)
        
        if success:
            await message.answer(
                f"✅ <b>ФИЛЬТР УДАЛЕН!</b>\n\n"
                f"💧 {filter_data['filter_type']}\n"
                f"📍 {filter_data['location']}\n\n"
                f"Фильтр успешно удален из базы данных.",
                reply_markup=get_management_keyboard(),
                parse_mode='HTML'
            )
        else:
            await message.answer(
                "❌ <b>Ошибка при удалении фильтра</b>",
                reply_markup=get_management_keyboard(),
                parse_mode='HTML'
            )
        
        await state.clear()
        
    elif message.text == "❌ Нет, изменить":
        await state.clear()
        await message.answer(
            "❌ <b>Удаление отменено</b>",
            reply_markup=get_management_keyboard(),
            parse_mode='HTML'
        )
    else:
        await message.answer(
            "Пожалуйста, подтвердите удаление:",
            reply_markup=get_confirmation_keyboard()
        )

@dp.message(F.text == "📊 Онлайн Excel")
async def cmd_online_excel(message: types.Message):
    """Экспорт в Excel файл"""
    health_monitor.record_message(message.from_user.id)
    
    user_id = message.from_user.id
    filters = get_user_filters(user_id)
    
    if not filters:
        await message.answer(
            "❌ <b>Нет данных для экспорта</b>\n\n"
            "Сначала добавьте фильтры через меню '✨ Добавить фильтр'",
            reply_markup=get_management_keyboard(),
            parse_mode='HTML'
        )
        return
    
    try:
        await message.answer("🔄 Создаю Excel файл...")
        
        excel_file = export_to_excel(user_id)
        
        # Отправляем файл пользователю
        await message.answer_document(
            types.BufferedInputFile(
                excel_file.getvalue(),
                filename=f"фильтры_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"
            ),
            caption="✅ <b>Ваши фильтры экспортированы в Excel</b>\n\n"
                   f"📊 Всего фильтров: {len(filters)}\n"
                   f"📅 Дата экспорта: {datetime.now().strftime('%d.%m.%Y %H:%M')}\n\n"
                   f"💡 <i>Файл содержит все данные о ваших фильтрах со статусами</i>",
            parse_mode='HTML'
        )
        
    except Exception as e:
        logging.error(f"Ошибка при создании Excel файла: {e}")
        await message.answer(
            "❌ <b>Ошибка при создании Excel файла</b>\n\n"
            "Пожалуйста, попробуйте позже.",
            reply_markup=get_management_keyboard(),
            parse_mode='HTML'
        )

@dp.message(F.text == "📥 Импорт из Excel")
async def cmd_import_excel(message: types.Message, state: FSMContext):
    """Начало импорта из Excel"""
    health_monitor.record_message(message.from_user.id)
    
    await message.answer(
        "📥 <b>ИМПОРТ ИЗ EXCEL</b>\n\n"
        "Пожалуйста, отправьте Excel файл с данными фильтров.\n\n"
        "💡 <b>Формат файла:</b>\n"
        "• Столбцы: Тип фильтра, Местоположение, Дата замены, Срок службы (дни)\n"
        "• Дата в формате: ДД.ММ.ГГГГ\n"
        "• Первая строка - заголовки\n\n"
        "📎 <i>Вы можете сначала скачать шаблон через меню '📋 Шаблон Excel'</i>",
        reply_markup=get_cancel_keyboard(),
        parse_mode='HTML'
    )
    await state.set_state(ImportExportStates.waiting_excel_file)

@dp.message(ImportExportStates.waiting_excel_file, F.document)
async def process_excel_import(message: types.Message, state: FSMContext):
    """Обработка загруженного Excel файла"""
    try:
        if not message.document:
            await message.answer(
                "❌ Пожалуйста, отправьте Excel файл",
                reply_markup=get_cancel_keyboard()
            )
            return
        
        # Проверяем расширение файла
        file_name = message.document.file_name.lower()
        if not file_name.endswith(('.xlsx', '.xls')):
            await message.answer(
                "❌ <b>Неверный формат файла</b>\n\n"
                "Пожалуйста, отправьте файл в формате Excel (.xlsx или .xls)",
                reply_markup=get_import_export_keyboard(),
                parse_mode='HTML'
            )
            await state.clear()
            return
        
        await message.answer("🔄 Обрабатываю Excel файл...")
        
        # Скачиваем файл
        file = await bot.get_file(message.document.file_id)
        file_path = file.file_path
        
        # Скачиваем и читаем файл
        downloaded_file = await bot.download_file(file_path)
        
        # Читаем Excel
        df = pd.read_excel(downloaded_file.getvalue())
        
        # Проверяем необходимые колонки
        required_columns = ['Тип фильтра', 'Местоположение', 'Дата замены', 'Срок службы (дни)']
        missing_columns = [col for col in required_columns if col not in df.columns]
        
        if missing_columns:
            await message.answer(
                f"❌ <b>В файле отсутствуют необходимые колонки:</b>\n"
                f"{', '.join(missing_columns)}\n\n"
                f"💡 Скачайте шаблон через меню '📋 Шаблон Excel'",
                reply_markup=get_import_export_keyboard(),
                parse_mode='HTML'
            )
            await state.clear()
            return
        
        # Обрабатываем данные
        imported_count = 0
        errors = []
        user_id = message.from_user.id
        
        for index, row in df.iterrows():
            try:
                # Пропускаем пустые строки
                if pd.isna(row['Тип фильтра']) or pd.isna(row['Местоположение']):
                    continue
                
                # Валидация типа фильтра
                filter_type = str(row['Тип фильтра']).strip()
                is_valid_type, error_msg = validate_filter_type(filter_type)
                if not is_valid_type:
                    errors.append(f"Строка {index+2}: {error_msg}")
                    continue
                
                # Валидация местоположения
                location = str(row['Местоположение']).strip()
                is_valid_loc, error_msg = validate_location(location)
                if not is_valid_loc:
                    errors.append(f"Строка {index+2}: {error_msg}")
                    continue
                
                # Валидация даты
                date_str = str(row['Дата замены']).strip() if not pd.isna(row['Дата замены']) else ""
                if not date_str:
                    errors.append(f"Строка {index+2}: Отсутствует дата замены")
                    continue
                
                try:
                    change_date = enhanced_validate_date(date_str)
                except ValueError as e:
                    errors.append(f"Строка {index+2}: {str(e)}")
                    continue
                
                # Валидация срока службы
                lifetime = row['Срок службы (дни)']
                if pd.isna(lifetime):
                    errors.append(f"Строка {index+2}: Отсутствует срок службы")
                    continue
                
                is_valid_lt, error_msg, lifetime_days = validate_lifetime(str(lifetime))
                if not is_valid_lt:
                    errors.append(f"Строка {index+2}: {error_msg}")
                    continue
                
                # Расчет даты истечения
                expiry_date = change_date + timedelta(days=lifetime_days)
                
                # Добавление в БД
                success = add_filter_to_db(
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
                    errors.append(f"Строка {index+2}: Ошибка базы данных")
                    
            except Exception as e:
                errors.append(f"Строка {index+2}: Неизвестная ошибка: {str(e)}")
                logging.error(f"Ошибка импорта строки {index+2}: {e}")
        
        # Формируем результат
        if imported_count > 0:
            message_text = f"✅ <b>ИМПОРТ УСПЕШЕН!</b>\n\nИмпортировано фильтров: {imported_count}"
        else:
            message_text = "❌ <b>Импорт не выполнен</b>"
        
        if errors:
            message_text += f"\n\nОшибки: {len(errors)}"
            if len(errors) <= 5:
                message_text += "\n" + "\n".join(errors[:5])
            else:
                message_text += f"\nПоказаны первые 5 из {len(errors)} ошибок:\n" + "\n".join(errors[:5])
        
        await message.answer(
            message_text,
            reply_markup=get_import_export_keyboard(),
            parse_mode='HTML'
        )
        
    except Exception as e:
        logging.error(f"Ошибка при импорте Excel: {e}")
        await message.answer(
            f"❌ <b>Ошибка при обработке файла:</b>\n{str(e)}",
            reply_markup=get_import_export_keyboard(),
            parse_mode='HTML'
        )
    
    await state.clear()

# ========== ОБРАБОТЧИКИ СИНХРОНИЗАЦИИ GOOGLE SHEETS ==========

@dp.message(F.text == "🔄 Синхронизировать с Google Sheets")
async def cmd_sync_now(message: types.Message):
    """Ручная синхронизация с Google Sheets"""
    health_monitor.record_message(message.from_user.id)
    
    if not google_sync.is_configured():
        await message.answer(
            "❌ <b>Синхронизация не настроена</b>\n\n"
            "Сначала настройте синхронизацию:\n"
            "1. Укажите ID таблицы Google Sheets\n"
            "2. Убедитесь, что GOOGLE_SHEETS_CREDENTIALS установлены в .env\n\n"
            "Используйте кнопку '⚙️ Настройки синхронизации'",
            reply_markup=get_sync_keyboard(),
            parse_mode='HTML'
        )
        return
    
    await message.answer("🔄 Начинаю синхронизацию...")
    
    user_id = message.from_user.id
    filters = get_user_filters(user_id)
    if not filters:
        await message.answer(
            "❌ Нет данных для синхронизации",
            reply_markup=get_sync_keyboard()
        )
        return
    
    success, result_message = safe_sync_to_sheets(user_id, filters)
    
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
            f"{result_message}",
            reply_markup=get_sync_keyboard(),
            parse_mode='HTML'
        )

@dp.message(F.text == "⚙️ Настройки синхронизации")
async def cmd_sync_settings(message: types.Message):
    """Настройки синхронизации"""
    health_monitor.record_message(message.from_user.id)
    
    auto_sync_status = "🟢 ВКЛ" if google_sync.auto_sync else "🔴 ВЫКЛ"
    config_status = "🟢 Настроена" if google_sync.is_configured() else "🔴 Не настроена"
    
    await message.answer(
        f"⚙️ <b>НАСТРОЙКИ СИНХРОНИЗАЦИИ</b>\n\n"
        f"📊 Статус: {config_status}\n"
        f"🔄 Автосинхронизация: {auto_sync_status}\n"
        f"📎 ID таблицы: {google_sync.sheet_id or 'Не указан'}\n\n"
        f"<b>Доступные действия:</b>",
        reply_markup=get_sync_settings_keyboard(),
        parse_mode='HTML'
    )

@dp.message(F.text == "📊 Статус синхронизации")
async def cmd_sync_status(message: types.Message):
    """Статус синхронизации"""
    health_monitor.record_message(message.from_user.id)
    
    # Проверяем настройки
    has_credentials = bool(config.GOOGLE_SHEETS_CREDENTIALS)
    has_sheet_id = bool(google_sync.sheet_id)
    is_configured = google_sync.is_configured()
    
    # Получаем статистику синхронизации
    auto_sync_status = "🟢 ВКЛ" if google_sync.auto_sync else "🔴 ВЫКЛ"
    last_sync = google_sync.last_sync_time.get(message.from_user.id)
    last_sync_text = format_date_nice(last_sync) + " " + last_sync.strftime("%H:%M:%S") if last_sync else "Никогда"
    
    status_emoji = "🟢" if is_configured else "🔴"
    config_status = "Настроена" if is_configured else "Не настроена"
    
    status_text = f"""
{status_emoji} <b>СТАТУС СИНХРОНИЗАЦИИ</b>

<b>Конфигурация:</b>
• Учетные данные: {'🟢 Установлены' if has_credentials else '🔴 Отсутствуют'}
• ID таблицы: {'🟢 Указан' if has_sheet_id else '🔴 Не указан'}
• Общий статус: {config_status}

<b>Статус синхронизации:</b>
• Автосинхронизация: {auto_sync_status}
• Интервал синхронизации: 5 секунд
• Последняя синхронизация: {last_sync_text}
• Всего синхронизаций: {health_monitor.sync_operations}

<b>Действия:</b>
"""
    
    if not has_credentials:
        status_text += "\n⚠️ <i>GOOGLE_SHEETS_CREDENTIALS не установлены в .env файле</i>"
    
    if not has_sheet_id:
        status_text += "\n⚠️ <i>ID таблицы не указан</i>"
    
    if google_sync.auto_sync:
        status_text += f"\n\n🔄 <b>Автосинхронизация активна</b>\nДанные обновляются каждые 5 секунд"
    
    await message.answer(status_text, parse_mode='HTML', reply_markup=get_sync_keyboard())

@dp.message(F.text == "📝 Указать ID таблицы")
async def cmd_set_sheet_id(message: types.Message, state: FSMContext):
    """Установка ID таблицы Google Sheets"""
    if not config.GOOGLE_SHEETS_CREDENTIALS:
        await message.answer(
            "❌ <b>Сначала настройте учетные данные</b>\n\n"
            "Добавьте в .env файл:\n"
            "<code>GOOGLE_SHEETS_CREDENTIALS=ваш_json_credentials</code>\n\n"
            "Получить credentials можно в Google Cloud Console.",
            parse_mode='HTML',
            reply_markup=get_sync_settings_keyboard()
        )
        return
    
    await message.answer(
        "📝 <b>УКАЖИТЕ ID ТАБЛИЦЫ GOOGLE SHEETS</b>\n\n"
        "ID можно найти в URL таблицы:\n"
        "https://docs.google.com/spreadsheets/d/<b>ЭТО_ID_ТАБЛИЦЫ</b>/edit\n\n"
        "Пример: <code>1BxiMVs0XRA5nFMdKvBdBZjgmUUqptlbs74OgvE2upms</code>\n\n"
        "Отправьте ID таблицы:",
        reply_markup=get_cancel_keyboard(),
        parse_mode='HTML'
    )
    await state.set_state(GoogleSheetsStates.waiting_sheet_id)

@dp.message(GoogleSheetsStates.waiting_sheet_id)
async def process_sheet_id(message: types.Message, state: FSMContext):
    """Обработка ID таблицы"""
    if message.text == "❌ Отмена":
        await state.clear()
        await cmd_sync_settings(message)
        return
    
    sheet_id = message.text.strip()
    
    # Простая валидация ID таблицы
    if len(sheet_id) < 10 or " " in sheet_id:
        await message.answer(
            "❌ <b>Неверный формат ID таблицы</b>\n\n"
            "ID таблицы должен быть длинной строкой без пробелов.\n"
            "Пример: <code>1BxiMVs0XRA5nFMdKvBdBZjgmUUqptlbs74OgvE2upms</code>\n\n"
            "Попробуйте еще раз:",
            reply_markup=get_cancel_keyboard(),
            parse_mode='HTML'
        )
        return
    
    # Сохраняем ID таблицы
    google_sync.sheet_id = sheet_id
    google_sync.save_settings()
    
    # Пробуем протестировать подключение
    await message.answer("🔗 Проверяю подключение к таблице...")
    
    try:
        # Инициализируем credentials
        if google_sync.initialize_credentials():
            # Пробуем создать тестовый лист
            import gspread
            gc = gspread.authorize(google_sync.credentials)
            sheet = gc.open_by_key(sheet_id)
            
            # Создаем тестовый лист для проверки
            test_sheet_name = f"Test_Connection_{message.from_user.id}"
            try:
                worksheet = sheet.worksheet(test_sheet_name)
                worksheet.clear()
            except gspread.exceptions.WorksheetNotFound:
                worksheet = sheet.add_worksheet(title=test_sheet_name, rows=10, cols=5)
            
            # Записыем тестовые данные
            worksheet.update('A1', [['Тест подключения', 'Успешно!']])
            worksheet.delete()
            
            await message.answer(
                f"✅ <b>ПОДКЛЮЧЕНИЕ УСПЕШНО!</b>\n\n"
                f"ID таблицы сохранен: <code>{sheet_id}</code>\n"
                f"Теперь вы можете использовать синхронизацию.",
                reply_markup=get_sync_settings_keyboard(),
                parse_mode='HTML'
            )
        else:
            await message.answer(
                "❌ <b>Ошибка инициализации Google API</b>\n\n"
                "Проверьте правильность GOOGLE_SHEETS_CREDENTIALS в .env файле.",
                reply_markup=get_sync_settings_keyboard(),
                parse_mode='HTML'
            )
            
    except Exception as e:
        error_msg = str(e)
        if "Unable to discover service" in error_msg:
            error_msg = "Неверные учетные данные Google Sheets"
        elif "not found" in error_msg.lower():
            error_msg = "Таблица с указанным ID не найдена или нет доступа"
        
        await message.answer(
            f"❌ <b>ОШИБКА ПОДКЛЮЧЕНИЯ</b>\n\n"
            f"{error_msg}\n\n"
            f"Проверьте:\n"
            f"1. Правильность ID таблицы\n"
            f"2. Доступ к таблице\n"
            f"3. Учетные данные в .env",
            reply_markup=get_sync_settings_keyboard(),
            parse_mode='HTML'
        )
    
    await state.clear()

@dp.message(F.text == "🔄 Автосинхронизация ВКЛ")
async def cmd_auto_sync_on(message: types.Message):
    """Включение автосинхронизации"""
    if not google_sync.is_configured():
        await message.answer(
            "❌ <b>Сначала настройте синхронизацию</b>\n\n"
            "Укажите ID таблицы и убедитесь в работоспособности подключения.",
            reply_markup=get_sync_settings_keyboard(),
            parse_mode='HTML'
        )
        return
    
    google_sync.auto_sync = True
    google_sync.save_settings()
    
    # Немедленная синхронизация при включении
    user_id = message.from_user.id
    filters = get_user_filters(user_id)
    if filters:
        success, result_message = google_sync.sync_to_sheets(user_id, filters)
        if success:
            sync_status = f"\n\n✅ Немедленная синхронизация: {result_message}"
        else:
            sync_status = f"\n\n⚠️ Немедленная синхронизация не удалась: {result_message}"
    else:
        sync_status = "\n\nℹ️ Нет данных для немедленной синхронизации"
    
    await message.answer(
        f"🔄 <b>АВТОСИНХРОНИЗАЦИЯ ВКЛЮЧЕНА!</b>\n\n"
        f"📊 Теперь ваши данные будут автоматически синхронизироваться с Google Sheets.\n"
        f"⏱️ <b>Интервал синхронизации:</b> 5 секунд\n"
        f"📈 <b>Статус:</b> Активный мониторинг изменений"
        f"{sync_status}",
        reply_markup=get_sync_settings_keyboard(),
        parse_mode='HTML'
    )
    
    # Логируем включение автосинхронизации
    logging.info(f"✅ Пользователь {user_id} включил автосинхронизацию")

@dp.message(F.text == "⏸️ Автосинхронизация ВЫКЛ")
async def cmd_auto_sync_off(message: types.Message):
    """Выключение автосинхронизации"""
    google_sync.auto_sync = False
    google_sync.save_settings()
    
    await message.answer(
        "⏸️ <b>АВТОСИНХРОНИЗАЦИЯ ВЫКЛЮЧЕНА</b>\n\n"
        "Автоматическая синхронизация отключена.\n"
        "Вы можете синхронизировать данные вручную через меню синхронизации.",
        reply_markup=get_sync_settings_keyboard(),
        parse_mode='HTML'
    )
    
    # Логируем выключение автосинхронизации
    logging.info(f"⏸️ Пользователь {message.from_user.id} выключил автосинхронизацию")

@dp.message(F.text == "🗑️ Отключить синхронизацию")
async def cmd_disable_sync(message: types.Message):
    """Полное отключение синхронизации"""
    google_sync.sheet_id = None
    google_sync.auto_sync = False
    google_sync.save_settings()
    
    await message.answer(
        "🗑️ <b>СИНХРОНИЗАЦИЯ ОТКЛЮЧЕНА</b>\n\n"
        "Все настройки синхронизации сброшены.\n"
        "Вы можете настроить заново в любое время.",
        reply_markup=get_sync_settings_keyboard(),
        parse_mode='HTML'
    )

# ========== ИСПРАВЛЕННЫЕ ОБРАБОТЧИКИ ДОБАВЛЕНИЯ ФИЛЬТРА ==========

@dp.message(F.text == "✨ Добавить фильтр")
async def cmd_add_filter(message: types.Message, state: FSMContext):
    """Начало процесса добавления фильтра"""
    health_monitor.record_message(message.from_user.id)
    
    # Очищаем состояние перед началом нового процесса
    await state.clear()
    
    await message.answer(
        "💧 <b>ДОБАВЛЕНИЕ НОВОГО ФИЛЬТРА</b>\n\n"
        "Выберите тип фильтра:",
        reply_markup=get_filter_type_keyboard(),
        parse_mode='HTML'
    )
    await state.set_state(FilterStates.waiting_filter_type)

@dp.message(FilterStates.waiting_filter_type)
async def process_filter_type(message: types.Message, state: FSMContext):
    """Обработка типа фильтра"""
    if message.text == "🔙 Назад":
        await state.clear()
        await message.answer("🔙 Возврат в главное меню", reply_markup=get_main_keyboard())
        return
    
    filter_type = sanitize_input(message.text)
    
    # Очищаем состояние перед сохранением новых данных
    await state.clear()
    await state.update_data(filter_type=filter_type)
    
    await message.answer(
        f"💧 <b>Тип фильтра:</b> {filter_type}\n\n"
        f"📍 <b>Теперь укажите местоположение фильтра:</b>\n"
        f"Например: Кухня, Ванная, Офис и т.д.",
        reply_markup=get_back_keyboard(),
        parse_mode='HTML'
    )
    await state.set_state(FilterStates.waiting_location)

@dp.message(FilterStates.waiting_location)
async def process_location(message: types.Message, state: FSMContext):
    """Обработка местоположения"""
    if message.text == "🔙 Назад":
        await state.set_state(FilterStates.waiting_filter_type)
        await message.answer(
            "💧 Выберите тип фильтра:",
            reply_markup=get_filter_type_keyboard(),
            parse_mode='HTML'
        )
        return
    
    location = sanitize_input(message.text)
    
    # Валидация местоположения
    is_valid, error_msg = validate_location(location)
    if not is_valid:
        await message.answer(
            f"❌ {error_msg}\n\n"
            f"📍 <b>Пожалуйста, укажите местоположение еще раз:</b>",
            reply_markup=get_back_keyboard(),
            parse_mode='HTML'
        )
        return
    
    # Сохраняем местоположение в состоянии
    await state.update_data(location=location)
    
    # Получаем текущие данные для отладки
    data = await state.get_data()
    logging.info(f"Данные состояния после location: {data}")
    
    await message.answer(
        f"📍 <b>Местоположение:</b> {location}\n\n"
        f"📅 <b>Теперь укажите дату последней замены фильтра:</b>\n"
        f"Формат: ДД.ММ.ГГГГ или ДД.ММ (текущий год)\n"
        f"Например: 15.12.2023 или 15.12",
        reply_markup=get_back_keyboard(),
        parse_mode='HTML'
    )
    await state.set_state(FilterStates.waiting_change_date)

@dp.message(FilterStates.waiting_change_date)
async def process_change_date(message: types.Message, state: FSMContext):
    """Обработка даты замены с улучшенной валидацией"""
    try:
        change_date = enhanced_validate_date(message.text)
        
        # Сохраняем дату в состоянии
        await state.update_data(change_date=change_date.strftime('%Y-%m-%d'))
        
        # Получаем данные из состояния для отладки
        data = await state.get_data()
        logging.info(f"Данные состояния после change_date: {data}")
        
        filter_type = data.get('filter_type', '').lower()
        
        # Определяем рекомендуемый срок службы
        recommended_lifetime = DEFAULT_LIFETIMES.get(filter_type, 180)
        
        await message.answer(
            f"📅 <b>Дата замены принята:</b> {format_date_nice(change_date)}\n\n"
            f"⏱️ <b>Теперь укажите срок службы фильтра в днях</b>\n\n"
            f"💡 <i>Рекомендуемый срок для {filter_type}: {recommended_lifetime} дней</i>\n"
            f"Или введите свое значение:",
            reply_markup=get_recommended_lifetime_keyboard(recommended_lifetime),
            parse_mode='HTML'
        )
        
        await state.set_state(FilterStates.waiting_lifetime)
        
    except ValueError as e:
        await message.answer(
            f"❌ <b>Ошибка в дате:</b> {str(e)}\n\n"
            f"📅 <b>Пожалуйста, введите дату еще раз:</b>",
            reply_markup=get_back_keyboard(),
            parse_mode='HTML'
        )
    except Exception as e:
        logging.error(f"Error processing change date: {e}")
        await message.answer(
            "❌ Произошла непредвиденная ошибка. Попробуйте еще раз.",
            reply_markup=get_back_keyboard()
        )

@dp.message(FilterStates.waiting_lifetime)
async def process_lifetime(message: types.Message, state: FSMContext):
    """Обработка срока службы"""
    if message.text == "🔙 Назад":
        await state.set_state(FilterStates.waiting_change_date)
        await message.answer(
            "📅 <b>Укажите дату последней замены фильтра:</b>",
            reply_markup=get_back_keyboard(),
            parse_mode='HTML'
        )
        return
    
    # Проверяем, не выбрана ли рекомендуемая кнопка
    if "Использовать рекомендуемый" in message.text:
        # Извлекаем число из текста кнопки
        import re
        match = re.search(r'\((\d+) дней\)', message.text)
        if match:
            lifetime_days = int(match.group(1))
        else:
            lifetime_days = 180  # значение по умолчанию
    else:
        # Валидация введенного срока службы
        is_valid, error_msg, lifetime_days = validate_lifetime(message.text)
        if not is_valid:
            await message.answer(
                f"❌ {error_msg}\n\n"
                f"⏱️ <b>Пожалуйста, укажите срок службы еще раз:</b>",
                reply_markup=get_back_keyboard(),
                parse_mode='HTML'
            )
            return
    
    # Сохраняем срок службы в состоянии
    await state.update_data(lifetime_days=lifetime_days)
    
    # Получаем ВСЕ данные из состояния для отладки
    data = await state.get_data()
    logging.info(f"Все данные состояния перед подтверждением: {data}")
    
    filter_type = data.get('filter_type', 'Не указан')
    location = data.get('location', 'Не указано')
    change_date_str = data.get('change_date')
    lifetime_days = data.get('lifetime_days')
    
    if not all([filter_type, location, change_date_str, lifetime_days]):
        await message.answer(
            "❌ <b>Ошибка: не все данные заполнены</b>\n\n"
            "Пожалуйста, начните процесс добавления заново.",
            reply_markup=get_main_keyboard(),
            parse_mode='HTML'
        )
        await state.clear()
        return
    
    # Рассчитываем дату истечения
    try:
        change_date = datetime.strptime(change_date_str, '%Y-%m-%d').date()
        expiry_date = change_date + timedelta(days=lifetime_days)
        
        # Сохраняем дату истечения
        await state.update_data(expiry_date=expiry_date.strftime('%Y-%m-%d'))
        
        # Получаем финальные данные для подтверждения
        final_data = await state.get_data()
        logging.info(f"Финальные данные для подтверждения: {final_data}")
        
        # Показываем подтверждение с ТЕКУЩИМИ данными
        await message.answer(
            f"✅ <b>ПОДТВЕРЖДЕНИЕ ДАННЫХ</b>\n\n"
            f"💧 <b>Тип фильтра:</b> {filter_type}\n"
            f"📍 <b>Местоположение:</b> {location}\n"
            f"📅 <b>Дата замены:</b> {format_date_nice(change_date)}\n"
            f"⏱️ <b>Срок службы:</b> {lifetime_days} дней\n"
            f"⏰ <b>Годен до:</b> {format_date_nice(expiry_date)}\n\n"
            f"<b>Всё верно?</b>",
            reply_markup=get_confirmation_keyboard(),
            parse_mode='HTML'
        )
        await state.set_state(FilterStates.waiting_confirmation)
        
    except Exception as e:
        logging.error(f"Ошибка расчета даты истечения: {e}")
        await message.answer(
            "❌ Ошибка при обработке данных. Попробуйте еще раз.",
            reply_markup=get_main_keyboard(),
            parse_mode='HTML'
        )
        await state.clear()

@dp.message(FilterStates.waiting_confirmation)
async def process_confirmation(message: types.Message, state: FSMContext):
    """Обработка подтверждения с принудительным обновлением кэша"""
    if message.text == "✅ Да, всё верно":
        # Получаем данные из состояния
        data = await state.get_data()
        logging.info(f"Данные для сохранения в БД: {data}")
        
        user_id = message.from_user.id
        filter_type = data.get('filter_type')
        location = data.get('location')
        change_date = data.get('change_date')
        expiry_date = data.get('expiry_date')
        lifetime_days = data.get('lifetime_days')
        
        # Проверяем, что все данные присутствуют
        if not all([filter_type, location, change_date, expiry_date, lifetime_days]):
            await message.answer(
                "❌ <b>Ошибка: не все данные заполнены</b>\n\n"
                "Пожалуйста, начните процесс добавления заново.",
                reply_markup=get_main_keyboard(),
                parse_mode='HTML'
            )
            await state.clear()
            return
        
        # Добавляем фильтр в БД
        success = add_filter_to_db(
            user_id=user_id,
            filter_type=filter_type,
            location=location,
            last_change=change_date,
            expiry_date=expiry_date,
            lifetime_days=lifetime_days
        )
        
        if success:
            # ПРИНУДИТЕЛЬНО обновляем данные для показа
            cache_manager.invalidate_user_cache(user_id)
            
            # Получаем СВЕЖИЕ данные для отображения
            fresh_filters = get_user_filters_db(user_id)
            expiry_date_obj = datetime.strptime(expiry_date, '%Y-%m-%d').date()
            
            await message.answer(
                f"✅ <b>ФИЛЬТР УСПЕШНО ДОБАВЛЕН!</b>\n\n"
                f"💧 {filter_type}\n"
                f"📍 {location}\n"
                f"📅 Следующая замена: {format_date_nice(expiry_date_obj)}\n\n"
                f"📊 Теперь у вас {len(fresh_filters)} фильтр(ов)",
                reply_markup=get_main_keyboard(),
                parse_mode='HTML'
            )
        else:
            await message.answer(
                "❌ <b>Ошибка при добавлении фильтра</b>\n\n"
                "Пожалуйста, попробуйте еще раз.",
                reply_markup=get_main_keyboard(),
                parse_mode='HTML'
            )
        
        # Всегда очищаем состояние после завершения
        await state.clear()
        
    elif message.text == "❌ Нет, изменить":
        await state.clear()
        await state.set_state(FilterStates.waiting_filter_type)
        await message.answer(
            "💧 <b>Начнем заново. Выберите тип фильтра:</b>",
            reply_markup=get_filter_type_keyboard(),
            parse_mode='HTML'
        )
    else:
        await message.answer(
            "Пожалуйста, выберите вариант:",
            reply_markup=get_confirmation_keyboard()
        )

# ========== ДОПОЛНИТЕЛЬНЫЕ ОБРАБОТЧИКИ КОМАНД ==========
@dp.message(Command("help"))
async def cmd_help(message: types.Message):
    """Расширенная справка"""
    help_text = """
🤖 <b>ПОМОЩЬ ПО КОМАНДАМ</b>

<b>Основные команды:</b>
/start - Запуск бота
/help - Эта справка
/cancel - Отмена текущей операции
/stats - Ваша персональная статистика

<b>Управление фильтрами:</b>
📋 Мои фильтры - Просмотр всех фильтров
✨ Добавить фильтр - Добавить новый фильтр
⚙️ Управление фильтрами - Редактирование и удаление

<b>Дополнительные функции:</b>
📊 Статистика - Статистика и аналитика
📤 Импорт/Экспорт - Работа с Excel
☁️ Синхронизация - Google Sheets
⏰ Настройка напоминаний - Установка времени уведомлений

💡 <i>Используйте кнопки меню для навигации</i>
    """
    await message.answer(help_text, parse_mode='HTML')

@dp.message(Command("stats"))
async def cmd_personal_stats(message: types.Message):
    """Персональная статистика пользователя"""
    user_id = message.from_user.id
    filters = get_user_filters(user_id)
    stats = cache_manager.get_user_stats(user_id)
    cache_stats = cache_manager.get_cache_stats(user_id)
    
    if not filters:
        await message.answer("📊 У вас пока нет статистики - добавьте первый фильтр!")
        return
    
    # Создаем детальную статистику по типам фильтров
    type_stats = {}
    for f in filters:
        filter_type = f['filter_type']
        if filter_type not in type_stats:
            type_stats[filter_type] = 0
        type_stats[filter_type] += 1
    
    type_stats_text = "\n".join([f"• {k}: {v}" for k, v in type_stats.items()])
    
    stats_text = f"""
📊 <b>ВАША ПЕРСОНАЛЬНАЯ СТАТИСТИКА</b>

💧 <b>Общее:</b>
• Всего фильтров: {stats['total']}
• 🟢 В норме: {stats['normal']}
• 🟡 Скоро истекают: {stats['expiring_soon']}
• 🔴 Просрочено: {stats['expired']}

📈 <b>Состояние системы:</b>
• Общее здоровье: {create_progress_bar(stats['health_percentage'])}
• Средний срок до замены: {stats['avg_days_until_expiry']:.1f} дней
• Эффективность кэша: {cache_stats['hit_rate']:.1f}%

📋 <b>По типам фильтров:</b>
{type_stats_text}

💫 <i>Статистика обновляется в реальном времени</i>
    """
    
    await message.answer(stats_text, parse_mode='HTML')

@dp.message(F.text == "⏰ Настройка напоминаний")
async def cmd_reminder_settings(message: types.Message):
    """Настройка напоминаний"""
    current_time = smart_reminders.get_user_reminder_time(message.from_user.id)
    
    await message.answer(
        f"⏰ <b>НАСТРОЙКА НАПОМИНАНИЙ</b>\n\n"
        f"Текущее время напоминаний: <b>{current_time}</b>\n"
        f"Выберите удобное время для получения уведомлений:",
        reply_markup=get_reminder_settings_keyboard(),
        parse_mode='HTML'
    )

@dp.message(F.text.regexp(r"🕘 09:00|🕙 10:00|🕚 11:00|🕛 12:00|🕐 13:00|🕑 14:00|🕒 15:00|🕓 16:00"))
async def process_reminder_time(message: types.Message):
    """Обработка выбора времени напоминаний"""
    time_str = message.text.split()[-1]  # Извлекаем время
    user_id = message.from_user.id
    
    smart_reminders.set_user_reminder_time(user_id, time_str)
    
    await message.answer(
        f"✅ <b>Время напоминаний установлено!</b>\n\n"
        f"Теперь вы будете получать уведомления в <b>{time_str}</b>\n\n"
        f"Следующее напоминание придет согласно установленному расписанию.",
        reply_markup=get_main_keyboard(),
        parse_mode='HTML'
    )

# ========== ОСНОВНЫЕ ОБРАБОТЧИКИ ==========
@dp.message(Command("start"))
async def cmd_start(message: types.Message, state: FSMContext):
    """Обработчик команды /start с rate limiting"""
    health_monitor.record_message(message.from_user.id)
    
    # Всегда очищаем состояние при старте
    await state.clear()
    
    # Проверяем статус автосинхронизации
    sync_status = ""
    if google_sync.auto_sync and google_sync.is_configured():
        sync_status = "\n\n🔄 <b>Автосинхронизация активна</b>\nДанные обновляются каждые 5 секунд"
    
    await message.answer(
        "🏭 <b>Завод «Контакт»</b>\n"
        "🌟 <b>Фильтр-Трекер</b> 🤖\n\n"
        "💧 <i>Умный помощник для своевременной замены фильтров</i>\n\n"
        "📦 <b>Основные возможности:</b>\n"
        "• 📋 Просмотр всех ваших фильтров\n"
        "• ✨ Добавление новых фильтров\n"
        "• ⏳ Контроль сроков замены\n"
        "• ⚙️ Полное управление базой\n"
        "• 📊 Детальная статистика\n"
        "• 📤 Импорт/Экспорт Excel\n"
        "• ☁️ Синхронизация с Google Sheets\n"
        "• 🔔 Автоматические напоминания\n"
        "• ⏰ Настройка времени уведомлений\n"
        "• ⚡ <b>Синхронизация в реальном времени (5 сек)</b>"
        f"{sync_status}\n\n"
        "🏭 <i>Официальная система учета фильтров завода «Контакт»</i>",
        reply_markup=get_main_keyboard(),
        parse_mode='HTML'
    )

@dp.message(Command("cancel"))
async def cmd_cancel(message: types.Message, state: FSMContext):
    """Сброс текущего состояния"""
    current_state = await state.get_state()
    if current_state is None:
        await message.answer("ℹ️ Нечего отменять", reply_markup=get_main_keyboard())
        return
    
    # Полностью очищаем состояние
    await state.clear()
    await message.answer(
        "❌ <b>ОПЕРАЦИЯ ОТМЕНЕНА</b>\n\n"
        "Все введенные данные удалены.",
        reply_markup=get_main_keyboard(),
        parse_mode='HTML'
    )

@dp.message(F.text == "🔙 Назад")
async def cmd_back(message: types.Message, state: FSMContext):
    """Универсальный обработчик кнопки Назад"""
    current_state = await state.get_state()
    
    # Определяем куда вернуться в зависимости от текущего состояния
    if current_state and "EditFilterStates" in current_state:
        await state.clear()
        await message.answer("🔙 Возврат в меню управления", reply_markup=get_management_keyboard())
    
    elif current_state and "DeleteFilterStates" in current_state:
        await state.clear()
        await message.answer("🔙 Возврат в меню управления", reply_markup=get_management_keyboard())
    
    elif current_state and "GoogleSheetsStates" in current_state:
        await state.clear()
        await cmd_google_sheets(message)
    
    elif current_state and "ImportExportStates" in current_state:
        await state.clear()
        await cmd_import_export(message)
    
    elif current_state and "FilterStates" in current_state:
        await state.clear()
        await message.answer("🔙 Возврат в главное меню", reply_markup=get_main_keyboard())
    
    elif current_state:
        # Для других состояний - очищаем и возвращаем в главное меню
        await state.clear()
        await message.answer("🔙 Возврат в главное меню", reply_markup=get_main_keyboard())
    
    else:
        # Если нет активного состояния - просто показываем главное меню
        await message.answer("🔙 Главное меню", reply_markup=get_main_keyboard())

# ========== ОБРАБОТЧИКИ ДЛЯ НОВЫХ КНОПОК ==========

@dp.message(F.text == "⚙️ Управление фильтрами")
async def cmd_management(message: types.Message):
    """Меню управления фильтрами"""
    health_monitor.record_message(message.from_user.id)
    
    filters = get_user_filters(message.from_user.id)
    if not filters:
        await message.answer(
            "📭 <b>У вас пока нет фильтров для управления</b>\n\n"
            "Сначала добавьте фильтры через меню '✨ Добавить фильтр'",
            reply_markup=get_main_keyboard(),
            parse_mode='HTML'
        )
        return
    
    await message.answer(
        "⚙️ <b>УПРАВЛЕНИЕ ФИЛЬТРАМИ</b>\n\n"
        f"📊 У вас {len(filters)} фильтр(ов)\n\n"
        "Выберите действие:",
        reply_markup=get_management_keyboard(),
        parse_mode='HTML'
    )

@dp.message(F.text == "📊 Статистика")
async def cmd_statistics(message: types.Message):
    """Общая статистика"""
    health_monitor.record_message(message.from_user.id)
    
    user_id = message.from_user.id
    filters = get_user_filters(user_id)
    
    if not filters:
        await message.answer(
            "📭 <b>У вас пока нет статистики</b>\n\n"
            "Добавьте первый фильтр, чтобы увидеть статистику.",
            reply_markup=get_main_keyboard(),
            parse_mode='HTML'
        )
        return
    
    stats = cache_manager.get_user_stats(user_id)
    
    # Детальная статистика по статусам
    today = datetime.now().date()
    status_counts = {
        "🔴 Просрочено": 0,
        "🟡 Скоро истекают": 0,
        "🟠 Внимание": 0,
        "🟢 Норма": 0
    }
    
    for f in filters:
        expiry_date = datetime.strptime(str(f['expiry_date']), '%Y-%m-%d').date()
        days_until = (expiry_date - today).days
        
        if days_until <= 0:
            status_counts["🔴 Просрочено"] += 1
        elif days_until <= 7:
            status_counts["🟡 Скоро истекают"] += 1
        elif days_until <= 30:
            status_counts["🟠 Внимание"] += 1
        else:
            status_counts["🟢 Норма"] += 1
    
    status_text = "\n".join([f"{status}: {count}" for status, count in status_counts.items()])
    
    stats_text = f"""
📊 <b>ОБЩАЯ СТАТИСТИКА</b>

💧 <b>Основные показатели:</b>
• Всего фильтров: {stats['total']}
• 🟢 В норме: {stats['normal']}
• 🟡 Скоро истекают: {stats['expiring_soon']}
• 🔴 Просрочено: {stats['expired']}

📈 <b>Детали по статусам:</b>
{status_text}

⏱️ <b>Сроки службы:</b>
• Средний срок до замены: {stats['avg_days_until_expiry']:.1f} дней
• Общее здоровье системы: {create_progress_bar(stats['health_percentage'])}

💫 <i>Статистика обновляется в реальном времени</i>
    """
    
    await message.answer(stats_text, parse_mode='HTML')

@dp.message(F.text == "📤 Импорт/Экспорт")
async def cmd_import_export(message: types.Message):
    """Меню импорта/экспорта"""
    health_monitor.record_message(message.from_user.id)
    
    await message.answer(
        "📤 <b>ИМПОРТ/ЭКСПОРТ ДАННЫХ</b>\n\n"
        "Выберите действие для работы с данными:",
        reply_markup=get_import_export_keyboard(),
        parse_mode='HTML'
    )

@dp.message(F.text == "📤 Экспорт в Excel")
async def cmd_export_excel(message: types.Message):
    """Экспорт данных в Excel"""
    health_monitor.record_message(message.from_user.id)
    
    user_id = message.from_user.id
    filters = get_user_filters(user_id)
    
    if not filters:
        await message.answer(
            "❌ <b>Нет данных для экспорта</b>\n\n"
            "Сначала добавьте фильтры через меню '✨ Добавить фильтр'",
            reply_markup=get_import_export_keyboard(),
            parse_mode='HTML'
        )
        return
    
    try:
        await message.answer("🔄 Создаю Excel файл...")
        
        excel_file = export_to_excel(user_id)
        
        # Отправляем файл пользователю
        await message.answer_document(
            types.BufferedInputFile(
                excel_file.getvalue(),
                filename=f"фильтры_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"
            ),
            caption="✅ <b>Ваши фильтры экспортированы в Excel</b>\n\n"
                   f"📊 Всего фильтров: {len(filters)}\n"
                   f"📅 Дата экспорта: {datetime.now().strftime('%d.%m.%Y %H:%M')}",
            parse_mode='HTML'
        )
        
    except Exception as e:
        logging.error(f"Ошибка при экспорте в Excel: {e}")
        await message.answer(
            "❌ <b>Ошибка при создании Excel файла</b>\n\n"
            "Пожалуйста, попробуйте позже.",
            reply_markup=get_import_export_keyboard(),
            parse_mode='HTML'
        )

@dp.message(F.text == "📋 Шаблон Excel")
async def cmd_excel_template(message: types.Message):
    """Отправка шаблона Excel для импорта"""
    health_monitor.record_message(message.from_user.id)
    
    # Создаем простой шаблон Excel
    try:
        template_data = {
            'Тип фильтра': ['Магистральный SL10', 'Гейзер', 'Аквафор'],
            'Местоположение': ['Кухня', 'Ванная', 'Офис'],
            'Дата замены': ['15.12.2023', '20.12.2023', '25.12.2023'],
            'Срок службы (дни)': [180, 365, 365]
        }
        
        df = pd.DataFrame(template_data)
        
        output = io.BytesIO()
        with pd.ExcelWriter(output, engine='openpyxl') as writer:
            df.to_excel(writer, sheet_name='Шаблон', index=False)
            
            # Получаем workbook и worksheet для форматирования
            workbook = writer.book
            worksheet = writer.sheets['Шаблон']
            
            # Настраиваем ширину колонок
            for column in worksheet.columns:
                max_length = 0
                column_letter = column[0].column_letter
                for cell in column:
                    try:
                        if len(str(cell.value)) > max_length:
                            max_length = len(str(cell.value))
                    except:
                        pass
                adjusted_width = min(max_length + 2, 50)
                worksheet.column_dimensions[column_letter].width = adjusted_width
        
        output.seek(0)
        
        await message.answer_document(
            types.BufferedInputFile(
                output.getvalue(),
                filename="шаблон_импорта_фильтров.xlsx"
            ),
            caption="📋 <b>ШАБЛОН ДЛЯ ИМПОРТА ФИЛЬТРОВ</b>\n\n"
                   "Заполните этот шаблон своими данными и импортируйте через меню '📥 Импорт из Excel'\n\n"
                   "<b>Столбцы шаблона:</b>\n"
                   "• Тип фильтра - тип вашего фильтра\n"
                   "• Местоположение - где установлен фильтр\n"
                   "• Дата замены - дата последней замены (ДД.ММ.ГГГГ)\n"
                   "• Срок службы (дни) - срок службы в днях",
            parse_mode='HTML'
        )
        
    except Exception as e:
        logging.error(f"Ошибка при создании шаблона: {e}")
        await message.answer(
            "❌ <b>Ошибка при создании шаблона</b>\n\n"
            "Пожалуйста, попробуйте позже.",
            reply_markup=get_import_export_keyboard(),
            parse_mode='HTML'
        )

@dp.message(F.text == "☁️ Синхронизация с Google Sheets")
async def cmd_google_sheets(message: types.Message):
    """Меню синхронизации с Google Sheets"""
    health_monitor.record_message(message.from_user.id)
    
    await message.answer(
        "☁️ <b>СИНХРОНИЗАЦИЯ С GOOGLE SHEETS</b>\n\n"
        "Настройте автоматическую синхронизацию ваших данных с Google Sheets:",
        reply_markup=get_sync_keyboard(),
        parse_mode='HTML'
    )

@dp.message(Command("admin"))
async def cmd_admin(message: types.Message):
    """Админ панель"""
    if not is_admin(message.from_user.id):
        await message.answer("❌ Доступ запрещен")
        return
    
    health_status = health_monitor.get_detailed_status()
    stats = get_all_users_stats()
    
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
        f"• 🏥 Health: {health_status['health_score']:.1f}%\n"
        f"• 💰 Hit Rate кэша: {health_status['cache_hit_rate']:.1f}%\n\n"
        f"⚡ <b>Реальное время:</b>\n"
        f"• 🔄 Автосинхронизация: {'ВКЛ' if google_sync.auto_sync else 'ВЫКЛ'}\n"
        f"• 📶 Интервал синхронизации: {config.REAL_TIME_SYNC_INTERVAL} сек\n"
        f"• 💾 Операций синхронизации: {health_status['sync_operations']}\n\n"
        f"🔧 <b>Действия:</b>\n"
        f"/backup - Создать резервную копию\n"
        f"/clear_cache - Очистить кэш\n"
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

@dp.message(Command("clear_cache"))
async def cmd_clear_cache(message: types.Message):
    """Очистка кэша"""
    if not is_admin(message.from_user.id):
        return
    
    cache_manager.clear_all_cache()
    await message.answer("✅ Кэш успешно очищен")

@dp.message(F.text == "📋 Мои фильтры")
async def cmd_my_filters(message: types.Message):
    """Показать фильтры пользователя"""
    health_monitor.record_message(message.from_user.id)
    
    filters = get_user_filters(message.from_user.id)
    
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
            f"📈 {format_filter_status_with_progress(f)}\n"
        )
    
    # Добавляем инфографику
    response.append("\n" + create_expiry_infographic(filters))
    
    # Разбиваем сообщение если слишком длинное
    full_text = "\n".join(response)
    if len(full_text) > 4000:
        parts = [full_text[i:i+4000] for i in range(0, len(full_text), 4000)]
        for part in parts:
            await message.answer(part, parse_mode='HTML')
    else:
        await message.answer(full_text, parse_mode='HTML')

# ========== ЗАПУСК ПРИЛОЖЕНИЯ С УЛУЧШЕННОЙ СИНХРОНИЗАЦИЕЙ ==========
def check_dependencies():
    """Проверка необходимых зависимостей"""
    try:
        import pandas as pd
        import sqlite3
        import re
        import json
        # Проверяем основные зависимости
        logging.info("Все зависимости загружены успешно")
        return True
    except ImportError as e:
        logging.critical(f"Отсутствует зависимость: {e}")
        return False

def start_background_tasks():
    """Запуск фоновых задач в отдельных потоках"""
    # Задача напоминаний
    reminder_thread = threading.Thread(target=send_personalized_reminders, daemon=True)
    reminder_thread.start()
    
    # Задача мониторинга здоровья
    health_thread = threading.Thread(target=health_monitoring_task, daemon=True)
    health_thread.start()
    
    # ЗАПУСК УЛУЧШЕННОЙ ЗАДАЧИ СИНХРОНИЗАЦИИ
    sync_thread = threading.Thread(target=real_time_sync_task, daemon=True)
    sync_thread.start()
    
    logging.info("🚀 Фоновые задачи запущены (автосинхронизация: 5 секунд)")

async def enhanced_main():
    """Улучшенная функция запуска"""
    try:
        # Проверка зависимостей
        if not check_dependencies():
            raise ImportError("Не все зависимости установлены")
        
        # Инициализация конфигурации
        config.validate()
        
        # Настройка логирования
        setup_logging()
        
        # Инициализация базы данных
        init_db()
        check_and_update_schema()
        
        # Создание резервной копии при запуске
        if config.BACKUP_ENABLED:
            if backup_database():
                logging.info("Резервная копия при запуске создана успешно")
            else:
                logging.warning("Не удалось создать резервную копию при запуске")
        
        # Запуск фоновых задач
        start_background_tasks()
        
        # Настройка обработчика ошибок
        dp.errors.register(error_handler)
        
        # Уведомление о успешном запуске
        logging.info("🤖 Бот успешно запущен с улучшенной автосинхронизацией (5 секунд)!")
        
        # Запуск бота
        await dp.start_polling(bot)
        
    except Exception as e:
        logging.critical(f"Критическая ошибка при запуске: {e}")
        raise

if __name__ == "__main__":
    try:
        asyncio.run(enhanced_main())
    except KeyboardInterrupt:
        logging.info("Бот остановлен пользователем")
    except Exception as e:
        logging.critical(f"Фатальная ошибка: {e}")
        sys.exit(1)
