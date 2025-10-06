import os
import logging
from datetime import datetime, timedelta
import sqlite3
import pandas as pd
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters, ContextTypes
import openpyxl
from openpyxl import Workbook
import asyncio

# Настройка логирования
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Конфигурация
DATABASE_NAME = 'filters.db'
EXCEL_FILE = 'filters_data.xlsx'

# Инициализация базы данных
def init_db():
    conn = sqlite3.connect(DATABASE_NAME)
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS filters (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            filter_name TEXT NOT NULL,
            installation_date TEXT NOT NULL,
            replacement_date TEXT NOT NULL,
            notification_sent INTEGER DEFAULT 0
        )
    ''')
    conn.commit()
    conn.close()

# Инициализация Excel файла
def init_excel():
    if not os.path.exists(EXCEL_FILE):
        wb = Workbook()
        ws = wb.active
        ws.title = "Фильтры"
        ws.append(['ID', 'Пользователь', 'Название фильтра', 'Дата установки', 'Дата замены', 'Статус'])
        wb.save(EXCEL_FILE)

# Главное меню
def main_menu_keyboard():
    keyboard = [
        ['📋 Список фильтров', '➕ Добавить фильтр'],
        ['🗑️ Удалить фильтр', '📊 Экспорт в Excel'],
        ['📅 Синхронизация с календарем']
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

# Клавиатура для подтверждения удаления
def confirmation_keyboard(filter_id):
    keyboard = [
        [
            InlineKeyboardButton("✅ Да", callback_data=f"confirm_delete_{filter_id}"),
            InlineKeyboardButton("❌ Нет", callback_data="cancel_delete")
        ]
    ]
    return InlineKeyboardMarkup(keyboard)

# Команда /start
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.message.from_user
    await update.message.reply_text(
        f"Привет, {user.first_name}! 👋\n\n"
        "Я бот для контроля замены фильтров.\n"
        "Выберите действие из меню ниже:",
        reply_markup=main_menu_keyboard()
    )

# Показать список фильтров
async def show_filters(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    conn = sqlite3.connect(DATABASE_NAME)
    cursor = conn.cursor()
    
    cursor.execute('''
        SELECT id, filter_name, installation_date, replacement_date 
        FROM filters WHERE user_id = ?
    ''', (user_id,))
    
    filters = cursor.fetchall()
    conn.close()
    
    if not filters:
        await update.message.reply_text("У вас пока нет добавленных фильтров.")
        return
    
    message = "📋 Ваши фильтры:\n\n"
    for filter_item in filters:
        filter_id, name, install_date, replace_date = filter_item
        days_left = (datetime.strptime(replace_date, '%Y-%m-%d') - datetime.now()).days
        status = "🔴 Просрочен" if days_left < 0 else f"🟢 Осталось дней: {days_left}"
        
        message += (
            f"🔹 {name}\n"
            f"   📅 Установлен: {install_date}\n"
            f"   ⏰ Замена: {replace_date}\n"
            f"   {status}\n\n"
        )
    
    await update.message.reply_text(message)

# Добавление фильтра
async def add_filter(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Введите данные фильтра в формате:\n"
        "Название фильтра, дата установки (ГГГГ-ММ-ДД), срок службы (в днях)\n\n"
        "Пример:\n"
        "Фильтр для воды, 2024-01-15, 180"
    )
    context.user_data['awaiting_filter_data'] = True

# Обработка ввода данных фильтра
async def handle_filter_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.user_data.get('awaiting_filter_data'):
        return
    
    try:
        user_input = update.message.text
        parts = [part.strip() for part in user_input.split(',')]
        
        if len(parts) != 3:
            raise ValueError("Неверный формат данных")
        
        filter_name, install_date_str, lifespan_str = parts
        
        # Проверка даты установки
        install_date = datetime.strptime(install_date_str, '%Y-%m-%d')
        lifespan = int(lifespan_str)
        
        # Расчет даты замены
        replacement_date = install_date + timedelta(days=lifespan)
        replacement_date_str = replacement_date.strftime('%Y-%m-%d')
        
        # Сохранение в базу данных
        user_id = update.message.from_user.id
        conn = sqlite3.connect(DATABASE_NAME)
        cursor = conn.cursor()
        
        cursor.execute('''
            INSERT INTO filters (user_id, filter_name, installation_date, replacement_date)
            VALUES (?, ?, ?, ?)
        ''', (user_id, filter_name, install_date_str, replacement_date_str))
        
        filter_id = cursor.lastrowid
        conn.commit()
        conn.close()
        
        # Обновление Excel файла
        update_excel_file()
        
        await update.message.reply_text(
            f"✅ Фильтр '{filter_name}' успешно добавлен!\n"
            f"📅 Дата замены: {replacement_date_str}\n"
            f"⏰ Уведомление придет за 2 дня до замены."
        )
        
        context.user_data['awaiting_filter_data'] = False
        
    except ValueError as e:
        await update.message.reply_text(
            "❌ Ошибка в формате данных. Пожалуйста, введите данные в правильном формате:\n"
            "Название, ГГГГ-ММ-ДД, количество_дней\n\n"
            "Пример: Фильтр для воды, 2024-01-15, 180"
        )
    except Exception as e:
        await update.message.reply_text("❌ Произошла ошибка при добавлении фильтра.")

# Удаление фильтра
async def delete_filter(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    conn = sqlite3.connect(DATABASE_NAME)
    cursor = conn.cursor()
    
    cursor.execute('''
        SELECT id, filter_name FROM filters WHERE user_id = ?
    ''', (user_id,))
    
    filters = cursor.fetchall()
    conn.close()
    
    if not filters:
        await update.message.reply_text("У вас нет фильтров для удаления.")
        return
    
    keyboard = []
    for filter_item in filters:
        filter_id, name = filter_item
        keyboard.append([InlineKeyboardButton(name, callback_data=f"select_delete_{filter_id}")])
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text("Выберите фильтр для удаления:", reply_markup=reply_markup)

# Обработка callback запросов
async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    data = query.data
    
    if data.startswith('select_delete_'):
        filter_id = data.split('_')[2]
        conn = sqlite3.connect(DATABASE_NAME)
        cursor = conn.cursor()
        cursor.execute('SELECT filter_name FROM filters WHERE id = ?', (filter_id,))
        filter_name = cursor.fetchone()[0]
        conn.close()
        
        await query.edit_message_text(
            f"Вы уверены, что хотите удалить фильтр '{filter_name}'?",
            reply_markup=confirmation_keyboard(filter_id)
        )
    
    elif data.startswith('confirm_delete_'):
        filter_id = data.split('_')[2]
        conn = sqlite3.connect(DATABASE_NAME)
        cursor = conn.cursor()
        cursor.execute('DELETE FROM filters WHERE id = ?', (filter_id,))
        conn.commit()
        conn.close()
        
        update_excel_file()
        
        await query.edit_message_text("✅ Фильтр успешно удален!")
    
    elif data == 'cancel_delete':
        await query.edit_message_text("❌ Удаление отменено.")

# Обновление Excel файла
def update_excel_file():
    conn = sqlite3.connect(DATABASE_NAME)
    df = pd.read_sql_query('''
        SELECT id, user_id, filter_name, installation_date, replacement_date 
        FROM filters
    ''', conn)
    conn.close()
    
    # Добавляем статус
    df['Статус'] = df['replacement_date'].apply(
        lambda x: 'Просрочен' if datetime.strptime(x, '%Y-%m-%d') < datetime.now() 
        else f"Осталось дней: {(datetime.strptime(x, '%Y-%m-%d') - datetime.now()).days}"
    )
    
    with pd.ExcelWriter(EXCEL_FILE, engine='openpyxl') as writer:
        df.to_excel(writer, sheet_name='Фильтры', index=False)
        
        # Форматирование
        workbook = writer.book
        worksheet = writer.sheets['Фильтры']
        
        # Авто-ширина колонок
        for column in worksheet.columns:
            max_length = 0
            column_letter = column[0].column_letter
            for cell in column:
                try:
                    if len(str(cell.value)) > max_length:
                        max_length = len(str(cell.value))
                except:
                    pass
            adjusted_width = (max_length + 2)
            worksheet.column_dimensions[column_letter].width = adjusted_width

# Экспорт в Excel
async def export_to_excel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        update_excel_file()
        
        with open(EXCEL_FILE, 'rb') as file:
            await update.message.reply_document(
                document=file,
                filename='filters_data.xlsx',
                caption='📊 Данные о фильтрах'
            )
    except Exception as e:
        await update.message.reply_text("❌ Ошибка при экспорте в Excel.")

# Синхронизация с календарем
async def sync_calendar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    conn = sqlite3.connect(DATABASE_NAME)
    cursor = conn.cursor()
    
    cursor.execute('''
        SELECT filter_name, replacement_date FROM filters WHERE user_id = ?
    ''', (user_id,))
    
    filters = cursor.fetchall()
    conn.close()
    
    if not filters:
        await update.message.reply_text("Нет фильтров для синхронизации с календарем.")
        return
    
    # Генерация файла для импорта в календарь
    ical_content = "BEGIN:VCALENDAR\nVERSION:2.0\nPRODID:-//Filter Bot//EN\n"
    
    for filter_name, replacement_date in filters:
        event_date = datetime.strptime(replacement_date, '%Y-%m-%d')
        notification_date = event_date - timedelta(days=2)
        
        ical_content += f"""BEGIN:VEVENT
SUMMARY:Замена фильтра {filter_name}
DTSTART;VALUE=DATE:{event_date.strftime('%Y%m%d')}
DTEND;VALUE=DATE:{(event_date + timedelta(days=1)).strftime('%Y%m%d')}
DESCRIPTION:Не забудьте заменить фильтр {filter_name}
BEGIN:VALARM
ACTION:DISPLAY
DESCRIPTION:Напоминание
TRIGGER;RELATED=START:-P2D
END:VALARM
END:VEVENT
"""
    
    ical_content += "END:VCALENDAR"
    
    # Сохранение временного файла
    with open('filters_calendar.ics', 'w', encoding='utf-8') as f:
        f.write(ical_content)
    
    with open('filters_calendar.ics', 'rb') as f:
        await update.message.reply_document(
            document=f,
            filename='filters_calendar.ics',
            caption='📅 Календарь для импорта. Файл содержит напоминания за 2 дня до замены фильтров.'
        )

# Проверка уведомлений
async def check_notifications(context: ContextTypes.DEFAULT_TYPE):
    conn = sqlite3.connect(DATABASE_NAME)
    cursor = conn.cursor()
    
    # Находим фильтры, у которых до замены осталось 2 дня и уведомление еще не отправлялось
    two_days_later = (datetime.now() + timedelta(days=2)).strftime('%Y-%m-%d')
    
    cursor.execute('''
        SELECT user_id, filter_name, replacement_date 
        FROM filters 
        WHERE replacement_date = ? AND notification_sent = 0
    ''', (two_days_later,))
    
    filters_to_notify = cursor.fetchall()
    
    for user_id, filter_name, replacement_date in filters_to_notify:
        try:
            await context.bot.send_message(
                chat_id=user_id,
                text=f"🔔 Напоминание!\n\n"
                     f"Фильтр '{filter_name}' требует замены через 2 дня ({replacement_date})!"
            )
            
            # Помечаем уведомление как отправленное
            cursor.execute('''
                UPDATE filters SET notification_sent = 1 
                WHERE user_id = ? AND filter_name = ? AND replacement_date = ?
            ''', (user_id, filter_name, replacement_date))
            
        except Exception as e:
            logger.error(f"Ошибка отправки уведомления пользователю {user_id}: {e}")
    
    conn.commit()
    conn.close()

# Обработка текстовых сообщений
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    
    if text == '📋 Список фильтров':
        await show_filters(update, context)
    elif text == '➕ Добавить фильтр':
        await add_filter(update, context)
    elif text == '🗑️ Удалить фильтр':
        await delete_filter(update, context)
    elif text == '📊 Экспорт в Excel':
        await export_to_excel(update, context)
    elif text == '📅 Синхронизация с календарем':
        await sync_calendar(update, context)
    else:
        await handle_filter_input(update, context)

# Основная функция
def main():
    # Инициализация
    init_db()
    init_excel()
    
    # Создание приложения
    application = Application.builder().token("").build()
    
    # Обработчики команд
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CallbackQueryHandler(button_handler))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    
    # Планировщик для уведомлений (проверка каждые 6 часов)
    job_queue = application.job_queue
    job_queue.run_repeating(check_notifications, interval=21600, first=10)  # 6 часов = 21600 секунд
    
    # Запуск бота
    application.run_polling()
    print("Бот запущен...")

if __name__ == '__main__':
    main()
