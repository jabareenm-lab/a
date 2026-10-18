"""נרמול טקסט עברי לחיפוש: ניקוד, אותיות סופיות, תחיליות (ו/ה/ב/כ/ל/מ/ש) ומרכאות בראשי תיבות."""
from __future__ import annotations

import re

_NIQQUD = re.compile(r"[֑-ׇ]")
_FINALS = str.maketrans({"ך": "כ", "ם": "מ", "ן": "נ", "ף": "פ", "ץ": "צ"})
# מרכאות/גרש בתוך מילה (תמ"א, ק"מ, מ'): מסירים כדי שהמילה תישאר טוקן אחד
_INNER_QUOTES = re.compile(r"(?<=[א-ת])[\"'׳״“”’](?=[א-ת])")
_TOKEN = re.compile(r"[\u05D0-\u05EA]+|[a-zA-Z]+|[0-9]+(?:[.,][0-9]+)*")
_PREFIXES = "והבכלמש"
_FINAL_LETTERS = set("ךםןףץ")


def tokens(text: str) -> list[str]:
    text = _NIQQUD.sub("", text)
    text = _INNER_QUOTES.sub("", text)
    return [t.lower().translate(_FINALS) for t in _TOKEN.findall(text)]


def variants(token: str) -> list[str]:
    """הטוקן עצמו + גרסאות ללא 1-2 אותיות תחילית (רק אם נשארות 3 אותיות לפחות)."""
    out = [token]
    t = token
    for _ in range(2):
        if len(t) > 3 and t[0] in _PREFIXES:
            t = t[1:]
            out.append(t)
        else:
            break
    return out


def index_text(text: str) -> str:
    """הטקסט המורחב שנכנס לאינדקס FTS."""
    return " ".join(v for tok in tokens(text) for v in variants(tok))


def fts_query(query: str) -> str:
    """בונה שאילתת FTS5: כל מילה (או אחת מגרסאותיה, כולל התאמת תחילית-מילה) - מחוברות ב-OR, BM25 מדרג."""
    groups = []
    seen = set()
    for tok in tokens(query):
        if tok in seen:
            continue
        seen.add(tok)
        terms = []
        for v in variants(tok):
            terms.append(f'"{v}"')
            if len(v) >= 3:
                terms.append(f'"{v}"*')
        groups.append("(" + " OR ".join(terms) + ")")
    return " OR ".join(groups)


def fix_visual_order(text: str) -> str:
    """חלק מקובצי ה-PDF של משרדי ממשלה שומרים עברית בסדר ויזואלי (הפוך).
    מזהים לפי אותיות סופיות בתחילת מילה, והופכים שורה-שורה (תוך שמירה על מספרים/לטינית)."""
    words = re.findall(r"[א-ת]{2,}", text)
    if not words:
        return text
    starts_final = sum(1 for w in words if w[0] in _FINAL_LETTERS)
    ends_final = sum(1 for w in words if w[-1] in _FINAL_LETTERS)
    if starts_final < 2 or starts_final <= ends_final * 2:
        return text
    fixed = []
    for line in text.splitlines():
        rev = line[::-1]
        # להחזיר רצפים לא-עבריים (מספרים, לטינית) לכיוון המקורי
        rev = re.sub(r"[0-9A-Za-z.,:/%\-+]+", lambda m: m.group(0)[::-1], rev)
        rev = rev.translate(_MIRROR)
        fixed.append(rev)
    return "\n".join(fixed)


_HEB = re.compile(r"[\u05D0-\u05EA]")
_MIRROR = str.maketrans("()[]{}<>", ")(][}{><")


def fix_word_order(text: str) -> str:
    """אותיות תקינות אבל סדר המילים בשורה הפוך (נקודה בתחילת השורה במקום בסופה) - הופכים את סדר המילים."""
    lines = [l.strip() for l in text.splitlines() if _HEB.search(l)]
    starts = sum(1 for l in lines if l[:1] in ".:,;")
    ends = sum(1 for l in lines if l[-1:] in ".:,;")
    if starts < 2 or starts <= ends * 2:
        return text
    out = []
    for line in text.splitlines():
        if _HEB.search(line):
            line = " ".join(w.translate(_MIRROR) for w in reversed(line.split()))
            if line[-1:] not in ".:,;" and line[:1] in ".:,;":
                line = line[1:] + line[0]
        out.append(line)
    return "\n".join(out)
