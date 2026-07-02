#!/usr/bin/env python3
"""
vanta_client.py — Vanta Manage API REST client for evidence collection.

Reads OAuth credentials from ~/.vanta/credentials.json.
Supports listing pending documents/tests, uploading evidence, and querying frameworks.
Does NOT submit documents — evidence stays in draft for human review.

Region resolution order: VANTA_REGION env var -> config.yaml (vanta_region) -> "us".

Usage:
  python3 vanta_client.py list-pending [--framework soc2]
  python3 vanta_client.py list-tests [--framework soc2]
  python3 vanta_client.py list-frameworks
  python3 vanta_client.py get-document <DOC_ID>
  python3 vanta_client.py upload <DOC_ID> <FILE_PATH> [--description "..."] [--effective-date "2026-01-01"]
  python3 vanta_client.py create-document --title "..." [--description "..."]
"""

import argparse
import json
import mimetypes
import os
import sys
import time
from pathlib import Path

import requests

try:
    import config as _config
except Exception:  # pragma: no cover
    _config = None

CREDS_PATH = Path.home() / ".vanta" / "credentials.json"
TOKEN_CACHE = Path.home() / ".vanta" / ".token_cache.json"

REGION_URLS = {
    "us": "https://api.vanta.com",
    "eu": "https://api.vanta.com",  # Vanta uses same base; region in OAuth
    "gov": "https://api.vanta-gov.com",
}

SCOPES = "vanta-api.all:read vanta-api.all:write vanta-api.documents:upload"


def _resolve_region() -> str:
    env = os.environ.get("VANTA_REGION")
    if env:
        return env.lower()
    if _config is not None:
        try:
            return _config.vanta_region()
        except Exception:
            pass
    return "us"


class VantaClient:
    def __init__(self):
        self.creds = self._load_credentials()
        self.region = _resolve_region()
        base = REGION_URLS.get(self.region, REGION_URLS["us"])
        self.base_url = f"{base}/v1"
        self.token_url = f"{base}/oauth/token"
        self._token = None
        self._token_expiry = 0

    def _load_credentials(self) -> dict:
        creds_path = os.environ.get("VANTA_ENV_FILE", str(CREDS_PATH))
        p = Path(creds_path)
        if not p.exists():
            print(json.dumps({"error": f"Credentials not found at {p}"}))
            sys.exit(1)
        return json.loads(p.read_text())

    def _get_token(self) -> str:
        if self._token and time.time() < self._token_expiry - 60:
            return self._token

        if TOKEN_CACHE.exists():
            try:
                cached = json.loads(TOKEN_CACHE.read_text())
                if time.time() < cached.get("expiry", 0) - 60:
                    self._token = cached["access_token"]
                    self._token_expiry = cached["expiry"]
                    return self._token
            except (json.JSONDecodeError, KeyError):
                pass

        resp = requests.post(self.token_url, json={
            "client_id": self.creds["client_id"],
            "client_secret": self.creds["client_secret"],
            "scope": SCOPES,
            "grant_type": "client_credentials",
        }, timeout=15)

        if resp.status_code != 200:
            print(json.dumps({
                "error": "OAuth token request failed",
                "status": resp.status_code,
                "body": resp.text,
            }))
            sys.exit(1)

        data = resp.json()
        self._token = data["access_token"]
        self._token_expiry = time.time() + data.get("expires_in", 3600)

        TOKEN_CACHE.write_text(json.dumps({
            "access_token": self._token,
            "expiry": self._token_expiry,
        }))

        return self._token

    def _headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self._get_token()}",
            "Accept": "application/json",
        }

    def _get(self, path: str, params: dict | None = None) -> requests.Response:
        resp = requests.get(f"{self.base_url}{path}", headers=self._headers(),
                            params=params, timeout=30)
        if resp.status_code == 401:
            self._token = None
            TOKEN_CACHE.unlink(missing_ok=True)
            resp = requests.get(f"{self.base_url}{path}", headers=self._headers(),
                                params=params, timeout=30)
        return resp

    def _post(self, path: str, **kwargs) -> requests.Response:
        resp = requests.post(f"{self.base_url}{path}", headers=self._headers(),
                             timeout=60, **kwargs)
        if resp.status_code == 401:
            self._token = None
            TOKEN_CACHE.unlink(missing_ok=True)
            resp = requests.post(f"{self.base_url}{path}", headers=self._headers(),
                                 timeout=60, **kwargs)
        return resp

    def _paginate(self, path: str, params: dict | None = None) -> list:
        params = dict(params or {})
        params.setdefault("pageSize", 100)
        all_items = []

        while True:
            resp = self._get(path, params)
            if resp.status_code != 200:
                return [{"error": resp.status_code, "body": resp.text}]
            data = resp.json()
            results = data.get("results", data)
            items = results.get("data", [])
            all_items.extend(items)
            page_info = results.get("pageInfo", {})
            if page_info.get("hasNextPage") and page_info.get("endCursor"):
                params["pageCursor"] = page_info["endCursor"]
            else:
                break

        return all_items

    # ── Public API ────────────────────────────────────────────────────

    def list_frameworks(self) -> list:
        return self._paginate("/frameworks")

    def list_pending_documents(self, framework: str | None = None) -> list:
        params = {}
        if framework:
            params["frameworkMatchesAny"] = framework
        docs = self._paginate("/documents", params)
        return [d for d in docs if isinstance(d, dict)
                and d.get("uploadStatus") in ("Needs document", "Needs update")]

    def list_all_documents(self, framework: str | None = None) -> list:
        params = {}
        if framework:
            params["frameworkMatchesAny"] = framework
        return self._paginate("/documents", params)

    def list_failing_tests(self, framework: str | None = None) -> list:
        params = {}
        if framework:
            params["frameworkFilter"] = framework
        tests = self._paginate("/tests", params)
        return [t for t in tests if isinstance(t, dict)
                and t.get("latestFlipResult", {}).get("outcome") != "PASS"]

    def get_document(self, doc_id: str) -> dict:
        resp = self._get(f"/documents/{doc_id}")
        if resp.status_code != 200:
            return {"error": resp.status_code, "body": resp.text}
        return resp.json()

    def create_document(self, title: str, description: str = "",
                        cadence: str = "P1Y",
                        reminder_window: str = "P1M") -> dict:
        resp = self._post("/documents", json={
            "title": title,
            "description": description,
            "timeSensitivity": "MOST_RECENT",
            "cadence": cadence,
            "reminderWindow": reminder_window,
            "isSensitive": False,
        })
        if resp.status_code not in (200, 201):
            return {"error": resp.status_code, "body": resp.text}
        return resp.json()

    def upload_evidence(self, doc_id: str, file_path: str,
                        description: str = "", effective_date: str = "") -> dict:
        p = Path(file_path)
        if not p.exists():
            return {"error": f"File not found: {file_path}"}

        # Vanta accepts: .docx .jpg .pdf .ai .png .webp .xlsx .csv .txt .json .zip
        # Map by extension so the declared MIME matches the bytes (a wrong MIME
        # like "image/json" gets a 422 even though the extension is supported).
        VANTA_MIME = {
            ".pdf": "application/pdf",
            ".ai": "application/pdf",
            ".png": "image/png",
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
            ".webp": "image/webp",
            ".json": "application/json",
            ".csv": "text/csv",
            ".txt": "text/plain",
            ".zip": "application/zip",
            ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        }
        ext = p.suffix.lower()
        mime = (VANTA_MIME.get(ext)
                or mimetypes.guess_type(p.name)[0]
                or "application/octet-stream")

        data = {}
        if description:
            data["description"] = description
        if effective_date:
            data["effectiveAtDate"] = effective_date

        url = f"{self.base_url}/documents/{doc_id}/uploads"

        # Retry with backoff: 401 -> refresh token and retry; 429/5xx -> honor
        # Retry-After (or exponential backoff). Uploading a multi-file package
        # back-to-back otherwise trips Vanta's rate limit (HTTP 429).
        max_attempts = 5
        backoff = 5  # seconds; grows on each rate-limit/transient error
        resp = None
        for attempt in range(1, max_attempts + 1):
            hdrs = self._headers()
            resp = requests.post(
                url,
                headers={"Authorization": hdrs["Authorization"]},
                files={"file": (p.name, p.open("rb"), mime)},
                data=data,
                timeout=120,
            )

            if resp.status_code == 401 and attempt < max_attempts:
                self._token = None
                TOKEN_CACHE.unlink(missing_ok=True)
                continue

            if resp.status_code in (429, 502, 503, 504) and attempt < max_attempts:
                wait = backoff
                retry_after = resp.headers.get("Retry-After")
                if retry_after:
                    try:
                        wait = max(wait, int(float(retry_after)))
                    except ValueError:
                        pass
                time.sleep(wait)
                backoff = min(backoff * 2, 60)
                continue

            break

        if resp is None or resp.status_code not in (200, 201):
            return {"error": resp.status_code if resp is not None else "no_response",
                    "body": resp.text if resp is not None else ""}
        return resp.json()

    def delete_document(self, doc_id: str) -> dict:
        resp = requests.delete(
            f"{self.base_url}/documents/{doc_id}",
            headers=self._headers(),
            timeout=30,
        )
        if resp.status_code == 401:
            self._token = None
            TOKEN_CACHE.unlink(missing_ok=True)
            resp = requests.delete(
                f"{self.base_url}/documents/{doc_id}",
                headers=self._headers(),
                timeout=30,
            )
        return {"status": resp.status_code, "body": resp.text if resp.text else "ok"}


def main():
    parser = argparse.ArgumentParser(description="Vanta REST API client")
    sub = parser.add_subparsers(dest="command", required=True)

    lp = sub.add_parser("list-pending", help="List documents needing evidence")
    lp.add_argument("--framework", help="Filter by framework ID")

    la = sub.add_parser("list-all", help="List all documents")
    la.add_argument("--framework", help="Filter by framework ID")

    lt = sub.add_parser("list-tests", help="List failing tests")
    lt.add_argument("--framework", help="Filter by framework ID")

    sub.add_parser("list-frameworks", help="List active frameworks")

    gd = sub.add_parser("get-document", help="Get a single document")
    gd.add_argument("doc_id")

    cd = sub.add_parser("create-document", help="Create a new document")
    cd.add_argument("--title", required=True)
    cd.add_argument("--description", default="")

    up = sub.add_parser("upload", help="Upload evidence file to a document")
    up.add_argument("doc_id")
    up.add_argument("file_path")
    up.add_argument("--description", default="")
    up.add_argument("--effective-date", default="")

    dd = sub.add_parser("delete-document", help="Delete a document")
    dd.add_argument("doc_id")

    args = parser.parse_args()
    client = VantaClient()

    if args.command == "list-pending":
        result = client.list_pending_documents(args.framework)
    elif args.command == "list-all":
        result = client.list_all_documents(args.framework)
    elif args.command == "list-tests":
        result = client.list_failing_tests(args.framework)
    elif args.command == "list-frameworks":
        result = client.list_frameworks()
    elif args.command == "get-document":
        result = client.get_document(args.doc_id)
    elif args.command == "create-document":
        result = client.create_document(args.title, args.description)
    elif args.command == "upload":
        result = client.upload_evidence(
            args.doc_id, args.file_path,
            args.description, args.effective_date,
        )
    elif args.command == "delete-document":
        result = client.delete_document(args.doc_id)
    else:
        parser.print_help()
        sys.exit(1)

    print(json.dumps(result, indent=2, default=str))


if __name__ == "__main__":
    main()
