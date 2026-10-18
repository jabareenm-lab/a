"""אינדקס חיפוש מקומי (SQLite FTS5 + BM25) מעל ספריית ההנחיות. עדכון אינקרמנטלי לפי תאריך שינוי וגודל."""
from __future__ import annotations

import json
import logging
import sqlite3
import threading
import time
from contextlib import contextmanager
from pathlib import Path

from . import hebrew
from .extract import extract

log = logging.getLogger(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS documents (
    id INTEGER PRIMARY KEY,
    path TEXT UNIQUE NOT NULL,
    title TEXT NOT NULL,
    folder TEXT NOT NULL,
    ext TEXT NOT NULL,
    mtime REAL NOT NULL,
    size INTEGER NOT NULL,
    sections INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL,          -- ok | no_text | error
    error TEXT,
    web_url TEXT,
    indexed_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS chunks (
    id INTEGER PRIMARY KEY,
    doc_id INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    page INTEGER NOT NULL,
    label TEXT NOT NULL,
    text TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS chunks_doc ON chunks(doc_id, page);
CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(norm, title, tokenize='unicode61 remove_diacritics 2');
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
"""

MANIFEST = ".sharepoint_manifest.json"


class Index:
    def __init__(self, db_path: Path):
        self.db_path = db_path
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._write_lock = threading.Lock()
        self.progress: dict = {"running": False}
        with self._conn() as c:
            c.executescript(SCHEMA)

    @contextmanager
    def _conn(self):
        c = sqlite3.connect(self.db_path, timeout=30)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA foreign_keys = ON")
        try:
            yield c
            c.commit()
        finally:
            c.close()

    # ---------- בנייה ----------

    def rebuild(self, folders: list[Path], extensions: list[str]) -> dict:
        if not self._write_lock.acquire(blocking=False):
            return {"skipped": "indexing already running"}
        try:
            return self._rebuild(folders, extensions)
        finally:
            self.progress["running"] = False
            self._write_lock.release()

    def _rebuild(self, folders: list[Path], extensions: list[str]) -> dict:
        files: dict[str, tuple[Path, Path]] = {}
        web_urls: dict[str, str] = {}
        for root in folders:
            if not root.exists():
                log.warning("folder not found: %s", root)
                continue
            manifest = root / MANIFEST
            if manifest.exists():
                for rel, info in json.loads(manifest.read_text(encoding="utf-8")).items():
                    web_urls[str((root / rel).resolve())] = info.get("web_url", "")
            for p in root.rglob("*"):
                if p.is_file() and p.suffix.lower() in extensions and not p.name.startswith(("~$", ".")):
                    files[str(p.resolve())] = (p, root)

        stats = {"added": 0, "updated": 0, "removed": 0, "unchanged": 0, "failed": 0}
        self.progress = {"running": True, "done": 0, "total": len(files), "current": ""}
        with self._conn() as c:
            known = {r["path"]: r for r in c.execute("SELECT id, path, mtime, size FROM documents")}
            for gone in set(known) - set(files):
                self._delete_doc(c, known[gone]["id"])
                stats["removed"] += 1
            c.commit()

            for key, (p, root) in sorted(files.items()):
                self.progress["current"] = p.name
                st = p.stat()
                old = known.get(key)
                if old and abs(old["mtime"] - st.st_mtime) < 1 and old["size"] == st.st_size:
                    stats["unchanged"] += 1
                    self.progress["done"] += 1
                    continue
                if old:
                    self._delete_doc(c, old["id"])
                ok = self._add_doc(c, p, root, st, web_urls.get(key))
                stats["updated" if old else "added"] += 1
                if not ok:
                    stats["failed"] += 1
                c.commit()
                self.progress["done"] += 1
            c.execute(
                "INSERT OR REPLACE INTO meta(key, value) VALUES ('last_indexed', ?)",
                (str(time.time()),),
            )
        log.info("index rebuild: %s", stats)
        return stats

    def _delete_doc(self, c: sqlite3.Connection, doc_id: int) -> None:
        c.execute("DELETE FROM chunks_fts WHERE rowid IN (SELECT id FROM chunks WHERE doc_id = ?)", (doc_id,))
        c.execute("DELETE FROM chunks WHERE doc_id = ?", (doc_id,))
        c.execute("DELETE FROM documents WHERE id = ?", (doc_id,))

    def _add_doc(self, c, p: Path, root: Path, st, web_url: str | None) -> bool:
        try:
            folder = str(p.parent.relative_to(root))
        except ValueError:
            folder = str(p.parent)
        folder = "" if folder == "." else folder
        status, error, sections = "ok", None, []
        try:
            sections = extract(p)
            if not sections:
                status = "no_text"
        except Exception as e:
            status, error = "error", f"{type(e).__name__}: {e}"
            log.warning("failed to extract %s: %s", p, error)
        cur = c.execute(
            "INSERT INTO documents(path, title, folder, ext, mtime, size, sections, status, error, web_url, indexed_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (str(p.resolve()), p.stem, folder, p.suffix.lower(), st.st_mtime, st.st_size,
             len(sections), status, error, web_url, time.time()),
        )
        doc_id = cur.lastrowid
        title_norm = hebrew.index_text(p.stem + " " + folder)
        for s in sections:
            cid = c.execute(
                "INSERT INTO chunks(doc_id, page, label, text) VALUES (?,?,?,?)",
                (doc_id, s.page, s.label, s.text),
            ).lastrowid
            c.execute(
                "INSERT INTO chunks_fts(rowid, norm, title) VALUES (?,?,?)",
                (cid, hebrew.index_text(s.text), title_norm),
            )
        return status != "error"

    # ---------- שליפה ----------

    def search(self, query: str, limit: int = 8, doc_id: int | None = None) -> list[dict]:
        q = hebrew.fts_query(query)
        if not q:
            return []
        sql = (
            "SELECT ch.id, ch.doc_id, ch.page, ch.label, ch.text, d.title, d.folder,"
            " bm25(chunks_fts, 1.0, 3.0) AS score"
            " FROM chunks_fts JOIN chunks ch ON ch.id = chunks_fts.rowid"
            " JOIN documents d ON d.id = ch.doc_id"
            " WHERE chunks_fts MATCH ?"
        )
        args: list = [q]
        if doc_id is not None:
            sql += " AND ch.doc_id = ?"
            args.append(doc_id)
        sql += " ORDER BY score LIMIT ?"
        args.append(limit)
        with self._conn() as c:
            rows = c.execute(sql, args).fetchall()
        return [
            {
                "doc_id": r["doc_id"],
                "title": r["title"],
                "folder": r["folder"],
                "page": r["page"],
                "location": r["label"],
                "snippet": snippet(r["text"], query),
            }
            for r in rows
        ]

    def read(self, doc_id: int, page_from: int, page_to: int) -> dict | None:
        with self._conn() as c:
            d = c.execute("SELECT id, title, folder, sections, ext FROM documents WHERE id = ?", (doc_id,)).fetchone()
            if not d:
                return None
            rows = c.execute(
                "SELECT page, label, text FROM chunks WHERE doc_id = ? AND page BETWEEN ? AND ? ORDER BY page",
                (doc_id, page_from, page_to),
            ).fetchall()
        return {
            "doc_id": d["id"],
            "title": d["title"],
            "folder": d["folder"],
            "total_sections": d["sections"],
            "unit": "עמודים" if d["ext"] == ".pdf" else "קטעים",
            "pages": [{"page": r["page"], "location": r["label"], "text": r["text"]} for r in rows],
        }

    def list_documents(self, contains: str = "") -> list[dict]:
        with self._conn() as c:
            rows = c.execute(
                "SELECT id, title, folder, sections, status FROM documents ORDER BY folder, title"
            ).fetchall()
        needle = hebrew.tokens(contains)
        out = []
        for r in rows:
            hay = " ".join(hebrew.tokens(f"{r['folder']} {r['title']}"))
            if all(n in hay for n in needle):
                out.append({"doc_id": r["id"], "title": r["title"], "folder": r["folder"],
                            "sections": r["sections"], "status": r["status"]})
        return out

    def document(self, doc_id: int) -> dict | None:
        with self._conn() as c:
            r = c.execute("SELECT * FROM documents WHERE id = ?", (doc_id,)).fetchone()
        return dict(r) if r else None

    def status(self) -> dict:
        with self._conn() as c:
            counts = {r["status"]: r["n"] for r in c.execute("SELECT status, COUNT(*) n FROM documents GROUP BY status")}
            problems = [dict(r) for r in c.execute(
                "SELECT title, folder, status, error FROM documents WHERE status != 'ok' ORDER BY title LIMIT 200")]
            chunks = c.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
            last = c.execute("SELECT value FROM meta WHERE key = 'last_indexed'").fetchone()
        return {
            "documents": sum(counts.values()),
            "ok": counts.get("ok", 0),
            "no_text": counts.get("no_text", 0),
            "errors": counts.get("error", 0),
            "chunks": chunks,
            "last_indexed": float(last[0]) if last else None,
            "problems": problems,
            "progress": self.progress,
        }


def snippet(text: str, query: str, width: int = 700) -> str:
    """חלון טקסט סביב האזור עם הכי הרבה התאמות למילות השאלה."""
    plain = text.translate(hebrew._FINALS).lower()
    terms = {v for t in hebrew.tokens(query) for v in hebrew.variants(t) if len(v) >= 2}
    hits = sorted(i for t in terms for i in _find_all(plain, t))
    if not hits:
        return text[:width]
    best, best_n = hits[0], 0
    for h in hits:
        n = sum(1 for x in hits if h <= x < h + width)
        if n > best_n:
            best, best_n = h, n
    start = max(0, best - width // 4)
    out = text[start:start + width]
    return ("…" if start else "") + out + ("…" if start + width < len(text) else "")


def _find_all(s: str, sub: str):
    i = s.find(sub)
    while i != -1:
        yield i
        i = s.find(sub, i + 1)
