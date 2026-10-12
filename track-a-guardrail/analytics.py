"""Separate guardrail decisions from agent-reported execution telemetry."""

from collections import Counter

from fastapi import APIRouter, Depends

from contracts.enums import PermissionDecision, Role, WorkflowStatus
from .approvals import approval_store
from .audit import audit_logger
from .auth import TokenClaims, require_role
from .tracing import trace_store

router = APIRouter(prefix="/analytics", tags=["Analytics"])


def fraction(numerator: int, denominator: int) -> float | None:
    return round(numerator / denominator, 4) if denominator else None


def build_summary() -> dict:
    events = [e for e in audit_logger.snapshot() if e.event_type == "permission"]
    decisions = Counter(e.permission_decision.value for e in events)
    blocked = [e for e in events if e.permission_decision in {
        PermissionDecision.DENIED, PermissionDecision.RATE_LIMITED,
    }]
    reasons = Counter(e.reason or "Reason not recorded" for e in blocked)
    requests = Counter((e.tool_name, e.role) for e in events)
    approvals = Counter(r.status.value for r in approval_store.list())
    traces = trace_store.list_all()
    statuses = Counter(t.status.value for t in traces)
    executions = Counter()
    errors = Counter()
    attempts = failed = 0
    for trace in traces:
        for span in trace.spans:
            if span.node != "tool_execution":
                continue
            attempts += 1
            executions[(span.tool_name or "unreported", trace.role.value)] += 1
            if span.error is not None:
                failed += 1
                errors[span.error] += 1

    def by_tool_role(counts):
        return [{"tool_name": tool, "role": role, "count": count}
                for (tool, role), count in sorted(counts.items())]

    return {
        "permission_checks": {
            "total": len(events),
            "allowed": decisions["allowed"],
            "denied": decisions["denied"],
            "rate_limited": decisions["rate_limited"],
            "blocked_rate": fraction(len(blocked), len(events)),
            "requests_by_tool_and_role": by_tool_role(requests),
            "common_block_reasons": [
                {"reason": reason, "count": count}
                for reason, count in reasons.most_common(10)
            ],
        },
        "approvals": {
            "pending": approvals["pending"],
            "approved": approvals["approved"],
            "rejected": approvals["rejected"],
            "approval_rate_among_resolved": fraction(
                approvals["approved"], approvals["approved"] + approvals["rejected"]
            ),
        },
        "reported_tasks": {
            "total": len(traces),
            "by_status": {s.value: statuses[s.value] for s in WorkflowStatus},
        },
        "reported_tool_executions": {
            "attempts": attempts,
            "failed_attempts": failed,
            "by_tool_and_role": by_tool_role(executions),
            "common_errors": [{"error": error, "count": count}
                              for error, count in errors.most_common(10)],
        },
    }


@router.get("/summary")
def get_summary(claims: TokenClaims = Depends(require_role(Role.OPERATOR))) -> dict:
    return build_summary()
