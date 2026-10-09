"""demo_live — the whole approval flow, end to end, in one command.

Needs Track A's service running (separate terminal):
    cd track-a-guardrail
    uvicorn main:app --port 8000

Then, from the repo root:
    python track-b-agent/demo_live.py            # approve path
    python track-b-agent/demo_live.py reject     # reject path

What it shows, step by step:
  1. a viewer asks for a high-risk tool          -> denied by Track A's engine
  2. an operator asks for the same tool          -> agent PAUSES, approval filed in Track A
  3. a reviewer (operator) decides in Track A    -> approve or reject
  4. sync_approvals() picks the decision up      -> the paused task resumes and finishes
"""

import os
import sys
import tempfile
import uuid

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(__file__))

import requests

from approval_sync import sync_approvals
from graph import submit_task
from guardrail_client import get_token

URL = os.environ.get("GUARDRAIL_URL", "http://localhost:8000")
REQUEST = "create a ticket titled 'printer broken' with description 'office printer is jammed'"


def main() -> None:
    action = "reject" if len(sys.argv) > 1 and sys.argv[1] == "reject" else "approve"
    run = uuid.uuid4().hex[:6]  # unique ids so the demo can be re-run freely
    db = os.path.join(tempfile.mkdtemp(), "demo.db")

    print("\n[1] viewer asks to create a ticket (high risk)")
    r = submit_task(f"demo-viewer-{run}", "dave", "viewer", REQUEST, registry_base_url=URL, db_path=db)
    print(f"    -> {r['status']}: {r['final_response']}")

    print("\n[2] operator asks for the same thing")
    task_id = f"demo-op-{run}"
    r = submit_task(task_id, "carol", "operator", REQUEST, registry_base_url=URL, db_path=db)
    if r["status"] != "waiting_approval":
        sys.exit(f"    -> expected the task to pause but got '{r['status']}': {r.get('final_response')}\n"
                 f"       (is Track A running at {URL}?)")
    print(f"    -> {r['status']}  (paused; approval filed in Track A: {r['approval_id']})")

    print("\n[3] nothing reviewed yet, so syncing changes nothing")
    for row in sync_approvals(URL, db):
        if row["task_id"] == task_id:
            print(f"    -> {row['outcome']}")

    print(f"\n[4] a reviewer ({action}s) in Track A")
    token = get_token(URL, "reviewer-bob", "operator")
    resp = requests.post(
        f"{URL}/approvals/{r['approval_id']}/{action}",
        headers={"Authorization": f"Bearer {token}"},
        json={"note": "looks fine" if action == "approve" else "not urgent"},
        timeout=5,
    )
    print(f"    -> HTTP {resp.status_code}")

    print("\n[5] sync picks up the decision and resumes the task")
    for row in sync_approvals(URL, db):
        if row["task_id"] == task_id:
            print(f"    -> {row['outcome']} -> task {row['final_status']}")
            print(f"       {row['final_response']}")


if __name__ == "__main__":
    main()
