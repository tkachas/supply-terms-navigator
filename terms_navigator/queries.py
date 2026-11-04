"""Ответы на вопросы закупщика поверх результата сверки.

Вопросы параметризованы (договор, дата) — ответы не зашиты, а собираются из
Analysis. Каждое утверждение в ответе несёт ссылки на цитаты-основания.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from .fields import FIELD_BY_KEY
from .model import Analysis, ContractView, Evidence, FieldState, PendingKind
from .text import fmt_date

PENDING_LABEL = {
    PendingKind.DRAFT: "проект ДС, не подписан",
    PendingKind.LETTER: "предложение в письме, не согласовано",
    PendingKind.NOT_YET_EFFECTIVE: "ДС подписано, но ещё не вступило в силу",
    PendingKind.SIGNED_AFTER_DATE: "ДС подписано после даты проверки",
    PendingKind.UNCLEAR: "статус ДС не подтверждён",
}


@dataclass
class Line:
    text: str
    evidence: list[Evidence] = field(default_factory=list)
    tone: str = "normal"     # normal | warn | muted


@dataclass
class Answer:
    question: str
    status: str               # ok | no_data | needs_review | not_found
    headline: str
    lines: list[Line] = field(default_factory=list)


def _field_answer(a: Analysis, contract_no: str, key: str, question: str) -> Answer:
    spec = FIELD_BY_KEY[key]
    c = a.contract(contract_no)
    if c is None:
        return Answer(question, "not_found", f"Договор {contract_no} не найден среди загруженных документов")
    fv = c.fields[key]
    ev = [fv.evidence] if fv.evidence else []
    subject = f"{c.contract_no} — {spec.title.lower()}"
    on_date = f"на {fmt_date(a.check_date)}"
    vat = f" ({fv.notes[0]})" if key == "amount" and fv.notes and "НДС" in fv.notes[0] else ""

    if fv.state == FieldState.NO_DATA:
        ans = Answer(question, "no_data", f"{subject}: нет данных")
        if fv.evidence:
            ans.lines.append(Line("Что сказано в договоре: «" + fv.evidence.quote + "»", ev))
        ans.lines += [Line(n, tone="warn") for n in fv.notes]
    elif fv.state == FieldState.NEEDS_REVIEW:
        shown = spec.fmt(fv.value) if fv.value is not None else "не определено"
        ans = Answer(question, "needs_review",
                     f"{subject} {on_date}: требует проверки ({shown})")
        ans.lines += [Line(n, ev, "warn") for n in fv.notes]
    else:
        ans = Answer(question, "ok", f"{subject} {on_date}: {spec.fmt(fv.value)}{vat}")
        if fv.state == FieldState.AMENDED:
            doc = a.doc(fv.changed_by)
            ans.lines.append(Line(f"Изменено документом «{doc.title}» "
                                  f"(подписано {fmt_date(doc.signed_on)}, в силе с {fmt_date(doc.effective_from)})",
                                  ev + ([doc.status_evidence] if doc.status_evidence else [])))
            for h in fv.history:
                if h["evidence"] is not None:
                    ans.lines.append(Line(f"Прежнее значение: {spec.fmt(h['value'])}", [h["evidence"]], "muted"))
        else:
            ans.lines.append(Line("Значение из договора, действующих изменений нет", ev))
        for n in fv.notes:
            if not (vat and n in vat):
                ans.lines.append(Line(n, tone="muted"))

    for m in fv.other_mentions:
        if m.value is not None:
            ans.lines.append(Line(f"Не использовано: {spec.fmt(m.value)} — {m.note}", [m.evidence], "muted"))
    _add_pending(ans, a, c, key)
    return ans


def _add_pending(ans: Answer, a: Analysis, c: ContractView, key: str) -> None:
    spec = FIELD_BY_KEY[key]
    for p in c.pending:
        if p.field != key:
            continue
        doc = a.doc(p.doc_id)
        ans.lines.append(Line(
            f"Не учтено: {spec.fmt(p.proposed)} — {PENDING_LABEL[p.kind]} («{doc.title}»)",
            [p.evidence], "warn"))


def delivery_date(a: Analysis, contract_no: str) -> Answer:
    q = f"Какой срок поставки действует по договору {contract_no} на {fmt_date(a.check_date)}?"
    return _field_answer(a, contract_no, "delivery_date", q)


def amount(a: Analysis, contract_no: str) -> Answer:
    q = f"Какова сумма договора {contract_no}?"
    return _field_answer(a, contract_no, "amount", q)


def deliveries_before(a: Analysis, before: date) -> Answer:
    q = (f"По каким договорам поставка запланирована раньше {fmt_date(before)} "
         "и есть ли несогласованные изменения срока?")
    early, unknown = [], []
    for c in a.contracts:
        fv = c.fields["delivery_date"]
        if fv.value is None or fv.state == FieldState.NEEDS_REVIEW:
            unknown.append(c)
        elif fv.value < before:
            early.append(c)

    if early:
        head = "Раньше {}: {}".format(fmt_date(before), ", ".join(
            f"{c.contract_no} ({fmt_date(c.fields['delivery_date'].value)})" for c in early))
    else:
        head = f"Договоров со сроком поставки раньше {fmt_date(before)} нет"
    ans = Answer(q, "needs_review" if unknown else "ok", head)

    for c in early:
        fv = c.fields["delivery_date"]
        ev = [fv.evidence] if fv.evidence else []
        how = "изменён действующим ДС" if fv.state == FieldState.AMENDED else "по договору"
        ans.lines.append(Line(f"{c.contract_no}: срок {fmt_date(fv.value)} ({how})", ev))
        pend = [p for p in c.pending if p.field == "delivery_date"]
        if not pend:
            ans.lines.append(Line(f"{c.contract_no}: несогласованных изменений срока нет", tone="muted"))
        for p in pend:
            later = " — уже после " + fmt_date(before) if p.proposed and p.proposed >= before else ""
            ans.lines.append(Line(
                f"{c.contract_no}: предлагается {fmt_date(p.proposed)}{later} — {PENDING_LABEL[p.kind]}, "
                "в расчёте не учтено", [p.evidence], "warn"))
    # Несогласованные изменения срока по остальным договорам: на ответ не влияют,
    # но закупщику важно знать о них (особенно если перенос — на более ранний срок).
    for c in a.contracts:
        if c in early or c in unknown:
            continue
        for p in c.pending:
            if p.field != "delivery_date":
                continue
            earlier = p.proposed is not None and p.proposed < before
            ans.lines.append(Line(
                f"{c.contract_no}: действует {fmt_date(c.fields['delivery_date'].value)}, предлагается "
                f"{fmt_date(p.proposed)}{' — раньше ' + fmt_date(before) if earlier else ''} — "
                f"{PENDING_LABEL[p.kind]}, не учтено", [p.evidence], "warn" if earlier else "muted"))
    for c in unknown:
        fv = c.fields["delivery_date"]
        ans.lines.append(Line(f"{c.contract_no}: срок поставки не определён — проверьте вручную",
                              [fv.evidence] if fv.evidence else [], "warn"))
    return ans
