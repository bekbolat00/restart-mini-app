import os
import logging
from pathlib import Path
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel
from aiogram import Bot, Dispatcher, types
from aiogram.types import Update, ReplyKeyboardMarkup, KeyboardButton, WebAppInfo, InlineKeyboardMarkup, InlineKeyboardButton

# Настройка логов для панели Vercel
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI()

# Берем токены из настроек Vercel (Environment Variables)
TOKEN = os.getenv("BOT_TOKEN")
URL = os.getenv("APP_URL", "").strip("/")

# Инициализация
bot = Bot(token=TOKEN) if TOKEN else None
dp = Dispatcher()
users_db = {}

class BookingData(BaseModel):
    user_id: int
    name: str
    date: str
    time: str

@app.get("/")
async def index():
    try:
        path = Path(__file__).parent / "index.html"
        return HTMLResponse(content=path.read_text(encoding="utf-8"))
    except Exception as e:
        return HTMLResponse(content=f"Error loading index.html: {e}", status_code=500)

@app.get("/health")
async def health():
    return {"status": "ok", "bot_token": bool(TOKEN), "url": URL}

@app.post("/api/webhook")
async def webhook(request: Request):
    if not bot: return {"error": "no token"}
    data = await request.json()
    update = Update(**data)
    await dp.feed_update(bot, update)
    return {"ok": True}

@dp.message(lambda m: m.text == "/start")
async def start(m: types.Message):
    kb = ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="📱 Регистрация", request_contact=True)]],
        resize_keyboard=True, one_time_keyboard=True
    )
    await m.answer(f"Привет, {m.from_user.first_name}! 🏥\nДля входа в клинику нажми кнопку ниже:", reply_markup=kb)

@dp.message(lambda m: m.contact is not None)
async def contact_done(m: types.Message):
    users_db[m.from_user.id] = m.contact.phone_number
    ikb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="🏥 Открыть клинику", web_app=WebAppInfo(url=URL))
    ]])
    await m.answer("✅ Готово! Теперь можно записываться:", reply_markup=ikb)

@app.post("/api/book")
async def book(data: BookingData):
    phone = users_db.get(data.user_id, "Номер не найден")
    logger.info(f"ЗАЯВКА: {data.name} | {phone} | {data.date} | {data.time}")
    return {"status": "success"}
