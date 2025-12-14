import os
import json
from fastapi import FastAPI, Request
from fastapi.responses import PlainTextResponse
from sqlalchemy import (
    create_engine, Column, Integer, String, Text
)
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from openai import OpenAI
import requests

# ================== ENV ==================

VK_CONFIRMATION_CODE = os.getenv("VK_CONFIRMATION_CODE")
VK_GROUP_TOKEN = os.getenv("VK_GROUP_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
DATABASE_URL = os.getenv("DATABASE_URL")

# ================== APP ==================

app = FastAPI()

# ================== DB ==================

engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(bind=engine)
Base = declarative_base()


class User(Base):
    __tablename__ = "users"

    vk_id = Column(Integer, primary_key=True)
    stage = Column(String, default="start")
    difficulty = Column(String, nullable=True)
    question_index = Column(Integer, default=0)


Base.metadata.create_all(bind=engine)

# ================== OPENAI ==================

client = OpenAI(api_key=OPENAI_API_KEY)

# ================== QUESTIONS ==================

QUESTIONS = {
    "easy": [
        "Сколько будет 2 + 2?",
        "Столица России?",
    ],
    "medium": [
        "Реши: 3x = 12",
        "Что такое фотосинтез?",
    ],
    "hard": [
        "Производная x²?",
        "Объясни второй закон Ньютона",
    ],
}

# ================== VK HELPERS ==================

def send_vk_message(user_id: int, text: str, keyboard: dict | None = None):
    payload = {
        "user_id": user_id,
        "message": text,
        "random_id": 0,
    }
    if keyboard:
        payload["keyboard"] = json.dumps(keyboard, ensure_ascii=False)

    requests.post(
        "https://api.vk.com/method/messages.send",
        params={
            "access_token": VK_GROUP_TOKEN,
            "v": "5.199",
        },
        json=payload,
        timeout=5,
    )


def start_keyboard():
    return {
        "one_time": True,
        "buttons": [
            [
                {
                    "action": {
                        "type": "text",
                        "label": "Начать",
                    },
                    "color": "primary",
                }
            ]
        ],
    }


def difficulty_keyboard():
    return {
        "one_time": True,
        "buttons": [
            [
                {"action": {"type": "text", "label": "Лёгкий"}, "color": "secondary"},
                {"action": {"type": "text", "label": "Средний"}, "color": "secondary"},
                {"action": {"type": "text", "label": "Сложный"}, "color": "secondary"},
            ]
        ],
    }

# ================== WEBHOOK ==================

@app.post("/webhook")
async def vk_webhook(request: Request):
    try:
        data = await request.json()

        # --- VK confirmation ---
        if data.get("type") == "confirmation":
            return PlainTextResponse(VK_CONFIRMATION_CODE)

        if data.get("type") != "message_new":
            return PlainTextResponse("ok")

        user_id = data["object"]["message"]["from_id"]
        text = data["object"]["message"].get("text", "").lower()

        db = SessionLocal()
        user = db.query(User).filter(User.vk_id == user_id).first()

        if not user:
            user = User(vk_id=user_id)
            db.add(user)
            db.commit()
            send_vk_message(
                user_id,
                "Привет! Я ИИ-тренажёр для подготовки к ОГЭ и ЕГЭ.",
                start_keyboard(),
            )
            return PlainTextResponse("ok")

        # ================== FLOW ==================

        if user.stage == "start":
            if "начать" in text:
                user.stage = "difficulty"
                db.commit()
                send_vk_message(user_id, "Выбери уровень сложности:", difficulty_keyboard())
            else:
                send_vk_message(user_id, "Нажми «Начать»", start_keyboard())

        elif user.stage == "difficulty":
            if "лёгк" in text:
                user.difficulty = "easy"
            elif "средн" in text:
                user.difficulty = "medium"
            elif "сложн" in text:
                user.difficulty = "hard"
            else:
                send_vk_message(user_id, "Выбери кнопкой 👇", difficulty_keyboard())
                return PlainTextResponse("ok")

            user.stage = "quiz"
            user.question_index = 0
            db.commit()

            send_vk_message(
                user_id,
                f"Начинаем! Вопрос 1:\n{QUESTIONS[user.difficulty][0]}"
            )

        elif user.stage == "quiz":
            user.question_index += 1

            if user.question_index >= len(QUESTIONS[user.difficulty]):
                user.stage = "start"
                user.difficulty = None
                user.question_index = 0
                db.commit()
                send_vk_message(user_id, "Вопросы закончились. Хочешь начать заново?", start_keyboard())
            else:
                db.commit()
                q = QUESTIONS[user.difficulty][user.question_index]
                send_vk_message(user_id, f"Следующий вопрос:\n{q}")

        db.close()
        return PlainTextResponse("ok")

    except Exception as e:
        print("Webhook error:", e)
        return PlainTextResponse("ok")


@app.get("/")
async def root():
    return {"status": "ok"}
