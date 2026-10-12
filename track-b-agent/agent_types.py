"""agent_types — Track B's own typed building blocks for Phase 3.

These extend the shared contracts without changing them: AgentPlan IS a
contracts.Plan (anything that accepts a Plan accepts it) with two extra
fields only Track B's planner and graph care about.
"""

from typing import Any

from pydantic import BaseModel, Field

from contracts import Plan

# Hard limits, so no plan (LLM-written or not) can run away.
MAX_PLAN_STEPS = 5   # most tool calls one task may contain
MAX_RETRIES = 1      # automatic retries per step; "exactly one retry" by default


class AgentPlan(Plan):
    """A Plan plus what the planner knows about the user's intent."""

    # Indexes of steps where the user clearly asked for ONE record ("look up
    # the customer ..."), so a result with several rows means "ask which
    # one" rather than "pick the first and hope".
    single_result_steps: list[int] = Field(default_factory=list)

    # Set instead of steps when the request is too vague to act on safely.
    clarification_question: str | None = None


class StepResult(BaseModel):
    """What one executed step produced. Later steps read these through
    {{step.path}} placeholders (see steps.py) — typed data, never
    free-text 'memory'."""

    step: int
    tool_name: str
    arguments: dict[str, Any]
    result: dict[str, Any]


class TransientToolError(Exception):
    """A tool failed in a way that might succeed if simply tried again
    (timeout, flaky dependency). Tools raise this to mark a failure as
    worth retrying; TimeoutError and ConnectionError count too."""


TRANSIENT_ERRORS = (TransientToolError, TimeoutError, ConnectionError)
