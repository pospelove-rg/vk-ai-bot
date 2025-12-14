# -*- coding: utf-8 -*-

import os
import json
import random
import requests
import psycopg2

from fastapi import FastAPI, Request
from openai import OpenAI

# ================= CONFIG =================

VK_TOKEN = os.getenv("VK_TOKEN")
VK_CONFIRMATION_CODE = os.getenv("VK_CONFIRMATION_CODE")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
DATABASE_URL = os.getenv("DATABASE_URL")

VK_API_URL = "https://api.vk.com/method/messages.send"
VK_API_VERSION = "5.131"

client = OpenAI()

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
    prompt = f"Придумай один вопрос уровня сложности '{level}' для викторины. Без ответа."

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

# ================= WEBHOOK =================

@app.post("/webhook")
async def webhook(req: Request):
    data = await req.json()

    # VK confirmation
    if data.get("type") == "confirmation":
        return VK_CONFIRMATION_CODE

    if data.get("type") != "message_new":
        return "ok"

    message = data["object"]["message"]
    user_id = message["from_id"]
    text = message.get("text", "").lower()

    conn, cur = get_db()

    # START
    if text in ("начать", "start"):
        cur.execute(
            "INSERT INTO user_progress (user_id, level, question) VALUES (%s, %s, %s) "
            "ON CONFLICT (user_id) DO UPDATE SET level = NULL, question = NULL",
            (user_id, None, None),
        )
        vk_send(user_id, "Выбери уровень сложности:", level_keyboard())
        cur.close()
        conn.close()
        return "ok"

    # LEVEL SELECT
    levels = {
        "лёгкий": "easy",
        "средний": "medium",
        "сложный": "hard",
    }

    if text in levels:
        level = levels[text]
        question = generate_question(level)

        cur.execute(
            "UPDATE user_progress SET level=%s, question=%s WHERE user_id=%s",
            (level, question, user_id),
        )

        vk_send(user_id, f"Вопрос:\n{question}")
        cur.close()
        conn.close()
        return "ok"

    # DEFAULT
    vk_send(user_id, "Напиши «Начать», чтобы начать игру.")
    cur.close()
    conn.close()
    return "ok"


@app.get("/")
def healthcheck():
    return {"status": "ok"}
