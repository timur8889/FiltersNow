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

class AutoContentBot:
    def __init__(self):
        self.BOT_TOKEN = "8278600298:AAGPjUhyU5HxXOaLRvu-FSRldBW_UCmwOME"  # Замените на ваш токен
        self.CHANNEL_ID = "@timur_onion"  # Замените на username вашего канала
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
            "💡 Совет Читайте вслух для улучшения запоминания информации",
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
        self.conn.commit()

    async def get_random_quote(self):
        """Получает случайную цитату из внешнего API"""
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get('https://api.quotable.io/random', timeout=10) as response:
                    if response.status == 200:
                        data = await response.json()
                        quote = f"\"{data['content']}\" — {data['author']}"
                        if not self.is_content_used(quote):
                            return quote
        except Exception as e:
            logger.warning(f"Не удалось получить цитату из API: {e}")
        
        # Резервный вариант
        return random.choice([q for q in self.quotes if not self.is_content_used(q)])

    async def get_random_fact(self):
        """Получает случайный факт"""
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get('https://uselessfacts.jsph.pl/random.json?language=en', timeout=10) as response:
                    if response.status == 200:
                        data = await response.json()
                        fact = f"🤔 Интересный факт:\n\n{data['text']}"
                        if not self.is_content_used(fact):
                            return fact
        except Exception as e:
            logger.warning(f"Не удалось получить факт из API: {e}")
        
        # Резервный вариант
        return random.choice([f for f in self.facts if not self.is_content_used(f)])

    async def get_news_summary(self):
        """Получает краткие новости (заглушка - можно подключить News API)"""
        news_items = [
            "📰 Сегодняшний обзор: Технологии продолжают менять наш мир!",
            "🌍 Актуально: Ученые делают новые открытия каждый день",
            "🚀 Новости науки: Исследования показывают интересные результаты",
            "💼 Бизнес-новости: Инновации двигают экономику вперед"
        ]
        return random.choice(news_items)

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
            return True
            
        except TelegramError as e:
            logger.error(f"Ошибка отправки в канал: {e}")
            return False

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
            content = random.choice([t for t in self.tips if not self.is_content_used(t)])
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

    async def delayed_post(self, delay, content_type, theme):
        """Отложенная публикация"""
        try:
            await asyncio.sleep(delay)
            content = await self.generate_content(content_type, theme)
            if content:
                await self.send_message_to_channel(content)
        except Exception as e:
            logger.error(f"Ошибка в отложенной публикации: {e}")

    async def cleanup_old_content(self):
        """Очищает старые записи из базы данных"""
        try:
            cursor = self.conn.cursor()
            cursor.execute(
                "DELETE FROM published_content WHERE publish_date < datetime('now', '-30 days')"
            )
            self.conn.commit()
            logger.info("Старые записи очищены")
        except Exception as e:
            logger.error(f"Ошибка очистки БД: {e}")

    async def run(self):
        """Основной цикл бота"""
        logger.info("🤖 Автоматический бот для канала запущен!")
        
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
        
        # Запускаем планировщик
        await self.post_scheduled_content()
        
        # Основной цикл
        while True:
            try:
                current_hour = datetime.now().hour
                
                # Каждый день в 6 утра обновляем расписание
                if current_hour == 6:
                    await self.post_scheduled_content()
                    await self.cleanup_old_content()
                    await asyncio.sleep(3600)  # Ждем 1 час
                
                # Каждое воскресенье в 23:00 делаем итоги недели
                elif datetime.now().weekday() == 6 and current_hour == 23:
                    weekly_summary = (
                        "📊 Итоги недели!\n\n"
                        "Спасибо, что остаетесь с нами! 🙏\n"
                        "На следующей неделе - еще больше интересного контента!\n\n"
                        "Хороших выходных! 😊"
                    )
                    await self.send_message_to_channel(weekly_summary)
                    await asyncio.sleep(3600)
                
                else:
                    await asyncio.sleep(1800)  # Проверяем каждые 30 минут
                    
            except Exception as e:
                logger.error(f"Ошибка в основном цикле: {e}")
                await asyncio.sleep(300)  # Ждем 5 минут при ошибке

async def main():
    """Точка входа"""
    bot = AutoContentBot()
    
    try:
        await bot.run()
    except KeyboardInterrupt:
        logger.info("Бот остановлен пользователем")
    except Exception as e:
        logger.error(f"Критическая ошибка: {e}")
    finally:
        if hasattr(bot, 'conn'):
            bot.conn.close()

if __name__ == "__main__":
    # Запуск бота
    asyncio.run(main())
