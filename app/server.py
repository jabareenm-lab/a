"""שרת מקומי: ממשק הצ'אט, הזרמת תשובות (SSE), סטטוס אינדקס ופתיחת מסמכי מקור."""
from __future__ import annotations

import json
import logging
import os
import threading
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse
from pydantic import BaseModel

from .agent import Agent
from .config import Config, load_config
from .index import Index

log = logging.getLogger(__name__)
STATIC = Path(__file__).parent / "static"


class Turn(BaseModel):
    role: str
    content: str


class AskRequest(BaseModel):
    question: str
    history: list[Turn] = []


def refresh(cfg: Config, index: Index) -> dict:
    """סנכרון SharePoint (אם מוגדר) ואז עדכון האינדקס."""
    result: dict = {}
    if cfg.sharepoint.enabled:
        from .sharepoint import SharePointSync

        try:
            result["sharepoint"] = SharePointSync(cfg).sync()
        except Exception as e:  # לא מפילים את האינדוקס המקומי בגלל SharePoint
            log.error("sharepoint sync failed: %s", e)
            result["sharepoint_error"] = str(e)
    result["index"] = index.rebuild(cfg.all_folders, cfg.extensions)
    return result


def create_app(cfg: Config | None = None) -> FastAPI:
    cfg = cfg or load_config()
    index = Index(cfg.db_path)
    agent = Agent(cfg, index)
    state: dict = {"last_refresh": None}

    def bg_refresh():
        state["last_refresh"] = refresh(cfg, index)

    @asynccontextmanager
    async def lifespan(_app):
        threading.Thread(target=bg_refresh, daemon=True).start()
        yield

    app = FastAPI(title="מהנדס שואל", lifespan=lifespan)

    @app.get("/", response_class=HTMLResponse)
    def home():
        return (STATIC / "index.html").read_text(encoding="utf-8")

    @app.get("/api/status")
    def status():
        return {
            **index.status(),
            "folders": [str(f) for f in cfg.all_folders],
            "sharepoint": cfg.sharepoint.enabled,
            "model": cfg.model,
            "api_key": bool(os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN")),
            "last_refresh": state["last_refresh"],
        }

    @app.post("/api/reindex")
    def reindex():
        if index.progress.get("running"):
            return {"started": False, "reason": "already running"}
        threading.Thread(target=bg_refresh, daemon=True).start()
        return {"started": True}

    @app.get("/api/documents")
    def documents(q: str = ""):
        return index.list_documents(q)

    @app.get("/api/search")
    def search(q: str, limit: int = 10):
        return index.search(q, limit=min(limit, 30))

    @app.get("/api/file/{doc_id}")
    def file(doc_id: int):
        d = index.document(doc_id)
        if not d:
            raise HTTPException(404)
        p = Path(d["path"])
        allowed = [f.resolve() for f in cfg.all_folders]
        if not p.exists() or not any(p.is_relative_to(f) for f in allowed):
            raise HTTPException(404)
        media = "application/pdf" if p.suffix.lower() == ".pdf" else None
        # inline כדי ש-PDF ייפתח בדפדפן ויקפוץ לעמוד (#page=N)
        return FileResponse(p, media_type=media, filename=p.name, content_disposition_type="inline")

    @app.post("/api/ask")
    def ask(req: AskRequest):
        def events():
            try:
                for ev in agent.answer(req.question, [t.model_dump() for t in req.history[-20:]]):
                    yield f"data: {json.dumps(ev, ensure_ascii=False)}\n\n"
            except Exception as e:
                log.exception("ask failed")
                yield f"data: {json.dumps({'type': 'error', 'message': str(e)}, ensure_ascii=False)}\n\n"

        return StreamingResponse(events(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    return app
