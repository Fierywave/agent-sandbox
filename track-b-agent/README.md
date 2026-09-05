# Track B — The Agent

Owner: Person B

Responsibilities (in build order): LangGraph state machine, LLM planning node,
tool implementations (calculator, file reader, CSV query, mock search, mock
ticket creation), permission-check node, "waiting for approval" state,
clarification/retry logic, user-facing + trace-viewer UI.

This folder is empty as of Phase 0 — it fills in starting Phase 1. See the
root `README.md` and `docs/PHASE0_CONTRACT.md` for the contracts this service
must honor. Until Track A's registry is running, build against a stub
matching `contracts.tool_registry.STARTER_TOOLS`.

Planned layout (Phase 1 onward):
```
track-b-agent/
├── graph.py               # LangGraph node wiring: intake -> plan -> tool
│                           # selection -> permission check -> execution ->
│                           # reflection -> approval wait -> final response
├── planner.py              # LLM planning node -> validated contracts.Plan
├── tools/
│   ├── calculator.py
│   ├── file_reader.py
│   ├── csv_query.py         # Phase 2
│   └── create_ticket.py     # Phase 2
├── registry_client.py       # calls Track A's GET /tools (or a local stub)
├── permission_client.py     # Phase 2: calls Track A's permission/approval endpoints
├── ui/
│   └── app.py               # Streamlit: task submission + trace viewer
└── requirements-b.txt
```
