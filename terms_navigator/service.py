"""Единая точка входа для CLI, веб-сервера и тестов."""
from __future__ import annotations

from datetime import date
from pathlib import Path

from . import queries
from .classify import load_document
from .model import Analysis
from .resolve import analyze

DEFAULT_CHECK_DATE = date(2026, 3, 10)   # дата проверки из условий задания; меняется в UI и CLI


def analyze_files(files: list[tuple[str, bytes | str]], check_date: date = DEFAULT_CHECK_DATE) -> Analysis:
    docs = [load_document(name, raw) for name, raw in files]
    return analyze(docs, check_date)


def analyze_dir(path: str | Path, check_date: date = DEFAULT_CHECK_DATE) -> Analysis:
    files = [(p.name, p.read_bytes()) for p in sorted(Path(path).glob("*.txt"))]
    return analyze_files(files, check_date)


def ask(a: Analysis, question: dict) -> queries.Answer:
    """question: {"type": "delivery_date"|"amount", "contract": "STN-101"}
               | {"type": "deliveries_before", "before": "2026-04-01"}"""
    qtype = question.get("type")
    if qtype == "delivery_date":
        return queries.delivery_date(a, question["contract"])
    if qtype == "amount":
        return queries.amount(a, question["contract"])
    if qtype == "deliveries_before":
        return queries.deliveries_before(a, date.fromisoformat(question["before"]))
    raise ValueError(f"Неизвестный тип вопроса: {qtype!r}")
