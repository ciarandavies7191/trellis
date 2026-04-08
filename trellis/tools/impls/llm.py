"""LLM tool implementation for language model tasks.

Provider-agnostic `llm_job` tool backed by litellm.
Configuration via environment variables (override per-call via inputs):
 - TRELLIS_LLM_MODEL: default litellm model string (default: "openai/gpt-4o-mini")
"""

from __future__ import annotations

import os
from typing import Any, Dict, Optional
import litellm  # type: ignore

from ..base import BaseTool, ToolInput, ToolOutput

DEFAULT_LLM_MODEL = os.getenv("TRELLIS_LLM_MODEL", "openai/gpt-4o-mini")


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
            prompt: Input prompt for the LLM
            **kwargs: Additional arguments (temperature, max_tokens, model)

        Returns:
            LLM response string
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

        call_kwargs: Dict[str, Any] = {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
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
