"""LLM tool implementation for language model tasks.

Provider-agnostic `llm_job` tool with OpenAI and Ollama backends.
Configuration via environment variables (override per-call via inputs):
 - TRELLIS_LLM_PROVIDER: "openai" | "ollama" (default: "openai")
 - OPENAI_API_KEY: API key for OpenAI
 - OPENAI_MODEL: default model (e.g., "gpt-4o-mini" or "gpt-3.5-turbo")
 - OLLAMA_HOST: base URL for Ollama (default: http://localhost:11434)
 - OLLAMA_MODEL: default model (e.g., "llama3")
 - ANTHROPIC_API_KEY: API key for Anthropic
 - ANTHROPIC_MODEL: default model (e.g., "claude-3-haiku-20240307")
"""

from __future__ import annotations

import json
import os
import urllib.request
import urllib.error
from typing import Any, Dict, Optional

from ..base import BaseTool, ToolInput, ToolOutput


class _ProviderBase:
    def generate(
        self,
        prompt: str,
        *,
        model: Optional[str],
        temperature: Optional[float],
        max_tokens: Optional[int],
    ) -> str:
        raise NotImplementedError


class _OpenAIProvider(_ProviderBase):
    def __init__(self, api_key: Optional[str]) -> None:
        self.api_key = api_key or os.environ.get("OPENAI_API_KEY")
        if not self.api_key:
            raise RuntimeError("OPENAI_API_KEY is not set")

    def generate(
        self,
        prompt: str,
        *,
        model: Optional[str],
        temperature: Optional[float],
        max_tokens: Optional[int],
    ) -> str:
        # Try SDK first; fall back to HTTP if unavailable
        try:
            from openai import OpenAI  # type: ignore

            client = OpenAI(api_key=self.api_key)
            completion = client.chat.completions.create(
                model=model or os.environ.get("OPENAI_MODEL", "gpt-3.5-turbo"),
                messages=[{"role": "user", "content": prompt}],
                temperature=temperature,
                max_tokens=max_tokens,
            )
            return completion.choices[0].message.content or ""
        except Exception:
            # HTTP fallback
            return self._http_chat(prompt, model, temperature, max_tokens)

    def _http_chat(
        self,
        prompt: str,
        model: Optional[str],
        temperature: Optional[float],
        max_tokens: Optional[int],
    ) -> str:
        url = "https://api.openai.com/v1/chat/completions"
        data = {
            "model": model or os.environ.get("OPENAI_MODEL", "gpt-3.5-turbo"),
            "messages": [{"role": "user", "content": prompt}],
        }
        if temperature is not None:
            data["temperature"] = temperature
        if max_tokens is not None:
            data["max_tokens"] = max_tokens
        req = urllib.request.Request(
            url,
            method="POST",
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}",
            },
            data=json.dumps(data).encode("utf-8"),
        )
        try:
            with urllib.request.urlopen(req) as resp:
                body = json.loads(resp.read().decode("utf-8"))
                return body.get("choices", [{}])[0].get("message", {}).get("content", "")
        except urllib.error.HTTPError as e:
            raise RuntimeError(f"OpenAI HTTP error: {e.code} {e.read().decode('utf-8', errors='ignore')}")
        except urllib.error.URLError as e:
            raise RuntimeError(f"OpenAI network error: {e.reason}")


class _OllamaProvider(_ProviderBase):
    def __init__(self, host: Optional[str]) -> None:
        self.host = (host or os.environ.get("OLLAMA_HOST") or "http://localhost:11434").rstrip("/")

    def generate(
        self,
        prompt: str,
        *,
        model: Optional[str],
        temperature: Optional[float],
        max_tokens: Optional[int],  # unused by /api/generate; left for parity
    ) -> str:
        url = f"{self.host}/api/generate"
        payload: Dict[str, Any] = {
            "model": model or os.environ.get("OLLAMA_MODEL", "llama3"),
            "prompt": prompt,
            "stream": False,
        }
        if temperature is not None:
            payload.setdefault("options", {})["temperature"] = temperature
        req = urllib.request.Request(
            url,
            method="POST",
            headers={"Content-Type": "application/json"},
            data=json.dumps(payload).encode("utf-8"),
        )
        try:
            with urllib.request.urlopen(req) as resp:
                body = json.loads(resp.read().decode("utf-8"))
                # Ollama returns {"response": "...", ...}
                return body.get("response", "")
        except urllib.error.HTTPError as e:
            raise RuntimeError(f"Ollama HTTP error: {e.code} {e.read().decode('utf-8', errors='ignore')}")
        except urllib.error.URLError as e:
            raise RuntimeError(f"Ollama network error: {e.reason}")


class _AnthropicProvider(_ProviderBase):
    def __init__(self, api_key: Optional[str]) -> None:
        self.api_key = api_key or os.environ.get("ANTHROPIC_API_KEY")
        if not self.api_key:
            raise RuntimeError("ANTHROPIC_API_KEY is not set")

    def generate(
        self,
        prompt: str,
        *,
        model: Optional[str],
        temperature: Optional[float],
        max_tokens: Optional[int],
    ) -> str:
        # Try SDK first; fall back to HTTP if unavailable
        try:
            from anthropic import Anthropic  # type: ignore

            client = Anthropic(api_key=self.api_key)
            msg = client.messages.create(
                model=model or os.environ.get("ANTHROPIC_MODEL", "claude-3-haiku-20240307"),
                max_tokens=max_tokens or 512,
                temperature=temperature,
                messages=[{"role": "user", "content": prompt}],
            )
            # content is a list of blocks; take text of first
            for block in getattr(msg, "content", []) or []:
                text = getattr(block, "text", None)
                if text:
                    return text
            return ""
        except Exception:
            return self._http_messages(prompt, model, temperature, max_tokens)

    def _http_messages(
        self,
        prompt: str,
        model: Optional[str],
        temperature: Optional[float],
        max_tokens: Optional[int],
    ) -> str:
        url = "https://api.anthropic.com/v1/messages"
        payload: Dict[str, Any] = {
            "model": model or os.environ.get("ANTHROPIC_MODEL", "claude-3-haiku-20240307"),
            "max_tokens": max_tokens or 512,
            "messages": [{"role": "user", "content": prompt}],
        }
        if temperature is not None:
            payload["temperature"] = temperature
        req = urllib.request.Request(
            url,
            method="POST",
            headers={
                "Content-Type": "application/json",
                "x-api-key": self.api_key,
                "anthropic-version": "2023-06-01",
            },
            data=json.dumps(payload).encode("utf-8"),
        )
        try:
            with urllib.request.urlopen(req) as resp:
                body = json.loads(resp.read().decode("utf-8"))
                # Find text in content blocks
                for block in body.get("content", []) or []:
                    text = block.get("text")
                    if text:
                        return text
                return ""
        except urllib.error.HTTPError as e:
            raise RuntimeError(f"Anthropic HTTP error: {e.code} {e.read().decode('utf-8', errors='ignore')}")
        except urllib.error.URLError as e:
            raise RuntimeError(f"Anthropic network error: {e.reason}")


class LLMTool(BaseTool):
    """Provider-agnostic tool that delegates to OpenAI or Ollama."""

    def __init__(self, name: str = "llm_job", model: Optional[str] = None):
        super().__init__(name, "Execute LLM-based reasoning and generation")
        self.default_model = model  # optional override; can also be set via env

    def _select_provider(self, provider: Optional[str]) -> _ProviderBase:
        which = (provider or os.environ.get("TRELLIS_LLM_PROVIDER") or "openai").lower()
        if which == "openai":
            return _OpenAIProvider(api_key=os.environ.get("OPENAI_API_KEY"))
        if which == "ollama":
            return _OllamaProvider(host=os.environ.get("OLLAMA_HOST"))
        if which in ("anthropic", "claude"):
            return _AnthropicProvider(api_key=os.environ.get("ANTHROPIC_API_KEY"))
        raise RuntimeError(
            f"Unsupported TRELLIS_LLM_PROVIDER: {which!r}. Use 'openai' | 'ollama' | 'anthropic'."
        )

    def execute(
        self,
        prompt: str,
        **kwargs: Any,
    ) -> str:
        """
        Execute LLM tool.

        Args:
            prompt: Input prompt for the LLM
            **kwargs: Additional arguments (temperature, max_tokens, model, provider)

        Returns:
            LLM response string
        """
        provider_name: Optional[str] = kwargs.get("provider")
        model: Optional[str] = kwargs.get("model", self.default_model)
        temperature: Optional[float] = kwargs.get("temperature")
        max_tokens: Optional[int] = kwargs.get("max_tokens")

        provider = self._select_provider(provider_name)
        return provider.generate(
            prompt,
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
        )

    def get_inputs(self) -> Dict[str, ToolInput]:
        return {
            "prompt": ToolInput(
                name="prompt",
                description="Input prompt for the LLM",
                required=True,
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
                description="Model name override",
                required=False,
                default=None,
            ),
            "provider": ToolInput(
                name="provider",
                description="LLM provider override ('openai'|'ollama'|'anthropic')",
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
