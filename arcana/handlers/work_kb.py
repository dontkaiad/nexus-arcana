"""arcana/handlers/work_kb.py — заглушка-роутер.

Старый flow «Работа создана → выбери reminder кнопкой» удалён в пользу
preview-flow (см. arcana/handlers/work_preview.py). Reminder теперь
автоматически = дедлайн - 1 день, либо явно указан Кай в превью.
"""
from __future__ import annotations

from aiogram import Router

router = Router()
