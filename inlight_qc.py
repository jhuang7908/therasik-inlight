#!/usr/bin/env python3
"""ACIR admission, structure QC, Gemini review, and data-chart helpers.

Writing and number/claim verification stay on Claude (Anthropic).
Gemini only scores a finished deep 解读 against the ACIR standard.
"""

from __future__ import annotations

import json
import logging
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

HAN_RE = re.compile(r"[\u4e00-\u9fff]")
WORD_RE = re.compile(r"[A-Za-z]+(?:-[A-Za-z]+)?")
STUB_RE = re.compile(
    r"(?i)subscribe to (?:read|access)|buy this article|"
    r"log ?in to (?:read|access)|purchase (?:pdf|access)|"
    r"access this article|get access|this article is available to subscribers|"
    r"full text is available to|accept (?:all )?cookies"
)
PARA_SPLIT_RE = re.compile(r"(?<=[。！？\n])")

MIN_RESULTS_WORDS = 1500
MIN_RESULTS_HAN = 2000
GEMINI_SCORE_KEYS = (
    "structure_completeness",
    "depth",
    "quantitative_density",
    "mechanism_vs_speculation",
    "specific_limitations",
    "source_traceability",
)
GEMINI_PASS_MIN = 7
DATACARD_REQUIRED = (
    "study_type", "n", "control", "intervention", "followup",
    "primary_endpoint", "primary_endpoint_result", "statistics", "safety",
)
SECTION_RANGES = {
    "title": (20, 40),
    "one_liner": (40, 70),
    "background": (180, 240),
    "design": (200, 280),
    "results": (500, 700),
    "mechanism": (250, 350),
    "limitations": (200, 280),
    "significance": (150, 220),
    "citation": (80, 140),
}
HOUSE_STYLE = (
    "journal-grade semi-realistic 3D scientific illustration, "
    "palette #EEF2F0 #0F6B5C #9FD8CB #D6DEDB #5C6B67 accent #C0492F, "
    "subject occupies about 72 percent of the frame, visual weight on "
    "the 0.382 or 0.618 line, no text, no labels, no words, no letters"
)
FIG_DISCLAIMER = "示意图由 AI 生成，依据原文结果绘制，非期刊原图，不代表分子比例。"


def acir_strict(config: dict | None) -> bool:
    """Production sources.yaml sets min_deep. Acceptance fixtures do not."""
    cfg = config or {}
    return bool(cfg.get("acir_qc")) or "min_deep" in cfg


def han_len(text: Any) -> int:
    if isinstance(text, list):
        return sum(han_len(x) for x in text)
    return len(HAN_RE.findall(str(text or "")))


def english_word_count(text: str) -> int:
    return len(WORD_RE.findall(text or ""))


def paragraphs(text: Any) -> list[str]:
    if isinstance(text, list):
        out: list[str] = []
        for item in text:
            out.extend(paragraphs(item))
        return out
    raw = str(text or "").strip()
    if not raw:
        return []
    parts = [p.strip() for p in PARA_SPLIT_RE.split(raw) if p.strip()]
    return parts or [raw]


def is_real_results_text(text: str) -> bool:
    """True only for real Results prose, not a landing page / abstract / stub."""
    if not text or not str(text).strip():
        return False
    blob = str(text)
    words = english_word_count(blob)
    han = han_len(blob)
    if STUB_RE.search(blob) and words < 3000:
        return False
    if words >= MIN_RESULTS_WORDS:
        return True
    if han >= MIN_RESULTS_HAN and (words + han) >= MIN_RESULTS_WORDS:
        return True
    return False


def item_has_real_fulltext(item: Any) -> bool:
    level = getattr(item, "evidence_level", None) or (item.get("evidence_level") if isinstance(item, dict) else "")
    results = getattr(item, "fulltext_results", None) or (item.get("fulltext_results") if isinstance(item, dict) else "")
    return level == "fulltext" and is_real_results_text(results or "")


def record_fulltext(
    item: Any,
    results: str,
    methods: str = "",
    figs: str = "",
    source_label: str = "",
) -> bool:
    """Set evidence_level=fulltext only when Results text is real. Mutates item."""
    if not is_real_results_text(results):
        words = english_word_count(results or "")
        if results:
            item.source_trace.append(
                f"rejected non-Results/stub ({words} words) from {source_label or 'source'}"
            )
        return False
    item.fulltext_results = (results or "")[:20000]
    if methods:
        item.methods_design = methods[:4000]
    if figs:
        item.fig_captions = figs[:6000]
    item.evidence_level = "fulltext"
    sections = {
        "results": {
            "chars": len(item.fulltext_results),
            "words": english_word_count(item.fulltext_results),
        },
        "methods": {
            "chars": len(getattr(item, "methods_design", "") or ""),
            "words": english_word_count(getattr(item, "methods_design", "") or ""),
        },
        "fig_captions": {
            "chars": len(getattr(item, "fig_captions", "") or ""),
            "words": english_word_count(getattr(item, "fig_captions", "") or ""),
        },
    }
    item.sections_read = sections
    bits = [name for name, key in (
        ("Results", "results"), ("Methods", "methods"), ("图注", "fig_captions"),
    ) if sections[key]["chars"]]
    pmcid = getattr(item, "pmcid", "") or ""
    if pmcid:
        note = f"读了 PMC 全文 {pmcid} 的 {'/'.join(bits)}"
    else:
        note = f"读了 {source_label or 'OA'} 全文的 {'/'.join(bits)}"
    item.read_note = note
    item.source_trace.append(f"{note}（Results {sections['results']['words']} words）")
    return True


def verifier_source_text(item: Any, fallback: str) -> str:
    """Claim/number verifier reads the full text that was actually fetched."""
    if item_has_real_fulltext(item):
        parts = [
            getattr(item, "fulltext_results", "") or "",
            getattr(item, "methods_design", "") or "",
            getattr(item, "fig_captions", "") or "",
        ]
        return "\n".join(p for p in parts if p)
    return fallback or ""


def _parse_env_line(line: str) -> tuple[str, str] | None:
    raw = line.strip()
    if not raw or raw.startswith("#") or "=" not in raw:
        return None
    if raw.startswith("export "):
        raw = raw[7:].strip()
    key, _, val = raw.partition("=")
    key = key.strip()
    val = val.strip().strip("'").strip('"')
    if not key:
        return None
    return key, val


def _read_gemini_key_from_file(path: str) -> str:
    """Read-only: return GEMINI_API_KEY from a dotenv-style file. Never log contents."""
    if not path:
        return ""
    try:
        with open(path, "r", encoding="utf-8") as fh:
            for line in fh:
                parsed = _parse_env_line(line)
                if parsed and parsed[0] == "GEMINI_API_KEY" and parsed[1]:
                    return parsed[1]
    except OSError:
        logging.warning("无法读取额外环境文件（不记录路径或内容）")
    return ""


def load_gemini_api_key(config: dict | None = None) -> str:
    """GEMINI_API_KEY from the process env, else a read-only extra env file.

    Extra file path: config extra_env_file or INLIGHT_EXTRA_ENV_FILE.
    Never writes the file. Never logs the key or file contents.
    """
    key = (os.environ.get("GEMINI_API_KEY") or "").strip()
    if key:
        return key
    cfg = config or {}
    path = (cfg.get("extra_env_file") or os.environ.get("INLIGHT_EXTRA_ENV_FILE") or "").strip()
    return _read_gemini_key_from_file(path)


def apply_extra_env_gemini_key(config: dict | None = None) -> bool:
    """Copy GEMINI_API_KEY from the extra file into os.environ if missing."""
    if (os.environ.get("GEMINI_API_KEY") or "").strip():
        return True
    key = load_gemini_api_key(config)
    if key:
        os.environ["GEMINI_API_KEY"] = key
        logging.info("已从额外环境文件读取 GEMINI_API_KEY（不记录内容）")
        return True
    return False


def mechanism_image_prompt(results_text: str, mechanism: str = "") -> str:
    """House-style mechanism figure prompt from full-text Results, not the abstract."""
    blob = f"{results_text or ''} {mechanism or ''}"
    words = [w for w in WORD_RE.findall(blob) if len(w) >= 4]
    stop = {
        "this", "that", "with", "from", "were", "been", "have", "that",
        "study", "result", "results", "using", "these", "those", "into",
    }
    keys = [w for w in words if w.lower() not in stop][:8]
    subject = ", ".join(keys) if keys else "cellular signaling cascade"
    return f"{subject}, {HOUSE_STYLE}"


def secondhand_label(item: Any) -> str:
    level = getattr(item, "evidence_level", "") or ""
    source = getattr(item, "source", "") or "来源"
    if level == "press":
        return f"二手：未读原文，信息来自{source}新闻稿"
    if level == "preprint":
        return f"二手：仅读摘要（预印本），未读原文"
    return f"二手：仅读摘要，未读原文"


def validate_acir_structure(art: dict) -> list[str]:
    """Section 1 of the binding spec. Deep articles only."""
    problems: list[str] = []
    if art.get("tier") != "deep":
        return problems

    def check_range(name: str, text: Any) -> None:
        lo, hi = SECTION_RANGES[name]
        n = han_len(text)
        if n < lo or n > hi:
            problems.append(f"{name} 字数 {n}，要求 {lo}–{hi}")

    check_range("title", art.get("title"))
    check_range("one_liner", art.get("one_liner"))
    check_range("background", art.get("background"))
    check_range("design", art.get("design"))
    check_range("results", art.get("results"))
    check_range("mechanism", art.get("mechanism"))
    check_range("limitations", art.get("limitations"))
    check_range("significance", art.get("significance"))
    citation = art.get("citation") or ""
    if citation:
        check_range("citation", citation)

    body = han_len([
        art.get("one_liner"), art.get("background"), art.get("design"),
        art.get("results"), art.get("mechanism"), art.get("limitations"),
        art.get("significance"),
    ])
    if body < 1400 or body > 1900:
        problems.append(f"deep 正文 {body} 汉字，要求 1400–1900")

    results = art.get("results") or []
    if not isinstance(results, list) or not (3 <= len(results) <= 5):
        problems.append("核心结果须为 3–5 段")
    else:
        for i, para in enumerate(results, 1):
            if not re.search(r"\d", str(para)):
                problems.append(f"结果第 {i} 段缺少具体数字")

    lims = art.get("limitations") or []
    if not isinstance(lims, list) or len(lims) < 3:
        problems.append("局限须至少 3 条")

    for para in paragraphs([
        art.get("background"), art.get("design"), art.get("results"),
        art.get("mechanism"), art.get("significance"),
    ]):
        n = han_len(para)
        if n > 150:
            problems.append(f"段落超过 150 字（{n}）：{para[:20]}")

    dc = art.get("datacard")
    if not isinstance(dc, dict):
        problems.append("数据卡缺失")
    else:
        for key in DATACARD_REQUIRED:
            if not str(dc.get(key) or "").strip():
                problems.append(f"数据卡缺 {key}")

    if art.get("evidence_level") != "fulltext":
        problems.append("深度解读要求 evidence_level=fulltext")
    return problems


def _quote_in_source(quote: str, source: str) -> bool:
    if not quote or not source:
        return False
    nq = re.sub(r"\s+", " ", quote).strip()
    ns = re.sub(r"\s+", " ", source).strip()
    return bool(nq) and nq in ns


def verified_data_points(art: dict, source: str) -> list[dict]:
    """data_points whose source_quote is a literal substring of the read text."""
    out = []
    for dp in art.get("data_points") or []:
        if not isinstance(dp, dict):
            continue
        quote = str(dp.get("source_quote") or "")
        value = str(dp.get("value") or "")
        if value and quote and _quote_in_source(quote, source) and value.replace(" ", "") in quote.replace(" ", ""):
            out.append(dp)
    return out


def comparable_verified_points(art: dict, source: str) -> list[dict]:
    """≥2 verified points that share a unit or meaning, for a code-drawn chart."""
    verified = verified_data_points(art, source)
    groups: dict[str, list[dict]] = {}
    for dp in verified:
        meaning = str(dp.get("meaning") or "").strip().lower()
        value = str(dp.get("value") or "")
        unit = ""
        m = re.search(r"(%|％|例|名|mg|kg|个月|周|天|年|倍)", value)
        if m:
            unit = m.group(1)
        key = unit or meaning
        if not key:
            continue
        groups.setdefault(key, []).append(dp)
    for pts in groups.values():
        if len(pts) >= 2:
            return pts
    return []


def render_data_chart_svg(points: list[dict], title: str = "") -> str:
    """Simple bar chart from verified values. Not an image-model drawing."""
    parsed: list[tuple[str, float]] = []
    for dp in points:
        raw = str(dp.get("value") or "")
        m = re.search(r"(\d+(?:\.\d+)?)", raw.replace(",", ""))
        if not m:
            continue
        label = str(dp.get("meaning") or raw)[:12]
        parsed.append((label, float(m.group(1))))
    if len(parsed) < 2:
        return ""
    width, height, pad = 360, 180, 36
    vmax = max(v for _, v in parsed) or 1
    bar_w = (width - 2 * pad) / len(parsed)
    bars = []
    for i, (label, val) in enumerate(parsed):
        h = max(4, (val / vmax) * (height - 2 * pad))
        x = pad + i * bar_w + 8
        y = height - pad - h
        bars.append(
            f'<rect x="{x:.1f}" y="{y:.1f}" width="{bar_w-16:.1f}" height="{h:.1f}" fill="#0F6B5C"/>'
            f'<text x="{x + (bar_w-16)/2:.1f}" y="{height-12}" text-anchor="middle" '
            f'font-size="10" fill="#5C6B67">{_svg_escape(label)}</text>'
            f'<text x="{x + (bar_w-16)/2:.1f}" y="{y-4:.1f}" text-anchor="middle" '
            f'font-size="10" fill="#0F6B5C">{val:g}</text>'
        )
    cap = _svg_escape(title or "核对后的关键对比")
    return (
        f'<svg class="data-chart" viewBox="0 0 {width} {height}" '
        f'xmlns="http://www.w3.org/2000/svg" role="img" aria-label="{cap}">'
        f'<rect width="{width}" height="{height}" fill="#EEF2F0"/>'
        + "".join(bars)
        + f'<text x="{pad}" y="16" font-size="11" fill="#5C6B67">{cap}</text>'
        "</svg>"
    )


def _svg_escape(text: str) -> str:
    return (
        str(text or "")
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def _gemini_generate(prompt: str, model: str, api_key: str) -> str:
    """Call Gemini generateContent. Never log the key or the request URL."""
    url = (
        "https://generativelanguage.googleapis.com/v1beta/models/"
        + urllib.parse.quote(model, safe=".-")
        + ":generateContent"
    )
    body = json.dumps({
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 0, "responseMimeType": "application/json"},
    }).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "x-goog-api-key": api_key,
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        logging.error("Gemini 审核请求失败（HTTP %s，不记录密钥）", e.code)
        raise
    except Exception:
        logging.error("Gemini 审核请求失败（不记录密钥）")
        raise
    candidates = payload.get("candidates") or []
    parts = ((candidates[0] or {}).get("content") or {}).get("parts") or []
    return str((parts[0] or {}).get("text") or "")


def _parse_gemini_review(text: str) -> dict:
    scores = {k: 0 for k in GEMINI_SCORE_KEYS}
    mismatch = True
    reasons = text or "empty Gemini response"
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", text or "", re.S)
        data = json.loads(m.group(0)) if m else {}
    if isinstance(data, dict):
        raw_scores = data.get("scores") or data
        for k in GEMINI_SCORE_KEYS:
            try:
                scores[k] = int(raw_scores.get(k, 0))
            except (TypeError, ValueError):
                scores[k] = 0
        mismatch = bool(data.get("factual_mismatch", True))
        reasons = str(data.get("reasons") or data.get("reason") or reasons)
    passed = (not mismatch) and all(scores[k] >= GEMINI_PASS_MIN for k in GEMINI_SCORE_KEYS)
    return {"pass": passed, "scores": scores, "factual_mismatch": mismatch, "reasons": reasons}


def gemini_review_deep(art: dict, fulltext: str, config: dict | None) -> dict:
    """Independent Gemini ACIR review. Writing stays on Claude. Fail-closed if no key."""
    cfg = config or {}
    key = load_gemini_api_key(cfg)
    if not key:
        logging.error("GEMINI_API_KEY 缺失，深度解读质控闭门失败（未放宽核对）")
        return {
            "pass": False,
            "scores": {k: 0 for k in GEMINI_SCORE_KEYS},
            "factual_mismatch": True,
            "reasons": "GEMINI_API_KEY missing; deep QC fail-closed",
            "skipped": False,
        }
    model = cfg.get("gemini_model") or os.environ.get("GEMINI_MODEL") or "gemini-2.5-flash"
    drafted = json.dumps({
        "title": art.get("title"),
        "one_liner": art.get("one_liner"),
        "background": art.get("background"),
        "design": art.get("design"),
        "results": art.get("results"),
        "mechanism": art.get("mechanism"),
        "limitations": art.get("limitations"),
        "significance": art.get("significance"),
        "datacard": art.get("datacard"),
    }, ensure_ascii=False)
    prompt = f"""You score one Chinese deep analysis against the ACIR (acir.org) weekly standard.
Return JSON only:
{{"scores": {{"structure_completeness": 1-10, "depth": 1-10, "quantitative_density": 1-10,
"mechanism_vs_speculation": 1-10, "specific_limitations": 1-10, "source_traceability": 1-10}},
"factual_mismatch": true/false, "reasons": "short English or Chinese note"}}
Pass requires every score >= 7 and factual_mismatch false.
Compare the article to the FULL TEXT excerpt. Flag any number or claim not in the excerpt.

## Article
{drafted[:12000]}

## Full text excerpt
{(fulltext or "")[:12000]}
"""
    try:
        raw = _gemini_generate(prompt, model, key)
        out = _parse_gemini_review(raw)
        out["model"] = model
        out["skipped"] = False
        return out
    except Exception as e:
        logging.error("Gemini 审核异常，深度解读闭门失败：%s", type(e).__name__)
        return {
            "pass": False,
            "scores": {k: 0 for k in GEMINI_SCORE_KEYS},
            "factual_mismatch": True,
            "reasons": f"Gemini error: {type(e).__name__}",
            "skipped": False,
        }


def empty_check(name: str, ok: bool, reason: str = "") -> dict:
    return {"name": name, "pass": bool(ok), "reason": reason}


def assemble_qc_entry(
    url: str,
    published: bool,
    checks: list[dict],
    extra: dict | None = None,
) -> dict:
    entry = {
        "url": url,
        "published": bool(published) and all(c.get("pass") for c in checks),
        "checks": checks,
    }
    if extra:
        entry.update(extra)
    return entry
