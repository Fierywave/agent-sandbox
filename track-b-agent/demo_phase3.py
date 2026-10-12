"""demo_phase3 — the three Phase 3 behaviours, offline (Track A not needed).

Run from the repo root:
    python track-b-agent/demo_phase3.py
"""

import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(__file__))

import graph
from agent_types import TransientToolError
from graph import resume_task, submit_task

DB = os.path.join(tempfile.mkdtemp(), "demo.db")


def ask(task_id, role, message):
    return submit_task(task_id, "demo-user", role, message, db_path=DB)


print("\n[1] Ambiguous request -> the agent asks instead of guessing")
r = ask("d1", "analyst", "look up the enterprise customer")
print(f"    status:   {r['status']}")
print(f"    response: {r['final_response']}")

print("\n[2] Same customers, but the user asked for a list -> no question needed")
r = ask("d2", "analyst", "list all enterprise customers")
print(f"    status:   {r['status']}  ({len(r['tool_result']['rows'])} rows returned)")

print("\n[3] Two tools in one task: look up a customer, then create a ticket about them")
r = ask("d3", "operator", "look up customer C003 and create a ticket about them")
print(f"    status:   {r['status']}  (paused before the high-risk ticket step)")
print(f"    ticket title the approver sees: {r['tool_call']['arguments']['title']!r}")
done = resume_task("d3", approved=True, approved_by="demo-boss", db_path=DB)
print(f"    after approval: {done['status']}, ticket {done['tool_result']['ticket_id']} created")

real_calculator = graph.TOOL_IMPLEMENTATIONS["calculator"]


def make_flaky(fail_times):
    calls = {"n": 0}

    def flaky(**kwargs):
        calls["n"] += 1
        if calls["n"] <= fail_times:
            raise TransientToolError("simulated timeout")
        return real_calculator(**kwargs)

    return flaky, calls


print("\n[4] A tool fails once -> exactly one automatic retry, then it works")
flaky, calls = make_flaky(1)
graph.TOOL_IMPLEMENTATIONS["calculator"] = flaky
r = ask("d4", "analyst", "what is 6 * 7")
print(f"    status:   {r['status']}, answer {r['tool_result']['result']}, tool called {calls['n']} times")

print("\n[5] A tool that keeps failing -> one retry, then a clear failure (no endless loop)")
flaky, calls = make_flaky(99)
graph.TOOL_IMPLEMENTATIONS["calculator"] = flaky
r = ask("d5", "analyst", "what is 6 * 7")
print(f"    status:   {r['status']}, tool called {calls['n']} times")
print(f"    response: {r['final_response']}")