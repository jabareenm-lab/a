"""חילוץ טקסט מקבצים: PDF (לפי עמוד), DOCX (לפי כותרות/קטעים), XLSX (לפי גיליון), TXT/MD."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from .hebrew import fix_visual_order, fix_word_order

log = logging.getLogger(__name__)

CHUNK_CHARS = 1800


@dataclass
class Section:
    page: int          # מספר עמוד (PDF) או מספר קטע (שאר הסוגים), מתחיל ב-1
    label: str         # "עמ' 12" / "סעיף: כותרת" / "גיליון: X"
    text: str


def extract(path: Path) -> list[Section]:
    ext = path.suffix.lower()
    if ext == ".pdf":
        return _pdf(path)
    if ext == ".docx":
        return _docx(path)
    if ext == ".xlsx":
        return _xlsx(path)
    if ext in (".txt", ".md"):
        return _split_plain(path.read_text(encoding="utf-8", errors="replace"))
    return []


def _pdf(path: Path) -> list[Section]:
    # pypdf שומר על סדר לוגי בעברית RTL; pypdfium2 כגיבוי לקבצים ש-pypdf נכשל בהם
    pages: list[str] = []
    try:
        from pypdf import PdfReader

        pages = [(p.extract_text() or "") for p in PdfReader(str(path)).pages]
    except Exception as e:
        log.debug("pypdf failed on %s: %s", path, e)
    if not any(t.strip() for t in pages):
        try:
            import pypdfium2 as pdfium

            doc = pdfium.PdfDocument(str(path))
            try:
                pages = [doc[i].get_textpage().get_text_range() for i in range(len(doc))]
            finally:
                doc.close()
        except Exception as e:
            log.debug("pypdfium2 failed on %s: %s", path, e)
            if not pages:
                raise
    return [
        Section(page=i + 1, label=f"עמ' {i + 1}", text=fix_word_order(fix_visual_order(t)).strip())
        for i, t in enumerate(pages)
        if t and t.strip()
    ]


def _docx(path: Path) -> list[Section]:
    import docx

    d = docx.Document(str(path))
    blocks: list[tuple[str, str]] = []  # (כותרת נוכחית, טקסט)
    heading = ""
    for p in d.paragraphs:
        txt = p.text.strip()
        if not txt:
            continue
        if p.style is not None and p.style.name.lower().startswith(("heading", "כותרת", "title")):
            heading = txt
        blocks.append((heading, txt))
    for t in d.tables:
        for row in t.rows:
            cells = [c.text.strip() for c in row.cells]
            if any(cells):
                blocks.append((heading or "טבלה", " | ".join(cells)))

    sections: list[Section] = []
    buf, cur_head = [], None
    for head, txt in blocks:
        if buf and (head != cur_head or sum(len(b) for b in buf) > CHUNK_CHARS):
            sections.append(_mk(len(sections) + 1, cur_head, buf))
            buf = []
        cur_head = head
        buf.append(txt)
    if buf:
        sections.append(_mk(len(sections) + 1, cur_head, buf))
    return sections


def _mk(n: int, head: str | None, buf: list[str]) -> Section:
    label = f"קטע {n}" + (f" - {head}" if head else "")
    return Section(page=n, label=label, text="\n".join(buf))


def _xlsx(path: Path) -> list[Section]:
    import openpyxl

    wb = openpyxl.load_workbook(str(path), read_only=True, data_only=True)
    out: list[Section] = []
    for ws in wb.worksheets:
        lines = []
        for row in ws.iter_rows(values_only=True):
            vals = [str(v) for v in row if v is not None and str(v).strip()]
            if vals:
                lines.append(" | ".join(vals))
        for part in _windows("\n".join(lines)):
            out.append(Section(page=len(out) + 1, label=f"גיליון: {ws.title}", text=part))
    wb.close()
    return out


def _split_plain(text: str) -> list[Section]:
    return [
        Section(page=i + 1, label=f"קטע {i + 1}", text=part)
        for i, part in enumerate(_windows(text))
    ]


def _windows(text: str) -> list[str]:
    text = text.strip()
    if not text:
        return []
    parts, start = [], 0
    while start < len(text):
        end = min(len(text), start + CHUNK_CHARS)
        if end < len(text):
            nl = text.rfind("\n", start + CHUNK_CHARS // 2, end)
            if nl > 0:
                end = nl
        parts.append(text[start:end].strip())
        start = end
    return [p for p in parts if p]
