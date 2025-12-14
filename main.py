import os
from fastapi import FastAPI, Request, Response, Form
from fastapi.responses import JSONResponse
from sqlalchemy import create_engine, Column, Integer, String, Boolean
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from openai import OpenAI
import json

# Переменные окружения
VK_CONFIRMATION_CODE = os.getenv("VK_CONFIRMATION_CODE")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
DATABASE_URL = os.getenv("DATABASE_URL")

# Инициализация OpenAI
client = OpenAI(api_key=OPENAI_API_KEY)

# База данных
engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(bind=engine)
Base = declarative_base()

class UserProgress(Base):
    __tablename__ = "user_progress"
    id = Column(Integer, primary_key=True, index=True)
    vk_id = Column(String, unique=True)
    level = Column(String)
    current_question = Column(Integer, default=0)

Base.metadata.create_all(bind=engine)

# Пример вопросов (UTF-8)
QUESTIONS = {
    "easy": ["Сколько будет 2+2?", "Сколько будет 3+5?"],
    "medium": ["Сколько будет 12*12?", "Сколько будет 15*3?"],
    "hard": ["Вычислите интеграл ∫ x^2 dx", "Решите уравнение x^2 - 5x + 6 = 0"]
}

# FastAPI приложение
app = FastAPI()

@app.get("/")
def root():
    return {"status": "OK"}

@app.post("/webhook")
async def vk_webhook(request: Request):
    data = await request.json()
    
    # Подтверждение Callback API
    if "type" in data and data["type"] == "confirmation":
        return Response(content=VK_CONFIRMATION_CODE, media_type="text/plain")
    
    if "type" in data and data["type"] == "message_new":
        vk_id = data["object"]["from_id"]
        text = data["object"]["text"].lower()
        
        session = SessionLocal()
        user = session.query(UserProgress).filter_by(vk_id=vk_id).first()
        if not user:
            user = UserProgress(vk_id=vk_id)
            session.add(user)
            session.commit()
        
        # Если пользователь написал "начать"
        if text == "начать":
            buttons = [
                {"action": {"type": "text", "label": "Easy"}, "color": "primary"},
                {"action": {"type": "text", "label": "Medium"}, "color": "primary"},
                {"action": {"type": "text", "label": "Hard"}, "color": "primary"}
            ]
            send_vk_message(vk_id, "Выберите уровень сложности:", buttons)
            session.close()
            return JSONResponse({"status": "ok"})
        
        # Выбор уровня сложности
        if text in ["easy", "medium", "hard"]:
            user.level = text
            user.current_question = 0
            session.commit()
            question = QUESTIONS[text][0]
            send_vk_message(vk_id, f"Вопрос 1: {question}")
            session.close()
            return JSONResponse({"status": "ok"})
        
        # Генерация ответа через OpenAI
        if user.level:
            question_index = user.current_question
            if question_index < len(QUESTIONS[user.level]):
                question_text = QUESTIONS[user.level][question_index]
                response_text = generate_answer(question_text)
                user.current_question += 1
                session.commit()
                send_vk_message(vk_id, f"Вопрос: {question_text}\nОтвет GPT: {response_text}")
            else:
                send_vk_message(vk_id, "Вопросы закончились!")
            session.close()
            return JSONResponse({"status": "ok"})
        
        session.close()
        return JSONResponse({"status": "ok"})

def send_vk_message(user_id: str, message: str, keyboard: list = None):
    import requests
    token = os.getenv("VK_GROUP_TOKEN")
    payload = {
        "user_id": user_id,
        "message": message,
        "random_id": 0
    }
    if keyboard:
        payload["keyboard"] = json.dumps({"one_time": True, "buttons": keyboard})
    requests.post("https://api.vk.com/method/messages.send", data=payload, params={"access_token": token, "v": "5.131"})

def generate_answer(question: str) -> str:
    resp = client.chat.completions.create(
        model="gpt-4",
        messages=[{"role": "user", "content": question}],
        max_tokens=150
    )
    return resp.choices[0].message.content.strip()
