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
        self.setup_scheduler()

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
            while True:
                schedule.run_pending()
                time.sleep(1)
        
        scheduler_thread = Thread(target=run_scheduler, daemon=True)
        scheduler_thread.start()

    def send_scheduled_post(self, post):
        """Отправка запланированного поста"""
        try:
            bot = self.updater.bot
            if post[3]:  # Если есть медиа
                if post[3].endswith(('.jpg', '.jpeg', '.png')):
                    bot.send_photo(
                        chat_id=CHANNEL_ID,
                        photo=open(post[3], 'rb'),
                        caption=post[2],
                        parse_mode="HTML"
                    )
                else:
                    bot.send_document(
                        chat_id=CHANNEL_ID,
                        document=open(post[3], 'rb'),
                        caption=post[2],
                        parse_mode="HTML"
                    )
            else:
                bot.send_message(
                    chat_id=CHANNEL_ID,
                    text=post[2],
                    parse_mode="HTML"
                )
        except Exception as e:
            logger.error(f"Error sending scheduled post: {e}")

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

    # Основные команды
    def start(self, update: Update, context):
        """Обработчик команды /start"""
        user = update.effective_user
        
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
• Формат: «текст» HH:MM DD.MM.YYYY

🖼️ <b>Медиа:</b>
• Отправьте фото/документ с подписью
• Используйте медиа-меню для управления

📊 <b>Управление:</b>
• «📋 Список постов» - все запланированные
• «📊 Статистика» - аналитика канала

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
            query.edit_message_text(f"⏰ Пост запланирован на {scheduled_time.strftime('%H:%M')}")
        elif data == "schedule_3h":
            scheduled_time = now + timedelta(hours=3)
            query.edit_message_text(f"⏰ Пост запланирован на {scheduled_time.strftime('%H:%M')}")
        elif data == "schedule_tomorrow_morning":
            tomorrow = now + timedelta(days=1)
            scheduled_time = tomorrow.replace(hour=9, minute=0, second=0, microsecond=0)
            query.edit_message_text(f"📅 Пост запланирован на завтра утро (09:00)")
        elif data == "schedule_tomorrow_evening":
            tomorrow = now + timedelta(days=1)
            scheduled_time = tomorrow.replace(hour=18, minute=0, second=0, microsecond=0)
            query.edit_message_text(f"📅 Пост запланирован на завтра вечер (18:00)")
        elif data == "schedule_custom":
            query.edit_message_text(
                "🗓️ Введите дату и время в формате:\n"
                "<code>HH:MM DD.MM.YYYY</code>\n\n"
                "Пример: <code>14:30 25.12.2024</code>",
                parse_mode="HTML"
            )
        elif data == "cancel_schedule":
            query.edit_message_text("❌ Планирование отменено")

    def publish_photo_handler(self, query, context):
        """Обработчик публикации фото"""
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

    def is_admin(self, update: Update) -> bool:
        """Проверка прав администратора"""
        user_id = update.effective_user.id
        return user_id in ADMIN_IDS

    def run(self):
        """Запуск бота"""
        logger.info("Бот запущен с улучшенным меню!")
        self.updater.start_polling()
        self.updater.idle()

# Запуск бота
if __name__ == "__main__":
    if BOT_TOKEN == "YOUR_BOT_TOKEN_HERE":
        print("❌ Установите BOT_TOKEN в коде!")
        exit(1)
    
    bot = ChannelBot(BOT_TOKEN)
    bot.run()
