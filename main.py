import logging
from telegram import Update, ReplyKeyboardMarkup, ReplyKeyboardRemove
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    filters,
    ConversationHandler,
    ContextTypes,
)
import sqlite3
from datetime import datetime

# Настройка логирования
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

# Определение состояний для ConversationHandler
FILTER_TYPE, FILTER_NAME, LAST_REPLACEMENT, REPLACEMENT_PERIOD = range(4)

# Подключение к базе данных
def init_db():
    conn = sqlite3.connect('filters.db')
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS filters (
            id INTEGER PRIMARY KEY,
            user_id INTEGER,
            filter_type TEXT,
            filter_name TEXT,
            last_replacement TEXT,
            replacement_period INTEGER
        )
    ''')
    conn.commit()
    conn.close()

# Команда /start
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.message.from_user
    keyboard = [['Добавить фильтр', 'Мои фильтры']]
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    await update.message.reply_text(
        f"Привет, {user.first_name}! Я бот для учета замены фильтров.\n"
        "Выберите действие:",
        reply_markup=reply_markup
    )

# Добавление фильтра - начало
async def add_filter(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text(
        "Введите тип фильтра (например: Вода, Воздух):",
        reply_markup=ReplyKeyboardRemove()
    )
    return FILTER_TYPE

# Получение типа фильтра
async def filter_type(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data['filter_type'] = update.message.text
    await update.message.reply_text("Введите название/модель фильтра:")
    return FILTER_NAME

# Получение названия фильтра
async def filter_name(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data['filter_name'] = update.message.text
    await update.message.reply_text("Введите дату последней замены (ГГГГ-ММ-ДД):")
    return LAST_REPLACEMENT

# Получение даты замены
async def last_replacement(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data['last_replacement'] = update.message.text
    await update.message.reply_text("Введите периодичность замены (в днях):")
    return REPLACEMENT_PERIOD

# Получение периодичности и сохранение в БД
async def replacement_period(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_data = context.user_data
    user_id = update.message.from_user.id
    
    try:
        # Валидация данных
        datetime.strptime(user_data['last_replacement'], '%Y-%m-%d')
        period = int(update.message.text)
        
        # Сохранение в БД
        conn = sqlite3.connect('filters.db')
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO filters (user_id, filter_type, filter_name, last_replacement, replacement_period)
            VALUES (?, ?, ?, ?, ?)
        ''', (user_id, user_data['filter_type'], user_data['filter_name'], 
              user_data['last_replacement'], period))
        conn.commit()
        conn.close()
        
        await update.message.reply_text("Фильтр успешно добавлен!")
        
    except ValueError:
        await update.message.reply_text("Ошибка в данных! Проверьте формат даты и числовые значения.")
    
    finally:
        context.user_data.clear()
    
    return ConversationHandler.END

# Просмотр всех фильтров пользователя
async def list_filters(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.message.from_user.id
    conn = sqlite3.connect('filters.db')
    cursor = conn.cursor()
    cursor.execute('''
        SELECT filter_type, filter_name, last_replacement, replacement_period 
        FROM filters 
        WHERE user_id = ?
    ''', (user_id,))
    
    filters = cursor.fetchall()
    conn.close()
    
    if not filters:
        await update.message.reply_text("У вас нет добавленных фильтров.")
        return
    
    response = "Ваши фильтры:\n\n"
    for f in filters:
        next_replacement = (datetime.strptime(f[2], '%Y-%m-%d') + 
                          timedelta(days=f[3])).strftime('%Y-%m-%d')
        response += (f"Тип: {f[0]}\n"
                    f"Название: {f[1]}\n"
                    f"Последняя замена: {f[2]}\n"
                    f"Следующая замена: {next_replacement}\n\n")
    
    await update.message.reply_text(response)

# Отмена диалога
async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text(
        'Действие отменено.',
        reply_markup=ReplyKeyboardRemove()
    )
    context.user_data.clear()
    return ConversationHandler.END

def main() -> None:
    # Инициализация БД
    init_db()
    
    # Создание Application
    application = Application.builder().token(8278600298:AAFA-R0ql-dibAoBruxgwitHTx_LLx61OdM).build()

    # Обработчики диалога
    conv_handler = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex('^Добавить фильтр$'), add_filter)],
        states={
            FILTER_TYPE: [MessageHandler(filters.TEXT & ~filters.COMMAND, filter_type)],
            FILTER_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, filter_name)],
            LAST_REPLACEMENT: [MessageHandler(filters.TEXT & ~filters.COMMAND, last_replacement)],
            REPLACEMENT_PERIOD: [MessageHandler(filters.TEXT & ~filters.COMMAND, replacement_period)],
        },
        fallbacks=[CommandHandler('cancel', cancel)],
    )

    # Регистрация обработчиков
    application.add_handler(CommandHandler("start", start))
    application.add_handler(conv_handler)
    application.add_handler(MessageHandler(filters.Regex('^Мои фильтры$'), list_filters))

    # Запуск бота
    application.run_polling()

if __name__ == "__main__":
    main()
