"""AuditEvent schema.

Owned by Track A. Every tool request — allowed, denied, rate-limited,
pending, approved, rejected, executed, or failed — gets written as one
of these. This table is the answer to "why did the agent do that?", so
don't drop fields to save space; storage is cheap, an unreconstructable
decision is not.
"""

from datetime import datetime
from typing import Any

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
        default=None, description="Only set for tools requiring approval"
    )
    approved_by: str | None = None

    execution_result: dict[str, Any] | None = None
    execution_error: str | None = None

    latency_ms: float | None = None
