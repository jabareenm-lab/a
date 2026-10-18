"""סנכרון תיקיית ההנחיות מ-SharePoint (Microsoft Graph) לתיקיית מטמון מקומית שהאינדקס קורא ממנה.

שתי שיטות הזדהות:
  device_code   - כניסה עם המשתמש האישי שלך (פעם אחת: python run.py sharepoint-login)
  client_secret - אפליקציה ארגונית עם הרשאת Sites.Read.All (דורש מנהל IT)
"""
from __future__ import annotations

import base64
import json
import logging
from pathlib import Path
from urllib.parse import quote, urlparse

import msal
import requests

from .config import Config
from .index import MANIFEST

log = logging.getLogger(__name__)

GRAPH = "https://graph.microsoft.com/v1.0"
DELEGATED_SCOPES = ["Files.Read.All", "Sites.Read.All"]


class SharePointError(RuntimeError):
    pass


class SharePointSync:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.sp = cfg.sharepoint
        self.cache_dir = cfg.cache_dir
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._token_cache_path = cfg.db_path.parent / "msal_token_cache.json"
        self._token_cache = msal.SerializableTokenCache()
        if self._token_cache_path.exists():
            self._token_cache.deserialize(self._token_cache_path.read_text())

    # ---------- הזדהות ----------

    def _app(self):
        authority = f"https://login.microsoftonline.com/{self.sp.tenant_id or 'organizations'}"
        if self.sp.auth == "client_secret":
            return msal.ConfidentialClientApplication(
                self.sp.client_id, authority=authority,
                client_credential=self.sp.client_secret, token_cache=self._token_cache,
            )
        return msal.PublicClientApplication(self.sp.client_id, authority=authority, token_cache=self._token_cache)

    def _save_cache(self):
        if self._token_cache.has_state_changed:
            self._token_cache_path.parent.mkdir(parents=True, exist_ok=True)
            self._token_cache_path.write_text(self._token_cache.serialize())

    def login_interactive(self) -> str:
        """זרימת Device Code - מדפיסה קוד שמזינים ב-https://microsoft.com/devicelogin."""
        app = self._app()
        flow = app.initiate_device_flow(scopes=DELEGATED_SCOPES)
        if "user_code" not in flow:
            raise SharePointError(f"device flow failed: {flow}")
        print(flow["message"], flush=True)
        result = app.acquire_token_by_device_flow(flow)
        self._save_cache()
        if "access_token" not in result:
            raise SharePointError(result.get("error_description", str(result)))
        return result.get("id_token_claims", {}).get("preferred_username", "")

    def _token(self) -> str:
        app = self._app()
        if self.sp.auth == "client_secret":
            result = app.acquire_token_for_client(scopes=["https://graph.microsoft.com/.default"])
        else:
            accounts = app.get_accounts()
            if not accounts:
                raise SharePointError("לא בוצעה התחברות ל-SharePoint. הריצו: python run.py sharepoint-login")
            result = app.acquire_token_silent(DELEGATED_SCOPES, account=accounts[0])
            if not result:
                raise SharePointError("פג תוקף ההתחברות ל-SharePoint. הריצו שוב: python run.py sharepoint-login")
        self._save_cache()
        if "access_token" not in result:
            raise SharePointError(result.get("error_description", str(result)))
        return result["access_token"]

    def _get(self, url: str, **kw) -> requests.Response:
        r = requests.get(url, headers={"Authorization": f"Bearer {self._token()}"}, timeout=60, **kw)
        if r.status_code >= 400:
            raise SharePointError(f"Graph {r.status_code}: {r.text[:300]}")
        return r

    # ---------- איתור התיקייה ----------

    def _resolve_folder(self) -> tuple[str, str]:
        """מחזיר (drive_id, item_id) של תיקיית ההנחיות."""
        if self.sp.folder_url:
            enc = base64.urlsafe_b64encode(self.sp.folder_url.encode()).decode().rstrip("=")
            item = self._get(f"{GRAPH}/shares/u!{enc}/driveItem").json()
            return item["parentReference"]["driveId"], item["id"]
        if not self.sp.site_url:
            raise SharePointError("יש להגדיר sharepoint.folder_url או sharepoint.site_url בקובץ config.yaml")
        u = urlparse(self.sp.site_url)
        site = self._get(f"{GRAPH}/sites/{u.hostname}:{u.path.rstrip('/') or '/'}").json()
        drives = self._get(f"{GRAPH}/sites/{site['id']}/drives").json()["value"]
        if self.sp.drive_name:
            drive = next((d for d in drives if d["name"] == self.sp.drive_name), None)
            if not drive:
                raise SharePointError(f"ספרייה '{self.sp.drive_name}' לא נמצאה. קיימות: {[d['name'] for d in drives]}")
        else:
            drive = self._get(f"{GRAPH}/sites/{site['id']}/drive").json()
        path = self.sp.folder_path.strip("/")
        url = f"{GRAPH}/drives/{drive['id']}/root" + (f":/{quote(path)}" if path else "")
        return drive["id"], self._get(url).json()["id"]

    def _walk(self, drive_id: str, item_id: str, rel: str = ""):
        url = f"{GRAPH}/drives/{drive_id}/items/{item_id}/children?$top=200"
        while url:
            data = self._get(url).json()
            for it in data.get("value", []):
                child_rel = f"{rel}/{it['name']}" if rel else it["name"]
                if "folder" in it:
                    yield from self._walk(drive_id, it["id"], child_rel)
                elif "file" in it:
                    yield child_rel, it
            url = data.get("@odata.nextLink")

    # ---------- סנכרון ----------

    def sync(self) -> dict:
        drive_id, folder_id = self._resolve_folder()
        manifest_path = self.cache_dir / MANIFEST
        old = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.exists() else {}
        new: dict = {}
        stats = {"downloaded": 0, "unchanged": 0, "removed": 0, "skipped": 0}
        exts = set(self.cfg.extensions)
        for rel, it in self._walk(drive_id, folder_id):
            if Path(rel).suffix.lower() not in exts:
                stats["skipped"] += 1
                continue
            local = self.cache_dir / rel
            entry = {"id": it["id"], "etag": it.get("eTag"), "web_url": it.get("webUrl", "")}
            if old.get(rel, {}).get("etag") == entry["etag"] and local.exists():
                stats["unchanged"] += 1
            else:
                local.parent.mkdir(parents=True, exist_ok=True)
                r = self._get(f"{GRAPH}/drives/{drive_id}/items/{it['id']}/content", stream=True)
                tmp = local.with_suffix(local.suffix + ".part")
                with open(tmp, "wb") as f:
                    for chunk in r.iter_content(1 << 16):
                        f.write(chunk)
                tmp.replace(local)
                stats["downloaded"] += 1
            new[rel] = entry
        for rel in set(old) - set(new):
            (self.cache_dir / rel).unlink(missing_ok=True)
            stats["removed"] += 1
        manifest_path.write_text(json.dumps(new, ensure_ascii=False, indent=1), encoding="utf-8")
        log.info("sharepoint sync: %s", stats)
        return stats
