"""graph — the explicit LangGraph state machine.

Nodes: intake -> plan -> tool_selection -> permission_check ->
       tool_execution -> reflection -> approval_wait -> final_response

Phase 2: csv_query and create_ticket are now wired into
TOOL_IMPLEMENTATIONS alongside calculator/file_reader. approval_wait is a
REAL pause now — it calls interrupt() and the graph is compiled with a
SQLite checkpointer, so a paused task survives across separate process
runs (submit in one script/terminal, approve in another).

"The model proposes, the system disposes": the LLM only ever produces a
Plan/ToolCall. Every tool call is re-validated against the permission
check before tool_execution runs — the model's own reasoning field is
never trusted as authorization.

Import note: this directory (`track-b-agent/`) has a hyphen in its name,
so it can't be imported as a dotted Python package (`import track-b-agent`
is invalid syntax). Every module here uses plain top-level imports
(`from planner import Planner`, not `from .planner import Planner`) and
expects `track-b-agent/` itself — not its parent — to be on `sys.path`.
Run scripts/tests from inside this directory, same pattern Track A uses
running `uvicorn main:app` from inside `track-a-guardrail/`. The repo root
(for `contracts`) stays on `sys.path` via `conftest.py` / how pytest is
invoked from the repo root.
"""

from pathlib import Path
from typing import Any, TypedDict
import sqlite3

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, StateGraph
from langgraph.types import Command, interrupt

from contracts import Plan, ToolCall, WorkflowStatus

from approval_store import add_pending, remove_pending
from permission_client import check_permission_stub
from planner import Planner
from registry_client import get_tool_registry
from tools import calculator, create_ticket, csv_query, file_reader

TOOL_IMPLEMENTATIONS = {
    "calculator": calculator.run,
    "file_reader": file_reader.run,
    "csv_query": csv_query.run,
    "create_ticket": create_ticket.run,
}

DEFAULT_DB_PATH = str((Path(__file__).parent / "approvals.db").resolve())


class GraphState(TypedDict, total=False):
    task_id: str
    user_id: str
    role: str
    user_message: str
    plan: dict[str, Any] | None
    tool_call: dict[str, Any] | None
    permission_result: dict[str, Any] | None
    tool_result: dict[str, Any] | None
    tool_error: str | None
    status: str
    final_response: str | None
    approved_by: str | None
    history: list[dict[str, Any]]


def build_graph(registry_base_url: str | None = None, db_path: str = DEFAULT_DB_PATH):
    """Compiles the graph with a SQLite-backed checkpointer, so a paused
    (waiting_approval) task survives across separate process runs — the
    submit can happen in one terminal/script and the approve/reject in
    another, exactly like a real approval workflow.

    Pass registry_base_url (e.g. 'http://localhost:8000') to use Track A's
    live registry; omit it to use the Phase 0 stub."""
    tools = get_tool_registry(registry_base_url)
    tools_by_name = {t.name: t for t in tools}
    planner = Planner(tools)

    def intake(state: GraphState) -> GraphState:
        state["history"] = state.get("history", []) + [
            {"node": "intake", "input": {"message": state["user_message"]}}
        ]
        state["status"] = WorkflowStatus.RUNNING.value
        return state

    def plan_node(state: GraphState) -> GraphState:
        plan = planner.plan(
            task_id=state["task_id"], user_message=state["user_message"], role=state["role"]
        )
        state["plan"] = plan.model_dump(mode="json")
        state["history"].append({"node": "plan", "output": state["plan"]})
        return state

    def tool_selection(state: GraphState) -> GraphState:
        plan = Plan.model_validate(state["plan"])
        if not plan.steps:
            state["tool_call"] = None
            state["final_response"] = (
                plan.final_answer_if_no_tool_needed or "No tool call was needed."
            )
            state["status"] = WorkflowStatus.COMPLETED.value
        else:
            state["tool_call"] = plan.steps[0].model_dump(mode="json")
        state["history"].append(
            {"node": "tool_selection", "output": {"tool_call": state["tool_call"]}}
        )
        return state

    def permission_check(state: GraphState) -> GraphState:
        tool_call = ToolCall.model_validate(state["tool_call"])
        decision = check_permission_stub(tool_call, state["role"], tools_by_name)
        state["permission_result"] = decision
        state["history"].append({"node": "permission_check", "output": decision})

        if decision["decision"] != "allowed":
            state["status"] = WorkflowStatus.REJECTED.value
            state["final_response"] = f"Request denied: {decision['reason']}"
        elif decision.get("requires_approval"):
            state["status"] = WorkflowStatus.WAITING_APPROVAL.value
        return state

    def tool_execution(state: GraphState) -> GraphState:
        tool_call = ToolCall.model_validate(state["tool_call"])
        impl = TOOL_IMPLEMENTATIONS.get(tool_call.tool_name)
        if impl is None:
            state["tool_error"] = (
                f"'{tool_call.tool_name}' has no implementation wired yet "
                "(expected in a later phase)"
            )
            state["status"] = WorkflowStatus.FAILED.value
            state["history"].append(
                {"node": "tool_execution", "error": state["tool_error"]}
            )
            return state
        try:
            result = impl(**tool_call.arguments)
            state["tool_result"] = result
            state["history"].append({"node": "tool_execution", "output": result})
        except Exception as exc:  # noqa: BLE001
            state["tool_error"] = str(exc)
            state["status"] = WorkflowStatus.FAILED.value
            state["history"].append(
                {"node": "tool_execution", "error": state["tool_error"]}
            )
        return state

    def reflection(state: GraphState) -> GraphState:
        if state.get("tool_error"):
            state["final_response"] = f"The tool call failed: {state['tool_error']}"
        else:
            state["final_response"] = planner.summarize(
                state["user_message"], state.get("tool_result", {})
            )
            state["status"] = WorkflowStatus.COMPLETED.value
        state["history"].append(
            {"node": "reflection", "output": {"final_response": state["final_response"]}}
        )
        return state

    def approval_wait(state: GraphState) -> GraphState:
        # First time we reach this node (fresh pause): record it in the
        # local pending-approvals index so a reviewer can see it, then
        # call interrupt() — this suspends the graph here and persists
        # the full state via the SqliteSaver checkpointer. Execution does
        # not continue past this line until someone resumes the same
        # thread_id with Command(resume=...).
        add_pending(
            task_id=state["task_id"],
            user_id=state["user_id"],
            role=state["role"],
            tool_call=state["tool_call"],
            reason=state.get("permission_result", {}).get("reason", ""),
        )
        decision = interrupt(
            {
                "task_id": state["task_id"],
                "tool_call": state["tool_call"],
                "reason": "Awaiting human approval for a high-risk tool call",
            }
        )
        # Execution resumes here, in the SAME process invocation that
        # called Command(resume=decision) — `decision` is whatever dict
        # was passed to resume.
        remove_pending(state["task_id"])
        state["history"].append({"node": "approval_wait", "output": {"decision": decision}})

        if decision.get("approved"):
            state["status"] = WorkflowStatus.RUNNING.value
            state["approved_by"] = decision.get("approved_by")
        else:
            state["status"] = WorkflowStatus.REJECTED.value
            state["final_response"] = (
                f"Approval denied by {decision.get('approved_by', 'reviewer')}: "
                f"{decision.get('reason', 'no reason given')}"
            )
        return state

    def final_response_node(state: GraphState) -> GraphState:
        state["history"].append(
            {"node": "final_response", "output": {"final_response": state.get("final_response")}}
        )
        return state

    graph = StateGraph(GraphState)
    graph.add_node("intake", intake)
    graph.add_node("plan", plan_node)
    graph.add_node("tool_selection", tool_selection)
    graph.add_node("permission_check", permission_check)
    graph.add_node("tool_execution", tool_execution)
    graph.add_node("reflection", reflection)
    graph.add_node("approval_wait", approval_wait)
    graph.add_node("final_response", final_response_node)

    graph.set_entry_point("intake")
    graph.add_edge("intake", "plan")
    graph.add_edge("plan", "tool_selection")

    graph.add_conditional_edges(
        "tool_selection",
        lambda s: "final_response" if s.get("tool_call") is None else "permission_check",
        {"final_response": "final_response", "permission_check": "permission_check"},
    )

    def route_after_permission(state: GraphState) -> str:
        if state["status"] == WorkflowStatus.REJECTED.value:
            return "final_response"
        if state["status"] == WorkflowStatus.WAITING_APPROVAL.value:
            return "approval_wait"
        return "tool_execution"

    graph.add_conditional_edges(
        "permission_check",
        route_after_permission,
        {
            "final_response": "final_response",
            "approval_wait": "approval_wait",
            "tool_execution": "tool_execution",
        },
    )

    graph.add_edge("tool_execution", "reflection")
    graph.add_edge("reflection", "final_response")

    def route_after_approval(state: GraphState) -> str:
        return "final_response" if state["status"] == WorkflowStatus.REJECTED.value else "tool_execution"

    graph.add_conditional_edges(
        "approval_wait",
        route_after_approval,
        {"final_response": "final_response", "tool_execution": "tool_execution"},
    )
    graph.add_edge("final_response", END)

    conn = sqlite3.connect(db_path, check_same_thread=False)
    checkpointer = SqliteSaver(conn)
    return graph.compile(checkpointer=checkpointer)


def submit_task(
    task_id: str,
    user_id: str,
    role: str,
    user_message: str,
    registry_base_url: str | None = None,
    db_path: str = DEFAULT_DB_PATH,
) -> GraphState:
    """Starts a new task. For low/medium-risk tools this runs straight
    through to completion. For a high-risk tool the graph pauses inside
    approval_wait and this returns immediately with an '__interrupt__' key
    in the result — call resume_task(task_id, ...) later (even from a
    different process) to continue it."""
    app = build_graph(registry_base_url, db_path)
    config = {"configurable": {"thread_id": task_id}}
    initial_state: GraphState = {
        "task_id": task_id,
        "user_id": user_id,
        "role": role,
        "user_message": user_message,
        "history": [],
    }
    return app.invoke(initial_state, config=config)


def resume_task(
    task_id: str,
    approved: bool,
    approved_by: str,
    reason: str = "",
    registry_base_url: str | None = None,
    db_path: str = DEFAULT_DB_PATH,
) -> GraphState:
    """Resumes a task that's paused in approval_wait. `task_id` must match
    the one used in submit_task — that's how the checkpointer finds the
    right paused state, even in a brand-new process."""
    app = build_graph(registry_base_url, db_path)
    config = {"configurable": {"thread_id": task_id}}
    decision = {"approved": approved, "approved_by": approved_by, "reason": reason}
    return app.invoke(Command(resume=decision), config=config)


def run_task(
    task_id: str,
    user_id: str,
    role: str,
    user_message: str,
    registry_base_url: str | None = None,
    db_path: str = DEFAULT_DB_PATH,
) -> GraphState:
    """Phase 1 convenience alias, kept for backward compatibility — for
    calculator/file_reader (no approval needed) this behaves exactly as
    before. For anything requiring approval, use submit_task/resume_task
    instead so the pause is explicit."""
    return submit_task(task_id, user_id, role, user_message, registry_base_url, db_path)