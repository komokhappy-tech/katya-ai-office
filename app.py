import os
import json
import requests
from datetime import datetime
from flask import Flask, request

app = Flask(__name__)

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "").strip()
OPENAI_KEY = os.environ.get("OPENAI_KEY", "").strip()
TELEGRAM_API = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}"

AGENTS = ["CORE", "LOOK", "MARKETING", "MONEY", "FAMILY", "PERSONAL"]

MEMORY_PATH = {
    "CORE": "memory/core.json",
    "LOOK": "memory/look.json",
    "MARKETING": "memory/marketing.json",
    "MONEY": "memory/money.json",
    "FAMILY": "memory/family.json",
    "PERSONAL": "memory/personal.json",
}

# режим по чату (в RAM; после редеплоя может сброситься)
CHAT_MODE = {}  # chat_id -> agent


# -------------------- memory I/O --------------------

def load_json(path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            data = {}
    except Exception:
        data = {}

    # нормализуем структуру
    data.setdefault("notes", [])
    data.setdefault("inbox", [])
    if not isinstance(data["notes"], list):
        data["notes"] = []
    if not isinstance(data["inbox"], list):
        data["inbox"] = []
    return data


def save_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


# -------------------- Telegram helpers --------------------

def send(chat_id, text, keyboard=None):
    payload = {"chat_id": chat_id, "text": text}
    if keyboard:
        payload["reply_markup"] = keyboard
    requests.post(f"{TELEGRAM_API}/sendMessage", json=payload, timeout=20)


def edit(chat_id, msg_id, text, keyboard=None):
    payload = {"chat_id": chat_id, "message_id": msg_id, "text": text}
    if keyboard:
        payload["reply_markup"] = keyboard
    requests.post(f"{TELEGRAM_API}/editMessageText", json=payload, timeout=20)


def tabs(active):
    def b(label):
        prefix = "• " if label == active else ""
        return {"text": prefix + label, "callback_data": f"MODE:{label}"}

    return {
        "inline_keyboard": [
            [b("CORE"), b("LOOK"), b("MARKETING")],
            [b("MONEY"), b("FAMILY"), b("PERSONAL")]
        ]
    }


# -------------------- command dispatcher --------------------

def parse_target_agent(text, default_agent):
    """
    Поддержка адресации:
    +задача @LOOK: ...
    +факт @MONEY: ...
    """
    t = text.strip()

    for a in AGENTS:
        marker = f"@{a}"
        if marker in t.upper():
            # вырежем маркер
            cleaned = t.replace(marker, "").replace(marker.lower(), "").strip()
            return a, cleaned

    return default_agent, t


def add_task(agent, task_text):
    mem = load_json(MEMORY_PATH[agent])
    mem["inbox"].append({
        "text": task_text,
        "status": "open",
        "created_at": datetime.utcnow().isoformat() + "Z"
    })
    save_json(MEMORY_PATH[agent], mem)
    return len(mem["inbox"])


def close_task(agent, idx):
    mem = load_json(MEMORY_PATH[agent])
    if idx < 1 or idx > len(mem["inbox"]):
        return False, "Нет задачи с таким номером."
    item = mem["inbox"][idx - 1]
    item["status"] = "done"
    item["done_at"] = datetime.utcnow().isoformat() + "Z"
    save_json(MEMORY_PATH[agent], mem)
    return True, item["text"]


def add_fact(agent, fact_text):
    mem = load_json(MEMORY_PATH[agent])
    mem["notes"].append(fact_text)
    save_json(MEMORY_PATH[agent], mem)
    return len(mem["notes"])


def format_tasks(agent, mem):
    items = mem.get("inbox", [])
    if not items:
        return f"Задачи {agent}: пусто."

    lines = [f"Задачи {agent}:"]
    n = 0
    for i, it in enumerate(items, start=1):
        status = it.get("status", "open")
        if status == "open":
            n += 1
            lines.append(f"{i}. {it.get('text','')}")
    if n == 0:
        lines.append("Открытых задач нет.")
    lines.append("\nКоманда: -готово N")
    return "\n".join(lines)


def format_summary(agent, mem):
    notes = mem.get("notes", [])[-5:]
    inbox = [x for x in mem.get("inbox", []) if x.get("status") == "open"][:5]

    out = [f"Сводка: {agent}", ""]
    out.append("Память (последнее):")
    if notes:
        for n in notes:
            out.append(f"• {n}")
    else:
        out.append("• пока пусто")

    out.append("")
    out.append("Задачи (топ-5):")
    if inbox:
        for i, it in enumerate(inbox, start=1):
            out.append(f"{i}) {it.get('text','')}")
    else:
        out.append("• пока пусто")

    out.append("")
    out.append("Команды: +факт: ... | +задача: ... | ?задачи | ?память | ?сводка")
    return "\n".join(out)


# -------------------- OpenAI (optional) --------------------

def ask_openai(system, user):
    if not OPENAI_KEY:
        return None

    r = requests.post(
        "https://api.openai.com/v1/chat/completions",
        headers={"Authorization": f"Bearer {OPENAI_KEY}", "Content-Type": "application/json"},
        json={
            "model": "gpt-4o-mini",
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "temperature": 0.4
        },
        timeout=60
    )

    if r.status_code != 200:
        return None

    return r.json()["choices"][0]["message"]["content"].strip()


def system_prompt(agent, memory):
    mem_text = json.dumps(memory, ensure_ascii=False)
    base = {
        "CORE": "Ты CORE.AI — штаб: превращаешь хаос в план и решения.",
        "LOOK": "Ты LOOK.AI — директор салона LOOK: запись, услуги, геосервисы, контент.",
        "MARKETING": "Ты MARKETING.AI — маркетолог и наставник: делаем и учимся.",
        "MONEY": "Ты MONEY.AI — финдиректор: считаешь, предлагаешь решения.",
        "FAMILY": "Ты FAMILY.AI — семейный координатор: быт, договоренности, конфликты.",
        "PERSONAL": "Ты PERSONAL.AI — личный коуч: привычки, здоровье, цели."
    }[agent]
    return f"{base}\nОперационная память (JSON): {mem_text}"


# -------------------- routes --------------------

@app.get("/")
def health():
    return "OK", 200


@app.post("/webhook")
def webhook():
    update = request.get_json(silent=True) or {}

    # кнопки вкладок
    if "callback_query" in update:
        cq = update["callback_query"]
        data = cq.get("data", "")
        msg = cq.get("message", {})
        chat_id = (msg.get("chat") or {}).get("id")
        msg_id = msg.get("message_id")

        if chat_id and msg_id and data.startswith("MODE:"):
            mode = data.split("MODE:", 1)[1].strip().upper()
            if mode in AGENTS:
                CHAT_MODE[chat_id] = mode
                edit(
                    chat_id,
                    msg_id,
                    f"Режим: {mode}\n\nКоманды:\n+факт: ...\n+задача: ...\n?сводка | ?задачи | ?память\n-готово N",
                    tabs(mode),
                )
        return "ok", 200

    # сообщение
    msg = update.get("message") or update.get("edited_message") or {}
    chat = msg.get("chat") or {}
    chat_id = chat.get("id")
    text = msg.get("text") or msg.get("caption")

    if not chat_id or not text:
        return "ok", 200

    active = CHAT_MODE.get(chat_id, "CORE")

    if text.strip().lower() == "/start":
        CHAT_MODE[chat_id] = "CORE"
        send(
            chat_id,
            "AI Office включён.\nВыбирай вкладку и работай по отделам.\n\nКоманды:\n+факт: ...\n+задача: ...\n?сводка | ?задачи | ?память\n-готово N",
            tabs("CORE"),
        )
        return "ok", 200

    # диспетчер: цель агента через @AGENT
    target_agent, cleaned = parse_target_agent(text, active)
    t = cleaned.strip()

    # команды диспетчера
    if t.lower() in ("?сводка", "/summary"):
        mem = load_json(MEMORY_PATH[target_agent])
        send(chat_id, format_summary(target_agent, mem), tabs(active))
        return "ok", 200

    if t.lower() in ("?задачи", "/tasks"):
        mem = load_json(MEMORY_PATH[target_agent])
        send(chat_id, format_tasks(target_agent, mem), tabs(active))
        return "ok", 200

    if t.lower() in ("?память", "/memory"):
        mem = load_json(MEMORY_PATH[target_agent])
        send(chat_id, json.dumps(mem, ensure_ascii=False, indent=2), tabs(active))
        return "ok", 200

    if t.lower().startswith("+задача:"):
        task_text = t.split(":", 1)[1].strip()
        n = add_task(target_agent, task_text)
        send(chat_id, f"Задача добавлена в {target_agent} (№{n}).", tabs(active))
        return "ok", 200

    if t.lower().startswith("+факт:"):
        fact_text = t.split(":", 1)[1].strip()
        n = add_fact(target_agent, fact_text)
        send(chat_id, f"Факт сохранён в {target_agent} (№{n}).", tabs(active))
        return "ok", 200

    if t.lower().startswith("-готово"):
        parts = t.split()
        if len(parts) >= 2 and parts[1].isdigit():
            ok, info = close_task(target_agent, int(parts[1]))
            if ok:
                send(chat_id, f"Готово: {info}", tabs(active))
            else:
                send(chat_id, info, tabs(active))
        else:
            send(chat_id, "Формат: -готово N", tabs(active))
        return "ok", 200

    # обычный запрос: если OpenAI доступен — ответит умно, если нет — предложим командный режим
    mem = load_json(MEMORY_PATH[target_agent])
    answer = ask_openai(system_prompt(target_agent, mem), t)

    if not answer:
        send(
            chat_id,
            f"Я принял запрос в режиме {target_agent}, но сейчас нет доступа к OpenAI.\n"
            "Можем работать в диспетчер-режиме:\n"
            "• +задача: ...\n• +факт: ...\n• ?сводка / ?задачи / ?память",
            tabs(active)
        )
        return "ok", 200

    send(chat_id, answer, tabs(active))
    return "ok", 200
