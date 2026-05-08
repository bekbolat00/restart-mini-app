import os
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from aiogram import Bot, Dispatcher, types
from aiogram.types import Update, ReplyKeyboardMarkup, KeyboardButton, WebAppInfo, InlineKeyboardMarkup, InlineKeyboardButton

app = FastAPI()

# Берем токены из Vercel
TOKEN = os.getenv("BOT_TOKEN")
URL = os.getenv("APP_URL")

bot = Bot(token=TOKEN)
dp = Dispatcher()
users_db = {} # Временная база

class BookingData(BaseModel):
    user_id: int
    name: str
    date: str
    time: str

@app.get("/")
async def index():
    return HTMLResponse(open("index.html").read())

@app.post("/api/webhook")
async def webhook(request: Request):
    data = await request.json()
    update = Update(**data)
    await dp.feed_update(bot, update)
    return {"ok": True}

@dp.message(lambda m: m.text == "/start")
async def start(m: types.Message):
    kb = ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="📱 Регистрация", request_contact=True)]], resize_keyboard=True)
    await m.answer("Добро пожаловать! Нажмите кнопку ниже, чтобы авторизоваться.", reply_markup=kb)

@dp.message(lambda m: m.contact is not None)
async def contact(m: types.Message):
    users_db[m.from_user.id] = m.contact.phone_number
    inline_kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🏥 Открыть клинику", web_app=WebAppInfo(url=URL))]])
    await m.answer("✅ Готово! Теперь вы можете записаться.", reply_markup=inline_kb)

@app.post("/api/book")
async def book(data: BookingData):
    phone = users_db.get(data.user_id, "Неизвестен")
    print(f"🔥 ЗАЯВКА: {data.name} | {phone} | {data.date} | {data.time}")
    return {"status": "success"}
