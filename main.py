import logging
from telegram import Update, ReplyKeyboardMarkup, KeyboardButton
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes, CallbackQueryHandler
import sqlite3
from datetime import datetime, timedelta
import json

# Настройка логирования
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Токен бота (получите у @BotFather)
BOT_TOKEN = 8278600298:AAFA-R0ql-dibAoBruxgwitHTx_LLx61OdM

# Инициализация базы данных
def init_db():
    conn = sqlite3.connect('filters.db')
    cursor = conn.cursor()
    
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS filters (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            location TEXT,
            last_change DATE,
            next_change DATE,
            change_interval INTEGER,
            status TEXT DEFAULT 'active',
            user_id INTEGER
        )
    ''')
    
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS notifications (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            filter_id INTEGER,
            notification_date DATE,
            sent BOOLEAN DEFAULT FALSE,
            FOREIGN KEY (filter_id) REFERENCES filters (id)
        )
    ''')
    
    conn.commit()
    conn.close()

# Команда /start
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    keyboard = [
        [KeyboardButton("📋 Мои фильтры"), KeyboardButton("➕ Добавить фильтр")],
        [KeyboardButton("🔔 Уведомления"), KeyboardButton("⚙️ Настройки")]
    ]
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    
    await update.message.reply_text(
        f"Привет, {user.first_name}! 👋\n\n"
        "Я помогу тебе контролировать состояние фильтров.\n"
        "Выбери действие:",
        reply_markup=reply_markup
    )

# Показать все фильтры пользователя
async def show_filters(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    conn = sqlite3.connect('filters.db')
    cursor = conn.cursor()
    
    cursor.execute('''
        SELECT id, name, location, last_change, next_change, status 
        FROM filters WHERE user_id = ?
    ''', (user_id,))
    
    filters_list = cursor.fetchall()
    conn.close()
    
    if not filters_list:
        await update.message.reply_text("У вас пока нет добавленных фильтров.")
        return
    
    message = "📋 Ваши фильтры:\n\n"
    for filter_item in filters_list:
        id, name, location, last_change, next_change, status = filter_item
        status_emoji = "✅" if status == 'active' else "❌"
        message += f"{status_emoji} {name}\n"
        message += f"📍 Место: {location}\n"
        message += f"📅 Последняя замена: {last_change}\n"
        message += f"🔄 Следующая замена: {next_change}\n"
        message += "─" * 20 + "\n"
    
    await update.message.reply_text(message)

# Добавление нового фильтра
async def add_filter(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Для добавления нового фильтра введите данные в формате:\n\n"
        "Название фильтра\n"
        "Местоположение\n"
        "Дата последней замены (ГГГГ-ММ-ДД)\n"
        "Интервал замены (в днях)\n\n"
        "Пример:\n"
        "Фильтр для воды\n"
        "Кухня\n"
        "2024-01-15\n"
        "180"
    )
    context.user_data['awaiting_filter_data'] = True

# Обработка ввода данных фильтра
async def handle_filter_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.user_data.get('awaiting_filter_data'):
        return
    
    try:
        data = update.message.text.split('\n')
        if len(data) != 4:
            await update.message.reply_text("❌ Неверный формат данных. Попробуйте снова.")
            return
        
        name, location, last_change_str, interval_str = data
        
        # Проверка даты
        last_change = datetime.strptime(last_change_str.strip(), '%Y-%m-%d').date()
        interval = int(interval_str.strip())
        
        # Расчет следующей замены
        next_change = last_change + timedelta(days=interval)
        
        # Сохранение в базу данных
        conn = sqlite3.connect('filters.db')
        cursor = conn.cursor()
        
        cursor.execute('''
            INSERT INTO filters (name, location, last_change, next_change, change_interval, user_id)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', (name.strip(), location.strip(), last_change_str.strip(), next_change.isoformat(), interval, update.effective_user.id))
        
        filter_id = cursor.lastrowid
        
        # Создание уведомления
        notification_date = next_change - timedelta(days=7)  # Уведомление за 7 дней
        cursor.execute('''
            INSERT INTO notifications (filter_id, notification_date)
            VALUES (?, ?)
        ''', (filter_id, notification_date.isoformat()))
        
        conn.commit()
        conn.close()
        
        context.user_data['awaiting_filter_data'] = False
        
        await update.message.reply_text(
            f"✅ Фильтр '{name}' успешно добавлен!\n"
            f"Следующая замена: {next_change}"
        )
        
    except ValueError as e:
        await update.message.reply_text("❌ Ошибка в данных. Проверьте формат даты и числа.")
    except Exception as e:
        await update.message.reply_text("❌ Произошла ошибка при добавлении фильтра.")

# Проверка просроченных фильтров
async def check_expired_filters(context: ContextTypes.DEFAULT_TYPE):
    conn = sqlite3.connect('filters.db')
    cursor = conn.cursor()
    
    cursor.execute('''
        SELECT f.id, f.name, f.next_change, f.user_id 
        FROM filters f 
        WHERE f.next_change <= date('now') AND f.status = 'active'
    ''')
    
    expired_filters = cursor.fetchall()
    
    for filter_item in expired_filters:
        filter_id, name, next_change, user_id = filter_item
        
        try:
            await context.bot.send_message(
                chat_id=user_id,
                text=f"⚠️ ВНИМАНИЕ!\n\n"
                     f"Фильтр '{name}' требует замены!\n"
                     f"Дата следующей замены: {next_change}\n\n"
                     f"Не забудьте заменить фильтр вовремя!"
            )
        except Exception as e:
            logger.error(f"Не удалось отправить уведомление пользователю {user_id}: {e}")
    
    conn.close()

# Уведомления о предстоящих заменах
async def send_upcoming_notifications(context: ContextTypes.DEFAULT_TYPE):
    conn = sqlite3.connect('filters.db')
    cursor = conn.cursor()
    
    cursor.execute('''
        SELECT f.name, f.next_change, f.user_id, n.id
        FROM filters f
        JOIN notifications n ON f.id = n.filter_id
        WHERE n.notification_date <= date('now') AND n.sent = FALSE
    ''')
    
    notifications = cursor.fetchall()
    
    for notification in notifications:
        name, next_change, user_id, notification_id = notification
        
        try:
            await context.bot.send_message(
                chat_id=user_id,
                text=f"🔔 Напоминание!\n\n"
                     f"Фильтр '{name}' требует замены через 7 дней.\n"
                     f"Дата замены: {next_change}\n\n"
                     f"Подготовьтесь к замене заранее!"
            )
            
            # Помечаем уведомление как отправленное
            cursor.execute('UPDATE notifications SET sent = TRUE WHERE id = ?', (notification_id,))
            conn.commit()
            
        except Exception as e:
            logger.error(f"Не удалось отправить уведомление пользователю {user_id}: {e}")
    
    conn.close()

# Основная функция
def main():
    # Инициализация базы данных
    init_db()
    
    # Создание приложения
    application = Application.builder().token(8278600298:AAFA-R0ql-dibAoBruxgwitHTx_LLx61OdM).build()
    
    # Обработчики команд
    application.add_handler(CommandHandler("start", start))
    application.add_handler(MessageHandler(filters.Text("📋 Мои фильтры"), show_filters))
    application.add_handler(MessageHandler(filters.Text("➕ Добавить фильтр"), add_filter))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_filter_input))
    
    # Планировщик для проверки фильтров
    job_queue = application.job_queue
    
    # Ежедневная проверка просроченных фильтров
    job_queue.run_repeating(check_expired_filters, interval=86400, first=10)  # 86400 секунд = 1 день
    
    # Ежедневная проверка уведомлений
    job_queue.run_repeating(send_upcoming_notifications, interval=86400, first=10)
    
    # Запуск бота
    application.run_polling()

if __name__ == '__main__':
    main()
