"""טעינת הגדרות מ-config.yaml ומשתני סביבה (.env)."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent


@dataclass
class SharePointConfig:
    enabled: bool = False
    tenant_id: str = ""
    client_id: str = ""
    # אחת משתי האפשרויות: קישור לתיקייה (מהדפדפן) או site_url + folder_path
    folder_url: str = ""
    site_url: str = ""
    drive_name: str = ""          # ריק = ספריית המסמכים הראשית של האתר
    folder_path: str = ""         # נתיב יחסי בתוך הספרייה, למשל "הנחיות"
    auth: str = "device_code"     # device_code | client_secret
    client_secret: str = ""


@dataclass
class Config:
    model: str = "claude-opus-5-5"
    effort: str = "high"
    max_tool_rounds: int = 10
    local_folders: list[Path] = field(default_factory=list)
    extensions: list[str] = field(
        default_factory=lambda: [".pdf", ".docx", ".xlsx", ".txt", ".md"]
    )
    sharepoint: SharePointConfig = field(default_factory=SharePointConfig)
    db_path: Path = ROOT / "data" / "index.sqlite"
    cache_dir: Path = ROOT / "data" / "sharepoint_cache"
    host: str = "127.0.0.1"
    port: int = 8765
    system_prompt_path: Path = ROOT / "prompts" / "system_he.md"

    @property
    def all_folders(self) -> list[Path]:
        folders = list(self.local_folders)
        if self.sharepoint.enabled:
            folders.append(self.cache_dir)
        return folders

    def system_prompt(self) -> str:
        return self.system_prompt_path.read_text(encoding="utf-8")


def _resolve(p: str) -> Path:
    path = Path(os.path.expandvars(os.path.expanduser(p)))
    return path if path.is_absolute() else ROOT / path


def load_config(path: Path | None = None) -> Config:
    load_dotenv(ROOT / ".env")
    path = path or Path(os.environ.get("MEHANDES_CONFIG", ROOT / "config.yaml"))
    raw: dict = {}
    if path.exists():
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}

    cfg = Config()
    ai = raw.get("anthropic", {}) or {}
    cfg.model = ai.get("model", cfg.model)
    cfg.effort = ai.get("effort", cfg.effort)
    cfg.max_tool_rounds = int(ai.get("max_tool_rounds", cfg.max_tool_rounds))

    src = raw.get("sources", {}) or {}
    cfg.local_folders = [_resolve(p) for p in src.get("local_folders", []) or []]
    if src.get("extensions"):
        cfg.extensions = [e.lower() for e in src["extensions"]]
    sp = src.get("sharepoint", {}) or {}
    cfg.sharepoint = SharePointConfig(
        **{k: v for k, v in sp.items() if k in SharePointConfig.__dataclass_fields__}
    )
    cfg.sharepoint.client_secret = (
        os.environ.get("SHAREPOINT_CLIENT_SECRET") or cfg.sharepoint.client_secret
    )

    idx = raw.get("index", {}) or {}
    if idx.get("db_path"):
        cfg.db_path = _resolve(idx["db_path"])
    if idx.get("cache_dir"):
        cfg.cache_dir = _resolve(idx["cache_dir"])

    srv = raw.get("server", {}) or {}
    cfg.host = srv.get("host", cfg.host)
    cfg.port = int(srv.get("port", cfg.port))
    if raw.get("system_prompt"):
        cfg.system_prompt_path = _resolve(raw["system_prompt"])
    return cfg
