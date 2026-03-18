import logging
from datetime import datetime, timezone, timedelta
from core.notion_client import db_query, page_create, page_update, _title, _text, _select
from core.config import config

logger = logging.getLogger("core.time")
_tz_cache = {}

def get_user_now(uid: int) -> datetime:
    return datetime.now(timezone(timedelta(hours=_tz_cache.get(uid, 3))))

def get_str_now(uid: int) -> str:
    return get_user_now(uid).strftime("%Y-%m-%d %H:%M (%A)")

async def load_user_tz(uid: int):
    res = await db_query(config.nexus.db_memory, filter={"property": "Ключ", "title": {"equals": f"tz_{uid}"}})
    if res:
        try:
            val = res[0]["properties"]["Значение"]["rich_text"][0]["plain_text"]
            _tz_cache[uid] = int(val); return True
        except: pass
    _tz_cache[uid] = 3
    return False

async def save_user_tz(uid: int, offset: int, city: str):
    _tz_cache[uid] = offset
    key = f"tz_{uid}"
    res = await db_query(config.nexus.db_memory, filter={"property": "Ключ", "title": {"equals": key}})
    props = {"Ключ": _title(key), "Значение": _text(str(offset)), "Категория": _select("⚙️ Настройки")}
    if res: await page_update(res[0]["id"], props)
    else: await page_create(config.nexus.db_memory, props)