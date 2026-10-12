"""Track A observability contracts, access controls and metric denominators."""
import importlib

import pytest
from fastapi.testclient import TestClient
from contracts.enums import Role


def module(name):
    return importlib.import_module(f"track-a-guardrail.{name}")


client = TestClient(module("main").app)


def headers(user="alice", role="analyst"):
    return {"Authorization": "Bearer " + module("auth").create_token(user, Role(role))}


@pytest.fixture(autouse=True)
def clean():
    def reset():
        module("audit").audit_logger.clear()
        module("approvals").approval_store.clear()
        module("rate_limit").rate_limiter.reset()
        module("tracing").trace_store.clear()
    reset()
    yield
    reset()


def summary():
    response = client.get("/analytics/summary", headers=headers("reviewer", "operator"))
    assert response.status_code == 200
    return response.json()


def tool(name="calculator"):
    return {
        "tool_name": name,
        "arguments": {"expression": "2+2"} if name == "calculator" else {
            "title": "Bug", "description": "Broken",
        },
        "reasoning": "Test", "risk_level": "low" if name == "calculator" else "high",
        "requires_approval": name != "calculator",
    }


def post_trace(status="running", spans=None, who=None):
    return client.post("/trace/t1", headers=who or headers(), json={
        "status": status, "spans": spans if spans is not None else [],
    })


def test_permission_task_id_reason_and_empty_task_validation():
    response = client.post("/permissions/check?task_id=t1", headers=headers(role="viewer"), json=tool("create_ticket"))
    assert response.json()["decision"] == "denied"
    events = client.get("/audit?task_id=t1").json()
    assert len(events) == 1
    assert events[0]["reason"] == response.json()["reason"]
    assert events[0]["event_type"] == "permission"
    assert client.get("/audit?task_id=other").json() == []
    assert client.post("/permissions/check?task_id=", headers=headers(), json=tool()).status_code == 422


def test_duplicate_snapshot_and_retry_metrics():
    spans = [
        {"node": "tool_execution", "tool_name": "calculator", "step": 0,
         "attempt": 0, "error": "timeout", "will_retry": True},
        {"node": "replan", "step": 0, "attempt": 1},
        {"node": "tool_execution", "tool_name": "calculator", "step": 0,
         "attempt": 1, "output": {"result": 4}, "latency_ms": 1.2},
    ]
    for _ in range(2):
        assert post_trace("completed", spans).status_code == 200
    data = summary()
    assert data["reported_tasks"]["total"] == 1
    assert data["reported_tasks"]["by_status"]["completed"] == 1
    assert data["reported_tasks"]["by_status"]["failed"] == 0
    assert data["reported_tool_executions"]["attempts"] == 2
    assert data["reported_tool_executions"]["failed_attempts"] == 1
    saved = client.get("/trace/t1", headers=headers()).json()["spans"]
    assert saved[0]["will_retry"] is True
    assert saved[0]["latency_ms"] is None
    assert saved[2]["latency_ms"] == 1.2


def test_clarification_and_missing_step_are_supported():
    assert post_trace("needs_clarification", [{"node": "clarification", "output": {"question": "Which?"}}]).status_code == 200
    data = summary()["reported_tasks"]["by_status"]
    assert data["needs_clarification"] == 1
    assert data["completed"] == data["failed"] == 0


def test_append_resume_stale_and_terminal_snapshots():
    first = [{"node": "intake"}]
    assert post_trace(spans=first).status_code == 200
    paused = first + [{"node": "permission_check", "step": 0}]
    assert post_trace("waiting_approval", paused).status_code == 200
    assert post_trace(spans=first).status_code == 409
    assert post_trace(spans=[{"node": "rewritten"}] + paused[1:]).status_code == 409
    final = paused + [{"node": "final_response"}]
    assert post_trace("completed", final).status_code == 200
    assert post_trace("failed", final).status_code == 409
    assert post_trace("completed", final + [{"node": "extra"}]).status_code == 409


def test_trace_identity_and_read_permissions():
    assert post_trace().status_code == 200
    assert post_trace(who=headers("bob")).status_code == 403
    assert post_trace(who=headers("alice", "admin")).status_code == 403
    assert client.get("/trace/t1", headers=headers("bob")).status_code == 403
    assert client.get("/trace/t1", headers=headers("reviewer", "operator")).status_code == 200
    assert client.get("/trace/missing", headers=headers()).status_code == 404


@pytest.mark.parametrize("path,method,payload", [
    ("/trace/t1", "get", None),
    ("/trace/t1", "post", {"status": "running", "spans": []}),
    ("/analytics/summary", "get", None),
])
def test_auth_required(path, method, payload):
    assert client.request(method, path, json=payload).status_code in {401, 403}


@pytest.mark.parametrize("role", ["viewer", "analyst"])
def test_analytics_requires_reviewer(role):
    assert client.get("/analytics/summary", headers=headers(role=role)).status_code == 403


@pytest.mark.parametrize("span", [
    {"node": ""}, {"node": "execution", "step": -1},
    {"node": "execution", "attempt": -1}, {"node": "execution", "latency_ms": -1},
])
def test_invalid_span_rejected(span):
    assert post_trace(spans=[span]).status_code == 422


@pytest.mark.parametrize("action", ["approve", "reject"])
def test_approval_audit_and_permission_denominators(action):
    response = client.post("/approvals", headers=headers(role="operator"), json={
        "task_id": "t1", "tool_call": tool("create_ticket"),
    })
    assert response.status_code == 201
    approval_id = response.json()["id"]
    assert summary()["approvals"]["pending"] == 1
    assert summary()["approvals"]["approval_rate_among_resolved"] is None
    url = f"/approvals/{approval_id}/{action}"
    assert client.post(url, headers=headers("reviewer", "operator"), json={"note": "Reviewed"}).status_code == 200
    assert client.post(url, headers=headers("reviewer", "operator")).status_code == 400
    events = client.get("/audit?task_id=t1").json()
    assert len(events) == 2
    assert events[0]["approval_decision"] == "pending"
    assert events[1]["approval_decision"] == ("approved" if action == "approve" else "rejected")
    assert events[1]["execution_result"]["resolved_by"] == "reviewer"
    assert events[1]["reason"] == "Reviewed"
    data = summary()
    assert data["permission_checks"]["total"] == 0
    assert data["approvals"]["approval_rate_among_resolved"] == (1 if action == "approve" else 0)


def test_rate_limits_and_analytics_include_more_than_100_events():
    for index in range(101):
        response = client.post("/permissions/check", headers=headers(f"user-{index}"), json=tool())
        assert response.status_code == 200
    for _ in range(11):
        response = client.post("/permissions/check?task_id=burst", headers=headers("op", "operator"), json=tool("create_ticket"))
    assert response.status_code == 429
    data = summary()["permission_checks"]
    assert data["total"] == 112
    assert data["rate_limited"] == 1
    assert data["blocked_rate"] == round(1 / 112, 4)
    assert data["common_block_reasons"][0]["reason"].startswith("Rate limit exceeded")
