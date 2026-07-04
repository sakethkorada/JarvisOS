"""Structured runtime errors for JarvisOS boundaries."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ErrorDetails:
    """Serializable details for trace events and user-facing diagnostics."""

    error_type: str
    component: str
    message: str
    recoverable: bool = True


class JarvisError(Exception):
    """Base runtime error for JarvisOS."""

    def __init__(
        self,
        message: str,
        *,
        component: str,
        recoverable: bool = True,
    ) -> None:
        super().__init__(message)
        self.details = ErrorDetails(
            error_type=self.__class__.__name__,
            component=component,
            message=message,
            recoverable=recoverable,
        )

    def to_trace_data(self) -> dict[str, object]:
        """Return a serializable error payload for trace storage."""
        return {
            "error_type": self.details.error_type,
            "component": self.details.component,
            "message": self.details.message,
            "recoverable": self.details.recoverable,
        }


class ModelProviderError(JarvisError):
    """Raised when a model provider fails."""


class ToolExecutionError(JarvisError):
    """Raised when a tool handler fails."""


class PluginLoadError(JarvisError):
    """Raised when plugin loading fails."""


class ConfigError(JarvisError):
    """Raised when settings are invalid."""
