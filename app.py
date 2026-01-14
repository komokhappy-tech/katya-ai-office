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

# ---------- filesystem helpers ----------

def ensure_storage():
    os.makedirs("memory", exist_ok=True)
    for a, path in MEMORY_PATH.items():
        if not os.path.exists(path):
            # core.json держит еще user_state
            if a == "CORE":
                save_json(path, {"notes": [], "inbox": [], "user_state": {}})
            else:
                save_json(path, {"notes": [], "inbox": []})

def load_json(path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            data = {}
    except Exception:
        data = {}

    data.setdefault("notes", [])
    data.setdefault("inbox", [])
    if not isinstance(data["notes"], list):
        data["notes"] = []
    if not isinstance(data["inbox"], list):
        data["inbox"] = []

    # core.json special
    if path == MEMORY_PATH["CORE"]:
        data.setdefault("user_state", {})
        if not isinstance(data["user_state"], dict):
            data["user_state"] = {}

    return data

def save_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def now_utc():
    return datetime.utcnow().isoformat() + "Z"


# ---------- Telegram API ----------

def tg(method, payload):
    return requests.post(f"{TELEGRAM_API}/{method}", json=payload, timeout=30)

def send_message(chat_id, text, reply_markup=None):
    payload = {"chat_id": chat_id, "text": text}
    if reply_markup:
        payload["reply_markup"] = reply_markup
    tg("sendMessage", payload)

def edit_message(chat_id, message_id, text, reply_markup=None):
    payload = {"chat_id": chat_id, "message_id": message_id, "text": text}
    if reply_markup:
        payload["reply_markup"] = reply_markup
    tg("editMessageText", payload)

def answer_callback(callback_id, text=None, show_alert=False):
    payload = {"callback_query_id": callback_id, "show_alert": show_alert}
    if text:
        payload["text"] = text
    tg("answerCallbackQuery", payload)


# ---------- Persistent user state (per chat) ----------

def get_active_agent(chat_id):
    core = load_json(MEMORY_PATH["CORE"])
    state = core.get("user_state", {})
    item = state.get(str(chat_id), {})
    agent = item.get("active_agent", "CORE")
    if agent not in AGENTS:
        agent = "CORE"
    return agent

def set_active_agent(chat_id, agent):
    if agent not in AGENTS:
        agent = "CORE"
    core = load_json(MEMORY_PATH["CORE"])
    core["user_state"].setdefault(str(chat_id), {})
    core["user_state"][str(chat_id)]["active_agent"] = agent
    save_json(MEMORY_PATH["CORE"], core)


# ---------- UI (Inline "tabs") ----------

def kb_tabs(active):
    # "вкладки" как inline кнопки под сообщением
    def btn(a):
        prefix = "✅ " if a == active else ""
        return {"text": f"{prefix}{a}", "callback_data": f"tab:{a}"}

    return {
        "inline_keyboard": [
            [btn("CORE"), btn("LOOK"), btn("MARKETING")],
            [btn("MONEY"), btn("FAMILY"), btn("PERSONAL")],
            [{"text": "📥 Задачи", "callback_data": "view:tasks"},
             {"text": "🧠 Память", "callback_data": "view:memory"},
             {"text": "🧾 Сводка", "callback_data": "view:summary"}],
            [{"text": "➕ Добавить задачу", "callback_data": "hint:add_task"},
             {"text": "➕ Добавить факт", "callback_data": "hint:add_fact"}],
        ]
    }

def screen_text(agent):
    return (
        f"Режим: {agent}\n\n"
        f"Пиши обычным текстом — я отвечу как {agent} (если OpenAI доступен).\n\n"
        f"Быстрые команды:\n"
        f"• +задача: ...\n"
        f"• +факт: ...\n"
        f"• -готово N\n"
        f"Можно адресовать так:\n"
        f"• +задача @LOOK: ...\n"
        f"• +факт @MONEY: ..."
    )

def format_tasks(agent, mem):
    items = mem.get("inbox", [])
    open_items = [it for it in items if it.get("status") == "open"]

    if not open_items:
        return f"📥 Задачи {agent}: пусто.\n\nДобавить: +задача: ..."

    lines = [f"📥 Задачи {agent} (открытые):"]
    for i, it in enumerate(items, start=1):
        if it.get("status") == "open":
            lines.append(f"{i}. {it.get('text','')}")
    lines.append("\nЗакрыть: -готово N")
    return "\n".join(lines)

def format_memory(agent, mem):
    # показываем последние 15 фактов
    notes = mem.get("notes", [])
    tail = notes[-15:]
    if not tail:
        return f"🧠 Память {agent}: пока пусто.\n\nДобавить: +факт: ..."
    out = [f"🧠 Память {agent} (последнее):"]
    for n in tail:
        out.append(f"• {n}")
    return "\n".join(out)

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
    return "\n".join(out)


# ---------- Dispatcher logic ----------

def parse_target_agent(text, default_agent):
    # адресация: @LOOK, @marketing и т.п.
    t = text.strip()
    up = t.upper()
    for a in AGENTS:
        marker = f"@{a}"
        if marker in up:
            # аккуратно удаляем маркер независимо от регистра
            cleaned = t
            # убираем варианты регистра
            cleaned = cleaned.replace(marker, "")
            cleaned = cleaned.replace(marker.lower(), "")
            cleaned = cleaned.replace(marker.capitalize(), "")
            return a, cleaned.strip()
    return default_agent, t

def add_task(agent, task_text):
    mem = load_json(MEMORY_PATH[agent])
    mem["inbox"].append({
        "text": task_text,
        "status": "open",
        "created_at": now_utc()
    })
    save_json(MEMORY_PATH[agent], mem)
    return len(mem["inbox"])

def close_task(agent, idx):
    mem = load_json(MEMORY_PATH[agent])
    if idx < 1 or idx > len(mem["inbox"]):
        return False, "Нет задачи с таким номером."
    item = mem["inbox"][idx - 1]
    item["status"] = "done"
    item["done_at"] = now_utc()
    save_json(MEMORY_PATH[agent], mem)
    return True, item.get("text", "")

def add_fact(agent, fact_text):
    mem = load_json(MEMORY_PATH[agent])
    mem["notes"].append(fact_text)
    save_json(MEMORY_PATH[agent], mem)
    return len(mem["notes"])


# ---------- OpenAI (optional) ----------

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


# ---------- Routes ----------

@app.get("/")
def health():
    return "OK", 200

@app.post("/webhook")
def webhook():
    ensure_storage()

    update = request.get_json(silent=True) or {}

    # 1) callback_query (нажатие inline кнопок)
    if "callback_query" in update:
        cq = update["callback_query"]
        cb_id = cq.get("id")
        msg = cq.get("message") or {}
        chat_id = (msg.get("chat") or {}).get("id")
        message_id = msg.get("message_id")
        data = cq.get("data") or ""

        if not chat_id or not message_id:
            answer_callback(cb_id, "Нет данных", False)
            return "ok", 200

        active = get_active_agent(chat_id)

        # tab switch
        if data.startswith("tab:"):
            new_agent = data.split(":", 1)[1].strip()
            if new_agent in AGENTS:
                set_active_agent(chat_id, new_agent)
                active = new_agent
                edit_message(chat_id, message_id, screen_text(active), kb_tabs(active))
                answer_callback(cb_id, f"Режим: {active}", False)
            else:
                answer_callback(cb_id, "Неизвестный режим", False)
            return "ok", 200

        # view screens
        if data == "view:tasks":
            mem = load_json(MEMORY_PATH[active])
            edit_message(chat_id, message_id, format_tasks(active, mem), kb_tabs(active))
            answer_callback(cb_id, None, False)
            return "ok", 200

        if data == "view:memory":
            mem = load_json(MEMORY_PATH[active])
            edit_message(chat_id, message_id, format_memory(active, mem), kb_tabs(active))
            answer_callback(cb_id, None, False)
            return "ok", 200

        if data == "view:summary":
            mem = load_json(MEMORY_PATH[active])
            edit_message(chat_id, message_id, format_summary(active, mem), kb_tabs(active))
            answer_callback(cb_id, None, False)
            return "ok", 200

        # hints
        if data == "hint:add_task":
            edit_message(
                chat_id, message_id,
                "Добавь задачу:\n\n+задача: текст\nили\n+задача @LOOK: текст",
                kb_tabs(active)
            )
            answer_callback(cb_id, None, False)
            return "ok", 200

        if data == "hint:add_fact":
            edit_message(
                chat_id, message_id,
                "Добавь факт:\n\n+факт: текст\nили\n+факт @MONEY: текст",
                kb_tabs(active)
            )
            answer_callback(cb_id, None, False)
            return "ok", 200

        answer_callback(cb_id, None, False)
        return "ok", 200

    # 2) обычное сообщение
    msg = update.get("message") or update.get("edited_message") or {}
    chat = msg.get("chat") or {}
    chat_id = chat.get("id")
    text = msg.get("text") or msg.get("caption")

    if not chat_id or not text:
        return "ok", 200

    t = (text or "").strip()
    active = get_active_agent(chat_id)

    # старт: рисуем "экран" с вкладками (inline)
    if t.lower() == "/start":
        set_active_agent(chat_id, "CORE")
        active = "CORE"
        send_message(chat_id, screen_text(active), kb_tabs(active))
        return "ok", 200

    # текстовые команды диспетчера
    target_agent, cleaned = parse_target_agent(t, active)
    tt = cleaned.strip()

    if tt.lower().startswith("+задача:"):
        task_text = tt.split(":", 1)[1].strip()
        n = add_task(target_agent, task_text)
        send_message(chat_id, f"✅ Задача добавлена в {target_agent} (№{n}).\nОткрой вкладку → 📥 Задачи")
        return "ok", 200

    if tt.lower().startswith("+факт:"):
        fact_text = tt.split(":", 1)[1].strip()
        n = add_fact(target_agent, fact_text)
        send_message(chat_id, f"✅ Факт сохранён в {target_agent} (№{n}).\nОткрой вкладку → 🧠 Память")
        return "ok", 200

    if tt.lower().startswith("-готово"):
        parts = tt.split()
        if len(parts) >= 2 and parts[1].isdigit():
            ok, info = close_task(target_agent, int(parts[1]))
            send_message(chat_id, (f"✅ Готово: {info}" if ok else f"⚠️ {info}"))
        else:
            send_message(chat_id, "Формат: -готово N")
        return "ok", 200

    if tt.lower() in ("?сводка", "/summary"):
        mem = load_json(MEMORY_PATH[target_agent])
        send_message(chat_id, format_summary(target_agent, mem))
        return "ok", 200

    # обычный запрос: если OpenAI доступен — ответит
    mem = load_json(MEMORY_PATH[target_agent])
    answer = ask_openai(system_prompt(target_agent, mem), tt)

    if not answer:
        send_message(
            chat_id,
            f"⚠️ Сейчас нет доступа к OpenAI (billing/лимиты/ключ).\n"
            f"Но диспетчер работает. Режим: {target_agent}\n\n"
            "Попробуй:\n"
            "• +задача: ...\n"
            "• +факт: ...\n"
            "• -готово N\n"
            "Или /start чтобы открыть вкладки."
        )
        return "ok", 200

    send_message(chat_id, answer)
    return "ok", 200
