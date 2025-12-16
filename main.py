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

    requests.post("https://api.vk.com/method/messages.send", data=payload)
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
        "ОГЭ": ["Математика","Русский язык","Английский язык","Физика","Химия","Биология","География","История","Обществознание","Информатика"],
        "ЕГЭ": ["Математика профиль","Русский язык","Английский язык","Физика","Химия","Биология","География","История","Обществознание","Информатика"]
    }
    return {
        "one_time": False,
        "buttons": [[{"action": {"type": "text", "label": s}, "color": "secondary"}] for s in subjects.get(exam, [])]
    }

def get_difficulty_keyboard():
    return {
        "one_time": False,
        "buttons": [
            [{"action": {"type": "text", "label": "Базовый"}, "color": "secondary"}],
            [{"action": {"type": "text", "label": "Средний"}, "color": "secondary"}],
            [{"action": {"type": "text", "label": "Сложный"}, "color": "secondary"}]
        ]
    }

def get_task_type_keyboard():
    return {
        "one_time": False,
        "buttons": [
            [{"action": {"type": "text", "label": "Теория"}, "color": "secondary"}],
            [{"action": {"type": "text", "label": "Тест"}, "color": "secondary"}],
            [{"action": {"type": "text", "label": "Практика"}, "color": "secondary"}]
        ]
    }

# ================== OPENAI ==================

def generate_question(exam, subject, difficulty=None, task_type=None):
    prompt = f"""
Ты экзаменатор {exam}.
Предмет: {subject}
Уровень сложности: {difficulty or "обычный"}
Тип задания: {task_type or "свободный"}

Сформулируй ОДИН экзаменационный вопрос.
"""
    r = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[{"role": "user", "content": prompt}]
    )
    return r.choices[0].message.content.strip()

def check_answer(question, answer):
    prompt = f"""
Вопрос:
{question}

Ответ ученика:
{answer}

Проверь ответ. Если неверно — объясни.
"""
    r = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[{"role": "user", "content": prompt}]
    )
    return r.choices[0].message.content.strip()

# ================== WEBHOOK ==================

COMMANDS = {
    "начать",
    "статистика",
    "сменить предмет",
    "сменить экзамен",
    "меню"
}

def is_answer(text: str) -> bool:
    if len(text.strip()) < 5:
        return False
    if text.lower() in COMMANDS:
        return False
    return True


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

    conn = get_connection()
    cur = conn.cursor()

    cur.execute("""
        SELECT exam, subject, question, waiting_for_answer, state, solved_count
        FROM user_progress
        WHERE vk_user_id=%s
    """, (user_id,))
    row = cur.fetchone()

    if not row:
        cur.execute("""
            INSERT INTO user_progress (vk_user_id, state)
            VALUES (%s, 'START')
        """, (user_id,))
        conn.commit()
        state = "START"
    else:
        state = row[4]

    # ===== ПРИВЕТ =====
    if text_lower in ("привет", "hello", "hi"):
        vk_send(user_id, "Привет! Я бот для подготовки к ОГЭ и ЕГЭ.", get_main_keyboard())
        conn.close()
        return PlainTextResponse("ok")

    # ===== СТАТИСТИКА =====
    if text_lower == "статистика":
        cur.execute(
            "SELECT solved_count FROM user_progress WHERE vk_user_id=%s",
            (user_id,)
        )
        solved = cur.fetchone()[0]
        vk_send(user_id, f"📊 Решено вопросов: {solved}", get_game_keyboard())
        conn.close()
        return PlainTextResponse("ok")

    # ===== СМЕНА ЭКЗАМЕНА =====
    if text_lower == "сменить экзамен":
        cur.execute("""
            UPDATE user_progress
            SET exam=NULL, subject=NULL, question=NULL,
                waiting_for_answer=false, state='SELECT_EXAM'
            WHERE vk_user_id=%s
        """, (user_id,))
        conn.commit()
        vk_send(user_id, "Выберите экзамен:", get_exam_keyboard())
        conn.close()
        return PlainTextResponse("ok")

    # ===== СМЕНА ПРЕДМЕТА =====
    if text_lower == "сменить предмет" and row and row[0]:
        cur.execute("""
            UPDATE user_progress
            SET subject=NULL, question=NULL,
                waiting_for_answer=false, state='SELECT_SUBJECT'
            WHERE vk_user_id=%s
        """, (user_id,))
        conn.commit()
        vk_send(user_id, "Выберите предмет:", get_subject_keyboard(row[0]))
        conn.close()
        return PlainTextResponse("ok")

    # ===== ВЫБОР ЭКЗАМЕНА =====
    if text_upper in ("ОГЭ", "ЕГЭ"):
        cur.execute("""
            UPDATE user_progress
            SET exam=%s, state='SELECT_SUBJECT'
            WHERE vk_user_id=%s
        """, (text_upper, user_id))
        conn.commit()
        vk_send(user_id, "Выберите предмет:", get_subject_keyboard(text_upper))
        conn.close()
        return PlainTextResponse("ok")

    # ===== ВЫБОР ПРЕДМЕТА =====
    if state == "SELECT_SUBJECT" and row and row[0]:
        cur.execute("""
            UPDATE user_progress
            SET subject=%s, state='IDLE'
            WHERE vk_user_id=%s
        """, (text, user_id))
        conn.commit()
        vk_send(user_id, "Предмет выбран. Нажмите «Начать».", get_game_keyboard())
        conn.close()
        return PlainTextResponse("ok")

    # ===== НАЧАТЬ =====
    if text_lower == "начать":
        if row and row[3]:
            vk_send(user_id, "Сначала ответьте на текущий вопрос.", get_game_keyboard())
            conn.close()
            return PlainTextResponse("ok")

        if not row or not row[0]:
            vk_send(user_id, "Выберите экзамен:", get_exam_keyboard())
            conn.close()
            return PlainTextResponse("ok")

        if not row[1]:
            vk_send(user_id, "Выберите предмет:", get_subject_keyboard(row[0]))
            conn.close()
            return PlainTextResponse("ok")

        question = generate_question(row[0], row[1])

        cur.execute("""
            UPDATE user_progress
            SET question=%s, waiting_for_answer=true, state='QUESTION'
            WHERE vk_user_id=%s
        """, (question, user_id))
        conn.commit()

        vk_send(user_id, f"Вопрос:\n{question}", get_game_keyboard())
        conn.close()
        return PlainTextResponse("ok")

    # ===== ОТВЕТ =====
    if state == "QUESTION" and is_answer(text):
        explanation = check_answer(row[2], text)

        cur.execute("""
            UPDATE user_progress
            SET waiting_for_answer=false,
                question=NULL,
                state='IDLE',
                solved_count = solved_count + 1
            WHERE vk_user_id=%s
        """, (user_id,))
        conn.commit()

        vk_send(user_id, explanation, get_game_keyboard())
        conn.close()
        return PlainTextResponse("ok")

    # ===== ПО УМОЛЧАНИЮ =====
    vk_send(user_id, "Используйте кнопки.", get_main_keyboard())
    conn.close()
    return PlainTextResponse("ok")

