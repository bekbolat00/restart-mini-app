import os
import logging
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

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

BOT_TOKEN = os.getenv("BOT_TOKEN", "")
APP_URL = os.getenv("APP_URL", "https://your-app.vercel.app")

if not BOT_TOKEN:
    logger.warning("BOT_TOKEN is not set — bot functionality will be disabled.")

# Lazy globals; populated inside lifespan when token is available
bot: Bot | None = None
dp: Dispatcher | None = None

router = Router()

# MVP: store user contacts in memory { user_id: {phone, first_name, last_name} }
users_db: dict[int, dict] = {}


def _setup_dispatcher() -> Dispatcher:
    _dp = Dispatcher()
    _dp.include_router(router)
    return _dp


@asynccontextmanager
async def lifespan(app: FastAPI):
    global bot, dp
    if BOT_TOKEN:
        try:
            bot = Bot(token=BOT_TOKEN)
            dp = _setup_dispatcher()
            webhook_url = f"{APP_URL}/api/webhook"
            await bot.set_webhook(webhook_url, drop_pending_updates=True)
            logger.info("Webhook set to %s", webhook_url)
        except Exception as exc:
            logger.warning("Failed to initialize bot: %s", exc)
            bot = None
            dp = None
    yield
    if bot:
        try:
            await bot.delete_webhook()
            await bot.session.close()
        except Exception as exc:
            logger.warning("Error during bot shutdown: %s", exc)


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

    await message.answer("Спасибо! Номер сохранён.", reply_markup=ReplyKeyboardRemove())
    await message.answer("Теперь вы можете записаться онлайн:", reply_markup=inline_kb)


# ── Pydantic models ────────────────────────────────────────────────────────────

class BookingData(BaseModel):
    user_id: int
    name: str
    date: str
    time: str


# ── FastAPI routes ─────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def index():
    try:
        with open("index.html", "r", encoding="utf-8") as f:
            return f.read()
    except FileNotFoundError:
        return HTMLResponse("<h1>ReStart Clinic</h1>", status_code=200)


@app.post("/api/webhook")
async def telegram_webhook(request: Request):
    if bot is None or dp is None:
        logger.warning("Webhook received but bot is not initialized.")
        return {"ok": False, "error": "bot not initialized"}
    data = await request.json()
    update = Update(**data)
    await dp.feed_update(bot, update)
    return {"ok": True}


@app.post("/api/book")
async def book_appointment(data: BookingData):
    user_info = users_db.get(data.user_id, {})
    phone = user_info.get("phone", "не указан")
    logger.info("Новая бронь: %s, %s, %s, %s", data.name, phone, data.date, data.time)
    return {"status": "success"}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=False)
