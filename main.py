import os
import logging
from pathlib import Path
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel
from aiogram import Bot, Dispatcher, types
from aiogram.types import Update, InlineKeyboardMarkup, InlineKeyboardButton, WebAppInfo
from supabase import create_client, Client

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI()

TOKEN        = os.getenv("BOT_TOKEN")
URL          = os.getenv("APP_URL", "").strip("/")
SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "")
ADMIN_ID     = os.getenv("ADMIN_ID")

bot: Bot | None = Bot(token=TOKEN) if TOKEN else None
dp = Dispatcher()

supabase: Client | None = (
    create_client(SUPABASE_URL, SUPABASE_KEY)
    if SUPABASE_URL and SUPABASE_KEY else None
)


class RegisterData(BaseModel):
    id: int
    name: str
    phone: str


class BookingData(BaseModel):
    user_id: int
    name: str
    date: str
    time: str


# ── Static ────────────────────────────────────────────────────────────────────

@app.get("/")
async def index():
    try:
        path = Path(__file__).parent / "index.html"
        return HTMLResponse(content=path.read_text(encoding="utf-8"))
    except Exception as e:
        return HTMLResponse(content=f"Error loading index.html: {e}", status_code=500)


@app.get("/health")
async def health():
    return {"status": "ok", "bot_token": bool(TOKEN), "url": URL, "supabase": bool(supabase)}


# ── User API ──────────────────────────────────────────────────────────────────

@app.get("/api/check_user")
async def check_user(user_id: int):
    if not supabase:
        return JSONResponse({"exists": False, "error": "Supabase not configured"})
    try:
        result = supabase.table("users").select("id").eq("id", user_id).execute()
        return JSONResponse({"exists": len(result.data) > 0})
    except Exception as e:
        logger.error(f"check_user error: {e}")
        return JSONResponse({"exists": False, "error": str(e)}, status_code=500)


@app.post("/api/register")
async def register(data: RegisterData):
    if not supabase:
        return JSONResponse({"status": "error", "message": "Supabase not configured"}, status_code=500)
    try:
        supabase.table("users").upsert({
            "id": data.id,
            "name": data.name,
            "phone": data.phone,
        }).execute()
        logger.info(f"REGISTERED: {data.id} | {data.name} | {data.phone}")
        return JSONResponse({"status": "success"})
    except Exception as e:
        logger.error(f"register error: {e}")
        return JSONResponse({"status": "error", "message": str(e)}, status_code=500)


@app.post("/api/book")
async def book(data: BookingData):
    phone = "Номер не найден"
    if supabase:
        try:
            result = supabase.table("users").select("phone").eq("id", data.user_id).execute()
            if result.data:
                phone = result.data[0].get("phone", phone)
        except Exception as e:
            logger.error(f"book lookup error: {e}")
    logger.info(f"ЗАЯВКА: {data.name} | {phone} | {data.date} | {data.time}")

    if bot and ADMIN_ID:
        try:
            text_message = (
                "🔔 <b>Новая заявка на прием!</b>\n\n"
                f"👤 <b>Имя:</b> {data.name}\n"
                f"📞 <b>Телефон:</b> {phone}\n"
                f"📅 <b>Дата:</b> {data.date}\n"
                f"⏰ <b>Время:</b> {data.time}"
            )
            await bot.send_message(chat_id=int(ADMIN_ID), text=text_message, parse_mode="HTML")
        except Exception as e:
            logger.error(f"Ошибка отправки уведомления администратору: {e}")

    return {"status": "success"}


# ── Services API ──────────────────────────────────────────────────────────────

@app.get("/api/services")
async def get_services():
    if not supabase:
        logger.error("get_services: Supabase not configured")
        return JSONResponse([])
    try:
        result = supabase.table("services").select("*").execute()
        return JSONResponse(result.data if result.data else [])
    except Exception as e:
        logger.error(f"get_services error: {e}")
        return JSONResponse([])


# ── Telegram webhook ──────────────────────────────────────────────────────────

@app.post("/api/webhook")
async def webhook(request: Request):
    if not bot:
        return {"error": "no token"}
    data = await request.json()
    update = Update(**data)
    await dp.feed_update(bot, update)
    return {"ok": True}


@dp.message(lambda m: m.text == "/start")
async def start(m: types.Message):
    ikb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="🏥 Открыть ReStart", web_app=WebAppInfo(url=URL))
    ]])
    await m.answer(
        f"Привет, {m.from_user.first_name}! 👋\n\n"
        "Добро пожаловать в *ReStart* — клинику спортивной медицины и реабилитации.\n"
        "Нажми кнопку ниже, чтобы открыть приложение:",
        parse_mode="Markdown",
        reply_markup=ikb,
    )
