import os
import logging
import sqlite3
import pandas as pd
from datetime import datetime, timedelta
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes, MessageHandler, filters
import asyncio

# Настройка логирования
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Инициализация базы данных
def init_db():
    conn = sqlite3.connect('filters.db')
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS filters (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            filter_name TEXT NOT NULL,
            install_date TEXT NOT NULL,
            replacement_date TEXT NOT NULL,
            reminder_days INTEGER DEFAULT 3,
            is_active INTEGER DEFAULT 1
        )
    ''')
    conn.commit()
    conn.close()

# Сохранение в Excel
def save_to_excel(user_id):
    conn = sqlite3.connect('filters.db')
    df = pd.read_sql_query('SELECT * FROM filters WHERE user_id = ?', conn, params=(user_id,))
    
    if not df.empty:
        filename = f'filters_{user_id}_{datetime.now().strftime("%Y%m%d_%H%M%S")}.xlsx'
        df.to_excel(filename, index=False)
        conn.close()
        return filename
    conn.close()
    return None

# Клавиатура главного меню
def main_menu_keyboard():
    keyboard = [
        [InlineKeyboardButton("📋 Список фильтров", callback_data='list_filters')],
        [InlineKeyboardButton("➕ Добавить фильтр", callback_data='add_filter')],
        [InlineKeyboardButton("📊 Сохранить в Excel", callback_data='export_excel')],
        [InlineKeyboardButton("⚙️ Настройки напоминаний", callback_data='reminder_settings')]
    ]
    return InlineKeyboardMarkup(keyboard)

# Клавиатура для действий с фильтром
def filter_actions_keyboard(filter_id):
    keyboard = [
        [
            InlineKeyboardButton("✏️ Редактировать", callback_data=f'edit_{filter_id}'),
            InlineKeyboardButton("🗑️ Удалить", callback_data=f'delete_{filter_id}')
        ],
        [InlineKeyboardButton("📅 Изменить дату", callback_data=f'change_date_{filter_id}')],
        [InlineKeyboardButton("🔙 Назад", callback_data='main_menu')]
    ]
    return InlineKeyboardMarkup(keyboard)

# Команда /start
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    welcome_text = """
💧 **Бот для замены фильтров воды**

Управляйте своевременной заменой фильтров для воды:
• Добавляйте фильтры с датой установки
• Получайте напоминания за 3 дня до замены
• Редактируйте и удаляйте фильтры
• Экспортируйте данные в Excel

Выберите действие:
    """
    await update.message.reply_text(welcome_text, reply_markup=main_menu_keyboard(), parse_mode='Markdown')

# Обработчик главного меню
async def main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text(
        "💧 **Главное меню**\nВыберите действие:",
        reply_markup=main_menu_keyboard(),
        parse_mode='Markdown'
    )

# Показать список фильтров
async def list_filters(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    user_id = query.from_user.id
    conn = sqlite3.connect('filters.db')
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM filters WHERE user_id = ? AND is_active = 1', (user_id,))
    filters = cursor.fetchall()
    conn.close()
    
    if not filters:
        await query.edit_message_text(
            "📭 У вас нет активных фильтров",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("➕ Добавить фильтр", callback_data='add_filter')]])
        )
        return
    
    text = "📋 **Ваши фильтры:**\n\n"
    keyboard = []
    
    for filter in filters:
        filter_id, _, name, install_date, replacement_date, reminder_days, _ = filter
        repl_date = datetime.strptime(replacement_date, '%Y-%m-%d')
        days_left = (repl_date - datetime.now()).days
        
        status = "🔴" if days_left < 0 else "🟡" if days_left <= reminder_days else "🟢"
        text += f"{status} **{name}**\n"
        text += f"Установлен: {install_date}\n"
        text += f"Замена: {replacement_date} ({days_left} дней)\n\n"
        
        keyboard.append([InlineKeyboardButton(f"⚙️ {name}", callback_data=f'filter_{filter_id}')])
    
    keyboard.append([InlineKeyboardButton("🔙 Назад", callback_data='main_menu')])
    
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')

# Обработчик действий с конкретным фильтром
async def filter_detail(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    filter_id = query.data.split('_')[1]
    conn = sqlite3.connect('filters.db')
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM filters WHERE id = ?', (filter_id,))
    filter_data = cursor.fetchone()
    conn.close()
    
    if filter_data:
        _, _, name, install_date, replacement_date, reminder_days, _ = filter_data
        repl_date = datetime.strptime(replacement_date, '%Y-%m-%d')
        days_left = (repl_date - datetime.now()).days
        
        text = f"**{name}**\n\n"
        text += f"📅 Дата установки: {install_date}\n"
        text += f"🔄 Дата замены: {replacement_date}\n"
        text += f"⏰ Дней до замены: {days_left}\n"
        text += f"🔔 Напоминание за: {reminder_days} дней"
        
        await query.edit_message_text(text, 
                                    reply_markup=filter_actions_keyboard(filter_id),
                                    parse_mode='Markdown')

# Процесс добавления фильтра
async def add_filter_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    context.user_data['adding_filter'] = True
    context.user_data['filter_stage'] = 'name'
    
    await query.edit_message_text(
        "📝 Введите название фильтра:",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Отмена", callback_data='main_menu')]])
    )

# Обработчик текстовых сообщений
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_data = context.user_data
    user_id = update.effective_user.id
    
    if user_data.get('adding_filter'):
        if user_data.get('filter_stage') == 'name':
            user_data['filter_name'] = update.message.text
            user_data['filter_stage'] = 'install_date'
            await update.message.reply_text(
                "📅 Введите дату установки (формат: ГГГГ-ММ-ДД):",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Отмена", callback_data='main_menu')]])
            )
        
        elif user_data.get('filter_stage') == 'install_date':
            try:
                install_date = datetime.strptime(update.message.text, '%Y-%m-%d')
                user_data['install_date'] = update.message.text
                user_data['filter_stage'] = 'lifespan'
                await update.message.reply_text(
                    "⏰ Введите срок службы фильтра в днях:",
                    reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Отмена", callback_data='main_menu')]])
                )
            except ValueError:
                await update.message.reply_text("❌ Неверный формат даты. Используйте ГГГГ-ММ-ДД:")
        
        elif user_data.get('filter_stage') == 'lifespan':
            try:
                lifespan = int(update.message.text)
                install_date = datetime.strptime(user_data['install_date'], '%Y-%m-%d')
                replacement_date = (install_date + timedelta(days=lifespan)).strftime('%Y-%m-%d')
                
                # Сохранение в базу данных
                conn = sqlite3.connect('filters.db')
                cursor = conn.cursor()
                cursor.execute('''
                    INSERT INTO filters (user_id, filter_name, install_date, replacement_date)
                    VALUES (?, ?, ?, ?)
                ''', (user_id, user_data['filter_name'], user_data['install_date'], replacement_date))
                conn.commit()
                conn.close()
                
                # Очистка временных данных
                user_data.clear()
                
                await update.message.reply_text(
                    f"✅ Фильтр '{user_data['filter_name']}' успешно добавлен!\n"
                    f"Дата замены: {replacement_date}",
                    reply_markup=main_menu_keyboard()
                )
            except ValueError:
                await update.message.reply_text("❌ Введите корректное число дней:")

# Удаление фильтра
async def delete_filter(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    filter_id = query.data.split('_')[1]
    
    conn = sqlite3.connect('filters.db')
    cursor = conn.cursor()
    cursor.execute('DELETE FROM filters WHERE id = ?', (filter_id,))
    conn.commit()
    conn.close()
    
    await query.edit_message_text(
        "✅ Фильтр успешно удален!",
        reply_markup=main_menu_keyboard()
    )

# Изменение даты
async def change_date(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    filter_id = query.data.split('_')[2]
    context.user_data['editing_filter'] = filter_id
    context.user_data['editing_stage'] = 'new_date'
    
    await query.edit_message_text(
        "📅 Введите новую дату замены (формат: ГГГГ-ММ-ДД):",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Отмена", callback_data='main_menu')]])
    )

# Экспорт в Excel
async def export_to_excel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    user_id = query.from_user.id
    filename = save_to_excel(user_id)
    
    if filename:
        await context.bot.send_document(
            chat_id=query.message.chat_id,
            document=open(filename, 'rb'),
            caption="📊 Ваши данные экспортированы в Excel"
        )
        os.remove(filename)  # Удаляем временный файл
    else:
        await query.edit_message_text(
            "❌ Нет данных для экспорта",
            reply_markup=main_menu_keyboard()
        )

# Проверка напоминаний
async def check_reminders(context: ContextTypes.DEFAULT_TYPE):
    conn = sqlite3.connect('filters.db')
    cursor = conn.cursor()
    cursor.execute('''
        SELECT f.user_id, f.filter_name, f.replacement_date, f.reminder_days 
        FROM filters f 
        WHERE f.is_active = 1
    ''')
    filters = cursor.fetchall()
    conn.close()
    
    for filter in filters:
        user_id, name, replacement_date, reminder_days = filter
        repl_date = datetime.strptime(replacement_date, '%Y-%m-%d')
        days_until_replacement = (repl_date - datetime.now()).days
        
        if 0 <= days_until_replacement <= reminder_days:
            message = f"🔔 **Напоминание о замене фильтра**\n\n"
            message += f"Фильтр: {name}\n"
            message += f"Дата замены: {replacement_date}\n"
            message += f"Осталось дней: {days_until_replacement}\n\n"
            message += "Не забудьте вовремя заменить фильтр!"
            
            try:
                await context.bot.send_message(chat_id=user_id, text=message, parse_mode='Markdown')
            except Exception as e:
                logger.error(f"Не удалось отправить напоминание пользователю {user_id}: {e}")

# Основная функция
def main():
    # Инициализация базы данных
    init_db()
    
    # Создание приложения
    application = Application.builder().token("8278600298:AAFA-R0ql-dibAoBruxgwitHTx_LLx61OdM").build()
    
    # Обработчики команд
    application.add_handler(CommandHandler("start", start))
    
    # Обработчики callback-запросов
    application.add_handler(CallbackQueryHandler(main_menu, pattern='^main_menu$'))
    application.add_handler(CallbackQueryHandler(list_filters, pattern='^list_filters$'))
    application.add_handler(CallbackQueryHandler(add_filter_start, pattern='^add_filter$'))
    application.add_handler(CallbackQueryHandler(export_to_excel, pattern='^export_excel$'))
    application.add_handler(CallbackQueryHandler(filter_detail, pattern='^filter_'))
    application.add_handler(CallbackQueryHandler(delete_filter, pattern='^delete_'))
    application.add_handler(CallbackQueryHandler(change_date, pattern='^change_date_'))
    
    # Обработчик текстовых сообщений
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    
    # Настройка напоминаний
    job_queue = application.job_queue
    job_queue.run_repeating(check_reminders, interval=86400, first=10)  # Проверка каждые 24 часа
    
    # Запуск бота
    application.run_polling()

if __name__ == '__main__':
    main()
