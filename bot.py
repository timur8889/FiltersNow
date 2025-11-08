import os
import logging
from telegram import Update, ReplyKeyboardMarkup, KeyboardButton
from telegram.ext import (Updater, CommandHandler, MessageHandler, Filters, 
                         ConversationHandler, CallbackContext)
from google.oauth2.service_account import Credentials
import gspread
from dotenv import load_dotenv

# Загружаем переменные окружения ДО их использования
load_dotenv()

# Настройка логирования
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Токен бота из переменных окружения
BOT_TOKEN = os.getenv('BOT_TOKEN')

# Проверяем, что токен загружен
if not BOT_TOKEN:
    logger.error("BOT_TOKEN не найден в переменных окружения!")
    exit(1)

# Настройки Google Sheets
SCOPES = ['https://www.googleapis.com/auth/spreadsheets']

# Состояния для ConversationHandler
SELECTING_ACTION, ADDING_OBJECT, ADDING_SALARY, ADDING_MATERIALS, ENTERING_ADDRESS, ENTERING_NAME, ENTERING_SALARY, ENTERING_MATERIAL_NAME, ENTERING_MATERIAL_COST = range(9)

# Инициализация Google Sheets
def init_google_sheets():
    try:
        creds = Credentials.from_service_account_file('credentials.json', scopes=SCOPES)
        client = gspread.authorize(creds)
        return client
    except Exception as e:
        logger.error(f"Ошибка при инициализации Google Sheets: {e}")
        return None

# Команда /start
def start(update: Update, context: CallbackContext):
    user = update.message.from_user
    update.message.reply_text(
        f"Добро пожаловать в систему учета ООО ИКС ГЕОСТРОЙ, {user.first_name}!\n\n"
        "Выберите действие:",
        reply_markup=main_keyboard()
    )
    return SELECTING_ACTION

# Главная клавиатура
def main_keyboard():
    keyboard = [
        [KeyboardButton("📋 Добавить объект")],
        [KeyboardButton("💰 Добавить зарплату")],
        [KeyboardButton("🏗️ Добавить материалы")],
        [KeyboardButton("📊 Отчет по объектам")]
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

# Добавление объекта - шаг 1: адрес
def add_object_start(update: Update, context: CallbackContext):
    update.message.reply_text("Введите адрес строительного объекта:")
    return ENTERING_ADDRESS

# Шаг 2: название объекта
def enter_address(update: Update, context: CallbackContext):
    context.user_data['address'] = update.message.text
    update.message.reply_text("Введите название объекта:")
    return ENTERING_NAME

# Сохранение объекта в Google Sheets
def enter_name(update: Update, context: CallbackContext):
    context.user_data['name'] = update.message.text
    
    try:
        client = init_google_sheets()
        if not client:
            update.message.reply_text("❌ Ошибка подключения к Google Sheets")
            return SELECTING_ACTION
        
        # Пытаемся открыть существующую таблицу или создать новую
        try:
            spreadsheet = client.open("Учет строительных объектов ООО ИКС ГЕОСТРОЙ")
            sheet = spreadsheet.worksheet("Объекты")
        except gspread.SpreadsheetNotFound:
            # Создаем новую таблицу если не существует
            spreadsheet = client.create("Учет строительных объектов ООО ИКС ГЕОСТРОЙ")
            sheet = spreadsheet.add_worksheet(title="Объекты", rows=100, cols=4)
            sheet.append_row(["Адрес", "Название", "Зарплата", "Материалы"])
            
            # Создаем лист для зарплат
            salary_sheet = spreadsheet.add_worksheet(title="Зарплаты", rows=100, cols=4)
            salary_sheet.append_row(["Адрес", "Название", "Сумма", "Дата"])
            
            # Создаем лист для материалов
            materials_sheet = spreadsheet.add_worksheet(title="Материалы", rows=100, cols=5)
            materials_sheet.append_row(["Адрес", "Название", "Материал", "Стоимость", "Дата"])
        
        # Добавляем новый объект
        sheet.append_row([
            context.user_data['address'],
            context.user_data['name'],
            '0',  # начальная сумма зарплат
            '0'   # начальная сумма материалов
        ])
        
        update.message.reply_text(
            f"✅ Объект добавлен!\n"
            f"Адрес: {context.user_data['address']}\n"
            f"Название: {context.user_data['name']}",
            reply_markup=main_keyboard()
        )
    except Exception as e:
        logger.error(f"Ошибка при добавлении объекта: {e}")
        update.message.reply_text("❌ Ошибка при добавлении объекта")
    
    return SELECTING_ACTION

# Добавление зарплаты - выбор объекта
def add_salary_start(update: Update, context: CallbackContext):
    try:
        client = init_google_sheets()
        if not client:
            update.message.reply_text("❌ Ошибка подключения к Google Sheets")
            return SELECTING_ACTION
            
        sheet = client.open("Учет строительных объектов ООО ИКС ГЕОСТРОЙ").worksheet("Объекты")
        objects_data = sheet.get_all_values()
        
        if len(objects_data) <= 1:  # Только заголовок
            update.message.reply_text("❌ Нет доступных объектов")
            return SELECTING_ACTION
        
        # Преобразуем в удобный формат
        objects = []
        for row in objects_data[1:]:  # Пропускаем заголовок
            if row[0]:  # Если адрес не пустой
                objects.append({
                    'Адрес': row[0],
                    'Название': row[1] if len(row) > 1 else '',
                    'Зарплата': row[2] if len(row) > 2 else '0',
                    'Материалы': row[3] if len(row) > 3 else '0'
                })
        
        keyboard = []
        for obj in objects:
            button_text = f"{obj['Адрес']} - {obj['Название']}"
            keyboard.append([KeyboardButton(button_text)])
        
        keyboard.append([KeyboardButton("🔙 Назад")])
        
        context.user_data['objects'] = objects
        update.message.reply_text(
            "Выберите объект для добавления зарплаты:",
            reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
        )
        return ENTERING_SALARY
        
    except Exception as e:
        logger.error(f"Ошибка при получении объектов: {e}")
        update.message.reply_text("❌ Ошибка при загрузке объектов")
        return SELECTING_ACTION

# Ввод суммы зарплаты
def enter_salary(update: Update, context: CallbackContext):
    if update.message.text == "🔙 Назад":
        update.message.reply_text("Главное меню:", reply_markup=main_keyboard())
        return SELECTING_ACTION
    
    selected_object = update.message.text
    context.user_data['selected_object'] = selected_object
    
    update.message.reply_text("Введите сумму зарплаты:")
    return ADDING_SALARY

# Сохранение зарплаты
def save_salary(update: Update, context: CallbackContext):
    try:
        salary_amount = float(update.message.text.replace(',', '.'))
        
        client = init_google_sheets()
        if not client:
            update.message.reply_text("❌ Ошибка подключения к Google Sheets")
            return SELECTING_ACTION
            
        sheet = client.open("Учет строительных объектов ООО ИКС ГЕОСТРОЙ").worksheet("Объекты")
        objects_data = sheet.get_all_values()
        
        # Находим выбранный объект
        selected_text = context.user_data['selected_object']
        for i, row in enumerate(objects_data[1:], start=2):  # Пропускаем заголовок
            if len(row) >= 2:
                object_text = f"{row[0]} - {row[1]}"
                if object_text == selected_text:
                    # Обновляем сумму зарплат
                    current_salary = float(row[2] or 0) if len(row) > 2 else 0
                    new_salary = current_salary + salary_amount
                    sheet.update_cell(i, 3, str(new_salary))
                    
                    # Записываем в историю зарплат
                    try:
                        history_sheet = client.open("Учет строительных объектов ООО ИКС ГЕОСТРОЙ").worksheet("Зарплаты")
                    except:
                        # Создаем лист если не существует
                        spreadsheet = client.open("Учет строительных объектов ООО ИКС ГЕОСТРОЙ")
                        history_sheet = spreadsheet.add_worksheet(title="Зарплаты", rows=100, cols=4)
                        history_sheet.append_row(["Адрес", "Название", "Сумма", "Дата"])
                    
                    history_sheet.append_row([
                        row[0], row[1], salary_amount, update.message.date.strftime("%Y-%m-%d %H:%M")
                    ])
                    
                    update.message.reply_text(
                        f"✅ Зарплата добавлена!\n"
                        f"Объект: {selected_text}\n"
                        f"Сумма: {salary_amount} руб.\n"
                        f"Общая сумма зарплат на объекте: {new_salary} руб.",
                        reply_markup=main_keyboard()
                    )
                    break
        
    except ValueError:
        update.message.reply_text("❌ Пожалуйста, введите корректную сумму:")
        return ADDING_SALARY
    except Exception as e:
        logger.error(f"Ошибка при добавлении зарплаты: {e}")
        update.message.reply_text("❌ Ошибка при добавлении зарплаты")
    
    return SELECTING_ACTION

# Добавление материалов - выбор объекта
def add_materials_start(update: Update, context: CallbackContext):
    try:
        client = init_google_sheets()
        if not client:
            update.message.reply_text("❌ Ошибка подключения к Google Sheets")
            return SELECTING_ACTION
            
        sheet = client.open("Учет строительных объектов ООО ИКС ГЕОСТРОЙ").worksheet("Объекты")
        objects_data = sheet.get_all_values()
        
        if len(objects_data) <= 1:  # Только заголовок
            update.message.reply_text("❌ Нет доступных объектов")
            return SELECTING_ACTION
        
        # Преобразуем в удобный формат
        objects = []
        for row in objects_data[1:]:  # Пропускаем заголовок
            if row[0]:  # Если адрес не пустой
                objects.append({
                    'Адрес': row[0],
                    'Название': row[1] if len(row) > 1 else '',
                    'Зарплата': row[2] if len(row) > 2 else '0',
                    'Материалы': row[3] if len(row) > 3 else '0'
                })
        
        keyboard = []
        for obj in objects:
            button_text = f"{obj['Адрес']} - {obj['Название']}"
            keyboard.append([KeyboardButton(button_text)])
        
        keyboard.append([KeyboardButton("🔙 Назад")])
        
        context.user_data['objects'] = objects
        update.message.reply_text(
            "Выберите объект для добавления материалов:",
            reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
        )
        return ENTERING_MATERIAL_NAME
        
    except Exception as e:
        logger.error(f"Ошибка при получении объектов: {e}")
        update.message.reply_text("❌ Ошибка при загрузке объектов")
        return SELECTING_ACTION

# Ввод названия материала
def enter_material_name(update: Update, context: CallbackContext):
    if update.message.text == "🔙 Назад":
        update.message.reply_text("Главное меню:", reply_markup=main_keyboard())
        return SELECTING_ACTION
    
    selected_object = update.message.text
    context.user_data['selected_object'] = selected_object
    
    update.message.reply_text("Введите название материала:")
    return ENTERING_MATERIAL_COST

# Ввод стоимости материала
def enter_material_cost(update: Update, context: CallbackContext):
    context.user_data['material_name'] = update.message.text
    update.message.reply_text("Введите стоимость материала:")
    return ADDING_MATERIALS

# Сохранение материала
def save_material(update: Update, context: CallbackContext):
    try:
        material_cost = float(update.message.text.replace(',', '.'))
        
        client = init_google_sheets()
        if not client:
            update.message.reply_text("❌ Ошибка подключения к Google Sheets")
            return SELECTING_ACTION
            
        sheet = client.open("Учет строительных объектов ООО ИКС ГЕОСТРОЙ").worksheet("Объекты")
        objects_data = sheet.get_all_values()
        
        # Находим выбранный объект
        selected_text = context.user_data['selected_object']
        for i, row in enumerate(objects_data[1:], start=2):  # Пропускаем заголовок
            if len(row) >= 2:
                object_text = f"{row[0]} - {row[1]}"
                if object_text == selected_text:
                    # Обновляем сумму материалов
                    current_materials = float(row[3] or 0) if len(row) > 3 else 0
                    new_materials = current_materials + material_cost
                    sheet.update_cell(i, 4, str(new_materials))
                    
                    # Записываем в историю материалов
                    try:
                        history_sheet = client.open("Учет строительных объектов ООО ИКС ГЕОСТРОЙ").worksheet("Материалы")
                    except:
                        # Создаем лист если не существует
                        spreadsheet = client.open("Учет строительных объектов ООО ИКС ГЕОСТРОЙ")
                        history_sheet = spreadsheet.add_worksheet(title="Материалы", rows=100, cols=5)
                        history_sheet.append_row(["Адрес", "Название", "Материал", "Стоимость", "Дата"])
                    
                    history_sheet.append_row([
                        row[0], row[1], 
                        context.user_data['material_name'], 
                        material_cost, 
                        update.message.date.strftime("%Y-%m-%d %H:%M")
                    ])
                    
                    update.message.reply_text(
                        f"✅ Материал добавлен!\n"
                        f"Объект: {selected_text}\n"
                        f"Материал: {context.user_data['material_name']}\n"
                        f"Стоимость: {material_cost} руб.\n"
                        f"Общая сумма материалов на объекте: {new_materials} руб.",
                        reply_markup=main_keyboard()
                    )
                    break
        
    except ValueError:
        update.message.reply_text("❌ Пожалуйста, введите корректную сумму:")
        return ADDING_MATERIALS
    except Exception as e:
        logger.error(f"Ошибка при добавлении материала: {e}")
        update.message.reply_text("❌ Ошибка при добавлении материала")
    
    return SELECTING_ACTION

# Отчет по объектам
def show_report(update: Update, context: CallbackContext):
    try:
        client = init_google_sheets()
        if not client:
            update.message.reply_text("❌ Ошибка подключения к Google Sheets")
            return SELECTING_ACTION
            
        sheet = client.open("Учет строительных объектов ООО ИКС ГЕОСТРОЙ").worksheet("Объекты")
        objects_data = sheet.get_all_values()
        
        if len(objects_data) <= 1:  # Только заголовок
            update.message.reply_text("❌ Нет данных об объектах")
            return SELECTING_ACTION
        
        report = "📊 ОТЧЕТ ПО ОБЪЕКТАМ:\n\n"
        total_salary = 0
        total_materials = 0
        
        for row in objects_data[1:]:  # Пропускаем заголовок
            if row[0]:  # Если адрес не пустой
                address = row[0]
                name = row[1] if len(row) > 1 else "Нет названия"
                salary = float(row[2] or 0) if len(row) > 2 else 0
                materials = float(row[3] or 0) if len(row) > 3 else 0
                total_cost = salary + materials
                
                report += f"🏗️ {address}\n"
                report += f"   Название: {name}\n"
                report += f"   Зарплата: {salary:,.2f} руб.\n"
                report += f"   Материалы: {materials:,.2f} руб.\n"
                report += f"   ИТОГО: {total_cost:,.2f} руб.\n\n"
                
                total_salary += salary
                total_materials += materials
        
        report += f"📈 ОБЩИЕ СУММЫ:\n"
        report += f"Зарплаты: {total_salary:,.2f} руб.\n"
        report += f"Материалы: {total_materials:,.2f} руб.\n"
        report += f"ВСЕГО: {total_salary + total_materials:,.2f} руб."
        
        # Разбиваем сообщение если слишком длинное
        if len(report) > 4000:
            parts = [report[i:i+4000] for i in range(0, len(report), 4000)]
            for part in parts:
                update.message.reply_text(part)
        else:
            update.message.reply_text(report)
        
    except Exception as e:
        logger.error(f"Ошибка при формировании отчета: {e}")
        update.message.reply_text("❌ Ошибка при формировании отчета")
    
    return SELECTING_ACTION

# Отмена
def cancel(update: Update, context: CallbackContext):
    update.message.reply_text(
        "Действие отменено.",
        reply_markup=main_keyboard()
    )
    return SELECTING_ACTION

def main():
    # Проверяем токен
    if not BOT_TOKEN:
        print("Ошибка: BOT_TOKEN не найден!")
        return
    
    # Создаем updater и dispatcher
    updater = Updater(BOT_TOKEN, use_context=True)
    dp = updater.dispatcher
    
    # ConversationHandler для управления состояниями
    conv_handler = ConversationHandler(
        entry_points=[CommandHandler('start', start)],
        states={
            SELECTING_ACTION: [
                MessageHandler(Filters.text("📋 Добавить объект"), add_object_start),
                MessageHandler(Filters.text("💰 Добавить зарплату"), add_salary_start),
                MessageHandler(Filters.text("🏗️ Добавить материалы"), add_materials_start),
                MessageHandler(Filters.text("📊 Отчет по объектам"), show_report),
            ],
            ENTERING_ADDRESS: [MessageHandler(Filters.text & ~Filters.command, enter_address)],
            ENTERING_NAME: [MessageHandler(Filters.text & ~Filters.command, enter_name)],
            ENTERING_SALARY: [MessageHandler(Filters.text & ~Filters.command, enter_salary)],
            ADDING_SALARY: [MessageHandler(Filters.text & ~Filters.command, save_salary)],
            ENTERING_MATERIAL_NAME: [MessageHandler(Filters.text & ~Filters.command, enter_material_name)],
            ENTERING_MATERIAL_COST: [MessageHandler(Filters.text & ~Filters.command, enter_material_cost)],
            ADDING_MATERIALS: [MessageHandler(Filters.text & ~Filters.command, save_material)],
        },
        fallbacks=[CommandHandler('cancel', cancel)]
    )
    
    dp.add_handler(conv_handler)
    
    # Запуск бота
    print("Бот запущен...")
    updater.start_polling()
    updater.idle()

if __name__ == '__main__':
    main()
