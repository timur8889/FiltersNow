from telegram import Update
from telegram.ext import Updater, CommandHandler, MessageHandler, Filters, CallbackContext

# Вставьте сюда ваш токен
TOKEN = '8278600298:AAGPjUhyU5HxXOaLRvu-FSRldBW_UCmwOME'

# Функция для обработки команды /start
def start(update: Update, context: CallbackContext) -> None:
    update.message.reply_text('Привет! Я бот для сбора заявок. Пожалуйста, напишите вашу заявку.')

# Функция для обработки текстовых сообщений
def handle_message(update: Update, context: CallbackContext) -> None:
    user_message = update.message.text
    # Здесь вы можете сохранить заявку в файл или базу данных
    with open('applications.txt', 'a') as f:
        f.write(f'{update.message.from_user.username}: {user_message}\n')
    update.message.reply_text('Ваша заявка принята! Спасибо!')

def main() -> None:
    updater = Updater(TOKEN)

    # Получаем диспетчер для регистрации обработчиков
    dispatcher = updater.dispatcher

    # Обработчики команд
    dispatcher.add_handler(CommandHandler("start", start))

    # Обработчик текстовых сообщений
    dispatcher.add_handler(MessageHandler(Filters.text & ~Filters.command, handle_message))

    # Запуск бота
    updater.start_polling()

    # Ожидание завершения работы
    updater.idle()

if __name__ == '__main__':
    main()


Конечно - я могу быть полезен, но турбо режим чата на уровень выше друг 😎
