from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from .enums import Role, WorkflowStatus


class TraceSpan(BaseModel):
    # Preserve new metadata added by Track B instead of silently dropping it.
    model_config = ConfigDict(extra="allow")

    node: str = Field(min_length=1, max_length=100)

    # Overall workflow events may not belong to a particular tool step.
    step: int | None = Field(default=None, ge=0)
    attempt: int | None = Field(default=None, ge=0)
    will_retry: bool | None = None

    input: dict[str, Any] | None = None
    output: dict[str, Any] | None = None
    error: str | None = None

    # Track B must measure this. Track A must not invent it.
    latency_ms: float | None = Field(default=None, ge=0, allow_inf_nan=False)

    # Useful on tool_execution events, whose current output may not name
    # the tool.
    tool_name: str | None = None


class TraceSubmission(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: WorkflowStatus
    spans: list[TraceSpan] = Field(max_length=1000)


class TaskTrace(TraceSubmission):
    task_id: str
    user_id: str
    role: Role