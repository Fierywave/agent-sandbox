"""approval_store — a small JSON-backed index of tasks paused for approval.

This is NOT what makes pause/resume actually work — that's LangGraph's own
checkpointer (see graph.py, SqliteSaver). It answers "which paused tasks
are we tracking?", and in live mode it holds the link between our task and
Track A's approval record: `approval_id` is the id of the record in Track
A's POST /approvals. approval_sync.py reads that link to know which of
Track A's approvals to poll for each paused task.

Track A's service remains the source of truth for the human's decision;
this file only remembers which of its records belongs to which task.
"""

import json
from datetime import datetime, timezone
from pathlib import Path

_STORE_PATH = Path(__file__).parent / "pending_approvals.json"


def _read_all() -> dict:
    if not _STORE_PATH.exists():
        return {}
    with open(_STORE_PATH, encoding="utf-8") as f:
        return json.load(f)


def _write_all(data: dict) -> None:
    with open(_STORE_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


def add_pending(
    task_id: str,
    user_id: str,
    role: str,
    tool_call: dict,
    reason: str,
    approval_id: str | None = None,
) -> None:
    data = _read_all()
    data[task_id] = {
        "task_id": task_id,
        "approval_id": approval_id,
        "user_id": user_id,
        "role": role,
        "tool_call": tool_call,
        "reason": reason,
        "submitted_at": datetime.now(timezone.utc).isoformat(),
    }
    _write_all(data)


def remove_pending(task_id: str) -> None:
    data = _read_all()
    data.pop(task_id, None)
    _write_all(data)


def list_pending() -> list[dict]:
    return list(_read_all().values())


def get_pending(task_id: str) -> dict | None:
    return _read_all().get(task_id)