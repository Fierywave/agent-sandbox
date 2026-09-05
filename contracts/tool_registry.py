"""Tool registry schema.

Owned by Track A. This is the shape of every item returned by
`GET /tools`. Track B's planner reads this to know what it can even
propose — treat changes to this file as a breaking-change conversation
between both of you, not a solo edit.
"""

from typing import Any

from pydantic import BaseModel, Field

from .enums import Role, RiskLevel


class ToolDefinition(BaseModel):
    name: str = Field(..., description="Unique tool identifier, e.g. 'calculator'")
    description: str = Field(..., description="Shown to the LLM planner")

    # JSON Schema dicts (not Python types) so this can be serialized as-is
    # in the GET /tools response and consumed by any language/client.
    input_schema: dict[str, Any] = Field(
        ..., description="JSON Schema for this tool's arguments"
    )
    output_schema: dict[str, Any] = Field(
        ..., description="JSON Schema for this tool's return value"
    )

    risk_level: RiskLevel
    allowed_roles: list[Role] = Field(
        ..., description="Roles permitted to request this tool at all"
    )
    requires_approval: bool = Field(
        ..., description="If true, medium/high-tier calls pause for human sign-off"
    )
    rate_limit_per_minute: dict[Role, int] = Field(
        default_factory=dict,
        description="Calls per minute allowed, keyed by role. Missing role = no calls allowed.",
    )


class ToolRegistryResponse(BaseModel):
    """The exact shape of GET /tools."""

    tools: list[ToolDefinition]


# --- Starter tool set agreed in Phase 0 ---------------------------------
# This is a reference seed list, not the live registry (Track A's service
# owns the live data). Useful for Track B to build a stub against before
# Track A's service is running.
STARTER_TOOLS: list[ToolDefinition] = [
    ToolDefinition(
        name="calculator",
        description="Evaluate a basic arithmetic expression.",
        input_schema={
            "type": "object",
            "properties": {"expression": {"type": "string"}},
            "required": ["expression"],
        },
        output_schema={
            "type": "object",
            "properties": {"result": {"type": "number"}},
        },
        risk_level=RiskLevel.LOW,
        allowed_roles=[Role.VIEWER, Role.ANALYST, Role.OPERATOR, Role.ADMIN],
        requires_approval=False,
        rate_limit_per_minute={
            Role.VIEWER: 30,
            Role.ANALYST: 60,
            Role.OPERATOR: 60,
            Role.ADMIN: 120,
        },
    ),
    ToolDefinition(
        name="file_reader",
        description="Read a file from the sandboxed demo docs folder.",
        input_schema={
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
        },
        output_schema={
            "type": "object",
            "properties": {"content": {"type": "string"}},
        },
        risk_level=RiskLevel.LOW,
        allowed_roles=[Role.VIEWER, Role.ANALYST, Role.OPERATOR, Role.ADMIN],
        requires_approval=False,
        rate_limit_per_minute={
            Role.VIEWER: 30,
            Role.ANALYST: 60,
            Role.OPERATOR: 60,
            Role.ADMIN: 120,
        },
    ),
    ToolDefinition(
        name="mock_search",
        description="Search a mock/stubbed web index.",
        input_schema={
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
        },
        output_schema={
            "type": "object",
            "properties": {"results": {"type": "array"}},
        },
        risk_level=RiskLevel.LOW,
        allowed_roles=[Role.VIEWER, Role.ANALYST, Role.OPERATOR, Role.ADMIN],
        requires_approval=False,
        rate_limit_per_minute={
            Role.VIEWER: 20,
            Role.ANALYST: 40,
            Role.OPERATOR: 40,
            Role.ADMIN: 80,
        },
    ),
    ToolDefinition(
        name="csv_query",
        description="Run a filtered lookup against a demo CSV dataset.",
        input_schema={
            "type": "object",
            "properties": {
                "dataset": {"type": "string"},
                "filter": {"type": "object"},
            },
            "required": ["dataset", "filter"],
        },
        output_schema={
            "type": "object",
            "properties": {"rows": {"type": "array"}},
        },
        risk_level=RiskLevel.MEDIUM,
        allowed_roles=[Role.ANALYST, Role.OPERATOR, Role.ADMIN],
        requires_approval=False,
        rate_limit_per_minute={
            Role.ANALYST: 20,
            Role.OPERATOR: 30,
            Role.ADMIN: 60,
        },
    ),
    ToolDefinition(
        name="create_ticket",
        description="Create a ticket in a mock ticketing API.",
        input_schema={
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "description": {"type": "string"},
                "priority": {"type": "string"},
            },
            "required": ["title", "description"],
        },
        output_schema={
            "type": "object",
            "properties": {"ticket_id": {"type": "string"}},
        },
        risk_level=RiskLevel.HIGH,
        allowed_roles=[Role.OPERATOR, Role.ADMIN],
        requires_approval=True,
        rate_limit_per_minute={
            Role.OPERATOR: 10,
            Role.ADMIN: 20,
        },
    ),
]
