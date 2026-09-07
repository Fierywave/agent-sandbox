import os
import sys

from fastapi import APIRouter, HTTPException, status

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from contracts.tool_registry import (
    STARTER_TOOLS,
    ToolDefinition,
    ToolRegistryResponse,
)

router = APIRouter(tags=["Registry"])


class ToolRegistry:
    def __init__(self, seed_tools: list[ToolDefinition] | None = None):
        self._tools: dict[str, ToolDefinition] = {}
        if seed_tools:
            for tool in seed_tools:
                self.register(tool)

    def register(self, tool: ToolDefinition) -> None:
        self._tools[tool.name] = tool

    def get(self, name: str) -> ToolDefinition | None:
        return self._tools.get(name)

    def list_all(self) -> list[ToolDefinition]:
        return list(self._tools.values())

    def to_response(self) -> ToolRegistryResponse:
        return ToolRegistryResponse(tools=self.list_all())


registry = ToolRegistry(seed_tools=STARTER_TOOLS)


def get_registry() -> ToolRegistry:
    return registry


@router.get("/tools", response_model=ToolRegistryResponse)
def get_tools() -> ToolRegistryResponse:
    return registry.to_response()


@router.get("/tools/{tool_name}", response_model=ToolDefinition)
def get_tool_by_name(tool_name: str) -> ToolDefinition:
    tool = registry.get(tool_name)
    if not tool:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Tool '{tool_name}' not found in registry.",
        )
    return tool
