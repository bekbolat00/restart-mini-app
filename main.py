import os
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone, timedelta
from pathlib import Path
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel
from typing import Optional
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.types import Update, InlineKeyboardMarkup, InlineKeyboardButton, WebAppInfo
from supabase import create_client, Client

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    if bot and URL:
        try:
            webhook_url = f"{URL}/api/webhook"
            await bot.set_webhook(webhook_url)
            logger.info(f"Webhook установлен: {webhook_url}")
        except Exception as e:
            logger.error(f"Ошибка установки webhook: {e}")
    yield
    try:
        if bot:
            await bot.session.close()
    except Exception:
        pass


app = FastAPI(lifespan=lifespan)

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
    appointment_iso: Optional[str] = None   # ISO-8601 UTC for reminder scheduling
    specialist: Optional[str] = None        # "Дежурный травматолог" etc.


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
    phone     = "Номер не найден"
    specialist = data.specialist or "Дежурный травматолог"

    if supabase:
        try:
            result = supabase.table("users").select("phone").eq("id", data.user_id).execute()
            if result.data:
                phone = result.data[0].get("phone", phone)
        except Exception as e:
            logger.error(f"book lookup error: {e}")

    logger.info(f"ЗАЯВКА: {data.name} | {phone} | {data.date} | {data.time}")

    # ── Persist booking for reminder scheduling ──────────────────────
    # Required Supabase table:
    #   bookings(id uuid default gen_random_uuid(), user_id bigint,
    #            name text, date text, time text, specialist text,
    #            appointment_datetime timestamptz, reminder_sent bool default false,
    #            created_at timestamptz default now())
    if supabase:
        try:
            row: dict = {
                "user_id":    data.user_id,
                "name":       data.name,
                "date":       data.date,
                "time":       data.time,
                "specialist": specialist,
                "reminder_sent": False,
            }
            if data.appointment_iso:
                row["appointment_datetime"] = data.appointment_iso
            supabase.table("bookings").insert(row).execute()
        except Exception as e:
            logger.error(f"book save error: {e}")

    # ── Notify admin ─────────────────────────────────────────────────
    if bot and ADMIN_ID:
        try:
            admin_text = (
                "🔔 <b>Новая заявка на прием!</b>\n\n"
                f"👤 <b>Имя:</b> {data.name}\n"
                f"📞 <b>Телефон:</b> {phone}\n"
                f"📅 <b>Дата:</b> {data.date}\n"
                f"⏰ <b>Время:</b> {data.time}\n"
                f"👨‍⚕️ <b>Специалист:</b> {specialist}"
            )
            await bot.send_message(chat_id=int(ADMIN_ID), text=admin_text, parse_mode="HTML")
        except Exception as e:
            logger.error(f"Ошибка отправки уведомления администратору: {e}")

    # ── Confirm to patient ───────────────────────────────────────────
    if bot:
        try:
            confirm_text = (
                "✅ <b>Запись подтверждена!</b>\n\n"
                f"📅 <b>Дата:</b> {data.date}\n"
                f"⏰ <b>Время:</b> {data.time}\n"
                f"👨‍⚕️ <b>Специалист:</b> {specialist}\n\n"
                "Мы напомним вам за 2 часа до приёма.\n"
                "До встречи в клинике ReStart! 🏥"
            )
            await bot.send_message(chat_id=data.user_id, text=confirm_text, parse_mode="HTML")
        except Exception as e:
            logger.error(f"Ошибка отправки подтверждения пациенту: {e}")

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


# ── Specialists API ────────────────────────────────────────────────────────────

@app.get("/api/specialists")
async def get_specialists():
    if not supabase:
        logger.error("get_specialists: Supabase not configured")
        return JSONResponse([])
    try:
        result = supabase.table("specialists").select("*").execute()
        return JSONResponse(result.data if result.data else [])
    except Exception as e:
        logger.error(f"get_specialists error: {e}")
        return JSONResponse([])


# ── Reminder cron ─────────────────────────────────────────────────
# Called by Vercel Cron (GET /api/send_reminders) every 30 minutes.
# Finds appointments whose datetime falls within the next 1h45m–2h15m window
# and sends a push notification to each patient who hasn't been reminded yet.

@app.get("/api/send_reminders")
async def send_reminders(request: Request):
    if not supabase or not bot:
        return JSONResponse({"error": "not configured"}, status_code=500)

    now          = datetime.now(timezone.utc)
    window_start = (now + timedelta(hours=1, minutes=45)).isoformat()
    window_end   = (now + timedelta(hours=2, minutes=15)).isoformat()

    try:
        result = (
            supabase.table("bookings")
            .select("*")
            .gte("appointment_datetime", window_start)
            .lte("appointment_datetime", window_end)
            .eq("reminder_sent", False)
            .execute()
        )
    except Exception as e:
        logger.error(f"send_reminders query error: {e}")
        return JSONResponse({"error": str(e)}, status_code=500)

    sent = 0
    for booking in result.data or []:
        uid  = booking.get("user_id")
        bid  = booking.get("id")
        time = booking.get("time", "")
        spec = booking.get("specialist", "Дежурный травматолог")
        try:
            reminder_text = (
                "⏰ <b>Напоминание о приёме</b>\n\n"
                f"Сегодня в <b>{time}</b> у вас приём в клинике ReStart.\n"
                f"👨‍⚕️ Специалист: {spec}\n\n"
                "Ждём вас! 🏥"
            )
            map_url     = "https://2gis.kz/almaty"
            contact_url = f"https://t.me/{URL.split('/')[-1]}" if URL else "https://t.me/restart_clinic"
            kb = InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text="🗺 Открыть маршрут",       url=map_url),
                InlineKeyboardButton(text="📞 Связаться с клиникой",  url=contact_url),
            ]])
            await bot.send_message(
                chat_id=int(uid),
                text=reminder_text,
                parse_mode="HTML",
                reply_markup=kb,
            )
            supabase.table("bookings").update({"reminder_sent": True}).eq("id", bid).execute()
            sent += 1
        except Exception as e:
            logger.error(f"Reminder failed for user {uid}: {e}")

    logger.info(f"Reminders dispatched: {sent}")
    return JSONResponse({"status": "ok", "sent": sent})


# ── Telegram webhook ──────────────────────────────────────────────────────────

@app.get("/api/set_webhook")
async def set_webhook():
    if not bot or not URL:
        return JSONResponse({"error": "bot or URL not configured"}, status_code=500)
    webhook_url = f"{URL}/api/webhook"
    await bot.set_webhook(webhook_url)
    info = await bot.get_webhook_info()
    return JSONResponse({"status": "ok", "webhook_url": info.url, "pending_updates": info.pending_update_count})


@app.get("/api/webhook_info")
async def webhook_info():
    if not bot:
        return JSONResponse({"error": "bot not configured"}, status_code=500)
    info = await bot.get_webhook_info()
    return JSONResponse({"url": info.url, "pending_updates": info.pending_update_count, "last_error": info.last_error_message})


@app.post("/api/webhook")
async def webhook(request: Request):
    if not bot:
        return {"error": "no token"}
    data = await request.json()
    update = Update(**data)
    await dp.feed_update(bot, update)
    return {"ok": True}


@dp.message(Command("start"))
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

    if ADMIN_ID:
        try:
            user = m.from_user
            username = f"@{user.username}" if user.username else "—"
            notify_text = (
                "👤 <b>Новый пользователь запустил бота!</b>\n\n"
                f"🆔 <b>ID:</b> <code>{user.id}</code>\n"
                f"📛 <b>Имя:</b> {user.full_name}\n"
                f"🔗 <b>Username:</b> {username}"
            )
            await bot.send_message(chat_id=int(ADMIN_ID), text=notify_text, parse_mode="HTML")
        except Exception as e:
            logger.error(f"Ошибка уведомления при /start: {e}")
