import logging
import sqlite3
import gspread
from datetime import datetime, timedelta
from aiogram import Bot, Dispatcher, types
from aiogram.contrib.fsm_storage.memory import MemoryStorage
from aiogram.dispatcher import FSMContext
from aiogram.dispatcher.filters.state import State, StatesGroup
from aiogram.utils import executor
from oauth2client.service_account import ServiceAccountCredentials
import os
import json

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

# Инициализация Google Sheets
def init_google_sheets():
    try:
        # Проверяем наличие файла с учетными данными
        if not os.path.exists('credentials.json'):
            return None
        
        # Настройка scope и авторизация
        scope = [
            'https://spreadsheets.google.com/feeds',
            'https://www.googleapis.com/auth/drive'
        ]
        
        creds = ServiceAccountCredentials.from_json_keyfile_name('credentials.json', scope)
        client = gspread.authorize(creds)
        return client
    except Exception as e:
        logging.error(f"Ошибка инициализации Google Sheets: {e}")
        return None

# States
class FilterStates(StatesGroup):
    waiting_filter_type = State()
    waiting_location = State()
    waiting_change_date = State()
    waiting_lifetime = State()

class MultipleFiltersStates(StatesGroup):
    waiting_filters_list = State()
    waiting_location = State()
    waiting_change_date = State()
    waiting_lifetime = State()

class EditFilterStates(StatesGroup):
    waiting_filter_selection = State()
    waiting_field_selection = State()
    waiting_new_value = State()

class ExcelStates(StatesGroup):
    waiting_spreadsheet_url = State()
    waiting_sheet_name = State()

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

def get_add_filter_keyboard():
    keyboard = types.ReplyKeyboardMarkup(resize_keyboard=True)
    keyboard.row(
        types.KeyboardButton("🔧 Один фильтр"),
        types.KeyboardButton("📦 Несколько фильтров")
    )
    keyboard.row(types.KeyboardButton("🔙 Главное меню"))
    return keyboard

def get_management_keyboard():
    keyboard = types.ReplyKeyboardMarkup(resize_keyboard=True)
    keyboard.row(
        types.KeyboardButton("✏️ Редактировать"),
        types.KeyboardButton("🗑️ Удалить")
    )
    keyboard.row(
        types.KeyboardButton("📊 Статистика"),
        types.KeyboardButton("📤 Экспорт в Excel")
    )
    keyboard.row(types.KeyboardButton("🔙 Главное меню"))
    return keyboard

def get_excel_keyboard():
    keyboard = types.ReplyKeyboardMarkup(resize_keyboard=True)
    keyboard.row(
        types.KeyboardButton("📤 Экспорт в Excel"),
        types.KeyboardButton("📥 Импорт из Excel")
    )
    keyboard.row(types.KeyboardButton("🔙 В управление"))
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
    keyboard.row(types.KeyboardButton("📍 Другое место"))
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

# Функция для преобразования даты из формата ДД.ММ.ГГ в ДД.ММ.ГГГГ
def parse_date(date_str):
    try:
        # Пробуем разные форматы дат
        formats = ['%d.%m.%y', '%d.%m.%Y', '%d-%m-%y', '%d-%m-%Y']
        
        for fmt in formats:
            try:
                return datetime.strptime(date_str, fmt).date()
            except ValueError:
                continue
        
        # Если ни один формат не подошел
        raise ValueError("Неверный формат даты")
    except Exception as e:
        raise ValueError(f"Ошибка преобразования даты: {e}")

# Функция для форматирования даты в красивый вид
def format_date_nice(date):
    return date.strftime('%d.%m.%y')

# Функция для экспорта данных в Google Sheets
async def export_to_google_sheets(user_id, spreadsheet_url=None, sheet_name="Фильтры"):
    try:
        client = init_google_sheets()
        if not client:
            return None, "❌ Google Sheets не настроен. Добавьте файл credentials.json"
        
        # Получаем данные из базы
        conn = sqlite3.connect('filters.db')
        cur = conn.cursor()
        cur.execute("SELECT filter_type, location, last_change, expiry_date, lifetime_days FROM filters WHERE user_id = ?", 
                    (user_id,))
        filters = cur.fetchall()
        conn.close()
        
        if not filters:
            return None, "❌ Нет данных для экспорта"
        
        if spreadsheet_url:
            # Открываем существующую таблицу
            try:
                spreadsheet = client.open_by_url(spreadsheet_url)
            except Exception as e:
                return None, f"❌ Не удалось открыть таблицу по ссылке: {e}"
        else:
            # Создаем новую таблицу
            spreadsheet = client.create(f"Фильтры пользователя {user_id}")
            # Даем доступ на чтение всем
            spreadsheet.share(None, perm_type='anyone', role='reader')
        
        # Работаем с листом
        try:
            worksheet = spreadsheet.worksheet(sheet_name)
        except gspread.exceptions.WorksheetNotFound:
            worksheet = spreadsheet.add_worksheet(title=sheet_name, rows=100, cols=10)
        
        # Подготавливаем заголовки
        headers = ["Тип фильтра", "Место установки", "Дата замены", "Срок службы (дни)", "Годен до", "Осталось дней", "Статус"]
        
        # Подготавливаем данные
        data = [headers]
        today = datetime.now().date()
        
        for filter_data in filters:
            expiry_date = datetime.strptime(str(filter_data[3]), '%Y-%m-%d').date()
            days_until_expiry = (expiry_date - today).days
            
            # Определяем статус
            if days_until_expiry <= 0:
                status = "ПРОСРОЧЕН"
            elif days_until_expiry <= 7:
                status = "СРОЧНО"
            elif days_until_expiry <= 30:
                status = "СКОРО"
            else:
                status = "НОРМА"
            
            last_change_nice = format_date_nice(datetime.strptime(str(filter_data[2]), '%Y-%m-%d').date())
            expiry_date_nice = format_date_nice(expiry_date)
            
            row = [
                filter_data[0],  # Тип фильтра
                filter_data[1],  # Место установки
                last_change_nice,  # Дата замены
                filter_data[4],  # Срок службы
                expiry_date_nice,  # Годен до
                days_until_expiry,  # Осталось дней
                status  # Статус
            ]
            data.append(row)
        
        # Очищаем лист и записываем данные
        worksheet.clear()
        worksheet.update('A1', data)
        
        # Форматируем заголовки
        worksheet.format('A1:G1', {
            'textFormat': {'bold': True},
            'backgroundColor': {'red': 0.9, 'green': 0.9, 'blue': 0.9}
        })
        
        # Автоподбор ширины колонок
        worksheet.columns_auto_resize(0, 6)
        
        return spreadsheet.url, f"✅ Данные успешно экспортированы!\n\n📊 Записей: {len(filters)}\n🔗 Ссылка: {spreadsheet.url}"
        
    except Exception as e:
        logging.error(f"Ошибка экспорта в Google Sheets: {e}")
        return None, f"❌ Ошибка экспорта: {e}"

# Функция для импорта данных из Google Sheets
async def import_from_google_sheets(user_id, spreadsheet_url, sheet_name="Фильтры"):
    try:
        client = init_google_sheets()
        if not client:
            return False, "❌ Google Sheets не настроен. Добавьте файл credentials.json"
        
        # Открываем таблицу
        try:
            spreadsheet = client.open_by_url(spreadsheet_url)
            worksheet = spreadsheet.worksheet(sheet_name)
        except Exception as e:
            return False, f"❌ Не удалось открыть таблицу: {e}"
        
        # Получаем все данные
        data = worksheet.get_all_values()
        
        if len(data) <= 1:  # Только заголовки
            return False, "❌ В таблице нет данных для импорта"
        
        # Парсим данные
        imported_count = 0
        errors = []
        
        conn = sqlite3.connect('filters.db')
        cur = conn.cursor()
        
        for i, row in enumerate(data[1:], start=2):  # Пропускаем заголовки
            try:
                if len(row) < 5:  # Минимальное количество полей
                    continue
                
                filter_type = row[0].strip()
                location = row[1].strip()
                last_change_str = row[2].strip()
                lifetime_days = int(row[3]) if row[3].strip() else 180
                
                if not filter_type or not location:
                    continue
                
                # Парсим дату
                try:
                    last_change = parse_date(last_change_str)
                except ValueError:
                    # Если дата не распознана, используем сегодняшнюю
                    last_change = datetime.now().date()
                
                # Рассчитываем дату истечения
                expiry_date = last_change + timedelta(days=lifetime_days)
                
                # Добавляем в базу
                cur.execute('''INSERT INTO filters 
                            (user_id, filter_type, location, last_change, expiry_date, lifetime_days) 
                            VALUES (?, ?, ?, ?, ?, ?)''',
                           (user_id, filter_type, location, last_change, expiry_date, lifetime_days))
                imported_count += 1
                
            except Exception as e:
                errors.append(f"Строка {i}: {e}")
        
        conn.commit()
        conn.close()
        
        result_message = f"✅ Импорт завершен!\n\n📥 Загружено записей: {imported_count}"
        if errors:
            result_message += f"\n\n❌ Ошибки ({len(errors)}):\n" + "\n".join(errors[:5])  # Показываем первые 5 ошибок
        
        return True, result_message
        
    except Exception as e:
        logging.error(f"Ошибка импорта из Google Sheets: {e}")
        return False, f"❌ Ошибка импорта: {e}"

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
        "• 📊 Детальная статистика\n"
        "• 📤 Экспорт/импорт в Excel",
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

# Обработка кнопки "Экспорт в Excel"
@dp.message_handler(lambda message: message.text == "📤 Экспорт в Excel")
async def cmd_excel_export(message: types.Message):
    await message.answer(
        "📤 <b>Экспорт в Google Sheets</b>\n\n"
        "💡 <i>Вы можете экспортировать данные в новую таблицу или в существующую</i>\n\n"
        "🔄 <b>Создаю новую таблицу...</b>",
        parse_mode='HTML',
        reply_markup=get_cancel_keyboard()
    )
    
    # Экспортируем в новую таблицу
    url, result_message = await export_to_google_sheets(message.from_user.id)
    
    if url:
        # Создаем инлайн-кнопку для открытия таблицы
        keyboard = types.InlineKeyboardMarkup()
        keyboard.add(types.InlineKeyboardButton("📊 Открыть таблицу", url=url))
        keyboard.add(types.InlineKeyboardButton("📥 Импорт из Excel", callback_data="import_excel"))
        
        await message.answer(
            result_message,
            parse_mode='HTML',
            reply_markup=keyboard
        )
    else:
        await message.answer(
            result_message,
            parse_mode='HTML',
            reply_markup=get_management_keyboard()
        )

# Обработка кнопки импорта
@dp.callback_query_handler(lambda c: c.data == "import_excel")
async def process_import_excel(callback_query: types.CallbackQuery):
    await ExcelStates.waiting_spreadsheet_url.set()
    await callback_query.message.answer(
        "📥 <b>Импорт из Google Sheets</b>\n\n"
        "📝 <b>Отправьте ссылку на Google Sheets таблицу:</b>\n\n"
        "💡 <i>Таблица должна быть доступна для редактирования</i>\n"
        "<i>Формат данных должен соответствовать экспорту</i>",
        parse_mode='HTML',
        reply_markup=get_cancel_keyboard()
    )

# Обработка ссылки на таблицу для импорта
@dp.message_handler(state=ExcelStates.waiting_spreadsheet_url)
async def process_spreadsheet_url(message: types.Message, state: FSMContext):
    if message.text == "❌ Отмена":
        await state.finish()
        await message.answer("🚫 Импорт отменен", reply_markup=get_management_keyboard())
        return
    
    spreadsheet_url = message.text.strip()
    
    # Проверяем, что это ссылка на Google Sheets
    if 'docs.google.com/spreadsheets' not in spreadsheet_url:
        await message.answer(
            "❌ <b>Неверная ссылка!</b>\n\n"
            "💡 <i>Отправьте корректную ссылку на Google Sheets таблицу</i>",
            parse_mode='HTML',
            reply_markup=get_cancel_keyboard()
        )
        return
    
    async with state.proxy() as data:
        data['spreadsheet_url'] = spreadsheet_url
    
    await ExcelStates.next()
    await message.answer(
        "📋 <b>Введите название листа:</b>\n\n"
        "💡 <i>По умолчанию: 'Фильтры'</i>\n"
        "<i>Оставьте пустым для использования листа по умолчанию</i>",
        parse_mode='HTML',
        reply_markup=get_cancel_keyboard()
    )

# Обработка названия листа для импорта
@dp.message_handler(state=ExcelStates.waiting_sheet_name)
async def process_sheet_name(message: types.Message, state: FSMContext):
    if message.text == "❌ Отмена":
        await state.finish()
        await message.answer("🚫 Импорт отменен", reply_markup=get_management_keyboard())
        return
    
    async with state.proxy() as data:
        spreadsheet_url = data['spreadsheet_url']
        sheet_name = message.text.strip() if message.text.strip() else "Фильтры"
    
    await message.answer(
        "🔄 <b>Импортирую данные...</b>",
        parse_mode='HTML'
    )
    
    # Выполняем импорт
    success, result_message = await import_from_google_sheets(
        message.from_user.id, 
        spreadsheet_url, 
        sheet_name
    )
    
    await message.answer(
        result_message,
        parse_mode='HTML',
        reply_markup=get_management_keyboard()
    )
    await state.finish()

# Определение срока службы по типу фильтра
def get_lifetime_by_type(filter_type):
    filter_type_lower = filter_type.lower()
    for key, days in DEFAULT_LIFETIMES.items():
        if key in filter_type_lower:
            return days
    return 180

# Добавление фильтра - выбор типа добавления
@dp.message_handler(lambda message: message.text == "✨ Добавить фильтр")
@dp.message_handler(commands=['add'])
async def cmd_add(message: types.Message):
    await message.answer(
        "🔧 <b>Выберите тип добавления:</b>\n\n"
        "💡 <i>Можно добавить один фильтр или сразу несколько</i>",
        parse_mode='HTML',
        reply_markup=get_add_filter_keyboard()
    )

# Обработка выбора типа добавления
@dp.message_handler(lambda message: message.text in ["🔧 Один фильтр", "📦 Несколько фильтров"])
async def process_add_type(message: types.Message):
    if message.text == "🔧 Один фильтр":
        await FilterStates.waiting_filter_type.set()
        await message.answer(
            "🔧 <b>Выберите тип фильтра:</b>\n\n"
            "💡 <i>Или укажите свой вариант</i>",
            parse_mode='HTML',
            reply_markup=get_filter_type_keyboard()
        )
    elif message.text == "📦 Несколько фильтров":
        await MultipleFiltersStates.waiting_filters_list.set()
        await message.answer(
            "📦 <b>Добавление нескольких фильтров</b>\n\n"
            "📝 <b>Введите типы фильтров через запятую или с новой строки:</b>\n\n"
            "💡 <i>Примеры:</i>\n"
            "<i>• Магистральный SL10, Магистральный SL20, Гейзер</i>\n"
            "<i>• Магистральный SL10\nМагистральный SL20\nГейзер</i>\n\n"
            "🔧 <i>Каждый фильтр будет добавлен с одинаковыми настройками</i>",
            parse_mode='HTML',
            reply_markup=get_cancel_keyboard()
        )

# Добавление одного фильтра
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
        "📍 <b>Укажите место установки фильтра:</b>\n\n"
        "💡 <i>Нажмите кнопку '📍 Другое место' для ввода своего варианта</i>",
        parse_mode='HTML',
        reply_markup=get_location_keyboard()
    )

# Добавление нескольких фильтров - обработка списка
@dp.message_handler(state=MultipleFiltersStates.waiting_filters_list)
async def process_multiple_filters_list(message: types.Message, state: FSMContext):
    if message.text == "❌ Отмена":
        await state.finish()
        await message.answer("🚫 Добавление фильтров отменено", reply_markup=get_main_keyboard())
        return
    
    # Разделяем ввод на отдельные фильтры
    filter_text = message.text
    filters_list = []
    
    # Пробуем разделить по запятым
    if ',' in filter_text:
        filters_list = [f.strip() for f in filter_text.split(',') if f.strip()]
    else:
        # Или по переносам строк
        filters_list = [f.strip() for f in filter_text.split('\n') if f.strip()]
    
    if not filters_list:
        await message.answer(
            "❌ <b>Не удалось распознать фильтры</b>\n\n"
            "💡 <i>Введите типы фильтров через запятую или с новой строки</i>",
            parse_mode='HTML',
            reply_markup=get_cancel_keyboard()
        )
        return
    
    # Сохраняем список фильтров
    async with state.proxy() as data:
        data['filters_list'] = filters_list
        # Устанавливаем срок службы по первому фильтру (можно будет изменить позже)
        data['lifetime'] = get_lifetime_by_type(filters_list[0])
    
    await MultipleFiltersStates.next()
    await message.answer(
        f"📦 <b>Будет добавлено фильтров:</b> {len(filters_list)}\n\n"
        f"🔧 <b>Список фильтров:</b>\n" + "\n".join([f"• {f}" for f in filters_list]) + "\n\n"
        f"📍 <b>Укажите место установки для всех фильтров:</b>\n\n"
        f"💡 <i>Все фильтры будут установлены в одном месте</i>",
        parse_mode='HTML',
        reply_markup=get_location_keyboard()
    )

# Общий обработчик для места установки (и для одного, и для нескольких фильтров)
@dp.message_handler(state=[FilterStates.waiting_location, MultipleFiltersStates.waiting_location])
async def process_location(message: types.Message, state: FSMContext):
    if message.text == "❌ Отмена":
        await state.finish()
        await message.answer("🚫 Добавление отменено", reply_markup=get_main_keyboard())
        return
        
    if message.text == "📍 Другое место":
        await message.answer(
            "📍 <b>Введите место установки фильтра:</b>\n\n"
            "💡 <i>Например: Кухня, Ванная комната, Под раковиной, Гостиная, Офис, Балкон, Гараж и т.д.</i>",
            parse_mode='HTML',
            reply_markup=get_cancel_keyboard()
        )
        return
    
    current_state = await state.get_state()
    
    if current_state == "FilterStates:waiting_location":
        # Для одного фильтра
        async with state.proxy() as data:
            data['location'] = message.text

        await FilterStates.next()
        today_nice = format_date_nice(datetime.now().date())
        await message.answer(
            f"📅 <b>Дата последней замены</b>\n\n"
            f"🔧 <i>Фильтр:</i> {data['filter_type']}\n"
            f"📍 <i>Место:</i> {data['location']}\n\n"
            f"📝 <b>Введите дату замены в формате ДД.ММ.ГГ:</b>\n"
            f"<i>Например: {today_nice}</i>",
            parse_mode='HTML',
            reply_markup=get_cancel_keyboard()
        )
    else:
        # Для нескольких фильтров
        async with state.proxy() as data:
            data['location'] = message.text

        await MultipleFiltersStates.next()
        today_nice = format_date_nice(datetime.now().date())
        await message.answer(
            f"📅 <b>Дата последней замены для всех фильтров</b>\n\n"
            f"📍 <i>Место для всех фильтров:</i> {data['location']}\n\n"
            f"📝 <b>Введите дату замены в формате ДД.ММ.ГГ:</b>\n"
            f"<i>Например: {today_nice}</i>",
            parse_mode='HTML',
            reply_markup=get_cancel_keyboard()
        )

# Общий обработчик для даты замены
@dp.message_handler(state=[FilterStates.waiting_change_date, MultipleFiltersStates.waiting_change_date])
async def process_date(message: types.Message, state: FSMContext):
    if message.text == "❌ Отмена":
        await state.finish()
        await message.answer("🚫 Добавление отменено", reply_markup=get_main_keyboard())
        return
        
    try:
        # Преобразуем дату из формата ДД.ММ.ГГ
        change_date = parse_date(message.text)
        
        current_state = await state.get_state()
        
        if current_state == "FilterStates:waiting_change_date":
            # Для одного фильтра
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
        else:
            # Для нескольких фильтров
            async with state.proxy() as data:
                data['change_date'] = change_date
                
            await MultipleFiltersStates.next()
            await message.answer(
                f"⏱️ <b>Срок службы для всех фильтров</b>\n\n"
                f"📅 <i>Рекомендуемый срок:</i> {data['lifetime']} дней\n\n"
                f"🔄 <b>Выберите срок службы:</b>",
                parse_mode='HTML',
                reply_markup=get_lifetime_keyboard()
            )
        
    except ValueError as e:
        today_nice = format_date_nice(datetime.now().date())
        await message.answer(
            "❌ <b>Неверный формат даты!</b>\n\n"
            "📝 <i>Используйте формат ДД.ММ.ГГ</i>\n"
            f"<i>Пример: {today_nice}</i>",
            parse_mode='HTML',
            reply_markup=get_cancel_keyboard()
        )

# Общий обработчик для срока службы
@dp.message_handler(state=[FilterStates.waiting_lifetime, MultipleFiltersStates.waiting_lifetime])
async def process_lifetime(message: types.Message, state: FSMContext):
    if message.text == "❌ Отмена":
        await state.finish()
        await message.answer("🚫 Добавление отменено", reply_markup=get_main_keyboard())
        return
        
    try:
        current_state = await state.get_state()
        
        if current_state == "FilterStates:waiting_lifetime":
            # Для одного фильтра
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
                
                change_date_nice = format_date_nice(change_date)
                expiry_date_nice = format_date_nice(expiry_date)
                
                await message.answer(
                    f"{status_emoji} <b>ФИЛЬТР ДОБАВЛЕН!</b>\n\n"
                    f"🔧 <b>Тип:</b> {filter_type}\n"
                    f"📍 <b>Место:</b> {location}\n"
                    f"📅 <b>Заменен:</b> {change_date_nice}\n"
                    f"⏱️ <b>Срок службы:</b> {lifetime} дней\n"
                    f"📅 <b>Годен до:</b> {expiry_date_nice}\n"
                    f"⏳ <b>Осталось дней:</b> {days_until_expiry}\n"
                    f"📊 <b>Статус:</b> {status_icon}",
                    parse_mode='HTML',
                    reply_markup=get_main_keyboard()
                )
                await state.finish()
                
        else:
            # Для нескольких фильтров
            async with state.proxy() as data:
                change_date = data['change_date']
                location = data['location']
                filters_list = data['filters_list']
                
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
                
                # Добавляем все фильтры в базу данных
                conn = sqlite3.connect('filters.db')
                cur = conn.cursor()
                
                added_count = 0
                results = []
                
                for filter_type in filters_list:
                    expiry_date = change_date + timedelta(days=lifetime)
                    
                    cur.execute('''INSERT INTO filters 
                                (user_id, filter_type, location, last_change, expiry_date, lifetime_days) 
                                VALUES (?, ?, ?, ?, ?, ?)''',
                               (message.from_user.id, filter_type, location, change_date, expiry_date, lifetime))
                    added_count += 1
                    
                    days_until_expiry = (expiry_date - datetime.now().date()).days
                    results.append({
                        'type': filter_type,
                        'expiry_date': expiry_date,
                        'days_until_expiry': days_until_expiry
                    })
                
                conn.commit()
                conn.close()
                
                change_date_nice = format_date_nice(change_date)
                
                # Формируем сообщение с результатами
                response = f"✅ <b>УСПЕШНО ДОБАВЛЕНО {added_count} ФИЛЬТРОВ!</b>\n\n"
                response += f"📍 <b>Место:</b> {location}\n"
                response += f"📅 <b>Дата замены:</b> {change_date_nice}\n"
                response += f"⏱️ <b>Срок службы:</b> {lifetime} дней\n\n"
                
                response += "<b>📋 Добавленные фильтры:</b>\n"
                for i, result in enumerate(results, 1):
                    expiry_date_nice = format_date_nice(result['expiry_date'])
                    status_icon = "🔴" if result['days_until_expiry'] <= 0 else "🟡" if result['days_until_expiry'] <= 30 else "✅"
                    response += f"{status_icon} {result['type']} (до {expiry_date_nice})\n"
                
                await message.answer(
                    response,
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

# Остальной код (список фильтров, проверка сроков, редактирование, удаление, статистика) остается без изменений...

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
            status_icon = "🟠"
            status_text = "СКОРО"
        else:
            status_icon = "✅"
            status_text = "НОРМА"
        
        last_change_nice = format_date_nice(datetime.strptime(str(f[3]), '%Y-%m-%d').date())
        expiry_date_nice = format_date_nice(expiry_date)
        
        response += (
            f"{status_icon} <b>ФИЛЬТР #{f[0]}</b>\n"
            f"   🔧 {f[1]}\n"
            f"   📍 {f[2]}\n"
            f"   📅 Заменен: {last_change_nice}\n"
            f"   ⏱️ Срок: {f[5]} дн.\n"
            f"   🗓️ Годен до: {expiry_date_nice}\n"
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
        
        expiry_date_nice = format_date_nice(expiry_date)
        
        if days_until_expiry <= 0:
            expired_filters.append(f"🔴 {f[0]} ({f[1]}) - просрочен {abs(days_until_expiry)} дн. назад (до {expiry_date_nice})")
        elif days_until_expiry <= 7:
            expiring_soon.append(f"🟡 {f[0]} ({f[1]}) - осталось {days_until_expiry} дн. (до {expiry_date_nice})")
        elif days_until_expiry <= 30:
            warning_filters.append(f"🟠 {f[0]} ({f[1]}) - осталось {days_until_expiry} дн. (до {expiry_date_nice})")

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

# Редактирование фильтра - выбор фильтра
@dp.message_handler(lambda message: message.text == "✏️ Редактировать")
@dp.message_handler(commands=['edit'])
async def cmd_edit(message: types.Message):
    conn = sqlite3.connect('filters.db')
    cur = conn.cursor()
    cur.execute("SELECT id, filter_type, location, expiry_date FROM filters WHERE user_id = ?", 
                (message.from_user.id,))
    filters = cur.fetchall()
    conn.close()

    if not filters:
        await message.answer(
            "❌ <b>Нет фильтров для редактирования</b>",
            parse_mode='HTML',
            reply_markup=get_management_keyboard()
        )
        return

    keyboard = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=1)
    for f in filters:
        expiry_date = datetime.strptime(str(f[3]), '%Y-%m-%d').date()
        days_until_expiry = (expiry_date - datetime.now().date()).days
        
        if days_until_expiry <= 0:
            status = "🔴"
        elif days_until_expiry <= 7:
            status = "🟡"
        elif days_until_expiry <= 30:
            status = "🟠"
        else:
            status = "✅"
        
        # Создаем более читаемую кнопку
        button_text = f"{status} {f[1]} - {f[2]}"
        keyboard.add(types.KeyboardButton(button_text))
    
    keyboard.add(types.KeyboardButton("🔙 Главное меню"))

    await EditFilterStates.waiting_filter_selection.set()
    await message.answer(
        "✏️ <b>Выберите фильтр для редактирования:</b>\n\n"
        "💡 <i>Статусы:\n🔴 - просрочен\n🟡 - срочно заменить\n🟠 - скоро истекает\n✅ - в норме</i>",
        parse_mode='HTML',
        reply_markup=keyboard
    )

# Редактирование фильтра - выбор фильтра из списка
@dp.message_handler(state=EditFilterStates.waiting_filter_selection)
async def process_edit_filter_selection(message: types.Message, state: FSMContext):
    if message.text == "🔙 Главное меню":
        await state.finish()
        await message.answer(
            "🏠 <b>Главное меню</b>",
            parse_mode='HTML',
            reply_markup=get_main_keyboard()
        )
        return

    # Получаем текст выбранной кнопки (без эмодзи статуса)
    filter_text = message.text[2:].strip()  # Убираем эмодзи статуса и пробел
    
    try:
        conn = sqlite3.connect('filters.db')
        cur = conn.cursor()
        
        # Разделяем текст на тип фильтра и место
        if " - " in filter_text:
            parts = filter_text.split(" - ")
            filter_type = parts[0].strip()
            location = parts[1].strip()
            
            cur.execute("SELECT id FROM filters WHERE user_id = ? AND filter_type = ? AND location = ?", 
                       (message.from_user.id, filter_type, location))
            result = cur.fetchone()
            
            if result:
                filter_id = result[0]
                
                # Получаем полную информацию о фильтре
                cur.execute("SELECT * FROM filters WHERE id = ?", (filter_id,))
                filter_data = cur.fetchone()
                
                async with state.proxy() as data:
                    data['edit_filter_id'] = filter_id
                    data['edit_filter_data'] = filter_data
                
                # Показываем информацию о фильтре
                expiry_date = datetime.strptime(str(filter_data[5]), '%Y-%m-%d').date()
                days_until_expiry = (expiry_date - datetime.now().date()).days
                
                if days_until_expiry <= 0:
                    status_icon = "🔴"
                    status_text = "ПРОСРОЧЕН"
                elif days_until_expiry <= 7:
                    status_icon = "🟡"
                    status_text = "СРОЧНО"
                elif days_until_expiry <= 30:
                    status_icon = "🟠"
                    status_text = "СКОРО"
                else:
                    status_icon = "✅"
                    status_text = "НОРМА"
                
                last_change_nice = format_date_nice(datetime.strptime(str(filter_data[4]), '%Y-%m-%d').date())
                expiry_date_nice = format_date_nice(expiry_date)
                
                await message.answer(
                    f"✏️ <b>РЕДАКТИРОВАНИЕ ФИЛЬТРА</b>\n\n"
                    f"{status_icon} <b>Текущие данные:</b>\n"
                    f"🔧 <b>Тип:</b> {filter_data[2]}\n"
                    f"📍 <b>Место:</b> {filter_data[3]}\n"
                    f"📅 <b>Дата замены:</b> {last_change_nice}\n"
                    f"⏱️ <b>Срок службы:</b> {filter_data[6]} дней\n"
                    f"🗓️ <b>Годен до:</b> {expiry_date_nice}\n"
                    f"⏳ <b>Осталось дней:</b> {days_until_expiry}\n"
                    f"📊 <b>Статус:</b> {status_text}\n\n"
                    f"📝 <b>Выберите поле для редактирования:</b>",
                    parse_mode='HTML',
                    reply_markup=get_edit_field_keyboard()
                )
                await EditFilterStates.next()
            else:
                await message.answer(
                    "❌ <b>Фильтр не найден</b>\n\n"
                    "💡 <i>Попробуйте выбрать фильтр из списка еще раз</i>",
                    parse_mode='HTML',
                    reply_markup=get_management_keyboard()
                )
                await state.finish()
        else:
            await message.answer(
                "❌ <b>Не удалось распознать фильтр</b>\n\n"
                "💡 <i>Пожалуйста, выберите фильтр из списка кнопок</i>",
                parse_mode='HTML',
                reply_markup=get_management_keyboard()
            )
            await state.finish()
        
        conn.close()
        
    except Exception as e:
        logging.error(f"Ошибка при выборе фильтра для редактирования: {e}")
        await message.answer(
            "❌ <b>Произошла ошибка при выборе фильтра</b>\n\n"
            "💡 <i>Попробуйте еще раз</i>",
            parse_mode='HTML',
            reply_markup=get_management_keyboard()
        )
        await state.finish()

# Редактирование фильтра - обработка выбора поля
@dp.message_handler(state=EditFilterStates.waiting_field_selection)
async def process_edit_field_selection(message: types.Message, state: FSMContext):
    if message.text == "↩️ Назад к фильтрам":
        await EditFilterStates.waiting_filter_selection.set()
        await cmd_edit(message)
        return

    async with state.proxy() as data:
        filter_id = data['edit_filter_id']
        filter_data = data['edit_filter_data']
    
    field_mapping = {
        "🔧 Тип": "filter_type",
        "📍 Место": "location", 
        "📅 Дата замены": "last_change",
        "⏱️ Срок службы": "lifetime_days"
    }
    
    if message.text in field_mapping:
        field = field_mapping[message.text]
        async with state.proxy() as data:
            data['edit_field'] = field
        
        # Запрашиваем новое значение в зависимости от поля
        if field == "filter_type":
            await message.answer(
                f"🔧 <b>Текущий тип:</b> {filter_data[2]}\n\n"
                f"📝 <b>Введите новый тип фильтра:</b>",
                parse_mode='HTML',
                reply_markup=get_filter_type_keyboard()
            )
        elif field == "location":
            await message.answer(
                f"📍 <b>Текущее место:</b> {filter_data[3]}\n\n"
                f"📝 <b>Введите новое место установки:</b>",
                parse_mode='HTML',
                reply_markup=get_location_keyboard()
            )
        elif field == "last_change":
            last_change_nice = format_date_nice(datetime.strptime(str(filter_data[4]), '%Y-%m-%d').date())
            today_nice = format_date_nice(datetime.now().date())
            await message.answer(
                f"📅 <b>Текущая дата замены:</b> {last_change_nice}\n\n"
                f"📝 <b>Введите новую дату замены в формате ДД.ММ.ГГ:</b>\n"
                f"<i>Например: {today_nice}</i>",
                parse_mode='HTML',
                reply_markup=get_cancel_keyboard()
            )
        elif field == "lifetime_days":
            await message.answer(
                f"⏱️ <b>Текущий срок службы:</b> {filter_data[6]} дней\n\n"
                f"📝 <b>Введите новый срок службы (в днях):</b>",
                parse_mode='HTML',
                reply_markup=get_lifetime_keyboard()
            )
        
        await EditFilterStates.next()
    else:
        await message.answer(
            "❌ <b>Пожалуйста, выберите поле из списка</b>",
            parse_mode='HTML',
            reply_markup=get_edit_field_keyboard()
        )

# Редактирование фильтра - обработка нового значения
@dp.message_handler(state=EditFilterStates.waiting_new_value)
async def process_edit_new_value(message: types.Message, state: FSMContext):
    if message.text == "❌ Отмена":
        await state.finish()
        await message.answer(
            "🚫 <b>Редактирование отменено</b>",
            parse_mode='HTML',
            reply_markup=get_management_keyboard()
        )
        return

    async with state.proxy() as data:
        filter_id = data['edit_filter_id']
        field = data['edit_field']
        old_filter_data = data['edit_filter_data']
    
    try:
        conn = sqlite3.connect('filters.db')
        cur = conn.cursor()
        
        if field == "filter_type":
            new_value = message.text
            cur.execute("UPDATE filters SET filter_type = ? WHERE id = ?", 
                       (new_value, filter_id))
            
            await message.answer(
                f"✅ <b>Тип фильтра успешно изменен!</b>\n\n"
                f"🔧 <b>Было:</b> {old_filter_data[2]}\n"
                f"🔧 <b>Стало:</b> {new_value}",
                parse_mode='HTML',
                reply_markup=get_management_keyboard()
            )
            
        elif field == "location":
            new_value = message.text
            cur.execute("UPDATE filters SET location = ? WHERE id = ?", 
                       (new_value, filter_id))
            
            await message.answer(
                f"✅ <b>Место установки успешно изменено!</b>\n\n"
                f"📍 <b>Было:</b> {old_filter_data[3]}\n"
                f"📍 <b>Стало:</b> {new_value}",
                parse_mode='HTML',
                reply_markup=get_management_keyboard()
            )
            
        elif field == "last_change":
            try:
                # Преобразуем дату из формата ДД.ММ.ГГ
                new_date = parse_date(message.text)
                
                # Получаем текущий срок службы
                cur.execute("SELECT lifetime_days FROM filters WHERE id = ?", (filter_id,))
                lifetime = cur.fetchone()[0]
                
                # Пересчитываем дату истечения
                new_expiry = new_date + timedelta(days=lifetime)
                
                cur.execute("UPDATE filters SET last_change = ?, expiry_date = ? WHERE id = ?",
                           (new_date, new_expiry, filter_id))
                
                old_date_nice = format_date_nice(datetime.strptime(str(old_filter_data[4]), '%Y-%m-%d').date())
                new_date_nice = format_date_nice(new_date)
                new_expiry_nice = format_date_nice(new_expiry)
                
                await message.answer(
                    f"✅ <b>Дата замены успешно изменена!</b>\n\n"
                    f"📅 <b>Было:</b> {old_date_nice}\n"
                    f"📅 <b>Стало:</b> {new_date_nice}\n"
                    f"🗓️ <b>Новая дата истечения:</b> {new_expiry_nice}",
                    parse_mode='HTML',
                    reply_markup=get_management_keyboard()
                )
                
            except ValueError:
                today_nice = format_date_nice(datetime.now().date())
                await message.answer(
                    "❌ <b>Неверный формат даты!</b>\n\n"
                    "📝 <i>Используйте формат ДД.ММ.ГГ</i>\n"
                    f"<i>Пример: {today_nice}</i>",
                    parse_mode='HTML',
                    reply_markup=get_cancel_keyboard()
                )
                return
                
        elif field == "lifetime_days":
            try:
                if message.text.startswith("3️⃣") or message.text.startswith("6️⃣") or message.text.startswith("1️⃣"):
                    new_lifetime = int(message.text.split()[1])
                elif message.text == "📅 Другое количество":
                    await message.answer(
                        "🔢 <b>Введите количество дней:</b>\n"
                        "<i>Например: 120, 200, 400 и т.д.</i>",
                        parse_mode='HTML',
                        reply_markup=get_cancel_keyboard()
                    )
                    return
                else:
                    new_lifetime = int(message.text)
                
                # Получаем текущую дату замены
                cur.execute("SELECT last_change FROM filters WHERE id = ?", (filter_id,))
                last_change = cur.fetchone()[0]
                
                # Пересчитываем дату истечения
                new_expiry = last_change + timedelta(days=new_lifetime)
                
                cur.execute("UPDATE filters SET lifetime_days = ?, expiry_date = ? WHERE id = ?",
                           (new_lifetime, new_expiry, filter_id))
                
                new_expiry_nice = format_date_nice(new_expiry)
                
                await message.answer(
                    f"✅ <b>Срок службы успешно изменен!</b>\n\n"
                    f"⏱️ <b>Было:</b> {old_filter_data[6]} дней\n"
                    f"⏱️ <b>Стало:</b> {new_lifetime} дней\n"
                    f"🗓️ <b>Новая дата истечения:</b> {new_expiry_nice}",
                    parse_mode='HTML',
                    reply_markup=get_management_keyboard()
                )
                
            except ValueError:
                await message.answer(
                    "❌ <b>Неверный формат!</b>\n\n"
                    "🔢 <i>Введите количество дней числом</i>\n"
                    "<i>Например: 90, 180, 365</i>",
                    parse_mode='HTML',
                    reply_markup=get_lifetime_keyboard()
                )
                return
        
        conn.commit()
        
        # Получаем обновленные данные фильтра
        cur.execute("SELECT * FROM filters WHERE id = ?", (filter_id,))
        updated_filter = cur.fetchone()
        
        # Показываем обновленную информацию
        expiry_date = datetime.strptime(str(updated_filter[5]), '%Y-%m-%d').date()
        days_until_expiry = (expiry_date - datetime.now().date()).days
        
        if days_until_expiry <= 0:
            status_icon = "🔴"
            status_text = "ПРОСРОЧЕН"
        elif days_until_expiry <= 7:
            status_icon = "🟡"
            status_text = "СРОЧНО"
        elif days_until_expiry <= 30:
            status_icon = "🟠"
            status_text = "СКОРО"
        else:
            status_icon = "✅"
            status_text = "НОРМА"
        
        last_change_nice = format_date_nice(datetime.strptime(str(updated_filter[4]), '%Y-%m-%d').date())
        expiry_date_nice = format_date_nice(expiry_date)
        
        await message.answer(
            f"📋 <b>ОБНОВЛЕННАЯ ИНФОРМАЦИЯ:</b>\n\n"
            f"{status_icon} <b>Фильтр #{filter_id}</b>\n"
            f"🔧 <b>Тип:</b> {updated_filter[2]}\n"
            f"📍 <b>Место:</b> {updated_filter[3]}\n"
            f"📅 <b>Заменен:</b> {last_change_nice}\n"
            f"⏱️ <b>Срок:</b> {updated_filter[6]} дн.\n"
            f"🗓️ <b>Годен до:</b> {expiry_date_nice}\n"
            f"⏳ <b>Осталось:</b> {days_until_expiry} дн.\n"
            f"📊 <b>Статус:</b> {status_text}",
            parse_mode='HTML'
        )
        
        conn.close()
        await state.finish()
        
    except Exception as e:
        logging.error(f"Ошибка при редактировании фильтра: {e}")
        await message.answer(
            "❌ <b>Произошла ошибка при редактировании фильтра</b>\n\n"
            "💡 <i>Попробуйте еще раз</i>",
            parse_mode='HTML',
            reply_markup=get_management_keyboard()
        )
        await state.finish()

# Удаление фильтра
@dp.message_handler(lambda message: message.text == "🗑️ Удалить")
@dp.message_handler(commands=['delete'])
async def cmd_delete(message: types.Message):
    conn = sqlite3.connect('filters.db')
    cur = conn.cursor()
    cur.execute("SELECT id, filter_type, location, expiry_date FROM filters WHERE user_id = ?", 
                (message.from_user.id,))
    filters = cur.fetchall()
    conn.close()

    if not filters:
        await message.answer(
            "❌ <b>Нет фильтров для удаления</b>",
            parse_mode='HTML',
            reply_markup=get_management_keyboard()
        )
        return

    keyboard = types.InlineKeyboardMarkup(row_width=1)
    for f in filters:
        expiry_date = datetime.strptime(str(f[3]), '%Y-%m-%d').date()
        days_until_expiry = (expiry_date - datetime.now().date()).days
        
        if days_until_expiry <= 0:
            status = "🔴"
        elif days_until_expiry <= 30:
            status = "🟡"
        else:
            status = "✅"
        
        expiry_date_nice = format_date_nice(expiry_date)
        
        keyboard.add(types.InlineKeyboardButton(
            f"{status} {f[1]} | {f[2]} | до {expiry_date_nice}",
            callback_data=f"select_delete_{f[0]}"
        ))
    
    keyboard.add(types.InlineKeyboardButton("🔙 Назад", callback_data="back_to_management"))

    await message.answer(
        "🗑️ <b>Выберите фильтр для удаления:</b>\n\n"
        "💡 <i>Статусы: 🔴 - просрочен, 🟡 - скоро истекает, ✅ - в норме</i>",
        parse_mode='HTML',
        reply_markup=keyboard
    )

# Подтверждение удаления
@dp.callback_query_handler(lambda c: c.data and c.data.startswith('select_delete_'))
async def confirm_delete(callback_query: types.CallbackQuery):
    filter_id = callback_query.data.split('_')[2]

    conn = sqlite3.connect('filters.db')
    cur = conn.cursor()
    
    cur.execute("SELECT filter_type, location, expiry_date FROM filters WHERE id = ? AND user_id = ?",
                (filter_id, callback_query.from_user.id))
    filter_info = cur.fetchone()
    conn.close()
    
    if filter_info:
        expiry_date = datetime.strptime(str(filter_info[2]), '%Y-%m-%d').date()
        days_until_expiry = (expiry_date - datetime.now().date()).days
        
        expiry_date_nice = format_date_nice(expiry_date)
        status_text = "🔴 ПРОСРОЧЕН" if days_until_expiry <= 0 else "🟡 Истекает скоро" if days_until_expiry <= 30 else "✅ В норме"
        
        await callback_query.message.edit_text(
            f"⚠️ <b>Подтверждение удаления</b>\n\n"
            f"🔧 <b>Тип:</b> {filter_info[0]}\n"
            f"📍 <b>Место:</b> {filter_info[1]}\n"
            f"📅 <b>Срок годности:</b> {expiry_date_nice}\n"
            f"⏳ <b>Осталось дней:</b> {days_until_expiry}\n"
            f"📊 <b>Статус:</b> {status_text}\n\n"
            f"❓ <b>Вы уверены, что хотите удалить этот фильтр?</b>",
            parse_mode='HTML',
            reply_markup=get_confirmation_keyboard(filter_id)
        )
    else:
        await callback_query.answer("❌ Фильтр не найден", show_alert=True)

# Фактическое удаление фильтра
@dp.callback_query_handler(lambda c: c.data and c.data.startswith('confirm_delete_'))
async def process_delete(callback_query: types.CallbackQuery):
    filter_id = callback_query.data.split('_')[2]

    conn = sqlite3.connect('filters.db')
    cur = conn.cursor()
    
    cur.execute("SELECT filter_type, location, expiry_date FROM filters WHERE id = ? AND user_id = ?",
                (filter_id, callback_query.from_user.id))
    filter_info = cur.fetchone()
    
    if filter_info:
        cur.execute("DELETE FROM filters WHERE id = ? AND user_id = ?",
                    (filter_id, callback_query.from_user.id))
        conn.commit()
        conn.close()
        
        expiry_date_nice = format_date_nice(datetime.strptime(str(filter_info[2]), '%Y-%m-%d').date())
        
        await callback_query.message.edit_text(
            f"✅ <b>Фильтр удален:</b>\n\n"
            f"🔧 <b>Тип:</b> {filter_info[0]}\n"
            f"📍 <b>Место:</b> {filter_info[1]}\n"
            f"📅 <b>Срок истекал:</b> {expiry_date_nice}",
            parse_mode='HTML'
        )
    else:
        await callback_query.answer("❌ Фильтр не найден", show_alert=True)
        conn.close()

# Отмена удаления
@dp.callback_query_handler(lambda c: c.data == "cancel_delete")
async def cancel_delete(callback_query: types.CallbackQuery):
    await callback_query.message.edit_text(
        "❌ <b>Удаление отменено</b>\n\n"
        "💡 <i>Фильтр не был удален</i>",
        parse_mode='HTML'
    )

@dp.callback_query_handler(lambda c: c.data == "back_to_management")
async def back_to_management(callback_query: types.CallbackQuery):
    await callback_query.message.edit_text(
        "⚙️ <b>Управление фильтрами</b>\n\n"
        "Выберите действие:",
        parse_mode='HTML'
    )

@dp.callback_query_handler(lambda c: c.data == "back_to_main")
async def back_to_main(callback_query: types.CallbackQuery):
    await callback_query.message.edit_text(
        "🔙 Возвращаемся в главное меню",
        parse_mode='HTML'
    )

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
    stats = cur.fetchall()
    
    cur.execute('''SELECT filter_type, COUNT(*) as count 
                   FROM filters WHERE user_id = ? GROUP BY filter_type''', 
                (message.from_user.id,))
    type_stats = cur.fetchall()
    
    conn.close()
    
    response = "📊 <b>СТАТИСТИКА ФИЛЬТРОВ</b>\n\n"
    response += f"📦 <b>Всего фильтров:</b> {stats[0]}\n"
    response += f"🔴 <b>Просрочено:</b> {stats[1]}\n"
    response += f"🟡 <b>Срочно заменить:</b> {stats[2]}\n"
    response += f"🟠 <b>Скоро истекают:</b> {stats[3]}\n\n"
    
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
