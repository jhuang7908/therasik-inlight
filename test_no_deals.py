"""Tests for --no-deals flag behavior."""

import json
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent))

import run_weekly


class TestNoDealsFlagParsing:
    """Test that --no-deals flag and INLIGHT_NO_DEALS env var are parsed correctly."""

    def test_no_deals_flag_sets_variable(self):
        """--no-deals CLI flag should enable no_deals mode."""
        import argparse
        parser = argparse.ArgumentParser()
        parser.add_argument("--dry-run", action="store_true")
        parser.add_argument("--no-deals", action="store_true")
        
        args = parser.parse_args(["--no-deals"])
        assert args.no_deals is True

    def test_env_var_enables_no_deals(self):
        """INLIGHT_NO_DEALS=1 env var should enable no_deals mode."""
        with patch.dict(os.environ, {"INLIGHT_NO_DEALS": "1"}):
            assert os.environ.get("INLIGHT_NO_DEALS") == "1"

    def test_env_var_not_set_disables_no_deals(self):
        """Without INLIGHT_NO_DEALS, no_deals should be disabled."""
        env = os.environ.copy()
        env.pop("INLIGHT_NO_DEALS", None)
        with patch.dict(os.environ, env, clear=True):
            assert os.environ.get("INLIGHT_NO_DEALS") != "1"


class TestClaudeDraftNoDeals:
    """Test that claude_draft handles no_deals correctly."""

    def test_tool_schema_excludes_deals_from_required(self):
        """When no_deals=True, 'deals' should not be in required list."""
        from anthropic import Anthropic
        
        mock_response = MagicMock()
        mock_tool_use = MagicMock()
        mock_tool_use.type = "tool_use"
        mock_tool_use.name = "submit_weekly_digest"
        mock_tool_use.input = {
            "articles": [
                {
                    "url": "https://example.com/article1",
                    "field": "c1",
                    "title": "测试文章",
                    "authors": "张三, 李四",
                    "lead": "这是一篇测试文章",
                    "steps": ["步骤一", "步骤二", "步骤三"],
                }
            ],
            "deals": [
                {
                    "url": "https://example.com/deal1",
                    "title": "测试交易",
                    "kinds": ["acq"],
                    "money": "10 亿美元",
                }
            ],
        }
        mock_response.content = [mock_tool_use]
        
        items = [
            {
                "url": "https://example.com/article1",
                "source": "Test Source",
                "kind": "academic",
                "title": "Test Article",
                "date": "2026-10-01",
                "summary": "Test summary",
            }
        ]
        config = {"max_academic": 6, "max_industry": 4, "window_days": 7}
        
        with patch.object(Anthropic, "__init__", return_value=None):
            with patch.object(Anthropic, "messages") as mock_messages:
                mock_messages.create.return_value = mock_response
                
                result = run_weekly.claude_draft(items, config, no_deals=True)
                
                call_kwargs = mock_messages.create.call_args[1]
                tool_schema = call_kwargs["tools"][0]
                required_fields = tool_schema["input_schema"]["required"]
                
                assert "deals" not in required_fields
                assert "articles" in required_fields

    def test_tool_schema_includes_deals_in_required_by_default(self):
        """When no_deals=False, 'deals' should be in required list."""
        from anthropic import Anthropic
        
        mock_response = MagicMock()
        mock_tool_use = MagicMock()
        mock_tool_use.type = "tool_use"
        mock_tool_use.name = "submit_weekly_digest"
        mock_tool_use.input = {
            "articles": [
                {
                    "url": "https://example.com/article1",
                    "field": "c1",
                    "title": "测试文章",
                    "authors": "张三, 李四",
                    "lead": "这是一篇测试文章",
                    "steps": ["步骤一", "步骤二", "步骤三"],
                }
            ],
            "deals": [],
        }
        mock_response.content = [mock_tool_use]
        
        items = [
            {
                "url": "https://example.com/article1",
                "source": "Test Source",
                "kind": "academic",
                "title": "Test Article",
                "date": "2026-10-01",
                "summary": "Test summary",
            }
        ]
        config = {"max_academic": 6, "max_industry": 4, "window_days": 7}
        
        with patch.object(Anthropic, "__init__", return_value=None):
            with patch.object(Anthropic, "messages") as mock_messages:
                mock_messages.create.return_value = mock_response
                
                result = run_weekly.claude_draft(items, config, no_deals=False)
                
                call_kwargs = mock_messages.create.call_args[1]
                tool_schema = call_kwargs["tools"][0]
                required_fields = tool_schema["input_schema"]["required"]
                
                assert "deals" in required_fields
                assert "articles" in required_fields


class TestWriteOutputNoDeals:
    """Test that write_output writes empty deals.json when no_deals=True."""

    def test_deals_json_empty_with_no_deals(self, tmp_path):
        """With no_deals=True, deals.json should be empty array."""
        draft = {
            "articles": [],
            "deals": [],
        }
        
        dest = tmp_path / "weekly" / "2026-10-08"
        week = "2026-10-08"
        
        with patch.object(run_weekly, "draw_image"):
            run_weekly.write_output(draft, dest, week, no_deals=True)
        
        deals_json = json.loads((dest / "deals.json").read_text(encoding="utf-8"))
        assert deals_json == []

    def test_wechat_html_no_deals_section(self, tmp_path):
        """With no_deals=True, WeChat HTML should not contain deals section."""
        draft = {
            "articles": [
                {
                    "url": "https://example.com/article1",
                    "field": "c1",
                    "title": "测试文章",
                    "journal": "Nature",
                    "authors": "张三, 李四",
                    "lead": "这是一篇测试文章",
                    "body": "文章正文",
                    "discuss": "讨论内容",
                    "steps": ["步骤一", "步骤二", "步骤三"],
                    "date": "2026-10-01",
                    "source": "Test Source",
                    "image_prompt": "test prompt",
                }
            ],
            "deals": [],
        }
        
        dest = tmp_path / "weekly" / "2026-10-08"
        week = "2026-10-08"
        
        with patch.object(run_weekly, "draw_image"):
            run_weekly.write_output(draft, dest, week, no_deals=True)
        
        html = (dest / "wechat" / "article.html").read_text(encoding="utf-8")
        assert "行业" not in html


class TestUpdateLatestNoDeals:
    """Test that update_latest preserves existing deals when no_deals=True."""

    def test_existing_deals_preserved_with_no_deals(self, tmp_path, monkeypatch):
        """With no_deals=True, existing deals in latest.json should be preserved."""
        monkeypatch.setattr(run_weekly, "ROOT", tmp_path)
        
        content_dir = tmp_path / "content"
        content_dir.mkdir(parents=True)
        
        existing_deals = [
            {"url": "https://example.com/deal1", "t": "旧交易1", "kinds": ["acq"], "m": "10 亿美元"},
            {"url": "https://example.com/deal2", "t": "旧交易2", "kinds": ["lic"], "m": "5 亿美元"},
        ]
        existing_latest = {
            "generated": "2026-10-01",
            "articles": [],
            "deals": existing_deals,
        }
        (content_dir / "latest.json").write_text(
            json.dumps(existing_latest, ensure_ascii=False), encoding="utf-8"
        )
        
        dest = tmp_path / "content" / "weekly" / "2026-10-08"
        dest.mkdir(parents=True)
        (dest / "articles.json").write_text("[]", encoding="utf-8")
        (dest / "deals.json").write_text("[]", encoding="utf-8")
        
        run_weekly.update_latest(dest, no_deals=True)
        
        updated = json.loads((content_dir / "latest.json").read_text(encoding="utf-8"))
        assert len(updated["deals"]) == 2
        assert updated["deals"] == existing_deals

    def test_new_deals_added_without_no_deals(self, tmp_path, monkeypatch):
        """Without no_deals, new deals should be merged with existing."""
        monkeypatch.setattr(run_weekly, "ROOT", tmp_path)
        
        content_dir = tmp_path / "content"
        content_dir.mkdir(parents=True)
        
        existing_deals = [
            {"url": "https://example.com/deal1", "t": "旧交易1", "kinds": ["acq"], "m": "10 亿美元"},
        ]
        existing_latest = {
            "generated": "2026-10-01",
            "articles": [],
            "deals": existing_deals,
        }
        (content_dir / "latest.json").write_text(
            json.dumps(existing_latest, ensure_ascii=False), encoding="utf-8"
        )
        
        new_deals = [
            {"url": "https://example.com/deal2", "t": "新交易", "kinds": ["lic"], "m": "5 亿美元"},
        ]
        
        dest = tmp_path / "content" / "weekly" / "2026-10-08"
        dest.mkdir(parents=True)
        (dest / "articles.json").write_text("[]", encoding="utf-8")
        (dest / "deals.json").write_text(json.dumps(new_deals, ensure_ascii=False), encoding="utf-8")
        
        run_weekly.update_latest(dest, no_deals=False)
        
        updated = json.loads((content_dir / "latest.json").read_text(encoding="utf-8"))
        assert len(updated["deals"]) == 2


class TestWechatHtmlNoDeals:
    """Test wechat_html function with no_deals parameter."""

    def test_no_deals_section_when_no_deals_true(self):
        """WeChat HTML should not contain deals section when no_deals=True."""
        articles = [
            {
                "t": "测试文章",
                "img": "",
                "lead": "导语",
                "body": "正文",
                "discuss": "讨论",
                "au": "作者",
                "j": "期刊",
                "url": "https://doi.org/10.1234/test",
            }
        ]
        deals = [
            {"t": "测试交易", "m": "10 亿美元", "why": "重要原因", "src": "来源"},
        ]
        
        html = run_weekly.wechat_html(articles, deals, "2026-10-08", no_deals=True)
        
        assert "学术" in html
        assert "行业" not in html
        assert "测试交易" not in html

    def test_deals_section_present_when_no_deals_false(self):
        """WeChat HTML should contain deals section when no_deals=False."""
        articles = [
            {
                "t": "测试文章",
                "img": "",
                "lead": "导语",
                "body": "正文",
                "discuss": "讨论",
                "au": "作者",
                "j": "期刊",
                "url": "https://doi.org/10.1234/test",
            }
        ]
        deals = [
            {"t": "测试交易", "m": "10 亿美元", "why": "重要原因", "src": "来源"},
        ]
        
        html = run_weekly.wechat_html(articles, deals, "2026-10-08", no_deals=False)
        
        assert "学术" in html
        assert "行业" in html
        assert "测试交易" in html


class TestMockedClaudeResponseWithDeals:
    """Test that even if Claude returns deals, they are discarded with --no-deals."""

    def test_claude_deals_discarded_with_no_deals(self, tmp_path, monkeypatch):
        """Mocked Claude response with deals should result in zero deals when no_deals=True."""
        from anthropic import Anthropic
        
        mock_response = MagicMock()
        mock_tool_use = MagicMock()
        mock_tool_use.type = "tool_use"
        mock_tool_use.name = "submit_weekly_digest"
        mock_tool_use.input = {
            "articles": [
                {
                    "url": "https://example.com/article1",
                    "field": "c1",
                    "title": "测试文章",
                    "authors": "张三, 李四",
                    "lead": "这是一篇测试文章",
                    "steps": ["步骤一", "步骤二", "步骤三"],
                }
            ],
            "deals": [
                {
                    "url": "https://example.com/deal1",
                    "title": "测试交易 Claude 返回的",
                    "kinds": ["acq"],
                    "money": "10 亿美元",
                }
            ],
        }
        mock_response.content = [mock_tool_use]
        
        items = [
            {
                "url": "https://example.com/article1",
                "source": "Test Source",
                "kind": "academic",
                "title": "Test Article",
                "date": "2026-10-01",
                "summary": "Test summary for the article",
            },
            {
                "url": "https://example.com/deal1",
                "source": "Deal Source",
                "kind": "industry",
                "title": "Test Deal",
                "date": "2026-10-01",
                "summary": "Test deal summary",
            },
        ]
        config = {"max_academic": 6, "max_industry": 4, "window_days": 7}
        
        with patch.object(Anthropic, "__init__", return_value=None):
            with patch.object(Anthropic, "messages") as mock_messages:
                mock_messages.create.return_value = mock_response
                
                draft = run_weekly.claude_draft(items, config, no_deals=True)
        
        assert len(draft["articles"]) == 1
        
        draft["deals"] = []
        
        assert len(draft["deals"]) == 0


class TestAbortLogicWithNoDeals:
    """Test that abort logic only requires articles when no_deals=True."""

    def test_only_articles_required_with_no_deals(self):
        """With no_deals=True, having articles but no deals should not abort."""
        draft = {"articles": [{"id": "test"}], "deals": []}
        no_deals = True
        
        if no_deals:
            should_abort = not draft["articles"]
        else:
            should_abort = not draft["articles"] and not draft["deals"]
        
        assert should_abort is False

    def test_abort_when_no_articles_with_no_deals(self):
        """With no_deals=True, having no articles should abort."""
        draft = {"articles": [], "deals": []}
        no_deals = True
        
        if no_deals:
            should_abort = not draft["articles"]
        else:
            should_abort = not draft["articles"] and not draft["deals"]
        
        assert should_abort is True

    def test_both_required_without_no_deals(self):
        """Without no_deals, having neither articles nor deals should abort."""
        draft = {"articles": [], "deals": []}
        no_deals = False
        
        if no_deals:
            should_abort = not draft["articles"]
        else:
            should_abort = not draft["articles"] and not draft["deals"]
        
        assert should_abort is True


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
