"""Shared enums for the Permissioned Tool-Using Agent Sandbox.

Both Track A (Guardrail) and Track B (Agent) import these — never redefine
your own copy of a role or risk level in service-specific code.
"""

from enum import Enum


class Role(str, Enum):
    VIEWER = "viewer"
    ANALYST = "analyst"
    OPERATOR = "operator"
    ADMIN = "admin"


class RiskLevel(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class WorkflowStatus(str, Enum):
    RUNNING = "running"
    WAITING_APPROVAL = "waiting_approval"
    COMPLETED = "completed"
    REJECTED = "rejected"
    FAILED = "failed"


class ApprovalDecision(str, Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


class PermissionDecision(str, Enum):
    ALLOWED = "allowed"
    DENIED = "denied"
    RATE_LIMITED = "rate_limited"


# Which risk tiers each role is allowed to *request*.
# Enforcement of this table lives in Track A's registry/permission service —
# Track B's planner may read it for UX purposes (e.g. don't even suggest a
# tool the user's role can never use) but must never rely on it as the
# actual security boundary.
ROLE_MAX_RISK: dict[Role, RiskLevel] = {
    Role.VIEWER: RiskLevel.LOW,
    Role.ANALYST: RiskLevel.MEDIUM,
    Role.OPERATOR: RiskLevel.HIGH,
    Role.ADMIN: RiskLevel.HIGH,
}
