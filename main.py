# -*- coding: utf-8 -*-

import os
import json
import random
import requests
import psycopg2
import openai

from fastapi import FastAPI, Request
from fastapi.responses import PlainTextResponse

# ----------------- CONFIG -----------------

VK_TOKEN = os.getenv("VK_TOKEN")
VK_CONFIRMATION_CODE = os.getenv("VK_CONFIRMATION_CODE")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
DATABASE_URL = os.getenv("DATABASE_URL")

# Настройка OpenAI (без proxies)
openai.api_key = OPENAI_API_KEY

VK_API_URL = "https://api.vk.com/method/messages.send"
VK_API_VERSION = "5.131"

app = FastAPI()

# ----------------- DATABASE -----------------

conn = psycopg2.connect(DATABASE_URL)
conn.autocommit = True
cursor = conn.cursor()

# ----------------- HELPERS -----------------

def send_vk_message(user_id: int, message: str, keyboard: dict = None):
    payload = {
        "user_id": user_id,
        "message": message,
        "random_id": random.randint(1, 2**31),
        "access_token": VK_TOKEN,
        "v": VK_API_VERSION
    }
    if keyboard:
        payload["keyboard"] = json.dumps(keyboard)
    requests.post(VK_API_URL, data=payload)

def generate_openai_response(prompt: str) -> str:
    response = openai.ChatCompletion.create(
        model="gpt-3.5-turbo",
        messages=[{"role": "user", "content": prompt}],
        max_tokens=200
    )
    return response.choices[0].message.content.strip()

# ----------------- VK KEYBOARDS -----------------

def get_main_keyboard():
    return {
        "one_time": False,
        "buttons": [
            [{"action": {"type": "text", "label": "Получить задание"}, "color": "primary"}],
            [{"action": {"type": "text", "label": "Помощь"}, "color": "secondary"}]
        ]
    }

# ----------------- ROUTES -----------------

@app.post("/webhook")
async def vk_webhook(request: Request):
    data = await request.json()
    print("VK webhook received:", data)  # Логирование для отладки

    # 1. Подтверждение сервера
    if data.get("type") == "confirmation":
        return PlainTextResponse(VK_CONFIRMATION_CODE)

    # 2. Обработка новых сообщений
    if data.get("type") == "message_new":
        obj = data.get("object", {})
        message = obj.get("message", {})

        user_id = message.get("from_id")
        text = message.get("text", "").lower()

        if user_id is None:
            print("Warning: from_id not found in message")
            return PlainTextResponse("ok")  # чтобы VK не блокировал

        if "задание" in text:
            # Генерация задания через OpenAI
            task = generate_openai_response("Придумай короткое математическое задание для школьника")
            send_vk_message(user_id, task, keyboard=get_main_keyboard())
        elif "помощь" in text:
            help_text = "Я могу сгенерировать для тебя задание. Напиши 'Получить задание'."
            send_vk_message(user_id, help_text, keyboard=get_main_keyboard())
        else:
            send_vk_message(user_id, "Выбери действие на клавиатуре.", keyboard=get_main_keyboard())

        return PlainTextResponse("ok")

    # 3. Для всех остальных событий
    return PlainTextResponse("ok")

@app.get("/")
async def root():
    return PlainTextResponse("Bot is running!")

# ----------------- END -----------------
