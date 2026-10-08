#!/usr/bin/env python3
"""Pass/fail gates for writing_standard + audit_requirements."""

from __future__ import annotations

import unittest
from unittest.mock import patch

from inlight_audit import (
    BLIND_DIMS,
    QC_REPORT_FIELDS,
    apply_dual_blind_to_week,
    attach_audit_fields,
    boilerplate_count,
    claim_support_rate,
    combine_blind_judges,
    extract_must_cover,
    reason_cites_concrete_defect,
    field_is_incidental_tool,
    figure_numbers_subset_of_data_points,
    hard_errors,
    headline_comparative_without_power,
    headline_untested_comparison,
    hedging_upgraded,
    infer_location,
    must_cover_coverage,
    one_liner_shares_title_basis,
    pick_top_deep_for_blind,
    primary_endpoint_paragraph_ok,
    run_automated_audit,
)
from inlight_qc import validate_acir_structure
from tests.test_acir_qc import _deep_art, _results


def _pass_judge(judge="claude", dim_override=None, major=False, available=True,
                reasons=None):
    scores = {k: 8 for k in BLIND_DIMS}
    if dim_override:
        scores.update(dim_override)
    overall = sum(scores.values()) / len(BLIND_DIMS)
    passed = (
        available
        and (not major)
        and all(scores[k] >= 7 for k in BLIND_DIMS)
        and overall >= 7
    )
    if reasons is None:
        reasons = "ok" if passed else "fail"
    return {
        "available": available,
        "pass": passed,
        "judge": judge,
        "scores": scores,
        "overall": round(overall, 2),
        "major_factual_error": major,
        "reasons": reasons,
    }


def _src_for_deep():
    return (
        _results()
        + " objective response rate was 64% control response rate was 32% "
        "among 527 women hazard ratio 0.50 Grade 3+ adverse events 21% "
        "median follow-up 18 months. A trend was associated with HLA-I."
    )


class TestHeadlineRules(unittest.TestCase):
    def test_pass_primary_endpoint_title_and_shared_basis(self):
        art = _deep_art()
        self.assertTrue(one_liner_shares_title_basis(art))
        self.assertFalse(headline_untested_comparison(art))
        self.assertFalse(headline_comparative_without_power(art))
        audit = run_automated_audit(
            art, results_src=_results(), source=_src_for_deep(),
            real_fulltext=True, structure_ok=True, number_ok=True,
        )
        self.assertTrue(audit["headline_ok"])

    def test_fail_untested_comparison_in_title(self):
        art = _deep_art(
            title="联合方案使缓解率达35%对20%",
            results=["35%对20%的对比原文未报告统计学检验。", "n=40例。", "对照体积257.8。"],
        )
        self.assertTrue(headline_untested_comparison(art))
        hs = hard_errors(art, real_fulltext=True)
        self.assertTrue(any(e["code"] == "H3" for e in hs))

    def test_fail_comparative_verdict_without_power(self):
        art = _deep_art(
            title="新药疗效相当且显著优于对照",
            design="单臂二期，未做正式比较，无权效。",
        )
        self.assertTrue(headline_comparative_without_power(art))
        hs = hard_errors(art, real_fulltext=True)
        self.assertTrue(any(e["code"] == "H2" for e in hs))

    def test_fail_one_liner_mixed_basis(self):
        art = _deep_art(one_liner="该队列把缓解率做到百分之十二。")
        self.assertFalse(one_liner_shares_title_basis(art))
        audit = run_automated_audit(
            art, results_src=_results(), real_fulltext=True,
            structure_ok=True, number_ok=True,
        )
        self.assertFalse(audit["headline_ok"])
        self.assertFalse(audit["publish_allowed"])


class TestPrimaryEndpointParagraph(unittest.TestCase):
    def test_pass_definition_n_result_control_power(self):
        self.assertTrue(primary_endpoint_paragraph_ok(_deep_art()))

    def test_fail_missing_control_and_power(self):
        art = _deep_art(
            design="开放标签探索性研究。",
            results=["主要终点客观缓解率为64%。", "527例可评估。", "不良事件21%。"],
            datacard={
                "study_type": "II期",
                "primary_endpoint": "ORR",
                "primary_endpoint_result": "64%",
            },
        )
        self.assertFalse(primary_endpoint_paragraph_ok(art))
        audit = run_automated_audit(art, real_fulltext=True, structure_ok=True, number_ok=True)
        self.assertFalse(audit["endpoint_hierarchy"])


class TestMustCover(unittest.TestCase):
    def test_pass_legend_survival_and_negative_control(self):
        abstract = "Primary endpoint ORR was 64% in 527 women."
        results = "Median PFS was 13.8 months in the treated arm.\n\nOS was immature."
        figs = "Figure 2. Median OS 22.4 months. Freeze-thaw control tumor volume 257.8 mm3."
        items = extract_must_cover(abstract, results, figs)
        self.assertGreaterEqual(len(items), 3)
        self.assertTrue(any("257.8" in it["text"] or "257.8" in it["cores"] for it in items))
        art = _deep_art(
            results=[
                "主要终点客观缓解率为64%，对照为32%，风险比0.50。",
                "527例可评估，中位PFS 13.8个月，图注中位OS 22.4个月。",
                "冻融对照体积257.8。",
                "三级以上不良事件发生率为21%。",
            ]
        )
        cover, uncovered = must_cover_coverage(art, items)
        self.assertGreaterEqual(cover, 0.90)
        self.assertEqual(uncovered, [])

    def test_fail_uncovered_unless_named_in_limitations(self):
        items = [
            {"text": "Median PFS was 13.8 months", "location": "Results", "cores": ["13.8"]},
            {"text": "Freeze-thaw volume 257.8", "location": "Fig", "cores": ["257.8"]},
        ]
        art = _deep_art()
        cover, uncovered = must_cover_coverage(art, items)
        self.assertLess(cover, 0.90)
        self.assertEqual(len(uncovered), 2)
        named = _deep_art(limitations=["未写入中位PFS 13.8与冻融对照257.8，因图注口径待核。", "外推有限。", "随访偏短。"])
        cover2, uncovered2 = must_cover_coverage(named, items)
        self.assertEqual(cover2, 1.0)
        self.assertEqual(uncovered2, [])


class TestLocationBasisAndFigure(unittest.TestCase):
    def test_pass_inferred_location_and_matching_figure(self):
        dp = {
            "value": "64%",
            "meaning": "ORR",
            "source_quote": "objective response rate was 64%",
        }
        loc = infer_location(dp, abstract="", results="The objective response rate was 64% among 527 women.")
        self.assertEqual(loc, "Results")
        art = _deep_art(
            figure_labels=["64%", "32%"],
            figure_basis="body",
            data_points=[
                {"value": "64%", "meaning": "缓解率", "source_quote": "objective response rate was 64%", "location": "Results", "basis": "body"},
                {"value": "32%", "meaning": "对照", "source_quote": "control response rate was 32%", "location": "Results", "basis": "body"},
            ],
        )
        self.assertTrue(figure_numbers_subset_of_data_points(art))

    def test_fail_mixed_basis_in_one_figure(self):
        art = _deep_art(
            figure_labels=["24.3%", "57.1%"],
            figure_mixed_basis=True,
            data_points=[
                {"value": "24.3%", "meaning": "摘要ORR", "source_quote": "ORR 24.3%", "location": "Abstract", "basis": "abstract"},
                {"value": "57.1%", "meaning": "正文ORR", "source_quote": "ORR 57.1%", "location": "Results", "basis": "body"},
            ],
        )
        self.assertFalse(figure_numbers_subset_of_data_points(art))
        hs = hard_errors(art, real_fulltext=True)
        self.assertTrue(any(e["code"] == "H4" for e in hs))

    def test_fail_figure_number_not_in_data_points(self):
        art = _deep_art(figure_labels=["99.9%"], figure_basis="body")
        self.assertFalse(figure_numbers_subset_of_data_points(art))


class TestHedging(unittest.TestCase):
    def test_pass_keeps_trend(self):
        art = _deep_art(mechanism="HLA-I与缓解呈相关趋势，作者推测或可外推。")
        self.assertFalse(hedging_upgraded(art, "a trend was associated with HLA-I"))

    def test_fail_upgrades_trend_to_proven(self):
        art = _deep_art(mechanism="HLA-I依赖已被证明，通路清除已证实。")
        self.assertTrue(hedging_upgraded(art, "HLA-I showed a trend and may be associated"))


class TestBoilerplateAndOmit(unittest.TestCase):
    def test_pass_at_most_two_and_omit_missing(self):
        art = _deep_art()
        self.assertLessEqual(boilerplate_count(art), 2)
        self.assertEqual(validate_acir_structure(art), [])
        omitted = _deep_art()
        omitted["datacard"] = {
            k: v for k, v in omitted["datacard"].items()
            if k in ("primary_endpoint", "primary_endpoint_result", "study_type",
                     "n", "control", "intervention", "followup", "statistics", "safety")
        }
        del omitted["datacard"]["followup"]
        self.assertEqual(validate_acir_structure(omitted), [])

    def test_fail_placeholder_and_over_cap(self):
        art = _deep_art()
        art["datacard"]["n"] = "原文未给出"
        art["datacard"]["followup"] = "原文未报告"
        art["limitations"] = ["原文未给出剂量。", "原文未报告随访。", "外推有限。"]
        self.assertGreater(boilerplate_count(art), 2)
        probs = validate_acir_structure(art)
        self.assertTrue(any("占位套话" in p for p in probs))
        from inlight_articles import build_article_prompt, EnrichedItem
        item = EnrichedItem(url="u", title="T", source="N", date="2026-01-01", evidence_level="fulltext")
        prompt = build_article_prompt(item, "deep")
        self.assertIn("直接省略", prompt)
        self.assertNotIn("写「原文未给出」", prompt)


class TestHardErrorsAndClaimRate(unittest.TestCase):
    def test_pass_clean_article_no_hard_errors(self):
        art = _deep_art()
        hs = hard_errors(
            art, source=_src_for_deep(), real_fulltext=True,
            claim_audit={"status": "ok", "claims": []},
            has_figure=True,
        )
        self.assertEqual(hs, [])
        rate, _s, uns, con = claim_support_rate({"status": "ok", "claims": []})
        self.assertEqual(rate, 1.0)
        self.assertEqual((uns, con), (0, 0))

    def test_fail_h1_through_h8(self):
        art = _deep_art()
        self.assertTrue(any(e["code"] == "H1" for e in hard_errors(art, number_problems=["数字 99% 未找到"], real_fulltext=True)))
        self.assertTrue(any(e["code"] == "H2" for e in hard_errors(
            _deep_art(title="新药疗效相当", design="未做正式比较，无权效。"), real_fulltext=True)))
        self.assertTrue(any(e["code"] == "H3" for e in hard_errors(
            _deep_art(title="缓解35%对20%", results=["35%对20%原文未报告统计学检验。"]), real_fulltext=True)))
        self.assertTrue(any(e["code"] == "H4" for e in hard_errors(
            _deep_art(figure_mixed_basis=True, figure_labels=["1"]), real_fulltext=True)))
        self.assertTrue(any(e["code"] == "H5" for e in hard_errors(art, real_fulltext=False)))
        self.assertTrue(any(e["code"] == "H6" for e in hard_errors(
            art, real_fulltext=True,
            claim_audit={"status": "ok", "claims": [
                {"factual": True, "label": "CONTRADICTED"},
                {"factual": True, "label": "SUPPORTED"},
            ]},
        )))
        self.assertTrue(any(e["code"] == "H7" for e in hard_errors(
            {**art, "field": "none"}, real_fulltext=True)))
        self.assertTrue(any(e["code"] == "H8" for e in hard_errors(
            art, real_fulltext=True, has_figure=False)))
        self.assertTrue(any(e["code"] == "H8" for e in hard_errors(
            art, real_fulltext=True, has_figure=True, ocr_text=True)))

    def test_fail_claim_rate_below_threshold(self):
        audit = {"claims": [
            {"factual": True, "label": "SUPPORTED"},
            {"factual": True, "label": "UNSUPPORTED"},
        ]}
        rate, _s, uns, con = claim_support_rate(audit)
        self.assertLess(rate, 0.95)
        self.assertEqual(uns, 1)
        hs = hard_errors(_deep_art(), real_fulltext=True, claim_audit=audit)
        self.assertTrue(any(e["code"] == "H6" for e in hs))


class TestDualBlindJudging(unittest.TestCase):
    def test_pass_both_judges_at_least_seven(self):
        combo = combine_blind_judges(_pass_judge("claude"), _pass_judge("gemini"))
        self.assertTrue(combo["pass"])
        self.assertFalse(combo["fail_closed"])

    def test_fail_any_dimension_below_seven(self):
        combo = combine_blind_judges(
            _pass_judge(
                "claude",
                dim_override={"accuracy": 6},
                reasons="ORR 64% is swapped with the control 32% in Results",
            ),
            _pass_judge("gemini"),
        )
        self.assertFalse(combo["pass"])
        self.assertFalse(combo["fail_closed"])

    def test_fail_major_factual_error(self):
        combo = combine_blind_judges(_pass_judge("claude", major=True), _pass_judge("gemini"))
        self.assertFalse(combo["pass"])

    def test_fail_closed_when_both_unavailable(self):
        combo = combine_blind_judges(
            {"available": False, "pass": False, "judge": "claude", "reasons": "PARSEERR"},
            {"available": False, "pass": False, "judge": "gemini", "reasons": "missing key"},
        )
        self.assertFalse(combo["pass"])
        self.assertTrue(combo["fail_closed"])

    def test_one_unavailable_other_must_still_pass(self):
        ok = combine_blind_judges(
            {"available": False, "pass": False, "judge": "claude", "reasons": "PARSEERR"},
            _pass_judge("gemini"),
        )
        self.assertTrue(ok["pass"])
        bad = combine_blind_judges(
            {"available": False, "pass": False, "judge": "claude", "reasons": "PARSEERR"},
            _pass_judge(
                "gemini",
                dim_override={"information": 5},
                reasons="information: omitted the 527-patient n from Results",
            ),
        )
        self.assertFalse(bad["pass"])

    def test_style_is_never_a_fail_condition(self):
        payload = _pass_judge("claude")
        payload["scores"]["style"] = 1
        payload["resembles_acir"] = False
        combo = combine_blind_judges(payload, _pass_judge("gemini"))
        self.assertTrue(combo["pass"])

    def test_at_most_three_articles_are_scored(self):
        arts = [_deep_art(url=f"https://doi.org/10.1/{i}", data_points=[{}] * (10 - i)) for i in range(5)]
        for a in arts:
            a["tier"] = "deep"
        picked = pick_top_deep_for_blind(arts, 3)
        self.assertEqual(len(picked), 3)
        scored = []

        def sc(_art, _src, _cfg):
            scored.append(_art.get("url"))
            return _pass_judge()

        stats = {"drops": [], "qc_report": {"articles": []}, "published_deep": 5}
        kept = apply_dual_blind_to_week(
            arts, stats, {"min_deep": 3},
            score_claude=sc, score_gemini=sc, redraft=None,
        )
        self.assertEqual(len(kept), 5)
        self.assertEqual(len(set(scored)), 3)

    def test_max_two_rewrites_then_unpublish(self):
        art = _deep_art()
        rewrites = {"n": 0}

        def fail_judge(*_a, **_k):
            return _pass_judge(
                "claude",
                dim_override={"understanding": 4},
                reasons="mechanism claims HLA-I clearance is proven; figure omits 21% AE",
            )

        def redraft(current, reasons):
            rewrites["n"] += 1
            nxt = _deep_art()
            nxt["title"] = current.get("title") + "改"
            return nxt

        stats = {"drops": [], "qc_report": {"articles": [{"url": art["url"], "published": True}]}, "published_deep": 1}
        kept = apply_dual_blind_to_week(
            [art], stats, {"min_deep": 3},
            score_claude=fail_judge, score_gemini=fail_judge, redraft=redraft,
        )
        self.assertEqual(kept, [])
        self.assertEqual(rewrites["n"], 2)
        self.assertTrue(stats["drops"])
        self.assertFalse(stats["qc_report"]["articles"][0].get("publish_allowed", True))

    def test_sub7_without_reason_reruns_then_passes(self):
        calls = {"n": 0}

        def scorer(*_a, **_k):
            calls["n"] += 1
            if calls["n"] == 1:
                return _pass_judge("claude", dim_override={"accuracy": 6}, reasons="fail")
            return _pass_judge("claude")

        def gemini(*_a, **_k):
            return _pass_judge("gemini")

        art = _deep_art()
        stats = {"drops": [], "qc_report": {"articles": [{"url": art["url"], "published": True}]}}
        kept = apply_dual_blind_to_week(
            [art], stats, {"min_deep": 3},
            score_claude=scorer, score_gemini=gemini, redraft=None,
        )
        self.assertEqual(len(kept), 1)
        self.assertGreaterEqual(calls["n"], 2)
        self.assertTrue(reason_cites_concrete_defect(
            "ORR 64% omitted from Results"
        ))
        self.assertFalse(reason_cites_concrete_defect("fail"))
        runs = stats["qc_report"]["articles"][0].get("blind_judge_runs") or []
        claude_run = next(r for r in runs if r.get("judge") == "claude")
        self.assertIsNotNone(claude_run.get("original"))
        self.assertIsNotNone(claude_run.get("rerun"))
        self.assertEqual((claude_run["original"] or {}).get("scores", {}).get("accuracy"), 6)
        self.assertGreaterEqual((claude_run["rerun"] or {}).get("overall") or 0, 7)

    def test_sub7_with_concrete_defect_fails(self):
        def scorer(*_a, **_k):
            return _pass_judge(
                "claude",
                dim_override={"accuracy": 5},
                reasons="Results claim ORR 99% which is not in the source",
            )

        art = _deep_art()
        stats = {"drops": [], "qc_report": {"articles": [{"url": art["url"], "published": True}]}}
        kept = apply_dual_blind_to_week(
            [art], stats, {"min_deep": 3},
            score_claude=scorer, score_gemini=lambda *_a, **_k: _pass_judge("gemini"),
            redraft=None,
        )
        self.assertEqual(kept, [])
        self.assertTrue(stats["drops"])
        self.assertIn("accuracy", stats["drops"][0]["reason"])
        runs = stats["qc_report"]["articles"][0].get("blind_judge_runs") or []
        claude_run = next(r for r in runs if r.get("judge") == "claude")
        self.assertIsNotNone(claude_run.get("original"))
        self.assertIsNone(claude_run.get("rerun"))

    def test_rerun_still_sub7_without_defect_fails_closed(self):
        def scorer(*_a, **_k):
            return _pass_judge("claude", dim_override={"exposition": 4}, reasons="vague")

        art = _deep_art()
        stats = {"drops": [], "qc_report": {"articles": [{"url": art["url"], "published": True}]}}
        kept = apply_dual_blind_to_week(
            [art], stats, {"min_deep": 3},
            score_claude=scorer, score_gemini=lambda *_a, **_k: _pass_judge("gemini"),
            redraft=lambda *_a, **_k: _deep_art(),
        )
        self.assertEqual(kept, [])
        self.assertTrue(stats["drops"])
        self.assertIn("without cited defect", stats["drops"][0]["reason"])
        runs = stats["qc_report"]["articles"][0].get("blind_judge_runs") or []
        claude_run = next(r for r in runs if r.get("judge") == "claude")
        self.assertIsNotNone(claude_run.get("original"))
        self.assertIsNotNone(claude_run.get("rerun"))


class TestQcReportAndPublishGate(unittest.TestCase):
    def test_pass_emits_section_four_fields(self):
        art = _deep_art()
        audit = run_automated_audit(
            art, results_src=_results(), source=_src_for_deep(),
            real_fulltext=True, structure_ok=True, number_ok=True,
            claim_audit={"status": "ok", "claims": []},
            has_figure=True,
        )
        entry = attach_audit_fields({"url": art["url"], "published": True}, audit)
        for key in QC_REPORT_FIELDS:
            self.assertIn(key, entry)
        self.assertTrue(entry["publish_allowed"])

    def test_fail_any_gate_blocks_publish(self):
        art = _deep_art(title="新药疗效相当")
        audit = run_automated_audit(
            art, real_fulltext=True, structure_ok=True, number_ok=True,
        )
        self.assertFalse(audit["publish_allowed"])
        entry = attach_audit_fields({"published": True}, audit)
        self.assertFalse(entry["publish_allowed"])
        self.assertFalse(entry["published"])


class TestFieldSubjectTags(unittest.TestCase):
    def test_pass_subject_not_incidental_mouse_use(self):
        art = _deep_art(field="f4")
        art["design"] = "在小鼠中验证该检查点抗体的抗肿瘤活性，并报告人源化小鼠建系。"
        # f4 is the subject (tumor immunity); incidental mice must not force f2.
        self.assertFalse(field_is_incidental_tool(art))
        subject = _deep_art(field="f2")
        subject["design"] = "本文贡献是人源化小鼠建系与模型验证平台。"
        self.assertFalse(field_is_incidental_tool(subject))

    def test_fail_incidental_mouse_or_delivery_tagged_as_subject(self):
        mouse = _deep_art(field="f2")
        mouse["design"] = "该疗法在小鼠中验证，动物实验仅作验证。"
        self.assertTrue(field_is_incidental_tool(mouse))
        delivery = _deep_art(field="f7")
        delivery["design"] = "本文只比较静脉注射与皮下给药途径，delivery route 不是格式改造。"
        self.assertTrue(field_is_incidental_tool(delivery))
        hs = hard_errors(mouse, real_fulltext=True)
        self.assertTrue(any(e["code"] == "H7" and "incidental" in e["detail"] for e in hs))


class TestAutomatedAuditOnDeepFixture(unittest.TestCase):
    def test_deep_art_passes_automated_gates(self):
        art = _deep_art()
        audit = run_automated_audit(
            art,
            abstract="The objective response rate was 64% among 527 women.",
            results_src=_results(),
            source=_src_for_deep(),
            real_fulltext=True,
            structure_ok=True,
            number_ok=True,
            claim_audit={"status": "ok", "claims": []},
            has_figure=True,
        )
        self.assertTrue(audit["publish_allowed"], audit)
        self.assertEqual(audit["hard_errors"], [])
        self.assertTrue(all(dp.get("location") for dp in art["data_points"]))


if __name__ == "__main__":
    unittest.main()
