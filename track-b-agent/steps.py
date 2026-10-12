"""steps — the pure logic behind multi-tool tasks and clarification.

1. Passing data between steps. A later step's arguments may contain
   placeholders like  {{0.rows.0.name}}  = "from step 0's result, the
   `name` of the first row". They are resolved by THIS code from the typed
   results of steps that already ran — the model never copies data
   between steps itself, and the resolved (concrete) call is what gets
   permission-checked and shown to a human approver.

   Resolution is a single pass: text inserted from a result is never
   scanned for placeholders again, so data returned by a tool (e.g. a CSV
   cell containing '{{0.rows.1.email}}') cannot make the system pull in
   anything else.

2. Ambiguity. When the user asked for ONE record and a lookup returns
   several (or none), that is a question for the user, not a guess.
"""

import re
from typing import Any

_PLACEHOLDER = re.compile(r"\{\{\s*(\d+)((?:\.[A-Za-z0-9_]+)+)\s*\}\}")

MAX_SHOWN_CANDIDATES = 5


class UnresolvedReferenceError(ValueError):
    """A placeholder points at something that doesn't exist."""


# --- placeholders -------------------------------------------------------------

def resolve_arguments(arguments: dict[str, Any], step_results: list[dict]) -> dict[str, Any]:
    """Return a copy of `arguments` with every placeholder replaced by the
    value it points at in `step_results` (a list of StepResult dicts)."""
    by_step = {r["step"]: r["result"] for r in step_results}
    return {key: _resolve_value(value, by_step) for key, value in arguments.items()}


def _resolve_value(value: Any, by_step: dict[int, dict]) -> Any:
    if isinstance(value, str):
        return _resolve_string(value, by_step)
    if isinstance(value, dict):
        return {k: _resolve_value(v, by_step) for k, v in value.items()}
    if isinstance(value, list):
        return [_resolve_value(v, by_step) for v in value]
    return value


def _resolve_string(text: str, by_step: dict[int, dict]) -> Any:
    whole = _PLACEHOLDER.fullmatch(text.strip())
    if whole:
        # The whole string is one placeholder: keep the real type (a number
        # stays a number) instead of turning it into text.
        return _lookup(by_step, int(whole.group(1)), whole.group(2))
    return _PLACEHOLDER.sub(
        lambda m: str(_lookup(by_step, int(m.group(1)), m.group(2))), text
    )


def _lookup(by_step: dict[int, dict], step: int, path: str) -> Any:
    if step not in by_step:
        raise UnresolvedReferenceError(
            f"'{{{{{step}{path}}}}}' refers to step {step}, which hasn't produced a result"
        )
    current: Any = by_step[step]
    for token in path.strip(".").split("."):
        try:
            if isinstance(current, dict):
                current = current[token]
            elif isinstance(current, list):
                current = current[int(token)]
            else:
                raise KeyError(token)
        except (KeyError, IndexError, ValueError):
            raise UnresolvedReferenceError(
                f"'{{{{{step}{path}}}}}' doesn't exist in step {step}'s result"
            ) from None
    return current


# --- ambiguity ----------------------------------------------------------------

def check_single_result(arguments: dict[str, Any], result: dict[str, Any]) -> str | None:
    """For a step where the user asked for ONE record: return a clarifying
    question if the result has zero or several rows, else None."""
    rows = result.get("rows")
    if not isinstance(rows, list) or len(rows) == 1:
        return None

    what = _describe_filter(arguments.get("filter"))
    if not rows:
        return (
            f"I couldn't find anything matching {what}. "
            "Could you check the details, or give me an ID (like C003)?"
        )

    shown = "; ".join(_label(r) for r in rows[:MAX_SHOWN_CANDIDATES])
    more = f" (and {len(rows) - MAX_SHOWN_CANDIDATES} more)" if len(rows) > MAX_SHOWN_CANDIDATES else ""
    first_id = next((str(v) for k, v in rows[0].items() if k == "customer_id"), None)
    hint = f" For example: 'look up customer {first_id}'." if first_id else ""
    return (
        f"I found {len(rows)} records matching {what}: {shown}{more}. "
        f"Which one did you mean? Reply with the ID.{hint}"
    )


def _describe_filter(filter_: Any) -> str:
    if not isinstance(filter_, dict) or not filter_:
        return "that request"
    return ", ".join(f"{k} = {v!r}" for k, v in filter_.items())


def _label(row: dict) -> str:
    """A short human label for a row: its id and name when it has them."""
    keys = [k for k in row if k.endswith("_id") or k == "name"]
    values = [str(row[k]) for k in keys] or [str(v) for v in list(row.values())[:2]]
    return " ".join(values)
