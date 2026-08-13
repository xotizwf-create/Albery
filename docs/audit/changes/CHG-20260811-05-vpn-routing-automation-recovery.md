# CHG-20260811-05: VPN routing and automation recovery

- Status: verified
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
- Implementation commit: `d6ff01807818933c0efd56ae59fd69b9033fc0d7`. GitHub Actions tests
  run `31469575216` and security audit run `31469575235` passed. Production fast-forwarded to the
  same commit; Python compilation and both shell syntax checks passed there.
- Production deploy smoke passed all 53 workflow references, ten exact private connector matrices,
  retired/public MCP negative probes, services, VPN, site, calculator, workspace routes/tables and
  workspace Telegram transport. It correctly returned non-zero only for
  `Hermes Telegram platform state=retrying`.

Production cannot yet be marked fully verified for every delivery channel: the Hermes Telegram
platform is independently in `retrying`, and a direct `getMe` returns 401 for its configured bot
token. The credential fingerprint is unchanged across backups dating to June, so this was not
introduced by the private-MCP migration. Recovery requires a newly issued Telegram bot token;
deploy smoke now fails closed on this state instead of accepting an active process as healthy.

Independent control acceptance at 10:50-10:56 MSK:

- Local `HEAD` and `origin/main` matched; the worktree was clean; shell syntax and pyflakes passed;
  the full suite passed again (`1888 passed, 43 skipped`). The two latest tests/security GitHub
  Actions runs for `101db0b` were green.
- Production `HEAD` and `origin/main` matched `101db0b`; tracked files were clean. Twelve older
  untracked backup/runtime artifacts remain on the server and were not touched.
- All seven core services were active, failed systemd units were zero, the five relevant timers
  were active, available RAM was 889 MiB, swap use 109 MiB, and disk use 69%.
- Ports `5002`, `5003`, and `5004` remained loopback-only. Hermes config was `0600`; all ten active
  agents had unique non-empty credentials and exactly ten loopback/header connectors; retired
  connectors were absent. Missing/wrong auth returned 403, path-token and forwarded/public access
  returned 404. All ten exact live tool matrices passed.
- Three consecutive VPN healthchecks passed with the Estonia exit and OpenAI HTTP 401. Required
  policy rules, table-200 effective route, recent handshake, active timer, and repository/live
  watchdog hash parity were confirmed.
- Zero-tool Codex self-check and JSON probe passed. Hermes `agent-main,web` performed exactly one
  read-only `health` call and returned `AUTOMATION-RECHECK-OK`. Post-recovery model logs contained
  zero API failures, HTTP 403s, missing final responses, or permission denials.
- Synthetic Groq media probes passed through the actual application functions: Whisper returned a
  non-empty transcript and vision OCR read both the test marker and number from a generated image.
- All three recent Zoom calls had `done` reports, full required top-level analysis shape and valid
  required fields on all nine operational tasks; pending calls remained zero. The first malformed
  save attempt was rejected by validation and did not persist an incomplete report.
- Bitrix `user.current` read-only authentication returned HTTP 200. WB sync and Google Drive sync
  completed successfully without document errors; self-check and quality services completed with
  exit status zero. Application services had no post-recovery errors; their only warnings were the
  expected rejected negative test and two network errors during the original routing transition.
- Runtime queues were empty: zero employee turns in flight, zero running automations, zero pending
  Zoom reports. Both recorded pre-change backup locations exist with restrictive permissions.

Remaining gates before `verified`:

1. Replace the rejected Hermes Telegram bot token and observe platform state `connected` plus a
   successful non-production-recipient delivery test.
2. Decide whether to replay automation 36 to its one configured Bitrix target; until then its
   09:00 run correctly remains `error` rather than being silently marked successful.
3. Automation 59 (`Ежедневное обновление цен WB`) is active but its previous 14:30 run timed out.
   Its Google Sheet metadata/read and WB-price dependencies pass read-only probes now, but the full
   write scenario was not rerun without owner approval. Observe its next scheduled run or approve a
   controlled write test before claiming the automation fleet is fully healthy.

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

## Amendment 2026-08-13: successor evidence closed the original gates

This record is now `verified`; no failed employee output was replayed to obtain that status.

1. The rejected native Hermes Telegram token belonged to a redundant transport. CHG-13 retired that
   credential explicitly after backup and proved the active channel-neutral employee profile with
   provider identity/access checks. Restoring the duplicate token is no longer the target state.
2. CHG-12 observed automation 36 complete and deliver its normal 2026-08-12 09:00 run and automation
   59 complete/deliver its normal 14:30 run. Historical failed runs remain truthful and unreplayed.
3. CHG-12 put the heavy Zoom job behind the versioned checksum-pinned wrapper and shared slot; its
   first natural post-cutover run succeeded. Current deploy smoke on 2026-08-13 again reports the
   effective VPN route/provider reachability and Zoom wrapper healthy.

The VPN watchdog validates the effective route rather than handshake freshness, all relevant roles
are active, and fresh error journals are empty under CHG-15 acceptance. These successor records close
the recovery contract; employee Telegram user-visible round-trip acceptance remains separately open
under CHG-13 and is not claimed here.
