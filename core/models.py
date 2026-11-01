"""core/models.py — РОЛИ LLM-вызовов: какая модель, какой режим мышления, какой кеш — в одном месте.

Вызов выбирает не модель, а роль: `ask_claude(prompt, role="parse")`. Роль → модель по цепочке (первое найденное):
    MODEL_<РОЛЬ>  →  MODEL_HAIKU / MODEL_SONNET (для всей группы)  →  значение по умолчанию роли ниже.
Так переход на новое поколение и откат — переменными окружения, без правки кода. Режим мышления роли
(`off` / `low` / None=по умолчанию модели) переопределяется THINKING_<РОЛЬ>. Мышление и `temperature` учитывает
claude_client.apply_model_params: для 4.x они игнорируются / передаются как раньше, для 5.x — см. там.

Правило проекта (CLAUDE.md): Sonnet — только там, где нужна глубина (трактовки, бюджет, vision, СДВГ-советы,
резюме сессий); всё остальное — Haiku. Это закреплено полем `tier` роли и тестом-стражем tests/test_models_audit.py.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Dict, Optional

HAIKU_DEFAULT = "claude-haiku-4-5-20251001"
SONNET_DEFAULT = "claude-sonnet-4-6"


@dataclass(frozen=True)
class Role:
    tier: str                         # "haiku" | "sonnet" — какая группа моделей разрешена роли
    purpose: str
    thinking: Optional[str] = None    # режим по умолчанию: "off" | "low" | None (по умолчанию модели)
    cache_ttl: Optional[str] = None   # prompt caching системного промпта: None | "5m" | "1h"
    default: Optional[str] = None     # модель по умолчанию, если отличается от умолчания группы (tier)


ROLES: Dict[str, Role] = {
    # ── Haiku ──
    "classify":      Role("haiku", "классификатор сообщений Nexus (промпт ~14K токенов)", "off", "5m"),
    "route":         Role("haiku", "короткая маршрутизация интента (Arcana-роутер, парсер времени, Zarya)", "off"),
    "parse":         Role("haiku", "JSON-парсеры и короткие извлечения (задачи, финансы, списки, клиенты, ритуалы…)", "off"),
    "spell":         Role("haiku", "исправление опечаток (preprocess.normalize_text)", "off",
                          default="claude-haiku-5-5"),      # пилот перехода на 5.5: A/B 5/5 совпало; откат: MODEL_SPELL=<4.5>
    "short_text":    Role("haiku", "короткий живой текст: дайджест дня, статистика, nudge, tip, резюме", "off"),
    "memory_rerank": Role("haiku", "реранк кандидатов поиска по памяти", "off"),
    "memory_answer": Role("haiku", "ответ по памяти своими словами (строгий grounding)", "off",
                          default="claude-haiku-5-5"),      # уже на 5.5 (этап 4); откат: MODEL_MEMORY_ANSWER=<4.5>
    "categorize":    Role("haiku", "разнос фактов по категориям (разовые скрипты разметки памяти, пакетами)", "off",
                          default="claude-haiku-5-5"),      # одноразовая разметка; откат: MODEL_CATEGORIZE=<4.5>
    # ── Sonnet ──
    "narrative":     Role("sonnet", "трактовки таро и правки трактовок (глубина + эмпатия)"),
    "analysis":      Role("sonnet", "бюджетный анализ и его пересказ"),
    "vision":        Role("sonnet", "фото: чеки, скрины банка, расклады, клиенты", "off"),
    "wellbeing":     Role("sonnet", "СДВГ-советы/профиль, tip погоды — персональный тон", "off"),
    "summary":       Role("sonnet", "резюме расклада в Mini App (в том числе стрим)", "off"),
}


def _env(name: str) -> str:
    return (os.environ.get(name) or "").strip()


def role_names():
    return tuple(ROLES)


def model_for(role: str) -> str:
    """Модель роли: MODEL_<РОЛЬ> → MODEL_HAIKU / MODEL_SONNET → умолчание. Неизвестная роль — ошибка (опечатка не должна
    молча уйти на дефолтную модель)."""
    r = ROLES[role]
    return (_env(f"MODEL_{role.upper()}")
            or (_env("MODEL_HAIKU" if r.tier == "haiku" else "MODEL_SONNET") if not r.default else "")
            or r.default
            or (HAIKU_DEFAULT if r.tier == "haiku" else SONNET_DEFAULT))


def thinking_for(role: str) -> Optional[str]:
    v = _env(f"THINKING_{role.upper()}").lower()
    if v in ("off", "low"):
        return v
    if v in ("default", "none"):
        return None
    return ROLES[role].thinking


def cache_ttl_for(role: str) -> Optional[str]:
    v = _env(f"CACHE_TTL_{role.upper()}").lower()
    if v in ("5m", "1h"):
        return v
    if v in ("off", "none", "0"):
        return None
    return ROLES[role].cache_ttl


def describe() -> Dict[str, Dict[str, object]]:
    """Текущая раскладка ролей (для логов и скриптов): роль → модель / мышление / кеш."""
    return {n: {"model": model_for(n), "thinking": thinking_for(n), "cache": cache_ttl_for(n), "tier": r.tier}
            for n, r in ROLES.items()}
