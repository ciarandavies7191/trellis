"""
CLI tests for `trellis compile`.

Uses Typer's CliRunner — no real LLM calls.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from typer.testing import CliRunner

from trellis_cli.main import app

runner = CliRunner(mix_stderr=False)

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

MINIMAL_PIPELINE_YAML = """\
pipeline:
  id: hello_pipeline
  goal: "Say hello"
  tasks:
    - id: greet
      tool: llm_job
      inputs:
        prompt: "Say hello."
"""

MINIMAL_PLAN_YAML = """\
plan:
  id: hello_plan
  goal: "Say hello"
  sub_pipelines:
    - id: step_one
      goal: "Greet the user"
      reads: []
      stores: [greeting]
"""


def _mock_compiler(yaml_text: str, attempts: int = 1):
    """Return a context-manager patch that makes PipelineCompiler.compile() succeed."""
    from trellis.compiler.result import CompilerResult
    from trellis.models.pipeline import Pipeline
    from trellis.models.plan import Plan

    if yaml_text.strip().startswith("pipeline:"):
        artifact = Pipeline.from_yaml(yaml_text)
        result = CompilerResult(yaml_text=yaml_text, pipeline=artifact, attempts=attempts)
    else:
        artifact = Plan.from_yaml(yaml_text)
        result = CompilerResult(yaml_text=yaml_text, plan=artifact, attempts=attempts)

    return patch(
        "trellis_cli.main.PipelineCompiler",
        return_value=MagicMock(
            compile=AsyncMock(return_value=result),
        ),
    )


def _mock_compiler_error(attempts: int = 3):
    """Return a patch that makes PipelineCompiler.compile() raise CompilerError."""
    from trellis.compiler.exceptions import CompilerError

    return patch(
        "trellis_cli.main.PipelineCompiler",
        return_value=MagicMock(
            compile=AsyncMock(
                side_effect=CompilerError(
                    "Compilation failed after 3 attempt(s). Last error: unknown tool",
                    attempts=attempts,
                    last_yaml="bad: yaml",
                    last_error="unknown tool 'oops'",
                )
            ),
        ),
    )


# ---------------------------------------------------------------------------
# Input validation
# ---------------------------------------------------------------------------


class TestCompileInputValidation:
    def test_no_prompt_exits_2(self):
        result = runner.invoke(app, ["compile"])
        assert result.exit_code == 2
        assert "prompt" in result.output.lower() or "required" in result.output.lower()

    def test_both_prompt_and_file_exits_2(self, tmp_path):
        f = tmp_path / "p.txt"
        f.write_text("hello")
        result = runner.invoke(app, ["compile", "hello", "--prompt-file", str(f)])
        assert result.exit_code == 2

    def test_empty_prompt_string_exits_2(self):
        result = runner.invoke(app, ["compile", "   "])
        assert result.exit_code == 2

    def test_empty_prompt_file_exits_2(self, tmp_path):
        f = tmp_path / "empty.txt"
        f.write_text("   ")
        result = runner.invoke(app, ["compile", "--prompt-file", str(f)])
        assert result.exit_code == 2

    def test_nonexistent_prompt_file_exits_nonzero(self):
        result = runner.invoke(app, ["compile", "--prompt-file", "does_not_exist.txt"])
        assert result.exit_code != 0


# ---------------------------------------------------------------------------
# Success — positional prompt
# ---------------------------------------------------------------------------


class TestCompileSuccessPromptArg:
    def test_prints_yaml_to_stdout(self):
        with _mock_compiler(MINIMAL_PIPELINE_YAML):
            result = runner.invoke(app, ["compile", "Say hello using an LLM"])
        assert result.exit_code == 0
        assert "pipeline:" in result.output

    def test_prints_pipeline_id_in_header(self):
        with _mock_compiler(MINIMAL_PIPELINE_YAML):
            result = runner.invoke(app, ["compile", "Say hello"])
        assert result.exit_code == 0
        assert "hello_pipeline" in result.output

    def test_prints_plan_id_when_plan_produced(self):
        with _mock_compiler(MINIMAL_PLAN_YAML):
            result = runner.invoke(app, ["compile", "Multi-step plan"])
        assert result.exit_code == 0
        assert "hello_plan" in result.output

    def test_shows_repair_count_when_repairs_needed(self):
        with _mock_compiler(MINIMAL_PIPELINE_YAML, attempts=3):
            result = runner.invoke(app, ["compile", "Say hello"])
        assert result.exit_code == 0
        assert "repair" in result.output.lower() or "2" in result.output


# ---------------------------------------------------------------------------
# Success — --prompt-file
# ---------------------------------------------------------------------------


class TestCompilePromptFile:
    def test_reads_prompt_from_file(self, tmp_path):
        f = tmp_path / "prompt.txt"
        f.write_text("Fetch AAPL 10-K and summarise")
        with _mock_compiler(MINIMAL_PIPELINE_YAML):
            result = runner.invoke(app, ["compile", "--prompt-file", str(f)])
        assert result.exit_code == 0
        assert "pipeline:" in result.output

    def test_strips_whitespace_from_file_content(self, tmp_path):
        f = tmp_path / "prompt.txt"
        f.write_text("  \nFetch AAPL\n  ")
        with _mock_compiler(MINIMAL_PIPELINE_YAML):
            result = runner.invoke(app, ["compile", "--prompt-file", str(f)])
        assert result.exit_code == 0


# ---------------------------------------------------------------------------
# --output flag
# ---------------------------------------------------------------------------


class TestCompileOutputFlag:
    def test_writes_yaml_to_file(self, tmp_path):
        out = tmp_path / "pipeline.yaml"
        with _mock_compiler(MINIMAL_PIPELINE_YAML):
            result = runner.invoke(app, ["compile", "Say hello", "--output", str(out)])
        assert result.exit_code == 0
        assert out.exists()
        assert "pipeline:" in out.read_text()

    def test_output_file_contains_exact_yaml(self, tmp_path):
        out = tmp_path / "pipeline.yaml"
        with _mock_compiler(MINIMAL_PIPELINE_YAML):
            runner.invoke(app, ["compile", "Say hello", "--output", str(out)])
        written = out.read_text(encoding="utf-8")
        assert "hello_pipeline" in written

    def test_stdout_shows_confirmation_not_yaml(self, tmp_path):
        out = tmp_path / "pipeline.yaml"
        with _mock_compiler(MINIMAL_PIPELINE_YAML):
            result = runner.invoke(app, ["compile", "Say hello", "--output", str(out)])
        assert result.exit_code == 0
        # Confirmation message should appear; raw YAML should NOT be on stdout
        assert str(out) in result.output
        # YAML body not duplicated to stdout
        assert "tasks:" not in result.output


# ---------------------------------------------------------------------------
# --json flag
# ---------------------------------------------------------------------------


class TestCompileJsonFlag:
    def test_json_mode_outputs_only_yaml(self):
        with _mock_compiler(MINIMAL_PIPELINE_YAML):
            result = runner.invoke(app, ["compile", "Say hello", "--json"])
        assert result.exit_code == 0
        assert result.output.strip() == MINIMAL_PIPELINE_YAML.strip()

    def test_json_mode_no_compiling_header(self):
        with _mock_compiler(MINIMAL_PIPELINE_YAML):
            result = runner.invoke(app, ["compile", "Say hello", "--json"])
        assert "Compiling" not in result.output
        assert "Compiled" not in result.output


# ---------------------------------------------------------------------------
# --model flag
# ---------------------------------------------------------------------------


class TestCompileModelFlag:
    def test_model_flag_passed_to_compiler(self):
        from trellis.compiler.result import CompilerResult
        from trellis.models.pipeline import Pipeline

        artifact = Pipeline.from_yaml(MINIMAL_PIPELINE_YAML)
        mock_result = CompilerResult(
            yaml_text=MINIMAL_PIPELINE_YAML, pipeline=artifact, attempts=1
        )
        mock_compile = AsyncMock(return_value=mock_result)
        mock_cls = MagicMock(return_value=MagicMock(compile=mock_compile))

        with patch("trellis_cli.main.PipelineCompiler", mock_cls):
            result = runner.invoke(
                app,
                ["compile", "Hello", "--model", "anthropic/claude-haiku-4-5-20251001"],
            )

        assert result.exit_code == 0
        # Compiler class should have been instantiated with model kwarg
        mock_cls.assert_called_once_with(model="anthropic/claude-haiku-4-5-20251001")


# ---------------------------------------------------------------------------
# --max-repairs flag
# ---------------------------------------------------------------------------


class TestCompileMaxRepairsFlag:
    def test_max_repairs_passed_to_compile(self):
        from trellis.compiler.result import CompilerResult
        from trellis.models.pipeline import Pipeline

        artifact = Pipeline.from_yaml(MINIMAL_PIPELINE_YAML)
        mock_result = CompilerResult(
            yaml_text=MINIMAL_PIPELINE_YAML, pipeline=artifact, attempts=1
        )
        mock_compile = AsyncMock(return_value=mock_result)
        mock_cls = MagicMock(return_value=MagicMock(compile=mock_compile))

        with patch("trellis_cli.main.PipelineCompiler", mock_cls):
            result = runner.invoke(app, ["compile", "Hello", "--max-repairs", "5"])

        assert result.exit_code == 0
        mock_compile.assert_called_once()
        call_kwargs = mock_compile.call_args
        assert call_kwargs.kwargs.get("max_repair_attempts") == 5


# ---------------------------------------------------------------------------
# Failure paths
# ---------------------------------------------------------------------------


class TestCompileFailure:
    def test_compiler_error_exits_1(self):
        with _mock_compiler_error():
            result = runner.invoke(app, ["compile", "Say hello"])
        assert result.exit_code == 1

    def test_compiler_error_shows_message(self):
        with _mock_compiler_error():
            result = runner.invoke(app, ["compile", "Say hello"])
        assert "failed" in result.output.lower() or "error" in result.output.lower()

    def test_compiler_error_shows_last_yaml(self):
        with _mock_compiler_error():
            result = runner.invoke(app, ["compile", "Say hello"])
        assert "bad: yaml" in result.output

    def test_unexpected_exception_exits_1(self):
        with patch(
            "trellis_cli.main.PipelineCompiler",
            return_value=MagicMock(
                compile=AsyncMock(side_effect=RuntimeError("network down"))
            ),
        ):
            result = runner.invoke(app, ["compile", "Say hello"])
        assert result.exit_code == 1
        assert "network down" in result.output
