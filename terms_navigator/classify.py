"""Разбор документа: тип, номер, ссылки на договор, статус подписания, дата вступления в силу.

Имя файла и порядок загрузки сознательно не используются: тип и статус
определяются только по содержимому.
"""
from __future__ import annotations

import re

from .model import DocKind, Document, Evidence, Paragraph, SignStatus
from .text import (
    DATE_RE,
    decode_bytes,
    iter_dates,
    make_doc_id,
    normalize,
    parse_date,
    split_paragraphs,
    split_sentences,
)

# Номер: после «№» — любой токен с цифрой («1», «STN-101», «45/2026»);
# без «№» — только токен с буквами и цифрами («STN-101»), чтобы не принять
# за номер договора ссылку на пункт («договора 2.1»).
NUMBER_TOKEN = r"(?=[\w\-/]*\d)[A-ZА-ЯЁ0-9][A-ZА-ЯЁ0-9\-/]*"
CODE_TOKEN = r"(?=[\w\-/]*\d)(?=[\w\-/]*[A-ZА-ЯЁ])[A-ZА-ЯЁ0-9][A-ZА-ЯЁ0-9\-/]*"
CONTRACT_REF_RE = re.compile(
    rf"договор\w*\s+(?:поставки\s+)?(?:№\s*({NUMBER_TOKEN})|({CODE_TOKEN}))", re.IGNORECASE
)
TITLE_NUMBER_RE = re.compile(rf"№\s*({NUMBER_TOKEN})", re.IGNORECASE)

UNSIGNED_RE = re.compile(
    r"\bне\s+подписан|\bпроект\b|на\s+согласовани|\bне\s+согласован", re.IGNORECASE
)
SIGNED_BOTH_RE = re.compile(r"подписан\w*\s+обеими\s+сторонами", re.IGNORECASE)
SIGNED_ANY_RE = re.compile(r"\bподписан", re.IGNORECASE)
EFFECTIVE_RE = re.compile(
    r"вступа\w*\s+в\s+(?:законную\s+)?силу\s+(?:с\s+)?" + DATE_RE.pattern, re.IGNORECASE
)
EFFECTIVE_ON_SIGN_RE = re.compile(
    r"вступа\w*\s+в\s+силу\s+с\s+(?:момента|даты|дня)\s+(?:его\s+|её\s+|ее\s+)?подписания",
    re.IGNORECASE,
)


def load_document(file: str, raw: bytes | str) -> Document:
    text = normalize(decode_bytes(raw) if isinstance(raw, bytes) else raw)
    paras = split_paragraphs(text)
    title = paras[0].text.strip() if paras else ""
    doc = Document(doc_id=make_doc_id(file), file=file, text=text, paragraphs=paras, title=title)
    _classify(doc)
    _read_header(doc)
    _find_effective_date(doc)
    return doc


def _classify(doc: Document) -> None:
    t = doc.title.upper()
    header_keys = {p.label for p in doc.paragraphs if p.is_header and p.label}
    if "СОГЛАШЕНИ" in t and ("ДОПОЛНИТЕЛЬН" in t or "ИЗМЕНЕНИ" in t):
        doc.kind = DocKind.AMENDMENT
        doc.is_draft_title = t.startswith("ПРОЕКТ")
    elif t.startswith("ДОГОВОР"):
        doc.kind = DocKind.CONTRACT
    elif "ПИСЬМО" in t or {"От", "Кому"} <= header_keys:
        doc.kind = DocKind.LETTER
        doc.sign_status = SignStatus.NOT_APPLICABLE
    else:
        doc.kind = DocKind.UNKNOWN
        doc.warnings.append("Тип документа не распознан по заголовку")

    if m := TITLE_NUMBER_RE.search(doc.title):
        doc.number = m.group(1).rstrip(".")

    refs: list[str] = []
    for m in CONTRACT_REF_RE.finditer(doc.text):
        ref = (m.group(1) or m.group(2)).rstrip(".").upper()
        if ref not in refs:
            refs.append(ref)
    doc.contract_refs = refs


def _read_header(doc: Document) -> None:
    for p in doc.paragraphs:
        if not p.is_header:
            continue
        if p.label in ("Дата", "Дата проекта") and doc.doc_date is None:
            doc.doc_date = next((d for d, _ in iter_dates(p.text)), None)
        if p.label == "Статус" and doc.kind != DocKind.LETTER:
            _parse_status(doc, p)

    if doc.kind in (DocKind.CONTRACT, DocKind.AMENDMENT) and doc.status_evidence is None:
        doc.warnings.append("В документе нет строки «Статус:» — подписание не подтверждено")

    if doc.is_draft_title and doc.sign_status == SignStatus.SIGNED:
        doc.sign_status = SignStatus.UNKNOWN
        doc.warnings.append("Заголовок «ПРОЕКТ», но в статусе указано подписание — противоречие")
    elif doc.is_draft_title and doc.sign_status == SignStatus.UNKNOWN:
        doc.sign_status = SignStatus.UNSIGNED


def _parse_status(doc: Document, p: Paragraph) -> None:
    body = p.text.split(":", 1)[1]
    if UNSIGNED_RE.search(body):
        doc.sign_status = SignStatus.UNSIGNED
    elif m := SIGNED_BOTH_RE.search(body):
        doc.sign_status = SignStatus.SIGNED
        after = body[m.end():]
        dm = DATE_RE.match(after.lstrip())
        if dm:
            doc.signed_on = parse_date(*dm.groups())
        else:
            doc.warnings.append("Дата подписания в статусе не указана")
    elif SIGNED_ANY_RE.search(body):
        doc.sign_status = SignStatus.PARTIAL
        doc.warnings.append("Подписание обеими сторонами не подтверждено")
    else:
        doc.sign_status = SignStatus.UNKNOWN
        doc.warnings.append("Статус подписания не распознан")
    quote = p.text.strip()
    doc.status_evidence = Evidence(doc.doc_id, doc.file, p.idx, p.label, quote, 0, len(quote))


def _find_effective_date(doc: Document) -> None:
    if doc.kind != DocKind.AMENDMENT:
        return
    for p in doc.paragraphs:
        if p.is_header:
            continue
        if m := EFFECTIVE_RE.search(p.text):
            doc.effective_from = parse_date(*m.groups()[-3:])
            doc.effective_evidence = _ev(doc, p, m.start(), m.end())
            return
        if m := EFFECTIVE_ON_SIGN_RE.search(p.text):
            doc.effective_from = doc.signed_on
            doc.effective_evidence = _ev(doc, p, m.start(), m.end())
            return


def _ev(doc: Document, p: Paragraph, s: int, e: int) -> Evidence:
    for a, b in split_sentences(p.text):
        if a <= s < b:
            return Evidence(doc.doc_id, doc.file, p.idx, p.label, p.text[a:b], a, b, s, e)
    return Evidence(doc.doc_id, doc.file, p.idx, p.label, p.text, 0, len(p.text), s, e)
