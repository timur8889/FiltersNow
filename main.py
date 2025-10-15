import logging
import asyncio
import aiohttp
import random
import json
from datetime import datetime, timedelta
from telegram import Bot
from telegram.error import TelegramError
import sqlite3
import os
from typing import Optional, List, Tuple

# Настройка логирования
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO,
    handlers=[
        logging.FileHandler('channel_bot.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

class Config:
    """Конфигурация бота"""
    BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "8278600298:AAGPjUhyU5HxXOaLRvu-FSRldBW_UCmwOME")
    CHANNEL_ID = os.getenv("TELEGRAM_CHANNEL_ID", "@timur_onion")
    
    # Интервалы проверки
    MAIN_LOOP_INTERVAL = 1800  # 30 минут
    CLEANUP_INTERVAL_DAYS = 30
    
    # Таймауты для API
    REQUEST_TIMEOUT = 10
    
    # ID администратора для уведомлений
    ADMIN_ID = os.getenv("ADMIN_ID")

class AutoContentBot:
    def __init__(self):
        self.BOT_TOKEN = Config.BOT_TOKEN
        self.CHANNEL_ID = Config.CHANNEL_ID
        self.ADMIN_ID = Config.ADMIN_ID
        self.bot = Bot(token=self.BOT_TOKEN)
        
        # Инициализация базы данных
        self.init_database()
        
        # Списки контента для резервного использования
        self.quotes = [
            "Успех — это движение от неудачи к неудаче без потери энтузиазма. — Уинстон Черчилль",
            "Единственный способ делать великие дела — любить то, что ты делаешь. — Стив Джобс",
            "Ваше время ограничено, не тратьте его, живя чужой жизнью. — Стив Джобс",
            "Сложнее всего начать действовать, все остальное зависит только от упорства. — Амелия Эрхарт",
            "Лучший способ предсказать будущее — создать его. — Абрахам Линкольн"
        ]
        
        self.facts = [
            "🐝 Медоносные пчелы могут распознавать человеческие лица!",
            "🌌 Млечный Путь столкнется с галактикой Андромеды через 4 миллиарда лет",
            "🧠 Человеческий мозг генерирует около 23 ватт энергии — этого достаточно для питания лампочки",
            "📚 Самый длинный роман в мире — «В поисках утраченного времени» Марселя Пруста (1.2 млн слов)",
            "🐜 Муравьи никогда не спят!"
        ]
        
        self.tips = [
            "💡 Совет: Начинайте день с самого сложного задания — это повысит продуктивность!",
            "💡 Совет: Регулярные перерывы улучшают концентрацию и креативность",
            "💡 Совет: Читайте вслух для улучшения запоминания информации",
            "💡 Совет: Пейте воду перед едой для улучшения метаболизма",
            "💡 Совет: 20 минут на свежем воздухе в день улучшают настроение и сон"
        ]

    def init_database(self):
        """Инициализация базы данных для отслеживания опубликованного контента"""
        self.conn = sqlite3.connect('content.db', check_same_thread=False)
        cursor = self.conn.cursor()
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS published_content (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                content_type TEXT NOT NULL,
                content TEXT NOT NULL,
                publish_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        # Таблица для статистики
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS bot_stats (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                posts_today INTEGER DEFAULT 0,
                last_reset TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        # Инициализируем статистику если нужно
        cursor.execute("SELECT COUNT(*) FROM bot_stats")
        if cursor.fetchone()[0] == 0:
            cursor.execute("INSERT INTO bot_stats (posts_today) VALUES (0)")
            
        self.conn.commit()

    def is_content_used(self, content):
        """Проверяет, использовался ли уже этот контент"""
        cursor = self.conn.cursor()
        cursor.execute(
            "SELECT COUNT(*) FROM published_content WHERE content = ?", 
            (content,)
        )
        return cursor.fetchone()[0] > 0

    def mark_content_used(self, content_type, content):
        """Помечает контент как использованный"""
        cursor = self.conn.cursor()
        cursor.execute(
            "INSERT INTO published_content (content_type, content) VALUES (?, ?)",
            (content_type, content)
        )
        
        # Обновляем статистику
        cursor.execute("UPDATE bot_stats SET posts_today = posts_today + 1")
        self.conn.commit()

    async def get_random_quote(self):
        """Получает случайную цитату из внешнего API"""
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get('https://api.quotable.io/random', timeout=Config.REQUEST_TIMEOUT) as response:
                    if response.status == 200:
                        data = await response.json()
                        quote = f"\"{data['content']}\" — {data['author']}"
                        if not self.is_content_used(quote):
                            return quote
                    else:
                        logger.warning(f"API цитат вернуло статус {response.status}")
        except asyncio.TimeoutError:
            logger.warning("Таймаут при получении цитаты")
        except Exception as e:
            logger.warning(f"Не удалось получить цитату из API: {e}")
        
        # Резервный вариант
        unused_quotes = [q for q in self.quotes if not self.is_content_used(q)]
        return random.choice(unused_quotes) if unused_quotes else random.choice(self.quotes)

    async def get_random_fact(self):
        """Получает случайный факт"""
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get('https://uselessfacts.jsph.pl/random.json?language=en', timeout=Config.REQUEST_TIMEOUT) as response:
                    if response.status == 200:
                        data = await response.json()
                        fact = f"🤔 Интересный факт:\n\n{data['text']}"
                        if not self.is_content_used(fact):
                            return fact
                    else:
                        logger.warning(f"API фактов вернуло статус {response.status}")
        except asyncio.TimeoutError:
            logger.warning("Таймаут при получении факта")
        except Exception as e:
            logger.warning(f"Не удалось получить факт из API: {e}")
        
        # Резервный вариант
        unused_facts = [f for f in self.facts if not self.is_content_used(f)]
        return random.choice(unused_facts) if unused_facts else random.choice(self.facts)

    async def get_news_summary(self):
        """Получает краткие новости (заглушка - можно подключить News API)"""
        news_items = [
            "📰 Сегодняшний обзор: Технологии продолжают менять наш мир!",
            "🌍 Актуально: Ученые делают новые открытия каждый день",
            "🚀 Новости науки: Исследования показывают интересные результаты",
            "💼 Бизнес-новости: Инновации двигают экономику вперед"
        ]
        unused_news = [n for n in news_items if not self.is_content_used(n)]
        return random.choice(unused_news) if unused_news else random.choice(news_items)

    async def send_message_to_channel(self, message, message_type="text"):
        """Отправляет сообщение в канал"""
        try:
            if message_type == "text":
                await self.bot.send_message(
                    chat_id=self.CHANNEL_ID,
                    text=message,
                    parse_mode='HTML'
                )
            
            self.mark_content_used("auto_post", message)
            logger.info(f"Сообщение отправлено в канал: {message[:50]}...")
            
            # Отправляем уведомление администратору
            await self.notify_admin(f"✅ Опубликован новый пост в канале")
            
            return True
            
        except TelegramError as e:
            logger.error(f"Ошибка отправки в канал: {e}")
            await self.notify_admin(f"❌ Ошибка отправки в канал: {e}")
            return False

    async def notify_admin(self, message: str):
        """Отправляет уведомление администратору"""
        if self.ADMIN_ID:
            try:
                await self.bot.send_message(
                    chat_id=self.ADMIN_ID,
                    text=message
                )
            except Exception as e:
                logger.error(f"Не удалось отправить уведомление администратору: {e}")

    async def get_daily_content_schedule(self):
        """Возвращает расписание контента на день"""
        now = datetime.now()
        weekday = now.weekday()
        
        # Разное расписание для разных дней недели
        schedules = {
            0: [  # Понедельник
                (8, "quote", "💭 Мотивационная цитата на неделю!"),
                (12, "fact", "🤔 Знаете ли вы?"),
                (18, "tip", "💡 Совет дня")
            ],
            1: [  # Вторник
                (9, "fact", "🔍 Интересный факт!"),
                (14, "news", "📰 Краткие новости"),
                (19, "quote", "💭 Цитата вечера")
            ],
            2: [  # Среда
                (10, "tip", "💡 Полезный совет"),
                (15, "fact", "🎯 Удивительный факт"),
                (20, "quote", "💭 Мудрая мысль")
            ],
            3: [  # Четверг
                (8, "news", "🌍 Что нового в мире?"),
                (13, "quote", "💭 Цитата для вдохновения"),
                (17, "tip", "💡 Совет по продуктивности")
            ],
            4: [  # Пятница
                (11, "fact", "🤯 Факт на выходные"),
                (16, "quote", "💭 Цитата завершения недели"),
                (21, "tip", "💡 Совет для отдыха")
            ],
            5: [  # Суббота
                (10, "quote", "💭 Цитата выходного дня"),
                (15, "fact", "🎪 Занимательный факт")
            ],
            6: [  # Воскресенье
                (11, "tip", "💡 Совет на новую неделю"),
                (17, "quote", "💭 Воскресная мудрость")
            ]
        }
        
        return schedules.get(weekday, [])

    async def generate_content(self, content_type, theme=""):
        """Генерирует контент по типу"""
        if content_type == "quote":
            content = await self.get_random_quote()
            return f"💭 {theme}\n\n{content}"
        
        elif content_type == "fact":
            content = await self.get_random_fact()
            return f"🎯 {theme}\n\n{content}"
        
        elif content_type == "tip":
            unused_tips = [t for t in self.tips if not self.is_content_used(t)]
            content = random.choice(unused_tips) if unused_tips else random.choice(self.tips)
            return f"💡 {theme}\n\n{content}"
        
        elif content_type == "news":
            content = await self.get_news_summary()
            return f"📰 {theme}\n\n{content}\n\n#новости #обзор"
        
        return None

    async def post_scheduled_content(self):
        """Публикует запланированный контент"""
        try:
            schedule = await self.get_daily_content_schedule()
            now = datetime.now()
            
            for hour, content_type, theme in schedule:
                # Создаем datetime для запланированного времени
                scheduled_time = now.replace(hour=hour, minute=0, second=0, microsecond=0)
                
                # Если время уже прошло сегодня, планируем на следующий день
                if scheduled_time < now:
                    scheduled_time += timedelta(days=1)
                
                # Вычисляем задержку до времени публикации
                delay = (scheduled_time - now).total_seconds()
                
                if delay > 0:
                    logger.info(f"Запланирована публикация {content_type} в {hour}:00")
                    
                    # Запускаем отложенную задачу
                    asyncio.create_task(
                        self.delayed_post(delay, content_type, theme)
                    )
                    
        except Exception as e:
            logger.error(f"Ошибка в планировщике: {e}")
            await self.notify_admin(f"❌ Ошибка в планировщике: {e}")

    async def delayed_post(self, delay, content_type, theme):
        """Отложенная публикация"""
        try:
            await asyncio.sleep(delay)
            content = await self.generate_content(content_type, theme)
            if content:
                await self.send_message_to_channel(content)
        except Exception as e:
            logger.error(f"Ошибка в отложенной публикации: {e}")
            await self.notify_admin(f"❌ Ошибка в отложенной публикации: {e}")

    async def cleanup_old_content(self):
        """Очищает старые записи из базы данных"""
        try:
            cursor = self.conn.cursor()
            cursor.execute(
                "DELETE FROM published_content WHERE publish_date < datetime('now', '-30 days')"
            )
            deleted_count = cursor.rowcount
            
            # Сбрасываем ежедневную статистику если прошли сутки
            cursor.execute("""
                UPDATE bot_stats 
                SET posts_today = 0, last_reset = CURRENT_TIMESTAMP 
                WHERE last_reset < datetime('now', '-1 day')
            """)
            
            self.conn.commit()
            logger.info(f"Старые записи очищены: удалено {deleted_count} записей")
            
        except Exception as e:
            logger.error(f"Ошибка очистки БД: {e}")

    async def get_bot_stats(self):
        """Получает статистику бота"""
        cursor = self.conn.cursor()
        
        # Общее количество постов
        cursor.execute("SELECT COUNT(*) FROM published_content")
        total_posts = cursor.fetchone()[0]
        
        # Посты за сегодня
        cursor.execute("SELECT posts_today FROM bot_stats")
        posts_today = cursor.fetchone()[0]
        
        # Распределение по типам контента
        cursor.execute("""
            SELECT content_type, COUNT(*) 
            FROM published_content 
            GROUP BY content_type
        """)
        content_stats = cursor.fetchall()
        
        return {
            "total_posts": total_posts,
            "posts_today": posts_today,
            "content_stats": dict(content_stats)
        }

    async def send_stats_to_admin(self):
        """Отправляет статистику администратору"""
        try:
            stats = await self.get_bot_stats()
            
            stats_message = (
                "📊 <b>Статистика бота</b>\n\n"
                f"📈 Всего публикаций: <b>{stats['total_posts']}</b>\n"
                f"📅 Публикаций сегодня: <b>{stats['posts_today']}</b>\n\n"
                "<b>Распределение по типам:</b>\n"
            )
            
            for content_type, count in stats["content_stats"].items():
                stats_message += f"• {content_type}: {count}\n"
            
            await self.bot.send_message(
                chat_id=self.ADMIN_ID,
                text=stats_message,
                parse_mode='HTML'
            )
            
        except Exception as e:
            logger.error(f"Ошибка отправки статистики: {e}")

    async def health_check(self):
        """Проверка здоровья бота"""
        try:
            # Проверяем соединение с Telegram - используем простой вызов без await
            me = await self.bot.get_me()
            logger.info(f"Бот подключен: @{me.username}")
            
            # Проверяем базу данных
            cursor = self.conn.cursor()
            cursor.execute("SELECT 1")
            
            # Проверяем доступность канала
            try:
                chat = await self.bot.get_chat(self.CHANNEL_ID)
                logger.info(f"Канал доступен: {chat.title}")
            except TelegramError as e:
                logger.error(f"Канал недоступен: {e}")
                return False
            
            logger.info("✅ Бот здоров - все системы работают")
            return True
            
        except Exception as e:
            logger.error(f"❌ Проблема со здоровьем бота: {e}")
            if self.ADMIN_ID:
                try:
                    await self.bot.send_message(
                        chat_id=self.ADMIN_ID,
                        text=f"🚨 Проблема со здоровьем бота: {e}"
                    )
                except:
                    pass  # Если не можем отправить уведомление, просто логируем
            return False

    async def manual_post(self, content_type: str = None):
        """Ручная публикация поста"""
        try:
            if not content_type:
                content_type = random.choice(["quote", "fact", "tip", "news"])
            
            content_types_map = {
                "quote": ("💭 Случайная цитата", "quote"),
                "fact": ("🤔 Случайный факт", "fact"),
                "tip": ("💡 Случайный совет", "tip"),
                "news": ("📰 Новости", "news")
            }
            
            theme, actual_type = content_types_map.get(content_type, ("📝 Случайный пост", "quote"))
            content = await self.generate_content(actual_type, theme)
            
            if content:
                success = await self.send_message_to_channel(content)
                if success:
                    await self.notify_admin(f"✅ Ручная публикация успешна: {content_type}")
                    return True
            
            return False
            
        except Exception as e:
            logger.error(f"Ошибка ручной публикации: {e}")
            await self.notify_admin(f"❌ Ошибка ручной публикации: {e}")
            return False

    async def run(self):
        """Основной цикл бота"""
        logger.info("🤖 Автоматический бот для канала запущен!")
        
        # Проверка здоровья при запуске
        health_ok = await self.health_check()
        if not health_ok:
            logger.error("Бот не прошел проверку здоровья при запуске")
            # Не прерываем выполнение, но логируем ошибку
            if self.ADMIN_ID:
                await self.notify_admin("⚠️ Бот запущен с проблемами здоровья")
        
        try:
            # Первая публикация при запуске
            welcome_message = (
                "🎉 Бот запущен! Автоматические публикации активированы.\n\n"
                "📅 Контент будет публиковаться по расписанию:\n"
                "• Утром - мотивационные цитаты\n"
                "• Днем - интересные факты и новости\n"
                "• Вечером - полезные советы\n\n"
                "Оставайтесь на связи! ✨"
            )
            await self.send_message_to_channel(welcome_message)
        except Exception as e:
            logger.error(f"Не удалось отправить приветственное сообщение: {e}")
        
        # Отправляем статистику администратору
        if self.ADMIN_ID:
            try:
                await self.send_stats_to_admin()
            except Exception as e:
                logger.error(f"Не удалось отправить статистику: {e}")
        
        # Запускаем планировщик
        await self.post_scheduled_content()
        
        # Основной цикл
        while True:
            try:
                current_time = datetime.now()
                current_hour = current_time.hour
                
                # Каждый день в 6 утра обновляем расписание
                if current_hour == 6:
                    await self.post_scheduled_content()
                    await self.cleanup_old_content()
                    await asyncio.sleep(3600)  # Ждем 1 час
                
                # Каждый день в 9 утра отправляем статистику
                elif current_hour == 9 and self.ADMIN_ID:
                    await self.send_stats_to_admin()
                    await asyncio.sleep(3600)
                
                # Каждое воскресенье в 23:00 делаем итоги недели
                elif current_time.weekday() == 6 and current_hour == 23:
                    weekly_summary = (
                        "📊 Итоги недели!\n\n"
                        "Спасибо, что остаетесь с нами! 🙏\n"
                        "На следующей неделе - еще больше интересного контента!\n\n"
                        "Хороших выходных! 😊"
                    )
                    await self.send_message_to_channel(weekly_summary)
                    await asyncio.sleep(3600)
                
                # Ежечасная проверка здоровья (раз в 6 часов)
                elif current_hour % 6 == 0:  # Каждые 6 часов
                    await self.health_check()
                    await asyncio.sleep(3600)
                
                else:
                    await asyncio.sleep(Config.MAIN_LOOP_INTERVAL)  # Проверяем каждые 30 минут
                    
            except Exception as e:
                logger.error(f"Ошибка в основном цикле: {e}")
                if self.ADMIN_ID:
                    await self.notify_admin(f"❌ Ошибка в основном цикле: {e}")
                await asyncio.sleep(300)  # Ждем 5 минут при ошибке

async def main():
    """Точка входа"""
    bot = AutoContentBot()
    
    try:
        await bot.run()
    except KeyboardInterrupt:
        logger.info("Бот остановлен пользователем")
        if hasattr(bot, 'ADMIN_ID') and bot.ADMIN_ID:
            await bot.notify_admin("⏹️ Бот остановлен пользователем")
    except Exception as e:
        logger.error(f"Критическая ошибка: {e}")
        if hasattr(bot, 'ADMIN_ID') and bot.ADMIN_ID:
            await bot.notify_admin(f"🚨 Критическая ошибка: {e}")
    finally:
        if hasattr(bot, 'conn'):
            bot.conn.close()

if __name__ == "__main__":
    # Запуск бота
    asyncio.run(main())
