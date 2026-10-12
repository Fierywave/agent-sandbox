"""planner — reads the tool registry and emits a validated AgentPlan
(a contracts.Plan plus Track B's intent fields; see agent_types.py).

Two modes:
  - LLM mode: used when ANTHROPIC_API_KEY is set (or an `llm_call` is
    injected, which is how the tests exercise this path without a key).
    The model's JSON is parsed and VALIDATED; one retry on a bad reply,
    then it falls back to rule-based rather than crashing the graph.
  - Rule-based mode: no key. Deterministic, so tests are reproducible
    without network access or cost.

What the planner can now produce (Phase 3):
  - several steps in order, where later steps pull data from earlier
    results with {{step.path}} placeholders (resolved in steps.py)
  - a clarification question instead of steps, when the request is too
    vague to act on safely
  - a revised plan after a tool error (replan), given the error text

The model never gets to decide what is allowed: risk_level and
requires_approval on every step are overwritten from the registry, and
every step still goes through the permission check before it runs.
"""

import json
import logging
import os
import re
from typing import Callable

from contracts import ToolCall, ToolDefinition

from agent_types import MAX_PLAN_STEPS, AgentPlan

logger = logging.getLogger(__name__)

# Set your own current model string here or via ANTHROPIC_MODEL env var —
# check docs.anthropic.com for the latest available model name before
# deploying this for real; don't assume this default stays current.
DEFAULT_MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-4-5")

LlmCall = Callable[[str, str], str]  # (system_prompt, user_message) -> reply text

_PLAN_FORMAT = """Respond with ONLY a JSON object (no prose, no markdown fences):
{
  "steps": [
    {
      "tool_name": "<a name from the registry>",
      "arguments": { ...matching that tool's input_schema... },
      "reasoning": "<short reason>",
      "expects_single_result": <true if the user asked about ONE record, else false>
    }
  ],
  "clarification_question": "<a question for the user, or null>",
  "final_answer_if_no_tool_needed": "<a direct answer, or null>"
}"""

_PLAN_SYSTEM_PROMPT = """You are the planning node of a permissioned tool-using agent.
Turn the user's request into an ordered list of tool calls.

Rules:
1. Use as few steps as the request needs, at most <<MAX_STEPS>>. Steps run in order.
2. A later step can use data an earlier step returned by writing a placeholder
   such as "{{0.rows.0.name}}" (step number counted from 0, then the path into
   that step's result). Only reference data you expect that step to return.
   Never invent data.
3. Set "expects_single_result": true when the user is clearly asking about ONE
   record (for example "look up the enterprise customer"), so the system can
   ask which one if several match.
4. If the request is too vague to act on safely (for example it names no
   customer), set "clarification_question" and leave "steps" empty. Do not
   guess an identifier, and do not run an unfiltered query to see what comes back.
5. Use only tools from the registry.

<<FORMAT>>

Tool registry:
<<TOOLS>>
"""

_REPLAN_SYSTEM_PROMPT = """You are the planning node of a permissioned tool-using agent.
A step you planned FAILED. You are given the user's request, the steps that
already completed (with their results), the failed step, and the error text.

Produce the steps that should run NEXT, starting with a corrected version of the
failed step (fix a wrong argument, choose a different tool, etc). Do not repeat
steps that already completed. Placeholders like "{{0.rows.0.name}}" may refer to
completed steps. If the error means you need information only the user has,
return no steps and set "clarification_question" instead. At most <<MAX_STEPS>>
steps in total across completed and new steps.

<<FORMAT>>

Tool registry:
<<TOOLS>>
"""

_TICKET_RE = re.compile(r"\b(?:create|file|open|raise|make)\s+(?:a\s+|the\s+)?ticket\b", re.IGNORECASE)
_LOOKUP_RE = re.compile(
    r"\b(?:look ?up|find|search|list|show|get)\b.*\b(?:customer|account)s?\b", re.IGNORECASE
)
_PLAN_WORD_RE = re.compile(r"\b(free|pro|enterprise)\b", re.IGNORECASE)
_STATUS_PATTERNS = [
    (re.compile(r"\bactive\b", re.IGNORECASE), "active"),
    (re.compile(r"\bcancel+ed\b", re.IGNORECASE), "cancelled"),
    (re.compile(r"\bpast[ _-]?due\b", re.IGNORECASE), "past_due"),
]
_MANY_RE = re.compile(r"\b(?:all|every|list)\b|\b(?:customers|accounts)\b", re.IGNORECASE)

_WHICH_CUSTOMER = (
    "Which customer do you mean? Give me a customer ID (like C003) or describe "
    "them (for example 'the enterprise customer')."
)


def _make_anthropic_call(api_key: str) -> LlmCall | None:
    try:
        import anthropic
    except ImportError:
        return None
    client = anthropic.Anthropic(api_key=api_key)

    def call(system: str, user: str) -> str:
        response = client.messages.create(
            model=DEFAULT_MODEL,
            max_tokens=1500,
            system=system,
            messages=[{"role": "user", "content": user}],
        )
        return "".join(b.text for b in response.content if b.type == "text")

    return call


class Planner:
    def __init__(self, tools: list[ToolDefinition], llm_call: LlmCall | None = None):
        self.tools_list = tools
        self.tools_by_name = {t.name: t for t in tools}
        self.llm_call = llm_call

    # ------------------------------------------------------------------ plan
    def plan(self, task_id: str, user_message: str, role: str) -> AgentPlan:
        call = self._get_llm()
        if call is not None:
            plan = self._plan_with_llm(call, task_id, user_message)
            if plan is not None:
                return plan
        return self._plan_rule_based(task_id, user_message)

    # ---------------------------------------------------------------- replan
    def replan(
        self,
        task_id: str,
        user_message: str,
        failed: ToolCall,
        error: str,
        transient: bool,
        expects_single: bool,
        completed: list[dict],
    ) -> AgentPlan | None:
        """Revise the plan after `failed` raised `error`. Returns the steps
        to run next (starting from the failed step), or None if there's
        nothing sensible to try — the graph then fails cleanly."""
        call = self._get_llm()
        if call is not None:
            payload = json.dumps(
                {
                    "user_request": user_message,
                    "completed_steps": completed,
                    "failed_step": failed.model_dump(mode="json"),
                    "error": error,
                }
            )[:6000]
            return self._ask_for_plan(call, self._system(_REPLAN_SYSTEM_PROMPT), payload, task_id)

        # Offline there is no model to revise anything, so the only honest
        # option is to repeat the same call — and only when the error looks
        # temporary. Repeating a call that failed on bad input just fails again.
        if transient:
            return AgentPlan(
                task_id=task_id,
                steps=[failed],
                single_result_steps=[0] if expects_single else [],
            )
        return None

    # -------------------------------------------------------------- LLM mode
    def _get_llm(self) -> LlmCall | None:
        if self.llm_call is not None:
            return self.llm_call
        api_key = os.environ.get("ANTHROPIC_API_KEY")
        return _make_anthropic_call(api_key) if api_key else None

    def _system(self, template: str) -> str:
        tools_json = json.dumps([t.model_dump(mode="json") for t in self.tools_list], indent=2)
        return (
            template.replace("<<FORMAT>>", _PLAN_FORMAT)
            .replace("<<TOOLS>>", tools_json)
            .replace("<<MAX_STEPS>>", str(MAX_PLAN_STEPS))
        )

    def _plan_with_llm(self, call: LlmCall, task_id: str, user_message: str) -> AgentPlan | None:
        return self._ask_for_plan(call, self._system(_PLAN_SYSTEM_PROMPT), user_message, task_id)

    def _ask_for_plan(self, call: LlmCall, system: str, user: str, task_id: str) -> AgentPlan | None:
        for attempt in (1, 2):  # one retry on a bad reply
            try:
                return self._parse_plan(task_id, call(system, user))
            except Exception as exc:  # noqa: BLE001 - API errors and bad JSON alike
                logger.warning("planner LLM attempt %d failed: %s", attempt, exc)
        return None

    def _parse_plan(self, task_id: str, text: str) -> AgentPlan:
        text = re.sub(r"^```(json)?|```$", "", text.strip(), flags=re.MULTILINE).strip()
        data = json.loads(text)
        raw_steps = data.get("steps") or []
        if len(raw_steps) > MAX_PLAN_STEPS:
            raise ValueError(f"plan has {len(raw_steps)} steps, the limit is {MAX_PLAN_STEPS}")

        steps, single = [], []
        for i, raw in enumerate(raw_steps):
            raw = dict(raw)
            if raw.pop("expects_single_result", False):
                single.append(i)
            raw.setdefault("reasoning", "(none given)")
            tool = self.tools_by_name.get(raw.get("tool_name"))
            if tool is not None:
                # Never trust the model's copy of these: the registry decides.
                raw["risk_level"] = tool.risk_level.value
                raw["requires_approval"] = tool.requires_approval
            else:
                raw.setdefault("risk_level", "high")  # unknown tool: assume the worst
                raw.setdefault("requires_approval", True)
            steps.append(ToolCall.model_validate(raw))

        return AgentPlan(
            task_id=task_id,
            steps=steps,
            single_result_steps=single,
            clarification_question=data.get("clarification_question") or None,
            final_answer_if_no_tool_needed=data.get("final_answer_if_no_tool_needed") or None,
        )

    # ------------------------------------------------------ rule-based mode
    def _step(self, tool_name: str, arguments: dict, reasoning: str) -> ToolCall:
        tool = self.tools_by_name[tool_name]
        return ToolCall(
            tool_name=tool_name,
            arguments=arguments,
            reasoning=reasoning,
            risk_level=tool.risk_level,
            requires_approval=tool.requires_approval,
        )

    def _customer_filter(self, message: str) -> tuple[dict, bool]:
        """(filter, expects_single). An ID or a description narrows to one
        customer; 'all'/'list'/plural means the user wants several."""
        id_match = re.search(r"\bC\d{3}\b", message, re.IGNORECASE)
        if id_match:
            return {"customer_id": id_match.group().upper()}, True
        filter_: dict = {}
        plan_match = _PLAN_WORD_RE.search(message)
        if plan_match:
            filter_["plan"] = plan_match.group(1).lower()
        for pattern, value in _STATUS_PATTERNS:
            if pattern.search(message):
                filter_["status"] = value
                break
        return filter_, not _MANY_RE.search(message)

    def _clarify(self, task_id: str, question: str) -> AgentPlan:
        return AgentPlan(task_id=task_id, steps=[], clarification_question=question)

    def _plan_rule_based(self, task_id: str, user_message: str) -> AgentPlan:
        wants_ticket = "create_ticket" in self.tools_by_name and _TICKET_RE.search(user_message)
        wants_lookup = "csv_query" in self.tools_by_name and _LOOKUP_RE.search(user_message)

        title_match = re.search(r"titled? ['\"]([^'\"]+)['\"]", user_message, re.IGNORECASE)
        desc_match = re.search(r"description(?:d)? ['\"]([^'\"]+)['\"]", user_message, re.IGNORECASE)

        # "look up customer X and create a ticket about them": two steps, the
        # ticket reading the customer from step 0's result.
        if wants_ticket and wants_lookup:
            filter_, _ = self._customer_filter(user_message)
            if not filter_:
                return self._clarify(task_id, _WHICH_CUSTOMER)
            title = title_match.group(1) if title_match else "Follow up with {{0.rows.0.name}}"
            description = (
                desc_match.group(1)
                if desc_match
                else "Customer {{0.rows.0.customer_id}} ({{0.rows.0.name}}) is on the "
                "{{0.rows.0.plan}} plan, status: {{0.rows.0.status}}."
            )
            return AgentPlan(
                task_id=task_id,
                steps=[
                    self._step("csv_query", {"dataset": "customers", "filter": filter_},
                               "Find the customer the ticket is about"),
                    self._step("create_ticket", {"title": title, "description": description},
                               "Create the ticket using the customer found in step 0"),
                ],
                single_result_steps=[0],
            )

        if wants_ticket:
            title = title_match.group(1) if title_match else "Untitled ticket"
            description = desc_match.group(1) if desc_match else user_message.strip()
            return AgentPlan(
                task_id=task_id,
                steps=[self._step("create_ticket", {"title": title, "description": description},
                                  f"User asked to create a ticket titled '{title}'")],
            )

        if wants_lookup:
            filter_, expects_single = self._customer_filter(user_message)
            if not filter_ and expects_single:
                return self._clarify(task_id, _WHICH_CUSTOMER)  # don't dump every row
            return AgentPlan(
                task_id=task_id,
                steps=[self._step("csv_query", {"dataset": "customers", "filter": filter_},
                                  f"User asked to look up customers matching {filter_ or 'everything'}")],
                single_result_steps=[0] if expects_single else [],
            )

        if "calculator" in self.tools_by_name and re.search(r"[+\-*/%]", user_message) and re.search(r"\d", user_message):
            expr = re.search(r"[\d\.\s()+\-*/%]{3,}", user_message)
            expression = expr.group().strip() if expr else user_message.strip()
            return AgentPlan(
                task_id=task_id,
                steps=[self._step("calculator", {"expression": expression},
                                  f"User's message contains an arithmetic expression: '{expression}'")],
            )

        if "file_reader" in self.tools_by_name and re.search(
            r"\b(read|open|show me|file|doc|onboarding)\b", user_message, re.IGNORECASE
        ):
            path_match = re.search(r"[\w\-/]+\.(md|txt)", user_message)
            path = path_match.group() if path_match else "onboarding.md"
            return AgentPlan(
                task_id=task_id,
                steps=[self._step("file_reader", {"path": path}, f"User asked to read '{path}'")],
            )

        return AgentPlan(
            task_id=task_id,
            steps=[],
            final_answer_if_no_tool_needed=(
                "I couldn't map this request to a tool. Try a math question, "
                "asking to read a doc, looking up a customer, or creating a ticket."
            ),
        )

    # ------------------------------------------------------------- summaries
    def summarize(self, user_message: str, tool_result: dict) -> str:
        """A deterministic summary of one tool result. Swap for an LLM call
        later if you want more natural phrasing."""
        return f"Here's the result: {tool_result}"

    def summarize_steps(self, user_message: str, step_results: list[dict]) -> str:
        if len(step_results) == 1:
            return self.summarize(user_message, step_results[0]["result"])
        lines = [f"Completed {len(step_results)} steps:"]
        lines += [f"{i + 1}. {r['tool_name']}: {r['result']}" for i, r in enumerate(step_results)]
        return "\n".join(lines)
