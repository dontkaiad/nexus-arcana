"""evals — проверки качества пайплайна памяти: классификатор, поиск (recall@5), grounding ответа.

Код и СИНТЕТИЧЕСКИЕ кейсы лежат в репозитории (evals/synthetic/), приватные кейсы на реальных данных — только в
data/evals/ (в .gitignore). Одна команда для офлайн-версии: `python -m evals offline`. См. docs/EVALS.md.
"""

import os as _os

# Эвалам нужен только импорт core.* — конфиг требует обязательные переменные. Для офлайн-прогонов (CI, без .env) ставим
# безопасные заглушки; НЕ перезаписываем уже заданные — на проде и локально с .env остаются настоящие значения.
for _k, _v in {
    "ALLOWED_TELEGRAM_IDS": "700000001",
    "ANTHROPIC_API_KEY": "offline-eval-no-network",
    "NEXUS_BOT_TOKEN": "123456789:ABCdefGHIjklMNOpqrsTUVwxyz012345678",
    "ARCANA_BOT_TOKEN": "987654321:ABCdefGHIjklMNOpqrsTUVwxyz012345678",
    "LOG_LEVEL": "WARNING",
}.items():
    if not _os.environ.get(_k):
        _os.environ[_k] = _v
