import os
import json
import requests
from openai import OpenAI
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, ContextTypes, filters

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "").replace("\\n", "").replace("\n", "").replace("\r", "").strip()
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").replace("\\n", "").replace("\n", "").replace("\r", "").strip()
YOUTUBE_API_KEY = os.getenv("YOUTUBE_API_KEY", "").replace("\\n", "").replace("\n", "").replace("\r", "").strip()

client = OpenAI(api_key=OPENAI_API_KEY)

MEMORY_FILE = "memory.json"
MODEL_CHEAP = "gpt-4.1-mini"
MAX_OUTPUT_TOKENS = 700

SYSTEM_PROMPT = """
Ты YouTube Growth Intelligence Agent для канала HiFi Trade.

Главная цель:
помогать развивать YouTube-канал, экономить время на ресерче,
находить сильные темы для видео и Shorts, анализировать канал,
анализировать отдельные ролики и давать идеи, которые могут привести подписчиков.

Тематика:
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
- long-term investing
- serious trading/investing content

Строго запрещено предлагать:
- мемкоины
- скам-монеты
- low-cap garbage
- random pumps
- "иксы", "100x", "срочно покупай"
- мусорные листинги
- хайп без фундаментальной причины

Ты НЕ новостной пересказчик.
Ты должен думать как:
- YouTube strategist
- crypto analyst
- content producer
- market psychologist

Правила:
- отвечай кратко
- без воды
- без длинных вступлений
- только практичная польза
- если тема слабая — говори прямо
- экономь токены

Отвечай по-русски.
"""

def load_memory():
    try:
        with open(MEMORY_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}

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

    if not items:
        return None

    return items[0]

def get_channel_id_from_video(video_url):
    video = get_video_data(video_url)
    if not video:
        return None
    return video["snippet"]["channelId"]

def get_channel_videos(channel_id, max_results=12):
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

    video_ids = []
    for item in data.get("items", []):
        video_ids.append(item["id"]["videoId"])

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
        videos.append({
            "title": item["snippet"]["title"],
            "description": item["snippet"].get("description", "")[:500],
            "publishedAt": item["snippet"]["publishedAt"],
            "views": int(stats.get("viewCount", 0)),
            "likes": int(stats.get("likeCount", 0)) if "likeCount" in stats else 0,
            "comments": int(stats.get("commentCount", 0)) if "commentCount" in stats else 0
        })

    return videos

def youtube_search(query, max_results=6):
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

def ask_ai(prompt, max_chars=2200):
    compact_prompt = f"""
Отвечай КРАТКО и ПОЛЕЗНО.

Правила:
- без воды
- без повторений
- только сильные идеи
- максимум пользы на символ
- если тема слабая — скажи прямо
- если идея мусорная — отфильтруй
- не предлагай мемкоины и скам

Формат:
- короткие блоки
- конкретика
- без длинных абзацев

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

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "HiFi Trade AI Agent запущен ✅\n\n"
        "Команды:\n"
        "/channel — анализ канала\n"
        "/review ссылка — анализ конкретного ролика\n"
        "/gaps — чего не хватает каналу\n"
        "/ideas — идеи роликов\n"
        "/shorts — идеи Shorts\n"
        "/report — краткий контент-отчёт\n"
        "/scan тема — проверить YouTube-конкуренцию\n"
        "/cheap вопрос — самый экономный ответ"
    )

async def channel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        if not YOUTUBE_API_KEY:
            await update.message.reply_text("Ошибка: YOUTUBE_API_KEY не добавлен в Railway.")
            return

        memory = load_memory()
        ref_video = memory.get("reference_video", "")

        channel_id = get_channel_id_from_video(ref_video)
        if not channel_id:
            await update.message.reply_text("Не смог получить channelId. Проверь reference_video в memory.json.")
            return

        videos = get_channel_videos(channel_id)

        prompt = f"""
Проанализируй канал HiFi Trade по последним видео.

Память:
{json.dumps(memory, ensure_ascii=False, indent=2)}

Видео:
{json.dumps(videos, ensure_ascii=False, indent=2)}

Дай кратко:
1. Что уже понятно по каналу
2. Что работает
3. Что мешает росту
4. Чего не хватает
5. 5 следующих роликов
"""
        answer = ask_ai(prompt)
        await update.message.reply_text(answer)

    except Exception as e:
        await update.message.reply_text(f"Ошибка /channel: {str(e)}")

async def review(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        if not YOUTUBE_API_KEY:
            await update.message.reply_text("Ошибка: YOUTUBE_API_KEY не добавлен в Railway.")
            return

        url = " ".join(context.args).strip()

        if not url:
            await update.message.reply_text("Используй:\n/review ссылка_на_ролик")
            return

        if "youtu" not in url:
            await update.message.reply_text("Это не YouTube ссылка.")
            return

        video = get_video_data(url)

        if not video:
            await update.message.reply_text("Видео не найдено. Проверь ссылку.")
            return

        snippet = video["snippet"]
        stats = video.get("statistics", {})

        title = snippet.get("title", "")
        description = snippet.get("description", "")
        channel_title = snippet.get("channelTitle", "")
        published_at = snippet.get("publishedAt", "")

        views = stats.get("viewCount", "0")
        likes = stats.get("likeCount", "0")
        comments = stats.get("commentCount", "0")

        prompt = f"""
Проанализируй конкретный YouTube ролик для канала HiFi Trade.

Канал:
{channel_title}

Название:
{title}

Описание:
{description[:2500]}

Дата публикации:
{published_at}

Просмотры:
{views}

Лайки:
{likes}

Комментарии:
{comments}

Дай кратко:
1. Сильная ли тема для канала
2. Что хорошо в ролике
3. Что слабое
4. Почему ролик может/не может привести подписчиков
5. Улучшенный заголовок
6. Hook первых 10 секунд
7. CTR potential /10
8. Growth potential /10
9. 5 follow-up роликов

Без воды.
Без мемкоинов.
Без скама.
Думай как YouTube strategist.
"""
        answer = ask_ai(prompt)
        await update.message.reply_text(answer)

    except Exception as e:
        await update.message.reply_text(f"Ошибка /review: {str(e)}")

async def gaps(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        memory = load_memory()
        prompt = f"""
Найди content gaps для HiFi Trade.

Память:
{json.dumps(memory, ensure_ascii=False, indent=2)}

Дай:
1. 5 недостающих рубрик
2. 5 тем для роста подписчиков
3. 3 evergreen темы
4. 3 темы, которые НЕ надо трогать
"""
        answer = ask_ai(prompt)
        await update.message.reply_text(answer)

    except Exception as e:
        await update.message.reply_text(f"Ошибка /gaps: {str(e)}")

async def ideas(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        prompt = """
Дай 7 сильных идей для роликов HiFi Trade.

Фокус:
BTC, ETH, macro, ETF, liquidity, cycles, risk, serious crypto investing.

Без мемкоинов, скама, low-cap и random pumps.

Для каждой идеи:
- заголовок
- почему кликнут
- hook первых 10 секунд
"""
        answer = ask_ai(prompt)
        await update.message.reply_text(answer)

    except Exception as e:
        await update.message.reply_text(f"Ошибка /ideas: {str(e)}")

async def shorts(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        prompt = """
Дай 7 идей для Shorts HiFi Trade.

Темы:
BTC, рынок, психология толпы, ошибки инвесторов, macro, ETF, liquidity.

Формат:
- тема
- первая фраза
- суть в 1 предложении
"""
        answer = ask_ai(prompt)
        await update.message.reply_text(answer)

    except Exception as e:
        await update.message.reply_text(f"Ошибка /shorts: {str(e)}")

async def report(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        prompt = """
Сделай КРАТКИЙ контент-отчёт для HiFi Trade.

Формат:
1. Главная тема дня
2. 3 идеи для long-form
3. 3 идеи для Shorts
4. Что не трогать
5. Лучший ролик для роста подписчиков

Без мусорных новостей.
Без мемкоинов.
"""
        answer = ask_ai(prompt)
        await update.message.reply_text(answer)

    except Exception as e:
        await update.message.reply_text(f"Ошибка /report: {str(e)}")

async def scan(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        query = " ".join(context.args).strip()
        if not query:
            await update.message.reply_text("Напиши так: /scan bitcoin etf liquidity")
            return

        results = youtube_search(query)

        prompt = f"""
Проверь YouTube-конкуренцию по теме: {query}

Видео конкурентов:
{json.dumps(results, ensure_ascii=False, indent=2)}

Дай кратко:
1. Тема перегрета или есть шанс?
2. Угол для HiFi Trade
3. Лучший заголовок
4. Снимать или нет
"""
        answer = ask_ai(prompt)
        await update.message.reply_text(answer)

    except Exception as e:
        await update.message.reply_text(f"Ошибка /scan: {str(e)}")

async def cheap(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = " ".join(context.args).strip()
    if not query:
        await update.message.reply_text("Напиши так: /cheap дай 5 идей")
        return

    answer = ask_ai("Ответь максимально кратко:\n" + query, max_chars=1200)
    await update.message.reply_text(answer)

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    answer = ask_ai(text, max_chars=1800)
    await update.message.reply_text(answer)

def main():
    if not OPENAI_API_KEY:
        raise ValueError("OPENAI_API_KEY missing")
    if not TELEGRAM_BOT_TOKEN:
        raise ValueError("TELEGRAM_BOT_TOKEN missing")

    app = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("channel", channel))
    app.add_handler(CommandHandler("review", review))
    app.add_handler(CommandHandler("gaps", gaps))
    app.add_handler(CommandHandler("ideas", ideas))
    app.add_handler(CommandHandler("shorts", shorts))
    app.add_handler(CommandHandler("report", report))
    app.add_handler(CommandHandler("scan", scan))
    app.add_handler(CommandHandler("cheap", cheap))

    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    print("HiFi Trade YouTube Growth Agent Started")
    app.run_polling()

if __name__ == "__main__":
    main()
