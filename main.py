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

def generate_question(subject: str, level: str) -> str:
    prompt = f"Придумай один вопрос по предмету '{subject}' уровня сложности '{level}' для школьника. Без ответа."
    response = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[{"role": "user", "content": prompt}],
        temperature=0.7,
    )
    return response.choices[0].message.content.strip()

def check_answer(question: str, user_answer: str) -> str:
    prompt = (
        f"Вопрос: {question}\n"
        f"Ответ пользователя: {user_answer}\n"
        "Проверь, правильный ли ответ, и если нет, объясни коротко правильный."
    )
    response = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[{"role": "user", "content": prompt}],
        temperature=0,
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

def level_keyboard():
    return {
        "one_time": True,
        "buttons": [
            [{"action": {"type": "text", "label": "Лёгкий"}, "color": "positive"}],
            [{"action": {"type": "text", "label": "Средний"}, "color": "primary"}],
            [{"action": {"type": "text", "label": "Сложный"}, "color": "negative"}],
        ],
    }

def subject_keyboard():
    subjects = [
        "Математика", "Русский язык", "Физика", "Химия", "Биология",
        "История", "Обществознание", "География", "Английский язык", "Информатика"
    ]
    buttons = [[{"action": {"type": "text", "label": subj}, "color": "primary"}] for subj in subjects]
    return {"one_time": True, "buttons": buttons}

# ================= WEBHOOK =================

@app.post("/webhook")
async def vk_webhook(request: Request):
    data = await request.json()
    print("VK webhook received:", data)

    # Подтверждение сервера
    if data.get("type") == "confirmation":
        return PlainTextResponse(VK_CONFIRMATION_CODE)

    # Обработка новых сообщений
    if data.get("type") == "message_new":
        obj = data.get("object", {})
        user_id = obj.get("from_id")
        text = obj.get("message", {}).get("text", "").lower()

        if not user_id:
            print("Warning: from_id not found in object")
            return PlainTextResponse("ok")

        conn, cur = get_db()
        cur.execute("SELECT * FROM user_progress WHERE vk_user_id=%s", (user_id,))
        user = cur.fetchone()

        # Начало игры
        if text in ("начать", "start"):
            if not user:
                cur.execute(
                    "INSERT INTO user_progress (vk_user_id) VALUES (%s)",
                    (user_id,)
                )
            else:
                cur.execute(
                    "UPDATE user_progress SET subject=NULL, level=NULL, question=NULL, waiting_for_answer=FALSE WHERE vk_user_id=%s",
                    (user_id,)
                )
            vk_send(user_id, "Выберите предмет:", keyboard=subject_keyboard())
            cur.close()
            conn.close()
            return PlainTextResponse("ok")

        # Выбор предмета
        subjects = [
            "математика", "русский язык", "физика", "химия", "биология",
            "история", "обществознание", "география", "английский язык", "информатика"
        ]
        if text in subjects:
            cur.execute(
                "UPDATE user_progress SET subject=%s WHERE vk_user_id=%s",
                (text.title(), user_id)
            )
            vk_send(user_id, "Выберите уровень сложности:", keyboard=level_keyboard())
            cur.close()
            conn.close()
            return PlainTextResponse("ok")

        # Выбор уровня
        levels = {"лёгкий": "easy", "средний": "medium", "сложный": "hard"}
        if text in levels:
            cur.execute("SELECT subject FROM user_progress WHERE vk_user_id=%s", (user_id,))
            subject = cur.fetchone()[0]
            question = generate_question(subject, levels[text])
            cur.execute(
                "UPDATE user_progress SET level=%s, question=%s, waiting_for_answer=TRUE WHERE vk_user_id=%s",
                (levels[text], question, user_id)
            )
            vk_send(user_id, f"Вопрос:\n{question}")
            cur.close()
            conn.close()
            return PlainTextResponse("ok")

        # Проверка ответа
        if user and user[5]:  # waiting_for_answer == True
            cur.execute("SELECT question FROM user_progress WHERE vk_user_id=%s", (user_id,))
            question_text = cur.fetchone()[0]
            result = check_answer(question_text, text)
            vk_send(user_id, f"{result}\n\nНапишите 'Начать', чтобы получить новый вопрос.")
            cur.execute("UPDATE user_progress SET last_answer=%s, waiting_for_answer=FALSE WHERE vk_user_id=%s", (text, user_id))
            cur.close()
            conn.close()
            return PlainTextResponse("ok")

        # По умолчанию
        vk_send(user_id, "Напишите «Начать», чтобы начать игру.")
        cur.close()
        conn.close()
        return PlainTextResponse("ok")

    return PlainTextResponse("ok")


@app.get("/")
def healthcheck():
    return {"status": "ok"}
