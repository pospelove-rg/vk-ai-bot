from fastapi import FastAPI, Request
import requests
import os

VK_TOKEN = os.getenv("VK_TOKEN")
CONFIRMATION_CODE = os.getenv("VK_CONFIRMATION_CODE")

app = FastAPI()

def send_message(user_id, text):
    requests.post(
        "https://api.vk.com/method/messages.send",
        data={
            "access_token": VK_TOKEN,
            "v": "5.199",
            "user_id": user_id,
            "random_id": 0,
            "message": text
        }
    )

@app.post("/webhook")
async def webhook(request: Request):
    data = await request.json()

    if data["type"] == "confirmation":
        return CONFIRMATION_CODE

    if data["type"] == "message_new":
        user_id = data["object"]["message"]["from_id"]
        text = data["object"]["message"]["text"]

        send_message(user_id, f"Ты написал: {text}")

    return "ok"