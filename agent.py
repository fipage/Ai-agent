import os
import json
from datetime import datetime
from openai import OpenAI
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, ContextTypes, filters

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "").replace("\\n", "").replace("\n", "").replace("\r", "").strip()
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").replace("\\n", "").replace("\n", "").replace("\r", "").strip()

client = OpenAI(api_key=OPENAI_API_KEY)

CHEAP_MODEL = os.getenv("CHEAP_MODEL", "gpt-4.1-mini")
DEEP_MODEL = os.getenv("DEEP_MODEL", "gpt-4.1-mini")
MEMORY_FILE = "memory.json"

SYSTEM_PROMPT = """
Ты AI-стратег и продюсер YouTube-канала HiFi Trade.
Тематика: криптовалюты, BTC, ETH, альткоины, трейдинг, рыночная психология, narrative rotations.

Главная стратегия канала:
- спокойный умный crypto analyst, не screaming influencer;
- не продавать “анализ”, а объяснять зрителю риск/выгоду для него;
- сложную технику переводить в простую человеческую мысль;
- меньше инфоцыганского кликбейта, больше напряжения, конкретики и доверия;
- фокус: заголовок, превью, первые 30 секунд, shorts, weekly content plan.

Всегда экономь кредиты:
- отвечай компактно;
- не пиши длинные рассуждения без команды /deep или /research;
- давай готовые формулировки, а не теорию.
"""

def load_memory():
    default = {
        "channel": "HiFi Trade",
        "positioning": "спокойный умный crypto analyst без дешевого кликбейта",
        "style_rules": [
            "говорить простым языком",
            "упаковывать анализ через риск/выгоду для зрителя",
            "не использовать screaming influencer стиль",
            "делать акцент на BTC levels, market psychology, narratives"
        ],
        "notes": []
    }
    try:
        with open(MEMORY_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            for k, v in default.items():
                data.setdefault(k, v)
            return data
    except Exception:
        return default

def save_memory(memory):
    with open(MEMORY_FILE, "w", encoding="utf-8") as f:
        json.dump(memory, f, ensure_ascii=False, indent=2)

def memory_blob():
    return json.dumps(load_memory(), ensure_ascii=False)

async def reply(update, text):
    if not text:
        text = "Пустой ответ."
    chunks = [text[i:i+3900] for i in range(0, len(text), 3900)]
    for chunk in chunks[:3]:
        await update.message.reply_text(chunk)

async def ask_ai(update, prompt, *, deep=False, web=False, max_tokens=900):
    try:
        model = DEEP_MODEL if deep else CHEAP_MODEL
        kwargs = {
            "model": model,
            "input": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "system", "content": f"Память канала: {memory_blob()}"},
                {"role": "user", "content": prompt},
            ],
            "max_output_tokens": max_tokens,
        }
        if web:
            kwargs["tools"] = [{"type": "web_search"}]
        response = client.responses.create(**kwargs)
        await reply(update, response.output_text or "Нет ответа от модели.")
    except Exception as e:
        await reply(update, f"Ошибка: {str(e)}")

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await reply(update, """AI агент HiFi Trade запущен ✅

Команды:
/title тема — заголовки
/thumb тема — превью
/hook тема — первые 30 секунд
/short тема — Shorts
/weekly — план недели
/trend — свежие crypto narratives через web search
/research тема — глубокий анализ с web search
/evaluate текст — оценка заголовка/превью/идеи
/humanize текст — упростить сложный анализ
/remember текст — запомнить
/memory — показать память
/cheap тема — самый дешёвый короткий ответ
/deep тема — глубокий ответ без web search""")

async def title(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = " ".join(context.args).strip()
    await ask_ai(update, f"Сделай 10 YouTube заголовков для HiFi Trade. Тема: {q}\nПравила: высокий CTR, без дешевого кликбейта, коротко. Для каждого дай оценку 1-10.", max_tokens=900)

async def thumb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = " ".join(context.args).strip()
    await ask_ai(update, f"Сделай 5 идей превью для ролика HiFi Trade. Тема: {q}\nДля каждой: текст на превью, композиция, эмоция, главный конфликт, почему кликнут.", max_tokens=1000)

async def hook(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = " ".join(context.args).strip()
    await ask_ai(update, f"Напиши первые 30 секунд ролика. Тема: {q}\nСразу tension, просто, без терминов, с выгодой/риском для зрителя.", max_tokens=700)

async def short(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = " ".join(context.args).strip()
    await ask_ai(update, f"Сделай сценарий Shorts 20-40 секунд. Тема: {q}\nФормат: hook -> 1 мысль -> вывод. Без интро.", max_tokens=700)

async def weekly(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await ask_ai(update, "Сделай недельный контент-план HiFi Trade: 3 long-form, 7 shorts, 3 идеи превью, 5 заголовков, что перегрето, что недооценено. Кратко.", max_tokens=1200)

async def trend(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await ask_ai(update, "Найди свежие crypto narratives и темы для YouTube на ближайшие 7 дней. Раздели: hype now, evergreen, risky, content gap. Дай 10 идей роликов для HiFi Trade.", web=True, max_tokens=1400)

async def research(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = " ".join(context.args).strip()
    await ask_ai(update, f"Сделай глубокий web research для HiFi Trade по теме: {q}\nНужно: факты, narratives, риски, идеи роликов, заголовки, что сказать в первых 30 сек.", deep=True, web=True, max_tokens=1800)

async def evaluate(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = " ".join(context.args).strip()
    await ask_ai(update, f"Оцени идею/заголовок/превью как топовый YouTube marketer. Материал: {q}\nДай оценку CTR, ясность, tension, слабые места, финальную улучшенную версию.", max_tokens=900)

async def humanize(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = " ".join(context.args).strip()
    await ask_ai(update, f"Перепиши сложный crypto-анализ простым человеческим языком для зрителя YouTube. Текст: {q}\nСохрани смысл, добавь риск/выгоду, убери лишние термины.", max_tokens=900)

async def cheap(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = " ".join(context.args).strip()
    await ask_ai(update, f"Ответь очень кратко и полезно: {q}", max_tokens=350)

async def deep(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = " ".join(context.args).strip()
    await ask_ai(update, f"Сделай глубокий анализ без веб-поиска: {q}\nРаздели: short-term, long-term, risks, opportunities, YouTube angle.", deep=True, max_tokens=1600)

async def remember(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = " ".join(context.args).strip()
    if not text:
        await reply(update, "Напиши так: /remember стиль канала — спокойный аналитик")
        return
    m = load_memory()
    m.setdefault("notes", []).append({"date": datetime.utcnow().isoformat(), "text": text})
    save_memory(m)
    await reply(update, "Запомнил ✅")

async def memory(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await reply(update, json.dumps(load_memory(), ensure_ascii=False, indent=2))

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await ask_ai(update, update.message.text, max_tokens=900)

def main():
    if not OPENAI_API_KEY:
        raise ValueError("OPENAI_API_KEY missing")
    if not TELEGRAM_BOT_TOKEN:
        raise ValueError("TELEGRAM_BOT_TOKEN missing")

    app = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).build()
    for name, fn in {
        "start": start,
        "title": title,
        "thumb": thumb,
        "hook": hook,
        "short": short,
        "weekly": weekly,
        "trend": trend,
        "research": research,
        "evaluate": evaluate,
        "humanize": humanize,
        "cheap": cheap,
        "deep": deep,
        "remember": remember,
        "memory": memory,
    }.items():
        app.add_handler(CommandHandler(name, fn))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    print("HiFi Trade AI Agent Started")
    app.run_polling()

if __name__ == "__main__":
    main()
