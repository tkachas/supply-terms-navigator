"""Сверка: связываем документы с договорами и вычисляем условия на дату проверки.

Правило задания: условия меняет только допсоглашение, подписанное обеими
сторонами и вступившее в силу к дате проверки. Всё остальное (проекты, письма,
ДС с будущей датой вступления) показывается отдельно как «несогласованные
изменения» и на значения не влияет. Если решить автоматически нельзя —
не угадываем, а помечаем «требует проверки».
"""
from __future__ import annotations

import re
from datetime import date

from .fields import FIELDS, FieldSpec
from .model import (
    Analysis,
    Candidate,
    ContractView,
    DocKind,
    Document,
    Evidence,
    FieldState,
    FieldValue,
    Hit,
    Issue,
    Paragraph,
    PendingChange,
    PendingKind,
    RelatedDoc,
    SignStatus,
)
from .text import fmt_date, split_sentences

_SKIP_HEADER = {"Дата", "Дата проекта", "Статус", "От", "Кому", "Тема"}
_REDACTION_RE = re.compile(r"редакци|изложить", re.IGNORECASE)
_QUOTED_RE = re.compile(r"«(.+)»", re.DOTALL)
_APPENDIX_RE = re.compile(r"приложени|спецификаци", re.IGNORECASE)


# --- извлечение кандидатов -------------------------------------------------

def _evidence(doc: Document, p: Paragraph, s: int, e: int, hit: Hit, base: int) -> Evidence:
    return Evidence(
        doc.doc_id, doc.file, p.idx, p.label, p.text[s:e], s, e,
        base + hit.start, base + hit.end,
    )


def _content_paragraphs(doc: Document):
    for p in doc.paragraphs:
        if p.idx == 0 or (p.is_header and p.label in _SKIP_HEADER):
            continue
        yield p


def contract_candidates(doc: Document, spec: FieldSpec) -> tuple[list[Candidate], list[Candidate]]:
    """(значения в формулировке договора, прочие упоминания)."""
    found, other = [], []
    for p in _content_paragraphs(doc):
        for s, e in split_sentences(p.text):
            sent = p.text[s:e]
            for hit in spec.find(sent):
                found.append(Candidate(spec.key, hit.value, _evidence(doc, p, s, e, hit, s),
                                       hit.problem, hit.note))
            for hit in spec.mentions(sent):
                other.append(Candidate(spec.key, hit.value, _evidence(doc, p, s, e, hit, s),
                                       None, hit.note))
    return found, other


def change_candidates(doc: Document, spec: FieldSpec) -> list[Candidate]:
    """Новые значения поля в допсоглашении / письме / проекте."""
    out: list[Candidate] = []
    for p in _content_paragraphs(doc):
        if p.is_header:
            continue
        q = _QUOTED_RE.search(p.text)
        if q and _REDACTION_RE.search(p.text[: q.start()]):
            # «Пункт 2.1 изложить в редакции: «…»» — новое значение ищем внутри цитаты
            # той же логикой, что и в договоре; основание — абзац целиком.
            inner = q.group(1)
            for s, e in split_sentences(inner):
                for hit in spec.find(inner[s:e]):
                    base = q.start(1) + s
                    ev = Evidence(doc.doc_id, doc.file, p.idx, p.label, p.text, 0, len(p.text),
                                  base + hit.start, base + hit.end)
                    out.append(Candidate(spec.key, hit.value, ev, hit.problem, hit.note))
            continue
        for s, e in split_sentences(p.text):
            for hit in spec.find_proposal(p.text[s:e]):
                out.append(Candidate(spec.key, hit.value, _evidence(doc, p, s, e, hit, s),
                                     hit.problem, hit.note))
    return out


def _topic_evidence(doc: Document, spec: FieldSpec) -> Evidence | None:
    if spec.topic is None:
        return None
    for p in _content_paragraphs(doc):
        for s, e in split_sentences(p.text):
            if spec.topic.search(p.text[s:e]):
                return Evidence(doc.doc_id, doc.file, p.idx, p.label, p.text[s:e], s, e)
    return None


def _base_value(doc: Document, spec: FieldSpec) -> FieldValue:
    found, other = contract_candidates(doc, spec)
    fv = FieldValue(spec.key, FieldState.NO_DATA, other_mentions=other)
    if not found:
        fv.evidence = _topic_evidence(doc, spec)
        if fv.evidence:
            if _APPENDIX_RE.search(fv.evidence.quote):
                fv.notes.append("Значение определяется приложением/спецификацией, "
                                "которых нет среди загруженных документов")
            else:
                fv.notes.append("Условие упомянуто, но конкретного значения в тексте нет")
        return fv

    distinct = {c.value for c in found if c.value is not None}
    first = found[0]
    fv.value, fv.evidence = first.value, first.evidence
    if first.note:
        fv.notes.append(first.note)
    problems = [c.problem for c in found if c.problem]
    if problems:
        fv.state = FieldState.NEEDS_REVIEW
        fv.notes.extend(problems)
        if first.value is None:
            fv.value = None
    elif len(distinct) > 1:
        fv.state = FieldState.NEEDS_REVIEW
        fv.value = None
        fv.notes.append("В договоре несколько разных значений: "
                        + ", ".join(spec.fmt(v) for v in distinct))
        fv.other_mentions = found + other
    else:
        fv.state = FieldState.ORIGINAL
    return fv


# --- статус документов изменения -------------------------------------------

def _applicability(doc: Document, check: date) -> tuple[bool, PendingKind | None, str]:
    """Можно ли применить документ к условиям на дату проверки и почему."""
    if doc.kind == DocKind.LETTER:
        return False, PendingKind.LETTER, "письмо — предложение, а не изменение договора"
    if doc.sign_status == SignStatus.UNSIGNED:
        return False, PendingKind.DRAFT, "проект, не подписан"
    if doc.sign_status != SignStatus.SIGNED:
        return False, PendingKind.UNCLEAR, "подписание обеими сторонами не подтверждено"
    if doc.signed_on is None:
        return False, PendingKind.UNCLEAR, "не указана дата подписания"
    if doc.signed_on > check:
        return False, PendingKind.SIGNED_AFTER_DATE, f"подписано {fmt_date(doc.signed_on)} — после даты проверки"
    if doc.effective_from is None:
        return False, PendingKind.UNCLEAR, "не указана дата вступления в силу"
    if doc.effective_from > check:
        return False, PendingKind.NOT_YET_EFFECTIVE, f"вступает в силу {fmt_date(doc.effective_from)}"
    return True, None, (f"подписано {fmt_date(doc.signed_on)}, "
                        f"в силе с {fmt_date(doc.effective_from)}")


def _order_key(doc: Document):
    num = int(doc.number) if doc.number and doc.number.isdigit() else 0
    return (doc.effective_from or date.min, doc.signed_on or date.min, num)


# --- основной расчёт -------------------------------------------------------

def analyze(docs: list[Document], check: date) -> Analysis:
    analysis = Analysis(check_date=check, documents=docs, contracts=[])

    contracts: dict[str, Document] = {}
    for d in docs:
        if d.kind != DocKind.CONTRACT:
            continue
        if not d.number:
            analysis.issues.append(Issue("review", "Номер договора не найден в заголовке", d.doc_id))
            continue
        key = d.number.upper()
        if key in contracts:
            analysis.issues.append(Issue(
                "review", f"Договор {d.number} загружен дважды ({contracts[key].file}, {d.file}) — "
                          "используется первый, сверьте версии", d.doc_id))
            continue
        contracts[key] = d

    linked: dict[str, list[Document]] = {k: [] for k in contracts}
    for d in docs:
        if d.kind == DocKind.CONTRACT:
            continue
        if d.kind == DocKind.UNKNOWN:
            analysis.issues.append(Issue("review", "Тип документа не распознан — документ не учтён", d.doc_id))
            continue
        refs = [r for r in d.contract_refs if r in contracts]
        if not refs:
            # запасной путь: номер известного договора где-то в тексте
            refs = [k for k in contracts if re.search(rf"(?<![\w-]){re.escape(k)}(?![\w-])", d.text, re.I)]
        if len(refs) == 1:
            linked[refs[0]].append(d)
        elif len(refs) > 1:
            analysis.issues.append(Issue(
                "review", f"Документ ссылается на несколько договоров ({', '.join(refs)}) — "
                          "привяжите вручную", d.doc_id))
        else:
            missing = ", ".join(d.contract_refs) or "не указан"
            analysis.issues.append(Issue(
                "review", f"Договор для документа не найден среди загруженных (ссылка: {missing})",
                d.doc_id))

    for key, cdoc in contracts.items():
        analysis.contracts.append(_resolve_contract(cdoc, linked[key], check))
    analysis.contracts.sort(key=lambda c: c.contract_no)
    return analysis


def _resolve_contract(cdoc: Document, related: list[Document], check: date) -> ContractView:
    view = ContractView(cdoc.number or "?", cdoc.doc_id, {})
    for w in cdoc.warnings:
        view.issues.append(Issue("review", w, cdoc.doc_id, cdoc.status_evidence))
    if cdoc.sign_status == SignStatus.SIGNED and cdoc.signed_on and cdoc.signed_on > check:
        view.issues.append(Issue("review", f"Договор подписан {fmt_date(cdoc.signed_on)} — "
                                           "позже даты проверки", cdoc.doc_id, cdoc.status_evidence))
    elif cdoc.sign_status not in (SignStatus.SIGNED,) and not cdoc.warnings:
        view.issues.append(Issue("review", "Договор не подписан обеими сторонами",
                                 cdoc.doc_id, cdoc.status_evidence))

    for spec in FIELDS:
        view.fields[spec.key] = _base_value(cdoc, spec)

    # Порядок применения — по дате вступления в силу и подписания,
    # а не по имени файла или порядку загрузки.
    applied_docs: list[Document] = []
    not_applied: list[tuple[Document, PendingKind, str]] = []
    for d in sorted(related, key=_order_key):
        ok, pkind, reason = _applicability(d, check)
        for w in d.warnings:
            view.issues.append(Issue("review", w, d.doc_id, d.status_evidence))
        view.related.append(RelatedDoc(d.doc_id, ok, f"{'учтено' if ok else 'не учтено'}: {reason}"))
        if ok:
            applied_docs.append(d)
            if not any(change_candidates(d, s) for s in FIELDS if s.changeable):
                view.issues.append(Issue("info", f"{_doc_name(d)}: изменений суммы или срока не найдено",
                                         d.doc_id))
        else:
            not_applied.append((d, pkind, reason))

    for spec in FIELDS:
        if spec.changeable:
            _apply_changes(view, spec, applied_docs)

    # Несогласованные изменения считаем после применения действующих ДС,
    # чтобы «текущее значение» в них было актуальным.
    for d, pkind, reason in not_applied:
        found_any = False
        for spec in FIELDS:
            if not spec.changeable:
                continue
            for c in change_candidates(d, spec):
                found_any = True
                current = view.fields[spec.key].value
                view.pending.append(PendingChange(spec.key, c.value, current, d.doc_id, pkind,
                                                  reason, c.evidence))
        if pkind == PendingKind.UNCLEAR:
            view.issues.append(Issue("review", f"{_doc_name(d)}: {reason} — решите вручную, "
                                               "учитывать ли изменения", d.doc_id, d.status_evidence))
        if not found_any:
            view.issues.append(Issue("info", f"{_doc_name(d)}: предлагаемых изменений суммы или срока "
                                             "не найдено", d.doc_id))
    return view


def _apply_changes(view: ContractView, spec: FieldSpec, docs: list[Document]) -> None:
    fv = view.fields[spec.key]
    by_date: dict[tuple, list[tuple[Document, Candidate]]] = {}
    for d in docs:
        for c in change_candidates(d, spec):
            by_date.setdefault((d.effective_from, d.signed_on), []).append((d, c))

    for _, items in sorted(by_date.items(), key=lambda kv: kv[0]):
        values = {c.value for _, c in items}
        if len(values) > 1:
            fv.state = FieldState.NEEDS_REVIEW
            fv.notes.append("Действующие допсоглашения с одной датой вступления в силу задают "
                            "разные значения: " + ", ".join(spec.fmt(v) for v in values))
            fv.other_mentions.extend(c for _, c in items)
            fv.value = None
            return
        d, c = items[-1]
        fv.history.append({"value": fv.value, "evidence": fv.evidence, "doc_id": view.doc_id
                           if not fv.changed_by else fv.changed_by})
        fv.value, fv.evidence, fv.changed_by = c.value, c.evidence, d.doc_id
        fv.notes = [c.note] if c.note else []
        if c.problem:
            fv.state = FieldState.NEEDS_REVIEW
            fv.notes.append(c.problem)
        else:
            fv.state = FieldState.AMENDED


def _doc_name(d: Document) -> str:
    return d.title if len(d.title) < 80 else d.file
