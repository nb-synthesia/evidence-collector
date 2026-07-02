# Vanta Evidence Collector

An open-source AI-agent skill that automates compliance evidence collection for
[Vanta](https://www.vanta.com/) audits. Give it a control ID, a framework, or
just say "process the backlog" — it researches your infrastructure, captures the
right screenshots via Playwright CDP, generates auditor-facing explainer PDFs,
and uploads everything to Vanta **as drafts for human review**.

It runs as a skill inside AI coding assistants (Claude Desktop, Claude Code,
Cursor) and is organization-agnostic: everything specific to your company lives
in `config.yaml` and the `knowledge/` files, which you fill in after forking.

```
/get-vanta-evidence CC8.1     →  captures change management evidence
/get-vanta-evidence soc2      →  processes all pending SOC 2 items
/get-vanta-evidence            →  processes the entire pending backlog
```

## What it does

For each pending Vanta document, the agent:

1. **Reads the auditor ask** from the Vanta API
2. **Researches** how your org handles it (knowledge base + playbook base document)
3. **Navigates** to source systems via your existing Chrome sessions (CDP)
4. **Screenshots** the relevant pages with full-page capture
5. **Generates** an explainer PDF connecting evidence to the control
6. **Audits** the package through a two-layer QA gate (deterministic checks +
   a skeptical, framework-tailored evaluator sub-agent)
7. **Uploads** to Vanta as a draft (never auto-submits)

### The QA gate (evaluator loop)

Before anything reaches Vanta, every package goes through a quality gate. The
policy is **annotate, with blocking only on hard deterministic failures**:

- **Deterministic layer** — [`scripts/evidence_audit.py`](scripts/evidence_audit.py)
  runs cheap, reproducible checks: provenance/checksum integrity, "did we
  capture a login wall?", evidence-within-the-audit-period, blank/corrupt
  images, and expected-system coverage. A `block` verdict here is the *only*
  thing that stops an upload. It writes `audit.json` next to the evidence.
- **Semantic layer** — a separate evaluator sub-agent adopts a skeptical auditor
  persona from [`knowledge/rubrics/<framework>.yaml`](knowledge/rubrics/) (SOC 2
  Type II operating-effectiveness, ISO 27001 ISMS process+records, HITRUST
  PRISMA maturity) and returns `pass | revise | escalate` with a confidence
  score. Its findings never hard-block — they **annotate** the draft
  (`qa_report.md`) so the human reviewer knows where to look.

On `revise`, the gate loops back to capture/explain with specific deltas
(bounded to 2 passes). The named-human gate is unchanged — drafts only, a human
submits.

### Provenance

Every run writes a `manifest.json` next to the captured files recording, per
item: the **source URL**, the **capture time (UTC)**, the **operator**, and a
**SHA-256 checksum** of the exact bytes. The explainer PDF embeds this as a
Provenance table and is itself hashed into the manifest. Any export can be
traced to its source, and re-hashing proves it is unchanged since capture.

## Architecture

```
├── CLAUDE.md                    # Agent instructions (the pipeline)
├── SKILL.md                     # Thin entry point for skill loaders
├── config.example.yaml          # Org config template (copied to config.yaml by install.sh)
├── install.sh                   # Interactive setup + symlink into skill dirs
├── scripts/
│   ├── config.py                # Loads config.yaml (sso_url, region, playbook backend)
│   ├── screenshot_capture.py    # Playwright CDP screenshot capture
│   ├── vanta_client.py          # Vanta REST API client
│   ├── evidence_report.py       # Explainer PDF generator
│   ├── evidence_audit.py        # Deterministic QA gate (verdict: pass|warn|block)
│   └── provenance.py            # Per-item manifest: source, timestamp, operator, SHA-256
└── knowledge/                   # Shared learning layer (git-tracked)
    ├── README.md                # Schema documentation
    ├── infrastructure.yaml      # System URLs, accounts, service map (template)
    ├── access-issues.yaml       # Known blockers + workarounds (grows over time)
    ├── sso-tiles.yaml           # SSO/IdP tile URL mapping (template)
    ├── rubrics/                 # Per-framework QA rubrics (framework-general)
    │   ├── soc2.yaml
    │   ├── iso27001.yaml
    │   └── hitrust.yaml
    └── playbooks/               # Local playbook base document (default backend)
        ├── index.yaml           # control → systems + playbook_ref routing table
        └── *.md                 # per-category evidence strategies
```

### The knowledge layer

The `knowledge/` directory is the key idea. It splits into a **static index**
(git-tracked YAML: system map, access issues, SSO tiles, control→playbook
routing) and a **detailed playbook layer** — the "base document" — whose
location you choose (local markdown, Notion, Confluence, Google Doc, or a URL).

After every run the agent updates these with what it learned: discovered URLs,
access issues, narrative framing that worked. This means each run gets smarter,
knowledge is shared via Git, and learnings are code-reviewable in PRs.

## Setup

> **This project is designed to be forked.** The `knowledge/` files and
> `config.yaml` are your organization's rules and live in *your* copy of the
> repo on GitHub, so your whole team shares the same knowledge and it stays
> reviewable via pull requests.

### 1. Fork, then clone your fork

Fork this repository on GitHub (click **Fork**), then:

```bash
git clone https://github.com/<your-org>/<your-fork>.git
cd <your-fork>
./install.sh
```

`install.sh` is interactive. It checks prerequisites (Python, Playwright,
Chrome, optional MCP servers), then asks you for:

- your **IdP / SSO dashboard URL** (`sso_url`),
- your **Vanta API region** (`us` / `eu` / `gov`),
- where your **playbook base document** lives (local markdown / Notion /
  Confluence / Google Doc / URL).

It writes these to `config.yaml` and symlinks the repo into
`~/.claude/skills/` and/or `~/.cursor/skills/`.

### 2. Vanta API credentials

```bash
mkdir -p ~/.vanta
cat > ~/.vanta/credentials.json << 'EOF'
{
  "client_id": "vci_...",
  "client_secret": "vcs_..."
}
EOF
```

Get these from **Vanta → Settings → API → OAuth Clients**. This file is never
committed.

### 3. Fill in your knowledge base

Edit the templates in `knowledge/` for your environment:

1. `knowledge/infrastructure.yaml` — your systems, URLs, accounts
2. `knowledge/sso-tiles.yaml` — your IdP tile URLs
3. `knowledge/playbooks/index.yaml` — map your Vanta controls to systems + playbooks
4. Playbooks — write them in your chosen base document (the `knowledge/playbooks/*.md`
   files are examples for the `local` backend)
5. `knowledge/access-issues.yaml` — starts nearly empty; grows as the agent learns

Commit these to your fork so your team shares them.

### 4. First run

Open Claude Desktop / Claude Code / Cursor and say:

```
/get-vanta-evidence <a Vanta document or test URL>
```

The agent prepares a Chrome CDP instance (copying your existing Chrome profile
for SSO sessions), verifies IdP connectivity, and starts processing.

## The "base document" (playbook backend)

The detailed, frequently-updated playbooks live in a **base document** whose
backend you pick at install time (`config.yaml → playbook.backend`):

| Backend | `playbook_ref` in `index.yaml` means | Needs |
|---|---|---|
| `local` (default) | a markdown filename under `knowledge/playbooks/` | nothing extra |
| `notion` | a Notion page ID | Notion MCP server |
| `confluence` | a Confluence page ID/URL | Confluence access/MCP |
| `google_doc` | a Google Doc URL/ID | Google access/MCP |
| `url` | any full URL | network access |

`local` is the zero-dependency default and keeps playbooks versioned in Git
alongside the index. Choose an external backend if your team already maintains
runbooks there.

## Contributing knowledge (within your fork)

After an evidence run, the agent updates `knowledge/` files. To share:

```bash
git checkout -b knowledge/add-firewall-urls
git add knowledge/
git commit -m "knowledge: add firewall URL discovery pattern"
git push -u origin HEAD
# open a PR for team review
```

See [`knowledge/README.md`](knowledge/README.md) for the schema of each file.

## Requirements

- Python 3.10+
- Google Chrome (the CDP screenshot capture copies your profile for SSO)
- `pip install -r requirements.txt` then `playwright install chromium`
- A Vanta account with API OAuth credentials
- An AI assistant that loads skills (Claude Desktop, Claude Code, or Cursor)

## Security notes

- Credentials live only in `~/.vanta/credentials.json` and are never committed
  (`.gitignore` also blocks `credentials.json`, `*.png`, `*.pdf`, evidence dirs).
- The agent uploads to Vanta as **drafts only** and never submits — a named
  human reviews and submits.
- **Never kill the CDP Chrome process** — it destroys all live SSO sessions.
- Review captures for secrets/PII before submitting to an auditor (the evaluator
  rubric flags obvious exposures, but the human review is the backstop).

## License

Licensed under the **GNU General Public License v3.0** — see [LICENSE](LICENSE).
Its strong copyleft means derivative works that are distributed must also be
released under GPL-3.0 with source, which keeps this tool and its improvements
open.
