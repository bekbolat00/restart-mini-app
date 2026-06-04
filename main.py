import os
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone, timedelta
from pathlib import Path
import uuid
from fastapi import FastAPI, Request, File, UploadFile, Form
from fastapi.responses import HTMLResponse, JSONResponse, Response
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
ADMIN_ID          = os.getenv("ADMIN_ID")
MAX_SLOT_CAPACITY = int(os.getenv("SLOT_CAPACITY", "3"))  # max bookings per time slot

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

NO_CACHE_HEADERS = {
    "Cache-Control": "no-cache, no-store, must-revalidate",
    "Pragma": "no-cache",
    "Expires": "0",
}

@app.get("/")
async def index():
    try:
        path = Path(__file__).parent / "index.html"
        return HTMLResponse(content=path.read_text(encoding="utf-8"), headers=NO_CACHE_HEADERS)
    except Exception as e:
        return HTMLResponse(content=f"Error loading index.html: {e}", status_code=500)


@app.get("/health")
async def health():
    return {"status": "ok", "bot_token": bool(TOKEN), "url": URL, "supabase": bool(supabase)}


@app.get("/api/ics")
async def get_ics(date: str, time: str, specialist: str = "Дежурный травматолог"):
    """
    Serve an .ics file so iOS Safari shows the native «Add to Calendar» sheet.
    date: YYYY-MM-DD, time: HH:MM
    """
    try:
        from datetime import datetime
        dt_str = f"{date} {time}"
        start  = datetime.strptime(dt_str, "%Y-%m-%d %H:%M")
        end    = start + timedelta(hours=1)

        def fmt(d: datetime) -> str:
            return d.strftime("%Y%m%dT%H%M%S")

        uid = f"restart-{date}-{time.replace(':', '')}@restart.kz"
        lines = [
            "BEGIN:VCALENDAR",
            "VERSION:2.0",
            "PRODID:-//ReStart Clinic//RU",
            "CALSCALE:GREGORIAN",
            "METHOD:PUBLISH",
            "BEGIN:VEVENT",
            f"UID:{uid}",
            f"DTSTAMP:{fmt(datetime.utcnow())}",
            f"DTSTART:{fmt(start)}",
            f"DTEND:{fmt(end)}",
            f"SUMMARY:Консультация в клинике ReStart",
            f"DESCRIPTION:Консультация: {specialist}\\nКлиника спортивной медицины ReStart",
            "LOCATION:Клиника ReStart\\, Алматы",
            "STATUS:CONFIRMED",
            "BEGIN:VALARM",
            "TRIGGER:-PT2H",
            "ACTION:DISPLAY",
            "DESCRIPTION:Напоминание: консультация в клинике ReStart через 2 часа",
            "END:VALARM",
            "END:VEVENT",
            "END:VCALENDAR",
        ]
        ics_content = "\r\n".join(lines)
        return Response(
            content=ics_content,
            media_type="text/calendar; charset=utf-8",
            headers={
                "Content-Disposition": 'attachment; filename="restart-consultation.ics"',
                "Cache-Control": "no-cache",
            },
        )
    except Exception as e:
        logger.error(f"get_ics error: {e}")
        return JSONResponse({"error": str(e)}, status_code=400)


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

    # ── Reject past-time bookings ────────────────────────────────────
    if data.appointment_iso:
        try:
            appt_dt = datetime.fromisoformat(data.appointment_iso.replace("Z", "+00:00"))
            if appt_dt < datetime.now(timezone.utc):
                return JSONResponse(
                    {"status": "error", "message": "Нельзя записаться на прошедшее время."},
                    status_code=400
                )
        except ValueError:
            pass

    # ── Check slot capacity ──────────────────────────────────────────
    if supabase:
        try:
            existing = (
                supabase.table("bookings")
                .select("id")
                .eq("date", data.date)
                .eq("time", data.time)
                .execute()
            )
            if len(existing.data or []) >= MAX_SLOT_CAPACITY:
                return JSONResponse(
                    {"status": "error", "message": "Это время уже занято. Пожалуйста, выберите другой слот."},
                    status_code=409
                )
        except Exception as e:
            logger.error(f"book conflict check error: {e}")

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


# ── Slots API ─────────────────────────────────────────────────────

@app.get("/api/available_slots")
async def available_slots(date: str, user_id: Optional[int] = None):
    """Returns slot availability for a given date with counts, capacity, and user's booking."""
    empty = {"taken": [], "counts": {}, "capacity": MAX_SLOT_CAPACITY, "user_booked": None}
    if not supabase:
        return JSONResponse(empty)
    try:
        result = supabase.table("bookings").select("time, user_id").eq("date", date).execute()
        rows = result.data or []

        counts: dict[str, int] = {}
        user_booked: str | None = None
        for row in rows:
            t = row["time"]
            counts[t] = counts.get(t, 0) + 1
            if user_id and row.get("user_id") == user_id:
                user_booked = t

        taken = [t for t, c in counts.items() if c >= MAX_SLOT_CAPACITY]
        return JSONResponse({
            "taken": taken,
            "counts": counts,
            "capacity": MAX_SLOT_CAPACITY,
            "user_booked": user_booked,
        })
    except Exception as e:
        logger.error(f"available_slots error: {e}")
        return JSONResponse(empty)


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


# ── Medical Scans API ─────────────────────────────────────────────────────────
# Required Supabase setup:
#   1. Storage bucket: "medical-scans" (public)
#   2. Table: scans(
#        id uuid default gen_random_uuid() primary key,
#        user_id bigint not null,
#        scan_type text,          -- МРТ | Рентген | УЗИ | Анализы
#        body_part text,
#        scan_date date,
#        notes text,
#        file_path text,
#        file_url text,
#        file_name text,
#        created_at timestamptz default now()
#      )

@app.post("/api/upload_scan")
async def upload_scan(
    file: UploadFile = File(...),
    user_id: int = Form(...),
    scan_type: str = Form(...),
    body_part: str = Form(""),
    scan_date: str = Form(""),
    notes: str = Form(""),
):
    if not supabase:
        return JSONResponse({"status": "error", "message": "Supabase not configured"}, status_code=500)
    try:
        content  = await file.read()
        ext      = (file.filename or "scan").rsplit(".", 1)[-1].lower() if "." in (file.filename or "") else "jpg"
        allowed  = {"jpg", "jpeg", "png", "gif", "webp", "pdf", "bmp", "heic"}
        if ext not in allowed:
            return JSONResponse({"status": "error", "message": "Недопустимый формат файла"}, status_code=400)

        file_path = f"{user_id}/{uuid.uuid4()}.{ext}"
        content_type = file.content_type or "image/jpeg"

        supabase.storage.from_("medical-scans").upload(
            file_path, content, {"content-type": content_type}
        )
        public_url = supabase.storage.from_("medical-scans").get_public_url(file_path)

        row = {
            "user_id":   user_id,
            "scan_type": scan_type,
            "body_part": body_part,
            "scan_date": scan_date or datetime.now(timezone.utc).strftime("%Y-%m-%d"),
            "notes":     notes,
            "file_path": file_path,
            "file_url":  public_url,
            "file_name": file.filename or "scan",
        }
        result = supabase.table("scans").insert(row).execute()
        logger.info(f"SCAN UPLOADED: user={user_id} type={scan_type} path={file_path}")
        return JSONResponse({"status": "success", "scan": result.data[0] if result.data else row})
    except Exception as e:
        logger.error(f"upload_scan error: {e}")
        return JSONResponse({"status": "error", "message": str(e)}, status_code=500)


@app.get("/api/scans")
async def get_scans(user_id: int):
    if not supabase:
        return JSONResponse({"scans": []})
    try:
        result = (
            supabase.table("scans")
            .select("*")
            .eq("user_id", user_id)
            .order("created_at", desc=True)
            .execute()
        )
        return JSONResponse({"scans": result.data or []})
    except Exception as e:
        logger.error(f"get_scans error: {e}")
        return JSONResponse({"scans": []})


@app.delete("/api/scans/{scan_id}")
async def delete_scan(scan_id: str, user_id: int):
    if not supabase:
        return JSONResponse({"status": "error", "message": "Supabase not configured"}, status_code=500)
    try:
        result = (
            supabase.table("scans")
            .select("file_path")
            .eq("id", scan_id)
            .eq("user_id", user_id)
            .execute()
        )
        if not result.data:
            return JSONResponse({"status": "error", "message": "Снимок не найден"}, status_code=404)

        file_path = result.data[0].get("file_path")
        if file_path:
            try:
                supabase.storage.from_("medical-scans").remove([file_path])
            except Exception as se:
                logger.warning(f"Storage remove warning: {se}")

        supabase.table("scans").delete().eq("id", scan_id).eq("user_id", user_id).execute()
        logger.info(f"SCAN DELETED: id={scan_id} user={user_id}")
        return JSONResponse({"status": "success"})
    except Exception as e:
        logger.error(f"delete_scan error: {e}")
        return JSONResponse({"status": "error", "message": str(e)}, status_code=500)


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
