# CHG-20260813-18: VPN healthcheck transient hardening

- Status: implemented_local
- Date opened: 2026-08-13
- Approval: the owner approved execution of the remaining audit/remediation plan and required all
  changes and decisions to be recorded
- Related: [CHG-20260811-05](CHG-20260811-05-vpn-routing-automation-recovery.md),
  [CHG-20260813-17](CHG-20260813-17-mcp-capability-rights-audit.md)

## Trigger

CHG-17 production deployment itself completed, but repeated acceptance smoke alternated between
green and `VPN: effective outbound route or provider reachability is unhealthy`. This required a
root-cause check before CHG-17 could be called verified.

## Evidence and root cause

- The live host retained all required rules `900`, `901`, `902`, `1000`, and `1001`, table `200`
  selected `default dev awg0`, and `ip route get 1.1.1.1` selected `awg0`.
- The interface, recent handshake, timer, watchdog run, public VPN exit `95.85.243.43`, local app,
  repository/live watchdog checksum parity and systemd failed-unit set were healthy.
- Six initial healthchecks passed. In a separate 20-run series, 17 passed and three failed.
- Every failed sample still had the correct VPN exit, recent handshake and local HTTP 302. The only
  failed field was a single OpenAI curl returning HTTP `000`; later samples returned the expected
  unauthenticated HTTP 401 without routing repair or service restart.

The VPN did not drop. The unversioned healthcheck made one external request and treated any
transient connect failure as proof that the whole policy route was unhealthy. It also inspected an
idle handshake before generating probe traffic and accepted any OpenAI code except `000`/`403`,
including provider 5xx. This produced false alarms while not being strict enough about the actual
provider response.

## Implemented target

- Put the production healthcheck under Git as `deploy/vpn-healthcheck.sh`.
- Validate the five required policy rules, table-200 default and effective route directly.
- Retry the public-exit and OpenAI probes at most three times with a two-second delay; neither the
  watchdog nor an application service is restarted by this check.
- Require the exact expected VPN exit and exact unauthenticated OpenAI HTTP 401. Three consecutive
  external failures still fail closed.
- Read handshake freshness after probe traffic so a healthy idle tunnel can refresh on demand.
- Add an atomic installer with a protected backup and exact source/mode drift check.

## Safety and user-visible behavior

The change affects monitoring/acceptance only. It sends no employee message, creates no task,
changes no CRM object and replays no automation. A sustained route/provider outage remains red;
only a single recoverable external connect blip is tolerated. The installer does not restart the
VPN or application services.

## Verification

- Before-state production series: `17/20` green; all three failures were OpenAI HTTP `000` with the
  correct VPN exit and healthy local state.
- Pending after implementation: shell syntax, installer/unit regression, full suite, CI, protected
  production backup/install, repeated live series, deploy smoke/self-check and fresh journals.

## Rollback

Restore `/usr/local/sbin/vpn-healthcheck.sh` from the protected installer backup. No service restart
is required. The pre-CHG-17 source archive remains independently available under
`/var/backups/albery/pre-chg17-*`.
