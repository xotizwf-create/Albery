# CHG-20260811-07: Durable and conflict-safe agent automations

- Status: verified
- Date opened: 2026-08-11
- Related decisions: [ADR-0004](../decisions/ADR-0004-durable-conflict-safe-agent-automations.md)
- Bitrix engineering task: pending

## Goal

Put agent automations under the same hard resource boundary as live requests and make scheduling,
retry and mutating actions safe against restarts, duplicate triggers and concurrent work.

## Before

- Live Bitrix/Telegram Hermes turns share two server-wide PostgreSQL advisory slots.
- Agent automations use a separate in-memory queue and one worker, so the host can reach three heavy
  Hermes processes.
- A restart loses queued/delayed work; `threading.Timer` owns delayed retry.
- `Run now` checks `last_status` and updates it in separate operations.
- A delivery failure repeats the full Hermes turn and can repeat external actions.
- Automation runs and MCP writes have no per-run idempotency ledger or shared business-object lock.

## Target / after

- One two-slot server-wide heavy-process limit covers live and automation Hermes processes.
- PostgreSQL owns automation runs, due times, leases, stage results, delivery attempts and retry.
- Manual and scheduled triggers are transactionally deduplicated.
- Brain and delivery are separate durable stages; successful work is never recomputed merely to
  retry delivery.
- Mutating automation tool calls are recorded and deduplicated per run; ambiguous calls fail closed.
- Conflicting writes to the same identifiable business object are serialized for both live and
  automation MCP traffic.
- The architecture record and overview diagram show the shared limit and durable stage machine.

## Changed boundaries and files

- `database/migrations/083_durable_agent_automation_runs.sql` and `scripts/ensure_postgres.py`:
  durable run, delivery and tool-effect ledgers plus due/lease/active-manual indexes.
- `agent_automations.py`: atomic enqueue, one PostgreSQL polling worker, stage claims/leases,
  shared-slot brain execution, safe recovery, stored-result delivery and fail-closed ambiguous states.
- `shared/automation_safety.py` and `mcp/context_server.py`: conservative mutation classifier,
  canonical action fingerprint, per-run effect ledger and cross-process business-object locks.
- `agent_center.py`, `scripts/migrate_private_mcp.py`,
  `scripts/materialize_automation_connectors.py` and `scripts/deploy_smoke.py`: local automation
  connector aliases, active-run binding and deployment validation without public endpoints.
- `b24bot.py`: optional single-attempt notification mode so the durable delivery stage, rather
  than an inner helper loop, owns retry and ambiguity decisions.
- `Интерфейс/src/agent/views/AutomationsPanel.tsx`: honest `queued` and `review` states and a
  disabled Run-now button while a run is active.
- automation unit/DB tests, ADR/audit/current architecture, the workspace SVG/PNG overview, and
  the consolidated human master report `Hermes Brain/tmp/albery-architecture-actual-2026-08-10.md`.
- `tests/unit/test_tg_closed_chats.py`: test-only clock freezing; its fixed August fixtures had
  crossed the real seven-day cutoff and would make otherwise green CI depend on wall-clock time.

`kind='system'` mirror rows and the external legacy Hermes cron runtime are explicitly outside this
implementation and remain a known follow-up.

## Safety and privacy

- No credentials, prompts, message bodies or recipient identifiers are written to audit records.
- PostgreSQL stores only execution data already required to perform and diagnose an automation.
- The automation connector alias remains loopback-only and uses the existing private bearer model.
- Unknown tools are classified as mutating. Ambiguous external outcomes stop for review rather than
  being replayed.
- No live message, task, deal or other irreversible action is part of verification without explicit
  owner confirmation and a disposable test object.

## Verification plan and evidence

Planned tests:

- migration is repeatable and creates all constraints/indexes;
- live and automation work contend for the same global slots;
- concurrent manual triggers create one active run;
- the same scheduled minute creates one run;
- queued, leased, brain-complete and delivery-retry states recover after restart;
- delivery failure retries stored output without another Hermes call;
- repeated mutating tool fingerprints return the recorded result, while ambiguous effects stop;
- same-object concurrent writes serialize and different-object writes may proceed;
- normal per-agent and automation-alias connectors remain private and correctly scoped;
- existing automation delivery/recovery, Bitrix and Telegram regression suites remain green.

Implemented local evidence on 2026-08-11:

- Backup before code changes:
  `tmp/backups/pre-chg-20260811-07-20260811_automation-safety/files.zip`, recorded git HEAD in the
  adjacent `git-head.txt`; `b24bot.py` and `AutomationsPanel.tsx` were backed up before their later
  inclusion. The workspace SVG has a separate pre-change copy under
  `Hermes Brain/tmp/backups/pre-chg-20260811-07/`.
- `python -m py_compile` passed for every changed Python runtime/deployment file.
- Pyflakes reported no new finding in changed files. Three existing findings remain in
  `b24bot.py`; the repository-wide `mcp/context_server.py` also has its pre-existing unused local.
- Focused automation/private-MCP/run-slot/destructive-gate regression: 39 passed, including
  shared-slot/connector and no-Hermes delivery-stage assertions.
- Final full local suite: 1,897 passed, 44 skipped, zero failures. The skipped PostgreSQL tests run
  in CI; LibreOffice-only document conversion remains server-only. The unrelated Telegram fixture
  clock was frozen to its documented 05.08.2026 scenario so CI no longer depends on wall time.
- PostgreSQL-specific atomic trigger test is present but skipped locally because `DATABASE_URL` is
  absent; CI must run it against migration 083.
- Frontend TypeScript lint and production Vite build passed. The existing large-chunk warning is
  unchanged.
- Dependency security gates passed: `pip-audit -r requirements.txt` found no known vulnerabilities;
  `npm audit --omit=dev` found zero vulnerabilities.
- SVG XML remained valid; Edge rendered the 1800 x 1540 final PNG without clipping. The diagram
  now shows one two-process global limit, PostgreSQL stages, delivery-only retry, action
  idempotency, business locks and manual review for ambiguous outcomes.
- The workspace architecture Markdown was synchronized on 2026-08-11 and made the single human
  entry point. It explicitly separates verified production from the locally implemented target
  and links back to this CHG, ADR-0004 and `architecture/CURRENT.md`; technical evidence remains
  canonical in the versioned audit base.

- Implementation commit `971f0b808a57595b3563fb7ff2be396a5b716c58` was pushed to
  `origin/main`.
- GitHub tests run `31478731789` passed: frontend lint/build plus backend on Python 3.10/PostgreSQL
  14 and Python 3.12/PostgreSQL 16. Both database jobs applied the full schema/migration chain
  twice before running all DB-marked tests, including the atomic-trigger test.
- GitHub security run `31478731850` passed both dependency audits.
- At that checkpoint production migration, connector materialization, restart and live smoke were
  not yet evidence because the stale direct credential was rejected. The later authorized rollout
  used the documented server-217 credential vault; see the production evidence below.

Production deployment evidence on 2026-08-11:

- Production fast-forwarded cleanly to `216dccb` only after green CI. The complete CHG-09 backup
  set at `/var/backups/albery/pre-chg09-20260811_154147` also covers this rollout's code, database,
  service, environment, Hermes and frontend state.
- Migration `083` created the durable run, delivery and effect ledgers on PostgreSQL 14. The
  idempotent schema runner completed successfully, and deploy verification found every required
  table.
- `scripts/materialize_automation_connectors.py` first passed dry-run for all active profiles and
  then applied exact loopback/header aliases. The previous Hermes configuration is retained at
  `/root/.hermes/config.yaml.bak-automation-connectors-20260811_154705`.
- Controlled restart happened only after zero Bitrix in-flight turns, zero legacy running agent
  automations and zero durable running stages. `albery.service`, `albery-tg.service` and
  `hermes-gateway.service` are active; application and employee-Telegram warning/error journals
  after cutover are empty.
- Production smoke passed all 53 workflow references, all nine active exact private connector
  matrices, closed/public-route negative probes, durable automation tables, workspace/Bitrix UI
  and API routes, VPN and both Albery Telegram transports. Durable queues were empty after cutover,
  which is expected because acceptance deliberately created no live automation or external write.
- This change is `deployed`, not `verified`: atomicity, contention, lease recovery, delivery-only
  retry and action deduplication are covered by unit/CI PostgreSQL tests and production structure,
  but a controlled production automation that performs and delivers a reversible business action
  still needs an explicitly approved test object and recipient.

## Risks

- Incorrect leasing can strand a run or allow two workers to claim it.
- Misclassified read tools can reduce throughput; misclassified write tools can duplicate effects.
- An external API can succeed while the process dies before Albery records success.
- Connector materialization drift can make only scheduled turns fail while live turns still work.
- A migration/restart during active work can interrupt employees or a running automation.

## Rollback

Before implementation, create a local archive of every touched tracked/untracked file and record
the current git commit. Before production migration, take a `pg_dump` of the affected automation
tables and configuration backups. Runtime changes will have an environment kill switch that keeps
the old UI readable while stopping new automation claims. Code rollback is git revert/fast-forward
to the recorded commit; migration rollback preserves new run tables for forensics rather than
dropping execution history.

Concrete runtime rollback after deployment: stop new claims with `AGENT_AUTOMATIONS=0`, wait for
`brain_running`/`delivery_running` leases to clear, revert the code commit, restore the previous
Hermes config backup and restart only through the safe-restart gate. Keep migration-083 tables for
forensics; they are additive and old code does not read them.

## Known gaps and follow-up

- Legacy system cron automations still need a separate inventory and migration plan.
- External systems without idempotency keys retain an unavoidable ambiguous-outcome review state.
- The business-object lock serializes individual mutating calls, not an entire multi-tool model
  conversation. External APIs with version/ETag support can later add optimistic concurrency.
- Result/effect history retention requires a separate policy; no automatic deletion was introduced.
- Production rollout is complete. Remaining acceptance is a controlled reversible live automation,
  not another schema or runtime deployment.

## Amendment 2026-08-13: production acceptance completed by CHG-12

CHG-12 executed the missing controlled production scenario through the real
`automation-agent-main` private MCP boundary. One local-only `export_document` effect was recorded
exactly once in the durable ledger and the disposable run/file rows were removed. The heavy Zoom
cron entered the same PostgreSQL slots through its reviewed checksum-pinned wrapper and completed a
natural run. Automations 36 and 59 also completed/delivered naturally without historical replay.

The known external ambiguous-outcome/manual-review behavior, per-call lock granularity and retention
policy are explicit design constraints/follow-up work, not evidence that the durable runtime failed
acceptance. Current 2026-08-13 deploy smoke again verifies the wrapper, private connectors, service
health and shared infrastructure. This change is therefore `verified`.
