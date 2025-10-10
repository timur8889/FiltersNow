import logging
import sqlite3
import os
from datetime import datetime, timedelta

# ДОБАВЛЕНО: Импорт и загрузка переменных окружения
from dotenv import load_dotenv
load_dotenv()  # Загружает переменные из .env файла

from aiogram import Bot, Dispatcher, types
from aiogram.contrib.fsm_storage.memory import MemoryStorage
from aiogram.dispatcher import FSMContext
from aiogram.dispatcher.filters.state import State, StatesGroup
from aiogram.utils import executor

# Настройки
# ИЗМЕНЕНО: Токен теперь берется из переменной окружения
API_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
ADMIN_ID = 5024165375

# ДОБАВЛЕНО: Проверка токена при запуске
if not API_TOKEN or API_TOKEN == '8278600298:AAGPjUhyU5HxXOaLRvu-FSRldBW_UCmwOME':
    logging.error("Токен бота не настроен! Установите переменную окружения TELEGRAM_BOT_TOKEN")
    logging.error("Создайте файл .env с содержанием: TELEGRAM_BOT_TOKEN=ваш_токен")
    exit(1)

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('bot.log', encoding='utf-8'),
        logging.StreamHandler()
    ]
)

# Стандартные сроки службы фильтров
DEFAULT_LIFETIMES = {
    "магистральный sl10": 180,
    "магистральный sl20": 180,
    "гейзер": 365,
    "аквафор": 365
}

# Инициализация бота
bot = Bot(token=API_TOKEN)
storage = MemoryStorage()
dp = Dispatcher(bot, storage=storage)

# Функции для работы с БД
def get_db_connection():
    """Создает соединение с базой данных"""
    try:
        conn = sqlite3.connect('filters.db')
        conn.row_factory = sqlite3.Row  # Для доступа к колонкам по имени
        return conn
    except sqlite3.Error as e:
        logging.error(f"Ошибка подключения к БД: {e}")
        raise

def init_db():
    """Инициализация базы данных"""
    try:
        conn = get_db_connection()
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
        logging.info("База данных инициализирована")
    except sqlite3.Error as e:
        logging.error(f"Ошибка инициализации БД: {e}")
    finally:
        if conn:
            conn.close()

def get_days_until_expiry(expiry_date):
    """Вычисляет количество дней до истечения срока"""
    try:
        if isinstance(expiry_date, str):
            expiry_date = datetime.strptime(expiry_date, '%Y-%m-%d').date()
        return (expiry_date - datetime.now().date()).days
    except (ValueError, TypeError) as e:
        logging.error(f"Ошибка вычисления дней до истечения: {e}")
        return 0

def get_status_icon(days_until_expiry):
    """Возвращает иконку и текст статуса в зависимости от дней до истечения"""
    if days_until_expiry <= 0:
        return "🔴", "ПРОСРОЧЕН"
    elif days_until_expiry <= 7:
        return "🟡", "СРОЧНО"
    elif days_until_expiry <= 30:
        return "🔔", "СКОРО"
    else:
        return "✅", "В НОРМЕ"

# States
class FilterStates(StatesGroup):
    waiting_filter_type = State()
    waiting_location = State()
    waiting_change_date = State()
    waiting_lifetime = State()

class EditFilterStates(StatesGroup):
    waiting_filter_selection = State()
    waiting_field_selection = State()
    waiting_new_value = State()

# Клавиатуры
def get_main_keyboard():
    keyboard = types.ReplyKeyboardMarkup(resize_keyboard=True)
    keyboard.row(
        types.KeyboardButton("📊 Мои фильтры"),
        types.KeyboardButton("➕ Новый фильтр")
    )
    keyboard.row(
        types.KeyboardButton("⏰ Сроки замены"),
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
        types.KeyboardButton("📈 Статистика"),
        types.KeyboardButton("🔙 Назад")
    )
    return keyboard

def get_cancel_keyboard():
    keyboard = types.ReplyKeyboardMarkup(resize_keyboard=True)
    keyboard.add(types.KeyboardButton("🔙 Отмена"))
    return keyboard

def get_back_keyboard():
    keyboard = types.ReplyKeyboardMarkup(resize_keyboard=True)
    keyboard.add(types.KeyboardButton("🔙 Назад"))
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
    keyboard.row(types.KeyboardButton("🔙 Отмена"))
    return keyboard

def get_location_keyboard():
    keyboard = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    keyboard.row(
        types.KeyboardButton("🏠 Кухня"),
        types.KeyboardButton("🚿 Ванная")
    )
    keyboard.row(
        types.KeyboardButton("🛋️ Гостиная"),
        types.KeyboardButton("🚰 Под раковиной")
    )
    keyboard.row(types.KeyboardButton("📍 Другое место"))
    keyboard.row(types.KeyboardButton("🔙 Отмена"))
    return keyboard

def get_lifetime_keyboard():
    keyboard = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=3)
    keyboard.row(
        types.KeyboardButton("📅 90 дней"),
        types.KeyboardButton("📅 180 дней"),
        types.KeyboardButton("📅 365 дней")
    )
    keyboard.row(types.KeyboardButton("🔙 Отмена"))
    return keyboard

def get_edit_field_keyboard():
    keyboard = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    keyboard.row(
        types.KeyboardButton("🔧 Тип фильтра"),
        types.KeyboardButton("📍 Место установки")
    )
    keyboard.row(
        types.KeyboardButton("📅 Дата замены"),
        types.KeyboardButton("⏰ Срок службы")
    )
    keyboard.row(types.KeyboardButton("🔙 Назад к фильтрам"))
    return keyboard

def get_confirmation_keyboard(filter_id):
    keyboard = types.InlineKeyboardMarkup(row_width=2)
    keyboard.add(
        types.InlineKeyboardButton("✅ Да, удалить", callback_data=f"confirm_delete_{filter_id}"),
        types.InlineKeyboardButton("❌ Нет, отменить", callback_data="cancel_delete")
    )
    return keyboard

# Определение срока службы по типу фильтра
def get_lifetime_by_type(filter_type):
    """Определяет стандартный срок службы по типу фильтра"""
    filter_type_lower = filter_type.lower()
    for key, days in DEFAULT_LIFETIMES.items():
        if key in filter_type_lower:
            return days
    return 180

# Команда start
@dp.message_handler(commands=['start'])
async def cmd_start(message: types.Message):
    await message.answer(
        "🤖 <b>Бот для учета замены фильтров</b>\n\n"
        "💧 <i>Никогда не забывайте о своевременной замене фильтров!</i>\n\n"
        "📊 <b>Основные функции:</b>\n"
        "• 📊 Мои фильтры - просмотр всех фильтров\n"
        "• ➕ Новый фильтр - добавить новый фильтр\n"
        "• ⏰ Сроки замены - проверить истекающие фильтры\n"
        "• ⚙️ Управление - редактирование и удаление",
        parse_mode='HTML',
        reply_markup=get_main_keyboard()
    )

# Обработка кнопки "Назад"
@dp.message_handler(lambda message: message.text == "🔙 Назад")
async def cmd_back(message: types.Message):
    await message.answer(
        "🔙 Возвращаемся в главное меню:",
        reply_markup=get_main_keyboard()
    )

# Обработка кнопки "Управление"
@dp.message_handler(lambda message: message.text == "⚙️ Управление")
async def cmd_management(message: types.Message):
    await message.answer(
        "⚙️ <b>Управление фильтрами</b>\n\n"
        "Выберите действие:",
        parse_mode='HTML',
        reply_markup=get_management_keyboard()
    )

# Добавление фильтра
@dp.message_handler(lambda message: message.text == "➕ Новый фильтр")
@dp.message_handler(commands=['add'])
async def cmd_add(message: types.Message):
    await FilterStates.waiting_filter_type.set()
    await message.answer(
        "🔧 <b>Выберите тип фильтра:</b>\n\n"
        "💡 <i>Или выберите 'Другой тип' для ручного ввода</i>",
        parse_mode='HTML',
        reply_markup=get_filter_type_keyboard()
    )

@dp.message_handler(state=FilterStates.waiting_filter_type)
async def process_filter_type(message: types.Message, state: FSMContext):
    if message.text == "🔙 Отмена":
        await state.finish()
        await message.answer("❌ Добавление фильтра отменено", reply_markup=get_main_keyboard())
        return
        
    if message.text == "📝 Другой тип":
        await message.answer(
            "📝 <b>Введите свой вариант типа фильтра:</b>",
            parse_mode='HTML',
            reply_markup=get_cancel_keyboard()
        )
        return
    
    async with state.proxy() as data:
        data['filter_type'] = message.text
        data['lifetime'] = get_lifetime_by_type(message.text)

    await FilterStates.next()
    await message.answer(
        "📍 <b>Выберите место установки фильтра:</b>\n\n"
        "🏠 <i>Где установлен этот фильтр?</i>",
        parse_mode='HTML',
        reply_markup=get_location_keyboard()
    )

@dp.message_handler(state=FilterStates.waiting_location)
async def process_location(message: types.Message, state: FSMContext):
    if message.text == "🔙 Отмена":
        await state.finish()
        await message.answer("❌ Добавление фильтра отменено", reply_markup=get_main_keyboard())
        return
        
    if message.text == "📍 Другое место":
        await message.answer(
            "📍 <b>Введите свое место установки:</b>",
            parse_mode='HTML',
            reply_markup=get_cancel_keyboard()
        )
        return
    
    async with state.proxy() as data:
        data['location'] = message.text

    await FilterStates.next()
    await message.answer(
        f"📅 <b>Срок службы для '{data['filter_type']}':</b> {data['lifetime']} дней\n\n"
        f"📝 <b>Введите дату последней замены (ГГГГ-ММ-ДД):</b>\n"
        f"<i>Например: {datetime.now().strftime('%Y-%m-%d')}</i>",
        parse_mode='HTML',
        reply_markup=get_cancel_keyboard()
    )

@dp.message_handler(state=FilterStates.waiting_change_date)
async def process_date(message: types.Message, state: FSMContext):
    if message.text == "🔙 Отмена":
        await state.finish()
        await message.answer("❌ Добавление фильтра отменено", reply_markup=get_main_keyboard())
        return
        
    try:
        change_date = datetime.strptime(message.text, '%Y-%m-%d').date()
        
        async with state.proxy() as data:
            data['change_date'] = change_date
            
        await FilterStates.next()
        await message.answer(
            f"⏰ <b>Установить срок службы {data['lifetime']} дней?</b>\n\n"
            f"📅 <i>Или введите другое количество дней:</i>",
            parse_mode='HTML',
            reply_markup=get_lifetime_keyboard()
        )
        
    except ValueError:
        await message.answer(
            "❌ <b>Неверный формат даты!</b>\n\n"
            "📝 <i>Используйте формат ГГГГ-ММ-ДД</i>\n"
            f"<i>Например: {datetime.now().strftime('%Y-%m-%d')}</i>",
            parse_mode='HTML',
            reply_markup=get_cancel_keyboard()
        )

@dp.message_handler(state=FilterStates.waiting_lifetime)
async def process_lifetime(message: types.Message, state: FSMContext):
    if message.text == "🔙 Отмена":
        await state.finish()
        await message.answer("❌ Добавление фильтра отменено", reply_markup=get_main_keyboard())
        return
        
    try:
        async with state.proxy() as data:
            change_date = data['change_date']
            filter_type = data['filter_type']
            location = data['location']
            
            if message.text.endswith("дней"):
                lifetime = int(message.text.split()[1])
            else:
                lifetime = int(message.text)
            
            expiry_date = change_date + timedelta(days=lifetime)
            
            conn = get_db_connection()
            cur = conn.cursor()
            cur.execute('''INSERT INTO filters 
                        (user_id, filter_type, location, last_change, expiry_date, lifetime_days) 
                        VALUES (?, ?, ?, ?, ?, ?)''',
                       (message.from_user.id, filter_type, location, change_date, expiry_date, lifetime))
            conn.commit()
            conn.close()

            days_until_expiry = get_days_until_expiry(expiry_date)
            status_icon, status_text = get_status_icon(days_until_expiry)
            
            await message.answer(
                f"✅ <b>Фильтр успешно добавлен!</b>\n\n"
                f"🔧 <b>Тип:</b> {filter_type}\n"
                f"📍 <b>Место:</b> {location}\n"
                f"📅 <b>Дата замены:</b> {change_date}\n"
                f"⏰ <b>Срок службы:</b> {lifetime} дней\n"
                f"📅 <b>Годен до:</b> {expiry_date}\n"
                f"⏳ <b>Осталось дней:</b> {days_until_expiry}\n"
                f"📊 <b>Статус:</b> {status_icon} {status_text}",
                parse_mode='HTML',
                reply_markup=get_main_keyboard()
            )
            await state.finish()
            
    except ValueError:
        await message.answer(
            "❌ <b>Неверный формат!</b>\n\n"
            "📝 <i>Введите количество дней числом</i>",
            parse_mode='HTML',
            reply_markup=get_lifetime_keyboard()
        )
    except Exception as e:
        logging.error(f"Ошибка при добавлении фильтра: {e}")
        await message.answer(
            "❌ <b>Произошла ошибка при добавлении фильтра</b>",
            parse_mode='HTML',
            reply_markup=get_main_keyboard()
        )
        await state.finish()

# Список фильтров
@dp.message_handler(lambda message: message.text == "📊 Мои фильтры")
@dp.message_handler(commands=['list'])
async def cmd_list(message: types.Message):
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("SELECT id, filter_type, location, last_change, expiry_date, lifetime_days FROM filters WHERE user_id = ?", 
                    (message.from_user.id,))
        filters = cur.fetchall()
        conn.close()

        if not filters:
            await message.answer(
                "📭 <b>Список фильтров пуст</b>\n\n"
                "💡 <i>Добавьте первый фильтр с помощью кнопки '➕ Новый фильтр'</i>",
                parse_mode='HTML',
                reply_markup=get_main_keyboard()
            )
            return

        response = "📊 <b>Ваши фильтры:</b>\n\n"
        
        for f in filters:
            days_until_expiry = get_days_until_expiry(f['expiry_date'])
            status_icon, status_text = get_status_icon(days_until_expiry)
            
            response += (
                f"🆔 <b>ID:</b> {f['id']}\n"
                f"🔧 <b>Тип:</b> {f['filter_type']}\n"
                f"📍 <b>Место:</b> {f['location']}\n"
                f"📅 <b>Заменен:</b> {f['last_change']}\n"
                f"⏰ <b>Срок:</b> {f['lifetime_days']} дней\n"
                f"📅 <b>Годен до:</b> {f['expiry_date']}\n"
                f"⏳ <b>Осталось дней:</b> {days_until_expiry}\n"
                f"📢 <b>Статус:</b> {status_icon} {status_text}\n\n"
            )

        await message.answer(response, parse_mode='HTML', reply_markup=get_main_keyboard())
    
    except Exception as e:
        logging.error(f"Ошибка при получении списка фильтров: {e}")
        await message.answer(
            "❌ <b>Произошла ошибка при получении списка фильтров</b>",
            parse_mode='HTML',
            reply_markup=get_main_keyboard()
        )

# Проверка сроков
@dp.message_handler(lambda message: message.text == "⏰ Сроки замены")
@dp.message_handler(commands=['check'])
async def cmd_check(message: types.Message):
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("SELECT filter_type, location, expiry_date FROM filters WHERE user_id = ?", 
                    (message.from_user.id,))
        filters = cur.fetchall()
        conn.close()

        if not filters:
            await message.answer(
                "📭 <b>Нет фильтров для проверки</b>\n\n"
                "💡 <i>Добавьте фильтры для отслеживания сроков</i>",
                parse_mode='HTML',
                reply_markup=get_main_keyboard()
            )
            return

        today = datetime.now().date()
        expired_filters = []
        expiring_soon = []
        warning_filters = []
        
        for f in filters:
            days_until_expiry = get_days_until_expiry(f['expiry_date'])
            
            if days_until_expiry <= 0:
                expired_filters.append(f"🔴 {f['filter_type']} ({f['location']}) - просрочен {abs(days_until_expiry)} дней назад")
            elif days_until_expiry <= 7:
                expiring_soon.append(f"🟡 {f['filter_type']} ({f['location']}) - осталось {days_until_expiry} дней")
            elif days_until_expiry <= 30:
                warning_filters.append(f"🔔 {f['filter_type']} ({f['location']}) - осталось {days_until_expiry} дней")

        response = "⏰ <b>Проверка сроков фильтров:</b>\n\n"
        
        if expired_filters:
            response += "🔴 <b>ПРОСРОЧЕНЫ:</b>\n" + "\n".join(expired_filters) + "\n\n"
        
        if expiring_soon:
            response += "🟡 <b>СРОЧНО ИСТЕКАЮТ:</b>\n" + "\n".join(expiring_soon) + "\n\n"
        
        if warning_filters:
            response += "🔔 <b>СКОРО ИСТЕКАЮТ:</b>\n" + "\n".join(warning_filters) + "\n\n"
        
        if not expired_filters and not expiring_soon and not warning_filters:
            response += "✅ <b>Все фильтры в норме!</b>\n\n"
            response += "💡 <i>Следующая проверка через 30+ дней</i>"

        await message.answer(response, parse_mode='HTML', reply_markup=get_main_keyboard())
    
    except Exception as e:
        logging.error(f"Ошибка при проверке сроков: {e}")
        await message.answer(
            "❌ <b>Произошла ошибка при проверке сроков</b>",
            parse_mode='HTML',
            reply_markup=get_main_keyboard()
        )

# Редактирование фильтра - выбор фильтра
@dp.message_handler(lambda message: message.text == "✏️ Редактировать")
@dp.message_handler(commands=['edit'])
async def cmd_edit(message: types.Message):
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("SELECT id, filter_type, location, expiry_date FROM filters WHERE user_id = ?", 
                    (message.from_user.id,))
        filters = cur.fetchall()
        conn.close()

        if not filters:
            await message.answer(
                "❌ <b>Нет фильтров для редактирования</b>",
                parse_mode='HTML',
                reply_markup=get_management_keyboard()
            )
            return

        keyboard = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=1)
        for f in filters:
            days_until_expiry = get_days_until_expiry(f['expiry_date'])
            status_icon, _ = get_status_icon(days_until_expiry)
            
            keyboard.add(types.KeyboardButton(
                f"{status_icon} {f['filter_type']} | {f['location']} | до {f['expiry_date']}"
            ))
        
        keyboard.add(types.KeyboardButton("🔙 Назад в управление"))

        await EditFilterStates.waiting_filter_selection.set()
        await message.answer(
            "✏️ <b>Выберите фильтр для редактирования:</b>\n\n"
            "💡 <i>Статусы: 🔴 - просрочен, 🟡 - скоро истекает, ✅ - в норме</i>",
            parse_mode='HTML',
            reply_markup=keyboard
        )
    
    except Exception as e:
        logging.error(f"Ошибка при начале редактирования: {e}")
        await message.answer(
            "❌ <b>Произошла ошибка при загрузке фильтров</b>",
            parse_mode='HTML',
            reply_markup=get_management_keyboard()
        )

# Редактирование фильтра - выбор поля
@dp.message_handler(state=EditFilterStates.waiting_filter_selection)
async def process_edit_filter_selection(message: types.Message, state: FSMContext):
    if message.text == "🔙 Назад в управление":
        await state.finish()
        await message.answer(
            "⚙️ <b>Управление фильтрами</b>\n\n"
            "Выберите действие:",
            parse_mode='HTML',
            reply_markup=get_management_keyboard()
        )
        return

    try:
        conn = get_db_connection()
        cur = conn.cursor()
        
        parts = message.text.split(' | ')
        if len(parts) >= 3:
            filter_type = parts[0][2:].strip()  # Убираем эмодзи статуса
            location = parts[1]
            expiry_date = parts[2][3:].strip()  # Убираем "до "
            
            cur.execute("SELECT id FROM filters WHERE user_id = ? AND filter_type = ? AND location = ? AND expiry_date = ?", 
                       (message.from_user.id, filter_type, location, expiry_date))
            result = cur.fetchone()
            
            if result:
                filter_id = result['id']
                
                async with state.proxy() as data:
                    data['edit_filter_id'] = filter_id
                    data['edit_filter_info'] = (filter_type, location, expiry_date)
                
                await EditFilterStates.next()
                await message.answer(
                    f"✏️ <b>Редактирование фильтра:</b>\n\n"
                    f"🔧 <b>Тип:</b> {filter_type}\n"
                    f"📍 <b>Место:</b> {location}\n"
                    f"📅 <b>Срок годности:</b> {expiry_date}\n\n"
                    f"📝 <b>Выберите поле для редактирования:</b>",
                    parse_mode='HTML',
                    reply_markup=get_edit_field_keyboard()
                )
            else:
                await message.answer(
                    "❌ <b>Фильтр не найден</b>",
                    parse_mode='HTML',
                    reply_markup=get_management_keyboard()
                )
        else:
            await message.answer(
                "❌ <b>Не удалось распознать фильтр</b>",
                parse_mode='HTML',
                reply_markup=get_management_keyboard()
            )
        
        conn.close()
        
    except Exception as e:
        logging.error(f"Ошибка при выборе фильтра для редактирования: {e}")
        await message.answer(
            "❌ <b>Произошла ошибка при выборе фильтра</b>",
            parse_mode='HTML',
            reply_markup=get_management_keyboard()
        )
        await state.finish()

# Редактирование фильтра - обработка выбора поля
@dp.message_handler(state=EditFilterStates.waiting_field_selection)
async def process_edit_field_selection(message: types.Message, state: FSMContext):
    if message.text == "🔙 Назад к фильтрам":
        await EditFilterStates.waiting_filter_selection.set()
        await cmd_edit(message)
        return

    async with state.proxy() as data:
        filter_id = data['edit_filter_id']
        filter_info = data['edit_filter_info']
    
    field_mapping = {
        "🔧 Тип фильтра": "filter_type",
        "📍 Место установки": "location", 
        "📅 Дата замены": "last_change",
        "⏰ Срок службы": "lifetime_days"
    }
    
    if message.text in field_mapping:
        field = field_mapping[message.text]
        async with state.proxy() as data:
            data['edit_field'] = field
        
        if field == "filter_type":
            await message.answer(
                "🔧 <b>Введите новый тип фильтра:</b>",
                parse_mode='HTML',
                reply_markup=get_filter_type_keyboard()
            )
        elif field == "location":
            await message.answer(
                "📍 <b>Введите новое место установки:</b>",
                parse_mode='HTML',
                reply_markup=get_location_keyboard()
            )
        elif field == "last_change":
            await message.answer(
                "📅 <b>Введите новую дату замены (ГГГГ-ММ-ДД):</b>\n"
                f"<i>Например: {datetime.now().strftime('%Y-%m-%d')}</i>",
                parse_mode='HTML',
                reply_markup=get_cancel_keyboard()
            )
        elif field == "lifetime_days":
            await message.answer(
                "⏰ <b>Введите новый срок службы (в днях):</b>",
                parse_mode='HTML',
                reply_markup=get_lifetime_keyboard()
            )
        
        await EditFilterStates.next()
    else:
        await message.answer(
            "❌ <b>Пожалуйста, выберите поле из списка</b>",
            parse_mode='HTML',
            reply_markup=get_edit_field_keyboard()
        )

# Редактирование фильтра - обработка нового значения
@dp.message_handler(state=EditFilterStates.waiting_new_value)
async def process_edit_new_value(message: types.Message, state: FSMContext):
    if message.text == "🔙 Отмена":
        await state.finish()
        await message.answer(
            "❌ <b>Редактирование отменено</b>",
            parse_mode='HTML',
            reply_markup=get_management_keyboard()
        )
        return

    async with state.proxy() as data:
        filter_id = data['edit_filter_id']
        field = data['edit_field']
    
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        
        if field == "filter_type":
            new_value = message.text
            cur.execute("UPDATE filters SET filter_type = ? WHERE id = ?", 
                       (new_value, filter_id))
            
            await message.answer(
                f"✅ <b>Тип фильтра успешно изменен на:</b> {new_value}",
                parse_mode='HTML',
                reply_markup=get_management_keyboard()
            )
            
        elif field == "location":
            new_value = message.text
            cur.execute("UPDATE filters SET location = ? WHERE id = ?", 
                       (new_value, filter_id))
            
            await message.answer(
                f"✅ <b>Место установки успешно изменено на:</b> {new_value}",
                parse_mode='HTML',
                reply_markup=get_management_keyboard()
            )
            
        elif field == "last_change":
            try:
                new_date = datetime.strptime(message.text, '%Y-%m-%d').date()
                
                cur.execute("SELECT lifetime_days FROM filters WHERE id = ?", (filter_id,))
                lifetime = cur.fetchone()['lifetime_days']
                
                new_expiry = new_date + timedelta(days=lifetime)
                
                cur.execute("UPDATE filters SET last_change = ?, expiry_date = ? WHERE id = ?",
                           (new_date, new_expiry, filter_id))
                
                await message.answer(
                    f"✅ <b>Дата замены успешно изменена на:</b> {new_date}\n"
                    f"📅 <b>Новая дата истечения:</b> {new_expiry}",
                    parse_mode='HTML',
                    reply_markup=get_management_keyboard()
                )
                
            except ValueError:
                await message.answer(
                    "❌ <b>Неверный формат даты!</b>\n\n"
                    "📝 <i>Используйте формат ГГГГ-ММ-ДД</i>",
                    parse_mode='HTML',
                    reply_markup=get_cancel_keyboard()
                )
                return
                
        elif field == "lifetime_days":
            try:
                if message.text.endswith("дней"):
                    new_lifetime = int(message.text.split()[1])
                else:
                    new_lifetime = int(message.text)
                
                cur.execute("SELECT last_change FROM filters WHERE id = ?", (filter_id,))
                last_change = cur.fetchone()['last_change']
                
                new_expiry = last_change + timedelta(days=new_lifetime)
                
                cur.execute("UPDATE filters SET lifetime_days = ?, expiry_date = ? WHERE id = ?",
                           (new_lifetime, new_expiry, filter_id))
                
                await message.answer(
                    f"✅ <b>Срок службы успешно изменен на:</b> {new_lifetime} дней\n"
                    f"📅 <b>Новая дата истечения:</b> {new_expiry}",
                    parse_mode='HTML',
                    reply_markup=get_management_keyboard()
                )
                
            except ValueError:
                await message.answer(
                    "❌ <b>Неверный формат!</b>\n\n"
                    "📝 <i>Введите количество дней числом</i>",
                    parse_mode='HTML',
                    reply_markup=get_lifetime_keyboard()
                )
                return
        
        conn.commit()
        conn.close()
        await state.finish()
        
    except Exception as e:
        logging.error(f"Ошибка при редактировании фильтра: {e}")
        await message.answer(
            "❌ <b>Произошла ошибка при редактировании фильтра</b>",
            parse_mode='HTML',
            reply_markup=get_management_keyboard()
        )
        await state.finish()

# Удаление фильтра
@dp.message_handler(lambda message: message.text == "🗑️ Удалить")
@dp.message_handler(commands=['delete'])
async def cmd_delete(message: types.Message):
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("SELECT id, filter_type, location, expiry_date FROM filters WHERE user_id = ?", 
                    (message.from_user.id,))
        filters = cur.fetchall()
        conn.close()

        if not filters:
            await message.answer(
                "❌ <b>Нет фильтров для удаления</b>",
                parse_mode='HTML',
                reply_markup=get_management_keyboard()
            )
            return

        keyboard = types.InlineKeyboardMarkup(row_width=1)
        for f in filters:
            days_until_expiry = get_days_until_expiry(f['expiry_date'])
            status_icon, _ = get_status_icon(days_until_expiry)
            
            keyboard.add(types.InlineKeyboardButton(
                f"{status_icon} {f['filter_type']} | {f['location']} | до {f['expiry_date']}",
                callback_data=f"select_delete_{f['id']}"
            ))
        
        keyboard.add(types.InlineKeyboardButton("🔙 Назад", callback_data="back_to_management"))

        await message.answer(
            "🗑️ <b>Выберите фильтр для удаления:</b>\n\n"
            "💡 <i>Статусы: 🔴 - просрочен, 🟡 - скоро истекает, ✅ - в норме</i>",
            parse_mode='HTML',
            reply_markup=keyboard
        )
    
    except Exception as e:
        logging.error(f"Ошибка при удалении фильтра: {e}")
        await message.answer(
            "❌ <b>Произошла ошибка при загрузке фильтров</b>",
            parse_mode='HTML',
            reply_markup=get_management_keyboard()
        )

# Подтверждение удаления
@dp.callback_query_handler(lambda c: c.data and c.data.startswith('select_delete_'))
async def confirm_delete(callback_query: types.CallbackQuery):
    filter_id = callback_query.data.split('_')[2]

    try:
        conn = get_db_connection()
        cur = conn.cursor()
        
        cur.execute("SELECT filter_type, location, expiry_date FROM filters WHERE id = ? AND user_id = ?",
                    (filter_id, callback_query.from_user.id))
        filter_info = cur.fetchone()
        conn.close()
        
        if filter_info:
            days_until_expiry = get_days_until_expiry(filter_info['expiry_date'])
            status_icon, status_text = get_status_icon(days_until_expiry)
            
            await callback_query.message.edit_text(
                f"⚠️ <b>Подтверждение удаления</b>\n\n"
                f"🔧 <b>Тип:</b> {filter_info['filter_type']}\n"
                f"📍 <b>Место:</b> {filter_info['location']}\n"
                f"📅 <b>Срок годности:</b> {filter_info['expiry_date']}\n"
                f"⏳ <b>Осталось дней:</b> {days_until_expiry}\n"
                f"📊 <b>Статус:</b> {status_icon} {status_text}\n\n"
                f"❓ <b>Вы уверены, что хотите удалить этот фильтр?</b>",
                parse_mode='HTML',
                reply_markup=get_confirmation_keyboard(filter_id)
            )
        else:
            await callback_query.answer("❌ Фильтр не найден", show_alert=True)
    
    except Exception as e:
        logging.error(f"Ошибка при подтверждении удаления: {e}")
        await callback_query.answer("❌ Произошла ошибка", show_alert=True)

# Фактическое удаление фильтра
@dp.callback_query_handler(lambda c: c.data and c.data.startswith('confirm_delete_'))
async def process_delete(callback_query: types.CallbackQuery):
    filter_id = callback_query.data.split('_')[2]

    try:
        conn = get_db_connection()
        cur = conn.cursor()
        
        cur.execute("SELECT filter_type, location, expiry_date FROM filters WHERE id = ? AND user_id = ?",
                    (filter_id, callback_query.from_user.id))
        filter_info = cur.fetchone()
        
        if filter_info:
            cur.execute("DELETE FROM filters WHERE id = ? AND user_id = ?",
                        (filter_id, callback_query.from_user.id))
            conn.commit()
            conn.close()
            
            await callback_query.message.edit_text(
                f"✅ <b>Фильтр удален:</b>\n\n"
                f"🔧 <b>Тип:</b> {filter_info['filter_type']}\n"
                f"📍 <b>Место:</b> {filter_info['location']}\n"
                f"📅 <b>Срок истекал:</b> {filter_info['expiry_date']}",
                parse_mode='HTML'
            )
        else:
            await callback_query.answer("❌ Фильтр не найден", show_alert=True)
            conn.close()
    
    except Exception as e:
        logging.error(f"Ошибка при удалении фильтра: {e}")
        await callback_query.answer("❌ Произошла ошибка при удалении", show_alert=True)

# Отмена удаления
@dp.callback_query_handler(lambda c: c.data == "cancel_delete")
async def cancel_delete(callback_query: types.CallbackQuery):
    await callback_query.message.edit_text(
        "❌ <b>Удаление отменено</b>\n\n"
        "💡 <i>Фильтр не был удален</i>",
        parse_mode='HTML'
    )

@dp.callback_query_handler(lambda c: c.data == "back_to_management")
async def back_to_management(callback_query: types.CallbackQuery):
    await callback_query.message.edit_text(
        "⚙️ <b>Управление фильтрами</b>\n\n"
        "Выберите действие:",
        parse_mode='HTML'
    )

@dp.callback_query_handler(lambda c: c.data == "back_to_main")
async def back_to_main(callback_query: types.CallbackQuery):
    await callback_query.message.edit_text(
        "🔙 Возвращаемся в главное меню",
        parse_mode='HTML'
    )

# Статистика
@dp.message_handler(lambda message: message.text == "📈 Статистика")
async def cmd_stats(message: types.Message):
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        
        cur.execute('''SELECT 
                        COUNT(*) as total,
                        SUM(CASE WHEN expiry_date < date('now') THEN 1 ELSE 0 END) as expired,
                        SUM(CASE WHEN expiry_date BETWEEN date('now') AND date('now', '+7 days') THEN 1 ELSE 0 END) as urgent,
                        SUM(CASE WHEN expiry_date BETWEEN date('now', '+8 days') AND date('now', '+30 days') THEN 1 ELSE 0 END) as soon
                     FROM filters WHERE user_id = ?''', (message.from_user.id,))
        stats = cur.fetchone()
        
        cur.execute('''SELECT filter_type, COUNT(*) as count 
                       FROM filters WHERE user_id = ? GROUP BY filter_type''', 
                    (message.from_user.id,))
        type_stats = cur.fetchall()
        
        conn.close()
        
        response = "📈 <b>Статистика фильтров</b>\n\n"
        response += f"📊 <b>Всего фильтров:</b> {stats['total']}\n"
        response += f"🔴 <b>Просрочено:</b> {stats['expired']}\n"
        response += f"🟡 <b>Срочно заменить:</b> {stats['urgent']}\n"
        response += f"🔔 <b>Скоро истекают:</b> {stats['soon']}\n\n"
        
        if type_stats:
            response += "<b>По типам:</b>\n"
            for filter_type, count in type_stats:
                response += f"  • {filter_type['filter_type']}: {filter_type['count']} шт.\n"
    
        await message.answer(response, parse_mode='HTML', reply_markup=get_management_keyboard())
    
    except Exception as e:
        logging.error(f"Ошибка при получении статистики: {e}")
        await message.answer(
            "❌ <b>Произошла ошибка при получении статистики</b>",
            parse_mode='HTML',
            reply_markup=get_management_keyboard()
        )

# Обработка отмены
@dp.message_handler(lambda message: message.text == "🔙 Отмена", state='*')
async def cmd_cancel(message: types.Message, state: FSMContext):
    current_state = await state.get_state()
    if current_state is None:
        return
    
    await state.finish()
    await message.answer("❌ Действие отменено", reply_markup=get_main_keyboard())

# Обработка других сообщений
@dp.message_handler()
async def handle_other_messages(message: types.Message):
    await message.answer(
        "🤖 <b>Бот для учета замены фильтров</b>\n\n"
        "💧 <i>Выберите действие с помощью кнопок ниже:</i>",
        parse_mode='HTML',
        reply_markup=get_main_keyboard()
    )

# Обработка ошибок
@dp.errors_handler()
async def errors_handler(update: types.Update, exception: Exception):
    logging.error(f"Произошла ошибка: {exception}", exc_info=True)
    return True

# Запуск бота
if __name__ == '__main__':
    try:
        init_db()
        logging.info("Бот запускается...")
        executor.start_polling(dp, skip_updates=True)
    except Exception as e:
        logging.error(f"Ошибка при запуске бота: {e}")
