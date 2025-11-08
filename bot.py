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
import signal
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
            raise ValueError("TELEGRAM_BOT_TOKEN не установлен в переменных окружения")
        
        self.ADMIN_ID = int(os.getenv('ADMIN_ID', '5024165375'))
        self.GOOGLE_SHEETS_CREDENTIALS = os.getenv('GOOGLE_SHEETS_CREDENTIALS')
        self.GOOGLE_SHEET_ID = os.getenv('GOOGLE_SHEET_ID')
        
        # Сохраняем БД в постоянной директории
        self.DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data', 'materials.db')
        
        # Создаем директорию если не существует
        os.makedirs(os.path.dirname(self.DB_PATH), exist_ok=True)
        
        self.BACKUP_ENABLED = True
        self.BACKUP_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data', 'backups')
        os.makedirs(self.BACKUP_PATH, exist_ok=True)
        
        # Настройки rate limiting
        self.RATE_LIMIT_MAX_REQUESTS = 10
        self.RATE_LIMIT_WINDOW = 30
        
        # Настройки уведомлений
        self.REMINDER_CHECK_INTERVAL = 24 * 60 * 60  # 24 часа
        self.EARLY_REMINDER_DAYS = 7
        
        # Настройки кэширования
        self.CACHE_TTL = 300  # 5 минут
        
        # Настройки реального времени
        self.REAL_TIME_SYNC_INTERVAL = 300  # 300 секунд
        
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

# ========== ПРОВЕРКА ПРАВ ДОСТУПА ==========
def check_access(user_id: int) -> bool:
    """Проверка доступа пользователя"""
    return user_id == config.ADMIN_ID

class AdminMiddleware(BaseMiddleware):
    """Middleware для проверки прав администратора"""
    async def __call__(
        self,
        handler: Callable[[types.TelegramObject, Dict[str, Any]], Any],
        event: types.Message,
        data: Dict[str, Any]
    ) -> Any:
        user_id = event.from_user.id
        if not check_access(user_id):
            await event.answer(
                "❌ <b>ДОСТУП ЗАПРЕЩЕН</b>\n\n"
                "Этот бот доступен только для администраторов ООО «ИКС ГЕОСТРОЙ».",
                parse_mode='HTML'
            )
            return
        return await handler(event, data)

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

# ========== УЛУЧШЕННЫЙ МЕНЕДЖЕР ПОДКЛЮЧЕНИЯ К БАЗЕ ДАННЫХ ==========
@contextmanager
def get_db_connection():
    """Улучшенный контекстный менеджер для работы с БД"""
    conn = None
    try:
        conn = sqlite3.connect(config.DB_PATH, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA journal_mode = WAL")  # Улучшаем производительность
        
        yield conn
        conn.commit()
        
    except sqlite3.Error as e:
        if conn:
            conn.rollback()
        logging.error(f"Ошибка SQLite: {e}")
        # Не пробрасываем исключение дальше, чтобы не показывать пользователю
        raise
    except Exception as e:
        if conn:
            conn.rollback()
        logging.error(f"Общая ошибка БД: {e}")
        raise
    finally:
        if conn:
            conn.close()

def debug_database_connection():
    """Расширенная диагностика подключения к БД"""
    try:
        # Проверяем существование файла БД
        db_exists = os.path.exists(config.DB_PATH)
        logging.info(f"Файл БД существует: {db_exists}, путь: {config.DB_PATH}")
        
        if db_exists:
            db_size = os.path.getsize(config.DB_PATH)
            logging.info(f"Размер файла БД: {db_size} байт")
        
        # Проверяем подключение
        with get_db_connection() as conn:
            cur = conn.cursor()
            
            # Проверяем существование таблицы
            cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='materials'")
            table_exists = cur.fetchone() is not None
            logging.info(f"Таблица 'materials' существует: {table_exists}")
            
            if table_exists:
                # Проверяем структуру таблицы
                cur.execute("PRAGMA table_info(materials)")
                columns = cur.fetchall()
                logging.info(f"Колонки таблицы: {[col[1] for col in columns]}")
                
                # Проверяем количество записей
                cur.execute("SELECT COUNT(*) FROM materials")
                count = cur.fetchone()[0]
                logging.info(f"Количество записей в таблице: {count}")
        
        return True
        
    except Exception as e:
        logging.error(f"Ошибка диагностики БД: {e}")
        return False

def check_database_permissions():
    """Проверка прав доступа к файлу базы данных"""
    try:
        db_path = config.DB_PATH
        
        # Проверяем права на чтение/запись
        if os.path.exists(db_path):
            readable = os.access(db_path, os.R_OK)
            writable = os.access(db_path, os.W_OK)
            logging.info(f"Права доступа к БД: Чтение={readable}, Запись={writable}")
            
            if not readable or not writable:
                logging.error("Недостаточно прав доступа к файлу БД")
                return False
        
        # Проверяем права на директорию
        db_dir = os.path.dirname(db_path) or '.'
        readable_dir = os.access(db_dir, os.R_OK)
        writable_dir = os.access(db_dir, os.W_OK)
        logging.info(f"Права доступа к директории: Чтение={readable_dir}, Запись={writable_dir}")
        
        return readable_dir and writable_dir
        
    except Exception as e:
        logging.error(f"Ошибка проверки прав доступа: {e}")
        return False

# ========== СИНХРОННАЯ БАЗА ДАННЫХ ==========
def get_user_materials_db(user_id: int) -> List[Dict]:
    """Синхронное получение материалов пользователя из БД"""
    try:
        with get_db_connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT * FROM materials WHERE user_id = ? ORDER BY delivery_date", (user_id,))
            rows = cur.fetchall()
            health_monitor.record_db_operation()
            health_monitor.record_cache_miss()
            return [dict(row) for row in rows]
    except Exception as e:
        logging.error(f"Ошибка при получении материалов пользователя {user_id}: {e}")
        health_monitor.record_error()
        return []

def get_material_by_id(material_id: int, user_id: int) -> Optional[Dict]:
    """Синхронное получение материала по ID"""
    try:
        # Сначала проверяем кэш
        materials = get_user_materials(user_id)
        for m in materials:
            if m['id'] == material_id:
                health_monitor.record_cache_hit()
                return m
        
        # Если не найдено в кэше, ищем в БД
        with get_db_connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT * FROM materials WHERE id = ? AND user_id = ?", (material_id, user_id))
            result = cur.fetchone()
            health_monitor.record_db_operation()
            health_monitor.record_cache_miss()
            return dict(result) if result else None
    except Exception as e:
        logging.error(f"Ошибка при получении материала {material_id}: {e}")
        health_monitor.record_error()
        return None

def get_all_users_stats() -> Dict:
    """Синхронное получение статистики"""
    try:
        with get_db_connection() as conn:
            cur = conn.cursor()
            cur.execute('''SELECT COUNT(DISTINCT user_id) as total_users, 
                                  COUNT(*) as total_materials,
                                  SUM(CASE WHEN delivery_date <= date('now') THEN 1 ELSE 0 END) as delivered_materials,
                                  SUM(CASE WHEN delivery_date BETWEEN date('now') AND date('now', '+7 days') THEN 1 ELSE 0 END) as delivering_soon
                           FROM materials''')
            result = cur.fetchone()
            health_monitor.record_db_operation()
            return dict(result) if result else {'total_users': 0, 'total_materials': 0, 'delivered_materials': 0, 'delivering_soon': 0}
    except Exception as e:
        logging.error(f"Ошибка при получении статистики: {e}")
        health_monitor.record_error()
        return {'total_users': 0, 'total_materials': 0, 'delivered_materials': 0, 'delivering_soon': 0}

def add_material_to_db(user_id: int, material_type: str, location: str, order_date: str, delivery_date: str, quantity: int, cost: float) -> bool:
    """Добавление материала в БД с улучшенной обработкой ошибок"""
    try:
        logging.info(f"Попытка добавления материала для user_id: {user_id}")
        
        with get_db_connection() as conn:
            cur = conn.cursor()
            cur.execute('''INSERT INTO materials 
                          (user_id, material_type, location, order_date, delivery_date, quantity, cost) 
                          VALUES (?, ?, ?, ?, ?, ?, ?)''',
                          (user_id, material_type, location, order_date, delivery_date, quantity, cost))
            
            # Проверяем, что запись действительно добавлена
            material_id = cur.lastrowid
            logging.info(f"Материал добавлен с ID: {material_id}")
            
            health_monitor.record_db_operation()
            
            # ПРИНУДИТЕЛЬНАЯ инвалидация кэша пользователя
            cache_manager.invalidate_user_cache(user_id)
            
            # Очищаем LRU кэш полностью
            cache_manager.lru_cache.cache.clear()
            
            # Мгновенная синхронизация при добавлении
            if google_sync.auto_sync and google_sync.is_configured():
                # Получаем СВЕЖИЕ данные из БД, минуя кэш
                fresh_materials = get_user_materials_db(user_id)
                google_sync.sync_to_sheets(user_id, fresh_materials)
            
            return True
            
    except sqlite3.Error as e:
        logging.error(f"SQL ошибка при добавлении материала: {e}")
        health_monitor.record_error()
        return False
    except Exception as e:
        logging.error(f"Общая ошибка при добавлении материала: {e}")
        health_monitor.record_error()
        return False

def update_material_in_db(material_id: int, user_id: int, **kwargs) -> bool:
    """Обновление материала в БД"""
    try:
        if not kwargs:
            return False
        
        with get_db_connection() as conn:
            cur = conn.cursor()
            set_clause = ", ".join([f"{key} = ?" for key in kwargs.keys()])
            values = list(kwargs.values())
            values.extend([material_id, user_id])
            
            cur.execute(f"UPDATE materials SET {set_clause} WHERE id = ? AND user_id = ?", values)
            
            health_monitor.record_db_operation()
            
            # Инвалидируем кэш пользователя
            cache_manager.invalidate_user_cache(user_id)
            
            # Мгновенная синхронизация при обновлении
            if google_sync.auto_sync and google_sync.is_configured():
                materials = get_user_materials(user_id)
                google_sync.sync_to_sheets(user_id, materials)
            
            return cur.rowcount > 0
    except Exception as e:
        logging.error(f"Ошибка при обновлении материала {material_id}: {e}")
        health_monitor.record_error()
        return False

def delete_material_from_db(material_id: int, user_id: int) -> bool:
    """Удаление материала из БД"""
    try:
        with get_db_connection() as conn:
            cur = conn.cursor()
            cur.execute("DELETE FROM materials WHERE id = ? AND user_id = ?", (material_id, user_id))
            
            health_monitor.record_db_operation()
            
            # Инвалидируем кэш пользователя
            cache_manager.invalidate_user_cache(user_id)
            
            # Мгновенная синхронизация при удалении
            if google_sync.auto_sync and google_sync.is_configured():
                materials = get_user_materials(user_id)
                google_sync.sync_to_sheets(user_id, materials)
            
            return cur.rowcount > 0
    except Exception as e:
        logging.error(f"Ошибка при удалении материала {material_id}: {e}")
        health_monitor.record_error()
        return False

def init_db():
    """Синхронная инициализация базы данных БЕЗ ПОЛЯ SALARY"""
    try:
        with get_db_connection() as conn:
            cur = conn.cursor()
            
            # Создаем таблицу если не существует БЕЗ ПОЛЯ SALARY
            cur.execute('''
                CREATE TABLE IF NOT EXISTS materials (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER,
                    material_type TEXT,
                    location TEXT,
                    order_date DATE,
                    delivery_date DATE,
                    quantity INTEGER,
                    cost REAL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            
            # Создаем индексы
            cur.execute('''CREATE INDEX IF NOT EXISTS idx_user_id ON materials(user_id)''')
            cur.execute('''CREATE INDEX IF NOT EXISTS idx_delivery_date ON materials(delivery_date)''')
            cur.execute('''CREATE INDEX IF NOT EXISTS idx_user_delivery ON materials(user_id, delivery_date)''')
            
            logging.info("База данных успешно инициализирована (без поля salary)")
                
    except Exception as e:
        logging.error(f"Критическая ошибка инициализации БД: {e}")
        # Создаем резервную копию при критической ошибке
        if os.path.exists(config.DB_PATH):
            backup_name = f'materials_backup_critical_{datetime.now().strftime("%Y%m%d_%H%M%S")}.db'
            try:
                shutil.copy2(config.DB_PATH, backup_name)
                logging.info(f"Создана критическая резервная копия: {backup_name}")
            except Exception as backup_error:
                logging.error(f"Не удалось создать резервную копию: {backup_error}")
        raise

def check_and_update_schema():
    """Проверка и обновление схема базы данных БЕЗ SALARY"""
    try:
        with get_db_connection() as conn:
            # Проверяем существование колонок
            cur = conn.cursor()
            cur.execute("PRAGMA table_info(materials)")
            columns = [row[1] for row in cur.fetchall()]
            
            # УДАЛЕНО: Добавление поля salary
            
            if 'created_at' not in columns:
                cur.execute("ALTER TABLE materials ADD COLUMN created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP")
                logging.info("Добавлена колонка created_at")
            
            if 'updated_at' not in columns:
                cur.execute("ALTER TABLE materials ADD COLUMN updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP")
                logging.info("Добавлена колонка updated_at")
            
            # Создаем недостающие индексы
            cur.execute("CREATE INDEX IF NOT EXISTS idx_user_delivery ON materials(user_id, delivery_date)")
            
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
        self._user_materials_cache = {}
        self._user_stats_cache = {}
        self._cache_ttl = {
            'materials': 300,  # 5 минут
            'stats': 60,     # 1 минута
            'general': 600   # 10 минут
        }
        self.lru_cache = LRUCache(max_size=500)
        self.hit_stats = {}
        self.miss_stats = {}
    
    def get_user_materials(self, user_id: int):
        """Получение материалов с улучшенным кэшированием"""
        cache_key = f"materials_{user_id}"
        
        # Проверяем LRU кэш first
        cached = self.lru_cache.get(cache_key)
        if cached:
            data, timestamp = cached
            if time.time() - timestamp < self._cache_ttl['materials']:
                self._record_hit(user_id)
                return data
        
        # Проверяем обычный кэш
        if cache_key in self._user_materials_cache:
            data, timestamp = self._user_materials_cache[cache_key]
            if time.time() - timestamp < self._cache_ttl['materials']:
                self._record_hit(user_id)
                return data
        
        # Загрузка из БД
        materials = get_user_materials_db(user_id)
        self.lru_cache.set(cache_key, (materials, time.time()))
        self._user_materials_cache[cache_key] = (materials, time.time())
        self._record_miss(user_id)
        return materials
    
    def get_user_stats(self, user_id: int):
        """Получение статистики с кэшированием"""
        cache_key = f"stats_{user_id}"
        
        if cache_key in self._user_stats_cache:
            data, timestamp = self._user_stats_cache[cache_key]
            if time.time() - timestamp < self._cache_ttl['stats']:
                return data
        
        # Загрузка из БД
        materials = self.get_user_materials(user_id)
        stats = self._calculate_user_stats(materials)
        self._user_stats_cache[cache_key] = (stats, time.time())
        return stats
    
    def _calculate_user_stats(self, materials: List[Dict]) -> Dict:
        """Расчет статистики пользователя БЕЗ ЗАРПЛАТЫ"""
        today = datetime.now().date()
        stats = {
            'total': len(materials),
            'delivered': 0,
            'delivering_soon': 0,
            'upcoming': 0,
            'total_cost': 0,
            'total_quantity': 0
        }
        
        for m in materials:
            delivery_date = datetime.strptime(str(m['delivery_date']), '%Y-%m-%d').date()
            days_until = (delivery_date - today).days
            
            stats['total_cost'] += m.get('cost', 0)
            stats['total_quantity'] += m.get('quantity', 0)
            
            if days_until <= 0:
                stats['delivered'] += 1
            elif days_until <= 7:
                stats['delivering_soon'] += 1
            else:
                stats['upcoming'] += 1
        
        if stats['total'] > 0:
            stats['avg_cost'] = stats['total_cost'] / stats['total']
            stats['avg_quantity'] = stats['total_quantity'] / stats['total']
        else:
            stats['avg_cost'] = 0
            stats['avg_quantity'] = 0
            
        return stats
    
    def invalidate_user_cache(self, user_id: int):
        """Инвалидация кэша пользователя"""
        cache_key_materials = f"materials_{user_id}"
        cache_key_stats = f"stats_{user_id}"
        self._user_materials_cache.pop(cache_key_materials, None)
        self._user_stats_cache.pop(cache_key_stats, None)
        self.lru_cache.cache.pop(cache_key_materials, None)
    
    def clear_all_cache(self):
        """Очистка всего кэша"""
        self._user_materials_cache.clear()
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
def get_user_materials(user_id: int) -> List[Dict]:
    """Получение материалов пользователя"""
    return cache_manager.get_user_materials(user_id)

def get_fresh_user_materials(user_id: int) -> List[Dict]:
    """Получение свежих данных из БД, минуя кэш"""
    cache_manager.invalidate_user_cache(user_id)
    return get_user_materials_db(user_id)

def force_refresh_user_cache(user_id: int):
    """Принудительное обновление кэша пользователя"""
    cache_manager.invalidate_user_cache(user_id)
    # Очищаем соответствующие ключи в LRU кэше
    cache_keys = [f"materials_{user_id}", f"stats_{user_id}"]
    for key in cache_keys:
        cache_manager.lru_cache.cache.pop(key, None)
    
    # Получаем свежие данные
    return get_user_materials_db(user_id)

# ========== УМНАЯ СИСТЕМА НАПОМИНАНИЙ ==========
class SmartReminderSystem:
    """Умная система напоминаний"""
    
    def __init__(self):
        pass
    
    def send_reminders(self):
        """Отправка напоминаний (синхронная версия)"""
        try:
            # Получаем всех пользователей с материалами
            with get_db_connection() as conn:
                cur = conn.cursor()
                cur.execute('''
                    SELECT DISTINCT user_id FROM materials 
                    WHERE delivery_date BETWEEN date('now') AND date('now', '+7 days')
                    OR delivery_date <= date('now')
                ''')
                users_to_notify = cur.fetchall()
                
                for user_row in users_to_notify:
                    user_id = user_row[0]
                    materials = get_user_materials(user_id)
                    
                    delivering_materials = []
                    delivered_materials = []
                    
                    for m in materials:
                        delivery_date = datetime.strptime(str(m['delivery_date']), '%Y-%m-%d').date()
                        days_until = (delivery_date - datetime.now().date()).days
                        
                        if days_until <= 0:
                            delivered_materials.append((m, days_until))
                        elif days_until <= 7:
                            delivering_materials.append((m, days_until))
                    
                    if delivered_materials or delivering_materials:
                        message = "🔔 <b>НАПОМИНАНИЕ О МАТЕРИАЛАХ</b>\n\n"
                        
                        if delivered_materials:
                            message += "🟢 <b>ДОСТАВЛЕННЫЕ МАТЕРИАЛЫ:</b>\n"
                            for m, days in delivered_materials:
                                message += f"• {m['material_type']} ({m['location']}) - ДОСТАВЛЕН\n"
                            message += "\n"
                        
                        if delivering_materials:
                            message += "🟡 <b>СКОРО ДОСТАВКА:</b>\n"
                            for m, days in delivering_materials:
                                message += f"• {m['material_type']} ({m['location']}) - {days} дней\n"
                        
                        message += f"\n💫 Всего материалов: {len(materials)}"
                        
                        try:
                            # Синхронная отправка сообщения
                            asyncio.create_task(bot.send_message(
                                user_id, 
                                message, 
                                parse_mode='HTML'
                            ))
                        except Exception as e:
                            logging.warning(f"Не удалось отправить напоминание пользователю {user_id}: {e}")
            
        except Exception as e:
            logging.error(f"Ошибка в задаче напоминаний: {e}")

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
    """Обновленная клавиатура главного меню БЕЗ ЗАРПЛАТЫ"""
    builder = ReplyKeyboardBuilder()
    builder.button(text="📋 Мои материалы")
    builder.button(text="✨ Добавить материал")
    builder.button(text="⚙️ Управление материалами")
    builder.button(text="📊 Статистика")
    builder.button(text="📤 Импорт/Экспорт")
    builder.adjust(2)
    return builder.as_markup(resize_keyboard=True)

def get_add_material_keyboard():
    """Клавиатура для добавления материалов"""
    builder = ReplyKeyboardBuilder()
    builder.button(text="➕ Один материал")
    builder.button(text="🔙 Назад")
    builder.adjust(2)
    return builder.as_markup(resize_keyboard=True)

def get_material_type_keyboard():
    """Клавиатура для выбора типа материала с кнопкой ручного ввода"""
    builder = ReplyKeyboardBuilder()
    builder.button(text="Цемент")
    builder.button(text="Песок")
    builder.button(text="Щебень")
    builder.button(text="Арматура")
    builder.button(text="Кирпич")
    builder.button(text="Бетон")
    builder.button(text="Доска")
    builder.button(text="✏️ Другой тип материала")
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
    builder.button(text="✏️ Редактировать материал")
    builder.button(text="🗑️ Удалить материал")
    builder.button(text="📊 Онлайн Excel")
    builder.button(text="🔄 Двусторонняя синхронизация")
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
    builder.button(text="🔙 Назад")
    return builder.as_markup(resize_keyboard=True)

def get_edit_keyboard():
    """Клавиатура для редактирования БЕЗ ЗАРПЛАТЫ"""
    builder = ReplyKeyboardBuilder()
    builder.button(text="🏗️ Тип материала")
    builder.button(text="📍 Местоположение")
    builder.button(text="📅 Дата заказа")
    builder.button(text="🚚 Дата доставки")
    builder.button(text="📦 Количество")
    builder.button(text="💵 Стоимость")
    builder.button(text="🔙 Назад")
    builder.adjust(2)
    return builder.as_markup(resize_keyboard=True)

def get_materials_selection_keyboard(materials: List[Dict], action: str):
    """Клавиатура для выбора материала"""
    builder = ReplyKeyboardBuilder()
    for m in materials:
        builder.button(text=f"#{m['id']} - {m['material_type']} ({m['location']})")
    builder.button(text="🔙 Назад")
    builder.adjust(1)
    return builder.as_markup(resize_keyboard=True)

def get_status_icon_and_text(days_until_delivery: int):
    """Получение иконки и текста статуса"""
    if days_until_delivery <= 0:
        return "🟢", "ДОСТАВЛЕН"
    elif days_until_delivery <= 7:
        return "🟡", "СКОРО ДОСТАВКА"
    elif days_until_delivery <= 30:
        return "🟠", "В ПУТИ"
    else:
        return "🔵", "ЗАКАЗАН"

def format_date_nice(date):
    """Красивое форматирование даты"""
    return date.strftime("%d.%m.%Y")

def create_progress_bar(percentage: float, length: int = 10) -> str:
    """Создание текстового прогресс-бара"""
    filled = int(length * percentage / 100)
    empty = length - filled
    return f"[{'█' * filled}{'░' * empty}] {percentage:.1f}%"

def format_material_status_with_progress(material_data: Dict) -> str:
    """Форматирование статуса с прогресс-баром"""
    delivery_date = datetime.strptime(str(material_data['delivery_date']), '%Y-%m-%d').date()
    order_date = datetime.strptime(str(material_data['order_date']), '%Y-%m-%d').date()
    days_total = (delivery_date - order_date).days
    days_passed = (datetime.now().date() - order_date).days
    percentage = min(100, max(0, (days_passed / days_total) * 100)) if days_total > 0 else 0
    
    progress_bar = create_progress_bar(percentage)
    days_until = (delivery_date - datetime.now().date()).days
    
    return f"{progress_bar} ({days_passed}/{days_total} дней, доставка через: {days_until} дней)"

def create_delivery_infographic(materials):
    """Создание инфографики по доставкам"""
    today = datetime.now().date()
    delivered = 0
    delivering_soon = 0
    in_transit = 0
    ordered = 0
    
    for m in materials:
        delivery_date = datetime.strptime(str(m['delivery_date']), '%Y-%m-%d').date()
        days_until = (delivery_date - today).days
        
        if days_until <= 0:
            delivered += 1
        elif days_until <= 7:
            delivering_soon += 1
        elif days_until <= 30:
            in_transit += 1
        else:
            ordered += 1
    
    total = len(materials)
    if total > 0:
        delivery_percentage = (delivered / total) * 100
        progress_bar = create_progress_bar(delivery_percentage)
    else:
        progress_bar = create_progress_bar(0)
    
    return (
        f"📊 <b>СТАТУС ДОСТАВОК:</b>\n"
        f"{progress_bar}\n"
        f"🟢 Доставлено: {delivered}\n"
        f"🟡 Скоро доставка: {delivering_soon}\n"
        f"🟠 В пути: {in_transit}\n"
        f"🔵 Заказано: {ordered}"
    )

def is_admin(user_id: int) -> bool:
    """Проверка прав администратора"""
    return user_id == config.ADMIN_ID

def backup_database() -> bool:
    """Создание резервной копии базы данных"""
    try:
        if os.path.exists(config.DB_PATH):
            backup_name = f'materials_backup_{datetime.now().strftime("%Y%m%d_%H%M%S")}.db'
            backup_path = os.path.join(config.BACKUP_PATH, backup_name)
            shutil.copy2(config.DB_PATH, backup_path)
            logging.info(f"Создана резервная копия: {backup_path}")
            
            # Очистка старых бэкапов (оставляем последние 10)
            backup_files = [f for f in os.listdir(config.BACKUP_PATH) if f.startswith('materials_backup_')]
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

def check_user_permission(user_id: int, material_id: int) -> bool:
    """Проверка прав пользователя на материал"""
    try:
        material_data = get_material_by_id(material_id, user_id)
        return material_data is not None
    except Exception:
        return False

def validate_material_type(material_type: str) -> tuple[bool, str]:
    """Валидация типа материала"""
    material_type = sanitize_input(material_type)
    
    if not material_type or len(material_type.strip()) == 0:
        return False, "Тип материала не может быть пустым"
    
    if len(material_type) > 100:
        return False, "Тип материала слишком длинный (макс. 100 символов)"
    
    # Проверка на запрещенные символы
    if re.search(r'[<>{}[\]]', material_type):
        return False, "Тип материала содержит запрещенные символы"
    
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

def validate_quantity(quantity: str) -> tuple[bool, str, int]:
    """Валидация количества"""
    try:
        qty = int(quantity)
        if qty <= 0:
            return False, "Количество должно быть положительным числом", 0
        if qty > 1000000:  # Максимальное количество
            return False, "Количество слишком большое", 0
        return True, "OK", qty
    except ValueError:
        return False, "Количество должно быть числом", 0

def validate_cost(cost: str) -> tuple[bool, str, float]:
    """Валидация стоимости"""
    try:
        cost_val = float(cost)
        if cost_val < 0:
            return False, "Стоимость не может быть отрицательной", 0
        if cost_val > 100000000:  # Максимальная стоимость
            return False, "Стоимость слишком большая", 0
        return True, "OK", cost_val
    except ValueError:
        return False, "Стоимость должна быть числом", 0

# УДАЛЕНО: validate_salary функция

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
            max_future = today + timedelta(days=365*2)  # 2 года вперед
            
            if date_obj > max_future:
                raise ValueError("Дата не может быть более чем на 2 года в будущем")
            if date_obj < max_past:
                raise ValueError("Дата слишком старая (более 10 лет)")
                
            return date_obj
        except ValueError:
            continue
    
    # Попытка автоматического исправления
    corrected = try_auto_correct_date(date_str)
    if corrected:
        today = datetime.now().date()
        if corrected <= today + timedelta(days=365*2) and corrected >= today - timedelta(days=10*365):
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

# Регистрируем middleware для проверки прав администратора
dp.message.middleware(AdminMiddleware())

# Регистрация middleware
dp.update.outer_middleware(EnhancedMiddleware())

# ========== ЭКСПОРТ В EXCEL ==========
def export_to_excel(user_id: int) -> io.BytesIO:
    """Экспорт материалов в Excel"""
    materials = get_user_materials(user_id)
    
    if not materials:
        raise ValueError("Нет данных для экспорта")
    
    # Создаем DataFrame
    df = pd.DataFrame(materials)
    
    # Удаляем служебные колонки
    columns_to_drop = ['user_id', 'created_at', 'updated_at']
    for col in columns_to_drop:
        if col in df.columns:
            df = df.drop(columns=[col])
    
    # Добавляем вычисляемые поля
    today = datetime.now().date()
    df['order_date'] = pd.to_datetime(df['order_date']).dt.strftime('%d.%m.%Y')
    df['delivery_date'] = pd.to_datetime(df['delivery_date']).dt.strftime('%d.%m.%Y')
    
    # Добавляем статус
    def calculate_status(delivery_date_str):
        delivery_date = datetime.strptime(delivery_date_str, '%d.%m.%Y').date()
        days_until = (delivery_date - today).days
        icon, status = get_status_icon_and_text(days_until)
        return f"{icon} {status} ({days_until} дней)"
    
    df['Статус'] = df['delivery_date'].apply(calculate_status)
    
    # Добавляем общую стоимость
    df['Общая стоимость'] = df['quantity'] * df['cost']
    
    # Создаем Excel файл в памяти
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        df.to_excel(writer, sheet_name='Материалы', index=False)
        
        # Получаем workbook и worksheet для форматирования
        workbook = writer.book
        worksheet = writer.sheets['Материалы']
        
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

# ========== ОСТАЛЬНЫЕ НАСТРОКИ ==========
MAX_MATERIALS_PER_USER = 1000  # Очень высокий лимит, практически без ограничений

# States
class MaterialStates(StatesGroup):
    waiting_material_type = State()
    waiting_location = State()
    waiting_order_date = State()
    waiting_delivery_date = State()
    waiting_quantity = State()
    waiting_cost = State()
    waiting_confirmation = State()

class EditMaterialStates(StatesGroup):
    waiting_material_selection = State()
    waiting_field_selection = State()
    waiting_new_value = State()
    waiting_confirmation = State()

class DeleteMaterialStates(StatesGroup):
    waiting_material_selection = State()
    waiting_confirmation = State()

# УДАЛЕНО: SalaryStates класс

class ImportExportStates(StatesGroup):
    waiting_excel_file = State()

class GoogleSheetsStates(StatesGroup):
    waiting_sheet_id = State()
    waiting_sync_confirmation = State()

# ========== СИНХРОННАЯ GOOGLE SHEETS ИНТЕГРАЦИЯ ==========
class GoogleSheetsSync:
    def __init__(self):
        self.credentials = None
        self.sheet_id = None
        self.auto_sync = False
        self.last_sync_time = {}
        self.sync_interval = config.REAL_TIME_SYNC_INTERVAL  # 300 секунд
        self.last_sync_from_time = {}  # Для двусторонней синхронизации
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
    
    def sync_to_sheets(self, user_id: int, user_materials: List[Dict]) -> tuple[bool, str]:
        """Синхронизация данных с Google Sheets с полной очисткой БЕЗ ЗАРПЛАТЫ"""
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
                worksheet = sheet.add_worksheet(title=worksheet_name, rows=100, cols=12)
            
            # Заголовки БЕЗ ЗАРПЛАТЫ
            headers = ['ID', 'Тип материала', 'Местоположение', 'Дата заказа', 
                      'Дата доставки', 'Количество', 'Стоимость за ед.', 'Общая стоимость',
                      'Статус', 'Осталось дней', 'Прогресс доставки']
            worksheet.append_row(headers)
            
            # Подготавливаем данные
            today = datetime.now().date()
            rows = []
            
            for m in user_materials:
                delivery_date = datetime.strptime(str(m['delivery_date']), '%Y-%m-%d').date()
                order_date = datetime.strptime(str(m['order_date']), '%Y-%m-%d').date()
                days_until = (delivery_date - today).days
                
                icon, status = get_status_icon_and_text(days_until)
                
                # Расчет прогресса доставки
                total_days = (delivery_date - order_date).days
                days_passed = (today - order_date).days
                progress = min(100, max(0, (days_passed / total_days) * 100)) if total_days > 0 else 0
                
                total_cost = m['quantity'] * m['cost']
                
                row = [
                    m['id'],
                    m['material_type'],
                    m['location'],
                    format_date_nice(order_date),
                    format_date_nice(delivery_date),
                    m['quantity'],
                    m['cost'],
                    total_cost,
                    status,
                    days_until,
                    f"{progress:.1f}%"
                ]
                rows.append(row)
            
            # Добавляем данные
            if rows:
                worksheet.append_rows(rows)
            
            # Форматируем таблицу
            try:
                # Заголовки жирным
                worksheet.format('A1:K1', {'textFormat': {'bold': True}})
                
                # Авто-ширина колонок
                sheet.batch_update({
                    "requests": [
                        {
                            "autoResizeDimensions": {
                                "dimensions": {
                                    "sheetId": worksheet.id,
                                    "dimension": "COLUMNS",
                                    "startIndex": 0,
                                    "endIndex": 11
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
            return True, f"Успешно синхронизировано {len(rows)} материалов"
            
        except Exception as e:
            logging.error(f"Ошибка синхронизации с Google Sheets: {e}")
            health_monitor.record_error()
            return False, f"Ошибка синхронизации: {str(e)}"
    
    def sync_from_sheets(self, user_id: int) -> tuple[bool, str]:
        """Синхронизация данных ИЗ Google Sheets в бота БЕЗ ЗАРПЛАТЫ"""
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
            
            # Получаем лист пользователя
            worksheet_name = f"User_{user_id}"
            try:
                worksheet = sheet.worksheet(worksheet_name)
            except gspread.exceptions.WorksheetNotFound:
                return False, "Лист пользователя не найден"
            
            # Получаем все данные из листа
            sheet_data = worksheet.get_all_records()
            
            if not sheet_data:
                return True, "Лист пустой"
            
            # Получаем текущие материалы из базы
            current_materials = get_user_materials_db(user_id)
            current_material_ids = {m['id'] for m in current_materials}
            
            updated_count = 0
            added_count = 0
            
            for row in sheet_data:
                if not row.get('ID'):
                    continue
                
                material_id = int(row['ID'])
                material_type = row.get('Тип материала', '')
                location = row.get('Местоположение', '')
                order_date_str = row.get('Дата заказа', '')
                delivery_date_str = row.get('Дата доставки', '')
                quantity = row.get('Количество', 0)
                cost = row.get('Стоимость за ед.', 0)
                
                # Пропускаем некорректные данные
                if not all([material_type, location, order_date_str, delivery_date_str, quantity, cost]):
                    continue
                
                try:
                    # Преобразуем даты
                    order_date = datetime.strptime(order_date_str, '%d.%m.%Y').date()
                    delivery_date = datetime.strptime(delivery_date_str, '%d.%m.%Y').date()
                    
                    if material_id in current_material_ids:
                        # Обновляем существующий материал
                        success = update_material_in_db(
                            material_id, user_id,
                            material_type=material_type,
                            location=location,
                            order_date=order_date.strftime('%Y-%m-%d'),
                            delivery_date=delivery_date.strftime('%Y-%m-%d'),
                            quantity=quantity,
                            cost=cost
                        )
                        if success:
                            updated_count += 1
                    else:
                        # Добавляем новый материал
                        success = add_material_to_db(
                            user_id=user_id,
                            material_type=material_type,
                            location=location,
                            order_date=order_date.strftime('%Y-%m-%d'),
                            delivery_date=delivery_date.strftime('%Y-%m-%d'),
                            quantity=quantity,
                            cost=cost
                        )
                        if success:
                            added_count += 1
                            
                except Exception as e:
                    logging.error(f"Ошибка обработки строки {row}: {e}")
                    continue
            
            health_monitor.record_sync_operation()
            return True, f"Добавлено: {added_count}, Обновлено: {updated_count}"
            
        except Exception as e:
            logging.error(f"Ошибка синхронизации из Google Sheets: {e}")
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
def safe_sync_to_sheets(user_id: int, materials: List[Dict]) -> tuple[bool, str]:
    """Безопасная синхронизация с обработкой ошибок"""
    try:
        health_monitor.record_sync_operation()
        return google_sync.sync_to_sheets(user_id, materials)
    except ImportError:
        return False, "Библиотеки Google не установлены. Установите: pip install gspread google-auth"
    except Exception as e:
        logging.error(f"Ошибка синхронизации: {e}")
        return False, f"Ошибка синхронизации: {str(e)}"

# ========== УВЕДОМЛЕНИЯ АДМИНИСТРАТОРА ==========
async def notify_admin(message: str):
    """Уведомление администратора"""
    try:
        if config.ADMIN_ID:
            await bot.send_message(
                config.ADMIN_ID,
                message,
                parse_mode='HTML'
            )
    except Exception as e:
        logging.error(f"Ошибка уведомления администратора: {e}")

# ========== ОБРАБОТЧИК ОШИБОК ==========
async def error_handler(update: types.Update, exception: Exception):
    """Глобальный обработчик ошибок"""
    try:
        # Логируем ошибку
        logging.error(f"Ошибка при обработке update {update}: {exception}")
        health_monitor.record_error()
        
        # Уведомляем администратора
        error_traceback = "".join(traceback.format_exception(None, exception, exception.__traceback__))
        short_error = str(exception)[:1000]
        
        await notify_admin(
            f"🚨 <b>КРИТИЧЕСКАЯ ОШИБКА</b>\n\n"
            f"💥 <b>Ошибка:</b> {short_error}\n"
            f"📱 <b>Update:</b> {update}\n\n"
            f"🔧 <i>Подробности в логаз</i>"
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
def send_reminders():
    """Синхронная отправка напоминаний"""
    while True:
        try:
            smart_reminders.send_reminders()
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
                    asyncio.create_task(notify_admin(
                        f"⚠️ <b>НИЗКИЙ HEALTH SCORE</b>\n\n"
                        f"📊 Текущий score: {health_status['health_score']:.1f}%\n"
                        f"💥 Ошибок: {health_status['error_count']}\n"
                        f"📨 Сообщений: {health_status['message_count']}\n"
                        f"💾 Hit Rate кэша: {health_status['cache_hit_rate']:.1f}%"
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

def bidirectional_sync_task():
    """Задача двусторонней синхронизации"""
    logging.info("🔄 Запуск задачи двусторонней синхронизации (интервал: 300 секунд)")
    
    while True:
        try:
            if google_sync.auto_sync and google_sync.is_configured():
                # Получаем всех пользователей с материалами
                with get_db_connection() as conn:
                    cur = conn.cursor()
                    cur.execute("SELECT DISTINCT user_id FROM materials")
                    users = cur.fetchall()
                    
                    synced_to_count = 0
                    synced_from_count = 0
                    total_users = len(users)
                    
                    for user_row in users:
                        user_id = user_row[0]
                        
                        # Синхронизация ИЗ бота В таблицу (как было)
                        if google_sync.should_sync_user(user_id):
                            materials = get_user_materials(user_id)
                            if materials:
                                success, message = google_sync.sync_to_sheets(user_id, materials)
                                if success:
                                    synced_to_count += 1
                        
                        # НОВАЯ СИНХРОНИЗАЦИЯ ИЗ ТАБЛИЦЫ В БОТА (каждые 10 минут)
                        current_time = datetime.now()
                        last_from_sync = google_sync.last_sync_from_time.get(user_id)
                        
                        if not last_from_sync or (current_time - last_from_sync).total_seconds() >= 600:  # 10 минут
                            success, message = google_sync.sync_from_sheets(user_id)
                            if success:
                                synced_from_count += 1
                                google_sync.last_sync_from_time[user_id] = current_time
                    
                    # Логируем статистику
                    if hasattr(bidirectional_sync_task, 'cycle_count'):
                        bidirectional_sync_task.cycle_count += 1
                    else:
                        bidirectional_sync_task.cycle_count = 1
                    
                    if bidirectional_sync_task.cycle_count % 10 == 0:
                        logging.info(f"📊 Двусторонняя синхронизация: {synced_to_count}→таблицу, {synced_from_count}←из таблицы")
            
            time.sleep(config.REAL_TIME_SYNC_INTERVAL)  # 300 секунд
            
        except Exception as e:
            logging.error(f"❌ Ошибка в задаче двусторонней синхронизации: {e}")
            time.sleep(60)  # При ошибке ждем 60 секунд

# ========== ОБРАБОТЧИКИ SIGNAL ДЛЯ УВЕДОМЛЕНИЙ О ЗАПУСКЕ/ОСТАНОВКЕ ==========
def handle_shutdown(signum, frame):
    """Обработчик сигнала остановки"""
    async def shutdown():
        await notify_admin(
            "🛑 <b>БОТ ОСТАНОВЛЕН</b>\n\n"
            f"⏰ Время: {datetime.now().strftime('%d.%m.%Y %H:%M:%S')}\n"
            f"📊 Аптайм: {datetime.now() - health_monitor.start_time}\n"
            f"💾 База данных: сохранена\n"
            f"🔜 Автозапуск: зависит от настроек сервера"
        )
        # Даем время на отправку сообщения
        await asyncio.sleep(2)
        sys.exit(0)
    
    asyncio.create_task(shutdown())

# Регистрация обработчиков сигналов
signal.signal(signal.SIGINT, handle_shutdown)
signal.signal(signal.SIGTERM, handle_shutdown)

# ========== ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ДЛЯ УПРАВЛЕНИЯ ==========
async def show_materials_for_selection(message: types.Message, materials: List[Dict], action: str):
    """Показать материалы для выбора"""
    if not materials:
        await message.answer("❌ Нет материалов для выбора")
        return
    
    text = f"📋 <b>ВЫБЕРИТЕ МАТЕРИАЛ ДЛЯ {action.upper()}:</b>\n\n"
    
    for m in materials:
        delivery_date = datetime.strptime(str(m['delivery_date']), '%Y-%m-%d').date()
        days_until = (delivery_date - datetime.now().date()).days
        icon, status = get_status_icon_and_text(days_until)
        
        text += (
            f"{icon} <b>#{m['id']}</b> - {m['material_type']}\n"
            f"📍 {m['location']} | 📅 {format_date_nice(delivery_date)} | {status}\n\n"
        )
    
    await message.answer(
        text,
        reply_markup=get_materials_selection_keyboard(materials, action),
        parse_mode='HTML'
    )

# ========== ОБРАБОТЧИКИ INLINE КНОПОК ==========
@dp.callback_query(lambda c: c.data.startswith('delivered_'))
async def process_delivered_material(callback_query: types.CallbackQuery):
    """Обработка кнопки 'Доставлен'"""
    try:
        material_id = int(callback_query.data.split('_')[1])
        user_id = callback_query.from_user.id
        
        # Обновляем дату доставки на сегодня
        today = datetime.now().date()
        success = update_material_in_db(
            material_id, 
            user_id, 
            delivery_date=today.strftime('%Y-%m-%d')
        )
        
        if success:
            await callback_query.message.edit_text(
                f"✅ <b>МАТЕРИАЛ ОБНОВЛЕН!</b>\n\n"
                f"Дата доставки установлена на сегодня.",
                parse_mode='HTML'
            )
        else:
            await callback_query.answer("❌ Ошибка при обновлении материала", show_alert=True)
            
    except Exception as e:
        logging.error(f"Ошибка при обработке delivered: {e}")
        await callback_query.answer("❌ Произошла ошибка", show_alert=True)

@dp.callback_query(lambda c: c.data.startswith('postpone_'))
async def process_postpone_material(callback_query: types.CallbackQuery):
    """Обработка кнопки 'Перенести на неделю'"""
    try:
        material_id = int(callback_query.data.split('_')[1])
        user_id = callback_query.from_user.id
        
        material_data = get_material_by_id(material_id, user_id)
        if not material_data:
            await callback_query.answer("❌ Материал не найден", show_alert=True)
            return
        
        # Переносим на 7 дней вперед
        current_delivery = datetime.strptime(str(material_data['delivery_date']), '%Y-%m-%d').date()
        new_delivery = current_delivery + timedelta(days=7)
        
        success = update_material_in_db(
            material_id, 
            user_id, 
            delivery_date=new_delivery.strftime('%Y-%m-%d')
        )
        
        if success:
            await callback_query.message.edit_text(
                f"🔄 <b>СРОК ПЕРЕНЕСЕН!</b>\n\n"
                f"Новая дата доставки: {format_date_nice(new_delivery)}",
                parse_mode='HTML'
            )
        else:
            await callback_query.answer("❌ Ошибка при переносе срока", show_alert=True)
            
    except Exception as e:
        logging.error(f"Ошибка при обработке postpone: {e}")
        await callback_query.answer("❌ Произошла ошибка", show_alert=True)

@dp.callback_query(lambda c: c.data.startswith('details_'))
async def process_details_material(callback_query: types.CallbackQuery):
    """Обработка кнопки 'Посмотреть детали'"""
    try:
        material_id = int(callback_query.data.split('_')[1])
        user_id = callback_query.from_user.id
        
        material_data = get_material_by_id(material_id, user_id)
        if not material_data:
            await callback_query.answer("❌ Материал не найден", show_alert=True)
            return
        
        delivery_date = datetime.strptime(str(material_data['delivery_date']), '%Y-%m-%d').date()
        order_date = datetime.strptime(str(material_data['order_date']), '%Y-%m-%d').date()
        days_until = (delivery_date - datetime.now().date()).days
        icon, status = get_status_icon_and_text(days_until)
        total_cost = material_data['quantity'] * material_data['cost']
        
        details_text = (
            f"🔍 <b>ДЕТАЛИ МАТЕРИАЛА #{material_id}</b>\n\n"
            f"{icon} <b>Статус:</b> {status}\n"
            f"🏗️ <b>Тип:</b> {material_data['material_type']}\n"
            f"📍 <b>Местоположение:</b> {material_data['location']}\n"
            f"📅 <b>Дата заказа:</b> {format_date_nice(order_date)}\n"
            f"🚚 <b>Дата доставки:</b> {format_date_nice(delivery_date)}\n"
            f"📦 <b>Количество:</b> {material_data['quantity']}\n"
            f"💵 <b>Стоимость за ед.:</b> {material_data['cost']:.2f} руб.\n"
            f"💰 <b>Общая стоимость:</b> {total_cost:.2f} руб.\n"
            f"📊 <b>Прогресс:</b> {format_material_status_with_progress(material_data)}"
        )
        
        await callback_query.message.edit_text(details_text, parse_mode='HTML')
        
    except Exception as e:
        logging.error(f"Ошибка при обработке details: {e}")
        await callback_query.answer("❌ Произошла ошибка", show_alert=True)

# ========== ОСНОВНЫЕ ОБРАБОТЧИКИ ==========
@dp.message(Command("start"))
async def cmd_start(message: types.Message, state: FSMContext):
    """Обработчик команды /start с rate limiting"""
    if not check_access(message.from_user.id):
        await message.answer(
            "❌ <b>ДОСТУП ЗАПРЕЩЕН</b>\n\n"
            "Этот бот доступен только для администраторов ООО «ИКС ГЕОСТРОЙ».",
            parse_mode='HTML'
        )
        return
    
    health_monitor.record_message(message.from_user.id)
    
    # Всегда очищаем состояние при старте
    await state.clear()
    
    # Проверяем статус автосинхронизации
    sync_status = ""
    if google_sync.auto_sync and google_sync.is_configured():
        sync_status = "\n\n🔄 <b>Автосинхронизация активна</b>\nДанные обновляются каждые 300 секунд"
    
    await message.answer(
        "🏗️ <b>ООО ИКС ГЕОСТРОЙ</b>\n"
        "📦 <b>Учет строительных материалов</b> 🤖\n\n"
        "📊 <i>Комплексная система учета строительных материалов</i>\n\n"
        "🎯 <b>Основные возможности:</b>\n"
        "• 📋 Просмотр всех материалов\n"
        "• ✨ Добавление новых материалов\n"
        "• ⏳ Контроль сроков доставки\n"
        "• ⚙️ Полное управление базой\n"
        "• 📊 Детальная статистика\n"
        "• 📤 Импорт/Экспорт Excel\n"
        "• ☁️ Синхронизация с Google Sheets\n"
        "• 🔔 Автоматические напоминания\n"
        "• ⚡ <b>Синхронизация в реальном времени (300 сек)</b>"
        f"{sync_status}\n\n"
        "🏗️ <i>Официальная система учета ООО «ИКС ГЕОСТРОЙ»</i>",
        reply_markup=get_main_keyboard(),
        parse_mode='HTML'
    )

@dp.message(F.text == "📋 Мои материалы")
async def cmd_show_materials(message: types.Message):
    """Показать все материалы пользователя"""
    health_monitor.record_message(message.from_user.id)
    
    user_id = message.from_user.id
    materials = get_user_materials(user_id)
    
    if not materials:
        await message.answer(
            "📭 <b>У вас пока нет материалов</b>\n\n"
            "Добавьте первый материал через меню '✨ Добавить материал'",
            reply_markup=get_main_keyboard(),
            parse_mode='HTML'
        )
        return
    
    # Сортируем материалы по дате доставки
    materials.sort(key=lambda x: x['delivery_date'])
    
    # Создаем сообщение с материалами
    response = "📦 <b>ВАШИ МАТЕРИАЛЫ:</b>\n\n"
    
    today = datetime.now().date()
    delivered_count = 0
    delivering_soon_count = 0
    
    for i, material in enumerate(materials, 1):
        delivery_date = datetime.strptime(str(material['delivery_date']), '%Y-%m-%d').date()
        days_until = (delivery_date - today).days
        icon, status = get_status_icon_and_text(days_until)
        total_cost = material['quantity'] * material['cost']
        
        response += (
            f"{icon} <b>#{material['id']}</b> - {material['material_type']}\n"
            f"📍 <b>Местоположение:</b> {material['location']}\n"
            f"📅 <b>Доставка:</b> {format_date_nice(delivery_date)} ({days_until} дней)\n"
            f"📦 <b>Количество:</b> {material['quantity']}\n"
            f"💰 <b>Стоимость:</b> {total_cost:.2f} руб.\n"
            f"📊 <b>Статус:</b> {status}\n"
        )
        
        # Добавляем прогресс-бар для материалов в процессе доставки
        if days_until > 0:
            order_date = datetime.strptime(str(material['order_date']), '%Y-%m-%d').date()
            progress_text = format_material_status_with_progress(material)
            response += f"⏳ <b>Прогресс:</b> {progress_text}\n"
        
        response += "─" * 30 + "\n"
        
        # Считаем статистику
        if days_until <= 0:
            delivered_count += 1
        elif days_until <= 7:
            delivering_soon_count += 1
    
    # Добавляем инфографику
    infographic = create_delivery_infographic(materials)
    response += f"\n{infographic}"
    
    await message.answer(response, parse_mode='HTML')

@dp.message(F.text == "✨ Добавить материал")
async def cmd_add_material(message: types.Message, state: FSMContext):
    """Начало добавления материала"""
    health_monitor.record_message(message.from_user.id)
    
    await message.answer(
        "🏗️ <b>ДОБАВЛЕНИЕ МАТЕРИАЛА</b>\n\n"
        "Выберите тип материала:",
        reply_markup=get_material_type_keyboard(),
        parse_mode='HTML'
    )
    await state.set_state(MaterialStates.waiting_material_type)

@dp.message(MaterialStates.waiting_material_type)
async def process_material_type(message: types.Message, state: FSMContext):
    """Обработка типа материала"""
    if message.text == "🔙 Назад":
        await state.clear()
        await message.answer("🔙 Возврат в главное меню", reply_markup=get_main_keyboard())
        return
    
    material_type = message.text
    
    # Если выбрана ручная опция
    if material_type == "✏️ Другой тип материала":
        await message.answer(
            "🏗️ <b>Введите тип материала:</b>",
            reply_markup=get_back_keyboard(),
            parse_mode='HTML'
        )
        return
    
    # Проверяем валидность типа материала
    is_valid, error_msg = validate_material_type(material_type)
    if not is_valid:
        await message.answer(f"❌ {error_msg}\n\nВыберите тип материала:")
        return
    
    await state.update_data(material_type=material_type)
    await message.answer(
        "📍 <b>Введите местоположение:</b>\n\n"
        "Пример: Склад №1, Участок №2, Объект 'Северный'",
        reply_markup=get_back_keyboard(),
        parse_mode='HTML'
    )
    await state.set_state(MaterialStates.waiting_location)

@dp.message(MaterialStates.waiting_location)
async def process_location(message: types.Message, state: FSMContext):
    """Обработка местоположения"""
    if message.text == "🔙 Назад":
        await message.answer("Выберите тип материала:", reply_markup=get_material_type_keyboard())
        await state.set_state(MaterialStates.waiting_material_type)
        return
    
    location = message.text
    
    # Проверяем валидность местоположения
    is_valid, error_msg = validate_location(location)
    if not is_valid:
        await message.answer(f"❌ {error_msg}\n\nВведите местоположение:")
        return
    
    await state.update_data(location=location)
    await message.answer(
        "📅 <b>Введите дату заказа:</b>\n\n"
        "Формат: ДД.ММ.ГГГГ или ДД.ММ\n"
        "Пример: 15.12.2023 или 15.12",
        reply_markup=get_back_keyboard(),
        parse_mode='HTML'
    )
    await state.set_state(MaterialStates.waiting_order_date)

@dp.message(MaterialStates.waiting_order_date)
async def process_order_date(message: types.Message, state: FSMContext):
    """Обработка даты заказа"""
    if message.text == "🔙 Назад":
        await message.answer(
            "📍 Введите местоположение:",
            reply_markup=get_back_keyboard()
        )
        await state.set_state(MaterialStates.waiting_location)
        return
    
    try:
        order_date = enhanced_validate_date(message.text)
        await state.update_data(order_date=order_date.strftime('%Y-%m-%d'))
        await message.answer(
            "🚚 <b>Введите дату доставки:</b>\n\n"
            "Формат: ДД.ММ.ГГГГ или ДД.ММ\n"
            "Пример: 20.12.2023 или 20.12",
            reply_markup=get_back_keyboard(),
            parse_mode='HTML'
        )
        await state.set_state(MaterialStates.waiting_delivery_date)
    except ValueError as e:
        await message.answer(f"❌ {str(e)}\n\nВведите дату заказа:")

@dp.message(MaterialStates.waiting_delivery_date)
async def process_delivery_date(message: types.Message, state: FSMContext):
    """Обработка даты доставки"""
    if message.text == "🔙 Назад":
        await message.answer(
            "📅 Введите дату заказа:",
            reply_markup=get_back_keyboard()
        )
        await state.set_state(MaterialStates.waiting_order_date)
        return
    
    try:
        delivery_date = enhanced_validate_date(message.text)
        
        # Проверяем, что дата доставки не раньше даты заказа
        data = await state.get_data()
        order_date = datetime.strptime(data['order_date'], '%Y-%m-%d').date()
        
        if delivery_date < order_date:
            await message.answer(
                "❌ <b>Дата доставки не может быть раньше даты заказа!</b>\n\n"
                f"Дата заказа: {format_date_nice(order_date)}\n"
                f"Введите корректную дату доставки:",
                parse_mode='HTML'
            )
            return
        
        await state.update_data(delivery_date=delivery_date.strftime('%Y-%m-%d'))
        await message.answer(
            "📦 <b>Введите количество:</b>",
            reply_markup=get_back_keyboard(),
            parse_mode='HTML'
        )
        await state.set_state(MaterialStates.waiting_quantity)
    except ValueError as e:
        await message.answer(f"❌ {str(e)}\n\nВведите дату доставки:")

@dp.message(MaterialStates.waiting_quantity)
async def process_quantity(message: types.Message, state: FSMContext):
    """Обработка количества"""
    if message.text == "🔙 Назад":
        await message.answer(
            "🚚 Введите дату доставки:",
            reply_markup=get_back_keyboard()
        )
        await state.set_state(MaterialStates.waiting_delivery_date)
        return
    
    try:
        is_valid, error_msg, quantity = validate_quantity(message.text)
        if not is_valid:
            await message.answer(f"❌ {error_msg}\n\nВведите количество:")
            return
        
        await state.update_data(quantity=quantity)
        await message.answer(
            "💵 <b>Введите стоимость за единицу (в рублях):</b>",
            reply_markup=get_back_keyboard(),
            parse_mode='HTML'
        )
        await state.set_state(MaterialStates.waiting_cost)
    except Exception as e:
        await message.answer(f"❌ Ошибка при обработке количества: {str(e)}")

@dp.message(MaterialStates.waiting_cost)
async def process_cost(message: types.Message, state: FSMContext):
    """Обработка стоимости материала (последний шаг перед подтверждением)"""
    if message.text == "🔙 Назад":
        await message.answer("Введите количество:", reply_markup=get_back_keyboard())
        await state.set_state(MaterialStates.waiting_quantity)
        return
    
    try:
        is_valid, error_msg, cost = validate_cost(message.text)
        if not is_valid:
            await message.answer(f"❌ {error_msg}\n\nВведите стоимость за единицу:")
            return
        
        await state.update_data(cost=cost)
        data = await state.get_data()
        
        # Форматируем данные для подтверждения БЕЗ ЗАРПЛАТЫ
        order_date = datetime.strptime(str(data['order_date']), '%Y-%m-%d').date()
        delivery_date = datetime.strptime(str(data['delivery_date']), '%Y-%m-%d').date()
        days_until = (delivery_date - datetime.now().date()).days
        icon, status = get_status_icon_and_text(days_until)
        total_cost = data['quantity'] * cost
        
        confirmation_text = (
            f"✅ <b>ПОДТВЕРЖДЕНИЕ ДАННЫХ</b>\n\n"
            f"{icon} <b>Статус:</b> {status}\n"
            f"🏗️ <b>Тип материала:</b> {data['material_type']}\n"
            f"📍 <b>Местоположение:</b> {data['location']}\n"
            f"📅 <b>Дата заказа:</b> {format_date_nice(order_date)}\n"
            f"🚚 <b>Дата доставки:</b> {format_date_nice(delivery_date)}\n"
            f"📦 <b>Количество:</b> {data['quantity']}\n"
            f"💵 <b>Стоимость за ед.:</b> {cost:.2f} руб.\n"
            f"💰 <b>Общая стоимость:</b> {total_cost:.2f} руб.\n\n"
            f"<b>Всё верно?</b>"
        )
        
        await message.answer(
            confirmation_text,
            reply_markup=get_confirmation_keyboard(),
            parse_mode='HTML'
        )
        await state.set_state(MaterialStates.waiting_confirmation)
        
    except Exception as e:
        logging.error(f"Ошибка при обработке стоимости: {e}")
        await message.answer("❌ Ошибка при обработке стоимости. Введите стоимость за единицу:")

@dp.message(MaterialStates.waiting_confirmation)
async def process_confirmation(message: types.Message, state: FSMContext):
    """Обработка подтверждения добавления материала"""
    if message.text == "✅ Да, всё верно":
        data = await state.get_data()
        user_id = message.from_user.id
        
        # Добавляем материал в БД
        success = add_material_to_db(
            user_id=user_id,
            material_type=data['material_type'],
            location=data['location'],
            order_date=data['order_date'],
            delivery_date=data['delivery_date'],
            quantity=data['quantity'],
            cost=data['cost']
        )
        
        if success:
            await message.answer(
                "✅ <b>МАТЕРИАЛ УСПЕШНО ДОБАВЛЕН!</b>\n\n"
                f"🏗️ <b>Тип:</b> {data['material_type']}\n"
                f"📍 <b>Местоположение:</b> {data['location']}\n"
                f"📦 <b>Количество:</b> {data['quantity']}\n"
                f"💰 <b>Общая стоимость:</b> {data['quantity'] * data['cost']:.2f} руб.\n\n"
                f"💫 Материал добавлен в вашу базу данных.",
                reply_markup=get_main_keyboard(),
                parse_mode='HTML'
            )
        else:
            await message.answer(
                "❌ <b>Ошибка при добавлении материала</b>\n\n"
                "Пожалуйста, попробуйте еще раз.",
                reply_markup=get_main_keyboard(),
                parse_mode='HTML'
            )
        
        await state.clear()
        
    elif message.text == "❌ Нет, изменить":
        await message.answer(
            "🔄 <b>Начнем заново</b>\n\n"
            "Выберите тип материала:",
            reply_markup=get_material_type_keyboard(),
            parse_mode='HTML'
        )
        await state.set_state(MaterialStates.waiting_material_type)
    else:
        await message.answer(
            "Пожалуйста, подтвердите добавление материала:",
            reply_markup=get_confirmation_keyboard()
        )

@dp.message(F.text == "⚙️ Управление материалами")
async def cmd_management(message: types.Message):
    """Меню управления материалами"""
    health_monitor.record_message(message.from_user.id)
    
    await message.answer(
        "⚙️ <b>УПРАВЛЕНИЕ МАТЕРИАЛАМИ</b>\n\n"
        "Выберите действие:",
        reply_markup=get_management_keyboard(),
        parse_mode='HTML'
    )

@dp.message(F.text == "📊 Статистика")
async def cmd_statistics(message: types.Message):
    """Показать статистику"""
    health_monitor.record_message(message.from_user.id)
    
    user_id = message.from_user.id
    stats = cache_manager.get_user_stats(user_id)
    all_stats = get_all_users_stats()
    
    stats_text = (
        f"📊 <b>СТАТИСТИКА МАТЕРИАЛОВ</b>\n\n"
        f"👤 <b>Ваша статистика:</b>\n"
        f"• 📦 Всего материалов: {stats['total']}\n"
        f"• 🟢 Доставлено: {stats['delivered']}\n"
        f"• 🟡 Скоро доставка: {stats['delivering_soon']}\n"
        f"• 🔵 Будущие: {stats['upcoming']}\n"
        f"• 💰 Общая стоимость: {stats['total_cost']:.2f} руб.\n"
        f"• 📦 Общее количество: {stats['total_quantity']}\n"
        f"• 💵 Средняя стоимость: {stats['avg_cost']:.2f} руб.\n"
        f"• 📦 Среднее количество: {stats['avg_quantity']:.1f}\n\n"
        f"🏢 <b>Общая статистика системы:</b>\n"
        f"• 👥 Пользователей: {all_stats['total_users']}\n"
        f"• 📦 Всего материалов: {all_stats['total_materials']}\n"
        f"• 🟢 Доставлено: {all_stats['delivered_materials']}\n"
        f"• 🟡 Скоро доставка: {all_stats['delivering_soon']}"
    )
    
    await message.answer(stats_text, parse_mode='HTML')

@dp.message(F.text == "📤 Импорт/Экспорт")
async def cmd_import_export(message: types.Message):
    """Меню импорта/экспорта"""
    health_monitor.record_message(message.from_user.id)
    
    keyboard = ReplyKeyboardBuilder()
    keyboard.button(text="📤 Экспорт в Excel")
    keyboard.button(text="📋 Шаблон Excel")
    keyboard.button(text="☁️ Синхронизация с Google Sheets")
    keyboard.button(text="🔙 Назад")
    keyboard.adjust(2)
    
    await message.answer(
        "📤 <b>ИМПОРТ/ЭКСПОРТ ДАННЫХ</b>\n\n"
        "Выберите действие:",
        reply_markup=keyboard.as_markup(resize_keyboard=True),
        parse_mode='HTML'
    )

@dp.message(F.text == "📤 Экспорт в Excel")
async def cmd_export_excel(message: types.Message):
    """Экспорт данных в Excel"""
    health_monitor.record_message(message.from_user.id)
    
    user_id = message.from_user.id
    materials = get_user_materials(user_id)
    
    if not materials:
        await message.answer(
            "❌ <b>Нет данных для экспорта</b>\n\n"
            "Сначала добавьте материалы через меню '✨ Добавить материал'",
            reply_markup=get_main_keyboard(),
            parse_mode='HTML'
        )
        return
    
    try:
        excel_file = export_to_excel(user_id)
        await message.answer_document(
            types.BufferedInputFile(
                excel_file.getvalue(),
                filename=f"materials_export_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"
            ),
            caption="📊 <b>ЭКСПОРТ МАТЕРИАЛОВ В EXCEL</b>\n\n"
                   f"📦 Материалов: {len(materials)}\n"
                   f"📅 Дата экспорта: {datetime.now().strftime('%d.%m.%Y %H:%M')}",
            parse_mode='HTML'
        )
    except Exception as e:
        logging.error(f"Ошибка при экспорте в Excel: {e}")
        await message.answer(
            "❌ <b>Ошибка при экспорте данных</b>\n\n"
            "Пожалуйста, попробуйте позже.",
            parse_mode='HTML'
        )

@dp.message(F.text == "☁️ Синхронизация с Google Sheets")
async def cmd_google_sheets(message: types.Message):
    """Меню синхронизации с Google Sheets"""
    health_monitor.record_message(message.from_user.id)
    
    keyboard = ReplyKeyboardBuilder()
    keyboard.button(text="🔄 Синхронизировать с Google Sheets")
    
    if google_sync.is_configured():
        status = "🟢 Настроена" if google_sync.sheet_id else "🔴 Не настроена"
        auto_status = "ВКЛ" if google_sync.auto_sync else "ВЫКЛ"
        
        await message.answer(
            f"☁️ <b>СИНХРОНИЗАЦИЯ С GOOGLE SHEETS</b>\n\n"
            f"📊 <b>Статус:</b> {status}\n"
            f"🔄 <b>Автосинхронизация:</b> {auto_status}\n"
            f"📋 <b>ID таблицы:</b> {google_sync.sheet_id or 'Не указан'}\n\n"
            f"💫 <i>Двусторонняя синхронизация работает каждые 300 секунд</i>",
            reply_markup=keyboard.as_markup(resize_keyboard=True),
            parse_mode='HTML'
        )
    else:
        await message.answer(
            "❌ <b>Синхронизация с Google Sheets не настроена</b>\n\n"
            "Для настройки необходимо:\n"
            "1. Указать GOOGLE_SHEETS_CREDENTIALS в .env файле\n"
            "2. Указать GOOGLE_SHEET_ID в .env файле\n"
            "3. Перезапустить бота",
            reply_markup=keyboard.as_markup(resize_keyboard=True),
            parse_mode='HTML'
        )

@dp.message(F.text == "🔄 Синхронизировать с Google Sheets")
async def cmd_sync_sheets(message: types.Message):
    """Ручная синхронизация с Google Sheets"""
    health_monitor.record_message(message.from_user.id)
    
    if not google_sync.is_configured():
        await message.answer(
            "❌ <b>Синхронизация не настроена</b>\n\n"
            "Проверьте настройки Google Sheets в конфигурации.",
            parse_mode='HTML'
        )
        return
    
    user_id = message.from_user.id
    materials = get_user_materials(user_id)
    
    if not materials:
        await message.answer(
            "❌ <b>Нет данных для синхронизации</b>\n\n"
            "Сначала добавьте материалы.",
            parse_mode='HTML'
        )
        return
    
    await message.answer("🔄 <b>Начинаю синхронизацию...</b>", parse_mode='HTML')
    
    success, message_text = safe_sync_to_sheets(user_id, materials)
    
    if success:
        await message.answer(
            f"✅ <b>СИНХРОНИЗАЦИЯ УСПЕШНА!</b>\n\n{message_text}",
            parse_mode='HTML'
        )
    else:
        await message.answer(
            f"❌ <b>ОШИБКА СИНХРОНИЗАЦИИ</b>\n\n{message_text}",
            parse_mode='HTML'
        )

@dp.message(F.text == "🔙 Назад")
async def cmd_back(message: types.Message, state: FSMContext):
    """Обработчик кнопки Назад"""
    health_monitor.record_message(message.from_user.id)
    await state.clear()
    await message.answer(
        "🔙 <b>Возврат в главное меню</b>",
        reply_markup=get_main_keyboard(),
        parse_mode='HTML'
    )

# ========== ОБРАБОТЧИКИ УПРАВЛЕНИЯ МАТЕРИАЛАМИ ==========

@dp.message(F.text == "✏️ Редактировать материал")
async def cmd_edit_material(message: types.Message, state: FSMContext):
    """Начало редактирования материала"""
    health_monitor.record_message(message.from_user.id)
    
    materials = get_user_materials(message.from_user.id)
    if not materials:
        await message.answer(
            "❌ <b>Нет материалов для редактирования</b>\n\n"
            "Сначала добавьте материалы через меню '✨ Добавить материал'",
            reply_markup=get_management_keyboard(),
            parse_mode='HTML'
        )
        return
    
    await show_materials_for_selection(message, materials, "редактирования")
    await state.set_state(EditMaterialStates.waiting_material_selection)

@dp.message(EditMaterialStates.waiting_material_selection)
async def process_edit_material_selection(message: types.Message, state: FSMContext):
    """Обработка выбора материала для редактирования"""
    if message.text == "🔙 Назад":
        await state.clear()
        await message.answer("🔙 Возврат в меню управления", reply_markup=get_management_keyboard())
        return
    
    # Парсим ID материала из текста (формат: "#123 - Тип материала (Местоположение)")
    match = re.search(r'#(\d+)', message.text)
    if not match:
        await message.answer(
            "❌ <b>Неверный формат выбора</b>\n\n"
            "Пожалуйста, выберите материал из списка:",
            reply_markup=get_materials_selection_keyboard(get_user_materials(message.from_user.id), "редактирования")
        )
        return
    
    material_id = int(match.group(1))
    user_id = message.from_user.id
    
    # Проверяем существование материала и права доступа
    material_data = get_material_by_id(material_id, user_id)
    if not material_data:
        await message.answer(
            "❌ <b>Материал не найден</b>\n\n"
            "Пожалуйста, выберите материал из списка:",
            reply_markup=get_materials_selection_keyboard(get_user_materials(user_id), "редактирования")
        )
        return
    
    # Сохраняем ID материала в состоянии
    await state.update_data(selected_material_id=material_id, material_data=material_data)
    
    total_cost = material_data['quantity'] * material_data['cost']
    
    await message.answer(
        f"✏️ <b>РЕДАКТИРОВАНИЕ МАТЕРИАЛА #{material_id}</b>\n\n"
        f"🏗️ <b>Текущие данные:</b>\n"
        f"• Тип: {material_data['material_type']}\n"
        f"• Местоположение: {material_data['location']}\n"
        f"• Дата заказа: {format_date_nice(datetime.strptime(str(material_data['order_date']), '%Y-%m-%d'))}\n"
        f"• Дата доставки: {format_date_nice(datetime.strptime(str(material_data['delivery_date']), '%Y-%m-%d'))}\n"
        f"• Количество: {material_data['quantity']}\n"
        f"• Стоимость за ед.: {material_data['cost']:.2f} руб.\n"
        f"• Общая стоимость: {total_cost:.2f} руб.\n\n"
        f"<b>Выберите поле для редактирования:</b>",
        reply_markup=get_edit_keyboard(),
        parse_mode='HTML'
    )
    await state.set_state(EditMaterialStates.waiting_field_selection)

@dp.message(EditMaterialStates.waiting_field_selection)
async def process_edit_field_selection(message: types.Message, state: FSMContext):
    """Обработка выбора поля для редактирования"""
    if message.text == "🔙 Назад":
        materials = get_user_materials(message.from_user.id)
        await show_materials_for_selection(message, materials, "редактирования")
        await state.set_state(EditMaterialStates.waiting_material_selection)
        return
    
    field_mapping = {
        "🏗️ Тип материала": "material_type",
        "📍 Местоположение": "location", 
        "📅 Дата заказа": "order_date",
        "🚚 Дата доставки": "delivery_date",
        "📦 Количество": "quantity",
        "💵 Стоимость": "cost"
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
    material_data = data.get('material_data', {})
    current_value = material_data.get(field_key, '')
    
    if field_key in ["order_date", "delivery_date"] and current_value:
        current_value = format_date_nice(datetime.strptime(str(current_value), '%Y-%m-%d'))
    
    prompt_texts = {
        "material_type": f"🏗️ <b>Введите новый тип материала:</b>\n\nТекущее значение: <i>{current_value}</i>",
        "location": f"📍 <b>Введите новое местоположение:</b>\n\nТекущее значение: <i>{current_value}</i>",
        "order_date": f"📅 <b>Введите новую дату заказа:</b>\n\nТекущее значение: <i>{current_value}</i>\nФормат: ДД.ММ.ГГГГ или ДД.ММ",
        "delivery_date": f"🚚 <b>Введите новую дату доставки:</b>\n\nТекущее значение: <i>{current_value}</i>\nФормат: ДД.ММ.ГГГГ или ДД.ММ",
        "quantity": f"📦 <b>Введите новое количество:</b>\n\nТекущее значение: <i>{current_value}</i>",
        "cost": f"💵 <b>Введите новую стоимость за единицу:</b>\n\nТекущее значение: <i>{current_value} руб.</i>"
    }
    
    await message.answer(
        prompt_texts[field_key],
        reply_markup=get_back_keyboard(),
        parse_mode='HTML'
    )
    await state.set_state(EditMaterialStates.waiting_new_value)

@dp.message(EditMaterialStates.waiting_new_value)
async def process_edit_new_value(message: types.Message, state: FSMContext):
    """Обработка нового значения для поля"""
    if message.text == "🔙 Назад":
        data = await state.get_data()
        material_data = data.get('material_data', {})
        material_id = data.get('selected_material_id')
        
        total_cost = material_data['quantity'] * material_data['cost']
        
        await message.answer(
            f"✏️ <b>РЕДАКТИРОВАНИЕ МАТЕРИАЛА #{material_id}</b>\n\n"
            f"🏗️ <b>Текущие данные:</b>\n"
            f"• Тип: {material_data['material_type']}\n"
            f"• Местоположение: {material_data['location']}\n"
            f"• Дата заказа: {format_date_nice(datetime.strptime(str(material_data['order_date']), '%Y-%m-%d'))}\n"
            f"• Дата доставки: {format_date_nice(datetime.strptime(str(material_data['delivery_date']), '%Y-%m-%d'))}\n"
            f"• Количество: {material_data['quantity']}\n"
            f"• Стоимость за ед.: {material_data['cost']:.2f} руб.\n"
            f"• Общая стоимость: {total_cost:.2f} руб.\n\n"
            f"<b>Выберите поле для редактирования:</b>",
            reply_markup=get_edit_keyboard(),
            parse_mode='HTML'
        )
        await state.set_state(EditMaterialStates.waiting_field_selection)
        return
    
    data = await state.get_data()
    field_key = data.get('editing_field')
    material_id = data.get('selected_material_id')
    user_id = message.from_user.id
    material_data = data.get('material_data', {})
    
    try:
        if field_key == "material_type":
            is_valid, error_msg = validate_material_type(message.text)
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
            
        elif field_key in ["order_date", "delivery_date"]:
            new_date = enhanced_validate_date(message.text)
            new_value = new_date.strftime('%Y-%m-%d')
            
        elif field_key == "quantity":
            is_valid, error_msg, quantity = validate_quantity(message.text)
            if not is_valid:
                await message.answer(f"❌ {error_msg}\n\nВведите новое значение:")
                return
            new_value = quantity
            
        elif field_key == "cost":
            is_valid, error_msg, cost = validate_cost(message.text)
            if not is_valid:
                await message.answer(f"❌ {error_msg}\n\nВведите новое значение:")
                return
            new_value = cost
        
        # Обновляем поле в БД
        success = update_material_in_db(material_id, user_id, **{field_key: new_value})
        
        if success:
            field_names = {
                "material_type": "тип материала",
                "location": "местоположение",
                "order_date": "дата заказа", 
                "delivery_date": "дата доставки",
                "quantity": "количество",
                "cost": "стоимость"
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
        logging.error(f"Ошибка при редактировании материала: {e}")
        await message.answer(
            "❌ <b>Произошла ошибка при редактировании</b>",
            reply_markup=get_management_keyboard(),
            parse_mode='HTML'
        )
        await state.clear()

@dp.message(F.text == "🗑️ Удалить материал")
async def cmd_delete_material(message: types.Message, state: FSMContext):
    """Начало удаления материала"""
    health_monitor.record_message(message.from_user.id)
    
    materials = get_user_materials(message.from_user.id)
    if not materials:
        await message.answer(
            "❌ <b>Нет материалов для удаления</b>\n\n"
            "Сначала добавьте материалы через меню '✨ Добавить материал'",
            reply_markup=get_management_keyboard(),
            parse_mode='HTML'
        )
        return
    
    await show_materials_for_selection(message, materials, "удаления")
    await state.set_state(DeleteMaterialStates.waiting_material_selection)

@dp.message(DeleteMaterialStates.waiting_material_selection)
async def process_delete_material_selection(message: types.Message, state: FSMContext):
    """Обработка выбора материала для удаления"""
    if message.text == "🔙 Назад":
        await state.clear()
        await message.answer("🔙 Возврат в меню управления", reply_markup=get_management_keyboard())
        return
    
    # Парсим ID материала из текста
    match = re.search(r'#(\d+)', message.text)
    if not match:
        await message.answer(
            "❌ <b>Неверный формат выбора</b>\n\n"
            "Пожалуйста, выберите материал из списка:",
            reply_markup=get_materials_selection_keyboard(get_user_materials(message.from_user.id), "удаления")
        )
        return
    
    material_id = int(match.group(1))
    user_id = message.from_user.id
    
    # Проверяем существование материала
    material_data = get_material_by_id(material_id, user_id)
    if not material_data:
        await message.answer(
            "❌ <b>Материал не найден</b>\n\n"
            "Пожалуйста, выберите материал из списка:",
            reply_markup=get_materials_selection_keyboard(get_user_materials(user_id), "удаления")
        )
        return
    
    # Сохраняем ID материала в состоянии
    await state.update_data(selected_material_id=material_id, material_data=material_data)
    
    delivery_date = datetime.strptime(str(material_data['delivery_date']), '%Y-%m-%d').date()
    days_until = (delivery_date - datetime.now().date()).days
    icon, status = get_status_icon_and_text(days_until)
    total_cost = material_data['quantity'] * material_data['cost']
    
    await message.answer(
        f"🗑️ <b>ПОДТВЕРЖДЕНИЕ УДАЛЕНИЯ</b>\n\n"
        f"{icon} <b>Материал #{material_id}</b>\n"
        f"🏗️ Тип: {material_data['material_type']}\n"
        f"📍 Место: {material_data['location']}\n"
        f"📦 Количество: {material_data['quantity']}\n"
        f"💵 Стоимость: {total_cost:.2f} руб.\n"
        f"📊 Статус: {status} ({days_until} дней)\n\n"
        f"<b>Вы уверены, что хотите удалить этот материал?</b>\n"
        f"<i>Это действие нельзя отменить!</i>",
        reply_markup=get_confirmation_keyboard(),
        parse_mode='HTML'
    )
    await state.set_state(DeleteMaterialStates.waiting_confirmation)

@dp.message(DeleteMaterialStates.waiting_confirmation)
async def process_delete_confirmation(message: types.Message, state: FSMContext):
    """Обработка подтверждения удаления"""
    if message.text == "✅ Да, всё верно":
        data = await state.get_data()
        material_id = data.get('selected_material_id')
        user_id = message.from_user.id
        material_data = data.get('material_data', {})
        
        # Удаляем материал из БД
        success = delete_material_from_db(material_id, user_id)
        
        if success:
            await message.answer(
                f"✅ <b>МАТЕРИАЛ УДАЛЕН!</b>\n\n"
                f"🏗️ {material_data['material_type']}\n"
                f"📍 {material_data['location']}\n\n"
                f"Материал успешно удален из базы данных.",
                reply_markup=get_management_keyboard(),
                parse_mode='HTML'
            )
        else:
            await message.answer(
                "❌ <b>Ошибка при удалении материала</b>",
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

@dp.message(F.text == "🔄 Двусторонняя синхронизация")
async def cmd_bidirectional_sync(message: types.Message):
    """Двусторонняя синхронизация с Google Sheets"""
    health_monitor.record_message(message.from_user.id)
    
    if not google_sync.is_configured():
        await message.answer(
            "❌ <b>Синхронизация не настроена</b>\n\n"
            "Проверьте настройки Google Sheets в конфигурации.",
            parse_mode='HTML'
        )
        return
    
    user_id = message.from_user.id
    
    await message.answer("🔄 <b>Запуск двусторонней синхронизации...</b>", parse_mode='HTML')
    
    # Синхронизация ИЗ бота В таблицу
    materials = get_user_materials(user_id)
    if materials:
        success_to, message_to = google_sync.sync_to_sheets(user_id, materials)
        if success_to:
            await message.answer(f"✅ <b>Данные отправлены в таблицу:</b>\n{message_to}", parse_mode='HTML')
        else:
            await message.answer(f"❌ <b>Ошибка отправки в таблицу:</b>\n{message_to}", parse_mode='HTML')
    
    # Синхронизация ИЗ таблицы В бота
    success_from, message_from = google_sync.sync_from_sheets(user_id)
    if success_from:
        await message.answer(f"✅ <b>Данные получены из таблицы:</b>\n{message_from}", parse_mode='HTML')
    else:
        await message.answer(f"❌ <b>Ошибка получения из таблицы:</b>\n{message_from}", parse_mode='HTML')

# ========== ЗАПУСК ПРИЛОЖЕНИЯ ==========
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
    reminder_thread = threading.Thread(target=send_reminders, daemon=True)
    reminder_thread.start()
    
    # Задача мониторинга здоровья
    health_thread = threading.Thread(target=health_monitoring_task, daemon=True)
    health_thread.start()
    
    # ЗАПУСК ДВУСТОРОННЕЙ СИНХРОНИЗАЦИИ
    sync_thread = threading.Thread(target=bidirectional_sync_task, daemon=True)
    sync_thread.start()
    
    logging.info("🚀 Фоновые задачи запущены (двусторонняя синхронизация: 300 секунд)")

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
        
        # УВЕДОМЛЕНИЕ О ЗАПУСКЕ АДМИНИСТРАТОРУ
        await notify_admin(
            "🚀 <b>БОТ ЗАПУЩЕН</b>\n\n"
            f"⏰ Время: {datetime.now().strftime('%d.%m.%Y %H:%M:%S')}\n"
            f"🔧 Режим: Учет строительных материалов\n"
            f"🔄 Интервал: 300 секунд\n"
            f"💾 База данных: сохранение при перезагрузке\n"
            f"🔄 Двусторонняя синхронизация: активирована\n"
            f"💰 Учет зарплаты: отключен"
        )
        
        # Расширенная диагностика
        logging.info("=== ЗАПУСК РАСШИРЕННОЙ ДИАГНОСТИКИ ===")
        
        # 1. Проверка прав
        if not check_database_permissions():
            logging.error("❌ Проблемы с правами доступа к БД")
        
        # 2. Диагностика подключения
        if not debug_database_connection():
            logging.error("❌ Проблемы с подключением к БД")
        
        # 3. Инициализация БД
        init_db()
        
        # 4. Финальная проверка
        debug_database_connection()
        
        logging.info("=== ДИАГНОСТИКА ЗАВЕРШЕНА ===")
        
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
        logging.info("🤖 Бот успешно запущен с улучшенной автосинхронизацией (300 секунд)!")
        
        # Запуск бота
        await dp.start_polling(bot)
        
    except Exception as e:
        logging.critical(f"Критическая ошибка при запуске: {e}")
        
        # УВЕДОМЛЕНИЕ ОБ ОШИБКЕ АДМИНИСТРАТОРУ
        await notify_admin(
            f"💥 <b>БОТ УПАЛ С ОШИБКОЙ</b>\n\n"
            f"⏰ Время: {datetime.now().strftime('%d.%m.%Y %H:%M:%S')}\n"
            f"💥 Ошибка: {str(e)[:1000]}"
        )
        raise

if __name__ == "__main__":
    try:
        asyncio.run(enhanced_main())
    except KeyboardInterrupt:
        logging.info("Бот остановлен пользователем")
    except Exception as e:
        logging.critical(f"Фатальная ошибка: {e}")
        sys.exit(1)
