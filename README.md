# Agent Sandbox — Permissioned Tool-Using Agent

A controlled agent environment where an LLM can use tools (calculator, sandboxed
file reader, CSV query, mock search, mock ticket creation) — but every tool call
is gated by **role-based permissions, risk tiers, rate limits, and (for sensitive
actions) human approval**, with a full **audit trail** and a **trace viewer**.

Narrative: *"A permissioned, auditable agent runtime — the model proposes, the
system disposes."*

## Repo layout

```
agent-sandbox/
├── contracts/            # Shared Pydantic schemas — BOTH tracks import these, never fork them
│   ├── enums.py           # Role, RiskLevel, WorkflowStatus, ApprovalDecision, PermissionDecision
│   ├── tool_registry.py   # ToolDefinition, GET /tools response shape, starter tool seed data
│   ├── plan.py            # ToolCall / Plan — what the LLM planner must emit
│   ├── workflow_state.py  # WorkflowState — the LangGraph state shape
│   └── audit_event.py     # AuditEvent — one row per tool-call decision
├── track-a-guardrail/    # Person A: registry, RBAC, approvals, rate limiting, audit, tracing
├── track-b-agent/        # Person B: LangGraph state machine, tools, planner, UI
├── docs/
│   └── PHASE0_CONTRACT.md
├── tests/
│   └── test_contracts.py
├── HANDOFF_TEMPLATE.md
└── docker-compose.yml     # filled in progressively from Phase 5
```

## Tracks

- **Person A — "The Guardrail":** tool registry, RBAC/roles, risk-tier policy,
  rate limiting, approval-queue backend, audit log, observability.
- **Person B — "The Agent":** LangGraph state machine, planning/tool-selection
  logic, tool implementations, user-facing + approval UI.

Full phase-by-phase plan: see [`docs/PHASE0_CONTRACT.md`](docs/PHASE0_CONTRACT.md)
and the phase breakdown in this README's project board / issues.

## Setup

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python -m pytest tests/ -q   # confirms contracts import & serialize cleanly
```

## Swapping tracks

You can swap who owns Track A vs Track B at any **phase boundary** (never
mid-phase), and only both at once. Whoever leaves a track writes a handoff
note from `HANDOFF_TEMPLATE.md`, committed as `HANDOFF_<track>_<phase>.md`,
before the swap.
