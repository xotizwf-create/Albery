# CHG-20260811-07: Durable and conflict-safe agent automations

- Status: implemented_local
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
- automation unit/DB tests, ADR/audit/current architecture, and the workspace SVG/PNG overview.
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

Commit, push, CI, migration, connector materialization, production restart and live smoke are not
yet evidence. Until they pass, production behavior remains the pre-change snapshot in
CHG-20260811-06.

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
- Production rollout depends on repository push/CI and authenticated access to server 186.
