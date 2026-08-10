# Audit operating rules

## Scope

Create a change record for any non-trivial code, configuration, prompt, model-routing, integration, database, deployment, security, or architecture change. Create an ADR when the change modifies a durable architectural boundary or invariant.

## Status lifecycle

`proposed -> approved -> implemented_local -> deployed -> verified`

Terminal alternatives: `rolled_back`, `superseded`, `cancelled`.

- `implemented_local`: code and local checks exist; production may still run the previous behavior.
- `deployed`: production received the change; full live verification is not yet complete.
- `verified`: deployment, service health, and the relevant real scenario were checked.

## Evidence

Every change record must contain:

- before and after behavior;
- affected boundaries and files;
- risks and rollback;
- tests and live checks with results;
- commit and deployment evidence;
- backup location when production changes;
- known gaps, assumptions, and follow-up work;
- related ADRs and the closed Bitrix engineering task, when applicable.

Record facts, not conclusions without evidence. Never store secrets, credentials, access tokens, full private conversations, or unnecessary personal data.

## Immutability

Do not silently rewrite verified history. Correct a mistake with an explicit amendment. Replace a decision with `superseded`; undo a change with `rolled_back` and link the successor record.

## One source of truth

The Git repository is canonical. Bitrix tasks are the operational notification and accountability layer, not a second architecture database. PostgreSQL runtime logs are evidence sources, not the decision register.
