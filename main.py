import logging
import sqlite3
from datetime import datetime, timedelta
from telegram import Update, ReplyKeyboardMarkup, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import (
    Application, CommandHandler, MessageHandler, filters, 
    ContextTypes, ConversationHandler, CallbackQueryHandler, JobQueue
)
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from io import BytesIO
import json
pip install python-telegram-bot matplotlib pillow
# Настройка логирования
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

TOKEN = "8278600298:AAFA-R0ql-dibAoBruxgwitHTx_LLx61OdM"

# Этапы разговора
SELECTING_ACTION, CHOOSING_FILTER_TYPE, ADDING_FILTER, UPLOADING_PHOTO = range(4)

# Роли пользователей
ROLES = {
    'user': 'Пользователь',
    'admin': 'Администратор',
    'technician': 'Техник'
}

# Подключение к базе данных
conn = sqlite3.connect('filters_advanced.db', check_same_thread=False)
cursor = conn.cursor()

# Создание таблиц
cursor.executescript('''
    CREATE TABLE IF NOT EXISTS users (
        user_id INTEGER PRIMARY KEY,
        username TEXT,
        first_name TEXT,
        role TEXT DEFAULT 'user',
        created_date TEXT
    );
    
    CREATE TABLE IF NOT EXISTS filter_types (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT UNIQUE,
        lifespan_days INTEGER,
        description TEXT,
        created_by INTEGER
    );
    
    CREATE TABLE IF NOT EXISTS filter_changes (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        filter_type_id INTEGER,
        change_date TEXT,
        next_change_date TEXT,
        photo_file_id TEXT,
        notes TEXT,
        FOREIGN KEY (filter_type_id) REFERENCES filter_types (id)
    );
    
    CREATE TABLE IF NOT EXISTS notifications (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        filter_type_id INTEGER,
        notification_date TEXT,
        sent BOOLEAN DEFAULT FALSE
    );
''')

# Добавляем стандартные типы фильтров
default_filter_types = [
    ('Механический фильтр', 30, 'Предварительная очистка от песка и ржавчины'),
    ('Угольный фильтр', 180, 'Удаление хлора и органических соединений'),
    ('Обратный осмос', 365, 'Мембрана обратного осмоса'),
    ('Пост-фильтр', 180, 'Финальная очистка воды')
]

for filter_type in default_filter_types:
    try:
        cursor.execute(
            "INSERT OR IGNORE INTO filter_types (name, lifespan_days, description) VALUES (?, ?, ?)",
            filter_type
        )
    except sqlite3.IntegrityError:
        pass

conn.commit()

class FilterBot:
    def __init__(self):
        self.application = None
    
    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user = update.effective_user
        self._add_user(user)
        
        keyboard = [
            ['➕ Добавить замену', '📊 Статистика'],
            ['🔔 Уведомления', '⚙️ Настройки']
        ]
        
        if self._is_admin(user.id):
            keyboard.append(['👑 Админ панель'])
        
        markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
        
        await update.message.reply_text(
            f"👋 Добро пожаловать, {user.first_name}!\n"
            "Я помогу вам отслеживать замену фильтров.\n\n"
            "Выберите действие:",
            reply_markup=markup
        )
        return SELECTING_ACTION
    
    def _add_user(self, user):
        cursor.execute(
            """INSERT OR REPLACE INTO users 
            (user_id, username, first_name, created_date) 
            VALUES (?, ?, ?, ?)""",
            (user.id, user.username, user.first_name, datetime.now().isoformat())
        )
        conn.commit()
    
    def _is_admin(self, user_id):
        cursor.execute("SELECT role FROM users WHERE user_id = ?", (user_id,))
        result = cursor.fetchone()
        return result and result[0] == 'admin'
    
    async def admin_panel(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self._is_admin(update.effective_user.id):
            await update.message.reply_text("❌ Доступ запрещен")
            return SELECTING_ACTION
        
        keyboard = [
            [InlineKeyboardButton("📊 Общая статистика", callback_data="admin_stats")],
            [InlineKeyboardButton("🔧 Управление типами фильтров", callback_data="manage_types")],
            [InlineKeyboardButton("👥 Управление пользователями", callback_data="manage_users")],
            [InlineKeyboardButton("⏰ Настройка уведомлений", callback_data="notification_settings")]
        ]
        markup = InlineKeyboardMarkup(keyboard)
        
        await update.message.reply_text(
            "👑 Панель администратора:",
            reply_markup=markup
        )
        return SELECTING_ACTION
    
    async def add_filter_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        cursor.execute("SELECT id, name, lifespan_days FROM filter_types")
        filter_types = cursor.fetchall()
        
        keyboard = []
        for filter_id, name, lifespan in filter_types:
            keyboard.append([
                InlineKeyboardButton(
                    f"{name} ({lifespan} дней)", 
                    callback_data=f"select_filter_{filter_id}"
                )
            ])
        
        markup = InlineKeyboardMarkup(keyboard)
        
        await update.message.reply_text(
            "🔧 Выберите тип фильтра для замены:",
            reply_markup=markup
        )
        return CHOOSING_FILTER_TYPE
    
    async def filter_type_selected(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        await query.answer()
        
        filter_type_id = int(query.data.split('_')[-1])
        context.user_data['current_filter_type'] = filter_type_id
        
        cursor.execute(
            "SELECT name, lifespan_days FROM filter_types WHERE id = ?", 
            (filter_type_id,)
        )
        filter_name, lifespan = cursor.fetchone()
        
        context.user_data['current_lifespan'] = lifespan
        
        await query.edit_message_text(
            f"📝 Замена фильтра: {filter_name}\n"
            f"⏱ Срок службы: {lifespan} дней\n\n"
            "Пришлите фото замены (опционально) или напишите 'пропустить':"
        )
        return UPLOADING_PHOTO
    
    async def handle_photo(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        photo_file = await update.message.photo[-1].get_file()
        context.user_data['photo_file_id'] = photo_file.file_id
        
        await update.message.reply_text(
            "📸 Фото сохранено. Добавьте комментарий или напишите 'пропустить':"
        )
        return ADDING_FILTER
    
    async def skip_photo(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        context.user_data['photo_file_id'] = None
        await update.message.reply_text("Добавьте комментарий или напишите 'пропустить':")
        return ADDING_FILTER
    
    async def save_filter_change(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        notes = update.message.text if update.message.text != "пропустить" else ""
        filter_type_id = context.user_data['current_filter_type']
        lifespan = context.user_data['current_lifespan']
        photo_file_id = context.user_data.get('photo_file_id')
        
        change_date = datetime.now()
        next_change_date = change_date + timedelta(days=lifespan)
        
        cursor.execute(
            """INSERT INTO filter_changes 
            (user_id, filter_type_id, change_date, next_change_date, photo_file_id, notes) 
            VALUES (?, ?, ?, ?, ?, ?)""",
            (user_id, filter_type_id, change_date.isoformat(), 
             next_change_date.isoformat(), photo_file_id, notes)
        )
        
        # Создаем уведомление о следующей замене
        notification_date = next_change_date - timedelta(days=7)  # Уведомление за 7 дней
        cursor.execute(
            "INSERT INTO notifications (user_id, filter_type_id, notification_date) VALUES (?, ?, ?)",
            (user_id, filter_type_id, notification_date.isoformat())
        )
        
        conn.commit()
        
        cursor.execute("SELECT name FROM filter_types WHERE id = ?", (filter_type_id,))
        filter_name = cursor.fetchone()[0]
        
        response = (
            f"✅ Замена фильтра '{filter_name}' записана!\n"
            f"📅 Следующая замена: {next_change_date.strftime('%d.%m.%Y')}\n"
            f"🔔 Вы получите уведомление за 7 дней до замены"
        )
        
        if photo_file_id:
            await update.message.reply_photo(
                photo=photo_file_id,
                caption=response
            )
        else:
            await update.message.reply_text(response)
        
        return await self.start(update, context)
    
    async def show_statistics(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        
        # Статистика по пользователю
        cursor.execute('''
            SELECT ft.name, COUNT(fc.id), MAX(fc.change_date)
            FROM filter_changes fc
            JOIN filter_types ft ON fc.filter_type_id = ft.id
            WHERE fc.user_id = ?
            GROUP BY ft.name
        ''', (user_id,))
        
        stats = cursor.fetchall()
        
        if not stats:
            await update.message.reply_text("📊 У вас пока нет записей о заменах фильтров.")
            return SELECTING_ACTION
        
        # Создаем график
        fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(12, 5))
        
        # График 1: Количество замен по типам
        names = [stat[0] for stat in stats]
        counts = [stat[1] for stat in stats]
        
        ax1.bar(names, counts, color=['#ff9999', '#66b3ff', '#99ff99', '#ffcc99'])
        ax1.set_title('Количество замен по типам фильтров')
        ax1.tick_params(axis='x', rotation=45)
        
        # График 2: Временная шкала замен
        cursor.execute('''
            SELECT ft.name, fc.change_date 
            FROM filter_changes fc
            JOIN filter_types ft ON fc.filter_type_id = ft.id
            WHERE fc.user_id = ?
            ORDER BY fc.change_date
        ''', (user_id,))
        
        changes = cursor.fetchall()
        
        dates = [datetime.fromisoformat(change[1]) for change in changes]
        types = [change[0] for change in changes]
        
        colors = {'Механический фильтр': 'red', 'Угольный фильтр': 'blue', 
                 'Обратный осмос': 'green', 'Пост-фильтр': 'orange'}
        
        for i, (date, filter_type) in enumerate(zip(dates, types)):
            ax2.scatter(date, i % 5, color=colors.get(filter_type, 'gray'), label=filter_type if i == 0 else "")
        
        ax2.xaxis.set_major_formatter(mdates.DateFormatter('%Y-%m'))
        ax2.xaxis.set_major_locator(mdates.MonthLocator())
        ax2.set_title('История замен')
        ax2.tick_params(axis='x', rotation=45)
        
        plt.tight_layout()
        
        # Сохраняем график в буфер
        buffer = BytesIO()
        plt.savefig(buffer, format='png', dpi=150)
        buffer.seek(0)
        plt.close()
        
        # Формируем текстовую статистику
        stats_text = "📊 Ваша статистика:\n\n"
        for name, count, last_change in stats:
            last_date = datetime.fromisoformat(last_change).strftime('%d.%m.%Y')
            stats_text += f"• {name}: {count} замен\n  Последняя: {last_date}\n"
        
        await update.message.reply_photo(
            photo=buffer,
            caption=stats_text
        )
        
        return SELECTING_ACTION
    
    async def notification_settings(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        
        cursor.execute('''
            SELECT ft.name, fc.next_change_date 
            FROM filter_changes fc
            JOIN filter_types ft ON fc.filter_type_id = ft.id
            WHERE fc.user_id = ? AND fc.next_change_date > ?
            ORDER BY fc.next_change_date
        ''', (user_id, datetime.now().isoformat()))
        
        upcoming = cursor.fetchall()
        
        if not upcoming:
            await update.message.reply_text("✅ Все фильтры заменены вовремя!")
            return SELECTING_ACTION
        
        notification_text = "🔔 Предстоящие замены:\n\n"
        for filter_name, next_change in upcoming:
            next_date = datetime.fromisoformat(next_change)
            days_left = (next_date - datetime.now()).days
            notification_text += f"• {filter_name}: через {days_left} дней ({next_date.strftime('%d.%m.%Y')})\n"
        
        keyboard = [[InlineKeyboardButton("🔕 Отключить уведомления", callback_data="disable_notifications")]]
        markup = InlineKeyboardMarkup(keyboard)
        
        await update.message.reply_text(notification_text, reply_markup=markup)
        return SELECTING_ACTION
    
    async def check_notifications(self, context: ContextTypes.DEFAULT_TYPE):
        """Проверка уведомлений - запускается по расписанию"""
        now = datetime.now()
        
        cursor.execute('''
            SELECT n.id, u.user_id, ft.name, fc.next_change_date
            FROM notifications n
            JOIN users u ON n.user_id = u.user_id
            JOIN filter_types ft ON n.filter_type_id = ft.id
            JOIN filter_changes fc ON n.filter_type_id = fc.filter_type_id AND n.user_id = fc.user_id
            WHERE n.notification_date <= ? AND n.sent = FALSE
        ''', (now.isoformat(),))
        
        notifications = cursor.fetchall()
        
        for notification_id, user_id, filter_name, next_change in notifications:
            next_date = datetime.fromisoformat(next_change)
            days_left = (next_date - now).days
            
            message = (
                f"🔔 Напоминание о замене фильтра!\n"
                f"Фильтр: {filter_name}\n"
                f"Срок замены: {next_date.strftime('%d.%m.%Y')}\n"
                f"Осталось дней: {days_left}"
            )
            
            try:
                await context.bot.send_message(chat_id=user_id, text=message)
                
                # Помечаем уведомление как отправленное
                cursor.execute(
                    "UPDATE notifications SET sent = TRUE WHERE id = ?",
                    (notification_id,)
                )
                conn.commit()
                
            except Exception as e:
                logging.error(f"Не удалось отправить уведомление пользователю {user_id}: {e}")
    
    async def handle_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        await query.answer()
        
        if query.data == "admin_stats":
            await self.show_admin_stats(query)
        elif query.data == "disable_notifications":
            await self.disable_notifications(query)
        
        return SELECTING_ACTION
    
    async def show_admin_stats(self, query):
        cursor.execute("SELECT COUNT(*) FROM users")
        user_count = cursor.fetchone()[0]
        
        cursor.execute("SELECT COUNT(*) FROM filter_changes")
        change_count = cursor.fetchone()[0]
        
        cursor.execute('''
            SELECT ft.name, COUNT(fc.id) 
            FROM filter_changes fc 
            JOIN filter_types ft ON fc.filter_type_id = ft.id 
            GROUP BY ft.name
        ''')
        changes_by_type = cursor.fetchall()
        
        stats_text = (
            "👑 Статистика системы:\n\n"
            f"👥 Пользователей: {user_count}\n"
            f"🔧 Всего замен: {change_count}\n\n"
            "Замены по типам:\n"
        )
        
        for filter_name, count in changes_by_type:
            stats_text += f"• {filter_name}: {count}\n"
        
        await query.edit_message_text(stats_text)
    
    async def disable_notifications(self, query):
        user_id = query.from_user.id
        cursor.execute("DELETE FROM notifications WHERE user_id = ?", (user_id,))
        conn.commit()
        await query.edit_message_text("🔕 Уведомления отключены для всех фильтров")
    
    def setup_jobs(self):
        """Настройка периодических задач"""
        job_queue = self.application.job_queue
        
        # Проверка уведомлений каждый день в 10:00
        job_queue.run_daily(
            self.check_notifications,
            time=datetime.strptime("10:00", "%H:%M").time(),
            name="daily_notifications"
        )
    
    def run(self):
        """Запуск бота"""
        self.application = Application.builder().token(TOKEN).build()
        
        # Настройка обработчиков
        conv_handler = ConversationHandler(
            entry_points=[MessageHandler(filters.Regex('^➕ Добавить замену$'), self.add_filter_start)],
            states={
                CHOOSING_FILTER_TYPE: [
                    CallbackQueryHandler(self.filter_type_selected, pattern='^select_filter_')
                ],
                UPLOADING_PHOTO: [
                    MessageHandler(filters.PHOTO, self.handle_photo),
                    MessageHandler(filters.TEXT & ~filters.COMMAND, self.skip_photo)
                ],
                ADDING_FILTER: [
                    MessageHandler(filters.TEXT & ~filters.COMMAND, self.save_filter_change)
                ]
            },
            fallbacks=[CommandHandler('cancel', self.start)],
            map_to_parent={SELECTING_ACTION: SELECTING_ACTION}
        )
        
        self.application.add_handler(CommandHandler("start", self.start))
        self.application.add_handler(conv_handler)
        self.application.add_handler(MessageHandler(filters.Regex('^📊 Статистика$'), self.show_statistics))
        self.application.add_handler(MessageHandler(filters.Regex('^🔔 Уведомления$'), self.notification_settings))
        self.application.add_handler(MessageHandler(filters.Regex('^👑 Админ панель$'), self.admin_panel))
        self.application.add_handler(CallbackQueryHandler(self.handle_callback))
        
        # Настройка периодических задач
        self.setup_jobs()
        
        # Запуск бота
        self.application.run_polling()

if __name__ == '__main__':
    bot = FilterBot()
    bot.run()
