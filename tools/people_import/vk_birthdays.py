"""ДР из VK: friends.get fields=bdate → staging. .env: VK_TOKEN.
Запуск: python3 -m tools.people_import.vk_birthdays
"""
from __future__ import annotations

import httpx

from tools.people_import.common import append_staging, birthday_fact, env


def main() -> None:
    r = httpx.get(
        "https://api.vk.com/method/friends.get",
        params={"fields": "bdate,screen_name,nickname", "access_token": env("VK_TOKEN"), "v": "5.199"},
        timeout=30,
    ).json()
    if "error" in r:
        raise SystemExit("VK error: %s" % r["error"].get("error_msg"))
    items = r["response"]["items"]
    records = []
    for f in items:
        parts = (f.get("bdate") or "").split(".")
        if len(parts) < 2:
            continue
        name = ("%s %s" % (f.get("first_name", ""), f.get("last_name", ""))).strip()
        records.append({
            "name": name,
            "aliases": [f.get("first_name", ""), f.get("last_name", ""), f.get("nickname", ""),
                        f.get("screen_name", "")],
            "type": "birthday",
            "fact": birthday_fact(int(parts[0]), int(parts[1]), int(parts[2]) if len(parts) > 2 else None),
            "quote": "", "source": "vk", "origin": "api",
            "vk_id": f.get("id"), "vk_screen": f.get("screen_name") or None,
        })
    print("VK: друзей=%d, с ДР=%d, добавлено в staging=%d" % (len(items), len(records), append_staging(records)))


if __name__ == "__main__":
    main()
