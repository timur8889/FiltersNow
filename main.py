import logging
from telegram import Update, ReplyKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes, ConversationHandler
import sqlite3
from datetime import datetime

# Настройка логирования
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

# Токен бота (замените на свой)
TOKEN = "8278600298:AAFA-R0ql-dibAoBruxgwitHTx_LLx61OdM"


# Этапы разговора
SELECTING_ACTION, ADDING_FILTER, SELECTING_FILTER = range(3)

# Подключение к базе данных
conn = sqlite3.connect('filters.db', check_same_thread=False)
cursor = conn.cursor()

# Создание таблицы
cursor.execute('''
    CREATE TABLE IF NOT EXISTS filters (
        id INTEGER PRIMARY KEY,
        user_id INTEGER,
        filter_type TEXT,
        change_date TEXT
    )
''')
conn.commit()

# Клавиатуры
main_keyboard = [['Добавить замену', 'История замен']]
markup = ReplyKeyboardMarkup(main_keyboard, resize_keyboard=True)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Бот для учета замены фильтров. Выберите действие:",
        reply_markup=markup
    )
    return SELECTING_ACTION

async def add_filter_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Введите тип фильтра:")
    return ADDING_FILTER

async def add_filter_finish(update: Update, context: ContextTypes.DEFAULT_TYPE):
    filter_type = update.message.text
    user_id = update.effective_user.id
    current_date = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    cursor.execute(
        "INSERT INTO filters (user_id, filter_type, change_date) VALUES (?, ?, ?)",
        (user_id, filter_type, current_date)
    )
    conn.commit()
    
    await update.message.reply_text(
        f"Запись о замене фильтра {filter_type} добавлена!",
        reply_markup=markup
    )
    return SELECTING_ACTION

async def show_history(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    cursor.execute(
        "SELECT filter_type, change_date FROM filters WHERE user_id = ? ORDER BY change_date DESC",
        (user_id,)
    )
    records = cursor.fetchall()
    
    if not records:
        await update.message.reply_text("У вас пока нет записей о заменах.")
        return SELECTING_ACTION
    
    history_text = "Последние замены:\n" + "\n".join(
        [f"• {record[0]} - {record[1]}" for record in records[-10:]]
    )
    await update.message.reply_text(history_text, reply_markup=markup)
    return SELECTING_ACTION

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Действие отменено", reply_markup=markup)
    return SELECTING_ACTION

def main():
    application = Application.builder().token(TOKEN).build()
    
    conv_handler = ConversationHandler(
        entry_points=[CommandHandler('start', start)],
        states={
            SELECTING_ACTION: [
                MessageHandler(filters.Regex('^Добавить замену$'), add_filter_start),
                MessageHandler(filters.Regex('^История замен$'), show_history),
            ],
            ADDING_FILTER: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, add_filter_finish)
            ],
        },
        fallbacks=[CommandHandler('cancel', cancel)]
    )
    
    application.add_handler(conv_handler)
    application.run_polling()

if __name__ == '__main__':
    main()
