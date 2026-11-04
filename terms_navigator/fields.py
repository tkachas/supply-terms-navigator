"""Реестр извлекаемых полей.

Каждое поле — это FieldSpec с тремя функциями, работающими на уровне одного
предложения:

* find(sentence)          — значение в формулировке договора («стоимость … составляет X»);
* find_proposal(sentence) — новое значение в формулировке изменения («изменить … на X»);
* mentions(sentence)      — прочие упоминания того же типа, которые сознательно
                            НЕ берутся как значение (аванс, лимит бюджета, срок оплаты),
                            с объяснением причины — чтобы пользователь видел, что они
                            учтены и отброшены, а не пропущены.

Чтобы добавить новое поле (например, ставку неустойки), достаточно описать
FieldSpec и добавить его в FIELDS — разрешение изменений, цитаты и UI
подхватят его автоматически. См. CLAUDE.md, раздел «Как добавить поле».
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Callable

from .model import Hit
from .numwords import words_to_int
from .text import AMOUNT_RE, fmt_amount, fmt_date, iter_dates


@dataclass(frozen=True)
class FieldSpec:
    key: str
    title: str
    find: Callable[[str], list[Hit]]
    find_proposal: Callable[[str], list[Hit]]
    mentions: Callable[[str], list[Hit]]
    fmt: Callable[[Any], str]
    changeable: bool = True     # может ли допсоглашение менять это поле
    # тема поля: если значения нет, предложение с этой темой показывается как основание «нет данных»
    topic: re.Pattern | None = None


def _no_hits(_: str) -> list[Hit]:
    return []


# «изменить … на X», «перенести … с A на B»: берём значение сразу после «на»
_NA_BEFORE = re.compile(r"\bна\s*$", re.IGNORECASE)
_AS_OF_BEFORE = re.compile(r"по\s+состоянию\s+на\s*$", re.IGNORECASE)


def _proposal_hits(sentence: str, keyword: re.Pattern, values: list[Hit]) -> list[Hit]:
    if not keyword.search(sentence) or not values:
        return []
    after_na = [
        h for h in values
        if _NA_BEFORE.search(sentence[: h.start]) and not _AS_OF_BEFORE.search(sentence[: h.start])
    ]
    if after_na:
        return after_na[-1:]
    if len(values) == 1:
        return values
    return [Hit(None, values[0].start, values[-1].end,
                problem="В формулировке изменения несколько значений — новое не определено")]


# --- сумма договора --------------------------------------------------------

_PRICE_CONTEXT = re.compile(
    r"(?:стоимост\w*|цен[аы]\w*)[^.;]{0,80}?"
    r"(?:составля\w*|составит|равн\w*|в\s+размере|:|—|–|-)\s*$",
    re.IGNORECASE,
)
_PRICE_KEYWORD = re.compile(r"стоимост|цен[ауы]\s+договора", re.IGNORECASE)
_NOT_PRICE = {
    "аванс": "авансовый платёж, а не стоимость договора",
    "предоплат": "предоплата, а не стоимость договора",
    "лимит": "лимит/бюджет, а не согласованная стоимость договора",
    "бюджет": "лимит/бюджет, а не согласованная стоимость договора",
    "неустойк": "неустойка, а не стоимость договора",
    "штраф": "штраф, а не стоимость договора",
}


def _amounts(sentence: str) -> list[tuple[Hit, re.Match]]:
    out = []
    for m in AMOUNT_RE.finditer(sentence):
        rub = int(m.group(1).replace(" ", ""))
        kop = m.group(2) or "00"
        value = Decimal(f"{rub}.{kop}")
        hit = Hit(value, m.start(), m.end())
        words = m.group(3)
        if words:
            parsed = words_to_int(words)
            if parsed is not None and parsed != rub:
                hit.problem = (
                    f"Сумма цифрами ({fmt_amount(value)}) не совпадает с суммой прописью "
                    f"({fmt_amount(Decimal(parsed))})"
                )
        if re.search(r"без\s+НДС", sentence, re.IGNORECASE):
            hit.note = "без НДС"
        elif re.search(r"НДС", sentence):
            hit.note = "включая НДС"
        out.append((hit, m))
    return out


def _price_find(sentence: str) -> list[Hit]:
    hits = []
    for hit, _ in _amounts(sentence):
        prefix = sentence[: hit.start]
        ctx = _PRICE_CONTEXT.search(prefix)
        if not ctx:
            continue
        if any(k in ctx.group(0).lower() for k in _NOT_PRICE):
            continue
        hits.append(hit)
    return hits


def _price_proposal(sentence: str) -> list[Hit]:
    return _proposal_hits(sentence, _PRICE_KEYWORD, [h for h, _ in _amounts(sentence)])


def _price_mentions(sentence: str) -> list[Hit]:
    strict = {h.start for h in _price_find(sentence)}
    out = []
    low = sentence.lower()
    for hit, _ in _amounts(sentence):
        if hit.start in strict:
            continue
        reason = next((r for k, r in _NOT_PRICE.items() if k in low), None)
        hit.note = reason or "сумма упомянута вне формулировки стоимости договора"
        hit.problem = None
        out.append(hit)
    return out


# --- срок поставки ---------------------------------------------------------

_DELIVERY_VERB = r"(?:поставить|поставит|поставлен\w*|поставк\w*|отгрузить|отгрузк\w*)"
_DELIVERY_CONTEXT = re.compile(
    _DELIVERY_VERB + r"[^;]{0,100}?(?:не\s+позднее|не\s+позже|до|по|:|—|–)\s*$",
    re.IGNORECASE,
)
_DELIVERY_KEYWORD = re.compile(r"срок\w*\s+поставк|" + _DELIVERY_VERB, re.IGNORECASE)
_NOT_DELIVERY = re.compile(r"оплат|платеж|платёж|приемк|приёмк|аванс", re.IGNORECASE)
_RELATIVE_TERM = re.compile(r"в\s+течение\s+\d+", re.IGNORECASE)


def _dates(sentence: str) -> list[Hit]:
    return [Hit(d, m.start(), m.end()) for d, m in iter_dates(sentence)]


def _delivery_find(sentence: str) -> list[Hit]:
    hits = []
    for hit in _dates(sentence):
        ctx = _DELIVERY_CONTEXT.search(sentence[: hit.start])
        if ctx and not _NOT_DELIVERY.search(ctx.group(0)):
            hits.append(hit)
    if (not hits and _DELIVERY_KEYWORD.search(sentence) and _RELATIVE_TERM.search(sentence)
            and not _NOT_DELIVERY.search(sentence)):
        m = _RELATIVE_TERM.search(sentence)
        hits.append(Hit(None, m.start(), m.end(),
                        problem="Срок поставки задан относительно события — календарную дату вычислить нельзя"))
    return hits


def _delivery_proposal(sentence: str) -> list[Hit]:
    if _NOT_DELIVERY.search(sentence) and not re.search(r"срок\w*\s+поставк", sentence, re.IGNORECASE):
        return []
    return _proposal_hits(sentence, _DELIVERY_KEYWORD, _dates(sentence))


def _delivery_mentions(sentence: str) -> list[Hit]:
    if not _NOT_DELIVERY.search(sentence):
        return []
    strict = {h.start for h in _delivery_find(sentence)}
    out = []
    for hit in _dates(sentence):
        if hit.start not in strict:
            hit.note = "дата относится к оплате/приёмке, а не к поставке"
            out.append(hit)
    return out


# --- поставщик -------------------------------------------------------------

_SUPPLIER_RE = re.compile(r"Поставщик:\s*([^«.;]*«[^»]+»|[^.;]+)")


def _supplier_find(sentence: str) -> list[Hit]:
    m = _SUPPLIER_RE.search(sentence)
    if not m:
        return []
    return [Hit(m.group(1).strip(), m.start(1), m.end(1))]


FIELDS: list[FieldSpec] = [
    FieldSpec("supplier", "Поставщик", _supplier_find, _no_hits, _no_hits,
              lambda v: v or "нет данных", changeable=False),
    FieldSpec("amount", "Сумма договора", _price_find, _price_proposal, _price_mentions,
              fmt_amount, topic=_PRICE_KEYWORD),
    FieldSpec("delivery_date", "Срок поставки", _delivery_find, _delivery_proposal,
              _delivery_mentions, fmt_date, topic=re.compile(r"срок\w*\s+поставк", re.I)),
]
FIELD_BY_KEY = {f.key: f for f in FIELDS}
