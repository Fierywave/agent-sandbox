from datetime import datetime, timezone
from typing import Any, Literal
import uuid

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
        event_type: Literal[
            "permission", "approval", "execution"
        ] = "permission",
        reason: str | None = None,
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
            event_type=event_type,
            reason=reason,
        )
        return self.log(event)

    def snapshot(self) -> list[AuditEvent]:
        """Return copies of all events for analytics."""
        return [
            event.model_copy(deep=True)
            for event in self._events
        ]

    def query(
        self,
        user_id: str | None = None,
        role: str | None = None,
        tool_name: str | None = None,
        risk_level: RiskLevel | None = None,
        permission_decision: PermissionDecision | None = None,
        limit: int = 100,
        task_id: str | None = None,
    ) -> list[AuditEvent]:
        results = self._events

        if task_id is not None:
            results = [
                event for event in results
                if event.task_id == task_id
            ]

        if user_id:
            results = [
                event for event in results
                if event.user_id == user_id
            ]

        if role:
            results = [
                event for event in results
                if event.role == role
            ]

        if tool_name:
            results = [
                event for event in results
                if event.tool_name == tool_name
            ]

        if risk_level:
            results = [
                event for event in results
                if event.risk_level == risk_level
            ]

        if permission_decision:
            results = [
                event for event in results
                if event.permission_decision == permission_decision
            ]

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
    task_id: str | None = Query(default=None),
) -> list[AuditEvent]:
    return audit_logger.query(
        user_id=user_id,
        role=role,
        tool_name=tool_name,
        risk_level=risk_level,
        permission_decision=permission_decision,
        limit=limit,
        task_id=task_id,
    )


@router.get("/{event_id}", response_model=AuditEvent)
def get_audit_event(event_id: str) -> AuditEvent:
    for event in audit_logger.snapshot():
        if event.event_id == event_id:
            return event

    raise HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail="Audit event not found",
    )