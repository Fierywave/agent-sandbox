"""Track B Phase 3 tests: clarification, multi-tool tasks, bounded retry.

All offline (no Track A, no API key). The model path is exercised with a
FakeLLM injected through `llm_call`, so what the planner does with a
model's reply — validation, registry overrides, retries — is tested for
real without network access.
"""

import json
import os

import pytest

import approval_store as local_store
import graph
from agent_types import MAX_PLAN_STEPS, TransientToolError
from contracts import STARTER_TOOLS, ToolCall, WorkflowStatus
from graph import resume_task, submit_task
from planner import Planner
from steps import UnresolvedReferenceError, check_single_result, resolve_arguments

os.environ.pop("ANTHROPIC_API_KEY", None)

OPERATOR_TICKET_FOR_C003 = "look up customer C003 and create a ticket about them"


@pytest.fixture(autouse=True)
def isolate(tmp_path, monkeypatch):
    monkeypatch.setattr(local_store, "_STORE_PATH", tmp_path / "pending.json")


def run(tmp_path, message, role="analyst", task="t1", **kw):
    return submit_task(task, "user1", role, message, db_path=str(tmp_path / "p3.db"), **kw)


def node_count(result, name):
    return sum(1 for h in result["history"] if h["node"] == name)


class Flaky:
    """Stands in for a tool: raises `exc` for the first `fail_times` calls."""

    def __init__(self, fail_times, exc, real=None):
        self.fail_times, self.exc, self.real, self.calls = fail_times, exc, real, 0

    def __call__(self, **kwargs):
        self.calls += 1
        if self.calls <= self.fail_times:
            raise self.exc
        return self.real(**kwargs) if self.real else {"result": 0}


class FakeLLM:
    """A scripted model. `plan` / `replan` may each be a string, a list of
    strings (used in order) or a function of the user message."""

    def __init__(self, plan=None, replan=None):
        self.script = {"plan": plan, "replan": replan}
        self.calls = []

    def __call__(self, system, user):
        kind = "replan" if "FAILED" in system else "plan"
        self.calls.append((kind, user))
        reply = self.script[kind]
        if isinstance(reply, list):
            reply = reply.pop(0)
        return reply(user) if callable(reply) else reply


def plan_json(*steps, clarification=None, answer=None):
    return json.dumps(
        {"steps": list(steps), "clarification_question": clarification,
         "final_answer_if_no_tool_needed": answer}
    )


def step(tool, args, single=False, **extra):
    return {"tool_name": tool, "arguments": args, "reasoning": "r",
            "expects_single_result": single, **extra}


# =============================================================================
# placeholders (steps.py)
# =============================================================================

RESULTS = [{"step": 0, "tool_name": "csv_query", "arguments": {},
            "result": {"rows": [{"customer_id": "C003", "name": "Chen Wei", "plan": "pro"}], "count": 1}}]


def test_placeholder_that_is_the_whole_value_keeps_its_type():
    assert resolve_arguments({"n": "{{0.count}}"}, RESULTS) == {"n": 1}


def test_placeholders_inside_text_and_nested_values():
    out = resolve_arguments(
        {"title": "Follow up with {{0.rows.0.name}}", "x": {"k": ["{{0.rows.0.plan}}"]}}, RESULTS
    )
    assert out == {"title": "Follow up with Chen Wei", "x": {"k": ["pro"]}}


@pytest.mark.parametrize("bad", ["{{0.rows.5.name}}", "{{3.rows.0.name}}", "{{0.nope}}"])
def test_placeholder_pointing_at_nothing_raises(bad):
    with pytest.raises(UnresolvedReferenceError):
        resolve_arguments({"t": bad}, RESULTS)


def test_data_returned_by_a_tool_is_never_treated_as_a_placeholder():
    sneaky = [{"step": 0, "tool_name": "x", "arguments": {},
               "result": {"rows": [{"name": "{{0.rows.0.secret}}", "secret": "LEAK"}]}}]
    assert resolve_arguments({"t": "hi {{0.rows.0.name}}"}, sneaky) == {"t": "hi {{0.rows.0.secret}}"}


def test_check_single_result_one_row_is_fine():
    assert check_single_result({"filter": {}}, {"rows": [{"customer_id": "C1"}]}) is None
    assert check_single_result({}, {"result": 5}) is None  # not a row-returning tool


def test_check_single_result_zero_rows_asks_to_recheck():
    assert "couldn't find" in check_single_result({"filter": {"plan": "x"}}, {"rows": []})


def test_check_single_result_several_rows_lists_candidates_and_caps_them():
    rows = [{"customer_id": f"C00{i}", "name": f"N{i}"} for i in range(1, 8)]
    q = check_single_result({"filter": {"plan": "pro"}}, {"rows": rows})
    assert "7 records" in q and "C001 N1" in q and "C005 N5" in q
    assert "C006" not in q and "and 2 more" in q


# =============================================================================
# planner, rule-based
# =============================================================================

def offline_plan(message):
    return Planner(STARTER_TOOLS).plan("t", message, "operator")


def test_lookup_by_id_is_a_single_record_lookup():
    p = offline_plan("look up customer C003")
    assert p.steps[0].arguments == {"dataset": "customers", "filter": {"customer_id": "C003"}}
    assert p.single_result_steps == [0]


def test_lookup_by_description_builds_a_filter():
    p = offline_plan("look up the enterprise customer")
    assert p.steps[0].arguments["filter"] == {"plan": "enterprise"}
    assert p.single_result_steps == [0]


def test_asking_for_a_list_is_not_treated_as_single_record():
    p = offline_plan("list all pro customers")
    assert p.steps[0].arguments["filter"] == {"plan": "pro"}
    assert p.single_result_steps == []


def test_lookup_with_nothing_to_go_on_asks_instead_of_dumping_every_row():
    p = offline_plan("look up customer")
    assert p.steps == [] and "Which customer" in p.clarification_question


def test_lookup_plus_ticket_becomes_two_steps_with_placeholders():
    p = offline_plan(OPERATOR_TICKET_FOR_C003)
    assert [s.tool_name for s in p.steps] == ["csv_query", "create_ticket"]
    assert "{{0.rows.0.name}}" in p.steps[1].arguments["title"]
    assert p.single_result_steps == [0]


def test_lookup_plus_ticket_with_no_customer_named_asks_first():
    p = offline_plan("look up the customer and create a ticket")
    assert p.steps == [] and p.clarification_question


def test_explicit_ticket_title_wins_over_the_default():
    p = offline_plan("look up customer C003 and create a ticket titled 'Renewal' ")
    assert p.steps[1].arguments["title"] == "Renewal"


def test_open_a_ticket_is_a_ticket_not_a_file_read():
    # "open" used to send this to the file reader
    p = offline_plan("open a ticket titled 'x' with description 'y'")
    assert [s.tool_name for s in p.steps] == ["create_ticket"]


# =============================================================================
# planner, model path (FakeLLM)
# =============================================================================

def llm_planner(llm):
    return Planner(STARTER_TOOLS, llm_call=llm)


def test_model_reply_with_several_steps_and_a_single_record_flag_is_parsed():
    llm = FakeLLM(plan=plan_json(
        step("csv_query", {"dataset": "customers", "filter": {"customer_id": "C003"}}, single=True),
        step("create_ticket", {"title": "{{0.rows.0.name}}", "description": "d"}),
    ))
    p = llm_planner(llm).plan("t", "whatever", "operator")
    assert [s.tool_name for s in p.steps] == ["csv_query", "create_ticket"]
    assert p.single_result_steps == [0]


def test_model_cannot_lower_a_tools_risk_it_is_overwritten_from_the_registry():
    llm = FakeLLM(plan=plan_json(
        step("create_ticket", {"title": "t", "description": "d"}, risk_level="low", requires_approval=False)
    ))
    call = llm_planner(llm).plan("t", "x", "viewer").steps[0]
    assert call.risk_level.value == "high" and call.requires_approval is True


def test_unknown_tool_from_the_model_is_assumed_high_risk():
    llm = FakeLLM(plan=plan_json(step("delete_everything", {})))
    call = llm_planner(llm).plan("t", "x", "viewer").steps[0]
    assert call.risk_level.value == "high" and call.requires_approval is True


def test_a_bad_first_reply_gets_exactly_one_retry():
    llm = FakeLLM(plan=["this is not json", plan_json(step("calculator", {"expression": "1+1"}))])
    p = llm_planner(llm).plan("t", "x", "viewer")
    assert p.steps[0].tool_name == "calculator" and len(llm.calls) == 2


def test_two_bad_replies_fall_back_to_rule_based_planning():
    llm = FakeLLM(plan=["nope", "still nope"])
    p = llm_planner(llm).plan("t", "what is 6 * 7", "viewer")
    assert p.steps[0].tool_name == "calculator" and len(llm.calls) == 2


def test_a_plan_over_the_step_limit_is_rejected_like_a_bad_reply():
    too_many = plan_json(*[step("calculator", {"expression": "1+1"}) for _ in range(MAX_PLAN_STEPS + 1)])
    llm = FakeLLM(plan=[too_many, too_many])
    p = llm_planner(llm).plan("t", "what is 6 * 7", "viewer")
    assert len(p.steps) == 1  # rule-based fallback, not the 6-step plan


def test_model_api_errors_do_not_crash_planning():
    def boom(system, user):
        raise RuntimeError("API down")
    assert llm_planner(boom).plan("t", "what is 6 * 7", "viewer").steps[0].tool_name == "calculator"


def test_model_can_ask_a_clarifying_question():
    llm = FakeLLM(plan=plan_json(clarification="Which customer?"))
    assert llm_planner(llm).plan("t", "look someone up", "viewer").clarification_question == "Which customer?"


# =============================================================================
# graph: multi-tool tasks
# =============================================================================

def test_two_step_task_passes_the_customer_into_the_ticket_and_pauses_for_approval(tmp_path):
    paused = run(tmp_path, OPERATOR_TICKET_FOR_C003, role="operator")
    assert "__interrupt__" in paused
    assert paused["status"] == WorkflowStatus.WAITING_APPROVAL.value

    # the call being approved holds the REAL name, not a placeholder
    assert paused["tool_call"]["arguments"]["title"] == "Follow up with Chen Wei"
    assert local_store.get_pending("t1")["tool_call"]["arguments"]["title"] == "Follow up with Chen Wei"
    assert [r["tool_name"] for r in paused["step_results"]] == ["csv_query"]

    done = resume_task("t1", approved=True, approved_by="boss", db_path=str(tmp_path / "p3.db"))
    assert done["status"] == WorkflowStatus.COMPLETED.value
    assert done["tool_result"]["title"] == "Follow up with Chen Wei"
    assert "Completed 2 steps" in done["final_response"]
    assert [h["step"] for h in done["history"] if h["node"] == "tool_execution"] == [0, 1]


def test_second_step_is_denied_by_the_same_permission_check_as_any_other_call(tmp_path):
    result = run(tmp_path, OPERATOR_TICKET_FOR_C003, role="analyst")  # analysts may query, not file tickets
    assert result["status"] == WorkflowStatus.REJECTED.value
    assert "step 2 of 2" in result["final_response"]
    assert [r["tool_name"] for r in result["step_results"]] == ["csv_query"]  # step 1 had run
    assert "__interrupt__" not in result


def test_a_placeholder_that_cannot_be_filled_fails_cleanly_before_anything_is_approved(tmp_path):
    llm = FakeLLM(plan=plan_json(
        step("csv_query", {"dataset": "customers", "filter": {"plan": "free"}}),
        step("create_ticket", {"title": "{{0.rows.7.name}}", "description": "d"}),
    ))
    result = run(tmp_path, "anything", role="operator", llm_call=llm)
    assert result["status"] == WorkflowStatus.FAILED.value
    assert "step 2 of 2" in result["final_response"]
    assert "__interrupt__" not in result and local_store.list_pending() == []


# =============================================================================
# graph: clarification instead of guessing
# =============================================================================

def test_several_matches_for_a_single_record_lookup_produces_a_question(tmp_path):
    result = run(tmp_path, "look up the enterprise customer")
    assert result["status"] == WorkflowStatus.NEEDS_CLARIFICATION.value
    assert result["clarification_question"] == result["final_response"]
    assert "C004 Dana Osei" in result["final_response"] and "C007 Grace Kim" in result["final_response"]


def test_asking_for_a_list_of_the_same_customers_is_not_ambiguous(tmp_path):
    result = run(tmp_path, "list all enterprise customers")
    assert result["status"] == WorkflowStatus.COMPLETED.value
    assert len(result["tool_result"]["rows"]) == 2


def test_no_match_asks_the_user_to_recheck(tmp_path):
    result = run(tmp_path, "look up the enterprise cancelled customer")
    assert result["status"] == WorkflowStatus.NEEDS_CLARIFICATION.value
    assert "couldn't find" in result["final_response"]


def test_vague_request_asks_before_running_any_tool(tmp_path):
    result = run(tmp_path, "look up customer")
    assert result["status"] == WorkflowStatus.NEEDS_CLARIFICATION.value
    assert node_count(result, "tool_execution") == 0


def test_ambiguity_in_step_one_stops_the_ticket_from_ever_being_requested(tmp_path):
    result = run(tmp_path, "look up the pro customer and create a ticket about them", role="operator")
    assert result["status"] == WorkflowStatus.NEEDS_CLARIFICATION.value
    assert "__interrupt__" not in result and local_store.list_pending() == []
    assert [r["tool_name"] for r in result["step_results"]] == ["csv_query"]
    assert node_count(result, "tool_execution") == 1  # only the lookup ever ran


# =============================================================================
# graph: bounded retry
# =============================================================================

def test_a_temporary_failure_is_retried_once_and_then_succeeds(tmp_path, monkeypatch):
    from tools import calculator
    flaky = Flaky(1, TransientToolError("glitch"), real=calculator.run)
    monkeypatch.setitem(graph.TOOL_IMPLEMENTATIONS, "calculator", flaky)

    result = run(tmp_path, "what is 6 * 7")
    assert result["status"] == WorkflowStatus.COMPLETED.value
    assert result["tool_result"]["result"] == 42
    assert flaky.calls == 2
    assert node_count(result, "replan") == 1
    assert node_count(result, "permission_check") == 2  # the retry was permission-checked again


def test_a_tool_that_keeps_failing_is_tried_exactly_twice_then_fails_visibly(tmp_path, monkeypatch):
    flaky = Flaky(99, TransientToolError("still down"))
    monkeypatch.setitem(graph.TOOL_IMPLEMENTATIONS, "calculator", flaky)

    result = run(tmp_path, "what is 6 * 7")
    assert flaky.calls == 2
    assert result["status"] == WorkflowStatus.FAILED.value
    assert "after 2 attempts" in result["final_response"] and "still down" in result["final_response"]


def test_an_error_that_retrying_cannot_fix_is_not_retried_blindly(tmp_path, monkeypatch):
    flaky = Flaky(99, ValueError("bad input"))
    monkeypatch.setitem(graph.TOOL_IMPLEMENTATIONS, "calculator", flaky)

    result = run(tmp_path, "what is 6 * 7")
    assert flaky.calls == 1
    assert result["status"] == WorkflowStatus.FAILED.value
    assert "not retried" in result["final_response"]


@pytest.mark.parametrize("max_retries, expected_calls", [(0, 1), (1, 2), (2, 3)])
def test_the_retry_cap_is_respected(tmp_path, monkeypatch, max_retries, expected_calls):
    flaky = Flaky(99, TransientToolError("down"))
    monkeypatch.setitem(graph.TOOL_IMPLEMENTATIONS, "calculator", flaky)
    run(tmp_path, "what is 6 * 7", max_retries=max_retries)
    assert flaky.calls == expected_calls


def test_a_high_risk_tool_is_never_retried_automatically(tmp_path, monkeypatch):
    flaky = Flaky(99, TransientToolError("timed out"))
    monkeypatch.setitem(graph.TOOL_IMPLEMENTATIONS, "create_ticket", flaky)

    run(tmp_path, "create a ticket titled 'x' with description 'y'", role="operator")
    done = resume_task("t1", approved=True, approved_by="boss", db_path=str(tmp_path / "p3.db"))
    assert flaky.calls == 1
    assert done["status"] == WorkflowStatus.FAILED.value
    assert "not retried automatically" in done["final_response"]


def test_the_planner_gets_the_error_and_can_revise_the_step(tmp_path):
    llm = FakeLLM(
        plan=plan_json(step("file_reader", {"path": "nope.md"})),
        replan=plan_json(step("file_reader", {"path": "onboarding.md"})),
    )
    result = run(tmp_path, "read the doc", llm_call=llm)

    assert result["status"] == WorkflowStatus.COMPLETED.value
    assert "Onboarding Guide" in result["tool_result"]["content"]
    kinds = [k for k, _ in llm.calls]
    assert kinds == ["plan", "replan"]
    assert "nope.md" in llm.calls[1][1]  # the failed call and its error were in the prompt


def test_on_failure_the_planner_may_answer_with_a_question_instead(tmp_path):
    llm = FakeLLM(
        plan=plan_json(step("file_reader", {"path": "nope.md"})),
        replan=plan_json(clarification="Which document did you mean?"),
    )
    result = run(tmp_path, "read the doc", llm_call=llm)
    assert result["status"] == WorkflowStatus.NEEDS_CLARIFICATION.value
    assert result["final_response"] == "Which document did you mean?"


def test_a_worst_case_plan_with_every_step_retried_still_finishes(tmp_path, monkeypatch):
    """5 steps, each failing once: ~30 node runs, more than LangGraph's
    default limit of 25 — this is what RECURSION_LIMIT is for."""
    from tools import calculator
    expressions = [f"{i}+{i}" for i in range(1, MAX_PLAN_STEPS + 1)]
    seen = set()

    def fail_once_per_expression(**kwargs):
        if kwargs["expression"] not in seen:
            seen.add(kwargs["expression"])
            raise TransientToolError("blip")
        return calculator.run(**kwargs)

    def replan(user):
        failed = json.loads(user)["failed_step"]["arguments"]["expression"]
        tail = expressions[expressions.index(failed):]
        return plan_json(*[step("calculator", {"expression": e}) for e in tail])

    monkeypatch.setitem(graph.TOOL_IMPLEMENTATIONS, "calculator", fail_once_per_expression)
    llm = FakeLLM(plan=plan_json(*[step("calculator", {"expression": e}) for e in expressions]), replan=replan)

    result = run(tmp_path, "do the sums", llm_call=llm)
    assert result["status"] == WorkflowStatus.COMPLETED.value
    assert len(result["step_results"]) == MAX_PLAN_STEPS
    assert node_count(result, "replan") == MAX_PLAN_STEPS
