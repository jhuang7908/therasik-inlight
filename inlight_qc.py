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
import struct
import urllib.error
import urllib.parse
import urllib.request
import zlib
from pathlib import Path
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
FULLTEXT_WINDOW = 20000
_RESULTS_TITLE = r"results?(?:\s+and\s+discussion)?"
_METHODS_TITLE = (
    r"(?:materials?\s+and\s+methods|methods?(?:\s+and\s+materials)?|"
    r"experimental\s+procedures?|experimental\s+design)"
)
_NEXT_MAJOR = r"(?:discussion|references|acknowledg|funding|conclusion|bibliography|methods)"
_AFTER_METHODS = r"(?:results?|discussion|references|acknowledg|funding|conclusion)"
LANDING_RE = re.compile(
    r"(?i)subscribe to (?:read|access)|buy this article|"
    r"log ?in to (?:read|access)|purchase (?:pdf|access|this article)|"
    r"access this article|get access|this article is available to subscribers|"
    r"full text is available to|accept (?:all )?cookies|"
    r"news feature|news & views|research highlight|"
    r"download pdf to view|this is a preview|"
    r"the copyright holder for this preprint|this article is a preprint|"
    r"full text html|subject areas?"
)
_PAGE_MARKERS_RE = re.compile(
    r"(?i)<html|<body|<nav\b|authors?\s+and\s+affiliations|subject areas?|"
    r"download pdf|full text html|the copyright holder for this preprint|"
    r"this article is a preprint"
)
CHROME_HEAD_RE = re.compile(
    r"(?is)(?:^|\n)\s*(?:references?|bibliography|acknowledg(?:e)?ments?|"
    r"funding|author contributions?|competing interests?|ethics(?:\s+statement)?|"
    r"supplementary(?:\s+information)?|subject index|keywords?|"
    r"affiliations?|corresponding author|authors?\s+and\s+affiliations|"
    r"copyright|privacy|related articles|cited by)\s*(?:\n|:)"
)
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
# House style v3 prefix/suffix — verbatim from the approved house_style_v3.md.
IMAGE_PREFIX = (
    "Polished BioRender-style scientific schematic, the quality of a graphical "
    "abstract or mechanism figure in a Nature or Cell paper. Standard crisp "
    "scientific icons (cells, membranes, receptors, Y-shaped antibodies, DNA, "
    "organoids, mice, organ-on-chips) drawn with smooth flat fills, gentle soft "
    "gradients and subtle shading, thin clean dark slate-green outlines of one "
    "consistent line weight, crisp vector edges. Plain pure white background "
    "(#FFFFFF), completely empty: no scene, no floor, no shadows on a ground, "
    "no vignette, no panels or boxes behind the figure. The figure explains one "
    "mechanism as a clear process of 2 to 4 stages connected by simple thin "
    "slate-grey arrows (#5C6B67). Colour palette strictly limited to the house "
    "greens - deep green #0F6B5C, mid green #2F7D6D, pale mint #9FD8CB, light "
    "grey-green #D6DEDB, slate grey #5C6B67 - plus neutral light greys and "
    "white; exactly ONE key element is terracotta #C0492F (lighter terracotta "
    "#E07A5F only for its shading); it is drawn large, solid and boldly filled "
    "- the largest and heaviest single object in the figure - occupying about "
    "6 percent of the whole image area, while the green parts stay lighter and "
    "more delicate."
)
IMAGE_SUFFIX = (
    "Composition: one cohesive figure group spanning about three quarters of "
    "the image width and kept inside the central 85 percent of the canvas, "
    "every cell and object drawn complete with generous empty white margins on "
    "every side, nothing touching or cut by the image edge, no stray elements "
    "near the edges. Professional, precise, restrained and scientifically "
    "accurate, like a figure made by a professional scientific illustrator in "
    "BioRender. Not childish clip art, no cartoon characters, no faces or "
    "eyes, not photorealistic, not a 3D render, no glossy product shot, no "
    "dramatic lighting, no glow, no dark background. No other hues anywhere: "
    "no red, orange, pink, yellow, blue or purple except the single terracotta "
    "element. Absolutely no text, no letters, no numbers, no symbols, no plus "
    "or minus signs, no labels, no legends, no titles, no panel letters, no "
    "logos, no watermark, no frame, no border."
)
HOUSE_STYLE = IMAGE_PREFIX + IMAGE_SUFFIX
PHI = (1 + 5 ** 0.5) / 2
G1, G2 = 1 - 1 / PHI, 1 / PHI
GOLDEN_POS = ("UR", "LL", "LM", "RM", "TM", "BM", "UL", "LR")
GOLDEN_XY = {
    "UL": (G1, G1), "UR": (G2, G1), "LL": (G1, G2), "LR": (G2, G2),
    "RM": (G2, 0.5), "LM": (G1, 0.5), "TM": (0.5, G1), "BM": (0.5, G2),
}
GOLDEN_PLACEMENTS = (
    "upper-right (x=0.618, y=0.382)",
    "lower-left (x=0.382, y=0.618)",
    "lower-middle (x=0.500, y=0.618)",
    "right-middle (x=0.618, y=0.500)",
    "top-middle (x=0.500, y=0.382)",
    "bottom-middle (x=0.500, y=0.618)",
    "upper-left (x=0.382, y=0.382)",
    "lower-right (x=0.618, y=0.618)",
)
DEFAULT_GEMINI_MODEL = "gemini-3.1-pro-preview"
IMAGE_MODELS = ("gpt-image-2", "gpt-image-1")
IMAGE_GEN_SIZE = "1536x1024"
IMAGE_OUT_SIZE = (1600, 989)
SUBJECT_TARGET = 0.72
DEDUPE_FAIL = 10
FIG_DISCLAIMER = "示意图由 AI 生成，依据原文结果绘制，非期刊原图，不代表分子比例。"
ACCENT_RGB = (0xC0, 0x49, 0x2F)
ACCENT_SHADE_RGB = (0xE0, 0x7A, 0x5F)
FILL_RANGE = (0.68, 0.78)
ACCENT_RANGE = (0.03, 0.06)
GOLDEN_DIST_MAX = 0.06
DEAD_CENTER_TOL = 0.04
IMAGE_REGEN_LIMIT = 2
FALLBACK_COVER_SIZE = (1600, 989)  # 1.618:1


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


def strip_page_chrome(text: str) -> str:
    """Drop nav, authors, affiliations, references, funding, indexes, site chrome."""
    blob = str(text or "")
    blob = re.sub(r"(?is)<nav\b.*?</nav>", " ", blob)
    blob = re.sub(r"(?is)<header\b.*?</header>", " ", blob)
    blob = re.sub(r"(?is)<footer\b.*?</footer>", " ", blob)
    blob = re.sub(
        r"(?is)<(?:div|section|aside)[^>]*(?:id|class)=[\"'][^\"']*"
        r"(?:nav|menu|footer|cookie|author|affiliat|reference|funding|sidebar|toolbar)"
        r"[^\"']*[\"'][^>]*>.*?</(?:div|section|aside)>",
        " ",
        blob,
    )
    cut = CHROME_HEAD_RE.search(blob)
    if cut:
        blob = blob[: cut.start()]
    return blob


def html_visible_text(html: str) -> str:
    raw = str(html or "")
    raw = re.sub(r"(?is)<script[^>]*>.*?</script>", " ", raw)
    raw = re.sub(r"(?is)<style[^>]*>.*?</style>", " ", raw)
    raw = re.sub(r"(?is)<noscript[^>]*>.*?</noscript>", " ", raw)
    raw = re.sub(r"(?is)<!--.*?-->", " ", raw)
    raw = re.sub(r"(?is)<[^>]+>", " ", raw)
    raw = raw.replace("&nbsp;", " ").replace("&amp;", "&")
    raw = raw.replace("&lt;", "<").replace("&gt;", ">")
    return re.sub(r"\s+", " ", raw).strip()


def extract_results_from_html(html: str) -> str:
    """Results body from a heading or <sec sec-type=results>. Never the whole page."""
    if not html:
        return ""
    m = re.search(
        rf'(?is)<sec[^>]*sec-type\s*=\s*["\']results["\'][^>]*>(.*?)(?:</sec>|<sec\b)',
        html,
    )
    if m:
        return html_visible_text(strip_page_chrome(m.group(1)))
    m = re.search(
        rf'(?is)<(?:h[1-4]|header)[^>]*>\s*(?:<[^>]+>\s*)*{_RESULTS_TITLE}'
        rf'\s*(?:</[^>]+>\s*)*</(?:h[1-4]|header)>(.*?)'
        rf'(?=<(?:h[1-4]|header)[^>]*>\s*(?:<[^>]+>\s*)*{_NEXT_MAJOR}|\Z)',
        html,
    )
    if m:
        return html_visible_text(strip_page_chrome(m.group(1)))
    m = re.search(
        rf'(?is)<(?:div|section)[^>]*(?:id|class)\s*=\s*["\'][^"\']*\bresults?\b'
        rf'[^"\']*["\'][^>]*>(.*?)'
        rf'(?=<(?:div|section|h[1-4])[^>]*(?:id|class|)\s*(?:=)?[^>]{{0,80}}{_NEXT_MAJOR}|\Z)',
        html,
    )
    if m:
        body = html_visible_text(strip_page_chrome(m.group(1)))
        if english_word_count(body) >= 80:
            return body
    return ""


def extract_methods_from_html(html: str) -> str:
    """Methods / Materials body from a heading or <sec sec-type=methods>."""
    if not html:
        return ""
    m = re.search(
        rf'(?is)<sec[^>]*sec-type\s*=\s*["\'](?:methods|materials)["\'][^>]*>(.*?)(?:</sec>|<sec\b)',
        html,
    )
    if m:
        return html_visible_text(strip_page_chrome(m.group(1)))
    m = re.search(
        rf'(?is)<(?:h[1-4]|header)[^>]*>\s*(?:<[^>]+>\s*)*{_METHODS_TITLE}'
        rf'\s*(?:</[^>]+>\s*)*</(?:h[1-4]|header)>(.*?)'
        rf'(?=<(?:h[1-4]|header)[^>]*>\s*(?:<[^>]+>\s*)*{_AFTER_METHODS}|\Z)',
        html,
    )
    if m:
        return html_visible_text(strip_page_chrome(m.group(1)))
    m = re.search(
        rf'(?is)<(?:div|section)[^>]*(?:id|class)\s*=\s*["\'][^"\']*\bmethods?\b'
        rf'[^"\']*["\'][^>]*>(.*?)'
        rf'(?=<(?:div|section|h[1-4])[^>]*(?:id|class|)\s*(?:=)?[^>]{{0,80}}{_AFTER_METHODS}|\Z)',
        html,
    )
    if m:
        body = html_visible_text(strip_page_chrome(m.group(1)))
        if english_word_count(body) >= 40:
            return body
    return ""


def extract_fig_captions_from_html(html: str, max_chars: int = 6000) -> str:
    """Full figure legends from HTML (figure body, figcaption, caption classes)."""
    if not html:
        return ""
    caps: list[str] = []
    seen: set[str] = set()

    def _add(text: str) -> None:
        text = re.sub(r"\s+", " ", html_visible_text(text or "")).strip()
        if text and text not in seen and not re.fullmatch(r"(?i)fig(?:ure)?\.?\s*\d+", text):
            seen.add(text)
            caps.append(text)

    for m in re.finditer(r'(?is)<figure\b[^>]*>(.*?)</figure>', html):
        _add(m.group(1))
    for pat in (
        r'(?is)<figcaption\b[^>]*>(.*?)</figcaption>',
        r'(?is)<(?:fig\b[^>]*>\s*)?<caption\b[^>]*>(.*?)</caption>',
        r'(?is)<(?:div|p|section)[^>]*(?:id|class)\s*=\s*["\'][^"\']*'
        r'(?:fig(?:ure)?[-_]?(?:caption|legend|desc)|c-article-section__figure)'
        r'[^"\']*["\'][^>]*>(.*?)</(?:div|p|section)>',
    ):
        for m in re.finditer(pat, html):
            _add(m.group(1))
    return "\n\n".join(caps)[:max_chars]


def looks_like_whole_page(text: str) -> bool:
    """True for HTML documents, landings, or multi-section dumps — not isolated Results."""
    blob = str(text or "")
    if _PAGE_MARKERS_RE.search(blob) or LANDING_RE.search(blob) or STUB_RE.search(blob):
        return True
    if re.search(r"(?is)</(?:p|div|section|sec|article|h[1-6])>", blob):
        return True
    heads = len(re.findall(
        r"(?im)^(?:abstract|introduction|methods|discussion|references|acknowledgements?)\b",
        blob,
    ))
    return heads >= 2


def isolate_results_text(text: str) -> str:
    """Heading/XML Results only. Empty when no Results section can be isolated."""
    raw = str(text or "")
    if not raw.strip():
        return ""
    if "<" in raw:
        hit = extract_results_from_html(raw)
        if hit:
            return hit
        if re.search(r"(?i)<(?:sec|article)\b", raw):
            hit = extract_results_from_xml(raw)
            if hit:
                return hit
        if looks_like_whole_page(raw):
            return ""
    if looks_like_whole_page(raw):
        m = re.search(rf"(?im)^{_RESULTS_TITLE}\s*$", raw)
        if not m:
            return ""
        body = raw[m.end():]
        cut = re.search(rf"(?im)^{_NEXT_MAJOR}\b", body)
        return (body[:cut.start()] if cut else body).strip()
    return raw


def extract_results_from_xml(xml_text: str) -> str:
    """Results from structured JATS: sec-type=results or a Results title."""
    if not xml_text:
        return ""
    import xml.etree.ElementTree as ET

    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return ""
    sections: list[str] = []
    for sec in root.iter("sec"):
        sec_type = (sec.get("sec-type") or "").lower()
        title_elem = sec.find("title")
        title = ""
        if title_elem is not None and (title_elem.text or "").strip():
            title = title_elem.text.strip().lower()
        if "results" in sec_type or title == "results" or title.startswith("results"):
            parts = ["".join(p.itertext()).strip() for p in sec.iter("p")]
            parts = [p for p in parts if p]
            if parts:
                sections.append(" ".join(parts))
    return strip_page_chrome("\n\n".join(sections))


def is_landing_or_chrome(text: str) -> bool:
    blob = str(text or "")
    if LANDING_RE.search(blob) or STUB_RE.search(blob):
        return True
    # Long reference list / author index without experimental prose
    if len(re.findall(r"(?i)\(\d{4}\)", blob)) >= 25 and english_word_count(blob) < 4000:
        return True
    if re.search(r"(?i)\breferences\b", blob) and not re.search(r"(?i)\bresults\b", blob):
        if english_word_count(blob) < MIN_RESULTS_WORDS:
            return True
    return False


def is_real_results_text(text: str) -> bool:
    """True only for isolated Results prose, never a whole landing page."""
    if not text or not str(text).strip():
        return False
    raw = str(text)
    if looks_like_whole_page(raw):
        blob = isolate_results_text(raw)
        if not blob:
            return False
    else:
        blob = raw
    blob = strip_page_chrome(blob)
    if is_landing_or_chrome(blob):
        return False
    words = english_word_count(blob)
    han = han_len(blob)
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
    if looks_like_whole_page(results):
        results = isolate_results_text(results)
    if not is_real_results_text(results):
        words = english_word_count(results or "")
        if results:
            item.source_trace.append(
                f"rejected non-Results/stub ({words} words) from {source_label or 'source'}"
            )
        return False
    item.fulltext_results = (results or "")[:FULLTEXT_WINDOW]
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


_MECH_HINT = re.compile(
    r"(?i)pathway|receptor|bind|ligand|signal|phosphoryl|transduc|"
    r"engag|synapse|internali|traffick|caspase|apoptos|cytokine|"
    r"antibody|car-?t|tcr|checkpoint|antigen|epitope|nucleosome|"
    r"机制|通路|受体|结合|信号|磷酸化"
)
_ENTITY_RE = re.compile(
    r"(?i)\b(?:CD\d+[A-Za-z0-9]*|IL-?\d+[A-Za-z0-9]*|TCR|CAR-?T?|PD-?1|"
    r"PD-?L1|CTLA-?4|HLA-[A-Z0-9*]+|NK|mRNA|siRNA|LNP|ADC|Fab|Fc|"
    r"organoid|nucleosome|antibody|receptor|ligand|cytokine|"
    r"[A-Z][A-Za-z]{2,}(?:in|ab|cept|nib|mab))\b|"
    r"[\u4e00-\u9fff]{2,8}(?:细胞|受体|抗体|器官|通路|蛋白)"
)
_MOUSE_RE = re.compile(r"(?i)\bmice\b|\bmouse\b|murine|小鼠")


def _named_entities(text: str) -> list[str]:
    seen: list[str] = []
    for m in _ENTITY_RE.finditer(str(text or "")):
        tok = m.group(0).strip()
        if tok.lower() in {"this", "that", "with", "from", "study", "result", "results"}:
            continue
        if tok not in seen:
            seen.append(tok)
        if len(seen) >= 8:
            break
    return seen


def _mechanism_stages(results_text: str, mechanism: str) -> list[str]:
    parts: list[str] = []
    for blob in (mechanism, results_text):
        for sent in re.split(r"(?<=[。．.!?])\s+", str(blob or "").strip()):
            sent = sent.strip()
            if not sent:
                continue
            if _MECH_HINT.search(sent) or _ENTITY_RE.search(sent):
                parts.append(sent[:160])
            if len(parts) >= 4:
                return parts
    if not parts and mechanism:
        parts.append(str(mechanism).strip()[:160])
    return parts[:4] or ["a receptor engages its ligand and the downstream signal fires"]


def _mechanism_subject(results_text: str, mechanism: str) -> str:
    """Story from verified mechanism/Results: who/what, 2–4 stages, one accent."""
    src = f"{mechanism or ''} {results_text or ''}"
    names = _named_entities(src)
    stages = _mechanism_stages(results_text, mechanism)
    accent = names[0] if names else "the key receptor complex"
    who = ", ".join(names[:6]) if names else "the named receptor, ligand and cell from the paper"
    allow_mouse = bool(_MOUSE_RE.search(src))
    forbid = (
        "No faces, no cartoon robots, no people, no human or animal body silhouettes"
    )
    if not allow_mouse:
        forbid += ", no mice unless the source study uses them"
    forbid += ", no English or Chinese text, letters, numbers or labels of any kind"
    stage_txt = " ".join(
        f"Stage {i}: {s.rstrip('。.')}." for i, s in enumerate(stages[:4], 1)
    )
    return (
        f"A 2-to-4 stage BioRender mechanism using only named elements from the paper "
        f"({who}). {stage_txt} Exactly ONE key element — {accent} — is drawn solid "
        f"terracotta #C0492F, large and boldly filled, the heaviest object in the "
        f"figure. {forbid}."
    )


def mechanism_image_prompt(
    results_text: str,
    mechanism: str = "",
    placement_index: int | None = None,
) -> str:
    """House-style figure from the article's mechanism statements, not fetch chrome."""
    subject = _mechanism_subject(results_text, mechanism)
    if placement_index is None:
        placement_index = sum(ord(c) for c in subject)
    pos = GOLDEN_POS[placement_index % len(GOLDEN_POS)]
    placement = GOLDEN_PLACEMENTS[placement_index % len(GOLDEN_PLACEMENTS)]
    return (
        f"{IMAGE_PREFIX} Subject: {subject} Place the visual centre of the solid "
        f"terracotta key element on the {placement} golden line (position {pos}). "
        f"The subject fills about 72 percent of the 1.618:1 card. {IMAGE_SUFFIX}"
    )


def secondhand_label(item: Any) -> str:
    level = getattr(item, "evidence_level", "") or ""
    source = getattr(item, "source", "") or "来源"
    if level == "press":
        return f"二手：未读原文，信息来自{source}新闻稿"
    if level == "preprint":
        return f"二手：仅读摘要（预印本），未读原文"
    return f"二手：仅读摘要，未读原文"


_CLINICAL_STUDY_RE = re.compile(
    r"(?i)"
    r"(?<!非)临床(?:试验|研究)|"
    r"随机(?:对照)?|"
    r"(?:I{1,3}|[123一二三]|一期|二期|三期)\s*期|"
    r"phase\s*[ivx1-3]|"
    r"\bRCT\b|interventional|"
    r"(?<![A-Za-z])trial(?![A-Za-z])|"
    r"患者入组|受试者|"
    r"NCT\d{8}|ChiCTR|"
    r"(?:主要|次要)?终点|"
    r"endpoint|"
    r"(?:给药|剂量|dosing).{0,12}(?:患者|受试|participant)|"
    r"(?:患者|受试|participant).{0,12}(?:给药|剂量|dosing)"
)
_DESCRIPTIVE_STUDY_RE = re.compile(
    r"(?i)图谱|atlas|描述性|descriptive|资源库|建库|表征|"
    r"characteri[sz]|综述|review|方法学|非临床"
)


def requires_primary_endpoint_result(art: dict) -> bool:
    """Schema: primary_endpoint_result is for interventional/trial studies only."""
    dc = (art or {}).get("datacard") if isinstance(art, dict) else None
    if not isinstance(dc, dict):
        return False
    blob = " ".join(
        str(dc.get(k) or "")
        for k in ("study_type", "intervention", "n", "control")
    )
    if _DESCRIPTIVE_STUDY_RE.search(blob) and not _CLINICAL_STUDY_RE.search(blob):
        return False
    return bool(_CLINICAL_STUDY_RE.search(blob))


def section_ranges_for_material(item: Any = None, sections_read: dict | None = None) -> dict:
    """Scale design/results bands to the methods/fig material actually read."""
    ranges = {k: tuple(v) for k, v in SECTION_RANGES.items()}
    sr = sections_read
    if sr is None and item is not None:
        sr = getattr(item, "sections_read", None) or (
            item.get("sections_read") if isinstance(item, dict) else None
        )
    sr = sr or {}

    def _chars(key: str) -> int:
        block = sr.get(key) or {}
        try:
            return int(block.get("chars") or 0)
        except (TypeError, ValueError):
            return 0

    methods_n = _chars("methods")
    figs_n = _chars("fig_captions")
    results_n = _chars("results")

    def _scale(lo: int, hi: int, have: int, typical: int) -> tuple[int, int]:
        if have >= typical:
            return lo, hi
        if have <= 0:
            return max(40, lo * 2 // 5), max(80, hi * 2 // 5)
        frac = max(0.4, min(1.0, have / typical))
        return max(40, int(lo * frac)), max(int(hi * frac), int(lo * frac) + 30)

    ranges["design"] = _scale(*SECTION_RANGES["design"], methods_n, 2000)
    material = results_n + figs_n
    if figs_n == 0 and results_n < 4000:
        ranges["results"] = _scale(*SECTION_RANGES["results"], material, 8000)
    return ranges


def validate_acir_structure(art: dict, section_ranges: dict | None = None) -> list[str]:
    """Section 1 of the binding spec. Deep articles only."""
    problems: list[str] = []
    if art.get("tier") != "deep":
        return problems
    ranges = section_ranges or SECTION_RANGES

    def check_range(name: str, text: Any) -> None:
        lo, hi = ranges[name]
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
        if not str(dc.get("primary_endpoint") or "").strip():
            problems.append("数据卡缺 primary_endpoint")
        if requires_primary_endpoint_result(art) and not str(dc.get("primary_endpoint_result") or "").strip():
            problems.append("数据卡缺 primary_endpoint_result")
        for key, val in dc.items():
            if re.search(r"原文未给出|原文未报告", str(val or "")):
                problems.append(f"数据卡 {key} 不要写占位套话，缺项请省略")

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
    """True like-for-like pairs only (same endpoint, X% vs Y%). Else []."""
    verified = verified_data_points(art, source)
    groups: dict[str, list[dict]] = {}
    for dp in verified:
        meaning = str(dp.get("meaning") or "").strip().lower()
        value = str(dp.get("value") or "")
        quote = str(dp.get("source_quote") or "")
        unit_m = re.search(r"(%|％|例|名|mg|kg|个月|周|天|年|倍)", value)
        if not unit_m:
            continue
        unit = unit_m.group(1)
        vs_hit = bool(re.search(r"(?i)\bvs\.?\b|versus|相比|对照|对比|control|placebo", f"{meaning} {quote} {value}"))
        meaning_key = re.sub(r"(?i)^(对照|control|placebo)\s*", "", meaning)
        meaning_key = re.sub(r"(?i)(对照|control|placebo)$", "", meaning_key).strip()
        key = f"{meaning_key}|{unit}" if meaning_key else ""
        if not key:
            continue
        dp = dict(dp)
        dp["_unit"] = unit
        dp["_vs"] = vs_hit
        groups.setdefault(key, []).append(dp)
    for pts in groups.values():
        if len(pts) < 2:
            continue
        if any(p.get("_vs") for p in pts) or len({str(p.get("value")) for p in pts}) >= 2:
            if all(p.get("source_quote") for p in pts):
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
        unit = str(dp.get("_unit") or "")
        if not unit:
            um = re.search(r"(%|％|例|名|mg|kg|个月|周|天|年|倍)", raw)
            unit = um.group(1) if um else ""
        n_m = re.search(r"(?i)(?:n\s*=\s*|例)\s*(\d+)", f"{raw} {dp.get('meaning') or ''} {dp.get('source_quote') or ''}")
        n_lab = f" n={n_m.group(1)}" if n_m else ""
        src_m = re.search(r"(?i)((?:fig(?:ure)?|table|图|表)\s*[\w\d.-]+)", str(dp.get("source_quote") or ""))
        src_lab = f" {src_m.group(1)}" if src_m else ""
        label = f"{str(dp.get('meaning') or raw)[:10]}{n_lab}"
        parsed.append((label, float(m.group(1)), unit, src_lab))
    if len(parsed) < 2:
        return ""
    width, height, pad = 360, 200, 36
    vmax = max(v for _, v, _, _ in parsed) or 1
    bar_w = (width - 2 * pad) / len(parsed)
    bars = []
    src_note = next((s for _, _, _, s in parsed if s), "")
    for i, (label, val, unit, _src) in enumerate(parsed):
        h = max(4, (val / vmax) * (height - 2 * pad - 16))
        x = pad + i * bar_w + 8
        y = height - pad - h
        bars.append(
            f'<rect x="{x:.1f}" y="{y:.1f}" width="{bar_w-16:.1f}" height="{h:.1f}" fill="#0F6B5C"/>'
            f'<text x="{x + (bar_w-16)/2:.1f}" y="{height-14}" text-anchor="middle" '
            f'font-size="10" fill="#5C6B67">{_svg_escape(label)}</text>'
            f'<text x="{x + (bar_w-16)/2:.1f}" y="{y-4:.1f}" text-anchor="middle" '
            f'font-size="10" fill="#0F6B5C">{val:g}{_svg_escape(unit)}</text>'
        )
    cap = _svg_escape((title or "核对后的关键对比") + src_note)
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
    model = cfg.get("gemini_model") or os.environ.get("GEMINI_MODEL") or DEFAULT_GEMINI_MODEL
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
{drafted[:FULLTEXT_WINDOW]}

## Full text excerpt
{(fulltext or "")[:FULLTEXT_WINDOW]}
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


def matte_to_white(im: Any) -> Any:
    """Composite transparent pixels onto opaque white before any measurement."""
    from PIL import Image

    if im.mode in ("RGBA", "LA") or (im.mode == "P" and "transparency" in im.info):
        rgba = im.convert("RGBA")
        bg = Image.new("RGB", rgba.size, (255, 255, 255))
        bg.paste(rgba, mask=rgba.split()[-1])
        return bg
    return im.convert("RGB")


def _crop_box(w: int, h: int, ar: float) -> tuple[int, int, int, int]:
    if w / max(h, 1) > ar:
        cw, ch = h * ar, float(h)
    else:
        cw, ch = float(w), w / ar
    return (
        int(round((w - cw) / 2)),
        int(round((h - ch) / 2)),
        int(round((w + cw) / 2)),
        int(round((h + ch) / 2)),
    )


def _border_bg(im: Any, b: int = 4) -> tuple[float, float, float]:
    w, h = im.size
    pix = im.load()
    samples: list[tuple[int, int, int]] = []
    for y in range(min(b, h)):
        for x in range(w):
            samples.append(pix[x, y])
    for y in range(max(h - b, 0), h):
        for x in range(w):
            samples.append(pix[x, y])
    for y in range(h):
        for x in range(min(b, w)):
            samples.append(pix[x, y])
        for x in range(max(w - b, 0), w):
            samples.append(pix[x, y])
    if not samples:
        return (255.0, 255.0, 255.0)
    n = len(samples)
    rs = sorted(p[0] for p in samples)
    gs = sorted(p[1] for p in samples)
    bs = sorted(p[2] for p in samples)
    mid = n // 2
    return (float(rs[mid]), float(gs[mid]), float(bs[mid]))


def _warm_pixels(im: Any) -> tuple[list[int], list[int], int, int]:
    hsv = im.convert("HSV")
    pix = hsv.load()
    w, h = hsv.size
    xs: list[int] = []
    ys: list[int] = []
    for y in range(h):
        for x in range(w):
            hh, s, v = pix[x, y]
            hd = hh * 360 / 255
            if (hd <= 38 or hd >= 340) and s >= 0.35 * 255 and v >= 0.28 * 255:
                xs.append(x)
                ys.append(y)
    return xs, ys, w, h


def _warm_mask_frac(im: Any) -> float:
    xs, ys, w, h = _warm_pixels(im)
    return len(xs) / max(w * h, 1)


def accent_placement(im: Any) -> dict[str, Any]:
    """Accent centroid vs golden-section points (check_v5: gd≤0.06, not dead centre)."""
    xs, ys, w, h = _warm_pixels(im)
    n = len(xs)
    if n < 80:
        return {
            "fx": None, "fy": None, "n": n,
            "golden_dist": 1.0, "dead_center": True,
        }
    fx, fy = (sum(xs) / n) / max(w, 1), (sum(ys) / n) / max(h, 1)
    gd = min(((fx - tx) ** 2 + (fy - ty) ** 2) ** 0.5 for tx, ty in GOLDEN_XY.values())
    dead = abs(fx - 0.5) < DEAD_CENTER_TOL and abs(fy - 0.5) < DEAD_CENTER_TOL
    return {
        "fx": round(fx, 4), "fy": round(fy, 4), "n": n,
        "golden_dist": round(gd, 4), "dead_center": dead,
    }


def subject_metrics(im: Any) -> dict[str, Any]:
    """check_v5 fill: subject bounding span inside the 1.618:1 centre crop."""
    w, h = im.size
    x0c, y0c, x1c, y1c = _crop_box(w, h, PHI)
    crop = im.crop((x0c, y0c, x1c, y1c))
    cw, ch = crop.size
    bg = _border_bg(im)
    pix = crop.load()
    mask = [[False] * cw for _ in range(ch)]
    row_hit = [0] * ch
    col_hit = [0] * cw
    for y in range(ch):
        for x in range(cw):
            r, g, b = pix[x, y]
            d = ((r - bg[0]) ** 2 + (g - bg[1]) ** 2 + (b - bg[2]) ** 2) ** 0.5
            if d > 28:
                mask[y][x] = True
                row_hit[y] += 1
                col_hit[x] += 1
    rows = [i for i, n in enumerate(row_hit) if n / max(cw, 1) > 0.004]
    cols = [i for i, n in enumerate(col_hit) if n / max(ch, 1) > 0.004]
    if not rows or not cols:
        return {"span": 0.0, "bbox": (0, 0, 0, 0), "margins": {"L": 1, "R": 1, "T": 1, "B": 1}}
    x0, x1, y0, y1 = cols[0], cols[-1] + 1, rows[0], rows[-1] + 1
    span = max((x1 - x0) / max(cw, 1), (y1 - y0) / max(ch, 1))
    return {
        "span": span,
        "bbox": (x0 + x0c, y0 + y0c, x1 + x0c, y1 + y0c),
        "margins": {
            "L": x0 / max(cw, 1),
            "R": (cw - x1) / max(cw, 1),
            "T": y0 / max(ch, 1),
            "B": (ch - y1) / max(ch, 1),
        },
    }


_OCR_CACHE: tuple[str, Any] | tuple[None, None] | None = None


def _ocr_engine() -> tuple[str, Any] | tuple[None, None]:
    global _OCR_CACHE
    if _OCR_CACHE is not None:
        return _OCR_CACHE
    try:
        import pytesseract  # type: ignore

        pytesseract.get_tesseract_version()
        _OCR_CACHE = ("tesseract", pytesseract)
        return _OCR_CACHE
    except Exception:
        pass
    try:
        from rapidocr_onnxruntime import RapidOCR  # type: ignore

        _OCR_CACHE = ("rapidocr", RapidOCR())
        return _OCR_CACHE
    except Exception:
        _OCR_CACHE = (None, None)
        return _OCR_CACHE


def ocr_available() -> bool:
    return _ocr_engine()[0] is not None


def image_pipeline_ready() -> tuple[bool, str]:
    """Check QC deps before spending any image generations."""
    try:
        from PIL import Image  # noqa: F401
    except ImportError:
        return False, "Pillow missing"
    if not ocr_available():
        return False, "OCR unavailable; fail closed"
    return True, ""


def image_has_ocr_text(im: Any) -> tuple[bool, str]:
    """Real OCR only (check_v5: conf>=80, >=3 alnum/CJK). Missing engine fails closed."""
    name, engine = _ocr_engine()
    if name is None:
        return True, "OCR unavailable; fail closed"
    token_re = re.compile(r"[A-Za-z]{3,}|\d{2,}|[\u4e00-\u9fff]{2,}")
    try:
        if name == "tesseract":
            data = engine.image_to_data(im, config="--psm 6", output_type=engine.Output.DICT)
            hits: list[str] = []
            for txt, conf in zip(data.get("text") or [], data.get("conf") or []):
                try:
                    score = float(conf)
                except (TypeError, ValueError):
                    continue
                if score < 80:
                    continue
                if token_re.search(str(txt or "")):
                    hits.append(str(txt).strip())
            return bool(hits), " ".join(hits[:8])
        arr = im.convert("RGB")
        try:
            import numpy as np

            result, _ = engine(np.asarray(arr))
        except TypeError:
            result, _ = engine(arr)
        hits = []
        for row in result or []:
            txt, conf = "", 1.0
            if isinstance(row, (list, tuple)):
                if len(row) >= 3:
                    txt, conf = str(row[1]), float(row[2] or 0)
                elif len(row) >= 2:
                    txt = str(row[1])
            else:
                txt = str(row)
            if conf < 0.8:
                continue
            if token_re.search(txt):
                hits.append(txt)
        return bool(hits), " ".join(hits[:8])
    except Exception as exc:
        return True, f"OCR error: {type(exc).__name__}"


def average_hash_bits(im: Any, size: int = 8) -> list[int]:
    g = im.convert("L").resize((size, size))
    pix = list(g.tobytes())
    avg = sum(pix) / max(len(pix), 1)
    return [1 if p >= avg else 0 for p in pix]


def hash_distance(a: list[int], b: list[int]) -> int:
    return sum(x != y for x, y in zip(a, b))


def is_publishable_image(path: str | Path) -> bool:
    p = Path(path)
    if not p.exists() or p.stat().st_size < 2000:
        return False
    try:
        from PIL import Image

        im = Image.open(p)
        w, h = im.size
    except Exception:
        return False
    return w >= 400 and h >= 247


def qc_image(path: str, prior_hashes: list[list[int]] | None = None) -> dict[str, Any]:
    """QC: span 68–78%, accent 3–6% on a golden point, real OCR. Weak accent fails."""
    reasons: list[str] = []
    result: dict[str, Any] = {
        "pass": False,
        "ocr_text": False,
        "accent_frac": 0.0,
        "fill_frac": 0.0,
        "golden_dist": 1.0,
        "dead_center": True,
        "reasons": reasons,
        "hash": [],
    }
    try:
        from PIL import Image
    except ImportError:
        reasons.append("Pillow missing; cannot QC image")
        return result
    if not ocr_available():
        reasons.append("OCR unavailable; fail closed")
        return result
    try:
        im = matte_to_white(Image.open(path))
    except Exception as exc:
        reasons.append(f"unreadable image: {type(exc).__name__}")
        return result
    if im.size[0] < 32 or im.size[1] < 32:
        reasons.append(f"image too small to publish ({im.size[0]}x{im.size[1]})")
        return result

    metrics = subject_metrics(im)
    fill_frac = float(metrics["span"])
    accent_frac = _warm_mask_frac(im)
    place = accent_placement(im)
    ocr_hit, ocr_note = image_has_ocr_text(im)
    bits = average_hash_bits(im)
    result["fill_frac"] = round(fill_frac, 4)
    result["accent_frac"] = round(accent_frac, 4)
    result["golden_dist"] = place["golden_dist"]
    result["dead_center"] = place["dead_center"]
    result["ocr_text"] = bool(ocr_hit)
    result["hash"] = bits
    if ocr_hit:
        reasons.append(f"ocr text detected: {ocr_note}")
    lo_a, hi_a = ACCENT_RANGE
    if not (lo_a <= accent_frac <= hi_a):
        reasons.append(f"accent {accent_frac:.3f} outside {lo_a:.2f}-{hi_a:.2f}")
    if place["dead_center"]:
        reasons.append("accent dead-centre; needs golden-section focal emphasis")
    if place["golden_dist"] > GOLDEN_DIST_MAX:
        reasons.append(
            f"golden_dist {place['golden_dist']:.3f} > {GOLDEN_DIST_MAX:.2f}"
        )
    lo_f, hi_f = FILL_RANGE
    if not (lo_f <= fill_frac <= hi_f):
        reasons.append(f"fill {fill_frac:.3f} outside {lo_f:.2f}-{hi_f:.2f}")
    if prior_hashes:
        dmin = min((hash_distance(bits, prev) for prev in prior_hashes), default=99)
        if dmin <= DEDUPE_FAIL:
            reasons.append(f"near-duplicate of another weekly image (d={dmin})")
    result["pass"] = not reasons
    result["reasons"] = reasons
    return result


def reframe_to_card(src: str | Path, dest: str | Path, pos: str = "UR", target: float = SUBJECT_TARGET) -> dict[str, Any]:
    """Port of reframe_v9: 1600×989, subject ~72%, accent on a golden point."""
    from PIL import Image

    src, dest = Path(src), Path(dest)
    im = matte_to_white(Image.open(src))
    w, h = im.size
    metrics = subject_metrics(im)
    x0, y0, x1, y1 = metrics["bbox"]
    if x1 <= x0 or y1 <= y0:
        canvas = Image.new("RGB", IMAGE_OUT_SIZE, (255, 255, 255))
        canvas.save(dest, "PNG")
        return {"ok": False, "reason": "empty subject"}
    ow, oh = IMAGE_OUT_SIZE
    iw, ih = x1 - x0, y1 - y0
    scale = min(target * ow / max(iw, 1), target * oh / max(ih, 1))
    tx, ty = GOLDEN_XY.get(pos, GOLDEN_XY["UR"])
    # Accent centroid if present, else subject centre
    xs, ys, _, _ = _warm_pixels(im)
    if len(xs) > 200:
        fx, fy = sum(xs) / len(xs), sum(ys) / len(ys)
    else:
        fx, fy = (x0 + x1) / 2, (y0 + y1) / 2
    nw, nh = max(1, int(round(w * scale))), max(1, int(round(h * scale)))
    scaled = im.resize((nw, nh), Image.Resampling.LANCZOS)
    left = tx * ow - fx * scale
    top = ty * oh - fy * scale
    mx, my = 0.055 * ow, 0.055 * oh
    left = min(max(left + x0 * scale, mx), ow - iw * scale - mx) - x0 * scale
    top = min(max(top + y0 * scale, my), oh - ih * scale - my) - y0 * scale
    canvas = Image.new("RGB", (ow, oh), (255, 255, 255))
    canvas.paste(scaled, (int(round(left)), int(round(top))))
    pix = canvas.load()
    for y in range(oh):
        for x in range(ow):
            r, g, b = pix[x, y]
            if min(r, g, b) >= 250 and max(r, g, b) - min(r, g, b) <= 3:
                pix[x, y] = (255, 255, 255)
    dest.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(dest, "PNG")
    return {"ok": True, "pos": pos, "scale": round(float(scale), 3)}


def write_fallback_cover(path: str) -> None:
    """Neutral house-style cover at 1600×989. Never write a 1×1 or blank PNG."""
    w, h = FALLBACK_COVER_SIZE
    try:
        from PIL import Image, ImageDraw

        im = Image.new("RGB", (w, h), (255, 255, 255))
        draw = ImageDraw.Draw(im)
        draw.ellipse([int(w * 0.12), int(h * 0.18), int(w * 0.46), int(h * 0.82)], fill=(159, 216, 203))
        draw.ellipse([int(w * 0.52), int(h * 0.16), int(w * 0.88), int(h * 0.84)], fill=(15, 107, 92))
        draw.ellipse([int(w * 0.60), int(h * 0.30), int(w * 0.80), int(h * 0.70)], fill=(47, 125, 109))
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        im.save(path, "PNG")
    except Exception:
        logging.warning("Pillow fallback cover failed; not writing a 1x1 placeholder")
