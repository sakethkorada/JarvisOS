"""Compatibility exports for final response synthesis."""

from jarvis.orchestration.synthesizer import (
    SynthesisResult,
    Synthesizer,
    deterministic_summary,
    grounded_result_lines,
)

__all__ = [
    "SynthesisResult",
    "Synthesizer",
    "deterministic_summary",
    "grounded_result_lines",
]
