import logging
import sqlite3
import os
import asyncio
import shutil
import traceback
import re
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

# Стандартные сроки службы фильтров
DEFAULT_LIFETIMES = {
    "магистральный sl10": 180,
    "магистральный sl20": 180,
    "гейзер": 365,
    "аквафор": 365,
    "угольный": 90,
    "механический": 180,
    "престиж": 365,
    "кристалл": 365
}

# Инициализация бота
logging.basicConfig(level=logging.INFO)
bot = Bot(token=API_TOKEN)
storage = MemoryStorage()
dp = Dispatcher(bot, storage=storage)

# ========== УЛУЧШЕНИЯ: БЕЗОПАСНОСТЬ БАЗЫ ДАННЫХ ==========
@contextmanager
def get_db_connection():
    """Контекстный менеджер для безопасной работы с БД"""
    conn = sqlite3.connect('filters.db')
    conn.row_factory = sqlite3.Row  # Для доступа к колонкам по имени
    try:
        yield conn
    except Exception as e:
        conn.rollback()
        raise e
    finally:
        conn.close()

def get_user_filters(user_id):
    """Безопасное получение фильтров пользователя"""
    with get_db_connection() as conn:
        cur = conn.cursor()
        cur.execute("SELECT * FROM filters WHERE user_id = ? ORDER BY expiry_date", (user_id,))
        return [dict(row) for row in cur.fetchall()]

# ========== УЛУЧШЕНИЯ: ВАЛИДАЦИЯ ВВОДА ==========
def validate_date(date_str: str):
    """Валидация даты с улучшенной обработкой ошибок"""
    date_str = date_str.strip()
    
    # Убираем лишние символы
    date_str = re.sub(r'[^\d\.\-]', '', date_str)
    
    formats = ['%d.%m.%y', '%d.%m.%Y', '%d-%m-%y', '%d-%m-%Y']
    
    for fmt in formats:
        try:
            date_obj = datetime.strptime(date_str, fmt).date()
            
            # Проверяем что дата не в будущем (максимум +1 день для запаса)
            today = datetime.now().date()
            if date_obj > today + timedelta(days=1):
                raise ValueError("Дата не может быть в будущем")
                
            # Проверяем что дата не слишком старая (максимум 5 лет назад)
            if date_obj < today - timedelta(days=5*365):
                raise ValueError("Дата слишком старая")
                
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
        if days > 2000:  # Максимум ~5.5 лет
            raise ValueError("Слишком большой срок службы")
        return days
    except ValueError:
        raise ValueError("Введите корректное число дней")

def validate_filter_name(name: str):
    """Валидация названия фильтра"""
    name = name.strip()
    if not name:
        raise ValueError("Название фильтра не может быть пустым")
    if len(name) > 100:
        raise ValueError("Название фильтра слишком длинное")
    if re.search(r'[^\w\s\-\.]', name, re.UNICODE):
        raise ValueError("Название содержит запрещенные символы")
    return name

# ========== УЛУЧШЕНИЯ: КЛАВИАТУРЫ ДЛЯ МНОЖЕСТВЕННОГО ДОБАВЛЕНИЯ ==========
def get_multiple_filters_keyboard():
    """Клавиатура для выбора нескольких фильтров"""
    keyboard = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    
    # Первый ряд - популярные фильтры
    keyboard.row(
        types.KeyboardButton("🔧 Магистральный SL10"),
        types.KeyboardButton("🔧 Магистральный SL20")
    )
    
    # Второй ряд
    keyboard.row(
        types.KeyboardButton("💧 Гейзер Престиж"),
        types.KeyboardButton("💧 Аквафор Кристалл")
    )
    
    # Третий ряд
    keyboard.row(
        types.KeyboardButton("⚡ Угольный картридж"),
        types.KeyboardButton("🧽 Механический фильтр")
    )
    
    # Четвертый ряд - дополнительные опции
    keyboard.row(
        types.KeyboardButton("📦 Набор: Кухня + Ванная"),
        types.KeyboardButton("🏠 Набор: Полная квартира")
    )
    
    # Пятый ряд - управление
    keyboard.row(
        types.KeyboardButton("✅ Готово"),
        types.KeyboardButton("🔄 Очистить список")
    )
    
    keyboard.row(types.KeyboardButton("❌ Отмена"))
    
    return keyboard

def get_quick_sets_keyboard():
    """Клавиатура быстрых наборов фильтров"""
    keyboard = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=1)
    
    keyboard.row(types.KeyboardButton("🏠 Стандартный набор квартиры"))
    keyboard.row(types.KeyboardButton("🍳 Базовый кухонный набор"))
    keyboard.row(types.KeyboardButton("🚿 Набор для ванной"))
    keyboard.row(types.KeyboardButton("⚡ Расширенный набор"))
    
    keyboard.row(types.KeyboardButton("↩️ Назад к выбору"))
    keyboard.row(types.KeyboardButton("❌ Отмена"))
    
    return keyboard

def get_add_filter_keyboard():
    keyboard = types.ReplyKeyboardMarkup(resize_keyboard=True)
    keyboard.row(
        types.KeyboardButton("🔧 Один фильтр"),
        types.KeyboardButton("📦 Несколько фильтров")
    )
    keyboard.row(types.KeyboardButton("🚀 Быстрые наборы"))
    keyboard.row(types.KeyboardButton("🔙 Главное меню"))
    return keyboard

# ========== СУЩЕСТВУЮЩИЕ КЛАВИАТУРЫ (остаются без изменений) ==========
def get_main_keyboard():
    keyboard = types.ReplyKeyboardMarkup(resize_keyboard=True)
    keyboard.row(
        types.KeyboardButton("📋 Мои фильтры"),
        types.KeyboardButton("✨ Добавить фильтр")
    )
    keyboard.row(
        types.KeyboardButton("⏳ Сроки замены"),
        types.KeyboardButton("⚙️ Управление")
    )
    return keyboard

def get_management_keyboard():
    keyboard = types.ReplyKeyboardMarkup(resize_keyboard=True)
    keyboard.row(
        types.KeyboardButton("✏️ Редактировать"),
        types.KeyboardButton("🗑️ Удалить")
    )
    keyboard.row(
        types.KeyboardButton("📊 Статистика"),
        types.KeyboardButton("🔙 Главное меню")
    )
    return keyboard

def get_cancel_keyboard():
    keyboard = types.ReplyKeyboardMarkup(resize_keyboard=True)
    keyboard.add(types.KeyboardButton("❌ Отмена"))
    return keyboard

def get_back_keyboard():
    keyboard = types.ReplyKeyboardMarkup(resize_keyboard=True)
    keyboard.add(types.KeyboardButton("↩️ Назад"))
    return keyboard

def get_filter_type_keyboard():
    keyboard = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    keyboard.row(
        types.KeyboardButton("🔧 Магистральный SL10"),
        types.KeyboardButton("🔧 Магистральный SL20")
    )
    keyboard.row(
        types.KeyboardButton("💧 Гейзер"),
        types.KeyboardButton("💧 Аквафор")
    )
    keyboard.row(types.KeyboardButton("📝 Другой тип"))
    keyboard.row(types.KeyboardButton("❌ Отмена"))
    return keyboard

def get_location_keyboard():
    keyboard = types.ReplyKeyboardMarkup(resize_keyboard=True)
    keyboard.row(types.KeyboardButton("🏠 Кухня"))
    keyboard.row(types.KeyboardButton("🚿 Ванная"))
    keyboard.row(types.KeyboardButton("🏢 Офис"))
    keyboard.row(types.KeyboardButton("📍 Другое место"))
    keyboard.row(types.KeyboardButton("❌ Отмена"))
    return keyboard

def get_lifetime_keyboard():
    keyboard = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=3)
    keyboard.row(
        types.KeyboardButton("3️⃣ 90 дней"),
        types.KeyboardButton("6️⃣ 180 дней"),
        types.KeyboardButton("1️⃣ 365 дней")
    )
    keyboard.row(types.KeyboardButton("📅 Другое количество"))
    keyboard.row(types.KeyboardButton("❌ Отмена"))
    return keyboard

def get_edit_field_keyboard():
    keyboard = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    keyboard.row(
        types.KeyboardButton("🔧 Тип"),
        types.KeyboardButton("📍 Место")
    )
    keyboard.row(
        types.KeyboardButton("📅 Дата замены"),
        types.KeyboardButton("⏱️ Срок службы")
    )
    keyboard.row(types.KeyboardButton("↩️ Назад к фильтрам"))
    return keyboard

def get_confirmation_keyboard(filter_id):
    keyboard = types.InlineKeyboardMarkup(row_width=2)
    keyboard.add(
        types.InlineKeyboardButton("✅ Подтвердить удаление", callback_data=f"confirm_delete_{filter_id}"),
        types.InlineKeyboardButton("❌ Отменить", callback_data="cancel_delete")
    )
    return keyboard

def get_reset_confirmation_keyboard():
    keyboard = types.InlineKeyboardMarkup(row_width=2)
    keyboard.add(
        types.InlineKeyboardButton("✅ Да, сбросить", callback_data="confirm_reset"),
        types.InlineKeyboardButton("❌ Отмена", callback_data="cancel_reset")
    )
    return keyboard

# Инициализация БД
def init_db():
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
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)''')
        
        # Индексы для ускорения запросов
        cur.execute('''CREATE INDEX IF NOT EXISTS idx_user_id ON filters(user_id)''')
        cur.execute('''CREATE INDEX IF NOT EXISTS idx_expiry_date ON filters(expiry_date)''')
        conn.commit()

# Функция резервного копирования базы данных
def backup_database():
    """Создание резервной копии базы данных"""
    try:
        backup_dir = "backups"
        os.makedirs(backup_dir, exist_ok=True)
        
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_file = os.path.join(backup_dir, f"filters_backup_{timestamp}.db")
        
        shutil.copy2('filters.db', backup_file)
        logging.info(f"Создана резервная копия: {backup_file}")
        
        # Удаляем старые резервные копии (оставляем последние 7)
        backups = sorted([f for f in os.listdir(backup_dir) if f.startswith("filters_backup")])
        for old_backup in backups[:-7]:
            os.remove(os.path.join(backup_dir, old_backup))
            logging.info(f"Удалена старая резервная копия: {old_backup}")
            
    except Exception as e:
        logging.error(f"Ошибка при создании резервной копии: {e}")

# States
class FilterStates(StatesGroup):
    waiting_filter_type = State()
    waiting_location = State()
    waiting_change_date = State()
    waiting_lifetime = State()

class MultipleFiltersStates(StatesGroup):
    waiting_filters_list = State()
    waiting_location = State()
    waiting_change_date = State()
    waiting_lifetime = State()

class EditFilterStates(StatesGroup):
    waiting_filter_selection = State()
    waiting_field_selection = State()
    waiting_new_value = State()

# ========== УЛУЧШЕНИЯ: ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ==========
def parse_date(date_str):
    """Улучшенный парсинг даты с валидацией"""
    return validate_date(date_str)

def format_date_nice(date):
    return date.strftime('%d.%m.%y')

def get_lifetime_by_type(filter_type):
    filter_type_lower = filter_type.lower()
    for key, days in DEFAULT_LIFETIMES.items():
        if key in filter_type_lower:
            return days
    return 180

def get_status_icon_and_text(days_until_expiry):
    """Получение иконки и текста статуса"""
    if days_until_expiry <= 0:
        return "🔴", "ПРОСРОЧЕН"
    elif days_until_expiry <= 7:
        return "🟡", "СРОЧНО ЗАМЕНИТЬ"
    elif days_until_expiry <= 30:
        return "🟠", "СКОРО ЗАМЕНИТЬ"
    else:
        return "✅", "В НОРМЕ"

# ========== УЛУЧШЕНИЯ: ФОНГОВЫЕ ЗАДАЧИ И ОБРАБОТКА ОШИБОК ==========
async def check_expired_filters():
    """Фоновая задача для проверки просроченных фильтров"""
    try:
        with get_db_connection() as conn:
            cur = conn.cursor()
            
            # Фильтры, которые истекают в ближайшие 7 дней
            cur.execute('''SELECT DISTINCT user_id, filter_type, location, expiry_date 
                          FROM filters 
                          WHERE expiry_date BETWEEN date('now') AND date('now', '+7 days')''')
            expiring_filters = cur.fetchall()
            
            # Фильтры, которые уже просрочены (но не более 30 дней назад)
            cur.execute('''SELECT DISTINCT user_id, filter_type, location, expiry_date 
                          FROM filters 
                          WHERE expiry_date BETWEEN date('now', '-30 days') AND date('now', '-1 day')''')
            expired_filters = cur.fetchall()
        
        notified_users = set()
        
        # Уведомления о скором истечении срока
        for user_id, filter_type, location, expiry_date in expiring_filters:
            try:
                days_until_expiry = (datetime.strptime(str(expiry_date), '%Y-%m-%d').date() - datetime.now().date()).days
                expiry_date_nice = format_date_nice(datetime.strptime(str(expiry_date), '%Y-%m-%d').date())
                
                await bot.send_message(
                    user_id,
                    f"🔔 <b>Напоминание о замене фильтра</b>\n\n"
                    f"🔧 {filter_type}\n"
                    f"📍 {location}\n"
                    f"📅 Срок истекает: {expiry_date_nice}\n"
                    f"⏳ Осталось дней: {days_until_expiry}\n\n"
                    f"⚠️ <i>Рекомендуется заменить в ближайшее время</i>",
                    parse_mode='HTML'
                )
                notified_users.add(user_id)
                await asyncio.sleep(0.1)  # Небольшая задержка между сообщениями
            except Exception as e:
                logging.error(f"Не удалось отправить уведомление пользователю {user_id}: {e}")
        
        # Уведомления о просроченных фильтрах
        for user_id, filter_type, location, expiry_date in expired_filters:
            if user_id not in notified_users:  # Не спамим пользователям, которые уже получили уведомление
                try:
                    days_expired = (datetime.now().date() - datetime.strptime(str(expiry_date), '%Y-%m-%d').date()).days
                    expiry_date_nice = format_date_nice(datetime.strptime(str(expiry_date), '%Y-%m-%d').date())
                    
                    await bot.send_message(
                        user_id,
                        f"🚨 <b>СРОЧНОЕ УВЕДОМЛЕНИЕ</b>\n\n"
                        f"🔧 {filter_type}\n"
                        f"📍 {location}\n"
                        f"📅 Срок истек: {expiry_date_nice}\n"
                        f"⏰ Просрочено дней: {days_expired}\n\n"
                        f"❌ <i>Требуется немедленная замена!</i>",
                        parse_mode='HTML'
                    )
                    await asyncio.sleep(0.1)
                except Exception as e:
                    logging.error(f"Не удалось отправить срочное уведомление пользователю {user_id}: {e}")
                    
    except Exception as e:
        logging.error(f"Ошибка при проверке просроченных фильтров: {e}")

# Глобальный обработчик ошибок
@dp.errors_handler()
async def errors_handler(update, exception):
    """Глобальный обработчик ошибок"""
    logging.error(f"Ошибка: {exception}\n{traceback.format_exc()}")
    
    try:
        # Отправляем сообщение администратору
        await bot.send_message(
            ADMIN_ID,
            f"❌ Ошибка в боте:\n\n"
            f"Тип: {type(exception).__name__}\n"
            f"Ошибка: {str(exception)[:1000]}"
        )
    except Exception as e:
        logging.error(f"Не удалось отправить сообщение об ошибке администратору: {e}")
    
    return True

# Запуск фоновой задачи
async def schedule_daily_check():
    """Планировщик ежедневных проверок"""
    while True:
        try:
            await check_expired_filters()
            # Создаем резервную копию раз в день в 3:00
            if datetime.now().hour == 3:
                backup_database()
        except Exception as e:
            logging.error(f"Ошибка в фоновой задаче: {e}")
        
        # Ожидаем 1 час до следующей проверки
        await asyncio.sleep(60 * 60)

async def on_startup(dp):
    """Действия при запуске бота"""
    logging.info("Бот запущен")
    
    # Создаем резервную копию при запуске
    backup_database()
    
    # Запускаем фоновую задачу
    asyncio.create_task(schedule_daily_check())
    
    # Уведомляем администратора о запуске
    try:
        await bot.send_message(ADMIN_ID, "🤖 Бот успешно запущен и работает")
    except Exception as e:
        logging.error(f"Не удалось отправить уведомление администратору: {e}")

# ========== УЛУЧШЕНИЯ: ОБРАБОТЧИКИ С КНОПКАМИ ДЛЯ МНОЖЕСТВЕННОГО ДОБАВЛЕНИЯ ==========

# Команда start
@dp.message_handler(commands=['start'])
async def cmd_start(message: types.Message):
    await message.answer(
        "🌟 <b>Фильтр-Трекер</b> 🤖\n\n"
        "💧 <i>Умный помощник для своевременной замены фильтров</i>\n\n"
        "📦 <b>Основные возможности:</b>\n"
        "• 📋 Просмотр всех ваших фильтров\n"
        "• ✨ Добавление новых фильтров\n"
        "• ⏳ Контроль сроков замены\n"
        "• ⚙️ Полное управление базой\n"
        "• 📊 Детальная статистика\n"
        "• 🔔 Автоматические напоминания",
        parse_mode='HTML',
        reply_markup=get_main_keyboard()
    )

# Обработка кнопки "Главное меню"
@dp.message_handler(lambda message: message.text == "🔙 Главное меню")
async def cmd_back(message: types.Message):
    await message.answer(
        "🏠 <b>Главное меню</b>\n\n"
        "Выберите нужный раздел:",
        parse_mode='HTML',
        reply_markup=get_main_keyboard()
    )

# Обработка кнопки "Управление"
@dp.message_handler(lambda message: message.text == "⚙️ Управление")
async def cmd_management(message: types.Message):
    await message.answer(
        "🛠️ <b>Центр управления фильтрами</b>\n\n"
        "Выберите действие:",
        parse_mode='HTML',
        reply_markup=get_management_keyboard()
    )

# Добавление фильтра - выбор типа добавления
@dp.message_handler(lambda message: message.text == "✨ Добавить фильтр")
@dp.message_handler(commands=['add'])
async def cmd_add(message: types.Message):
    await message.answer(
        "🔧 <b>Выберите тип добавления:</b>\n\n"
        "💡 <i>Можно добавить один фильтр или сразу несколько</i>",
        parse_mode='HTML',
        reply_markup=get_add_filter_keyboard()
    )

# Обработка выбора типа добавления
@dp.message_handler(lambda message: message.text in ["🔧 Один фильтр", "📦 Несколько фильтров", "🚀 Быстрые наборы"])
async def process_add_type(message: types.Message, state: FSMContext):
    if message.text == "🔧 Один фильтр":
        await FilterStates.waiting_filter_type.set()
        await message.answer(
            "🔧 <b>Выберите тип фильтра:</b>\n\n"
            "💡 <i>Или укажите свой вариант</i>",
            parse_mode='HTML',
            reply_markup=get_filter_type_keyboard()
        )
    elif message.text == "📦 Несколько фильтров":
        await MultipleFiltersStates.waiting_filters_list.set()
        
        # Инициализируем список выбранных фильтров
        async with state.proxy() as data:
            data['selected_filters'] = []
            data['filters_list'] = []
        
        await message.answer(
            "📦 <b>Добавление нескольких фильтров</b>\n\n"
            "🔄 <b>Выберите фильтры из списка ниже:</b>\n\n"
            "💡 <i>Можно:</i>\n"
            "• Нажимать кнопки для добавления фильтров\n"
            "• Выбрать готовый набор\n"
            "• Ввести свои варианты текстом\n"
            "• Нажать '✅ Готово' когда закончите\n\n"
            "📝 <b>Текущий список:</b>\n"
            "<i>Пока пусто</i>",
            parse_mode='HTML',
            reply_markup=get_multiple_filters_keyboard()
        )
    elif message.text == "🚀 Быстрые наборы":
        await MultipleFiltersStates.waiting_filters_list.set()
        
        # Инициализируем список
        async with state.proxy() as data:
            data['selected_filters'] = []
            data['filters_list'] = []
        
        await message.answer(
            "🚀 <b>Быстрые наборы фильтров</b>\n\n"
            "💫 <i>Выберите готовый набор для быстрого добавления:</i>\n\n"
            "🏠 <b>Стандартный набор квартиры</b> - основные фильтры\n"
            "🍳 <b>Базовый кухонный набор</b> - минимум для кухни\n"
            "🚿 <b>Набор для ванной</b> - фильтры для ванной комнаты\n"
            "⚡ <b>Расширенный набор</b> - полный комплект\n\n"
            "💡 <i>После выбора набора можно добавить дополнительные фильтры</i>",
            parse_mode='HTML',
            reply_markup=get_quick_sets_keyboard()
        )

# Обработка кнопок при выборе нескольких фильтров
@dp.message_handler(state=MultipleFiltersStates.waiting_filters_list)
async def process_multiple_filters_selection(message: types.Message, state: FSMContext):
    async with state.proxy() as data:
        if 'selected_filters' not in data:
            data['selected_filters'] = []
        if 'filters_list' not in data:
            data['filters_list'] = []
    
    # Обработка кнопки отмены
    if message.text == "❌ Отмена":
        await state.finish()
        await message.answer("🚫 Добавление фильтров отменено", reply_markup=get_main_keyboard())
        return
    
    # Обработка кнопки "Готово"
    if message.text == "✅ Готово":
        if not data['selected_filters']:
            await message.answer(
                "❌ <b>Список фильтров пуст!</b>\n\n"
                "💡 <i>Добавьте хотя бы один фильтр перед завершением</i>",
                parse_mode='HTML',
                reply_markup=get_multiple_filters_keyboard()
            )
            return
        
        # Сохраняем выбранные фильтры и переходим к следующему шагу
        data['filters_list'] = data['selected_filters'].copy()
        await MultipleFiltersStates.next()
        
        await message.answer(
            f"✅ <b>Список фильтров сохранен!</b>\n\n"
            f"📦 <b>Будет добавлено фильтров:</b> {len(data['filters_list'])}\n\n"
            f"🔧 <b>Список фильтров:</b>\n" + "\n".join([f"• {f}" for f in data['filters_list']]) + "\n\n"
            f"📍 <b>Укажите место установки для всех фильтров:</b>\n\n"
            f"💡 <i>Все фильтры будут установлены в одном месте</i>",
            parse_mode='HTML',
            reply_markup=get_location_keyboard()
        )
        return
    
    # Обработка кнопки "Очистить список"
    if message.text == "🔄 Очистить список":
        data['selected_filters'] = []
        await message.answer(
            "🔄 <b>Список фильтров очищен!</b>\n\n"
            "💫 <i>Начните добавлять фильтры заново</i>",
            parse_mode='HTML',
            reply_markup=get_multiple_filters_keyboard()
        )
        return
    
    # Обработка кнопки "Назад к выбору"
    if message.text == "↩️ Назад к выбору":
        await message.answer(
            "📦 <b>Добавление нескольких фильтров</b>\n\n"
            "🔄 <b>Выберите фильтры из списка:</b>",
            parse_mode='HTML',
            reply_markup=get_multiple_filters_keyboard()
        )
        return
    
    # Обработка готовых наборов
    predefined_filters = []
    if message.text == "📦 Набор: Кухня + Ванная":
        predefined_filters = ["Магистральный SL10", "Гейзер Престиж", "Угольный картридж"]
    elif message.text == "🏠 Набор: Полная квартира":
        predefined_filters = ["Магистральный SL10", "Магистральный SL20", "Гейзер Престиж", "Аквафор Кристалл", "Угольный картридж"]
    elif message.text == "🏠 Стандартный набор квартиры":
        predefined_filters = ["Магистральный SL10", "Гейзер Престиж", "Угольный картридж"]
    elif message.text == "🍳 Базовый кухонный набор":
        predefined_filters = ["Магистральный SL10", "Угольный картридж"]
    elif message.text == "🚿 Набор для ванной":
        predefined_filters = ["Магистральный SL20", "Механический фильтр"]
    elif message.text == "⚡ Расширенный набор":
        predefined_filters = ["Магистральный SL10", "Магистральный SL20", "Гейзер Престиж", "Аквафор Кристалл", "Угольный картридж", "Механический фильтр"]
    
    if predefined_filters:
        data['selected_filters'].extend(predefined_filters)
        # Удаляем дубликаты
        data['selected_filters'] = list(dict.fromkeys(data['selected_filters']))
        
        await message.answer(
            f"✅ <b>Набор добавлен!</b>\n\n"
            f"📦 Добавлено фильтров: {len(predefined_filters)}\n"
            f"📊 Всего в списке: {len(data['selected_filters'])}",
            parse_mode='HTML',
            reply_markup=get_multiple_filters_keyboard()
        )
        return
    
    # Обработка отдельных фильтров (кнопки)
    filter_mapping = {
        "🔧 Магистральный SL10": "Магистральный SL10",
        "🔧 Магистральный SL20": "Магистральный SL20",
        "💧 Гейзер Престиж": "Гейзер Престиж",
        "💧 Аквафор Кристалл": "Аквафор Кристалл",
        "⚡ Угольный картридж": "Угольный картридж",
        "🧽 Механический фильтр": "Механический фильтр"
    }
    
    if message.text in filter_mapping:
        filter_name = filter_mapping[message.text]
        if filter_name not in data['selected_filters']:
            data['selected_filters'].append(filter_name)
            await message.answer(
                f"✅ <b>Добавлен:</b> {filter_name}\n\n"
                f"📊 Всего в списке: {len(data['selected_filters'])}",
                parse_mode='HTML',
                reply_markup=get_multiple_filters_keyboard()
            )
        else:
            await message.answer(
                f"ℹ️ <b>Фильтр уже в списке:</b> {filter_name}",
                parse_mode='HTML',
                reply_markup=get_multiple_filters_keyboard()
            )
        return
    
    # Обработка текстового ввода (пользователь ввел свои фильтры)
    if message.text and message.text not in ["✅ Готово", "🔄 Очистить список", "❌ Отмена"]:
        # Разделяем ввод на отдельные фильтры
        filter_text = message.text
        additional_filters = []
        
        # Пробуем разделить по запятым
        if ',' in filter_text:
            additional_filters = [f.strip() for f in filter_text.split(',') if f.strip()]
        else:
            # Или по переносам строк
            additional_filters = [f.strip() for f in filter_text.split('\n') if f.strip()]
        
        # Добавляем только уникальные фильтры
        added_count = 0
        for new_filter in additional_filters:
            if new_filter and new_filter not in data['selected_filters']:
                data['selected_filters'].append(new_filter)
                added_count += 1
        
        if added_count > 0:
            await message.answer(
                f"✅ <b>Добавлено фильтров:</b> {added_count}\n\n"
                f"📊 Всего в списке: {len(data['selected_filters'])}",
                parse_mode='HTML',
                reply_markup=get_multiple_filters_keyboard()
            )
        else:
            await message.answer(
                "ℹ️ <b>Новых фильтров не добавлено</b>\n\n"
                "💡 <i>Все введенные фильтры уже есть в списке</i>",
                parse_mode='HTML',
                reply_markup=get_multiple_filters_keyboard()
            )
        return
    
    # Обновляем отображение списка
    if data['selected_filters']:
        filters_text = "\n".join([f"• {f}" for f in data['selected_filters']])
        status_text = f"✅ <b>Выбрано фильтров:</b> {len(data['selected_filters'])}\n\n{filters_text}"
    else:
        status_text = "📝 <b>Список пуст</b>\n\n<i>Добавьте фильтры с помощью кнопок</i>"
    
    await message.answer(
        f"📦 <b>Добавление фильтров</b>\n\n"
        f"{status_text}\n\n"
        f"💡 <i>Продолжайте добавлять фильтры или нажмите '✅ Готово'</i>",
        parse_mode='HTML',
        reply_markup=get_multiple_filters_keyboard()
    )

# ========== СУЩЕСТВУЮЩИЕ ОБРАБОТЧИКИ (с улучшенной безопасностью) ==========

# Добавление одного фильтра
@dp.message_handler(state=FilterStates.waiting_filter_type)
async def process_filter_type(message: types.Message, state: FSMContext):
    if message.text == "❌ Отмена":
        await state.finish()
        await message.answer("🚫 Добавление фильтра отменено", reply_markup=get_main_keyboard())
        return
        
    if message.text == "📝 Другой тип":
        await message.answer(
            "📝 <b>Введите тип фильтра:</b>\n"
            "<i>Например: Угольный фильтр, Механический фильтр и т.д.</i>",
            parse_mode='HTML',
            reply_markup=get_cancel_keyboard()
        )
        return
    
    try:
        filter_name = validate_filter_name(message.text)
        
        async with state.proxy() as data:
            data['filter_type'] = filter_name
            data['lifetime'] = get_lifetime_by_type(filter_name)

        await FilterStates.next()
        await message.answer(
            "📍 <b>Укажите место установки фильтра:</b>\n\n"
            "💡 <i>Выберите из списка или укажите свой вариант</i>",
            parse_mode='HTML',
            reply_markup=get_location_keyboard()
        )
    except ValueError as e:
        await message.answer(
            f"❌ <b>Ошибка в названии фильтра:</b>\n\n"
            f"💡 <i>{str(e)}</i>",
            parse_mode='HTML',
            reply_markup=get_filter_type_keyboard()
        )

# Общий обработчик для места установки (и для одного, и для нескольких фильтров)
@dp.message_handler(state=[FilterStates.waiting_location, MultipleFiltersStates.waiting_location])
async def process_location(message: types.Message, state: FSMContext):
    if message.text == "❌ Отмена":
        await state.finish()
        await message.answer("🚫 Добавление отменено", reply_markup=get_main_keyboard())
        return
        
    if message.text == "📍 Другое место":
        await message.answer(
            "📍 <b>Введите место установки фильтра:</b>\n\n"
            "💡 <i>Например: Кухня, Ванная комната, Под раковиной, Гостиная, Офис, Балкон, Гараж и т.д.</i>",
            parse_mode='HTML',
            reply_markup=get_cancel_keyboard()
        )
        return
    
    current_state = await state.get_state()
    
    if current_state == "FilterStates:waiting_location":
        # Для одного фильтра
        async with state.proxy() as data:
            data['location'] = message.text

        await FilterStates.next()
        today_nice = format_date_nice(datetime.now().date())
        await message.answer(
            f"📅 <b>Дата последней замены</b>\n\n"
            f"🔧 <i>Фильтр:</i> {data['filter_type']}\n"
            f"📍 <i>Место:</i> {data['location']}\n\n"
            f"📝 <b>Введите дату замены в формате ДД.ММ.ГГ:</b>\n"
            f"<i>Например: {today_nice}</i>",
            parse_mode='HTML',
            reply_markup=get_cancel_keyboard()
        )
    else:
        # Для нескольких фильтров
        async with state.proxy() as data:
            data['location'] = message.text

        await MultipleFiltersStates.next()
        today_nice = format_date_nice(datetime.now().date())
        await message.answer(
            f"📅 <b>Дата последней замены для всех фильтров</b>\n\n"
            f"📍 <i>Место для всех фильтров:</i> {data['location']}\n\n"
            f"📝 <b>Введите дату замены в формате ДД.ММ.ГГ:</b>\n"
            f"<i>Например: {today_nice}</i>",
            parse_mode='HTML',
            reply_markup=get_cancel_keyboard()
        )

# Общий обработчик для даты замены
@dp.message_handler(state=[FilterStates.waiting_change_date, MultipleFiltersStates.waiting_change_date])
async def process_date(message: types.Message, state: FSMContext):
    if message.text == "❌ Отмена":
        await state.finish()
        await message.answer("🚫 Добавление отменено", reply_markup=get_main_keyboard())
        return
        
    try:
        # Преобразуем дату из формата ДД.ММ.ГГ
        change_date = parse_date(message.text)
        
        current_state = await state.get_state()
        
        if current_state == "FilterStates:waiting_change_date":
            # Для одного фильтра
            async with state.proxy() as data:
                data['change_date'] = change_date
                
            await FilterStates.next()
            await message.answer(
                f"⏱️ <b>Срок службы фильтра</b>\n\n"
                f"📅 <i>Рекомендуемый срок:</i> {data['lifetime']} дней\n\n"
                f"🔄 <b>Выберите срок службы:</b>",
                parse_mode='HTML',
                reply_markup=get_lifetime_keyboard()
            )
        else:
            # Для нескольких фильтров
            async with state.proxy() as data:
                data['change_date'] = change_date
                
            await MultipleFiltersStates.next()
            await message.answer(
                f"⏱️ <b>Срок службы для всех фильтров</b>\n\n"
                f"📅 <i>Рекомендуемый срок:</i> {data['lifetime']} дней\n\n"
                f"🔄 <b>Выберите срок службы:</b>",
                parse_mode='HTML',
                reply_markup=get_lifetime_keyboard()
            )
        
    except ValueError as e:
        today_nice = format_date_nice(datetime.now().date())
        await message.answer(
            f"❌ <b>Ошибка в дате!</b>\n\n"
            f"💡 <i>{str(e)}</i>\n\n"
            f"📝 <i>Используйте формат ДД.ММ.ГГ</i>\n"
            f"<i>Пример: {today_nice}</i>",
            parse_mode='HTML',
            reply_markup=get_cancel_keyboard()
        )

# Общий обработчик для срока службы
@dp.message_handler(state=[FilterStates.waiting_lifetime, MultipleFiltersStates.waiting_lifetime])
async def process_lifetime(message: types.Message, state: FSMContext):
    if message.text == "❌ Отмена":
        await state.finish()
        await message.answer("🚫 Добавление отменено", reply_markup=get_main_keyboard())
        return
        
    try:
        current_state = await state.get_state()
        
        if current_state == "FilterStates:waiting_lifetime":
            # Для одного фильтра
            async with state.proxy() as data:
                change_date = data['change_date']
                filter_type = data['filter_type']
                location = data['location']
                
                if message.text.startswith("3️⃣") or message.text.startswith("6️⃣") or message.text.startswith("1️⃣"):
                    lifetime = int(message.text.split()[1])
                elif message.text == "📅 Другое количество":
                    await message.answer(
                        "🔢 <b>Введите количество дней:</b>\n"
                        "<i>Например: 120, 200, 400 и т.д.</i>",
                        parse_mode='HTML',
                        reply_markup=get_cancel_keyboard()
                    )
                    return
                else:
                    lifetime = validate_lifetime(message.text)
                
                expiry_date = change_date + timedelta(days=lifetime)
                
                with get_db_connection() as conn:
                    cur = conn.cursor()
                    cur.execute('''INSERT INTO filters 
                                (user_id, filter_type, location, last_change, expiry_date, lifetime_days) 
                                VALUES (?, ?, ?, ?, ?, ?)''',
                               (message.from_user.id, filter_type, location, change_date, expiry_date, lifetime))
                    conn.commit()

                days_until_expiry = (expiry_date - datetime.now().date()).days
                status_icon, status_text = get_status_icon_and_text(days_until_expiry)
                
                change_date_nice = format_date_nice(change_date)
                expiry_date_nice = format_date_nice(expiry_date)
                
                await message.answer(
                    f"{status_icon} <b>ФИЛЬТР ДОБАВЛЕН!</b>\n\n"
                    f"🔧 <b>Тип:</b> {filter_type}\n"
                    f"📍 <b>Место:</b> {location}\n"
                    f"📅 <b>Заменен:</b> {change_date_nice}\n"
                    f"⏱️ <b>Срок службы:</b> {lifetime} дней\n"
                    f"📅 <b>Годен до:</b> {expiry_date_nice}\n"
                    f"⏳ <b>Осталось дней:</b> {days_until_expiry}\n"
                    f"📊 <b>Статус:</b> {status_text}",
                    parse_mode='HTML',
                    reply_markup=get_main_keyboard()
                )
                await state.finish()
                
        else:
            # Для нескольких фильтров
            async with state.proxy() as data:
                change_date = data['change_date']
                location = data['location']
                filters_list = data['filters_list']
                
                if message.text.startswith("3️⃣") or message.text.startswith("6️⃣") or message.text.startswith("1️⃣"):
                    lifetime = int(message.text.split()[1])
                elif message.text == "📅 Другое количество":
                    await message.answer(
                        "🔢 <b>Введите количество дней:</b>\n"
                        "<i>Например: 120, 200, 400 и т.д.</i>",
                        parse_mode='HTML',
                        reply_markup=get_cancel_keyboard()
                    )
                    return
                else:
                    lifetime = validate_lifetime(message.text)
                
                # Добавляем все фильтры в базу данных
                with get_db_connection() as conn:
                    cur = conn.cursor()
                    
                    added_count = 0
                    results = []
                    
                    for filter_type in filters_list:
                        expiry_date = change_date + timedelta(days=lifetime)
                        
                        cur.execute('''INSERT INTO filters 
                                    (user_id, filter_type, location, last_change, expiry_date, lifetime_days) 
                                    VALUES (?, ?, ?, ?, ?, ?)''',
                                   (message.from_user.id, filter_type, location, change_date, expiry_date, lifetime))
                        added_count += 1
                        
                        days_until_expiry = (expiry_date - datetime.now().date()).days
                        results.append({
                            'type': filter_type,
                            'expiry_date': expiry_date,
                            'days_until_expiry': days_until_expiry
                        })
                    
                    conn.commit()
                
                change_date_nice = format_date_nice(change_date)
                
                # Формируем сообщение с результатами
                response = f"✅ <b>УСПЕШНО ДОБАВЛЕНО {added_count} ФИЛЬТРОВ!</b>\n\n"
                response += f"📍 <b>Место:</b> {location}\n"
                response += f"📅 <b>Дата замены:</b> {change_date_nice}\n"
                response += f"⏱️ <b>Срок службы:</b> {lifetime} дней\n\n"
                
                response += "<b>📋 Добавленные фильтры:</b>\n"
                for i, result in enumerate(results, 1):
                    expiry_date_nice = format_date_nice(result['expiry_date'])
                    status_icon, _ = get_status_icon_and_text(result['days_until_expiry'])
                    response += f"{status_icon} {result['type']} (до {expiry_date_nice})\n"
                
                await message.answer(
                    response,
                    parse_mode='HTML',
                    reply_markup=get_main_keyboard()
                )
                await state.finish()
            
    except ValueError as e:
        await message.answer(
            f"❌ <b>Ошибка в сроке службы!</b>\n\n"
            f"💡 <i>{str(e)}</i>\n\n"
            f"🔢 <i>Введите количество дней числом</i>\n"
            f"<i>Например: 90, 180, 365</i>",
            parse_mode='HTML',
            reply_markup=get_lifetime_keyboard()
        )

# Остальные обработчики остаются без изменений, но используют улучшенные функции
# Список фильтров
@dp.message_handler(lambda message: message.text == "📋 Мои фильтры")
@dp.message_handler(commands=['list'])
async def cmd_list(message: types.Message):
    filters = get_user_filters(message.from_user.id)

    if not filters:
        await message.answer(
            "📭 <b>Список фильтров пуст</b>\n\n"
            "💫 <i>Добавьте первый фильтр с помощью кнопки '✨ Добавить фильтр'</i>",
            parse_mode='HTML',
            reply_markup=get_main_keyboard()
        )
        return

    response = "📋 <b>ВАШИ ФИЛЬТРЫ</b>\n\n"
    today = datetime.now().date()
    
    for f in filters:
        expiry_date = datetime.strptime(str(f['expiry_date']), '%Y-%m-%d').date()
        days_until_expiry = (expiry_date - today).days
        
        status_icon, status_text = get_status_icon_and_text(days_until_expiry)
        
        last_change_nice = format_date_nice(datetime.strptime(str(f['last_change']), '%Y-%m-%d').date())
        expiry_date_nice = format_date_nice(expiry_date)
        
        response += (
            f"{status_icon} <b>ФИЛЬТР #{f['id']}</b>\n"
            f"   🔧 {f['filter_type']}\n"
            f"   📍 {f['location']}\n"
            f"   📅 Заменен: {last_change_nice}\n"
            f"   ⏱️ Срок: {f['lifetime_days']} дн.\n"
            f"   🗓️ Годен до: {expiry_date_nice}\n"
            f"   ⏳ Осталось: {days_until_expiry} дн.\n"
            f"   📊 Статус: {status_text}\n\n"
        )

    await message.answer(response, parse_mode='HTML', reply_markup=get_main_keyboard())

# Проверка сроков
@dp.message_handler(lambda message: message.text == "⏳ Сроки замены")
@dp.message_handler(commands=['check'])
async def cmd_check(message: types.Message):
    filters = get_user_filters(message.from_user.id)

    if not filters:
        await message.answer(
            "📭 <b>Нет фильтров для проверки</b>\n\n"
            "💫 <i>Добавьте фильтры для отслеживания сроков</i>",
            parse_mode='HTML',
            reply_markup=get_main_keyboard()
        )
        return

    today = datetime.now().date()
    expired_filters = []
    expiring_soon = []
    warning_filters = []
    
    for f in filters:
        expiry_date = datetime.strptime(str(f['expiry_date']), '%Y-%m-%d').date()
        days_until_expiry = (expiry_date - today).days
        
        expiry_date_nice = format_date_nice(expiry_date)
        
        if days_until_expiry <= 0:
            expired_filters.append(f"🔴 {f['filter_type']} ({f['location']}) - просрочен {abs(days_until_expiry)} дн. назад (до {expiry_date_nice})")
        elif days_until_expiry <= 7:
            expiring_soon.append(f"🟡 {f['filter_type']} ({f['location']}) - осталось {days_until_expiry} дн. (до {expiry_date_nice})")
        elif days_until_expiry <= 30:
            warning_filters.append(f"🟠 {f['filter_type']} ({f['location']}) - осталось {days_until_expiry} дн. (до {expiry_date_nice})")

    response = "⏳ <b>КОНТРОЛЬ СРОКОВ</b>\n\n"
    
    if expired_filters:
        response += "🚨 <b>ПРОСРОЧЕНЫ:</b>\n" + "\n".join(expired_filters) + "\n\n"
    
    if expiring_soon:
        response += "⚠️ <b>СРОЧНО ИСТЕКАЮТ:</b>\n" + "\n".join(expiring_soon) + "\n\n"
    
    if warning_filters:
        response += "🔔 <b>СКОРО ИСТЕКАЮТ:</b>\n" + "\n".join(warning_filters) + "\n\n"
    
    if not expired_filters and not expiring_soon and not warning_filters:
        response += "✅ <b>ВСЕ ФИЛЬТРЫ В НОРМЕ!</b>\n\n"
        response += "💫 <i>Следующая проверка через 30+ дней</i>"

    await message.answer(response, parse_mode='HTML', reply_markup=get_main_keyboard())

# Обработка отмены
@dp.message_handler(lambda message: message.text == "❌ Отмена", state='*')
async def cmd_cancel(message: types.Message, state: FSMContext):
    current_state = await state.get_state()
    if current_state is None:
        return
    
    await state.finish()
    await message.answer("🚫 Действие отменено", reply_markup=get_main_keyboard())

# Обработка других сообщений
@dp.message_handler()
async def handle_other_messages(message: types.Message):
    await message.answer(
        "🌟 <b>Фильтр-Трекер</b> 🤖\n\n"
        "💧 <i>Выберите действие с помощью кнопок ниже:</i>",
        parse_mode='HTML',
        reply_markup=get_main_keyboard()
    )

# Запуск бота
if __name__ == '__main__':
    # Проверка обязательных переменных
    if not API_TOKEN:
        logging.error("Токен бота не найден! Установите переменную TELEGRAM_BOT_TOKEN")
        exit(1)
    
    init_db()
    
    # Запуск с обработчиком startup
    executor.start_polling(
        dp, 
        skip_updates=True,
        on_startup=on_startup
   
