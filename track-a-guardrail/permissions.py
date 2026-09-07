import os
import sys
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from contracts.enums import PermissionDecision, RiskLevel, Role, ROLE_MAX_RISK
from contracts.plan import ToolCall

try:
    from .auth import TokenClaims, get_current_claims
    from .registry import registry
except (ImportError, ValueError):
    from auth import TokenClaims, get_current_claims
    from registry import registry

router = APIRouter(prefix="/permissions", tags=["Permissions"])

_RISK_ORDER: dict[RiskLevel, int] = {
    RiskLevel.LOW: 1,
    RiskLevel.MEDIUM: 2,
    RiskLevel.HIGH: 3,
}


class PermissionCheckResponse(BaseModel):
    decision: PermissionDecision
    tool_name: str
    user_role: Role
    risk_level: RiskLevel
    requires_approval: bool
    reason: str


def validate_arguments(schema: dict[str, Any], arguments: dict[str, Any]) -> tuple[bool, str | None]:
    for required in schema.get("required", []):
        if required not in arguments:
            return False, f"Missing required argument: '{required}'"

    props = schema.get("properties", {})
    if props:
        for key in arguments:
            if key not in props:
                return False, f"Unexpected argument: '{key}'"

    return True, None


def evaluate_permission(user_role: Role, tool_call: ToolCall) -> PermissionCheckResponse:
    tool = registry.get(tool_call.tool_name)
    if not tool:
        return PermissionCheckResponse(
            decision=PermissionDecision.DENIED,
            tool_name=tool_call.tool_name,
            user_role=user_role,
            risk_level=tool_call.risk_level,
            requires_approval=False,
            reason=f"Tool '{tool_call.tool_name}' is not registered in the system.",
        )

    valid_args, err = validate_arguments(tool.input_schema, tool_call.arguments)
    if not valid_args:
        return PermissionCheckResponse(
            decision=PermissionDecision.DENIED,
            tool_name=tool.name,
            user_role=user_role,
            risk_level=tool.risk_level,
            requires_approval=False,
            reason=f"Argument validation failed: {err}",
        )

    if user_role not in tool.allowed_roles:
        allowed = ", ".join(r.value for r in tool.allowed_roles)
        return PermissionCheckResponse(
            decision=PermissionDecision.DENIED,
            tool_name=tool.name,
            user_role=user_role,
            risk_level=tool.risk_level,
            requires_approval=False,
            reason=f"Role '{user_role.value}' is not permitted to use tool '{tool.name}'. Allowed: [{allowed}].",
        )

    max_risk = ROLE_MAX_RISK.get(user_role, RiskLevel.LOW)
    if _RISK_ORDER[tool.risk_level] > _RISK_ORDER[max_risk]:
        return PermissionCheckResponse(
            decision=PermissionDecision.DENIED,
            tool_name=tool.name,
            user_role=user_role,
            risk_level=tool.risk_level,
            requires_approval=False,
            reason=(
                f"Tool risk '{tool.risk_level.value}' exceeds max risk "
                f"'{max_risk.value}' for role '{user_role.value}'."
            ),
        )

    needs_approval = tool.requires_approval or (tool.risk_level == RiskLevel.HIGH)
    return PermissionCheckResponse(
        decision=PermissionDecision.ALLOWED,
        tool_name=tool.name,
        user_role=user_role,
        risk_level=tool.risk_level,
        requires_approval=needs_approval,
        reason=(
            "Tool call approved for execution."
            if not needs_approval
            else "Tool call permitted but requires human approval before execution."
        ),
    )


@router.post("/check", response_model=PermissionCheckResponse)
def check_permission_endpoint(
    tool_call: ToolCall,
    claims: TokenClaims = Depends(get_current_claims),
) -> PermissionCheckResponse:
    return evaluate_permission(user_role=claims.role, tool_call=tool_call)


check_permission = evaluate_permission
