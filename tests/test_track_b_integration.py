"""Day-7 integration tests: Track B's graph against Track A's REAL service.

A real uvicorn server running Track A's FastAPI app is started on a free
port for the duration of this module, and everything below talks to it
over actual HTTP — no mocks of Track A. What this proves:

  * Track B's offline permission stub agrees with Track A's real engine
    on every role x tool x argument combination (so it can't silently drift)
  * live permission checks work: allowed, denied, rate-limited (429), bad token (401)
  * the full approval loop: agent pauses -> approval filed in Track A ->
    a human approves/rejects there -> sync resumes the paused task
  * the approval request is filed exactly ONCE (not duplicated on resume)
  * an approval that doesn't match the paused call is NOT acted on
  * if Track A is unreachable, tool calls are denied (fail closed)
"""

import importlib
import itertools
import socket
import threading
import time

import pytest
import requests

import approval_store as local_store
from approval_sync import sync_approvals
from contracts import STARTER_TOOLS, ToolCall, WorkflowStatus
from contracts.enums import Role
from graph import submit_task
from guardrail_client import check_permission_live, get_token
from permission_client import check_permission_stub


def _a(name: str):
    """Import a Track A module (its folder name has a hyphen, hence importlib)."""
    return importlib.import_module(f"track-a-guardrail.{name}")


@pytest.fixture(scope="module")
def guardrail_url():
    import uvicorn

    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()

    server = uvicorn.Server(
        uvicorn.Config(_a("main").app, host="127.0.0.1", port=port, log_level="warning")
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()

    url = f"http://127.0.0.1:{port}"
    for _ in range(100):
        try:
            requests.get(f"{url}/health", timeout=0.5)
            break
        except requests.RequestException:
            time.sleep(0.1)
    else:
        pytest.fail("Track A guardrail server did not start")

    yield url
    server.should_exit = True
    thread.join(timeout=5)


@pytest.fixture(autouse=True)
def clean_state(tmp_path, monkeypatch):
    """Fresh Track A in-memory state and an isolated Track B pending file per test."""
    monkeypatch.setattr(local_store, "_STORE_PATH", tmp_path / "pending.json")
    for reset in (_a("rate_limit").rate_limiter.reset,
                  _a("approvals").approval_store.clear,
                  _a("audit").audit_logger.clear):
        reset()
    yield


def _db(tmp_path) -> str:
    return str(tmp_path / "checkpoints.db")


def _reviewer_call(url: str, approval_id: str, action: str, role: str = "operator", note: str = ""):
    token = get_token(url, f"reviewer-{role}", role)
    return requests.post(
        f"{url}/approvals/{approval_id}/{action}",
        headers={"Authorization": f"Bearer {token}"},
        json={"note": note},
        timeout=5,
    )


TICKET_REQUEST = "create a ticket titled 'printer broken' with description 'jammed'"


# --- stub vs Track A's real engine ------------------------------------------

def _valid_args(tool) -> dict:
    sample = {"string": "x", "object": {}, "array": [], "number": 1}
    props = tool.input_schema.get("properties", {})
    return {k: sample.get(props[k].get("type"), "x") for k in tool.input_schema.get("required", [])}


def test_offline_stub_agrees_with_track_a_engine_on_every_combination():
    evaluate = _a("permissions").evaluate_permission
    limiter = _a("rate_limit").rate_limiter
    tools_by_name = {t.name: t for t in STARTER_TOOLS}

    mismatches = []
    for role, tool in itertools.product(Role, STARTER_TOOLS):
        variants = {
            "valid": _valid_args(tool),
            "missing_required": {},
            "unexpected_arg": {**_valid_args(tool), "bogus": 1},
        }
        for label, args in variants.items():
            call = ToolCall(
                tool_name=tool.name, arguments=args, reasoning="parity",
                risk_level=tool.risk_level, requires_approval=tool.requires_approval,
            )
            limiter.reset()
            real = evaluate(role, call, user_id="parity-user")
            mine = check_permission_stub(call, role.value, tools_by_name)
            if (real.decision.value, real.requires_approval) != (mine["decision"], mine["requires_approval"]):
                mismatches.append((role.value, tool.name, label, real.decision.value, mine["decision"]))

    assert mismatches == [], f"stub disagrees with Track A's engine: {mismatches}"


# --- live permission checks --------------------------------------------------

def test_live_low_risk_task_completes_and_is_audited(guardrail_url, tmp_path):
    result = submit_task("live-1", "alice", "analyst", "what is 6 * 7",
                         registry_base_url=guardrail_url, db_path=_db(tmp_path))
    assert result["status"] == WorkflowStatus.COMPLETED.value
    assert result["tool_result"]["result"] == 42

    events = _a("audit").audit_logger.query(user_id="alice")
    assert [(e.tool_name, e.permission_decision.value) for e in events] == [("calculator", "allowed")]


def test_live_viewer_denied_by_real_engine(guardrail_url, tmp_path):
    result = submit_task("live-2", "dave", "viewer", TICKET_REQUEST,
                         registry_base_url=guardrail_url, db_path=_db(tmp_path))
    assert result["status"] == WorkflowStatus.REJECTED.value
    assert "not permitted" in result["final_response"]
    assert _a("approvals").approval_store.list() == []  # never reached approval

    events = _a("audit").audit_logger.query(user_id="dave")
    assert [e.permission_decision.value for e in events] == ["denied"]


def test_live_rate_limit_429_is_translated(guardrail_url):
    tool = next(t for t in STARTER_TOOLS if t.name == "calculator")
    call = ToolCall(tool_name="calculator", arguments={"expression": "1+1"}, reasoning="x",
                    risk_level=tool.risk_level, requires_approval=False)
    token = get_token(guardrail_url, "spammer", "viewer")
    limit = tool.rate_limit_per_minute[Role.VIEWER]

    decisions = [check_permission_live(guardrail_url, token, call, "viewer")["decision"]
                 for _ in range(limit + 1)]
    assert decisions[:limit] == ["allowed"] * limit
    assert decisions[limit] == "rate_limited"


def test_live_bad_token_is_translated_to_denied(guardrail_url):
    tool = next(t for t in STARTER_TOOLS if t.name == "calculator")
    call = ToolCall(tool_name="calculator", arguments={"expression": "1+1"}, reasoning="x",
                    risk_level=tool.risk_level, requires_approval=False)
    result = check_permission_live(guardrail_url, "not-a-real-token", call, "viewer")
    assert result["decision"] == "denied"


def test_unreachable_guardrail_fails_closed(tmp_path):
    result = submit_task("down-1", "alice", "analyst", "what is 6 * 7",
                         registry_base_url="http://127.0.0.1:1", db_path=_db(tmp_path))
    assert result["status"] == WorkflowStatus.REJECTED.value
    assert "fail closed" in result["final_response"]
    assert result.get("tool_result") is None  # the tool never ran


# --- the full approval loop --------------------------------------------------

def test_approval_loop_approve(guardrail_url, tmp_path):
    paused = submit_task("loop-1", "carol", "operator", TICKET_REQUEST,
                         registry_base_url=guardrail_url, db_path=_db(tmp_path))
    assert paused["status"] == WorkflowStatus.WAITING_APPROVAL.value

    records = _a("approvals").approval_store.list()
    assert len(records) == 1 and records[0].task_id == "loop-1"
    approval_id = records[0].id
    assert local_store.get_pending("loop-1")["approval_id"] == approval_id

    # nobody has reviewed yet: sync must not resume anything
    assert [r["outcome"] for r in sync_approvals(guardrail_url, _db(tmp_path))] == ["still_pending"]

    assert _reviewer_call(guardrail_url, approval_id, "approve", note="ok").status_code == 200

    results = sync_approvals(guardrail_url, _db(tmp_path))
    assert [(r["outcome"], r["final_status"]) for r in results] == [("approved", "completed")]

    assert local_store.list_pending() == []
    assert len(_a("approvals").approval_store.list()) == 1, "approval was filed more than once"
    assert sync_approvals(guardrail_url, _db(tmp_path)) == []  # nothing left to do


def test_approval_loop_reject(guardrail_url, tmp_path):
    submit_task("loop-2", "carol", "operator", TICKET_REQUEST,
                registry_base_url=guardrail_url, db_path=_db(tmp_path))
    approval_id = _a("approvals").approval_store.list()[0].id

    assert _reviewer_call(guardrail_url, approval_id, "reject", role="admin", note="not urgent").status_code == 200

    [row] = sync_approvals(guardrail_url, _db(tmp_path))
    assert (row["outcome"], row["final_status"]) == ("rejected", "rejected")
    assert "not urgent" in row["final_response"]


def test_analyst_cannot_approve_so_task_stays_paused(guardrail_url, tmp_path):
    submit_task("loop-3", "carol", "operator", TICKET_REQUEST,
                registry_base_url=guardrail_url, db_path=_db(tmp_path))
    approval_id = _a("approvals").approval_store.list()[0].id

    assert _reviewer_call(guardrail_url, approval_id, "approve", role="analyst").status_code == 403
    assert [r["outcome"] for r in sync_approvals(guardrail_url, _db(tmp_path))] == ["still_pending"]
    assert local_store.get_pending("loop-3") is not None


def test_mismatched_approval_is_not_acted_on(guardrail_url, tmp_path):
    submit_task("loop-4", "carol", "operator", TICKET_REQUEST,
                registry_base_url=guardrail_url, db_path=_db(tmp_path))
    record = _a("approvals").approval_store.list()[0]
    assert _reviewer_call(guardrail_url, record.id, "approve").status_code == 200

    # Simulate the approved record no longer describing what we paused on.
    record.tool_call.arguments["title"] = "something else entirely"

    [row] = sync_approvals(guardrail_url, _db(tmp_path))
    assert row["outcome"] == "mismatch"
    assert local_store.get_pending("loop-4") is not None  # still paused, tool never ran
