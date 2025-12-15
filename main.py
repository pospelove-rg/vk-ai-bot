import os
import json
import psycopg2
from fastapi import FastAPI, Request
from fastapi.responses import PlainTextResponse
from openai import OpenAI
import requests

# ================== CONFIG ==================

VK_TOKEN = os.getenv("VK_TOKEN")
VK_CONFIRMATION = os.getenv("VK_CONFIRMATION")

DB_HOST = os.getenv("DB_HOST")
DB_PORT = os.getenv("DB_PORT", "5432")
DB_USER = os.getenv("DB_USER")
DB_PASSWORD = os.getenv("DB_PASSWORD")
DB_NAME = os.getenv("DB_NAME")

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

client = OpenAI(api_key=OPENAI_API_KEY)

app = FastAPI()

# ================== DB ==================

def get_connection():
    return psycopg2.connect(
        host="dpg-d4v7f7npm1nc73bi9640-a.frankfurt-postgres.render.com",
        port="5432",
        user="vk_ai_bot_db_user",
        password="2nejvbVyY5yxTHLOGQCh3K7ylPyi5pwC",
        database="vk_ai_bot_db"
    )

# ================== VK SEND ==================

def vk_send(user_id: int, message: str, keyboard: dict | None = None):
    payload = {
        "user_id": user_id,
        "message": message,
        "random_id": 0,
        "access_token": VK_TOKEN,
        "v": "5.131"
    }
    if keyboard:
        payload["keyboard"] = json.dumps(keyboard, ensure_ascii=False)

    requests.post(
        "https://api.vk.com/method/messages.send",
        data=payload
    )

    print(f"[VK_SEND] to {user_id}: {message}")

# ================== KEYBOARDS ==================

def get_main_keyboard():
    return {
        "one_time": False,
        "buttons": [
            [{"action": {"type": "text", "label": "Начать"}, "color": "primary"}],
            [{"action": {"type": "text", "label": "Статистика"}, "color": "secondary"}]
        ]
    }

def get_game_keyboard():
    return {
        "one_time": False,
        "buttons": [
            [
                {"action": {"type": "text", "label": "Начать"}, "color": "primary"},
                {"action": {"type": "text", "label": "Сменить предмет"}, "color": "secondary"}
            ],
            [
                {"action": {"type": "text", "label": "Сменить экзамен"}, "color": "secondary"},
                {"action": {"type": "text", "label": "Статистика"}, "color": "secondary"}
            ]
        ]
    }


def get_exam_keyboard():
    return {
        "one_time": False,
        "buttons": [
            [{"action": {"type": "text", "label": "ОГЭ"}, "color": "primary"}],
            [{"action": {"type": "text", "label": "ЕГЭ"}, "color": "primary"}]
        ]
    }


def get_subject_keyboard(exam: str):
    subjects = {
        "ОГЭ": [
            "Математика", "Русский язык", "Английский язык", "Физика",
            "Химия", "Биология", "География", "История",
            "Обществознание", "Информатика"
        ],
        "ЕГЭ": [
            "Математика профиль", "Русский язык", "Английский язык", "Физика",
            "Химия", "Биология", "География", "История",
            "Обществознание", "Информатика"
        ]
    }

    buttons = []
    for s in subjects.get(exam, []):
        buttons.append([
            {"action": {"type": "text", "label": s}, "color": "secondary"}
        ])

    return {
        "one_time": False,
        "buttons": buttons
    }

# ================== OPENAI ==================

def generate_question(exam: str, subject: str):
    prompt = f"""
Ты экзаменатор {exam}.
Сформулируй ОДИН школьный вопрос по предмету "{subject}".
Без вариантов ответа.
"""

    r = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[{"role": "user", "content": prompt}]
    )
    return r.choices[0].message.content.strip()

def check_answer(question: str, user_answer: str):
    prompt = f"""
Вопрос:
{question}

Ответ ученика:
{user_answer}

Определи, правильный ли ответ.
Если неверный — объясни решение.
"""

    r = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[{"role": "user", "content": prompt}]
    )
    return r.choices[0].message.content.strip()

# ================== WEBHOOK ==================

@app.post("/webhook")
async def vk_webhook(request: Request):
    data = await request.json()

    if data["type"] == "confirmation":
        return PlainTextResponse(VK_CONFIRMATION)

    if data["type"] != "message_new":
        return PlainTextResponse("ok")

    msg = data["object"]["message"]
    user_id = msg["from_id"]
    text = msg.get("text", "").strip()
    text_lower = text.lower()
    text_upper = text.upper()

    print(f"[DEBUG] Пользователь {user_id} написал: {text}")

    COMMANDS = {
        "начать",
        "статистика",
        "сменить предмет",
        "сменить экзамен",
        "меню"
    }

    conn = get_connection()
    cur = conn.cursor()

    cur.execute(
        "SELECT exam, subject, question, waiting_for_answer FROM user_progress WHERE vk_user_id=%s",
        (user_id,)
    )
    row = cur.fetchone()

    # ===== НАЧАТЬ =====
    if text_lower == "начать":
        if not row:
            cur.execute(
                "INSERT INTO user_progress (vk_user_id) VALUES (%s)",
                (user_id,)
            )
            conn.commit()
            row = (None, None, None, False)

        if not row[0]:
            vk_send(user_id, "Выберите экзамен:", get_exam_keyboard())
            conn.close()
            return PlainTextResponse("ok")

        if not row[1]:
            vk_send(user_id, "Выберите предмет:", get_subject_keyboard(row[0]))
            conn.close()
            return PlainTextResponse("ok")

        question = generate_question(row[0], row[1])
        cur.execute(
            "UPDATE user_progress SET question=%s, waiting_for_answer=true WHERE vk_user_id=%s",
            (question, user_id)
        )
        conn.commit()
        vk_send(user_id, f"Вопрос:\n{question}", get_game_keyboard())
        conn.close()
        return PlainTextResponse("ok")

    # ===== ПРИВЕТ =====
    if text_lower in ("привет", "hello", "hi"):
        vk_send(user_id, "Привет! Я бот для подготовки к ОГЭ и ЕГЭ.", get_main_keyboard())
        conn.close()
        return PlainTextResponse("ok")

    # ===== СТАТИСТИКА =====
    if text_lower == "статистика":
        cur.execute(
            "SELECT COUNT(*) FROM user_progress WHERE vk_user_id=%s AND waiting_for_answer=false",
            (user_id,)
        )
        total = cur.fetchone()[0]
        vk_send(user_id, f"📊 Решено вопросов: {total}", get_game_keyboard())
        conn.close()
        return PlainTextResponse("ok")

    # ===== СМЕНА ПРЕДМЕТА =====
    if row and row[0] and not row[1]:
        cur.execute(
            "UPDATE user_progress SET subject=%s WHERE vk_user_id=%s",
            (text, user_id)
        )
        conn.commit()
        vk_send(user_id, "Нажмите «Начать» для получения вопроса", get_game_keyboard())
        conn.close()
        return PlainTextResponse("ok")


    # ===== СМЕНА ЭКЗАМЕНА =====
    if text_upper in ("ОГЭ", "ЕГЭ"):
        cur.execute(
            "UPDATE user_progress SET exam=%s, subject=NULL WHERE vk_user_id=%s",
            (text_upper, user_id)
        )
        conn.commit()
        vk_send(user_id, "Выберите предмет:", get_subject_keyboard(text_upper))
        conn.close()
        return PlainTextResponse("ok")

    # ===== ОТВЕТ НА ВОПРОС =====
    if row and row[3] and text_lower not in COMMANDS:
        explanation = check_answer(row[2], text)
        cur.execute(
            "UPDATE user_progress SET waiting_for_answer=false, question=NULL WHERE vk_user_id=%s",
            (user_id,)
        )
        conn.commit()
        vk_send(user_id, explanation, get_game_keyboard())
        conn.close()
        return PlainTextResponse("ok")

    # ===== ПО УМОЛЧАНИЮ =====
    vk_send(user_id, "Используйте кнопки или напишите «Начать».", get_main_keyboard())
    conn.close()
    return PlainTextResponse("ok")
