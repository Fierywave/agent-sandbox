"""approval_sync — turns a human's decision in Track A's service into a
resumed Track B task.

The flow in live mode:
  1. agent hits a high-risk tool  -> graph files an approval in Track A
                                     and pauses (status: waiting_approval)
  2. a reviewer (operator/admin)  -> POST /approvals/{id}/approve|reject
                                     on Track A
  3. sync_approvals() (this file) -> reads Track A's record and resumes
                                     the paused task with that decision

The decision itself lives only in Track A. This code never decides
anything; it reads the record and, before acting on it, checks the record
still describes the exact tool call we paused on (same task, tool and
arguments). If it doesn't, the task is left paused and reported, because
resuming on the strength of a mismatched approval would defeat the point
of having approvals.

Run once from the command line (from inside track-b-agent/):
    python approval_sync.py http://localhost:8000
Call it on a timer, or from a UI refresh, for continuous operation.
"""

import sys

from approval_store import list_pending
from graph import DEFAULT_DB_PATH, resume_task
from guardrail_client import GuardrailError, get_approval


def _same_call(remote: dict, local: dict) -> bool:
    r_call, l_call = remote.get("tool_call", {}), local.get("tool_call", {})
    return (
        remote.get("task_id") == local.get("task_id")
        and r_call.get("tool_name") == l_call.get("tool_name")
        and r_call.get("arguments") == l_call.get("arguments")
    )


def sync_approvals(base_url: str, db_path: str = DEFAULT_DB_PATH) -> list[dict]:
    """One pass over every task we have paused. Returns one result dict per
    task examined: {task_id, approval_id, outcome, ...} where outcome is one of
    'still_pending', 'approved', 'rejected', 'mismatch', 'unreachable'."""
    results = []
    for local in list_pending():
        task_id, approval_id = local["task_id"], local.get("approval_id")
        if not approval_id:
            continue  # offline-mode task: resolved by calling resume_task directly

        try:
            remote = get_approval(base_url, approval_id)
        except GuardrailError as exc:
            results.append({"task_id": task_id, "approval_id": approval_id,
                            "outcome": "unreachable", "error": str(exc)})
            continue

        if remote["status"] == "pending":
            results.append({"task_id": task_id, "approval_id": approval_id,
                            "outcome": "still_pending"})
            continue

        if not _same_call(remote, local):
            results.append({"task_id": task_id, "approval_id": approval_id,
                            "outcome": "mismatch"})
            continue

        approved = remote["status"] == "approved"
        final = resume_task(
            task_id,
            approved=approved,
            approved_by=remote.get("resolved_by") or "unknown",
            reason=remote.get("resolution_note") or "",
            registry_base_url=base_url,
            db_path=db_path,
        )
        results.append({"task_id": task_id, "approval_id": approval_id,
                        "outcome": remote["status"], "final_status": final["status"],
                        "final_response": final.get("final_response")})
    return results


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("usage: python approval_sync.py <guardrail-base-url>")
    for row in sync_approvals(sys.argv[1]):
        print(row)
