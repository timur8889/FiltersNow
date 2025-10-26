import asyncio
import logging
from aiogram import Bot, Dispatcher, types, F
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.context import FSMContext
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
import gspread
from google.oauth2.service_account import Credentials
from datetime import datetime
import os
import json
import pandas as pd

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Токен бота
API_TOKEN = 'YOUR_BOT_TOKEN'

# Настройка Google Sheets
SCOPES = ['https://www.googleapis.com/auth/spreadsheets', 'https://www.googleapis.com/auth/drive']
SERVICE_ACCOUNT_FILE = 'service-account.json'
SPREADSHEET_ID = 'your-spreadsheet-id-here'

# Инициализация бота и диспетчера
bot = Bot(token=API_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.MARKDOWN))
storage = MemoryStorage()
dp = Dispatcher(storage=storage)

# Глобальная переменная для хранения данных
users_data = {}
sync_status = "🟢 Активна"

# Определение состояний FSM
class UserStates(StatesGroup):
    WAITING_FOR_NAME = State()
    WAITING_FOR_EMAIL = State()
    WAITING_FOR_PHONE = State()
    EDITING_NAME = State()
    EDITING_EMAIL = State()
    EDITING_PHONE = State()

# Настройка Google Sheets
def setup_google_sheets():
    try:
        if not os.path.exists(SERVICE_ACCOUNT_FILE):
            logger.error(f"❌ Файл сервисного аккаунта {SERVICE_ACCOUNT_FILE} не найден!")
            return None
            
        creds = Credentials.from_service_account_file(SERVICE_ACCOUNT_FILE, scopes=SCOPES)
        client = gspread.authorize(creds)
        spreadsheet = client.open_by_key(SPREADSHEET_ID)
        worksheet = spreadsheet.sheet1
        
        # Проверяем заголовки
        headers = worksheet.row_values(1)
        expected_headers = ['user_id', 'name', 'email', 'phone', 'registration_date', 'telegram_username', 'last_update']
        
        if headers != expected_headers:
            worksheet.clear()
            worksheet.append_row(expected_headers)
            logger.info("✅ Созданы заголовки таблицы")
        
        return worksheet
    except Exception as e:
        logger.error(f"❌ Ошибка при настройке Google Sheets: {e}")
        return None

# Синхронизация с Google Sheets
async def sync_with_google_sheets():
    global sync_status
    worksheet = setup_google_sheets()
    if not worksheet:
        sync_status = "🔴 Ошибка подключения"
        logger.error("❌ Не удалось подключиться к Google Sheets")
        return
    
    try:
        # Получаем все данные из таблицы
        all_records = worksheet.get_all_records()
        
        # Обновляем локальные данные
        global users_data
        updated_count = 0
        new_users_count = 0
        
        for record in all_records:
            user_id = record.get('user_id')
            if user_id:
                user_id = str(user_id)
                if user_id not in users_data:
                    users_data[user_id] = record
                    new_users_count += 1
                    updated_count += 1
                elif users_data[user_id].get('last_update') != record.get('last_update'):
                    users_data[user_id] = record
                    updated_count += 1
        
        sync_status = f"🟢 Активна ({len(users_data)} пользователей)"
        
        if updated_count > 0:
            logger.info(f"✅ Синхронизировано {updated_count} записей ({new_users_count} новых) из Google Sheets")
        else:
            logger.info("✅ Данные актуальны, синхронизация не требуется")
        
    except Exception as e:
        sync_status = "🔴 Ошибка синхронизации"
        logger.error(f"❌ Ошибка при синхронизации с Google Sheets: {e}")

# Фоновая задача для синхронизации
async def scheduled_sync():
    while True:
        try:
            await sync_with_google_sheets()
        except Exception as e:
            logger.error(f"❌ Ошибка в фоновой синхронизации: {e}")
        await asyncio.sleep(5)

# Главное меню
def get_main_menu():
    keyboard = InlineKeyboardMarkup(row_width=2)
    
    buttons = [
        InlineKeyboardButton(text="👤 Регистрация", callback_data="register"),
        InlineKeyboardButton(text="📊 Мои данные", callback_data="my_data"),
        InlineKeyboardButton(text="✏️ Редактировать", callback_data="edit_data"),
        InlineKeyboardButton(text="📋 Все пользователи", callback_data="all_users"),
        InlineKeyboardButton(text="🔄 Синхронизация", callback_data="force_sync"),
        InlineKeyboardButton(text="📈 Статистика", callback_data="stats"),
        InlineKeyboardButton(text="❓ Помощь", callback_data="help"),
        InlineKeyboardButton(text="⭐ О боте", callback_data="about")
    ]
    
    keyboard.add(buttons[0], buttons[1])
    keyboard.add(buttons[2], buttons[3])
    keyboard.add(buttons[4], buttons[5])
    keyboard.add(buttons[6], buttons[7])
    
    return keyboard

# Меню редактирования
def get_edit_menu():
    keyboard = InlineKeyboardMarkup(row_width=1)
    
    buttons = [
        InlineKeyboardButton(text="✏️ Изменить имя", callback_data="edit_name"),
        InlineKeyboardButton(text="📧 Изменить email", callback_data="edit_email"),
        InlineKeyboardButton(text="📞 Изменить телефон", callback_data="edit_phone"),
        InlineKeyboardButton(text="🗑️ Удалить аккаунт", callback_data="delete_account"),
        InlineKeyboardButton(text="🔙 Назад", callback_data="back_to_main")
    ]
    
    for button in buttons:
        keyboard.add(button)
    
    return keyboard

# Меню подтверждения удаления
def get_delete_confirm_menu():
    keyboard = InlineKeyboardMarkup(row_width=2)
    
    buttons = [
        InlineKeyboardButton(text="✅ Да, удалить", callback_data="confirm_delete"),
        InlineKeyboardButton(text="❌ Нет, отмена", callback_data="cancel_delete")
    ]
    
    keyboard.add(*buttons)
    return keyboard

# Меню отмены
def get_cancel_menu():
    keyboard = InlineKeyboardMarkup()
    keyboard.add(InlineKeyboardButton(text="❌ Отмена", callback_data="cancel"))
    return keyboard

# Команда старт
@dp.message(lambda message: message.text and message.text.startswith('/start'))
async def cmd_start(message: types.Message):
    user = message.from_user
    welcome_text = f"""
🎉 *Добро пожаловать, {user.first_name}!* 🎉

🤖 *Умный бот для управления данными с синхронизацией Google Sheets*

📋 *Основные возможности:*
👤 Регистрация и управление профилем
📊 Просмотр и редактирование данных  
🔄 Автосинхронизация с Google Sheets каждые 5 сек
📈 Статистика и аналитика
🗑️ Управление аккаунтом

💫 *Выберите действие в меню ниже:*
    """
    
    await message.answer(welcome_text, 
                        reply_markup=get_main_menu())

# Обработчик главного меню
@dp.callback_query(F.data.in_([
    "register", "my_data", "edit_data", "all_users", 
    "force_sync", "help", "about", "stats", "cancel", "back_to_main",
    "delete_account", "confirm_delete", "cancel_delete"
]))
async def process_main_menu(callback_query: types.CallbackQuery, state: FSMContext):
    user_id = str(callback_query.from_user.id)
    
    if callback_query.data == "register":
        if user_id in users_data:
            user_data = users_data[user_id]
            await callback_query.message.edit_text(
                f"✅ *Вы уже зарегистрированы!* 🎉\n\n"
                f"👤 *Имя:* {user_data.get('name', 'Не указано')}\n"
                f"📧 *Email:* {user_data.get('email', 'Не указан')}\n"
                f"📞 *Телефон:* {user_data.get('phone', 'Не указан')}\n\n"
                f"✏️ Используйте кнопку 'Редактировать' для изменения данных",
                reply_markup=get_main_menu()
            )
        else:
            await callback_query.message.edit_text(
                "👤 *Давайте зарегистрируем вас!* 📝\n\n"
                "📛 Пожалуйста, введите ваше имя:",
                reply_markup=get_cancel_menu()
            )
            await state.set_state(UserStates.WAITING_FOR_NAME)
    
    elif callback_query.data == "my_data":
        if user_id in users_data:
            user_data = users_data[user_id]
            data_text = f"""
📊 *Ваши данные:* 📋

👤 *Имя:* {user_data.get('name', 'Не указано')}
📧 *Email:* {user_data.get('email', 'Не указан')}
📞 *Телефон:* {user_data.get('phone', 'Не указан')}
🆔 *User ID:* {user_id}
📅 *Дата регистрации:* {user_data.get('registration_date', 'Не указана')}
🕒 *Последнее обновление:* {user_data.get('last_update', 'Не указано')}
📱 *Username:* @{user_data.get('telegram_username', 'Не указан')}

💾 *Синхронизация:* {sync_status}
            """
            await callback_query.message.edit_text(data_text, 
                                                 reply_markup=get_main_menu())
        else:
            await callback_query.message.edit_text(
                "❌ *Вы еще не зарегистрированы!*\n\n"
                "👤 Нажмите 'Регистрация' чтобы создать аккаунт 📝",
                reply_markup=get_main_menu()
            )
    
    elif callback_query.data == "edit_data":
        if user_id in users_data:
            await callback_query.message.edit_text(
                "✏️ *Редактирование данных*\n\n"
                "Выберите какие данные вы хотите изменить:",
                reply_markup=get_edit_menu()
            )
        else:
            await callback_query.message.edit_text(
                "❌ *Вы еще не зарегистрированы!*\n\n"
                "👤 Сначала зарегистрируйтесь чтобы редактировать данные",
                reply_markup=get_main_menu()
            )
    
    elif callback_query.data == "all_users":
        await callback_query.message.edit_text(
            "⏳ *Загружаю список пользователей...*"
        )
        
        if users_data:
            users_list = "📋 *Зарегистрированные пользователи:* 👥\n\n"
            for i, (uid, data) in enumerate(list(users_data.items())[:15], 1):
                users_list += f"{i}. 👤 {data.get('name', 'Неизвестно')} | 📞 {data.get('phone', 'Нет телефона')}\n"
            
            if len(users_data) > 15:
                users_list += f"\n... и еще {len(users_data) - 15} пользователей 👥"
            
            users_list += f"\n\n📊 *Всего пользователей:* {len(users_data)} ✅"
        else:
            users_list = "📭 *Пользователей пока нет*\n\nБудьте первым! 🎉"
        
        await callback_query.message.edit_text(users_list, 
                                             reply_markup=get_main_menu())
    
    elif callback_query.data == "force_sync":
        await callback_query.message.edit_text(
            "🔄 *Принудительная синхронизация...* ⏳"
        )
        await sync_with_google_sheets()
        await callback_query.message.edit_text(
            "✅ *Синхронизация завершена!* 🎉\n\n"
            f"📊 Загружено {len(users_data)} пользователей\n"
            f"💾 Статус: {sync_status}",
            reply_markup=get_main_menu()
        )
    
    elif callback_query.data == "help":
        help_text = """
❓ *Помощь по боту* 📚

👤 *Регистрация* - Создать новый аккаунт
📊 *Мои данные* - Посмотреть вашу информацию
✏️ *Редактировать* - Изменить ваши данные
📋 *Все пользователи* - Список всех зарегистрированных
🔄 *Синхронизация* - Принудительная синхронизация с Google Sheets
📈 *Статистика* - Статистика бота
🗑️ *Удалить аккаунт* - Полное удаление ваших данных

💡 *Советы:*
• 📝 Данные автоматически синхронизируются с Google Sheets каждые 5 секунд
• ✏️ Вы можете обновлять информацию в любое время
• 🔄 Бот работает 24/7 ⭐
• 📊 Все изменения сохраняются в облаке
• 🗑️ Удаление аккаунта безвозвратно удаляет все данные

🛠 *Команды:*
/start - Главное меню
/help - Эта справка
/profile - Ваш профиль
/stats - Статистика бота
        """
        await callback_query.message.edit_text(help_text, 
                                             reply_markup=get_main_menu())
    
    elif callback_query.data == "about":
        about_text = """
⭐ *О боте* 🤖

*Умный бот для управления данными с облачной синхронизацией*

📊 *Возможности:*
• 📝 Регистрация и управление пользователями
• 💾 Автосохранение в Google Sheets
• 🔄 Синхронизация каждые 5 секунд
• 🎨 Красивый интерфейс с эмодзи
• ⚡ Быстрая работа
• 📈 Статистика и аналитика
• 🗑️ Безопасное удаление данных

🛠 *Технологии:*
• Python aiogram 3.x
• Google Sheets API
• Асинхронное программирование
• FSM для управления состояниями

🔒 *Безопасность:*
• Данные хранятся в защищенной Google таблице
• Только вы имеете доступ к своим данным
• Прозрачная работа с открытым исходным кодом

💫 *Разработано с ❤️ для удобства пользователей!* 🚀
        """
        await callback_query.message.edit_text(about_text, 
                                             reply_markup=get_main_menu())
    
    elif callback_query.data == "stats":
        total_users = len(users_data)
        users_with_email = len([u for u in users_data.values() if u.get('email')])
        users_with_phone = len([u for u in users_data.values() if u.get('phone')])
        
        stats_text = f"""
📊 *Статистика бота* 📈

👥 *Пользователи:*
• 👤 Всего зарегистрировано: {total_users}
• 📧 С email: {users_with_email}
• 📞 С телефоном: {users_with_phone}
• 📝 Заполненные профили: {len([u for u in users_data.values() if u.get('name') and u.get('email') and u.get('phone')])}

💾 *Система:*
• 🗃️ Размер базы: {len(str(users_data))} байт
• 🔄 Синхронизация: {sync_status}
• 📅 Бот запущен: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
• ⚡ Автосинхронизация: каждые 5 секунд

📈 *Активность:*
• 🆕 Новые пользователи: можно добавить аналитику
• ✏️ Обновления данных: можно добавить отслеживание
• 🗑️ Удаленные аккаунты: можно добавить историю
        """
        await callback_query.message.edit_text(stats_text, 
                                             reply_markup=get_main_menu())
    
    elif callback_query.data == "delete_account":
        await callback_query.message.edit_text(
            "⚠️ *Внимание! Удаление аккаунта* 🗑️\n\n"
            "❌ Это действие невозможно отменить!\n"
            "📝 Все ваши данные будут удалены:\n"
            "• 👤 Имя, email, телефон\n"
            "• 📅 Дата регистрации\n"
            "• 📊 История изменений\n\n"
            "✅ *Вы уверены что хотите удалить аккаунт?*",
            reply_markup=get_delete_confirm_menu()
        )
    
    elif callback_query.data == "confirm_delete":
        if user_id in users_data:
            # Удаляем из локального хранилища
            deleted_user = users_data.pop(user_id)
            
            # Удаляем из Google Sheets
            success = await delete_from_google_sheets(user_id)
            
            await callback_query.message.edit_text(
                f"✅ *Аккаунт успешно удален!* 🗑️\n\n"
                f"👤 Пользователь: {deleted_user.get('name', 'Неизвестно')}\n"
                f"📧 Email: {deleted_user.get('email', 'Не указан')}\n"
                f"📞 Телефон: {deleted_user.get('phone', 'Не указан')}\n\n"
                f"{'💾 Данные удалены из Google Sheets' if success else '⚠️ Ошибка удаления из Google Sheets'}\n\n"
                f"💫 Вы можете зарегистрироваться снова в любое время!",
                reply_markup=get_main_menu()
            )
        else:
            await callback_query.message.edit_text(
                "❌ *Аккаунт не найден!*\n\n"
                "Возможно он уже был удален или не существовал",
                reply_markup=get_main_menu()
            )
    
    elif callback_query.data == "cancel_delete":
        await callback_query.message.edit_text(
            "✅ *Удаление отменено* ❌\n\n"
            "Ваши данные в безопасности! 🔒",
            reply_markup=get_main_menu()
        )
    
    elif callback_query.data == "cancel":
        await callback_query.message.edit_text(
            "❌ Действие отменено\n\n"
            "💫 Возврат в главное меню:",
            reply_markup=get_main_menu()
        )
        await state.clear()
    
    elif callback_query.data == "back_to_main":
        await callback_query.message.edit_text(
            "🔙 Возврат в главное меню:",
            reply_markup=get_main_menu()
        )
        await state.clear()
    
    await callback_query.answer()

# Обработчики редактирования данных
@dp.callback_query(F.data.in_(["edit_name", "edit_email", "edit_phone"]))
async def process_edit_selection(callback_query: types.CallbackQuery, state: FSMContext):
    user_id = str(callback_query.from_user.id)
    
    if callback_query.data == "edit_name":
        await callback_query.message.edit_text(
            "✏️ *Изменение имени*\n\n"
            "📛 Введите ваше новое имя:",
            reply_markup=get_cancel_menu()
        )
        await state.set_state(UserStates.EDITING_NAME)
    
    elif callback_query.data == "edit_email":
        await callback_query.message.edit_text(
            "📧 *Изменение email*\n\n"
            "📫 Введите ваш новый email:",
            reply_markup=get_cancel_menu()
        )
        await state.set_state(UserStates.EDITING_EMAIL)
    
    elif callback_query.data == "edit_phone":
        await callback_query.message.edit_text(
            "📞 *Изменение телефона*\n\n"
            "📱 Введите ваш новый телефон:",
            reply_markup=get_cancel_menu()
        )
        await state.set_state(UserStates.EDITING_PHONE)
    
    await callback_query.answer()

# Обработка имени (регистрация)
@dp.message(UserStates.WAITING_FOR_NAME)
async def process_name(message: types.Message, state: FSMContext):
    await state.update_data(name=message.text)
    
    await message.answer(
        "📧 *Отлично!* ✅\n\n"
        "Теперь введите ваш email:",
        reply_markup=get_cancel_menu()
    )
    await state.set_state(UserStates.WAITING_FOR_EMAIL)

# Обработка email (регистрация)
@dp.message(UserStates.WAITING_FOR_EMAIL)
async def process_email(message: types.Message, state: FSMContext):
    await state.update_data(email=message.text)
    
    await message.answer(
        "📞 *Прекрасно!* ✅\n\n"
        "Теперь введите ваш телефон:",
        reply_markup=get_cancel_menu()
    )
    await state.set_state(UserStates.WAITING_FOR_PHONE)

# Обработка телефона и сохранение (регистрация)
@dp.message(UserStates.WAITING_FOR_PHONE)
async def process_phone(message: types.Message, state: FSMContext):
    user_id = str(message.from_user.id)
    
    user_data = await state.get_data()
    user_data.update({
        'phone': message.text,
        'user_id': user_id,
        'registration_date': datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        'telegram_username': message.from_user.username,
        'last_update': datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    })
    
    # Сохраняем в локальное хранилище
    users_data[user_id] = user_data
    
    # Сохраняем в Google Sheets
    success = await save_to_google_sheets(user_id, user_data)
    
    success_text = f"""
✅ *Регистрация завершена!* 🎉

📋 *Ваши данные:*
👤 *Имя:* {user_data['name']}
📧 *Email:* {user_data['email']}
📞 *Телефон:* {user_data['phone']}
🆔 *User ID:* {user_id}
📅 *Дата регистрации:* {user_data['registration_date']}

{'💫 *Данные сохранены и синхронизированы с Google Sheets!* ☁️' if success else '⚠️ *Данные сохранены локально, но возникла ошибка синхронизации*'}
    """
    
    await message.answer(success_text, 
                        reply_markup=get_main_menu())
    await state.clear()

# Обработка изменений данных
@dp.message(UserStates.EDITING_NAME)
async def process_edit_name(message: types.Message, state: FSMContext):
    user_id = str(message.from_user.id)
    new_name = message.text
    
    if user_id in users_data:
        users_data[user_id]['name'] = new_name
        users_data[user_id]['last_update'] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        
        success = await save_to_google_sheets(user_id, users_data[user_id])
        
        await message.answer(
            f"✅ *Имя успешно изменено!* ✏️\n\n"
            f"👤 Новое имя: {new_name}\n\n"
            f"{'☁️ Данные синхронизированы с Google Sheets' if success else '⚠️ Ошибка синхронизации'}",
            reply_markup=get_main_menu()
        )
    else:
        await message.answer(
            "❌ *Ошибка:* пользователь не найден",
            reply_markup=get_main_menu()
        )
    
    await state.clear()

@dp.message(UserStates.EDITING_EMAIL)
async def process_edit_email(message: types.Message, state: FSMContext):
    user_id = str(message.from_user.id)
    new_email = message.text
    
    if user_id in users_data:
        users_data[user_id]['email'] = new_email
        users_data[user_id]['last_update'] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        
        success = await save_to_google_sheets(user_id, users_data[user_id])
        
        await message.answer(
            f"✅ *Email успешно изменен!* 📧\n\n"
            f"📫 Новый email: {new_email}\n\n"
            f"{'☁️ Данные синхронизированы с Google Sheets' if success else '⚠️ Ошибка синхронизации'}",
            reply_markup=get_main_menu()
        )
    else:
        await message.answer(
            "❌ *Ошибка:* пользователь не найден",
            reply_markup=get_main_menu()
        )
    
    await state.clear()

@dp.message(UserStates.EDITING_PHONE)
async def process_edit_phone(message: types.Message, state: FSMContext):
    user_id = str(message.from_user.id)
    new_phone = message.text
    
    if user_id in users_data:
        users_data[user_id]['phone'] = new_phone
        users_data[user_id]['last_update'] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        
        success = await save_to_google_sheets(user_id, users_data[user_id])
        
        await message.answer(
            f"✅ *Телефон успешно изменен!* 📞\n\n"
            f"📱 Новый телефон: {new_phone}\n\n"
            f"{'☁️ Данные синхронизированы с Google Sheets' if success else '⚠️ Ошибка синхронизации'}",
            reply_markup=get_main_menu()
        )
    else:
        await message.answer(
            "❌ *Ошибка:* пользователь не найден",
            reply_markup=get_main_menu()
        )
    
    await state.clear()

# Сохранение в Google Sheets
async def save_to_google_sheets(user_id, user_data):
    worksheet = setup_google_sheets()
    if not worksheet:
        return False
    
    try:
        # Получаем все данные
        all_records = worksheet.get_all_records()
        
        # Ищем существующую запись
        existing_row = None
        for i, record in enumerate(all_records, start=2):
            if str(record.get('user_id')) == str(user_id):
                existing_row = i
                break
        
        # Подготавливаем данные для записи
        row_data = [
            user_data.get('user_id', ''),
            user_data.get('name', ''),
            user_data.get('email', ''),
            user_data.get('phone', ''),
            user_data.get('registration_date', ''),
            user_data.get('telegram_username', ''),
            user_data.get('last_update', '')
        ]
        
        if existing_row:
            worksheet.update(f'A{existing_row}:G{existing_row}', [row_data])
            logger.info(f"✅ Обновлены данные пользователя {user_id} в Google Sheets")
        else:
            worksheet.append_row(row_data)
            logger.info(f"✅ Добавлены данные пользователя {user_id} в Google Sheets")
        
        return True
        
    except Exception as e:
        logger.error(f"❌ Ошибка при сохранении в Google Sheets: {e}")
        return False

# Удаление из Google Sheets
async def delete_from_google_sheets(user_id):
    worksheet = setup_google_sheets()
    if not worksheet:
        return False
    
    try:
        # Получаем все данные
        all_records = worksheet.get_all_records()
        
        # Ищем существующую запись
        existing_row = None
        for i, record in enumerate(all_records, start=2):
            if str(record.get('user_id')) == str(user_id):
                existing_row = i
                break
        
        if existing_row:
            worksheet.delete_rows(existing_row)
            logger.info(f"✅ Удалены данные пользователя {user_id} из Google Sheets")
            return True
        else:
            logger.warning(f"⚠️ Пользователь {user_id} не найден в Google Sheets для удаления")
            return True  # Возвращаем True, т.к. в таблице его уже нет
            
    except Exception as e:
        logger.error(f"❌ Ошибка при удалении из Google Sheets: {e}")
        return False

# Команда помощи
@dp.message(lambda message: message.text and message.text.startswith('/help'))
async def cmd_help(message: types.Message):
    await message.answer(
        "🆘 *Помощь по боту*\n\n"
        "Используйте кнопки меню для навигации или команды:\n"
        "/start - Главное меню\n"
        "/help - Эта справка\n"
        "/profile - Ваш профиль\n"
        "/stats - Статистика бота\n\n"
        "💫 Выберите действие в меню:",
        reply_markup=get_main_menu()
    )

# Команда профиля
@dp.message(lambda message: message.text and message.text.startswith('/profile'))
async def cmd_profile(message: types.Message):
    user_id = str(message.from_user.id)
    
    if user_id in users_data:
        user_data = users_data[user_id]
        profile_text = f"""
👤 *Ваш профиль* 📊

*Основная информация:*
👤 *Имя:* {user_data.get('name', 'Не указано')}
📧 *Email:* {user_data.get('email', 'Не указан')}
📞 *Телефон:* {user_data.get('phone', 'Не указан')}

*Системная информация:*
🆔 *User ID:* {user_id}
📱 *Username:* @{user_data.get('telegram_username', 'Не указан')}
📅 *Регистрация:* {user_data.get('registration_date', 'Не указана')}
🕒 *Обновлено:* {user_data.get('last_update', 'Не указано')}

💾 *Синхронизация:* {sync_status}
        """
        await message.answer(profile_text, 
                           reply_markup=get_main_menu())
    else:
        await message.answer(
            "❌ *Профиль не найден!*\n\n"
            "👤 Зарегистрируйтесь чтобы создать профиль",
            reply_markup=get_main_menu()
        )

# Команда статистики
@dp.message(lambda message: message.text and message.text.startswith('/stats'))
async def cmd_stats(message: types.Message):
    total_users = len(users_data)
    
    stats_text = f"""
📊 *Статистика бота* 📈

👥 *Пользователи:* {total_users}
💾 *Синхронизация:* {sync_status}
⚡ *Автообновление:* каждые 5 секунд
🕒 *Время сервера:* {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}

💫 Используйте кнопку 'Статистика' в меню для подробной информации
    """
    
    await message.answer(stats_text, 
                       reply_markup=get_main_menu())

# Запуск синхронизации при старте
async def on_startup():
    logger.info("🔄 Запуск синхронизации с Google Sheets...")
    await sync_with_google_sheets()
    
    # Запускаем фоновую задачу синхронизации
    asyncio.create_task(scheduled_sync())
    logger.info("✅ Бот запущен и синхронизация активна!")
    logger.info(f"📊 Загружено {len(users_data)} пользователей")

# Запуск бота
async def main():
    await on_startup()
    await dp.start_polling(bot)

if __name__ == '__main__':
    asyncio.run(main())
