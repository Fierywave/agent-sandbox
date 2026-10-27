"""Drives the real Streamlit app (track-b-agent/app.py) headlessly."""

import importlib
import socket
import threading
import time
from pathlib import Path

import pytest
import requests
from streamlit.testing.v1 import AppTest

import approval_store as local_store
from guardrail_client import get_token

APP = str(Path(__file__).resolve().parent.parent / "track-b-agent" / "app.py")
TICKET = "create a ticket titled 'printer broken' with description 'jammed'"


def _a(name):
    return importlib.import_module(f"track-a-guardrail.{name}")


@pytest.fixture(autouse=True)
def isolate(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_DB_PATH", str(tmp_path / "ui.db"))
    monkeypatch.setattr(local_store, "_STORE_PATH", tmp_path / "pending.json")


@pytest.fixture(scope="module")
def guardrail_url():
    import uvicorn

    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    server = uvicorn.Server(uvicorn.Config(_a("main").app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{port}"
    for _ in range(100):
        try:
            requests.get(f"{url}/health", timeout=0.5)
            break
        except requests.RequestException:
            time.sleep(0.1)
    yield url
    server.should_exit = True
    thread.join(timeout=5)


@pytest.fixture
def reset_track_a(guardrail_url):
    for reset in (_a("rate_limit").rate_limiter.reset, _a("approvals").approval_store.clear,
                  _a("audit").audit_logger.clear, _a("tracing").trace_store.clear):
        reset()


def open_app(url="", role="operator"):
    at = AppTest.from_file(APP, default_timeout=60).run()
    at.sidebar.text_input(key="url").set_value(url)
    at.sidebar.selectbox(key="role").set_value(role)
    return at


def submit(at, message):
    at.text_area(key="message").set_value(message)
    at.button(key="submit").click()
    return at.run()


def shown(at):
    """All text the page is showing in its status areas."""
    parts = [e.value for e in at.subheader] + [e.value for e in at.success] + [e.value for e in at.error]
    parts += [e.value for e in at.warning] + [e.value for e in at.info] + [e.value for e in at.caption]
    return "\n".join(parts)


def test_page_loads_with_no_errors():
    at = open_app()
    assert not at.exception and at.title[0].value.endswith("Permissioned Agent")


def test_a_simple_task_completes_and_shows_its_trace():
    at = submit(open_app(role="analyst"), "what is 6 * 7")
    assert not at.exception
    assert "Completed" in at.subheader[0].value
    assert "42" in at.success[0].value
    labels = [e.label for e in at.expander]
    assert any("tool_execution" in l and "calculator" in l and "ms" in l for l in labels), labels
    assert "local checkpoint" in shown(at)


def test_an_ambiguous_request_shows_the_agents_question_and_not_an_answer():
    at = submit(open_app(role="analyst"), "look up the enterprise customer")
    assert "Needs your answer" in at.subheader[0].value
    assert "C004 Dana Osei" in at.warning[0].value and not at.success


def test_a_denied_task_is_shown_as_rejected():
    at = submit(open_app(role="viewer"), TICKET)
    assert "Rejected" in at.subheader[0].value and "not permitted" in at.error[0].value


def test_offline_approval_buttons_resume_the_paused_task():
    at = submit(open_app(role="operator"), "look up customer C003 and create a ticket about them")
    assert "Waiting for approval" in at.subheader[0].value
    assert any("Follow up with Chen Wei" in str(j.value) for j in at.json)  # the real name, not a placeholder
    assert any("Received" in m.value for m in at.markdown)

    at.button(key="approve_offline").click()
    at.run()
    assert "Completed" in at.subheader[0].value
    waited = [e.label for e in at.expander if e.label.startswith("👤 approval_wait")]
    assert waited and "waited" in waited[0]


def test_offline_reject_button_ends_the_task():
    at = submit(open_app(role="operator"), TICKET)
    at.button(key="reject_offline").click()
    at.run()
    assert "Rejected" in at.subheader[0].value and "rejected in the UI" in at.error[0].value


def test_empty_request_is_refused_politely():
    at = open_app()
    at.button(key="submit").click()
    at.run()
    assert at.warning and "Type a request" in at.warning[0].value


def test_live_the_ui_picks_up_a_reviewers_decision_by_itself(guardrail_url, reset_track_a):
    at = submit(open_app(url=guardrail_url, role="operator"), TICKET)
    assert "Waiting for approval" in at.subheader[0].value
    assert "Track A's dashboard" in "".join(m.value for m in at.markdown)

    approval = _a("approvals").approval_store.list()[0]
    token = get_token(guardrail_url, "reviewer", "operator")
    requests.post(f"{guardrail_url}/approvals/{approval.id}/approve",
                  headers={"Authorization": f"Bearer {token}"}, json={"note": "ok"}, timeout=5)

    at.run()  # a refresh is all it takes
    assert "Completed" in at.subheader[0].value
    assert "Track A's trace API" in shown(at)


def test_live_when_track_a_is_down_the_ui_shows_a_denial_not_a_crash():
    at = submit(open_app(url="http://127.0.0.1:1", role="analyst"), "what is 6 * 7")
    assert not at.exception
    assert "Rejected" in at.subheader[0].value and "fail closed" in at.error[0].value