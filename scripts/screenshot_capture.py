#!/usr/bin/env python3
"""
screenshot_capture.py — Playwright CDP screenshot capture for compliance evidence.

Connects to Chrome via CDP to reuse existing sessions, cookies, and auth.

Usage:
  # Prepare: copy Chrome profile + launch with CDP (one-time per session)
  python3 screenshot_capture.py prepare-chrome

  # Verify SSO: navigate to your IdP dashboard, check if logged in
  #   (--sso-url defaults to the sso_url in config.yaml)
  python3 screenshot_capture.py ensure-sso [--sso-url https://your-idp.example.com]

  # Capture a screenshot
  python3 screenshot_capture.py capture <URL> --test-id CC8.1 --stem 01_github_pr
  python3 screenshot_capture.py capture <URL> --test-id CC8.1 --stem 02_idp --scroll --pdf

  # Check CDP connectivity
  python3 screenshot_capture.py check

Output (JSON to stdout):
  {"success": true, "png": "/path/to/file.png"}
  {"needs_user_action": true, "reason": "login form detected", "url": "..."}
"""

import argparse
import asyncio
import json
import os
import platform
import shutil
import signal
import subprocess
import sys
import time
from datetime import date
from pathlib import Path
from urllib.parse import urlparse

import provenance

try:
    import config as _config
except Exception:  # pragma: no cover
    _config = None

COOKIE_SELECTORS = [
    "#onetrust-accept-btn-handler",
    ".cc-accept",
    "[aria-label*='Accept cookies']",
    "[aria-label*='Accept all']",
    "button[id*='accept']",
    "button[class*='accept']",
    ".cookie-banner button",
    "#CybotCookiebotDialogBodyLevelButtonLevelOptinAllowAll",
    "[data-testid='cookie-accept']",
    ".js-cookie-accept",
    "#cookie-accept",
    "button:has-text('Accept')",
    "button:has-text('Got it')",
    "button:has-text('I agree')",
]

SSO_DOMAINS = [
    "okta.com", "auth0.com", "login.microsoftonline.com",
    "accounts.google.com", "sso.", "login.", "signin.",
    "identity.", "idp.", "saml.", "onelogin.com", "duosecurity.com",
    "jumpcloud.com", "pingidentity.com",
]

CDP_URL = "http://localhost:9222"
DEFAULT_OUT = Path.home() / "Downloads" / "compliance-evidence"
CDP_PROFILE_DIR = Path.home() / ".vanta" / "chrome-cdp-profile"
CDP_PID_FILE = Path.home() / ".vanta" / "chrome-cdp.pid"


def _detect_chrome_binary() -> str:
    """Locate the Google Chrome executable across macOS / Linux / Windows."""
    override = os.environ.get("CHROME_BINARY")
    if override and Path(override).exists():
        return override

    system = platform.system()
    candidates: list[str] = []
    if system == "Darwin":
        candidates = [
            "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
            "/Applications/Google Chrome Beta.app/Contents/MacOS/Google Chrome Beta",
            "/Applications/Chromium.app/Contents/MacOS/Chromium",
        ]
    elif system == "Windows":
        candidates = [
            r"C:\Program Files\Google\Chrome\Application\chrome.exe",
            r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        ]
    else:  # Linux and others
        for name in ("google-chrome", "google-chrome-stable", "chromium",
                     "chromium-browser"):
            found = shutil.which(name)
            if found:
                candidates.append(found)

    for c in candidates:
        if c and Path(c).exists():
            return c
    return candidates[0] if candidates else "google-chrome"


def _detect_chrome_profile() -> Path:
    """Locate the default Chrome user-data directory for this platform."""
    override = os.environ.get("CHROME_PROFILE_DIR")
    if override:
        return Path(override).expanduser()

    system = platform.system()
    home = Path.home()
    if system == "Darwin":
        return home / "Library" / "Application Support" / "Google" / "Chrome"
    if system == "Windows":
        return home / "AppData" / "Local" / "Google" / "Chrome" / "User Data"
    return home / ".config" / "google-chrome"


CHROME_BIN = _detect_chrome_binary()
CHROME_PROFILE_SRC = _detect_chrome_profile()

PROFILE_COPY_FILES = [
    "Default/Cookies",
    "Default/Login Data",
    "Default/Web Data",
    "Default/Preferences",
    "Default/Secure Preferences",
    "Default/Network/Cookies",
    "Default/Network/TransportSecurity",
    "Default/Extension Cookies",
    "Local State",
]

PROFILE_COPY_DIRS = [
    "Default/Local Storage",
    "Default/Session Storage",
    "Default/Sessions",
    "Default/IndexedDB",
    "Default/Service Worker",
]


def _default_sso_url() -> str:
    if _config is not None:
        try:
            return _config.sso_url()
        except Exception:
            pass
    return ""


def result_json(**kwargs):
    print(json.dumps(kwargs, default=str))
    sys.exit(0 if kwargs.get("success") else 1)


def prepare_chrome_profile():
    """Copy essential auth files from Chrome profile, launch Chrome with CDP."""
    import requests as req

    # Check if CDP is already running
    try:
        r = req.get(f"{CDP_URL}/json/version", timeout=2)
        if r.status_code == 200:
            result_json(
                success=True,
                message="Chrome CDP already running",
                browser=r.json().get("Browser", "unknown"),
                profile=str(CDP_PROFILE_DIR),
            )
    except Exception:
        pass

    # Kill any existing CDP Chrome
    if CDP_PID_FILE.exists():
        try:
            pid = int(CDP_PID_FILE.read_text().strip())
            os.kill(pid, signal.SIGTERM)
            time.sleep(2)
        except (ProcessLookupError, ValueError):
            pass
        CDP_PID_FILE.unlink(missing_ok=True)

    if not CHROME_PROFILE_SRC.exists():
        result_json(success=False, error=f"Chrome profile not found at {CHROME_PROFILE_SRC}. "
                    "Set CHROME_PROFILE_DIR to override.")

    # Copy profile
    CDP_PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    (CDP_PROFILE_DIR / "Default").mkdir(parents=True, exist_ok=True)
    (CDP_PROFILE_DIR / "Default" / "Network").mkdir(parents=True, exist_ok=True)

    copied = []
    for f in PROFILE_COPY_FILES:
        src = CHROME_PROFILE_SRC / f
        dst = CDP_PROFILE_DIR / f
        dst.parent.mkdir(parents=True, exist_ok=True)
        if src.exists():
            shutil.copy2(str(src), str(dst))
            copied.append(f)

    for d in PROFILE_COPY_DIRS:
        src = CHROME_PROFILE_SRC / d
        dst = CDP_PROFILE_DIR / d
        if src.exists():
            if dst.exists():
                shutil.rmtree(str(dst))
            shutil.copytree(str(src), str(dst))
            copied.append(f"{d}/")

    # Launch Chrome with CDP
    proc = subprocess.Popen(
        [CHROME_BIN,
         f"--remote-debugging-port=9222",
         f"--user-data-dir={CDP_PROFILE_DIR}",
         "--no-first-run",
         "--no-default-browser-check"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    CDP_PID_FILE.write_text(str(proc.pid))

    # Wait for CDP to be ready
    for i in range(15):
        time.sleep(1)
        try:
            r = req.get(f"{CDP_URL}/json/version", timeout=2)
            if r.status_code == 200:
                result_json(
                    success=True,
                    message=f"Chrome CDP ready (copied {len(copied)} auth files)",
                    browser=r.json().get("Browser", "unknown"),
                    pid=proc.pid,
                    profile=str(CDP_PROFILE_DIR),
                    copied_files=copied,
                )
        except Exception:
            continue

    result_json(
        success=False,
        error="Chrome started but CDP not reachable after 15s",
        pid=proc.pid,
    )


async def check_cdp():
    """Verify Chrome CDP is reachable."""
    try:
        from playwright.async_api import async_playwright
        async with async_playwright() as p:
            browser = await p.chromium.connect_over_cdp(CDP_URL)
            version = browser.version
            n_contexts = len(browser.contexts)
            await browser.close()
            result_json(success=True, mode="cdp", browser=version, contexts=n_contexts)
    except Exception as e:
        result_json(
            success=False,
            error=f"Cannot connect to Chrome CDP at {CDP_URL}: {e}",
            fix="Run: python3 screenshot_capture.py prepare-chrome",
        )


async def ensure_sso(sso_url: str, wait: float = 3.0):
    """
    Navigate to the SSO dashboard (e.g. your IdP tiles), check if logged in.
    Returns success if authenticated, needs_user_action if login required.
    """
    from playwright.async_api import async_playwright

    if not sso_url:
        result_json(
            success=False,
            error="No SSO URL configured.",
            fix="Set sso_url in config.yaml, pass --sso-url, or set EVIDENCE_SSO_URL.",
        )

    async with async_playwright() as p:
        try:
            browser = await p.chromium.connect_over_cdp(CDP_URL)
        except Exception as e:
            result_json(
                success=False,
                error=f"CDP not available: {e}",
                fix="Run: python3 screenshot_capture.py prepare-chrome",
            )

        context = browser.contexts[0] if browser.contexts else await browser.new_context(
            viewport={"width": 1440, "height": 900}
        )
        page = await context.new_page()

        try:
            resp = await page.goto(sso_url, wait_until="domcontentloaded", timeout=30_000)
            await asyncio.sleep(wait)

            auth_wall = await detect_auth_wall(page)
            if auth_wall:
                await page.close()
                result_json(
                    needs_user_action=True,
                    reason=f"SSO login required at {sso_url}",
                    detail=auth_wall["reason"],
                    url=page.url,
                    instructions=(
                        "Open the CDP Chrome window (the one launched by prepare-chrome) "
                        "and log in to your IdP. Once you see the dashboard, re-run this command."
                    ),
                )

            current_url = page.url
            title = await page.title()
            await page.close()

            result_json(
                success=True,
                message="SSO session active",
                sso_url=sso_url,
                landed_on=current_url,
                title=title,
            )

        except Exception as e:
            await page.close()
            result_json(success=False, error=str(e))


async def detect_auth_wall(page) -> dict | None:
    """Check if the page is an auth/login wall."""
    url = page.url
    parsed = urlparse(url)
    hostname = parsed.hostname or ""

    for domain in SSO_DOMAINS:
        if domain in hostname:
            return {"reason": f"Redirected to SSO ({hostname})", "url": url}

    try:
        pw_visible = await page.locator("input[type='password']").first.is_visible(timeout=500)
        if pw_visible:
            return {"reason": "Login form detected (password field visible)", "url": url}
    except Exception:
        pass

    title = (await page.title()).lower()
    login_titles = ("sign in", "login", "authenticate", "sso login", "single sign-on")
    for kw in login_titles:
        if kw in title and "log index" not in title and "log into" not in title:
            return {"reason": f"Login page detected (title: '{await page.title()}')", "url": url}

    return None


async def dismiss_cookies(page):
    """Try to dismiss cookie consent banners."""
    for sel in COOKIE_SELECTORS:
        try:
            btn = page.locator(sel).first
            if await btn.is_visible(timeout=300):
                await btn.click()
                await asyncio.sleep(0.5)
                return True
        except Exception:
            continue
    return False


async def capture(args):
    from playwright.async_api import async_playwright

    out_dir = Path(args.out) / f"{date.today():%Y-%m-%d}-{args.test_id}"
    out_dir.mkdir(parents=True, exist_ok=True)

    try:
        async with async_playwright() as p:
            if args.mode == "cdp":
                browser = await p.chromium.connect_over_cdp(CDP_URL)
                context = browser.contexts[0] if browser.contexts else await browser.new_context(
                    viewport={"width": 1440, "height": 900}
                )
                should_close = False
            else:
                browser = await p.chromium.launch(headless=False, channel="chrome")
                context = await browser.new_context(viewport={"width": 1440, "height": 900})
                should_close = True

            page = await context.new_page()

            resp = await page.goto(args.url, wait_until="domcontentloaded", timeout=30_000)

            status = resp.status if resp else 0
            if status in (401, 403):
                await page.close()
                result_json(
                    needs_user_action=True,
                    reason=f"HTTP {status} — access denied",
                    url=args.url,
                )

            await asyncio.sleep(args.wait)

            if not getattr(args, 'no_auth_check', False):
                auth = await detect_auth_wall(page)
                if auth:
                    await page.close()
                    result_json(needs_user_action=True, **auth)

            await dismiss_cookies(page)

            if args.scroll:
                for _ in range(3):
                    await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                    await asyncio.sleep(0.8)
                await page.evaluate("window.scrollTo(0, 0)")
                await asyncio.sleep(0.5)

            result = {"success": True}

            if args.selector:
                element = page.locator(args.selector).first
                png_path = out_dir / f"{args.stem}.png"
                await element.screenshot(path=str(png_path))
                result["png"] = str(png_path)
            else:
                png_path = out_dir / f"{args.stem}.png"
                await page.screenshot(path=str(png_path), full_page=args.full_page)
                result["png"] = str(png_path)

            png_prov = provenance.record_item(
                out_dir, png_path, kind="screenshot",
                source_url=args.url, test_id=args.test_id,
            )
            result["source_url"] = args.url
            result["captured_at"] = png_prov["captured_at"]
            result["operator"] = png_prov["operator"]
            result["sha256"] = png_prov["sha256"]

            if args.pdf:
                pdf_page = await context.new_page()
                await pdf_page.goto(args.url, wait_until="domcontentloaded", timeout=30_000)
                await asyncio.sleep(args.wait)
                await dismiss_cookies(pdf_page)
                if args.scroll:
                    for _ in range(3):
                        await pdf_page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                        await asyncio.sleep(0.8)

                pdf_path = out_dir / f"{args.stem}.pdf"
                await pdf_page.pdf(
                    path=str(pdf_path),
                    format="A4",
                    print_background=True,
                    margin={"top": "15mm", "bottom": "15mm", "left": "10mm", "right": "10mm"},
                )
                result["pdf"] = str(pdf_path)
                pdf_prov = provenance.record_item(
                    out_dir, pdf_path, kind="screenshot_pdf",
                    source_url=args.url, test_id=args.test_id,
                )
                result["pdf_sha256"] = pdf_prov["sha256"]
                await pdf_page.close()

            result["output_dir"] = str(out_dir)
            result["manifest"] = str(provenance.manifest_path(out_dir))
            await page.close()
            if should_close:
                await browser.close()

            print(json.dumps(result))

    except Exception as e:
        error_msg = str(e)
        if "connect" in error_msg.lower() or "ECONNREFUSED" in error_msg:
            result_json(
                success=False,
                error=f"Cannot connect to Chrome CDP: {error_msg}",
                fix="Run: python3 screenshot_capture.py prepare-chrome",
            )
        else:
            result_json(success=False, error=error_msg)


def main():
    parser = argparse.ArgumentParser(description="CDP screenshot capture for compliance evidence")
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("check", help="Verify CDP connectivity")
    sub.add_parser("prepare-chrome",
                   help="Copy Chrome profile + launch with CDP (reuses cookies/sessions)")

    sso_p = sub.add_parser("ensure-sso",
                           help="Navigate to SSO dashboard, verify logged in")
    sso_p.add_argument("--sso-url", default=None,
                       help="SSO/IdP dashboard URL (default: sso_url from config.yaml)")
    sso_p.add_argument("--wait", type=float, default=3.0)

    cap = sub.add_parser("capture", help="Capture a screenshot")
    cap.add_argument("url", help="URL to capture")
    cap.add_argument("--test-id", default="unknown", help="Test/control ID for output folder")
    cap.add_argument("--stem", default="screenshot", help="Output filename stem")
    cap.add_argument("--scroll", action="store_true", help="Scroll to bottom before capture")
    cap.add_argument("--pdf", action="store_true", help="Also save a PDF version")
    cap.add_argument("--full-page", action="store_true", default=True)
    cap.add_argument("--no-full-page", dest="full_page", action="store_false")
    cap.add_argument("--no-auth-check", action="store_true",
                     help="Skip auth wall detection (use when target IS an auth provider)")
    cap.add_argument("--selector", help="CSS selector to screenshot a specific element")
    cap.add_argument("--wait", type=float, default=2.0,
                     help="Seconds to wait after page load (default: 2)")
    cap.add_argument("--out", default=str(DEFAULT_OUT), help="Base output directory")
    cap.add_argument("--mode", choices=["cdp", "launch"], default="cdp",
                     help="cdp (connect to running Chrome) or launch (fresh browser)")

    args = parser.parse_args()

    if args.command == "check":
        asyncio.run(check_cdp())
    elif args.command == "prepare-chrome":
        prepare_chrome_profile()
    elif args.command == "ensure-sso":
        sso_url = args.sso_url if args.sso_url is not None else _default_sso_url()
        asyncio.run(ensure_sso(sso_url, args.wait))
    elif args.command == "capture":
        asyncio.run(capture(args))
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
