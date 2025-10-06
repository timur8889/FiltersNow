import logging
from telegram import Update, ReplyKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

# Настройка логирования
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

# Словарь для хранения данных пользователей (временное хранилище)
user_data = {}

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [['Установить фильтр', 'Статус фильтра'], ['Помощь']]
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    
    await update.message.reply_text(
        'Добро пожаловать! Я помогу отслеживать замену фильтров для воды.\n'
        'Выберите действие:',
        reply_markup=reply_markup
    )

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    user_id = update.effective_user.id

    if text == 'Установить фильтр':
        await set_filter(update, context)
    elif text == 'Статус фильтра':
        await check_status(update, context)
    elif text == 'Помощь':
        await show_help(update, context)

async def set_filter(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user_data[user_id] = {
        'install_date': datetime.now(),
        'replace_after': 6  # месяцев по умолчанию
    }
    
    await update.message.reply_text(
        f'Фильтр установлен! Дата установки: {datetime.now().strftime("%d.%m.%Y")}\n'
        'Рекомендуемая замена через 6 месяцев.'
    )

async def check_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id not in user_data:
        await update.message.reply_text('У вас нет активного фильтра. Используйте "Установить фильтр"')
        return

    data = user_data[user_id]
    replace_date = data['install_date'] + timedelta(days=30*data['replace_after'])
    days_left = (replace_date - datetime.now()).days

    if days_left > 0:
        await update.message.reply_text(
            f'Ваш фильтр установлен {data["install_date"].strftime("%d.%m.%Y")}\n'
            f'До замены осталось: {days_left} дней'
        )
    else:
        await update.message.reply_text(
            '❌ Время заменить фильтр!\n'
            f'Последняя замена: {data["install_date"].strftime("%d.%m.%Y")}'
        )

async def show_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    help_text = (
        '📋 Доступные команды:\n'
        '• Установить фильтр - начать отсчёт времени работы фильтра\n'
        '• Статус фильтра - проверить оставшееся время\n'
        '• Помощь - показать это сообщение\n\n'
        'Стандартный срок службы картриджа - 6 месяцев'
    )
    await update.message.reply_text(help_text)

def main():
    # Замените 'YOUR_TOKEN' на реальный токен бота
    application = Application.builder().token(8278600298:AAFA-R0ql-dibAoBruxgwitHTx_LLx61OdM).build()

    # Обработчики команд
    application.add_handler(CommandHandler("start", start))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    # Запуск бота
    application.run_polling()

if __name__ == '__main__':
    main()
