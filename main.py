import logging
import sqlite3
import gspread
import os
import json
import asyncio
import io
from datetime import datetime, timedelta
from aiogram import Bot, Dispatcher, types
from aiogram.contrib.fsm_storage.memory import MemoryStorage
from aiogram.dispatcher import FSMContext
from aiogram.dispatcher.filters.state import State, StatesGroup
from aiogram.utils import executor
from google.oauth2.service_account import Credentials
from google.auth.transport.requests import Request
from google.auth.exceptions import RefreshError
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

# Настройки
API_TOKEN = '8278600298:AAGPjUhyU5HxXOaLRvu-FSRldBW_UCmwOME'
ADMIN_ID = 5024165375  # Замените на ваш ID

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
scheduler = AsyncIOScheduler()

# Глобальные переменные
google_sheets_available = False
spreadsheet_url = ""

# States
class FilterStates(StatesGroup):
    waiting_filter_type = State()
    waiting_change_date = State()
    waiting_lifetime = State()

class EditFilterStates(StatesGroup):
    waiting_filter_selection = State()
    waiting_field_selection = State()
    waiting_new_value = State()

# Функция для диагностики проблем с Google Sheets
def diagnose_google_sheets_issue():
    issues = []
    
    if not os.path.exists(SERVICE_ACCOUNT_FILE):
        issues.append("❌ Файл service_account.json не найден")
        return issues
    
    try:
        with open(SERVICE_ACCOUNT_FILE, 'r') as f:
            creds_data = json.load(f)
        
        required_fields = ['type', 'project_id', 'private_key_id', 'private_key', 'client_email']
        for field in required_fields:
            if field not in creds_data:
                issues.append(f"❌ В файле учетных данных отсутствует поле: {field}")
        
        if issues:
            return issues
            
        if not creds_data['private_key'].startswith('-----BEGIN PRIVATE KEY-----'):
            issues.append("❌ Неправильный формат приватного ключа")
            
    except json.JSONDecodeError:
        issues.append("❌ Файл service_account.json содержит невалидный JSON")
    except Exception as e:
        issues.append(f"❌ Ошибка при чтении файла учетных данных: {e}")
    
    return issues

# Инициализация Google Sheets
def init_google_sheets_alternative():
    global google_sheets_available, spreadsheet_url
    
    try:
        issues = diagnose_google_sheets_issue()
        if issues:
            for issue in issues:
                logging.error(issue)
            return None
        
        creds = Credentials.from_service_account_file(SERVICE_ACCOUNT_FILE, scopes=SCOPES)
        
        if creds.expired and creds.refresh_token:
            creds.refresh(Request())
        
        client = gspread.authorize(creds)
        client.list_spreadsheet_files()
        
        try:
            spreadsheet = client.open(SPREADSHEET_NAME)
        except gspread.SpreadsheetNotFound:
            spreadsheet = client.create(SPREADSHEET_NAME)
            spreadsheet.share(None, perm_type='anyone', role='reader')
        
        try:
            worksheet = spreadsheet.get_worksheet(0)
        except gspread.WorksheetNotFound:
            worksheet = spreadsheet.add_worksheet(title="Фильтры", rows=100, cols=10)
        
        current_data = worksheet.get_all_values()
        if not current_data:
            headers = ['ID', 'User ID', 'Тип фильтра', 'Дата замены', 'Срок годности', 'Оставшееся дней', 'Статус']
            worksheet.append_row(headers)
            worksheet.format('A1:G1', {
                'textFormat': {'bold': True},
                'backgroundColor': {'red': 0.9, 'green': 0.9, 'blue': 0.9}
            })
        
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
        
        conn = sqlite3.connect('filters.db')
        cur = conn.cursor()
        cur.execute("SELECT id, user_id, filter_type, last_change, expiry_date, lifetime_days FROM filters")
        filters = cur.fetchall()
        conn.close()
        
        if not filters:
            return True
        
        all_data = worksheet.get_all_values()
        if len(all_data) > 1:
            worksheet.delete_rows(2, len(all_data))
        
        today = datetime.now().date()
        for f in filters:
            expiry_date = datetime.strptime(str(f[4]), '%Y-%m-%d').date()
            days_until_expiry = (expiry_date - today).days
            
            if days_until_expiry <= 0:
                status = "ПРОСРОЧЕН"
            elif days_until_expiry <= 7:
                status = "СРОЧНО"
            elif days_until_expiry <= 30:
                status = "СКОРО"
            else:
                status = "НОРМА"
            
            row_data = [
                f[0], f[1], f[2], str(f[3]), str(f[4]), days_until_expiry, status
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

# Клавиатуры
def get_main_keyboard():
    keyboard = types.ReplyKeyboardMarkup(resize_keyboard=True)
    keyboard.row(types.KeyboardButton("📋 Список"), types.KeyboardButton("➕ Быстро добавить"))
    keyboard.row(types.KeyboardButton("🔔 Проверить сроки"), types.KeyboardButton("📈 Статистика"))
    keyboard.row(types.KeyboardButton("✏️ Редактировать"), types.KeyboardButton("💡 Рекомендации"))
    if google_sheets_available:
        keyboard.row(types.KeyboardButton("📊 Google Sheets"))
    return keyboard

def get_quick_actions_keyboard():
    keyboard = types.ReplyKeyboardMarkup(resize_keyboard=True)
    keyboard.row(
        types.KeyboardButton("🚰 Угольный"), 
        types.KeyboardButton("⚙️ Механический")
    )
    keyboard.row(
        types.KeyboardButton("💧 ОСмос"), 
        types.KeyboardButton("🔄 Умягчитель")
    )
    keyboard.row(
        types.KeyboardButton("📋 Все фильтры"), 
        types.KeyboardButton("🔔 Сроки")
    )
    keyboard.row(types.KeyboardButton("🏠 Главное меню"))
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

# Уведомления и напоминания
async def check_expiring_filters():
    """Проверка истекающих фильтров и отправка уведомлений"""
    conn = sqlite3.connect('filters.db')
    cur = conn.cursor()
    cur.execute('''SELECT user_id, filter_type, expiry_date 
                   FROM filters WHERE expiry_date <= date('now', '+30 days')''')
    expiring_filters = cur.fetchall()
    conn.close()
    
    today = datetime.now().date()
    
    for user_id, filter_type, expiry_date in expiring_filters:
        expiry = datetime.strptime(expiry_date, '%Y-%m-%d').date()
        days_left = (expiry - today).days
        
        if days_left <= 0:
            message = f"🚨 СРОЧНО! Фильтр '{filter_type}' ПРОСРОЧЕН!"
        elif days_left <= 7:
            message = f"🔔 Фильтр '{filter_type}' истекает через {days_left} дней (до {expiry_date})"
        elif days_left <= 30:
            message = f"📅 Фильтр '{filter_type}' истекает через {days_left} дней"
        else:
            continue
            
        try:
            await bot.send_message(user_id, message)
            await asyncio.sleep(0.1)
        except Exception as e:
            logging.error(f"Не удалось отправить уведомление пользователю {user_id}: {e}")

# Отчет для администратора
async def send_admin_report():
    """Ежедневный отчет для администратора"""
    if not ADMIN_ID:
        return
        
    conn = sqlite3.connect('filters.db')
    cur = conn.cursor()
    
    cur.execute("SELECT COUNT(DISTINCT user_id) FROM filters")
    total_users = cur.fetchone()[0]
    
    cur.execute("SELECT COUNT(*) FROM filters WHERE date(last_change) = date('now')")
    today_added = cur.fetchone()[0]
    
    cur.execute("SELECT COUNT(*) FROM filters WHERE expiry_date < date('now')")
    total_expired = cur.fetchone()[0]
    
    conn.close()
    
    report = f"""📊 Ежедневный отчет бота фильтров
⏰ {datetime.now().strftime('%Y-%m-%d %H:%M')}

👥 Всего пользователей: {total_users}
📥 Добавлено сегодня: {today_added}
❌ Просроченных фильтров: {total_expired}
📊 Google Sheets: {'✅' if google_sheets_available else '❌'}"""
    
    try:
        await bot.send_message(ADMIN_ID, report)
    except Exception as e:
        logging.error(f"Не удалось отправить отчет админу: {e}")

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
@dp.message_handler(lambda message: message.text == "➕ Быстро добавить")
@dp.message_handler(commands=['add'])
async def cmd_add(message: types.Message):
    await message.answer(
        "Выберите способ добавления:",
        reply_markup=get_quick_actions_keyboard()
    )

# Быстрое добавление фильтров
@dp.message_handler(lambda message: message.text in ["🚰 Угольный", "⚙️ Механический", "💧 ОСмос", "🔄 Умягчитель"])
async def quick_add_filter(message: types.Message):
    text_to_type = {
        "🚰 Угольный": ("Угольный", 180),
        "⚙️ Механический": ("Механический", 90),
        "💧 ОСмос": ("Обратного осмоса", 365),
        "🔄 Умягчитель": ("Умягчитель", 180)
    }
    
    filter_type, lifetime = text_to_type[message.text]
    change_date = datetime.now().date()
    expiry_date = change_date + timedelta(days=lifetime)
    
    conn = sqlite3.connect('filters.db')
    cur = conn.cursor()
    cur.execute('''INSERT INTO filters 
                (user_id, filter_type, last_change, expiry_date, lifetime_days) 
                VALUES (?, ?, ?, ?, ?)''',
               (message.from_user.id, filter_type, change_date, expiry_date, lifetime))
    conn.commit()
    conn.close()
    
    simple_sync_to_google_sheets()
    
    days_until_expiry = (expiry_date - datetime.now().date()).days
    status_icon = "🔔" if days_until_expiry <= 30 else "✅"
    
    await message.answer(
        f"✅ Фильтр '{filter_type}' добавлен!\n\n"
        f"📅 Заменен: {change_date}\n"
        f"⏰ Срок службы: {lifetime} дней\n"
        f"📅 Годен до: {expiry_date} {status_icon}\n"
        f"⏳ Осталось дней: {days_until_expiry}",
        reply_markup=get_main_keyboard()
    )

# Стандартное добавление фильтра
@dp.message_handler(lambda message: message.text == "Другой тип")
async def cmd_add_custom(message: types.Message):
    await FilterStates.waiting_filter_type.set()
    await message.answer("Введите тип фильтра:", reply_markup=get_cancel_keyboard())

@dp.message_handler(state=FilterStates.waiting_filter_type)
async def process_filter_type(message: types.Message, state: FSMContext):
    if message.text == "❌ Отмена":
        await state.finish()
        await message.answer("❌ Действие отменено", reply_markup=get_main_keyboard())
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
            
            conn = sqlite3.connect('filters.db')
            cur = conn.cursor()
            cur.execute('''INSERT INTO filters 
                        (user_id, filter_type, last_change, expiry_date, lifetime_days) 
                        VALUES (?, ?, ?, ?, ?)''',
                       (message.from_user.id, filter_type, change_date, expiry_date, lifetime))
            conn.commit()
            conn.close()

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
@dp.message_handler(lambda message: message.text in ["📋 Список", "📋 Все фильтры"])
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
@dp.message_handler(lambda message: message.text in ["🔔 Проверить сроки", "🔔 Сроки"])
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

# Статистика
@dp.message_handler(lambda message: message.text == "📈 Статистика")
@dp.message_handler(commands=['stats'])
async def cmd_stats(message: types.Message):
    conn = sqlite3.connect('filters.db')
    cur = conn.cursor()
    
    cur.execute('''SELECT 
                    COUNT(*) as total,
                    SUM(CASE WHEN expiry_date < date('now') THEN 1 ELSE 0 END) as expired,
                    SUM(CASE WHEN expiry_date BETWEEN date('now') AND date('now', '+30 days') THEN 1 ELSE 0 END) as expiring_soon
                 FROM filters WHERE user_id = ?''', (message.from_user.id,))
    stats = cur.fetchone()
    
    cur.execute('''SELECT filter_type, COUNT(*) as count 
                   FROM filters WHERE user_id = ? GROUP BY filter_type''', 
                (message.from_user.id,))
    type_stats = cur.fetchall()
    
    cur.execute('''SELECT filter_type, expiry_date 
                   FROM filters WHERE user_id = ? AND expiry_date >= date('now')
                   ORDER BY expiry_date LIMIT 3''', (message.from_user.id,))
    next_replacements = cur.fetchall()
    
    conn.close()
    
    response = "📈 Статистика ваших фильтров:\n\n"
    response += f"📊 Всего фильтров: {stats[0]}\n"
    response += f"❌ Просрочено: {stats[1]}\n"
    response += f"🟡 Истекают скоро: {stats[2]}\n\n"
    
    response += "📋 По типам:\n"
    for filter_type, count in type_stats:
        response += f"  • {filter_type}: {count} шт.\n"
    
    if next_replacements:
        response += "\n🔜 Ближайшие замены:\n"
        for filter_type, expiry_date in next_replacements:
            days_left = (datetime.strptime(expiry_date, '%Y-%m-%d').date() - datetime.now().date()).days
            response += f"  • {filter_type}: через {days_left} дней\n"
    
    await message.answer(response, reply_markup=get_main_keyboard())

# Рекомендации
@dp.message_handler(lambda message: message.text == "💡 Рекомендации")
async def cmd_recommendations(message: types.Message):
    conn = sqlite3.connect('filters.db')
    cur = conn.cursor()
    cur.execute('''SELECT filter_type, expiry_date FROM filters 
                   WHERE user_id = ? AND expiry_date <= date('now', '+14 days')''',
                (message.from_user.id,))
    urgent_filters = cur.fetchall()
    conn.close()
    
    if not urgent_filters:
        await message.answer("✅ Все фильтры в порядке! Рекомендаций нет.", reply_markup=get_main_keyboard())
        return
    
    response = "💡 Рекомендации по замене фильтров:\n\n"
    
    for filter_type, expiry_date in urgent_filters:
        expiry = datetime.strptime(expiry_date, '%Y-%m-%d').date()
        days_left = (expiry - datetime.now().date()).days
        
        if days_left <= 0:
            response += f"🚨 НЕМЕДЛЕННО замените '{filter_type}' (просрочен)\n"
        elif days_left <= 3:
            response += f"🔴 Срочно замените '{filter_type}' (осталось {days_left} дней)\n"
        elif days_left <= 14:
            response += f"🟡 Запланируйте замену '{filter_type}' (осталось {days_left} дней)\n"
    
    response += "\n💡 Советы:\n"
    response += "• Меняйте фильтры утром выходного дня\n"
    response += "• Имейте запасные фильтры дома\n"
    response += "• После замены отмечайте в боте сразу\n"
    
    await message.answer(response, reply_markup=get_main_keyboard())

# Редактирование фильтров
@dp.message_handler(lambda message: message.text == "✏️ Редактировать")
@dp.message_handler(commands=['edit'])
async def cmd_edit(message: types.Message):
    conn = sqlite3.connect('filters.db')
    cur = conn.cursor()
    cur.execute("SELECT id, filter_type, expiry_date FROM filters WHERE user_id = ?", 
                (message.from_user.id,))
    filters = cur.fetchall()
    conn.close()

    if not filters:
        await message.answer("❌ Нет фильтров для редактирования", reply_markup=get_main_keyboard())
        return

    keyboard = types.InlineKeyboardMarkup(row_width=1)
    for f in filters:
        keyboard.add(types.InlineKeyboardButton(
            f"{f[1]} (до {f[2]})",
            callback_data=f"edit_select_{f[0]}"
        ))
    
    keyboard.add(types.InlineKeyboardButton("❌ Отменить", callback_data="cancel_edit"))

    await message.answer("Выберите фильтр для редактирования:", reply_markup=keyboard)

@dp.callback_query_handler(lambda c: c.data and c.data.startswith('edit_select_'))
async def process_edit_select(callback_query: types.CallbackQuery, state: FSMContext):
    filter_id = callback_query.data.split('_')[2]
    
    async with state.proxy() as data:
        data['edit_filter_id'] = filter_id
    
    keyboard = types.InlineKeyboardMarkup(row_width=2)
    keyboard.add(
        types.InlineKeyboardButton("📝 Тип", callback_data="edit_field_type"),
        types.InlineKeyboardButton("📅 Дата замены", callback_data="edit_field_date"),
        types.InlineKeyboardButton("⏰ Срок службы", callback_data="edit_field_lifetime")
    )
    keyboard.add(types.InlineKeyboardButton("❌ Отменить", callback_data="cancel_edit"))
    
    await callback_query.message.edit_text("Что вы хотите изменить?", reply_markup=keyboard)
    await EditFilterStates.waiting_field_selection.set()

@dp.callback_query_handler(lambda c: c.data == 'edit_field_date', state=EditFilterStates.waiting_field_selection)
async def process_edit_date(callback_query: types.CallbackQuery, state: FSMContext):
    await callback_query.message.edit_text("Введите новую дату замены (ГГГГ-ММ-ДД):")
    async with state.proxy() as data:
        data['edit_field'] = 'last_change'
    await EditFilterStates.waiting_new_value.set()

@dp.message_handler(state=EditFilterStates.waiting_new_value)
async def process_edit_value(message: types.Message, state: FSMContext):
    if message.text == "❌ Отмена":
        await state.finish()
        await message.answer("❌ Редактирование отменено", reply_markup=get_main_keyboard())
        return
    
    async with state.proxy() as data:
        filter_id = data['edit_filter_id']
        field = data['edit_field']
        
        try:
            if field == 'last_change':
                new_date = datetime.strptime(message.text, '%Y-%m-%d').date()
                
                conn = sqlite3.connect('filters.db')
                cur = conn.cursor()
                
                cur.execute("SELECT lifetime_days FROM filters WHERE id = ?", (filter_id,))
                lifetime = cur.fetchone()[0]
                
                new_expiry = new_date + timedelta(days=lifetime)
                
                cur.execute("UPDATE filters SET last_change = ?, expiry_date = ? WHERE id = ?",
                           (new_date, new_expiry, filter_id))
                conn.commit()
                conn.close()
                
                await message.answer(f"✅ Дата замены обновлена на {new_date}", reply_markup=get_main_keyboard())
                
            elif field == 'lifetime_days':
                new_lifetime = int(message.text)
                
                conn = sqlite3.connect('filters.db')
                cur = conn.cursor()
                
                cur.execute("SELECT last_change FROM filters WHERE id = ?", (filter_id,))
                last_change = cur.fetchone()[0]
                
                new_expiry = last_change + timedelta(days=new_lifetime)
                
                cur.execute("UPDATE filters SET lifetime_days = ?, expiry_date = ? WHERE id = ?",
                           (new_lifetime, new_expiry, filter_id))
                conn.commit()
                conn.close()
                
                await message.answer(f"✅ Срок службы обновлен на {new_lifetime} дней", reply_markup=get_main_keyboard())
                
            elif field == 'filter_type':
                conn = sqlite3.connect('filters.db')
                cur = conn.cursor()
                cur.execute("UPDATE filters SET filter_type = ? WHERE id = ?",
                           (message.text, filter_id))
                conn.commit()
                conn.close()
                
                await message.answer(f"✅ Тип фильтра обновлен на '{message.text}'", reply_markup=get_main_keyboard())
            
            simple_sync_to_google_sheets()
            await state.finish()
            
        except Exception as e:
            await message.answer(f"❌ Ошибка при обновлении: {e}", reply_markup=get_main_keyboard())
            await state.finish()

# Управление Google Sheets
@dp.message_handler(lambda message: message.text == "📊 Google Sheets")
@dp.message_handler(commands=['sheets'])
async def cmd_sheets(message: types.Message):
    if not google_sheets_available:
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

@dp.message_handler(lambda message: message.text == "🏠 Главное меню")
async def cmd_main_menu(message: types.Message):
    await message.answer("Возвращаемся в главное меню:", reply_markup=get_main_keyboard())

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

@dp.callback_query_handler(lambda c: c.data == "cancel_edit", state='*')
async def cancel_edit(callback_query: types.CallbackQuery, state: FSMContext):
    await state.finish()
    await callback_query.message.edit_text("❌ Редактирование отменено")

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
    
   logging.info("Бот запущен с улучшениями!")
    executor.start_polling(dp, skip_updates=True)
