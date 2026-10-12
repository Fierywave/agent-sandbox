from threading import RLock

from fastapi import APIRouter, Depends, HTTPException, Path

from contracts.enums import Role, WorkflowStatus
from contracts.trace import TaskTrace, TraceSubmission

from .auth import TokenClaims, get_current_claims


router = APIRouter(prefix="/trace", tags=["Trace"])

TERMINAL_STATUSES = {
    WorkflowStatus.COMPLETED,
    WorkflowStatus.REJECTED,
    WorkflowStatus.FAILED,
}

ALLOWED_TRANSITIONS = {
    WorkflowStatus.RUNNING: set(WorkflowStatus),
    WorkflowStatus.WAITING_APPROVAL: {
        WorkflowStatus.WAITING_APPROVAL,
        WorkflowStatus.RUNNING,
        WorkflowStatus.COMPLETED,
        WorkflowStatus.REJECTED,
        WorkflowStatus.FAILED,
        WorkflowStatus.NEEDS_CLARIFICATION,
    },
    WorkflowStatus.NEEDS_CLARIFICATION: {
        WorkflowStatus.NEEDS_CLARIFICATION,
        WorkflowStatus.RUNNING,
        WorkflowStatus.WAITING_APPROVAL,
        WorkflowStatus.COMPLETED,
        WorkflowStatus.REJECTED,
        WorkflowStatus.FAILED,
    },
}


class TraceStore:
    def __init__(self):
        self._records: dict[str, TaskTrace] = {}
        self._lock = RLock()

    def save(
        self,
        task_id: str,
        submission: TraceSubmission,
        claims: TokenClaims,
    ) -> TaskTrace:
        with self._lock:
            existing = self._records.get(task_id)

            if existing is not None:
                if (
                    existing.user_id != claims.sub
                    or existing.role != claims.role
                ):
                    raise HTTPException(
                        status_code=403,
                        detail="Trace identity does not match the original run",
                    )

                previous = [
                    span.model_dump(mode="json")
                    for span in existing.spans
                ]
                incoming = [
                    span.model_dump(mode="json")
                    for span in submission.spans
                ]

                # An identical resend is safe.
                if (
                    previous == incoming
                    and existing.status == submission.status
                ):
                    return existing.model_copy(deep=True)

                if existing.status in TERMINAL_STATUSES:
                    raise HTTPException(
                        status_code=409,
                        detail="A finished trace cannot be changed",
                    )

                # Reject stale or rewritten snapshots.
                if (
                    len(incoming) < len(previous)
                    or incoming[:len(previous)] != previous
                ):
                    raise HTTPException(
                        status_code=409,
                        detail="A trace snapshot must preserve earlier events",
                    )

                allowed = ALLOWED_TRANSITIONS[existing.status]
                if submission.status not in allowed:
                    raise HTTPException(
                        status_code=409,
                        detail="Invalid workflow status transition",
                    )

            record = TaskTrace(
                task_id=task_id,
                user_id=claims.sub,
                role=claims.role,
                status=submission.status,
                spans=submission.spans,
            )

            self._records[task_id] = record.model_copy(deep=True)
            return record.model_copy(deep=True)

    def get(self, task_id: str) -> TaskTrace | None:
        with self._lock:
            record = self._records.get(task_id)
            return record.model_copy(deep=True) if record else None

    def list_all(self) -> list[TaskTrace]:
        with self._lock:
            return [
                record.model_copy(deep=True)
                for record in self._records.values()
            ]

    def clear(self) -> None:
        with self._lock:
            self._records.clear()


trace_store = TraceStore()


@router.post("/{task_id}", response_model=TaskTrace)
def submit_trace(
    submission: TraceSubmission,
    task_id: str = Path(min_length=1, max_length=200),
    claims: TokenClaims = Depends(get_current_claims),
) -> TaskTrace:
    return trace_store.save(task_id, submission, claims)


@router.get("/{task_id}", response_model=TaskTrace)
def get_trace(
    task_id: str = Path(min_length=1, max_length=200),
    claims: TokenClaims = Depends(get_current_claims),
) -> TaskTrace:
    record = trace_store.get(task_id)

    if record is None:
        raise HTTPException(status_code=404, detail="Trace not found")

    can_review = claims.role in {Role.OPERATOR, Role.ADMIN}
    if record.user_id != claims.sub and not can_review:
        raise HTTPException(status_code=403, detail="Trace access denied")

    return record