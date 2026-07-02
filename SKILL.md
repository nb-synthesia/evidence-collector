---
name: collect-evidence
description: >
  Collect, screenshot, and upload compliance evidence for Vanta audits.
  Invoke with /collect-evidence [filter] where filter is a Vanta test ID,
  control ID (e.g. CC8.1), framework name (soc2, hitrust, iso27001), or
  empty to process all pending "Needs document" items.
  For each pending item: pulls the auditor ask, researches how your
  organization handles it (via the configured playbook base document),
  navigates source systems in the user's Chrome session via Playwright CDP,
  captures screenshots, generates an explainer PDF, and uploads to Vanta
  (draft — human reviews before submitting).
  Trigger on: /collect-evidence, "collect evidence", "get screenshots for
  Vanta", "process evidence backlog", "screenshot for control",
  "build evidence for", "upload to Vanta", "gather evidence for vanta".
---

# /collect-evidence

Read `CLAUDE.md` for the full pipeline instructions.

Organization settings live in `config.yaml` (see `config.example.yaml`).

Before starting evidence capture, load the knowledge base:
- `knowledge/infrastructure.yaml` — system URLs, accounts, service map
- `knowledge/sso-tiles.yaml` — SSO/IdP tile URLs for each service
- `knowledge/access-issues.yaml` — known blockers and workarounds
- `knowledge/playbooks/` — per-category evidence strategies (the base document)
- `knowledge/rubrics/` — per-framework QA rubrics for the evidence quality
  gate (deterministic checks + skeptical evaluator persona)
