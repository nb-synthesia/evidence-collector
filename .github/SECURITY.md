# Security Policy

## Reporting a vulnerability

Please report security issues **privately** — do not open a public issue.

Use GitHub's private vulnerability reporting:
**[Report a vulnerability](https://github.com/nb-synthesia/evidence-collector/security/advisories/new)**

This especially includes anything that could:

- expose secrets or credentials (e.g. leaking `~/.vanta/credentials.json`),
- leak captured evidence, screenshots, or customer/PII data,
- bypass the provenance/checksum trail, or
- cause evidence to be **submitted** to Vanta without the human review gate
  (the tool must only ever upload drafts).

Please include reproduction steps and impact. We'll acknowledge as soon as we
can and coordinate a fix and disclosure timeline with you.

## Handling sensitive data

This tool captures screenshots of internal systems. When reporting any issue
(security or otherwise), **redact** real hostnames, account IDs, tokens, and any
personal data before sharing logs or images.
