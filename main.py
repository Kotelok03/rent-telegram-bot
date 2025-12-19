import asyncio
import os
from dataclasses import dataclass
from typing import List, Optional

import asyncpg

from aiogram import Bot, Dispatcher, F, Router
from aiogram.enums import ParseMode
from aiogram.client.default import DefaultBotProperties
from aiogram.filters import CommandStart, StateFilter
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
    ReplyKeyboardRemove,
)

# =====================
# Configuration
# =====================

BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_USER_ID = int(os.getenv("ADMIN_USER_ID", "0"))
DATABASE_URL = os.getenv("DATABASE_URL")

DOMIX_CHANNEL_ID = -1003445716247  # канал @domixcapital
NOTIFY_CHAT_ID = int(os.getenv("NOTIFY_CHAT_ID", "0"))  # рабочий чат для заявок (может быть 0)

if not BOT_TOKEN or not ADMIN_USER_ID:
    raise RuntimeError("BOT_TOKEN and ADMIN_USER_ID must be set as environment variables")

if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL must be set as environment variable")

db_pool: Optional[asyncpg.Pool] = None

# Cities
CITY_LABELS = ["Бенидорм", "Аликанте", "Кальпе", "Торревьеха", "Коммерческое"]
CITY_CODES = {
    "Бенидорм": "benidorm",
    "Аликанте": "alicante",
    "Кальпе": "calpe",
    "Торревьеха": "torrevieja",
    "Коммерческое": "commercial",
}
CITY_LABEL_BY_CODE = {v: k for k, v in CITY_CODES.items()}

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


# =====================
# Helper: main keyboard
# =====================

def build_main_keyboard(is_admin: bool) -> ReplyKeyboardMarkup:
    rows = [
        [KeyboardButton(text="Бенидорм"), KeyboardButton(text="Аликанте")],
        [KeyboardButton(text="Кальпе"), KeyboardButton(text="Торревьеха")],
        [KeyboardButton(text="Коммерческое")],
        [KeyboardButton(text="Перезапустить")],
    ]

    if is_admin:
        rows.append(
            [
                KeyboardButton(text="Добавить объявление"),
                KeyboardButton(text="Просмотреть список объявлений"),
            ]
        )

    return ReplyKeyboardMarkup(
        keyboard=rows,
        resize_keyboard=True,
    )


# =====================
# Data model & DB
# =====================

@dataclass
class Listing:
    id: str
    city_code: str
    deal_type: str
    rooms: str
    title: str
    description: str
    link: str


async def init_db() -> None:
    """Create connection pool and ensure table exists."""
    global db_pool
    db_pool = await asyncpg.create_pool(DATABASE_URL)
    async with db_pool.acquire() as conn:
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS listings (
                id SERIAL PRIMARY KEY,
                city_code TEXT NOT NULL,
                deal_type TEXT NOT NULL,
                rooms TEXT NOT NULL,
                title TEXT NOT NULL,
                description TEXT NOT NULL,
                link TEXT NOT NULL,
                is_active BOOLEAN NOT NULL DEFAULT TRUE
            )
            """
        )


async def db_get_last_listings(
    city_code: str, deal_type: str, rooms: str, limit: int = 5
) -> List[Listing]:
    """Return last listings for given filters."""
    if db_pool is None:
        raise RuntimeError("DB pool is not initialized")
    async with db_pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT id, city_code, deal_type, rooms, title, description, link
            FROM listings
            WHERE city_code = $1 AND deal_type = $2 AND rooms = $3 AND is_active = TRUE
            ORDER BY id DESC
            LIMIT $4
            """,
            city_code,
            deal_type,
            rooms,
            limit,
        )
    return [
        Listing(
            id=str(row["id"]),
            city_code=row["city_code"],
            deal_type=row["deal_type"],
            rooms=row["rooms"],
            title=row["title"],
            description=row["description"],
            link=row["link"],
        )
        for row in rows
    ]


async def db_get_last_listings_admin(limit: int = 20) -> List[Listing]:
    """Return last active listings for admin view."""
    if db_pool is None:
        raise RuntimeError("DB pool is not initialized")
    async with db_pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT id, city_code, deal_type, rooms, title, description, link
            FROM listings
            WHERE is_active = TRUE
            ORDER BY id DESC
            LIMIT $1
            """,
            limit,
        )
    return [
        Listing(
            id=str(row["id"]),
            city_code=row["city_code"],
            deal_type=row["deal_type"],
            rooms=row["rooms"],
            title=row["title"],
            description=row["description"],
            link=row["link"],
        )
        for row in rows
    ]


async def db_find_listing_by_id(listing_id: str) -> Optional[Listing]:
    """Find single listing by id."""
    if db_pool is None:
        raise RuntimeError("DB pool is not initialized")
    async with db_pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT id, city_code, deal_type, rooms, title, description, link
            FROM listings
            WHERE id = $1
            """,
            int(listing_id),
        )
    if not row:
        return None
    return Listing(
        id=str(row["id"]),
        city_code=row["city_code"],
        deal_type=row["deal_type"],
        rooms=row["rooms"],
        title=row["title"],
        description=row["description"],
        link=row["link"],
    )


async def db_insert_listing(data: dict) -> None:
    """Insert new listing from admin form."""
    if db_pool is None:
        raise RuntimeError("DB pool is not initialized")
    async with db_pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO listings (city_code, deal_type, rooms, title, description, link)
            VALUES ($1, $2, $3, $4, $5, $6)
            """,
            data["city_code"],
            data["deal_type"],
            data["rooms"],
            data["title"],
            data["description"],
            data["link"],
        )


async def db_deactivate_listing(listing_id: str) -> None:
    """Mark listing as inactive (hide from users)."""
    if db_pool is None:
        raise RuntimeError("DB pool is not initialized")
    async with db_pool.acquire() as conn:
        await conn.execute(
            """
            UPDATE listings
            SET is_active = FALSE
            WHERE id = $1
            """,
            int(listing_id),
        )


# =====================
# FSM states
# =====================

class ApplicationStates(StatesGroup):
    people = State()
    nationality = State()
    pets = State()
    income = State()
    period = State()
    viewing = State()
    contact = State()


class AdminAddListingStates(StatesGroup):
    city = State()
    deal_type = State()
    rooms = State()
    description = State()
    link = State()


router = Router()


# =====================
# User flow: search & application
# =====================

@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext) -> None:
    await state.clear()

    is_admin = message.from_user.id == ADMIN_USER_ID
    city_keyboard = build_main_keyboard(is_admin=is_admin)

    await message.answer(
        "Здравствуйте. Выберите город, в котором ищете объект:",
        reply_markup=city_keyboard,
    )


@router.message(F.text == "Перезапустить")
async def handle_restart_button(message: Message, state: FSMContext) -> None:
    await cmd_start(message, state)


@router.message(StateFilter(None), F.text.in_(CITY_LABELS))
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

    # НИЧЕГО не убираем, просто отправляем текст
    await message.answer(
        f"Город: {city_label}. Выберите тип: аренда или покупка."
    )
    await message.answer(
        "Выберите тип сделки:",
        reply_markup=type_kb,
    )



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

    listings = await db_get_last_listings(
        city_code=city_code, deal_type=deal_type, rooms=rooms, limit=5
    )

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

    listing = await db_find_listing_by_id(data.get("listing_id", ""))
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

    # 1) админу
    try:
        await bot.send_message(chat_id=ADMIN_USER_ID, text=text)
    except Exception as e:
        print(f"Ошибка отправки заявки админу: {e}")

    # 2) рабочий чат (если задан)
    if NOTIFY_CHAT_ID:
        try:
            await bot.send_message(
                chat_id=NOTIFY_CHAT_ID,
                text="Новая заявка (рабочий чат):\n\n" + text,
            )
        except Exception as e:
            print(f"Ошибка отправки заявки в рабочий чат: {e}")

    is_admin = message.from_user.id == ADMIN_USER_ID
    await message.answer(
        "Спасибо, заявка отправлена. Мы свяжемся с вами в ближайшее время.",
        reply_markup=build_main_keyboard(is_admin=is_admin),
    )

    await state.clear()


# =====================
# Admin flow: add listing (description + link)
# =====================

@router.message(F.text.in_(["/add_listing", "Добавить объявление"]))
async def admin_add_listing_start(message: Message, state: FSMContext) -> None:
    if message.from_user.id != ADMIN_USER_ID:
        return

    await state.set_state(AdminAddListingStates.city)
    await message.answer("Выберите город для нового объекта (кнопкой ниже):")


@router.message(AdminAddListingStates.city)
async def admin_set_city(message: Message, state: FSMContext) -> None:
    if message.text not in CITY_LABELS:
        await message.answer("Пожалуйста, выберите город из списка ниже.")
        return

    city_code = CITY_CODES[message.text]
    await state.update_data(city_code=city_code)

    type_kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="Аренда", callback_data="adm_type:rent"),
                InlineKeyboardButton(text="Покупка", callback_data="adm_type:buy"),
            ]
        ]
    )

    await state.set_state(AdminAddListingStates.deal_type)
    await message.answer("Выберите тип сделки:", reply_markup=type_kb)


@router.callback_query(AdminAddListingStates.deal_type, F.data.startswith("adm_type:"))
async def admin_set_deal_type(callback: CallbackQuery, state: FSMContext) -> None:
    deal_type = callback.data.split(":", 1)[1]
    await state.update_data(deal_type=deal_type)

    rooms_kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="1", callback_data="adm_rooms:1"),
                InlineKeyboardButton(text="2", callback_data="adm_rooms:2"),
                InlineKeyboardButton(text="3+", callback_data="adm_rooms:3+"),
            ]
        ]
    )

    await state.set_state(AdminAddListingStates.rooms)
    await callback.message.answer("Выберите количество комнат:", reply_markup=rooms_kb)
    await callback.answer()


@router.callback_query(AdminAddListingStates.rooms, F.data.startswith("adm_rooms:"))
async def admin_set_rooms(callback: CallbackQuery, state: FSMContext) -> None:
    rooms = callback.data.split(":", 1)[1]
    await state.update_data(rooms=rooms)

    await state.set_state(AdminAddListingStates.description)
    await callback.message.answer(
        "Введите полное описание объекта (любое количество текста):"
    )
    await callback.answer()


@router.message(AdminAddListingStates.description)
async def admin_set_description(message: Message, state: FSMContext) -> None:
    await state.update_data(description=message.text.strip())

    await state.set_state(AdminAddListingStates.link)
    await message.answer(
        "Отправьте ссылку на исходное объявление (канал/группа/сайт):"
    )


@router.message(AdminAddListingStates.link)
async def admin_save_listing(message: Message, state: FSMContext, bot: Bot) -> None:
    """Save listing (description + link) and send it to channel."""
    await state.update_data(link=message.text.strip())
    data = await state.get_data()

    # Auto title from first line of description
    desc = data["description"].strip()
    first_line = desc.split("\n", 1)[0]
    auto_title = first_line[:80] if first_line else "Объявление"

    save_data = {
        "city_code": data["city_code"],
        "deal_type": data["deal_type"],
        "rooms": data["rooms"],
        "title": auto_title,
        "description": desc,
        "link": data["link"],
    }

    # Save to DB
    try:
        await db_insert_listing(save_data)
    except Exception as e:
        print(f"Ошибка сохранения объявления в БД: {e}")
        is_admin = message.from_user.id == ADMIN_USER_ID
        await message.answer(
            "Произошла ошибка при сохранении объявления. Пожалуйста, сообщите разработчику.",
            reply_markup=build_main_keyboard(is_admin=is_admin),
        )
        await state.clear()
        return

    # Send to channel
    if DOMIX_CHANNEL_ID:
        city_label = CITY_LABEL_BY_CODE.get(save_data["city_code"], save_data["city_code"])
        deal_type_label = DEAL_TYPES.get(save_data["deal_type"], save_data["deal_type"])
        text = (
            "Новый объект:\n\n"
            f"Город: {city_label}\n"
            f"Тип: {deal_type_label}\n"
            f"Комнат: {save_data['rooms']}\n\n"
            f"{save_data['description']}\n\n"
            f"Ссылка: {save_data['link']}"
        )
        try:
            await bot.send_message(chat_id=DOMIX_CHANNEL_ID, text=text)
        except Exception as e:
            print(f"Ошибка отправки объекта в канал DOMIX: {e}")

    await state.clear()

    is_admin = message.from_user.id == ADMIN_USER_ID
    await message.answer(
        "Объект добавлен. Он будет показан пользователям по соответствующим фильтрам.",
        reply_markup=build_main_keyboard(is_admin=is_admin),
    )


# =====================
# Admin flow: list and delete listings
# =====================

@router.message(F.text.in_(["/list_listings", "Просмотреть список объявлений"]))
async def admin_list_listings(message: Message) -> None:
    """Show last active listings with delete buttons."""
    if message.from_user.id != ADMIN_USER_ID:
        return

    listings = await db_get_last_listings_admin(limit=20)
    if not listings:
        await message.answer("Активных объявлений сейчас нет.")
        return

    for lst in listings:
        city_label = CITY_LABEL_BY_CODE.get(lst.city_code, lst.city_code)
        deal_type_label = DEAL_TYPES.get(lst.deal_type, lst.deal_type)
        text = (
            f"ID: {lst.id}\n"
            f"Город: {city_label}\n"
            f"Тип: {deal_type_label}\n"
            f"Комнат: {lst.rooms}\n"
            f"Заголовок: {lst.title}\n"
            f"Ссылка: {lst.link}"
        )
        kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="Удалить из выдачи",
                        callback_data=f"adm_del:{lst.id}",
                    )
                ]
            ]
        )
        await message.answer(text, reply_markup=kb)


@router.callback_query(F.data.startswith("adm_del:"))
async def admin_delete_listing(callback: CallbackQuery) -> None:
    """Deactivate listing from admin interface."""
    if callback.from_user.id != ADMIN_USER_ID:
        await callback.answer()
        return

    listing_id = callback.data.split(":", 1)[1]
    await db_deactivate_listing(listing_id)

    await callback.message.edit_reply_markup(reply_markup=None)
    await callback.message.answer(f"Объявление ID {listing_id} удалено из выдачи.")
    await callback.answer("Объявление скрыто.")


# =====================
# Bot start
# =====================

async def main() -> None:
    await init_db()
    bot = Bot(
        token=BOT_TOKEN,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dp = Dispatcher()
    dp.include_router(router)
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
