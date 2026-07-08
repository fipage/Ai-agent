import os
import re
import json
import csv
from io import StringIO
import asyncio
import requests
import urllib.parse
import feedparser
import concurrent.futures
import logging
import hashlib
import tempfile
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from openai import OpenAI
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ApplicationBuilder, CallbackQueryHandler, CommandHandler, MessageHandler, ContextTypes, filters

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

YOUTUBE_API_KEY = (
    os.getenv("YOUTUBE_API_KEY", "")
    .replace("\\n", "")
    .replace("\n", "")
    .replace("\r", "")
    .strip()
)


ALLOWED_USER_IDS_RAW = os.getenv("ALLOWED_USER_IDS", "").strip()
ALLOWED_USER_IDS = set()

if ALLOWED_USER_IDS_RAW:
    for item in ALLOWED_USER_IDS_RAW.split(","):
        item = item.strip()
        if item.isdigit():
            ALLOWED_USER_IDS.add(int(item))

conversation_history = {}

client = OpenAI(api_key=OPENAI_API_KEY)

MEMORY_FILE = "memory.json"
SUCCESS_FILE = "success_memory.json"
STATE_FILE = "agent_state.json"
COMPETITOR_VIDEO_DB_FILE = "competitor_video_db.json"
LOG_FILE = "agent_runtime.log"
NEWS_CANDIDATES_FILE = "news_candidates.json"
POST_DRAFTS_FILE = "post_drafts.json"

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    handlers=[logging.StreamHandler(), logging.FileHandler(LOG_FILE, encoding="utf-8")]
)
logger = logging.getLogger("hifi_agent")


MODEL_CHEAP = "gpt-4.1-mini"
MAX_OUTPUT_TOKENS = 850

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

QUALITY_FRAMEWORK = """
Оценивай каждую идею по 10-балльной шкале:

1. CTR /10 — насколько зритель захочет кликнуть.
2. Clarity /10 — понятно ли зрителю, о чём ролик/Shorts/превью.
3. Curiosity gap /10 — есть ли интрига без дешёвого хайпа.
4. Audience interest /10 — насколько тема совпадает с интересом crypto/инвест-аудитории.
5. Algorithm potential /10 — есть ли шанс на удержание, комментарии, досмотры и рекомендации YouTube.
6. Seriousness /10 — насколько тема выглядит серьёзно, без скама и мусора.
7. Subscriber potential /10 — может ли тема привести новых подписчиков.
8. Timeliness /10 — актуальность сейчас.
9. Evergreen value /10 — будет ли тема жить дольше одного дня.
10. Differentiation /10 — отличается ли тема от конкурентов.

Правило качества:
- Пиши коротко и без лишней информации.
- Каждая тема должна быть самостоятельным компактным блоком.
- Не добавляй длинные вступления и повторения.
- Не объясняй очевидное.
- Не выдавай слабые идеи.
- Если вариант ниже 8/10 по общей оценке — переделай его.
- В финальный ответ выводи только лучший вариант или топ-3 лучших.
- Память не является вечным запретом: востребованные темы можно возвращать после паузы с новым углом.
- Не показывай слабые черновики.
- Для превью: стиль строгий, чистый, понятный, без грязи, без перегруза.
- На превью должно быть 2-5 слов, не длинная фраза.
- Заголовок должен быть понятный, цепляющий, без кликбейта уровня скама.
- Хук должен удерживать первые 10 секунд.
- Всегда объясняй, почему выбранный вариант получил высокую оценку.
"""



YOUTUBE_PACKAGING_RULES = """
Нельзя выдавать сырой инфоповод как готовую идею.

Перед отправкой пользователю агент обязан:
1. Определить сырой инфоповод.
2. Перевести его на язык обычного зрителя.
3. Найти простой конфликт/интригу:
   - толпа ошибается?
   - рынок ведёт себя не так, как ждут?
   - сильный актив против слабого актива?
   - данные против эмоций?
   - RU/CIS против Запада?
   - крупные деньги против розницы?
4. Убрать лишние сложные имена из заголовка, если они не усиливают клик.
5. Сделать название понятным человеку без подготовки.
6. Сделать превью понятным за 1 секунду.
7. На превью использовать 2-5 слов.
8. Не использовать банковские/аналитические заголовки вроде:
   - анализ JPMorgan и Hilbert
   - макроликвидность на фоне ограничений
   - институциональные движения в металлрынке
9. Если тема звучит как отчёт банка — переделать в человеческий YouTube-угол.
10. Финальный ответ должен быть готов к использованию:
   - сильное название
   - хук
   - 3 превью
   - почему зритель кликнет
   - что именно сказать в ролике/посте
11. Если упаковка ниже 8/10 — переделать до 8/10+.
12. Не показывать сырые варианты пользователю.
"""




HYPE_NEWS_RULES = """
Фильтр хайповых новостей для роста HiFi Trade.

Цель утренних новостей — найти не просто важное, а то, что уже массово обсуждают рынок, медиа и блогеры,
потому что маленькому каналу нужны темы с внешним спросом.

Новость проходит как тема для ролика только если одновременно выполняется:
1. Массовое обсуждение: тема видна в нескольких источниках, YouTube-повестке, Google News/RSS или вокруг крупных игроков.
2. Связь с ядром канала: BTC, ETH, крупные альты, ETF, ФРС, ликвидность, ставки, регуляторы, фондовый рынок или рыночный цикл.
3. Есть эмоция рынка: конфликт, страх, жадность, спор быков и медведей, риск слома ожиданий или крупные деньги против розницы.
4. Можно сделать понятный YouTube-заголовок и превью без банковского жаргона.
5. Тема не является мемкоином, скамом, random pump, low-cap garbage, “100x” или призывом срочно покупать.

Для каждой сильной новости выводить:
- что произошло;
- почему все обсуждают;
- конфликт;
- как упаковать для ролика;
- заголовок;
- текст на превью;
- Hype Score /10.

Если новость важная, но скучная, слабая по конфликту или плохо упаковывается для YouTube, помечать её как “Фон”,
а не как главную тему для ролика.
"""

SOURCE_TIER_RULES = """
Рейтинг источников:
Tier 1 — данные и первоисточники: Farside, Coinglass, Glassnode, The Block, SEC, ETF/биржевые данные, FRED, TradingView charts.
Tier 2 — сильные медиа/аналитика: CoinDesk, Blockworks, Cointelegraph, Decrypt, CryptoSlate, Investing, CNBC, Yahoo Finance, ForkLog, РБК Крипто.
Tier 3 — вторичные/слабые источники: мелкие сайты, пересказы, непроверенные новости, эмоциональные посты.

Для серьёзного ролика нужен минимум один Tier 1 или Tier 2 источник.
Если источник слабый, агент обязан пометить это и не строить на нём главный тезис.
"""



HIFITRADE_POST_STYLE = """
Стиль Telegram-поста HiFi Trade:
1. Писать коротко, уверенно, по делу.
2. Стиль: авторский аналитический Telegram-пост.
3. Не писать как новостной сайт.
4. Не использовать канцелярит.
5. Не использовать чрезмерно эмодзи.
6. Не обещать прибыль.
7. Не давать прямой финансовый совет.
8. Не писать “покупайте”, “продавайте”, “точно будет рост”.
9. Объяснять не только “что произошло”, но и “что это значит для рынка”.
10. Делать вывод в конце.
11. Сохранять оформление в стиле Telegram:
   - короткие абзацы;
   - смысловые блоки;
   - списки через тире;
   - без огромных полотен текста.
12. В конце каждого поста обязательно добавлять:
Источник: <ссылка>

Формат поста:
Заголовок без кликбейта, но с интригой

1–2 коротких абзаца: что произошло.

Что важно:
— пункт
— пункт
— пункт

Вывод:
короткий авторский вывод для рынка.

Источник: ссылка
"""

ANALYTICAL_CONTENT_RULES = """
Каждая идея ролика должна содержать:
1. Главный тезис.
2. 2-3 причины.
3. Какие данные/графики показать на экране.
4. Ссылки/источники, которые нужно проверить.
5. Контраргумент: что может опровергнуть тезис.
6. Вывод для зрителя.

Запрещено:
- выдавать пустой ролик без данных;
- делать сценарий только на эмоции;
- говорить "рынок странный" без объяснения;
- использовать интригу без аналитики.
"""

def get_performance_memory():
    data = load_success()
    return {
        "video_performance": data.get("video_performance", [])[-50:],
        "successful_patterns": data.get("successful_patterns", [])[-30:],
        "failed_patterns": data.get("failed_patterns", [])[-30:]
    }


def save_performance_memory(memory):
    data = load_success()
    data["video_performance"] = memory.get("video_performance", [])[-100:]
    data["successful_patterns"] = memory.get("successful_patterns", [])[-50:]
    data["failed_patterns"] = memory.get("failed_patterns", [])[-50:]
    save_success(data)


def classify_video_result(views=0, ctr=None, retention=None, subscribers=0):
    score = 0
    try:
        views = int(views)
    except Exception:
        views = 0
    try:
        subscribers = int(subscribers)
    except Exception:
        subscribers = 0

    if views >= 10000:
        score += 3
    elif views >= 3000:
        score += 2
    elif views >= 1000:
        score += 1

    if ctr is not None:
        try:
            ctr_float = float(str(ctr).replace("%", "").replace(",", "."))
            if ctr_float >= 8:
                score += 3
            elif ctr_float >= 5:
                score += 2
            elif ctr_float >= 3:
                score += 1
        except Exception:
            pass

    if retention is not None:
        try:
            ret_float = float(str(retention).replace("%", "").replace(",", "."))
            if ret_float >= 50:
                score += 3
            elif ret_float >= 35:
                score += 2
            elif ret_float >= 25:
                score += 1
        except Exception:
            pass

    if subscribers >= 50:
        score += 2
    elif subscribers >= 10:
        score += 1

    if score >= 6:
        return "success"
    if score <= 2:
        return "weak"
    return "neutral"


def remember_video_performance(title, url="", views=0, ctr=None, retention=None, subscribers=0, notes=""):
    perf = get_performance_memory()
    result = classify_video_result(views, ctr, retention, subscribers)

    item = {
        "title": title,
        "url": url,
        "views": views,
        "ctr": ctr,
        "retention": retention,
        "subscribers": subscribers,
        "notes": notes,
        "result": result,
        "created_at": datetime.utcnow().isoformat()
    }

    perf["video_performance"].append(item)

    pattern = {
        "title": title,
        "result": result,
        "reason": notes,
        "created_at": item["created_at"]
    }

    if result == "success":
        perf["successful_patterns"].append(pattern)
    elif result == "weak":
        perf["failed_patterns"].append(pattern)

    save_performance_memory(perf)
    return item


def performance_context_for_prompt():
    perf = get_performance_memory()
    return {
        "recent_video_performance": perf.get("video_performance", [])[-15:],
        "successful_patterns": perf.get("successful_patterns", [])[-10:],
        "failed_patterns": perf.get("failed_patterns", [])[-10:]
    }



DEFAULT_RU_CIS_SENTIMENT_WATCHLIST = [
    {
        "name": "InvTrading",
        "last_known_mood": "unknown",
        "mood_source": "old baseline only; must be rechecked from fresh content",
        "mood_last_checked": ""
    },
    {
        "name": "MrMozart",
        "last_known_mood": "unknown",
        "mood_source": "old baseline only; must be rechecked from fresh content",
        "mood_last_checked": ""
    },
    {
        "name": "PifagorTrade",
        "last_known_mood": "unknown",
        "mood_source": "old baseline only; must be rechecked from fresh content",
        "mood_last_checked": ""
    },
    {
        "name": "Рафаэль Слезы Сатоши",
        "last_known_mood": "unknown",
        "mood_source": "old baseline only; must be rechecked from fresh content",
        "mood_last_checked": ""
    },
    {
        "name": "Юра Франциско",
        "last_known_mood": "unknown",
        "mood_source": "old baseline only; must be rechecked from fresh content",
        "mood_last_checked": ""
    },
    {
        "name": "IKIGAI",
        "last_known_mood": "unknown",
        "mood_source": "old baseline only; must be rechecked from fresh content",
        "mood_last_checked": ""
    },
    {
        "name": "Биткоин Адепт",
        "last_known_mood": "unknown",
        "mood_source": "old baseline only; must be rechecked from fresh content",
        "mood_last_checked": ""
    },
    {
        "name": "Разумный Инвестор",
        "last_known_mood": "unknown",
        "mood_source": "old baseline only; must be rechecked from fresh content",
        "mood_last_checked": ""
    },
    {
        "name": "Cryptus",
        "last_known_mood": "unknown",
        "mood_source": "old baseline only; must be rechecked from fresh content",
        "mood_last_checked": ""
    },
    {
        "name": "Богдан Где Иксы",
        "last_known_mood": "unknown",
        "mood_source": "old baseline only; must be rechecked from fresh content",
        "mood_last_checked": ""
    },
    {
        "name": "Факич",
        "last_known_mood": "unknown",
        "mood_source": "old baseline only; must be rechecked from fresh content",
        "mood_last_checked": ""
    },
    {
        "name": "Ридван",
        "last_known_mood": "unknown",
        "mood_source": "old baseline only; must be rechecked from fresh content",
        "mood_last_checked": ""
    },
    {
        "name": "HAMAHA",
        "last_known_mood": "unknown",
        "mood_source": "old baseline only; must be rechecked from fresh content",
        "mood_last_checked": ""
    },
    {
        "name": "OtFront",
        "last_known_mood": "unknown",
        "mood_source": "added from user-provided target @OtFront; must be rechecked from fresh content",
        "mood_last_checked": ""
    }
]

DEFAULT_RU_CIS_BLOGGERS = [
    "InvTrading",
    "MrMozart",
    "PifagorTrade",
    "Рафаэль Слезы Сатоши",
    "Юра Франциско",
    "IKIGAI",
    "Биткоин Адепт",
    "Разумный Инвестор",
    "Cryptus",
    "Богдан Где Иксы",
    "Факич",
    "Ридван",
    "HAMAHA",
    "Крипто Патруль",
    "РБК Крипто",
    "ForkLog",
    "Bits.media",
    "Crypto Family",
    "Pro Blockchain",
    "Крипто Журнал",
    "Биткоин База",
    "OtFront"
]

DEFAULT_RU_CIS_MONITORING_SOURCES = [
    "Крипто Патруль",
    "РБК Крипто",
    "ForkLog",
    "Bits.media",
    "Crypto Family",
    "Pro Blockchain",
    "CRYPTUS",
    "Крипто Журнал",
    "Биткоин База",
    "Roman Nekrasov",
    "Happy Coin News",
    "MarsDAO",
    "Bitkogan",
    "InvestFuture",
    "Смартлаб",
    "Тимофей Мартынов"
]

DEFAULT_WEST_SENTIMENT_WATCHLIST = [
    {
        "name": "Benjamin Cowen",
        "last_known_mood": "unknown",
        "mood_source": "old baseline only; must be rechecked from fresh content",
        "mood_last_checked": ""
    },
    {
        "name": "InvestAnswers",
        "last_known_mood": "unknown",
        "mood_source": "old baseline only; must be rechecked from fresh content",
        "mood_last_checked": ""
    },
    {
        "name": "DataDash",
        "last_known_mood": "unknown",
        "mood_source": "old baseline only; must be rechecked from fresh content",
        "mood_last_checked": ""
    },
    {
        "name": "Into The Cryptoverse",
        "last_known_mood": "unknown",
        "mood_source": "old baseline only; must be rechecked from fresh content",
        "mood_last_checked": ""
    },
    {
        "name": "Crypto Banter",
        "last_known_mood": "unknown",
        "mood_source": "old baseline only; must be rechecked from fresh content",
        "mood_last_checked": ""
    },
    {
        "name": "Altcoin Daily",
        "last_known_mood": "unknown",
        "mood_source": "old baseline only; must be rechecked from fresh content",
        "mood_last_checked": ""
    },
    {
        "name": "The Modern Investor",
        "last_known_mood": "unknown",
        "mood_source": "old baseline only; must be rechecked from fresh content",
        "mood_last_checked": ""
    },
    {
        "name": "Anthony Pompliano",
        "last_known_mood": "unknown",
        "mood_source": "old baseline only; must be rechecked from fresh content",
        "mood_last_checked": ""
    },
    {
        "name": "Michael Saylor",
        "last_known_mood": "unknown",
        "mood_source": "old baseline only; must be rechecked from fresh content",
        "mood_last_checked": ""
    },
    {
        "name": "Raoul Pal",
        "last_known_mood": "unknown",
        "mood_source": "old baseline only; must be rechecked from fresh content",
        "mood_last_checked": ""
    }
]

DEFAULT_WEST_BLOGGERS = [
    "Benjamin Cowen",
    "InvestAnswers",
    "DataDash",
    "Into The Cryptoverse",
    "Crypto Banter",
    "Altcoin Daily",
    "The Modern Investor",
    "Anthony Pompliano",
    "Michael Saylor",
    "Raoul Pal",
    "Coin Bureau",
    "Bankless",
    "Real Vision",
    "Glassnode",
    "Blockworks Macro"
]

DEFAULT_WEST_MONITORING_SOURCES = [
    "Coin Bureau",
    "Bankless",
    "Real Vision",
    "Glassnode",
    "ARK Invest",
    "Blockworks Macro",
    "The Defiant",
    "Unchained",
    "What Bitcoin Did",
    "CoinDesk",
    "Cointelegraph"
]


def get_memory_list(memory, key, fallback):
    value = memory.get(key)
    if isinstance(value, list) and len(value) > 0:
        return value
    return fallback



TEN_OUT_OF_TEN_EDITOR_RULES = """
Режим 10/10 для HiFi Trade.

Агент — не генератор кликбейта, а главный редактор аналитического канала.

Каждая сильная идея обязана пройти фильтр:
1. Актуальность: почему это важно именно сейчас?
2. Спрос: будет ли это интересно зрителю?
3. Доказательства: какие данные/графики/источники показать?
4. Простота: поймёт ли новичок заголовок за 1 секунду?
5. Глубина: есть ли реальная аналитика, а не вода?
6. Контраргумент: что может сломать тезис?
7. Упаковка: можно ли сделать сильное превью и название?
8. Уникальность: чем ролик отличается от других?
9. Риск: где можно ошибиться или перегнуть?
10. Вывод: что зритель поймёт после ролика?

Если нет данных, источников или контраргумента — идея не выше 7/10.
Если заголовок сложный — переписать.
Если ролик держится только на эмоции — отклонить.
"""

VIDEO_SCRIPT_10X_STRUCTURE = """
Для большого ролика агент должен выдавать:
1. Название.
2. Превью-текст 2-4 слова.
3. Хук на 20-30 секунд.
4. Главный тезис.
5. 3 смысловых блока.
6. Какие графики/источники показать на экране.
7. Контраргумент.
8. Что смотреть дальше.
9. Финальный вывод.
10. Комментарий-вопрос для вовлечения.

Стиль:
- простой русский язык;
- без банковского жаргона;
- без воды;
- без обещаний иксов;
- без инвестиционных рекомендаций;
- не пугать и не обещать;
- объяснять, а не продавать.
"""

YOUTUBE_ANALYTICS_IMPORT_RULES = """
YouTube-метрики важнее догадок.
Если есть реальные данные по роликам, агент обязан учитывать:
- CTR;
- удержание;
- просмотры;
- подписки;
- тему;
- тип превью;
- тип заголовка;
- что было в хуке.

Выводы строить не по одному ролику, а по паттернам.
Один успешный ролик — не закон.
3-5 повторяющихся результатов — уже сигнал.
"""

def parse_number_safe(value, default=0):
    try:
        if value is None:
            return default
        value = str(value).strip().replace("%", "").replace(",", ".")
        if value == "":
            return default
        if "." in value:
            return float(value)
        return int(value)
    except Exception:
        return default


def remember_video_performance_bulk(rows):
    saved = []
    for row in rows:
        title = row.get("title") or row.get("название") or row.get("video") or row.get("ролик") or ""
        if not title:
            continue

        item = remember_video_performance(
            title=title,
            url=row.get("url", ""),
            views=row.get("views") or row.get("просмотры") or 0,
            ctr=row.get("ctr") or row.get("CTR") or row.get("ctr_percent") or None,
            retention=row.get("retention") or row.get("удержание") or row.get("avd") or None,
            subscribers=row.get("subscribers") or row.get("подписчики") or 0,
            notes=row.get("notes") or row.get("заметки") or ""
        )
        saved.append(item)
    return saved


def summarize_performance_patterns():
    perf = get_performance_memory()
    videos = perf.get("video_performance", [])[-100:]

    if not videos:
        return {
            "total": 0,
            "success": 0,
            "neutral": 0,
            "weak": 0,
            "best": [],
            "weakest": []
        }

    def views_num(v):
        return parse_number_safe(v.get("views", 0), 0)

    success = [v for v in videos if v.get("result") == "success"]
    neutral = [v for v in videos if v.get("result") == "neutral"]
    weak = [v for v in videos if v.get("result") == "weak"]

    best = sorted(videos, key=views_num, reverse=True)[:5]
    weakest = sorted(videos, key=views_num)[:5]

    return {
        "total": len(videos),
        "success": len(success),
        "neutral": len(neutral),
        "weak": len(weak),
        "best": best,
        "weakest": weakest
    }




FINAL_10X_POLICY = """
Финальная политика 10/10 для HiFi Trade:

1. Агент не должен генерировать пустой контент.
2. Любая идея ролика должна иметь: тезис, данные, источники, контраргумент, вывод.
3. Если источников нет — агент обязан написать, какие источники нужно проверить, и не завышать оценку идеи.
4. Если идея держится только на эмоции — максимум 6/10.
5. Если нет контраргумента — максимум 7/10.
6. Если заголовок сложный для новичка — переписать простым языком.
7. Если тема хайповая, но слабая по доказательствам — пометить риск.
8. Агент должен думать как главный редактор канала, а не как генератор кликбейта.
9. Реальные метрики канала важнее догадок.
10. Цель: понятный, глубокий, доказательный контент без инфоцыганства.

Формула:
простота упаковки + глубина содержания + доказательства + контраргумент = сильный ролик.
"""


def ensure_memory_defaults(memory):
    changed = False

    defaults = {
        "telegram_chat_id": "",
        "report_timezone": "Europe/Moscow",
        "daily_news_time": "09:00",
        "smart_monitor_enabled": True,
        "smart_monitor_interval_minutes": 120,
        "smart_monitor_min_score": 12,
        "smart_monitor_max_alerts_per_day": 2,
        "last_known_mood": "unknown",
        "sent_keys": [],
        "used_ideas": [],
        "weekly_notes": [],
        "conversation_history": {}
    }

    for key, value in defaults.items():
        if key not in memory:
            memory[key] = value
            changed = True

    fallback_lists = {
        "ru_cis_sentiment_watchlist": DEFAULT_RU_CIS_SENTIMENT_WATCHLIST,
        "ru_cis_bloggers": DEFAULT_RU_CIS_BLOGGERS,
        "ru_cis_monitoring_sources": DEFAULT_RU_CIS_MONITORING_SOURCES,
        "west_sentiment_watchlist": DEFAULT_WEST_SENTIMENT_WATCHLIST,
        "west_bloggers": DEFAULT_WEST_BLOGGERS,
        "west_monitoring_sources": DEFAULT_WEST_MONITORING_SOURCES,
    }

    for key, fallback in fallback_lists.items():
        value = memory.get(key)
        if not isinstance(value, list) or len(value) == 0:
            memory[key] = fallback
            changed = True

    if changed:
        save_json(MEMORY_FILE, memory)

    return memory


def load_json(path, default):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default

def save_json(path, data):
    """Atomic JSON write: protects memory files from corruption during crashes."""
    directory = os.path.dirname(os.path.abspath(path)) or "."
    os.makedirs(directory, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(prefix=os.path.basename(path) + ".", suffix=".tmp", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp_path, path)
    finally:
        if os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except Exception:
                pass


def load_news_candidates():
    data = load_json(NEWS_CANDIDATES_FILE, {"updated_at": "", "items": []})
    if isinstance(data, list):
        return {"updated_at": "", "items": data}
    if not isinstance(data, dict):
        return {"updated_at": "", "items": []}
    items = data.get("items", [])
    if not isinstance(items, list):
        items = []
    return {"updated_at": data.get("updated_at", ""), "items": items}


def save_news_candidates(items):
    normalized = []
    for index, item in enumerate(items[:7], 1):
        if not isinstance(item, dict):
            continue
        normalized.append({
            "number": int(item.get("number") or index),
            "topic": str(item.get("topic") or item.get("title") or "").strip(),
            "why_discussed": str(item.get("why_discussed") or item.get("why") or item.get("summary") or "").strip(),
            "hype_score": str(item.get("hype_score") or item.get("hype") or "0/10").strip(),
            "source": str(item.get("source") or "").strip(),
            "source_url": str(item.get("source_url") or item.get("link") or "").strip(),
            "can_make_post": bool(item.get("can_make_post", True)),
            "raw": item.get("raw", {})
        })
    save_json(NEWS_CANDIDATES_FILE, {
        "updated_at": datetime.utcnow().isoformat(),
        "items": normalized
    })
    return normalized


def load_post_drafts():
    data = load_json(POST_DRAFTS_FILE, {"updated_at": "", "drafts": []})
    if isinstance(data, list):
        return {"updated_at": "", "drafts": data}
    if not isinstance(data, dict):
        return {"updated_at": "", "drafts": []}
    drafts = data.get("drafts", [])
    if not isinstance(drafts, list):
        drafts = []
    return {"updated_at": data.get("updated_at", ""), "drafts": drafts}


def save_post_drafts(drafts):
    save_json(POST_DRAFTS_FILE, {
        "updated_at": datetime.utcnow().isoformat(),
        "drafts": drafts[-50:]
    })


def upsert_post_draft(draft):
    data = load_post_drafts()
    drafts = data.get("drafts", [])
    draft_id = draft.get("draft_id")
    replaced = False
    for i, item in enumerate(drafts):
        if item.get("draft_id") == draft_id:
            drafts[i] = draft
            replaced = True
            break
    if not replaced:
        drafts.append(draft)
    save_post_drafts(drafts)
    return draft


def get_post_draft(draft_id):
    for draft in load_post_drafts().get("drafts", []):
        if draft.get("draft_id") == draft_id:
            return draft
    return None


def format_news_candidates(items):
    if not items:
        return "Пока нет сохранённых новостей. Запусти /morning_now или дождись утреннего отчёта."
    lines = ["🌅 Новости-кандидаты для Telegram", ""]
    for item in items:
        number = item.get("number")
        can_post = "да" if item.get("can_make_post") else "нет"
        lines.extend([
            f"{number}. {item.get('topic', 'Без темы')}",
            f"Почему обсуждают: {item.get('why_discussed', '—')}",
            f"Hype Score: {item.get('hype_score', '—')}",
            f"Источник: {item.get('source', '—')}",
            f"Можно сделать пост: {can_post}",
            ""
        ])
    lines.append("Чтобы подготовить пост: /pick_news номер")
    return "\n".join(lines).strip()


def build_news_candidates_from_sources(news, ru_youtube=None, west_youtube=None, limit=7):
    ru_youtube = ru_youtube or []
    west_youtube = west_youtube or []
    candidates = []
    for item in sorted(news, key=lambda x: (x.get("hype_score", 0), x.get("score", 0)), reverse=True):
        if len(candidates) >= limit:
            break
        hype = int(item.get("hype_score") or 0)
        if hype < 6 and len(candidates) >= 5:
            continue
        candidates.append({
            "number": len(candidates) + 1,
            "topic": item.get("title", "")[:180],
            "why_discussed": (item.get("summary") or "Тема попала в свежие источники и совпадает с повесткой BTC/ETH/macro/regulation.")[:260],
            "hype_score": f"{max(6, hype)}/10" if hype else "6/10",
            "source": item.get("source", ""),
            "source_url": item.get("link", ""),
            "can_make_post": not is_trash_news(item.get("title", "")),
            "raw": item
        })

    for video in (ru_youtube + west_youtube):
        if len(candidates) >= 5:
            break
        title = video.get("title", "")
        if not title or is_trash_news(title):
            continue
        candidates.append({
            "number": len(candidates) + 1,
            "topic": title[:180],
            "why_discussed": f"Тему поднимают YouTube-источники; источник: {video.get('channel', 'YouTube')}.",
            "hype_score": "6/10",
            "source": video.get("channel", "YouTube"),
            "source_url": f"https://youtu.be/{video.get('videoId')}" if video.get("videoId") else "",
            "can_make_post": True,
            "raw": video
        })

    return save_news_candidates(candidates[:limit])


def is_valid_source_url(source_url):
    if not source_url:
        return False
    parsed = urllib.parse.urlparse(str(source_url).strip())
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def get_candidate_source_url(candidate):
    return str(candidate.get("source_url") or candidate.get("link") or "").strip()


def ensure_source_in_post(post_text, source_url):
    post_text = str(post_text or "").strip()
    source_url = str(source_url or "").strip()
    post_text = re.sub(r"\n*Источник:\s*\S+\s*$", "", post_text, flags=re.IGNORECASE).strip()
    return f"{post_text}\n\nИсточник: {source_url}".strip()


def format_draft_message(draft):
    media = "Картинка: сгенерирована" if draft.get("image_path") else f"image_prompt:\n{draft.get('image_prompt', '—')}"
    return (
        "📝 Черновик Telegram-поста\n\n"
        f"{draft.get('post_text', '')}\n\n"
        f"Hype Score: {draft.get('hype_score', '—')}\n\n"
        f"{media}\n\n"
        "Публикуем?"
    )


def draft_keyboard(draft_id):
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ Опубликовать", callback_data=f"draft:publish:{draft_id}"),
            InlineKeyboardButton("❌ Отмена", callback_data=f"draft:cancel:{draft_id}")
        ],
        [
            InlineKeyboardButton("✏️ Переделать текст", callback_data=f"draft:rewrite_text:{draft_id}"),
            InlineKeyboardButton("🎨 Переделать картинку", callback_data=f"draft:rewrite_image:{draft_id}")
        ]
    ])


def generate_telegram_post_draft(candidate, rewrite_text=False, rewrite_image=False):
    source_url = get_candidate_source_url(candidate)
    prompt = f"""
Сделай черновик Telegram-поста для канала HiFi Trade по выбранной новости.

Стиль и обязательный формат:
{HIFITRADE_POST_STYLE}

Источник для строки в конце поста: {source_url}

Новость:
{json.dumps(candidate, ensure_ascii=False, indent=2)}

Нужно вернуть только JSON:
{{
  "post_text": "готовый текст Telegram-поста на русском",
  "image_prompt": "английский промпт для строгой clean crypto/macro картинки"
}}

Правила:
- без инвестиционных рекомендаций;
- без обещаний прибыли;
- без скама, 100x и дешёвого хайпа;
- 900-1400 знаков;
- не используй слова и формулировки: “покупайте”, “продавайте”, “точно будет рост”;
- пост обязан закончиться ровно строкой: Источник: {source_url}
- если переписываешь текст, всё равно сохрани этот стиль и источник в конце.
"""
    if rewrite_text:
        prompt += "\nПеределай именно текст: сделай сильнее хук и яснее вывод."
    if rewrite_image:
        prompt += "\nПеределай именно image_prompt: сделай визуал более строгим и кликабельным."

    raw = ask_ai(prompt, max_chars=2200)
    try:
        cleaned = raw.strip().replace("```json", "").replace("```", "").strip()
        parsed = json.loads(cleaned)
    except Exception:
        parsed = {
            "post_text": raw.strip(),
            "image_prompt": (
                "Clean editorial crypto macro image, Bitcoin and Ethereum symbols, "
                "serious dark background, market chart glow, no text, no hype, premium finance style"
            )
        }

    post_text = ensure_source_in_post(str(parsed.get("post_text") or raw).strip()[:3500], source_url)
    return {
        "post_text": post_text,
        "image_prompt": str(parsed.get("image_prompt") or "").strip()[:1200]
    }


def try_generate_image(image_prompt, draft_id):
    if os.getenv("OPENAI_IMAGES_ENABLED", "").strip().lower() not in {"1", "true", "yes", "on"}:
        return None
    try:
        result = client.images.generate(
            model=os.getenv("OPENAI_IMAGE_MODEL", "gpt-image-1"),
            prompt=image_prompt,
            size=os.getenv("OPENAI_IMAGE_SIZE", "1024x1024")
        )
        image_b64 = result.data[0].b64_json
        if not image_b64:
            return None
        import base64
        image_path = os.path.join(tempfile.gettempdir(), f"{draft_id}.png")
        with open(image_path, "wb") as f:
            f.write(base64.b64decode(image_b64))
        return image_path
    except Exception:
        logger.exception("Image generation failed")
        return None


def get_runtime_state():
    return load_json(STATE_FILE, {"seen_event_keys": [], "sent_keys": []})


def save_runtime_state(state):
    if "seen_event_keys" in state:
        state["seen_event_keys"] = list(dict.fromkeys(state.get("seen_event_keys", [])))[-1000:]
    if "sent_keys" in state:
        state["sent_keys"] = list(dict.fromkeys(state.get("sent_keys", [])))[-1000:]
    save_json(STATE_FILE, state)


def get_seen_event_keys():
    state = get_runtime_state()
    return set(state.get("seen_event_keys", []))


def save_seen_event_key(key):
    if not key:
        return
    state = get_runtime_state()
    keys = state.get("seen_event_keys", [])
    if key not in keys:
        keys.append(key)
    state["seen_event_keys"] = keys[-1000:]
    save_runtime_state(state)


def load_sent_keys():
    state = get_runtime_state()
    return set(state.get("sent_keys", []))


def mark_sent_key(sent_keys, key):
    if not key:
        return
    sent_keys.add(key)
    state = get_runtime_state()
    keys = state.get("sent_keys", [])
    if key not in keys:
        keys.append(key)
    state["sent_keys"] = keys[-1000:]
    save_runtime_state(state)


def stable_hash(value):
    return hashlib.sha256(str(value).encode("utf-8")).hexdigest()[:16]

def load_memory():
    return load_json(MEMORY_FILE, {})

def save_memory(memory):
    save_json(MEMORY_FILE, memory)


def load_competitor_video_db_data():
    data = load_json(COMPETITOR_VIDEO_DB_FILE, None)
    if isinstance(data, dict):
        db = data.get("competitor_video_db", [])
        if not isinstance(db, list):
            db = []
        return {
            "competitor_video_db": db,
            "competitor_video_db_updated_at": data.get("competitor_video_db_updated_at", "нет")
        }

    memory = load_memory()
    db = memory.get("competitor_video_db", [])
    if not isinstance(db, list):
        db = []

    return {
        "competitor_video_db": db,
        "competitor_video_db_updated_at": memory.get("competitor_video_db_updated_at", "нет")
    }


def save_competitor_video_db_data(data):
    db = data.get("competitor_video_db", []) if isinstance(data, dict) else []
    if not isinstance(db, list):
        db = []

    save_json(COMPETITOR_VIDEO_DB_FILE, {
        "competitor_video_db": db[-500:],
        "competitor_video_db_updated_at": data.get("competitor_video_db_updated_at", datetime.utcnow().isoformat())
    })

def load_success():
    return load_json(SUCCESS_FILE, {
        "successful_videos": [],
        "used_ideas": [],
        "weekly_notes": [],
        "video_performance": [],
        "successful_patterns": [],
        "failed_patterns": []
    })

def save_success(data):
    save_json(SUCCESS_FILE, data)

def remember_generated_content(kind, title, summary="", source="auto"):
    """
    Automatically remembers ideas/reports generated by the agent.
    This prevents repeating the same video/Shorts ideas again and again.
    """
    data = load_success()

    if "used_ideas" not in data:
        data["used_ideas"] = []

    item = {
        "kind": kind,
        "title": title[:200] if title else "",
        "summary": summary[:500] if summary else "",
        "source": source,
        "created_at": datetime.utcnow().isoformat()
    }

    # Simple duplicate protection
    existing_titles = {
        x.get("title", "").lower().strip()
        for x in data.get("used_ideas", [])
    }

    if item["title"].lower().strip() not in existing_titles:
        data["used_ideas"].append(item)

    # Keep memory compact
    data["used_ideas"] = data["used_ideas"][-80:]

    save_success(data)


def remember_report(kind, summary):
    """
    Stores short summaries of automatic reports.
    """
    data = load_success()

    if "weekly_notes" not in data:
        data["weekly_notes"] = []

    data["weekly_notes"].append({
        "kind": kind,
        "summary": summary[:700] if summary else "",
        "created_at": datetime.utcnow().isoformat()
    })

    data["weekly_notes"] = data["weekly_notes"][-40:]
    save_success(data)


def extract_first_title(text):
    """
    Attempts to extract a useful title from generated content.
    Falls back to the first non-empty line.
    """
    if not text:
        return ""

    lines = [line.strip() for line in text.splitlines() if line.strip()]

    for line in lines:
        lower = line.lower()
        if "название" in lower or "заголовок" in lower:
            cleaned = (
                line.replace("Название ролика:", "")
                .replace("Название:", "")
                .replace("Заголовок:", "")
                .replace("**", "")
                .strip(" -—:«»\"")
            )
            if cleaned:
                return cleaned[:200]

    return lines[0].replace("**", "").strip(" -—:«»\"")[:200] if lines else ""


def get_recent_memory_context(limit=20):
    data = load_success()
    used = data.get("used_ideas", [])[-limit:]
    notes = data.get("weekly_notes", [])[-10:]

    return {
        "recent_used_ideas": used,
        "recent_reports": notes
    }


def get_memory_policy_context():
    """
    Memory is not a permanent ban.
    It works as a cooldown:
    - 0-14 days: avoid repeating the same idea.
    - 15-30 days: may return only with a clearly new angle/news hook.
    - 30+ days: evergreen topics may return with updated framing.
    """
    data = load_success()
    used = data.get("used_ideas", [])

    now = datetime.utcnow()
    recent_14 = []
    older_30 = []
    evergreen_candidates = []

    evergreen_keywords = [
        "btc", "bitcoin", "биткоин", "eth", "ethereum", "эфир",
        "etf", "ликвидность", "liquidity", "фрс", "fed",
        "ставка", "доминация", "dominance", "dxy", "инфляция",
        "регуляция", "altseason", "альтсезон", "макро"
    ]

    for item in used:
        created_raw = item.get("created_at", "")
        try:
            created = datetime.fromisoformat(created_raw.replace("Z", ""))
            age_days = (now - created).days
        except Exception:
            age_days = 999

        title = item.get("title", "")
        summary = item.get("summary", "")
        combined = f"{title} {summary}".lower()

        compact = {
            "kind": item.get("kind", ""),
            "title": title,
            "age_days": age_days,
            "source": item.get("source", "")
        }

        if age_days <= 14:
            recent_14.append(compact)
        elif age_days <= 30:
            older_30.append(compact)
        else:
            if any(k in combined for k in evergreen_keywords):
                evergreen_candidates.append(compact)

    return {
        "memory_policy": {
            "hot_cooldown": "0-14 days: do not repeat the same idea",
            "warm_cooldown": "15-30 days: topic may return only with a clearly new angle or new event",
            "evergreen_return": "30+ days: evergreen BTC/macro/liquidity/ETF topics may return with updated framing"
        },
        "avoid_repeating_now": recent_14[-25:],
        "may_return_only_with_new_angle": older_30[-25:],
        "evergreen_can_return_with_updated_angle": evergreen_candidates[-25:]
    }


def inject_memory_policy(prompt):
    policy = get_memory_policy_context()
    return f"""
Память агента и политика повторов:
{json.dumps(policy, ensure_ascii=False, indent=2)}

Важно:
- Память НЕ означает вечный запрет темы.
- Не повторяй идеи из avoid_repeating_now.
- Идеи из may_return_only_with_new_angle можно использовать только с новым инфоповодом/новым углом.
- Идеи из evergreen_can_return_with_updated_angle можно возвращать, если тема снова востребована.
- Вечные темы BTC/ETF/ликвидность/ФРС/ETH/доминация можно возвращать циклами, но не тем же самым заголовком.

{prompt}
"""


def passes_quality_gate(answer):
    """
    Simple gate: the model must explicitly contain 8/10, 9/10 or 10/10.
    If not, the agent treats it as not strong enough for proactive sending.
    """
    if not answer:
        return False

    normalized = answer.replace(" ", "")
    strong_markers = [
        "8/10", "8.5/10", "9/10", "9.5/10", "10/10",
        "8из10", "9из10", "10из10"
    ]

    return any(marker in normalized for marker in strong_markers)


def ask_ai_packaged(prompt, max_chars=2300):
    """
    Generates only strong content. Memory is a cooldown, not a permanent ban.
    If the first answer is weak, asks once to improve it to 8/10+.
    """
    prompt_with_memory = inject_memory_policy(prompt)
    answer = ask_ai(prompt_with_memory, max_chars=max_chars)

    if passes_quality_gate(answer):
        return answer

    improve_prompt = f"""
Предыдущий вариант слабый или без явной оценки 8/10+.

Переделай. Нужно:
- только один лучший вариант
- общая оценка 8/10 или выше
- без длинного текста
- ответ должен помещаться в одно Telegram-сообщение
- без механического повтора старых идей
- если тема уже была, верни её только с новым углом или новым инфоповодом
- без мусора и хайпа
- объясни, почему это 8/10+

Исходный запрос с политикой памяти:
{prompt_with_memory}

Слабый ответ:
{answer}
"""
    improved = ask_ai(improve_prompt, max_chars=max_chars)

    if passes_quality_gate(improved):
        return improved

    return (
        "⚠️ Сегодня не нашёл достаточно сильный вариант 8/10+.\n"
        "Лучше не публиковать слабую тему. Нужен новый инфоповод или другой угол."
    )



def ask_ai_packaged(prompt, max_chars=2200):
    """
    Final editorial pass: raw market event -> human YouTube/Telegram packaging.
    Use for proactive alerts, video ideas, Shorts ideas and competitor opportunities.
    """
    packaged_prompt = f"""
{prompt}

Финальная редакторская проверка перед отправкой пользователю:

{YOUTUBE_PACKAGING_RULES}

Запрещено:
- сырые заголовки из новостей
- длинные банковские формулировки
- перегруженные названия
- названия, которые понятны только аналитикам
- упоминание JPMorgan/Hilbert/Glassnode/и т.п. в заголовке, если без них клик будет понятнее

Обязательно:
- переведи тему в простой конфликт
- дай готовый заголовок для зрителя
- дай хук
- дай 3 превью
- оцени упаковку /10
- добавь блок "Данные/источники для экрана"
- добавь блок "Контраргумент"
- ответ должен быть компактный и готовый к использованию
"""

    first = ask_ai_strong(packaged_prompt, max_chars=max_chars)

    raw_markers = [
        "jpmorgan",
        "hilbert",
        "макроликвид",
        "ликвидных огранич",
        "институциональные движения",
        "анализ ",
        "на фоне падающих",
        "на фоне усиливающихся"
    ]

    lower = first.lower()
    if any(marker in lower for marker in raw_markers):
        repack_prompt = f"""
Ответ всё ещё звучит как сырой аналитический отчёт.

Переделай в человеческую YouTube-упаковку.
Сохрани смысл, но убери тяжёлые формулировки.

Сырой ответ:
{first}

Верни только финальный вариант:

Название:
...

Оценка упаковки: .../10

Почему кликнут:
...

Хук:
...

Превью:
1. "..." — .../10
2. "..." — .../10
3. "..." — .../10

Короткая структура:
1. ...
2. ...
3. ...
4. ...
5. ...
"""
        return ask_ai_strong(repack_prompt, max_chars=max_chars)

    return first


def build_weekly_content_plan():
    memory_context = get_recent_memory_context(limit=30)

    fear_greed = get_fear_greed_index()
    news = get_rss_news()[:15]
    ru = youtube_search("биткоин крипта рынок BTC", max_results=8)
    west = youtube_search("bitcoin crypto market macro", max_results=8)
    competitor_topics = collect_ru_competitor_topics(max_channels=20, per_channel=2)
    search_demand = collect_search_demand_proxy(max_queries=20)

    prompt = f"""
Составь автоматический недельный контент-план для YouTube-канала HiFi Trade.

Контекст памяти агента, чтобы НЕ повторяться:
{json.dumps(memory_context, ensure_ascii=False, indent=2)}

Fear & Greed:
{fear_greed}

Новости:
{json.dumps(news, ensure_ascii=False, indent=2)}

RU/CIS YouTube:
{json.dumps(ru, ensure_ascii=False, indent=2)}

WEST YouTube:
{json.dumps(west, ensure_ascii=False, indent=2)}

RU/CIS конкуренты:
{json.dumps(competitor_topics, ensure_ascii=False, indent=2)}

Ответ должен уместиться в одно Telegram-сообщение.
Нужно:
- план на 7 дней
- только сильные темы 8/10+
- не повторять идеи из памяти за последние 14 дней
- старые востребованные темы можно вернуть только с новым углом/инфоповодом
- не предлагать мемкоины, скам, low-cap мусор
- вторник: большой ролик
- понедельник и четверг: Shorts
- остальные дни: наблюдение/резерв/подготовка
- каждый день максимум 2 строки

Формат строго:

🗓 План недели

Пн — Shorts:
Тема ... / Оценка .../10

Вт — Ролик:
Тема ... / Оценка .../10

Ср — Резерв:
...

Чт — Shorts:
...

Пт — Подготовка:
...

Сб — Наблюдение:
...

Вс — Итог:
...

Главная тема недели:
...

Не трогать:
...
"""

    answer = ask_ai_packaged(prompt, max_chars=2200)
    remember_report("auto_weekly_content_plan", answer)

    # remember individual plan as generated content too
    remember_generated_content(
        kind="weekly_content_plan",
        title="Недельный контент-план",
        summary=answer,
        source="auto_weekly_plan"
    )

    return answer


def get_timezone():
    memory = load_memory()
    return ZoneInfo(memory.get("report_timezone", "Europe/Moscow"))

def is_user_allowed(update):
    if not ALLOWED_USER_IDS:
        return True

    user = getattr(update, "effective_user", None)
    if not user:
        return False

    return user.id in ALLOWED_USER_IDS


def restricted(handler):
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not is_user_allowed(update):
            if update and update.message:
                await safe_reply(update, "Доступ закрыт.")
            return
        return await handler(update, context)

    return wrapper


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
            "publishedAt": item["snippet"]["publishedAt"],
            "videoId": item.get("id", {}).get("videoId", "")
        })
    return results

def clean_text_for_dedupe(value):
    if not value:
        return ""

    value = value.lower()
    for ch in [":", ";", ",", ".", "!", "?", "—", "-", "–", "|", "«", "»", "\"", "'", "(", ")", "[", "]"]:
        value = value.replace(ch, " ")

    words = [w.strip() for w in value.split() if len(w.strip()) > 2]
    stop = {
        "the", "and", "for", "with", "from", "that", "this", "are", "was",
        "как", "что", "для", "или", "это", "при", "про", "после", "будет",
        "bitcoin", "биткоин", "crypto", "крипто", "рынок"
    }
    words = [w for w in words if w not in stop]
    return " ".join(words[:14])


def is_trash_news(title):
    if not title:
        return True

    lower = title.lower()

    trash_words = [
        "100x", "100х", "100 иксов", "иксы", "x100",
        "airdrop", "эйрдроп", "giveaway", "розыгрыш",
        "presale", "pre-sale", "предпродажа",
        "meme coin", "memecoin", "мемкоин", "мем coin",
        "shiba", "doge", "pepe", "floki", "bonk",
        "low-cap", "hidden gem", "gem coin",
        "срочно покупай", "to the moon", "moonshot",
        "best coin to buy now", "купить сейчас"
    ]

    return any(word in lower for word in trash_words)


def source_weight(source_name, link):
    source_text = f"{source_name} {link}".lower()

    weights = [
        ("reuters", 10),
        ("bloomberg", 10),
        ("cnbc", 8),
        ("yahoo", 7),
        ("investing", 7),
        ("coindesk", 9),
        ("theblock", 9),
        ("the block", 9),
        ("blockworks", 8),
        ("cointelegraph", 7),
        ("decrypt", 7),
        ("cryptoslate", 7),
        ("bitcoinmagazine", 7),
        ("forklog", 8),
        ("rbc", 8),
        ("рбк", 8),
        ("bits.media", 7),
        ("bcs", 7),
        ("smart-lab", 6),
        ("смартлаб", 6),
        ("google news", 5),
    ]

    for key, weight in weights:
        if key in source_text:
            return weight

    return 5


def news_topic_score(title, summary="", source_name="", link=""):
    text = f"{title} {summary}".lower()
    score = source_weight(source_name, link)

    important_terms = {
        "bitcoin": 4, "btc": 4, "биткоин": 4,
        "ethereum": 3, "eth": 3, "эфир": 3,
        "etf": 4, "spot etf": 4,
        "fed": 4, "federal reserve": 4, "фрс": 4,
        "inflation": 3, "инфляция": 3,
        "rates": 3, "ставк": 3,
        "liquidity": 4, "ликвид": 4,
        "dxy": 3, "treasury": 3, "bond": 3, "облигац": 3,
        "sec": 3, "regulation": 3, "регуляц": 3,
        "stablecoin": 3, "стейбл": 3,
        "blackrock": 3, "fidelity": 3,
        "microstrategy": 2, "saylor": 2,
        "binance": 2, "coinbase": 2,
        "nasdaq": 2, "s&p": 2, "sp500": 2,
        "gold": 2, "золото": 2,
        "oil": 1, "нефть": 1,
    }

    for term, points in important_terms.items():
        if term in text:
            score += points

    if is_trash_news(title):
        score -= 30

    return score


def calculate_hype_score(title, summary="", source_name="", link=""):
    if is_trash_news(title):
        return 0

    text = f"{title} {summary} {source_name} {link}".lower()
    score = 0

    market_terms = [
        "bitcoin", "btc", "биткоин", "ethereum", "eth", "эфир",
        "altcoin", "альт", "etf", "fed", "federal reserve", "фрс",
        "rate", "ставк", "inflation", "инфляц", "liquidity", "ликвид",
        "sec", "regulation", "регуляц", "market", "рынок"
    ]
    conflict_terms = [
        "crash", "dump", "selloff", "fear", "greed", "panic", "surge", "rally",
        "record", "breakout", "risk", "warning", "lawsuit", "ban", "approval",
        "outflow", "inflow", "volatility", "обвал", "паден", "паник", "страх",
        "жадност", "ралли", "рекорд", "прорыв", "риск", "предупреж",
        "запрет", "одобр", "приток", "отток", "волатиль"
    ]
    discussion_terms = [
        "analysts", "traders", "investors", "wall street", "blackrock", "fidelity",
        "microstrategy", "saylor", "binance", "coinbase", "sec", "bloggers",
        "аналитик", "трейдер", "инвестор", "обсужд", "блогер", "крупн", "институц"
    ]
    packaging_terms = [
        "why", "could", "next", "now", "today", "explained", "что", "почему",
        "сейчас", "сегодня", "может", "будет", "главн", "новый"
    ]

    if any(term in text for term in market_terms):
        score += 3
    if any(term in text for term in conflict_terms):
        score += 3
    if any(term in text for term in discussion_terms):
        score += 2
    if any(term in text for term in packaging_terms):
        score += 1
    if source_weight(source_name, link) >= 8:
        score += 1

    return max(0, min(10, score))


def get_rss_news():
    feeds = [
        # Global crypto / digital assets
        "https://www.coindesk.com/arc/outboundfeeds/rss/",
        "https://cointelegraph.com/rss",
        "https://decrypt.co/feed",
        "https://cryptoslate.com/feed/",
        "https://bitcoinmagazine.com/.rss/full/",
        "https://blockworks.co/feed",
        "https://www.theblock.co/rss.xml",

        # Markets / macro / investing
        "https://www.investing.com/rss/news_301.rss",
        "https://www.investing.com/rss/news_25.rss",
        "https://finance.yahoo.com/news/rssindex",
        "https://www.cnbc.com/id/100003114/device/rss/rss.html",

        # RU/CIS crypto and finance
        "https://forklog.com/feed",
        "https://bits.media/rss2/",
        "https://www.rbc.ru/crypto/rss.xml",
        "https://smart-lab.ru/blog/rss/",
        "https://bcs-express.ru/category/kriptovaluty/rss",

        # Google News backups
        "https://news.google.com/rss/search?q=bitcoin+ETF+flows+crypto+market&hl=ru&gl=RU&ceid=RU:ru",
        "https://news.google.com/rss/search?q=bitcoin+ethereum+crypto+regulation+macro&hl=ru&gl=RU&ceid=RU:ru",
        "https://news.google.com/rss/search?q=Federal+Reserve+liquidity+bitcoin+market&hl=ru&gl=RU&ceid=RU:ru",
        "https://news.google.com/rss/search?q=биткоин+ETF+крипторынок&hl=ru&gl=RU&ceid=RU:ru",
        "https://news.google.com/rss/search?q=криптовалюта+рынок+регулирование+ФРС&hl=ru&gl=RU&ceid=RU:ru",
        "https://news.google.com/rss/search?q=биткоин+ликвидность+рынок+сегодня&hl=ru&gl=RU&ceid=RU:ru"
    ]

    news = []

    for feed_url in feeds:
        try:
            feed = feedparser.parse(feed_url)
            source_name = getattr(feed.feed, "title", "") or feed_url

            for entry in feed.entries[:10]:
                title = entry.get("title", "")
                link = entry.get("link", "")
                summary = entry.get("summary", "")[:400]
                published = entry.get("published", "")

                if is_trash_news(title):
                    continue

                score = news_topic_score(title, summary, source_name, link)

                news.append({
                    "title": title,
                    "link": link,
                    "published": published,
                    "source": source_name,
                    "summary": summary,
                    "score": score,
                    "hype_score": calculate_hype_score(title, summary, source_name, link)
                })
        except Exception:
            continue

    # Deduplicate similar headlines and keep strongest
    deduped = []
    seen_keys = set()

    for item in sorted(news, key=lambda x: (x.get("hype_score", 0), x.get("score", 0)), reverse=True):
        key = clean_text_for_dedupe(item.get("title", ""))
        if not key:
            continue

        key_short = " ".join(key.split()[:8])
        if key_short in seen_keys:
            continue

        seen_keys.add(key_short)
        deduped.append(item)

    # Return strong candidates only. OpenAI will select final 5-7.
    strong = [x for x in deduped if x.get("score", 0) >= 7 or x.get("hype_score", 0) >= 6]

    if len(strong) < 10:
        strong = deduped

    return strong[:35]


def get_compact_memory():
    memory = load_memory()
    excluded_keys = {
        "ru_cis_bloggers",
        "west_bloggers",
        "ru_cis_monitoring_sources",
        "west_monitoring_sources",
        "ru_cis_sentiment_watchlist",
        "west_sentiment_watchlist",
        "sent_keys",
        "runtime_state"
    }

    compact = {}

    for key, value in memory.items():
        if key in excluded_keys:
            continue
        compact[key] = value

    return compact


def get_compact_success():
    success = load_success()

    return {
        "successful_videos": success.get("successful_videos", [])[-5:],
        "used_ideas": success.get("used_ideas", [])[-15:],
        "weekly_notes": success.get("weekly_notes", [])[-5:]
    }


def safe_get_json(url, params=None, timeout=15):
    try:
        r = requests.get(url, params=params or {}, timeout=timeout, headers={"User-Agent": "HiFiTradeAgent/1.0"})
        if r.status_code >= 400:
            logger.warning("HTTP %s for %s: %s", r.status_code, url, r.text[:300])
            return None
        return r.json()
    except Exception:
        logger.exception("JSON request failed: %s", url)
        return None


def get_market_snapshot():
    """Free lightweight market module: BTC/ETH/SOL price, 24h move, volume, dominance proxy."""
    data = safe_get_json(
        "https://api.coingecko.com/api/v3/coins/markets",
        params={
            "vs_currency": "usd",
            "ids": "bitcoin,ethereum,solana,binancecoin,ripple",
            "order": "market_cap_desc",
            "per_page": 5,
            "page": 1,
            "sparkline": "false",
            "price_change_percentage": "24h,7d"
        },
        timeout=12
    ) or []
    global_data = safe_get_json("https://api.coingecko.com/api/v3/global", timeout=12) or {}
    dominance = (global_data.get("data", {}).get("market_cap_percentage", {}) or {})
    rows = []
    for x in data:
        rows.append({
            "asset": x.get("symbol", "").upper(),
            "price_usd": x.get("current_price"),
            "change_24h_pct": round(float(x.get("price_change_percentage_24h") or 0), 2),
            "change_7d_pct": round(float(x.get("price_change_percentage_7d_in_currency") or 0), 2),
            "volume_24h_usd": x.get("total_volume"),
            "market_cap_usd": x.get("market_cap")
        })
    return {
        "assets": rows,
        "btc_dominance_pct": round(float(dominance.get("btc", 0) or 0), 2),
        "eth_dominance_pct": round(float(dominance.get("eth", 0) or 0), 2),
        "updated_at_utc": datetime.utcnow().isoformat()
    }


def collect_search_demand_proxy(max_queries=20):
    queries = [
        "bitcoin price prediction", "bitcoin ETF flows", "bitcoin macro liquidity",
        "ethereum ETF staking", "crypto market crash", "crypto bull market",
        "биткоин прогноз", "биткоин сегодня", "криптовалюта рынок",
        "эфириум прогноз", "альтсезон", "крипта ФРС",
        "bitcoin dominance", "stablecoin supply crypto", "crypto regulation SEC",
        "liquidations bitcoin", "funding rate bitcoin", "bitcoin halving cycle",
        "Solana ecosystem", "RWA crypto"
    ][:max_queries]
    out = []
    for q in queries:
        videos = youtube_search(q, max_results=3)
        score = 0
        for v in videos:
            title = v.get("title", "")
            score += max(1, score_monitor_event(title, v.get("channel", "")))
        out.append({"query": q, "proxy_score": score, "sample_titles": [v.get("title", "") for v in videos[:2]]})
    return sorted(out, key=lambda x: x["proxy_score"], reverse=True)


def build_search_demand_report():
    demand = collect_search_demand_proxy(max_queries=20)
    market = get_market_snapshot()
    news = get_rss_news()[:12]
    prompt = f"""
Сделай радар поискового спроса для HiFi Trade.

Market snapshot:
{json.dumps(market, ensure_ascii=False, indent=2)}

Search Demand Proxy:
{json.dumps(demand, ensure_ascii=False, indent=2)}

Новости:
{json.dumps(news, ensure_ascii=False, indent=2)}

Нужно кратко:
1. Топ-5 тем спроса.
2. Что растёт в интересе аудитории.
3. Что лучше не трогать из-за шума/скама.
4. 3 идеи Telegram-постов.
5. 2 идеи YouTube-роликов с оценкой /10.
6. 2 идеи Shorts с оценкой /10.

Только серьёзные темы: BTC/ETH/macro/ETF/liquidity/regulation/risk management.
"""
    answer = ask_ai_packaged(prompt, max_chars=2200)
    remember_report("search_demand", answer)
    return answer


def ask_ai_strong(prompt, max_chars=2200):
    """Quality-gated LLM call used by packaged commands and alerts."""
    prompt_with_memory = inject_memory_policy(prompt)
    first = ask_ai(prompt_with_memory, max_chars=max_chars)
    if passes_quality_gate(first):
        return first[:max_chars]
    improve_prompt = f"""
Усиль ответ до 8/10+. Верни только финальный готовый вариант.
Требования:
- без скама, мемкоинов, low-cap мусора;
- понятный конфликт для зрителя;
- оценка явно в формате .../10;
- компактно, Telegram-ready;
- не повторять идеи из памяти без нового угла.

Исходный запрос:
{prompt_with_memory}

Слабый вариант:
{first}
"""
    second = ask_ai(improve_prompt, max_chars=max_chars)
    if passes_quality_gate(second):
        return second[:max_chars]
    return (
        "⚠️ Не нашёл достаточно сильный вариант 8/10+.\n"
        "Лучше не публиковать слабую тему: нужен новый инфоповод, более понятный конфликт или другой угол."
    )[:max_chars]


def get_fear_greed_index():
    try:
        r = requests.get("https://api.alternative.me/fng/?limit=1", timeout=10)
        data = r.json()
        item = data.get("data", [{}])[0]
        value = item.get("value", "")
        classification = item.get("value_classification", "")
        if value and classification:
            return f"Fear & Greed: {value} ({classification})"
    except Exception:
        pass

    return "Fear & Greed: unavailable"


def ask_ai(prompt, max_chars=3500):
    memory = get_compact_memory()
    success = get_compact_success()

    compact_prompt = f"""
Память канала:
{json.dumps(memory, ensure_ascii=False, indent=2)}

Краткая память успешных роликов и использованных идей:
{json.dumps(success, ensure_ascii=False, indent=2)}

Система оценки качества:
{QUALITY_FRAMEWORK}

Правила YouTube-упаковки:
{YOUTUBE_PACKAGING_RULES}

Правила источников:
{SOURCE_TIER_RULES}

Правила аналитического содержания:
{ANALYTICAL_CONTENT_RULES}

Правила редактора 10/10:
{TEN_OUT_OF_TEN_EDITOR_RULES}

Финальная политика 10/10:
{FINAL_10X_POLICY}

Структура сильного ролика:
{VIDEO_SCRIPT_10X_STRUCTURE}

Правила обучения на YouTube-метриках:
{YOUTUBE_ANALYTICS_IMPORT_RULES}

Память эффективности роликов:
{json.dumps(performance_context_for_prompt(), ensure_ascii=False, indent=2)}

Правила:
- не предлагай мемкоины, скам и low-cap мусор
- думай как команда продвижения канала
- оценивай идеи через интерес аудитории, удержание и YouTube growth
- если тема слабая, скажи прямо и предложи замену
- ищи недопокрытые темы
- давай готовые практичные решения
- сначала внутренне оцени варианты по шкале 1-10
- в ответ выводи только варианты с общей оценкой 8/10 и выше
- если не можешь получить 8/10, напиши, что тема слабая, и предложи более сильный угол
- пиши максимально сжато
- каждая тема: максимум 3-5 строк
- старайся уместить ответ в одно Telegram-сообщение
- без длинных вступлений
- без повторения одних и тех же причин
- не добавляй разделы, которых нет в запросе

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
    if max_chars is None:
        return text
    return text[:max_chars]

async def send_long(context, chat_id, text):
    if not text:
        text = "Пустой ответ."
    await send_chunked_to_chat(context, chat_id, text)


async def safe_reply(update: Update, text: str = "", max_len: int = 2500, header: str = None, **kwargs):
    await send_chunked_message(update.message, text, max_len=max_len, header=header)


async def safe_send(app, chat_id, text: str = "", max_len: int = 2500, header: str = None, **kwargs):
    await send_chunked_to_chat(app, chat_id, text, max_len=max_len, header=header)



async def reply_long(update, text):
    if not text:
        text = "Пустой ответ."
    await safe_reply(update, text)



# =========================
# YouTube Analytics OAuth
# =========================
def get_youtube_oauth_access_token():
    client_id = os.getenv("YOUTUBE_CLIENT_ID", "").strip()
    client_secret = os.getenv("YOUTUBE_CLIENT_SECRET", "").strip()
    refresh_token = os.getenv("YOUTUBE_REFRESH_TOKEN", "").strip()

    if not client_id or not client_secret or not refresh_token:
        return None, "В Railway не заданы YOUTUBE_CLIENT_ID / YOUTUBE_CLIENT_SECRET / YOUTUBE_REFRESH_TOKEN."

    try:
        response = requests.post(
            "https://oauth2.googleapis.com/token",
            data={
                "client_id": client_id,
                "client_secret": client_secret,
                "refresh_token": refresh_token,
                "grant_type": "refresh_token",
            },
            timeout=20,
        )
        if response.status_code != 200:
            return None, f"Google OAuth error {response.status_code}: {response.text[:700]}"
        data = response.json()
        token = data.get("access_token")
        if not token:
            return None, f"Google не вернул access_token: {data}"
        return token, None
    except Exception as e:
        return None, f"Ошибка OAuth-запроса: {e}"


def youtube_api_get(url, params=None, use_oauth=False):
    params = params or {}
    headers = {}

    if use_oauth:
        token, err = get_youtube_oauth_access_token()
        if err:
            return None, err
        headers["Authorization"] = f"Bearer {token}"
    else:
        api_key = os.getenv("YOUTUBE_API_KEY", "").strip()
        if api_key:
            params["key"] = api_key

    try:
        response = requests.get(url, params=params, headers=headers, timeout=25)
        if response.status_code != 200:
            return None, f"YouTube API error {response.status_code}: {response.text[:700]}"
        return response.json(), None
    except Exception as e:
        return None, f"Ошибка запроса YouTube API: {e}"


def yt_analytics_query(start_date=None, end_date=None, dimensions="video", metrics=None, sort="-views", max_results=10):
    if not end_date:
        end_date = datetime.utcnow().date().isoformat()
    if not start_date:
        start_date = (datetime.utcnow().date() - timedelta(days=28)).isoformat()
    if metrics is None:
        metrics = "views,estimatedMinutesWatched,averageViewDuration,averageViewPercentage,subscribersGained"

    params = {
        "ids": "channel==MINE",
        "startDate": start_date,
        "endDate": end_date,
        "metrics": metrics,
        "dimensions": dimensions,
        "sort": sort,
        "maxResults": max_results,
    }
    return youtube_api_get("https://youtubeanalytics.googleapis.com/v2/reports", params=params, use_oauth=True)


def get_my_recent_youtube_videos(max_results=10):
    data, err = youtube_api_get(
        "https://www.googleapis.com/youtube/v3/channels",
        params={"part": "contentDetails", "mine": "true"},
        use_oauth=True,
    )
    if err:
        return None, err
    items = data.get("items", [])
    if not items:
        return None, "YouTube не вернул канал. Проверь, что OAuth сделан на аккаунт владельца канала."

    uploads_playlist = items[0]["contentDetails"]["relatedPlaylists"]["uploads"]
    playlist, err = youtube_api_get(
        "https://www.googleapis.com/youtube/v3/playlistItems",
        params={"part": "snippet,contentDetails", "playlistId": uploads_playlist, "maxResults": max_results},
        use_oauth=True,
    )
    if err:
        return None, err

    video_ids = [item.get("contentDetails", {}).get("videoId") for item in playlist.get("items", []) if item.get("contentDetails", {}).get("videoId")]
    if not video_ids:
        return [], None

    videos, err = youtube_api_get(
        "https://www.googleapis.com/youtube/v3/videos",
        params={"part": "snippet,statistics,contentDetails", "id": ",".join(video_ids)},
        use_oauth=True,
    )
    if err:
        return None, err
    return videos.get("items", []), None


def get_youtube_video_titles(video_ids):
    if not video_ids:
        return {}
    result = {}
    ids = list(video_ids)
    for i in range(0, len(ids), 50):
        chunk = ids[i:i+50]
        videos, err = youtube_api_get(
            "https://www.googleapis.com/youtube/v3/videos",
            params={"part": "snippet,statistics", "id": ",".join(chunk)},
            use_oauth=True,
        )
        if err:
            continue
        for item in videos.get("items", []):
            vid = item.get("id")
            result[vid] = {
                "title": item.get("snippet", {}).get("title", ""),
                "url": f"https://youtu.be/{vid}",
                "views_public": item.get("statistics", {}).get("viewCount"),
            }
    return result


def format_yt_seconds(seconds):
    try:
        seconds = int(float(seconds))
    except Exception:
        return str(seconds)
    m, s = divmod(seconds, 60)
    return f"{m}:{s:02d}"


def yt_learn_from_analytics(days=28, max_results=15):
    end_date = datetime.utcnow().date().isoformat()
    start_date = (datetime.utcnow().date() - timedelta(days=days)).isoformat()
    data, err = yt_analytics_query(start_date=start_date, end_date=end_date, dimensions="video", sort="-views", max_results=max_results)
    if err:
        return None, err
    rows = data.get("rows", [])
    headers = [h.get("name") for h in data.get("columnHeaders", [])]
    if not rows:
        return [], None

    video_idx = headers.index("video") if "video" in headers else 0
    video_ids = [row[video_idx] for row in rows]
    titles = get_youtube_video_titles(video_ids)
    saved = []

    for row in rows:
        row_data = dict(zip(headers, row))
        vid = row_data.get("video")
        meta = titles.get(vid, {})
        item = remember_video_performance(
            title=meta.get("title") or vid,
            url=meta.get("url", f"https://youtu.be/{vid}"),
            views=row_data.get("views", 0),
            ctr=None,
            retention=row_data.get("averageViewPercentage"),
            subscribers=row_data.get("subscribersGained", 0),
            notes=f"Автоимпорт YouTube Analytics за {days} дней. Средний просмотр: {format_yt_seconds(row_data.get('averageViewDuration', 0))}. Показы: {row_data.get('impressions', 'n/a')}.",
        )
        saved.append(item)
    return saved, None







def split_telegram_text(text, max_len=2500):
    """
    Делит длинный текст на несколько сообщений Telegram.
    Telegram лимит около 4096 символов, но оставляем запас.
    Делит по абзацам/строкам, чтобы не резать мысль посередине.
    """
    text = str(text or "").strip()
    if not text:
        return []

    if len(text) <= max_len:
        return [text]

    chunks = []
    current = ""

    # Сначала делим по двойным переносам, потом по строкам
    blocks = re.split(r"(\n\s*\n)", text)

    for block in blocks:
        if not block:
            continue

        if len(current) + len(block) <= max_len:
            current += block
            continue

        if current.strip():
            chunks.append(current.strip())
            current = ""

        # Если сам блок огромный — режем по строкам
        if len(block) > max_len:
            lines = block.splitlines(keepends=True)
            line_buf = ""

            for line in lines:
                if len(line_buf) + len(line) <= max_len:
                    line_buf += line
                else:
                    if line_buf.strip():
                        chunks.append(line_buf.strip())
                    line_buf = ""

                    # Если одна строка слишком длинная — режем грубо
                    while len(line) > max_len:
                        chunks.append(line[:max_len].strip())
                        line = line[max_len:]
                    line_buf = line

            if line_buf.strip():
                chunks.append(line_buf.strip())
        else:
            current = block

    if current.strip():
        chunks.append(current.strip())

    return chunks


async def send_chunked_message(message, text, max_len=2500, header=None):
    chunks = split_telegram_text(text, max_len=max_len)

    if not chunks:
        return

    total = len(chunks)

    for i, chunk in enumerate(chunks, 1):
        prefix = ""

        if header and i == 1:
            prefix += header.strip() + "\n\n"

        if total > 1:
            prefix += f"Часть {i}/{total}\n\n"

        await message.reply_text(prefix + chunk)


async def send_chunked_to_chat(app, chat_id, text, max_len=2500, header=None):
    chunks = split_telegram_text(text, max_len=max_len)

    if not chunks:
        return

    total = len(chunks)

    for i, chunk in enumerate(chunks, 1):
        prefix = ""

        if header and i == 1:
            prefix += header.strip() + "\n\n"

        if total > 1:
            prefix += f"Часть {i}/{total}\n\n"

        await app.bot.send_message(chat_id=chat_id, text=prefix + chunk)



async def split_test(update: Update, context: ContextTypes.DEFAULT_TYPE):
    sample = "\\n\\n".join([
        f"Тестовый блок {i}. Это длинный тест. Если бот прислал много сообщений с пометками Часть 1/N, Часть 2/N и так далее — нарезка работает. Текст специально сделан длиннее обычного ответа."
        for i in range(1, 180)
    ])
    await reply_long(update, sample)


async def competitor_learn(update: Update, context: ContextTypes.DEFAULT_TYPE):
    max_channels = 12
    max_videos = 3

    if len(context.args) >= 1:
        try:
            max_channels = min(max(int(context.args[0]), 1), 25)
        except Exception:
            pass

    if len(context.args) >= 2:
        try:
            max_videos = min(max(int(context.args[1]), 1), 10)
        except Exception:
            pass

    memory = load_memory()
    candidates = get_competitor_candidates(memory)[:max_channels]

    if not candidates:
        await safe_reply(update, "Не нашёл конкурентов в memory.json.")
        return

    all_videos = []
    errors = []

    await safe_reply(update, f"Начал сбор конкурентов: каналов {len(candidates)}, по {max_videos} ролика.")

    for competitor in candidates:
        videos, err = scan_competitor_youtube_channel(competitor, max_videos=max_videos)
        if err:
            errors.append(f"{competitor.get('name')}: {err}")
            continue
        all_videos.extend(videos or [])

    added = save_competitor_videos_to_memory(all_videos)

    lines = [
        "✅ Сбор базы конкурентов завершён",
        "",
        f"Проверено каналов: {len(candidates)}",
        f"Новых роликов добавлено: {len(added)}",
        f"Ошибок: {len(errors)}",
        ""
    ]

    if added:
        lines.append("Новые ролики:")
        for item in added[:15]:
            lines.append(f"— {item.get('channel_title')}: {item.get('title')}")
            lines.append(item.get("url", ""))

    if errors:
        lines.append("")
        lines.append("Ошибки:")
        lines.extend(errors[:10])

    await reply_long(update, "\n".join(lines))


async def competitor_digest(update: Update, context: ContextTypes.DEFAULT_TYPE):
    summary = competitor_video_db_summary(limit=80)
    recent = summary.get("recent", [])

    if not recent:
        await safe_reply(update, "База конкурентов пока пустая. Сначала запусти /competitor_learn")
        return

    prompt = f"""
Проанализируй свежую базу роликов конкурентов HiFi Trade.

Данные:
{json.dumps(summary, ensure_ascii=False, indent=2)}

Дай:
1. Какие темы сейчас повторяются у конкурентов
2. Где толпа слишком однообразна
3. Какие темы можно раскрыть лучше
4. Где есть риск инфоцыганства/пустого хайпа
5. 5 идей роликов для HiFi Trade
6. 5 идей Shorts
7. Что НЕ стоит снимать

Ответ на русском. Без воды.
"""

    await reply_long(update, ask_ai(prompt, max_chars=3500))


async def topic_gap_auto(update: Update, context: ContextTypes.DEFAULT_TYPE):
    summary = competitor_video_db_summary(limit=100)
    recent = summary.get("recent", [])

    if not recent:
        await safe_reply(update, "База конкурентов пустая. Запускаю автосбор конкурентов…")
        chat_id = update.effective_chat.id if update.effective_chat else None
        if chat_id is not None:
            await auto_competitor_learn_tick(
                context.application,
                chat_id,
                max_channels=12,
                max_videos=3,
                silent=False
            )

        summary = competitor_video_db_summary(limit=100)
        recent = summary.get("recent", [])

    if not recent:
        await safe_reply(
            update,
            "Не удалось собрать базу конкурентов. Проверь YouTube API key или точные ссылки на конкурентов."
        )
        return

    prompt = f"""
Найди topic gap для канала HiFi Trade на базе роликов конкурентов.

База конкурентов:
{json.dumps(summary, ensure_ascii=False, indent=2)}

Задача:
Найди темы, где конкуренты уже создают спрос, но можно сделать лучше:
- глубже,
- понятнее,
- с источниками,
- без инфоцыганства,
- с сильной YouTube-упаковкой.

Дай 7 тем.

Для каждой:
1. Название ролика
2. Почему это gap
3. Как раскрыть лучше конкурентов
4. Какие данные/источники показать
5. Контраргумент
6. Превью-текст 2-4 слова
7. Оценка /10

Ответ на русском.
"""

    await reply_long(update, ask_ai(prompt, max_chars=9000))


async def competitor_db_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    db_data = load_competitor_video_db_data()
    db = db_data.get("competitor_video_db", [])
    if not isinstance(db, list):
        db = []

    competitors = {}
    for item in db:
        name = item.get("channel_title") or item.get("competitor") or "unknown"
        competitors[name] = competitors.get(name, 0) + 1

    lines = [
        "📚 База конкурентов",
        "",
        f"Всего роликов сохранено: {len(db)}",
        f"Последнее обновление: {db_data.get('competitor_video_db_updated_at', 'нет')}",
        "",
        "Топ каналов в базе:"
    ]

    for name, count in sorted(competitors.items(), key=lambda x: x[1], reverse=True)[:15]:
        lines.append(f"— {name}: {count}")

    if not db:
        lines.append("")
        lines.append("База пустая. Запусти /competitor_learn")

    await reply_long(update, "\n".join(lines))


async def auto_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    memory = load_memory()

    lines = [
        "🤖 Статус автоматизации",
        "",
        f"Новости: каждый день в {memory.get('daily_news_time', '09:00')}",
        f"Smart monitoring: {'включен' if memory.get('smart_monitor_enabled', True) else 'выключен'}, интервал {memory.get('smart_monitor_interval_minutes', 120)} минут",
        "",
        f"YouTube auto-learn: {'включен' if memory.get('yt_auto_learn_enabled', True) else 'выключен'}",
        f"Время auto-learn: {memory.get('yt_auto_learn_time', '21:00')}",
        f"Период auto-learn: последние {memory.get('yt_auto_learn_days', 7)} дней",
        "",
        f"Еженедельная стратегия: {'включена' if memory.get('yt_weekly_strategy_enabled', True) else 'выключена'}",
        f"День/время: {memory.get('yt_weekly_strategy_day', 'Sunday')} {memory.get('yt_weekly_strategy_time', '18:00')}",
        "",
        f"Проверка новых роликов 24/72 часа: {'включена' if memory.get('yt_new_video_check_enabled', True) else 'выключена'}",
        f"Время проверки: {memory.get('yt_new_video_check_time', '20:00')}",
        "",
        f"Автосбор конкурентов: {'включен' if memory.get('competitor_auto_learn_enabled', True) else 'выключен'}",
        f"Время автосбора конкурентов: {memory.get('competitor_auto_learn_time', '22:00')}",
        "",
        "Если хочешь поменять время — правим значения в memory.json."
    ]

    await safe_reply(update, "\n".join(lines))



async def env_check(update: Update, context: ContextTypes.DEFAULT_TYPE):
    checks = {
        "OPENAI_API_KEY": os.getenv("OPENAI_API_KEY"),
        "TELEGRAM_BOT_TOKEN": os.getenv("TELEGRAM_BOT_TOKEN"),
        "YOUTUBE_API_KEY": os.getenv("YOUTUBE_API_KEY"),
        "YOUTUBE_CLIENT_ID": os.getenv("YOUTUBE_CLIENT_ID"),
        "YOUTUBE_CLIENT_SECRET": os.getenv("YOUTUBE_CLIENT_SECRET"),
        "YOUTUBE_REFRESH_TOKEN": os.getenv("YOUTUBE_REFRESH_TOKEN"),
        "TELEGRAM_CHANNEL_ID": os.getenv("TELEGRAM_CHANNEL_ID"),
        "ALLOWED_USER_IDS": os.getenv("ALLOWED_USER_IDS"),
    }

    lines = ["🔍 Railway env check", ""]

    for key, value in checks.items():
        if value and str(value).strip():
            safe_len = len(str(value).strip())
            lines.append(f"✅ {key}: есть, длина {safe_len}")
        else:
            lines.append(f"❌ {key}: не найдено")

    lines.append("")
    lines.append("Если YOUTUBE_CLIENT_ID / YOUTUBE_CLIENT_SECRET / YOUTUBE_REFRESH_TOKEN тут ❌, значит переменные добавлены не туда или деплой их не подхватил.")
    lines.append("Если тут ✅, но /yt_auth_check падает — проблема уже не в Railway variables, а в OAuth-токене/доступах Google.")

    await safe_reply(update, "\n".join(lines))



async def yt_auth_check(update: Update, context: ContextTypes.DEFAULT_TYPE):
    token, err = get_youtube_oauth_access_token()
    if err:
        await safe_reply(update, f"❌ YouTube OAuth не работает:\n{err}")
        return

    data, err = youtube_api_get(
        "https://www.googleapis.com/youtube/v3/channels",
        params={"part": "snippet,statistics", "mine": "true"},
        use_oauth=True,
    )
    if err:
        await safe_reply(update, f"❌ OAuth токен получен, но канал не прочитан:\n{err}")
        return
    items = data.get("items", [])
    if not items:
        await safe_reply(update, "❌ Канал не найден. Возможно OAuth сделан не на аккаунт владельца канала.")
        return
    channel = items[0]
    snippet = channel.get("snippet", {})
    stats = channel.get("statistics", {})
    await safe_reply(update, 
        "✅ YouTube OAuth работает.\n\n"
        f"Канал: {snippet.get('title', 'без названия')}\n"
        f"Подписчики: {stats.get('subscriberCount', 'скрыто')}\n"
        f"Видео: {stats.get('videoCount', 'n/a')}\n"
        f"Просмотры канала: {stats.get('viewCount', 'n/a')}"
    )


async def yt_recent(update: Update, context: ContextTypes.DEFAULT_TYPE):
    max_results = 10
    if context.args:
        try:
            max_results = min(max(int(context.args[0]), 1), 25)
        except Exception:
            pass
    videos, err = get_my_recent_youtube_videos(max_results=max_results)
    if err:
        await safe_reply(update, f"❌ Не смог получить ролики:\n{err}")
        return
    if not videos:
        await safe_reply(update, "Ролики не найдены.")
        return
    lines = ["🎬 Последние ролики канала:\n"]
    for i, item in enumerate(videos, 1):
        title = item.get("snippet", {}).get("title", "без названия")
        vid = item.get("id")
        stats = item.get("statistics", {})
        published = item.get("snippet", {}).get("publishedAt", "")[:10]
        lines.append(f"{i}. {title}\nДата: {published}\nПросмотры: {stats.get('viewCount', 'n/a')}, лайки: {stats.get('likeCount', 'n/a')}, комменты: {stats.get('commentCount', 'n/a')}\nhttps://youtu.be/{vid}\n")
    await reply_long(update, "\n".join(lines))


async def yt_analytics(update: Update, context: ContextTypes.DEFAULT_TYPE):
    days = 28
    if context.args:
        try:
            days = min(max(int(context.args[0]), 1), 365)
        except Exception:
            pass
    end_date = datetime.utcnow().date().isoformat()
    start_date = (datetime.utcnow().date() - timedelta(days=days)).isoformat()
    data, err = yt_analytics_query(start_date=start_date, end_date=end_date, dimensions="video", sort="-views", max_results=10)
    if err:
        await safe_reply(update, f"❌ Не смог получить YouTube Analytics:\n{err}")
        return
    rows = data.get("rows", [])
    headers = [h.get("name") for h in data.get("columnHeaders", [])]
    if not rows:
        await safe_reply(update, f"За последние {days} дней YouTube Analytics не вернул строк.")
        return
    video_idx = headers.index("video") if "video" in headers else 0
    titles = get_youtube_video_titles([row[video_idx] for row in rows])
    lines = [f"📊 YouTube Analytics за {days} дней:\n"]
    for row in rows:
        row_data = dict(zip(headers, row))
        vid = row_data.get("video")
        meta = titles.get(vid, {})
        title = meta.get("title", vid)
        lines.append(
            f"🎬 {title}\n"
            f"Просмотры: {row_data.get('views', 'n/a')}\n"
            f"Удержание: {row_data.get('averageViewPercentage', 'n/a')}%\n"
            f"Средний просмотр: {format_yt_seconds(row_data.get('averageViewDuration', 0))}\n"
            f"Подписчики: +{row_data.get('subscribersGained', 0)}\n"
            f"{meta.get('url', '')}\n"
        )
    await reply_long(update, "\n".join(lines))


async def yt_learn(update: Update, context: ContextTypes.DEFAULT_TYPE):
    days = 28
    if context.args:
        try:
            days = min(max(int(context.args[0]), 1), 365)
        except Exception:
            pass
    saved, err = yt_learn_from_analytics(days=days, max_results=15)
    if err:
        await safe_reply(update, f"❌ Не смог обучиться на YouTube Analytics:\n{err}")
        return
    if not saved:
        await safe_reply(update, f"За последние {days} дней нечего импортировать.")
        return
    await safe_reply(update, f"✅ Импортировал в память метрики роликов: {len(saved)} шт.\nПериод: последние {days} дней.\n\nТеперь вызови /performance или /strategy10.")


async def yt_video_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    raw = " ".join(context.args).strip()
    if not raw:
        await safe_reply(update, "Пришли ссылку или ID ролика:\n/yt_video_stats https://youtu.be/...")
        return
    match = re.search(r"(?:v=|youtu\.be/|shorts/)([A-Za-z0-9_-]{8,})", raw)
    video_id = match.group(1) if match else raw.strip()
    end_date = datetime.utcnow().date().isoformat()
    start_date = (datetime.utcnow().date() - timedelta(days=365)).isoformat()
    data, err = yt_analytics_query(start_date=start_date, end_date=end_date, dimensions="video", metrics="views,estimatedMinutesWatched,averageViewDuration,averageViewPercentage,subscribersGained", sort="-views", max_results=200)
    if err:
        await safe_reply(update, f"❌ Не смог получить аналитику:\n{err}")
        return
    headers = [h.get("name") for h in data.get("columnHeaders", [])]
    found = None
    for row in data.get("rows", []):
        row_data = dict(zip(headers, row))
        if row_data.get("video") == video_id:
            found = row_data
            break
    titles = get_youtube_video_titles([video_id])
    title = titles.get(video_id, {}).get("title", video_id)
    if not found:
        await safe_reply(update, f"Не нашёл этот ролик в Analytics за последние 365 дней.\nНазвание: {title}\nВозможно, мало данных или другой аккаунт OAuth.")
        return
    prompt = f"""
Разбери статистику ролика HiFi Trade.

Название:
{title}

Данные YouTube Analytics:
{json.dumps(found, ensure_ascii=False, indent=2)}

Дай:
1. Оценка результата /10
2. Что хорошо
3. Что плохо
4. Что улучшить в следующем ролике
5. Что можно повторить
6. Вывод по заголовку/превью/теме

Ответ на русском, без воды.
"""
    await reply_long(update, ask_ai(prompt, max_chars=2500))


async def health(update: Update, context: ContextTypes.DEFAULT_TYPE):
    memory = load_memory()
    success = load_success()

    ru_bloggers = get_memory_list(memory, "ru_cis_bloggers", DEFAULT_RU_CIS_BLOGGERS)
    ru_sent = get_memory_list(memory, "ru_cis_sentiment_watchlist", DEFAULT_RU_CIS_SENTIMENT_WATCHLIST)
    west_bloggers = get_memory_list(memory, "west_bloggers", DEFAULT_WEST_BLOGGERS)
    west_sent = get_memory_list(memory, "west_sentiment_watchlist", DEFAULT_WEST_SENTIMENT_WATCHLIST)

    lines = [
        "✅ Проверка бота HiFi Trade",
        "",
        f"Telegram chat id: {'есть' if memory.get('telegram_chat_id') else 'пусто — напиши /setchat'}",
        f"Новости: каждый день в {memory.get('daily_news_time', '09:00')} ({memory.get('report_timezone', 'Europe/Moscow')})",
        f"Smart monitor: {'включен' if memory.get('smart_monitor_enabled') else 'выключен'}",
        f"Smart interval: {memory.get('smart_monitor_interval_minutes')} минут",
        f"Smart min score: {memory.get('smart_monitor_min_score')}",
        "",
        f"RU/CIS блогеры: {len(ru_bloggers)}",
        f"RU/CIS sentiment: {len(ru_sent)}",
        f"WEST блогеры: {len(west_bloggers)}",
        f"WEST sentiment: {len(west_sent)}",
        "",
        f"Video performance записей: {len(success.get('video_performance', []))}",
        f"Successful patterns: {len(success.get('successful_patterns', []))}",
        f"Failed patterns: {len(success.get('failed_patterns', []))}",
        "",
        "Если в списках не 0 — бот готов."
    ]

    await safe_reply(update, "\n".join(lines))



async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await safe_reply(update, 
        "HiFi Trade AI Growth Team запущен ✅\n\n"
        "Сначала отправь:\n"
        "/setchat\n\n"
        "Команды:\n"
        "/morning_now — короткий новостной отчёт\n"
        "/news_candidates — последний список новостей\n"
        "/pick_news 1 — подготовить черновик поста по новости\n"
        "/post_queue — pending-черновики\n"
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
        "/winners — анализ успешных роликов\n"
        "/scoreidea идея — оценить идею /10\n"
        "/scoretitle название — оценить заголовок /10\n"
        "/weekly_plan — проверить авто-контент-план\n"
        "/smart_monitor_now — проверить smart monitoring\n"
        "/search_demand — радар поискового спроса\n"
        "/memory_status — посмотреть, что агент запомнил\n"
        "/remember_perf — запомнить метрики ролика\n"
        "/performance — анализ результатов роликов"
    )

async def setchat(update: Update, context: ContextTypes.DEFAULT_TYPE):
    memory = load_memory()
    memory["telegram_chat_id"] = str(update.effective_chat.id)
    save_memory(memory)
    await safe_reply(update, "Чат сохранён ✅ Теперь я смогу присылать отчёты автоматически.")

async def morning_now(update: Update, context: ContextTypes.DEFAULT_TYPE):
    fear_greed = get_fear_greed_index()
    news = get_rss_news()
    ru_youtube = youtube_search("криптовалюта биткоин рынок сегодня", max_results=8)
    west_youtube = youtube_search("bitcoin crypto market today macro", max_results=8)
    candidates = build_news_candidates_from_sources(news, ru_youtube, west_youtube, limit=7)

    prompt = f"""
Сделай утренний новостной отчёт HiFi Trade в формате: «что сейчас обсуждают все».
Ищи не просто важные новости, а хайповые темы с массовым обсуждением и потенциалом роста канала.

Правила отбора:
{HYPE_NEWS_RULES}

Fear & Greed:
{fear_greed}

Данные новостей:
{json.dumps(news, ensure_ascii=False, indent=2)}

RU/CIS YouTube:
{json.dumps(ru_youtube, ensure_ascii=False, indent=2)}

WEST YouTube:
{json.dumps(west_youtube, ensure_ascii=False, indent=2)}

Сохранённый список кандидатов:
{json.dumps(candidates, ensure_ascii=False, indent=2)}

Нужно:
1. Выведи 5–7 хайповых новостей под номерами из сохранённого списка кандидатов.
2. Для каждой сильной новости выведи строго:
   - Номер
   - Короткая тема
   - Почему обсуждают: 1 предложение
   - Hype Score: X/10
   - Источник
   - Можно ли сделать пост: да/нет
3. В конце напиши: "Пост сам не публикуется. Чтобы подготовить черновик: /pick_news номер".

Не придумывай торговые рекомендации.
Не продвигай мемкоины, скам, low-cap garbage, random pumps и 100x-темы.
"""
    answer = ask_ai(prompt)
    remember_report("morning_news", answer)
    await reply_long(update, answer)


async def news_candidates(update: Update, context: ContextTypes.DEFAULT_TYPE):
    data = load_news_candidates()
    await reply_long(update, format_news_candidates(data.get("items", [])))


async def pick_news(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await safe_reply(update, "Используй: /pick_news 1")
        return
    try:
        number = int(context.args[0])
    except Exception:
        await safe_reply(update, "Номер должен быть числом. Пример: /pick_news 1")
        return

    items = load_news_candidates().get("items", [])
    candidate = next((item for item in items if int(item.get("number", 0)) == number), None)
    if not candidate:
        await safe_reply(update, "Не нашёл такую новость в последнем news_candidates.json. Проверь /news_candidates")
        return
    if not candidate.get("can_make_post", True):
        await safe_reply(update, "Эта новость помечена как неподходящая для поста.")
        return
    source_url = get_candidate_source_url(candidate)
    if not is_valid_source_url(source_url):
        await safe_reply(update, "У новости нет источника. Публикация запрещена.")
        return

    draft_id = stable_hash(f"{datetime.utcnow().isoformat()}-{number}-{candidate.get('topic')}")
    generated = generate_telegram_post_draft(candidate)
    image_path = try_generate_image(generated.get("image_prompt", ""), draft_id)
    draft = {
        "draft_id": draft_id,
        "status": "pending",
        "created_at": datetime.utcnow().isoformat(),
        "candidate": candidate,
        "post_text": ensure_source_in_post(generated.get("post_text", ""), source_url),
        "image_prompt": generated.get("image_prompt", ""),
        "image_path": image_path or "",
        "source": source_url,
        "source_url": source_url,
        "hype_score": candidate.get("hype_score", "—")
    }
    upsert_post_draft(draft)
    text = format_draft_message(draft)
    if image_path:
        with open(image_path, "rb") as image:
            await update.message.reply_photo(photo=image, caption=text[:1024], reply_markup=draft_keyboard(draft_id))
        if len(text) > 1024:
            await safe_reply(update, text[1024:])
    else:
        await update.message.reply_text(text, reply_markup=draft_keyboard(draft_id))


async def post_queue(update: Update, context: ContextTypes.DEFAULT_TYPE):
    drafts = [d for d in load_post_drafts().get("drafts", []) if d.get("status") == "pending"]
    if not drafts:
        await safe_reply(update, "Pending-черновиков нет.")
        return
    lines = ["🧾 Pending-черновики", ""]
    for i, draft in enumerate(drafts, 1):
        candidate = draft.get("candidate", {})
        lines.append(f"{i}. {candidate.get('topic', 'Без темы')}")
        lines.append(f"ID: {draft.get('draft_id')}")
        lines.append(f"Hype Score: {draft.get('hype_score', '—')}")
        lines.append(f"Источник: {draft.get('source', '—')}")
        lines.append("")
    await reply_long(update, "\n".join(lines).strip())


async def draft_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    parts = (query.data or "").split(":")
    if len(parts) != 3 or parts[0] != "draft":
        return
    action, draft_id = parts[1], parts[2]
    draft = get_post_draft(draft_id)
    if not draft:
        await query.edit_message_text("Черновик не найден.")
        return

    if action == "cancel":
        draft["status"] = "cancelled"
        draft["updated_at"] = datetime.utcnow().isoformat()
        upsert_post_draft(draft)
        await query.edit_message_text("❌ Черновик отменён.")
        return

    if action in {"rewrite_text", "rewrite_image"}:
        generated = generate_telegram_post_draft(
            draft.get("candidate", {}),
            rewrite_text=action == "rewrite_text",
            rewrite_image=action == "rewrite_image"
        )
        source_url = draft.get("source_url") or draft.get("source") or get_candidate_source_url(draft.get("candidate", {}))
        if action == "rewrite_text":
            draft["post_text"] = ensure_source_in_post(generated.get("post_text", draft.get("post_text", "")), source_url)
            draft["source"] = source_url
            draft["source_url"] = source_url
        else:
            draft["image_prompt"] = generated.get("image_prompt", draft.get("image_prompt", ""))
            draft["image_path"] = try_generate_image(draft.get("image_prompt", ""), draft_id) or ""
        draft["updated_at"] = datetime.utcnow().isoformat()
        upsert_post_draft(draft)
        await query.edit_message_text(format_draft_message(draft), reply_markup=draft_keyboard(draft_id))
        return

    if action == "publish":
        source_url = draft.get("source_url") or draft.get("source") or get_candidate_source_url(draft.get("candidate", {}))
        if not is_valid_source_url(source_url):
            await query.edit_message_text("У новости нет источника. Публикация запрещена.")
            return
        draft["post_text"] = ensure_source_in_post(draft.get("post_text", ""), source_url)
        draft["source"] = source_url
        draft["source_url"] = source_url
        channel_id = os.getenv("TELEGRAM_CHANNEL_ID", "").strip()
        if not channel_id:
            await query.edit_message_text("Не задан TELEGRAM_CHANNEL_ID в Railway Variables")
            return
        if draft.get("image_path") and os.path.exists(draft.get("image_path")):
            with open(draft["image_path"], "rb") as image:
                await context.bot.send_photo(chat_id=channel_id, photo=image, caption=draft.get("post_text", "")[:1024])
            if len(draft.get("post_text", "")) > 1024:
                await context.bot.send_message(chat_id=channel_id, text=draft.get("post_text", "")[1024:])
        else:
            await context.bot.send_message(chat_id=channel_id, text=draft.get("post_text", ""))
        draft["status"] = "published"
        draft["published_at"] = datetime.utcnow().isoformat()
        upsert_post_draft(draft)
        await query.edit_message_text("✅ Пост опубликован в канал.")

def collect_btc_sentiment_influencers():
    memory = load_memory()

    rows = []
    seen = set()

    def add_row(name, mood="neutral", market="RU/CIS", role="source"):
        if not name:
            return

        key = name.lower().strip()
        if key in seen:
            return
        seen.add(key)

        rows.append({
            "name": name,
            "mood": mood,
            "market": market,
            "role": role
        })

    # RU/CIS: directional crypto/BTC influencers
    for item in get_memory_list(memory, "ru_cis_sentiment_watchlist", DEFAULT_RU_CIS_SENTIMENT_WATCHLIST):
        add_row(
            item.get("name", ""),
            item.get("last_known_mood", "neutral"),
            "RU/CIS",
            "influencer"
        )

    # RU/CIS: media / channels / broader socio-economic sources
    for name in get_memory_list(memory, "ru_cis_monitoring_sources", DEFAULT_RU_CIS_MONITORING_SOURCES):
        add_row(name, "unknown", "RU/CIS", "media")

    # Legacy RU/CIS list support
    for name in get_memory_list(memory, "ru_cis_bloggers", DEFAULT_RU_CIS_BLOGGERS):
        add_row(name, "unknown", "RU/CIS", "source")

    # WEST: directional crypto/BTC influencers
    for item in get_memory_list(memory, "west_sentiment_watchlist", DEFAULT_WEST_SENTIMENT_WATCHLIST):
        add_row(
            item.get("name", ""),
            item.get("last_known_mood", "neutral"),
            "WEST",
            "influencer"
        )

    # WEST: media / analytics / macro sources
    for name in get_memory_list(memory, "west_monitoring_sources", DEFAULT_WEST_MONITORING_SOURCES):
        add_row(name, "unknown", "WEST", "media")

    # Legacy WEST list support
    for name in get_memory_list(memory, "west_bloggers", DEFAULT_WEST_BLOGGERS):
        add_row(name, "unknown", "WEST", "source")

    return rows


def collect_recent_context_for_sources(rows, max_total_items=60):
    limited_rows = rows[:30]

    def fetch_one(row):
        try:
            name = row["name"]
            market = row["market"]

            if market == "RU/CIS":
                query = f'{name} биткоин крипта рынок прогноз'
            else:
                query = f'{name} bitcoin crypto market outlook'

            results = youtube_search(query, max_results=2)
            output = []

            for r in results:
                output.append({
                    "source": name,
                    "market": market,
                    "title": r.get("title", ""),
                    "channel": r.get("channel", ""),
                    "publishedAt": r.get("publishedAt", "")
                })

            return output
        except Exception:
            return []

    items = []

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
        futures = [executor.submit(fetch_one, row) for row in limited_rows]
        for future in concurrent.futures.as_completed(futures):
            items.extend(future.result())
            if len(items) >= max_total_items:
                break

    return items[:max_total_items]


def circle_for_mood(mood):
    if mood == "bullish":
        return "🟢"
    if mood == "bearish":
        return "🔴"
    return "🟡"


def classify_source_moods(rows, recent_context):
    """
    One compact OpenAI call for all sources.
    This costs a little, but lets the agent estimate mood for media too.
    """
    source_names = [
        {
            "name": r["name"],
            "market": r["market"],
            "role": r["role"],
            "saved_mood": r["mood"]
        }
        for r in rows
    ]

    prompt = f"""
Ты анализируешь настроение crypto/BTC инфополя для YouTube-канала HiFi Trade.

Нужно классифицировать КАЖДЫЙ источник:
- bullish = скорее ждёт рост / позитивный рыночный тон
- bearish = скорее ждёт падение / риск / негативный рыночный тон
- neutral = нет явного прогноза, новостной/аналитический/смешанный тон

Правила:
- если источник медиа, всё равно оцени общий тон по последним найденным заголовкам
- если данных мало, ставь neutral
- не выдумывай точные прогнозы
- верни ТОЛЬКО JSON массив
- каждый объект: name, market, mood
- mood только: bullish, bearish, neutral
- не добавляй объяснения

Источники:
{json.dumps(source_names, ensure_ascii=False)}

Последние найденные заголовки:
{json.dumps(recent_context, ensure_ascii=False)}
"""

    try:
        response = client.responses.create(
            model=MODEL_CHEAP,
            max_output_tokens=1800,
            input=[
                {"role": "system", "content": "Ты классификатор настроения crypto/BTC источников. Отвечай только валидным JSON."},
                {"role": "user", "content": prompt}
            ]
        )

        raw = response.output_text or "[]"
        raw = raw.strip()

        # Try to extract JSON if model wrapped it
        if raw.startswith("```"):
            raw = raw.replace("```json", "").replace("```", "").strip()

        parsed = json.loads(raw)
        mood_map = {}

        for item in parsed:
            name = item.get("name")
            mood = item.get("mood", "neutral")
            if mood not in ["bullish", "bearish", "neutral"]:
                mood = "neutral"
            if name:
                mood_map[name.lower().strip()] = mood

        classified = []
        for row in rows:
            key = row["name"].lower().strip()
            mood = mood_map.get(key)

            if not mood:
                saved = row.get("mood", "neutral")
                mood = saved if saved in ["bullish", "bearish", "neutral"] else "neutral"

            classified.append({
                "name": row["name"],
                "market": row["market"],
                "role": row["role"],
                "mood": mood,
                "circle": circle_for_mood(mood)
            })

        return classified

    except Exception:
        # Fallback: use saved moods, neutral for media/sources
        fallback = []
        for row in rows:
            mood = row.get("mood", "neutral")
            if mood not in ["bullish", "bearish", "neutral"]:
                mood = "neutral"
            fallback.append({
                "name": row["name"],
                "market": row["market"],
                "role": row["role"],
                "mood": mood,
                "circle": circle_for_mood(mood)
            })
        return fallback


def build_blogger_mood_report(classified_rows):
    ru_rows = [r for r in classified_rows if r["market"] == "RU/CIS"]
    west_rows = [r for r in classified_rows if r["market"] == "WEST"]

    red_count = sum(1 for r in classified_rows if r["mood"] == "bearish")
    green_count = sum(1 for r in classified_rows if r["mood"] == "bullish")
    yellow_count = sum(1 for r in classified_rows if r["mood"] == "neutral")
    total = len(classified_rows)

    lines = []
    lines.append("📊 Общее настроение crypto / BTC инфополя")
    lines.append("")
    lines.append(f"Всего источников: {total}")
    lines.append(f"🔴 Медвежий тон: {red_count}")
    lines.append(f"🟢 Бычий тон: {green_count}")
    lines.append(f"🟡 Нейтрально/смешанно: {yellow_count}")
    lines.append("")

    lines.append("RU/CIS:")
    if ru_rows:
        for r in ru_rows:
            lines.append(f"{r['circle']} {r['name']}")
    else:
        lines.append("—")

    lines.append("")
    lines.append("WEST:")
    if west_rows:
        for r in west_rows:
            lines.append(f"{r['circle']} {r['name']}")
    else:
        lines.append("—")

    lines.append("")
    lines.append(build_blogger_takeaway(red_count, green_count, yellow_count, total))

    return "\n".join(lines)


def build_blogger_takeaway(red_count, green_count, yellow_count, total):
    if total == 0:
        return "🎬 Вывод: список источников пуст или не загрузился. Проверь memory.json или обнови agent.py на v29 с резервными списками."

    red_share = red_count / total
    green_share = green_count / total

    if red_share >= 0.45:
        return (
            "🎬 Вывод:\n"
            "Инфополе заметно склоняется к страху/ожиданию падения.\n"
            "Идея: «Все ждут обвал BTC. Почему рынок может сделать наоборот?»\n"
            "Хук: «Когда большинство уже стоит в одну сторону, рынок часто наказывает толпу.»\n"
            "Превью: «ВСЕ ЖДУТ ОБВАЛ» / «ТОЛПА ОШИБАЕТСЯ?»"
        )

    if green_share >= 0.45:
        return (
            "🎬 Вывод:\n"
            "Инфополе заметно склоняется к росту. Нужно проверить, нет ли перегрева ожиданий.\n"
            "Идея: «Все снова верят в рост BTC. Где может быть ловушка?»\n"
            "Хук: «Когда рынок слишком уверен в росте, риск часто уже рядом.»\n"
            "Превью: «ВСЕ ЖДУТ РОСТ» / «ЛОВУШКА BTC?»"
        )

    return (
        "🎬 Вывод:\n"
        "Инфополе смешанное. Это хороший момент для спокойного разбора социономики.\n"
        "Идея: «Рынок разделился: BTC готовится к росту или ловушке?»\n"
        "Хук: «Смотреть нужно не на один прогноз, а на то, как вся толпа распределилась по ожиданиям.»\n"
        "Превью: «РЫНОК РАЗДЕЛИЛСЯ» / «BTC: КТО ОШИБАЕТСЯ?»"
    )


async def bloggers_now(update: Update, context: ContextTypes.DEFAULT_TYPE):
    rows = collect_btc_sentiment_influencers()
    recent_context = collect_recent_context_for_sources(rows, max_total_items=60)
    classified = classify_source_moods(rows, recent_context)
    final_report = build_blogger_mood_report(classified)
    remember_report("blogger_mood", final_report)
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

Ответ должен уместиться в одно Telegram-сообщение.
Нужно выдать только ОДНУ лучшую идею.
Она должна быть не копией конкурентов, а более сильным углом на фоне их тем.
Не выдавай сырой инфоповод. Упакуй тему как готовый YouTube-ролик для зрителя.

Формат строго:

🎬 Идея ролика

Название:
...

Оценка: .../10

Почему кликнут:
...

Хук:
...

Структура:
1. ...
2. ...
3. ...
4. ...
5. ...

Превью:
1. "..." — .../10
2. "..." — .../10
3. "..." — .../10

Лучшее превью:
...

Почему даст подписчиков:
...
"""
    answer = ask_ai_packaged(prompt, max_chars=2200)
    remember_generated_content(
        kind="video_idea",
        title=extract_first_title(answer),
        summary=answer,
        source="manual_videoidea"
    )
    await reply_long(update, answer)

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

Ответ должен уместиться в одно Telegram-сообщение.
Нужно выдать только ОДИН лучший Shorts.

Формат строго:

⚡ Shorts

Тема:
...

Оценка: .../10

Первая фраза:
...

Текст 35-50 сек:
...

Превью:
"..." — .../10

Почему сработает:
...

Не говорить:
...
"""
    answer = ask_ai_packaged(prompt, max_chars=1900)
    remember_generated_content(
        kind="shorts_idea",
        title=extract_first_title(answer),
        summary=answer,
        source="manual_shortidea"
    )
    await reply_long(update, answer)

async def channel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    memory = load_memory()
    ref_video = memory.get("reference_video", "")
    channel_id = get_channel_id_from_video(ref_video)

    if not channel_id:
        await safe_reply(update, "Не смог получить channelId. Проверь reference_video в memory.json.")
        return

    videos = get_channel_videos(channel_id)

    prompt = f"""
Проанализируй канал HiFi Trade.

Последние видео:
{json.dumps(videos, ensure_ascii=False, indent=2)}

Дай кратко:
1. Что работает: топ-3
2. Что мешает росту: топ-3
3. Чего не хватает: топ-3
4. Какие темы повторяются
5. 3 следующих ролика с оценкой /10
6. 3 Shorts с оценкой /10
"""
    await reply_long(update, ask_ai(prompt))

async def review(update: Update, context: ContextTypes.DEFAULT_TYPE):
    url = " ".join(context.args).strip()
    if not url:
        await safe_reply(update, "Используй: /review ссылка")
        return

    video = get_video_data(url)
    if not video:
        await safe_reply(update, "Видео не найдено.")
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
8. 3 улучшенных превью с оценкой каждого /10
9. Лучший вариант превью
10. Итоговая оценка ролика /10
11. 5 follow-up роликов с оценкой потенциала /10
"""
    await reply_long(update, ask_ai(prompt))

async def thumbnail(update: Update, context: ContextTypes.DEFAULT_TYPE):
    url = " ".join(context.args).strip()
    if not url:
        await safe_reply(update, "Используй: /thumbnail ссылка")
        return

    video = get_video_data(url)
    if not video:
        await safe_reply(update, "Видео не найдено.")
        return

    snippet = video["snippet"]
    thumbs = snippet.get("thumbnails", {})
    thumb_url = thumbs.get("maxres", thumbs.get("high", thumbs.get("default", {}))).get("url", "")

    title = snippet.get("title", "")
    description = snippet.get("description", "")[:1200]

    if thumb_url:
        try:
            response = client.chat.completions.create(
                model="gpt-4o",
                max_tokens=900,
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "image_url",
                                "image_url": {"url": thumb_url}
                            },
                            {
                                "type": "text",
                                "text": f"""
Проанализируй превью YouTube-ролика HiFi Trade глазами YouTube thumbnail strategist.

Название:
{title}

Описание:
{description}

Дай кратко:
1. Оценка превью /10
2. Понятно ли зрителю, о чём ролик
3. CTR potential /10
4. Что слабое
5. 3 улучшенных варианта:
   - концепт
   - текст 2-5 слов
   - оценка /10
6. Лучший вариант

Стиль: строго, чисто, без грязи и дешёвого хайпа.
"""
                            }
                        ]
                    }
                ]
            )

            answer = response.choices[0].message.content or "Не удалось проанализировать превью."
            await reply_long(update, answer[:2500])
            return

        except Exception:
            pass

    prompt = f"""
Проанализируй thumbnail strategy ролика.

Название:
{title}

Thumbnail URL:
{thumb_url}

Описание:
{description}

Картинку получить не удалось, анализируй по названию и теме.

Дай:
1. Вероятная кликабельность
2. Что может быть непонятно зрителю
3. 3 строгих варианта превью
4. Текст на каждом превью
5. Оценка каждого превью /10
6. Лучший вариант превью
7. Как сохранить интригу без грязного хайпа
"""
    await reply_long(update, ask_ai(prompt))



def get_ru_competitor_watchlist():
    memory = load_memory()
    names = []

    for item in get_memory_list(memory, "ru_cis_sentiment_watchlist", DEFAULT_RU_CIS_SENTIMENT_WATCHLIST):
        if isinstance(item, dict):
            name = item.get("name", "")
        else:
            name = str(item)

        if name and name not in names:
            names.append(name)

    for name in get_memory_list(memory, "ru_cis_bloggers", DEFAULT_RU_CIS_BLOGGERS):
        if name and name not in names:
            names.append(name)

    for name in get_memory_list(memory, "ru_cis_monitoring_sources", DEFAULT_RU_CIS_MONITORING_SOURCES):
        if name and name not in names:
            names.append(name)

    return names[:35]


def collect_ru_competitor_topics(max_channels=25, per_channel=2):
    channels = get_ru_competitor_watchlist()[:max_channels]

    def fetch_one(name):
        try:
            query = f'{name} биткоин крипта рынок BTC'
            results = youtube_search(query, max_results=per_channel)
            output = []

            for r in results:
                output.append({
                    "source": name,
                    "title": r.get("title", ""),
                    "channel": r.get("channel", ""),
                    "publishedAt": r.get("publishedAt", ""),
                    "videoId": r.get("videoId", "")
                })

            return output
        except Exception:
            return []

    collected = []

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
        futures = [executor.submit(fetch_one, name) for name in channels]
        for future in concurrent.futures.as_completed(futures):
            collected.extend(future.result())

    return collected[:70]


def build_ahead_of_competitors_report():
    memory_context = get_memory_policy_context()
    competitor_topics = collect_ru_competitor_topics(max_channels=20, per_channel=2)
    news = get_rss_news()[:15]
    west = youtube_search("bitcoin crypto market macro ETF liquidity", max_results=8)
    search_demand = collect_search_demand_proxy(max_queries=20)

    prompt = f"""
Ты — стратег YouTube-канала HiFi Trade.
Задача: смотреть темы RU/CIS crypto-блогеров и предложить тему, которая может быть сильнее их тем.

Важно:
- Не копируй конкурентов.
- Нужно быть на шаг впереди.
- Не выдавай сырой инфоповод; превращай его в понятный конфликт для зрителя.
- Смотри, что они обсуждают, и найди более сильный угол.
- Тема должна быть понятная, серьёзная, без скама, мемкоинов и low-cap мусора.
- Выдавай только варианты 8/10+.
- Ответ должен уместиться в одно Telegram-сообщение.

Политика памяти:
{json.dumps(memory_context, ensure_ascii=False, indent=2)}

Темы RU/CIS конкурентов:
{json.dumps(competitor_topics, ensure_ascii=False, indent=2)}

Новости и рынок:
{json.dumps(news, ensure_ascii=False, indent=2)}

WEST инфополе:
{json.dumps(west, ensure_ascii=False, indent=2)}

Search Demand Proxy:
{json.dumps(search_demand, ensure_ascii=False, indent=2)}

Формат строго:

🧠 На шаг впереди конкурентов

Конкуренты сейчас:
1. ...
2. ...
3. ...

Слабое место:
...

Лучшая тема:
...

Оценка: .../10

Название:
...

Хук:
...

Превью:
1. "..." — .../10
2. "..." — .../10
3. "..." — .../10

Как отличиться:
...
"""

    answer = ask_ai_packaged(prompt, max_chars=2200)
    remember_generated_content(
        kind="ahead_of_competitors",
        title=extract_first_title(answer),
        summary=answer,
        source="ru_competitor_monitor"
    )
    return answer


async def ahead(update: Update, context: ContextTypes.DEFAULT_TYPE):
    answer = build_ahead_of_competitors_report()
    await reply_long(update, "🧠 На шаг впереди RU/CIS конкурентов\n\n" + answer)


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

Дай кратко:
1. Что сейчас обсуждают: 3 пункта
2. Что перегрето: 2 пункта
3. Что недопокрыто: 3 пункта
4. 3 long-form идеи с оценкой /10
5. 3 Shorts с оценкой /10
6. Лучшая тема сейчас и почему
"""
    answer = ask_ai(prompt)
    remember_report("monitor", answer)
    await reply_long(update, answer)

async def competitors(update: Update, context: ContextTypes.DEFAULT_TYPE):
    memory = load_memory()
    ru = get_memory_list(memory, "ru_cis_bloggers", DEFAULT_RU_CIS_BLOGGERS)
    west = get_memory_list(memory, "west_bloggers", DEFAULT_WEST_BLOGGERS)

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

Дай кратко:
1. RU/CIS: 3 главных темы
2. WEST: 3 главных темы
3. Где ждут рост/падение
4. Перегретые темы: 2 пункта
5. Недопокрытые темы: 3 пункта
6. 3 идеи для HiFi Trade с оценкой /10
7. Лучшая идея
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
5. 5 тем для HiFi Trade с оценкой каждой /10
6. Лучшая тема и почему
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
4. 5 идей роликов с оценкой каждой /10
5. Лучшая идея и почему
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
    answer = ask_ai(prompt)
    remember_generated_content(
        kind="opportunity",
        title=extract_first_title(answer),
        summary=answer,
        source="manual_opportunity"
    )
    await reply_long(update, answer)

async def remember_success(update: Update, context: ContextTypes.DEFAULT_TYPE):
    url = " ".join(context.args).strip()
    if not url:
        await safe_reply(update, "Используй: /remember_success ссылка")
        return

    video = get_video_data(url)
    if not video:
        await safe_reply(update, "Видео не найдено.")
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

    await safe_reply(update, "Успешный ролик запомнен ✅")

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

async def scoreidea(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = " ".join(context.args).strip()
    if not query:
        await safe_reply(update, "Используй: /scoreidea идея_ролика")
        return

    prompt = f"""
Оцени идею ролика для HiFi Trade по 10-балльной шкале.

Идея:
{query}

Дай:
1. Общая оценка /10
2. CTR /10
3. Удержание /10
4. Интерес аудитории /10
5. Потенциал подписок /10
6. Риски темы
7. Как усилить до 9/10
8. Лучшее название
9. Хук первых 10 секунд
10. 3 превью с оценкой /10
"""
    await reply_long(update, ask_ai(prompt, max_chars=2500))


async def scoretitle(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = " ".join(context.args).strip()
    if not query:
        await safe_reply(update, "Используй: /scoretitle название_ролика")
        return

    prompt = f"""
Оцени название ролика для HiFi Trade.

Название:
{query}

Дай:
1. Оценка названия /10
2. Понятность /10
3. Интрига /10
4. CTR potential /10
5. Что слабое
6. 5 улучшенных вариантов названия
7. Лучший вариант и почему
"""
    await reply_long(update, ask_ai(prompt, max_chars=2200))


async def weekly_plan(update: Update, context: ContextTypes.DEFAULT_TYPE):
    answer = build_weekly_content_plan()
    await reply_long(update, "🗓 Контент-план на неделю\n\n" + answer)




async def import_perf(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Импорт метрик роликов текстом.
    Формат после команды:
    title,views,ctr,retention,subscribers,notes
    Почему Bitcoin не падает,1500,6.2,38,12,простой заголовок сработал
    """
    raw = update.message.text or ""
    raw = raw.replace("/import_perf", "", 1).strip()

    if not raw:
        await safe_reply(update, 
            "Пришли данные так:\n\n"
            "/import_perf\n"
            "title,views,ctr,retention,subscribers,notes\n"
            "Почему Bitcoin не падает,1500,6.2,38,12,простой заголовок сработал\n"
            "Альткоины готовы к росту,700,3.1,22,2,слишком общий заголовок"
        )
        return

    try:
        reader = csv.DictReader(StringIO(raw))
        rows = list(reader)
        saved = remember_video_performance_bulk(rows)
    except Exception as e:
        await safe_reply(update, f"Не смог разобрать таблицу: {e}")
        return

    if not saved:
        await safe_reply(update, "Не нашёл ролики для импорта. Проверь заголовки колонок: title,views,ctr,retention,subscribers,notes")
        return

    await safe_reply(update, f"Импортировал метрики роликов: {len(saved)} шт.\nТеперь можно вызвать /performance")


async def editor10(update: Update, context: ContextTypes.DEFAULT_TYPE):
    idea = " ".join(context.args).strip()

    if not idea:
        await safe_reply(update, 
            "Используй:\n"
            "/editor10 идея ролика\n\n"
            "Пример:\n"
            "/editor10 Почему Bitcoin не падает несмотря на страх на рынке"
        )
        return

    prompt = f"""
Оцени и доработай идею ролика для HiFi Trade в режиме главного редактора 10/10.

Идея:
{idea}

Правила:
{TEN_OUT_OF_TEN_EDITOR_RULES}

Структура:
{VIDEO_SCRIPT_10X_STRUCTURE}

Память эффективности роликов:
{json.dumps(performance_context_for_prompt(), ensure_ascii=False, indent=2)}

Дай:
1. Оценка идеи /10
2. Что в идее сильного
3. Что слабого
4. Как сделать её не инфоцыганской
5. Название
6. Текст на превью
7. Хук
8. Структура ролика
9. Данные/источники для экрана
10. Контраргумент
11. Финальный вывод

Ответ на русском. Без воды.
"""

    await reply_long(update, ask_ai(prompt, max_chars=3500))


async def strategy10(update: Update, context: ContextTypes.DEFAULT_TYPE):
    prompt = f"""
Ты главный редактор HiFi Trade.

Проанализируй память эффективности роликов и правила канала.

Память эффективности:
{json.dumps(performance_context_for_prompt(), ensure_ascii=False, indent=2)}

Сводка паттернов:
{json.dumps(summarize_performance_patterns(), ensure_ascii=False, indent=2)}

Дай:
1. Текущая оценка канала по контент-стратегии /10
2. Какие темы стоит усиливать
3. Какие темы стоит убирать
4. Какие заголовки стоит повторять
5. Какие превью стоит повторять
6. 5 идей больших роликов
7. 5 идей Shorts
8. Что делать на этой неделе

Ответ компактно, но по делу.
"""

    await reply_long(update, ask_ai(prompt, max_chars=3500))



async def remember_perf(update: Update, context: ContextTypes.DEFAULT_TYPE):
    raw = " ".join(context.args).strip()

    if not raw:
        await safe_reply(update, 
            "Используй:\n"
            "/remember_perf title=... views=... ctr=... retention=... subscribers=... url=... notes=..."
        )
        return

    def extract_field(name, default=""):
        pattern = rf'{name}=([^=]+?)(?=\s+\w+=|$)'
        match = re.search(pattern, raw)
        if not match:
            return default
        return match.group(1).strip()

    title = extract_field("title")
    views = extract_field("views", "0")
    ctr = extract_field("ctr", "")
    retention = extract_field("retention", "")
    subscribers = extract_field("subscribers", "0")
    url = extract_field("url", "")
    notes = extract_field("notes", "")

    if not title:
        await safe_reply(update, "Не вижу title=. Пример: /remember_perf title=BTC не падает views=1500 ctr=6.2 retention=38 subscribers=12")
        return

    item = remember_video_performance(
        title=title,
        url=url,
        views=views,
        ctr=ctr if ctr else None,
        retention=retention if retention else None,
        subscribers=subscribers,
        notes=notes
    )

    await safe_reply(update, 
        f"Запомнил результат ролика.\n"
        f"Название: {item['title']}\n"
        f"Результат: {item['result']}\n"
        f"Views: {item['views']}\n"
        f"CTR: {item['ctr']}\n"
        f"Retention: {item['retention']}"
    )


async def performance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    perf = get_performance_memory()
    recent = perf.get("video_performance", [])[-10:]

    if not recent:
        await safe_reply(update, "Пока нет сохранённых результатов роликов.")
        return

    prompt = f"""
Проанализируй результаты роликов HiFi Trade.

Данные:
{json.dumps(recent, ensure_ascii=False, indent=2)}

Дай кратко:
1. Что работает
2. Что не работает
3. Какие темы/форматы повторить
4. Какие темы избегать
5. 3 рекомендации для следующих роликов
6. Что улучшить в заголовках/превью

Ответ на русском.
"""

    await reply_long(update, ask_ai(prompt, max_chars=2500))


async def memory_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    data = load_success()
    used = data.get("used_ideas", [])
    notes = data.get("weekly_notes", [])
    policy = get_memory_policy_context()

    last_ideas = used[-5:]
    lines = []
    lines.append("🧠 Память агента")
    lines.append("")
    lines.append(f"Запомнено идей: {len(used)}")
    lines.append(f"Запомнено отчётов: {len(notes)}")
    lines.append("")
    lines.append("Политика повторов:")
    lines.append("- 0–14 дней: не повторять")
    lines.append("- 15–30 дней: можно только с новым инфоповодом")
    lines.append("- 30+ дней: вечные темы можно вернуть с новым углом")
    lines.append("")
    lines.append(f"В паузе 0–14 дней: {len(policy.get('avoid_repeating_now', []))}")
    lines.append(f"Можно вернуть с новым углом: {len(policy.get('may_return_only_with_new_angle', []))}")
    lines.append(f"Вечные темы 30+ дней: {len(policy.get('evergreen_can_return_with_updated_angle', []))}")
    lines.append("")
    lines.append("Последние идеи:")

    if not last_ideas:
        lines.append("—")
    else:
        for item in last_ideas:
            kind = item.get("kind", "")
            title = item.get("title", "")
            lines.append(f"- {kind}: {title}")

    await reply_long(update, "\n".join(lines))


async def cheap(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = " ".join(context.args).strip()
    if not query:
        await safe_reply(update, "Напиши так: /cheap вопрос")
        return
    await reply_long(update, ask_ai("Ответь максимально кратко: " + query, max_chars=1200))

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.effective_chat.id)
    user_text = update.message.text

    history = conversation_history.get(chat_id, [])
    history.append({"role": "user", "content": user_text})
    history = history[-8:]

    memory = get_compact_memory()
    success = get_compact_success()

    system_context = f"""
Ты AI-команда продвижения HiFi Trade.
Отвечай кратко и полезно.
Не предлагай мемкоины, скам, low-cap мусор.

Память канала:
{json.dumps(memory, ensure_ascii=False, indent=2)}

Краткая память:
{json.dumps(success, ensure_ascii=False, indent=2)}
"""

    response = client.responses.create(
        model=MODEL_CHEAP,
        max_output_tokens=900,
        input=[
            {"role": "system", "content": system_context},
            *history
        ]
    )

    answer = response.output_text or "Не удалось получить ответ."

    history.append({"role": "assistant", "content": answer})
    conversation_history[chat_id] = history[-8:]

    await reply_long(update, answer[:2200])



def normalize_event_key(title):
    if not title:
        return ""

    value = title.lower()
    for ch in [":", ";", ",", ".", "!", "?", "—", "-", "–", "|", "«", "»", "\"", "'", "(", ")", "[", "]"]:
        value = value.replace(ch, " ")

    words = [w.strip() for w in value.split() if len(w.strip()) > 3]
    stop = {
        "bitcoin", "биткоин", "crypto", "крипто", "рынок", "market",
        "today", "сегодня", "will", "после", "this", "that", "with",
        "from", "что", "как", "для", "или", "цена", "price"
    }
    words = [w for w in words if w not in stop]
    return " ".join(words[:10])


def score_monitor_event(title, source=""):
    text_value = f"{title} {source}".lower()
    score = 0

    strong_terms = {
        "bitcoin": 3, "btc": 3, "биткоин": 3,
        "ethereum": 2, "eth": 2, "эфир": 2,
        "etf": 4, "flows": 3, "приток": 3, "отток": 3,
        "fed": 4, "фрс": 4, "rate": 3, "ставк": 3,
        "inflation": 3, "инфляц": 3,
        "liquidity": 4, "ликвид": 4,
        "sec": 3, "regulation": 3, "регуляц": 3,
        "blackrock": 3, "fidelity": 3,
        "recession": 3, "рецесс": 3,
        "bond": 3, "treasury": 3, "облигац": 3,
        "dxy": 3, "dollar": 2, "доллар": 2,
        "dominance": 2, "доминац": 2,
        "stablecoin": 2, "стейбл": 2
    }

    for term, pts in strong_terms.items():
        if term in text_value:
            score += pts

    weak_terms = [
        "meme", "memecoin", "мемкоин", "airdrop", "presale",
        "100x", "100х", "shiba", "doge", "pepe", "floki",
        "giveaway", "розыгрыш", "best coin to buy"
    ]

    if any(term in text_value for term in weak_terms):
        score -= 20

    return score


def collect_light_monitor_events():
    events = []

    try:
        market = get_market_snapshot()
        for asset in market.get("assets", []):
            ch24 = float(asset.get("change_24h_pct") or 0)
            ch7 = float(asset.get("change_7d_pct") or 0)
            if abs(ch24) >= 4 or abs(ch7) >= 8:
                title = f"{asset.get('asset')} move: {ch24}% 24h, {ch7}% 7d"
                events.append({
                    "key": stable_hash(title),
                    "type": "market_data",
                    "title": title,
                    "source": "CoinGecko",
                    "score": 10 + int(abs(ch24)) + int(abs(ch7) / 2)
                })
        if float(market.get("btc_dominance_pct") or 0) >= 55:
            title = f"BTC dominance high: {market.get('btc_dominance_pct')}%"
            events.append({"key": stable_hash(title), "type": "market_data", "title": title, "source": "CoinGecko Global", "score": 12})
    except Exception:
        logger.exception("Market snapshot monitor failed")

    try:
        for item in get_rss_news()[:18]:
            title = item.get("title", "")
            source = item.get("source", "")
            key = normalize_event_key(title)
            score = score_monitor_event(title, source)

            if key and score >= 5:
                events.append({
                    "key": key,
                    "type": "news",
                    "title": title,
                    "source": source,
                    "score": score
                })
    except Exception:
        pass

    try:
        competitor_topics = collect_ru_competitor_topics(max_channels=12, per_channel=1)
        for item in competitor_topics[:20]:
            title = item.get("title", "")
            source = item.get("source", item.get("channel", ""))
            key = normalize_event_key(title)
            score = score_monitor_event(title, source) + 1

            if key and score >= 5:
                events.append({
                    "key": key,
                    "type": "ru_competitor",
                    "title": title,
                    "source": source,
                    "score": score
                })
    except Exception:
        pass

    try:
        youtube_queries = [
            "биткоин рынок сегодня ETF ликвидность",
            "криптовалюта рынок ФРС биткоин",
            "bitcoin ETF flows liquidity macro",
            "bitcoin crypto market Fed liquidity"
        ]

        for q in youtube_queries:
            for item in youtube_search(q, max_results=3):
                title = item.get("title", "")
                source = item.get("channel", "")
                key = normalize_event_key(title)
                score = score_monitor_event(title, source)

                if key and score >= 5:
                    events.append({
                        "key": key,
                        "type": "youtube_trend",
                        "title": title,
                        "source": source,
                        "score": score
                    })
    except Exception:
        pass

    seen = set()
    unique = []

    for e in sorted(events, key=lambda x: x.get("score", 0), reverse=True):
        key = e.get("key", "")
        if not key or key in seen:
            continue
        seen.add(key)
        unique.append(e)

    return unique[:20]


def pick_new_strong_events(events, min_score=12, limit=3):
    seen_keys = get_seen_event_keys()
    picked = []

    for e in events:
        key = e.get("key", "")
        if not key or key in seen_keys:
            continue

        if e.get("score", 0) >= min_score:
            picked.append(e)

    return picked[:limit]


def build_smart_monitor_alert(events):
    """
    Strict smart monitoring.
    Must not turn every event into an alert.
    Sends alert only if AI explicitly says ALERT_YES.
    """
    memory_context = get_memory_policy_context()

    prompt = f"""
Ты строгий редакторский фильтр HiFi Trade.

Задача: решить, стоит ли беспокоить владельца канала alert-сообщением.

Важно:
- Не каждый инфоповод достоин сообщения.
- Если тема обычная, повторная, слабая, слишком общая или уже похожа на прошлые — ответь только NO_ALERT.
- Alert нужен только если тема реально сильная, свежая и может дать контент 8.5/10+.
- Не превращай сырой инфоповод в идею любой ценой.
- Если сомневаешься — NO_ALERT.
- Мемкоины, 100x, airdrop, presale, low-cap мусор — всегда NO_ALERT.
- Не более одной главной темы в ответе.
- Ответ должен уместиться в одно Telegram-сообщение.

Критерии ALERT_YES:
1. Есть свежий сильный инфоповод.
2. Он связан с BTC / ETH / ETF / ликвидностью / ФРС / макро / регуляцией / институционалами.
3. Есть понятный YouTube/Telegram-угол.
4. Тема не выглядит повтором старой.
5. Оценка не ниже 8.5/10.

Память и анти-повтор:
{json.dumps(memory_context, ensure_ascii=False, indent=2)}

Новые события:
{json.dumps(events, ensure_ascii=False, indent=2)}

Если alert НЕ нужен, ответь строго:
NO_ALERT

Если alert нужен, формат строго такой:

ALERT_YES

🚨 Сильный инфоповод

Тема:
...

Почему это реально важно:
...

Оценка: .../10

Куда подходит:
Telegram / Shorts / Ролик

Человеческий угол:
...

Заголовок:
...

Превью:
"..."
"""

    answer = ask_ai(prompt, max_chars=1600)

    if "ALERT_YES" not in answer[:80]:
        return "NO_ALERT"

    return answer.replace("ALERT_YES", "", 1).strip()


async def smart_monitor_tick(app, chat_id):
    memory = load_memory()
    min_score = int(memory.get("smart_monitor_min_score", 12))
    max_alerts_per_day = int(memory.get("smart_monitor_max_alerts_per_day", 2))

    state = get_runtime_state()
    today = datetime.now(get_timezone()).strftime("%Y-%m-%d")
    alerts_today_key = f"smart_alerts_{today}"
    alerts_today = int(state.get(alerts_today_key, 0))

    if alerts_today >= max_alerts_per_day:
        return

    events = collect_light_monitor_events()
    strong_events = pick_new_strong_events(events, min_score=min_score, limit=3)

    if not strong_events:
        return

    alert = build_smart_monitor_alert(strong_events)

    if not alert or "NO_ALERT" in alert.strip()[:30]:
        for e in strong_events:
            save_seen_event_key(e.get("key", ""))
        return

    remember_report("smart_monitor_alert", alert)
    remember_generated_content(
        kind="smart_monitor_alert",
        title=extract_first_title(alert),
        summary=alert,
        source="smart_monitor"
    )

    await send_long(app, chat_id, alert)

    for e in strong_events:
        save_seen_event_key(e.get("key", ""))

    state = get_runtime_state()
    state[alerts_today_key] = alerts_today + 1
    save_runtime_state(state)


def scam_risk_score_text(text):
    """Heuristic risk engine for topics/tokens/links before editorial use."""
    lower = (text or "").lower()
    risk = 0
    reasons = []
    red_flags = {
        "100x": 25, "100х": 25, "икс": 10, "guaranteed": 25, "гарант": 25,
        "airdrop": 15, "эйрдроп": 15, "presale": 20, "предпродаж": 20,
        "meme": 18, "memecoin": 18, "мемкоин": 18, "shiba": 20, "doge": 20, "pepe": 20, "floki": 20, "bonk": 18,
        "low cap": 18, "low-cap": 18, "hidden gem": 18, "gem": 10,
        "срочно покупай": 30, "buy now": 18, "to the moon": 15, "moonshot": 20,
        "telegram signal": 18, "vip signal": 22, "private sale": 25, "whitelist": 12,
        "без риска": 25, "risk free": 25, "пассивный доход": 12
    }
    for k, pts in red_flags.items():
        if k in lower:
            risk += pts
            reasons.append(f"красный флаг: {k}")
    serious_terms = ["bitcoin", "btc", "ethereum", "eth", "etf", "fed", "фрс", "liquidity", "ликвид", "regulation", "регуляц", "risk management"]
    if any(k in lower for k in serious_terms):
        risk = max(0, risk - 10)
    risk = min(100, risk)
    level = "LOW" if risk < 25 else "MEDIUM" if risk < 60 else "HIGH"
    return {"risk_score": risk, "risk_level": level, "reasons": reasons[:8]}


def build_market_report():
    market = get_market_snapshot()
    fear = get_fear_greed_index()
    news = get_rss_news()[:10]
    prompt = f"""
Сделай быстрый market intelligence report для Telegram-канала HiFi Trade.

Fear & Greed:
{fear}

Market snapshot:
{json.dumps(market, ensure_ascii=False, indent=2)}

Новости:
{json.dumps(news, ensure_ascii=False, indent=2)}

Формат:
📈 Market Now

1. Состояние рынка: bullish/bearish/neutral и почему.
2. BTC: что важно.
3. ETH: что важно.
4. Альты: есть ли риск/ротация.
5. Главный риск дня.
6. Что можно дать в Telegram.
7. Что НЕ стоит шиллировать.

Без торговых рекомендаций. Без обещаний прибыли. Компактно.
"""
    answer = ask_ai(prompt, max_chars=2200)
    remember_report("market_now", answer)
    return answer


async def market_now(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await reply_long(update, build_market_report())


async def riskcheck(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = " ".join(context.args).strip()
    if not query:
        await safe_reply(update, "Используй: /riskcheck название_токена_или_тема_или_текст")
        return
    heuristic = scam_risk_score_text(query)
    prompt = f"""
Проверь тему/токен/инфоповод на риск для честного crypto media канала.

Объект проверки:
{query}

Heuristic risk:
{json.dumps(heuristic, ensure_ascii=False, indent=2)}

Дай:
1. Risk level: LOW/MEDIUM/HIGH.
2. Почему.
3. Можно ли упоминать в Telegram/YouTube.
4. Как безопасно подать, если упоминать.
5. Что обязательно НЕ говорить.
6. Нужен ли дисклеймер.

Если данных мало — прямо скажи, что это предварительная проверка, а не on-chain аудит.
"""
    await reply_long(update, ask_ai(prompt, max_chars=2200))


def build_watchlist_status():
    memory = load_memory()
    rows = collect_btc_sentiment_influencers()
    state = get_runtime_state()
    lines = [
        "🧭 Статус агента",
        "",
        f"Источников sentiment/watchlist: {len(rows)}",
        f"RU/CIS источников: {sum(1 for r in rows if r.get('market') == 'RU/CIS')}",
        f"WEST источников: {sum(1 for r in rows if r.get('market') == 'WEST')}",
        f"Seen event keys: {len(state.get('seen_event_keys', []))}",
        f"Sent keys: {len(state.get('sent_keys', []))}",
        f"Smart monitor: {'ON' if memory.get('smart_monitor_enabled', True) else 'OFF'}",
        f"Smart monitor min score: {memory.get('smart_monitor_min_score', 12)}",
        f"Max alerts/day: {memory.get('smart_monitor_max_alerts_per_day', 2)}",
        "",
        "Если здесь 0 — бот читает старый/пустой memory.json. В v29 есть резервный список прямо в коде, но лучше также заменить memory.json на v16."
    ]
    return "\n".join(lines)


async def watchlist_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await reply_long(update, build_watchlist_status())


async def smart_monitor_now(update: Update, context: ContextTypes.DEFAULT_TYPE):
    memory = load_memory()
    min_score = int(memory.get("smart_monitor_min_score", 12))

    events = collect_light_monitor_events()
    strong_events = pick_new_strong_events(events, min_score=min_score, limit=3)

    if not strong_events:
        await safe_reply(update, "Сильных новых инфоповодов пока не найдено.")
        return

    alert = build_smart_monitor_alert(strong_events)

    if not alert or "NO_ALERT" in alert.strip()[:30]:
        await safe_reply(update, "События есть, но они пока недостаточно сильные для alert.")
        for e in strong_events:
            save_seen_event_key(e.get("key", ""))
        return

    remember_report("manual_smart_monitor", alert)
    await reply_long(update, alert)



async def search_demand(update: Update, context: ContextTypes.DEFAULT_TYPE):
    answer = build_search_demand_report()
    await reply_long(update, answer)



def get_yt_analytics_row_for_video(video_id, days=365):
    end_date = datetime.utcnow().date().isoformat()
    start_date = (datetime.utcnow().date() - timedelta(days=days)).isoformat()

    data, err = yt_analytics_query(
        start_date=start_date,
        end_date=end_date,
        dimensions="video",
        metrics="views,estimatedMinutesWatched,averageViewDuration,averageViewPercentage,subscribersGained",
        sort="-views",
        max_results=200
    )
    if err:
        return None, err

    headers = [h.get("name") for h in data.get("columnHeaders", [])]
    rows = data.get("rows", [])

    for row in rows:
        row_data = dict(zip(headers, row))
        if row_data.get("video") == video_id:
            return row_data, None

    return None, "Видео не найдено в YouTube Analytics за выбранный период."



def extract_youtube_handle_or_channel(value):
    value = str(value or "").strip()
    if not value:
        return ""

    if "youtube.com/@" in value:
        return value.split("youtube.com/@", 1)[1].split("/", 1)[0].split("?", 1)[0]

    if "@" in value and "youtube" not in value:
        return value.split("@", 1)[1].split("/", 1)[0].strip()

    if "youtube.com/channel/" in value:
        return value.split("youtube.com/channel/", 1)[1].split("/", 1)[0].split("?", 1)[0]

    return value


def youtube_search_channels(query, max_results=1):
    api_key = os.getenv("YOUTUBE_API_KEY", "").strip()
    if not api_key:
        return None, "Не задан YOUTUBE_API_KEY."

    data, err = youtube_api_get(
        "https://www.googleapis.com/youtube/v3/search",
        params={
            "part": "snippet",
            "type": "channel",
            "q": query,
            "maxResults": max_results
        },
        use_oauth=False
    )
    if err:
        return None, err

    return data.get("items", []), None


def get_channel_uploads_playlist(channel_id):
    data, err = youtube_api_get(
        "https://www.googleapis.com/youtube/v3/channels",
        params={
            "part": "snippet,contentDetails,statistics",
            "id": channel_id
        },
        use_oauth=False
    )
    if err:
        return None, None, err

    items = data.get("items", [])
    if not items:
        return None, None, "Канал не найден."

    item = items[0]
    uploads = item.get("contentDetails", {}).get("relatedPlaylists", {}).get("uploads")
    return uploads, item, None


def get_recent_videos_from_uploads_playlist(playlist_id, max_results=5):
    data, err = youtube_api_get(
        "https://www.googleapis.com/youtube/v3/playlistItems",
        params={
            "part": "snippet,contentDetails",
            "playlistId": playlist_id,
            "maxResults": max_results
        },
        use_oauth=False
    )
    if err:
        return None, err

    video_ids = [
        item.get("contentDetails", {}).get("videoId")
        for item in data.get("items", [])
        if item.get("contentDetails", {}).get("videoId")
    ]

    if not video_ids:
        return [], None

    videos, err = youtube_api_get(
        "https://www.googleapis.com/youtube/v3/videos",
        params={
            "part": "snippet,statistics,contentDetails",
            "id": ",".join(video_ids)
        },
        use_oauth=False
    )
    if err:
        return None, err

    return videos.get("items", []), None


def normalize_competitor_entry(entry):
    if isinstance(entry, dict):
        name = entry.get("name") or entry.get("title") or entry.get("channel") or ""
        url = entry.get("url") or entry.get("link") or ""
        platform = entry.get("platform", "")
    else:
        name = str(entry)
        url = ""
        platform = ""

    return {
        "name": str(name).strip(),
        "url": str(url).strip(),
        "platform": str(platform).strip()
    }


def get_competitor_candidates(memory):
    raw = []
    for key in ["ru_cis_bloggers", "west_bloggers"]:
        value = memory.get(key, [])
        if isinstance(value, list):
            raw.extend(value)

    result = []
    seen = set()

    for entry in raw:
        item = normalize_competitor_entry(entry)
        name = item.get("name", "")
        url = item.get("url", "")

        # Берём только YouTube или элементы, где можно попробовать поиск по названию.
        if not name:
            continue

        identity = (name.lower(), url.lower())
        if identity in seen:
            continue
        seen.add(identity)
        result.append(item)

    return result


def scan_competitor_youtube_channel(competitor, max_videos=5):
    name = competitor.get("name", "")
    url = competitor.get("url", "")

    channel_id = ""
    query = name

    if "youtube.com/channel/" in url:
        channel_id = extract_youtube_handle_or_channel(url)
    elif "youtube.com/@" in url:
        query = "@" + extract_youtube_handle_or_channel(url)
    elif url:
        query = url

    if not channel_id:
        channels, err = youtube_search_channels(query, max_results=1)
        if err:
            return None, err
        if not channels:
            return None, f"Не найден YouTube-канал для {name}"

        channel_id = channels[0].get("snippet", {}).get("channelId")
        if not channel_id:
            return None, f"Не найден channelId для {name}"

    uploads, channel_meta, err = get_channel_uploads_playlist(channel_id)
    if err:
        return None, err
    if not uploads:
        return None, f"Не найден uploads playlist для {name}"

    videos, err = get_recent_videos_from_uploads_playlist(uploads, max_results=max_videos)
    if err:
        return None, err

    channel_title = channel_meta.get("snippet", {}).get("title", name) if channel_meta else name

    parsed = []
    for video in videos:
        snippet = video.get("snippet", {})
        stats = video.get("statistics", {})
        vid = video.get("id", "")

        parsed.append({
            "competitor": name,
            "channel_title": channel_title,
            "video_id": vid,
            "title": snippet.get("title", ""),
            "published_at": snippet.get("publishedAt", ""),
            "url": f"https://youtu.be/{vid}" if vid else "",
            "views": stats.get("viewCount"),
            "likes": stats.get("likeCount"),
            "comments": stats.get("commentCount")
        })

    return parsed, None


def save_competitor_videos_to_memory(videos):
    db_data = load_competitor_video_db_data()
    db = db_data.get("competitor_video_db", [])
    if not isinstance(db, list):
        db = []

    seen = {item.get("video_id") for item in db if isinstance(item, dict)}
    added = []

    for video in videos:
        vid = video.get("video_id")
        if not vid or vid in seen:
            continue
        video["saved_at"] = datetime.utcnow().isoformat()
        db.append(video)
        seen.add(vid)
        added.append(video)

    db_data["competitor_video_db"] = db[-500:]
    db_data["competitor_video_db_updated_at"] = datetime.utcnow().isoformat()
    save_competitor_video_db_data(db_data)

    return added


def competitor_video_db_summary(limit=80):
    db_data = load_competitor_video_db_data()
    db = db_data.get("competitor_video_db", [])
    if not isinstance(db, list):
        db = []

    recent = db[-limit:]

    return {
        "total_saved": len(db),
        "recent": recent
    }


async def auto_competitor_learn_tick(app, chat_id, max_channels=12, max_videos=3, silent=True):
    memory = load_memory()
    candidates = get_competitor_candidates(memory)[:max_channels]

    if not candidates:
        if not silent:
            await send_long(app, chat_id, "⚠️ Не нашёл список конкурентов в memory.json.")
        return

    all_videos = []
    errors = []

    for competitor in candidates:
        videos, err = scan_competitor_youtube_channel(competitor, max_videos=max_videos)
        if err:
            errors.append(f"{competitor.get('name')}: {err}")
            continue
        all_videos.extend(videos or [])

    added = save_competitor_videos_to_memory(all_videos)

    if silent and not added:
        return

    text = (
        f"🧠 База конкурентов обновлена\n\n"
        f"Проверено каналов: {len(candidates)}\n"
        f"Новых роликов добавлено: {len(added)}\n"
        f"Ошибок: {len(errors)}"
    )

    if added:
        text += "\n\nНовые ролики:\n"
        for item in added[:10]:
            text += f"— {item.get('channel_title')}: {item.get('title')}\n{item.get('url')}\n"

    if errors and not silent:
        text += "\n\nОшибки:\n" + "\n".join(errors[:8])

    await send_long(app, chat_id, text)


async def auto_youtube_learn_tick(app, chat_id, days=7):
    saved, err = yt_learn_from_analytics(days=days, max_results=15)

    if err:
        await send_long(app, chat_id, f"⚠️ Автообновление YouTube-метрик не сработало:\n{err}")
        return

    if not saved:
        return

    success_count = len([x for x in saved if x.get("result") == "success"])
    weak_count = len([x for x in saved if x.get("result") == "weak"])

    text = (
        f"📊 YouTube-метрики обновлены автоматически\n\n"
        f"Период: последние {days} дней\n"
        f"Импортировано роликов: {len(saved)}\n"
        f"Сильных: {success_count}\n"
        f"Слабых: {weak_count}\n\n"
        f"Теперь /performance и /strategy10 учитывают свежие данные."
    )

    await send_long(app, chat_id, text)


async def auto_weekly_performance_strategy_tick(app, chat_id):
    prompt = f"""
Ты главный редактор HiFi Trade.

Сделай еженедельный автоматический отчёт по эффективности канала на базе сохранённых YouTube-метрик.

Память эффективности:
{json.dumps(performance_context_for_prompt(), ensure_ascii=False, indent=2)}

Сводка паттернов:
{json.dumps(summarize_performance_patterns(), ensure_ascii=False, indent=2)}

Дай:
1. Что сработало на этой неделе
2. Что не сработало
3. Какие темы повторять
4. Какие заголовки/превью повторять
5. Какие темы избегать
6. 3 идеи больших роликов на следующую неделю
7. 3 идеи Shorts
8. Главный вывод редактора

Ответ на русском, без воды.
"""

    answer = ask_ai(prompt, max_chars=3500)
    await send_long(app, chat_id, "📈 Еженедельная стратегия по метрикам YouTube\n\n" + answer)


async def auto_new_video_check_tick(app, chat_id, target_hours):
    videos, err = get_my_recent_youtube_videos(max_results=10)

    if err or not videos:
        return

    now_utc = datetime.utcnow()

    for item in videos:
        video_id = item.get("id")
        snippet = item.get("snippet", {})
        title = snippet.get("title", "без названия")
        published_at = snippet.get("publishedAt", "")

        if not video_id or not published_at:
            continue

        try:
            published_dt = datetime.fromisoformat(published_at.replace("Z", "+00:00")).replace(tzinfo=None)
        except Exception:
            continue

        age_hours = (now_utc - published_dt).total_seconds() / 3600

        # Проверяем ролики около 24 часов и около 72 часов.
        if target_hours == 24:
            in_window = 20 <= age_hours <= 30
        else:
            in_window = 68 <= age_hours <= 80

        if not in_window:
            continue

        row, row_err = get_yt_analytics_row_for_video(video_id, days=365)
        if row_err or not row:
            continue

        prompt = f"""
Разбери ранние метрики ролика HiFi Trade через {target_hours} часов после публикации.

Название:
{title}

Ссылка:
https://youtu.be/{video_id}

Метрики:
{json.dumps(row, ensure_ascii=False, indent=2)}

Дай:
1. Ранняя оценка ролика /10
2. Ролик стартовал сильнее или слабее ожиданий?
3. Что видно по удержанию и просмотрам
4. Что можно сделать в следующих роликах
5. Нужно ли повторять тему/формат
6. Короткий вывод

Ответ на русском, без воды.
"""

        answer = ask_ai(prompt, max_chars=2500)
        await send_long(app, chat_id, f"🎬 Автопроверка ролика через {target_hours} часов\n\n" + answer)


async def scheduled_loop(app):
    sent_keys = load_sent_keys()

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
                weekly_plan_day = memory.get("weekly_content_plan_day", "Sunday")
                weekly_plan_time = memory.get("weekly_content_plan_time", "11:00")
                video_day = memory.get("video_idea_day", "Friday")
                video_time = memory.get("video_idea_time", "12:00")
                shorts_days = memory.get("shorts_idea_days", ["Monday", "Thursday"])
                shorts_time = memory.get("shorts_idea_time", "12:00")
                ahead_days = memory.get("ahead_competitors_days", ["Wednesday"])
                ahead_time = memory.get("ahead_competitors_time", "12:00")
                smart_monitor_enabled = memory.get("smart_monitor_enabled", True)
                smart_monitor_interval_minutes = int(memory.get("smart_monitor_interval_minutes", 120))

                yt_auto_learn_enabled = memory.get("yt_auto_learn_enabled", True)
                yt_auto_learn_time = memory.get("yt_auto_learn_time", "21:00")
                yt_auto_learn_days = int(memory.get("yt_auto_learn_days", 7))

                yt_weekly_strategy_enabled = memory.get("yt_weekly_strategy_enabled", True)
                yt_weekly_strategy_day = memory.get("yt_weekly_strategy_day", "Sunday")
                yt_weekly_strategy_time = memory.get("yt_weekly_strategy_time", "18:00")

                yt_new_video_check_enabled = memory.get("yt_new_video_check_enabled", True)
                yt_new_video_check_time = memory.get("yt_new_video_check_time", "20:00")

                competitor_auto_learn_enabled = memory.get("competitor_auto_learn_enabled", True)
                competitor_auto_learn_time = memory.get("competitor_auto_learn_time", "22:00")
                competitor_auto_learn_max_channels = int(memory.get("competitor_auto_learn_max_channels", 12))
                competitor_auto_learn_max_videos = int(memory.get("competitor_auto_learn_max_videos", 3))

                if current_time == daily_time:
                    key = f"{date_key}-daily"
                    if key not in sent_keys:
                        fear_greed = get_fear_greed_index()
                        news = get_rss_news()
                        ru = youtube_search("криптовалюта биткоин рынок сегодня", max_results=6)
                        west = youtube_search("bitcoin crypto market today macro", max_results=6)
                        candidates = build_news_candidates_from_sources(news, ru, west, limit=7)

                        prompt = f"""
Автоматический утренний новостной отчёт HiFi Trade в формате: «что сейчас обсуждают все».
Ищи не просто важные новости, а хайповые темы с массовым обсуждением и потенциалом роста канала.

Правила отбора:
{HYPE_NEWS_RULES}

Новости:
{json.dumps(news, ensure_ascii=False, indent=2)}

RU/CIS YouTube:
{json.dumps(ru, ensure_ascii=False, indent=2)}

WEST:
{json.dumps(west, ensure_ascii=False, indent=2)}

Сохранённый список кандидатов:
{json.dumps(candidates, ensure_ascii=False, indent=2)}

Дай:
1. Выведи 5–7 хайповых новостей под номерами из сохранённого списка кандидатов.
2. Для каждой сильной новости выведи строго:
   - Номер
   - Короткая тема
   - Почему обсуждают
   - Hype Score: X/10
   - Источник
   - Можно ли сделать пост: да/нет
3. В конце напиши: "Пост сам не публикуется. Чтобы подготовить черновик: /pick_news номер".

Не придумывай торговые рекомендации.
Не продвигай мемкоины, скам, low-cap garbage, random pumps и 100x-темы.
"""
                        answer = ask_ai(prompt)
                        remember_report("auto_morning_news", answer)
                        text = "🌅 Утренний новостной отчёт HiFi Trade\n\n" + answer
                        await send_long(app, chat_id, text)
                        mark_sent_key(sent_keys, key)

                if weekday == blogger_day and current_time == blogger_time:
                    key = f"{date_key}-bloggers"
                    if key not in sent_keys:
                        rows = collect_btc_sentiment_influencers()
                        recent_context = collect_recent_context_for_sources(rows, max_total_items=60)
                        classified = classify_source_moods(rows, recent_context)
                        report = build_blogger_mood_report(classified)
                        remember_report("auto_blogger_mood", report)
                        text = "📊 Еженедельное настроение инфополя\n\n" + report
                        await send_long(app, chat_id, text)
                        mark_sent_key(sent_keys, key)

                if weekday == weekly_plan_day and current_time == weekly_plan_time:
                    key = f"{date_key}-weekly-plan"
                    if key not in sent_keys:
                        answer = build_weekly_content_plan()
                        text = "🗓 Автоматический контент-план на неделю\n\n" + answer
                        await send_long(app, chat_id, text)
                        mark_sent_key(sent_keys, key)

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
                        answer = ask_ai_packaged(prompt, max_chars=2200)
                        remember_generated_content(
                            kind="video_idea",
                            title=extract_first_title(answer),
                            summary=answer,
                            source="auto_friday_videoidea"
                        )
                        text = "🎬 Идея большого ролика на вторник\n\n" + answer
                        await send_long(app, chat_id, text)
                        mark_sent_key(sent_keys, key)

                if weekday in ahead_days and current_time == ahead_time:
                    key = f"{date_key}-ahead-competitors"
                    if key not in sent_keys:
                        answer = build_ahead_of_competitors_report()
                        text = "🧠 На шаг впереди RU/CIS конкурентов\n\n" + answer
                        await send_long(app, chat_id, text)
                        mark_sent_key(sent_keys, key)

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
                        answer = ask_ai_packaged(prompt, max_chars=1900)
                        remember_generated_content(
                            kind="shorts_idea",
                            title=extract_first_title(answer),
                            summary=answer,
                            source="auto_shorts"
                        )
                        text = "⚡ Идея Shorts\n\n" + answer
                        await send_long(app, chat_id, text)
                        mark_sent_key(sent_keys, key)


                if yt_auto_learn_enabled and current_time == yt_auto_learn_time:
                    key = f"{date_key}-yt-auto-learn"
                    if key not in sent_keys:
                        await auto_youtube_learn_tick(app, chat_id, days=yt_auto_learn_days)
                        mark_sent_key(sent_keys, key)

                if yt_weekly_strategy_enabled and weekday == yt_weekly_strategy_day and current_time == yt_weekly_strategy_time:
                    key = f"{date_key}-yt-weekly-strategy"
                    if key not in sent_keys:
                        await auto_weekly_performance_strategy_tick(app, chat_id)
                        mark_sent_key(sent_keys, key)

                if yt_new_video_check_enabled and current_time == yt_new_video_check_time:
                    key24 = f"{date_key}-yt-new-video-24h"
                    if key24 not in sent_keys:
                        await auto_new_video_check_tick(app, chat_id, target_hours=24)
                        mark_sent_key(sent_keys, key24)

                    key72 = f"{date_key}-yt-new-video-72h"
                    if key72 not in sent_keys:
                        await auto_new_video_check_tick(app, chat_id, target_hours=72)
                        mark_sent_key(sent_keys, key72)



                if competitor_auto_learn_enabled and current_time == competitor_auto_learn_time:
                    key = f"{date_key}-competitor-auto-learn"
                    if key not in sent_keys:
                        await auto_competitor_learn_tick(
                            app,
                            chat_id,
                            max_channels=competitor_auto_learn_max_channels,
                            max_videos=competitor_auto_learn_max_videos,
                            silent=True
                        )
                        mark_sent_key(sent_keys, key)


            if chat_id and smart_monitor_enabled:
                minute = int(datetime.now(get_timezone()).strftime("%M"))
                if smart_monitor_interval_minutes > 0 and minute % smart_monitor_interval_minutes == 0:
                    monitor_key = f"{date_key}-smart-monitor-{datetime.now(get_timezone()).strftime('%H:%M')}"
                    if monitor_key not in sent_keys:
                        await smart_monitor_tick(app, chat_id)
                        mark_sent_key(sent_keys, monitor_key)

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

    app.add_handler(CommandHandler("start", restricted(start)))
    app.add_handler(CommandHandler("health", restricted(health)))
    app.add_handler(CommandHandler("env_check", restricted(env_check)))
    app.add_handler(CommandHandler("auto_status", restricted(auto_status)))
    app.add_handler(CommandHandler("competitor_learn", restricted(competitor_learn)))
    app.add_handler(CommandHandler("competitor_digest", restricted(competitor_digest)))
    app.add_handler(CommandHandler("topic_gap_auto", restricted(topic_gap_auto)))
    app.add_handler(CommandHandler("competitor_db_status", restricted(competitor_db_status)))
    app.add_handler(CommandHandler("split_test", restricted(split_test)))
    app.add_handler(CommandHandler("yt_auth_check", restricted(yt_auth_check)))
    app.add_handler(CommandHandler("yt_recent", restricted(yt_recent)))
    app.add_handler(CommandHandler("yt_analytics", restricted(yt_analytics)))
    app.add_handler(CommandHandler("yt_learn", restricted(yt_learn)))
    app.add_handler(CommandHandler("yt_video_stats", restricted(yt_video_stats)))
    app.add_handler(CommandHandler("setchat", restricted(setchat)))
    app.add_handler(CommandHandler("morning_now", restricted(morning_now)))
    app.add_handler(CommandHandler("news_candidates", restricted(news_candidates)))
    app.add_handler(CommandHandler("pick_news", restricted(pick_news)))
    app.add_handler(CommandHandler("post_queue", restricted(post_queue)))
    app.add_handler(CommandHandler("bloggers_now", restricted(bloggers_now)))
    app.add_handler(CommandHandler("videoidea", restricted(videoidea)))
    app.add_handler(CommandHandler("shortidea", restricted(shortidea)))
    app.add_handler(CommandHandler("channel", restricted(channel)))
    app.add_handler(CommandHandler("review", restricted(review)))
    app.add_handler(CommandHandler("thumbnail", restricted(thumbnail)))
    app.add_handler(CommandHandler("monitor", restricted(monitor)))
    app.add_handler(CommandHandler("ahead", restricted(ahead)))
    app.add_handler(CommandHandler("competitors", restricted(competitors)))
    app.add_handler(CommandHandler("trendru", restricted(trendru)))
    app.add_handler(CommandHandler("trendwest", restricted(trendwest)))
    app.add_handler(CommandHandler("opportunity", restricted(opportunity)))
    app.add_handler(CommandHandler("remember_success", restricted(remember_success)))
    app.add_handler(CommandHandler("winners", restricted(winners)))
    app.add_handler(CommandHandler("scoreidea", restricted(scoreidea)))
    app.add_handler(CommandHandler("scoretitle", restricted(scoretitle)))
    app.add_handler(CommandHandler("smart_monitor_now", restricted(smart_monitor_now)))
    app.add_handler(CommandHandler("market_now", restricted(market_now)))
    app.add_handler(CommandHandler("riskcheck", restricted(riskcheck)))
    app.add_handler(CommandHandler("watchlist_status", restricted(watchlist_status)))
    app.add_handler(CommandHandler("search_demand", restricted(search_demand)))
    app.add_handler(CommandHandler("weekly_plan", restricted(weekly_plan)))
    app.add_handler(CommandHandler("memory_status", restricted(memory_status)))
    app.add_handler(CommandHandler("remember_perf", restricted(remember_perf)))
    app.add_handler(CommandHandler("performance", restricted(performance)))
    app.add_handler(CommandHandler("import_perf", restricted(import_perf)))
    app.add_handler(CommandHandler("editor10", restricted(editor10)))
    app.add_handler(CommandHandler("strategy10", restricted(strategy10)))
    app.add_handler(CommandHandler("cheap", restricted(cheap)))
    app.add_handler(CallbackQueryHandler(restricted(draft_callback), pattern=r"^draft:"))

    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, restricted(handle_message)))

    print("HiFi Trade AI Growth Team Started")
    app.run_polling()

if __name__ == "__main__":
    main()
