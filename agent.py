import os
import json
import asyncio
import datetime as dt
import urllib.request
import urllib.parse
from typing import Any, Dict, List

from openai import OpenAI
from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

def clean_env(name: str) -> str:
    return (
        os.getenv(name, "")
        .replace("\\n", "")
        .replace("\n", "")
        .replace("\r", "")
        .strip()
    )

OPENAI_API_KEY = clean_env("OPENAI_API_KEY")
TELEGRAM_BOT_TOKEN = clean_env("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = clean_env("TELEGRAM_CHAT_ID")

DAILY_REPORT_TIME = clean_env("DAILY_REPORT_TIME") or "09:00"

YOUTUBE_API_KEY = clean_env("YOUTUBE_API_KEY")
YOUTUBE_CHANNEL_ID = clean_env("YOUTUBE_CHANNEL_ID")
X_BEARER_TOKEN = clean_env("X_BEARER_TOKEN")
COINGECKO_API_KEY = clean_env("COINGECKO_API_KEY")

CHEAP_MODEL = clean_env("CHEAP_MODEL") or "gpt-4.1-mini"
DEEP_MODEL = clean_env("DEEP_MODEL") or "gpt-4.1-mini"

MEMORY_FILE = "memory.json"
CACHE_FILE = "cache.json"

client = OpenAI(api_key=OPENAI_API_KEY)

SYSTEM_PROMPT = """
Ты AI-стратег для YouTube канала HiFi Trade.

Ниша:
- криптовалюты
- Bitcoin
- Ethereum
- альткоины
- инвестиции
- трейдинг
- market psychology
- macro
- narratives

Позиционирование:
спокойный, умный, адекватный crypto analyst.
Не screaming influencer, не инфоцыганство, не "1000x GEM".

Главная задача:
упаковывать сложный анализ в понятный YouTube-контент:
- выгода для зрителя
- риск
- tension
- timing
- понятные заголовки
- сильные hooks
- чистые превью
- retention

Правила:
- отвечай по-русски
- экономь токены
- без воды
- если данных мало, честно скажи
- разделяй факт, гипотезу и спекуляцию
- не давай финансовых гарантий
- не обещай доходность
"""

def load_json(path: str, default: Any) -> Any:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default

def save_json(path: str, data: Any) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def load_memory() -> Dict[str, Any]:
    return load_json(MEMORY_FILE, {
        "channel": "HiFi Trade",
        "style": "спокойный умный crypto analyst",
        "notes": [],
        "competitors": [
            "Benjamin Cowen",
            "Coin Bureau",
            "Altcoin Daily"
        ],
        "avoid": [
            "дешёвый кликбейт",
            "обещания иксов",
            "инфоцыганские формулировки"
        ]
    })

def save_memory(memory: Dict[str, Any]) -> None:
    save_json(MEMORY_FILE, memory)

def http_json(url: str, headers: Dict[str, str] | None = None, timeout: int = 20) -> Any:
    req = urllib.request.Request(url, headers=headers or {})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))

def get_coingecko_markets() -> List[Dict[str, Any]]:
    url = (
        "https://api.coingecko.com/api/v3/coins/markets?"
        + urllib.parse.urlencode({
            "vs_currency": "usd",
            "order": "market_cap_desc",
            "per_page": "20",
            "page": "1",
            "sparkline": "false",
            "price_change_percentage": "24h,7d"
        })
    )
    headers = {}
    if COINGECKO_API_KEY:
        headers["x-cg-demo-api-key"] = COINGECKO_API_KEY
    return http_json(url, headers=headers)

def get_youtube_channel_stats() -> Dict[str, Any]:
    if not YOUTUBE_API_KEY or not YOUTUBE_CHANNEL_ID:
        return {
            "error": "YOUTUBE_API_KEY или YOUTUBE_CHANNEL_ID не добавлены в Railway Variables."
        }

    url = (
        "https://www.googleapis.com/youtube/v3/channels?"
        + urllib.parse.urlencode({
            "part": "snippet,statistics,contentDetails",
            "id": YOUTUBE_CHANNEL_ID,
            "key": YOUTUBE_API_KEY
        })
    )
    return http_json(url)

def get_youtube_recent_videos() -> Dict[str, Any]:
    if not YOUTUBE_API_KEY or not YOUTUBE_CHANNEL_ID:
        return {
            "error": "YOUTUBE_API_KEY или YOUTUBE_CHANNEL_ID не добавлены в Railway Variables."
        }

    search_url = (
        "https://www.googleapis.com/youtube/v3/search?"
        + urllib.parse.urlencode({
            "part": "snippet",
            "channelId": YOUTUBE_CHANNEL_ID,
            "order": "date",
            "maxResults": "10",
            "type": "video",
            "key": YOUTUBE_API_KEY
        })
    )
    search_data = http_json(search_url)
    video_ids = ",".join([item["id"]["videoId"] for item in search_data.get("items", [])])

    if not video_ids:
        return search_data

    videos_url = (
        "https://www.googleapis.com/youtube/v3/videos?"
        + urllib.parse.urlencode({
            "part": "snippet,statistics,contentDetails",
            "id": video_ids,
            "key": YOUTUBE_API_KEY
        })
    )
    return http_json(videos_url)

def search_x_recent(query: str) -> Dict[str, Any]:
    if not X_BEARER_TOKEN:
        return {
            "error": "X_BEARER_TOKEN не добавлен в Railway Variables. Без него реальный X/Twitter мониторинг не работает."
        }

    url = (
        "https://api.twitter.com/2/tweets/search/recent?"
        + urllib.parse.urlencode({
            "query": query + " lang:en -is:retweet",
            "max_results": "10",
            "tweet.fields": "created_at,public_metrics,author_id"
        })
    )
    return http_json(url, headers={"Authorization": f"Bearer {X_BEARER_TOKEN}"})

async def reply(update: Update, text: str) -> None:
    if not update.message:
        return

    chunks = [text[i:i+3900] for i in range(0, len(text), 3900)] or ["Пустой ответ."]
    for chunk in chunks:
        await update.message.reply_text(chunk)

def gpt_text(prompt: str, model: str = CHEAP_MODEL, web: bool = False, max_output_tokens: int = 900) -> str:
    memory = load_memory()

    kwargs: Dict[str, Any] = {
        "model": model,
        "input": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "system", "content": "Память канала:\n" + json.dumps(memory, ensure_ascii=False)},
            {"role": "user", "content": prompt}
        ],
        "max_output_tokens": max_output_tokens,
    }

    if web:
        kwargs["tools"] = [{"type": "web_search_preview"}]

    response = client.responses.create(**kwargs)
    return response.output_text or "Нет ответа."

async def ask(update: Update, prompt: str, web: bool = False, deep: bool = False, max_tokens: int = 900) -> None:
    try:
        model = DEEP_MODEL if deep else CHEAP_MODEL
        answer = gpt_text(prompt, model=model, web=web, max_output_tokens=max_tokens)
        await reply(update, answer)
    except Exception as e:
        await reply(update, f"Ошибка: {str(e)}")

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = """
AI агент HiFi Trade запущен ✅

Главные команды:
/title тема — заголовки
/thumb тема — превью
/hook тема — первые 30 секунд
/short тема — Shorts
/weekly — контент-план недели
/trend — crypto narratives сейчас
/research тема — web research
/news — 5 новостей крипты и инвестиций
/evaluate текст — оценка заголовка/превью
/humanize текст — сделать проще
/yt — статистика канала через YouTube API
/competitors — идеи анализа конкурентов
/x тема — мониторинг X/Twitter, если добавлен API
/market — данные CoinGecko
/myid — узнать Telegram chat id
/remember текст — запомнить
/memory — показать память
/cheap текст — экономный ответ
/deep тема — глубокий анализ
/testreport — проверить утренний отчёт
"""
    await reply(update, text)

async def myid(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id if update.effective_chat else "unknown"
    await reply(update, f"Твой Telegram chat id:\n{chat_id}\n\nДобавь его в Railway Variables как TELEGRAM_CHAT_ID.")

async def remember(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = " ".join(context.args).strip()
    if not text:
        await reply(update, "Напиши так: /remember не использовать агрессивный кликбейт")
        return

    memory = load_memory()
    memory.setdefault("notes", []).append(text)
    save_memory(memory)
    await reply(update, "Запомнил ✅")

async def memory(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await reply(update, json.dumps(load_memory(), ensure_ascii=False, indent=2))

async def cheap(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = " ".join(context.args).strip()
    await ask(update, f"Ответь очень кратко и экономно:\n{query}", max_tokens=400)

async def deep(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = " ".join(context.args).strip()
    await ask(update, f"""
Сделай глубокий анализ для HiFi Trade.

Тема:
{query}

Структура:
1. Что происходит
2. Почему это важно зрителю
3. Short-term
4. Long-term
5. Риски
6. YouTube упаковка
7. 3 идеи роликов
""", deep=True, max_tokens=1400)

async def title(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = " ".join(context.args).strip()
    await ask(update, f"""
Придумай 12 YouTube заголовков для HiFi Trade.

Тема:
{query}

Правила:
- высокий CTR
- без дешёвого кликбейта
- спокойный умный crypto analyst
- фокус на выгоду/риск зрителя
- коротко
""", max_tokens=900)

async def thumb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = " ".join(context.args).strip()
    await ask(update, f"""
Придумай превью для ролика HiFi Trade.

Тема:
{query}

Дай:
1. Текст на превью, 2-4 слова
2. Главный визуальный объект
3. Композиция
4. Эмоция
5. Tension
6. Что убрать, чтобы не выглядело дешево
7. 3 альтернативы текста
""", max_tokens=1000)

async def hook(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = " ".join(context.args).strip()
    await ask(update, f"""
Напиши первые 30 секунд видео.

Тема:
{query}

Важно:
- без долгого интро
- сразу tension
- простым языком
- сказать, почему зрителю важно досмотреть
""", max_tokens=900)

async def short(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = " ".join(context.args).strip()
    await ask(update, f"""
Сделай Shorts 20-40 секунд.

Тема:
{query}

Формат:
- Hook
- 2-3 тезиса
- Вывод
- CTA без навязчивости
""", max_tokens=700)

async def weekly(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await ask(update, """
Сделай контент-план на неделю для HiFi Trade.

Нужно:
- 3 long-form ролика
- 7 shorts
- 5 заголовков
- 3 идеи превью
- что сейчас перегрето
- что недооценено
- что лучше не трогать
- план публикаций, учитывая видео каждый вторник

Кратко.
""", web=True, max_tokens=1400)

async def evaluate(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = " ".join(context.args).strip()
    await ask(update, f"""
Оцени упаковку для HiFi Trade.

Текст/идея:
{query}

Оцени по 10-балльной шкале:
1. CTR
2. Понятность
3. Tension
4. Доверие
5. Соответствие стилю канала

Потом дай улучшенную версию.
""", max_tokens=900)

async def humanize(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = " ".join(context.args).strip()
    await ask(update, f"""
Перепиши сложный crypto analysis простым человеческим языком.

Исходник:
{query}

Стиль:
спокойно, понятно, без инфоцыганства.
""", max_tokens=900)

async def trend(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await ask(update, """
Найди актуальные crypto narratives и темы для YouTube на сейчас.

Нужно:
- 5 narratives
- почему обсуждают
- какие темы могут зайти на YouTube
- какие темы перегреты
- 5 hooks для HiFi Trade
""", web=True, max_tokens=1400)

async def research(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = " ".join(context.args).strip()
    await ask(update, f"""
Сделай web research для HiFi Trade.

Тема:
{query}

Структура:
1. Что известно сейчас
2. Почему это обсуждают
3. Риски
4. Что может быть click-worthy
5. 5 идей роликов
6. 5 shorts
""", web=True, deep=True, max_tokens=1600)

async def news(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await ask(update, """
Найди 5 самых обсуждаемых новостей за последние 24 часа из мира крипты и инвестиций.

Для каждой:
- новость
- почему это важно
- влияние на BTC/альты/рынок
- идея ролика или Shorts
- уровень срочности: low/medium/high

Кратко, но полезно.
""", web=True, max_tokens=1400)

async def market(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        data = get_coingecko_markets()
        compact = [
            {
                "symbol": c.get("symbol", "").upper(),
                "price": c.get("current_price"),
                "24h%": c.get("price_change_percentage_24h"),
                "7d%": c.get("price_change_percentage_7d_in_currency"),
                "mcap_rank": c.get("market_cap_rank")
            }
            for c in data[:15]
        ]

        prompt = f"""
Вот свежие данные CoinGecko по топ-монетам:
{json.dumps(compact, ensure_ascii=False)}

Сделай краткий market brief для HiFi Trade:
- что заметно
- где риск
- где потенциальная тема для видео
- 5 заголовков
"""
        answer = gpt_text(prompt, max_output_tokens=1000)
        await reply(update, answer)
    except Exception as e:
        await reply(update, f"Ошибка market: {str(e)}")

async def yt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        stats = get_youtube_channel_stats()
        recent = get_youtube_recent_videos()

        prompt = f"""
Проанализируй YouTube канал HiFi Trade по API данным.

Channel stats:
{json.dumps(stats, ensure_ascii=False)[:3000]}

Recent videos:
{json.dumps(recent, ensure_ascii=False)[:5000]}

Дай:
1. Что работает
2. Что слабое
3. Идеи по CTR
4. Идеи по retention
5. 5 следующих видео
"""
        answer = gpt_text(prompt, max_output_tokens=1400)
        await reply(update, answer)
    except Exception as e:
        await reply(update, f"Ошибка YouTube API: {str(e)}")

async def competitors(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = " ".join(context.args).strip() or "crypto YouTube BTC altcoins market analysis"
    await ask(update, f"""
Сделай competitor/content gap analysis для HiFi Trade.

Поиск по теме:
{query}

Нужно:
- какие темы часто используют конкуренты
- где есть content gap
- что можно сделать спокойнее и умнее
- 5 long-form идей
- 10 shorts
""", web=True, max_tokens=1400)

async def x_monitor(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = " ".join(context.args).strip() or "Bitcoin OR Ethereum OR crypto"
    try:
        data = search_x_recent(query)
        if "error" in data:
            await reply(update, data["error"])
            return

        prompt = f"""
Вот свежие tweets по теме {query}:
{json.dumps(data, ensure_ascii=False)[:5000]}

Сделай:
- 5 обсуждаемых тем
- sentiment
- что может стать роликом
- что перегрето
"""
        answer = gpt_text(prompt, max_output_tokens=1200)
        await reply(update, answer)
    except Exception as e:
        await reply(update, f"Ошибка X monitoring: {str(e)}")

def parse_report_time() -> tuple[int, int]:
    try:
        h, m = DAILY_REPORT_TIME.split(":")
        return int(h), int(m)
    except Exception:
        return 9, 0

async def send_daily_report(app):
    if not TELEGRAM_CHAT_ID:
        print("TELEGRAM_CHAT_ID missing: daily reports disabled")
        return

    try:
        answer = gpt_text("""
Сформируй утренний отчёт для HiFi Trade.

Нужно 5 самых обсуждаемых новостей за последние 24 часа из крипты и инвестиций.

Для каждой:
1. Новость
2. Почему обсуждают
3. Влияние на рынок
4. Идея для ролика/Shorts
5. Срочность

В конце:
- 3 темы для видео
- 5 тем для Shorts
- что лучше не трогать сегодня

Кратко.
""", web=True, max_output_tokens=1600)

        await app.bot.send_message(chat_id=int(TELEGRAM_CHAT_ID), text=answer[:3900])
    except Exception as e:
        try:
            await app.bot.send_message(chat_id=int(TELEGRAM_CHAT_ID), text=f"Ошибка утреннего отчёта: {str(e)}")
        except Exception:
            print("Daily report error:", e)

async def daily_scheduler(app):
    sent_dates = set()
    hour, minute = parse_report_time()

    while True:
        now = dt.datetime.now()
        today_key = now.strftime("%Y-%m-%d")

        if now.hour == hour and now.minute == minute and today_key not in sent_dates:
            await send_daily_report(app)
            sent_dates.add(today_key)

        await asyncio.sleep(30)

async def test_report(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await reply(update, "Готовлю тестовый утренний отчёт...")
    await send_daily_report(context.application)

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await ask(update, update.message.text, max_tokens=900)

async def post_init(app):
    asyncio.create_task(daily_scheduler(app))
    print("Daily scheduler started")

def main():
    if not OPENAI_API_KEY:
        raise ValueError("OPENAI_API_KEY missing")
    if not TELEGRAM_BOT_TOKEN:
        raise ValueError("TELEGRAM_BOT_TOKEN missing")

    app = (
        ApplicationBuilder()
        .token(TELEGRAM_BOT_TOKEN)
        .post_init(post_init)
        .build()
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("myid", myid))
    app.add_handler(CommandHandler("remember", remember))
    app.add_handler(CommandHandler("memory", memory))
    app.add_handler(CommandHandler("cheap", cheap))
    app.add_handler(CommandHandler("deep", deep))

    app.add_handler(CommandHandler("title", title))
    app.add_handler(CommandHandler("thumb", thumb))
    app.add_handler(CommandHandler("hook", hook))
    app.add_handler(CommandHandler("short", short))
    app.add_handler(CommandHandler("weekly", weekly))
    app.add_handler(CommandHandler("evaluate", evaluate))
    app.add_handler(CommandHandler("humanize", humanize))

    app.add_handler(CommandHandler("trend", trend))
    app.add_handler(CommandHandler("research", research))
    app.add_handler(CommandHandler("news", news))
    app.add_handler(CommandHandler("market", market))
    app.add_handler(CommandHandler("yt", yt))
    app.add_handler(CommandHandler("competitors", competitors))
    app.add_handler(CommandHandler("x", x_monitor))
    app.add_handler(CommandHandler("testreport", test_report))

    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    print("HiFi Trade AI Agent v3 started")
    app.run_polling()

if __name__ == "__main__":
    main()
