"""Prompt loading for built-in and user-overridden JarvisOS agents."""

from __future__ import annotations

from pathlib import Path


DEFAULT_PROMPT_DIR = Path(__file__).parent / "prompts"


class PromptLibrary:
    """Loads prompts from configured paths with bundled defaults."""

    def __init__(
        self,
        planner_prompt_path: Path | None = None,
        synthesis_prompt_path: Path | None = None,
        tool_use_prompt_path: Path | None = None,
    ) -> None:
        self._planner_prompt_path = planner_prompt_path
        self._synthesis_prompt_path = synthesis_prompt_path
        self._tool_use_prompt_path = tool_use_prompt_path

    def planner_prompt(self) -> str:
        """Return the planner system prompt."""
        return _read_prompt(
            self._planner_prompt_path,
            DEFAULT_PROMPT_DIR / "planner.md",
        )

    def synthesis_prompt(self) -> str:
        """Return the synthesis agent system prompt."""
        return _read_prompt(
            self._synthesis_prompt_path,
            DEFAULT_PROMPT_DIR / "synthesis.md",
        )

    def tool_use_prompt(self) -> str:
        """Return the ToolUseAgent system prompt."""
        return _read_prompt(
            self._tool_use_prompt_path,
            DEFAULT_PROMPT_DIR / "tool_use.md",
        )


def _read_prompt(override_path: Path | None, default_path: Path) -> str:
    path = override_path or default_path
    return path.read_text(encoding="utf-8").strip()
