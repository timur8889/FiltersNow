import logging
from datetime import datetime, timedelta
from telegram import (
    Update,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
    InlineKeyboardMarkup,
    InlineKeyboardButton
)
from telegram.ext import (
    Updater,
    CommandHandler,
    MessageHandler,
    filters,
    ConversationHandler,
    CallbackContext,
    CallbackQueryHandler
)
import sqlite3
import pytz

# Настройка логирования
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Состояния разговора
FIO, POSITION, PHONE, PRIORITY, CATEGORY, PROBLEM = range(6)

# Настройки
ADMIN_IDS = [5024165375]  # Замените на ID администраторов
TIMEZONE = pytz.timezone('Europe/Moscow')
WORK_START = 9  # 9:00
WORK_END = 17   # 17:00

# Инициализация базы данных
def init_db():
    conn = sqlite3.connect('tickets.db')
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS tickets
                 (id INTEGER PRIMARY KEY,
                  user_id INTEGER,
                  fio TEXT,
                  position TEXT,
                  phone TEXT,
                  priority TEXT,
                  category TEXT,
                  problem TEXT,
                  created DATETIME,
                  deadline DATETIME,
                  status TEXT DEFAULT 'new',
                  admin_comment TEXT)''')
    conn.commit()
    conn.close()

# Проверка рабочего времени
def is_working_hours():
    now = datetime.now(TIMEZONE)
    if now.weekday() >= 5:  # Суббота и воскресенье
        return False
    current_hour = now.hour
    return WORK_START <= current_hour < WORK_END

# Расчет дедлайна
def calculate_deadline():
    now = datetime.now(TIMEZONE)
    hours_added = 0
    
    while hours_added < 48:
        now += timedelta(hours=1)
        if now.weekday() < 5 and WORK_START <= now.hour < WORK_END:
            hours_added += 1
            
    return now

# Клавиатуры
def main_keyboard():
    return ReplyKeyboardMarkup([
        ['📝 Создать заявку'],
        ['📊 Статус заявок']
    ], resize_keyboard=True)

def priority_keyboard():
    return ReplyKeyboardMarkup([
        ['🔴 Высокий', '🟡 Средний'],
        ['🟢 Низкий', '🔵 Обычный']
    ], resize_keyboard=True)

def category_keyboard():
    return ReplyKeyboardMarkup([
        ['💻 Техника', '📊 Программы'],
        ['🌐 Сеть', '🔐 Безопасность'],
        ['📝 Документы', '❓ Другое']
    ], resize_keyboard=True)

# Команда start
def start(update: Update, context: CallbackContext):
    user = update.message.from_user
    update.message.reply_text(
        f"Добро пожаловать, {user.first_name}!\n"
        "Я бот для управления заявками.\n\n"
        "Рабочее время: Пн-Пт 9:00-17:00\n"
        "Время выполнения заявки: 48 часов\n\n"
        "Выберите действие:",
        reply_markup=main_keyboard()
    )

# Начало создания заявки
def create_ticket_start(update: Update, context: CallbackContext):
    if not is_working_hours():
        update.message.reply_text(
            "Сейчас нерабочее время. Ваша заявка будет создана "
            "в следующем рабочем дне.",
            reply_markup=main_keyboard()
        )
    
    update.message.reply_text(
        "Введите ваше ФИО:",
        reply_markup=ReplyKeyboardRemove()
    )
    return FIO

# Обработка ФИО
def fio_received(update: Update, context: CallbackContext):
    context.user_data['fio'] = update.message.text
    update.message.reply_text("Введите вашу должность:")
    return POSITION

# Обработка должности
def position_received(update: Update, context: CallbackContext):
    context.user_data['position'] = update.message.text
    update.message.reply_text("Введите ваш телефон:")
    return PHONE

# Обработка телефона
def phone_received(update: Update, context: CallbackContext):
    context.user_data['phone'] = update.message.text
    update.message.reply_text(
        "Выберите приоритет:",
        reply_markup=priority_keyboard()
    )
    return PRIORITY

# Обработка приоритета
def priority_received(update: Update, context: CallbackContext):
    context.user_data['priority'] = update.message.text
    update.message.reply_text(
        "Выберите категорию проблемы:",
        reply_markup=category_keyboard()
    )
    return CATEGORY

# Обработка категории
def category_received(update: Update, context: CallbackContext):
    context.user_data['category'] = update.message.text
    update.message.reply_text(
        "Опишите проблему подробно:",
        reply_markup=ReplyKeyboardRemove()
    )
    return PROBLEM

# Создание заявки
def problem_received(update: Update, context: CallbackContext):
    context.user_data['problem'] = update.message.text
    user_data = context.user_data
    
    # Сохранение в БД
    conn = sqlite3.connect('tickets.db')
    c = conn.cursor()
    
    created = datetime.now(TIMEZONE)
    deadline = calculate_deadline()
    
    c.execute('''INSERT INTO tickets 
                 (user_id, fio, position, phone, priority, category, problem, created, deadline)
                 VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)''',
              (update.message.from_user.id,
               user_data['fio'],
               user_data['position'],
               user_data['phone'],
               user_data['priority'],
               user_data['category'],
               user_data['problem'],
               created,
               deadline))
    
    ticket_id = c.lastrowid
    conn.commit()
    conn.close()
    
    # Уведомление администраторов
    for admin_id in ADMIN_IDS:
        try:
            context.bot.send_message(
                admin_id,
                f"🎫 Новая заявка #{ticket_id}\n"
                f"👤 {user_data['fio']}\n"
                f"💼 {user_data['position']}\n"
                f"📞 {user_data['phone']}\n"
                f"🚩 {user_data['priority']}\n"
                f"📁 {user_data['category']}\n"
                f"📝 {user_data['problem']}\n"
                f"⏰ Дедлайн: {deadline.strftime('%d.%m.%Y %H:%M')}"
            )
        except Exception as e:
            logger.error(f"Ошибка отправки уведомления администратору {admin_id}: {e}")
    
    update.message.reply_text(
        f"✅ Заявка #{ticket_id} создана!\n"
        f"Дедлайн: {deadline.strftime('%d.%m.%Y %H:%M')}",
        reply_markup=main_keyboard()
    )
    
    # Очистка временных данных
    context.user_data.clear()
    
    return ConversationHandler.END

# Отмена разговора
def cancel(update: Update, context: CallbackContext):
    update.message.reply_text(
        'Создание заявки отменено.',
        reply_markup=main_keyboard()
    )
    return ConversationHandler.END

# Проверка статуса заявок
def check_status(update: Update, context: CallbackContext):
    user_id = update.message.from_user.id
    conn = sqlite3.connect('tickets.db')
    c = conn.cursor()
    
    c.execute('''SELECT id, created, deadline, status, admin_comment 
                 FROM tickets WHERE user_id = ? ORDER BY created DESC LIMIT 5''',
              (user_id,))
    
    tickets = c.fetchall()
    conn.close()
    
    if not tickets:
        update.message.reply_text("У вас нет созданных заявок.")
        return
    
    response = "📊 Ваши последние заявки:\n\n"
    for ticket in tickets:
        status_icons = {'new': '🆕', 'in_progress': '🔄', 'done': '✅', 'overdue': '❌'}
        status_icon = status_icons.get(ticket[3], '📄')
        
        response += (f"{status_icon} Заявка #{ticket[0]}\n"
                    f"📅 Создана: {ticket[1][:16]}\n"
                    f"⏰ Дедлайн: {ticket[2][:16]}\n"
                    f"📋 Статус: {ticket[3]}\n")
        
        if ticket[4]:
            response += f"💬 Комментарий: {ticket[4]}\n"
        response += "\n"
    
    update.message.reply_text(response)

# Обработка ошибок
def error_handler(update: Update, context: CallbackContext):
    logger.error(msg="Исключение при обработке сообщения:", exc_info=context.error)
    
    try:
        update.message.reply_text(
            "❌ Произошла ошибка. Пожалуйста, попробуйте позже.",
            reply_markup=main_keyboard()
        )
    except:
        pass

def main():
    # Инициализация БД
    init_db()
    
    # Создание updater и dispatcher
    updater = Updater("8278600298:AAGPjUhyU5HxXOaLRvu-FSRldBW_UCmwOME", use_context=True)  # Замените на ваш токен
    dp = updater.dispatcher

    # Обработчики команд
    dp.add_handler(CommandHandler("start", start))
    
    # Обработчик создания заявки
    conv_handler = ConversationHandler(
        entry_points=[MessageHandler(Filters.regex('^(📝 Создать заявку)$'), create_ticket_start)],
        states={
            FIO: [MessageHandler(Filters.text & ~Filters.command, fio_received)],
            POSITION: [MessageHandler(Filters.text & ~Filters.command, position_received)],
            PHONE: [MessageHandler(Filters.text & ~Filters.command, phone_received)],
            PRIORITY: [MessageHandler(Filters.text & ~Filters.command, priority_received)],
            CATEGORY: [MessageHandler(Filters.text & ~Filters.command, category_received)],
            PROBLEM: [MessageHandler(Filters.text & ~Filters.command, problem_received)],
        },
        fallbacks=[CommandHandler('cancel', cancel)]
    )
    
    dp.add_handler(conv_handler)
    dp.add_handler(MessageHandler(Filters.regex('^(📊 Статус заявок)$'), check_status))
    
    # Обработчик ошибок
    dp.add_error_handler(error_handler)

    # Запуск бота
    updater.start_polling()
    updater.idle()

if __name__ == '__main__':
    main()
