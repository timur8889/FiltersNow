import logging
import sqlite3
import os
import asyncio
import shutil
import traceback
from datetime import datetime, timedelta
from collections import defaultdict
from aiogram import Bot, Dispatcher, types
from aiogram.contrib.fsm_storage.memory import MemoryStorage
from aiogram.dispatcher import FSMContext
from aiogram.dispatcher.filters.state import State, StatesGroup
from aiogram.utils import executor
from dotenv import load_dotenv

# Загрузка переменных окружения
load_dotenv()

# Класс конфигурации
class Config:
    def __init__(self):
        self.API_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
        self.ADMIN_ID = int(os.getenv('ADMIN_ID', '5024165375'))
        self.BACKUP_KEEP_COUNT = 7
        self.CHECK_INTERVAL = 3600  # 1 час
        self.RATE_LIMIT = 10  # запросов в минуту
        self.MESSAGE_CHUNK_SIZE = 4096  # Максимальный размер сообщения Telegram
        
        if not self.API_TOKEN:
            raise ValueError("TELEGRAM_BOT_TOKEN не установлен")

# Инициализация конфигурации
try:
    config = Config()
except ValueError as e:
    logging.error(f"Ошибка конфигурации: {e}")
    exit(1)

# Стандартные сроки службы фильтров
DEFAULT_LIFETIMES = {
    "магистральный sl10": 180,
    "магистральный sl20": 180,
    "гейзер": 365,
    "аквафор": 365
}

# Инициализация бота
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
bot = Bot(token=config.API_TOKEN)
storage = MemoryStorage()
dp = Dispatcher(bot, storage=storage)

# Словарь для отслеживания запросов (защита от спама)
user_requests = defaultdict(list)

# Инициализация БД
def init_db():
    conn = sqlite3.connect('filters.db')
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
    conn.close()

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
        for old_backup in backups[:-config.BACKUP_KEEP_COUNT]:
            os.remove(os.path.join(backup_dir, old_backup))
            logging.info(f"Удалена старая резервная копия: {old_backup}")
            
    except Exception as e:
        logging.error(f"Ошибка при создании резервной копии: {e}")

# Функции для улучшенной безопасности и обработки
def is_rate_limited(user_id, limit=config.RATE_LIMIT, period=60):
    """Проверка ограничения запросов"""
    now = datetime.now()
    user_requests[user_id] = [req for req in user_requests[user_id] 
                             if now - req < timedelta(seconds=period)]
    
    if len(user_requests[user_id]) >= limit:
        return True
    
    user_requests[user_id].append(now)
    return False

def log_user_action(user_id, action, details=""):
    """Логирование действий пользователя"""
    logging.info(f"User {user_id}: {action} - {details}")

async def safe_send_message(chat_id, text, **kwargs):
    """Безопасная отправка сообщений с ограничением длины"""
    if len(text) > config.MESSAGE_CHUNK_SIZE:
        parts = []
        current_part = ""
        
        # Разделяем по строкам чтобы не обрывать сообщение посередине строки
        lines = text.split('\n')
        
        for line in lines:
            if len(current_part + line + '\n') <= config.MESSAGE_CHUNK_SIZE:
                current_part += line + '\n'
            else:
                if current_part:
                    parts.append(current_part.strip())
                current_part = line + '\n'
        
        if current_part:
            parts.append(current_part.strip())
        
        for i, part in enumerate(parts):
            if i == len(parts) - 1:
                # Последняя часть с оригинальной разметкой
                await bot.send_message(chat_id, part, **kwargs)
            else:
                # Промежуточные части без разметки чтобы избежать ошибок
                await bot.send_message(chat_id, part, parse_mode=None)
            await asyncio.sleep(0.1)
    else:
        await bot.send_message(chat_id, text, **kwargs)

def safe_parse_date(date_str):
    """Безопасное преобразование даты с дополнительными проверками"""
    try:
        # Пробуем разные форматы дат
        formats = ['%d.%m.%y', '%d.%m.%Y', '%d-%m-%y', '%d-%m-%Y']
        
        for fmt in formats:
            try:
                date = datetime.strptime(date_str, fmt).date()
                break
            except ValueError:
                continue
        else:
            raise ValueError("Неверный формат даты")
        
        # Проверяем, что дата не в будущем (если это замена)
        if date > datetime.now().date():
            raise ValueError("Дата замены не может быть в будущем")
        
        # Проверяем разумность даты (не старше 10 лет)
        if date < (datetime.now().date() - timedelta(days=3650)):
            raise ValueError("Дата слишком старая")
        
        return date
        
    except Exception as e:
        raise ValueError(f"Ошибка преобразования даты: {e}")

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

# Клавиатуры
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

def get_add_filter_keyboard():
    keyboard = types.ReplyKeyboardMarkup(resize_keyboard=True)
    keyboard.row(
        types.KeyboardButton("🔧 Один фильтр"),
        types.KeyboardButton("📦 Несколько фильтров")
    )
    keyboard.row(types.KeyboardButton("🔙 Главное меню"))
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

# Функция для форматирования даты в красивый вид
def format_date_nice(date):
    return date.strftime('%d.%m.%y')

# Функция для проверки просроченных фильтров
async def check_expired_filters():
    """Фоновая задача для проверки просроченных фильтров"""
    try:
        conn = sqlite3.connect('filters.db')
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
        conn.close()
        
        notified_users = set()
        
        # Уведомления о скором истечении срока
        for user_id, filter_type, location, expiry_date in expiring_filters:
            try:
                days_until_expiry = (datetime.strptime(str(expiry_date), '%Y-%m-%d').date() - datetime.now().date()).days
                expiry_date_nice = format_date_nice(datetime.strptime(str(expiry_date), '%Y-%m-%d').date())
                
                await safe_send_message(
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
                    
                    await safe_send_message(
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
        await safe_send_message(
            config.ADMIN_ID,
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
        await asyncio.sleep(config.CHECK_INTERVAL)

async def on_startup(dp):
    """Действия при запуске бота"""
    logging.info("Бот запущен")
    
    # Создаем резервную копию при запуске
    backup_database()
    
    # Запускаем фоновую задачу
    asyncio.create_task(schedule_daily_check())
    
    # Уведомляем администратора о запуске
    try:
        await safe_send_message(config.ADMIN_ID, "🤖 Бот успешно запущен и работает")
    except Exception as e:
        logging.error(f"Не удалось отправить уведомление администратору: {e}")

# Универсальный обработчик отмены
async def cancel_handler(message: types.Message, state: FSMContext):
    """Универсальный обработчик отмены"""
    current_state = await state.get_state()
    if current_state is not None:
        await state.finish()
        log_user_action(message.from_user.id, "cancelled_action", f"state: {current_state}")
    
    await safe_send_message(
        message.chat.id,
        "🚫 Действие отменено", 
        reply_markup=get_main_keyboard()
    )

# Мидлварь для проверки ограничения запросов
@dp.middleware_handler()
async def rate_limit_middleware(handler, event, data):
    """Мидлварь для ограничения запросов"""
    if hasattr(event, 'from_user') and event.from_user:
        user_id = event.from_user.id
        if is_rate_limited(user_id):
            if hasattr(event, 'message'):
                await safe_send_message(event.message.chat.id, "⚠️ Слишком много запросов. Подождите немного.")
            return
    return await handler(event, data)

# Команда start
@dp.message_handler(commands=['start'])
async def cmd_start(message: types.Message):
    log_user_action(message.from_user.id, "started_bot")
    await safe_send_message(
        message.chat.id,
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
    log_user_action(message.from_user.id, "main_menu")
    await safe_send_message(
        message.chat.id,
        "🏠 <b>Главное меню</b>\n\n"
        "Выберите нужный раздел:",
        parse_mode='HTML',
        reply_markup=get_main_keyboard()
    )

# Обработка кнопки "Управление"
@dp.message_handler(lambda message: message.text == "⚙️ Управление")
async def cmd_management(message: types.Message):
    log_user_action(message.from_user.id, "management_menu")
    await safe_send_message(
        message.chat.id,
        "🛠️ <b>Центр управления фильтрами</b>\n\n"
        "Выберите действие:",
        parse_mode='HTML',
        reply_markup=get_management_keyboard()
    )

# Определение срока службы по типу фильтра
def get_lifetime_by_type(filter_type):
    filter_type_lower = filter_type.lower()
    for key, days in DEFAULT_LIFETIMES.items():
        if key in filter_type_lower:
            return days
    return 180

# Добавление фильтра - выбор типа добавления
@dp.message_handler(lambda message: message.text == "✨ Добавить фильтр")
@dp.message_handler(commands=['add'])
async def cmd_add(message: types.Message):
    log_user_action(message.from_user.id, "started_add_filter")
    await safe_send_message(
        message.chat.id,
        "🔧 <b>Выберите тип добавления:</b>\n\n"
        "💡 <i>Можно добавить один фильтр или сразу несколько</i>",
        parse_mode='HTML',
        reply_markup=get_add_filter_keyboard()
    )

# Обработка выбора типа добавления
@dp.message_handler(lambda message: message.text in ["🔧 Один фильтр", "📦 Несколько фильтров"])
async def process_add_type(message: types.Message):
    log_user_action(message.from_user.id, "chose_add_type", message.text)
    if message.text == "🔧 Один фильтр":
        await FilterStates.waiting_filter_type.set()
        await safe_send_message(
            message.chat.id,
            "🔧 <b>Выберите тип фильтра:</b>\n\n"
            "💡 <i>Или укажите свой вариант</i>",
            parse_mode='HTML',
            reply_markup=get_filter_type_keyboard()
        )
    elif message.text == "📦 Несколько фильтров":
        await MultipleFiltersStates.waiting_filters_list.set()
        await safe_send_message(
            message.chat.id,
            "📦 <b>Добавление нескольких фильтров</b>\n\n"
            "📝 <b>Введите типы фильтров через запятую или с новой строки:</b>\n\n"
            "💡 <i>Примеры:</i>\n"
            "<i>• Магистральный SL10, Магистральный SL20, Гейзер</i>\n"
            "<i>• Магистральный SL10\nМагистральный SL20\nГейзер</i>\n\n"
            "🔧 <i>Каждый фильтр будет добавлен с одинаковыми настройками</i>",
            parse_mode='HTML',
            reply_markup=get_cancel_keyboard()
        )

# Добавление одного фильтра
@dp.message_handler(state=FilterStates.waiting_filter_type)
async def process_filter_type(message: types.Message, state: FSMContext):
    if message.text == "❌ Отмена":
        await cancel_handler(message, state)
        return
        
    if message.text == "📝 Другой тип":
        await safe_send_message(
            message.chat.id,
            "📝 <b>Введите тип фильтра:</b>\n"
            "<i>Например: Угольный фильтр, Механический фильтр и т.д.</i>",
            parse_mode='HTML',
            reply_markup=get_cancel_keyboard()
        )
        return
    
    async with state.proxy() as data:
        data['filter_type'] = message.text
        data['lifetime'] = get_lifetime_by_type(message.text)

    log_user_action(message.from_user.id, "selected_filter_type", message.text)
    await FilterStates.next()
    await safe_send_message(
        message.chat.id,
        "📍 <b>Укажите место установки фильтра:</b>\n\n"
        "💡 <i>Нажмите кнопку '📍 Другое место' для ввода своего варианта</i>",
        parse_mode='HTML',
        reply_markup=get_location_keyboard()
    )

# Добавление нескольких фильтров - обработка списка
@dp.message_handler(state=MultipleFiltersStates.waiting_filters_list)
async def process_multiple_filters_list(message: types.Message, state: FSMContext):
    if message.text == "❌ Отмена":
        await cancel_handler(message, state)
        return
    
    # Разделяем ввод на отдельные фильтры
    filter_text = message.text
    filters_list = []
    
    # Пробуем разделить по запятым
    if ',' in filter_text:
        filters_list = [f.strip() for f in filter_text.split(',') if f.strip()]
    else:
        # Или по переносам строк
        filters_list = [f.strip() for f in filter_text.split('\n') if f.strip()]
    
    if not filters_list:
        await safe_send_message(
            message.chat.id,
            "❌ <b>Не удалось распознать фильтры</b>\n\n"
            "💡 <i>Введите типы фильтров через запятую или с новой строки</i>",
            parse_mode='HTML',
            reply_markup=get_cancel_keyboard()
        )
        return
    
    # Сохраняем список фильтров
    async with state.proxy() as data:
        data['filters_list'] = filters_list
        # Устанавливаем срок службы по первому фильтру (можно будет изменить позже)
        data['lifetime'] = get_lifetime_by_type(filters_list[0])
    
    log_user_action(message.from_user.id, "entered_multiple_filters", f"count: {len(filters_list)}")
    await MultipleFiltersStates.next()
    await safe_send_message(
        message.chat.id,
        f"📦 <b>Будет добавлено фильтров:</b> {len(filters_list)}\n\n"
        f"🔧 <b>Список фильтров:</b>\n" + "\n".join([f"• {f}" for f in filters_list]) + "\n\n"
        f"📍 <b>Укажите место установки для всех фильтров:</b>\n\n"
        f"💡 <i>Все фильтры будут установлены в одном месте</i>",
        parse_mode='HTML',
        reply_markup=get_location_keyboard()
    )

# Общий обработчик для места установки (и для одного, и для нескольких фильтров)
@dp.message_handler(state=[FilterStates.waiting_location, MultipleFiltersStates.waiting_location])
async def process_location(message: types.Message, state: FSMContext):
    if message.text == "❌ Отмена":
        await cancel_handler(message, state)
        return
        
    if message.text == "📍 Другое место":
        await safe_send_message(
            message.chat.id,
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

        log_user_action(message.from_user.id, "selected_location", message.text)
        await FilterStates.next()
        today_nice = format_date_nice(datetime.now().date())
        await safe_send_message(
            message.chat.id,
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

        log_user_action(message.from_user.id, "selected_location_multiple", message.text)
        await MultipleFiltersStates.next()
        today_nice = format_date_nice(datetime.now().date())
        await safe_send_message(
            message.chat.id,
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
        await cancel_handler(message, state)
        return
        
    try:
        # Преобразуем дату из формата ДД.ММ.ГГ с дополнительными проверками
        change_date = safe_parse_date(message.text)
        
        current_state = await state.get_state()
        
        if current_state == "FilterStates:waiting_change_date":
            # Для одного фильтра
            async with state.proxy() as data:
                data['change_date'] = change_date
                
            log_user_action(message.from_user.id, "selected_change_date", str(change_date))
            await FilterStates.next()
            await safe_send_message(
                message.chat.id,
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
                
            log_user_action(message.from_user.id, "selected_change_date_multiple", str(change_date))
            await MultipleFiltersStates.next()
            await safe_send_message(
                message.chat.id,
                f"⏱️ <b>Срок службы для всех фильтров</b>\n\n"
                f"📅 <i>Рекомендуемый срок:</i> {data['lifetime']} дней\n\n"
                f"🔄 <b>Выберите срок службы:</b>",
                parse_mode='HTML',
                reply_markup=get_lifetime_keyboard()
            )
        
    except ValueError as e:
        today_nice = format_date_nice(datetime.now().date())
        await safe_send_message(
            message.chat.id,
            f"❌ <b>Неверный формат даты!</b>\n\n"
            f"📝 <i>{str(e)}</i>\n"
            f"<i>Пример: {today_nice}</i>",
            parse_mode='HTML',
            reply_markup=get_cancel_keyboard()
        )

# Общий обработчик для срока службы
@dp.message_handler(state=[FilterStates.waiting_lifetime, MultipleFiltersStates.waiting_lifetime])
async def process_lifetime(message: types.Message, state: FSMContext):
    if message.text == "❌ Отмена":
        await cancel_handler(message, state)
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
                    await safe_send_message(
                        message.chat.id,
                        "🔢 <b>Введите количество дней:</b>\n"
                        "<i>Например: 120, 200, 400 и т.д.</i>",
                        parse_mode='HTML',
                        reply_markup=get_cancel_keyboard()
                    )
                    return
                else:
                    lifetime = int(message.text)
                
                expiry_date = change_date + timedelta(days=lifetime)
                
                conn = sqlite3.connect('filters.db')
                cur = conn.cursor()
                cur.execute('''INSERT INTO filters 
                            (user_id, filter_type, location, last_change, expiry_date, lifetime_days) 
                            VALUES (?, ?, ?, ?, ?, ?)''',
                           (message.from_user.id, filter_type, location, change_date, expiry_date, lifetime))
                conn.commit()
                conn.close()

                days_until_expiry = (expiry_date - datetime.now().date()).days
                
                # Определяем статус с эмодзи
                if days_until_expiry <= 0:
                    status_icon = "🔴 ПРОСРОЧЕН"
                    status_emoji = "🚨"
                elif days_until_expiry <= 7:
                    status_icon = "🟡 СРОЧНО ЗАМЕНИТЬ"
                    status_emoji = "⚠️"
                elif days_until_expiry <= 30:
                    status_icon = "🔔 СКОРО ЗАМЕНИТЬ"
                    status_emoji = "🔔"
                else:
                    status_icon = "✅ В НОРМЕ"
                    status_emoji = "✅"
                
                change_date_nice = format_date_nice(change_date)
                expiry_date_nice = format_date_nice(expiry_date)
                
                log_user_action(message.from_user.id, "added_filter", f"{filter_type} at {location}")
                await safe_send_message(
                    message.chat.id,
                    f"{status_emoji} <b>ФИЛЬТР ДОБАВЛЕН!</b>\n\n"
                    f"🔧 <b>Тип:</b> {filter_type}\n"
                    f"📍 <b>Место:</b> {location}\n"
                    f"📅 <b>Заменен:</b> {change_date_nice}\n"
                    f"⏱️ <b>Срок службы:</b> {lifetime} дней\n"
                    f"📅 <b>Годен до:</b> {expiry_date_nice}\n"
                    f"⏳ <b>Осталось дней:</b> {days_until_expiry}\n"
                    f"📊 <b>Статус:</b> {status_icon}",
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
                    await safe_send_message(
                        message.chat.id,
                        "🔢 <b>Введите количество дней:</b>\n"
                        "<i>Например: 120, 200, 400 и т.д.</i>",
                        parse_mode='HTML',
                        reply_markup=get_cancel_keyboard()
                    )
                    return
                else:
                    lifetime = int(message.text)
                
                # Добавляем все фильтры в базу данных
                conn = sqlite3.connect('filters.db')
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
                conn.close()
                
                change_date_nice = format_date_nice(change_date)
                
                # Формируем сообщение с результатами
                response = f"✅ <b>УСПЕШНО ДОБАВЛЕНО {added_count} ФИЛЬТРОВ!</b>\n\n"
                response += f"📍 <b>Место:</b> {location}\n"
                response += f"📅 <b>Дата замены:</b> {change_date_nice}\n"
                response += f"⏱️ <b>Срок службы:</b> {lifetime} дней\n\n"
                
                response += "<b>📋 Добавленные фильтры:</b>\n"
                for i, result in enumerate(results, 1):
                    expiry_date_nice = format_date_nice(result['expiry_date'])
                    status_icon = "🔴" if result['days_until_expiry'] <= 0 else "🟡" if result['days_until_expiry'] <= 30 else "✅"
                    response += f"{status_icon} {result['type']} (до {expiry_date_nice})\n"
                
                log_user_action(message.from_user.id, "added_multiple_filters", f"count: {added_count}")
                await safe_send_message(
                    message.chat.id,
                    response,
                    parse_mode='HTML',
                    reply_markup=get_main_keyboard()
                )
                await state.finish()
            
    except ValueError:
        await safe_send_message(
            message.chat.id,
            "❌ <b>Неверный формат!</b>\n\n"
            "🔢 <i>Введите количество дней числом</i>\n"
            "<i>Например: 90, 180, 365</i>",
            parse_mode='HTML',
            reply_markup=get_lifetime_keyboard()
        )

# Список фильтров
@dp.message_handler(lambda message: message.text == "📋 Мои фильтры")
@dp.message_handler(commands=['list'])
async def cmd_list(message: types.Message):
    log_user_action(message.from_user.id, "viewed_filters_list")
    conn = sqlite3.connect('filters.db')
    cur = conn.cursor()
    cur.execute("SELECT id, filter_type, location, last_change, expiry_date, lifetime_days FROM filters WHERE user_id = ?", 
                (message.from_user.id,))
    filters = cur.fetchall()
    conn.close()

    if not filters:
        await safe_send_message(
            message.chat.id,
            "📭 <b>Список фильтров пуст</b>\n\n"
            "💫 <i>Добавьте первый фильтр с помощью кнопки '✨ Добавить фильтр'</i>",
            parse_mode='HTML',
            reply_markup=get_main_keyboard()
        )
        return

    response = "📋 <b>ВАШИ ФИЛЬТРЫ</b>\n\n"
    today = datetime.now().date()
    
    for f in filters:
        expiry_date = datetime.strptime(str(f[4]), '%Y-%m-%d').date()
        days_until_expiry = (expiry_date - today).days
        
        # Определяем иконку статуса
        if days_until_expiry <= 0:
            status_icon = "🔴"
            status_text = "ПРОСРОЧЕН"
        elif days_until_expiry <= 7:
            status_icon = "🟡"
            status_text = "СРОЧНО"
        elif days_until_expiry <= 30:
            status_icon = "🟠"
            status_text = "СКОРО"
        else:
            status_icon = "✅"
            status_text = "НОРМА"
        
        last_change_nice = format_date_nice(datetime.strptime(str(f[3]), '%Y-%m-%d').date())
        expiry_date_nice = format_date_nice(expiry_date)
        
        response += (
            f"{status_icon} <b>ФИЛЬТР #{f[0]}</b>\n"
            f"   🔧 {f[1]}\n"
            f"   📍 {f[2]}\n"
            f"   📅 Заменен: {last_change_nice}\n"
            f"   ⏱️ Срок: {f[5]} дн.\n"
            f"   🗓️ Годен до: {expiry_date_nice}\n"
            f"   ⏳ Осталось: {days_until_expiry} дн.\n"
            f"   📊 Статус: {status_text}\n\n"
        )

    await safe_send_message(message.chat.id, response, parse_mode='HTML', reply_markup=get_main_keyboard())

# Проверка сроков
@dp.message_handler(lambda message: message.text == "⏳ Сроки замены")
@dp.message_handler(commands=['check'])
async def cmd_check(message: types.Message):
    log_user_action(message.from_user.id, "checked_expiry_dates")
    conn = sqlite3.connect('filters.db')
    cur = conn.cursor()
    cur.execute("SELECT filter_type, location, expiry_date FROM filters WHERE user_id = ?", 
                (message.from_user.id,))
    filters = cur.fetchall()
    conn.close()

    if not filters:
        await safe_send_message(
            message.chat.id,
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
        expiry_date = datetime.strptime(str(f[2]), '%Y-%m-%d').date()
        days_until_expiry = (expiry_date - today).days
        
        expiry_date_nice = format_date_nice(expiry_date)
        
        if days_until_expiry <= 0:
            expired_filters.append(f"🔴 {f[0]} ({f[1]}) - просрочен {abs(days_until_expiry)} дн. назад (до {expiry_date_nice})")
        elif days_until_expiry <= 7:
            expiring_soon.append(f"🟡 {f[0]} ({f[1]}) - осталось {days_until_expiry} дн. (до {expiry_date_nice})")
        elif days_until_expiry <= 30:
            warning_filters.append(f"🟠 {f[0]} ({f[1]}) - осталось {days_until_expiry} дн. (до {expiry_date_nice})")

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

    await safe_send_message(message.chat.id, response, parse_mode='HTML', reply_markup=get_main_keyboard())

# Редактирование фильтра - выбор фильтра
@dp.message_handler(lambda message: message.text == "✏️ Редактировать")
@dp.message_handler(commands=['edit'])
async def cmd_edit(message: types.Message):
    log_user_action(message.from_user.id, "started_edit")
    conn = sqlite3.connect('filters.db')
    cur = conn.cursor()
    cur.execute("SELECT id, filter_type, location, expiry_date FROM filters WHERE user_id = ?", 
                (message.from_user.id,))
    filters = cur.fetchall()
    conn.close()

    if not filters:
        await safe_send_message(
            message.chat.id,
            "❌ <b>Нет фильтров для редактирования</b>",
            parse_mode='HTML',
            reply_markup=get_management_keyboard()
        )
        return

    keyboard = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=1)
    for f in filters:
        expiry_date = datetime.strptime(str(f[3]), '%Y-%m-%d').date()
        days_until_expiry = (expiry_date - datetime.now().date()).days
        
        if days_until_expiry <= 0:
            status = "🔴"
        elif days_until_expiry <= 7:
            status = "🟡"
        elif days_until_expiry <= 30:
            status = "🟠"
        else:
            status = "✅"
        
        # Создаем более читаемую кнопку
        button_text = f"{status} {f[1]} - {f[2]}"
        keyboard.add(types.KeyboardButton(button_text))
    
    keyboard.add(types.KeyboardButton("🔙 Главное меню"))

    await EditFilterStates.waiting_filter_selection.set()
    await safe_send_message(
        message.chat.id,
        "✏️ <b>Выберите фильтр для редактирования:</b>\n\n"
        "💡 <i>Статусы:\n🔴 - просрочен\n🟡 - срочно заменить\n🟠 - скоро истекает\n✅ - в норме</i>",
        parse_mode='HTML',
        reply_markup=keyboard
    )

# Редактирование фильтра - выбор фильтра из списка
@dp.message_handler(state=EditFilterStates.waiting_filter_selection)
async def process_edit_filter_selection(message: types.Message, state: FSMContext):
    if message.text == "🔙 Главное меню":
        await state.finish()
        await safe_send_message(
            message.chat.id,
            "🏠 <b>Главное меню</b>",
            parse_mode='HTML',
            reply_markup=get_main_keyboard()
        )
        return

    # Получаем текст выбранной кнопки (без эмодзи статуса)
    filter_text = message.text[2:].strip()  # Убираем эмодзи статуса и пробел
    
    try:
        conn = sqlite3.connect('filters.db')
        cur = conn.cursor()
        
        # Разделяем текст на тип фильтра и место
        if " - " in filter_text:
            parts = filter_text.split(" - ")
            filter_type = parts[0].strip()
            location = parts[1].strip()
            
            cur.execute("SELECT id FROM filters WHERE user_id = ? AND filter_type = ? AND location = ?", 
                       (message.from_user.id, filter_type, location))
            result = cur.fetchone()
            
            if result:
                filter_id = result[0]
                
                # Получаем полную информацию о фильтре
                cur.execute("SELECT * FROM filters WHERE id = ?", (filter_id,))
                filter_data = cur.fetchone()
                
                async with state.proxy() as data:
                    data['edit_filter_id'] = filter_id
                    data['edit_filter_data'] = filter_data
                
                # Показываем информацию о фильтре
                expiry_date = datetime.strptime(str(filter_data[5]), '%Y-%m-%d').date()
                days_until_expiry = (expiry_date - datetime.now().date()).days
                
                if days_until_expiry <= 0:
                    status_icon = "🔴"
                    status_text = "ПРОСРОЧЕН"
                elif days_until_expiry <= 7:
                    status_icon = "🟡"
                    status_text = "СРОЧНО"
                elif days_until_expiry <= 30:
                    status_icon = "🟠"
                    status_text = "СКОРО"
                else:
                    status_icon = "✅"
                    status_text = "НОРМА"
                
                last_change_nice = format_date_nice(datetime.strptime(str(filter_data[4]), '%Y-%m-%d').date())
                expiry_date_nice = format_date_nice(expiry_date)
                
                log_user_action(message.from_user.id, "selected_filter_for_edit", f"id: {filter_id}")
                await safe_send_message(
                    message.chat.id,
                    f"✏️ <b>РЕДАКТИРОВАНИЕ ФИЛЬТРА</b>\n\n"
                    f"{status_icon} <b>Текущие данные:</b>\n"
                    f"🔧 <b>Тип:</b> {filter_data[2]}\n"
                    f"📍 <b>Место:</b> {filter_data[3]}\n"
                    f"📅 <b>Дата замены:</b> {last_change_nice}\n"
                    f"⏱️ <b>Срок службы:</b> {filter_data[6]} дней\n"
                    f"🗓️ <b>Годен до:</b> {expiry_date_nice}\n"
                    f"⏳ <b>Осталось дней:</b> {days_until_expiry}\n"
                    f"📊 <b>Статус:</b> {status_text}\n\n"
                    f"📝 <b>Выберите поле для редактирования:</b>",
                    parse_mode='HTML',
                    reply_markup=get_edit_field_keyboard()
                )
                await EditFilterStates.next()
            else:
                await safe_send_message(
                    message.chat.id,
                    "❌ <b>Фильтр не найден</b>\n\n"
                    "💡 <i>Попробуйте выбрать фильтр из списка еще раз</i>",
                    parse_mode='HTML',
                    reply_markup=get_management_keyboard()
                )
                await state.finish()
        else:
            await safe_send_message(
                message.chat.id,
                "❌ <b>Не удалось распознать фильтр</b>\n\n"
                "💡 <i>Пожалуйста, выберите фильтр из списка кнопок</i>",
                parse_mode='HTML',
                reply_markup=get_management_keyboard()
            )
            await state.finish()
        
        conn.close()
        
    except Exception as e:
        logging.error(f"Ошибка при выборе фильтра для редактирования: {e}")
        await safe_send_message(
            message.chat.id,
            "❌ <b>Произошла ошибка при выборе фильтра</b>\n\n"
            "💡 <i>Попробуйте еще раз</i>",
            parse_mode='HTML',
            reply_markup=get_management_keyboard()
        )
        await state.finish()

# Редактирование фильтра - обработка выбора поля
@dp.message_handler(state=EditFilterStates.waiting_field_selection)
async def process_edit_field_selection(message: types.Message, state: FSMContext):
    if message.text == "↩️ Назад к фильтрам":
        await EditFilterStates.waiting_filter_selection.set()
        await cmd_edit(message)
        return

    async with state.proxy() as data:
        filter_id = data['edit_filter_id']
        filter_data = data['edit_filter_data']
    
    field_mapping = {
        "🔧 Тип": "filter_type",
        "📍 Место": "location", 
        "📅 Дата замены": "last_change",
        "⏱️ Срок службы": "lifetime_days"
    }
    
    if message.text in field_mapping:
        field = field_mapping[message.text]
        async with state.proxy() as data:
            data['edit_field'] = field
        
        # Запрашиваем новое значение в зависимости от поля
        if field == "filter_type":
            await safe_send_message(
                message.chat.id,
                f"🔧 <b>Текущий тип:</b> {filter_data[2]}\n\n"
                f"📝 <b>Введите новый тип фильтра:</b>",
                parse_mode='HTML',
                reply_markup=get_filter_type_keyboard()
            )
        elif field == "location":
            await safe_send_message(
                message.chat.id,
                f"📍 <b>Текущее место:</b> {filter_data[3]}\n\n"
                f"📝 <b>Введите новое место установки:</b>",
                parse_mode='HTML',
                reply_markup=get_location_keyboard()
            )
        elif field == "last_change":
            last_change_nice = format_date_nice(datetime.strptime(str(filter_data[4]), '%Y-%m-%d').date())
            today_nice = format_date_nice(datetime.now().date())
            await safe_send_message(
                message.chat.id,
                f"📅 <b>Текущая дата замены:</b> {last_change_nice}\n\n"
                f"📝 <b>Введите новую дату замены в формате ДД.ММ.ГГ:</b>\n"
                f"<i>Например: {today_nice}</i>",
                parse_mode='HTML',
                reply_markup=get_cancel_keyboard()
            )
        elif field == "lifetime_days":
            await safe_send_message(
                message.chat.id,
                f"⏱️ <b>Текущий срок службы:</b> {filter_data[6]} дней\n\n"
                f"📝 <b>Введите новый срок службы (в днях):</b>",
                parse_mode='HTML',
                reply_markup=get_lifetime_keyboard()
            )
        
        log_user_action(message.from_user.id, "selected_field_for_edit", field)
        await EditFilterStates.next()
    else:
        await safe_send_message(
            message.chat.id,
            "❌ <b>Пожалуйста, выберите поле из списка</b>",
            parse_mode='HTML',
            reply_markup=get_edit_field_keyboard()
        )

# Редактирование фильтра - обработка нового значения
@dp.message_handler(state=EditFilterStates.waiting_new_value)
async def process_edit_new_value(message: types.Message, state: FSMContext):
    if message.text == "❌ Отмена":
        await cancel_handler(message, state)
        return

    async with state.proxy() as data:
        filter_id = data['edit_filter_id']
        field = data['edit_field']
        old_filter_data = data['edit_filter_data']
    
    try:
        conn = sqlite3.connect('filters.db')
        cur = conn.cursor()
        
        if field == "filter_type":
            new_value = message.text
            cur.execute("UPDATE filters SET filter_type = ? WHERE id = ?", 
                       (new_value, filter_id))
            
            log_user_action(message.from_user.id, "edited_filter_type", f"id: {filter_id}, new: {new_value}")
            await safe_send_message(
                message.chat.id,
                f"✅ <b>Тип фильтра успешно изменен!</b>\n\n"
                f"🔧 <b>Было:</b> {old_filter_data[2]}\n"
                f"🔧 <b>Стало:</b> {new_value}",
                parse_mode='HTML',
                reply_markup=get_management_keyboard()
            )
            
        elif field == "location":
            new_value = message.text
            cur.execute("UPDATE filters SET location = ? WHERE id = ?", 
                       (new_value, filter_id))
            
            log_user_action(message.from_user.id, "edited_location", f"id: {filter_id}, new: {new_value}")
            await safe_send_message(
                message.chat.id,
                f"✅ <b>Место установки успешно изменено!</b>\n\n"
                f"📍 <b>Было:</b> {old_filter_data[3]}\n"
                f"📍 <b>Стало:</b> {new_value}",
                parse_mode='HTML',
                reply_markup=get_management_keyboard()
            )
            
        elif field == "last_change":
            try:
                # Преобразуем дату из формата ДД.ММ.ГГ с дополнительными проверками
                new_date = safe_parse_date(message.text)
                
                # Получаем текущий срок службы
                cur.execute("SELECT lifetime_days FROM filters WHERE id = ?", (filter_id,))
                lifetime = cur.fetchone()[0]
                
                # Пересчитываем дату истечения
                new_expiry = new_date + timedelta(days=lifetime)
                
                cur.execute("UPDATE filters SET last_change = ?, expiry_date = ? WHERE id = ?",
                           (new_date, new_expiry, filter_id))
                
                old_date_nice = format_date_nice(datetime.strptime(str(old_filter_data[4]), '%Y-%m-%d').date())
                new_date_nice = format_date_nice(new_date)
                new_expiry_nice = format_date_nice(new_expiry)
                
                log_user_action(message.from_user.id, "edited_change_date", f"id: {filter_id}, new: {new_date}")
                await safe_send_message(
                    message.chat.id,
                    f"✅ <b>Дата замены успешно изменена!</b>\n\n"
                    f"📅 <b>Было:</b> {old_date_nice}\n"
                    f"📅 <b>Стало:</b> {new_date_nice}\n"
                    f"🗓️ <b>Новая дата истечения:</b> {new_expiry_nice}",
                    parse_mode='HTML',
                    reply_markup=get_management_keyboard()
                )
                
            except ValueError as e:
                today_nice = format_date_nice(datetime.now().date())
                await safe_send_message(
                    message.chat.id,
                    f"❌ <b>Неверный формат даты!</b>\n\n"
                    f"📝 <i>{str(e)}</i>\n"
                    f"<i>Пример: {today_nice}</i>",
                    parse_mode='HTML',
                    reply_markup=get_cancel_keyboard()
                )
                return
                
        elif field == "lifetime_days":
            try:
                if message.text.startswith("3️⃣") or message.text.startswith("6️⃣") or message.text.startswith("1️⃣"):
                    new_lifetime = int(message.text.split()[1])
                elif message.text == "📅 Другое количество":
                    await safe_send_message(
                        message.chat.id,
                        "🔢 <b>Введите количество дней:</b>\n"
                        "<i>Например: 120, 200, 400 и т.д.</i>",
                        parse_mode='HTML',
                        reply_markup=get_cancel_keyboard()
                    )
                    return
                else:
                    new_lifetime = int(message.text)
                
                # Получаем текущую дату замены
                cur.execute("SELECT last_change FROM filters WHERE id = ?", (filter_id,))
                last_change = cur.fetchone()[0]
                
                # Пересчитываем дату истечения
                new_expiry = last_change + timedelta(days=new_lifetime)
                
                cur.execute("UPDATE filters SET lifetime_days = ?, expiry_date = ? WHERE id = ?",
                           (new_lifetime, new_expiry, filter_id))
                
                new_expiry_nice = format_date_nice(new_expiry)
                
                log_user_action(message.from_user.id, "edited_lifetime", f"id: {filter_id}, new: {new_lifetime}")
                await safe_send_message(
                    message.chat.id,
                    f"✅ <b>Срок службы успешно изменен!</b>\n\n"
                    f"⏱️ <b>Было:</b> {old_filter_data[6]} дней\n"
                    f"⏱️ <b>Стало:</b> {new_lifetime} дней\n"
                    f"🗓️ <b>Новая дата истечения:</b> {new_expiry_nice}",
                    parse_mode='HTML',
                    reply_markup=get_management_keyboard()
                )
                
            except ValueError:
                await safe_send_message(
                    message.chat.id,
                    "❌ <b>Неверный формат!</b>\n\n"
                    "🔢 <i>Введите количество дней числом</i>\n"
                    "<i>Например: 90, 180, 365</i>",
                    parse_mode='HTML',
                    reply_markup=get_lifetime_keyboard()
                )
                return
        
        conn.commit()
        
        # Получаем обновленные данные фильтра
        cur.execute("SELECT * FROM filters WHERE id = ?", (filter_id,))
        updated_filter = cur.fetchone()
        
        # Показываем обновленную информацию
        expiry_date = datetime.strptime(str(updated_filter[5]), '%Y-%m-%d').date()
        days_until_expiry = (expiry_date - datetime.now().date()).days
        
        if days_until_expiry <= 0:
            status_icon = "🔴"
            status_text = "ПРОСРОЧЕН"
        elif days_until_expiry <= 7:
            status_icon = "🟡"
            status_text = "СРОЧНО"
        elif days_until_expiry <= 30:
            status_icon = "🟠"
            status_text = "СКОРО"
        else:
            status_icon = "✅"
            status_text = "НОРМА"
        
        last_change_nice = format_date_nice(datetime.strptime(str(updated_filter[4]), '%Y-%m-%d').date())
        expiry_date_nice = format_date_nice(expiry_date)
        
        await safe_send_message(
            message.chat.id,
            f"📋 <b>ОБНОВЛЕННАЯ ИНФОРМАЦИЯ:</b>\n\n"
            f"{status_icon} <b>Фильтр #{filter_id}</b>\n"
            f"🔧 <b>Тип:</b> {updated_filter[2]}\n"
            f"📍 <b>Место:</b> {updated_filter[3]}\n"
            f"📅 <b>Заменен:</b> {last_change_nice}\n"
            f"⏱️ <b>Срок:</b> {updated_filter[6]} дн.\n"
            f"🗓️ <b>Годен до:</b> {expiry_date_nice}\n"
            f"⏳ <b>Осталось:</b> {days_until_expiry} дн.\n"
            f"📊 <b>Статус:</b> {status_text}",
            parse_mode='HTML'
        )
        
        conn.close()
        await state.finish()
        
    except Exception as e:
        logging.error(f"Ошибка при редактировании фильтра: {e}")
        await safe_send_message(
            message.chat.id,
            "❌ <b>Произошла ошибка при редактировании фильтра</b>\n\n"
            "💡 <i>Попробуйте еще раз</i>",
            parse_mode='HTML',
            reply_markup=get_management_keyboard()
        )
        await state.finish()

# Удаление фильтра
@dp.message_handler(lambda message: message.text == "🗑️ Удалить")
@dp.message_handler(commands=['delete'])
async def cmd_delete(message: types.Message):
    log_user_action(message.from_user.id, "started_delete")
    conn = sqlite3.connect('filters.db')
    cur = conn.cursor()
    cur.execute("SELECT id, filter_type, location, expiry_date FROM filters WHERE user_id = ?", 
                (message.from_user.id,))
    filters = cur.fetchall()
    conn.close()

    if not filters:
        await safe_send_message(
            message.chat.id,
            "❌ <b>Нет фильтров для удаления</b>",
            parse_mode='HTML',
            reply_markup=get_management_keyboard()
        )
        return

    keyboard = types.InlineKeyboardMarkup(row_width=1)
    for f in filters:
        expiry_date = datetime.strptime(str(f[3]), '%Y-%m-%d').date()
        days_until_expiry = (expiry_date - datetime.now().date()).days
        
        if days_until_expiry <= 0:
            status = "🔴"
        elif days_until_expiry <= 30:
            status = "🟡"
        else:
            status = "✅"
        
        expiry_date_nice = format_date_nice(expiry_date)
        
        keyboard.add(types.InlineKeyboardButton(
            f"{status} {f[1]} | {f[2]} | до {expiry_date_nice}",
            callback_data=f"select_delete_{f[0]}"
        ))
    
    keyboard.add(types.InlineKeyboardButton("🔙 Назад", callback_data="back_to_management"))

    await safe_send_message(
        message.chat.id,
        "🗑️ <b>Выберите фильтр для удаления:</b>\n\n"
        "💡 <i>Статусы: 🔴 - просрочен, 🟡 - скоро истекает, ✅ - в норме</i>",
        parse_mode='HTML',
        reply_markup=keyboard
    )

# Подтверждение удаления
@dp.callback_query_handler(lambda c: c.data and c.data.startswith('select_delete_'))
async def confirm_delete(callback_query: types.CallbackQuery):
    filter_id = callback_query.data.split('_')[2]

    conn = sqlite3.connect('filters.db')
    cur = conn.cursor()
    
    cur.execute("SELECT filter_type, location, expiry_date FROM filters WHERE id = ? AND user_id = ?",
                (filter_id, callback_query.from_user.id))
    filter_info = cur.fetchone()
    conn.close()
    
    if filter_info:
        expiry_date = datetime.strptime(str(filter_info[2]), '%Y-%m-%d').date()
        days_until_expiry = (expiry_date - datetime.now().date()).days
        
        expiry_date_nice = format_date_nice(expiry_date)
        status_text = "🔴 ПРОСРОЧЕН" if days_until_expiry <= 0 else "🟡 Истекает скоро" if days_until_expiry <= 30 else "✅ В норме"
        
        await callback_query.message.edit_text(
            f"⚠️ <b>Подтверждение удаления</b>\n\n"
            f"🔧 <b>Тип:</b> {filter_info[0]}\n"
            f"📍 <b>Место:</b> {filter_info[1]}\n"
            f"📅 <b>Срок годности:</b> {expiry_date_nice}\n"
            f"⏳ <b>Осталось дней:</b> {days_until_expiry}\n"
            f"📊 <b>Статус:</b> {status_text}\n\n"
            f"❓ <b>Вы уверены, что хотите удалить этот фильтр?</b>",
            parse_mode='HTML',
            reply_markup=get_confirmation_keyboard(filter_id)
        )
    else:
        await callback_query.answer("❌ Фильтр не найден", show_alert=True)

# Фактическое удаление фильтра
@dp.callback_query_handler(lambda c: c.data and c.data.startswith('confirm_delete_'))
async def process_delete(callback_query: types.CallbackQuery):
    filter_id = callback_query.data.split('_')[2]

    conn = sqlite3.connect('filters.db')
    cur = conn.cursor()
    
    cur.execute("SELECT filter_type, location, expiry_date FROM filters WHERE id = ? AND user_id = ?",
                (filter_id, callback_query.from_user.id))
    filter_info = cur.fetchone()
    
    if filter_info:
        cur.execute("DELETE FROM filters WHERE id = ? AND user_id = ?",
                    (filter_id, callback_query.from_user.id))
        conn.commit()
        conn.close()
        
        expiry_date_nice = format_date_nice(datetime.strptime(str(filter_info[2]), '%Y-%m-%d').date())
        
        log_user_action(callback_query.from_user.id, "deleted_filter", f"id: {filter_id}")
        await callback_query.message.edit_text(
            f"✅ <b>Фильтр удален:</b>\n\n"
            f"🔧 <b>Тип:</b> {filter_info[0]}\n"
            f"📍 <b>Место:</b> {filter_info[1]}\n"
            f"📅 <b>Срок истекал:</b> {expiry_date_nice}",
            parse_mode='HTML'
        )
    else:
        await callback_query.answer("❌ Фильтр не найден", show_alert=True)
        conn.close()

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

# Команда сброса базы данных (только для админа)
@dp.message_handler(commands=['reset_db'], user_id=config.ADMIN_ID)
async def cmd_reset_db(message: types.Message):
    """Сброс базы данных (только для администратора)"""
    log_user_action(message.from_user.id, "requested_db_reset")
    await safe_send_message(
        message.chat.id,
        "⚠️ <b>ВНИМАНИЕ!</b>\n\n"
        "Вы уверены, что хотите полностью очистить базу данных?\n"
        "Это действие нельзя отменить!\n\n"
        "🗑️ <i>Будут удалены ВСЕ фильтры всех пользователей</i>",
        parse_mode='HTML',
        reply_markup=get_reset_confirmation_keyboard()
    )

@dp.callback_query_handler(lambda c: c.data == "confirm_reset")
async def process_reset_db(callback_query: types.CallbackQuery):
    """Обработка подтверждения сброса базы данных"""
    try:
        # Создаем резервную копию перед сбросом
        backup_database()
        
        conn = sqlite3.connect('filters.db')
        cur = conn.cursor()
        cur.execute("DELETE FROM filters")
        conn.commit()
        
        # Получаем количество удаленных записей
        cur.execute("SELECT COUNT(*) FROM filters")
        remaining = cur.fetchone()[0]
        conn.close()
        
        log_user_action(callback_query.from_user.id, "executed_db_reset")
        await callback_query.message.edit_text(
            f"✅ <b>База данных успешно сброшена!</b>\n\n"
            f"🗑️ Все фильтры были удалены.\n"
            f"💾 Создана резервная копия перед сбросом.",
            parse_mode='HTML'
        )
        
    except Exception as e:
        logging.error(f"Ошибка при сбросе базы данных: {e}")
        await callback_query.message.edit_text(
            "❌ <b>Ошибка при сбросе базы данных</b>\n\n"
            f"💡 <i>{str(e)}</i>",
            parse_mode='HTML'
        )

@dp.callback_query_handler(lambda c: c.data == "cancel_reset")
async def cancel_reset_db(callback_query: types.CallbackQuery):
    """Отмена сброса базы данных"""
    await callback_query.message.edit_text(
        "✅ <b>Сброс базы данных отменен</b>\n\n"
        "💡 <i>Данные сохранены</i>",
        parse_mode='HTML'
    )

# Статистика
@dp.message_handler(lambda message: message.text == "📊 Статистика")
async def cmd_stats(message: types.Message):
    log_user_action(message.from_user.id, "viewed_stats")
    conn = sqlite3.connect('filters.db')
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
    
    # Общая статистика по всем пользователям (только для админа)
    if message.from_user.id == config.ADMIN_ID:
        cur.execute('''SELECT COUNT(DISTINCT user_id) FROM filters''')
        total_users = cur.fetchone()[0]
        
        cur.execute('''SELECT COUNT(*) FROM filters''')
        total_filters = cur.fetchone()[0]
    
    conn.close()
    
    response = "📊 <b>СТАТИСТИКА ФИЛЬТРОВ</b>\n\n"
    response += f"📦 <b>Всего фильтров:</b> {stats[0]}\n"
    response += f"🔴 <b>Просрочено:</b> {stats[1]}\n"
    response += f"🟡 <b>Срочно заменить:</b> {stats[2]}\n"
    response += f"🟠 <b>Скоро истекают:</b> {stats[3]}\n\n"
    
    if type_stats:
        response += "<b>📈 По типам:</b>\n"
        for filter_type, count in type_stats:
            response += f"   • {filter_type}: {count} шт.\n"
    
    # Добавляем общую статистику для админа
    if message.from_user.id == config.ADMIN_ID:
        response += f"\n👥 <b>Общая статистика (админ):</b>\n"
        response += f"   • Пользователей: {total_users}\n"
        response += f"   • Всего фильтров: {total_filters}\n"
    
    await safe_send_message(message.chat.id, response, parse_mode='HTML', reply_markup=get_management_keyboard())

# Обработка отмены
@dp.message_handler(lambda message: message.text == "❌ Отмена", state='*')
async def cmd_cancel(message: types.Message, state: FSMContext):
    await cancel_handler(message, state)

# Обработка других сообщений
@dp.message_handler()
async def handle_other_messages(message: types.Message):
    if is_rate_limited(message.from_user.id):
        return
        
    await safe_send_message(
        message.chat.id,
        "🌟 <b>Фильтр-Трекер</b> 🤖\n\n"
        "💧 <i>Выберите действие с помощью кнопок ниже:</i>",
        parse_mode='HTML',
        reply_markup=get_main_keyboard()
    )

# Запуск бота
if __name__ == '__main__':
    init_db()
    
    # Запуск с обработчиком startup
    executor.start_polling(
        dp, 
        skip_updates=True,
        on_startup=on_startup
    )
