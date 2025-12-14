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
    prompt = f"–ü—Ä–∏–¥—É–º–∞–π –æ–¥–∏–Ω –≤–æ–ø—Ä–æ—Å —É—Ä–æ–≤–Ω—è —Å–ª–æ–∂–Ω–æ—Å—Ç–∏ '{level}' –¥–ª—è –≤–∏–∫—Ç–æ—Ä–∏–Ω—ã. –ë–µ–∑ –æ—Ç–≤–µ—Ç–∞."

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
            [{"action": {"type": "text", "label": "–õ—ë–≥–∫–∏–π"}, "color": "positive"}],
            [{"action": {"type": "text", "label": "–°—Ä–µ–¥–Ω–∏–π"}, "color": "primary"}],
            [{"action": {"type": "text", "label": "–°–ª–æ–∂–Ω—ã–π"}, "color": "negative"}],
        ],
    }

# ================= WEBHOOK =================

@app.post("/webhook")
async def vk_webhook(request: Request):
    data = await request.json()

    # 1. èÆ§‚¢•‡¶§•≠®• ·•‡¢•‡†
    if data.get("type") == "confirmation":
        return PlainTextResponse(content=VK_CONFIRMATION_CODE, media_type="text/plain")

    # 2. é°‡†°Æ‚™† ≠Æ¢ÎÂ ·ÆÆ°È•≠®©
    if data.get("type") == "message_new":
        user_id = data["object"]["from_id"]
        text = data["object"]["text"].lower()

        if "ß†§†≠®•" in text:
            task = generate_openai_response("è‡®§„¨†© ™Æ‡Æ‚™Æ• ¨†‚•¨†‚®Á•·™Æ• ß†§†≠®• §´Ô Ë™Æ´Ï≠®™†")
            send_vk_message(user_id, task, keyboard=get_main_keyboard())
        elif "ØÆ¨ÆÈÏ" in text:
            help_text = "ü ¨Æ£„ ·£•≠•‡®‡Æ¢†‚Ï §´Ô ‚•°Ô ß†§†≠®•. ç†Ø®Ë® 'èÆ´„Á®‚Ï ß†§†≠®•'."
            send_vk_message(user_id, help_text, keyboard=get_main_keyboard())
        else:
            send_vk_message(user_id, "ÇÎ°•‡® §•©·‚¢®• ≠† ™´†¢®†‚„‡•.", keyboard=get_main_keyboard())

        return PlainTextResponse("ok", media_type="text/plain")

    # 3. Ñ´Ô ¢·•Â Æ·‚†´Ï≠ÎÂ ·Æ°Î‚®©
    return PlainTextResponse("ok", media_type="text/plain")

    # START
    if text in ("–Ω–∞—á–∞—Ç—å", "start"):
        cur.execute(
            "INSERT INTO user_progress (user_id, level, question) VALUES (%s, %s, %s) "
            "ON CONFLICT (user_id) DO UPDATE SET level = NULL, question = NULL",
            (user_id, None, None),
        )
        vk_send(user_id, "–í—ã–±–µ—Ä–∏ —É—Ä–æ–≤–µ–Ω—å —Å–ª–æ–∂–Ω–æ—Å—Ç–∏:", level_keyboard())
        cur.close()
        conn.close()
        return "ok"

    # LEVEL SELECT
    levels = {
        "–ª—ë–≥–∫–∏–π": "easy",
        "—Å—Ä–µ–¥–Ω–∏–π": "medium",
        "—Å–ª–æ–∂–Ω—ã–π": "hard",
    }

    if text in levels:
        level = levels[text]
        question = generate_question(level)

        cur.execute(
            "UPDATE user_progress SET level=%s, question=%s WHERE user_id=%s",
            (level, question, user_id),
        )

        vk_send(user_id, f"–í–æ–ø—Ä–æ—Å:\n{question}")
        cur.close()
        conn.close()
        return "ok"

    # DEFAULT
    vk_send(user_id, "–ù–∞–ø–∏—à–∏ ¬´–ù–∞—á–∞—Ç—å¬ª, —á—Ç–æ–±—ã –Ω–∞—á–∞—Ç—å –∏–≥—Ä—É.")
    cur.close()
    conn.close()
    return "ok"


@app.get("/")
def healthcheck():
    return {"status": "ok"}
