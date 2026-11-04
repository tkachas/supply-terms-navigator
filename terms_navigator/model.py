"""Доменная модель: документы, абзацы, цитаты-основания и результат сверки.

Главный инвариант прототипа: любое значение, которое видит пользователь,
сопровождается Evidence — точной цитатой из загруженного текста.
Если цитаты нет, значения тоже нет («нет данных» или «требует проверки»).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from enum import Enum
from typing import Any


class DocKind(str, Enum):
    CONTRACT = "contract"
    AMENDMENT = "amendment"
    LETTER = "letter"
    UNKNOWN = "unknown"


class SignStatus(str, Enum):
    SIGNED = "signed"          # подписан обеими сторонами
    UNSIGNED = "unsigned"      # проект / не подписан
    PARTIAL = "partial"        # подписан, но не ясно кем (например, одной стороной)
    UNKNOWN = "unknown"        # статус не указан или не распознан
    NOT_APPLICABLE = "n/a"     # письма: статус подписания к ним не относится


class FieldState(str, Enum):
    ORIGINAL = "original"          # значение из договора, изменений нет
    AMENDED = "amended"            # значение изменено действующим допсоглашением
    NO_DATA = "no_data"            # в документах значения нет
    NEEDS_REVIEW = "needs_review"  # найдено противоречие / неоднозначность


class PendingKind(str, Enum):
    DRAFT = "draft"                        # проект ДС, не подписан
    LETTER = "letter"                      # предложение в письме
    NOT_YET_EFFECTIVE = "not_yet_effective"  # подписано, но вступает в силу позже даты проверки
    SIGNED_AFTER_DATE = "signed_after_date"  # подписано позже даты проверки
    UNCLEAR = "unclear"                    # статус/дата не позволяют решить автоматически


@dataclass
class Paragraph:
    idx: int                 # сквозной номер абзаца в документе (для якоря в UI)
    text: str
    label: str | None        # «п. 2.1», «Статус», None для неименованных абзацев
    is_header: bool = False


@dataclass
class Evidence:
    """Точная цитата: quote == paragraph.text[start:end]."""

    doc_id: str
    file: str
    para: int
    label: str | None
    quote: str
    start: int
    end: int
    value_start: int | None = None  # подсветка самого значения внутри абзаца
    value_end: int | None = None


@dataclass
class Hit:
    """Сырое попадание экстрактора внутри одного предложения."""

    value: Any
    start: int                  # смещение значения внутри предложения
    end: int
    problem: str | None = None  # если задано — значение нельзя принять без проверки
    note: str | None = None


@dataclass
class Candidate:
    field: str
    value: Any
    evidence: Evidence
    problem: str | None = None
    note: str | None = None


@dataclass
class Document:
    doc_id: str
    file: str
    text: str
    paragraphs: list[Paragraph]
    title: str
    kind: DocKind = DocKind.UNKNOWN
    number: str | None = None              # номер договора или допсоглашения
    contract_refs: list[str] = field(default_factory=list)
    is_draft_title: bool = False           # заголовок начинается с «ПРОЕКТ»
    sign_status: SignStatus = SignStatus.UNKNOWN
    signed_on: date | None = None
    effective_from: date | None = None
    doc_date: date | None = None
    status_evidence: Evidence | None = None
    effective_evidence: Evidence | None = None
    warnings: list[str] = field(default_factory=list)


@dataclass
class FieldValue:
    field: str
    state: FieldState
    value: Any = None
    evidence: Evidence | None = None
    changed_by: str | None = None              # doc_id документа, изменившего значение
    history: list[dict] = field(default_factory=list)   # [{value, evidence, doc_id}]
    notes: list[str] = field(default_factory=list)
    other_mentions: list[Candidate] = field(default_factory=list)  # найдено, но не использовано


@dataclass
class PendingChange:
    field: str
    proposed: Any
    current: Any
    doc_id: str
    kind: PendingKind
    reason: str
    evidence: Evidence


@dataclass
class Issue:
    severity: str          # "review" — требует проверки человеком, "info" — к сведению
    message: str
    doc_id: str | None = None
    evidence: Evidence | None = None


@dataclass
class RelatedDoc:
    doc_id: str
    applied: bool
    verdict: str           # человекочитаемое решение: «учтено», «не учтено: проект» …


@dataclass
class ContractView:
    contract_no: str
    doc_id: str
    fields: dict[str, FieldValue]
    related: list[RelatedDoc] = field(default_factory=list)
    pending: list[PendingChange] = field(default_factory=list)
    issues: list[Issue] = field(default_factory=list)

    @property
    def needs_review(self) -> bool:
        return any(i.severity == "review" for i in self.issues) or any(
            f.state == FieldState.NEEDS_REVIEW for f in self.fields.values()
        )


@dataclass
class Analysis:
    check_date: date
    documents: list[Document]
    contracts: list[ContractView]
    issues: list[Issue] = field(default_factory=list)   # проблемы вне конкретного договора

    def contract(self, number: str) -> ContractView | None:
        norm = number.strip().upper()
        return next((c for c in self.contracts if c.contract_no.upper() == norm), None)

    def doc(self, doc_id: str) -> Document:
        return next(d for d in self.documents if d.doc_id == doc_id)
