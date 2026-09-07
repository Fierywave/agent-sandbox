# Permissioned Tool-Using Agent Sandbox

> **Core Philosophy:** *The model proposes, the system disposes.*

A production-grade, security-hardened agent runtime environment. While large language models can propose actions across file systems, calculators, data querying engines, and ticketing APIs, every tool invocation is strictly gated by **deterministic role-based access control (RBAC)**, **risk tiers**, **sliding-window rate limits**, and **human-in-the-loop approvals**, backed by an immutable **audit trail** and **observability traces**.

---

## The Problem and Architectural Thesis

Standard agent implementations grant autonomous models direct execution access to tools, relying on prompt instructions alone to enforce safety boundaries. This approach fails in production:
- **Prompt injection** can coerce models into executing privileged functions.
- **Hallucinated or corrupted parameters** lead to destructive commands.
- **Runaway agent loops** incur unexpected latency and API exhaustion.
- **Unverified model intent** bypasses organizational compliance and security policies.

This platform treats all LLM outputs as **untrusted proposals**. Security and authorization decisions are enforced deterministically by an independent guardrail layer before any tool execution occurs.

---

## System Architecture

```
                      +----------------------------------+
                      |         User / Client            |
                      +-----------------+----------------+
                                        |
                                        v
                      +----------------------------------+
                      |      LangGraph State Machine     |
                      |          (Track B: Agent)        |
                      +-----------------+----------------+
                                        |
                            Proposes ToolCall (Plan)
                                        |
                                        v
+---------------------------------------------------------------------------------+
|                         Track A: The Guardrail Service                          |
|                                                                                 |
|  1. Authentication & Role Claims                                                |
|     `Authorization: Bearer <JWT>` -> Resolves UserContext & Role                |
|                                                                                 |
|  2. Tool Registry Verification                                                  |
|     Validates tool existence against registry seed (`GET /tools`)               |
|                                                                                 |
|  3. Deterministic Argument Validation                                           |
|     Validates arguments against JSON schema (missing fields, unexpected keys)   |
|                                                                                 |
|  4. Role-Based Access Control (RBAC)                                            |
|     Enforces tool allowed_roles (viewer / analyst / operator / admin)           |
|                                                                                 |
|  5. Risk Tier Policy Engine                                                     |
|     Enforces ROLE_MAX_RISK ceiling against tool risk classification             |
|                                                                                 |
|  6. Human-in-the-Loop Approval Gateway                                          |
|     High-risk actions pause execution state awaiting operator/admin sign-off   |
|                                                                                 |
|  7. Sliding-Window Rate Limiting                                                |
|     Throttles per-role, per-tool request velocity                              |
|                                                                                 |
|  8. Structured Audit Event Dispatch                                             |
|     Emits immutable audit record for every decision                             |
+---------------------------------------------------------------------------------+
                                        |
                      +-----------------+-----------------+
                      |                                   |
              [ Decision: ALLOWED ]               [ Decision: DENIED ]
                      |                                   |
                      v                                   v
             Executes Tool in                   Rejects Request with
            Sandboxed Runtime                   Detailed Security Audit
```

---

## Governance Model

### 1. Role Hierarchy (RBAC)

Every caller is authenticated via JWT containing signed role claims. The system defines four distinct privilege tiers:

| Role | Permitted Actions | Maximum Risk Tier Allowed |
|---|---|---|
| `viewer` | Read-only inspection, calculations, basic discovery | `LOW` |
| `analyst` | Data aggregation, dataset querying, statistical evaluation | `MEDIUM` |
| `operator` | Operational workflows, ticket generation (subject to approval) | `HIGH` |
| `admin` | Full system execution, policy override, approval authority | `HIGH` |

### 2. Risk Tiers and Execution Policies

Every registered tool is statically categorized by risk tier:

| Tier | Policy | Action on Violation |
|---|---|---|
| `LOW` | Auto-executed for all authorized roles. | Blocked if role not in `allowed_roles`. |
| `MEDIUM` | Auto-executed for `analyst`, `operator`, `admin`. Strictly rate-limited. | Blocked for `viewer`. |
| `HIGH` | Execution halted; task transitions to `WAITING_APPROVAL`. Requires human sign-off. | Blocked for `viewer` and `analyst`. |

### 3. Starter Tool Registry

| Tool | Risk Tier | Allowed Roles | Requires Human Approval | Description |
|---|---|---|---|---|
| `calculator` | `LOW` | `viewer`, `analyst`, `operator`, `admin` | No | Evaluates mathematical expressions safely. |
| `file_reader` | `LOW` | `viewer`, `analyst`, `operator`, `admin` | No | Reads sandboxed documentation files. |
| `mock_search` | `LOW` | `viewer`, `analyst`, `operator`, `admin` | No | Queries simulated search index. |
| `csv_query` | `MEDIUM` | `analyst`, `operator`, `admin` | No | Filters and aggregates demo CSV datasets. |
| `create_ticket` | `HIGH` | `operator`, `admin` | Yes | Submits incident tickets via ticketing API. |

---

## Repository Structure

```
agent-sandbox/
├── contracts/                  # Shared Pydantic contracts and protocol definitions
│   ├── __init__.py             # Central re-exports of schemas and enums
│   ├── audit_event.py          # AuditEvent schema (decision, latency, approver)
│   ├── enums.py                # Role, RiskLevel, WorkflowStatus, Decisions
│   ├── plan.py                 # ToolCall and Plan schemas emitted by planners
│   ├── tool_registry.py        # ToolDefinition, ToolRegistryResponse, STARTER_TOOLS
│   └── workflow_state.py       # WorkflowState model for LangGraph state persistence
├── track-a-guardrail/          # Track A: Security, permissions, and registry service
│   ├── __init__.py             # Public package exports
│   ├── auth.py                 # OAuth2/JWT issuance, verification, role hierarchy
│   ├── main.py                 # FastAPI application entry point and routing
│   ├── permissions.py          # Deterministic RBAC and risk policy evaluation
│   └── registry.py             # Tool registry store and query endpoints
├── track-b-agent/              # Track B: LangGraph state machine, planner, and tools
│   └── README.md
├── tests/                      # Comprehensive test suite
│   ├── test_contracts.py       # Serialization and schema compatibility tests
│   └── test_track_a_phase1.py  # Guardrail RBAC, JWT, and permissions test suite
├── docker-compose.yml          # Containerized local orchestration (Postgres, Redis)
└── requirements.txt            # Unified dependencies
```

---

## Quickstart Guide

### Prerequisites

- Python 3.11+
- Virtual environment (`venv`)

### Installation

1. Create and activate a virtual environment:

```bash
# macOS / Linux
python3 -m venv .venv
source .venv/bin/activate

# Windows (PowerShell)
py -3.13 -m venv .venv
.\.venv\Scripts\Activate.ps1
```

2. Install dependencies:

```bash
pip install -r requirements.txt
```

### Running the Test Suite

Execute the test suite to verify contract serialization, JWT issuance, and RBAC enforcement:

```bash
python -m pytest tests/ -v
```

Expected output:
```
tests/test_contracts.py::test_tool_registry_serializes PASSED
tests/test_contracts.py::test_plan_round_trip PASSED
tests/test_contracts.py::test_workflow_state_defaults PASSED
tests/test_contracts.py::test_audit_event_round_trip PASSED
tests/test_track_a_phase1.py::test_create_and_decode_token PASSED
tests/test_track_a_phase1.py::test_issue_test_tokens PASSED
tests/test_track_a_phase1.py::test_invalid_token_rejected PASSED
tests/test_track_a_phase1.py::test_require_role_hierarchy PASSED
tests/test_track_a_phase1.py::test_auth_me_endpoint PASSED
tests/test_track_a_phase1.py::test_get_tools_matches_contract PASSED
tests/test_track_a_phase1.py::test_get_tool_by_name PASSED
tests/test_track_a_phase1.py::test_permissions_low_risk_all_roles PASSED
tests/test_track_a_phase1.py::test_permissions_medium_risk_rbac PASSED
tests/test_track_a_phase1.py::test_permissions_high_risk_rbac_and_approval PASSED
tests/test_track_a_phase1.py::test_permissions_argument_validation PASSED
tests/test_track_a_phase1.py::test_permissions_endpoint_http PASSED

======================= 16 passed in 0.45s =======================
```

---

## API Reference

Start the Guardrail service locally:

```bash
uvicorn track-a-guardrail.main:app --reload --port 8000
```

### Key Endpoints

| Method | Path | Description | Access Control |
|---|---|---|---|
| `GET` | `/health` | Service health status check | Public |
| `GET` | `/tools` | Returns all registered tools and schemas | Public |
| `GET` | `/tools/{tool_name}` | Retrieves single tool schema | Public |
| `POST` | `/auth/token` | Generates a JWT access token | Public |
| `GET` | `/auth/test-tokens` | Emits pre-computed test tokens for all roles | Public (Dev) |
| `GET` | `/auth/me` | Inspects authenticated user claims | Authenticated |
| `POST` | `/permissions/check` | Evaluates proposed `ToolCall` deterministically | Authenticated |

### Example: Permission Evaluation

Request:
```bash
curl -X POST "http://localhost:8000/permissions/check" \
  -H "Authorization: Bearer <VIEWER_JWT>" \
  -H "Content-Type: application/json" \
  -d '{
    "tool_name": "create_ticket",
    "arguments": {
      "title": "System alert",
      "description": "Disk space warning"
    },
    "reasoning": "Report system issue",
    "risk_level": "high",
    "requires_approval": true
  }'
```

Response:
```json
{
  "decision": "denied",
  "tool_name": "create_ticket",
  "user_role": "viewer",
  "risk_level": "high",
  "requires_approval": false,
  "reason": "Role 'viewer' is not permitted to use tool 'create_ticket'. Allowed: [operator, admin]."
}
```

---

## License

MIT License

Copyright (c) 2026 Agent Sandbox Contributors

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
