"""Prompt templates for the Trellis pipeline compiler."""

from __future__ import annotations

_PIPELINE_SCHEMA = """\
pipeline:
  id: snake_case_id               # snake_case, starts with a letter
  goal: "Human-readable goal"
  params:                         # optional typed parameters
    param_name:
      type: string                # string | integer | number | boolean | list | object
      description: "..."
      default: value              # omit to make the param required
  tasks:
    - id: task_id                 # snake_case, unique within the pipeline
      tool: tool_name             # must be from KNOWN TOOLS below
      inputs:                     # key-value pairs; values may be templates
        key: literal_value
        other: "{{prev_task.output}}"
      parallel_over: "{{list}}"   # optional fan-out — runs once per list item
      retry: 0                    # optional retry count on failure
      timeout: 30                 # optional per-invocation timeout (seconds)
      await:                      # optional explicit barriers (no input reference)
        - other_task_id"""

_TEMPLATE_RULES = """\
{{task_id.output}}            full output of a completed task (also creates a dependency edge)
{{task_id.output.field}}      field within a dict output
{{task_id.output.list.first}} first element of a list field  (.last for last)
{{params.key}}                pipeline parameter value (must be declared in params block)
{{session.key}}               blackboard value written by a prior store task
{{item}}                      current element inside a parallel_over fan-out

Rules:
  - A whole-value template "{{expr}}" preserves the resolved type (list, dict, str, …)
  - An embedded template "text {{expr}} text" always produces a string
  - Dependency edges are inferred from {{task_id.*}} references — no depends_on field
  - A task with parallel_over MUST reference {{item}} somewhere in its inputs
  - {{item}} in inputs REQUIRES parallel_over to be set on the same task"""

_VALIDATION_RULES = """\
  - id fields: snake_case, start with a letter, lowercase alphanumeric + underscore only
  - tool: must be one of the KNOWN TOOLS listed below
  - All {{task_id.*}} references must point to a task that exists in this pipeline
  - All {{params.key}} references must be declared in the params block
  - Task ids must be unique within the pipeline
  - No circular dependencies between tasks
  - A compute task must include a `function` input key"""


def build_system_prompt(tool_catalog: str) -> str:
    """
    Build the compiler system prompt, embedding the live tool catalog.

    Args:
        tool_catalog: Output of catalog.build_tool_catalog().

    Returns:
        System prompt string to send as the first message.
    """
    return f"""\
You are the Trellis pipeline compiler.
Convert a natural-language description into a valid Trellis pipeline YAML.

═══ OUTPUT FORMAT ════════════════════════════════════════════════════════════
Output ONLY the YAML — no explanations, no markdown code fences, no preamble.
The YAML must begin with `pipeline:` (single pipeline) or `plan:` (multi-pipeline plan).
Output nothing else.

═══ PIPELINE SCHEMA ══════════════════════════════════════════════════════════
{_PIPELINE_SCHEMA}

═══ TEMPLATE SYNTAX ══════════════════════════════════════════════════════════
{_TEMPLATE_RULES}

═══ VALIDATION RULES ═════════════════════════════════════════════════════════
{_VALIDATION_RULES}

═══ KNOWN TOOLS ══════════════════════════════════════════════════════════════
{tool_catalog}
"""


def build_repair_prompt(broken_yaml: str, error: str) -> str:
    """
    Build a repair user message when the previous YAML failed validation.

    Args:
        broken_yaml: The LLM's previous (invalid) YAML output.
        error:       The validation error message.

    Returns:
        User message asking for a corrected YAML.
    """
    return f"""\
The YAML you produced failed validation. Fix it and output ONLY the corrected YAML.

ERROR:
{error}

YOUR PREVIOUS OUTPUT:
{broken_yaml}"""
