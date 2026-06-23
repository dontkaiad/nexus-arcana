"""arcana/handlers/sessions.py"""
from __future__ import annotations

import base64
import html
import json
import logging
import traceback as tb
from datetime import datetime, timezone, timedelta
from typing import List, Optional

from aiogram import F, Router
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from core.claude_client import ask_claude, ask_claude_vision
from core.config import config as _cfg
from core.error_log import log_error
from core.shared_handlers import get_user_tz
from core.tg_send import send_long, split_text
from arcana.repos.sessions_repo import (
    SessionsRepo, TripletEntry, PrevSessionSnippet, SessionSearchResult,
)
from arcana.repos.clients_repo import ClientsRepo, CLIENT_TYPE_PAID, CLIENT_TYPE_FREE

logger = logging.getLogger("arcana.sessions")

router = Router()
_repo = SessionsRepo()
_client_repo = ClientsRepo()

# ────────────────────────── Справочники ────────────────────────────────────

SPREAD_MAP = {
    "триплет":                     "🔺 Триплет",
    "3 карты":                     "🔺 Триплет",
    "три карты":                   "🔺 Триплет",
    "сфера":                       "🌐 Сфера жизни",
    "сфера жизни":                 "🌐 Сфера жизни",
    "кельтский":                   "✝️ Кельтский крест",
    "кельтский крест":             "✝️ Кельтский крест",
    "celtic cross":                "✝️ Кельтский крест",
    "воздействия":                 "⚡ Магические воздействия",
    "магические воздействия":      "⚡ Магические воздействия",
    "диагностика перед ритуалом":  "🔍 Диагностика перед ритуалом",
    "диагностика":                 "🔍 Диагностика перед ритуалом",
    "способности":                 "✨ Диагностика способностей",
    "диагностика способностей":    "✨ Диагностика способностей",
    "родовой":                     "🌳 Родовой узел",
    "родовой узел":                "🌳 Родовой узел",
}

PAYMENT_SOURCE_MAP = {
    "карта":     "💳 Карта",
    "наличные":  "💵 Наличные",
    "бартер":    "🔄 Бартер",
}

DECK_MAP = {
    "уэйт":         "Уэйт",
    "dark wood":    "Dark Wood",
    "дарк вуд":     "Dark Wood",
    "дарквуд":      "Dark Wood",
    "ленорман":     "Ленорман",
    "игральн":      "Игральные",
    "deviant moon": "Deviant Moon",
    "девиант мун":  "Deviant Moon",
}

AREA_VALUES = {"Отношения", "Финансы", "Работа", "Здоровье", "Род", "Общая ситуация"}
AREA_DEFAULT = "Общая ситуация"


def _match_spread(text: str) -> str:
    if not text:
        return ""
    low = text.strip().lower()
    if low in SPREAD_MAP:
        return SPREAD_MAP[low]
    for key, value in SPREAD_MAP.items():
        if key in low or low in key:
            return value
    return text.strip()


def _match_deck(text: str) -> str:
    """Нормализует название колоды: 'Уэйта'/'уэйту' → 'Уэйт' и т.п."""
    if not text:
        return ""
    low = text.strip().lower()
    for key, value in DECK_MAP.items():
        if low.startswith(key) or key in low:
            return value
    return text.strip()


def _normalize_area(text: str) -> str:
    """Нормализует область к одному из AREA_VALUES. Дефолт — 'Общая ситуация'."""
    if not text:
        return AREA_DEFAULT
    low = text.strip().lower()
    for value in AREA_VALUES:
        if low == value.lower() or value.lower() in low or low in value.lower():
            return value
    return AREA_DEFAULT


def _now_iso(tz: timezone) -> str:
    return datetime.now(tz).isoformat()


# ────────────────────────── Промпты ────────────────────────────────────────

PARSE_SESSION_SYSTEM = (
    "Извлеки данные о сеансе таро. Ответь ТОЛЬКО JSON без markdown.\n\n"
    "Распознавай ТРИ формата ввода:\n\n"
    "═══ ФОРМАТ C — ОДИНОЧНЫЙ ТРИПЛЕТ (single) ═══\n"
    "Одна строка/абзац, один вопрос:\n"
    "  «устроюсь ли на работу — жрица суд шут дно король кубков»\n"
    "Возврат:\n"
    '{"client_name": "имя или null", "spread_type": "тип расклада", '
    '"question": "конкретный вопрос", "cards": ["карта1","карта2","карта3"] или null, '
    '"bottom_card": "карта или null", '
    '"area": "Отношения|Финансы|Работа|Здоровье|Род|Общая ситуация", '
    '"deck": "Уэйт|Dark Wood|Ленорман|Игральные|Deviant Moon или null", '
    '"amount": число, "paid": число, '
    '"payment_source": "карта|наличные|бартер или null", '
    '"interpretation": "трактовка пользователя ДОСЛОВНО ИЛИ null"}\n\n'
    "═══ ФОРМАТ A — нумерованная сессия ═══\n"
    "  {Имя}:\n"
    "  1) что думает — шут маг жрица дно король кубков\n"
    "  2) что чувствует — туз кубков двойка кубков влюблённые дно императрица\n\n"
    "═══ ФОРМАТ B — свободный, блоками ═══\n"
    "Первая строка — короткое название темы/имя (1-3 слова, без карт). "
    "Далее чередуются строки 'вопрос' / 'карты'. Пример:\n"
    "  {имя}\n"
    "  что ко мне чувствует\n"
    "  король кубков туз мечей 9 пентаклей дно 2 мечей\n"
    "  что думает обо мне\n"
    "  7 мечей 8 жезлов колесо фортуны дно 2 жезлов\n\n"
    "Эвристика B: если первая строка короткая без карт, далее чётное число строк, "
    "и каждая «нечётная» строка явно похожа на вопрос (без множества карт), "
    "а «чётная» содержит ≥3 карт или слово «дно» — это формат B.\n\n"
    "Возврат для форматов A и B (одинаковый):\n"
    '{"session_name": "название группы раскладов (см. правила ниже)", '
    '"session_category": "Сфера жизни|Отношения|Работа|Финансы|Здоровье|Род|'
    'Магические воздействия|Диагностика|Кельтский крест или null", '
    '"client_name": "имя или null", '
    '"deck": "Уэйт|...", "amount": число, "paid": число, "payment_source": "...", '
    '"triplets": [{"question": "вопрос1", "cards": ["карта1","карта2","карта3"], '
    '"bottom_card": "карта или null", "area": "Отношения|Финансы|Работа|Здоровье|'
    'Род|Общая ситуация", "spread_type": "Триплет", '
    '"interpretation": "трактовка ЭТОГО триплета ДОСЛОВНО ИЛИ null"}, ...]}\n\n'
    "═══ ПОЛЕ interpretation (КРИТИЧНО — авторская трактовка для CRM) ═══\n"
    "Различай ДВА случая и НЕ путай:\n"
    "1) ТОЛЬКО КАРТЫ — пользователь назвал имена карт + вопрос, БЕЗ пояснения "
    "что карты значат. → interpretation = null.\n"
    "   Примеры (interpretation=null):\n"
    "   - «{имя}, что чувствует — король кубков туз мечей дно двойка»\n"
    "   - «устроюсь ли на работу — жрица суд шут дно король кубков»\n"
    "   - «{имя}, что думает — шут маг жрица»\n"
    "2) КАРТЫ + ТРАКТОВКА — есть НАРРАТИВ поверх имён карт: что карта значит, "
    "к чему ведёт, совет, вывод. → interpretation = этот текст ДОСЛОВНО (слова "
    "пользователя; НЕ сочиняй, НЕ переписывай своими словами, только перенеси речь).\n"
    "   Примеры (interpretation=<текст>):\n"
    "   - «король кубков тёплый но закрыт, туз мечей режет иллюзии, дно двойка — "
    "выбор не сделан» → interpretation: «король кубков тёплый но закрыт, туз мечей "
    "режет иллюзии, дно двойка — выбор не сделан»\n"
    "   - «жрица говорит жди, не лезь сейчас, суд — ответ придёт позже» → "
    "interpretation: «жрица говорит жди, не лезь сейчас, суд — ответ придёт позже»\n"
    "СТРОГО: НЕ клади в interpretation кусок вопроса, имя клиента, оплату или "
    "болтовню/мету — ТОЛЬКО смысловую трактовку карт. Сомневаешься, трактовка это "
    "или просто вопрос — ставь null. В мульти interpretation своя у КАЖДОГО триплета.\n\n"
    "═══ ОПРЕДЕЛЕНИЕ client_name (ЧЕЙ это расклад) ═══\n"
    "client_name = имя ЗАКАЗЧИКА расклада (для кого/по чьему заказу), а НЕ субъект "
    "вопроса.\n"
    "1) Пользователь про СЕБЯ — «клиент я», «себе», «на себя», «для себя», "
    "«личный», «мне», «себе расклад», «мой расклад» → это ЛИЧНЫЙ расклад → "
    "client_name = null (НЕ имя; код сам привяжет к self-клиенту).\n"
    "2) Имя в ВОПРОСЕ — это субъект вопроса, НЕ клиент. «что чувствует {Имя}», "
    "«будем ли вместе с {Имя}» — расклад ПРО отношения с {Имя}, но клиент — тот, "
    "кто заказал. Если заказчик не назван ИЛИ сказано «я/себе» → client_name = "
    "null, ДАЖЕ ЕСЛИ в вопросах фигурируют имена.\n"
    "3) client_name заполняй ТОЛЬКО если явно назван заказчик/тот, для кого "
    "расклад: «клиентка {Имя}», «расклад для {Имя}», «{Имя} заказала».\n"
    "4) Имена в примерах ниже ({Имя}, {имя}) — ПЛЕЙСХОЛДЕРЫ формата. НЕ подставляй "
    "их в вывод, когда клиент реально не назван.\n\n"
    "ОПРЕДЕЛЕНИЕ session_name — 1-5 слов, отражает СУТЬ группы:\n"
    "- если в тексте есть и имя клиента, и тема/практика → "
    "'{Имя клиента} — {Тема}' "
    "(пр.: 'клиентка {имя} расклад после приворота' → '{Имя} — приворот'; "
    "'{имя} диагностика порчи' → '{Имя} — диагностика')\n"
    "- если только тема без имени → '{Тема}' "
    "(пр.: 'расклад на работу' → 'Работа'; 'отворот' → 'Отворот')\n"
    "- если только имя без явной темы → '{Имя}' "
    "(пр.: '{Имя}' → '{Имя}')\n"
    "Темы/практики (триггеры): приворот, отворот, привязка, печать, "
    "очищение, чистка, снятие, диагностика, родовая работа, защита.\n\n"
    "ОПРЕДЕЛЕНИЕ session_category — по НАЗВАНИЮ темы и контексту, не по числу пунктов.\n"
    "СНАЧАЛА проверяй маркеры магии и диагностики (они приоритетнее):\n"
    "- 'приворот' / 'отворот' / 'привязка' / 'печать' / 'как лег ритуал' / "
    "'после ритуала' / 'после работы' (в смысле магической работы) "
    "→ 'Магические воздействия'\n"
    "- 'диагностика' / 'что на ней лежит' / 'есть ли порча' / 'сглаз' "
    "→ 'Диагностика'\n"
    "- 'род' / 'родовое' / 'родовая работа' → 'Род'\n"
    "- 'кельтский крест' / '10 карт' → 'Кельтский крест'\n"
    "ИНАЧЕ по теме:\n"
    "- 'Работа' / 'Карьера' / 'Финансы' / 'Деньги' / 'Здоровье' "
    "→ 'Сфера жизни'\n"
    "- имя человека ({Имя}) без магических маркеров → 'Сфера жизни'\n"
    "- если непонятно — null (код подставит дефолт).\n\n"
    "ОБЯЗАТЕЛЬНО: question — конкретный вопрос клиента, короткий (3-7 слов), "
    "с именами если есть. Примеры:\n"
    "- 'что думает {Имя} обо мне' → 'Что думает {Имя}'\n"
    "- 'будем ли вместе с {Имя}' → 'Будут ли отношения с {Имя}'\n"
    "- 'триплет уэйт отношения' (без вопроса) → 'Отношения — общий расклад'\n"
    "- 'что на работе ждёт' → 'Перспективы на работе'\n"
    "- 'расклад по здоровью мамы' → 'Здоровье мамы'\n\n"
    "КРИТИЧНО: area КАЖДОГО триплета определяется ПО ЕГО конкретному вопросу, "
    "НЕ по общей теме сессии. Не ставь одинаковую area всем триплетам — "
    "анализируй каждый вопрос отдельно:\n"
    "- 'общее состояние', 'общий обзор', 'как лег ритуал', 'что вообще' "
    "→ 'Общая ситуация'\n"
    "- 'что чувствует', 'любит ли', 'отношения', 'совместность', "
    "'официальный статус', 'сойдётся ли', 'тревожит в отношениях', "
    "'планы по отношению к', 'страхи перед статусом', 'кто для него' "
    "→ 'Отношения'\n"
    "- 'секс', 'сексуальная жизнь', 'тело', 'здоровье', 'болезнь', "
    "'самочувствие' → 'Здоровье'\n"
    "- 'работа', 'карьера', 'устроится ли', 'увольнение', 'коллеги' "
    "→ 'Работа'\n"
    "- 'деньги', 'финансы', 'доход', 'долги', 'покупка' → 'Финансы'\n"
    "- 'род', 'предки', 'родовое' → 'Род'\n"
    "Если совсем непонятно — 'Общая ситуация'.\n"
    "Карты в cards — массив строк (можно с числовыми именами '9 пентаклей', "
    "'2 мечей' — код их сам нормализует).\n"
    "ЗАПРЕЩЕНО ВЫДУМЫВАТЬ КАРТУ. Заноси в cards только то, что ОДНОЗНАЧНО узнал "
    "как реальную карту таро. Если слово неразборчиво/искажено (мисхёрд голоса) "
    "или не похоже однозначно на реальную карту — ты ОБЯЗАН вернуть его ДОСЛОВНО "
    "как в тексте (или null). НЕ подменяй знакомой картой, НЕ угадывай ранг и "
    "масть, НЕ «исправляй» к ближайшей. Тихая подмена чужой картой = испорченная "
    "трактовка; дословный мусор в карточке (код залогирует card_not_in_ref) "
    "пользователь увидит и поправит сам.\n"
    "  Примеры:\n"
    "  - «...король кубков, крыльева мячей, шут...» → cards: "
    "[\"король кубков\", \"крыльева мячей\", \"шут\"] "
    "(«крыльева мячей» искажено — перенеси ДОСЛОВНЫЙ фрагмент как в тексте)\n"
    "  - «...королева мечей, шут, маг...» → cards: "
    "[\"королева мечей\", \"шут\", \"маг\"] (узнаваемо — как есть)\n"
    "Колода (deck): канонические названия БЕЗ склонений — "
    "'Уэйт' (не 'Уэйта'/'Уэйту'), 'Dark Wood', 'Ленорман', 'Игральные', 'Deviant Moon'.\n"
    "Если в тексте есть упоминание 'дно', 'дно колоды', 'bottom' — "
    "выдели эту карту отдельно в поле bottom_card. Это НЕ позиция расклада, "
    "а фоновая карта, её нельзя включать в cards."
)


class SessionParseError(Exception):
    """Бросается когда ни один формат расклада не распознался."""


PARSE_HELP_TEXT = (
    "🔮 Не поняла формат расклада. Попробуй так:\n\n"
    "<b>Сессия (несколько вопросов):</b>\n"
    "Вадим:\n"
    "1) что думает — шут маг жрица дно король кубков\n"
    "2) что чувствует — туз кубков двойка кубков влюблённые дно императрица\n\n"
    "<b>Или одной строкой для одиночного триплета:</b>\n"
    "устроюсь ли на работу — жрица суд шут дно король кубков"
)

TRIPLET_SUMMARY_SYSTEM = (
    "Output as plain Russian text, 1-2 sentences (~140 знаков), no formatting, "
    "no markdown, no HTML tags, no emojis, no 'итог:' / 'вывод:'. "
    "Суть ответа на вопрос с учётом карт и дна — по делу, без воды."
)

CORRECTION_PARSE_SYSTEM = (
    "Пользователь правит данные сеанса таро. Извлеки ТОЛЬКО то, что явно меняется. "
    "Ответь ТОЛЬКО JSON без markdown:\n"
    '{"client_name": "новое имя или null", '
    '"question": "новый вопрос или null", '
    '"area": "Отношения|Финансы|Работа|Здоровье|Род|Общая ситуация или null"}\n'
    "Если поле не упоминается в правке — ставь null. Только изменения."
)

CARD_EDIT_PARSE_SYSTEM = (
    "Пользователь правит уже сохранённый триплет таро. Определи, меняет ли правка "
    "КАРТУ расклада (а не только текст трактовки). Ответь ТОЛЬКО JSON без markdown:\n"
    '{"card_edit": true ИЛИ false, '
    '"cards": ["карта1","карта2","карта3"] ИЛИ null, '
    '"bottom_card": "карта ИЛИ null"}\n'
    "card_edit=true ТОЛЬКО при явной замене карты: «X а не Y», «первая карта Z», "
    "«там не король а королева», «дно не маг а жрица». Тогда cards — ПОЛНЫЙ новый "
    "список карт триплета (обычно 3) RU-именами с учётом замены; остальные карты "
    "оставь как в текущем наборе. bottom_card — новое дно если менялось, иначе null.\n"
    "card_edit=false если правка про ТЕКСТ трактовки (тон, «добавь про деньги», "
    "«перепиши мягче», «учти прошлый расклад») — карты НЕ трогаются, cards=null."
)

SESSION_SEARCH_PARSE_SYSTEM = (
    "Пользователь ищет свои прошлые расклады. Извлеки ключевые слова для поиска "
    "в теме расклада. Ответь ТОЛЬКО JSON без markdown:\n"
    '{"keywords": ["слово1", "слово2"]}\n\n'
    "Примеры:\n"
    "- 'что падало на Вадима' → {\"keywords\": [\"Вадим\"]}\n"
    "- 'расклады про работу' → {\"keywords\": [\"работа\"]}\n"
    "- 'расклады на Машу про отношения' → {\"keywords\": [\"Маша\", \"отношения\"]}\n"
    "- 'покажи расклад про здоровье мамы' → {\"keywords\": [\"здоровье\", \"мамы\"]}\n"
    "Имена — в именительном падеже если возможно. Максимум 3 ключевых слова."
)

TAROT_SYSTEM = (
    "Ты — ассистент-таролог. Трактуй строго по справочнику колоды.\n\n"
    "Правила:\n"
    "1. Значения карт ЗАВИСЯТ ОТ КОЛОДЫ (см. «Колода: …» в справочнике):\n"
    "   • Уэйт (классическая) — ты знаешь канон. Раскрывай карту по её РЕАЛЬНОМУ "
    "классическому значению; справочник используй если есть. Карты НЕТ в "
    "справочнике — это НЕ мешает: раскрой классическое значение Уэйта.\n"
    "   • НЕ Уэйт (авторская: Dark Wood, Deviant Moon, Ленорман, игральные и др.) "
    "— значения СТРОГО из справочника. НЕ придумывай своё, НЕ подставляй "
    "классические значения (у этих колод свои нестандартные смыслы). Нет карты в "
    "справочнике — не выдумывай её значение.\n"
    "2. Каждая карта: Позиция → Название → значение В ЭТОЙ ПОЗИЦИИ применительно к вопросу (1-2 предложения).\n"
    "3. Если есть предыдущие расклады клиента — свяжи с ними: что изменилось, что подтвердилось, куда движется ситуация.\n"
    "4. Краткий вывод: 2-3 предложения, практическая суть. Учитывай дно колоды при формулировке общего вывода.\n"
    "5. БЕЗ поэзии, метафор, воды. Факты и структура.\n"
    "6. Привязывай значения к вопросу клиента.\n\n"
    "ВЫВОДИ ТОЛЬКО HTML с тегами <h3>, <b>, <i>, <p>. Никакого markdown "
    "(никаких **, __, ##, *, _ — никогда). Никаких других тегов "
    "(<div>, <span>, <strong>, <em>, классы, инлайн-стили — запрещены).\n\n"
    "Структура трактовки для триплета (3 карты + опциональное дно):\n"
    "<h3>Общий смысл</h3>\n"
    "<p>2-3 предложения о том, что показывает расклад в целом</p>\n"
    "<h3>🃏 [Карта 1]</h3>\n"
    "<p>Что говорит первая карта о ситуации/вопросе</p>\n"
    "<h3>🃏 [Карта 2]</h3>\n"
    "<p>Что говорит вторая карта</p>\n"
    "<h3>🃏 [Карта 3]</h3>\n"
    "<p>Что говорит третья карта</p>\n"
    "<h3>🂠 [Дно колоды]</h3>\n"
    "<p>Как дно влияет на весь расклад — это фон, скрытый фактор</p>\n\n"
    "ВАЖНО: НЕ приписывай картам «Прошлое/Настоящее/Будущее» или другие "
    "временные/позиционные значения. Триплет — это раскрытие сути ситуации "
    "тремя ракурсами, не таймлайн. Каждая карта говорит о своей грани вопроса.\n"
    "ВАЖНО: эмодзи 🃏 для всех трёх карт триплета (можешь варьировать масти, "
    "если карта явной масти: ⚔️ Мечи, 🪙 Пентакли, 🍷 Кубки, 🪄 Жезлы, иначе 🃏). "
    "🂠 строго для дна колоды.\n\n"
    "Имена карт и ключевые сущности выделяй <b>...</b>. Цитаты/акценты — <i>...</i>. "
    "Не используй <br>, разделяй блоки тегами <h3>/<p>."
)

# Режим A: Кай НАДИКТОВАЛА свою трактовку ТЕЗИСНО — её надо РАЗВЕРНУТЬ в полную
# трактовку, опираясь на РЕАЛЬНЫЕ значения карт из справочника (против выдумки),
# не сочиняя постороннего. Sonnet (не Haiku): связность + голос автора + удержать
# грань «разворачиваю, но не галлюцинирую» — narrative-задача с эмпатией, Haiku
# пересушит/переврёт (CLAUDE.md разрешает Sonnet для трактовок sessions.py).
# Регрессия — test_models_audit. Справочник колоды дописывается к этому system
# вызывающим кодом (--- СПРАВОЧНИК КАРТ ---), как в режиме B.
PERSONAL_INTERP_SYSTEM = (
    "Кай — таролог — НАДИКТОВАЛА свою трактовку расклада голосом, ТЕЗИСНО: по "
    "каждой карте короткий акцент («туз — шанс», «влюблённые — выбор»). Твоя "
    "задача — РАЗВЕРНУТЬ её тезисы в полную связную трактовку. Это ЕЁ трактовка, "
    "ты её РАСКРЫВАЕШЬ, а не сочиняешь свою.\n\n"
    "КАК разворачивать (ГЛАВНОЕ):\n"
    "1. Тезис Кай по карте = АКЦЕНТ: какую грань значения карты подсветить.\n"
    "2. Разверни акцент в связный абзац по РЕАЛЬНОМУ значению карты, в контексте "
    "вопроса и соседних карт. Источник значения ЗАВИСИТ ОТ КОЛОДЫ (см. «Колода: …» "
    "в «--- СПРАВОЧНИК КАРТ ---»):\n"
    "   • Уэйт (классическая) — ты знаешь канон: дорабатывай акцент Кай по "
    "классическому значению карты (справочник используй если есть).\n"
    "   • НЕ Уэйт (авторская: Dark Wood, Deviant Moon, Ленорман, игральные) — "
    "ТОЛЬКО значения из справочника, у этих колод свои нестандартные смыслы; НЕ "
    "подставляй классику.\n"
    "3. ЗАПРЕТ ГАЛЛЮЦИНАЦИЙ: НЕ вводи смысл, которого нет НИ в значении карты "
    "(справочник; для Уэйта — её классическое значение), НИ в акценте Кай. Акцент "
    "Кай — что именно из значения подсветить. Третьего не придумывай: ни денег, "
    "ни отношений, ни мистики, если их нет ни в карте, ни в тезисе.\n"
    "4. Если акцент Кай и справочник РАСХОДЯТСЯ — приоритет у АКЦЕНТА Кай (это её "
    "трактовка), но разворачивай через значение карты, не вводя посторонний смысл.\n"
    "5. Карты НЕТ в справочнике: для Уэйта — раскрой по КЛАССИЧЕСКОМУ значению (ты "
    "его знаешь, это НЕ мешает); для авторской колоды — разворачивай ТОЛЬКО по "
    "акценту Кай, не доизобретая (классику НЕ подставляй).\n"
    "6. Тезисы по картам → каждую карту своим заголовком и абзацем. Если Кай "
    "говорила сплошным потоком (холистически) — сохрани поток абзацами, не дроби "
    "насильно. В ОБОИХ случаях в конце — блок «Общий вывод»: синтез триплета (как "
    "карты складываются в ответ на вопрос) по значениям карт И логике Кай.\n"
    "7. ТОЧКА ЗРЕНИЯ автора неприкосновенна. Если Кай говорит от ПЕРВОГО лица "
    "(«я вижу», «я читаю», «для меня», «мне кажется») — оставляй ПЕРВОЕ ЛИЦО. "
    "НЕ заменяй «я» на имя «Кай», НЕ переписывай в третьем лице («Кай видит», "
    "«И Кай читает»). Трактовка звучит ЕЁ голосом ОТ ПЕРВОГО ЛИЦА, а не пересказом "
    "про неё.\n\n"
    "ТОН: голос Кай — эмпатичный, прямой, по делу. НЕ академический пересказ "
    "справочника, не вода, не поэзия. Сохрани её интонацию.\n\n"
    "ВЫВОДИ ТОЛЬКО HTML с тегами <h3>, <b>, <i>, <p>. Никакого markdown "
    "(никаких **, __, ##, *, _ — никогда). Других тегов нет "
    "(<div>, <span>, <strong>, <em> — запрещены).\n"
    "Структура: <h3>🃏 [Карта]</h3><p>развёрнутый абзац</p> на каждую карту "
    "(🂠 — строго для дна колоды), затем <h3>Общий вывод</h3><p>...</p>. "
    "Имена карт и сущности — <b>...</b>, акценты — <i>...</i>. Без <br>.\n\n"
    "ПРИМЕР ГРАНИ (тезис + значение справочника → развёрнутый абзац по значению):\n"
    "Вопрос: выйду ли на новую работу. Тезис Кай: «туз мечей — прорыв». "
    "Справочник: Туз Мечей — ясность, решение, прорыв через интеллект, "
    "разрубание узла.\n"
    "→ <h3>⚔️ Туз Мечей</h3><p><b>Туз Мечей</b> — это про <i>прорыв</i>, как ты и "
    "говоришь: момент ясности, когда узел разрубается одним решением. В контексте "
    "работы — не вязнешь в сомнениях, а берёшь и решаешь; путь открывается через "
    "чёткое «да».</p>\n"
    "(Развёрнуто по значению карты + акцент Кай «прорыв». НЕ добавлено постороннее "
    "— ни денег, ни отношений, ни мистики, которых нет ни в карте, ни в тезисе.)\n\n"
    "ПРИМЕР ТОЧКИ ЗРЕНИЯ (сохранить ПЕРВОЕ лицо автора):\n"
    "Вход: «я читаю это как — он сам отказался».\n"
    "→ «<i>Я читаю это как</i>: он сам отказался…» — ПЕРВОЕ лицо сохранено. "
    "НЕ «Кай читает это как…», НЕ «И Кай видит здесь…»."
)

VISION_SYSTEM = (
    "Ты анализируешь фото расклада карт таро. "
    "Определи все карты, тип расклада и колоду. Ответь ТОЛЬКО JSON без markdown:\n"
    '{"spread_type": "тип или Другой", "deck": "Уэйт|Dark Wood|Ленорман|Игральные|Deviant Moon", '
    '"cards": [{"position": "позиция", "card": "название"}], '
    '"bottom_card": "название карты дна колоды или null"}\n\n'
    "Порядок карт на фото: СЛЕВА НАПРАВО, СВЕРХУ ВНИЗ. "
    "Верни карты в этом порядке.\n"
    "Если карт БОЛЬШЕ чем нужно для расклада (например 4 карты для триплета) — "
    "ПОСЛЕДНЯЯ карта = дно колоды, положи её в bottom_card, "
    "а в cards оставь только карты самого расклада.\n"
    "Если на фото дно лежит отдельно от расклада (ниже, сбоку, рядом с колодой) — "
    "тоже в bottom_card.\n"
    "Колода — каноническое название без склонений."
)


# ────────────────────────── Вспомогательные ────────────────────────────────

# Маппинг session_category из парсера → значение поля «Тип расклада» в Notion
SESSION_CATEGORY_MAP = {
    "сфера жизни":             "🌐 Сфера жизни",
    "отношения":               "🌐 Сфера жизни",
    "работа":                  "🌐 Сфера жизни",
    "финансы":                 "🌐 Сфера жизни",
    "здоровье":                "🌐 Сфера жизни",
    "род":                     "🌳 Родовой узел",
    "родовое":                 "🌳 Родовой узел",
    "магические воздействия":  "⚡ Магические воздействия",
    "диагностика":             "🔍 Диагностика перед ритуалом",
    "кельтский крест":         "✝️ Кельтский крест",
    "триплет":                 "🔺 Триплет",
}


def _resolve_session_category(
    name: Optional[str], items_count: int, *, all_triplets: bool = False,
) -> str:
    """Категория расклада по названию темы.

    Дефолт:
    - одиночный (items_count <= 1) → 🔺 Триплет
    - multi-session где КАЖДАЯ запись — триплет (3 карты + дно), но Haiku
      не дала явной категории → 🔺 Триплет (через all_triplets=True). См. #83
    - иначе multi-session без явной категории → 🌐 Сфера жизни
    """
    if name:
        low = name.strip().lower()
        if low in SESSION_CATEGORY_MAP:
            return SESSION_CATEGORY_MAP[low]
        for k, v in SESSION_CATEGORY_MAP.items():
            if k in low or low in k:
                return v
    if items_count <= 1 or all_triplets:
        return "🔺 Триплет"
    return "🌐 Сфера жизни"


def _coerce_cards_str(value) -> str:
    """Sonnet может вернуть cards как list или str — приводим к comma-string."""
    if value is None:
        return ""
    if isinstance(value, list):
        return ", ".join(str(x).strip() for x in value if str(x).strip())
    return str(value).strip()


def _canon_cards_str(cards_str: str, deck: str) -> str:
    """RU-ввод карт → каноническая EN-строка через реестр deck_cards.json.
    Невнятные имена остаются как есть."""
    if not cards_str:
        return ""
    try:
        from miniapp.backend.tarot import resolve_deck_id, find_card
        deck_id = resolve_deck_id(deck or "Уэйт")
        out: List[str] = []
        for raw in cards_str.split(","):
            raw = raw.strip()
            if not raw:
                continue
            c = find_card(deck_id, raw)
            out.append(c["en"] if c and c.get("en") else raw)
        return ", ".join(out)
    except Exception:
        return cards_str


def _canon_card(card: str, deck: str) -> str:
    if not card:
        return ""
    try:
        from miniapp.backend.tarot import resolve_deck_id, find_card
        c = find_card(resolve_deck_id(deck or "Уэйт"), card.strip())
        return (c and c.get("en")) or card.strip()
    except Exception:
        return card.strip()


def _cards_to_ru(cards_str: str, deck: str) -> str:
    """Канон-строка карт (в PG хранится EN) → RU-имена для показа в трактовке.

    Нужно для correction-флоу: иначе Sonnet берёт EN-имена из промпта и пишет
    заголовки «Eight of Swords» вместо «Восьмёрка Мечей» (#160)."""
    if not cards_str:
        return ""
    try:
        from miniapp.backend.tarot import resolve_deck_id, find_card
        deck_id = resolve_deck_id(deck or "Уэйт")
        out: List[str] = []
        for raw in cards_str.split(","):
            raw = raw.strip()
            if not raw:
                continue
            c = find_card(deck_id, raw)
            out.append((c and c.get("ru")) or raw)
        return ", ".join(out)
    except Exception:
        return cards_str


def _canon_card_ru(card: str, deck: str) -> str:
    if not card:
        return ""
    try:
        from miniapp.backend.tarot import resolve_deck_id, find_card
        c = find_card(resolve_deck_id(deck or "Уэйт"), card.strip())
        return (c and c.get("ru")) or card.strip()
    except Exception:
        return card.strip()


async def _upload_spread_photo(message: Message) -> str:
    """Если к сообщению расклада приложено фото — грузим его в Cloudinary
    (folder arcana-sessions) и возвращаем secure_url. Иначе ''.

    Это путь «текст + фото расклада»: карты введены текстом, фото идёт как
    приложение и раньше тихо терялось (в Cloudinary не уезжало) — #161."""
    if not getattr(message, "photo", None):
        return ""
    try:
        from core.cloudinary_client import cloudinary_upload
        file = await message.bot.get_file(message.photo[-1].file_id)
        bio = await message.bot.download_file(file.file_path)
        return await cloudinary_upload(
            bio.read(), filename="spread.jpg", folder="arcana-sessions",
        ) or ""
    except Exception as e:
        logger.warning("spread photo upload failed: %s", e)
        return ""


async def _make_triplet_summary(
    question: str, cards: str, bottom: str, interpretation: str
) -> str:
    """Haiku: 1-2 предложения по триплету. На пустом вводе → ''."""
    if not (cards or interpretation):
        return ""
    src = (
        f"Вопрос: {question}\n"
        f"Карты: {cards}\n"
        + (f"Дно: {bottom}\n" if bottom else "")
        + (f"Трактовка: {interpretation[:1500]}" if interpretation else "")
    )
    try:
        out = await ask_claude(
            src,
            system=TRIPLET_SUMMARY_SYSTEM,
            model="claude-haiku-4-5-20251001",
            max_tokens=160,
            temperature=0.5,
        )
        from core.html_sanitize import sanitize_summary
        return sanitize_summary(out or "")
    except Exception as e:
        logger.warning("triplet_summary failed: %s", e)
        return ""


async def _polish_authored_interpretation(
    authored: str, cards_text: str, bottom_card: str, question: str,
    cards_context: str = "",
) -> str:
    """Режим A: РАЗВЕРНУТЬ надиктованные Кай тезисы в полную трактовку, опираясь
    на РЕАЛЬНЫЕ значения карт из справочника (cards_context) — без галлюцинаций.
    Sonnet с PERSONAL_INTERP_SYSTEM. Возвращает HTML; на пустом authored → ''.

    cards_context (get_cards_context — те же значения, что кормят режим B) =
    опора против выдумки. Общий для single и multi флоу — чтобы режим A не
    разошёлся параллельными реализациями (CLAUDE.md: оба session-флоу — один
    паттерн).
    """
    if not authored:
        return ""
    system = PERSONAL_INTERP_SYSTEM
    if cards_context:
        system += f"\n\n--- СПРАВОЧНИК КАРТ ---\n{cards_context}"
    user_prompt = (
        f"Карты расклада: {cards_text}\n"
        + (f"Дно колоды: {bottom_card}\n" if bottom_card else "")
        + f"Вопрос: {question}\n\n"
        f"Тезисы Кай по картам (разверни по значениям карт, не сочиняй "
        f"постороннего):\n{authored}"
    )
    return await ask_claude(
        user_prompt,
        system=system,
        model=_cfg.model_sonnet,
        max_tokens=2000,
        temperature=0.5,
    )


def _parse_json_safe(raw: str) -> Optional[dict]:
    try:
        clean = raw.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
        return json.loads(clean)
    except Exception:
        return None


async def _parse_card_edit(
    correction_text: str, cards_ru: str, bottom_ru: str,
) -> Optional[dict]:
    """Haiku: меняет ли правка КАРТУ (vs только текст)? Общий для обоих
    correction-флоу (saved-триплет и preview), чтобы не было параллельных
    реализаций. Возвращает {"cards_ru": str, "bottom_ru": str} с НОВЫМ полным
    набором RU-карт, или None если правка только текстовая. Graceful → None."""
    try:
        raw = await ask_claude(
            f"Текущие карты: {cards_ru}\n"
            + (f"Текущее дно: {bottom_ru}\n" if bottom_ru else "")
            + f"Замечание: {correction_text}",
            system=CARD_EDIT_PARSE_SYSTEM,
            model="claude-haiku-4-5-20251001",
            max_tokens=200,
            temperature=0,
        )
        data = _parse_json_safe(raw) or {}
    except Exception as e:
        logger.warning("card edit parse failed: %s", e)
        return None
    if not data.get("card_edit"):
        return None
    new_cards = _coerce_cards_str(data.get("cards"))
    if not new_cards:
        return None
    new_bottom = (data.get("bottom_card") or "").strip()
    return {"cards_ru": new_cards, "bottom_ru": new_bottom or bottom_ru}


def _format_prev_sessions(snippets: List[PrevSessionSnippet]) -> str:
    """Форматирует предыдущие расклады клиента для вставки в промпт."""
    lines: List[str] = []
    for s in snippets[:5]:
        parts: List[str] = [f"📅 {s.date}: {s.question}" if s.question else f"📅 {s.date}"]
        if s.cards:
            parts.append(f"  Карты: {s.cards[:150]}")
        if s.interpretation_excerpt:
            parts.append(f"  Итог: {s.interpretation_excerpt}")
        lines.append("\n".join(parts))
    return "\n\n".join(lines)


_RAG_VOICE_HEADER = "--- ПОХОЖИЕ ТВОИ ПРОШЛЫЕ ТРАКТОВКИ (стиль/тон) ---"


async def _rag_voice_block(
    cards_text: str, question: str, exclude_id: Optional[str] = None
) -> str:
    """RAG-AB интеграция A (консистентность голоса): семантический поиск ПОХОЖИХ
    прошлых трактовок ПО ВСЕМ клиентам (не история одного) → блок для system.

    Возвращает готовый блок (с ведущими \\n\\n) или '' — чисто аддитивно.
    Текущий триплет (exclude_id) и пустые трактовки исключаются. Никогда не
    бросает — RAG вторичен, трактовка работает и без него."""
    q = f"{cards_text or ''} {question or ''}".strip()
    if not q:
        return ""
    try:
        import asyncio as _aio
        from core.rag import search_triplets
        hits = await _aio.to_thread(search_triplets, q, 3, None)
    except Exception as e:
        logger.warning("rag voice retrieve failed: %s", e)
        return ""
    lines: List[str] = []
    for h in (hits or []):
        if exclude_id and str(h.get("triplet_id")) == str(exclude_id):
            continue
        excerpt = (h.get("interp_excerpt") or "").strip()
        if not excerpt:
            continue
        cards = (h.get("cards") or "").strip()
        lines.append(f"• {cards}: {excerpt}" if cards else f"• {excerpt}")
    if not lines:
        return ""
    return (
        f"\n\n{_RAG_VOICE_HEADER}\n"
        "Это твои прежние трактовки похожих раскладов. Опирайся на свой "
        "устоявшийся голос, тон и манеру — но НЕ копируй формулировки дословно.\n"
        + "\n".join(lines)
    )


async def _rag_index_safe(
    page_id: Optional[str],
    *,
    cards: str,
    question: str,
    interpretation: str,
    client_id: Optional[str],
    session_name: Optional[str],
    occurred_at: Optional[str],
) -> None:
    """RAG-AB индексация триплета после сохранения. Провал НЕ роняет save —
    данные уже в PG, Qdrant вторичен."""
    if not page_id:
        return
    try:
        import asyncio as _aio
        from core.rag import index_triplet
        await _aio.to_thread(
            index_triplet, page_id, cards, question, interpretation,
            client_id, session_name, occurred_at,
        )
    except Exception as e:
        logger.warning("rag index failed for %s: %s", page_id, e)


async def _rag_index_batch_safe(items: List[dict]) -> None:
    """RAG-AB батч-индексация N триплетов сессии ОДНИМ запросом Voyage (бережём
    3 RPM). items — список dict для index_triplets_batch. Провал НЕ роняет
    сессию (данные уже в PG)."""
    if not items:
        return
    try:
        import asyncio as _aio
        from core.rag import index_triplets_batch
        await _aio.to_thread(index_triplets_batch, items)
    except Exception as e:
        logger.warning("rag batch index failed (%s items): %s", len(items), e)


async def _rag_delete_safe(triplet_id: Optional[str]) -> None:
    """RAG-AB: убрать вектор триплета из Qdrant (при удалении расклада). Провал
    НЕ роняет операцию — PG источник истины (#166)."""
    if not triplet_id:
        return
    try:
        import asyncio as _aio
        from core.rag import delete_triplet
        await _aio.to_thread(delete_triplet, triplet_id)
    except Exception as e:
        logger.warning("rag delete failed for %s: %s", triplet_id, e)


def _triplet_keyboard(page_id: str) -> InlineKeyboardMarkup:
    """Кнопки [✏️ Поправить] [🗑 Удалить] под сохранённым триплетом."""
    from core.utils import cancel_button, secondary_button
    short = page_id.replace("-", "")[:32]  # Telegram callback_data ≤ 64 bytes
    return InlineKeyboardMarkup(inline_keyboard=[[
        secondary_button("✏️ Поправить", f"triplet_correct:{short}"),
        cancel_button("🗑 Удалить", f"triplet_remove:{short}"),
    ]])


def _triplet_remove_confirm_keyboard(short_id: str) -> InlineKeyboardMarkup:
    from core.utils import secondary_button
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(
            text="✅ Да, удалить",
            callback_data=f"triplet_remove_yes:{short_id}",
        ),
        secondary_button("↩️ Отмена", f"triplet_remove_no:{short_id}"),
    ]])


async def _resolve_triplet_page(short_id: str, user_notion_id: str) -> Optional[TripletEntry]:
    """short_id (32 hex без дефисов) → TripletEntry. None если не найден."""
    return await _repo.find_by_short_id(short_id, user_notion_id)


async def _save_and_post_triplet(
    message: Message,
    *,
    tz: timezone,
    user_notion_id: str,
    client_id: Optional[str],
    client_name: Optional[str],
    deck: str,
    spread_type: str,
    question: str,
    cards_text: str,
    bottom_card: str,
    area: str,
    interpretation: str,
    authored: bool = False,
    session_name: Optional[str] = None,
    payment_source: Optional[str] = None,
    amount: float = 0,
    paid: float = 0,
    self_client_missing: bool = False,
) -> Optional[str]:
    """Унифицированный путь: канон → Sonnet-трактовка уже готова → Haiku-саммари
    → запись в Notion → пост в чат с кнопками [Поправить/Удалить] (+оплата
    для платного клиента). Возвращает page_id.

    authored=True (режим A — авторская трактовка из голоса): НЕ дописываем
    машинный блок дна, иначе он загрязнит текст Кай выдуманным описанием. Дно
    всё равно видно в шапке сообщения и хранится в своём поле bottom_card."""
    from core.html_sanitize import sanitize_interpretation
    from core.html_for_telegram import html_to_telegram
    from core.client_resolve import client_get_type, should_skip_payment
    from core.message_pages import save_message_page

    cards_en = _canon_cards_str(cards_text, deck or "Уэйт") or cards_text
    bottom_en = _canon_card(bottom_card, deck or "Уэйт") if bottom_card else ""
    if bottom_en and "🂠" not in interpretation and not authored:
        interpretation = (
            interpretation.rstrip()
            + f"\n\n<h3>🂠 {bottom_en} · фон</h3><p>Скрытый фон расклада.</p>"
        )

    triplet_summary = await _make_triplet_summary(
        question, cards_text, bottom_card or "", interpretation,
    )
    interpretation = sanitize_interpretation(interpretation)
    is_personal = not client_name

    page_id = await _repo.add(
        date=_now_iso(tz),
        spread_type=spread_type,
        title=question,
        question=question,
        cards=cards_en,
        interpretation=interpretation,
        amount=amount,
        paid=paid,
        session_type="Личный" if is_personal else "Клиентский",
        client_id=client_id,
        user_notion_id=user_notion_id,
        area=area,
        deck=deck,
        payment_source=payment_source,
        session=session_name,
        triplet_summary=triplet_summary or None,
        bottom_card=bottom_en or None,
    )
    if not page_id:
        await message.answer("⚠️ Не получилось сохранить расклад.")
        return None

    # RAG-AB: индексируем триплет в Qdrant (для будущих retrieve). Провал НЕ
    # роняет сохранение — данные уже в PG, Qdrant вторичен (#166).
    await _rag_index_safe(
        page_id, cards=cards_text, question=question, interpretation=interpretation,
        client_id=client_id, session_name=session_name, occurred_at=_now_iso(tz)[:10],
    )

    # Авто-привязка к открытой Работе (категория 🃏 Расклад) + закрыть её (PG, #151).
    work_closed = False
    if client_id:
        try:
            from core.work_relation import (
                set_event_work_id, close_work_as_done,
                find_active_work_for_client,
            )
            w_id = await find_active_work_for_client(
                client_id, "🃏 Расклад", user_notion_id,
            )
            if w_id:
                ok = await set_event_work_id("session", page_id, w_id)
                if ok:
                    await close_work_as_done(w_id)
                    work_closed = True
        except Exception as e:
            logger.warning("session→work relation failed: %s", e)

    # Сообщение в чат: вопрос + карты + дно + трактовка (telegram-safe).
    interp_tg = html_to_telegram(interpretation)
    cards_line = ", ".join(c.strip() for c in cards_text.split(",") if c.strip())
    head_lines = [f"<b>{html.escape(question or 'Расклад')}</b>"]
    head_lines.append(
        f"🃏 {html.escape(spread_type or 'Триплет')} · {html.escape(deck or 'Уэйт')}"
    )
    head_lines.append(
        f"👤 {html.escape(client_name)}" if client_name else "🔮 Личный"
    )
    if cards_line:
        head_lines.append(f"📍 {html.escape(cards_line)}")
    if bottom_card:
        head_lines.append(f"🂠 {html.escape(bottom_card)}")
    if is_personal and self_client_missing:
        head_lines.append(
            "\n💡 Заведи себя как клиента чтобы группировать личные расклады"
        )
    if work_closed:
        head_lines.append("✅ Связанная Работа закрыта")
    head = "\n".join(head_lines)

    body = f"{head}\n\n{interp_tg}"
    triplet_kb = _triplet_keyboard(page_id)

    # Тип клиента → решаем нужны ли кнопки оплаты.
    ctype = await client_get_type(client_id) if client_id else None
    show_payment = bool(client_id) and not should_skip_payment(ctype)

    # Режем на чанки <4096 по границам (без потери хвоста и битых тегов).
    # Кнопки — на последнее сообщение, reply-mapping — на первое (где head).
    chunks = split_text(body)
    bot_msg = None
    for i, chunk in enumerate(chunks):
        sent = await message.answer(
            chunk, parse_mode="HTML",
            reply_markup=triplet_kb if i == len(chunks) - 1 else None,
        )
        if bot_msg is None:
            bot_msg = sent

    # Привязка msg→page для reply-flow.
    try:
        await save_message_page(
            chat_id=bot_msg.chat.id,
            message_id=bot_msg.message_id,
            page_id=page_id,
            page_type="session",
            bot="arcana",
        )
    except Exception:
        pass

    # Источник=🔄 Бартер → спрашиваем «Что в бартере?» (создаст чеклист).
    if payment_source == "🔄 Бартер":
        try:
            from arcana.handlers.barter_prompt import propose_barter_prompt
            await propose_barter_prompt(
                message, kind="session", page_id=page_id,
                group_name=session_name or question or "Расклад",
            )
        except Exception as e:
            logger.warning("session barter prompt failed: %s", e)

    # Кнопки оплаты для платного клиента — отдельным сообщением.
    if show_payment:
        from arcana.handlers.payment import payment_keyboard
        await message.answer(
            f"💰 Как оплатил(а) {html.escape(client_name or 'клиент(а)')}?",
            parse_mode="HTML",
            reply_markup=payment_keyboard(page_id, "sessions"),
        )
    return page_id


# ────────────────────────── Основной обработчик ────────────────────────────

async def handle_add_session(
    message: Message, text: str, user_notion_id: str = ""
) -> None:
    try:
        tg_id = message.from_user.id
        tz_offset = await get_user_tz(tg_id)
        tz = timezone(timedelta(hours=tz_offset))

        # 1. Haiku парсит данные. max_tokens=4000: при 9+ триплетах JSON
        # уходит за 700 токенов; 600 усекало хвост массива (см. issue #81).
        raw = await ask_claude(text, system=PARSE_SESSION_SYSTEM, max_tokens=4000, temperature=0)
        data = _parse_json_safe(raw)
        if data is None:
            await log_error(text, "parse_error", bot_label="🌒 Arcana", error_code="–")
            await message.answer(PARSE_HELP_TEXT, parse_mode="HTML")
            return

        # 1a. Граундинг карт в транскрипт. Парсер мог подменить искажённое слово
        # валидной-но-ЧУЖОЙ картой («крыльева мячей» → «Король Жезлов»): промпт-
        # правило Haiku игнорит, проверка по 78 картам не ловит (чужая карта тоже
        # валидна). Сверяем каждую карту с тем, что РЕАЛЬНО в транскрипте;
        # негрундящиеся → дословный фрагмент → дальше нормализатор смапит алиасами
        # («крыльева мячей» → «Королева Мечей»). ДО split single/multi и resolve-
        # диалога → покрывает оба флоу одним вызовом, grounded data едет в pending.
        from core.card_grounding import ground_cards_in_data
        from miniapp.backend.tarot import resolve_deck_id, find_card
        _gr_deck = resolve_deck_id(data.get("deck") or "Уэйт")
        ground_cards_in_data(
            data, text, resolver=lambda s: bool(find_card(_gr_deck, s)),
        )

        # 1b. Multi-question session → отдельная ветка: сразу сохраняем N триплетов.
        # Принимаем оба ключа: новый "triplets" и legacy "items".
        items = data.get("triplets") or data.get("items") or []
        if isinstance(items, list) and len(items) >= 2:
            await _handle_multi_session(
                message, data, items, tz, tz_offset, user_notion_id
            )
            return

        client_name = data.get("client_name") or None
        client_id: Optional[str] = None
        self_client_missing = False
        if client_name:
            from core.client_resolve import resolve_or_create, is_valid_client_name
            if not is_valid_client_name(client_name):
                await message.answer("🤔 Не разобрала имя клиента — напиши ещё раз?")
                return
            client_id = await resolve_or_create(
                message, client_name, user_notion_id=user_notion_id,
            )
        else:
            # Личный расклад → автоматически на self-клиента «Кай (личный)».
            from core.client_resolve import resolve_self_client
            client_id = await resolve_self_client(user_notion_id=user_notion_id)
            if not client_id:
                # Fallback: ищем по имени из user_manager (legacy путь).
                from core.user_manager import get_user
                owner = await get_user(tg_id)
                owner_name = (owner or {}).get("name") or ""
                if owner_name:
                    sc = await _client_repo.find(owner_name, user_notion_id=user_notion_id)
                    if sc:
                        client_id = sc.id
                    else:
                        self_client_missing = True
                else:
                    self_client_missing = True

        deck = _match_deck(data.get("deck") or "") or "Уэйт"
        cards_text = _coerce_cards_str(data.get("cards"))
        card_names: List[str] = [c.strip() for c in cards_text.split(",") if c.strip()]
        bottom_card = (data.get("bottom_card") or "").strip()
        # Если single-flow без карт — это знак, что Sonnet не смог распарсить.
        if not card_names and not bottom_card and not data.get("triplets"):
            raise SessionParseError("no cards parsed in single flow")
        area = _normalize_area(data.get("area") or "")
        question = data.get("question") or area

        # 2. Справочник — нужные карты + дно (если есть)
        from arcana.tarot_loader import get_cards_context, missing_cards
        ctx_cards = card_names + ([bottom_card] if bottom_card else [])
        cards_context = get_cards_context(deck, ctx_cards)
        # Карта расклада без значения в справочнике → трактовка деградирует
        # (Sonnet отказывается), но исключения нет — логируем в мониторинг (#159).
        missing = missing_cards(deck, ctx_cards)
        if missing:
            await log_error(
                f"Колода {deck}: нет в справочнике — {', '.join(missing)}",
                "card_not_in_ref", bot_label="🌒 Arcana", error_code="ref",
                context=(message.text or "")[:200],
            )

        # 3. Память
        memory_context = ""
        try:
            from core.memory import get_memories_for_context, extract_context_keywords
            keywords = extract_context_keywords(data, client_name)
            if keywords:
                memory_context = await get_memories_for_context(user_notion_id, keywords)
        except Exception:
            pass

        # 4. Предыдущие расклады клиента
        prev_context = ""
        if client_id:
            try:
                prev_snippets = await _repo.prev_for_client(client_id, user_notion_id=user_notion_id)
                if prev_snippets:
                    prev_context = _format_prev_sessions(prev_snippets)
            except Exception:
                pass

        # 5. Трактовка: режим A (причесать авторскую из голоса) vs B (сгенерить).
        authored = (data.get("interpretation") or "").strip()
        interpretation = ""
        if authored:
            # Режим A — Кай надиктовала тезисы. Разворачиваем по значениям карт
            # (cards_context — опора против выдумки); текст идёт в interpretation
            # → дальше в саммари/RAG (учимся на голосе Кай).
            interpretation = await _polish_authored_interpretation(
                authored, cards_text, bottom_card, question, cards_context
            )
        elif card_names:
            # Режим B — трактовки в голосе нет, генерим по картам (как раньше).
            system = TAROT_SYSTEM
            if cards_context:
                system += f"\n\n--- СПРАВОЧНИК КАРТ ---\n{cards_context}"
            if memory_context:
                system += f"\n\n--- ПАМЯТЬ ---\n{memory_context}"
            if prev_context:
                system += f"\n\n--- ПРЕДЫДУЩИЕ РАСКЛАДЫ КЛИЕНТА ---\n{prev_context}"
            # RAG-AB интеграция A: похожие прошлые трактовки ПО ВСЕМ клиентам
            # (консистентность голоса). Аддитивно, graceful (#166).
            system += await _rag_voice_block(cards_text, question)

            user_prompt = (
                f"Расклад: {data.get('spread_type') or ''}\n"
                f"Вопрос: {question}\n"
                f"Карты: {cards_text}"
            )
            if bottom_card:
                user_prompt += f"\nДно колоды: {bottom_card}"
            interpretation = await ask_claude(
                user_prompt,
                system=system,
                model=_cfg.model_sonnet,
                max_tokens=2000,
                temperature=0.7,
            )

        # 6. Сохраняем сразу в Notion + постим в чат с кнопками управления.
        spread = _match_spread(data.get("spread_type") or "")
        payment_source_raw = data.get("payment_source") or None
        payment_source = (
            PAYMENT_SOURCE_MAP.get((payment_source_raw or "").lower(), payment_source_raw)
            if payment_source_raw else None
        )
        amount = float(data.get("amount") or 0)
        paid = float(data.get("paid") or 0)
        page_id = await _save_and_post_triplet(
            message,
            tz=tz,
            user_notion_id=user_notion_id,
            client_id=client_id,
            client_name=client_name,
            deck=deck,
            spread_type=spread or "🔺 Триплет",
            question=question,
            cards_text=cards_text,
            bottom_card=bottom_card,
            area=area,
            interpretation=interpretation,
            authored=bool(authored),
            session_name=None,  # одиночный — без сессии
            payment_source=payment_source,
            amount=amount,
            paid=paid,
            self_client_missing=self_client_missing,
        )

        # Фото расклада приложено к сообщению → в Cloudinary + на запись (#161).
        if page_id:
            photo_url = await _upload_spread_photo(message)
            if photo_url:
                await _repo.set_photo_url(page_id, photo_url)

    except SessionParseError as e:
        logger.warning("session parse error: %s", e)
        await log_error(
            (message.text or "")[:200], "parse_error",
            bot_label="🌒 Arcana", error_code="parse",
        )
        await message.answer(PARSE_HELP_TEXT, parse_mode="HTML")
        return
    except Exception as e:
        trace = tb.format_exc()
        logger.error("handle_add_session error: %s", trace)
        err_str = str(e)
        if "529" in err_str:
            code, suffix = "529", "серверная ошибка Anthropic · попробуй позже"
        elif any(x in err_str for x in ("500", "502", "503")):
            code, suffix = "5xx", "серверная ошибка · попробуй позже"
        elif "timeout" in err_str.lower():
            code, suffix = "timeout", "запрос завис · попробуй ещё раз"
        elif any(x in err_str for x in ("401", "403", "404")):
            code, suffix = "4xx", "ошибка конфигурации · пусть Кай правит код"
        else:
            code, suffix = "–", "что-то сломалось · пусть Кай правит код"
        logged = await log_error(
            (message.text or "")[:200], "processing_error",
            traceback=trace, bot_label="🌒 Arcana", error_code=code,
        )
        notion_status = "залогировано"
        await message.answer(f"❌ {suffix} · {notion_status}")


# ────────────────────────── Multi-question (сессия) ───────────────────────

def _resolve_dialog_kb(slug: str) -> InlineKeyboardMarkup:
    """4 кнопки: новый платный / новый бесплатный / self / отмена."""
    from core.utils import cancel_button
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(
                text="🤝 Новый клиент (Платный)",
                callback_data=f"client_resolve_new_paid:{slug}",
            ),
        ],
        [
            InlineKeyboardButton(
                text="🎁 Новый клиент (Бесплатный)",
                callback_data=f"client_resolve_new_free:{slug}",
            ),
        ],
        [
            InlineKeyboardButton(
                text="🌟 Это мне (self)",
                callback_data=f"client_resolve_self:{slug}",
            ),
            cancel_button("❌ Отмена", f"client_resolve_cancel:{slug}"),
        ],
    ])


def _short_resolve_slug() -> str:
    import secrets
    return secrets.token_hex(8)


async def _handle_multi_session(
    message: Message,
    data: dict,
    items: List[dict],
    tz: timezone,
    tz_offset: float,
    user_notion_id: str,
    *,
    forced_client_id: Optional[str] = None,
    forced_client_name: Optional[str] = None,
    forced_is_personal: bool = False,
) -> None:
    """Парсер увидел структуру «Тема: 1) … 2) …» — сохраняем N триплетов
    в одной сессии без preview-флоу: каждый получает свою трактовку и саммари.

    forced_client_id/name/is_personal — выставляются после resolve-диалога,
    чтобы пропустить find/ask логику.
    """
    tg_id = message.from_user.id
    session_name: str = (data.get("session_name") or "").strip()
    # Если КАЖДЫЙ item — триплет (3 карты + опц. дно), и Haiku не дала явной
    # категории — пишем «🔺 Триплет», а не «🌐 Сфера жизни» (см. #83).
    all_triplets = bool(items) and all(
        len((it.get("cards") or [])) == 3 for it in items
    )
    session_category = _resolve_session_category(
        data.get("session_category") or session_name,
        len(items),
        all_triplets=all_triplets,
    )
    deck_raw = data.get("deck") or "Уэйт"
    deck = _match_deck(deck_raw) or "Уэйт"
    parsed_client_name = (data.get("client_name") or "").strip() or None
    if parsed_client_name and not forced_client_name:
        from core.client_resolve import is_valid_client_name
        if not is_valid_client_name(parsed_client_name):
            await message.answer("🤔 Не разобрала имя клиента — напиши ещё раз?")
            return
    client_name = forced_client_name or parsed_client_name

    # Клиент / личный — если уже резолвлен через dialog, используем как есть.
    client_id: Optional[str] = forced_client_id
    if not client_id and not forced_is_personal:
        # Сначала пробуем по client_name (если задан) или session_name (для format A/B)
        lookup_name = client_name or session_name
        if lookup_name:
            c = await _client_repo.find(lookup_name, user_notion_id=user_notion_id)
            if c:
                client_id = c.id
                client_name = c.name or client_name
            else:
                # Не нашли — спрашиваем Кай через resolve-диалог.
                from arcana.pending_tarot import save_pending
                slug = _short_resolve_slug()
                await save_pending(tg_id, {
                    "type": "client_resolve_pending",
                    "slug": slug,
                    "data": data,
                    "tz_offset": tz_offset,
                    "user_notion_id": user_notion_id,
                })
                await message.answer(
                    f"«{html.escape(lookup_name)}» — это:",
                    parse_mode="HTML",
                    reply_markup=_resolve_dialog_kb(slug),
                )
                return
        else:
            # session_name пустой → self-сессия по умолчанию.
            from core.client_resolve import resolve_self_client
            client_id = await resolve_self_client(user_notion_id=user_notion_id)

    if not client_id and not forced_is_personal:
        # fallback на user_manager.owner
        from core.user_manager import get_user
        owner = await get_user(tg_id)
        owner_name = (owner or {}).get("name") or ""
        if owner_name:
            sc = await _client_repo.find(owner_name, user_notion_id=user_notion_id)
            if sc:
                client_id = sc.id
    is_personal = forced_is_personal or not client_name

    # Существовала ли ТЕМА (session_name+client) ДО этой отправки? Если да —
    # после сохранения обнулим её theme_summary (кросс-дневная сводка устарела),
    # вкладка покажет «Сгенерировать» (#165). Проверяем ДО вставки новых строк.
    theme_preexisted = False
    if session_name:
        try:
            theme_preexisted = await _repo.session_group_exists(
                session_name, client_id, user_notion_id
            )
        except Exception as e:
            logger.warning("session_group_exists failed: %s", e)

    # Контекст предыдущих раскладов клиента — общий для всех триплетов
    prev_context = ""
    if client_id:
        try:
            prev_snippets = await _repo.prev_for_client(client_id, user_notion_id=user_notion_id)
            if prev_snippets:
                prev_context = _format_prev_sessions(prev_snippets)
        except Exception:
            pass

    from arcana.tarot_loader import get_cards_context, missing_cards
    from core.html_for_telegram import html_to_telegram
    saved_n = 0
    first_page_id: Optional[str] = None
    saved_titles: List[str] = []
    saved_triplets: List[dict] = []  # для финального саммари сессии
    rag_batch: List[dict] = []       # триплеты для одного RAG-батч-эмбеддинга (#166)

    await message.answer(
        f"🃏 Сессия «{html.escape(session_name or '—')}» · {len(items)} триплетов — обрабатываю…"
    )

    for idx, it in enumerate(items, 1):
        try:
            question = (it.get("question") or "").strip() or f"{session_name} · вопрос {idx}"
            cards_text = _coerce_cards_str(it.get("cards"))
            bottom_card = (it.get("bottom_card") or "").strip()
            area = _normalize_area(it.get("area") or "")
            spread_type = (it.get("spread_type") or data.get("spread_type") or "Триплет")

            card_names = [c.strip() for c in cards_text.split(",") if c.strip()]
            ctx_cards = card_names + ([bottom_card] if bottom_card else [])
            cards_context = get_cards_context(deck, ctx_cards)
            # Карты триплета без значения в справочнике → лог в мониторинг (#159).
            missing = missing_cards(deck, ctx_cards)
            if missing:
                await log_error(
                    f"Колода {deck} · «{question}»: нет в справочнике — "
                    f"{', '.join(missing)}",
                    "card_not_in_ref", bot_label="🌒 Arcana", error_code="ref",
                    context=(message.text or "")[:200],
                )

            # Трактовка: режим A (причесать авторскую из голоса) vs B (сгенерить).
            authored = (it.get("interpretation") or "").strip()
            interpretation = ""
            if authored:
                # Режим A — тезисы этого триплета из голоса; разворачиваем по
                # значениям карт (cards_context — опора против выдумки).
                interpretation = await _polish_authored_interpretation(
                    authored, cards_text, bottom_card, question, cards_context
                )
            elif card_names:
                # Режим B — генерим по картам (как раньше).
                system = TAROT_SYSTEM
                if cards_context:
                    system += f"\n\n--- СПРАВОЧНИК КАРТ ---\n{cards_context}"
                if prev_context:
                    system += f"\n\n--- ПРЕДЫДУЩИЕ РАСКЛАДЫ КЛИЕНТА ---\n{prev_context}"
                # RAG-AB интеграция A: похожие прошлые трактовки ПО ВСЕМ клиентам
                # (консистентность голоса). Аддитивно, graceful (#166).
                system += await _rag_voice_block(cards_text, question)
                user_prompt = (
                    f"Сессия: {session_name}\n"
                    f"Расклад: {spread_type}\n"
                    f"Вопрос: {question}\n"
                    f"Карты: {cards_text}"
                )
                if bottom_card:
                    user_prompt += f"\nДно колоды: {bottom_card}"
                interpretation = await ask_claude(
                    user_prompt, system=system,
                    model=_cfg.model_sonnet, max_tokens=2000,
                    temperature=0.7,
                )

            # Haiku — саммари триплета (по interpretation: авторской или машинной)
            t_summary = await _make_triplet_summary(
                question, cards_text, bottom_card, interpretation
            )

            # Канон → EN для Notion
            cards_en = _canon_cards_str(cards_text, deck)
            bottom_en = _canon_card(bottom_card, deck) if bottom_card else ""
            # Режим A: не дописываем машинный блок дна к авторскому тексту.
            if bottom_en and "🂠" not in interpretation and not authored:
                interpretation = (
                    interpretation.rstrip()
                    + f"\n\n<h3>🂠 {bottom_en} · фон</h3>"
                    + "<p>Скрытый фон расклада.</p>"
                )

            from core.html_sanitize import sanitize_interpretation
            interpretation = sanitize_interpretation(interpretation)

            page_id = await _repo.add(
                date=_now_iso(tz),
                spread_type=session_category,
                title=question,
                question=question,
                cards=cards_en or cards_text,
                interpretation=interpretation,
                amount=0, paid=0,
                session_type="Личный" if is_personal else "Клиентский",
                client_id=client_id,
                user_notion_id=user_notion_id,
                area=area,
                deck=deck,
                payment_source=None,
                session=session_name or None,
                triplet_summary=t_summary or None,
                bottom_card=bottom_en or None,
            )
            if page_id:
                saved_n += 1
                saved_titles.append(question)
                if not first_page_id:
                    first_page_id = page_id

                # RAG-AB: копим триплет для ОДНОГО батч-эмбеддинга после цикла
                # (N триплетов = 1 запрос Voyage, бережём лимит 3 RPM, #166).
                rag_batch.append({
                    "triplet_id": page_id,
                    "cards": cards_text,
                    "question": question,
                    "interpretation": interpretation,
                    "client_id": client_id,
                    "session_name": session_name or None,
                    "occurred_at": _now_iso(tz)[:10],
                })

                # Триплет в чат: вопрос + карты + дно + трактовка (telegram-safe).
                interp_tg = html_to_telegram(interpretation)
                cards_line = (
                    ", ".join(c.strip() for c in cards_text.split(",") if c.strip())
                    if cards_text else ""
                )
                head = (
                    f"<b>{html.escape(question)}</b>\n"
                    f"🔺 Триплет · {html.escape(deck)}\n"
                )
                if cards_line:
                    head += f"🃏 {html.escape(cards_line)}\n"
                if bottom_card:
                    head += f"🂠 {html.escape(bottom_card)}\n"
                body_full = f"{head}\n{interp_tg}"
                tkb = _triplet_keyboard(page_id)
                # Чанки <4096; кнопки — на последнее сообщение.
                sent = await send_long(message, body_full, parse_mode="HTML", reply_markup=tkb)
                # Маппим сообщение триплета → reply на него = правка (как в single,
                # иначе reply на карточку в мульти-сессии не находил page → уходил
                # в НОВЫЙ расклад). Паритет reply-правки single/multi.
                if sent is not None:
                    try:
                        from core.message_pages import save_message_page
                        await save_message_page(
                            chat_id=sent.chat.id, message_id=sent.message_id,
                            page_id=page_id, page_type="session", bot="arcana",
                        )
                    except Exception:
                        pass

                saved_triplets.append({
                    "question": question,
                    "cards": cards_text,
                    "bottom": bottom_card,
                    "summary": t_summary or "",
                })
        except Exception as e:
            logger.error("multi-session item %d failed: %s", idx, e)

    # RAG-AB: все триплеты сессии — ОДНИМ батч-эмбеддингом + одним upsert
    # (вместо N запросов; критично под 3 RPM Voyage, #166).
    await _rag_index_batch_safe(rag_batch)

    # Фото расклада приложено к сообщению → в Cloudinary + на якорный
    # (первый) триплет сессии (#161). Одно фото на всю сессию.
    if first_page_id:
        photo_url = await _upload_spread_photo(message)
        if photo_url:
            await _repo.set_photo_url(first_page_id, photo_url)

    # Финальный общий вывод: Sonnet → plain text, кеш в session_cache.
    session_summary_text = ""
    if saved_triplets and session_name:
        try:
            triplet_block = "\n\n".join(
                f"Вопрос: {t['question']}\n"
                f"Карты: {t['cards']}\n"
                + (f"Дно: {t['bottom']}\n" if t['bottom'] else "")
                + (f"Краткое: {t['summary']}" if t['summary'] else "")
                for t in saved_triplets
            )
            sess_prompt = (
                f"Ты — таролог Кай. На основе {len(saved_triplets)} триплетов одной "
                f"сессии «{session_name}» напиши общий вывод 2-3 предложениями простым "
                f"русским языком. Опирайся на дно колоды каждого триплета как на фон, "
                f"и на связь между вопросами.\n\n"
                f"Output as plain Russian text, 2-3 sentences, no formatting, no markdown, "
                f"no HTML tags. Только текст вывода, ничего больше.\n\n"
                f"--- ТРИПЛЕТЫ ---\n{triplet_block}"
            )
            raw_summary = await ask_claude(
                sess_prompt, max_tokens=400,
                model=_cfg.model_sonnet,
                temperature=0.5,
            )
            from core.html_sanitize import sanitize_summary
            session_summary_text = sanitize_summary(raw_summary or "")
        except Exception as e:
            logger.warning("session_summary generation failed: %s", e)

    # Саммари СОБЫТИЯ (триплеты этой отправки) — в БД на якорный (первый)
    # триплет этой отправки (#162); кеш для миниапа как fast-path.
    if first_page_id and session_summary_text:
        try:
            await _repo.set_session_summary(first_page_id, session_summary_text)
        except Exception as e:
            logger.warning("session_summary PG write failed: %s", e)

    # Тема пополнилась новой отправкой → кросс-дневная сводка устарела: обнуляем
    # theme_summary всей группы, миниап предложит «Сгенерировать» (#165).
    if theme_preexisted and session_name:
        try:
            await _repo.clear_theme_summary(session_name, client_id)
        except Exception as e:
            logger.warning("clear_theme_summary failed: %s", e)
    try:
        from core.session_cache import session_summary_key, cache_delete, cache_set
        if session_name:
            key = session_summary_key(session_name, client_id)
            cache_delete(key)
            if session_summary_text:
                cache_set(key, session_summary_text)
    except Exception:
        pass

    bullet_list = "\n".join(f"• {html.escape(t)}" for t in saved_titles[:12])
    if len(saved_titles) > 12:
        bullet_list += f"\n• … и ещё {len(saved_titles) - 12}"

    final_msg = (
        f"✅ Сессия «{html.escape(session_name or '—')}» · "
        f"{saved_n} триплетов сохранены\n"
    )
    if bullet_list:
        final_msg += bullet_list + "\n"
    if session_summary_text:
        final_msg += (
            f"\n<b>Общий вывод</b>\n"
            f"<i>{html.escape(session_summary_text)}</i>"
        )
    await send_long(message, final_msg, parse_mode="HTML")

    # Кнопки оплаты ЗА СЕССИЮ ЦЕЛИКОМ — anchor = first saved triplet.
    # Skip для self/бесплатных и для сессий, где ни одного триплета не сохранили.
    if first_page_id and client_id:
        try:
            from core.client_resolve import client_get_type, should_skip_payment
            from arcana.handlers.payment import payment_keyboard
            ctype = await client_get_type(client_id)
            if not should_skip_payment(ctype):
                await message.answer(
                    f"💰 Как оплатил(а) {html.escape(client_name or 'клиент(а)')} "
                    f"за всю сессию?",
                    parse_mode="HTML",
                    reply_markup=payment_keyboard(first_page_id, "sessions"),
                )
        except Exception as e:
            logger.warning("multi-flow payment kb skipped: %s", e)


# ────────────────────────── Callbacks ──────────────────────────────────────



# ────────────────────── Триплет: правка / удаление ────────────────────────

@router.callback_query(F.data.startswith("triplet_correct:"))
async def cb_triplet_correct(call: CallbackQuery) -> None:
    """[✏️ Поправить] под сохранённым триплетом — переводим в режим правки."""
    await call.answer()
    short_id = call.data.split(":", 1)[1]
    uid = call.from_user.id
    from arcana.pending_tarot import save_pending
    await save_pending(uid, {
        "awaiting_triplet_edit": True,
        "triplet_short_id": short_id,
    })
    try:
        await call.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass
    await call.message.answer(
        "✏️ Что поправить в этом триплете? Напиши коммент."
    )


@router.callback_query(F.data.startswith("triplet_remove_yes:"))
async def cb_triplet_remove_yes(call: CallbackQuery) -> None:
    """[✅ Да, удалить] — архивируем страницу в Notion."""
    await call.answer()
    short_id = call.data.split(":", 1)[1]
    from core.user_manager import get_user_notion_id
    user_notion_id = (await get_user_notion_id(call.from_user.id)) or ""
    entry = await _resolve_triplet_page(short_id, user_notion_id)
    if not entry:
        await call.message.edit_text("⚠️ Триплет не найден.")
        return
    ok = await _repo.archive(entry.id)
    if not ok:
        await call.message.edit_text("⚠️ Не удалось удалить триплет.")
        return
    # RAG-AB: убрать вектор из Qdrant — soft-delete оставляет строку в PG, но
    # иначе search отдавал бы удалённый триплет (#166). Провал не роняет удаление.
    await _rag_delete_safe(entry.id)
    # Триплет удалён → общее саммари сессии И кросс-дневная сводка темы устарели:
    # чистим БД + кеш (#162 #165).
    if entry.session_name:
        try:
            await _repo.clear_session_summary(entry.session_name, entry.client_id)
        except Exception as e:
            logger.warning("clear_session_summary failed: %s", e)
        try:
            await _repo.clear_theme_summary(entry.session_name, entry.client_id)
        except Exception as e:
            logger.warning("clear_theme_summary failed: %s", e)
        try:
            from core.session_cache import cache_delete, session_summary_key
            cache_delete(session_summary_key(entry.session_name, entry.client_id))
        except Exception:
            pass
    await call.message.edit_text("🗑 Триплет удалён.")


@router.callback_query(F.data.startswith("triplet_remove_no:"))
async def cb_triplet_remove_no(call: CallbackQuery) -> None:
    await call.answer()
    try:
        await call.message.edit_text("Отменено.")
    except Exception:
        pass


@router.callback_query(F.data.startswith("triplet_remove:"))
async def cb_triplet_remove(call: CallbackQuery) -> None:
    """[🗑 Удалить] — показать confirm-кнопки."""
    await call.answer()
    short_id = call.data.split(":", 1)[1]
    from core.user_manager import get_user_notion_id
    user_notion_id = (await get_user_notion_id(call.from_user.id)) or ""
    entry = await _resolve_triplet_page(short_id, user_notion_id)
    title = entry.question if entry else "—"
    await call.message.answer(
        f"🗑 Удалить триплет «{html.escape(title)}»?\nДействие необратимо.",
        parse_mode="HTML",
        reply_markup=_triplet_remove_confirm_keyboard(short_id),
    )


# ───────── Client resolve dialog (multi-flow с неизвестным именем) ─────────

async def _resume_multi_after_resolve(
    call: CallbackQuery,
    pending: dict,
    *,
    forced_client_id: Optional[str],
    forced_client_name: Optional[str],
    forced_is_personal: bool,
) -> None:
    """Достаём parsed data из pending и продолжаем _handle_multi_session
    с уже резолвленым клиентом."""
    from arcana.pending_tarot import delete_pending
    data = pending.get("data") or {}
    tz_offset = float(pending.get("tz_offset") or 3)
    user_notion_id = pending.get("user_notion_id") or ""
    tz = timezone(timedelta(hours=tz_offset))
    items = data.get("triplets") or data.get("items") or []
    await delete_pending(call.from_user.id)
    try:
        await call.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass
    await _handle_multi_session(
        call.message, data, items, tz, tz_offset, user_notion_id,
        forced_client_id=forced_client_id,
        forced_client_name=forced_client_name,
        forced_is_personal=forced_is_personal,
    )


async def _create_resolved_client(
    user_notion_id: str, name: str, client_type: str
) -> Optional[tuple]:
    from datetime import datetime as _dt, timezone as _tz
    today = _dt.now(_tz.utc).strftime("%Y-%m-%d")
    pid = await _client_repo.add(
        name=name, date=today, user_notion_id=user_notion_id,
        client_type=client_type,
    )
    return (pid, name) if pid else None


@router.callback_query(F.data.startswith("client_resolve_new_paid:"))
async def cb_client_resolve_new_paid(call: CallbackQuery) -> None:
    await call.answer()
    slug = call.data.split(":", 1)[1]
    from arcana.pending_tarot import get_pending
    pending = await get_pending(call.from_user.id) or {}
    if pending.get("slug") != slug or pending.get("type") != "client_resolve_pending":
        return
    user_notion_id = pending.get("user_notion_id") or ""
    name = (pending.get("data", {}).get("session_name") or "").strip()
    if not name:
        return
    res = await _create_resolved_client(user_notion_id, name, CLIENT_TYPE_PAID)
    if not res:
        await call.message.answer("⚠️ Не удалось создать клиента.")
        return
    cid, cname = res
    await call.message.answer(
        f"✅ Клиент «{html.escape(cname)}» создан · 🤝 Платный · обрабатываю сессию…",
        parse_mode="HTML",
    )
    await _resume_multi_after_resolve(
        call, pending,
        forced_client_id=cid, forced_client_name=cname, forced_is_personal=False,
    )


@router.callback_query(F.data.startswith("client_resolve_new_free:"))
async def cb_client_resolve_new_free(call: CallbackQuery) -> None:
    await call.answer()
    slug = call.data.split(":", 1)[1]
    from arcana.pending_tarot import get_pending
    pending = await get_pending(call.from_user.id) or {}
    if pending.get("slug") != slug or pending.get("type") != "client_resolve_pending":
        return
    user_notion_id = pending.get("user_notion_id") or ""
    name = (pending.get("data", {}).get("session_name") or "").strip()
    if not name:
        return
    res = await _create_resolved_client(user_notion_id, name, CLIENT_TYPE_FREE)
    if not res:
        await call.message.answer("⚠️ Не удалось создать клиента.")
        return
    cid, cname = res
    await call.message.answer(
        f"✅ Клиент «{html.escape(cname)}» создан · 🎁 Бесплатный · обрабатываю сессию…",
        parse_mode="HTML",
    )
    await _resume_multi_after_resolve(
        call, pending,
        forced_client_id=cid, forced_client_name=cname, forced_is_personal=False,
    )


@router.callback_query(F.data.startswith("client_resolve_self:"))
async def cb_client_resolve_self(call: CallbackQuery) -> None:
    await call.answer()
    slug = call.data.split(":", 1)[1]
    from arcana.pending_tarot import get_pending
    pending = await get_pending(call.from_user.id) or {}
    if pending.get("slug") != slug or pending.get("type") != "client_resolve_pending":
        return
    user_notion_id = pending.get("user_notion_id") or ""
    from core.client_resolve import resolve_self_client
    cid = await resolve_self_client(user_notion_id=user_notion_id)
    await call.message.answer("🌟 Личная сессия · обрабатываю…")
    await _resume_multi_after_resolve(
        call, pending,
        forced_client_id=cid, forced_client_name=None, forced_is_personal=True,
    )


@router.callback_query(F.data.startswith("client_resolve_cancel:"))
async def cb_client_resolve_cancel(call: CallbackQuery) -> None:
    await call.answer()
    from arcana.pending_tarot import delete_pending
    await delete_pending(call.from_user.id)
    try:
        await call.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass
    await call.message.answer("❌ Сессия отменена.")


async def handle_triplet_correction(
    message: Message, correction_text: str, pending: dict, user_notion_id: str
) -> None:
    """Кнопка «Поправить»: pending{triplet_short_id} + текст правки → правка
    триплета (карта и/или трактовка). Делегирует в общее ядро."""
    from arcana.pending_tarot import delete_pending
    await delete_pending(message.from_user.id)
    short_id = pending.get("triplet_short_id") or ""
    entry = await _resolve_triplet_page(short_id, user_notion_id)
    if not entry:
        await message.answer("⚠️ Триплет не найден.")
        return
    await _apply_triplet_correction(message, correction_text, entry, user_notion_id)


async def correct_triplet_by_id(
    message: Message, correction_text: str, page_id: str, user_notion_id: str
) -> bool:
    """Reply на карточку триплета = правка СВОБОДНЫМ ТЕКСТОМ (карта/трактовка),
    как кнопка «Поправить» — паритет с Nexus (reply = правка). page_id берётся
    из message_pages. Возвращает False если триплет не найден."""
    entry = await _resolve_triplet_page(page_id, user_notion_id)
    if not entry:
        return False
    await _apply_triplet_correction(message, correction_text, entry, user_notion_id)
    return True


async def _apply_triplet_correction(
    message: Message, correction_text: str, entry: TripletEntry, user_notion_id: str
) -> None:
    """Ядро правки триплета — ОБЩЕЕ для кнопки «Поправить» и reply на карточку:
    card_edit → пересбор карт + справочника → Sonnet регенерит трактовку →
    update_cards + update_interpretation → RAG reindex → репост с подтверждением.
    Pending НЕ трогает (это забота вызывающего)."""
    page_id = entry.id
    question = entry.question
    cards_raw = entry.cards
    interp_raw = entry.interpretation
    deck = entry.deck or "Уэйт"
    sname = entry.session_name
    cid = entry.client_id
    bottom_raw = getattr(entry, "bottom_card", "") or ""

    # Карты в PG хранятся canonical-EN. Для трактовки нужны RU-имена (заголовки)
    # и справочник колоды — иначе Sonnet пишет EN-заголовки без значений (#160).
    cards_ru = _cards_to_ru(cards_raw, deck) or cards_raw
    bottom_ru = _canon_card_ru(bottom_raw, deck) if bottom_raw else ""

    # Правка может менять КАРТУ («королева кубков, а не король»), а не только
    # текст. Тогда пересобираем карты ДО справочника/генерации — иначе трактовка
    # уйдёт по старой карте, а данные останутся противоречивыми (#166 #3).
    old_cards_ru = cards_ru
    card_edit = await _parse_card_edit(correction_text, cards_ru, bottom_ru)
    if card_edit:
        cards_ru = card_edit["cards_ru"]
        bottom_ru = card_edit["bottom_ru"]

    from arcana.tarot_loader import get_cards_context
    ctx_cards = [c.strip() for c in cards_ru.split(",") if c.strip()]
    if bottom_ru:
        ctx_cards.append(bottom_ru)
    cards_context = get_cards_context(deck, ctx_cards)

    # Переиспользуем канонический TAROT_SYSTEM (эмодзи-заголовки, RU-имена,
    # структура Общий смысл/карты/дно/Вывод, запрет позиций) — чтобы правка
    # выходила в ТОМ ЖЕ формате, что и исходная трактовка, а не в параллельном.
    system = TAROT_SYSTEM
    if cards_context:
        system += f"\n\n--- СПРАВОЧНИК КАРТ ---\n{cards_context}"
    prompt = (
        "Это ПРАВКА уже сохранённой трактовки триплета по замечанию Кай. "
        "Верни ПОЛНУЮ новую трактовку в том же формате (структура и эмодзи "
        "по правилам выше), учтя замечание.\n\n"
        f"Вопрос триплета: {question}\n"
        f"Карты: {cards_ru}\n"
        + (f"Дно колоды: {bottom_ru}\n" if bottom_ru else "")
        + f"\nТекущая трактовка (контекст):\n{interp_raw}\n\n"
        f"Замечание Кай: {correction_text}"
    )
    try:
        new_interp = await ask_claude(
            prompt, system=system,
            model=_cfg.model_sonnet, max_tokens=2000,
            temperature=0.7,
        )
    except Exception as e:
        logger.error("triplet correction sonnet failed: %s", e)
        await message.answer("❌ Не получилось скорректировать трактовку.")
        return

    from core.html_sanitize import sanitize_interpretation
    from core.html_for_telegram import html_to_telegram
    # Дно — фоновый блок, как в create-флоу (если Sonnet его не дал сам).
    if bottom_ru and "🂠" not in new_interp:
        new_interp = (
            new_interp.rstrip()
            + f"\n\n<h3>🂠 {bottom_ru} · фон</h3><p>Скрытый фон расклада.</p>"
        )
    new_interp = sanitize_interpretation(new_interp)

    # Регенерим Haiku-саммари, затем обновляем Notion одним вызовом репо.
    new_summary = await _make_triplet_summary(question, cards_ru, bottom_ru, new_interp)
    await _repo.update_interpretation(page_id, new_interp, new_summary)

    # Карта реально сменилась → пишем новые карты в данные (canonical-EN, как в
    # create-флоу), иначе при новом тексте заголовок остался бы старой картой.
    if card_edit:
        cards_en = _canon_cards_str(cards_ru, deck) or cards_ru
        bottom_en = _canon_card(bottom_ru, deck) if bottom_ru else None
        await _repo.update_cards(page_id, cards_en, bottom_en)

    # RAG-AB: текст трактовки изменился → перезаписываем вектор (upsert по id,
    # delete не нужен). Карты — в RU-форме (cards_ru), как индексировал create-флоу
    # (там cards_text был RU), чтобы вектор не разошёлся с корпусом (#165 #166).
    await _rag_index_safe(
        page_id, cards=cards_ru, question=question, interpretation=new_interp,
        client_id=cid, session_name=sname, occurred_at=entry.date,
    )

    # Триплет изменён → общее саммари сессии И кросс-дневная сводка темы устарели:
    # чистим БД + кеш, миниап предложит регенерацию (#162 #165).
    if sname:
        try:
            await _repo.clear_session_summary(sname, cid)
        except Exception as e:
            logger.warning("clear_session_summary failed: %s", e)
        try:
            await _repo.clear_theme_summary(sname, cid)
        except Exception as e:
            logger.warning("clear_theme_summary failed: %s", e)
        try:
            from core.session_cache import cache_delete, session_summary_key
            cache_delete(session_summary_key(sname, cid))
        except Exception:
            pass

    interp_tg = html_to_telegram(new_interp)
    head_lines = [f"✏️ <b>{html.escape(question)}</b>"]
    if card_edit:
        # Смена карты → ПЕРЕСОБИРАЕМ заголовок с НОВОЙ картой (📍/🂠), как в
        # create-флоу. Иначе карточка после правки оставалась бы со старой картой
        # в заголовке при новой трактовке. Правка ТЕКСТА сюда не заходит —
        # заголовок не трогаем.
        head_lines.append(
            f"🔄 Карта обновлена: {html.escape(old_cards_ru)} → {html.escape(cards_ru)}"
        )
        new_cards_line = ", ".join(c.strip() for c in cards_ru.split(",") if c.strip())
        if new_cards_line:
            head_lines.append(f"📍 {html.escape(new_cards_line)}")
        if bottom_ru:
            head_lines.append(f"🂠 {html.escape(bottom_ru)}")
    head = "\n".join(head_lines)
    body = f"{head}\n\n{interp_tg}"
    tkb = _triplet_keyboard(page_id)
    await send_long(message, body, parse_mode="HTML", reply_markup=tkb)


# ────────────────────────── Фото расклада ──────────────────────────────────

async def handle_tarot_photo(message: Message, user_notion_id: str = "") -> None:
    try:
        photo = message.photo[-1]
        file = await message.bot.get_file(photo.file_id)
        bio = await message.bot.download_file(file.file_path)
        image_b64 = base64.standard_b64encode(bio.read()).decode()

        await message.answer("🔍 Распознаю карты...")

        raw = await ask_claude_vision(
            "Определи все карты в раскладе, колоду и тип расклада.",
            image_b64,
            system=VISION_SYSTEM,
            temperature=0,
        )
        vision_data = _parse_json_safe(raw)
        if vision_data is None:
            await message.answer("⚠️ Не смог распознать карты. Опиши текстом.")
            return

        cards = vision_data.get("cards") or []
        spread_type = vision_data.get("spread_type") or "Другой"
        deck = _match_deck(vision_data.get("deck") or "") or "Уэйт"
        bottom_card = (vision_data.get("bottom_card") or "").strip()

        if not cards:
            await message.answer("⚠️ Карты не определены. Опиши текстом.")
            return

        cards_text = ", ".join(
            f"{c.get('position', '')}: {c.get('card', '')}" for c in cards
        )
        card_names: List[str] = [c.get("card", "") for c in cards if c.get("card")]
        question = message.caption or "общий расклад"

        # Загрузить справочник
        from arcana.tarot_loader import get_cards_context
        ctx_cards = card_names + ([bottom_card] if bottom_card else [])
        cards_context = get_cards_context(deck, ctx_cards)

        system = TAROT_SYSTEM
        if cards_context:
            system += f"\n\n--- СПРАВОЧНИК КАРТ ---\n{cards_context}"

        user_prompt = f"Расклад: {spread_type}\nВопрос: {question}\nКарты: {cards_text}"
        if bottom_card:
            user_prompt += f"\nДно колоды: {bottom_card}"
        interpretation = await ask_claude(
            user_prompt,
            system=system,
            model=_cfg.model_sonnet,
            max_tokens=2000,
            temperature=0.7,
        )

        tg_id = message.from_user.id
        tz_offset = await get_user_tz(tg_id)

        # Личный расклад с фото — резолвим self-клиента (новый путь).
        from core.client_resolve import resolve_self_client
        self_client_id: Optional[str] = await resolve_self_client(
            user_notion_id=user_notion_id
        )
        self_client_missing = not bool(self_client_id)

        tz = timezone(timedelta(hours=tz_offset))
        await _save_and_post_triplet(
            message,
            tz=tz,
            user_notion_id=user_notion_id,
            client_id=self_client_id,
            client_name=None,
            deck=deck,
            spread_type=spread_type or "🔺 Триплет",
            question=question,
            cards_text=cards_text,
            bottom_card=bottom_card,
            area=AREA_DEFAULT,
            interpretation=interpretation,
            session_name=None,
            payment_source=None,
            amount=0.0,
            paid=0.0,
            self_client_missing=self_client_missing,
        )

    except Exception as e:
        trace = tb.format_exc()
        logger.error("handle_tarot_photo error: %s", trace)
        await message.answer("❌ Ошибка при анализе фото.")


# ────────────────────────── Поиск раскладов ────────────────────────────────

async def handle_session_search(
    message: Message, text: str, user_notion_id: str = ""
) -> None:
    """Поиск прошлых раскладов по ключевым словам в Теме."""
    try:
        raw = await ask_claude(
            text, system=SESSION_SEARCH_PARSE_SYSTEM, max_tokens=150,
            temperature=0,
        )
        data = _parse_json_safe(raw) or {}
        keywords = [
            k.strip() for k in (data.get("keywords") or []) if isinstance(k, str) and k.strip()
        ]
        if not keywords:
            await message.answer("🔍 Не поняла что искать. Напиши имя или тему яснее.")
            return

        results = await _repo.search(keywords, user_notion_id=user_notion_id, limit=10)
        kw_display = html.escape(", ".join(keywords))
        if not results:
            await message.answer(f"🔍 По «{kw_display}» раскладов не нашла.")
            return

        lines: List[str] = [f"🔍 <b>Расклады по «{kw_display}»</b>:"]
        shown = results[:5]
        for s in shown:
            meta_parts = [x for x in (s.spread_name, s.area_name) if x]
            meta = " · ".join(html.escape(x) for x in meta_parts)
            block = f"\n📅 <b>{s.date}</b> · {html.escape(s.theme)}"
            if meta:
                block += f"\n   {meta}"
            if s.cards_short:
                block += f"\n   🃏 {html.escape(s.cards_short)}"
            lines.append(block)

        if len(results) > len(shown):
            lines.append(f"\n… и ещё {len(results) - len(shown)}")

        await message.answer("\n".join(lines), parse_mode="HTML")

    except Exception as e:
        trace = tb.format_exc()
        logger.error("handle_session_search error: %s", trace)
        await message.answer("❌ Не удалось найти расклады.")


# ────────────────────────── Быстрая трактовка ──────────────────────────────

async def handle_tarot_interpret(message: Message, text: str) -> None:
    interpretation = await ask_claude(
        f"Карты/расклад: {text}",
        system=TAROT_SYSTEM,
        model=_cfg.model_sonnet,
        max_tokens=2000,
        temperature=0.7,
    )
    await send_long(
        message, f"🔮 <b>Трактовка:</b>\n\n{interpretation}", parse_mode="HTML"
    )
