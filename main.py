from fastapi import FastAPI, Request
from fastapi.responses import PlainTextResponse
from openai import OpenAI
import psycopg2
import os
import json

app = FastAPI()
openai_client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

# Настройки БД
DB_HOST = "ваш_хост"
DB_PORT = 5432
DB_USER = "ваш_логин"
DB_PASSWORD = "ваш_пароль"
DB_NAME = "vk_ai_bot_db"

# Соединение с БД
def get_connection():
    return psycopg2.connect(
        host=DB_HOST,
        port=DB_PORT,
        user=DB_USER,
        password=DB_PASSWORD,
        database=DB_NAME
    )

# Клавиатуры ВК
def get_main_keyboard():
    return {
        "one_time": False,
        "buttons": [
            [{"action": {"type": "text", "label": "Начать"}}]
        ]
    }

def get_subject_keyboard(exam_type):
    if exam_type == "ОГЭ":
        subjects = ["Математика", "Русский язык", "Физика", "Химия", "Биология"]
    else:
        subjects = ["Математика", "Русский язык", "Физика", "Химия", "Биология", "История", "Обществознание", "Информатика"]
    
    buttons = [[{"action": {"type": "text", "label": subj}}] for subj in subjects]
    return {"one_time": True, "buttons": buttons}

# VK send
def vk_send(user_id, text, keyboard=None):
    print(f"[VK_SEND] to {user_id}: {text}")
    # Здесь можно вызвать VK API для отправки сообщений

# Генерация вопроса
def generate_question(subject, level):
    return f"Вопрос по {subject} ({level})"

# Проверка ответа через OpenAI
def check_answer(question, user_answer):
    prompt = f"""
    Я учитель школьникa. Вот вопрос: "{question}"
    Вот ответ ученика: "{user_answer}"
    Скажи, правильно ли он ответил. 
    Если неправильно, объясни коротко и дай правильный ответ.
    Ответь в формате:
    Верно или Неверно
    Объяснение
    Правильный ответ
    """
    response = openai_client.chat.completions.create(
        model="gpt-4.1-mini",
        messages=[{"role": "user", "content": prompt}]
    )
    content = response.choices[0].message.content.strip()
    return content

@app.post("/webhook")
async def vk_webhook(request: Request):
    data = await request.json()
    print("VK webhook received:", data)

    obj = data.get("object", {})
    message = obj.get("message", {})
    user_id = message.get("from_id")
    text = message.get("text", "").strip()

    if not user_id:
        print("Warning: from_id not found in object")
        return PlainTextResponse("ok")

    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT subject, level, waiting_for_answer FROM user_progress WHERE vk_user_id=%s", (user_id,))
            user_data = cur.fetchone()

            if text.lower() in ["начать", "start"]:
                vk_send(user_id, "Выберите тип экзамена:", keyboard={
                    "one_time": True,
                    "buttons": [
                        [{"action": {"type": "text", "label": "ОГЭ"}}],
                        [{"action": {"type": "text", "label": "ЕГЭ"}}]
                    ]
                })
            elif text.lower() in ["огэ", "егэ"]:
                exam_type = text.upper()
                cur.execute("""
                    INSERT INTO user_progress (vk_user_id, subject, level, waiting_for_answer)
                    VALUES (%s, %s, %s, %s)
                    ON CONFLICT (vk_user_id) DO UPDATE 
                    SET subject=%s, level=NULL, waiting_for_answer=FALSE
                """, (user_id, exam_type, None, False, exam_type))
                conn.commit()
                vk_send(user_id, f"Выберите предмет для {exam_type}:", keyboard=get_subject_keyboard(exam_type))
            elif user_data and user_data[0] in ["ОГЭ", "ЕГЭ"]:
                # Пользователь выбрал предмет
                exam_type = user_data[0]
                subject = text.title()
                cur.execute("""
                    UPDATE user_progress 
                    SET subject=%s, level='easy', waiting_for_answer=TRUE 
                    WHERE vk_user_id=%s
                """, (subject, user_id))
                conn.commit()
                question = generate_question(subject, "easy")
                cur.execute("UPDATE user_progress SET question=%s WHERE vk_user_id=%s", (question, user_id))
                conn.commit()
                vk_send(user_id, f"Ваш вопрос:\n{question}")
            elif user_data and user_data[2]:  # waiting_for_answer
                cur.execute("SELECT question FROM user_progress WHERE vk_user_id=%s", (user_id,))
                question = cur.fetchone()[0] or "Вопрос не найден"
                review = check_answer(question, text)
                vk_send(user_id, f"{review}\nНапишите 'Начать', чтобы продолжить.")
                cur.execute("""
                    UPDATE user_progress 
                    SET waiting_for_answer=FALSE, last_answer=%s
                    WHERE vk_user_id=%s
                """, (text, user_id))
                conn.commit()
            else:
                vk_send(user_id, "Выберите действие на клавиатуре или напишите 'Начать', чтобы начать игру.", keyboard=get_main_keyboard())
    finally:
        conn.close()

    return PlainTextResponse("ok")
