import logging
from telegram import (
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
    Update,
)
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    filters,
    ConversationHandler,
    ContextTypes,
)

# Настройки
ADMIN_CHAT_ID = 5024165375  # Замените на реальный ID администратора
BOT_TOKEN = "8278600298:AAGPjUhyU5HxXOaLRvu-FSRldBW_UCmwOME"

# Состояния разговора
FIO, PHONE, PROBLEM, PRIORITY, DESCRIPTION = range(5)

# Клавиатуры
main_keyboard = [['Подать заявку']]
problem_keyboard = [
    ['Интернет', 'Телефония'],
    ['Видеонаблюдение', 'Домофон'],
    ['Другая проблема']
]
priority_keyboard = [['Низкий', 'Средний'], ['Высокий', 'Критический']]

# Включим логирование
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO
)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        'Добро пожаловать! Для подачи заявки нажмите кнопку:',
        reply_markup=ReplyKeyboardMarkup(main_keyboard, resize_keyboard=True)
    )

async def start_application(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text(
        'Введите ваше ФИО:',
        reply_markup=ReplyKeyboardRemove()
    )
    return FIO

async def fio_received(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data['fio'] = update.message.text
    await update.message.reply_text('Введите ваш номер телефона:')
    return PHONE

async def phone_received(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data['phone'] = update.message.text
    await update.message.reply_text(
        'Выберите тип проблемы:',
        reply_markup=ReplyKeyboardMarkup(problem_keyboard, resize_keyboard=True)
    )
    return PROBLEM

async def problem_received(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data['problem'] = update.message.text
    await update.message.reply_text(
        'Выберите приоритет:',
        reply_markup=ReplyKeyboardMarkup(priority_keyboard, resize_keyboard=True)
    )
    return PRIORITY

async def priority_received(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data['priority'] = update.message.text
    await update.message.reply_text(
        'Опишите проблему подробнее:',
        reply_markup=ReplyKeyboardRemove()
    )
    return DESCRIPTION

async def description_received(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data['description'] = update.message.text
    
    # Формируем заявку
    application = (
        "🎛 *Новая заявка*\n"
        f"👤 *ФИО:* {context.user_data['fio']}\n"
        f"📞 *Телефон:* {context.user_data['phone']}\n"
        f"🔧 *Проблема:* {context.user_data['problem']}\n"
        f"🚨 *Приоритет:* {context.user_data['priority']}\n"
        f"📝 *Описание:* {context.user_data['description']}\n"
        f"🆔 *ID пользователя:* {update.effective_user.id}"
    )
    
    # Отправляем администратору
    await context.bot.send_message(
        chat_id=ADMIN_CHAT_ID,
        text=application,
        parse_mode='Markdown'
    )
    
    # Подтверждение пользователю
    await update.message.reply_text(
        '✅ Ваша заявка принята! Мы свяжемся с вами в ближайшее время.',
        reply_markup=ReplyKeyboardMarkup(main_keyboard, resize_keyboard=True)
    )
    
    return ConversationHandler.END

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text(
        'Заявка отменена.',
        reply_markup=ReplyKeyboardMarkup(main_keyboard, resize_keyboard=True)
    )
    return ConversationHandler.END

def main() -> None:
    # Создаем Application
    application = Application.builder().token(BOT_TOKEN).build()

    conv_handler = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex('^Подать заявку$'), start_application)],
        states={
            FIO: [MessageHandler(filters.TEXT & ~filters.COMMAND, fio_received)],
            PHONE: [MessageHandler(filters.TEXT & ~filters.COMMAND, phone_received)],
            PROBLEM: [MessageHandler(filters.Regex('^(Интернет|Телефония|Видеонаблюдение|Домофон|Другая проблема)$'), problem_received)],
            PRIORITY: [MessageHandler(filters.Regex('^(Низкий|Средний|Высокий|Критический)$'), priority_received)],
            DESCRIPTION: [MessageHandler(filters.TEXT & ~filters.COMMAND, description_received)],
        },
        fallbacks=[CommandHandler('cancel', cancel)],
    )

    application.add_handler(CommandHandler("start", start))
    application.add_handler(conv_handler)

    # Запускаем бота
    application.run_polling()

if __name__ == '__main__':
    main()
