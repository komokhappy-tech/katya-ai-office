import os
import requests
from flask import Flask, request

app = Flask(__name__)

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "").strip()
OPENAI_KEY = os.environ.get("OPENAI_KEY", "").strip()

TELEGRAM_API = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}"

# --- Prompts ---
CORE_PROMPT = (
    "Ты CORE.AI — маршрутизатор. Определи категорию сообщения.\n"
    "Если это про салон красоты LOOK (запись, мастера, услуги, акции, геосервисы, отзывы, контент салона) — ответь: LOOK.\n"
    "Если это про работу Кати как маркетолога (клиенты, стратегия, офферы, контент-план, воронки, продажи, обучение, проекты кроме LOOK) — ответь: MARKETING.\n"
    "Ответь строго одним словом: LOOK или MARKETING."
)

LOOK_PROMPT = (
    "Ты LOOK.AI — управляющий салоном красоты LOOK.\n"
    "Отвечай структурно:\n"
    "1) Что происходит\n"
    "2) Что делать (3–7 шагов)\n"
    "3) Что спросить/проверить дальше\n"
)

MARKETING_PROMPT = (
    "Ты MARKETING.AI — маркетинговый мозг Кати.\n"
    "Отвечай структурно:\n"
    "1) Диагностика\n"
    "2) Гипотезы\n"
    "3) План на 3–7 шагов\n"
    "4) Что нужно уточнить\n"
)

# --- Helpers ---
def send_message(chat_id: int, text: str):
    if not TELEGRAM_TOKEN:
        print("ERROR: TELEGRAM_TOKEN is missing")
        return
    try:
        requests.post(
            f"{TELEGRAM_API}/sendMessage",
            json={"chat_id": chat_id, "text": text},
            timeout=20,
        )
    except Exception as e:
        print(f"ERROR sending message: {e}")

def ask_openai(system_prompt: str, user_text: str) -> str:
    if not OPENAI_KEY:
        return "Похоже, не задан OPENAI_KEY в Render → Environment. Добавь ключ и сделай Deploy."

    try:
        r = requests.post(
            "https://api.openai.com/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {OPENAI_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "model": "gpt-4o-mini",
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_text},
                ],
                "temperature": 0.4,
            },
            timeout=60,
        )
        if r.status_code != 200:
            print("OPENAI ERROR:", r.status_code, r.text[:500])
            return "Упс. У меня ошибка доступа к модели (OpenAI). Проверь Billing/лимиты или ключ API."

        data = r.json()
        return (data.get("choices", [{}])[0].get("message", {}) or {}).get("content", "").strip() or "Пустой ответ модели."
    except Exception as e:
        print(f"OPENAI EXCEPTION: {e}")
        return "Упс. Не смогла дойти до OpenAI (ошибка сети/ключа/лимита)."

def extract_chat_and_text(update: dict):
    """
    Возвращает (chat_id, text) или (None, None), если это не текстовый апдейт.
    """
    # Обычное сообщение
    msg = update.get("message") or update.get("edited_message")
    if msg:
        chat = msg.get("chat") or {}
        chat_id = chat.get("id")

        # Текст
        text = msg.get("text")
        if text and chat_id:
            return chat_id, text

        # Команда /start часто тоже text, но на всякий:
        caption = msg.get("caption")  # если прислали фото с подписью
        if caption and chat_id:
            return chat_id, caption

        return None, None

    # Нажатия на кнопки (если добавишь)
    cq = update.get("callback_query")
    if cq:
        msg = cq.get("message") or {}
        chat = msg.get("chat") or {}
        chat_id = chat.get("id")
        data = cq.get("data")
        if chat_id and data:
            return chat_id, data
        return None, None

    return None, None


# --- Routes ---
@app.get("/")
def health():
    return "OK", 200


@app.post("/webhook")
def webhook():
    update = request.get_json(silent=True) or {}
    chat_id, text = extract_chat_and_text(update)

    # если это не текст — просто не падаем
    if not chat_id or not text:
        return "ok", 200

    # Базовый /start
    if text.strip().lower() in ("/start", "start"):
        send_message(
            chat_id,
            "Я на месте. Пиши как есть — я сам решу: LOOK это или MARKETING.\n"
            "Можно принудительно: #look ... или #mkt ...",
        )
        return "ok", 200

    # Принудительные теги
    t = text.strip()
    forced = None
    if t.lower().startswith("#look"):
        forced = "LOOK"
        t = t[5:].strip()
    elif t.lower().startswith("#mkt") or t.lower().startswith("#marketing"):
        forced = "MARKETING"
        t = t.split(" ", 1)[1].strip() if " " in t else ""

    # Маршрутизация
    route = forced or ask_openai(CORE_PROMPT, t)
    route_upper = route.upper()

    if "LOOK" in route_upper:
        answer = ask_openai(LOOK_PROMPT, t)
    else:
        answer = ask_openai(MARKETING_PROMPT, t)

    send_message(chat_id, answer)
    return "ok", 200

