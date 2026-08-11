# CHG-20260811-06: Bitrix agent and automation diagram

- Status: implemented_local
- Date opened: 2026-08-11
- Related decisions: [ADR-0003](../decisions/ADR-0003-private-per-agent-mcp.md)
- Bitrix engineering task: not created; this change documents existing production behavior

## Goal

Make the Bitrix24 request diagram explicitly show that Albery routes events to different agent
profiles and that every automation belongs to a specific profile through `agent_slug`.

## Before

The overview showed one linear employee-message path from webhook to Bitrix response. It mentioned
ten profiles elsewhere, but did not visualize profile selection or the independent automation lane.
The Hermes card also described the two-slot live-turn limit without making clear that scheduled
agent automations use a separate queue and worker pool.

## Target / after

- Keep the ordinary employee-message path intact.
- Show `bot_id -> agent_slug` routing into the matching Bitrix-enabled profile, while making clear
  that the ten active `agents` rows also include internal and Telegram profiles.
- Show that identity, rules, knowledge, conversation history and private MCP are scoped to the
  selected agent.
- Show agent-owned automations as a distinct Bitrix path: schedule/manual trigger -> automation
  queue -> owning `agent_slug` -> Hermes with that profile -> the same private MCP -> Bitrix
  message or action.
- State that live Bitrix/Telegram turns use two shared slots while scheduled agent automations use
  an independent lane with one configured worker in the verified production snapshot.

## Changed boundaries and files

Documentation and architecture diagrams only. No runtime, database, prompt, credential, network or
production configuration changes are authorized by this record.

## Safety and privacy

The diagram contains only architecture metadata. It does not contain employee messages, recipient
IDs, automation payloads, tokens or credentials.

## Verification plan and evidence

- Cross-check the diagram against `b24bot.py`, `agent_automations.py`, the current architecture
  record and the verified private per-agent MCP record.
- Parse the edited SVG as XML and render it to a raster image for visual inspection.
- Confirm that the normal webhook path and the automation path remain visually distinct.

Implemented evidence:

- The workspace artifact `../Hermes Brain/tmp/albery-architecture-overview-2026-08-10.svg` now
  shows the ten-profile registry, Bitrix-specific `bitrix_bot_id` selection and a separate six-step
  Bitrix automation lane; its companion
  `-final.png` was regenerated from the same SVG.
- The misleading global wording on the Hermes box was narrowed to the two live Bitrix/Telegram
  slots; the automation lane shows its separate one-worker queue.
- `docs/audit/architecture/CURRENT.md` now contains the same routing and ownership model in a
  repository-visible Mermaid diagram and human-readable invariants.
- Python XML parsing passed. Microsoft Edge rendered the full 1800 x 1540 SVG without clipping;
  the ordinary request, agent ownership and automation paths remained visually distinct.

## Risks

- A diagram can incorrectly imply that scheduled automations enter through the Bitrix webhook.
- The ten active profiles can be misread as ten Bitrix bots even though the registry also includes
  internal and Telegram runtime profiles.
- A global concurrency label can incorrectly imply that automations consume the two live-turn
  slots.
- Listing profile examples can be mistaken for an exhaustive or fixed agent registry.

## Rollback

Revert the documentation and SVG changes from this record. No production rollback is required.

## Known gaps and follow-up

This step maps the automation architecture only. The next audit step must inventory and verify the
actual automation rows, schedules, recipients, tool dependencies, last results and retry behavior.
Its independent in-memory lane description is the verified pre-change snapshot and is superseded
for the locally implemented target by
[CHG-20260811-07](CHG-20260811-07-durable-conflict-safe-agent-automations.md).
