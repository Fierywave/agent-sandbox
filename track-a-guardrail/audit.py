from datetime import datetime, timezone
import uuid
from typing import Any

from fastapi import APIRouter, HTTPException, Query, status

from contracts.audit_event import AuditEvent
from contracts.enums import ApprovalDecision, PermissionDecision, RiskLevel

router = APIRouter(prefix="/audit", tags=["Audit"])


class AuditLogger:
    def __init__(self):
        self._events: list[AuditEvent] = []

    def log(self, event: AuditEvent) -> AuditEvent:
        self._events.append(event)
        return event

    def create_and_log(
        self,
        task_id: str,
        user_id: str,
        role: str,
        tool_name: str,
        arguments: dict[str, Any],
        risk_level: RiskLevel,
        permission_decision: PermissionDecision,
        approval_decision: ApprovalDecision | None = None,
        approved_by: str | None = None,
        execution_result: dict[str, Any] | None = None,
        execution_error: str | None = None,
        latency_ms: float | None = None,
    ) -> AuditEvent:
        event = AuditEvent(
            event_id=str(uuid.uuid4()),
            task_id=task_id,
            timestamp=datetime.now(timezone.utc),
            user_id=user_id,
            role=role,
            tool_name=tool_name,
            arguments=arguments,
            risk_level=risk_level,
            permission_decision=permission_decision,
            approval_decision=approval_decision,
            approved_by=approved_by,
            execution_result=execution_result,
            execution_error=execution_error,
            latency_ms=latency_ms,
        )
        return self.log(event)

    def query(
        self,
        user_id: str | None = None,
        role: str | None = None,
        tool_name: str | None = None,
        risk_level: RiskLevel | None = None,
        permission_decision: PermissionDecision | None = None,
        limit: int = 100,
    ) -> list[AuditEvent]:
        results = self._events
        if user_id:
            results = [e for e in results if e.user_id == user_id]
        if role:
            results = [e for e in results if e.role == role]
        if tool_name:
            results = [e for e in results if e.tool_name == tool_name]
        if risk_level:
            risk_val = risk_level.value if hasattr(risk_level, "value") else str(risk_level)
            results = [e for e in results if e.risk_level == risk_level or e.risk_level.value == risk_val]
        if permission_decision:
            decision_val = permission_decision.value if hasattr(permission_decision, "value") else str(permission_decision)
            results = [e for e in results if e.permission_decision == permission_decision or e.permission_decision.value == decision_val]
        return results[-limit:]

    def clear(self) -> None:
        self._events.clear()


audit_logger = AuditLogger()


@router.get("", response_model=list[AuditEvent])
def get_audit_events(
    user_id: str | None = Query(default=None),
    role: str | None = Query(default=None),
    tool_name: str | None = Query(default=None),
    risk_level: RiskLevel | None = Query(default=None),
    permission_decision: PermissionDecision | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=1000),
) -> list[AuditEvent]:
    return audit_logger.query(
        user_id=user_id,
        role=role,
        tool_name=tool_name,
        risk_level=risk_level,
        permission_decision=permission_decision,
        limit=limit,
    )


@router.get("/{event_id}", response_model=AuditEvent)
def get_audit_event(event_id: str) -> AuditEvent:
    for event in audit_logger._events:
        if event.event_id == event_id:
            return event
    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Audit event not found")
