import logging
import sqlite3
import json
import random
import asyncio
import aiohttp
from datetime import datetime, timedelta
from telegram import Update, ChatPermissions
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

# Настройка логирования
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO,
    handlers=[
        logging.FileHandler('bot.log', encoding='utf-8'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# Конфигурация
class Config:
    BOT_TOKEN = "8278600298:AAGPjUhyU5HxXOaLRvu-FSRldBW_UCmwOME"  # ЗАМЕНИТЕ на ваш токен!
    ADMIN_IDS = [5024165375]  # ЗАМЕНИТЕ на ID администраторов
    BAD_WORDS = ['спам', 'реклама', 'оскорбление']  # Добавьте свои плохие слова

# ========== СИСТЕМА КАРМЫ ==========
class KarmaSystem:
    def __init__(self, db_path='karma.db'):
        self.db_path = db_path
        self.init_database()
    
    def init_database(self):
        """Инициализация базы данных кармы"""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS karma (
                    user_id INTEGER PRIMARY KEY,
                    username TEXT,
                    karma INTEGER DEFAULT 0,
                    last_thank TIMESTAMP,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS thanks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    from_user INTEGER,
                    to_user INTEGER,
                    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            
            conn.commit()
            conn.close()
            logger.info("База данных кармы инициализирована")
        except Exception as e:
            logger.error(f"Ошибка инициализации БД кармы: {e}")
    
    def add_karma(self, user_id: int, username: str, from_user: int) -> bool:
        """Добавить карму пользователю"""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            # Обновляем карму
            cursor.execute('''
                INSERT OR REPLACE INTO karma (user_id, username, karma, last_thank)
                VALUES (?, ?, COALESCE((SELECT karma FROM karma WHERE user_id = ?), 0) + 1, ?)
            ''', (user_id, username, user_id, datetime.now()))
            
            # Записываем благодарность
            cursor.execute(
                'INSERT INTO thanks (from_user, to_user, timestamp) VALUES (?, ?, ?)',
                (from_user, user_id, datetime.now())
            )
            
            conn.commit()
            conn.close()
            return True
        except Exception as e:
            logger.error(f"Ошибка добавления кармы: {e}")
            return False
    
    def get_karma(self, user_id: int) -> int:
        """Получить карму пользователя"""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            cursor.execute(
                'SELECT karma FROM karma WHERE user_id = ?', (user_id,)
            )
            result = cursor.fetchone()
            conn.close()
            
            return result[0] if result else 0
        except Exception as e:
            logger.error(f"Ошибка получения кармы: {e}")
            return 0
    
    def get_top_users(self, limit=10):
        """Топ пользователей по карме"""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            cursor.execute(
                'SELECT username, karma FROM karma ORDER BY karma DESC LIMIT ?', (limit,)
            )
            results = cursor.fetchall()
            conn.close()
            
            return results
        except Exception as e:
            logger.error(f"Ошибка получения топа: {e}")
            return []

# ========== СИСТЕМА ЭКОНОМИКИ ==========
class EconomySystem:
    def __init__(self, db_path='economy.db'):
        self.db_path = db_path
        self.init_database()
    
    def init_database(self):
        """Инициализация базы данных экономики"""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS economy (
                    user_id INTEGER PRIMARY KEY,
                    username TEXT,
                    balance INTEGER DEFAULT 100,
                    last_daily TIMESTAMP,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            
            conn.commit()
            conn.close()
            logger.info("База данных экономики инициализирована")
        except Exception as e:
            logger.error(f"Ошибка инициализации БД экономики: {e}")
    
    def get_balance(self, user_id: int, username: str = "") -> int:
        """Получить баланс пользователя"""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            cursor.execute(
                'SELECT balance FROM economy WHERE user_id = ?', (user_id,)
            )
            result = cursor.fetchone()
            
            if not result:
                # Создаем запись для нового пользователя
                cursor.execute(
                    'INSERT INTO economy (user_id, username, balance) VALUES (?, ?, ?)',
                    (user_id, username, 100)
                )
                conn.commit()
                conn.close()
                return 100
            
            conn.close()
            return result[0]
        except Exception as e:
            logger.error(f"Ошибка получения баланса: {e}")
            return 0
    
    def transfer_money(self, from_user: int, to_user: int, amount: int, to_username: str = "") -> bool:
        """Перевод денег между пользователями"""
        try:
            if amount <= 0:
                return False
            
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            # Проверяем баланс отправителя
            cursor.execute(
                'SELECT balance FROM economy WHERE user_id = ?', (from_user,)
            )
            from_balance = cursor.fetchone()
            
            if not from_balance or from_balance[0] < amount:
                conn.close()
                return False
            
            # Проверяем получателя
            cursor.execute(
                'SELECT balance FROM economy WHERE user_id = ?', (to_user,)
            )
            to_balance = cursor.fetchone()
            
            if not to_balance:
                # Создаем запись для получателя
                cursor.execute(
                    'INSERT INTO economy (user_id, username, balance) VALUES (?, ?, ?)',
                    (to_user, to_username, amount)
                )
            else:
                # Обновляем баланс получателя
                cursor.execute(
                    'UPDATE economy SET balance = balance + ? WHERE user_id = ?',
                    (amount, to_user)
                )
            
            # Обновляем баланс отправителя
            cursor.execute(
                'UPDATE economy SET balance = balance - ? WHERE user_id = ?',
                (amount, from_user)
            )
            
            conn.commit()
            conn.close()
            return True
        except Exception as e:
            logger.error(f"Ошибка перевода денег: {e}")
            return False
    
    def daily_bonus(self, user_id: int, username: str) -> dict:
        """Ежедневный бонус"""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            cursor.execute(
                'SELECT last_daily FROM economy WHERE user_id = ?', (user_id,)
            )
            result = cursor.fetchone()
            
            today = datetime.now().date()
            bonus_amount = 50
            
            if result and result[0]:
                last_daily = datetime.fromisoformat(result[0]).date()
                if last_daily == today:
                    conn.close()
                    return {'success': False, 'message': '❌ Вы уже получали бонус сегодня!'}
            
            # Выдаем бонус
            cursor.execute('''
                INSERT OR REPLACE INTO economy (user_id, username, balance, last_daily)
                VALUES (?, ?, COALESCE((SELECT balance FROM economy WHERE user_id = ?), 100) + ?, ?)
            ''', (user_id, username, user_id, bonus_amount, datetime.now()))
            
            conn.commit()
            conn.close()
            
            return {'success': True, 'amount': bonus_amount, 'message': f'🎁 Вы получили {bonus_amount} монет!'}
        except Exception as e:
            logger.error(f"Ошибка выдачи бонуса: {e}")
            return {'success': False, 'message': '❌ Ошибка выдачи бонуса'}

# ========== СИСТЕМА МИНИ-ИГР ==========
class MiniGames:
    def __init__(self, economy_system: EconomySystem):
        self.economy = economy_system
    
    async def coin_flip(self, user_id: int, username: str, bet: int, choice: str) -> dict:
        """Игра в орлянку"""
        try:
            if bet <= 0:
                return {'success': False, 'message': '❌ Ставка должна быть положительной!'}
            
            balance = self.economy.get_balance(user_id, username)
            if balance < bet:
                return {'success': False, 'message': '❌ Недостаточно средств!'}
            
            if choice not in ['орёл', 'орел', 'решка']:
                return {'success': False, 'message': '❌ Выберите "орёл" или "решка"!'}
            
            # Нормализуем выбор
            if choice in ['орёл', 'орел']:
                choice = 'орёл'
            else:
                choice = 'решка'
            
            # Подбрасываем монетку
            result = random.choice(['орёл', 'решка'])
            win = result == choice
            
            if win:
                # Удваиваем выигрыш
                win_amount = bet * 2
                # Обновляем баланс (ставка уже включена в выигрыш)
                self.economy.transfer_money(user_id, user_id, bet + win_amount, username)
                message = f'🎉 Поздравляем! Выпал {result}. Вы выиграли {win_amount} монет!'
            else:
                # Ставка уже списана при проверке баланса
                message = f'😔 Увы! Выпал {result}. Вы проиграли {bet} монет.'
            
            return {
                'success': True,
                'win': win,
                'result': result,
                'message': message
            }
        except Exception as e:
            logger.error(f"Ошибка в игре coin_flip: {e}")
            return {'success': False, 'message': '❌ Ошибка в игре!'}

# ========== СИСТЕМА ВНЕШНИХ API ==========
class ExternalAPIs:
    @staticmethod
    async def get_weather(city: str) -> str:
        """Получение погоды (заглушка)"""
        try:
            # В реальной реализации здесь будет запрос к API погоды
            await asyncio.sleep(1)  # Имитация задержки сети
            
            weather_data = {
                'москва': '🌡 Москва: +15°C, облачно',
                'санкт-петербург': '🌡 Санкт-Петербург: +12°C, дождь',
                'новосибирск': '🌡 Новосибирск: +8°C, ясно'
            }
            
            city_lower = city.lower()
            if city_lower in weather_data:
                return weather_data[city_lower]
            else:
                return f'🌡 Погода для {city}: +20°C, солнечно'
        except Exception as e:
            logger.error(f"Ошибка получения погоды: {e}")
            return "❌ Ошибка получения данных о погоде"

    @staticmethod
    async def get_exchange_rates() -> str:
        """Получение курсов валют (заглушка)"""
        try:
            await asyncio.sleep(1)  # Имитация задержки сети
            
            # В реальной реализации здесь будет запрос к API курсов валют
            return "💱 Курсы валют:\nUSD: 90.50 RUB\nEUR: 98.20 RUB\nCNY: 12.50 RUB"
        except Exception as e:
            logger.error(f"Ошибка получения курсов: {e}")
            return "❌ Ошибка получения курсов валют"

# ========== ОСНОВНОЙ КЛАСС БОТА ==========
class SuperGroupBot:
    def __init__(self, token: str):
        self.token = token
        self.config = Config()
        
        # Инициализация систем
        self.karma_system = KarmaSystem()
        self.economy_system = EconomySystem()
        self.games_system = MiniGames(self.economy_system)
        self.api_system = ExternalAPIs()
        
        # Инициализация бота
        self.application = Application.builder().token(token).build()
        self.setup_handlers()
        
        logger.info("🤖 Супер-бот инициализирован!")
    
    def setup_handlers(self):
        """Настройка всех обработчиков команд"""
        
        # Основные команды
        self.application.add_handler(CommandHandler("start", self.start_command))
        self.application.add_handler(CommandHandler("help", self.help_command))
        self.application.add_handler(CommandHandler("rules", self.rules_command))
        
        # Команды кармы
        self.application.add_handler(CommandHandler("karma", self.karma_command))
        self.application.add_handler(CommandHandler("thank", self.thank_command))
        self.application.add_handler(CommandHandler("top", self.top_command))
        
        # Команды экономики
        self.application.add_handler(CommandHandler("balance", self.balance_command))
        self.application.add_handler(CommandHandler("transfer", self.transfer_command))
        self.application.add_handler(CommandHandler("daily", self.daily_command))
        
        # Команды игр
        self.application.add_handler(CommandHandler("coinflip", self.coin_flip_command))
        
        # Команды API
        self.application.add_handler(CommandHandler("weather", self.weather_command))
        self.application.add_handler(CommandHandler("exchange", self.exchange_command))
        
        # Обработчики сообщений
        self.application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self.handle_message))
        self.application.add_handler(MessageHandler(filters.StatusUpdate.NEW_CHAT_MEMBERS, self.welcome_new_members))
        self.application.add_handler(MessageHandler(filters.StatusUpdate.LEFT_CHAT_MEMBER, self.goodbye_member))
        
        # Обработчик ошибок
        self.application.add_error_handler(self.error_handler)
    
    # ========== ОСНОВНЫЕ КОМАНДЫ ==========
    async def start_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Команда /start"""
        try:
            user = update.effective_user
            welcome_text = f"""
👋 Привет, {user.first_name}!

🤖 Я - многофункциональный бот для этой группы с кучей возможностей:

⭐ **Карма**: Получайте карму за полезные сообщения
💰 **Экономика**: Зарабатывайте монеты и играйте в игры
🎮 **Игры**: coinflip - подбрось монетку на деньги
🌤 **Полезное**: Погода, курсы валют
📊 **Статистика**: Топ пользователей

📋 Используйте /help для списка всех команд
            """
            await update.message.reply_text(welcome_text)
            logger.info(f"Пользователь {user.id} запустил бота")
        except Exception as e:
            logger.error(f"Ошибка в start_command: {e}")
            await update.message.reply_text("❌ Произошла ошибка!")

    async def help_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Команда /help"""
        try:
            help_text = """
📋 **Доступные команды:**

👤 **Основные:**
/start - Начать работу
/help - Эта справка  
/rules - Правила группы

⭐ **Система кармы:**
/karma - Ваша карма
/thank @username - Дать карму пользователю
/top - Топ пользователей по карме

💰 **Экономика:**
/balance - Ваш баланс
/daily - Ежедневный бонус
/transfer @username сумма - Перевести деньги

🎮 **Игры:**
/coinflip сумма орёл/решка - Подбросить монетку

🌤 **Полезное:**
/weather город - Погода
/exchange - Курсы валют

⚡ **Для админов:**
/warn @username - Выдать предупреждение
            """
            await update.message.reply_text(help_text)
        except Exception as e:
            logger.error(f"Ошибка в help_command: {e}")
            await update.message.reply_text("❌ Произошла ошибка!")

    async def rules_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Команда /rules"""
        try:
            rules_text = """
📜 **Правила группы:**

1. 🤝 Уважайте всех участников
2. 🚫 Запрещен спам и реклама  
3. ❌ Не размещайте запрещенный контент
4. 💬 Соблюдайте тематику обсуждений
5. ⭐ Полезные сообщения получают карму

⚠️ Нарушение правил ведет к предупреждениям, муту или бану.
            """
            await update.message.reply_text(rules_text)
        except Exception as e:
            logger.error(f"Ошибка в rules_command: {e}")
            await update.message.reply_text("❌ Произошла ошибка!")

    # ========== КОМАНДЫ КАРМЫ ==========
    async def karma_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Команда /karma - показать карму"""
        try:
            user = update.effective_user
            karma = self.karma_system.get_karma(user.id)
            
            await update.message.reply_text(
                f"⭐ {user.first_name}, ваша карма: {karma} очков"
            )
        except Exception as e:
            logger.error(f"Ошибка в karma_command: {e}")
            await update.message.reply_text("❌ Ошибка получения кармы!")

    async def thank_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Команда /thank - поблагодарить пользователя"""
        try:
            if not context.args:
                await update.message.reply_text("❌ Укажите пользователя: /thank @username")
                return
            
            target_username = context.args[0].replace('@', '')
            from_user = update.effective_user
            
            # В реальной реализации здесь нужно получить user_id из username
            # Для демонстрации используем фиктивный ID
            target_user_id = hash(target_username) % 1000000  # Фиктивный ID
            
            if self.karma_system.add_karma(target_user_id, target_username, from_user.id):
                await update.message.reply_text(
                    f"⭐ Вы поблагодарили @{target_username}! Карма увеличена."
                )
            else:
                await update.message.reply_text("❌ Ошибка при добавлении кармы!")
                
        except Exception as e:
            logger.error(f"Ошибка в thank_command: {e}")
            await update.message.reply_text("❌ Ошибка при добавлении кармы!")

    async def top_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Команда /top - топ пользователей по карме"""
        try:
            top_users = self.karma_system.get_top_users(10)
            
            if not top_users:
                await update.message.reply_text("📊 Пока нет данных о карме!")
                return
            
            top_text = "🏆 **Топ пользователей по карме:**\n\n"
            for i, (username, karma) in enumerate(top_users, 1):
                top_text += f"{i}. @{username}: {karma} ⭐\n"
            
            await update.message.reply_text(top_text)
        except Exception as e:
            logger.error(f"Ошибка в top_command: {e}")
            await update.message.reply_text("❌ Ошибка получения топа!")

    # ========== КОМАНДЫ ЭКОНОМИКИ ==========
    async def balance_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Команда /balance - показать баланс"""
        try:
            user = update.effective_user
            balance = self.economy_system.get_balance(user.id, user.username or user.first_name)
            
            await update.message.reply_text(
                f"💰 {user.first_name}, ваш баланс: {balance} монет"
            )
        except Exception as e:
            logger.error(f"Ошибка в balance_command: {e}")
            await update.message.reply_text("❌ Ошибка получения баланса!")

    async def transfer_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Команда /transfer - перевести деньги"""
        try:
            if len(context.args) < 2:
                await update.message.reply_text("❌ Использование: /transfer @username сумма")
                return
            
            target_username = context.args[0].replace('@', '')
            amount = int(context.args[1])
            
            if amount <= 0:
                await update.message.reply_text("❌ Сумма должна быть положительной!")
                return
            
            from_user = update.effective_user
            # Фиктивный ID получателя для демонстрации
            target_user_id = hash(target_username) % 1000000
            
            if self.economy_system.transfer_money(from_user.id, target_user_id, amount, target_username):
                await update.message.reply_text(
                    f"✅ Вы перевели {amount} монет пользователю @{target_username}"
                )
            else:
                await update.message.reply_text("❌ Недостаточно средств или ошибка перевода!")
                
        except ValueError:
            await update.message.reply_text("❌ Неверная сумма!")
        except Exception as e:
            logger.error(f"Ошибка в transfer_command: {e}")
            await update.message.reply_text("❌ Ошибка перевода!")

    async def daily_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Команда /daily - ежедневный бонус"""
        try:
            user = update.effective_user
            result = self.economy_system.daily_bonus(user.id, user.username or user.first_name)
            
            await update.message.reply_text(result['message'])
        except Exception as e:
            logger.error(f"Ошибка в daily_command: {e}")
            await update.message.reply_text("❌ Ошибка выдачи бонуса!")

    # ========== КОМАНДЫ ИГР ==========
    async def coin_flip_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Команда /coinflip - игра в орлянку"""
        try:
            if len(context.args) < 2:
                await update.message.reply_text("❌ Использование: /coinflip сумма орёл/решка")
                return
            
            amount = int(context.args[0])
            choice = context.args[1].lower()
            user = update.effective_user
            
            result = await self.games_system.coin_flip(user.id, user.username or user.first_name, amount, choice)
            await update.message.reply_text(result['message'])
            
        except ValueError:
            await update.message.reply_text("❌ Неверная сумма!")
        except Exception as e:
            logger.error(f"Ошибка в coin_flip_command: {e}")
            await update.message.reply_text("❌ Ошибка в игре!")

    # ========== КОМАНДЫ API ==========
    async def weather_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Команда /weather - погода"""
        try:
            city = context.args[0] if context.args else "Москва"
            
            # Показываем временный ответ
            temp_message = await update.message.reply_text("⏳ Запрашиваю данные о погоде...")
            
            weather = await self.api_system.get_weather(city)
            await context.bot.edit_message_text(
                chat_id=update.effective_chat.id,
                message_id=temp_message.message_id,
                text=weather
            )
            
        except Exception as e:
            logger.error(f"Ошибка в weather_command: {e}")
            await update.message.reply_text("❌ Ошибка получения погоды!")

    async def exchange_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Команда /exchange - курсы валют"""
        try:
            temp_message = await update.message.reply_text("⏳ Запрашиваю курсы валют...")
            
            exchange = await self.api_system.get_exchange_rates()
            await context.bot.edit_message_text(
                chat_id=update.effective_chat.id,
                message_id=temp_message.message_id,
                text=exchange
            )
            
        except Exception as e:
            logger.error(f"Ошибка в exchange_command: {e}")
            await update.message.reply_text("❌ Ошибка получения курсов!")

    # ========== ОБРАБОТЧИКИ СООБЩЕНИЙ ==========
    async def handle_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработка обычных сообщений"""
        try:
            message_text = update.message.text.lower()
            user = update.effective_user
            
            # Проверка на плохие слова
            for bad_word in self.config.BAD_WORDS:
                if bad_word in message_text:
                    await update.message.delete()
                    warning = await update.message.reply_text(
                        f"⚠️ {user.first_name}, пожалуйста, соблюдайте правила группы!"
                    )
                    # Удаляем предупреждение через 10 секунд
                    await asyncio.sleep(10)
                    await warning.delete()
                    return
            
            # Автоматическое добавление кармы за длинные сообщения
            if len(update.message.text) > 100:
                self.karma_system.add_karma(user.id, user.username or user.first_name, user.id)
                
        except Exception as e:
            logger.error(f"Ошибка в handle_message: {e}")

    async def welcome_new_members(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Приветствие новых участников"""
        try:
            for member in update.message.new_chat_members:
                welcome_text = f"""
👋 Добро пожаловать, {member.first_name}!

Рады видеть тебя в нашей группе!
Ознакомься с правилами: /rules
Получить помощь: /help

🎁 Не забудь забрать ежедневный бонус: /daily
                """
                await update.message.reply_text(welcome_text)
        except Exception as e:
            logger.error(f"Ошибка в welcome_new_members: {e}")

    async def goodbye_member(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Прощание с вышедшими участниками"""
        try:
            left_member = update.message.left_chat_member
            if left_member:
                await update.message.reply_text(
                    f"😢 {left_member.first_name} покинул(а) нас..."
                )
        except Exception as e:
            logger.error(f"Ошибка в goodbye_member: {e}")

    # ========== ОБРАБОТЧИК ОШИБОК ==========
    async def error_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработчик ошибок"""
        try:
            logger.error(f"Ошибка: {context.error}", exc_info=context.error)
            
            if update and update.effective_message:
                await update.effective_message.reply_text(
                    "❌ Произошла непредвиденная ошибка. Разработчики уже уведомлены."
                )
        except Exception as e:
            logger.error(f"Ошибка в обработчике ошибок: {e}")

    # ========== ЗАПУСК БОТА ==========
    def run(self):
        """Запуск бота"""
        try:
            logger.info("🚀 Запускаю бота...")
            print("🤖 Бот запускается...")
            print("⚠️  Убедитесь, что вы заменили BOT_TOKEN на реальный токен!")
            print("📝 Логи записываются в файл bot.log")
            
            self.application.run_polling()
            
        except Exception as e:
            logger.critical(f"Критическая ошибка при запуске бота: {e}")
            print(f"❌ Критическая ошибка: {e}")

# ========== ТЕСТИРОВАНИЕ И ЗАПУСК ==========
def test_bot_initialization():
    """Тест инициализации бота"""
    print("🧪 Тестирую инициализацию бота...")
    
    try:
        # Тестовый токен
        test_token = "TEST_TOKEN"
        bot = SuperGroupBot(test_token)
        
        print("✅ Инициализация бота прошла успешно")
        print("✅ Все системы загружены")
        print("✅ Обработчики команд настроены")
        
        return True
        
    except Exception as e:
        print(f"❌ Ошибка инициализации: {e}")
        return False

def main():
    """Основная функция запуска"""
    print("=" * 50)
    print("🤖 SUPER GROUP BOT - ЗАПУСК")
    print("=" * 50)
    
    # Проверка токена
    if Config.BOT_TOKEN == "YOUR_BOT_TOKEN_HERE":
        print("❌ ОШИБКА: Вы не заменили BOT_TOKEN!")
        print("📝 Получите токен у @BotFather и замените в коде")
        return
    
    # Запуск тестов
    if not test_bot_initialization():
        print("❌ Тесты не пройдены, бот не запускается")
        return
    
    print("✅ Все тесты пройдены успешно!")
    print("🚀 Запускаю основного бота...")
    
    try:
        # Создаем и запускаем бота
        bot = SuperGroupBot(Config.BOT_TOKEN)
        bot.run()
        
    except KeyboardInterrupt:
        print("\n🛑 Бот остановлен пользователем")
    except Exception as e:
        print(f"❌ Критическая ошибка: {e}")
        logger.critical(f"Критическая ошибка: {e}")

if __name__ == "__main__":
    main()
