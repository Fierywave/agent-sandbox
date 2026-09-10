<div align="center">

# Permissioned Tool-Using Agent Sandbox
### Governed Multi-Tool Agent Runtime with Deterministic RBAC, Rate Limiting & Human-in-the-Loop Approval

[![Python](https://img.shields.io/badge/Python-3.11%2B-3776AB?style=for-the-badge&logo=python&logoColor=white)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/API-FastAPI-009688?style=for-the-badge&logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com/)
[![LangGraph](https://img.shields.io/badge/Orchestration-LangGraph-FF6F00?style=for-the-badge&logo=langchain&logoColor=white)](https://langchain-ai.github.io/langgraph/)
[![Pydantic](https://img.shields.io/badge/Validation-Pydantic%20v2-E92063?style=for-the-badge&logo=pydantic&logoColor=white)](https://docs.pydantic.dev/)
[![Auth](https://img.shields.io/badge/Auth-OAuth2%20%2B%20JWT-000000?style=for-the-badge&logo=jsonwebtokens&logoColor=white)](https://jwt.io/)
[![Testing](https://img.shields.io/badge/Test%20Suite-pytest-0A9EDC?style=for-the-badge&logo=pytest&logoColor=white)](https://docs.pytest.org/)

<p align="center">
  A controlled, security-hardened agent environment where an LLM can plan and use tools (sandboxed file reading, calculations, CSV querying, and ticket creation), but every tool invocation is deterministically governed by role-based access control, risk tiers, sliding-window rate limits, and human-in-the-loop approvals, backed by immutable audit logs.
</p>

[Project Overview](#project-overview) • [Key Capabilities](#key-platform-capabilities) • [Governance & RBAC](#governance--multi-tier-rbac) • [Starter Tools](#starter-tool-specifications) • [Setup & Execution Guide](#cross-platform-setup--execution-guide) • [API Reference](#api-reference) • [License](#license)

---

</div>

## Project Overview

Most autonomous agent implementations trust the underlying model to obey system instructions and avoid sensitive actions. In production, this assumption fails due to prompt injection, hallucinated arguments, runaway execution loops, and lack of compliance traceability.

This project implements a strict security principle: **the model proposes, the system disposes**. 

The LLM is treated as an untrusted reasoning engine. When the model selects a tool, its proposal is validated against strict Pydantic schemas, evaluated against the caller's authenticated JWT role, checked against rolling rate limits, and—for high-risk operations—held in a persistent paused state until a human operator grants approval. The agent runs as an explicit LangGraph state machine backed by SQLite checkpointing, allowing tasks to safely pause and resume across separate processes and server restarts.

---

## Key Platform Capabilities

| Module | Core Functionality | Security & Operational Value |
| :--- | :--- | :--- |
| **Tool Registry & Schemas** | Central catalog of tools with strict JSON input/output schemas, allowed roles, and risk levels. | Rejects malformed arguments before tools execute; eliminates raw command injection. |
| **Role-Based Access Control** | Token-level authorization mapping callers to Viewer, Analyst, Operator, or Admin roles. | Enforces security boundaries in application logic rather than relying on LLM good behavior. |
| **Risk-Tier Policy Engine** | Categorizes tools into Low, Medium, and High risk tiers with strict role ceilings. | Blocks unauthorized callers from accessing sensitive tools regardless of agent reasoning. |
| **Sliding-Window Rate Limiting** | Tracks tool usage per user and role across rolling 60-second windows. | Protects downstream APIs from runaway loops, brute-force attempts, and quota exhaustion. |
| **Human-in-the-Loop Gateway** | Persistent pause-and-resume workflow powered by LangGraph interrupts and SQLite checkpointing. | Halts sensitive operations for operator sign-off, surviving process restarts seamlessly. |
| **Immutable Audit Logging** | Records timestamps, user IDs, roles, arguments, decisions, approvers, and execution latencies. | Provides complete post-mortem auditability for compliance and debugging. |

---

## Governance & Multi-Tier RBAC

All inbound requests must supply an authenticated Bearer token carrying signed role claims. The system enforces four privilege tiers:

| Role | Permitted Actions | Maximum Risk Tier Allowed | Human Approval Authority |
| :--- | :--- | :---: | :---: |
| **Admin** | System administration, policy management, all tools | High | Full Approval Rights |
| **Operator** | Incident ticket management, operational workflows, all tools | High | Full Approval Rights |
| **Analyst** | CSV data analysis, dataset querying, math, search, file reading | Medium | Restricted (No Approval Rights) |
| **Viewer** | Sandboxed file reading, basic math, mock search index | Low | Restricted (No Approval Rights) |

---

## Starter Tool Specifications

The platform includes five pre-configured tools demonstrating governance across every risk tier:

| Tool Identifier | Risk Level | Allowed Roles | Requires Human Sign-Off | Rate Limit (Calls / Min) | Intended Workflow |
| :--- | :---: | :--- | :---: | :--- | :--- |
| `calculator` | Low | Viewer, Analyst, Operator, Admin | No | Viewer: 30 / Analyst: 60 / Operator: 60 / Admin: 120 | Evaluates mathematical expressions safely. |
| `file_reader` | Low | Viewer, Analyst, Operator, Admin | No | Viewer: 30 / Analyst: 60 / Operator: 60 / Admin: 120 | Reads sandboxed documentation from a demo folder. |
| `mock_search` | Low | Viewer, Analyst, Operator, Admin | No | Viewer: 20 / Analyst: 40 / Operator: 40 / Admin: 80 | Queries a simulated web knowledge index. |
| `csv_query` | Medium | Analyst, Operator, Admin | No | Analyst: 20 / Operator: 30 / Admin: 60 | Runs structured lookups on business CSV datasets. |
| `create_ticket` | High | Operator, Admin | Yes | Operator: 10 / Admin: 20 | Submits incident tickets to an issue-tracking API. |

---

## Cross-Platform Setup & Execution Guide

Follow these instructions to run the sandbox and test suites on Windows, macOS, or Linux.

### Prerequisites

- **Python 3.11+** installed and added to PATH
- **Git**

### 1. Clone the Repository
```bash
git clone https://github.com/Fierywave/agent-sandbox.git
cd agent-sandbox
```

### 2. Configure Virtual Environment

#### On Windows (PowerShell)
```powershell
py -3.13 -m venv .venv
.\.venv\Scripts\Activate.ps1
```

#### On macOS / Linux (Bash or Zsh)
```bash
python3 -m venv .venv
source .venv/bin/activate
```

### 3. Install Dependencies
```bash
pip install -r requirements.txt
```

### 4. Run the Full Test Suite
The repository includes automated test suites covering shared contracts, Guardrail RBAC, rate limiting, and the LangGraph state machine:

```bash
python -m pytest tests/ -v
```

---

## Running the Guardrail API Service

To launch the FastAPI guardrail service locally:

```bash
uvicorn track-a-guardrail.main:app --host 127.0.0.1 --port 8000 --reload
```

Once running, the interactive OpenAPI documentation is available at:
`http://127.0.0.1:8000/docs`

---

## API Reference

| HTTP Method | Route | Description | Authorization Requirement |
| :--- | :--- | :--- | :--- |
| `GET` | `/health` | Service health status check | Public |
| `GET` | `/tools` | Returns full registry with input/output schemas | Public |
| `GET` | `/tools/{tool_name}` | Retrieves single tool schema | Public |
| `POST` | `/auth/token` | Issues a signed JWT for testing or service auth | Public |
| `GET` | `/auth/test-tokens` | Returns pre-computed tokens for all 4 roles | Public (Development) |
| `GET` | `/auth/me` | Decodes caller identity and role claims | Authenticated (Bearer Token) |
| `POST` | `/permissions/check` | Evaluates proposed tool call through 6-step gate | Authenticated (Bearer Token) |
| `POST` | `/approvals` | Registers a pending human approval request | Authenticated (Bearer Token) |
| `GET` | `/approvals/pending` | Lists all actions currently awaiting sign-off | Public / Operator |
| `GET` | `/approvals/{id}` | Retrieves details of a specific approval task | Public / Operator |
| `POST` | `/approvals/{id}/approve` | Approves action, permitting agent resumption | Restricted to Operator / Admin |
| `POST` | `/approvals/{id}/reject` | Rejects action, terminating execution cleanly | Restricted to Operator / Admin |
| `GET` | `/audit` | Queries audit trail with user, tool, and decision filters | Public / Auditor |
| `GET` | `/audit/{event_id}` | Retrieves a single immutable audit event | Public / Auditor |

---

## License

This project is licensed under the terms of the [MIT License](LICENSE).
