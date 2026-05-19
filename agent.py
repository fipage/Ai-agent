import os
import json
from openai import OpenAI
from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

OPENAI_API_KEY = (
    os.getenv("OPENAI_API_KEY", "")
    .replace("\\n", "")
    .replace("\n", "")
    .replace("\r", "")
    .strip()
)

TELEGRAM_BOT_TOKEN = (
    os.getenv("TELEGRAM_BOT_TOKEN", "")
    .replace("\\n", "")
    .replace("\n", "")
    .replace("\r", "")
    .strip()
)

client = OpenAI(api_key=OPENAI_API_KEY)

MODEL = "gpt-4.1-mini"

SYSTEM_PROMPT = """
Ты AI-стратег для YouTube канала HiFi Trade.

Темы:
- крипта
- Bitcoin
- Ethereum
- альткоины
- трейдинг
- market psychology
- crypto narratives

Главная задача:
упаковывать сложный анализ
в понятный интересный контент.

Важно:
- отвечай коротко
- экономь API токены
- без воды
- делай сильные hooks
- делай понятные заголовки
- думай как YouTube strategist

Стиль:
спокойный умный crypto analyst,
а не screaming influencer.
"""

MEMORY_FILE = "memory.json"

def load_memory():
    try:
        with open(MEMORY_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except:
        return {
            "notes": []
        }

def save_memory(memory):
    with open(MEMORY_FILE, "w", encoding="utf-8") as f:
        json.dump(memory, f, ensure_ascii=False, indent=2)

async def ask_gpt(update, prompt):
    try:
        memory = load_memory()

        response = client.responses.create(
            model=MODEL,
            input=[
                {
                    "role": "system",
                    "content": SYSTEM_PROMPT
                },
                {
                    "role": "system",
                    "content": f"Память:\n{json.dumps(memory, ensure_ascii=False)}"
                },
                {
                    "role": "user",
                    "content": prompt
                }
            ]
        )

        answer = response.output_text

        if not answer:
            answer = "Нет ответа."

        await update.message.reply_text(answer[:4000])

    except Exception as e:
        await update.message.reply_text(f"Ошибка: {str(e)}")

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = """
AI агент запущен ✅

Команды:

/title
/thumb
/hook
/short
/weekly
/remember
/memory
/deep
"""

    await update.message.reply_text(text)

async def title(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = " ".join(context.args)

    prompt = f"""
Придумай 10 сильных YouTube заголовков.

Тема:
{query}

Правила:
- высокий CTR
- без дешевого кликбейта
- стиль умного crypto канала
- коротко
"""

    await ask_gpt(update, prompt)

async def thumb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = " ".join(context.args)

    prompt = f"""
Придумай идею YouTube превью.

Тема:
{query}

Опиши:
- главный текст
- эмоцию
- композицию
- tension
- что должно быть на экране

Стиль:
дорогой crypto analysis,
а не дешёвый инфоцыганский thumbnail.
"""

    await ask_gpt(update, prompt)

async def hook(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = " ".join(context.args)

    prompt = f"""
Напиши первые 30 секунд YouTube ролика.

Тема:
{query}

Важно:
- сразу hook
- сразу tension
- просто и понятно
- без сложных терминов в начале
"""

    await ask_gpt(update, prompt)

async def short(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = " ".join(context.args)

    prompt = f"""
Сделай Shorts сценарий 20-40 секунд.

Тема:
{query}

Формат:
- 1 мысль
- 1 hook
- 1 вывод

Без интро.
"""

    await ask_gpt(update, prompt)

async def weekly(update: Update, context: ContextTypes.DEFAULT_TYPE):
    prompt = """
Сделай контент-план на неделю для crypto YouTube канала HiFi Trade.

Нужно:
- 3 long-form идеи
- 7 shorts
- narratives недели
- идеи заголовков
- что сейчас перегрето
- что недооценено

Кратко.
"""

    await ask_gpt(update, prompt)

async def deep(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = " ".join(context.args)

    prompt = f"""
Сделай глубокий анализ.

Тема:
{query}

Раздели:
- short-term
- long-term
- risks
- opportunities
- narratives
"""

    await ask_gpt(update, prompt)

async def remember(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = " ".join(context.args)

    if not text:
        await update.message.reply_text("Напиши текст после /remember")
        return

    memory = load_memory()
    memory["notes"].append(text)
    save_memory(memory)

    await update.message.reply_text("Запомнил ✅")

async def memory(update: Update, context: ContextTypes.DEFAULT_TYPE):
    memory = load_memory()

    await update.message.reply_text(
        json.dumps(memory, ensure_ascii=False, indent=2)[:4000]
    )

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await ask_gpt(update, update.message.text)

def main():
    app = ApplicationBuilder().token(
        TELEGRAM_BOT_TOKEN
    ).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("title", title))
    app.add_handler(CommandHandler("thumb", thumb))
    app.add_handler(CommandHandler("hook", hook))
    app.add_handler(CommandHandler("short", short))
    app.add_handler(CommandHandler("weekly", weekly))
    app.add_handler(CommandHandler("deep", deep))
    app.add_handler(CommandHandler("remember", remember))
    app.add_handler(CommandHandler("memory", memory))

    app.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            handle_message
        )
    )

    print("AI Agent Started")

    app.run_polling()

if __name__ == "__main__":
    main()
