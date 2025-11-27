import asyncio
import os
from dataclasses import dataclass
from typing import List, Optional

from aiogram import Bot, Dispatcher, F, Router
from aiogram.enums import ParseMode
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    Message,
    ReplyKeyboardMarkup,
    KeyboardButton,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    CallbackQuery,
    Contact,
)


# =====================
# Basic configuration
# =====================

BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_USER_ID = int(os.getenv("ADMIN_USER_ID", "0"))

if not BOT_TOKEN or not ADMIN_USER_ID:
    raise RuntimeError("BOT_TOKEN and ADMIN_USER_ID must be set as environment variables")

# Cities
CITY_LABELS = ["Бенидорм", "Аликанте", "Кальпе", "Торревьеха", "Коммерческое"]
CITY_CODES = {
    "Бенидорм": "benidorm",
    "Аликанте": "alicante",
    "Кальпе": "calpe",
    "Торревьеха": "torrevieja",
    "Коммерческое": "commercial",
}

# Deal types
DEAL_TYPES = {
    "rent": "Аренда",
    "buy": "Покупка",
}

# Rooms
ROOMS = {
    "1": "1",
    "2": "2",
    "3+": "3+",
}


@dataclass
class Listing:
    id: str
    city_code: str
    deal_type: str  # "rent" / "buy"
    rooms: str      # "1" / "2" / "3+"
    title: str
    description: str
    link: str       # link to Telegram message or any URL


# For MVP we store listings in memory.
# Later this can be replaced with DB or external storage.
LISTINGS: List[Listing] = [
    Listing(
        id="ben_rent_1_1",
        city_code="benidorm",
        deal_type="rent",
        rooms="1",
        title="Бенидорм, 1 спальня, 600€/мес",
        description="Район Ринкон де Лойкс, 10 минут до моря, кондиционер, Wi-Fi.",
        link="https://t.me/your_benidorm_group/1",
    ),
    Listing(
        id="ben_rent_1_2",
        city_code="benidorm",
        deal_type="rent",
        rooms="1",
        title="Бенидорм, 1 спальня, 650€/мес",
        description="Центр, рядом со всеми сервисами, возможна регистрация.",
        link="https://t.me/your_benidorm_group/2",
    ),
    # Add your real listings here
]


def get_last_listings(city_code: str, deal_type: str, rooms: str, limit: int = 5) -> List[Listing]:
    filtered = [
        x for x in LISTINGS
        if x.city_code == city_code and x.deal_type == deal_type and x.rooms == rooms
    ]
    return filtered[-limit:]


def find_listing_by_id(listing_id: str) -> Optional[Listing]:
    for item in LISTINGS:
        if item.id == listing_id:
            return item
    return None


# =====================
# FSM states for application
# =====================

class ApplicationStates(StatesGroup):
    people = State()
    nationality = State()
    pets = State()
    income = State()
    period = State()
    viewing = State()
    contact = State()


router = Router()


# =====================
# Start and city selection
# =====================

@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext) -> None:
    await state.clear()

    city_keyboard = ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="Бенидорм"), KeyboardButton(text="Аликанте")],
            [KeyboardButton(text="Кальпе"), KeyboardButton(text="Торревьеха")],
            [KeyboardButton(text="Коммерческое")],
        ],
        resize_keyboard=True,
    )

    await message.answer(
        "Здравствуйте. Выберите город, в котором ищете объект:",
        reply_markup=city_keyboard,
    )


@router.message(F.text.in_(CITY_LABELS))
async def handle_city(message: Message, state: FSMContext) -> None:
    city_label = message.text
    city_code = CITY_CODES[city_label]

    await state.update_data(city_code=city_code)

    type_kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="Аренда", callback_data="type:rent"),
                InlineKeyboardButton(text="Покупка", callback_data="type:buy"),
            ]
        ]
    )

    await message.answer(
        f"Город: {city_label}. Выберите тип: аренда или покупка.",
        reply_markup=type_kb,
    )


# =====================
# Deal type and rooms selection
# =====================

@router.callback_query(F.data.startswith("type:"))
async def handle_type(callback: CallbackQuery, state: FSMContext) -> None:
    deal_type = callback.data.split(":", 1)[1]
    await state.update_data(deal_type=deal_type)

    rooms_kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="1", callback_data="rooms:1"),
                InlineKeyboardButton(text="2", callback_data="rooms:2"),
                InlineKeyboardButton(text="3+", callback_data="rooms:3+"),
            ]
        ]
    )

    await callback.message.answer(
        "Выберите количество комнат:",
        reply_markup=rooms_kb,
    )
    await callback.answer()


@router.callback_query(F.data.startswith("rooms:"))
async def handle_rooms(callback: CallbackQuery, state: FSMContext) -> None:
    rooms = callback.data.split(":", 1)[1]
    await state.update_data(rooms=rooms)

    data = await state.get_data()
    city_code = data["city_code"]
    deal_type = data["deal_type"]

    listings = get_last_listings(city_code=city_code, deal_type=deal_type, rooms=rooms, limit=5)

    if not listings:
        await callback.message.answer("К сожалению, по выбранным фильтрам объявлений пока нет.")
        await callback.answer()
        return

    for lst in listings:
        text = (
            f"<b>{lst.title}</b>\n\n"
            f"{lst.description}\n\n"
            f"Ссылка на объявление: {lst.link}"
        )
        contact_kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="Связаться по этому объекту",
                        callback_data=f"contact:{lst.id}",
                    )
                ]
            ]
        )
        await callback.message.answer(text, reply_markup=contact_kb)

    await callback.answer()


# =====================
# Application questionnaire
# =====================

@router.callback_query(F.data.startswith("contact:"))
async def start_application(callback: CallbackQuery, state: FSMContext) -> None:
    listing_id = callback.data.split(":", 1)[1]
    await state.update_data(listing_id=listing_id)

    people_kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="1", callback_data="people:1"),
                InlineKeyboardButton(text="2", callback_data="people:2"),
            ],
            [
                InlineKeyboardButton(text="3–4", callback_data="people:3-4"),
                InlineKeyboardButton(text="5+", callback_data="people:5+"),
            ],
        ]
    )

    await state.set_state(ApplicationStates.people)
    await callback.message.answer("Сколько человек будет проживать?", reply_markup=people_kb)
    await callback.answer()


@router.callback_query(ApplicationStates.people, F.data.startswith("people:"))
async def ask_nationality(callback: CallbackQuery, state: FSMContext) -> None:
    people_val = callback.data.split(":", 1)[1]
    await state.update_data(people=people_val)

    await state.set_state(ApplicationStates.nationality)
    await callback.message.answer("Укажите, пожалуйста, национальность (текстом):")
    await callback.answer()


@router.message(ApplicationStates.nationality)
async def ask_pets(message: Message, state: FSMContext) -> None:
    await state.update_data(nationality=message.text.strip())

    pets_kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="Да", callback_data="pets:yes"),
                InlineKeyboardButton(text="Нет", callback_data="pets:no"),
            ]
        ]
    )

    await state.set_state(ApplicationStates.pets)
    await message.answer("Есть ли животные?", reply_markup=pets_kb)


@router.callback_query(ApplicationStates.pets, F.data.startswith("pets:"))
async def ask_income(callback: CallbackQuery, state: FSMContext) -> None:
    pets_val = callback.data.split(":", 1)[1]
    await state.update_data(pets=pets_val)

    income_kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="Да", callback_data="income:yes"),
                InlineKeyboardButton(text="Нет", callback_data="income:no"),
                InlineKeyboardButton(text="Autónomo", callback_data="income:autonomo"),
            ]
        ]
    )

    await state.set_state(ApplicationStates.income)
    await callback.message.answer("Можете ли подтвердить доходы?", reply_markup=income_kb)
    await callback.answer()


@router.callback_query(ApplicationStates.income, F.data.startswith("income:"))
async def ask_period(callback: CallbackQuery, state: FSMContext) -> None:
    income_val = callback.data.split(":", 1)[1]
    await state.update_data(income=income_val)

    period_kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="3–6 месяцев", callback_data="period:3-6"),
                InlineKeyboardButton(text="6–12 месяцев", callback_data="period:6-12"),
            ],
            [
                InlineKeyboardButton(text="12+ месяцев", callback_data="period:12+"),
                InlineKeyboardButton(text="Иной вариант", callback_data="period:other"),
            ],
        ]
    )

    await state.set_state(ApplicationStates.period)
    await callback.message.answer("На какой срок планируете аренду?", reply_markup=period_kb)
    await callback.answer()


@router.callback_query(ApplicationStates.period, F.data.startswith("period:"))
async def ask_viewing(callback: CallbackQuery, state: FSMContext) -> None:
    period_val = callback.data.split(":", 1)[1]
    await state.update_data(period=period_val)

    await state.set_state(ApplicationStates.viewing)
    await callback.message.answer(
        "Укажите удобную дату и время просмотра (например: 15.12, после 17:00):"
    )
    await callback.answer()


@router.message(ApplicationStates.viewing)
async def ask_contact(message: Message, state: FSMContext) -> None:
    await state.update_data(viewing=message.text.strip())

    contact_kb = ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="Отправить контакт", request_contact=True)],
        ],
        resize_keyboard=True,
        one_time_keyboard=True,
    )

    await state.set_state(ApplicationStates.contact)
    await message.answer(
        "Отправьте, пожалуйста, номер телефона (кнопкой ниже или просто текстом):",
        reply_markup=contact_kb,
    )


@router.message(ApplicationStates.contact)
async def complete_application(message: Message, state: FSMContext, bot: Bot) -> None:
    phone: Optional[str] = None

    if message.contact and isinstance(message.contact, Contact):
        phone = message.contact.phone_number
    else:
        phone = message.text.strip() if message.text else ""

    await state.update_data(phone=phone)
    data = await state.get_data()

    listing = find_listing_by_id(data.get("listing_id", ""))
    if listing is None:
        listing_info = "Объявление не найдено (id потерян)."
        listing_link = "-"
    else:
        listing_info = listing.title
        listing_link = listing.link

    user = message.from_user
    username = f"@{user.username}" if user.username else f"id: {user.id}"

    text = (
        "Новая заявка по объекту:\n\n"
        f"Объявление: {listing_info}\n"
        f"Ссылка: {listing_link}\n\n"
        f"Сколько человек: {data.get('people')}\n"
        f"Национальность: {data.get('nationality')}\n"
        f"Животные: {data.get('pets')}\n"
        f"Подтверждение доходов: {data.get('income')}\n"
        f"Период аренды: {data.get('period')}\n"
        f"Дата/время просмотра: {data.get('viewing')}\n"
        f"Телефон: {data.get('phone')}\n\n"
        f"Пользователь: {username}"
    )

    await bot.send_message(chat_id=ADMIN_USER_ID, text=text)

    await message.answer(
        "Спасибо, заявка отправлена. Мы свяжемся с вами в ближайшее время."
    )

    await state.clear()


# =====================
# Bot start
# =====================

async def main() -> None:
    bot = Bot(token=BOT_TOKEN, parse_mode=ParseMode.HTML)
    dp = Dispatcher()
    dp.include_router(router)
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
