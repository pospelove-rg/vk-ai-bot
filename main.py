# -*- coding: utf-8 -*-

import os
import json
import random
import requests
import psycopg2
from psycopg2 import OperationalError, DatabaseError

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

# Инициализация OpenAI клиента
client = OpenAI(api_key=OPENAI_API_KEY)

app = FastAPI()

# ================= DATABASE =================

def get_db():
    try:
        conn = psycopg2.connect(DATABASE_URL)
        conn.autocommit = True
        return conn, conn.cursor()
    except OperationalError as e:
        print(f"[DB ERROR] Connection failed: {e}")
        raise

def init_db():
    try:
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
    except DatabaseError as e:
        print(f"[DB ERROR] Init failed: {e}")
        raise

init_db()

# ================= OPENAI =================

def generate_question(level: str) -> str:
    """
    Генерация одного вопроса через OpenAI
    """
    try:
        prompt = f"Придумай один вопрос уровня сложности '{level}' для викторины. Без ответа."
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.7,
        )
        return response.choices[0].message.content.strip()
    except Exception as e:
        print(f"[OpenAI ERROR] Failed to generate question: {e}")
        return "Ошибка генерации вопроса. Попробуйте позже."

# ================= VK =================

def vk_send(user_id: int, text: str, keyboard: dict | None = None):
    try:
        payload = {
            "access_token": VK_TOKEN,
            "v": VK_API_VERSION,
            "user_id": user_id,
            "message": text,
            "random_id": random.randint(1, 2**31 - 1),
        }
        if keyboard:
            payload["keyboard"] = json.dumps(keyboard, ensure_ascii=False)
        response = requests.post(VK_API_URL, data=payload)
        if response.status_code != 200:
            print(f"[VK ERROR] Failed to send message: {response.text}")
    except Exception as e:
        print(f"[VK ERROR] Exception sending message: {e}")

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
    try:
        data = await request.json()
        print("VK webhook received:", data)

        if data.get("type") == "confirmation":
            return PlainTextResponse(VK_CONFIRMATION_CODE)

        if data.get("type") == "message_new":
            obj = data.get("object", {})
            message = obj.get("message", {})
            user_id = message.get("from_id")
            text = message.get("text", "").lower()

            if not user_id:
                print("[WARNING] from_id not found in message")
                return PlainTextResponse("ok")

            try:
                conn, cur = get_db()
            except Exception:
                vk_send(user_id, "Сервис временно недоступен. Попробуйте позже.")
                return PlainTextResponse("ok")

            try:
                # ----------------- ЛОГИКА -----------------
                if text in ("начать", "start"):
                    cur.execute(
                        "INSERT INTO user_progress (vk_user_id, level, question) VALUES (%s, %s, %s) "
                        "ON CONFLICT (vk_user_id) DO UPDATE SET level = NULL, question = NULL",
                        (user_id, None, None),
                    )
                    vk_send(user_id, "Выбери уровень сложности:", level_keyboard())

                elif text in ("лёгкий", "средний", "сложный"):
                    levels = {"лёгкий": "easy", "средний": "medium", "сложный": "hard"}
                    level = levels[text]
                    question = generate_question(level)
                    cur.execute(
                        "UPDATE user_progress SET level=%s, question=%s WHERE vk_user_id=%s",
                        (level, question, user_id),
                    )
                    vk_send(user_id, f"Вопрос:\n{question}", keyboard=main_keyboard())

                elif text in ("получить задание",):
                    cur.execute("SELECT level FROM user_progress WHERE vk_user_id=%s", (user_id,))
                    result = cur.fetchone()
                    if result and result[0]:
                        level = result[0]
                        question = generate_question(level)
                        cur.execute(
                            "UPDATE user_progress SET question=%s WHERE vk_user_id=%s",
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

            except Exception as e:
                print(f"[DB/Logic ERROR] {e}")
                vk_send(user_id, "Произошла ошибка обработки запроса. Попробуйте позже.")
            finally:
                cur.close()
                conn.close()

            return PlainTextResponse("ok")

        return PlainTextResponse("ok")

    except Exception as e:
        print(f"[WEBHOOK ERROR] {e}")
        return PlainTextResponse("ok")

# ================= HEALTHCHECK =================

@app.get("/")
def healthcheck():
    return {"status": "ok"}
