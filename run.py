"""מהנדס שואל - הפעלה.

    python run.py                    מפעיל את האפליקציה ופותח דפדפן
    python run.py reindex            סנכרון SharePoint + עדכון אינדקס בלבד
    python run.py sharepoint-login   התחברות חד-פעמית ל-SharePoint (Device Code)
    python run.py ask "שאלה"         שאלה מהטרמינל
"""
from __future__ import annotations

import logging
import sys
import threading
import webbrowser

from app.config import load_config


def main(argv: list[str]) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    cfg = load_config()
    cmd = argv[0] if argv else "serve"

    if cmd == "sharepoint-login":
        from app.sharepoint import SharePointSync

        user = SharePointSync(cfg).login_interactive()
        print(f"מחובר ל-SharePoint כ: {user}")
        return 0

    if cmd == "reindex":
        from app.index import Index
        from app.server import refresh

        print(refresh(cfg, Index(cfg.db_path)))
        return 0

    if cmd == "ask":
        from app.agent import Agent
        from app.index import Index

        index = Index(cfg.db_path)
        if not index.status()["documents"]:
            index.rebuild(cfg.all_folders, cfg.extensions)
        for ev in Agent(cfg, index).answer(" ".join(argv[1:]), []):
            if ev["type"] == "delta":
                print(ev["text"], end="", flush=True)
            elif ev["type"] == "status":
                print(f"\n[{ev['text']}]", flush=True)
            elif ev["type"] == "error":
                print(f"\nשגיאה: {ev['message']}")
            elif ev["type"] == "sources":
                print("\n\nמסמכים שנבדקו:")
                for s in ev["sources"]:
                    print(f"  - {s['title']} ({', '.join(p['location'] for p in s['pages'])})")
        print()
        return 0

    if cmd == "serve":
        import uvicorn

        from app.server import create_app

        url = f"http://{'localhost' if cfg.host in ('127.0.0.1', '0.0.0.0') else cfg.host}:{cfg.port}"
        if "--no-browser" not in argv:
            threading.Timer(1.5, lambda: webbrowser.open(url)).start()
        print(f"מהנדס שואל פועל בכתובת {url}  (לסגירה: Ctrl+C)")
        uvicorn.run(create_app(cfg), host=cfg.host, port=cfg.port, log_level="warning")
        return 0

    print(__doc__)
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
