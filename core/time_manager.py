from datetime import datetime, timezone, timedelta

# #170: общий tz-кеш из единого источника (core.location). Раньше тут был
# локальный пустой dict — get_user_now всегда отдавал 3. Теперь sync-хелпер
# видит реальный offset, прогретый core.location.get_user_tz / set_user_location.
from core.location import _tz_offsets as _tz_cache

def get_user_now(uid: int) -> datetime:
    return datetime.now(timezone(timedelta(hours=_tz_cache.get(uid, 3))))

def get_str_now(uid: int) -> str:
    return get_user_now(uid).strftime("%Y-%m-%d %H:%M (%A)")
