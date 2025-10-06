import logging
import sqlite3
from datetime import datetime, timedelta
from telegram import (
    Update,
    ReplyKeyboardMarkup,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
)
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
    CallbackQueryHandler,
    ConversationHandler,
)
import pandas as pd

# Настройка логирования
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

# Состояния для ConversationHandler
ADD_FILTER, SET_DATE, DELETE_FILTER = range(3)

# Инициализация базы данных
def init_db():
    conn = sqlite3.connect("filters.db")
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY,
            telegram_id INTEGER UNIQUE,
            username TEXT
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS filters (
            id INTEGER PRIMARY KEY,
            user_id INTEGER,
            name TEXT,
            replace_date DATE,
            FOREIGN KEY(user_id) REFERENCES users(id)
        )
    """)
    conn.commit()
    conn.close()

# Проверка авторизации
def get_user(telegram_id):
    conn = sqlite3.connect("filters.db")
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM users WHERE telegram_id = ?", (telegram_id,))
    user = cursor.fetchone()
    conn.close()
    return user

# Команда старта и авторизации
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = get_user(update.effective_user.id)
    if not user:
        conn = sqlite3.connect("filters.db")
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO users (telegram_id, username) VALUES (?, ?)",
            (update.effective_user.id, update.effective_user.username),
        )
        conn.commit()
        conn.close()
    
    keyboard = [["Добавить фильтр", "Мои фильтры"], ["Экспорт в Excel"]]
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    await update.message.reply_text("Вы авторизованы!", reply_markup=reply_markup)

# Добавление фильтра
async def add_filter(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Введите название фильтра:")
    return ADD_FILTER

async def set_filter_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["filter_name"] = update.message.text
    await update.message.reply_text("Установите дату замены в формате ГГГГ-ММ-ДД:")
    return SET_DATE

async def set_filter_date(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        replace_date = datetime.strptime(update.message.text, "%Y-%m-%d").date()
        user = get_user(update.effective_user.id)
        
        conn = sqlite3.connect("filters.db")
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO filters (user_id, name, replace_date) VALUES (?, ?, ?)",
            (user[0], context.user_data["filter_name"], replace_date),
        )
        conn.commit()
        conn.close()
        
        # Установка напоминания
        alert_date = replace_date - timedelta(days=3)
        context.job_queue.run_once(
            send_alert,
            alert_date,
            chat_id=update.effective_chat.id,
            data=context.user_data["filter_name"],
        )
        
        await update.message.reply_text("Фильтр успешно добавлен!")
        return ConversationHandler.END
    except ValueError:
        await update.message.reply_text("Неверный формат даты! Используйте ГГГГ-ММ-ДД:")

# Удаление фильтра
async def delete_filter(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = get_user(update.effective_user.id)
    conn = sqlite3.connect("filters.db")
    cursor = conn.cursor()
    cursor.execute("SELECT id, name FROM filters WHERE user_id = ?", (user[0],))
    filters = cursor.fetchall()
    conn.close()
    
    keyboard = [
        [InlineKeyboardButton(f[1], callback_data=f"delete_{f[0]}")] for f in filters
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text("Выберите фильтр для удаления:", reply_markup=reply_markup)

async def handle_delete(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    filter_id = query.data.split("_")[1]
    
    conn = sqlite3.connect("filters.db")
    cursor = conn.cursor()
    cursor.execute("DELETE FROM filters WHERE id = ?", (filter_id,))
    conn.commit()
    conn.close()
    
    await query.edit_message_text("Фильтр удален!")

# Экспорт в Excel
async def export_to_excel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = get_user(update.effective_user.id)
    conn = sqlite3.connect("filters.db")
    df = pd.read_sql_query(
        "SELECT name, replace_date FROM filters WHERE user_id = ?", conn, params=(user[0],)
    )
    conn.close()
    
    filename = f"filters_{update.effective_user.id}.xlsx"
    df.to_excel(filename, index=False)
    
    await update.message.reply_document(document=open(filename, "rb"))

# Напоминание
async def send_alert(context: ContextTypes.DEFAULT_TYPE):
    job = context.job
    await context.bot.send_message(
        job.chat_id, f"Напоминание: заменить фильтр {job.data} через 3 дня!"
    )

# Просмотр фильтров
async def show_filters(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = get_user(update.effective_user.id)
    conn = sqlite3.connect("filters.db")
    cursor = conn.cursor()
    cursor.execute("SELECT name, replace_date FROM filters WHERE user_id = ?", (user[0],))
    filters = cursor.fetchall()
    conn.close()
    
    if not filters:
        await update.message.reply_text("У вас нет активных фильтров.")
        return
    
    text = "\n".join([f"{f[0]}: {f[1]}" for f in filters])
    await update.message.reply_text(f"Ваши фильтры:\n{text}")

# Основная функция
def main():
    init_db()
    application = Application.builder().token("8278600298:AAFA-R0ql-dibAoBruxgwitHTx_LLx61OdM").build()
    
    conv_handler = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex("^Добавить фильтр$"), add_filter)],
        states={
            ADD_FILTER: [MessageHandler(filters.TEXT, set_filter_name)],
            SET_DATE: [MessageHandler(filters.TEXT, set_filter_date)],
        },
        fallbacks=[],
    )
    
    application.add_handler(CommandHandler("start", start))
    application.add_handler(conv_handler)
    application.add_handler(MessageHandler(filters.Regex("^Мои фильтры$"), show_filters))
    application.add_handler(MessageHandler(filters.Regex("^Экспорт в Excel$"), export_to_excel))
    application.add_handler(MessageHandler(filters.Regex("^Удалить фильтр$"), delete_filter))
    application.add_handler(CallbackQueryHandler(handle_delete, pattern="^delete_"))
    
    application.run_polling()

if __name__ == "__main__":
    main()
