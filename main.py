import os
import json
import requests
from fastapi import FastAPI, Request, Response
from pydantic import BaseModel
from typing import Dict, Any
from openai import OpenAI
from sqlalchemy import create_engine, Column, Integer, String, Boolean
from sqlalchemy.orm import sessionmaker, declarative_base
import httpx

# -------------------
# Переменные окружения
# -------------------
VK_CONFIRMATION = os.getenv("VK_CONFIRMATION")
VK_GROUP_TOKEN = os.getenv("VK_GROUP_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
DATABASE_URL = os.getenv("DATABASE_URL")  # postgres://user:pass@host:port/dbname

# -------------------
# Настройка БД
# -------------------
Base = declarative_base()

class UserProgress(Base):
    __tablename__ = "user_progress"
    id = Column(Integer, primary_key=True)
    vk_user_id = Column(String, unique=True)
    level = Column(String, default="easy")
    question_index = Column(Integer, default=0)
    completed = Column(Boolean, default=False)

engine = create_engine(DATABASE_URL)
Base.metadata.create_all(engine)
SessionLocal = sessionmaker(bind=engine)

# -------------------
# Настройка OpenAI
# -------------------
http_client = httpx.Client(timeout=30.0)
client = OpenAI(api_key=OPENAI_API_KEY, http_client=http_client)

# -------------------
# FastAPI
# -------------------
app = FastAPI()

class VKUpdate(BaseModel):
    type: str
    object: Dict[str, Any]
    group_id: int
    secret: str
    event_id: str

# -------------------
# Клавиатура
# -------------------
START_KEYBOARD = {
    "one_time": True,
    "buttons": [[
        {"action": {"type": "text", "payload": {"command": "start"}, "label": "Начать"}, "color": "positive"}
    ]]
}

# -------------------
# Отправка сообщений в VK
# -------------------
def send_vk_message(user_id: str, text: str, keyboard: dict = None):
    payload = {
        "user_id": user_id,
        "message": text,
        "random_id": 0
    }
    if keyboard:
        payload["keyboard"] = json.dumps(keyboard)
    requests.post(
        "https://api.vk.com/method/messages.send",
        params={"access_token": VK_GROUP_TOKEN, "v": "5.131"},
        data=payload
    )

# -------------------
# Генерация вопроса через OpenAI
# -------------------
def generate_question(level: str) -> str:
    prompt = f"Сгенерируй простой учебный вопрос по математике для уровня {level}."
    response = client.chat.completions.create(
        model="gpt-3.5-turbo",
        messages=[{"role": "user", "content": prompt}],
        temperature=0.7,
        max_tokens=100
    )
    return response.choices[0].message.content.strip()

# -------------------
# Генерация ответа на вопрос
# -------------------
def generate_answer(question: str) -> str:
    prompt = f"Дай краткий правильный ответ на вопрос: {question}"
    response = client.chat.completions.create(
        model="gpt-3.5-turbo",
        messages=[{"role": "user", "content": prompt}],
        temperature=0.0,
        max_tokens=50
    )
    return response.choices[0].message.content.strip()

# -------------------
# Роуты
# -------------------
@app.get("/")
async def root():
    return {"status": "ok"}

@app.get("/webhook")
async def verify():
    return Response(content=VK_CONFIRMATION, media_type="text/plain")

@app.post("/webhook")
async def webhook(update: VKUpdate):
    obj = update.object
    user_id = str(obj.get("from_id") or obj.get("user_id"))
    text = obj.get("text", "").lower()

    # -------------------
    # Confirmation
    # -------------------
    if update.type == "confirmation":
        return VK_CONFIRMATION

    session = SessionLocal()
    progress = session.query(UserProgress).filter_by(vk_user_id=user_id).first()
    if not progress:
        progress = UserProgress(vk_user_id=user_id)
        session.add(progress)
        session.commit()

    # -------------------
    # Кнопка "Начать"
    # -------------------
    if obj.get("payload"):
        payload = obj["payload"]
        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except:
                payload = {}
        if payload.get("command") == "start":
            progress.question_index = 0
            progress.completed = False
            session.commit()
            send_vk_message(user_id, "Вы выбрали Начать!", START_KEYBOARD)
            session.close()
            return {"status": "ok"}

    # -------------------
    # Генерация нового вопроса через OpenAI
    # -------------------
    if progress.completed:
        send_vk_message(user_id, "Все вопросы закончились!")
        session.close()
        return {"status": "ok"}

    question = generate_question(progress.level)
    answer = generate_answer(question)

    send_vk_message(user_id, f"Вопрос: {question}\n\nОтвет (скрыт): {answer}")
    progress.question_index += 1
    session.commit()
    session.close()

    return {"status": "ok"}
