import logging
import sqlite3
import gspread
from datetime import datetime, timedelta
from aiogram import Bot, Dispatcher, types
from aiogram.contrib.fsm_storage.memory import MemoryStorage
from aiogram.dispatcher import FSMContext
from aiogram.dispatcher.filters.state import State, StatesGroup
from aiogram.utils import executor
from google.oauth2.service_account import Credentials

# Настройки
API_TOKEN = '8278600298:AAGPjUhyU5HxXOaLRvu-FSRldBW_UCmwOME'
ADMIN_ID = 5024165375

# Настройки Google Sheets
SCOPES = ['https://www.googleapis.com/auth/spreadsheets', 'https://www.googleapis.com/auth/drive']
SERVICE_ACCOUNT_FILE = 'service_account.json'  # Файл с учетными данными
SPREADSHEET_NAME = 'Учет фильтров'  # Название таблицы

# Стандартные сроки службы фильтров (в днях)
DEFAULT_LIFETIMES = {
    "угольный": 180,
    "механический": 90,
    "обратного осмоса": 365,
    "умягчитель": 180,
    "пост-фильтр": 180,
    "пре-фильтр": 90
}

# Инициализация бота
logging.basicConfig(level=logging.INFO)
bot = Bot(token=API_TOKEN)
storage = MemoryStorage()
dp = Dispatcher(bot, storage=storage)

# Инициализация Google Sheets
def init_google_sheets():
    try:
        creds = Credentials.from_service_account_file(SERVICE_ACCOUNT_FILE, scopes=SCOPES)
        client = gspread.authorize(creds)
        
        # Попробуем открыть существующую таблицу или создать новую
        try:
            spreadsheet = client.open(SPREADSHEET_NAME)
        except gspread.SpreadsheetNotFound:
            spreadsheet = client.create(SPREADSHEET_NAME)
            # Предоставляем доступ для просмотра всем по ссылке
            spreadsheet.share(None, perm_type='anyone', role='reader')
        
        # Получаем первый лист
        worksheet = spreadsheet.sheet1
        
        # Устанавливаем заголовки, если лист пустой
        if not worksheet.get('A1'):
            worksheet.update('A1:F1', [['ID', 'User ID', 'Тип фильтра', 'Дата замены', 'Срок годности', 'Оставшееся дней']])
            worksheet.format('A1:F1', {'textFormat': {'bold': True}})
        
        return worksheet
    except Exception as e:
        logging.error(f"Ошибка инициализации Google Sheets: {e}")
        return None

# Инициализация БД
def init_db():
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
    keyboard.add(types.KeyboardButton("📊 Экспорт в Google Sheets"))
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

# Синхронизация с Google Sheets
def sync_to_google_sheets():
    try:
        worksheet = init_google_sheets()
        if not worksheet:
            return None
        
        conn = sqlite3.connect('filters.db')
        cur = conn.cursor()
        cur.execute("SELECT id, user_id, filter_type, last_change, expiry_date, lifetime_days FROM filters")
        filters = cur.fetchall()
        conn.close()
        
        if not filters:
            return worksheet
        
        # Подготавливаем данные
        data = []
        today = datetime.now().date()
        
        for f in filters:
            expiry_date = datetime.strptime(str(f[4]), '%Y-%m-%d').date()
            days_until_expiry = (expiry_date - today).days
            
            data.append([
                f[0],  # ID
                f[1],  # User ID
                f[2],  # Тип фильтра
                str(f[3]),  # Дата замены
                str(f[4]),  # Срок годности
                days_until_expiry  # Оставшееся дней
            ])
        
        # Очищаем старые данные (кроме заголовка) и добавляем новые
        if len(worksheet.get_all_values()) > 1:
            worksheet.delete_rows(2, len(worksheet.get_all_values()))
        
        if data:
            worksheet.update(f'A2:F{len(data)+1}', data)
        
        return worksheet
    except Exception as e:
        logging.error(f"Ошибка синхронизации с Google Sheets: {e}")
        return None

# Команда start
@dp.message_handler(commands=['start'])
async def cmd_start(message: types.Message):
    await message.answer(
        "🤖 Бот для учета замены фильтров\n\n"
        "Отслеживайте сроки службы всех ваших фильтров!\n"
        "📊 Данные синхронизируются с Google Sheets\n"
        "Используйте кнопки ниже для управления:",
        reply_markup=get_main_keyboard()
    )

# Определение срока службы по типу фильтра
def get_lifetime_by_type(filter_type):
    filter_type_lower = filter_type.lower()
    for key, days in DEFAULT_LIFETIMES.items():
        if key in filter_type_lower:
            return days
    return 180  # значение по умолчанию

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
    if message.text == "Другой тип":
        await message.answer("Введите свой вариант типа фильтра:", reply_markup=get_cancel_keyboard())
        return
    
    async with state.proxy() as data:
        data['filter_type'] = message.text
        # Автоматически определяем срок службы по типу
        data['lifetime'] = get_lifetime_by_type(message.text)

    await FilterStates.next()
    await message.answer(
        f"📅 Срок службы для '{message.text}': {data['lifetime']} дней\n"
        f"Введите дату последней замены (ГГГГ-ММ-ДД):",
        reply_markup=get_cancel_keyboard()
    )

@dp.message_handler(state=FilterStates.waiting_change_date)
async def process_date(message: types.Message, state: FSMContext):
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
        await message.answer("❌ Неверный формат даты. Используйте ГГГГ-ММ-ДД:")

@dp.message_handler(state=FilterStates.waiting_lifetime)
async def process_lifetime(message: types.Message, state: FSMContext):
    try:
        async with state.proxy() as data:
            change_date = data['change_date']
            filter_type = data['filter_type']
            
            # Определяем срок службы
            if message.text.endswith("дней"):
                lifetime = int(message.text.split()[0])
            else:
                lifetime = int(message.text)
            
            # Рассчитываем дату окончания
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

            # Синхронизируем с Google Sheets
            sync_to_google_sheets()

            # Определяем статус срока
            days_until_expiry = (expiry_date - datetime.now().date()).days
            status_icon = "🔔" if days_until_expiry <= 30 else "✅"
            
            await message.answer(
                f"✅ Фильтр успешно добавлен!\n\n"
                f"📊 Тип: {filter_type}\n"
                f"📅 Дата замены: {change_date}\n"
                f"⏰ Срок службы: {lifetime} дней\n"
                f"📅 Годен до: {expiry_date} {status_icon}\n"
                f"⏳ Осталось дней: {days_until_expiry}",
                reply_markup=get_main_keyboard()
            )
            await state.finish()
            
    except ValueError:
        await message.answer("❌ Неверный формат. Введите количество дней:")

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
        
        # Определяем иконку статуса
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

# Экспорт в Google Sheets
@dp.message_handler(lambda message: message.text == "📊 Экспорт в Google Sheets")
@dp.message_handler(commands=['export'])
async def cmd_export(message: types.Message):
    try:
        worksheet = sync_to_google_sheets()
        
        if worksheet:
            # Получаем ссылку на таблицу
            spreadsheet = worksheet.spreadsheet
            url = f"https://docs.google.com/spreadsheets/d/{spreadsheet.id}"
            
            await message.answer(
                f"✅ Данные успешно экспортированы в Google Sheets!\n\n"
                f"📊 Ссылка на таблицу:\n{url}\n\n"
                f"Таблица обновляется автоматически при каждом изменении.",
                reply_markup=get_main_keyboard(),
                disable_web_page_preview=True
            )
        else:
            await message.answer(
                "❌ Не удалось подключиться к Google Sheets.\n"
                "Проверьте настройки учетной записи сервиса.",
                reply_markup=get_main_keyboard()
            )
    except Exception as e:
        logging.error(f"Ошибка экспорта: {e}")
        await message.answer(
            "❌ Произошла ошибка при экспорте данных.",
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

# Обработка отмены
@dp.message_handler(lambda message: message.text == "❌ Отмена", state='*')
async def cmd_cancel(message: types.Message, state: FSMContext):
    await state.finish()
    await message.answer("❌ Действие отменено", reply_markup=get_main_keyboard())

# Обработка удаления через inline кнопки
@dp.callback_query_handler(lambda c: c.data and c.data.startswith('delete_'))
async def process_delete(callback_query: types.CallbackQuery):
    filter_id = callback_query.data.split('_')[1]

    conn = sqlite3.connect('filters.db')
    cur = conn.cursor()
    
    # Сначала получим информацию о фильтре для сообщения
    cur.execute("SELECT filter_type, expiry_date FROM filters WHERE id = ? AND user_id = ?",
                (filter_id, callback_query.from_user.id))
    filter_info = cur.fetchone()
    
    if filter_info:
        cur.execute("DELETE FROM filters WHERE id = ? AND user_id = ?",
                    (filter_id, callback_query.from_user.id))
        conn.commit()
        conn.close()
        
        # Синхронизируем с Google Sheets
        sync_to_google_sheets()
        
        await callback_query.message.edit_text(
            f"✅ Фильтр удален:\n📊 {filter_info[0]}\n📅 Срок истекал: {filter_info[1]}"
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
    
    # Инициализируем Google Sheets при запуске
    worksheet = init_google_sheets()
    if worksheet:
        logging.info("Google Sheets успешно инициализирован")
        # Синхронизируем существующие данные
        sync_to_google_sheets()
    else:
        logging.warning("Google Sheets не доступен, работаем только с локальной БД")
    
    executor.start_polling(dp, skip_updates=True)
{
  "type": "service_account",
  "project_id": "decent-surf-474418-h6",
  "private_key_id": "234b66a011af6018c407e4fb9f6d475df265b8ad",
  "private_key": "-----BEGIN PRIVATE KEY-----\nMIIEvAIBADANBgkqhkiG9w0BAQEFAASCBKYwggSiAgEAAoIBAQDCKKUPfQJtGiEB\nCvgg64kDPWxe91legC1/eWFBLOueL/acs+PqYMwfFhFDdg16OadS2sXEsmgjR0Mm\nw3xRVMghHqioQIYqSs2D/4Y4QxHbNRKoY8sqMv2gbjlOPQWizOp0Mr2ZsbUjK6QW\nrG4PLJ21RsyKsK6hitf2UiSs7ysEapMFLmABDC4spE75fpfDpkJUkkbHdqXv4EO3\nki7mQ7H1CrgxLxwS/QMAdceSltQxhlbaAskj1t6A2NlusS3TSKGBBoZDYee8lcLE\n4jHICFNSCgOG5wjItJT8JdZdy1WA6YyP2AcbzKAjZv9sZzDCqfxzvPt3Dowqdj7t\na0MNPCdVAgMBAAECggEABcYJToIZpIk+ilY7Va3wskDNLFpvn7tGXxWKxPzjJB4/\nV56T0aA/lvxkLAeNKWF12SVknyTtACuPfXjFBXLKIWNU1u1Vmq6vGNSW4a0WPANX\n2DELgBLpxC+y6Dbb/YuEEfYrpC/gIo5wKopwcBFpaSlp+K/TVD4ZjkYYDW/D7YrO\ndSeg6e6dfUtgVUlDie9wwXT9yvdr0hHNO24pzwL6xJ1Iqo3FdGtNNzYueMfENznW\nMOlv4YpV1Y4cmip7aiMgrpTY5KKzEbcl6OWUrvRRgA18nlDhQrZQgQHY6CLHhjXS\nu1jnSUWIJt1z8KnfXrVm6gcbnsJjJoHQBNJYCowXiwKBgQDzelGNuWOmOIFz4UI+\ncvf+q9qI3kFhaWhrfh9OFRHMGIGZ/Z8bV69H/U+Gh0KNFQZJ06CWAQEsEkAks7+d\noHjOsvpUsOX8XPS/dNLTcJW+C3DhZNnakPvwlmrFODeqltnpI8T0LrbTCGDeSBDc\nL83yV7lJJIB4x3IbyQFVHsrT6wKBgQDMJPsoHbqEEYjoAylYsqgWTy9VtCdv5bt6\nRWT3Y2Ii3buiEKaS6Tlq5welFJSqujxNNKvgYb/+xFacZF2pmeFtefkVgc+c8Mac\nA3pfhOuHR+naltpr5rpBe/53XpW0X6YenbYH+nExyP9TBZRk1HA98MAGwtLVPsP2\n1Yy8cdRhvwKBgFc2Yk4EsQgSR3RtpJ/f6KsFfdL24a515lxSof4DGAvmX038hehy\nYSvFNZ43hwQoGseJEYWJuJTDuMwswp9AouVPQNHjR4488cI+o2HvewPG71kdGdfr\nhQMprfcU6wOk40tab6sTrJMs9h3I7JkiWn/LImhrtcUNk/cV6e9Gh1qfAoGAD6MV\nifEdBFjDf+IeHKkyruLJ4Wq3Sp7ySV9faIO4VKtfX/6eg8pRl5qyvzZgXON/Evrh\n34IdTsqDR994PTGITvZ02ezjlkXrMLAjg4WO9QHRK+k6OeGmirWQvFwGY5agBuBV\nvS611PZseFOJFmAfaN80FlB+Dc+M+y9IZHbjqckCgYBx/ti3nCK/WVyJMjcmX8VY\nRJ5vpahsOsz30UMSmh7JqjZIsk1RV3mpJ+8E4FabPPfE0/yar3BsijRK8FWbnaE0\nHbPS/WtnUuqtjcall5DhXurhEGFMkGZ/4pCeyYj6Nl4HWn0AjF33uMb1XF99iOfd\nsEMN7pC6s34Zh9Tbt8QuQg==\n-----END PRIVATE KEY-----\n",
  "client_email": "timurka@decent-surf-474418-h6.iam.gserviceaccount.com",
  "client_id": "113848664495921437290",
  "auth_uri": "https://accounts.google.com/o/oauth2/auth",
  "token_uri": "https://oauth2.googleapis.com/token",
  "auth_provider_x509_cert_url": "https://www.googleapis.com/oauth2/v1/certs",
  "client_x509_cert_url": "https://www.googleapis.com/robot/v1/metadata/x509/timurka%40decent-surf-474418-h6.iam.gserviceaccount.com",
  "universe_domain": "googleapis.com"
}
