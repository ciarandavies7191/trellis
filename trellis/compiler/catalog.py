"""Tool catalog builder — generates a compact tool reference for LLM prompts."""

from __future__ import annotations

from trellis.tools.registry import AsyncToolRegistry

# These are test/fixture tools — exclude them from the compiler's tool catalog.
_TEST_TOOLS: frozenset[str] = frozenset({
    "mock",
    "failing_mock",
    "flaky_tool",
    "reliable_tool",
    "permanent_failure",
    "tracker",
    "tracker_1",
    "tracker_2",
    "tracker_3",
    "tracker_4",
})


def build_tool_catalog(registry: AsyncToolRegistry) -> str:
    """
    Return a compact, human-readable tool catalog for inclusion in the compiler
    system prompt.

    Only BaseTool instances with discoverable input/output schemas are included.
    Internal test tools are excluded.

    Example output line:
        llm_job — Execute LLM-based reasoning and generation
          Required: prompt (str)
          Optional: temperature=0.7, max_tokens=2000, model=None
          Output: LLM generated response (string)
    """
    lines: list[str] = []

    for name in sorted(registry._tools_by_name):
        if name in _TEST_TOOLS:
            continue

        tool = registry._tools_by_name[name]
        lines.append(f"\n{name} — {tool.description}")

        try:
            inputs = tool.get_inputs()
        except Exception:
            inputs = {}

        required_parts: list[str] = []
        optional_parts: list[str] = []

        for key, inp in inputs.items():
            if inp.required:
                type_str = ""
                if inp.accepted_types:
                    type_str = f" ({', '.join(t.__name__ for t in inp.accepted_types)})"
                desc = f" — {inp.description}" if inp.description else ""
                required_parts.append(f"{key}{type_str}{desc}")
            else:
                default_repr = repr(inp.default) if inp.default is not None else "None"
                desc = f" — {inp.description}" if inp.description else ""
                optional_parts.append(f"{key}={default_repr}{desc}")

        if required_parts:
            lines.append(f"  Required: {', '.join(required_parts)}")
        if optional_parts:
            lines.append(f"  Optional: {', '.join(optional_parts)}")

        try:
            out = tool.get_output()
            lines.append(f"  Output: {out.description} ({out.type_})")
        except Exception:
            pass

    return "\n".join(lines)
