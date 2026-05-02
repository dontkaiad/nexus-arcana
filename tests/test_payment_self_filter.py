"""tests/test_payment_self_filter.py — by_payment_source игнорирует Self/Free."""
from miniapp.backend.routes.arcana_today import _by_payment_source


def _sess(client_id: str, source: str = "", paid: float = 0, sum_: float = 0) -> dict:
    p = {"properties": {}}
    if client_id:
        p["properties"]["👥 Клиенты"] = {"relation": [{"id": client_id}]}
    if source:
        p["properties"]["Источник"] = {"select": {"name": source}}
    p["properties"]["Сумма"] = {"number": sum_}
    p["properties"]["Оплачено"] = {"number": paid}
    return p


def test_self_client_skipped():
    sessions = [
        _sess("c-self", source="💵 Наличные", paid=500, sum_=500),
        _sess("c-paid", source="💵 Наличные", paid=1000, sum_=1000),
    ]
    type_map = {"c-self": "🌟 Self", "c-paid": "🤝 Платный"}
    out = _by_payment_source(sessions, [], type_map)
    # only c-paid должен учитываться
    assert out["💵 Наличные"]["sessions"] == 1
    assert out["💵 Наличные"]["total_rub"] == 1000


def test_free_client_skipped():
    sessions = [
        _sess("c-free", source="💵 Наличные", paid=0, sum_=0),
        _sess("c-paid", source="💵 Наличные", paid=2000, sum_=2000),
    ]
    type_map = {"c-free": "🎁 Бесплатный", "c-paid": "🤝 Платный"}
    out = _by_payment_source(sessions, [], type_map)
    assert out["💵 Наличные"]["sessions"] == 1
    assert out["💵 Наличные"]["total_rub"] == 2000


def test_no_client_skipped():
    """Запись без relation на клиента — не учитываем (legacy без типа)."""
    sessions = [_sess("", source="💵 Наличные", paid=500, sum_=500)]
    out = _by_payment_source(sessions, [], {})
    assert out["💵 Наличные"]["sessions"] == 0


def test_paid_clients_counted():
    sessions = [
        _sess("c1", source="💵 Наличные", paid=500, sum_=500),
        _sess("c2", source="💳 Карта", paid=1500, sum_=1500),
        _sess("c1", source="🔄 Бартер", paid=0, sum_=0),
    ]
    type_map = {"c1": "🤝 Платный", "c2": "🤝 Платный"}
    out = _by_payment_source(sessions, [], type_map)
    assert out["💵 Наличные"]["sessions"] == 1
    assert out["💵 Наличные"]["total_rub"] == 500
    assert out["💳 Карта"]["sessions"] == 1
    assert out["💳 Карта"]["total_rub"] == 1500
    assert out["🔄 Бартер"]["sessions"] == 1
