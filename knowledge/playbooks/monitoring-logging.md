# Playbook: Monitoring & Logging — EXAMPLE

> ILLUSTRATIVE example local playbook. Replace with your real strategy and
> commit to your fork (or point `playbook_ref` at an external page instead).

## Controls covered

- Continuous Monitoring
- Audit Log Settings

## Evidence strategy

1. Show that application/infra logs are collected centrally and retained.
2. Show the monitors/alerts that fire on anomalous conditions.
3. Show that security-relevant events reach the SIEM and are retained for the
   required period.

## Where to capture (fill in your real URLs)

| # | System | Page | What it proves |
|---|--------|------|----------------|
| 1 | Monitoring/APM | Log explorer with retention visible | Logs are collected + retained |
| 2 | Monitoring/APM | Active monitors list | Alerting operates continuously |
| 3 | SIEM | Data sources page | Security events are ingested |

## Narrative framing

Explain the two layers (operational monitoring + security SIEM), the retention
period, and who receives alerts. Tie the retention window to the control's
requirement.

## Gotchas / lessons learned

- Set an explicit time range so retention is visible in the capture.

## Run history

- (append dated notes here as you learn what works)
