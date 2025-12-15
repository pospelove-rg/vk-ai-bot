from fastapi import FastAPI, Request
from fastapi.responses import PlainTextResponse
from openai import OpenAI
import psycopg2
import os
import json

app = FastAPI()
openai_client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

# Настройки БД
DB_HOST = "dpg-d4v7f7npm1nc73bi9640-a.frankfurt-postgres.render.com"
DB_PORT = 5432
DB_USER = "vk_ai_bot_db_user"
DB_PASSWORD = "2nejvbVyY5yxTHLOGQCh3K7ylPyi5pwC"
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
    event = await request.json()
    event_type = event.get("type")
    
    # Игнорируем системные события, которые не являются сообщениями от пользователя
    if event_type not in ["message_new", "message_reply"]:
        return PlainTextResponse("ok")

    msg = event.get("object", {}).get("message", {})
    
    # Безопасное извлечение user_id
    user_id = msg.get("from_id") or msg.get("peer_id")
    if not user_id:
        return PlainTextResponse("ok")  # если user_id не определился, игнорируем

    text = msg.get("text", "").strip().lower()
    print(f"[DEBUG] Пользователь {user_id} написал: {text}")

    # Подключение к базе
    try:
        conn = get_connection()
        cur = conn.cursor()
    except Exception as e:
        print(f"[DB ERROR] {e}")
        return PlainTextResponse("ok")

    # Проверяем есть ли пользователь в базе
    cur.execute("SELECT vk_user_id, subject, level, question, waiting_for_answer FROM user_progress WHERE vk_user_id=%s", (user_id,))
    user = cur.fetchone()

    # Если пользователь новый — создаем запись
    if not user:
        cur.execute(
            "INSERT INTO user_progress (vk_user_id, waiting_for_answer) VALUES (%s, %s)",
            (user_id, False)
        )
        conn.commit()
        user = (user_id, None, None, None, False)

    # Основная логика
    if text == "начать":
        vk_send(user_id, "Выберите экзамен: ОГЭ или ЕГЭ", keyboard=get_exam_keyboard())
        cur.execute(
            "UPDATE user_progress SET waiting_for_answer=%s WHERE vk_user_id=%s",
            (True, user_id)
        )
        conn.commit()
    elif text in ["огэ", "егэ"] and user[4]:  # waiting_for_answer
        exam = text.upper()
        vk_send(user_id, f"Вы выбрали {exam}. Теперь выберите предмет.", keyboard=get_subject_keyboard(exam))
        cur.execute(
            "UPDATE user_progress SET subject=%s, waiting_for_answer=%s WHERE vk_user_id=%s",
            (exam, True, user_id)
        )
        conn.commit()
    elif user[4]:  # waiting_for_answer, ожидаем выбор предмета
        subject = text.title()
        vk_send(user_id, f"Вы выбрали предмет {subject}. Начинаем игру!")
        question = generate_question(subject)  # Ваша функция генерации вопросов
        vk_send(user_id, f"Вопрос:\n{question}")
        cur.execute(
            "UPDATE user_progress SET question=%s, waiting_for_answer=%s WHERE vk_user_id=%s",
            (question, True, user_id)
        )
        conn.commit()
    else:
        # Обработка ответов на вопросы
        if user[3]:  # question
            correct, explanation = check_answer(user[3], text)  # Ваша функция проверки ответа
            if correct:
                vk_send(user_id, f"Правильно! {explanation}")
            else:
                vk_send(user_id, f"Неправильно. {explanation}")
            vk_send(user_id, "Напишите 'Начать', чтобы получить следующий вопрос или сменить предмет.")
            cur.execute(
                "UPDATE user_progress SET last_answer=%s, waiting_for_answer=%s WHERE vk_user_id=%s",
                (text, False, user_id)
            )
            conn.commit()

    cur.close()
    conn.close()
    return PlainTextResponse("ok")
