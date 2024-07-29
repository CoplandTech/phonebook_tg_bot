from aiogram import Bot, Dispatcher, executor, types
from aiogram.dispatcher.filters import Text
from aiogram.dispatcher.filters.state import State, StatesGroup
from aiogram.contrib.fsm_storage.memory import MemoryStorage
from aiogram.dispatcher import FSMContext
from aiogram.types import BotCommand, InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.callback_data import CallbackData
from aiogram.utils.exceptions import MessageTextIsEmpty
import aioschedule as schedule
import asyncio
from datetime import datetime, timedelta
import re
import pymorphy2
from inc.config import TOKEN_API, LIST_ADMIN_ID, RETRY_INTERVAL_DAYS, PHRASES, PATH_XLSX_FILE
from keyboards import kb, ra, kb_phonebook, kb_phonebook_search, kb_request, admin_panel, ikb, ikb_request, kb_chat
from data import get_list_contact, get_unit_contact, getpagephones, generate_xlsx
from workrequests import record, get_unit_record, get_user_status, get_last_request_time, getpagerequests, getnotification, setrefusal, setapprove

bot = Bot(TOKEN_API)
storage = MemoryStorage()
dp = Dispatcher(bot=bot, storage=storage)

class Form(StatesGroup):
    name = State()

class Request(StatesGroup):
    name = State()
    job = State()

class Contact(StatesGroup):
    id = State()

# Инициализация pymorphy2
morph = pymorphy2.MorphAnalyzer()

# Функция для склонения
def decline_word(number, word):
    p = morph.parse(word)[0]
    return p.make_agree_with_number(number).word

async def scheduler():
    schedule.every().hour.at(":01").do(generate_xlsx)
    while True:
        await schedule.run_pending()

        now = datetime.now()
        
        next_hour = (now + timedelta(hours=1)).replace(minute=1, second=0, microsecond=0)
        sleep_duration = (next_hour - now).total_seconds()

        await asyncio.sleep(sleep_duration)

async def on_startup(_):
    print('Запущено')
    await bot.set_my_commands([
        BotCommand("start", PHRASES['start']),
        BotCommand("admin", PHRASES['admin_panel'])
    ])

@dp.message_handler(commands=['admin'])
async def open_admin_panel(message: types.Message):
    k = 0
    for admin_id in LIST_ADMIN_ID:
        k += 1
        if message.from_user.id == int(admin_id):
            await message.answer(text=PHRASES['login_admin'], reply_markup=admin_panel)
            break
        elif k == len(LIST_ADMIN_ID):
            await message.answer(text=PHRASES['access_error'])

async def handle_user_status(message: types.Message, user_status: str):
    user_id = message.from_user.id

    if user_status == "Обработка":
        await message.answer(text=PHRASES['request_processing'])
    elif user_status == "Одобрено":
        await message.answer(text=PHRASES['select_next'], reply_markup=kb)
    elif user_status == "Отклонено":
        last_request_time = get_last_request_time(user_id)
        retry_time = last_request_time + timedelta(days=RETRY_INTERVAL_DAYS)
        formatted_date = last_request_time.strftime("%d.%m.%Y года")
        if datetime.now() < retry_time:
            remaining_time = retry_time - datetime.now()
            days_word = decline_word(remaining_time.days, 'день')
            hours_word = decline_word(remaining_time.seconds // 3600, 'час')
            await message.answer(
                text=f"⛔ Ваша заявка была отклонена. Попробуйте повторно через {remaining_time.days} {days_word}, {remaining_time.seconds // 3600} {hours_word}."
            )
        else:
            await message.answer(text=f"⛔ Ваша заявка была отклонена {formatted_date}. Вы можете подать повторно заявку.", reply_markup=ra)
    else:
        await message.answer(text=PHRASES['hello_message'], reply_markup=kb_request)

@dp.message_handler(commands=['start'])
async def cmd_start(message: types.Message):
    user_id = message.from_user.id
    user_status = get_user_status(user_id)
    await handle_user_status(message, user_status)

@dp.message_handler(Text(equals=['📝 Подать заявку', '📝 Подать повторную заявку']))
async def request_step_1(message: types.Message):
    user_id = message.from_user.id
    user_status = get_user_status(user_id)
    
    if user_status in ("Обработка", "Отклонено"):
        if user_status == "Отклонено":
            last_request_time = get_last_request_time(user_id)
            retry_time = last_request_time + timedelta(days=RETRY_INTERVAL_DAYS)
            if datetime.now() > retry_time:
                await Request.name.set()
                await message.answer(text=PHRASES['enter_full_name'])
            else:
                await handle_user_status(message, user_status)
        else:
            await handle_user_status(message, user_status)
    elif user_status == "Одобрено":
        await message.answer(text=PHRASES['access_approved'])
    else:
        await Request.name.set()
        await message.answer(text=PHRASES['enter_full_name'])

@dp.message_handler(state=Request.name)
async def valid_request_send_name(message: types.Message, state: FSMContext):
    user_input = message.text.strip()
    sanitized_input = re.sub(r'[^a-zA-Zа-яА-ЯёЁ\s-]', '', user_input)
    if not sanitized_input:
        await message.answer(PHRASES['enter_full_name'])
        return
    words = sanitized_input.split()
    if len(words) < 3:
        await message.answer(PHRASES['oops'])
    elif len(words) > 3:
        await message.answer(PHRASES['dooble_fisrt_name'])
    else:
        if any(len(word) < 2 for word in words):
            await message.answer(PHRASES['min_words'])
        else:
            await state.update_data(name=sanitized_input)
            await request_step_2(message, state)

@dp.message_handler(state=Request.name)
async def request_step_2(message: types.Message, state: FSMContext):
    async with state.proxy() as data:
        data['name'] = message.text
    await Request.next()
    await message.answer(text='💼 Ваша должность?')

@dp.message_handler(Text(equals='Ввести запрос'))
async def phone_get(message: types.Message):
    await message.answer(text='Введите ФАМИЛИЮ или добавочный номер', reply_markup=kb_phonebook_search)
    await Form.name.set()

@dp.message_handler(state=Form.name)
async def search_xml_name(message: types.Message, state: FSMContext):
    if message.text in ("↪️ Назад", "/start", "/admin"):
        await state.finish()
        if message.text == "/start":
            await message.answer(text=PHRASES['select_next'], reply_markup=kb)
        elif message.text == "/admin":
            await message.answer(text=PHRASES['select_next'], reply_markup=admin_panel)
        else:
            await message.answer(text=PHRASES['select_next'], reply_markup=kb_phonebook)
        return

    async with state.proxy() as data:
        data['name'] = message.text
    await message.answer(text=get_unit_contact(data['name']))

@dp.message_handler(Text(equals='📖 Телефонная книга'))
async def open_phonebook(message: types.Message):
    user_id = message.from_user.id
    user_status = get_user_status(user_id)
    if user_status != "Отклонено":
        await message.answer(text=PHRASES['select_next'], reply_markup=kb_phonebook)

@dp.message_handler(Text(equals='Показать всех сотрудников'))
async def show_employee(message: types.Message):
    global page
    page = 0
    pages = (len(get_list_contact()[1]) + 9) // 10 
    ikb = InlineKeyboardMarkup(row_width=3)
    if page > 0:
        ikb.add(InlineKeyboardButton(text='Предыдущая', callback_data='phones.prev'))
    ikb.add(InlineKeyboardButton(text=f'{page + 1}/{pages}', callback_data='phones.page_info'))
    if page < pages - 1:
        ikb.add(InlineKeyboardButton(text='Следующая', callback_data='phones.next'))
    await bot.send_message(text="\n".join(getpagephones()[page]), chat_id=message.chat.id, reply_markup=ikb)

# Пагинация
@dp.callback_query_handler(lambda call: call.data.startswith('phones.'))
async def call_employee_process(call: types.CallbackQuery):
    pages = (len(get_list_contact()[1]) + 9) // 10 
    global page
    if call.data == "phones.next" and page < pages - 1:
        page += 1
    elif call.data == "phones.prev" and page > 0:
        page -= 1
    ikb = InlineKeyboardMarkup(row_width=3)
    if page > 0:
        ikb.add(InlineKeyboardButton(text='Предыдущая', callback_data='phones.prev'))
    ikb.add(InlineKeyboardButton(text=f'{page + 1}/{pages}', callback_data='phones.page_info'))
    if page < pages - 1:
        ikb.add(InlineKeyboardButton(text='Следующая', callback_data='phones.next'))
    await call.message.edit_text(text="\n".join(getpagephones()[page]), reply_markup=ikb)

@dp.message_handler(Text(equals='📄 Скачать EXEL'))
async def send_file(message: types.Message):
    await bot.send_document(message.chat.id, types.InputFile(PATH_XLSX_FILE))

@dp.message_handler(Text(equals='↪️ Назад'))
async def open_kb(message: types.Message):
    await message.answer(text='Телеграм бот компании "ООО БМУ ГЭМ".\nЧто вас интересует?', reply_markup=kb)

@dp.message_handler(Text(equals='💬 Открыть чат'))
async def open_chat_question(message: types.Message):
    user_id = message.from_user.id
    user_status = get_user_status(user_id)
    if user_status != "Отклонено":
        await message.answer("Нажмите на кнопку ниже, чтобы открыть чат:", reply_markup=kb_chat)

@dp.message_handler(state=Request.job)
async def process_final(message: types.Message, state: FSMContext):
    async with state.proxy() as data:
        data['job'] = message.text
        user_id = message.from_user.id
        username = message.from_user.username
        record(data['name'], data['job'], user_id, username)
        await message.answer(text='✅ Ваша заявка отправлена')
    await state.finish()
    markup = InlineKeyboardMarkup()
    markup.add(InlineKeyboardButton(text='✅ Одобрить', callback_data=f'request.approve_{user_id}'),
               InlineKeyboardButton(text='👁 Профиль', url=f'tg://user?id={user_id}'),
               InlineKeyboardButton(text='⛔ Отклонить', callback_data=f'request.refusal_{user_id}'))
    for admin_id in LIST_ADMIN_ID:
        await bot.send_message(admin_id, text='Заявка!\n' + getnotification(user_id), reply_markup=markup)

@dp.callback_query_handler(lambda call: call.data.startswith('request.approve'))
async def call_approve_process(call: types.CallbackQuery):
    await bot.edit_message_reply_markup(chat_id=call.from_user.id, message_id=call.message.message_id, reply_markup=None)
    user_id = call.data.split("_")
    if get_user_status(user_id[1]) == "Одобрено":
        print('Уже одобрена')
    else:
        setapprove(user_id[1])
        await bot.send_message(user_id[1], text='✅ Ваша заявка была принята!', reply_markup=kb)
        for admin_id in LIST_ADMIN_ID:
            await bot.send_message(admin_id, text='✅ Поступившая заявка была одобрена!')

@dp.callback_query_handler(lambda call: call.data.startswith('request.refusal'))
async def call_refusal_process(call: types.CallbackQuery):
    await bot.edit_message_reply_markup(chat_id=call.from_user.id, message_id=call.message.message_id, reply_markup=None)
    user_id = call.data.split("_")
    if get_user_status(user_id[1]) == "Отклонено":
        print('Уже отклонена')
    else:
        setrefusal(user_id[1])
        await bot.send_message(user_id[1], text='⛔ Ваша заявка была отклонена', reply_markup=ra)
        for admin_id in LIST_ADMIN_ID:
            await bot.send_message(admin_id, text='⛔ Поступившая заявка была отклонена!')

@dp.message_handler(Text(equals='Список заявок'))
async def show_employee(message: types.Message):
    await bot.send_message(text="\n".join(getpagerequests()[0]), chat_id=message.chat.id, reply_markup=ikb_request)

page_data = 0

@dp.callback_query_handler(lambda call: call.data.startswith('page.'))
async def call_data_process(call: types.CallbackQuery):
    pages_data = (float(len(getpagerequests())))
    global page_data
    if call.data == "page.next" and page_data < pages_data:
        page_data += 1
    if call.data == "page.prev" and page_data > -pages_data:
        page_data -= 1
    await call.message.edit_text(text="\n".join(getpagerequests()[page_data]), reply_markup=ikb_request)

@dp.message_handler(Text(equals='Просмотреть заявку'))
async def getuserid(message: types.Message):
    await Contact.id.set()
    await message.answer(text='Введите № заявки')

@dp.message_handler(state=Contact.id)
async def process_id(message: types.Message, state: FSMContext):
    async with state.proxy() as data:
        data['name'] = message.text
    await state.finish()
    markup = InlineKeyboardMarkup()
    markup.add(InlineKeyboardButton(text=f'👁 Профиль', url=f'tg://user?id={get_unit_record(data["name"])[0]}'))
    await message.answer(text=get_unit_record(data["name"])[1], reply_markup=markup)

@dp.message_handler()
async def handle_user_message(message: types.Message):
    user_id = message.from_user.id
    user_status = get_user_status(user_id)
    
    if user_status == "Отклонено":
        await cmd_start(message)
    else:
        await message.answer(f"Мы пока не хотим принимать простые сообщения 😶")

if __name__ == '__main__':
    loop = asyncio.get_event_loop()
    loop.create_task(scheduler())
    executor.start_polling(dp, skip_updates=True, on_startup=on_startup)
