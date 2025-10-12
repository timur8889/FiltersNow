import logging
import sqlite3
import os
import asyncio
import shutil
import traceback
import re
import sys
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
    "угольный": 90,
    "механический": 180,
    "престиж": 365,
    "кристалл": 365
}

# Ограничения
MAX_FILTERS_PER_USER = 50

# Инициализация бота
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

def safe_db_string(value: str) -> str:
    """Очистка строки для безопасного использования в БД"""
    if not value:
        return ""
    return re.sub(r'[;\'"\\]', '', value.strip())

def get_user_filters(user_id):
    """Безопасное получение фильтров пользователя"""
    with get_db_connection() as conn:
        cur = conn.cursor()
        cur.execute("SELECT * FROM filters WHERE user_id = ? ORDER BY expiry_date", (user_id,))
        return [dict(row) for row in cur.fetchall()]

def get_filter_by_id(filter_id, user_id):
    """Получение фильтра по ID с проверкой пользователя"""
    with get_db_connection() as conn:
        cur = conn.cursor()
        cur.execute("SELECT * FROM filters WHERE id = ? AND user_id = ?", (filter_id, user_id))
        result = cur.fetchone()
        return dict(result) if result else None

def check_filters_limit(user_id):
    """Проверка лимита фильтров"""
    filters = get_user_filters(user_id)
    return len(filters) >= MAX_FILTERS_PER_USER

# ========== УЛУЧШЕНИЯ: ВАЛИДАЦИЯ ВВОДА ==========
def validate_date(date_str: str):
    """Валидация даты с улучшенной обработкой ошибок"""
    date_str = date_str.strip()
    
    # Убираем лишние символы, но оставляем точки, дефисы и слэши
    date_str = re.sub(r'[^\d\.\-/]', '', date_str)
    
    formats = ['%d.%m.%y', '%d.%m.%Y', '%d-%m-%y', '%d-%m-%Y', '%d/%m/%y', '%d/%m/%Y']
    
    for fmt in formats:
        try:
            date_obj = datetime.strptime(date_str, fmt).date()
            today = datetime.now().date()
            
            # Проверяем что дата не в будущем (максимум +1 день для запаса)
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
    # Разрешаем буквы, цифры, пробелы, дефисы и точки
    if re.search(r'[^\w\s\-\.]', name, re.UNICODE):
        raise ValueError("Название содержит запрещенные символы")
    return safe_db_string(name)

# ========== ОБНОВЛЕННЫЕ КЛАВИАТУРЫ ==========

def get_filter_type_keyboard():
    """Клавиатура для выбора типа фильтра с ВСЕМИ вариантами"""
    keyboard = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    
    # Все доступные фильтры в одном списке
    keyboard.row(
        types.KeyboardButton("🔧 Магистральный SL10"),
        types.KeyboardButton("🔧 Магистральный SL20")
    )
    keyboard.row(
        types.KeyboardButton("💧 Гейзер Престиж"),
        types.KeyboardButton("💧 Аквафор Кристалл")
    )
    keyboard.row(
        types.KeyboardButton("⚡ Угольный картридж"),
        types.KeyboardButton("🧽 Механический фильтр")
    )
    keyboard.row(types.KeyboardButton("📝 Другой тип"))
    keyboard.row(types.KeyboardButton("❌ Отмена"))
    
    return keyboard

def get_multiple_filters_keyboard():
    """Клавиатура для выбора нескольких фильтров с ВСЕМИ вариантами"""
    keyboard = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    
    # ВСЕ фильтры из списка
    keyboard.row(
        types.KeyboardButton("🔧 Магистральный SL10"),
        types.KeyboardButton("🔧 Магистральный SL20")
    )
    keyboard.row(
        types.KeyboardButton("💧 Гейзер Престиж"),
        types.KeyboardButton("💧 Аквафор Кристалл")
    )
    keyboard.row(
        types.KeyboardButton("⚡ Угольный картридж"),
        types.KeyboardButton("🧽 Механический фильтр")
    )
    keyboard.row(types.KeyboardButton("📝 Другой тип"))
    
    # Управляющие кнопки
    keyboard.row(
        types.KeyboardButton("✅ Готово"),
        types.KeyboardButton("🔄 Очистить список")
    )
    keyboard.row(types.KeyboardButton("❌ Отмена"))
    
    return keyboard

def get_add_filter_keyboard():
    """Обновленная клавиатура добавления фильтра (без быстрых наборов)"""
    keyboard = types.ReplyKeyboardMarkup(resize_keyboard=True)
    keyboard.row(
        types.KeyboardButton("🔧 Один фильтр"),
        types.KeyboardButton("📦 Несколько фильтров")
    )
    # Убрана кнопка "🚀 Быстрые наборы"
    keyboard.row(types.KeyboardButton("🔙 Главное меню"))
    return keyboard

def get_location_keyboard():
    """Упрощенная клавиатура для места установки"""
    keyboard = types.ReplyKeyboardMarkup(resize_keyboard=True)
    keyboard.row(types.KeyboardButton("📍 Указать место установки"))
    keyboard.row(types.KeyboardButton("❌ Отмена"))
    return keyboard

def get_filters_list_keyboard(filters, action="delete"):
    """Клавиатура со списком фильтров для удаления или редактирования"""
    keyboard = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    
    for f in filters:
        button_text = f"#{f['id']} {f['filter_type']} - {f['location']}"
        keyboard.add(types.KeyboardButton(button_text))
    
    keyboard.row(types.KeyboardButton("🔙 Главное меню"))
    return keyboard

def get_edit_filter_keyboard():
    """Клавиатура для редактирования фильтра"""
    keyboard = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    keyboard.row(
        types.KeyboardButton("✏️ Тип фильтра"),
        types.KeyboardButton("📍 Место установки")
    )
    keyboard.row(
        types.KeyboardButton("📅 Дата замены"),
        types.KeyboardButton("⏱️ Срок службы")
    )
    keyboard.row(types.KeyboardButton("🔙 К списку фильтров"))
    keyboard.row(types.KeyboardButton("🔙 Главное меню"))
    return keyboard

def get_confirmation_keyboard(filter_id=None, action="delete"):
    """Клавиатура подтверждения удаления"""
    keyboard = types.InlineKeyboardMarkup(row_width=2)
    if action == "delete":
        keyboard.add(
            types.InlineKeyboardButton("✅ Да, удалить", callback_data=f"confirm_delete_{filter_id}"),
            types.InlineKeyboardButton("❌ Отмена", callback_data="cancel_delete")
        )
    return keyboard

# ========== СУЩЕСТВУЮЩИЕ КЛАВИАТУРЫ ==========
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

# ========== УЛУЧШЕНИЯ: ИНИЦИАЛИЗАЦИЯ БАЗЫ ДАННЫХ ==========
def init_db():
    """Безопасная инициализация базы данных"""
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
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)''')
            
            # Индексы для ускорения запросов
            cur.execute('''CREATE INDEX IF NOT EXISTS idx_user_id ON filters(user_id)''')
            cur.execute('''CREATE INDEX IF NOT EXISTS idx_expiry_date ON filters(expiry_date)''')
            conn.commit()
    except Exception as e:
        logging.error(f"Ошибка инициализации БД: {e}")
        # Создаем резервную копию при ошибке
        if os.path.exists('filters.db'):
            backup_name = f'filters_backup_error_{datetime.now().strftime("%Y%m%d_%H%M%S")}.db'
            os.rename('filters.db', backup_name)
            logging.info(f"Создана резервную копию при ошибке: {backup_name}")
        
        # Повторная попытка создания БД
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
                            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)''')
                conn.commit()
                logging.info("База данных успешно создана после ошибки")
        except Exception as e2:
            logging.error(f"Критическая ошибка при создании БД: {e2}")
            raise

# Функция резервного копирования базы данных
def backup_database():
    """Создание резервной копии базы данных"""
    try:
        if not os.path.exists('filters.db'):
            logging.warning("База данных не найдена для резервного копирования")
            return
            
        backup_dir = "backups"
        os.makedirs(backup_dir, exist_ok=True)
        
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_file = os.path.join(backup_dir, f"filters_backup_{timestamp}.db")
        
        shutil.copy2('filters.db', backup_file)
        logging.info(f"Создана резервная копия: {backup_file}")
        
        # Удаляем старые резервные копии (оставляем последние 7)
        backups = sorted([f for f in os.listdir(backup_dir) if f.startswith("filters_backup")])
        for old_backup in backups[:-7]:
            old_backup_path = os.path.join(backup_dir, old_backup)
            os.remove(old_backup_path)
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

class DeleteFilterStates(StatesGroup):
    waiting_filter_selection = State()

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

def create_expiry_infographic(filters):
    """Создание инфографики по срокам замены"""
    today = datetime.now().date()
    expired_count = 0
    expiring_soon_count = 0
    warning_count = 0
    ok_count = 0
    
    for f in filters:
        expiry_date = datetime.strptime(str(f['expiry_date']), '%Y-%m-%d').date()
        days_until_expiry = (expiry_date - today).days
        
        if days_until_expiry <= 0:
            expired_count += 1
        elif days_until_expiry <= 7:
            expiring_soon_count += 1
        elif days_until_expiry <= 30:
            warning_count += 1
        else:
            ok_count += 1
    
    total = len(filters)
    
    # Создаем текстовую инфографику
    infographic = "📊 <b>ИНФОГРАФИКА СРОКОВ ЗАМЕНЫ</b>\n\n"
    
    if expired_count > 0:
        infographic += f"🔴 <b>Просрочено:</b> {expired_count} фильтров\n"
        infographic += "   ⚠️ Требуется немедленная замена!\n\n"
    
    if expiring_soon_count > 0:
        infographic += f"🟡 <b>Срочно заменить:</b> {expiring_soon_count} фильтров\n"
        infographic += "   📅 Заменить в течение недели\n\n"
    
    if warning_count > 0:
        infographic += f"🟠 <b>Скоро заменить:</b> {warning_count} фильтров\n"
        infographic += "   📅 Заменить в течение месяца\n\n"
    
    if ok_count > 0:
        infographic += f"✅ <b>В норме:</b> {ok_count} фильтров\n"
        infographic += "   💧 Следующая замена через 30+ дней\n\n"
    
    # Прогресс-бар
    if total > 0:
        infographic += "📈 <b>Статус фильтров:</b>\n"
        infographic += "[" + "🔴" * min(expired_count, 10) + "🟡" * min(expiring_soon_count, 10) + "🟠" * min(warning_count, 10) + "✅" * min(ok_count, 10) + "]\n\n"
    
    infographic += f"📦 <b>Всего фильтров:</b> {total}"
    
    return infographic

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
            if datetime.now().hour == 3 and datetime.now().minute == 0:
                backup_database()
                await asyncio.sleep(60)  # Ждем минуту чтобы не повторять
        except Exception as e:
            logging.error(f"Ошибка в фоновой задаче: {e}")
            await asyncio.sleep(300)  # Ждем 5 минут при ошибке
        
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

# ========== ОБНОВЛЕННЫЕ ОБРАБОТЧИКИ ==========

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

# Обработка кнопки "Назад"
@dp.message_handler(lambda message: message.text == "↩️ Назад")
async def cmd_back_simple(message: types.Message, state: FSMContext):
    current_state = await state.get_state()
    if current_state:
        await state.finish()
    await message.answer("↩️ Возврат в главное меню", reply_markup=get_main_keyboard())

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
    # Проверяем лимит фильтров
    if check_filters_limit(message.from_user.id):
        await message.answer(
            f"❌ <b>Достигнут лимит фильтров!</b>\n\n"
            f"💡 <i>Максимальное количество фильтров: {MAX_FILTERS_PER_USER}</i>\n"
            f"📊 <i>Удалите некоторые фильтры перед добавлением новых</i>",
            parse_mode='HTML',
            reply_markup=get_main_keyboard()
        )
        return
        
    await message.answer(
        "🔧 <b>Выберите тип добавления:</b>\n\n"
        "💡 <i>Можно добавить один фильтр или сразу несколько</i>",
        parse_mode='HTML',
        reply_markup=get_add_filter_keyboard()
    )

# Обработка выбора типа добавления (УБРАНА КНОПКА "БЫСТРЫЕ НАБОРЫ")
@dp.message_handler(lambda message: message.text in ["🔧 Один фильтр", "📦 Несколько фильтров"])
async def process_add_type(message: types.Message, state: FSMContext):
    if message.text == "🔧 Один фильтр":
        await FilterStates.waiting_filter_type.set()
        await message.answer(
            "🔧 <b>Выберите тип фильтра:</b>\n\n"
            "💡 <i>Доступны все варианты фильтров</i>",
            parse_mode='HTML',
            reply_markup=get_filter_type_keyboard()  # Теперь показывает ВСЕ фильтры
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
            "💡 <i>Можно выбрать ВСЕ фильтры из списка</i>\n"
            "• Нажимайте кнопки для добавления фильтров\n"
            "• Можно добавить свои варианты через '📝 Другой тип'\n"
            "• Нажмите '✅ Готово' когда закончите\n\n"
            "📝 <b>Текущий список:</b>\n"
            "<i>Пока пусто</i>",
            parse_mode='HTML',
            reply_markup=get_multiple_filters_keyboard()  # Теперь показывает ВСЕ фильтры
        )

# Обработка выбора типа фильтра для ОДНОГО фильтра
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
    
    # Обработка всех типов фильтров из списка
    filter_mapping = {
        "🔧 Магистральный SL10": "Магистральный SL10",
        "🔧 Магистральный SL20": "Магистральный SL20",
        "💧 Гейзер Престиж": "Гейзер Престиж",
        "💧 Аквафор Кристалл": "Аквафор Кристалл",
        "⚡ Угольный картридж": "Угольный картридж",
        "🧽 Механический фильтр": "Механический фильтр"
    }
    
    filter_name = None
    
    if message.text in filter_mapping:
        filter_name = filter_mapping[message.text]
    else:
        # Пользователь ввел свой вариант
        try:
            filter_name = validate_filter_name(message.text)
        except ValueError as e:
            await message.answer(
                f"❌ <b>Ошибка в названии фильтра:</b>\n\n"
                f"💡 <i>{str(e)}</i>",
                parse_mode='HTML',
                reply_markup=get_filter_type_keyboard()
            )
            return
    
    if filter_name:
        async with state.proxy() as data:
            data['filter_type'] = filter_name
            data['lifetime'] = get_lifetime_by_type(filter_name)

        await FilterStates.next()
        await message.answer(
            "📍 <b>Укажите место установки фильтра:</b>\n\n"
            "💡 <i>Нажмите кнопку ниже чтобы указать место</i>",
            parse_mode='HTML',
            reply_markup=get_location_keyboard()  # Упрощенная клавиатура
        )

# Обработка кнопок при выборе НЕСКОЛЬКИХ фильтров
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
        
        # Проверяем общий лимит фильтров
        current_filters_count = len(get_user_filters(message.from_user.id))
        if current_filters_count + len(data['selected_filters']) > MAX_FILTERS_PER_USER:
            await message.answer(
                f"❌ <b>Превышен лимит фильтров!</b>\n\n"
                f"📊 <i>Текущее количество: {current_filters_count}</i>\n"
                f"📦 <i>Пытаетесь добавить: {len(data['selected_filters'])}</i>\n"
                f"💡 <i>Максимум: {MAX_FILTERS_PER_USER}</i>\n\n"
                f"🔄 <i>Удалите некоторые фильтры или уменьшите список</i>",
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
            reply_markup=get_location_keyboard()  # Упрощенная клавиатура
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
    
    # Обработка кнопки "Другой тип" для нескольких фильтров
    if message.text == "📝 Другой тип":
        await message.answer(
            "📝 <b>Введите тип фильтра:</b>\n\n"
            "💡 <i>Можно ввести несколько фильтров через запятую</i>\n"
            "<i>Например: Угольный фильтр, Механический фильтр, Сетчатый фильтр</i>",
            parse_mode='HTML',
            reply_markup=get_cancel_keyboard()
        )
        return
    
    # Обработка ВСЕХ фильтров из списка для нескольких фильтров
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
            try:
                validated_filter = validate_filter_name(new_filter)
                if validated_filter and validated_filter not in data['selected_filters']:
                    data['selected_filters'].append(validated_filter)
                    added_count += 1
            except ValueError as e:
                await message.answer(
                    f"❌ <b>Ошибка в фильтре '{new_filter}':</b>\n\n"
                    f"💡 <i>{str(e)}</i>",
                    parse_mode='HTML',
                    reply_markup=get_multiple_filters_keyboard()
                )
                return
        
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

# ========== НОВЫЕ ОБРАБОТЧИКИ: УДАЛЕНИЕ ФИЛЬТРОВ ==========

@dp.message_handler(lambda message: message.text == "🗑️ Удалить")
async def cmd_delete_filter(message: types.Message):
    """Начало процесса удаления фильтра"""
    filters = get_user_filters(message.from_user.id)
    
    if not filters:
        await message.answer(
            "📭 <b>Нет фильтров для удаления</b>\n\n"
            "💫 <i>Добавьте фильтры перед использованием этой функции</i>",
            parse_mode='HTML',
            reply_markup=get_main_keyboard()
        )
        return
    
    await DeleteFilterStates.waiting_filter_selection.set()
    
    filters_list = "\n".join([f"• #{f['id']} {f['filter_type']} - {f['location']}" for f in filters])
    
    await message.answer(
        f"🗑️ <b>УДАЛЕНИЕ ФИЛЬТРА</b>\n\n"
        f"📋 <b>Ваши фильтры:</b>\n{filters_list}\n\n"
        f"🔢 <b>Выберите фильтр для удаления:</b>\n"
        f"<i>Нажмите на соответствующий номер фильтра</i>",
        parse_mode='HTML',
        reply_markup=get_filters_list_keyboard(filters, "delete")
    )

@dp.message_handler(state=DeleteFilterStates.waiting_filter_selection)
async def process_delete_filter_selection(message: types.Message, state: FSMContext):
    if message.text == "🔙 Главное меню":
        await state.finish()
        await message.answer("🏠 Возврат в главное меню", reply_markup=get_main_keyboard())
        return
    
    # Парсим ID фильтра из текста (формат: #ID Тип - Место)
    match = re.match(r'#(\d+)', message.text)
    if match:
        filter_id = int(match.group(1))
        filter_data = get_filter_by_id(filter_id, message.from_user.id)
        
        if filter_data:
            async with state.proxy() as data:
                data['filter_to_delete'] = filter_data
            
            expiry_date_nice = format_date_nice(datetime.strptime(str(filter_data['expiry_date']), '%Y-%m-%d').date())
            
            await message.answer(
                f"❓ <b>ПОДТВЕРЖДЕНИЕ УДАЛЕНИЯ</b>\n\n"
                f"🔧 <b>Тип:</b> {filter_data['filter_type']}\n"
                f"📍 <b>Место:</b> {filter_data['location']}\n"
                f"📅 <b>Годен до:</b> {expiry_date_nice}\n\n"
                f"⚠️ <b>Вы уверены что хотите удалить этот фильтр?</b>\n"
                f"<i>Это действие нельзя отменить</i>",
                parse_mode='HTML',
                reply_markup=get_confirmation_keyboard(filter_id, "delete")
            )
        else:
            await message.answer(
                "❌ <b>Фильтр не найден!</b>\n\n"
                "💡 <i>Выберите фильтр из списка</i>",
                parse_mode='HTML'
            )
    else:
        await message.answer(
            "❌ <b>Неверный формат!</b>\n\n"
            "💡 <i>Выберите фильтр из списка кнопок</i>",
            parse_mode='HTML'
        )

@dp.callback_query_handler(lambda c: c.data.startswith('confirm_delete_'), state='*')
async def process_confirm_delete(callback_query: types.CallbackQuery, state: FSMContext):
    filter_id = int(callback_query.data.split('_')[2])
    user_id = callback_query.from_user.id
    
    # Удаляем фильтр из БД
    with get_db_connection() as conn:
        cur = conn.cursor()
        cur.execute("DELETE FROM filters WHERE id = ? AND user_id = ?", (filter_id, user_id))
        conn.commit()
    
    await callback_query.message.edit_text(
        "✅ <b>ФИЛЬТР УДАЛЕН</b>\n\n"
        "🗑️ <i>Фильтр успешно удален из вашего списка</i>",
        parse_mode='HTML'
    )
    
    await state.finish()
    await bot.send_message(
        user_id,
        "🏠 Возврат в главное меню",
        reply_markup=get_main_keyboard()
    )

@dp.callback_query_handler(lambda c: c.data == 'cancel_delete', state='*')
async def process_cancel_delete(callback_query: types.CallbackQuery, state: FSMContext):
    await callback_query.message.edit_text(
        "🚫 <b>УДАЛЕНИЕ ОТМЕНЕНО</b>\n\n"
        "💡 <i>Фильтр не был удален</i>",
        parse_mode='HTML'
    )
    
    await state.finish()
    await bot.send_message(
        callback_query.from_user.id,
        "🏠 Возврат в главное меню",
        reply_markup=get_main_keyboard()
    )

# ========== НОВЫЕ ОБРАБОТЧИКИ: РЕДАКТИРОВАНИЕ ФИЛЬТРОВ ==========

@dp.message_handler(lambda message: message.text == "✏️ Редактировать")
async def cmd_edit_filter(message: types.Message):
    """Начало процесса редактирования фильтра"""
    filters = get_user_filters(message.from_user.id)
    
    if not filters:
        await message.answer(
            "📭 <b>Нет фильтров для редактирования</b>\n\n"
            "💫 <i>Добавьте фильтры перед использованием этой функции</i>",
            parse_mode='HTML',
            reply_markup=get_main_keyboard()
        )
        return
    
    await EditFilterStates.waiting_filter_selection.set()
    
    filters_list = "\n".join([f"• #{f['id']} {f['filter_type']} - {f['location']}" for f in filters])
    
    await message.answer(
        f"✏️ <b>РЕДАКТИРОВАНИЕ ФИЛЬТРА</b>\n\n"
        f"📋 <b>Ваши фильтры:</b>\n{filters_list}\n\n"
        f"🔢 <b>Выберите фильтр для редактирования:</b>\n"
        f"<i>Нажмите на соответствующий номер фильтра</i>",
        parse_mode='HTML',
        reply_markup=get_filters_list_keyboard(filters, "edit")
    )

@dp.message_handler(state=EditFilterStates.waiting_filter_selection)
async def process_edit_filter_selection(message: types.Message, state: FSMContext):
    if message.text == "🔙 Главное меню":
        await state.finish()
        await message.answer("🏠 Возврат в главное меню", reply_markup=get_main_keyboard())
        return
    
    if message.text == "🔙 К списку фильтров":
        filters = get_user_filters(message.from_user.id)
        filters_list = "\n".join([f"• #{f['id']} {f['filter_type']} - {f['location']}" for f in filters])
        
        await message.answer(
            f"✏️ <b>РЕДАКТИРОВАНИЕ ФИЛЬТРА</b>\n\n"
            f"📋 <b>Ваши фильтры:</b>\n{filters_list}\n\n"
            f"🔢 <b>Выберите фильтр для редактирования:</b>",
            parse_mode='HTML',
            reply_markup=get_filters_list_keyboard(filters, "edit")
        )
        return
    
    # Парсим ID фильтра из текста (формат: #ID Тип - Место)
    match = re.match(r'#(\d+)', message.text)
    if match:
        filter_id = int(match.group(1))
        filter_data = get_filter_by_id(filter_id, message.from_user.id)
        
        if filter_data:
            async with state.proxy() as data:
                data['editing_filter'] = filter_data
            
            expiry_date_nice = format_date_nice(datetime.strptime(str(filter_data['expiry_date']), '%Y-%m-%d').date())
            last_change_nice = format_date_nice(datetime.strptime(str(filter_data['last_change']), '%Y-%m-%d').date())
            
            await EditFilterStates.waiting_field_selection.set()
            await message.answer(
                f"✏️ <b>РЕДАКТИРОВАНИЕ ФИЛЬТРА</b>\n\n"
                f"🔧 <b>Тип:</b> {filter_data['filter_type']}\n"
                f"📍 <b>Место:</b> {filter_data['location']}\n"
                f"📅 <b>Заменен:</b> {last_change_nice}\n"
                f"⏱️ <b>Срок службы:</b> {filter_data['lifetime_days']} дн.\n"
                f"🗓️ <b>Годен до:</b> {expiry_date_nice}\n\n"
                f"🔄 <b>Что вы хотите изменить?</b>",
                parse_mode='HTML',
                reply_markup=get_edit_filter_keyboard()
            )
        else:
            await message.answer(
                "❌ <b>Фильтр не найден!</b>\n\n"
                "💡 <i>Выберите фильтр из списка</i>",
                parse_mode='HTML'
            )
    else:
        await message.answer(
            "❌ <b>Неверный формат!</b>\n\n"
            "💡 <i>Выберите фильтр из списка кнопок</i>",
            parse_mode='HTML'
        )

@dp.message_handler(state=EditFilterStates.waiting_field_selection)
async def process_edit_field_selection(message: types.Message, state: FSMContext):
    if message.text == "🔙 Главное меню":
        await state.finish()
        await message.answer("🏠 Возврат в главное меню", reply_markup=get_main_keyboard())
        return
    
    if message.text == "🔙 К списку фильтров":
        await EditFilterStates.waiting_filter_selection.set()
        filters = get_user_filters(message.from_user.id)
        filters_list = "\n".join([f"• #{f['id']} {f['filter_type']} - {f['location']}" for f in filters])
        
        await message.answer(
            f"✏️ <b>РЕДАКТИРОВАНИЕ ФИЛЬТРА</b>\n\n"
            f"📋 <b>Ваши фильтры:</b>\n{filters_list}\n\n"
            f"🔢 <b>Выберите фильтр для редактирования:</b>",
            parse_mode='HTML',
            reply_markup=get_filters_list_keyboard(filters, "edit")
        )
        return
    
    async with state.proxy() as data:
        filter_data = data['editing_filter']
    
    if message.text == "✏️ Тип фильтра":
        await EditFilterStates.waiting_new_value.set()
        data['editing_field'] = 'filter_type'
        await message.answer(
            f"✏️ <b>ИЗМЕНЕНИЕ ТИПА ФИЛЬТРА</b>\n\n"
            f"🔧 <b>Текущий тип:</b> {filter_data['filter_type']}\n\n"
            f"📝 <b>Введите новый тип фильтра:</b>",
            parse_mode='HTML',
            reply_markup=get_cancel_keyboard()
        )
    
    elif message.text == "📍 Место установки":
        await EditFilterStates.waiting_new_value.set()
        data['editing_field'] = 'location'
        await message.answer(
            f"✏️ <b>ИЗМЕНЕНИЕ МЕСТА УСТАНОВКИ</b>\n\n"
            f"📍 <b>Текущее место:</b> {filter_data['location']}\n\n"
            f"📝 <b>Введите новое место установки:</b>",
            parse_mode='HTML',
            reply_markup=get_cancel_keyboard()
        )
    
    elif message.text == "📅 Дата замены":
        await EditFilterStates.waiting_new_value.set()
        data['editing_field'] = 'last_change'
        last_change_nice = format_date_nice(datetime.strptime(str(filter_data['last_change']), '%Y-%m-%d').date())
        await message.answer(
            f"✏️ <b>ИЗМЕНЕНИЕ ДАТЫ ЗАМЕНЫ</b>\n\n"
            f"📅 <b>Текущая дата замены:</b> {last_change_nice}\n\n"
            f"📝 <b>Введите новую дату замены (ДД.ММ.ГГ):</b>",
            parse_mode='HTML',
            reply_markup=get_cancel_keyboard()
        )
    
    elif message.text == "⏱️ Срок службы":
        await EditFilterStates.waiting_new_value.set()
        data['editing_field'] = 'lifetime_days'
        await message.answer(
            f"✏️ <b>ИЗМЕНЕНИЕ СРОКА СЛУЖБЫ</b>\n\n"
            f"⏱️ <b>Текущий срок службы:</b> {filter_data['lifetime_days']} дней\n\n"
            f"📝 <b>Введите новый срок службы в днях:</b>",
            parse_mode='HTML',
            reply_markup=get_cancel_keyboard()
        )

@dp.message_handler(state=EditFilterStates.waiting_new_value)
async def process_edit_new_value(message: types.Message, state: FSMContext):
    if message.text == "❌ Отмена":
        await EditFilterStates.waiting_field_selection.set()
        async with state.proxy() as data:
            filter_data = data['editing_filter']
        
        expiry_date_nice = format_date_nice(datetime.strptime(str(filter_data['expiry_date']), '%Y-%m-%d').date())
        last_change_nice = format_date_nice(datetime.strptime(str(filter_data['last_change']), '%Y-%m-%d').date())
        
        await message.answer(
            f"✏️ <b>РЕДАКТИРОВАНИЕ ФИЛЬТРА</b>\n\n"
            f"🔧 <b>Тип:</b> {filter_data['filter_type']}\n"
            f"📍 <b>Место:</b> {filter_data['location']}\n"
            f"📅 <b>Заменен:</b> {last_change_nice}\n"
            f"⏱️ <b>Срок службы:</b> {filter_data['lifetime_days']} дн.\n"
            f"🗓️ <b>Годен до:</b> {expiry_date_nice}\n\n"
            f"🔄 <b>Что вы хотите изменить?</b>",
            parse_mode='HTML',
            reply_markup=get_edit_filter_keyboard()
        )
        return
    
    async with state.proxy() as data:
        filter_data = data['editing_filter']
        field = data['editing_field']
        filter_id = filter_data['id']
    
    try:
        if field == 'filter_type':
            new_value = validate_filter_name(message.text)
            update_query = "UPDATE filters SET filter_type = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?"
        
        elif field == 'location':
            new_value = safe_db_string(message.text)
            update_query = "UPDATE filters SET location = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?"
        
        elif field == 'last_change':
            new_value = parse_date(message.text)
            # При изменении даты замены пересчитываем expiry_date
            lifetime = filter_data['lifetime_days']
            new_expiry_date = new_value + timedelta(days=lifetime)
            update_query = "UPDATE filters SET last_change = ?, expiry_date = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?"
            new_value = [new_value, new_expiry_date, filter_id]
        
        elif field == 'lifetime_days':
            new_value = validate_lifetime(message.text)
            # При изменении срока службы пересчитываем expiry_date
            last_change = datetime.strptime(str(filter_data['last_change']), '%Y-%m-%d').date()
            new_expiry_date = last_change + timedelta(days=new_value)
            update_query = "UPDATE filters SET lifetime_days = ?, expiry_date = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?"
            new_value = [new_value, new_expiry_date, filter_id]
        
        # Выполняем обновление в БД
        with get_db_connection() as conn:
            cur = conn.cursor()
            if isinstance(new_value, list):
                cur.execute(update_query, new_value)
            else:
                cur.execute(update_query, (new_value, filter_id))
            conn.commit()
        
        field_names = {
            'filter_type': 'тип фильтра',
            'location': 'место установки',
            'last_change': 'дата замены',
            'lifetime_days': 'срок службы'
        }
        
        await message.answer(
            f"✅ <b>ИЗМЕНЕНИЯ СОХРАНЕНЫ!</b>\n\n"
            f"✏️ <b>Изменено поле:</b> {field_names[field]}\n"
            f"🔧 <b>Фильтр:</b> #{filter_id}\n\n"
            f"💫 <i>Данные успешно обновлены</i>",
            parse_mode='HTML',
            reply_markup=get_main_keyboard()
        )
        await state.finish()
        
    except ValueError as e:
        await message.answer(
            f"❌ <b>Ошибка в данных!</b>\n\n"
            f"💡 <i>{str(e)}</i>\n\n"
            f"📝 <i>Попробуйте ввести данные еще раз</i>",
            parse_mode='HTML',
            reply_markup=get_cancel_keyboard()
        )

# ========== ОБНОВЛЕННЫЕ ОБРАБОТЧИКИ: СРОКИ ЗАМЕНЫ С ИНФОГРАФИКОЙ ==========

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
    ok_filters = []
    
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
        else:
            ok_filters.append(f"✅ {f['filter_type']} ({f['location']}) - осталось {days_until_expiry} дн. (до {expiry_date_nice})")

    # Создаем инфографику
    infographic = create_expiry_infographic(filters)
    
    response = f"{infographic}\n\n"
    response += "⏳ <b>ДЕТАЛЬНЫЙ КОНТРОЛЬ СРОКОВ</b>\n\n"
    
    if expired_filters:
        response += "🚨 <b>ПРОСРОЧЕНЫ:</b>\n" + "\n".join(expired_filters) + "\n\n"
    
    if expiring_soon:
        response += "⚠️ <b>СРОЧНО ИСТЕКАЮТ:</b>\n" + "\n".join(expiring_soon) + "\n\n"
    
    if warning_filters:
        response += "🔔 <b>СКОРО ИСТЕКАЮТ:</b>\n" + "\n".join(warning_filters) + "\n\n"
    
    if ok_filters:
        response += "💧 <b>В НОРМЕ:</b>\n" + "\n".join(ok_filters[:10])  # Показываем только первые 10
        if len(ok_filters) > 10:
            response += f"\n... и еще {len(ok_filters) - 10} фильтров в норме"

    await message.answer(response, parse_mode='HTML', reply_markup=get_main_keyboard())

# ========== ОБНОВЛЕННЫЕ ОБРАБОТЧИКИ: МЕСТО УСТАНОВКИ ==========

# Общий обработчик для места установки
@dp.message_handler(state=[FilterStates.waiting_location, MultipleFiltersStates.waiting_location])
async def process_location(message: types.Message, state: FSMContext):
    if message.text == "❌ Отмена":
        await state.finish()
        await message.answer("🚫 Добавление отменено", reply_markup=get_main_keyboard())
        return
        
    if message.text == "📍 Указать место установки":
        await message.answer(
            "📍 <b>Введите место установки фильтра:</b>\n\n"
            "💡 <i>Например: Кухня, Ванная комната, Под раковиной, Гостиная, Офис, Балкон, Гараж и т.д.</i>",
            parse_mode='HTML',
            reply_markup=get_cancel_keyboard()
        )
        return
    
    current_state = await state.get_state()
    
    if current_state == FilterStates.waiting_location.state:
        # Для одного фильтра
        async with state.proxy() as data:
            data['location'] = safe_db_string(message.text)

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
            data['location'] = safe_db_string(message.text)

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

# ========== ОСТАЛЬНЫЕ ОБРАБОТЧИКИ ==========

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
    )
