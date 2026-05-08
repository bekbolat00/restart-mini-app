from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
import os

app = FastAPI(title="ReStart Clinic Mini App")

# Раздаем наш HTML файл на главной странице
@app.get("/", response_class=HTMLResponse)
async def read_root():
    with open("index.html", "r", encoding="utf-8") as f:
        html_content = f.read()
    return html_content

# Здесь позже мы добавим роуты для приема данных из Telegram (Webhooks)
@app.post("/api/book")
async def book_appointment(data: dict):
    # Сюда Cursor позже напишет логику отправки в amoCRM
    return {"status": "success", "message": "Заявка принята"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)