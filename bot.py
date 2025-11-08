import os
import logging
import json
from datetime import datetime
from telegram import Update, ReplyKeyboardMarkup, KeyboardButton
from telegram.ext import (Updater, CommandHandler, MessageHandler, Filters, 
                         ConversationHandler, CallbackContext)
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

# Состояния для ConversationHandler
(
    SELECTING_ACTION, ADDING_OBJECT, ADDING_SALARY, ADDING_MATERIALS,
    ENTERING_ADDRESS, ENTERING_NAME, ENTERING_SALARY, 
    ENTERING_MATERIAL_NAME, ENTERING_MATERIAL_COST,
    CONFIRMING_OBJECT, CONFIRMING_SALARY, CONFIRMING_MATERIAL,
    EDITING_OBJECT, EDITING_SALARY, EDITING_MATERIAL
) = range(15)

# Файлы для хранения данных
OBJECTS_FILE = 'objects.json'
SALARIES_FILE = 'salaries.json'
MATERIALS_FILE = 'materials.json'

# Инициализация данных
def init_data():
    # Создаем файлы если не существуют
    for file in [OBJECTS_FILE, SALARIES_FILE, MATERIALS_FILE]:
        if not os.path.exists(file):
            with open(file, 'w', encoding='utf-8') as f:
                json.dump([], f, ensure_ascii=False, indent=2)

# Функции для работы с данными
def load_objects():
    try:
        with open(OBJECTS_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return []

def save_objects(objects):
    with open(OBJECTS_FILE, 'w', encoding='utf-8') as f:
        json.dump(objects, f, ensure_ascii=False, indent=2)

def load_salaries():
    try:
        with open(SALARIES_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return []

def save_salaries(salaries):
    with open(SALARIES_FILE, 'w', encoding='utf-8') as f:
        json.dump(salaries, f, ensure_ascii=False, indent=2)

def load_materials():
    try:
        with open(MATERIALS_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return []

def save_materials(materials):
    with open(MATERIALS_FILE, 'w', encoding='utf-8') as f:
        json.dump(materials, f, ensure_ascii=False, indent=2)

# Главная клавиатура
def main_keyboard():
    keyboard = [
        [KeyboardButton("📋 Добавить объект")],
        [KeyboardButton("💰 Добавить зарплату")],
        [KeyboardButton("🏗️ Добавить материалы")],
        [KeyboardButton("📊 Отчет по объектам")]
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

# Клавиатура подтверждения/редактирования
def confirmation_keyboard():
    keyboard = [
        [KeyboardButton("✅ Подтвердить"), KeyboardButton("✏️ Редактировать")],
        [KeyboardButton("❌ Отменить")]
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

# Клавиатура редактирования полей для объекта
def edit_object_fields_keyboard():
    keyboard = [
        [KeyboardButton("✏️ Редактировать адрес")],
        [KeyboardButton("✏️ Редактировать название")],
        [KeyboardButton("🔙 Назад к подтверждению")]
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

# Клавиатура редактирования полей для зарплаты
def edit_salary_fields_keyboard():
    keyboard = [
        [KeyboardButton("✏️ Редактировать объект")],
        [KeyboardButton("✏️ Редактировать сумму")],
        [KeyboardButton("🔙 Назад к подтверждению")]
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

# Клавиатура редактирования полей для материала
def edit_material_fields_keyboard():
    keyboard = [
        [KeyboardButton("✏️ Редактировать объект")],
        [KeyboardButton("✏️ Редактировать название материала")],
        [KeyboardButton("✏️ Редактировать стоимость")],
        [KeyboardButton("🔙 Назад к подтверждению")]
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

# Команда /start
def start(update: Update, context: CallbackContext):
    user = update.message.from_user
    update.message.reply_text(
        f"Добро пожаловать в систему учета ООО ИКС ГЕОСТРОЙ, {user.first_name}!\n\n"
        "Выберите действие:",
        reply_markup=main_keyboard()
    )
    return SELECTING_ACTION

# Добавление объекта - шаг 1: адрес
def add_object_start(update: Update, context: CallbackContext):
    context.user_data.clear()
    update.message.reply_text("Введите адрес строительного объекта:")
    return ENTERING_ADDRESS

# Шаг 2: название объекта
def enter_address(update: Update, context: CallbackContext):
    context.user_data['address'] = update.message.text
    update.message.reply_text("Введите название объекта:")
    return ENTERING_NAME

# Подтверждение объекта
def enter_name(update: Update, context: CallbackContext):
    context.user_data['name'] = update.message.text
    return show_object_confirmation(update, context)

# Показать подтверждение объекта
def show_object_confirmation(update: Update, context: CallbackContext):
    update.message.reply_text(
        f"📋 ПОДТВЕРЖДЕНИЕ ДОБАВЛЕНИЯ ОБЪЕКТА:\n\n"
        f"🏗️ Адрес: {context.user_data['address']}\n"
        f"📝 Название: {context.user_data['name']}\n\n"
        f"Подтвердите добавление объекта:",
        reply_markup=confirmation_keyboard()
    )
    return CONFIRMING_OBJECT

# Сохранение объекта в JSON
def save_object_to_json(context):
    try:
        objects = load_objects()
        
        # Проверяем, нет ли уже объекта с таким адресом
        for obj in objects:
            if obj['address'] == context.user_data['address']:
                return False, "❌ Объект с таким адресом уже существует"
        
        # Добавляем новый объект
        new_object = {
            'address': context.user_data['address'],
            'name': context.user_data['name'],
            'salary_total': 0.0,
            'materials_total': 0.0,
            'created_at': datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        }
        
        objects.append(new_object)
        save_objects(objects)
        
        return True, "✅ Объект успешно добавлен!"
        
    except Exception as e:
        logger.error(f"Ошибка при добавлении объекта: {e}")
        return False, f"❌ Ошибка при добавлении объекта: {str(e)}"

# Обработка подтверждения объекта
def confirm_object(update: Update, context: CallbackContext):
    text = update.message.text
    
    if text == "✅ Подтвердить":
        success, message = save_object_to_json(context)
        
        if success:
            update.message.reply_text(
                f"{message}\n"
                f"🏗️ Адрес: {context.user_data['address']}\n"
                f"📝 Название: {context.user_data['name']}",
                reply_markup=main_keyboard()
            )
        else:
            update.message.reply_text(
                message,
                reply_markup=main_keyboard()
            )
        
        context.user_data.clear()
        return SELECTING_ACTION
    
    elif text == "✏️ Редактировать":
        update.message.reply_text(
            "Выберите поле для редактирования:",
            reply_markup=edit_object_fields_keyboard()
        )
        return EDITING_OBJECT
    
    elif text == "❌ Отменить":
        return cancel(update, context)
    else:
        update.message.reply_text("Пожалуйста, используйте кнопки для выбора действия:")
        return CONFIRMING_OBJECT

# Редактирование объекта
def edit_object(update: Update, context: CallbackContext):
    text = update.message.text
    
    if text == "✏️ Редактировать адрес":
        update.message.reply_text("Введите новый адрес объекта:")
        return ENTERING_ADDRESS
    elif text == "✏️ Редактировать название":
        update.message.reply_text("Введите новое название объекта:")
        return ENTERING_NAME
    elif text == "🔙 Назад к подтверждению":
        # Возвращаемся к подтверждению с текущими данными
        return show_object_confirmation(update, context)
    else:
        update.message.reply_text("Пожалуйста, используйте кнопки для выбора действия:")
        return EDITING_OBJECT

# Добавление зарплаты - выбор объекта
def add_salary_start(update: Update, context: CallbackContext):
    context.user_data.clear()
    try:
        objects = load_objects()
        
        if not objects:
            update.message.reply_text("❌ Нет доступных объектов. Сначала добавьте объект.")
            return SELECTING_ACTION
        
        keyboard = []
        for obj in objects:
            button_text = f"{obj['address']} - {obj['name']}"
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

# Подтверждение зарплаты
def add_salary_amount(update: Update, context: CallbackContext):
    try:
        salary_amount = float(update.message.text.replace(',', '.'))
        context.user_data['salary_amount'] = salary_amount
        
        return show_salary_confirmation(update, context)
        
    except ValueError:
        update.message.reply_text("❌ Пожалуйста, введите корректную сумму:")
        return ADDING_SALARY

# Показать подтверждение зарплаты
def show_salary_confirmation(update: Update, context: CallbackContext):
    update.message.reply_text(
        f"💰 ПОДТВЕРЖДЕНИЕ ДОБАВЛЕНИЯ ЗАРПЛАТЫ:\n\n"
        f"🏗️ Объект: {context.user_data['selected_object']}\n"
        f"💵 Сумма: {context.user_data['salary_amount']:,.2f} руб.\n\n"
        f"Подтвердите добавление зарплаты:",
        reply_markup=confirmation_keyboard()
    )
    return CONFIRMING_SALARY

# Сохранение зарплаты в JSON
def save_salary_to_json(context):
    try:
        salary_amount = context.user_data['salary_amount']
        selected_text = context.user_data['selected_object']
        
        objects = load_objects()
        salaries = load_salaries()
        
        # Находим выбранный объект
        for obj in objects:
            object_text = f"{obj['address']} - {obj['name']}"
            if object_text == selected_text:
                # Обновляем сумму зарплат в объекте
                obj['salary_total'] = obj.get('salary_total', 0) + salary_amount
                
                # Добавляем запись в историю зарплат
                new_salary = {
                    'address': obj['address'],
                    'name': obj['name'],
                    'amount': salary_amount,
                    'date': context.user_data.get('current_date', datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
                }
                salaries.append(new_salary)
                
                # Сохраняем обновленные данные
                save_objects(objects)
                save_salaries(salaries)
                
                return True, f"✅ Зарплата успешно добавлена! Общая сумма: {obj['salary_total']:,.2f} руб."
        
        return False, "❌ Объект не найден"
            
    except Exception as e:
        logger.error(f"Ошибка при добавлении зарплаты: {e}")
        return False, f"❌ Ошибка при добавлении зарплаты: {str(e)}"

# Обработка подтверждения зарплаты
def confirm_salary(update: Update, context: CallbackContext):
    text = update.message.text
    
    if text == "✅ Подтвердить":
        # Сохраняем текущую дату для записи
        context.user_data['current_date'] = update.message.date.strftime("%Y-%m-%d %H:%M:%S")
        
        success, message = save_salary_to_json(context)
        
        update.message.reply_text(
            f"{message}\n"
            f"🏗️ Объект: {context.user_data['selected_object']}\n"
            f"💵 Сумма: {context.user_data['salary_amount']:,.2f} руб.",
            reply_markup=main_keyboard()
        )
        
        context.user_data.clear()
        return SELECTING_ACTION
    
    elif text == "✏️ Редактировать":
        update.message.reply_text(
            "Выберите поле для редактирования:",
            reply_markup=edit_salary_fields_keyboard()
        )
        return EDITING_SALARY
    
    elif text == "❌ Отменить":
        return cancel(update, context)
    else:
        update.message.reply_text("Пожалуйста, используйте кнопки для выбора действия:")
        return CONFIRMING_SALARY

# Редактирование зарплаты
def edit_salary(update: Update, context: CallbackContext):
    text = update.message.text
    
    if text == "✏️ Редактировать объект":
        return add_salary_start(update, context)
    elif text == "✏️ Редактировать сумму":
        update.message.reply_text("Введите новую сумму зарплаты:")
        return ADDING_SALARY
    elif text == "🔙 Назад к подтверждению":
        # Возвращаемся к подтверждению
        return show_salary_confirmation(update, context)
    else:
        update.message.reply_text("Пожалуйста, используйте кнопки для выбора действия:")
        return EDITING_SALARY

# Добавление материалов - выбор объекта
def add_materials_start(update: Update, context: CallbackContext):
    context.user_data.clear()
    try:
        objects = load_objects()
        
        if not objects:
            update.message.reply_text("❌ Нет доступных объектов. Сначала добавьте объект.")
            return SELECTING_ACTION
        
        keyboard = []
        for obj in objects:
            button_text = f"{obj['address']} - {obj['name']}"
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

# Подтверждение материала
def add_material_cost(update: Update, context: CallbackContext):
    try:
        material_cost = float(update.message.text.replace(',', '.'))
        context.user_data['material_cost'] = material_cost
        
        return show_material_confirmation(update, context)
        
    except ValueError:
        update.message.reply_text("❌ Пожалуйста, введите корректную сумму:")
        return ADDING_MATERIALS

# Показать подтверждение материала
def show_material_confirmation(update: Update, context: CallbackContext):
    update.message.reply_text(
        f"🏗️ ПОДТВЕРЖДЕНИЕ ДОБАВЛЕНИЯ МАТЕРИАЛА:\n\n"
        f"📦 Объект: {context.user_data['selected_object']}\n"
        f"🔧 Материал: {context.user_data['material_name']}\n"
        f"💵 Стоимость: {context.user_data['material_cost']:,.2f} руб.\n\n"
        f"Подтвердите добавление материала:",
        reply_markup=confirmation_keyboard()
    )
    return CONFIRMING_MATERIAL

# Сохранение материала в JSON
def save_material_to_json(context):
    try:
        material_cost = context.user_data['material_cost']
        selected_text = context.user_data['selected_object']
        
        objects = load_objects()
        materials = load_materials()
        
        # Находим выбранный объект
        for obj in objects:
            object_text = f"{obj['address']} - {obj['name']}"
            if object_text == selected_text:
                # Обновляем сумму материалов в объекте
                obj['materials_total'] = obj.get('materials_total', 0) + material_cost
                
                # Добавляем запись в историю материалов
                new_material = {
                    'address': obj['address'],
                    'name': obj['name'],
                    'material_name': context.user_data['material_name'],
                    'cost': material_cost,
                    'date': context.user_data.get('current_date', datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
                }
                materials.append(new_material)
                
                # Сохраняем обновленные данные
                save_objects(objects)
                save_materials(materials)
                
                return True, f"✅ Материал успешно добавлен! Общая сумма: {obj['materials_total']:,.2f} руб."
        
        return False, "❌ Объект не найден"
            
    except Exception as e:
        logger.error(f"Ошибка при добавлении материала: {e}")
        return False, f"❌ Ошибка при добавлении материала: {str(e)}"

# Обработка подтверждения материала
def confirm_material(update: Update, context: CallbackContext):
    text = update.message.text
    
    if text == "✅ Подтвердить":
        # Сохраняем текущую дату для записи
        context.user_data['current_date'] = update.message.date.strftime("%Y-%m-%d %H:%M:%S")
        
        success, message = save_material_to_json(context)
        
        update.message.reply_text(
            f"{message}\n"
            f"📦 Объект: {context.user_data['selected_object']}\n"
            f"🔧 Материал: {context.user_data['material_name']}\n"
            f"💵 Стоимость: {context.user_data['material_cost']:,.2f} руб.",
            reply_markup=main_keyboard()
        )
        
        context.user_data.clear()
        return SELECTING_ACTION
    
    elif text == "✏️ Редактировать":
        update.message.reply_text(
            "Выберите поле для редактирования:",
            reply_markup=edit_material_fields_keyboard()
        )
        return EDITING_MATERIAL
    
    elif text == "❌ Отменить":
        return cancel(update, context)
    else:
        update.message.reply_text("Пожалуйста, используйте кнопки для выбора действия:")
        return CONFIRMING_MATERIAL

# Редактирование материала
def edit_material(update: Update, context: CallbackContext):
    text = update.message.text
    
    if text == "✏️ Редактировать объект":
        return add_materials_start(update, context)
    elif text == "✏️ Редактировать название материала":
        update.message.reply_text("Введите новое название материала:")
        return ENTERING_MATERIAL_COST
    elif text == "✏️ Редактировать стоимость":
        update.message.reply_text("Введите новую стоимость материала:")
        return ADDING_MATERIALS
    elif text == "🔙 Назад к подтверждению":
        # Возвращаемся к подтверждению
        return show_material_confirmation(update, context)
    else:
        update.message.reply_text("Пожалуйста, используйте кнопки для выбора действия:")
        return EDITING_MATERIAL

# Отчет по объектам
def show_report(update: Update, context: CallbackContext):
    try:
        objects = load_objects()
        
        if not objects:
            update.message.reply_text("❌ Нет данных об объектах")
            return SELECTING_ACTION
        
        report = "📊 ОТЧЕТ ПО ОБЪЕКТАМ:\n\n"
        total_salary = 0
        total_materials = 0
        
        for obj in objects:
            address = obj['address']
            name = obj['name']
            salary = obj.get('salary_total', 0)
            materials = obj.get('materials_total', 0)
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
    context.user_data.clear()
    update.message.reply_text(
        "❌ Действие отменено.",
        reply_markup=main_keyboard()
    )
    return SELECTING_ACTION

def main():
    # Проверяем токен
    if not BOT_TOKEN:
        print("Ошибка: BOT_TOKEN не найден!")
        return
    
    # Инициализируем данные
    init_data()
    
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
            CONFIRMING_OBJECT: [MessageHandler(Filters.text & ~Filters.command, confirm_object)],
            EDITING_OBJECT: [MessageHandler(Filters.text & ~Filters.command, edit_object)],
            
            ENTERING_SALARY: [MessageHandler(Filters.text & ~Filters.command, enter_salary)],
            ADDING_SALARY: [MessageHandler(Filters.text & ~Filters.command, add_salary_amount)],
            CONFIRMING_SALARY: [MessageHandler(Filters.text & ~Filters.command, confirm_salary)],
            EDITING_SALARY: [MessageHandler(Filters.text & ~Filters.command, edit_salary)],
            
            ENTERING_MATERIAL_NAME: [MessageHandler(Filters.text & ~Filters.command, enter_material_name)],
            ENTERING_MATERIAL_COST: [MessageHandler(Filters.text & ~Filters.command, enter_material_cost)],
            ADDING_MATERIALS: [MessageHandler(Filters.text & ~Filters.command, add_material_cost)],
            CONFIRMING_MATERIAL: [MessageHandler(Filters.text & ~Filters.command, confirm_material)],
            EDITING_MATERIAL: [MessageHandler(Filters.text & ~Filters.command, edit_material)],
        },
        fallbacks=[CommandHandler('cancel', cancel)]
    )
    
    dp.add_handler(conv_handler)
    
    # Запуск бота
    print("Бот запущен...")
    print("Данные сохраняются в JSON файлы:")
    print(f"- Объекты: {OBJECTS_FILE}")
    print(f"- Зарплаты: {SALARIES_FILE}")
    print(f"- Материалы: {MATERIALS_FILE}")
    
    updater.start_polling()
    updater.idle()

if __name__ == '__main__':
    main()
