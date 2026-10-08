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

# Chinese display name -> source-language forms that justify publishing it.
DRUG_CN_TO_SOURCE = {
    "中康酸": ("mesaconate", "mesaconic acid"),
    "莫妥珠单抗": ("mosunetuzumab",),
    "格菲妥单抗": ("glofitamab",),
    "特瑞普利单抗": ("toripalimab",),
    "普特利单抗": ("pucotenlimab",),
    "普可欣": ("pucotenlimab",),
    "帕博利珠单抗": ("pembrolizumab", "keytruda"),
    "派姆单抗": ("pembrolizumab",),
    "纳武利尤单抗": ("nivolumab", "opdivo"),
    "替雷利珠单抗": ("tislelizumab",),
    "信迪利单抗": ("sintilimab",),
    "卡瑞利珠单抗": ("camrelizumab",),
    "度伐利尤单抗": ("durvalumab",),
    "阿替利珠单抗": ("atezolizumab",),
    "伊匹木单抗": ("ipilimumab",),
    "利妥昔单抗": ("rituximab",),
    "曲妥珠单抗": ("trastuzumab",),
    "贝伐珠单抗": ("bevacizumab",),
    "西妥昔单抗": ("cetuximab",),
    "帕妥珠单抗": ("pertuzumab",),
    "奥妥珠单抗": ("obinutuzumab",),
    "维泊妥珠单抗": ("polatuzumab", "polatuzumab vedotin"),
    "兰瑞肽": ("lanreotide",),
}

INST_CN_TO_SOURCE = {
    "中山大学肿瘤防治中心": ("sun yat-sen", "sysucc", "zhongshan university cancer"),
    "北京大学": ("peking university", "beijing university"),
    "清华大学": ("tsinghua",),
    "复旦大学": ("fudan",),
    "上海交通大学": ("shanghai jiao tong", "sjtu"),
    "中国医学科学院": ("chinese academy of medical", "cams"),
    "哈佛": ("harvard",),
    "斯坦福": ("stanford",),
    "纪念斯隆凯特琳": ("memorial sloan", "mskcc"),
    "md安德森": ("md anderson", "m.d. anderson"),
}

AUTHOR_PLACEHOLDER_RE = re.compile(
    r"原文未提供|原文未列出|原文未报告作者|未提供作者|未列出作者|作者信息"
)
PREPRINT_SOURCE_RE = re.compile(r"biorxiv|medrxiv", re.IGNORECASE)

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
    source_trace: list[str] = field(default_factory=list)


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


def epmc_core_search(doi: str) -> dict | None:
    """Search Europe PMC core API by DOI.
    
    Returns dict with keys: abstractText, pmid, pmcid, isOpenAccess, title, etc.
    """
    if not doi:
        return None
    query = f'DOI:"{doi}"'
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
        logging.warning("EPMC parse error for %s: %s", doi, e)
    return None


def epmc_fulltext_xml(pmcid: str) -> str | None:
    """Fetch OA full text XML from Europe PMC."""
    if not pmcid:
        return None
    url = f"https://www.ebi.ac.uk/europepmc/webservices/rest/{pmcid}/fullTextXML"
    data = _http_get(url, timeout=60)
    if data:
        return data.decode("utf-8", errors="replace")
    return None


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
    """
    if not xml_text:
        return ""
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return ""
    
    sections = []
    for sec in root.iter("sec"):
        title_elem = sec.find("title")
        if title_elem is not None and title_elem.text:
            title = title_elem.text.strip().lower()
            if any(name.lower() in title for name in section_names):
                text_parts = []
                for p in sec.iter("p"):
                    para_text = element_text_with_superscripts(p).strip()
                    if para_text:
                        text_parts.append(para_text)
                sections.append(" ".join(text_parts))
    return "\n\n".join(sections)


def extract_fig_captions_from_xml(xml_text: str, max_chars: int = 6000) -> str:
    """Extract figure captions from PMC XML.
    
    Uses itertext() to properly handle nested elements.
    """
    if not xml_text:
        return ""
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return ""
    
    captions = []
    for fig in root.iter("fig"):
        cap = fig.find("caption")
        if cap is not None:
            cap_text = element_text_with_superscripts(cap).strip()
            if cap_text:
                captions.append(cap_text)
    
    result = "\n\n".join(captions)
    return result[:max_chars]


def extract_design_methods_from_xml(xml_text: str, max_chars: int = 4000) -> str:
    """Extract study design related methods from PMC XML.
    
    Uses itertext() to properly handle nested elements.
    """
    if not xml_text:
        return ""
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return ""
    
    design_keywords = [
        "study design", "patient", "participant", "cohort", "sample",
        "randomiz", "blind", "control", "endpoint", "inclusion", "exclusion",
        "dose", "dosing", "treatment", "intervention", "cell line", "mouse", "mice",
        "animal", "in vivo", "in vitro"
    ]
    
    sections = []
    for sec in root.iter("sec"):
        title_elem = sec.find("title")
        if title_elem is not None and title_elem.text:
            title = title_elem.text.strip().lower()
            if "method" in title or "material" in title:
                for subsec in sec.iter("sec"):
                    subsec_title = subsec.find("title")
                    if subsec_title is not None and subsec_title.text:
                        subsec_text = subsec_title.text.lower()
                        if any(kw in subsec_text for kw in design_keywords):
                            text_parts = []
                            for p in subsec.iter("p"):
                                para_text = element_text_with_superscripts(p).strip()
                                if para_text:
                                    text_parts.append(para_text)
                            if text_parts:
                                sections.append(" ".join(text_parts))
    
    result = "\n\n".join(sections)
    return result[:max_chars]


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


def scrape_biorxiv_fulltext(url: str) -> str:
    """Get bioRxiv/medRxiv abstract if available."""
    return fetch_biorxiv_record(url).get("abstract", "")


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
                item.fulltext_results = extract_sections_from_xml(xml, ("Results", "Discussion"))[:20000]
                item.fig_captions = extract_fig_captions_from_xml(xml)
                item.methods_design = extract_design_methods_from_xml(xml)
                if item.fulltext_results or item.fig_captions:
                    item.evidence_level = "fulltext"
                    item.source_trace.append(f"PMC fulltext: Results {len(item.fulltext_results)} chars, Figs {len(item.fig_captions)} chars")
    
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

    if abstracts:
        label, text = max(abstracts, key=lambda x: len(x[1]))
        item.abstract = text
        if item.evidence_level != "fulltext":
            item.evidence_level = "abstract"
        item.source_trace.append(
            f"{label}: {len(text)} chars (longest of {len(abstracts)} sources)"
        )
    elif item.rss_summary:
        item.abstract = item.rss_summary
        item.evidence_level = "press"
        item.source_trace.append("Fallback to RSS summary")

    if "biorxiv.org" in item.url or "medrxiv.org" in item.url:
        if item.evidence_level in ("abstract", "press"):
            item.evidence_level = "preprint"
        if not item.journal:
            item.journal = (
                "medRxiv（预印本）" if "medrxiv.org" in item.url else "bioRxiv（预印本）"
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
                    "n": {"type": "string", "description": "样本量，写清单位与分组；无则写'原文未给出'"},
                    "control": {"type": "string"},
                    "intervention": {"type": "string", "description": "药名/剂量/途径/频次/疗程"},
                    "followup": {"type": "string"},
                    "primary_endpoint": {"type": "string", "description": "终点名称与定义"},
                    "primary_endpoint_result": {"type": "string", "description": "数值 + 对照值；无则写'原文未给出'"},
                    "statistics": {"type": "string", "description": "HR/OR/95%CI/P；无则写'原文未报告统计学检验'"},
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
                    },
                    "required": ["value", "meaning", "source_quote"],
                },
                "minItems": 1,
            },
            "unknowns": {
                "type": "array",
                "description": "来源中确实缺失的要素，逐项列出，如'原文未给出中位随访时长'",
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


def build_triage_prompt(items: list[EnrichedItem], config: dict) -> str:
    """Build prompt for triage stage."""
    max_deep = config.get("max_deep", 3)
    max_brief = config.get("max_brief", 6)
    max_industry = config.get("max_industry", 4)
    
    items_json = []
    for item in items:
        items_json.append({
            "url": item.url,
            "title": item.title,
            "source": item.source,
            "date": item.date,
            "kind": item.kind,
            "evidence_level": item.evidence_level,
            "abstract_preview": item.abstract[:500] if item.abstract else item.rss_summary[:500],
        })
    
    return f"""你是前沿追踪的选题编辑。下面是本周抓到的条目，请挑选最重要的进入本期周报。

## 选题规则

1. 最多选 {max_deep} 篇深度解读（tier=deep），必须有 fulltext 或 abstract 级别的证据
2. 最多选 {max_brief} 篇论文速览（tier=brief）
3. 最多选 {max_industry} 条行业动态（tier=industry）
4. evidence_level 为 press/secondary 的条目只能选为 brief 或 industry，不能选为 deep
5. 优先选择：临床试验结果、首次人体数据、平台级方法突破、有开放获取全文的重要发现

## 领域分类

{json.dumps(FIELDS, ensure_ascii=False)}

分类基于研究的主要对象，而非使用的工具。

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
        return "\n".join(parts)
    if rss:
        return rss[:8000]
    return "无"


def build_article_prompt(item: EnrichedItem, tier: str) -> str:
    """Build prompt for single article drafting."""
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
6. **不要断言原文没有的事情不存在**。如果原文没有提到人体试验数据，只能写「原文未报告人体数据」，不能写「未涉及人体」或「未在人体验证」。
   后者暗示研究故意不涉及人体，但实际上可能只是摘要没有报告。

## 材料等级（已由系统判定，不要自行改写）

evidence_level = {item.evidence_level}
- fulltext：有开放获取全文（Results / 图注 / 部分 Methods），可以写到实验级细节。
- abstract：只有摘要，正文深度到摘要为止；凡摘要没有的细节，在 unknowns 里列出来。
- press / secondary：只有新闻稿或二手转述。这种材料**不要写成解读**，tier 填 "brief"，
  并在 limitations 第一条写明「本条未读原文，信息来自 {{source_name}}」。

## 写作档次

tier = "{tier}"
- deep（正文 1400–1900 字）：背景 180-240 + 设计 200-280 + 结果 500-700（3-5段）+ 机制 250-350 + 局限 200-280（≥3条）+ 意义 150-220
- brief（正文 450–650 字）：背景 50-80 + 设计 60-90 + 结果 180-280（1-2段）+ 局限 60-100（≥1条）+ 意义 60-100

evidence_level 为 press/secondary 时只能填 brief。

## deep 档要求

- 标题：20–40 字，结论式，含一个关键数字或关键对比。不要用「重磅」「颠覆」「突破」「首次」（除非材料明写 first-in-human / first report）。
- one_liner 一句话结论：40–70 字，必须包含「什么对象 / 什么模型 + 做了什么 + 得到什么量化结果」。
- results 核心结果：3–5 段，合计 500–700 字。**每段讲一个实验或一个终点，每段至少写出一个具体数字。**
  主要终点那一段必须给：绝对值 + 对照组数值 + 统计量（HR/OR/95%CI/P）。
  材料未报告统计检验的，写「原文未报告统计学检验」。
- limitations 局限与不确定：至少 3 条，合计 200–280 字。每条都要具体，必须覆盖以下三类中的至少两类：
  ① 外推性（物种、人群、样本量、单中心、无对照、剂量未优化）
  ② 终点与随访（替代终点、随访过短、未按疗效设定检验效能、开放标签）
  ③ 未报告项（材料没写的终点、随访、统计量——只写材料里实际缺的内容，不要写「未给出」）
  禁止写「仍需更多研究验证」「期待后续大样本研究」这类空话。

## brief 档要求

背景一句（50–80 字）→ 设计一句（60–90 字，必须含研究类型与 n）→ 结果 2–3 句（180–280 字，至少两个数字）
→ 局限一句（60–100 字，不得为空）→ 意义一句（60–100 字）。

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

摘要：
{_article_prompt_abstract(item)}

全文结果与讨论（若有）：
{item.fulltext_results[:15000] if item.fulltext_results else '无'}

图注（若有）：
{item.fig_captions[:4000] if item.fig_captions else '无'}

研究设计相关方法（若有）：
{item.methods_design[:3000] if item.methods_design else '无'}

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
    
    # Extract the numeric part
    match = re.search(r'(\d+(?:\.\d+)?)', text)
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
    unit_re = re.compile(
        r'(?:mg|kg|mL|µg|nM|pM|µM|mM|μg|μL|ng|pg|mmol|mol|g|L|%|％|倍|年|个月|天|周|小时|例|名)',
        re.IGNORECASE,
    )
    for match in re.finditer(re.escape(number_clean), text_clean):
        start, end = match.start(), match.end()
        prev = text_clean[start - 1] if start else ""
        prev2 = text_clean[start - 2] if start >= 2 else ""
        if prev == "-" and prev2.isalpha():
            continue
        if prev and (prev.isalnum() or prev == "."):
            continue
        after = text_clean[end:]
        if unit_re.match(after) or re.match(r'-\s*\d', after):
            return True
        nxt = after[:1]
        if nxt == "." and after[1:2].isdigit():
            continue
        if nxt.isalnum():
            continue
        return True
    return False


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
    
    # Gene names: CD4, CD8, CD14, CD318, IL-23, IFN-α2, HLA-DP04, NK, NF-κB
    for match in re.finditer(r'\b(?:CD|IL|HLA|IFN|NK|NF|CCR|Th|TAK|CCL|CXCL|CXCR|ROR)[A-Za-zα-ω]?-?[A-Za-z0-9αβγδ/-]*\d+[A-Za-z0-9αβγδ/-]*\b', source, re.IGNORECASE):
        identifiers.add(match.group(0))
    
    # Element/family names: R2, S1, M1
    for match in re.finditer(r'\b[A-Z]\d+\b', source):
        identifiers.add(match.group(0))
    
    # Mouse lines: SAMP1/YitFC, TNFΔARE, Rag2-/-
    for match in re.finditer(r'\b[A-Z][A-Za-z0-9Δ]*\d[A-Za-z0-9Δ/-]*(?:/[A-Za-z0-9Δ/-]+)?\b', source):
        identifiers.add(match.group(0))
    
    # Strain / line designations: a capitalized genus or strain name + number
    # (Nissle 1917, BALB 3, etc.) — not a one-strain special case.
    for match in re.finditer(r'\b[A-Z][a-z]{2,}\s+\d+\b', source):
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
_IDENTIFIER_TOKEN_RE = re.compile(
    r'(?i)(?:'
    # Gene/protein/strain/compound tokens: letters + digits (Dsg2, CD14, p38, MK-25)
    r'(?<![A-Za-z0-9])[A-Za-z][A-Za-z]{0,10}-?\d+[A-Za-z0-9./-]*'
    r'|(?:NCT|RPCEC|ISRCTN|EudraCT|ACTRN|ChiCTR)\d+'
    r'|[A-Z][a-z]{2,}\s+\d+'
    r')'
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
BRIEF_HAN_MAX = 900
SEE_BODY_RE = re.compile(r"详见正文")


def identifier_spans(text: str) -> list[tuple[int, int]]:
    """Character spans of identifier tokens (CD318, IL-6, p38, NCT…)."""
    if not text:
        return []
    return [m.span() for m in _IDENTIFIER_TOKEN_RE.finditer(text)]


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
    if re.match(r'[\s]*(?:组|臂|项|groups?|arms?|cohorts?)', after):
        return NOUN_GROUP
    if re.match(r'[\s]*(例|名|位|patients?|subjects?|participants?|cases?|患者|病人)', after):
        return NOUN_PATIENT
    return None


def classify_unit_in_context(context: str, num_core: str) -> str | None:
    """Unit class claimed by a non-identifier occurrence of num_core.

    Identifier digits (the 6 in IL-6) are ignored so a nearby "6例" still
    classifies as a count.
    """
    if not context or not num_core:
        return None
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
        if prev.isalnum() or prev == ".":
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
    return False


def _source_has_once_or_one(num_core: str, context: str, source: str) -> bool:
    """一次 / 一例 are the same claim as 'once' / 'a/one patient'."""
    if num_core != "1" or not context or not source:
        return False
    ctx = context.lower()
    src = source.lower()
    if re.search(r'一次', ctx) and re.search(
        r'\bonce\b|\bevery\s+\d+|\bone\s+time|\ba\s+time', src
    ):
        return True
    if re.search(r'一[例名位]|1[例名位]', ctx) and re.search(
        r'\b(?:a|one|1)\s+(?:patient|case|subject|participant)', src
    ):
        return True
    return False


def _source_has_grouping(num_core: str, source: str) -> bool:
    """N组 / N项 / N臂 must be backed by N groups/arms/cohorts in the source."""
    if not num_core or not source:
        return False
    src = source.lower()
    return bool(re.search(
        rf'(?<![a-zA-Z0-9.]){re.escape(num_core)}\s*'
        rf'(?:groups?|arms?|cohorts?|组|臂|项|队列)|'
        rf'(?:randomized|randomised|randomly|divided|assigned)'
        rf'.{{0,40}}(?<![a-zA-Z0-9.]){re.escape(num_core)}',
        src,
        re.IGNORECASE,
    ))


def _noun_mismatch(num_core: str, context: str, source: str) -> bool:
    """True when the output counts a different thing than the source."""
    if not num_core or not context or not source:
        return False
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
        if prev.isalnum() or prev == ".":
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
    
    # Identifiers in the source (CD318, IL-6, NCT…) are never evidence for a
    # claimed number. A nearby "CD318" must not justify "31例".
    # Digits that are themselves an identifier token are skipped earlier
    # by extract_numbers_with_context.

    # Honest unit conversions (21 days ↔ 3 weeks, 1 year ↔ 12 months).
    # Weeks are never months.
    if source_has_equivalent_number(num_core, context_window or "", source_norm):
        return True

    raw = source_raw if source_raw is not None else source_norm
    ctx = context_window or ""
    out_class = classify_unit_in_context(ctx, num_core)

    if _source_has_date(num_core, ctx, raw) or _source_has_date(num_core, ctx, source_norm):
        return True
    if _source_has_grade_or_schedule(num_core, ctx, raw) or _source_has_grade_or_schedule(num_core, ctx, source_norm):
        return True
    if _source_has_once_or_one(num_core, ctx, raw) or _source_has_once_or_one(num_core, ctx, source_norm):
        return True
    if re.search(r'组|臂|项', ctx) and not (
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
    r'每\s*(周|天|日|月)\s*[一二三四五六七八九十\d]+\s*次',
    r'每\s*\d+\s*(天|日|周|月)\s*一次',
    r'every\s+\d+\s+(?:days?|weeks?|months?)',
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


def closest_metric_class(text: str, num_start: int) -> str | None:
    """Single metric class attached to this number (nearest, then longest)."""
    if not text or num_start < 0:
        return None
    window_start = max(0, num_start - 40)
    window_end = min(len(text), num_start + 24)
    window = text[window_start:window_end]
    lower = window.lower()
    found: list[tuple[int, int, str]] = []
    for cls, kw in METRIC_CLASS_KEYWORDS:
        if kw in _SHORT_METRIC_KEYWORDS:
            for m in re.finditer(r'(?<![a-z])' + re.escape(kw) + r'(?![a-z])', lower):
                abs_pos = window_start + m.start()
                dist = min(abs(abs_pos - num_start), abs(window_start + m.end() - num_start))
                found.append((dist, -len(kw), cls))
        else:
            start = 0
            while True:
                idx = lower.find(kw, start)
                if idx < 0:
                    break
                abs_pos = window_start + idx
                dist = min(abs(abs_pos - num_start), abs(abs_pos + len(kw) - num_start))
                found.append((dist, -len(kw), cls))
                start = idx + 1
    if not found:
        return None
    found.sort()
    return found[0][2]


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
        if prev.isalnum() or prev == ".":
            continue
        nxt = source[m.end():m.end() + 1]
        nxt2 = source[m.end() + 1:m.end() + 2] if m.end() + 1 < len(source) else ""
        if nxt.isdigit() or (nxt == "." and nxt2.isdigit()):
            continue
        spans.append(m)
    return spans


def number_meaning_matches_source(num_str: str, output_context: str, source_text: str) -> tuple[bool, str]:
    """Check that the unit and metric attached to a number match the source.

    Count vs percent is read from the token on the number itself, not from a
    nearby '%'. Metric class is the closest label on each side.
    """
    num_core = extract_number_core(num_str)
    if not num_core:
        return True, ""

    id_spans = identifier_spans(output_context)
    claimed_match = None
    for num_match in re.finditer(re.escape(num_core), output_context):
        if span_covers(num_match.start(), num_match.end(), id_spans):
            continue
        claimed_match = num_match
        break
    if not claimed_match:
        return True, ""

    out_unit = classify_unit_after(output_context, claimed_match.end())
    source_norm = source_text.lower()
    src_matches = _source_number_spans(num_core, source_norm)
    if out_unit in (UNIT_COUNT, UNIT_RATE) and src_matches:
        src_units = [classify_unit_after(source_norm, m.end()) for m in src_matches]
        src_units = [u for u in src_units if u is not None]
        if src_units:
            if out_unit == UNIT_COUNT and UNIT_RATE in src_units and UNIT_COUNT not in src_units:
                return False, f"数字 '{num_str}' 含义不匹配：单位不匹配，输出为人数（例/名），原文为百分比（%）"
            if out_unit == UNIT_RATE and UNIT_COUNT in src_units and UNIT_RATE not in src_units:
                return False, f"数字 '{num_str}' 含义不匹配：单位不匹配，输出为百分比（%），原文为人数（例/名）"

    out_class = closest_metric_class(output_context, claimed_match.start())
    if out_class and src_matches:
        src_classes = {
            closest_metric_class(source_norm, m.start())
            for m in src_matches
        }
        src_classes.discard(None)
        if src_classes and out_class not in src_classes:
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
            for _cls, kw in METRIC_CLASS_KEYWORDS:
                if kw in _SHORT_METRIC_KEYWORDS:
                    if not re.search(r'(?<![a-z])' + re.escape(kw) + r'(?![a-z])', sent.lower()):
                        continue
                elif kw not in sent.lower():
                    continue
                for m in re.finditer(re.escape(kw), src.lower()):
                    windows.append(src[max(0, m.start() - 48):m.end() + 48])
        if not windows:
            continue
        src_hi = any(_DIR_HIGHER.search(w) for w in windows)
        src_lo = any(_DIR_LOWER.search(w) for w in windows)
        if hi and src_lo and not src_hi:
            problems.append(f"比较方向不匹配：输出写更高/延长，原文为更低/缩短（{sent.strip()[:40]}）")
            break
        if lo and src_hi and not src_lo:
            problems.append(f"比较方向不匹配：输出写更低/缩短，原文为更高/延长（{sent.strip()[:40]}）")
            break
    return problems


def extract_numbers_with_context(text: str) -> list[tuple[str, str]]:
    """Extract numbers from text with their surrounding context.
    
    Returns list of (number_string, context_window) tuples.
    Context window is ~20 chars before and after for unit/meaning verification.
    """
    results = []
    
    # Arabic numbers with optional units
    number_pattern = r'(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?(?:\s*(?:%|％|倍|年|个月|天|周|小时|例|名|mg|kg|mL|µg|nM|pM|µM|mM|μg|μL))?'
    id_spans = identifier_spans(text)
    for match in re.finditer(number_pattern, text):
        # Skip digits that live inside an identifier token (CD318, p38, NCT…)
        if span_covers(match.start(), match.end(), id_spans):
            continue
        num = match.group(0)
        start = max(0, match.start() - 20)
        end = min(len(text), match.end() + 20)
        context = text[start:end]
        results.append((num, context))
    
    return results


def extract_chinese_numbers_with_context(text: str) -> list[tuple[str, str]]:
    """Extract Chinese numerals with their surrounding context.
    
    Handles: 一二三四五六七八九十百千万亿两
    With units: 年|倍|%|％|个月|天|周|小时|例|名|位|人|剂|次|万|亿
    """
    results = []
    
    # Chinese numerals with units, plus classifier nouns (三组, 两项, 两臂)
    cn_data_pattern = (
        r'[零一二三四五六七八九十百千万亿两]+(?:多)?'
        r'(?:年|倍|%|％|个月|天|周|小时|例|名|位|人|剂|次|万|亿|组|项|臂|份|条|个)'
    )
    for match in re.finditer(cn_data_pattern, text):
        cn_num = match.group(0)
        start = max(0, match.start() - 20)
        end = min(len(text), match.end() + 20)
        context = text[start:end]
        results.append((cn_num, context))
    
    return results


def validate_depth(art: dict, raw_material: str) -> list[str]:
    """Validate generated article against SOURCE TEXT.
    
    Design principle: Every number in output must exist in source.
    No special-casing of "原文未给出" - text saying something is not given
    is fine only if it contains no number not in source.
    
    Identifiers (R2, Th17, CCR8, etc.) that appear verbatim in source
    are allowed automatically - never ask the model to rename them.
    """
    problems = []
    tier = art.get("tier", "brief")
    
    # Normalize source for comparison
    source_norm = normalize_source_text(raw_material)
    source_raw = normalize_source_text(raw_material, convert_english_words=False)
    
    # Extract identifiers from source (these are allowed to have digits)
    source_identifiers = extract_identifiers_from_source(raw_material)
    
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
    if isinstance(datacard, dict):
        for field_val in datacard.values():
            if isinstance(field_val, str):
                all_text_parts.append(field_val)
    elif isinstance(datacard, str):
        all_text_parts.append(datacard)
    all_text = " ".join(all_text_parts)
    
    # Marketing words check on ALL text
    if MARKETING_BLOCKLIST.search(all_text):
        problems.append("文章含有营销词汇（重磅/颠覆/震撼等）")

    # Identifier tokens in the output (NCT…, IL-6, CD19, …) must occur in
    # the source. Skipping their digits as claimed numbers must not let an
    # invented registry ID through.
    src_id_keys = {
        re.sub(r'[\s-]+', '', m.group(0).lower())
        for m in _IDENTIFIER_TOKEN_RE.finditer(raw_material)
    }
    src_lower = raw_material.lower()
    seen_ids: set[str] = set()
    for match in _IDENTIFIER_TOKEN_RE.finditer(all_text):
        tok = match.group(0)
        key = re.sub(r'[\s-]+', '', tok.lower())
        if key in seen_ids:
            continue
        seen_ids.add(key)
        if key in src_id_keys or tok.lower() in src_lower:
            continue
        problems.append(f"标识符 '{tok}' 在原始材料中未找到")
    
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
            problems.append(f"data_point value 必须包含数字：'{value}'")
            continue
        
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
        
        # Check value appears in quote. Unit spacing is ignored so
        # "100mg" matches a quote that says "100 mg".
        value_core = extract_number_core(value)
        if value_core:
            quote_for_check = normalize_unit_spacing(
                chinese_numeral_to_arabic(english_number_to_arabic(quote))
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
            problems.append(f"数字 '{num}' 在原始材料中未找到")
        else:
            # Number exists - also check if meaning matches
            meaning_ok, meaning_reason = number_meaning_matches_source(num, context, raw_material)
            if not meaning_ok:
                problems.append(meaning_reason)
    
    # Extract Chinese numerals with context (万/亿 included)
    for cn_num, context in extract_chinese_numbers_with_context(all_text):
        arabic = chinese_numeral_to_arabic(cn_num)
        arabic_core = extract_number_core(arabic)
        if not arabic_core:
            continue
        
        # Check if this number is in an exempt context
        if is_exempt_number_context(context, arabic_core):
            continue
        
        if re.search(r'组|臂|项', cn_num) and not (
            _source_has_grouping(arabic_core, source_norm_units)
            or _source_has_grouping(arabic_core, source_raw_units)
        ):
            problems.append(f"中文数字 '{cn_num}' ({arabic}) 在原始材料中未找到")
            continue
        if not number_exists_in_source(
            arabic, source_norm_units, source_identifiers, context,
            source_raw=source_raw_units,
        ):
            problems.append(f"中文数字 '{cn_num}' ({arabic}) 在原始材料中未找到")
        else:
            # Chinese number exists - also check meaning
            meaning_ok, meaning_reason = number_meaning_matches_source(cn_num, context, raw_material)
            if not meaning_ok:
                problems.append(meaning_reason)
    
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
    
    # Hard limits: brief ≥ 450 Han; deep ≤ 1900 Han
    if tier == "deep":
        if total_chars > 1900:
            problems.append(f"deep 档正文 {total_chars} 汉字，超过上限 1900 字")
        elif total_chars < 450:
            problems.append(f"deep 档正文 {total_chars} 汉字，低于下限 450 字")
    else:
        if total_chars < 450:
            problems.append(f"brief 档正文 {total_chars} 汉字，低于下限 450 字")
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
    for num, context in results_claims:
        num_core = extract_number_core(num) or extract_number_core(
            chinese_numeral_to_arabic(num)
        )
        if not num_core:
            continue
        if is_exempt_number_context(context, num_core):
            continue
        if number_exists_in_source(
            num, source_norm_units, source_identifiers, context,
            source_raw=source_raw_units,
        ):
            found_valid_number = True
            break

    def _is_bibliographic_number(num: str, context: str) -> bool:
        """Dates, years and DOI fragments are not study data."""
        ctx = (context or "").lower()
        if re.search(r'doi|published online|volume|pages?|issn', ctx):
            return True
        core = extract_number_core(num) or ""
        if re.fullmatch(r'(?:19|20)\d{2}', core):
            return True
        return False

    source_has_standalone = any(
        not _is_bibliographic_number(num, ctx)
        for num, ctx in extract_numbers_with_context(raw_material)
    )
    if source_has_standalone:
        if results_claims and not found_valid_number:
            problems.append("结果字段中的数字无法在原文中核实")
        elif not results_claims:
            problems.append("结果字段应包含至少一个可核实的数字（来自原文）")

    problems.extend(check_comparison_direction(all_text, raw_material))
    
    return problems


_GENE_ACRONYM_ALLOW = {
    "ORR", "DCR", "CRR", "PFS", "DFS", "EFS", "DOR", "CBR", "BOR",
    "SAE", "TEAE", "TRAE", "CRS", "DLT", "AUC", "FDA", "NIH", "WHO",
    "EMA", "DNA", "RNA", "MRNA", "PCR", "HIV", "HBV", "HCV", "HPV",
    "EBV", "CMV", "MHC", "HLA", "APC", "TCR", "BCR", "CAR", "NCT",
    "DOI", "PMID", "PMC", "USA", "UK", "EU", "COVID", "IFN", "TNF",
    "IL", "NK", "DC", "OS", "HR", "OR", "RR", "CI", "AE", "CR", "PR",
}


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

    # 3. Chinese drug / biologic names: accept only if the source form is present
    seen_cn_drugs: set[str] = set()
    for match in re.finditer(r'[\u4e00-\u9fff]{1,8}(?:单抗|替尼|利单抗|珠单抗|昔单抗|妥单抗)', all_text):
        seen_cn_drugs.add(match.group(0))
    seen_cn_drugs.update(cn for cn in DRUG_CN_TO_SOURCE if cn in all_text)
    for cn in seen_cn_drugs:
        if cn in raw_material:
            continue
        aliases = DRUG_CN_TO_SOURCE.get(cn, ())
        if any(_source_has_name_form(a, norm) for a in aliases):
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
    inst_pat = r'[\u4e00-\u9fff]{2,8}(?:大学|医院|医学院|肿瘤防治中心|附属医院|研究所|研究院)'
    seen_inst: set[str] = set()
    for match in re.finditer(inst_pat, all_text):
        inst = re.sub(r'^(?:研究)?(?:由|在|于|来自)', '', match.group(0))
        if inst in seen_inst or inst in ("单中心", "多中心", "中心数") or len(inst) < 4:
            continue
        seen_inst.add(inst)
        if inst in raw_material or _source_has_name_form(inst, norm):
            continue
        aliases = ()
        for stem, forms in INST_CN_TO_SOURCE.items():
            if stem in inst or inst in stem:
                aliases = forms
                break
        if aliases and any(_source_has_name_form(a, norm) for a in aliases):
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


def triage_items(items: list[EnrichedItem], config: dict) -> list[dict]:
    """Run triage to select items and assign tiers.
    
    Returns list of {url, tier, field} dicts.
    """
    from anthropic import Anthropic
    
    prompt = build_triage_prompt(items, config)
    model = os.environ.get("ANTHROPIC_TRIAGE_MODEL", os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-5-5"))
    
    logging.info("Triage: calling %s for %d items", model, len(items))
    
    client = Anthropic()
    message = client.messages.create(
        model=model,
        max_tokens=2000,
        tools=[TRIAGE_TOOL_SCHEMA],
        tool_choice={"type": "auto"},
        messages=[{"role": "user", "content": prompt}],
    )
    
    for block in message.content:
        if block.type == "tool_use" and block.name == "submit_triage":
            selections = block.input.get("selections", [])
            logging.info("Triage selected %d items", len(selections))
            return selections
    
    logging.warning("Triage did not return tool_use, using fallback")
    return []


def draft_single_article(item: EnrichedItem, tier: str, config: dict, problems: list[str] = None) -> dict | None:
    """Draft a single article using Claude.
    
    Args:
        item: The enriched item to draft
        tier: "deep" or "brief"
        config: Pipeline config
        problems: Optional list of problems from previous attempt (for targeted redraft)
    
    Returns the article dict or None on failure.
    """
    from anthropic import Anthropic
    
    prompt = build_article_prompt(item, tier)
    
    # If we have problems from a previous attempt, include them
    if problems:
        prompt += f"\n\n## 上次生成的问题（请务必修正）\n\n" + "\n".join(f"- {p}" for p in problems)
    
    prompt += "\n\n请务必调用 submit_article 工具提交你的文章。"
    
    model = os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-5-5")
    # Use higher token limits to avoid thinking consuming the budget
    max_tokens = 16000 if tier == "deep" else 8000
    
    logging.info("Drafting %s article for: %s", tier, item.title[:50])
    
    client = Anthropic()
    
    try:
        message = client.messages.create(
            model=model,
            max_tokens=max_tokens,
            tools=[ARTICLE_TOOL_SCHEMA],
            tool_choice={"type": "auto"},
            messages=[{"role": "user", "content": prompt}],
        )
    except Exception as e:
        logging.error("API error drafting article for %s: %s", item.title[:50], e)
        return None
    
    # Check for max_tokens truncation - retry with bigger budget
    if message.stop_reason == "max_tokens":
        logging.warning("Article draft truncated (max_tokens), retrying with larger budget: %s", item.title[:50])
        bigger_max_tokens = 24000 if tier == "deep" else 12000
        try:
            message = client.messages.create(
                model=model,
                max_tokens=bigger_max_tokens,
                tools=[ARTICLE_TOOL_SCHEMA],
                tool_choice={"type": "auto"},
                messages=[{"role": "user", "content": prompt}],
            )
        except Exception as e:
            logging.error("API error on max_tokens retry for %s: %s", item.title[:50], e)
            return None
        
        if message.stop_reason == "max_tokens":
            logging.error("Still truncated after retry with %d tokens, giving up: %s", bigger_max_tokens, item.title[:50])
            return None
    
    for block in message.content:
        if block.type == "tool_use" and block.name == "submit_article":
            art = block.input
            
            # Validate and coerce field types - model may return strings for dict/list fields
            # This happens when the model outputs XML-like tags embedded in strings
            validated = _validate_article_structure(art, item.title[:50])
            if validated is None:
                logging.warning("Article draft failed structure check: %s", item.title[:50])
                return None
            
            validated["source"] = item.source
            validated["evidence_level"] = item.evidence_level
            validated["source_trace"] = item.source_trace
            return validated
    
    logging.warning("Article draft did not return tool_use for: %s", item.title[:50])
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
        written = str(art.get("journal") or "").strip()
        if written and (MISSING_VALUE_MARK in written or AUTHOR_PLACEHOLDER_RE.search(written)):
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


def _hard_problems(problems: list[str]) -> list[str]:
    """Problems that block publishing (redraft or drop)."""
    markers = (
        "未找到", "无法回溯", "编造", "营销词汇", "新闻稿",
        "含义不匹配", "单位不匹配", "汉字", "上限", "下限",
        "必须包含数字", "必须使用阿拉伯", "过于模糊",
        "不是数值数据", "注册了标识符",
        "作者", "术语翻译", "过短", "过长", "结果字段",
        "比较方向", "机构", "基因", "药物", "蛋白质",
    )
    return [p for p in problems if any(m in p for m in markers)]


def process_articles(items: list[dict], config: dict) -> dict:
    """Process items through enrichment, triage, drafting, and validation.
    
    Returns dict with articles and deals (deals passed through unchanged).
    """
    academic_items = [item for item in items if item.get("kind") == "academic"]
    industry_items = [item for item in items if item.get("kind") != "academic"]
    
    logging.info("Enriching %d academic items", len(academic_items))
    enriched = [enrich_item(item) for item in academic_items]
    
    selections = triage_items(enriched, config)
    
    url_to_enriched = {e.url: e for e in enriched}
    url_to_selection = {s["url"]: s for s in selections}
    
    articles = []
    for selection in selections:
        url = selection["url"]
        tier = selection["tier"]
        field = selection["field"]
        
        if tier == "industry":
            continue
        
        # Wrap ALL per-article processing in try-except
        # One article's failure should NEVER crash the whole run
        try:
            art = _process_single_article(selection, url_to_enriched, config)
            if art:
                articles.append(art)
        except Exception as e:
            logging.error("EXCEPTION processing article %s: %s - dropping this article, run continues", url, e)
            import traceback
            logging.debug("Traceback: %s", traceback.format_exc())
            continue
    
    # Process industry items: they stay on the existing claude_draft path
    # Don't return raw industry items - return empty list
    # Industry items should continue using the old claude_draft deals path
    return {"articles": articles, "deals": []}


def _process_single_article(selection: dict, url_to_enriched: dict, config: dict) -> dict | None:
    """Process a single article through drafting, validation, and transformation.
    
    Extracted to allow per-article exception handling in process_articles.
    Returns the processed article dict or None if it should be dropped.
    """
    url = selection["url"]
    tier = selection["tier"]
    field = selection["field"]
    
    enriched_item = url_to_enriched.get(url)
    if not enriched_item:
        logging.warning("Skipping unknown URL from triage: %s", url)
        return None
    
    if enriched_item.evidence_level in ("press", "secondary") and tier == "deep":
        logging.warning("Downgrading %s from deep to brief (evidence: %s)", url, enriched_item.evidence_level)
        tier = "brief"
    
    # Deep tier requires fulltext OR rich abstract (>=1200 chars)
    # Otherwise we get filler content ("未给出" padding)
    abstract_len = len(enriched_item.abstract or "")
    has_fulltext = bool(enriched_item.fulltext_results)
    if tier == "deep" and not has_fulltext and abstract_len < 1200:
        logging.warning("Downgrading %s from deep to brief (abstract only %d chars, need >=1200 or fulltext)", 
                      url, abstract_len)
        tier = "brief"
    
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
    raw_material = "\n".join(raw_parts)
    
    def _prepare(draft: dict | None) -> tuple[dict | None, list[str]]:
        if not draft:
            return None, ["draft missing"]
        draft = sanitize_published_article(dict(draft), enriched_item)
        draft["field"] = field
        draft["tier"] = tier
        probs = validate_depth(draft, raw_material)
        probs.extend(validate_names(draft, raw_material))
        return draft, probs

    art = draft_single_article(enriched_item, tier, config)
    if not art:
        # Malformed / empty / text-only replies get exactly one redraft
        logging.warning("Malformed or empty draft, redrafting once: %s", url)
        art = draft_single_article(enriched_item, tier, config)
    if not art:
        logging.warning("Failed to draft article for: %s", url)
        return None

    art, problems = _prepare(art)

    first_hard_problems = _hard_problems(problems)
    first_soft_problems = [p for p in problems if p not in first_hard_problems]
    first_has_soft_only = len(first_hard_problems) == 0 and len(first_soft_problems) > 0

    # Soft-only issues (empty optional fields, limitation count after omit)
    # are not a reason to spend a redraft or to drop the article.
    if first_soft_problems and not first_hard_problems:
        logging.warning("Accepting %s with soft-only problems: %s", url, first_soft_problems)
        problems = first_soft_problems

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
                    return None
                tier = "brief"
                art, problems = _prepare(retry_art)
            elif first_has_soft_only:
                logging.warning("Redraft failed but first draft had soft-only problems, keeping first: %s", url)
                art = first_art
                problems = first_problems
            else:
                logging.error("Dropping %s after failed redraft: %s", url, first_hard_problems)
                return None
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
                    if tier == "deep":
                        logging.warning("Downgrading %s from deep to brief after retry - hard problems: %s", url, hard_problems)
                        brief_art = draft_single_article(enriched_item, "brief", config, problems=problems)
                        if brief_art is None:
                            logging.error("Brief targeted redraft failed, dropping: %s", url)
                            return None
                        tier = "brief"
                        art, problems = _prepare(brief_art)
                        hard_problems = _hard_problems(problems)
                        if hard_problems:
                            logging.error("Dropping %s after brief redraft - hard problems: %s", url, hard_problems)
                            return None
                        if problems:
                            logging.warning("Accepting %s with soft problems: %s", url, problems)
                    else:
                        logging.error("Dropping %s after retry - hard problems: %s", url, hard_problems)
                        return None
                else:
                    logging.warning("Accepting %s with soft-only problems: %s", url, soft_problems)
    
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
    
    # Ensure image_prompt is set - derive from paper subject ONLY
    # IMPORTANT: Use English keywords only (Chinese text renders as characters in image)
    # IMPORTANT: Do NOT add generic field boilerplate that may conflict with subject
    if not art.get("image_prompt"):
        # Extract English keywords from the enriched item's title
        source_title = enriched_item.title or ""
        # Extract English words (proteins, drugs, mechanisms, cell types)
        english_words = re.findall(r'\b[A-Za-z][A-Za-z0-9-]{2,}\b', source_title)
        # Filter to likely scientific terms (exclude common words)
        stopwords = {'the', 'and', 'for', 'with', 'from', 'this', 'that', 'are', 'was', 
                    'were', 'not', 'via', 'new', 'novel', 'study', 'research', 'analysis',
                    'findings', 'results', 'evidence', 'role', 'effect', 'effects'}
        keywords = [w for w in english_words if len(w) >= 3 and w.lower() not in stopwords][:6]
        
        # Build prompt from paper subject only, no generic field boilerplate
        # End with style directives that prevent text rendering
        if keywords:
            art["image_prompt"] = f"{', '.join(keywords)}, medical illustration, scientific diagram, no text, no labels, no words"
        else:
            # Fallback: generic biomedical imagery only
            art["image_prompt"] = "biomedical research, cells, molecules, medical illustration, no text, no labels, no words"
    else:
        # Model provided image_prompt - ensure no-text directive is added
        model_prompt = art["image_prompt"]
        if "no text" not in model_prompt.lower() and "无文字" not in model_prompt:
            art["image_prompt"] = f"{model_prompt}, no text, no labels, no words"
    
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
    
    # Image (if available)
    img = art.get("img", "")
    if img:
        parts.append(f'<p style="margin:1em 0;"><img src="{_escape_html(img)}" alt="" style="max-width:100%;border-radius:8px;"></p>')
    
    # One-liner
    if art.get("one_liner"):
        parts.append(f'<p style="margin:0.5em 0;font-weight:700;color:#0f6b5c;">{_escape_html(art["one_liner"])}</p>')
    
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
    }
    for key, label in field_names.items():
        val = datacard.get(key, "")
        if val and val != "不适用" and MISSING_VALUE_MARK not in str(val):
            datacard_rows.append(f'<tr><td style="padding:6px 10px;border:1px solid #eee;font-weight:700;width:80px;">{label}</td><td style="padding:6px 10px;border:1px solid #eee;">{_escape_html(val)}</td></tr>')
    
    if datacard_rows:
        parts.append('<table style="width:100%;border-collapse:collapse;margin:1em 0;font-size:14px;background:#f9f9f9;">')
        parts.extend(datacard_rows)
        parts.append('</table>')
    
    # Evidence level - use Chinese labels
    evidence = art.get("evidence_level", "abstract")
    evidence_label = EVIDENCE_LEVEL_LABELS.get(evidence, evidence)
    parts.append(f'<p style="font-size:12px;color:#999;margin:0.5em 0;">证据等级：{evidence_label}</p>')
    
    # Section: Background
    if art.get("background"):
        parts.append('<h4 style="font-size:15px;margin:1.2em 0 0.3em;color:#333;">研究背景</h4>')
        parts.append(f'<p style="margin:0.5em 0;">{_escape_html(art["background"])}</p>')
    
    # Section: Design
    if art.get("design"):
        parts.append('<h4 style="font-size:15px;margin:1.2em 0 0.3em;color:#333;">研究设计</h4>')
        parts.append(f'<p style="margin:0.5em 0;">{_escape_html(art["design"])}</p>')
    
    # Section: Results
    results = art.get("results", [])
    if results:
        parts.append('<h4 style="font-size:15px;margin:1.2em 0 0.3em;color:#333;">核心结果</h4>')
        for para in results:
            parts.append(f'<p style="margin:0.5em 0;">{_escape_html(para)}</p>')
    
    # Section: Mechanism (deep only)
    if tier == "deep" and art.get("mechanism"):
        parts.append('<h4 style="font-size:15px;margin:1.2em 0 0.3em;color:#333;">机制解读</h4>')
        parts.append(f'<p style="margin:0.5em 0;">{_escape_html(art["mechanism"])}</p>')
    
    # Section: Limitations
    limitations = art.get("limitations", [])
    if limitations:
        parts.append('<h4 style="font-size:15px;margin:1.2em 0 0.3em;color:#333;">局限与不确定</h4>')
        parts.append('<div style="background:#f5f5f5;padding:10px 14px;border-radius:8px;margin:0.5em 0;">')
        parts.append('<ul style="margin:0;padding-left:18px;">')
        for lim in limitations:
            parts.append(f'<li style="margin:4px 0;font-size:14px;color:#666;">{_escape_html(lim)}</li>')
        parts.append('</ul>')
        parts.append('</div>')
    
    # Section: Significance
    if art.get("significance"):
        parts.append('<h4 style="font-size:15px;margin:1.2em 0 0.3em;color:#333;">临床/产业意义</h4>')
        parts.append(f'<p style="margin:0.5em 0;">{_escape_html(art["significance"])}</p>')
    
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
