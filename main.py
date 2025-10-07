import logging
from telegram import (
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
    Update,
    KeyboardButton
)
from telegram.ext import (
    Updater,
    CommandHandler,
    MessageHandler,
    Filters,
    ConversationHandler,
    CallbackContext,
)

# Настройки
ADMIN_CHAT_ID = 5024165375  # Замените на реальный ID администратора
BOT_TOKEN = "8278600298:AAFA-R0ql-dibAoBruxgwitHTx_LLx61OdM"

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

def start(update: Update, context: CallbackContext) -> None:
    update.message.reply_text(
        'Добро пожаловать! Для подачи заявки нажмите кнопку:',
        reply_markup=ReplyKeyboardMarkup(main_keyboard, resize_keyboard=True)
    )

def start_application(update: Update, context: CallbackContext) -> int:
    update.message.reply_text(
        'Введите ваше ФИО:',
        reply_markup=ReplyKeyboardRemove()
    )
    return FIO

def fio_received(update: Update, context: CallbackContext) -> int:
    context.user_data['fio'] = update.message.text
    update.message.reply_text('Введите ваш номер телефона:')
    return PHONE

def phone_received(update: Update, context: CallbackContext) -> int:
    context.user_data['phone'] = update.message.text
    update.message.reply_text(
        'Выберите тип проблемы:',
        reply_markup=ReplyKeyboardMarkup(problem_keyboard, resize_keyboard=True)
    )
    return PROBLEM

def problem_received(update: Update, context: CallbackContext) -> int:
    context.user_data['problem'] = update.message.text
    update.message.reply_text(
        'Выберите приоритет:',
        reply_markup=ReplyKeyboardMarkup(priority_keyboard, resize_keyboard=True)
    )
    return PRIORITY

def priority_received(update: Update, context: CallbackContext) -> int:
    context.user_data['priority'] = update.message.text
    update.message.reply_text(
        'Опишите проблему подробнее:',
        reply_markup=ReplyKeyboardRemove()
    )
    return DESCRIPTION

def description_received(update: Update, context: CallbackContext) -> int:
    context.user_data['description'] = update.message.text
    
    # Формируем заявку
    application = (
        "🎛 *Новая заявка*\n"
        f"👤 *ФИО:* {context.user_data['fio']}\n"
        f"📞 *Телефон:* {context.user_data['phone']}\n"
        f"🔧 *Проблема:* {context.user_data['problem']}\n"
        f"🚨 *Приоритет:* {context.user_data['priority']}\n"
        f"📝 *Описание:* {context.user_data['description']}"
    )
    
    # Отправляем администратору
    context.bot.send_message(
        chat_id=ADMIN_CHAT_ID,
        text=application,
        parse_mode='Markdown'
    )
    
    # Подтверждение пользователю
    update.message.reply_text(
        '✅ Ваша заявка принята! Мы свяжемся с вами в ближайшее время.',
        reply_markup=ReplyKeyboardMarkup(main_keyboard, resize_keyboard=True)
    )
    
    return ConversationHandler.END

def cancel(update: Update, context: CallbackContext) -> int:
    update.message.reply_text(
        'Заявка отменена.',
        reply_markup=ReplyKeyboardMarkup(main_keyboard, resize_keyboard=True)
    )
    return ConversationHandler.END

def main() -> None:
    updater = Updater(BOT_TOKEN)
    dispatcher = updater.dispatcher

    conv_handler = ConversationHandler(
        entry_points=[MessageHandler(Filters.regex('^Подать заявку$'), start_application)],
        states={
            FIO: [MessageHandler(Filters.text & ~Filters.command, fio_received)],
            PHONE: [MessageHandler(Filters.text & ~Filters.command, phone_received)],
            PROBLEM: [MessageHandler(Filters.regex('^(Интернет|Телефония|Видеонаблюдение|Домофон|Другая проблема)$'), problem_received)],
            PRIORITY: [MessageHandler(Filters.regex('^(Низкий|Средний|Высокий|Критический)$'), priority_received)],
            DESCRIPTION: [MessageHandler(Filters.text & ~Filters.command, description_received)],
        },
        fallbacks=[CommandHandler('cancel', cancel)],
    )

    dispatcher.add_handler(CommandHandler("start", start))
    dispatcher.add_handler(conv_handler)

    updater.start_polling()
    updater.idle()

if __name__ == '__main__':
    main()
