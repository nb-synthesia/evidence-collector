#!/usr/bin/env python3
"""
provenance.py — shared provenance helpers for evidence collection.

Records, per evidence file: the source URL it came from, the capture/generation
timestamp (UTC), the operator who ran the collection, and a SHA-256 checksum of
the exact bytes. Entries are written to a `manifest.json` that lives alongside
the evidence, so any single export can be traced back to its source — and
re-verified against its checksum — on demand.

Pure standard library (hashlib, json, datetime, subprocess, getpass) so both
scripts can import it without extra dependencies.
"""

import hashlib
import json
import os
import subprocess
from datetime import datetime, timezone
from getpass import getuser
from pathlib import Path

MANIFEST_NAME = "manifest.json"
_CHUNK = 1024 * 1024


def utc_now_iso() -> str:
    """Current UTC time as a precise, sortable ISO-8601 timestamp."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def sha256_file(path) -> str:
    """SHA-256 of a file, streamed so large screenshots/PDFs stay cheap."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(_CHUNK), b""):
            h.update(chunk)
    return h.hexdigest()


def get_operator() -> str:
    """
    Identify who ran the collection, in priority order:
      1. EVIDENCE_OPERATOR / VANTA_OPERATOR env var (explicit override)
      2. git config user.email (the named human behind the repo)
      3. OS login name (last resort)
    """
    op = os.environ.get("EVIDENCE_OPERATOR") or os.environ.get("VANTA_OPERATOR")
    if op:
        return op.strip()
    try:
        email = subprocess.run(
            ["git", "config", "--get", "user.email"],
            capture_output=True, text=True, timeout=3,
        ).stdout.strip()
        if email:
            return email
    except Exception:
        pass
    try:
        return getuser()
    except Exception:
        return "unknown"


def manifest_path(out_dir) -> Path:
    return Path(out_dir) / MANIFEST_NAME


def read_manifest(out_dir) -> dict:
    p = manifest_path(out_dir)
    if p.exists():
        try:
            return json.loads(p.read_text())
        except (json.JSONDecodeError, OSError):
            pass
    return {"items": []}


def record_item(out_dir, file_path, *, kind, source_url="", test_id="",
                captured_at=None, sha256=None) -> dict:
    """
    Append (or replace) a provenance entry for `file_path` in the manifest.

    Idempotent: re-recording the same filename replaces the prior entry, so
    re-runs don't duplicate rows. Returns the entry that was written.
    """
    out_dir = Path(out_dir)
    fp = Path(file_path)
    entry = {
        "file": fp.name,
        "kind": kind,
        "source_url": source_url,
        "captured_at": captured_at or utc_now_iso(),
        "operator": get_operator(),
        "sha256": sha256 or sha256_file(fp),
        "size_bytes": fp.stat().st_size if fp.exists() else None,
    }
    if test_id:
        entry["test_id"] = test_id

    manifest = read_manifest(out_dir)
    manifest.setdefault("items", [])
    manifest["items"] = [it for it in manifest["items"]
                         if it.get("file") != fp.name]
    manifest["items"].append(entry)
    if test_id and not manifest.get("test_id"):
        manifest["test_id"] = test_id
    manifest["updated_at"] = utc_now_iso()

    manifest_path(out_dir).write_text(json.dumps(manifest, indent=2))
    return entry


def index_by_file(out_dir) -> dict:
    """Map filename -> provenance entry, for enriching the explainer PDF."""
    manifest = read_manifest(out_dir)
    return {it["file"]: it for it in manifest.get("items", []) if "file" in it}
