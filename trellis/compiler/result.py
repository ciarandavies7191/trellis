"""CompilerResult — the output of a successful compilation."""

from __future__ import annotations

from dataclasses import dataclass, field

from trellis.models.pipeline import Pipeline
from trellis.models.plan import Plan


@dataclass
class CompilerResult:
    """
    Result of a successful PipelineCompiler.compile() call.

    Attributes:
        yaml_text:      The validated YAML string (code fences stripped).
        pipeline:       Parsed Pipeline object, or None if a Plan was produced.
        plan:           Parsed Plan object, or None if a Pipeline was produced.
        attempts:       Total LLM calls made (1 = first-try success; >1 = repairs needed).
        repair_history: List of (broken_yaml, error_message) pairs from failed attempts.
    """

    yaml_text: str
    pipeline: Pipeline | None = None
    plan: Plan | None = None
    attempts: int = 1
    repair_history: list[tuple[str, str]] = field(default_factory=list)

    @property
    def artifact(self) -> Pipeline | Plan:
        """Return the compiled artifact regardless of type."""
        if self.pipeline is not None:
            return self.pipeline
        if self.plan is not None:
            return self.plan
        raise RuntimeError("CompilerResult contains neither a Pipeline nor a Plan")

    @property
    def is_pipeline(self) -> bool:
        return self.pipeline is not None

    @property
    def is_plan(self) -> bool:
        return self.plan is not None
