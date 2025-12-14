# -*- coding: utf-8 -*-

import os
import json
import random
import requests
import psycopg2

from fastapi.responses import PlainTextResponse
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
            [{"action": {"type": "text", "label": "Р›С‘РіРєРёР№"}, "color": "positive"}],
            [{"action": {"type": "text", "label": "РЎСЂРµРґРЅРёР№"}, "color": "primary"}],
            [{"action": {"type": "text", "label": "РЎР»РѕР¶РЅС‹Р№"}, "color": "negative"}],
        ],
    }

# ================= WEBHOOK =================

@app.post("/webhook")
async def vk_webhook(request: Request):
    data = await request.json()

    # 1. Подтверждение сервера
    if data.get("type") == "confirmation":
        return PlainTextResponse(content=VK_CONFIRMATION_CODE, media_type="text/plain")

    # 2. Обработка новых сообщений
    if data.get("type") == "message_new":
        user_id = data["object"]["from_id"]
        text = data["object"]["text"].lower()

        if "задание" in text:
            task = generate_openai_response("Придумай короткое математическое задание для школьника")
            send_vk_message(user_id, task, keyboard=get_main_keyboard())
        elif "помощь" in text:
            help_text = "Я могу сгенерировать для тебя задание. Напиши 'Получить задание'."
            send_vk_message(user_id, help_text, keyboard=get_main_keyboard())
        else:
            send_vk_message(user_id, "Выбери действие на клавиатуре.", keyboard=get_main_keyboard())

        return PlainTextResponse("ok", media_type="text/plain")

    # 3. Для всех остальных событий
    return PlainTextResponse("ok", media_type="text/plain")


    # START
    if text in ("РЅР°С‡Р°С‚СЊ", "start"):
        cur.execute(
            "INSERT INTO user_progress (user_id, level, question) VALUES (%s, %s, %s) "
            "ON CONFLICT (user_id) DO UPDATE SET level = NULL, question = NULL",
            (user_id, None, None),
        )
        vk_send(user_id, "Р’С‹Р±РµСЂРё СѓСЂРѕРІРµРЅСЊ СЃР»РѕР¶РЅРѕСЃС‚Рё:", level_keyboard())
        cur.close()
        conn.close()
        return "ok"

    # LEVEL SELECT
    levels = {
        "Р»С‘РіРєРёР№": "easy",
        "СЃСЂРµРґРЅРёР№": "medium",
        "СЃР»РѕР¶РЅС‹Р№": "hard",
    }

    if text in levels:
        level = levels[text]
        question = generate_question(level)

        cur.execute(
            "UPDATE user_progress SET level=%s, question=%s WHERE user_id=%s",
            (level, question, user_id),
        )

        vk_send(user_id, f"Р’РѕРїСЂРѕСЃ:\n{question}")
        cur.close()
        conn.close()
        return "ok"

    # DEFAULT
    vk_send(user_id, "РќР°РїРёС€Рё В«РќР°С‡Р°С‚СЊВ», С‡С‚РѕР±С‹ РЅР°С‡Р°С‚СЊ РёРіСЂСѓ.")
    cur.close()
    conn.close()
    return "ok"


@app.get("/")
def healthcheck():
    return {"status": "ok"}
