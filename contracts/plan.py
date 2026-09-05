"""ToolCall / Plan schema.

Owned jointly: Track B's planning node PRODUCES objects of this shape,
Track A's permission-check endpoint CONSUMES them. Never let the LLM's
raw text substitute for this — always parse into ToolCall/Plan and
validate before anything executes.
"""

from typing import Any

from pydantic import BaseModel, Field

from .enums import RiskLevel


class ToolCall(BaseModel):
    tool_name: str
    arguments: dict[str, Any]
    reasoning: str = Field(..., description="Why the planner chose this tool/args")
    risk_level: RiskLevel = Field(
        ..., description="Copied from the tool registry at plan time, for audit clarity"
    )
    requires_approval: bool


class Plan(BaseModel):
    """A single planning-node output. Usually one ToolCall, but modeled as
    a list so multi-step tasks (Phase 3) don't need a schema migration."""

    task_id: str
    steps: list[ToolCall]
    final_answer_if_no_tool_needed: str | None = Field(
        default=None,
        description="Set when the planner decides no tool call is needed at all",
    )
