"""permission_client — Phase 1: a LOCAL stub that replicates Track A's
documented permission logic exactly (see HANDOFF_A_PHASE1.md), so the graph
behaves identically once swapped to `check_permission_live` in Phase 2.
Never let the model's own claim of "this should be fine" substitute for
this check — it always runs before tool_execution.

Track A's documented order, first failure wins:
  1. tool must exist in the registry -> else DENIED
  2. all `required` fields in the tool's input_schema must be present -> else DENIED
  3. caller's role must be in tool.allowed_roles AND role's max risk
     (ROLE_MAX_RISK) must cover the tool's risk_level -> else DENIED
  4. otherwise ALLOWED, with requires_approval = tool.requires_approval OR risk_level == HIGH
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


def check_permission_live(
    registry_base_url: str, token: str, tool_call: ToolCall
) -> dict:
    """Phase 2: calls Track A's real POST /permissions/check. Wire this in
    at the Day-7 sync point once Track A's approval backend is running —
    same response shape as the stub above, so callers don't change."""
    import requests

    resp = requests.post(
        f"{registry_base_url.rstrip('/')}/permissions/check",
        headers={"Authorization": f"Bearer {token}"},
        json=tool_call.model_dump(mode="json"),
        timeout=5,
    )
    resp.raise_for_status()
    return resp.json()