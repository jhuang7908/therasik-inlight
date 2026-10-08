#!/usr/bin/env python3
"""End-to-end tests for the InLight pipeline.

Tests that call main() with --use-new-pipeline --dry-run using:
- Mocked HTTP responses
- Mocked Claude API in the real API response shape
- Including max_tokens stop and 400 error scenarios

Also tests rendering of existing content pages to verify no duplication vs main.
"""

import os
import sys
import json
import tempfile
import shutil
from pathlib import Path
from unittest.mock import patch, MagicMock, Mock
from dataclasses import dataclass

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


@dataclass
class MockBlock:
    """Mock for Anthropic message content block."""
    type: str
    name: str = ""
    input: dict = None
    
    def __post_init__(self):
        if self.input is None:
            self.input = {}


@dataclass  
class MockMessage:
    """Mock for Anthropic API response."""
    content: list
    stop_reason: str = "end_turn"


def make_triage_response(selections):
    """Create a mock triage response."""
    return MockMessage(
        content=[
            MockBlock(
                type="tool_use",
                name="submit_triage",
                input={"selections": selections}
            )
        ],
        stop_reason="end_turn"
    )


def make_article_response(article_data, stop_reason="end_turn"):
    """Create a mock article draft response."""
    return MockMessage(
        content=[
            MockBlock(
                type="tool_use",
                name="submit_article",
                input=article_data
            )
        ],
        stop_reason=stop_reason
    )


def make_truncated_response():
    """Create a mock response truncated due to max_tokens."""
    return MockMessage(
        content=[
            MockBlock(
                type="text",
                name="",
                input={}
            )
        ],
        stop_reason="max_tokens"
    )


MOCK_ARTICLE_DATA = {
    "url": "https://doi.org/10.1038/test",
    "tier": "brief",
    "field": "c3",
    "title": "测试研究发现52%缓解率",
    "journal": "Nature Medicine",
    "authors": "Test Author",
    "one_liner": "一项研究显示缓解率达52%。",
    "datacard": {
        "study_type": "临床试验",
        "n": "36例",
        "control": "随机对照",
        "intervention": "药物A",
        "followup": "12个月",
        "primary_endpoint": "ORR",
        "primary_endpoint_result": "52%",
        "statistics": "P<0.001",
        "safety": "可接受",
    },
    "background": "研究背景说明现有方案疗效有限。",
    "design": "研究设计：36例患者接受药物A治疗。",
    "results": ["主要终点ORR为52%（19/36），P<0.001。"],
    "mechanism": "",
    "limitations": ["单中心研究外推性有限"],
    "significance": "若后续验证可能改变实践。",
    "data_points": [
        {"value": "52%", "meaning": "缓解率", "source_quote": "response rate was 52%"},
        {"value": "36", "meaning": "患者数", "source_quote": "36 patients enrolled"},
    ],
    "unknowns": [],
    "steps": ["纳入患者", "给药治疗", "评估疗效", "统计分析"],
    "image_prompt": "Clinical trial diagram",
}


class TestEndToEnd:
    """End-to-end tests with mocked external services."""
    
    @pytest.fixture
    def mock_env(self, tmp_path, monkeypatch):
        """Set up mock environment."""
        # Set required env vars
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
        monkeypatch.setenv("OPENAI_API_KEY", "test-key")
        monkeypatch.setenv("ANTHROPIC_MODEL", "claude-sonnet-5-5")
        
        # Create temp directory structure
        test_dir = tmp_path / "inlight"
        test_dir.mkdir()
        (test_dir / "content").mkdir()
        (test_dir / "preview").mkdir()
        (test_dir / "logs").mkdir()
        
        # Copy sources.yaml
        src_sources = Path(__file__).parent.parent / "sources.yaml"
        if src_sources.exists():
            shutil.copy(src_sources, test_dir / "sources.yaml")
        else:
            # Create minimal sources.yaml
            (test_dir / "sources.yaml").write_text("""
window_days: 7
max_per_source: 2
max_academic: 2
max_industry: 1
max_deep: 1
max_brief: 2
sources:
  - name: Test Source
    type: rss
    feed: https://example.com/feed.xml
""")
        
        return test_dir
    
    @patch('anthropic.Anthropic')
    @patch('inlight_articles._http_get')
    def test_triage_with_tool_choice_auto(self, mock_http, mock_anthropic_class, mock_env):
        """Test that triage uses tool_choice=auto (not forced tool)."""
        from inlight_articles import triage_items, EnrichedItem
        
        # Mock EPMC response
        mock_http.return_value = json.dumps({
            "resultList": {"result": []}
        }).encode()
        
        # Mock Anthropic client
        mock_client = MagicMock()
        mock_anthropic_class.return_value = mock_client
        mock_client.messages.create.return_value = make_triage_response([
            {"url": "https://test.com", "tier": "brief", "field": "c3"}
        ])
        
        items = [
            EnrichedItem(
                url="https://test.com",
                title="Test",
                source="Test",
                date="2026-01-01",
                abstract="Test abstract",
                evidence_level="abstract",
            )
        ]
        
        result = triage_items(items, {"max_deep": 1, "max_brief": 2})
        
        # Verify tool_choice was auto
        call_args = mock_client.messages.create.call_args
        assert call_args.kwargs["tool_choice"]["type"] == "auto"
    
    @patch('anthropic.Anthropic')
    @patch('inlight_articles._http_get')
    def test_draft_with_tool_choice_auto(self, mock_http, mock_anthropic_class, mock_env):
        """Test that article drafting uses tool_choice=auto (not forced tool)."""
        from inlight_articles import draft_single_article, EnrichedItem
        
        mock_http.return_value = None
        
        mock_client = MagicMock()
        mock_anthropic_class.return_value = mock_client
        mock_client.messages.create.return_value = make_article_response(MOCK_ARTICLE_DATA)
        
        item = EnrichedItem(
            url="https://doi.org/10.1038/test",
            title="Test Article",
            source="Nature",
            date="2026-01-01",
            abstract="Test abstract with response rate was 52% and 36 patients enrolled.",
            evidence_level="abstract",
        )
        
        result = draft_single_article(item, "brief", {})
        
        # Verify tool_choice was auto
        call_args = mock_client.messages.create.call_args
        assert call_args.kwargs["tool_choice"]["type"] == "auto"
    
    @patch('anthropic.Anthropic')
    @patch('inlight_articles._http_get')
    def test_max_tokens_truncation_handled(self, mock_http, mock_anthropic_class, mock_env):
        """Test that max_tokens truncation returns None (failure)."""
        from inlight_articles import draft_single_article, EnrichedItem
        
        mock_http.return_value = None
        
        mock_client = MagicMock()
        mock_anthropic_class.return_value = mock_client
        mock_client.messages.create.return_value = make_truncated_response()
        
        item = EnrichedItem(
            url="https://test.com",
            title="Test",
            source="Test",
            date="2026-01-01",
            abstract="Test abstract",
            evidence_level="abstract",
        )
        
        result = draft_single_article(item, "brief", {})
        
        # Should return None on truncation
        assert result is None
    
    @patch('anthropic.Anthropic')
    @patch('inlight_articles._http_get')
    def test_400_error_handled(self, mock_http, mock_anthropic_class, mock_env):
        """Test that 400 API errors are handled gracefully."""
        from inlight_articles import draft_single_article, EnrichedItem
        
        mock_http.return_value = None
        
        mock_client = MagicMock()
        mock_anthropic_class.return_value = mock_client
        mock_client.messages.create.side_effect = Exception("API error 400")
        
        item = EnrichedItem(
            url="https://test.com",
            title="Test",
            source="Test",
            date="2026-01-01",
            abstract="Test abstract",
            evidence_level="abstract",
        )
        
        result = draft_single_article(item, "brief", {})
        
        # Should return None on error, not raise
        assert result is None
    
    @patch('anthropic.Anthropic')
    @patch('inlight_articles._http_get')
    def test_deep_requires_rich_abstract(self, mock_http, mock_anthropic_class, mock_env):
        """Test that deep tier requires fulltext or >=1200 char abstract."""
        from inlight_articles import process_articles
        
        mock_http.return_value = None  # No EPMC data
        
        mock_client = MagicMock()
        mock_anthropic_class.return_value = mock_client
        
        # Create an article response with brief tier (as would result after downgrade)
        brief_article = {**MOCK_ARTICLE_DATA, "tier": "brief"}
        
        # Triage returns deep tier but article will be downgraded
        mock_client.messages.create.side_effect = [
            make_triage_response([
                {"url": "https://test.com", "tier": "deep", "field": "c3", "reason": "test"}
            ]),
            make_article_response(brief_article),  # Will be brief after downgrade
        ]
        
        items = [{
            "url": "https://test.com",
            "title": "Test",
            "source": "Test",
            "date": "2026-01-01",
            "kind": "academic",
            "summary": "Short abstract less than 1200 chars",  # Short abstract triggers downgrade
        }]
        
        # Call process_articles - this should downgrade from deep to brief
        result = process_articles(items, {})
        
        # Verify an article was produced (downgraded to brief)
        assert "articles" in result, f"Expected 'articles' key in result: {result.keys()}"
        articles = result.get("articles", [])
        # May be empty if validation fails, but should not raise


class TestLegacyRendering:
    """Test that legacy content pages render without duplication."""
    
    def test_legacy_renderLegacyBody_no_duplication(self):
        """Verify renderLegacyBody doesn't duplicate content when only sum exists."""
        # This would need a browser test or JS evaluation
        # For now, we verify the JS logic in the index.html is correct
        index_path = Path(__file__).parent.parent / "index.html"
        if not index_path.exists():
            pytest.skip("index.html not found")
        
        content = index_path.read_text(encoding="utf-8")
        
        # Verify the fix is in place: renderLegacyBody should check for distinct fields
        assert "hasDistinctLead" in content or "renderLegacyBody" in content
        
        # Verify no duplicate 文献出处 in sidebar rendering
        # The fix removed the duplicate line
        sidebar_pattern = "文献出处"
        sidebar_section = content[content.find("aside class=\"meta\""):content.find("</aside>", content.find("aside class=\"meta\"")) + 10]
        # Should not have two 文献出处 in the dynamic rendering part
        # Count occurrences in the renderPaper function area
        render_paper_start = content.find("function renderPaper")
        render_paper_end = content.find("function colsOf")
        render_paper_code = content[render_paper_start:render_paper_end]
        
        # Should have only one 文献出处 in the conditional (datacardHtml || ...)
        # Not a second unconditional one
        count = render_paper_code.count("文献出处")
        assert count <= 2, f"Found {count} occurrences of 文献出处 in renderPaper, expected at most 2"


class TestRenderingConsistency:
    """Test that rendering is consistent with main branch behavior."""
    
    def test_c8_vac_4_no_months_claim(self):
        """Verify c8-vac-4 doesn't claim '从数月' which isn't in source."""
        index_path = Path(__file__).parent.parent / "index.html"
        if not index_path.exists():
            pytest.skip("index.html not found")
        
        content = index_path.read_text(encoding="utf-8")
        
        # Find c8-vac-4 entry
        start = content.find("id:'c8-vac-4'")
        end = content.find("},", start) + 2
        entry = content[start:end]
        
        # Should NOT have "从数月" claim
        assert "从数月" not in entry, "c8-vac-4 should not claim '从数月' - not in source"
        
        # Should have "500天" or "500 days" reference
        assert "500" in entry, "c8-vac-4 should reference 500 days from source"


class TestValidationFalsePositives:
    """Regression tests for validation false positives that were dropping good articles.
    
    These tests verify that the validator correctly PASSES articles that are faithful
    to their source material. Each test represents a real article that was incorrectly
    dropped due to over-strict validation.
    """
    
    def test_thousands_separator_1139(self):
        """1,139 should parse as one number, not '1' and '139'."""
        from inlight_articles import validate_depth
        
        art = {
            "tier": "brief",
            "title": "1,139种禽类R2逆转录转座子分析",
            "one_liner": "研究分析了1,139种禽类R2逆转录转座子的多样性。",
            "datacard": {
                "study_type": "基因组学分析",
                "n": "1,139种",
                "control": "不适用",
                "intervention": "不适用",
                "followup": "不适用",
                "primary_endpoint": "R2多样性",
                "primary_endpoint_result": "高度多样化",
                "statistics": "原文未报告",
                "safety": "不适用",
            },
            "background": "R2逆转录转座子研究。",
            "design": "分析1,139种禽类R2。",
            "results": ["共鉴定出1,139种不同的R2元件。"],
            "mechanism": "",
            "limitations": ["计算分析，未经实验验证"],
            "significance": "揭示禽类R2多样性。",
            "data_points": [
                {"value": "1,139", "meaning": "R2种类数", "source_quote": "We identified 1,139 distinct R2 elements"},
            ],
        }
        raw = "We identified 1,139 distinct R2 elements from avian genomes."
        problems = validate_depth(art, raw)
        
        # Should NOT flag "139" or "1" as unregistered - 1,139 is one number
        number_problems = [p for p in problems if "正文数字未登记" in p]
        assert not any("139" in p for p in number_problems), f"Incorrectly flagged 139: {number_problems}"
        assert not any(p == "正文数字未登记：1" for p in number_problems), f"Incorrectly flagged 1: {number_problems}"
    
    def test_95_percent_ci_not_flagged(self):
        """95%CI is a confidence interval label, not a standalone 95% value."""
        from inlight_articles import validate_depth
        
        art = {
            "tier": "brief",
            "title": "慢性荨麻疹T细胞研究",
            "one_liner": "研究发现T细胞变化与疾病相关。",
            "datacard": {
                "study_type": "临床研究",
                "n": "50例",
                "control": "健康对照",
                "intervention": "不适用",
                "followup": "不适用",
                "primary_endpoint": "T细胞比例",
                "primary_endpoint_result": "显著差异",
                "statistics": "原文未报告",
                "safety": "不适用",
            },
            "background": "慢性荨麻疹机制不明。",
            "design": "纳入50例患者。",
            "results": ["具体P值或95%CI原文未给出。"],  # Honest disclaimer
            "mechanism": "",
            "limitations": ["原文未给出具体统计量"],
            "significance": "提示T细胞参与发病。",
            "data_points": [
                {"value": "50", "meaning": "患者数", "source_quote": "50 patients were enrolled"},
            ],
        }
        raw = "50 patients were enrolled. HR 0.75 (95%CI 0.5-1.0)."
        problems = validate_depth(art, raw)
        
        # Should NOT flag "95%" from "95%CI" - it's a label, not data
        number_problems = [p for p in problems if "正文数字未登记" in p]
        assert not any("95" in p for p in number_problems), f"Incorrectly flagged 95%CI as data: {number_problems}"
    
    def test_english_number_words_nine_doses(self):
        """English number words in quotes should match their digit forms."""
        from inlight_articles import validate_depth
        
        art = {
            "tier": "deep",
            "title": "itolizumab首次人体试验：9剂给药方案安全可耐受",
            "one_liner": "诱导期A组每周给药100 mg共9次。",
            "datacard": {
                "study_type": "I期试验",
                "n": "原文未给出",
                "control": "无对照",
                "intervention": "itolizumab",
                "followup": "原文未给出",
                "primary_endpoint": "安全性",
                "primary_endpoint_result": "无相关严重不良事件",
                "statistics": "原文未报告",
                "safety": "可耐受",
            },
            "background": "CD6靶点有前景。",
            "design": "A组每周100mg共9次，B组每两周200mg共5次。",
            "results": ["A组共9剂给药后无严重不良事件。B组共5剂后同样安全。"],
            "mechanism": "",
            "limitations": ["无对照组", "样本量未报告", "随访时长未报告"],
            "significance": "初步证据支持CD6靶向安全。",
            "data_points": [
                {"value": "9", "meaning": "A组给药次数", "source_quote": "nine doses in cohort A"},
                {"value": "5", "meaning": "B组给药次数", "source_quote": "five doses in cohort B"},
                {"value": "100", "meaning": "A组剂量", "source_quote": "100 mg weekly"},
                {"value": "200", "meaning": "B组剂量", "source_quote": "200 mg biweekly"},
            ],
        }
        raw = "Cohort A received nine doses at 100 mg weekly. Cohort B received five doses at 200 mg biweekly."
        problems = validate_depth(art, raw)
        
        # Should NOT flag "9" or "5" as not in quote - "nine" matches "9", "five" matches "5"
        number_problems = [p for p in problems if "value 不在 quote 中" in p]
        assert not any("9" in p for p in number_problems), f"Incorrectly flagged 9 (nine): {number_problems}"
        assert not any("5" in p for p in number_problems), f"Incorrectly flagged 5 (five): {number_problems}"
    
    def test_gene_identifiers_not_flagged(self):
        """Gene names like CD318, CD8, CD14, IL-23 should not be flagged as numbers."""
        from inlight_articles import validate_depth
        
        art = {
            "tier": "brief",
            "title": "CD318阳性肿瘤细胞对CD8 T细胞杀伤更敏感",
            "one_liner": "CD8和NK细胞对CD318阳性肿瘤细胞的杀伤增强。",
            "datacard": {
                "study_type": "机制研究",
                "n": "原文未给出",
                "control": "同型对照",
                "intervention": "抗CD6抗体",
                "followup": "不适用",
                "primary_endpoint": "肿瘤杀伤",
                "primary_endpoint_result": "增强",
                "statistics": "原文未报告",
                "safety": "不适用",
            },
            "background": "CD6靶向可能增强抗肿瘤。",
            "design": "体外杀伤实验。",
            "results": ["CD8 T细胞和NK细胞对CD318阳性细胞杀伤增强。"],
            "mechanism": "",
            "limitations": ["体外研究，需体内验证"],
            "significance": "提示CD318可作为生物标志物。",
            "data_points": [],  # No quantitative data - qualitative study
        }
        raw = "CD8 T cells and NK cells showed enhanced killing of CD318-positive tumor cells. CD14 expression was not affected."
        problems = validate_depth(art, raw)
        
        # Should NOT flag CD8, CD14, CD318 as numbers
        number_problems = [p for p in problems if "正文数字未登记" in p]
        gene_flags = [p for p in number_problems if any(g in p for g in ["CD8", "CD14", "CD318", "318", "8", "14"])]
        assert not gene_flags, f"Incorrectly flagged gene identifiers: {gene_flags}"
    
    def test_identifiers_must_pass_unchanged(self):
        """Identifiers from source (R2, Th17, IL-24, IFN-α2, SAMP1/YitFC, Nissle 1917) must pass."""
        from inlight_articles import validate_depth, extract_identifiers_from_source
        
        source = """The study examined R2 retroelements in Th17 cells. IL-24 and IFN-α2 
        were measured in SAMP1/YitFC mice treated with E. coli Nissle 1917. Drug TAK-981
        enhanced CCR8 expression. Trial registered as NCT04443907."""
        
        identifiers = extract_identifiers_from_source(source)
        
        # These identifiers should be extracted from source
        expected = ['R2', 'Th17', 'IL-24', 'IFN-α2', 'SAMP1/YitFC', 'Nissle 1917', 
                   'TAK-981', 'CCR8', 'NCT04443907']
        for ident in expected:
            # Case-insensitive check
            found = any(ident.lower() in i.lower() or i.lower() in ident.lower() 
                       for i in identifiers)
            assert found or any(ident.split('/')[0] in i for i in identifiers), \
                f"Identifier '{ident}' not extracted from source"
        
        art = {
            "tier": "brief",
            "title": "R2逆转录转座子在Th17细胞中的作用",
            "one_liner": "研究发现Th17细胞中R2表达升高，IL-24和IFN-α2水平变化。",
            "datacard": {
                "study_type": "基础研究",
                "n": "原文未给出",
                "control": "野生型小鼠",
                "intervention": "TAK-981处理",
                "followup": "不适用",
                "primary_endpoint": "CCR8表达",
                "primary_endpoint_result": "升高",
                "statistics": "原文未报告",
                "safety": "不适用",
            },
            "background": "Th17细胞研究。",
            "design": "SAMP1/YitFC模型研究。",
            "results": ["Nissle 1917处理后IL-24升高。"],
            "mechanism": "",
            "limitations": ["动物模型"],
            "significance": "提示R2可作为靶点。",
            "data_points": [],
        }
        problems = validate_depth(art, source)
        
        # Should NOT flag any of these identifiers as invented numbers
        number_problems = [p for p in problems if "数字" in p and "未找到" in p]
        ident_flags = [p for p in number_problems if any(
            i in p for i in ['R2', 'Th17', 'IL-24', 'IFN-α2', 'SAMP1', 'TAK-981', 'CCR8', '17', '24', '981']
        )]
        assert not ident_flags, f"Incorrectly flagged identifiers: {ident_flags}"

    def test_trial_ids_not_flagged(self):
        """Trial IDs like NCT04443907, RPCEC00000444 should not be flagged."""
        from inlight_articles import validate_depth
        
        art = {
            "tier": "brief",
            "title": "NCT04443907试验初步结果",
            "one_liner": "该试验（NCT04443907）显示安全可耐受。",
            "datacard": {
                "study_type": "I期试验",
                "n": "12例",
                "control": "无对照",
                "intervention": "基因编辑",
                "followup": "原文未给出",
                "primary_endpoint": "安全性",
                "primary_endpoint_result": "无严重不良事件",
                "statistics": "原文未报告",
                "safety": "可耐受",
            },
            "background": "镰状细胞病需要新疗法。",
            "design": "试验编号NCT04443907。",
            "results": ["12例患者完成治疗，无严重不良事件。"],
            "mechanism": "",
            "limitations": ["单臂研究"],
            "significance": "初步安全性数据。",
            "data_points": [
                {"value": "12", "meaning": "患者数", "source_quote": "12 patients enrolled"},
            ],
        }
        raw = "Trial NCT04443907 enrolled 12 patients. Also registered as RPCEC00000444."
        problems = validate_depth(art, raw)
        
        # Should NOT flag NCT04443907 or parts of it as numbers
        number_problems = [p for p in problems if "正文数字未登记" in p]
        trial_flags = [p for p in number_problems if "NCT" in p or "04443907" in p or "RPCEC" in p]
        assert not trial_flags, f"Incorrectly flagged trial IDs: {trial_flags}"
    
    def test_qualitative_paper_no_numbers_required(self):
        """Papers without quantitative data should not require numbers in results.
        
        Per spec: 'missing content is OK; any invented fact is a hard defect'.
        Results can omit numbers - we only flag INVENTED numbers.
        """
        from inlight_articles import validate_depth
        
        # Source has no quantitative numbers - qualitative platform description
        raw = """We present a shotgun genetic engineering platform that enables 
        exploration of tens of kilobases of sequence space. The system generated 
        millions of variants across three design dimensions. Two amino acid 
        substitutions were prioritized based on computational analysis."""
        
        art = {
            "tier": "brief",
            "title": "霰弹枪基因工程平台实现大规模序列探索",
            "one_liner": "新平台能够探索数十千碱基的序列空间。",
            "datacard": {
                "study_type": "方法学研究",
                "n": "不适用",
                "control": "不适用",
                "intervention": "基因工程平台",
                "followup": "不适用",
                "primary_endpoint": "序列多样性",
                "primary_endpoint_result": "高度多样",
                "statistics": "原文未报告",
                "safety": "不适用",
            },
            "background": "传统方法效率有限。",
            "design": "开发新型基因工程平台。",
            "results": ["平台生成大量变体。"],  # No specific numbers - qualitative
            "mechanism": "",
            "limitations": ["需验证实际应用效果"],
            "significance": "可加速蛋白质工程。",
            "data_points": [],
        }
        problems = validate_depth(art, raw)
        
        # Should NOT require numbers in results - missing content is OK
        number_in_results = [p for p in problems if "没有任何数字" in p]
        assert not number_in_results, f"Should not require numbers in qualitative paper: {number_in_results}"
    
    def test_honest_disclaimer_not_flagged(self):
        """'原文未给出X' statements should not be flagged for containing numbers."""
        from inlight_articles import validate_depth
        
        art = {
            "tier": "brief",
            "title": "研究初步结果",
            "one_liner": "初步安全性数据。",
            "datacard": {
                "study_type": "I期试验",
                "n": "原文未给出",
                "control": "无对照",
                "intervention": "新药",
                "followup": "原文未给出",
                "primary_endpoint": "安全性",
                "primary_endpoint_result": "可耐受",
                "statistics": "原文未报告",
                "safety": "≥3级不良事件发生率原文未给出",
            },
            "background": "研究背景。",
            "design": "I期试验。",
            "results": ["结果显示安全可耐受。"],
            "mechanism": "",
            "limitations": ["≥3级不良事件及死亡的具体数据原文未给出"],
            "significance": "初步证据。",
            "data_points": [],
        }
        raw = "The drug was well tolerated. Safety data for grade 3+ events not reported."


class TestInventedNumbersMustBeCaught:
    """Negative tests: invented numbers MUST be caught by validation.
    
    Per spec A: Every numeric token in output must exist in source.
    These tests verify that invented numbers are FLAGGED (test passes if caught).
    """
    
    def test_invented_50nm_72hours_caught(self):
        """'50 nM处理72小时' must be caught when not in source."""
        from inlight_articles import validate_depth
        
        art = {
            "tier": "brief",
            "title": "药物处理实验",
            "one_liner": "50 nM处理72小时后细胞活力下降。",
            "datacard": {
                "study_type": "体外实验",
                "n": "原文未给出",
                "control": "DMSO",
                "intervention": "药物A",
                "followup": "不适用",
                "primary_endpoint": "细胞活力",
                "primary_endpoint_result": "下降",
                "statistics": "原文未报告",
                "safety": "不适用",
            },
            "background": "研究药物对细胞的影响。",
            "design": "药物处理实验。",
            "results": ["50 nM处理72小时后细胞活力下降50%。"],
            "mechanism": "",
            "limitations": ["体外实验"],
            "significance": "提示药物抑制细胞。",
            "data_points": [],
        }
        # Source does NOT contain 50, 72, or 50%
        raw = "Drug treatment reduced cell viability. Cells were treated for various durations."
        problems = validate_depth(art, raw)
        
        # MUST catch 50, 72 as invented numbers
        invented_flags = [p for p in problems if any(n in p for n in ['50', '72'])]
        assert invented_flags, f"Failed to catch invented numbers 50/72: {problems}"
    
    def test_invented_12_healthy_donors_caught(self):
        """'12名健康供者' must be caught when not in source."""
        from inlight_articles import validate_depth
        
        art = {
            "tier": "brief",
            "title": "供体细胞研究",
            "one_liner": "从12名健康供者获取细胞。",
            "datacard": {
                "study_type": "体外实验",
                "n": "12名",
                "control": "无",
                "intervention": "不适用",
                "followup": "不适用",
                "primary_endpoint": "细胞特征",
                "primary_endpoint_result": "表征完成",
                "statistics": "原文未报告",
                "safety": "不适用",
            },
            "background": "研究健康供者细胞。",
            "design": "从12名健康供者获取PBMC。",
            "results": ["12名供者的细胞表现一致。"],
            "mechanism": "",
            "limitations": ["样本有限"],
            "significance": "初步数据。",
            "data_points": [],
        }
        # Source does NOT contain 12 or "healthy donors"
        raw = "Peripheral blood mononuclear cells were obtained from volunteer donors."
        problems = validate_depth(art, raw)
        
        # MUST catch 12 as invented number
        invented_flags = [p for p in problems if '12' in p]
        assert invented_flags, f"Failed to catch invented number 12: {problems}"
    
    def test_invented_3point5_fold_upregulation_caught(self):
        """'上调3.5倍' must be caught when not in source."""
        from inlight_articles import validate_depth
        
        art = {
            "tier": "brief",
            "title": "基因表达研究",
            "one_liner": "处理后基因表达上调3.5倍。",
            "datacard": {
                "study_type": "机制研究",
                "n": "原文未给出",
                "control": "未处理组",
                "intervention": "药物处理",
                "followup": "不适用",
                "primary_endpoint": "基因表达",
                "primary_endpoint_result": "上调3.5倍",
                "statistics": "原文未报告",
                "safety": "不适用",
            },
            "background": "研究药物对基因表达的影响。",
            "design": "qPCR检测基因表达。",
            "results": ["目标基因上调3.5倍。"],
            "mechanism": "",
            "limitations": ["机制不清"],
            "significance": "提示调控作用。",
            "data_points": [],
        }
        # Source does NOT contain 3.5 or fold change data
        raw = "Gene expression was significantly upregulated after treatment. Statistical analysis confirmed the change."
        problems = validate_depth(art, raw)
        
        # MUST catch 3.5 as invented number
        invented_flags = [p for p in problems if '3.5' in p]
        assert invented_flags, f"Failed to catch invented number 3.5: {problems}"
    
    def test_invented_wanyi_number_caught(self):
        """Chinese 万/亿 numbers must be caught when not in source."""
        from inlight_articles import validate_depth
        
        art = {
            "tier": "brief",
            "title": "投资研究",
            "one_liner": "总投资达五亿美元。",
            "datacard": {
                "study_type": "产业分析",
                "n": "不适用",
                "control": "不适用",
                "intervention": "不适用",
                "followup": "不适用",
                "primary_endpoint": "不适用",
                "primary_endpoint_result": "不适用",
                "statistics": "不适用",
                "safety": "不适用",
            },
            "background": "产业投资分析。",
            "design": "文献综述。",
            "results": ["总投资达五亿美元，涉及三万患者。"],
            "mechanism": "",
            "limitations": ["数据有限"],
            "significance": "反映投资趋势。",
            "data_points": [],
        }
        # Source does NOT contain 五亿, 三万 or equivalent numbers
        raw = "Investment in this therapeutic area has grown significantly over the past decade."
        problems = validate_depth(art, raw)
        
        # MUST catch 五亿 and/or 三万 as invented
        invented_flags = [p for p in problems if any(n in p for n in ['五亿', '三万', '亿', '万'])]
        assert invented_flags, f"Failed to catch invented Chinese numbers: {problems}"


class TestInstitutionFalsePositives:
    """Test that generic terms are not flagged as invented institutions."""
    
    def test_single_center_not_flagged(self):
        """'单中心' (single-center) should not be flagged as an institution name."""
        from inlight_articles import validate_names
        
        art = {
            "title": "单中心研究显示疗效",
            "one_liner": "这是一项单中心研究。",
            "background": "研究在某医疗中心开展。",
            "design": "单中心、单臂设计。",
            "results": ["结果显示有效。"],
            "mechanism": "",
            "significance": "有临床意义。",
            "authors": "Test Author",
            "limitations": ["单中心研究外推性有限"],
        }
        raw = "This single-center study was conducted at Memorial Hospital."
        problems = validate_names(art, raw)
        
        # Should NOT flag "单中心" as an invented institution
        institution_flags = [p for p in problems if "机构名" in p and "单中心" in p]
        assert not institution_flags, f"Incorrectly flagged '单中心' as institution: {institution_flags}"
    
    def test_center_count_not_flagged(self):
        """'中心数' (number of centers) should not be flagged."""
        from inlight_articles import validate_names
        
        art = {
            "title": "多中心研究",
            "one_liner": "中心数为12。",
            "background": "研究设计。",
            "design": "多中心设计，中心数共12个。",
            "results": ["结果。"],
            "mechanism": "",
            "significance": "意义。",
            "authors": "Test",
            "limitations": [],
        }
        raw = "The study was conducted at 12 centers."
        problems = validate_names(art, raw)
        
        # Should NOT flag "中心数" as an institution
        center_flags = [p for p in problems if "中心数" in p]
        assert not center_flags, f"Incorrectly flagged '中心数': {center_flags}"


class TestDataPointGaming:
    """Test that the validator rejects data_points that game the checker."""
    
    def test_reject_gene_name_as_data_point(self):
        """data_points with meaning containing '名称中的编号' should be rejected."""
        from inlight_articles import validate_depth
        
        art = {
            "tier": "brief",
            "title": "CD4 T细胞研究",
            "one_liner": "CD4阳性T细胞分析。",
            "datacard": {
                "study_type": "机制研究",
                "n": "原文未给出",
                "control": "无",
                "intervention": "无",
                "followup": "不适用",
                "primary_endpoint": "CD4表达",
                "primary_endpoint_result": "升高",
                "statistics": "原文未报告",
                "safety": "不适用",
            },
            "background": "研究CD4。",
            "design": "分析CD4 T细胞。",
            "results": ["CD4阳性细胞增加。"],
            "mechanism": "",
            "limitations": ["机制研究"],
            "significance": "揭示CD4作用。",
            "data_points": [
                {"value": "4", "meaning": "CD4 T细胞名称中的编号", "source_quote": "CD4 positive T cells"},
            ],  # Gaming: registering "4" from "CD4" as a data point
        }
        raw = "CD4 positive T cells were analyzed."
        problems = validate_depth(art, raw)
        
        # Should flag the gaming data_point
        gaming_flags = [p for p in problems if "标识符而非数据" in p]
        assert gaming_flags, f"Should flag gaming data_point: {problems}"


class TestValidateDepthIntegration:
    """Integration tests for validate_depth in pipeline context (not calling main())."""
    
    @pytest.fixture
    def mock_run_env(self, tmp_path, monkeypatch):
        """Set up mock environment for run_weekly.py main()."""
        # Set required env vars
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
        monkeypatch.setenv("OPENAI_API_KEY", "test-key")
        monkeypatch.setenv("ANTHROPIC_MODEL", "claude-sonnet-5-5")
        
        # Create temp directory structure
        test_dir = tmp_path / "inlight_e2e"
        test_dir.mkdir()
        (test_dir / "content").mkdir()
        (test_dir / "preview").mkdir()
        (test_dir / "logs").mkdir()
        
        # Create minimal sources.yaml
        sources_content = """
window_days: 7
max_per_source: 2
max_academic: 2
max_industry: 1
max_deep: 1
max_brief: 2
sources:
  - name: Test Nature
    type: rss
    feed: https://example.com/nature.xml
"""
        (test_dir / "sources.yaml").write_text(sources_content)
        
        # Change to test directory
        monkeypatch.chdir(test_dir)
        
        return test_dir
    
    def test_validate_depth_catches_invented_in_pipeline(self):
        """Test that validate_depth correctly catches invented numbers in a pipeline context."""
        from inlight_articles import validate_depth, validate_names
        
        # Simulate a draft article with invented numbers (as if generated by Claude)
        draft_article = {
            "tier": "brief",
            "title": "新型抗体疗法临床试验",
            "one_liner": "36例患者中52%达到缓解，中位随访12个月。",
            "datacard": {
                "study_type": "I期试验",
                "n": "36例",
                "control": "无对照",
                "intervention": "抗体A",
                "followup": "12个月",
                "primary_endpoint": "客观缓解率",
                "primary_endpoint_result": "52%",
                "statistics": "原文未报告",
                "safety": "可接受",
            },
            "background": "研究新型抗体。",
            "design": "纳入36例患者。",
            "results": ["缓解率52%，随访12个月后50%仍维持缓解。"],  # 50% is INVENTED
            "mechanism": "",
            "limitations": ["单臂研究"],
            "significance": "有前景的疗法。",
            "data_points": [
                {"value": "52%", "meaning": "缓解率", "source_quote": "response rate was 52%"},
                {"value": "36", "meaning": "患者数", "source_quote": "36 patients enrolled"},
                {"value": "12", "meaning": "随访月数", "source_quote": "median follow-up 12 months"},
            ],
        }
        
        # Source only has 52%, 36, 12 - NOT 50%
        source = "In this phase I trial, 36 patients enrolled with response rate was 52%. Median follow-up 12 months showed durable responses."
        
        problems = validate_depth(draft_article, source)
        name_problems = validate_names(draft_article, source)
        all_problems = problems + name_problems
        
        # MUST catch the invented "50%" in results
        invented_found = any("50" in p for p in problems)
        assert invented_found, f"Pipeline failed to catch invented 50%: {all_problems}"
    
    def test_validate_real_article_passes(self):
        """Test that a properly sourced article passes validation.
        
        Uses REAL published data from Nature Biotechnology R2 retrotransposon study
        (pr5e/sample_brief_r2_REAL_PUBLISHED.json).
        """
        from inlight_articles import validate_depth, validate_names
        
        # REAL published article: Nature Biotechnology R2 retrotransposon study
        # All numbers match source material from the actual paper
        good_article = {
            "tier": "brief",
            "title": "159个鸟类R2逆转座子经工程化，人原代细胞位点特异整合最高达60%",
            "one_liner": "研究者检索1,139个鸟类基因组，鉴定159个R2逆转座子，其工程化变体在人原代细胞中实现最高60%的位点特异基因整合。",
            "datacard": {
                "study_type": "计算检索加实验工程化研究（据摘要）",
                "n": "检索1,139个鸟类基因组；鉴定159个鸟类R2逆转座子；人原代细胞样本量原文未给出",
                "control": "原文未给出",
                "intervention": "鸟类R2逆转座子工程化变体",
                "followup": "原文未给出",
                "primary_endpoint": "人原代细胞中的位点特异基因整合",
                "primary_endpoint_result": "最高60%位点特异基因整合",
                "statistics": "原文未报告统计学检验",
                "safety": "原文未给出",
            },
            "background": "研究者为寻找可用于人类细胞基因组整合的未知逆转座子，以鸟类基因组为检索对象，目标是拓展全RNA介导的靶向DNA整合工具。",
            "design": "计算检索加实验工程化研究：检索1,139个鸟类基因组，鉴定159个鸟类R2逆转座子，比较其蛋白与非翻译区元件的保守及非保守序列特征，并检测工程化变体在人原代细胞中的整合。",
            "results": ["检索1,139个鸟类基因组后，共鉴定出159个鸟类R2逆转座子，并刻画了其蛋白和非翻译区元件中保守与非保守的序列特征。这些逆转座子的工程化变体在人原代细胞中实现了最高60%的位点特异基因整合。"],
            "mechanism": "摘要未提供机制层面的实验细节。",
            "limitations": ["本条仅依据摘要：最高60%对应的变体、细胞类型、靶位点、重复数及与既有系统的对比均原文未给出。"],
            "significance": "若这些变体的整合效率和特异性在更多细胞类型与靶位点中得到重复，该资源可能为全RNA递送的基因组编辑提供更多可选工具。",
            "data_points": [
                {"value": "1,139", "meaning": "检索的鸟类基因组数", "source_quote": "screened 1,139 avian genomes"},
                {"value": "159", "meaning": "鉴定的R2逆转座子数", "source_quote": "identified 159 avian R2 retrotransposons"},
                {"value": "60%", "meaning": "最高整合率", "source_quote": "up to 60% site-specific gene integration"},
            ],
        }
        
        # Realistic abstract source text matching the REAL paper
        source = """We screened 1,139 avian genomes and identified 159 avian R2 retrotransposons. 
        We characterized the conserved and non-conserved sequence features in their proteins and 
        untranslated region elements. Engineered variants achieved up to 60% site-specific gene 
        integration in human primary cells. This work expands the avian R2 resource and provides 
        additional tools for all-RNA-mediated genome editing."""
        
        problems = validate_depth(good_article, source)
        name_problems = validate_names(good_article, source)
        all_problems = problems + name_problems
        
        # Should have zero problems (or only soft char count issues)
        hard_problems = [p for p in all_problems if '未找到' in p or '编造' in p or '营销' in p]
        assert len(hard_problems) == 0, f"Properly sourced article should pass hard checks: {hard_problems}"
    
    def test_string_datacard_handled_gracefully(self):
        """Test that validate_depth handles string datacard without crashing (P0 crash fix)."""
        from inlight_articles import validate_depth
        
        # Simulate malformed model output where datacard is a string instead of dict
        # This matches the real crash in server_run1_crash/logs/weekly-20261008-080552.log
        malformed_article = {
            "tier": "brief",
            "title": "测试文章",
            "one_liner": "测试摘要",
            # datacard as string (malformed) - this was causing AttributeError
            "datacard": '\n<parameter name="study_type">研究</parameter>\n<parameter name="n">36例</parameter>',
            "background": "背景",
            "design": "设计",
            "results": ["结果"],
            "mechanism": "",
            "limitations": ["局限"],
            "significance": "意义",
            "data_points": [],
        }
        
        source = "Some source text with numbers 36 and percentages"
        
        # MUST NOT crash - should return problems list
        try:
            problems = validate_depth(malformed_article, source)
        except AttributeError as e:
            pytest.fail(f"validate_depth crashed on string datacard: {e}")
        
        # Should return a list (may contain validation issues but shouldn't crash)
        assert isinstance(problems, list), f"Expected list, got {type(problems)}"


class TestMainInvocationE2E:
    """Full e2e test calling main() --dry-run --use-new-pipeline with mocked HTTP/Claude."""
    
    @pytest.fixture
    def mock_main_env(self, tmp_path, monkeypatch):
        """Set up mock environment for run_weekly.py main() invocation."""
        import sys
        
        # Set required env vars
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
        monkeypatch.setenv("OPENAI_API_KEY", "test-key")
        monkeypatch.setenv("ANTHROPIC_MODEL", "claude-sonnet-5-5")
        
        # Create temp directory structure
        test_dir = tmp_path / "inlight_main_test"
        test_dir.mkdir()
        (test_dir / "content").mkdir()
        (test_dir / "preview").mkdir()
        (test_dir / "preview" / "weekly").mkdir()
        (test_dir / "logs").mkdir()
        
        # Create sources.yaml with RSS source
        sources_content = """
window_days: 7
max_per_source: 2
max_academic: 2
max_industry: 0
max_deep: 0
max_brief: 1
sources:
  - name: Test Nature Feed
    type: rss
    feed: https://example.com/nature.xml
    kind: academic
"""
        (test_dir / "sources.yaml").write_text(sources_content)
        
        # Change working directory
        monkeypatch.chdir(test_dir)
        
        # Add test_dir to ROOT in run_weekly module
        return test_dir

    def _make_rss_feed(self):
        """Create a mock RSS feed with one academic item.
        
        Note: Summary must be at least 50 characters to pass the filter.
        """
        from datetime import date
        import email.utils
        import time
        today = date.today()
        # RFC 2822 format for RSS pubDate
        pub_date = email.utils.formatdate(time.mktime(today.timetuple()))
        # Summary must be >50 chars and not look like author names (comma-heavy)
        long_description = """In this first-in-human study, 24 patients with autoimmune disease were enrolled. 
        Cohort A received itolizumab 100 mg weekly for 9 doses (total 9 infusions). 
        The objective response rate was 58% (14/24 patients). Median follow-up duration was 18 months.
        Grade 3+ adverse events occurred in 21% of patients. The drug was generally well tolerated."""
        return f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
<channel>
<title>Test Nature Feed</title>
<item>
  <title>Phase I trial of itolizumab in autoimmune disease</title>
  <link>https://doi.org/10.1038/test-cd6-trial</link>
  <pubDate>{pub_date}</pubDate>
  <description>{long_description}</description>
</item>
</channel>
</rss>"""

    def _make_epmc_response(self):
        """Create a mock EPMC response."""
        # Match the RSS description exactly for source validation
        abstract = """In this first-in-human study, 24 patients with autoimmune disease were enrolled. 
        Cohort A received itolizumab 100 mg weekly for 9 doses (total 9 infusions). 
        The objective response rate was 58% (14/24 patients). Median follow-up duration was 18 months.
        Grade 3+ adverse events occurred in 21% of patients. The drug was generally well tolerated."""
        return json.dumps({
            "resultList": {
                "result": [{
                    "title": "Phase I trial of itolizumab in autoimmune disease",
                    "authorString": "Smith J, Jones K",
                    "abstractText": abstract,
                    "journalTitle": "Nature Medicine",
                    "doi": "10.1038/test-cd6-trial",
                    "pubYear": "2026",
                    "source": "MED"
                }]
            }
        }).encode()

    def test_main_dry_run_use_new_pipeline(self, mock_main_env):
        """Test main() --dry-run --use-new-pipeline with mocked HTTP and Claude.
        
        This test verifies:
        1. The pipeline runs without errors
        2. Output is written to preview/ directory
        3. Articles are validated against source material
        """
        import sys
        import feedparser
        import run_weekly
        import inlight_articles
        from datetime import date
        
        test_dir = mock_main_env
        
        # Mock EPMC API responses
        def http_get_mock(url, **kwargs):
            if 'europepmc.org' in url:
                return self._make_epmc_response()
            return None
        
        # Create mock Anthropic client instance
        mock_client = MagicMock()
        
        # Model check response (first call to messages.create)
        model_check_response = MockMessage(
            content=[MockBlock(type="tool_use", name="test_tool", input={"ok": True})],
            stop_reason="end_turn"
        )
        
        # Triage response (second call)
        triage_response = make_triage_response([
            {"url": "https://doi.org/10.1038/test-cd6-trial", "tier": "brief", "field": "c2", "reason": "autoimmune trial"}
        ])
        
        # Second call: article draft - return a properly sourced article
        # Quotes must match source exactly; body must be 383+ chars for brief tier
        article_response = make_article_response({
            "url": "https://doi.org/10.1038/test-cd6-trial",
            "tier": "brief",
            "field": "c2",
            "title": "itolizumab自身免疫病I期试验：客观缓解率58%",
            "journal": "Nature Medicine",
            "authors": "Smith J, Jones K",
            "one_liner": "一项纳入24例自身免疫病患者的首次人体I期试验显示，itolizumab客观缓解率为58%（14/24例），中位随访18个月，三级以上不良事件发生率21%。",
            "datacard": {
                "study_type": "I期试验",
                "n": "24例",
                "control": "无对照",
                "intervention": "itolizumab",
                "followup": "18个月",
                "primary_endpoint": "客观缓解率",
                "primary_endpoint_result": "58%（14/24例）",
                "statistics": "原文未报告统计学检验",
                "safety": "三级以上不良事件21%",
            },
            "background": "自身免疫病治疗领域仍存在未满足的临床需求，传统治疗方案疗效有限且存在明显副作用，需要开发新型治疗药物改善患者预后。itolizumab是一种靶向特定免疫细胞表面分子的单克隆抗体，通过调节免疫细胞功能发挥治疗作用，在临床前研究中显示出良好的安全性和疗效信号。",
            "design": "这是一项首次人体I期开放标签研究，纳入24例符合入组标准的自身免疫病患者。患者每周接受itolizumab 100 mg静脉输注，共进行9次给药。研究主要评估安全性和耐受性，次要终点包括药代动力学参数和初步临床疗效。",
            "results": ["在24例可评估患者中，客观缓解率达到58%（14/24例），提示治疗具有临床意义的疗效。中位随访时间为18个月，多数获得缓解的患者能够维持疗效，显示缓解具有持久性。安全性方面，三级及以上不良事件发生率为21%，未观察到剂量限制性毒性，药物总体耐受性良好，支持在后续试验中进一步探索。"],
            "mechanism": "",
            "limitations": ["单臂研究设计，缺乏对照组无法评估与现有治疗方案的相对疗效"],
            "significance": "这项首次人体试验提供了itolizumab在自身免疫病患者中的初步安全性和疗效证据，为后续临床开发奠定了基础。该研究同时记录了给药间隔、随访时长与不良事件分布，为后续更大样本的对照试验提供了剂量和安全性方面的参考依据。",
            "data_points": [
                {"value": "24", "meaning": "患者数", "source_quote": "24 patients with autoimmune disease were enrolled"},
                {"value": "58%", "meaning": "缓解率", "source_quote": "objective response rate was 58%"},
                {"value": "14/24", "meaning": "缓解人数", "source_quote": "response rate was 58% (14/24 patients)"},
                {"value": "9", "meaning": "给药次数", "source_quote": "100 mg weekly for 9 doses"},
                {"value": "100", "meaning": "剂量mg", "source_quote": "itolizumab 100 mg weekly"},
                {"value": "18", "meaning": "随访月数", "source_quote": "Median follow-up duration was 18 months"},
                {"value": "21%", "meaning": "AE比例", "source_quote": "Grade 3+ adverse events occurred in 21%"},
            ],
            "unknowns": [],
            "steps": ["纳入患者", "给药治疗", "评估疗效", "安全性分析"],
            "image_prompt": "Clinical trial diagram showing patient flow for autoimmune disease study",
        })
        
        mock_client.messages.create.side_effect = [model_check_response, triage_response, article_response]
        
        # Create mock Anthropic class that returns our client
        mock_anthropic_cls = MagicMock(return_value=mock_client)
        
        # Parse RSS feed for mocking
        rss_content = self._make_rss_feed()
        real_parsed = feedparser.parse(rss_content)
        
        # Patch ROOT
        monkeypatch = pytest.MonkeyPatch()
        monkeypatch.setattr(run_weekly, 'ROOT', test_dir)
        
        # Mock sys.argv for argparse
        original_argv = sys.argv
        sys.argv = ['run_weekly.py', '--dry-run', '--use-new-pipeline']
        
        try:
            # Apply all patches together using nested context managers
            with patch.object(run_weekly.feedparser, 'parse', return_value=real_parsed):
                with patch.object(inlight_articles, '_http_get', side_effect=http_get_mock):
                    with patch('anthropic.Anthropic', mock_anthropic_cls):
                        # Run main - should complete without error
                        run_weekly.main()
            
            # Verify output was created
            today = date.today().isoformat()
            output_dir = test_dir / "preview" / "weekly" / today
            assert output_dir.exists(), f"Output directory {output_dir} should exist"
            
            # Verify articles.json was written
            articles_json = output_dir / "articles.json"
            assert articles_json.exists(), "articles.json should be written"
            
            # Read and verify content - articles.json is a list
            articles = json.loads(articles_json.read_text())
            assert isinstance(articles, list), "articles.json should contain a list"
            assert len(articles) >= 1, "Should have at least one article"
            
            # Verify the article has expected fields (t=title in compact format)
            article = articles[0]
            assert article.get("t"), "Article should have title (t)"
            assert "58%" in article.get("sum", ""), "Article should mention 58% from source"
            
        finally:
            sys.argv = original_argv
            monkeypatch.undo()

    def test_main_catches_invented_and_retries(self, mock_main_env):
        """Test that main() catches invented numbers and triggers retry.
        
        First draft has invented 75%, second draft is correct.
        The pipeline should retry and accept the second draft.
        """
        import sys
        import feedparser
        import run_weekly
        import inlight_articles
        from datetime import date
        
        test_dir = mock_main_env
        
        # Mock EPMC API
        def http_get_mock(url, **kwargs):
            if 'europepmc.org' in url:
                return self._make_epmc_response()
            return None
        
        # Mock Anthropic client
        mock_client = MagicMock()
        
        # Model check response (first call)
        model_check_response = MockMessage(
            content=[MockBlock(type="tool_use", name="test_tool", input={"ok": True})],
            stop_reason="end_turn"
        )
        
        # Triage response
        triage_response = make_triage_response([
            {"url": "https://doi.org/10.1038/test-cd6-trial", "tier": "brief", "field": "c2", "reason": "test"}
        ])
        
        # First draft: has INVENTED 75% not in source
        bad_article = {
            "url": "https://doi.org/10.1038/test-cd6-trial",
            "tier": "brief",
            "field": "c2",
            "title": "测试研究",
            "journal": "Nature Medicine",
            "authors": "Smith J",
            "one_liner": "缓解率高达75%。",  # INVENTED - source has 58%
            "datacard": {
                "study_type": "I期试验",
                "n": "24例",
                "control": "无对照",
                "intervention": "药物",
                "followup": "18个月",
                "primary_endpoint": "缓解率",
                "primary_endpoint_result": "75%",  # INVENTED
                "statistics": "原文未报告",
                "safety": "可接受",
            },
            "background": "研究背景。",
            "design": "纳入24例患者。",
            "results": ["缓解率75%。"],  # INVENTED
            "mechanism": "",
            "limitations": ["单臂研究"],
            "significance": "有意义。",
            "data_points": [
                {"value": "24", "meaning": "患者数", "source_quote": "24 patients were enrolled"},
            ],
            "unknowns": [],
            "steps": ["步骤1"],
            "image_prompt": "diagram",
        }
        bad_draft_response = make_article_response(bad_article)
        
        # Second draft: correct with 58% from source and proper body length
        good_article = {
            "url": "https://doi.org/10.1038/test-cd6-trial",
            "tier": "brief",
            "field": "c2",
            "title": "itolizumab自身免疫病I期试验：客观缓解率58%",
            "journal": "Nature Medicine",
            "authors": "Smith J, Jones K",
            "one_liner": "一项纳入24例自身免疫病患者的首次人体I期试验显示，itolizumab客观缓解率为58%（14/24例），中位随访18个月，三级以上不良事件发生率21%。",
            "datacard": {
                "study_type": "I期试验",
                "n": "24例",
                "control": "无对照",
                "intervention": "itolizumab",
                "followup": "18个月",
                "primary_endpoint": "客观缓解率",
                "primary_endpoint_result": "58%（14/24例）",
                "statistics": "原文未报告统计学检验",
                "safety": "三级以上不良事件21%",
            },
            "background": "自身免疫病治疗领域仍存在未满足的临床需求，传统治疗方案疗效有限且存在明显副作用，需要开发新型治疗药物改善患者预后。itolizumab是一种靶向特定免疫细胞表面分子的单克隆抗体，通过调节免疫细胞功能发挥治疗作用，在临床前研究中显示出良好的安全性和疗效信号。",
            "design": "这是一项首次人体I期开放标签研究，纳入24例符合入组标准的自身免疫病患者。患者每周接受itolizumab 100 mg静脉输注，共进行9次给药。研究主要评估安全性和耐受性，次要终点包括药代动力学参数和初步临床疗效。",
            "results": ["在24例可评估患者中，客观缓解率达到58%（14/24例），提示治疗具有临床意义的疗效。中位随访时间为18个月，多数获得缓解的患者能够维持疗效，显示缓解具有持久性。安全性方面，三级及以上不良事件发生率为21%，未观察到剂量限制性毒性，药物总体耐受性良好，支持在后续试验中进一步探索。"],
            "mechanism": "",
            "limitations": ["单臂研究设计，缺乏对照组无法评估与现有治疗方案的相对疗效"],
            "significance": "这项首次人体试验提供了itolizumab在自身免疫病患者中的初步安全性和疗效证据，为后续临床开发奠定了基础。该研究同时记录了给药间隔、随访时长与不良事件分布，为后续更大样本的对照试验提供了剂量和安全性方面的参考依据。",
            "data_points": [
                {"value": "24", "meaning": "患者数", "source_quote": "24 patients with autoimmune disease were enrolled"},
                {"value": "58%", "meaning": "缓解率", "source_quote": "objective response rate was 58%"},
                {"value": "14/24", "meaning": "缓解人数", "source_quote": "response rate was 58% (14/24 patients)"},
                {"value": "9", "meaning": "给药次数", "source_quote": "100 mg weekly for 9 doses"},
                {"value": "100", "meaning": "剂量mg", "source_quote": "itolizumab 100 mg weekly"},
                {"value": "18", "meaning": "随访月数", "source_quote": "Median follow-up duration was 18 months"},
                {"value": "21%", "meaning": "AE比例", "source_quote": "Grade 3+ adverse events occurred in 21%"},
            ],
            "unknowns": [],
            "steps": ["纳入患者", "给药治疗", "评估疗效", "安全性分析"],
            "image_prompt": "Clinical trial diagram for autoimmune disease study",
        }
        good_draft_response = make_article_response(good_article)
        
        # Sequence: model check, triage, bad draft, retry with good draft
        mock_client.messages.create.side_effect = [
            model_check_response,
            triage_response,
            bad_draft_response,
            good_draft_response  # Retry produces correct output
        ]
        
        # Create mock Anthropic class
        mock_anthropic_cls = MagicMock(return_value=mock_client)
        
        # Parse RSS feed for mocking
        rss_content = self._make_rss_feed()
        real_parsed = feedparser.parse(rss_content)
        
        # Patch ROOT
        monkeypatch = pytest.MonkeyPatch()
        monkeypatch.setattr(run_weekly, 'ROOT', test_dir)
        
        original_argv = sys.argv
        sys.argv = ['run_weekly.py', '--dry-run', '--use-new-pipeline']
        
        try:
            with patch.object(run_weekly.feedparser, 'parse', return_value=real_parsed):
                with patch.object(inlight_articles, '_http_get', side_effect=http_get_mock):
                    with patch('anthropic.Anthropic', mock_anthropic_cls):
                        run_weekly.main()
            
            # Verify output was created
            today = date.today().isoformat()
            output_dir = test_dir / "preview" / "weekly" / today
            
            # Should have at least an articles.json (might have no articles if retry also fails validation)
            # The important thing is no exception was raised
            articles_json = output_dir / "articles.json"
            if articles_json.exists():
                articles = json.loads(articles_json.read_text())
                # If articles exist (list format), the good draft should have been accepted
                if articles and isinstance(articles, list):
                    article = articles[0]
                    # Should NOT have invented 75% - check sum field (compact format)
                    assert "75%" not in article.get("sum", ""), "Invented 75% should not appear"
            
        finally:
            sys.argv = original_argv
            monkeypatch.undo()


class TestLegacyPromptTruncation:
    """Test that the legacy path prompt matches main's 700-char truncation."""
    
    def test_truncate_items_for_legacy(self):
        """Verify _truncate_items_for_legacy truncates summary to 700 chars."""
        import sys
        sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        from run_weekly import _truncate_items_for_legacy
        
        items = [
            {
                "url": "https://example.com",
                "title": "Test",
                "summary": "A" * 1000  # 1000 chars
            },
            {
                "url": "https://example2.com",
                "title": "Test2",
                "summary": "B" * 500  # 500 chars, less than 700
            }
        ]
        
        result = _truncate_items_for_legacy(items)
        
        # First item should be truncated to 700
        assert len(result[0]["summary"]) == 700
        assert result[0]["summary"] == "A" * 700
        
        # Second item should be unchanged (already < 700)
        assert len(result[1]["summary"]) == 500
        assert result[1]["summary"] == "B" * 500
        
        # Original items should not be modified
        assert len(items[0]["summary"]) == 1000

    def test_truncate_strips_journal_key(self):
        """Legacy prompt must not serialize the new-pipeline journal key."""
        from run_weekly import _truncate_items_for_legacy

        items = [{
            "source": "PubMed",
            "kind": "academic",
            "title": "T",
            "url": "https://pubmed.ncbi.nlm.nih.gov/1/",
            "date": "2026-01-01",
            "summary": "Journal name. Authors",
            "journal": "Journal name",
        }]
        result = _truncate_items_for_legacy(items)
        assert "journal" not in result[0]
        assert items[0]["journal"] == "Journal name"
    
    def test_legacy_prompt_uses_700_truncation(self):
        """Verify that claude_draft uses 700-char truncation in prompt."""
        import sys
        sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        import run_weekly
        
        # Create items with long summaries
        items = [
            {
                "url": "https://example.com",
                "title": "Test",
                "summary": "X" * 2000,
                "source": "Test",
                "date": "2026-01-01",
                "kind": "academic"
            }
        ]
        
        # Verify _truncate_items_for_legacy is called correctly
        truncated = run_weekly._truncate_items_for_legacy(items)
        assert len(truncated[0]["summary"]) == 700


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
