import os
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from aiogram import Bot, Dispatcher, Router
from aiogram.types import (
    Update, Message,
    ReplyKeyboardMarkup, KeyboardButton,
    ReplyKeyboardRemove,
    InlineKeyboardMarkup, InlineKeyboardButton,
    WebAppInfo,
)
from aiogram.filters import Command

BOT_TOKEN = os.getenv("BOT_TOKEN", "")
APP_URL = os.getenv("APP_URL", "https://your-app.vercel.app")

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()
router = Router()
dp.include_router(router)

# MVP: храним контакты пользователей в памяти { user_id: {phone, first_name, last_name} }
users_db: dict[int, dict] = {}


@asynccontextmanager
async def lifespan(app: FastAPI):
    webhook_url = f"{APP_URL}/api/webhook"
    await bot.set_webhook(webhook_url, drop_pending_updates=True)
    yield
    await bot.delete_webhook()
    await bot.session.close()


app = FastAPI(title="ReStart Clinic Mini App", lifespan=lifespan)


# ── Telegram bot handlers ──────────────────────────────────────────────────────

@router.message(Command("start"))
async def cmd_start(message: Message):
    keyboard = ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="📱 Поделиться контактом", request_contact=True)]],
        resize_keyboard=True,
        one_time_keyboard=True,
    )
    await message.answer(
        "Добро пожаловать в клинику ReStart! Для доступа к онлайн-записи авторизуйтесь",
        reply_markup=keyboard,
    )


@router.message(lambda m: m.contact is not None)
async def handle_contact(message: Message):
    contact = message.contact
    user_id = message.from_user.id

    users_db[user_id] = {
        "phone": contact.phone_number,
        "first_name": contact.first_name or "",
        "last_name": contact.last_name or "",
    }

    inline_kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(
            text="🏥 Открыть клинику",
            web_app=WebAppInfo(url=APP_URL),
        )
    ]])

    await message.answer(
        "Спасибо! Номер сохранён.",
        reply_markup=ReplyKeyboardRemove(),
    )
    await message.answer(
        "Теперь вы можете записаться онлайн:",
        reply_markup=inline_kb,
    )


# ── Pydantic models ────────────────────────────────────────────────────────────

class BookingData(BaseModel):
    user_id: int
    name: str
    date: str
    time: str


# ── FastAPI routes ─────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def read_root():
    with open("index.html", "r", encoding="utf-8") as f:
        return f.read()


@app.post("/api/webhook")
async def telegram_webhook(request: Request):
    data = await request.json()
    update = Update(**data)
    await dp.feed_update(bot, update)
    return {"ok": True}


@app.post("/api/book")
async def book_appointment(data: BookingData):
    user_info = users_db.get(data.user_id, {})
    phone = user_info.get("phone", "не указан")
    print(f"Новая бронь: {data.name}, {phone}, {data.date}, {data.time}")
    return {"status": "success"}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000)
