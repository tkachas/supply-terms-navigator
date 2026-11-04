"""Низкоуровневая работа с текстом: декодирование, абзацы, предложения, даты, суммы."""
from __future__ import annotations

import hashlib
import re
from datetime import date
from decimal import Decimal

from .model import Paragraph

# --- декодирование ---------------------------------------------------------

def decode_bytes(raw: bytes) -> str:
    """TXT из корпоративной среды бывает и в UTF-8, и в Windows-1251."""
    for enc in ("utf-8-sig", "cp1251"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def normalize(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    # неразрывные и узкие пробелы в суммах приводим к обычным
    return text.replace(" ", " ").replace(" ", " ").replace(" ", " ")


def make_doc_id(file: str) -> str:
    return "d" + hashlib.sha1(file.encode("utf-8")).hexdigest()[:8]


# --- абзацы ----------------------------------------------------------------

CLAUSE_RE = re.compile(r"^\s*(\d+(?:\.\d+)*)\.\s")
HEADER_KEY_RE = re.compile(r"^\s*([А-ЯЁA-Z][А-ЯЁа-яёA-Za-z ]{1,30}):\s")


def split_paragraphs(text: str) -> list[Paragraph]:
    """Первый блок (до пустой строки) — шапка: каждая строка отдельный абзац.

    В теле новый абзац начинается с пустой строки, с номера пункта («2.1.»)
    или с поля вида «Ключ: значение». Остальные строки считаются переносом.
    """
    blocks: list[list[str]] = []
    current: list[str] = []
    for line in text.split("\n"):
        if line.strip():
            current.append(line.rstrip())
        elif current:
            blocks.append(current)
            current = []
    if current:
        blocks.append(current)

    paras: list[Paragraph] = []
    for b_idx, block in enumerate(blocks):
        is_header = b_idx == 0
        buf: list[str] = []

        def flush() -> None:
            if buf:
                body = " ".join(s.strip() for s in buf)
                paras.append(Paragraph(len(paras), body, _label(body), is_header))
                buf.clear()

        for line in block:
            starts_new = is_header or CLAUSE_RE.match(line) or HEADER_KEY_RE.match(line)
            if starts_new:
                flush()
            buf.append(line)
        flush()
    return paras


def _label(text: str) -> str | None:
    if m := CLAUSE_RE.match(text):
        return f"п. {m.group(1)}"
    if m := HEADER_KEY_RE.match(text):
        return m.group(1).strip()
    return None


# --- предложения -----------------------------------------------------------

_ABBREV = {"г", "гг", "ул", "д", "т", "им", "п", "пп", "ст", "см", "тыс", "млн", "коп"}


def split_sentences(text: str) -> list[tuple[int, int]]:
    """Возвращает границы предложений (start, end) в исходной строке.

    Учитывает «ёлочки» (внутри цитаты не режем), даты 01.03.2026 и номера
    пунктов 2.1 (точка без пробела после неё не считается концом предложения).
    """
    spans: list[tuple[int, int]] = []
    depth = 0
    start = 0
    n = len(text)
    i = 0
    while i < n:
        ch = text[i]
        if ch == "«":
            depth += 1
        elif ch == "»":
            depth = max(0, depth - 1)
        elif ch in ".!?" and depth == 0:
            j = i + 1
            while j < n and text[j] in "»\"')":
                j += 1
            if j >= n or text[j] == " ":
                k = j
                while k < n and text[k] == " ":
                    k += 1
                word = re.search(r"([А-ЯЁа-яёA-Za-z]+)$", text[start:i])
                is_abbrev = word is not None and word.group(1).lower() in _ABBREV
                # «3.1. Поставщик…» — номер пункта не отдельное предложение
                is_clause_no = re.fullmatch(r"\s*\d+(?:\.\d+)*", text[start:i]) is not None
                if k >= n or (not is_abbrev and not is_clause_no and (text[k].isupper() or text[k] in "«\"")):
                    spans.append((start, j))
                    start = k
                    i = k
                    continue
        i += 1
    if start < n and text[start:].strip():
        spans.append((start, n))
    return [(s, e) for s, e in spans if text[s:e].strip()]


# --- даты и суммы ----------------------------------------------------------

DATE_RE = re.compile(r"(?<![\d.])(\d{1,2})\.(\d{1,2})\.(\d{4})(?![\d])")

AMOUNT_RE = re.compile(
    r"(?<![\d.,])(\d{1,3}(?: \d{3})+|\d+)(?:,(\d{2}))?"   # 1 200 000 / 840000 / 1 200 000,00
    r"\s*(?:\(([^()]*)\)\s*)?"                              # (сумма прописью)
    r"(?:руб(?:лей|ля|ль)?\.?|₽|р\.)",
    re.IGNORECASE,
)


def parse_date(d: str, m: str, y: str) -> date | None:
    try:
        return date(int(y), int(m), int(d))
    except ValueError:
        return None


def iter_dates(text: str):
    for m in DATE_RE.finditer(text):
        value = parse_date(*m.groups())
        if value is not None:
            yield value, m


def parse_iso(s: str) -> date:
    return date.fromisoformat(s)


def fmt_date(d: date | None) -> str:
    return d.strftime("%d.%m.%Y") if d else "нет данных"


def fmt_amount(v) -> str:
    """Decimal/int → «1 380 000 ₽» (копейки показываются, только если они не нулевые)."""
    if v is None:
        return "нет данных"
    d = Decimal(v)
    rub = int(d)
    kop = int((d - rub) * 100)
    s = f"{rub:,}".replace(",", " ")
    return f"{s},{kop:02d} ₽" if kop else f"{s} ₽"
