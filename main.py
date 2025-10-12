import logging
import sqlite3
import os
import asyncio
import shutil
import traceback
import re
import sys
import pandas as pd
import openpyxl
from datetime import datetime, timedelta
from contextlib import contextmanager
from aiogram import Bot, Dispatcher, types
from aiogram.contrib.fsm_storage.memory import MemoryStorage
from aiogram.dispatcher import FSMContext
from aiogram.dispatcher.filters.state import State, StatesGroup
from aiogram.utils import executor
from dotenv import load_dotenv

# Загрузка переменных окружения
load_dotenv()

# Настройки
API_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
ADMIN_ID = int(os.getenv('ADMIN_ID', '5024165375'))

# Проверка обязательных переменных
if not API_TOKEN:
    logging.error("Токен бота не найден! Установите переменную TELEGRAM_BOT_TOKEN")
    exit(1)

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('bot.log', encoding='utf-8'),
        logging.StreamHandler(sys.stdout)
    ]
)

# Стандартные сроки службы фильтров (убраны механический, престиж, кристалл, угольный)
DEFAULT_LIFETIMES = {
    "магистральный sl10": 180,
    "магистральный sl20": 180,
    "гейзер": 365,
    "аквафор": 365
}

# Ограничения
MAX_FILTERS_PER_USER = 50

# Инициализация бота
bot = Bot(token=API_TOKEN)
storage = MemoryStorage()
dp = Dispatcher(bot, storage=storage)

# ========== УЛУЧШЕНИЯ: БЕЗОПАСНОСТЬ БАЗЫ ДАННЫХ ==========
@contextmanager
def get_db_connection():
    """Контекстный менеджер для безопасной работы с БД"""
    conn = sqlite3.connect('filters.db')
    conn.row_factory = sqlite3.Row  # Для доступа к колонкам по имени
    try:
        yield conn
    except Exception as e:
        conn.rollback()
        raise e
    finally:
        conn.close()

def safe_db_string(value: str) -> str:
    """Очистка строки для безопасного использования в БД"""
    if not value:
        return ""
    return re.sub(r'[;\'"\\]', '', value.strip())

def get_user_filters(user_id):
    """Безопасное получение фильтров пользователя"""
    with get_db_connection() as conn:
        cur = conn.cursor()
        cur.execute("SELECT * FROM filters WHERE user_id = ? ORDER BY expiry_date", (user_id,))
        return [dict(row) for row in cur.fetchall()]

def get_filter_by_id(filter_id, user_id):
    """Получение фильтра по ID с проверкой пользователя"""
    with get_db_connection() as conn:
        cur = conn.cursor()
        cur.execute("SELECT * FROM filters WHERE id = ? AND user_id = ?", (filter_id, user_id))
        result = cur.fetchone()
        return dict(result) if result else None

def check_filters_limit(user_id):
    """Проверка лимита фильтров"""
    filters = get_user_filters(user_id)
    return len(filters) >= MAX_FILTERS_PER_USER

def get_all_users_stats():
    """Получение статистики по всем пользователям (для админа)"""
    with get_db_connection() as conn:
        cur = conn.cursor()
        cur.execute('''SELECT COUNT(DISTINCT user_id) as total_users, 
                              COUNT(*) as total_filters,
                              SUM(CASE WHEN expiry_date <= date('now') THEN 1 ELSE 0 END) as expired_filters,
                              SUM(CASE WHEN expiry_date BETWEEN date('now') AND date('now', '+7 days') THEN 1 ELSE 0 END) as expiring_soon
                       FROM filters''')
        result = cur.fetchone()
        return dict(result) if result else {'total_users': 0, 'total_filters': 0, 'expired_filters': 0, 'expiring_soon': 0}

def get_all_users():
    """Получение списка всех пользователей (для админа)"""
    with get_db_connection() as conn:
        cur = conn.cursor()
        cur.execute('''SELECT DISTINCT user_id, COUNT(*) as filter_count 
                       FROM filters 
                       GROUP BY user_id 
                       ORDER BY filter_count DESC''')
        return [dict(row) for row in cur.fetchall()]

def clear_all_filters():
    """Очистка всей базы данных (для админа)"""
    with get_db_connection() as conn:
        cur = conn.cursor()
        cur.execute("DELETE FROM filters")
        conn.commit()
        return cur.rowcount

# ========== ЭКСПОРТ/ИМПОРТ EXCEL ==========
def export_filters_to_excel(user_id):
    """Экспорт фильтров пользователя в Excel файл"""
    filters = get_user_filters(user_id)
    
    if not filters:
        return None
    
    # Создаем DataFrame
    data = []
    for f in filters:
        data.append({
            'ID': f['id'],
            'Тип фильтра': f['filter_type'],
            'Место установки': f['location'],
            'Дата замены': f['last_change'],
            'Срок службы (дни)': f['lifetime_days'],
            'Годен до': f['expiry_date']
        })
    
    df = pd.DataFrame(data)
    
    # Создаем файл
    filename = f"filters_export_{user_id}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"
    filepath = os.path.join('exports', filename)
    
    # Создаем директорию если нет
    os.makedirs('exports', exist_ok=True)
    
    # Сохраняем в Excel
    with pd.ExcelWriter(filepath, engine='openpyxl') as writer:
        df.to_excel(writer, sheet_name='Фильтры', index=False)
        
        # Форматируем колонки
        worksheet = writer.sheets['Фильтры']
        worksheet.column_dimensions['A'].width = 8
        worksheet.column_dimensions['B'].width = 25
        worksheet.column_dimensions['C'].width = 20
        worksheet.column_dimensions['D'].width = 12
        worksheet.column_dimensions['E'].width = 15
        worksheet.column_dimensions['F'].width = 12
    
    return filepath

def import_filters_from_excel(file_path, user_id):
    """Импорт фильтров из Excel файла"""
    try:
        # Читаем Excel файл
        df = pd.read_excel(file_path)
        
        # Проверяем обязательные колонки
        required_columns = ['Тип фильтра', 'Место установки', 'Дата замены', 'Срок службы (дни)']
        for col in required_columns:
            if col not in df.columns:
                raise ValueError(f"Отсутствует обязательная колонка: {col}")
        
        imported_count = 0
        errors = []
        
        with get_db_connection() as conn:
            cur = conn.cursor()
            
            for index, row in df.iterrows():
                try:
                    # Валидация данных
                    filter_type = safe_db_string(str(row['Тип фильтра']))
                    location = safe_db_string(str(row['Место установки']))
                    
                    # Парсим дату
                    if isinstance(row['Дата замены'], str):
                        last_change = validate_date(row['Дата замены'])
                    else:
                        last_change = row['Дата замены'].date()
                    
                    # Получаем срок службы
                    if pd.isna(row['Срок службы (дни)']):
                        lifetime_days = get_lifetime_by_type(filter_type)
                    else:
                        lifetime_days = int(row['Срок службы (дни)'])
                    
                    # Проверяем лимит
                    current_filters = len(get_user_filters(user_id))
                    if current_filters + imported_count >= MAX_FILTERS_PER_USER:
                        errors.append(f"Достигнут лимит фильтров. Импортировано: {imported_count}")
                        break
                    
                    # Рассчитываем дату истечения срока
                    expiry_date = last_change + timedelta(days=lifetime_days)
                    
                    # Добавляем в БД
                    cur.execute('''INSERT INTO filters 
                                (user_id, filter_type, location, last_change, expiry_date, lifetime_days) 
                                VALUES (?, ?, ?, ?, ?, ?)''',
                                (user_id, filter_type, location, last_change, expiry_date, lifetime_days))
                    
                    imported_count += 1
                    
                except Exception as e:
                    errors.append(f"Строка {index + 2}: {str(e)}")
            
            conn.commit()
        
        return imported_count, errors
        
    except Exception as e:
        raise ValueError(f"Ошибка чтения файла: {str(e)}")

# ========== УЛУЧШЕНИЯ: ВАЛИДАЦИЯ ВВОДА ==========
def validate_date(date_str: str):
    """Валидация даты с улучшенной обработкой ошибок"""
    date_str = date_str.strip()
    
    # Убираем лишние символы, но оставляем точки, дефисы и слэши
    date_str = re.sub(r'[^\d\.\-/]', '', date_str)
    
    formats = ['%d.%m.%y', '%d.%m.%Y', '%d-%m-%y', '%d-%m-%Y', '%d/%m/%y', '%d/%m/%Y']
    
    for fmt in formats:
        try:
            date_obj = datetime.strptime(date_str, fmt).date()
            today = datetime.now().date()
            
            # Проверяем что дата не в будущем (максимум +1 день для запаса)
            if date_obj > today + timedelta(days=1):
                raise ValueError("Дата не может быть в будущем")
                
            # Проверяем что дата не слишком старая (максимум 5 лет назад)
            if date_obj < today - timedelta(days=5*365):
                raise ValueError("Дата слишком старая")
                
            return date_obj
        except ValueError:
            continue
    
    raise ValueError("Неверный формат даты. Используйте ДД.ММ.ГГ или ДД.ММ.ГГГГ")

def validate_lifetime(days_str: str):
    """Валидация срока службы"""
    try:
        days = int(days_str)
        if days <= 0:
            raise ValueError("Срок службы должен быть положительным числом")
        if days > 2000:  # Максимум ~5.5 лет
            raise ValueError("Слишком большой срок службы")
        return days
    except ValueError:
        raise ValueError("Введите корректное число дней")

def validate_filter_name(name: str):
    """Валидация названия фильтра"""
    name = name.strip()
    if not name:
        raise ValueError("Название фильтра не может быть пустым")
    if len(name) > 100:
        raise ValueError("Название фильтра слишком длинное")
    # Разрешаем буквы, цифры, пробелы, дефисы и точки
    if re.search(r'[^\w\s\-\.]', name, re.UNICODE):
        raise ValueError("Название содержит запрещенные символы")
    return safe_db_string(name)

# ========== ОБНОВЛЕННЫЕ КЛАВИАТУРЫ ==========

def get_filter_type_keyboard():
    """Клавиатура для выбора типа фильтра"""
    keyboard = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    
    # Только оставшиеся фильтры
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

def get_multiple_filters_keyboard():
    """Упрощенная клавиатура для нескольких фильтров"""
    keyboard = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    
    keyboard.row(types.KeyboardButton("➕ Добавить фильтр"))
    keyboard.row(types.KeyboardButton("✅ Готово"))
    keyboard.row(types.KeyboardButton("❌ Отмена"))
    
    return keyboard

def get_add_filter_keyboard():
    """Обновленная клавиатура добавления фильтра"""
    keyboard = types.ReplyKeyboardMarkup(resize_keyboard=True)
    keyboard.row(
        types.KeyboardButton("🔧 Один фильтр"),
        types.KeyboardButton("📦 Несколько фильтров")
    )
    keyboard.row(types.KeyboardButton("📤 Экспорт в Excel"))
    keyboard.row(types.KeyboardButton("📥 Импорт из Excel"))
    keyboard.row(types.KeyboardButton("🔙 Главное меню"))
    return keyboard

def get_location_keyboard():
    """Упрощенная клавиатура для места установки"""
    keyboard = types.ReplyKeyboardMarkup(resize_keyboard=True)
    keyboard.row(types.KeyboardButton("📍 Указать место установки"))
    keyboard.row(types.KeyboardButton("❌ Отмена"))
    return keyboard

def get_filters_list_keyboard(filters, action="delete"):
    """Клавиатура со списком фильтров для удаления или редактирования"""
    keyboard = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    
    for f in filters:
        button_text = f"#{f['id']} {f['filter_type']} - {f['location']}"
        keyboard.add(types.KeyboardButton(button_text))
    
    keyboard.row(types.KeyboardButton("🔙 Главное меню"))
    return keyboard

def get_edit_filter_keyboard():
    """Клавиатура для редактирования фильтра"""
    keyboard = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    keyboard.row(
        types.KeyboardButton("✏️ Тип фильтра"),
        types.KeyboardButton("📍 Место установки")
    )
    keyboard.row(
        types.KeyboardButton("📅 Дата замены"),
        types.KeyboardButton("⏱️ Срок службы")
    )
    keyboard.row(types.KeyboardButton("🔙 К списку фильтров"))
    keyboard.row(types.KeyboardButton("🔙 Главное меню"))
    return keyboard

def get_admin_keyboard():
    """Клавиатура админ панели"""
    keyboard = types.ReplyKeyboardMarkup(resize_keyboard=True)
    keyboard.row(
        types.KeyboardButton("📊 Общая статистика"),
        types.KeyboardButton("👥 Пользователи")
    )
    keyboard.row(
        types.KeyboardButton("🗑️ Очистить базу"),
        types.KeyboardButton("🔙 Главное меню")
    )
    return keyboard

def get_excel_keyboard():
    """Клавиатура для работы с Excel"""
    keyboard = types.ReplyKeyboardMarkup(resize_keyboard=True)
    keyboard.row(
        types.KeyboardButton("📤 Экспорт в Excel"),
        types.KeyboardButton("📥 Импорт из Excel")
    )
    keyboard.row(types.KeyboardButton("🔙 Главное меню"))
    return keyboard

def get_confirmation_keyboard(filter_id=None, action="delete"):
    """Клавиатура подтверждения удаления"""
    keyboard = types.InlineKeyboardMarkup(row_width=2)
    if action == "delete":
        keyboard.add(
            types.InlineKeyboardButton("✅ Да, удалить", callback_data=f"confirm_delete_{filter_id}"),
            types.InlineKeyboardButton("❌ Отмена", callback_data="cancel_delete")
        )
    elif action == "clear_db":
        keyboard.add(
            types.InlineKeyboardButton("✅ Да, очистить всю базу", callback_data="confirm_clear_db"),
            types.InlineKeyboardButton("❌ Отмена", callback_data="cancel_clear_db")
        )
    return keyboard

# ========== СУЩЕСТВУЮЩИЕ КЛАВИАТУРЫ ==========
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
    if str(ADMIN_ID) == str(ADMIN_ID):  # Проверка что пользователь админ
        keyboard.row(types.KeyboardButton("👑 Админ панель"))
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

# ========== УЛУЧШЕНИЯ: ИНИЦИАЛИЗАЦИЯ БАЗЫ ДАННЫХ ==========
def init_db():
    """Безопасная инициализация базы данных с проверками"""
    try:
        with get_db_connection() as conn:
            cur = conn.cursor()
            
            # Проверяем существование таблицы и её структуру
            cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='filters'")
            table_exists = cur.fetchone()
            
            if not table_exists:
                # Создаем таблицу с полной структурой
                cur.execute('''CREATE TABLE filters (
                            id INTEGER PRIMARY KEY AUTOINCREMENT,
                            user_id INTEGER,
                            filter_type TEXT,
                            location TEXT,
                            last_change DATE,
                            expiry_date DATE,
                            lifetime_days INTEGER,
                            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)''')
                
                # Создаем индексы
                cur.execute('''CREATE INDEX idx_user_id ON filters(user_id)''')
                cur.execute('''CREATE INDEX idx_expiry_date ON filters(expiry_date)''')
                logging.info("База данных успешно создана")
            else:
                # Проверяем структуру существующей таблицы
                cur.execute("PRAGMA table_info(filters)")
                columns = [column[1] for column in cur.fetchall()]
                required_columns = ['id', 'user_id', 'filter_type', 'location', 'last_change', 'expiry_date', 'lifetime_days', 'created_at', 'updated_at']
                
                logging.info("База данных уже существует, проверка структуры завершена")
            
            conn.commit()
            
    except Exception as e:
        logging.error(f"Критическая ошибка инициализации БД: {e}")
        if os.path.exists('filters.db'):
            backup_name = f'filters_backup_critical_{datetime.now().strftime("%Y%m%d_%H%M%S")}.db'
            shutil.copy2('filters.db', backup_name)
            logging.info(f"Создана критическая резервная копия: {backup_name}")
        raise

# Функция резервного копирования базы данных
def backup_database():
    """Создание резервной копии базы данных с проверками"""
    try:
        if not os.path.exists('filters.db'):
            logging.warning("База данных не найдена для резервного копирования")
            return False
            
        backup_dir = "backups"
        os.makedirs(backup_dir, exist_ok=True)
        
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_file = os.path.join(backup_dir, f"filters_backup_{timestamp}.db")
        
        # Проверяем размер базы данных
        db_size = os.path.getsize('filters.db')
        if db_size == 0:
            logging.warning("База данных пуста, пропускаем резервное копирование")
            return False
            
        shutil.copy2('filters.db', backup_file)
        logging.info(f"Создана резервная копия: {backup_file} ({db_size} bytes)")
        
        # Удаляем старые резервные копии (оставляем последние 10)
        backups = sorted([f for f in os.listdir(backup_dir) if f.startswith("filters_backup") and f.endswith(".db")])
        for old_backup in backups[:-10]:
            old_backup_path = os.path.join(backup_dir, old_backup)
            try:
                os.remove(old_backup_path)
                logging.info(f"Удалена старая резервная копия: {old_backup}")
            except Exception as e:
                logging.error(f"Не удалось удалить старую резервную копию {old_backup}: {e}")
        
        return True
        
    except Exception as e:
        logging.error(f"Ошибка при создании резервной копии: {e}")
        return False

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

class DeleteFilterStates(StatesGroup):
    waiting_filter_selection = State()

class ExcelStates(StatesGroup):
    waiting_excel_file = State()

# ========== УЛУЧШЕНИЯ: ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ==========
def parse_date(date_str):
    """Улучшенный парсинг даты с валидацией"""
    return validate_date(date_str)

def format_date_nice(date):
    return date.strftime('%d.%m.%y')

def get_lifetime_by_type(filter_type):
    filter_type_lower = filter_type.lower()
    for key, days in DEFAULT_LIFETIMES.items():
        if key in filter_type_lower:
            return days
    return 180

def get_status_icon_and_text(days_until_expiry):
    """Получение иконки и текста статуса"""
    if days_until_expiry <= 0:
        return "🔴", "ПРОСРОЧЕН"
    elif days_until_expiry <= 7:
        return "🟡", "СРОЧНО ЗАМЕНИТЬ"
    elif days_until_expiry <= 30:
        return "🟠", "СКОРО ЗАМЕНИТЬ"
    else:
        return "✅", "В НОРМЕ"

# ========== ОБНОВЛЕННЫЙ РАЗДЕЛ НЕСКОЛЬКИХ ФИЛЬТРОВ ==========

@dp.message_handler(lambda message: message.text == "📦 Несколько фильтров")
async def cmd_multiple_filters(message: types.Message, state: FSMContext):
    """Начало процесса добавления нескольких фильтров"""
    # Проверяем лимит фильтров
    if check_filters_limit(message.from_user.id):
        await message.answer(
            f"❌ <b>Достигнут лимит фильтров!</b>\n\n"
            f"💡 <i>Максимальное количество фильтров: {MAX_FILTERS_PER_USER}</i>\n"
            f"📊 <i>Удалите некоторые фильтры перед добавлением новых</i>",
            parse_mode='HTML',
            reply_markup=get_main_keyboard()
        )
        return
        
    await MultipleFiltersStates.waiting_filters_list.set()
    
    # Инициализируем список выбранных фильтров
    async with state.proxy() as data:
        data['selected_filters'] = []
    
    await message.answer(
        "📦 <b>Добавление нескольких фильтров</b>\n\n"
        "💡 <i>Используйте кнопки ниже для добавления фильтров:</i>\n\n"
        "• <b>➕ Добавить фильтр</b> - выбрать из списка типов\n"
        "• <b>✅ Готово</b> - завершить добавление\n\n"
        "📝 <b>Текущий список:</b>\n"
        "<i>Пока пусто</i>",
        parse_mode='HTML',
        reply_markup=get_multiple_filters_keyboard()
    )

@dp.message_handler(state=MultipleFiltersStates.waiting_filters_list)
async def process_multiple_filters_selection(message: types.Message, state: FSMContext):
    async with state.proxy() as data:
        if 'selected_filters' not in data:
            data['selected_filters'] = []
    
    # Обработка кнопки отмены
    if message.text == "❌ Отмена":
        await state.finish()
        await message.answer("🚫 Добавление фильтров отменено", reply_markup=get_main_keyboard())
        return
    
    # Обработка кнопки "Готово"
    if message.text == "✅ Готово":
        if not data['selected_filters']:
            await message.answer(
                "❌ <b>Список фильтров пуст!</b>\n\n"
                "💡 <i>Добавьте хотя бы один фильтр перед завершением</i>",
                parse_mode='HTML',
                reply_markup=get_multiple_filters_keyboard()
            )
            return
        
        # Проверяем общий лимит фильтров
        current_filters_count = len(get_user_filters(message.from_user.id))
        if current_filters_count + len(data['selected_filters']) > MAX_FILTERS_PER_USER:
            await message.answer(
                f"❌ <b>Превышен лимит фильтров!</b>\n\n"
                f"📊 <i>Текущее количество: {current_filters_count}</i>\n"
                f"📦 <i>Пытаетесь добавить: {len(data['selected_filters'])}</i>\n"
                f"💡 <i>Максимум: {MAX_FILTERS_PER_USER}</i>\n\n"
                f"🔄 <i>Удалите некоторые фильтры или уменьшите список</i>",
                parse_mode='HTML',
                reply_markup=get_multiple_filters_keyboard()
            )
            return
        
        await MultipleFiltersStates.waiting_location.set()
        
        # Формируем красивый список фильтров
        filters_text = "\n".join([f"• {f}" for f in data['selected_filters']])
        
        await message.answer(
            f"✅ <b>Список фильтров сохранен!</b>\n\n"
            f"📦 <b>Будет добавлено фильтров:</b> {len(data['selected_filters'])}\n\n"
            f"🔧 <b>Список фильтров:</b>\n{filters_text}\n\n"
            f"📍 <b>Укажите место установки для всех фильтров:</b>\n\n"
            f"💡 <i>Все фильтры будут установлены в одном месте</i>",
            parse_mode='HTML',
            reply_markup=get_location_keyboard()
        )
        return
    
    # Обработка кнопки "Добавить фильтр"
    if message.text == "➕ Добавить фильтр":
        await message.answer(
            "🔧 <b>Выберите тип фильтра:</b>\n\n"
            "💡 <i>Доступны все варианты фильтров</i>",
            parse_mode='HTML',
            reply_markup=get_filter_type_keyboard()
        )
        return
    
    # Обработка выбора типа фильтра из списка
    filter_mapping = {
        "🔧 Магистральный SL10": "Магистральный SL10",
        "🔧 Магистральный SL20": "Магистральный SL20",
        "💧 Гейзер": "Гейзер",
        "💧 Аквафор": "Аквафор"
    }
    
    if message.text in filter_mapping:
        filter_name = filter_mapping[message.text]
        if filter_name not in data['selected_filters']:
            data['selected_filters'].append(filter_name)
            
            # Формируем текущий список для отображения
            if data['selected_filters']:
                filters_text = "\n".join([f"• {f}" for f in data['selected_filters']])
                status_text = f"✅ <b>Выбрано фильтров:</b> {len(data['selected_filters'])}\n\n{filters_text}"
            else:
                status_text = "📝 <b>Список пуст</b>\n\n<i>Добавьте фильтры с помощью кнопок</i>"
            
            await message.answer(
                f"✅ <b>Добавлен:</b> {filter_name}\n\n"
                f"{status_text}\n\n"
                f"💡 <i>Продолжайте добавлять фильтры или нажмите '✅ Готово'</i>",
                parse_mode='HTML',
                reply_markup=get_multiple_filters_keyboard()
            )
        else:
            await message.answer(
                f"ℹ️ <b>Фильтр уже в списке:</b> {filter_name}",
                parse_mode='HTML',
                reply_markup=get_multiple_filters_keyboard()
            )
        return
    
    # Обработка текстового ввода (пользователь ввел свой фильтр)
    if message.text and message.text not in ["✅ Готово", "➕ Добавить фильтр", "❌ Отмена"]:
        try:
            validated_filter = validate_filter_name(message.text)
            if validated_filter not in data['selected_filters']:
                data['selected_filters'].append(validated_filter)
                
                # Формируем обновленный список
                filters_text = "\n".join([f"• {f}" for f in data['selected_filters']])
                
                await message.answer(
                    f"✅ <b>Добавлен:</b> {validated_filter}\n\n"
                    f"📊 Всего в списке: {len(data['selected_filters'])}\n\n"
                    f"🔧 <b>Текущий список:</b>\n{filters_text}",
                    parse_mode='HTML',
                    reply_markup=get_multiple_filters_keyboard()
                )
            else:
                await message.answer(
                    f"ℹ️ <b>Фильтр уже в списке:</b> {validated_filter}",
                    parse_mode='HTML',
                    reply_markup=get_multiple_filters_keyboard()
                )
        except ValueError as e:
            await message.answer(
                f"❌ <b>Ошибка в названии фильтра:</b>\n\n"
                f"💡 <i>{str(e)}</i>",
                parse_mode='HTML',
                reply_markup=get_multiple_filters_keyboard()
            )
        return
    
    # Обновляем отображение списка при любом другом сообщении
    if data['selected_filters']:
        filters_text = "\n".join([f"• {f}" for f in data['selected_filters']])
        status_text = f"✅ <b>Выбрано фильтров:</b> {len(data['selected_filters'])}\n\n{filters_text}"
    else:
        status_text = "📝 <b>Список пуст</b>\n\n<i>Добавьте фильтры с помощью кнопок</i>"
    
    await message.answer(
        f"📦 <b>Добавление фильтров</b>\n\n"
        f"{status_text}\n\n"
        f"💡 <i>Продолжайте добавлять фильтры или нажмите '✅ Готово'</i>",
        parse_mode='HTML',
        reply_markup=get_multiple_filters_keyboard()
    )

# ========== ЭКСПОРТ/ИМПОРТ EXCEL ==========

@dp.message_handler(lambda message: message.text == "📤 Экспорт в Excel")
async def cmd_export_excel(message: types.Message):
    """Экспорт фильтров в Excel"""
    filters = get_user_filters(message.from_user.id)
    
    if not filters:
        await message.answer(
            "📭 <b>Нет фильтров для экспорта</b>\n\n"
            "💫 <i>Добавьте фильтры перед использованием этой функции</i>",
            parse_mode='HTML',
            reply_markup=get_main_keyboard()
        )
        return
    
    try:
        # Создаем Excel файл
        filepath = export_filters_to_excel(message.from_user.id)
        
        if filepath:
            # Отправляем файл пользователю
            with open(filepath, 'rb') as file:
                await message.answer_document(
                    file,
                    caption="📤 <b>ЭКСПОРТ ФИЛЬТРОВ В EXCEL</b>\n\n"
                           f"✅ <b>Экспортировано фильтров:</b> {len(filters)}\n"
                           f"📅 <b>Дата экспорта:</b> {datetime.now().strftime('%d.%m.%Y %H:%M')}\n\n"
                           f"💡 <i>Файл содержит все ваши фильтры с датами замены</i>",
                    parse_mode='HTML'
                )
            
            # Удаляем временный файл
            os.remove(filepath)
        else:
            await message.answer(
                "❌ <b>Ошибка при создании файла</b>",
                parse_mode='HTML'
            )
            
    except Exception as e:
        logging.error(f"Ошибка экспорта в Excel: {e}")
        await message.answer(
            "❌ <b>Ошибка при экспорте в Excel</b>\n\n"
            f"💡 <i>{str(e)}</i>",
            parse_mode='HTML'
        )

@dp.message_handler(lambda message: message.text == "📥 Импорт из Excel")
async def cmd_import_excel(message: types.Message):
    """Начало импорта из Excel"""
    await ExcelStates.waiting_excel_file.set()
    
    await message.answer(
        "📥 <b>ИМПОРТ ФИЛЬТРОВ ИЗ EXCEL</b>\n\n"
        "💡 <b>Инструкция:</b>\n"
        "1. Подготовьте Excel файл со следующими колонками:\n"
        "   • <b>Тип фильтра</b> (обязательно)\n"
        "   • <b>Место установки</b> (обязательно)\n"
        "   • <b>Дата замены</b> (обязательно, формат ДД.ММ.ГГ)\n"
        "   • <b>Срок службы (дни)</b> (опционально)\n\n"
        "2. Отправьте Excel файл боту\n\n"
        "⚠️ <b>Внимание:</b>\n"
        "• Максимальное количество фильтров: 50\n"
        "• Существующие фильтры не будут удалены\n"
        "• Проверьте данные перед импортом\n\n"
        "📎 <b>Отправьте Excel файл:</b>",
        parse_mode='HTML',
        reply_markup=get_cancel_keyboard()
    )

@dp.message_handler(content_types=types.ContentType.DOCUMENT, state=ExcelStates.waiting_excel_file)
async def process_excel_file(message: types.Message, state: FSMContext):
    """Обработка Excel файла для импорта"""
    try:
        # Проверяем что это Excel файл
        if not message.document.file_name.endswith(('.xlsx', '.xls')):
            await message.answer(
                "❌ <b>Неверный формат файла!</b>\n\n"
                "💡 <i>Поддерживаются только файлы Excel (.xlsx, .xls)</i>",
                parse_mode='HTML',
                reply_markup=get_cancel_keyboard()
            )
            return
        
        # Скачиваем файл
        file_info = await bot.get_file(message.document.file_id)
        downloaded_file = await bot.download_file(file_info.file_path)
        
        # Сохраняем временный файл
        temp_file = f"temp_import_{message.from_user.id}.xlsx"
        with open(temp_file, 'wb') as file:
            file.write(downloaded_file.getvalue())
        
        # Импортируем данные
        imported_count, errors = import_filters_from_excel(temp_file, message.from_user.id)
        
        # Удаляем временный файл
        os.remove(temp_file)
        
        # Формируем ответ
        response = f"✅ <b>ИМПОРТ ЗАВЕРШЕН</b>\n\n"
        response += f"📦 <b>Успешно импортировано:</b> {imported_count} фильтров\n\n"
        
        if errors:
            response += f"⚠️ <b>Ошибки при импорте:</b>\n"
            for error in errors[:5]:  # Показываем только первые 5 ошибок
                response += f"• {error}\n"
            if len(errors) > 5:
                response += f"... и еще {len(errors) - 5} ошибок\n"
        
        await message.answer(response, parse_mode='HTML', reply_markup=get_main_keyboard())
        await state.finish()
        
    except Exception as e:
        await message.answer(
            f"❌ <b>Ошибка при импорте файла!</b>\n\n"
            f"💡 <i>{str(e)}</i>",
            parse_mode='HTML',
            reply_markup=get_cancel_keyboard()
        )

# ========== ОСНОВНЫЕ ОБРАБОТЧИКИ ==========

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
        "• 🔔 Автоматические напоминания\n"
        "• 📤📥 Импорт/экспорт в Excel",
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

# Добавление фильтра - выбор типа добавления
@dp.message_handler(lambda message: message.text == "✨ Добавить фильтр")
async def cmd_add(message: types.Message):
    # Проверяем лимит фильтров
    if check_filters_limit(message.from_user.id):
        await message.answer(
            f"❌ <b>Достигнут лимит фильтров!</b>\n\n"
            f"💡 <i>Максимальное количество фильтров: {MAX_FILTERS_PER_USER}</i>\n"
            f"📊 <i>Удалите некоторые фильтры перед добавлением новых</i>",
            parse_mode='HTML',
            reply_markup=get_main_keyboard()
        )
        return
        
    await message.answer(
        "🔧 <b>Выберите тип добавления:</b>\n\n"
        "💡 <i>Можно добавить один фильтр или сразу несколько</i>",
        parse_mode='HTML',
        reply_markup=get_add_filter_keyboard()
    )

# Обработка выбора типа добавления
@dp.message_handler(lambda message: message.text in ["🔧 Один фильтр", "📦 Несколько фильтров", "📤 Экспорт в Excel", "📥 Импорт из Excel"])
async def process_add_type(message: types.Message, state: FSMContext):
    if message.text == "🔧 Один фильтр":
        await FilterStates.waiting_filter_type.set()
        await message.answer(
            "🔧 <b>Выберите тип фильтра:</b>\n\n"
            "💡 <i>Доступны все варианты фильтров</i>",
            parse_mode='HTML',
            reply_markup=get_filter_type_keyboard()
        )
    elif message.text == "📦 Несколько фильтров":
        await cmd_multiple_filters(message, state)
    elif message.text == "📤 Экспорт в Excel":
        await cmd_export_excel(message)
    elif message.text == "📥 Импорт из Excel":
        await cmd_import_excel(message)

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

# ========== ФОНОВЫЕ ЗАДАЧИ ==========
async def check_expired_filters():
    """Фоновая задача для проверки просроченных фильтров"""
    try:
        with get_db_connection() as conn:
            cur = conn.cursor()
            cur.execute('''SELECT DISTINCT user_id, filter_type, location, expiry_date 
                          FROM filters 
                          WHERE expiry_date BETWEEN date('now') AND date('now', '+7 days')''')
            expiring_filters = cur.fetchall()
            
        for user_id, filter_type, location, expiry_date in expiring_filters:
            try:
                days_until_expiry = (datetime.strptime(str(expiry_date), '%Y-%m-%d').date() - datetime.now().date()).days
                expiry_date_nice = format_date_nice(datetime.strptime(str(expiry_date), '%Y-%m-%d').date())
                
                await bot.send_message(
                    user_id,
                    f"🔔 <b>Напоминание о замене фильтра</b>\n\n"
                    f"🔧 {filter_type}\n"
                    f"📍 {location}\n"
                    f"📅 Срок истекает: {expiry_date_nice}\n"
                    f"⏳ Осталось дней: {days_until_expiry}\n\n"
                    f"⚠️ <i>Рекомендуется заменить в ближайшее время</i>",
                    parse_mode='HTML'
                )
                await asyncio.sleep(0.1)
            except Exception as e:
                logging.error(f"Не удалось отправить уведомление пользователю {user_id}: {e}")
                    
    except Exception as e:
        logging.error(f"Ошибка при проверке просроченных фильтров: {e}")

async def schedule_daily_check():
    """Планировщик ежедневных проверок"""
    while True:
        try:
            await check_expired_filters()
            await asyncio.sleep(60 * 60)  # Ждем 1 час
        except Exception as e:
            logging.error(f"Ошибка в фоновой задаче: {e}")
            await asyncio.sleep(300)

async def on_startup(dp):
    """Действия при запуске бота"""
    logging.info("Бот запущен")
    asyncio.create_task(schedule_daily_check())

# Запуск бота
if __name__ == '__main__':
    # Проверка обязательных переменных
    if not API_TOKEN:
        logging.error("Токен бота не найден! Установите переменную TELEGRAM_BOT_TOKEN")
        exit(1)
    
    init_db()
    
    # Запуск с обработчиком startup
    executor.start_polling(
        dp, 
        skip_updates=True,
        on_startup=on_startup
    )
