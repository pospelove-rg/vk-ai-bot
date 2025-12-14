import os
from fastapi import FastAPI, Request, Response
from pydantic import BaseModel
from openai import OpenAI
import httpx
from sqlalchemy import create_engine, Column, Integer, String, Boolean
from sqlalchemy.orm import sessionmaker, declarative_base

# -------------------
# Переменные окружения
# -------------------
VK_CONFIRMATION = os.getenv("VK_CONFIRMATION")
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
    level = Column(String)
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
    object: dict
    group_id: int
    secret: str
    event_id: str

@app.get("/")
async def root():
    return {"status": "ok"}

@app.get("/webhook")
async def verify():
    return Response(content=VK_CONFIRMATION, media_type="text/plain")

@app.post("/webhook")
async def webhook(update: VKUpdate):
    user_id = str(update.object.get("from_id") or update.object.get("user_id"))
    text = update.object.get("text", "").lower()

    session = SessionLocal()
    progress = session.query(UserProgress).filter_by(vk_user_id=user_id).first()
    if not progress:
        progress = UserProgress(vk_user_id=user_id, level="easy", question_index=0)
        session.add(progress)
        session.commit()

    # Вопросы
    questions = {
        "easy": ["Сколько будет 2+2?", "Сколько будет 3+5?"],
        "medium": ["Решите уравнение x+3=7", "Найдите корень уравнения 2x=10"],
        "hard": ["Вычислите интеграл ∫x dx", "Найдите производную x^2"]
    }

    level_questions = questions.get(progress.level, [])
    idx = progress.question_index

    if idx >= len(level_questions):
        response_text = "Вопросы закончились"
        progress.completed = True
    else:
        response_text = level_questions[idx]
        progress.question_index += 1

    session.commit()
    session.close()

    # Здесь нужно отправить сообщение в VK через API (requests или vk_api)
    print(f"Send to VK {user_id}: {response_text}")

    return {"status": "ok"}
