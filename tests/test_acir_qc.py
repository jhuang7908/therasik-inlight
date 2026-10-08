#!/usr/bin/env python3
"""ACIR full-text admission, structure, chart, QC gate, and Gemini review."""

from __future__ import annotations

import json
import logging
import os
import re
import tempfile
import unittest
from unittest.mock import patch

from inlight_articles import EnrichedItem, _process_single_article, process_articles, LAST_RUN_STATS
from inlight_qc import (
    FIG_DISCLAIMER,
    GEMINI_SCORE_KEYS,
    comparable_verified_points,
    extract_results_from_html,
    gemini_review_deep,
    is_real_results_text,
    item_has_real_fulltext,
    load_gemini_api_key,
    mechanism_image_prompt,
    record_fulltext,
    render_data_chart_svg,
    requires_primary_endpoint_result,
    secondhand_label,
    validate_acir_structure,
)


def _results(n=1600, extra=" The objective response rate was 64% among 527 women."):
    return ("outcome " * n) + extra


def _deep_art(**overrides):
    def pad(text, n, cap=150):
        han = len([c for c in text if "\u4e00" <= c <= "\u9fff"])
        need = max(0, n - han)
        chunks = []
        while need > 0:
            take = min(cap - 1, need)
            chunks.append("测" * take + "。")
            need -= take
        return text + "".join(chunks)

    art = {
        "url": "https://doi.org/10.1/ft",
        "tier": "deep",
        "field": "c3",
        "title": pad("替雷利珠单抗使缓解率达64%", 20),
        "one_liner": pad("替雷利珠单抗在五二七例队列中把缓解率做到百分之六四。", 40),
        "background": pad("现有方案卡在缓解不足与毒性。", 180),
        "design": pad("这是一项随机对照三期试验，纳入五二七例。", 200),
        "results": [
            pad("主要终点客观缓解率为64%，对照为32%，风险比0.50。", 140),
            pad("527例可评估，中位随访18个月。", 140),
            pad("三级以上不良事件发生率为21%。", 140),
            pad("疾病控制率达到80%。", 140),
        ],
        "mechanism": pad("原文实验证明通路被阻断，作者推测或可外推。", 250),
        "limitations": [
            pad("单中心外推有限。", 70),
            pad("随访偏短，终点为替代指标。", 70),
            pad("未报告总生存期检验。", 70),
        ],
        "significance": pad("若后续验证，或改变该人群的一线选择。", 150),
        "citation": pad("读了 PMC 全文 PMC999 的 Results。DOI 10.1/ft。", 80),
        "datacard": {
            "study_type": "III期随机对照",
            "n": "527例",
            "control": "标准治疗",
            "intervention": "tislelizumab 200 mg",
            "followup": "18个月",
            "primary_endpoint": "ORR",
            "primary_endpoint_result": "64% vs 32%",
            "statistics": "HR 0.50",
            "safety": "≥3级AE 21%",
        },
        "data_points": [
            {"value": "64%", "meaning": "缓解率", "source_quote": "objective response rate was 64%"},
            {"value": "32%", "meaning": "对照缓解率", "source_quote": "control response rate was 32%"},
            {"value": "527", "meaning": "例数", "source_quote": "among 527 women"},
            {"value": "21%", "meaning": "AE", "source_quote": "Grade 3+ adverse events 21%"},
            {"value": "18", "meaning": "随访月", "source_quote": "median follow-up 18 months"},
            {"value": "0.50", "meaning": "HR", "source_quote": "hazard ratio 0.50"},
        ],
        "evidence_level": "fulltext",
        "steps": ["入组", "给药", "评估", "随访"],
    }
    art.update(overrides)
    return art


class TestFulltextAdmission(unittest.TestCase):
    def test_abstract_only_cannot_be_deep_or_get_figure(self):
        item = EnrichedItem(
            url="https://doi.org/10.1/abs",
            title="T",
            source="N",
            date="2026-01-01",
            abstract="A long abstract. " * 80,
            evidence_level="abstract",
        )
        self.assertFalse(item_has_real_fulltext(item))
        drafted = []

        def fake_draft(it, tier, config, problems=None):
            drafted.append(tier)
            return None

        with patch("inlight_articles.draft_single_article", side_effect=fake_draft):
            out = _process_single_article(
                {"url": item.url, "tier": "deep", "field": "c3"},
                {item.url: item},
                {"min_deep": 3, "max_deep": 5, "max_brief": 0},
            )
        self.assertIsNone(out)
        self.assertEqual(drafted, [])

    def test_press_only_labeled_or_dropped(self):
        item = EnrichedItem(
            url="https://example.org/press",
            title="T",
            source="EurekAlert",
            date="2026-01-01",
            abstract="press note",
            press_coverage="institution press release",
            evidence_level="press",
        )
        self.assertIn("新闻稿", secondhand_label(item))
        stats = {"drops": [], "qc_report": {"articles": []}}
        out = _process_single_article(
            {"url": item.url, "tier": "deep", "field": "c3"},
            {item.url: item},
            {"min_deep": 3, "max_brief": 0},
            stats=stats,
        )
        self.assertIsNone(out)
        self.assertTrue(stats["drops"])

    def test_fulltext_records_pmcid_and_sections(self):
        item = EnrichedItem(
            url="https://doi.org/10.1/pmc",
            title="T", source="N", date="2026-01-01", pmcid="PMC9990001",
        )
        ok = record_fulltext(
            item, _results(), methods="randomized double-blind cohort of 527 women",
            figs="Figure 1. waterfall of response.", source_label="PMC PMC9990001",
        )
        self.assertTrue(ok)
        self.assertEqual(item.evidence_level, "fulltext")
        self.assertGreaterEqual(item.sections_read["results"]["words"], 1500)
        self.assertIn("PMC9990001", item.read_note)
        self.assertIn("Results", item.read_note)

    def test_fake_fulltext_landing_or_stub_rejected(self):
        self.assertFalse(is_real_results_text("Subscribe to read the full text of this article. Buy this article."))
        item = EnrichedItem(url="u", title="T", source="N", date="2026-01-01")
        self.assertFalse(record_fulltext(item, "Short stub Results only.", source_label="landing"))
        self.assertNotEqual(item.evidence_level, "fulltext")
        self.assertTrue(any("rejected" in t for t in item.source_trace))

    def test_landing_news_and_paywall_pages_rejected(self):
        from inlight_qc import extract_results_from_xml, looks_like_whole_page

        refs = " ".join(f"Smith et al. ({1990 + i}) Nature {i}." for i in range(40))
        landing = (
            "<html><nav>Home Journal</nav><h1>News Feature</h1>"
            "<p>Authors and affiliations. Download PDF to view the full article.</p>"
            f"<h2>References</h2><p>{refs}</p></html>"
        )
        self.assertEqual(extract_results_from_html(landing), "")
        self.assertFalse(is_real_results_text(re.sub(r"<[^>]+>", " ", landing)))
        paywall = "<html><p>Subscribe to read. This article is available to subscribers. Get access.</p></html>"
        self.assertEqual(extract_results_from_html(paywall), "")
        biorxiv_landing = (
            "<html><body><nav>bioRxiv</nav>"
            "<p>The copyright holder for this preprint is the author/funder.</p>"
            "<h2>Abstract</h2><p>" + ("background method finding " * 400) + "</p>"
            "<h2>Authors and affiliations</h2><p>Jane Doe University</p>"
            "<p>Download PDF Full Text HTML Subject Area Immunology</p>"
            f"<h2>References</h2><p>{refs}</p></body></html>"
        )
        self.assertTrue(looks_like_whole_page(biorxiv_landing))
        self.assertEqual(extract_results_from_html(biorxiv_landing), "")
        self.assertFalse(is_real_results_text(biorxiv_landing))
        self.assertFalse(record_fulltext(
            EnrichedItem(url="u", title="T", source="bioRxiv", date="2026-01-01"),
            biorxiv_landing, source_label="bioRxiv landing",
        ))
        real = (
            "<html><h2><span>Results</span></h2><p>" + ("outcome " * 1600) +
            "response rate was 64% among 527 women.</p><h2>Discussion</h2><p>ok</p></html>"
        )
        extracted = extract_results_from_html(real)
        self.assertIn("64%", extracted)
        self.assertNotIn("Discussion", extracted)
        self.assertTrue(is_real_results_text(extracted))
        xml = (
            '<article><sec sec-type="results"><title>Results</title><p>'
            + ("measured response " * 1600) +
            "64% of 527 women.</p></sec>"
            '<sec sec-type="discussion"><title>Discussion</title><p>ok</p></sec></article>'
        )
        xml_results = extract_results_from_xml(xml)
        self.assertIn("64%", xml_results)
        self.assertNotIn("Discussion", xml_results)
        self.assertTrue(is_real_results_text(xml_results))

    def test_no_mechanism_figure_without_fulltext(self):
        prompt = mechanism_image_prompt(_results(), "pathway blocked")
        self.assertIn("#0F6B5C", prompt)
        self.assertIn("#C0492F", prompt)
        self.assertIn("Subject:", prompt)
        self.assertIn("pathway", prompt.lower())
        self.assertNotIn("outcome outcome", prompt.lower())
        item = EnrichedItem(
            url="https://doi.org/10.1/abs2", title="T", source="N", date="2026-01-01",
            abstract="x" * 2000, evidence_level="abstract",
        )
        with patch("inlight_articles.draft_single_article", return_value=None):
            out = _process_single_article(
                {"url": item.url, "tier": "brief", "field": "c3"},
                {item.url: item},
                {},
            )
        self.assertIsNone(out)


class TestStructureAndChart(unittest.TestCase):
    def test_primary_endpoint_result_only_for_clinical(self):
        atlas = _deep_art()
        atlas["datacard"] = {
            "study_type": "描述性图谱",
            "n": "12例组织",
            "control": "无",
            "intervention": "单细胞表征",
            "followup": "无",
            "primary_endpoint": "细胞分群完整性",
            "statistics": "无",
            "safety": "无",
        }
        self.assertFalse(requires_primary_endpoint_result(atlas))
        atlas_probs = validate_acir_structure(atlas)
        self.assertFalse(
            any("primary_endpoint_result" in p for p in atlas_probs),
            atlas_probs,
        )
        clinical = _deep_art()
        dc = dict(clinical["datacard"])
        dc.pop("primary_endpoint_result", None)
        clinical["datacard"] = dc
        self.assertTrue(requires_primary_endpoint_result(clinical))
        self.assertTrue(
            any("primary_endpoint_result" in p for p in validate_acir_structure(clinical))
        )
        nonclin = _deep_art()
        nonclin["datacard"] = {
            "study_type": "非临床对照实验",
            "n": "12只小鼠",
            "control": "溶剂对照",
            "intervention": "体外给药",
            "followup": "无",
            "primary_endpoint": "通路激活",
            "statistics": "无",
            "safety": "无",
        }
        self.assertFalse(requires_primary_endpoint_result(nonclin))
        self.assertFalse(
            any("primary_endpoint_result" in p for p in validate_acir_structure(nonclin))
        )

    def test_word_and_paragraph_limits(self):
        art = _deep_art()
        self.assertEqual(validate_acir_structure(art), [])
        bad = _deep_art(background="短")
        self.assertTrue(any("background" in p for p in validate_acir_structure(bad)))
        long_para = _deep_art(results=["测" * 160 + "率64%。", "测" * 40 + "例527。", "测" * 40 + "率21%。"])
        self.assertTrue(any("150" in p for p in validate_acir_structure(long_para)))

    def test_chart_only_from_verified_values(self):
        source = "objective response rate was 64% and control response rate was 32% among 527 women"
        art = {
            "data_points": [
                {"value": "64%", "meaning": "缓解率", "source_quote": "objective response rate was 64%"},
                {"value": "32%", "meaning": "对照缓解率", "source_quote": "control response rate was 32%"},
                {"value": "99%", "meaning": "编造", "source_quote": "invented 99% not in source"},
            ]
        }
        pts = comparable_verified_points(art, source)
        self.assertEqual(len(pts), 2)
        svg = render_data_chart_svg(pts)
        self.assertIn("<svg", svg)
        self.assertIn("64", svg)
        self.assertNotIn("99", svg)
        self.assertEqual(comparable_verified_points({"data_points": art["data_points"][:1]}, source), [])


class TestQcGateAndGemini(unittest.TestCase):
    def test_qc_gate_blocks_publication_on_bad_number(self):
        item = EnrichedItem(
            url="https://doi.org/10.1/ft",
            title="T", source="N", date="2026-01-01",
            fulltext_results=_results(),
            evidence_level="fulltext",
            pmcid="PMC1",
            read_note="读了 PMC 全文 PMC1 的 Results",
            sections_read={"results": {"words": 1600, "chars": 9000}},
        )
        record_fulltext(item, item.fulltext_results, source_label="PMC PMC1")
        art = _deep_art(results=["客观缓解率达到99%。", "随访十八个月。", "不良事件21%。"])
        stats = {"drops": [], "qc_report": {"articles": []}}

        def fake_draft(it, tier, config, problems=None):
            return dict(art)

        with patch("inlight_articles.draft_single_article", side_effect=fake_draft):
            with patch.dict(os.environ, {"GEMINI_API_KEY": "test-gemini"}):
                with patch("inlight_qc.gemini_review_deep", return_value={
                    "pass": True, "scores": {k: 8 for k in GEMINI_SCORE_KEYS},
                    "reasons": "ok", "factual_mismatch": False,
                }):
                    out = _process_single_article(
                        {"url": item.url, "tier": "deep", "field": "c3"},
                        {item.url: item},
                        {"min_deep": 3, "max_deep": 5, "max_brief": 0, "acir_qc": True},
                        stats=stats,
                    )
        self.assertIsNone(out)
        self.assertTrue(any("99" in d.get("reason", "") or "数字" in d.get("reason", "") or "hard" in d.get("reason", "")
                            for d in stats["drops"]))

    def test_gemini_pass(self):
        payload = json.dumps({
            "scores": {k: 8 for k in GEMINI_SCORE_KEYS},
            "factual_mismatch": False,
            "reasons": "aligned",
        })
        with patch.dict(os.environ, {"GEMINI_API_KEY": "test-gemini"}):
            with patch("inlight_qc._gemini_generate", return_value=payload):
                r = gemini_review_deep(_deep_art(), _results(), {"gemini_model": "gemini-2.5-flash"})
        self.assertTrue(r["pass"])
        self.assertEqual(r["scores"]["depth"], 8)

    def test_gemini_fail(self):
        payload = json.dumps({
            "scores": {k: 6 for k in GEMINI_SCORE_KEYS},
            "factual_mismatch": False,
            "reasons": "thin",
        })
        with patch.dict(os.environ, {"GEMINI_API_KEY": "test-gemini"}):
            with patch("inlight_qc._gemini_generate", return_value=payload):
                r = gemini_review_deep(_deep_art(), _results(), {})
        self.assertFalse(r["pass"])

    def test_gemini_revise_then_pass(self):
        item = EnrichedItem(
            url="https://doi.org/10.1/ft2", title="T", source="N", date="2026-01-01",
            pmcid="PMC2",
        )
        record_fulltext(item, _results() + " objective response rate was 64% control response rate was 32% among 527 women hazard ratio 0.50 Grade 3+ adverse events 21% median follow-up 18 months",
                        source_label="PMC PMC2")
        art = _deep_art()
        art["results"] = [
            "主要终点客观缓解率为64%，对照为32%，风险比0.50。",
            "五二七例可评估，中位随访十八个月。",
            "三级以上不良事件发生率为21%。",
        ]
        # Relax structure so the Gemini path is what we test.
        calls = {"n": 0}

        def fake_gemini(a, ft, config):
            calls["n"] += 1
            if calls["n"] == 1:
                return {"pass": False, "scores": {k: 6 for k in GEMINI_SCORE_KEYS},
                        "reasons": "low depth", "factual_mismatch": False}
            return {"pass": True, "scores": {k: 8 for k in GEMINI_SCORE_KEYS},
                    "reasons": "ok", "factual_mismatch": False}

        def fake_draft(it, tier, config, problems=None):
            return dict(art)

        with patch.dict(os.environ, {"GEMINI_API_KEY": "test-gemini"}):
            with patch("inlight_qc.gemini_review_deep", side_effect=fake_gemini):
                with patch("inlight_articles.draft_single_article", side_effect=fake_draft):
                    with patch("inlight_articles.validate_depth", return_value=[]):
                        with patch("inlight_articles.validate_names", return_value=[]):
                            with patch("inlight_qc.validate_acir_structure", return_value=[]):
                                with patch("inlight_articles.verify_article_claims", return_value={
                                    "status": "ok", "problems": [], "calls": 1,
                                    "input_tokens": 10, "output_tokens": 10,
                                }):
                                    out = _process_single_article(
                                        {"url": item.url, "tier": "deep", "field": "c3"},
                                        {item.url: item},
                                        {"min_deep": 3, "acir_qc": True},
                                    )
        self.assertIsNotNone(out)
        self.assertEqual(calls["n"], 2)
        self.assertTrue(out.get("gemini_review", {}).get("pass"))
        self.assertIn(FIG_DISCLAIMER, out.get("fig_caption", ""))
        self.assertFalse(out.get("skip_mechanism_figure"))

    def test_gemini_missing_key_fail_closed(self):
        env = {k: v for k, v in os.environ.items()
               if k not in ("GEMINI_API_KEY", "INLIGHT_EXTRA_ENV_FILE")}
        with patch.dict(os.environ, env, clear=True):
            r = gemini_review_deep(_deep_art(), _results(), {"min_deep": 3})
            self.assertFalse(r["pass"])
            self.assertIn("GEMINI_API_KEY", r["reasons"])
            item = EnrichedItem(url="https://doi.org/10.1/ft3", title="T", source="N", date="2026-01-01")
            record_fulltext(item, _results(), source_label="PMC x")
            stats = {"drops": [], "qc_report": {"articles": []}}
            out = _process_single_article(
                {"url": item.url, "tier": "deep", "field": "c3"},
                {item.url: item},
                {"min_deep": 3, "max_brief": 0},
                stats=stats,
            )
            self.assertIsNone(out)
            self.assertTrue(any("GEMINI_API_KEY" in d["reason"] for d in stats["drops"]))

    def test_extra_env_file_loads_only_gemini_key(self):
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "other.env")
            with open(path, "w", encoding="utf-8") as fh:
                fh.write("OPENAI_API_KEY=should-not-load\nGEMINI_API_KEY=from-file-secret\n")
            env = {k: v for k, v in os.environ.items()
                   if k not in ("GEMINI_API_KEY", "INLIGHT_EXTRA_ENV_FILE")}
            records = []

            class _H(logging.Handler):
                def emit(self, record):
                    records.append(record.getMessage())

            h = _H()
            log = logging.getLogger()
            log.addHandler(h)
            try:
                with patch.dict(os.environ, env, clear=True):
                    key = load_gemini_api_key({"extra_env_file": path})
                    self.assertEqual(key, "from-file-secret")
                    self.assertNotEqual(os.environ.get("OPENAI_API_KEY"), "should-not-load")
            finally:
                log.removeHandler(h)
            joined = " ".join(records)
            self.assertNotIn("from-file-secret", joined)
            self.assertNotIn("should-not-load", joined)


    def test_claims_qc_uses_audit_not_constant_pass(self):
        from pathlib import Path
        src = Path(__file__).resolve().parent.parent.joinpath("inlight_articles.py").read_text()
        self.assertNotIn('empty_check("claims", True', src)
        self.assertIn('empty_check("claims", claim_ok', src)

    def test_length_only_after_targeted_retry_stays_deep(self):
        item = EnrichedItem(
            url="https://doi.org/10.1/ft-len",
            title="T", source="N", date="2026-01-01", pmcid="PMC3",
        )
        record_fulltext(item, _results(), source_label="PMC PMC3")
        art = _deep_art()
        drafts = []
        vd_n = {"n": 0}

        def vd(draft, src, **kwargs):
            vd_n["n"] += 1
            if vd_n["n"] == 1:
                return [
                    "标识符 'ZZ9' 在原始材料中未找到",
                    "background 字数 80，要求 180–240",
                ]
            if vd_n["n"] == 2:
                return ["background 字数 80，要求 180–240"]
            return []

        def fake_draft(it, tier, config, problems=None):
            drafts.append((tier, problems))
            out = dict(art)
            out["tier"] = tier
            return out

        with patch.dict(os.environ, {"GEMINI_API_KEY": "test-gemini"}):
            with patch("inlight_qc.gemini_review_deep", return_value={
                "pass": True, "scores": {k: 8 for k in GEMINI_SCORE_KEYS},
                "reasons": "ok", "factual_mismatch": False,
            }):
                with patch("inlight_qc.verified_data_points", return_value=[{}] * 6):
                    with patch("inlight_articles.validate_depth", side_effect=vd):
                        with patch("inlight_articles.validate_names", return_value=[]):
                            with patch("inlight_qc.validate_acir_structure", return_value=[]):
                                with patch("inlight_articles.draft_single_article", side_effect=fake_draft):
                                    with patch("inlight_articles.verify_article_claims", return_value={
                                        "status": "ok", "problems": [], "calls": 1,
                                        "input_tokens": 1, "output_tokens": 1,
                                    }):
                                        out = _process_single_article(
                                            {"url": item.url, "tier": "deep", "field": "c3"},
                                            {item.url: item},
                                            {"min_deep": 3, "max_brief": 2, "acir_qc": True},
                                        )
        self.assertIsNotNone(out)
        self.assertEqual(out["tier"], "deep")
        self.assertTrue(all(tier == "deep" for tier, _ in drafts))
        self.assertGreaterEqual(len(drafts), 3)
        self.assertTrue(any(
            probs and any("必须压缩" in str(p) or "必须扩写" in str(p) or "实测" in str(p) for p in (probs or []))
            for _, probs in drafts
        ))

    def test_length_redraft_content_gets_one_deep_fix(self):
        item = EnrichedItem(
            url="https://doi.org/10.1/ft-len-fix",
            title="T", source="N", date="2026-01-01", pmcid="PMC4",
        )
        record_fulltext(item, _results(), source_label="PMC PMC4")
        art = _deep_art()
        drafts = []
        vd_n = {"n": 0}

        def vd(draft, src, **kwargs):
            vd_n["n"] += 1
            if vd_n["n"] == 1:
                return ["标识符 'ZZ9' 在原始材料中未找到", "background 字数 80，要求 180–240"]
            if vd_n["n"] == 2:
                return ["background 字数 80，要求 180–240"]
            if vd_n["n"] == 3:
                return ["药物名 'inventedmab' 在原始材料中未找到"]
            return []

        def fake_draft(it, tier, config, problems=None):
            drafts.append((tier, problems))
            out = dict(art)
            out["tier"] = tier
            return out

        with patch.dict(os.environ, {"GEMINI_API_KEY": "test-gemini"}):
            with patch("inlight_qc.gemini_review_deep", return_value={
                "pass": True, "scores": {k: 8 for k in GEMINI_SCORE_KEYS},
                "reasons": "ok", "factual_mismatch": False,
            }):
                with patch("inlight_qc.verified_data_points", return_value=[{}] * 6):
                    with patch("inlight_articles.validate_depth", side_effect=vd):
                        with patch("inlight_articles.validate_names", return_value=[]):
                            with patch("inlight_qc.validate_acir_structure", return_value=[]):
                                with patch("inlight_articles.draft_single_article", side_effect=fake_draft):
                                    with patch("inlight_articles.verify_article_claims", return_value={
                                        "status": "ok", "problems": [], "calls": 1,
                                        "input_tokens": 1, "output_tokens": 1,
                                    }):
                                        out = _process_single_article(
                                            {"url": item.url, "tier": "deep", "field": "c3"},
                                            {item.url: item},
                                            {"min_deep": 3, "max_brief": 2, "acir_qc": True},
                                        )
        self.assertIsNotNone(out)
        self.assertEqual(out["tier"], "deep")
        self.assertTrue(all(tier == "deep" for tier, _ in drafts))
        self.assertFalse(any(tier == "brief" for tier, _ in drafts))

    def test_brief_gets_one_length_redraft_before_drop(self):
        item = EnrichedItem(
            url="https://doi.org/10.1/brief-len",
            title="T", source="N", date="2026-01-01",
            abstract="x" * 400, evidence_level="abstract",
        )
        art = _deep_art()
        art["tier"] = "brief"
        drafts = []
        vd_n = {"n": 0}

        def vd(draft, src, **kwargs):
            vd_n["n"] += 1
            if vd_n["n"] <= 2:
                return ["background 字数 80，要求 180–240"]
            return []

        def fake_draft(it, tier, config, problems=None):
            drafts.append((tier, problems))
            out = dict(art)
            out["tier"] = tier
            return out

        with patch("inlight_articles.validate_depth", side_effect=vd):
            with patch("inlight_articles.validate_names", return_value=[]):
                with patch("inlight_articles.draft_single_article", side_effect=fake_draft):
                    with patch("inlight_articles.verify_article_claims", return_value={
                        "status": "ok", "problems": [], "calls": 1,
                        "input_tokens": 1, "output_tokens": 1,
                    }):
                        out = _process_single_article(
                            {"url": item.url, "tier": "brief", "field": "c3"},
                            {item.url: item},
                            {"min_deep": 3, "max_brief": 2, "acir_qc": True},
                        )
        self.assertIsNotNone(out)
        self.assertEqual(out["tier"], "brief")
        self.assertGreaterEqual(len(drafts), 3)
        self.assertTrue(any(
            probs and any("必须扩写" in str(p) or "实测" in str(p) for p in (probs or []))
            for _, probs in drafts
        ))

    def test_section_band_slack_logged_when_body_in_range(self):
        from inlight_articles import _apply_section_band_slack
        from inlight_qc import han_len

        art = _deep_art()
        body = han_len([
            art.get("one_liner"), art.get("background"), art.get("design"),
            art.get("results"), art.get("mechanism"), art.get("limitations"),
            art.get("significance"),
        ])
        self.assertTrue(1400 <= body <= 1900, body)
        kept, overs = _apply_section_band_slack(
            art, ["results 字数 760，要求 500–700", "核心结果须为 3–5 段"],
        )
        self.assertIn("核心结果须为 3–5 段", kept)
        self.assertTrue(any(o.get("section") == "results" for o in overs), overs)
        far, far_overs = _apply_section_band_slack(
            art, ["results 字数 1200，要求 500–700"],
        )
        self.assertEqual(far, ["results 字数 1200，要求 500–700"])
        self.assertFalse(far_overs)
        title_kept, title_overs = _apply_section_band_slack(
            art, ["title 字数 15，要求 20–40"],
        )
        self.assertEqual(title_kept, ["title 字数 15，要求 20–40"])
        self.assertFalse(title_overs)
        no_floor, no_floor_overs = _apply_section_band_slack(
            art, ["one_liner 字数 28，要求 40–70"],
        )
        self.assertEqual(no_floor, ["one_liner 字数 28，要求 40–70"])
        self.assertFalse(no_floor_overs)

        item = EnrichedItem(
            url="https://doi.org/10.1/ft-over",
            title="T", source="N", date="2026-01-01", pmcid="PMC8",
        )
        record_fulltext(item, _results(), source_label="PMC PMC8")
        stats = {"drops": [], "qc_report": {"articles": []}}

        def fake_draft(it, tier, config, problems=None):
            return dict(_deep_art())

        with patch.dict(os.environ, {"GEMINI_API_KEY": "test-gemini"}):
            with patch("inlight_qc.gemini_review_deep", return_value={
                "pass": True, "scores": {k: 8 for k in GEMINI_SCORE_KEYS},
                "reasons": "ok", "factual_mismatch": False,
            }):
                with patch("inlight_qc.verified_data_points", return_value=[{}] * 6):
                    with patch("inlight_articles.validate_depth", return_value=[]):
                        with patch("inlight_articles.validate_names", return_value=[]):
                            with patch(
                                "inlight_qc.validate_acir_structure",
                                return_value=["results 字数 760，要求 500–700"],
                            ):
                                with patch("inlight_articles.verify_article_claims", return_value={
                                    "status": "contradicted", "problems": ["主张与原文矛盾"],
                                    "calls": 1, "input_tokens": 1, "output_tokens": 1,
                                }):
                                    with patch("inlight_articles.draft_single_article", side_effect=fake_draft):
                                        out = _process_single_article(
                                            {"url": item.url, "tier": "deep", "field": "c3"},
                                            {item.url: item},
                                            {"min_deep": 3, "max_brief": 2, "acir_qc": True},
                                            stats=stats,
                                        )
        self.assertIsNone(out)
        self.assertTrue(stats["drops"])
        self.assertTrue(any(d.get("section_overages") for d in stats["drops"]), stats["drops"])

    def test_out_of_scope_field_is_dropped(self):
        item = EnrichedItem(
            url="https://doi.org/10.1/none", title="T", source="N", date="2026-01-01",
            abstract="x" * 200, evidence_level="abstract",
        )
        stats = {"drops": [], "qc_report": {"articles": []}}
        out = _process_single_article(
            {"url": item.url, "tier": "brief", "field": "none"},
            {item.url: item},
            {"min_deep": 3, "max_brief": 2},
            stats=stats,
        )
        self.assertIsNone(out)
        self.assertTrue(any("out-of-scope" in d["reason"] for d in stats["drops"]))

    def test_production_fields_match_pr8(self):
        from inlight_fields import FIELDS, FIELD_PROMPT_RULES
        from inlight_articles import _triage_field_map, _triage_field_rules

        self.assertEqual(list(FIELDS), [f"f{i}" for i in range(1, 10)])
        self.assertEqual(FIELDS["f1"], "类器官")
        self.assertEqual(FIELDS["f2"], "动物模型")
        self.assertEqual(FIELDS["f9"], "精准肿瘤与临床转化")
        rules = _triage_field_rules({"min_deep": 3})
        self.assertIn("肿瘤类器官", rules)
        self.assertIn("f2", rules)
        self.assertIn("none", rules)
        self.assertEqual(_triage_field_map({"min_deep": 3}), dict(FIELDS))
        self.assertIn("类器官", FIELD_PROMPT_RULES)


class TestTriageBackfillAndPublishedQc(unittest.TestCase):
    def test_triage_backfills_fulltext_to_twice_min_deep(self):
        from inlight_articles import triage_items
        from tests.test_e2e import make_triage_response

        def _ft(url, title, extra):
            item = EnrichedItem(
                url=url, title=title, source="N", date="2026-01-01", pmcid="PMC1",
            )
            record_fulltext(item, _results(extra=extra), source_label="PMC PMC1")
            return item

        items = [
            _ft("https://doi.org/10.1/org", "Organoid culture", " human organoid disease modeling "),
            _ft("https://doi.org/10.1/car", "CAR-T checkpoint", " CAR-T checkpoint PD-1 tumor immunology "),
            _ft("https://doi.org/10.1/vax", "Vaccine adjuvant", " vaccine adjuvant infection immunity "),
            _ft("https://doi.org/10.1/lnp", "LNP siRNA", " siRNA ASO LNP gene therapy "),
            EnrichedItem(
                url="https://doi.org/10.1/abs",
                title="Abstract only", source="N", date="2026-01-01",
                abstract="x" * 200, evidence_level="abstract",
            ),
            _ft(
                "https://doi.org/10.1/until",
                "Treated until this reason",
                " Patients were treated until progression for this reason. ",
            ),
        ]
        records = []

        class _H(logging.Handler):
            def emit(self, record):
                records.append(record.getMessage())

        h = _H()
        log = logging.getLogger()
        prev = log.level
        log.setLevel(logging.INFO)
        log.addHandler(h)
        try:
            with patch("anthropic.Anthropic") as mock_cls:
                mock_client = mock_cls.return_value
                mock_client.messages.create.side_effect = [
                    make_triage_response([
                        {"url": items[0].url, "tier": "deep", "field": "f1"},
                        {"url": items[1].url, "tier": "deep", "field": "f4"},
                        {"url": items[2].url, "tier": "deep", "field": "f6"},
                    ]),
                    make_triage_response([
                        {"url": items[3].url, "tier": "deep", "field": "f8"},
                        {"url": items[5].url, "tier": "deep", "field": "none"},
                    ]),
                ]
                out = triage_items(items, {"min_deep": 3, "max_deep": 5, "acir_qc": True})
        finally:
            log.removeHandler(h)
            log.setLevel(prev)

        deep_urls = [s["url"] for s in out if s.get("tier") == "deep"]
        self.assertGreaterEqual(len(deep_urls), 3)
        self.assertEqual(len(deep_urls), 4)
        self.assertNotIn(items[4].url, deep_urls)
        self.assertNotIn(items[5].url, deep_urls)
        self.assertTrue(any("backfill" in str(s.get("reason") or "") for s in out))
        self.assertIn(items[3].url, deep_urls)
        skip_txt = "\n".join(records)
        self.assertIn("no legally accessible full text", skip_txt)
        self.assertIn("Triage backfill", skip_txt)
        self.assertTrue(
            any("pool exhausted" in m or "try cap" in m for m in records),
            skip_txt,
        )

    def test_published_qc_has_must_cover_blind_removed(self):
        from inlight_audit import apply_dual_blind_to_week, BLIND_DIMS

        item = EnrichedItem(
            url="https://doi.org/10.1/qc-pub",
            title="T", source="N", date="2026-01-01", pmcid="PMC9",
        )
        source = (
            _results()
            + " objective response rate was 64% control response rate was 32% "
            "among 527 women hazard ratio 0.50 Grade 3+ adverse events 21% "
            "median follow-up 18 months. tislelizumab 200 mg. DCR 80%."
        )
        record_fulltext(item, source, source_label="PMC PMC9")
        art = _deep_art(url=item.url)
        stats = {"drops": [], "qc_report": {"articles": []}, "published_deep": 0}

        def fake_draft(it, tier, config, problems=None):
            return dict(art)

        def ok_judge(*_a, **_k):
            return {
                "available": True, "pass": True, "judge": "claude",
                "scores": {k: 8 for k in BLIND_DIMS},
                "overall": 8, "reasons": "ok cited ORR 64% in Results",
            }

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
                                    stats=stats,
                                )
        self.assertIsNotNone(out)
        apply_dual_blind_to_week(
            [out], stats, {"min_deep": 3, "acir_qc": True},
            {item.url: item},
            score_claude=ok_judge, score_gemini=ok_judge, redraft=None,
        )
        entry = stats["qc_report"]["articles"][0]
        self.assertTrue(entry.get("published"))
        self.assertIn("must_cover_list_id", entry)
        self.assertTrue(entry.get("must_cover_text_hash"))
        self.assertIn("must_cover_item_count", entry)
        self.assertIn("must_cover_coverage", entry)
        self.assertIn("removed_numbers", entry)
        self.assertIn("section_overages", entry)
        runs = entry.get("blind_judge_runs") or []
        self.assertTrue(runs or entry.get("blind_scores"))


class TestQcReportWritten(unittest.TestCase):
    def test_process_articles_writes_qc_entries(self):
        item = EnrichedItem(
            url="https://doi.org/10.1/drop", title="T", source="N", date="2026-01-01",
            abstract="only abstract", evidence_level="abstract",
        )
        with patch("inlight_articles.enrich_item", return_value=item):
            with patch("inlight_articles.triage_items", return_value=[
                {"url": item.url, "tier": "deep", "field": "c3"}
            ]):
                out = process_articles(
                    [{"url": item.url, "kind": "academic", "title": "T",
                      "source": "N", "date": "2026-01-01", "summary": "x" * 80}],
                    {"min_deep": 3, "max_brief": 0},
                )
        self.assertEqual(out["articles"], [])
        qc = LAST_RUN_STATS.get("qc_report") or {}
        self.assertTrue(qc.get("articles"))
        self.assertFalse(qc["articles"][0]["published"])


class TestHouseStylePromptsAndImageQc(unittest.TestCase):
    def test_prompt_thick_lines_solid_large_accent_no_text(self):
        from inlight_qc import IMAGE_PREFIX, IMAGE_SUFFIX, mechanism_image_prompt
        import run_weekly

        prompt = mechanism_image_prompt(
            "The TCR on the T cell bound the peptide and the downstream signal fired.",
            "TCR–pMHC engagement phosphorylated ZAP-70 and opened the calcium flux.",
        )
        for blob in (IMAGE_PREFIX, run_weekly.IMAGE_PREFIX, prompt):
            self.assertIn("#C0492F", blob)
            self.assertIn("thin clean dark slate-green outlines", blob)
            self.assertIn("6 percent", blob)
        for blob in (IMAGE_SUFFIX, run_weekly.IMAGE_SUFFIX, prompt):
            low = blob.lower()
            self.assertIn("no text", low)
            self.assertIn("no letters", low)
            self.assertIn("no logos", low)
        self.assertNotIn("THICKER", IMAGE_PREFIX)
        self.assertIn("Subject:", prompt)
        self.assertIn("TCR", prompt)
        self.assertIn("terracotta", prompt.lower())
        self.assertNotIn("outcome outcome", prompt.lower())
        self.assertIn("No faces", prompt)
        self.assertIn("0.382", prompt)
        self.assertIn("1.618", prompt)

    def _span_card(self, text=None, transparent=False):
        from PIL import Image, ImageDraw

        w, h = 1600, 989
        if transparent:
            im = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        else:
            im = Image.new("RGB", (w, h), (255, 255, 255))
        d = ImageDraw.Draw(im)
        # Subject bbox ≈ 72% of the 1.618 card; terracotta on UR golden (~4%).
        d.rectangle([230, 140, 1370, 850], fill=(15, 107, 92))
        gx, gy = int(0.618 * w), int(0.382 * h)
        r = 145
        d.ellipse([gx - r, gy - r, gx + r, gy + r], fill=(192, 73, 47))
        if text:
            d.text((40, 40), text, fill=(20, 20, 20))
        return im

    def test_qc_fill_is_subject_span_not_ink_share(self):
        from PIL import Image, ImageDraw
        from inlight_qc import qc_image, subject_metrics, matte_to_white

        w, h = 1600, 989
        im = Image.new("RGB", (w, h), (255, 255, 255))
        d = ImageDraw.Draw(im)
        d.ellipse([230, 150, 1370, 840], outline=(15, 107, 92), width=6)
        d.ellipse([250, 170, 430, 350], outline=(47, 125, 109), width=4)
        gx, gy = int(0.618 * w), int(0.382 * h)
        d.ellipse([gx - 145, gy - 145, gx + 145, gy + 145], fill=(192, 73, 47))
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "span.png")
            im.save(path)
            out = qc_image(path)
        metrics = subject_metrics(matte_to_white(im))
        self.assertTrue(0.68 <= metrics["span"] <= 0.78, metrics)
        self.assertTrue(0.68 <= out["fill_frac"] <= 0.78, out)
        self.assertLess(out["accent_frac"], 0.20)
        self.assertTrue(out["pass"], out)

    def test_qc_image_accepts_in_range_and_rejects_text(self):
        from inlight_qc import qc_image, write_fallback_cover, is_publishable_image

        with tempfile.TemporaryDirectory() as td:
            good = os.path.join(td, "good.png")
            self._span_card().save(good)
            out = qc_image(good)
            self.assertFalse(out["ocr_text"], out)
            self.assertGreaterEqual(out["fill_frac"], 0.68)
            self.assertLessEqual(out["fill_frac"], 0.78)
            self.assertGreaterEqual(out["accent_frac"], 0.03)
            self.assertTrue(out["pass"], out)

            bad = self._span_card(text="ABC LABEL RESPONSE 64%")
            bad_path = os.path.join(td, "text.png")
            bad.save(bad_path)
            text_out = qc_image(bad_path)
            self.assertTrue(text_out["ocr_text"] or text_out["reasons"], text_out)
            self.assertFalse(text_out["pass"])

            fb = os.path.join(td, "fallback.png")
            write_fallback_cover(fb)
            self.assertTrue(is_publishable_image(fb))
            from PIL import Image
            self.assertEqual(Image.open(fb).size, (1600, 989))

    def test_qc_mattes_transparency_and_fail_closed_without_ocr(self):
        from inlight_qc import qc_image, matte_to_white

        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "alpha.png")
            self._span_card(transparent=True).save(path)
            from PIL import Image
            matted = matte_to_white(Image.open(path))
            self.assertEqual(matted.getpixel((5, 5)), (255, 255, 255))
            out = qc_image(path)
            self.assertLess(out["fill_frac"], 0.95, out)
        with patch("inlight_qc.ocr_available", return_value=False):
            with tempfile.TemporaryDirectory() as td:
                path = os.path.join(td, "x.png")
                self._span_card().save(path)
                closed = qc_image(path)
        self.assertFalse(closed["pass"])
        self.assertTrue(any("OCR" in r for r in closed["reasons"]), closed)

    def test_qc_passes_approved_house_style_references(self):
        from pathlib import Path
        from inlight_qc import qc_image

        roots = [
            Path("/workspace/inlight_review/img_trial3/final/images"),
            Path("/home/ubuntu/.cursor/projects/workspace/uploads"),
            Path(__file__).resolve().parent / "fixtures" / "house_style_v3",
        ]
        wanted = ("t1_trap.png", "t4_tsc_astro.png")
        seen = []
        for root in roots:
            if not root.is_dir():
                continue
            for name in wanted:
                p = root / name
                if p.is_file() and p.stat().st_size > 2000:
                    seen.append(p)
            seen.extend(p for p in sorted(root.glob("APPROVED*.png")) if p.stat().st_size > 2000)
        # Dedup while keeping approved names first.
        uniq = []
        for p in seen:
            if p.resolve() not in {x.resolve() for x in uniq}:
                uniq.append(p)
        self.assertTrue(uniq, "approved reference PNGs must be available for QC")
        for path in uniq:
            out = qc_image(str(path))
            self.assertTrue(0.68 <= out["fill_frac"] <= 0.78, (path.name, out))
            self.assertGreaterEqual(out["accent_frac"], 0.03, (path.name, out))
            self.assertLessEqual(out["accent_frac"], 0.06, (path.name, out))
            self.assertFalse(out["ocr_text"], (path.name, out))
            self.assertLessEqual(out.get("golden_dist", 1), 0.06, (path.name, out))
            self.assertFalse(out.get("dead_center"), (path.name, out))
            self.assertTrue(out["pass"], (path.name, out))

    def test_qc_golden_section_and_accent_emphasis_pass_and_fail(self):
        """Golden placement + strong accent pass; weak/centred accent fail closed."""
        from PIL import Image, ImageDraw
        from inlight_qc import qc_image, GOLDEN_DIST_MAX

        w, h = 1600, 989

        def card(cx, cy, radius, extra_green=True):
            im = Image.new("RGB", (w, h), (255, 255, 255))
            d = ImageDraw.Draw(im)
            if extra_green:
                d.rectangle([230, 140, 1370, 850], fill=(15, 107, 92))
            d.ellipse([cx - radius, cy - radius, cx + radius, cy + radius], fill=(192, 73, 47))
            return im

        with tempfile.TemporaryDirectory() as td:
            good = os.path.join(td, "golden.png")
            card(int(0.618 * w), int(0.382 * h), 145).save(good)
            ok = qc_image(good)
            self.assertTrue(ok["pass"], ok)
            self.assertLessEqual(ok["golden_dist"], GOLDEN_DIST_MAX, ok)
            self.assertFalse(ok["dead_center"], ok)
            self.assertGreaterEqual(ok["accent_frac"], 0.03, ok)

            centred = os.path.join(td, "centre.png")
            card(w // 2, h // 2, 145).save(centred)
            bad_c = qc_image(centred)
            self.assertFalse(bad_c["pass"], bad_c)
            self.assertTrue(
                bad_c.get("dead_center") or bad_c["golden_dist"] > GOLDEN_DIST_MAX,
                bad_c,
            )

            weak = os.path.join(td, "weak.png")
            card(int(0.618 * w), int(0.382 * h), 20).save(weak)
            bad_w = qc_image(weak)
            self.assertFalse(bad_w["pass"], bad_w)
            self.assertTrue(any("accent" in r for r in bad_w["reasons"]), bad_w)

    def test_generate_article_image_falls_back_after_two_retries(self):
        import run_weekly

        calls = {"n": 0}

        def fake_draw(prompt, dest, pos="UR", reframe=False):
            calls["n"] += 1
            dest.write_bytes(b"not-a-png")

        fail = {
            "pass": False,
            "ocr_text": True,
            "accent_frac": 0.01,
            "fill_frac": 0.40,
            "reasons": ["ocr text detected", "accent 0.010 outside 0.03-0.06"],
        }
        with tempfile.TemporaryDirectory() as td:
            dest = os.path.join(td, "a1.png")
            with patch.object(run_weekly, "image_pipeline_ready", return_value=(True, "")):
                with patch.object(run_weekly, "draw_image", side_effect=fake_draw):
                    with patch("inlight_qc.qc_image", return_value=fail):
                        out = run_weekly.generate_article_image("Subject: cells", dest, True)
            self.assertEqual(calls["n"], 3)
            self.assertTrue(out.get("skipped"))
            self.assertFalse(out["pass"])
            self.assertFalse(os.path.isfile(dest))

    def test_write_output_skips_images_when_nothing_published(self):
        import run_weekly

        called = []
        with tempfile.TemporaryDirectory() as td:
            dest = os.path.join(td, "week")
            os.makedirs(dest, exist_ok=True)
            dest_path = __import__("pathlib").Path(dest)
            with patch.object(run_weekly, "ROOT", dest_path.parent):
                with patch.object(run_weekly, "draw_image", side_effect=lambda *a, **k: called.append("draw")):
                    with patch.object(run_weekly, "generate_article_image", side_effect=lambda *a, **k: called.append("gen") or {"pass": True, "fallback": False}):
                        run_weekly.write_output(
                            {"articles": [], "deals": [], "qc_report": {"articles": []}},
                            dest_path,
                            "2026-10-08",
                        )
            self.assertEqual(called, [])
            self.assertFalse((dest_path / "wechat" / "cover.png").exists())
            qc = json.loads((dest_path / "qc_report.json").read_text())
            self.assertNotIn("images", qc)

    def test_write_output_logs_image_qc_fallback(self):
        import run_weekly

        def fake_gen(prompt, dest, qc_enabled=True, **kwargs):
            dest.write_bytes(b"\x89PNG\r\n\x1a\n")
            return {
                "pass": False,
                "fallback": False,
                "skipped": True,
                "attempts": 3,
                "reasons": ["ocr text detected"],
                "ocr_text": True,
                "accent_frac": 0.01,
                "fill_frac": 0.4,
            }

        with tempfile.TemporaryDirectory() as td:
            dest = __import__("pathlib").Path(td) / "week"
            dest.mkdir()
            with patch.object(run_weekly, "ROOT", dest.parent):
                with patch.object(run_weekly, "generate_article_image", side_effect=fake_gen):
                    run_weekly.write_output(
                        {
                            "articles": [{
                                "title": "GOOD",
                                "field": "c3",
                                "date": "2026-10-01",
                                "url": "https://example.com/good",
                                "lead": "good",
                                "body": "body",
                                "discuss": "d",
                                "journal": "Nature",
                                "authors": "",
                                "image_prompt": "Subject: cells",
                                "tier": "deep",
                                "one_liner": "对照文章",
                                "datacard": {"n": "20例"},
                                "results": ["缓解率58%"],
                            }],
                            "deals": [],
                            "qc_report": {"articles": [{"url": "https://example.com/good", "published": True}]},
                        },
                        dest,
                        "2026-10-01",
                    )
            qc = json.loads((dest / "qc_report.json").read_text())
            self.assertTrue(qc.get("images"))
            self.assertTrue(any(x.get("skipped") or x.get("fallback") for x in qc["images"]))
            arts = json.loads((dest / "articles.json").read_text())
            self.assertEqual(len(arts), 1)
            self.assertFalse(arts[0].get("img"))
            cover = dest / "wechat" / "cover.png"
            self.assertTrue(cover.is_file())
            from PIL import Image
            self.assertEqual(Image.open(cover).size, (1600, 989))
            self.assertTrue(any(
                x.get("file") == "wechat/cover.png" and x.get("fallback")
                for x in qc["images"]
            ))
            self.assertGreater(cover.stat().st_size, 2000)

    def test_site_css_card_ratio_and_grid(self):
        from pathlib import Path
        css = Path(__file__).resolve().parent.parent.joinpath("index.html").read_text()
        self.assertIn("aspect-ratio:1.618/1", css)
        self.assertIn("object-fit:contain", css)
        self.assertIn("grid-template-columns:repeat(4,minmax(0,1fr))", css)
        self.assertIn("grid-template-columns:repeat(2,minmax(0,1fr))", css)
        self.assertNotIn("grid-template-columns:repeat(3,minmax(0,1fr))", css.split(".fieldlist")[0])
        self.assertIn("${escHtml(r.t)}", css)
        self.assertIn("${escHtml(x.t)}", css)
        self.assertIn("${escHtml(x.m)}", css)
        self.assertIn("${escHtml(a.j)}", css)
        self.assertIn("${escHtml(a.t)}", css)
        self.assertIn("const FNAMES={f1:'类器官'", css)
        self.assertIn("k:'f9',n:'精准肿瘤与临床转化'", css)
        self.assertIn("function escUrl", css)
        self.assertIn("function fieldKey", css)
        self.assertIn("${escUrl(a.url)}", css)
        self.assertIn("${escUrl(dealData.url)}", css)
        self.assertIn("${escHtml(a.disp)}", css)
        self.assertIn("fieldKey(a.f)", css)
        self.assertIn("function isVisibleArticle", css)
        self.assertIn("function primaryField", css)
        self.assertIn("每篇只进一个主领域栏目", css)
        self.assertIn("不属于九个领域的条目不展示", css)
        self.assertIn("isVisibleArticle(a)", css)

    def test_write_output_hides_out_of_scope_articles(self):
        import run_weekly

        with tempfile.TemporaryDirectory() as td:
            dest = __import__("pathlib").Path(td) / "week"
            dest.mkdir()
            with patch.object(run_weekly, "ROOT", dest.parent):
                with patch.object(run_weekly, "generate_article_image", return_value={"pass": False, "skipped": True}):
                    run_weekly.write_output(
                        {
                            "articles": [
                                {
                                    "title": "OUT",
                                    "url": "https://doi.org/10.1/none",
                                    "date": "2026-10-08",
                                    "field": "none",
                                    "source": "N",
                                    "authors": "A",
                                    "lead": "x",
                                    "body": "y",
                                    "discuss": "z",
                                    "steps": ["a", "b", "c"],
                                    "excluded": True,
                                },
                                {
                                    "title": "IN",
                                    "url": "https://doi.org/10.1/f4",
                                    "date": "2026-10-08",
                                    "field": "f4",
                                    "primary_field": "f4",
                                    "related_fields": ["f5"],
                                    "source": "N",
                                    "authors": "A",
                                    "lead": "x",
                                    "body": "y",
                                    "discuss": "z",
                                    "steps": ["a", "b", "c"],
                                },
                            ],
                            "deals": [],
                            "qc_report": {"articles": []},
                        },
                        dest,
                        "2026-10-08",
                    )
            data = json.loads((dest / "articles.json").read_text())
            titles = [a.get("t") for a in data]
            self.assertNotIn("OUT", titles)
            self.assertIn("IN", titles)
            kept = next(a for a in data if a["t"] == "IN")
            self.assertEqual(kept["f"], "f4")
            self.assertEqual(kept["tags"][0], "f4")

    def test_gemini_reviewer_model_is_31_pro_preview(self):
        from pathlib import Path
        from inlight_qc import DEFAULT_GEMINI_MODEL

        self.assertEqual(DEFAULT_GEMINI_MODEL, "gemini-3.1-pro-preview")
        src = Path(__file__).resolve().parent.parent.joinpath("sources.yaml").read_text()
        self.assertIn("gemini_model: gemini-3.1-pro-preview", src)
        self.assertNotIn("gemini-2.5-flash", src)


if __name__ == "__main__":
    unittest.main()
