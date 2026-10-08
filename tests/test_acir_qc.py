#!/usr/bin/env python3
"""ACIR full-text admission, structure, chart, QC gate, and Gemini review."""

from __future__ import annotations

import json
import logging
import os
import tempfile
import unittest
from unittest.mock import patch

from inlight_articles import EnrichedItem, _process_single_article, process_articles, LAST_RUN_STATS
from inlight_qc import (
    FIG_DISCLAIMER,
    GEMINI_SCORE_KEYS,
    comparable_verified_points,
    gemini_review_deep,
    is_real_results_text,
    item_has_real_fulltext,
    load_gemini_api_key,
    mechanism_image_prompt,
    record_fulltext,
    render_data_chart_svg,
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

    def test_no_mechanism_figure_without_fulltext(self):
        prompt = mechanism_image_prompt(_results(), "pathway blocked")
        self.assertIn("#0F6B5C", prompt)
        self.assertNotIn("abstract", prompt.lower())
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


if __name__ == "__main__":
    unittest.main()
