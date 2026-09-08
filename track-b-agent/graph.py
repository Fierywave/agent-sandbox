"""graph — the explicit LangGraph state machine.

Nodes: intake -> plan -> tool_selection -> permission_check ->
       tool_execution -> reflection -> approval_wait -> final_response

Phase 1 scope: only calculator and file_reader are wired into
TOOL_IMPLEMENTATIONS (both low risk, no approval), so the approval_wait
node is a placeholder — it will become a real persisted "pause and resume"
state in Phase 2 once Track A's approval backend exists. Nothing here
should later require restructuring the graph shape, only filling in
approval_wait's body and adding more tools to TOOL_IMPLEMENTATIONS.

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

from typing import Any, TypedDict

from langgraph.graph import END, StateGraph

from contracts import Plan, ToolCall, WorkflowStatus

from permission_client import check_permission_stub
from planner import Planner
from registry_client import get_tool_registry
from tools import calculator, file_reader

TOOL_IMPLEMENTATIONS = {
    "calculator": calculator.run,
    "file_reader": file_reader.run,
    # Phase 2 adds: "csv_query": csv_query.run, "create_ticket": create_ticket.run
}


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
    history: list[dict[str, Any]]


def build_graph(registry_base_url: str | None = None):
    """Compiles the graph. Pass registry_base_url (e.g. 'http://localhost:8000')
    to use Track A's live registry; omit it to use the Phase 0 stub."""
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
        # Phase 1 placeholder: no tool in the current TOOL_IMPLEMENTATIONS
        # set requires_approval, so this node is unreachable for now. Phase
        # 2 replaces this body with real persistence (task id, plan, reason)
        # and the graph resumes here once Track A's approval endpoint
        # reports a decision — see HANDOFF template for the swap point.
        state["history"].append({"node": "approval_wait", "output": {"note": "paused"}})
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
    graph.add_edge("approval_wait", END)  # Phase 2 replaces this with a real pause/resume
    graph.add_edge("final_response", END)

    return graph.compile()


def run_task(
    task_id: str,
    user_id: str,
    role: str,
    user_message: str,
    registry_base_url: str | None = None,
) -> GraphState:
    """Convenience entrypoint for the demo / tests."""
    app = build_graph(registry_base_url)
    initial_state: GraphState = {
        "task_id": task_id,
        "user_id": user_id,
        "role": role,
        "user_message": user_message,
        "history": [],
    }
    return app.invoke(initial_state)