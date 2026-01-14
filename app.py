import os
import re
import requests
from flask import Flask, request

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
OPENAI_KEY = os.environ.get("OPENAI_KEY", "")

app = Flask(__name__)

def send_telegram(chat_id: int, text: str):
    requests.post(
        f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
        json={"chat_id": chat_id, "text": text}
    )

def ask_openai(system_prompt: str, user_text: str) -> str:
    r = requests.post(
        "https://api.openai.com/v1/chat/completions",
        headers={"Authorization": f"Bearer {OPENAI_KEY}"},
        json={
            "model": "gpt-4o-mini",
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_text},
            ],
            "temperature": 0.2,
        },
        timeout=60
    )
    r.raise_for_status()
    return r.json()["choices"][0]["message"]["content"]

ROUTER_PROMPT = """
Ты — маршрутизатор. Определи категорию:
PERSONAL — личные цели и жизнь
FAMILY — дом, муж, дети
WORK_MARKETING — работа Кати как маркетолога
WORK_SALON — салон LOOK

Верни строго один из тегов:
PERSONAL / FAMILY / WORK_MARKETING / WORK_SALON
"""

PERSONAL_PROMPT = """
Ты — агент ЛИЧНОЕ. Помогаешь с целями, планами, жизнью.
Дай: краткий вывод, план, первый шаг.
"""

FAMILY_PROMPT = """
Ты — агент СЕМЬЯ. Помогаешь с домом, детьми, отношениями.
Дай: чеклист, что делегировать, первый шаг.
"""

WORK_MARKETING_PROMPT = """
Ты — агент МАРКЕТИНГ. Помогаешь с клиентами, стратегией, офферами.
Дай: план, решение, следующий шаг.
"""

WORK_SALON_PROMPT = """
Ты — агент САЛОН LOOK. Управляешь записями, акциями, ростом.
Дай: план, метрики, следующий шаг.
"""

@app.route("/webhook", methods=["POST"])
def webhook():
    data = request.json
    msg = data["message"]
    chat_id = msg["chat"]["id"]
    text = msg["text"]

    tag = ask_openai(ROUTER_PROMPT, text).strip()

    if tag == "PERSONAL":
        answer = ask_openai(PERSONAL_PROMPT, text)
    elif tag == "FAMILY":
        answer = ask_openai(FAMILY_PROMPT, text)
    elif tag == "WORK_MARKETING":
        answer = ask_openai(WORK_MARKETING_PROMPT, text)
    else:
        answer = ask_openai(WORK_SALON_PROMPT, text)

    send_telegram(chat_id, answer)
    return "ok"
