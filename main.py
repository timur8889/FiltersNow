import logging
from telegram import Update, ReplyKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
from datetime import datetime, timedelta

# Настройка логирования
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

# Словарь для хранения данных пользователей
user_data = {}

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [['Добавить фильтр', 'Список фильтров'], ['Удалить фильтр']]
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    await update.message.reply_text(
        'Добро пожаловать в бот для отслеживания замены фильтров воды!',
        reply_markup=reply_markup
    )

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    user_id = update.effective_user.id

    if text == 'Добавить фильтр':
        await update.message.reply_text(
            'Введите название фильтра и срок службы в днях через запятую:\n'
            'Пример: "Картридж Pre-Clear, 180"'
        )
        context.user_data['awaiting_input'] = 'add'
    
    elif text == 'Список фильтров':
        await show_filters(update, user_id)
    
    elif text == 'Удалить фильтр':
        await update.message.reply_text('Введите название фильтра для удаления:')
        context.user_data['awaiting_input'] = 'delete'

    elif context.user_data.get('awaiting_input') == 'add':
        try:
            name, days = map(str.strip, text.split(','))
            install_date = datetime.now()
            replace_date = install_date + timedelta(days=int(days))
            
            if user_id not in user_data:
                user_data[user_id] = {}
            
            user_data[user_id][name] = {
                'install_date': install_date,
                'replace_date': replace_date
            }
            
            await update.message.reply_text(
                f'Фильтр "{name}" добавлен!\n'
                f'Дата установки: {install_date.strftime("%d.%m.%Y")}\n'
                f'Рекомендуемая замена: {replace_date.strftime("%d.%m.%Y")}'
            )
            context.user_data['awaiting_input'] = None
        
        except:
            await update.message.reply_text('Ошибка формата! Используйте: "Название, количество_дней"')
    
    elif context.user_data.get('awaiting_input') == 'delete':
        if user_id in user_data and text in user_data[user_id]:
            del user_data[user_id][text]
            await update.message.reply_text(f'Фильтр "{text}" удален!')
        else:
            await update.message.reply_text('Фильтр не найден!')
        context.user_data['awaiting_input'] = None

async def show_filters(update: Update, user_id: int):
    if user_id not in user_data or not user_data[user_id]:
        await update.message.reply_text('У вас нет добавленных фильтров!')
        return
    
    text = "Ваши фильтры:\n\n"
    for name, data in user_data[user_id].items():
        status = "🔴 Требуется замена!" if datetime.now() > data['replace_date'] else "🟢 Активен"
        text += (
            f"{name}\n"
            f"Установлен: {data['install_date'].strftime('%d.%m.%Y')}\n"
            f"Замена до: {data['replace_date'].strftime('%d.%m.%Y')}\n"
            f"Статус: {status}\n\n"
        )
    
    await update.message.reply_text(text)

def main():
    application = Application.builder().token("8278600298:AAGPjUhyU5HxXOaLRvu-FSRldBW_UCmwOME").build()
    
    application.add_handler(CommandHandler("start", start))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    
    # Проверка напоминаний каждые 24 часа
    job_queue = application.job_queue
    job_queue.run_repeating(check_reminders, interval=86400, first=10)
    
    application.run_polling()

if __name__ == '__main__':
    main()
