import logging
import sqlite3
import gspread
import os
import json
from datetime import datetime, timedelta
from aiogram import Bot, Dispatcher, types
from aiogram.contrib.fsm_storage.memory import MemoryStorage
from aiogram.dispatcher import FSMContext
from aiogram.dispatcher.filters.state import State, StatesGroup
from aiogram.utils import executor
from google.oauth2.service_account import Credentials
from google.auth.transport.requests import Request
from google.auth.exceptions import RefreshError

# Настройки
API_TOKEN = '8278600298:AAGPjUhyU5HxXOaLRvu-FSRldBW_UCmwOME'
ADMIN_ID = 5024165375

# Настройки Google Sheets
SCOPES = ['https://www.googleapis.com/auth/spreadsheets', 'https://www.googleapis.com/auth/drive']
SERVICE_ACCOUNT_FILE = 'service_account.json'
SPREADSHEET_NAME = 'Учет фильтров'

# Стандартные сроки службы фильтров
DEFAULT_LIFETIMES = {
    "угольный": 180,
    "механический": 90,
    "обратного осмоса": 365,
    "умягчитель": 180,
    "пост-фильтр": 180,
    "пре-фильтр": 90
}

# Инициализация бота
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
bot = Bot(token=API_TOKEN)
storage = MemoryStorage()
dp = Dispatcher(bot, storage=storage)

# Глобальная переменная для статуса Google Sheets
google_sheets_available = False
spreadsheet_url = ""

# Функция для диагностики проблем с Google Sheets
def diagnose_google_sheets_issue():
    issues = []
    
    # Проверка файла учетных данных
    if not os.path.exists(SERVICE_ACCOUNT_FILE):
        issues.append("❌ Файл service_account.json не найден")
        return issues
    
    try:
        with open(SERVICE_ACCOUNT_FILE, 'r') as f:
            creds_data = json.load(f)
        
        # Проверка обязательных полей
        required_fields = ['type', 'project_id', 'private_key_id', 'private_key', 'client_email']
        for field in required_fields:
            if field not in creds_data:
                issues.append(f"❌ В файле учетных данных отсутствует поле: {field}")
        
        if issues:
            return issues
            
        # Проверка формата private_key
        if not creds_data['private_key'].startswith('-----BEGIN PRIVATE KEY-----'):
            issues.append("❌ Неправильный формат приватного ключа")
            
    except json.JSONDecodeError:
        issues.append("❌ Файл service_account.json содержит невалидный JSON")
    except Exception as e:
        issues.append(f"❌ Ошибка при чтении файла учетных данных: {e}")
    
    return issues

# Альтернативный способ инициализации Google Sheets
def init_google_sheets_alternative():
    global google_sheets_available, spreadsheet_url
    
    try:
        # Диагностика проблем
        issues = diagnose_google_sheets_issue()
        if issues:
            for issue in issues:
                logging.error(issue)
            return None
        
        # Загрузка учетных данных
        creds = Credentials.from_service_account_file(SERVICE_ACCOUNT_FILE, scopes=SCOPES)
        
        # Обновление токена, если необходимо
        if creds.expired and creds.refresh_token:
            creds.refresh(Request())
        
        # Создание клиента
        client = gspread.authorize(creds)
        
        # Проверка доступности API
        client.list_spreadsheet_files()
        
        # Поиск или создание таблицы
        try:
            spreadsheet = client.open(SPREADSHEET_NAME)
        except gspread.SpreadsheetNotFound:
            # Создаем новую таблицу
            spreadsheet = client.create(SPREADSHEET_NAME)
            # Настраиваем доступ
            spreadsheet.share(None, perm_type='anyone', role='reader')
        
        # Получаем или создаем лист
        try:
            worksheet = spreadsheet.get_worksheet(0)
        except gspread.WorksheetNotFound:
            worksheet = spreadsheet.add_worksheet(title="Фильтры", rows=100, cols=10)
        
        # Настраиваем заголовки
        current_data = worksheet.get_all_values()
        if not current_data:
            headers = ['ID', 'User ID', 'Тип фильтра', 'Дата замены', 'Срок годности', 'Оставшееся дней', 'Статус']
            worksheet.append_row(headers)
            # Форматируем заголовки
            worksheet.format('A1:G1', {
                'textFormat': {'bold': True},
                'backgroundColor': {'red': 0.9, 'green': 0.9, 'blue': 0.9}
            })
        
        # Сохраняем URL для доступа
        global spreadsheet_url
        spreadsheet_url = f"https://docs.google.com/spreadsheets/d/{spreadsheet.id}"
        
        google_sheets_available = True
        logging.info(f"✅ Google Sheets успешно подключен: {spreadsheet_url}")
        return worksheet
        
    except Exception as e:
        logging.error(f"❌ Ошибка подключения к Google Sheets: {e}")
        google_sheets_available = False
        return None

# Упрощенная функция синхронизации
def simple_sync_to_google_sheets():
    if not google_sheets_available:
        return False
    
    try:
        worksheet = init_google_sheets_alternative()
        if not worksheet:
            return False
        
        # Получаем данные из БД
        conn = sqlite3.connect('filters.db')
        cur = conn.cursor()
        cur.execute("SELECT id, user_id, filter_type, last_change, expiry_date, lifetime_days FROM filters")
        filters = cur.fetchall()
        conn.close()
        
        if not filters:
            return True
        
        # Очищаем старые данные (кроме заголовка)
        all_data = worksheet.get_all_values()
        if len(all_data) > 1:
            worksheet.delete_rows(2, len(all_data))
        
        # Добавляем новые данные
        today = datetime.now().date()
        for f in filters:
            expiry_date = datetime.strptime(str(f[4]), '%Y-%m-%d').date()
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
            
            row_data = [
                f[0],  # ID
                f[1],  # User ID
                f[2],  # Тип фильтра
                str(f[3]),  # Дата замены
                str(f[4]),  # Срок годности
                days_until_expiry,  # Оставшееся дней
                status  # Статус
            ]
            worksheet.append_row(row_data)
        
        logging.info(f"✅ Данные синхронизированы: {len(filters)} записей")
        return True
        
    except Exception as e:
        logging.error(f"❌ Ошибка синхронизации: {e}")
        return False

# Инициализация БД
def init_db():
    try:
        conn = sqlite3.connect('filters.db')
        cur = conn.cursor()
        cur.execute('''CREATE TABLE IF NOT EXISTS filters (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER,
                    filter_type TEXT,
                    last_change DATE,
                    expiry_date DATE,
                    lifetime_days INTEGER)''')
        conn.commit()
        conn.close()
        logging.info("✅ База данных инициализирована")
    except Exception as e:
        logging.error(f"❌ Ошибка инициализации БД: {e}")

# States
class FilterStates(StatesGroup):
    waiting_filter_type = State()
    waiting_change_date = State()
    waiting_lifetime = State()

# Клавиатуры
def get_main_keyboard():
    keyboard = types.ReplyKeyboardMarkup(resize_keyboard=True)
    keyboard.add(types.KeyboardButton("📋 Список фильтров"))
    keyboard.add(types.KeyboardButton("➕ Добавить фильтр"))
    keyboard.add(types.KeyboardButton("🗑️ Удалить фильтр"))
    keyboard.add(types.KeyboardButton("🔔 Проверить сроки"))
    if google_sheets_available:
        keyboard.add(types.KeyboardButton("📊 Google Sheets"))
    return keyboard

def get_cancel_keyboard():
    keyboard = types.ReplyKeyboardMarkup(resize_keyboard=True)
    keyboard.add(types.KeyboardButton("❌ Отмена"))
    return keyboard

def get_lifetime_keyboard():
    keyboard = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    keyboard.add(
        types.KeyboardButton("90 дней"),
        types.KeyboardButton("180 дней"),
        types.KeyboardButton("365 дней")
    )
    keyboard.add(types.KeyboardButton("❌ Отмена"))
    return keyboard

def get_filter_type_keyboard():
    keyboard = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    keyboard.add(
        types.KeyboardButton("Угольный"),
        types.KeyboardButton("Механический"),
        types.KeyboardButton("Обратного осмоса"),
        types.KeyboardButton("Умягчитель")
    )
    keyboard.add(types.KeyboardButton("Другой тип"))
    keyboard.add(types.KeyboardButton("❌ Отмена"))
    return keyboard

# Команда start
@dp.message_handler(commands=['start'])
async def cmd_start(message: types.Message):
    status_text = "✅ Google Sheets подключен" if google_sheets_available else "❌ Google Sheets недоступен (работаем локально)"
    
    await message.answer(
        f"🤖 Бот для учета замены фильтров\n\n"
        f"{status_text}\n"
        f"Используйте кнопки ниже для управления:",
        reply_markup=get_main_keyboard()
    )

# Определение срока службы по типу фильтра
def get_lifetime_by_type(filter_type):
    filter_type_lower = filter_type.lower()
    for key, days in DEFAULT_LIFETIMES.items():
        if key in filter_type_lower:
            return days
    return 180

# Добавление фильтра
@dp.message_handler(lambda message: message.text == "➕ Добавить фильтр")
@dp.message_handler(commands=['add'])
async def cmd_add(message: types.Message):
    await FilterStates.waiting_filter_type.set()
    await message.answer(
        "Выберите тип фильтра:",
        reply_markup=get_filter_type_keyboard()
    )

@dp.message_handler(state=FilterStates.waiting_filter_type)
async def process_filter_type(message: types.Message, state: FSMContext):
    # Обработка отмены должна быть ПЕРВОЙ
    if message.text == "❌ Отмена":
        await state.finish()
        await message.answer("❌ Действие отменено", reply_markup=get_main_keyboard())
        return
        
    if message.text == "Другой тип":
        await message.answer("Введите свой вариант типа фильтра:", reply_markup=get_cancel_keyboard())
        return
    
    async with state.proxy() as data:
        data['filter_type'] = message.text
        data['lifetime'] = get_lifetime_by_type(message.text)

    await FilterStates.next()
    await message.answer(
        f"📅 Срок службы для '{message.text}': {data['lifetime']} дней\n"
        f"Введите дату последней замены (ГГГГ-ММ-ДД):",
        reply_markup=get_cancel_keyboard()
    )

@dp.message_handler(state=FilterStates.waiting_change_date)
async def process_date(message: types.Message, state: FSMContext):
    # Обработка отмены должна быть ПЕРВОЙ
    if message.text == "❌ Отмена":
        await state.finish()
        await message.answer("❌ Действие отменено", reply_markup=get_main_keyboard())
        return
        
    try:
        change_date = datetime.strptime(message.text, '%Y-%m-%d').date()
        
        async with state.proxy() as data:
            data['change_date'] = change_date
            
        await FilterStates.next()
        await message.answer(
            f"Установить срок службы {data['lifetime']} дней?\n"
            f"Или введите другое количество дней:",
            reply_markup=get_lifetime_keyboard()
        )
        
    except ValueError:
        await message.answer("❌ Неверный формат даты. Используйте ГГГГ-ММ-ДД:", reply_markup=get_cancel_keyboard())

@dp.message_handler(state=FilterStates.waiting_lifetime)
async def process_lifetime(message: types.Message, state: FSMContext):
    # Обработка отмены должна быть ПЕРВОЙ
    if message.text == "❌ Отмена":
        await state.finish()
        await message.answer("❌ Действие отменено", reply_markup=get_main_keyboard())
        return
        
    try:
        async with state.proxy() as data:
            change_date = data['change_date']
            filter_type = data['filter_type']
            
            if message.text.endswith("дней"):
                lifetime = int(message.text.split()[0])
            else:
                lifetime = int(message.text)
            
            expiry_date = change_date + timedelta(days=lifetime)
            
            # Сохраняем в БД
            conn = sqlite3.connect('filters.db')
            cur = conn.cursor()
            cur.execute('''INSERT INTO filters 
                        (user_id, filter_type, last_change, expiry_date, lifetime_days) 
                        VALUES (?, ?, ?, ?, ?)''',
                       (message.from_user.id, filter_type, change_date, expiry_date, lifetime))
            conn.commit()
            conn.close()

            # Пытаемся синхронизировать с Google Sheets
            sync_success = simple_sync_to_google_sheets()
            sync_status = "✅ Данные синхронизированы с Google Sheets" if sync_success else "⚠️ Данные сохранены локально"

            days_until_expiry = (expiry_date - datetime.now().date()).days
            status_icon = "🔔" if days_until_expiry <= 30 else "✅"
            
            await message.answer(
                f"✅ Фильтр успешно добавлен!\n\n"
                f"📊 Тип: {filter_type}\n"
                f"📅 Дата замены: {change_date}\n"
                f"⏰ Срок службы: {lifetime} дней\n"
                f"📅 Годен до: {expiry_date} {status_icon}\n"
                f"⏳ Осталось дней: {days_until_expiry}\n\n"
                f"{sync_status}",
                reply_markup=get_main_keyboard()
            )
            await state.finish()
            
    except ValueError:
        await message.answer("❌ Неверный формат. Введите количество дней:", reply_markup=get_lifetime_keyboard())

# Список фильтров
@dp.message_handler(lambda message: message.text == "📋 Список фильтров")
@dp.message_handler(commands=['list'])
async def cmd_list(message: types.Message):
    conn = sqlite3.connect('filters.db')
    cur = conn.cursor()
    cur.execute("SELECT id, filter_type, last_change, expiry_date, lifetime_days FROM filters WHERE user_id = ?", 
                (message.from_user.id,))
    filters = cur.fetchall()
    conn.close()

    if not filters:
        await message.answer("📭 Список фильтров пуст", reply_markup=get_main_keyboard())
        return

    response = "📋 Ваши фильтры:\n\n"
    today = datetime.now().date()
    
    for f in filters:
        expiry_date = datetime.strptime(str(f[3]), '%Y-%m-%d').date()
        days_until_expiry = (expiry_date - today).days
        
        if days_until_expiry <= 0:
            status_icon = "❌ ПРОСРОЧЕН"
        elif days_until_expiry <= 7:
            status_icon = "🔴 СРОЧНО"
        elif days_until_expiry <= 30:
            status_icon = "🟡 СКОРО"
        else:
            status_icon = "✅ НОРМА"
        
        response += (f"🆔 {f[0]}\n"
                    f"📊 Тип: {f[1]}\n"
                    f"📅 Заменен: {f[2]}\n"
                    f"⏰ Срок: {f[4]} дней\n"
                    f"📅 Годен до: {f[3]}\n"
                    f"⏳ Осталось дней: {days_until_expiry}\n"
                    f"📢 Статус: {status_icon}\n\n")

    await message.answer(response, reply_markup=get_main_keyboard())

# Проверка сроков
@dp.message_handler(lambda message: message.text == "🔔 Проверить сроки")
@dp.message_handler(commands=['check'])
async def cmd_check(message: types.Message):
    conn = sqlite3.connect('filters.db')
    cur = conn.cursor()
    cur.execute("SELECT filter_type, expiry_date FROM filters WHERE user_id = ?", 
                (message.from_user.id,))
    filters = cur.fetchall()
    conn.close()

    if not filters:
        await message.answer("📭 Нет фильтров для проверки", reply_markup=get_main_keyboard())
        return

    today = datetime.now().date()
    expired_filters = []
    expiring_soon = []
    
    for f in filters:
        expiry_date = datetime.strptime(str(f[1]), '%Y-%m-%d').date()
        days_until_expiry = (expiry_date - today).days
        
        if days_until_expiry <= 0:
            expired_filters.append(f"{f[0]} - просрочен {abs(days_until_expiry)} дней назад")
        elif days_until_expiry <= 30:
            expiring_soon.append(f"{f[0]} - осталось {days_until_expiry} дней")

    response = "🔔 Проверка сроков фильтров:\n\n"
    
    if expired_filters:
        response += "❌ ПРОСРОЧЕНЫ:\n" + "\n".join(expired_filters) + "\n\n"
    
    if expiring_soon:
        response += "🟡 СКОРО ИСТЕКАЮТ:\n" + "\n".join(expiring_soon) + "\n\n"
    
    if not expired_filters and not expiring_soon:
        response += "✅ Все фильтры в норме!\n"

    await message.answer(response, reply_markup=get_main_keyboard())

# Управление Google Sheets
@dp.message_handler(lambda message: message.text == "📊 Google Sheets")
@dp.message_handler(commands=['sheets'])
async def cmd_sheets(message: types.Message):
    if not google_sheets_available:
        # Диагностика проблемы
        issues = diagnose_google_sheets_issue()
        issues_text = "\n".join(issues) if issues else "Неизвестная ошибка"
        
        await message.answer(
            f"❌ Google Sheets недоступен\n\n"
            f"Проблемы:\n{issues_text}\n\n"
            f"Инструкция по настройке:\n"
            f"1. Создайте сервисный аккаунт в Google Cloud Console\n"
            f"2. Включите Google Sheets API\n"
            f"3. Скачайте JSON-ключ и переименуйте в 'service_account.json'\n"
            f"4. Положите файл в папку с ботом",
            reply_markup=get_main_keyboard()
        )
        return
    
    # Синхронизируем данные
    sync_success = simple_sync_to_google_sheets()
    
    if sync_success:
        await message.answer(
            f"✅ Google Sheets подключен!\n\n"
            f"📊 Ссылка на таблицу:\n{spreadsheet_url}\n\n"
            f"Данные автоматически синхронизируются при добавлении/удалении фильтров.",
            reply_markup=get_main_keyboard(),
            disable_web_page_preview=True
        )
    else:
        await message.answer(
            "❌ Ошибка синхронизации с Google Sheets\n"
            "Данные сохранены локально и будут синхронизированы позже.",
            reply_markup=get_main_keyboard()
        )

# Удаление фильтра
@dp.message_handler(lambda message: message.text == "🗑️ Удалить фильтр")
@dp.message_handler(commands=['delete'])
async def cmd_delete(message: types.Message):
    conn = sqlite3.connect('filters.db')
    cur = conn.cursor()
    cur.execute("SELECT id, filter_type, expiry_date FROM filters WHERE user_id = ?", 
                (message.from_user.id,))
    filters = cur.fetchall()
    conn.close()

    if not filters:
        await message.answer("❌ Нет фильтров для удаления", reply_markup=get_main_keyboard())
        return

    keyboard = types.InlineKeyboardMarkup(row_width=1)
    for f in filters:
        expiry_date = datetime.strptime(str(f[2]), '%Y-%m-%d').date()
        days_until_expiry = (expiry_date - datetime.now().date()).days
        
        status = "❌" if days_until_expiry <= 0 else "🟡" if days_until_expiry <= 30 else "✅"
        
        keyboard.add(types.InlineKeyboardButton(
            f"{status} {f[1]} (до {f[2]})",
            callback_data=f"delete_{f[0]}"
        ))
    
    keyboard.add(types.InlineKeyboardButton("❌ Отменить", callback_data="cancel_delete"))

    await message.answer("Выберите фильтр для удаления:", reply_markup=keyboard)

# Обработка отмены для всех состояний
@dp.message_handler(lambda message: message.text == "❌ Отмена", state='*')
async def cmd_cancel(message: types.Message, state: FSMContext):
    current_state = await state.get_state()
    if current_state is None:
        return
    
    await state.finish()
    await message.answer("❌ Действие отменено", reply_markup=get_main_keyboard())

# Обработка удаления
@dp.callback_query_handler(lambda c: c.data and c.data.startswith('delete_'))
async def process_delete(callback_query: types.CallbackQuery):
    filter_id = callback_query.data.split('_')[1]

    conn = sqlite3.connect('filters.db')
    cur = conn.cursor()
    
    cur.execute("SELECT filter_type, expiry_date FROM filters WHERE id = ? AND user_id = ?",
                (filter_id, callback_query.from_user.id))
    filter_info = cur.fetchone()
    
    if filter_info:
        cur.execute("DELETE FROM filters WHERE id = ? AND user_id = ?",
                    (filter_id, callback_query.from_user.id))
        conn.commit()
        conn.close()
        
        # Синхронизируем с Google Sheets
        simple_sync_to_google_sheets()
        
        await callback_query.message.edit_text(
            f"✅ Фильтр удален:\n📊 {filter_info[0]}\n📅 Срок истекал: {filter_info[1]}"
        )
    else:
        await callback_query.answer("Фильтр не найден", show_alert=True)
        conn.close()

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
    
    # Инициализируем Google Sheets
    init_google_sheets_alternative()
    
    if not google_sheets_available:
        logging.warning("Google Sheets недоступен. Бот будет работать только с локальной БД.")
    
    executor.start_polling(dp, skip_updates=True)
