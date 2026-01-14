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

# --- Helpers
