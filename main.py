import logging
import sqlite3
from datetime import datetime
# Вместо этого:
# from aiogram import Bot, Dispatcher, types
from aiogram import Bot, types
from aiogram.dispatcher import Dispatcher

# Настройки
API_TOKEN = '8278600298:AAGPjUhyU5HxXOaLRvu-FSRldBW_UCmwOME'
ADMIN_ID = 5024165375  # Замените на ваш ID в Telegram

# Инициализация бота
logging.basicConfig(level=logging.INFO)
bot = Bot(token=API_TOKEN)
storage = MemoryStorage()
dp = Dispatcher(bot, storage=storage)

# Инициализация БД
def init_db():
    conn = sqlite3.connect('filters.db')
    cur = conn.cursor()
    cur.execute('''CREATE TABLE IF NOT EXISTS filters (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                filter_type TEXT,
                last_change DATE)''')
    conn.commit()
    conn.close()

# States
class FilterStates(StatesGroup):
    waiting_filter_type = State()
    waiting_change_date = State()

# Команда start
@dp.message_handler(commands=['start'])
async def cmd_start(message: types.Message):
    await message.answer(
        "🤖 Бот для учета замены фильтров\n\n"
        "Доступные команды:\n"
        "/add - Добавить фильтр\n"
        "/list - Показать все фильтры\n"
        "/delete - Удалить фильтр"
    )

# Добавление фильтра
@dp.message_handler(commands=['add'])
async def cmd_add(message: types.Message):
    await FilterStates.waiting_filter_type.set()
    await message.answer("Введите тип фильтра:")

@dp.message_handler(state=FilterStates.waiting_filter_type)
async def process_filter_type(message: types.Message, state: FSMContext):
    async with state.proxy() as data:
        data['filter_type'] = message.text

    await FilterStates.next()
    await message.answer("Введите дату последней замены (ГГГГ-ММ-ДД):")

@dp.message_handler(state=FilterStates.waiting_change_date)
async def process_date(message: types.Message, state: FSMContext):
    try:
        change_date = datetime.strptime(message.text, '%Y-%m-%d').date()
        
        conn = sqlite3.connect('filters.db')
        cur = conn.cursor()
        cur.execute("INSERT INTO filters (user_id, filter_type, last_change) VALUES (?, ?, ?)",
                   (message.from_user.id, 
                    (await state.get_data())['filter_type'],
                    change_date))
        conn.commit()
        conn.close()

        await message.answer("✅ Фильтр успешно добавлен!")
        await state.finish()
        
    except ValueError:
        await message.answer("❌ Неверный формат даты. Используйте ГГГГ-ММ-ДД")

# Список фильтров
@dp.message_handler(commands=['list'])
async def cmd_list(message: types.Message):
    conn = sqlite3.connect('filters.db')
    cur = conn.cursor()
    cur.execute("SELECT id, filter_type, last_change FROM filters WHERE user_id = ?", 
                (message.from_user.id,))
    filters = cur.fetchall()
    conn.close()

    if not filters:
        await message.answer("📭 Список фильтров пуст")
        return

    response = "📋 Ваши фильтры:\n\n"
    for f in filters:
        response += f"🆔 {f[0]}\nТип: {f[1]}\nЗаменен: {f[2]}\n\n"

    await message.answer(response)

# Удаление фильтра
@dp.message_handler(commands=['delete'])
async def cmd_delete(message: types.Message):
    conn = sqlite3.connect('filters.db')
    cur = conn.cursor()
    cur.execute("SELECT id, filter_type FROM filters WHERE user_id = ?", 
                (message.from_user.id,))
    filters = cur.fetchall()
    conn.close()

    if not filters:
        await message.answer("❌ Нет фильтров для удаления")
        return

    keyboard = types.InlineKeyboardMarkup()
    for f in filters:
        keyboard.add(types.InlineKeyboardButton(
            f"{f[1]} (ID: {f[0]})",
            callback_data=f"delete_{f[0]}"
        ))

    await message.answer("Выберите фильтр для удаления:", reply_markup=keyboard)

@dp.callback_query_handler(lambda c: c.data.startswith('delete_'))
async def process_delete(callback_query: types.CallbackQuery):
    filter_id = callback_query.data.split('_')[1]

    conn = sqlite3.connect('filters.db')
    cur = conn.cursor()
    cur.execute("DELETE FROM filters WHERE id = ? AND user_id = ?",
                (filter_id, callback_query.from_user.id))
    conn.commit()
    conn.close()

    await bot.answer_callback_query(callback_query.id, "Фильтр удален")
    await bot.send_message(callback_query.from_user.id, "✅ Фильтр успешно удален")

# Запуск бота
if __name__ == '__main__':
    init_db()
    executor.start_polling(dp, skip_updates=True)
