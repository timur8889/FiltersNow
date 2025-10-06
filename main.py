import logging
from telegram import Update, ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes, CallbackQueryHandler, ConversationHandler
from telegram.error import BadRequest
import sqlite3
from datetime import datetime, timedelta
import json
import io
import csv
from calendar import monthrange
import matplotlib.pyplot as plt
import matplotlib
matplotlib.use('Agg')  # Для работы без GUI

# Настройка логирования
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Токен бота
BOT_TOKEN = "8278600298:AAFA-R0ql-dibAoBruxgwitHTx_LLx61OdM"

# Состояния для ConversationHandler
FILTER_NAME, FILTER_LOCATION, FILTER_LAST_CHANGE, FILTER_INTERVAL, FILTER_COST, FILTER_PHOTO = range(6)
GROUP_NAME, GROUP_MEMBERS = range(2)

# Инициализация базы данных
def init_db():
    conn = sqlite3.connect('filters.db')
    cursor = conn.cursor()
    
    # Основная таблица фильтров
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS filters (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            location TEXT,
            last_change DATE,
            next_change DATE,
            change_interval INTEGER,
            cost REAL DEFAULT 0,
            status TEXT DEFAULT 'active',
            user_id INTEGER,
            group_id INTEGER,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (group_id) REFERENCES groups (id)
        )
    ''')
    
    # Таблица уведомлений
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS notifications (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            filter_id INTEGER,
            notification_date DATE,
            sent BOOLEAN DEFAULT FALSE,
            FOREIGN KEY (filter_id) REFERENCES filters (id)
        )
    ''')
    
    # Таблица групп пользователей
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS groups (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            admin_id INTEGER,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    # Таблица участников групп
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS group_members (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            group_id INTEGER,
            user_id INTEGER,
            user_name TEXT,
            joined_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (group_id) REFERENCES groups (id)
        )
    ''')
    
    # Таблица истории замен
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS replacement_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            filter_id INTEGER,
            replacement_date DATE,
            cost REAL,
            photo_id TEXT,
            notes TEXT,
            replaced_by INTEGER,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
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
        [KeyboardButton("🔔 Уведомления"), KeyboardButton("📊 Статистика")],
        [KeyboardButton("👥 Группы"), KeyboardButton("📅 Календарь")],
        [KeyboardButton("📸 Фотоотчеты"), KeyboardButton("📈 Аналитика")]
    ]
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    
    await update.message.reply_text(
        f"Привет, {user.first_name}! 👋\n\n"
        "Я помогу тебе контролировать состояние фильтров.\n"
        "Выбери действие:",
        reply_markup=reply_markup
    )

# === СТАТИСТИКА ПО ЗАМЕНАМ ===

async def show_statistics(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    conn = sqlite3.connect('filters.db')
    cursor = conn.cursor()
    
    # Общая статистика
    cursor.execute('''
        SELECT 
            COUNT(*) as total_filters,
            SUM(CASE WHEN status = 'active' THEN 1 ELSE 0 END) as active_filters,
            SUM(CASE WHEN next_change <= date('now') THEN 1 ELSE 0 END) as expired_filters
        FROM filters WHERE user_id = ?
    ''', (user_id,))
    
    stats = cursor.fetchone()
    
    # Статистика по заменам
    cursor.execute('''
        SELECT 
            COUNT(*) as total_replacements,
            AVG(rh.cost) as avg_cost,
            SUM(rh.cost) as total_cost
        FROM replacement_history rh
        JOIN filters f ON rh.filter_id = f.id
        WHERE f.user_id = ?
    ''', (user_id,))
    
    replacement_stats = cursor.fetchone()
    
    # Статистика по экономии
    cursor.execute('''
        SELECT 
            f.name,
            COUNT(rh.id) as replacement_count,
            AVG(rh.cost) as avg_cost
        FROM filters f
        LEFT JOIN replacement_history rh ON f.id = rh.filter_id
        WHERE f.user_id = ?
        GROUP BY f.id
        ORDER BY replacement_count DESC
        LIMIT 5
    ''', (user_id,))
    
    top_filters = cursor.fetchall()
    
    conn.close()
    
    message = "📊 Статистика по фильтрам:\n\n"
    message += f"📋 Всего фильтров: {stats[0]}\n"
    message += f"✅ Активных: {stats[1]}\n"
    message += f"⚠️ Требуют замены: {stats[2]}\n\n"
    
    if replacement_stats[0] > 0:
        message += "💰 Статистика замен:\n"
        message += f"🔄 Всего замен: {replacement_stats[0]}\n"
        message += f"💵 Общие затраты: {replacement_stats[2]:.2f} руб.\n"
        message += f"📊 Средняя стоимость: {replacement_stats[1]:.2f} руб.\n\n"
    
    if top_filters:
        message += "🏆 Чаще всего заменяются:\n"
        for name, count, avg_cost in top_filters:
            message += f"• {name}: {count} замен"
            if avg_cost:
                message += f" ({avg_cost:.2f} руб.)"
            message += "\n"
    
    # Клавиатура для дополнительной статистики
    keyboard = [
        [InlineKeyboardButton("📈 Графики", callback_data="stats_charts")],
        [InlineKeyboardButton("📄 Отчет PDF", callback_data="stats_pdf")],
        [InlineKeyboardButton("📊 Детальная аналитика", callback_data="detailed_stats")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(message, reply_markup=reply_markup)

# Генерация графиков статистики
async def generate_statistics_charts(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    conn = sqlite3.connect('filters.db')
    cursor = conn.cursor()
    
    # Статистика замен по месяцам
    cursor.execute('''
        SELECT 
            strftime('%Y-%m', replacement_date) as month,
            COUNT(*) as count,
            SUM(cost) as total_cost
        FROM replacement_history rh
        JOIN filters f ON rh.filter_id = f.id
        WHERE f.user_id = ?
        GROUP BY month
        ORDER BY month
        LIMIT 12
    ''', (user_id,))
    
    monthly_data = cursor.fetchall()
    
    if not monthly_data:
        await update.callback_query.answer("Нет данных для построения графиков")
        return
    
    months = [f"{row[0][5:7]}/{row[0][:4]}" for row in monthly_data]
    counts = [row[1] for row in monthly_data]
    costs = [row[2] for row in monthly_data]
    
    # Создание графиков
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(10, 8))
    
    # График количества замен
    ax1.bar(months, counts, color='skyblue')
    ax1.set_title('Количество замен по месяцам')
    ax1.set_ylabel('Количество замен')
    
    # График затрат
    ax2.bar(months, costs, color='lightcoral')
    ax2.set_title('Затраты на замены по месяцам')
    ax2.set_ylabel('Затраты (руб.)')
    ax2.tick_params(axis='x', rotation=45)
    
    plt.tight_layout()
    
    # Сохранение в буфер
    buf = io.BytesIO()
    plt.savefig(buf, format='png', dpi=150)
    buf.seek(0)
    plt.close()
    
    # Отправка графика
    await context.bot.send_photo(
        chat_id=update.effective_chat.id,
        photo=buf,
        caption="📈 Статистика замен по месяцам"
    )
    
    conn.close()

# === ГРУППОВОЕ УПРАВЛЕНИЕ ФИЛЬТРАМИ ===

async def group_management(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton("👥 Мои группы", callback_data="my_groups")],
        [InlineKeyboardButton("➕ Создать группу", callback_data="create_group")],
        [InlineKeyboardButton("🔗 Присоединиться к группе", callback_data="join_group")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(
        "👥 Управление группами\n\n"
        "Создавайте группы для совместного контроля фильтров "
        "в семье, офисе или команде.",
        reply_markup=reply_markup
    )

# Создание группы
async def create_group_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.message.reply_text(
        "Введите название для новой группы:"
    )
    return GROUP_NAME

async def create_group_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['group_name'] = update.message.text
    await update.message.reply_text(
        "Отлично! Теперь отправьте мне @username участников через запятую.\n"
        "Например: @user1, @user2, @user3"
    )
    return GROUP_MEMBERS

async def create_group_members(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    group_name = context.user_data['group_name']
    members_text = update.message.text
    
    # Создание группы в базе данных
    conn = sqlite3.connect('filters.db')
    cursor = conn.cursor()
    
    cursor.execute(
        'INSERT INTO groups (name, admin_id) VALUES (?, ?)',
        (group_name, user_id)
    )
    group_id = cursor.lastrowid
    
    # Добавление администратора как участника
    cursor.execute(
        'INSERT INTO group_members (group_id, user_id, user_name) VALUES (?, ?, ?)',
        (group_id, user_id, update.effective_user.first_name)
    )
    
    # Обработка участников (упрощенная версия)
    members = [member.strip() for member in members_text.split(',')]
    for member in members:
        if member.startswith('@'):
            cursor.execute(
                'INSERT INTO group_members (group_id, user_name) VALUES (?, ?)',
                (group_id, member)
            )
    
    conn.commit()
    conn.close()
    
    await update.message.reply_text(
        f"✅ Группа '{group_name}' создана!\n"
        f"Участники: {members_text}\n\n"
        f"Для приглашения участников отправьте им код группы: `GROUP_{group_id}`",
        parse_mode='Markdown'
    )
    
    return ConversationHandler.END

# Показать группы пользователя
async def show_my_groups(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    conn = sqlite3.connect('filters.db')
    cursor = conn.cursor()
    
    cursor.execute('''
        SELECT g.id, g.name, COUNT(gm.id) as member_count
        FROM groups g
        JOIN group_members gm ON g.id = gm.group_id
        WHERE gm.user_id = ? OR g.admin_id = ?
        GROUP BY g.id
    ''', (user_id, user_id))
    
    groups = cursor.fetchall()
    
    if not groups:
        await update.callback_query.message.reply_text("Вы не состоите ни в одной группе.")
        return
    
    message = "👥 Ваши группы:\n\n"
    keyboard = []
    
    for group_id, name, member_count in groups:
        message += f"🏠 {name}\n"
        message += f"👥 Участников: {member_count}\n"
        message += f"🔗 Код: `GROUP_{group_id}`\n"
        message += "─" * 20 + "\n"
        
        keyboard.append([InlineKeyboardButton(
            f"📋 Фильтры {name}", 
            callback_data=f"group_filters_{group_id}"
        )])
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.callback_query.message.reply_text(message, reply_markup=reply_markup)
    
    conn.close()

# === ИНТЕГРАЦИЯ С КАЛЕНДАРЕМ ===

async def calendar_integration(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    conn = sqlite3.connect('filters.db')
    cursor = conn.cursor()
    
    # Получаем предстоящие замены
    cursor.execute('''
        SELECT name, next_change, location 
        FROM filters 
        WHERE user_id = ? AND status = 'active'
        ORDER BY next_change
        LIMIT 10
    ''', (user_id,))
    
    upcoming = cursor.fetchall()
    
    message = "📅 Календарь замен:\n\n"
    
    if not upcoming:
        message += "Нет предстоящих замен."
    else:
        for name, next_change, location in upcoming:
            days_left = (datetime.strptime(next_change, '%Y-%m-%d').date() - datetime.now().date()).days
            message += f"📌 {name}\n"
            message += f"📍 {location}\n"
            message += f"📅 {next_change} ({days_left} дней)\n"
            message += "─" * 20 + "\n"
    
    # Генерация iCal файла
    ical_content = generate_ical_content(user_id)
    buf = io.BytesIO(ical_content.encode('utf-8'))
    buf.name = 'filter_calendar.ics'
    
    keyboard = [
        [InlineKeyboardButton("📆 Экспорт в Google Calendar", callback_data="export_gcal")],
        [InlineKeyboardButton("📅 Показать на месяц", callback_data="show_monthly")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(
        message,
        reply_markup=reply_markup
    )
    
    # Отправка iCal файла
    await update.message.reply_document(
        document=buf,
        filename='filter_calendar.ics',
        caption="📥 Календарь замен (импортируйте в любой календарь)"
    )
    
    conn.close()

def generate_ical_content(user_id):
    conn = sqlite3.connect('filters.db')
    cursor = conn.cursor()
    
    cursor.execute('''
        SELECT name, next_change, location 
        FROM filters 
        WHERE user_id = ? AND status = 'active'
    ''', (user_id,))
    
    filters_list = cursor.fetchall()
    
    ical = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//FilterBot//Filter Calendar//RU",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH"
    ]
    
    for name, next_change, location in filters_list:
        event_uid = f"filter_{user_id}_{name}_{next_change}"
        ical.extend([
            "BEGIN:VEVENT",
            f"UID:{event_uid}",
            f"DTSTART;VALUE=DATE:{next_change.replace('-', '')}",
            f"DTEND;VALUE=DATE:{next_change.replace('-', '')}",
            f"SUMMARY:Замена фильтра {name}",
            f"DESCRIPTION:Запланирована замена фильтра {name} в {location}",
            "STATUS:CONFIRMED",
            "END:VEVENT"
        ])
    
    ical.append("END:VCALENDAR")
    
    conn.close()
    return "\n".join(ical)

# === ФОТОФИКСАЦИЯ ЗАМЕН ФИЛЬТРОВ ===

async def photo_reports(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    conn = sqlite3.connect('filters.db')
    cursor = conn.cursor()
    
    # Получаем историю замен с фото
    cursor.execute('''
        SELECT f.name, rh.replacement_date, rh.photo_id, rh.notes
        FROM replacement_history rh
        JOIN filters f ON rh.filter_id = f.id
        WHERE f.user_id = ? AND rh.photo_id IS NOT NULL
        ORDER BY rh.replacement_date DESC
        LIMIT 10
    ''', (user_id,))
    
    photos_history = cursor.fetchall()
    
    message = "📸 История фотоотчетов:\n\n"
    
    if not photos_history:
        message += "Пока нет фотоотчетов о заменах."
        await update.message.reply_text(message)
        return
    
    for name, date, photo_id, notes in photos_history:
        message += f"📌 {name}\n"
        message += f"📅 {date}\n"
        if notes:
            message += f"📝 {notes}\n"
        message += "─" * 20 + "\n"
    
    await update.message.reply_text(message)
    
    # Отправка последних фото
    for name, date, photo_id, notes in photos_history[:3]:  # Первые 3 фото
        try:
            await context.bot.send_photo(
                chat_id=update.effective_chat.id,
                photo=photo_id,
                caption=f"📸 {name} - {date}"
            )
        except BadRequest:
            await update.message.reply_text(f"Фото для {name} недоступно")

# Обработка фото при замене фильтра
async def handle_replacement_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.photo and context.user_data.get('awaiting_replacement_photo'):
        photo_id = update.message.photo[-1].file_id
        context.user_data['replacement_photo_id'] = photo_id
        
        # Сохранение в базу данных
        filter_id = context.user_data['replacement_filter_id']
        conn = sqlite3.connect('filters.db')
        cursor = conn.cursor()
        
        cursor.execute('''
            UPDATE replacement_history 
            SET photo_id = ? 
            WHERE filter_id = ? 
            ORDER BY id DESC LIMIT 1
        ''', (photo_id, filter_id))
        
        conn.commit()
        conn.close()
        
        context.user_data['awaiting_replacement_photo'] = False
        
        await update.message.reply_text(
            "✅ Фото сохранено! Замена фильтра завершена с фотоотчетом."
        )

# === ОТЧЕТЫ И АНАЛИТИКА ===

async def analytics_reports(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton("📈 Финансовый отчет", callback_data="financial_report")],
        [InlineKeyboardButton("📊 Эффективность замен", callback_data="efficiency_report")],
        [InlineKeyboardButton("📋 Полный отчет (CSV)", callback_data="full_report_csv")],
        [InlineKeyboardButton("📅 Плановый отчет", callback_data="planning_report")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(
        "📈 Аналитика и отчеты\n\n"
        "Выберите тип отчета для генерации:",
        reply_markup=reply_markup
    )

# Генерация финансового отчета
async def generate_financial_report(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    conn = sqlite3.connect('filters.db')
    cursor = conn.cursor()
    
    # Финансовая статистика по месяцам
    cursor.execute('''
        SELECT 
            strftime('%Y-%m', replacement_date) as month,
            SUM(cost) as monthly_cost,
            COUNT(*) as replacements_count
        FROM replacement_history rh
        JOIN filters f ON rh.filter_id = f.id
        WHERE f.user_id = ?
        GROUP BY month
        ORDER BY month DESC
        LIMIT 6
    ''', (user_id,))
    
    financial_data = cursor.fetchall()
    
    if not financial_data:
        await update.callback_query.answer("Нет данных для отчета")
        return
    
    message = "💰 Финансовый отчет:\n\n"
    
    total_cost = 0
    for month, monthly_cost, count in financial_data:
        message += f"📅 {month[5:7]}/{month[:4]}:\n"
        message += f"   💵 Затраты: {monthly_cost:.2f} руб.\n"
        message += f"   🔄 Замен: {count}\n"
        message += f"   📊 Средняя стоимость: {monthly_cost/count:.2f} руб.\n\n"
        total_cost += monthly_cost
    
    message += f"💵 Общие затраты за период: {total_cost:.2f} руб.\n"
    
    # Прогноз на следующий месяц
    cursor.execute('''
        SELECT AVG(cost) as avg_cost, COUNT(*) as upcoming
        FROM filters 
        WHERE user_id = ? AND status = 'active' 
        AND next_change BETWEEN date('now') AND date('now', '+1 month')
    ''', (user_id,))
    
    forecast = cursor.fetchone()
    
    if forecast[1] > 0:
        forecast_cost = forecast[0] * forecast[1]
        message += f"\n📈 Прогноз на следующий месяц:\n"
        message += f"   🔄 Плановых замен: {forecast[1]}\n"
        message += f"   💰 Примерные затраты: {forecast_cost:.2f} руб.\n"
    
    await update.callback_query.message.reply_text(message)
    conn.close()

# Генерация CSV отчета
async def generate_csv_report(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    conn = sqlite3.connect('filters.db')
    cursor = conn.cursor()
    
    # Полные данные по заменам
    cursor.execute('''
        SELECT 
            f.name,
            f.location,
            rh.replacement_date,
            rh.cost,
            rh.notes,
            CASE WHEN rh.photo_id IS NOT NULL THEN 'Да' ELSE 'Нет' END as has_photo
        FROM replacement_history rh
        JOIN filters f ON rh.filter_id = f.id
        WHERE f.user_id = ?
        ORDER BY rh.replacement_date DESC
    ''', (user_id,))
    
    data = cursor.fetchall()
    
    if not data:
        await update.callback_query.answer("Нет данных для отчета")
        return
    
    # Создание CSV в памяти
    output = io.StringIO()
    writer = csv.writer(output)
    
    # Заголовки
    writer.writerow(['Фильтр', 'Местоположение', 'Дата замены', 'Стоимость', 'Примечания', 'Фотоотчет'])
    
    # Данные
    for row in data:
        writer.writerow(row)
    
    # Подготовка файла для отправки
    buf = io.BytesIO()
    buf.write(output.getvalue().encode('utf-8'))
    buf.seek(0)
    buf.name = 'filter_report.csv'
    
    await context.bot.send_document(
        chat_id=update.effective_chat.id,
        document=buf,
        filename='filter_report.csv',
        caption="📊 Полный отчет по заменам фильтров"
    )
    
    conn.close()

# Основная функция
def main():
    # Инициализация базы данных
    init_db()
    
    # Создание приложения
    application = Application.builder().token(BOT_TOKEN).build()
    
    # Базовые обработчики
    application.add_handler(CommandHandler("start", start))
    application.add_handler(MessageHandler(filters.Text("📋 Мои фильтры"), show_filters))
    application.add_handler(MessageHandler(filters.Text("📊 Статистика"), show_statistics))
    application.add_handler(MessageHandler(filters.Text("👥 Группы"), group_management))
    application.add_handler(MessageHandler(filters.Text("📅 Календарь"), calendar_integration))
    application.add_handler(MessageHandler(filters.Text("📸 Фотоотчеты"), photo_reports))
    application.add_handler(MessageHandler(filters.Text("📈 Аналитика"), analytics_reports))
    
    # Обработчики callback запросов
    application.add_handler(CallbackQueryHandler(generate_statistics_charts, pattern="^stats_charts$"))
    application.add_handler(CallbackQueryHandler(show_my_groups, pattern="^my_groups$"))
    application.add_handler(CallbackQueryHandler(generate_financial_report, pattern="^financial_report$"))
    application.add_handler(CallbackQueryHandler(generate_csv_report, pattern="^full_report_csv$"))
    
    # Обработчик фото
    application.add_handler(MessageHandler(filters.PHOTO, handle_replacement_photo))
    
    # ConversationHandler для создания группы
    group_conv_handler = ConversationHandler(
        entry_points=[CallbackQueryHandler(create_group_start, pattern="^create_group$")],
        states={
            GROUP_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, create_group_name)],
            GROUP_MEMBERS: [MessageHandler(filters.TEXT & ~filters.COMMAND, create_group_members)]
        },
        fallbacks=[]
    )
    application.add_handler(group_conv_handler)
    
    # Планировщик для уведомлений
    job_queue = application.job_queue
    job_queue.run_repeating(check_expired_filters, interval=86400, first=10)
    job_queue.run_repeating(send_upcoming_notifications, interval=86400, first=10)
    
    # Запуск бота
    application.run_polling()

if __name__ == '__main__':
    main()
