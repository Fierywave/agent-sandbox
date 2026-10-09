"""guardrail_client — every live HTTP call from Track B to Track A's
guardrail service lives here, so the graph never touches `requests`
directly and there's exactly one place to change if his API changes.

Endpoints used (all from Track A's Phase 2 code):
  POST /auth/token            -> mint a JWT for (user_id, role)
  POST /permissions/check     -> the 6-step deterministic permission engine
  POST /approvals             -> file a pending human-approval request
  GET  /approvals/{id}        -> read its current status

Failure policy: if the guardrail service can't be reached or answers
with something unexpected, this module raises GuardrailError and the
CALLER decides what to do. graph.py's rule is fail CLOSED — an
unreachable guardrail means the tool call is denied, never silently
allowed by a local copy of the policy.
"""

import requests

from contracts import ToolCall

TIMEOUT_SECONDS = 5


class GuardrailError(Exception):
    """The guardrail service was unreachable or returned an unusable response."""


def _base(url: str) -> str:
    return url.rstrip("/")


def get_token(base_url: str, user_id: str, role: str) -> str:
    """DEV-MODE ONLY: Track A's POST /auth/token has no authentication, so
    anyone can mint a token for any role. Fine locally; before anything
    real, the caller's own token should be passed in instead of minted."""
    try:
        resp = requests.post(
            f"{_base(base_url)}/auth/token",
            json={"user_id": user_id, "role": role},
            timeout=TIMEOUT_SECONDS,
        )
        resp.raise_for_status()
        return resp.json()["access_token"]
    except (requests.RequestException, KeyError, ValueError) as exc:
        raise GuardrailError(f"could not obtain a token: {exc}") from exc


def check_permission_live(
    base_url: str,
    token: str,
    tool_call: ToolCall,
    role: str,
    task_id: str | None = None,
) -> dict:
    """POST /permissions/check. Always returns the documented response dict:
    {decision, tool_name, user_role, risk_level, requires_approval, reason}.

    His endpoint signals a rate limit with HTTP 429 (not a normal body),
    and bad/expired tokens with 401/403 — all three are translated here
    into the same dict shape so the graph has one thing to handle.
    """
    try:
        resp = requests.post(
            f"{_base(base_url)}/permissions/check",
            headers={"Authorization": f"Bearer {token}"},
            # task_id lets his audit log attribute the event to this task.
            # Track A's endpoint doesn't read it yet (events currently get
            # task_id='default-task'); it's ignored harmlessly until it does.
            params={"task_id": task_id} if task_id else None,
            json=tool_call.model_dump(mode="json"),
            timeout=TIMEOUT_SECONDS,
        )
    except requests.RequestException as exc:
        raise GuardrailError(f"permission service unreachable: {exc}") from exc

    def _blocked(decision: str, reason: str) -> dict:
        return {
            "decision": decision,
            "tool_name": tool_call.tool_name,
            "user_role": role,
            "risk_level": tool_call.risk_level.value,
            "requires_approval": False,
            "reason": reason,
        }

    if resp.status_code == 429:
        return _blocked("rate_limited", _detail(resp))
    if resp.status_code in (401, 403):
        return _blocked("denied", f"Not authorized: {_detail(resp)}")
    if not resp.ok:
        raise GuardrailError(f"permission check failed: HTTP {resp.status_code}: {_detail(resp)}")

    try:
        return resp.json()
    except ValueError as exc:
        raise GuardrailError(f"permission service returned invalid JSON: {exc}") from exc


def create_approval(
    base_url: str, token: str, task_id: str, tool_call: dict, reason: str
) -> dict:
    """POST /approvals -> the created ApprovalRecord (its `id` is what we
    track). `tool_call` is the dict form of a contracts.ToolCall."""
    try:
        resp = requests.post(
            f"{_base(base_url)}/approvals",
            headers={"Authorization": f"Bearer {token}"},
            json={"task_id": task_id, "tool_call": tool_call, "reason": reason},
            timeout=TIMEOUT_SECONDS,
        )
        resp.raise_for_status()
        return resp.json()
    except (requests.RequestException, ValueError) as exc:
        raise GuardrailError(f"could not create approval request: {exc}") from exc


def get_approval(base_url: str, approval_id: str) -> dict:
    """GET /approvals/{id} -> ApprovalRecord with status pending|approved|rejected."""
    try:
        resp = requests.get(
            f"{_base(base_url)}/approvals/{approval_id}", timeout=TIMEOUT_SECONDS
        )
        resp.raise_for_status()
        return resp.json()
    except (requests.RequestException, ValueError) as exc:
        raise GuardrailError(f"could not read approval {approval_id}: {exc}") from exc


def _detail(resp: requests.Response) -> str:
    try:
        return str(resp.json().get("detail", resp.text))
    except ValueError:
        return resp.text
