"""Phase 4 (Track B) backend tests: trace metadata, measured timing, and
automatic trace publishing to Track A's real /trace API (a live uvicorn
server on a free port, as in test_track_b_integration.py)."""

import importlib
import socket
import threading
import time

import pytest
import requests

import approval_store as local_store
import graph
import trace_publisher
from agent_types import TransientToolError
from approval_sync import sync_approvals
from contracts import WorkflowStatus
from graph import resume_task, submit_task
from guardrail_client import get_token
from trace_publisher import fetch_trace, publish_trace

TICKET = "create a ticket titled 'printer broken' with description 'jammed'"


def _a(name):
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
        pytest.fail("Track A did not start")
    yield url
    server.should_exit = True
    thread.join(timeout=5)


@pytest.fixture(autouse=True)
def clean_state(tmp_path, monkeypatch):
    monkeypatch.setattr(local_store, "_STORE_PATH", tmp_path / "pending.json")
    for reset in (_a("rate_limit").rate_limiter.reset, _a("approvals").approval_store.clear,
                  _a("audit").audit_logger.clear, _a("tracing").trace_store.clear):
        reset()


@pytest.fixture
def publishes(monkeypatch):
    """Records the result of every trace publish the graph makes."""
    results, real = [], graph.publish_trace

    def spy(*args, **kwargs):
        results.append(real(*args, **kwargs))
        return results[-1]

    monkeypatch.setattr(graph, "publish_trace", spy)
    return results


def db(tmp_path):
    return str(tmp_path / "p4.db")


def live_trace(url, user, role, task_id):
    return fetch_trace(url, get_token(url, user, role), task_id)


def nodes(spans):
    return [s["node"] for s in spans]


def approve(url, approval_id):
    token = get_token(url, "reviewer", "operator")
    return requests.post(f"{url}/approvals/{approval_id}/approve",
                         headers={"Authorization": f"Bearer {token}"}, json={"note": "ok"}, timeout=5)


# =============================================================================
# metadata and timing (offline)
# =============================================================================

def test_every_history_entry_has_measured_latency_and_tool_runs_name_their_tool(tmp_path):
    r = submit_task("m1", "u", "analyst", "what is 6 * 7", db_path=db(tmp_path))
    for entry in r["history"]:
        assert isinstance(entry["latency_ms"], float) and entry["latency_ms"] >= 0, entry["node"]
    run = next(h for h in r["history"] if h["node"] == "tool_execution")
    assert run["tool_name"] == "calculator" and "6" in run["input"]["expression"]
    assert run["output"]["result"] == 42


def test_a_slow_node_gets_its_real_time_recorded(tmp_path, monkeypatch):
    def slow(**kwargs):
        time.sleep(0.08)
        return {"result": 1}

    monkeypatch.setitem(graph.TOOL_IMPLEMENTATIONS, "calculator", slow)
    r = submit_task("m2", "u", "analyst", "what is 6 * 7", db_path=db(tmp_path))
    run = next(h for h in r["history"] if h["node"] == "tool_execution")
    assert run["latency_ms"] >= 70


def test_time_spent_waiting_for_a_person_is_reported_separately_from_node_time(tmp_path):
    submit_task("m3", "u", "operator", TICKET, db_path=db(tmp_path))
    time.sleep(0.25)
    done = resume_task("m3", approved=True, approved_by="boss", db_path=db(tmp_path))
    wait = next(h for h in done["history"] if h["node"] == "approval_wait")
    assert wait["waited_ms"] >= 240
    assert wait["latency_ms"] < 100  # the node itself ran for a moment, not for the wait


def test_retries_and_denials_carry_their_details(tmp_path, monkeypatch):
    monkeypatch.setitem(graph.TOOL_IMPLEMENTATIONS, "calculator",
                        lambda **kw: (_ for _ in ()).throw(TransientToolError("glitch")))
    r = submit_task("m4", "u", "analyst", "what is 6 * 7", db_path=db(tmp_path))
    first = next(h for h in r["history"] if h["node"] == "tool_execution")
    assert first["will_retry"] is True and first["error"] == "glitch" and first["tool_name"] == "calculator"
    replan = next(h for h in r["history"] if h["node"] == "replan")
    assert replan["tool_name"] == "calculator" and replan["input"]["failed_step"]["tool_name"] == "calculator"


# =============================================================================
# the publisher never raises and retries only what can be fixed by retrying
# =============================================================================

class FakeResponse:
    def __init__(self, status_code, body=None):
        self.status_code, self.body, self.text = status_code, body or {}, str(body)

    def json(self):
        return self.body


@pytest.fixture
def fake_post(monkeypatch):
    monkeypatch.setattr(trace_publisher.time, "sleep", lambda s: None)

    def install(*responses):
        calls = []

        def post(url, **kwargs):
            calls.append(url)
            item = responses[min(len(calls), len(responses)) - 1]
            if isinstance(item, Exception):
                raise item
            return item

        monkeypatch.setattr(trace_publisher.requests, "post", post)
        return calls

    return install


def send():
    return publish_trace("http://x", "tok", "t1", "running", [{"node": "intake"}])


def test_publisher_success(fake_post):
    calls = fake_post(FakeResponse(200))
    assert send() == {"ok": True, "status_code": 200} and len(calls) == 1


def test_publisher_does_not_retry_a_conflict(fake_post):
    calls = fake_post(FakeResponse(409, {"detail": "A finished trace cannot be changed"}))
    result = send()
    assert result["ok"] is False and result["status_code"] == 409 and len(calls) == 1


def test_publisher_retries_a_server_error_until_it_works(fake_post):
    calls = fake_post(FakeResponse(503), FakeResponse(503), FakeResponse(200))
    assert send()["ok"] is True and len(calls) == 3


def test_publisher_gives_up_quietly_after_the_retries(fake_post):
    calls = fake_post(FakeResponse(503))
    assert send()["ok"] is False and len(calls) == 3


def test_publisher_survives_connection_errors_and_odd_failures(fake_post):
    calls = fake_post(requests.ConnectionError("down"))
    assert send()["ok"] is False and len(calls) == 3
    fake_post(TypeError("not serialisable"))
    assert send()["ok"] is False


# =============================================================================
# automatic publishing against Track A's real trace API
# =============================================================================

def test_a_finished_task_has_a_complete_published_trace_and_nothing_was_rejected(guardrail_url, tmp_path, publishes):
    r = submit_task("p-1", "alice", "analyst", "what is 6 * 7",
                    registry_base_url=guardrail_url, db_path=db(tmp_path))
    assert all(p["ok"] for p in publishes) and len(publishes) == len(r["history"])

    trace = live_trace(guardrail_url, "alice", "analyst", "p-1")
    assert trace["status"] == "completed"
    assert nodes(trace["spans"]) == nodes(r["history"])
    run = next(s for s in trace["spans"] if s["node"] == "tool_execution")
    assert run["tool_name"] == "calculator" and run["latency_ms"] is not None


def test_a_paused_task_is_visible_as_waiting_then_completes_in_the_same_trace(guardrail_url, tmp_path, publishes):
    paused = submit_task("p-2", "carol", "operator", TICKET, registry_base_url=guardrail_url, db_path=db(tmp_path))
    trace = live_trace(guardrail_url, "carol", "operator", "p-2")
    assert trace["status"] == "waiting_approval" and nodes(trace["spans"])[-1] == "permission_check"

    assert approve(guardrail_url, paused["approval_id"]).status_code == 200
    [row] = sync_approvals(guardrail_url, db(tmp_path))
    assert row["final_status"] == "completed"

    trace = live_trace(guardrail_url, "carol", "operator", "p-2")
    assert trace["status"] == "completed"
    wait = next(s for s in trace["spans"] if s["node"] == "approval_wait")
    assert wait["waited_ms"] > 0 and wait["latency_ms"] is not None
    assert all(p["ok"] for p in publishes), [p for p in publishes if not p["ok"]]


def test_a_rejected_approval_ends_as_a_rejected_trace(guardrail_url, tmp_path, publishes):
    paused = submit_task("p-3", "carol", "operator", TICKET, registry_base_url=guardrail_url, db_path=db(tmp_path))
    token = get_token(guardrail_url, "reviewer", "operator")
    requests.post(f"{guardrail_url}/approvals/{paused['approval_id']}/reject",
                  headers={"Authorization": f"Bearer {token}"}, json={"note": "no"}, timeout=5)
    sync_approvals(guardrail_url, db(tmp_path))
    assert live_trace(guardrail_url, "carol", "operator", "p-3")["status"] == "rejected"
    assert all(p["ok"] for p in publishes)


def test_a_denied_task_ends_as_a_rejected_trace(guardrail_url, tmp_path, publishes):
    submit_task("p-4", "dave", "viewer", TICKET, registry_base_url=guardrail_url, db_path=db(tmp_path))
    assert live_trace(guardrail_url, "dave", "viewer", "p-4")["status"] == "rejected"
    assert all(p["ok"] for p in publishes)


def test_a_clarification_is_published_as_needs_clarification(guardrail_url, tmp_path, publishes):
    submit_task("p-5", "erin", "analyst", "look up the enterprise customer",
                registry_base_url=guardrail_url, db_path=db(tmp_path))
    trace = live_trace(guardrail_url, "erin", "analyst", "p-5")
    assert trace["status"] == "needs_clarification"
    assert "clarification" in nodes(trace["spans"])
    assert all(p["ok"] for p in publishes)


def test_a_retry_shows_up_in_the_published_trace(guardrail_url, tmp_path, publishes, monkeypatch):
    from tools import calculator
    state = {"calls": 0}

    def flaky(**kwargs):
        state["calls"] += 1
        if state["calls"] == 1:
            raise TransientToolError("blip")
        return calculator.run(**kwargs)

    monkeypatch.setitem(graph.TOOL_IMPLEMENTATIONS, "calculator", flaky)
    submit_task("p-6", "frank", "analyst", "what is 6 * 7", registry_base_url=guardrail_url, db_path=db(tmp_path))
    trace = live_trace(guardrail_url, "frank", "analyst", "p-6")
    runs = [s for s in trace["spans"] if s["node"] == "tool_execution"]
    assert trace["status"] == "completed" and "replan" in nodes(trace["spans"])
    assert [(r["attempt"], r.get("will_retry")) for r in runs] == [(0, True), (1, None)]
    assert all(p["ok"] for p in publishes)


def test_analytics_names_the_tool_instead_of_reporting_it_as_unreported(guardrail_url, tmp_path):
    submit_task("p-7", "gina", "analyst", "what is 6 * 7", registry_base_url=guardrail_url, db_path=db(tmp_path))
    token = get_token(guardrail_url, "boss", "operator")
    summary = requests.get(f"{guardrail_url}/analytics/summary",
                           headers={"Authorization": f"Bearer {token}"}, timeout=5).json()
    tools = {row["tool_name"] for row in summary["reported_tool_executions"]["by_tool_and_role"]}
    assert "calculator" in tools and "unreported" not in tools


def test_a_failing_trace_publish_never_changes_the_task_outcome(guardrail_url, tmp_path, monkeypatch):
    def broken(*args, **kwargs):
        raise RuntimeError("telemetry exploded")

    monkeypatch.setattr(graph, "publish_trace", broken)
    r = submit_task("p-8", "hank", "analyst", "what is 6 * 7", registry_base_url=guardrail_url, db_path=db(tmp_path))
    assert r["status"] == WorkflowStatus.COMPLETED.value and r["tool_result"]["result"] == 42

    monkeypatch.setattr(graph, "publish_trace", lambda *a, **k: {"ok": False, "error": "nope"})
    r = submit_task("p-9", "hank", "analyst", "what is 6 * 7", registry_base_url=guardrail_url, db_path=db(tmp_path))
    assert r["status"] == WorkflowStatus.COMPLETED.value


def test_offline_mode_sends_no_traces(tmp_path, publishes):
    submit_task("p-10", "u", "analyst", "what is 6 * 7", db_path=db(tmp_path))
    assert publishes == []