import time
from collections import defaultdict

from contracts.enums import Role
from .registry import registry


class SlidingWindowRateLimiter:
    def __init__(self):
        self._calls: dict[str, list[float]] = defaultdict(list)

    def check(self, user_id: str, role: Role, tool_name: str) -> tuple[bool, int]:
        tool = registry.get(tool_name)
        if not tool:
            return True, 0

        limit = tool.rate_limit_per_minute.get(role)
        if limit is None:
            return False, 60

        now = time.time()
        window_start = now - 60.0
        key = f"{role.value}:{user_id}:{tool_name}"

        valid_calls = [t for t in self._calls[key] if t > window_start]
        self._calls[key] = valid_calls

        if len(valid_calls) >= limit:
            retry_after = max(1, int(60.0 - (now - valid_calls[0])))
            return False, retry_after

        self._calls[key].append(now)
        return True, 0

    def reset(self) -> None:
        self._calls.clear()


rate_limiter = SlidingWindowRateLimiter()
