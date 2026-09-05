"""Phase 0 definition-of-done check: the shared contracts import cleanly
and round-trip through JSON. Run with: python -m pytest tests/ -q
"""

import datetime

from contracts import (
    AuditEvent,
    Plan,
    PermissionDecision,
    Role,
    RiskLevel,
    STARTER_TOOLS,
    ToolCall,
    ToolRegistryResponse,
    WorkflowState,
    WorkflowStatus,
)


def test_tool_registry_serializes():
    resp = ToolRegistryResponse(tools=STARTER_TOOLS)
    assert len(resp.tools) == 5
    payload = resp.model_dump_json()
    assert "calculator" in payload
    assert "create_ticket" in payload


def test_plan_round_trip():
    tc = ToolCall(
        tool_name="calculator",
        arguments={"expression": "42*17"},
        reasoning="user asked for a product",
        risk_level=RiskLevel.LOW,
        requires_approval=False,
    )
    plan = Plan(task_id="t1", steps=[tc])
    restored = Plan.model_validate_json(plan.model_dump_json())
    assert restored.steps[0].tool_name == "calculator"


def test_workflow_state_defaults():
    ws = WorkflowState(
        task_id="t1",
        user_id="u1",
        role=Role.ANALYST,
        current_node="plan",
        status=WorkflowStatus.RUNNING,
    )
    assert ws.history == []
    assert ws.pending_tool_call is None


def test_audit_event_round_trip():
    ev = AuditEvent(
        event_id="e1",
        task_id="t1",
        timestamp=datetime.datetime.now(datetime.timezone.utc),
        user_id="u1",
        role="analyst",
        tool_name="calculator",
        arguments={"expression": "42*17"},
        risk_level=RiskLevel.LOW,
        permission_decision=PermissionDecision.ALLOWED,
    )
    restored = AuditEvent.model_validate_json(ev.model_dump_json())
    assert restored.permission_decision == PermissionDecision.ALLOWED
