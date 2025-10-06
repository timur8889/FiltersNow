import logging
from telegram import Update, ReplyKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes, ConversationHandler
import sqlite3
from datetime import datetime, timedelta
import asyncio

# Настройка логирования
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

# Состояния для ConversationHandler
CHOOSING, TYPING_REPLY = range(2)

# База данных
def init_db():
    conn = sqlite3.connect('filters.db')
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS filters
                 (id INTEGER PRIMARY KEY, 
                  filter_type TEXT, 
                  install_date TEXT,
                  replacement_period INTEGER,
                  chat_id INTEGER)''')
    conn.commit()
    conn.close()

def add_filter(filter_type, install_date, replacement_period, chat_id):
    conn = sqlite3.connect('filters.db')
    c = conn.cursor()
    c.execute("INSERT INTO filters (filter_type, install_date, replacement_period, chat_id) VALUES (?, ?, ?, ?)",
              (filter_type, install_date, replacement_period, chat_id))
    conn.commit()
    conn.close()

def get_filters(chat_id):
    conn = sqlite3.connect('filters.db')
    c = conn.cursor()
    c.execute("SELECT * FROM filters WHERE chat_id = ?", (chat_id,))
    filters = c.fetchall()
    conn.close()
    return filters

def get_due_filters():
    conn = sqlite3.connect('filters.db')
    c = conn.cursor()
    c.execute("SELECT * FROM filters WHERE date(install_date) <= date('now', '-' || replacement_period || ' days')")
    due_filters = c.fetchall()
    conn.close()
    return due_filters

# Команды бота
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [['Добавить фильтр', 'Мои фильтры'], ['Проверить замену']]
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    
    await update.message.reply_text(
        '🤖 Бот для отслеживания замены фильтров\n\n'
        'Выберите действие:',
        reply_markup=reply_markup
    )
    return CHOOSING

async def add_filter_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        'Введите данные фильтра в формате:\n'
        'Тип фильтра | Дата установки (ГГГГ-ММ-ДД) | Период замены (в днях)\n\n'
        'Пример: Водяной фильтр | 2024-01-15 | 180'
    )
    return TYPING_REPLY

async def received_info(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        text = update.message.text
        parts = [part.strip() for part in text.split('|')]
        
        if len(parts) != 3:
            await update.message.reply_text('Неверный формат. Попробуйте снова.')
            return TYPING_REPLY
        
        filter_type, install_date, period = parts
        replacement_period = int(period)
        chat_id = update.message.chat_id
        
        add_filter(filter_type, install_date, replacement_period, chat_id)
        
        next_replacement = datetime.strptime(install_date, '%Y-%m-%d') + timedelta(days=replacement_period)
        
        await update.message.reply_text(
            f'✅ Фильтр добавлен!\n'
            f'Тип: {filter_type}\n'
            f'Дата установки: {install_date}\n'
            f'Следующая замена: {next_replacement.strftime("%Y-%m-%d")}'
        )
        
    except ValueError as e:
        await update.message.reply_text('Ошибка в данных. Проверьте формат даты и периода.')
    
    return CHOOSING

async def show_filters(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.message.chat_id
    filters = get_filters(chat_id)
    
    if not filters:
        await update.message.reply_text('У вас нет добавленных фильтров.')
        return CHOOSING
    
    message = "📋 Ваши фильтры:\n\n"
    for filter_item in filters:
        filter_id, filter_type, install_date, replacement_period, _ = filter_item
        install_dt = datetime.strptime(install_date, '%Y-%m-%d')
        next_replacement = install_dt + timedelta(days=replacement_period)
        days_left = (next_replacement - datetime.now()).days
        
        status = "✅" if days_left > 7 else "⚠️" if days_left > 0 else "🔴"
        
        message += f"{status} {filter_type}\n"
        message += f"   Установлен: {install_date}\n"
        message += f"   Замена через: {days_left} дней\n\n"
    
    await update.message.reply_text(message)
    return CHOOSING

async def check_replacement(update: Update, context: ContextTypes.DEFAULT_TYPE):
    due_filters = get_due_filters()
    
    if not due_filters:
        await update.message.reply_text('✅ Все фильтры в порядке!')
        return CHOOSING
    
    message = "🔔 Требуется замена фильтров:\n\n"
    for filter_item in due_filters:
        _, filter_type, install_date, replacement_period, _ = filter_item
        message += f"🔴 {filter_type}\n"
        message += f"   Установлен: {install_date}\n"
        message += f"   Период замены: {replacement_period} дней\n\n"
    
    await update.message.reply_text(message)
    return CHOOSING

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text('До свидания!')
    return ConversationHandler.END

def main():
    # Инициализация базы данных
    init_db()
    
    # Создание приложения
    application = Application.builder().token("8278600298:AAFA-R0ql-dibAoBruxgwitHTx_LLx61OdM").build()
    
    # Обработчик диалога
    conv_handler = ConversationHandler(
        entry_points=[CommandHandler('start', start)],
        states={
            CHOOSING: [
                MessageHandler(filters.Regex('^Добавить фильтр$'), add_filter_command),
                MessageHandler(filters.Regex('^Мои фильтры$'), show_filters),
                MessageHandler(filters.Regex('^Проверить замену$'), check_replacement),
            ],
            TYPING_REPLY: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, received_info)
            ],
        },
        fallbacks=[CommandHandler('cancel', cancel)]
    )
    
    application.add_handler(conv_handler)
    
    # Запуск бота
    application.run_polling()

if __name__ == '__main__':
    main()
