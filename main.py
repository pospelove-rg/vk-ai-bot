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
        temperature=0.7
    )
    return response.choices[0].message.content.strip()

def check_answer(question: str, answer: str) -> str:
    """Проверка ответа и выдача пояснения"""
    prompt = f"Вопрос: {question}\nОтвет пользователя: {answer}\nСкажи, правильно или нет, и объясни кратко."
    response = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[{"role": "user", "content": prompt}],
        temperature=0.7
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
        "inline": True,
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
    return {"inline": True, "buttons": buttons}

def get_main_keyboard():
    return {
        "inline": True,
        "buttons": [
            [{"action": {"type": "text", "label": "Получить задание"}, "color": "primary"}],
            [{"action": {"type": "text", "label": "Помощь"}, "color": "secondary"}],
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
        message = obj.get("message", {})
        user_id = message.get("from_id")
        text = message.get("text", "").lower()

        if user_id is None:
            print("Warning: from_id not found in object")
            return PlainTextResponse("ok")

        print(f"[DEBUG] Пользователь {user_id} написал: {text}")

        conn, cur = get_db()
        cur.execute(
            "SELECT level, subject, question, waiting_for_answer FROM user_progress WHERE vk_user_id=%s",
            (user_id,)
        )
        row = cur.fetchone()
        if row:
            user_level, user_subject, current_question, waiting_for_answer = row
        else:
            user_level = user_subject = current_question = None
            waiting_for_answer = False

        # --- Начало игры ---
        if text in ("начать", "start") or not row:
            cur.execute("""
                INSERT INTO user_progress (vk_user_id, level, subject, question, waiting_for_answer)
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (vk_user_id) DO UPDATE
                SET level=NULL, subject=NULL, question=NULL, waiting_for_answer=FALSE
            """, (user_id, None, None, None, False))

            print(f"[DEBUG] Пользователь {user_id} начал игру. Отправляем выбор предмета.")
            vk_send(user_id, "Выбери предмет:", keyboard=subject_keyboard())

            cur.close()
            conn.close()
            return PlainTextResponse("ok")

        # --- Выбор предмета ---
        subjects = {
            "математика": "Математика",
            "русский язык": "Русский язык",
            "физика": "Физика",
            "химия": "Химия",
            "биология": "Биология",
            "история": "История",
            "обществознание": "Обществознание",
            "география": "География",
            "английский язык": "Английский язык",
            "информатика": "Информатика",
        }

        if text in subjects:
            subject = subjects[text]
            cur.execute("UPDATE user_progress SET subject=%s WHERE vk_user_id=%s", (subject, user_id))
            print(f"[DEBUG] Пользователь {user_id} выбрал предмет: {subject}")
            vk_send(user_id, "Выбери уровень сложности:", keyboard=level_keyboard())
            cur.close()
            conn.close()
            return PlainTextResponse("ok")

        # --- Выбор уровня сложности ---
        levels = {"лёгкий": "easy", "средний": "medium", "сложный": "hard"}
        if text in levels:
            if not user_subject:
                vk_send(user_id, "Сначала выбери предмет:", keyboard=subject_keyboard())
                cur.close()
                conn.close()
                return PlainTextResponse("ok")
            level = levels[text]
            question = generate_question(user_subject, level)
            cur.execute("""
                UPDATE user_progress SET level=%s, question=%s, waiting_for_answer=TRUE WHERE vk_user_id=%s
            """, (level, question, user_id))
            vk_send(user_id, f"Вопрос по {user_subject}:\n{question}")
            print(f"[DEBUG] Пользователь {user_id} получил вопрос: {question}")
            cur.close()
            conn.close()
            return PlainTextResponse("ok")

        # --- Ответ на вопрос ---
        if waiting_for_answer and current_question:
            cur.execute("UPDATE user_progress SET last_answer=%s WHERE vk_user_id=%s", (text, user_id))
            feedback = check_answer(current_question, text)
            vk_send(user_id, f"{feedback}\n\nСледующий вопрос:")

            new_question = generate_question(user_subject, user_level)
            cur.execute("""
                UPDATE user_progress SET question=%s, waiting_for_answer=TRUE WHERE vk_user_id=%s
            """, (new_question, user_id))
            vk_send(user_id, f"{new_question}")

            cur.close()
            conn.close()
            return PlainTextResponse("ok")

        # --- Основное меню ---
        if text in ("получить задание", "помощь"):
            if text == "получить задание":
                vk_send(user_id, "Выберите предмет сначала:", keyboard=subject_keyboard())
            else:
                vk_send(user_id, "Я могу сгенерировать для тебя задания по выбранному предмету и уровню сложности. Напиши 'Начать'.", keyboard=get_main_keyboard())
            cur.close()
            conn.close()
            return PlainTextResponse("ok")

        # --- По умолчанию ---
        vk_send(user_id, "Напиши «Начать», чтобы начать игру.", keyboard=get_main_keyboard())
        cur.close()
        conn.close()
        return PlainTextResponse("ok")

    return PlainTextResponse("ok")


@app.get("/")
def healthcheck():
    return {"status": "ok"}
