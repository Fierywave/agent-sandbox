"""WorkflowState schema.

Owned by Track B (it's the LangGraph state), but Track A's approval
backend reads/writes `status` and `pending_tool_call` as the task moves
through waiting_approval -> completed/rejected. Keep this the single
source of truth for "what stage is this task at" — don't let either
side track status in a separate ad-hoc field.
"""

from typing import Any

from pydantic import BaseModel, Field

from .enums import WorkflowStatus
from .plan import ToolCall


class WorkflowStep(BaseModel):
    node: str
    input: dict[str, Any] | None = None
    output: dict[str, Any] | None = None
    error: str | None = None


class WorkflowState(BaseModel):
    task_id: str
    user_id: str
    role: str
    current_node: str
    status: WorkflowStatus
    history: list[WorkflowStep] = Field(default_factory=list)
    pending_tool_call: ToolCall | None = Field(
        default=None,
        description="Set when status == waiting_approval; cleared on resume",
    )
    retry_count: int = 0
    final_response: str | None = None
