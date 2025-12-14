import os
import json
import random
import requests

from fastapi import FastAPI, Request
from sqlalchemy import (
    create_engine, Column, Integer, String
)
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker

from openai import OpenAI

# =====================
# НАСТРОЙКИ
# =====================

VK_TOKEN = os.getenv("VK_TOKEN")
VK_CONFIRMATION_TOKEN = os.getenv("VK_CONFIRMATION_TOKEN")
VK_API_VERSION = "5.199"

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
DATABASE_URL = os.getenv("DATABASE_URL")

NUM_QUESTIONS = 5

# =====================
# ИНИЦИАЛИЗАЦИЯ
# =====================

app = FastAPI()

engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(bind=engine)

Base = declarative_base()

openai_client = OpenAI(api_key=OPENAI_API_KEY)

# =====================
# МОДЕЛИ БД
# =====================

class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True)
    stage = Column(String, default="start")
    subject = Column(String)
    level = Column(String)
    current_question_index = Column(Integer, default=0)
    score = Column(Integer, default=0)


class Question(Base):
    __tablename__ = "questions"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, index=True)
    subject = Column(String)
    level = Column(String)
    question_text = Column(String)
    correct_answer = Column(String)
    explanation = Column(String)


Base.metadata.create_all(engine)

# =====================
# VK ВСПОМОГАТЕЛЬНОЕ
# =====================

def send_vk_message(user_id: int, text: str, keyboard: dict | None = None):
    payload = {
        "user_id": user_id,
        "message": text,
        "random_id": random.randint(1, 10**9),
        "access_token": VK_TOKEN,
        "v": VK_API_VERSION,
    }

    if keyboard:
        payload["keyboard"] = json.dumps(keyboard, ensure_ascii=False)

    requests.post(
        "https://api.vk.com/method/messages.send",
        data=payload
    )


start_keyboard = {
    "one_time": True,
    "buttons": [
        [
            {
                "action": {
                    "type": "text",
                    "label": "Начать"
                },
                "color": "primary"
            }
        ]
    ]
}

level_keyboard = {
    "one_time": True,
    "buttons": [
        [
            {"action": {"type": "text", "label": "Легкий"}, "color": "secondary"},
            {"action": {"type": "text", "label": "Средний"}, "color": "secondary"},
            {"action": {"type": "text", "label": "Сложный"}, "color": "secondary"},
        ]
    ]
}

# =====================
# OPENAI
# =====================

def generate_question(subject: str, level: str):
    prompt = f"""
Сгенерируй один вопрос по теме "{subject}".
Сложность: {level}.

Формат строго:
Вопрос: ...
Ответ: ...
Пояснение: ...
"""

    response = openai_client.chat.completions.create(
        model="gpt-4.1-mini",
        messages=[{"role": "user", "content": prompt}],
        temperature=0.7
    )

    content = response.choices[0].message.content

    lines = content.split("\n")
    question = lines[0].replace("Вопрос:", "").strip()
    answer = lines[1].replace("Ответ:", "").strip()
    explanation = lines[2].replace("Пояснение:", "").strip()

    return question, answer, explanation


def check_answer(user_answer: str, correct_answer: str) -> bool:
    return user_answer.strip().lower() == correct_answer.strip().lower()

# =====================
# WEBHOOK
# =====================

@app.post("/webhook")
async def vk_webhook(request: Request):
    data = await request.json()

    # Подтверждение сервера
    if data["type"] == "confirmation":
        return VK_CONFIRMATION_TOKEN

    if data["type"] != "message_new":
        return "ok"

    message = data["object"]["message"]
    user_id = message["from_id"]
    text = message.get("text", "").strip()

    session = SessionLocal()

    user = session.query(User).filter(User.id == user_id).first()
    if not user:
        user = User(id=user_id)
        session.add(user)
        session.commit()

    # =====================
    # ЛОГИКА БОТА
    # =====================

    if text.lower() == "начать":
        user.stage = "choose_level"
        session.commit()

        send_vk_message(
            user_id,
            "Выбери уровень сложности:",
            keyboard=level_keyboard
        )
        return "ok"

    if user.stage == "choose_level":
        if text not in ["Легкий", "Средний", "Сложный"]:
            send_vk_message(user_id, "Выбери уровень кнопкой.")
            return "ok"

        user.level = text
        user.subject = "Общие знания"
        user.current_question_index = 0
        user.score = 0
        user.stage = "quiz"
        session.commit()

        # удалить старые вопросы
        session.query(Question).filter(
            Question.user_id == user.id
        ).delete()
        session.commit()

        # создать новые вопросы
        for _ in range(NUM_QUESTIONS):
            q, a, exp = generate_question(user.subject, user.level)
            session.add(Question(
                user_id=user.id,
                subject=user.subject,
                level=user.level,
                question_text=q,
                correct_answer=a,
                explanation=exp
            ))

        session.commit()

        first_question = session.query(Question).filter(
            Question.user_id == user.id
        ).order_by(Question.id).first()

        send_vk_message(user_id, f"Вопрос 1:\n{first_question.question_text}")
        return "ok"

    if user.stage == "quiz":
        question = session.query(Question).filter(
            Question.user_id == user.id
        ).order_by(Question.id).offset(
            user.current_question_index
        ).first()

        if not question:
            send_vk_message(
                user_id,
                f"Тест завершён!\nРезультат: {user.score}/{NUM_QUESTIONS}",
                keyboard=start_keyboard
            )
            user.stage = "start"
            session.commit()
            return "ok"

        correct = check_answer(text, question.correct_answer)

        if correct:
            user.score += 1
            feedback = "Правильно!"
        else:
            feedback = f"Неправильно.\nПравильный ответ: {question.correct_answer}"

        user.current_question_index += 1
        session.commit()

        next_question = session.query(Question).filter(
            Question.user_id == user.id
        ).order_by(Question.id).offset(
            user.current_question_index
        ).first()

        if next_question:
            send_vk_message(
                user_id,
                f"{feedback}\n\nСледующий вопрос:\n{next_question.question_text}"
            )
        else:
            send_vk_message(
                user_id,
                f"{feedback}\n\nТест завершён!\nРезультат: {user.score}/{NUM_QUESTIONS}",
                keyboard=start_keyboard
            )
            user.stage = "start"
            session.commit()

    return "ok"
