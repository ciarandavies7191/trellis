"""LLM tool implementation for language model tasks.

Provider-agnostic `llm_job` tool backed by litellm.
Configuration via environment variables (override per-call via inputs):
 - TRELLIS_LLM_MODEL: default litellm model string (default: "openai/gpt-4o-mini")

Extra kwargs behavior
----------------------
Any keyword argument that is not a control parameter (model, temperature,
max_tokens) is treated as *context data* and serialised into the prompt
before the caller-supplied prompt text.  This lets tasks pass structured
objects alongside the prompt without having to manually embed them:

    tool: llm_job
    inputs:
      extracted: "{{session.extracted_fields}}"
      schema:    "{{load_output_schema.output}}"
      prompt:    "Review the extracted values and fix any __not_found__ entries."

The serialised block looks like:

    --- extracted ---
    [{"Total Revenues": "350018", ...}, ...]

    --- schema ---
    {"source": "template.md", "fields": ["Total Revenues", ...]}

    --- prompt ---
    Review the extracted values and fix any __not_found__ entries.
"""

from __future__ import annotations

import dataclasses
import json
import os
from enum import Enum
from typing import Any, Dict, Optional
import litellm  # type: ignore

from ..base import BaseTool, ToolInput, ToolOutput

DEFAULT_LLM_MODEL = os.getenv("TRELLIS_LLM_MODEL", "openai/gpt-4o-mini")

# kwargs that control litellm — never treated as context data
_CONTROL_PARAMS = frozenset({"model", "temperature", "max_tokens"})


def _to_json_safe(val: Any, _depth: int = 0) -> Any:
    """
    Convert an arbitrary pipeline value to a JSON-serialisable structure.

    Domain objects are summarised rather than fully expanded so the prompt
    stays readable:
      - SchemaHandle   → {source, fields: [names]}
      - DocumentHandle / PageList → "<Type: source, N pages>"  (opaque ref)
      - dataclass      → field dict (recursively)
      - Enum           → value
      - bytes          → "<bytes: N>"
    """
    if _depth > 10:
        return f"<depth-limit: {type(val).__name__}>"
    if val is None or isinstance(val, (bool, int, float, str)):
        return val
    if isinstance(val, bytes):
        return f"<bytes: {len(val)}>"
    if isinstance(val, Enum):
        return val.value
    if isinstance(val, (list, tuple)):
        return [_to_json_safe(v, _depth + 1) for v in val]
    if isinstance(val, dict):
        return {str(k): _to_json_safe(v, _depth + 1) for k, v in val.items()}
    if dataclasses.is_dataclass(val) and not isinstance(val, type):
        type_name = type(val).__name__
        # Summarise large document objects — their full text is not useful here
        if type_name in ("DocumentHandle", "PageList"):
            source = getattr(val, "source", getattr(val, "parent_source", "?"))
            n_pages = len(getattr(val, "pages", []))
            return f"<{type_name}: {source}, {n_pages} pages>"
        # SchemaHandle: expose source + field names only
        if type_name == "SchemaHandle":
            return {
                "source": getattr(val, "source", ""),
                "fields": [f.name for f in getattr(val, "fields", [])],
            }
        # Generic dataclass: expand fields recursively
        return {
            f.name: _to_json_safe(getattr(val, f.name), _depth + 1)
            for f in dataclasses.fields(val)
        }
    return str(val)


def _build_prompt(prompt: str, context: Dict[str, Any]) -> str:
    """
    Prepend serialised context blocks to the prompt.

    Each context entry becomes a labelled section:
        --- key ---
        <JSON or string value>
    """
    if not context:
        return prompt

    parts: list[str] = []
    for key, val in context.items():
        safe = _to_json_safe(val)
        if isinstance(safe, (dict, list)):
            serialised = json.dumps(safe, indent=2, ensure_ascii=False)
        else:
            serialised = str(safe)
        parts.append(f"--- {key} ---\n{serialised}")

    parts.append(f"--- prompt ---\n{prompt}")
    return "\n\n".join(parts)


class LLMTool(BaseTool):
    """Provider-agnostic LLM tool backed by litellm."""

    def __init__(self, name: str = "llm_job", model: Optional[str] = None):
        super().__init__(name, "Execute LLM-based reasoning and generation")
        self.default_model = model

    def execute(
        self,
        prompt: str,
        **kwargs: Any,
    ) -> str:
        """
        Execute LLM tool.

        Args:
            prompt:      Instruction text for the LLM.
            **kwargs:    Control params (model, temperature, max_tokens) plus any
                         context data to inject into the prompt (see module docstring).

        Returns:
            LLM response string.
        """
        if litellm is None:  # pragma: no cover
            raise RuntimeError("litellm is not installed. Install with: pip install litellm")

        if not isinstance(prompt, str):
            raise TypeError(
                f"llm_job: 'prompt' must be a str, got {type(prompt).__name__!r}. "
                "To process a DocumentHandle or PageList, use extract_from_texts or "
                "extract_from_tables instead, or pass `{{task.output.some_text_field}}`."
            )

        model: str = kwargs.get("model") or self.default_model or DEFAULT_LLM_MODEL
        temperature: Optional[float] = kwargs.get("temperature")
        max_tokens: Optional[int] = kwargs.get("max_tokens")

        # Collect non-control kwargs as context data and inject into prompt
        context = {k: v for k, v in kwargs.items() if k not in _CONTROL_PARAMS}
        full_prompt = _build_prompt(prompt, context)

        call_kwargs: Dict[str, Any] = {
            "model": model,
            "messages": [{"role": "user", "content": full_prompt}],
        }
        if temperature is not None:
            call_kwargs["temperature"] = temperature
        if max_tokens is not None:
            call_kwargs["max_tokens"] = max_tokens

        resp = litellm.completion(**call_kwargs)
        return resp.choices[0].message.content or ""

    def get_inputs(self) -> Dict[str, ToolInput]:
        return {
            "prompt": ToolInput(
                name="prompt",
                description="Input prompt for the LLM",
                required=True,
                accepted_types=(str,),
            ),
            "temperature": ToolInput(
                name="temperature",
                description="Temperature parameter for generation",
                required=False,
                default=0.7,
            ),
            "max_tokens": ToolInput(
                name="max_tokens",
                description="Maximum tokens in response",
                required=False,
                default=2000,
            ),
            "model": ToolInput(
                name="model",
                description="litellm model string override (e.g. 'openai/gpt-4o', 'anthropic/claude-3-haiku-20240307')",
                required=False,
                default=None,
            ),
        }

    def get_output(self) -> ToolOutput:
        return ToolOutput(
            name="response",
            description="LLM generated response",
            type_="string",
        )
