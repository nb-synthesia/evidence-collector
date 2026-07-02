#!/usr/bin/env python3
"""
config.py — shared configuration loader for the evidence collector.

Reads organization configuration from `config.yaml` at the repo root (created
by install.sh from config.example.yaml). Everything here is org-specific but
NOT secret — credentials live separately in ~/.vanta/credentials.json and are
never stored in config.yaml.

Resolution order for the config file:
  1. $EVIDENCE_CONFIG (explicit path override)
  2. <repo_root>/config.yaml
  3. built-in defaults (so scripts still run before install.sh has been run)

Env vars always win over the file, so a single run can be overridden without
editing config:
  EVIDENCE_SSO_URL, EVIDENCE_VANTA_REGION, VANTA_REGION

Pure standard library except PyYAML (already a dependency). Degrades to an
empty config if PyYAML or the file is missing.
"""

import os
from pathlib import Path

try:
    import yaml
    _HAVE_YAML = True
except Exception:  # pragma: no cover
    _HAVE_YAML = False


DEFAULTS = {
    # The org's IdP / SSO dashboard (e.g. an Okta end-user dashboard, Azure
    # MyApps, Google app launcher). ensure-sso navigates here to verify login.
    "sso_url": "",
    # Vanta API region: us | eu | gov
    "vanta_region": "us",
    # Where the detailed playbooks live (the "base document").
    "playbook": {
        "backend": "local",              # local | notion | confluence | google_doc | url
        "path": "knowledge/playbooks",   # used when backend == local
        "base_document_url": "",         # root page/doc URL for external backends
        "base_document_id": "",          # optional id (e.g. Notion page id)
        "notes": "",
    },
}


def repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def config_path() -> Path:
    override = os.environ.get("EVIDENCE_CONFIG")
    if override:
        return Path(override).expanduser()
    return repo_root() / "config.yaml"


def _deep_merge(base: dict, over: dict) -> dict:
    out = dict(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def load() -> dict:
    cfg = {k: (dict(v) if isinstance(v, dict) else v) for k, v in DEFAULTS.items()}
    p = config_path()
    if _HAVE_YAML and p.exists():
        try:
            loaded = yaml.safe_load(p.read_text()) or {}
            if isinstance(loaded, dict):
                cfg = _deep_merge(cfg, loaded)
        except Exception:
            pass

    # Env overrides (highest priority).
    env_sso = os.environ.get("EVIDENCE_SSO_URL")
    if env_sso:
        cfg["sso_url"] = env_sso
    env_region = os.environ.get("EVIDENCE_VANTA_REGION") or os.environ.get("VANTA_REGION")
    if env_region:
        cfg["vanta_region"] = env_region.lower()

    return cfg


def sso_url() -> str:
    return load().get("sso_url", "") or ""


def vanta_region() -> str:
    return (load().get("vanta_region") or "us").lower()


def playbook() -> dict:
    pb = load().get("playbook") or {}
    return _deep_merge(DEFAULTS["playbook"], pb if isinstance(pb, dict) else {})


if __name__ == "__main__":
    import json
    print(json.dumps(load(), indent=2))
