"""PipelineCompiler — compile natural-language prompts into validated pipeline YAML."""

from __future__ import annotations

import os
import re
from typing import Any

import litellm
import yaml
from pydantic import ValidationError as PydanticValidationError

from trellis.models.pipeline import Pipeline
from trellis.models.plan import Plan
from trellis.tools.registry import AsyncToolRegistry, build_default_registry
from trellis.validation.graph import pipeline_execution_waves

from .catalog import build_tool_catalog
from .exceptions import CompilerError
from .prompts import build_repair_prompt, build_system_prompt
from .result import CompilerResult

# Default model: TRELLIS_COMPILER_MODEL → TRELLIS_LLM_MODEL → fallback
_DEFAULT_MODEL: str = os.getenv(
    "TRELLIS_COMPILER_MODEL",
    os.getenv("TRELLIS_LLM_MODEL", "openai/gpt-4o-mini"),
)

# Strip optional markdown code fences that some LLMs add despite instructions.
_FENCE_RE = re.compile(r"^```[a-z]*\n?(.*?)```\s*$", re.DOTALL)


def _strip_fences(text: str) -> str:
    """Remove surrounding markdown code fences if present."""
    stripped = text.strip()
    m = _FENCE_RE.match(stripped)
    return m.group(1).strip() if m else stripped


def _parse_and_validate(text: str) -> tuple[Pipeline | None, Plan | None, str]:
    """
    Parse and structurally validate a YAML string as a Pipeline or Plan.

    Also runs pipeline_execution_waves() to catch cycle errors.

    Returns:
        (pipeline, None, clean_yaml) or (None, plan, clean_yaml)

    Raises:
        ValueError / PydanticValidationError / CycleError on any validation failure.
    """
    clean = _strip_fences(text)

    doc = yaml.safe_load(clean)
    if not isinstance(doc, dict):
        raise ValueError(
            f"Expected a YAML mapping at the top level, got {type(doc).__name__!r}."
        )

    if "pipeline" in doc:
        pipeline = Pipeline.from_yaml(clean)
        pipeline_execution_waves(pipeline)  # catches cycles
        return pipeline, None, clean

    if "plan" in doc:
        plan = Plan.from_yaml(clean)
        return None, plan, clean

    raise ValueError(
        f"YAML must have a top-level `pipeline:` or `plan:` key. "
        f"Got: {list(doc.keys())}"
    )


class PipelineCompiler:
    """
    Compile natural-language descriptions into validated Trellis pipeline YAML.

    The compiler calls an LLM with a system prompt that includes the full DSL
    specification and a live tool catalog derived from the registry.  If the
    first response fails validation, the compiler re-prompts with the error
    context and tries again, up to *max_repair_attempts* additional times.

    Args:
        registry:  Tool registry used to build the tool catalog embedded in the
                   system prompt.  Defaults to build_default_registry().
        model:     litellm model string for compilation calls.  Falls back to
                   TRELLIS_COMPILER_MODEL → TRELLIS_LLM_MODEL → "openai/gpt-4o-mini".

    Example::

        compiler = PipelineCompiler()
        result = await compiler.compile(
            "Fetch Apple's latest 10-K from SEC EDGAR and summarize key risks."
        )
        print(result.yaml_text)
    """

    def __init__(
        self,
        registry: AsyncToolRegistry | None = None,
        *,
        model: str | None = None,
    ) -> None:
        self._registry = registry or build_default_registry()
        self._model = model or _DEFAULT_MODEL
        self._system_prompt: str | None = None  # built lazily and cached

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _get_system_prompt(self) -> str:
        if self._system_prompt is None:
            catalog = build_tool_catalog(self._registry)
            self._system_prompt = build_system_prompt(catalog)
        return self._system_prompt

    async def _call_llm(self, messages: list[dict[str, Any]], model: str) -> str:
        resp = await litellm.acompletion(model=model, messages=messages)
        return resp.choices[0].message.content or ""

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def compile(
        self,
        prompt: str,
        *,
        model: str | None = None,
        max_repair_attempts: int = 2,
    ) -> CompilerResult:
        """
        Compile a natural-language description into a validated Pipeline or Plan.

        Args:
            prompt:               Natural-language description of the desired pipeline.
            model:                litellm model override for this call.
            max_repair_attempts:  How many times to re-prompt after a validation failure
                                  before raising CompilerError.  0 means no repair (fail fast).

        Returns:
            CompilerResult containing the validated artifact and compilation metadata.

        Raises:
            CompilerError: all attempts exhausted without producing valid output.
        """
        effective_model = model or self._model
        system_prompt = self._get_system_prompt()

        messages: list[dict[str, Any]] = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": prompt},
        ]

        repair_history: list[tuple[str, str]] = []
        total_attempts = max_repair_attempts + 1  # initial + repairs

        for attempt in range(1, total_attempts + 1):
            raw = await self._call_llm(messages, effective_model)

            try:
                pipeline, plan, clean_yaml = _parse_and_validate(raw)
                return CompilerResult(
                    yaml_text=clean_yaml,
                    pipeline=pipeline,
                    plan=plan,
                    attempts=attempt,
                    repair_history=repair_history,
                )
            except Exception as exc:
                error_msg = str(exc)
                repair_history.append((raw, error_msg))

                if attempt == total_attempts:
                    raise CompilerError(
                        f"Compilation failed after {attempt} attempt(s). "
                        f"Last error: {error_msg}",
                        attempts=attempt,
                        last_yaml=raw,
                        last_error=error_msg,
                    ) from exc

                # Extend conversation with the failed attempt and repair request.
                messages.append({"role": "assistant", "content": raw})
                messages.append({"role": "user", "content": build_repair_prompt(raw, error_msg)})

        # Unreachable — loop always returns or raises above.
        raise RuntimeError("compile() loop exited unexpectedly")  # pragma: no cover
