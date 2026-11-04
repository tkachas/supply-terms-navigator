"""Analysis/Answer → JSON для веб-интерфейса и CLI (--json)."""
from __future__ import annotations

from dataclasses import asdict
from datetime import date
from decimal import Decimal
from typing import Any

from .fields import FIELD_BY_KEY, FIELDS
from .model import Analysis, Document, Evidence, FieldValue
from .queries import PENDING_LABEL, Answer

KIND_LABEL = {"contract": "Договор", "amendment": "Допсоглашение", "letter": "Письмо", "unknown": "Не распознан"}
SIGN_LABEL = {"signed": "подписан", "unsigned": "не подписан", "partial": "подписание не подтверждено",
              "unknown": "статус не указан", "n/a": "—"}


def _plain(v: Any) -> Any:
    if isinstance(v, date):
        return v.isoformat()
    if isinstance(v, Decimal):
        return str(v)
    return v


def evidence(e: Evidence | None) -> dict | None:
    return asdict(e) if e else None


def _field(fv: FieldValue) -> dict:
    spec = FIELD_BY_KEY[fv.field]
    return {
        "field": fv.field,
        "title": spec.title,
        "state": fv.state.value,
        "value": _plain(fv.value),
        "display": spec.fmt(fv.value),
        "evidence": evidence(fv.evidence),
        "changed_by": fv.changed_by,
        "history": [{"display": spec.fmt(h["value"]), "evidence": evidence(h["evidence"])} for h in fv.history],
        "notes": fv.notes,
        "other_mentions": [{"display": spec.fmt(m.value), "note": m.note, "evidence": evidence(m.evidence)}
                           for m in fv.other_mentions if m.value is not None],
    }


def document(d: Document) -> dict:
    return {
        "doc_id": d.doc_id,
        "file": d.file,
        "title": d.title,
        "kind": d.kind.value,
        "kind_label": KIND_LABEL[d.kind.value],
        "number": d.number,
        "contract_refs": d.contract_refs,
        "sign_status": d.sign_status.value,
        "sign_label": SIGN_LABEL[d.sign_status.value],
        "signed_on": _plain(d.signed_on),
        "effective_from": _plain(d.effective_from),
        "warnings": d.warnings,
        "paragraphs": [{"idx": p.idx, "text": p.text, "label": p.label, "header": p.is_header}
                       for p in d.paragraphs],
    }


def analysis(a: Analysis) -> dict:
    return {
        "check_date": a.check_date.isoformat(),
        "fields": [{"key": f.key, "title": f.title} for f in FIELDS],
        "documents": [document(d) for d in a.documents],
        "issues": [_issue(i) for i in a.issues],
        "contracts": [
            {
                "contract_no": c.contract_no,
                "doc_id": c.doc_id,
                "needs_review": c.needs_review,
                "fields": {k: _field(v) for k, v in c.fields.items()},
                "related": [asdict(r) for r in c.related],
                "pending": [
                    {
                        "field": p.field,
                        "field_title": FIELD_BY_KEY[p.field].title,
                        "proposed": FIELD_BY_KEY[p.field].fmt(p.proposed),
                        "current": FIELD_BY_KEY[p.field].fmt(p.current),
                        "doc_id": p.doc_id,
                        "kind": p.kind.value,
                        "kind_label": PENDING_LABEL[p.kind],
                        "evidence": evidence(p.evidence),
                    }
                    for p in c.pending
                ],
                "issues": [_issue(i) for i in c.issues],
            }
            for c in a.contracts
        ],
    }


def _issue(i) -> dict:
    return {"severity": i.severity, "message": i.message, "doc_id": i.doc_id, "evidence": evidence(i.evidence)}


def answer(ans: Answer) -> dict:
    return {
        "question": ans.question,
        "status": ans.status,
        "headline": ans.headline,
        "lines": [{"text": l.text, "tone": l.tone, "evidence": [evidence(e) for e in l.evidence]}
                  for l in ans.lines],
    }

