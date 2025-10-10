import logging
import sqlite3
from datetime import datetime, timedelta
from aiogram import Bot, Dispatcher, types
from aiogram.contrib.fsm_storage.memory import MemoryStorage
from aiogram.dispatcher import FSMContext
from aiogram.dispatcher.filters.state import State, StatesGroup
from aiogram.utils import executor

# Настройки
API_TOKEN = '8278600298:AAGPjUhyU5HxXOaLRvu-FSRldBW_UCmwOME'
ADMIN_ID = 5024165375

# Стандартные сроки службы фильтров
DEFAULT_LIFETIMES = {
    "магистральный sl10": 180,
    "магистральный sl20": 180,
    "гейзер": 365,
    "аквафор": 365
}

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
                location TEXT,
                last_change DATE,
                expiry_date DATE,
                lifetime_days INTEGER)''')
    conn.commit()
    conn.close()

# States
class FilterStates(StatesGroup):
    waiting_filter_type = State()
    waiting_location = State()
    waiting_change_date = State()
    waiting_lifetime = State()

class EditFilterStates(StatesGroup):
    waiting_filter_selection = State()
    waiting_field_selection = State()
    waiting_new_value = State()

# Клавиатуры
def get_main_keyboard():
    keyboard = types.ReplyKeyboardMarkup(resize_keyboard=True)
    keyboard.row(
        types.KeyboardButton("📋 Мои фильтры"),
        types.KeyboardButton("✨ Добавить фильтр")
    )
    keyboard.row(
        types.KeyboardButton("⏳ Сроки замены"),
        types.KeyboardButton("⚙️ Управление")
    )
    return keyboard

def get_management_keyboard():
    keyboard = types.ReplyKeyboardMarkup(resize_keyboard=True)
    keyboard.row(
        types.KeyboardButton("✏️ Редактировать"),
        types.KeyboardButton("🗑️ Удалить")
    )
    keyboard.row(
        types.KeyboardButton("📊 Статистика"),
        types.KeyboardButton("🔙 Главное меню")
    )
    return keyboard

def get_cancel_keyboard():
    keyboard = types.ReplyKeyboardMarkup(resize_keyboard=True)
    keyboard.add(types.KeyboardButton("❌ Отмена"))
    return keyboard

def get_back_keyboard():
    keyboard = types.ReplyKeyboardMarkup(resize_keyboard=True)
    keyboard.add(types.KeyboardButton("↩️ Назад"))
    return keyboard

def get_filter_type_keyboard():
    keyboard = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    keyboard.row(
        types.KeyboardButton("🔧 Магистральный SL10"),
        types.KeyboardButton("🔧 Магистральный SL20")
    )
    keyboard.row(
        types.KeyboardButton("💧 Гейзер"),
        types.KeyboardButton("💧 Аквафор")
    )
    keyboard.row(types.KeyboardButton("📝 Другой тип"))
    keyboard.row(types.KeyboardButton("❌ Отмена"))
    return keyboard

def get_location_keyboard():
    keyboard = types.ReplyKeyboardMarkup(resize_keyboard=True)
    keyboard.row(types.KeyboardButton("🏷️ Указать место установки"))
    keyboard.row(types.KeyboardButton("❌ Отмена"))
    return keyboard

def get_lifetime_keyboard():
    keyboard = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=3)
    keyboard.row(
        types.KeyboardButton("3️⃣ 90 дней"),
        types.KeyboardButton("6️⃣ 180 дней"),
        types.KeyboardButton("1️⃣ 365 дней")
    )
    keyboard.row(types.KeyboardButton("📅 Другое количество"))
    keyboard.row(types.KeyboardButton("❌ Отмена"))
    return keyboard

def get_edit_field_keyboard():
    keyboard = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    keyboard.row(
        types.KeyboardButton("🔧 Тип"),
        types.KeyboardButton("📍 Место")
    )
    keyboard.row(
        types.KeyboardButton("📅 Дата замены"),
        types.KeyboardButton("⏱️ Срок службы")
    )
    keyboard.row(types.KeyboardButton("↩️ Назад к фильтрам"))
    return keyboard

def get_confirmation_keyboard(filter_id):
    keyboard = types.InlineKeyboardMarkup(row_width=2)
    keyboard.add(
        types.InlineKeyboardButton("✅ Подтвердить удаление", callback_data=f"confirm_delete_{filter_id}"),
        types.InlineKeyboardButton("❌ Отменить", callback_data="cancel_delete")
    )
    return keyboard

def get_quick_actions_keyboard():
    keyboard = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    keyboard.row(
        types.KeyboardButton("🔄 Заменить фильтр"),
        types.KeyboardButton("📊 Быстрая статистика")
    )
    keyboard.row(types.KeyboardButton("🔙 Главное меню"))
    return keyboard

# Команда start
@dp.message_handler(commands=['start'])
async def cmd_start(message: types.Message):
    await message.answer(
        "🌟 <b>Фильтр-Трекер</b> 🤖\n\n"
        "💧 <i>Умный помощник для своевременной замены фильтров</i>\n\n"
        "📦 <b>Основные возможности:</b>\n"
        "• 📋 Просмотр всех ваших фильтров\n"
        "• ✨ Добавление новых фильтров\n"
        "• ⏳ Контроль сроков замены\n"
        "• ⚙️ Полное управление базой\n"
        "• 📊 Детальная статистика",
        parse_mode='HTML',
        reply_markup=get_main_keyboard()
    )

# Обработка кнопки "Главное меню"
@dp.message_handler(lambda message: message.text == "🔙 Главное меню")
async def cmd_back(message: types.Message):
    await message.answer(
        "🏠 <b>Главное меню</b>\n\n"
        "Выберите нужный раздел:",
        parse_mode='HTML',
        reply_markup=get_main_keyboard()
    )

# Обработка кнопки "Управление"
@dp.message_handler(lambda message: message.text == "⚙️ Управление")
async def cmd_management(message: types.Message):
    await message.answer(
        "🛠️ <b>Центр управления фильтрами</b>\n\n"
        "Выберите действие:",
        parse_mode='HTML',
        reply_markup=get_management_keyboard()
    )

# Определение срока службы по типу фильтра
def get_lifetime_by_type(filter_type):
    filter_type_lower = filter_type.lower()
    for key, days in DEFAULT_LIFETIMES.items():
        if key in filter_type_lower:
            return days
    return 180

# Добавление фильтра
@dp.message_handler(lambda message: message.text == "✨ Добавить фильтр")
@dp.message_handler(commands=['add'])
async def cmd_add(message: types.Message):
    await FilterStates.waiting_filter_type.set()
    await message.answer(
        "🔧 <b>Выберите тип фильтра:</b>\n\n"
        "💡 <i>Или укажите свой вариант</i>",
        parse_mode='HTML',
        reply_markup=get_filter_type_keyboard()
    )

@dp.message_handler(state=FilterStates.waiting_filter_type)
async def process_filter_type(message: types.Message, state: FSMContext):
    if message.text == "❌ Отмена":
        await state.finish()
        await message.answer("🚫 Добавление фильтра отменено", reply_markup=get_main_keyboard())
        return
        
    if message.text == "📝 Другой тип":
        await message.answer(
            "📝 <b>Введите тип фильтра:</b>\n"
            "<i>Например: Угольный фильтр, Механический фильтр и т.д.</i>",
            parse_mode='HTML',
            reply_markup=get_cancel_keyboard()
        )
        return
    
    async with state.proxy() as data:
        data['filter_type'] = message.text
        data['lifetime'] = get_lifetime_by_type(message.text)

    await FilterStates.next()
    await message.answer(
        "📍 <b>Укажите место установки:</b>\n\n"
        "🏠 <i>Нажмите кнопку ниже для ввода</i>",
        parse_mode='HTML',
        reply_markup=get_location_keyboard()
    )

@dp.message_handler(state=FilterStates.waiting_location)
async def process_location(message: types.Message, state: FSMContext):
    if message.text == "❌ Отмена":
        await state.finish()
        await message.answer("🚫 Добавление фильтра отменено", reply_markup=get_main_keyboard())
        return
        
    if message.text == "🏷️ Указать место установки":
        await message.answer(
            "🏷️ <b>Введите место установки:</b>\n\n"
            "💡 <i>Примеры: Кухня, Ванная комната, Под раковиной, Гостиная, Офис и т.д.</i>",
            parse_mode='HTML',
            reply_markup=get_cancel_keyboard()
        )
        return
    
    # Если пользователь ввел текст напрямую
    async with state.proxy() as data:
        data['location'] = message.text

    await FilterStates.next()
    await message.answer(
        f"📅 <b>Дата последней замены</b>\n\n"
        f"🔧 <i>Фильтр:</i> {data['filter_type']}\n"
        f"📍 <i>Место:</i> {data['location']}\n\n"
        f"📝 <b>Введите дату замены в формате ГГГГ-ММ-ДД:</b>\n"
        f"<i>Например: {datetime.now().strftime('%Y-%m-%d')}</i>",
        parse_mode='HTML',
        reply_markup=get_cancel_keyboard()
    )

@dp.message_handler(state=FilterStates.waiting_change_date)
async def process_date(message: types.Message, state: FSMContext):
    if message.text == "❌ Отмена":
        await state.finish()
        await message.answer("🚫 Добавление фильтра отменено", reply_markup=get_main_keyboard())
        return
        
    try:
        change_date = datetime.strptime(message.text, '%Y-%m-%d').date()
        
        async with state.proxy() as data:
            data['change_date'] = change_date
            
        await FilterStates.next()
        await message.answer(
            f"⏱️ <b>Срок службы фильтра</b>\n\n"
            f"📅 <i>Рекомендуемый срок:</i> {data['lifetime']} дней\n\n"
            f"🔄 <b>Выберите срок службы:</b>",
            parse_mode='HTML',
            reply_markup=get_lifetime_keyboard()
        )
        
    except ValueError:
        await message.answer(
            "❌ <b>Неверный формат даты!</b>\n\n"
            "📝 <i>Используйте формат ГГГГ-ММ-ДД</i>\n"
            f"<i>Пример: {datetime.now().strftime('%Y-%m-%d')}</i>",
            parse_mode='HTML',
            reply_markup=get_cancel_keyboard()
        )

@dp.message_handler(state=FilterStates.waiting_lifetime)
async def process_lifetime(message: types.Message, state: FSMContext):
    if message.text == "❌ Отмена":
        await state.finish()
        await message.answer("🚫 Добавление фильтра отменено", reply_markup=get_main_keyboard())
        return
        
    try:
        async with state.proxy() as data:
            change_date = data['change_date']
            filter_type = data['filter_type']
            location = data['location']
            
            if message.text.startswith("3️⃣") or message.text.startswith("6️⃣") or message.text.startswith("1️⃣"):
                lifetime = int(message.text.split()[1])
            elif message.text == "📅 Другое количество":
                await message.answer(
                    "🔢 <b>Введите количество дней:</b>\n"
                    "<i>Например: 120, 200, 400 и т.д.</i>",
                    parse_mode='HTML',
                    reply_markup=get_cancel_keyboard()
                )
                return
            else:
                lifetime = int(message.text)
            
            expiry_date = change_date + timedelta(days=lifetime)
            
            conn = sqlite3.connect('filters.db')
            cur = conn.cursor()
            cur.execute('''INSERT INTO filters 
                        (user_id, filter_type, location, last_change, expiry_date, lifetime_days) 
                        VALUES (?, ?, ?, ?, ?, ?)''',
                       (message.from_user.id, filter_type, location, change_date, expiry_date, lifetime))
            conn.commit()
            conn.close()

            days_until_expiry = (expiry_date - datetime.now().date()).days
            
            # Определяем статус с эмодзи
            if days_until_expiry <= 0:
                status_icon = "🔴 ПРОСРОЧЕН"
                status_emoji = "🚨"
            elif days_until_expiry <= 7:
                status_icon = "🟡 СРОЧНО ЗАМЕНИТЬ"
                status_emoji = "⚠️"
            elif days_until_expiry <= 30:
                status_icon = "🔔 СКОРО ЗАМЕНИТЬ"
                status_emoji = "🔔"
            else:
                status_icon = "✅ В НОРМЕ"
                status_emoji = "✅"
            
            await message.answer(
                f"{status_emoji} <b>ФИЛЬТР ДОБАВЛЕН!</b>\n\n"
                f"🔧 <b>Тип:</b> {filter_type}\n"
                f"📍 <b>Место:</b> {location}\n"
                f"📅 <b>Заменен:</b> {change_date}\n"
                f"⏱️ <b>Срок службы:</b> {lifetime} дней\n"
                f"📅 <b>Годен до:</b> {expiry_date}\n"
                f"⏳ <b>Осталось дней:</b> {days_until_expiry}\n"
                f"📊 <b>Статус:</b> {status_icon}",
                parse_mode='HTML',
                reply_markup=get_main_keyboard()
            )
            await state.finish()
            
    except ValueError:
        await message.answer(
            "❌ <b>Неверный формат!</b>\n\n"
            "🔢 <i>Введите количество дней числом</i>\n"
            "<i>Например: 90, 180, 365</i>",
            parse_mode='HTML',
            reply_markup=get_lifetime_keyboard()
        )

# Список фильтров
@dp.message_handler(lambda message: message.text == "📋 Мои фильтры")
@dp.message_handler(commands=['list'])
async def cmd_list(message: types.Message):
    conn = sqlite3.connect('filters.db')
    cur = conn.cursor()
    cur.execute("SELECT id, filter_type, location, last_change, expiry_date, lifetime_days FROM filters WHERE user_id = ?", 
                (message.from_user.id,))
    filters = cur.fetchall()
    conn.close()

    if not filters:
        await message.answer(
            "📭 <b>Список фильтров пуст</b>\n\n"
            "💫 <i>Добавьте первый фильтр с помощью кнопки '✨ Добавить фильтр'</i>",
            parse_mode='HTML',
            reply_markup=get_main_keyboard()
        )
        return

    response = "📋 <b>ВАШИ ФИЛЬТРЫ</b>\n\n"
    today = datetime.now().date()
    
    for f in filters:
        expiry_date = datetime.strptime(str(f[4]), '%Y-%m-%d').date()
        days_until_expiry = (expiry_date - today).days
        
        # Определяем иконку статуса
        if days_until_expiry <= 0:
            status_icon = "🔴"
            status_text = "ПРОСРОЧЕН"
        elif days_until_expiry <= 7:
            status_icon = "🟡"
            status_text = "СРОЧНО"
        elif days_until_expiry <= 30:
            status_icon = "🔔"
            status_text = "СКОРО"
        else:
            status_icon = "✅"
            status_text = "НОРМА"
        
        response += (
            f"{status_icon} <b>ФИЛЬТР #{f[0]}</b>\n"
            f"   🔧 {f[1]}\n"
            f"   📍 {f[2]}\n"
            f"   📅 Заменен: {f[3]}\n"
            f"   ⏱️ Срок: {f[5]} дн.\n"
            f"   🗓️ Годен до: {f[4]}\n"
            f"   ⏳ Осталось: {days_until_expiry} дн.\n"
            f"   📊 Статус: {status_text}\n\n"
        )

    await message.answer(response, parse_mode='HTML', reply_markup=get_main_keyboard())

# Проверка сроков
@dp.message_handler(lambda message: message.text == "⏳ Сроки замены")
@dp.message_handler(commands=['check'])
async def cmd_check(message: types.Message):
    conn = sqlite3.connect('filters.db')
    cur = conn.cursor()
    cur.execute("SELECT filter_type, location, expiry_date FROM filters WHERE user_id = ?", 
                (message.from_user.id,))
    filters = cur.fetchall()
    conn.close()

    if not filters:
        await message.answer(
            "📭 <b>Нет фильтров для проверки</b>\n\n"
            "💫 <i>Добавьте фильтры для отслеживания сроков</i>",
            parse_mode='HTML',
            reply_markup=get_main_keyboard()
        )
        return

    today = datetime.now().date()
    expired_filters = []
    expiring_soon = []
    warning_filters = []
    
    for f in filters:
        expiry_date = datetime.strptime(str(f[2]), '%Y-%m-%d').date()
        days_until_expiry = (expiry_date - today).days
        
        if days_until_expiry <= 0:
            expired_filters.append(f"🔴 {f[0]} ({f[1]}) - просрочен {abs(days_until_expiry)} дн. назад")
        elif days_until_expiry <= 7:
            expiring_soon.append(f"🟡 {f[0]} ({f[1]}) - осталось {days_until_expiry} дн.")
        elif days_until_expiry <= 30:
            warning_filters.append(f"🔔 {f[0]} ({f[1]}) - осталось {days_until_expiry} дн.")

    response = "⏳ <b>КОНТРОЛЬ СРОКОВ</b>\n\n"
    
    if expired_filters:
        response += "🚨 <b>ПРОСРОЧЕНЫ:</b>\n" + "\n".join(expired_filters) + "\n\n"
    
    if expiring_soon:
        response += "⚠️ <b>СРОЧНО ИСТЕКАЮТ:</b>\n" + "\n".join(expiring_soon) + "\n\n"
    
    if warning_filters:
        response += "🔔 <b>СКОРО ИСТЕКАЮТ:</b>\n" + "\n".join(warning_filters) + "\n\n"
    
    if not expired_filters and not expiring_soon and not warning_filters:
        response += "✅ <b>ВСЕ ФИЛЬТРЫ В НОРМЕ!</b>\n\n"
        response += "💫 <i>Следующая проверка через 30+ дней</i>"

    await message.answer(response, parse_mode='HTML', reply_markup=get_main_keyboard())

# Остальной код остается таким же, но с обновленными эмоджи в сообщениях...

# Статистика
@dp.message_handler(lambda message: message.text == "📊 Статистика")
async def cmd_stats(message: types.Message):
    conn = sqlite3.connect('filters.db')
    cur = conn.cursor()
    
    cur.execute('''SELECT 
                    COUNT(*) as total,
                    SUM(CASE WHEN expiry_date < date('now') THEN 1 ELSE 0 END) as expired,
                    SUM(CASE WHEN expiry_date BETWEEN date('now') AND date('now', '+7 days') THEN 1 ELSE 0 END) as urgent,
                    SUM(CASE WHEN expiry_date BETWEEN date('now', '+8 days') AND date('now', '+30 days') THEN 1 ELSE 0 END) as soon
                 FROM filters WHERE user_id = ?''', (message.from_user.id,))
    stats = cur.fetchone()
    
    cur.execute('''SELECT filter_type, COUNT(*) as count 
                   FROM filters WHERE user_id = ? GROUP BY filter_type''', 
                (message.from_user.id,))
    type_stats = cur.fetchall()
    
    conn.close()
    
    response = "📊 <b>СТАТИСТИКА ФИЛЬТРОВ</b>\n\n"
    response += f"📦 <b>Всего фильтров:</b> {stats[0]}\n"
    response += f"🔴 <b>Просрочено:</b> {stats[1]}\n"
    response += f"🟡 <b>Срочно заменить:</b> {stats[2]}\n"
    response += f"🔔 <b>Скоро истекают:</b> {stats[3]}\n\n"
    
    if type_stats:
        response += "<b>📈 По типам:</b>\n"
        for filter_type, count in type_stats:
            response += f"   • {filter_type}: {count} шт.\n"
    
    await message.answer(response, parse_mode='HTML', reply_markup=get_management_keyboard())

# Обработка отмены
@dp.message_handler(lambda message: message.text == "❌ Отмена", state='*')
async def cmd_cancel(message: types.Message, state: FSMContext):
    current_state = await state.get_state()
    if current_state is None:
        return
    
    await state.finish()
    await message.answer("🚫 Действие отменено", reply_markup=get_main_keyboard())

# Обработка других сообщений
@dp.message_handler()
async def handle_other_messages(message: types.Message):
    await message.answer(
        "🌟 <b>Фильтр-Трекер</b> 🤖\n\n"
        "💧 <i>Выберите действие с помощью кнопок ниже:</i>",
        parse_mode='HTML',
        reply_markup=get_main_keyboard()
    )

# Запуск бота
if __name__ == '__main__':
    init_db()
    executor.start_polling(dp, skip_updates=True)
