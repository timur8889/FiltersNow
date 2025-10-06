import asyncio
import datetime
import logging
from aiogram import Bot, Dispatcher, types
from aiogram.contrib.fsm_storage.memory import MemoryStorage
from aiogram.dispatcher import FSMContext
from aiogram.dispatcher.filters.state import State, StatesGroup

# Настройка логирования
logging.basicConfig(level=logging.INFO)

# Замените на ваш токен бота
API_TOKEN = '8278600298:AAFA-R0ql-dibAoBruxgwitHTx_LLx61OdM'

bot = Bot(token=API_TOKEN)
storage = MemoryStorage()
dp = Dispatcher(bot, storage=storage)

# Временное хранилище данных (в реальном проекте используйте БД)
filters_db = []

class FilterForm(StatesGroup):
    name = State()
    install_date = State()
    expiry_date = State()

@dp.message_handler(commands=['start'])
async def cmd_start(message: types.Message):
    await message.answer(
        "Добро пожаловать в менеджер фильтров!\n"
        "Доступные команды:\n"
        "/add - добавить новый фильтр\n"
        "/list - показать все фильтры\n"
        "/check - проверить просроченные фильтры"
    )

@dp.message_handler(commands=['add'])
async def cmd_add(message: types.Message):
    await FilterForm.name.set()
    await message.answer("Введите название фильтра:")

@dp.message_handler(state=FilterForm.name)
async def process_name(message: types.Message, state: FSMContext):
    async with state.proxy() as data:
        data['name'] = message.text

    await FilterForm.next()
    await message.answer("Введите дату установки (ДД.ММ.ГГГГ):")

@dp.message_handler(state=FilterForm.install_date)
async def process_install_date(message: types.Message, state: FSMContext):
    try:
        install_date = datetime.datetime.strptime(message.text, '%d.%m.%Y').date()
    except ValueError:
        await message.answer("Неверный формат даты! Используйте ДД.ММ.ГГГГ")
        return

    async with state.proxy() as data:
        data['install_date'] = install_date

    await FilterForm.next()
    await message.answer("Введите срок годности (в днях) или дату окончания (ДД.ММ.ГГГГ):")

@dp.message_handler(state=FilterForm.expiry_date)
async def process_expiry(message: types.Message, state: FSMContext):
    async with state.proxy() as data:
        try:
            # Попытка интерпретировать ввод как количество дней
            expiry_days = int(message.text)
            expiry_date = data['install_date'] + datetime.timedelta(days=expiry_days)
        except ValueError:
            # Если не число, пробуем как дату
            try:
                expiry_date = datetime.datetime.strptime(message.text, '%d.%m.%Y').date()
            except ValueError:
                await message.answer("Неверный формат! Используйте число (дни) или ДД.ММ.ГГГГ")
                return

        filter_data = {
            'name': data['name'],
            'install_date': data['install_date'],
            'expiry_date': expiry_date,
            'user_id': message.from_user.id
        }

        filters_db.append(filter_data)
        await message.answer(
            f"Фильтр добавлен!\n"
            f"Название: {data['name']}\n"
            f"Дата установки: {data['install_date'].strftime('%d.%m.%Y')}\n"
            f"Срок годности до: {expiry_date.strftime('%d.%m.%Y')}"
        )

    await state.finish()

@dp.message_handler(commands=['list'])
async def cmd_list(message: types.Message):
    if not filters_db:
        await message.answer("Список фильтров пуст")
        return

    response = "Список фильтров:\n\n"
    for idx, filter_data in enumerate(filters_db, 1):
        status = "🔴 Просрочен" if filter_data['expiry_date'] < datetime.date.today() else "✅ Активен"
        response += (
            f"{idx}. {filter_data['name']}\n"
            f"Установлен: {filter_data['install_date'].strftime('%d.%m.%Y')}\n"
            f"Годен до: {filter_data['expiry_date'].strftime('%d.%m.%Y')}\n"
            f"Статус: {status}\n\n"
        )

    await message.answer(response)

@dp.message_handler(commands=['check'])
async def cmd_check(message: types.Message):
    today = datetime.date.today()
    expired_filters = [
        f for f in filters_db
        if f['expiry_date'] < today
    ]

    if not expired_filters:
        await message.answer("Нет просроченных фильтров")
    else:
        response = "Просроченные фильтры:\n\n"
        for filter_data in expired_filters:
            response += (
                f"🔴 {filter_data['name']}\n"
                f"Просрочен с: {filter_data['expiry_date'].strftime('%d.%m.%Y')}\n\n"
            )
        await message.answer(response)


if __name__ == '__main__':
    from aiogram import executor
    loop = asyncio.get_event_loop()
    loop.create_task(check_expired_periodically())
    executor.start_polling(dp, skip_updates=True)
