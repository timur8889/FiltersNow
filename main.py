pip install python-telegram-bot
# Настройка логирования
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

# Состояния для ConversationHandler
SET_FILTER_NAME, SET_INSTALL_DATE, SET_EXPIRY_DAYS = range(3)

# Временное хранилище данных (вместо БД)
filters_db = {}
notifications_jobs = {}

# Клавиатура
main_keyboard = [["Добавить фильтр", "Мои фильтры"], ["Удалить фильтр"]]

# Функции для работы с датами
def parse_date(date_str: str) -> datetime:
    return datetime.strptime(date_str, "%d.%m.%Y").date()

def format_date(date: datetime) -> str:
    return date.strftime("%d.%m.%Y")

# Проверка и уведомления
async def check_expiry(context: CallbackContext) -> None:
    chat_id = context.job.chat_id
    for filter_name, filter_data in filters_db.get(chat_id, {}).items():
        expiry_date = filter_data["expiry_date"]
        days_until_expiry = (expiry_date - datetime.now().date()).days
        
        if days_until_expiry == 3:
            await context.bot.send_message(
                chat_id=chat_id,
                text=f"🔔 Напоминание: замените фильтр '{filter_name}' через 3 дня!\n"
                     f"Дата окончания срока: {format_date(expiry_date)}",
            )

# Команды бота
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    reply_markup = ReplyKeyboardMarkup(main_keyboard, resize_keyboard=True)
    await update.message.reply_text(
        "Добро пожаловать! Управляйте фильтрами через меню:",
        reply_markup=reply_markup,
    )

async def add_filter_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text(
        "Введите название фильтра:",
        reply_markup=ReplyKeyboardRemove(),
    )
    return SET_FILTER_NAME

async def set_filter_name(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data["filter_name"] = update.message.text
    await update.message.reply_text("Введите дату установки (в формате ДД.ММ.ГГГГ):")
    return SET_INSTALL_DATE

async def set_install_date(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    try:
        install_date = parse_date(update.message.text)
        context.user_data["install_date"] = install_date
        await update.message.reply_text("Введите срок службы (в днях):")
        return SET_EXPIRY_DAYS
    except ValueError:
        await update.message.reply_text("Неверный формат даты! Используйте ДД.ММ.ГГГГ:")
        return SET_INSTALL_DATE

async def set_expiry_days(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    try:
        expiry_days = int(update.message.text)
        chat_id = update.effective_chat.id
        filter_data = {
            "install_date": context.user_data["install_date"],
            "expiry_date": context.user_data["install_date"] + timedelta(days=expiry_days),
        }
        
        # Сохраняем фильтр
        if chat_id not in filters_db:
            filters_db[chat_id] = {}
        filters_db[chat_id][context.user_data["filter_name"]] = filter_data
        
        # Запускаем проверку уведомлений
        if chat_id not in notifications_jobs:
            job_queue = context.application.job_queue
            notifications_jobs[chat_id] = job_queue.run_repeating(
                check_expiry,
                interval=timedelta(hours=24),
                chat_id=chat_id,
            )
        
        reply_markup = ReplyKeyboardMarkup(main_keyboard, resize_keyboard=True)
        await update.message.reply_text(
            f"Фильтр '{context.user_data['filter_name']}' добавлен!\n"
            f"Срок годности до: {format_date(filter_data['expiry_date'])}",
            reply_markup=reply_markup,
        )
        return ConversationHandler.END
    except ValueError:
        await update.message.reply_text("Введите целое число дней:")
        return SET_EXPIRY_DAYS

async def show_filters(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    if not filters_db.get(chat_id):
        await update.message.reply_text("У вас нет добавленных фильтров.")
        return
    
    text = "Ваши фильтры:\n\n"
    for name, data in filters_db[chat_id].items():
        text += (
            f"🔧 {name}\n"
            f"📅 Установлен: {format_date(data['install_date'])}\n"
            f"⏰ Срок до: {format_date(data['expiry_date'])}\n\n"
        )
    await update.message.reply_text(text)

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text(
        "Отменено",
        reply_markup=ReplyKeyboardMarkup(main_keyboard, resize_keyboard=True),
    )
    return ConversationHandler.END

def main() -> None:
    application = ApplicationBuilder().token("8278600298:AAFA-R0ql-dibAoBruxgwitHTx_LLx61OdM").build()

    conv_handler = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex("^Добавить фильтр$"), add_filter_start)],
        states={
            SET_FILTER_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, set_filter_name)],
            SET_INSTALL_DATE: [MessageHandler(filters.TEXT & ~filters.COMMAND, set_install_date)],
            SET_EXPIRY_DAYS: [MessageHandler(filters.TEXT & ~filters.COMMAND, set_expiry_days)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )

    application.add_handler(CommandHandler("start", start))
    application.add_handler(conv_handler)
    application.add_handler(MessageHandler(filters.Regex("^Мои фильтры$"), show_filters))

    application.run_polling()

if __name__ == "__main__":
    main()
