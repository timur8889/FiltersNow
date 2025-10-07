import logging
from telegram import Update, ReplyKeyboardMarkup, ReplyKeyboardRemove
from telegram.ext import (
    Updater, CommandHandler, MessageHandler, Filters, 
    CallbackContext, ConversationHandler
)
import sqlite3
from datetime import datetime, time
import pytz
import re

# Настройки
TOKEN = "8278600298:AAFA-R0ql-dibAoBruxgwitHTx_LLx61OdM"
ADMIN_ID = "@merik_202"  # Ваш ID в Telegram
TIMEZONE = pytz.timezone('Europe/Moscow')
DB_NAME = "applications.db"

# Состояния разговора
NAME, PHONE, CONFIRM = range(3)

# Настройка логирования
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

# Инициализация базы данных
def init_db():
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS applications (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            username TEXT,
            name TEXT,
            phone TEXT,
            status TEXT DEFAULT 'new',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    conn.commit()
    conn.close()

# Проверка рабочего времени
def is_working_time():
    now = datetime.now(TIMEZONE)
    if now.weekday() >= 5:  # Суббота и воскресенье
        return False
    
    start_time = time(9, 0)
    end_time = time(17, 30)
    return start_time <= now.time() <= end_time

# Проверка номера телефона
def is_valid_phone(phone):
    pattern = r'^(\+7|8)?[\s\-]?\(?[489][0-9]{2}\)?[\s\-]?[0-9]{3}[\s\-]?[0-9]{2}[\s\-]?[0-9]{2}$'
    return re.match(pattern, phone) is not None

# Уведомление администратора
def notify_admin(context: CallbackContext, application_data):
    message = (
        "🆕 НОВАЯ ЗАЯВКА!\n"
        f"👤 Имя: {application_data['name']}\n"
        f"📞 Телефон: {application_data['phone']}\n"
        f"👤 Пользователь: @{application_data['username']}\n"
        f"🆔 User ID: {application_data['user_id']}\n"
        f"⏰ Время: {datetime.now(TIMEZONE).strftime('%d.%m.%Y %H:%M')}"
    )
    
    context.bot.send_message(chat_id=ADMIN_ID, text=message)

# Команда /start
def start(update: Update, context: CallbackContext):
    if not is_working_time():
        update.message.reply_text(
            "❌ Прием заявок осуществляется только с 9:00 до 17:30 в рабочие дни.\n"
            "Пожалуйста, вернитесь в рабочее время."
        )
        return ConversationHandler.END
    
    update.message.reply_text(
        "👋 Добро пожаловать! Я помогу вам оставить заявку.\n"
        "Для начала введите ваше имя:",
        reply_markup=ReplyKeyboardRemove()
    )
    return NAME

# Получение имени
def get_name(update: Update, context: CallbackContext):
    name = update.message.text.strip()
    if len(name) < 2:
        update.message.reply_text("❌ Имя должно содержать минимум 2 символа. Попробуйте еще раз:")
        return NAME
    
    context.user_data['name'] = name
    context.user_data['user_id'] = update.effective_user.id
    context.user_data['username'] = update.effective_user.username or "Не указан"
    
    update.message.reply_text(
        "📞 Теперь введите ваш номер телефона:\n"
        "Например: +7 999 123-45-67 или 89991234567"
    )
    return PHONE

# Получение телефона
def get_phone(update: Update, context: CallbackContext):
    phone = update.message.text.strip()
    
    if not is_valid_phone(phone):
        update.message.reply_text(
            "❌ Неверный формат номера телефона.\n"
            "Пожалуйста, введите номер в формате:\n"
            "+7 999 123-45-67 или 89991234567\n"
            "Попробуйте еще раз:"
        )
        return
