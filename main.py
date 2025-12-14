# -*- coding: utf-8 -*-

import os
import json
import random
import requests
import psycopg2
import openai

from fastapi import FastAPI, Request

# ----------------- CONFIG -----------------

VK_TOKEN = os.getenv("VK_TOKEN")
VK_CONFIRMATION_CODE = os.getenv("VK_CONFIRMATION_CODE")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
DATABASE_URL = os.getenv("DATABASE_URL")

openai.api_key = OPENAI_API_KEY

VK_API_URL = "https://api.vk.com/method/messages.send"
VK_API_VERSION = "5.131"

app = FastAPI()

# ----------------- DATABASE -----------------

conn = psycopg2.connect(DATABASE_URL)
conn.autocommit = True
cursor = conn.cursor()

cursor.execute("""
CREATE TABLE IF NOT EXISTS users (
    user_id BIGINT PRIMARY KEY,
    created_at TIMESTAMP DEFAULT NOW()
)
""")

cursor.execute("""
CREATE TABLE IF NOT EXISTS progress (
    user_id BIGINT PRIMARY KEY,
    level TEXT,
    step INT DEFAULT 0,
    updated_at TIMESTAMP DEFAULT NOW()
)
""")

# ----------------- VK HELPERS -----------------

def vk_send(user_id: int, text: str, keyboard: dict | None = None):
    payload = {
        "access_token": VK_TOKEN,
        "v": VK_API_VERSION,
        "user_id": user_id,
        "random_id": random.randint(1, 10**9),
        "message": text
    }
    if keyboard:
        payload["keyboard"] = json.dumps(keyboard, ensure_ascii=False)

    requests.post(VK_API_URL, data=payload)

def main_keyboard():
    return {
        "one_time": False,
        "buttons": [[
            {"action": {"type": "text", "label": "Лёгкий"}, "color": "positive"},
            {"action": {"type": "text", "label": "Средний"}, "color": "primary"},
            {"action": {"type": "text", "label": "Сложный"}, "color": "negative"}
        ]]
    }

# ----------------- OPENAI -----------------

def generate_task(level: str):
    prompt = f"""
Сгенерируй ОДНО задание уровня "{level}".
Формат:
Вопрос: ...
Ответ: ...
"""

    response = openai.ChatCompletion.create(
        model="gpt-3.5-turbo",
        messages=[{"role": "user", "content": prompt}],
        temperature=0.7
    )

    content = response.choices[0].message.content
    question, answer = content.split("Ответ:")
    return question.replace("Вопрос:", "").strip(), answer.strip()

# ----------------- WEBHOOK -----------------

@app.post("/webhook")
async def webhook(request: Request):
    data = await request.json()

    # VK confirmation
    if data["type"] == "confirmation":
        return VK_CONFIRMATION_CODE

    if data["type"] != "message_new":
        return "ok"

    obj = data["object"]["message"]
    user_id = obj["from_id"]
    text = obj.get("text", "").strip()

    # save user
    cursor.execute(
        "INSERT INTO users (user_id) VALUES (%s) ON CONFLICT DO NOTHING",
        (user_id,)
    )

    if text.lower() == "начать":
        vk_send(user_id, "Выбери уровень сложности:", main_keyboard())
        return "ok"

    if text in ("Лёгкий", "Средний", "Сложный"):
        cursor.execute("""
        INSERT INTO progress (user_id, level, step)
        VALUES (%s, %s, 0)
        ON CONFLICT (user_id) DO UPDATE
        SET level = EXCLUDED.level, step = 0
        """, (user_id, text))

        question, answer = generate_task(text)
        cursor.execute(
            "UPDATE progress SET step = step + 1 WHERE user_id = %s",
            (user_id,)
        )

        vk_send(user_id, f"Задание:\n{question}\n\nНапиши ответ.")
        cursor.execute(
            "UPDATE progress SET level = level || '||' || %s WHERE user_id = %s",
            (answer, user_id)
        )
        return "ok"

    # answer check
    cursor.execute(
        "SELECT level FROM progress WHERE user_id = %s",
        (user_id,)
    )
    row = cursor.fetchone()

    if row and "||" in row[0]:
        level, correct = row[0].split("||")
        if text.strip().lower() == correct.strip().lower():
            vk_send(user_id, "✅ Верно! Напиши «начать» для нового задания.")
        else:
            vk_send(user_id, f"❌ Неверно. Правильный ответ: {correct}")

        cursor.execute(
            "DELETE FROM progress WHERE user_id = %s",
            (user_id,)
        )

    return "ok"

@app.get("/")
def health():
    return {"status": "ok", "version": "v1.0"}
