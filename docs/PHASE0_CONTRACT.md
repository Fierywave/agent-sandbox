# Phase 0 — Shared Contract

Status: **committed as code in `contracts/`, this doc is the human-readable companion.**

Everything below is implemented as Pydantic models in `contracts/`. This file
explains the *why* behind each shape so neither of you has to reverse-engineer
intent from the code later.

## 1. Roles (`contracts/enums.py::Role`)

`viewer`, `analyst`, `operator`, `admin` — max requestable risk tier per role
is in `ROLE_MAX_RISK`. This is a starting point; if you decide a role needs a
different ceiling, change it here and both of you get the update for free.

## 2. Risk tiers (`contracts/enums.py::RiskLevel`)

`low` / `medium` / `high`. Enforcement decision at each tier:

| Tier | Behavior |
|---|---|
| low | auto-execute |
| medium | auto-execute, but rate-limited harder + always audited |
| high | pauses for human approval (`operator`/`admin` only) before executing |

## 3. Starter tool set (`contracts/tool_registry.py::STARTER_TOOLS`)

| Tool | Risk | Allowed roles | Approval required |
|---|---|---|---|
| calculator | low | all | no |
| file_reader | low | all | no |
| mock_search | low | all | no |
| csv_query | medium | analyst, operator, admin | no |
| create_ticket | high | operator, admin | yes |

This is a **reference seed list** for Track B to build a stub against in
Phase 1. Track A's live registry service is the actual source of truth once
it's running — if the live version diverges from this seed (e.g. you decide
`csv_query` should require approval too), update this file so it stays in
sync, and flag it to your teammate.

## 4. `ToolCall` / `Plan` (`contracts/plan.py`)

The LLM planner must emit a `Plan` (list of `ToolCall`s). Every `ToolCall`
carries `risk_level` and `requires_approval` **copied at plan time** from the
registry — this isn't the model's opinion, it's a snapshot of the registry's
answer, and Track A's permission check re-verifies it independently rather
than trusting the copy.

## 5. `WorkflowState` (`contracts/workflow_state.py`)

The LangGraph run's state. `status` is the single source of truth for where a
task is: `running → waiting_approval → completed | rejected | failed`.
`pending_tool_call` is set only while `status == waiting_approval`.

## 6. `AuditEvent` (`contracts/audit_event.py`)

One row per tool-call decision — allowed, denied, rate-limited, pending,
approved, rejected, executed, or failed. This is what answers "why did the
agent do that?" during a demo or a debugging session, so every field stays;
don't trim it to save space.

## Definition of done ✅

- [x] `contracts/` folder with all six shapes as Pydantic models
- [x] Imports cleanly (`from contracts import ...`)
- [x] Serializes to JSON without errors (see `tests/test_contracts.py`)
- [ ] Both of you have pulled this commit and can `import contracts` from your
      own track's code before starting Phase 1
