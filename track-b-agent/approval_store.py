"""approval_store — a small JSON-backed index of pending approvals.

This is NOT what makes pause/resume actually work — that's LangGraph's own
checkpointer (see graph.py, SqliteSaver). This file exists only so a human
reviewer (or a CLI/UI) can answer "what's waiting for approval right now?"
without having to inspect LangGraph's internal checkpoint tables directly.

Phase 3 swaps this for Track A's real approvals backend/table. Same idea,
just centralized on their side instead of a local JSON file here.
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


def add_pending(task_id: str, user_id: str, role: str, tool_call: dict, reason: str) -> None:
    data = _read_all()
    data[task_id] = {
        "task_id": task_id,
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