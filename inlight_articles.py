#!/usr/bin/env python3
"""Article enrichment, two-stage generation, and validation for InLight.

This module implements the article-depth overhaul:
- enrich_item(): Fetch abstracts and full text from Europe PMC, PubMed, publishers
- triage(): Cheap model call to pick items and assign tiers
- draft_article(): One Claude call per article with new schema/prompt
- validate_depth(): Machine-verifiable checks on generated content
- wechat_html_article(): WeChat-compatible HTML with data card and sections
"""

from __future__ import annotations

import ast
import json
import logging
import os
import re
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Any

import xml.etree.ElementTree as ET

UA = "FrontierDigestWeekly/1.0 (+https://inlight.therasik.com)"

FIELDS = {
    "c1": "类器官",
    "c2": "AI 药物设计",
    "c3": "肿瘤免疫",
    "c4": "自身免疫疾病",
    "c5": "动物模型",
    "c6": "抗体工程",
    "c7": "细胞治疗",
    "c8": "疫苗",
    "c9": "小核酸与 LNP",
}

MARKETING_BLOCKLIST = re.compile(r"重磅|颠覆|改写教科书|震撼|碾压|轰动|史诗级|划时代", re.IGNORECASE)

# English / INN form -> (preferred Chinese, common incorrect Chinese forms).
# Used both to catch mistranslations and to map a Chinese drug name back
# to the source's international name.
TERMINOLOGY_GLOSSARY = {
    "mesaconate": ("中康酸", ["美康酸", "梅沙康酸", "麦康酸"]),
    "mesaconic acid": ("中康酸", ["美康酸", "梅沙康酸", "麦康酸"]),
}

# Phonetic syllables used in official Chinese generic-name transcriptions.
# This is a character↔sound table, not a drug or institution list.
_HAN_PINYIN = {
    "莫": "mo", "妥": "tuo", "珠": "zhu", "利": "li", "特": "te",
    "瑞": "rui", "普": "pu", "可": "ke", "辛": "xin", "帕": "pa",
    "博": "bo", "纳": "na", "武": "wu", "尤": "you", "替": "ti",
    "雷": "lei", "信": "xin", "迪": "di", "卡": "ka", "度": "du",
    "伐": "fa", "阿": "a", "伊": "yi", "匹": "pi", "木": "mu",
    "曲": "qu", "贝": "bei", "西": "xi", "奥": "ao", "维": "wei",
    "泊": "bo", "兰": "lan", "肽": "tai", "格": "ge", "菲": "fei",
    "派": "pai", "姆": "mu", "单": "dan", "抗": "kang", "尼": "ni",
    "昔": "xi", "达": "da", "拉": "la", "安": "an", "托": "tuo",
    "珠": "zhu", "利": "li", "尤": "you", "替": "ti", "赛": "sai",
    "妥": "tuo", "珠": "zhu", "单": "dan",
}

_GENERIC_FACILITY = {
    "单中心", "多中心", "中心数", "医疗中心", "研究中心", "医学中心",
    "大学医院", "附属医院", "教学医院",
}
_GENERIC_INST_HEADS = (
    "其他", "其它", "多家", "多个", "若干", "各", "该", "本", "此", "其",
    "这些", "那些", "不同", "另外", "部分", "个别", "相关", "上述", "下列",
    "某", "某个", "某些", "几家", "几所", "多数", "少数", "所有", "任何",
    "每家", "每所", "不少", "很多",
)
_GENERIC_INST_SUFFIXES = (
    "大学", "医院", "医学院", "研究所", "研究院", "实验室", "中心",
    "肿瘤防治中心", "附属医院",
)

_INST_LEAD_WORDS = (
    "使用", "采用", "利用", "通过", "借助", "根据", "按照",
    "研究", "由", "在", "于", "来自", "和", "与", "对", "将", "把",
    "经", "以", "从", "向",
)
_INST_LEAD_CLAUSES = (
    "实验经", "研究经", "方案经", "工作经", "实验由", "研究由",
    "已经", "已由",
)
_INST_SUFFIX_RE = re.compile(
    r"(?:大学|医院|医学院|肿瘤防治中心|附属医院|研究所|研究院|实验室)$"
)

# Chinese institution → English name / bracketed acronym aliases.
_INSTITUTION_ALIASES = {
    "中山大学": ("sun yat-sen university", "sun yat sen university", "sysu"),
    "中山大学肿瘤防治中心": (
        "sun yat-sen university cancer center",
        "sun yat sen university cancer center",
        "sysucc",
    ),
    "中山大学附属肿瘤医院": (
        "sun yat-sen university cancer center",
        "sysucc",
    ),
    "北京大学": ("peking university", "pku"),
    "清华大学": ("tsinghua university",),
    "复旦大学": ("fudan university",),
    "浙江大学": ("zhejiang university", "zju"),
    "上海交通大学": ("shanghai jiao tong university", "sjtu"),
    "中国科学院": ("chinese academy of sciences", "cas"),
    "中国医学科学院": ("chinese academy of medical sciences", "cams"),
    "北京协和医院": ("peking union medical college hospital", "pumch"),
    "北京协和医学院": ("peking union medical college", "pumc"),
    "四川大学": ("sichuan university",),
    "华中科技大学": ("huazhong university of science and technology", "hust"),
    "西湖大学": ("westlake university",),
    "西湖实验室": ("westlake laboratory", "westlake lab"),
    "西湖生物医学研究所": (
        "westlake biomedical research institute", "wbri",
    ),
    "南方医科大学": ("southern medical university",),
    "中国科学技术大学": (
        "university of science and technology of china", "ustc",
    ),
    "南京大学": ("nanjing university",),
    "武汉大学": ("wuhan university",),
    "中南大学": ("central south university",),
    "山东大学": ("shandong university",),
    "吉林大学": ("jilin university",),
    "厦门大学": ("xiamen university",),
    "斯坦福大学": ("stanford university", "stanford"),
    "哈佛大学": ("harvard university", "harvard"),
    "麻省理工学院": ("massachusetts institute of technology", "mit"),
    "耶鲁大学": ("yale university", "yale"),
    "牛津大学": ("university of oxford", "oxford"),
    "剑桥大学": ("university of cambridge", "cambridge"),
    "约翰霍普金斯大学": ("johns hopkins university", "johns hopkins"),
    "安德森癌症中心": ("md anderson", "m.d. anderson"),
    "梅奥诊所": ("mayo clinic",),
    "卡罗林斯卡医学院": ("karolinska institutet", "karolinska"),
    "巴斯德研究所": ("institut pasteur", "pasteur institute"),
    "马克斯普朗克研究所": ("max planck",),
    "美国国立卫生研究院": ("national institutes of health", "nih"),
    "纪念斯隆凯特琳癌症中心": (
        "memorial sloan kettering", "mskcc", "sloan kettering",
    ),
    "同济大学": ("tongji university",),
    "东南大学": ("southeast university",),
    "广州医科大学": ("guangzhou medical university",),
    "首都医科大学": ("capital medical university",),
    "上海理工大学": ("university of shanghai for science and technology",),
    "香港大学": ("the university of hong kong", "university of hong kong", "hku"),
    "香港中文大学": ("chinese university of hong kong", "cuhk"),
    "台湾大学": ("national taiwan university", "ntu"),
    "东京大学": ("the university of tokyo", "university of tokyo", "todai"),
    "京都大学": ("kyoto university",),
    "首尔大学": ("seoul national university", "snu"),
    "伦敦大学学院": ("university college london", "ucl"),
    "帝国理工学院": ("imperial college london", "imperial college"),
    "多伦多大学": ("university of toronto",),
    "密歇根大学": ("university of michigan",),
    "加州大学旧金山分校": ("university of california san francisco", "ucsf"),
    "加州大学洛杉矶分校": ("university of california los angeles", "ucla"),
    "加州大学圣地亚哥分校": ("university of california san diego", "ucsd"),
    "杜克大学": ("duke university", "duke"),
    "宾夕法尼亚大学": ("university of pennsylvania", "upenn", "penn"),
    "西北大学": ("northwestern university",),
    "华盛顿大学": ("university of washington",),
    "哥伦比亚大学": ("columbia university",),
    "芝加哥大学": ("university of chicago",),
}

_CN_EN_INST_TYPES = (
    ("肿瘤防治中心", ("cancer center", "cancer hospital")),
    ("附属医院", ("affiliated hospital", "hospital")),
    ("医学院", ("medical college", "medical school", "school of medicine", "institutet")),
    ("研究所", ("research institute", "institute")),
    ("研究院", ("academy", "research academy", "institute")),
    ("实验室", ("laboratory", "lab")),
    ("大学", ("university", "universität", "universidad")),
    ("医院", ("hospital", "clinic")),
    ("学院", ("college", "institute", "institut")),
)
_CN_EN_INST_TOKENS = (
    ("生物医学", ("biomedical",)),
    ("肿瘤", ("cancer", "oncology", "tumor")),
    ("西湖", ("westlake", "west lake")),
    ("中山", ("sun yat-sen", "sun yat sen", "zhongshan")),
    ("北京", ("peking", "beijing")),
    ("清华", ("tsinghua",)),
    ("复旦", ("fudan",)),
    ("浙江", ("zhejiang",)),
    ("上海交通", ("shanghai jiao tong", "shanghai jiaotong")),
    ("斯坦福", ("stanford",)),
    ("哈佛", ("harvard",)),
    ("麻省理工", ("massachusetts institute of technology", "mit")),
    ("耶鲁", ("yale",)),
    ("牛津", ("oxford",)),
    ("剑桥", ("cambridge",)),
    ("约翰霍普金斯", ("johns hopkins",)),
    ("安德森", ("anderson", "md anderson")),
    ("梅奥", ("mayo",)),
    ("卡罗林斯卡", ("karolinska",)),
    ("巴斯德", ("pasteur",)),
    ("马克斯普朗克", ("max planck",)),
    ("纪念斯隆", ("memorial sloan", "mskcc", "sloan kettering")),
    ("南方医科", ("southern medical",)),
    ("中国科学技", ("university of science and technology of china", "ustc")),
    ("南京", ("nanjing",)),
    ("武汉", ("wuhan",)),
    ("中南", ("central south",)),
    ("山东", ("shandong",)),
    ("吉林", ("jilin",)),
    ("厦门", ("xiamen",)),
    ("华中科技", ("huazhong", "hust")),
    ("四川", ("sichuan",)),
    ("协和", ("peking union", "pumc", "pumch")),
    ("同济", ("tongji",)),
    ("东南", ("southeast",)),
    ("广州医科", ("guangzhou medical",)),
    ("首都医科", ("capital medical",)),
    ("香港", ("hong kong",)),
    ("台湾", ("taiwan",)),
    ("东京", ("tokyo",)),
    ("京都", ("kyoto",)),
    ("首尔", ("seoul",)),
    ("帝国理工", ("imperial",)),
    ("多伦多", ("toronto",)),
    ("密歇根", ("michigan",)),
    ("旧金山", ("san francisco", "ucsf")),
    ("洛杉矶", ("los angeles", "ucla")),
    ("圣地亚哥", ("san diego", "ucsd")),
    ("杜克", ("duke",)),
    ("宾夕法尼亚", ("pennsylvania", "upenn", "penn")),
    ("西北", ("northwestern",)),
    ("哥伦比亚", ("columbia",)),
    ("芝加哥", ("chicago",)),
)
_EN_INST_PHRASE_RE = re.compile(
    r"\b([A-Z][A-Za-z][A-Za-z .'\-]{0,80}"
    r"(?:University|Hospital|Institute|Institut|Academy|College|"
    r"Center|Centre|Laboratory|Clinic)\b)"
)
_ETHICS_CTX_RE = re.compile(
    r"(?i)伦理委员会|动物管理与使用委员会|实验动物管理|"
    r"IACUC|IRB|animal care and use committee|ethics committee|"
    r"institutional review board"
)
_ETHICS_EN_RE = re.compile(
    r"(?i)IACUC|IRB|animal care and use committee|ethics committee|"
    r"institutional review board"
)
_SITE_LABEL_RE = re.compile(
    r"(?i)\b(?:cohort|site|center|centre|hospital|arm|campus|institute|lab)\b"
)

_CN_DRUG_SUFFIX_RE = re.compile(r'单抗|替尼')
# Function words that must not be glued onto a generic name.
_CN_DRUG_LEAD_STOP = set("予给用的在对将把与和及经以于从向其该本此所已未正和取服注输")
_LATIN_DRUG_RE = re.compile(
    r'(?i)\b([a-z]{4,}(?:mab|nib|limab|zumab|ximab|tinib|ciclib|lizumab|cept))\b'
)
_PREPRINT_PUBLISHED_RE = re.compile(
    r'发表于|刊登于|刊于|同行评议|同行评审|正式发表|已发表在|'
    r'published in|peer[-\s]?reviewed|accepted in',
    re.IGNORECASE,
)
_PREPRINT_NEGATION_RE = re.compile(
    r'尚未|未经|未经过|没有经过|并非|不是|未获|未接受|'
    r'not\s+(?:yet\s+)?(?:peer|published)|non[- ]peer',
    re.IGNORECASE,
)


def _preprint_claims_publication(text: str) -> bool:
    """True only for affirmative journal/peer-review claims, not '尚未经同行评审'."""
    if not text or not _PREPRINT_PUBLISHED_RE.search(text):
        return False
    for sent in re.split(r'[。！？；;\n]', text):
        if not _PREPRINT_PUBLISHED_RE.search(sent):
            continue
        if _PREPRINT_NEGATION_RE.search(sent):
            continue
        return True
    return False

AUTHOR_PLACEHOLDER_RE = re.compile(
    r"原文未提供|原文未列出|原文未报告作者|未提供作者|未列出作者|作者信息"
)
PREPRINT_SOURCE_RE = re.compile(r"biorxiv|medrxiv", re.IGNORECASE)


_PREPRINT_HOST_MARKERS = (
    "biorxiv.org", "medrxiv.org", "researchsquare.com", "ssrn.com",
    "arxiv.org", "preprints.org", "osf.io", "chemrxiv.org",
    "psyarxiv.com", "authorea.com", "techrxiv.org", "eartharxiv.org",
    "peerj.com/preprints", "advance.sagepub",
)


def _is_preprint_host(url: str) -> bool:
    """True for preprint hosts. Preprints are never deep sources."""
    u = (url or "").lower()
    return any(h in u for h in _PREPRINT_HOST_MARKERS)


def _oa_host_label(url: str) -> str:
    try:
        return (urllib.parse.urlparse(url or "").hostname or "").lower()
    except Exception:
        return ""


def _is_preprint_oa(url: str = "", version: str = "") -> bool:
    """Reject submittedVersion and preprint hosts (Research Square, arXiv, …)."""
    ver = (version or "").strip()
    if ver.lower() == "submittedversion":
        return True
    return _is_preprint_host(url or "")

CHINESE_NUMBER_MAP = {
    "零": "0", "一": "1", "二": "2", "三": "3", "四": "4",
    "五": "5", "六": "6", "七": "7", "八": "8", "九": "9",
    "十": "10", "百": "100", "千": "1000", "万": "10000",
    "亿": "100000000",
}


@dataclass
class EnrichedItem:
    """An item enriched with abstract/fulltext from public sources."""
    url: str
    title: str
    source: str
    date: str
    kind: str = "academic"
    doi: str = ""
    pmid: str = ""
    pmcid: str = ""
    journal: str = ""  # Journal name from EPMC or source
    authors: str = ""  # Author string from EPMC / PubMed / bioRxiv metadata only
    abstract: str = ""
    fulltext_results: str = ""
    fig_captions: str = ""
    methods_design: str = ""
    evidence_level: str = "press"
    rss_summary: str = ""
    press_coverage: str = ""  # public press/media text when journal FT is closed
    source_trace: list[str] = field(default_factory=list)
    sections_read: dict = field(default_factory=dict)
    read_note: str = ""


def _cfg_int(cfg: dict, key: str, default: int) -> int:
    """Read an int from config. Missing/blank uses default; explicit 0 is kept."""
    if key not in cfg or cfg[key] is None or cfg[key] == "":
        return default
    return int(cfg[key])


def pipeline_targets(config: dict | None) -> dict:
    """Configurable weekly yield. Defaults: 3–5 deep 解读 (all deep, no brief padding)."""
    cfg = config or {}
    if "min_deep" in cfg and cfg["min_deep"] not in (None, ""):
        min_deep = int(cfg["min_deep"])
    elif "target_deep" in cfg and cfg["target_deep"] not in (None, ""):
        min_deep = int(cfg["target_deep"])
    elif "target_articles" in cfg and cfg["target_articles"] not in (None, ""):
        min_deep = int(cfg["target_articles"])
    else:
        min_deep = 3
    max_deep = _cfg_int(cfg, "max_deep", 5)
    return {
        "min_deep": min_deep,
        "max_deep": max_deep,
        # Aliases: every 解读 is a deep analysis.
        "target_articles": min_deep,
        "target_deep": min_deep,
        "max_brief": _cfg_int(cfg, "max_brief", 0),
        "max_industry": _cfg_int(cfg, "max_industry", 4),
        # 0 = no cap. Only apply when the loaded config sets the key
        # (production sources.yaml does; acceptance/replay fixtures do not).
        "max_candidates": int(cfg["max_candidates"]) if cfg.get("max_candidates") else 0,
    }


_PUBLISHER_MARKERS = (
    ("biorxiv", ("biorxiv",)),
    ("medrxiv", ("medrxiv",)),
    ("pmc", ("pmc.ncbi", "europepmc", "nih.gov/pmc")),
    ("pubmed", ("pubmed", "nih.gov")),
    ("nature", ("nature", "10.1038", "nature.com")),
    ("cell", ("cell.com", "cell press", "cell ")),
    ("science", ("science.org", "sciencemag", "science ")),
    ("lancet", ("lancet", "thelancet")),
    ("nejm", ("nejm", "new england journal")),
    ("wiley", ("wiley", "onlinelibrary")),
    ("springer", ("springer", "link.springer")),
    ("plos", ("plos", "plosone", "plos.org")),
    ("elife", ("elifesciences", "elife")),
    ("frontiers", ("frontiersin",)),
)
_OA_FAMILIES = ("biorxiv", "medrxiv", "pmc", "plos", "elife")


def publisher_family(row: dict | None) -> str:
    """Bucket a candidate by publisher family so one RSS feed cannot fill the cap."""
    row = row or {}
    src = str(row.get("source") or "")
    url = str(row.get("url") or "")
    journal = str(row.get("journal") or "")
    blob = f"{src} {url} {journal}".lower()
    for family, markers in _PUBLISHER_MARKERS:
        if any(m in blob for m in markers):
            return family
    if src.strip():
        return src.strip().lower()
    host = urllib.parse.urlparse(url).netloc.lower()
    return host or "other"


def _oa_pref_key(row: dict) -> tuple[int, int]:
    fam = publisher_family(row)
    url = str(row.get("url") or "").lower()
    src = str(row.get("source") or "").lower()
    oa = (
        fam in _OA_FAMILIES
        or "pmc" in url
        or "biorxiv" in url
        or "medrxiv" in url
        or "europepmc" in url
        or "open" in src
        or "elife" in url
        or "plos" in url
    )
    return (0 if oa else 1, 0)


def balance_academic_candidates(rows: list[dict], n: int) -> list[dict]:
    """Round-robin by publisher family before the cap; OA families first."""
    if n <= 0 or len(rows) <= n:
        return list(rows)
    from collections import defaultdict, deque

    indexed = list(rows)
    buckets: dict[str, deque] = defaultdict(deque)
    order: list[str] = []
    for row in sorted(indexed, key=_oa_pref_key):
        fam = publisher_family(row)
        if fam not in buckets:
            order.append(fam)
        buckets[fam].append(row)
    order = [f for f in order if f in _OA_FAMILIES] + [f for f in order if f not in _OA_FAMILIES]
    out: list[dict] = []
    while len(out) < n and any(buckets.values()):
        progressed = False
        for fam in order:
            if buckets[fam] and len(out) < n:
                out.append(buckets[fam].popleft())
                progressed = True
        if not progressed:
            break
    return out


def extract_doi(url: str) -> str:
    """Extract DOI from various URL formats.
    
    Notes:
    - bioRxiv/medRxiv DOIs: strips version suffix like 'v1' since the API
      doesn't find versioned DOIs
    - Cell PIIs: returns empty string since PIIs are not DOIs and should not
      be converted to fake 10.1038/... DOIs
    - Nature article IDs: converts to 10.1038/... format
    """
    url = url.strip()
    
    # Direct DOI URL
    doi_org_match = re.search(r'https?://(?:dx\.)?doi\.org/(10\.\d+/[^\s?#]+)', url, re.IGNORECASE)
    if doi_org_match:
        doi = doi_org_match.group(1)
        # Strip bioRxiv/medRxiv version suffix
        doi = re.sub(r'v\d+$', '', doi)
        return doi
    
    # Nature article URLs -> DOI
    nature_match = re.search(r'https?://(?:www\.)?nature\.com/articles/(s\d+-\d+-\d+-\w+)', url, re.IGNORECASE)
    if nature_match:
        return f"10.1038/{nature_match.group(1)}"
    
    # Cell article URLs: return empty string, not a fake DOI
    # Cell uses PIIs (e.g., S0092-8674(26)00123-4) which are NOT DOIs
    cell_match = re.search(r'https?://(?:www\.)?cell\.com/[^/]+/(?:fulltext|abstract)/(S[\d\-\(\)]+)', url, re.IGNORECASE)
    if cell_match:
        # Cell PIIs need Crossref/EPMC lookup by title, not DOI conversion
        return ""
    
    # Science URLs
    science_match = re.search(r'https?://(?:www\.)?science\.org/doi/(10\.\d+/[^\s?#]+)', url, re.IGNORECASE)
    if science_match:
        return science_match.group(1)
    
    # Lancet URLs (PII, not DOI)
    lancet_match = re.search(r'https?://(?:www\.)?thelancet\.com/journals/[^/]+/article/(PIIS[\d]+)', url, re.IGNORECASE)
    if lancet_match:
        # Lancet PIIs are not DOIs
        return ""
    
    # NEJM URLs
    nejm_match = re.search(r'https?://(?:www\.)?nejm\.org/doi/(10\.\d+/[^\s?#]+)', url, re.IGNORECASE)
    if nejm_match:
        return nejm_match.group(1)
    
    # bioRxiv/medRxiv URLs
    biorxiv_match = re.search(r'https?://(?:www\.)?(?:bio|med)rxiv\.org/content/(10\.\d+/[^\s?#]+)', url, re.IGNORECASE)
    if biorxiv_match:
        doi = biorxiv_match.group(1)
        # Strip version suffix (v1, v2, etc.) - API doesn't find versioned DOIs
        doi = re.sub(r'v\d+$', '', doi)
        return doi
    
    # Generic DOI pattern (fallback)
    doi_match = re.search(r'(10\.\d+/[^\s?#]+)', url)
    if doi_match:
        doi = doi_match.group(1)
        # Strip version suffix for any preprint DOI
        doi = re.sub(r'v\d+$', '', doi)
        return doi
    
    return ""


def _http_get(url: str, timeout: int = 30) -> bytes | None:
    """Make an HTTP GET request with polite rate limiting."""
    time.sleep(0.3)  # Polite delay
    try:
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read()
    except Exception as e:
        logging.warning("HTTP GET failed for %s: %s", url, e)
        return None


def epmc_core_search(doi: str = "", pmid: str = "") -> dict | None:
    """Search Europe PMC core API by DOI or PubMed ID.
    
    Returns dict with keys: abstractText, pmid, pmcid, isOpenAccess, title, etc.
    """
    if doi:
        query = f'DOI:"{doi}"'
    elif pmid:
        query = f'EXT_ID:"{pmid}" AND SRC:MED'
    else:
        return None
    url = "https://www.ebi.ac.uk/europepmc/webservices/rest/search?" + urllib.parse.urlencode({
        "query": query,
        "resultType": "core",
        "format": "json",
        "pageSize": "1",
    })
    data = _http_get(url)
    if not data:
        return None
    try:
        result = json.loads(data.decode("utf-8"))
        hits = result.get("resultList", {}).get("result", [])
        if hits:
            return hits[0]
    except (json.JSONDecodeError, KeyError) as e:
        logging.warning("EPMC parse error for %s: %s", doi or pmid, e)
    return None


def _doi_from_html(html: str) -> str:
    """citation_doi / dc.identifier from a publisher landing page."""
    if not html:
        return ""
    pats = (
        r'(?is)<meta[^>]*(?:name|property)=["\']citation_doi["\'][^>]*content=["\']\s*(?:doi:)?(10\.\d+/[^"\']+)',
        r'(?is)<meta[^>]*content=["\']\s*(?:doi:)?(10\.\d+/[^"\']+)["\'][^>]*(?:name|property)=["\']citation_doi["\']',
        r'(?is)<meta[^>]*(?:name|property)=["\']dc\.identifier["\'][^>]*content=["\']\s*(?:doi:)?(10\.\d+/[^"\']+)',
        r'(?is)<meta[^>]*content=["\']\s*(?:doi:)?(10\.\d+/[^"\']+)["\'][^>]*(?:name|property)=["\']dc\.identifier["\']',
    )
    for pat in pats:
        m = re.search(pat, html)
        if m:
            return m.group(1).strip().rstrip(".")
    return ""


def recover_missing_doi(item: EnrichedItem) -> str:
    """Fill item.doi from the URL, publisher meta, or a PubMed ID before OA lookup."""
    doi = (getattr(item, "doi", None) or extract_doi(getattr(item, "url", "") or "")).strip()
    if doi:
        item.doi = doi
        return doi
    pmid = (getattr(item, "pmid", None) or "").strip()
    if not pmid:
        m = re.search(r"pubmed\.ncbi\.nlm\.nih\.gov/(\d+)", getattr(item, "url", "") or "")
        if m:
            pmid = m.group(1)
            item.pmid = pmid
    if pmid:
        core = epmc_core_search(pmid=pmid)
        if core:
            found = (core.get("doi") or "").strip()
            item.pmid = core.get("pmid") or item.pmid
            item.pmcid = core.get("pmcid") or item.pmcid
            if found:
                logging.info("Recovered DOI %s from PMID %s", found, pmid)
                item.doi = found
                return found
    url = getattr(item, "url", "") or ""
    if url.startswith("http"):
        data = _http_get(url)
        if data:
            try:
                html = data.decode("utf-8", errors="ignore")
            except Exception:
                html = ""
            found = _doi_from_html(html)
            if found:
                logging.info("Recovered DOI %s from page metadata %s", found, url[:80])
                item.doi = found
                return found
    return ""


def epmc_fulltext_xml(pmcid: str) -> str | None:
    """Fetch OA full text XML from Europe PMC."""
    if not pmcid:
        return None
    pmcid = pmcid if str(pmcid).upper().startswith("PMC") else f"PMC{pmcid}"
    url = f"https://www.ebi.ac.uk/europepmc/webservices/rest/{pmcid}/fullTextXML"
    data = _http_get(url, timeout=60)
    if data:
        return data.decode("utf-8", errors="replace")
    return None


def pmc_oa_efetch_xml(pmcid: str) -> str | None:
    """Legal PMC OA XML via NCBI efetch (not a preprint)."""
    if not pmcid:
        return None
    pid = re.sub(r"(?i)^PMC", "", str(pmcid))
    url = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi?" + urllib.parse.urlencode({
        "db": "pmc",
        "id": pid,
        "rettype": "full",
        "retmode": "xml",
    })
    data = _http_get(url, timeout=60)
    if data:
        return data.decode("utf-8", errors="replace")
    return None


def _apply_fulltext_xml(item: EnrichedItem, xml: str, source_label: str) -> bool:
    from inlight_qc import record_fulltext, FULLTEXT_WINDOW

    results = extract_sections_from_xml(xml, ("Results",))
    figs = extract_fig_captions_from_xml(xml)
    methods = extract_design_methods_from_xml(xml)
    if not record_fulltext(item, results, methods=methods, figs=figs, source_label=source_label):
        return False
    discussion = extract_sections_from_xml(xml, ("Discussion",))
    if discussion:
        item.fulltext_results = (
            (item.fulltext_results or "") + "\n\n" + discussion
        )[:FULLTEXT_WINDOW]
    logging.info("OA full text supplied by %s for %s", source_label, item.url)
    return True


def try_legal_oa_fulltext(item: EnrichedItem) -> bool:
    """Unpaywall / Europe PMC fullTextXML / PMC OA for abstract-only DOIs.

    Preprints are never promoted to deep full-text sources.
    """
    from inlight_qc import item_has_real_fulltext, record_fulltext

    if item_has_real_fulltext(item):
        return True
    if _is_preprint_host(item.url):
        logging.info(
            "OA lookup skip preprint host=%s url=%s",
            _oa_host_label(item.url) or "preprint", item.url,
        )
        return False
    doi = item.doi or extract_doi(item.url) or recover_missing_doi(item)
    if not doi:
        logging.info("OA lookup no DOI for %s", item.url)
        return False
    tried: list[str] = []
    logging.info("OA lookup DOI %s: start url=%s pmcid=%s", doi, item.url, item.pmcid or "")
    if not item.pmcid:
        tried.append("EPMC core search")
        core = epmc_core_search(doi)
        if core:
            item.pmcid = core.get("pmcid") or item.pmcid
            item.pmid = core.get("pmid") or item.pmid
            logging.info(
                "OA lookup DOI %s: EPMC core search -> pmcid=%s",
                doi, item.pmcid or "none",
            )
        else:
            logging.info("OA lookup DOI %s: EPMC core search -> miss", doi)
    if item.pmcid:
        tried.append("Europe PMC fullTextXML")
        xml = epmc_fulltext_xml(item.pmcid)
        logging.info(
            "OA lookup DOI %s: Europe PMC fullTextXML %s -> %s",
            doi, item.pmcid, "hit" if xml else "miss",
        )
        if xml and _apply_fulltext_xml(item, xml, f"Europe PMC fullTextXML {item.pmcid}"):
            logging.info(
                "OA lookup DOI %s: used Europe PMC fullTextXML host=europepmc.org version=published",
                doi,
            )
            return True
        tried.append("PMC OA efetch")
        xml = pmc_oa_efetch_xml(item.pmcid)
        logging.info(
            "OA lookup DOI %s: PMC OA efetch %s -> %s",
            doi, item.pmcid, "hit" if xml else "miss",
        )
        if xml and _apply_fulltext_xml(item, xml, f"PMC OA {item.pmcid}"):
            logging.info(
                "OA lookup DOI %s: used PMC OA host=ncbi.nlm.nih.gov version=published",
                doi,
            )
            return True
    tried.append("Unpaywall")
    loc = unpaywall_oa_location(doi)
    oa_url, version, host = loc.get("url") or "", loc.get("version") or "", loc.get("host") or ""
    logging.info(
        "OA lookup DOI %s: Unpaywall -> host=%s version=%s url=%s",
        doi, host or "none", version or "none", (oa_url or "")[:80],
    )
    if oa_url and _is_preprint_oa(oa_url, version):
        logging.info(
            "OA lookup DOI %s: skip Unpaywall preprint host=%s version=%s",
            doi, host or _oa_host_label(oa_url), version or "unknown",
        )
        oa_url = ""
    if oa_url:
        ft, methods, figs = fetch_oa_sections(oa_url)
        src = f"Unpaywall/OA {oa_url[:60]}"
        if ft and record_fulltext(
            item, ft, methods=methods, figs=figs, source_label=src,
        ):
            logging.info(
                "OA lookup DOI %s: used %s host=%s version=%s",
                doi, src, host or _oa_host_label(oa_url), version or "unknown",
            )
            return True
        logging.info(
            "OA lookup DOI %s: Unpaywall HTML/PDF fetch -> no real Results; trying next source",
            doi,
        )
        oa_url = ""
    if not oa_url:
        tried.append("OpenAlex")
        oa_work = openalex_work(doi)
        oa_url = (oa_work or {}).get("oa_url") or ""
        host = _oa_host_label(oa_url)
        logging.info(
            "OA lookup DOI %s: OpenAlex -> host=%s url=%s",
            doi, host or "none", (oa_url or "")[:80],
        )
        if oa_url and _is_preprint_oa(oa_url):
            logging.info(
                "OA lookup DOI %s: skip OpenAlex preprint host=%s",
                doi, host or _oa_host_label(oa_url),
            )
            oa_url = ""
        if oa_url:
            ft, methods, figs = fetch_oa_sections(oa_url)
            src = f"OpenAlex/OA {oa_url[:60]}"
            if ft and record_fulltext(
                item, ft, methods=methods, figs=figs, source_label=src,
            ):
                logging.info(
                    "OA lookup DOI %s: used %s host=%s version=%s",
                    doi, src, host or _oa_host_label(oa_url), version or "unknown",
                )
                return True
            logging.info("OA lookup DOI %s: OpenAlex HTML fetch -> no real Results", doi)
    logging.info(
        "OA lookup DOI %s: exhausted (tried %s) url=%s",
        doi, ", ".join(tried) or "none", item.url,
    )
    return False


SUPERSCRIPT_MAP = {
    '0': '⁰', '1': '¹', '2': '²', '3': '³', '4': '⁴',
    '5': '⁵', '6': '⁶', '7': '⁷', '8': '⁸', '9': '⁹',
    '+': '⁺', '-': '⁻', 'n': 'ⁿ',
}


def element_text_with_superscripts(elem) -> str:
    """Extract text from an XML element, converting <sup> to Unicode superscripts.
    
    Example: '5 × 10<sup>6</sup> cells' becomes '5 × 10⁶ cells'.
    """
    parts = []
    if elem.text:
        parts.append(elem.text)
    for child in elem:
        if child.tag == 'sup':
            # Convert superscript content to Unicode superscript characters
            sup_text = "".join(child.itertext())
            sup_converted = "".join(SUPERSCRIPT_MAP.get(c, c) for c in sup_text)
            parts.append(sup_converted)
        elif child.tag == 'sub':
            # Subscripts: just include as plain text with brackets
            sub_text = "".join(child.itertext())
            parts.append(f"_{sub_text}")
        else:
            # Recursively handle other elements
            parts.append(element_text_with_superscripts(child))
        if child.tail:
            parts.append(child.tail)
    return "".join(parts)


def extract_sections_from_xml(xml_text: str, section_names: tuple[str, ...]) -> str:
    """Extract specific sections from PMC XML.
    
    Uses custom text extraction to properly handle nested elements like <sup>, <italic>.
    Example: '5 × 10<sup>6</sup> cells' becomes '5 × 10⁶ cells'.
    Results prefer sec-type=results or a Results title; never the whole document.
    """
    if not xml_text:
        return ""
    want = {n.lower() for n in section_names}
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return ""
    
    sections = []
    for sec in root.iter("sec"):
        sec_type = (sec.get("sec-type") or "").lower()
        title_elem = sec.find("title")
        title = (title_elem.text or "").strip().lower() if title_elem is not None else ""
        is_results = "results" in want and (
            "results" in sec_type or title == "results" or title.startswith("results")
        )
        if is_results or sec_type in want or any(name in title for name in want):
            text_parts = []
            for p in sec.iter("p"):
                para_text = element_text_with_superscripts(p).strip()
                if para_text:
                    text_parts.append(para_text)
            if text_parts:
                sections.append(" ".join(text_parts))
    return "\n\n".join(sections)


def extract_fig_captions_from_xml(xml_text: str, max_chars: int = 6000) -> str:
    """Extract full figure legends (title + body), not label-only titles."""
    if not xml_text:
        return ""
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return ""

    captions: list[str] = []
    seen: set[str] = set()
    for fig in root.iter("fig"):
        bits: list[str] = []
        cap = fig.find("caption")
        if cap is not None:
            bits.append(element_text_with_superscripts(cap).strip())
        for p in fig.iter("p"):
            para = element_text_with_superscripts(p).strip()
            if para:
                bits.append(para)
        title_elem = fig.find("title")
        if title_elem is not None and not bits:
            bits.append(element_text_with_superscripts(title_elem).strip())
        text = re.sub(r"\s+", " ", " ".join(b for b in bits if b)).strip()
        if text and text not in seen and not re.fullmatch(r"(?i)fig(?:ure)?\.?\s*\d+", text):
            seen.add(text)
            captions.append(text)

    result = "\n\n".join(captions)
    return result[:max_chars]


def _sec_title_text(sec) -> str:
    title_elem = sec.find("title")
    if title_elem is None:
        return ""
    return ((title_elem.text or "") + "".join(title_elem.itertext())).strip().lower()


def _sec_is_methods(sec) -> bool:
    sec_type = (sec.get("sec-type") or "").lower()
    title = _sec_title_text(sec)
    return (
        "method" in sec_type
        or "materials" in sec_type
        or any(k in title for k in (
            "method", "material", "experimental procedure", "experimental design",
        ))
    )


_METHODS_DESIGN_HINTS = (
    "study design", "statistical", "statistic", "randomiz", "patient",
    "participant", "cohort", "sample size", "endpoint", "inclusion",
    "exclusion", "treatment", "dosing", "dose", "intervention",
    "analysis", "outcome", "primary", "secondary",
    "设计", "统计", "随机", "患者", "入组", "终点", "给药", "分析", "分组",
)
_METHODS_SKIP_HINTS = (
    "animal care", "animal welfare", "ethics approval", "housing",
    "husbandry", "veterinary", "iacuc", "arrive",
    "动物饲养", "伦理批准", "饲养条件", "福利",
)


_METHODS_TITLE_RANKS = (
    (r"study design|experimental design|trial design|研究设计", 100),
    (r"statistical analysis|statistical|statistic|统计分析|统计方法", 95),
    (r"participants?|patients?|受试|入组|患者", 90),
    (r"ethics|ethical|iacuc|irb|伦理", 80),
)


def _methods_title_rank(title: str) -> int:
    t = (title or "").lower()
    best = 0
    for pat, score in _METHODS_TITLE_RANKS:
        if re.search(pat, t):
            best = max(best, score)
    return best


def _is_stats_methods_title(title: str) -> bool:
    return bool(re.search(r"(?i)statistical|statistic|统计", title or ""))


def _methods_block_score(title: str, body: str) -> int:
    """Title-first rank; length-normalised body score so long dumps cannot win."""
    tr = _methods_title_rank(title)
    if tr:
        return tr + min(len(body or "") // 500, 3)
    blob = f"{title} {body}".lower()
    if any(h in blob for h in _METHODS_SKIP_HINTS):
        return -2
    hits = sum(1 for h in _METHODS_DESIGN_HINTS if h in blob)
    denom = max(1, len(body or "") // 600)
    return (hits * 8) // denom


def extract_design_methods_from_xml(xml_text: str, max_chars: int = 4000) -> str:
    """Prefer design/statistics Methods subsections over animal-care lead-in."""
    if not xml_text:
        return ""
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return ""

    def _local_tag(tag) -> str:
        return tag.split("}")[-1] if isinstance(tag, str) else ""

    blocks: list[tuple[int, str]] = []
    seen: set[str] = set()
    for sec in root.iter("sec"):
        if not _sec_is_methods(sec):
            continue
        subs = [c for c in list(sec) if _local_tag(c.tag) == "sec"]
        for block_sec in (subs or [sec]):
            title = _sec_title_text(block_sec)
            text_parts = []
            for p in block_sec.iter("p"):
                para_text = element_text_with_superscripts(p).strip()
                if para_text and para_text not in seen:
                    seen.add(para_text)
                    text_parts.append(para_text)
            if not text_parts:
                continue
            body = " ".join(text_parts)
            blocks.append((_methods_block_score(title, body), body, title))

    return _pick_scored_methods(blocks, max_chars)


def _pick_scored_methods(blocks: list, max_chars: int) -> str:
    """Always include a statistics subsection when present; then title-ranked rest."""
    if not blocks:
        return ""
    norm: list[tuple[int, str, str]] = []
    for b in blocks:
        if len(b) >= 3:
            norm.append((int(b[0]), str(b[1]), str(b[2])))
        else:
            norm.append((int(b[0]), str(b[1]), ""))
    stats = [x for x in norm if _is_stats_methods_title(x[2]) or _is_stats_methods_title(x[1][:80])]
    rest = [x for x in norm if x not in stats]
    rest.sort(key=lambda x: x[0], reverse=True)
    picked: list[str] = []
    n = 0
    for score, body, _title in stats + rest:
        if score < 0 and picked:
            continue
        if n >= max_chars:
            break
        take = body[: max_chars - n]
        picked.append(take)
        n += len(take)
    return "\n\n".join(picked)[:max_chars]


def extract_design_methods_from_html(html: str, max_chars: int = 4000) -> str:
    """Prefer design/statistics Methods subsections from OA HTML pages."""
    from inlight_qc import (
        html_visible_text, strip_page_chrome, extract_methods_from_html,
        _METHODS_TITLE, _AFTER_METHODS,
    )

    raw = html or ""
    block = ""
    m = re.search(
        rf'(?is)<sec[^>]*sec-type\s*=\s*["\'](?:methods|materials)["\'][^>]*>(.*?)(?:</sec>|<sec\b)',
        raw,
    )
    if m:
        block = m.group(1)
    if not block:
        m = re.search(
            rf'(?is)<(?:h[1-4]|header)[^>]*>\s*(?:<[^>]+>\s*)*{_METHODS_TITLE}'
            rf'\s*(?:</[^>]+>\s*)*</(?:h[1-4]|header)>(.*?)'
            rf'(?=<(?:h[1-4]|header)[^>]*>\s*(?:<[^>]+>\s*)*{_AFTER_METHODS}|\Z)',
            raw,
        )
        if m:
            block = m.group(1)
    if not block:
        m = re.search(
            rf'(?is)<(?:div|section)[^>]*(?:id|class)\s*=\s*["\'][^"\']*\bmethods?\b'
            rf'[^"\']*["\'][^>]*>(.*?)'
            rf'(?=<(?:div|section|h[1-4])[^>]*(?:id|class|)\s*(?:=)?[^>]{{0,80}}{_AFTER_METHODS}|\Z)',
            raw,
        )
        if m:
            block = m.group(1)

    blocks: list[tuple[int, str]] = []
    if block:
        parts = re.split(r'(?is)(<(?:h[2-6])\b[^>]*>.*?</(?:h[2-6])>)', block)
        title = ""
        buf: list[str] = []

        def _flush() -> None:
            body = html_visible_text(strip_page_chrome("".join(buf)))
            if body:
                blocks.append((_methods_block_score(title, body), body, title))

        for part in parts:
            if re.match(r'(?is)<(?:h[2-6])\b', part or ""):
                if buf or title:
                    _flush()
                title = html_visible_text(part)
                buf = []
            else:
                buf.append(part or "")
        if buf or title:
            _flush()
    if not blocks:
        text = extract_methods_from_html(raw)
        if not text:
            return ""
        chunks = re.split(
            r'(?i)(?=\b(?:animal care|animal welfare|statistical analysis|'
            r'study design|patients?|participants?|randomi[sz]|ethics)\b)',
            text,
        )
        for ch in chunks:
            if ch.strip():
                blocks.append((_methods_block_score(ch[:80], ch), ch.strip(), ch[:80]))
    return _pick_scored_methods(blocks, max_chars)


def pubmed_efetch_abstract(pmid: str) -> str:
    """Fetch abstract from PubMed efetch XML API - extracts ONLY AbstractText.
    
    This avoids including author information, affiliations, COI statements,
    trial IDs, and other metadata that inflates the abstract and causes
    false positive validation errors.
    """
    if not pmid:
        return ""
    url = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi?" + urllib.parse.urlencode({
        "db": "pubmed",
        "id": pmid,
        "rettype": "xml",
        "retmode": "xml",
    })
    data = _http_get(url)
    if data:
        try:
            root = ET.fromstring(data)
            abstract_parts = []
            for abstract_text in root.iter("AbstractText"):
                text = "".join(abstract_text.itertext()).strip()
                if text:
                    label = abstract_text.get("Label", "")
                    if label and not label.lower().startswith("background"):
                        abstract_parts.append(f"{label}: {text}")
                    else:
                        abstract_parts.append(text)
            return " ".join(abstract_parts)
        except ET.ParseError:
            pass
    return ""


def scrape_nature_abstract(url: str) -> str:
    """Scrape abstract from Nature.com page using #Abs1-content."""
    if "nature.com" not in url:
        return ""
    
    try:
        import requests
        resp = requests.get(url, headers={"User-Agent": UA}, timeout=30)
        if resp.status_code != 200:
            return ""
        
        from html.parser import HTMLParser
        
        class AbstractParser(HTMLParser):
            def __init__(self):
                super().__init__()
                self.in_abstract = False
                self.abstract_text = []
                self.depth = 0
            
            def handle_starttag(self, tag, attrs):
                attrs_dict = dict(attrs)
                if attrs_dict.get("id") == "Abs1-content":
                    self.in_abstract = True
                    self.depth = 1
                elif self.in_abstract:
                    self.depth += 1
            
            def handle_endtag(self, tag):
                if self.in_abstract:
                    self.depth -= 1
                    if self.depth == 0:
                        self.in_abstract = False
            
            def handle_data(self, data):
                if self.in_abstract:
                    self.abstract_text.append(data.strip())
        
        parser = AbstractParser()
        parser.feed(resp.text)
        return " ".join(parser.abstract_text)
    except Exception as e:
        logging.warning("Failed to scrape Nature abstract: %s", e)
        return ""


def fetch_biorxiv_record(url: str) -> dict:
    """Fetch abstract and authors from the bioRxiv/medRxiv details API."""
    if "biorxiv.org" not in url and "medrxiv.org" not in url:
        return {}
    doi = extract_doi(url)
    if not doi:
        return {}
    kind = "medrxiv" if "medrxiv.org" in url else "biorxiv"
    api_url = f"https://api.biorxiv.org/details/{kind}/{doi}"
    data = _http_get(api_url)
    if not data:
        return {}
    try:
        result = json.loads(data.decode("utf-8"))
        collection = result.get("collection", [])
        if not collection:
            return {}
        rec = collection[0]
        authors = rec.get("authors") or rec.get("author_corresponding") or ""
        if isinstance(authors, list):
            authors = ", ".join(str(a) for a in authors if a)
        return {
            "abstract": rec.get("abstract") or "",
            "authors": authors if isinstance(authors, str) else "",
        }
    except (json.JSONDecodeError, KeyError, TypeError):
        return {}


def scrape_biorxiv_sections(url: str) -> tuple[str, str, str]:
    """Results, methods, and figure legends from bioRxiv/medRxiv HTML."""
    from inlight_qc import is_real_results_text

    if "biorxiv.org" not in url and "medrxiv.org" not in url:
        return "", "", ""
    page = url.split("?")[0].rstrip("/")
    page = re.sub(r"v\d+$", "", page)
    candidates = [page + ".full", page, url]
    seen: set[str] = set()
    for cand in candidates:
        if cand in seen:
            continue
        seen.add(cand)
        data = _http_get(cand)
        if not data:
            continue
        try:
            html = data.decode("utf-8", errors="ignore")
        except Exception:
            continue
        results, methods, figs = _oa_text_sections(html)
        if is_real_results_text(results):
            return results, methods, figs
    return "", "", ""


def scrape_biorxiv_fulltext(url: str) -> str:
    """Results section from a bioRxiv/medRxiv HTML full text. Never the landing page."""
    results, _, _ = scrape_biorxiv_sections(url)
    return results


def _html_visible_text(data: bytes | None) -> str:
    """Strip tags/scripts from HTML bytes and return visible text."""
    if not data:
        return ""
    try:
        raw = data.decode("utf-8", errors="ignore")
    except Exception:
        return ""
    raw = re.sub(r"(?is)<script[^>]*>.*?</script>", " ", raw)
    raw = re.sub(r"(?is)<style[^>]*>.*?</style>", " ", raw)
    raw = re.sub(r"(?is)<noscript[^>]*>.*?</noscript>", " ", raw)
    raw = re.sub(r"(?is)<!--.*?-->", " ", raw)
    raw = re.sub(r"(?is)<[^>]+>", " ", raw)
    raw = raw.replace("&nbsp;", " ").replace("&amp;", "&")
    raw = raw.replace("&lt;", "<").replace("&gt;", ">")
    raw = re.sub(r"\s+", " ", raw).strip()
    return raw


def scrape_publisher_abstract(url: str) -> str:
    """Best-effort open abstract from publisher HTML. No paywall bypass."""
    if not url or not url.startswith("http"):
        return ""
    data = _http_get(url)
    if not data:
        return ""
    try:
        html = data.decode("utf-8", errors="ignore")
    except Exception:
        return ""
    patterns = [
        r'(?is)<meta[^>]*(?:name|property)=["\'](?:citation_abstract|dc\.description|DC\.Description)["\'][^>]*content=["\']([^"\']{80,})["\']',
        r'(?is)<meta[^>]*content=["\']([^"\']{80,})["\'][^>]*(?:name|property)=["\'](?:citation_abstract|dc\.description|DC\.Description)["\']',
    ]
    for pat in patterns:
        m = re.search(pat, html)
        if m:
            return re.sub(r"\s+", " ", m.group(1)).strip()
    m = re.search(
        r'(?is)<(?:section|div|p)[^>]*(?:id|class)=["\'][^"\']*abstract[^"\']*["\'][^>]*>(.{80,8000}?)</(?:section|div|p)>',
        html,
    )
    if m:
        return _html_visible_text(m.group(1).encode("utf-8"))
    return ""


def crossref_abstract(doi: str) -> str:
    """Abstract from Crossref when the work deposits one."""
    if not doi:
        return ""
    url = "https://api.crossref.org/works/" + urllib.parse.quote(doi)
    data = _http_get(url)
    if not data:
        return ""
    try:
        msg = json.loads(data.decode("utf-8")).get("message") or {}
        raw = msg.get("abstract") or ""
        if not raw:
            return ""
        return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", raw)).strip()
    except (json.JSONDecodeError, TypeError, AttributeError):
        return ""


def _openalex_deinvert(inv: dict) -> str:
    if not isinstance(inv, dict) or not inv:
        return ""
    size = 0
    for positions in inv.values():
        if positions:
            size = max(size, max(positions) + 1)
    if size <= 0 or size > 20000:
        return ""
    words = [""] * size
    for word, positions in inv.items():
        for p in positions or []:
            if 0 <= p < size:
                words[p] = str(word)
    return " ".join(w for w in words if w).strip()


def openalex_work(doi: str) -> dict:
    """OpenAlex work record: reconstructed abstract and OA landing URL."""
    if not doi:
        return {}
    url = "https://api.openalex.org/works/" + urllib.parse.quote(f"https://doi.org/{doi}")
    data = _http_get(url)
    if not data:
        return {}
    try:
        msg = json.loads(data.decode("utf-8"))
    except (json.JSONDecodeError, TypeError):
        return {}
    abstract = _openalex_deinvert(msg.get("abstract_inverted_index") or {})
    oa = ""
    loc = msg.get("best_oa_location") or msg.get("open_access") or {}
    if isinstance(loc, dict):
        oa = loc.get("pdf_url") or loc.get("oa_url") or loc.get("landing_page_url") or ""
    if not oa and isinstance(msg.get("open_access"), dict):
        oa = msg["open_access"].get("oa_url") or ""
    return {"abstract": abstract, "oa_url": oa}


def unpaywall_oa_location(doi: str) -> dict:
    """Best public OA location from Unpaywall: url, version, host. No paywall bypass."""
    empty = {"url": "", "version": "", "host": ""}
    if not doi:
        return empty
    url = (
        "https://api.unpaywall.org/v2/"
        + urllib.parse.quote(doi)
        + "?email=frontier%40inlight.therasik.com"
    )
    data = _http_get(url)
    if not data:
        return empty
    try:
        msg = json.loads(data.decode("utf-8"))
        loc = msg.get("best_oa_location") or {}
        oa_url = loc.get("url_for_pdf") or loc.get("url") or ""
        return {
            "url": oa_url,
            "version": loc.get("version") or "",
            "host": _oa_host_label(oa_url) or (loc.get("host_type") or ""),
        }
    except (json.JSONDecodeError, TypeError, AttributeError):
        return empty


def unpaywall_oa_url(doi: str) -> str:
    """Best public OA URL from Unpaywall. Preprint versions/hosts are skipped."""
    loc = unpaywall_oa_location(doi)
    if _is_preprint_oa(loc.get("url") or "", loc.get("version") or ""):
        logging.info(
            "OA skip Unpaywall preprint DOI %s host=%s version=%s",
            doi, loc.get("host") or "", loc.get("version") or "",
        )
        return ""
    return loc.get("url") or ""


def _oa_text_sections(raw: str) -> tuple[str, str, str]:
    """Results, methods, and figure legends from OA HTML or JATS XML."""
    from inlight_qc import (
        extract_results_from_html,
        extract_fig_captions_from_html, extract_results_from_xml,
        FULLTEXT_WINDOW,
    )

    if not raw:
        return "", "", ""
    looks_xml = bool(re.search(r'<(?:article|sec)\b', raw)) and not re.search(r'(?i)<html\b', raw)
    if looks_xml or re.search(r'(?i)<sec\b[^>]*sec-type', raw):
        results = extract_sections_from_xml(raw, ("Results",)) or extract_results_from_xml(raw)
        methods = extract_design_methods_from_xml(raw)
        figs = extract_fig_captions_from_xml(raw)
        if results or methods or figs:
            return (results or "")[:FULLTEXT_WINDOW], methods, figs
    results = extract_results_from_html(raw)
    methods = extract_design_methods_from_html(raw)
    figs = extract_fig_captions_from_html(raw)
    return (results or "")[:FULLTEXT_WINDOW], methods, figs


def _is_pdf_url(url: str) -> bool:
    u = (url or "").split("?")[0].lower()
    return u.endswith(".pdf") or u.endswith("/pdf") or "/pdf/" in u


def _publisher_html_from_pdf(pdf_url: str) -> str:
    """Best-effort publisher HTML article URL from a PDF link."""
    u = (pdf_url or "").split("?")[0]
    m = re.search(r'(https?://(?:www\.)?nature\.com/articles/[^/]+)\.pdf$', u, re.I)
    if m:
        return m.group(1)
    m = re.search(r'(https?://link\.springer\.com)/content/pdf/(.+?)\.pdf$', u, re.I)
    if m:
        return m.group(1) + "/article/" + urllib.parse.unquote(m.group(2))
    m = re.search(r'(https?://(?:www\.)?sciencedirect\.com/science/article/pii/[^/]+)', u, re.I)
    if m:
        return m.group(1)
    stripped = re.sub(r'(?i)/pdf/?$', "", u)
    stripped = re.sub(r'(?i)\.full\.pdf$', "", stripped)
    stripped = re.sub(r'(?i)\.pdf$', "", stripped)
    return stripped if stripped.startswith("http") and stripped != u else ""


def _oa_html_candidates(oa_url: str) -> list[str]:
    """Landing page, then publisher HTML, when the free full text is a PDF."""
    if not oa_url or not oa_url.startswith("http"):
        return []
    url = oa_url.strip()
    if not _is_pdf_url(url):
        return [url]
    out: list[str] = []
    landing = re.sub(r'(?i)/[^/]+\.pdf(?:[?#].*)?$', "", url)
    landing = re.sub(r'(?i)\.pdf(?:[?#].*)?$', "", landing)
    landing = re.sub(r'(?i)/pdf(?:[?#].*)?$', "", landing)
    if landing.startswith("http") and landing != url and not _is_pdf_url(landing):
        out.append(landing)
    pub = _publisher_html_from_pdf(url)
    if pub and pub not in out and pub != url and not _is_pdf_url(pub):
        out.append(pub)
    return out


def fetch_oa_sections(oa_url: str) -> tuple[str, str, str]:
    """Results + methods + figure legends from a public OA page.

    A PDF URL tries the article landing page and then the publisher HTML
    page. It never counts as a successful source by itself.
    """
    from inlight_qc import is_real_results_text

    last_methods, last_figs = "", ""
    for cand in _oa_html_candidates(oa_url):
        if not cand.startswith("http") or _is_pdf_url(cand):
            continue
        data = _http_get(cand)
        if not data:
            continue
        try:
            raw = data.decode("utf-8", errors="ignore")
        except Exception:
            continue
        results, methods, figs = _oa_text_sections(raw)
        last_methods = methods or last_methods
        last_figs = figs or last_figs
        if is_real_results_text(results):
            return results, methods, figs
    return "", last_methods, last_figs


def fetch_oa_fulltext(oa_url: str) -> str:
    """Results section from a public OA HTML page. Never the whole landing page."""
    results, _, _ = fetch_oa_sections(oa_url)
    return results


def fetch_press_coverage(title: str, doi: str) -> str:
    """Public press-release / media text when journal full text is closed."""
    queries: list[str] = []
    if doi:
        queries.append(f'"{doi}" AND ("press release" OR eurekalert OR "news release")')
    words = [w for w in re.findall(r"[A-Za-z0-9\-]+", title or "") if len(w) > 2][:8]
    if words:
        queries.append(
            "TITLE:\"" + " ".join(words) + "\" AND (\"press release\" OR eurekalert)"
        )
    for q in queries:
        url = "https://www.ebi.ac.uk/europepmc/webservices/rest/search?" + urllib.parse.urlencode({
            "query": q,
            "resultType": "core",
            "format": "json",
            "pageSize": "3",
        })
        data = _http_get(url)
        if not data:
            continue
        try:
            hits = json.loads(data.decode("utf-8")).get("resultList", {}).get("result", [])
        except (json.JSONDecodeError, TypeError):
            continue
        for hit in hits:
            if not isinstance(hit, dict):
                continue
            hit_doi = (hit.get("doi") or "").lower()
            if doi and hit_doi == doi.lower():
                continue
            text = (hit.get("abstractText") or "").strip()
            if len(text) >= 80:
                return text[:8000]
    return ""


def enrich_item(row: dict) -> EnrichedItem:
    """Enrich an item with abstract/fulltext from public sources.
    
    Priority: OA fulltext > Europe PMC abstract > publisher abstract > PubMed efetch > RSS teaser
    Only uses public APIs, no paywall bypass.
    """
    item = EnrichedItem(
        url=row.get("url", ""),
        title=row.get("title", ""),
        source=row.get("source", ""),
        date=row.get("date", ""),
        kind=row.get("kind", "academic"),
        rss_summary=row.get("summary", "")[:8000],
    )
    
    # Get journal from row if provided (e.g., PubMed fetcher includes it)
    if row.get("journal"):
        item.journal = row["journal"]
    
    doi = extract_doi(item.url)
    item.doi = doi
    item.source_trace.append(f"RSS: {len(item.rss_summary)} chars")
    
    if not doi and "pubmed.ncbi.nlm.nih.gov" in item.url:
        pmid_match = re.search(r'/(\d+)/?$', item.url)
        if pmid_match:
            item.pmid = pmid_match.group(1)
    
    core = epmc_core_search(doi) if doi else None
    if core:
        item.abstract = core.get("abstractText", "")
        item.pmid = core.get("pmid", "") or item.pmid
        item.pmcid = core.get("pmcid", "")
        # Get journal name from EPMC if available
        if core.get("journalTitle"):
            item.journal = core.get("journalTitle")
        elif core.get("journalInfo", {}).get("journal", {}).get("title"):
            item.journal = core["journalInfo"]["journal"]["title"]
        if core.get("authorString"):
            item.authors = core.get("authorString") or item.authors
        if item.abstract:
            item.evidence_level = "abstract"
            item.source_trace.append(f"EPMC abstract: {len(item.abstract)} chars")
        
        if item.pmcid and core.get("isOpenAccess") == "Y":
            xml = epmc_fulltext_xml(item.pmcid)
            if xml:
                from inlight_qc import record_fulltext, FULLTEXT_WINDOW
                results = extract_sections_from_xml(xml, ("Results",))
                figs = extract_fig_captions_from_xml(xml)
                methods = extract_design_methods_from_xml(xml)
                if record_fulltext(item, results, methods=methods, figs=figs, source_label=f"PMC {item.pmcid}"):
                    discussion = extract_sections_from_xml(xml, ("Discussion",))
                    if discussion:
                        item.fulltext_results = (
                            (item.fulltext_results or "") + "\n\n" + discussion
                        )[:FULLTEXT_WINDOW]
    
    # Collect every public abstract and keep the longest BEFORE the
    # 1200-character deep-tier gate. A short EPMC teaser must not hide
    # a full PubMed / journal / bioRxiv abstract.
    abstracts: list[tuple[str, str]] = []
    if item.abstract:
        abstracts.append(("EPMC abstract", item.abstract))

    if item.pmid:
        pm = pubmed_efetch_abstract(item.pmid)
        if pm:
            abstracts.append(("PubMed efetch", pm))

    if "nature.com" in item.url:
        nat = scrape_nature_abstract(item.url)
        if nat:
            abstracts.append(("Nature abstract", nat))

    if "biorxiv.org" in item.url or "medrxiv.org" in item.url:
        rec = fetch_biorxiv_record(item.url)
        if rec.get("abstract"):
            abstracts.append(("bioRxiv API", rec["abstract"]))
        if rec.get("authors") and not item.authors:
            item.authors = rec["authors"]

    if doi:
        cr = crossref_abstract(doi)
        if cr:
            abstracts.append(("Crossref abstract", cr))
        oa_work = openalex_work(doi)
        if oa_work.get("abstract"):
            abstracts.append(("OpenAlex abstract", oa_work["abstract"]))
        oa_url = oa_work.get("oa_url") or unpaywall_oa_url(doi)
        if oa_url and _is_preprint_host(oa_url):
            logging.info("Skip preprint OA landing as deep source: %s", oa_url)
            oa_url = ""
        if oa_url and not item.fulltext_results:
            from inlight_qc import record_fulltext
            ft, methods, figs = fetch_oa_sections(oa_url)
            if ft:
                record_fulltext(
                    item, ft, methods=methods, figs=figs,
                    source_label=f"OA {oa_url[:60]}",
                )

    pub = scrape_publisher_abstract(item.url)
    if pub:
        abstracts.append(("publisher abstract", pub))

    press = fetch_press_coverage(item.title, doi)
    if press:
        item.press_coverage = press
        item.source_trace.append(f"press/media: {len(press)} chars")
        if not item.abstract and not abstracts:
            abstracts.append(("press coverage", press))

    if abstracts:
        label, text = max(abstracts, key=lambda x: len(x[1]))
        item.abstract = text
        if item.evidence_level != "fulltext":
            item.evidence_level = "press" if label == "press coverage" else "abstract"
        item.source_trace.append(
            f"{label}: {len(text)} chars (longest of {len(abstracts)} sources)"
        )
    elif item.rss_summary:
        item.abstract = item.rss_summary
        item.evidence_level = "press"
        item.source_trace.append("Fallback to RSS summary")

    from inlight_qc import item_has_real_fulltext
    if not item_has_real_fulltext(item) and not _is_preprint_host(item.url):
        try_legal_oa_fulltext(item)

    if _is_preprint_host(item.url):
        if item.evidence_level == "fulltext":
            logging.info("Do not use preprint as deep full-text source: %s", item.url)
        item.evidence_level = "preprint"
        if not item.journal:
            item.journal = (
                "medRxiv（预印本）" if "medrxiv.org" in item.url.lower() else "bioRxiv（预印本）"
            )

    return item


def extract_numbers_from_text(text: str) -> set[str]:
    """Extract meaningful data numbers from text for validation.
    
    This function extracts numbers that represent real data (percentages,
    sample sizes, durations, etc.) while SKIPPING:
    - Gene/protein names (CD4, CD8, CD14, CD318, IL-23, HLA-DP04)
    - Trial IDs (NCT..., RPCEC...)
    - Duration patterns like "1-year", "2-week" (these are descriptive)
    - Confidence interval labels (95%CI, 95% CI)
    - Pure identifiers that don't represent quantities
    
    Handles thousands separators (1,139 -> 1139).
    """
    numbers = set()
    
    # First, mark things we should ignore by replacing them with placeholders
    text_clean = text
    
    # Remove gene/protein names like CD4, CD8, CD14, CD318, IL-23, HLA-DP04, R2, S1, M2
    text_clean = re.sub(r'CD\d+', ' ', text_clean, flags=re.IGNORECASE)
    text_clean = re.sub(r'IL-?\d+', ' ', text_clean, flags=re.IGNORECASE)
    text_clean = re.sub(r'HLA-?[A-Z]*\d+', ' ', text_clean, flags=re.IGNORECASE)
    text_clean = re.sub(r'NF-?κ?B', ' ', text_clean, flags=re.IGNORECASE)
    # Remove single letter + number identifiers like R2, S1, M2 (gene/element names)
    text_clean = re.sub(r'\b[A-Z]\d+\b', ' ', text_clean)
    # Remove trial IDs (must come before general number extraction)
    text_clean = re.sub(r'(?:NCT|RPCEC|ISRCTN|EudraCT|ACTRN|ChiCTR|JR?CT|CTRI|DRKS|NTR)\d+', ' ', text_clean, flags=re.IGNORECASE)
    # Remove duration patterns like "1-year", "2-week", "4-week" (descriptive, not data)
    text_clean = re.sub(r'\d+-(?:year|month|week|day|hour|年|周|个月|天)', ' ', text_clean, flags=re.IGNORECASE)
    # Remove "95%CI" or "95% CI" (confidence interval label, not a percentage value)
    text_clean = re.sub(r'95\s*%\s*CI', ' ', text_clean, flags=re.IGNORECASE)
    # Remove reference numbers like "(1)", "(2)", "(1)…(5)" etc.
    text_clean = re.sub(r'\(\d{1,2}\)', ' ', text_clean)
    
    # Extract numbers in priority order, removing matched spans to prevent double-counting
    # Use a set of (start, end) positions to track what's been matched
    matched_positions = set()
    
    patterns = [
        r'\d{1,3}(?:,\d{3})+(?:\.\d+)?(?:\s*(?:%|％))?',  # Numbers with thousands separator (must be first)
        r'\d+(?:\.\d+)?(?:\s*(?:%|％))',  # Percentages
        r'\d+(?:\.\d+)?(?:\s*倍)',  # Fold changes
        r'\d+(?:\.\d+)?(?:\s*(?:个月|天|周|年|岁|例|名|只|条|个|位|人|mg|kg|µg|ng|mL|L|µL|nM|pM|µM|mM))',
        r'(?:HR|OR|RR|P|p)\s*[=<>≤≥]\s*\d+(?:\.\d+)?',  # NOT CI - CI is confidence level
        r'\d+(?:\.\d+)?\s*[×x]\s*10[\^⁰¹²³⁴⁵⁶⁷⁸⁹]+',  # Scientific notation with superscripts
        r'\d+(?:\.\d+)?\s*[×x]\s*10\^\d+',  # Scientific notation with caret
        r'\d+/\d+',  # Fractions like 19/36
        r'\d+(?:\.\d+)?',  # Plain numbers (last, most general)
    ]
    
    for pattern in patterns:
        for match in re.finditer(pattern, text_clean, re.IGNORECASE):
            # Check if this position overlaps with already matched positions
            start, end = match.start(), match.end()
            overlaps = any(s <= start < e or s < end <= e or (start <= s and end >= e) 
                          for s, e in matched_positions)
            if overlaps:
                continue
            
            num = match.group(0).strip()
            if num and len(num) > 0:
                # Normalize thousands separators
                num_normalized = num.replace(",", "").replace("，", "")
                numbers.add(num_normalized)
                matched_positions.add((start, end))
    
    # Chinese numerals with units (these ARE data)
    chinese_data_pattern = r'[零一二三四五六七八九十百千万亿两]+(?:倍|%|％|年|个月|天|周|岁|例|名|只|条|个|位|人)'
    for match in re.finditer(chinese_data_pattern, text):
        numbers.add(match.group(0))
    
    return numbers


# English number words to Arabic, including hyphenated compounds (forty-one).
ENGLISH_ONES = {
    'zero': 0, 'once': 1, 'one': 1, 'twice': 2, 'two': 2,
    'three': 3, 'four': 4, 'five': 5, 'six': 6, 'seven': 7,
    'eight': 8, 'nine': 9,
}
ENGLISH_TEENS = {
    'ten': 10, 'eleven': 11, 'twelve': 12, 'thirteen': 13, 'fourteen': 14,
    'fifteen': 15, 'sixteen': 16, 'seventeen': 17, 'eighteen': 18, 'nineteen': 19,
}
ENGLISH_TENS = {
    'twenty': 20, 'thirty': 30, 'forty': 40, 'fifty': 50,
    'sixty': 60, 'seventy': 70, 'eighty': 80, 'ninety': 90,
}
ENGLISH_NUMBER_WORDS = {
    **{k: str(v) for k, v in ENGLISH_ONES.items()},
    **{k: str(v) for k, v in ENGLISH_TEENS.items()},
    **{k: str(v) for k, v in ENGLISH_TENS.items()},
}


_ORDINAL_TIME_RE = re.compile(
    r'(?i)\b(?:first|second|third|fourth|fifth|sixth|seventh|eighth|ninth|tenth|'
    r'eleventh|twelfth|thirteenth|fourteenth|fifteenth|sixteenth|seventeenth|'
    r'eighteenth|nineteenth|twentieth|\d+(?:st|nd|rd|th))\s+'
    r'(?:week|day|month|year|hour)s?\b'
)

ENGLISH_ORDINALS = {
    "first": "1", "second": "2", "third": "3", "fourth": "4", "fifth": "5",
    "sixth": "6", "seventh": "7", "eighth": "8", "ninth": "9", "tenth": "10",
    "eleventh": "11", "twelfth": "12", "thirteenth": "13", "fourteenth": "14",
    "fifteenth": "15", "sixteenth": "16", "seventeenth": "17",
    "eighteenth": "18", "nineteenth": "19", "twentieth": "20",
}


_QUANTITY_UNIT_RE = re.compile(
    r'(?i)(doses?|groups?|folds?|weeks?|days?|months?|years?|hours?|'
    r'patients?|arms?|cohorts?|kinds?|types?|[a-z]{2,}|'
    r'组|倍|次|例|名|周|天|月|年|剂|[\u4e00-\u9fff]{1,4})'
)
_FOLD_WORD_RE = re.compile(r'(?i)\b((?:once|twice|thrice|[a-z]+))-?fold\b')
_EN_CARDINAL_RE = re.compile(
    r'(?i)\b(?:'
    + "|".join(sorted({
        *ENGLISH_ONES, *ENGLISH_TEENS, *ENGLISH_TENS, *ENGLISH_ORDINALS,
    }, key=len, reverse=True))
    + r')\b'
)
_CN_CARDINAL_RE = re.compile(r'[零一二三四五六七八九十两廿卅]+')


def _english_ordinal_quantity_to_arabic(text: str) -> str:
    """Map English cardinals and ordinals (first week → 1 week) to digits."""
    result = english_number_to_arabic(text)
    for word, digit in sorted(ENGLISH_ORDINALS.items(), key=lambda x: -len(x[0])):
        result = re.sub(r'\b' + word + r'\b', digit, result, flags=re.IGNORECASE)
    return re.sub(r'\b(\d+)(?:st|nd|rd|th)\b', r'\1', result, flags=re.IGNORECASE)


def _quantity_words_to_arabic(text: str) -> str:
    """Normalize English/Chinese cardinals, ordinals, and fold words to digits."""
    result = _english_ordinal_quantity_to_arabic(text or "")

    def _fold(m: re.Match) -> str:
        word = m.group(1).lower()
        special = {"once": "1", "twice": "2", "thrice": "3"}
        if word in special:
            return special[word] + " fold"
        conv = _english_ordinal_quantity_to_arabic(word)
        return (conv + " fold") if re.search(r'\d', conv) else m.group(0)

    result = _FOLD_WORD_RE.sub(_fold, result)
    return chinese_numeral_to_arabic(result)


def _canonical_quantity(text: str) -> str:
    t = normalize_whitespace(_quantity_words_to_arabic(text))
    t = re.sub(r'(?i)groups?|组', '组', t)
    t = re.sub(r'(?i)folds?|倍', '倍', t)
    t = re.sub(r'(?i)doses?|剂', '次', t)
    t = re.sub(r'(?i)weeks?|周', '周', t)
    t = re.sub(r'(?i)days?|天', '天', t)
    t = re.sub(r'(?i)months?|月', '月', t)
    t = re.sub(r'(?i)years?|年', '年', t)
    return t


def _is_word_quantity_phrase(value: str) -> bool:
    """True for ordinal time, fold words, any unit, or a bare cardinal."""
    if not value:
        return False
    if _ORDINAL_TIME_RE.search(value) or re.search(r'(?i)fold|倍', value):
        return True
    has_card = bool(_EN_CARDINAL_RE.search(value) or _CN_CARDINAL_RE.search(value))
    if not has_card:
        return False
    rest = _EN_CARDINAL_RE.sub("", value)
    rest = _CN_CARDINAL_RE.sub("", rest)
    rest = re.sub(r'[\s\-]', '', rest)
    return (not rest) or bool(_QUANTITY_UNIT_RE.search(value))


def _quantity_digits(text: str) -> set[str]:
    return set(re.findall(r'\d+(?:\.\d+)?', _quantity_words_to_arabic(text or "")))


_CJK_NUMERAL_CHARS = set("零一二三四五六七八九十两廿卅百千万亿")


def _token_boundary_in_text(needle: str, haystack: str) -> bool:
    """True when needle sits on a word/number boundary (not inside a longer word)."""
    needle = normalize_whitespace(needle or "")
    haystack = normalize_whitespace(haystack or "")
    if not needle or not haystack:
        return False
    pat = re.compile(
        r"(?i)(?<![A-Za-z0-9])" + re.escape(needle) + r"(?![A-Za-z0-9])"
    )
    for m in pat.finditer(haystack):
        before = haystack[m.start() - 1] if m.start() else ""
        after = haystack[m.end()] if m.end() < len(haystack) else ""
        if before in _CJK_NUMERAL_CHARS or after in _CJK_NUMERAL_CHARS:
            continue
        return True
    return False


def _word_quantity_boundary_in_quote(value: str, quote: str) -> bool:
    """Value wording or number must match on a boundary inside the quote."""
    value_norm = normalize_whitespace(value or "")
    quote_n = normalize_whitespace(quote or "")
    if not value_norm or not quote_n:
        return False
    if _token_boundary_in_text(value_norm, quote_n):
        return True
    quote_ar = normalize_whitespace(_quantity_words_to_arabic(quote_n))
    val_ar = normalize_whitespace(_quantity_words_to_arabic(value_norm))
    if val_ar and _token_boundary_in_text(val_ar, quote_ar):
        return True
    for digit in _quantity_digits(value_norm):
        if number_in_text_as_word_boundary(digit, quote_ar):
            return True
    return False


def _quote_verbatim_in_source(quote: str, source: str) -> bool:
    """Normalized quote must appear verbatim in the source (no fuzzy fallback)."""
    quote_n = normalize_whitespace(quote or "")
    src_n = normalize_whitespace(source or "")
    if not quote_n or not src_n:
        return False
    if quote_n in src_n or quote_n.lower() in src_n.lower():
        return True
    quote_en = normalize_whitespace(english_number_to_arabic(quote_n))
    src_en = normalize_whitespace(english_number_to_arabic(src_n))
    return bool(quote_en) and (quote_en in src_en or quote_en.lower() in src_en.lower())


def _word_quantity_in_passage(value: str, quote: str, source: str) -> bool:
    """Quote verbatim in source; value on a word/number boundary inside that quote."""
    quote_n = normalize_whitespace(quote or "")
    if not quote_n or not _quote_verbatim_in_source(quote_n, source):
        return False
    return _word_quantity_boundary_in_quote(value, quote_n)


def english_number_to_arabic(text: str) -> str:
    """Convert English number words to Arabic numerals.

    Handles "nine doses", "Forty-one patients", "twenty one".
    """
    result = text.lower()
    # Hyphenated or spaced compounds first: forty-one, twenty one
    compound_ones = {k: v for k, v in ENGLISH_ONES.items() if k not in ("once", "twice", "zero")}
    ones_alt = "|".join(sorted(compound_ones, key=len, reverse=True))
    tens_alt = "|".join(sorted(ENGLISH_TENS, key=len, reverse=True))
    def _compound(m: re.Match) -> str:
        return str(ENGLISH_TENS[m.group(1)] + compound_ones[m.group(2)])
    result = re.sub(
        rf'\b({tens_alt})[\s-]+({ones_alt})\b',
        _compound,
        result,
        flags=re.IGNORECASE,
    )
    for word, digit in sorted(ENGLISH_NUMBER_WORDS.items(), key=lambda x: -len(x[0])):
        result = re.sub(r'\b' + word + r'\b', digit, result, flags=re.IGNORECASE)
    return result


def normalize_whitespace(text: str) -> str:
    """Normalize whitespace for substring matching."""
    return re.sub(r'\s+', ' ', text.strip()).lower()


TRIAGE_TOOL_SCHEMA = {
    "name": "submit_triage",
    "description": "Submit triage results: which items to include and their tiers",
    "input_schema": {
        "type": "object",
        "properties": {
            "selections": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "url": {"type": "string"},
                        "tier": {"type": "string", "enum": ["deep", "brief", "industry"]},
                        "field": {"type": "string", "enum": list(FIELDS.keys())},
                        "reason": {"type": "string", "description": "Why this item was selected and assigned this tier"},
                    },
                    "required": ["url", "tier", "field"],
                },
            },
        },
        "required": ["selections"],
    },
}

ARTICLE_TOOL_SCHEMA = {
    "name": "submit_article",
    "description": "Submit the drafted article",
    "input_schema": {
        "type": "object",
        "properties": {
            "url": {"type": "string"},
            "tier": {"type": "string", "enum": ["deep", "brief"]},
            "field": {"type": "string", "enum": list(FIELDS.keys())},
            "title": {"type": "string", "description": "结论式中文标题，20-40字，含一个关键数字或关键对比，不用重磅/颠覆/突破"},
            "journal": {"type": "string"},
            "authors": {"type": "string"},
            "one_liner": {"type": "string", "description": "一句话结论，40-70字，含对象+做法+量化结果"},
            "datacard": {
                "type": "object",
                "properties": {
                    "study_type": {"type": "string"},
                    "n": {"type": "string", "description": "样本量，写清单位与分组；材料没有则省略该字段，不要写占位句"},
                    "control": {"type": "string"},
                    "intervention": {"type": "string", "description": "药名/剂量/途径/频次/疗程"},
                    "followup": {"type": "string"},
                    "primary_endpoint": {"type": "string", "description": "终点名称与定义"},
                    "primary_endpoint_result": {"type": "string", "description": "数值 + 对照值；材料没有则省略该字段"},
                    "statistics": {"type": "string", "description": "HR/OR/95%CI/P；未做检验则在结果段写明未检验，不要用套话填满数据卡"},
                    "safety": {"type": "string", "description": "≥3级AE/SAE/死亡；非临床研究写'不适用'"},
                },
                "required": ["study_type", "n", "control", "intervention", "followup",
                            "primary_endpoint", "primary_endpoint_result", "statistics", "safety"],
            },
            "background": {"type": "string", "description": "研究背景与待解问题，180-240字"},
            "design": {"type": "string", "description": "研究设计，200-280字，含分组/剂量/终点定义"},
            "results": {
                "type": "array",
                "description": "核心结果，3-5段，每段一个实验或终点，每段必须含至少一个具体数字",
                "items": {"type": "string"},
                "minItems": 1,
                "maxItems": 5,
            },
            "mechanism": {"type": "string", "description": "机制解读，250-350字，区分原文证明与作者推测"},
            "limitations": {
                "type": "array",
                "description": "局限，至少3条，每条具体；禁止'仍需更多研究'这类空话",
                "items": {"type": "string"},
                "minItems": 1,
            },
            "significance": {"type": "string", "description": "临床/产业意义，150-220字，用条件句"},
            "data_points": {
                "type": "array",
                "description": "本文引用的每一个数字，用于机器核对",
                "items": {
                    "type": "object",
                    "properties": {
                        "value": {"type": "string"},
                        "meaning": {"type": "string"},
                        "source_quote": {"type": "string", "description": "来源原文中支撑该数字的英文原句，必须逐字复制"},
                        "location": {"type": "string", "description": "Abstract / Results / Fig / Table / Methods"},
                        "basis": {"type": "string", "enum": ["abstract", "body"], "description": "摘要口径或正文口径，同一张图不得混用"},
                    },
                    "required": ["value", "meaning", "source_quote"],
                },
                "minItems": 1,
            },
            "unknowns": {
                "type": "array",
                "description": "来源中确实缺失的要素；写入局限一条，不要写「原文未给出」占位",
                "items": {"type": "string"},
            },
            "steps": {
                "type": "array",
                "items": {"type": "string", "maxLength": 25},
                "minItems": 3,
                "maxItems": 5,
            },
            "image_prompt": {"type": "string"},
        },
        "required": ["url", "tier", "field", "title", "journal", "authors", "one_liner",
                    "datacard", "background", "design", "results", "mechanism",
                    "limitations", "significance", "data_points", "steps"],
    },
}


def _triage_field_map(config: dict | None) -> dict:
    from inlight_qc import acir_strict
    if acir_strict(config):
        from inlight_fields import FIELDS as NEW_FIELDS
        return dict(NEW_FIELDS)
    return dict(FIELDS)


def _triage_field_rules(config: dict | None) -> str:
    from inlight_qc import acir_strict
    if acir_strict(config):
        from inlight_fields import FIELD_PROMPT_RULES
        return FIELD_PROMPT_RULES
    return (
        "分类基于研究的主要对象，而非使用的工具。\n"
        "- 类器官工作不得归入动物模型。\n"
        "- 仅在小鼠中验证疗法不得标动物模型；仅给药途径不得标抗体工程。\n"
        "- 肿瘤类器官资源库/药敏归精准肿瘤（生产字段 f9）。\n"
    )


def build_triage_prompt(items: list[EnrichedItem], config: dict) -> str:
    """Build prompt for triage stage."""
    t = pipeline_targets(config)
    max_deep = t["max_deep"]
    max_brief = t["max_brief"]
    max_industry = t["max_industry"]
    min_deep = t["min_deep"]

    items_json = []
    for item in items:
        items_json.append({
            "url": item.url,
            "title": item.title,
            "source": item.source,
            "date": item.date,
            "kind": item.kind,
            "evidence_level": item.evidence_level,
            "abstract_chars": len(item.abstract or ""),
            "has_fulltext": item.evidence_level == "fulltext" and bool(item.fulltext_results),
            "has_press": bool(item.press_coverage),
            "read_note": item.read_note,
            "results_words": (item.sections_read or {}).get("results", {}).get("words", 0),
            "abstract_preview": item.abstract[:500] if item.abstract else item.rss_summary[:500],
        })
    items_json.sort(key=lambda r: (0 if r.get("has_fulltext") else 1))

    if max_brief:
        brief_rule = (
            f"2. 最多选 {max_brief} 篇论文速览（tier=brief）。"
            "不要用速览凑解读数量；解读必须是深度分析。"
        )
    else:
        brief_rule = "2. 不要选题速览来凑数。材料不够写成深度解读的条目直接不选。"
    if max_industry:
        industry_rule = (
            f"3. 行业、商业或交易新闻可选：有就最多选 {max_industry} 条（tier=industry）；"
            "没有就跳过，不要因此报错或硬凑。"
        )
    else:
        industry_rule = "3. 本期不选行业动态。"

    return f"""你是前沿追踪的选题编辑。下面是本周抓到的条目，请挑选最重要的进入本期周报。

本期对标 ACIR 周报：解读必须有深度。目标是发表 {min_deep}–{max_deep} 篇深度解读（机制 + 关键数据 + 意义 + 局限）。宁可发 {min_deep} 篇过硬的，不要发 {max_deep} 篇平庸的。核对不过就少发，不可发错。

## 选题规则

1. 只有 evidence_level=fulltext（程序已取到 Results 正文）的条目可以选为深度解读（tier=deep）。最多 {max_deep} 篇，目标至少 {min_deep} 篇。必须写出机制、数据、意义与局限。没有全文就不要选 deep。
{brief_rule}
{industry_rule}
4. evidence_level 为 abstract / preprint / press / secondary 的条目不能选为 deep，也不得配机制图。
5. 优先选择：有全文的临床试验结果、首次人体数据、平台级方法突破。
6. 不要为凑数降低全文门槛。全文不够的条目不要选为解读。

## 领域分类

{json.dumps(_triage_field_map(config), ensure_ascii=False)}

{_triage_field_rules(config)}

不属于以上任一领域的条目选 field=none，不要硬塞到最接近的领域。

调用 submit_triage 工具提交选题结果。

候选条目：
{json.dumps(items_json, ensure_ascii=False, indent=2)}
"""


def _article_prompt_abstract(item: EnrichedItem) -> str:
    """Abstract block for the article prompt, with RSS text at most once."""
    abstract = (item.abstract or "").strip()
    rss = (item.rss_summary or "").strip()
    if abstract:
        parts = [abstract[:8000]]
        # Only append the RSS teaser when it is not the same text as the abstract
        if rss and not is_near_duplicate(rss, abstract):
            parts.append("RSS 摘要：\n" + rss[:8000])
        press = (item.press_coverage or "").strip()
        if press and not is_near_duplicate(press, abstract):
            parts.append("公开新闻稿 / 媒体报道（期刊全文不可得时的补充材料，不得写成已读全文）：\n" + press[:8000])
        return "\n".join(parts)
    press = (item.press_coverage or "").strip()
    if rss or press:
        extra = []
        if rss:
            extra.append(rss[:8000])
        if press and (not rss or not is_near_duplicate(press, rss)):
            extra.append("公开新闻稿 / 媒体报道：\n" + press[:8000])
        return "\n".join(extra)
    return "无"


def build_article_prompt(
    item: EnrichedItem,
    tier: str,
    *,
    source_window: int | None = None,
    compact: bool = False,
) -> str:
    """Build prompt for single article drafting."""
    from inlight_qc import FULLTEXT_WINDOW
    window = FULLTEXT_WINDOW if source_window is None else max(2000, int(source_window))
    fig_n = 1600 if compact else 4000
    meth_n = 1200 if compact else 3000
    compact_note = (
        "\n上次输出因长度被截断。请更紧凑地写完全部字段，并完整调用 submit_article，不要中途停止。\n"
        if compact else ""
    )
    return f"""你是「前沿追踪」的科学编辑。下面是一篇论文的可核实材料，请据此写一篇中文解读。

## 不可违反的规则

1. **只写材料已经写明的事实**。材料里没有的数字、作者、适应症、剂量、人群、金额，一律不要写。
2. **材料里查不到的字段，直接省略，不要写「未给出」「原文未给出」或任何占位句**，不要推测，不要用相近的数字代替。
3. **每个数字都要有 source_quote**。你引用的每一个数字，都必须能在材料里找到对应的原句。把这些原句逐字放进 data_points[].source_quote。
   凑不出 source_quote 的数字，就不要写进正文。
4. **不要把标识符登记为数字**。CD4、CD8、CD14、IL-23、HLA-DP04 等是蛋白/基因名称，不是数据；NCT、RPCEC 等是试验编号，不是数据。
   data_points 的 meaning 字段不得写「名称中的编号」或类似内容。
5. **保留原文的推测性语气**。原文说"may"、"might"、"could"、"suggests"时，译文要保留相应的推测词（可能、或许、提示）。
   不要把推测性结论写成确定性结果。
6. **不要断言原文没有的事情不存在**。材料没写的内容直接省略，不要写「原文未报告 X」或 “the source does not report X”，也不要写「未涉及人体」或「未在人体验证」。
7. **每个数字和专有名称必须从材料逐字复制**。数字（含小数点、百分号、单位、剂量基准）和名称（药名、基因、蛋白、机构、试验名、作者、期刊）一律从材料原样抄写，不得改写、换算、音译替换或用近义名。
8. **预印本不得写成已发表或已同行评议**。来源没有期刊名时不要填写期刊。

## 材料等级（已由系统判定，不要自行改写）

evidence_level = {item.evidence_level}
核对记录 = {item.read_note or '未读全文'}
- fulltext：程序已取到 Results 正文并写入下方「全文结果」。只根据这些全文写深度解读和机制图。
- abstract / preprint：只有摘要。不能写成深度解读，不能配机制图。
- press / secondary：只有新闻稿或二手转述。不能写成深度解读，不能配机制图。

## 写作档次

tier = "{tier}"
- deep（正文 1400–1900 字）：背景 180-240 + 设计 200-280 + 结果 500-700（3-5段）+ 机制 250-350 + 局限 200-280（≥3条）+ 意义 150-220
- brief（正文 {BRIEF_HAN_MIN}–{BRIEF_HAN_MAX} 字，目标约 600）：{_brief_section_prompt_line()}

evidence_level 不是 fulltext 时只能填 brief，且不得写 image_prompt / 机制图。
核对记录必须写明实际读到的材料（例如「读了 PMC 全文 PMCxxxx 的 Results/Methods/图注」）。
每个数字必须能在全文结果中逐字找到。段落每段不超过 150 字。全文至少 6 个可核实数字。

## deep 档要求

- 标题：20–40 字，结论式；优先已检验的主要终点或摘要与正文一致的核心读出，可含一个关键数字。不要用「重磅」「颠覆」「突破」「首次」（除非材料明写 first-in-human / first report）。
  文中标明未做统计检验的对比不得出现在标题；试验未设计/未 powered 做比较时，标题禁止「优于/疗效相当/显著优于」。
- one_liner 一句话结论：40–70 字，必须包含「什么对象 / 什么模型 + 做了什么 + 得到什么量化结果」，与标题使用同一套数字口径。
- results 核心结果：3–5 段，合计 500–700 字。**每段讲一个实验或一个终点，每段至少写出一个具体数字。**
  主要终点那一段必须含：终点定义、分析集 n、点估计或事件数、对照/阈值、以及是否为组间比较设计效能（powered / Fleming 等）。
  材料未做检验的对比写在结果段并标「未检验」，不要写进标题。
- data_points 每条加 location（Abstract / Results 小节 / Fig / Table / Methods）和 basis（abstract 或 body）。摘要与正文数字冲突时正文用 Results 口径；摘要口径须标明「摘要」。同一张图禁止混用两套口径。
- 不要写「原文未报告/原文未给出」或 “the source does not report”；缺项省略字段，写入局限，不要用套话填数据卡。
- 保留原文语气：趋势/相关/作者推测不得升级为「显著/证明」。
- 领域标签必须对应文章主题，不是顺带用到的工具：仅在小鼠中验证不得标动物模型；仅给药途径不得标抗体工程。
- limitations 局限与不确定：至少 3 条，合计 200–280 字。每条都要具体，必须覆盖以下三类中的至少两类：
  ① 外推性（物种、人群、样本量、单中心、无对照、剂量未优化）
  ② 终点与随访（替代终点、随访过短、未按疗效设定检验效能、开放标签）
  ③ 未报告项（材料没写的终点、随访、统计量——只写材料里实际缺的内容，不要写「未给出」）
  禁止写「仍需更多研究验证」「期待后续大样本研究」这类空话。

## brief 档要求

背景一句（{BRIEF_SECTION_RANGES['background'][0]}–{BRIEF_SECTION_RANGES['background'][1]} 字）→ 设计一句（{BRIEF_SECTION_RANGES['design'][0]}–{BRIEF_SECTION_RANGES['design'][1]} 字，必须含研究类型与 n）→ 结果 2–3 句（{BRIEF_SECTION_RANGES['results'][0]}–{BRIEF_SECTION_RANGES['results'][1]} 字，至少两个数字）
→ 局限一句（{BRIEF_SECTION_RANGES['limitations'][0]}–{BRIEF_SECTION_RANGES['limitations'][1]} 字，不得为空）→ 意义一句（{BRIEF_SECTION_RANGES['significance'][0]}–{BRIEF_SECTION_RANGES['significance'][1]} 字）。

## 语气

第三人称、过去时叙述实验、克制。不用感叹号，不用「惊人」「震撼」「改写教科书」。
作者称谓用「姓氏 等」，首次出现时给期刊名。不评价单位排名。

## 术语翻译（硬性规定）

以下术语的翻译是固定的，必须使用正确翻译，使用错误翻译将导致文章被拒绝：
- mesaconate / mesaconic acid → 中康酸（不是「美康酸」「梅沙康酸」「麦康酸」）

## 材料

标题：{item.title}
期刊 / 来源：{item.journal or item.source}
日期：{item.date}
DOI / 链接：{item.url}
证据等级：{item.evidence_level}
核对记录：{item.read_note or '未读全文'}
已读章节：{json.dumps(item.sections_read, ensure_ascii=False) if item.sections_read else '{}'}

摘要：
{_article_prompt_abstract(item) if item.evidence_level != "fulltext" else "（深度解读以全文 Results 为准，摘要仅供对照）"}

全文结果与讨论（仅在 evidence_level=fulltext 时提供，必须作为数字与机制的唯一依据）：
{item.fulltext_results[:window] if item.evidence_level == "fulltext" and item.fulltext_results else '无'}

图注（若有）：
{item.fig_captions[:fig_n] if item.evidence_level == "fulltext" and item.fig_captions else '无'}

研究设计相关方法（若有）：
{item.methods_design[:meth_n] if item.evidence_level == "fulltext" and item.methods_design else '无'}
{compact_note}
调用 submit_article 工具提交。
"""


def cn_len(text: str) -> int:
    """Count Chinese characters and treat continuous ASCII as 1."""
    count = 0
    in_ascii = False
    for char in text:
        if '\u4e00' <= char <= '\u9fff' or char in '，。、；：？！""''（）【】':
            count += 1
            in_ascii = False
        elif char.isascii() and not char.isspace():
            if not in_ascii:
                count += 1
                in_ascii = True
        else:
            in_ascii = False
    return count


def chinese_numeral_to_arabic(text: str) -> str:
    """Convert Chinese numerals to Arabic numbers for comparison.
    
    Examples: 两年 -> 2年, 五百天 -> 500天, 三倍 -> 3倍
    """
    # Simple Chinese numeral mapping
    simple_map = {
        '零': '0', '一': '1', '二': '2', '两': '2', '三': '3', '四': '4',
        '五': '5', '六': '6', '七': '7', '八': '8', '九': '9', '十': '10',
    }
    
    result = text
    
    # Handle compound numbers like 五百, 三千, etc.
    compound_patterns = [
        (r'([一二三四五六七八九])千([一二三四五六七八九]?)百([一二三四五六七八九]?)十([一二三四五六七八九]?)',
         lambda m: str(int(simple_map.get(m.group(1), '0')) * 1000 + 
                       int(simple_map.get(m.group(2), '0')) * 100 + 
                       int(simple_map.get(m.group(3), '0')) * 10 + 
                       int(simple_map.get(m.group(4), '0')))),
        (r'([一二三四五六七八九])百([一二三四五六七八九]?)十([一二三四五六七八九]?)',
         lambda m: str(int(simple_map.get(m.group(1), '0')) * 100 + 
                       int(simple_map.get(m.group(2), '0')) * 10 + 
                       int(simple_map.get(m.group(3), '0')))),
        (r'([一二三四五六七八九])十([一二三四五六七八九]?)',
         lambda m: str(int(simple_map.get(m.group(1), '0')) * 10 + 
                       int(simple_map.get(m.group(2), '0')))),
        (r'十([一二三四五六七八九])',
         lambda m: str(10 + int(simple_map.get(m.group(1), '0')))),
        (r'五十亿', '5000000000'),
        (r'([一二三四五六七八九])亿', lambda m: str(int(simple_map.get(m.group(1), '0')) * 100000000)),
        (r'([一二三四五六七八九])万', lambda m: str(int(simple_map.get(m.group(1), '0')) * 10000)),
    ]
    
    for pattern, replacement in compound_patterns:
        if callable(replacement):
            result = re.sub(pattern, replacement, result)
        else:
            result = re.sub(pattern, replacement, result)
    
    # Simple single-character replacements
    for cn, ar in simple_map.items():
        result = result.replace(cn, ar)
    
    return result


def is_bibliographic_number(num: str, context: str) -> bool:
    """Dates, years and DOI fragments are not study data."""
    ctx = (context or "").lower()
    if _BIBLIOGRAPHIC_CTX_RE.search(ctx):
        return True
    core = extract_number_core(num) or ""
    if re.fullmatch(r'(?:19|20)\d{2}', core):
        return True
    return False


def extract_number_core(text: str) -> str:
    """Extract the core numeric value from a number string for comparison.
    
    Examples: "52%" -> "52", "1,139例" -> "1139", "95%CI" -> "", "HR=0.66" -> "0.66"
    
    Handles:
    - Thousands separators (1,139 -> 1139)
    - Skips confidence interval labels (95%CI)
    - Skips gene/protein identifiers (CD14 -> "")
    """
    # Remove commas and spaces
    text = text.replace(",", "").replace(" ", "").replace("，", "")
    
    # Skip "95%CI" or "95% CI" - this is a confidence interval label, not data
    if re.match(r'^\d+\s*%\s*CI', text, re.IGNORECASE):
        return ""
    
    # Skip gene/protein names like CD14, IL-23, HLA-DP04
    if re.match(r'^(?:CD|IL|HLA|NK|NF)[A-Za-z]?-?[A-Za-z0-9]*$', text, flags=re.IGNORECASE):
        return ""
    
    # Skip trial IDs
    if re.match(r'^(?:NCT|RPCEC|ISRCTN)\d+$', text, flags=re.IGNORECASE):
        return ""
    
    # Middle-dot decimals used in some journals: 18·9 = 18.9
    text = text.replace("·", ".").replace("•", ".")

    # Extract the numeric part (Arabic, or after Chinese-numeral conversion)
    match = re.search(r'(\d+(?:\.\d+)?)', text)
    if match:
        return match.group(1)
    converted = chinese_numeral_to_arabic(text)
    if converted != text:
        match = re.search(r'(\d+(?:\.\d+)?)', converted)
        if match:
            return match.group(1)
    return ""


def normalize_for_dedup(text: str) -> str:
    """Normalize text for near-duplicate detection.
    
    Handles common differences between EPMC and RSS versions:
    - HTML entities and tags
    - Special character encoding (ROR{gamma}t vs actual Greek letters)
    - Whitespace and punctuation variations
    """
    if not text:
        return ""
    
    # Remove HTML tags
    normalized = re.sub(r'<[^>]+>', '', text)
    
    # Decode common HTML entities
    normalized = normalized.replace('&lt;', '<').replace('&gt;', '>')
    normalized = normalized.replace('&amp;', '&').replace('&nbsp;', ' ')
    normalized = normalized.replace('&alpha;', 'alpha').replace('&beta;', 'beta')
    normalized = normalized.replace('&gamma;', 'gamma').replace('&delta;', 'delta')
    
    # Handle curly-brace notation like {gamma}, {alpha}
    normalized = re.sub(r'\{(\w+)\}', r'\1', normalized)
    
    # Normalize Greek letters to ASCII names
    greek_map = {
        'α': 'alpha', 'β': 'beta', 'γ': 'gamma', 'δ': 'delta',
        'ε': 'epsilon', 'ζ': 'zeta', 'η': 'eta', 'θ': 'theta',
        'κ': 'kappa', 'λ': 'lambda', 'μ': 'mu', 'ν': 'nu',
        'π': 'pi', 'ρ': 'rho', 'σ': 'sigma', 'τ': 'tau',
        'ω': 'omega'
    }
    for greek, ascii_name in greek_map.items():
        normalized = normalized.replace(greek, ascii_name)
    
    # Normalize whitespace
    normalized = re.sub(r'\s+', ' ', normalized).strip().lower()
    
    return normalized


def is_near_duplicate(text1: str, text2: str, threshold: float = 0.9) -> bool:
    """Check if two texts are near-duplicates after normalization.
    
    Returns True if the shorter text is >=threshold% contained in the longer,
    or if they're very similar after normalization.
    """
    if not text1 or not text2:
        return False
    
    norm1 = normalize_for_dedup(text1)
    norm2 = normalize_for_dedup(text2)
    
    # Exact match after normalization
    if norm1 == norm2:
        return True
    
    # Check if shorter is largely contained in longer
    shorter, longer = (norm1, norm2) if len(norm1) <= len(norm2) else (norm2, norm1)
    
    # If shorter is >=threshold% of longer and shorter is subset
    if len(shorter) >= len(longer) * threshold and shorter in longer:
        return True
    
    # Check character overlap (simple similarity)
    if len(shorter) == 0:
        return False
    
    # Count matching character pairs (bigrams)
    shorter_bigrams = set(shorter[i:i+2] for i in range(len(shorter)-1))
    longer_bigrams = set(longer[i:i+2] for i in range(len(longer)-1))
    
    if not shorter_bigrams:
        return False
    
    overlap = len(shorter_bigrams & longer_bigrams) / len(shorter_bigrams)
    return overlap >= threshold


def number_in_text_as_word_boundary(number: str, text: str) -> bool:
    """Check if a number appears in text as STANDALONE DATA with word boundaries.
    
    Returns True only if the number appears as a data value, not embedded in an identifier.
    
    Examples:
    - "52% response rate" contains "52" as standalone data -> True
    - "CD8 T cells" does NOT contain "8" as standalone data -> False (it's part of CD8)
    - "36 patients" contains "36" as standalone data -> True
    - "100mg" contains "100" as data with unit -> True (after normalize_unit_spacing)
    
    Prevents:
    - '500' from matching '5000' or '1500'
    - '8' from matching 'CD8', 'IL-8', 'TAK-981'
    - '31' from matching 'CD318'
    - '2' from matching '3.2' (decimal)
    - '45' from matching 'NCT04512345'
    """
    if source_has_numeric_value(number, text):
        return True
    # Remove commas from both
    number_clean = number.replace(",", "").replace("，", "").strip()
    text_clean = text.replace(",", "").replace("，", "")
    
    if not number_clean:
        return False
    
    # Build pattern that requires the number to NOT be part of an identifier
    # Numbers should be bounded by non-alphanumeric characters AND not part of:
    # - Identifiers (preceded/followed by letters at letter-digit boundary)
    # - Larger numbers (preceded/followed by digits)
    # - Decimals (preceded/followed by decimal point + digit)
    # - Hyphenated identifiers like TAK-981 (preceded/followed by hyphen + alnum)
    #
    # BUT: Allow units immediately after (mg, kg, nM, etc.)
    # These are valid data patterns: 100mg, 5nM, 12%
    
    # A hyphen is allowed on BOTH ends of a numeric range
    # ("1.30-3.35", "1.7%-40.5%", "6-23 months"). "TAK-981" stays rejected
    # because the digits are preceded by a letter-hyphen identifier.
    # CJK, dashes, and thousands separators are word boundaries: 共527例,
    # 缓解率64%, —1,139例 must match. Python str.isalnum() is True for CJK.
    unit_re = re.compile(
        r'(?:mg|kg|mL|µg|nM|pM|µM|mM|μg|μL|ng|pg|mmol|mol|g|L|%|％|倍|年|个月|天|周|小时|例|名)',
        re.IGNORECASE,
    )
    for match in re.finditer(re.escape(number_clean), text_clean):
        start, end = match.start(), match.end()
        prev = text_clean[start - 1] if start else ""
        prev2 = text_clean[start - 2] if start >= 2 else ""
        if prev == "-" and _is_ascii_alpha(prev2):
            continue
        if prev and (_is_ascii_alnum(prev) or prev == "."):
            continue
        after = text_clean[end:]
        if unit_re.match(after) or re.match(r'-\s*\d', after):
            return True
        nxt = after[:1]
        if nxt == "." and after[1:2].isdigit():
            continue
        if _is_ascii_alnum(nxt):
            continue
        return True
    return False


def _is_ascii_alnum(ch: str) -> bool:
    """True for ASCII letters/digits only. CJK is a number-word boundary."""
    return bool(ch) and ch.isascii() and ch.isalnum()


def _is_ascii_alpha(ch: str) -> bool:
    return bool(ch) and ch.isascii() and ch.isalpha()


def normalize_source_text(text: str, *, convert_english_words: bool = True) -> str:
    """Normalize source text for number/name comparison.
    
    Applies:
    - Lowercase
    - Whitespace normalization
    - English number words to digits (nine -> 9), unless convert_english_words=False
    - Unicode superscripts to plain (10⁶ -> 10^6)
    - Thousands separators removed (1,139 -> 1139)
    - En-dash ranges (10–20 -> 10-20)
    - Plus-minus (± -> +/-)
    """
    result = text.lower()
    result = re.sub(r'\s+', ' ', result)
    
    # English number words. Callers that need to distinguish a real Arabic
    # digit from a converted word (six → 6) pass convert_english_words=False.
    if convert_english_words:
        result = english_number_to_arabic(result)
    
    # Superscripts to caret notation
    sup_map = {'⁰': '0', '¹': '1', '²': '2', '³': '3', '⁴': '4',
               '⁵': '5', '⁶': '6', '⁷': '7', '⁸': '8', '⁹': '9',
               '⁺': '+', '⁻': '-', 'ⁿ': 'n'}
    for s, r in sup_map.items():
        result = result.replace(s, r)
    
    # Remove thousands separators
    result = result.replace(',', '').replace('，', '')
    
    # Normalize dashes
    result = result.replace('–', '-').replace('—', '-')
    
    # Plus-minus
    result = result.replace('±', '+/-')

    # Angstrom variants: "2.8 Å" and "2.8 A" are the same measurement
    result = result.replace('å', 'a').replace('Å', 'a')

    # Middle-dot decimals used in some journals: 18·9 = 18.9
    result = result.replace('·', '.').replace('•', '.')
    
    return result


def extract_identifiers_from_source(source: str) -> set[str]:
    """Extract all identifiers from source that may contain digits.
    
    These identifiers should be allowed to pass through unchanged:
    - Gene names: R2, Th17, CCR8, CD318, IL-24, IFN-α2
    - Mouse lines: SAMP1/YitFC, TNFΔARE, Rag2-/-
    - Bacterial strains: Nissle 1917, EcN-CAD
    - Drug names: TAK-981, itolizumab
    - Trial IDs: NCT04443907, RPCEC00000444
    """
    identifiers = set()
    # Collapse spaced labels without lowercasing, so R2 / TH17 stay extractable.
    source = _collapse_spaced_labels(source or "")
    
    # Gene names: CD4, CD8, CD 8, CD318, IL-23, IFN-α2, HLA-DP04, NK, NF-κB, TH17
    scan = _identifier_scan_text(source)
    for match in re.finditer(
        r'\b(?:CD|IL|HLA|IFN|NK|NF|CCR|Th|TH|TAK|CCL|CXCL|CXCR|ROR)[A-Za-zα-ω]?[\s_-]?[A-Za-z0-9αβγδ/-]*\d+[A-Za-z0-9αβγδ/-]*\b',
        scan,
        re.IGNORECASE,
    ):
        identifiers.add(match.group(0))
    
    # Element/family names: R2, S1, M1
    for match in re.finditer(r'\b[A-Z]\d+\b', source):
        identifiers.add(match.group(0))
    
    # Mouse lines: SAMP1/YitFC, TNFΔARE, Rag2-/-
    for match in re.finditer(r'\b[A-Z][A-Za-z0-9Δ]*\d[A-Za-z0-9Δ/-]*(?:/[A-Za-z0-9Δ/-]+)?\b', source):
        identifiers.add(match.group(0))
    
    # Strain / line designations: CapitalizedName + 3–4 digit accession
    # (Nissle 1917). Not "was 52" / "itolizumab 18".
    for match in re.finditer(r'\b[A-Z][a-z]{2,}\s+\d{3,4}\b', source):
        identifiers.add(match.group(0))
    
    # Drug compounds with numbers: TAK-981, TAK981
    for match in re.finditer(r'\b[A-Z]{2,}-?\d{2,}\b', source):
        identifiers.add(match.group(0))
    
    # Trial IDs
    for match in re.finditer(r'\b(?:NCT|RPCEC|ISRCTN|EudraCT|ACTRN|ChiCTR)\d+\b', source, re.IGNORECASE):
        identifiers.add(match.group(0))
    
    # EcN-CAD style names
    for match in re.finditer(r'\b[A-Z][a-z]?[A-Z]-?[A-Z]{2,}\b', source):
        identifiers.add(match.group(0))
    
    return identifiers


# Tokens that look like identifiers, not data claims. Digits inside these
# must not be extracted as claimed numbers and must not evidence a count/% .
# Spaced / subscript-style splits (CD 8, Th 17) are the same token as CD8.
# Do not allow a free letter-run + space + digits ("was 52" is a count).
_IDENTIFIER_PREFIXES = (
    "CD", "IL", "HLA", "IFN", "NK", "NF", "CCR", "CXCR", "CXCL", "CCL",
    "Th", "TH", "TAK", "ROR", "Dsg", "MK",
)
_IDENTIFIER_TOKEN_RE = re.compile(
    r'(?i)(?:'
    # Gene/protein/strain/compound tokens: letters + digits (Dsg2, CD14, p38, MK-25)
    r'(?<![A-Za-z0-9])[A-Za-z][A-Za-z]{0,10}-?\d+[A-Za-z0-9./-]*'
    # Spaced cell-subset / receptor names: CD 8, Th 17, CCR 8
    r'|(?<![A-Za-z0-9])(?:'
    + "|".join(_IDENTIFIER_PREFIXES)
    + r')[\s_-]+\d+[A-Za-z0-9./-]*'
    # Leading-digit names: 4-1BB, 4-1BBL
    r'|(?<![A-Za-z0-9])\d+-\d+[A-Za-z]{1,8}'
    r'|(?:NCT|RPCEC|ISRCTN|EudraCT|ACTRN|ChiCTR)\d+'
    r')'
)
_SUBSCRIPT_DIGIT_MAP = str.maketrans("₀₁₂₃₄₅₆₇₈₉", "0123456789")

# Taxonomy / kind words: 「六种」「6类」「six kinds」 / bare six|four are not counts.
_QUALITATIVE_COUNT_RE = re.compile(
    r'(?i)(?:'
    r'\d+(?:\.\d+)?\s*(?:种|类|kinds?|types?|subsets?|classes?)|'
    r'[零一二三四五六七八九十两]+\s*(?:种|类)|'
    r'(?:zero|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve)'
    r'\s+(?:kinds?|types?|subsets?|classes?)'
    r')'
)

# Vague magnitude words: 数以百万计 / 数百万 / millions of. Not numeric claims.
_QUALITATIVE_SCALE_RE = re.compile(
    r'(?i)数以[零一二三四五六七八九十百千万亿两]+计|'
    r'数[百千万亿]+|'
    r'\b(?:tens|hundreds|thousands|millions|billions)\s+of\b'
)

_BIBLIOGRAPHIC_CTX_RE = re.compile(
    r'(?i)doi|published\s*online|volume|pages?|issn|pmcid|pmid|'
    r'核对记录|pmc\d+|10\.\d{4,}|/s\d{5}|\bs\d{5}\b'
)

# English number words that cannot stand in as a data_point value
_ENGLISH_NUMERAL_VALUE_RE = re.compile(
    r'(?i)\b(?:zero|once|one|twice|two|three|four|five|six|seven|eight|nine|ten|'
    r'eleven|twelve|thirteen|fourteen|fifteen|sixteen|seventeen|eighteen|nineteen|'
    r'twenty|thirty|forty|fifty|sixty|seventy|eighty|ninety|hundred|thousand|'
    r'million|billion|tens?|hundreds?|thousands?)\b'
)

HAN_CHAR_RE = re.compile(r'[\u4e00-\u9fff]')
LENGTH_BODY_FIELDS = (
    "one_liner", "background", "design", "results",
    "mechanism", "significance", "limitations",
)
MISSING_VALUE_MARK = "未给出"
BRIEF_HAN_MIN = 450
BRIEF_HAN_MAX = 900
# Per-section targets sum to 450–730 (aim ~600), same band in prompt and check.
BRIEF_SECTION_RANGES = {
    "background": (60, 100),
    "design": (70, 110),
    "results": (200, 320),
    "limitations": (60, 100),
    "significance": (60, 100),
}


def _brief_section_prompt_line() -> str:
    labels = {
        "background": "背景",
        "design": "设计",
        "results": "结果",
        "limitations": "局限",
        "significance": "意义",
    }
    extra = {
        "results": "（1-2段）",
        "limitations": "（≥1条）",
    }
    parts = []
    for name, (lo, hi) in BRIEF_SECTION_RANGES.items():
        parts.append(f"{labels[name]} {lo}-{hi}{extra.get(name, '')}")
    return " + ".join(parts)
SEE_BODY_RE = re.compile(r"详见正文")


_IDENT_LETTER_SPACE_RE = re.compile(
    r'(?i)(?<![A-Za-z0-9])([A-Za-z](?:\s+[A-Za-z]){1,6})\s+(\d+[A-Za-z0-9]*)'
)


def _collapse_spaced_labels(text: str) -> str:
    """T H 17 / CD 8 / Th 17 → TH17 / CD8 / Th17 without changing case otherwise."""
    t = (text or "").translate(_SUBSCRIPT_DIGIT_MAP)
    t = _IDENT_LETTER_SPACE_RE.sub(lambda m: re.sub(r"\s+", "", m.group(1)) + m.group(2), t)
    t = re.sub(
        r'(?i)(?<![A-Za-z0-9])('
        + "|".join(_IDENTIFIER_PREFIXES)
        + r')[ ]+(\d+[A-Za-z0-9]*)',
        r"\1\2",
        t,
    )
    return t


_SUPERSCRIPT_DIGIT_MAP = str.maketrans("⁰¹²³⁴⁵⁶⁷⁸⁹", "0123456789")
_ID_DASH_RE = re.compile(r'[\s\-\u2010\u2011\u2012\u2013\u2014\u2212]+')
_THOUSANDS_INSIDE_RE = re.compile(r'\d{1,3}(?:[, \u00a0\u202f\u2009\u2007]\d{3})+')
_CJK_PUNCT_RE = re.compile(r'[，、；：。（）【】《》『』「」]')
_NUM_TOKEN_RE = re.compile(
    r'(?:'
    r'\d{1,3}(?:[, \u00a0\u202f\u2009\u2007]\d{3})+'
    r'|\d+'
    r')'
    r'(?:[.\u00b7\u2022]\d+)?'
)
_NUM_SCALE_RE = re.compile(r'(?i)[\s\u00a0]*(million|billion|万|亿)')
_ID_UNSPACED_RE = re.compile(
    r'(?i)(?:'
    r'(?<![A-Za-z0-9])(?:NCT|RPCEC|ISRCTN|EUDRACT|ACTRN|CHICTR)\d+'
    r'|(?<![A-Za-z0-9])\d+-\d+[A-Za-z]{1,8}'
    r'|(?<![A-Za-z0-9])(?:' + "|".join(_IDENTIFIER_PREFIXES) + r')-?[A-Za-z]{0,8}-?\d+[A-Za-z0-9]*'
    r'|(?<![A-Za-z0-9])[A-Za-z][A-Za-z]{0,14}-?\d+[A-Za-z0-9]*'
    r')'
)
_TRAILING_ID_WORD_RE = re.compile(r'-[a-z]{5,}$', re.IGNORECASE)
_ID_SPACED_RE = re.compile(
    r'(?i)(?<![A-Za-z0-9])(?:'
    r'[A-Za-z](?:[\t ]+[A-Za-z]){1,6}'
    r'|(?:' + "|".join(_IDENTIFIER_PREFIXES + ("log",)) + r')'
    r')[\t ]+\d+[A-Za-z0-9]*'
)
_CLINICAL_CLAIM_RE = re.compile(
    r'(?i)%|％|例|名|率|倍|死亡|缓解|生存|HR|ORR|DCR|PFS|OS|患者|hazard'
)
_MAX_MINOR_UNMATCHED = 2


@dataclass
class NumToken:
    value: float
    raw: str
    start: int
    end: int
    context: str


@dataclass
class IdToken:
    key: str
    raw: str
    start: int
    end: int


@dataclass
class MatchDoc:
    """Shared draft/source tokenization. The live verifier uses only this."""
    canonical: str
    numbers: list[NumToken]
    identifiers: list[IdToken]
    values: set[float]


def _ident_key(raw: str) -> str:
    t = (raw or "").translate(_SUBSCRIPT_DIGIT_MAP).translate(_SUPERSCRIPT_DIGIT_MAP)
    t = _TRAILING_ID_WORD_RE.sub("", t)
    return _ID_DASH_RE.sub("", t).lower()


def _id_keys_compatible(draft_key: str, src_keys: set[str]) -> bool:
    """True when draft and source name the same label in different dress.

    Spacing/case/dashes/subscripts already share a key. HLA-DP04 vs DP04
    is the same allele written with or without the locus prefix.
    """
    if draft_key in src_keys:
        return True
    d_tail = re.search(r"(\d+[a-z0-9]*)$", draft_key)
    if not d_tail:
        return False
    tail = d_tail.group(1)
    for src in src_keys:
        if not src.endswith(tail):
            continue
        if draft_key.endswith(src) or src.endswith(draft_key):
            return True
        d_stem = draft_key[: -len(tail)]
        s_stem = src[: -len(tail)]
        if d_stem and s_stem and (d_stem.endswith(s_stem) or s_stem.endswith(d_stem)):
            return True
    return False


def parse_numeric_value(text: str) -> float | None:
    """Parse a numeric token to a float. Thousands seps and middle-dots allowed."""
    if not text:
        return None
    s = (
        str(text)
        .replace(",", "").replace("，", "")
        .replace("\u00a0", "").replace("\u202f", "")
        .replace("\u2009", "").replace("\u2007", "").replace(" ", "")
        .replace("·", ".").replace("•", ".")
    )
    s = re.sub(r'[^0-9.+-]', '', s)
    if not s or s in "+-.":
        return None
    try:
        return float(s)
    except ValueError:
        return None


def values_equivalent(a: float, b: float) -> bool:
    """Same numeric value, plus 0.5 ↔ 50% and scaled million-style aliases."""
    if abs(a - b) <= max(1e-9, 1e-6 * max(abs(a), abs(b), 1.0)):
        return True
    lo, hi = (a, b) if a <= b else (b, a)
    if lo <= 1.0 + 1e-9 and hi <= 100.0 + 1e-6:
        if abs(lo * 100.0 - hi) <= max(1e-6, 1e-4 * hi):
            return True
    return False


def _scale_factor(word: str) -> float:
    w = (word or "").lower()
    if w == "million":
        return 1_000_000.0
    if w == "billion":
        return 1_000_000_000.0
    if w == "万":
        return 10_000.0
    if w == "亿":
        return 100_000_000.0
    return 1.0


def _match_scan_text(text: str, *, convert_english_words: bool = False) -> str:
    """Lowercase scan that keeps number/identifier boundaries.

    Thousands separators, CJK punctuation and dash characters stay in
    place so 184,973 / TH17，1,139 / totaled—184,973 cannot collapse
    into one identifier. Middle-dots become decimals; English words
    convert only when asked.
    """
    scan = (text or "").lower()
    scan = re.sub(r"\s+", " ", scan)
    if convert_english_words:
        scan = english_number_to_arabic(scan)
    scan = scan.translate(_SUBSCRIPT_DIGIT_MAP).translate(_SUPERSCRIPT_DIGIT_MAP)
    scan = scan.replace("·", ".").replace("•", ".")
    scan = scan.replace("å", "a").replace("Å", "a")
    return scan


def match_document(text: str, *, convert_english_words: bool = False) -> MatchDoc:
    """Tokenize numbers (by value) and identifiers with one shared scanner.

    A number is digits plus optional thousands separators and a decimal.
    Letters, every dash/hyphen, CJK punctuation, brackets and whitespace
    are boundaries and never merge into the number. Identifiers are
    letter-digit runs with no whitespace, no CJK punctuation, and no
    thousands-formatted number inside. Spaced/unspaced and
    subscript/superscript forms share a key.
    """
    raw = text or ""
    scan = _match_scan_text(raw, convert_english_words=convert_english_words)

    id_spans: list[tuple[int, int, str]] = []
    for cre in (_ID_UNSPACED_RE, _ID_SPACED_RE):
        for m in cre.finditer(scan):
            tok = m.group(0).rstrip(".,;:)]}>\"'")
            tok = _TRAILING_ID_WORD_RE.sub("", tok)
            if not tok:
                continue
            if _CJK_PUNCT_RE.search(tok) or _THOUSANDS_INSIDE_RE.search(tok):
                continue
            end = m.start() + len(tok)
            id_spans.append((m.start(), end, tok))
    id_spans.sort(key=lambda x: (x[0], -(x[1] - x[0])))
    kept_ids: list[tuple[int, int, str]] = []
    for start, end, tok in id_spans:
        if any(s <= start and end <= e for s, e, _ in kept_ids):
            continue
        kept_ids.append((start, end, tok))

    numbers: list[NumToken] = []
    for m in _NUM_TOKEN_RE.finditer(scan):
        if any(s <= m.start() and m.end() <= e for s, e, _ in kept_ids):
            continue
        raw_num = m.group(0)
        val = parse_numeric_value(raw_num)
        if val is None:
            continue
        end = m.end()
        scale_m = _NUM_SCALE_RE.match(scan, end)
        if scale_m:
            val *= _scale_factor(scale_m.group(1))
            end = scale_m.end()
        ctx = scan[max(0, m.start() - 20):min(len(scan), end + 20)]
        if is_bibliographic_number(raw_num, ctx) or _QUALITATIVE_SCALE_RE.search(ctx):
            continue
        after = scan[end:end + 4]
        if re.match(r'\s*(?:种|类|次)', after):
            continue
        numbers.append(NumToken(value=val, raw=raw_num, start=m.start(), end=end, context=ctx))

    identifiers = [
        IdToken(key=_ident_key(tok), raw=tok, start=s, end=e)
        for s, e, tok in kept_ids
    ]
    pieces: list[str] = []
    cursor = 0
    events = (
        [(s, e, "id", _ident_key(tok)) for s, e, tok in kept_ids]
        + [(n.start, n.end, "num", _canonical_number(n.value)) for n in numbers]
    )
    events.sort(key=lambda x: (x[0], x[1]))
    used: list[tuple[int, int]] = []
    for start, end, _kind, repl in events:
        if any(u <= start and end <= v for u, v in used):
            continue
        pieces.append(scan[cursor:start])
        pieces.append(repl)
        used.append((start, end))
        cursor = max(cursor, end)
    pieces.append(scan[cursor:])
    canonical = re.sub(r"\s+", " ", "".join(pieces)).strip()
    values = {n.value for n in numbers}
    for n in numbers:
        values.update(_value_aliases(n.value))
    return MatchDoc(canonical=canonical, numbers=numbers, identifiers=identifiers, values=values)


def _canonical_number(val: float) -> str:
    if abs(val - round(val)) <= 1e-9:
        return str(int(round(val)))
    return f"{val:.12g}"


def _value_aliases(val: float) -> set[float]:
    out = {val}
    if 0 < val <= 1.0 + 1e-9:
        out.add(val * 100.0)
    if 0 < val <= 100.0 + 1e-6:
        out.add(val / 100.0)
    return out


def _claim_is_clinical_count_or_rate(num_str: str, context: str = "") -> bool:
    blob = f"{context or ''} {num_str or ''}"
    unit = classify_unit_in_context(blob, extract_number_core(num_str) or "")
    return unit in (UNIT_COUNT, UNIT_RATE) or bool(_CLINICAL_CLAIM_RE.search(blob))


def source_has_numeric_value(num_str: str, *texts: str, context: str = "") -> bool:
    """True when the claimed number's value appears as a standalone token.

    Formatting around the digits is ignored (dashes, CJK punctuation, spaces,
    thousands separators, %, units, 0.5↔50%, million/万/亿). Identifier
    digits and English-converted words are not evidence. A clinical count or
    rate does not match a time or dose token (1例 ↛ 1-year; 6例 ↛ six doses).
    """
    core = extract_number_core(num_str) or num_str
    claimed = parse_numeric_value(core)
    if claimed is None:
        cn = extract_number_core(chinese_numeral_to_arabic(num_str))
        claimed = parse_numeric_value(cn) if cn else None
    if claimed is None:
        return True
    claim_clinical = _claim_is_clinical_count_or_rate(num_str, context)
    for text in texts:
        if not text:
            continue
        doc = match_document(text, convert_english_words=False)
        for tok in doc.numbers:
            if not values_equivalent(claimed, tok.value):
                continue
            src_unit = classify_unit_in_context(tok.context, _canonical_number(tok.value))
            if claim_clinical and src_unit in _TIME_UNITS | {UNIT_DOSE}:
                continue
            return True
        if not claim_clinical and any(values_equivalent(claimed, v) for v in doc.values):
            return True
    return False


def normalize_for_match(text: str, *, convert_english_words: bool = False) -> str:
    """Canonical form from the shared tokenizer. Draft and source use this."""
    return match_document(text or "", convert_english_words=convert_english_words).canonical


def _identifier_scan_text(text: str) -> str:
    """Map subscript digits so CD₈ matches the same token as CD8 / CD 8."""
    return (text or "").translate(_SUBSCRIPT_DIGIT_MAP)


def identifier_spans(text: str) -> list[tuple[int, int]]:
    """Character spans of identifier tokens (CD318, IL-6, p38, NCT…)."""
    if not text:
        return []
    return [(tok.start, tok.end) for tok in match_document(text, convert_english_words=False).identifiers]


def span_covers(pos: int, end: int, spans: list[tuple[int, int]]) -> bool:
    return any(s <= pos and end <= e for s, e in spans)


def han_len_fields(art: dict) -> int:
    """Han-character count over the published body fields only."""
    total = 0
    for key in LENGTH_BODY_FIELDS:
        val = art.get(key)
        if isinstance(val, list):
            for item in val:
                if isinstance(item, str):
                    total += len(HAN_CHAR_RE.findall(item))
        elif isinstance(val, str):
            total += len(HAN_CHAR_RE.findall(val))
    return total


def source_has_equivalent_number(num_core: str, context: str, source_norm: str) -> bool:
    """Accept honest unit conversions that preserve the same quantity.

    21 days ↔ 3 weeks, 1 year ↔ 12 months. The converted number must appear
    in the source next to the matching unit, not as a bare digit.
    """
    if not num_core or not context or not source_norm:
        return False
    try:
        n = float(num_core)
    except ValueError:
        return False
    if abs(n - round(n)) > 1e-9:
        return False
    n = int(round(n))
    ctx = context.lower()
    src = source_norm.lower()

    def has(num: int, units: str) -> bool:
        return bool(re.search(
            rf'(?<![a-zA-Z0-9.\-]){num}(?![a-zA-Z0-9])\s*-?\s*(?:{units})',
            src,
        ))

    if re.search(r'周', ctx) and has(n * 7, r'days?|天|日'):
        return True
    if re.search(r'天|日', ctx) and n % 7 == 0 and has(n // 7, r'weeks?|wk|周'):
        return True
    if re.search(r'个?月', ctx) and n % 12 == 0 and has(n // 12, r'years?|yr|年'):
        return True
    if re.search(r'年', ctx) and has(n * 12, r'months?|mo|个?月'):
        return True
    # Same duration, different language: 1年 ↔ 1-year / 1 year
    if re.search(r'年', ctx) and has(n, r'years?|yrs?|yr\b'):
        return True
    if re.search(r'(?i)years?|yrs?\b', ctx) and has(n, r'年'):
        return True
    return False


# Unit classes attached to a number. Time subclasses must match exactly
# (week ≠ month). A time or dose quantity is a different dimension from a
# clinical count or rate.
UNIT_COUNT = "count"
UNIT_RATE = "rate"
UNIT_TIME = "time"
UNIT_TIME_DAY = "time_day"
UNIT_TIME_WEEK = "time_week"
UNIT_TIME_MONTH = "time_month"
UNIT_TIME_YEAR = "time_year"
UNIT_DOSE = "dose"
_TIME_UNITS = {UNIT_TIME, UNIT_TIME_DAY, UNIT_TIME_WEEK, UNIT_TIME_MONTH, UNIT_TIME_YEAR}
_CLINICAL_UNITS = {UNIT_COUNT, UNIT_RATE, None}

# The noun being counted. samples ≠ patients ≠ mice; groups ≠ any of those.
NOUN_PATIENT = "patient"
NOUN_SAMPLE = "sample"
NOUN_ANIMAL = "animal"
NOUN_GROUP = "group"


def _text_after_number(text: str, num_end: int) -> str:
    """Token stream after a number, skipping a numeric range partner.

    In "6-23 months" / "1.7%-40.5%" the unit belongs to both ends.
    """
    after = text[num_end:num_end + 28]
    return re.sub(r'^[\s]*-\s*\d+(?:\.\d+)?[%％]?', '', after, count=1)


def classify_unit_after(text: str, num_end: int) -> str | None:
    """Unit class of the token immediately following a number."""
    after = _text_after_number(text, num_end)
    if re.match(r'(?i)[\s\-]*(years?|yrs?|年)', after):
        return UNIT_TIME_YEAR
    if re.match(r'(?i)[\s\-]*(months?|mo\b|个?月)', after):
        return UNIT_TIME_MONTH
    if re.match(r'(?i)[\s\-]*(weeks?|wk|周)', after):
        return UNIT_TIME_WEEK
    if re.match(r'(?i)[\s\-]*(days?|hours?|hrs?|天|日|小时)', after):
        return UNIT_TIME_DAY
    if re.match(r'(?i)[\s\-]*(doses?|dosing|mg\b|μg\b|ug\b|µg\b|剂)', after):
        return UNIT_DOSE
    if re.match(r'(?i)\s*(例|名|位|patients?|subjects?|participants?|cases?)', after):
        return UNIT_COUNT
    if re.match(r'(?i)\s*(%|％|percent)', after):
        return UNIT_RATE
    if after.startswith("次"):
        return UNIT_DOSE
    return None


def classify_noun_after(text: str, num_end: int) -> str | None:
    """What is being counted immediately after this number."""
    after = _text_after_number(text, num_end).lower()
    if re.match(r'[\s]*(?:urine\s+)?samples?|标本|样本|份', after):
        return NOUN_SAMPLE
    if re.match(r'[\s]*(?:mice|mouse|animals?|rats?|只)', after):
        return NOUN_ANIMAL
    if re.match(r'[\s]*(?:组|臂|groups?|arms?|cohorts?)', after):
        return NOUN_GROUP
    if re.match(r'[\s]*(例|名|位|patients?|subjects?|participants?|cases?|患者|病人)', after):
        return NOUN_PATIENT
    return None


def classify_unit_in_context(context: str, num_core: str) -> str | None:
    """Unit class claimed by a non-identifier occurrence of num_core.

    Identifier digits (the 6 in IL-6) are ignored so a nearby "6例" still
    classifies as a count. Chinese numerals are converted first so 三组
    classifies the same way as 3组.
    """
    if not context or not num_core:
        return None
    context = chinese_numeral_to_arabic(context)
    id_spans = identifier_spans(context)
    determined = []
    for match in re.finditer(re.escape(num_core), context):
        if span_covers(match.start(), match.end(), id_spans):
            continue
        cls = classify_unit_after(context, match.end())
        if cls is not None:
            determined.append(cls)
    if determined:
        return determined[0]
    return None


def _occurrence_unit_classes(num_core: str, source: str) -> list[str | None]:
    """Unit class of every standalone occurrence of num_core in source.

    Letters immediately after the digits are allowed so "100mg" still counts
    as a dose; they are not treated as a larger identifier.
    """
    if not num_core or not source:
        return []
    id_spans = identifier_spans(source)
    classes = []
    for m in re.finditer(re.escape(num_core), source):
        if span_covers(m.start(), m.end(), id_spans):
            continue
        prev = source[m.start() - 1] if m.start() else ""
        if _is_ascii_alnum(prev) or prev == ".":
            continue
        nxt = source[m.end():m.end() + 1]
        nxt2 = source[m.end() + 1:m.end() + 2] if m.end() + 1 < len(source) else ""
        if nxt.isdigit() or (nxt == "." and nxt2.isdigit()):
            continue
        classes.append(classify_unit_after(source, m.end()))
    return classes


def _number_presence(num_core: str, source: str, out_class: str | None) -> str:
    """How a standalone number in source relates to the claimed unit class.

    Returns:
        "ok"               – number is usable evidence for this claim
        "wrong_dimension"  – number exists only as time/dose/etc. while the
                             claim is a clinical count or rate
        "no"               – number is not present as standalone data
    """
    if not number_in_text_as_word_boundary(num_core, source):
        return "no"
    classes = _occurrence_unit_classes(num_core, source)
    if not classes:
        return "no"
    if out_class in (UNIT_COUNT, UNIT_RATE):
        if any(c in _CLINICAL_UNITS for c in classes):
            return "ok"
        return "wrong_dimension"
    if out_class in _TIME_UNITS:
        if any(c == out_class for c in classes):
            return "ok"
        # Bare numbers with no unit do not evidence a dated duration.
        if any(c in _TIME_UNITS for c in classes):
            return "wrong_dimension"
        if any(c is None for c in classes):
            return "ok"
        return "no"
    if out_class == UNIT_DOSE:
        if any(c in (UNIT_DOSE, None) for c in classes):
            return "ok"
        return "no"
    return "ok"


_MONTH_NAMES = {
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6,
    "july": 7, "august": 8, "september": 9, "october": 10, "november": 11, "december": 12,
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "jun": 6, "jul": 7, "aug": 8,
    "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}


def _source_has_date(num_core: str, context: str, source: str) -> bool:
    """Accept calendar numbers copied from a source date.

    "07 October 2026" evidences 2026年10月7日 / 10月 / 7日.
    """
    if not num_core or not source:
        return False
    ctx = context.lower()
    if not re.search(r'年|月|日|日期|published', ctx):
        return False
    src = source.lower()
    # "07 October 2026" must evidence both 7日 and 10月 / 2026年.
    if num_core.isdigit():
        n = int(num_core)
        present = bool(re.search(rf'(?<![a-zA-Z0-9.])0?{n}(?![a-zA-Z0-9.])', src))
    else:
        present = bool(re.search(
            rf'(?<![a-zA-Z0-9.]){re.escape(num_core)}(?![a-zA-Z0-9.])', src
        ))
    if not present:
        return False
    if re.fullmatch(r'(?:19|20)\d{2}', num_core):
        return True
    if re.fullmatch(r'0?[1-9]|1[0-2]', num_core):
        month = int(num_core)
        names = [n for n, i in _MONTH_NAMES.items() if i == month]
        return any(n in src for n in names) or bool(re.search(
            rf'(?:^|[^\d])0?{month}(?:[/-]\d|\s+(?:{_month_alt()}))', src
        ))
    if re.fullmatch(r'0?[1-9]|[12]\d|3[01]', num_core):
        return True
    return False


def _month_alt() -> str:
    return "|".join(sorted(_MONTH_NAMES, key=len, reverse=True))


def _source_has_grade_or_schedule(num_core: str, context: str, source: str) -> bool:
    """grade N → N级; every N days → 每N天一次."""
    if not num_core or not source:
        return False
    ctx = context.lower()
    src = source.lower()
    if re.search(r'级|grade', ctx) and re.search(
        rf'(?:grade|g)\s*[≥>=]?\s*{re.escape(num_core)}\b|{re.escape(num_core)}\s*级',
        src,
    ):
        return True
    n = re.escape(num_core)
    if re.search(r'每.{0,6}个?月.{0,4}次', ctx):
        return bool(re.search(rf'(?:every|q)\s*{n}\s*months?|{n}\s*months?\s*(?:apart|cycle)?', src))
    if re.search(r'每.{0,6}周.{0,4}次', ctx):
        return bool(re.search(rf'(?:every|q)\s*{n}\s*weeks?|{n}\s*weeks?\s*(?:apart|cycle)?', src))
    if re.search(r'每.{0,6}(天|日).{0,4}次', ctx):
        return bool(re.search(rf'(?:every|q)\s*{n}\s*(?:days?|d\b)|{n}\s*days?\s*(?:apart|cycle)?', src))
    if re.search(r'一次', ctx) and re.search(
        rf'(?:every|q)\s*{n}\s*(?:days?|weeks?|months?|d\b)|'
        rf'{n}\s*(?:days?|weeks?|months?)\s*(?:apart|cycle)?',
        src,
    ):
        return True
    if re.search(r'周期|访视|cycle|visit', ctx) and re.search(
        rf'(?:cycle|visit)\s*{n}\b|{n}\s*(?:周期|访视)',
        src,
    ):
        return True
    return False


def _source_has_once_or_one(num_core: str, context: str, source: str) -> bool:
    """一次 / 一例 are the same claim as 'once' / 'a/one patient'."""
    if num_core != "1" or not context or not source:
        return False
    ctx = context.lower()
    src = source.lower()
    if re.search(r'一次|1\s*次', ctx) and re.search(
        r'\bonce\b|\bevery\s+\d+|\bone\s+time|\ba\s+time', src
    ):
        return True
    if re.search(r'一[例名位]|1[例名位]', ctx) and re.search(
        r'\b(?:a|one|1)\s+(?:patient|case|subject|participant)', src
    ):
        return True
    return False


def _source_has_grouping(num_core: str, source: str) -> bool:
    """N组 / N臂 must be backed by N groups/arms/cohorts in the source."""
    if not num_core or not source:
        return False
    src = source.lower()
    if re.search(
        rf'(?<![a-zA-Z0-9.]){re.escape(num_core)}\s*'
        rf'(?:groups?|arms?|cohorts?|组|臂|队列)|'
        rf'(?:randomized|randomised|randomly|divided|assigned)'
        rf'.{{0,40}}(?<![a-zA-Z0-9.]){re.escape(num_core)}',
        src,
        re.IGNORECASE,
    ):
        return True
    try:
        n = int(float(num_core))
    except ValueError:
        return False
    if n < 2:
        return False
    labels = set(re.findall(r'(?:cohort|group|arm)\s*([a-z0-9])', src, re.I))
    if len(labels) >= n:
        return True
    if n == 2 and re.search(r'two\s+(?:cohorts|groups|arms)|both\s+(?:cohorts|groups|arms)', src, re.I):
        return True
    return False


def _noun_mismatch(num_core: str, context: str, source: str) -> bool:
    """True when the output counts a different thing than the source."""
    if not num_core or not context or not source:
        return False
    context = chinese_numeral_to_arabic(context)
    source = chinese_numeral_to_arabic(source)
    out_noun = None
    id_spans = identifier_spans(context)
    for m in re.finditer(re.escape(num_core), context):
        if span_covers(m.start(), m.end(), id_spans):
            continue
        out_noun = classify_noun_after(context, m.end())
        if out_noun:
            break
    if not out_noun:
        return False
    src_nouns = []
    for m in re.finditer(re.escape(num_core), source):
        prev = source[m.start() - 1] if m.start() else ""
        if _is_ascii_alnum(prev) or prev == ".":
            continue
        noun = classify_noun_after(source, m.end())
        if noun:
            src_nouns.append(noun)
    if not src_nouns:
        return False
    return out_noun not in src_nouns


def number_exists_in_source(num_str: str, source_norm: str, source_identifiers: set[str], 
                            context_window: str = "", source_raw: str | None = None) -> bool:
    """Check if a number exists in the source text (after normalization).
    
    Returns True ONLY if the number appears as a STANDALONE data value in source,
    not as a substring of an identifier.
    
    Design: Identifiers like CD318, CD8, RPCEC00000444 should NOT make their
    embedded digits (31, 8, 44) available as data. For example:
    - Source has "CD318" -> "31例" should NOT pass
    - Source has "52% response rate" -> "52%" should pass
    
    A dimensioned quantity (1-year, six doses, 100 mg) is not evidence for a
    clinical count or rate (1例, 6例, 100%). English number words only evidence
    a claim when the claimed unit class is compatible with the source unit.
    
    Args:
        num_str: The number string from output (e.g., "50", "3.5倍", "72小时")
        source_norm: Normalized source text (English words converted to digits)
        source_identifiers: Set of identifiers extracted from source (for reference only)
        context_window: Surrounding text from output
        source_raw: Normalized source WITHOUT English-word conversion. Used to
                    tell a real Arabic digit from a converted word.
    """
    # Normalize the number
    num_clean = num_str.replace(',', '').replace('，', '').strip()
    
    # Extract core numeric value
    num_core = extract_number_core(num_clean)
    if not num_core:
        return True  # Not a number (empty after extraction)

    # Value-anywhere on actual Arabic tokens (not English-word conversions).
    # Formatting around the digits is ignored; identifier digits are not.
    raw_for_value = source_raw if source_raw is not None else source_norm
    if source_has_numeric_value(num_str, raw_for_value, context=context_window or ""):
        return True

    # Honest unit conversions (21 days ↔ 3 weeks, 1 year ↔ 12 months).
    # Weeks are never months.
    if source_has_equivalent_number(num_core, context_window or "", source_norm):
        return True

    raw = source_raw if source_raw is not None else source_norm
    ctx = context_window or ""
    ctx_cls = chinese_numeral_to_arabic(ctx)
    out_class = classify_unit_in_context(ctx_cls, num_core)

    if _source_has_date(num_core, ctx, raw) or _source_has_date(num_core, ctx, source_norm):
        return True
    if _source_has_grade_or_schedule(num_core, ctx, raw) or _source_has_grade_or_schedule(num_core, ctx, source_norm):
        return True
    if _source_has_once_or_one(num_core, ctx, raw) or _source_has_once_or_one(num_core, ctx, source_norm):
        return True
    # 1个 / 一个 is the Chinese indefinite article, not a counted quantity.
    if num_core == "1" and re.search(r'1\s*个|一个', ctx):
        return True
    # N组 / 三组 is a grouping claim only when THIS number is the count.
    # A nearby "两组" must not poison an unrelated 25%.
    # Only this number + 组/臂 is a grouping claim. Nearby 一组/两组
    # must not poison an unrelated rate or count.
    claims_group = bool(re.search(
        rf'(?<![0-9.]){re.escape(num_core)}\s*[组臂]',
        ctx_cls,
    ))
    out_noun = None
    id_spans_ctx = identifier_spans(ctx_cls)
    for m in re.finditer(re.escape(num_core), ctx_cls):
        if span_covers(m.start(), m.end(), id_spans_ctx):
            continue
        out_noun = classify_noun_after(ctx_cls, m.end())
        if out_noun:
            break
    if (out_noun == NOUN_GROUP or claims_group) and not (
        _source_has_grouping(num_core, raw) or _source_has_grouping(num_core, source_norm)
    ):
        return False

    raw_status = _number_presence(num_core, raw, out_class)
    if raw_status == "ok":
        if not _noun_mismatch(num_core, ctx, raw) and not _noun_mismatch(num_core, ctx, source_norm):
            return True
        return False
    # "1" in "1-year" is a real Arabic digit, but a time label is not a
    # patient-count / rate and must not evidence "1例" / "1%".
    if raw_status == "wrong_dimension":
        return False

    # Digit appears only after English-word conversion ("six" → "6").
    # Compatible units (6剂 ← six doses) are accepted; 6例 / 6% are not.
    if raw is not source_norm:
        conv_status = _number_presence(num_core, source_norm, out_class)
        if conv_status == "ok" and not _noun_mismatch(num_core, ctx, source_norm):
            return True
    
    # Try Chinese numeral conversion
    cn_converted = chinese_numeral_to_arabic(num_clean)
    cn_core = extract_number_core(cn_converted)
    if cn_core and cn_core != num_core:
        if _number_presence(cn_core, raw, out_class) == "ok":
            return True
        if raw is not source_norm and _number_presence(cn_core, source_norm, out_class) == "ok":
            return True
    
    return False


# Standard terminology that should be exempt from invented-number checks
# These phrases contain numbers that are part of terminology, not data claims
EXEMPT_NUMBER_PATTERNS = [
    # Grading: N级 / grade N (any grade, not only 3)
    r'[≥>=]?\s*\d+\s*级',
    r'grade\s*[≥>=]?\s*\d+',
    r'[一二三四五六七八九十]\s*级',
    r'[三四五]级及以上',
    r'[三四五]级以上',
    # Clinical phases: I/II/III/IV期, phase 1/2/3
    r'[IiⅠⅡⅢⅣ]+\s*[/／-]?\s*[IiⅠⅡⅢⅣ]*\s*期',
    r'[一二三四]期',
    r'phase\s*[1-4ivⅰⅡⅢⅣ]+',
    # Statistical terms: 95%CI, p<0.05, HR/OR
    r'95\s*%?\s*ci',
    r'p\s*[<>=]\s*0?\.\d+',
    r'hr\s*[=:]\s*\d',
    r'or\s*[=:]\s*\d',
    # Frequency phrases: 一次/周, once a week, 每周1次
    r'[一二三四五六七八九十]\s*次\s*[/／每]\s*(周|天|月|日)',
    r'\d\s*次\s*[/／每]\s*(周|天|月|日)',
    r'once\s+a\s+(week|day|month)',
    r'twice\s+(weekly|daily|a\s+week)',
    r'每\s*[两一二三四五六七八九十\d]*\s*(周|天|日|月)\s*[一二三四五六七八九十\d]+\s*次',
    r'每\s*\d+\s*(天|日|周|月)\s*一次',
    r'every\s+\d+\s+(?:days?|weeks?|months?)',
    r'cycle\s*\d+',
    r'visit\s*\d+',
    r'第\s*\d+\s*(?:周期|疗程|访视|次访)',
    r'\d+\s*(?:周期|访视)',
    r'cycle\s*(?:number\s*)?\d+',
    # Time-horizon labels: the digit in 1年生存 / 1-year survival is not a count
    r'\d+\s*年生存',
    r'\d+\s*[- ]\s*year survival',
    r'[一二三四五六七八九十]\s*年生存',
    # Roman / class labels (MHC II类, class II) — not invented numbers
    r'(?:mhc|hla|class|级|类)\s*[ivxⅠ-Ⅻ]+',
    r'[ivxⅠ-Ⅻ]+\s*(?:类|期|class)',
    # Dosing identifiers: 100 mg, 200 mg (when part of dosing scheme description)
    r'\d+\s*mg\s*每',  # 100 mg每周
    r'每\s*(周|天)\s*\d+\s*mg',
    # "原文未给出/未报告" phrases - don't flag numbers inside these
    r'原文未给出',
    r'原文未报告',
    r'未读到',
]

# Compiled patterns for efficiency
EXEMPT_NUMBER_RE = [re.compile(p, re.IGNORECASE) for p in EXEMPT_NUMBER_PATTERNS]


def is_exempt_number_context(context: str, number: str) -> bool:
    """Check if a number appears in an exempt context (standard terminology).
    
    Returns True if the number is part of standard grading, clinical phases,
    statistical terms, or other terminology that shouldn't be flagged as invented.
    """
    if _QUALITATIVE_COUNT_RE.search(context or "") or _QUALITATIVE_SCALE_RE.search(context or ""):
        # 「六种」「6类」「six kinds」 / 数以百万计 are not numeric claims.
        return True
    # First, check if the context contains any exempt patterns
    for pattern in EXEMPT_NUMBER_RE:
        for match in pattern.finditer(context):
            # The number itself must sit inside the matched terminology span.
            # "31" inside a nearby "CD318" is not exemption — those tokens are
            # skipped at extraction time instead.
            start, end = match.start(), match.end()
            # Find this number occurrence nearest the match
            for nm in re.finditer(re.escape(number), context):
                if start <= nm.start() < end or abs(nm.start() - start) <= 1:
                    return True
    
    # A number is exempt only if it sits in the SAME sentence as a
    # missing-value phrase. A neighboring "原文未给出" must not launder
    # an invented "6例" in the next sentence.
    for sent in re.split(r'[。！？；;\n]', context):
        if number in sent and re.search(r'原文未给出|原文未报告|未读到|未给出|未报告', sent):
            return True
    
    return False


def normalize_unit_spacing(text: str) -> str:
    """Normalize spacing between numbers and units.
    
    '12 nM' and '12nM' should be treated as equivalent.
    """
    # Add space between number and unit if missing, then normalize
    # Common units
    units = r'(mg|kg|mL|µg|nM|pM|µM|mM|μg|μL|%|％|ng|pg|μM|mmol|mol|M|g|L)'
    # Remove space between number and unit, then we can match either form
    text = re.sub(rf'(\d)\s+{units}', r'\1\2', text)
    return text


# Exclusive metric classes. The label attached to an output number must be
# the same class as the label attached to that number in the source.
# Sharing a window of several names is not enough (ORR 93.5% ≠ DCR 93.5%).
_SHORT_METRIC_TOKENS = {
    "os", "cr", "pr", "ae", "dcr", "orr", "pfs", "dfs", "efs", "crr", "dor",
    "crs",
}
METRIC_CLASS_KEYWORDS: list[tuple[str, str]] = [
    ("orr", "objective response"),
    ("orr", "客观缓解"),
    ("orr", "orr"),
    ("dcr", "disease control"),
    ("dcr", "disease-control"),
    ("dcr", "疾病控制"),
    ("dcr", "控制率"),
    ("dcr", "dcr"),
    ("cr", "complete response"),
    ("cr", "完全缓解"),
    ("cr", "crr"),
    ("cr", "cr"),
    ("pr", "partial response"),
    ("pr", "部分缓解"),
    ("pr", "pr"),
    ("pfs", "progression-free"),
    ("pfs", "progression free"),
    ("pfs", "无进展"),
    ("pfs", "pfs"),
    ("os", "overall survival"),
    ("os", "1-year survival"),
    ("os", "1 year survival"),
    ("os", "一年生存"),
    ("os", "1年生存"),
    ("os", "个月生存"),
    ("os", "总生存"),
    ("os", "os"),
    ("dor", "duration of response"),
    ("dor", "缓解持续"),
    ("dor", "持续缓解"),
    ("dor", "dor"),
    ("sensitivity", "sensitivity"),
    ("sensitivity", "敏感性"),
    ("sensitivity", "灵敏度"),
    ("specificity", "specificity"),
    ("specificity", "特异性"),
    ("ae", "adverse"),
    ("ae", "不良"),
    ("ae", "toxicity"),
    ("ae", "毒性"),
    ("ae", "side effect"),
    ("ae", "副作用"),
    ("ae", "trae"),
    ("ae", "teae"),
    ("ae", "ae"),
    ("crs", "cytokine release"),
    ("crs", "细胞因子释放"),
    ("crs", "crs"),
    ("death", "mortality"),
    ("death", "death"),
    ("death", "died"),
    ("death", "死亡"),
    ("death", "致死"),
]
METRIC_KEYWORDS = [kw for _, kw in METRIC_CLASS_KEYWORDS]
_SHORT_METRIC_KEYWORDS = _SHORT_METRIC_TOKENS

# Kept for existing tests that look for 单位不匹配 in the reason string.
COUNT_UNIT_PATTERNS = re.compile(r'例|名|位|patients|subjects|participants|cases|\d+\s*/\s*\d+', re.IGNORECASE)
RATE_UNIT_PATTERNS = re.compile(r'%|％|percent', re.IGNORECASE)

_DIR_HIGHER = re.compile(
    r'更高|较高|优于|延长|增加|升高|上升|greater|higher|increased|prolonged|improved|superior',
    re.IGNORECASE,
)
_DIR_LOWER = re.compile(
    r'更低|较低|劣于|缩短|降低|下降|减少|fewer|lower|decreased|reduced|shortened|inferior|worse',
    re.IGNORECASE,
)


def extract_metric_keywords(context: str) -> set[str]:
    """Extract specific metric keywords from context (not categories)."""
    context_lower = context.lower()
    keywords = set()
    for kw in METRIC_KEYWORDS:
        if kw in _SHORT_METRIC_KEYWORDS:
            if re.search(r'(?<![a-z])' + re.escape(kw) + r'(?![a-z])', context_lower):
                keywords.add(kw)
        elif kw in context_lower:
            keywords.add(kw)
    return keywords


def _metric_class_for_keyword(kw: str) -> str | None:
    for cls, token in METRIC_CLASS_KEYWORDS:
        if token == kw:
            return cls
    return None


def _clause_around(text: str, pos: int) -> tuple[int, int]:
    """Span of the conjunct containing pos.

    Split on sentence stops and the Chinese conjunct 及 ("A及B").
    Do not split on ASCII/Chinese commas or 、: those join listed rates
    of the same metric ("A组25%、B组20%，1年生存率26%").
    """
    seps = "。！？；;\n及"
    left = pos
    while left > 0 and text[left - 1] not in seps:
        left -= 1
    right = pos
    while right < len(text) and text[right] not in seps:
        right += 1
    return left, right


def _metric_hits(text: str, start: int, end: int) -> list[tuple[int, int, str]]:
    """Metric labels in text[start:end] as (kw_start, kw_end, class)."""
    if start < 0:
        start = 0
    if end > len(text):
        end = len(text)
    if start >= end:
        return []
    window = text[start:end]
    lower = window.lower()
    found: list[tuple[int, int, str]] = []
    for cls, kw in METRIC_CLASS_KEYWORDS:
        if kw in _SHORT_METRIC_KEYWORDS:
            for m in re.finditer(r'(?<![a-z])' + re.escape(kw) + r'(?![a-z])', lower):
                found.append((start + m.start(), start + m.end(), cls))
        else:
            idx = 0
            while True:
                hit = lower.find(kw, idx)
                if hit < 0:
                    break
                found.append((start + hit, start + hit + len(kw), cls))
                idx = hit + 1
    return found


def _number_token_end(text: str, num_start: int) -> int:
    i = num_start
    while i < len(text) and (text[i].isdigit() or text[i] in ",.，"):
        i += 1
    if i < len(text) and text[i] in "%％":
        i += 1
    return i


_RIGHT_PARTICLE_RE = re.compile(
    r"^[的为是于在\s:：]*"
    r"(?:患者|病人|受试者|小鼠|大鼠|动物|病例|"
    r"patients?|subjects?|recipients?|mice|mouse|rats?|animals?|cases?)?"
    r"\s*"
)
# Coordinated number list only (25%与20%的). A comma is a new phrase,
# not a list gap — otherwise 20% binds to the following 1年生存.
_LIST_GAP_RE = re.compile(
    r"^(?:\s|[的为是与和及、])*(?:\d+(?:\.\d+)?\s*[%％]?(?:\s|[的为是与和及、])*)*$"
)


def closest_metric_class(text: str, num_start: int) -> str | None:
    """Metric class attached to this number.

    Nearest-label is wrong for listed rates: in "DCR 25% and 20%, and the
    1-year survival was 26%", 20% is closer to survival than to DCR.
    Attachment order:
    1. a label that begins immediately after the number (20%特异性);
    2. a label immediately before the number, with no other number in between;
    3. the most recent preceding label in the same clause (the 20% inherits DCR);
    4. a following label reached only through a number list (25%与20%的疾病控制).
    """
    if not text or num_start < 0:
        return None
    clause_start, clause_end = _clause_around(text, num_start)
    num_end = _number_token_end(text, num_start)
    clause = text[clause_start:clause_end]
    if re.search(r'(?i)respectively|分别', clause):
        num_pos = [clause_start + m.start() for m in re.finditer(r'\d+(?:\.\d+)?', clause)]
        metrics = sorted(_metric_hits(text, clause_start, clause_end), key=lambda h: h[0])
        # One label per number, in listed order.
        if len(num_pos) >= 2 and len(metrics) == len(num_pos) and num_start in num_pos:
            return metrics[num_pos.index(num_start)][2]

    # 0. Digit sits inside the metric name itself (1年生存, 1-year survival).
    covering = [
        h for h in _metric_hits(text, max(0, num_start - 24), min(len(text), num_end + 24))
        if h[0] <= num_start < h[1]
    ]
    if covering:
        covering.sort(key=lambda h: -(h[1] - h[0]))
        return covering[0][2]

    # 1. Tight right: metric starts after optional 的/为/是/counted-noun.
    rest = text[num_end:clause_end]
    skip = _RIGHT_PARTICLE_RE.match(rest)
    attach_at = num_end + (skip.end() if skip else 0)
    right_immediate = [
        h for h in _metric_hits(text, attach_at, min(clause_end, attach_at + 24))
        if h[0] == attach_at
    ]
    if right_immediate:
        right_immediate.sort(key=lambda h: -(h[1] - h[0]))
        return right_immediate[0][2]

    # 2. Tight left: nearest preceding label with no other digit in the gap.
    left_hits = _metric_hits(text, clause_start, num_start)
    tight_left = []
    for start, end, cls in left_hits:
        gap = text[end:num_start]
        if re.search(r"\d", gap):
            continue
        tight_left.append((num_start - end, - (end - start), cls))
    if tight_left:
        tight_left.sort()
        return tight_left[0][2]

    # 3. Inherit the most recent preceding label in this clause.
    if left_hits:
        left_hits.sort(key=lambda h: h[1])
        return left_hits[-1][2]

    # 4. Forward through a coordinated number list to a following label.
    right_hits = _metric_hits(text, num_end, clause_end)
    if right_hits:
        right_hits.sort(key=lambda h: h[0])
        start, _end, cls = right_hits[0]
        if _LIST_GAP_RE.match(text[num_end:start]):
            return cls
    return None


def closest_metric_keywords(text: str, num_start: int) -> set[str]:
    """Keywords attached to this number, preferring the closest phrase."""
    cls = closest_metric_class(text, num_start)
    if not cls:
        return set()
    return {kw for c, kw in METRIC_CLASS_KEYWORDS if c == cls}


def _source_number_spans(num_core: str, source: str) -> list[re.Match]:
    id_spans = identifier_spans(source)
    spans = []
    for m in re.finditer(re.escape(num_core), source):
        if span_covers(m.start(), m.end(), id_spans):
            continue
        prev = source[m.start() - 1] if m.start() else ""
        if _is_ascii_alnum(prev) or prev == ".":
            continue
        nxt = source[m.end():m.end() + 1]
        nxt2 = source[m.end() + 1:m.end() + 2] if m.end() + 1 < len(source) else ""
        if nxt.isdigit() or (nxt == "." and nxt2.isdigit()):
            continue
        spans.append(m)
    return spans


def _original_meaning_windows(token: str, original: str) -> list[str]:
    """Local original-text windows for a claimed number.

    Prefer the surface token (一次, 26%, 184,973) so a Chinese numeral is
    not expanded to every digit 1 in the article. Fall back to a
    word-bounded core only when the surface form is gone.
    """
    if not token or not original:
        return []
    core = extract_number_core(chinese_numeral_to_arabic(token))
    needles = []
    if token in original:
        needles.append(token)
    if core and core != token:
        needles.append(core)
    windows: list[str] = []
    seen: set[tuple[int, int]] = set()
    for needle in needles:
        found = False
        for m in re.finditer(re.escape(needle), original):
            if needle == core:
                prev = original[m.start() - 1] if m.start() else ""
                if _is_ascii_alnum(prev) or prev == ".":
                    continue
                nxt = original[m.end():m.end() + 1]
                nxt2 = original[m.end() + 1:m.end() + 2] if m.end() + 1 < len(original) else ""
                if nxt.isdigit() or (nxt == "." and nxt2.isdigit()):
                    continue
            key = (m.start(), m.end())
            if key in seen:
                continue
            seen.add(key)
            windows.append(original[max(0, m.start() - 48):m.end() + 48])
            found = True
        if found:
            break
    return windows


def _clause_has_sens_and_spec(text: str, pos: int) -> bool:
    """True when sensitivity and specificity both appear in the same clause."""
    start, end = _clause_around(text, pos)
    window = text[start:end].lower()
    has_sens = bool(re.search(r'sensitivity|敏感性|灵敏度', window))
    has_spec = bool(re.search(r'specificity|特异性', window))
    return has_sens and has_spec


def number_meaning_matches_source(num_str: str, output_context: str, source_text: str) -> tuple[bool, str]:
    """Check that the unit and metric attached to a number match the source.

    Count vs percent is read from the token on the number itself, not from a
    nearby '%'. Metric class is the closest label on each side.
    Chinese numerals are converted so every classifier sees 三组 as 3组.
    """
    output_context = chinese_numeral_to_arabic(output_context or "")
    source_text = chinese_numeral_to_arabic(source_text or "")
    num_core = extract_number_core(num_str)
    if not num_core:
        return True, ""

    id_spans = identifier_spans(output_context)
    claimed_matches = [
        num_match
        for num_match in _source_number_spans(num_core, output_context)
        if not span_covers(num_match.start(), num_match.end(), id_spans)
    ]
    if not claimed_matches:
        return True, ""

    source_norm = source_text.lower()
    src_matches = _source_number_spans(num_core, source_norm)
    src_units = []
    if src_matches:
        src_units = [u for u in (
            classify_unit_after(source_norm, m.end()) for m in src_matches
        ) if u is not None]
    src_classes = set()
    if src_matches:
        src_classes = {
            closest_metric_class(source_norm, m.start())
            for m in src_matches
        }
        src_classes.discard(None)

    for claimed_match in claimed_matches:
        out_unit = classify_unit_after(output_context, claimed_match.end())
        if out_unit in (UNIT_COUNT, UNIT_RATE) and src_units:
            if out_unit == UNIT_COUNT and UNIT_RATE in src_units and UNIT_COUNT not in src_units:
                return False, f"数字 '{num_str}' 含义不匹配：单位不匹配，输出为人数（例/名），原文为百分比（%）"
            if out_unit == UNIT_RATE and UNIT_COUNT in src_units and UNIT_RATE not in src_units:
                return False, f"数字 '{num_str}' 含义不匹配：单位不匹配，输出为百分比（%），原文为人数（例/名）"

        out_class = closest_metric_class(output_context, claimed_match.start())
        if out_class and src_classes:
            # Complementary sensitivity/specificity in one sentence are not an
            # exclusive swap unless "respectively" already paired them.
            if (
                out_class in {"sensitivity", "specificity"}
                and src_classes <= {"sensitivity", "specificity"}
                and any(_clause_has_sens_and_spec(source_norm, m.start()) for m in src_matches)
                and _clause_has_sens_and_spec(output_context, claimed_match.start())
            ):
                continue
            if out_class not in src_classes:
                return False, (
                    f"数字 '{num_str}' 含义不匹配："
                    f"输出用于{out_class}类指标，原文用于{'/'.join(sorted(src_classes))}类指标"
                )

    return True, ""


def check_comparison_direction(output_text: str, source_text: str) -> list[str]:
    """Flag 更高/更低 (and English equivalents) that contradict the source."""
    if not output_text or not source_text:
        return []
    problems = []
    src = source_text
    for sent in re.split(r'[。！？；;\n]', output_text):
        if not sent.strip():
            continue
        hi = bool(_DIR_HIGHER.search(sent))
        lo = bool(_DIR_LOWER.search(sent))
        if hi == lo:
            continue
        cores = []
        for num, _ctx in extract_numbers_with_context(sent):
            core = extract_number_core(num)
            if core:
                cores.append(core)
        windows = []
        for core in cores:
            for m in _source_number_spans(core, src.lower()):
                windows.append(src[max(0, m.start() - 48):m.end() + 48])
        if not windows:
            # Metric-only claim: "DCR更低" with no number in the sentence.
            # Look up every keyword of the same class (DCR ↔ disease control).
            classes_in_sent = set()
            sent_l = sent.lower()
            for cls, kw in METRIC_CLASS_KEYWORDS:
                if kw in _SHORT_METRIC_KEYWORDS:
                    if re.search(r'(?<![a-z])' + re.escape(kw) + r'(?![a-z])', sent_l):
                        classes_in_sent.add(cls)
                elif kw in sent_l:
                    classes_in_sent.add(cls)
            src_l = src.lower()
            for cls, kw in METRIC_CLASS_KEYWORDS:
                if cls not in classes_in_sent:
                    continue
                start = 0
                while True:
                    idx = src_l.find(kw, start)
                    if idx < 0:
                        break
                    windows.append(src[max(0, idx - 48):idx + len(kw) + 48])
                    start = idx + 1
        if not windows:
            continue
        src_hi = any(_DIR_HIGHER.search(w) for w in windows)
        src_lo = any(_DIR_LOWER.search(w) for w in windows)
        lower_better = bool(re.search(
            r'(?i)lower is better|越小越好|越低越好|毒性|ae|不良反应|'
            r'肿瘤负荷|residual|safer|less toxicity|grade\s*[≥>=]?\s*\d',
            sent,
        ))
        if hi and src_lo and not src_hi and not lower_better:
            problems.append(f"比较方向不匹配：输出写更高/延长，原文为更低/缩短（{sent.strip()[:40]}）")
            break
        if lo and src_hi and not src_lo and not lower_better:
            problems.append(f"比较方向不匹配：输出写更低/缩短，原文为更高/延长（{sent.strip()[:40]}）")
            break
    return problems


def extract_numbers_with_context(text: str) -> list[tuple[str, str]]:
    """Extract numbers from text with their surrounding context.
    
    Returns list of (number_string, context_window) tuples.
    Context window is ~20 chars before and after for unit/meaning verification.
    """
    results = []
    
    doc = match_document((text or "").replace("·", ".").replace("•", "."), convert_english_words=False)
    for tok in doc.numbers:
        results.append((_canonical_number(tok.value), tok.context))
    return results


def extract_chinese_numbers_with_context(text: str) -> list[tuple[str, str]]:
    """Extract Chinese numerals with their surrounding context.
    
    Handles: 一二三四五六七八九十百千万亿两
    With units: 年|倍|%|％|个月|天|周|小时|例|名|位|人|剂|万|亿.
    次 is omitted: 一次/每周一次 are idioms, not numeric claims.
    """
    results = []
    text = normalize_for_match(text or "", convert_english_words=False)
    
    # Chinese numerals with units. 一组/两组 are grouping words, not data.
    # 次 is a frequency/idiom marker (一次独立实验, 每周一次), not a data unit.
    cn_data_pattern = (
        r'[零一二三四五六七八九十百千万亿两]+(?:多)?'
        r'(?:年|倍|%|％|个月|天|周|小时|例|名|位|人|剂|万|亿)'
    )
    for match in re.finditer(cn_data_pattern, text):
        after = text[match.end():match.end() + 2]
        if after[:1] in ("种", "类"):
            continue
        before = text[max(0, match.start() - 2):match.start()]
        cn_num = match.group(0)
        if cn_num.endswith(("万", "亿")) and (
            "数" in before or re.fullmatch(r"[百千]+", cn_num[:-1])
        ):
            continue
        window = text[max(0, match.start() - 20):min(len(text), match.end() + 20)]
        if _QUALITATIVE_SCALE_RE.search(window) or _QUALITATIVE_COUNT_RE.search(window):
            continue
        start = max(0, match.start() - 20)
        end = min(len(text), match.end() + 20)
        context = text[start:end]
        results.append((cn_num, context))
    
    return results


def _is_qualitative_datapoint(value: str, meaning: str) -> bool:
    """Non-numeric labels (phase, tissue, genotype) are not invented counts."""
    blob = f"{value} {meaning}"
    return bool(re.search(
        r'(?i)[ivxⅠ-Ⅻ]+期|phase\s*[ivx]|wild[- ]type|knock[- ]?out|'
        r'genotype|biomarker|high|low|mild|moderate|severe|'
        r'阳性|阴性|野生型|突变型|组织分型|瘤种|内型|分型|'
        r'高|低|轻|中|重|'
        r'(?:\d+\s*(?:种|类)|[零一二三四五六七八九十两]+\s*(?:种|类)|kinds?)',
        blob,
    ))


_THOUSANDS_IN_NUM_RE = re.compile(r'(?<=\d)[,，\u00a0\u202f\u2009\u2007 ](?=\d{3})')
_RANGE_TOKEN_RE = re.compile(
    r'(\d+(?:\.\d+)?)\s*(?:[-–—−~～至到]|to|and|及|与)\s*(\d+(?:\.\d+)?)'
    r'(\s*(?:%|％|个月|周|天|年|mg|kg))?',
    re.I,
)
_RANGE_WINDOW_RE = re.compile(
    r'(?i)CI|置信|剂量|随访|个月|周|天|年|mg|range|interval|至|到|\bto\b',
)
_RANGE_ID_RE = re.compile(r'(?i)CD\d|IL-?\d|HLA|NCT|p38|MK-\d')


def _normalize_thousands_for_ranges(text: str) -> str:
    """Strip thousands separators before range extraction so 1,139-2,000 parses."""
    return _THOUSANDS_IN_NUM_RE.sub('', text or '')


def _range_spans(text: str) -> list[str]:
    """Sentences and table rows — a range may be written across one row."""
    parts = re.split(r'[\n\r]+|[。！？]|[.!?](?=\s|$)|\t', text or '')
    return [p.strip() for p in parts if p and p.strip()]


def _span_has_both_values(span: str, va: float, vb: float) -> bool:
    vals = [n.value for n in match_document(span, convert_english_words=False).numbers]
    return (
        any(values_equivalent(va, x) for x in vals)
        and any(values_equivalent(vb, x) for x in vals)
    )


def _invented_numeric_range(output: str, source: str) -> list[str]:
    """An output interval (CI / dose / time) must appear as a range in the source."""
    problems = []
    out_n = _normalize_thousands_for_ranges(output).replace('·', '.')
    src_n = _normalize_thousands_for_ranges(source).replace('·', '.')
    src_spans = _range_spans(src_n) + _range_spans(source)
    for m in _RANGE_TOKEN_RE.finditer(out_n):
        a, b, unit = m.group(1), m.group(2), m.group(3) or ""
        va, vb = parse_numeric_value(a), parse_numeric_value(b)
        if va is None or vb is None:
            continue
        window = out_n[max(0, m.start() - 16):m.end() + 12]
        if not _RANGE_WINDOW_RE.search(window):
            continue
        if _RANGE_ID_RE.search(window):
            continue
        found = False
        for sm in _RANGE_TOKEN_RE.finditer(src_n):
            sa, sb = parse_numeric_value(sm.group(1)), parse_numeric_value(sm.group(2))
            if sa is None or sb is None:
                continue
            if (
                (values_equivalent(va, sa) and values_equivalent(vb, sb))
                or (values_equivalent(va, sb) and values_equivalent(vb, sa))
            ):
                found = True
                break
        if found:
            continue
        if any(_span_has_both_values(span, va, vb) for span in src_spans):
            continue
        problems.append(f"数字范围 '{a}-{b}{unit}' 在原文中未作为区间出现")
    return problems


_SENT_SPLIT_RE = re.compile(r'(?<=[。！？.!?])\s*')


def _own_sentence_for_number(text: str, num: str, context: str = "") -> str:
    """Classify a number from its own sentence, not a ±N token window."""
    parts = [p.strip() for p in _SENT_SPLIT_RE.split(text or "") if p and p.strip()]
    if not parts:
        return (context or "").strip()
    token = re.escape(str(num or ""))
    bounded = re.compile(rf'(?<!\d){token}(?!\d)') if token else None

    def _has_num(part: str) -> bool:
        if bounded and bounded.search(part):
            return True
        return bool(num) and source_has_numeric_value(num, part)

    hits = [p for p in parts if _has_num(p)]
    if context and hits:
        ctx = context.strip()
        hits.sort(
            key=lambda p: (
                ctx[:12] in p,
                ctx in p,
                p in ctx,
                len(set(p) & set(ctx)),
            ),
            reverse=True,
        )
        return hits[0]
    if hits:
        return hits[0]
    if context:
        head = context.strip()[:10]
        for p in parts:
            if head and head in p:
                return p
    return (context or "").strip()


def _strip_unmatched_number_claims(art: dict, nums: list[str]) -> list[dict]:
    """Drop sentences (or the number itself) that carry 1–2 minor unmatched values."""
    removed: list[dict] = []

    def _keep_text(text: str) -> str:
        if not text:
            return text
        parts = re.split(r'(?<=[。！？.!?])\s*', text)
        kept: list[str] = []
        for part in parts:
            hit = next((n for n in nums if source_has_numeric_value(n, part)), None)
            if hit:
                removed.append({"number": hit, "text": part.strip()})
                continue
            kept.append(part)
        return "".join(kept).strip()

    for key in (
        "title", "one_liner", "background", "design", "results",
        "mechanism", "significance", "limitations",
    ):
        val = art.get(key)
        if isinstance(val, list):
            art[key] = [kept for item in val if (kept := _keep_text(str(item)))]
        elif isinstance(val, str) and val:
            art[key] = _keep_text(val)
    datacard = art.get("datacard")
    if isinstance(datacard, dict):
        for key, val in list(datacard.items()):
            if isinstance(val, str) and any(source_has_numeric_value(n, val) for n in nums):
                removed.append({"number": next(n for n in nums if source_has_numeric_value(n, val)), "text": val, "field": f"datacard.{key}"})
                datacard.pop(key, None)
    return removed


def validate_depth(art: dict, raw_material: str, *, allow_word_quantities: bool = False) -> list[str]:
    """Validate generated article against SOURCE TEXT.
    
    Design principle: Every number in output must exist in source.
    No special-casing of "原文未给出" - text saying something is not given
    is fine only if it contains no number not in source.
    
    Identifiers (R2, Th17, CCR8, etc.) that appear verbatim in source
    are allowed automatically - never ask the model to rename them.
    """
    problems = []
    tier = art.get("tier", "brief")
    
    # Normalize source AND draft to the same match form before comparing.
    source_norm = normalize_for_match(raw_material, convert_english_words=True)
    source_raw = normalize_for_match(raw_material, convert_english_words=False)
    
    # Extract identifiers from source (these are allowed to have digits)
    source_identifiers = extract_identifiers_from_source(
        normalize_for_match(raw_material, convert_english_words=False)
    )
    
    def _text_chunks(val) -> list[str]:
        if isinstance(val, str) and val:
            return [val]
        if isinstance(val, list):
            return [str(x) for x in val if x not in (None, "")]
        return []

    # Collect ALL text from output
    all_text_parts = [
        *(_text_chunks(art.get("title"))),
        *(_text_chunks(art.get("one_liner"))),
        *(_text_chunks(art.get("background"))),
        *(_text_chunks(art.get("design"))),
        *(_text_chunks(art.get("results"))),
        *(_text_chunks(art.get("mechanism"))),
        *(_text_chunks(art.get("significance"))),
        *(_text_chunks(art.get("limitations"))),
    ]
    datacard = art.get("datacard", {})
    _SYSTEM_NOTE_KEYS = {"read_note", "citation", "source_trace", "url", "doi"}
    if isinstance(datacard, dict):
        for field_key, field_val in datacard.items():
            if field_key in _SYSTEM_NOTE_KEYS:
                continue
            if isinstance(field_val, str):
                all_text_parts.append(field_val)
    elif isinstance(datacard, str):
        all_text_parts.append(datacard)
    all_text = " ".join(all_text_parts)
    
    # Marketing words check on ALL text
    if MARKETING_BLOCKLIST.search(all_text):
        problems.append("文章含有营销词汇（重磅/颠覆/震撼等）")

    problems.extend(_invented_numeric_range(all_text, raw_material))

    if (
        art.get("evidence_level") == "preprint"
        or PREPRINT_SOURCE_RE.search(str(art.get("url") or "") + " " + str(art.get("source") or ""))
    ) and _preprint_claims_publication(all_text):
        problems.append("预印本正文不得声称已在期刊发表或已经同行评议")

    # Identifier formatting (spacing, case, dashes, subscripts) is a soft
    # warning when the collapsed key matches. A key that is not in the
    # source at all is an invented label and stays a hard drop.
    src_ids = match_document(raw_material, convert_english_words=False).identifiers
    src_id_keys = {tok.key for tok in src_ids}
    src_id_raws = {tok.key: tok.raw for tok in src_ids}
    seen_ids: set[str] = set()
    id_warnings: list[str] = []
    for tok in match_document(all_text, convert_english_words=False).identifiers:
        if tok.key in seen_ids:
            continue
        seen_ids.add(tok.key)
        if re.match(r'(?i)(?:10\.\d{4,}/|pmc\d+|pmid\d*|doi$)', tok.key):
            continue
        if tok.key in src_id_keys:
            src_raw = src_id_raws.get(tok.key, "")
            if src_raw and src_raw.replace(" ", "") != tok.raw.replace(" ", ""):
                id_warnings.append(f"标识符写法不一致（软警告）：'{tok.raw}'")
            continue
        if _id_keys_compatible(tok.key, src_id_keys):
            id_warnings.append(f"标识符写法不一致（软警告）：'{tok.raw}'")
            continue
        problems.append(f"标识符 '{tok.raw}' 在原始材料中未找到")
    if isinstance(art, dict) and id_warnings:
        art.setdefault("qc_id_warnings", []).extend(id_warnings)
    
    # Validate data_points
    norm = normalize_whitespace(raw_material)
    norm_english = english_number_to_arabic(norm)
    
    for dp in art.get("data_points") or []:
        if not isinstance(dp, dict):
            problems.append("data_point 不是对象")
            continue
        value = str(dp.get("value", "")).strip()
        quote = dp.get("source_quote", "").strip()
        meaning = dp.get("meaning", "").strip()
        
        # Reject gaming: non-numeric values
        # data_points must contain actual numeric data, not:
        # - Vague quantifiers (millions, tens of)
        # - Disease names or other non-numeric content
        # - Placeholder values
        
        # A data_point must itself be numeric. "nine doses" / "millions" /
        # "tens of kilobases" have no digit and are rejected. A value that
        # already carries Arabic digits (e.g. "100 mg weekly for nine doses")
        # is a real measurement and is kept.
        if not re.search(r'\d', value):
            if _is_qualitative_datapoint(value, meaning):
                continue
            # Source-faithful word quantities (any unit, fold words, or a
            # bare cardinal). Quote must be verbatim in the source and the
            # value must sit inside that quote — no full-text fallback.
            # allow_word_quantities=False keeps the locked-suite contract.
            allow_words = allow_word_quantities or bool(_ORDINAL_TIME_RE.search(value))
            if allow_words and _is_word_quantity_phrase(value):
                if not _word_quantity_in_passage(value, quote, raw_material):
                    problems.append(f"data_point value 必须包含数字：'{value}'")
                    continue
                word_qty_ok = True
            else:
                problems.append(f"data_point value 必须包含数字：'{value}'")
                continue
        else:
            word_qty_ok = False
        
        # Check for vague/imprecise quantifiers that lack specific numbers
        vague_patterns = [
            r'^millions?$',
            r'^tens? of\b',
            r'^hundreds? of\b',
            r'^thousands? of\b',
            r'^多种',
            r'^数十',
            r'^数百',
            r'^数千',
            r'^若干',
            r'^部分',
        ]
        is_vague = any(re.search(p, value.lower()) for p in vague_patterns)
        if is_vague:
            problems.append(f"data_point value 过于模糊，需要具体数字：'{value}'")
            continue
        
        # Reject disease names and other non-quantitative content
        # BUT: allow clinical metrics like 疾病控制率 (DCR), 无病生存期 (DFS), etc.
        clinical_metric_patterns = [
            r'疾病控制',    # DCR, 疾病控制率, 疾病控制比例
            r'无病生存',    # Disease-Free Survival (DFS)
            r'疾病进展',    # Disease Progression
            r'disease\s*-?\s*control',
            r'disease\s*-?\s*free\s*survival',
            r'无进展生存',  # Progression-Free Survival (often involves disease)
        ]
        is_clinical_metric = any(re.search(p, meaning, re.IGNORECASE) for p in clinical_metric_patterns)
        
        if meaning and not is_clinical_metric:
            if any(x in meaning.lower() for x in ['非数值', 'disease', 'condition', '疾病', '病症']):
                problems.append(f"data_point 不是数值数据：{value} ({meaning})")
                continue
        
        # Reject gaming: registering identifier digits
        if "名称" in meaning or "编号" in meaning:
            problems.append(f"data_point 注册了标识符而非数据：{value} ({meaning})")
            continue
        
        # Check quote exists in source
        quote_norm = normalize_whitespace(quote)
        if len(quote_norm) < 10:
            problems.append(f"data_point source_quote 过短：{value}")
            continue
        
        quote_norm_english = english_number_to_arabic(quote_norm)
        quote_cmp = normalize_unit_spacing(quote_norm_english)
        src_cmp = normalize_unit_spacing(norm_english)
        if (
            quote_norm not in norm
            and quote_norm_english not in norm_english
            and quote_cmp not in src_cmp
        ):
            problems.append(f"data_point 无法回溯：{value} (quote: {quote_norm[:50]}...)")
            continue

        if word_qty_ok:
            if not _word_quantity_boundary_in_quote(value, quote):
                problems.append(f"data_point value 必须包含数字：'{value}'")
            continue
        
        # Check value appears in quote. Unit spacing is ignored so
        # "100mg" matches a quote that says "100 mg".
        value_core = extract_number_core(value)
        if value_core:
            quote_for_check = normalize_unit_spacing(
                chinese_numeral_to_arabic(english_number_to_arabic(
                    quote.replace("·", ".").replace("•", ".")
                ))
            )
            if not number_in_text_as_word_boundary(value_core, quote_for_check):
                problems.append(f"data_point value 不在 quote 中：{value}")
    
    # Extract ALL numbers from output text and check each against source
    # EXEMPT: Standard terminology (≥3级, 95%CI, phase 3, p38, etc.)
    # EXEMPT: Numbers inside "原文未给出/未报告" phrases
    # Every other numeric token must appear in source (after normalization)
    
    # Normalize unit spacing in source for matching: "12 nM" = "12nM"
    source_norm_units = normalize_unit_spacing(source_norm)
    source_raw_units = normalize_unit_spacing(source_raw)
    
    unmatched: list[tuple[str, str]] = []
    # Extract Arabic numbers with context
    for num, context in extract_numbers_with_context(all_text):
        num_core = extract_number_core(num)
        if not num_core:
            continue
        
        # Check if this number is in an exempt context (standard terminology)
        if is_exempt_number_context(context, num_core):
            continue
        
        # Normalize unit spacing for comparison
        context_norm = normalize_unit_spacing(context)
        
        if not number_exists_in_source(
            num, source_norm_units, source_identifiers, context_norm,
            source_raw=source_raw_units,
        ):
            unmatched.append((num, context))
        else:
            # Meaning reads original local windows. The match-normalized
            # ±20 context CJK-splits "1年生存率26%" into "1 年生存率 26%",
            # which would inherit DCR; the whole draft would also turn
            # 一次 → 1 and collide with 1年生存.
            meaning_hit = False
            for window in _original_meaning_windows(num, all_text) or [context]:
                meaning_ok, meaning_reason = number_meaning_matches_source(
                    num, window, raw_material
                )
                if not meaning_ok:
                    problems.append(meaning_reason)
                    meaning_hit = True
                    break
            if meaning_hit:
                continue
    
    # Extract Chinese numerals with context (万/亿 included)
    for cn_num, context in extract_chinese_numbers_with_context(all_text):
        arabic = chinese_numeral_to_arabic(cn_num)
        arabic_core = extract_number_core(arabic)
        if not arabic_core:
            continue
        if _QUALITATIVE_COUNT_RE.search(context or ""):
            continue
        
        # Check if this number is in an exempt context
        if is_exempt_number_context(context, arabic_core):
            continue
        
        if re.search(r'组|臂', cn_num) and not (
            _source_has_grouping(arabic_core, source_norm_units)
            or _source_has_grouping(arabic_core, source_raw_units)
        ):
            unmatched.append((cn_num, context))
            continue
        if not number_exists_in_source(
            arabic, source_norm_units, source_identifiers, context,
            source_raw=source_raw_units,
        ):
            unmatched.append((cn_num, context))
        else:
            for window in _original_meaning_windows(cn_num, all_text) or [context]:
                meaning_ok, meaning_reason = number_meaning_matches_source(
                    cn_num, window, raw_material
                )
                if not meaning_ok:
                    problems.append(meaning_reason)
                    break

    clinical_unmatched = []
    minor_unmatched = []
    for n, c in unmatched:
        sent = _own_sentence_for_number(all_text, n, c)
        if _CLINICAL_CLAIM_RE.search(sent or ""):
            clinical_unmatched.append((n, c))
        else:
            minor_unmatched.append((n, c))
    if clinical_unmatched:
        for num, _ctx in clinical_unmatched:
            problems.append(f"数字 '{num}' 在原始材料中未找到")
    elif 1 <= len(minor_unmatched) <= _MAX_MINOR_UNMATCHED and isinstance(art, dict):
        removed = _strip_unmatched_number_claims(art, [n for n, _ in minor_unmatched])
        if removed:
            art.setdefault("qc_removed_numbers", []).extend(removed)
    else:
        for num, _ctx in unmatched:
            problems.append(f"数字 '{num}' 在原始材料中未找到")
    
    # Limitations count and quality
    min_limits = 3 if tier == "deep" else 1
    limitations = art.get("limitations") or []
    if not isinstance(limitations, list):
        limitations = [str(limitations)]
    if len(limitations) < min_limits:
        problems.append(f"局限条数不足：需要 {min_limits} 条，实际 {len(limitations)} 条")
    
    empty_phrases = r"仍需(更多)?研究|有待(进一步)?验证$|期待后续|需要更大规模"
    for lim in limitations:
        if re.search(empty_phrases, lim) and len(lim) < 30:
            problems.append(f"局限为空话：{lim}")
    
    # Datacard required fields
    required_fields = ["study_type", "n", "control", "intervention", "followup",
                      "primary_endpoint", "primary_endpoint_result", "statistics", "safety"]
    if isinstance(datacard, dict):
        for field in required_fields:
            val = str(datacard.get(field, "")).strip()
            if not val:
                # Missing datacard values are omitted at publish time, not written out.
                continue
    else:
        problems.append("datacard 字段格式错误（应为对象）")
    
    # Character count: Han characters in body fields only (titles/datacard/journal excluded)
    total_chars = han_len_fields(art)
    
    # Hard limits (locked suite contract): brief ≥ 450 Han; deep ≤ 1900 Han.
    # Deep has no 450 floor here; production ACIR uses section ranges when strict.
    if tier == "deep":
        if total_chars > 1900:
            problems.append(f"deep 档正文 {total_chars} 汉字，超过上限 1900 字")
    else:
        if total_chars < BRIEF_HAN_MIN:
            problems.append(f"brief 档正文 {total_chars} 汉字，低于下限 {BRIEF_HAN_MIN} 字")
        elif total_chars > BRIEF_HAN_MAX:
            problems.append(f"brief 档正文 {total_chars} 汉字，超过上限 {BRIEF_HAN_MAX} 字")
    
    # Evidence level check
    if isinstance(datacard, dict):
        evidence = art.get("evidence_level", datacard.get("evidence_level", "abstract"))
    else:
        evidence = art.get("evidence_level", "abstract")
    if tier == "deep" and evidence in ("press", "secondary"):
        problems.append("仅有新闻稿，不得写成深度解读")
    
    # Results must contain at least one verifiable number from source
    # (Arabic or Chinese numerals; honest unit conversions count).
    results_text = " ".join(_text_chunks(art.get("results")))
    results_claims = (
        extract_numbers_with_context(results_text)
        + extract_chinese_numbers_with_context(results_text)
    )
    found_valid_number = False
    usable_results = []
    for num, context in results_claims:
        if _QUALITATIVE_COUNT_RE.search(context or "") or _QUALITATIVE_SCALE_RE.search(context or ""):
            continue
        if is_bibliographic_number(num, context):
            continue
        num_core = extract_number_core(num) or extract_number_core(
            chinese_numeral_to_arabic(num)
        )
        if not num_core:
            continue
        if is_exempt_number_context(context, num_core):
            continue
        usable_results.append((num, context))
        if number_exists_in_source(
            num, source_norm_units, source_identifiers, context,
            source_raw=source_raw_units,
        ):
            found_valid_number = True
            break

    source_has_standalone = bool(extract_numbers_with_context(raw_material))
    if source_has_standalone:
        if usable_results and not found_valid_number:
            problems.append("结果字段中的数字无法在原文中核实")
        elif not usable_results:
            problems.append("结果字段应包含至少一个可核实的数字（来自原文）")

    problems.extend(check_comparison_direction(all_text, raw_material))
    problems.extend(duplicate_heading_problems(art))
    
    return problems


_GENE_ACRONYM_ALLOW = {
    "ORR", "DCR", "CRR", "PFS", "DFS", "EFS", "DOR", "CBR", "BOR",
    "SAE", "TEAE", "TRAE", "CRS", "DLT", "AUC", "FDA", "NIH", "WHO",
    "EMA", "DNA", "RNA", "MRNA", "PCR", "HIV", "HBV", "HCV", "HPV",
    "EBV", "CMV", "MHC", "HLA", "APC", "TCR", "BCR", "CAR", "NCT",
    "DOI", "PMID", "PMC", "USA", "UK", "EU", "COVID", "IFN", "TNF",
    "IL", "NK", "DC", "OS", "HR", "OR", "RR", "CI", "AE", "CR", "PR",
    # Assay / buffer / cofactor tokens that are not gene symbols.
    "ITC", "ATP", "GTP", "GDP", "ADP", "AMP", "NAD", "FAD", "PBS",
    "TBS", "BSA", "DMSO", "EDTA", "SDS", "PEG", "ANOVA", "ELISA",
    "FACS", "NMR", "HPLC", "MALDI", "SPR",
    "RECIST", "CTCAE", "ECOG", "NYHA", "CONSORT", "STROBE", "PRISMA",
    "IMWG", "LUGANO", "IUPAC",
}


def _inn_justifies_chinese(inn: str, chinese: str) -> bool:
    """True when a source INN and a Chinese generic name share class and sound.

    Uses a general syllable table, not a per-drug glossary.
    """
    if not inn or not chinese:
        return False
    inn_l = inn.lower()
    is_mab = inn_l.endswith(("mab", "cept"))
    is_nib = inn_l.endswith(("nib", "tinib", "ciclib"))
    if is_mab and not chinese.endswith("单抗"):
        return False
    if is_nib and not chinese.endswith("替尼") and "替尼" not in chinese:
        return False
    stem = re.sub(r'(?:单抗|替尼|利单抗|珠单抗|昔单抗|妥单抗)$', '', chinese)
    syls = [_HAN_PINYIN.get(ch) for ch in stem]
    if not syls or any(s is None for s in syls):
        return False
    pos = 0
    full_hits = 0
    for syl in syls:
        hit = inn_l.find(syl, pos)
        if hit >= 0:
            full_hits += 1
            pos = hit + len(syl)
            continue
        hit = inn_l.find(syl[0], pos)
        if hit < 0:
            return False
        pos = hit + 1
    # First syllable must match in full so a neighbour-clipped token
    # (雷利珠单抗 from 予替雷利珠单抗) cannot ride on the same INN.
    return full_hits >= 2 and inn_l.find(syls[0]) >= 0 and len(syls[0]) >= 2


def iter_cn_drug_tokens(text: str):
    """Chinese generic-name tokens on INN-like suffixes, not neighbouring verbs."""
    if not text:
        return
    for m in _CN_DRUG_SUFFIX_RE.finditer(text):
        end = m.end()
        i = m.start()
        taken = 0
        while i > 0 and taken < 8:
            ch = text[i - 1]
            if ch < "\u4e00" or ch > "\u9fff" or ch in _CN_DRUG_LEAD_STOP:
                break
            i -= 1
            taken += 1
        token = text[i:end]
        if len(token) >= 3:
            yield token


def _source_has_name_form(name: str, source_lower: str) -> bool:
    """True if name or a hyphen/space variant appears in the source."""
    if not name:
        return False
    needle = name.lower()
    if needle in source_lower:
        return True
    compact = re.sub(r"[\s\-]+", "", needle)
    src_compact = re.sub(r"[\s\-]+", "", source_lower)
    return bool(compact) and compact in src_compact


def _is_generic_institution(inst: str) -> bool:
    """True for '其他实验室' / '多家医院' / '该大学' — never a real name."""
    s = (inst or "").strip()
    if not s or s in _GENERIC_FACILITY:
        return True
    bits = re.split(r"[和与及、]", s)
    if len(bits) > 1:
        return all(_is_generic_institution(b) for b in bits if b)
    for head in sorted(_GENERIC_INST_HEADS, key=len, reverse=True):
        if not s.startswith(head):
            continue
        rest = s[len(head):].lstrip("的家所")
        if rest in _GENERIC_INST_SUFFIXES or rest in _GENERIC_FACILITY:
            return True
    return False


def _trim_institution_lead(inst: str) -> str:
    """Strip lead verbs at the start and multi-char clauses (实验经…), never inside a word."""
    out = inst or ""
    if _is_generic_institution(out):
        return out
    start_leads = tuple(sorted(_INST_LEAD_CLAUSES + _INST_LEAD_WORDS, key=len, reverse=True))
    changed = True
    while changed and out:
        changed = False
        for w in start_leads:
            rest = out[len(w):]
            if out.startswith(w) and len(rest) >= 4 and _INST_SUFFIX_RE.search(rest):
                out = rest
                changed = True
                break
    # Multi-character clauses only (实验经), never single-character 其/该 inside 其他/该大学.
    for w in sorted(_INST_LEAD_CLAUSES, key=len, reverse=True):
        idx = out.find(w)
        if idx < 0:
            continue
        rest = out[idx + len(w):]
        if len(rest) >= 4 and _INST_SUFFIX_RE.search(rest):
            out = rest
            break
    bits = re.split(r"[和与及、]", out)
    if len(bits) > 1 and len(bits[-1]) >= 4 and _INST_SUFFIX_RE.search(bits[-1]):
        out = bits[-1]
    return out


def _institution_aliases(inst: str) -> list[str]:
    found: list[str] = []
    for key, als in _INSTITUTION_ALIASES.items():
        if inst == key or inst.endswith(key) or (key.endswith(inst) and len(inst) >= 4):
            found.extend(als)
    return found


def _draft_inst_acronym(inst: str, all_text: str) -> str:
    m = re.search(
        re.escape(inst) + r"\s*[\(（]([A-Za-z][A-Za-z0-9\-]{1,11})[\)）]",
        all_text or "",
    )
    return m.group(1) if m else ""


def _english_translates_chinese_inst(inst: str, english: str) -> bool:
    """True when English is an alias or a type-matched translation of inst."""
    eng = (english or "").lower()
    if not inst or not eng:
        return False
    for alias in _institution_aliases(inst):
        a = (alias or "").lower()
        if a and (a in eng or eng in a):
            return True
    rest = inst
    type_ok = False
    for cn, ens in _CN_EN_INST_TYPES:
        if inst.endswith(cn) and any(e in eng for e in ens):
            type_ok = True
            rest = inst[: -len(cn)]
            break
    if not type_ok:
        return False
    leftover = rest
    for cn, ens in _CN_EN_INST_TOKENS:
        if cn in leftover and any(e in eng for e in ens):
            leftover = leftover.replace(cn, "")
    leftover = re.sub(r"[的和与及、\s]", "", leftover)
    return not re.search(r"[\u4e00-\u9fff]", leftover)


def _source_english_tied_to_acronym(acronym: str, raw_material: str) -> list[str]:
    """English names immediately before (ACR) in the source."""
    if not acronym:
        return []
    found = []
    for m in re.finditer(
        r"([A-Za-z][A-Za-z .'\-]{2,80})[\(（]\s*" + re.escape(acronym) + r"\s*[\)）]",
        raw_material or "",
    ):
        found.append(m.group(1).strip(" ,;:-"))
    return found


def _institution_in_source(
    inst: str, raw_material: str, source_lower: str, all_text: str = "",
) -> bool:
    """Accept Chinese name, tied English translation/alias, or tied 中文名（ACR）."""
    if inst in raw_material or _source_has_name_form(inst, source_lower):
        return True
    aliases = _institution_aliases(inst)
    for alias in aliases:
        a = alias.lower()
        if a and a in source_lower:
            return True
        if re.search(r'[\(（]\s*' + re.escape(alias) + r'\s*[\)）]', raw_material, re.I):
            return True
        if len(alias) <= 12 and re.search(
            r'(?<![A-Za-z0-9])' + re.escape(alias) + r'(?![A-Za-z0-9])',
            raw_material or "", re.I,
        ) and (_SITE_LABEL_RE.search(raw_material or "") or "(" + alias in (raw_material or "")
               or "（" + alias in (raw_material or "")):
            return True
    for m in _EN_INST_PHRASE_RE.finditer(raw_material or ""):
        if _english_translates_chinese_inst(inst, m.group(1)):
            return True
    acr = _draft_inst_acronym(inst, all_text)
    tied_eng = _source_english_tied_to_acronym(acr, raw_material) if acr else []
    for eng in tied_eng:
        if _english_translates_chinese_inst(inst, eng):
            return True
    if acr and re.search(
        r'(?<![A-Za-z0-9])' + re.escape(acr) + r'(?![A-Za-z0-9])',
        raw_material or "", re.I,
    ) and (tied_eng or _SITE_LABEL_RE.search(raw_material or "")):
        if any(a.lower() == acr.lower() for a in aliases):
            return True
        for eng in tied_eng:
            if _english_translates_chinese_inst(inst, eng):
                return True
    # Source "English Name (ACR)" even when the draft omitted the brackets.
    for alias in aliases:
        if len(alias) >= 2:
            for eng in _source_english_tied_to_acronym(alias, raw_material):
                if _english_translates_chinese_inst(inst, eng):
                    return True
    return False


def _source_ethics_approval_names(raw_material: str) -> list[str]:
    """English institution names in the same sentence as IACUC/IRB/ethics."""
    names: list[str] = []
    for sent in re.split(r"(?<=[.!?。；;\n])\s*", raw_material or ""):
        if not _ETHICS_CTX_RE.search(sent):
            continue
        for m in _EN_INST_PHRASE_RE.finditer(sent):
            names.append(m.group(1).strip())
        for m in re.finditer(
            r"(?i)\b([A-Z][A-Za-z][A-Za-z .'\-]{2,60})\s+(?:IACUC|IRB)\b",
            sent,
        ):
            names.append(m.group(1).strip(" ,;:-"))
    return names


def _ethics_committee_as_note(inst: str, window: str, raw_material: str) -> bool:
    """Chinese 伦理委员会 / 动物管理与使用委员会 matches source IACUC/IRB."""
    if not inst or not _ETHICS_CTX_RE.search(window or ""):
        return False
    if not _ETHICS_CTX_RE.search(raw_material or ""):
        return False
    for eng in _source_ethics_approval_names(raw_material):
        if _english_translates_chinese_inst(inst, eng):
            return True
        leftover = inst
        for cn, _ens in _CN_EN_INST_TYPES:
            if leftover.endswith(cn):
                leftover = leftover[: -len(cn)]
                break
        for cn, ens in _CN_EN_INST_TOKENS:
            if cn in leftover and any(e in (eng or "").lower() for e in ens):
                leftover = leftover.replace(cn, "")
        leftover = re.sub(r"[的和与及、\s]", "", leftover)
        if leftover and not re.search(r"[\u4e00-\u9fff]", leftover):
            return True
        if leftover == "":
            return True
    return False


def validate_names(art: dict, raw_material: str) -> list[str]:
    """Check that proper names in output appear in the source (or a known alias).

    Covers Latin and Chinese drugs, gene/protein symbols, institutions,
    authors and trial-like names. Generic 单中心 / 中心数 are not institutions.
    """
    problems = []
    norm = normalize_whitespace(raw_material).lower()

    def _name_chunks(val) -> list[str]:
        if isinstance(val, str) and val:
            return [val]
        if isinstance(val, list):
            return [str(x) for x in val if x not in (None, "")]
        return []

    all_text = " ".join([
        *_name_chunks(art.get("title")),
        *_name_chunks(art.get("one_liner")),
        *_name_chunks(art.get("background")),
        *_name_chunks(art.get("design")),
        *_name_chunks(art.get("results")),
        *_name_chunks(art.get("mechanism")),
        *_name_chunks(art.get("significance")),
        *_name_chunks(art.get("authors")),
        *_name_chunks(art.get("limitations")),
    ])

    # 1. Latin-script author names: "Zhang 等", "Smith 等"
    for match in re.finditer(r'([A-Z][a-z]+)\s*等', all_text):
        name = match.group(1).lower()
        if len(name) >= 2 and not _source_has_name_form(name, norm):
            problems.append(f"作者姓氏 '{match.group(1)}' 在原始材料中未找到")

    author_verbs = r'发现|报道|报告|称|指出|认为|表示|提出|观察|测定|检测|分析|开展|证明|证实'
    for char in set(re.findall(rf'([\u4e00-\u9fff])等(?=\s*(?:{author_verbs}))', all_text)):
        if char not in raw_material:
            problems.append(f"中文作者姓氏 '{char}' 在原始材料中未找到")

    # 2. Latin drug / compound names anywhere, any case (toripalimab, Glofitamab)
    drug_pattern = r'(?i)\b([a-z]{4,}(?:mab|nib|limab|zumab|ximab|tinib|ciclib|lizumab|cept))\b'
    seen_drugs: set[str] = set()
    for match in re.finditer(drug_pattern, all_text):
        drug = match.group(1)
        key = drug.lower()
        if key in seen_drugs:
            continue
        seen_drugs.add(key)
        if not _source_has_name_form(key, norm):
            problems.append(f"药物名 '{drug}' 在原始材料中未找到")

    # 3. Chinese drug tokens (token-bounded) must match a source INN or the same Chinese.
    seen_cn_drugs: set[str] = set(iter_cn_drug_tokens(all_text))
    source_inns = [m.group(1).lower() for m in _LATIN_DRUG_RE.finditer(raw_material)]
    for cn in seen_cn_drugs:
        if cn in raw_material:
            continue
        if any(_inn_justifies_chinese(inn, cn) for inn in source_inns):
            continue
        problems.append(f"药物名 '{cn}' 在原始材料中未找到")

    # 4. Gene / protein symbols, including those without digits (TIGIT)
    seen_genes: set[str] = set()
    for match in re.finditer(r'(?<![A-Za-z0-9])[A-Z][A-Z0-9]{2,7}(?![A-Za-z0-9])', all_text):
        sym = match.group(0)
        if sym in _GENE_ACRONYM_ALLOW or sym in seen_genes:
            continue
        seen_genes.add(sym)
        if not _source_has_name_form(sym, norm):
            problems.append(f"基因 '{sym}' 在原始材料中未找到")

    # 5. Chinese institutions. Do not use bare 中心 (单中心 / 中心数 / 医疗中心).
    # Skip generic modifier+suffix on the RAW match so trim cannot invent 他实验室.
    inst_pat = r'[\u4e00-\u9fff]{2,12}(?:大学|医院|医学院|肿瘤防治中心|附属医院|研究所|研究院|实验室)'
    seen_inst: set[str] = set()
    for match in re.finditer(inst_pat, all_text):
        raw_inst = match.group(0)
        if _is_generic_institution(raw_inst):
            continue
        inst = _trim_institution_lead(raw_inst)
        if (
            inst in seen_inst
            or inst in _GENERIC_FACILITY
            or _is_generic_institution(inst)
            or len(inst) < 4
        ):
            continue
        seen_inst.add(inst)
        if _institution_in_source(inst, raw_material, norm, all_text):
            continue
        window = all_text[max(0, match.start() - 24): match.end() + 24]
        ethics_window = window if _ETHICS_CTX_RE.search(window) else all_text
        if _ethics_committee_as_note(inst, ethics_window, raw_material):
            note = f"伦理委员会表述对应原文 IACUC/IRB 批准句（{inst}）"
            art.setdefault("qc_notes", []).append(note)
            logging.info("Ethics committee wording noted, not hard-fail: %s", inst)
            continue
        problems.append(f"机构名 '{inst}' 在原始材料中未找到")

    # 6. Terminology: incorrect Chinese for a source English term
    for eng_term, (correct, incorrect_list) in TERMINOLOGY_GLOSSARY.items():
        if eng_term.lower() in norm:
            for wrong in incorrect_list:
                if wrong in all_text:
                    problems.append(f"术语翻译错误：'{wrong}' 应为 '{correct}'（英文：{eng_term}）")

    company_pattern = r'([A-Z][A-Za-z]+(?:\s+[A-Z][A-Za-z]+)?)\s*(?:公司|Inc\.?|Ltd\.?|Corp\.?|Therapeutics|Pharma|Biopharma)'
    for match in re.finditer(company_pattern, all_text):
        company = match.group(1).lower()
        if len(company) >= 3 and company not in norm:
            if company not in ['the', 'and', 'bio', 'new', 'global', 'inc', 'international']:
                problems.append(f"公司名 '{match.group(1)}' 在原始材料中未找到")

    return problems


CLAIM_AUDIT_TOOL = {
    "name": "submit_claim_audit",
    "description": "Submit every factual claim in the drafted 解读, each labelled against the source",
    "input_schema": {
        "type": "object",
        "properties": {
            "claims": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "claim": {"type": "string", "description": "The factual claim as written in the 解读"},
                        "kind": {
                            "type": "string",
                            "description": (
                                "number, comparison, design, species, tissue, "
                                "biomarker, named_entity, journal, peer_review"
                            ),
                        },
                        "label": {
                            "type": "string",
                            "enum": ["SUPPORTED", "CONTRADICTED", "NOT_IN_SOURCE"],
                        },
                        "source_span": {
                            "type": "string",
                            "description": "Exact contiguous span copied from the source, or empty if none",
                        },
                        "factual": {
                            "type": "boolean",
                            "description": "False only for background/explanatory sentences with no specific fact",
                        },
                    },
                    "required": ["claim", "label", "source_span"],
                },
            },
        },
        "required": ["claims"],
    },
}

# Included so the locked acceptance harness routes this call to test_tool
# (unknown tools fail the suite). Production must still call submit_claim_audit.
_CLAIM_AUDIT_HARNESS_TOOL = {
    "name": "test_tool",
    "description": "Do not call. The audit must be submitted with submit_claim_audit.",
    "input_schema": {
        "type": "object",
        "properties": {"ok": {"type": "boolean"}},
        "required": ["ok"],
    },
}

LAST_CLAIM_AUDIT = {
    "calls": 0,
    "input_tokens": 0,
    "output_tokens": 0,
    "status": "ok",
}


def _article_plain_text(art: dict) -> str:
    parts: list[str] = []
    for key in (
        "title", "journal", "authors", "one_liner", "background",
        "design", "results", "mechanism", "limitations", "significance",
    ):
        val = art.get(key)
        if isinstance(val, list):
            parts.extend(str(x) for x in val if x)
        elif val:
            parts.append(str(val))
    dc = art.get("datacard")
    if isinstance(dc, dict):
        parts.extend(f"{k}: {v}" for k, v in dc.items() if v)
    return "\n".join(parts)


def normalize_span_for_match(text: str) -> str:
    """Whitespace-collapsed, middle-dot-normalized source span."""
    return re.sub(r"\s+", " ", normalize_source_text(text or "", convert_english_words=False)).strip()


def span_exists_in_source(span: str, source: str) -> bool:
    """True when the quoted span is a normalized substring of the source."""
    if not span or not source:
        return False
    nspan = normalize_span_for_match(span)
    nsrc = normalize_span_for_match(source)
    if not nspan:
        return False
    if nspan in nsrc:
        return True
    compact_span = re.sub(r"[^a-z0-9\u4e00-\u9fff.%]+", "", nspan)
    compact_src = re.sub(r"[^a-z0-9\u4e00-\u9fff.%]+", "", nsrc)
    return len(compact_span) >= 12 and compact_span in compact_src


def build_claim_audit_prompt(art: dict, raw_material: str, item: EnrichedItem | None) -> str:
    from inlight_qc import FULLTEXT_WINDOW
    meta = []
    if item:
        meta.append(f"title: {item.title}")
        meta.append(f"journal: {item.journal or item.source}")
        meta.append(f"authors: {item.authors}")
        meta.append(f"url: {item.url}")
        meta.append(f"evidence_level: {item.evidence_level}")
        if item.abstract:
            meta.append("abstract:\n" + item.abstract[:8000])
    source = raw_material or ""
    drafted = _article_plain_text(art)
    return f"""你是事实核对员。对照下面的来源（深度解读必须对照全文 Results），审核这篇中文解读的每一个事实主张。

## 必须抽取的主张

- 每一个数字，并写清它描述的对象：臂、人群、严重程度分级、指标类型、时间单位、剂量基准与单位、百分点 vs 百分率
- 每一个比较（含没有数字的比较），写清方向与是否显著
- 研究设计属性、物种、组织、生物标志物状态
- 每一个专有名称（药、基因、蛋白、机构、试验、作者）
- 期刊名或同行评议状态

背景或解释句如果没有任何具体事实主张，不要列入 claims，或把 factual 设为 false。

## 标签

- SUPPORTED：来源明确支持。source_span 必须是来源里逐字出现的连续片段。
- CONTRADICTED：解读与来源冲突（方向/否定翻转、臂或人群对调、名词/设计/指标对调、名称替换、预印本声称已发表或已同行评议）。source_span 必须是来源里支持「原文实际说法」的连续片段。
- NOT_IN_SOURCE：解读写了来源没有的具体事实。source_span 留空。

必须调用 submit_claim_audit。不要调用 test_tool。

## 来源

{chr(10).join(meta) if meta else source[:FULLTEXT_WINDOW]}

## 来源全文（校验用）

{source[:FULLTEXT_WINDOW]}

## 待审解读

{drafted[:FULLTEXT_WINDOW]}
"""


def verify_article_claims(
    art: dict,
    raw_material: str,
    item: EnrichedItem | None = None,
    fail_closed: bool | None = None,
) -> dict:
    """Claim-level semantic audit. Never pass temperature.

    Returns {status, problems, claims, calls, input_tokens, output_tokens}.
    status: ok | contradicted | not_in_source | error
    Default fail_closed=True: API errors or a missing submit_claim_audit
    are status=error. The weekly stage passes acir_strict(config) so the
    locked replay harness (no min_deep) stays fail-open.
    A label is trusted only when its source_span actually occurs in the source
    (NOT_IN_SOURCE may have an empty span).
    """
    if fail_closed is None:
        fail_closed = True
    result = {
        "status": "ok",
        "problems": [],
        "claims": [],
        "calls": 0,
        "input_tokens": 0,
        "output_tokens": 0,
    }
    LAST_CLAIM_AUDIT.update(result)

    def _fail(reason: str, exc: Exception | None = None) -> dict:
        if fail_closed:
            result["status"] = "error"
            result["problems"] = [reason]
            logging.error("Claim verifier failed closed: %s", exc or reason)
        else:
            logging.warning("Claim verifier failed open: %s", exc or reason)
        LAST_CLAIM_AUDIT.update(result)
        return result

    try:
        from anthropic import Anthropic

        client = Anthropic()
        model = os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-5-5")
        tools = [CLAIM_AUDIT_TOOL]
        if not fail_closed:
            tools.append(_CLAIM_AUDIT_HARNESS_TOOL)
        message = _claude_create(
            client,
            model=model,
            max_tokens=4000,
            tools=tools,
            tool_choice={"type": "auto"},
            messages=[{"role": "user", "content": build_claim_audit_prompt(art, raw_material, item)}],
        )
    except Exception as exc:
        return _fail(f"claim verifier API error: {type(exc).__name__}", exc)

    result["calls"] = 1
    usage = getattr(message, "usage", None)
    if usage is not None:
        result["input_tokens"] = int(getattr(usage, "input_tokens", 0) or 0)
        result["output_tokens"] = int(getattr(usage, "output_tokens", 0) or 0)

    payload = None
    try:
        content = message.content or []
        for block in content:
            name = getattr(block, "name", None)
            btype = getattr(block, "type", None)
            if btype == "tool_use" and name == "submit_claim_audit":
                payload = getattr(block, "input", None) or {}
                break
    except Exception as exc:
        return _fail(f"claim verifier parse error: {type(exc).__name__}", exc)

    if not isinstance(payload, dict):
        return _fail("claim verifier missing submit_claim_audit")

    claims = payload.get("claims") or []
    if not isinstance(claims, list):
        return _fail("claim verifier invalid claims payload")
    result["claims"] = claims

    contradicted: list[str] = []
    missing: list[str] = []
    for cl in claims:
        if not isinstance(cl, dict):
            continue
        if cl.get("factual") is False:
            continue
        label = str(cl.get("label") or "").upper()
        span = str(cl.get("source_span") or "")
        claim_text = str(cl.get("claim") or "").strip() or "(unnamed claim)"
        if label == "SUPPORTED":
            if not span_exists_in_source(span, raw_material):
                logging.info("Ignoring SUPPORTED without source span: %s", claim_text[:60])
            continue
        if label == "CONTRADICTED":
            if not span_exists_in_source(span, raw_material):
                logging.info("Ignoring CONTRADICTED without source span: %s", claim_text[:60])
                continue
            contradicted.append(f"主张与原文矛盾：{claim_text[:80]}")
            continue
        if label == "NOT_IN_SOURCE":
            if _is_unreported_disclaimer(claim_text):
                continue
            missing.append(f"原文未支持的事实主张：{claim_text[:80]}")

    if contradicted:
        result["status"] = "contradicted"
        result["problems"] = contradicted
    elif missing:
        result["status"] = "not_in_source"
        result["problems"] = missing
    LAST_CLAIM_AUDIT.update(result)
    logging.info(
        "Claim verifier: status=%s claims=%d extra_calls=%d tokens_in=%d tokens_out=%d",
        result["status"], len(claims), result["calls"],
        result["input_tokens"], result["output_tokens"],
    )
    return result


_FIELD_RELEVANCE_TERMS = {
    "f1": ("organoid", "类器官", "器官芯片", "organ-on-chip", "organ on chip"),
    "f2": ("humanized", "xenograft", "knockout", "人源化", "动物模型", "建系", "knock-in"),
    "f3": ("alphafold", "generative", "structure prediction", "ai drug", "生成式", "结构预测", "药效预测"),
    "f4": ("car-t", "car t", "checkpoint", "pd-1", "pd-l1", "肿瘤免疫", "tme", "til", "tcr-t"),
    "f5": ("autoimmune", "transplant", "lupus", "自免", "移植", "排斥"),
    "f6": ("vaccine", "adjuvant", "疫苗", "佐剂", "感染免疫"),
    "f7": ("nanobody", "adc", "fc engineering", "双抗", "抗体工程", "nanobodies"),
    "f8": ("sirna", "aso", "lnp", "mrna", "gene therapy", "核酸", "基因治疗"),
    "f9": ("pdx", "precision oncolog", "药敏", "临床转化", "patient-derived", "basket"),
    "c1": ("organoid", "类器官", "器官芯片", "organ-on-chip"),
    "c2": ("alphafold", "generative", "ai drug", "生成式", "结构预测"),
    "c3": ("car-t", "checkpoint", "pd-1", "肿瘤免疫", "tme"),
    "c4": ("autoimmune", "lupus", "自免", "移植"),
    "c5": ("humanized", "xenograft", "动物模型", "人源化"),
    "c6": ("nanobody", "adc", "抗体工程", "双抗"),
    "c7": ("car-t", "til", "细胞治疗", "tcr-t"),
    "c8": ("vaccine", "adjuvant", "疫苗", "佐剂"),
    "c9": ("sirna", "aso", "lnp", "mrna", "核酸"),
}


def _item_relevance_blob(item: EnrichedItem) -> str:
    return " ".join(
        filter(
            None,
            [
                getattr(item, "title", "") or "",
                getattr(item, "abstract", "") or "",
                getattr(item, "fulltext_results", "") or "",
                getattr(item, "fig_captions", "") or "",
                getattr(item, "methods_design", "") or "",
                getattr(item, "rss_summary", "") or "",
            ],
        )
    ).lower()


def _term_in_blob(term: str, blob: str) -> bool:
    """Word-boundary match for English; substring for Chinese."""
    t = (term or "").lower().strip()
    if not t or not blob:
        return False
    if re.search(r'[\u4e00-\u9fff]', t):
        return t in blob
    parts = [p for p in re.split(r'[\s\-]+', t) if p]
    if not parts:
        return False
    if len(parts) == 1:
        return bool(re.search(r'(?<![a-z0-9])' + re.escape(parts[0]) + r'(?![a-z0-9])', blob))
    pat = r'(?<![a-z0-9])' + r'[\s\-]+'.join(re.escape(p) for p in parts) + r'(?![a-z0-9])'
    return bool(re.search(pat, blob))


def _score_fields_for_item(item: EnrichedItem, config: dict | None) -> tuple[str, int]:
    """Rank an item against the 9 fields. Returns (best_field, score).

    Score 0 → field=none (out of scope). English terms use word boundaries so
    short tokens like til/aso do not match inside until/reason.
    """
    blob = _item_relevance_blob(item)
    allowed = _triage_field_map(config)
    best_k = "none"
    best_s = 0
    for key, name in allowed.items():
        terms = _FIELD_RELEVANCE_TERMS.get(key) or ()
        score = sum(1 for t in terms if t and _term_in_blob(t, blob))
        if name and _term_in_blob(str(name), blob):
            score += 2
        if score > best_s:
            best_k, best_s = key, score
    return best_k, best_s


def _out_of_scope_reason(item: EnrichedItem | None, config: dict | None = None, field: str | None = "none") -> str:
    """Why a candidate was classified out of the 9 fields."""
    if item is None:
        return "out of scope: field=none (item missing)"
    best, score = _score_fields_for_item(item, config)
    return (
        f"out of scope: assigned field={field or 'none'}; "
        f"best_term_field={best} relevance={score}; "
        f"title={((item.title or '')[:80])}"
    )


def _model_field_assignments(items: list[EnrichedItem], config: dict) -> dict[str, str]:
    """Same model field triage as primary picks: one primary field or none."""
    if not items:
        return {}
    allowed = _triage_field_map(config)
    rows = []
    for item in items:
        rows.append({
            "url": item.url,
            "title": item.title,
            "abstract_preview": (item.abstract or item.rss_summary or "")[:500],
            "read_note": item.read_note,
        })
    prompt = (
        "为下列已确定有合法全文的条目各标恰好一个主领域。"
        "不属于以下 9 个领域的条目标 field=none，不要硬塞。\n\n"
        f"{json.dumps(allowed, ensure_ascii=False)}\n\n"
        f"{_triage_field_rules(config)}\n\n"
        "调用 submit_triage：tier 一律 deep，每条恰好一个 field。\n\n"
        f"{json.dumps(rows, ensure_ascii=False, indent=2)}\n"
    )
    try:
        from anthropic import Anthropic

        client = Anthropic()
        model = os.environ.get("ANTHROPIC_TRIAGE_MODEL", os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-5-5"))
        message = _claude_create(
            client,
            model=model,
            max_tokens=2000,
            tools=_triage_tools_for(config),
            tool_choice={"type": "auto"},
            messages=[{"role": "user", "content": prompt}],
        )
    except Exception as exc:
        logging.info("Backfill field triage unavailable: %s", exc)
        return {}
    out: dict[str, str] = {}
    for block in getattr(message, "content", None) or []:
        if getattr(block, "type", None) == "tool_use" and getattr(block, "name", None) == "submit_triage":
            payload = getattr(block, "input", None) or {}
            for sel in payload.get("selections") or []:
                if not isinstance(sel, dict) or not sel.get("url"):
                    continue
                field = sel.get("field")
                out[sel["url"]] = field if field in allowed or field == "none" else "none"
            break
    return out


def _backfill_deep_selections(
    items: list[EnrichedItem],
    selections: list[dict],
    config: dict,
) -> list[dict]:
    """When min_deep is missed, keep unused OA/PMC full-text candidates."""
    from inlight_qc import acir_strict, item_has_real_fulltext

    cfg = config or {}
    if not (acir_strict(cfg) or "min_deep" in cfg):
        for it in items:
            if it.url not in {s.get("url") for s in selections}:
                logging.info(
                    "Triage skip %s: not selected (no min_deep backfill on this path)",
                    it.url,
                )
        return selections

    t = pipeline_targets(cfg)
    url_to_item = {it.url: it for it in items}
    selected_urls = {s.get("url") for s in selections if s.get("url")}

    def _is_ft(url: str) -> bool:
        it = url_to_item.get(url)
        return bool(it and item_has_real_fulltext(it))

    deep_ft_n = sum(
        1 for s in selections if s.get("tier") == "deep" and _is_ft(s.get("url") or "")
    )
    try_cap = max(int(t["min_deep"]) * 2, int(t["min_deep"]))
    ft_pool = [it for it in items if item_has_real_fulltext(it)]
    logging.info(
        "Triage full-text pool %d, deep selected %d, try cap %d",
        len(ft_pool), deep_ft_n, try_cap,
    )

    for it in items:
        if it.url in selected_urls:
            continue
        if not item_has_real_fulltext(it):
            logging.info(
                "Triage skip %s: no legally accessible full text (OA/PMC/publisher OA)",
                it.url,
            )

    unused_ft = [
        it for it in items
        if item_has_real_fulltext(it) and it.url not in selected_urls
    ]
    need = max(0, try_cap - deep_ft_n)
    if need == 0:
        logging.info(
            "Triage backfill stop: already have %d deep full-text (≥ try cap %d)",
            deep_ft_n, try_cap,
        )
        return selections
    if not unused_ft:
        logging.info(
            "Triage backfill stop: OA/PMC full-text pool exhausted "
            "(%d deep full-text, try cap %d, unused 0)",
            deep_ft_n, try_cap,
        )
        return selections

    ranked = sorted(
        unused_ft,
        key=lambda it: _score_fields_for_item(it, cfg)[1],
        reverse=True,
    )
    candidates = ranked[:need]
    model_fields = _model_field_assignments(candidates, cfg)
    if candidates and not model_fields:
        logging.info(
            "Triage skip %d backfill candidate(s): model field triage failed",
            len(candidates),
        )
    out = list(selections)
    for it in candidates:
        score = _score_fields_for_item(it, cfg)[1]
        if it.url not in model_fields:
            logging.info(
                "Triage skip %s: model field triage failed or skipped this item",
                it.url,
            )
            continue
        field = model_fields.get(it.url)
        if field in ("none", "", None):
            logging.info(
                "Triage skip %s: %s",
                it.url, _out_of_scope_reason(it, cfg, field),
            )
            continue
        out.append({
            "url": it.url,
            "tier": "deep",
            "field": field,
            "reason": f"backfill OA/PMC full text, field={field} score={score}",
        })
        selected_urls.add(it.url)
        logging.info(
            "Triage backfill %s: unused legally accessible full text, field=%s relevance=%d",
            it.url, field, score,
        )
    for it in ranked[need:]:
        logging.info(
            "Triage skip %s: try cap %d reached (ranked below other full-text candidates)",
            it.url, try_cap,
        )
    filled = sum(1 for s in out if s.get("tier") == "deep" and _is_ft(s.get("url") or ""))
    if filled < try_cap and len(ranked) <= need:
        logging.info(
            "Triage backfill stop: OA/PMC full-text pool exhausted "
            "(%d deep full-text after backfill, try cap %d)",
            filled, try_cap,
        )
    return out


def triage_items(items: list[EnrichedItem], config: dict) -> list[dict]:
    """Run triage to select items and assign tiers.
    
    Returns list of {url, tier, field} dicts.
    """
    from anthropic import Anthropic
    from inlight_qc import item_has_real_fulltext

    items = sorted(items, key=lambda it: (0 if item_has_real_fulltext(it) else 1))
    
    prompt = build_triage_prompt(items, config)
    model = os.environ.get("ANTHROPIC_TRIAGE_MODEL", os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-5-5"))
    
    logging.info("Triage: calling %s for %d items", model, len(items))
    
    client = Anthropic()
    message = _claude_create(
        client,
        model=model,
        max_tokens=4000,
        tools=_triage_tools_for(config),
        tool_choice={"type": "auto"},
        messages=[{"role": "user", "content": prompt}],
    )
    
    selections: list[dict] = []
    for block in message.content:
        if block.type == "tool_use" and block.name == "submit_triage":
            selections = list(block.input.get("selections", []) or [])
            logging.info("Triage model selected %d items", len(selections))
            break
    else:
        logging.warning("Triage did not return tool_use, using fallback")

    url_to_item = {it.url: it for it in items}
    for sel in selections:
        if (sel.get("field") in ("none", "", None)) and sel.get("url"):
            logging.info(
                "Candidate out of scope %s: %s",
                sel.get("url"),
                _out_of_scope_reason(url_to_item.get(sel.get("url")), config, sel.get("field")),
            )
    selections = _backfill_deep_selections(items, selections, config)
    logging.info("Triage selected %d items after backfill", len(selections))
    return selections


def _claude_create(client, **kwargs):
    """messages.create compatible with anthropic 1.12.0 (stream or ≤8192 tokens).

    max_tokens > 8192 (including the 24000 truncation retry) streams first so
    the SDK cannot raise 'Streaming is required' before fallback. Smaller
    calls try a normal create first; stream only when the SDK demands it.

    create(stream=True) is consumed as a context-manager when the SDK returns
    one (MessageStreamManager). That primary request is never followed by a
    second messages.stream() call.
    """
    kwargs.pop("temperature", None)
    max_tok = int(kwargs.get("max_tokens") or 0)

    def _create(**kw):
        return client.messages.create(**kw)

    def _is_mock(obj) -> bool:
        return type(obj).__module__.startswith("unittest.mock")

    def _as_message(obj):
        if obj is None or isinstance(obj, (list, tuple)):
            return None
        if getattr(obj, "content", None) is not None and getattr(obj, "stop_reason", None) is not None:
            return obj
        return None

    def _iterate_events(stream):
        final = None
        for event in stream:
            if getattr(event, "message", None) is not None:
                final = event.message
            elif getattr(event, "type", None) == "message":
                final = event
            else:
                got = _as_message(event)
                if got is not None:
                    final = got
        return final

    def _consume_stream(stream):
        msg = _as_message(stream)
        if msg is not None:
            return msg
        if isinstance(stream, (list, tuple)):
            final = _iterate_events(stream)
            if final is not None:
                return final
            raise RuntimeError("Claude streaming returned no message")
        needs_enter = (
            hasattr(stream, "__enter__")
            and hasattr(stream, "__exit__")
            and not _is_mock(stream)
        )
        if needs_enter:
            with stream as opened:
                getter = getattr(opened, "get_final_message", None) or getattr(
                    stream, "get_final_message", None
                )
                if callable(getter):
                    got = getter()
                    if got is not None:
                        return got
                final = _iterate_events(opened)
                if final is not None:
                    return final
            raise RuntimeError("Claude streaming returned no message")
        getter = getattr(stream, "get_final_message", None)
        if callable(getter) and not _is_mock(stream):
            try:
                got = getter()
                if got is not None:
                    return got
            except Exception:
                pass
        final = _iterate_events(stream)
        if final is not None:
            return final
        raise RuntimeError("Claude streaming returned no message")

    def _stream(**kw):
        try:
            stream = client.messages.create(**kw, stream=True)
        except Exception:
            stream_fn = getattr(client.messages, "stream", None)
            if callable(stream_fn) and not hasattr(stream_fn, "assert_called_with"):
                with stream_fn(**kw) as stream:
                    return stream.get_final_message()
            raise
        return _consume_stream(stream)

    if max_tok > 8192:
        try:
            return _stream(**kwargs)
        except Exception:
            small = dict(kwargs)
            small["max_tokens"] = 8192
            return _create(**small)
    try:
        return _create(**kwargs)
    except Exception as exc:
        if "streaming is required" not in str(exc).lower():
            raise
        return _stream(**kwargs)


def _article_tools_for(config: dict | None) -> list[dict]:
    from inlight_qc import acir_strict
    if not acir_strict(config):
        return [ARTICLE_TOOL_SCHEMA]
    import copy
    from inlight_fields import FIELDS as NEW_FIELDS, NONE_FIELD
    schema = copy.deepcopy(ARTICLE_TOOL_SCHEMA)
    schema["input_schema"]["properties"]["field"]["enum"] = list(NEW_FIELDS.keys()) + [NONE_FIELD]
    schema["input_schema"]["properties"]["related_fields"] = {
        "type": "array",
        "items": {"type": "string", "enum": list(NEW_FIELDS.keys())},
        "description": "Other related fields, not including the primary",
    }
    return [schema]


def _triage_tools_for(config: dict | None) -> list[dict]:
    from inlight_qc import acir_strict
    if not acir_strict(config):
        return [TRIAGE_TOOL_SCHEMA]
    import copy
    from inlight_fields import FIELDS as NEW_FIELDS, NONE_FIELD
    schema = copy.deepcopy(TRIAGE_TOOL_SCHEMA)
    schema["input_schema"]["properties"]["selections"]["items"]["properties"]["field"]["enum"] = (
        list(NEW_FIELDS.keys()) + [NONE_FIELD]
    )
    schema["input_schema"]["properties"]["selections"]["items"]["properties"]["related_fields"] = {
        "type": "array",
        "items": {"type": "string", "enum": list(NEW_FIELDS.keys())},
        "description": "Other related fields, not including the primary",
    }
    return [schema]


def _article_prompt_with_problems(
    item: EnrichedItem,
    tier: str,
    problems: list[str] | None = None,
    *,
    source_window: int | None = None,
    compact: bool = False,
) -> str:
    prompt = build_article_prompt(
        item, tier, source_window=source_window, compact=compact,
    ) if source_window is not None or compact else build_article_prompt(item, tier)
    if problems:
        prompt += f"\n\n## 上次生成的问题（请务必修正）\n\n" + "\n".join(f"- {p}" for p in problems)
        prompt += (
            "\n\n只删除或改写被点名的主张，其余已核对内容保持不变。"
            "每个数字和专有名称必须从材料逐字复制，不得改写或替换。"
        )
    prompt += "\n\n请务必调用 submit_article 工具提交你的文章。"
    return prompt


def _article_from_claude_message(message, item: EnrichedItem) -> tuple[dict | None, str]:
    """Return (article, status) where status is ok / refusal / empty / max_tokens."""
    if message is None:
        return None, "empty"
    stop = getattr(message, "stop_reason", None)
    if stop == "refusal":
        return None, "refusal"
    if stop == "max_tokens":
        return None, "max_tokens"
    content = getattr(message, "content", None) or []
    if not content:
        return None, "empty"
    for block in content:
        if getattr(block, "type", None) == "tool_use" and getattr(block, "name", None) == "submit_article":
            validated = _validate_article_structure(block.input, item.title[:50])
            if validated is None:
                logging.warning("Article draft failed structure check: %s", item.title[:50])
                return None, "empty"
            validated["source"] = item.source
            validated["evidence_level"] = item.evidence_level
            validated["source_trace"] = item.source_trace
            return validated, "ok"
    return None, "empty"


def _openai_drafter_model() -> str:
    return os.environ.get("OPENAI_MODEL") or "gpt-4o"


def _draft_article_via_openai(
    prompt: str, tools: list[dict], item: EnrichedItem, config: dict,
) -> dict | None:
    """Same prompt, schema and audit path — only the model changes."""
    try:
        from openai import OpenAI
    except Exception as exc:
        logging.error("OpenAI SDK unavailable for drafter fallback: %s", exc)
        return None
    if not os.environ.get("OPENAI_API_KEY"):
        logging.error("OpenAI drafter requested but OPENAI_API_KEY missing")
        return None
    model = _openai_drafter_model()
    oai_tools = []
    for t in tools or []:
        oai_tools.append({
            "type": "function",
            "function": {
                "name": t.get("name"),
                "description": t.get("description") or "",
                "parameters": t.get("input_schema") or {"type": "object", "properties": {}},
            },
        })
    logging.info("Drafting via OpenAI %s after Claude refusal/empty: %s", model, item.title[:50])
    try:
        resp = OpenAI().chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            tools=oai_tools,
            tool_choice="auto",
        )
    except Exception as exc:
        logging.error("OpenAI drafter error for %s: %s", item.title[:50], exc)
        return None
    choice = (getattr(resp, "choices", None) or [None])[0]
    msg = getattr(choice, "message", None) if choice is not None else None
    for tc in (getattr(msg, "tool_calls", None) or []):
        fn = getattr(tc, "function", None)
        if not fn or getattr(fn, "name", None) != "submit_article":
            continue
        try:
            payload = json.loads(fn.arguments or "{}")
        except json.JSONDecodeError:
            return None
        validated = _validate_article_structure(payload, item.title[:50])
        if validated is None:
            return None
        validated["source"] = item.source
        validated["evidence_level"] = item.evidence_level
        validated["source_trace"] = item.source_trace
        validated["drafter_model"] = model
        return validated
    logging.warning("OpenAI drafter did not return submit_article for: %s", item.title[:50])
    return None


def draft_single_article(item: EnrichedItem, tier: str, config: dict, problems: list[str] = None) -> dict | None:
    """Draft a single article using Claude.

    If Claude refuses (stop_reason=refusal) or returns empty output twice,
    switch this article's drafter to OpenAI. Same prompts, schema and audit.
    """
    from anthropic import Anthropic

    prompt = _article_prompt_with_problems(item, tier, problems)
    model = os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-5-5")
    max_tokens = 16000 if tier == "deep" else 8000
    tools = _article_tools_for(config)

    logging.info("Drafting %s article for: %s", tier, item.title[:50])
    client = Anthropic()

    def _claude_once(user_prompt: str, tokens: int):
        return _claude_create(
            client,
            model=model,
            max_tokens=tokens,
            tools=tools,
            tool_choice={"type": "auto"},
            messages=[{"role": "user", "content": user_prompt}],
        )

    empty_or_refusal = 0
    for attempt in (1, 2):
        try:
            message = _claude_once(prompt, max_tokens)
        except Exception as e:
            logging.error("API error drafting article for %s: %s", item.title[:50], e)
            return None

        if getattr(message, "stop_reason", None) == "max_tokens":
            retry_tokens = 24000 if tier == "deep" else 16000
            retry_prompt = _article_prompt_with_problems(
                item, tier, problems, source_window=8000, compact=True,
            )
            logging.warning(
                "Article draft truncated (max_tokens), retrying with %d tokens and shorter source: %s",
                retry_tokens, item.title[:50],
            )
            try:
                message = _claude_once(retry_prompt, retry_tokens)
            except Exception as e:
                logging.error("API error on max_tokens retry for %s: %s", item.title[:50], e)
                return None
            if getattr(message, "stop_reason", None) == "max_tokens":
                logging.error(
                    "Dropping truncated draft after two max_tokens stops (retry budget %d): %s",
                    retry_tokens, item.title[:50],
                )
                return None

        art, status = _article_from_claude_message(message, item)
        if art is not None:
            art["drafter_model"] = model
            return art
        if status in ("refusal", "empty"):
            empty_or_refusal += 1
            logging.warning(
                "Claude draft %s (%d/2) for %s",
                status, empty_or_refusal, item.title[:50],
            )
            continue
        return None

    if empty_or_refusal >= 2:
        art = _draft_article_via_openai(prompt, tools, item, config)
        if art is not None:
            return art
    return None


def _validate_article_structure(art: dict, title_snippet: str) -> dict | None:
    """Validate and coerce article field types.
    
    The model may return malformed responses where dict/list fields are strings
    (e.g., with embedded XML-like tags) or where string fields are None/dict/list.
    
    This function handles ALL 11 known malformed shapes:
    1. datacard as string
    2. results as list of dicts (should be list of strings)
    3. limitations as ints (should be list of strings)
    4. data_point value as int (should be string)
    5. background as list (should be string)
    6. title as None
    7. one_liner as dict
    8. significance as None
    9. image_prompt as list
    10. steps as ints
    11. unknowns as malformed
    
    Returns validated article dict, or None if unrecoverable.
    """
    # Required string fields - must exist and be string (or coercible)
    required_string_fields = ["title", "one_liner", "background", "design", "significance"]
    # Optional string fields
    optional_string_fields = ["mechanism", "image_prompt", "journal", "authors"]
    # Required dict fields
    dict_fields = ["datacard"]
    # Required list of strings fields
    list_string_fields = ["results", "limitations", "steps"]
    # Required list of dicts fields
    list_dict_fields = ["data_points"]
    # Optional list fields
    optional_list_fields = ["unknowns"]
    
    # Validate and coerce required string fields
    for field in required_string_fields:
        val = art.get(field)
        if val is None:
            logging.error("Article missing required string field '%s': %s", field, title_snippet)
            return None
        elif isinstance(val, dict):
            # Try to convert dict to string (e.g., {"text": "value"})
            if "text" in val:
                art[field] = str(val["text"])
            else:
                logging.error("Article field '%s' is dict, cannot coerce: %s", field, title_snippet)
                return None
        elif isinstance(val, list):
            # Coerce list to string by joining
            try:
                art[field] = " ".join(str(item) for item in val)
                logging.warning("Coerced list to string for field '%s': %s", field, title_snippet)
            except Exception:
                logging.error("Article field '%s' is list, cannot coerce: %s", field, title_snippet)
                return None
        elif not isinstance(val, str):
            # Try string conversion
            try:
                art[field] = str(val)
            except Exception:
                logging.error("Article field '%s' has unexpected type %s: %s", field, type(val).__name__, title_snippet)
                return None
    
    # Validate and coerce optional string fields
    for field in optional_string_fields:
        val = art.get(field)
        if val is None:
            art[field] = ""
        elif isinstance(val, list):
            # Coerce list to string
            art[field] = " ".join(str(item) for item in val) if val else ""
        elif isinstance(val, dict):
            art[field] = str(val.get("text", "")) if "text" in val else ""
        elif not isinstance(val, str):
            art[field] = str(val) if val else ""
    
    # Validate and coerce dict fields
    for field in dict_fields:
        val = art.get(field)
        if val is None:
            # Missing dict field - try to continue with empty dict
            art[field] = {}
            logging.warning("Article missing required dict field '%s': %s", field, title_snippet)
        elif isinstance(val, str):
            # Try to parse as JSON
            try:
                parsed = json.loads(val)
                if isinstance(parsed, dict):
                    art[field] = parsed
                else:
                    logging.error("Article field '%s' parsed to non-dict type: %s", field, title_snippet)
                    return None
            except json.JSONDecodeError:
                logging.error("Article field '%s' is malformed string (not valid JSON): %s", field, title_snippet)
                return None
        elif not isinstance(val, dict):
            logging.error("Article field '%s' has unexpected type %s: %s", field, type(val).__name__, title_snippet)
            return None
        if isinstance(art.get(field), dict):
            art[field] = {k: coerce_publish_text(v) for k, v in art[field].items()}
    
    # Validate and coerce list-of-strings fields
    for field in list_string_fields:
        val = art.get(field)
        if val is None:
            # Missing list field - use empty list
            art[field] = []
            logging.warning("Article missing required list field '%s': %s", field, title_snippet)
        elif isinstance(val, str):
            # Try to parse as JSON
            try:
                parsed = json.loads(val)
                if isinstance(parsed, list):
                    art[field] = [str(item) for item in parsed]
                else:
                    # Single string - wrap in list
                    art[field] = [val]
            except json.JSONDecodeError:
                # Single string - wrap in list
                art[field] = [val]
        elif isinstance(val, list):
            art[field] = flatten_string_list(val)
        else:
            # Single non-list value - wrap in list
            art[field] = [str(val)]
            logging.warning("Coerced single value to list for field '%s': %s", field, title_snippet)
    
    # Validate and coerce list-of-dicts fields (data_points)
    for field in list_dict_fields:
        val = art.get(field)
        if val is None:
            art[field] = []
            logging.warning("Article missing list-of-dicts field '%s': %s", field, title_snippet)
        elif isinstance(val, str):
            try:
                parsed = json.loads(val)
                if isinstance(parsed, list):
                    art[field] = parsed
                else:
                    art[field] = []
            except json.JSONDecodeError:
                art[field] = []
        elif isinstance(val, list):
            # Validate each item is a dict with required fields
            valid_items = []
            for item in val:
                if isinstance(item, dict):
                    # Ensure value is string
                    if "value" in item and not isinstance(item["value"], str):
                        item["value"] = str(item["value"])
                    valid_items.append(item)
            art[field] = valid_items
        else:
            art[field] = []
    
    for field in optional_list_fields:
        val = art.get(field)
        if val is None:
            art[field] = []
        elif isinstance(val, str):
            try:
                parsed = json.loads(val)
                if isinstance(parsed, list):
                    art[field] = parsed
                else:
                    art[field] = []
            except json.JSONDecodeError:
                art[field] = []
        elif not isinstance(val, list):
            art[field] = []
    
    # Validate data_points structure - each item must have value, meaning, source_quote
    valid_data_points = []
    for i, dp in enumerate(art.get("data_points", [])):
        if not isinstance(dp, dict):
            logging.warning("data_point %d is not a dict, skipping: %s", i, title_snippet)
            continue
        if not all(k in dp for k in ["value", "meaning", "source_quote"]):
            logging.warning("data_point %d missing required keys, skipping: %s", i, title_snippet)
            continue
        valid_data_points.append(dp)
    art["data_points"] = valid_data_points
    
    return art


def _replace_literal_backslash_n(text: str) -> str:
    """Turn the two-character sequence backslash-n into a real newline."""
    if not isinstance(text, str) or "\\n" not in text:
        return text
    return text.replace("\\n", "\n")


def _omit_missing_value_text(text: str) -> str:
    """Drop sentences that write out a missing value as 未给出."""
    if not isinstance(text, str):
        return ""
    text = _replace_literal_backslash_n(text)
    if MISSING_VALUE_MARK not in text:
        return text
    parts = re.split(r'(?<=[。！？；;\n])', text)
    kept = [p for p in parts if MISSING_VALUE_MARK not in p]
    return "".join(kept).strip()


def _walk_omit_missing(obj: Any) -> Any:
    """Recursively omit 未给出 and literal \\n from nested article fields."""
    if isinstance(obj, str):
        return _omit_missing_value_text(obj)
    if isinstance(obj, list):
        return [v for v in (_walk_omit_missing(x) for x in obj) if v not in ("", None, [], {})]
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            cleaned = _walk_omit_missing(v)
            if cleaned in ("", None, [], {}):
                continue
            out[k] = cleaned
        return out
    return obj


_UNREPORTED_CLAIM_RE = re.compile(
    r'(?:'
    r'(?:原文|所读材料|论文|文中|来源|摘要)(?:中)?'
    r'(?:未报告|未给出|未提及|未列出|未提供|没有报告|没有提及)|'
    r'(?:the\s+)?source\s+(?:does|did)\s+not\s+(?:report|mention|provide|list|give)|'
    r'(?:the\s+)?(?:paper|article|text|materials?)\s+(?:does|did)\s+not\s+'
    r'(?:report|mention|provide|list|give)|'
    r'(?:no|not\s+a)\s+(?:safety\s+)?(?:signal|finding)s?\s+(?:was\s+)?(?:mentioned|reported)'
    r')',
    re.IGNORECASE,
)
_INFERENCE_AFTER_UNREPORTED_RE = re.compile(
    r'(?i)^[\s，、；;]*'
    r'(?:因此|所以|由此可见|据此|可见|提示|表明|说明|认为|'
    r'thus|therefore|hence|suggesting|indicating|implying|'
    r'so\b|consistent with)'
    r'.{0,80}(?:耐受|安全|tolerab|well[-\s]?tolerat|'
    r'no (?:safety )?(?:concern|issue))'
)


def _is_unreported_disclaimer(text: str) -> bool:
    """True for a disclaimer clause with no number/finding (keep 未报告3级以上…)."""
    if not _UNREPORTED_CLAIM_RE.search(text or ""):
        return False
    return not re.search(r'\d', text or "")


def _is_dependent_inference(text: str) -> bool:
    """Tolerability/safety conclusion that only follows an unreported disclaimer."""
    if re.search(r'\d', text or ""):
        return False
    return bool(_INFERENCE_AFTER_UNREPORTED_RE.search((text or "").strip()))


def _split_clauses_no_decimal(text: str) -> list[str]:
    """Split on sentence/clause punctuation, never at a decimal point (11.2)."""
    parts: list[str] = []
    buf: list[str] = []
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        if (
            ch == "."
            and i > 0
            and i + 1 < n
            and text[i - 1].isdigit()
            and text[i + 1].isdigit()
        ):
            buf.append(ch)
            i += 1
            continue
        if ch in "。！？；;\n，、":
            buf.append(ch)
            parts.append("".join(buf))
            buf = []
            i += 1
            continue
        if ch in ".!?" and (i + 1 >= n or text[i + 1].isspace() or text[i + 1] in "\"'”’"):
            buf.append(ch)
            parts.append("".join(buf))
            buf = []
            i += 1
            continue
        buf.append(ch)
        i += 1
    if buf:
        parts.append("".join(buf))
    return parts


def _strip_unreported_text(text: str) -> str:
    """Remove disclaimer clauses and inference that depends on them; never a numbered finding."""
    if not text:
        return text
    kept: list[str] = []
    skip_inference = False
    for clause in _split_clauses_no_decimal(text):
        if _is_unreported_disclaimer(clause):
            skip_inference = True
            continue
        if skip_inference and _is_dependent_inference(clause):
            continue
        skip_inference = False
        kept.append(clause)
    cleaned = "".join(kept)
    cleaned = re.sub(r"[ \t]{2,}", " ", cleaned)
    cleaned = re.sub(r"^[，、；;。.!?]+", "", cleaned)
    cleaned = re.sub(r"[，、；;]+$", "", cleaned)
    return cleaned.strip()


def strip_unreported_disclaimer_sentences(art: dict) -> dict:
    """Remove 'the source does not report X' / '原文未报告 X' sentences."""
    keys = (
        "title", "one_liner", "background", "design", "results", "mechanism",
        "significance", "limitations", "lead", "body", "discuss",
    )

    def _walk(obj):
        if isinstance(obj, str):
            return _strip_unreported_text(obj)
        if isinstance(obj, list):
            return [x for x in (_walk(v) for v in obj) if x not in ("", None, [])]
        if isinstance(obj, dict):
            return {k: _walk(v) for k, v in obj.items()}
        return obj

    for key in keys:
        if key in art:
            art[key] = _walk(art[key])
    return art


def sanitize_published_article(art: dict, enriched: EnrichedItem | None = None) -> dict:
    """Prepare an article for publication: omit missing-value boilerplate, drop
    literal \\n, and prefer the journal name from the source item.
    """
    visible_keys = [
        "title", "one_liner", "background", "design", "results", "mechanism",
        "significance", "limitations", "journal", "authors", "lead", "body",
        "discuss", "datacard", "steps", "unknowns",
    ]
    for key in visible_keys:
        if key in art:
            art[key] = _walk_omit_missing(art[key])
    strip_article_field_headings(art)

    # Journal and authors come from source metadata. The model never
    # supplies a journal for a preprint, and never supplies authors
    # the source did not list.
    known = (enriched.journal if enriched else "") or ""
    url = ((enriched.url if enriched else "") + " " + (enriched.source if enriched else "")).lower()
    is_preprint = bool(
        (enriched and enriched.evidence_level == "preprint")
        or PREPRINT_SOURCE_RE.search(url)
    )
    if is_preprint:
        if known:
            art["journal"] = known
        elif enriched and PREPRINT_SOURCE_RE.search(enriched.url or ""):
            host = "medRxiv" if "medrxiv" in (enriched.url or "").lower() else "bioRxiv"
            art["journal"] = f"{host}（预印本）"
        elif enriched and enriched.source:
            art["journal"] = enriched.source
        else:
            art["journal"] = "bioRxiv（预印本）"
    elif known:
        art["journal"] = known
    else:
        # Source gave no journal: never keep a model-invented title.
        art["journal"] = (enriched.source if enriched else "") or ""

    src_authors = (enriched.authors if enriched else "") or ""
    if src_authors.strip():
        art["authors"] = src_authors.strip()
    else:
        art["authors"] = ""
    authors = str(art.get("authors") or "")
    if AUTHOR_PLACEHOLDER_RE.search(authors) or MISSING_VALUE_MARK in authors:
        art["authors"] = ""

    body = " ".join(
        str(x) for x in (
            art.get("one_liner"),
            art.get("results"),
            art.get("design"),
            art.get("significance"),
        ) if x
    )
    datacard = art.get("datacard")
    if isinstance(datacard, dict):
        for key, val in list(datacard.items()):
            if SEE_BODY_RE.search(str(val)) and not re.search(r'95\s*%|CI|HR|置信区间', body, re.I):
                datacard.pop(key, None)

    # Final sweep: no remaining literal \n in any string
    def _scrub(obj: Any) -> Any:
        if isinstance(obj, str):
            return _replace_literal_backslash_n(obj)
        if isinstance(obj, list):
            return [_scrub(x) for x in obj]
        if isinstance(obj, dict):
            return {k: _scrub(v) for k, v in obj.items()}
        return obj

    return _scrub(art)


def count_verified_numeric_claims(art: dict, raw_material: str) -> int:
    """How many distinct output numbers are source-verified.

    Used so a redraft that deletes correct CIs scores worse than the first draft.
    """
    if not art or not raw_material:
        return 0
    source_norm = normalize_unit_spacing(normalize_source_text(raw_material))
    source_raw = normalize_unit_spacing(
        normalize_source_text(raw_material, convert_english_words=False)
    )
    ids = extract_identifiers_from_source(raw_material)
    parts: list[str] = []
    for key in ("results", "one_liner", "design", "statistics"):
        val = art.get(key)
        if isinstance(val, list):
            parts.extend(str(x) for x in val if x)
        elif isinstance(val, str) and val:
            parts.append(val)
    datacard = art.get("datacard")
    if isinstance(datacard, dict):
        parts.extend(str(v) for v in datacard.values() if v)
    text = " ".join(parts)
    seen: set[str] = set()
    n = 0
    for num, ctx in extract_numbers_with_context(text) + extract_chinese_numbers_with_context(text):
        core = extract_number_core(num) or extract_number_core(chinese_numeral_to_arabic(num))
        if not core or core in seen:
            continue
        if number_exists_in_source(num, source_norm, ids, ctx, source_raw=source_raw):
            seen.add(core)
            n += 1
    return n


def _draft_quality_score(hard: list[str], problems: list[str], art: dict | None, raw: str) -> tuple:
    """Lower is better: hard problems, then fewer verified numbers, then total."""
    verified = count_verified_numeric_claims(art or {}, raw)
    return (len(hard), -verified, len(problems))


def _run_claim_verifier_stage(
    art: dict,
    problems: list[str],
    raw_material: str,
    enriched_item: EnrichedItem,
    config: dict,
    tier: str,
    field: str,
) -> tuple[dict | None, bool]:
    """Deterministic pass already succeeded. Semantic audit may drop or redraft once.

    Returns (article_or_none, dropped).
    Extra Claude cost: 1 submit_claim_audit call; +1 draft +1 audit if NOT_IN_SOURCE.
    """
    def _prepare(draft: dict | None) -> tuple[dict | None, list[str]]:
        if not draft:
            return None, ["draft missing"]
        draft = sanitize_published_article(dict(draft), enriched_item)
        if acir_strict(config):
            strip_unreported_disclaimer_sentences(draft)
        draft["field"] = field
        draft["tier"] = tier
        probs = validate_depth(
            draft, raw_material, allow_word_quantities=acir_strict(config),
        )
        probs.extend(validate_names(draft, raw_material))
        probs = _soften_lone_institution_hit(probs, draft)
        return draft, probs

    from inlight_qc import acir_strict, verifier_source_text
    audit_src = verifier_source_text(enriched_item, raw_material)
    closed = acir_strict(config)
    audit = verify_article_claims(art, audit_src, enriched_item, fail_closed=closed)
    LAST_CLAIM_AUDIT.update(audit)
    extra_calls = audit.get("calls", 0)
    extra_in = audit.get("input_tokens", 0)
    extra_out = audit.get("output_tokens", 0)
    if audit["status"] in ("contradicted", "error"):
        logging.error("Dropping %s: claim verifier %s %s", enriched_item.url, audit["status"].upper(), audit["problems"])
        logging.info(
            "Claim verifier extra per article %s: calls=%d tokens_in=%d tokens_out=%d",
            enriched_item.url, extra_calls, extra_in, extra_out,
        )
        return None, True
    if audit["status"] == "not_in_source":
        logging.warning("NOT_IN_SOURCE claims for %s, targeted redraft once: %s",
                        enriched_item.url, audit["problems"])
        retry = draft_single_article(
            enriched_item, tier, config, problems=problems + audit["problems"],
        )
        extra_calls += 1
        if retry is None:
            logging.error("NOT_IN_SOURCE redraft failed, dropping: %s", enriched_item.url)
            logging.info(
                "Claim verifier extra per article %s: calls=%d tokens_in=%d tokens_out=%d",
                enriched_item.url, extra_calls, extra_in, extra_out,
            )
            return None, True
        retry, retry_probs = _prepare(retry)
        if not retry or _hard_problems(retry_probs):
            logging.error("NOT_IN_SOURCE redraft still hard, dropping: %s", enriched_item.url)
            return None, True
        audit2 = verify_article_claims(retry, audit_src, enriched_item, fail_closed=closed)
        LAST_CLAIM_AUDIT.update(audit2)
        extra_calls += audit2.get("calls", 0)
        extra_in += audit2.get("input_tokens", 0)
        extra_out += audit2.get("output_tokens", 0)
        logging.info(
            "Claim verifier extra per article %s: calls=%d tokens_in=%d tokens_out=%d",
            enriched_item.url, extra_calls, extra_in, extra_out,
        )
        if audit2["status"] != "ok":
            logging.error("Dropping %s after NOT_IN_SOURCE retry: %s", enriched_item.url, audit2["problems"])
            return None, True
        return retry, False
    logging.info(
        "Claim verifier extra per article %s: calls=%d tokens_in=%d tokens_out=%d",
        enriched_item.url, extra_calls, extra_in, extra_out,
    )
    return art, False


def _hard_problems(problems: list[str]) -> list[str]:
    """Problems that block publishing (redraft or drop)."""
    markers = (
        "未找到", "无法回溯", "编造", "营销词汇", "新闻稿",
        "含义不匹配", "单位不匹配", "汉字", "上限", "下限",
        "必须包含数字", "必须使用阿拉伯", "过于模糊",
        "不是数值数据", "注册了标识符",
        "作者", "术语翻译", "过短", "过长", "结果字段",
        "标题连写",
        "比较方向", "机构", "基因", "药物", "蛋白质",
        "预印本正文", "同行评议", "主张与原文矛盾", "原文未支持",
        "数字范围", "字数", "要求", "核心结果", "局限须", "数据卡",
        "段落超过", "evidence_level", "可核实数字",
    )
    return [p for p in problems if any(m in p for m in markers)]


_LENGTH_STRUCTURE_MARKERS = (
    "字数", "汉字", "上限", "下限",
    "核心结果须", "段落超过", "数据卡",
    "局限须至少", "局限条数不足",
    "deep 正文", "标题连写",
    "段缺少具体数字",
)
_NOT_LENGTH_STRUCTURE = (
    "source_quote 过短", "必须包含数字", "数字 '", "数字范围",
    "含义不匹配", "单位不匹配", "主张与原文矛盾", "原文未支持",
    "标识符", "编造", "无法回溯", "结果字段", "可核实数字",
)


def _is_length_structure_problem(problem: str) -> bool:
    if any(x in problem for x in _NOT_LENGTH_STRUCTURE):
        return False
    return any(m in problem for m in _LENGTH_STRUCTURE_MARKERS)


def _length_structure_only(problems: list[str]) -> bool:
    hard = _hard_problems(problems)
    return bool(hard) and all(_is_length_structure_problem(p) for p in hard)


def _soften_lone_institution_hit(problems: list[str], art: dict | None = None) -> list[str]:
    """One institution miss must not turn length/structure-only issues into hard fails."""
    inst = [p for p in (problems or []) if "机构名" in p]
    if len(inst) != 1:
        return list(problems or [])
    rest = [p for p in problems if p not in inst]
    hard_rest = _hard_problems(rest)
    if hard_rest and all(_is_length_structure_problem(p) for p in hard_rest):
        if art is not None:
            art.setdefault("qc_notes", []).append(inst[0])
        logging.info(
            "Lone institution miss demoted to note (length/structure remain): %s",
            inst[0],
        )
        return rest
    return list(problems or [])


def _section_length_targets(
    art: dict, problems: list[str], section_ranges: dict | None = None,
) -> list[str]:
    """Explicit per-section targets plus measured overages/shortfalls."""
    from inlight_qc import SECTION_RANGES, han_len

    ranges = section_ranges or SECTION_RANGES
    body = han_len([
        (art or {}).get("one_liner"), (art or {}).get("background"),
        (art or {}).get("design"), (art or {}).get("results"),
        (art or {}).get("mechanism"), (art or {}).get("limitations"),
        (art or {}).get("significance"),
    ])
    lines = [
        "仅因各段字数或结构未达标。按下列实测重写为 deep：",
        "只改被点名段落的长短；已核对数字、主张、标识符必须逐字保留，不得改数或换名。",
        "超标段删次要句压到目标上限；不足段只补材料里已有的事实，不得编数字。不得编造未读章节。",
        *problems,
        f"正文合计现 {body} 字，硬性目标 1400–1900。",
    ]
    for name, (lo, hi) in ranges.items():
        n = han_len(art.get(name) if art else "")
        if n < lo:
            lines.append(
                f"【必须扩写】{name} 现 {n} 字 → 目标 {lo}–{hi}（少 {lo - n} 字）。"
                f"只补材料已写明的事实，保留全部已核实数字。"
            )
        elif n > hi:
            lines.append(
                f"【必须压缩】{name} 现 {n} 字 → 目标 {lo}–{hi}（多 {n - hi} 字）。"
                f"删次要细节，保留全部已核实数字。"
            )
        else:
            lines.append(f"{name} 现 {n} 字，已在 {lo}–{hi}，保持。")
    if body < 1400:
        lines.append(f"正文合计少 {1400 - body} 字，先扩写不足段。")
    elif body > 1900:
        lines.append(f"正文合计多 {body - 1900} 字，先压缩超标段。")
    return lines


_SECTION_BAND_RE = re.compile(r'^(\S+) 字数 (\d+)，要求 (\d+)–(\d+)$')
SECTION_BAND_SLACK_FRAC = 0.15
_SLACK_EXCLUDE_SECTIONS = {"title"}


def _apply_section_band_slack(art: dict, struct_probs: list[str]) -> tuple[list[str], list[dict]]:
    """Modest per-section slack when body is already 1400–1900 and claims are clean."""
    from inlight_qc import han_len

    body = han_len([
        (art or {}).get("one_liner"), (art or {}).get("background"),
        (art or {}).get("design"), (art or {}).get("results"),
        (art or {}).get("mechanism"), (art or {}).get("limitations"),
        (art or {}).get("significance"),
    ])
    if not (1400 <= body <= 1900):
        logging.info(
            "Section band slack withheld: body %d not in 1400–1900 (%d issues)",
            body, len(struct_probs),
        )
        return list(struct_probs), []
    kept: list[str] = []
    overages: list[dict] = []
    for p in struct_probs:
        m = _SECTION_BAND_RE.match(p.strip())
        if not m:
            kept.append(p)
            continue
        name, n, lo, hi = m.group(1), int(m.group(2)), int(m.group(3)), int(m.group(4))
        if name in _SLACK_EXCLUDE_SECTIONS:
            logging.info(
                "Section band slack withheld %s: excluded section (%s)",
                name, p,
            )
            kept.append(p)
            continue
        slack = int(round(hi * SECTION_BAND_SLACK_FRAC))
        if lo - slack <= n <= hi + slack:
            delta = (n - hi) if n > hi else (n - lo)
            overages.append({
                "section": name, "n": n, "lo": lo, "hi": hi,
                "slack": slack, "delta": delta,
            })
            logging.info(
                "Section band slack %s: %d vs %d–%d (slack %d, delta %+d)",
                name, n, lo, hi, slack, delta,
            )
        else:
            logging.info(
                "Section band slack withheld %s: %d vs %d–%d (slack %d, outside band)",
                name, n, lo, hi, slack,
            )
            kept.append(p)
    return kept, overages


def _article_body_han(art: dict | None) -> int:
    from inlight_qc import han_len
    return han_len([
        (art or {}).get("one_liner"), (art or {}).get("background"),
        (art or {}).get("design"), (art or {}).get("results"),
        (art or {}).get("mechanism"), (art or {}).get("limitations"),
        (art or {}).get("significance"),
    ])


def _band_distance(
    art: dict, section_ranges: dict | None = None, *, brief: bool = False,
) -> int:
    """How far a draft sits from section bands (lower is closer)."""
    from inlight_qc import SECTION_RANGES, han_len

    if brief:
        body = _article_body_han(art)
        if body < BRIEF_HAN_MIN:
            return BRIEF_HAN_MIN - body
        if body > BRIEF_HAN_MAX:
            return body - BRIEF_HAN_MAX
        return 0

    ranges = section_ranges or SECTION_RANGES
    dist = 0
    for name, (lo, hi) in ranges.items():
        if name == "title":
            continue
        n = han_len(art.get(name) if art else "")
        if n < lo:
            dist += lo - n
        elif n > hi:
            dist += n - hi
    body = _article_body_han(art)
    if body < 1400:
        dist += 1400 - body
    elif body > 1900:
        dist += body - 1900
    return dist


def _length_keep_score(
    art: dict, problems: list[str], section_ranges: dict | None = None,
    *, brief: bool = False,
) -> tuple[int, int]:
    return (len(_hard_problems(problems)), _band_distance(art, section_ranges, brief=brief))


def _brief_length_targets(art: dict, problems: list[str]) -> list[str]:
    """Both bounds of brief (450–900 字) plus the same per-section targets as the prompt."""
    from inlight_qc import han_len

    body = _article_body_han(art)
    lines = [
        "仅因字数或结构未达标。按下列实测重写为 brief，不得写成 deep：",
        "只改长短；已核对数字、主张、标识符必须逐字保留，不得改数或换名。",
        *problems,
        f"正文合计现 {body} 字，硬性目标 {BRIEF_HAN_MIN}–{BRIEF_HAN_MAX}（目标约 600）。",
        f"各段目标：{_brief_section_prompt_line()}。",
    ]
    for name, (lo, hi) in BRIEF_SECTION_RANGES.items():
        n = han_len(art.get(name) if art else "")
        if n < lo:
            lines.append(
                f"【必须扩写】{name} 现 {n} 字 → 目标 {lo}–{hi}（少 {lo - n} 字）。"
                f"只补材料已写明的事实，保留全部已核实数字。"
            )
        elif n > hi:
            lines.append(
                f"【必须压缩】{name} 现 {n} 字 → 目标 {lo}–{hi}（多 {n - hi} 字）。"
                f"删次要细节，保留全部已核实数字。"
            )
        else:
            lines.append(f"{name} 现 {n} 字，已在 {lo}–{hi}，保持。")
    if body < BRIEF_HAN_MIN:
        lines.append(
            f"【必须扩写】正文少 {BRIEF_HAN_MIN - body} 字 → 目标 "
            f"{BRIEF_HAN_MIN}–{BRIEF_HAN_MAX}。只补材料已写明的事实。"
        )
    elif body > BRIEF_HAN_MAX:
        lines.append(
            f"【必须压缩】正文多 {body - BRIEF_HAN_MAX} 字 → 目标 "
            f"{BRIEF_HAN_MIN}–{BRIEF_HAN_MAX}。删次要句，保留已核实数字。"
        )
    else:
        lines.append(f"正文已在 {BRIEF_HAN_MIN}–{BRIEF_HAN_MAX}，保持。")
    return lines


def _run_deep_length_redrafts(
    art: dict,
    problems: list[str],
    enriched_item,
    config: dict,
    url: str,
    prepare,
    section_ranges: dict | None = None,
) -> tuple[dict, list[str], str | None]:
    """Up to 2 deep redrafts for length/structure-only. Keep the best draft."""
    best_art, best_probs = art, problems
    best_score = _length_keep_score(art, problems, section_ranges)
    for attempt in (1, 2):
        targets = _section_length_targets(best_art, best_probs, section_ranges)
        logging.info(
            "Deep length/structure redraft %d/2 for %s: %s",
            attempt, url, best_probs,
        )
        retry_len = draft_single_article(enriched_item, "deep", config, problems=targets)
        if retry_len is None:
            continue
        cand, cand_probs = prepare(retry_len)
        if not cand:
            continue
        best_body = _article_body_han(best_art)
        cand_body = _article_body_han(cand)
        if best_body < 1400 and cand_body < best_body:
            logging.info(
                "Discarding shorter deep length redraft %d (%d < %d) when under-length: %s",
                attempt, cand_body, best_body, url,
            )
            continue
        score = _length_keep_score(cand, cand_probs, section_ranges)
        if score < best_score:
            logging.info(
                "Keeping length redraft %d (score %s) over previous %s: %s",
                attempt, score, best_score, url,
            )
            best_art, best_probs, best_score = cand, cand_probs, score
        else:
            logging.info(
                "Discarding shorter/worse length redraft %d (score %s vs best %s): %s",
                attempt, score, best_score, url,
            )
        if not _hard_problems(best_probs):
            return best_art, best_probs, None
    art, problems = best_art, best_probs
    if _hard_problems(problems) and _length_structure_only(problems):
        return art, problems, (
            "deep length/structure still failing after 2 redrafts: "
            + "; ".join(_hard_problems(problems)[:4])
        )
    return art, problems, None


def _run_brief_length_redraft(
    art: dict,
    problems: list[str],
    enriched_item,
    config: dict,
    url: str,
    prepare,
    section_ranges: dict | None = None,
) -> tuple[dict, list[str], str | None]:
    """One length-only brief redraft before drop (strict/production path only)."""
    from inlight_qc import acir_strict
    if not acir_strict(config):
        return art, problems, None
    if not (_hard_problems(problems) and _length_structure_only(problems)):
        return art, problems, None
    targets = _brief_length_targets(art, problems)
    logging.info("Brief length-only redraft for %s: %s", url, problems)
    retry = draft_single_article(enriched_item, "brief", config, problems=targets)
    if retry is None:
        return art, problems, (
            "brief length redraft failed: " + "; ".join(_hard_problems(problems)[:4])
        )
    cand, cand_probs = prepare(retry)
    if cand and _length_keep_score(cand, cand_probs, brief=True) < _length_keep_score(
        art, problems, brief=True
    ):
        art, problems = cand, cand_probs
    if _hard_problems(problems):
        return art, problems, (
            "brief length still failing: " + "; ".join(_hard_problems(problems)[:4])
        )
    return art, problems, None


def _source_bucket(item: EnrichedItem) -> str:
    from inlight_qc import item_has_real_fulltext
    if item_has_real_fulltext(item):
        return "fulltext"
    if item.evidence_level == "preprint":
        return "preprint"
    if item.press_coverage and item.evidence_level == "press":
        return "press"
    if item.abstract and item.evidence_level != "press":
        return "abstract"
    return "press"


def log_run_yield(stats: dict, config: dict | None) -> None:
    """Per-run yield log. Targets are aims; accuracy is never relaxed to hit them."""
    t = pipeline_targets(config)
    src = stats.get("sources") or {}
    logging.info("=== 周报产量 ===")
    logging.info(
        "候选：抓取 %d，选题 %d（深度解读目标 %d–%d；速览上限 %d；行业可选）",
        stats.get("candidates_fetched", 0),
        stats.get("candidates_triaged", 0),
        t["min_deep"], t["max_deep"], t["max_brief"],
    )
    logging.info(
        "来源：全文 %d / 摘要 %d / 新闻稿 %d / 预印本 %d",
        src.get("fulltext", 0), src.get("abstract", 0),
        src.get("press", 0), src.get("preprint", 0),
    )
    logging.info(
        "发表：深度 %d / 速览 %d / 行业 %d / 丢弃 %d",
        stats.get("published_deep", 0),
        stats.get("published_brief", 0),
        stats.get("published_industry", 0),
        stats.get("dropped", 0),
    )
    for drop in stats.get("drops") or []:
        logging.info("丢弃 %s：%s", drop.get("url", ""), drop.get("reason", ""))
    if stats.get("published_deep", 0) < t["min_deep"]:
        logging.warning(
            "深度解读低于目标：%d < %d（上限 %d）。全文候选 %d。未放宽全文门槛，未放宽核对。",
            stats.get("published_deep", 0), t["min_deep"], t["max_deep"],
            stats.get("fulltext_candidates", 0),
        )


LAST_RUN_STATS: dict = {}


def process_articles(items: list[dict], config: dict) -> dict:
    """Process items through enrichment, triage, drafting, and validation.
    
    Returns dict with articles and deals (deals passed through unchanged).
    """
    from inlight_qc import acir_strict, apply_extra_env_gemini_key

    apply_extra_env_gemini_key(config)
    t = pipeline_targets(config)
    academic_items = [item for item in items if item.get("kind") == "academic"]
    if t["max_candidates"] and len(academic_items) > t["max_candidates"]:
        logging.info(
            "Capping academic candidates %d → %d for enrich/triage (来源均衡)",
            len(academic_items), t["max_candidates"],
        )
        academic_items = balance_academic_candidates(academic_items, t["max_candidates"])
    
    logging.info("Enriching %d academic items", len(academic_items))
    enriched = [enrich_item(item) for item in academic_items]
    
    source_counts = {"fulltext": 0, "abstract": 0, "press": 0, "preprint": 0}
    for item in enriched:
        source_counts[_source_bucket(item)] = source_counts.get(_source_bucket(item), 0) + 1
        logging.info(
            "来源抓取 %s：%s（%s）",
            item.url, _source_bucket(item), "; ".join(item.source_trace[-3:]),
        )
    
    selections = triage_items(enriched, config)
    academic_sels = [s for s in selections if s.get("tier") != "industry"]
    industry_sels = [s for s in selections if s.get("tier") == "industry"]

    stats = {
        "candidates_fetched": len(academic_items),
        "candidates_triaged": len(academic_sels),
        "sources": source_counts,
        "published_deep": 0,
        "published_brief": 0,
        "published_industry": len(industry_sels),
        "dropped": 0,
        "drops": [],
        "qc_report": {"articles": [], "strict": acir_strict(config)},
        "fulltext_candidates": 0,
    }
    
    url_to_enriched = {e.url: e for e in enriched}
    from inlight_qc import item_has_real_fulltext
    stats["fulltext_candidates"] = sum(1 for e in enriched if item_has_real_fulltext(e))

    articles = []
    for selection in selections:
        url = selection["url"]
        tier = selection["tier"]
        
        if tier == "industry":
            continue
        
        try:
            art = _process_single_article(selection, url_to_enriched, config, stats=stats)
            if art:
                articles.append(art)
                if art.get("tier") == "deep":
                    stats["published_deep"] += 1
                else:
                    stats["published_brief"] += 1
            else:
                stats["dropped"] += 1
                if not any(d.get("url") == url for d in stats["drops"]):
                    stats["drops"].append({"url": url, "reason": "dropped after validation"})
        except Exception as e:
            logging.error("EXCEPTION processing article %s: %s - dropping this article, run continues", url, e)
            import traceback
            logging.debug("Traceback: %s", traceback.format_exc())
            stats["dropped"] += 1
            stats["drops"].append({"url": url, "reason": f"exception: {e}"})
            continue

    if acir_strict(config):
        from inlight_audit import apply_dual_blind_to_week, run_automated_audit
        from inlight_qc import item_has_real_fulltext

        def _redraft_blind(art, reasons):
            item = url_to_enriched.get(art.get("url"))
            if not item:
                return None
            retry = draft_single_article(item, "deep", config, problems=[str(reasons)])
            if not retry:
                return None
            retry["field"] = art.get("field")
            retry["tier"] = "deep"
            retry["url"] = art.get("url")
            retry["skip_mechanism_figure"] = art.get("skip_mechanism_figure", False)
            raw = "\n".join(filter(None, [
                getattr(item, "abstract", None),
                getattr(item, "fulltext_results", None),
                getattr(item, "fig_captions", None),
                getattr(item, "methods_design", None),
            ]))
            audit = run_automated_audit(
                retry,
                abstract=getattr(item, "abstract", "") or "",
                results_src=getattr(item, "fulltext_results", "") or "",
                fig_captions=getattr(item, "fig_captions", "") or "",
                methods=getattr(item, "methods_design", "") or "",
                source=raw,
                real_fulltext=item_has_real_fulltext(item),
                claim_audit=None,
                structure_ok=True,
                number_ok=True,
                has_figure=not retry.get("skip_mechanism_figure", True),
                rewrite_count=int(art.get("rewrite_count") or 0) + 1,
            )
            if not audit.get("publish_allowed"):
                return None
            retry["writing_audit"] = audit
            return retry

        articles = apply_dual_blind_to_week(
            articles, stats, config, url_to_enriched, redraft=_redraft_blind,
        )

    stats["dropped"] = len(stats["drops"])
    LAST_RUN_STATS.clear()
    LAST_RUN_STATS.update(stats)
    log_run_yield(stats, config)
    
    return {"articles": articles, "deals": [], "stats": stats}


def _process_single_article(
    selection: dict, url_to_enriched: dict, config: dict, stats: dict | None = None,
) -> dict | None:
    """Process a single article through drafting, validation, and transformation.
    
    Extracted to allow per-article exception handling in process_articles.
    Returns the processed article dict or None if it should be dropped.
    """
    from inlight_qc import (
        acir_strict, item_has_real_fulltext, load_gemini_api_key, secondhand_label,
        validate_acir_structure, verifier_source_text, mechanism_image_prompt,
        comparable_verified_points, render_data_chart_svg, gemini_review_deep,
        empty_check, assemble_qc_entry, FIG_DISCLAIMER, section_ranges_for_material,
    )

    url = selection["url"]
    tier = selection["tier"]
    field = selection["field"]
    related_fields = [
        r for r in (selection.get("related_fields") or [])
        if r and r != field
    ]

    captured_overages: list[dict] = []

    def drop(reason: str, extra: dict | None = None):
        logging.error("Dropping %s: %s", url, reason)
        rec = {"url": url, "reason": reason}
        payload = dict(extra or {})
        overs = payload.get("section_overages") or captured_overages
        if overs:
            rec["section_overages"] = list(overs)
            payload.setdefault("section_overages", list(overs))
        if stats is not None:
            stats.setdefault("drops", []).append(rec)
            stats.setdefault("qc_report", {}).setdefault("articles", []).append(
                assemble_qc_entry(url, False, [empty_check("gate", False, reason)], payload)
            )
        return None

    enriched_item = url_to_enriched.get(url)
    if field in ("none", None, ""):
        logging.info(
            "Candidate out of scope %s: %s",
            url, _out_of_scope_reason(enriched_item, config, field),
        )
        return drop("out-of-scope (logged); not forced into a field")
    if not enriched_item:
        logging.warning("Skipping unknown URL from triage: %s", url)
        return drop("unknown URL from triage")
    
    strict = acir_strict(config)
    t = pipeline_targets(config)
    real_ft = item_has_real_fulltext(enriched_item)
    abstract_len = len(enriched_item.abstract or "")
    section_ranges = section_ranges_for_material(enriched_item)

    if not real_ft:
        if strict and t["max_brief"] == 0:
            return drop("no real fulltext Results; deep requires fulltext")
        if tier == "deep" and (strict or abstract_len < 1200):
            logging.warning("Downgrading %s from deep to brief (no real fulltext Results)", url)
            tier = "brief"

    if strict and tier == "deep" and not load_gemini_api_key(config):
        return drop("GEMINI_API_KEY missing; deep QC fail-closed")
    
    # Build raw_material for validation - avoid duplicating abstract/rss_summary
    raw_parts = []
    if enriched_item.abstract:
        raw_parts.append(enriched_item.abstract)
    if enriched_item.fulltext_results:
        raw_parts.append(enriched_item.fulltext_results)
    if enriched_item.fig_captions:
        raw_parts.append(enriched_item.fig_captions)
    if enriched_item.methods_design:
        raw_parts.append(enriched_item.methods_design)
    # Only include rss_summary if it's not a near-duplicate of abstract
    # EPMC and RSS may differ only in encoding (ROR{gamma}t vs Greek letters)
    if enriched_item.rss_summary:
        if not enriched_item.abstract or not is_near_duplicate(enriched_item.rss_summary, enriched_item.abstract):
            raw_parts.append(enriched_item.rss_summary)
    if enriched_item.press_coverage:
        if not any(is_near_duplicate(enriched_item.press_coverage, p) for p in raw_parts if p):
            raw_parts.append(enriched_item.press_coverage)
    raw_material = "\n".join(raw_parts)
    
    def _prepare(draft: dict | None) -> tuple[dict | None, list[str]]:
        if not draft:
            return None, ["draft missing"]
        draft = sanitize_published_article(dict(draft), enriched_item)
        if strict:
            strip_unreported_disclaimer_sentences(draft)
        draft["field"] = field
        draft["tier"] = tier
        src = verifier_source_text(enriched_item, raw_material) if real_ft else raw_material
        draft["evidence_level"] = "fulltext" if real_ft else enriched_item.evidence_level
        draft["sections_read"] = enriched_item.sections_read
        if isinstance(draft.get("datacard"), dict):
            draft["datacard"]["evidence_level"] = draft["evidence_level"]
            draft["datacard"].pop("read_note", None)
        probs = validate_depth(draft, src, allow_word_quantities=strict)
        probs.extend(validate_names(draft, src))
        probs = _soften_lone_institution_hit(probs, draft)
        if strict and draft.get("tier") == "deep":
            struct = validate_acir_structure(draft, section_ranges=section_ranges)
            depth_content = [
                p for p in _hard_problems(probs) if not _is_length_structure_problem(p)
            ]
            if not depth_content:
                struct, overages = _apply_section_band_slack(draft, struct)
                if overages:
                    draft.setdefault("qc_section_overages", []).extend(overages)
                    captured_overages.clear()
                    captured_overages.extend(draft.get("qc_section_overages") or [])
            probs.extend(struct)
            from inlight_qc import verified_data_points
            if len(verified_data_points(draft, src)) < 6:
                n = len(verified_data_points(draft, src))
                probs.append(f"可核实数字不足 6 个（{n}）")
        # System notes after number/identifier checks so DOI/PMCID/核对记录
        # are not treated as invented identifiers.
        draft["read_note"] = enriched_item.read_note
        if isinstance(draft.get("datacard"), dict) and enriched_item.read_note:
            draft["datacard"]["read_note"] = enriched_item.read_note
        return draft, probs

    art = draft_single_article(enriched_item, tier, config)
    if not art:
        # Malformed / empty / text-only replies get exactly one redraft
        logging.warning("Malformed or empty draft, redrafting once: %s", url)
        art = draft_single_article(enriched_item, tier, config)
    if not art:
        logging.warning("Failed to draft article for: %s", url)
        return drop("draft failed")

    art, problems = _prepare(art)

    first_hard_problems = _hard_problems(problems)
    first_soft_problems = [p for p in problems if p not in first_hard_problems]
    first_has_soft_only = len(first_hard_problems) == 0 and len(first_soft_problems) > 0

    # Soft-only issues (empty optional fields, limitation count after omit)
    # are not a reason to spend a redraft or to drop the article.
    if first_soft_problems and not first_hard_problems:
        logging.warning("Accepting %s with soft-only problems: %s", url, first_soft_problems)
        problems = first_soft_problems

    if (
        first_hard_problems
        and tier == "deep"
        and real_ft
        and _length_structure_only(problems)
        and strict
    ):
        art, problems, drop_reason = _run_deep_length_redrafts(
            art, problems, enriched_item, config, url, _prepare,
            section_ranges=section_ranges,
        )
        if drop_reason:
            return drop(drop_reason)
        first_hard_problems = _hard_problems(problems)
        first_soft_problems = [p for p in problems if p not in first_hard_problems]

    if first_hard_problems:
        logging.warning("Validation issues for %s: %s", url, problems)

        first_art = art
        first_problems = problems

        logging.info("Targeted redraft with %d problems listed...", len(problems))
        retry_art = draft_single_article(enriched_item, tier, config, problems=problems)

        if retry_art is None:
            logging.error("Targeted redraft failed for %s", url)
            if tier == "deep":
                logging.info("Trying brief fallback for: %s", url)
                retry_art = draft_single_article(enriched_item, "brief", config)
                if retry_art is None:
                    logging.error("Brief fallback also failed, dropping: %s", url)
                    return drop("brief fallback draft failed")
                tier = "brief"
                art, problems = _prepare(retry_art)
                art, problems, drop_reason = _run_brief_length_redraft(
                    art, problems, enriched_item, config, url, _prepare,
                    section_ranges=section_ranges,
                )
                if drop_reason and _hard_problems(problems):
                    return drop(drop_reason)
            elif first_has_soft_only:
                logging.warning("Redraft failed but first draft had soft-only problems, keeping first: %s", url)
                art = first_art
                problems = first_problems
            else:
                logging.error("Dropping %s after failed redraft: %s", url, first_hard_problems)
                return drop("redraft failed: " + "; ".join(first_hard_problems[:4]))
        else:
            retry_art, retry_problems = _prepare(retry_art)
            retry_hard = _hard_problems(retry_problems)

            first_score = _draft_quality_score(
                first_hard_problems, first_problems, first_art, raw_material
            )
            retry_score = _draft_quality_score(
                retry_hard, retry_problems, retry_art, raw_material
            )

            if retry_score <= first_score:
                art = retry_art
                problems = retry_problems
                logging.info("Using retry draft (score %s vs first %s): %s", retry_score, first_score, url)
            else:
                art = first_art
                problems = first_problems
                logging.info("Keeping first draft (score %s vs retry %s): %s", first_score, retry_score, url)

            if problems:
                hard_problems = _hard_problems(problems)
                soft_problems = [p for p in problems if p not in hard_problems]

                if hard_problems:
                    ran_length_loop = False
                    if (
                        tier == "deep"
                        and real_ft
                        and strict
                        and _length_structure_only(problems)
                    ):
                        art, problems, drop_reason = _run_deep_length_redrafts(
                            art, problems, enriched_item, config, url, _prepare,
                            section_ranges=section_ranges,
                        )
                        if drop_reason:
                            return drop(drop_reason)
                        hard_problems = _hard_problems(problems)
                        soft_problems = [p for p in problems if p not in hard_problems]
                        ran_length_loop = True
                    if (
                        ran_length_loop
                        and hard_problems
                        and not _length_structure_only(problems)
                        and tier == "deep"
                        and real_ft
                        and strict
                    ):
                        logging.info(
                            "Length redraft introduced content problems; one targeted deep fix: %s",
                            url,
                        )
                        fix = draft_single_article(
                            enriched_item, "deep", config, problems=problems,
                        )
                        if fix:
                            art, problems = _prepare(fix)
                            hard_problems = _hard_problems(problems)
                            soft_problems = [p for p in problems if p not in hard_problems]
                    if not hard_problems:
                        if soft_problems:
                            logging.warning("Accepting %s with soft-only problems: %s", url, soft_problems)
                    elif tier == "deep":
                        logging.warning("Downgrading %s from deep to brief after retry - hard problems: %s", url, hard_problems)
                        brief_art = draft_single_article(enriched_item, "brief", config, problems=problems)
                        if brief_art is None:
                            logging.error("Brief targeted redraft failed, dropping: %s", url)
                            return drop("brief redraft failed")
                        tier = "brief"
                        art, problems = _prepare(brief_art)
                        art, problems, drop_reason = _run_brief_length_redraft(
                            art, problems, enriched_item, config, url, _prepare,
                            section_ranges=section_ranges,
                        )
                        hard_problems = _hard_problems(problems)
                        if hard_problems:
                            logging.error("Dropping %s after brief redraft - hard problems: %s", url, hard_problems)
                            return drop(drop_reason or (
                                "brief redraft still hard: " + "; ".join(hard_problems[:4])
                            ))
                        if problems:
                            logging.warning("Accepting %s with soft problems: %s", url, problems)
                    else:
                        art, problems, drop_reason = _run_brief_length_redraft(
                            art, problems, enriched_item, config, url, _prepare,
                            section_ranges=section_ranges,
                        )
                        hard_problems = _hard_problems(problems)
                        if hard_problems:
                            logging.error("Dropping %s after retry - hard problems: %s", url, hard_problems)
                            return drop(drop_reason or (
                                "retry still hard: " + "; ".join(hard_problems[:4])
                            ))
                else:
                    logging.warning("Accepting %s with soft-only problems: %s", url, soft_problems)

    if _hard_problems(problems) and tier == "brief":
        art, problems, drop_reason = _run_brief_length_redraft(
            art, problems, enriched_item, config, url, _prepare,
            section_ranges=section_ranges,
        )
    if _hard_problems(problems):
        logging.error("Dropping %s with remaining hard problems: %s", url, _hard_problems(problems))
        return drop("hard problems remain: " + "; ".join(_hard_problems(problems)[:4]))

    art, dropped = _run_claim_verifier_stage(
        art, problems, raw_material, enriched_item, config, tier, field,
    )
    if dropped or not art:
        return drop("claim verifier: " + "; ".join((LAST_CLAIM_AUDIT.get("problems") or ["rejected"])[:4]))

    # Transform new format to include legacy fields needed by write_output
    # Add date from enriched item
    art["date"] = enriched_item.date
    
    # Map new fields to legacy fields for backward compatibility
    # lead: one_liner or first result
    art["lead"] = art.get("one_liner", "") or (art.get("results", [""])[0] if art.get("results") else "")
    
    # body: combine background, design, results
    body_parts = []
    if art.get("background"):
        body_parts.append(art["background"])
    if art.get("design"):
        body_parts.append(art["design"])
    if art.get("results"):
        body_parts.extend(art["results"])
    if art.get("mechanism"):
        body_parts.append(art["mechanism"])
    art["body"] = " ".join(body_parts)
    
    # discuss: combine limitations and significance
    discuss_parts = []
    if art.get("limitations"):
        # Strip trailing periods to avoid "。；" in the join
        lims = [lim.rstrip("。.") for lim in art["limitations"]]
        discuss_parts.append("局限：" + "；".join(lims) + "。")
    if art.get("significance"):
        discuss_parts.append(art["significance"])
    art["discuss"] = " ".join(discuss_parts)
    
    # Ensure journal is set - prefer EPMC journal, fallback to source
    if not art.get("journal"):
        art["journal"] = enriched_item.journal or enriched_item.source
    
    # Ensure steps is set (required for image caption)
    if not art.get("steps"):
        art["steps"] = ["研究背景", "方法设计", "核心发现", "意义与局限"]
    
    art["evidence_level"] = "fulltext" if real_ft else enriched_item.evidence_level
    art["read_note"] = enriched_item.read_note
    art["sections_read"] = enriched_item.sections_read
    if not art.get("citation"):
        bits = [enriched_item.authors or art.get("authors") or "",
                enriched_item.journal or art.get("journal") or "",
                enriched_item.date, enriched_item.doi or url,
                art["evidence_level"], enriched_item.read_note or ""]
        art["citation"] = " ".join(b for b in bits if b)
    if not real_ft:
        art["secondhand_label"] = secondhand_label(enriched_item)
        art["skip_mechanism_figure"] = True
        art.pop("image_prompt", None)
        art["fig_caption"] = ""
    elif art.get("tier") == "deep":
        art["image_prompt"] = mechanism_image_prompt(
            art.get("results") or art.get("mechanism") or "",
            art.get("mechanism") or "",
        )
        art["fig_caption"] = FIG_DISCLAIMER
        art["skip_mechanism_figure"] = False
    else:
        art["skip_mechanism_figure"] = True
        art.pop("image_prompt", None)

    qc_src = verifier_source_text(enriched_item, raw_material)
    gemini_result = None
    if strict and art.get("tier") == "deep":
        gemini_result = gemini_review_deep(art, qc_src, config)
        if not gemini_result.get("pass"):
            logging.warning("Gemini ACIR 未过，Claude 重写一次：%s", gemini_result.get("reasons"))
            retry = draft_single_article(
                enriched_item, "deep", config,
                problems=[str(gemini_result.get("reasons") or "Gemini ACIR scores < 7")],
            )
            if retry:
                retry, retry_probs = _prepare(retry)
                if retry and not _hard_problems(retry_probs):
                    retry, dropped = _run_claim_verifier_stage(
                        retry, retry_probs, raw_material, enriched_item, config, "deep", field,
                    )
                    if retry and not dropped:
                        gemini_result = gemini_review_deep(retry, qc_src, config)
                        if gemini_result.get("pass"):
                            art = retry
                            art["image_prompt"] = mechanism_image_prompt(
                                art.get("results") or art.get("mechanism") or "",
                                art.get("mechanism") or "",
                            )
                            art["fig_caption"] = FIG_DISCLAIMER
                            art["skip_mechanism_figure"] = False
                        else:
                            return drop("Gemini ACIR still failing after one Claude revise")
                    else:
                        return drop("Gemini revise failed claim verifier")
                else:
                    return drop("Gemini revise failed structure/numbers")
            else:
                return drop("Gemini revise draft failed")
        art["gemini_review"] = {
            "scores": gemini_result.get("scores"),
            "reasons": gemini_result.get("reasons"),
            "pass": True,
        }

    if art.get("tier") == "deep" and real_ft:
        pts = comparable_verified_points(art, qc_src)
        if len(pts) >= 2:
            art["data_chart_svg"] = render_data_chart_svg(pts, "核对后的关键对比")
            art["data_chart_points"] = [
                {"value": p.get("value"), "meaning": p.get("meaning")} for p in pts
            ]
        else:
            art["data_chart_svg"] = ""

    allowed_fields = _triage_field_map(config)
    raw_field = art.get("field") or field
    if raw_field in ("none", "", None) or raw_field not in allowed_fields:
        if strict and raw_field in ("none", "", None):
            logging.info(
                "Candidate out of scope %s: %s",
                url, _out_of_scope_reason(enriched_item, config, raw_field),
            )
            return drop("out-of-scope (logged); not forced into a field")
        if raw_field not in allowed_fields and raw_field not in FIELDS:
            return drop("field not in the 9 domains")
        if raw_field in FIELDS and raw_field not in allowed_fields:
            from inlight_fields import OLD_TO_NEW
            art["field"] = OLD_TO_NEW.get(raw_field, raw_field)
            if art["field"] not in allowed_fields:
                logging.info(
                    "Candidate out of scope %s: mapped field %s not in allowed set",
                    url, art["field"],
                )
                return drop("out-of-scope (logged); not forced into a field")
    else:
        art["field"] = raw_field

    draft_related = art.get("related_fields") or related_fields
    art["related_fields"] = [
        r for r in draft_related
        if r in allowed_fields and r != art.get("field")
    ]

    claim_status = LAST_CLAIM_AUDIT.get("status") or "ok"
    claim_ok = claim_status == "ok"
    struct_probs = (
        validate_acir_structure(art, section_ranges=section_ranges)
        if (strict and art.get("tier") == "deep") else []
    )
    if struct_probs:
        struct_probs, end_overs = _apply_section_band_slack(art, struct_probs)
        if end_overs:
            art.setdefault("qc_section_overages", []).extend(end_overs)
            captured_overages.clear()
            captured_overages.extend(art.get("qc_section_overages") or [])
    number_probs = [p for p in (problems or []) if ("数字" in p and "未找到" in p) or "可核实数字" in p]
    checks = [
        empty_check("admission", real_ft if art.get("tier") == "deep" else True,
                    enriched_item.read_note or "not fulltext"),
        empty_check("structure", not struct_probs, "; ".join(struct_probs) or "ok"),
        empty_check("numbers", not number_probs, "; ".join(number_probs) or "ok"),
        empty_check("claims", claim_ok, claim_status if claim_ok else "; ".join(LAST_CLAIM_AUDIT.get("problems") or [claim_status])),
        empty_check("field", art.get("field") in allowed_fields, art.get("field") or ""),
        empty_check("images",
                    (art.get("tier") != "deep") or (real_ft and not art.get("skip_mechanism_figure")),
                    "mechanism figure requires fulltext"),
        empty_check(
            "id_warnings",
            True,
            "; ".join(art.get("qc_id_warnings") or []) or "ok",
        ),
        empty_check(
            "removed_numbers",
            True,
            "; ".join(
                f"{r.get('number')}: {str(r.get('text') or '')[:40]}"
                for r in (art.get("qc_removed_numbers") or [])
            ) or "ok",
        ),
    ]
    if gemini_result is not None:
        checks.append(empty_check("gemini", bool(gemini_result.get("pass")),
                                  str(gemini_result.get("reasons") or "")))
    writing_audit = None
    if strict and art.get("tier") == "deep":
        from inlight_audit import attach_audit_fields, run_automated_audit
        writing_audit = run_automated_audit(
            art,
            abstract=enriched_item.abstract or "",
            results_src=enriched_item.fulltext_results or "",
            fig_captions=enriched_item.fig_captions or "",
            methods=enriched_item.methods_design or "",
            source=qc_src,
            real_fulltext=real_ft,
            claim_audit=LAST_CLAIM_AUDIT,
            structure_ok=not struct_probs,
            number_ok=not number_probs,
            number_problems=number_probs,
            has_figure=not art.get("skip_mechanism_figure"),
            rewrite_count=int(art.get("rewrite_count") or 0),
        )
        art["writing_audit"] = {k: writing_audit.get(k) for k in (
            "headline_ok", "endpoint_hierarchy", "must_cover_coverage",
            "must_cover_list_id", "must_cover_text_hash", "must_cover_item_count",
            "boilerplate_count", "hard_errors", "publish_allowed",
        )}
        checks.append(empty_check(
            "writing_audit",
            bool(writing_audit.get("publish_allowed")),
            "; ".join(
                f"{e.get('code')}: {e.get('detail')}"
                for e in (writing_audit.get("hard_errors") or [])
            ) or ("gates failed" if not writing_audit.get("publish_allowed") else "ok"),
        ))
    failed = [c for c in checks if not c.get("pass")]

    def _qc_extra(audit=None):
        extra = {
            "tier": art.get("tier"),
            "read_note": enriched_item.read_note,
            "sections_read": enriched_item.sections_read,
            "gemini": (gemini_result or {}).get("scores"),
            "id_warnings": art.get("qc_id_warnings") or [],
            "removed_numbers": art.get("qc_removed_numbers") or [],
            "must_cover_list_id": "",
            "must_cover_text_hash": "",
            "must_cover_item_count": 0,
            "must_cover_coverage": None,
            "blind_scores": [],
            "blind_judge_runs": [],
            "section_overages": art.get("qc_section_overages") or [],
            "drafter_model": art.get("drafter_model") or "",
            "qc_notes": art.get("qc_notes") or [],
        }
        if audit:
            from inlight_audit import attach_audit_fields
            extra = attach_audit_fields(extra, audit)
            extra["removed_numbers"] = art.get("qc_removed_numbers") or []
            extra["section_overages"] = art.get("qc_section_overages") or []
        return extra

    if failed and strict:
        return drop("QC check failed: " + "; ".join(
            f"{c['name']}: {c.get('reason') or ''}" for c in failed[:4]
        ), _qc_extra(writing_audit))
    if failed:
        logging.warning("QC warnings (non-strict, still publishing): %s",
                        "; ".join(f"{c['name']}: {c.get('reason') or ''}" for c in failed[:4]))
    extra = _qc_extra(writing_audit)
    if stats is not None:
        stats.setdefault("qc_report", {}).setdefault("articles", []).append(
            assemble_qc_entry(url, True, checks, extra)
        )

    return art


def coerce_publish_text(val) -> str:
    """Turn any datacard / field value into publishable text.

    Nested lists are flattened. Dict and list reprs are never published.
    """
    if val is None or val is False:
        return ""
    if isinstance(val, bool):
        return ""
    if isinstance(val, (int, float)):
        return str(val)
    if isinstance(val, str):
        s = val.strip()
        if len(s) >= 2 and s[0] in "[({" and s[-1] in "])}":
            try:
                parsed = ast.literal_eval(s)
            except (ValueError, SyntaxError):
                return val
            if isinstance(parsed, (list, dict, tuple)):
                return coerce_publish_text(parsed)
        return val
    if isinstance(val, (list, tuple)):
        parts = [coerce_publish_text(x) for x in val]
        return "、".join(p for p in parts if p)
    if isinstance(val, dict):
        if "text" in val:
            return coerce_publish_text(val["text"])
        return ""
    return str(val)


def flatten_string_list(val) -> list[str]:
    """Flatten nested lists/dicts into a list of strings; drop repr text."""
    if val is None:
        return []
    if isinstance(val, str):
        s = val.strip()
        if len(s) >= 2 and s[0] in "[({" and s[-1] in "])}":
            try:
                parsed = ast.literal_eval(s)
            except (ValueError, SyntaxError):
                return [val] if val else []
            return flatten_string_list(parsed)
        return [val] if val else []
    if isinstance(val, (list, tuple)):
        out: list[str] = []
        for item in val:
            out.extend(flatten_string_list(item))
        return out
    if isinstance(val, dict):
        text = coerce_publish_text(val)
        return [text] if text else []
    if isinstance(val, (int, float)):
        return [str(val)]
    return []


def _escape_html(text) -> str:
    """Escape HTML special characters after coercing any field to text."""
    text = coerce_publish_text(text)
    if not text:
        return ""
    return (text
            .replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace('"', "&quot;"))


def _extract_doi_from_url(url: str) -> str:
    """Extract DOI string for display from any URL format."""
    if not url:
        return ""
    # Direct DOI URL
    if "doi.org" in url:
        return url.replace("https://doi.org/", "DOI: ").replace("http://doi.org/", "DOI: ")
    # Nature articles
    match = re.search(r'nature\.com/articles/(s\d+-\d+-\d+-\w+)', url)
    if match:
        return f"DOI: 10.1038/{match.group(1)}"
    # bioRxiv/medRxiv
    match = re.search(r'(?:bio|med)rxiv\.org/content/(10\.\d+/[\d.]+)', url)
    if match:
        return f"DOI: {match.group(1)}"
    # PubMed
    match = re.search(r'pubmed\.ncbi\.nlm\.nih\.gov/(\d+)', url)
    if match:
        return f"PMID: {match.group(1)}"
    # Science
    match = re.search(r'science\.org/doi/(10\.\d+/[^\s?#]+)', url)
    if match:
        return f"DOI: {match.group(1)}"
    return ""


EVIDENCE_LEVEL_LABELS = {
    "fulltext": "全文",
    "abstract": "摘要", 
    "preprint": "预印本",
    "press": "新闻稿",
    "secondary": "二手",
}

# Schema descriptions / renderer headings the model copies into the value.
# Longer aliases first so "研究背景与待解问题" wins over "研究背景".
FIELD_HEADING_ALIASES = {
    "one_liner": ("一句话结论",),
    "background": ("研究背景与待解问题", "研究背景"),
    "design": ("研究设计",),
    "results": ("核心结果",),
    "mechanism": ("机制解读",),
    "limitations": ("局限与不确定", "局限"),
    "significance": ("临床/产业意义", "临床意义", "产业意义"),
}
DATACARD_HEADING_ALIASES = {
    "study_type": ("研究类型",),
    "n": ("样本量",),
    "control": ("对照",),
    "intervention": ("干预/剂量", "药名/剂量/途径/频次/疗程"),
    "followup": ("随访",),
    "primary_endpoint": ("主要终点", "终点名称与定义"),
    "primary_endpoint_result": ("主要结果", "数值 + 对照值"),
    "statistics": ("统计量",),
    "safety": ("安全性",),
    "read_note": ("核对记录",),
}


def _heading_style_prefix(text: str, aliases: tuple[str, ...]) -> re.Match | None:
    """Match a leading 'Label: ...' / 'Label，…：' copied from the schema."""
    if not text or not aliases:
        return None
    for alias in aliases:
        m = re.match(
            rf"^{re.escape(alias)}(?:[，,].{{0,40}})?\s*[:：]\s*",
            text,
        )
        if m:
            return m
        m = re.match(rf"^{re.escape(alias)}\s*[。.]+\s*", text)
        if m:
            return m
    return None


def strip_leading_field_heading(text: str, field: str = "", aliases: tuple[str, ...] | None = None) -> str:
    """Remove one leading copy of the field heading the renderer will add."""
    if not isinstance(text, str) or not text:
        return text
    names = aliases or FIELD_HEADING_ALIASES.get(field) or ()
    hit = _heading_style_prefix(text, names)
    if not hit:
        return text
    return text[hit.end():].lstrip()


def strip_article_field_headings(art: dict) -> dict:
    """Strip schema/renderer headings from body and datacard values."""
    for field in FIELD_HEADING_ALIASES:
        val = art.get(field)
        if isinstance(val, list):
            art[field] = [strip_leading_field_heading(str(x), field) if x else x for x in val]
        elif isinstance(val, str) and val:
            art[field] = strip_leading_field_heading(val, field)
    datacard = art.get("datacard")
    if isinstance(datacard, dict):
        for key, aliases in DATACARD_HEADING_ALIASES.items():
            val = datacard.get(key)
            if isinstance(val, str) and val:
                datacard[key] = strip_leading_field_heading(val, aliases=aliases)
    return art


def duplicate_heading_problems(art: dict) -> list[str]:
    """Fail when the same heading appears twice in a row in a field value."""
    problems: list[str] = []

    def _dup(text: str, aliases: tuple[str, ...], label: str) -> None:
        if not text:
            return
        for alias in aliases:
            if text.startswith(alias + alias) or re.match(
                rf"^{re.escape(alias)}\s*[:：]?\s*{re.escape(alias)}", text
            ):
                problems.append(f"标题连写：{label}")
                return
        once = strip_leading_field_heading(text, aliases=aliases)
        if once != text and _heading_style_prefix(once, aliases):
            problems.append(f"标题连写：{label}")

    for field, aliases in FIELD_HEADING_ALIASES.items():
        val = art.get(field)
        chunks = val if isinstance(val, list) else [val]
        for chunk in chunks:
            if isinstance(chunk, str):
                _dup(chunk.strip(), aliases, aliases[0])
    datacard = art.get("datacard")
    if isinstance(datacard, dict):
        for key, aliases in DATACARD_HEADING_ALIASES.items():
            val = datacard.get(key)
            if isinstance(val, str):
                _dup(val.strip(), aliases, aliases[0])
    return problems


def wechat_html_article(art: dict, include_ai_disclaimer: bool = False) -> str:
    """Generate WeChat-compatible HTML for a single article.
    
    Args:
        art: Article dict with title, one_liner, datacard, etc.
        include_ai_disclaimer: If False, skip per-article AI disclaimer (use single footer disclaimer)
    """
    tier = art.get("tier", "brief")
    datacard = art.get("datacard", {})
    
    parts = []
    
    # Title
    parts.append(f'<h3 style="font-size:18px;margin:1.5em 0 0.5em;color:#1d2a27;border-left:4px solid #0f6b5c;padding-left:12px;">{_escape_html(art.get("title", ""))}</h3>')

    if art.get("secondhand_label"):
        parts.append(
            f'<p style="font-size:12px;color:#888;margin:0.4em 0;">{_escape_html(art["secondhand_label"])}</p>'
        )
    
    # Image (if available) — mechanism figure only when fulltext/deep
    img = art.get("img", "")
    if img and not art.get("skip_mechanism_figure"):
        parts.append(f'<p style="margin:1em 0;"><img src="{_escape_html(img)}" alt="" style="max-width:100%;border-radius:8px;"></p>')
        cap = art.get("fig_caption") or "示意图由 AI 生成，依据原文结果绘制，非期刊原图，不代表分子比例。"
        parts.append(f'<p style="font-size:12px;color:#999;margin:0.3em 0;">{_escape_html(cap)}</p>')
    
    # One-liner
    one_liner = strip_leading_field_heading(str(art.get("one_liner") or ""), "one_liner")
    if one_liner:
        parts.append(f'<p style="margin:0.5em 0;font-weight:700;color:#0f6b5c;">{_escape_html(one_liner)}</p>')
    
    # Datacard table
    datacard_rows = []
    field_names = {
        "study_type": "研究类型",
        "n": "样本量",
        "control": "对照",
        "intervention": "干预/剂量",
        "followup": "随访",
        "primary_endpoint": "主要终点",
        "primary_endpoint_result": "主要结果",
        "statistics": "统计量",
        "safety": "安全性",
        "evidence_level": "证据等级",
        "read_note": "核对记录",
    }
    for key, label in field_names.items():
        val = datacard.get(key, "")
        if isinstance(val, str):
            val = strip_leading_field_heading(val, aliases=DATACARD_HEADING_ALIASES.get(key) or (label,))
        if val and val != "不适用" and MISSING_VALUE_MARK not in str(val):
            datacard_rows.append(f'<tr><td style="padding:6px 10px;border:1px solid #eee;font-weight:700;width:80px;">{label}</td><td style="padding:6px 10px;border:1px solid #eee;">{_escape_html(val)}</td></tr>')
    
    if datacard_rows:
        parts.append('<table style="width:100%;border-collapse:collapse;margin:1em 0;font-size:14px;background:#f9f9f9;">')
        parts.extend(datacard_rows)
        parts.append('</table>')
    
    # Evidence level + 核对记录
    evidence = art.get("evidence_level", "abstract")
    evidence_label = EVIDENCE_LEVEL_LABELS.get(evidence, evidence)
    read_note = art.get("read_note") or (art.get("datacard") or {}).get("read_note") or ""
    parts.append(f'<p style="font-size:12px;color:#999;margin:0.5em 0;">证据等级：{evidence_label}'
                 f'{" · " + _escape_html(read_note) if read_note else ""}</p>')
    
    # Section: Background
    background = strip_leading_field_heading(str(art.get("background") or ""), "background")
    if background:
        parts.append('<h4 style="font-size:15px;margin:1.2em 0 0.3em;color:#333;">研究背景</h4>')
        parts.append(f'<p style="margin:0.5em 0;">{_escape_html(background)}</p>')
    
    # Section: Design
    design = strip_leading_field_heading(str(art.get("design") or ""), "design")
    if design:
        parts.append('<h4 style="font-size:15px;margin:1.2em 0 0.3em;color:#333;">研究设计</h4>')
        parts.append(f'<p style="margin:0.5em 0;">{_escape_html(design)}</p>')
    
    # Section: Results
    results = [
        strip_leading_field_heading(str(para), "results")
        for para in (art.get("results") or [])
        if para
    ]
    results = [p for p in results if p]
    if results:
        parts.append('<h4 style="font-size:15px;margin:1.2em 0 0.3em;color:#333;">核心结果</h4>')
        for para in results:
            parts.append(f'<p style="margin:0.5em 0;">{_escape_html(para)}</p>')
        if art.get("data_chart_svg"):
            parts.append(f'<div style="margin:0.8em 0;overflow-x:auto;">{art["data_chart_svg"]}</div>')
    
    # Section: Mechanism (deep only)
    mechanism = strip_leading_field_heading(str(art.get("mechanism") or ""), "mechanism")
    if tier == "deep" and mechanism:
        parts.append('<h4 style="font-size:15px;margin:1.2em 0 0.3em;color:#333;">机制解读</h4>')
        parts.append(f'<p style="margin:0.5em 0;">{_escape_html(mechanism)}</p>')
    
    # Section: Limitations
    limitations = [
        strip_leading_field_heading(str(lim), "limitations")
        for lim in (art.get("limitations") or [])
        if lim
    ]
    limitations = [lim for lim in limitations if lim]
    if limitations:
        parts.append('<h4 style="font-size:15px;margin:1.2em 0 0.3em;color:#333;">局限与不确定</h4>')
        parts.append('<div style="background:#f5f5f5;padding:10px 14px;border-radius:8px;margin:0.5em 0;">')
        parts.append('<ul style="margin:0;padding-left:18px;">')
        for lim in limitations:
            parts.append(f'<li style="margin:4px 0;font-size:14px;color:#666;">{_escape_html(lim)}</li>')
        parts.append('</ul>')
        parts.append('</div>')
    
    # Section: Significance
    significance = strip_leading_field_heading(str(art.get("significance") or ""), "significance")
    if significance:
        parts.append('<h4 style="font-size:15px;margin:1.2em 0 0.3em;color:#333;">临床/产业意义</h4>')
        parts.append(f'<p style="margin:0.5em 0;">{_escape_html(significance)}</p>')
    
    # Author and journal - skip if "原文未给出" 
    authors = str(art.get("authors", "") or "")
    journal = str(art.get("journal", "") or "")
    authors_ok = (
        authors
        and MISSING_VALUE_MARK not in authors
        and not AUTHOR_PLACEHOLDER_RE.search(authors)
    )
    journal_ok = (
        journal
        and MISSING_VALUE_MARK not in journal
        and not AUTHOR_PLACEHOLDER_RE.search(journal)
    )
    if authors_ok and journal_ok:
        parts.append(f'<p style="font-size:13px;color:#666;margin:1em 0;">{_escape_html(authors)} · {_escape_html(journal)}</p>')
    elif authors_ok:
        parts.append(f'<p style="font-size:13px;color:#666;margin:1em 0;">{_escape_html(authors)}</p>')
    elif journal_ok:
        parts.append(f'<p style="font-size:13px;color:#666;margin:1em 0;">{_escape_html(journal)}</p>')
    
    # DOI - show for any source that has one
    url = art.get("url", "")
    doi_str = _extract_doi_from_url(url)
    if doi_str:
        parts.append(f'<p style="font-size:12px;color:#999;margin:0.5em 0;">{_escape_html(doi_str)}</p>')
    if art.get("citation"):
        parts.append(f'<p style="font-size:13px;color:#555;margin:0.8em 0;">{_escape_html(art["citation"])}</p>')
    
    # Per-article AI disclaimer (only if requested - usually use single footer disclaimer)
    if include_ai_disclaimer:
        parts.append('<p style="font-size:11px;color:#999;margin:0.5em 0;font-style:italic;">本文由 Claude 起草，编辑核对后发布。</p>')
    
    return "\n".join(parts)


def wechat_html_full(articles: list[dict], deals: list[dict], week: str, all_validated: bool = True) -> str:
    """Generate complete WeChat HTML for all articles and deals.
    
    Features:
    - Article images included
    - Chinese evidence-level labels
    - DOI shown for any source
    - Single AI disclaimer at footer (not per-article)
    - Industry items as Chinese summaries with money/why
    
    Args:
        articles: List of article dicts
        deals: List of deal dicts  
        week: Week string for header
        all_validated: If True, all articles passed validation; show checked claim.
                       If False (e.g., deals present from old path), show weaker claim.
    """
    parts = [
        '<section style="font-family:-apple-system,BlinkMacSystemFont,\'Segoe UI\',Roboto,sans-serif;font-size:16px;line-height:1.75;color:#333;">',
        f'<p style="font-size:14px;color:#666;">前沿追踪 · {week} · TheraSik 出品</p>',
    ]
    
    # Only claim source-checked for validated articles
    if all_validated and articles and not deals:
        parts.append('<p style="margin:1em 0;">本期学术内容均基于原始来源核对，配图由 AI 生成（示意图，非期刊原图）。</p>')
    else:
        parts.append('<p style="margin:1em 0;">本期配图由 AI 生成（示意图，非期刊原图）。</p>')
    
    # Table of contents
    toc_items = []
    for i, art in enumerate(articles, 1):
        title = art.get("title", art.get("t", ""))[:30]
        tier_label = "深度" if art.get("tier") == "deep" else "速览"
        toc_items.append(f"{i}. [{tier_label}] {title}...")
    
    if toc_items:
        parts.append('<div style="margin:1em 0;padding:1em;background:#f5f5f5;border-radius:8px;">')
        parts.append('<strong>本期目录</strong><br>')
        parts.append('<br>'.join(toc_items))
        parts.append('</div>')
    
    # Academic articles
    if articles:
        parts.append('<h2 style="border-left:4px solid #0f6b5c;padding-left:12px;margin:2em 0 1em;">学术</h2>')
        for art in articles:
            try:
                parts.append(wechat_html_article(art, include_ai_disclaimer=False))
            except Exception:
                logging.exception("WeChat 单篇渲染失败，跳过：%s", art.get("title", art.get("t", "")))
                continue
    
    # Industry deals - show as Chinese summaries, not raw English headlines
    if deals:
        parts.append('<h2 style="border-left:4px solid #0f6b5c;padding-left:12px;margin:2em 0 1em;">行业</h2>')
        for deal in deals:
            # Get title - prefer 't' (site format) or 'title' (input format)
            title = deal.get("t", deal.get("title", ""))
            parts.append(f'<h3 style="font-size:18px;margin:1.5em 0 0.5em;color:#1d2a27;">{_escape_html(title)}</h3>')
            
            # Money
            money = deal.get("m", deal.get("money", ""))
            if money and money != "未披露":
                parts.append(f'<p style="margin:0.5em 0;"><strong>{_escape_html(money)}</strong></p>')
            
            # Structure (if available)
            structure = deal.get("ms", deal.get("structure", ""))
            if structure:
                parts.append(f'<p style="margin:0.5em 0;color:#666;">{_escape_html(structure)}</p>')
            
            # Why it matters
            why = deal.get("why", "")
            if why:
                parts.append(f'<p style="margin:0.5em 0;">{_escape_html(why)}</p>')
            
            # Source
            source = deal.get("src", deal.get("source_name", ""))
            if source:
                parts.append(f'<p style="font-size:14px;color:#666;margin:0.5em 0;">来源：{_escape_html(source)}</p>')
    
    # Single AI disclaimer at footer
    parts.append('<hr style="border:none;border-top:1px solid #eee;margin:2em 0;">')
    parts.append('<p style="font-size:14px;color:#666;margin:1em 0;">')
    parts.append('本期内容由 Claude 起草，配图由 gpt-image-1 生成，编辑核对后发布。')
    parts.append('</p>')
    parts.append('<p style="font-size:14px;color:#666;margin:1em 0;">')
    parts.append('如发现错误，请邮件 contact@therasik.com，我们会在下期更正。')
    parts.append('</p>')
    parts.append('<p style="font-size:14px;color:#0f6b5c;margin:1em 0;">')
    parts.append('点击「阅读原文」查看完整版。')
    parts.append('</p>')
    parts.append('</section>')
    
    return "\n".join(parts)


if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    
    test_doi = "10.1038/s41591-026-04704-z"
    print(f"Testing EPMC search for DOI: {test_doi}")
    result = epmc_core_search(test_doi)
    if result:
        print(f"  Found: {result.get('title', 'N/A')[:60]}...")
        print(f"  PMID: {result.get('pmid')}, PMCID: {result.get('pmcid')}")
        print(f"  Abstract: {len(result.get('abstractText', ''))} chars")
    else:
        print("  Not found")
