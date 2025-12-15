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
    print("VK webhook received:", data)  # Логируем данные для отладки

    # 1. Подтверждение сервера
    if data.get("type") == "confirmation":
        return PlainTextResponse(VK_CONFIRMATION_CODE)

    # 2. Обрабатываем только новые сообщения
    if data.get("type") != "message_new":
        return PlainTextResponse("ok")

    obj = data.get("object", {})
    message = obj.get("message", {})
    user_id = message.get("from_id")
    text = message.get("text", "").lower() if message.get("text") else ""

    # Игнорируем служебные события или сообщения от группы
    if user_id is None or user_id < 0:
        print("Warning: from_id not found or message from group")
        return PlainTextResponse("ok")

    print(f"[DEBUG] Пользователь {user_id} написал: {text}")

    # Подключение к базе
    conn, cur = get_db()

    # --- Шаг 1: Начало игры ---
    if text in ("начать", "start"):
        cur.execute(
            """
            INSERT INTO user_progress (vk_user_id, subject, level, question, last_answer, waiting_for_answer)
            VALUES (%s, %s, %s, %s, %s, %s)
            ON CONFLICT (vk_user_id) DO UPDATE
            SET subject=NULL, level=NULL, question=NULL, last_answer=NULL, waiting_for_answer=FALSE
            """,
            (user_id, None, None, None, None, False),
        )
        vk_send(user_id, "Выбери предмет:", subject_keyboard())  # клавиатура с предметами
        cur.close()
        conn.close()
        print(f"[DEBUG] Пользователь {user_id} начал игру. Отправляем выбор предмета.")
        return PlainTextResponse("ok")

    # --- Шаг 2: Выбор предмета ---
    subjects = ["Математика", "Физика", "Химия", "Биология", "История", 
                "Обществознание", "Русский язык", "Литература", "Английский", "Информатика"]

    if text.capitalize() in subjects:
        subject = text.capitalize()
        cur.execute(
            "UPDATE user_progress SET subject=%s WHERE vk_user_id=%s",
            (subject, user_id)
        )
        vk_send(user_id, f"Выбран предмет: {subject}. Выберите уровень сложности:", level_keyboard())
        cur.close()
        conn.close()
        return PlainTextResponse("ok")

    # --- Шаг 3: Выбор уровня ---
    levels = {"лёгкий": "easy", "средний": "medium", "сложный": "hard"}
    if text in levels:
        level = levels[text]
        cur.execute(
            "SELECT subject FROM user_progress WHERE vk_user_id=%s",
            (user_id,)
        )
        subject_row = cur.fetchone()
        subject = subject_row[0] if subject_row else "Математика"

        question = generate_question(level, subject)  # Шаг 3: генерируем вопрос по предмету и уровню
        cur.execute(
            "UPDATE user_progress SET level=%s, question=%s, waiting_for_answer=TRUE WHERE vk_user_id=%s",
            (level, question, user_id)
        )

        vk_send(user_id, f"Вопрос по {subject} ({text} уровень):\n{question}")
        cur.close()
        conn.close()
        return PlainTextResponse("ok")

    # --- Проверка ответа пользователя ---
    cur.execute(
        "SELECT question, waiting_for_answer, subject FROM user_progress WHERE vk_user_id=%s",
        (user_id,)
    )
    row = cur.fetchone()
    if row:
        question_text, waiting_for_answer, subject = row
        if waiting_for_answer:
            # Генерируем проверку ответа через OpenAI
            answer_feedback = check_answer(question_text, text, subject)
            cur.execute(
                "UPDATE user_progress SET last_answer=%s, waiting_for_answer=FALSE WHERE vk_user_id=%s",
                (text, user_id)
            )
            vk_send(user_id, answer_feedback + "\n\nНапишите «Начать», чтобы получить следующий вопрос.")
            cur.close()
            conn.close()
            return PlainTextResponse("ok")

    # --- Для всех остальных сообщений ---
    vk_send(user_id, "Выберите действие на клавиатуре или напишите «Начать», чтобы начать игру.", keyboard=get_main_keyboard())
    cur.close()
    conn.close()
    return PlainTextResponse("ok")


@app.get("/")
def healthcheck():
    return {"status": "ok"}
