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


# -------------------- Memory I/O --------------------

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


# -------------------- Telegram UI --------------------

def reply_keyboard_main():
    # постоянная нижняя панель
    return {
        "keyboard": [
            [{"text": "🏠 Меню"}, {"text": "📥 Задачи"}, {"text": "🧠 Память"}],
            [{"text": "➕ Задача"}, {"text": "➕ Факт"}]
        ],
        "resize_keyboard": True,
        "is_persistent": True
    }


def reply_keyboard_menu():
    return {
        "keyboard": [
            [{"text": "🧭 Управление"}, {"text": "🆕 Новый диалог"}],
            [{"text": "👤 Профиль"}, {"text": "📚 База знаний"}],
            [{"text": "⬅️ Назад"}]
        ],
        "resize_keyboard": True,
        "is_persistent": True
    }


def reply_keyboard_agents(active):
    rows = [
        [{"text": f"{'✅ ' if active == 'CORE' else ''}CORE"},
         {"text": f"{'✅ ' if active == 'LOOK' else ''}LOOK"},
         {"text": f"{'✅ ' if active == 'MARKETING' else ''}MARKETING"}],
        [{"text": f"{'✅ ' if active == 'MONEY' else ''}MONEY"},
         {"text": f"{'✅ ' if active == 'FAMILY' else ''}FAMILY"},
         {"text": f"{'✅ ' if active == 'PERSONAL' else ''}PERSONAL"}],
        [{"text": "⬅️ Назад"}]
    ]
    return {"keyboard": rows, "resize_keyboard": True, "is_persistent": True}


def send(chat_id, text, keyboard=None):
    payload = {"chat_id": chat_id, "text": text}
    if keyboard:
        payload["reply_markup"] = keyboard
    requests.post(f"{TELEGRAM_API}/sendMessage", json=payload, timeout=20)


# -------------------- Dispatcher --------------------

def parse_target_agent(text, default_agent):
    """
    Адресация:
    +задача @LOOK: ...
    +факт @MONEY: ...
    """
    t = text.strip()

    for a in AGENTS:
        marker = f"@{a}"
        if marker in t.upper():
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
    return True, item.get("text", "")


def add_fact(agent, fact_text):
    mem = load_json(MEMORY_PATH[agent])
    mem["notes"].append(fact_text)
    save_json(MEMORY_PATH[agent], mem)
    return len(mem["notes"])


def format_tasks(agent, mem):
    items = mem.get("inbox", [])
    if not items:
        return f"📥 Задачи {agent}: пусто.\n\nКоманды:\n+задача: ...\n-готово N"

    lines = [f"📥 Задачи {agent}:"]
    open_count = 0
    for i, it in enumerate(items, start=1):
        if it.get("status") == "open":
            open_count += 1
            lines.append(f"{i}. {it.get('text', '')}")

    if open_count == 0:
        lines.append("Открытых задач нет.")

    lines.append("\nКоманды:\n+задача: ...\n-готово N")
    return "\n".join(lines)


def format_summary(agent, mem):
    notes = mem.get("notes", [])[-5:]
    inbox_open = [x for x in mem.get("inbox", []) if x.get("status") == "open"][:5]

    out = [f"🧾 Сводка: {agent}", ""]

    out.append("🧠 Память (последнее):")
    if notes:
        for n in notes:
            out.append(f"• {n}")
    else:
        out.append("• пока пусто")

    out.append("")
    out.append("📥 Задачи (топ-5):")
    if inbox_open:
        for i, it in enumerate(inbox_open, start=1):
            out.append(f"{i}) {it.get('text', '')}")
    else:
        out.append("• пока пусто")

    out.append("")
    out.append("Команды: +факт: ... | +задача: ... | ?сводка | -готово N")
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

    try:
        return r.json()["choices"][0]["message"]["content"].strip()
    except Exception:
        return None


def system_prompt(agent, memory):
    mem_text = json.dumps(memory, ensure_ascii=False)
    base = {
        "CORE": "Ты CORE.AI — штаб: превращаешь хаос в план и решения.",
        "LOOK": "Ты LOOK.AI — директор салона LOOK: задачи, геосервисы, контент, продажи.",
        "MARKETING": "Ты MARKETING.AI — маркетолог и наставник: делаем и учимся.",
        "MONEY": "Ты MONEY.AI — финдиректор: считаешь, предлагаешь решения, контролируешь бюджет.",
        "FAMILY": "Ты FAMILY.AI — семейный координатор: быт, договорённости, коммуникация.",
        "PERSONAL": "Ты PERSONAL.AI — личный коуч: привычки, здоровье, цели."
    }[agent]
    return f"{base}\nОперационная память (JSON): {mem_text}"


# -------------------- Routes --------------------

@app.get("/")
def health():
    return "OK", 200


@app.post("/webhook")
def webhook():
    update = request.get_json(silent=True) or {}

    msg = update.get("message") or update.get("edited_message") or {}
    chat = msg.get("chat") or {}
    chat_id = chat.get("id")
    text = msg.get("text") or msg.get("caption")

    if not chat_id or not text:
        return "ok", 200

    active = CHAT_MODE.get(chat_id, "CORE")
    t = (text or "").strip()

    # /start
    if t.lower() == "/start":
        CHAT_MODE[chat_id] = "CORE"
        send(
            chat_id,
            "AI Office включён.\nНажимай кнопки внизу — это твоя панель.\n\n"
            "Быстрые команды:\n"
            "• +задача: ...\n"
            "• +факт: ...\n"
            "• ?сводка\n"
            "• -готово N",
            reply_keyboard_main()
        )
        return "ok", 200

    # Панель
    if t == "🏠 Меню":
        send(chat_id, "Меню:", reply_keyboard_menu())
        return "ok", 200

    if t == "⬅️ Назад":
        send(chat_id, "Назад в рабочую панель.", reply_keyboard_main())
        return "ok", 200

    if t == "🧭 Управление":
        active = CHAT_MODE.get(chat_id, "CORE")
        send(chat_id, "Выбери ассистента:", reply_keyboard_agents(active))
        return "ok", 200

    # выбор агента
    clean = t.replace("✅ ", "").strip()
    if clean in AGENTS:
        CHAT_MODE[chat_id] = clean
        send(chat_id, f"Режим установлен: {clean}", reply_keyboard_main())
        return "ok", 200

    if t == "📥 Задачи":
        active = CHAT_MODE.get(chat_id, "CORE")
        mem = load_json(MEMORY_PATH[active])
        send(chat_id, format_tasks(active, mem), reply_keyboard_main())
        return "ok", 200

    if t == "🧠 Память":
        active = CHAT_MODE.get(chat_id, "CORE")
        mem = load_json(MEMORY_PATH[active])
        send(chat_id, json.dumps(mem, ensure_ascii=False, indent=2), reply_keyboard_main())
        return "ok", 200

    if t == "➕ Задача":
        send(chat_id, "Напиши задачу в формате:\n+задача: ...\n\nМожно так:\n+задача @LOOK: ...", reply_keyboard_main())
        return "ok", 200

    if t == "➕ Факт":
        send(chat_id, "Напиши факт в формате:\n+факт: ...\n\nМожно так:\n+факт @MONEY: ...", reply_keyboard_main())
        return "ok", 200

    if t == "🆕 Новый диалог":
        CHAT_MODE[chat_id] = "CORE"
        send(chat_id, "Ок, новый диалог. Режим: CORE", reply_keyboard_main())
        return "ok", 200

    if t == "👤 Профиль":
        active = CHAT_MODE.get(chat_id, "CORE")
        send(chat_id, f"Профиль (заглушка). Текущий режим: {active}\n\nСкоро добавим настройки.", reply_keyboard_main())
        return "ok", 200

    if t == "📚 База знаний":
        send(chat_id, "База знаний (заглушка). Сюда добавим ссылки, шаблоны, инструкции.", reply_keyboard_main())
        return "ok", 200

    # Текстовые команды диспетчера
    if t.lower() in ("?сводка", "/summary"):
        active = CHAT_MODE.get(chat_id, "CORE")
        mem = load_json(MEMORY_PATH[active])
        send(chat_id, format_summary(active, mem), reply_keyboard_main())
        return "ok", 200

    # адресация @AGENT внутри команд
    target_agent, cleaned = parse_target_agent(t, CHAT_MODE.get(chat_id, "CORE"))
    tt = cleaned.strip()

    if tt.lower().startswith("+задача:"):
        task_text = tt.split(":", 1)[1].strip()
        n = add_task(target_agent, task_text)
        send(chat_id, f"Задача добавлена в {target_agent} (№{n}).", reply_keyboard_main())
        return "ok", 200

    if tt.lower().startswith("+факт:"):
        fact_text = tt.split(":", 1)[1].strip()
        n = add_fact(target_agent, fact_text)
        send(chat_id, f"Факт сохранён в {target_agent} (№{n}).", reply_keyboard_main())
        return "ok", 200

    if tt.lower().startswith("-готово"):
        parts = tt.split()
        if len(parts) >= 2 and parts[1].isdigit():
            ok, info = close_task(target_agent, int(parts[1]))
            send(chat_id, (f"Готово: {info}" if ok else info), reply_keyboard_main())
        else:
            send(chat_id, "Формат: -готово N", reply_keyboard_main())
        return "ok", 200

    # обычный запрос: если OpenAI доступен — ответит, если нет — предложит диспетчер
    mem = load_json(MEMORY_PATH[target_agent])
    answer = ask_openai(system_prompt(target_agent, mem), tt)

    if not answer:
        send(
            chat_id,
            f"Сейчас нет доступа к OpenAI (billing/лимиты/ключ).\n"
            f"Но диспетчер работает. Режим: {target_agent}\n\n"
            "Попробуй:\n"
            "• +задача: ...\n"
            "• +факт: ...\n"
            "• ?сводка\n"
            "• 📥 Задачи / 🧠 Память",
            reply_keyboard_main()
        )
        return "ok", 200

    send(chat_id, answer, reply_keyboard_main())
    return "ok", 200

