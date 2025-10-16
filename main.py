import logging
import sqlite3
import schedule
import time
import asyncio
from datetime import datetime, timedelta
from threading import Thread
from telegram.ext import Updater, CommandHandler, MessageHandler, Filters, CallbackQueryHandler
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
CHANNEL_ID = "@timur_onion"
ADMIN_IDS = [5024165375]

# База данных (остается без изменений)
class Database:
    def __init__(self):
        self.conn = sqlite3.connect('channel_bot.db', check_same_thread=False)
        self.create_tables()
    
    def create_tables(self):
        cursor = self.conn.cursor()
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
        self.conn.commit()

    def add_scheduled_post(self, chat_id: int, message_text: str, scheduled_time: datetime, media_path: str = None):
        cursor = self.conn.cursor()
        cursor.execute('''
            INSERT INTO scheduled_posts (chat_id, message_text, media_path, scheduled_time)
            VALUES (?, ?, ?, ?)
        ''', (chat_id, message_text, media_path, scheduled_time))
        self.conn.commit()
        return cursor.lastrowid

    def get_pending_posts(self):
        cursor = self.conn.cursor()
        cursor.execute('''
            SELECT * FROM scheduled_posts 
            WHERE scheduled_time <= datetime('now') 
            ORDER BY scheduled_time ASC
        ''')
        return cursor.fetchall()

    def delete_scheduled_post(self, post_id: int):
        cursor = self.conn.cursor()
        cursor.execute('DELETE FROM scheduled_posts WHERE id = ?', (post_id,))
        self.conn.commit()

    def get_all_scheduled_posts(self):
        cursor = self.conn.cursor()
        cursor.execute('SELECT * FROM scheduled_posts ORDER BY scheduled_time ASC')
        return cursor.fetchall()

# Основной класс бота для PTB v13.x
class ChannelBot:
    def __init__(self, token: str):
        self.updater = Updater(token, use_context=True)
        self.dispatcher = self.updater.dispatcher
        self.db = Database()
        self.setup_handlers()

    def setup_handlers(self):
        """Настройка обработчиков для PTB v13"""
        # Команды
        self.dispatcher.add_handler(CommandHandler("start", self.start))
        self.dispatcher.add_handler(CommandHandler("help", self.help_command))
        self.dispatcher.add_handler(CommandHandler("post", self.post_to_channel))
        self.dispatcher.add_handler(CommandHandler("schedule", self.schedule_post))
        self.dispatcher.add_handler(CommandHandler("posts_list", self.list_scheduled_posts))
        self.dispatcher.add_handler(CommandHandler("stats", self.show_stats))
        
        # Обработчики кнопок
        self.dispatcher.add_handler(CallbackQueryHandler(self.button_handler))
        
        # Обработчики сообщений
        self.dispatcher.add_handler(
            MessageHandler(Filters.text & ~Filters.command, self.handle_message)
        )
        self.dispatcher.add_handler(
            MessageHandler(Filters.photo, self.handle_photo)
        )
        self.dispatcher.add_handler(
            MessageHandler(Filters.document, self.handle_document)
        )

    # Основные команды
    def start(self, update: Update, context):
        """Обработчик команды /start"""
        user = update.effective_user
        
        keyboard = [
            [InlineKeyboardButton("📝 Создать пост", callback_data="create_post")],
            [InlineKeyboardButton("📅 Запланировать", callback_data="schedule_post")],
            [InlineKeyboardButton("📊 Статистика", callback_data="show_stats")],
            [InlineKeyboardButton("❓ Помощь", callback_data="help")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        update.message.reply_text(
            f"👋 Привет, {user.first_name}!\n\n"
            f"Я бот для управления каналом {CHANNEL_ID}\n"
            f"Выберите действие:",
            reply_markup=reply_markup
        )

    def help_command(self, update: Update, context):
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

📁 **Поддержка форматов:**
- Текст с форматированием
- Фотографии
- Документы
        """
        update.message.reply_text(help_text)

    def post_to_channel(self, update: Update, context):
        """Отправка сообщения в канал"""
        if not self.is_admin(update):
            update.message.reply_text("❌ Эта команда только для администраторов!")
            return

        if context.args:
            message_text = " ".join(context.args)
        else:
            if update.message.reply_to_message:
                message_text = update.message.reply_to_message.text or update.message.reply_to_message.caption
            else:
                update.message.reply_text(
                    "📝 Укажите текст сообщения после команды /post\n"
                    "Или ответьте на сообщение командой /post"
                )
                return

        try:
            context.bot.send_message(
                chat_id=CHANNEL_ID,
                text=message_text,
                parse_mode="HTML"
            )
            update.message.reply_text("✅ Сообщение успешно отправлено в канал!")
            logger.info(f"Post sent to channel by {update.effective_user.id}")
        except Exception as e:
            error_msg = f"❌ Ошибка при отправке: {e}"
            update.message.reply_text(error_msg)
            logger.error(f"Error posting to channel: {e}")

    def schedule_post(self, update: Update, context):
        """Планирование поста"""
        if not self.is_admin(update):
            update.message.reply_text("❌ Эта команда только для администраторов!")
            return

        if not context.args:
            update.message.reply_text(
                "⏰ Формат: /schedule \"текст сообщения\" HH:MM DD.MM.YYYY\n"
                "Пример: /schedule \"Привет мир!\" 14:30 25.12.2024"
            )
            return

        try:
            message_parts = " ".join(context.args).split('"')
            if len(message_parts) < 3:
                raise ValueError("Неверный формат сообщения")
            
            message_text = message_parts[1]
            time_date = message_parts[2].strip().split()
            
            if len(time_date) < 2:
                raise ValueError("Укажите время и дату")
            
            time_str = time_date[0]
            date_str = time_date[1] if len(time_date) > 1 else datetime.now().strftime("%d.%m.%Y")
            
            scheduled_time = datetime.strptime(f"{date_str} {time_str}", "%d.%m.%Y %H:%M")
            
            if scheduled_time <= datetime.now():
                update.message.reply_text("❌ Укажите время в будущем!")
                return
            
            post_id = self.db.add_scheduled_post(
                update.effective_chat.id,
                message_text,
                scheduled_time
            )
            
            update.message.reply_text(
                f"✅ Пост запланирован на {scheduled_time.strftime('%d.%m.%Y %H:%M')}\n"
                f"ID поста: {post_id}"
            )
            
        except ValueError as e:
            update.message.reply_text(f"❌ Ошибка формата: {e}")
        except Exception as e:
            update.message.reply_text(f"❌ Ошибка: {e}")

    def list_scheduled_posts(self, update: Update, context):
        """Показать список запланированных постов"""
        if not self.is_admin(update):
            update.message.reply_text("❌ Эта команда только для администраторов!")
            return

        posts = self.db.get_all_scheduled_posts()
        
        if not posts:
            update.message.reply_text("📭 Нет запланированных постов")
            return
        
        posts_text = "📅 Запланированные посты:\n\n"
        for post in posts:
            post_time = datetime.strptime(post[4], "%Y-%m-%d %H:%M:%S")
            posts_text += f"🆔 {post[0]}: {post[2][:50]}...\n"
            posts_text += f"⏰ {post_time.strftime('%d.%m.%Y %H:%M')}\n\n"
        
        update.message.reply_text(posts_text)

    def show_stats(self, update: Update, context):
        """Показать статистику"""
        if not self.is_admin(update):
            update.message.reply_text("❌ Эта команда только для администраторов!")
            return

        posts = self.db.get_all_scheduled_posts()
        stats_text = (
            f"📊 Статистика бота:\n"
            f"• Запланировано постов: {len(posts)}\n"
            f"• Канал: {CHANNEL_ID}\n"
            f"• Время сервера: {datetime.now().strftime('%d.%m.%Y %H:%M')}"
        )
        
        update.message.reply_text(stats_text)

    # Обработчики медиа
    def handle_photo(self, update: Update, context):
        """Обработка фотографий"""
        if not self.is_admin(update):
            return

        keyboard = [
            [InlineKeyboardButton("📢 Опубликовать в канал", callback_data="publish_photo")],
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        context.user_data['last_photo'] = update.message.photo[-1].file_id
        context.user_data['last_caption'] = update.message.caption
        
        update.message.reply_text(
            "📸 Фото получено! Выберите действие:",
            reply_markup=reply_markup
        )

    def handle_document(self, update: Update, context):
        """Обработка документов"""
        if not self.is_admin(update):
            return

        update.message.reply_text(
            "📎 Документ получен. Для публикации в канал используйте:\n"
            "/post с текстом и прикрепленным документом"
        )

    # Обработчики кнопок
    def button_handler(self, update: Update, context):
        """Обработка нажатий на кнопки"""
        query = update.callback_query
        query.answer()
        
        data = query.data
        
        if data == "create_post":
            query.edit_message_text(
                "📝 Отправьте текст сообщения и используйте команду /post для публикации"
            )
        elif data == "schedule_post":
            query.edit_message_text(
                "⏰ Для планирования поста используйте:\n"
                "/schedule \"текст сообщения\" HH:MM DD.MM.YYYY"
            )
        elif data == "show_stats":
            posts = self.db.get_all_scheduled_posts()
            stats_text = f"📊 Статистика:\nЗапланировано постов: {len(posts)}"
            query.edit_message_text(stats_text)
        elif data == "help":
            self.help_command(update, context)
        elif data == "publish_photo":
            photo_id = context.user_data.get('last_photo')
            caption = context.user_data.get('last_caption', '')
            
            if photo_id:
                try:
                    context.bot.send_photo(
                        chat_id=CHANNEL_ID,
                        photo=photo_id,
                        caption=caption,
                        parse_mode="HTML"
                    )
                    query.edit_message_text("✅ Фото опубликовано в канале!")
                except Exception as e:
                    query.edit_message_text(f"❌ Ошибка: {e}")
            else:
                query.edit_message_text("❌ Фото не найдено")

    # Вспомогательные функции
    def is_admin(self, update: Update) -> bool:
        """Проверка прав администратора"""
        user_id = update.effective_user.id
        return user_id in ADMIN_IDS

    def handle_message(self, update: Update, context):
        """Обработка обычных сообщений"""
        if not self.is_admin(update):
            update.message.reply_text("ℹ️ Этот бот только для администраторов канала")
            return

        text = update.message.text
        update.message.reply_text(
            f"💬 Получено сообщение: {text[:100]}...\n\n"
            f"Используйте /post чтобы отправить его в канал\n"
            f"Или /schedule для планирования"
        )

    def run(self):
        """Запуск бота"""
        logger.info("Бот запущен!")
        self.updater.start_polling()
        self.updater.idle()

# Запуск бота
if __name__ == "__main__":
    if BOT_TOKEN == "YOUR_BOT_TOKEN_HERE":
        print("❌ Установите BOT_TOKEN в коде!")
        exit(1)
    
    bot = ChannelBot(BOT_TOKEN)
    bot.run()
