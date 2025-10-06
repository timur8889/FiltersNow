import logging
import sqlite3
from datetime import datetime, timedelta
from telegram import Update, ReplyKeyboardMarkup, ReplyKeyboardRemove
from telegram.ext import (
    Application, CommandHandler, MessageHandler, filters, 
    ContextTypes, ConversationHandler
)

# Настройка логирования
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

# Токен бота (замените на ваш)
BOT_TOKEN = 8278600298:AAFA-R0ql-dibAoBruxgwitHTx_LLx61OdM

# Состояния для ConversationHandler
SET_FILTER_TYPE, SET_INSTALL_DATE, SET_REPLACEMENT_PERIOD = range(3)

# Инициализация базы данных
def init_db():
    conn = sqlite3.connect('filters.db')
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS user_filters (
            user_id INTEGER,
            filter_type TEXT,
            install_date TEXT,
            replacement_period INTEGER,
            next_replacement TEXT,
            PRIMARY KEY (user_id, filter_type)
        )
    ''')
    conn.commit()
    conn.close()

# Команда /start
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.message.from_user
    welcome_text = f"""
Привет, {user.first_name}! 🚰

Я бот для отслеживания замены водяных фильтров.

Возможности:
✅ Добавить фильтр
📋 Посмотреть мои фильтры
⏰ Напоминать о замене
🗑️ Удалить фильтр

Команды:
/start - начать работу
/add_filter - добавить фильтр
/my_filters - мои фильтры
/delete_filter - удалить фильтр
/help - помощь
    """
    await update.message.reply_text(welcome_text)

# Команда /add_filter
async def add_filter(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Введите тип/название фильтра (например: 'Главный фильтр', 'Фильтр под раковиной'):",
        reply_markup=ReplyKeyboardRemove()
    )
    return SET_FILTER_TYPE

# Получение типа фильтра
async def set_filter_type(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['filter_type'] = update.message.text
    await update.message.reply_text(
        "Введите дату установки в формате ДД.ММ.ГГГГ (например: 15.01.2024):"
    )
    return SET_INSTALL_DATE

# Получение даты установки
async def set_install_date(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        install_date = datetime.strptime(update.message.text, '%d.%m.%Y').date()
        context.user_data['install_date'] = install_date.isoformat()
        
        keyboard = [['3', '6', '12'], ['24']]
        reply_markup = ReplyKeyboardMarkup(keyboard, one_time_keyboard=True)
        
        await update.message.reply_text(
            "Выберите период замены (в месяцах):",
            reply_markup=reply_markup
        )
        return SET_REPLACEMENT_PERIOD
    except ValueError:
        await update.message.reply_text("Неверный формат даты. Попробуйте снова (ДД.ММ.ГГГГ):")
        return SET_INSTALL_DATE

# Получение периода замены и сохранение фильтра
async def set_replacement_period(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        period = int(update.message.text)
        user_id = update.message.from_user.id
        filter_type = context.user_data['filter_type']
        install_date = context.user_data['install_date']
        
        # Расчет даты следующей замены
        install_dt = datetime.strptime(install_date, '%Y-%m-%d')
        next_replacement = install_dt + timedelta(days=period*30)
        
        # Сохранение в базу данных
        conn = sqlite3.connect('filters.db')
        cursor = conn.cursor()
        cursor.execute('''
            INSERT OR REPLACE INTO user_filters 
            (user_id, filter_type, install_date, replacement_period, next_replacement)
            VALUES (?, ?, ?, ?, ?)
        ''', (user_id, filter_type, install_date, period, next_replacement.isoformat()))
        conn.commit()
        conn.close()
        
        await update.message.reply_text(
            f"✅ Фильтр '{filter_type}' успешно добавлен!\n"
            f"📅 Следующая замена: {next_replacement.strftime('%d.%m.%Y')}",
            reply_markup=ReplyKeyboardRemove()
        )
        return ConversationHandler.END
    except ValueError:
        await update.message.reply_text("Пожалуйста, введите число (период в месяцах):")
        return SET_REPLACEMENT_PERIOD

# Команда /my_filters
async def my_filters(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    
    conn = sqlite3.connect('filters.db')
    cursor = conn.cursor()
    cursor.execute(
        'SELECT filter_type, install_date, replacement_period, next_replacement FROM user_filters WHERE user_id = ?',
        (user_id,)
    )
    filters = cursor.fetchall()
    conn.close()
    
    if not filters:
        await update.message.reply_text("У вас пока нет добавленных фильтров.")
        return
    
    text = "📋 Ваши фильтры:\n\n"
    today = datetime.now().date()
    
    for filter_data in filters:
        filter_type, install_date, period, next_replacement = filter_data
        next_replacement_date = datetime.strptime(next_replacement, '%Y-%m-%d').date()
        days_left = (next_replacement_date - today).days
        
        status = "✅ В норме" if days_left > 7 else "⚠️ Скоро замена" if days_left > 0 else "🚨 ТРЕБУЕТ ЗАМЕНЫ!"
        
        text += f"""🔹 {filter_type}
📅 Установлен: {datetime.strptime(install_date, '%Y-%m-%d').strftime('%d.%m.%Y')}
🔄 Период: {period} мес.
📅 Замена: {next_replacement_date.strftime('%d.%m.%Y')}
⏰ Осталось дней: {days_left}
{status}

"""
    
    await update.message.reply_text(text)

# Команда /delete_filter
async def delete_filter(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    
    conn = sqlite3.connect('filters.db')
    cursor = conn.cursor()
    cursor.execute(
        'SELECT filter_type FROM user_filters WHERE user_id = ?',
        (user_id,)
    )
    user_filters = cursor.fetchall()
    conn.close()
    
    if not user_filters:
        await update.message.reply_text("У вас нет фильтров для удаления.")
        return
    
    keyboard = [[filter[0]] for filter in user_filters]
    reply_markup = ReplyKeyboardMarkup(keyboard, one_time_keyboard=True)
    
    await update.message.reply_text(
        "Выберите фильтр для удаления:",
        reply_markup=reply_markup
    )
    context.user_data['awaiting_delete'] = True

# Обработка выбора фильтра для удаления
async def handle_filter_selection(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if context.user_data.get('awaiting_delete'):
        filter_type = update.message.text
        user_id = update.message.from_user.id
        
        conn = sqlite3.connect('filters.db')
        cursor = conn.cursor()
        cursor.execute(
            'DELETE FROM user_filters WHERE user_id = ? AND filter_type = ?',
            (user_id, filter_type)
        )
        conn.commit()
        conn.close()
        
        await update.message.reply_text(
            f"Фильтр '{filter_type}' удален.",
            reply_markup=ReplyKeyboardRemove()
        )
        context.user_data['awaiting_delete'] = False

# Ежедневная проверка и уведомления
async def check_replacements(context: ContextTypes.DEFAULT_TYPE):
    conn = sqlite3.connect('filters.db')
    cursor = conn.cursor()
    cursor.execute('SELECT DISTINCT user_id FROM user_filters')
    users = cursor.fetchall()
    
    today = datetime.now().date()
    
    for user in users:
        user_id = user[0]
        cursor.execute(
            'SELECT filter_type, next_replacement FROM user_filters WHERE user_id = ?',
            (user_id,)
        )
        filters = cursor.fetchall()
        
        for filter_data in filters:
            filter_type, next_replacement = filter_data
            next_replacement_date = datetime.strptime(next_replacement, '%Y-%m-%d').date()
            days_left = (next_replacement_date - today).days
            
            if days_left == 7:
                message = f"⚠️ Напоминание: до замены фильтра '{filter_type}' осталось 7 дней!"
                await context.bot.send_message(chat_id=user_id, text=message)
            elif days_left == 0:
                message = f"🚨 ВНИМАНИЕ: фильтр '{filter_type}' требует замены сегодня!"
                await context.bot.send_message(chat_id=user_id, text=message)
            elif days_left < 0:
                message = f"🚨 СРОЧНО: фильтр '{filter_type}' просрочен на {abs(days_left)} дней!"
                await context.bot.send_message(chat_id=user_id, text=message)
    
    conn.close()

# Команда /help
async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    help_text = """
🤖 Бот для замены водяных фильтров

📋 Доступные команды:
/start - начать работу
/add_filter - добавить новый фильтр
/my_filters - посмотреть мои фильтры
/delete_filter - удалить фильтр
/help - показать эту справку

🔔 Бот автоматически напомнит о необходимости замены:
- за 7 дней до срока
- в день замены
- если фильтр просрочен

💡 Советы:
• Регулярно меняйте фильтры для качественной воды
• Записывайте точные даты установки
• Настраивайте реалистичные периоды замены
    """
    await update.message.reply_text(help_text)

# Отмена диалога
async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        'Действие отменено.',
        reply_markup=ReplyKeyboardRemove()
    )
    return ConversationHandler.END

def main():
    # Инициализация базы данных
    init_db()
    
    # Создание приложения
    application = Application.builder().token(BOT_TOKEN).build()
    
    # Обработчик добавления фильтра
    conv_handler = ConversationHandler(
        entry_points=[CommandHandler('add_filter', add_filter)],
        states={
            SET_FILTER_TYPE: [MessageHandler(filters.TEXT & ~filters.COMMAND, set_filter_type)],
            SET_INSTALL_DATE: [MessageHandler(filters.TEXT & ~filters.COMMAND, set_install_date)],
            SET_REPLACEMENT_PERIOD: [MessageHandler(filters.TEXT & ~filters.COMMAND, set_replacement_period)],
        },
        fallbacks=[CommandHandler('cancel', cancel)]
    )
    
    # Добавление обработчиков
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("my_filters", my_filters))
    application.add_handler(CommandHandler("delete_filter", delete_filter))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(conv_handler)
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_filter_selection))
    
    # Настройка ежедневных уведомлений
    job_queue = application.job_queue
    job_queue.run_daily(check_replacements, time=datetime.time(hour=9, minute=0))
    
    # Запуск бота
    application.run_polling()

if __name__ == '__main__':
    main()
