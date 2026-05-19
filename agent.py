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

SYSTEM_PROMPT = """
Ты YouTube Growth Intelligence Agent для канала HiFi Trade.

Главная цель:
помогать владельцу канала развивать YouTube-канал, экономить время на ресерче,
находить сильные темы для видео и Shorts, анализировать что уже есть на канале
и чего не хватает для роста подписчиков.

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

Каждый ответ должен быть полезен для роста канала.

Формат хорошей идеи:
1. Тема
2. Почему это может дать просмотры
3. Для кого ролик
4. Hook первых 10 секунд
5. Заголовок
6. Почему это не мусор

Отвечай по-русски.
Кратко, но по делу.
Экономь токены.
"""

def load_memory():
    try:
        with open(MEMORY_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}

def save_memory(memory):
    with open(MEMORY_FILE, "w", encoding="utf-8") as f:
        json.dump(memory, f, ensure_ascii=False, indent=2)

def get_channel_id_from_video(video_url):
    if "youtu.be/" in video_url:
        video_id = video_url.split("youtu.be/")[1].split("?")[0]
    elif "v=" in video_url:
        video_id = video_url.split("v=")[1].split("&")[0]
    else:
        return None

    url = "https://www.googleapis.com/youtube/v3/videos"
    params = {
        "part": "snippet,statistics",
        "id": video_id,
        "key": YOUTUBE_API_KEY
    }

    r = requests.get(url, params=params, timeout=20)
    data = r.json()

    items = data.get("items", [])
    if not items:
        return None

    return items[0]["snippet"]["channelId"]

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

    video_ids = []
    basic = {}

    for item in data.get("items", []):
        vid = item["id"]["videoId"]
        video_ids.append(vid)
        basic[vid] = {
            "title": item["snippet"]["title"],
            "publishedAt": item["snippet"]["publishedAt"]
        }

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
            "publishedAt": item["snippet"]["publishedAt"],
            "views": int(stats.get("viewCount", 0)),
            "likes": int(stats.get("likeCount", 0)) if "likeCount" in stats else 0,
            "comments": int(stats.get("commentCount", 0)) if "commentCount" in stats else 0
        })

    return videos

def youtube_search(query, max_results=8):
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

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "HiFi Trade AI Agent запущен ✅\n\n"
        "Команды:\n"
        "/channel — анализ канала\n"
        "/gaps — чего не хватает каналу\n"
        "/ideas — идеи роликов\n"
        "/shorts — идеи Shorts\n"
        "/report — отчёт по контенту\n"
        "/scan тема — проверить YouTube-конкуренцию\n"
        "/cheap вопрос — экономный ответ"
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
            await update.message.reply_text("Не смог получить channelId из видео. Проверь ссылку в memory.json.")
            return

        videos = get_channel_videos(channel_id)

        prompt = f"""
Проанализируй последние видео YouTube-канала HiFi Trade.

Память канала:
{json.dumps(memory, ensure_ascii=False, indent=2)}

Последние видео:
{json.dumps(videos, ensure_ascii=False, indent=2)}

Дай:
1. Что уже понятно по каналу
2. Какие темы повторяются
3. Чего не хватает для роста
4. Какие ролики могут привести новых подписчиков
5. Что НЕ надо делать
6. 5 конкретных следующих видео

Не предлагай мемкоины, скам, random pumps.
"""

        answer = ask_ai(prompt, max_chars=3500)
        await update.message.reply_text(answer[:4000])

    except Exception as e:
        await update.message.reply_text(f"Ошибка /channel: {str(e)}")

async def gaps(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        memory = load_memory()
        prompt = f"""
Ты анализируешь канал HiFi Trade.

Задача: найти content gaps — чего не хватает каналу, чтобы росли подписчики.

Память:
{json.dumps(memory, ensure_ascii=False, indent=2)}

Дай:
1. 7 недостающих рубрик
2. 7 тем, которые могут привлекать новую аудиторию
3. 5 evergreen тем
4. 5 тем под хайп, но без мусора
5. Что убрать из контента
"""
        answer = ask_ai(prompt)
        await update.message.reply_text(answer[:4000])
    except Exception as e:
        await update.message.reply_text(f"Ошибка /gaps: {str(e)}")

async def ideas(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        prompt = """
Дай 10 сильных идей для YouTube-роликов канала HiFi Trade.

Фокус:
- BTC
- ETH
- macro
- ETF
- liquidity
- cycles
- risk
- serious crypto investing

Запрещено:
мемкоины, скам, low-cap, random pumps.

Формат:
1. Заголовок
2. Почему зритель кликнет
3. Hook первых 10 секунд
4. Почему это серьёзная тема
"""
        answer = ask_ai(prompt)
        await update.message.reply_text(answer[:4000])
    except Exception as e:
        await update.message.reply_text(f"Ошибка /ideas: {str(e)}")

async def shorts(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        prompt = """
Дай 10 идей для Shorts канала HiFi Trade.

Нужны короткие, цепляющие, серьёзные темы:
- BTC
- рынок
- психология толпы
- ошибки инвесторов
- macro
- ETF
- liquidity

Без мемкоинов и скама.

Формат:
1. Тема
2. Первая фраза
3. Суть в 1 предложении
"""
        answer = ask_ai(prompt)
        await update.message.reply_text(answer[:4000])
    except Exception as e:
        await update.message.reply_text(f"Ошибка /shorts: {str(e)}")

async def report(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        prompt = """
Сделай ежедневный контент-отчёт для HiFi Trade.

Не пересказывай мусорные новости.
Найди стратегические темы.

Формат:
1. Главный market narrative дня
2. 3 идеи для long-form видео
3. 3 идеи для Shorts
4. Что сейчас лучше не трогать
5. Одна сильная тема для роста подписчиков
6. Почему это может сработать на YouTube

Без мемкоинов, скама и random pumps.
"""
        answer = ask_ai(prompt)
        await update.message.reply_text(answer[:4000])
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

Найденные видео:
{json.dumps(results, ensure_ascii=False, indent=2)}

Оцени:
1. Тема перегрета или есть окно возможностей?
2. Какой угол можно взять для HiFi Trade?
3. Какой заголовок лучше?
4. Как сделать ролик не копией конкурентов?
5. Стоит ли снимать: YES/NO и почему
"""
        answer = ask_ai(prompt)
        await update.message.reply_text(answer[:4000])
    except Exception as e:
        await update.message.reply_text(f"Ошибка /scan: {str(e)}")

async def cheap(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = " ".join(context.args).strip()
    if not query:
        await update.message.reply_text("Напиши так: /cheap дай 5 идей")
        return

    answer = ask_ai("Ответь очень кратко и экономно:\n" + query, max_chars=1200)
    await update.message.reply_text(answer[:4000])

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    answer = ask_ai(text, max_chars=2000)
    await update.message.reply_text(answer[:4000])

def ask_ai(prompt, max_chars=3000):
    response = client.responses.create(
        model=MODEL_CHEAP,
        input=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": prompt}
        ]
    )

    text = response.output_text or "Не удалось получить ответ."
    return text[:max_chars]

def main():
    if not OPENAI_API_KEY:
        raise ValueError("OPENAI_API_KEY missing")
    if not TELEGRAM_BOT_TOKEN:
        raise ValueError("TELEGRAM_BOT_TOKEN missing")

    app = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("channel", channel))
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
