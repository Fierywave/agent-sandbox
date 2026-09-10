import importlib
import pytest
from fastapi.testclient import TestClient

from contracts.enums import ApprovalDecision, PermissionDecision, RiskLevel, Role
from contracts.plan import ToolCall

auth = importlib.import_module("track-a-guardrail.auth")
approvals_mod = importlib.import_module("track-a-guardrail.approvals")
audit_mod = importlib.import_module("track-a-guardrail.audit")
rate_limit_mod = importlib.import_module("track-a-guardrail.rate_limit")
main = importlib.import_module("track-a-guardrail.main")

client = TestClient(main.app)
tokens = auth.issue_test_tokens()


@pytest.fixture(autouse=True)
def cleanup():
    rate_limit_mod.rate_limiter.reset()
    approvals_mod.approval_store.clear()
    audit_mod.audit_logger.clear()
    yield
    rate_limit_mod.rate_limiter.reset()
    approvals_mod.approval_store.clear()
    audit_mod.audit_logger.clear()


def test_approval_creation_and_listing():
    payload = {
        "task_id": "task-001",
        "tool_call": {
            "tool_name": "create_ticket",
            "arguments": {"title": "DB Outage", "description": "High latency"},
            "reasoning": "Report incident",
            "risk_level": "high",
            "requires_approval": True,
        },
        "reason": "Needs human verification",
    }

    resp = client.post(
        "/approvals",
        json=payload,
        headers={"Authorization": f"Bearer {tokens['operator']}"},
    )
    assert resp.status_code == 201
    data = resp.json()
    approval_id = data["id"]
    assert data["status"] == "pending"
    assert data["task_id"] == "task-001"
    assert data["role"] == "operator"

    pending_resp = client.get("/approvals/pending")
    assert pending_resp.status_code == 200
    pending_ids = [item["id"] for item in pending_resp.json()]
    assert approval_id in pending_ids

    get_resp = client.get(f"/approvals/{approval_id}")
    assert get_resp.status_code == 200
    assert get_resp.json()["id"] == approval_id


def test_approval_rbac_operator_and_admin_only():
    payload = {
        "task_id": "task-002",
        "tool_call": {
            "tool_name": "create_ticket",
            "arguments": {"title": "Restart Server", "description": "Memory leak"},
            "reasoning": "Maintenance",
            "risk_level": "high",
            "requires_approval": True,
        },
    }

    create_resp = client.post(
        "/approvals",
        json=payload,
        headers={"Authorization": f"Bearer {tokens['operator']}"},
    )
    approval_id = create_resp.json()["id"]

    viewer_approve = client.post(
        f"/approvals/{approval_id}/approve",
        json={"note": "Viewer trying to approve"},
        headers={"Authorization": f"Bearer {tokens['viewer']}"},
    )
    assert viewer_approve.status_code == 403

    analyst_approve = client.post(
        f"/approvals/{approval_id}/approve",
        json={"note": "Analyst trying to approve"},
        headers={"Authorization": f"Bearer {tokens['analyst']}"},
    )
    assert analyst_approve.status_code == 403

    op_approve = client.post(
        f"/approvals/{approval_id}/approve",
        json={"note": "Approved by operator"},
        headers={"Authorization": f"Bearer {tokens['operator']}"},
    )
    assert op_approve.status_code == 200
    data = op_approve.json()
    assert data["status"] == "approved"
    assert data["resolved_by"] == "test-operator"

    duplicate_approve = client.post(
        f"/approvals/{approval_id}/approve",
        headers={"Authorization": f"Bearer {tokens['admin']}"},
    )
    assert duplicate_approve.status_code == 400


def test_approval_rejection_flow():
    payload = {
        "task_id": "task-003",
        "tool_call": {
            "tool_name": "create_ticket",
            "arguments": {"title": "Drop Schema", "description": "Clean db"},
            "reasoning": "Cleanup",
            "risk_level": "high",
            "requires_approval": True,
        },
    }
    create_resp = client.post(
        "/approvals",
        json=payload,
        headers={"Authorization": f"Bearer {tokens['admin']}"},
    )
    approval_id = create_resp.json()["id"]

    reject_resp = client.post(
        f"/approvals/{approval_id}/reject",
        json={"note": "Rejected: dangerous action"},
        headers={"Authorization": f"Bearer {tokens['admin']}"},
    )
    assert reject_resp.status_code == 200
    assert reject_resp.json()["status"] == "rejected"
    assert reject_resp.json()["resolved_by"] == "test-admin"


def test_rate_limiting_enforcement():
    # Operator limit for create_ticket is 10/min
    call_payload = {
        "tool_name": "create_ticket",
        "arguments": {"title": "Burst Ticket", "description": "Testing rate limit"},
        "reasoning": "Load test",
        "risk_level": "high",
        "requires_approval": True,
    }

    headers = {"Authorization": f"Bearer {tokens['operator']}"}

    for i in range(10):
        resp = client.post("/permissions/check", json=call_payload, headers=headers)
        assert resp.status_code == 200
        assert resp.json()["decision"] == "allowed"

    blocked_resp = client.post("/permissions/check", json=call_payload, headers=headers)
    assert blocked_resp.status_code == 429
    assert "Rate limit exceeded" in blocked_resp.json()["detail"]


def test_audit_logging_and_filtering():
    calc_payload = {
        "tool_name": "calculator",
        "arguments": {"expression": "2 * 3"},
        "reasoning": "Calculate",
        "risk_level": "low",
        "requires_approval": False,
    }
    client.post(
        "/permissions/check",
        json=calc_payload,
        headers={"Authorization": f"Bearer {tokens['viewer']}"},
    )

    ticket_payload = {
        "tool_name": "create_ticket",
        "arguments": {"title": "Bug", "description": "Crash"},
        "reasoning": "File issue",
        "risk_level": "high",
        "requires_approval": True,
    }
    client.post(
        "/permissions/check",
        json=ticket_payload,
        headers={"Authorization": f"Bearer {tokens['viewer']}"},
    )

    audit_resp = client.get("/audit")
    assert audit_resp.status_code == 200
    events = audit_resp.json()
    assert len(events) >= 2

    filter_calc = client.get("/audit?tool_name=calculator")
    assert filter_calc.status_code == 200
    assert all(e["tool_name"] == "calculator" for e in filter_calc.json())

    filter_denied = client.get("/audit?permission_decision=denied")
    assert filter_denied.status_code == 200
    assert any(e["tool_name"] == "create_ticket" and e["permission_decision"] == "denied" for e in filter_denied.json())

    first_id = events[0]["event_id"]
    event_by_id = client.get(f"/audit/{first_id}")
    assert event_by_id.status_code == 200
    assert event_by_id.json()["event_id"] == first_id
