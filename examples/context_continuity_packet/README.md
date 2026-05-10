# Context Continuity Packet (CCP) - Phase 1 Pattern Example

This example demonstrates a **pure app-layer** context continuity pattern for Swarm.

Boundary model:

`continuity outside, orchestration inside`

## Intent

Show how to preserve context between separate `Swarm.run()` calls without changing Swarm runtime internals.

## Included

- A tiny local JSON persistence backend.
- A context continuity metadata envelope (`_ccp_*` fields).
- An optional validator that rejects stale persisted context.
- Two-run flow:
  - Run A updates context and persists it.
  - Run B loads persisted context and continues.

## Excluded

- No edits to `swarm/core.py`, `swarm/types.py`, or runtime loop behavior.
- No distributed synchronization.
- No conversation history persistence framework.
- No core API changes.

## Run

1. Ensure `OPENAI_API_KEY` is configured.
2. Run:

```bash
python examples/context_continuity_packet/main.py
```

The script prints:

- first run response + persisted context
- second run response + merged context
- stale-context rejection path (deterministic validator failure)

## Phase 1 Pattern Note

This is an **optional, additive, educational recipe**.  
It is intentionally small and does not imply a Swarm core roadmap commitment.
