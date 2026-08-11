# CHG-20260811-05: VPN routing and automation recovery

- Status: implemented_production
- Date opened: 2026-08-11
- Incident window: confirmed at 2026-08-11 09:00 MSK; exact start is still under investigation
- Related decisions: [ADR-0002](../decisions/ADR-0002-codex-reasoning-groq-media.md), [ADR-0003](../decisions/ADR-0003-private-per-agent-mcp.md)
- Approval: the owner explicitly requested an urgent repair after Zoom reports, agent automations,
  and morning messages failed

## Impact

- The 09:00 employee automation ran twice but Hermes produced no final response.
- The tool-free Codex quality contour failed with the same model-provider error.
- Zoom webhook events were accepted, but the report watchdog repeatedly failed to analyze pending
  calls and retried them.
- Hermes Telegram delivery could not establish a reliable API connection.

No evidence currently points to the private per-agent MCP transport itself: loopback connectors are
present and the failure happens before a model can call any tool.

## Root cause

The `awg0` AmneziaWG interface was active and had a fresh handshake, but policy-routing rules
`900`, `901`, `902`, `1000`, and `1001` were absent. Table `200` still contained its VPN default,
but no rule selected that table. Outbound traffic therefore used `eth0` and IP `186.246.7.32`
instead of VPN exit `95.85.243.43`.

Observed consequences:

- Codex/OpenAI returned HTTP 403 and identified `186.246.7.32` as the client address.
- `/usr/local/sbin/vpn-healthcheck.sh` returned `RESULT: PROBLEM` with outbound IP
  `186.246.7.32` and OpenAI HTTP 403.
- The current watchdog only repairs a missing interface or a stale handshake combined with a
  failed generic Internet request. A fresh handshake with missing policy routes is a false healthy
  state, so it never invoked `/root/vpn_apply.sh`.

## Approved repair

1. Back up the live VPN apply/rollback/watchdog/config files and current routing state.
2. Reapply the existing idempotent policy-routing script without restarting application services.
3. Verify the VPN exit, OpenAI/Codex probe, Telegram connectivity, website/SSH return paths, and
   private MCP loopback access.
4. Harden the watchdog to validate the required rules, table-200 default, and effective outbound
   route. If routing drift is detected, reapply it; restart the tunnel only if repair fails.
   Extend deploy smoke so an active-but-ineffective VPN and an active gateway with a disconnected
   Telegram platform can no longer pass acceptance.
5. Run local regression, deploy the versioned watchdog, then exercise dry-run/read-only automation
   and Zoom recovery checks.
6. Do not replay employee messages until the exact recipients and payload class are known and the
   owner explicitly confirms the irreversible resend.

## Safety

- Existing inbound SSH/HTTP/HTTPS connections retain the main-table return path through connmark
  and explicit source-port rules.
- No application or database restart is required to restore routing.
- No prompt, transcript, credential, or employee message is written to the audit record.
- Missed work remains queued: the failed employee automation records an error, while pending Zoom
  calls remain without an analytical note and are retried.

## Verification evidence

- Backed up the VPN configuration, apply/rollback scripts, watchdogs, healthcheck, ip rules,
  routes, and IPv4/IPv6 firewall state under
  `/var/backups/albery/vpn/pre-routing-recovery-20260811_101932` before mutation.
- Reapplying `/root/vpn_apply.sh` restored rules `900`, `901`, `902`, `1000`, and `1001` without
  restarting an application or breaking the active SSH connection.
- Effective route to `1.1.1.1` changed to `dev awg0 table 200`; outbound IP changed to
  `95.85.243.43`; `/usr/local/sbin/vpn-healthcheck.sh` returned `RESULT: OK` with OpenAI HTTP 401.
- The isolated zero-tool Codex probe returned `{"ok":true}`. An end-to-end Hermes probe using
  `agent-main,web` called only the read-only `health` tool and returned `AUTOMATION-PROBE-OK`.
- Main private MCP `tools/list` returned 116 tools and a header-authenticated loopback `health`
  call returned application/database status `ok`.
- The next Zoom worker drained the retry queue: three calls existed in the two-day window and the
  count of calls with transcripts but no analytical note fell to zero.
- The hardened watchdog was installed mode `0700`; a healthy run preserved the policy rules and
  the VPN healthcheck remained green.
- Shell syntax passed for both watchdogs. Pyflakes passed. Focused tests passed (`8 passed`), then
  the full local suite passed (`1888 passed, 43 skipped`); skips are the documented local
  PostgreSQL/LibreOffice cases.

Production cannot yet be marked fully verified for every delivery channel: the Hermes Telegram
platform is independently in `retrying`, and a direct `getMe` returns 401 for its configured bot
token. The credential fingerprint is unchanged across backups dating to June, so this was not
introduced by the private-MCP migration. Recovery requires a newly issued Telegram bot token;
deploy smoke now fails closed on this state instead of accepting an active process as healthy.

During diagnosis, an additional source-of-truth drift was found: the production Zoom watchdog has
the newer detached-worker/retry-safe implementation, while the repository copy is older. The live
file must not be overwritten by that stale copy; source parity will be restored and regression
tested as part of this change.

The failed 09:00 automation (`Отчёт по новым просроченным задачам`) remains recorded as `error` and
was not silently marked successful. Its irreversible employee delivery has not been replayed; the
owner must confirm that resend after reviewing the affected automation.

## Rollback

Restore the backed-up watchdog and run `/root/vpn_rollback.sh` only if the repaired routing breaks
public service return paths. The application code and private MCP migration do not need rollback
for this incident.
