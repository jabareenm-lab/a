"""לולאת הסוכן: Claude מחפש בספריית ההנחיות בעזרת כלים, קורא עמודים רלוונטיים ועונה עם הפניות למקור."""
from __future__ import annotations

import json
import logging
from collections.abc import Iterator

import anthropic

from .config import Config
from .index import Index

log = logging.getLogger(__name__)

FALLBACK_BETA = "server-side-fallback-2026-07-01"
MAX_TOKENS = 32000

TOOLS = [
    {
        "name": "search_guidelines",
        "description": (
            "חיפוש מילות מפתח בכל מסמכי ספריית ההנחיות (PDF/Word/Excel). מחזיר קטעים מדורגים לפי רלוונטיות, "
            "כל אחד עם doc_id, שם המסמך, תיקייה, עמוד וקטע טקסט. החיפוש לפי מילים (לא סמנטי): "
            "חפש כמה פעמים עם ניסוחים שונים, מונחים מקצועיים ונרדפים (למשל 'מעגל תנועה' וגם 'כיכר'), "
            "וצמצם למסמך מסוים עם doc_id כשצריך."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "מילות חיפוש בעברית (או מספר סעיף/טבלה)"},
                "doc_id": {
                    "anyOf": [{"type": "integer"}, {"type": "null"}],
                    "description": "לחיפוש בתוך מסמך אחד בלבד, אחרת null",
                },
                "max_results": {"type": "integer", "description": "1-15, ברירת מחדל 8"},
            },
            "required": ["query", "doc_id", "max_results"],
            "additionalProperties": False,
        },
        "strict": True,
        "eager_input_streaming": True,
    },
    {
        "name": "read_pages",
        "description": (
            "קריאת הטקסט המלא של עמודים (PDF) או קטעים (Word/Excel) ממסמך, כדי לאמת ערכים, טבלאות "
            "והקשר לפני שעונים. עד 6 עמודים בכל קריאה."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "doc_id": {"type": "integer"},
                "page_from": {"type": "integer"},
                "page_to": {"type": "integer"},
            },
            "required": ["doc_id", "page_from", "page_to"],
            "additionalProperties": False,
        },
        "strict": True,
        "eager_input_streaming": True,
    },
    {
        "name": "list_documents",
        "description": "רשימת המסמכים בספרייה (שם, תיקייה, doc_id), עם סינון אופציונלי לפי מילים בשם/תיקייה.",
        "input_schema": {
            "type": "object",
            "properties": {"contains": {"type": "string", "description": "מילים לסינון, או מחרוזת ריקה"}},
            "required": ["contains"],
            "additionalProperties": False,
        },
        "strict": True,
        "eager_input_streaming": True,
    },
]

STATUS_TEXT = {
    "search_guidelines": lambda i: f"מחפש בהנחיות: {i.get('query', '')}",
    "read_pages": lambda i: f"קורא מסמך #{i.get('doc_id')} עמ' {i.get('page_from')}-{i.get('page_to')}",
    "list_documents": lambda i: "סוקר את רשימת המסמכים" + (f": {i['contains']}" if i.get("contains") else ""),
}


class Agent:
    def __init__(self, cfg: Config, index: Index):
        self.cfg = cfg
        self.index = index
        self.client = anthropic.Anthropic()

    # ---------- כלים ----------

    def _run_tool(self, name: str, inp: dict, sources: dict) -> tuple[str, bool]:
        try:
            if name == "search_guidelines":
                q = inp["query"]
                if not isinstance(q, str) or not q.strip():
                    raise ValueError("query must be a non-empty string")
                doc_id = inp.get("doc_id")
                n = max(1, min(15, int(inp.get("max_results") or 8)))
                hits = self.index.search(q, limit=n, doc_id=int(doc_id) if doc_id is not None else None)
                for h in hits:
                    self._track(sources, h["doc_id"], h["page"], h["location"], used=False)
                if not hits:
                    return "לא נמצאו תוצאות. נסה מילים אחרות, צורת יחיד/רבים, מונח נרדף או מספר סעיף.", False
                return json.dumps(hits, ensure_ascii=False), False
            if name == "read_pages":
                doc_id, a, b = int(inp["doc_id"]), int(inp["page_from"]), int(inp["page_to"])
                if b < a:
                    a, b = b, a
                b = min(b, a + 5)
                doc = self.index.read(doc_id, a, b)
                if doc is None:
                    return f"מסמך {doc_id} לא קיים", True
                for p in doc["pages"]:
                    self._track(sources, doc_id, p["page"], p["location"], used=True)
                return json.dumps(doc, ensure_ascii=False), False
            if name == "list_documents":
                docs = self.index.list_documents(str(inp.get("contains") or ""))
                return json.dumps(docs[:300], ensure_ascii=False), False
            return f"unknown tool {name}", True
        except (KeyError, TypeError, ValueError) as e:
            return f"קלט לא תקין לכלי {name}: {e}", True

    def _track(self, sources: dict, doc_id: int, page: int, location: str, used: bool):
        s = sources.setdefault(doc_id, {"pages": {}, "read": False})
        s["pages"].setdefault(page, location)
        s["read"] = s["read"] or used

    def _sources_payload(self, sources: dict) -> list[dict]:
        out = []
        for doc_id, s in sources.items():
            d = self.index.document(doc_id)
            if not d:
                continue
            pages = sorted(s["pages"])
            out.append({
                "doc_id": doc_id,
                "title": d["title"],
                "folder": d["folder"],
                "read": s["read"],
                "pages": [{"page": p, "location": s["pages"][p]} for p in pages[:12]],
                "is_pdf": d["ext"] == ".pdf",
                "web_url": d["web_url"] or None,
            })
        out.sort(key=lambda x: (not x["read"], x["title"]))
        return out

    # ---------- לולאה ----------

    def answer(self, question: str, history: list[dict]) -> Iterator[dict]:
        """מחזיר אירועים: status / delta / discard_draft / sources / done / error."""
        messages: list = [
            {"role": h["role"], "content": h["content"]}
            for h in history
            if h.get("role") in ("user", "assistant") and isinstance(h.get("content"), str) and h["content"].strip()
        ]
        messages.append({"role": "user", "content": question})
        sources: dict = {}
        system = self.cfg.system_prompt()

        for rnd in range(self.cfg.max_tool_rounds + 1):
            last_round = rnd == self.cfg.max_tool_rounds
            msg = None
            for attempt in range(2):
                try:
                    msg = yield from self._stream_round(system, messages, last_round)
                    break
                except ValueError as e:  # JSON חלקי/שבור של קלט כלי (eager streaming) - שולחים שוב
                    log.warning("tool input JSON error, retrying round: %s", e)
                    yield {"type": "discard_draft"}
                except anthropic.APIStatusError as e:
                    yield {"type": "error", "message": f"שגיאת API ({e.status_code}): {e.message}"}
                    return
                except anthropic.APIConnectionError:
                    yield {"type": "error", "message": "אין חיבור לשרת של Anthropic. בדקו חיבור אינטרנט."}
                    return
            if msg is None:
                yield {"type": "error", "message": "המודל החזיר קלט כלי לא תקין פעמיים. נסו לנסח מחדש."}
                return

            if msg.stop_reason == "refusal":
                yield {"type": "error", "message": "המודל סירב לענות על הבקשה הזו."}
                return

            content = echo_safe(msg.content)
            messages.append({"role": "assistant", "content": content})
            tool_uses = [b for b in content if b.type == "tool_use"]

            if msg.stop_reason == "max_tokens" and tool_uses:
                yield {"type": "error", "message": "התשובה נקטעה (חריגה מאורך מרבי)."}
                return
            if not tool_uses or msg.stop_reason != "tool_use":
                yield {"type": "sources", "sources": self._sources_payload(sources)}
                yield {"type": "done", "stop_reason": msg.stop_reason}
                return

            yield {"type": "discard_draft"}
            results = []
            for tu in tool_uses:
                inp = tu.input if isinstance(tu.input, dict) else {}
                yield {"type": "status", "text": STATUS_TEXT.get(tu.name, lambda i: tu.name)(inp)}
                text, is_err = self._run_tool(tu.name, inp, sources)
                results.append({"type": "tool_result", "tool_use_id": tu.id, "content": text, "is_error": is_err})
            messages.append({"role": "user", "content": results})

    def _stream_round(self, system: str, messages: list, last_round: bool):
        kwargs = {}
        if last_round:
            # סבב אחרון: בלי עוד חיפושים - חייב לענות ממה שנאסף
            kwargs["tool_choice"] = {"type": "none"}
        with self.client.beta.messages.stream(
            model=self.cfg.model,
            max_tokens=MAX_TOKENS,
            system=system,
            messages=messages,
            tools=TOOLS,
            thinking={"type": "adaptive"},
            output_config={"effort": self.cfg.effort},
            cache_control={"type": "ephemeral"},
            betas=[FALLBACK_BETA],
            fallbacks="default",
            **kwargs,
        ) as stream:
            for event in stream:
                if event.type == "content_block_delta" and event.delta.type == "text_delta":
                    yield {"type": "delta", "text": event.delta.text}
                elif event.type == "content_block_start" and event.content_block.type == "tool_use":
                    yield {"type": "status", "text": "מחפש במסמכים…"}
            return stream.get_final_message()


_DROP_BEFORE_FALLBACK = {"thinking", "redacted_thinking", "tool_use"}


def echo_safe(content: list) -> list:
    """אחרי fallback באמצע תשובה: משמיטים בלוקי thinking/tool_use שלפני בלוק ה-fallback האחרון."""
    idx = max((i for i, b in enumerate(content) if b.type == "fallback"), default=-1)
    if idx < 0:
        return list(content)
    kept = [b for b in content[:idx] if b.type == "text"]
    return kept + list(content[idx:])
