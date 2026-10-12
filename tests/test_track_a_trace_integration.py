"""Publish real Track B histories through Track A's HTTP trace contract.

Publishing is performed by the test: Track B's automatic sender remains
the teammate's integration work.
"""
import importlib

import pytest
import requests

import approval_store as local_store
from approval_sync import sync_approvals
from graph import build_graph, submit_task
from guardrail_client import get_token
from test_track_b_integration import guardrail_url  # Reuse real HTTP server fixture.


def a(name):
    return importlib.import_module(f"track-a-guardrail.{name}")


@pytest.fixture(autouse=True)
def reset(tmp_path, monkeypatch):
    monkeypatch.setattr(local_store, "_STORE_PATH", tmp_path / "pending.json")
    for clear in (a("audit").audit_logger.clear, a("approvals").approval_store.clear,
                  a("rate_limit").rate_limiter.reset, a("tracing").trace_store.clear):
        clear()


@pytest.mark.parametrize("message,role,expected", [
    ("what is 6 * 7", "analyst", "completed"),
    ("create a ticket titled 'printer broken' with description 'jammed'", "viewer", "rejected"),
    ("look up the customer", "analyst", "needs_clarification"),
])
def test_real_graph_history_posts_unchanged(guardrail_url, tmp_path, message, role, expected):
    result = submit_task("trace-live", "alice", role, message,
                         registry_base_url=guardrail_url, db_path=str(tmp_path / "graph.db"))
    assert result["status"] == expected
    headers = {"Authorization": "Bearer " + get_token(guardrail_url, "alice", role)}
    response = requests.post(f"{guardrail_url}/trace/trace-live", headers=headers,
                             json={"status": result["status"], "spans": result["history"]}, timeout=5)
    assert response.status_code == 200, response.text
    assert len(response.json()["spans"]) == len(result["history"])
    for event in a("audit").audit_logger.snapshot():
        assert event.task_id == "trace-live"


@pytest.mark.parametrize("decision,expected", [("approve", "completed"), ("reject", "rejected")])
def test_real_pause_resume_trace_append(guardrail_url, tmp_path, decision, expected):
    task_id = "trace-approval"
    db_path = str(tmp_path / "graph.db")
    result = submit_task(task_id, "alice", "operator",
                         "create a ticket titled 'printer broken' with description 'jammed'",
                         registry_base_url=guardrail_url, db_path=db_path)
    assert result["status"] == "waiting_approval"
    headers = {"Authorization": "Bearer " + get_token(guardrail_url, "alice", "operator")}
    trace_url = f"{guardrail_url}/trace/{task_id}"
    response = requests.post(trace_url, headers=headers,
                             json={"status": result["status"], "spans": result["history"]}, timeout=5)
    assert response.status_code == 200, response.text
    reviewer = {"Authorization": "Bearer " + get_token(guardrail_url, "reviewer", "operator")}
    response = requests.post(f"{guardrail_url}/approvals/{result['approval_id']}/{decision}",
                             headers=reviewer, timeout=5)
    assert response.status_code == 200
    synced = sync_approvals(guardrail_url, db_path=db_path)
    assert synced[0]["final_status"] == expected
    app = build_graph(registry_base_url=guardrail_url, db_path=db_path)
    final = app.get_state({"configurable": {"thread_id": task_id}}).values
    response = requests.post(trace_url, headers=headers,
                             json={"status": final["status"], "spans": final["history"]}, timeout=5)
    assert response.status_code == 200, response.text
    assert response.json()["status"] == expected
