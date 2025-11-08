import os
import logging
from telegram import Update, ReplyKeyboardMarkup, KeyboardButton
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ConversationHandler, ContextTypes
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
import gspread
from dotenv import load_dotenv

load_dotenv()

# Настройка логирования
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Токен бота из переменных окружения
BOT_TOKEN = os.getenv('BOT_TOKEN')

# Настройки Google Sheets
SCOPES = ['https://www.googleapis.com/auth/spreadsheets']
SPREADSHEET_ID = 'your_spreadsheet_id_here'  # Замените на ID вашей таблицы

# Состояния для ConversationHandler
SELECTING_ACTION, ADDING_OBJECT, ADDING_SALARY, ADDING_MATERIALS, ENTERING_ADDRESS, ENTERING_NAME, ENTERING_SALARY, ENTERING_MATERIAL_NAME, ENTERING_MATERIAL_COST = range(9)

# Инициализация Google Sheets
def init_google_sheets():
    creds = Credentials.from_service_account_file('credentials.json', scopes=SCOPES)
    client = gspread.authorize(creds)
    return client

# Команда /start
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.message.from_user
    await update.message.reply_text(
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
async def add_object_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Введите адрес строительного объекта:")
    return ENTERING_ADDRESS

# Шаг 2: название объекта
async def enter_address(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['address'] = update.message.text
    await update.message.reply_text("Введите название объекта:")
    return ENTERING_NAME

# Сохранение объекта в Google Sheets
async def enter_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['name'] = update.message.text
    
    try:
        client = init_google_sheets()
        sheet = client.open("Учет строительных объектов ООО ИКС ГЕОСТРОЙ").worksheet("Объекты")
        
        # Добавляем новый объект
        sheet.append_row([
            context.user_data['address'],
            context.user_data['name'],
            '0',  # начальная сумма зарплат
            '0'   # начальная сумма материалов
        ])
        
        await update.message.reply_text(
            f"✅ Объект добавлен!\n"
            f"Адрес: {context.user_data['address']}\n"
            f"Название: {context.user_data['name']}",
            reply_markup=main_keyboard()
        )
    except Exception as e:
        logger.error(f"Ошибка при добавлении объекта: {e}")
        await update.message.reply_text("❌ Ошибка при добавлении объекта")
    
    return SELECTING_ACTION

# Добавление зарплаты - выбор объекта
async def add_salary_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        client = init_google_sheets()
        sheet = client.open("Учет строительных объектов ООО ИКС ГЕОСТРОЙ").worksheet("Объекты")
        objects = sheet.get_all_records()
        
        if not objects:
            await update.message.reply_text("❌ Нет доступных объектов")
            return SELECTING_ACTION
        
        keyboard = []
        for obj in objects:
            button_text = f"{obj['Адрес']} - {obj['Название']}"
            keyboard.append([KeyboardButton(button_text)])
        
        keyboard.append([KeyboardButton("🔙 Назад")])
        
        context.user_data['objects'] = objects
        await update.message.reply_text(
            "Выберите объект для добавления зарплаты:",
            reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
        )
        return ENTERING_SALARY
        
    except Exception as e:
        logger.error(f"Ошибка при получении объектов: {e}")
        await update.message.reply_text("❌ Ошибка при загрузке объектов")
        return SELECTING_ACTION

# Ввод суммы зарплаты
async def enter_salary(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.text == "🔙 Назад":
        await update.message.reply_text("Главное меню:", reply_markup=main_keyboard())
        return SELECTING_ACTION
    
    selected_object = update.message.text
    context.user_data['selected_object'] = selected_object
    
    await update.message.reply_text("Введите сумму зарплаты:")
    return ADDING_SALARY

# Сохранение зарплаты
async def save_salary(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        salary_amount = float(update.message.text.replace(',', '.'))
        
        client = init_google_sheets()
        sheet = client.open("Учет строительных объектов ООО ИКС ГЕОСТРОЙ").worksheet("Объекты")
        objects_data = sheet.get_all_values()
        
        # Находим выбранный объект
        selected_text = context.user_data['selected_object']
        for i, row in enumerate(objects_data[1:], start=2):  # Пропускаем заголовок
            object_text = f"{row[0]} - {row[1]}"
            if object_text == selected_text:
                # Обновляем сумму зарплат
                current_salary = float(row[2] or 0)
                new_salary = current_salary + salary_amount
                sheet.update_cell(i, 3, str(new_salary))
                
                # Записываем в историю зарплат
                history_sheet = client.open("Учет строительных объектов ООО ИКС ГЕОСТРОЙ").worksheet("Зарплаты")
                history_sheet.append_row([
                    row[0], row[1], salary_amount, update.message.date.strftime("%Y-%m-%d %H:%M")
                ])
                
                await update.message.reply_text(
                    f"✅ Зарплата добавлена!\n"
                    f"Объект: {selected_text}\n"
                    f"Сумма: {salary_amount} руб.\n"
                    f"Общая сумма зарплат на объекте: {new_salary} руб.",
                    reply_markup=main_keyboard()
                )
                break
        
    except ValueError:
        await update.message.reply_text("❌ Пожалуйста, введите корректную сумму:")
        return ADDING_SALARY
    except Exception as e:
        logger.error(f"Ошибка при добавлении зарплаты: {e}")
        await update.message.reply_text("❌ Ошибка при добавлении зарплаты")
    
    return SELECTING_ACTION

# Добавление материалов - выбор объекта
async def add_materials_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        client = init_google_sheets()
        sheet = client.open("Учет строительных объектов ООО ИКС ГЕОСТРОЙ").worksheet("Объекты")
        objects = sheet.get_all_records()
        
        if not objects:
            await update.message.reply_text("❌ Нет доступных объектов")
            return SELECTING_ACTION
        
        keyboard = []
        for obj in objects:
            button_text = f"{obj['Адрес']} - {obj['Название']}"
            keyboard.append([KeyboardButton(button_text)])
        
        keyboard.append([KeyboardButton("🔙 Назад")])
        
        context.user_data['objects'] = objects
        await update.message.reply_text(
            "Выберите объект для добавления материалов:",
            reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
        )
        return ENTERING_MATERIAL_NAME
        
    except Exception as e:
        logger.error(f"Ошибка при получении объектов: {e}")
        await update.message.reply_text("❌ Ошибка при загрузке объектов")
        return SELECTING_ACTION

# Ввод названия материала
async def enter_material_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.text == "🔙 Назад":
        await update.message.reply_text("Главное меню:", reply_markup=main_keyboard())
        return SELECTING_ACTION
    
    selected_object = update.message.text
    context.user_data['selected_object'] = selected_object
    
    await update.message.reply_text("Введите название материала:")
    return ENTERING_MATERIAL_COST

# Ввод стоимости материала
async def enter_material_cost(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['material_name'] = update.message.text
    await update.message.reply_text("Введите стоимость материала:")
    return ADDING_MATERIALS

# Сохранение материала
async def save_material(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        material_cost = float(update.message.text.replace(',', '.'))
        
        client = init_google_sheets()
        sheet = client.open("Учет строительных объектов ООО ИКС ГЕОСТРОЙ").worksheet("Объекты")
        objects_data = sheet.get_all_values()
        
        # Находим выбранный объект
        selected_text = context.user_data['selected_object']
        for i, row in enumerate(objects_data[1:], start=2):  # Пропускаем заголовок
            object_text = f"{row[0]} - {row[1]}"
            if object_text == selected_text:
                # Обновляем сумму материалов
                current_materials = float(row[3] or 0)
                new_materials = current_materials + material_cost
                sheet.update_cell(i, 4, str(new_materials))
                
                # Записываем в историю материалов
                history_sheet = client.open("Учет строительных объектов ООО ИКС ГЕОСТРОЙ").worksheet("Материалы")
                history_sheet.append_row([
                    row[0], row[1], 
                    context.user_data['material_name'], 
                    material_cost, 
                    update.message.date.strftime("%Y-%m-%d %H:%M")
                ])
                
                await update.message.reply_text(
                    f"✅ Материал добавлен!\n"
                    f"Объект: {selected_text}\n"
                    f"Материал: {context.user_data['material_name']}\n"
                    f"Стоимость: {material_cost} руб.\n"
                    f"Общая сумма материалов на объекте: {new_materials} руб.",
                    reply_markup=main_keyboard()
                )
                break
        
    except ValueError:
        await update.message.reply_text("❌ Пожалуйста, введите корректную сумму:")
        return ADDING_MATERIALS
    except Exception as e:
        logger.error(f"Ошибка при добавлении материала: {e}")
        await update.message.reply_text("❌ Ошибка при добавлении материала")
    
    return SELECTING_ACTION

# Отчет по объектам
async def show_report(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        client = init_google_sheets()
        sheet = client.open("Учет строительных объектов ООО ИКС ГЕОСТРОЙ").worksheet("Объекты")
        objects = sheet.get_all_records()
        
        if not objects:
            await update.message.reply_text("❌ Нет данных об объектах")
            return SELECTING_ACTION
        
        report = "📊 ОТЧЕТ ПО ОБЪЕКТАМ:\n\n"
        total_salary = 0
        total_materials = 0
        
        for obj in objects:
            salary = float(obj.get('Зарплата', 0) or 0)
            materials = float(obj.get('Материалы', 0) or 0)
            total_cost = salary + materials
            
            report += f"🏗️ {obj['Адрес']}\n"
            report += f"   Название: {obj['Название']}\n"
            report += f"   Зарплата: {salary:,.2f} руб.\n"
            report += f"   Материалы: {materials:,.2f} руб.\n"
            report += f"   ИТОГО: {total_cost:,.2f} руб.\n\n"
            
            total_salary += salary
            total_materials += materials
        
        report += f"📈 ОБЩИЕ СУММЫ:\n"
        report += f"Зарплаты: {total_salary:,.2f} руб.\n"
        report += f"Материалы: {total_materials:,.2f} руб.\n"
        report += f"ВСЕГО: {total_salary + total_materials:,.2f} руб."
        
        await update.message.reply_text(report)
        
    except Exception as e:
        logger.error(f"Ошибка при формировании отчета: {e}")
        await update.message.reply_text("❌ Ошибка при формировании отчета")
    
    return SELECTING_ACTION

# Отмена
async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Действие отменено.",
        reply_markup=main_keyboard()
    )
    return SELECTING_ACTION

def main():
    # Создаем приложение
    application = Application.builder().token(BOT_TOKEN).build()
    
    # ConversationHandler для управления состояниями
    conv_handler = ConversationHandler(
        entry_points=[CommandHandler('start', start)],
        states={
            SELECTING_ACTION: [
                MessageHandler(filters.Text(["📋 Добавить объект"]), add_object_start),
                MessageHandler(filters.Text(["💰 Добавить зарплату"]), add_salary_start),
                MessageHandler(filters.Text(["🏗️ Добавить материалы"]), add_materials_start),
                MessageHandler(filters.Text(["📊 Отчет по объектам"]), show_report),
            ],
            ENTERING_ADDRESS: [MessageHandler(filters.TEXT & ~filters.COMMAND, enter_address)],
            ENTERING_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, enter_name)],
            ENTERING_SALARY: [MessageHandler(filters.TEXT & ~filters.COMMAND, enter_salary)],
            ADDING_SALARY: [MessageHandler(filters.TEXT & ~filters.COMMAND, save_salary)],
            ENTERING_MATERIAL_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, enter_material_name)],
            ENTERING_MATERIAL_COST: [MessageHandler(filters.TEXT & ~filters.COMMAND, enter_material_cost)],
            ADDING_MATERIALS: [MessageHandler(filters.TEXT & ~filters.COMMAND, save_material)],
        },
        fallbacks=[CommandHandler('cancel', cancel)]
    )
    
    application.add_handler(conv_handler)
    
    # Запуск бота
    print("Бот запущен...")
    application.run_polling()

if __name__ == '__main__':
    main()
