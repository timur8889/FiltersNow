import logging
from datetime import datetime, timedelta
from telegram import Update, ReplyKeyboardMarkup, ReplyKeyboardRemove
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    filters,
    ContextTypes,
    ConversationHandler,
)
import sqlite3

# Настройка базы данных
def init_db():
    conn = sqlite3.connect('filters.db')
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS filters
                 (id INTEGER PRIMARY KEY, 
                  name TEXT, 
                  replacement_date DATE,
                  user_id INTEGER)''')
    conn.commit()
    conn.close()

# Константы состояний
CHOOSING, TYPING_NAME, TYPING_DATE = range(3)

# Клавиатура
main_keyboard = [['Добавить фильтр', 'Список фильтров'],
                 ['Удалить фильтр', 'Статус замены']]
reply_markup = ReplyKeyboardMarkup(main_keyboard, one_time_keyboard=True)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.re_html(
        "🤖 <b>Бот контроля замены фильтров</b>\n\n"
        "Выберите действие:",
        reply_markup=reply_markup
    )
    return CHOOSING

async def add_filter_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Введите название фильтра:",
        reply_markup=ReplyKeyboardRemove()
    )
    return TYPING_NAME

async def save_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['filter_name'] = update.message.text
    await update.message.reply_text("Введите дату последней замены (ГГГГ-ММ-ДД):")
    return TYPING_DATE

async def save_date(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        replacement_date = datetime.strptime(update.message.text, '%Y-%m-%d').date()
        
        conn = sqlite3.connect('filters.db')
        c = conn.cursor()
        c.execute("INSERT INTO filters (name, replacement_date, user_id) VALUES (?, ?, ?)",
                 (context.user_data['filter_name'], replacement_date, update.effective_user.id))
        conn.commit()
        conn.close()

        await update.message.reply_text(
            f"✅ Фильтр '{context.user_data['filter_name']}' добавлен!",
            reply_markup=reply_markup
        )
        return CHOOSING
    except ValueError:
        await update.message.reply_text("❌ Неверный формат даты! Используйте ГГГГ-ММ-ДД")
        return TYPING_DATE

async def list_filters(update: Update, context: ContextTypes.DEFAULT_TYPE):
    conn = sqlite3.connect('filters.db')
    c = conn.cursor()
    c.execute("SELECT name, replacement_date FROM filters WHERE user_id = ?", 
             (update.effective_user.id,))
    filters = c.fetchall()
    conn.close()

    if not filters:
        await update.message.reply_text("Список фильтров пуст", reply_markup=reply_markup)
        return CHOOSING

    response = "📋 Ваши фильтры:\n\n"
    for name, date in filters:
        response += f"• {name} (замена: {date})\n"
    
    await update.message.reply_text(response, reply_markup=reply_markup)
    return CHOOSING

async def check_replacements(context: ContextTypes.DEFAULT_TYPE):
    conn = sqlite3.connect('filters.db')
    c = conn.cursor()
    c.execute("SELECT user_id, name, replacement_date FROM filters")
    all_filters = c.fetchall()
    conn.close()

    for user_id, name, replacement_date in all_filters:
        if isinstance(replacement_date, str):
            replacement_date = datetime.strptime(replacement_date, '%Y-%m-%d').date()
        
        days_to_replace = (replacement_date + timedelta(days=90)) - datetime.now().date()
        
        if days_to_replace.days == 2:
            await context.bot.send_message(
                chat_id=user_id,
                text=f"🔔 Напоминание: до замены фильтра '{name}' осталось 2 дня!"
            )
        elif days_to_replace.days <= 0:
            await context.bot.send_message(
                chat_id=user_id,
                text=f"⚠️ Срочно замените фильтр '{name}'!"
            )

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Действие отменено", reply_markup=reply_markup)
    return CHOOSING

def main():
    init_db()
    
    application = Application.builder().token ("8278600298:AAFA-R0ql-dibAoBruxgwitHTx_LLx61OdM").build()

    # Проверка замен каждый день
    job_queue = application.job_queue
    job_queue.run_repeating(check_replacements, interval=86400)  # 24 часа

    conv_handler = ConversationHandler(
        entry_points=[CommandHandler('start', start)],
        states={
            CHOOSING: [
                MessageHandler(filters.Regex('^Добавить фильтр$'), add_filter_start),
                MessageHandler(filters.Regex('^Список фильтров$'), list_filters),
            ],
            TYPING_NAME: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, save_name)
            ],
            TYPING_DATE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, save_date)
            ],
        },
        fallbacks=[MessageHandler(filters.Regex('^Отмена$'), cancel)]
    )

    application.add_handler(conv_handler)
    application.run_polling()

if __name__ == '__main__':
    main()
