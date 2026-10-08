"""Claude call kwargs must match the installed Anthropic SDK (no temperature)."""

from __future__ import annotations

import inspect

from anthropic.resources.messages.messages import Messages

from inlight_fields import (
    assert_claude_kwargs_match_sdk,
    classify_tool_schema,
    claude_create_kwargs,
)


def test_messages_create_has_no_temperature_parameter():
    params = inspect.signature(Messages.create).parameters
    assert "temperature" not in params


def test_claude_create_kwargs_strips_temperature_and_matches_sdk():
    kwargs = claude_create_kwargs(
        model="claude-sonnet-5-5",
        max_tokens=400,
        temperature=0,
        tools=[classify_tool_schema()],
        tool_choice={"type": "auto"},
        messages=[{"role": "user", "content": "classify"}],
    )
    assert "temperature" not in kwargs
    assert_claude_kwargs_match_sdk(kwargs)


def test_run_weekly_check_and_draft_omit_temperature():
    import ast
    from pathlib import Path

    src = Path(__file__).resolve().parents[1] / "run_weekly.py"
    tree = ast.parse(src.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            name = ""
            if isinstance(func, ast.Attribute):
                name = func.attr
            if name == "create":
                keys = {
                    k.arg
                    for k in node.keywords
                    if k.arg
                }
                assert "temperature" not in keys
