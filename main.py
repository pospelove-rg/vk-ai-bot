from fastapi import FastAPI, Request, Response
import requests, os, json
from sqlalchemy import create_engine, Column, Integer, String, Boolean, DateTime
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from datetime import datetime
import openai
from dotenv import load_dotenv

load_dotenv()

VK_TOKEN = os.getenv("VK_TOKEN")
CONFIRMATION_CODE = os.getenv("VK_CONFIRMATION_CODE")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
DATABASE_URL = os.getenv("DATABASE_URL")

if not all([VK_TOKEN, CONFIRMATION_CODE, OPENAI_API_KEY, DATABASE_URL]):
    raise Exception("Не все переменные окружения установлены!")

openai.api_key = OPENAI_API_KEY

engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(bind=engine)
Base = declarative_base()

# Таблицы
class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True)
    stage = Column(String, default="start")
    subject = Column(String, default="")
    level = Column(String, default="easy")
    current_question_index = Column(Integer, default=0)
    score = Column(Integer, default=0)

class Question(Base):
    __tablename__ = "questions"
    id = Column(Integer, primary_key=True)
    subject = Column(String)
    level = Column(String)
    question_text = Column(String)
    correct_answer = Column(String)
    explanation = Column(String)

class Result(Base):
    __tablename__ = "results"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer)
    subject = Column(String)
    level = Column(String)
    question_text = Column(String)
    user_answer = Column(String)
    correct_answer = Column(String)
    is_correct = Column(Boolean)
    explanation = Column(String)
    timestamp = Column(DateTime, default=datetime.utcnow)

Base.metadata.create_all(bind=engine)

app = FastAPI()

# Клавиатуры
start_keyboard = {
    "one_time": True,
    "buttons": [[{"action": {"type": "text", "label": "Начать"}, "color": "positive"}]]
}

subject_keyboard = {
    "one_time": True,
    "buttons": [
        [{"action": {"type": "text", "label": "Математика"}, "color": "primary"}],
        [{"action": {"type": "text", "label": "Русский"}, "color": "primary"}],
        [{"action": {"type": "text", "label": "Физика"}, "color": "primary"}]
    ]
}

level_keyboard = {
    "one_time": True,
    "buttons": [
        [{"action": {"type": "text", "label": "Легкий"}, "color": "primary"}],
        [{"action": {"type": "text", "label": "Средний"}, "color": "primary"}],
        [{"action": {"type": "text", "label": "Сложный"}, "color": "primary"}]
    ]
}

NUM_QUESTIONS = 5

# Функции ИИ
def generate_question(subject, level):
    prompt = (
        f"Сгенерируй {level} вопрос для ОГЭ/ЕГЭ по предмету {subject}. "
        f"Формат: Вопрос? Ответ: <ответ>. Объяснение: <пояснение>"
    )
    response = openai.Completion.create(
        engine="text-davinci-003",
        prompt=prompt,
        max_tokens=200
    )
    text = response.choices[0].text.strip()
    if "Ответ:" in text:
        q_part, rest = text.split("Ответ:")
        if "Объяснение:" in rest:
            a_part, exp_part = rest.split("Объяснение:")
            return q_part.strip(), a_part.strip(), exp_part.strip()
        else:
            return q_part.strip(), rest.strip(), ""
    return text, "", ""

def check_answer(user_answer, correct_answer):
    prompt = (
        f"Проверить ответ ученика. Вопрос: {correct_answer}, "
        f"Ответ ученика: {user_answer}. Напиши 'Правильно' или 'Неправильно' и кратко объясни."
    )
    response = openai.Completion.create(
        engine="text-davinci-003",
        prompt=prompt,
        max_tokens=50
    )
    return response.choices[0].text.strip()

def send_vk_message(user_id, message, keyboard=None):
    payload = {
        "access_token": VK_TOKEN,
        "v": "5.199",
        "user_id": user_id,
        "random_id": int(datetime.now().timestamp() * 1000),
        "message": message
    }
    if keyboard:
        payload["keyboard"] = json.dumps(keyboard)
    requests.post("https://api.vk.com/method/messages.send", data=payload)

# Webhook VK
@app.post("/webhook")
async def webhook(request: Request):
    data = await request.json()
    if data["type"] == "confirmation":
        return Response(content=CONFIRMATION_CODE, media_type="text/plain")

    if data["type"] == "message_new":
        user_id = data["object"]["message"]["from_id"]
        text = data["object"]["message"]["text"].strip().lower()
        session = SessionLocal()
        user = session.query(User).filter(User.id==user_id).first()
        if not user:
            user = User(id=user_id)
            session.add(user)
            session.commit()

        # Логика стадий
        if text == "начать":
            send_vk_message(user_id, "Привет! Выбери предмет:", keyboard=subject_keyboard)
            user.stage = "choose_subject"
            session.commit()
        elif user.stage == "choose_subject":
            subject = text.capitalize()
            if subject in ["Математика", "Русский", "Физика"]:
                user.subject = subject
                user.stage = "choose_level"
                send_vk_message(user_id, "Выбери уровень сложности:", keyboard=level_keyboard)
                session.commit()
            else:
                send_vk_message(user_id, "Выбери предмет из кнопок.", keyboard=subject_keyboard)
        elif user.stage == "choose_level":
            level = text.capitalize()
            if level in ["Легкий", "Средний", "Сложный"]:
                user.level = level.lower()
                user.stage = "answer_question"
                user.current_question_index = 0
                user.score = 0
                session.commit()
                # Генерация вопросов
                questions_list = []
                for _ in range(NUM_QUESTIONS):
                    q, a, exp = generate_question(user.subject, user.level)
                    question = Question(subject=user.subject, level=user.level, question_text=q, correct_answer=a, explanation=exp)
                    session.add(question)
                    questions_list.append(question)
                session.commit()
                send_vk_message(user_id, f"Первый вопрос по {user.subject} ({user.level}):\n{questions_list[0].question_text}")
            else:
                send_vk_message(user_id, "Выбери уровень из кнопок.", keyboard=level_keyboard)
        elif user.stage == "answer_question":
            question = session.query(Question).filter(Question.subject==user.subject, Question.level==user.level)\
                .offset(user.current_question_index).first()
            if not question:
                send_vk_message(user_id, "Вопросы закончились. Напиши 'Начать', чтобы пройти новый тест.", keyboard=start_keyboard)
            else:
                feedback = check_answer(text, question.correct_answer)
                result = Result(
                    user_id=user.id,
                    subject=user.subject,
                    level=user.level,
                    question_text=question.question_text,
                    user_answer=text,
                    correct_answer=question.correct_answer,
                    is_correct=feedback.lower().startswith("правильно"),
                    explanation=question.explanation
                )
                session.add(result)
                session.commit()
                user.current_question_index += 1
                session.commit()
                next_message = f"{feedback}\nОбъяснение: {question.explanation}"
                if user.current_question_index < NUM_QUESTIONS:
                    next_q = session.query(Question).filter(Question.subject==user.subject, Question.level==user.level)\
                        .offset(user.current_question_index).first()
                    next_message += f"\nСледующий вопрос:\n{next_q.question_text}"
                else:
                    correct_count = session.query(Result).filter(Result.user_id==user.id, Result.subject==user.subject,
                                                                 Result.level==user.level, Result.is_correct==True).count()
                    next_message += f"\nТест завершён! Правильных ответов: {correct_count}/{NUM_QUESTIONS}\nНапиши 'Начать', чтобы пройти новый тест."
                    user.stage = "choose_subject"
                    user.current_question_index = 0
                    user.score = 0
                    session.commit()
                    send_vk_message(user_id, next_message, keyboard=subject_keyboard)
                    session.close()
                    return "ok"
                send_vk_message(user_id, next_message)
        else:
            send_vk_message(user_id, f"Ты написал: {text}", keyboard=start_keyboard)

        session.close()
    return "ok"
