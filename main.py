import logging
import sqlite3
import pandas as pd
import io
from datetime import datetime, timedelta
from aiogram import Bot, Dispatcher, types
from aiogram.contrib.fsm_storage.memory import MemoryStorage
from aiogram.dispatcher import FSMContext
from aiogram.dispatcher.filters.state import State, StatesGroup
from aiogram.utils import executor

# Настройки
API_TOKEN = '8278600298:AAGPjUhyU5HxXOaLRvu-FSRldBW_UCmwOME'
ADMIN_ID = 5024165375

# Инициализация бота
logging.basicConfig(level=logging.INFO)
bot = Bot(token=API_TOKEN)
storage = MemoryStorage()
dp = Dispatcher(bot, storage=storage)

# Инициализация БД
def init_db():
    conn = sqlite3.connect('filters.db')
    cur = conn.cursor()
    
    # Таблица фильтров пользователей
    cur.execute('''CREATE TABLE IF NOT EXISTS filters (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                filter_type TEXT,
                location TEXT,
                last_change DATE,
                expiry_date DATE,
                lifetime_days INTEGER)''')
    
    # Таблица стандартных сроков службы фильтров
    cur.execute('''CREATE TABLE IF NOT EXISTS filter_standards (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                filter_type TEXT UNIQUE,
                lifetime_days INTEGER,
                description TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)''')
    
    # Проверяем, есть ли уже стандартные значения
    cur.execute("SELECT COUNT(*) FROM filter_standards")
    count = cur.fetchone()[0]
    
    if count == 0:
        # Добавляем стандартные значения
        default_standards = [
            ("Магистральный SL10", 180, "Стандартный магистральный фильтр"),
            ("Магистральный SL20", 180, "Улучшенный магистральный фильтр"),
            ("Гейзер", 365, "Фильтр-кувшин Гейзер"),
            ("Аквафор", 365, "Фильтр-кувшин Аквафор"),
            ("Угольный фильтр", 180, "Стандартный угольный фильтр"),
            ("Механический фильтр", 90, "Фильтр грубой очистки"),
            ("УФ-лампа", 365, "Ультрафиолетовый стерилизатор"),
            ("Обратный осмос", 365, "Система обратного осмоса")
        ]
        cur.executemany('''INSERT INTO filter_standards (filter_type, lifetime_days, description) 
                          VALUES (?, ?, ?)''', default_standards)
    
    conn.commit()
    conn.close()

# Определение срока службы по типу фильтра
def get_lifetime_by_type(filter_type):
    conn = sqlite3.connect('filters.db')
    cur = conn.cursor()
    
    # Ищем точное совпадение
    cur.execute("SELECT lifetime_days FROM filter_standards WHERE filter_type = ?", (filter_type,))
    result = cur.fetchone()
    
    if result:
        conn.close()
        return result[0]
    
    # Ищем частичное совпадение (без учета регистра)
    cur.execute("SELECT filter_type, lifetime_days FROM filter_standards")
    standards = cur.fetchall()
    conn.close()
    
    filter_type_lower = filter_type.lower()
    for standard_type, days in standards:
        if standard_type.lower() in filter_type_lower:
            return days
    
    return 180

# Экспорт данных в Excel
def export_to_excel(user_id):
    try:
        conn = sqlite3.connect('filters.db')
        
        # Получаем данные пользователя
        query = '''SELECT id, filter_type, location, last_change, expiry_date, lifetime_days 
                   FROM filters WHERE user_id = ?'''
        df = pd.read_sql_query(query, conn, params=(user_id,))
        conn.close()

        if df.empty:
            return None, "Нет данных для экспорта"

        # Добавляем столбец с остатком дней и статусом
        today = pd.to_datetime('today').date()
        df['expiry_date'] = pd.to_datetime(df['expiry_date']).dt.date
        df['days_until_expiry'] = (df['expiry_date'] - today).dt.days
        
        # Добавляем столбец со статусом
        def get_status(days):
            if days <= 0:
                return "🔴 ПРОСРОЧЕН"
            elif days <= 7:
                return "🟡 СРОЧНО"
            elif days <= 30:
                return "🔔 СКОРО"
            else:
                return "✅ НОРМА"
        
        df['status'] = df['days_until_expiry'].apply(get_status)
        
        # Создаем Excel файл в памяти
        output = io.BytesIO()
        with pd.ExcelWriter(output, engine='openpyxl') as writer:
            df.to_excel(writer, sheet_name='Мои фильтры', index=False)
            
            # Добавляем лист со стандартными сроками
            conn = sqlite3.connect('filters.db')
            standards_df = pd.read_sql_query("SELECT filter_type, lifetime_days, description FROM filter_standards", conn)
            conn.close()
            standards_df.to_excel(writer, sheet_name='Стандартные сроки', index=False)
        
        output.seek(0)
        return output, "✅ Данные успешно экспортированы в Excel"
    
    except Exception as e:
        logging.error(f"Ошибка экспорта в Excel: {e}")
        return None, f"❌ Ошибка экспорта: {e}"

# Импорт данных из Excel
def import_from_excel(user_id, file_content):
    try:
        # Читаем Excel файл
        df = pd.read_excel(file_content, sheet_name='Мои фильтры')
        
        # Проверяем необходимые колонки
        required_columns = ['filter_type', 'location', 'last_change', 'lifetime_days']
        if not all(col in df.columns for col in required_columns):
            return False, "❌ Неверный формат файла. Отсутствуют необходимые колонки."

        conn = sqlite3.connect('filters.db')
        cur = conn.cursor()

        imported_count = 0
        errors = []
        
        for index, row in df.iterrows():
            try:
                filter_type = str(row['filter_type']).strip()
                location = str(row['location']).strip()
                
                # Обрабатываем дату замены
                if pd.isna(row['last_change']):
                    last_change = datetime.now().date()
                else:
                    try:
                        last_change = pd.to_datetime(row['last_change']).date()
                    except:
                        last_change = datetime.now().date()
                
                # Обрабатываем срок службы
                if pd.isna(row['lifetime_days']):
                    lifetime_days = get_lifetime_by_type(filter_type)
                else:
                    try:
                        lifetime_days = int(row['lifetime_days'])
                    except:
                        lifetime_days = get_lifetime_by_type(filter_type)
                
                # Рассчитываем дату истечения
                expiry_date = last_change + timedelta(days=lifetime_days)
                
                # Если есть дата истечения в файле, используем её
                if 'expiry_date' in df.columns and not pd.isna(row['expiry_date']):
                    try:
                        expiry_date = pd.to_datetime(row['expiry_date']).date()
                    except:
                        pass  # Используем рассчитанную дату
                
                # Добавляем фильтр в базу
                cur.execute('''INSERT INTO filters 
                            (user_id, filter_type, location, last_change, expiry_date, lifetime_days) 
                            VALUES (?, ?, ?, ?, ?, ?)''',
                           (user_id, filter_type, location, last_change, expiry_date, lifetime_days))
                
                imported_count += 1
                
            except Exception as e:
                errors.append(f"Строка {index + 2}: {str(e)}")
                continue

        conn.commit()
        conn.close()
        
        result_message = f"✅ Импорт завершен!\n📊 Загружено записей: {imported_count}"
        if errors:
            result_message += f"\n\n❌ Ошибки ({len(errors)}):\n" + "\n".join(errors[:5])  # Показываем первые 5 ошибок
        
        return True, result_message
    
    except Exception as e:
        logging.error(f"Ошибка импорта из Excel: {e}")
        return False, f"❌ Ошибка импорта: {e}"

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

class MultipleFiltersStates(StatesGroup):
    waiting_filters_selection = State()
    waiting_common_location = State()
    waiting_common_change_date = State()
    waiting_common_lifetime = State()

# Клавиатуры
def get_main_keyboard():
    keyboard = types.ReplyKeyboardMarkup(resize_keyboard=True)
    keyboard.row(
        types.KeyboardButton("📊 Мои фильтры"),
        types.KeyboardButton("➕ Новый фильтр")
    )
    keyboard.row(
        types.KeyboardButton("⏰ Сроки замены"),
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
        types.KeyboardButton("📈 Статистика"),
        types.KeyboardButton("📊 Excel")
    )
    keyboard.row(types.KeyboardButton("🔙 Назад"))
    return keyboard

def get_excel_keyboard():
    keyboard = types.ReplyKeyboardMarkup(resize_keyboard=True)
    keyboard.row(
        types.KeyboardButton("📤 Экспорт в Excel"),
        types.KeyboardButton("📥 Импорт из Excel")
    )
    keyboard.row(types.KeyboardButton("🔙 Назад в управление"))
    return keyboard

def get_cancel_keyboard():
    keyboard = types.ReplyKeyboardMarkup(resize_keyboard=True)
    keyboard.add(types.KeyboardButton("🔙 Отмена"))
    return keyboard

def get_back_keyboard():
    keyboard = types.ReplyKeyboardMarkup(resize_keyboard=True)
    keyboard.add(types.KeyboardButton("🔙 Назад"))
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
    keyboard.row(
        types.KeyboardButton("📝 Другой тип"),
        types.KeyboardButton("📋 Добавить несколько")
    )
    keyboard.row(types.KeyboardButton("🔙 Отмена"))
    return keyboard

def get_multiple_filters_keyboard():
    """Клавиатура для выбора нескольких фильтров"""
    conn = sqlite3.connect('filters.db')
    cur = conn.cursor()
    cur.execute("SELECT filter_type FROM filter_standards ORDER BY filter_type")
    filter_types = [row[0] for row in cur.fetchall()]
    conn.close()
    
    keyboard = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    
    # Добавляем кнопки для каждого типа фильтра
    for i in range(0, len(filter_types), 2):
        if i + 1 < len(filter_types):
            keyboard.row(
                types.KeyboardButton(filter_types[i]),
                types.KeyboardButton(filter_types[i + 1])
            )
        else:
            keyboard.add(types.KeyboardButton(filter_types[i]))
    
    keyboard.row(
        types.KeyboardButton("✅ Завершить выбор"),
        types.KeyboardButton("🔄 Очистить выбор")
    )
    keyboard.row(types.KeyboardButton("🔙 Отмена"))
    return keyboard

def get_location_keyboard():
    keyboard = types.ReplyKeyboardMarkup(resize_keyboard=True)
    keyboard.add(types.KeyboardButton("📍 Указать место"))
    keyboard.add(types.KeyboardButton("🔙 Отмена"))
    return keyboard

def get_lifetime_keyboard():
    keyboard = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=3)
    keyboard.row(
        types.KeyboardButton("📅 90 дней"),
        types.KeyboardButton("📅 180 дней"),
        types.KeyboardButton("📅 365 дней")
    )
    keyboard.row(types.KeyboardButton("🔙 Отмена"))
    return keyboard

def get_edit_field_keyboard():
    keyboard = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    keyboard.row(
        types.KeyboardButton("🔧 Тип фильтра"),
        types.KeyboardButton("📍 Место установки")
    )
    keyboard.row(
        types.KeyboardButton("📅 Дата замены"),
        types.KeyboardButton("⏰ Срок службы")
    )
    keyboard.row(types.KeyboardButton("🔙 Назад к фильтрам"))
    return keyboard

def get_confirmation_keyboard(filter_id):
    keyboard = types.InlineKeyboardMarkup(row_width=2)
    keyboard.add(
        types.InlineKeyboardButton("✅ Да, удалить", callback_data=f"confirm_delete_{filter_id}"),
        types.InlineKeyboardButton("❌ Нет, отменить", callback_data="cancel_delete")
    )
    return keyboard

# Команда start
@dp.message_handler(commands=['start'])
async def cmd_start(message: types.Message):
    await message.answer(
        "🤖 <b>Бот для учета замены фильтров</b>\n\n"
        "💧 <i>Никогда не забывайте о своевременной замене фильтров!</i>\n\n"
        "📊 <b>Основные функции:</b>\n"
        "• 📊 Мои фильтры - просмотр всех фильтров\n"
        "• ➕ Новый фильтр - добавить новый фильтр\n"
        "• ⏰ Сроки замены - проверить истекающие фильтры\n"
        "• ⚙️ Управление - редактирование и удаление\n"
        "• 📊 Excel - экспорт и импорт данных",
        parse_mode='HTML',
        reply_markup=get_main_keyboard()
    )

# Обработка кнопки "Назад"
@dp.message_handler(lambda message: message.text == "🔙 Назад")
async def cmd_back(message: types.Message):
    await message.answer(
        "🔙 Возвращаемся в главное меню:",
        reply_markup=get_main_keyboard()
    )

# Обработка кнопки "Управление"
@dp.message_handler(lambda message: message.text == "⚙️ Управление")
async def cmd_management(message: types.Message):
    await message.answer(
        "⚙️ <b>Управление фильтрами</b>\n\n"
        "Выберите действие:",
        parse_mode='HTML',
        reply_markup=get_management_keyboard()
    )

# Обработка кнопки "Excel"
@dp.message_handler(lambda message: message.text == "📊 Excel")
async def cmd_excel(message: types.Message):
    await message.answer(
        "📊 <b>Работа с Excel</b>\n\n"
        "📈 <b>Доступные действия:</b>\n"
        "• 📤 Экспорт в Excel - выгрузить данные в файл\n"
        "• 📥 Импорт из Excel - загрузить данные из файла\n\n"
        "💡 <i>Формат файла: стандартный Excel (.xlsx)</i>",
        parse_mode='HTML',
        reply_markup=get_excel_keyboard()
    )

# Обработка кнопки "Экспорт в Excel"
@dp.message_handler(lambda message: message.text == "📤 Экспорт в Excel")
async def cmd_export_excel(message: types.Message):
    file_data, result_message = export_to_excel(message.from_user.id)
    if file_data:
        await message.answer_document(
            document=types.InputFile(file_data, filename=f"фильтры_{message.from_user.id}_{datetime.now().strftime('%Y%m%d')}.xlsx"),
            caption=result_message,
            reply_markup=get_excel_keyboard()
        )
    else:
        await message.answer(result_message, reply_markup=get_excel_keyboard())

# Обработка кнопки "Импорт из Excel"
@dp.message_handler(lambda message: message.text == "📥 Импорт из Excel")
async def cmd_import_excel(message: types.Message):
    await message.answer(
        "📥 <b>Импорт из Excel</b>\n\n"
        "📎 <i>Пожалуйста, загрузите Excel файл с данными фильтров.</i>\n\n"
        "💡 <b>Формат файла:</b>\n"
        "• Обязательные колонки: filter_type, location, last_change, lifetime_days\n"
        "• Дополнительные: expiry_date\n"
        "• Формат дат: ГГГГ-ММ-ДД\n\n"
        "📝 <i>Если срок службы не указан, будет использован стандартный для данного типа фильтра</i>",
        parse_mode='HTML',
        reply_markup=get_cancel_keyboard()
    )

# Обработка загруженного файла
@dp.message_handler(content_types=types.ContentType.DOCUMENT)
async def handle_document(message: types.Message):
    if message.document.mime_type in ['application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', 
                                     'application/vnd.ms-excel',
                                     'application/octet-stream']:
        
        await message.answer("⏳ Обрабатываю файл...")
        
        # Скачиваем файл
        file_info = await bot.get_file(message.document.file_id)
        downloaded_file = await bot.download_file(file_info.file_path)
        
        # Импортируем данные
        success, result_message = import_from_excel(message.from_user.id, downloaded_file)
        
        await message.answer(result_message, reply_markup=get_excel_keyboard())
    else:
        await message.answer(
            "❌ <b>Неверный формат файла!</b>\n\n"
            "💡 <i>Пожалуйста, загрузите файл в формате Excel (.xlsx)</i>",
            parse_mode='HTML',
            reply_markup=get_excel_keyboard()
        )

# Обработка кнопки "Назад в управление"
@dp.message_handler(lambda message: message.text == "🔙 Назад в управление")
async def cmd_back_to_management(message: types.Message):
    await message.answer(
        "⚙️ <b>Управление фильтрами</b>\n\n"
        "Выберите действие:",
        parse_mode='HTML',
        reply_markup=get_management_keyboard()
    )

# Добавление фильтра
@dp.message_handler(lambda message: message.text == "➕ Новый фильтр")
@dp.message_handler(commands=['add'])
async def cmd_add(message: types.Message):
    await FilterStates.waiting_filter_type.set()
    await message.answer(
        "🔧 <b>Выберите тип фильтра:</b>\n\n"
        "💡 <i>Или выберите 'Другой тип' для ручного ввода</i>",
        parse_mode='HTML',
        reply_markup=get_filter_type_keyboard()
    )

@dp.message_handler(state=FilterStates.waiting_filter_type)
async def process_filter_type(message: types.Message, state: FSMContext):
    if message.text == "🔙 Отмена":
        await state.finish()
        await message.answer("❌ Добавление фильтра отменено", reply_markup=get_main_keyboard())
        return
        
    if message.text == "📝 Другой тип":
        await message.answer(
            "📝 <b>Введите свой вариант типа фильтра:</b>",
            parse_mode='HTML',
            reply_markup=get_cancel_keyboard()
        )
        return
    
    # Пропускаем обработку кнопки "Добавить несколько", так как у нее отдельный обработчик
    if message.text == "📋 Добавить несколько":
        return
    
    async with state.proxy() as data:
        data['filter_type'] = message.text
        data['lifetime'] = get_lifetime_by_type(message.text)

    await FilterStates.next()
    await message.answer(
        "📍 <b>Укажите место установки фильтра</b>\n\n"
        "💡 <i>Нажмите кнопку '📍 Указать место' и введите место установки</i>",
        parse_mode='HTML',
        reply_markup=get_location_keyboard()
    )

# Обработка кнопки "Добавить несколько" - улучшенная версия
@dp.message_handler(lambda message: message.text == "📋 Добавить несколько", state=FilterStates.waiting_filter_type)
async def process_multiple_filters_start(message: types.Message, state: FSMContext):
    await MultipleFiltersStates.waiting_filters_selection.set()
    
    # Инициализируем список выбранных фильтров
    async with state.proxy() as data:
        data['selected_filters'] = []
    
    await message.answer(
        "📋 <b>Массовое добавление фильтров</b>\n\n"
        "🔧 <b>Выберите типы фильтров из списка:</b>\n"
        "• Нажимайте на кнопки с типами фильтров для добавления в список\n"
        "• Фильтры добавляются в список выбора\n"
        "• Нажмите '✅ Завершить выбор' когда закончите\n"
        "• '🔄 Очистить выбор' - начать заново\n\n"
        "💡 <i>Выбранные фильтры: пока нет</i>",
        parse_mode='HTML',
        reply_markup=get_multiple_filters_keyboard()
    )

# Обработка выбора фильтров в массовом добавлении
@dp.message_handler(state=MultipleFiltersStates.waiting_filters_selection)
async def process_multiple_filters_selection(message: types.Message, state: FSMContext):
    if message.text == "🔙 Отмена":
        await state.finish()
        await message.answer("❌ Добавление фильтров отменено", reply_markup=get_main_keyboard())
        return
        
    if message.text == "✅ Завершить выбор":
        async with state.proxy() as data:
            selected_filters = data['selected_filters']
        
        if not selected_filters:
            await message.answer(
                "❌ <b>Список выбранных фильтров пуст!</b>\n\n"
                "💡 <i>Выберите хотя бы один тип фильтра</i>",
                parse_mode='HTML',
                reply_markup=get_multiple_filters_keyboard()
            )
            return
        
        await MultipleFiltersStates.next()
        
        filters_list = "\n".join([f"• {filter_type}" for filter_type in selected_filters])
        await message.answer(
            f"📋 <b>Выбранные фильтры:</b>\n{filters_list}\n\n"
            "📍 <b>Укажите общее место установки для всех фильтров</b>\n\n"
            "💡 <i>Все фильтры будут добавлены с одинаковым местом установки</i>",
            parse_mode='HTML',
            reply_markup=get_location_keyboard()
        )
        return
        
    if message.text == "🔄 Очистить выбор":
        async with state.proxy() as data:
            data['selected_filters'] = []
        
        await message.answer(
            "🔄 <b>Список выбранных фильтров очищен</b>\n\n"
            "💡 <i>Выберите типы фильтров заново</i>",
            parse_mode='HTML',
            reply_markup=get_multiple_filters_keyboard()
        )
        return
    
    # Добавляем фильтр в список выбранных
    async with state.proxy() as data:
        if message.text not in data['selected_filters']:
            data['selected_filters'].append(message.text)
            selected_filters = data['selected_filters']
        else:
            # Если фильтр уже выбран, удаляем его
            data['selected_filters'].remove(message.text)
            selected_filters = data['selected_filters']
    
    # Формируем список выбранных фильтров для отображения
    if selected_filters:
        filters_text = "\n".join([f"• {filter_type}" for filter_type in selected_filters])
        status_text = f"✅ <b>Выбрано {len(selected_filters)} фильтров:</b>\n{filters_text}"
    else:
        status_text = "💡 <i>Выбранные фильтры: пока нет</i>"
    
    await message.answer(
        f"📋 <b>Массовое добавление фильтров</b>\n\n"
        f"{status_text}\n\n"
        "🔧 <b>Продолжайте выбирать типы фильтров:</b>\n"
        "• Нажимайте на кнопки с типами фильтров\n"
        "• Фильтр добавляется/убирается из списка при нажатии\n"
        "• Нажмите '✅ Завершить выбор' когда закончите",
        parse_mode='HTML',
        reply_markup=get_multiple_filters_keyboard()
    )

# Обработка общего места установки
@dp.message_handler(state=MultipleFiltersStates.waiting_common_location)
async def process_common_location(message: types.Message, state: FSMContext):
    if message.text == "🔙 Отмена":
        await state.finish()
        await message.answer("❌ Добавление фильтров отменено", reply_markup=get_main_keyboard())
        return
        
    if message.text == "📍 Указать место":
        await message.answer(
            "📍 <b>Введите общее место установки для всех фильтров:</b>\n\n"
            "💡 <i>Например: Кухня, Ванная комната, Под раковиной, Гостиная и т.д.</i>",
            parse_mode='HTML',
            reply_markup=get_cancel_keyboard()
        )
        return
    
    async with state.proxy() as data:
        data['common_location'] = message.text

    await MultipleFiltersStates.next()
    await message.answer(
        "📅 <b>Введите общую дату последней замены (ГГГГ-ММ-ДД):</b>\n"
        f"<i>Например: {datetime.now().strftime('%Y-%m-%d')}</i>\n\n"
        "💡 <i>Для всех фильтров будет установлена одинаковая дата замены</i>",
        parse_mode='HTML',
        reply_markup=get_cancel_keyboard()
    )

# Обработка общей даты замены
@dp.message_handler(state=MultipleFiltersStates.waiting_common_change_date)
async def process_common_change_date(message: types.Message, state: FSMContext):
    if message.text == "🔙 Отмена":
        await state.finish()
        await message.answer("❌ Добавление фильтров отменено", reply_markup=get_main_keyboard())
        return
        
    try:
        change_date = datetime.strptime(message.text, '%Y-%m-%d').date()
        
        async with state.proxy() as data:
            data['common_change_date'] = change_date
            
        await MultipleFiltersStates.next()
        await message.answer(
            "⏰ <b>Установить общий срок службы для всех фильтров?</b>\n\n"
            "💡 <i>Или введите количество дней для всех фильтров:</i>",
            parse_mode='HTML',
            reply_markup=get_lifetime_keyboard()
        )
        
    except ValueError:
        await message.answer(
            "❌ <b>Неверный формат даты!</b>\n\n"
            "📝 <i>Используйте формат ГГГГ-ММ-ДД</i>\n"
            f"<i>Например: {datetime.now().strftime('%Y-%m-%d')}</i>",
            parse_mode='HTML',
            reply_markup=get_cancel_keyboard()
        )

# Обработка общего срока службы и сохранение всех фильтров
@dp.message_handler(state=MultipleFiltersStates.waiting_common_lifetime)
async def process_common_lifetime_and_save(message: types.Message, state: FSMContext):
    if message.text == "🔙 Отмена":
        await state.finish()
        await message.answer("❌ Добавление фильтров отменено", reply_markup=get_main_keyboard())
        return
        
    try:
        async with state.proxy() as data:
            selected_filters = data['selected_filters']
            common_location = data['common_location']
            common_change_date = data['common_change_date']
            
            if message.text.endswith("дней"):
                common_lifetime = int(message.text.split()[1])
            else:
                common_lifetime = int(message.text)
            
            added_count = 0
            today = datetime.now().date()
            
            conn = sqlite3.connect('filters.db')
            cur = conn.cursor()
            
            # Сохраняем все фильтры
            for filter_type in selected_filters:
                # Для каждого фильтра определяем срок службы (если есть стандартный - используем его, иначе общий)
                lifetime = get_lifetime_by_type(filter_type)
                if lifetime == 180:  # Если стандартный срок не найден, используем введенный пользователем
                    lifetime = common_lifetime
                
                expiry_date = common_change_date + timedelta(days=lifetime)
                
                cur.execute('''INSERT INTO filters 
                            (user_id, filter_type, location, last_change, expiry_date, lifetime_days) 
                            VALUES (?, ?, ?, ?, ?, ?)''',
                           (message.from_user.id, filter_type, common_location, common_change_date, expiry_date, lifetime))
                added_count += 1
            
            conn.commit()
            conn.close()

            # Формируем отчет
            expiry_date = common_change_date + timedelta(days=common_lifetime)
            days_until_expiry = (expiry_date - today).days
            
            # Определяем статус с эмодзи
            if days_until_expiry <= 0:
                status_icon = "🔴 ПРОСРОЧЕН"
            elif days_until_expiry <= 7:
                status_icon = "🟡 СКОРО ИСТЕКАЕТ"
            elif days_until_expiry <= 30:
                status_icon = "🔔 СКОРО ЗАМЕНИТЬ"
            else:
                status_icon = "✅ В НОРМЕ"
            
            filters_text = "\n".join([f"• {filter_type}" for filter_type in selected_filters])
            
            await message.answer(
                f"✅ <b>Успешно добавлено {added_count} фильтров!</b>\n\n"
                f"📋 <b>Добавленные фильтры:</b>\n{filters_text}\n\n"
                f"📍 <b>Общее место:</b> {common_location}\n"
                f"📅 <b>Дата замены:</b> {common_change_date}\n"
                f"⏰ <b>Срок службы:</b> {common_lifetime} дней\n"
                f"📅 <b>Годны до:</b> {expiry_date}\n"
                f"⏳ <b>Осталось дней:</b> {days_until_expiry}\n"
                f"📊 <b>Статус:</b> {status_icon}",
                parse_mode='HTML',
                reply_markup=get_main_keyboard()
            )
            await state.finish()
            
    except ValueError:
        await message.answer(
            "❌ <b>Неверный формат!</b>\n\n"
            "📝 <i>Введите количество дней числом</i>",
            parse_mode='HTML',
            reply_markup=get_lifetime_keyboard()
        )

# ... (остальной код обработчиков остается без изменений)
# Сохраните все остальные обработчики из предыдущей версии:
# - process_location, process_date, process_lifetime
# - cmd_list, cmd_check, cmd_edit, process_edit_filter_selection, process_edit_field_selection, process_edit_new_value
# - cmd_delete, confirm_delete, process_delete, cancel_delete, back_to_management, back_to_main
# - cmd_stats, cmd_cancel, handle_other_messages

@dp.message_handler(state=FilterStates.waiting_location)
async def process_location(message: types.Message, state: FSMContext):
    if message.text == "🔙 Отмена":
        await state.finish()
        await message.answer("❌ Добавление фильтра отменено", reply_markup=get_main_keyboard())
        return
        
    if message.text == "📍 Указать место":
        await message.answer(
            "📍 <b>Введите место установки фильтра:</b>\n\n"
            "💡 <i>Например: Кухня, Ванная комната, Под раковиной, Гостиная и т.д.</i>",
            parse_mode='HTML',
            reply_markup=get_cancel_keyboard()
        )
        return
    
    # Если пользователь ввел текст напрямую (без нажатия кнопки)
    async with state.proxy() as data:
        data['location'] = message.text

    await FilterStates.next()
    await message.answer(
        f"📅 <b>Срок службы для '{data['filter_type']}':</b> {data['lifetime']} дней\n\n"
        f"📝 <b>Введите дату последней замены (ГГГГ-ММ-ДД):</b>\n"
        f"<i>Например: {datetime.now().strftime('%Y-%m-%d')}</i>",
        parse_mode='HTML',
        reply_markup=get_cancel_keyboard()
    )

@dp.message_handler(state=FilterStates.waiting_change_date)
async def process_date(message: types.Message, state: FSMContext):
    if message.text == "🔙 Отмена":
        await state.finish()
        await message.answer("❌ Добавление фильтра отменено", reply_markup=get_main_keyboard())
        return
        
    try:
        change_date = datetime.strptime(message.text, '%Y-%m-%d').date()
        
        async with state.proxy() as data:
            data['change_date'] = change_date
            
        await FilterStates.next()
        await message.answer(
            f"⏰ <b>Установить срок службы {data['lifetime']} дней?</b>\n\n"
            f"📅 <i>Или введите другое количество дней:</i>",
            parse_mode='HTML',
            reply_markup=get_lifetime_keyboard()
        )
        
    except ValueError:
        await message.answer(
            "❌ <b>Неверный формат даты!</b>\n\n"
            "📝 <i>Используйте формат ГГГГ-ММ-ДД</i>\n"
            f"<i>Например: {datetime.now().strftime('%Y-%m-%d')}</i>",
            parse_mode='HTML',
            reply_markup=get_cancel_keyboard()
        )

@dp.message_handler(state=FilterStates.waiting_lifetime)
async def process_lifetime(message: types.Message, state: FSMContext):
    if message.text == "🔙 Отмена":
        await state.finish()
        await message.answer("❌ Добавление фильтра отменено", reply_markup=get_main_keyboard())
        return
        
    try:
        async with state.proxy() as data:
            change_date = data['change_date']
            filter_type = data['filter_type']
            location = data['location']
            
            if message.text.endswith("дней"):
                lifetime = int(message.text.split()[1])
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
            elif days_until_expiry <= 7:
                status_icon = "🟡 СКОРО ИСТЕКАЕТ"
            elif days_until_expiry <= 30:
                status_icon = "🔔 СКОРО ЗАМЕНИТЬ"
            else:
                status_icon = "✅ В НОРМЕ"
            
            await message.answer(
                f"✅ <b>Фильтр успешно добавлен!</b>\n\n"
                f"🔧 <b>Тип:</b> {filter_type}\n"
                f"📍 <b>Место:</b> {location}\n"
                f"📅 <b>Дата замены:</b> {change_date}\n"
                f"⏰ <b>Срок службы:</b> {lifetime} дней\n"
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
            "📝 <i>Введите количество дней числом</i>",
            parse_mode='HTML',
            reply_markup=get_lifetime_keyboard()
        )

# Список фильтров
@dp.message_handler(lambda message: message.text == "📊 Мои фильтры")
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
            "💡 <i>Добавьте первый фильтр с помощью кнопки '➕ Новый фильтр'</i>",
            parse_mode='HTML',
            reply_markup=get_main_keyboard()
        )
        return

    response = "📊 <b>Ваши фильтры:</b>\n\n"
    today = datetime.now().date()
    
    for f in filters:
        expiry_date = datetime.strptime(str(f[4]), '%Y-%m-%d').date()
        days_until_expiry = (expiry_date - today).days
        
        # Определяем иконку статуса
        if days_until_expiry <= 0:
            status_icon = "🔴 ПРОСРОЧЕН"
        elif days_until_expiry <= 7:
            status_icon = "🟡 СРОЧНО"
        elif days_until_expiry <= 30:
            status_icon = "🔔 СКОРО"
        else:
            status_icon = "✅ НОРМА"
        
        response += (
            f"🆔 <b>ID:</b> {f[0]}\n"
            f"🔧 <b>Тип:</b> {f[1]}\n"
            f"📍 <b>Место:</b> {f[2]}\n"
            f"📅 <b>Заменен:</b> {f[3]}\n"
            f"⏰ <b>Срок:</b> {f[5]} дней\n"
            f"📅 <b>Годен до:</b> {f[4]}\n"
            f"⏳ <b>Осталось дней:</b> {days_until_expiry}\n"
            f"📢 <b>Статус:</b> {status_icon}\n\n"
        )

    await message.answer(response, parse_mode='HTML', reply_markup=get_main_keyboard())

# Проверка сроков
@dp.message_handler(lambda message: message.text == "⏰ Сроки замены")
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
            "💡 <i>Добавьте фильтры для отслеживания сроков</i>",
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
            expired_filters.append(f"🔴 {f[0]} ({f[1]}) - просрочен {abs(days_until_expiry)} дней назад")
        elif days_until_expiry <= 7:
            expiring_soon.append(f"🟡 {f[0]} ({f[1]}) - осталось {days_until_expiry} дней")
        elif days_until_expiry <= 30:
            warning_filters.append(f"🔔 {f[0]} ({f[1]}) - осталось {days_until_expiry} дней")

    response = "⏰ <b>Проверка сроков фильтров:</b>\n\n"
    
    if expired_filters:
        response += "🔴 <b>ПРОСРОЧЕНЫ:</b>\n" + "\n".join(expired_filters) + "\n\n"
    
    if expiring_soon:
        response += "🟡 <b>СРОЧНО ИСТЕКАЮТ:</b>\n" + "\n".join(expiring_soon) + "\n\n"
    
    if warning_filters:
        response += "🔔 <b>СКОРО ИСТЕКАЮТ:</b>\n" + "\n".join(warning_filters) + "\n\n"
    
    if not expired_filters and not expiring_soon and not warning_filters:
        response += "✅ <b>Все фильтры в норме!</b>\n\n"
        response += "💡 <i>Следующая проверка через 30+ дней</i>"

    await message.answer(response, parse_mode='HTML', reply_markup=get_main_keyboard())

# Обработка отмены
@dp.message_handler(lambda message: message.text == "🔙 Отмена", state='*')
async def cmd_cancel(message: types.Message, state: FSMContext):
    current_state = await state.get_state()
    if current_state is None:
        return
    
    await state.finish()
    await message.answer("❌ Действие отменено", reply_markup=get_main_keyboard())

# Обработка других сообщений
@dp.message_handler()
async def handle_other_messages(message: types.Message):
    await message.answer(
        "🤖 <b>Бот для учета замены фильтров</b>\n\n"
        "💧 <i>Выберите действие с помощью кнопок ниже:</i>",
        parse_mode='HTML',
        reply_markup=get_main_keyboard()
    )

# Запуск бота
if __name__ == '__main__':
    init_db()
    executor.start_polling(dp, skip_updates=True)
