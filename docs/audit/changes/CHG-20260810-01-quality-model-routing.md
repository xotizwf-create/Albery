# CHG-20260810-01: Quality model routing

- Status: verified
- Date opened: 2026-08-10
- Related decision: [ADR-0002](../decisions/ADR-0002-codex-reasoning-groq-media.md)
- Bitrix engineering task: 2666; result comment 42730

## Goal

Move task-offer composition, task check-in classification, and Novinki analysis from Groq to a higher-quality Codex contour while leaving audio and screenshot/OCR processing on Groq.

## Before

- `task_offers.py` called Groq first and used a Hermes/Codex fallback with broad `web` tools and the prompt in process arguments.
- `task_checkin.py` classified user answers with Groq and then retried through Hermes.
- `novinki_watch.py` summarized recommendations through Groq.
- There was no canonical versioned change/decision base visible to all repository-aware agents.

## After (locally implemented target)

- `quality_llm.py` is the single JSON quality boundary.
- `scripts/hermes_quality_oneshot.py` calls the installed Hermes/Codex runtime with an unknown sentinel toolset that resolves to zero tools; `--self-check` verifies zero definitions.
- Prompts travel over stdin; logs contain purpose, status, duration, and result keys, not prompt contents.
- Shared global run slots, timeout, retry, and explicit failure behavior protect the 2 GB server.
- `QUALITY_LLM_ENABLED=0` is an immediate kill switch that requires no code rollback.
- Offers respect an explicit “no useful help” result and use a deterministic fallback only on failure; check-in fails closed; Novinki uses lossless hierarchical batching and retains files on failure.
- Versioned audit instructions, records, and skills make the change discoverable to coding and runtime agents.

## Changed files

- `quality_llm.py`
- `scripts/hermes_quality_oneshot.py`
- `task_offers.py`
- `task_checkin.py`
- `novinki_watch.py`
- focused unit tests under `tests/unit/`
- `.agents/skills/albery-audit/`
- `agent_knowledge/instructions/Albery Architecture Audit.md`
- `agent_knowledge/skills/albery-audit/SKILL.md`
- `agent_knowledge/agents/{main,agent-razrabotchik}.yaml`
- `docs/audit/`
- root `AGENTS.md` and `CLAUDE.md`

## Safety and privacy

- The quality subprocess has no tools, starts in `/tmp`, and receives untrusted text through stdin.
- No prompt or source document content is logged by the wrapper.
- No Groq generative fallback can silently change the decision policy.
- No database schema or secret change is required.

## Verification evidence

Completed locally:

- Focused unit suite: `18 passed` (`test_quality_llm`, `test_task_offers`, `test_task_checkin`, `test_novinki_quality`).
- Full predeploy suite: `1863 passed, 43 skipped`; production-style imports completed. The local import check logged the expected missing-`DATABASE_URL` background warning but exited successfully.
- Changed Python modules pass `pyflakes`; both audit skills and all edited YAML manifests validate.

Production evidence:

- GitHub tests workflow: green; final commit security/tests workflows also green.
- Backup: `/var/backups/albery/code/pre-quality-routing-20260810_135802.tar.gz`; SHA-256 `384b2cf76d6a219c2a3c6b90fcb662ea4643ad7ec29016eddc9bf7cc1085e293`.
- Production fast-forwarded to `0f20709`; changed modules compiled.
- Hermes runner self-check: `tool_count=0`.
- Synthetic quality call returned the requested Codex JSON object.
- Task check-in dry-run: scanned 86, passed deterministic filters 8, picked 1, writes 0.
- Synthetic legal task offer: valid agent selection and 134-character message; no Bitrix post.
- Synthetic Novinki analysis completed with valid empty output and no file or external writes.
- Safe bot-role restart occurred with `inflight=0` and `running_automations=0`.
- `deploy_smoke.py`: `SMOKE OK`; all three health endpoints reported `database=ok`; five services active; fresh error journal empty.
- Closed Bitrix task 2666 contains the implementation, verification, backup, commit, and rollback evidence.

## Risks

- Codex calls can be slower and consume more capacity than Groq.
- A large Novinki set requires multiple serial calls.
- Hermes runtime changes could break the internal one-shot import; deploy self-check detects tool wiring but not every upstream incompatibility.

## Rollback

Set `QUALITY_LLM_ENABLED=0` for immediate containment, then revert the implementation commit and redeploy if the old routing must be restored. The offer/check-in feature flags remain available. Novinki source files are retained on analysis failure, so a failed rollout does not discard input.

## Known gaps

The one-shot runner uses a private Hermes helper and must retain its deploy self-check when Hermes is upgraded. Novinki may legitimately return an empty recommendation set for evidence that does not meet its strict threshold.
