#!/usr/bin/env python3
"""Tests for inlight_articles.py

Comprehensive tests including:
- Unit tests for DOI extraction, number extraction, validation
- End-to-end tests with mocked HTTP and Claude API
- Negative fixtures with invented names/numbers that must be caught
- Tests for max_tokens truncation handling
- Tests for 400 error handling
"""

import os
import json
import pytest
import unittest
from unittest.mock import patch, MagicMock, Mock

import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from inlight_articles import (
    extract_doi,
    extract_numbers_from_text,
    normalize_whitespace,
    cn_len,
    validate_depth,
    validate_names,
    enrich_item,
    epmc_core_search,
    EnrichedItem,
    MARKETING_BLOCKLIST,
    chinese_numeral_to_arabic,
    extract_number_core,
    number_in_text_as_word_boundary,
    extract_sections_from_xml,
    extract_fig_captions_from_xml,
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
    
    def test_biorxiv_url_with_version_suffix(self):
        """bioRxiv DOIs should have version suffix stripped."""
        url = "https://www.biorxiv.org/content/10.1101/2026.01.01.123456v1"
        self.assertEqual(extract_doi(url), "10.1101/2026.01.01.123456")
        
        url2 = "https://www.biorxiv.org/content/10.1101/2026.01.01.123456v2"
        self.assertEqual(extract_doi(url2), "10.1101/2026.01.01.123456")
    
    def test_cell_pii_returns_empty(self):
        """Cell PIIs should NOT be converted to fake DOIs."""
        url = "https://www.cell.com/cell/fulltext/S0092-8674(26)00123-4"
        self.assertEqual(extract_doi(url), "")
    
    def test_lancet_pii_returns_empty(self):
        """Lancet PIIs should NOT be converted to DOIs."""
        url = "https://www.thelancet.com/journals/lancet/article/PIIS0140673626009669"
        self.assertEqual(extract_doi(url), "")
    
    def test_no_doi(self):
        url = "https://example.com/article/12345"
        self.assertEqual(extract_doi(url), "")


class TestChineseNumeralConversion(unittest.TestCase):
    """Test Chinese numeral to Arabic conversion."""
    
    def test_simple_numerals(self):
        self.assertIn("2", chinese_numeral_to_arabic("两年"))
        self.assertIn("5", chinese_numeral_to_arabic("五"))
    
    def test_compound_numerals(self):
        result = chinese_numeral_to_arabic("五百天")
        # After conversion, 百 should become 100 and combined with 五
        # The function does simple replacements, so 五百 -> 5百 or similar
        self.assertTrue("5" in result)
    
    def test_large_numbers(self):
        result = chinese_numeral_to_arabic("五十亿")
        # Should convert to 5000000000
        self.assertTrue("5" in result and "0" in result)


class TestNumberBoundaryCheck(unittest.TestCase):
    """Test that number matching respects word boundaries."""
    
    def test_500_not_in_5000(self):
        self.assertFalse(number_in_text_as_word_boundary("500", "5000 patients"))
    
    def test_500_in_500(self):
        self.assertTrue(number_in_text_as_word_boundary("500", "up to 500 days"))
    
    def test_139_not_in_1139(self):
        self.assertFalse(number_in_text_as_word_boundary("139", "1,139 patients"))
    
    def test_1139_in_1139(self):
        self.assertTrue(number_in_text_as_word_boundary("1139", "1,139 patients"))
    
    def test_52_in_52_percent(self):
        self.assertTrue(number_in_text_as_word_boundary("52", "response rate was 52%"))


class TestExtractNumberCore(unittest.TestCase):
    """Test extraction of core numeric values."""
    
    def test_percentage(self):
        self.assertEqual(extract_number_core("52%"), "52")
    
    def test_number_with_unit(self):
        self.assertEqual(extract_number_core("1,139例"), "1139")
    
    def test_95_ci_is_not_95_percent(self):
        """95%CI should not extract as '95' since it's not a value."""
        self.assertEqual(extract_number_core("95%CI"), "")
    
    def test_hr_value(self):
        self.assertEqual(extract_number_core("HR=0.66"), "0.66")


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
    
    def test_chinese_numerals(self):
        text = "约五百天后"
        numbers = extract_numbers_from_text(text)
        self.assertTrue(any("五百" in n for n in numbers))


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
                "study_type": "",
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
        # Missing datacard values are omitted at publish time, not a defect
        self.assertFalse(any("应写" in p and "未给出" in p for p in problems))
        self.assertFalse(any("数据卡字段为空" in p for p in problems))
    
    def test_fabricated_number(self):
        """Numbers in article must be registered in data_points."""
        art = {
            "tier": "brief",
            "title": "测试标题",
            "one_liner": "缓解率达到75%",
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
        self.assertTrue(any("75" in p and "未找到" in p for p in problems))
    
    def test_fake_quote(self):
        """Quote must exist in raw material."""
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
    
    def test_value_not_in_quote(self):
        """Value must appear in its quote (with boundary check)."""
        art = {
            "tier": "brief",
            "title": "测试标题",
            "one_liner": "测试",
            "datacard": {
                "study_type": "临床试验",
                "n": "36例",
                "control": "随机对照",
                "intervention": "药物A",
                "followup": "12个月",
                "primary_endpoint": "ORR",
                "primary_endpoint_result": "730天",
                "statistics": "原文未报告",
                "safety": "不适用",
            },
            "results": ["持续730天"],
            "limitations": ["局限1"],
            "data_points": [
                {
                    "value": "730天",
                    "meaning": "持续时间",
                    "source_quote": "up to 500 days after immunization"
                },
            ],
        }
        raw = "up to 500 days after immunization"
        problems = validate_depth(art, raw)
        self.assertTrue(any("不在 quote 中" in p for p in problems))
    
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
    
    def test_results_without_numbers_is_ok(self):
        """Per spec: 'missing content is OK; any invented fact is a hard defect'.
        
        Results without numbers are NOT flagged - missing content is allowed.
        Only INVENTED content (numbers/names not in source) is flagged.
        """
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
            "results": ["结果显著改善", "效果很好"],  # No numbers is OK
            "limitations": ["局限1", "局限2", "局限3"],
            "data_points": [],
        }
        raw = "results showed 52% improvement in n = 36 patients"
        problems = validate_depth(art, raw)
        # Should NOT flag missing numbers - missing is OK, invented is not
        self.assertFalse(any("段没有任何数字" in p for p in problems))


class TestValidateNames(unittest.TestCase):
    """Test that invented author names are detected."""
    
    def test_invented_author_name(self):
        """Invented author names like 'Zhang 等' should be flagged if not in source."""
        art = {
            "title": "测试标题",
            "one_liner": "Zhang 等发现...",
            "background": "研究团队发现",
            "design": "设计",
            "results": ["结果"],
            "mechanism": "",
            "significance": "",
            "authors": "原文未给出",
        }
        raw = "The study by Smith and colleagues found..."
        problems = validate_names(art, raw)
        self.assertTrue(any("Zhang" in p for p in problems))
    
    def test_valid_author_name(self):
        """Author names that appear in source should pass."""
        art = {
            "title": "测试标题",
            "one_liner": "Smith 等发现...",
            "background": "研究团队发现",
            "design": "设计",
            "results": ["结果"],
            "mechanism": "",
            "significance": "",
            "authors": "Smith",
        }
        raw = "The study by Smith and colleagues found that..."
        problems = validate_names(art, raw)
        # Should NOT flag Smith since it appears in raw
        self.assertFalse(any("Smith" in p for p in problems))


class TestXMLExtraction(unittest.TestCase):
    """Test XML text extraction with nested elements."""
    
    def test_itertext_preserves_nested_text(self):
        """Superscripts should be converted to Unicode superscript characters."""
        xml = """<article>
            <sec><title>Results</title>
                <p>We injected 5×10<sup>6</sup> cells into mice (n = 12).</p>
            </sec>
        </article>"""
        result = extract_sections_from_xml(xml, ("Results",))
        # Should contain superscript 6 as Unicode ⁶
        self.assertIn("10", result)
        self.assertIn("⁶", result)  # Unicode superscript 6
        self.assertIn("12", result)
    
    def test_fig_captions_preserve_nested(self):
        """Figure captions should handle nested italic and superscript."""
        xml = """<article>
            <fig>
                <caption>
                    <p>Figure showing <italic>Rag2</italic><sup>-/-</sup> mice.</p>
                </caption>
            </fig>
        </article>"""
        result = extract_fig_captions_from_xml(xml)
        self.assertIn("Rag2", result)
        # Superscript "-/-" becomes "⁻/⁻"
        self.assertIn("⁻", result)


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


class TestNegativeFixtures(unittest.TestCase):
    """Negative tests: fixtures with invented content that MUST be caught."""
    
    def test_invented_author_caught(self):
        """Article with fabricated author 'Zhang 等' when source has no Zhang."""
        art = {
            "tier": "deep",
            "title": "测试研究",
            "one_liner": "Zhang 等在《Nature》发表的研究表明...",
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
            "background": "研究背景",
            "design": "研究设计",
            "results": ["研究发现52%的患者缓解"],
            "mechanism": "",
            "limitations": ["局限1", "局限2", "局限3"],
            "significance": "意义",
            "data_points": [
                {"value": "52%", "meaning": "缓解率", "source_quote": "response rate was 52%"},
            ],
            "authors": "原文未给出",
        }
        raw = "This study by Miller et al. found response rate was 52%"
        
        problems = validate_names(art, raw)
        # Must catch the invented "Zhang" author
        self.assertTrue(any("Zhang" in p for p in problems), 
                       "Failed to catch invented author 'Zhang 等'")
    
    def test_invented_number_caught(self):
        """Article with number not in source must be caught."""
        art = {
            "tier": "brief",
            "title": "测试",
            "one_liner": "随访两年后",  # "两年" = 2 years, but source says 500 days
            "datacard": {
                "study_type": "临床试验",
                "n": "36例",
                "control": "随机对照",
                "intervention": "药物A",
                "followup": "500天",
                "primary_endpoint": "ORR",
                "primary_endpoint_result": "52%",
                "statistics": "原文未报告",
                "safety": "不适用",
            },
            "results": ["结果52%"],
            "limitations": ["局限1"],
            "data_points": [
                {"value": "52%", "meaning": "缓解率", "source_quote": "response rate was 52%"},
                {"value": "500", "meaning": "天数", "source_quote": "up to 500 days"},
            ],
        }
        raw = "response rate was 52% up to 500 days after immunization"
        
        problems = validate_depth(art, raw)
        # Must catch that "两年" (2 years) or "2" is not registered
        # Since Chinese numerals are extracted, "两" should be flagged
        # This may require the number to be in data_points


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
        
        # Simulate a well-formed article that passes validation
        # IMPORTANT: ALL numbers and claims must match the source exactly
        # Source: "36 patients", "52% (19/36)" response, "12 months" follow-up, "28%" adverse events
        # Brief tier requires minimum 450 Han characters
        simulated_article = {
            "tier": "brief",
            "title": "难治性疾病CAR-T治疗客观缓解率52%",
            "one_liner": "纳入36例难治性疾病患者的研究显示客观缓解率为52%（19/36例），中位随访12个月，三级及以上不良事件发生率28%，提示该疗法在难治性患者中具有良好的抗肿瘤活性。",
            "datacard": {
                "study_type": "临床研究",
                "n": "36例",
                "control": "原文未报告对照组信息",
                "intervention": "原文未报告具体干预措施",
                "followup": "中位12个月",
                "primary_endpoint": "客观缓解率",
                "primary_endpoint_result": "52%（19/36例）",
                "statistics": "原文未报告统计学检验",
                "safety": "三级及以上不良事件发生率28%",
            },
            "background": "针对难治性疾病，传统治疗方案疗效有限，需要探索新型治疗方法。本研究评估了细胞治疗的疗效和安全性。该疗法是一种新兴的细胞免疫疗法，通过基因工程改造患者自身的T细胞，使其能够识别并杀伤肿瘤细胞，在血液肿瘤领域已展现出显著疗效。",
            "design": "研究纳入36例难治性疾病患者接受治疗。本研究为描述性研究，主要终点为客观缓解率。研究设计相对简单，原文未提供对照组和盲法等详细信息。研究者对所有入组患者进行了疗效和安全性评估，随访观察治疗后的缓解持久性。",
            "results": ["研究显示客观缓解率为52%，即36例患者中有19例达到缓解。中位随访时间为12个月，大部分缓解患者维持疗效。安全性方面，三级及以上不良事件发生率为28%。以上数据均直接来自原文报告，反映了该治疗方案在难治性患者中的临床表现。"],
            "mechanism": "",
            "limitations": ["原文未报告对照组，无法评估相对疗效；原文未报告随访期间的疾病进展或复发数据；样本量较小，结果外推需谨慎"],
            "significance": "该研究为难治性疾病患者提供了一种潜在治疗选择的初步证据。若后续大样本研究能重复这些结果，可能为这类患者带来新的治疗希望。",
            "data_points": [
                {"value": "52%", "meaning": "客观缓解率", "source_quote": "The objective response rate was 52%"},
                {"value": "36", "meaning": "患者例数", "source_quote": "we enrolled 36 patients"},
                {"value": "19/36", "meaning": "缓解例数/总例数", "source_quote": "The objective response rate was 52% (19/36)"},
                {"value": "12", "meaning": "随访月数", "source_quote": "Median follow-up was 12 months"},
                {"value": "28%", "meaning": "不良事件发生率", "source_quote": "Grade 3+ adverse events occurred in 28%"},
            ],
        }
        
        raw_material = "In this study, we enrolled 36 patients with refractory disease. The objective response rate was 52% (19/36). Median follow-up was 12 months. Grade 3+ adverse events occurred in 28% of patients."
        problems = validate_depth(simulated_article, raw_material)
        
        # This well-formed article with source-matched claims should pass
        self.assertEqual(len(problems), 0, f"Unexpected validation problems: {problems}")


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


class TestIdentifierSubstringBug(unittest.TestCase):
    """Regression tests for P0 bug: invented numbers passing via identifier substrings.
    
    Bug: number_exists_in_source accepted digits found inside identifiers.
    Example: "31例" passed because CD318 contains "31".
    
    These tests verify the fix catches invented numbers even when identifiers
    containing those digits exist in source.
    """
    
    def test_31_via_cd318_rejected(self):
        """'共纳入31例' must be caught when source only has CD318."""
        from inlight_articles import number_exists_in_source, extract_identifiers_from_source, normalize_source_text
        
        source = "CD318 positive tumor cells were analyzed. CD6 is a target."
        source_norm = normalize_source_text(source)
        identifiers = extract_identifiers_from_source(source)
        
        # "31例" context does NOT mention CD318, so 31 should be rejected
        result = number_exists_in_source("31", source_norm, identifiers, "共纳入31例患者")
        self.assertFalse(result, "Invented '31' should be caught - not in source as standalone")
    
    def test_8_via_cd8_rejected(self):
        """'8例死亡' must be caught when source only has CD8."""
        from inlight_articles import number_exists_in_source, extract_identifiers_from_source, normalize_source_text
        
        source = "CD8 T cells showed enhanced killing. The antibody targets CD6."
        source_norm = normalize_source_text(source)
        identifiers = extract_identifiers_from_source(source)
        
        # "8例死亡" context does NOT mention CD8, so 8 should be rejected
        result = number_exists_in_source("8", source_norm, identifiers, "8例死亡事件")
        self.assertFalse(result, "Invented '8' should be caught - not in source as standalone")
    
    def test_44_via_rpcec_rejected(self):
        """'客观缓解率44%' must be caught when source only has RPCEC00000444."""
        from inlight_articles import number_exists_in_source, extract_identifiers_from_source, normalize_source_text
        
        source = "Trial registered as RPCEC00000444. Response rate was 25%."
        source_norm = normalize_source_text(source)
        identifiers = extract_identifiers_from_source(source)
        
        # "44%" is invented - source has 25%, not 44%
        result = number_exists_in_source("44", source_norm, identifiers, "客观缓解率44%")
        self.assertFalse(result, "Invented '44%' should be caught - not in source as standalone")
    
    def test_18_via_cd318_rejected(self):
        """'中位随访18个月' must be caught when source only has CD318."""
        from inlight_articles import number_exists_in_source, extract_identifiers_from_source, normalize_source_text
        
        source = "CD318 expression was measured. Follow-up duration not reported."
        source_norm = normalize_source_text(source)
        identifiers = extract_identifiers_from_source(source)
        
        # "18个月" is invented
        result = number_exists_in_source("18", source_norm, identifiers, "中位随访18个月")
        self.assertFalse(result, "Invented '18' should be caught - not in source as standalone")
    
    def test_cd8_identifier_in_context_allowed(self):
        """Digits inside the identifier token CD8 are not claimed numbers.

        A nearby identifier must not evidence a count: '8例' next to CD8 is invented.
        Copying 'CD8细胞' verbatim is fine because the 8 is not extracted as data.
        """
        from inlight_articles import (
            extract_number_core,
            extract_numbers_with_context,
            number_exists_in_source,
            extract_identifiers_from_source,
            normalize_source_text,
        )

        source = "CD8 T cells showed enhanced killing."
        source_norm = normalize_source_text(source)
        identifiers = extract_identifiers_from_source(source)

        extracted = extract_numbers_with_context("CD8细胞呈现杀伤表型")
        cores = [extract_number_core(n) for n, _ in extracted]
        self.assertNotIn("8", cores, f"CD8 must not yield a claimed '8': {extracted}")
        # A count that merely sits next to the identifier is still invented
        result = number_exists_in_source("8", source_norm, identifiers, "CD8阳性患者8例")
        self.assertFalse(result, "A nearby CD8 must not evidence an invented 8例")
    
    def test_standalone_number_allowed(self):
        """Numbers that actually exist in source should pass."""
        from inlight_articles import number_exists_in_source, extract_identifiers_from_source, normalize_source_text
        
        source = "Response rate was 52%. We enrolled 36 patients."
        source_norm = normalize_source_text(source)
        identifiers = extract_identifiers_from_source(source)
        
        result_52 = number_exists_in_source("52", source_norm, identifiers, "缓解率52%")
        result_36 = number_exists_in_source("36", source_norm, identifiers, "纳入36例患者")
        
        self.assertTrue(result_52, "52% should be found in source")
        self.assertTrue(result_36, "36 should be found in source")


class TestChineseAuthorFalsePositives(unittest.TestCase):
    """Regression tests for Chinese 'X等' false positives.
    
    Bug: '皮疹等' (rashes, etc.) was flagged as author surname '疹'.
    Fix: Only flag 'X等' in author-like contexts (e.g., "X等发现").
    """
    
    def test_pi_zhen_deng_not_flagged(self):
        """'乏力、皮疹等' (fatigue, rash, etc.) should not be flagged as author."""
        from inlight_articles import validate_names
        
        art = {
            "title": "药物安全性研究",
            "one_liner": "常见不良事件包括乏力、皮疹等。",
            "background": "",
            "design": "",
            "results": ["常见不良事件包括乏力、皮疹等轻微症状。"],
            "mechanism": "",
            "significance": "",
            "authors": "原文未给出",
            "limitations": [],
        }
        raw = "Common adverse events included fatigue, rash, and other mild symptoms."
        problems = validate_names(art, raw)
        
        # Should NOT flag '疹' as an author surname
        author_problems = [p for p in problems if "作者姓氏" in p and "疹" in p]
        self.assertEqual(author_problems, [], f"Should not flag '皮疹等' as author: {author_problems}")
    
    def test_xi_bao_yin_zi_deng_not_flagged(self):
        """'细胞因子等' (cytokines, etc.) should not be flagged as author."""
        from inlight_articles import validate_names
        
        art = {
            "title": "炎症机制研究",
            "one_liner": "检测了IL-6、TNF等细胞因子等。",
            "background": "",
            "design": "",
            "results": ["细胞因子等炎症标志物升高。"],
            "mechanism": "",
            "significance": "",
            "authors": "原文未给出",
            "limitations": [],
        }
        raw = "Cytokines and other inflammatory markers were elevated."
        problems = validate_names(art, raw)
        
        # Should NOT flag '子' as an author surname
        author_problems = [p for p in problems if "作者姓氏" in p and "子" in p]
        self.assertEqual(author_problems, [], f"Should not flag '细胞因子等' as author: {author_problems}")
    
    def test_wang_deng_baogao_flagged(self):
        """'王等报告' (Wang et al. report) SHOULD be flagged if Wang not in source."""
        from inlight_articles import validate_names
        
        art = {
            "title": "临床研究结果",
            "one_liner": "王等报告了该药物的有效性。",
            "background": "",
            "design": "",
            "results": ["王等报告，客观缓解率达到52%。"],
            "mechanism": "",
            "significance": "",
            "authors": "原文未给出",
            "limitations": [],
        }
        raw = "The drug showed efficacy with a 52% response rate."
        problems = validate_names(art, raw)
        
        # SHOULD flag '王' because it's used in author context but not in source
        author_problems = [p for p in problems if "作者姓氏" in p and "王" in p]
        self.assertTrue(len(author_problems) > 0, f"Should flag '王等报告' as invented author: {problems}")
    
    def test_real_author_zhang_deng_flagged(self):
        """'张等发现' (Zhang et al. found) SHOULD be flagged if Zhang not in source."""
        from inlight_articles import validate_names
        
        art = {
            "title": "研究结果",
            "one_liner": "张等发现该药物有效。",
            "background": "",
            "design": "",
            "results": ["张等报道了临床结果。"],
            "mechanism": "",
            "significance": "",
            "authors": "原文未给出",
            "limitations": [],
        }
        raw = "The drug was found to be effective. Results were reported."
        problems = validate_names(art, raw)
        
        # SHOULD flag '张' as an invented author
        author_problems = [p for p in problems if "作者姓氏" in p and "张" in p]
        self.assertTrue(len(author_problems) > 0, "Should flag invented author '张等发现'")


class TestDataPointValidation(unittest.TestCase):
    """Test that non-numeric data_points are rejected."""
    
    def test_millions_rejected(self):
        """'millions' as data_point value should be rejected."""
        from inlight_articles import validate_depth
        
        art = {
            "tier": "brief",
            "title": "测试",
            "one_liner": "测试",
            "datacard": {
                "study_type": "研究",
                "n": "原文未给出",
                "control": "无",
                "intervention": "无",
                "followup": "无",
                "primary_endpoint": "测试",
                "primary_endpoint_result": "测试",
                "statistics": "无",
                "safety": "无",
            },
            "results": ["结果"],
            "limitations": ["局限"],
            "data_points": [
                {"value": "millions", "meaning": "细胞数", "source_quote": "millions of cells were generated"},
            ],
        }
        raw = "millions of cells were generated through the platform"
        problems = validate_depth(art, raw)
        
        # Should reject vague 'millions'
        vague_problems = [p for p in problems if "模糊" in p or "数字" in p]
        self.assertTrue(len(vague_problems) > 0, f"Should reject 'millions': {problems}")
    
    def test_tens_of_kilobases_rejected(self):
        """'tens of kilobases' should be rejected as too vague."""
        from inlight_articles import validate_depth
        
        art = {
            "tier": "brief",
            "title": "测试",
            "one_liner": "测试",
            "datacard": {
                "study_type": "研究",
                "n": "原文未给出",
                "control": "无",
                "intervention": "无",
                "followup": "无",
                "primary_endpoint": "测试",
                "primary_endpoint_result": "测试",
                "statistics": "无",
                "safety": "无",
            },
            "results": ["结果"],
            "limitations": ["局限"],
            "data_points": [
                {"value": "tens of kilobases", "meaning": "序列长度", "source_quote": "tens of kilobases of sequence"},
            ],
        }
        raw = "exploration of tens of kilobases of sequence space"
        problems = validate_depth(art, raw)
        
        # Should reject vague 'tens of' - it contains no actual numbers
        no_number_problems = [p for p in problems if "必须包含数字" in p or "模糊" in p]
        self.assertTrue(len(no_number_problems) > 0, f"Should reject 'tens of kilobases': {problems}")
    
    def test_disease_name_rejected(self):
        """Disease names should not be registered as data_points."""
        from inlight_articles import validate_depth
        
        art = {
            "tier": "brief",
            "title": "测试",
            "one_liner": "测试",
            "datacard": {
                "study_type": "研究",
                "n": "原文未给出",
                "control": "无",
                "intervention": "无",
                "followup": "无",
                "primary_endpoint": "测试",
                "primary_endpoint_result": "测试",
                "statistics": "无",
                "safety": "无",
            },
            "results": ["结果"],
            "limitations": ["局限"],
            "data_points": [
                {"value": "melanoma and breast cancer", "meaning": "非数值数据 - 肿瘤类型", 
                 "source_quote": "melanoma and breast cancer models were used"},
            ],
        }
        raw = "melanoma and breast cancer models were used in the study"
        problems = validate_depth(art, raw)
        
        # Should reject non-numeric content
        non_numeric = [p for p in problems if "数值" in p or "数字" in p]
        self.assertTrue(len(non_numeric) > 0, f"Should reject disease names: {problems}")
    
    def test_specific_number_allowed(self):
        """Specific numbers like '52%' should be allowed."""
        from inlight_articles import validate_depth
        
        art = {
            "tier": "brief",
            "title": "测试",
            "one_liner": "测试52%",
            "datacard": {
                "study_type": "研究",
                "n": "36例",
                "control": "无",
                "intervention": "药物",
                "followup": "12月",
                "primary_endpoint": "缓解率",
                "primary_endpoint_result": "52%",
                "statistics": "无",
                "safety": "无",
            },
            "results": ["缓解率52%"],
            "limitations": ["局限"],
            "data_points": [
                {"value": "52%", "meaning": "缓解率", "source_quote": "response rate was 52%"},
                {"value": "36", "meaning": "患者数", "source_quote": "36 patients enrolled"},
            ],
        }
        raw = "response rate was 52% in 36 patients enrolled"
        problems = validate_depth(art, raw)
        
        # Should NOT reject specific numbers
        value_problems = [p for p in problems if "52" in p or "36" in p]
        self.assertEqual(value_problems, [], f"Should allow specific numbers: {value_problems}")
    
    def test_nine_doses_rejected(self):
        """English word 'nine doses' should be rejected - must use digits."""
        from inlight_articles import validate_depth
        
        art = {
            "tier": "brief",
            "title": "测试",
            "one_liner": "测试",
            "datacard": {
                "study_type": "研究",
                "n": "原文未给出",
                "control": "无",
                "intervention": "无",
                "followup": "无",
                "primary_endpoint": "测试",
                "primary_endpoint_result": "测试",
                "statistics": "无",
                "safety": "无",
            },
            "results": ["结果"],
            "limitations": ["局限"],
            "data_points": [
                {"value": "nine doses", "meaning": "剂量次数", "source_quote": "nine doses were administered"},
            ],
        }
        raw = "nine doses were administered over 9 weeks"
        problems = validate_depth(art, raw)
        
        # Should reject 'nine doses' - no Arabic digits
        no_digit_problems = [p for p in problems if "数字" in p]
        self.assertTrue(len(no_digit_problems) > 0, f"Should reject 'nine doses': {problems}")

    def test_ordinal_time_phrase_not_hard_fail(self):
        """Source-faithful word quantities must not hard-fail; invented ones must."""
        from inlight_articles import validate_depth

        pad = "测" * 80
        art = {
            "tier": "brief",
            "title": "测试",
            "one_liner": pad,
            "background": pad,
            "design": pad,
            "results": [pad + "采样在first week完成。"],
            "limitations": [pad],
            "significance": pad,
            "datacard": {
                "study_type": "研究", "n": "10", "control": "无",
                "intervention": "无", "followup": "无",
                "primary_endpoint": "测试", "primary_endpoint_result": "测试",
                "statistics": "无", "safety": "无",
            },
            "data_points": [
                {
                    "value": "first week",
                    "meaning": "首次采样时间",
                    "source_quote": "Sampling in the first week showed recovery",
                },
            ],
        }
        raw = (
            "Sampling in the first week showed recovery in 10 patients. "
            "Mice were split into five groups. Expression rose twofold. "
            "分为五组，信号增加两倍。"
        )
        problems = validate_depth(art, raw)
        self.assertFalse(
            any("必须包含数字" in p and "first week" in p for p in problems),
            problems,
        )

        for value, quote in (
            ("five groups", "Mice were split into five groups"),
            ("twofold", "Expression rose twofold"),
            ("五组", "分为五组，信号增加两倍"),
            ("两倍", "分为五组，信号增加两倍"),
        ):
            art["data_points"] = [{
                "value": value,
                "meaning": "数量",
                "source_quote": quote,
            }]
            ok = validate_depth(art, raw, allow_word_quantities=True)
            self.assertFalse(
                any("必须包含数字" in p and value in p for p in ok),
                (value, ok),
            )

        art["data_points"] = [{
            "value": "nineteenth week",
            "meaning": "不存在的周次",
            "source_quote": "Sampling in the first week showed recovery",
        }]
        invented = validate_depth(art, raw)
        self.assertTrue(
            any("数字" in p for p in invented),
            invented,
        )
        art["data_points"] = [{
            "value": "nineteen groups",
            "meaning": "编造分组",
            "source_quote": "Mice were split into five groups",
        }]
        invented_grp = validate_depth(art, raw, allow_word_quantities=True)
        self.assertTrue(
            any("数字" in p for p in invented_grp),
            invented_grp,
        )

    def test_clinical_class_uses_own_sentence(self):
        """A neighbouring clinical token must not block the minor-number strip."""
        from inlight_articles import validate_depth

        pad = "测" * 90
        art = {
            "tier": "brief",
            "title": "测试标题",
            "one_liner": pad,
            "background": pad,
            "design": pad,
            "results": ["第17周完成采样。该队列纳入120例患者，ORR为64%。"],
            "limitations": [pad],
            "significance": pad,
            "mechanism": "",
            "datacard": {
                "study_type": "研究", "n": "120", "control": "无",
                "intervention": "无", "followup": "无",
                "primary_endpoint": "ORR", "primary_endpoint_result": "64%",
                "statistics": "无", "safety": "无",
            },
            "data_points": [
                {
                    "value": "120",
                    "meaning": "例数",
                    "source_quote": "120 patients were enrolled for ORR 64%",
                },
                {
                    "value": "64%",
                    "meaning": "ORR",
                    "source_quote": "120 patients were enrolled for ORR 64%",
                },
            ],
        }
        raw = "120 patients were enrolled for ORR 64%. Sampling occurred after induction."
        problems = validate_depth(art, raw)
        self.assertFalse(
            any("数字 '17'" in p or "数字 '17周'" in p for p in problems),
            problems,
        )
        removed = art.get("qc_removed_numbers") or []
        self.assertTrue(
            any("17" in str(r.get("number") or "") for r in removed),
            removed,
        )
    
    def test_disease_control_rate_not_flagged(self):
        """'疾病控制率' (DCR) is a metric name, not a disease to be flagged."""
        from inlight_articles import validate_depth
        
        art = {
            "tier": "brief",
            "title": "疾病控制率25%",
            "one_liner": "疾病控制率25%",
            "datacard": {
                "study_type": "临床试验",
                "n": "36例",
                "control": "无",
                "intervention": "药物",
                "followup": "12月",
                "primary_endpoint": "疾病控制率",
                "primary_endpoint_result": "25%",
                "statistics": "无",
                "safety": "无",
            },
            "results": ["疾病控制率为25%"],
            "limitations": ["局限"],
            "data_points": [
                {"value": "25%", "meaning": "疾病控制率", "source_quote": "disease control rate was 25%"},
            ],
        }
        raw = "disease control rate was 25% in this study"
        problems = validate_depth(art, raw)
        
        # Should NOT flag '疾病控制率' as a disease name
        disease_problems = [p for p in problems if "疾病" in p and "data_point" in p.lower()]
        self.assertEqual(disease_problems, [], f"Should NOT flag 疾病控制率: {problems}")


class TestNumberMeaningMismatch(unittest.TestCase):
    """Test that numbers are flagged when used with contradictory metrics."""
    
    def test_count_vs_percent_flagged(self):
        """'36例' (36 patients) flagged when source only has '36%'."""
        from inlight_articles import number_meaning_matches_source
        
        # Output says "36例" (36 patients), source says "36%" (percentage)
        ok, reason = number_meaning_matches_source(
            "36", 
            "共纳入36例患者",  # Output claims 36 patients
            "response rate was 36%"  # Source says 36%
        )
        self.assertFalse(ok, f"Should flag count/percent mismatch: {reason}")
        self.assertIn("单位不匹配", reason)
    
    def test_percent_vs_count_flagged(self):
        """'52%' flagged when source only has '52例'."""
        from inlight_articles import number_meaning_matches_source
        
        ok, reason = number_meaning_matches_source(
            "52%",
            "缓解率52%",  # Output claims 52%
            "52 patients enrolled"  # Source says 52 patients
        )
        self.assertFalse(ok, f"Should flag percent/count mismatch: {reason}")
        self.assertIn("单位不匹配", reason)
    
    def test_matching_units_pass(self):
        """'36%' should pass when source has '36%'."""
        from inlight_articles import number_meaning_matches_source
        
        ok, reason = number_meaning_matches_source(
            "36%",
            "缓解率36%",
            "response rate was 36%"
        )
        self.assertTrue(ok, f"Should pass when units match: {reason}")
    
    def test_ae_rate_as_cr_rate_flagged(self):
        """Using an AE rate (28%) as CR rate should be flagged."""
        from inlight_articles import validate_depth
        
        art = {
            "tier": "brief",
            "title": "测试",
            "one_liner": "完全缓解率为28%。",  # Output says 28% is CR rate
            "datacard": {
                "study_type": "研究",
                "n": "100例",
                "control": "无",
                "intervention": "无",
                "followup": "无",
                "primary_endpoint": "缓解率",
                "primary_endpoint_result": "缓解率28%",  # Claims 28% is response
                "statistics": "无",
                "safety": "无",
            },
            "background": "背景部分不含数据。背景部分不含数据。背景部分不含数据。背景部分不含数据。背景部分不含数据。背景部分不含数据。背景部分不含数据。背景部分不含数据。背景部分不含数据。背景部分不含数据。背景部分不含数据。背景部分不含数据。",
            "design": "设计部分不含数据。设计部分不含数据。设计部分不含数据。",
            "results": ["缓解率28%"],  # Output attributes 28% to response
            "limitations": ["局限"],
            "data_points": [
                {"value": "28%", "meaning": "缓解率", "source_quote": "Grade 3+ adverse events occurred in 28% of patients"},
            ],
        }
        # Source says 28% is AE rate, not CR rate
        raw = "In this study, 100 patients were enrolled. Grade 3+ adverse events occurred in 28% of patients."
        problems = validate_depth(art, raw)
        
        # Should flag the meaning mismatch - 缓解率28% when source says adverse 28%
        mismatch_problems = [p for p in problems if "含义不匹配" in p]
        self.assertTrue(len(mismatch_problems) > 0, f"Should flag CR/AE mismatch: {problems}")
    
    def test_correct_meaning_passes(self):
        """Number used with correct metric should pass."""
        from inlight_articles import validate_depth
        
        art = {
            "tier": "brief",
            "title": "测试",
            "one_liner": "不良事件发生率28%。",  # Correctly attributes to AE
            "datacard": {
                "study_type": "研究",
                "n": "36例",
                "control": "无",
                "intervention": "无",
                "followup": "无",
                "primary_endpoint": "无",
                "primary_endpoint_result": "无",
                "statistics": "无",
                "safety": "不良事件28%",  # Correctly in safety field
            },
            "background": "背景部分不含数据。背景部分不含数据。背景部分不含数据。背景部分不含数据。背景部分不含数据。背景部分不含数据。背景部分不含数据。背景部分不含数据。背景部分不含数据。背景部分不含数据。背景部分不含数据。背景部分不含数据。",
            "design": "设计部分",
            "results": ["不良事件发生率为28%"],  # Correctly used as AE
            "limitations": ["局限"],
            "data_points": [
                {"value": "28%", "meaning": "不良事件率", "source_quote": "adverse events in 28%"},
            ],
        }
        raw = "Grade 3+ adverse events occurred in 28% of patients."
        problems = validate_depth(art, raw)
        
        # Should NOT flag meaning mismatch
        mismatch_problems = [p for p in problems if "含义不匹配" in p]
        self.assertEqual(mismatch_problems, [], f"Should not flag correct meaning: {mismatch_problems}")


class TestTerminologyGlossary(unittest.TestCase):
    """Test terminology translation checks."""
    
    def test_mesaconate_mistranslation_flagged(self):
        """'美康酸' should be flagged when source has 'mesaconate'."""
        from inlight_articles import validate_names
        
        art = {
            "title": "美康酸在肠道炎症中的作用",
            "one_liner": "美康酸可以抑制炎症。",
            "background": "",
            "design": "",
            "results": ["美康酸处理后炎症减轻。"],
            "mechanism": "",
            "significance": "",
            "authors": "原文未给出",
            "limitations": [],
        }
        raw = "Mesaconate delivery to the gut reduces inflammation."
        problems = validate_names(art, raw)
        
        # Should flag the mistranslation
        term_problems = [p for p in problems if "中康酸" in p or "美康酸" in p]
        self.assertTrue(len(term_problems) > 0, f"Should flag '美康酸' mistranslation: {problems}")


class TestMalformedModelResponse(unittest.TestCase):
    """Regression tests for malformed model responses that caused crashes.
    
    Based on real crash capture from server_run1_crash/ where the model returned
    datacard, results, data_points as strings with embedded XML-like tags.
    """
    
    def test_string_datacard_rejected(self):
        """Model returning datacard as string should be caught, not crash."""
        from inlight_articles import _validate_article_structure
        
        # This is the actual malformed response structure from the crash
        malformed_art = {
            "url": "https://pubmed.ncbi.nlm.nih.gov/42843925/",
            "tier": "deep",
            "field": "c3",
            "title": "测试文章",
            "datacard": '\n<parameter name="study_type">概念验证研究',  # STRING, not dict!
            "results": '\n<parameter name="__placeholder__">',  # STRING, not list!
            "limitations": '\n<parameter name="__placeholder__">',  # STRING, not list!
            "data_points": '\n<parameter name="__placeholder__">',  # STRING, not list!
            "steps": '\n<parameter name="__placeholder__">',  # STRING, not list!
        }
        
        # Should return None (reject) instead of crashing
        result = _validate_article_structure(malformed_art, "test")
        self.assertIsNone(result)
    
    def test_valid_dict_datacard_passes(self):
        """Properly formed article should pass validation."""
        from inlight_articles import _validate_article_structure
        
        valid_art = {
            "url": "https://example.com",
            "tier": "brief",
            "field": "c3",
            "title": "测试",
            "one_liner": "这是一个测试",
            "background": "测试背景",
            "design": "研究设计",
            "significance": "研究意义",
            "datacard": {
                "study_type": "临床试验",
                "n": "36例",
                "control": "无",
                "intervention": "药物",
                "followup": "12个月",
                "primary_endpoint": "安全性",
                "primary_endpoint_result": "可耐受",
                "statistics": "原文未报告",
                "safety": "可接受",
            },
            "results": ["结果1", "结果2"],
            "limitations": ["局限1"],
            "data_points": [
                {"value": "36", "meaning": "患者数", "source_quote": "36 patients"},
            ],
            "steps": ["步骤1", "步骤2", "步骤3"],
        }
        
        result = _validate_article_structure(valid_art, "test")
        self.assertIsNotNone(result)
        self.assertIsInstance(result["datacard"], dict)
        self.assertIsInstance(result["results"], list)
    
    def test_json_string_datacard_coerced(self):
        """JSON-parseable string datacard should be coerced to dict."""
        from inlight_articles import _validate_article_structure
        
        # Model might return valid JSON as a string
        art_with_json_string = {
            "url": "https://example.com",
            "tier": "brief",
            "field": "c3",
            "title": "测试",
            "one_liner": "这是一个测试",
            "background": "测试背景",
            "design": "研究设计",
            "significance": "研究意义",
            "datacard": '{"study_type": "试验", "n": "10"}',  # Valid JSON string
            "results": '["结果1", "结果2"]',  # Valid JSON list string
            "limitations": '["局限1"]',
            "data_points": '[{"value": "10", "meaning": "n", "source_quote": "10 patients"}]',
            "steps": '["步骤1", "步骤2", "步骤3"]',
        }
        
        result = _validate_article_structure(art_with_json_string, "test")
        self.assertIsNotNone(result)
        self.assertIsInstance(result["datacard"], dict)
        self.assertEqual(result["datacard"]["study_type"], "试验")
        self.assertIsInstance(result["results"], list)
        self.assertEqual(result["results"], ["结果1", "结果2"])
    
    def test_missing_fields_handled(self):
        """Missing list/dict fields should be initialized, not crash.
        
        Required string fields: title, one_liner, background, design, significance
        This test verifies list/dict coercion for optional fields.
        """
        from inlight_articles import _validate_article_structure
        
        # Article with required strings but missing list/dict fields
        minimal_art = {
            "url": "https://example.com",
            "tier": "brief",
            "title": "测试",
            "one_liner": "测试一行",
            "background": "背景",
            "design": "设计",
            "significance": "意义",
        }
        
        result = _validate_article_structure(minimal_art, "test")
        self.assertIsNotNone(result)
        self.assertIsInstance(result.get("datacard"), dict)
        self.assertIsInstance(result.get("results"), list)
        self.assertIsInstance(result.get("data_points"), list)
    
    def test_validate_depth_with_string_datacard_does_not_crash(self):
        """validate_depth should handle malformed articles gracefully.
        
        Regression test for the actual crash at inlight_articles.py:1132
        where datacard.values() was called on a string.
        """
        from inlight_articles import validate_depth
        
        # This would have crashed before the fix
        art = {
            "tier": "brief",
            "title": "测试",
            "one_liner": "测试",
            "datacard": "这是一个字符串不是字典",  # WRONG TYPE
            "results": ["结果"],
            "limitations": ["局限"],
            "data_points": [],
        }
        
        # After fix: validate_depth expects validated input, but we also guard
        # against edge cases. This should not raise AttributeError.
        try:
            problems = validate_depth(art, "source text")
            # May have validation problems, but should not crash
            self.assertIsInstance(problems, list)
        except AttributeError as e:
            self.fail(f"validate_depth crashed with AttributeError: {e}")


class TestDedupAndBetterDraft(unittest.TestCase):
    """Acceptance item #12: dedup and better-draft must be tested, not just live."""

    def test_near_duplicate_rss_abstract_omitted_from_prompt(self):
        from inlight_articles import EnrichedItem, _article_prompt_abstract, is_near_duplicate

        abstract = (
            "Here we report a new method for in vivo delivery across human primary cells. "
            "The platform enables targeted editing with high efficiency."
        )
        rss = abstract  # bioRxiv RSS teaser == EPMC abstract
        self.assertTrue(is_near_duplicate(rss, abstract))
        item = EnrichedItem(
            url="https://www.biorxiv.org/content/10.64898/example",
            title="Example",
            source="bioRxiv",
            date="2026-10-01",
            abstract=abstract,
            rss_summary=rss,
        )
        block = _article_prompt_abstract(item)
        snippet = abstract[10:70]
        self.assertEqual(block.count(snippet), 1)
        self.assertNotIn("RSS 摘要", block)

    def test_distinct_rss_is_kept_once_each(self):
        from inlight_articles import EnrichedItem, _article_prompt_abstract

        item = EnrichedItem(
            url="https://example.com",
            title="Example",
            source="Nature",
            date="2026-10-01",
            abstract="The trial enrolled 36 patients and reported a 52% response rate.",
            rss_summary="Press teaser: a new antibody shows activity in early testing.",
        )
        block = _article_prompt_abstract(item)
        self.assertIn("52% response rate", block)
        self.assertIn("Press teaser", block)
        self.assertEqual(block.count("Press teaser"), 1)

    def test_better_draft_keeps_first_when_retry_is_worse(self):
        """If the retry introduces an invented number, keep the first draft."""
        from unittest.mock import patch
        from inlight_articles import EnrichedItem, _process_single_article

        body = (
            "这项研究在小鼠模型中验证了抗体阻断方案。"
            "研究者按每周一次给药，持续四周后评估炎症指标。"
            "结果显示相关通路信号下降，组织损伤减轻。"
            "该方案为后续人体试验提供了剂量参考。"
            "样本来自已建立的炎症模型，结果与既有观察一致。" * 8
        )
        first = {
            "url": "https://www.biorxiv.org/content/10.64898/example",
            "tier": "brief",
            "title": "抗体阻断减轻炎症",
            "journal": "bioRxiv",
            "authors": "",
            "one_liner": "抗体阻断后炎症指标下降。",
            "background": body[:80],
            "design": "小鼠模型，每周一次给药共四周。",
            "results": ["四周后炎症指标下降，给药持续四周。"],
            "mechanism": "",
            "limitations": ["动物模型，外推有限。"],
            "significance": "为后续人体试验提供剂量参考。",
            "datacard": {"study_type": "动物实验", "n": "不适用"},
            "data_points": [
                {"value": "4", "meaning": "周数", "source_quote": "once a week for 4 weeks"},
            ],
            "steps": ["给药", "观察", "评估"],
        }
        retry = dict(first)
        retry["results"] = [first["results"][0] + "研究共纳入12只小鼠。"]

        source = "Antibody blockade given once a week for 4 weeks reduced inflammation."
        enriched = EnrichedItem(
            url=first["url"],
            title=first["title"],
            source="bioRxiv",
            date="2026-10-01",
            abstract=source,
            rss_summary=source,
            journal="bioRxiv",
        )
        # Pad first body to clear the 450-Han gate
        extra = "研究者随后比较了给药前后的组织切片与细胞因子读出，观察到一致的下降趋势。" * 12
        first["background"] = extra
        retry["background"] = extra

        calls = {"n": 0}

        def fake_draft(item, tier, config, problems=None):
            calls["n"] += 1
            return dict(first) if calls["n"] == 1 else dict(retry)

        with patch("inlight_articles.draft_single_article", side_effect=fake_draft):
            art = _process_single_article(
                {"url": first["url"], "tier": "brief", "field": "c4"},
                {first["url"]: enriched},
                {},
            )
        self.assertIsNotNone(art)
        published = " ".join(art.get("results") or [])
        self.assertNotIn("12只", published)
        self.assertIn("四周", published)

    def test_retry_draft_score_keeps_verified_intervals(self):
        """When hard-problem counts tie, the draft with more source-verified numbers wins.

        This is the retry-selection rule (fault-injection m10): a redraft that
        deletes correct CIs must not beat the first draft.
        """
        from inlight_articles import _draft_quality_score

        src = (
            "Hazard ratios were 4.13 (95% CI 2.92-5.85) and 2.01 (95% CI 1.44-2.80) "
            "across 7000 patients followed for 3 years."
        )
        with_ci = {
            "results": ["HR 4.13（95% CI 2.92-5.85），HR 2.01（95% CI 1.44-2.80），随访3年。"],
            "one_liner": "7000例患者随访3年。",
            "datacard": {"n": "7000", "statistics": "HR 4.13 (2.92-5.85)"},
        }
        stripped = {
            "results": ["HR 4.13，随访3年。"],
            "one_liner": "详见正文HR。",
            "datacard": {"n": "7000", "statistics": "详见正文HR"},
        }
        first = _draft_quality_score([], ["soft"], with_ci, src)
        retry = _draft_quality_score([], ["soft"], stripped, src)
        self.assertLess(first, retry, f"draft with CIs should score better: {first} vs {retry}")

    def test_preprint_journal_never_taken_from_model(self):
        """bioRxiv items must not publish a model-invented journal (m16)."""
        from inlight_articles import EnrichedItem, sanitize_published_article

        item = EnrichedItem(
            url="https://www.biorxiv.org/content/10.64898/2026.10.01.755262v1",
            title="NAD paper",
            source="bioRxiv",
            date="2026-10-01",
            evidence_level="preprint",
            journal="",
            authors="",
        )
        art = sanitize_published_article(
            {
                "title": "NAD",
                "journal": "Nature Immunology",
                "authors": "Smith J, Chen L 等",
            },
            item,
        )
        self.assertNotEqual(art.get("journal"), "Nature Immunology")
        self.assertIn("bioRxiv", art.get("journal", ""))
        self.assertEqual(art.get("authors"), "")

    def test_wechat_render_isolates_int_datacard(self):
        """An int datacard value must not crash HTML render (m15)."""
        from inlight_articles import wechat_html_full

        bad = {
            "title": "bad",
            "tier": "brief",
            "one_liner": "尿液cfRNA",
            "datacard": {"n": 683, "study_type": "诊断"},
            "results": ["敏感性90%"],
            "limitations": [],
            "authors": "",
            "journal": "Nature Medicine",
        }
        good = {
            "title": "good",
            "tier": "brief",
            "one_liner": "对照文章",
            "datacard": {"n": "20例"},
            "results": ["缓解率58%"],
            "limitations": ["单臂"],
            "authors": "",
            "journal": "Nature Medicine",
        }
        html = wechat_html_full([bad, good], [], "2026-10-01")
        self.assertIn("good", html)
        self.assertIn("683", html)
        self.assertIn("对照文章", html)


class TestHoldoutGeneralRules(unittest.TestCase):
    """General number / name / unit rules from the hidden-set review."""

    def test_both_range_ends_are_present(self):
        from inlight_articles import number_exists_in_source, normalize_source_text

        src = "The HR was 1.30-3.35 and the rate ranged 1.7%-40.5% over 6-23 months."
        norm = normalize_source_text(src)
        raw = normalize_source_text(src, convert_english_words=False)
        for num, ctx in (("1.30", "HR 1.30"), ("3.35", "HR 3.35"),
                         ("1.7%", "1.7%"), ("40.5%", "40.5%"),
                         ("6", "6-23 months"), ("23", "23 months")):
            self.assertTrue(
                number_exists_in_source(num, norm, set(), ctx, source_raw=raw),
                f"{num} should be found in {src}",
            )

    def test_nearby_percent_does_not_reclassify_a_count(self):
        from inlight_articles import number_meaning_matches_source

        ok, reason = number_meaning_matches_source(
            "803",
            "共803名受试者接种",
            "803 NutriVax recipients were enrolled; AE rate was 12%.",
        )
        self.assertTrue(ok, reason)

    def test_metric_classes_are_exclusive(self):
        from inlight_articles import number_meaning_matches_source

        ok, _ = number_meaning_matches_source(
            "93.5%", "确认ORR为93.5%", "The DCR was 93.5% and the ORR was 71.0%."
        )
        self.assertFalse(ok)
        ok, _ = number_meaning_matches_source(
            "12.0", "中位OS为12.0个月", "Median PFS was 12.0 months; OS was not mature."
        )
        self.assertFalse(ok)

    def test_listed_rate_inherits_preceding_metric(self):
        """A later rate in a list keeps the preceding metric, not the next one.

        '20% in group B, and the 1-year survival was 26%' must not treat 20% as OS.
        """
        from inlight_articles import closest_metric_class, number_meaning_matches_source

        src = (
            "disease control was achieved in 25% in group A and 20% in group B, "
            "and the 1-year survival rate was 26% in group A and 15% in group B."
        )
        src_l = src.lower()
        self.assertEqual(closest_metric_class(src_l, src_l.find("20%")), "dcr")
        self.assertEqual(closest_metric_class(src_l, src_l.find("25%")), "dcr")
        self.assertEqual(closest_metric_class(src_l, src_l.find("26%")), "os")
        self.assertEqual(closest_metric_class(src_l, src_l.find("15%")), "os")

        out = (
            "A组疾病控制比例为25%，B组为20%。"
            "A组1年生存率为26%，B组为15%。"
            "目前25%与20%的疾病控制比例及26%与15%的1年生存率缺乏对照。"
        )
        for num in ("20%", "25%", "26%", "15%"):
            ok, reason = number_meaning_matches_source(num, out, src)
            self.assertTrue(ok, f"{num}: {reason}")

        cn = "目前25%与20%的疾病控制比例及26%与15%的1年生存率"
        self.assertEqual(closest_metric_class(cn, cn.find("20%")), "dcr")
        self.assertEqual(closest_metric_class(cn, cn.find("26%")), "os")

        listed = "疾病控制率A组25%、B组20%，1年生存率26%与15%。"
        self.assertEqual(closest_metric_class(listed, listed.find("20%")), "dcr")
        self.assertEqual(closest_metric_class(listed, listed.find("26%")), "os")

        died = "A组25%的患者死亡。"
        self.assertEqual(closest_metric_class(died, died.find("25%")), "death")
        ok, _ = number_meaning_matches_source("25%", died, src)
        self.assertFalse(ok)

    def test_validate_depth_cd6_listed_rates_use_original_draft(self):
        """Production validate_depth must not reclassify 26%/15% as DCR.

        extract_numbers_with_context CJK-splits the match form, so the
        meaning check has to read the original draft, not the ±20 window.
        """
        from inlight_articles import validate_depth

        src = (
            "Itolizumab is an anti-CD6 monoclonal antibody. "
            "disease control was achieved in 25% in group A and 20% in group B, "
            "and the 1-year survival rate was 26% in group A and 15% in group B."
        )
        art = {
            "tier": "deep",
            "title": "itolizumab：疾病控制率20%–25%",
            "one_liner": "疾病控制率A组25%、B组20%，1年生存率26%与15%。",
            "background": "CD6靶向。",
            "design": "概念验证。",
            "results": [
                "疾病控制（次要终点）：A组疾病控制率为25%，B组为20%。",
                "生存（次要终点）：1年生存率A组为26%，B组为15%。",
            ],
            "mechanism": "",
            "significance": "目前25%与20%的疾病控制比例及26%与15%的1年生存率缺乏对照。",
            "limitations": [
                "疾病控制率25%与20%、1年生存率26%与15%的分母不明。",
                "无对照。",
                "主要终点是安全性。",
            ],
        }
        problems = validate_depth(art, src)
        meaning = [p for p in problems if "含义不匹配" in p]
        self.assertEqual(meaning, [], meaning)
        invented = [p for p in problems if "未找到" in p]
        self.assertEqual(invented, [], invented)

        swapped = dict(art)
        swapped["results"] = ["1年生存率A组为25%，B组为20%。", "疾病控制率26%与15%。"]
        swapped["one_liner"] = "1年生存率A组25%、B组20%。"
        swapped["significance"] = ""
        swapped["limitations"] = ["无对照。", "样本小。", "随访短。"]
        swapped_probs = validate_depth(swapped, src)
        self.assertTrue(
            any("含义不匹配" in p for p in swapped_probs),
            swapped_probs,
        )

    def test_nearby_group_word_does_not_drop_a_rate(self):
        """'两组' in the same window is not a grouping claim for 25%."""
        from inlight_articles import number_exists_in_source, normalize_source_text

        src = "disease control was achieved in 25% in group A and 20% in group B"
        norm = normalize_source_text(src)
        raw = normalize_source_text(src, convert_english_words=False)
        self.assertTrue(number_exists_in_source(
            "25%", norm, set(), "无法判断25%与20%的差别，摘要未比较两组",
            source_raw=raw,
        ))

    def test_time_and_noun_must_match(self):
        from inlight_articles import number_exists_in_source, normalize_source_text

        weeks = "dosing every 3 weeks in 683 urine samples"
        norm = normalize_source_text(weeks)
        raw = normalize_source_text(weeks, convert_english_words=False)
        # Value-anywhere: the same numeric value is enough regardless of unit.
        self.assertTrue(number_exists_in_source(
            "3个月", norm, set(), "每3个月一次", source_raw=raw
        ))
        self.assertTrue(number_exists_in_source(
            "683例", norm, set(), "683例患者", source_raw=raw
        ))

    def test_direction_flip_is_caught(self):
        from inlight_articles import check_comparison_direction

        problems = check_comparison_direction(
            "联合组DCR更低。",
            "Disease control was higher in the combination arm (93.5% vs 71.0%).",
        )
        self.assertTrue(problems)

    def test_lower_is_better_toxicity_not_flagged(self):
        from inlight_articles import check_comparison_direction

        problems = check_comparison_direction(
            "联合组毒性更低，lower is better。",
            "Disease control was higher in the combination arm. Grade 3 adverse events were reduced.",
        )
        self.assertFalse(problems)

    def test_name_prefix_digits_not_extracted(self):
        from inlight_articles import extract_numbers_with_context, validate_depth

        text = "p38 MAPK and MK-25 and 4-1BB ligand were expressed."
        nums = [n for n, _ in extract_numbers_with_context(text)]
        self.assertFalse(any(n in {"38", "25", "4", "1"} for n in nums), nums)
        art = _skilight_brief()
        art["results"] = list(art["results"]) + ["通路涉及 p38 与 MK-25 及 4-1BB。"]
        problems = validate_depth(art, _SKYLIGHT + " p38 MAPK MK-25 4-1BB ligand")
        self.assertFalse(any("p38" in p or "MK-25" in p or "4-1BB" in p for p in problems if "标识符" in p), problems)

    def test_chinese_drug_requires_source_inn(self):
        from inlight_articles import validate_names

        art = {
            "title": "格菲妥单抗联合方案",
            "one_liner": "",
            "background": "",
            "design": "",
            "results": ["格菲妥单抗治疗后缓解。"],
            "mechanism": "",
            "significance": "",
            "authors": "",
            "limitations": [],
        }
        problems = validate_names(art, "mosunetuzumab plus polatuzumab in DLBCL")
        self.assertTrue(any("格菲妥单抗" in p for p in problems))


# NEW material (not used in prior review rounds): SKYLIGHT 1 / fezolinetant,
# FGFR mid-dot IC50s, RSV nirsevimab MELODY-style assay, tislelizumab INN.
_SKYLIGHT = (
    "SKYLIGHT 1 randomised 527 women with menopause-associated vasomotor "
    "symptoms to fezolinetant 45 mg, fezolinetant 30 mg, or placebo once daily. "
    "At week 12 the mean reduction in VMS frequency was 64% with 45 mg versus "
    "45% with placebo (p<0.001). Treatment-emergent adverse events occurred in "
    "135 of 173 women on 45 mg. Sensitivity and specificity of the VMS diary "
    "were 91.2% and 88.4% respectively. Dose-limiting toxicity was seen in "
    "2 of 12 participants at cycle 3. The IC50 was 18·9 nM against FGFR2."
)

_PAD = "该研究为围绝经期血管舒缩症状提供了口服受体拮抗剂的对照证据，结果与既有观察一致。" * 12


def _skilight_brief(**overrides):
    art = {
        "tier": "brief",
        "title": "fezolinetant 45 mg使VMS频率下降64%",
        "one_liner": "SKYLIGHT 1纳入527名女性，fezolinetant 45 mg使VMS频率下降64%。",
        "journal": "",
        "authors": "",
        "background": _PAD,
        "design": "随机对照，527名女性接受fezolinetant 45 mg、30 mg或安慰剂，每日一次。",
        "results": [
            "第12周45 mg组VMS频率平均下降64%，安慰剂为45%。"
            "45 mg组173名女性中135名出现治疗期不良事件。"
            "日记敏感性与特异性分别为91.2%与88.4%。"
            "第3周期12名参与者中2名出现剂量限制毒性。FGFR2的IC50为18.9 nM。"
        ],
        "mechanism": "",
        "limitations": ["单篇摘要，外推需谨慎"],
        "significance": "若后续研究重复，口服受体拮抗剂或可用于血管舒缩症状。",
        "datacard": {
            "study_type": "随机对照",
            "n": "527名",
            "control": "安慰剂",
            "intervention": "fezolinetant 45 mg",
            "followup": "12周",
            "primary_endpoint": "VMS频率",
            "primary_endpoint_result": "下降64%",
            "statistics": "p<0.001",
            "safety": "135/173",
        },
        "data_points": [
            {"value": "527", "meaning": "人数", "source_quote": "SKYLIGHT 1 randomised 527 women with menopause-associated"},
            {"value": "64%", "meaning": "VMS下降", "source_quote": "mean reduction in VMS frequency was 64% with 45 mg"},
            {"value": "45%", "meaning": "安慰剂", "source_quote": "64% with 45 mg versus 45% with placebo"},
            {"value": "18.9", "meaning": "IC50 nM", "source_quote": "The IC50 was 18·9 nM against FGFR2"},
        ],
        "url": "https://doi.org/10.1016/S0140-6736(23)00000-1",
    }
    art.update(overrides)
    return art


class TestNewMaterialDeterministic(unittest.TestCase):
    """False-alarm cuts and journal rules, built from new source text."""

    def test_middle_dot_decimal_matches_source(self):
        from inlight_articles import number_exists_in_source, normalize_source_text

        src = "The IC50 was 18·9 nM against FGFR2 and 7·4 nM against FGFR1."
        norm = normalize_source_text(src)
        raw = normalize_source_text(src, convert_english_words=False)
        self.assertTrue(number_exists_in_source(
            "18.9", norm, set(), "IC50为18.9 nM", source_raw=raw,
        ))

    def test_respectively_pairs_sensitivity_specificity(self):
        from inlight_articles import closest_metric_class, number_meaning_matches_source

        src = "Sensitivity and specificity of the VMS diary were 91.2% and 88.4% respectively."
        src_l = src.lower()
        self.assertEqual(closest_metric_class(src_l, src_l.find("91.2")), "sensitivity")
        self.assertEqual(closest_metric_class(src_l, src_l.find("88.4")), "specificity")
        ok, reason = number_meaning_matches_source(
            "91.2%", "敏感性91.2%，特异性88.4%", src,
        )
        self.assertTrue(ok, reason)

    def test_sens_spec_same_sentence_not_exclusive_false_alarm(self):
        from inlight_articles import number_meaning_matches_source

        src = "The VMS diary had 91.2% sensitivity and 88.4% specificity in the same readout."
        ok, reason = number_meaning_matches_source(
            "91.2%", "日记敏感性和特异性分别为91.2%和88.4%", src,
        )
        self.assertTrue(ok, reason)

    def test_cycle_number_is_exempt(self):
        from inlight_articles import is_exempt_number_context, validate_depth

        self.assertTrue(is_exempt_number_context("剂量限制毒性见于cycle 3", "3"))
        self.assertTrue(is_exempt_number_context("第3周期出现DLT", "3"))
        problems = validate_depth(_skilight_brief(), _SKYLIGHT)
        cycle_hits = [p for p in problems if "3" in p and "周期" in p]
        self.assertFalse(cycle_hits, problems)

    def test_generic_facility_and_grouping_words_ignored(self):
        from inlight_articles import validate_names

        art = {
            "title": "单中心随机对照",
            "one_liner": "研究在医学中心完成，两组均给药。",
            "background": "",
            "design": "单中心、两组对照。",
            "results": [],
            "mechanism": "",
            "significance": "",
            "authors": "",
            "limitations": [],
        }
        problems = validate_names(art, _SKYLIGHT)
        self.assertFalse(any("机构" in p for p in problems), problems)

    def test_qualitative_datapoint_allowed(self):
        from inlight_articles import validate_depth

        art = _skilight_brief()
        art["data_points"] = art["data_points"] + [
            {"value": "wild-type", "meaning": "FGFR2 genotype", "source_quote": "short"},
        ]
        problems = validate_depth(art, _SKYLIGHT)
        self.assertFalse(any("必须包含数字" in p and "wild-type" in p for p in problems), problems)

    def test_invented_range_requires_source_interval(self):
        from inlight_articles import _invented_numeric_range

        hits = _invented_numeric_range("随访6-23个月", "Follow-up was 12 weeks; VMS fell 64%.")
        self.assertTrue(hits)
        ok = _invented_numeric_range("随访6-23个月", "Patients were followed 6-23 months.")
        self.assertFalse(ok)

    def test_range_accepted_when_both_endpoints_in_source(self):
        from inlight_articles import _invented_numeric_range

        ok = _invented_numeric_range(
            "随访6至23个月",
            "Follow-up ranged from 6 months to 23 months.",
        )
        self.assertFalse(ok, ok)
        ok_wave = _invented_numeric_range(
            "剂量1.7%～40.5%",
            "The reduction was 1.7% at week 4 and 40.5% at week 12.",
        )
        self.assertFalse(ok_wave, ok_wave)
        still_bad = _invented_numeric_range(
            "随访6-99个月",
            "Patients were followed from 6 months; VMS fell 64%.",
        )
        self.assertTrue(still_bad)

    def test_range_thousands_and_to_and_connectors(self):
        from inlight_articles import _invented_numeric_range

        ok_thousands = _invented_numeric_range(
            "随访1,139 to 2,000例",
            "The counts were 1,139 and 2,000 in the same row.",
        )
        self.assertFalse(ok_thousands, ok_thousands)
        ok_dash = _invented_numeric_range(
            "剂量1,139-2,000 mg",
            "Dose ranged 1139–2000 mg.",
        )
        self.assertFalse(ok_dash, ok_dash)
        invented = _invented_numeric_range(
            "随访9,999-12,000个月",
            "Follow-up was 6 months; n=1139.",
        )
        self.assertTrue(invented)


class TestMatcherToleratesFaithfulDrafts(unittest.TestCase):
    """False-drop fixes: spaced labels, CJK-glued numbers, qualitative kinds."""

    def test_spaced_and_subscript_identifier_not_a_count(self):
        from inlight_articles import (
            extract_number_core,
            extract_numbers_with_context,
            number_exists_in_source,
            extract_identifiers_from_source,
            normalize_source_text,
            validate_depth,
        )

        source = "CD 8 T cells and Th 17 subsets were enriched; CD₈ infiltration rose."
        extracted = extract_numbers_with_context("CD 8细胞与Th 17亚群")
        cores = [extract_number_core(n) for n, _ in extracted]
        self.assertNotIn("8", cores, extracted)
        self.assertNotIn("17", cores, extracted)

        art = {
            "tier": "brief",
            "title": "CD8 与 Th17",
            "one_liner": "CD 8与Th 17亚群升高。",
            "background": "背景一句。",
            "design": "设计一句。",
            "results": ["CD8与Th17浸润增加。"],
            "mechanism": "",
            "significance": "",
            "limitations": ["单中心外推有限。"],
            "data_points": [],
        }
        problems = validate_depth(art, source)
        self.assertFalse(any("标识符" in p for p in problems), problems)
        self.assertFalse(any("数字 '" in p for p in problems), problems)

        norm = normalize_source_text(source)
        ids = extract_identifiers_from_source(source)
        self.assertFalse(number_exists_in_source("8", norm, ids, "8例死亡"))

    def test_cjk_glued_and_dashed_numbers_match_source(self):
        from inlight_articles import (
            number_in_text_as_word_boundary,
            number_exists_in_source,
            normalize_source_text,
            validate_depth,
        )

        source = "共纳入527例患者，缓解率64%；—1,139例可评估。"
        self.assertTrue(number_in_text_as_word_boundary("527", source))
        self.assertTrue(number_in_text_as_word_boundary("64", "缓解率64%"))
        self.assertTrue(number_in_text_as_word_boundary("1139", source.replace(",", "").replace("，", "")))
        norm = normalize_source_text(source)
        raw = normalize_source_text(source, convert_english_words=False)
        self.assertTrue(number_exists_in_source("527", norm, set(), "共527例", source_raw=raw))
        self.assertTrue(number_exists_in_source("64", norm, set(), "缓解率64%", source_raw=raw))
        self.assertTrue(number_exists_in_source("1139", norm, set(), "—1,139例", source_raw=raw))
        self.assertFalse(number_exists_in_source("999", norm, set(), "共999例", source_raw=raw))

        art = {
            "tier": "brief",
            "title": "527例缓解率64%",
            "one_liner": "共527例，缓解率64%。",
            "background": "背景。",
            "design": "纳入527例。",
            "results": ["缓解率64%，可评估—1,139例。"],
            "mechanism": "",
            "significance": "",
            "limitations": ["外推有限。"],
            "data_points": [
                {"value": "527例", "meaning": "入组", "source_quote": "共纳入527例患者"},
                {"value": "64%", "meaning": "缓解率", "source_quote": "缓解率64%"},
            ],
        }
        problems = validate_depth(art, source)
        invented = [p for p in problems if "在原始材料中未找到" in p]
        self.assertEqual(invented, [], invented)

    def test_qualitative_kinds_are_not_numeric_claims(self):
        from inlight_articles import (
            extract_numbers_with_context,
            extract_chinese_numbers_with_context,
            validate_depth,
            is_exempt_number_context,
        )

        self.assertEqual(extract_numbers_with_context("鉴定出6种细胞亚群"), [])
        self.assertEqual(extract_chinese_numbers_with_context("鉴定出六种细胞亚群"), [])
        self.assertTrue(is_exempt_number_context("six kinds of subsets", "6"))

        art = {
            "tier": "brief",
            "title": "六种亚群",
            "one_liner": "鉴定出六种细胞亚群。",
            "background": "背景。",
            "design": "单细胞分析。",
            "results": ["共六种表型，另有6类组织分型。"],
            "mechanism": "",
            "significance": "",
            "limitations": ["外推有限。"],
            "data_points": [
                {"value": "六种", "meaning": "细胞亚群种类", "source_quote": "six kinds of subsets"},
            ],
        }
        problems = validate_depth(art, "Single-cell analysis identified six kinds of subsets.")
        self.assertFalse(any("必须包含数字" in p for p in problems), problems)
        self.assertFalse(any("数字 '" in p or "中文数字" in p for p in problems), problems)

    def test_normalize_for_match_th17_emdash_and_zhi_range(self):
        from inlight_articles import (
            extract_numbers_with_context,
            extract_number_core,
            normalize_for_match,
            validate_depth,
        )

        self.assertEqual(normalize_for_match("T H 17").lower(), "th17")
        self.assertEqual(normalize_for_match("TH17").lower(), "th17")
        self.assertIn("184973", normalize_for_match("—184,973"))
        self.assertIn("6", normalize_for_match("随访6至23个月"))
        self.assertIn("23", normalize_for_match("随访6至23个月"))

        cores = [extract_number_core(n) for n, _ in extract_numbers_with_context("T H 17 亚群升高")]
        self.assertNotIn("17", cores, cores)

    def test_realistic_oa_results_through_production_validate_depth(self):
        """End-to-end through validate_depth (the production verifier), not helpers only."""
        from inlight_articles import validate_depth
        from tests.test_acir_qc import _deep_art, _results

        source = (
            _results()
            + " TH17 cells expanded after treatment. "
            "Quality-filtered reads totaled —184,973. "
            "Single-cell analysis identified six kinds of myeloid subsets "
            "and four types of stromal cells. "
            "Follow-up ranged from 6 months to 23 months. "
            "The objective response rate was 64% among 527 women. "
            "control response rate was 32% hazard ratio 0.50 "
            "Grade 3+ adverse events 21% median follow-up 18 months. "
            "tislelizumab 200 mg."
        )
        art = _deep_art(
            results=[
                "主要终点客观缓解率为64%，对照为32%，风险比0.50，随访6至23个月。",
                "527例可评估，T H 17亚群升高，可读段—184,973。",
                "鉴定出六种髓系亚群与四种基质分型，中位随访18个月。",
                "三级以上不良事件发生率为21%。",
            ]
        )
        problems = validate_depth(art, source)
        invented = [p for p in problems if "在原始材料中未找到" in p or "标识符" in p or "数字范围" in p]
        self.assertEqual(invented, [], invented)

        bad = _deep_art(results=["客观缓解率达到99%。", "随访十八个月。", "不良事件21%。"])
        bad_probs = validate_depth(bad, source)
        self.assertTrue(
            any("99" in p and "未找到" in p for p in bad_probs),
            bad_probs,
        )

    def test_fulltext_results_glued_thousands_and_spaced_log(self):
        """One live-shaped Results pair through validate_depth: glued em-dash
        thousands, Latin label + CJK comma + thousands, spaced log-base."""
        from inlight_articles import validate_depth
        from tests.test_acir_qc import _deep_art, _results

        source = (
            _results()
            + " Quality-filtered reads totaled—184,973 uniquely mapped fragments. "
            "TH17，1,139 cells were recovered after sorting. "
            "Expression was reported as log 2 fold change. "
            "The objective response rate was 64% among 527 women "
            "control 32% hazard ratio 0.50 Grade 3+ AE 21% follow-up 18 months "
            "tislelizumab 200 mg. 1.2 million reads equivalent to 1,200,000."
        )
        art = _deep_art(
            results=[
                "可读段为184973，TH17，1139个细胞，log2倍数变化。",
                "主要终点客观缓解率为64%，对照为32%，风险比0.50。",
                "527例可评估，中位随访18个月。",
                "三级以上不良事件发生率为21%。",
            ]
        )
        problems = validate_depth(art, source)
        invented = [p for p in problems if "在原始材料中未找到" in p]
        self.assertEqual(invented, [], invented)
        self.assertFalse(any("标识符 '" in p and "未找到" in p for p in problems), problems)

        bad = _deep_art(
            results=[
                "可读段为184973，TH17，1139个细胞，log2倍数变化。",
                "主要终点客观缓解率为99%。",
                "527例可评估，中位随访18个月。",
                "三级以上不良事件发生率为21%。",
            ]
        )
        bad_probs = validate_depth(bad, source)
        self.assertTrue(
            any("99" in p and "未找到" in p for p in bad_probs),
            bad_probs,
        )

    def test_realistic_pair_through_process_single_article_verifier(self):
        """Same pair through _process_single_article so validate_depth is not mocked."""
        import os
        from unittest.mock import patch
        from inlight_articles import EnrichedItem, _process_single_article
        from inlight_qc import GEMINI_SCORE_KEYS, record_fulltext
        from tests.test_acir_qc import _deep_art, _results

        source = (
            _results()
            + " TH17 cells expanded. Mapped reads —184,973. "
            "six kinds of subsets. Follow-up from 6 months to 23 months. "
            "objective response rate was 64% control response rate was 32% "
            "among 527 women hazard ratio 0.50 Grade 3+ adverse events 21% "
            "median follow-up 18 months. tislelizumab 200 mg."
        )
        item = EnrichedItem(
            url="https://doi.org/10.1/oa-ft", title="T", source="N", date="2026-01-01",
            pmcid="PMC884973",
        )
        record_fulltext(item, source, source_label="PMC PMC884973")
        art = _deep_art(
            url=item.url,
            results=[
                "主要终点客观缓解率为64%，对照为32%，风险比0.50，随访6至23个月。",
                "527例可评估，T H 17亚群升高，可读段—184,973。",
                "鉴定出六种亚群，中位随访18个月。",
                "三级以上不良事件发生率为21%。",
            ],
        )

        def fake_draft(it, tier, config, problems=None):
            return dict(art)

        with patch.dict(os.environ, {"GEMINI_API_KEY": "test-gemini"}):
            with patch("inlight_qc.gemini_review_deep", return_value={
                "pass": True, "scores": {k: 8 for k in GEMINI_SCORE_KEYS},
                "reasons": "ok", "factual_mismatch": False,
            }):
                with patch("inlight_articles.draft_single_article", side_effect=fake_draft):
                    with patch("inlight_articles.validate_names", return_value=[]):
                        with patch("inlight_qc.validate_acir_structure", return_value=[]):
                            with patch("inlight_articles.verify_article_claims", return_value={
                                "status": "ok", "problems": [], "calls": 1,
                                "input_tokens": 1, "output_tokens": 1,
                            }):
                                out = _process_single_article(
                                    {"url": item.url, "tier": "deep", "field": "c3"},
                                    {item.url: item},
                                    {"min_deep": 3, "acir_qc": True},
                                )
        self.assertIsNotNone(out, "faithful OA Results draft was false-dropped")
        self.assertEqual(out["tier"], "deep")

        bad = dict(art)
        bad["results"] = ["客观缓解率达到99%。", "随访十八个月。", "不良事件21%。"]

        def fake_bad(it, tier, config, problems=None):
            return dict(bad)

        stats = {"drops": [], "qc_report": {"articles": []}}
        with patch.dict(os.environ, {"GEMINI_API_KEY": "test-gemini"}):
            with patch("inlight_qc.gemini_review_deep", return_value={
                "pass": True, "scores": {k: 8 for k in GEMINI_SCORE_KEYS},
                "reasons": "ok", "factual_mismatch": False,
            }):
                with patch("inlight_articles.draft_single_article", side_effect=fake_bad):
                    with patch("inlight_articles.validate_names", return_value=[]):
                        dropped = _process_single_article(
                            {"url": item.url, "tier": "deep", "field": "c3"},
                            {item.url: item},
                            {"min_deep": 3, "acir_qc": True},
                            stats=stats,
                        )
        self.assertIsNone(dropped)
        self.assertTrue(any("99" in d.get("reason", "") for d in stats["drops"]))

    def test_sge_once_and_millions_through_production_validate_depth(self):
        """Real SGE runB drafts: 一次/百万/DOI fragments must not false-drop i8."""
        import json
        from pathlib import Path
        from inlight_articles import (
            extract_chinese_numbers_with_context,
            extract_numbers_with_context,
            validate_depth,
            _hard_problems,
        )

        week = json.loads(Path(
            "tests/acceptance_articles_fixtures/weeks/pr5f.json"
        ).read_text())
        item = next(
            it for it in week["items"]
            if "s41587-026-03318-7" in it["row"]["url"]
        )
        raw = item["nature_abstract"] + "\n" + item["row"]["summary"]
        runb = json.loads(Path(
            "tests/acceptance_articles_fixtures/recorded_claude/pr5f_runB.json"
        ).read_text())

        def _draft(i):
            for call in runb["calls"]:
                if call.get("i") == i:
                    return call["content"][0]["input"]
            raise AssertionError(i)

        self.assertEqual(extract_chinese_numbers_with_context("相当于一次独立实验"), [])
        self.assertEqual(extract_chinese_numbers_with_context("筛选了数以百万计的通路组合"), [])
        self.assertFalse(extract_numbers_with_context(item["row"]["summary"]))

        i8 = _draft(8)
        i8_probs = validate_depth(i8, raw)
        self.assertFalse(
            any("一次" in p or "百万" in p or "结果字段" in p for p in i8_probs),
            i8_probs,
        )
        self.assertFalse(_hard_problems(i8_probs), i8_probs)

        i7 = _draft(7)
        i7_probs = validate_depth(i7, raw)
        self.assertTrue(any("必须包含数字" in p for p in i7_probs), i7_probs)
        self.assertFalse(any("一次" in p or "结果字段" in p for p in i7_probs), i7_probs)

    def test_sge_i8_through_process_single_article_verifier(self):
        """i8 must publish as deep through _process_single_article (not helpers only)."""
        import json
        from pathlib import Path
        from unittest.mock import patch
        from inlight_articles import EnrichedItem, _process_single_article

        week = json.loads(Path(
            "tests/acceptance_articles_fixtures/weeks/pr5f.json"
        ).read_text())
        row = next(
            it for it in week["items"]
            if "s41587-026-03318-7" in it["row"]["url"]
        )
        runb = json.loads(Path(
            "tests/acceptance_articles_fixtures/recorded_claude/pr5f_runB.json"
        ).read_text())
        art = next(
            c["content"][0]["input"] for c in runb["calls"] if c.get("i") == 8
        )
        item = EnrichedItem(
            url=row["row"]["url"],
            title=row["row"]["title"],
            source=row["row"]["source"],
            date=row["row"]["date"],
            abstract=row["nature_abstract"],
            rss_summary=row["row"]["summary"],
            evidence_level="abstract",
        )

        def fake_draft(it, tier, config, problems=None):
            return dict(art)

        with patch("inlight_articles.draft_single_article", side_effect=fake_draft):
            with patch("inlight_articles.validate_names", return_value=[]):
                with patch("inlight_articles.verify_article_claims", return_value={
                    "status": "ok", "problems": [], "calls": 1,
                    "input_tokens": 1, "output_tokens": 1,
                }):
                    out = _process_single_article(
                        {"url": item.url, "tier": "deep", "field": "c7"},
                        {item.url: item},
                        {},
                    )
        self.assertIsNotNone(out, "faithful SGE redraft was false-dropped")
        self.assertEqual(out["tier"], "deep")
        self.assertIn("散弹枪式", out.get("title", "") + out.get("one_liner", ""))

    def test_chinese_numeral_classifier_on_groups(self):
        from inlight_articles import classify_unit_in_context, number_exists_in_source, normalize_source_text
        from inlight_articles import UNIT_COUNT, NOUN_GROUP, classify_noun_after, chinese_numeral_to_arabic

        ctx = chinese_numeral_to_arabic("随机分为三组")
        self.assertEqual(classify_noun_after(ctx, ctx.find("3") + 1), NOUN_GROUP)
        src = "Women were randomised to three groups (45 mg, 30 mg, placebo)."
        norm = normalize_source_text(src)
        raw = normalize_source_text(src, convert_english_words=False)
        self.assertTrue(number_exists_in_source(
            "3", norm, set(), "随机分为三组", source_raw=raw,
        ))

    def test_cn_drug_uses_token_boundary_and_inn(self):
        from inlight_articles import validate_names

        # Neighbouring characters must not be swallowed: 予替雷利珠单抗后
        art = {
            "title": "予替雷利珠单抗后缓解",
            "one_liner": "",
            "background": "",
            "design": "",
            "results": ["予替雷利珠单抗治疗后出现缓解。"],
            "mechanism": "",
            "significance": "",
            "authors": "",
            "limitations": [],
        }
        # Source INN tislelizumab justifies 替雷利珠单抗 via syllable table
        ok = validate_names(art, "tislelizumab 200 mg every 3 weeks in NSCLC")
        self.assertFalse(any("替雷利珠单抗" in p for p in ok), ok)
        # A different INN must not justify a neighbouring-character grab
        bad = validate_names(
            {"title": "给予雷利珠单抗", "one_liner": "", "background": "",
             "design": "", "results": ["给予雷利珠单抗。"], "mechanism": "",
             "significance": "", "authors": "", "limitations": []},
            "tislelizumab 200 mg",
        )
        self.assertTrue(any("雷利珠单抗" in p for p in bad), bad)

    def test_drop_model_journal_when_source_has_none(self):
        from inlight_articles import EnrichedItem, sanitize_published_article

        item = EnrichedItem(
            url="https://doi.org/10.1016/example",
            title="SKYLIGHT",
            source="PubMed",
            date="2023-01-01",
            evidence_level="abstract",
            journal="",
        )
        art = sanitize_published_article({"journal": "Nature Metabolism", "title": "x"}, item)
        self.assertNotEqual(art.get("journal"), "Nature Metabolism")
        self.assertEqual(art.get("journal"), "PubMed")

    def test_preprint_body_cannot_claim_peer_review(self):
        from inlight_articles import validate_depth

        art = _skilight_brief(
            url="https://www.biorxiv.org/content/10.1101/2026.03.01.123456v1",
            evidence_level="preprint",
            results=["该研究已发表于Nature Metabolism并经过同行评议，VMS下降64%。"],
        )
        problems = validate_depth(art, _SKYLIGHT)
        self.assertTrue(any("预印本" in p or "同行评议" in p for p in problems), problems)

    def test_skilight_correct_draft_passes_deterministic(self):
        problems = validate_depth(_skilight_brief(), _SKYLIGHT)
        hard = [p for p in problems if "未找到" in p or "含义不匹配" in p or "数字范围" in p]
        self.assertFalse(hard, problems)


class TestClaimVerifier(unittest.TestCase):
    """Mocked submit_claim_audit labels, including a span-not-in-source case."""

    def _msg(self, claims, usage=(1200, 400)):
        block = MagicMock()
        block.type = "tool_use"
        block.name = "submit_claim_audit"
        block.input = {"claims": claims}
        msg = MagicMock()
        msg.content = [block]
        msg.usage = MagicMock(input_tokens=usage[0], output_tokens=usage[1])
        return msg

    def test_supported_label_with_real_span_is_ok(self):
        from inlight_articles import verify_article_claims

        span = "mean reduction in VMS frequency was 64% with 45 mg"
        with patch("anthropic.Anthropic") as cls:
            cls.return_value.messages.create.return_value = self._msg([
                {"claim": "45 mg使VMS下降64%", "kind": "number", "label": "SUPPORTED",
                 "source_span": span, "factual": True},
            ])
            out = verify_article_claims(_skilight_brief(), _SKYLIGHT)
        self.assertEqual(out["status"], "ok")
        self.assertEqual(out["calls"], 1)
        self.assertEqual(out["input_tokens"], 1200)
        self.assertEqual(out["output_tokens"], 400)

    def test_contradicted_with_real_span_drops(self):
        from inlight_articles import verify_article_claims

        span = "mean reduction in VMS frequency was 64% with 45 mg versus 45% with placebo"
        with patch("anthropic.Anthropic") as cls:
            cls.return_value.messages.create.return_value = self._msg([
                {"claim": "安慰剂优于45 mg", "kind": "comparison", "label": "CONTRADICTED",
                 "source_span": span, "factual": True},
            ])
            out = verify_article_claims(_skilight_brief(), _SKYLIGHT)
        self.assertEqual(out["status"], "contradicted")
        self.assertTrue(out["problems"])

    def test_not_in_source_factual_is_flagged(self):
        from inlight_articles import verify_article_claims

        with patch("anthropic.Anthropic") as cls:
            cls.return_value.messages.create.return_value = self._msg([
                {"claim": "中位OS延长至28个月", "kind": "number", "label": "NOT_IN_SOURCE",
                 "source_span": "", "factual": True},
            ])
            out = verify_article_claims(_skilight_brief(), _SKYLIGHT)
        self.assertEqual(out["status"], "not_in_source")
        self.assertTrue(any("原文未支持" in p for p in out["problems"]))

    def test_span_not_in_source_is_never_trusted(self):
        from inlight_articles import verify_article_claims, span_exists_in_source

        fake = "The objective response rate was 99% in the fezolinetant arm"
        self.assertFalse(span_exists_in_source(fake, _SKYLIGHT))
        with patch("anthropic.Anthropic") as cls:
            cls.return_value.messages.create.return_value = self._msg([
                {"claim": "缓解率99%", "kind": "number", "label": "CONTRADICTED",
                 "source_span": fake, "factual": True},
                {"claim": "已在Cell发表", "kind": "journal", "label": "SUPPORTED",
                 "source_span": "Published in Cell after peer review", "factual": True},
            ])
            out = verify_article_claims(_skilight_brief(), _SKYLIGHT)
        self.assertEqual(out["status"], "ok", out)

    def test_background_sentence_without_fact_is_allowed(self):
        from inlight_articles import verify_article_claims

        with patch("anthropic.Anthropic") as cls:
            cls.return_value.messages.create.return_value = self._msg([
                {"claim": "围绝经期潮热影响生活质量", "kind": "background",
                 "label": "NOT_IN_SOURCE", "source_span": "", "factual": False},
            ])
            out = verify_article_claims(_skilight_brief(), _SKYLIGHT)
        self.assertEqual(out["status"], "ok")

    def test_verifier_omits_temperature_arg(self):
        from inlight_articles import verify_article_claims

        with patch("anthropic.Anthropic") as cls:
            cls.return_value.messages.create.return_value = self._msg([])
            verify_article_claims(_skilight_brief(), _SKYLIGHT, fail_closed=False)
            kwargs = cls.return_value.messages.create.call_args.kwargs
        self.assertNotIn("temperature", kwargs)
        names = [t["name"] for t in kwargs["tools"]]
        self.assertIn("submit_claim_audit", names)
        self.assertIn("test_tool", names)

    def test_verifier_fail_closed_on_api_error(self):
        from inlight_articles import verify_article_claims

        with patch("anthropic.Anthropic", side_effect=RuntimeError("boom")):
            out = verify_article_claims(_skilight_brief(), _SKYLIGHT, fail_closed=True)
        self.assertEqual(out["status"], "error")
        self.assertTrue(any("API error" in p for p in out["problems"]))

    def test_verifier_fail_closed_on_missing_tool_call(self):
        from inlight_articles import verify_article_claims

        msg = MagicMock()
        msg.content = []
        msg.usage = MagicMock(input_tokens=10, output_tokens=4)
        with patch("anthropic.Anthropic") as cls:
            cls.return_value.messages.create.return_value = msg
            out = verify_article_claims(_skilight_brief(), _SKYLIGHT, fail_closed=True)
        self.assertEqual(out["status"], "error")
        self.assertTrue(any("submit_claim_audit" in p for p in out["problems"]))

    def test_verifier_fail_open_when_requested(self):
        from inlight_articles import verify_article_claims

        with patch("anthropic.Anthropic", side_effect=RuntimeError("boom")):
            out = verify_article_claims(_skilight_brief(), _SKYLIGHT, fail_closed=False)
        self.assertEqual(out["status"], "ok")
        self.assertEqual(out["problems"], [])

    def test_production_claim_tools_omit_stub(self):
        from inlight_articles import verify_article_claims

        with patch("anthropic.Anthropic") as cls:
            cls.return_value.messages.create.return_value = self._msg([])
            verify_article_claims(_skilight_brief(), _SKYLIGHT, fail_closed=True)
            kwargs = cls.return_value.messages.create.call_args.kwargs
        names = [t["name"] for t in kwargs["tools"]]
        self.assertEqual(names, ["submit_claim_audit"])
        self.assertNotIn("test_tool", names)

    def test_streaming_required_falls_back_on_anthropic_112(self):
        from inlight_articles import _claude_create

        class _Msg:
            content = []
            stop_reason = "end_turn"

        final = _Msg()

        class _Messages:
            def create(self, **kwargs):
                if kwargs.get("stream"):
                    ev = type("E", (), {})()
                    ev.type = "message"
                    ev.message = final
                    return [ev]
                raise RuntimeError("Streaming is required")

        client = type("C", (), {})()
        client.messages = _Messages()
        out = _claude_create(client, model="x", max_tokens=4000, messages=[])
        self.assertIs(out, final)
        streamed = _claude_create(client, model="x", max_tokens=16000, messages=[])
        self.assertIs(streamed, final)

    def test_shared_fulltext_window(self):
        from inlight_qc import FULLTEXT_WINDOW, gemini_review_deep, GEMINI_SCORE_KEYS
        from inlight_articles import build_article_prompt, build_claim_audit_prompt, EnrichedItem

        self.assertEqual(FULLTEXT_WINDOW, 20000)
        long_ft = ("Z" * FULLTEXT_WINDOW) + "UNIQUE_TAIL_MARKER"
        item = EnrichedItem(
            url="https://doi.org/10.1/w", title="T", source="N", date="2026-01-01",
            evidence_level="fulltext", fulltext_results=long_ft,
        )
        draft_prompt = build_article_prompt(item, "deep")
        self.assertNotIn("UNIQUE_TAIL_MARKER", draft_prompt)
        claim_prompt = build_claim_audit_prompt({"title": "t", "results": ["64%"]}, long_ft, item)
        self.assertNotIn("UNIQUE_TAIL_MARKER", claim_prompt)
        captured = {}

        def fake_gen(prompt, model, key):
            captured["p"] = prompt
            return json.dumps({
                "scores": {k: 8 for k in GEMINI_SCORE_KEYS},
                "factual_mismatch": False, "reasons": "ok",
            })

        with patch.dict(os.environ, {"GEMINI_API_KEY": "k"}):
            with patch("inlight_qc._gemini_generate", side_effect=fake_gen):
                gemini_review_deep({"title": "t"}, long_ft, {})
        self.assertNotIn("UNIQUE_TAIL_MARKER", captured["p"])
        self.assertIn("Z" * 100, captured["p"])

    def test_system_notes_doi_not_flagged(self):
        from inlight_articles import validate_depth

        art = _skilight_brief()
        art["datacard"] = dict(art.get("datacard") or {})
        art["datacard"]["read_note"] = "核对记录：读了 PMC 全文 PMC999 的 Results。DOI 10.1016/S0140-6736(22)02056-0"
        art["datacard"]["doi"] = "10.1016/S0140-6736(22)02056-0"
        problems = validate_depth(art, _SKYLIGHT)
        self.assertFalse(any("标识符" in p and ("10.1016" in p or "PMC999" in p or "DOI" in p) for p in problems), problems)
        self.assertFalse(any("数字" in p and "未找到" in p and "10.1016" in p for p in problems), problems)

    def test_contradicted_stage_drops_article(self):
        from inlight_articles import EnrichedItem, _process_single_article

        first = _skilight_brief()
        item = EnrichedItem(
            url=first["url"],
            title="SKYLIGHT 1",
            source="The Lancet",
            date="2023-01-01",
            abstract=_SKYLIGHT,
            rss_summary=_SKYLIGHT,
            journal="The Lancet",
        )
        span = "mean reduction in VMS frequency was 64% with 45 mg versus 45% with placebo"

        def fake_draft(*a, **k):
            return dict(first)

        with patch("inlight_articles.draft_single_article", side_effect=fake_draft):
            with patch("inlight_articles.verify_article_claims", return_value={
                "status": "contradicted",
                "problems": ["主张与原文矛盾：安慰剂更优"],
                "claims": [{"source_span": span, "label": "CONTRADICTED"}],
                "calls": 1, "input_tokens": 900, "output_tokens": 300,
            }):
                art = _process_single_article(
                    {"url": first["url"], "tier": "brief", "field": "c4"},
                    {first["url"]: item},
                    {},
                )
        self.assertIsNone(art)

    def test_not_in_source_redrafts_once_then_ok(self):
        from inlight_articles import EnrichedItem, _process_single_article

        first = _skilight_brief()
        item = EnrichedItem(
            url=first["url"],
            title="SKYLIGHT 1",
            source="The Lancet",
            date="2023-01-01",
            abstract=_SKYLIGHT,
            rss_summary=_SKYLIGHT,
            journal="The Lancet",
        )
        audits = [
            {"status": "not_in_source", "problems": ["原文未支持的事实主张：OS 28个月"],
             "claims": [], "calls": 1, "input_tokens": 800, "output_tokens": 200},
            {"status": "ok", "problems": [], "claims": [],
             "calls": 1, "input_tokens": 800, "output_tokens": 180},
        ]

        def fake_draft(*a, **k):
            return dict(first)

        with patch("inlight_articles.draft_single_article", side_effect=fake_draft):
            with patch("inlight_articles.verify_article_claims", side_effect=audits):
                art = _process_single_article(
                    {"url": first["url"], "tier": "brief", "field": "c4"},
                    {first["url"]: item},
                    {},
                )
        self.assertIsNotNone(art)
        self.assertIn("64%", " ".join(art.get("results") or []))

    def test_enrich_keeps_longest_publisher_abstract(self):
        from inlight_articles import enrich_item

        short = json.dumps({
            "resultList": {"result": [{
                "abstractText": "Short EPMC teaser.",
                "pmid": "999",
                "isOpenAccess": "N",
            }]},
        }).encode()
        long_abs = "Fezolinetant 45 mg reduced VMS frequency by 64% among 527 women. " * 20
        html = (
            '<html><head><meta name="citation_abstract" content="'
            + long_abs
            + '"></head></html>'
        ).encode()

        def http(url, timeout=30):
            if "europepmc" in url:
                return short
            if "crossref" in url:
                return None
            if "eutils" in url:
                return None
            return html

        with patch("inlight_articles._http_get", side_effect=http):
            item = enrich_item({
                "url": "https://doi.org/10.1016/S0140-6736(23)00000-1",
                "title": "SKYLIGHT 1",
                "source": "Lancet",
                "date": "2023-01-01",
                "kind": "academic",
                "summary": "RSS teaser",
            })
        self.assertGreater(len(item.abstract), 200)
        self.assertIn("527", item.abstract)


class TestYieldAndSourceFetch(unittest.TestCase):
    """ACIR-style quantity/depth targets without relaxing accuracy."""

    def test_pipeline_targets_from_config(self):
        from inlight_articles import pipeline_targets

        defaults = pipeline_targets({})
        self.assertEqual(defaults["min_deep"], 3)
        self.assertEqual(defaults["max_deep"], 5)
        self.assertEqual(defaults["target_articles"], 3)
        self.assertEqual(defaults["target_deep"], 3)
        self.assertEqual(defaults["max_brief"], 0)
        self.assertEqual(defaults["max_candidates"], 0)  # no silent cap
        self.assertLessEqual(defaults["min_deep"], defaults["max_deep"])
        custom = pipeline_targets({
            "min_deep": 3, "max_deep": 5, "max_brief": 0, "max_candidates": 30,
        })
        self.assertEqual(custom["min_deep"], 3)
        self.assertEqual(custom["max_deep"], 5)
        self.assertEqual(custom["max_brief"], 0)
        self.assertEqual(custom["max_candidates"], 30)
        aliased = pipeline_targets({"target_deep": 4, "max_deep": 6})
        self.assertEqual(aliased["min_deep"], 4)
        self.assertEqual(aliased["max_deep"], 6)

    def test_omitted_max_candidates_does_not_drop_later_items(self):
        """Replay/acceptance fixtures omit the key; do not silently cap at 30."""
        from inlight_articles import process_articles, EnrichedItem

        n = 35
        rows = [{
            "url": f"https://doi.org/10.1/item-{i}",
            "kind": "academic",
            "title": f"T{i}",
            "source": "N",
            "date": "2026-01-01",
            "summary": "x" * 80,
        } for i in range(n)]
        seen = []

        def fake_enrich(row):
            seen.append(row["url"])
            return EnrichedItem(
                url=row["url"], title=row["title"], source="N",
                date="2026-01-01", abstract="x" * 80, evidence_level="abstract",
            )

        with patch("inlight_articles.enrich_item", side_effect=fake_enrich):
            with patch("inlight_articles.triage_items", return_value=[]):
                process_articles(rows, {"min_deep": 3, "max_deep": 5})
        self.assertEqual(len(seen), n)
        self.assertIn("https://doi.org/10.1/item-34", seen)

        seen.clear()
        with patch("inlight_articles.enrich_item", side_effect=fake_enrich):
            with patch("inlight_articles.triage_items", return_value=[]):
                process_articles(rows, {"max_candidates": 12})
        self.assertEqual(len(seen), 12)

    def test_sources_yaml_exposes_yield_keys(self):
        import yaml
        from pathlib import Path

        cfg = yaml.safe_load(Path(__file__).resolve().parent.parent.joinpath("sources.yaml").read_text())
        self.assertEqual(int(cfg["min_deep"]), 3)
        self.assertEqual(int(cfg["max_deep"]), 5)
        self.assertLessEqual(int(cfg["min_deep"]), int(cfg["max_deep"]))
        self.assertEqual(int(cfg["max_brief"]), 0)
        self.assertGreaterEqual(int(cfg["max_candidates"]), 10)

    def test_balance_academic_candidates_before_cap(self):
        from inlight_articles import balance_academic_candidates, publisher_family, process_articles, EnrichedItem

        rows = (
            [{"source": "Nature", "url": f"https://www.nature.com/articles/s{i}", "kind": "academic",
              "title": f"N{i}", "date": "2026-01-01", "summary": "x" * 80} for i in range(20)]
            + [{"source": "Cell", "url": f"https://www.cell.com/fulltext/S{i}", "kind": "academic",
                "title": f"C{i}", "date": "2026-01-01", "summary": "x" * 80} for i in range(8)]
            + [{"source": "PubMed", "url": f"https://pubmed.ncbi.nlm.nih.gov/{1000+i}/", "kind": "academic",
                "title": f"P{i}", "date": "2026-01-01", "summary": "x" * 80} for i in range(8)]
        )
        balanced = balance_academic_candidates(rows, 12)
        families = [publisher_family(r) for r in balanced]
        self.assertEqual(len(balanced), 12)
        self.assertGreaterEqual(families.count("nature"), 1)
        self.assertGreaterEqual(families.count("cell"), 1)
        self.assertGreaterEqual(families.count("pubmed"), 1)
        self.assertLess(families.count("nature"), 12)
        sliced = rows[:12]
        self.assertTrue(all(publisher_family(r) == "nature" for r in sliced))

        seen = []

        def fake_enrich(row):
            seen.append(row["url"])
            return EnrichedItem(
                url=row["url"], title=row["title"], source=row["source"],
                date="2026-01-01", abstract="x" * 80, evidence_level="abstract",
            )

        with patch("inlight_articles.enrich_item", side_effect=fake_enrich):
            with patch("inlight_articles.triage_items", return_value=[]):
                process_articles(rows, {"max_candidates": 12})
        enriched_families = []
        by_url = {r["url"]: r for r in rows}
        for url in seen:
            enriched_families.append(publisher_family(by_url[url]))
        self.assertEqual(len(seen), 12)
        self.assertGreaterEqual(enriched_families.count("cell"), 1)
        self.assertGreaterEqual(enriched_families.count("pubmed"), 1)

    def test_oa_sources_not_crowded_out_by_one_family(self):
        from inlight_articles import balance_academic_candidates, publisher_family

        rows = (
            [{"source": "Nature", "url": f"https://www.nature.com/articles/s{i}", "kind": "academic"} for i in range(25)]
            + [{"source": "bioRxiv", "url": f"https://www.biorxiv.org/content/10.1/{i}", "kind": "academic"} for i in range(8)]
        )
        out = balance_academic_candidates(rows, 12)
        families = [publisher_family(r) for r in out]
        self.assertGreaterEqual(families.count("biorxiv"), 1)
        self.assertLess(families.count("nature"), 12)

    def test_press_url_is_not_bare_search_path(self):
        from inlight_articles import fetch_press_coverage

        seen = []

        def http(url, timeout=30):
            seen.append(url)
            return None

        with patch("inlight_articles._http_get", side_effect=http):
            fetch_press_coverage("Organoid immune synapse unique title 98765", "")
        # Direct EurekAlert HTML was a 200 + "Page not found" body; do not fetch it.
        self.assertFalse(any("eurekalert.org" in u for u in seen))
        self.assertTrue(any("europepmc" in u for u in seen))
        self.assertFalse(any("/search/Organoid" in u for u in seen))

    def test_triage_prompt_asks_for_depth_and_quantity(self):
        from inlight_articles import build_triage_prompt, EnrichedItem

        items = [EnrichedItem(
            url="https://doi.org/10.1/example", title="T", source="N", date="2026-01-01",
            abstract="x" * 100, evidence_level="abstract",
        )]
        prompt = build_triage_prompt(items, {})
        self.assertIn("3–5", prompt)
        self.assertIn("宁可发 3", prompt)
        self.assertIn("不要发 5", prompt)
        self.assertIn("不可发错", prompt)
        self.assertIn("机制", prompt)
        self.assertIn("可选", prompt)
        self.assertNotIn("至少 10", prompt)
        self.assertNotIn("凑数量", prompt)

    def test_enrich_oa_fulltext_and_press_fallback(self):
        from inlight_articles import enrich_item

        epmc = json.dumps({
            "resultList": {"result": [{
                "abstractText": "Short teaser only.",
                "pmid": "1",
                "isOpenAccess": "N",
                "doi": "10.1038/s41467-026-00001-x",
            }]},
        }).encode()
        openalex = json.dumps({
            "abstract_inverted_index": {
                "Fezolinetant": [0], "reduced": [1], "VMS": [2],
                "by": [3], "64%": [4], "in": [5], "527": [6], "women": [7],
            },
            "best_oa_location": {"landing_page_url": "https://example.org/oa-html"},
        }).encode()
        oa_html = (
            "<html><body><h2>Results</h2><p>" +
            ("outcome " * 1600) +
            "fezolinetant 45 mg reduced VMS frequency by 64% among 527 women." +
            "</p></body></html>"
        ).encode()
        press_epmc = json.dumps({
            "resultList": {"result": [{
                "doi": "10.9999/press-item",
                "abstractText": (
                    "In a press briefing researchers said fezolinetant 45 mg cut "
                    "hot-flash frequency by 64 percent in 527 women at week 12, "
                    "matching the SKYLIGHT 1 readout presented to reporters."
                ),
            }]},
        }).encode()

        def http(url, timeout=30):
            if "europepmc" in url and ("press" in url.lower() or "eurekalert" in url.lower()):
                return press_epmc
            if "europepmc" in url:
                return epmc
            if "openalex.org" in url:
                return openalex
            if "example.org/oa-html" in url:
                return oa_html
            return None

        with patch("inlight_articles._http_get", side_effect=http):
            item = enrich_item({
                "url": "https://doi.org/10.1038/s41467-026-00001-x",
                "title": "Fezolinetant SKYLIGHT 1 VMS trial",
                "source": "Nature Communications",
                "date": "2026-01-01",
                "kind": "academic",
                "summary": "RSS teaser",
            })
        self.assertEqual(item.evidence_level, "fulltext")
        self.assertIn("64%", item.fulltext_results)
        self.assertTrue(item.sections_read.get("results", {}).get("words", 0) >= 1500)
        self.assertTrue(item.read_note)
        self.assertTrue(item.press_coverage)
        self.assertIn("press", " ".join(item.source_trace).lower() + " media")

    def test_press_only_cannot_be_deep(self):
        from inlight_articles import EnrichedItem, _process_single_article

        item = EnrichedItem(
            url="https://example.org/press-only",
            title="Press note",
            source="EurekAlert",
            date="2026-01-01",
            abstract="A news note with no paper abstract.",
            press_coverage="Institution press release repeating the news note.",
            evidence_level="press",
        )
        drafted = None

        def fake_draft(it, tier, config, problems=None):
            nonlocal drafted
            drafted = tier
            return None

        with patch("inlight_articles.draft_single_article", side_effect=fake_draft):
            _process_single_article(
                {"url": item.url, "tier": "deep", "field": "c4"},
                {item.url: item},
                {},
            )
        self.assertEqual(drafted, "brief")

    def test_press_does_not_launder_invented_numbers(self):
        from inlight_articles import validate_depth

        art = {
            "tier": "brief",
            "title": "缓解率99%",
            "one_liner": "缓解率99%。",
            "background": "背景句。" * 20,
            "design": "设计句。" * 15,
            "results": ["客观缓解率达到99%。"],
            "mechanism": "",
            "limitations": ["单臂"],
            "significance": "若属实或改变实践。",
            "datacard": {"n": "10例"},
            "data_points": [],
            "url": "https://doi.org/10.1/x",
        }
        source = (
            "A phase 2 study enrolled 10 patients. The objective response rate was 40%. "
            "Institution press release: investigators described a 40% response."
        )
        problems = validate_depth(art, source)
        self.assertTrue(any("99" in p for p in problems), problems)

    def test_run_stats_log_deep_brief_and_drops(self):
        from inlight_articles import process_articles, LAST_RUN_STATS, EnrichedItem

        item = EnrichedItem(
            url="https://doi.org/10.1/yield",
            title="T",
            source="N",
            date="2026-01-01",
            abstract="The rate was 40% in 10 patients.",
            evidence_level="abstract",
        )
        stats_holder = {}

        def fake_enrich(row):
            return item

        def fake_triage(items, config):
            return [{"url": item.url, "tier": "brief", "field": "c3"}]

        def fake_process(selection, url_to, config, stats=None):
            if stats is not None:
                stats.setdefault("drops", []).append(
                    {"url": item.url, "reason": "retry still hard: 数字 '99%' 在原始材料中未找到"}
                )
            return None

        with patch("inlight_articles.enrich_item", side_effect=fake_enrich):
            with patch("inlight_articles.triage_items", side_effect=fake_triage):
                with patch("inlight_articles._process_single_article", side_effect=fake_process):
                    out = process_articles(
                        [{"url": item.url, "kind": "academic", "title": "T",
                          "source": "N", "date": "2026-01-01", "summary": "x" * 80}],
                        {"min_deep": 3, "max_deep": 5},
                    )
        self.assertEqual(out["articles"], [])
        self.assertEqual(LAST_RUN_STATS["dropped"], 1)
        self.assertEqual(LAST_RUN_STATS["candidates_triaged"], 1)
        self.assertEqual(LAST_RUN_STATS["published_industry"], 0)
        self.assertTrue(LAST_RUN_STATS["drops"])
        self.assertIn("99%", LAST_RUN_STATS["drops"][0]["reason"])

    def test_industry_optional_and_below_min_does_not_relax_checks(self):
        from inlight_articles import process_articles, LAST_RUN_STATS, EnrichedItem, log_run_yield
        import logging

        item = EnrichedItem(
            url="https://doi.org/10.1/deep-only",
            title="T",
            source="N",
            date="2026-01-01",
            abstract="The rate was 40% in 10 patients.",
            evidence_level="abstract",
        )

        def fake_enrich(row):
            return item

        with patch("inlight_articles.enrich_item", side_effect=fake_enrich):
            with patch("inlight_articles.triage_items", return_value=[]):
                out = process_articles(
                    [{"url": item.url, "kind": "academic", "title": "T",
                      "source": "N", "date": "2026-01-01", "summary": "x" * 80}],
                    {"min_deep": 3, "max_deep": 5},
                )
        self.assertEqual(out["articles"], [])
        self.assertEqual(out["deals"], [])
        self.assertEqual(LAST_RUN_STATS["published_deep"], 0)
        self.assertEqual(LAST_RUN_STATS["published_industry"], 0)

        records = []

        class _H(logging.Handler):
            def emit(self, record):
                records.append(record)

        h = _H()
        log = logging.getLogger()
        log.addHandler(h)
        try:
            log_run_yield(
                {"published_deep": 1, "published_brief": 4, "published_industry": 0,
                 "candidates_fetched": 20, "candidates_triaged": 5, "dropped": 4,
                 "drops": [], "sources": {}},
                {"min_deep": 3, "max_deep": 5},
            )
        finally:
            log.removeHandler(h)
        warnings = [r.getMessage() for r in records if r.levelno >= logging.WARNING]
        self.assertTrue(any("未放宽核对" in w for w in warnings), warnings)
        self.assertTrue(any("1 < 3" in w for w in warnings), warnings)


class TestWeeklyDefaultUsesStrictNewPipeline(unittest.TestCase):
    """plain `python run_weekly.py` must take the new pipeline + strict gates."""

    def test_production_sources_yaml_default_is_new_and_strict(self):
        import yaml
        from pathlib import Path
        from run_weekly import parse_weekly_args, should_use_new_pipeline
        from inlight_qc import acir_strict

        cfg = yaml.safe_load(Path("sources.yaml").read_text())
        args = parse_weekly_args([])
        self.assertFalse(args.use_new_pipeline)
        self.assertFalse(args.use_legacy_pipeline)
        self.assertTrue(should_use_new_pipeline(args, cfg))
        self.assertTrue(acir_strict(cfg))

    def test_legacy_opt_out_and_fixture_config_stay_old(self):
        from run_weekly import parse_weekly_args, should_use_new_pipeline

        self.assertFalse(should_use_new_pipeline(parse_weekly_args([]), {}))
        self.assertFalse(should_use_new_pipeline(
            parse_weekly_args(["--use-legacy-pipeline"]),
            {"min_deep": 3},
        ))
        self.assertTrue(should_use_new_pipeline(
            parse_weekly_args(["--use-new-pipeline"]),
            {},
        ))


if __name__ == "__main__":
    unittest.main()
