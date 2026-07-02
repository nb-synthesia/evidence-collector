# Playbook: Change Management — EXAMPLE

> This is an ILLUSTRATIVE example of a local playbook. When `playbook.backend`
> is `local` in `config.yaml`, the agent reads files like this one (referenced
> by `playbook_ref` in `index.yaml`). Replace the content with how YOUR org
> actually handles the control, then commit to your fork. If you use Notion /
> Confluence / Google Docs instead, delete this and point `playbook_ref` at the
> corresponding page.

## Controls covered

- Evidence of Edit Checks
- Tickets for Sampled Changes
- List of System Changes

## Evidence strategy

1. Show that changes flow through pull requests with required review before merge.
2. Show the branch/ruleset protection that enforces the review requirement.
3. Show CI checks (tests, security scans) gating the merge.
4. For "sampled changes", pull a small set of merged PRs across the audit
   period with their approvals and linked tickets.

## Where to capture (fill in your real URLs)

| # | System | Page | What it proves |
|---|--------|------|----------------|
| 1 | GitHub | Org rulesets (`/rules`) | Review is enforced org-wide |
| 2 | GitHub | A merged PR with approvals + passing checks | The control operates |
| 3 | GitHub | PRs list filtered to the audit period | Population for sampling |

## Narrative framing

Explain that no change reaches production without peer review and passing
automated checks, and that each change is traceable to a ticket/PR. Cite the
specific ruleset and the sampled PRs by number.

## Gotchas / lessons learned

- Branch-protection settings pages can 404 for non-admins; use org rulesets.
- Capture merged (not open) PRs so approvals and check results are final.

## Run history

- (append dated notes here as you learn what works)
