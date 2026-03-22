"""
fix_users.py — одноразовый скрипт: заполнить/исправить поле "🪪 Пользователи"
для всех записей где оно пустое или содержит неверный page ID.

Запуск: python fix_users.py
"""
import json
import time
import urllib.request
import urllib.error

TOKEN = "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"
TG_ID = 67686090
DB_USERS = "32842b3b1ac080f4b4bde1aaa3b9d312"
FIELD = "🪪 Пользователи"
# Старый неверный хардкод — тоже заменяем
OLD_WRONG_ID = "32842b3b-1ac0-805d-8f6e-e48b313e64b2"

DATABASES = {
    "FINANCE":   "31a42b3b-1ac0-80ae-8b6a-d8ba84d141bb",
    "TASKS":     "31a42b3b-1ac0-8051-a3cc-de86e6233d30",
    "NOTES":     "31a42b3b-1ac0-807b-a68f-d700ab695e7c",
    "MEMORY":    "31a42b3b-1ac0-801f-8e3c-f1441b61bc69",
}

HEADERS = {
    "Authorization": f"Bearer {TOKEN}",
    "Notion-Version": "2022-06-28",
    "Content-Type": "application/json",
}


def _req(method: str, url: str, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, headers=HEADERS, method=method)
    try:
        with urllib.request.urlopen(req) as r:
            return json.loads(r.read()), None
    except urllib.error.HTTPError as e:
        return None, f"HTTP {e.code}: {e.read().decode()}"


# ── Шаг 1: получить правильный page ID из базы Пользователи ──────────────────

def get_correct_user_id() -> str:
    print(f"🔍 Ищу пользователя TG ID={TG_ID} в базе Пользователи...")
    body = {"filter": {"property": "TG ID", "number": {"equals": TG_ID}}}
    resp, err = _req("POST", f"https://api.notion.com/v1/databases/{DB_USERS}/query", body)
    if err:
        raise RuntimeError(f"Ошибка запроса к Users DB: {err}")
    results = resp.get("results", [])
    if not results:
        raise RuntimeError(f"Пользователь TG={TG_ID} не найден в базе Пользователи")
    if len(results) > 1:
        print(f"  ⚠️  Найдено {len(results)} записей — берём первую (самую старую):")
        for p in results:
            props = p["properties"]
            name_items = props.get("Имя", {}).get("title") or []
            name = name_items[0]["text"]["content"] if name_items else "?"
            print(f"      id={p['id']}  name={name!r}")
    page = results[0]
    props = page["properties"]
    name_items = props.get("Имя", {}).get("title") or []
    name = name_items[0]["text"]["content"] if name_items else "?"
    uid = page["id"]
    print(f"  ✅ Правильный page ID: {uid}  ({name!r})\n")
    return uid


# ── Шаг 2: обход баз ─────────────────────────────────────────────────────────

def query_all(db_id: str):
    pages = []
    cursor = None
    while True:
        body = {"page_size": 100}
        if cursor:
            body["start_cursor"] = cursor
        resp, err = _req("POST", f"https://api.notion.com/v1/databases/{db_id}/query", body)
        if err:
            print(f"    ⚠️  query error: {err}")
            break
        pages.extend(resp.get("results", []))
        if resp.get("has_more"):
            cursor = resp.get("next_cursor")
        else:
            break
        time.sleep(0.3)
    return pages


def get_current_relation_ids(page: dict) -> list:
    """Возвращает список id из поля FIELD (может быть пустым)."""
    field_data = page.get("properties", {}).get(FIELD, {})
    return [r["id"] for r in field_data.get("relation", [])]


def needs_update(current_ids: list, correct_id: str) -> bool:
    """True если поле пустое или содержит только старый неверный ID."""
    if not current_ids:
        return True
    if current_ids == [OLD_WRONG_ID]:
        return True
    return False


def get_title(page: dict) -> str:
    props = page.get("properties", {})
    for prop in props.values():
        if prop.get("type") == "title":
            items = prop.get("title", [])
            if items:
                t = items[0].get("text", {}).get("content", "")
                return (t[:40] + "…") if len(t) > 40 else t
    return ""


def patch_page(page_id: str, correct_id: str):
    body = {"properties": {FIELD: {"relation": [{"id": correct_id}]}}}
    _, err = _req("PATCH", f"https://api.notion.com/v1/pages/{page_id}", body)
    return err


def main():
    correct_id = get_correct_user_id()

    total_patched = 0
    total_skipped = 0
    total_errors = 0

    for db_name, db_id in DATABASES.items():
        print(f"📂 {db_name} ({db_id})")
        pages = query_all(db_id)
        print(f"   Всего записей: {len(pages)}")

        patched = skipped = errors = 0

        for page in pages:
            page_id = page["id"]
            current_ids = get_current_relation_ids(page)
            title = get_title(page)

            if not needs_update(current_ids, correct_id):
                skipped += 1
                continue

            reason = "пустое" if not current_ids else f"старый ID {current_ids[0]}"
            err = patch_page(page_id, correct_id)
            if err:
                print(f"   ❌ {page_id} «{title}» ({reason}) — {err}")
                errors += 1
            else:
                print(f"   ✅ {page_id} «{title}» ({reason})")
                patched += 1

            time.sleep(0.35)

        print(f"   → Заполнено/исправлено: {patched} | Пропущено: {skipped} | Ошибок: {errors}\n")
        total_patched += patched
        total_skipped += skipped
        total_errors += errors

    print("=" * 55)
    print(f"Итого: ✅ обновлено {total_patched} | ⏭ пропущено {total_skipped} | ❌ ошибок {total_errors}")
    print(f"Правильный user page ID: {correct_id}")


if __name__ == "__main__":
    main()
