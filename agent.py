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

CHEAP_MODEL = "gpt-4.1-mini"
MEMORY_FILE = "memory.json"

client = OpenAI(api_key=OPENAI_API_KEY)

SYSTEM_PROMPT = """
Ты персональный AI-агент для YouTube-канала HiFi Trade.

Тематика канала:
- криптовалюты,
- трейдинг,
- инвестиции,
- Bitcoin,
- Ethereum,
- альткоины,
- meme coins,
- AI coins,
- RWA,
- DePIN,
- GameFi,
- macro,
- рыночные циклы,
- риск-менеджмент.

Главная задача:
помогать владельцу канала находить темы для YouTube, которые могут дать просмотры, удержание и рост канала.

Ты работаешь как:
1. YouTube-стратег.
2. Crypto market analyst.
3. Research assistant.
4. Редактор заголовков и хуков.
5. Экономный AI-ассистент.

Правила:
- Отвечай по-русски.
- Отвечай компактно, без воды.
- Не давай финансовых гарантий.
- Всегда разделяй: хайп / долгосрок / риск.
- Для идей роликов давай: тема, заголовок, hook, почему зайдёт, риск.
- Если информации мало — задавай 1 короткий уточняющий вопрос.
- Не обещай точный рост монет.
- Не выдавай спекуляции за факт.
"""

DEFAULT_MEMORY = {
    "channel": "HiFi Trade",
    "channel_url": "https://www.youtube.com/@hifitrade",
    "niche": "crypto, trading, investing, YouTube content",
    "style": "практичный крипто-анализ, идеи роликов, хайповые и долгосрочные темы",
    "important_notes": []
}


def load_memory():
    try:
        with open(MEMORY_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            for key, value in DEFAULT_MEMORY.items():
                if key not in data:
                    data[key] = value
            return data
    except Exception:
        save_memory(DEFAULT_MEMORY)
        return DEFAULT_MEMORY.copy()


def save_memory(memory):
    with open(MEMORY_FILE, "w", encoding="utf-8") as f:
        json.dump(memory, f, ensure_ascii=False, indent=2)


def memory_text():
    return json.dumps(load_memory(), ensure_ascii=False, indent=2)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "AI агент HiFi Trade запущен ✅\n\n"
        "Команды:\n"
        "/youtube запрос — идеи для роликов\n"
        "/trend запрос — хайповые темы\n"
        "/evergreen запрос — долгосрочные темы\n"
        "/coin запрос — идея/анализ монеты\n"
        "/title запрос — заголовки и хуки\n"
        "/cheap запрос — короткий экономный ответ\n"
        "/remember текст — запомнить\n"
        "/memory — показать память"
    )


async def show_memory(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(memory_text()[:4000])


async def remember(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = " ".join(context.args).strip()
    if not text:
        await update.message.reply_text("Пример: /remember аудитория канала — новички и средний уровень в крипте")
        return

    memory = load_memory()
    memory.setdefault("important_notes", []).append(text)
    save_memory(memory)

    await update.message.reply_text("Запомнил ✅")


async def youtube(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = " ".join(context.args).strip()
    if not query:
        query = "дай 10 идей для роликов по крипте для канала HiFi Trade"
    await ask_ai(update, query, mode="youtube")


async def trend(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = " ".join(context.args).strip()
    if not query:
        query = "какие крипто-темы могут быть хайповыми на YouTube в ближайшие дни"
    await ask_ai(update, query, mode="trend")


async def evergreen(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = " ".join(context.args).strip()
    if not query:
        query = "какие долгосрочные темы по крипте стоит делать на канал"
    await ask_ai(update, query, mode="evergreen")


async def coin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = " ".join(context.args).strip()
    if not query:
        await update.message.reply_text("Пример: /coin SOL стоит ли делать ролик?")
        return
    await ask_ai(update, query, mode="coin")


async def title(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = " ".join(context.args).strip()
    if not query:
        await update.message.reply_text("Пример: /title ролик про альтсезон")
        return
    await ask_ai(update, query, mode="title")


async def cheap(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = " ".join(context.args).strip()
    if not query:
        await update.message.reply_text("Пример: /cheap дай 5 идей для роликов")
        return
    await ask_ai(update, query, mode="cheap")


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await ask_ai(update, update.message.text, mode="normal")


def build_mode_prompt(mode: str) -> str:
    if mode == "youtube":
        return (
            "Режим YouTube-стратега. Дай идеи роликов. Формат: "
            "1) тема 2) заголовок 3) hook 4) почему зайдёт 5) риск. "
            "До 10 идей, компактно."
        )
    if mode == "trend":
        return (
            "Режим хайп-трендов. Найди темы, которые могут быть актуальны прямо сейчас. "
            "Раздели на: срочно снять / можно подождать / рискованно."
        )
    if mode == "evergreen":
        return (
            "Режим evergreen. Дай темы, которые будут набирать просмотры долго. "
            "Укажи почему тема не устареет."
        )
    if mode == "coin":
        return (
            "Режим анализа монеты/нарратива. Не давай финансовых гарантий. "
            "Формат: тезис, потенциал для видео, риски, возможный заголовок."
        )
    if mode == "title":
        return (
            "Режим редактора. Дай 10 кликабельных, но не мошеннических заголовков "
            "и 5 hooks для начала ролика."
        )
    if mode == "cheap":
        return "Экономный режим. Ответь очень кратко, максимум 500 символов."
    return "Обычный режим. Отвечай полезно и компактно."


async def ask_ai(update: Update, user_text: str, mode: str):
    try:
        response = client.responses.create(
            model=CHEAP_MODEL,
            input=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "system", "content": f"Память пользователя:\n{memory_text()}"},
                {"role": "system", "content": build_mode_prompt(mode)},
                {"role": "user", "content": user_text},
            ],
        )

        answer = response.output_text or "Не удалось получить ответ."
        await update.message.reply_text(answer[:4000])

    except Exception as e:
        await update.message.reply_text(f"Ошибка: {str(e)}")


def main():
    if not OPENAI_API_KEY:
        raise ValueError("OPENAI_API_KEY missing")

    if not TELEGRAM_BOT_TOKEN:
        raise ValueError("TELEGRAM_BOT_TOKEN missing")

    load_memory()

    app = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("memory", show_memory))
    app.add_handler(CommandHandler("remember", remember))
    app.add_handler(CommandHandler("youtube", youtube))
    app.add_handler(CommandHandler("trend", trend))
    app.add_handler(CommandHandler("evergreen", evergreen))
    app.add_handler(CommandHandler("coin", coin))
    app.add_handler(CommandHandler("title", title))
    app.add_handler(CommandHandler("cheap", cheap))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    print("HiFi Trade Telegram AI Agent Started")
    app.run_polling()


if __name__ == "__main__":
    main()
