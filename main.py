import os
import asyncio
from fastapi import FastAPI, Request
from fastapi.responses import PlainTextResponse, JSONResponse
from sqlalchemy import create_engine, Column, Integer, String, Text
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from openai import OpenAI

# ------------------- Настройки -------------------
VK_CONFIRMATION = os.getenv("VK_CONFIRMATION")
VK_GROUP_TOKEN = os.getenv("VK_GROUP_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
DATABASE_URL = os.getenv("DATABASE_URL")

# ------------------- База данных -------------------
Base = declarative_base()
engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(bind=engine)

class UserProgress(Base):
    __tablename__ = "user_progress"
    id = Column(Integer, primary_key=True, index=True)
    vk_id = Column(String, unique=True, index=True)
    level = Column(String, default="easy")
    question_index = Column(Integer, default=0)

Base.metadata.create_all(bind=engine)

# ------------------- OpenAI -------------------
client = OpenAI(api_key=OPENAI_API_KEY)

# ------------------- FastAPI -------------------
app = FastAPI()

# Пример вопросов по уровням
QUESTIONS = {
    "easy": [
        "Сколько будет 2+2?",
        "Сколько будет 3+5?"
    ],
    "medium": [
        "Решите уравнение: 2x + 3 = 7",
        "Сколько будет 12 * 12?"
    ],
    "hard": [
        "Интеграл от x^2 dx?",
        "Решите систему: x + y = 10, x - y = 4"
    ]
}

# ------------------- Webhook -------------------
@app.get("/webhook")
async def vk_confirmation():
    # ВК проверяет GET запрос при подключении Callback API
    return PlainTextResponse(content=VK_CONFIRMATION)

@app.post("/webhook")
async def vk_webhook(request: Request):
    data = await request.json()
    if "type" not in data:
        return JSONResponse(content={"status": "ignored"})
    
    if data["type"] == "message_new":
        user_id = str(data["object"]["from_id"])
        text = data["object"]["text"]

        session = SessionLocal()
        user = session.query(UserProgress).filter_by(vk_id=user_id).first()
        if not user:
            user = UserProgress(vk_id=user_id)
            session.add(user)
            session.commit()

        level = user.level
        q_index = user.question_index

        # Проверяем, есть ли ещё вопросы
        if q_index >= len(QUESTIONS[level]):
            response_text = "Все вопросы закончились!"
        else:
            question_text = QUESTIONS[level][q_index]

            # Генерация ответа через OpenAI (пример)
            prompt = f"Сгенерируй объяснение и решение для ученика по вопросу: {question_text}"
            gpt_response = client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[{"role": "user", "content": prompt}],
                temperature=0.7
            )
            answer_text = gpt_response.choices[0].message.content

            response_text = f"Вопрос: {question_text}\nОтвет: {answer_text}"

            # Обновляем прогресс
            user.question_index += 1
            session.commit()

        session.close()

        # Отправка ответа через VK API
        import requests
        vk_url = "https://api.vk.com/method/messages.send"
        params = {
            "user_id": user_id,
            "message": response_text,
            "random_id": int.from_bytes(os.urandom(4), "big"),
            "access_token": VK_GROUP_TOKEN,
            "v": "5.131"
        }
        requests.post(vk_url, params=params)

    return JSONResponse(content={"status": "ok"})
