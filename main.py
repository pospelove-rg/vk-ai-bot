from fastapi import FastAPI, Request
from fastapi.responses import PlainTextResponse
import psycopg2
import json
import openai

# --- Настройки ---
VK_GROUP_TOKEN = "ВАШ_ТОКЕН_ГРУППЫ"
OPENAI_API_KEY = "ВАШ_OPENAI_KEY"

# --- OpenAI ---
openai.api_key = OPENAI_API_KEY

# --- FastAPI ---
app = FastAPI()

# --- Подключение к PostgreSQL ---
conn = psycopg2.connect(
    host="HOST_DB",
    port=5432,
    user="vk_ai_bot_db_user",
    password="ВАШ_ПАРОЛЬ",
    database="vk_ai_bot_db"
)
cur = conn.cursor()

# --- Словари предметов и экзаменов ---
EXAMS = ["ОГЭ", "ЕГЭ"]
SUBJECTS = {
    "Математика": ["Алгебра", "Геометрия", "Функции"],
    "Физика": ["Механика", "Оптика", "Электричество"],
    "Русский язык": ["Орфография", "Пунктуация", "Синтаксис"],
    "Химия": ["Неорганика", "Органика", "Физическая химия"]
}
LEVELS = ["easy", "medium", "hard"]

# --- Клавиатуры ---
def get_main_keyboard():
    return {
        "one_time": False,
        "buttons": [
            [{"action": {"type": "text", "label": "Начать"}, "color": "positive"}],
            [{"action": {"type": "text", "label": "Статистика"}, "color": "secondary"}]
        ]
    }

def get_exam_keyboard():
    return {
        "one_time": True,
        "buttons": [[{"action": {"type": "text", "label": exam}, "color": "primary"}] for exam in EXAMS]
    }

def get_subject_keyboard(exam):
    return {
        "one_time": True,
        "buttons": [[{"action": {"type": "text", "label": subj}, "color": "primary"}] for subj in SUBJECTS]
    }

def get_level_keyboard():
    return {
        "one_time": True,
        "buttons": [[{"action": {"type": "text", "label": lvl}, "color": "primary"}] for lvl in LEVELS]
    }

# --- VK API send function ---
def vk_send(user_id, text, keyboard=None):
    import requests
    data = {
        "user_id": user_id,
        "message": text,
        "random_id": 0
    }
    if keyboard:
        data["keyboard"] = json.dumps(keyboard)
    requests.post(f"https://api.vk.com/method/messages.send?access_token={VK_GROUP_TOKEN}&v=5.131", data=data)

# --- Генерация вопросов через OpenAI ---
def generate_question(subject, level):
    prompt = f"Придумай короткий вопрос по предмету {subject} уровня {level} для школьника."
    response = openai.ChatCompletion.create(
        model="gpt-3.5-turbo",
        messages=[{"role": "user", "content": prompt}],
        max_tokens=150
    )
    return response.choices[0].message.content.strip()

def check_answer(question, user_answer):
    prompt = f"Вопрос: {question}\nОтвет пользователя: {user_answer}\nСкажи, правильный ли ответ и дай краткое объяснение."
    response = openai.ChatCompletion.create(
        model="gpt-3.5-turbo",
        messages=[{"role": "user", "content": prompt}],
        max_tokens=150
    )
    return response.choices[0].message.content.strip()

# --- VK Webhook ---
@app.post("/webhook")
async def vk_webhook(req: Request):
    body = await req.json()
    event_type = body.get("type")
    obj = body.get("object", {})

    if event_type == "confirmation":
        return PlainTextResponse("ВАШ_CONFIRMATION_CODE")

    if event_type == "message_new":
        msg = obj.get("message", {})
        user_id = msg.get("from_id")
        text = msg.get("text", "").lower().strip()

        if not user_id:
            return PlainTextResponse("ok")

        # --- Обработка команд ---
        cur.execute("SELECT exam, subject, level, question, waiting_for_answer FROM user_progress WHERE vk_user_id=%s", (user_id,))
        row = cur.fetchone()

        if text == "начать":
            # Сбросим прогресс
            cur.execute("INSERT INTO user_progress (vk_user_id, waiting_for_answer) VALUES (%s, FALSE) ON CONFLICT (vk_user_id) DO UPDATE SET waiting_for_answer=FALSE", (user_id,))
            conn.commit()
            vk_send(user_id, "Выберите экзамен:", keyboard=get_exam_keyboard())
        elif text in EXAMS:
            cur.execute("UPDATE user_progress SET exam=%s WHERE vk_user_id=%s", (text, user_id))
            conn.commit()
            vk_send(user_id, f"Выберите предмет для {text}:", keyboard=get_subject_keyboard(text))
        elif text in SUBJECTS:
            cur.execute("UPDATE user_progress SET subject=%s WHERE vk_user_id=%s", (text, user_id))
            conn.commit()
            vk_send(user_id, f"Выберите уровень сложности для {text}:", keyboard=get_level_keyboard())
        elif text in LEVELS:
            cur.execute("UPDATE user_progress SET level=%s WHERE vk_user_id=%s", (text, user_id))
            conn.commit()
            # Генерация первого вопроса
            cur.execute("SELECT subject, level FROM user_progress WHERE vk_user_id=%s", (user_id,))
            subj, lvl = cur.fetchone()
            question = generate_question(subj, lvl)
            cur.execute("UPDATE user_progress SET question=%s, waiting_for_answer=TRUE WHERE vk_user_id=%s", (question, user_id))
            conn.commit()
            vk_send(user_id, f"Вопрос:\n{question}")
        else:
            # Проверка ответа
            if row and row[4]:  # waiting_for_answer
                question = row[3]
                explanation = check_answer(question, text)
                cur.execute("UPDATE user_progress SET last_answer=%s, waiting_for_answer=FALSE WHERE vk_user_id=%s", (text, user_id))
                conn.commit()
                vk_send(user_id, f"{explanation}\nНапишите «Начать», чтобы продолжить.")
            else:
                vk_send(user_id, "Выберите действие на клавиатуре или напишите «Начать», чтобы начать игру.", keyboard=get_main_keyboard())

    return PlainTextResponse("ok")
