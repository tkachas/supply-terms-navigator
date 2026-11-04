"""CLI.

    python -m terms_navigator serve [--port 8000]
    python -m terms_navigator check documents/ [--date 2026-03-10] [--json]
    python -m terms_navigator ask documents/ amount STN-102
    python -m terms_navigator ask documents/ delivery_date STN-101
    python -m terms_navigator ask documents/ deliveries_before 2026-04-01
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date

from . import serialize
from .fields import FIELDS
from .model import FieldState
from .server import serve
from .service import DEFAULT_CHECK_DATE, analyze_dir, ask

STATE_MARK = {FieldState.AMENDED: " *", FieldState.NEEDS_REVIEW: " ?", FieldState.NO_DATA: "", FieldState.ORIGINAL: ""}


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    p = argparse.ArgumentParser(prog="terms_navigator", description="Сверка условий договоров поставки")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("serve", help="запустить веб-интерфейс")
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--port", type=int, default=8000)

    for name in ("check", "ask"):
        c = sub.add_parser(name)
        c.add_argument("folder", help="папка с TXT-файлами")
        c.add_argument("--date", type=date.fromisoformat, default=DEFAULT_CHECK_DATE, help="дата проверки, ГГГГ-ММ-ДД")
        c.add_argument("--json", action="store_true")
        if name == "ask":
            c.add_argument("type", choices=["amount", "delivery_date", "deliveries_before"])
            c.add_argument("arg", help="номер договора или дата ГГГГ-ММ-ДД")

    args = p.parse_args(argv)
    if args.cmd == "serve":
        serve(args.host, args.port)
        return 0

    a = analyze_dir(args.folder, args.date)
    if args.cmd == "ask":
        q = {"type": args.type, "contract" if args.type != "deliveries_before" else "before": args.arg}
        ans = ask(a, q)
        if args.json:
            print(json.dumps(serialize.answer(ans), ensure_ascii=False, indent=2))
            return 0
        print(ans.question)
        print("→ " + ans.headline)
        for line in ans.lines:
            refs = "; ".join(f"{e.file}, {e.label or 'абз. ' + str(e.para + 1)}" for e in line.evidence)
            print(f"  · {line.text}" + (f"  [{refs}]" if refs else ""))
        return 0

    if args.json:
        print(json.dumps(serialize.analysis(a), ensure_ascii=False, indent=2))
        return 0
    print(f"Дата проверки: {a.check_date:%d.%m.%Y}   (* — изменено ДС, ? — требует проверки)")
    cols = FIELDS
    print(" | ".join(["Договор"] + [f.title for f in cols] + ["Несогласовано"]))
    for c in a.contracts:
        row = [c.contract_no + (" ?" if c.needs_review else "")]
        for f in cols:
            fv = c.fields[f.key]
            row.append(f.fmt(fv.value) + STATE_MARK[fv.state])
        row.append(str(len(c.pending)))
        print(" | ".join(row))
    for i in a.issues:
        where = a.doc(i.doc_id).file + ": " if i.doc_id else ""
        print(f"! {where}{i.message}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
