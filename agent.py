import os
import json
import csv
from io import StringIO
import asyncio
import requests
import feedparser
import concurrent.futures
import logging
import hashlib
import tempfile
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from openai import OpenAI
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, ContextTypes, filters

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
LOG_FILE = "agent_runtime.log"

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



SOURCE_TIER_RULES = """
Рейтинг источников:
Tier 1 — данные и первоисточники: Farside, Coinglass, Glassnode, The Block, SEC, ETF/биржевые данные, FRED, TradingView charts.
Tier 2 — сильные медиа/аналитика: CoinDesk, Blockworks, Cointelegraph, Decrypt, CryptoSlate, Investing, CNBC, Yahoo Finance, ForkLog, РБК Крипто.
Tier 3 — вторичные/слабые источники: мелкие сайты, пересказы, непроверенные новости, эмоциональные посты.

Для серьёзного ролика нужен минимум один Tier 1 или Tier 2 источник.
Если источник слабый, агент обязан пометить это и не строить на нём главный тезис.
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
                await update.message.reply_text("Доступ закрыт.")
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
                    "score": score
                })
        except Exception:
            continue

    # Deduplicate similar headlines and keep strongest
    deduped = []
    seen_keys = set()

    for item in sorted(news, key=lambda x: x.get("score", 0), reverse=True):
        key = clean_text_for_dedupe(item.get("title", ""))
        if not key:
            continue

        key_short = " ".join(key.split()[:8])
        if key_short in seen_keys:
            continue

        seen_keys.add(key_short)
        deduped.append(item)

    # Return strong candidates only. OpenAI will select final 5-7.
    strong = [x for x in deduped if x.get("score", 0) >= 7]

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

    await update.message.reply_text("\n".join(lines))



async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "HiFi Trade AI Growth Team запущен ✅\n\n"
        "Сначала отправь:\n"
        "/setchat\n\n"
        "Команды:\n"
        "/morning_now — короткий новостной отчёт\n"
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
    await update.message.reply_text("Чат сохранён ✅ Теперь я смогу присылать отчёты автоматически.")

async def morning_now(update: Update, context: ContextTypes.DEFAULT_TYPE):
    fear_greed = get_fear_greed_index()
    news = get_rss_news()
    ru_youtube = youtube_search("криптовалюта биткоин рынок сегодня", max_results=8)
    west_youtube = youtube_search("bitcoin crypto market today macro", max_results=8)

    prompt = f"""
Сделай утренний новостной отчёт HiFi Trade.
Используй только самые важные новости из списка. Не пересказывай всё подряд.

Fear & Greed:
{fear_greed}

Данные новостей:
{json.dumps(news, ensure_ascii=False, indent=2)}

RU/CIS YouTube:
{json.dumps(ru_youtube, ensure_ascii=False, indent=2)}

WEST YouTube:
{json.dumps(west_youtube, ensure_ascii=False, indent=2)}

Нужно:
1. 5 самых популярных и обсуждаемых новостей из крипты и инвестиций.
2. Для каждой новости строго 3-4 строки:
   - Заголовок
   - Источник
   - Суть: 1 предложение
   - Почему важно: 1 предложение
   - Влияние: BTC / ETH / альты / фондовый рынок / макро / регуляции + High/Medium/Low
3. В конце 1 короткий общий вывод.

Не добавляй идеи для роликов.
Не добавляй идеи для Shorts.
Не добавляй настроение аудитории.
Не добавляй раздел про мусорные темы.
Не придумывай торговые рекомендации.
"""
    answer = ask_ai(prompt)
    remember_report("morning_news", answer)
    await reply_long(update, answer)

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
        await update.message.reply_text("Не смог получить channelId. Проверь reference_video в memory.json.")
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
8. 3 улучшенных превью с оценкой каждого /10
9. Лучший вариант превью
10. Итоговая оценка ролика /10
11. 5 follow-up роликов с оценкой потенциала /10
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

async def scoreidea(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = " ".join(context.args).strip()
    if not query:
        await update.message.reply_text("Используй: /scoreidea идея_ролика")
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
        await update.message.reply_text("Используй: /scoretitle название_ролика")
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
        await update.message.reply_text(
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
        await update.message.reply_text(f"Не смог разобрать таблицу: {e}")
        return

    if not saved:
        await update.message.reply_text("Не нашёл ролики для импорта. Проверь заголовки колонок: title,views,ctr,retention,subscribers,notes")
        return

    await update.message.reply_text(f"Импортировал метрики роликов: {len(saved)} шт.\nТеперь можно вызвать /performance")


async def editor10(update: Update, context: ContextTypes.DEFAULT_TYPE):
    idea = " ".join(context.args).strip()

    if not idea:
        await update.message.reply_text(
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
        await update.message.reply_text(
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
        await update.message.reply_text("Не вижу title=. Пример: /remember_perf title=BTC не падает views=1500 ctr=6.2 retention=38 subscribers=12")
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

    await update.message.reply_text(
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
        await update.message.reply_text("Пока нет сохранённых результатов роликов.")
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
        await update.message.reply_text("Напиши так: /cheap вопрос")
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
        await update.message.reply_text("Используй: /riskcheck название_токена_или_тема_или_текст")
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
        await update.message.reply_text("Сильных новых инфоповодов пока не найдено.")
        return

    alert = build_smart_monitor_alert(strong_events)

    if not alert or "NO_ALERT" in alert.strip()[:30]:
        await update.message.reply_text("События есть, но они пока недостаточно сильные для alert.")
        for e in strong_events:
            save_seen_event_key(e.get("key", ""))
        return

    remember_report("manual_smart_monitor", alert)
    await reply_long(update, alert)



async def search_demand(update: Update, context: ContextTypes.DEFAULT_TYPE):
    answer = build_search_demand_report()
    await reply_long(update, answer)


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

                if current_time == daily_time:
                    key = f"{date_key}-daily"
                    if key not in sent_keys:
                        fear_greed = get_fear_greed_index()
                        news = get_rss_news()
                        ru = youtube_search("криптовалюта биткоин рынок сегодня", max_results=6)
                        west = youtube_search("bitcoin crypto market today macro", max_results=6)

                        prompt = f"""
Автоматический утренний новостной отчёт HiFi Trade.
Используй только самые важные новости из списка. Не пересказывай всё подряд.

Новости:
{json.dumps(news, ensure_ascii=False, indent=2)}

RU/CIS YouTube:
{json.dumps(ru, ensure_ascii=False, indent=2)}

WEST:
{json.dumps(west, ensure_ascii=False, indent=2)}

Дай:
1. 5 самых популярных и обсуждаемых новостей из крипты и инвестиций.
2. Для каждой новости строго 3-4 строки:
   - Заголовок
   - Источник
   - Суть: 1 предложение
   - Почему важно: 1 предложение
   - Влияние: BTC / ETH / альты / фондовый рынок / макро / регуляции + High/Medium/Low
3. В конце 1 короткий общий вывод.

Не добавляй идеи для роликов.
Не добавляй идеи для Shorts.
Не добавляй настроение аудитории.
Не добавляй раздел про мусорные темы.
Не придумывай торговые рекомендации.
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
    app.add_handler(CommandHandler("setchat", restricted(setchat)))
    app.add_handler(CommandHandler("morning_now", restricted(morning_now)))
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

    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, restricted(handle_message)))

    print("HiFi Trade AI Growth Team Started")
    app.run_polling()

if __name__ == "__main__":
    main()
