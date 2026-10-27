"""graph — the explicit LangGraph state machine.

Nodes: intake -> plan -> tool_selection -> permission_check ->
       [approval_wait] -> tool_execution -> [replan] -> reflection -> final_response

A task is now a list of steps. For each step the graph loops
permission_check -> (approval_wait) -> tool_execution, then moves to the
next step, so a task like "look up customer C003 and create a ticket about
them" is one run. What each step produced is kept as typed StepResult
records in the state; later steps read them through {{step.path}}
placeholders (steps.py), resolved by the system just before the step is
permission-checked — the permission check and any human approver always
see the real, concrete arguments.

Three Phase 3 behaviours:
  * CLARIFY, don't guess. If the planner can't act safely (no customer
    named) or a lookup the user meant to return ONE record returns several
    or none, the run ends in needs_clarification with a question.
  * BOUNDED RETRY. When a tool raises, the error goes back to the planner
    (`replan`) which may revise the step; this is capped at `max_retries`
    per step. Every retried step goes through permission_check again.
    High-risk tools are NEVER retried automatically: the failed attempt may
    have partly succeeded (a ticket half-created), so repeating it without
    a person is how duplicates happen.
  * LIVE MODE (from Day 7): with a guardrail URL, permission checks go to
    Track A and approvals are filed there; an unreachable service denies
    the call (fail closed). With no URL everything runs on local stubs.

"The model proposes, the system disposes": the LLM only ever produces
plans. Every step is re-validated by the permission check before it runs.

Import note: this directory (`track-b-agent/`) has a hyphen in its name,
so it can't be imported as a dotted Python package. Every module here
uses plain top-level imports and expects `track-b-agent/` itself on
`sys.path` (tests/conftest.py handles that), with the repo root on
`sys.path` for `contracts`.
"""

import logging
import sqlite3
import time
from pathlib import Path
from typing import Any, TypedDict

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, StateGraph
from langgraph.types import Command, interrupt

from contracts import RiskLevel, ToolCall, WorkflowStatus

from agent_types import MAX_PLAN_STEPS, MAX_RETRIES, TRANSIENT_ERRORS, AgentPlan, StepResult
from approval_store import add_pending, remove_pending
from guardrail_client import GuardrailError, check_permission_live, create_approval, get_token
from permission_client import check_permission_stub
from planner import LlmCall, Planner
from registry_client import get_tool_registry
from steps import UnresolvedReferenceError, check_single_result, resolve_arguments
from tools import calculator, create_ticket, csv_query, file_reader
from trace_publisher import publish_trace

logger = logging.getLogger(__name__)

TOOL_IMPLEMENTATIONS = {
    "calculator": calculator.run,
    "file_reader": file_reader.run,
    "csv_query": csv_query.run,
    "create_ticket": create_ticket.run,
}

DEFAULT_DB_PATH = str((Path(__file__).parent / "approvals.db").resolve())

# LangGraph stops a run after this many node executions (default 25). A
# worst-case plan (5 steps, each retried) needs ~30, so raise it explicitly.
RECURSION_LIMIT = 100

# Track A freezes a trace once it reaches one of these, so intermediate
# snapshots are sent as "running" and the real final status goes out with
# the last node only.
TERMINAL_STATUSES = {
    WorkflowStatus.COMPLETED.value,
    WorkflowStatus.REJECTED.value,
    WorkflowStatus.FAILED.value,
}


class GraphState(TypedDict, total=False):
    task_id: str
    user_id: str
    role: str
    user_message: str
    plan: dict[str, Any] | None
    # the step list and where we are in it
    steps: list[dict[str, Any]]
    single_result_steps: list[int]
    step_index: int
    step_results: list[dict[str, Any]]
    # the concrete call currently being checked/run (placeholders resolved)
    tool_call: dict[str, Any] | None
    permission_result: dict[str, Any] | None
    tool_result: dict[str, Any] | None
    tool_error: str | None
    error_kind: str | None
    # retry bookkeeping
    attempt: int
    retry_count: int
    retry_pending: bool
    clarification_question: str | None
    status: str
    final_response: str | None
    approved_by: str | None
    approval_id: str | None
    approval_requested_at: float | None
    history: list[dict[str, Any]]


def build_graph(
    registry_base_url: str | None = None,
    db_path: str = DEFAULT_DB_PATH,
    max_retries: int = MAX_RETRIES,
    llm_call: LlmCall | None = None,
):
    """Compiles the graph with a SQLite-backed checkpointer, so a paused
    (waiting_approval) task survives across separate process runs.

    registry_base_url: Track A's URL for live mode; None for offline stubs.
    max_retries: automatic retries allowed per step (default 1).
    llm_call: inject a model function (tests); default is the real API when
              ANTHROPIC_API_KEY is set, rule-based planning otherwise."""
    tools = get_tool_registry(registry_base_url)
    tools_by_name = {t.name: t for t in tools}
    planner = Planner(tools, llm_call=llm_call)
    live = registry_base_url is not None
    token_cache: dict[tuple[str, str], str] = {}

    def token_for(user_id: str, role: str) -> str:
        if (user_id, role) not in token_cache:
            token_cache[(user_id, role)] = get_token(registry_base_url, user_id, role)
        return token_cache[(user_id, role)]

    def publish(state: GraphState, node_name: str) -> None:
        """Send the history so far to Track A's trace API (live mode only).
        Telemetry must never change the task: nothing here can raise, and
        no tool is ever re-run to produce a trace."""
        if not live:
            return
        try:
            status = state["status"]
            if node_name != "final_response" and status in TERMINAL_STATUSES:
                status = WorkflowStatus.RUNNING.value
            publish_trace(
                registry_base_url,
                token_for(state["user_id"], state["role"]),
                state["task_id"],
                status,
                state["history"],
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("trace publishing failed for %s: %s", state.get("task_id"), exc)

    def instrument(name: str, fn):
        """Measure how long a node ran and stamp it on the history entry the
        node wrote, then publish. The timing is set before the entry is ever
        published, because Track A rejects any later change to an entry."""

        def wrapped(state: GraphState) -> GraphState:
            first_new = len(state.get("history", []))
            started = time.perf_counter()
            out = fn(state)
            elapsed_ms = round((time.perf_counter() - started) * 1000, 3)
            for entry in out["history"][first_new:]:
                if entry["node"] == name:
                    entry["latency_ms"] = elapsed_ms
            publish(out, name)
            return out

        return wrapped

    def risk_of(tool_name: str) -> RiskLevel:
        tool = tools_by_name.get(tool_name)
        return tool.risk_level if tool else RiskLevel.HIGH  # unknown tool: assume the worst

    def ask(state: GraphState, question: str) -> None:
        state["status"] = WorkflowStatus.NEEDS_CLARIFICATION.value
        state["clarification_question"] = question
        state["final_response"] = question

    # ---------------------------------------------------------------- nodes
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
        plan = AgentPlan.model_validate(state["plan"])
        state.update(
            steps=[s.model_dump(mode="json") for s in plan.steps],
            single_result_steps=list(plan.single_result_steps),
            step_index=0,
            step_results=[],
            tool_call=None,
            attempt=0,
            retry_count=0,
            retry_pending=False,
            error_kind=None,
            clarification_question=None,
        )
        if plan.clarification_question:
            state["steps"] = []  # never run anything alongside a question
            ask(state, plan.clarification_question)
        elif not plan.steps:
            state["final_response"] = plan.final_answer_if_no_tool_needed or "No tool call was needed."
            state["status"] = WorkflowStatus.COMPLETED.value
        elif len(plan.steps) > MAX_PLAN_STEPS:
            state["steps"] = []
            state["status"] = WorkflowStatus.FAILED.value
            state["final_response"] = (
                f"That plan has {len(plan.steps)} steps; the limit is {MAX_PLAN_STEPS}."
            )
        state["history"].append(
            {
                "node": "tool_selection",
                "output": {
                    "tool_call": state["steps"][0] if state["steps"] else None,
                    "steps": [s["tool_name"] for s in state["steps"]],
                    "clarification": state["clarification_question"],
                },
            }
        )
        return state

    def permission_check(state: GraphState) -> GraphState:
        idx = state["step_index"]
        step = state["steps"][idx]
        total = len(state["steps"])

        # Turn this step's placeholders into real values from earlier
        # results, BEFORE any permission decision, so the decision (and a
        # human approver) is about the concrete call.
        try:
            resolved = resolve_arguments(step["arguments"], state["step_results"])
        except UnresolvedReferenceError as exc:
            state["tool_call"] = None
            state["status"] = WorkflowStatus.FAILED.value
            state["final_response"] = f"I couldn't continue at step {idx + 1} of {total}: {exc}"
            state["history"].append({"node": "permission_check", "step": idx, "error": str(exc)})
            return state
        if resolved != step["arguments"]:
            state["history"].append(
                {"node": "resolve_references", "step": idx,
                 "input": {"arguments": step["arguments"]}, "output": {"arguments": resolved}}
            )

        tool_call = ToolCall.model_validate({**step, "arguments": resolved})
        state["tool_call"] = tool_call.model_dump(mode="json")
        token = None

        if live:
            # Ask Track A's real engine; fail CLOSED on any trouble reaching
            # it. The token is minted per call and never stored in graph
            # state (state is persisted to the checkpoint DB).
            try:
                token = token_for(state["user_id"], state["role"])
                decision = check_permission_live(
                    registry_base_url, token, tool_call, state["role"], state["task_id"]
                )
            except GuardrailError as exc:
                decision = {
                    "decision": "denied",
                    "tool_name": tool_call.tool_name,
                    "user_role": state["role"],
                    "risk_level": tool_call.risk_level.value,
                    "requires_approval": False,
                    "reason": f"Guardrail service unavailable, call denied (fail closed): {exc}",
                }
        else:
            decision = check_permission_stub(tool_call, state["role"], tools_by_name)

        state["permission_result"] = decision
        state["history"].append(
            {"node": "permission_check", "step": idx, "tool_name": tool_call.tool_name,
             "input": {"arguments": resolved}, "output": decision}
        )

        if decision["decision"] != "allowed":
            state["status"] = WorkflowStatus.REJECTED.value
            note = f" (step {idx + 1} of {total}; earlier steps had already completed)" if idx else ""
            state["final_response"] = f"Request denied: {decision['reason']}{note}"
            return state

        if decision.get("requires_approval"):
            # File the approval request HERE, not inside approval_wait:
            # LangGraph re-runs a paused node from its first line when it
            # resumes, so a side effect placed before interrupt() would run
            # twice (a duplicate approval request).
            approval_id = None
            if live:
                try:
                    record = create_approval(
                        registry_base_url,
                        token,
                        state["task_id"],
                        state["tool_call"],
                        decision.get("reason", ""),
                    )
                    approval_id = record["id"]
                except (GuardrailError, KeyError) as exc:
                    state["status"] = WorkflowStatus.REJECTED.value
                    state["final_response"] = (
                        f"Request denied: could not file approval request (fail closed): {exc}"
                    )
                    return state
            state["approval_id"] = approval_id
            state["approval_requested_at"] = time.time()
            add_pending(
                task_id=state["task_id"],
                user_id=state["user_id"],
                role=state["role"],
                tool_call=state["tool_call"],
                reason=decision.get("reason", ""),
                approval_id=approval_id,
            )
            state["status"] = WorkflowStatus.WAITING_APPROVAL.value
        return state

    def tool_execution(state: GraphState) -> GraphState:
        tool_call = ToolCall.model_validate(state["tool_call"])
        idx = state["step_index"]
        attempt = state.get("attempt", 0)

        impl = TOOL_IMPLEMENTATIONS.get(tool_call.tool_name)
        if impl is None:
            state["tool_error"] = (
                f"'{tool_call.tool_name}' has no implementation wired yet "
                "(expected in a later phase)"
            )
            state["status"] = WorkflowStatus.FAILED.value
            state["history"].append(
                {"node": "tool_execution", "step": idx, "attempt": attempt, "tool_name": tool_call.tool_name,
                 "input": tool_call.arguments, "error": state["tool_error"]}
            )
            return state

        try:
            result = impl(**tool_call.arguments)
            record = StepResult(
                step=idx, tool_name=tool_call.tool_name, arguments=tool_call.arguments, result=result
            )
        except Exception as exc:  # noqa: BLE001 - any tool failure is handled the same way
            state["tool_error"] = str(exc) or type(exc).__name__
            state["error_kind"] = "transient" if isinstance(exc, TRANSIENT_ERRORS) else "error"
            high_risk = risk_of(tool_call.tool_name) == RiskLevel.HIGH
            will_retry = not high_risk and attempt < max_retries
            if will_retry:
                state["retry_pending"] = True
            else:
                state["status"] = WorkflowStatus.FAILED.value
                if high_risk:
                    state["tool_error"] += (
                        " (not retried automatically: a high-risk action may have partly "
                        "succeeded, so only a person should repeat it)"
                    )
            state["history"].append(
                {
                    "node": "tool_execution",
                    "step": idx,
                    "attempt": attempt,
                    "tool_name": tool_call.tool_name,
                    "input": tool_call.arguments,
                    "error": state["tool_error"],
                    "will_retry": will_retry,
                }
            )
            return state

        state["tool_result"] = result
        state["step_results"] = state["step_results"] + [record.model_dump(mode="json")]
        state["tool_error"] = None
        state["error_kind"] = None
        state["retry_pending"] = False
        state["attempt"] = 0
        state["step_index"] = idx + 1
        state["history"].append(
            {"node": "tool_execution", "step": idx, "attempt": attempt, "tool_name": tool_call.tool_name,
                 "input": tool_call.arguments, "output": result}
        )

        # The user asked for ONE record but the lookup returned several (or
        # none): ask which, rather than carrying on with a guess.
        if idx in state["single_result_steps"]:
            question = check_single_result(tool_call.arguments, result)
            if question:
                ask(state, question)
                state["history"].append(
                    {"node": "clarification", "step": idx, "output": {"question": question}}
                )
        return state

    def replan(state: GraphState) -> GraphState:
        """A tool raised. Give the planner the error and see if it can
        revise the step. Runs only when a retry is allowed."""
        idx = state["step_index"]
        failed = ToolCall.model_validate(state["tool_call"])
        new = planner.replan(
            task_id=state["task_id"],
            user_message=state["user_message"],
            failed=failed,
            error=state["tool_error"],
            transient=state.get("error_kind") == "transient",
            expects_single=idx in state["single_result_steps"],
            completed=state["step_results"],
        )
        state["history"].append(
            {
                "node": "replan",
                "step": idx,
                "attempt": state["attempt"] + 1,
                "tool_name": failed.tool_name,
                "input": {"failed_step": failed.model_dump(mode="json")},
                "error": state["tool_error"],
                "output": new.model_dump(mode="json") if new else None,
            }
        )
        state["retry_pending"] = False

        if new is not None and new.clarification_question:
            state["tool_error"] = None
            state["error_kind"] = None
            ask(state, new.clarification_question)
            return state
        if new is None or not new.steps:
            state["status"] = WorkflowStatus.FAILED.value
            state["tool_error"] += " (not retried automatically: no revised plan was available)"
            return state
        if idx + len(new.steps) > MAX_PLAN_STEPS:
            state["status"] = WorkflowStatus.FAILED.value
            state["tool_error"] += f" (the revised plan would exceed {MAX_PLAN_STEPS} steps)"
            return state

        # Replace the failed step and everything after it with the revision.
        state["steps"] = state["steps"][:idx] + [s.model_dump(mode="json") for s in new.steps]
        state["single_result_steps"] = [i for i in state["single_result_steps"] if i < idx] + [
            idx + j for j in new.single_result_steps
        ]
        state["attempt"] += 1
        state["retry_count"] += 1
        state["tool_error"] = None
        state["error_kind"] = None
        return state

    def reflection(state: GraphState) -> GraphState:
        if state.get("tool_error"):
            attempts = state.get("attempt", 0) + 1
            after = f" after {attempts} attempts" if attempts > 1 else ""
            done = [r["tool_name"] for r in state.get("step_results", [])]
            earlier = f" Steps completed before the failure: {', '.join(done)}." if done else ""
            state["final_response"] = f"The tool call failed{after}: {state['tool_error']}.{earlier}"
        else:
            state["final_response"] = planner.summarize_steps(
                state["user_message"], state["step_results"]
            )
            state["status"] = WorkflowStatus.COMPLETED.value
        state["history"].append(
            {"node": "reflection", "output": {"final_response": state["final_response"]}}
        )
        return state

    def approval_wait(state: GraphState) -> GraphState:
        # interrupt() suspends the graph here and persists the full state
        # via the SqliteSaver checkpointer; nothing below it runs until
        # someone resumes the same thread_id with Command(resume=...).
        # NOTE: this whole function re-runs from the top on resume, so it
        # must have no side effects before interrupt() (the approval
        # request is filed in permission_check for exactly that reason).
        decision = interrupt(
            {
                "task_id": state["task_id"],
                "tool_call": state["tool_call"],
                "reason": "Awaiting human approval for a high-risk tool call",
            }
        )
        remove_pending(state["task_id"])
        # Time spent waiting for a person is reported separately (waited_ms);
        # latency_ms stays the time the node itself was actually running.
        waited_ms = round((time.time() - state.get("approval_requested_at", time.time())) * 1000, 1)
        state["history"].append(
            {"node": "approval_wait", "input": {"tool_call": state["tool_call"]},
             "output": {"decision": decision}, "waited_ms": waited_ms}
        )

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

    # ---------------------------------------------------------------- wiring
    graph = StateGraph(GraphState)
    for name, fn in [
        ("intake", intake),
        ("plan", plan_node),
        ("tool_selection", tool_selection),
        ("permission_check", permission_check),
        ("tool_execution", tool_execution),
        ("replan", replan),
        ("reflection", reflection),
        ("approval_wait", approval_wait),
        ("final_response", final_response_node),
    ]:
        graph.add_node(name, instrument(name, fn))

    graph.set_entry_point("intake")
    graph.add_edge("intake", "plan")
    graph.add_edge("plan", "tool_selection")

    graph.add_conditional_edges(
        "tool_selection",
        lambda s: "permission_check" if s["steps"] else "final_response",
        {"permission_check": "permission_check", "final_response": "final_response"},
    )

    def route_after_permission(state: GraphState) -> str:
        if state["status"] in (WorkflowStatus.REJECTED.value, WorkflowStatus.FAILED.value):
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

    def route_after_approval(state: GraphState) -> str:
        return "final_response" if state["status"] == WorkflowStatus.REJECTED.value else "tool_execution"

    graph.add_conditional_edges(
        "approval_wait",
        route_after_approval,
        {"final_response": "final_response", "tool_execution": "tool_execution"},
    )

    def route_after_execution(state: GraphState) -> str:
        if state.get("retry_pending"):
            return "replan"
        if state["status"] == WorkflowStatus.NEEDS_CLARIFICATION.value:
            return "final_response"
        if state.get("tool_error") or state["status"] == WorkflowStatus.FAILED.value:
            return "reflection"
        if state["step_index"] < len(state["steps"]):
            return "permission_check"  # next step
        return "reflection"

    graph.add_conditional_edges(
        "tool_execution",
        route_after_execution,
        {
            "replan": "replan",
            "final_response": "final_response",
            "reflection": "reflection",
            "permission_check": "permission_check",
        },
    )

    def route_after_replan(state: GraphState) -> str:
        if state["status"] == WorkflowStatus.NEEDS_CLARIFICATION.value:
            return "final_response"
        if state["status"] == WorkflowStatus.FAILED.value:
            return "reflection"
        return "permission_check"  # the revised step is permission-checked again

    graph.add_conditional_edges(
        "replan",
        route_after_replan,
        {
            "final_response": "final_response",
            "reflection": "reflection",
            "permission_check": "permission_check",
        },
    )

    graph.add_edge("reflection", "final_response")
    graph.add_edge("final_response", END)

    conn = sqlite3.connect(db_path, check_same_thread=False)
    return graph.compile(checkpointer=SqliteSaver(conn))


def _config(task_id: str) -> dict:
    return {"configurable": {"thread_id": task_id}, "recursion_limit": RECURSION_LIMIT}


def submit_task(
    task_id: str,
    user_id: str,
    role: str,
    user_message: str,
    registry_base_url: str | None = None,
    db_path: str = DEFAULT_DB_PATH,
    max_retries: int = MAX_RETRIES,
    llm_call: LlmCall | None = None,
) -> GraphState:
    """Starts a new task. Runs straight through for anything not needing
    approval. For a high-risk step the graph pauses inside approval_wait and
    this returns with an '__interrupt__' key — call resume_task (or, in live
    mode, approval_sync.sync_approvals) later to continue it."""
    app = build_graph(registry_base_url, db_path, max_retries, llm_call)
    initial_state: GraphState = {
        "task_id": task_id,
        "user_id": user_id,
        "role": role,
        "user_message": user_message,
        "history": [],
    }
    return app.invoke(initial_state, config=_config(task_id))


def resume_task(
    task_id: str,
    approved: bool,
    approved_by: str,
    reason: str = "",
    registry_base_url: str | None = None,
    db_path: str = DEFAULT_DB_PATH,
    max_retries: int = MAX_RETRIES,
    llm_call: LlmCall | None = None,
) -> GraphState:
    """Resumes a task paused in approval_wait. `task_id` must match the one
    used in submit_task — that's how the checkpointer finds the paused state,
    even in a brand-new process."""
    app = build_graph(registry_base_url, db_path, max_retries, llm_call)
    decision = {"approved": approved, "approved_by": approved_by, "reason": reason}
    return app.invoke(Command(resume=decision), config=_config(task_id))


def run_task(
    task_id: str,
    user_id: str,
    role: str,
    user_message: str,
    registry_base_url: str | None = None,
    db_path: str = DEFAULT_DB_PATH,
    max_retries: int = MAX_RETRIES,
    llm_call: LlmCall | None = None,
) -> GraphState:
    """Phase 1 convenience alias, kept for backward compatibility."""
    return submit_task(
        task_id, user_id, role, user_message, registry_base_url, db_path, max_retries, llm_call
    )


def get_task_state(task_id: str, db_path: str = DEFAULT_DB_PATH) -> GraphState | None:
    """The saved state of a task (status, history, final response...), read
    from the checkpoint database. Works for finished and paused tasks alike."""
    app = build_graph(None, db_path)
    snapshot = app.get_state({"configurable": {"thread_id": task_id}})
    return dict(snapshot.values) if snapshot and snapshot.values else None
