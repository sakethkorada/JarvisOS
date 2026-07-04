"""Tool registry and execution compatibility layer."""

from __future__ import annotations

from jarvis.contracts import (
    AvailableTool,
    ContextToolHandler,
    ToolCall,
    ToolExecutionContext,
    ToolHandler,
    ToolResult,
    ToolSpec,
)


class ToolRegistry:
    """In-memory registry that maps tool specs to executable handlers."""

    def __init__(self) -> None:
        self._specs: dict[str, ToolSpec] = {}
        self._handlers: dict[str, ToolHandler] = {}
        self._context_handlers: dict[str, ContextToolHandler] = {}

    def register(self, spec: ToolSpec, handler: ToolHandler) -> None:
        """Register or replace a tool and its handler."""
        self._specs[spec.name] = spec
        self._handlers[spec.name] = handler
        self._context_handlers.pop(spec.name, None)

    def register_contextual(
        self,
        spec: ToolSpec,
        handler: ContextToolHandler,
    ) -> None:
        """Register a tool handler that needs runtime execution context."""
        self._specs[spec.name] = spec
        self._context_handlers[spec.name] = handler
        self._handlers.pop(spec.name, None)

    def get(self, name: str) -> ToolSpec:
        """Return a tool specification by name."""
        try:
            return self._specs[name]
        except KeyError as exc:
            raise KeyError(f"Unknown tool: {name}") from exc

    def has(self, name: str) -> bool:
        """Return whether a tool is registered."""
        return name in self._specs

    def execute(
        self,
        call: ToolCall,
        context: ToolExecutionContext | None = None,
    ) -> ToolResult:
        """Execute a registered tool call and normalize failures."""
        spec = self.get(call.tool_name)
        try:
            if spec.name in self._context_handlers:
                if context is None:
                    raise ValueError(f"{spec.name} requires execution context.")
                output = self._context_handlers[spec.name](call.arguments, context)
            else:
                output = self._handlers[spec.name](call.arguments)
            return ToolResult(tool_name=spec.name, output=output)
        except Exception as exc:  # pragma: no cover - defensive boundary
            return ToolResult(
                tool_name=spec.name,
                output={},
                success=False,
                error=str(exc),
            )

    def list(self) -> list[ToolSpec]:
        """Return registered tools in stable display order."""
        return sorted(self._specs.values(), key=lambda tool: tool.name)

    def available_tools(self) -> tuple[AvailableTool, ...]:
        """Return planner-safe metadata for registered tools."""
        return tuple(
            AvailableTool(
                name=tool.name,
                description=tool.description,
                risk_level=tool.risk_level,
                requires_approval=tool.requires_approval,
                source=tool.source,
            )
            for tool in self.list()
        )
