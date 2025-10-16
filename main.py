import logging
import sqlite3
import schedule
import time
import asyncio
from datetime import datetime, timedelta
from threading import Thread
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes, CallbackQueryHandler
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, InputFile
import os
from typing import Dict, List

# Настройка логирования
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO,
    handlers=[
        logging.FileHandler('bot.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# Конфигурация
BOT_TOKEN = "8278600298:AAGPjUhyU5HxXOaLRvu-FSRldBW_UCmwOME"
CHANNEL_ID = "@timur_onion"  # Замените на username вашего канала
ADMIN_IDS = [5024165375]  # Замените на ID администраторов

# База данных
class Database:
    def __init__(self):
        self.conn = sqlite3.connect('channel_bot.db', check_same_thread=False)
        self.create_tables()
    
    def create_tables(self):
        """Создание таблиц в базе данных"""
        cursor = self.conn.cursor()
        
        # Таблица запланированных постов
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS scheduled_posts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id INTEGER,
                message_text TEXT,
                media_path TEXT,
                scheduled_time DATETIME,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        # Таблица статистики
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS statistics (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                date DATE,
                posts_sent INTEGER DEFAULT 0,
                commands_used INTEGER DEFAULT 0
            )
        ''')
        
        self.conn.commit()

    def add_scheduled_post(self, chat_id: int, message_text: str, scheduled_time: datetime, media_path: str = None):
        """Добавление запланированного поста"""
        cursor = self.conn.cursor()
        cursor.execute('''
            INSERT INTO scheduled_posts (chat_id, message_text, media_path, scheduled_time)
            VALUES (?, ?, ?, ?)
        ''', (chat_id, message_text, media_path, scheduled_time))
        self.conn.commit()
        return cursor.lastrowid

    def get_pending_posts(self):
        """Получение постов, готовых к отправке"""
        cursor = self.conn.cursor()
        cursor.execute('''
            SELECT * FROM scheduled_posts 
            WHERE scheduled_time <= datetime('now') 
            ORDER BY scheduled_time ASC
        ''')
        return cursor.fetchall()

    def delete_scheduled_post(self, post_id: int):
        """Удаление запланированного поста"""
        cursor = self.conn.cursor()
        cursor.execute('DELETE FROM scheduled_posts WHERE id = ?', (post_id,))
        self.conn.commit()

    def get_all_scheduled_posts(self):
        """Получение всех запланированных постов"""
        cursor = self.conn.cursor()
        cursor.execute('SELECT * FROM scheduled_posts ORDER BY scheduled_time ASC')
        return cursor.fetchall()

# Основной класс бота
class ChannelBot:
    def __init__(self, token: str):
        self.application = Application.builder().token(token).build()
        self.db = Database()
        self.setup_handlers()
        self.scheduled_posts: Dict[int, asyncio.Task] = {}

    def setup_handlers(self):
        """Настройка обработчиков"""
        # Команды
        self.application.add_handler(CommandHandler("start", self.start))
        self.application.add_handler(CommandHandler("help", self.help_command))
        self.application.add_handler(CommandHandler("post", self.post_to_channel))
        self.application.add_handler(CommandHandler("schedule", self.schedule_post))
        self.application.add_handler(CommandHandler("posts_list", self.list_scheduled_posts))
        self.application.add_handler(CommandHandler("stats", self.show_stats))
        self.application.add_handler(CommandHandler("broadcast", self.broadcast))
        
        # Обработчики кнопок
        self.application.add_handler(CallbackQueryHandler(self.button_handler))
        
        # Обработчики сообщений
        self.application.add_handler(
            MessageHandler(filters.TEXT & ~filters.COMMAND, self.handle_message)
        )
        self.application.add_handler(
            MessageHandler(filters.PHOTO, self.handle_photo)
        )
        self.application.add_handler(
            MessageHandler(filters.Document.ALL, self.handle_document)
        )

    # Основные команды
    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработчик команды /start"""
        user = update.effective_user
        
        keyboard = [
            [InlineKeyboardButton("📝 Создать пост", callback_data="create_post")],
            [InlineKeyboardButton("📅 Запланировать", callback_data="schedule_post")],
            [InlineKeyboardButton("📊 Статистика", callback_data="show_stats")],
            [InlineKeyboardButton("❓ Помощь", callback_data="help")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.message.reply_text(
            f"👋 Привет, {user.first_name}!\n\n"
            f"Я бот для управления каналом {CHANNEL_ID}\n"
            f"Выберите действие:",
            reply_markup=reply_markup
        )

    async def help_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработчик команды /help"""
        help_text = """
🤖 **Команды бота для канала:**

📝 **Основные команды:**
/start - Начать работу
/help - Показать справку
/post [текст] - Отправить сообщение в канал
/schedule - Запланировать пост

📊 **Управление:**
/posts_list - Список запланированных постов
/stats - Статистика бота
/broadcast - Рассылка (только для админов)

📁 **Поддержка форматов:**
- Текст с форматированием
- Фотографии
- Документы
- HTML/Markdown разметка

⚙️ **Использование:**
1. Отправьте текст с командой /post
2. Или используйте кнопки меню
3. Для планирования укажите время после /schedule
        """
        await update.message.reply_text(help_text, parse_mode="Markdown")

    # Функции работы с каналом
    async def post_to_channel(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Отправка сообщения в канал"""
        if not await self.is_admin(update):
            await update.message.reply_text("❌ Эта команда только для администраторов!")
            return

        if context.args:
            message_text = " ".join(context.args)
        else:
            # Если текст не указан, проверяем reply
            if update.message.reply_to_message:
                message_text = update.message.reply_to_message.text or update.message.reply_to_message.caption
            else:
                await update.message.reply_text(
                    "📝 Укажите текст сообщения после команды /post\n"
                    "Или ответьте на сообщение командой /post"
                )
                return

        try:
            # Отправка в канал
            await context.bot.send_message(
                chat_id=CHANNEL_ID,
                text=message_text,
                parse_mode="HTML"
            )
            
            await update.message.reply_text("✅ Сообщение успешно отправлено в канал!")
            logger.info(f"Post sent to channel by {update.effective_user.id}")
            
        except Exception as e:
            error_msg = f"❌ Ошибка при отправке: {e}"
            await update.message.reply_text(error_msg)
            logger.error(f"Error posting to channel: {e}")

    async def schedule_post(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Планирование поста"""
        if not await self.is_admin(update):
            await update.message.reply_text("❌ Эта команда только для администраторов!")
            return

        if not context.args:
            await update.message.reply_text(
                "⏰ Формат: /schedule \"текст сообщения\" HH:MM DD.MM.YYYY\n"
                "Пример: /schedule \"Привет мир!\" 14:30 25.12.2024"
            )
            return

        try:
            # Парсинг аргументов
            message_parts = " ".join(context.args).split('"')
            if len(message_parts) < 3:
                raise ValueError("Неверный формат сообщения")
            
            message_text = message_parts[1]
            time_date = message_parts[2].strip().split()
            
            if len(time_date) < 2:
                raise ValueError("Укажите время и дату")
            
            time_str = time_date[0]
            date_str = time_date[1] if len(time_date) > 1 else datetime.now().strftime("%d.%m.%Y")
            
            # Парсинг даты и времени
            scheduled_time = datetime.strptime(f"{date_str} {time_str}", "%d.%m.%Y %H:%M")
            
            if scheduled_time <= datetime.now():
                await update.message.reply_text("❌ Укажите время в будущем!")
                return
            
            # Сохранение в БД
            post_id = self.db.add_scheduled_post(
                update.effective_chat.id,
                message_text,
                scheduled_time
            )
            
            # Создание задачи
            self.create_scheduled_task(post_id, message_text, scheduled_time)
            
            await update.message.reply_text(
                f"✅ Пост запланирован на {scheduled_time.strftime('%d.%m.%Y %H:%M')}\n"
                f"ID поста: {post_id}"
            )
            
        except ValueError as e:
            await update.message.reply_text(f"❌ Ошибка формата: {e}")
        except Exception as e:
            await update.message.reply_text(f"❌ Ошибка: {e}")

    def create_scheduled_task(self, post_id: int, message_text: str, scheduled_time: datetime):
        """Создание асинхронной задачи для запланированного поста"""
        async def send_scheduled_post():
            try:
                delay = (scheduled_time - datetime.now()).total_seconds()
                if delay > 0:
                    await asyncio.sleep(delay)
                
                await self.application.bot.send_message(
                    chat_id=CHANNEL_ID,
                    text=message_text,
                    parse_mode="HTML"
                )
                
                # Удаляем из БД после отправки
                self.db.delete_scheduled_post(post_id)
                logger.info(f"Scheduled post {post_id} sent successfully")
                
            except Exception as e:
                logger.error(f"Error sending scheduled post {post_id}: {e}")

        # Создаем и сохраняем задачу
        task = asyncio.create_task(send_scheduled_post())
        self.scheduled_posts[post_id] = task

    async def list_scheduled_posts(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Показать список запланированных постов"""
        if not await self.is_admin(update):
            await update.message.reply_text("❌ Эта команда только для администраторов!")
            return

        posts = self.db.get_all_scheduled_posts()
        
        if not posts:
            await update.message.reply_text("📭 Нет запланированных постов")
            return
        
        posts_text = "📅 Запланированные посты:\n\n"
        for post in posts:
            post_time = datetime.strptime(post[4], "%Y-%m-%d %H:%M:%S")
            posts_text += f"🆔 {post[0]}: {post[2][:50]}...\n"
            posts_text += f"⏰ {post_time.strftime('%d.%m.%Y %H:%M')}\n\n"
        
        await update.message.reply_text(posts_text)

    async def show_stats(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Показать статистику"""
        if not await self.is_admin(update):
            await update.message.reply_text("❌ Эта команда только для администраторов!")
            return

        posts = self.db.get_all_scheduled_posts()
        stats_text = (
            f"📊 Статистика бота:\n"
            f"• Запланировано постов: {len(posts)}\n"
            f"• Активные задачи: {len(self.scheduled_posts)}\n"
            f"• Канал: {CHANNEL_ID}\n"
            f"• Время сервера: {datetime.now().strftime('%d.%m.%Y %H:%M')}"
        )
        
        await update.message.reply_text(stats_text)

    async def broadcast(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Рассылка сообщения всем подписчикам (заглушка)"""
        if not await self.is_admin(update):
            await update.message.reply_text("❌ Эта команда только для администраторов!")
            return
        
        await update.message.reply_text("📢 Функция рассылки в разработке...")

    # Обработчики медиа
    async def handle_photo(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработка фотографий"""
        if not await self.is_admin(update):
            return

        keyboard = [
            [InlineKeyboardButton("📢 Опубликовать в канал", callback_data="publish_photo")],
            [InlineKeyboardButton("📅 Запланировать", callback_data="schedule_photo")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        # Сохраняем фото во временный контекст
        context.user_data['last_photo'] = update.message.photo[-1].file_id
        context.user_data['last_caption'] = update.message.caption
        
        await update.message.reply_text(
            "📸 Фото получено! Выберите действие:",
            reply_markup=reply_markup
        )

    async def handle_document(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработка документов"""
        if not await self.is_admin(update):
            return

        await update.message.reply_text(
            "📎 Документ получен. Для публикации в канал используйте:\n"
            "/post с текстом и прикрепленным документом"
        )

    # Обработчики кнопок
    async def button_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработка нажатий на кнопки"""
        query = update.callback_query
        await query.answer()
        
        data = query.data
        
        if data == "create_post":
            await query.edit_message_text(
                "📝 Отправьте текст сообщения и используйте команду /post для публикации"
            )
        elif data == "schedule_post":
            await query.edit_message_text(
                "⏰ Для планирования поста используйте:\n"
                "/schedule \"текст сообщения\" HH:MM DD.MM.YYYY"
            )
        elif data == "show_stats":
            posts = self.db.get_all_scheduled_posts()
            stats_text = f"📊 Статистика:\nЗапланировано постов: {len(posts)}"
            await query.edit_message_text(stats_text)
        elif data == "help":
            await self.help_command(update, context)
        elif data == "publish_photo":
            # Публикация фото в канал
            photo_id = context.user_data.get('last_photo')
            caption = context.user_data.get('last_caption', '')
            
            if photo_id:
                try:
                    await context.bot.send_photo(
                        chat_id=CHANNEL_ID,
                        photo=photo_id,
                        caption=caption,
                        parse_mode="HTML"
                    )
                    await query.edit_message_text("✅ Фото опубликовано в канале!")
                except Exception as e:
                    await query.edit_message_text(f"❌ Ошибка: {e}")
            else:
                await query.edit_message_text("❌ Фото не найдено")

    # Вспомогательные функции
    async def is_admin(self, update: Update) -> bool:
        """Проверка прав администратора"""
        user_id = update.effective_user.id
        return user_id in ADMIN_IDS

    async def handle_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработка обычных сообщений"""
        if not await self.is_admin(update):
            await update.message.reply_text("ℹ️ Этот бот только для администраторов канала")
            return

        text = update.message.text
        await update.message.reply_text(
            f"💬 Получено сообщение: {text[:100]}...\n\n"
            f"Используйте /post чтобы отправить его в канал\n"
            f"Или /schedule для планирования"
        )

    def load_scheduled_posts(self):
        """Загрузка запланированных постов при запуске"""
        posts = self.db.get_all_scheduled_posts()
        for post in posts:
            post_id = post[0]
            message_text = post[2]
            scheduled_time = datetime.strptime(post[4], "%Y-%m-%d %H:%M:%S")
            
            if scheduled_time > datetime.now():
                self.create_scheduled_task(post_id, message_text, scheduled_time)
            else:
                # Удаляем просроченные посты
                self.db.delete_scheduled_post(post_id)

    def run(self):
        """Запуск бота"""
        # Загружаем запланированные посты
        self.load_scheduled_posts()
        
        # Запускаем бота
        logger.info("Бот запущен!")
        self.application.run_polling()

# Запуск бота
if __name__ == "__main__":
    # Проверка токена
    if BOT_TOKEN == "YOUR_BOT_TOKEN_HERE":
        print("❌ Установите BOT_TOKEN в коде!")
        exit(1)
    
    bot = ChannelBot(BOT_TOKEN)
    bot.run()
