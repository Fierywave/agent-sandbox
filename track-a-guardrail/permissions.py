import os
import sys
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from contracts.enums import PermissionDecision, RiskLevel, Role, ROLE_MAX_RISK
from contracts.plan import ToolCall

try:
    from .auth import TokenClaims, get_current_claims
    from .registry import registry
    from .rate_limit import rate_limiter
    from .audit import audit_logger
except (ImportError, ValueError):
    from auth import TokenClaims, get_current_claims
    from registry import registry
    from rate_limit import rate_limiter
    from audit import audit_logger

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


def evaluate_permission(
    user_role: Role,
    tool_call: ToolCall,
    user_id: str = "anonymous",
    task_id: str = "default-task",
) -> PermissionCheckResponse:
    # 1. Tool existence check
    tool = registry.get(tool_call.tool_name)
    if not tool:
        res = PermissionCheckResponse(
            decision=PermissionDecision.DENIED,
            tool_name=tool_call.tool_name,
            user_role=user_role,
            risk_level=tool_call.risk_level,
            requires_approval=False,
            reason=f"Tool '{tool_call.tool_name}' is not registered in the system.",
        )
        audit_logger.create_and_log(
            task_id=task_id,
            user_id=user_id,
            role=user_role.value,
            tool_name=tool_call.tool_name,
            arguments=tool_call.arguments,
            risk_level=tool_call.risk_level,
            permission_decision=res.decision,
        )
        return res

    # 2. Argument validation
    valid_args, err = validate_arguments(tool.input_schema, tool_call.arguments)
    if not valid_args:
        res = PermissionCheckResponse(
            decision=PermissionDecision.DENIED,
            tool_name=tool.name,
            user_role=user_role,
            risk_level=tool.risk_level,
            requires_approval=False,
            reason=f"Argument validation failed: {err}",
        )
        audit_logger.create_and_log(
            task_id=task_id,
            user_id=user_id,
            role=user_role.value,
            tool_name=tool.name,
            arguments=tool_call.arguments,
            risk_level=tool.risk_level,
            permission_decision=res.decision,
        )
        return res

    # 3. Allowed roles check
    if user_role not in tool.allowed_roles:
        allowed_str = ", ".join(r.value for r in tool.allowed_roles)
        res = PermissionCheckResponse(
            decision=PermissionDecision.DENIED,
            tool_name=tool.name,
            user_role=user_role,
            risk_level=tool.risk_level,
            requires_approval=False,
            reason=f"Role '{user_role.value}' is not permitted to use tool '{tool.name}'. Allowed: [{allowed_str}].",
        )
        audit_logger.create_and_log(
            task_id=task_id,
            user_id=user_id,
            role=user_role.value,
            tool_name=tool.name,
            arguments=tool_call.arguments,
            risk_level=tool.risk_level,
            permission_decision=res.decision,
        )
        return res

    # 4. Role max risk ceiling check
    max_risk = ROLE_MAX_RISK.get(user_role, RiskLevel.LOW)
    if _RISK_ORDER[tool.risk_level] > _RISK_ORDER[max_risk]:
        res = PermissionCheckResponse(
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
        audit_logger.create_and_log(
            task_id=task_id,
            user_id=user_id,
            role=user_role.value,
            tool_name=tool.name,
            arguments=tool_call.arguments,
            risk_level=tool.risk_level,
            permission_decision=res.decision,
        )
        return res

    # 5. Rate limiting check (runs for permitted roles)
    allowed, retry_after = rate_limiter.check(user_id=user_id, role=user_role, tool_name=tool.name)
    if not allowed:
        res = PermissionCheckResponse(
            decision=PermissionDecision.RATE_LIMITED,
            tool_name=tool.name,
            user_role=user_role,
            risk_level=tool.risk_level,
            requires_approval=False,
            reason=f"Rate limit exceeded for tool '{tool.name}' and role '{user_role.value}'. Retry in {retry_after}s.",
        )
        audit_logger.create_and_log(
            task_id=task_id,
            user_id=user_id,
            role=user_role.value,
            tool_name=tool.name,
            arguments=tool_call.arguments,
            risk_level=tool.risk_level,
            permission_decision=res.decision,
        )
        return res

    # 6. Approval requirement check
    needs_approval = tool.requires_approval or (tool.risk_level == RiskLevel.HIGH)
    res = PermissionCheckResponse(
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
    audit_logger.create_and_log(
        task_id=task_id,
        user_id=user_id,
        role=user_role.value,
        tool_name=tool.name,
        arguments=tool_call.arguments,
        risk_level=tool.risk_level,
        permission_decision=res.decision,
        approval_decision=None,
    )
    return res


@router.post("/check", response_model=PermissionCheckResponse)
def check_permission_endpoint(
    tool_call: ToolCall,
    claims: TokenClaims = Depends(get_current_claims),
) -> PermissionCheckResponse:
    res = evaluate_permission(
        user_role=claims.role,
        tool_call=tool_call,
        user_id=claims.sub,
    )
    if res.decision == PermissionDecision.RATE_LIMITED:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=res.reason,
        )
    return res


check_permission = evaluate_permission
