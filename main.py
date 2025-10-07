import logging
from telegram import Update, ReplyKeyboardMarkup
from telegram.ext import Updater, CommandHandler, MessageHandler, Filters, CallbackContext
import sqlite3
from datetime import datetime, time
import pytz

# Настройки
TOKEN = 8278600298:AAFA-R0ql-dibAoBruxgwitHTx_LLx61OdM
TIMEZONE = pytz.timezone('Europe/Moscow')  # Замените на вашу временную зону
DB_NAME = "applications.db"

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
            application_text TEXT,
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

# Команда /start
def start(update: Update, context: CallbackContext):
    user = update.effective_user
    keyboard = [['Оставить заявку']]
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    
    update.message.reply_text(
        f"Привет, {user.first_name}!\n"
        "Я бот для приема заявок с 9:00 до 17:30.\n"
        "Нажмите кнопку ниже чтобы оставить заявку.",
        reply_markup=reply_markup
    )

# Обработчик заявок
def handle_application(update: Update, context: CallbackContext):
    if not is_working_time():
        update.message.reply_text(
            "❌ Прием заявок осуществляется только с 9:00 до 17:30.\n"
            "Пожалуйста, вернитесь в рабочее время."
        )
        return

    user = update.effective_user
    application_text = update.message.text

    # Сохранение в базу данных
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO applications (user_id, username, application_text) VALUES (?, ?, ?)",
        (user.id, user.username, application_text)
    )
    conn.commit()
    conn.close()

    update.message.reply_text(
        "✅ Ваша заявка принята! Мы обработаем ее в ближайшее время."
    )

# Основная функция
def main():
    init_db()
    updater = Updater(TOKEN)
    dp = updater.dispatcher

    dp.add_handler(CommandHandler("start", start))
    dp.add_handler(MessageHandler(Filters.text & ~Filters.command, handle_application))

    updater.start_polling()
    updater.idle()

if __name__ == '__main__':
    main()
