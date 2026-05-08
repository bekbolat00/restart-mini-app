from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
import os

app = FastAPI(title="ReStart Clinic Mini App")


class BookingData(BaseModel):
    name: str
    date: str
    time: str


@app.get("/", response_class=HTMLResponse)
async def read_root():
    with open("index.html", "r", encoding="utf-8") as f:
        return f.read()


@app.post("/api/book")
async def book_appointment(data: BookingData):
    print(f"[НОВАЯ ЗАЯВКА] Имя: {data.name} | Дата: {data.date} | Время: {data.time}")
    return {"status": "success"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)