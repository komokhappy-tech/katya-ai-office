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

# -------------------- Helpers --------------------

def nowz():
    return datetime.utcnow().isoformat() + "Z"

def load_json(path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            data = {}
    except Exception:
        data = {}

    # unify structure for agent memories
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

def core_state():
    """
    core.json also stores per-chat UI state:
    {
      "notes": [],
      "inbox": [],
      "chats": {
         "12345": {"active_agent":"LOOK","screen":"HOME","awaiting":null,"panel_msg_id":111}
      }
    }
    """
    core = load_json(MEMORY_PATH["CORE"])
    core.setdefault("chats", {})
    if not isinstance(core["chats"], dict):
        core["chats"] = {}
    return core

def get_chat_state(chat_id):
    core = core_state()
    cid = str(chat_id)
    st = core["chats"].get(cid) or {}
    st.setdefault("active_agent", "CORE")
    st.setdefault("screen", "HOME")        # HOME | TASKS | MEMORY | SUMMARY | ADD
    st.setdefault("awaiting", None)        # TASK | FACT | None
    st.setdefault("panel_msg_id", None)    # message id of dashboard/panel
    core["chats"][cid] = st
    save_json(MEMORY_PATH["CORE"], core)
    return st

def set_chat_state(chat_id, **updates):
    core = core_state()
    cid = str(chat_id)
    st = core["chats"].get(cid) or {}
    st.setdefault("active_agent", "CORE")
    st.setdefault("screen", "HOME")
    st.setdefault("awaiting", None)
    st.setdefault("panel_msg_id", None)
    for k, v in updates.items():
        st[k] = v
    core["chats"][cid] = st
    save_json(MEMORY_PATH["CORE"], core)
    return st

# -------------------- Telegram API --------------------

def tg(method, payload):
    return requests.post(f"{TELEGRAM_API}/{method}", json=payload, timeout=25)

def answer_cb(callback_query_id, text=None):
    payload = {"callback_query_id": callback_query_id}
    if text:
        payload["text"] = text
        payload["show_alert"] = False
    tg("answerCallbackQuery", payload)

def send_message(chat_id, text, reply_markup=None):
    payload = {"chat_id": chat_id, "text": text}
    if reply_markup:
        payload["reply_markup"] = reply_markup
    r = tg("sendMessage", payload)
    try:
        return r.json().get("result", {}).get("message_id")
    except Exception:
        return None

def edit_message(chat_id, message_id, text, reply_markup=None):
    payload = {"chat_id": chat_id, "message_id": message_id, "text": text}
    if reply_markup:
        payload["reply_markup"] = reply_markup
    return tg("editMessageText", payload)

def safe_upsert_panel(chat_id, text, keyboard):
    """
    Keep ONE main message as 'screen'. If can't edit (e.g. deleted), send new and store msg_id.
    """
    st = get_chat_state(chat_id)
    panel_id = st.get("panel_msg_id")
    if panel_id:
        r = edit_message(chat_id, panel_id, text, keyboard)
        try:
            ok = r.json().get("ok", False)
        except Exception:
            ok = False
        if ok:
            return panel_id
    new_id = send_message(chat_id, text, keyboard)
    if new_id:
        set_chat_state(chat_id, panel_msg_id=new_id)
    return new_id

def truncate(s, limit=3800):
    s = s or ""
    if len(s) <= limit:
        return s
    return s[:limit] + "\n…(обрезано)"

# -------------------- UI (Inline Keyboards) --------------------

def kb_home(active):
    # agent tabs
    def tab(a):
        prefix = "✅ " if a == active else ""
        return {"text": f"{prefix}{a}", "callback_data": f"agent:{a}"}

    rows = [
        [tab("CORE"), tab("LOOK"), tab("MARKETING")],
        [tab("MONEY"), tab("FAMILY"), tab("PERSONAL")],
        [{"text": "📥 Задачи", "callback_data": "view:TASKS"},
         {"text": "🧠 Память", "callback_data": "view:MEMORY"},
         {"text": "🧾 Сводка", "callback_data": "view:SUMMARY"}],
        [{"text": "➕ Добавить", "callback_data": "view:ADD"}],
    ]
    return {"inline_keyboard": rows}

def kb_add():
    return {
        "inline_keyboard": [
            [{"text": "➕ Задачу", "callback_data": "add:TASK"},
             {"text": "➕ Факт", "callback_data": "add:FACT"}],
            [{"text": "⬅️ Назад", "callback_data": "back:HOME"}],
        ]
    }

def kb_back():
    return {"inline_keyboard": [[{"text": "⬅️ Назад", "callback_data": "back:HOME"}]]}

def kb_tasks(agent, mem):
    # show open tasks; allow closing by buttons
    open_items = []
    for i, it in enumerate(mem.get("inbox", []), start=1):
        if it.get("status") == "open":
            open_items.append((i, it.get("text", "")))

    rows = []
    # limit buttons to avoid huge keyboard
    for i, _ in open_items[:8]:
        rows.append([{"text": f"✅ Закрыть #{i}", "callback_data": f"done:{agent}:{i}"}])

    rows.append([{"text": "➕ Добавить задачу", "callback_data": "add:TASK"}])
    rows.append([{"text": "⬅️ Назад", "callback_data": "back:HOME"}])
    return {"inline_keyboard": rows}

# -------------------- Memory operations --------------------

def add_task(agent, task_text):
    mem = load_json(MEMORY_PATH[agent])
    mem["inbox"].append({"text": task_text, "status": "open", "created_at": nowz()})
    save_json(MEMORY_PATH[agent], mem)
    return len(mem["inbox"])

def close_task(agent, idx):
    mem = load_json(MEMORY_PATH[agent])
    if idx < 1 or idx > len(mem["inbox"]):
        return False, "Нет задачи с таким номером."
    item = mem["inbox"][idx - 1]
    item["status"] = "done"
    item["done_at"] = nowz()
    save_json(MEMORY_PATH[agent], mem)
    return True, item.get("text", "")

def add_fact(agent, fact_text):
    mem = load_json(MEMORY_PATH[agent])
    mem["notes"].append(fact_text)
    save_json(MEMORY_PATH[agent], mem)
    return len(mem["notes"])

# -------------------- Screens --------------------

def render_home(chat_id):
    st = get_chat_state(chat_id)
    a = st["active_agent"]
    text = (
        f"🏢 Katya AI Office\n"
        f"Активный отдел: **{a}**\n\n"
        "Выбирай отдел (вкладку) и работай через кнопки ниже.\n"
        "➕ Добавить — чтобы записать задачу/факт.\n"
    )
    # Telegram Markdown is picky; keep plain text:
    text = text.replace("**", "")
    safe_upsert_panel(chat_id, text, kb_home(a))
    set_chat_state(chat_id, screen="HOME", awaiting=None)

def render_add(chat_id):
    st = get_chat_state(chat_id)
    a = st["active_agent"]
    text = (
        f"➕ Добавление в отдел: {a}\n\n"
        "Выбери, что добавить:"
    )
    safe_upsert_panel(chat_id, text, kb_add())
    set_chat_state(chat_id, screen="ADD")

def render_tasks(chat_id):
    st = get_chat_state(chat_id)
    a = st["active_agent"]
    mem = load_json(MEMORY_PATH[a])
    open_tasks = []
    for i, it in enumerate(mem.get("inbox", []), start=1):
        if it.get("status") == "open":
            open_tasks.append((i, it.get("text", "")))

    if not open_tasks:
        text = f"📥 Задачи отдела {a}\n\nПока пусто."
    else:
        lines = [f"📥 Задачи отдела {a}", ""]
        for i, t in open_tasks[:20]:
            lines.append(f"{i}. {t}")
        if len(open_tasks) > 20:
            lines.append("\n…есть ещё задачи (показаны первые 20).")
        text = "\n".join(lines)

    safe_upsert_panel(chat_id, truncate(text), kb_tasks(a, mem))
    set_chat_state(chat_id, screen="TASKS", awaiting=None)

def render_memory(chat_id):
    st = get_chat_state(chat_id)
    a = st["active_agent"]
    mem = load_json(MEMORY_PATH[a])
    notes = mem.get("notes", [])
    if not notes:
        text = f"🧠 Память отдела {a}\n\nПока пусто."
    else:
        last = notes[-30:]
        lines = [f"🧠 Память отдела {a}", ""]
        for n in last:
            lines.append(f"• {n}")
        text = "\n".join(lines)

    safe_upsert_panel(chat_id, truncate(text), kb_back())
    set_chat_state(chat_id, screen="MEMORY", awaiting=None)

def render_summary(chat_id):
    st = get_chat_state(chat_id)
    a = st["active_agent"]
    mem = load_json(MEMORY_PATH[a])

    notes = mem.get("notes", [])[-5:]
    inbox_open = [x for x in mem.get("inbox", []) if x.get("status") == "open"][:5]

    out = [f"🧾 Сводка отдела {a}", ""]
    out.append("🧠 Последние факты:")
    if notes:
        for n in notes:
            out.append(f"• {n}")
    else:
        out.append("• пусто")

    out.append("")
    out.append("📥 Открытые задачи (топ-5):")
    if inbox_open:
        for it in inbox_open:
            out.append(f"• {it.get('text','')}")
    else:
        out.append("• пусто")

    safe_upsert_panel(chat_id, truncate("\n".join(out)), kb_back())
    set_chat_state(chat_id, screen="SUMMARY", awaiting=None)

# -------------------- Optional OpenAI (only when key works) --------------------

def ask_openai(system, user):
    if not OPENAI_KEY:
        return None

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

# -------------------- Text dispatcher (kept) --------------------

def parse_target_agent(text, default_agent):
    t = text.strip()
    up = t.upper()
    for a in AGENTS:
        marker = f"@{a}"
        if marker in up:
            cleaned = up.replace(marker, "").strip()
            # restore original minus marker (simple approach)
            cleaned2 = t.replace(marker, "").replace(marker.lower(), "").strip()
            return a, cleaned2
    return default_agent, t

# -------------------- Routes --------------------

@app.get("/")
def health():
    return "OK", 200

@app.post("/webhook")
def webhook():
    update = request.get_json(silent=True) or {}

    # --- callbacks (inline buttons) ---
    if "callback_query" in update:
        cq = update["callback_query"]
        data = cq.get("data", "")
        cb_id = cq.get("id")
        msg = cq.get("message") or {}
        chat = msg.get("chat") or {}
        chat_id = chat.get("id")

        if not chat_id:
            if cb_id:
                answer_cb(cb_id)
            return "ok", 200

        st = get_chat_state(chat_id)

        # agent switching
        if data.startswith("agent:"):
            a = data.split(":", 1)[1].strip().upper()
            if a in AGENTS:
                set_chat_state(chat_id, active_agent=a)
                render_home(chat_id)
                if cb_id:
                    answer_cb(cb_id, f"Режим: {a}")
            else:
                if cb_id:
                    answer_cb(cb_id, "Неизвестный отдел.")
            return "ok", 200

        # views
        if data == "view:TASKS":
            render_tasks(chat_id)
            if cb_id:
                answer_cb(cb_id)
            return "ok", 200

        if data == "view:MEMORY":
            render_memory(chat_id)
            if cb_id:
                answer_cb(cb_id)
            return "ok", 200

        if data == "view:SUMMARY":
            render_summary(chat_id)
            if cb_id:
                answer_cb(cb_id)
            return "ok", 200

        if data == "view:ADD":
            render_add(chat_id)
            if cb_id:
                answer_cb(cb_id)
            return "ok", 200

        if data == "back:HOME":
            render_home(chat_id)
            if cb_id:
                answer_cb(cb_id)
            return "ok", 200

        # add modes
        if data == "add:TASK":
            set_chat_state(chat_id, awaiting="TASK", screen="ADD")
            a = get_chat_state(chat_id)["active_agent"]
            safe_upsert_panel(
                chat_id,
                f"➕ Новая задача в отдел {a}\n\nНапиши одним сообщением текст задачи.\n\nПример: Снять Reels про лазер",
                kb_back()
            )
            if cb_id:
                answer_cb(cb_id, "Жду текст задачи…")
            return "ok", 200

        if data == "add:FACT":
            set_chat_state(chat_id, awaiting="FACT", screen="ADD")
            a = get_chat_state(chat_id)["active_agent"]
            safe_upsert_panel(
                chat_id,
                f"➕ Новый факт в отдел {a}\n\nНапиши одним сообщением факт.\n\nПример: В LOOK нет администратора, запись через YCLIENTS",
                kb_back()
            )
            if cb_id:
                answer_cb(cb_id, "Жду факт…")
            return "ok", 200

        # close task
        if data.startswith("done:"):
            try:
                _, agent, idx = data.split(":")
                idx = int(idx)
                ok, info = close_task(agent, idx)
                if cb_id:
                    answer_cb(cb_id, "Закрыто ✅" if ok else "Не получилось")
                render_tasks(chat_id)
            except Exception:
                if cb_id:
                    answer_cb(cb_id, "Ошибка закрытия.")
            return "ok", 200

        if cb_id:
            answer_cb(cb_id)
        return "ok", 200

    # --- messages (text) ---
    msg = update.get("message") or update.get("edited_message") or {}
    chat = msg.get("chat") or {}
    chat_id = chat.get("id")
    text = msg.get("text") or msg.get("caption")

    if not chat_id:
        return "ok", 200

    t = (text or "").strip()

    # /start
    if t.lower() == "/start":
        # keep panel message stable
        set_chat_state(chat_id, screen="HOME", awaiting=None)
        render_home(chat_id)
        return "ok", 200

    st = get_chat_state(chat_id)

    # if awaiting input
    if st.get("awaiting") in ("TASK", "FACT") and t:
        a = st.get("active_agent", "CORE")
        if st["awaiting"] == "TASK":
            add_task(a, t)
            set_chat_state(chat_id, awaiting=None)
            render_tasks(chat_id)
        else:
            add_fact(a, t)
            set_chat_state(chat_id, awaiting=None)
            render_memory(chat_id)
        return "ok", 200

    # keep support for manual commands
    active = st.get("active_agent", "CORE")
    target_agent, cleaned = parse_target_agent(t, active)
    tt = cleaned.strip()

    if tt.lower().startswith("+задача:"):
        task_text = tt.split(":", 1)[1].strip()
        add_task(target_agent, task_text)
        render_tasks(chat_id)
        return "ok", 200

    if tt.lower().startswith("+факт:"):
        fact_text = tt.split(":", 1)[1].strip()
        add_fact(target_agent, fact_text)
        render_memory(chat_id)
        return "ok", 200

    if tt.lower() in ("?сводка", "/summary"):
        render_summary(chat_id)
        return "ok", 200

    # ordinary text: if OpenAI works -> answer, else keep UI
    mem = load_json(MEMORY_PATH[target_agent])
    answer = ask_openai(system_prompt(target_agent, mem), tt)

    if not answer:
        # Don't spam. Just show home screen and hint.
        safe_upsert_panel(
            chat_id,
            f"🏢 Katya AI Office\nАктивный отдел: {target_agent}\n\n"
            "Я могу вести задачи/память через кнопки.\n"
            "Ответы ИИ появятся, когда OpenAI ключ/лимиты точно активны.\n\n"
            "Нажми: ➕ Добавить / 📥 Задачи / 🧠 Память",
            kb_home(target_agent)
        )
        return "ok", 200

    # show answer in separate message (short), keep panel clean
    send_message(chat_id, truncate(answer, 3500), None)
    render_home(chat_id)
    return "ok", 200
