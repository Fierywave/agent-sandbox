"""trace_publisher — sends a task's history to Track A's trace API.

Telemetry must never change what the task does, so nothing in here raises
and nothing here re-runs a tool. Rules from Track A's handoff:
  * 200            -> done
  * 409 / 422      -> NOT retried: the snapshot itself is the problem
  * 401/403/404    -> NOT retried: retrying the same request can't fix it
  * timeouts, connection errors, 5xx, 429 -> retried a couple of times
"""

import logging
import time

import requests

logger = logging.getLogger(__name__)

TIMEOUT_SECONDS = 5
NO_RETRY = {401, 403, 404, 409, 422}


def publish_trace(
    base_url: str,
    token: str,
    task_id: str,
    status: str,
    spans: list[dict],
    retries: int = 2,
    backoff_seconds: float = 0.3,
) -> dict:
    """POST one full snapshot. Returns {'ok': bool, 'status_code'?, 'error'?}."""
    url = f"{base_url.rstrip('/')}/trace/{task_id}"
    last_error = "unknown error"

    for attempt in range(retries + 1):
        try:
            resp = requests.post(
                url,
                headers={"Authorization": f"Bearer {token}"},
                json={"status": status, "spans": spans},
                timeout=TIMEOUT_SECONDS,
            )
        except Exception as exc:  # noqa: BLE001 - telemetry must never raise
            last_error = str(exc)
        else:
            if resp.status_code == 200:
                return {"ok": True, "status_code": 200}
            last_error = f"HTTP {resp.status_code}: {_detail(resp)}"
            if resp.status_code in NO_RETRY:
                logger.warning("trace for %s not accepted (not retrying): %s", task_id, last_error)
                return {"ok": False, "status_code": resp.status_code, "error": last_error}

        if attempt < retries:
            time.sleep(backoff_seconds * (attempt + 1))

    logger.warning("trace for %s could not be delivered: %s", task_id, last_error)
    return {"ok": False, "error": last_error}


def fetch_trace(base_url: str, token: str, task_id: str) -> dict | None:
    """GET /trace/{task_id}. Returns the trace dict, or None if it isn't
    there (or can't be reached) — the UI then falls back to local history."""
    try:
        resp = requests.get(
            f"{base_url.rstrip('/')}/trace/{task_id}",
            headers={"Authorization": f"Bearer {token}"},
            timeout=TIMEOUT_SECONDS,
        )
        return resp.json() if resp.status_code == 200 else None
    except Exception:  # noqa: BLE001
        return None


def _detail(resp: requests.Response) -> str:
    try:
        return str(resp.json().get("detail", resp.text))
    except Exception:  # noqa: BLE001
        return resp.text