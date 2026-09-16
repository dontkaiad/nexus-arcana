"""Regression: создание задачи со словом «заметка» внутри того же сообщения
(«задача получить загран ... заметка подойти в окно 5 по очереди...»)
классифицировалось как ДВА отдельных item: {"type":"task",...} И
{"type":"note",...} из одного сообщения — бот выполнял два разных действия
(создавал задачу И отдельную заметку с попыткой подобрать теги) вместо
одной задачи с текстом «заметки» в её поле note.

По аналогии с уже решённым случаем «задача + сумма денег» (см. note про
expense чуть выше в промпте) добавлена явная инструкция + пример: слово
«заметка», описывающее детали ТОЙ ЖЕ задачи, остаётся ОДНИМ item type=task
с деталями в поле note, без отдельного item type=note.

Privacy: синтетический текст задачи.
"""
from __future__ import annotations

from core.classifier import build_system


def test_system_prompt_has_task_note_merge_guidance():
    system = build_system(3)
    assert "заметка" in system.lower()
    assert "ОДИН item" in system or "один item" in system.lower()
    # инструкция должна явно запрещать создавать отдельный note-item
    # для деталей, относящихся к только что описанной задаче
    assert "БЕЗ отдельного note" in system or "без отдельного note" in system.lower()


def test_system_prompt_example_matches_reported_bug():
    system = build_system(3)
    assert "получить загран" in system
    assert "окно 5" in system
