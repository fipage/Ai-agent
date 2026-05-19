import os
from openai import OpenAI
from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "").strip()
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()

client = OpenAI(api_key=OPENAI_API_KEY)

SYSTEM_PROMPT = """
Ты персональный AI-ассистент пользователя.

Твои задачи:
- помощь с YouTube контентом,
- поиск вирусных идей,
- анализ истории,
- критическое мышление,
- анализ источников,
- построение гипотез.

Отвечай по-русски.
Будь структурным и полезным.
"""

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "AI агент запущен ✅\nНапиши мне задачу."
    )

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        user_text = update.message.text

        response = client.responses.create(
            model="gpt-4.1-mini",
            input=[
                {
                    "role": "system",
                    "content": SYSTEM_PROMPT
                },
                {
                    "role": "user",
                    "content": user_text
                }
            ]
        )

        answer = response.output_text

        if not answer:
            answer = "Не удалось получить ответ."

        await update.message.reply_text(answer[:4000])

    except Exception as e:
        await update.message.reply_text(f"Ошибка: {str(e)}")

def main():
    if not OPENAI_API_KEY:
        raise ValueError("OPENAI_API_KEY missing")

    if not TELEGRAM_BOT_TOKEN:
        raise ValueError("TELEGRAM_BOT_TOKEN missing")

    app = ApplicationBuilder().token(
        TELEGRAM_BOT_TOKEN
    ).build()

    app.add_handler(CommandHandler("start", start))

    app.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            handle_message
        )
    )

    print("Telegram AI Agent Started")

    app.run_polling()

if __name__ == "__main__":
    main()
