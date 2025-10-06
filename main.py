import logging
from telegram import Update, ReplyKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

# Настройка логирования
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

# Словарь для хранения данных о фильтрах (в реальном проекте используйте БД)
user_filters = {}

# Клавиатура для быстрых команд
keyboard = [['Добавить фильтр', 'Список фильтров'], ['Удалить фильтр', 'Помощь']]
reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.re_text(
        'Привет! Я помогу отслеживать замену фильтров.\n\n'
        'Доступные команды:\n'
        '• Добавить фильтр - внести новый фильтр\n'
        '• Список фильтров - показать все фильтры\n'
        '• Удалить фильтр - удалить фильтр из списка\n'
        '• Помощь - показать это сообщение',
        reply_markup=reply_markup
    )

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    user_id = update.effective_user.id

    if text == 'Добавить фильтр':
        await update.message.re_text('Введите название фильтра и срок службы в месяцах через запятую:\nПример: "Фильтр грубой очистки, 6"')
        context.user_data['awaiting_input'] = 'add_filter'
    
    elif text == 'Список фильтров':
        if user_id not in user_filters or not user_filters[user_id]:
            await update.message.re_text('У вас нет добавленных фильтров')
        else:
            filters_list = '\n'.join([
                f'{name} (замена через {months} мес.)' 
                for name, months in user_filters[user_id].items()
            ])
            await update.message.re_text(f'Ваши фильтры:\n{filters_list}')
    
    elif text == 'Удалить фильтр':
        if user_id not in user_filters or not user_filters[user_id]:
            await update.message.re_text('Нет фильтров для удаления')
        else:
            await update.message.re_text('Введите название фильтра для удаления:')
            context.user_data['awaiting_input'] = 'remove_filter'
    
    elif text == 'Помощь':
        await start(update, context)
    
    elif context.user_data.get('awaiting_input') == 'add_filter':
        try:
            name, months = map(str.strip, text.split(','))
            months = int(months)
            if user_id not in user_filters:
                user_filters[user_id] = {}
            user_filters[user_id][name] = months
            await update.message.re_text(f'Фильтр "{name}" добавлен с сроком службы {months} мес.')
        except:
            await update.message.re_text('Ошибка формата! Используйте: "Название, срок_в_месяцах"')
        context.user_data['awaiting_input'] = None
    
    elif context.user_data.get('awaiting_input') == 'remove_filter':
        if user_id in user_filters and text in user_filters[user_id]:
            del user_filters[user_id][text]
            await update.message.re_text(f'Фильтр "{text}" удален')
        else:
            await update.message.re_text('Фильтр не найден')
        context.user_data['awaiting_input'] = None

def main():
    # Замените 'YOUR_TOKEN' на реальный токен бота
    application = Application.builder().token(8278600298:AAFA-R0ql-dibAoBruxgwitHTx_LLx61OdM).build()
    
    application.add_handler(CommandHandler('start', start))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    
    # Запуск бота
    application.run_polling()

if __name__ == '__main__':
    main()
