#!/usr/bin/env python3
"""
evidence_audit.py — deterministic QA gate for a Vanta evidence package.

This is the *deterministic* half of the evidence quality loop. It runs cheap,
reproducible checks over an evidence output directory (the folder containing the
screenshots, the explainer PDF, and `manifest.json`) and emits a structured
verdict. The *semantic* half — "does this evidence actually convince a skeptical
auditor?" — is handled by the framework-tailored evaluator sub-agent described in
CLAUDE.md, which runs after this script passes.

Policy: ANNOTATE, with BLOCKING ONLY ON HARD DETERMINISTIC FAILURES.
  - A `block` verdict means a hard, objective defect that invalidates the package
    (tampered/corrupt files, a captured login wall, evidence outside the audit
    period, nothing captured at all). The pipeline must NOT upload on `block`.
  - A `warn` verdict means a quality concern a human should look at, but the
    package is still uploadable as a draft (annotated with the findings).
  - A `pass` verdict means no deterministic issues were found.

Every failing check is classified `block` | `warn`. The overall verdict is the
most severe failing check (block > warn > pass).

Usage:
  python3 evidence_audit.py --dir ~/Downloads/vanta-evidence/2026-06-29-CC8.1 \
      --framework soc2 --control "Change Management" \
      --expected-systems github,monitoring

  # Override the audit window explicitly (otherwise read from the rubric):
  python3 evidence_audit.py --dir <DIR> --framework soc2 \
      --period-start 2025-07-01 --period-end 2026-06-30

Output: a JSON report to stdout AND written to `<dir>/audit.json` (unless
--no-write). Exit code: 0 for pass/warn, 2 for block, 1 for an internal error.
"""

import argparse
import json
import re
import sys
from datetime import datetime, timezone, date
from pathlib import Path
from urllib.parse import urlparse

import provenance

# Optional deps — the script degrades gracefully if they are absent.
try:
    import yaml  # PyYAML, for loading rubric files
    _HAVE_YAML = True
except Exception:  # pragma: no cover
    _HAVE_YAML = False

try:
    from PIL import Image  # bundled transitively via reportlab
    _HAVE_PIL = True
except Exception:  # pragma: no cover
    _HAVE_PIL = False


# Hosts/paths that indicate we captured an auth wall instead of evidence.
# Kept in sync conceptually with screenshot_capture.SSO_DOMAINS, but inlined so
# this script stays dependency-light (no playwright import).
SSO_DOMAINS = (
    "okta.com", "auth0.com", "login.microsoftonline.com",
    "accounts.google.com", "sso.", "login.", "signin.",
    "identity.", "idp.", "saml.", "onelogin.com", "duosecurity.com",
)
LOGIN_PATH_HINTS = ("/login", "/signin", "/sign-in", "/sso", "/auth", "/oauth",
                    "/authenticate", "/saml")
IMG_EXTS = (".png", ".jpg", ".jpeg", ".gif", ".webp")

# Generic tokens that should not, on their own, count as "system covered".
GENERIC_TOKENS = {"aws", "gcp", "api", "app", "web", "the", "and", "cloud",
                  "console", "settings", "admin", "prod", "prd"}


def _skill_dir() -> Path:
    return Path(__file__).resolve().parent.parent


def _parse_iso(ts: str):
    """Parse an ISO-8601 timestamp (the manifest uses %Y-%m-%dT%H:%M:%SZ)."""
    if not ts:
        return None
    s = ts.strip().replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except ValueError:
        return None


def _parse_date(s: str):
    if not s:
        return None
    try:
        return date.fromisoformat(s.strip())
    except ValueError:
        return None


def load_rubric(framework: str, override_path: str | None) -> tuple[dict, bool]:
    """Return (rubric_dict, loaded). Empty dict + False if not available."""
    if not _HAVE_YAML:
        return {}, False
    if override_path:
        path = Path(override_path)
    elif framework:
        path = _skill_dir() / "knowledge" / "rubrics" / f"{framework.lower()}.yaml"
    else:
        return {}, False
    if not path.exists():
        return {}, False
    try:
        return yaml.safe_load(path.read_text()) or {}, True
    except Exception:
        return {}, False


def _system_tokens(slug: str) -> list[str]:
    parts = re.split(r"[-_\s/]+", slug.lower())
    return [p for p in parts if len(p) >= 3 and p not in GENERIC_TOKENS]


def _system_covered(slug: str, haystack: str) -> bool:
    """Heuristic: is `slug` (e.g. 'aws-rds') represented in the haystack?"""
    flat = re.sub(r"[-_\s/]+", "", slug.lower())
    if len(flat) >= 4 and flat in haystack:
        return True
    distinctive = [t for t in _system_tokens(slug)]
    # Require a distinctive (non-generic) token to match.
    return any(t in haystack for t in distinctive)


class Check:
    """A single audit finding."""

    def __init__(self, cid, title, severity, status, message, items=None):
        self.cid = cid
        self.title = title
        self.severity = severity  # block | warn | info
        self.status = status      # pass | fail
        self.message = message
        self.items = items or []

    def as_dict(self):
        d = {
            "id": self.cid,
            "title": self.title,
            "severity": self.severity,
            "status": self.status,
            "message": self.message,
        }
        if self.items:
            d["items"] = self.items
        return d


def audit(out_dir: Path, *, framework: str = "", control: str = "",
          rubric: dict | None = None, expected_systems: list[str] | None = None,
          period_start: date | None = None, period_end: date | None = None,
          max_age_days: int | None = None) -> dict:
    rubric = rubric or {}
    det = rubric.get("deterministic", {}) if isinstance(rubric, dict) else {}

    checks: list[Check] = []

    # ── 1. Manifest present & parses ──────────────────────────────────────
    manifest = provenance.read_manifest(out_dir)
    mpath = provenance.manifest_path(out_dir)
    if not mpath.exists():
        checks.append(Check(
            "manifest_present", "Provenance manifest exists", "block", "fail",
            "manifest.json is missing — the package has no provenance trail. "
            "Re-run capture so provenance is recorded.",
        ))
        return _assemble(out_dir, framework, control, checks, rubric)
    checks.append(Check("manifest_present", "Provenance manifest exists",
                        "block", "pass", "manifest.json present and parsed."))

    items = [it for it in manifest.get("items", []) if isinstance(it, dict)]
    evidence_items = [it for it in items
                      if it.get("kind") in ("screenshot", "screenshot_pdf")]
    explainers = [it for it in items if it.get("kind") == "explainer_pdf"]

    # ── 2. At least one evidence artifact ─────────────────────────────────
    # Screenshots are the usual evidence, but some controls are legitimately
    # document-only — e.g. "no instances to report" attestations or process
    # descriptions. Those have an explainer PDF and no screenshots, which is a
    # WARN (verify the narrative suffices), not a hard block. A package with
    # neither screenshots nor an explainer is a true block (nothing to upload).
    if evidence_items:
        checks.append(Check(
            "has_evidence", "Evidence captured", "block", "pass",
            f"{len(evidence_items)} evidence file(s) recorded.",
        ))
    elif explainers:
        checks.append(Check(
            "has_evidence", "Evidence captured", "warn", "fail",
            "No screenshots — this is a document-only package (explainer PDF "
            "only). Valid for attestation / 'no instances' / process controls; "
            "confirm the narrative and provenance are sufficient for this control.",
        ))
    else:
        checks.append(Check(
            "has_evidence", "Evidence captured", "block", "fail",
            "No evidence files and no explainer recorded — nothing to upload.",
        ))

    # ── 3. Every manifest file exists on disk ─────────────────────────────
    missing = [it["file"] for it in items
               if it.get("file") and not (out_dir / it["file"]).exists()]
    if missing:
        checks.append(Check(
            "files_present", "Manifest files exist on disk", "block", "fail",
            "Manifest references files that are not on disk.", items=missing,
        ))
    else:
        checks.append(Check("files_present", "Manifest files exist on disk",
                            "block", "pass", "All manifest files present."))

    # ── 4. Checksum integrity (tamper / corruption) ───────────────────────
    mismatches = []
    for it in items:
        fp = out_dir / it.get("file", "")
        recorded = it.get("sha256")
        if not recorded or not fp.exists():
            continue
        actual = provenance.sha256_file(fp)
        if actual != recorded:
            mismatches.append({"file": it["file"],
                               "recorded": recorded, "actual": actual})
    if mismatches:
        checks.append(Check(
            "checksum_integrity", "Files match recorded checksums",
            "block", "fail",
            "File bytes differ from the SHA-256 recorded at capture time. "
            "The package was modified after capture — re-capture to restore a "
            "clean provenance trail.",
            items=mismatches,
        ))
    elif items:
        checks.append(Check("checksum_integrity", "Files match recorded checksums",
                            "block", "pass", "All checksums verified."))

    # ── 5. No captured login / SSO / auth walls ───────────────────────────
    block_login = det.get("block_on_login_page", True)
    auth_walls = []
    for it in evidence_items:
        url = (it.get("source_url") or "").lower()
        host = (urlparse(url).hostname or "")
        path = urlparse(url).path or ""
        if any(d in host for d in SSO_DOMAINS) or any(h in path for h in LOGIN_PATH_HINTS):
            auth_walls.append({"file": it.get("file"), "source_url": it.get("source_url")})
    if auth_walls:
        checks.append(Check(
            "no_auth_wall", "No login/SSO pages captured",
            "block" if block_login else "warn", "fail",
            "One or more captures appear to be login/SSO/auth pages rather than "
            "the underlying evidence. Re-SSO and re-capture the real page.",
            items=auth_walls,
        ))
    elif evidence_items:
        checks.append(Check("no_auth_wall", "No login/SSO pages captured",
                            "block", "pass", "No auth walls detected in sources."))

    # ── 6. Freshness / audit-period coverage ──────────────────────────────
    # Explicit period == hard requirement (block). Soft max-age == warn.
    if period_start is None and period_end is None and isinstance(rubric.get("audit_period"), dict):
        period_start = _parse_date(rubric["audit_period"].get("start", ""))
        period_end = _parse_date(rubric["audit_period"].get("end", ""))
    if max_age_days is None:
        max_age_days = rubric.get("max_age_days")

    has_period = period_start is not None or period_end is not None
    stale_blocking = det.get("block_on_period_violation", True)
    today = datetime.now(timezone.utc).date()
    out_of_period = []
    stale = []
    for it in evidence_items:
        cdt = _parse_iso(it.get("captured_at", ""))
        if cdt is None:
            continue
        cd = cdt.date()
        if has_period:
            if (period_start and cd < period_start) or (period_end and cd > period_end):
                out_of_period.append({"file": it.get("file"),
                                      "captured_at": it.get("captured_at")})
        elif max_age_days:
            if (today - cd).days > int(max_age_days):
                stale.append({"file": it.get("file"),
                              "captured_at": it.get("captured_at"),
                              "age_days": (today - cd).days})
    if out_of_period:
        win = f"{period_start or '...'} .. {period_end or '...'}"
        checks.append(Check(
            "freshness", "Evidence within audit period",
            "block" if stale_blocking else "warn", "fail",
            f"Captures fall outside the audit window ({win}). Evidence dated "
            "outside the review period does not demonstrate the control operated "
            "during it — re-capture inside the period.",
            items=out_of_period,
        ))
    elif stale:
        checks.append(Check(
            "freshness", "Evidence is fresh", "warn", "fail",
            f"Captures are older than {max_age_days} days. Consider re-capturing "
            "so the auditor sees current state.",
            items=stale,
        ))
    elif evidence_items:
        checks.append(Check("freshness", "Evidence within audit period",
                            "warn", "pass", "All captures within window."))

    # ── 7. Image legibility (corrupt / blank / tiny) ──────────────────────
    if _HAVE_PIL:
        corrupt, blank, tiny = [], [], []
        min_w = int(det.get("min_image_width", 320))
        for it in evidence_items:
            fp = out_dir / it.get("file", "")
            if fp.suffix.lower() not in IMG_EXTS or not fp.exists():
                continue
            try:
                with Image.open(fp) as im:
                    im.load()
                    w, h = im.size
                    if w == 0 or h == 0:
                        corrupt.append(it.get("file"))
                        continue
                    if w < min_w:
                        tiny.append({"file": it.get("file"), "width": w})
                    extrema = im.convert("L").getextrema()
                    if extrema and extrema[0] == extrema[1]:
                        blank.append(it.get("file"))
            except Exception:
                corrupt.append(it.get("file"))
        if corrupt:
            checks.append(Check(
                "image_legible", "Images open and render", "block", "fail",
                "Image files are corrupt or unreadable — re-capture.",
                items=corrupt,
            ))
        else:
            checks.append(Check("image_legible", "Images open and render",
                                "block", "pass", "All images decode cleanly."))
        if blank:
            checks.append(Check(
                "image_not_blank", "Images are not blank", "warn", "fail",
                "Image(s) are a single flat color (likely a blank/unloaded page).",
                items=blank,
            ))
        if tiny:
            checks.append(Check(
                "image_size", "Images are large enough to read", "warn", "fail",
                f"Image(s) narrower than {min_w}px may be illegible to an auditor.",
                items=tiny,
            ))
    else:
        checks.append(Check(
            "image_legible", "Images open and render", "info", "pass",
            "Pillow not installed — skipped blank/corrupt image detection.",
        ))

    # ── 8. Explainer PDF present ──────────────────────────────────────────
    require_explainer = det.get("require_explainer", True)
    if explainers:
        checks.append(Check("has_explainer", "Auditor explainer PDF present",
                            "warn", "pass", "Explainer PDF recorded."))
    elif require_explainer:
        checks.append(Check(
            "has_explainer", "Auditor explainer PDF present", "warn", "fail",
            "No explainer PDF recorded. Generate one so the package explains how "
            "the evidence satisfies the control.",
        ))

    # ── 9. Expected-system coverage (heuristic, warn-only) ────────────────
    if expected_systems:
        haystack = " ".join(
            [(it.get("source_url") or "") + " " + (it.get("file") or "")
             for it in evidence_items]
        ).lower()
        haystack_flat = re.sub(r"[-_\s/]+", "", haystack)
        not_covered = []
        for sysslug in expected_systems:
            if not (_system_covered(sysslug, haystack) or
                    _system_covered(sysslug, haystack_flat)):
                not_covered.append(sysslug)
        if not_covered:
            checks.append(Check(
                "system_coverage", "Expected systems represented",
                "warn", "fail",
                "Expected system(s) for this control are not obviously present in "
                "the captures. Confirm coverage (heuristic check — may be a false "
                "positive if a system shows on a shared page).",
                items=not_covered,
            ))
        else:
            checks.append(Check("system_coverage", "Expected systems represented",
                                "warn", "pass", "All expected systems represented."))

    # ── 10. Operator recorded ─────────────────────────────────────────────
    no_op = [it.get("file") for it in items if not it.get("operator")]
    if no_op:
        checks.append(Check(
            "operator_recorded", "Operator identity recorded", "warn", "fail",
            "Some items have no operator recorded — accountability is incomplete.",
            items=no_op,
        ))

    return _assemble(out_dir, framework, control, checks, rubric)


def _assemble(out_dir, framework, control, checks, rubric):
    failing = [c for c in checks if c.status == "fail"]
    blocking = [c for c in failing if c.severity == "block"]
    warnings = [c for c in failing if c.severity == "warn"]

    if blocking:
        verdict = "block"
    elif warnings:
        verdict = "warn"
    else:
        verdict = "pass"

    if verdict == "block":
        summary = (f"BLOCK — {len(blocking)} hard failure(s). Do NOT upload; "
                   f"remediate and re-audit.")
    elif verdict == "warn":
        summary = (f"WARN — {len(warnings)} concern(s). Uploadable as draft; "
                   f"annotate so the human reviewer can verify.")
    else:
        summary = "PASS — no deterministic issues found."

    return {
        "schema": "vanta-evidence-audit/1",
        "dir": str(out_dir),
        "framework": framework or None,
        "control": control or None,
        "verdict": verdict,
        "blocking_count": len(blocking),
        "warning_count": len(warnings),
        "summary": summary,
        "blocking": [c.cid for c in blocking],
        "warnings": [c.cid for c in warnings],
        "checks": [c.as_dict() for c in checks],
        "audited_at": provenance.utc_now_iso(),
        "operator": provenance.get_operator(),
    }


def main():
    ap = argparse.ArgumentParser(
        description="Deterministic QA gate for a Vanta evidence package.")
    ap.add_argument("--dir", required=True,
                    help="Evidence output directory (contains manifest.json).")
    ap.add_argument("--framework", default="",
                    help="Framework id (soc2|iso27001|hitrust) — selects rubric.")
    ap.add_argument("--control", default="", help="Control name/id (context).")
    ap.add_argument("--rubric", default="", help="Override path to a rubric YAML.")
    ap.add_argument("--expected-systems", default="",
                    help="Comma-separated system slugs expected for this control "
                         "(e.g. from playbooks/index.yaml).")
    ap.add_argument("--period-start", default="",
                    help="Audit period start (YYYY-MM-DD). Overrides rubric.")
    ap.add_argument("--period-end", default="",
                    help="Audit period end (YYYY-MM-DD). Overrides rubric.")
    ap.add_argument("--max-age-days", type=int, default=None,
                    help="Soft freshness threshold when no hard period applies.")
    ap.add_argument("--no-write", action="store_true",
                    help="Do not write audit.json into the evidence dir.")
    args = ap.parse_args()

    out_dir = Path(args.dir).expanduser()
    if not out_dir.exists():
        print(json.dumps({"error": f"Directory not found: {out_dir}"}))
        sys.exit(1)

    rubric, loaded = load_rubric(args.framework, args.rubric or None)
    expected = [s.strip() for s in args.expected_systems.split(",") if s.strip()]

    report = audit(
        out_dir,
        framework=args.framework,
        control=args.control,
        rubric=rubric,
        expected_systems=expected,
        period_start=_parse_date(args.period_start),
        period_end=_parse_date(args.period_end),
        max_age_days=args.max_age_days,
    )
    report["rubric_loaded"] = loaded
    report["pillow_available"] = _HAVE_PIL

    if not args.no_write:
        try:
            (out_dir / "audit.json").write_text(json.dumps(report, indent=2))
            report["audit_file"] = str(out_dir / "audit.json")
        except OSError:
            pass

    print(json.dumps(report, indent=2))
    sys.exit(2 if report["verdict"] == "block" else 0)


if __name__ == "__main__":
    main()
