"""Agent Sandbox UI — submit a task, watch its status, review the trace.

Run from the repo root:
    streamlit run track-b-agent/app.py

With Track A running (see its handoff) this is the full system: permission
checks, approvals and traces all go through its service. Clear the URL box in
the sidebar to run offline against local stubs.
"""

import os
import sys
import time
import uuid

_HERE = os.path.dirname(os.path.abspath(__file__))
for _p in (os.path.join(_HERE, ".."), _HERE):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import streamlit as st

from approval_sync import sync_approvals
from graph import DEFAULT_DB_PATH, get_task_state, resume_task, submit_task
from guardrail_client import GuardrailError, get_token
from trace_publisher import fetch_trace

DB = os.environ.get("AGENT_DB_PATH", DEFAULT_DB_PATH)

STATUS = {
    "running": ("🔵", "Running"),
    "waiting_approval": ("🟡", "Waiting for approval"),
    "needs_clarification": ("🟠", "Needs your answer"),
    "completed": ("🟢", "Completed"),
    "rejected": ("🔴", "Rejected"),
    "failed": ("🔴", "Failed"),
}
NODE_ICON = {
    "intake": "📥", "plan": "🧠", "tool_selection": "🧭", "resolve_references": "🔗",
    "permission_check": "🛡️", "approval_wait": "👤", "tool_execution": "🔧",
    "replan": "🔁", "clarification": "❓", "reflection": "💬", "final_response": "🏁",
}
EXAMPLES = [
    "what is 6 * 7",
    "look up customer C003 and create a ticket about them",
    "look up the enterprise customer",
    "create a ticket titled 'printer broken' with description 'jammed'",
]


# ----------------------------------------------------------------- helpers
def stages(state: dict) -> list[tuple[str, str]]:
    """The big steps of a run and how far this task got: ✅ done, ⏸️ waiting, ⬜ not yet."""
    seen = {h["node"] for h in state["history"]}
    waiting = state["status"] == "waiting_approval"
    plan = [("intake", "Received"), ("plan", "Planned"), ("permission_check", "Checked")]
    if "approval_wait" in seen or waiting:
        plan.append(("approval_wait", "Approved"))
    plan += [("tool_execution", "Ran"), ("final_response", "Answered")]
    out = []
    for node, label in plan:
        if node == "approval_wait" and waiting:
            out.append(("⏸️", label))
        else:
            out.append(("✅" if node in seen else "⬜", label))
    return out


def span_label(span: dict) -> str:
    parts = [f"{'❌' if span.get('error') else NODE_ICON.get(span['node'], '▫️')} {span['node']}"]
    if span.get("step") is not None:
        parts.append(f"step {span['step'] + 1}")
    if span.get("tool_name"):
        parts.append(span["tool_name"])
    if span.get("attempt"):
        parts.append(f"retry {span['attempt']}")
    if span.get("latency_ms") is not None:
        parts.append(f"{span['latency_ms']:.1f} ms")
    if span.get("waited_ms"):
        parts.append(f"waited {span['waited_ms'] / 1000:.1f} s for a person")
    return " · ".join(parts)


def reasoning(span: dict) -> list[str]:
    """Plain-language 'why' for a span, taken from what the system recorded."""
    out, node = span.get("output") or {}, span["node"]
    lines = []
    if node == "plan":
        for step in out.get("steps", []):
            lines.append(f"Planned **{step['tool_name']}** — {step['reasoning']}")
        if out.get("clarification_question"):
            lines.append(f"Asked instead of guessing: {out['clarification_question']}")
    elif node == "permission_check" and out:
        lines.append(f"Decision: **{out.get('decision')}** — {out.get('reason')}")
        if out.get("requires_approval"):
            lines.append("This action needs a person's approval first.")
    elif node == "approval_wait":
        decision = out.get("decision", {})
        verdict = "approved" if decision.get("approved") else "rejected"
        lines.append(f"A person {verdict} this ({decision.get('approved_by', 'unknown')}).")
    elif node == "tool_execution" and span.get("will_retry"):
        lines.append("This attempt failed; one automatic retry follows.")
    elif node == "clarification":
        lines.append(out.get("question", ""))
    return lines


def load_spans(url: str, state: dict) -> tuple[list[dict], str]:
    if url:
        try:
            trace = fetch_trace(url, get_token(url, state["user_id"], state["role"]), state["task_id"])
        except GuardrailError:
            trace = None
        if trace and trace.get("spans"):
            return trace["spans"], "Track A's trace API (GET /trace)"
    return state["history"], "the local checkpoint (offline, or Track A has no trace for this task)"


# ------------------------------------------------------------------- page
st.set_page_config(page_title="Agent Sandbox", page_icon="🛡️", layout="wide")
st.session_state.setdefault("tasks", [])
st.session_state.setdefault("current", None)
st.session_state.setdefault("message", "")

with st.sidebar:
    st.header("Settings")
    url = st.text_input(
        "Track A guardrail URL (blank = offline demo)",
        value=os.environ.get("GUARDRAIL_URL", "http://127.0.0.1:8000"),
        key="url",
    ).strip()
    user = st.text_input("User ID", value="demo-user", key="user").strip() or "demo-user"
    role = st.selectbox("Role", ["viewer", "analyst", "operator", "admin"], index=2, key="role")
    st.caption("Development identity: Track A issues tokens for any role, so the role here is what the agent acts as.")
    st.divider()
    st.subheader("Recent tasks")
    for t in st.session_state.tasks[:10]:
        if st.button(f"{t['task_id']} · {t['message'][:26]}", key=f"open-{t['task_id']}"):
            st.session_state.current = t["task_id"]

st.title("🛡️ Permissioned Agent")
st.write("Ask the agent to do something. Low-risk actions run immediately; high-risk ones wait for a person.")

st.caption("Try an example:")
for col, example in zip(st.columns(len(EXAMPLES)), EXAMPLES):
    col.button(example, key=f"ex-{example[:12]}", on_click=lambda e=example: st.session_state.update(message=e))

st.text_area("What should the agent do?", key="message", height=90)

if st.button("Submit task", type="primary", key="submit"):
    message = st.session_state.message.strip()
    if not message:
        st.warning("Type a request first.")
    else:
        task_id = f"ui-{uuid.uuid4().hex[:8]}"
        try:
            with st.spinner("The agent is working..."):
                submit_task(task_id, user, role, message, registry_base_url=url or None, db_path=DB)
        except Exception as exc:  # noqa: BLE001 - show it, don't crash the page
            st.error(f"The task could not run: {exc}")
        else:
            st.session_state.tasks.insert(0, {"task_id": task_id, "message": message})
            st.session_state.current = task_id

task_id = st.session_state.current
if task_id:
    state = get_task_state(task_id, DB)
    if state is None:
        st.warning(f"No saved task called {task_id}.")
        st.stop()

    # With Track A connected, pick up any decision a reviewer has made.
    if url and state["status"] == "waiting_approval":
        try:
            sync_approvals(url, DB)
            state = get_task_state(task_id, DB)
        except Exception:  # noqa: BLE001 - keep showing the task if Track A is briefly away
            pass

    st.divider()
    icon, label = STATUS.get(state["status"], ("⚪", state["status"]))
    st.subheader(f"{icon} {label}")
    st.caption(f"Task {task_id} · acting as {state['user_id']} ({state['role']})")
    st.write("  →  ".join(f"{mark} {name}" for mark, name in stages(state)))

    text = state.get("final_response") or ""
    status = state["status"]
    if status == "needs_clarification":
        st.warning(f"**The agent needs more detail.** {text}\n\nSubmit a new request that includes it.")
    elif status == "completed":
        st.success(text)
    elif status in ("rejected", "failed"):
        st.error(text)
    elif status == "waiting_approval":
        call = state["tool_call"]
        st.info(f"**{call['tool_name']}** wants to run with these exact arguments and needs a person's approval:")
        st.json(call["arguments"])
        if url:
            st.write(f"A reviewer (operator/admin) approves or rejects it in Track A's dashboard: {url}/dashboard")
            st.button("Check for the decision now", key="check")
            st.checkbox("Keep checking every 3 seconds", key="auto")
        else:
            st.caption("Offline demo: no Track A reviewer exists, so you can decide here.")
            left, right = st.columns(2)
            if left.button("Approve (offline demo)", key="approve_offline"):
                resume_task(task_id, True, f"{user} (offline demo)", db_path=DB)
                st.rerun()
            if right.button("Reject (offline demo)", key="reject_offline"):
                resume_task(task_id, False, f"{user} (offline demo)", "rejected in the UI", db_path=DB)
                st.rerun()

    st.divider()
    st.subheader("Trace")
    spans, source = load_spans(url, state)
    active = sum(s.get("latency_ms") or 0 for s in spans)
    st.caption(f"Source: {source}. {len(spans)} steps, {active:.1f} ms of active work.")
    for i, span in enumerate(spans):
        with st.expander(span_label(span), expanded=bool(span.get("error")) or span["node"] == "clarification"):
            for line in reasoning(span):
                st.markdown(line)
            if span.get("error"):
                st.error(span["error"])
            a, b = st.columns(2)
            if span.get("input"):
                a.caption("Input")
                a.json(span["input"])
            if span.get("output"):
                b.caption("Output")
                b.json(span["output"])

    if state["status"] == "waiting_approval" and url and st.session_state.get("auto"):
        time.sleep(3)
        st.rerun()