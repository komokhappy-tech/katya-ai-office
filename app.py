import os
import json
import requests
from flask import Flask, request

app = Flask(__name__)

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
OPENAI_KEY = os.environ.get("OPENAI_KEY")

TELEGRAM_API = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}"

MEMORY_PATH = {
    "CORE": "memory/core.json",
    "LOOK": "memory/look.json",
    "MARKETING": "memory/marketing.json",
    "MONEY": "memory/money.json",
    "FAMILY": "memory/family.json",
    "PERSONAL": "memory/personal.json"
}

CHAT_MODE = {}  # chat_id -> active agent


# -------------------- helpers --------------------

def load_memory(agent):
    try:
        with open(MEMORY_PATH[agent], "r", encoding="utf-8") as f:
            return json.load(f)
    except:
        return {}

def save_memory(agent, data):
    with open(MEMORY_PATH[agent], "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def send(chat_id, text, keyboard=None):
    payload = {"chat_id": chat_id, "text": text}
    if keyboard:
        payload["reply_markup"] = keyboard
    requests.post(f"{TELEGRAM_API}/sendMessage", json=payload)

def edit(chat_id, msg_id, text, keyboard=None):
    payload = {"chat_id": chat_id, "message_id": msg_id, "text": text}
    if keyboard:
        payload["reply_markup"] = keyboard
    requests.post(f"{TELEGRAM_API}/editMessageText", json=payload)

def tabs(active):
    def b(label):
        return {"text": ("• " if label == active else "") + label, "callback_data": f"MODE:{label}"}

    return {
        "inline_keyboard": [
            [b("CORE"), b("LOOK"), b("MARKETING")],
            [b("MONEY"), b("FAMILY"), b("PERSONAL")]
        ]
    }


# -------------------- OpenAI --------------------

def ask_openai(system, user):
    if not OPENAI_KEY:
        return "OPENAI_KEY не задан."

    r = requests.post(
        "https://api.openai.com/v1/chat/completions",
        headers={"Authorization": f"Bearer {OPENAI_KEY}", "Content-Type": "application/json"},
        json={
            "model": "gpt-4o-mini",
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user}
            ],
            "temperature": 0.4
        }
    )

    if r.status_code != 200:
        return "Ошибка доступа к OpenAI. Проверь billing или лимиты."

    return r.json()["choices"][0]["message"]["content"]


def system_prompt(agent, memory):
    mem = json.dumps(memory, ensure_ascii=False)

    prompts = {
        "CORE": "Ты CORE.AI — штаб и навигатор.",
        "LOOK": "Ты директор салона красоты LOOK.",
        "MARKETING": "Ты наставник и маркетолог.",
        "MONEY": "Ты финансовый директор.",
        "FAMILY": "Ты семейный координатор.",
        "PERSONAL": "Ты коуч по жизни и привычкам."
    }

    return f"{prompts[agent]}\nОперационная память:\n{mem}"


# -------------------- routes --------------------

@app.route("/", methods=["GET"])
def health():
    return "OK", 200


@app.route("/webhook", methods=["POST"])
def webhook():
    update = request.json

    # handle buttons
    if "callback_query" in update:
        cq = update["callback_query"]
        data = cq["data"]
        msg = cq["message"]
        chat_id = msg["chat"]["id"]
        msg_id = msg["message_id"]

        if data.startswith("MODE:"):
            mode = data.replace("MODE:", "")
            CHAT_MODE[chat_id] = mode
            edit(chat_id, msg_id, f"Режим: {mode}", tabs(mode))
        return "ok", 200

    message = update.get("message")
    if not message:
        return "ok", 200

    chat_id = message["chat"]["id"]
    text = message.get("text", "")

    if chat_id not in CHAT_MODE:
        CHAT_MODE[chat_id] = "CORE"

    active = CHAT_MODE[chat_id]

    if text == "/start":
        send(chat_id, "AI Office запущен. Выбери режим.", tabs(active))
        return "ok", 200

    if text.lower().startswith("+запомни:"):
        fact = text.split(":", 1)[1].strip()
        mem = load_memory(active)
        mem.setdefault("notes", []).append(fact)
        save_memory(active, mem)
        send(chat_id, f"Запомнила для {active}: {fact}", tabs(active))
        return "ok", 200

    if text.lower() in ("?память", "/memory"):
        mem = load_memory(active)
        send(chat_id, json.dumps(mem, ensure_ascii=False, indent=2), tabs(active))
        return "ok", 200

    mem = load_memory(active)
    reply = ask_openai(system_prompt(active, mem), text)
    send(chat_id, reply, tabs(active))

    return "ok", 200
