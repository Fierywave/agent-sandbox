import importlib
import pytest
from fastapi import Depends, FastAPI, HTTPException
from fastapi.testclient import TestClient

from contracts.enums import PermissionDecision, RiskLevel, Role
from contracts.plan import ToolCall
from contracts.tool_registry import ToolRegistryResponse

auth = importlib.import_module("track-a-guardrail.auth")
permissions = importlib.import_module("track-a-guardrail.permissions")
main = importlib.import_module("track-a-guardrail.main")

client = TestClient(main.app)


def test_create_and_decode_token():
    token = auth.create_token(user_id="alice", role=Role.ANALYST)
    claims = auth.decode_token(token)
    assert claims.sub == "alice"
    assert claims.role == Role.ANALYST


def test_issue_test_tokens():
    tokens = auth.issue_test_tokens()
    assert len(tokens) == 4
    for role in Role:
        claims = auth.decode_token(tokens[role.value])
        assert claims.role == role


def test_invalid_token_rejected():
    with pytest.raises(HTTPException) as exc_info:
        auth.decode_token("invalid.token.here")
    assert exc_info.value.status_code == 401


def test_require_role_hierarchy():
    test_app = FastAPI()

    @test_app.get("/operator-area")
    def operator_area(claims: auth.TokenClaims = Depends(auth.require_role(Role.OPERATOR))):
        return {"ok": True}

    app_client = TestClient(test_app)
    tokens = auth.issue_test_tokens()

    resp_viewer = app_client.get("/operator-area", headers={"Authorization": f"Bearer {tokens['viewer']}"})
    assert resp_viewer.status_code == 403

    resp_analyst = app_client.get("/operator-area", headers={"Authorization": f"Bearer {tokens['analyst']}"})
    assert resp_analyst.status_code == 403

    resp_op = app_client.get("/operator-area", headers={"Authorization": f"Bearer {tokens['operator']}"})
    assert resp_op.status_code == 200

    resp_admin = app_client.get("/operator-area", headers={"Authorization": f"Bearer {tokens['admin']}"})
    assert resp_admin.status_code == 200


def test_auth_me_endpoint():
    tokens = auth.issue_test_tokens()
    resp = client.get("/auth/me", headers={"Authorization": f"Bearer {tokens['operator']}"})
    assert resp.status_code == 200
    assert resp.json()["role"] == "operator"


def test_get_tools_matches_contract():
    resp = client.get("/tools")
    assert resp.status_code == 200
    validated = ToolRegistryResponse.model_validate(resp.json())
    assert len(validated.tools) == 5
    names = {t.name for t in validated.tools}
    assert {"calculator", "file_reader", "mock_search", "csv_query", "create_ticket"}.issubset(names)


def test_get_tool_by_name():
    resp = client.get("/tools/calculator")
    assert resp.status_code == 200
    assert resp.json()["name"] == "calculator"

    resp_404 = client.get("/tools/unknown_tool")
    assert resp_404.status_code == 404


def test_permissions_low_risk_all_roles():
    call = ToolCall(
        tool_name="calculator",
        arguments={"expression": "10 * 10"},
        reasoning="Multiplication",
        risk_level=RiskLevel.LOW,
        requires_approval=False,
    )
    for role in Role:
        res = permissions.evaluate_permission(user_role=role, tool_call=call)
        assert res.decision == PermissionDecision.ALLOWED
        assert res.requires_approval is False


def test_permissions_medium_risk_rbac():
    call = ToolCall(
        tool_name="csv_query",
        arguments={"dataset": "users", "filter": {"status": "active"}},
        reasoning="Fetch active users",
        risk_level=RiskLevel.MEDIUM,
        requires_approval=False,
    )
    viewer_res = permissions.evaluate_permission(user_role=Role.VIEWER, tool_call=call)
    assert viewer_res.decision == PermissionDecision.DENIED

    analyst_res = permissions.evaluate_permission(user_role=Role.ANALYST, tool_call=call)
    assert analyst_res.decision == PermissionDecision.ALLOWED
    assert analyst_res.requires_approval is False


def test_permissions_high_risk_rbac_and_approval():
    call = ToolCall(
        tool_name="create_ticket",
        arguments={"title": "Bug", "description": "Crash on launch"},
        reasoning="Log ticket",
        risk_level=RiskLevel.HIGH,
        requires_approval=True,
    )
    assert permissions.evaluate_permission(user_role=Role.VIEWER, tool_call=call).decision == PermissionDecision.DENIED
    assert permissions.evaluate_permission(user_role=Role.ANALYST, tool_call=call).decision == PermissionDecision.DENIED

    op_res = permissions.evaluate_permission(user_role=Role.OPERATOR, tool_call=call)
    assert op_res.decision == PermissionDecision.ALLOWED
    assert op_res.requires_approval is True

    admin_res = permissions.evaluate_permission(user_role=Role.ADMIN, tool_call=call)
    assert admin_res.decision == PermissionDecision.ALLOWED
    assert admin_res.requires_approval is True


def test_permissions_argument_validation():
    invalid_call = ToolCall(
        tool_name="create_ticket",
        arguments={"title": "Only Title"},
        reasoning="Missing description",
        risk_level=RiskLevel.HIGH,
        requires_approval=True,
    )
    res = permissions.evaluate_permission(user_role=Role.ADMIN, tool_call=invalid_call)
    assert res.decision == PermissionDecision.DENIED
    assert "Missing required argument" in res.reason


def test_permissions_endpoint_http():
    tokens = auth.issue_test_tokens()
    calc_call = {
        "tool_name": "calculator",
        "arguments": {"expression": "5 + 5"},
        "reasoning": "addition",
        "risk_level": "low",
        "requires_approval": False,
    }

    unauth = client.post("/permissions/check", json=calc_call)
    assert unauth.status_code in [401, 403]

    viewer_resp = client.post(
        "/permissions/check",
        json=calc_call,
        headers={"Authorization": f"Bearer {tokens['viewer']}"},
    )
    assert viewer_resp.status_code == 200
    assert viewer_resp.json()["decision"] == "allowed"

    ticket_call = {
        "tool_name": "create_ticket",
        "arguments": {"title": "Issue", "description": "Details"},
        "reasoning": "create ticket",
        "risk_level": "high",
        "requires_approval": True,
    }
    denied_resp = client.post(
        "/permissions/check",
        json=ticket_call,
        headers={"Authorization": f"Bearer {tokens['viewer']}"},
    )
    assert denied_resp.status_code == 200
    assert denied_resp.json()["decision"] == "denied"
