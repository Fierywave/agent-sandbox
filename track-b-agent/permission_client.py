"""permission_client — the OFFLINE permission check, used only when no
guardrail URL is configured (unit tests, working without Track A running).
When a guardrail URL is set, graph.py calls Track A's real engine through
guardrail_client.check_permission_live instead and this file isn't used.

It mirrors Track A's permissions.py, first failure wins:
  1. tool must exist in the registry
  2. all `required` args present, and no args outside the schema's properties
  3. caller's role must be in tool.allowed_roles
  4. role's max risk (ROLE_MAX_RISK) must cover the tool's risk_level
  5. (Track A only) sliding-window rate limit — NOT mirrored here
  6. ALLOWED, with requires_approval = tool.requires_approval OR risk_level == HIGH

tests/test_track_b_integration.py runs this against Track A's real engine
across every role x tool x argument combination, so the two can't drift
apart without a test failing.
"""

from contracts import ROLE_MAX_RISK, Role, RiskLevel, ToolCall, ToolDefinition

_RISK_ORDER = {RiskLevel.LOW: 0, RiskLevel.MEDIUM: 1, RiskLevel.HIGH: 2}


def _covers(role_max: RiskLevel, tool_risk: RiskLevel) -> bool:
    return _RISK_ORDER[role_max] >= _RISK_ORDER[tool_risk]


def check_permission_stub(
    tool_call: ToolCall, role_str: str, tools_by_name: dict[str, ToolDefinition]
) -> dict:
    """Mirrors POST /permissions/check's documented response shape:
    {decision, tool_name, user_role, risk_level, requires_approval, reason}
    """
    tool = tools_by_name.get(tool_call.tool_name)
    base = {"tool_name": tool_call.tool_name, "user_role": role_str}

    if tool is None:
        return {
            **base,
            "decision": "denied",
            "risk_level": tool_call.risk_level.value,
            "requires_approval": False,
            "reason": f"Unknown tool '{tool_call.tool_name}'",
        }

    missing = [
        field
        for field in tool.input_schema.get("required", [])
        if field not in tool_call.arguments
    ]
    if missing:
        return {
            **base,
            "decision": "denied",
            "risk_level": tool.risk_level.value,
            "requires_approval": False,
            "reason": f"Missing required argument(s): {', '.join(missing)}",
        }

    properties = tool.input_schema.get("properties", {})
    unexpected = [k for k in tool_call.arguments if properties and k not in properties]
    if unexpected:
        return {
            **base,
            "decision": "denied",
            "risk_level": tool.risk_level.value,
            "requires_approval": False,
            "reason": f"Unexpected argument(s): {', '.join(unexpected)}",
        }

    try:
        role = Role(role_str)
    except ValueError:
        return {
            **base,
            "decision": "denied",
            "risk_level": tool.risk_level.value,
            "requires_approval": False,
            "reason": f"Unknown role '{role_str}'",
        }

    if role not in tool.allowed_roles or not _covers(ROLE_MAX_RISK[role], tool.risk_level):
        return {
            **base,
            "decision": "denied",
            "risk_level": tool.risk_level.value,
            "requires_approval": False,
            "reason": f"Role '{role_str}' is not permitted to call '{tool.name}' (risk={tool.risk_level.value})",
        }

    requires_approval = tool.requires_approval or tool.risk_level == RiskLevel.HIGH
    return {
        **base,
        "decision": "allowed",
        "risk_level": tool.risk_level.value,
        "requires_approval": requires_approval,
        "reason": "ok",
    }
