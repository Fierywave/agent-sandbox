"""create_ticket tool — high risk, requires approval.

Mocks a ticketing API. Never called directly by tool_execution unless the
permission check already returned decision=allowed for this specific call
— for create_ticket that only happens after a human approves it in
approval_wait (Phase 2). This file has no awareness of approval state on
purpose: enforcing that is the graph's job, not the tool's.
"""

import uuid


def run(title: str, description: str, priority: str = "normal") -> dict:
    """Contract: input_schema={title: str, description: str, priority?: str}
    -> output_schema={ticket_id: str}

    Mock implementation — no real ticketing system is called. Returns a
    fake ticket_id so the graph and UI can demo the full flow end-to-end.
    """
    if priority not in {"low", "normal", "high", "urgent"}:
        raise ValueError(f"Invalid priority '{priority}'")
    ticket_id = f"TCK-{uuid.uuid4().hex[:8].upper()}"
    return {
        "ticket_id": ticket_id,
        "title": title,
        "description": description,
        "priority": priority,
        "status": "open",
    }