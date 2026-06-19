from datetime import datetime, timezone, timedelta

_tz_cache = {}

def get_user_now(uid: int) -> datetime:
    return datetime.now(timezone(timedelta(hours=_tz_cache.get(uid, 3))))

def get_str_now(uid: int) -> str:
    return get_user_now(uid).strftime("%Y-%m-%d %H:%M (%A)")
