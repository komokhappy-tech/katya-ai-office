import os
import requests
from flask import Flask, request

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
OPENAI_KEY = os.environ.get("OPENAI_KEY")

app = Flask(__name__)

def ask_gpt(system_prompt, user_text):
    r = requests.post(
        "https://api.openai.com/v1/chat/completions",
        headers={
            "Authorization": f"Bearer {OPENAI_KEY}",
            "Content-Type": "application/json"
        },
        json={
            "model": "gpt-4o-mini",
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_text}
            ],
            "temperature": 0.3
        }
    )
    return r.json()["choices"][0]["message"]["content"]

CORE = "Ответь только LOOK или MARKETING. Если это про салон — LOOK. Иначе MARKETING."
LOOK = "Ты директор салона красоты LOOK. Дай чёткий план действий."
MARKETING = "Ты маркетолог Кати. Дай стратегию и следующие шаги."

@app.route("/webhook", methods=["POST"])
def webhook():
    data = request.json
    text = data["message"]["text"]
    chat_id = data["message"]["chat"]["id"]

    route = ask_gpt(CORE, text)

    if "LOOK" in route:
        answer = ask_gpt(LOOK, text)
    else:
        answer = ask_gpt(MARKETING, text)

    requests.post(
        f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
        json={"chat_id": chat_id, "text": answer}
    )

    return "ok"

@app.route("/", methods=["GET"])
def index():
    return "Alive"
