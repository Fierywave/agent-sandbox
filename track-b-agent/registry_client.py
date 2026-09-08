"""registry_client — reads the tool registry either from Track A's live
service (GET /tools) or from the Phase 0 stub (contracts.STARTER_TOOLS)
when the live service isn't reachable.

Phase 1 definition of done for Track B explicitly allows building against
the stub. Once Track A's registry is running, pass registry_base_url and
this switches to live data with zero other code changes — that's the
point of building against the contract instead of the implementation.
"""

import logging

from contracts import STARTER_TOOLS, ToolDefinition, ToolRegistryResponse

logger = logging.getLogger(__name__)


def get_tool_registry(registry_base_url: str | None = None) -> list[ToolDefinition]:
    if registry_base_url:
        try:
            import requests

            resp = requests.get(f"{registry_base_url.rstrip('/')}/tools", timeout=5)
            resp.raise_for_status()
            parsed = ToolRegistryResponse.model_validate(resp.json())
            logger.info("Loaded %d tools from live registry at %s", len(parsed.tools), registry_base_url)
            return parsed.tools
        except Exception as exc:  # noqa: BLE001 - fall back deliberately
            logger.warning(
                "Could not reach live registry at %s (%s) — falling back to Phase 0 stub",
                registry_base_url,
                exc,
            )

    return list(STARTER_TOOLS)