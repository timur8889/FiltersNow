import logging
import sqlite3
import schedule
import time
import asyncio
from datetime import datetime, timedelta
from threading import Thread
from telegram.ext import Updater, CommandHandler, MessageHandler, Filters, CallbackQueryHandler
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, InputFile, ReplyKeyboardMarkup, KeyboardButton
import os
from typing import Dict, List
import atexit
import signal
import sys

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

# База данных
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
                media_type TEXT,
                scheduled_time DATETIME,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        self.conn.commit()

    def add_scheduled_post(self, chat_id: int, message_text: str, scheduled_time: datetime, media_path: str = None, media_type: str = None):
        cursor = self.conn.cursor()
        cursor.execute('''
            INSERT INTO scheduled_posts (chat_id, message_text, media_path, media_type, scheduled_time)
            VALUES (?, ?, ?, ?, ?)
        ''', (chat_id, message_text, media_path, media_type, scheduled_time))
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

    def get_post_by_id(self, post_id: int):
        cursor = self.conn.cursor()
        cursor.execute('SELECT * FROM scheduled_posts WHERE id = ?', (post_id,))
        return cursor.fetchone()

    def close_connection(self):
        """Закрытие соединения с БД"""
        if self.conn:
            self.conn.close()
            logger.info("Соединение с БД закрыто")

# Основной класс бота для PTB v13.x
class ChannelBot:
    def __init__(self, token: str):
        self.updater = Updater(token, use_context=True)
        self.dispatcher = self.updater.dispatcher
        self.db = Database()
        self.setup_handlers()
        self.setup_scheduler()
        self.running = True

    def setup_handlers(self):
        """Настройка обработчиков для PTB v13"""
        # Команды
        self.dispatcher.add_handler(CommandHandler("start", self.start))
        self.dispatcher.add_handler(CommandHandler("help", self.help_command))
        self.dispatcher.add_handler(CommandHandler("post", self.post_to_channel))
        self.dispatcher.add_handler(CommandHandler("schedule", self.schedule_post))
        self.dispatcher.add_handler(CommandHandler("posts_list", self.list_scheduled_posts))
        self.dispatcher.add_handler(CommandHandler("stats", self.show_stats))
        self.dispatcher.add_handler(CommandHandler("menu", self.show_main_menu))
        self.dispatcher.add_handler(CommandHandler("cancel", self.cancel_action))
        self.dispatcher.add_handler(CommandHandler("delete_post", self.delete_post_command))
        
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

    def setup_scheduler(self):
        """Настройка планировщика для проверки отложенных постов"""
        def check_pending_posts():
            if not self.running:
                return
            try:
                pending_posts = self.db.get_pending_posts()
                for post in pending_posts:
                    try:
                        self.send_scheduled_post(post)
                        self.db.delete_scheduled_post(post[0])
                        logger.info(f"Scheduled post {post[0]} sent to channel")
                    except Exception as e:
                        logger.error(f"Error sending scheduled post {post[0]}: {e}")
            except Exception as e:
                logger.error(f"Error in scheduler: {e}")

        schedule.every(1).minutes.do(check_pending_posts)
        
        def run_scheduler():
            while self.running:
                schedule.run_pending()
                time.sleep(1)
        
        scheduler_thread = Thread(target=run_scheduler, daemon=True)
        scheduler_thread.start()

    def send_scheduled_post(self, post):
        """Отправка запланированного поста"""
        try:
            bot = self.updater.bot
            media_path = post[3]
            media_type = post[5] if len(post) > 5 else None
            
            if media_path and os.path.exists(media_path):
                if media_type == 'photo' or media_path.lower().endswith(('.jpg', '.jpeg', '.png')):
                    with open(media_path, 'rb') as photo:
                        bot.send_photo(
                            chat_id=CHANNEL_ID,
                            photo=photo,
                            caption=post[2],
                            parse_mode="HTML"
                        )
                else:
                    with open(media_path, 'rb') as document:
                        bot.send_document(
                            chat_id=CHANNEL_ID,
                            document=document,
                            caption=post[2],
                            parse_mode="HTML"
                        )
                # Удаляем временный файл после отправки
                try:
                    os.remove(media_path)
                except Exception as e:
                    logger.warning(f"Could not delete media file {media_path}: {e}")
            else:
                bot.send_message(
                    chat_id=CHANNEL_ID,
                    text=post[2],
                    parse_mode="HTML"
                )
        except Exception as e:
            logger.error(f"Error sending scheduled post: {e}")

    def validate_schedule_time(self, scheduled_time: datetime) -> bool:
        """Проверка корректности времени планирования"""
        min_time = datetime.now() + timedelta(minutes=5)
        max_time = datetime.now() + timedelta(days=365)  # 1 год максимум
        return min_time <= scheduled_time <= max_time

    def validate_message_length(self, text: str) -> bool:
        """Проверка длины сообщения"""
        return len(text) <= 4096  # Лимит Telegram

    def check_bot_channel_permissions(self):
        """Проверка прав бота в канале"""
        try:
            chat = self.updater.bot.get_chat(CHANNEL_ID)
            logger.info(f"Бот имеет доступ к каналу: {chat.title}")
            return True
        except Exception as e:
            logger.error(f"Бот не имеет доступа к каналу: {e}")
            return False

    def get_main_keyboard(self):
        """Основная клавиатура меню"""
        keyboard = [
            [
                KeyboardButton("📝 Создать пост"), 
                KeyboardButton("📅 Запланировать")
            ],
            [
                KeyboardButton("📋 Список постов"), 
                KeyboardButton("📊 Статистика")
            ],
            [
                KeyboardButton("❓ Помощь"), 
                KeyboardButton("⚙️ Настройки")
            ]
        ]
        return ReplyKeyboardMarkup(keyboard, resize_keyboard=True, persistent=True)

    def get_admin_keyboard(self):
        """Админ клавиатура (расширенная)"""
        keyboard = [
            [
                KeyboardButton("📝 Быстрый пост"), 
                KeyboardButton("📅 Планировщик")
            ],
            [
                KeyboardButton("📋 Все посты"), 
                KeyboardButton("📊 Статистика")
            ],
            [
                KeyboardButton("🖼️ Медиа меню"), 
                KeyboardButton("⚡ Инструменты")
            ],
            [
                KeyboardButton("❓ Помощь"), 
                KeyboardButton("🔙 Главное меню")
            ]
        ]
        return ReplyKeyboardMarkup(keyboard, resize_keyboard=True, persistent=True)

    def get_media_keyboard(self):
        """Клавиатура для работы с медиа"""
        keyboard = [
            [
                KeyboardButton("📸 Фото + текст"), 
                KeyboardButton("📎 Документ + текст")
            ],
            [
                KeyboardButton("🖼️ Только фото"), 
                KeyboardButton("📄 Только документ")
            ],
            [
                KeyboardButton("🔙 Назад"), 
                KeyboardButton("📋 Список медиа")
            ]
        ]
        return ReplyKeyboardMarkup(keyboard, resize_keyboard=True, persistent=True)

    def get_tools_keyboard(self):
        """Клавиатура инструментов"""
        keyboard = [
            [
                KeyboardButton("🕐 Проверить посты"), 
                KeyboardButton("🧹 Очистить старые")
            ],
            [
                KeyboardButton("📈 Аналитика"), 
                KeyboardButton("🔔 Уведомления")
            ],
            [
                KeyboardButton("🔙 Назад"), 
                KeyboardButton("🏠 Главное меню")
            ]
        ]
        return ReplyKeyboardMarkup(keyboard, resize_keyboard=True, persistent=True)

    def get_inline_post_keyboard(self):
        """Inline клавиатура для постов"""
        keyboard = [
            [
                InlineKeyboardButton("📢 Опубликовать сейчас", callback_data="publish_now"),
                InlineKeyboardButton("📅 Запланировать", callback_data="schedule_this")
            ],
            [
                InlineKeyboardButton("✏️ Редактировать", callback_data="edit_post"),
                InlineKeyboardButton("🗑️ Удалить", callback_data="delete_post")
            ],
            [
                InlineKeyboardButton("🖼️ Добавить медиа", callback_data="add_media"),
                InlineKeyboardButton("⏰ Время публикации", callback_data="set_time")
            ]
        ]
        return InlineKeyboardMarkup(keyboard)

    def get_inline_schedule_keyboard(self):
        """Inline клавиатура для планирования"""
        keyboard = [
            [
                InlineKeyboardButton("⏰ Через 1 час", callback_data="schedule_1h"),
                InlineKeyboardButton("⏰ Через 3 часа", callback_data="schedule_3h")
            ],
            [
                InlineKeyboardButton("📅 Завтра утро", callback_data="schedule_tomorrow_morning"),
                InlineKeyboardButton("📅 Завтра вечер", callback_data="schedule_tomorrow_evening")
            ],
            [
                InlineKeyboardButton("🗓️ Выбрать дату", callback_data="schedule_custom"),
                InlineKeyboardButton("❌ Отмена", callback_data="cancel_schedule")
            ]
        ]
        return InlineKeyboardMarkup(keyboard)

    def get_post_management_keyboard(self, post_id: int):
        """Inline клавиатура для управления конкретным постом"""
        keyboard = [
            [
                InlineKeyboardButton("📢 Опубликовать сейчас", callback_data=f"publish_{post_id}"),
                InlineKeyboardButton("✏️ Редактировать", callback_data=f"edit_{post_id}")
            ],
            [
                InlineKeyboardButton("🗑️ Удалить", callback_data=f"delete_{post_id}"),
                InlineKeyboardButton("⏰ Изменить время", callback_data=f"reschedule_{post_id}")
            ]
        ]
        return InlineKeyboardMarkup(keyboard)

    # Основные команды
    def start(self, update: Update, context):
        """Обработчик команды /start"""
        user = update.effective_user
        
        # Проверка прав бота в канале при первом запуске
        if not hasattr(self, 'channel_checked'):
            if self.check_bot_channel_permissions():
                self.channel_checked = True
            else:
                update.message.reply_text(
                    "❌ Бот не имеет доступа к каналу! Пожалуйста, добавьте бота как администратора в канал.",
                    reply_markup=self.get_main_keyboard()
                )
                return
        
        welcome_text = f"""
🎉 Добро пожаловать, {user.first_name}!

🤖 Я - ваш помощник в управлении каналом {CHANNEL_ID}

✨ <b>Что я умею:</b>
• 📝 Создавать и публиковать посты
• 📅 Планировать публикации
• 🖼️ Работать с фото и документами
• 📊 Показывать статистику

👇 Используйте кнопки ниже для навигации:
        """
        
        if self.is_admin(update):
            update.message.reply_text(
                welcome_text,
                reply_markup=self.get_admin_keyboard(),
                parse_mode="HTML"
            )
        else:
            update.message.reply_text(
                "👋 Привет! Этот бот предназначен для администраторов канала.",
                reply_markup=self.get_main_keyboard()
            )

    def show_main_menu(self, update: Update, context):
        """Показать главное меню"""
        if self.is_admin(update):
            update.message.reply_text(
                "🏠 <b>Главное меню администратора</b>\n\n"
                "Выберите действие:",
                reply_markup=self.get_admin_keyboard(),
                parse_mode="HTML"
            )
        else:
            update.message.reply_text(
                "🏠 Главное меню",
                reply_markup=self.get_main_keyboard()
            )

    def cancel_action(self, update: Update, context):
        """Отмена текущего действия"""
        context.user_data.clear()
        update.message.reply_text(
            "❌ Действие отменено.",
            reply_markup=self.get_admin_keyboard() if self.is_admin(update) else self.get_main_keyboard()
        )

    def help_command(self, update: Update, context):
        """Обработчик команды /help"""
        help_text = """
🤖 <b>Руководство по использованию бота</b>

📝 <b>Создание постов:</b>
• Нажмите «📝 Создать пост» и отправьте текст
• Или используйте команду /post [текст]

📅 <b>Планирование:</b>
• «📅 Запланировать» - создать отложенный пост
• Формат: /schedule "текст" HH:MM DD.MM.YYYY

🖼️ <b>Медиа:</b>
• Отправьте фото/документ с подписью
• Используйте медиа-меню для управления

📊 <b>Управление:</b>
• «📋 Список постов» - все запланированные
• «📊 Статистика» - аналитика канала
• /delete_post ID - удалить запланированный пост

⚡ <b>Быстрые команды:</b>
/post - опубликовать сейчас
/schedule - запланировать пост
/posts_list - список постов
/stats - статистика
/menu - главное меню
/cancel - отмена действия
        """
        update.message.reply_text(help_text, parse_mode="HTML")

    def post_to_channel(self, update: Update, context):
        """Отправка сообщения в канал"""
        if not self.is_admin(update):
            update.message.reply_text("❌ Эта команда только для администраторов!")
            return

        if context.args:
            message_text = " ".join(context.args)
        else:
            if update.message.reply_to_message:
                message_text = update.message.reply_to_message.text or update.message.reply_to_message.caption or ""
            else:
                update.message.reply_text(
                    "📝 Укажите текст сообщения после команды /post\n"
                    "Или ответьте на сообщение командой /post"
                )
                return

        # Валидация длины сообщения
        if not self.validate_message_length(message_text):
            update.message.reply_text("❌ Сообщение слишком длинное! Максимум 4096 символов.")
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
                raise ValueError("Неверный формат сообщения. Используйте кавычки для текста.")
            
            message_text = message_parts[1]
            time_date = message_parts[2].strip().split()
            
            if len(time_date) < 2:
                raise ValueError("Укажите время и дату")
            
            time_str = time_date[0]
            date_str = time_date[1] if len(time_date) > 1 else datetime.now().strftime("%d.%m.%Y")
            
            scheduled_time = datetime.strptime(f"{date_str} {time_str}", "%d.%m.%Y %H:%M")
            
            # Валидация времени
            if not self.validate_schedule_time(scheduled_time):
                update.message.reply_text(
                    "❌ Некорректное время планирования!\n"
                    "• Минимум: через 5 минут\n"
                    "• Максимум: 1 год вперед"
                )
                return
            
            # Валидация длины сообщения
            if not self.validate_message_length(message_text):
                update.message.reply_text("❌ Сообщение слишком длинное! Максимум 4096 символов.")
                return
            
            post_id = self.db.add_scheduled_post(
                update.effective_chat.id,
                message_text,
                scheduled_time
            )
            
            update.message.reply_text(
                f"✅ Пост запланирован на {scheduled_time.strftime('%d.%m.%Y %H:%M')}\n"
                f"🆔 ID поста: {post_id}\n"
                f"📝 Текст: {message_text[:100]}{'...' if len(message_text) > 100 else ''}"
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
            time_left = post_time - datetime.now()
            hours_left = int(time_left.total_seconds() // 3600)
            minutes_left = int((time_left.total_seconds() % 3600) // 60)
            
            posts_text += f"🆔 {post[0]}: {post[2][:50]}...\n"
            posts_text += f"⏰ {post_time.strftime('%d.%m.%Y %H:%M')}\n"
            posts_text += f"⏳ Осталось: {hours_left}ч {minutes_left}м\n\n"
        
        posts_text += "ℹ️ Используйте /delete_post ID для удаления"
        update.message.reply_text(posts_text)

    def delete_post_command(self, update: Update, context):
        """Удаление запланированного поста"""
        if not self.is_admin(update):
            update.message.reply_text("❌ Эта команда только для администраторов!")
            return

        if not context.args:
            update.message.reply_text("❌ Укажите ID поста: /delete_post ID")
            return

        try:
            post_id = int(context.args[0])
            post = self.db.get_post_by_id(post_id)
            
            if not post:
                update.message.reply_text("❌ Пост с таким ID не найден")
                return
            
            self.db.delete_scheduled_post(post_id)
            update.message.reply_text(f"✅ Пост {post_id} удален из планировщика")
            
        except ValueError:
            update.message.reply_text("❌ ID поста должен быть числом")
        except Exception as e:
            update.message.reply_text(f"❌ Ошибка при удалении: {e}")

    def show_stats(self, update: Update, context):
        """Показать статистику"""
        if not self.is_admin(update):
            update.message.reply_text("❌ Эта команда только для администраторов!")
            return

        posts = self.db.get_all_scheduled_posts()
        now = datetime.now()
        
        # Статистика по времени
        upcoming_posts = [p for p in posts if datetime.strptime(p[4], "%Y-%m-%d %H:%M:%S") > now]
        past_posts = [p for p in posts if datetime.strptime(p[4], "%Y-%m-%d %H:%M:%S") <= now]
        
        stats_text = (
            f"📊 <b>Статистика бота</b>\n\n"
            f"• 📅 Всего постов в БД: {len(posts)}\n"
            f"• ⏳ Ожидают публикации: {len(upcoming_posts)}\n"
            f"• ✅ Опубликовано: {len(past_posts)}\n"
            f"• 📢 Канал: {CHANNEL_ID}\n"
            f"• 🕒 Время сервера: {now.strftime('%d.%m.%Y %H:%M:%S')}\n"
            f"• 🤖 Статус бота: {'🟢 Активен' if self.running else '🔴 Остановлен'}"
        )
        
        update.message.reply_text(stats_text, parse_mode="HTML")

    def handle_photo(self, update: Update, context):
        """Обработка фотографий"""
        if not self.is_admin(update):
            return

        # Сохраняем информацию о фото
        photo_file = update.message.photo[-1].get_file()
        photo_path = f"temp_photo_{update.message.message_id}.jpg"
        photo_file.download(photo_path)
        
        context.user_data['last_photo_path'] = photo_path
        context.user_data['last_photo_id'] = update.message.photo[-1].file_id
        context.user_data['last_caption'] = update.message.caption or ""
        
        keyboard = [
            [InlineKeyboardButton("📢 Опубликовать в канал", callback_data="publish_photo")],
            [InlineKeyboardButton("📅 Запланировать публикацию", callback_data="schedule_photo")],
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        update.message.reply_text(
            "📸 Фото получено! Выберите действие:",
            reply_markup=reply_markup
        )

    def handle_document(self, update: Update, context):
        """Обработка документов"""
        if not self.is_admin(update):
            return

        # Сохраняем информацию о документе
        document_file = update.message.document.get_file()
        document_path = f"temp_document_{update.message.message_id}_{update.message.document.file_name}"
        document_file.download(document_path)
        
        context.user_data['last_document_path'] = document_path
        context.user_data['last_document_id'] = update.message.document.file_id
        context.user_data['last_caption'] = update.message.caption or ""
        
        keyboard = [
            [InlineKeyboardButton("📢 Опубликовать в канал", callback_data="publish_document")],
            [InlineKeyboardButton("📅 Запланировать публикацию", callback_data="schedule_document")],
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        update.message.reply_text(
            "📎 Документ получен! Выберите действие:",
            reply_markup=reply_markup
        )

    def handle_message(self, update: Update, context):
        """Обработка текстовых сообщений с кнопок"""
        if not self.is_admin(update):
            update.message.reply_text("ℹ️ Этот бот только для администраторов канала")
            return

        text = update.message.text
        
        if text == "📝 Создать пост" or text == "📝 Быстрый пост":
            update.message.reply_text(
                "📝 <b>Создание поста</b>\n\n"
                "Отправьте текст поста или используйте команду:\n"
                "<code>/post ваш текст</code>\n\n"
                "Или ответьте на сообщение командой /post",
                parse_mode="HTML"
            )
            
        elif text == "📅 Запланировать" or text == "📅 Планировщик":
            update.message.reply_text(
                "📅 <b>Планировщик постов</b>\n\n"
                "Формат планирования:\n"
                "<code>/schedule \"Текст поста\" 14:30 25.12.2024</code>\n\n"
                "Или выберите быстрый вариант:",
                reply_markup=self.get_inline_schedule_keyboard(),
                parse_mode="HTML"
            )
            
        elif text == "📋 Список постов" or text == "📋 Все посты":
            self.list_scheduled_posts(update, context)
            
        elif text == "📊 Статистика":
            self.show_stats(update, context)
            
        elif text == "🖼️ Медиа меню":
            update.message.reply_text(
                "🖼️ <b>Медиа меню</b>\n\n"
                "Выберите тип медиа-поста:",
                reply_markup=self.get_media_keyboard(),
                parse_mode="HTML"
            )
            
        elif text == "⚡ Инструменты":
            update.message.reply_text(
                "⚡ <b>Инструменты администратора</b>\n\n"
                "Дополнительные функции управления:",
                reply_markup=self.get_tools_keyboard(),
                parse_mode="HTML"
            )
            
        elif text == "🔙 Назад":
            update.message.reply_text(
                "🔙 Возвращаемся назад...",
                reply_markup=self.get_admin_keyboard()
            )
            
        elif text == "🏠 Главное меню" or text == "🔙 Главное меню":
            self.show_main_menu(update, context)
            
        elif text == "❓ Помощь":
            self.help_command(update, context)
            
        elif text == "⚙️ Настройки":
            update.message.reply_text(
                "⚙️ <b>Настройки бота</b>\n\n"
                "Доступные настройки:\n"
                "• Уведомления\n"
                "• Формат времени\n"
                "• Автоматизация\n\n"
                "Эта функция в разработке 🚧",
                parse_mode="HTML"
            )
            
        elif text == "🕐 Проверить посты":
            self.list_scheduled_posts(update, context)
            
        elif text == "🧹 Очистить старые":
            update.message.reply_text(
                "🧹 <b>Очистка старых постов</b>\n\n"
                "Эта функция будет удалять уже опубликованные посты из БД.\n"
                "В разработке 🚧",
                parse_mode="HTML"
            )
            
        else:
            # Если сообщение не соответствует кнопкам, предлагаем меню
            update.message.reply_text(
                f"💬 Получено сообщение: {text[:100]}...\n\n"
                f"Используйте кнопки меню для управления ботом:",
                reply_markup=self.get_admin_keyboard()
            )

    def button_handler(self, update: Update, context):
        """Обработка нажатий на кнопки"""
        query = update.callback_query
        query.answer()
        
        data = query.data
        
        # Обработка новых callback данных
        if data.startswith("schedule_"):
            self.handle_schedule_buttons(query, context, data)
        elif data == "publish_now":
            query.edit_message_text("📢 Публикую пост...")
            # Здесь логика немедленной публикации
        elif data == "edit_post":
            query.edit_message_text("✏️ Режим редактирования...")
            # Логика редактирования
        elif data == "delete_post":
            query.edit_message_text("🗑️ Удаляем пост...")
            # Логика удаления
        elif data == "publish_photo":
            self.publish_photo_handler(query, context)
        elif data == "schedule_photo":
            self.schedule_photo_handler(query, context)
        elif data == "publish_document":
            self.publish_document_handler(query, context)
        elif data == "schedule_document":
            self.schedule_document_handler(query, context)
        elif data.startswith("publish_"):
            post_id = int(data.split("_")[1])
            self.publish_post_now(query, context, post_id)
        elif data.startswith("delete_"):
            post_id = int(data.split("_")[1])
            self.delete_post_handler(query, context, post_id)
        else:
            # Старые обработчики
            if data == "create_post":
                query.edit_message_text(
                    "📝 Отправьте текст сообщения и используйте команду /post для публикации"
                )
            elif data == "schedule_post":
                query.edit_message_text(
                    "⏰ Для планирования поста используйте:\n"
                    "/schedule \"текст сообщения\" HH:MM DD.MM.YYYY"
                )

    def handle_schedule_buttons(self, query, context, data):
        """Обработка кнопок планирования"""
        now = datetime.now()
        
        if data == "schedule_1h":
            scheduled_time = now + timedelta(hours=1)
            self.schedule_quick_post(query, context, scheduled_time, "через 1 час")
        elif data == "schedule_3h":
            scheduled_time = now + timedelta(hours=3)
            self.schedule_quick_post(query, context, scheduled_time, "через 3 часа")
        elif data == "schedule_tomorrow_morning":
            tomorrow = now + timedelta(days=1)
            scheduled_time = tomorrow.replace(hour=9, minute=0, second=0, microsecond=0)
            self.schedule_quick_post(query, context, scheduled_time, "завтра утро (09:00)")
        elif data == "schedule_tomorrow_evening":
            tomorrow = now + timedelta(days=1)
            scheduled_time = tomorrow.replace(hour=18, minute=0, second=0, microsecond=0)
            self.schedule_quick_post(query, context, scheduled_time, "завтра вечер (18:00)")
        elif data == "schedule_custom":
            query.edit_message_text(
                "🗓️ Введите дату и время в формате:\n"
                "<code>HH:MM DD.MM.YYYY</code>\n\n"
                "Пример: <code>14:30 25.12.2024</code>",
                parse_mode="HTML"
            )
        elif data == "cancel_schedule":
            query.edit_message_text("❌ Планирование отменено")

    def schedule_quick_post(self, query, context, scheduled_time, description):
        """Быстрое планирование поста"""
        if 'last_message_text' in context.user_data:
            message_text = context.user_data['last_message_text']
            post_id = self.db.add_scheduled_post(
                query.message.chat_id,
                message_text,
                scheduled_time
            )
            query.edit_message_text(
                f"✅ Пост запланирован на {description}\n"
                f"🆔 ID: {post_id}\n"
                f"📝 Текст: {message_text[:100]}{'...' if len(message_text) > 100 else ''}"
            )
        else:
            query.edit_message_text(
                "❌ Не найден текст для публикации. Сначала отправьте текст сообщения."
            )

    def publish_photo_handler(self, query, context):
        """Обработчик публикации фото"""
        photo_path = context.user_data.get('last_photo_path')
        caption = context.user_data.get('last_caption', '')
        
        if photo_path and os.path.exists(photo_path):
            try:
                with open(photo_path, 'rb') as photo:
                    context.bot.send_photo(
                        chat_id=CHANNEL_ID,
                        photo=photo,
                        caption=caption,
                        parse_mode="HTML"
                    )
                query.edit_message_text("✅ Фото опубликовано в канале!")
                # Очищаем временные данные
                os.remove(photo_path)
                context.user_data.pop('last_photo_path', None)
                context.user_data.pop('last_caption', None)
            except Exception as e:
                query.edit_message_text(f"❌ Ошибка: {e}")
        else:
            query.edit_message_text("❌ Фото не найдено или было удалено")

    def schedule_photo_handler(self, query, context):
        """Планирование публикации фото"""
        query.edit_message_text(
            "⏰ Для планирования фото используйте текстовую команду:\n"
            "/schedule с прикрепленным фото и текстом"
        )

    def publish_document_handler(self, query, context):
        """Обработчик публикации документа"""
        document_path = context.user_data.get('last_document_path')
        caption = context.user_data.get('last_caption', '')
        
        if document_path and os.path.exists(document_path):
            try:
                with open(document_path, 'rb') as document:
                    context.bot.send_document(
                        chat_id=CHANNEL_ID,
                        document=document,
                        caption=caption,
                        parse_mode="HTML"
                    )
                query.edit_message_text("✅ Документ опубликован в канале!")
                # Очищаем временные данные
                os.remove(document_path)
                context.user_data.pop('last_document_path', None)
                context.user_data.pop('last_caption', None)
            except Exception as e:
                query.edit_message_text(f"❌ Ошибка: {e}")
        else:
            query.edit_message_text("❌ Документ не найден или был удален")

    def schedule_document_handler(self, query, context):
        """Планирование публикации документа"""
        query.edit_message_text(
            "⏰ Для планирования документа используйте текстовую команду:\n"
            "/schedule с прикрепленным документом и текстом"
        )

    def publish_post_now(self, query, context, post_id):
        """Немедленная публикация запланированного поста"""
        post = self.db.get_post_by_id(post_id)
        if post:
            try:
                self.send_scheduled_post(post)
                self.db.delete_scheduled_post(post_id)
                query.edit_message_text(f"✅ Пост {post_id} опубликован сейчас!")
            except Exception as e:
                query.edit_message_text(f"❌ Ошибка публикации: {e}")
        else:
            query.edit_message_text("❌ Пост не найден")

    def delete_post_handler(self, query, context, post_id):
        """Удаление запланированного поста"""
        post = self.db.get_post_by_id(post_id)
        if post:
            self.db.delete_scheduled_post(post_id)
            query.edit_message_text(f"✅ Пост {post_id} удален из планировщика")
        else:
            query.edit_message_text("❌ Пост не найден")

    def is_admin(self, update: Update) -> bool:
        """Проверка прав администратора"""
        user_id = update.effective_user.id
        return user_id in ADMIN_IDS

    def stop_bot(self):
        """Остановка бота"""
        logger.info("Остановка бота...")
        self.running = False
        self.updater.stop()
        self.db.close_connection()
        logger.info("Бот остановлен")

    def run(self):
        """Запуск бота"""
        logger.info("Бот запущен с улучшенным меню!")
        
        # Проверка доступа к каналу
        if not self.check_bot_channel_permissions():
            logger.error("Бот не имеет доступа к каналу! Добавьте бота как администратора.")
        
        self.updater.start_polling()
        logger.info("Бот начал работу")
        self.updater.idle()

# Обработчики сигналов для graceful shutdown
def signal_handler(signum, frame, bot):
    print(f"\n🛑 Получен сигнал {signum}. Останавливаем бота...")
    bot.stop_bot()
    sys.exit(0)

# Запуск бота
if __name__ == "__main__":
    if BOT_TOKEN == "YOUR_BOT_TOKEN_HERE":
        print("❌ Установите BOT_TOKEN в коде!")
        exit(1)
    
    try:
        bot = ChannelBot(BOT_TOKEN)
        
        # Регистрация обработчиков сигналов
        signal.signal(signal.SIGINT, lambda s, f: signal_handler(s, f, bot))
        signal.signal(signal.SIGTERM, lambda s, f: signal_handler(s, f, bot))
        
        # Регистрация функции закрытия при выходе
        atexit.register(bot.stop_bot)
        
        bot.run()
    except Exception as e:
        logger.error(f"Ошибка при запуске бота: {e}")
        sys.exit(1)
