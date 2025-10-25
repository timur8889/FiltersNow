import logging
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
