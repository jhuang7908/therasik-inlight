#!/usr/bin/env python3
"""Writing-standard and pre-publish audit gates for deep 解读.

Builds on article_spec / validate_depth / claim verifier / structure QC.
Does not replace them. Style or 'resembles ACIR' is never a fail condition.
"""

from __future__ import annotations

import json
import logging
import os
import re
from typing import Any

from inlight_qc import (
    DEFAULT_GEMINI_MODEL,
    FULLTEXT_WINDOW,
    _gemini_generate,
    acir_strict,
    load_gemini_api_key,
)

BLIND_DIMS = (
    "accuracy",
    "information",
    "understanding",
    "exposition",
    "figure_information",
    "significance",
)
BLIND_MIN = 7
BLIND_MAX_ARTICLES = 3
BLIND_MAX_REWRITES = 2
CLAIM_RATE_MIN = 0.95
MUST_COVER_MIN = 0.90
MUST_COVER_LO, MUST_COVER_HI = 5, 12
BOILERPLATE_MAX = 2
QC_REPORT_FIELDS = (
    "gate_fulltext", "structure", "number_trace", "claim_support_rate",
    "headline_ok", "endpoint_hierarchy", "must_cover_coverage",
    "figure_text_consistency", "classification", "boilerplate_count",
    "blind_scores", "hard_errors", "rewrite_count", "publish_allowed",
)

BOILERPLATE_RE = re.compile(r"原文未报告|原文未给出")
UNTESTED_RE = re.compile(
    r"原文未报告统计学检验|未做统计检验|未做正式比较|未检验|无权效|"
    r"not (?:statistically )?tested|not powered|no formal comparison",
    re.I,
)
COMPARATIVE_VERDICT_RE = re.compile(
    r"优于|减半|疗效相当|显著优于|非劣|优效|equivalent|superior|non-?inferior",
    re.I,
)
POWERED_RE = re.compile(
    r"powered|检验效能|正式比较|组间比较设计|Fleming|优效检验|非劣效",
    re.I,
)
HEDGE_SRC_RE = re.compile(r"\b(?:trend|associated|may|might|suggests?|possibly)\b|趋势|相关|推测", re.I)
HEDGE_UPGRADE_RE = re.compile(r"显著优于|已证实|证明了|显著改善|causal|proven", re.I)
LOCATION_RE = re.compile(
    r"(?i)\b(abstract|results?|fig(?:ure)?\.?\s*[\w\d.-]*|table\.?\s*[\w\d.-]*|methods?)\b|"
    r"摘要|结果|图\s*[\w\d.-]*|表\s*[\w\d.-]*|方法"
)
LOCATION_CANON = {
    "abstract": "Abstract",
    "摘要": "Abstract",
    "results": "Results",
    "result": "Results",
    "结果": "Results",
    "methods": "Methods",
    "method": "Methods",
    "方法": "Methods",
}
ANIMAL_SUBJECT_RE = re.compile(r"建系|模型验证|人源化小鼠|新品系|动物模型本身|敲除小鼠平台")
ANIMAL_INCIDENTAL_RE = re.compile(r"在小鼠中验证|mouse model of|用于验证该疗法|动物实验仅作验证")
AB_SUBJECT_RE = re.compile(r"抗体格式|Fc\s*改造|双特异|纳米抗体|ADC 格式|抗体工程")
AB_INCIDENTAL_RE = re.compile(r"给药途径|delivery route|静脉注射|递送路径")
NUM_RE = re.compile(r"(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?")
CN_NUM_RE = re.compile(r"[零一二三四五六七八九十百千万两]+")
_CN_DIGIT = {
    "零": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4,
    "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10,
}

HAN_RE = re.compile(r"[\u4e00-\u9fff]")


def _han(text: Any) -> str:
    if isinstance(text, list):
        return "。".join(_han(x) for x in text if x)
    return str(text or "")


def article_plain(art: dict) -> str:
    parts = [
        _han(art.get(k))
        for k in (
            "title", "one_liner", "background", "design", "results",
            "mechanism", "limitations", "significance",
        )
    ]
    dc = art.get("datacard")
    if isinstance(dc, dict):
        parts.extend(f"{k}:{v}" for k, v in dc.items() if v)
    return "\n".join(p for p in parts if p)


def _num_cores(text: str) -> list[str]:
    cores: list[str] = []
    blob = str(text or "").replace(",", "").replace("，", "")
    for m in NUM_RE.finditer(blob):
        cores.append(m.group(0).lstrip("0") or "0" if m.group(0) != "0" else "0")
        if "." in m.group(0):
            cores.append(m.group(0))
    # 百分之六四 / 五二七
    for m in re.finditer(r"百分之([一二三四五六七八九十]+)", blob):
        cores.append("".join(str(_CN_DIGIT.get(ch, "")) for ch in m.group(1)))
    compact = []
    i = 0
    chars = list(blob)
    while i < len(chars):
        if chars[i] in _CN_DIGIT and chars[i] != "十":
            digits = []
            while i < len(chars) and chars[i] in _CN_DIGIT and chars[i] != "十":
                digits.append(str(_CN_DIGIT[chars[i]]))
                i += 1
            if len(digits) >= 2:
                compact.append("".join(digits).lstrip("0") or "0")
            continue
        i += 1
    cores.extend(compact)
    return [c for c in cores if c]


def boilerplate_count(art: dict) -> int:
    return len(BOILERPLATE_RE.findall(article_plain(art)))


def infer_location(dp: dict, abstract: str = "", results: str = "", figs: str = "", methods: str = "") -> str:
    raw = str(dp.get("location") or dp.get("section") or "")
    if raw:
        key = raw.strip()
        low = key.lower()
        for tok, canon in LOCATION_CANON.items():
            if tok in low or tok in key:
                if canon == "Results" and re.search(r"(?i)fig|图", key):
                    return "Fig"
                if canon != "Results" or "result" in low or "结果" in key:
                    return canon
        if re.search(r"(?i)fig|图", key):
            return "Fig"
        if re.search(r"(?i)table|表", key):
            return "Table"
        if key in ("Abstract", "Results", "Fig", "Table", "Methods"):
            return key
    meaning = str(dp.get("meaning") or "")
    pref = LOCATION_RE.search(meaning)
    if pref:
        return _canon_loc(pref.group(0))
    quote = str(dp.get("source_quote") or "")
    if quote and abstract and quote[:40] in abstract:
        return "Abstract"
    if quote and figs and quote[:40] in figs:
        return "Fig"
    if quote and methods and quote[:40] in methods:
        return "Methods"
    if quote and results and quote[:40] in results:
        return "Results"
    return ""


def _canon_loc(token: str) -> str:
    low = token.lower()
    if re.search(r"(?i)fig|图", token):
        return "Fig"
    if re.search(r"(?i)table|表", token):
        return "Table"
    for tok, canon in LOCATION_CANON.items():
        if tok in low or tok in token:
            return canon
    return "Results"


def data_point_basis(dp: dict) -> str:
    raw = str(dp.get("basis") or "").lower()
    if raw in ("abstract", "摘要"):
        return "abstract"
    if raw in ("body", "results", "正文"):
        return "body"
    loc = str(dp.get("location") or "")
    if loc == "Abstract" or loc.lower() == "abstract":
        return "abstract"
    return "body"


def headline_untested_comparison(art: dict) -> bool:
    """H3: a comparison the draft marks as untested must not appear in the title."""
    title = _han(art.get("title"))
    if not title:
        return False
    title_nums = set(_num_cores(title))
    blobs = [_han(art.get("results")), _han(art.get("one_liner")), _han(art.get("design"))]
    for blob in blobs:
        for sent in re.split(r"[。；;\n]", blob):
            if not UNTESTED_RE.search(sent):
                continue
            nums = set(_num_cores(sent))
            if len(nums) >= 2 and len(nums & title_nums) >= 2:
                return True
            if COMPARATIVE_VERDICT_RE.search(title) and COMPARATIVE_VERDICT_RE.search(sent):
                return True
    return False


def headline_comparative_without_power(art: dict) -> bool:
    """H2: comparative verdict in the title when the trial was not designed/powered."""
    title = _han(art.get("title"))
    if not COMPARATIVE_VERDICT_RE.search(title):
        return False
    context = _han(art.get("design")) + _han(art.get("limitations")) + _han(art.get("datacard"))
    if UNTESTED_RE.search(context) or re.search(r"未.*比较|非正式比较", context):
        return True
    if POWERED_RE.search(context):
        return False
    # A comparative title with no power/formal-comparison language is a fail.
    return True


def one_liner_shares_title_basis(art: dict) -> bool:
    title_nums = set(_num_cores(_han(art.get("title"))))
    line_nums = set(_num_cores(_han(art.get("one_liner"))))
    if not title_nums:
        return True
    return bool(title_nums & line_nums)


def primary_endpoint_in_results(art: dict) -> bool:
    dc = art.get("datacard") if isinstance(art.get("datacard"), dict) else {}
    pe = str(dc.get("primary_endpoint_result") or "").strip()
    if not pe:
        return False
    results = _han(art.get("results"))
    cores = _num_cores(pe)
    if cores:
        return any(c in results or c in "".join(_num_cores(results)) for c in cores)
    return pe[:6] in results


def primary_endpoint_paragraph_ok(art: dict) -> bool:
    """D1: definition, analysis n, result, control/threshold, comparison design."""
    dc = art.get("datacard") if isinstance(art.get("datacard"), dict) else {}
    results = art.get("results") or []
    first = _han(results[0] if results else "")
    design = _han(art.get("design"))
    window = first + " " + design + " " + " ".join(f"{k}{v}" for k, v in dc.items() if v)
    has_def = bool(
        re.search(r"主要终点|primary endpoint|终点", window, re.I)
        or dc.get("primary_endpoint")
    )
    has_n = bool(re.search(r"\d+\s*例|n\s*=\s*\d+|分析集", window, re.I) or dc.get("n"))
    has_result = bool(dc.get("primary_endpoint_result") or re.search(r"\d+(?:\.\d+)?\s*%", first))
    has_ctrl = bool(
        re.search(r"对照|阈值|control|placebo|vs\.?|相比", window, re.I)
        or dc.get("control")
    )
    has_stat = bool(
        re.search(r"HR|OR|95\s*%?\s*CI|P\s*[<＝=]|Fleming|检验效能|powered|正式比较|统计学", window, re.I)
        or dc.get("statistics")
        or POWERED_RE.search(window)
        or UNTESTED_RE.search(window)
    )
    return all((has_def, has_n, has_result, has_ctrl, has_stat))


def extract_must_cover(
    abstract: str = "",
    results: str = "",
    fig_captions: str = "",
) -> list[dict[str, str]]:
    """5–12 key results from abstract + Results leads + main figure legends."""
    items: list[dict[str, str]] = []
    seen: set[str] = set()

    def add(sentence: str, loc: str) -> None:
        sent = re.sub(r"\s+", " ", sentence).strip()
        if len(sent) < 12 or not NUM_RE.search(sent):
            return
        cores = tuple(sorted(set(_num_cores(sent))))
        if not cores or cores in seen:
            return
        seen.add(cores)
        items.append({"text": sent[:240], "location": loc, "cores": list(cores)})

    if abstract:
        for sent in re.split(r"(?<=[.!?。])\s+", abstract):
            add(sent, "Abstract")
    if results:
        paras = [p.strip() for p in re.split(r"\n{2,}|(?<=\n)(?=[A-Z\u4e00-\u9fff])", results) if p.strip()]
        for para in paras[:12]:
            lead = re.split(r"(?<=[.!?。])\s+", para)[0]
            add(lead, "Results")
    if fig_captions:
        for sent in re.split(r"(?<=[.!?。])\s+", fig_captions):
            if re.search(r"(?i)surviv|OS|PFS|median|对照|control|volume|冻融", sent):
                add(sent, "Fig")
            elif NUM_RE.search(sent):
                add(sent, "Fig")
    # Prefer survival / endpoint / negative-control sentences, cap 5–12.
    def rank(it: dict) -> int:
        t = it["text"].lower()
        score = 0
        if re.search(r"(?i)primary|主要终点|orr|pfs|os|surviv", t):
            score += 3
        if re.search(r"(?i)control|对照|冻融|vehicle|untreated", t):
            score += 2
        if it["location"] == "Fig":
            score += 1
        return -score

    items.sort(key=rank)
    if len(items) > MUST_COVER_HI:
        items = items[:MUST_COVER_HI]
    return items


def must_cover_coverage(art: dict, items: list[dict]) -> tuple[float, list[str]]:
    """Fraction covered in body, or named as 未写入 in limitations."""
    if not items:
        return 1.0, []
    body = article_plain(art)
    body_nums = set(_num_cores(body))
    lims = _han(art.get("limitations"))
    uncovered: list[str] = []
    covered = 0
    for it in items:
        cores = set(it.get("cores") or _num_cores(it.get("text") or ""))
        named = bool(
            re.search(r"未写入|未收入|未写入正文", lims)
            and (any(c in lims for c in cores) or it["text"][:12] in lims)
        )
        if cores & body_nums or named:
            covered += 1
        else:
            uncovered.append(it.get("text") or "")
    return covered / max(len(items), 1), uncovered


def figure_numbers_subset_of_data_points(art: dict) -> bool:
    """Figure label numbers ⊆ data_points with matching basis. Empty labels pass."""
    labels: list[str] = []
    for key in ("figure_labels", "fig_labels", "fig_numbers"):
        val = art.get(key)
        if isinstance(val, list):
            labels.extend(str(x) for x in val if x)
        elif val:
            labels.append(str(val))
    cfg = art.get("figure_label_cfg") or art.get("label_cfg")
    if isinstance(cfg, dict):
        labels.extend(str(v) for v in cfg.values() if v)
    if not labels:
        return True
    dps = [d for d in (art.get("data_points") or []) if isinstance(d, dict)]
    dp_nums: dict[str, set[str]] = {}
    for dp in dps:
        basis = data_point_basis(dp)
        for c in _num_cores(str(dp.get("value") or "")):
            dp_nums.setdefault(c, set()).add(basis)
    fig_basis = str(art.get("figure_basis") or "body").lower()
    if fig_basis not in ("abstract", "body"):
        fig_basis = "body"
    mixed = art.get("figure_mixed_basis")
    if mixed:
        return False
    for lab in labels:
        cores = _num_cores(lab)
        if not cores:
            continue
        for c in cores:
            if c not in dp_nums:
                return False
            if fig_basis not in dp_nums[c] and "abstract" in dp_nums[c] and fig_basis == "body":
                return False
    bases_used = set()
    for lab in labels:
        for c in _num_cores(lab):
            bases_used |= dp_nums.get(c, set())
    if len(bases_used) > 1:
        return False
    return True


def hedging_upgraded(art: dict, source: str) -> bool:
    """True when the draft upgrades source hedging to 显著/证明."""
    if not source or not HEDGE_SRC_RE.search(source):
        return False
    draft = article_plain(art)
    if not HEDGE_UPGRADE_RE.search(draft):
        return False
    # Allowed if the same window still keeps 趋势/相关/推测.
    for sent in re.split(r"[。；;\n]", draft):
        if HEDGE_UPGRADE_RE.search(sent) and not HEDGE_SRC_RE.search(sent):
            return True
    return False


def field_is_incidental_tool(art: dict) -> bool:
    """Tag must reflect the subject, not incidental use of mice / a delivery route."""
    field = str(art.get("field") or "")
    text = article_plain(art)
    if field in ("f2", "c5"):
        if ANIMAL_INCIDENTAL_RE.search(text) and not ANIMAL_SUBJECT_RE.search(text):
            return True
    if field in ("f7", "c6"):
        if AB_INCIDENTAL_RE.search(text) and not AB_SUBJECT_RE.search(text):
            return True
    return False


def claim_support_rate(audit: dict | None) -> tuple[float, int, int, int]:
    """Returns (rate, supported, unsupported, contradicted). Empty ok audit → 1.0."""
    audit = audit or {}
    claims = audit.get("claims") or []
    supported = unsupported = contradicted = 0
    for cl in claims:
        if not isinstance(cl, dict) or cl.get("factual") is False:
            continue
        label = str(cl.get("label") or "").upper()
        if label == "SUPPORTED":
            supported += 1
        elif label in ("NOT_IN_SOURCE", "UNSUPPORTED"):
            unsupported += 1
        elif label == "CONTRADICTED":
            contradicted += 1
    status = str(audit.get("status") or "ok")
    if not claims:
        if status == "contradicted":
            return 0.0, 0, 0, 1
        if status in ("not_in_source", "error"):
            return 0.0, 0, 1, 0
        return 1.0, 0, 0, 0
    denom = supported + unsupported + contradicted
    rate = (supported / denom) if denom else 1.0
    return rate, supported, unsupported, contradicted


def hard_errors(
    art: dict,
    *,
    source: str = "",
    real_fulltext: bool = False,
    claim_audit: dict | None = None,
    ocr_text: bool = False,
    has_figure: bool | None = None,
    number_problems: list[str] | None = None,
) -> list[dict[str, str]]:
    """H1–H8. Any one blocks publication."""
    errors: list[dict[str, str]] = []
    tier = art.get("tier")
    field = art.get("field")
    if number_problems:
        errors.append({"code": "H1", "detail": number_problems[0][:160]})
    if headline_comparative_without_power(art):
        errors.append({"code": "H2", "detail": "comparative verdict without power/formal comparison"})
    if headline_untested_comparison(art):
        errors.append({"code": "H3", "detail": "untested comparison used as title hook"})
    if not figure_numbers_subset_of_data_points(art):
        errors.append({"code": "H4", "detail": "figure numbers mix or leave the data_point basis"})
    if tier == "deep" and not real_fulltext:
        errors.append({"code": "H5", "detail": "deep/figure without real fulltext Results"})
    rate, _s, uns, con = claim_support_rate(claim_audit)
    if con or uns or rate < CLAIM_RATE_MIN:
        errors.append({"code": "H6", "detail": f"claim support rate={rate:.2f} unsupported={uns} contradicted={con}"})
    allowed = set()
    try:
        from inlight_fields import FIELDS as NEW_FIELDS
        allowed = set(NEW_FIELDS) | {f"c{i}" for i in range(1, 10)}
    except Exception:
        allowed = {f"f{i}" for i in range(1, 10)} | {f"c{i}" for i in range(1, 10)}
    primaries = [field] if field else []
    extra = art.get("primary_field")
    if extra and extra != field:
        primaries.append(extra)
    if not field or field in ("none", "") or field not in allowed or len([p for p in primaries if p]) != 1:
        errors.append({"code": "H7", "detail": f"primary field invalid or not unique: {field}"})
    if field_is_incidental_tool(art):
        errors.append({"code": "H7", "detail": "field tag reflects an incidental tool, not the subject"})
    if tier == "deep":
        skip = art.get("skip_mechanism_figure")
        if has_figure is False or skip is True:
            errors.append({"code": "H8", "detail": "deep article missing mechanism figure"})
        if ocr_text:
            errors.append({"code": "H8", "detail": "OCR detected text on the figure"})
    return errors


def run_automated_audit(
    art: dict,
    *,
    abstract: str = "",
    results_src: str = "",
    fig_captions: str = "",
    methods: str = "",
    source: str = "",
    real_fulltext: bool = False,
    claim_audit: dict | None = None,
    structure_ok: bool = True,
    number_ok: bool = True,
    number_problems: list[str] | None = None,
    ocr_text: bool = False,
    has_figure: bool | None = None,
    rewrite_count: int = 0,
) -> dict[str, Any]:
    """All automated gates. Dual-blind scores are attached later for ≤3 articles."""
    for dp in art.get("data_points") or []:
        if isinstance(dp, dict) and not dp.get("location"):
            loc = infer_location(dp, abstract, results_src, fig_captions, methods)
            if loc:
                dp["location"] = loc
            if not dp.get("basis"):
                dp["basis"] = "abstract" if loc == "Abstract" else "body"

    must = extract_must_cover(abstract, results_src, fig_captions)
    cover, uncovered = must_cover_coverage(art, must)
    loc_ok = True
    for dp in art.get("data_points") or []:
        if not isinstance(dp, dict):
            continue
        loc = infer_location(dp, abstract, results_src, fig_captions, methods)
        if loc:
            dp["location"] = loc
        elif not dp.get("location"):
            # Body/Results is the default basis; missing a tag is stamped, not invented.
            dp["location"] = "Results"
        if not dp.get("basis"):
            dp["basis"] = "abstract" if dp.get("location") == "Abstract" else "body"
        if not dp.get("location"):
            loc_ok = False
            break
    rate, _s, uns, con = claim_support_rate(claim_audit)
    claim_ok = rate >= CLAIM_RATE_MIN and uns == 0 and con == 0
    headline_ok = (
        one_liner_shares_title_basis(art)
        and not headline_untested_comparison(art)
        and not headline_comparative_without_power(art)
    )
    endpoint_ok = primary_endpoint_in_results(art) and primary_endpoint_paragraph_ok(art)
    fig_ok = figure_numbers_subset_of_data_points(art)
    boiler = boilerplate_count(art)
    field_ok = not field_is_incidental_tool(art) and bool(art.get("field"))
    hedge_ok = not hedging_upgraded(art, source)
    hs = hard_errors(
        art,
        source=source,
        real_fulltext=real_fulltext,
        claim_audit=claim_audit,
        ocr_text=ocr_text,
        has_figure=has_figure,
        number_problems=number_problems,
    )
    gates = {
        "gate_fulltext": bool(real_fulltext) if art.get("tier") == "deep" else True,
        "structure": bool(structure_ok),
        "number_trace": bool(number_ok) and loc_ok,
        "claim_support_rate": round(rate, 4),
        "headline_ok": headline_ok,
        "endpoint_hierarchy": endpoint_ok,
        "must_cover_coverage": round(cover, 4),
        "figure_text_consistency": fig_ok,
        "classification": field_ok,
        "boilerplate_count": boiler,
        "hedging_ok": hedge_ok,
    }
    gate_bools = {
        "gate_fulltext": gates["gate_fulltext"],
        "structure": gates["structure"],
        "number_trace": gates["number_trace"],
        "claim_ok": claim_ok,
        "headline_ok": headline_ok,
        "endpoint_hierarchy": endpoint_ok,
        "must_cover": cover >= MUST_COVER_MIN or not must,
        "figure_text_consistency": fig_ok,
        "classification": field_ok,
        "boilerplate": boiler <= BOILERPLATE_MAX,
        "hedging_ok": hedge_ok,
    }
    publish = all(gate_bools.values()) and not hs
    return {
        **gates,
        "must_cover": must,
        "must_cover_uncovered": uncovered,
        "hard_errors": hs,
        "blind_scores": [],
        "rewrite_count": rewrite_count,
        "publish_allowed": bool(publish),
        "gate_bools": gate_bools,
    }


def pick_top_deep_for_blind(articles: list[dict], limit: int = BLIND_MAX_ARTICLES) -> list[dict]:
    deep = [a for a in articles if a.get("tier") == "deep"]

    def key(a: dict) -> tuple:
        dps = a.get("data_points") or []
        return (len(dps), len(_han(a.get("results"))))

    deep.sort(key=key, reverse=True)
    return deep[: max(0, limit)]


def _parse_blind_payload(text: str) -> dict[str, Any]:
    scores = {k: 0 for k in BLIND_DIMS}
    major = True
    reasons = text or "empty"
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", text or "", re.S)
        data = json.loads(m.group(0)) if m else {}
    if isinstance(data, dict):
        raw = data.get("scores") or data
        for k in BLIND_DIMS:
            try:
                scores[k] = int(raw.get(k, 0))
            except (TypeError, ValueError):
                scores[k] = 0
        major = bool(data.get("major_factual_error") or data.get("factual_mismatch"))
        reasons = str(data.get("reasons") or data.get("reason") or reasons)
    overall = sum(scores.values()) / max(len(BLIND_DIMS), 1)
    passed = (not major) and all(scores[k] >= BLIND_MIN for k in BLIND_DIMS) and overall >= BLIND_MIN
    return {
        "pass": passed,
        "scores": scores,
        "overall": round(overall, 2),
        "major_factual_error": major,
        "reasons": reasons,
    }


def score_blind_gemini(art: dict, source: str, config: dict | None) -> dict[str, Any]:
    cfg = config or {}
    key = load_gemini_api_key(cfg)
    if not key:
        return {"available": False, "pass": False, "judge": "gemini", "reasons": "GEMINI_API_KEY missing"}
    model = cfg.get("gemini_model") or os.environ.get("GEMINI_MODEL") or DEFAULT_GEMINI_MODEL
    drafted = json.dumps({
        "title": art.get("title"),
        "one_liner": art.get("one_liner"),
        "design": art.get("design"),
        "results": art.get("results"),
        "mechanism": art.get("mechanism"),
        "limitations": art.get("limitations"),
        "significance": art.get("significance"),
    }, ensure_ascii=False)
    prompt = f"""Score this anonymous Chinese scientific brief. Ignore style and whether it resembles ACIR.
Return JSON only:
{{"scores": {{"accuracy":1-10,"information":1-10,"understanding":1-10,"exposition":1-10,"figure_information":1-10,"significance":1-10}},
"major_factual_error": true/false, "reasons": "short note"}}
Do not score writing style.

## Article
{drafted[:FULLTEXT_WINDOW]}

## Source excerpt
{(source or "")[:FULLTEXT_WINDOW]}
"""
    try:
        raw = _gemini_generate(prompt, model, key)
        out = _parse_blind_payload(raw)
        out["available"] = True
        out["judge"] = "gemini"
        out["model"] = model
        return out
    except Exception as exc:
        logging.error("Gemini blind judge failed: %s", type(exc).__name__)
        return {"available": False, "pass": False, "judge": "gemini", "reasons": type(exc).__name__}


def score_blind_claude(art: dict, source: str, config: dict | None = None) -> dict[str, Any]:
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return {"available": False, "pass": False, "judge": "claude", "reasons": "ANTHROPIC_API_KEY missing"}
    drafted = json.dumps({
        "title": art.get("title"),
        "one_liner": art.get("one_liner"),
        "design": art.get("design"),
        "results": art.get("results"),
        "mechanism": art.get("mechanism"),
        "limitations": art.get("limitations"),
        "significance": art.get("significance"),
    }, ensure_ascii=False)
    prompt = f"""Score this anonymous Chinese scientific brief. Ignore style and whether it resembles ACIR.
Return JSON only:
{{"scores": {{"accuracy":1-10,"information":1-10,"understanding":1-10,"exposition":1-10,"figure_information":1-10,"significance":1-10}},
"major_factual_error": true/false, "reasons": "short note"}}

## Article
{drafted[:FULLTEXT_WINDOW]}

## Source excerpt
{(source or "")[:FULLTEXT_WINDOW]}
"""
    try:
        from anthropic import Anthropic
        from inlight_articles import _claude_create

        message = _claude_create(
            Anthropic(),
            model=os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-5-5"),
            max_tokens=800,
            messages=[{"role": "user", "content": prompt}],
        )
        text = ""
        for block in getattr(message, "content", None) or []:
            if getattr(block, "type", None) == "text":
                text += str(getattr(block, "text", "") or "")
        out = _parse_blind_payload(text)
        out["available"] = True
        out["judge"] = "claude"
        return out
    except Exception as exc:
        logging.error("Claude blind judge failed: %s", type(exc).__name__)
        return {"available": False, "pass": False, "judge": "claude", "reasons": type(exc).__name__}


def combine_blind_judges(claude: dict, gemini: dict) -> dict[str, Any]:
    """Fail if any available judge any dim <7, overall <7, or major error.
    Both unavailable → fail closed. Style is ignored (not in the payload).
    """
    available = [j for j in (claude, gemini) if j.get("available")]
    if not available:
        return {
            "pass": False,
            "fail_closed": True,
            "reasons": "both blind judges unavailable",
            "judges": [claude, gemini],
        }
    failed = False
    reasons = []
    for j in available:
        if j.get("major_factual_error"):
            failed = True
            reasons.append(f"{j.get('judge')}: major factual error")
        scores = j.get("scores") or {}
        for dim, val in scores.items():
            if dim in BLIND_DIMS and int(val or 0) < BLIND_MIN:
                failed = True
                reasons.append(f"{j.get('judge')} {dim}={val}<{BLIND_MIN}")
        if float(j.get("overall") or 0) < BLIND_MIN:
            failed = True
            reasons.append(f"{j.get('judge')} overall={j.get('overall')}<{BLIND_MIN}")
        if j.get("pass") is False and not j.get("scores"):
            failed = True
            reasons.append(str(j.get("reasons") or "judge fail"))
    return {
        "pass": not failed,
        "fail_closed": False,
        "reasons": "; ".join(reasons) or "ok",
        "judges": [claude, gemini],
    }


def attach_audit_fields(entry: dict, audit: dict) -> dict:
    """qc_report.json fields from audit_requirements §4."""
    entry["gate_fulltext"] = audit.get("gate_fulltext")
    entry["structure"] = audit.get("structure")
    entry["number_trace"] = audit.get("number_trace")
    entry["claim_support_rate"] = audit.get("claim_support_rate")
    entry["headline_ok"] = audit.get("headline_ok")
    entry["endpoint_hierarchy"] = audit.get("endpoint_hierarchy")
    entry["must_cover_coverage"] = audit.get("must_cover_coverage")
    entry["figure_text_consistency"] = audit.get("figure_text_consistency")
    entry["classification"] = audit.get("classification")
    entry["boilerplate_count"] = audit.get("boilerplate_count")
    entry["blind_scores"] = audit.get("blind_scores") or []
    entry["hard_errors"] = audit.get("hard_errors") or []
    entry["rewrite_count"] = audit.get("rewrite_count") or 0
    entry["publish_allowed"] = bool(audit.get("publish_allowed"))
    if not entry["publish_allowed"]:
        entry["published"] = False
    return entry


def _source_from_enriched(item: Any) -> str:
    if item is None:
        return ""
    if isinstance(item, dict):
        return "\n".join(
            str(item.get(k) or "")
            for k in ("abstract", "fulltext_results", "fig_captions", "methods_design")
        )
    return "\n".join(
        str(getattr(item, k, "") or "")
        for k in ("abstract", "fulltext_results", "fig_captions", "methods_design")
    )


def _patch_qc_blind(stats: dict | None, art: dict, combo: dict, rewrite_count: int, published: bool) -> None:
    if not stats:
        return
    url = art.get("url")
    audit = dict(art.get("writing_audit") or {})
    audit["blind_scores"] = combo.get("judges") or []
    audit["rewrite_count"] = rewrite_count
    audit["publish_allowed"] = bool(published) and bool(audit.get("publish_allowed", True))
    art["writing_audit"] = audit
    for entry in (stats.get("qc_report") or {}).get("articles") or []:
        if entry.get("url") == url:
            attach_audit_fields(entry, audit)
            entry["published"] = bool(published) and bool(entry.get("published", True))
            return


def apply_dual_blind_to_week(
    articles: list[dict],
    stats: dict | None,
    config: dict | None,
    url_to_enriched: dict | None = None,
    *,
    score_claude=None,
    score_gemini=None,
    redraft=None,
) -> list[dict]:
    """Score at most 3 top deep articles. Fail-closed if both judges are down.

    Automated gates already ran on every article. Style is never scored.
    Max 2 rewrites; still failing → unpublish that article.
    """
    if not acir_strict(config):
        return articles
    score_claude = score_claude or score_blind_claude
    score_gemini = score_gemini or score_blind_gemini
    selected = pick_top_deep_for_blind(articles)
    selected_urls = {a.get("url") for a in selected}
    kept: list[dict] = []
    for art in articles:
        if art.get("url") not in selected_urls:
            kept.append(art)
            continue
        item = (url_to_enriched or {}).get(art.get("url"))
        source = _source_from_enriched(item)
        current = art
        rewrite_count = int(current.get("rewrite_count") or 0)
        while True:
            claude = score_claude(current, source, config)
            gemini = score_gemini(current, source, config)
            combo = combine_blind_judges(claude, gemini)
            current.setdefault("writing_audit", {})
            current["writing_audit"]["blind_scores"] = combo.get("judges")
            current["rewrite_count"] = rewrite_count
            if combo.get("pass"):
                _patch_qc_blind(stats, current, combo, rewrite_count, published=True)
                kept.append(current)
                break
            if combo.get("fail_closed") or rewrite_count >= BLIND_MAX_REWRITES or redraft is None:
                reason = (
                    "both blind judges unavailable"
                    if combo.get("fail_closed")
                    else f"blind judge: {combo.get('reasons') or 'fail'}"
                )
                _patch_qc_blind(stats, current, combo, rewrite_count, published=False)
                if stats is not None:
                    stats.setdefault("drops", []).append({"url": current.get("url"), "reason": reason})
                break
            nxt = redraft(current, combo.get("reasons") or "blind judge fail")
            if not nxt:
                _patch_qc_blind(stats, current, combo, rewrite_count, published=False)
                if stats is not None:
                    stats.setdefault("drops", []).append({
                        "url": current.get("url"),
                        "reason": "blind rewrite failed",
                    })
                break
            rewrite_count += 1
            nxt["rewrite_count"] = rewrite_count
            current = nxt
    if stats is not None:
        stats["published_deep"] = sum(1 for a in kept if a.get("tier") == "deep")
        stats["published_brief"] = sum(1 for a in kept if a.get("tier") == "brief")
        stats["dropped"] = len(stats.get("drops") or [])
    return kept
