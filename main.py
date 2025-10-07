import sqlite3
from datetime import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Updater, CommandHandler, CallbackQueryHandler, CallbackContext, MessageHandler, Filters

# Инициализация базы данных
def init_db():
    conn = sqlite3.connect('finance.db')
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS transactions
                 (id INTEGER PRIMARY KEY, 
                  user_id INTEGER,
                  amount REAL, 
                  category TEXT,
                  description TEXT,
                  date TEXT)''')
    conn.commit()
    conn.close()

# Добавление транзакции
def add_transaction(user_id, amount, category, description=""):
    conn = sqlite3.connect('finance.db')
    c = conn.cursor()
    date = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    c.execute("INSERT INTO transactions (user_id, amount, category, description, date) VALUES (?, ?, ?, ?, ?)",
              (user_id, amount, category, description, date))
    conn.commit()
    conn.close()

# Команда /start
def start(update: Update, context: CallbackContext):
    update.message.reply_text(
        "Привет! Я бот для учета финансов.\n"
        "Доступные команды:\n"
        "/add - добавить транзакцию\n"
        "/list - показать историю\n"
        "/stats - статистика"
    )

# Команда /add
def add(update: Update, context: CallbackContext):
    keyboard = [
        [InlineKeyboardButton("Продукты", callback_data="category_Продукты")],
        [InlineKeyboardButton("Транспорт", callback_data="category_Транспорт")],
        [InlineKeyboardButton("Развлечения", callback_data="category_Развлечения")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    update.message.reply_text("Выберите категорию:", reply_markup=reply_markup)
    context.user_data['waiting_for_amount'] = True

# Обработчик кнопок категорий
def button_handler(update: Update, context: CallbackContext):
    query = update.callback_query
    category = query.data.split('_')[1]
    context.user_data['category'] = category
    query.edit_message_text(f"Категория: {category}\nВведите сумму:")
    context.user_data['waiting_for_amount'] = True

# Обработчик ввода суммы
def amount_handler(update: Update, context: CallbackContext):
    if context.user_data.get('waiting_for_amount'):
        try:
            amount = float(update.message.text)
            category = context.user_data.get('category')
            add_transaction(update.effective_user.id, amount, category)
            update.message.reply_text(f"Добавлено: {amount} руб. в категорию '{category}'")
            context.user_data.clear()
        except ValueError:
            update.message.reply_text("Ошибка! Введите число.")

# Команда /list с фильтрами
def list_transactions(update: Update, context: CallbackContext):
    user_id = update.effective_user.id
    conn = sqlite3.connect('finance.db')
    c = conn.cursor()
    
    # Базовый запрос
    query = "SELECT * FROM transactions WHERE user_id = ?"
    params = [user_id]
    
    # Фильтр по категории (если передан аргумент /list транспорт)
    if context.args:
        category_filter = context.args[0].capitalize()
        query += " AND category = ?"
        params.append(category_filter)
    
    c.execute(query, params)
    transactions = c.fetchall()
    conn.close()
    
    if not transactions:
        update.message.reply_text("Транзакций не найдено")
        return
    
    response = "Последние транзакции:\n" + "\n".join(
        [f"{t[3]} руб. | {t[4]} | {t[5]}" for t in transactions[-10:]])
    update.message.reply_text(response)

# Команда /stats
def stats(update: Update, context: CallbackContext):
    user_id = update.effective_user.id
    conn = sqlite3.connect('finance.db')
    c = conn.cursor()
    
    c.execute("""SELECT category, SUM(amount) 
                 FROM transactions 
                 WHERE user_id = ? 
                 GROUP BY category""", (user_id,))
    results = c.fetchall()
    conn.close()
    
    if not results:
        update.message.reply_text("Нет данных для статистики")
        return
    
    stats_text = "Статистика по категориям:\n" + "\n".join(
        [f"{cat}: {total} руб." for cat, total in results])
    update.message.reply_text(stats_text)

def main():
    init_db()
    updater = Updater("8278600298:AAGPjUhyU5HxXOaLRvu-FSRldBW_UCmwOME", use_context=True)
    dp = updater.dispatcher

    dp.add_handler(CommandHandler("start", start))
    dp.add_handler(CommandHandler("add", add))
    dp.add_handler(CommandHandler("list", list_transactions))
    dp.add_handler(CommandHandler("stats", stats))
    dp.add_handler(CallbackQueryHandler(button_handler))
    dp.add_handler(MessageHandler(Filters.text & ~Filters.command, amount_handler))

    updater.start_polling()
    updater.idle()

if __name__ == '__main__':
    main()
