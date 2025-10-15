import logging
import sqlite3
import json
import random
import asyncio
import aiohttp
from datetime import datetime, timedelta
from telegram import Update, ChatPermissions, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Updater, CommandHandler, MessageHandler, Filters, CallbackContext, CallbackQueryHandler

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

# Конфигурация - ВАЖНО: ЗАМЕНИТЕ НА ВАШ РЕАЛЬНЫЙ ТОКЕН!
class Config:
    BOT_TOKEN = "8278600298:AAGPjUhyU5HxXOaLRvu-FSRldBW_UCmwOME"  # ⚠️ ЗАМЕНИТЕ ЭТОТ ТОКЕН!
    ADMIN_IDS = [5024165375]  
    BAD_WORDS = ['спам', 'реклама', 'оскорбление']

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
            
            cursor.execute('''
                INSERT OR REPLACE INTO karma (user_id, username, karma, last_thank)
                VALUES (?, ?, COALESCE((SELECT karma FROM karma WHERE user_id = ?), 0) + 1, ?)
            ''', (user_id, username, user_id, datetime.now()))
            
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
    
    def add_money(self, user_id: int, username: str, amount: int) -> bool:
        """Добавить деньги пользователю"""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            cursor.execute('''
                INSERT OR REPLACE INTO economy (user_id, username, balance)
                VALUES (?, ?, COALESCE((SELECT balance FROM economy WHERE user_id = ?), 100) + ?)
            ''', (user_id, username, user_id, amount))
            
            conn.commit()
            conn.close()
            return True
        except Exception as e:
            logger.error(f"Ошибка добавления денег: {e}")
            return False
    
    def transfer_money(self, from_user: int, to_user: int, amount: int, to_username: str = "") -> bool:
        """Перевод денег между пользователями"""
        try:
            if amount <= 0:
                return False
            
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            cursor.execute(
                'SELECT balance FROM economy WHERE user_id = ?', (from_user,)
            )
            from_balance = cursor.fetchone()
            
            if not from_balance or from_balance[0] < amount:
                conn.close()
                return False
            
            cursor.execute(
                'SELECT balance FROM economy WHERE user_id = ?', (to_user,)
            )
            to_balance = cursor.fetchone()
            
            if not to_balance:
                cursor.execute(
                    'INSERT INTO economy (user_id, username, balance) VALUES (?, ?, ?)',
                    (to_user, to_username, amount)
                )
            else:
                cursor.execute(
                    'UPDATE economy SET balance = balance + ? WHERE user_id = ?',
                    (amount, to_user)
                )
            
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
    
    def coin_flip(self, user_id: int, username: str, bet: int, choice: str) -> dict:
        """Игра в орлянку"""
        try:
            if bet <= 0:
                return {'success': False, 'message': '❌ Ставка должна быть положительной!'}
            
            balance = self.economy.get_balance(user_id, username)
            if balance < bet:
                return {'success': False, 'message': '❌ Недостаточно средств!'}
            
            if choice not in ['орёл', 'орел', 'решка']:
                return {'success': False, 'message': '❌ Выберите "орёл" или "решка"!'}
            
            if choice in ['орёл', 'орел']:
                choice = 'орёл'
            else:
                choice = 'решка'
            
            result = random.choice(['орёл', 'решка'])
            win = result == choice
            
            if win:
                win_amount = bet
                self.economy.add_money(user_id, username, win_amount)
                message = f'🎉 Поздравляем! Выпал {result}. Вы выиграли {win_amount} монет!'
            else:
                self.economy.add_money(user_id, username, -bet)
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

# ========== СИСТЕМА ВИЗУАЛЬНЫХ МЕНЮ ==========
class MenuSystem:
    @staticmethod
    def get_main_menu():
        """Главное меню"""
        keyboard = [
            [InlineKeyboardButton("⭐ Карма", callback_data="menu_karma"),
             InlineKeyboardButton("💰 Экономика", callback_data="menu_economy")],
            [InlineKeyboardButton("🎮 Игры", callback_data="menu_games"),
             InlineKeyboardButton("🌤 Полезное", callback_data="menu_utils")],
            [InlineKeyboardButton("📊 Статистика", callback_data="menu_stats"),
             InlineKeyboardButton("📋 Помощь", callback_data="menu_help")]
        ]
        return InlineKeyboardMarkup(keyboard)
    
    @staticmethod
    def get_karma_menu():
        """Меню кармы"""
        keyboard = [
            [InlineKeyboardButton("📊 Моя карма", callback_data="karma_my")],
            [InlineKeyboardButton("🏆 Топ кармы", callback_data="karma_top")],
            [InlineKeyboardButton("⭐ Благодарить", callback_data="karma_thank")],
            [InlineKeyboardButton("🔙 Назад", callback_data="menu_main")]
        ]
        return InlineKeyboardMarkup(keyboard)
    
    @staticmethod
    def get_economy_menu():
        """Меню экономики"""
        keyboard = [
            [InlineKeyboardButton("💰 Баланс", callback_data="economy_balance")],
            [InlineKeyboardButton("🎁 Ежедневный бонус", callback_data="economy_daily")],
            [InlineKeyboardButton("💸 Перевести", callback_data="economy_transfer")],
            [InlineKeyboardButton("🔙 Назад", callback_data="menu_main")]
        ]
        return InlineKeyboardMarkup(keyboard)
    
    @staticmethod
    def get_games_menu():
        """Меню игр"""
        keyboard = [
            [InlineKeyboardButton("🎯 Орлянка", callback_data="game_coinflip")],
            [InlineKeyboardButton("🔙 Назад", callback_data="menu_main")]
        ]
        return InlineKeyboardMarkup(keyboard)
    
    @staticmethod
    def get_utils_menu():
        """Меню полезных функций"""
        keyboard = [
            [InlineKeyboardButton("🌤 Погода", callback_data="utils_weather")],
            [InlineKeyboardButton("💱 Курсы валют", callback_data="utils_exchange")],
            [InlineKeyboardButton("📜 Правила", callback_data="utils_rules")],
            [InlineKeyboardButton("🔙 Назад", callback_data="menu_main")]
        ]
        return InlineKeyboardMarkup(keyboard)

# ========== ОСНОВНОЙ КЛАСС БОТА ==========
class SuperGroupBot:
    def __init__(self, token: str):
        self.token = token
        self.config = Config()
        
        # Инициализация систем
        self.karma_system = KarmaSystem()
        self.economy_system = EconomySystem()
        self.games_system = MiniGames(self.economy_system)
        self.menu_system = MenuSystem()
        
        # Инициализация бота
        self.updater = Updater(token=token, use_context=True)
        self.dispatcher = self.updater.dispatcher
        
        self.setup_handlers()
        
        logger.info("🤖 Супер-бот инициализирован!")
    
    def setup_handlers(self):
        """Настройка всех обработчиков команд"""
        
        # Основные команды
        self.dispatcher.add_handler(CommandHandler("start", self.start_command))
        self.dispatcher.add_handler(CommandHandler("help", self.help_command))
        self.dispatcher.add_handler(CommandHandler("rules", self.rules_command))
        self.dispatcher.add_handler(CommandHandler("menu", self.menu_command))
        
        # Команды кармы
        self.dispatcher.add_handler(CommandHandler("karma", self.karma_command))
        self.dispatcher.add_handler(CommandHandler("thank", self.thank_command))
        self.dispatcher.add_handler(CommandHandler("top", self.top_command))
        
        # Команды экономики
        self.dispatcher.add_handler(CommandHandler("balance", self.balance_command))
        self.dispatcher.add_handler(CommandHandler("transfer", self.transfer_command))
        self.dispatcher.add_handler(CommandHandler("daily", self.daily_command))
        
        # Команды игр
        self.dispatcher.add_handler(CommandHandler("coinflip", self.coin_flip_command))
        
        # Обработчики сообщений
        self.dispatcher.add_handler(MessageHandler(Filters.text & ~Filters.command, self.handle_message))
        self.dispatcher.add_handler(MessageHandler(Filters.status_update.new_chat_members, self.welcome_new_members))
        self.dispatcher.add_handler(MessageHandler(Filters.status_update.left_chat_member, self.goodbye_member))
        
        # Обработчики инлайн-кнопок
        self.dispatcher.add_handler(CallbackQueryHandler(self.button_handler))
        
        # Обработчик ошибок
        self.dispatcher.add_error_handler(self.error_handler)
    
    # ========== ОСНОВНЫЕ КОМАНДЫ ==========
    def start_command(self, update: Update, context: CallbackContext):
        """Команда /start с красивым меню"""
        try:
            user = update.effective_user
            
            welcome_text = f"""
🎉 *Добро пожаловать, {user.first_name}\!*

🤖 *Super Group Bot* \- твой надежный помощник в этой группе\!

✨ *Что я умею:*
⭐ *Карма* \- система репутации
💰 *Экономика* \- зарабатывай и трать монеты
🎮 *Игры* \- развлекайся с друзьями
🌤 *Полезное* \- погода, курсы и многое другое

👇 *Выбери раздел в меню ниже или используй команды:*
/help \- Все команды
/menu \- Открыть меню
            """
            
            update.message.reply_text(
                welcome_text,
                reply_markup=self.menu_system.get_main_menu(),
                parse_mode='MarkdownV2'
            )
            logger.info(f"Пользователь {user.id} запустил бота")
        except Exception as e:
            logger.error(f"Ошибка в start_command: {e}")
            update.message.reply_text("❌ Произошла ошибка!")

    def menu_command(self, update: Update, context: CallbackContext):
        """Команда /menu - показать главное меню"""
        try:
            menu_text = """
🎯 *Главное меню*

Выберите раздел для управления ботом:
            """
            update.message.reply_text(
                menu_text,
                reply_markup=self.menu_system.get_main_menu(),
                parse_mode='MarkdownV2'
            )
        except Exception as e:
            logger.error(f"Ошибка в menu_command: {e}")
            update.message.reply_text("❌ Ошибка отображения меню!")

    def help_command(self, update: Update, context: CallbackContext):
        """Команда /help"""
        try:
            help_text = """
📋 *Доступные команды:*

👤 *Основные:*
/start \- Начать работу
/menu \- Открыть меню
/help \- Эта справка  
/rules \- Правила группы

⭐ *Система кармы:*
/karma \- Ваша карма
/thank @username \- Дать карму
/top \- Топ пользователей

💰 *Экономика:*
/balance \- Ваш баланс
/daily \- Ежедневный бонус
/transfer @username сумма \- Перевести деньги

🎮 *Игры:*
/coinflip сумма орёл/решка \- Подбросить монетку

💡 *Совет:* Используй /menu для удобного управления через кнопки\!
            """
            update.message.reply_text(help_text, parse_mode='MarkdownV2')
        except Exception as e:
            logger.error(f"Ошибка в help_command: {e}")
            update.message.reply_text("❌ Произошла ошибка!")

    def rules_command(self, update: Update, context: CallbackContext):
        """Команда /rules"""
        try:
            rules_text = """
📜 *Правила группы:*

1\. 🤝 Уважайте всех участников
2\. 🚫 Запрещен спам и реклама  
3\. ❌ Не размещайте запрещенный контент
4\. 💬 Соблюдайте тематику обсуждений
5\. ⭐ Полезные сообщения получают карму

⚠️ Нарушение правил ведет к предупреждениям, муту или бану\.
            """
            update.message.reply_text(rules_text, parse_mode='MarkdownV2')
        except Exception as e:
            logger.error(f"Ошибка в rules_command: {e}")
            update.message.reply_text("❌ Произошла ошибка!")

    # ========== ОБРАБОТЧИК ИНЛАЙН-КНОПОК ==========
    def button_handler(self, update: Update, context: CallbackContext):
        """Обработчик нажатий на инлайн-кнопки"""
        query = update.callback_query
        query.answer()
        
        user = query.from_user
        data = query.data
        
        try:
            if data == "menu_main":
                query.edit_message_text(
                    "🎯 *Главное меню*\n\nВыберите раздел:",
                    reply_markup=self.menu_system.get_main_menu(),
                    parse_mode='MarkdownV2'
                )
            
            elif data == "menu_karma":
                query.edit_message_text(
                    "⭐ *Система Кармы*\n\nУправление репутацией:",
                    reply_markup=self.menu_system.get_karma_menu(),
                    parse_mode='MarkdownV2'
                )
            
            elif data == "menu_economy":
                query.edit_message_text(
                    "💰 *Экономика*\n\nУправление финансами:",
                    reply_markup=self.menu_system.get_economy_menu(),
                    parse_mode='MarkdownV2'
                )
            
            elif data == "menu_games":
                query.edit_message_text(
                    "🎮 *Игры*\n\nВыберите игру:",
                    reply_markup=self.menu_system.get_games_menu(),
                    parse_mode='MarkdownV2'
                )
            
            elif data == "menu_utils":
                query.edit_message_text(
                    "🌤 *Полезные функции*\n\nДополнительные возможности:",
                    reply_markup=self.menu_system.get_utils_menu(),
                    parse_mode='MarkdownV2'
                )
            
            elif data == "karma_my":
                karma = self.karma_system.get_karma(user.id)
                query.edit_message_text(
                    f"⭐ *Ваша карма*\n\n{user.first_name}, ваша карма: *{karma}* очков\n\nПовышайте карму полезными сообщениями\!",
                    reply_markup=self.menu_system.get_karma_menu(),
                    parse_mode='MarkdownV2'
                )
            
            elif data == "karma_top":
                top_users = self.karma_system.get_top_users(5)
                if top_users:
                    top_text = "🏆 *Топ кармы:*\n\n"
                    for i, (username, karma) in enumerate(top_users, 1):
                        top_text += f"{i}\. @{username}: *{karma}* ⭐\n"
                else:
                    top_text = "📊 Пока нет данных о карме\!\n\nБудьте первым\!\!"
                
                query.edit_message_text(
                    top_text,
                    reply_markup=self.menu_system.get_karma_menu(),
                    parse_mode='MarkdownV2'
                )
            
            elif data == "economy_balance":
                balance = self.economy_system.get_balance(user.id, user.first_name)
                query.edit_message_text(
                    f"💰 *Ваш баланс*\n\n{user.first_name}, ваш баланс: *{balance}* монет\n\nЗарабатывайте деньги ежедневными бонусами\!",
                    reply_markup=self.menu_system.get_economy_menu(),
                    parse_mode='MarkdownV2'
                )
            
            elif data == "economy_daily":
                result = self.economy_system.daily_bonus(user.id, user.first_name)
                query.edit_message_text(
                    result['message'],
                    reply_markup=self.menu_system.get_economy_menu()
                )
            
            elif data == "utils_rules":
                rules_text = """
📜 *Правила группы:*

1\. 🤝 Уважайте всех участников
2\. 🚫 Запрещен спам и реклама  
3\. ❌ Не размещайте запрещенный контент
4\. 💬 Соблюдайте тематику обсуждений
5\. ⭐ Полезные сообщения получают карму

⚠️ Нарушение правил ведет к предупреждениям, муту или бану\.
                """
                query.edit_message_text(
                    rules_text,
                    reply_markup=self.menu_system.get_utils_menu(),
                    parse_mode='MarkdownV2'
                )
            
            elif data in ["karma_thank", "economy_transfer", "game_coinflip", "utils_weather", "utils_exchange"]:
                help_texts = {
                    "karma_thank": "💡 Чтобы поблагодарить пользователя, используйте команду:\n`/thank @username`",
                    "economy_transfer": "💡 Чтобы перевести деньги, используйте команду:\n`/transfer @username сумма`",
                    "game_coinflip": "💡 Чтобы сыграть в орлянку, используйте команду:\n`/coinflip сумма орёл/решка`",
                    "utils_weather": "💡 Чтобы узнать погоду, используйте команду:\n`/weather город`",
                    "utils_exchange": "💡 Чтобы узнать курсы валют, используйте команду:\n`/exchange`"
                }
                
                query.edit_message_text(
                    help_texts[data],
                    parse_mode='MarkdownV2'
                )
            
            elif data == "menu_stats":
                karma = self.karma_system.get_karma(user.id)
                balance = self.economy_system.get_balance(user.id, user.first_name)
                
                stats_text = f"""
📊 *Ваша статистика*

👤 *Пользователь:* {user.first_name}
⭐ *Карма:* {karma} очков
💰 *Баланс:* {balance} монет
📅 *В системе с:* {datetime.now().strftime('%d.%m.%Y')}

🎯 *Продолжайте в том же духе\!*
                """
                query.edit_message_text(
                    stats_text,
                    reply_markup=self.menu_system.get_main_menu(),
                    parse_mode='MarkdownV2'
                )
            
            elif data == "menu_help":
                self.help_command(update, context)
                
        except Exception as e:
            logger.error(f"Ошибка в button_handler: {e}")
            query.edit_message_text("❌ Произошла ошибка при обработке запроса!")

    # ========== КОМАНДЫ КАРМЫ ==========
    def karma_command(self, update: Update, context: CallbackContext):
        """Команда /karma - показать карму"""
        try:
            user = update.effective_user
            karma = self.karma_system.get_karma(user.id)
            
            update.message.reply_text(
                f"⭐ {user.first_name}, ваша карма: {karma} очков"
            )
        except Exception as e:
            logger.error(f"Ошибка в karma_command: {e}")
            update.message.reply_text("❌ Ошибка получения кармы!")

    def thank_command(self, update: Update, context: CallbackContext):
        """Команда /thank - поблагодарить пользователя"""
        try:
            if not context.args:
                update.message.reply_text("❌ Укажите пользователя: /thank @username")
                return
            
            target_username = context.args[0].replace('@', '')
            from_user = update.effective_user
            
            # Фиктивный ID для демонстрации
            target_user_id = hash(target_username) % 1000000
            
            if self.karma_system.add_karma(target_user_id, target_username, from_user.id):
                update.message.reply_text(
                    f"⭐ Вы поблагодарили @{target_username}! Карма увеличена."
                )
            else:
                update.message.reply_text("❌ Ошибка при добавлении кармы!")
                
        except Exception as e:
            logger.error(f"Ошибка в thank_command: {e}")
            update.message.reply_text("❌ Ошибка при добавлении кармы!")

    def top_command(self, update: Update, context: CallbackContext):
        """Команда /top - топ пользователей по карме"""
        try:
            top_users = self.karma_system.get_top_users(10)
            
            if not top_users:
                update.message.reply_text("📊 Пока нет данных о карме!")
                return
            
            top_text = "🏆 **Топ пользователей по карме:**\n\n"
            for i, (username, karma) in enumerate(top_users, 1):
                top_text += f"{i}. @{username}: {karma} ⭐\n"
            
            update.message.reply_text(top_text)
        except Exception as e:
            logger.error(f"Ошибка в top_command: {e}")
            update.message.reply_text("❌ Ошибка получения топа!")

    # ========== КОМАНДЫ ЭКОНОМИКИ ==========
    def balance_command(self, update: Update, context: CallbackContext):
        """Команда /balance - показать баланс"""
        try:
            user = update.effective_user
            balance = self.economy_system.get_balance(user.id, user.username or user.first_name)
            
            update.message.reply_text(
                f"💰 {user.first_name}, ваш баланс: {balance} монет"
            )
        except Exception as e:
            logger.error(f"Ошибка в balance_command: {e}")
            update.message.reply_text("❌ Ошибка получения баланса!")

    def transfer_command(self, update: Update, context: CallbackContext):
        """Команда /transfer - перевести деньги"""
        try:
            if len(context.args) < 2:
                update.message.reply_text("❌ Использование: /transfer @username сумма")
                return
            
            target_username = context.args[0].replace('@', '')
            amount = int(context.args[1])
            
            if amount <= 0:
                update.message.reply_text("❌ Сумма должна быть положительной!")
                return
            
            from_user = update.effective_user
            target_user_id = hash(target_username) % 1000000
            
            if self.economy_system.transfer_money(from_user.id, target_user_id, amount, target_username):
                update.message.reply_text(
                    f"✅ Вы перевели {amount} монет пользователю @{target_username}"
                )
            else:
                update.message.reply_text("❌ Недостаточно средств или ошибка перевода!")
                
        except ValueError:
            update.message.reply_text("❌ Неверная сумма!")
        except Exception as e:
            logger.error(f"Ошибка в transfer_command: {e}")
            update.message.reply_text("❌ Ошибка перевода!")

    def daily_command(self, update: Update, context: CallbackContext):
        """Команда /daily - ежедневный бонус"""
        try:
            user = update.effective_user
            result = self.economy_system.daily_bonus(user.id, user.username or user.first_name)
            
            update.message.reply_text(result['message'])
        except Exception as e:
            logger.error(f"Ошибка в daily_command: {e}")
            update.message.reply_text("❌ Ошибка выдачи бонуса!")

    # ========== КОМАНДЫ ИГР ==========
    def coin_flip_command(self, update: Update, context: CallbackContext):
        """Команда /coinflip - игра в орлянку"""
        try:
            if len(context.args) < 2:
                update.message.reply_text("❌ Использование: /coinflip сумма орёл/решка")
                return
            
            amount = int(context.args[0])
            choice = context.args[1].lower()
            user = update.effective_user
            
            result = self.games_system.coin_flip(user.id, user.username or user.first_name, amount, choice)
            update.message.reply_text(result['message'])
            
        except ValueError:
            update.message.reply_text("❌ Неверная сумма!")
        except Exception as e:
            logger.error(f"Ошибка в coin_flip_command: {e}")
            update.message.reply_text("❌ Ошибка в игре!")

    # ========== ОБРАБОТЧИКИ СООБЩЕНИЙ ==========
    def handle_message(self, update: Update, context: CallbackContext):
        """Обработка обычных сообщений"""
        try:
            message_text = update.message.text.lower()
            user = update.effective_user
            
            # Проверка на плохие слова
            for bad_word in self.config.BAD_WORDS:
                if bad_word in message_text:
                    update.message.delete()
                    warning = update.message.reply_text(
                        f"⚠️ {user.first_name}, пожалуйста, соблюдайте правила группы!"
                    )
                    
                    # Удаляем предупреждение через 10 секунд
                    def delete_warning():
                        import time
                        time.sleep(10)
                        try:
                            context.bot.delete_message(
                                chat_id=update.effective_chat.id,
                                message_id=warning.message_id
                            )
                        except:
                            pass
                    
                    import threading
                    thread = threading.Thread(target=delete_warning)
                    thread.start()
                    return
            
            # Автоматическое добавление кармы за длинные сообщения
            if len(update.message.text) > 100:
                self.karma_system.add_karma(user.id, user.username or user.first_name, user.id)
                
        except Exception as e:
            logger.error(f"Ошибка в handle_message: {e}")

    def welcome_new_members(self, update: Update, context: CallbackContext):
        """Приветствие новых участников"""
        try:
            for member in update.message.new_chat_members:
                welcome_text = f"""
👋 Добро пожаловать, {member.first_name}!

Рады видеть тебя в нашей группе!
Ознакомься с правилами: /rules
Получить помощь: /help

🎁 Не забудь забрать ежедневный бонус: /daily
💡 Используй /menu для удобного управления
                """
                update.message.reply_text(welcome_text)
        except Exception as e:
            logger.error(f"Ошибка в welcome_new_members: {e}")

    def goodbye_member(self, update: Update, context: CallbackContext):
        """Прощание с вышедшими участниками"""
        try:
            left_member = update.message.left_chat_member
            if left_member:
                update.message.reply_text(
                    f"😢 {left_member.first_name} покинул(а) нас..."
                )
        except Exception as e:
            logger.error(f"Ошибка в goodbye_member: {e}")

    # ========== ОБРАБОТЧИК ОШИБОК ==========
    def error_handler(self, update: Update, context: CallbackContext):
        """Обработчик ошибок"""
        try:
            logger.error(f"Ошибка: {context.error}", exc_info=context.error)
            
            if update and update.effective_message:
                update.effective_message.reply_text(
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
            print("📝 Логи записываются в файл
