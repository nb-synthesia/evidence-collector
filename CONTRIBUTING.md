# Contributing

Thanks for your interest in improving the Compliance Evidence Collector! This
is a community-built, open-source tool (GPL-3.0) for automating compliance
evidence collection for [Vanta](https://www.vanta.com/) audits. It is **not
affiliated with Vanta Inc.**

## Two kinds of "contributions" — know which one you mean

This project deliberately separates the **tool** from your **organization's
knowledge**:

- **Your org's knowledge lives in *your* fork, not here.** Your
  `config.yaml`, `knowledge/infrastructure.yaml`, `sso-tiles.yaml`,
  `access-issues.yaml`, `playbooks/`, etc. describe *your* environment. Keep
  them in your private/company fork. **Do not open PRs adding your company's
  systems, URLs, IdP tiles, or playbooks to this upstream repo.**
- **Upstream contributions** to *this* repo are improvements to the tool
  itself: the pipeline (`CLAUDE.md`), the scripts, the install flow, the
  rubrics' general structure, docs, and the **template** knowledge files (which
  must stay generic).

If you're unsure, open an issue first.

## Ground rules

1. **No organization-specific data upstream.** Templates use fake placeholders
   (`example.com`, `your-org`, `<Your IdP>`). Never commit real hostnames,
   account IDs, IdP tile URLs, Notion page IDs, employee names, or internal
   process detail.
2. **No secrets, ever.** Credentials live only in `~/.vanta/credentials.json`.
   Never commit tokens, API keys, `credentials.json`, or `.token_cache.json`.
3. **No captured evidence.** Screenshots/PDFs and evidence directories are
   gitignored — keep it that way. They may contain sensitive data.
4. **Keep the human gate.** The tool uploads to Vanta as **drafts only** and
   never auto-submits. Don't add anything that submits on the user's behalf.
5. **Keep it org-agnostic and cross-platform.** No hardcoded company URLs; read
   from `config.yaml`. Prefer POSIX-friendly shell and cross-platform paths.

## Development setup

```bash
git clone https://github.com/<you>/evidence-collector.git
cd evidence-collector
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
playwright install chromium
```

Quick checks before you push:

```bash
# Python scripts parse
python3 -m py_compile scripts/*.py
# Shell script syntax
bash -n install.sh
# Config loader sanity
python3 scripts/config.py
```

## Coding conventions

- **Python**: standard library first; the only runtime deps are `playwright`,
  `requests`, `reportlab`, `pyyaml` (see `requirements.txt`). Keep scripts
  runnable standalone with `--help`. Scripts print JSON to stdout so the agent
  can parse results.
- **Comments** explain *why*, not *what*. Don't narrate obvious code.
- **Docs**: use "for Vanta" descriptively; never brand the project as a Vanta
  product. Update `CLAUDE.md` if you change the pipeline behavior.

## Submitting a change

1. Fork and branch: `git checkout -b fix/short-description`.
2. Make the change; run the quick checks above.
3. Write a clear commit message explaining the why.
4. Open a PR against `main` and fill in the PR template.

By contributing, you agree your contributions are licensed under the project's
**GPL-3.0** license.

## Reporting bugs / requesting features

Use the issue templates. For anything that could expose sensitive data (a leak
in captured evidence, a provenance bypass, etc.), please treat it as a security
report — see [SECURITY.md](.github/SECURITY.md) if present, or contact the
maintainers privately rather than filing a public issue.

## Trademarks

"Vanta" is a trademark of Vanta Inc., referenced here only to describe
compatibility. See the Trademarks section in the [README](README.md).
