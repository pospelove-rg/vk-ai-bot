# -*- coding: utf-8 -*-

import os
import json
import random
import requests
import psycopg2

from fastapi import FastAPI, Request
from fastapi.responses import PlainTextResponse
from openai import OpenAI

# ================= CONFIG =================

VK_TOKEN = os.getenv("VK_TOKEN")
VK_CONFIRMATION_CODE = os.getenv("VK_CONFIRMATION_CODE")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
DATABASE_URL = os.getenv("DATABASE_URL")

VK_API_URL = "https://api.vk.com/method/messages.send"
VK_API_VERSION = "5.131"

client = OpenAI(api_key=OPENAI_API_KEY)

app = FastAPI()

# ================= DATABASE =================

def get_db():
    conn = psycopg2.connect(DATABASE_URL)
    conn.autocommit = True
    return conn, conn.cursor()

def init_db():
    conn, cur = get_db()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS user_progress (
            vk_user_id BIGINT PRIMARY KEY,
            subject TEXT,
            exam_type TEXT,
            topic TEXT,
            level TEXT,
            question TEXT,
            last_answer TEXT,
            waiting_for_answer BOOLEAN DEFAULT FALSE
        );
    """)
    cur.close()
    conn.close()

init_db()

# ================= OPENAI =================

def generate_question(subject: str, exam_type: str, topic: str, level: str) -> str:
    prompt = f"Придумай один вопрос по предмету {subject}, для экзамена {exam_type}, тему {topic}, уровень сложности {level}. Без ответа."

    response = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[{"role": "user", "content": prompt}],
        temperature=0.7,
    )

    return response.choices[0].message.content.strip()

def check_answer(question: str, user_answer: str) -> str:
    prompt = f"Проверь правильность ответа: Вопрос: {question}. Ответ пользователя: {user_answer}. Объясни правильно или нет."

    response = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[{"role": "user", "content": prompt}],
        temperature=0.7,
    )

    return response.choices[0].message.content.strip()

# ================= VK =================

def vk_send(user_id: int, text: str, keyboard: dict | None = None):
    payload = {
        "access_token": VK_TOKEN,
        "v": VK_API_VERSION,
        "user_id": user_id,
        "message": text,
        "random_id": random.randint(1, 10**9),
    }

    if keyboard:
        payload["keyboard"] = json.dumps(keyboard, ensure_ascii=False)

    requests.post(VK_API_URL, data=payload)

# ================= KEYBOARDS =================

def subject_keyboard():
    subjects = ["Математика", "Русский язык", "Физика", "Химия", "Биология", "История", "Обществознание", "Информатика", "География", "Английский язык"]
    buttons = [[{"action": {"type": "text", "label": subj}, "color": "primary"}] for subj in subjects]
    return {"inline": True, "buttons": buttons}

def exam_type_keyboard():
    exams = ["ОГЭ", "ЕГЭ"]
    buttons = [[{"action": {"type": "text", "label": ex}, "color": "primary"}] for ex in exams]
    return {"inline": True, "buttons": buttons}

def level_keyboard():
    return {
        "inline": True,
        "buttons": [
            [{"action": {"type": "text", "label": "Лёгкий"}, "color": "positive"}],
            [{"action": {"type": "text", "label": "Средний"}, "color": "primary"}],
            [{"action": {"type": "text", "label": "Сложный"}, "color": "negative"}],
        ],
    }

# ================= WEBHOOK =================

@app.post("/webhook")
async def vk_webhook(request: Request):
    data = await request.json()
    print("VK webhook received:", data)

    if data.get("type") == "confirmation":
        return PlainTextResponse(VK_CONFIRMATION_CODE)

    if data.get("type") == "message_new":
        obj = data.get("object", {})
        user_id = obj.get("from_id")
        text = obj.get("text", "").lower()

        if user_id is None:
            print("Warning: from_id not found in object")
            return PlainTextResponse("ok")

        conn, cur = get_db()

        # START
        if text in ("начать", "start"):
            cur.execute(
                "INSERT INTO user_progress (vk_user_id) VALUES (%s) ON CONFLICT (vk_user_id) DO NOTHING",
                (user_id,)
            )
            vk_send(user_id, "Выберите предмет для подготовки:", keyboard=subject_keyboard())
            cur.close()
            conn.close()
            return PlainTextResponse("ok")

        # SUBJECT SELECT
        cur.execute("SELECT subject, exam_type, topic, level, waiting_for_answer, question FROM user_progress WHERE vk_user_id=%s", (user_id,))
        row = cur.fetchone()

        subject, exam_type, topic, level, waiting_for_answer, question = (row or (None,)*6)

        if subject is None and text.title() in [s.title() for s in ["Математика", "Русский язык", "Физика", "Химия", "Биология", "История", "Обществознание", "Информатика", "География", "Английский язык"]]:
            subject = text.title()
            cur.execute("UPDATE user_progress SET subject=%s WHERE vk_user_id=%s", (subject, user_id))
            vk_send(user_id, "Выберите тип экзамена:", keyboard=exam_type_keyboard())
            cur.close()
            conn.close()
            return PlainTextResponse("ok")

        # EXAM TYPE SELECT
        if subject and exam_type is None and text.upper() in ["ОГЭ", "ЕГЭ"]:
            exam_type = text.upper()
            cur.execute("UPDATE user_progress SET exam_type=%s WHERE vk_user_id=%s", (exam_type, user_id))
            vk_send(user_id, f"Выберите тему по предмету {subject}:", keyboard=level_keyboard())  # можно заменить на темы конкретного предмета
            cur.close()
            conn.close()
            return PlainTextResponse("ok")

        # LEVEL SELECT и ГЕНЕРАЦИЯ ВОПРОСА (ШАГ 3)
        if subject and exam_type and text in ["лёгкий", "средний", "сложный"]:
            level_map = {"лёгкий": "easy", "средний": "medium", "сложный": "hard"}
            level = level_map[text]

            question = generate_question(subject, exam_type, topic or "Общие вопросы", level)
            cur.execute(
                "UPDATE user_progress SET level=%s, question=%s, waiting_for_answer=TRUE WHERE vk_user_id=%s",
                (level, question, user_id)
            )

            vk_send(user_id, f"Вопрос:
{question}")
            cur.close()
            conn.close()
            return PlainTextResponse("ok")

        # ПРОВЕРКА ОТВЕТА
        if waiting_for_answer and question:
            user_answer = text
            result = check_answer(question, user_answer)
            cur.execute(
                "UPDATE user_progress SET last_answer=%s, waiting_for_answer=FALSE WHERE vk_user_id=%s",
                (user_answer, user_id)
            )
            vk_send(user_id, f"{result}\nНапишите 'Начать' чтобы получить новый вопрос.")
            cur.close()
            conn.close()
            return PlainTextResponse("ok")

        # DEFAULT
        vk_send(user_id, "Выберите действие на клавиатуре.")
        cur.close()
        conn.close()
        return PlainTextResponse("ok")

    return PlainTextResponse("ok")

@app.get("/")
def healthcheck():
    return {"status": "ok"}
