"""core/classifier.py — классификация и обработка сообщений Nexus (без circular import)."""
from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List

from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from core.claude_client import ask_claude
from core.notion_client import finance_add, log_error
from core.config import ARCANA_KEYWORDS
from core.list_classifier import (
    _LIST_BUY_RE, _LIST_CHECK_RE, _SUBTASK_RE, _LIST_INV_ADD_RE,
    _LIST_INV_SEARCH_RE, _LIST_DONE_RE, _LIST_INV_UPDATE_RE,
    LIST_HAIKU_TYPES,
)
from nexus.handlers.utils import react

logger = logging.getLogger("nexus.classifier")
MOSCOW_TZ = timezone(timedelta(hours=3))

# Маппинг известных тегов на эмодзи
TAGS_EMOJI = {
    "практика": "🔮",
    "таро": "🔮",
    "ленорман": "🃏",
    "ритуал": "🕯️",
    "расходники": "🕯️",
    "идея": "💡",
    "рецепт": "🍳",
    "здоровье": "❤️",
    "финансы": "💰",
    "мысль": "🧠",
}

_classify_last_raw: str = ""

# ── Парсинг "до [день_недели]" в дедлайне ──────────────────────────────────────
_WEEKDAY_DEADLINE_RE = re.compile(
    r'\bдо\s+(понедельника|вторника|среды|четверга|пятницы|субботы|воскресенья'
    r'|пн|вт|ср|чт|пт|сб|вс)\b',
    re.IGNORECASE,
)
_RU_WEEKDAY_NUM = {
    'понедельника': 0, 'пн': 0,
    'вторника': 1,    'вт': 1,
    'среды': 2,       'ср': 2,
    'четверга': 3,    'чт': 3,
    'пятницы': 4,     'пт': 4,
    'субботы': 5,     'сб': 5,
    'воскресенья': 6, 'вс': 6,
}


def _nearest_weekday_iso(target_wd: int, tz_offset: int) -> str:
    """Ближайший target_wd от завтра включительно. Если сегодня уже этот день → +7."""
    today = datetime.now(timezone(timedelta(hours=tz_offset))).date()
    days_ahead = (target_wd - today.weekday()) % 7
    if days_ahead == 0:
        days_ahead = 7
    return (today + timedelta(days=days_ahead)).strftime("%Y-%m-%d")


def today_moscow() -> str:
    return datetime.now(MOSCOW_TZ).strftime("%Y-%m-%d")


def _today() -> str:
    return datetime.now(MOSCOW_TZ).isoformat()[:10]


def _next_weekday_iso(day_ru: str) -> str:
    """Вернуть ISO дату ближайшего дня недели (для примеров в промпте)."""
    _dow_map = {"Пн": 0, "Вт": 1, "Ср": 2, "Чт": 3, "Пт": 4, "Сб": 5, "Вс": 6}
    target = _dow_map.get(day_ru, 6)
    today = datetime.now().date()
    days_ahead = (target - today.weekday()) % 7
    if days_ahead == 0:
        days_ahead = 7
    return (today + timedelta(days=days_ahead)).strftime("%Y-%m-%d")


_DOW_RU = ["понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье"]


def build_system(tz_offset: int = 3) -> str:
    now_local = datetime.now(timezone(timedelta(hours=tz_offset)))
    today = now_local.strftime("%Y-%m-%d")
    today_dow = _DOW_RU[now_local.weekday()]
    night_rule = (
        f"- НОЧНАЯ ЛОГИКА: сейчас {now_local.strftime('%H:%M')} (ночь до 05:00) — "
        f"'завтра' = СЕГОДНЯ ({today}), 'послезавтра' = завтра ({(now_local + timedelta(days=1)).strftime('%Y-%m-%d')})"
    ) if now_local.hour < 5 else ""
    cats = ", ".join(["🐾 Коты", "🏠 Жилье", "🚬 Привычки", "🍜 Продукты",
                      "🍱 Кафе/Доставка", "🚕 Транспорт", "💅 Бьюти", "👗 Гардероб",
                      "💻 Подписки", "🏥 Здоровье", "📚 Хобби/Учеба",
                      "🤖 Боты", "💰 Зарплата", "💳 Прочее", "👥 Люди"])
    srcs = ", ".join(["💳 Карта", "💵 Наличные", "🔄 Бартер"])

    return "\n".join([
        "КРИТИЧЕСКИ ВАЖНО: Ты ТОЛЬКО классифицируешь сообщения и возвращаешь JSON. Никогда не отвечай на вопросы, не давай советы, не объясняй что ты можешь или не можешь. Если сообщение похоже на поиск чего-либо в истории/заметках/задачах — это note_search или task_search. Всегда возвращай ТОЛЬКО валидный JSON без markdown и пояснений. Если не можешь классифицировать — верни {\"type\":\"unknown\"}.",
        "",
        f"Сегодня: {today}, {today_dow}.",
        "",
        "Классифицируй сообщение пользователя. Исправляй опечатки. Ответь ТОЛЬКО JSON без markdown:",
        "",
        "expense — трата денег:",
        '{"type":"expense","amount":450.0,"title":"назначение платежа","category":"<из ' + cats + '>","source":"<из ' + srcs + '>","confidence":"high если явно расход, иначе low"}',
        "",
        "income — поступление денег:",
        '{"type":"income","amount":35000.0,"title":"откуда поступление","category":"<из ' + cats + '>","source":"<из ' + srcs + '>","confidence":"high если явно доход, иначе low"}',
        "",
        "update — исправить последнюю запись финансов:",
        '{"type":"update","target":"<expense|income>","field":"<source|category|amount>","new_value":"новое значение"}',
        "Примеры: исправь на налик/карту → field=source; на категорию → field=category; сумму → field=amount",
        "",
        "edit_record — изменить поле существующей записи (задачи или финансовой):",
        '{"type":"edit_record","record_type":"task|finance","record_hint":"поисковые слова","field":"category|priority|title|deadline|status","new_value":"новое значение"}',
        "Примеры: 'поменяй категорию задачи купить корм на продукты' → edit_record",
        "         'переименуй задачу купить корм в купить корм котам' → edit_record",
        "         'смени приоритет купить молоко на срочно' → edit_record field=priority new_value=Срочно",
        "         'поставь статус в процессе для купить корм' → edit_record field=status new_value=В процессе",
        "ВАЖНО: edit_record только если явно упомянуто изменение поля существующей записи!",
        "",
        'Вход: "погладить кота каждый день в 9" → {"type":"task","title":"погладить кота","category":"🐾 Коты","priority":"Можно потом","deadline":null,"repeat":"Ежедневно","repeat_time":"09:00","day_of_week":null,"confidence":"high"}',
        'Вход: "йога каждую пятницу в 18" → {"type":"task","title":"йога","category":"📚 Хобби/Учеба","priority":"Важно","deadline":null,"repeat":"Еженедельно","repeat_time":"18:00","day_of_week":"Пт","confidence":"high"}',
        'Вход: "купить корм коту" → {"type":"task","title":"купить корм коту","category":"🐾 Коты","priority":"Важно","deadline":null,"repeat":"Нет","repeat_time":null,"day_of_week":null,"confidence":"high"}',
        'Вход: "срочно позвонить врачу" → {"type":"task","title":"позвонить врачу","category":"🏥 Здоровье","priority":"Срочно","deadline":null,"repeat":"Нет","repeat_time":null,"day_of_week":null,"confidence":"high"}',
        "",
        "task_done — пользователь сообщает что уже выполнил какое-то дело:",
        '{"type":"task_done","task_hint":"ключевые слова названия задачи"}',
        "Примеры: 'сделала покупку корма' → task_done hint='покупка корм'",
        "         'позвонила в клинику' → task_done hint='позвонить клиника'",
        "         'написала отчёт' → task_done hint='написать отчёт'",
        "         'купила корм готово' → task_done hint='купить корм'",
        "         'отметь врача выполненным' → task_done hint='врач'",
        "ВАЖНО: task_done только если речь идёт о чём-то уже сделанном (прошедшее время глагола или слово 'готово'/'выполнено')",
        "",
        "task — задача на Нексус (БЕЗ слов из Арканы!):",
        '{"type":"task","title":"что сделать","category":"<из кат>","priority":"Срочно|Важно|Можно потом","deadline":"YYYY-MM-DDTHH:MM или null","reminder":"YYYY-MM-DDTHH:MM или null (только если явно указано напомни/напоминалку с отдельной датой)","repeat":"Нет|Ежедневно|Еженедельно|Ежемесячно","repeat_time":"HH:MM или null","day_of_week":"Пн|Вт|Ср|Чт|Пт|Сб|Вс или null","confidence":"high если есть дата или repeat, low иначе"}',
        "Приоритет — определяй по СМЫСЛУ задачи, всегда ставь один из трёх:",
        "  🔴 Срочно: слова 'срочно/немедленно/сейчас/asap', дедлайн сегодня, деньги (оплата, долг, счёт, квартплата), документы (налоговая, паспорт, справка, заявление)",
        "  🟡 Важно: здоровье (врач, лекарства, анализы), работа, дедлайн скоро (не сегодня), продукты, обычные покупки, рутинные дела",
        "  ⚪ Можно потом: бытовые мелочи (выкинуть, убрать, погладить кота), развлечения (сериал, кино, игра), 'когда-нибудь', 'как-нибудь'",
        "  🤖 Боты: разработка ботов, фичи, код, баги, IT. 'сделать списки покупок'→🤖 Боты (фича бота), 'пофиксить баг'→🤖 Боты, 'прикрутить голосовые'→🤖 Боты, 'добавить команду'→🤖 Боты",
        "Примеры: 'оплатить квартиру'→Срочно, 'сдать документы в налоговую'→Срочно, 'срочно позвонить маме'→Срочно, 'записаться к врачу'→Важно, 'купить сметану'→Важно, 'купить корм коту'→Важно, 'выкинуть салфетки'→Можно потом, 'погладить кота'→Можно потом, 'посмотреть сериал'→Можно потом",
        "",
        "arcana_redirect — отправить в Аркану (содержит слова из ARCANA_KEYWORDS):",
        '{"type":"arcana_redirect","text":"оригинальный текст"}',
        "",
        "arcana_clarify — уточнить у пользователя: это для Арканы или обычная задача?",
        '{"type":"arcana_clarify","text":"оригинальный текст","confidence":"low"}',
        "Примеры: 'купить свечи' (может быть и задача и для ритуала) → arcana_clarify с confidence=low",
        "memory_save — факт о человеке/предмете для долгосрочной памяти:",
        '{"type":"memory_save","text":"<оригинальный текст>"}',
        "Примеры memory_save:",
        "  'запомни что маша не ест мясо' → memory_save",
        "  'маша это моя подруга' → memory_save",
        "  'батон весит 4 кг' → memory_save",
        "  'у меня аллергия на пыль' → memory_save",
        "  'кот боится пылесоса' → memory_save",
        "  'мой день рождения 15 апреля' → memory_save",
        "  'лимит продукты 1000 в месяц' → memory_save",
        "  'лимит на кафе 5000р' → memory_save",
        "  'поставь лимит на транспорт 3000' → memory_save",
        "ПРАВИЛО memory_save: '[имя/предмет] это [описание]', '[имя] [факт]', 'у меня [факт]', 'запомни что [факт]', 'лимит [категория] [сумма]'",
        "ВАЖНО: memory_save — только если это факт/характеристика о ком-то или чём-то. Идеи и мысли → note.",
        "",
        "memory_search — поиск в долгосрочной памяти (что бот запомнил о людях/котах/предметах):",
        '{"type":"memory_search","query":"ключевые слова для поиска"}',
        "Примеры memory_search:",
        "  'что ты помнишь о маше' → {\"type\":\"memory_search\",\"query\":\"маша\"}",
        "  'напомни про котов' → {\"type\":\"memory_search\",\"query\":\"коты\"}",
        "  'что знаешь о батоне' → {\"type\":\"memory_search\",\"query\":\"батон\"}",
        "  'расскажи про алуну' → {\"type\":\"memory_search\",\"query\":\"алуна\"}",
        "ВАЖНО: memory_search — только если спрашивают о том, что БОТ запомнил. Поиск заметок/идей → note_search.",
        "",
        "note — заметка (идея, мысль, рецепт, что хочу попробовать):",
        '{"type":"note","text":"<краткий заголовок максимум 80 символов из слов пользователя, не пересказ>","tags":"<список тегов через запятую>"}',
        "Примеры note:",
        "  'запомни идею про подкаст таро' → {\"type\":\"note\",\"text\":\"идея про подкаст таро\",\"tags\":\"идея,таро\"}",
        "  'идея про ленорман расклады' → {\"type\":\"note\",\"text\":\"идея про ленорман расклады\",\"tags\":\"идея,ленорман\"}",
        "  'хочу попробовать масло розы для ритуалов' → {\"type\":\"note\",\"text\":\"попробовать масло розы для ритуалов\",\"tags\":\"рецепт,практика\"}",
        "",
        "note_search — поиск заметок по ключевым словам:",
        '{"type":"note_search","query":"ключевые слова"}',
        "Примеры note_search:",
        "  'найди заметку про таро' → {\"type\":\"note_search\",\"query\":\"таро\"}",
        "  'покажи заметки про ритуалы' → {\"type\":\"note_search\",\"query\":\"ритуал\"}",
        "  'напомни куда я хотела сходить' → {\"type\":\"note_search\",\"query\":\"места\"}",
        "  'какие идеи у меня были' → {\"type\":\"note_search\",\"query\":\"идея\"}",
        "  'что я записывала про расклады' → {\"type\":\"note_search\",\"query\":\"расклады\"}",
        "  'найди мою запись про масло розы' → {\"type\":\"note_search\",\"query\":\"масло розы\"}",
        "  'какие идеи у меня были' → {\"type\":\"note_search\",\"query\":\"идея\"}",
        "  'что я записывала' → {\"type\":\"note_search\",\"query\":\"\"}",
        "  'напомни мысли про практику' → {\"type\":\"note_search\",\"query\":\"практика\"}",
        "  'покажи все мои заметки' → {\"type\":\"note_search\",\"query\":\"\"}",
        "ВАЖНО: note_search только если явно просят найти/показать/вспомнить/искать заметку!",
        "query = самое смысловое слово или фраза из запроса (1–3 слова), или пустая строка если ищут всё",
        "",
        "edit_note — редактировать поле существующей заметки (теги):",
        '{"type":"edit_note","hint":"ключевые слова для поиска заметки или \'последняя\'","field":"tags","new_value":"новое значение"}',
        "Примеры: 'измени тег на расклады' → {\"type\":\"edit_note\",\"hint\":\"последняя\",\"field\":\"tags\",\"new_value\":\"расклады\"}",
        "         'поменяй тег заметки про таро на практика' → {\"type\":\"edit_note\",\"hint\":\"таро\",\"field\":\"tags\",\"new_value\":\"практика\"}",
        "         'переименуй тег последней заметки в расклады' → {\"type\":\"edit_note\",\"hint\":\"последняя\",\"field\":\"tags\",\"new_value\":\"расклады\"}",
        "         'обнови тег последней заметки' → edit_note",
        "ВАЖНО: edit_note только если явно упомянуто изменение тега заметки!",
        "",
        "stats — статистика / сводка / вопрос о расходах по категории:",
        '{"type":"stats","query":"<запрос>"}',
        "Примеры stats: 'сколько потратила на коты', 'скок ушло на транспорт', 'сколько потратила на котов в этом месяце',",
        "  'расходы за месяц', 'сводка', 'статистика', 'сколько потратила', 'сколько ушло на продукты'",
        "ВАЖНО: любой вопрос со словами 'сколько/скок потратила/потратил/ушло/израсходовала' → ВСЕГДА stats, НЕ task!",
        "",
    ] + LIST_HAIKU_TYPES + [
        "",
        "help:",
        '{"type":"help"}',
        "",
        "ПРАВИЛА:",
        "- ИСПРАВЛЯЙ ОПЕЧАТКИ во всех полях, включая title: 'вадмму'→'вадиму', 'молко'→'молоко'",
        "- СЛЕНГ: энергосы/энерги/сигетки/сиги/бабки/бабосы/нал — это сленг, НЕ опечатки! Оставлять как есть в title, не исправлять.",
        "- ЭНЕРГЕТИКИ/КОЛА → 🚬 Привычки: энергосы/энерги/монстр/monster/ред булл/редбулл/redbull/burn/кола/cola/pepsi/пепси/чипман/chapman/сигареты/сиги → category='🚬 Привычки'",
        "- КОТЫ/ЖИВОТНЫЕ → 🐾 Коты: погладить кота/покормить кота/кошачий корм/ветеринар/лоток/шерсть/когти/котик/кошка → category='🐾 Коты'",
        "- ЛЮДИ → 👥 Люди: написать [имя]/позвонить [имя]/встретиться с [имя]/написать маше/позвонить руслану/встретиться с аней → category='👥 Люди'",
        "- source: нал/наличные/кэш/налик → '💵 Наличные'; бартер → '🔄 Бартер'; иначе '💳 Карта'",
        "- type task/expense/income/note: если в тексте есть явное слово ('заметка', 'расход', 'доход', 'задача') - это приоритет! Даже если есть ARCANA_KEYWORDS → определить точный тип БЕЗ redirection",
        "- type task/expense/income: если есть ключевое слово из ARCANA_KEYWORDS (ритуал, практика, расходники, клиент, сеанс...) И нет явного типа → НЕ task/expense/income, а arcana_redirect!",
        "- arcana_redirect: явно для Арканы (слова типа 'ритуал', 'практика', 'сеанс', 'гримуар', 'таро') → сразу редирект без вопросов",
        "- arcana_clarify: подозрительные слова (свечи, травы, масла, пентаграмма), которые могут быть и обычной задачей и для ритуала → спросить пользователя",
        "- type task: только для Нексуса. Обычные задачи/дела: глаголы действия (купить, позвонить, написать, отправить, запросить, посетить, встретиться, забрать, принести, исправить, отремонтировать и т.д.)",
        "- НАМЕРЕНИЕ vs ФАКТ: различай 'хочу сделать' (task) и 'уже потратила' (expense):",
        "  ЗАДАЧИ (намерение): кинуть/закинуть/пополнить/оплатить/перевести/купить/надо заплатить → task",
        "  РАСХОДЫ (факт): потратила/заплатила/стоило/вышло/обошлось/[сумма][описание] без глагола → expense",
        "  Примеры: 'кинуть 5$ на OpenAI'→task, 'оплатить интернет'→task, 'пополнить баланс'→task",
        "  Примеры: '500р такси'→expense, 'заплатила 3000 за ногти'→expense, 'потратила 500 на еду'→expense",
        "- task_done: глагол прошедшего времени (сделала/выполнила/купила/написала/позвонила/закончила/отправила) БЕЗ суммы → task_done, НЕ task!",
        "- task_done vs expense: 'купила корм' БЕЗ суммы → task_done; 'купила корм 500₽' → expense",
        "- ПРИОРИТЕТ stats: фразы 'сколько потратила/потратил', 'скок потратила', 'сколько ушло', 'сколько израсходовала' → ВСЕГДА stats, даже если есть категория или слово 'на'! НЕ task, НЕ expense.",
        "- ВАЖНО: Короткие глаголы ВСЕГДА задача (type=task), даже без деталей! Примеры: 'позвонить'→task, 'написать'→task, 'купить'→task (confidence=low если нет деталей)",
        "- Если просто глагол БЕЗ объекта (типа 'написать') → task с title=исходный глагол, confidence=low",
        "- type expense/income: если 'доход'/'пришла'/'зарплата'/'поступление' → income, confidence=high; 'расход'/'потрачено' → expense, confidence=high",
        "- category для income: ТОЛЬКО если явно 'зарплата' → '💰 Зарплата'; иначе '💳 Прочее' (даже если просто 'доход')",
        "- confidence: high если есть явное слово (доход/расход/бартер); low если только сумма+имя (спросить потом)",
        "- title: ВСЕГДА объединяй всё остальное в одну строку. Пример: '450 такси карта вадмму' → title='такси вадиму' (исправленная опечатка)",
        "- priority: срочно/немедленно/деньги/документы/дедлайн сегодня → 'Срочно'; здоровье/работа/дедлайн скоро → 'Важно'; мелочь/развлечение → 'Можно потом'",
        "- deadline: 'дедлайн сегодня' → " + today + ". 'до пятницы/среды/понедельника' → ISO дата ближайшего такого дня (если сегодня этот день → следующая неделя). 'до пятницы' ≠ 'каждую пятницу'. Пример: сегодня=" + today + " " + today_dow + " → 'до пятницы'=" + _nearest_weekday_iso(4, tz_offset) + ", 'до понедельника'=" + _nearest_weekday_iso(0, tz_offset),
        "- deadline с временем: парсить 'завтра в 15:00' → YYYY-MM-DDTHH:MM; 'в 14:30 без даты' → сегодня+время; 'завтра' БЕЗ времени → YYYY-MM-DD (НЕ добавлять T09:00!)",
        "- КРИТИЧНО — ОДНОРАЗОВАЯ vs ПОВТОРЯЮЩАЯСЯ: слово 'каждый/каждое/каждую/ежедневно/еженедельно/ежемесячно' = ПОВТОРЯЮЩАЯСЯ. БЕЗ этих слов = ОДНОРАЗОВАЯ. Примеры:",
        "-   'напомни в воскресенье в 19' → ОДНОРАЗОВАЯ: repeat='Нет', deadline=ближайшее вс, reminder_time не ставить (обработается позже)",
        "-   'напомни в пятницу' → ОДНОРАЗОВАЯ: repeat='Нет', deadline=ближайшая пт",
        "-   'напоминай каждое воскресенье в 19' → ПОВТОРЯЮЩАЯСЯ: repeat='Еженедельно', day_of_week='Вс', repeat_time='19:00'",
        "-   'каждую пятницу йога' → ПОВТОРЯЮЩАЯСЯ: repeat='Еженедельно', day_of_week='Пт'",
        "- ПОВТОРЯЮЩИЕСЯ ЗАДАЧИ: если есть 'каждый день/неделю/месяц' → repeat != 'Нет', deadline = null (НЕ ставить текущую дату!), repeat_time = указанное время",
        "- repeat: 'каждый день/ежедневно/каждое утро/каждый вечер/каждую ночь' → 'Ежедневно'; 'каждую [день недели]/каждый [день недели]' → 'Еженедельно'; 'раз в месяц/ежемесячно/каждый месяц' → 'Ежемесячно'; 'каждые N дней/раз в N дней' → 'Ежедневно' (интервал кодируется в repeat_time); иначе → 'Нет'",
        "- day_of_week: только если repeat='Еженедельно': пн/понедельник→'Пн'; вт/вторник→'Вт'; ср/среда→'Ср'; чт/четверг→'Чт'; пт/пятница→'Пт'; сб/суббота→'Сб'; вс/воскресенье→'Вс'",
        "- repeat_time: 'каждый день в 10' → '10:00'; 'каждое утро' → '09:00'; 'каждый вечер' → '20:00'; 'каждую ночь' → '23:00'; если время не указано → null",
        "- КАЖДЫЕ N ДНЕЙ: 'каждые 2/3/5/N дней' или 'раз в N дней' → repeat='Ежедневно', repeat_time='HH:MM|every_Nd' (N — число). Парсить числительные: два→2, три→3, четыре→4, пять→5, шесть→6, семь→7, десять→10. Если время не указано → '09:00|every_Nd'",
    ] + ([night_rule] if night_rule else []) + [
        "- к/тыс в суммах: 35к = 35000",
        "- tags: выбирай на основе содержания заметки из [практика, таро, ленорман, ритуал, идея, рецепт, здоровье, финансы, мысль]",
        "- тег 'таро' ТОЛЬКО если в тексте явно упоминается слово 'таро' или 'карты таро'. Ленорман — отдельная карточная система, тег 'ленорман' а не 'таро'",
        "- теги должны отражать реальные слова/смысл текста, не добавляй теги которых нет в содержании",
        '- неизвестная строка -> {"type":"unknown"}',
        "",
        "ARCANA_KEYWORDS (→ arcana_redirect): " + ", ".join(sorted(ARCANA_KEYWORDS)),
        "",
        "ПРИМЕР:",
        "  Ввод (4 строки):",
        "    450 такси карта вадмму",
        "    доход 50к нал",
        "    пришла зарплата 80к",
        "    12к котам",
        "  Ответ JSON:",
        '  [{"type":"expense","amount":450,"title":"такси вадиму","category":"🚕 Транспорт","source":"💳 Карта","confidence":"high"},',
        '   {"type":"income","amount":50000,"title":"доход","category":"💳 Прочее","source":"💵 Наличные","confidence":"high"},',
        '   {"type":"income","amount":80000,"title":"зарплата","category":"💰 Зарплата","source":"💳 Карта","confidence":"high"},',
        '   {"type":"expense","amount":12000,"title":"котам","category":"🐾 Коты","source":"💳 Карта","confidence":"low"}]',
        "",
        "ПРИМЕР STATS:",
        "  Ввод: 'скок потратила на котов в этом месяце'",
        '  Ответ: {"type":"stats","query":"скок потратила на котов в этом месяце"}',
        "",
        "ПРИМЕР ОДНОРАЗОВЫЕ (БЕЗ 'каждый'):",
        "  Ввод: 'напомни в воскресенье в 19 часов зарядить наушники'",
        '  Ответ: {"type":"task","title":"зарядить наушники","category":"🏠 Жилье","priority":"Можно потом","deadline":"' + _next_weekday_iso("Вс") + '","repeat":"Нет","repeat_time":null,"day_of_week":null,"confidence":"high"}',
        "  Ввод: 'напомни в пятницу позвонить врачу'",
        '  Ответ: {"type":"task","title":"позвонить врачу","category":"🏥 Здоровье","priority":"Важно","deadline":"' + _next_weekday_iso("Пт") + '","repeat":"Нет","repeat_time":null,"day_of_week":null,"confidence":"high"}',
        "",
        "ПРИМЕР ПОВТОРОВ (ЕСТЬ 'каждый/каждую/каждое'):",
        "  Ввод: 'погладить кота каждый день в 9'",
        '  Ответ: {"type":"task","title":"погладить кота","category":"🐾 Коты","priority":"Можно потом","deadline":null,"repeat":"Ежедневно","repeat_time":"09:00","day_of_week":null,"confidence":"high"}',
        "  Ввод: 'йога каждую пятницу в 18'",
        '  Ответ: {"type":"task","title":"йога","category":"📚 Хобби/Учеба","priority":"Важно","deadline":null,"repeat":"Еженедельно","repeat_time":"18:00","day_of_week":"Пт","confidence":"high"}',
        "  Ввод: 'каждое утро пить воду'",
        '  Ответ: {"type":"task","title":"пить воду","category":"🏥 Здоровье","priority":"Важно","deadline":null,"repeat":"Ежедневно","repeat_time":"09:00","day_of_week":null,"confidence":"low"}',
        "  Ввод: 'платить за аренду раз в месяц'",
        '  Ответ: {"type":"task","title":"платить за аренду","category":"🏠 Жилье","priority":"Срочно","deadline":null,"repeat":"Ежемесячно","repeat_time":null,"day_of_week":null,"confidence":"low"}',
        "",
        "ПРИМЕР КАЖДЫЕ N ДНЕЙ:",
        "  Ввод: 'менять воду коту каждые два дня в 17'",
        '  Ответ: {"type":"task","title":"менять воду коту","category":"🐾 Коты","priority":"Можно потом","deadline":null,"repeat":"Ежедневно","repeat_time":"17:00|every_2d","day_of_week":null,"confidence":"high"}',
        "  Ввод: 'каждые 3 дня чистить лоток'",
        '  Ответ: {"type":"task","title":"чистить лоток","category":"🐾 Коты","priority":"Можно потом","deadline":null,"repeat":"Ежедневно","repeat_time":"09:00|every_3d","day_of_week":null,"confidence":"high"}',
        "  Ввод: 'раз в 5 дней напомни полить цветы в 10'",
        '  Ответ: {"type":"task","title":"полить цветы","category":"🏠 Жилье","priority":"Можно потом","deadline":null,"repeat":"Ежедневно","repeat_time":"10:00|every_5d","day_of_week":null,"confidence":"high"}',
        "",
        "ПРИМЕР ARCANA_REDIRECT:",
        "  Ввод: 'провести ритуал защиты'",
        '  Ответ: {"type":"arcana_redirect","text":"провести ритуал защиты"}',
        "",
        "ПРИМЕР ARCANA_CLARIFY:",
        "  Ввод: 'купить свечи'",
        '  Ответ: {"type":"arcana_clarify","text":"купить свечи","confidence":"low"}',
        "  (Пользователь выберет: это для Арканы или просто задача?)",
    ])


_EDIT_RE = re.compile(
    r"\b(поменяй|измени|обнови|исправь|смени|замени|измените|обновите|исправьте|сменить|изменить|поменять)\b"
    r".{0,50}\b(категорию|категория|приоритет|название|заголовок|дедлайн|имя|источник|статус)\b"
    r"|"
    r"\b(категорию|приоритет|название|дедлайн|источник)\b.{0,30}\b(поменяй|измени|обнови|исправь|смени)\b",
    re.IGNORECASE,
)

_RENAME_RE = re.compile(
    r"\bпереименуй\b.{0,60}\bв\b",
    re.IGNORECASE,
)

_EDIT_NOTE_RE = re.compile(
    r"(измени|исправь|поменяй|обнови|смени|замени|переименуй)"
    r"\s+(тег|теги|метку|метки|категорию)\s+(заметки|заметку|последней\s+заметки)",
    re.IGNORECASE,
)

_CANCEL_RE = re.compile(
    r"\b(отмени|отменить|отмена)\b.{0,50}\b(задач\w*)\b"
    r"|\b(удали|убери)\s+задач\w*",
    re.IGNORECASE,
)

_DONE_RE = re.compile(
    r"\b(сделал[аи]?\b|выполнил[аи]?\b|закончил[аи]?\b|завершил[аи]?\b|"
    r"позвонил[аи]\b|написал[аи]\b|отправил[аи]\b|забрал[аи]\b|"
    r"готово\b|готова\b|выполнено\b|сделано\b|"
    r"отметь\s+\w+\s+выполненным|отметь\s+выполненным|уже\s+сделал[аи]?\b)",
    re.IGNORECASE,
)

# Тексты начинающиеся с "запомни" — это память (memory_save), НЕ task_done и НЕ note
_ZAPOMNI_RE = re.compile(r"^\s*запомни\b", re.IGNORECASE)

# Явные команды сохранения в память
_MEMORY_SAVE_RE = re.compile(
    r"^\s*запомни\b"
    r"|^\s*сохрани\s+в\s+памяти?\b"
    r"|^\s*запиши\s+в\s+памяти?\b"
    r"|^\s*лимит\b"
    r"|^\s*поставь\s+лимит\b"
    r"|^\s*установи\s+лимит\b"
    r"|^\s*обязательн\w*\s+расход\b"
    r"|^\s*цель\s+\S+"
    r"|^\s*долг\s+\S+"
    r"|^\s*убери\s+обязательн"
    r"|^\s*измени\s+обязательн",
    re.IGNORECASE,
)

# Показать бюджет текстовым триггером
_BUDGET_RE = re.compile(
    r"^\s*(покажи\s+бюджет|сколько\s+могу\s+тратить|сколько\s+свободных"
    r"|бюджет\s+на\s+месяц|мой\s+бюджет|свободные\s+деньги)\s*$",
    re.IGNORECASE,
)

# Budget v2: управление долгами
_DEBT_CMD_RE = re.compile(
    r"(?:закрыла?\s+долг|новый\s+долг|отдала?\s.*долг|погасила?)",
    re.IGNORECASE,
)

# Budget v2: управление целями
_GOAL_CMD_RE = re.compile(
    r"(?:новая\s+цель|убери\s+цель|достигла?\s+цель|купила?\s.*цель)",
    re.IGNORECASE,
)

# Budget v2: ручной лимит ("лимит привычки 15к", "лимит на кафе 10000")
_LIMIT_OVERRIDE_RE = re.compile(
    r"лимит\s+(?:на\s+)?(\w+)\s+(\d+[кk]?\d*)",
    re.IGNORECASE,
)

# Деактивация записи памяти: "неактуально", "неактуально 1", "неактуально маша"
_DEACTIVATE_RE = re.compile(r"^\s*неактуально\b", re.IGNORECASE)

# Удаление заметки из дайджеста: "удали заметку про расходники", "удали все заметки"
_NOTE_DELETE_RE = re.compile(
    r"^\s*удали\s+(все\s+)?заметк\w+(\s+про|\s+по|\s+о)?\s*(.+)?$",
    re.IGNORECASE,
)

# Удаление из памяти: "удали из памяти ...", "забудь про ...", "убери запись ..."
_MEMORY_DELETE_RE = re.compile(
    r"^\s*(удали|забудь|удалить|стёр|убери)\s+(из\s+памяти|из\s+памят\w+|факт|запись)?",
    re.IGNORECASE,
)

# "купить/купи X" без явной суммы → задача, не финансы
_BUY_TASK_RE = re.compile(r"^\s*(купить|купи)\b", re.IGNORECASE)
_CURRENCY_RE = re.compile(r"\d+\s*(₽|руб\.?|р\b)", re.IGNORECASE)

# Поиск по долгосрочной памяти (НЕ заметки)
_MEMORY_SEARCH_RE = re.compile(
    r"(что\s+(ты\s+)?помнишь|что\s+знаешь\s+о|расскажи\s+про"
    r"|напомни\s+про|покажи\s+памят|покажи\s+что\s+помнишь"
    r"|что\s+помнишь\s+о|напомни\s+о|вспомни\s+про|вспомни\s+о)",
    re.IGNORECASE,
)

_TASK_CATS = ["🐾 Коты", "🏠 Жилье", "🚬 Привычки", "🍜 Продукты",
              "🍱 Кафе/Доставка", "🚕 Транспорт", "💅 Бьюти", "👗 Гардероб",
              "💻 Подписки", "🏥 Здоровье", "📚 Хобби/Учеба", "🤖 Боты", "💳 Прочее"]

_CATEGORY_SYSTEM = (
    "Определи категорию задачи из списка ниже. "
    "Отвечай ТОЛЬКО одним значением из списка, без пояснений.\n"
    "Категории: " + ", ".join(_TASK_CATS) + "\n"
    "Примеры:\n"
    "  'купить royal canin' → 🐾 Коты\n"
    "  'купить хлеб молоко' → 🍜 Продукты\n"
    "  'купить шампунь' → 💅 Бьюти\n"
    "  'купить корм коту' → 🐾 Коты\n"
    "  'купить кофе' → 🍜 Продукты\n"
    "  'купить кроссовки' → 👗 Гардероб\n"
    "  'сделать списки покупок' → 🤖 Боты\n"
    "  'пофиксить баг с категориями' → 🤖 Боты\n"
    "  'прикрутить голосовые' → 🤖 Боты"
)


async def _haiku_task_category(title: str) -> str:
    """Определить категорию задачи через Haiku. Fallback — 💳 Прочее."""
    try:
        raw = await ask_claude(
            title,
            system=_CATEGORY_SYSTEM,
            max_tokens=20,
            model="claude-haiku-4-5-20251001",
        )
        raw = raw.strip()
        for cat in _TASK_CATS:
            if cat in raw or cat.split(" ", 1)[-1].lower() in raw.lower():
                return cat
    except Exception as e:
        logger.error("_haiku_task_category: %s", e)
    return "💳 Прочее"

_TZ_RE = re.compile(
    r"(utc\s*[+-]\d+"
    r"|мой\s+часовой\s+пояс|часовой\s+пояс|мой\s+пояс|timezone"
    r"|\bживу\s+в\s+\w+"
    r"|\bя\s+в\s+\w+"
    r"|\bнахожусь\s+в\s+\w+"
    r"|\bпереезжаю\s+в\s+\w+"
    r"|\bпереехал[аи]?\s+в\s+\w+"
    r"|\bсейчас\s+в\s+\w+"
    r"|\bу\s+меня\s+сейчас\s+\d{1,2}:\d{2}"
    r"|\bу\s+меня\s+\d{1,2}:\d{2}"
    r")",
    re.IGNORECASE,
)

# Слова-триггеры задачи — если они есть, _TZ_RE не должен срабатывать
_TASK_KEYWORDS_RE = re.compile(
    r"\b(напомни|напоминай|напомнить|напоминание|сделай|сделать|купи|купить|"
    r"позвони|позвонить|закинь|запиши|поставь|нужно\s+сделать|надо\s+сделать)\b",
    re.IGNORECASE,
)

_STATS_RE = re.compile(
    r"(скол?ько|скок|сколько)\s+(потратил[аи]?|ушло|израсходовал[аи]?|трачу|потрачено)"
    r"|расходы\s+за\s+(месяц|неделю|период|март|апрел|май|июн|июл|август|сентябр|октябр|ноябр|декабр|январ|феврал)"
    r"|(финансовая?\s+)?сводка"
    r"|статистика\s+(за|расходов|доходов)",
    re.IGNORECASE,
)


_EDIT_PARSE_SYSTEM = (
    "Извлеки параметры редактирования записи. Если несколько изменений — верни все в списке edits. Ответь ТОЛЬКО JSON без markdown:\n"
    '{"type":"edit_record","record_type":"task","record_hint":"ключевые слова для поиска","edits":[{"field":"category|priority|title|deadline|reminder|status","new_value":"новое значение"}]}\n'
    "\nПравила:\n"
    "- record_type: 'task' если о задаче, 'finance' если о финансовой записи\n"
    "- field: 'category' для категории; 'priority' для приоритета (Срочно/Важно/Можно потом); 'title' или 'name' для переименования; 'deadline' для дедлайна; 'reminder' для напоминания (напоминалку/напомни/напоминание); 'status' для статуса (Not started/In progress/Done/Archived)\n"
    "- record_hint: фраза для поиска записи (название задачи/финансовой операции), пустая строка если не указано\n"
    "- edits: список всех изменений (одно или несколько)\n"
    "\nПримеры:\n"
    "'поменяй категорию задачи купить корм на Продукты' → record_hint='купить корм', edits=[{\"field\":\"category\",\"new_value\":\"Продукты\"}]\n"
    "'переименуй задачу купить корм в купить корм котам' → record_hint='купить корм', edits=[{\"field\":\"title\",\"new_value\":\"купить корм котам\"}]\n"
    "'смени приоритет купить молоко на срочно' → record_hint='купить молоко', edits=[{\"field\":\"priority\",\"new_value\":\"Срочно\"}]\n"
    "'поставь статус в процессе для купить корм' → record_hint='купить корм', edits=[{\"field\":\"status\",\"new_value\":\"In progress\"}]\n"
    "'измени название на Икеа и категорию на Хобби' → record_hint='', edits=[{\"field\":\"title\",\"new_value\":\"Икеа\"},{\"field\":\"category\",\"new_value\":\"Хобби\"}]\n"
    "'поменяй категорию на привычки и источник на нал' → record_hint='', edits=[{\"field\":\"category\",\"new_value\":\"привычки\"},{\"field\":\"source\",\"new_value\":\"нал\"}]\n"
    "'поставь дедлайн 15 мая и напоминалку 1 мая для задачи гардероб' → record_hint='гардероб', edits=[{\"field\":\"deadline\",\"new_value\":\"15 мая\"},{\"field\":\"reminder\",\"new_value\":\"1 мая\"}]\n"
)


async def _parse_edit_record(text: str) -> dict:
    """Распарсить запрос на редактирование записи. Всегда возвращает edits-список."""
    raw = await ask_claude(text, system=_EDIT_PARSE_SYSTEM, max_tokens=300, model="claude-haiku-4-5-20251001")
    try:
        raw = raw.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
        data = json.loads(raw)
        data["type"] = "edit_record"
        # Нормализация: если Claude вернул старый формат field/new_value — конвертируем в edits список
        if "edits" not in data and "field" in data:
            data["edits"] = [{"field": data.pop("field"), "new_value": data.pop("new_value", "")}]
        elif "edits" not in data:
            data["edits"] = []
        return data
    except Exception:
        return {"type": "edit_record", "record_hint": text, "edits": [{"field": "unknown", "new_value": ""}]}


async def classify(text: str, tz_offset: int = 3) -> list[dict]:
    """Классифицировать текст через Claude."""
    logger.info("classify: input text=%r tz_offset=%d", text[:100], tz_offset)

    # ПЕРВЫЙ pre-фильтр: деактивация записи памяти ("неактуально", "неактуально 1")
    if _DEACTIVATE_RE.match(text):
        hint = re.sub(r"^\s*неактуально\s*", "", text, flags=re.IGNORECASE).strip()
        logger.info("classify: memory_deactivate matched, hint=%r", hint)
        return [{"type": "memory_deactivate", "hint": hint, "text": text}]

    # Быстрый pre-фильтр: удаление заметок ("удали заметку про X", "удали все заметки")
    m = _NOTE_DELETE_RE.match(text)
    if m:
        delete_all = bool(m.group(1))
        hint = (m.group(3) or "").strip()
        hint = re.sub(r"^\s*(про|по|о|об)\s+", "", hint, flags=re.IGNORECASE).strip()
        logger.info("classify: note_delete matched, delete_all=%s hint=%r", delete_all, hint)
        return [{"type": "note_delete", "hint": hint, "delete_all": delete_all, "text": text}]

    # Быстрый pre-фильтр: удаление из памяти ("удали из памяти ...", "забудь про ...")
    m = _MEMORY_DELETE_RE.match(text)
    if m:
        hint = text[m.end():].strip()
        hint = re.sub(r"^\s*(про|о|об)\s+", "", hint, flags=re.IGNORECASE).strip()
        logger.info("classify: memory_delete matched, hint=%r", hint)
        return [{"type": "memory_delete", "hint": hint, "text": text}]

    # Budget v2: команды долгов → budget handler (ПЕРЕД budget и memory_save!)
    if _DEBT_CMD_RE.search(text):
        logger.info("classify: debt_command matched")
        return [{"type": "debt_command", "text": text}]

    # Budget v2: команды целей → budget handler
    if _GOAL_CMD_RE.search(text):
        logger.info("classify: goal_command matched")
        return [{"type": "goal_command", "text": text}]

    # Budget v2: ручной лимит → budget handler (ПЕРЕД memory_save "лимит...")
    m = _LIMIT_OVERRIDE_RE.search(text)
    if m:
        logger.info("classify: limit_override matched cat=%s amt=%s", m.group(1), m.group(2))
        return [{"type": "limit_override", "text": text, "category": m.group(1), "amount": m.group(2)}]

    # Быстрый pre-фильтр: показать бюджет
    if _BUDGET_RE.match(text):
        logger.info("classify: budget pattern matched")
        return [{"type": "budget", "text": text}]

    # Быстрый pre-фильтр: память ("запомни ...")
    logger.info("classify: checking memory_save pre-filter for: '%s'", text[:50])
    logger.info("classify: memory_save match result: %s", bool(_MEMORY_SAVE_RE.match(text)))
    if _MEMORY_SAVE_RE.match(text):
        logger.info("classify: memory_save pattern matched")
        return [{"type": "memory_save", "text": text}]

    # Быстрый pre-фильтр: редактирование тега заметки
    if _EDIT_NOTE_RE.search(text):
        logger.info("classify: edit_note pattern matched")
        # Извлечь new_value из "на X" / "в X"
        new_val_match = re.search(r"\b(?:на|в)\s+(\S+)", text, re.IGNORECASE)
        new_value = new_val_match.group(1).rstrip(".,!?") if new_val_match else ""
        # Попытаться найти hint (название заметки) между "заметки" и "тег" / после "тег"
        hint_match = re.search(r"\bзаметки\s+про\s+(\w+)\b|\bзаметки\s+(\w+)\b", text, re.IGNORECASE)
        if hint_match:
            hint = hint_match.group(1) or hint_match.group(2) or "последняя"
        else:
            hint = "последняя"
        return [{"type": "edit_note", "hint": hint, "field": "tags", "new_value": new_value}]

    # Быстрый pre-фильтр: изменение записи ("поменяй категорию X на Y", "переименуй X в Y")
    if _EDIT_RE.search(text) or _RENAME_RE.search(text):
        logger.info("classify: edit_record pattern matched")
        parsed = await _parse_edit_record(text)
        return [parsed]

    # Быстрый pre-фильтр: отмена задачи ("отмени задачу X", "удали задачу X")
    if _CANCEL_RE.search(text):
        logger.info("classify: task_cancel pattern matched")
        return [{"type": "task_cancel", "task_hint": text}]

    # Быстрый pre-фильтр: задача выполнена ("сделала X", "X готово")
    # Исключение: "запомни ..." → это заметка, пропустить к Claude
    # Исключение: "купила X 89р" (с ценой) → это list_done, пропустить к списковым фильтрам
    if _DONE_RE.search(text) and not _ZAPOMNI_RE.search(text) and not _CURRENCY_RE.search(text):
        logger.info("classify: task_done pattern matched")
        return [{"type": "task_done", "task_hint": text}]

    # Быстрый pre-фильтр: timezone — только если нет слов-триггеров задачи
    if _TZ_RE.search(text) and not _TASK_KEYWORDS_RE.search(text):
        logger.info("classify: timezone pattern matched")
        return [{"type": "timezone_update", "text": text}]

    # Быстрый pre-фильтр: stats-запросы не отдаём Claude — он их путает с task
    if _STATS_RE.search(text):
        logger.info("classify: stats pattern matched, bypassing Claude")
        return [{"type": "stats", "query": text}]

    # ── Списки pre-filters (ПОРЯДОК ВАЖЕН: list_done ПЕРЕД list_buy!) ────────
    # "купила молоко 89р" → list_done (НЕ list_buy!)
    if _LIST_DONE_RE.search(text):
        logger.info("classify: list_done pattern matched")
        return [{"type": "list_done", "text": text}]

    # "купить молоко, яйца" → list_buy (только если НЕТ цены — иначе это list_done)
    if _LIST_BUY_RE.search(text) and not _CURRENCY_RE.search(text):
        logger.info("classify: list_buy pattern matched")
        return [{"type": "list_buy", "text": text}]

    # "разбей задачу X на подзадачи" → list_subtask (ПЕРЕД list_check!)
    if _SUBTASK_RE.search(text):
        logger.info("classify: list_subtask pattern matched")
        return [{"type": "list_subtask", "text": text}]

    # "список: паспорт, зарядка" / "чеклист" → list_check
    if _LIST_CHECK_RE.search(text):
        logger.info("classify: list_check pattern matched")
        return [{"type": "list_check", "text": text}]

    # "закончился парацетамол" / "осталась 1 пачка" → list_inventory_update
    if _LIST_INV_UPDATE_RE.search(text):
        logger.info("classify: list_inventory_update pattern matched")
        return [{"type": "list_inventory_update", "text": text}]

    # "есть ибупрофен?", "дома есть ибупрофен?" → list_inventory_search (ПЕРЕД ADD!)
    if _LIST_INV_SEARCH_RE.search(text):
        logger.info("classify: list_inventory_search pattern matched")
        return [{"type": "list_inventory_search", "text": text}]

    # "дома есть: парацетамол" / "добавь в инвентарь" → list_inventory_add
    if _LIST_INV_ADD_RE.search(text):
        logger.info("classify: list_inventory_add pattern matched")
        return [{"type": "list_inventory_add", "text": text}]

    # Быстрый pre-фильтр: поиск по долгосрочной памяти (до Claude, чтобы не попал в note_search)
    if _MEMORY_SEARCH_RE.search(text):
        hint = re.sub(
            r"(что\s+(ты\s+)?помнишь\s*(о|про)?|что\s+знаешь\s+о|расскажи\s+про"
            r"|напомни\s+(про|о)|покажи\s+памят\w*|покажи\s+что\s+помнишь"
            r"|что\s+помнишь\s+о|вспомни\s+(про|о))\s*",
            "", text, flags=re.IGNORECASE,
        ).strip()
        logger.info("classify: memory_search matched, hint=%r", hint)
        return [{"type": "memory_search", "query": hint, "text": text}]

    # Быстрый pre-фильтр: "купить/купи X" без явной суммы → задача, не финансы
    if _BUY_TASK_RE.match(text) and not _CURRENCY_RE.search(text):
        logger.info("classify: buy_task matched (no currency)")
        category = await _haiku_task_category(text)
        logger.info("classify: buy_task category=%r", category)
        return [{"type": "task", "title": text.strip(), "category": category,
                 "priority": "Важно", "deadline": None, "repeat": "Нет",
                 "repeat_time": None, "day_of_week": None, "confidence": "low"}]

    # Guard: если text выглядит как разговорный ответ Claude (утечка из spell correction) —
    # не отправлять обратно в Claude, вернуть unknown сразу
    _LEAKED_PREFIXES = (
        "я не имею", "я не могу", "извините", "к сожалению",
        "не имею доступа", "у меня нет доступа", "как языковая модель",
        "как ии", "i don't have access", "i cannot",
    )
    if any(text.lower().startswith(p) for p in _LEAKED_PREFIXES):
        logger.warning("classify: detected leaked Claude response as input, returning unknown. text=%r", text[:80])
        return [{"type": "unknown"}]

    raw = await ask_claude(text, system=build_system(tz_offset), max_tokens=1024)
    global _classify_last_raw
    _classify_last_raw = raw
    
    try:
        raw = raw.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
        items = json.loads(raw)
        if not isinstance(items, list):
            items = [items]
        logger.info("classify: parsed %d items: %s", len(items), [i.get("type") for i in items])
        return items
    except (json.JSONDecodeError, ValueError) as e:
        logger.warning("classify: bad JSON for %r → %r", text, raw)
        await log_error(text, "parse_error", raw, str(e), error_code="–")
        return [{"type": "parse_error"}]


async def process_item(data: Dict[str, Any], original_text: str, msg, clarify: dict, user_notion_id: str = "") -> str:
    """Обработка классифицированного элемента."""
    kind = data.get("type", "unknown")
    logger.info("process_item: type=%r data=%s", kind, data)

    # EDIT RECORD
    if kind == "edit_record":
        from nexus.handlers.tasks import handle_edit_record
        await handle_edit_record(
            msg,
            record_hint=data.get("record_hint", original_text),
            edits=data.get("edits"),
            field=data.get("field", ""),
            new_value=data.get("new_value", ""),
            record_type=data.get("record_type", "task"),
            user_notion_id=user_notion_id,
        )
        return ""

    # TASK CANCEL
    if kind == "task_cancel":
        from nexus.handlers.tasks import handle_task_cancel
        await handle_task_cancel(msg, data.get("task_hint", original_text), user_notion_id=user_notion_id)
        return ""

    # TASK DONE
    if kind == "task_done":
        await react(msg, "🔥")
        from nexus.handlers.tasks import handle_task_done
        await handle_task_done(msg, data.get("task_hint", original_text), user_notion_id=user_notion_id)
        return ""

    # TIMEZONE UPDATE
    if kind == "timezone_update":
        from nexus.handlers.tasks import _update_user_tz
        await _update_user_tz(msg, data.get("text", original_text))
        try:
            from core.memory import save_memory
            await save_memory(msg, original_text, user_notion_id, "☀️ Nexus")
        except Exception:
            pass
        return ""

    # БЮДЖЕТ — v2: всегда Sonnet
    if kind == "budget":
        from nexus.handlers.finance import start_budget_analysis
        await start_budget_analysis(msg, user_notion_id)
        return ""

    # ДОЛГИ — v2
    if kind == "debt_command":
        from nexus.handlers.finance import handle_debt_command
        await handle_debt_command(msg, user_notion_id)
        return ""

    # ЦЕЛИ — v2
    if kind == "goal_command":
        from nexus.handlers.finance import handle_goal_command
        await handle_goal_command(msg, user_notion_id)
        return ""

    # РУЧНОЙ ЛИМИТ — v2
    if kind == "limit_override":
        from nexus.handlers.finance import handle_limit_override
        await handle_limit_override(msg, data.get("category", ""), data.get("amount", "0"), user_notion_id)
        return ""

    # ПАМЯТЬ (memory_save)
    if kind == "memory_save":
        await react(msg, "💅")
        from nexus.handlers.memory import handle_memory_save
        data["text"] = data.get("text", original_text)
        await handle_memory_save(msg, data, user_notion_id=user_notion_id)
        return ""

    # ПАМЯТЬ (memory_search)
    if kind == "memory_search":
        from nexus.handlers.memory import handle_memory_search
        await handle_memory_search(msg, data, user_notion_id=user_notion_id)
        return ""

    # ПАМЯТЬ (memory_deactivate)
    if kind == "memory_deactivate":
        from nexus.handlers.memory import handle_memory_deactivate
        await handle_memory_deactivate(msg, data, user_notion_id=user_notion_id)
        return ""

    # ПАМЯТЬ (memory_delete)
    if kind == "memory_delete":
        from nexus.handlers.memory import handle_memory_delete
        await handle_memory_delete(msg, data, user_notion_id=user_notion_id)
        return ""

    # ЗАМЕТКИ (note_delete из дайджеста)
    if kind == "note_delete":
        from nexus.handlers.notes import handle_note_delete
        await handle_note_delete(msg, data, user_notion_id=user_notion_id)
        return ""

    # ── СПИСКИ ────────────────────────────────────────────────────────────────
    if kind == "list_buy":
        await react(msg, "🫡")
        from nexus.handlers.lists import handle_list_buy
        await handle_list_buy(msg, data, user_notion_id=user_notion_id)
        return ""

    if kind in ("list_done", "list_done_bulk"):
        await react(msg, "💸")
        from nexus.handlers.lists import handle_list_done
        await handle_list_done(msg, data, user_notion_id=user_notion_id)
        return ""

    if kind == "list_check":
        await react(msg, "🫡")
        from nexus.handlers.lists import handle_list_check
        await handle_list_check(msg, data, user_notion_id=user_notion_id)
        return ""

    if kind == "list_subtask":
        await react(msg, "🫡")
        from nexus.handlers.lists import handle_list_subtask
        await handle_list_subtask(msg, data, user_notion_id=user_notion_id)
        return ""

    if kind == "list_inventory_add":
        await react(msg, "🫡")
        from nexus.handlers.lists import handle_list_inv_add
        await handle_list_inv_add(msg, data, user_notion_id=user_notion_id)
        return ""

    if kind == "list_inventory_search":
        from nexus.handlers.lists import handle_list_inv_search
        await handle_list_inv_search(msg, data, user_notion_id=user_notion_id)
        return ""

    if kind == "list_inventory_update":
        await react(msg, "🫡")
        from nexus.handlers.lists import handle_list_inv_update
        await handle_list_inv_update(msg, data, user_notion_id=user_notion_id)
        return ""

    if kind == "unknown":
        return f"unknown_clarify:{original_text}"
    
    if kind == "parse_error":
        logged = await log_error(original_text, "parse_error", _classify_last_raw, error_code="–")
        notion_status = "записано в ⚠️Ошибки" if logged else "лог недоступен"
        raw_preview = _classify_last_raw[:200] if _classify_last_raw else "—"
        return f"❌ Не понял: <code>{raw_preview}</code>\n{notion_status}"

    # АРКАНА РЕДИРЕКТ
    if kind == "arcana_redirect":
        return ("🔮 <b>Это работа для Арканы!</b>\n\n"
                "Перейди в <a href=\"https://t.me/arcana_kailark_bot\">🌒 Arcana</a> и отправь туда:\n"
                f"<code>{original_text[:100]}</code>\n\n"
                "Там я помогу с ритуалами, практикой и сеансами.")
    
    # АРКАНА УТОЧНЕНИЕ - спросить пользователя
    if kind == "arcana_clarify":
        confidence = data.get("confidence", "low")
        if confidence == "low":
            return f"arcana_clarify:{original_text}"
        # Если confidence=high - редирект без вопроса
        return ("🔮 <b>Это работа для Арканы!</b>\n\n"
                "Перейди в <a href=\"https://t.me/arcana_kailark_bot\">🌒 Arcana</a> и отправь туда:\n"
                f"<code>{original_text[:100]}</code>\n\n"
                "Там я помогу с ритуалами, практикой и сеансами.")

    # ФИНАНСЫ
    if kind in ("expense", "income"):
        await react(msg, "👌" if kind == "expense" else "🏆")
        confidence = data.get("confidence", "high")
        type_label = "💸 Расход" if kind == "expense" else "💰 Доход"
        
        # Safe cast amount
        raw_amount = data.get("amount")
        amount = float(raw_amount) if raw_amount not in (None, "", 0) else 0
        
        # Определить переменные ДО проверки confidence
        category = data.get("category", "💳 Прочее")
        source = data.get("source", "💳 Карта")
        title = data.get("title", original_text[:50])
        
        logger.info("process_item: finance %s - amount=%.0f category=%r source=%r confidence=%r",
                   kind, amount, category, source, confidence)

        # Ambiguous слова без явного дохода-контекста → принудительно low confidence
        _ambiguous_m = re.compile(r'\b(аренд[аы]|арендую|сдаю|сдала|займ)\b', re.IGNORECASE)
        _income_ctx_m = re.compile(
            r'\b(получила|получил|заработала|заработал|зарплата|доход'
            r'|пришло|пришла|вернули|вернул|поступил[аио]?|аванс)\b',
            re.IGNORECASE,
        )
        if confidence == "high" and _ambiguous_m.search(original_text) and not _income_ctx_m.search(original_text):
            logger.info("process_item: ambiguous word without income context → force low confidence")
            confidence = "low"

        # Low confidence → проверяем маркеры дохода/бартера
        if confidence == "low":
            _barter_m = re.compile(r'\b(бартер|обмен)\b', re.IGNORECASE)
            if not _income_ctx_m.search(original_text) and not _barter_m.search(original_text) and not _ambiguous_m.search(original_text):
                logger.info("process_item: low confidence, no income/barter markers → auto-expense")
                confidence = "high"
                kind = "expense"
                type_label = "💸 Расход"
            else:
                logger.info("process_item: low confidence finance, showing UI for clarification")
                return f"finance_clarify:{kind}:{amount}:{category}:{source}:{title}"

        # High confidence + amount=0 → пропустить
        if amount == 0:
            logger.info("process_item: high confidence but amount=0, skipping")
            return ""

        # High confidence + amount > 0 → сохранить в Notion
        logger.info("process_item: saving to Notion - %s %s %s", type_label, amount, category)
        result = await finance_add(
            date=today_moscow(),
            amount=amount,
            category=category,
            type_=type_label,
            source=source,
            description=title,
            user_notion_id=user_notion_id,
        )
        if result:
            sign = "−" if kind == "expense" else "+"
            icon = "💸" if kind == "expense" else "💰"
            if kind == "expense":
                logger.info("finance saved via classifier: category=%s — calling budget check", category)
                try:
                    from nexus.handlers.finance import _check_budget_limit
                    await _check_budget_limit(category, msg, user_notion_id, amount=amount)
                except Exception as e:
                    logger.error("budget check error: %s", e, exc_info=True)
                # Предложить вычеркнуть из списка покупок
                try:
                    from core.list_manager import find_matching_items
                    from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
                    matches = await find_matching_items(title, category, "☀️ Nexus", user_notion_id)
                    if matches:
                        buttons = []
                        item_names = []
                        for m in matches[:3]:
                            cat_e = (m.get("category") or "").split(" ")[0]
                            item_names.append(f"◻️ {m['name']} · {cat_e}")
                            buttons.append([InlineKeyboardButton(
                                text=f"✅ {m['name']}",
                                callback_data=f"list_cross_{m['id'][:28]}",
                            )])
                        buttons.append([InlineKeyboardButton(text="Нет", callback_data="list_cross_no")])
                        await msg.answer(
                            f"🛒 Есть в списке:\n" + "\n".join(item_names),
                            reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
                            parse_mode="HTML",
                        )
                except Exception as e:
                    logger.debug("list cross-off check: %s", e)
            elif kind == "income":
                # Любой доход (кроме ЗП/аренды/практики) → предложить пересчёт бюджета
                _skip_cats = {"💰 Зарплата", "🔮 Практика", "🏠 Жильё"}
                _skip_desc = {"аренда", "зарплата", "зп"}
                title_lower = (title or "").lower()
                if category not in _skip_cats and not any(w in title_lower for w in _skip_desc):
                    try:
                        from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
                        await msg.answer(
                            "📊 Пересчитать бюджет с учётом дохода?",
                            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                                InlineKeyboardButton(text="📊 Да", callback_data="budget_recalc_full"),
                                InlineKeyboardButton(text="❌ Нет", callback_data="msg_hide"),
                            ]]),
                        )
                    except Exception as e:
                        logger.error("income recalc prompt error: %s", e)
            return f"{icon} <b>{sign}{amount:,.0f}₽</b> · <b>{title}</b>\n🏷 {category} <i>{source}</i>"
        
        logged = await log_error(original_text, "processing_error", _classify_last_raw,
                                 "finance_add вернул None", error_code="–")
        notion_status = "записано в ⚠️Ошибки" if logged else "лог недоступен"
        return f"❌ Ошибка записи финансов · {notion_status}"

    # UPDATE - исправить последнюю финансовую запись
    if kind == "update":
        from core.notion_client import finance_update
        target = data.get("target", "expense")  # expense или income
        field = data.get("field", "source")  # source, category, amount
        new_value = data.get("new_value", "")
        
        result = await finance_update(target_type=target, field=field, new_value=new_value)
        if result:
            field_name = {"source": "Источник", "category": "Категория", "amount": "Сумма"}.get(field, field)
            return f"✏️ Обновлено: {field_name} → {new_value}"
        
        return "❌ Ошибка при обновлении записи"
    if kind == "task":
        await react(msg, "⚡")
        from nexus.handlers.tasks import handle_task_parsed, _REL_TIME_RE, _parse_relative_time, _get_user_tz
        logger.info(
            "classifier: task detected - title=%r category=%r deadline=%r priority=%r "
            "repeat=%r day_of_week=%r repeat_time=%r",
            data.get("title"), data.get("category"), data.get("deadline"), data.get("priority"),
            data.get("repeat"), data.get("day_of_week"), data.get("repeat_time"),
        )

        # Post-processing: исправить относительное время которое Claude мог понять неверно
        # "через 2 мин" → Claude пишет "00:02", правильно: datetime.now() + timedelta(minutes=2)
        rel_match = _REL_TIME_RE.search(original_text)
        if rel_match:
            uid = msg.from_user.id
            tz_offset = await _get_user_tz(uid)
            relative_time = _parse_relative_time(original_text, tz_offset)
            unit = rel_match.group(2).lower()
            if unit.startswith("мин") or unit.startswith("ч"):
                logger.info("classifier: overriding deadline→reminder_time with relative=%s", relative_time)
                data["reminder_time"] = relative_time
                data["deadline"] = None
            else:
                logger.info("classifier: overriding deadline with relative=%s", relative_time)
                data["deadline"] = relative_time

        # Post-processing: "до пятницы/среды/etc." → ближайший день недели
        wd_match = _WEEKDAY_DEADLINE_RE.search(original_text)
        if wd_match:
            tz_offset = await _get_user_tz(msg.from_user.id)
            target_wd = _RU_WEEKDAY_NUM[wd_match.group(1).lower()]
            correct_deadline = _nearest_weekday_iso(target_wd, tz_offset)
            logger.info("classifier: weekday deadline '%s' → %s", wd_match.group(1), correct_deadline)
            data["deadline"] = correct_deadline

        logger.info("classifier: calling handle_task_parsed with full data=%s", data)
        data["user_notion_id"] = user_notion_id
        await handle_task_parsed(msg, data)
        return ""

    # ЗАМЕТКИ
    if kind == "note":
        await react(msg, "✍️")
        from nexus.handlers.notes import handle_note
        from core.config import config

        logger.info("process_item: note - text=%r tags=%r", data.get("text", "")[:50], data.get("tags", ""))
        
        # Получить теги из classifier
        raw_tags = data.get("tags", "")
        
        # Добавить эмодзи к известным тегам
        if raw_tags:
            tag_list = [t.strip().lstrip("#").lower() for t in raw_tags.split(",")]
            enriched_tags = []
            for tag in tag_list:
                if tag in TAGS_EMOJI:
                    enriched_tags.append(f"{TAGS_EMOJI[tag]} {tag.capitalize()}")
                else:
                    enriched_tags.append(tag)
            raw_tags = ", ".join(enriched_tags)
        
        await handle_note(msg, data.get("text", original_text), config.nexus.db_notes, raw_tags,
                          user_notion_id=user_notion_id)
        return ""

    # РЕДАКТИРОВАНИЕ ЗАМЕТКИ
    if kind == "edit_note":
        from nexus.handlers.notes import handle_edit_note
        await handle_edit_note(msg, data, user_notion_id)
        return ""

    # ПОИСК ЗАМЕТОК
    if kind == "note_search":
        from nexus.handlers.notes import handle_note_search
        logger.info("process_item: note_search query=%r", data.get("query", ""))
        await handle_note_search(msg, data, user_notion_id=user_notion_id)
        return ""

    # СТАТИСТИКА
    if kind == "stats":
        tg_id = msg.from_user.id if msg and msg.from_user else 0
        logger.info(
            "process_item: stats request - tg_id=%s user_notion_id=%r query=%r",
            tg_id, user_notion_id, data.get("query", ""),
        )
        from nexus.handlers.finance import handle_finance_summary
        result = await handle_finance_summary(
            query=data.get("query", ""), user_notion_id=user_notion_id, uid=int(tg_id) if tg_id else 0
        )
        # Если есть пагинация — отправить сводку + список отдельными сообщениями
        if tg_id:
            from core.pagination import has_pages, get_page_text, get_page_keyboard
            uid_int = int(tg_id)
            if has_pages(uid_int):
                if result and result != "__paginated__":
                    await msg.answer(result, parse_mode="HTML")
                await msg.answer(get_page_text(uid_int), reply_markup=get_page_keyboard(uid_int))
                return ""
        return result

    # ПОМОЩЬ
    if kind == "help":
        logger.info("process_item: help request")
        return ("📋 <b>Nexus понимает свободный текст</b>\n\n"
                "Просто пиши как есть — я разберусь:\n\n"
                "💸 <b>Финансы:</b> <code>450р такси</code>, <code>пришла аренда 35000</code>\n"
                "✓ <b>Задачи:</b> <code>купить корм коту</code>, <code>записаться к врачу</code>\n"
                "📝 <b>Заметки:</b> <code>идея про подкаст</code>, <code>рецепт #здоровье</code>\n"
                "🔮 <b>Для Арканы:</b> <code>купить свечи</code>, <code>ритуал новолуния</code>\n\n"
                "Или команды: <code>/help /stats /start</code>")

    return f"❌ Не так ответил Claude · пусть Кай правит промпт"