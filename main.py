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
            level TEXT,
            question TEXT
        );
    """)
    cur.close()
    conn.close()

init_db()

# ================= OPENAI =================

def generate_question(level: str) -> str:
    prompt = f"Придумай один вопрос уровня сложности '{level}' для викторины. Без ответа."

    response = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[{"role": "user", "content": prompt}],
        temperature=0.7,
    )

    return response.choices[0].message.content.strip()

def generate_openai_response(prompt: str) -> str:
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

def level_keyboard():
    return {
        "inline": True,
        "buttons": [
            [{"action": {"type": "text", "label": "Лёгкий"}, "color": "positive"}],
            [{"action": {"type": "text", "label": "Средний"}, "color": "primary"}],
            [{"action": {"type": "text", "label": "Сложный"}, "color": "negative"}],
        ],
    }

def get_main_keyboard():
    return {
        "inline": False,
        "buttons": [
            [{"action": {"type": "text", "label": "Получить задание"}, "color": "primary"}],
            [{"action": {"type": "text", "label": "Помощь"}, "color": "secondary"}],
        ]
    }

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
        text = obj.get("text", "").lower()

        if user_id is None:
            print("Warning: from_id not found in object")
            return PlainTextResponse("ok")

        conn, cur = get_db()

        try:
            # Если пользователь только начинает
            if text in ("начать", "start"):
                cur.execute("""
                    INSERT INTO user_progress (vk_user_id, level, question)
                    VALUES (%s, %s, %s)
                    ON CONFLICT (vk_user_id) DO UPDATE SET level = NULL, question = NULL
                """, (user_id, None, None))
                vk_send(user_id, "Выбери уровень сложности:", keyboard=level_keyboard())
                return PlainTextResponse("ok")

            # Запрос задания
            elif "получить задание" in text or "задание" in text:
                cur.execute("SELECT level FROM user_progress WHERE vk_user_id=%s", (user_id,))
                result = cur.fetchone()
                level = result[0] if result else None

                if level is None:
                    vk_send(user_id, "Сначала выбери уровень сложности:", keyboard=level_keyboard())
                else:
                    question = generate_question(level)
                    cur.execute(
                        "UPDATE user_progress SET question=%s WHERE vk_user_id=%s",
                        (question, user_id)
                    )
                    vk_send(user_id, f"Вопрос:\n{question}", keyboard=get_main_keyboard())

                return PlainTextResponse("ok")

            # Запрос помощи
            elif "помощь" in text:
                help_text = "Я могу сгенерировать для тебя задание. Напиши 'Получить задание'."
                vk_send(user_id, help_text, keyboard=get_main_keyboard())
                return PlainTextResponse("ok")

            # Выбор уровня
            levels = {
                "лёгкий": "easy",
                "средний": "medium",
                "сложный": "hard",
            }
            if text in levels:
                level = levels[text]
                question = generate_question(level)
                cur.execute("""
                    INSERT INTO user_progress (vk_user_id, level, question)
                    VALUES (%s, %s, %s)
                    ON CONFLICT (vk_user_id) DO UPDATE SET level=%s, question=%s
                """, (user_id, level, question, level, question))
                vk_send(user_id, f"Вопрос:\n{question}", keyboard=get_main_keyboard())
                return PlainTextResponse("ok")

            # По умолчанию
            vk_send(user_id, "Напиши «Начать», чтобы начать игру.", keyboard=get_main_keyboard())
            return PlainTextResponse("ok")

        except Exception as e:
            print("[DB/Logic ERROR]", e)
            vk_send(user_id, "Произошла ошибка обработки запроса. Попробуйте позже.")
            return PlainTextResponse("ok")

        finally:
            cur.close()
            conn.close()

    # Для всех остальных событий
    return PlainTextResponse("ok")

@app.get("/")
def healthcheck():
    return {"status": "ok"}
