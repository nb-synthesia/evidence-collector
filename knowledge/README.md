# Knowledge Base

The knowledge layer is what turns a screenshot bot into a useful evidence
collector: it remembers your systems, your access quirks, and the strategy for
each control. It is split into a **static index** (these git-tracked YAML files,
fast local lookup) and a **detailed playbook layer** (the "base document" —
configurable per organization).

```
Git (static index — changes rarely)        Base document (detailed playbooks)
┌───────────────────────────────┐          ┌────────────────────────────────┐
│ infrastructure.yaml           │          │ Playbook: Change Management     │
│ access-issues.yaml            │          │ Playbook: Monitoring & Logging  │
│ sso-tiles.yaml                │          │ ...                             │
│ playbooks/index.yaml ─────────────ref────▶│                                 │
│   (control → systems + ref)   │          │  local md / Notion / Confluence │
│ rubrics/<framework>.yaml      │          │  / Google Doc / URL             │
└───────────────────────────────┘          └────────────────────────────────┘
        ~0ms to read                          fetched on demand per control
```

Where the base document lives is set in `config.yaml` (`playbook.backend`):
`local` (markdown files in this repo), `notion`, `confluence`, `google_doc`, or
`url`. `install.sh` asks you which one and records it.

## File schema

### `infrastructure.yaml`

Maps your systems, URLs, accounts, and configurations so the agent knows where
to navigate.

```yaml
<service_key>:
  name: Human-readable name
  url: Primary URL
  sso: okta_tile | direct | manual
  key_pages:
    <page_name>: <url>
  notes: Free-text operational notes
  corrections:
    - date: YYYY-MM-DD
      note: What changed and why
```

### `access-issues.yaml`

Known blockers discovered during collection. Checked BEFORE capture to avoid
wasted time and burned SSO sessions.

```yaml
- system: System name
  url: Optional URL that triggers the issue
  issue: What goes wrong
  workaround: How to get evidence anyway
  discovered: YYYY-MM-DD
```

### `sso-tiles.yaml`

SSO/IdP tile URL mapping. Many services only SSO correctly when the flow is
initiated from the IdP tile rather than by direct navigation.

```yaml
tiles:
  - service: Service name
    tile_url: https://your-idp.example.com/home/...
    post_sso_url: Where you land after SSO (optional)
    notes: Any quirks (optional)
```

### `playbooks/index.yaml`

The routing table. Maps each Vanta control to a category, the systems involved,
and a `playbook_ref` pointing into the base document.

```yaml
categories:
  - id: category-slug
    title: Human-readable title
    controls:
      - Control Name 1
    systems: [system-1, system-2]
    playbook_ref: <filename | page-id | url>   # interpreted per config.playbook.backend
    summary: One-line pattern summary
```

### `rubrics/<framework>.yaml`

Per-framework QA rubrics for the evidence quality gate (step 4e in `CLAUDE.md`).
One file per framework: `soc2`, `iso27001`, `hitrust`. Two layers:

```yaml
framework: soc2
audit_period: {}        # { start: YYYY-MM-DD, end: YYYY-MM-DD } — empty = soft freshness only
max_age_days: 365

deterministic:          # consumed by scripts/evidence_audit.py (hard gate)
  require_explainer: true
  require_provenance: true
  min_screenshots: 1
  min_image_width: 320
  block_on_login_page: true
  block_on_checksum_mismatch: true
  block_on_period_violation: true

evaluator:              # consumed by the skeptical evaluator sub-agent (annotation only)
  persona: ...
  focus: [ ... ]
  rubric: [ { dimension, question }, ... ]
  red_flags: [ ... ]
  verdict_guidance: ...
```

The deterministic block is the only thing that can hard-block an upload; the
evaluator block only annotates the draft. The three shipped rubrics are
framework-general and usually need no edits — tune the `evaluator` prose as you
learn what your auditor cares about.

## How to update

### After every evidence run

Update the **base document** playbook for the control you just ran: refined
narrative, new working/broken URLs, screenshot strategy, a dated run-history
note.

### When something structural changes (rare)

Commit and push the git YAML files:

| What changed | File to update |
|---|---|
| New access blocker discovered | `access-issues.yaml` |
| New SSO tile URL found | `sso-tiles.yaml` |
| Infrastructure fact corrected | `infrastructure.yaml` |
| New control category added | `playbooks/index.yaml` (+ create the playbook) |

**Rules:** always append (never delete — history is valuable); add `discovered`
dates to access issues; when adding a category also create its playbook and set
`playbook_ref`.
