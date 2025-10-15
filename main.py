import logging
import requests
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
from config import TELEGRAM_BOT_TOKEN, DEEPSEEK_API_KEY

# Настройка логирования
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

class DeepSeekBot:
    def __init__(self):
        self.deepseek_api_url = "https://api.deepseek.com/v1/chat/completions"
        self.headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {DEEPSEEK_API_KEY}"
        }

    async def ask_deepseek(self, question: str) -> str:
        """Отправляет запрос к DeepSeek API и возвращает ответ"""
        try:
            payload = {
                "model": "deepseek-chat",
                "messages": [
                    {
                        "role": "system",
                        "content": "Ты полезный AI-ассистент. Отвечай точно и понятно."
                    },
                    {
                        "role": "user",
                        "content": question
                    }
                ],
                "stream": False,
                "max_tokens": 2048
            }
            
            response = requests.post(self.deepseek_api_url, json=payload, headers=self.headers, timeout=30)
            response.raise_for_status()
            
            data = response.json()
            return data['choices'][0]['message']['content']
            
        except requests.exceptions.RequestException as e:
            logger.error(f"Ошибка при запросе к DeepSeek API: {e}")
            return "Извините, произошла ошибка при обращении к AI. Попробуйте позже."
        except Exception as e:
            logger.error(f"Неожиданная ошибка: {e}")
            return "Произошла непредвиденная ошибка."

    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработчик команды /start"""
        user = update.effective_user
        welcome_text = f"""
Привет, {user.first_name}! 👋

Я бот с интеграцией DeepSeek AI. Просто напиши мне любой вопрос, и я постараюсь помочь!

Примеры вопросов:
• Объясни квантовую физику простыми словами
• Напиши код для сортировки массива на Python
• Помоги составить план обучения программированию
        """
        await update.message.reply_text(welcome_text)

    async def help_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработчик команды /help"""
        help_text = """
🤖 **Доступные команды:**
/start - Начать работу с ботом
/help - Показать эту справку

💡 **Как использовать:**
Просто напиши мне любой вопрос на естественном языке, и я обращусь к DeepSeek AI для получения ответа.

⚠️ **Ограничения:**
• Максимальная длина ответа: 2048 токенов
• Время ответа: до 30 секунд
        """
        await update.message.reply_text(help_text)

    async def handle_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработчик текстовых сообщений"""
        user_message = update.message.text
        
        # Показываем, что бот печатает
        await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")
        
        # Получаем ответ от DeepSeek
        response = await self.ask_deepseek(user_message)
        
        # Отправляем ответ пользователю
        await update.message.reply_text(response)

    async def error_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработчик ошибок"""
        logger.error(f"Ошибка при обработке сообщения: {context.error}")
        try:
            await update.message.reply_text("Произошла ошибка. Пожалуйста, попробуйте еще раз.")
        except:
            pass

def main():
    """Основная функция запуска бота"""
    bot = DeepSeekBot()
    
    # Создаем приложение
    application = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
    
    # Добавляем обработчики
    application.add_handler(CommandHandler("start", bot.start))
    application.add_handler(CommandHandler("help", bot.help_command))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, bot.handle_message))
    
    # Добавляем обработчик ошибок
    application.add_error_handler(bot.error_handler)
    
    # Запускаем бота
    print("Бот запущен...")
    application.run_polling()

if __name__ == '__main__':
    main()
