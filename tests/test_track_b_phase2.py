"""Track B Phase 2 tests: csv_query + create_ticket tool wiring, and real
pause/resume for approval_wait using a SQLite-backed checkpointer.

Each test uses its own throwaway db_path (pytest's tmp_path fixture) so
tests never share checkpoint state with each other or with a real
approvals.db someone might have sitting around locally.
"""

import os

from contracts import WorkflowStatus
from graph import resume_task, submit_task
from tools import create_ticket, csv_query

os.environ.pop("ANTHROPIC_API_KEY", None)


# --- tools -----------------------------------------------------------------

def test_csv_query_tool_direct():
    result = csv_query.run("customers", {"customer_id": "C003"})
    assert len(result["rows"]) == 1
    assert result["rows"][0]["name"] == "Chen Wei"


def test_csv_query_tool_multi_filter():
    result = csv_query.run("customers", {"plan": "pro", "status": "active"})
    names = {row["name"] for row in result["rows"]}
    assert "Alice Nguyen" in names
    assert "Chen Wei" not in names  # past_due, not active


def test_csv_query_tool_unknown_dataset():
    import pytest

    with pytest.raises(ValueError):
        csv_query.run("nonexistent", {})


def test_create_ticket_tool_direct():
    result = create_ticket.run("Printer broken", "Office printer is jammed")
    assert result["ticket_id"].startswith("TCK-")
    assert result["status"] == "open"


def test_create_ticket_tool_invalid_priority():
    import pytest

    with pytest.raises(ValueError):
        create_ticket.run("x", "y", priority="not-a-real-priority")


# --- full graph: csv_query (medium risk, no approval) -----------------------

def test_graph_csv_query_analyst_completes_immediately(tmp_path):
    db_path = str(tmp_path / "test.db")
    result = submit_task(
        "csv-1", "erin", "analyst", "look up customer C003", db_path=db_path
    )
    assert "__interrupt__" not in result
    assert result["status"] == WorkflowStatus.COMPLETED.value
    assert result["tool_call"]["tool_name"] == "csv_query"


def test_graph_csv_query_viewer_denied(tmp_path):
    db_path = str(tmp_path / "test.db")
    result = submit_task(
        "csv-2", "dave", "viewer", "look up customer C003", db_path=db_path
    )
    assert result["status"] == WorkflowStatus.REJECTED.value


# --- full graph: create_ticket (high risk, real pause/resume) ---------------

def test_graph_create_ticket_pauses_for_approval(tmp_path):
    db_path = str(tmp_path / "test.db")
    result = submit_task(
        "ticket-1",
        "carol",
        "operator",
        "create a ticket titled 'printer broken' with description 'jammed'",
        db_path=db_path,
    )
    assert "__interrupt__" in result
    assert result["status"] == WorkflowStatus.WAITING_APPROVAL.value


def test_graph_create_ticket_approved_resumes_and_executes(tmp_path):
    db_path = str(tmp_path / "test.db")
    submit_task(
        "ticket-2",
        "carol",
        "operator",
        "create a ticket titled 'printer broken' with description 'jammed'",
        db_path=db_path,
    )
    result = resume_task(
        "ticket-2", approved=True, approved_by="admin_bob", db_path=db_path
    )
    assert result["status"] == WorkflowStatus.COMPLETED.value
    assert result["tool_result"]["ticket_id"].startswith("TCK-")


def test_graph_create_ticket_rejected_resumes_with_denial(tmp_path):
    db_path = str(tmp_path / "test.db")
    submit_task(
        "ticket-3",
        "carol",
        "operator",
        "create a ticket titled 'printer broken' with description 'jammed'",
        db_path=db_path,
    )
    result = resume_task(
        "ticket-3",
        approved=False,
        approved_by="admin_bob",
        reason="not urgent",
        db_path=db_path,
    )
    assert result["status"] == WorkflowStatus.REJECTED.value
    assert "not urgent" in result["final_response"]