import importlib
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from contracts.audit_event import AuditEvent
from contracts.enums import PermissionDecision, RiskLevel, Role


def module(name):
    return importlib.import_module(f"track-a-guardrail.{name}")


client = TestClient(module("main").app)


def headers(role="operator", user="reviewer"):
    token = module("auth").create_token(user, Role(role))
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture(autouse=True)
def reset_stores():
    def clear():
        module("audit").audit_logger.clear()
        module("approvals").approval_store.clear()
        module("rate_limit").rate_limiter.reset()
        module("tracing").trace_store.clear()

    clear()
    yield
    clear()


def add_event(event_id, hour, user="alice", tool_name="calculator"):
    module("audit").audit_logger.log(
        AuditEvent(
            event_id=event_id,
            task_id=f"task-{event_id}",
            timestamp=datetime(
                2026, 10, 12, hour, 0, tzinfo=timezone.utc
            ),
            user_id=user,
            role="analyst",
            tool_name=tool_name,
            arguments={},
            risk_level=RiskLevel.LOW,
            permission_decision=PermissionDecision.ALLOWED,
            event_type="permission",
            reason="Test permission event",
        )
    )


def test_dashboard_page_is_served():
    response = client.get("/dashboard")

    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert "Review Console" in response.text
    assert "no-store" in response.headers["cache-control"]


@pytest.mark.parametrize(
    "path",
    ["/dashboard/api/approvals", "/dashboard/api/audit"],
)
def test_dashboard_data_requires_authentication(path):
    assert client.get(path).status_code in {401, 403}


@pytest.mark.parametrize("role", ["viewer", "analyst"])
@pytest.mark.parametrize(
    "path",
    ["/dashboard/api/approvals", "/dashboard/api/audit"],
)
def test_dashboard_data_rejects_non_reviewers(path, role):
    assert client.get(path, headers=headers(role)).status_code == 403


@pytest.mark.parametrize("role", ["operator", "admin"])
def test_reviewers_can_read_dashboard_data(role):
    assert client.get(
        "/dashboard/api/approvals",
        headers=headers(role),
    ).status_code == 200

    assert client.get(
        "/dashboard/api/audit",
        headers=headers(role),
    ).status_code == 200


def test_audit_dates_are_inclusive_and_timezone_aware():
    add_event("one", 9)
    add_event("two", 10)
    add_event("three", 11)

    response = client.get(
        "/dashboard/api/audit",
        headers=headers(),
        params={
            "start": "2026-10-12T14:30:00+05:30",
            "end": "2026-10-12T15:30:00+05:30",
        },
    )

    assert response.status_code == 200
    data = response.json()

    assert data["total"] == 2
    assert [event["event_id"] for event in data["items"]] == [
        "two",
        "one",
    ]


def test_reversed_date_range_is_rejected():
    response = client.get(
        "/dashboard/api/audit",
        headers=headers(),
        params={
            "start": "2026-10-13T00:00:00Z",
            "end": "2026-10-12T00:00:00Z",
        },
    )

    assert response.status_code == 422


def test_date_without_timezone_is_rejected():
    response = client.get(
        "/dashboard/api/audit",
        headers=headers(),
        params={"start": "2026-10-12T10:00:00"},
    )

    assert response.status_code == 422


def test_filters_and_pagination():
    add_event("one", 9)
    add_event("two", 10)
    add_event("three", 11, user="bob")

    response = client.get(
        "/dashboard/api/audit",
        headers=headers(),
        params={
            "user_id": "alice",
            "tool_name": "calculator",
            "risk_level": "low",
            "event_type": "permission",
            "offset": 1,
            "limit": 1,
        },
    )

    assert response.status_code == 200
    data = response.json()

    assert data["total"] == 2
    assert len(data["items"]) == 1
    assert data["items"][0]["event_id"] == "one"

    task_response = client.get(
        "/dashboard/api/audit",
        headers=headers(),
        params={"task_id": "task-two"},
    )

    assert task_response.json()["total"] == 1


@pytest.mark.parametrize("action", ["approve", "reject"])
def test_review_action_removes_pending_item_and_records_note(action):
    response = client.post(
        "/approvals",
        headers=headers(user="requester"),
        json={
            "task_id": "review-task",
            "tool_call": {
                "tool_name": "create_ticket",
                "arguments": {
                    "title": "Printer broken",
                    "description": "Paper jam",
                },
                "reasoning": "Report the printer problem",
                "risk_level": "high",
                "requires_approval": True,
            },
            "reason": "High-risk action requires review",
        },
    )

    assert response.status_code == 201
    approval_id = response.json()["id"]

    pending = client.get(
        "/dashboard/api/approvals",
        headers=headers(),
    ).json()

    assert len(pending) == 1
    assert pending[0]["registry_risk_level"] == "high"

    decision = client.post(
        f"/approvals/{approval_id}/{action}",
        headers=headers(),
        json={"note": "Reviewed in the dashboard"},
    )

    assert decision.status_code == 200

    assert client.get(
        "/dashboard/api/approvals",
        headers=headers(),
    ).json() == []

    audit = client.get(
        "/dashboard/api/audit",
        headers=headers(),
        params={
            "task_id": "review-task",
            "event_type": "approval",
        },
    ).json()

    assert audit["total"] == 2
    assert audit["items"][0]["reason"] == "Reviewed in the dashboard"
    assert audit["items"][0]["approval_decision"] == (
        "approved" if action == "approve" else "rejected"
    )