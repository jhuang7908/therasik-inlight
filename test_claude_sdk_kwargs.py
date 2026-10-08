#!/usr/bin/env python3
"""Every production Claude call must match the installed anthropic SDK signature.

anthropic 1.12.x rejects unknown kwargs such as temperature= before sending.
Mocks accept any kwargs, so this test binds real call kwargs against
anthropic.resources.messages.Messages.create.
"""
from __future__ import annotations

import ast
import inspect
from pathlib import Path
from unittest.mock import MagicMock

import pytest

import anthropic
from anthropic.resources.messages import Messages

import sec_deals

REPO = Path(__file__).resolve().parent
PRODUCTION_PY = (REPO / "sec_deals.py", REPO / "run_weekly.py")


def _create_signature():
    return inspect.signature(Messages.create)


def bind_create_kwargs(kwargs: dict) -> None:
    """Raise TypeError if kwargs would be rejected by the installed SDK."""
    _create_signature().bind(None, **kwargs)


def _is_messages_create(node: ast.AST) -> bool:
    if not isinstance(node, ast.Call):
        return False
    func = node.func
    if isinstance(func, ast.Attribute) and func.attr == "create":
        val = func.value
        if isinstance(val, ast.Attribute) and val.attr == "messages":
            return True
    return False


def _literal_kwargs(call: ast.Call) -> dict | None:
    """Best-effort kwargs reconstructed from a call with literal values only."""
    out = {}
    for kw in call.keywords:
        if kw.arg is None:
            return None
        try:
            out[kw.arg] = ast.literal_eval(kw.value)
        except (ValueError, TypeError):
            # Non-literal (model name from env, tools list, prompt, …) — use a dummy
            # of the right kind so bind() still checks the *name*.
            if isinstance(kw.value, ast.List):
                out[kw.arg] = []
            elif isinstance(kw.value, ast.Dict):
                out[kw.arg] = {}
            elif isinstance(kw.value, (ast.Constant,)) and isinstance(kw.value.value, str):
                out[kw.arg] = kw.value.value
            else:
                if kw.arg == "max_tokens":
                    out[kw.arg] = 1
                elif kw.arg == "model":
                    out[kw.arg] = "dummy"
                elif kw.arg == "messages":
                    out[kw.arg] = []
                elif kw.arg in ("tools", "stop_sequences"):
                    out[kw.arg] = []
                elif kw.arg == "tool_choice":
                    out[kw.arg] = {"type": "auto"}
                else:
                    out[kw.arg] = ""
    return out


class TestInstalledSdkSignature:
    def test_temperature_is_rejected_by_installed_sdk(self):
        with pytest.raises(TypeError, match="temperature"):
            bind_create_kwargs({
                "model": "claude-sonnet-5-5",
                "max_tokens": 1,
                "messages": [],
                "temperature": 0,
            })

    def test_ast_every_messages_create_kwargs_bind(self):
        """Static: every messages.create keyword in production source must bind."""
        allowed = set(_create_signature().parameters) - {"self"}
        found = 0
        for path in PRODUCTION_PY:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if not _is_messages_create(node):
                    continue
                found += 1
                names = [kw.arg for kw in node.keywords if kw.arg]
                unknown = [n for n in names if n not in allowed]
                assert not unknown, f"{path.name}:{node.lineno} unknown Claude kwargs: {unknown}"
                reconstructed = _literal_kwargs(node)
                assert reconstructed is not None
                # Required fields for bind()
                reconstructed.setdefault("model", "dummy")
                reconstructed.setdefault("max_tokens", 1)
                reconstructed.setdefault("messages", [])
                bind_create_kwargs(reconstructed)
        assert found >= 3, f"expected several messages.create calls, found {found}"

    def test_runtime_every_production_call_binds(self):
        """Runtime: kwargs actually passed by production functions bind()."""
        captured: list[dict] = []

        class _Block:
            type = "tool_use"
            name = "verify_deal"
            input = {
                "company": {"verdict": "unsupported", "quote": "x"},
                "counterparty": {"verdict": "unsupported", "quote": "x"},
                "deal_type": {"verdict": "unsupported", "quote": "x"},
                "date": {"verdict": "unsupported", "quote": "x"},
                "amounts": [],
            }

        class _Msg:
            content = [_Block()]
            stop_reason = "tool_use"

        def _capture(**kwargs):
            captured.append(dict(kwargs))
            bind_create_kwargs(kwargs)
            return _Msg()

        client = MagicMock()
        client.messages.create.side_effect = _capture

        deal = {
            "title": "Filer与Partner授权合作",
            "company": "Filer Inc.",
            "counterparty": "Partner Corp",
            "deal_type": "license_collaboration",
            "date": "2026-10",
            "money": "",
            "structure": "",
            "verified_amounts": [],
            "type_quote": "the Company granted Partner an exclusive license",
        }
        filing = "On October 1, 2026, the Company granted Partner an exclusive license."
        sec_deals.verify_deal_with_claude(deal, filing, client)

        extract_block = type("B", (), {
            "type": "tool_use",
            "name": "extract_deal",
            "input": {"deal_type": "none"},
        })()
        extract_msg = type("M", (), {"content": [extract_block], "stop_reason": "tool_use"})()

        def _capture_extract(**kwargs):
            captured.append(dict(kwargs))
            bind_create_kwargs(kwargs)
            return extract_msg

        client.messages.create.side_effect = _capture_extract
        sec_deals.extract_deals_from_filings(
            [{
                "filing_text": filing * 5,
                "company": "Filer Inc.",
                "url": "https://example.test/filing",
                "date": "2026-10-05",
                "event_date": "2026-10-01",
            }],
            claude_client=client,
        )

        import run_weekly
        from anthropic.types import Message

        def _ok_message(**kwargs):
            captured.append(dict(kwargs))
            bind_create_kwargs(kwargs)
            payload = {
                "id": "msg_sdk",
                "type": "message",
                "role": "assistant",
                "model": kwargs.get("model") or "claude-sonnet-5-5",
                "content": [{"type": "tool_use", "id": "toolu_x", "name": "test_tool",
                             "input": {"ok": True}}],
                "stop_reason": "tool_use",
                "stop_sequence": None,
                "usage": {"input_tokens": 10, "output_tokens": 5},
            }
            return Message.model_validate(payload)

        dummy = MagicMock()
        dummy.messages.create.side_effect = _ok_message
        monkey_anth = MagicMock(return_value=dummy)

        import anthropic as anth_mod
        orig = anth_mod.Anthropic
        anth_mod.Anthropic = monkey_anth
        try:
            run_weekly.check_anthropic_model()
        finally:
            anth_mod.Anthropic = orig

        assert captured, "no Claude calls were recorded"
        for kw in captured:
            bind_create_kwargs(kw)
            assert "temperature" not in kw
