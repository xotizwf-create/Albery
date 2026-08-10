# Audit map

| Path | Purpose |
| --- | --- |
| `docs/audit/INDEX.md` | Entry point and register of active records |
| `docs/audit/architecture/CURRENT.md` | Verified current architecture and explicit pending target state |
| `docs/audit/decisions/ADR-*.md` | Durable architectural decisions and consequences |
| `docs/audit/changes/CHG-*.md` | One end-to-end implementation and deployment history |
| `docs/audit/CHANGELOG.md` | Append-only chronological index |
| `docs/audit/templates/` | Required record structures |

Statuses move forward as `proposed -> approved -> implemented_local -> deployed -> verified`.
Terminal alternatives are `rolled_back`, `superseded`, and `cancelled`.

Do not label target behavior as current until production evidence exists. If code is ready but not deployed, show both states explicitly.
