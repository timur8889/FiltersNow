import logging
import sqlite3
import os
import asyncio
import shutil
import traceback
import re
import sys
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

def get_main_keyboard(user_id=None):
    """Главная клавиатура с проверкой прав администратора"""
    keyboard = types.ReplyKeyboardMarkup(resize_keyboard=True)
    keyboard.row(
        types.KeyboardButton("📋 Мои фильтры"),
        types.KeyboardButton("✨ Добавить фильтр")
    )
    keyboard.row(
        types.KeyboardButton("⏳ Сроки замены"),
        types.KeyboardButton("⚙️ Управление")
    )
    # Правильная проверка администратора
    if user_id and user_id == ADMIN_ID:
        keyboard.row(types.KeyboardButton("👑 Админ панель"))
    return keyboard

def get_filter_type_keyboard():
    """Клавиатура для выбора типа фильтра (убраны механический, престиж, кристалл, угольный)"""
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
    keyboard.row(types.KeyboardButton("➕ 1 фильтр"))
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

def create_expiry_infographic(filters):
    """Создание инфографики по срокам замены"""
    today = datetime.now().date()
    expired_count = 0
    expiring_soon_count = 0
    warning_count = 0
    ok_count = 0
    
    for f in filters:
        expiry_date = datetime.strptime(str(f['expiry_date']), '%Y-%m-%d').date()
        days_until_expiry = (expiry_date - today).days
        
        if days_until_expiry <= 0:
            expired_count += 1
        elif days_until_expiry <= 7:
            expiring_soon_count += 1
        elif days_until_expiry <= 30:
            warning_count += 1
        else:
            ok_count += 1
    
    total = len(filters)
    
    # Создаем текстовую инфографику
    infographic = "📊 <b>ИНФОГРАФИКА СРОКОВ ЗАМЕНЫ</b>\n\n"
    
    if expired_count > 0:
        infographic += f"🔴 <b>Просрочено:</b> {expired_count} фильтров\n"
        infographic += "   ⚠️ Требуется немедленная замена!\n\n"
    
    if expiring_soon_count > 0:
        infographic += f"🟡 <b>Срочно заменить:</b> {expiring_soon_count} фильтров\n"
        infographic += "   📅 Заменить в течение недели\n\n"
    
    if warning_count > 0:
        infographic += f"🟠 <b>Скоро заменить:</b> {warning_count} фильтров\n"
        infographic += "   📅 Заменить в течение месяца\n\n"
    
    if ok_count > 0:
        infographic += f"✅ <b>В норме:</b> {ok_count} фильтров\n"
        infographic += "   💧 Следующая замена через 30+ дней\n\n"
    
    # Прогресс-бар
    if total > 0:
        infographic += "📈 <b>Статус фильтров:</b>\n"
        infographic += "[" + "🔴" * min(expired_count, 10) + "🟡" * min(expiring_soon_count, 10) + "🟠" * min(warning_count, 10) + "✅" * min(ok_count, 10) + "]\n\n"
    
    infographic += f"📦 <b>Всего фильтров:</b> {total}"
    
    return infographic

def create_progress_bar(count, total, emoji):
    """Создание текстового прогресс-бара"""
    if total == 0:
        return ""
    
    percentage = count / total
    bars = int(percentage * 10)  # 10 символов для прогресс-бара
    return emoji + "█" * bars + "░" * (10 - bars)

def create_stats_infographic(stats, filters):
    """Создание визуальной инфографики статистики"""
    total = stats['total']
    
    # Создаем прогресс-бары
    expired_bar = create_progress_bar(stats['expired'], total, "🔴")
    expiring_7_bar = create_progress_bar(stats['expiring_7'], total, "🟡")
    expiring_30_bar = create_progress_bar(stats['expiring_30'], total, "🟠")
    ok_bar = create_progress_bar(stats['ok'], total, "✅")
    
    # Самый популярный тип фильтра
    most_common_type = max(stats['filter_types'].items(), key=lambda x: x[1]) if stats['filter_types'] else ("Нет данных", 0)
    
    # Самый популярный тип фильтра
    most_common_location = max(stats['locations'].items(), key=lambda x: x[1]) if stats['locations'] else ("Нет данных", 0)
    
    infographic = (
        "📊 <b>СТАТИСТИКА ФИЛЬТРОВ - ИНФОГРАФИКА</b>\n\n"
        
        "🎯 <b>ОБЩАЯ СТАТИСТИКА</b>\n"
        f"   📦 Всего фильтров: <b>{total}</b>\n"
        f"   ⚠️ Требуют внимания: <b>{stats['expired'] + stats['expiring_7'] + stats['expiring_30']}</b>\n"
        f"   ✅ В норме: <b>{stats['ok']}</b>\n\n"
        
        "📈 <b>РАСПРЕДЕЛЕНИЕ ПО СТАТУСАМ</b>\n"
        f"   {expired_bar} Просрочено: {stats['expired']} ({stats['expired']/total*100:.1f}%)\n"
        f"   {expiring_7_bar} Срочно: {stats['expiring_7']} ({stats['expiring_7']/total*100:.1f}%)\n"
        f"   {expiring_30_bar} Скоро: {stats['expiring_30']} ({stats['expiring_30']/total*100:.1f}%)\n"
        f"   {ok_bar} Норма: {stats['ok']} ({stats['ok']/total*100:.1f}%)\n\n"
        
        "🏆 <b>САМЫЕ ПОПУЛЯРНЫЕ</b>\n"
        f"   🔧 Тип фильтра: <b>{most_common_type[0]}</b> ({most_common_type[1]} шт.)\n"
        f"   📍 Место: <b>{most_common_location[0]}</b> ({most_common_location[1]} шт.)\n\n"
    )
    
    # Добавляем информацию о ближайшем сроке
    if stats['nearest_expiry'] is not None:
        if stats['nearest_expiry'] <= 0:
            infographic += f"🚨 <b>БЛИЖАЙШИЙ СРОК:</b> ПРОСРОЧЕН на {abs(stats['nearest_expiry'])} дн.\n"
        else:
            infographic += f"⏰ <b>БЛИЖАЙШИЙ СРОК:</b> через {stats['nearest_expiry']} дн.\n"
    
    return infographic

def create_detailed_stats(stats, filters):
    """Создание детальной статистики"""
    total = stats['total']
    
    detailed = "📋 <b>ДЕТАЛЬНАЯ СТАТИСТИКА</b>\n\n"
    
    # Статистика по типам фильтров
    if stats['filter_types']:
        detailed += "🔧 <b>РАСПРЕДЕЛЕНИЕ ПО ТИПАМ:</b>\n"
        for filter_type, count in sorted(stats['filter_types'].items(), key=lambda x: x[1], reverse=True)[:5]:  # Топ-5
            percentage = count / total * 100
            bar = create_progress_bar(count, total, "●")
            detailed += f"   {bar} {filter_type}: {count} ({percentage:.1f}%)\n"
        detailed += "\n"
    
    # Статистика по местам установки
    if stats['locations']:
        detailed += "📍 <b>РАСПРЕДЕЛЕНИЕ ПО МЕСТАМ:</b>\n"
        for location, count in sorted(stats['locations'].items(), key=lambda x: x[1], reverse=True)[:5]:  # Топ-5
            percentage = count / total * 100
            bar = create_progress_bar(count, total, "●")
            detailed += f"   {bar} {location}: {count} ({percentage:.1f}%)\n"
        detailed += "\n"
    
    # Средний срок службы
    if stats['ok'] > 0:
        avg_days = stats['total_days_remaining'] / stats['ok']
        detailed += f"📅 <b>СРЕДНИЙ СРОК У НОРМАЛЬНЫХ ФИЛЬТРОВ:</b> {avg_days:.1f} дней\n\n"
    
    # Рекомендации
    recommendations = []
    if stats['expired'] > 0:
        recommendations.append(f"🚨 Заменить {stats['expired']} просроченных фильтров")
    if stats['expiring_7'] > 0:
        recommendations.append(f"⚠️ Подготовиться к замене {stats['expiring_7']} срочных фильтров")
    if stats['expiring_30'] > 0:
        recommendations.append(f"📝 Запланировать замену {stats['expiring_30']} фильтров в течение месяца")
    
    if recommendations:
        detailed += "💡 <b>РЕКОМЕНДАЦИИ:</b>\n" + "\n".join([f"   • {rec}" for rec in recommendations])
    else:
        detailed += "🎉 <b>Отличная работа! Все фильтры в норме.</b>"
    
    return detailed

# ========== ФОНОВЫЕ ЗАДАЧИ И ОБРАБОТКА ОШИБОК ==========
async def check_expired_filters():
    """Фоновая задача для проверки просроченных фильтров"""
    try:
        with get_db_connection() as conn:
            cur = conn.cursor()
            
            # Фильтры, которые истекают в ближайшие 7 дней
            cur.execute('''SELECT DISTINCT user_id, filter_type, location, expiry_date 
                          FROM filters 
                          WHERE expiry_date BETWEEN date('now') AND date('now', '+7 days')''')
            expiring_filters = cur.fetchall()
            
            # Фильтры, которые уже просрочены (но не более 30 дней назад)
            cur.execute('''SELECT DISTINCT user_id, filter_type, location, expiry_date 
                          FROM filters 
                          WHERE expiry_date BETWEEN date('now', '-30 days') AND date('now', '-1 day')''')
            expired_filters = cur.fetchall()
        
        notified_users = set()
        
        # Уведомления о скором истечении срока
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
                notified_users.add(user_id)
                await asyncio.sleep(0.1)  # Небольшая задержка между сообщениями
            except Exception as e:
                logging.error(f"Не удалось отправить уведомление пользователю {user_id}: {e}")
        
        # Уведомления о просроченных фильтрах
        for user_id, filter_type, location, expiry_date in expired_filters:
            if user_id not in notified_users:  # Не спамим пользователям, которые уже получили уведомление
                try:
                    days_expired = (datetime.now().date() - datetime.strptime(str(expiry_date), '%Y-%m-%d').date()).days
                    expiry_date_nice = format_date_nice(datetime.strptime(str(expiry_date), '%Y-%m-%d').date())
                    
                    await bot.send_message(
                        user_id,
                        f"🚨 <b>СРОЧНОЕ УВЕДОМЛЕНИЕ</b>\n\n"
                        f"🔧 {filter_type}\n"
                        f"📍 {location}\n"
                        f"📅 Срок истек: {expiry_date_nice}\n"
                        f"⏰ Просрочено дней: {days_expired}\n\n"
                        f"❌ <i>Требуется немедленная замена!</i>",
                        parse_mode='HTML'
                    )
                    await asyncio.sleep(0.1)
                except Exception as e:
                    logging.error(f"Не удалось отправить срочное уведомление пользователю {user_id}: {e}")
                    
    except Exception as e:
        logging.error(f"Ошибка при проверке просроченных фильтров: {e}")

# Глобальный обработчик ошибок
@dp.errors_handler()
async def errors_handler(update, exception):
    """Глобальный обработчик ошибок"""
    logging.error(f"Ошибка: {exception}\n{traceback.format_exc()}")
    
    try:
        # Отправляем сообщение администратору
        await bot.send_message(
            ADMIN_ID,
            f"❌ Ошибка в боте:\n\n"
            f"Тип: {type(exception).__name__}\n"
            f"Ошибка: {str(exception)[:1000]}"
        )
    except Exception as e:
        logging.error(f"Не удалось отправить сообщение об ошибке администратору: {e}")
    
    return True

# Запуск фоновой задачи
async def schedule_daily_check():
    """Планировщик ежедневных проверок"""
    while True:
        try:
            await check_expired_filters()
            # Создаем резервную копию раз в день в 3:00
            if datetime.now().hour == 3 and datetime.now().minute == 0:
                backup_database()
                await asyncio.sleep(60)  # Ждем минуту чтобы не повторять
        except Exception as e:
            logging.error(f"Ошибка в фоновой задаче: {e}")
            await asyncio.sleep(300)  # Ждем 5 минут при ошибке
        
        # Ожидаем 1 час до следующей проверки
        await asyncio.sleep(60 * 60)

async def on_startup(dp):
    """Действия при запуске бота"""
    logging.info("Бот запущен")
    
    # Создаем резервную копию при запуске
    backup_database()
    
    # Запускаем фоновую задачу
    asyncio.create_task(schedule_daily_check())
    
    # Уведомляем администратора о запуске
    try:
        await bot.send_message(ADMIN_ID, "🤖 Бот успешно запущен и работает")
    except Exception as e:
        logging.error(f"Не удалось отправить уведомление администратору: {e}")

# ========== НОВЫЕ ОБРАБОТЧИКИ ДЛЯ НЕСКОЛЬКИХ ФИЛЬТРОВ ==========

@dp.message_handler(state=MultipleFiltersStates.waiting_change_date)
async def process_multiple_change_date(message: types.Message, state: FSMContext):
    """Обработка даты замены для нескольких фильтров"""
    if message.text == "❌ Отмена":
        await state.finish()
        await message.answer("🚫 Добавление отменено", reply_markup=get_main_keyboard(message.from_user.id))
        return
    
    try:
        change_date = validate_date(message.text)
        
        async with state.proxy() as data:
            data['change_date'] = change_date
            selected_filters = data['selected_filters']
            location = data['location']
        
        await MultipleFiltersStates.next()
        
        await message.answer(
            f"⏱️ <b>Срок службы для всех фильтров</b>\n\n"
            f"📍 <i>Место:</i> {location}\n"
            f"📅 <i>Дата замены:</i> {format_date_nice(change_date)}\n"
            f"📦 <i>Количество фильтров:</i> {len(selected_filters)}\n\n"
            f"📝 <b>Выберите или введите срок службы в днях:</b>",
            parse_mode='HTML',
            reply_markup=get_lifetime_keyboard()
        )
        
    except ValueError as e:
        today_nice = format_date_nice(datetime.now().date())
        await message.answer(
            f"❌ <b>Ошибка в дате!</b>\n\n"
            f"💡 <i>{str(e)}</i>\n\n"
            f"📝 <b>Введите дату замены в формате ДД.ММ.ГГ:</b>\n"
            f"<i>Например: {today_nice}</i>",
            parse_mode='HTML',
            reply_markup=get_cancel_keyboard()
        )

@dp.message_handler(state=MultipleFiltersStates.waiting_lifetime)
async def process_multiple_lifetime(message: types.Message, state: FSMContext):
    """Обработка срока службы для нескольких фильтров"""
    if message.text == "❌ Отмена":
        await state.finish()
        await message.answer("🚫 Добавление отменено", reply_markup=get_main_keyboard(message.from_user.id))
        return
    
    lifetime_mapping = {
        "3️⃣ 90 дней": 90,
        "6️⃣ 180 дней": 180,
        "1️⃣ 365 дней": 365
    }
    
    try:
        if message.text in lifetime_mapping:
            lifetime = lifetime_mapping[message.text]
        elif message.text == "📅 Другое количество":
            await message.answer(
                "📅 <b>Введите срок службы в днях:</b>\n\n"
                "💡 <i>Например: 30, 60, 90, 180, 365 и т.д.</i>",
                parse_mode='HTML',
                reply_markup=get_cancel_keyboard()
            )
            return
        else:
            lifetime = validate_lifetime(message.text)
        
        async with state.proxy() as data:
            change_date = data['change_date']
            selected_filters = data['selected_filters']
            location = data['location']
            
            # Сохраняем все фильтры в БД
            with get_db_connection() as conn:
                cur = conn.cursor()
                for filter_type in selected_filters:
                    expiry_date = change_date + timedelta(days=lifetime)
                    cur.execute('''INSERT INTO filters 
                                (user_id, filter_type, location, last_change, expiry_date, lifetime_days) 
                                VALUES (?, ?, ?, ?, ?, ?)''',
                                (message.from_user.id, filter_type, location, change_date, expiry_date, lifetime))
                conn.commit()
        
        filters_list = "\n".join([f"• {f}" for f in selected_filters])
        
        await message.answer(
            f"🎉 <b>ФИЛЬТРЫ УСПЕШНО ДОБАВЛЕНЫ!</b>\n\n"
            f"📦 <b>Добавлено фильтров:</b> {len(selected_filters)}\n"
            f"📍 <b>Место:</b> {location}\n"
            f"📅 <b>Дата замены:</b> {format_date_nice(change_date)}\n"
            f"⏱️ <b>Срок службы:</b> {lifetime} дней\n\n"
            f"🔧 <b>Список фильтров:</b>\n{filters_list}\n\n"
            f"💫 <i>Теперь вы можете отслеживать срок их замены</i>",
            parse_mode='HTML',
            reply_markup=get_main_keyboard(message.from_user.id)
        )
        
        await state.finish()
        
    except ValueError as e:
        await message.answer(
            f"❌ <b>Ошибка в сроке службы!</b>\n\n"
            f"💡 <i>{str(e)}</i>\n\n"
            f"📝 <b>Введите корректное число дней:</b>",
            parse_mode='HTML',
            reply_markup=get_lifetime_keyboard()
        )

# ========== ОБРАБОТЧИКИ ДЛЯ УПРАВЛЕНИЯ ФИЛЬТРАМИ ==========

@dp.message_handler(lambda message: message.text == "📋 Мои фильтры")
async def cmd_my_filters(message: types.Message):
    """Показать все фильтры пользователя"""
    filters = get_user_filters(message.from_user.id)
    
    if not filters:
        await message.answer(
            "📭 <b>У вас пока нет фильтров</b>\n\n"
            "💫 <i>Добавьте первый фильтр с помощью кнопки '✨ Добавить фильтр'</i>",
            parse_mode='HTML',
            reply_markup=get_main_keyboard(message.from_user.id)
        )
        return
    
    today = datetime.now().date()
    response = "📋 <b>ВАШИ ФИЛЬТРЫ</b>\n\n"
    
    for f in filters:
        expiry_date = datetime.strptime(str(f['expiry_date']), '%Y-%m-%d').date()
        last_change = datetime.strptime(str(f['last_change']), '%Y-%m-%d').date()
        days_until_expiry = (expiry_date - today).days
        
        icon, status = get_status_icon_and_text(days_until_expiry)
        
        response += (
            f"{icon} <b>#{f['id']} {f['filter_type']}</b>\n"
            f"📍 {f['location']}\n"
            f"📅 Заменен: {format_date_nice(last_change)}\n"
            f"🗓️ Годен до: {format_date_nice(expiry_date)}\n"
            f"⏱️ Осталось дней: <b>{days_until_expiry}</b>\n"
            f"📊 Статус: <b>{status}</b>\n\n"
        )
    
    # Добавляем инфографику
    infographic = create_expiry_infographic(filters)
    await message.answer(response, parse_mode='HTML')
    await message.answer(infographic, parse_mode='HTML', reply_markup=get_main_keyboard(message.from_user.id))

@dp.message_handler(lambda message: message.text == "⏳ Сроки замены")
async def cmd_expiry_dates(message: types.Message):
    """Показать сроки замены с инфографикой"""
    filters = get_user_filters(message.from_user.id)
    
    if not filters:
        await message.answer(
            "📭 <b>У вас пока нет фильтров</b>\n\n"
            "💫 <i>Добавьте первый фильтр для отслеживания сроков замены</i>",
            parse_mode='HTML',
            reply_markup=get_main_keyboard(message.from_user.id)
        )
        return
    
    infographic = create_expiry_infographic(filters)
    await message.answer(infographic, parse_mode='HTML', reply_markup=get_main_keyboard(message.from_user.id))

@dp.message_handler(lambda message: message.text == "📊 Статистика")
async def cmd_statistics(message: types.Message):
    """Показать статистику по фильтрам"""
    filters = get_user_filters(message.from_user.id)
    
    if not filters:
        await message.answer(
            "📭 <b>У вас пока нет фильтров</b>\n\n"
            "💫 <i>Добавьте фильтры для просмотра статистики</i>",
            parse_mode='HTML',
            reply_markup=get_main_keyboard(message.from_user.id)
        )
        return
    
    today = datetime.now().date()
    stats = {
        'total': len(filters),
        'expired': 0,
        'expiring_7': 0,
        'expiring_30': 0,
        'ok': 0,
        'filter_types': {},
        'locations': {},
        'total_days_remaining': 0,
        'nearest_expiry': None
    }
    
    for f in filters:
        expiry_date = datetime.strptime(str(f['expiry_date']), '%Y-%m-%d').date()
        days_until_expiry = (expiry_date - today).days
        
        # Статистика по статусам
        if days_until_expiry <= 0:
            stats['expired'] += 1
        elif days_until_expiry <= 7:
            stats['expiring_7'] += 1
        elif days_until_expiry <= 30:
            stats['expiring_30'] += 1
        else:
            stats['ok'] += 1
            stats['total_days_remaining'] += days_until_expiry
        
        # Статистика по типам
        filter_type = f['filter_type']
        stats['filter_types'][filter_type] = stats['filter_types'].get(filter_type, 0) + 1
        
        # Статистика по местам
        location = f['location']
        stats['locations'][location] = stats['locations'].get(location, 0) + 1
        
        # Ближайший срок
        if stats['nearest_expiry'] is None or days_until_expiry < stats['nearest_expiry']:
            stats['nearest_expiry'] = days_until_expiry
    
    # Создаем инфографику
    infographic = create_stats_infographic(stats, filters)
    detailed_stats = create_detailed_stats(stats, filters)
    
    await message.answer(infographic, parse_mode='HTML')
    await message.answer(detailed_stats, parse_mode='HTML', reply_markup=get_main_keyboard(message.from_user.id))

# ========== ОБРАБОТЧИКИ УДАЛЕНИЯ ФИЛЬТРОВ ==========

@dp.message_handler(lambda message: message.text == "🗑️ Удалить")
async def cmd_delete_filter(message: types.Message):
    """Начало процесса удаления фильтра"""
    filters = get_user_filters(message.from_user.id)
    
    if not filters:
        await message.answer(
            "📭 <b>Нет фильтров для удаления</b>\n\n"
            "💫 <i>Добавьте фильтры перед использованием этой функции</i>",
            parse_mode='HTML',
            reply_markup=get_main_keyboard(message.from_user.id)
        )
        return
    
    await DeleteFilterStates.waiting_filter_selection.set()
    
    filters_list = "\n".join([f"• #{f['id']} {f['filter_type']} - {f['location']}" for f in filters])
    
    await message.answer(
        f"🗑️ <b>УДАЛЕНИЕ ФИЛЬТРА</b>\n\n"
        f"📋 <b>Ваши фильтры:</b>\n{filters_list}\n\n"
        f"🔢 <b>Выберите фильтр для удаления:</b>\n"
        f"<i>Нажмите на соответствующий номер фильтра</i>",
        parse_mode='HTML',
        reply_markup=get_filters_list_keyboard(filters, "delete")
    )

@dp.message_handler(state=DeleteFilterStates.waiting_filter_selection)
async def process_delete_filter_selection(message: types.Message, state: FSMContext):
    """Обработка выбора фильтра для удаления"""
    if message.text == "🔙 Главное меню":
        await state.finish()
        await message.answer("🏠 Возврат в главное меню", reply_markup=get_main_keyboard(message.from_user.id))
        return
    
    # Парсим ID фильтра из текста (формат: #ID Тип - Место)
    match = re.match(r'#(\d+)', message.text)
    if match:
        filter_id = int(match.group(1))
        filter_data = get_filter_by_id(filter_id, message.from_user.id)
        
        if filter_data:
            expiry_date_nice = format_date_nice(datetime.strptime(str(filter_data['expiry_date']), '%Y-%m-%d').date())
            
            await message.answer(
                f"🗑️ <b>ПОДТВЕРЖДЕНИЕ УДАЛЕНИЯ</b>\n\n"
                f"🔧 <b>Фильтр:</b> #{filter_id} {filter_data['filter_type']}\n"
                f"📍 <b>Место:</b> {filter_data['location']}\n"
                f"🗓️ <b>Годен до:</b> {expiry_date_nice}\n\n"
                f"❓ <b>Вы уверены что хотите удалить этот фильтр?</b>\n"
                f"<i>Это действие нельзя отменить</i>",
                parse_mode='HTML',
                reply_markup=get_confirmation_keyboard(filter_id, "delete")
            )
            await state.finish()
        else:
            await message.answer(
                "❌ <b>Фильтр не найден!</b>\n\n"
                "💡 <i>Выберите фильтр из списка</i>",
                parse_mode='HTML',
                reply_markup=get_filters_list_keyboard(get_user_filters(message.from_user.id), "delete")
            )
    else:
        await message.answer(
            "❌ <b>Неверный формат!</b>\n\n"
            "💡 <i>Выберите фильтр из списка кнопок</i>",
            parse_mode='HTML',
            reply_markup=get_filters_list_keyboard(get_user_filters(message.from_user.id), "delete")
        )

@dp.callback_query_handler(lambda c: c.data.startswith('confirm_delete_'))
async def process_confirm_delete(callback_query: types.CallbackQuery):
    """Подтверждение удаления фильтра"""
    filter_id = int(callback_query.data.split('_')[2])
    user_id = callback_query.from_user.id
    
    filter_data = get_filter_by_id(filter_id, user_id)
    if not filter_data:
        await callback_query.answer("❌ Фильтр не найден!")
        return
    
    # Удаляем фильтр из БД
    with get_db_connection() as conn:
        cur = conn.cursor()
        cur.execute("DELETE FROM filters WHERE id = ? AND user_id = ?", (filter_id, user_id))
        conn.commit()
    
    await callback_query.message.edit_text(
        f"✅ <b>ФИЛЬТР УДАЛЕН!</b>\n\n"
        f"🗑️ <b>Удален фильтр:</b> #{filter_id} {filter_data['filter_type']}\n"
        f"📍 <b>Место:</b> {filter_data['location']}\n\n"
        f"💫 <i>Фильтр успешно удален из вашего списка</i>",
        parse_mode='HTML'
    )
    
    await bot.send_message(
        user_id,
        "🏠 Возврат в главное меню",
        reply_markup=get_main_keyboard(user_id)
    )

@dp.callback_query_handler(lambda c: c.data == 'cancel_delete')
async def process_cancel_delete(callback_query: types.CallbackQuery):
    """Отмена удаления фильтра"""
    await callback_query.message.edit_text(
        "🚫 <b>УДАЛЕНИЕ ОТМЕНЕНО</b>\n\n"
        "💡 <i>Фильтр не был удален</i>",
        parse_mode='HTML'
    )
    
    await bot.send_message(
        callback_query.from_user.id,
        "🏠 Возврат в главное меню",
        reply_markup=get_main_keyboard(callback_query.from_user.id)
    )

# ========== ИСПРАВЛЕННЫЙ РАЗДЕЛ РЕДАКТИРОВАНИЯ ==========

@dp.message_handler(lambda message: message.text == "✏️ Редактировать")
async def cmd_edit_filter(message: types.Message):
    """Начало процесса редактирования фильтра"""
    filters = get_user_filters(message.from_user.id)
    
    if not filters:
        await message.answer(
            "📭 <b>Нет фильтров для редактирования</b>\n\n"
            "💫 <i>Добавьте фильтры перед использованием этой функции</i>",
            parse_mode='HTML',
            reply_markup=get_main_keyboard(message.from_user.id)
        )
        return
    
    await EditFilterStates.waiting_filter_selection.set()
    
    filters_list = "\n".join([f"• #{f['id']} {f['filter_type']} - {f['location']}" for f in filters])
    
    await message.answer(
        f"✏️ <b>РЕДАКТИРОВАНИЕ ФИЛЬТРА</b>\n\n"
        f"📋 <b>Ваши фильтры:</b>\n{filters_list}\n\n"
        f"🔢 <b>Выберите фильтр для редактирования:</b>\n"
        f"<i>Нажмите на соответствующий номер фильтра</i>",
        parse_mode='HTML',
        reply_markup=get_filters_list_keyboard(filters, "edit")
    )

@dp.message_handler(state=EditFilterStates.waiting_filter_selection)
async def process_edit_filter_selection(message: types.Message, state: FSMContext):
    if message.text == "🔙 Главное меню":
        await state.finish()
        await message.answer("🏠 Возврат в главное меню", reply_markup=get_main_keyboard(message.from_user.id))
        return
    
    # Парсим ID фильтра из текста (формат: #ID Тип - Место)
    match = re.match(r'#(\d+)', message.text)
    if match:
        filter_id = int(match.group(1))
        filter_data = get_filter_by_id(filter_id, message.from_user.id)
        
        if filter_data:
            async with state.proxy() as data:
                data['editing_filter'] = filter_data
                data['filter_id'] = filter_id
            
            expiry_date_nice = format_date_nice(datetime.strptime(str(filter_data['expiry_date']), '%Y-%m-%d').date())
            last_change_nice = format_date_nice(datetime.strptime(str(filter_data['last_change']), '%Y-%m-%d').date())
            
            await EditFilterStates.waiting_field_selection.set()
            await message.answer(
                f"✏️ <b>РЕДАКТИРОВАНИЕ ФИЛЬТРА</b>\n\n"
                f"🔧 <b>Тип:</b> {filter_data['filter_type']}\n"
                f"📍 <b>Место:</b> {filter_data['location']}\n"
                f"📅 <b>Заменен:</b> {last_change_nice}\n"
                f"⏱️ <b>Срок службы:</b> {filter_data['lifetime_days']} дн.\n"
                f"🗓️ <b>Годен до:</b> {expiry_date_nice}\n\n"
                f"🔄 <b>Что вы хотите изменить?</b>",
                parse_mode='HTML',
                reply_markup=get_edit_filter_keyboard()
            )
        else:
            await message.answer(
                "❌ <b>Фильтр не найден!</b>\n\n"
                "💡 <i>Выберите фильтр из списка</i>",
                parse_mode='HTML',
                reply_markup=get_filters_list_keyboard(get_user_filters(message.from_user.id), "edit")
            )
    else:
        await message.answer(
            "❌ <b>Неверный формат!</b>\n\n"
            "💡 <i>Выберите фильтр из списка кнопок</i>",
            parse_mode='HTML',
            reply_markup=get_filters_list_keyboard(get_user_filters(message.from_user.id), "edit")
        )

@dp.message_handler(state=EditFilterStates.waiting_field_selection)
async def process_edit_field_selection(message: types.Message, state: FSMContext):
    if message.text == "🔙 Главное меню":
        await state.finish()
        await message.answer("🏠 Возврат в главное меню", reply_markup=get_main_keyboard(message.from_user.id))
        return
    
    if message.text == "🔙 К списку фильтров":
        await EditFilterStates.waiting_filter_selection.set()
        filters = get_user_filters(message.from_user.id)
        filters_list = "\n".join([f"• #{f['id']} {f['filter_type']} - {f['location']}" for f in filters])
        
        await message.answer(
            f"✏️ <b>РЕДАКТИРОВАНИЕ ФИЛЬТРА</b>\n\n"
            f"📋 <b>Ваши фильтры:</b>\n{filters_list}\n\n"
            f"🔢 <b>Выберите фильтр для редактирования:</b>",
            parse_mode='HTML',
            reply_markup=get_filters_list_keyboard(filters, "edit")
        )
        return
    
    async with state.proxy() as data:
        filter_data = data['editing_filter']
    
    if message.text == "✏️ Тип фильтра":
        await EditFilterStates.waiting_new_value.set()
        data['editing_field'] = 'filter_type'
        await message.answer(
            f"✏️ <b>ИЗМЕНЕНИЕ ТИПА ФИЛЬТРА</b>\n\n"
            f"🔧 <b>Текущий тип:</b> {filter_data['filter_type']}\n\n"
            f"📝 <b>Введите новый тип фильтра:</b>",
            parse_mode='HTML',
            reply_markup=get_cancel_keyboard()
        )
    
    elif message.text == "📍 Место установки":
        await EditFilterStates.waiting_new_value.set()
        data['editing_field'] = 'location'
        await message.answer(
            f"✏️ <b>ИЗМЕНЕНИЕ МЕСТА УСТАНОВКИ</b>\n\n"
            f"📍 <b>Текущее место:</b> {filter_data['location']}\n\n"
            f"📝 <b>Введите новое место установки:</b>",
            parse_mode='HTML',
            reply_markup=get_cancel_keyboard()
        )
    
    elif message.text == "📅 Дата замены":
        await EditFilterStates.waiting_new_value.set()
        data['editing_field'] = 'last_change'
        last_change_nice = format_date_nice(datetime.strptime(str(filter_data['last_change']), '%Y-%m-%d').date())
        await message.answer(
            f"✏️ <b>ИЗМЕНЕНИЕ ДАТЫ ЗАМЕНЫ</b>\n\n"
            f"📅 <b>Текущая дата замены:</b> {last_change_nice}\n\n"
            f"📝 <b>Введите новую дату замены (ДД.ММ.ГГ):</b>",
            parse_mode='HTML',
            reply_markup=get_cancel_keyboard()
        )
    
    elif message.text == "⏱️ Срок службы":
        await EditFilterStates.waiting_new_value.set()
        data['editing_field'] = 'lifetime_days'
        await message.answer(
            f"✏️ <b>ИЗМЕНЕНИЕ СРОКА СЛУЖБЫ</b>\n\n"
            f"⏱️ <b>Текущий срок службы:</b> {filter_data['lifetime_days']} дней\n\n"
            f"📝 <b>Введите новый срок службы в днях:</b>",
            parse_mode='HTML',
            reply_markup=get_cancel_keyboard()
        )

@dp.message_handler(state=EditFilterStates.waiting_new_value)
async def process_edit_new_value(message: types.Message, state: FSMContext):
    if message.text == "❌ Отмена":
        await EditFilterStates.waiting_field_selection.set()
        async with state.proxy() as data:
            filter_data = data['editing_filter']
        
        expiry_date_nice = format_date_nice(datetime.strptime(str(filter_data['expiry_date']), '%Y-%m-%d').date())
        last_change_nice = format_date_nice(datetime.strptime(str(filter_data['last_change']), '%Y-%m-%d').date())
        
        await message.answer(
            f"✏️ <b>РЕДАКТИРОВАНИЕ ФИЛЬТРА</b>\n\n"
            f"🔧 <b>Тип:</b> {filter_data['filter_type']}\n"
            f"📍 <b>Место:</b> {filter_data['location']}\n"
            f"📅 <b>Заменен:</b> {last_change_nice}\n"
            f"⏱️ <b>Срок службы:</b> {filter_data['lifetime_days']} дн.\n"
            f"🗓️ <b>Годен до:</b> {expiry_date_nice}\n\n"
            f"🔄 <b>Что вы хотите изменить?</b>",
            parse_mode='HTML',
            reply_markup=get_edit_filter_keyboard()
        )
        return
    
    async with state.proxy() as data:
        filter_data = data['editing_filter']
        field = data['editing_field']
        filter_id = data['filter_id']
    
    try:
        if field == 'filter_type':
            new_value = validate_filter_name(message.text)
            update_query = "UPDATE filters SET filter_type = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?"
            params = (new_value, filter_id)
        
        elif field == 'location':
            new_value = safe_db_string(message.text)
            update_query = "UPDATE filters SET location = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?"
            params = (new_value, filter_id)
        
        elif field == 'last_change':
            new_value = parse_date(message.text)
            # При изменении даты замены пересчитываем expiry_date
            lifetime = filter_data['lifetime_days']
            new_expiry_date = new_value + timedelta(days=lifetime)
            update_query = "UPDATE filters SET last_change = ?, expiry_date = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?"
            params = (new_value, new_expiry_date, filter_id)
        
        elif field == 'lifetime_days':
            new_value = validate_lifetime(message.text)
            # При изменении срока службы пересчитываем expiry_date
            last_change = datetime.strptime(str(filter_data['last_change']), '%Y-%m-%d').date()
            new_expiry_date = last_change + timedelta(days=new_value)
            update_query = "UPDATE filters SET lifetime_days = ?, expiry_date = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?"
            params = (new_value, new_expiry_date, filter_id)
        
        # Выполняем обновление в БД
        with get_db_connection() as conn:
            cur = conn.cursor()
            cur.execute(update_query, params)
            conn.commit()
        
        field_names = {
            'filter_type': 'тип фильтра',
            'location': 'место установки',
            'last_change': 'дата замены',
            'lifetime_days': 'срок службы'
        }
        
        await message.answer(
            f"✅ <b>ИЗМЕНЕНИЯ СОХРАНЕНЫ!</b>\n\n"
            f"✏️ <b>Изменено поле:</b> {field_names[field]}\n"
            f"🔧 <b>Фильтр:</b> #{filter_id}\n\n"
            f"💫 <i>Данные успешно обновлены</i>",
            parse_mode='HTML',
            reply_markup=get_main_keyboard(message.from_user.id)
        )
        await state.finish()
        
    except ValueError as e:
        await message.answer(
            f"❌ <b>Ошибка в данных!</b>\n\n"
            f"💡 <i>{str(e)}</i>\n\n"
            f"📝 <i>Попробуйте ввести данные еще раз</i>",
            parse_mode='HTML',
            reply_markup=get_cancel_keyboard()
        )

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
            reply_markup=get_main_keyboard(message.from_user.id)
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
        "• <b>➕ 1 фильтр</b> - быстро добавить один фильтр\n"
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
        await message.answer("🚫 Добавление фильтров отменено", reply_markup=get_main_keyboard(message.from_user.id))
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
    
    # Обработка кнопки "+ 1 фильтр"
    if message.text == "➕ 1 фильтр":
        # Просто добавляем "Фильтр" в список
        new_filter = "Фильтр"
        data['selected_filters'].append(new_filter)
        
        # Формируем текущий список для отображения
        if data['selected_filters']:
            filters_text = "\n".join([f"• {f}" for f in data['selected_filters']])
            status_text = f"✅ <b>Выбрано фильтров:</b> {len(data['selected_filters'])}\n\n{filters_text}"
        else:
            status_text = "📝 <b>Список пуст</b>\n\n<i>Добавьте фильтры с помощью кнопок</i>"
        
        await message.answer(
            f"✅ <b>Добавлен:</b> {new_filter}\n\n"
            f"{status_text}\n\n"
            f"💡 <i>Продолжайте добавлять фильтры или нажмите '✅ Готово'</i>",
            parse_mode='HTML',
            reply_markup=get_multiple_filters_keyboard()
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
    if message.text and message.text not in ["✅ Готово", "➕ Добавить фильтр", "➕ 1 фильтр", "❌ Отмена"]:
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

# ========== АДМИН ПАНЕЛЬ ==========

def is_admin(user_id: int) -> bool:
    """Проверка прав администратора"""
    return user_id == ADMIN_ID

@dp.message_handler(lambda message: message.text == "👑 Админ панель")
async def cmd_admin_panel(message: types.Message):
    """Админ панель"""
    if not is_admin(message.from_user.id):
        await message.answer("❌ <b>Доступ запрещен!</b>", parse_mode='HTML')
        return
    
    await message.answer(
        "👑 <b>АДМИН ПАНЕЛЬ</b>\n\n"
        "💡 <i>Управление базой данных и статистика</i>",
        parse_mode='HTML',
        reply_markup=get_admin_keyboard()
    )

@dp.message_handler(lambda message: message.text == "📊 Общая статистика")
async def cmd_admin_stats(message: types.Message):
    """Общая статистика для админа"""
    if not is_admin(message.from_user.id):
        await message.answer("❌ <b>Доступ запрещен!</b>", parse_mode='HTML')
        return
    
    stats = get_all_users_stats()
    users = get_all_users()
    
    response = (
        "📊 <b>ОБЩАЯ СТАТИСТИКА СИСТЕМЫ</b>\n\n"
        f"👥 <b>Всего пользователей:</b> {stats['total_users']}\n"
        f"📦 <b>Всего фильтров:</b> {stats['total_filters']}\n"
        f"🔴 <b>Просрочено фильтров:</b> {stats['expired_filters']}\n"
        f"🟡 <b>Скоро истекает:</b> {stats['expiring_soon']}\n\n"
    )
    
    if users:
        response += "👥 <b>ТОП пользователей по количеству фильтров:</b>\n"
        for i, user in enumerate(users[:10], 1):
            response += f"{i}. ID {user['user_id']}: {user['filter_count']} фильтров\n"
    
    await message.answer(response, parse_mode='HTML', reply_markup=get_admin_keyboard())

@dp.message_handler(lambda message: message.text == "👥 Пользователи")
async def cmd_admin_users(message: types.Message):
    """Список пользователей для админа"""
    if not is_admin(message.from_user.id):
        await message.answer("❌ <b>Доступ запрещен!</b>", parse_mode='HTML')
        return
    
    users = get_all_users()
    
    if not users:
        await message.answer("📭 <b>Нет пользователей в базе</b>", parse_mode='HTML')
        return
    
    response = "👥 <b>СПИСОК ПОЛЬЗОВАТЕЛЕЙ</b>\n\n"
    for i, user in enumerate(users, 1):
        response += f"{i}. <b>ID {user['user_id']}</b>: {user['filter_count']} фильтров\n"
    
    await message.answer(response, parse_mode='HTML', reply_markup=get_admin_keyboard())

@dp.message_handler(lambda message: message.text == "🗑️ Очистить базу")
async def cmd_clear_database(message: types.Message):
    """Очистка всей базы данных"""
    if not is_admin(message.from_user.id):
        await message.answer("❌ <b>Доступ запрещен!</b>", parse_mode='HTML')
        return
    
    stats = get_all_users_stats()
    
    await message.answer(
        f"🚨 <b>ПОДТВЕРЖДЕНИЕ ОЧИСТКИ БАЗЫ</b>\n\n"
        f"📊 <b>Текущая статистика:</b>\n"
        f"• Пользователей: {stats['total_users']}\n"
        f"• Фильтров: {stats['total_filters']}\n\n"
        f"⚠️ <b>ВНИМАНИЕ!</b>\n"
        f"Это действие удалит ВСЕ данные из базы!\n"
        f"<i>Восстановление будет невозможно</i>\n\n"
        f"❓ <b>Вы уверены что хотите очистить всю базу?</b>",
        parse_mode='HTML',
        reply_markup=get_confirmation_keyboard(action="clear_db")
    )

@dp.callback_query_handler(lambda c: c.data == 'confirm_clear_db')
async def process_confirm_clear_db(callback_query: types.CallbackQuery):
    """Подтверждение очистки базы"""
    if not is_admin(callback_query.from_user.id):
        await callback_query.answer("❌ Доступ запрещен!")
        return
    
    deleted_count = clear_all_filters()
    
    await callback_query.message.edit_text(
        f"✅ <b>БАЗА ДАННЫХ ОЧИЩЕНА!</b>\n\n"
        f"🗑️ <b>Удалено записей:</b> {deleted_count}\n\n"
        f"💫 <i>Все данные успешно удалены</i>",
        parse_mode='HTML'
    )
    
    await bot.send_message(
        ADMIN_ID,
        "🏠 Возврат в админ панель",
        reply_markup=get_admin_keyboard()
    )

@dp.callback_query_handler(lambda c: c.data == 'cancel_clear_db')
async def process_cancel_clear_db(callback_query: types.CallbackQuery):
    """Отмена очистки базы"""
    await callback_query.message.edit_text(
        "🚫 <b>ОЧИСТКА БАЗЫ ОТМЕНЕНА</b>\n\n"
        "💡 <i>Данные не были удалены</i>",
        parse_mode='HTML'
    )
    
    await bot.send_message(
        callback_query.from_user.id,
        "🏠 Возврат в админ панель",
        reply_markup=get_admin_keyboard()
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
        "• 🔔 Автоматические напоминания",
        parse_mode='HTML',
        reply_markup=get_main_keyboard(message.from_user.id)
    )

# Обработка кнопки "Главное меню"
@dp.message_handler(lambda message: message.text == "🔙 Главное меню")
async def cmd_back(message: types.Message):
    await message.answer(
        "🏠 <b>Главное меню</b>\n\n"
        "Выберите нужный раздел:",
        parse_mode='HTML',
        reply_markup=get_main_keyboard(message.from_user.id)
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
            reply_markup=get_main_keyboard(message.from_user.id)
        )
        return
        
    await message.answer(
        "🔧 <b>Выберите тип добавления:</b>\n\n"
        "💡 <i>Можно добавить один фильтр или сразу несколько</i>",
        parse_mode='HTML',
        reply_markup=get_add_filter_keyboard()
    )

# Обработка выбора типа добавления
@dp.message_handler(lambda message: message.text in ["🔧 Один фильтр", "📦 Несколько фильтров"])
async def process_add_type(message: types.Message, state: FSMContext):
    if message.text == "🔧 Один фильтр":
        await FilterStates.waiting_filter_type.set()
        await message.answer(
            "🔧 <b>Выберите тип фильтра:</b>\n\n"
            "💡 <i>Доступны все варианты фильтров</i>",
            parse_mode='HTML',
            reply_markup=get_filter_type_keyboard()
        )

# Обработка выбора типа фильтра для ОДНОГО фильтра
@dp.message_handler(state=FilterStates.waiting_filter_type)
async def process_filter_type(message: types.Message, state: FSMContext):
    if message.text == "❌ Отмена":
        await state.finish()
        await message.answer("🚫 Добавление фильтра отменено", reply_markup=get_main_keyboard(message.from_user.id))
        return
        
    if message.text == "📝 Другой тип":
        await message.answer(
            "📝 <b>Введите тип фильтра:</b>\n"
            "<i>Например: Угольный фильтр, Механический фильтр и т.д.</i>",
            parse_mode='HTML',
            reply_markup=get_cancel_keyboard()
        )
        return
    
    # Обработка всех типов фильтров из списка
    filter_mapping = {
        "🔧 Магистральный SL10": "Магистральный SL10",
        "🔧 Магистральный SL20": "Магистральный SL20",
        "💧 Гейзер": "Гейзер",
        "💧 Аквафор": "Аквафор"
    }
    
    filter_name = None
    
    if message.text in filter_mapping:
        filter_name = filter_mapping[message.text]
    else:
        # Пользователь ввел свой вариант
        try:
            filter_name = validate_filter_name(message.text)
        except ValueError as e:
            await message.answer(
                f"❌ <b>Ошибка в названии фильтра:</b>\n\n"
                f"💡 <i>{str(e)}</i>",
                parse_mode='HTML',
                reply_markup=get_filter_type_keyboard()
            )
            return
    
    if filter_name:
        async with state.proxy() as data:
            data['filter_type'] = filter_name
            data['lifetime'] = get_lifetime_by_type(filter_name)

        await FilterStates.next()
        await message.answer(
            "📍 <b>Укажите место установки фильтра:</b>\n\n"
            "💡 <i>Нажмите кнопку ниже чтобы указать место</i>",
            parse_mode='HTML',
            reply_markup=get_location_keyboard()
        )

# ========== ОБРАБОТЧИКИ МЕСТА УСТАНОВКИ И ДАТЫ ==========

@dp.message_handler(state=[FilterStates.waiting_location, MultipleFiltersStates.waiting_location])
async def process_location(message: types.Message, state: FSMContext):
    if message.text == "❌ Отмена":
        await state.finish()
        await message.answer("🚫 Добавление отменено", reply_markup=get_main_keyboard(message.from_user.id))
        return
        
    if message.text == "📍 Указать место установки":
        await message.answer(
            "📍 <b>Введите место установки фильтра:</b>\n\n"
            "💡 <i>Например: Кухня, Ванная комната, Под раковиной, Гостиная, Офис, Балкон, Гараж и т.д.</i>",
            parse_mode='HTML',
            reply_markup=get_cancel_keyboard()
        )
        return
    
    current_state = await state.get_state()
    
    if current_state == FilterStates.waiting_location.state:
        # Для одного фильтра
        async with state.proxy() as data:
            data['location'] = safe_db_string(message.text)

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
            data['location'] = safe_db_string(message.text)

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

@dp.message_handler(state=FilterStates.waiting_change_date)
async def process_change_date(message: types.Message, state: FSMContext):
    if message.text == "❌ Отмена":
        await state.finish()
        await message.answer("🚫 Добавление фильтра отменено", reply_markup=get_main_keyboard(message.from_user.id))
        return
    
    try:
        change_date = validate_date(message.text)
        
        async with state.proxy() as data:
            data['change_date'] = change_date
            filter_type = data['filter_type']
            lifetime = data.get('lifetime', get_lifetime_by_type(filter_type))
        
        expiry_date = change_date + timedelta(days=lifetime)
        expiry_date_nice = format_date_nice(expiry_date)
        
        await FilterStates.next()
        
        await message.answer(
            f"⏱️ <b>Срок службы фильтра</b>\n\n"
            f"🔧 <i>Фильтр:</i> {filter_type}\n"
            f"📍 <i>Место:</i> {data['location']}\n"
            f"📅 <i>Дата замены:</i> {format_date_nice(change_date)}\n"
            f"🗓️ <i>Годен до:</i> {expiry_date_nice}\n\n"
            f"📝 <b>Выберите или введите срок службы в днях:</b>",
            parse_mode='HTML',
            reply_markup=get_lifetime_keyboard()
        )
        
    except ValueError as e:
        today_nice = format_date_nice(datetime.now().date())
        await message.answer(
            f"❌ <b>Ошибка в дате!</b>\n\n"
            f"💡 <i>{str(e)}</i>\n\n"
            f"📝 <b>Введите дату замены в формате ДД.ММ.ГГ:</b>\n"
            f"<i>Например: {today_nice}</i>",
            parse_mode='HTML',
            reply_markup=get_cancel_keyboard()
        )

@dp.message_handler(state=FilterStates.waiting_lifetime)
async def process_lifetime(message: types.Message, state: FSMContext):
    if message.text == "❌ Отмена":
        await state.finish()
        await message.answer("🚫 Добавление фильтра отменено", reply_markup=get_main_keyboard(message.from_user.id))
        return
    
    lifetime_mapping = {
        "3️⃣ 90 дней": 90,
        "6️⃣ 180 дней": 180,
        "1️⃣ 365 дней": 365
    }
    
    try:
        if message.text in lifetime_mapping:
            lifetime = lifetime_mapping[message.text]
        elif message.text == "📅 Другое количество":
            await message.answer(
                "📅 <b>Введите срок службы в днях:</b>\n\n"
                "💡 <i>Например: 30, 60, 90, 180, 365 и т.д.</i>",
                parse_mode='HTML',
                reply_markup=get_cancel_keyboard()
            )
            return
        else:
            lifetime = validate_lifetime(message.text)
        
        async with state.proxy() as data:
            change_date = data['change_date']
            filter_type = data['filter_type']
            location = data['location']
            
            expiry_date = change_date + timedelta(days=lifetime)
            
            # Сохраняем в БД
            with get_db_connection() as conn:
                cur = conn.cursor()
                cur.execute('''INSERT INTO filters 
                            (user_id, filter_type, location, last_change, expiry_date, lifetime_days) 
                            VALUES (?, ?, ?, ?, ?, ?)''',
                            (message.from_user.id, filter_type, location, change_date, expiry_date, lifetime))
                conn.commit()
        
        await message.answer(
            f"🎉 <b>ФИЛЬТР УСПЕШНО ДОБАВЛЕН!</b>\n\n"
            f"🔧 <b>Тип:</b> {filter_type}\n"
            f"📍 <b>Место:</b> {location}\n"
            f"📅 <b>Дата замены:</b> {format_date_nice(change_date)}\n"
            f"⏱️ <b>Срок службы:</b> {lifetime} дней\n"
            f"🗓️ <b>Годен до:</b> {format_date_nice(expiry_date)}\n\n"
            f"💫 <i>Теперь вы можете отслеживать срок его замены</i>",
            parse_mode='HTML',
            reply_markup=get_main_keyboard(message.from_user.id)
        )
        
        await state.finish()
        
    except ValueError as e:
        await message.answer(
            f"❌ <b>Ошибка в сроке службы!</b>\n\n"
            f"💡 <i>{str(e)}</i>\n\n"
            f"📝 <b>Введите корректное число дней:</b>",
            parse_mode='HTML',
            reply_markup=get_lifetime_keyboard()
        )

# Обработка отмены
@dp.message_handler(lambda message: message.text == "❌ Отмена", state='*')
async def cmd_cancel(message: types.Message, state: FSMContext):
    current_state = await state.get_state()
    if current_state is None:
        return
    
    await state.finish()
    await message.answer("🚫 Действие отменено", reply_markup=get_main_keyboard(message.from_user.id))

# Обработка других сообщений
@dp.message_handler()
async def handle_other_messages(message: types.Message):
    await message.answer(
        "🌟 <b>Фильтр-Трекер</b> 🤖\n\n"
        "💧 <i>Выберите действие с помощью кнопок ниже:</i>",
        parse_mode='HTML',
        reply_markup=get_main_keyboard(message.from_user.id)
    )

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
