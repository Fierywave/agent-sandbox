"""planner — reads the tool registry and emits a validated contracts.Plan.

Two modes:
  - LLM mode: used when ANTHROPIC_API_KEY is set. Calls the model, parses
    its JSON response into Plan, retries once on parse failure per the
    Phase 1 spec, then falls back to rule-based rather than crashing the
    graph.
  - Rule-based mode: used when no API key is present (e.g. CI, or your
    teammate running tests without a key). Deterministic, so tests are
    reproducible without network access or cost.

Both modes only ever produce a `contracts.Plan` — nothing downstream cares
which mode produced it.
"""

import json
import os
import re

from contracts import Plan, ToolCall, ToolDefinition

# Set your own current model string here or via ANTHROPIC_MODEL env var —
# check docs.anthropic.com for the latest available model name before
# deploying this for real; don't assume this default stays current.
DEFAULT_MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-4-5")

_PLAN_SYSTEM_PROMPT = """You are the planning node of a permissioned tool-using agent.
Given a tool registry and a user request, decide whether a tool call is
needed and, if so, exactly one tool call to make.

Respond with ONLY a JSON object, no prose, no markdown fences, matching:
{{
  "steps": [
    {{
      "tool_name": "<one of the registered tool names, or omit entirely if no tool is needed>",
      "arguments": {{...matching that tool's input_schema...}},
      "reasoning": "<short reason>",
      "risk_level": "<low|medium|high, copied from the tool's registry entry>",
      "requires_approval": <bool, copied from the tool's registry entry>
    }}
  ],
  "final_answer_if_no_tool_needed": "<string, or null if a tool call is included above>"
}}

Tool registry:
{tools_json}
"""


class Planner:
    def __init__(self, tools: list[ToolDefinition]):
        self.tools_list = tools
        self.tools_by_name = {t.name: t for t in tools}

    def plan(self, task_id: str, user_message: str, role: str) -> Plan:
        api_key = os.environ.get("ANTHROPIC_API_KEY")
        if api_key:
            plan = self._plan_with_llm(task_id, user_message, api_key)
            if plan is not None:
                return plan
        return self._plan_rule_based(task_id, user_message)

    # -- LLM mode -----------------------------------------------------
    def _plan_with_llm(self, task_id: str, user_message: str, api_key: str) -> Plan | None:
        try:
            import anthropic
        except ImportError:
            return None

        client = anthropic.Anthropic(api_key=api_key)
        tools_json = json.dumps(
            [t.model_dump(mode="json") for t in self.tools_list], indent=2
        )
        system = _PLAN_SYSTEM_PROMPT.format(tools_json=tools_json)

        last_error = None
        for _attempt in range(2):  # one retry on parse failure, per spec
            response = client.messages.create(
                model=DEFAULT_MODEL,
                max_tokens=1000,
                system=system,
                messages=[{"role": "user", "content": user_message}],
            )
            text = "".join(
                block.text for block in response.content if block.type == "text"
            ).strip()
            text = re.sub(r"^```(json)?|```$", "", text.strip(), flags=re.MULTILINE).strip()
            try:
                data = json.loads(text)
                data["task_id"] = task_id
                return Plan.model_validate(data)
            except Exception as exc:  # noqa: BLE001
                last_error = exc
                continue
        # both attempts failed to parse -> caller falls back to rule-based
        del last_error
        return None

    # -- Offline / rule-based fallback ---------------------------------
    def _plan_rule_based(self, task_id: str, user_message: str) -> Plan:
        expr_match = re.search(r"[\d][\d\.\s()+\-*/%]*\d|\d", user_message)
        has_math_op = bool(re.search(r"[+\-*/%]", user_message))
        if "calculator" in self.tools_by_name and has_math_op and expr_match:
            expr = re.search(r"[\d\.\s()+\-*/%]{3,}", user_message)
            expression = expr.group().strip() if expr else user_message.strip()
            tool = self.tools_by_name["calculator"]
            step = ToolCall(
                tool_name="calculator",
                arguments={"expression": expression},
                reasoning=f"User's message contains an arithmetic expression: '{expression}'",
                risk_level=tool.risk_level,
                requires_approval=tool.requires_approval,
            )
            return Plan(task_id=task_id, steps=[step])

        if "file_reader" in self.tools_by_name and re.search(
            r"\b(read|open|show me|file|doc|onboarding)\b", user_message, re.IGNORECASE
        ):
            path_match = re.search(r"[\w\-/]+\.(md|txt)", user_message)
            path = path_match.group() if path_match else "onboarding.md"
            tool = self.tools_by_name["file_reader"]
            step = ToolCall(
                tool_name="file_reader",
                arguments={"path": path},
                reasoning=f"User asked to read '{path}'",
                risk_level=tool.risk_level,
                requires_approval=tool.requires_approval,
            )
            return Plan(task_id=task_id, steps=[step])

        return Plan(
            task_id=task_id,
            steps=[],
            final_answer_if_no_tool_needed=(
                "I couldn't map this request to a tool. Phase 1 only wires up "
                "calculator and file_reader — try asking a math question or "
                "asking to read a doc."
            ),
        )

    def summarize(self, user_message: str, tool_result: dict) -> str:
        """Phase 1: a deterministic summary of the tool result. Swap for an
        LLM call in a later phase if you want more natural phrasing."""
        return f"Here's the result: {tool_result}"