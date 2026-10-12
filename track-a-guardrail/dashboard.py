"""Track A approval queue and audit dashboard."""

from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import AwareDatetime

from contracts.enums import ApprovalDecision, RiskLevel, Role

from .approvals import approval_store
from .audit import audit_logger
from .auth import TokenClaims, require_role
from .registry import registry


router = APIRouter(prefix="/dashboard", tags=["Dashboard"])

HTML_PATH = Path(__file__).parent / "static" / "dashboard.html"


@router.get("", include_in_schema=False)
def dashboard_page():
    """The page itself contains no private data."""
    return FileResponse(
        HTML_PATH,
        media_type="text/html",
        headers={"Cache-Control": "no-store"},
    )


@router.get("/api/approvals")
def pending_approvals(
    claims: TokenClaims = Depends(require_role(Role.OPERATOR)),
):
    records = approval_store.list(
        status_filter=ApprovalDecision.PENDING
    )

    result = []

    for record in sorted(records, key=lambda item: item.created_at):
        data = record.model_dump(mode="json")
        registered_tool = registry.get(record.tool_call.tool_name)

        # Display the registry's policy instead of trusting a submitted
        # tool call's claimed risk level.
        data["registry_risk_level"] = (
            registered_tool.risk_level.value
            if registered_tool is not None
            else "unknown"
        )

        result.append(data)

    return result


@router.get("/api/audit")
def dashboard_audit(
    claims: TokenClaims = Depends(require_role(Role.OPERATOR)),
    user_id: str | None = None,
    task_id: str | None = None,
    tool_name: str | None = None,
    risk_level: RiskLevel | None = None,
    event_type: Literal[
        "permission", "approval", "execution"
    ] | None = None,
    start: AwareDatetime | None = None,
    end: AwareDatetime | None = None,
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=25, ge=1, le=100),
):
    """
    Date bounds are inclusive and must include a timezone.

    Example:
    start=2026-10-12T00:00:00Z
    end=2026-10-12T23:59:59Z
    """
    if start is not None and end is not None and start > end:
        raise HTTPException(
            status_code=422,
            detail="Start time must be before or equal to end time",
        )

    events = audit_logger.snapshot()

    if user_id:
        events = [event for event in events if event.user_id == user_id]

    if task_id:
        events = [event for event in events if event.task_id == task_id]

    if tool_name:
        events = [
            event for event in events
            if event.tool_name == tool_name
        ]

    if risk_level is not None:
        events = [
            event for event in events
            if event.risk_level == risk_level
        ]

    if event_type is not None:
        events = [
            event for event in events
            if event.event_type == event_type
        ]

    if start is not None:
        events = [
            event for event in events
            if event.timestamp >= start
        ]

    if end is not None:
        events = [
            event for event in events
            if event.timestamp <= end
        ]

    # Newest first, with a stable tie-breaker.
    events.sort(
        key=lambda event: (event.timestamp, event.event_id),
        reverse=True,
    )

    return {
        "total": len(events),
        "offset": offset,
        "limit": limit,
        "items": [
            event.model_dump(mode="json")
            for event in events[offset:offset + limit]
        ],
    }