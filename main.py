import logging
import sqlite3
import os
import asyncio
import shutil
import traceback
import re
import sys
import pandas as pd
import openpyxl
from datetime import datetime, timedelta
from contextlib import contextmanager
from aiogram import Bot, Dispatcher, types
from aiogram.contrib.fsm_storage.memory import MemoryStorage
from aiogram.dispatcher import FSMContext
from aiogram.dispatcher.filters.state import State, StatesGroup
from aiogram.utils import executor
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

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('bot.log', encoding='utf-8'),
        logging.StreamHandler(sys.stdout)
    ]
)

# Стандартные сроки службы фильтров
DEFAULT_LIFETIMES = {
    "магистральный sl10": 180,
    "магистральный sl20": 180,
    "гейзер": 365,
    "аквафор": 365,
    "механический": 90,
    "угольный": 180,
    "минеральный": 365,
    "ультрафильтрация": 365,
    "обратный осмос": 365
}

# Ограничения
MAX_FILTERS_PER_USER = 50

# Инициализация бота
bot = Bot(token=API_TOKEN)
storage = MemoryStorage()
dp = Dispatcher(bot, storage=storage)

# ========== БАЗА ДАННЫХ ==========
@contextmanager
def get_db_connection():
    """Контекстный менеджер для безопасной работы с БД"""
    conn = sqlite3.connect('filters.db')
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    except Exception as e:
        conn.rollback()
        raise e
    finally:
        conn.close()

def init_db():
    """Инициализация базы данных"""
    try:
        with get_db_connection() as conn:
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
    except Exception as e:
        logging.error(f"Ошибка инициализации БД: {e}")

def get_user_filters(user_id):
    """Получение фильтров пользователя"""
    with get_db_connection() as conn:
        cur = conn.cursor()
        cur.execute("SELECT * FROM filters WHERE user_id = ? ORDER BY expiry_date", (user_id,))
        return [dict(row) for row in cur.fetchall()]

def get_filter_by_id(filter_id, user_id):
    """Получение фильтра по ID"""
    with get_db_connection() as conn:
        cur = conn.cursor()
        cur.execute("SELECT * FROM filters WHERE id = ? AND user_id = ?", (filter_id, user_id))
        result = cur.fetchone()
        return dict(result) if result else None

def check_filters_limit(user_id):
    """Проверка лимита фильтров"""
    filters = get_user_filters(user_id)
    return len(filters) >= MAX_FILTERS_PER_USER

# ========== ВАЛИДАЦИЯ ==========
def validate_date(date_str: str):
    """Валидация даты"""
    date_str = date_str.strip()
    formats = ['%d.%m.%y', '%d.%m.%Y', '%d-%m-%y', '%d-%m-%Y', '%d/%m/%y', '%d/%m/%Y']
    
    for fmt in formats:
        try:
            date_obj = datetime.strptime(date_str, fmt).date()
            today = datetime.now().date()
            
            if date_obj > today + timedelta(days=1):
                raise ValueError("Дата не может быть в будущем")
                
            return date_obj
        except ValueError:
            continue
    
    raise ValueError("Неверный формат даты. Используйте ДД.ММ.ГГ или ДД.ММ.ГГГГ")

def validate_lifetime(days_str: str):
    """Валидация срока службы"""
    try:
        days = int(days_str)
        if days <= 0:
            raise ValueError("Срок службы должен быть положительным числом")
        if days > 2000:
            raise ValueError("Слишком большой срок службы")
        return days
    except ValueError:
        raise ValueError("Введите корректное число дней")

def safe_db_string(value: str) -> str:
    """Очистка строки для БД"""
    if not value:
        return ""
    return re.sub(r'[;\'"\\]', '', value.strip())

# ========== ОБНОВЛЕННЫЕ КЛАВИАТУРЫ ==========
def get_main_keyboard(user_id=None):
    """Главное меню с Excel кнопками"""
    keyboard = types.ReplyKeyboardMarkup(resize_keyboard=True)
    keyboard.row(
        types.KeyboardButton("📊 Мои фильтры"),
        types.KeyboardButton("➕ Добавить фильтр")
    )
    keyboard.row(
        types.KeyboardButton("⏰ Сроки замены"),
        types.KeyboardButton("⚙️ Управление")
    )
    keyboard.row(
        types.KeyboardButton("📤 Экспорт Excel"),
        types.KeyboardButton("📥 Импорт Excel")
    )
    if user_id and str(user_id) == str(ADMIN_ID):
        keyboard.row(types.KeyboardButton("👑 Админ"))
    return keyboard

def get_management_keyboard():
    """Меню управления"""
    keyboard = types.ReplyKeyboardMarkup(resize_keyboard=True)
    keyboard.row(
        types.KeyboardButton("✏️ Редактировать фильтр"),
        types.KeyboardButton("🗑️ Удалить фильтр")
    )
    keyboard.row(
        types.KeyboardButton("📈 Статистика"),
        types.KeyboardButton("🏠 Главное меню")
    )
    return keyboard

def get_filter_type_keyboard():
    """Выбор типа фильтра"""
    keyboard = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    keyboard.row(
        types.KeyboardButton("🔧 Магистральный SL10"),
        types.KeyboardButton("🔧 Магистральный SL20")
    )
    keyboard.row(
        types.KeyboardButton("💧 Гейзер"),
        types.KeyboardButton("💧 Аквафор")
    )
    keyboard.row(
        types.KeyboardButton("⚙️ Механический"),
        types.KeyboardButton("🔮 Угольный")
    )
    keyboard.row(
        types.KeyboardButton("💎 Минеральный"),
        types.KeyboardButton("🌀 Обратный осмос")
    )
    keyboard.row(types.KeyboardButton("📝 Другой тип"))
    keyboard.row(types.KeyboardButton("🏠 Главное меню"))
    return keyboard

def get_cancel_keyboard():
    """Клавиатура отмены"""
    keyboard = types.ReplyKeyboardMarkup(resize_keyboard=True)
    keyboard.add(types.KeyboardButton("❌ Отмена"))
    return keyboard

def get_back_keyboard():
    """Клавиатура назад"""
    keyboard = types.ReplyKeyboardMarkup(resize_keyboard=True)
    keyboard.add(types.KeyboardButton("↩️ Назад"))
    return keyboard

def get_lifetime_keyboard():
    """Выбор срока службы"""
    keyboard = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=3)
    keyboard.row(
        types.KeyboardButton("90 дней"),
        types.KeyboardButton("180 дней"),
        types.KeyboardButton("365 дней")
    )
    keyboard.row(types.KeyboardButton("📅 Другое количество"))
    keyboard.row(types.KeyboardButton("🏠 Главное меню"))
    return keyboard

def get_edit_keyboard():
    """Клавиатура редактирования"""
    keyboard = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    keyboard.row(
        types.KeyboardButton("✏️ Тип фильтра"),
        types.KeyboardButton("📍 Место установки")
    )
    keyboard.row(
        types.KeyboardButton("📅 Дата замены"),
        types.KeyboardButton("⏱️ Срок службы")
    )
    keyboard.row(types.KeyboardButton("🏠 Главное меню"))
    return keyboard

def get_confirmation_keyboard(filter_id=None, action="delete"):
    """Клавиатура подтверждения"""
    keyboard = types.InlineKeyboardMarkup(row_width=2)
    if action == "delete":
        keyboard.add(
            types.InlineKeyboardButton("✅ Да, удалить", callback_data=f"confirm_delete_{filter_id}"),
            types.InlineKeyboardButton("❌ Нет, отменить", callback_data="cancel_delete")
        )
    return keyboard

def get_filters_list_keyboard(filters, action="edit"):
    """Список фильтров для выбора"""
    keyboard = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=1)
    
    for f in filters:
        expiry_date = datetime.strptime(str(f['expiry_date']), '%Y-%m-%d').date()
        days_left = (expiry_date - datetime.now().date()).days
        
        if days_left <= 0:
            icon = "🔴"
        elif days_left <= 7:
            icon = "🟡"
        elif days_left <= 30:
            icon = "🟠"
        else:
            icon = "✅"
            
        button_text = f"{icon} {f['filter_type']} - {f['location']}"
        keyboard.add(types.KeyboardButton(button_text))
    
    keyboard.row(types.KeyboardButton("🏠 Главное меню"))
    return keyboard

# ========== STATES ==========
class FilterStates(StatesGroup):
    waiting_filter_type = State()
    waiting_location = State()
    waiting_change_date = State()
    waiting_lifetime = State()

class EditFilterStates(StatesGroup):
    waiting_filter_selection = State()
    waiting_field_selection = State()
    waiting_new_value = State()

class DeleteFilterStates(StatesGroup):
    waiting_filter_selection = State()

class ExcelStates(StatesGroup):
    waiting_excel_file = State()

# ========== ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ==========
def format_date_nice(date):
    return date.strftime('%d.%m.%Y')

def get_lifetime_by_type(filter_type):
    filter_type_lower = filter_type.lower()
    for key, days in DEFAULT_LIFETIMES.items():
        if key in filter_type_lower:
            return days
    return 180

def get_status_icon(days_until_expiry):
    """Получение иконки статуса"""
    if days_until_expiry <= 0:
        return "🔴"
    elif days_until_expiry <= 7:
        return "🟡"
    elif days_until_expiry <= 30:
        return "🟠"
    else:
        return "✅"

def create_statistics_message(filters):
    """Создание графической статистики"""
    if not filters:
        return "📊 <b>Статистика</b>\n\n📭 Нет данных для отображения"
    
    total = len(filters)
    expired = 0
    urgent = 0
    warning = 0
    normal = 0
    
    for f in filters:
        expiry_date = datetime.strptime(str(f['expiry_date']), '%Y-%m-%d').date()
        days_left = (expiry_date - datetime.now().date()).days
        
        if days_left <= 0:
            expired += 1
        elif days_left <= 7:
            urgent += 1
        elif days_left <= 30:
            warning += 1
        else:
            normal += 1
    
    # Создаем графические прогресс-бары
    def create_bar(percentage, icon):
        bars = int(percentage / 10)
        return icon * bars + " " * (10 - bars)
    
    expired_pct = (expired / total) * 100
    urgent_pct = (urgent / total) * 100
    warning_pct = (warning / total) * 100
    normal_pct = (normal / total) * 100
    
    message = (
        "📊 <b>СТАТИСТИКА ФИЛЬТРОВ</b>\n\n"
        f"🔧 Всего фильтров: <b>{total}</b>\n\n"
        f"📈 <b>Состояние фильтров:</b>\n"
        f"🔴 Просрочено: {expired} ({expired_pct:.1f}%)\n{create_bar(expired_pct, '🔴')}\n\n"
        f"🟡 Срочно заменить (1-7 дн.): {urgent} ({urgent_pct:.1f}%)\n{create_bar(urgent_pct, '🟡')}\n\n"
        f"🟠 Скоро заменить (8-30 дн.): {warning} ({warning_pct:.1f}%)\n{create_bar(warning_pct, '🟠')}\n\n"
        f"✅ В норме (>30 дн.): {normal} ({normal_pct:.1f}%)\n{create_bar(normal_pct, '✅')}\n\n"
        f"📅 <b>Ближайшая замена:</b>\n"
    )
    
    # Добавляем ближайшие замены
    soon_filters = sorted(filters, key=lambda x: datetime.strptime(str(x['expiry_date']), '%Y-%m-%d').date())[:3]
    for i, f in enumerate(soon_filters, 1):
        expiry_date = datetime.strptime(str(f['expiry_date']), '%Y-%m-%d').date()
        days_left = (expiry_date - datetime.now().date()).days
        icon = get_status_icon(days_left)
        message += f"{icon} {f['filter_type']} - {days_left} дн.\n"
    
    return message

# ========== ОСНОВНЫЕ ОБРАБОТЧИКИ ==========
@dp.message_handler(commands=['start'])
async def cmd_start(message: types.Message):
    await message.answer(
        "🤖 <b>Фильтр-Трекер</b>\n\n"
        "💧 Умный помощник для контроля замены фильтров\n\n"
        "📋 <b>Возможности:</b>\n"
        "• 📊 Просмотр всех фильтров\n"
        "• ➕ Добавление новых фильтров\n"
        "• ⏰ Контроль сроков замены\n"
        "• ✏️ Редактирование данных\n"
        "• 📈 Детальная статистика\n"
        "• 📤📥 Импорт/экспорт в Excel",
        parse_mode='HTML',
        reply_markup=get_main_keyboard(message.from_user.id)
    )

@dp.message_handler(lambda message: message.text == "🏠 Главное меню")
async def cmd_main_menu(message: types.Message):
    await message.answer(
        "🏠 <b>Главное меню</b>\n\nВыберите действие:",
        parse_mode='HTML',
        reply_markup=get_main_keyboard(message.from_user.id)
    )

# ========== ДОБАВЛЕНИЕ ФИЛЬТРА ==========
@dp.message_handler(lambda message: message.text == "➕ Добавить фильтр")
async def cmd_add_filter(message: types.Message):
    if check_filters_limit(message.from_user.id):
        await message.answer(
            f"❌ <b>Достигнут лимит фильтров!</b>\n\n"
            f"Максимум: {MAX_FILTERS_PER_USER} фильтров\n"
            f"Удалите некоторые фильтры перед добавлением новых",
            parse_mode='HTML',
            reply_markup=get_main_keyboard(message.from_user.id)
        )
        return
        
    await FilterStates.waiting_filter_type.set()
    await message.answer(
        "🔧 <b>Выберите тип фильтра:</b>",
        parse_mode='HTML',
        reply_markup=get_filter_type_keyboard()
    )

@dp.message_handler(state=FilterStates.waiting_filter_type)
async def process_filter_type(message: types.Message, state: FSMContext):
    if message.text == "🏠 Главное меню":
        await state.finish()
        await cmd_main_menu(message)
        return
        
    filter_mapping = {
        "🔧 Магистральный SL10": "Магистральный SL10",
        "🔧 Магистральный SL20": "Магистральный SL20",
        "💧 Гейзер": "Гейзер",
        "💧 Аквафор": "Аквафор",
        "⚙️ Механический": "Механический",
        "🔮 Угольный": "Угольный",
        "💎 Минеральный": "Минеральный",
        "🌀 Обратный осмос": "Обратный осмос"
    }
    
    if message.text in filter_mapping:
        filter_type = filter_mapping[message.text]
    elif message.text == "📝 Другой тип":
        await message.answer(
            "📝 <b>Введите название фильтра:</b>",
            parse_mode='HTML',
            reply_markup=get_cancel_keyboard()
        )
        return
    else:
        filter_type = safe_db_string(message.text)
    
    async with state.proxy() as data:
        data['filter_type'] = filter_type
    
    await FilterStates.waiting_location.set()
    await message.answer(
        f"🔧 <b>Тип фильтра:</b> {filter_type}\n\n"
        f"📍 <b>Введите место установки:</b>\n\n"
        f"Пример: Кухня, Ванная, Офис и т.д.",
        parse_mode='HTML',
        reply_markup=get_cancel_keyboard()
    )

@dp.message_handler(state=FilterStates.waiting_location)
async def process_location(message: types.Message, state: FSMContext):
    if message.text == "❌ Отмена":
        await state.finish()
        await message.answer("❌ Добавление отменено", reply_markup=get_main_keyboard(message.from_user.id))
        return
        
    location = safe_db_string(message.text)
    
    async with state.proxy() as data:
        data['location'] = location
    
    await FilterStates.waiting_change_date.set()
    await message.answer(
        f"📍 <b>Место установки:</b> {location}\n\n"
        f"📅 <b>Введите дату последней замены:</b>\n\n"
        f"Формат: <i>ДД.ММ.ГГГГ</i>\n"
        f"Пример: <i>15.01.2024</i>",
        parse_mode='HTML',
        reply_markup=get_cancel_keyboard()
    )

@dp.message_handler(state=FilterStates.waiting_change_date)
async def process_change_date(message: types.Message, state: FSMContext):
    if message.text == "❌ Отмена":
        await state.finish()
        await message.answer("❌ Добавление отменено", reply_markup=get_main_keyboard(message.from_user.id))
        return
        
    try:
        change_date = validate_date(message.text)
        
        async with state.proxy() as data:
            data['change_date'] = change_date
            filter_type = data['filter_type']
            
            # Автоматически определяем срок службы по типу фильтра
            auto_lifetime = get_lifetime_by_type(filter_type)
            data['lifetime_days'] = auto_lifetime
        
        await FilterStates.waiting_lifetime.set()
        await message.answer(
            f"📅 <b>Дата замены:</b> {format_date_nice(change_date)}\n\n"
            f"⏱️ <b>Срок службы:</b> {auto_lifetime} дней\n\n"
            f"✅ <i>Срок установлен автоматически</i>\n"
            f"🔄 <b>Хотите изменить срок службы?</b>",
            parse_mode='HTML',
            reply_markup=get_lifetime_keyboard()
        )
        
    except ValueError as e:
        await message.answer(
            f"❌ <b>Ошибка в дате:</b>\n\n{str(e)}",
            parse_mode='HTML'
        )

@dp.message_handler(state=FilterStates.waiting_lifetime)
async def process_lifetime(message: types.Message, state: FSMContext):
    if message.text == "🏠 Главное меню":
        await state.finish()
        await cmd_main_menu(message)
        return
        
    try:
        lifetime_mapping = {
            "90 дней": 90,
            "180 дней": 180,
            "365 дней": 365
        }
        
        if message.text in lifetime_mapping:
            lifetime_days = lifetime_mapping[message.text]
        elif message.text == "📅 Другое количество":
            await message.answer(
                "⏱️ <b>Введите количество дней:</b>\n\nПример: 120",
                parse_mode='HTML',
                reply_markup=get_cancel_keyboard()
            )
            return
        else:
            lifetime_days = validate_lifetime(message.text)
        
        async with state.proxy() as data:
            filter_type = data['filter_type']
            location = data['location']
            change_date = data['change_date']
            
            # Сохраняем в БД
            expiry_date = change_date + timedelta(days=lifetime_days)
            
            with get_db_connection() as conn:
                cur = conn.cursor()
                cur.execute('''INSERT INTO filters 
                            (user_id, filter_type, location, last_change, expiry_date, lifetime_days) 
                            VALUES (?, ?, ?, ?, ?, ?)''',
                            (message.from_user.id, filter_type, location, change_date, expiry_date, lifetime_days))
                conn.commit()
        
        await state.finish()
        
        await message.answer(
            f"✅ <b>ФИЛЬТР ДОБАВЛЕН!</b>\n\n"
            f"🔧 <b>Тип:</b> {filter_type}\n"
            f"📍 <b>Место:</b> {location}\n"
            f"📅 <b>Дата замены:</b> {format_date_nice(change_date)}\n"
            f"⏱️ <b>Срок службы:</b> {lifetime_days} дней\n"
            f"📅 <b>Следующая замена:</b> {format_date_nice(expiry_date)}\n\n"
            f"💡 <i>Фильтр будет автоматически отслеживаться</i>",
            parse_mode='HTML',
            reply_markup=get_main_keyboard(message.from_user.id)
        )
        
    except ValueError as e:
        await message.answer(
            f"❌ <b>Ошибка в сроке службы:</b>\n\n{str(e)}",
            parse_mode='HTML'
        )

# ========== УПРАВЛЕНИЕ ФИЛЬТРАМИ ==========
@dp.message_handler(lambda message: message.text == "⚙️ Управление")
async def cmd_management(message: types.Message):
    await message.answer(
        "⚙️ <b>Управление фильтрами</b>\n\nВыберите действие:",
        parse_mode='HTML',
        reply_markup=get_management_keyboard()
    )

# ========== РЕДАКТИРОВАНИЕ ФИЛЬТРА ==========
@dp.message_handler(lambda message: message.text == "✏️ Редактировать фильтр")
async def cmd_edit_filter(message: types.Message):
    filters = get_user_filters(message.from_user.id)
    
    if not filters:
        await message.answer(
            "📭 <b>Нет фильтров для редактирования</b>",
            parse_mode='HTML',
            reply_markup=get_main_keyboard(message.from_user.id)
        )
        return
    
    await EditFilterStates.waiting_filter_selection.set()
    await message.answer(
        "✏️ <b>Выберите фильтр для редактирования:</b>",
        parse_mode='HTML',
        reply_markup=get_filters_list_keyboard(filters, "edit")
    )

@dp.message_handler(state=EditFilterStates.waiting_filter_selection)
async def process_edit_filter_selection(message: types.Message, state: FSMContext):
    if message.text == "🏠 Главное меню":
        await state.finish()
        await cmd_main_menu(message)
        return
    
    filters = get_user_filters(message.from_user.id)
    selected_filter = None
    
    # Ищем выбранный фильтр
    for f in filters:
        display_text = f"{get_status_icon((datetime.strptime(str(f['expiry_date']), '%Y-%m-%d').date() - datetime.now().date()).days)} {f['filter_type']} - {f['location']}"
        if message.text == display_text:
            selected_filter = f
            break
    
    if not selected_filter:
        await message.answer(
            "❌ <b>Фильтр не найден</b>\n\nПожалуйста, выберите фильтр из списка:",
            parse_mode='HTML',
            reply_markup=get_filters_list_keyboard(filters, "edit")
        )
        return
    
    async with state.proxy() as data:
        data['editing_filter'] = selected_filter
    
    await EditFilterStates.waiting_field_selection.set()
    
    expiry_date = datetime.strptime(str(selected_filter['expiry_date']), '%Y-%m-%d').date()
    days_left = (expiry_date - datetime.now().date()).days
    
    await message.answer(
        f"✏️ <b>Редактирование фильтра</b>\n\n"
        f"🔧 <b>Тип:</b> {selected_filter['filter_type']}\n"
        f"📍 <b>Место:</b> {selected_filter['location']}\n"
        f"📅 <b>Дата замены:</b> {format_date_nice(datetime.strptime(str(selected_filter['last_change']), '%Y-%m-%d').date())}\n"
        f"⏱️ <b>Срок службы:</b> {selected_filter['lifetime_days']} дней\n"
        f"📅 <b>Годен до:</b> {format_date_nice(expiry_date)}\n"
        f"⏰ <b>Осталось дней:</b> {days_left}\n\n"
        f"<b>Что хотите изменить?</b>",
        parse_mode='HTML',
        reply_markup=get_edit_keyboard()
    )

@dp.message_handler(state=EditFilterStates.waiting_field_selection)
async def process_edit_field_selection(message: types.Message, state: FSMContext):
    if message.text == "🏠 Главное меню":
        await state.finish()
        await cmd_main_menu(message)
        return
    
    field_mapping = {
        "✏️ Тип фильтра": "filter_type",
        "📍 Место установки": "location", 
        "📅 Дата замены": "last_change",
        "⏱️ Срок службы": "lifetime_days"
    }
    
    if message.text not in field_mapping:
        await message.answer(
            "❌ <b>Пожалуйста, выберите поле для редактирования из списка:</b>",
            parse_mode='HTML',
            reply_markup=get_edit_keyboard()
        )
        return
    
    async with state.proxy() as data:
        data['editing_field'] = field_mapping[message.text]
    
    await EditFilterStates.waiting_new_value.set()
    
    field_prompts = {
        "filter_type": "✏️ <b>Введите новый тип фильтра:</b>",
        "location": "📍 <b>Введите новое место установки:</b>",
        "last_change": "📅 <b>Введите новую дату замены (ДД.ММ.ГГГГ):</b>",
        "lifetime_days": "⏱️ <b>Введите новый срок службы в днях:</b>"
    }
    
    await message.answer(
        field_prompts[field_mapping[message.text]],
        parse_mode='HTML',
        reply_markup=get_cancel_keyboard()
    )

@dp.message_handler(state=EditFilterStates.waiting_new_value)
async def process_edit_new_value(message: types.Message, state: FSMContext):
    if message.text == "❌ Отмена":
        await state.finish()
        await message.answer("❌ Редактирование отменено", reply_markup=get_main_keyboard(message.from_user.id))
        return
    
    try:
        async with state.proxy() as data:
            filter_data = data['editing_filter']
            field = data['editing_field']
            
            # Валидация в зависимости от поля
            if field == "last_change":
                new_value = validate_date(message.text)
            elif field == "lifetime_days":
                new_value = validate_lifetime(message.text)
            else:
                new_value = safe_db_string(message.text)
            
            # Обновляем в БД
            with get_db_connection() as conn:
                cur = conn.cursor()
                
                if field == "last_change":
                    # При изменении даты замены пересчитываем дату истечения
                    cur.execute('''UPDATE filters SET last_change = ?, expiry_date = date(?, '+' || lifetime_days || ' days') 
                                WHERE id = ? AND user_id = ?''',
                                (new_value, new_value, filter_data['id'], message.from_user.id))
                elif field == "lifetime_days":
                    # При изменении срока службы пересчитываем дату истечения
                    cur.execute('''UPDATE filters SET lifetime_days = ?, expiry_date = date(last_change, '+' || ? || ' days') 
                                WHERE id = ? AND user_id = ?''',
                                (new_value, new_value, filter_data['id'], message.from_user.id))
                else:
                    cur.execute(f'UPDATE filters SET {field} = ? WHERE id = ? AND user_id = ?',
                                (new_value, filter_data['id'], message.from_user.id))
                
                conn.commit()
        
        await state.finish()
        await message.answer(
            f"✅ <b>ФИЛЬТР ОБНОВЛЕН!</b>\n\n"
            f"🔄 <b>Изменено поле:</b> {field}\n"
            f"📝 <b>Новое значение:</b> {new_value}",
            parse_mode='HTML',
            reply_markup=get_main_keyboard(message.from_user.id)
        )
        
    except ValueError as e:
        await message.answer(
            f"❌ <b>Ошибка в данных:</b>\n\n{str(e)}",
            parse_mode='HTML'
        )

# ========== УДАЛЕНИЕ ФИЛЬТРА ==========
@dp.message_handler(lambda message: message.text == "🗑️ Удалить фильтр")
async def cmd_delete_filter(message: types.Message):
    filters = get_user_filters(message.from_user.id)
    
    if not filters:
        await message.answer(
            "📭 <b>Нет фильтров для удаления</b>",
            parse_mode='HTML',
            reply_markup=get_main_keyboard(message.from_user.id)
        )
        return
    
    await DeleteFilterStates.waiting_filter_selection.set()
    await message.answer(
        "🗑️ <b>Выберите фильтр для удаления:</b>",
        parse_mode='HTML',
        reply_markup=get_filters_list_keyboard(filters, "delete")
    )

@dp.message_handler(state=DeleteFilterStates.waiting_filter_selection)
async def process_delete_filter_selection(message: types.Message, state: FSMContext):
    if message.text == "🏠 Главное меню":
        await state.finish()
        await cmd_main_menu(message)
        return
    
    filters = get_user_filters(message.from_user.id)
    selected_filter = None
    
    # Ищем выбранный фильтр
    for f in filters:
        display_text = f"{get_status_icon((datetime.strptime(str(f['expiry_date']), '%Y-%m-%d').date() - datetime.now().date()).days)} {f['filter_type']} - {f['location']}"
        if message.text == display_text:
            selected_filter = f
            break
    
    if not selected_filter:
        await message.answer(
            "❌ <b>Фильтр не найден</b>\n\nПожалуйста, выберите фильтр из списка:",
            parse_mode='HTML',
            reply_markup=get_filters_list_keyboard(filters, "delete")
        )
        return
    
    expiry_date = datetime.strptime(str(selected_filter['expiry_date']), '%Y-%m-%d').date()
    days_left = (expiry_date - datetime.now().date()).days
    
    await message.answer(
        f"🗑️ <b>ПОДТВЕРЖДЕНИЕ УДАЛЕНИЯ</b>\n\n"
        f"🔧 <b>Тип:</b> {selected_filter['filter_type']}\n"
        f"📍 <b>Место:</b> {selected_filter['location']}\n"
        f"📅 <b>Годен до:</b> {format_date_nice(expiry_date)}\n"
        f"⏰ <b>Осталось дней:</b> {days_left}\n\n"
        f"<b>Вы уверены, что хотите удалить этот фильтр?</b>",
        parse_mode='HTML',
        reply_markup=get_confirmation_keyboard(selected_filter['id'])
    )
    await state.finish()

@dp.callback_query_handler(lambda c: c.data.startswith('confirm_delete_'))
async def process_confirm_delete(callback_query: types.CallbackQuery):
    filter_id = int(callback_query.data.split('_')[-1])
    
    with get_db_connection() as conn:
        cur = conn.cursor()
        cur.execute("DELETE FROM filters WHERE id = ?", (filter_id,))
        conn.commit()
    
    await callback_query.message.edit_text(
        "✅ <b>Фильтр успешно удален</b>",
        parse_mode='HTML'
    )
    await callback_query.answer()

@dp.callback_query_handler(lambda c: c.data == 'cancel_delete')
async def process_cancel_delete(callback_query: types.CallbackQuery):
    await callback_query.message.edit_text(
        "❌ <b>Удаление отменено</b>",
        parse_mode='HTML'
    )
    await callback_query.answer()

# ========== СТАТИСТИКА ==========
@dp.message_handler(lambda message: message.text == "📈 Статистика")
async def cmd_statistics(message: types.Message):
    filters = get_user_filters(message.from_user.id)
    
    if not filters:
        await message.answer(
            "📭 <b>Нет данных для статистики</b>\n\nДобавьте фильтры для просмотра статистики",
            parse_mode='HTML',
            reply_markup=get_main_keyboard(message.from_user.id)
        )
        return
    
    stats_message = create_statistics_message(filters)
    await message.answer(stats_message, parse_mode='HTML')

# ========== ПРОСМОТР ФИЛЬТРОВ ==========
@dp.message_handler(lambda message: message.text == "📊 Мои фильтры")
async def cmd_list_filters(message: types.Message):
    filters = get_user_filters(message.from_user.id)
    
    if not filters:
        await message.answer(
            "📭 <b>У вас пока нет фильтров</b>\n\n"
            "💫 Добавьте первый фильтр с помощью кнопки '➕ Добавить фильтр'",
            parse_mode='HTML',
            reply_markup=get_main_keyboard(message.from_user.id)
        )
        return
    
    response = "🔧 <b>ВАШИ ФИЛЬТРЫ</b>\n\n"
    
    for i, f in enumerate(filters, 1):
        expiry_date = datetime.strptime(str(f['expiry_date']), '%Y-%m-%d').date()
        days_until_expiry = (expiry_date - datetime.now().date()).days
        
        icon = get_status_icon(days_until_expiry)
        
        response += (
            f"{icon} <b>Фильтр #{f['id']}</b>\n"
            f"🔧 {f['filter_type']}\n"
            f"📍 {f['location']}\n"
            f"📅 Замена: {format_date_nice(datetime.strptime(str(f['last_change']), '%Y-%m-%d').date())}\n"
            f"⏱️ Срок: {f['lifetime_days']} дней\n"
            f"📅 Годен до: {format_date_nice(expiry_date)}\n"
            f"⏰ Осталось: {days_until_expiry} дней\n\n"
        )
    
    await message.answer(response, parse_mode='HTML')

@dp.message_handler(lambda message: message.text == "⏰ Сроки замены")
async def cmd_expiry_dates(message: types.Message):
    filters = get_user_filters(message.from_user.id)
    
    if not filters:
        await message.answer(
            "📭 <b>У вас пока нет фильтров</b>",
            parse_mode='HTML',
            reply_markup=get_main_keyboard(message.from_user.id)
        )
        return
    
    urgent_filters = []
    warning_filters = []
    
    for f in filters:
        expiry_date = datetime.strptime(str(f['expiry_date']), '%Y-%m-%d').date()
        days_until_expiry = (expiry_date - datetime.now().date()).days
        
        if days_until_expiry <= 7:
            urgent_filters.append((f, days_until_expiry))
        elif days_until_expiry <= 30:
            warning_filters.append((f, days_until_expiry))
    
    response = "⏰ <b>СРОКИ ЗАМЕНЫ ФИЛЬТРОВ</b>\n\n"
    
    if urgent_filters:
        response += "🔴 <b>СРОЧНО ЗАМЕНИТЬ (до 7 дней):</b>\n"
        for f, days in urgent_filters:
            response += f"• {f['filter_type']} - {f['location']} ({days} дн.)\n"
        response += "\n"
    
    if warning_filters:
        response += "🟠 <b>СКОРО ЗАМЕНИТЬ (до 30 дней):</b>\n"
        for f, days in warning_filters:
            response += f"• {f['filter_type']} - {f['location']} ({days} дн.)\n"
    
    if not urgent_filters and not warning_filters:
        response += "✅ <b>Все фильтры в норме</b>\n\nСледующая замена более чем через 30 дней"
    
    await message.answer(response, parse_mode='HTML')

# ========== EXCEL ФУНКЦИОНАЛ ==========
@dp.message_handler(lambda message: message.text == "📤 Экспорт Excel")
async def cmd_export_excel(message: types.Message):
    filters = get_user_filters(message.from_user.id)
    
    if not filters:
        await message.answer(
            "📭 <b>Нет фильтров для экспорта</b>",
            parse_mode='HTML',
            reply_markup=get_main_keyboard(message.from_user.id)
        )
        return
    
    try:
        # Создаем DataFrame
        data = []
        for f in filters:
            expiry_date = datetime.strptime(str(f['expiry_date']), '%Y-%m-%d').date()
            days_left = (expiry_date - datetime.now().date()).days
            
            data.append({
                'ID': f['id'],
                'Тип фильтра': f['filter_type'],
                'Место установки': f['location'],
                'Дата замены': f['last_change'],
                'Срок службы (дни)': f['lifetime_days'],
                'Годен до': f['expiry_date'],
                'Осталось дней': days_left,
                'Статус': 'Срочно заменить' if days_left <= 7 else 'Скоро заменить' if days_left <= 30 else 'В норме'
            })
        
        df = pd.DataFrame(data)
        
        # Создаем файл
        filename = f"filters_export_{message.from_user.id}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"
        os.makedirs('exports', exist_ok=True)
        filepath = os.path.join('exports', filename)
        
        df.to_excel(filepath, index=False, engine='openpyxl')
        
        # Отправляем файл
        with open(filepath, 'rb') as file:
            await message.answer_document(
                file,
                caption="📤 <b>ЭКСПОРТ ФИЛЬТРОВ</b>\n\n"
                       f"✅ Экспортировано: {len(filters)} фильтров\n"
                       f"📅 Дата: {datetime.now().strftime('%d.%m.%Y %H:%M')}",
                parse_mode='HTML'
            )
        
        # Удаляем временный файл
        try:
            os.remove(filepath)
        except:
            pass
            
    except Exception as e:
        await message.answer(
            f"❌ <b>Ошибка при экспорте:</b>\n\n{str(e)}",
            parse_mode='HTML'
        )

@dp.message_handler(lambda message: message.text == "📥 Импорт Excel")
async def cmd_import_excel(message: types.Message):
    await ExcelStates.waiting_excel_file.set()
    await message.answer(
        "📥 <b>ИМПОРТ ИЗ EXCEL</b>\n\n"
        "📎 <b>Отправьте Excel файл со следующими колонками:</b>\n"
        "• Тип фильтра\n"
        "• Место установки\n"
        "• Дата замены (ДД.ММ.ГГГГ)\n"
        "• Срок службы (дни)\n\n"
        "💡 <i>Файл должен быть в формате .xlsx</i>",
        parse_mode='HTML',
        reply_markup=get_cancel_keyboard()
    )

@dp.message_handler(content_types=types.ContentType.DOCUMENT, state=ExcelStates.waiting_excel_file)
async def process_excel_file(message: types.Message, state: FSMContext):
    if message.text == "❌ Отмена":
        await state.finish()
        await message.answer("❌ Импорт отменен", reply_markup=get_main_keyboard(message.from_user.id))
        return
    
    try:
        if not message.document.file_name.endswith(('.xlsx', '.xls')):
            await message.answer("❌ Поддерживаются только файлы Excel (.xlsx, .xls)")
            return
        
        # Скачиваем файл
        file_info = await bot.get_file(message.document.file_id)
        downloaded_file = await bot.download_file(file_info.file_path)
        
        # Сохраняем временный файл
        temp_file = f"temp_import_{message.from_user.id}.xlsx"
        with open(temp_file, 'wb') as file:
            file.write(downloaded_file.getvalue())
        
        # Читаем Excel
        df = pd.read_excel(temp_file)
        required_columns = ['Тип фильтра', 'Место установки', 'Дата замены']
        
        for col in required_columns:
            if col not in df.columns:
                raise ValueError(f"Отсутствует колонка: {col}")
        
        imported_count = 0
        errors = []
        
        with get_db_connection() as conn:
            cur = conn.cursor()
            
            for index, row in df.iterrows():
                try:
                    filter_type = safe_db_string(str(row['Тип фильтра']))
                    location = safe_db_string(str(row['Место установки']))
                    
                    if isinstance(row['Дата замены'], str):
                        last_change = validate_date(row['Дата замены'])
                    else:
                        last_change = row['Дата замены'].date()
                    
                    if pd.isna(row.get('Срок службы (дни)', pd.NA)):
                        lifetime_days = get_lifetime_by_type(filter_type)
                    else:
                        lifetime_days = int(row['Срок службы (дни)'])
                    
                    # Проверяем лимит
                    current_filters = len(get_user_filters(message.from_user.id))
                    if current_filters >= MAX_FILTERS_PER_USER:
                        errors.append(f"Достигнут лимит фильтров")
                        break
                    
                    expiry_date = last_change + timedelta(days=lifetime_days)
                    
                    cur.execute('''INSERT INTO filters 
                                (user_id, filter_type, location, last_change, expiry_date, lifetime_days) 
                                VALUES (?, ?, ?, ?, ?, ?)''',
                                (message.from_user.id, filter_type, location, last_change, expiry_date, lifetime_days))
                    
                    imported_count += 1
                    
                except Exception as e:
                    errors.append(f"Строка {index + 2}: {str(e)}")
            
            conn.commit()
        
        # Удаляем временный файл
        try:
            os.remove(temp_file)
        except:
            pass
        
        response = f"✅ <b>ИМПОРТ ЗАВЕРШЕН</b>\n\n📦 Импортировано: {imported_count} фильтров\n"
        if errors:
            response += f"\n⚠️ Ошибки: {len(errors)}\n"
            for error in errors[:3]:
                response += f"• {error}\n"
        
        await message.answer(response, parse_mode='HTML', reply_markup=get_main_keyboard(message.from_user.id))
        await state.finish()
        
    except Exception as e:
        await message.answer(
            f"❌ <b>Ошибка импорта:</b>\n\n{str(e)}",
            parse_mode='HTML',
            reply_markup=get_cancel_keyboard()
        )

# ========== ФОНОВЫЕ ЗАДАЧИ ==========
async def check_expired_filters():
    """Проверка просроченных фильтров"""
    try:
        with get_db_connection() as conn:
            cur = conn.cursor()
            cur.execute('''SELECT DISTINCT user_id, filter_type, location, expiry_date 
                          FROM filters 
                          WHERE expiry_date BETWEEN date('now') AND date('now', '+7 days')''')
            expiring_filters = cur.fetchall()
            
        for user_id, filter_type, location, expiry_date in expiring_filters:
            try:
                days_until_expiry = (datetime.strptime(str(expiry_date), '%Y-%m-%d').date() - datetime.now().date()).days
                expiry_date_nice = format_date_nice(datetime.strptime(str(expiry_date), '%Y-%m-%d').date())
                
                await bot.send_message(
                    user_id,
                    f"🔔 <b>НАПОМИНАНИЕ О ЗАМЕНЕ</b>\n\n"
                    f"🔧 {filter_type}\n"
                    f"📍 {location}\n"
                    f"📅 Срок истекает: {expiry_date_nice}\n"
                    f"⏳ Осталось дней: {days_until_expiry}\n\n"
                    f"⚠️ Рекомендуется заменить в ближайшее время",
                    parse_mode='HTML'
                )
                await asyncio.sleep(0.1)
            except Exception as e:
                logging.error(f"Не удалось отправить уведомление пользователю {user_id}: {e}")
                    
    except Exception as e:
        logging.error(f"Ошибка при проверке фильтров: {e}")

async def schedule_daily_check():
    """Ежедневная проверка"""
    while True:
        try:
            await check_expired_filters()
            await asyncio.sleep(3600)  # Каждый час
        except Exception as e:
            logging.error(f"Ошибка в фоновой задаче: {e}")
            await asyncio.sleep(300)

async def on_startup(dp):
    """Запуск бота"""
    logging.info("Бот запущен")
    init_db()
    asyncio.create_task(schedule_daily_check())

# Запуск бота
if __name__ == '__main__':
    executor.start_polling(dp, skip_updates=True, on_startup=on_startup)
