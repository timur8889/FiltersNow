import logging
import sqlite3
from datetime import datetime
from aiogram import Bot, Dispatcher, types
from aiogram.contrib.fsm_storage.memory import MemoryStorage
from aiogram.dispatcher import FSMContext
from aiogram.dispatcher.filters.state import State, StatesGroup
from aiogram.utils import executor

# Настройки
API_TOKEN = '8278600298:AAGPjUhyU5HxXOaLRvu-FSRldBW_UCmwOME'  # Замените на реальный токен
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

# Клавиатуры
def get_main_keyboard():
    keyboard = types.ReplyKeyboardMarkup(resize_keyboard=True)
    keyboard.add(types.KeyboardButton("📋 Список фильтров"))
    keyboard.add(types.KeyboardButton("➕ Добавить фильтр"))
    keyboard.add(types.KeyboardButton("🗑️ Удалить фильтр"))
    return keyboard

def get_cancel_keyboard():
    keyboard = types.ReplyKeyboardMarkup(resize_keyboard=True)
    keyboard.add(types.KeyboardButton("❌ Отмена"))
    return keyboard

# Команда start
@dp.message_handler(commands=['start'])
async def cmd_start(message: types.Message):
    await message.answer(
        "🤖 Бот для учета замены фильтров\n\n"
        "Используйте кнопки ниже для управления фильтрами:",
        reply_markup=get_main_keyboard()
    )

# Обработка текстовых команд через кнопки
@dp.message_handler(lambda message: message.text == "📋 Список фильтров")
@dp.message_handler(commands=['list'])
async def cmd_list(message: types.Message):
    conn = sqlite3.connect('filters.db')
    cur = conn.cursor()
    cur.execute("SELECT id, filter_type, last_change FROM filters WHERE user_id = ?", 
                (message.from_user.id,))
    filters = cur.fetchall()
    conn.close()

    if not filters:
        await message.answer("📭 Список фильтров пуст", reply_markup=get_main_keyboard())
        return

    response = "📋 Ваши фильтры:\n\n"
    for f in filters:
        # Рассчитаем, сколько дней прошло с замены
        days_passed = (datetime.now().date() - datetime.strptime(str(f[2]), '%Y-%m-%d').date()).days
        response += f"🆔 {f[0]}\n📊 Тип: {f[1]}\n📅 Заменен: {f[2]}\n⏰ Прошло дней: {days_passed}\n\n"

    await message.answer(response, reply_markup=get_main_keyboard())

@dp.message_handler(lambda message: message.text == "➕ Добавить фильтр")
@dp.message_handler(commands=['add'])
async def cmd_add(message: types.Message):
    await FilterStates.waiting_filter_type.set()
    await message.answer(
        "Введите тип фильтра (например: 'Фильтр грубой очистки', 'Угольный фильтр' и т.д.):",
        reply_markup=get_cancel_keyboard()
    )

@dp.message_handler(lambda message: message.text == "🗑️ Удалить фильтр")
@dp.message_handler(commands=['delete'])
async def cmd_delete(message: types.Message):
    conn = sqlite3.connect('filters.db')
    cur = conn.cursor()
    cur.execute("SELECT id, filter_type, last_change FROM filters WHERE user_id = ?", 
                (message.from_user.id,))
    filters = cur.fetchall()
    conn.close()

    if not filters:
        await message.answer("❌ Нет фильтров для удаления", reply_markup=get_main_keyboard())
        return

    keyboard = types.InlineKeyboardMarkup(row_width=1)
    for f in filters:
        days_passed = (datetime.now().date() - datetime.strptime(str(f[2]), '%Y-%m-%d').date()).days
        keyboard.add(types.InlineKeyboardButton(
            f"🗑️ {f[1]} (заменен {f[2]}, {days_passed} дн.)",
            callback_data=f"delete_{f[0]}"
        ))
    
    keyboard.add(types.InlineKeyboardButton("❌ Отменить", callback_data="cancel_delete"))

    await message.answer("Выберите фильтр для удаления:", reply_markup=keyboard)

# Обработка отмены
@dp.message_handler(lambda message: message.text == "❌ Отмена", state='*')
async def cmd_cancel(message: types.Message, state: FSMContext):
    await state.finish()
    await message.answer("❌ Действие отменено", reply_markup=get_main_keyboard())

# Добавление фильтра - обработка типа
@dp.message_handler(state=FilterStates.waiting_filter_type)
async def process_filter_type(message: types.Message, state: FSMContext):
    async with state.proxy() as data:
        data['filter_type'] = message.text

    await FilterStates.next()
    await message.answer(
        "Введите дату последней замены в формате ГГГГ-ММ-ДД (например: 2024-01-15):",
        reply_markup=get_cancel_keyboard()
    )

# Добавление фильтра - обработка даты
@dp.message_handler(state=FilterStates.waiting_change_date)
async def process_date(message: types.Message, state: FSMContext):
    try:
        change_date = datetime.strptime(message.text, '%Y-%m-%d').date()
        
        async with state.proxy() as data:
            filter_type = data['filter_type']
        
        conn = sqlite3.connect('filters.db')
        cur = conn.cursor()
        cur.execute("INSERT INTO filters (user_id, filter_type, last_change) VALUES (?, ?, ?)",
                   (message.from_user.id, filter_type, change_date))
        conn.commit()
        conn.close()

        await message.answer(
            f"✅ Фильтр успешно добавлен!\n\n"
            f"📊 Тип: {filter_type}\n"
            f"📅 Дата замены: {change_date}",
            reply_markup=get_main_keyboard()
        )
        await state.finish()
        
    except ValueError:
        await message.answer(
            "❌ Неверный формат даты. Используйте ГГГГ-ММ-ДД (например: 2024-01-15):",
            reply_markup=get_cancel_keyboard()
        )

# Обработка удаления через inline кнопки
@dp.callback_query_handler(lambda c: c.data and c.data.startswith('delete_'))
async def process_delete(callback_query: types.CallbackQuery):
    filter_id = callback_query.data.split('_')[1]

    conn = sqlite3.connect('filters.db')
    cur = conn.cursor()
    
    # Сначала получим информацию о фильтре для сообщения
    cur.execute("SELECT filter_type, last_change FROM filters WHERE id = ? AND user_id = ?",
                (filter_id, callback_query.from_user.id))
    filter_info = cur.fetchone()
    
    if filter_info:
        cur.execute("DELETE FROM filters WHERE id = ? AND user_id = ?",
                    (filter_id, callback_query.from_user.id))
        conn.commit()
        
        await callback_query.message.edit_text(
            f"✅ Фильтр удален:\n📊 {filter_info[0]}\n📅 {filter_info[1]}"
        )
    else:
        await callback_query.answer("Фильтр не найден", show_alert=True)
    
    conn.close()

# Обработка отмены удаления
@dp.callback_query_handler(lambda c: c.data == "cancel_delete")
async def cancel_delete(callback_query: types.CallbackQuery):
    await callback_query.message.edit_text("❌ Удаление отменено")

# Обработка других сообщений
@dp.message_handler()
async def handle_other_messages(message: types.Message):
    await message.answer(
        "Используйте кнопки ниже для управления фильтрами:",
        reply_markup=get_main_keyboard()
    )

# Запуск бота
if __name__ == '__main__':
    init_db()
    executor.start_polling(dp, skip_updates=True)
