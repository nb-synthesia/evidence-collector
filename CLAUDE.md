# Vanta Evidence Collector

Automated Vanta evidence collection pipeline. One command iterates the pending
backlog: identify → research → navigate → screenshot → explain → audit (QA
gate) → upload (draft).

This is the open-source, organization-agnostic edition. All organization
specifics (systems, IdP, playbooks) live in `config.yaml` and the `knowledge/`
files, which you fill in for your own environment after forking.

## Quality gate at a glance

Before anything reaches Vanta, every package passes a two-layer QA gate
(step 4e). The policy is **annotate, with blocking only on hard deterministic
failures**:

- **Deterministic layer** (`scripts/evidence_audit.py`): cheap, reproducible
  checks (provenance/checksum integrity, captured a login wall, evidence
  outside the audit period, blank/corrupt images, system coverage). A `block`
  verdict here is the ONLY thing that stops an upload.
- **Semantic layer** (framework-tailored evaluator sub-agent): a skeptical
  auditor persona (per `knowledge/rubrics/<framework>.yaml`) reads the package
  and returns `pass | revise | escalate` with a confidence score. Its findings
  never hard-block — they **annotate** the draft so the human reviewer knows
  exactly where to look.

The named-human gate is unchanged: everything is uploaded as a draft and a
human submits.

## IMPORTANT: Use Playwright CDP for screenshots, not "Claude in Chrome"

**Do NOT use `mcp__Claude_in_Chrome__*` tools for screenshots.** They are
unreliable and often disconnected. Instead, ALWAYS use the Playwright CDP
scripts in `scripts/`. These scripts:
- Copy the user's Chrome profile (cookies, SSO sessions)
- Launch a separate Chrome instance with remote debugging
- Capture full-page screenshots reliably via Playwright
- Work in both Claude Desktop and Claude Code / Cursor

The only prerequisite is running `prepare-chrome` once per session (step 1).

## Configuration

Organization settings live in `config.yaml` at the repo root (created by
`install.sh` from `config.example.yaml`). It is NOT secret and can be committed
to your fork so your whole team shares it:

```yaml
sso_url: https://your-idp.example.com   # your IdP end-user dashboard
vanta_region: us                        # us | eu | gov
playbook:
  backend: local                        # local | notion | confluence | google_doc | url
  path: knowledge/playbooks             # used when backend == local
  base_document_url: ""                 # root page/doc for external backends
  base_document_id: ""
```

## Prerequisites

### Credentials

OAuth credentials live at `~/.vanta/credentials.json` (never committed):
```json
{"client_id": "vci_...", "client_secret": "vcs_..."}
```
Region comes from `config.yaml` (`vanta_region`) or the `VANTA_REGION` env var.

### Script locations

Resolve the repo directory first:

```bash
SKILL_DIR="$(cd "$(dirname "$0")" && pwd)"  # if running from repo root
# OR: set to wherever the skill is installed/symlinked, e.g.
SKILL_DIR="$HOME/.claude/skills/get-vanta-evidence"
```

All scripts:
```
$SKILL_DIR/scripts/vanta_client.py        # Vanta REST API (list, upload)
$SKILL_DIR/scripts/screenshot_capture.py  # Playwright CDP screenshots
$SKILL_DIR/scripts/evidence_report.py     # Explainer PDF generator
$SKILL_DIR/scripts/evidence_audit.py      # Deterministic QA gate
```

### Python dependencies

```bash
pip install -r "$SKILL_DIR/requirements.txt"
```

## Invocation

```
/get-vanta-evidence                      # all pending items
/get-vanta-evidence CC8.1               # specific control
/get-vanta-evidence 69f9b90fae863182... # specific document/test ID
/get-vanta-evidence soc2                # all pending SOC 2 items
/get-vanta-evidence iso27001            # all pending ISO 27001 items
```

## Pipeline (step by step)

### 0. Sync knowledge base (and verify you're on the latest version)

Before starting, make sure you're running the latest skill + knowledge that
teammates have pushed (new access issues, infrastructure corrections, SSO
tiles, rubric tweaks). The canonical version lives on **`origin/main`**.

This check is deliberately **non-destructive**: it never switches branches,
discards local work, or hides failures. It fetches, then reports how the
working copy compares to `origin/main`, and only fast-forwards when that is
safe (on `main`, clean tree). Surface the result to the user — do not silently
`|| true` past a sync problem.

```bash
cd "$SKILL_DIR" || exit 1

git fetch origin --quiet || echo "WARN: git fetch failed — proceeding on local version, may be stale"

BRANCH="$(git rev-parse --abbrev-ref HEAD)"
read BEHIND AHEAD < <(git rev-list --left-right --count HEAD...origin/main 2>/dev/null | awk '{print $2, $1}')
DIRTY="$(git status --porcelain)"

echo "skill version: branch=$BRANCH  behind origin/main=${BEHIND:-?}  ahead=${AHEAD:-?}  dirty=$([ -n "$DIRTY" ] && echo yes || echo no)"

if [ "$BRANCH" = "main" ] && [ -z "$DIRTY" ] && [ "${BEHIND:-0}" -gt 0 ]; then
  git merge --ff-only origin/main && echo "fast-forwarded main to origin/main"
fi
```

Interpret and ACT on the output before continuing:
- **On `main`, clean, behind** → it just fast-forwarded; you're current.
- **Not on `main`, or behind `origin/main`** → tell the user they may be
  missing recent knowledge/fixes, and ask whether to continue or pull first.
- **Dirty working tree** → mention it (don't auto-stash); proceed with awareness.
- **`fetch` failed** → warn that knowledge may be stale, continue only if OK.

### 1. Check prerequisites and establish SSO

Run these commands in order. Do not skip any.

```bash
# 1a. Credentials exist?
test -f ~/.vanta/credentials.json || echo "MISSING: create ~/.vanta/credentials.json"

# 1b. Deps installed?
python3 -c "import playwright, requests, reportlab, yaml" 2>/dev/null \
  || pip install -r "$SKILL_DIR/requirements.txt"

# 1c. Prepare Chrome: copy profile + launch with CDP
#     This opens a NEW Chrome window with the user's cookies/sessions.
#     If CDP is already running, this is a no-op.
python3 "$SKILL_DIR/scripts/screenshot_capture.py" prepare-chrome

# 1d. Verify SSO session (navigates to sso_url from config.yaml)
python3 "$SKILL_DIR/scripts/screenshot_capture.py" ensure-sso
```

If `ensure-sso` returns `needs_user_action`, tell the user:
> "A Chrome window opened. Please log in to your IdP there.
> Once you see the dashboard, tell me and I'll continue."

Wait for the user to confirm, then re-run `ensure-sso` to verify.
Once SSO is established, all apps accessible through the IdP will work.

**SSO tile URLs**: Services that don't SSO from their direct URL need the IdP
tile. See `knowledge/sso-tiles.yaml` for the mapping.

### 2. Enumerate pending items

If a Vanta MCP server is configured for reads, use it. Otherwise use the REST
client:

```bash
python3 "$SKILL_DIR/scripts/vanta_client.py" list-pending [--framework soc2]
python3 "$SKILL_DIR/scripts/vanta_client.py" list-tests [--framework soc2]
python3 "$SKILL_DIR/scripts/vanta_client.py" list-frameworks
```

If a specific ID or URL was provided, extract the document/test slug and fetch:
```bash
python3 "$SKILL_DIR/scripts/vanta_client.py" get-document <DOC_ID>
```

If a Vanta URL is provided (e.g. `https://app.vanta.com/c/<org>/tests/<slug>`),
extract the slug from the URL path and search for it in the document/test list.

### 3. Triage

Present the user with a summary table:

| # | Title | Framework | Status | Due date |
|---|-------|-----------|--------|----------|

Ask: "I found N pending items. Process all, or pick specific ones?"

### 4. For each item: capture evidence

#### 4a. Understand the ask

Read the document/test `description` — this is what the auditor wants.

#### 4b. Research how your organization handles it

Before capturing anything, check the knowledge base. This is what turns a
screenshot-taking bot into something useful — it captures the RIGHT pages.

**Step 1 — Local index (instant, no API calls):**
1. Read `knowledge/playbooks/index.yaml` — find the matching category by
   control name. The `summary` orients you; the `systems` list is what the QA
   gate checks for coverage.
2. Read `knowledge/access-issues.yaml` — check for known blockers with the
   target systems before attempting capture.
3. Read `knowledge/infrastructure.yaml` — get system URLs and config.
4. Read `knowledge/sso-tiles.yaml` — get SSO tile URLs.
5. Read `knowledge/rubrics/<framework>.yaml` — the same rubric the QA gate uses
   in step 4e. Reading the `evaluator.focus` NOW lets you capture the right
   evidence first time (e.g. for SOC 2 Type II, capture period-spanning
   records, not just a point-in-time config).

**Step 2 — Fetch the detailed playbook (base document):**
Resolve `playbook.backend` from `config.yaml` and fetch the playbook referenced
by `playbook_ref` in `index.yaml`:
- `local` → read the markdown file at `<playbook.path>/<playbook_ref>`.
- `notion` → fetch the Notion page whose ID is `playbook_ref` (Notion MCP).
- `confluence` / `google_doc` / `url` → fetch the referenced page/URL.
The playbook has the full evidence strategy, narrative framing, URLs that
work/don't work, and gotchas learned from previous runs.

**Step 3 — Organizational research (optional):**
If you have access to your org's knowledge system (Notion/Confluence/wiki via
MCP or search), query it to understand the real implementation:
> "How does our organization handle <control topic>?"
This surfaces process docs, policies, and ownership info that complement the
operational playbook. Skip if no such source is configured.

#### 4c. Capture screenshots

**ALWAYS use the Playwright CDP script, never Claude in Chrome.**

```bash
python3 "$SKILL_DIR/scripts/screenshot_capture.py" capture "<URL>" \
  --test-id "<control_id>" \
  --stem "<NN>_<system>_<what>" \
  [--scroll] [--pdf] [--wait 3]
```

The script outputs JSON to stdout:
```json
{"success": true, "png": "/path/to/file.png", "pdf": "/path/to/file.pdf",
 "source_url": "<URL>", "captured_at": "2026-05-24T09:14:02Z",
 "operator": "you@org.com", "sha256": "<hex>",
 "manifest": "/path/to/manifest.json"}
```

Every capture is automatically recorded in a `manifest.json` in the output
folder: the source URL, capture time (UTC), operator identity, and a SHA-256
checksum of the exact bytes. This is the provenance trail — do not edit it by
hand. Operator identity comes from `EVIDENCE_OPERATOR`/`VANTA_OPERATOR`, else
`git config user.email`, else the OS user.

If the script returns `needs_user_action`:
```json
{"needs_user_action": true, "reason": "login form detected", "url": "..."}
```
Tell the user: "I need you to log in to [system] in the CDP Chrome window.
Let me know when done and I'll retry."

**Stem naming convention:** `<NN>_<system>_<what>`
Examples: `01_github_branch_protection`, `02_idp_mfa_policy`,
`03_monitoring_error_logs`, `04_cloud_audit_config`

Multiple screenshots per item are encouraged — capture all relevant views.

#### 4d. Generate explainer PDF

```bash
python3 "$SKILL_DIR/scripts/evidence_report.py" \
  --title "Evidence: <document_title>" \
  --description "<auditor_ask_text>" \
  --files "01_screenshot.png,02_screenshot.png" \
  --explanation "<your analysis of how this evidence satisfies the control>" \
  --output "/path/to/evidence_<control>.pdf"
```

The explainer should be factual and auditor-facing. Explain *what* the
screenshots show and *how* they satisfy the control requirement.

The report generator embeds a **Provenance** table (each file's source, capture
time, and SHA-256), records the operator and generation time on the cover,
appends the explainer PDF's own checksum to `manifest.json`, and prints that
checksum in its JSON output. It reads source/timestamp per file from the
`manifest.json` written during capture, so run capture first. Pass `--operator`
to override the recorded identity; `--no-provenance` to skip (not recommended).

#### 4e. Audit & evaluate (QA gate)

This is the quality loop. Run it on the assembled package (screenshots +
explainer + `manifest.json`) BEFORE uploading. Policy: **annotate, with
blocking only on hard deterministic failures.**

**Step 1 — Deterministic gate (always run, cheap):**

```bash
python3 "$SKILL_DIR/scripts/evidence_audit.py" \
  --dir "<EVIDENCE_DIR>" \
  --framework <soc2|iso27001|hitrust> \
  --control "<control name>" \
  --expected-systems "<comma list from playbooks/index.yaml 'systems'>" \
  [--period-start YYYY-MM-DD --period-end YYYY-MM-DD]
```

Pass the real engagement window via `--period-start/--period-end` when you know
it — that turns out-of-period captures into a hard block. Without it, freshness
is a warn only.

The script writes `audit.json` into the evidence dir, prints a JSON report, and
exits non-zero (`2`) on a `block` verdict. Read the `verdict` field:

- **`block`** → a hard, objective defect (checksum mismatch, captured login
  wall, evidence outside the audit period, blank/corrupt image, nothing
  captured). **Do NOT upload.** Inspect `blocking` + `checks`, then enter the
  remediation loop below.
- **`warn`** / **`pass`** → proceed to the semantic evaluator (Step 2). Carry
  the warnings forward into the QA report so they annotate the draft.

**Step 2 — Semantic evaluator sub-agent (skeptical, framework-tailored):**

When the deterministic gate is not `block`, launch a SEPARATE evaluator
sub-agent (fresh context, do not let the capture agent grade its own work).
Give it the `evaluator` section of `knowledge/rubrics/<framework>.yaml`, the
auditor ask, the document **cadence** (`renewalCadence` from the Vanta document
— e.g. `P3M` = quarterly), the explainer text, the screenshots, and
`audit.json`. The cadence matters: per the rubric's `cadence_awareness`, the
evaluator must judge period coverage against the cadence interval, NOT a fixed
12 months. Instruct the sub-agent to adopt the skeptical persona and return
strict JSON:

```json
{"verdict": "pass|revise|escalate", "confidence": 0-100,
 "rationale": "one paragraph",
 "deltas": [{"system": "...", "issue": "...", "fix": "specific action"}],
 "residual_concerns": ["for the human reviewer"]}
```

**Step 3 — Act on the combined result (bounded loop, max 2 remediation passes):**

| Result | Action |
|---|---|
| det=`block` | Do not upload. Auto-remediate fixable items (loop to 4c/4d). If still blocked after 2 passes, **escalate to the human** with the defect list. |
| eval=`revise` (budget left) | Loop back to 4c/4d with the `deltas`, re-run 4e. |
| eval=`revise` (budget spent) or `escalate` | Proceed to upload, but **flag for the human** — the residual concerns go in the QA report. |
| det in {`warn`,`pass`} and eval=`pass` | Proceed to upload. |

Keep a hard cap of **2** remediation iterations per item. Never silently pass a
blocked item and never auto-submit.

**Step 4 — Write the QA report (annotation):**

Combine the deterministic `audit.json` and the evaluator JSON into a short
`qa_report.md` in the evidence dir: verdict, confidence, what was checked,
warnings, and residual concerns for the human. This annotation travels with the
draft.

#### 4f. Upload to Vanta (draft only)

```bash
# Upload the explainer PDF
python3 "$SKILL_DIR/scripts/vanta_client.py" upload <DOC_ID> \
  "/path/to/evidence_report.pdf" \
  --description "Evidence package for <control>"

# Upload individual screenshots
python3 "$SKILL_DIR/scripts/vanta_client.py" upload <DOC_ID> \
  "/path/to/01_screenshot.png" \
  --description "<what this screenshot shows>"

# Attach the provenance manifest + QA report so the package is self-describing
python3 "$SKILL_DIR/scripts/vanta_client.py" upload <DOC_ID> \
  "/path/to/manifest.json" \
  --description "Provenance manifest (source, timestamp, operator, SHA-256 per item)"
python3 "$SKILL_DIR/scripts/vanta_client.py" upload <DOC_ID> \
  "/path/to/qa_report.md" \
  --description "QA report — deterministic audit + skeptical <framework> evaluator (confidence: NN)"
```

**IMPORTANT: Do NOT submit the document.** Evidence stays in draft state. The
user reviews in Vanta and submits manually. Items the QA gate flagged
(`warn`/`revise`/`escalate`) should be called out in the run summary so the
human reviews those first.

### 5. Summary

After processing all items, report. Include the QA verdict + evaluator
confidence so the human knows which drafts to scrutinize first:

```
Completed (uploaded to Vanta as draft):
  - CC8.1 — Change Management (3 screenshots + explainer) → <vanta_url>
        QA: pass · evaluator confidence 92
  - CC7.2 — Audit Logging (2 screenshots + explainer) → <vanta_url>
        QA: warn (freshness) · evaluator confidence 71 · REVIEW FIRST

Blocked (NOT uploaded — hard deterministic failure):
  - CC9.1 — captured a login wall after SSO expired; re-capture needed

Needs attention (escalated to you):
  - CC6.1 — Login required for <system>, user did not respond

Reminder: review uploaded evidence in Vanta and submit when ready.
Prioritize anything marked REVIEW FIRST or with confidence < 80.
```

### 6. Post-capture: update the knowledge base

After every run, update knowledge with lessons learned. This makes the skill
self-improving across sessions.

**Base document playbooks (dynamic, changes every run):**
Update the matching playbook (local markdown, or Notion/Confluence/Google Doc
per your backend) with: refined narrative framing, new URLs discovered (working
or broken), updated screenshot strategies, and a dated run-history entry.

**Git YAML files (static index, changes rarely):**
Only commit to git when something structural changed:

| What changed | File to update |
|---|---|
| New access blocker or workaround discovered | `knowledge/access-issues.yaml` |
| New SSO tile URL found | `knowledge/sso-tiles.yaml` |
| Infrastructure fact corrected (URL, account, service) | `knowledge/infrastructure.yaml` |
| New control category added | `knowledge/playbooks/index.yaml` |

```bash
git add knowledge/
git commit -m "knowledge: <what was learned>"
git push
```

Always append, never delete existing entries — the history of what worked (and
what didn't) is valuable context for future runs.

See `knowledge/README.md` for the schema of each file.

## Handling auth and access issues

**CRITICAL: NEVER kill Chrome or the CDP Chrome process.** Killing Chrome (even
the CDP instance) destroys all established SSO sessions across every service. If
the CDP Chrome becomes unresponsive, ask the user to close and reopen it
manually. NEVER use `kill`, `pkill`, or `killall` on Chrome processes. If
`prepare-chrome` says "Chrome CDP already running", that's fine — just use the
existing instance.

**SSO flow for ALL services**: Always go through your IdP dashboard
(`sso_url`) first:
1. Navigate to the IdP dashboard (the tiles/bookmarks page)
2. If you know the tile URL, navigate directly to it (see `knowledge/sso-tiles.yaml`)
3. If you don't, search the tiles page for the service name
4. Click the tile — the IdP handles SAML/OIDC SSO automatically
5. You land on the service's dashboard with full access
NEVER navigate directly to a service URL when SSO is required — it will show a
login page and tell you to use your IdP. Always go via the tile.

**Login/SSO screen**: Pause, tell the user which system needs login in the CDP
Chrome window, wait for confirmation.
**MFA/2FA prompt**: Same — the user must handle it manually in the CDP Chrome.
**Cookie consent**: Auto-dismissed by the screenshot script. If it persists,
tell the user to dismiss it in the CDP Chrome window, then retry.
**Page requires permissions**: Note it in the explainer PDF, suggest the user
grant access and re-run for that item.
**Service needs a tile URL**: Some services don't SSO from their direct URL.
Check `knowledge/sso-tiles.yaml`.

## SIEM fallback strategy

When direct admin access is unavailable (e.g. a policy page that needs an
elevated admin role), supplement evidence by capturing the corresponding logs
in your SIEM (see the `siem` entry in `knowledge/infrastructure.yaml`). This
demonstrates that:
1. The system is a monitored data source (screenshot the data sources page)
2. Authentication/policy events are being captured (search for the system)
3. Detections are active (show relevant alerts or correlations)

Always disclose in the explainer that this is corroborating log evidence rather
than the primary configuration page, and suggest the owner pull the config page
if the auditor needs it.

## Output organization

```
~/Downloads/compliance-evidence/
└── <YYYY-MM-DD>-<test_id>/
    ├── 01_<stem>.png
    ├── 02_<stem>.png
    ├── 01_<stem>.pdf           (if --pdf used)
    ├── evidence_<control>.pdf  ← explainer PDF (uploaded to Vanta)
    ├── manifest.json           ← provenance: source, timestamp, operator, SHA-256 per item
    ├── audit.json              ← deterministic QA gate result (evidence_audit.py)
    └── qa_report.md            ← combined QA annotation (audit + evaluator) uploaded with the draft
```
