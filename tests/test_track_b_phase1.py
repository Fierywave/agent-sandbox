"""Track B Phase 1 definition of done:
"given a stubbed registry and a sample request ('what's 42 * 17' or 'read
me the onboarding doc'), the graph runs start to finish and produces a
valid tool call and a final answer — no live permission service needed
yet."

These tests run entirely offline (no ANTHROPIC_API_KEY needed) via the
planner's rule-based fallback, so they're reproducible in CI and for your
teammate without a key or network access.
"""

import os

import pytest

from contracts import STARTER_TOOLS, WorkflowStatus
from graph import build_graph, run_task
from permission_client import check_permission_stub
from planner import Planner
from registry_client import get_tool_registry
from tools import calculator, file_reader

# Make sure these tests never accidentally hit the real Anthropic API.
os.environ.pop("ANTHROPIC_API_KEY", None)


# --- registry_client -----------------------------------------------------

def test_registry_client_falls_back_to_stub_without_url():
    tools = get_tool_registry(registry_base_url=None)
    names = {t.name for t in tools}
    assert names == {t.name for t in STARTER_TOOLS}


def test_registry_client_falls_back_when_live_url_unreachable():
    tools = get_tool_registry(registry_base_url="http://localhost:59999")
    assert len(tools) == len(STARTER_TOOLS)


# --- tools -----------------------------------------------------------------

def test_calculator_tool_direct():
    assert calculator.run("42 * 17")["result"] == 714


def test_calculator_tool_rejects_unsafe_expression():
    with pytest.raises(ValueError):
        calculator.run("__import__('os').system('echo hi')")


def test_file_reader_tool_direct():
    result = file_reader.run("onboarding.md")
    assert "Onboarding Guide" in result["content"]


def test_file_reader_blocks_path_escape():
    with pytest.raises(ValueError):
        file_reader.run("../../etc/passwd")


# --- planner (rule-based / offline) ----------------------------------------

def test_planner_produces_calculator_call():
    planner = Planner(STARTER_TOOLS)
    plan = planner.plan(task_id="t1", user_message="what's 42 * 17", role="analyst")
    assert len(plan.steps) == 1
    assert plan.steps[0].tool_name == "calculator"


def test_planner_produces_file_reader_call():
    planner = Planner(STARTER_TOOLS)
    plan = planner.plan(
        task_id="t2", user_message="read me the onboarding doc", role="viewer"
    )
    assert len(plan.steps) == 1
    assert plan.steps[0].tool_name == "file_reader"


def test_planner_no_tool_needed():
    planner = Planner(STARTER_TOOLS)
    plan = planner.plan(task_id="t3", user_message="hello there", role="viewer")
    assert plan.steps == []
    assert plan.final_answer_if_no_tool_needed is not None


# --- permission_client (mirrors Track A's documented logic) ----------------

def test_permission_stub_allows_low_risk_for_viewer():
    planner = Planner(STARTER_TOOLS)
    plan = planner.plan(task_id="t4", user_message="what's 2 + 2", role="viewer")
    tools_by_name = {t.name: t for t in STARTER_TOOLS}
    decision = check_permission_stub(plan.steps[0], "viewer", tools_by_name)
    assert decision["decision"] == "allowed"
    assert decision["requires_approval"] is False


def test_permission_stub_denies_high_risk_for_viewer():
    from contracts import RiskLevel, ToolCall

    tools_by_name = {t.name: t for t in STARTER_TOOLS}
    tc = ToolCall(
        tool_name="create_ticket",
        arguments={"title": "x", "description": "y"},
        reasoning="test",
        risk_level=RiskLevel.HIGH,
        requires_approval=True,
    )
    decision = check_permission_stub(tc, "viewer", tools_by_name)
    assert decision["decision"] == "denied"


def test_permission_stub_flags_requires_approval_for_operator():
    from contracts import RiskLevel, ToolCall

    tools_by_name = {t.name: t for t in STARTER_TOOLS}
    tc = ToolCall(
        tool_name="create_ticket",
        arguments={"title": "x", "description": "y"},
        reasoning="test",
        risk_level=RiskLevel.HIGH,
        requires_approval=True,
    )
    decision = check_permission_stub(tc, "operator", tools_by_name)
    assert decision["decision"] == "allowed"
    assert decision["requires_approval"] is True


# --- full graph, end to end (Phase 1 definition of done) -------------------

def test_graph_end_to_end_calculator():
    result = run_task(
        task_id="task-calc-1",
        user_id="alice",
        role="analyst",
        user_message="what's 42 * 17",
    )
    assert result["status"] == WorkflowStatus.COMPLETED.value
    assert result["tool_call"]["tool_name"] == "calculator"
    assert result["tool_result"]["result"] == 714
    assert result["final_response"] is not None
    node_names = [h["node"] for h in result["history"]]
    assert node_names == [
        "intake",
        "plan",
        "tool_selection",
        "permission_check",
        "tool_execution",
        "reflection",
        "final_response",
    ]


def test_graph_end_to_end_file_reader():
    result = run_task(
        task_id="task-file-1",
        user_id="bob",
        role="viewer",
        user_message="read me the onboarding doc",
    )
    assert result["status"] == WorkflowStatus.COMPLETED.value
    assert result["tool_call"]["tool_name"] == "file_reader"
    assert "Onboarding Guide" in result["tool_result"]["content"]


def test_graph_builds_without_live_registry():
    # Confirms Phase 1's "no live permission service needed yet" requirement
    app = build_graph(registry_base_url=None)
    assert app is not None