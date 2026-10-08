#!/usr/bin/env python3
"""Tests for inlight_articles.py

Unit tests for:
- enrich_item() with mocked HTTP + one live EPMC call (guarded by env)
- validate_depth() including fabricated numbers and fake quotes
- number extraction
- Integration test with mocked model response
"""

import os
import json
import pytest
import unittest
from unittest.mock import patch, MagicMock

import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from inlight_articles import (
    extract_doi,
    extract_numbers_from_text,
    normalize_whitespace,
    cn_len,
    validate_depth,
    enrich_item,
    epmc_core_search,
    EnrichedItem,
    MARKETING_BLOCKLIST,
)


class TestExtractDoi(unittest.TestCase):
    """Test DOI extraction from various URL formats."""
    
    def test_doi_org_url(self):
        url = "https://doi.org/10.1038/s41591-026-04704-z"
        self.assertEqual(extract_doi(url), "10.1038/s41591-026-04704-z")
    
    def test_dx_doi_org_url(self):
        url = "https://dx.doi.org/10.1038/s41591-026-04704-z"
        self.assertEqual(extract_doi(url), "10.1038/s41591-026-04704-z")
    
    def test_nature_article_url(self):
        url = "https://www.nature.com/articles/s41591-026-04704-z"
        self.assertEqual(extract_doi(url), "10.1038/s41591-026-04704-z")
    
    def test_science_url(self):
        url = "https://www.science.org/doi/10.1126/science.abc1234"
        self.assertEqual(extract_doi(url), "10.1126/science.abc1234")
    
    def test_biorxiv_url(self):
        url = "https://www.biorxiv.org/content/10.1101/2026.01.01.123456"
        self.assertEqual(extract_doi(url), "10.1101/2026.01.01.123456")
    
    def test_no_doi(self):
        url = "https://example.com/article/12345"
        self.assertEqual(extract_doi(url), "")


class TestExtractNumbers(unittest.TestCase):
    """Test number extraction from text."""
    
    def test_percentage(self):
        text = "缓解率达到52%"
        numbers = extract_numbers_from_text(text)
        self.assertTrue(any("52%" in n or "52 %" in n for n in numbers))
    
    def test_fold_change(self):
        text = "提升约3.5倍"
        numbers = extract_numbers_from_text(text)
        self.assertTrue(any("3.5" in n for n in numbers))
    
    def test_sample_size(self):
        text = "纳入36例患者"
        numbers = extract_numbers_from_text(text)
        self.assertTrue(any("36" in n for n in numbers))
    
    def test_time_unit(self):
        text = "随访12个月"
        numbers = extract_numbers_from_text(text)
        self.assertTrue(any("12" in n for n in numbers))
    
    def test_hr_with_ci(self):
        text = "HR=0.66 (95%CI 0.51-0.85)"
        numbers = extract_numbers_from_text(text)
        self.assertTrue(any("0.66" in n for n in numbers))
        self.assertTrue(any("95%" in n for n in numbers))
    
    def test_chinese_numerals(self):
        text = "约五百天后"
        numbers = extract_numbers_from_text(text)
        self.assertTrue(any("五百" in n for n in numbers))
    
    def test_scientific_notation(self):
        text = "浓度为6×10^7"
        numbers = extract_numbers_from_text(text)
        self.assertTrue(any("6" in n and "10" in n for n in numbers) or any("6" in n for n in numbers))


class TestNormalizeWhitespace(unittest.TestCase):
    """Test whitespace normalization."""
    
    def test_basic(self):
        text = "  hello   world  "
        self.assertEqual(normalize_whitespace(text), "hello world")
    
    def test_newlines(self):
        text = "hello\n\nworld"
        self.assertEqual(normalize_whitespace(text), "hello world")
    
    def test_tabs(self):
        text = "hello\t\tworld"
        self.assertEqual(normalize_whitespace(text), "hello world")


class TestCnLen(unittest.TestCase):
    """Test Chinese character counting."""
    
    def test_chinese_only(self):
        text = "这是一个测试"
        self.assertEqual(cn_len(text), 6)
    
    def test_chinese_with_punctuation(self):
        text = "这是测试，真的。"
        self.assertEqual(cn_len(text), 8)
    
    def test_mixed_text(self):
        text = "Nature发表了一篇论文"
        count = cn_len(text)
        self.assertEqual(count, 8)
    
    def test_numbers_as_unit(self):
        text = "发现52%的患者"
        count = cn_len(text)
        self.assertTrue(count > 0)


class TestMarketingBlocklist(unittest.TestCase):
    """Test marketing word detection."""
    
    def test_detect_breakthrough(self):
        self.assertTrue(MARKETING_BLOCKLIST.search("重磅研究发现"))
        self.assertTrue(MARKETING_BLOCKLIST.search("颠覆性成果"))
        self.assertTrue(MARKETING_BLOCKLIST.search("改写教科书"))
    
    def test_normal_text(self):
        self.assertIsNone(MARKETING_BLOCKLIST.search("研究发现了一种新的机制"))


class TestValidateDepth(unittest.TestCase):
    """Test validation of generated articles."""
    
    def test_missing_datacard_fields(self):
        art = {
            "tier": "deep",
            "title": "测试标题",
            "one_liner": "一句话结论",
            "datacard": {
                "study_type": "",  # Empty, should fail
                "n": "36例",
                "control": "随机对照",
                "intervention": "药物A",
                "followup": "12个月",
                "primary_endpoint": "ORR",
                "primary_endpoint_result": "52%",
                "statistics": "P<0.001",
                "safety": "未报告",
            },
            "results": ["结果1含数字52%", "结果2含数字36"],
            "limitations": ["局限1具体", "局限2具体", "局限3具体"],
            "data_points": [
                {"value": "52%", "meaning": "缓解率", "source_quote": "the response rate was 52%"},
                {"value": "36", "meaning": "患者数", "source_quote": "36 patients were enrolled"},
            ],
        }
        raw = "the response rate was 52% in 36 patients who were enrolled"
        problems = validate_depth(art, raw)
        self.assertTrue(any("study_type" in p for p in problems))
    
    def test_fabricated_number(self):
        art = {
            "tier": "brief",
            "title": "测试标题",
            "one_liner": "缓解率达到75%",  # 75% not in source
            "datacard": {
                "study_type": "临床试验",
                "n": "36例",
                "control": "随机对照",
                "intervention": "药物A",
                "followup": "12个月",
                "primary_endpoint": "ORR",
                "primary_endpoint_result": "52%",
                "statistics": "P<0.001",
                "safety": "未报告",
            },
            "results": ["缓解率52%"],
            "limitations": ["局限1"],
            "data_points": [
                {"value": "52%", "meaning": "缓解率", "source_quote": "response rate was 52%"},
            ],
        }
        raw = "response rate was 52%"
        problems = validate_depth(art, raw)
        self.assertTrue(any("75%" in p for p in problems))
    
    def test_fake_quote(self):
        art = {
            "tier": "brief",
            "title": "测试标题",
            "one_liner": "结果显示52%",
            "datacard": {
                "study_type": "临床试验",
                "n": "36例",
                "control": "随机对照",
                "intervention": "药物A",
                "followup": "12个月",
                "primary_endpoint": "ORR",
                "primary_endpoint_result": "52%",
                "statistics": "原文未报告",
                "safety": "不适用",
            },
            "results": ["缓解率52%"],
            "limitations": ["局限1"],
            "data_points": [
                {
                    "value": "52%",
                    "meaning": "缓解率",
                    "source_quote": "this quote does not exist in the source material at all"
                },
            ],
        }
        raw = "The actual source says something completely different about response rates."
        problems = validate_depth(art, raw)
        self.assertTrue(any("无法回溯" in p for p in problems))
    
    def test_empty_limitations(self):
        art = {
            "tier": "deep",
            "title": "测试标题",
            "one_liner": "测试",
            "datacard": {
                "study_type": "临床试验",
                "n": "36例",
                "control": "随机对照",
                "intervention": "药物A",
                "followup": "12个月",
                "primary_endpoint": "ORR",
                "primary_endpoint_result": "52%",
                "statistics": "P<0.001",
                "safety": "不适用",
            },
            "results": ["结果52%"],
            "limitations": ["仍需更多研究"],
            "data_points": [
                {"value": "52%", "meaning": "test", "source_quote": "result was 52%"},
            ],
        }
        raw = "result was 52%"
        problems = validate_depth(art, raw)
        self.assertTrue(any("局限" in p for p in problems))
    
    def test_marketing_words_in_title(self):
        art = {
            "tier": "brief",
            "title": "重磅发现：新药效果显著",
            "one_liner": "测试结果",
            "datacard": {
                "study_type": "临床试验",
                "n": "36例",
                "control": "随机对照",
                "intervention": "药物A",
                "followup": "12个月",
                "primary_endpoint": "ORR",
                "primary_endpoint_result": "52%",
                "statistics": "P<0.001",
                "safety": "不适用",
            },
            "results": ["结果52%"],
            "limitations": ["局限1"],
            "data_points": [
                {"value": "52%", "meaning": "test", "source_quote": "result was 52%"},
            ],
        }
        raw = "result was 52%"
        problems = validate_depth(art, raw)
        self.assertTrue(any("营销词汇" in p for p in problems))
    
    def test_press_only_deep(self):
        art = {
            "tier": "deep",
            "evidence_level": "press",
            "title": "测试",
            "one_liner": "测试",
            "datacard": {
                "study_type": "临床试验",
                "n": "36例",
                "control": "随机对照",
                "intervention": "药物A",
                "followup": "12个月",
                "primary_endpoint": "ORR",
                "primary_endpoint_result": "52%",
                "statistics": "P<0.001",
                "safety": "不适用",
            },
            "results": ["结果52%"],
            "limitations": ["局限1", "局限2", "局限3"],
            "data_points": [
                {"value": "52%", "meaning": "test", "source_quote": "result was 52%"},
            ],
        }
        raw = "result was 52%"
        problems = validate_depth(art, raw)
        self.assertTrue(any("新闻稿" in p for p in problems))
    
    def test_results_without_numbers(self):
        art = {
            "tier": "deep",
            "title": "测试",
            "one_liner": "测试",
            "datacard": {
                "study_type": "临床试验",
                "n": "36例",
                "control": "随机对照",
                "intervention": "药物A",
                "followup": "12个月",
                "primary_endpoint": "ORR",
                "primary_endpoint_result": "52%",
                "statistics": "P<0.001",
                "safety": "不适用",
            },
            "results": ["结果显著改善", "效果很好"],
            "limitations": ["局限1", "局限2", "局限3"],
            "data_points": [],
        }
        raw = "results showed improvement"
        problems = validate_depth(art, raw)
        self.assertTrue(any("第 1 段没有任何数字" in p for p in problems))


class TestEnrichItemMocked(unittest.TestCase):
    """Test enrich_item with mocked HTTP responses."""
    
    @patch('inlight_articles._http_get')
    def test_enrich_with_epmc_abstract(self, mock_http):
        mock_response = json.dumps({
            "resultList": {
                "result": [{
                    "abstractText": "This is a test abstract about CAR-T therapy.",
                    "pmid": "12345678",
                    "pmcid": None,
                    "isOpenAccess": "N",
                }]
            }
        }).encode()
        mock_http.return_value = mock_response
        
        row = {
            "url": "https://doi.org/10.1038/s41591-026-04704-z",
            "title": "Test Article",
            "source": "Nature Medicine",
            "date": "2026-10-01",
            "kind": "academic",
            "summary": "RSS summary here",
        }
        
        item = enrich_item(row)
        
        self.assertEqual(item.evidence_level, "abstract")
        self.assertIn("CAR-T therapy", item.abstract)
        self.assertEqual(item.pmid, "12345678")
    
    @patch('inlight_articles._http_get')
    def test_enrich_fallback_to_rss(self, mock_http):
        mock_http.return_value = None
        
        row = {
            "url": "https://example.com/no-doi-here",
            "title": "Test Article",
            "source": "Some Source",
            "date": "2026-10-01",
            "kind": "academic",
            "summary": "This is the RSS summary that should be used.",
        }
        
        item = enrich_item(row)
        
        self.assertEqual(item.evidence_level, "press")
        self.assertEqual(item.abstract, "This is the RSS summary that should be used.")


@pytest.mark.skipif(
    not os.environ.get("TEST_LIVE_EPMC"),
    reason="Set TEST_LIVE_EPMC=1 to run live EPMC tests"
)
class TestLiveEPMC(unittest.TestCase):
    """Live tests against Europe PMC API."""
    
    def test_real_epmc_search(self):
        result = epmc_core_search("10.1038/s41586-024-07386-0")
        
        if result is None:
            self.skipTest("EPMC API not responding")
        
        self.assertIsNotNone(result.get("abstractText"))
        self.assertTrue(len(result.get("abstractText", "")) > 100)


class TestIntegrationMocked(unittest.TestCase):
    """Integration test with mocked model responses."""
    
    @patch('inlight_articles._http_get')
    def test_full_pipeline_mocked(self, mock_http):
        mock_epmc = json.dumps({
            "resultList": {
                "result": [{
                    "abstractText": "In this study, we enrolled 36 patients with refractory disease. The objective response rate was 52% (19/36). Median follow-up was 12 months. Grade 3+ adverse events occurred in 28% of patients.",
                    "pmid": "99999999",
                    "pmcid": None,
                    "isOpenAccess": "N",
                }]
            }
        }).encode()
        mock_http.return_value = mock_epmc
        
        row = {
            "url": "https://doi.org/10.1038/s41591-026-test",
            "title": "Test Clinical Trial Results",
            "source": "Nature Medicine",
            "date": "2026-10-01",
            "kind": "academic",
            "summary": "Brief RSS summary",
        }
        
        item = enrich_item(row)
        
        self.assertEqual(item.evidence_level, "abstract")
        self.assertIn("52%", item.abstract)
        self.assertIn("36 patients", item.abstract)
        
        simulated_article = {
            "tier": "brief",
            "title": "CAR-T治疗难治性疾病客观缓解率52%",
            "one_liner": "一项纳入36例难治性疾病患者的单臂研究显示，CAR-T治疗的客观缓解率为52%，中位随访12个月，安全性可接受。",
            "datacard": {
                "study_type": "临床试验",
                "n": "36例",
                "control": "单臂无对照",
                "intervention": "CAR-T",
                "followup": "12个月",
                "primary_endpoint": "ORR",
                "primary_endpoint_result": "52%",
                "statistics": "原文未报告统计学检验",
                "safety": "≥3级AE 28%",
            },
            "background": "针对难治性疾病，传统化疗方案和靶向药物的疗效有限，中位生存期往往不足一年。CAR-T作为一种新型细胞疗法，通过基因改造T细胞使其靶向肿瘤抗原，已在血液肿瘤中展现出显著疗效。本研究旨在评估CAR-T在此类难治性疾病中的初步疗效和安全性。",
            "design": "这是一项单中心单臂研究，连续纳入36例经标准治疗后复发或难治的患者。所有患者接受淋巴细胞清除预处理后回输CAR-T细胞，主要终点为客观缓解率，次要终点包括缓解持续时间和安全性。中位随访时间为12个月。",
            "results": ["研究显示客观缓解率达到52%（19/36例）。中位随访12个月时大部分缓解患者仍然持续缓解。安全性方面，三级及以上不良事件发生率为28%，主要为细胞因子释放综合征和神经毒性，无治疗相关死亡报告。"],
            "mechanism": "",
            "limitations": ["单臂设计缺乏对照组，无法评估相对疗效；单中心研究外推性有限；随访时间尚短不能评估长期疗效"],
            "significance": "这项研究为难治性疾病患者提供了一个有前景的治疗选择。52%的缓解率和可控的安全性令人鼓舞。若后续随机对照试验验证疗效，有望改变临床实践。",
            "data_points": [
                {"value": "52%", "meaning": "缓解率", "source_quote": "The objective response rate was 52%"},
                {"value": "36", "meaning": "患者数", "source_quote": "we enrolled 36 patients"},
                {"value": "19/36", "meaning": "缓解人数", "source_quote": "The objective response rate was 52% (19/36)"},
                {"value": "12个月", "meaning": "随访时长", "source_quote": "Median follow-up was 12 months"},
                {"value": "28%", "meaning": "AE比例", "source_quote": "Grade 3+ adverse events occurred in 28%"},
            ],
        }
        
        raw_material = "In this study, we enrolled 36 patients with refractory disease. The objective response rate was 52% (19/36). Median follow-up was 12 months. Grade 3+ adverse events occurred in 28% of patients."
        problems = validate_depth(simulated_article, raw_material)
        
        self.assertEqual(len(problems), 0, f"Unexpected validation problems: {problems}")


if __name__ == "__main__":
    unittest.main()
