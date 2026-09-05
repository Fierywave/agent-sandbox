# Track A — The Guardrail

Owner: Person A

Responsibilities (in build order): tool registry service, OAuth2/JWT auth +
RBAC, role→risk-tier enforcement, approval workflow backend, per-tool/per-role
rate limiting, audit log, tracing/observability.

This folder is empty as of Phase 0 — it fills in starting Phase 1. See the
root `README.md` and `docs/PHASE0_CONTRACT.md` for the contracts this service
must honor, especially the exact shape of `GET /tools` (must match
`contracts.tool_registry.ToolRegistryResponse`).

Planned layout (Phase 1 onward):
```
track-a-guardrail/
├── main.py              # FastAPI app
├── auth.py              # JWT issuing/verification, role claims
├── registry.py           # GET /tools, seeded from contracts.STARTER_TOOLS
├── permissions.py        # role -> risk tier enforcement middleware
├── approvals.py          # Phase 2: pending/approve/reject endpoints
├── rate_limit.py          # Phase 2: Redis sliding-window limiter
├── audit.py               # Phase 2: AuditEvent writes + GET /audit
├── tracing.py              # Phase 3: spans + GET /trace/{task_id}
└── requirements-a.txt
```
