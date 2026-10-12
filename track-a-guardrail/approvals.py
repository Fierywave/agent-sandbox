from datetime import datetime, timezone
import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field

from contracts.enums import ApprovalDecision, PermissionDecision, Role
from contracts.plan import ToolCall

try:
    from .auth import TokenClaims, get_current_claims, require_role
    from .audit import audit_logger
    from .registry import registry
except (ImportError, ValueError):
    from auth import TokenClaims, get_current_claims, require_role
    from audit import audit_logger
    from registry import registry

router = APIRouter(prefix="/approvals", tags=["Approvals"])


class ApprovalRecord(BaseModel):
    id: str
    task_id: str
    user_id: str
    role: Role
    tool_call: ToolCall
    reason: str | None = None
    status: ApprovalDecision = ApprovalDecision.PENDING
    created_at: datetime
    resolved_at: datetime | None = None
    resolved_by: str | None = None
    resolution_note: str | None = None


class ApprovalCreateRequest(BaseModel):
    task_id: str
    tool_call: ToolCall
    reason: str | None = None


class ApprovalActionRequest(BaseModel):
    note: str | None = None


def log_approval_event(record: ApprovalRecord) -> None:
    tool = registry.get(record.tool_call.tool_name)
    audit_logger.create_and_log(
        task_id=record.task_id,
        user_id=record.user_id,
        role=record.role.value,
        tool_name=record.tool_call.tool_name,
        arguments=record.tool_call.arguments,
        risk_level=tool.risk_level if tool else record.tool_call.risk_level,
        # Legacy required field: this event is not a permission evaluation.
        # Consumers must use event_type to distinguish the two categories.
        permission_decision=PermissionDecision.ALLOWED,
        approval_decision=record.status,
        approved_by=(record.resolved_by if record.status == ApprovalDecision.APPROVED else None),
        event_type="approval",
        reason=record.resolution_note if record.status != ApprovalDecision.PENDING else record.reason,
        execution_result={"approval_id": record.id, "resolved_by": record.resolved_by},
    )


class ApprovalStore:
    def __init__(self):
        self._records: dict[str, ApprovalRecord] = {}

    def create(
        self, task_id: str, user_id: str, role: Role, tool_call: ToolCall, reason: str | None = None
    ) -> ApprovalRecord:
        record_id = str(uuid.uuid4())
        record = ApprovalRecord(
            id=record_id,
            task_id=task_id,
            user_id=user_id,
            role=role,
            tool_call=tool_call,
            reason=reason,
            status=ApprovalDecision.PENDING,
            created_at=datetime.now(timezone.utc),
        )
        self._records[record_id] = record
        log_approval_event(record)
        return record

    def get(self, record_id: str) -> ApprovalRecord | None:
        return self._records.get(record_id)

    def list(self, status_filter: ApprovalDecision | None = None) -> list[ApprovalRecord]:
        records = list(self._records.values())
        if status_filter:
            records = [r for r in records if r.status == status_filter]
        return records

    def resolve(
        self,
        record_id: str,
        decision: ApprovalDecision,
        resolved_by: str,
        note: str | None = None,
    ) -> ApprovalRecord:
        record = self.get(record_id)
        if not record:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Approval request not found")
        if record.status != ApprovalDecision.PENDING:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Approval request already resolved with status '{record.status.value}'",
            )

        record.status = decision
        record.resolved_at = datetime.now(timezone.utc)
        record.resolved_by = resolved_by
        record.resolution_note = note
        log_approval_event(record)
        return record

    def clear(self) -> None:
        self._records.clear()


approval_store = ApprovalStore()


@router.post("", response_model=ApprovalRecord, status_code=status.HTTP_201_CREATED)
def create_approval(
    req: ApprovalCreateRequest,
    claims: TokenClaims = Depends(get_current_claims),
) -> ApprovalRecord:
    return approval_store.create(
        task_id=req.task_id,
        user_id=claims.sub,
        role=claims.role,
        tool_call=req.tool_call,
        reason=req.reason,
    )


@router.get("/pending", response_model=list[ApprovalRecord])
def list_pending_approvals() -> list[ApprovalRecord]:
    return approval_store.list(status_filter=ApprovalDecision.PENDING)


@router.get("", response_model=list[ApprovalRecord])
def list_approvals(
    status: ApprovalDecision | None = Query(default=None),
) -> list[ApprovalRecord]:
    return approval_store.list(status_filter=status)


@router.get("/{approval_id}", response_model=ApprovalRecord)
def get_approval(approval_id: str) -> ApprovalRecord:
    record = approval_store.get(approval_id)
    if not record:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Approval request not found")
    return record


@router.post("/{approval_id}/approve", response_model=ApprovalRecord)
def approve_request(
    approval_id: str,
    action: ApprovalActionRequest | None = None,
    claims: TokenClaims = Depends(require_role(Role.OPERATOR)),
) -> ApprovalRecord:
    note = action.note if action else None
    return approval_store.resolve(
        record_id=approval_id,
        decision=ApprovalDecision.APPROVED,
        resolved_by=claims.sub,
        note=note,
    )


@router.post("/{approval_id}/reject", response_model=ApprovalRecord)
def reject_request(
    approval_id: str,
    action: ApprovalActionRequest | None = None,
    claims: TokenClaims = Depends(require_role(Role.OPERATOR)),
) -> ApprovalRecord:
    note = action.note if action else None
    return approval_store.resolve(
        record_id=approval_id,
        decision=ApprovalDecision.REJECTED,
        resolved_by=claims.sub,
        note=note,
    )
