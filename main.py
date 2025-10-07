import os
import logging
from datetime import datetime, time, timedelta
from telegram import Update, ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, ContextTypes, filters
import gspread
from google.oauth2.service_account import Credentials
import pytz

# Настройка логирования
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Конфигурация
BOT_TOKEN = "8278600298:AAGPjUhyU5HxXOaLRvu-FSRldBW_UCmwOME"
TIMEZONE = pytz.timezone('Europe/Moscow')

# Настройки Google Sheets
SCOPES = ['https://www.googleapis.com/auth/spreadsheets']
CREDENTIALS_FILE = 'credentials.json'
SPREADSHEET_ID = '1CBhuVDTgH-RaMzJ-sDW-vsS4mvjvh2fGFKztlVb2SFQ'

# Приоритеты заявок
PRIORITIES = {
    'low': '🔵 Низкий',
    'medium': '🟡 Средний', 
    'high': '🟠 Высокий',
    'critical': '🔴 Критический'
}

# Категории проблем
CATEGORIES = {
    'network': '📶 Проблемы с сетью',
    'phone': '📞 Телефонная связь',
    'cctv': '📹 Видеонаблюдение',
    'access': '🔐 Система доступа',
    'other': '❓ Другое'
}

# Состояния для обработки заявок
class States:
    WAITING_FIO = 1
    WAITING_POSITION = 2
    WAITING_PHONE = 3
    WAITING_CATEGORY = 4
    WAITING_PRIORITY = 5
    WAITING_DESCRIPTION = 6
    WAITING_COMMENT = 7

# Инициализация Google Sheets
def init_google_sheets():
    try:
        creds = Credentials.from_service_account_file(CREDENTIALS_FILE, scopes=SCOPES)
        client = gspread.authorize(creds)
        spreadsheet = client.open_by_key(SPREADSHEET_ID)
        return spreadsheet.sheet1
    except Exception as e:
        logger.error(f"Ошибка инициализации Google Sheets: {e}")
        return None

# Проверка рабочего времени
def is_work_time():
    now = datetime.now(TIMEZONE)
    if now.weekday() >= 5:  # 5=суббота, 6=воскресенье
        return False
    
    work_start = time(9, 0)
    work_end = time(17, 0)
    current_time = now.time()
    
    return work_start <= current_time <= work_end

# Расчет времени выполнения с учетом рабочих часов
def calculate_due_date(create_time):
    create_dt = create_time.astimezone(TIMEZONE)
    
    # Если заявка создана после 22:00, переносим на следующий день 9:00
    if create_dt.time() > time(22, 0):
        create_dt = create_dt.replace(hour=9, minute=0, second=0) + timedelta(days=1)
    
    # Пропускаем выходные
    while create_dt.weekday() >= 5:
        create_dt += timedelta(days=1)
    
    # Добавляем 48 рабочих часов
    work_hours_added = 0
    due_date = create_dt
    
    while work_hours_added < 48:
        due_date += timedelta(hours=1)
        
        # Пропускаем нерабочее время и выходные
        if due_date.weekday() < 5 and time(9, 0) <= due_date.time() <= time(17, 0):
            work_hours_added += 1
            
        # Если достигли конца рабочего дня, переходим к следующему утру
        if due_date.time() > time(17, 0):
            due_date = due_date.replace(hour=9, minute=0, second=0) + timedelta(days=1)
            
        # Пропускаем выходные
        while due_date.weekday() >= 5:
            due_date += timedelta(days=1)
    
    return due_date

# Генерация номера заявки
def generate_ticket_number():
    return f"T{datetime.now().strftime('%Y%m%d%H%M%S')}"

# Главное меню
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [KeyboardButton("📝 Создать заявку")],
        [KeyboardButton("📊 Мои заявки"), KeyboardButton("🆘 Помощь")]
    ]
    
    if str(update.effective_user.id) in get_admins():
        keyboard.append([KeyboardButton("⚙️ Панель администратора")])
    
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    
    await update.message.reply_text(
        "Добро пожаловать в систему заявок по слаботочным системам!\n"
        "Выберите действие:",
        reply_markup=reply_markup
    )

# Получение списка администраторов
def get_admins():
    return ["5024165375"]  # Замените на реальные ID

# Начало создания заявки
async def create_ticket_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_work_time():
        await update.message.reply_text(
            "⚠️ Прием заявок осуществляется только в рабочее время:\n"
            "Пн-Пт с 9:00 до 17:00\n\n"
            "Ваша заявка будет зарегистрирована в начале следующего рабочего дня."
        )
    
    context.user_data['ticket'] = {
        'user_id': update.effective_user.id,
        'username': update.effective_user.username or '',
        'created_at': datetime.now(TIMEZONE)
    }
    
    await update.message.reply_text(
        "Для создания заявки введите вашу Фамилию и Имя:"
    )
    return States.WAITING_FIO

# Обработка ФИО
async def process_fio(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['ticket']['fio'] = update.message.text
    await update.message.reply_text("Введите вашу должность:")
    return States.WAITING_POSITION

# Обработка должности
async def process_position(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['ticket']['position'] = update.message.text
    await update.message.reply_text("Введите ваш номер телефона:")
    return States.WAITING_PHONE

# Обработка телефона
async def process_phone(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['ticket']['phone'] = update.message.text
    
    # Клавиатура с категориями
    keyboard = [
        [InlineKeyboardButton(cat_name, callback_data=cat_id)]
        for cat_id, cat_name in CATEGORIES.items()
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(
        "Выберите категорию проблемы:",
        reply_markup=reply_markup
    )
    return States.WAITING_CATEGORY

# Обработка категории
async def process_category(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    context.user_data['ticket']['category'] = query.data
    context.user_data['ticket']['category_name'] = CATEGORIES[query.data]
    
    # Клавиатура с приоритетами
    keyboard = [
        [InlineKeyboardButton(pri_name, callback_data=pri_id)]
        for pri_id, pri_name in PRIORITIES.items()
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await query.edit_message_text(
        f"Категория: {CATEGORIES[query.data]}\n"
        "Выберите приоритет заявки:",
        reply_markup=reply_markup
    )
    return States.WAITING_PRIORITY

# Обработка приоритета
async def process_priority(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    context.user_data['ticket']['priority'] = query.data
    context.user_data['ticket']['priority_name'] = PRIORITIES[query.data]
    
    await query.edit_message_text(
        f"Приоритет: {PRIORITIES[query.data]}\n"
        "Теперь опишите проблему подробно:"
    )
    return States.WAITING_DESCRIPTION

# Обработка описания и завершение заявки
async def process_description(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ticket_data = context.user_data['ticket']
    ticket_data['description'] = update.message.text
    ticket_data['ticket_number'] = generate_ticket_number()
    ticket_data['due_date'] = calculate_due_date(ticket_data['created_at'])
    
    # Сохранение заявки
    if save_ticket_to_sheets(ticket_data):
        # Уведомление администраторов
        await notify_admins(context, ticket_data)
        
        await update.message.reply_text(
            f"✅ Заявка #{ticket_data['ticket_number']} успешно создана!\n\n"
            f"📋 Данные заявки:\n"
            f"👤 ФИО: {ticket_data['fio']}\n"
            f"💼 Должность: {ticket_data['position']}\n"
            f"📞 Телефон: {ticket_data['phone']}\n"
            f"📂 Категория: {ticket_data['category_name']}\n"
            f"🚨 Приоритет: {ticket_data['priority_name']}\n"
            f"📝 Описание: {ticket_data['description']}\n"
            f"⏰ Срок выполнения: {ticket_data['due_date'].strftime('%d.%m.%Y %H:%M')}\n\n"
            f"Вы будете уведомлены об изменении статуса заявки."
        )
    else:
        await update.message.reply_text(
            "❌ Произошла ошибка при сохранении заявки. Пожалуйста, попробуйте позже."
        )
    
    context.user_data.clear()
    return -1

# Сохранение заявки в Google Sheets
def save_ticket_to_sheets(ticket_data):
    try:
        worksheet = init_google_sheets()
        if not worksheet:
            return False
            
        row = [
            ticket_data['ticket_number'],
            ticket_data['created_at'].strftime('%d.%m.%Y %H:%M'),
            ticket_data['fio'],
            ticket_data['position'],
            ticket_data['phone'],
            ticket_data['category_name'],
            ticket_data['priority_name'],
            ticket_data['description'],
            "Новая",
            "",  # Исполнитель
            "",  # Комментарий
            ticket_data['due_date'].strftime('%d.%m.%Y %H:%M'),
            str(ticket_data['user_id']),
            ticket_data.get('username', '')
        ]
        
        worksheet.append_row(row)
        return True
    except Exception as e:
        logger.error(f"Ошибка сохранения в Google Sheets: {e}")
        return False

# Уведомление администраторов
async def notify_admins(context: ContextTypes.DEFAULT_TYPE, ticket_data):
    admins = get_admins()
    message = (
        f"🚨 НОВАЯ ЗАЯВКА #{ticket_data['ticket_number']}\n\n"
        f"👤 ФИО: {ticket_data['fio']}\n"
        f"💼 Должность: {ticket_data['position']}\n"
        f"📞 Телефон: {ticket_data['phone']}\n"
        f"📂 Категория: {ticket_data['category_name']}\n"
        f"🚨 Приоритет: {ticket_data['priority_name']}\n"
        f"📝 Описание: {ticket_data['description']}\n"
        f"⏰ Срок выполнения: {ticket_data['due_date'].strftime('%d.%m.%Y %H:%M')}"
    )
    
    for admin_id in admins:
        try:
            await context.bot.send_message(chat_id=admin_id, text=message)
        except Exception as e:
            logger.error(f"Ошибка отправки уведомления администратору {admin_id}: {e}")

# Панель администратора
async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if str(update.effective_user.id) not in get_admins():
        await update.message.reply_text("⛔ Доступ запрещен")
        return
    
    keyboard = [
        [KeyboardButton("📋 Все заявки"), KeyboardButton("⏰ Просроченные")],
        [KeyboardButton("📊 Статистика"), KeyboardButton("🔙 Назад")]
    ]
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    
    await update.message.reply_text(
        "⚙️ Панель администратора",
        reply_markup=reply_markup
    )

# Обработка текстовых сообщений
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    
    if text == "📝 Создать заявку":
        return await create_ticket_start(update, context)
    elif text == "⚙️ Панель администратора":
        return await admin_panel(update, context)
    elif text == "🔙 Назад":
        return await start(update, context)
    else:
        await update.message.reply_text("Используйте кнопки меню для навигации")

# Обработка ошибок
async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.error(f"Ошибка: {context.error}", exc_info=context.error)
    
    if update and update.effective_message:
        await update.effective_message.reply_text(
            "❌ Произошла непредвиденная ошибка. Пожалуйста, попробуйте позже."
        )

# Основная функция
def main():
    application = Application.builder().token(BOT_TOKEN).build()
    
    # Обработчики команд
    application.add_handler(CommandHandler("start", start))
    
    # Обработчики состояний для создания заявки
    ticket_conv_handler = MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message)
    application.add_handler(ticket_conv_handler)
    
    # Обработчики callback-запросов
    application.add_handler(CallbackQueryHandler(process_category, pattern="^(" + "|".join(CATEGORIES.keys()) + ")$"))
    application.add_handler(CallbackQueryHandler(process_priority, pattern="^(" + "|".join(PRIORITIES.keys()) + ")$"))
    
    # Обработчик ошибок
    application.add_error_handler(error_handler)
    
    # Запуск бота
    application.run_polling()

if __name__ == "__main__":
    main()
