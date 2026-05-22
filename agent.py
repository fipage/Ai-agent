import os
import json
import asyncio
import requests
import feedparser
from datetime import datetime
from zoneinfo import ZoneInfo
from openai import OpenAI
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, ContextTypes, filters

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "").replace("\\n", "").replace("\n", "").replace("\r", "").strip()
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").replace("\\n", "").replace("\n", "").replace("\r", "").strip()
YOUTUBE_API_KEY = os.getenv("YOUTUBE_API_KEY", "").replace("\\n", "").replace("\n", "").replace("\r", "").strip()

client = OpenAI(api_key=OPENAI_API_KEY)

MEMORY_FILE = "memory.json"
SUCCESS_FILE = "success_memory.json"

MODEL_CHEAP = "gpt-4.1-mini"
MAX_OUTPUT_TOKENS = 1200

SYSTEM_PROMPT = """
Ты не обычный Telegram-бот.

Ты — AI-команда продвижения YouTube-канала HiFi Trade.

Твои роли:
1. Crypto/Macro Research Analyst
2. YouTube Growth Strategist
3. RU/CIS Audience Sentiment Analyst
4. Competitor Intelligence Analyst
5. Scriptwriter
6. Thumbnail Strategist
7. Shorts Producer
8. Content Calendar Assistant

Главная цель:
помогать владельцу канала HiFi Trade экономить время, находить сильные темы,
понимать интерес аудитории, анализировать настроение блогеров и рынка,
выдавать готовые идеи для роликов и Shorts.

Фокус канала:
- Bitcoin
- Ethereum
- crypto market cycles
- macro
- liquidity
- ETF flows
- regulation
- institutional adoption
- risk management
- market psychology
- serious investing/trading

Строго запрещено предлагать:
- мемкоины
- скам-монеты
- random pumps
- low-cap garbage
- "100x"
- "срочно покупай"
- мусорные листинги
- хайп без фундаментальной причины

Все идеи должны быть основаны на:
- настроении людей
- интересе аудитории
- интересе блогеров
- анализе YouTube-конкуренции
- недопокрытых темах
- серьёзных market narratives

Если даёшь идею ролика:
обязательно дай:
1. Название
2. Почему зритель кликнет
3. Хук первых 10 секунд
4. Краткий текст/структуру ролика
5. 3 варианта превью
6. Текст на превью
7. Почему тема может привести подписчиков
8. Почему это не мусор

Если даёшь идею Shorts:
обязательно дай:
1. Тему
2. Первую фразу
3. Текст Shorts
4. Превью/обложку
5. Текст на превью
6. Почему это сработает

Стиль превью:
строгий, чистый, без грязи, без перегруза, понятный зрителю,
с интригой, но без дешёвого хайпа.

Отвечай по-русски.
Кратко, но достаточно полезно.
"""

def load_json(path, default):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default

def save_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def load_memory():
    return load_json(MEMORY_FILE, {})

def save_memory(memory):
    save_json(MEMORY_FILE, memory)

def load_success():
    return load_json(SUCCESS_FILE, {
        "successful_videos": [],
        "used_ideas": [],
        "weekly_notes": []
    })

def save_success(data):
    save_json(SUCCESS_FILE, data)

def get_timezone():
    memory = load_memory()
    return ZoneInfo(memory.get("report_timezone", "Europe/Moscow"))

def extract_video_id(url):
    if "youtu.be/" in url:
        return url.split("youtu.be/")[1].split("?")[0].split("/")[0]
    if "v=" in url:
        return url.split("v=")[1].split("&")[0]
    return None

def get_video_data(video_url):
    video_id = extract_video_id(video_url)
    if not video_id:
        return None

    api_url = "https://www.googleapis.com/youtube/v3/videos"
    params = {
        "part": "snippet,statistics",
        "id": video_id,
        "key": YOUTUBE_API_KEY
    }

    r = requests.get(api_url, params=params, timeout=20)
    data = r.json()
    items = data.get("items", [])
    return items[0] if items else None

def get_channel_id_from_video(video_url):
    video = get_video_data(video_url)
    if not video:
        return None
    return video["snippet"]["channelId"]

def get_channel_videos(channel_id, max_results=15):
    search_url = "https://www.googleapis.com/youtube/v3/search"
    search_params = {
        "part": "snippet",
        "channelId": channel_id,
        "maxResults": max_results,
        "order": "date",
        "type": "video",
        "key": YOUTUBE_API_KEY
    }

    r = requests.get(search_url, params=search_params, timeout=20)
    data = r.json()

    video_ids = [item["id"]["videoId"] for item in data.get("items", [])]
    if not video_ids:
        return []

    stats_url = "https://www.googleapis.com/youtube/v3/videos"
    stats_params = {
        "part": "snippet,statistics",
        "id": ",".join(video_ids),
        "key": YOUTUBE_API_KEY
    }

    r = requests.get(stats_url, params=stats_params, timeout=20)
    data = r.json()

    videos = []
    for item in data.get("items", []):
        stats = item.get("statistics", {})
        snippet = item.get("snippet", {})
        thumbnails = snippet.get("thumbnails", {})
        videos.append({
            "title": snippet.get("title", ""),
            "description": snippet.get("description", "")[:600],
            "publishedAt": snippet.get("publishedAt", ""),
            "views": int(stats.get("viewCount", 0)),
            "likes": int(stats.get("likeCount", 0)) if "likeCount" in stats else 0,
            "comments": int(stats.get("commentCount", 0)) if "commentCount" in stats else 0,
            "thumbnail": thumbnails.get("high", thumbnails.get("default", {})).get("url", "")
        })

    return videos

def youtube_search(query, max_results=7):
    if not YOUTUBE_API_KEY:
        return []

    url = "https://www.googleapis.com/youtube/v3/search"
    params = {
        "part": "snippet",
        "q": query,
        "maxResults": max_results,
        "order": "relevance",
        "type": "video",
        "key": YOUTUBE_API_KEY
    }

    r = requests.get(url, params=params, timeout=20)
    data = r.json()

    results = []
    for item in data.get("items", []):
        results.append({
            "title": item["snippet"]["title"],
            "channel": item["snippet"]["channelTitle"],
            "publishedAt": item["snippet"]["publishedAt"]
        })
    return results

def get_rss_news():
    feeds = [
        "https://news.google.com/rss/search?q=bitcoin+crypto+market+investing&hl=ru&gl=RU&ceid=RU:ru",
        "https://news.google.com/rss/search?q=биткоин+криптовалюта+инвестиции+рынок&hl=ru&gl=RU&ceid=RU:ru",
        "https://www.coindesk.com/arc/outboundfeeds/rss/",
        "https://cointelegraph.com/rss"
    ]

    news = []
    for feed_url in feeds:
        try:
            feed = feedparser.parse(feed_url)
            for entry in feed.entries[:8]:
                news.append({
                    "title": entry.get("title", ""),
                    "link": entry.get("link", ""),
                    "published": entry.get("published", "")
                })
        except Exception:
            continue

    return news[:25]

def ask_ai(prompt, max_chars=3500):
    memory = load_memory()
    success = load_success()

    compact_prompt = f"""
Память канала:
{json.dumps(memory, ensure_ascii=False, indent=2)}

Память успешных роликов и использованных идей:
{json.dumps(success, ensure_ascii=False, indent=2)}

Правила:
- не предлагай мемкоины, скам и low-cap мусор
- думай как команда продвижения канала
- оценивай идеи через интерес аудитории и YouTube growth
- если тема слабая, скажи прямо
- ищи недопокрытые темы
- давай готовые практичные решения

Запрос:
{prompt}
"""

    response = client.responses.create(
        model=MODEL_CHEAP,
        max_output_tokens=MAX_OUTPUT_TOKENS,
        input=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": compact_prompt}
        ]
    )

    text = response.output_text or "Не удалось получить ответ."
    return text[:max_chars]

async def send_long(context, chat_id, text):
    if not text:
        text = "Пустой ответ."
    for i in range(0, len(text), 3500):
        await context.bot.send_message(chat_id=chat_id, text=text[i:i+3500])

async def reply_long(update, text):
    if not text:
        text = "Пустой ответ."
    for i in range(0, len(text), 3500):
        await update.message.reply_text(text[i:i+3500])

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "HiFi Trade AI Growth Team запущен ✅\n\n"
        "Сначала отправь:\n"
        "/setchat\n\n"
        "Команды:\n"
        "/morning_now — новости и идеи на сегодня\n"
        "/bloggers_now — настроение RU/CIS блогеров\n"
        "/videoidea — идея большого ролика\n"
        "/shortidea — идея Shorts\n"
        "/channel — анализ канала\n"
        "/review ссылка — анализ ролика\n"
        "/thumbnail ссылка — анализ превью\n"
        "/monitor — мониторинг тем\n"
        "/competitors — конкуренты\n"
        "/trendru — RU/CIS тренды\n"
        "/trendwest — западные narratives\n"
        "/opportunity — окна роста\n"
        "/remember_success ссылка — запомнить успешный ролик\n"
        "/winners — анализ успешных роликов"
    )

async def setchat(update: Update, context: ContextTypes.DEFAULT_TYPE):
    memory = load_memory()
    memory["telegram_chat_id"] = str(update.effective_chat.id)
    save_memory(memory)
    await update.message.reply_text("Чат сохранён ✅ Теперь я смогу присылать отчёты автоматически.")

async def morning_now(update: Update, context: ContextTypes.DEFAULT_TYPE):
    news = get_rss_news()
    ru_youtube = youtube_search("криптовалюта биткоин рынок сегодня", max_results=8)
    west_youtube = youtube_search("bitcoin crypto market today macro", max_results=8)

    prompt = f"""
Сделай утренний отчёт HiFi Trade.

Данные новостей:
{json.dumps(news, ensure_ascii=False, indent=2)}

RU/CIS YouTube:
{json.dumps(ru_youtube, ensure_ascii=False, indent=2)}

WEST YouTube:
{json.dumps(west_youtube, ensure_ascii=False, indent=2)}

Нужно:
1. 5 популярных новостей из крипты и инвестиций
2. Почему это обсуждают
3. Настроение аудитории: страх / жадность / ожидание / сомнение
4. Какие темы являются мусором
5. 3 идеи для роликов
6. 3 идеи для Shorts
7. Лучшая тема дня
8. Название ролика
9. Хук
10. Краткий текст ролика
11. 3 строгих превью на выбор с текстом
"""
    await reply_long(update, ask_ai(prompt))

async def bloggers_now(update: Update, context: ContextTypes.DEFAULT_TYPE):
    memory = load_memory()
    watchlist = memory.get("ru_cis_sentiment_watchlist", [])

    if not watchlist:
        await update.message.reply_text(
            "Список блогеров пуст. Проверь ru_cis_sentiment_watchlist в memory.json."
        )
        return

    bearish = []
    bullish = []
    neutral = []

    for item in watchlist:
        mood = item.get("last_known_mood", "neutral")
        name = item.get("name", "Unknown")
        view = item.get("last_known_view", "")

        if mood == "bearish":
            bearish.append((name, view))
        elif mood == "bullish":
            bullish.append((name, view))
        else:
            neutral.append((name, view))

    total = len(watchlist)
    red_count = len(bearish)
    green_count = len(bullish)
    yellow_count = len(neutral)

    red_bar = "🔴" * red_count
    green_bar = "🟢" * green_count
    yellow_bar = "🟡" * yellow_count

    lines = []
    lines.append("📊 Настроение RU/CIS крипто-блогеров")
    lines.append("")
    lines.append(f"Всего в списке: {total}")
    lines.append(f"🔴 Медвежьи: {red_count}")
    lines.append(f"🟢 Бычьи: {green_count}")
    lines.append(f"🟡 Нейтральные: {yellow_count}")
    lines.append("")
    lines.append("Диаграмма:")
    lines.append(f"Падение: {red_bar if red_bar else '—'}")
    lines.append(f"Рост: {green_bar if green_bar else '—'}")
    lines.append(f"Нейтрально: {yellow_bar if yellow_bar else '—'}")
    lines.append("")
    lines.append("Список:")
    lines.append("")

    for name, view in bearish:
        lines.append(f"🔴 {name} — ждёт падение")
        lines.append(f"   {view}")

    for name, view in bullish:
        lines.append(f"🟢 {name} — ждёт рост")
        lines.append(f"   {view}")

    for name, view in neutral:
        lines.append(f"🟡 {name} — нейтрально/неясно")
        lines.append(f"   {view}")

    base_report = "\n".join(lines)

    prompt = f"""
На основе настроения крипто-блогеров сделай короткий вывод для HiFi Trade.

Данные:
{base_report}

Дай:
1. Что ждёт большинство
2. Где может быть ошибка толпы
3. Контртрендовая идея для ролика
4. Название ролика
5. Хук первых 10 секунд
6. 2 варианта текста на превью

Кратко.
"""

    ai_summary = ask_ai(prompt, max_chars=1300)

    final_report = base_report + "\n\n🎬 Контентный вывод для HiFi Trade:\n" + ai_summary

    await reply_long(update, final_report)

async def videoidea(update: Update, context: ContextTypes.DEFAULT_TYPE):
    news = get_rss_news()
    ru = youtube_search("биткоин криптовалюта рынок прогноз", max_results=8)
    west = youtube_search("bitcoin macro liquidity ETF", max_results=8)

    prompt = f"""
Дай одну сильную идею для большого ролика HiFi Trade на вторник.

Основа:
Новости:
{json.dumps(news[:15], ensure_ascii=False, indent=2)}

RU/CIS:
{json.dumps(ru, ensure_ascii=False, indent=2)}

WEST:
{json.dumps(west, ensure_ascii=False, indent=2)}

Нужно:
1. Идея ролика
2. Почему она может привести подписчиков
3. Почему тема недопокрыта на YouTube
4. Название ролика
5. Альтернативные 3 названия
6. Хук первых 10 секунд
7. Структура ролика
8. Готовый краткий текст ролика
9. 3 варианта превью:
   - концепт
   - текст на превью
   - эмоция/визуал
10. Что сказать в конце для подписки
11. Почему это не мусор
"""
    await reply_long(update, ask_ai(prompt))

async def shortidea(update: Update, context: ContextTypes.DEFAULT_TYPE):
    news = get_rss_news()
    ru = youtube_search("биткоин рынок сегодня shorts крипта", max_results=8)

    prompt = f"""
Дай одну сильную идею для Shorts HiFi Trade.

Основа:
Новости:
{json.dumps(news[:12], ensure_ascii=False, indent=2)}

RU/CIS YouTube:
{json.dumps(ru, ensure_ascii=False, indent=2)}

Нужно:
1. Тема Shorts
2. Почему зрителю будет интересно
3. Первая фраза
4. Полный текст Shorts на 35-50 секунд
5. Превью:
   - строгий концепт
   - текст на превью
   - визуальная идея
6. Почему это может привести подписчиков
7. Чего не говорить, чтобы не выглядеть как хайп
"""
    await reply_long(update, ask_ai(prompt))

async def channel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    memory = load_memory()
    ref_video = memory.get("reference_video", "")
    channel_id = get_channel_id_from_video(ref_video)

    if not channel_id:
        await update.message.reply_text("Не смог получить channelId. Проверь reference_video в memory.json.")
        return

    videos = get_channel_videos(channel_id)

    prompt = f"""
Проанализируй канал HiFi Trade.

Последние видео:
{json.dumps(videos, ensure_ascii=False, indent=2)}

Дай:
1. Что работает
2. Что мешает росту
3. Чего не хватает
4. Какие темы повторяются
5. Какие форматы нужны
6. 5 следующих роликов
7. 5 Shorts
"""
    await reply_long(update, ask_ai(prompt))

async def review(update: Update, context: ContextTypes.DEFAULT_TYPE):
    url = " ".join(context.args).strip()
    if not url:
        await update.message.reply_text("Используй: /review ссылка")
        return

    video = get_video_data(url)
    if not video:
        await update.message.reply_text("Видео не найдено.")
        return

    snippet = video["snippet"]
    stats = video.get("statistics", {})

    prompt = f"""
Проанализируй ролик HiFi Trade.

Название:
{snippet.get("title", "")}

Описание:
{snippet.get("description", "")[:2500]}

Просмотры: {stats.get("viewCount", "0")}
Лайки: {stats.get("likeCount", "0")}
Комментарии: {stats.get("commentCount", "0")}

Дай:
1. Сила темы /10
2. CTR potential /10
3. Growth potential /10
4. Что хорошо
5. Что слабое
6. Улучшенное название
7. Хук первых 10 секунд
8. 3 улучшенных превью
9. 5 follow-up роликов
"""
    await reply_long(update, ask_ai(prompt))

async def thumbnail(update: Update, context: ContextTypes.DEFAULT_TYPE):
    url = " ".join(context.args).strip()
    if not url:
        await update.message.reply_text("Используй: /thumbnail ссылка")
        return

    video = get_video_data(url)
    if not video:
        await update.message.reply_text("Видео не найдено.")
        return

    snippet = video["snippet"]
    thumbs = snippet.get("thumbnails", {})
    thumb_url = thumbs.get("maxres", thumbs.get("high", thumbs.get("default", {}))).get("url", "")

    prompt = f"""
Проанализируй thumbnail strategy ролика.

Название:
{snippet.get("title", "")}

Thumbnail URL:
{thumb_url}

Описание:
{snippet.get("description", "")[:1200]}

Важно: ты не видишь картинку глазами, но анализируешь по названию, теме и ссылке на thumbnail.

Дай:
1. Вероятная кликабельность
2. Что может быть непонятно зрителю
3. 3 строгих варианта превью
4. Текст на каждом превью
5. Как сохранить интригу без грязного хайпа
"""
    await reply_long(update, ask_ai(prompt))

async def monitor(update: Update, context: ContextTypes.DEFAULT_TYPE):
    queries = [
        "биткоин рынок сегодня",
        "криптовалюта инвестиции сегодня",
        "bitcoin ETF flows",
        "bitcoin liquidity macro",
        "ethereum institutional adoption",
        "bitcoin recession risk"
    ]

    results = []
    for q in queries:
        results.extend(youtube_search(q, max_results=3))

    prompt = f"""
Мониторинг контентных возможностей HiFi Trade.

Данные:
{json.dumps(results, ensure_ascii=False, indent=2)}

Дай:
1. Что сейчас обсуждают
2. Где шум
3. Какие темы перегреты
4. Какие темы недопокрыты
5. 3 long-form идеи
6. 3 Shorts
7. Лучшая тема сейчас
"""
    await reply_long(update, ask_ai(prompt))

async def competitors(update: Update, context: ContextTypes.DEFAULT_TYPE):
    memory = load_memory()
    ru = memory.get("ru_cis_bloggers", [])
    west = memory.get("west_bloggers", [])

    results_ru = []
    results_west = []

    for name in ru:
        results_ru.extend(youtube_search(name + " криптовалюта биткоин", max_results=2))

    for name in west:
        results_west.extend(youtube_search(name + " bitcoin crypto macro", max_results=2))

    prompt = f"""
Анализ конкурентов HiFi Trade.

RU/CIS:
{json.dumps(results_ru, ensure_ascii=False, indent=2)}

WEST:
{json.dumps(results_west, ensure_ascii=False, indent=2)}

Дай:
1. Что крутится у RU/CIS блогеров
2. Что крутится на западе
3. Где настроение на рост
4. Где настроение на падение
5. Перегретые темы
6. Недопокрытые темы
7. 5 идей для HiFi Trade
"""
    await reply_long(update, ask_ai(prompt))

async def trendru(update: Update, context: ContextTypes.DEFAULT_TYPE):
    results = []
    for q in ["биткоин прогноз", "криптовалюта рынок", "эфир прогноз", "крипта макроэкономика"]:
        results.extend(youtube_search(q, max_results=4))

    prompt = f"""
RU/CIS crypto YouTube тренды:
{json.dumps(results, ensure_ascii=False, indent=2)}

Дай:
1. Что обсуждают
2. Где шум
3. Что перегрето
4. Что раскрыть умнее
5. 5 тем для HiFi Trade
"""
    await reply_long(update, ask_ai(prompt))

async def trendwest(update: Update, context: ContextTypes.DEFAULT_TYPE):
    results = []
    for q in ["bitcoin macro liquidity", "bitcoin ETF flows", "crypto market cycle", "ethereum institutional adoption"]:
        results.extend(youtube_search(q, max_results=4))

    prompt = f"""
Западные crypto/macro narratives:
{json.dumps(results, ensure_ascii=False, indent=2)}

Дай:
1. Какие narratives появляются
2. Что может прийти в RU/CIS позже
3. Что HiFi Trade может раскрыть раньше
4. 5 идей роликов
"""
    await reply_long(update, ask_ai(prompt))

async def opportunity(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ru = youtube_search("биткоин макро ликвидность ETF крипта", max_results=8)
    west = youtube_search("bitcoin liquidity ETF macro crypto", max_results=8)

    prompt = f"""
Найди окна возможностей для HiFi Trade.

RU/CIS:
{json.dumps(ru, ensure_ascii=False, indent=2)}

WEST:
{json.dumps(west, ensure_ascii=False, indent=2)}

Дай:
1. Что заспамлено
2. Что недопокрыто
3. Где можно выделиться
4. 3 long-form темы
5. 3 Shorts
6. Лучшая тема сейчас
"""
    await reply_long(update, ask_ai(prompt))

async def remember_success(update: Update, context: ContextTypes.DEFAULT_TYPE):
    url = " ".join(context.args).strip()
    if not url:
        await update.message.reply_text("Используй: /remember_success ссылка")
        return

    video = get_video_data(url)
    if not video:
        await update.message.reply_text("Видео не найдено.")
        return

    snippet = video["snippet"]
    stats = video.get("statistics", {})

    data = load_success()
    data["successful_videos"].append({
        "title": snippet.get("title", ""),
        "url": url,
        "views": int(stats.get("viewCount", 0)),
        "likes": int(stats.get("likeCount", 0)) if "likeCount" in stats else 0,
        "comments": int(stats.get("commentCount", 0)) if "commentCount" in stats else 0,
        "saved_at": datetime.utcnow().isoformat()
    })
    save_success(data)

    await update.message.reply_text("Успешный ролик запомнен ✅")

async def winners(update: Update, context: ContextTypes.DEFAULT_TYPE):
    data = load_success()
    prompt = f"""
Проанализируй успешные ролики HiFi Trade:
{json.dumps(data, ensure_ascii=False, indent=2)}

Дай:
1. Какие темы работают
2. Какие заголовки работают
3. Что повторить
4. 5 новых идей
"""
    await reply_long(update, ask_ai(prompt))

async def cheap(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = " ".join(context.args).strip()
    if not query:
        await update.message.reply_text("Напиши так: /cheap вопрос")
        return
    await reply_long(update, ask_ai("Ответь максимально кратко: " + query, max_chars=1200))

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await reply_long(update, ask_ai(update.message.text, max_chars=2000))

async def scheduled_loop(app):
    sent_keys = set()

    while True:
        try:
            memory = load_memory()
            chat_id = memory.get("telegram_chat_id", "")

            if chat_id:
                tz = get_timezone()
                now = datetime.now(tz)
                current_time = now.strftime("%H:%M")
                weekday = now.strftime("%A")
                date_key = now.strftime("%Y-%m-%d")

                daily_time = memory.get("daily_news_time", "09:00")
                blogger_day = memory.get("weekly_blogger_mood_day", "Sunday")
                blogger_time = memory.get("weekly_blogger_mood_time", "10:00")
                video_day = memory.get("video_idea_day", "Friday")
                video_time = memory.get("video_idea_time", "12:00")
                shorts_days = memory.get("shorts_idea_days", ["Monday", "Thursday"])
                shorts_time = memory.get("shorts_idea_time", "12:00")

                if current_time == daily_time:
                    key = f"{date_key}-daily"
                    if key not in sent_keys:
                        news = get_rss_news()
                        ru = youtube_search("криптовалюта биткоин рынок сегодня", max_results=6)
                        west = youtube_search("bitcoin crypto market today macro", max_results=6)

                        prompt = f"""
Автоматический утренний отчёт HiFi Trade.

Новости:
{json.dumps(news, ensure_ascii=False, indent=2)}

RU/CIS YouTube:
{json.dumps(ru, ensure_ascii=False, indent=2)}

WEST:
{json.dumps(west, ensure_ascii=False, indent=2)}

Дай:
1. 5 популярных новостей
2. Настроение людей
3. Что обсуждают блогеры
4. Что является шумом
5. 3 идеи роликов
6. 3 идеи Shorts
7. Лучшая тема дня
"""
                        text = "🌅 Утренний отчёт HiFi Trade\n\n" + ask_ai(prompt)
                        await send_long(app, chat_id, text)
                        sent_keys.add(key)

                if weekday == blogger_day and current_time == blogger_time:
                    key = f"{date_key}-bloggers"
                    if key not in sent_keys:
                        bloggers = memory.get("ru_cis_bloggers", [])
                        results = []
                        for name in bloggers:
                            results.extend(youtube_search(name + " биткоин крипта прогноз", max_results=2))

                        prompt = f"""
Еженедельное настроение RU/CIS блогеров.

Данные:
{json.dumps(results, ensure_ascii=False, indent=2)}

Формат:
🟢 Канал — ждёт рост. Причина.
🔴 Канал — ждёт падение. Причина.
🟡 Канал — нейтрально. Причина.

В конце:
- общий настрой
- где толпа может ошибаться
- 3 идеи для HiFi Trade
"""
                        text = "📊 Настроение RU/CIS блогеров\n\n" + ask_ai(prompt)
                        await send_long(app, chat_id, text)
                        sent_keys.add(key)

                if weekday == video_day and current_time == video_time:
                    key = f"{date_key}-videoidea"
                    if key not in sent_keys:
                        news = get_rss_news()
                        ru = youtube_search("биткоин криптовалюта рынок прогноз", max_results=8)
                        west = youtube_search("bitcoin macro liquidity ETF", max_results=8)

                        prompt = f"""
Пятничная идея большого ролика для публикации во вторник.

Новости:
{json.dumps(news[:15], ensure_ascii=False, indent=2)}

RU/CIS:
{json.dumps(ru, ensure_ascii=False, indent=2)}

WEST:
{json.dumps(west, ensure_ascii=False, indent=2)}

Дай:
1. Идею ролика
2. Название
3. 3 альтернативных названия
4. Хук
5. Текст ролика
6. Структуру
7. 3 превью
8. Текст на превью
9. Почему это может привести подписчиков
"""
                        text = "🎬 Идея большого ролика на вторник\n\n" + ask_ai(prompt)
                        await send_long(app, chat_id, text)
                        sent_keys.add(key)

                if weekday in shorts_days and current_time == shorts_time:
                    key = f"{date_key}-shorts"
                    if key not in sent_keys:
                        news = get_rss_news()
                        ru = youtube_search("биткоин рынок сегодня shorts крипта", max_results=8)

                        prompt = f"""
Идея Shorts для HiFi Trade.

Новости:
{json.dumps(news[:10], ensure_ascii=False, indent=2)}

RU/CIS:
{json.dumps(ru, ensure_ascii=False, indent=2)}

Дай:
1. Тему Shorts
2. Первую фразу
3. Текст Shorts 35-50 секунд
4. Превью
5. Текст на превью
6. Почему это может привести подписчиков
"""
                        text = "⚡ Идея Shorts\n\n" + ask_ai(prompt)
                        await send_long(app, chat_id, text)
                        sent_keys.add(key)

            await asyncio.sleep(60)

        except Exception as e:
            print("Scheduler error:", str(e))
            await asyncio.sleep(60)

async def post_init(app):
    asyncio.create_task(scheduled_loop(app))

def main():
    if not OPENAI_API_KEY:
        raise ValueError("OPENAI_API_KEY missing")
    if not TELEGRAM_BOT_TOKEN:
        raise ValueError("TELEGRAM_BOT_TOKEN missing")

    app = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).post_init(post_init).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("setchat", setchat))
    app.add_handler(CommandHandler("morning_now", morning_now))
    app.add_handler(CommandHandler("bloggers_now", bloggers_now))
    app.add_handler(CommandHandler("videoidea", videoidea))
    app.add_handler(CommandHandler("shortidea", shortidea))
    app.add_handler(CommandHandler("channel", channel))
    app.add_handler(CommandHandler("review", review))
    app.add_handler(CommandHandler("thumbnail", thumbnail))
    app.add_handler(CommandHandler("monitor", monitor))
    app.add_handler(CommandHandler("competitors", competitors))
    app.add_handler(CommandHandler("trendru", trendru))
    app.add_handler(CommandHandler("trendwest", trendwest))
    app.add_handler(CommandHandler("opportunity", opportunity))
    app.add_handler(CommandHandler("remember_success", remember_success))
    app.add_handler(CommandHandler("winners", winners))
    app.add_handler(CommandHandler("cheap", cheap))

    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    print("HiFi Trade AI Growth Team Started")
    app.run_polling()

if __name__ == "__main__":
    main()
