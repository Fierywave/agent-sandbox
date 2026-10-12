"""Shared audit event schema used by Track A and Track B."""

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from .enums import ApprovalDecision, PermissionDecision, RiskLevel


class AuditEvent(BaseModel):
    event_id: str
    task_id: str
    timestamp: datetime

    user_id: str
    role: str

    tool_name: str
    arguments: dict[str, Any]
    risk_level: RiskLevel

    permission_decision: PermissionDecision

    approval_decision: ApprovalDecision | None = Field(
        default=None,
        description="Only set for tools requiring approval",
    )
    approved_by: str | None = None

    execution_result: dict[str, Any] | None = None
    execution_error: str | None = None

    latency_ms: float | None = None

    event_type: Literal[
        "permission", "approval", "execution"
    ] = "permission"
    reason: str | None = None