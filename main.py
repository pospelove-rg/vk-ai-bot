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

# Новый OpenAI клиент
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
            user_id BIGINT PRIMARY KEY,
            level TEXT,
            question TEXT
        );
    """)
    cur.close()
    conn.close()

init_db()

# ================= OPENAI =================

def generate_question(level: str) -> str:
    """
    Генерация одного вопроса по уровню сложности через OpenAI (новый API >=1.0.0)
    """
    prompt = f"Придумай один вопрос уровня сложности '{level}' для викторины. Без ответа."
    response = client.chat.completions.create(
        model="gpt-4o-mini",  # можно gpt-3.5-turbo
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
        "random_id": random.randint(1, 2**31 - 1),
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

def main_keyboard():
    return {
        "inline": True,
        "buttons": [
            [{"action": {"type": "text", "label": "Получить задание"}, "color": "positive"}],
            [{"action": {"type": "text", "label": "Помощь"}, "color": "primary"}],
        ],
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
        message = obj.get("message", {})
        user_id = message.get("from_id")
        text = message.get("text", "").lower()

        if not user_id:
            print("Warning: from_id not found")
            return PlainTextResponse("ok")

        conn, cur = get_db()

        # ----------- ЛОГИКА ----------------
        if text in ("начать", "start"):
            cur.execute(
                "INSERT INTO user_progress (user_id, level, question) VALUES (%s, %s, %s) "
                "ON CONFLICT (user_id) DO UPDATE SET level = NULL, question = NULL",
                (user_id, None, None),
            )
            vk_send(user_id, "Выбери уровень сложности:", level_keyboard())

        elif text in ("лёгкий", "средний", "сложный"):
            levels = {"лёгкий": "easy", "средний": "medium", "сложный": "hard"}
            level = levels[text]

            # Генерация первого вопроса
            question = generate_question(level)
            cur.execute(
                "UPDATE user_progress SET level=%s, question=%s WHERE user_id=%s",
                (level, question, user_id),
            )
            vk_send(user_id, f"Вопрос:\n{question}", keyboard=main_keyboard())

        elif text in ("получить задание",):
            # Получаем уровень пользователя
            cur.execute("SELECT level FROM user_progress WHERE user_id=%s", (user_id,))
            result = cur.fetchone()
            if result and result[0]:
                level = result[0]
                # Генерация нового вопроса
                question = generate_question(level)
                cur.execute(
                    "UPDATE user_progress SET question=%s WHERE user_id=%s",
                    (question, user_id),
                )
                vk_send(user_id, f"Твое новое задание:\n{question}", keyboard=main_keyboard())
            else:
                vk_send(user_id, "Сначала выбери уровень сложности командой «Начать».", keyboard=main_keyboard())

        elif text in ("помощь",):
            help_text = "Я могу сгенерировать для тебя задание. Напиши 'Начать', чтобы выбрать уровень сложности."
            vk_send(user_id, help_text, keyboard=main_keyboard())

        else:
            vk_send(user_id, "Выбери действие на клавиатуре.", keyboard=main_keyboard())

        cur.close()
        conn.close()
        return PlainTextResponse("ok")

    return PlainTextResponse("ok")

# ================= HEALTHCHECK =================

@app.get("/")
def healthcheck():
    return {"status": "ok"}
