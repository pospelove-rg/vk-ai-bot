import os
import json
import random
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

# ================== CONSTANTS ==================

DIFFICULTIES = ["Базовый", "Средний", "Повышенный"]
TASK_TYPES = ["Теория", "Практика", "Тест", "Развёрнутый ответ"]

SUBJECTS = {
    "ОГЭ": [
        "Математика",
        "Русский язык",
        "Английский язык",
        "Физика",
        "Химия",
        "Биология",
        "География",
        "История",
        "Обществознание",
        "Информатика",
    ],
    "ЕГЭ": [
        "Математика профиль",
        "Русский язык",
        "Английский язык",
        "Физика",
        "Химия",
        "Биология",
        "География",
        "История",
        "Обществознание",
        "Информатика",
    ],
}

# Набор команд, которые НЕ должны считаться ответом
BASE_COMMANDS = {
    "начать",
    "знайка",
    "статистика",
    "сменить предмет",
    "сменить экзамен",
    "меню",
}

# Минимальная длина ответа для заданий с развернутым решением
MIN_LEN_BY_TYPE = {
    "Практика": 40,
    "Развёрнутый ответ": 80,
}

# ================== DB ==================


def get_connection():
    # Рекомендация: вынести в env, но оставляю как у тебя (чтобы "ничего не потерять").
    return psycopg2.connect(
        host="dpg-d4v7f7npm1nc73bi9640-a.frankfurt-postgres.render.com",
        port="5432",
        user="vk_ai_bot_db_user",
        password="2nejvbVyY5yxTHLOGQCh3K7ylPyi5pwC",
        database="vk_ai_bot_db",
    )


def ensure_user_row(cur, user_id: int):
    cur.execute(
        """
        INSERT INTO user_progress (vk_user_id)
        VALUES (%s)
        ON CONFLICT (vk_user_id) DO NOTHING
    """,
        (user_id,),
    )


def get_user_row(cur, user_id: int):
    cur.execute(
        """
        SELECT
            exam,
            subject,
            difficulty,
            task_type,
            question,
            waiting_for_answer,
            solved_count,
            current_question_id,
            current_source,
            attempts_count,
            correct_count
        FROM user_progress
        WHERE vk_user_id=%s
    """,
        (user_id,),
    )
    return cur.fetchone()


# ================== VK SEND ==================


def vk_send(user_id: int, message: str, keyboard: dict | None = None):
    payload = {
        "user_id": user_id,
        "message": message,
        "random_id": random.randint(1, 2_000_000_000),
        "access_token": VK_TOKEN,
        "v": "5.131",
    }
    if keyboard:
        payload["keyboard"] = json.dumps(keyboard, ensure_ascii=False)

    requests.post("https://api.vk.com/method/messages.send", data=payload, timeout=15)
    print(f"[VK_SEND] to {user_id}: {message}")


# ================== KEYBOARDS ==================


def get_main_keyboard():
    return {
        "one_time": False,
        "buttons": [
            [{"action": {"type": "text", "label": "Начать"}, "color": "primary"}],
            [{"action": {"type": "text", "label": "Статистика"}, "color": "secondary"}],
        ],
    }


def get_game_keyboard():
    return {
        "one_time": False,
        "buttons": [
            [
                {"action": {"type": "text", "label": "Знайка"}, "color": "primary"},
                {
                    "action": {"type": "text", "label": "Сменить предмет"},
                    "color": "secondary",
                },
            ],
            [
                {
                    "action": {"type": "text", "label": "Сменить экзамен"},
                    "color": "secondary",
                },
                {
                    "action": {"type": "text", "label": "Статистика"},
                    "color": "secondary",
                },
            ],
        ],
    }


def get_exam_keyboard():
    return {
        "one_time": False,
        "buttons": [
            [{"action": {"type": "text", "label": "ОГЭ"}, "color": "primary"}],
            [{"action": {"type": "text", "label": "ЕГЭ"}, "color": "primary"}],
        ],
    }


def get_subject_keyboard(exam: str):
    return {
        "one_time": False,
        "buttons": [
            [{"action": {"type": "text", "label": s}, "color": "secondary"}]
            for s in SUBJECTS.get(exam, [])
        ],
    }


def get_difficulty_keyboard():
    return {
        "one_time": False,
        "buttons": [
            [{"action": {"type": "text", "label": d}, "color": "secondary"}]
            for d in DIFFICULTIES
        ],
    }


def get_task_type_keyboard():
    return {
        "one_time": False,
        "buttons": [
            [{"action": {"type": "text", "label": t}, "color": "secondary"}]
            for t in TASK_TYPES
        ],
    }


def format_settings(exam, subject, difficulty, task_type):
    return (
        f"📌 Текущие настройки:\n"
        f"Экзамен: {exam}\n"
        f"Предмет: {subject}\n"
        f"Сложность: {difficulty}\n"
        f"Тип задания: {task_type}"
    )


# ================== OPENAI ==================


def generate_question(exam: str, subject: str, difficulty: str, task_type: str) -> str:
    prompt = f"""
Ты экзаменатор {exam}.

Предмет: {subject}
Уровень сложности: {difficulty}
Тип задания: {task_type}

Сформулируй ОДНО задание.
Не давай ответ.
Не пиши "Вопрос:" — только текст задания.
"""
    r = client.chat.completions.create(
        model="gpt-4o-mini", messages=[{"role": "user", "content": prompt}]
    )
    return r.choices[0].message.content.strip()


def check_answer(question: str, user_answer: str, task_type: str):
    prompt = f"""
Ты строгий экзаменатор.

Тип задания: {task_type}

Вопрос:
{question}

Ответ ученика:
{user_answer}

Правила проверки:
- Если ответ слишком короткий или формальный — RESULT: WRONG
- Если отсутствуют формулы, законы, рассуждения (для практики) — RESULT: WRONG
- НЕ додумывай ответ за ученика
- Засчитывай ТОЛЬКО если ответ явно демонстрирует понимание

Ответь строго в формате:
RESULT: CORRECT или RESULT: WRONG
EXPLANATION: краткое объяснение (2–4 предложения)
"""
    r = client.chat.completions.create(
        model="gpt-4o-mini", messages=[{"role": "user", "content": prompt}]
    )
    return r.choices[0].message.content.strip()


# ================== QUESTION SOURCE ==================


def choose_source(task_type: str, difficulty: str) -> str:
    # Тесты всегда локальные (экономим AI)
    if task_type == "Тест":
        return "local"

    # Базовая практика — сначала локально
    if task_type == "Практика" and difficulty == "Базовый":
        return "local"

    return "ai"


def get_question(exam, subject, difficulty, task_type, cur):
    source = choose_source(task_type, difficulty)

    # 1️⃣ Пробуем локальный банк
    if source == "local":
        cur.execute(
            """
            SELECT id, question_text
            FROM questions
            WHERE exam=%s
              AND subject=%s
              AND difficulty=%s
              AND task_type=%s
              AND source='local'
            ORDER BY RANDOM()
            LIMIT 1
        """,
            (exam, subject, difficulty, task_type),
        )
        row = cur.fetchone()
        if row:
            return {"id": row[0], "text": row[1], "source": "local"}

        # fallback на AI
        source = "ai"

    # 2️⃣ AI-вопрос
    text = generate_question(exam, subject, difficulty, task_type)

    cur.execute(
        """
        INSERT INTO questions (exam, subject, difficulty, task_type, question_text, source)
        VALUES (%s,%s,%s,%s,%s,'ai')
        RETURNING id
    """,
        (exam, subject, difficulty, task_type, text),
    )
    qid = cur.fetchone()[0]

    return {"id": qid, "text": text, "source": "ai"}


# ================== HELPERS ==================


def normalize(text: str) -> str:
    return (text or "").strip()


def normalize_lower(text: str) -> str:
    return normalize(text).lower()


def is_command(text_lower: str) -> bool:
    # команды + уровни/типы тоже не "ответ"
    if text_lower in BASE_COMMANDS:
        return True
    if text_lower in {d.lower() for d in DIFFICULTIES}:
        return True
    if text_lower in {t.lower() for t in TASK_TYPES}:
        return True
    if text_lower in {"огэ", "егэ"}:
        return True
    return False


# ================== WEBHOOK ==================


@app.post("/webhook")
async def vk_webhook(request: Request):
    data = await request.json()

    if data.get("type") == "confirmation":
        return PlainTextResponse(VK_CONFIRMATION or "")

    if data.get("type") != "message_new":
        return PlainTextResponse("ok")

    msg = data["object"]["message"]
    user_id = msg["from_id"]
    text = normalize(msg.get("text", ""))
    text_lower = text.lower()
    text_upper = text.upper()

    print(f"[DEBUG] Пользователь {user_id} написал: {text}")

    conn = get_connection()
    cur = conn.cursor()

    # гарантируем строку пользователя
    ensure_user_row(cur, user_id)
    conn.commit()

    row = get_user_row(cur, user_id)
    # row: (exam, subject, difficulty, task_type, question, waiting_for_answer, solved_count)
    (
        exam,
        subject,
        difficulty,
        task_type,
        question,
        waiting,
        solved_count,
        current_qid,
        current_source,
        attempts_count,
        correct_count,
    ) = row

    # ===== 1) ПРИВЕТ (всегда раньше всего, чтобы "привет" не считался ответом) =====
    if text_lower in ("привет", "hello", "hi"):
        vk_send(
            user_id,
            "Привет! 👋 Я бот для подготовки к ОГЭ и ЕГЭ.\n\n"
            "Как работать со мной:\n"
            "1️⃣ Выбери экзамен и предмет\n"
            "2️⃣ Укажи сложность и тип задания\n"
            "3️⃣ Нажми «Знайка» — получишь вопрос\n"
            "4️⃣ Отвечай текстом или буквой (в тестах)\n\n"
            "В любой момент можно сменить предмет или экзамен кнопками ниже.",
            get_main_keyboard(),
        )
        conn.close()
        return PlainTextResponse("ok")

    # ===== 2) СТАТИСТИКА =====
    if text_lower == "статистика":
        cur.execute(
            """
            SELECT 
                COALESCE(attempts_count, 0),
                COALESCE(correct_count, 0)
            FROM user_progress
            WHERE vk_user_id = %s
        """,
            (user_id,),
        )

        row_stats = cur.fetchone()
        attempts, correct = row_stats if row_stats else (0, 0)

        vk_send(
            user_id,
            (
                "📊 Ваша статистика:\n"
                f"Всего попыток: {attempts}\n"
                f"Правильных ответов: {correct}\n"
                f"Точность: {round((correct / attempts) * 100, 1) if attempts else 0}%"
            ),
            get_game_keyboard(),
        )

        conn.close()
        return PlainTextResponse("ok")

    # ===== 3) СМЕНА ЭКЗАМЕНА =====
    if text_lower == "сменить экзамен":
        cur.execute(
            """
            UPDATE user_progress
            SET exam=NULL, subject=NULL, difficulty=NULL, task_type=NULL,
                question=NULL, waiting_for_answer=false
            WHERE vk_user_id=%s
        """,
            (user_id,),
        )
        conn.commit()
        vk_send(user_id, "Выберите экзамен:", get_exam_keyboard())
        conn.close()
        return PlainTextResponse("ok")

    # ===== 4) СМЕНА ПРЕДМЕТА =====
    if text_lower == "сменить предмет":
        if not exam:
            vk_send(user_id, "Сначала выберите экзамен:", get_exam_keyboard())
            conn.close()
            return PlainTextResponse("ok")

        cur.execute(
            """
            UPDATE user_progress
            SET subject=NULL, difficulty=NULL, task_type=NULL,
                question=NULL, waiting_for_answer=false
            WHERE vk_user_id=%s
        """,
            (user_id,),
        )
        conn.commit()
        vk_send(user_id, "Выберите предмет:", get_subject_keyboard(exam))
        conn.close()
        return PlainTextResponse("ok")

    # ===== 5) ВЫБОР ЭКЗАМЕНА =====
    if text_upper in ("ОГЭ", "ЕГЭ"):
        cur.execute(
            """
            UPDATE user_progress
            SET exam=%s, subject=NULL, difficulty=NULL, task_type=NULL,
                question=NULL, waiting_for_answer=false
            WHERE vk_user_id=%s
        """,
            (text_upper, user_id),
        )
        conn.commit()
        vk_send(user_id, "Выберите предмет:", get_subject_keyboard(text_upper))
        conn.close()
        return PlainTextResponse("ok")

    # ===== 6) ВЫБОР ПРЕДМЕТА (только если экзамен выбран, а предмет ещё нет) =====
    if exam and not subject:
        # валидируем, что это реально предмет из списка
        if text not in SUBJECTS.get(exam, []):
            vk_send(user_id, "Выберите предмет кнопками:", get_subject_keyboard(exam))
            conn.close()
            return PlainTextResponse("ok")

        cur.execute(
            """
            UPDATE user_progress
            SET subject=%s, difficulty=NULL, task_type=NULL,
                question=NULL, waiting_for_answer=false
            WHERE vk_user_id=%s
        """,
            (text, user_id),
        )
        conn.commit()

        vk_send(user_id, "Выберите уровень сложности:", get_difficulty_keyboard())
        conn.close()
        return PlainTextResponse("ok")

    # ===== 7) ВЫБОР СЛОЖНОСТИ =====
    if (
        exam
        and subject
        and not difficulty
        and text_lower in {d.lower() for d in DIFFICULTIES}
    ):
        # сохраняем каноническое значение (с заглавной)
        chosen = next(d for d in DIFFICULTIES if d.lower() == text_lower)

        cur.execute(
            """
            UPDATE user_progress
            SET difficulty=%s, task_type=NULL,
                question=NULL, waiting_for_answer=false
            WHERE vk_user_id=%s
        """,
            (chosen, user_id),
        )
        conn.commit()

        vk_send(user_id, "Выберите тип задания:", get_task_type_keyboard())
        conn.close()
        return PlainTextResponse("ok")

    # ===== 8) ВЫБОР ТИПА ЗАДАНИЯ =====
    if (
        exam
        and subject
        and difficulty
        and not task_type
        and text_lower in {t.lower() for t in TASK_TYPES}
    ):
        chosen = next(t for t in TASK_TYPES if t.lower() == text_lower)

        cur.execute(
            """
            UPDATE user_progress
            SET task_type=%s, question=NULL, waiting_for_answer=false
            WHERE vk_user_id=%s
        """,
            (chosen, user_id),
        )
        conn.commit()

        vk_send(
            user_id,
            "Настройки сохранены. Нажмите «Знайка», чтобы получить вопрос.",
            get_game_keyboard(),
        )
        conn.close()
        return PlainTextResponse("ok")

    # ===== 9) НАЧАТЬ =====
    if text_lower == "начать":
        # если ждём ответ — НЕ генерируем новый вопрос
        if waiting and question:
            vk_send(user_id, "Сначала ответьте на текущий вопрос.", get_game_keyboard())
            conn.close()
            return PlainTextResponse("ok")

        if not exam:
            vk_send(user_id, "Выберите экзамен:", get_exam_keyboard())
            conn.close()
            return PlainTextResponse("ok")

        if not subject:
            vk_send(user_id, "Выберите предмет:", get_subject_keyboard(exam))
            conn.close()
            return PlainTextResponse("ok")

        if not difficulty:
            vk_send(user_id, "Выберите уровень сложности:", get_difficulty_keyboard())
            conn.close()
            return PlainTextResponse("ok")

        if not task_type:
            vk_send(user_id, "Выберите тип задания:", get_task_type_keyboard())
            conn.close()
            return PlainTextResponse("ok")

        vk_send(
            user_id,
            (
                f"📘 Текущие настройки:\n"
                f"Экзамен: {exam}\n"
                f"Предмет: {subject}\n"
                f"Сложность: {difficulty}\n"
                f"Тип задания: {task_type}\n\n"
                f"Нажмите «Знайка», чтобы получить вопрос."
            ),
            get_game_keyboard(),
        )
        conn.close()
        return PlainTextResponse("ok")

    # ===== 9.1) ЗНАЙКА — СРАЗУ ВОПРОС =====
    if text_lower == "знайка":
        if waiting and question:
            vk_send(user_id, "Сначала ответьте на текущий вопрос.", get_game_keyboard())
            conn.close()
            return PlainTextResponse("ok")

        if not exam:
            vk_send(user_id, "Выберите экзамен:", get_exam_keyboard())
            conn.close()
            return PlainTextResponse("ok")

        if not subject:
            vk_send(user_id, "Выберите предмет:", get_subject_keyboard(exam))
            conn.close()
            return PlainTextResponse("ok")

        if not difficulty:
            vk_send(user_id, "Выберите уровень сложности:", get_difficulty_keyboard())
            conn.close()
            return PlainTextResponse("ok")

        if not task_type:
            vk_send(user_id, "Выберите тип задания:", get_task_type_keyboard())
            conn.close()
            return PlainTextResponse("ok")

        # ⚡ СРАЗУ генерируем вопрос (без экрана настроек)
        q = get_question(exam, subject, difficulty, task_type, cur)

        cur.execute(
            """
            UPDATE user_progress
            SET
                question=%s,
                waiting_for_answer=true,
                current_question_id=%s,
                current_source=%s
            WHERE vk_user_id=%s
        """,
            (q["text"], q["id"], q["source"], user_id),
        )
        conn.commit()

        vk_send(user_id, f"🧠 Вопрос от «Знайки»:\n{q['text']}", get_game_keyboard())
        conn.close()
        return PlainTextResponse("ok")

    # ===== 10) ОТВЕТ НА ВОПРОС =====
    # Ответом считаем только если реально ждём ответ и это не команда
    if waiting and question and (not is_command(text_lower)):

        # --- 10.1 Проверка на отписку ---
        if text_lower in {
            "сложно",
            "не знаю",
            "хз",
            "без понятия",
            "не понял",
            "не могу",
            "не знаю ответ",
        }:
            vk_send(
                user_id,
                "❌ Такой ответ не может быть засчитан.\n"
                "Попробуйте описать решение или рассуждения.",
                get_game_keyboard(),
            )
            conn.close()
            return PlainTextResponse("ok")

        # --- 10.2 Проверка минимальной длины ---
        min_len = MIN_LEN_BY_TYPE.get(task_type)

        if min_len and len(text.strip()) < min_len:
            vk_send(
                user_id,
                f"❌ Ответ слишком короткий для задания типа «{task_type}».\n"
                f"Пожалуйста, опишите решение подробнее.",
                get_game_keyboard(),
            )
            conn.close()
            return PlainTextResponse("ok")

        # --- 10.3 Проверка через AI ---
        result_text = check_answer(question, text, task_type)

        is_correct = "RESULT: CORRECT" in result_text

        cur.execute(
            """
            INSERT INTO user_answers (vk_user_id, question_id, source, user_answer, is_correct)
            VALUES (%s, %s, %s, %s, %s)
        """,
            (user_id, current_qid, current_source or "ai", text, is_correct),
        )
        conn.commit()

        cur.execute(
            """
            UPDATE user_progress
            SET
                waiting_for_answer=false,
                question=NULL,
                current_question_id=NULL,
            current_source=NULL,
            attempts_count = attempts_count + 1,
            correct_count = correct_count + %s
        WHERE vk_user_id=%s
    """,
            (1 if is_correct else 0, user_id),
        )
    conn.commit()

    vk_send(
        user_id,
        result_text.replace("RESULT: CORRECT", "✅ Верно").replace(
            "RESULT: WRONG", "❌ Неверно"
        ),
        get_game_keyboard(),
    )
    conn.close()
    return PlainTextResponse("ok")

    # ===== 11) ПО УМОЛЧАНИЮ =====
    # Если пользователь нажал что-то не по сценарию — мягко подсказываем нужный шаг
    if waiting and question:
        vk_send(
            user_id,
            "Пожалуйста, ответьте на текущий вопрос или используйте кнопки.",
            get_game_keyboard(),
        )
    elif not exam:
        vk_send(user_id, "Выберите экзамен:", get_exam_keyboard())
    elif not subject:
        vk_send(user_id, "Выберите предмет:", get_subject_keyboard(exam))
    elif not difficulty:
        vk_send(user_id, "Выберите уровень сложности:", get_difficulty_keyboard())
    elif not task_type:
        vk_send(user_id, "Выберите тип задания:", get_task_type_keyboard())
    else:
        vk_send(
            user_id, "Нажмите «Знайка», чтобы получить вопрос.", get_game_keyboard()
        )

    conn.close()
    return PlainTextResponse("ok")
