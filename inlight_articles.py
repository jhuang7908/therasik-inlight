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

# Terminology glossary: correct translations for scientific terms
# Format: {English term: (correct Chinese, common incorrect translations)}
TERMINOLOGY_GLOSSARY = {
    "mesaconate": ("中康酸", ["美康酸", "梅沙康酸", "麦康酸"]),
    "mesaconic acid": ("中康酸", ["美康酸", "梅沙康酸", "麦康酸"]),
}

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


def scrape_biorxiv_fulltext(url: str) -> str:
    """Get bioRxiv/medRxiv full text if available."""
    if "biorxiv.org" not in url and "medrxiv.org" not in url:
        return ""
    
    doi = extract_doi(url)
    if not doi:
        return ""
    
    api_url = f"https://api.biorxiv.org/details/biorxiv/{doi}"
    data = _http_get(api_url)
    if data:
        try:
            result = json.loads(data.decode("utf-8"))
            collection = result.get("collection", [])
            if collection:
                return collection[0].get("abstract", "")
        except (json.JSONDecodeError, KeyError):
            pass
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
    
    if not item.abstract and "nature.com" in item.url:
        abstract = scrape_nature_abstract(item.url)
        if abstract:
            item.abstract = abstract
            item.evidence_level = "abstract"
            item.source_trace.append(f"Nature abstract: {len(abstract)} chars")
    
    if not item.abstract and item.pmid:
        abstract = pubmed_efetch_abstract(item.pmid)
        if abstract:
            item.abstract = abstract
            item.evidence_level = "abstract"
            item.source_trace.append(f"PubMed efetch: {len(abstract)} chars")
    
    if not item.abstract and ("biorxiv.org" in item.url or "medrxiv.org" in item.url):
        abstract = scrape_biorxiv_fulltext(item.url)
        if abstract:
            item.abstract = abstract
            item.evidence_level = "preprint"  # bioRxiv/medRxiv are preprints
            item.source_trace.append(f"bioRxiv API: {len(abstract)} chars")
    
    # Always mark bioRxiv/medRxiv as preprint, even if we got abstract from EPMC
    if "biorxiv.org" in item.url or "medrxiv.org" in item.url:
        if item.evidence_level == "abstract":
            item.evidence_level = "preprint"
    
    if not item.abstract:
        item.abstract = item.rss_summary
        item.evidence_level = "press"
        item.source_trace.append("Fallback to RSS summary")
    
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


# English number words to Arabic
ENGLISH_NUMBER_WORDS = {
    'zero': '0', 'one': '1', 'two': '2', 'three': '3', 'four': '4',
    'five': '5', 'six': '6', 'seven': '7', 'eight': '8', 'nine': '9',
    'ten': '10', 'eleven': '11', 'twelve': '12', 'thirteen': '13',
    'fourteen': '14', 'fifteen': '15', 'sixteen': '16', 'seventeen': '17',
    'eighteen': '18', 'nineteen': '19', 'twenty': '20'
}


def english_number_to_arabic(text: str) -> str:
    """Convert English number words to Arabic numerals.
    
    Examples: "nine doses" -> "9 doses", "five patients" -> "5 patients"
    """
    result = text.lower()
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


def build_article_prompt(item: EnrichedItem, tier: str) -> str:
    """Build prompt for single article drafting."""
    return f"""你是「前沿追踪」的科学编辑。下面是一篇论文的可核实材料，请据此写一篇中文解读。

## 不可违反的规则

1. **只写材料已经写明的事实**。材料里没有的数字、作者、适应症、剂量、人群、金额，一律不要写。
2. **材料里查不到的字段，写「原文未给出」或「未读到该部分」**，不要留空，不要推测，不要用相近的数字代替。
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
  ③ 未报告项（「原文未给出 X」）
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
期刊 / 来源：{item.source}
日期：{item.date}
DOI / 链接：{item.url}
证据等级：{item.evidence_level}

摘要：
{item.abstract[:8000] if item.abstract else '无'}

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
    
    # Known unit prefixes that make a number valid even without space
    unit_pattern = r'(?:mg|kg|mL|µg|nM|pM|µM|mM|μg|μL|ng|pg|mmol|mol|g|L|%|％|倍|年|个月|天|周|小时|例|名)'
    
    # Pattern: number must be preceded by non-identifier chars,
    # and followed by either non-identifier chars OR a known unit
    pattern = (
        r'(?<![a-zA-Z0-9])(?<![-.])'  # Not preceded by alnum, hyphen, or dot
        + re.escape(number_clean) +
        r'(?:' + unit_pattern + r'|(?![a-zA-Z0-9])(?![-.]?\d))'  # Followed by unit OR non-identifier
    )
    return bool(re.search(pattern, text_clean))


def normalize_source_text(text: str) -> str:
    """Normalize source text for number/name comparison.
    
    Applies:
    - Lowercase
    - Whitespace normalization
    - English number words to digits (nine -> 9)
    - Unicode superscripts to plain (10⁶ -> 10^6)
    - Thousands separators removed (1,139 -> 1139)
    - En-dash ranges (10–20 -> 10-20)
    - Plus-minus (± -> +/-)
    """
    result = text.lower()
    result = re.sub(r'\s+', ' ', result)
    
    # English number words
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
    
    # Bacterial strains: Nissle 1917, E. coli Nissle 1917
    for match in re.finditer(r'\bNissle\s*\d+\b', source, re.IGNORECASE):
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


def number_exists_in_source(num_str: str, source_norm: str, source_identifiers: set[str], 
                            context_window: str = "") -> bool:
    """Check if a number exists in the source text (after normalization).
    
    Returns True ONLY if the number appears as a STANDALONE data value in source,
    not as a substring of an identifier.
    
    Design: Identifiers like CD318, CD8, RPCEC00000444 should NOT make their
    embedded digits (31, 8, 44) available as data. For example:
    - Source has "CD318" -> "31例" should NOT pass
    - Source has "52% response rate" -> "52%" should pass
    
    Args:
        num_str: The number string from output (e.g., "50", "3.5倍", "72小时")
        source_norm: Normalized source text (lowercased, whitespace normalized)
        source_identifiers: Set of identifiers extracted from source (for reference only)
        context_window: Surrounding text from output (used to check if number appears
                        as part of an identifier that exists in source)
    """
    # Normalize the number
    num_clean = num_str.replace(',', '').replace('，', '').strip()
    
    # Extract core numeric value
    num_core = extract_number_core(num_clean)
    if not num_core:
        return True  # Not a number (empty after extraction)
    
    # Check if the number in output is part of an identifier FROM SOURCE
    # Only allow if the FULL identifier appears in the context_window of output
    # This handles: output says "CD8细胞" and source has "CD8" -> the "8" is OK
    # But NOT: output says "8例死亡" and source has "CD8" -> the "8" is INVENTED
    if context_window:
        for ident in source_identifiers:
            ident_lower = ident.lower()
            # Only relevant if the identifier contains this number
            if num_core in ident_lower:
                # Check if the FULL identifier appears near the number in output
                context_lower = context_window.lower()
                if ident_lower in context_lower:
                    # The identifier (e.g., CD8) appears in output context
                    # AND the number is part of that identifier -> allow
                    return True
    
    # Check if the number exists in source with word boundaries
    # This is the primary check: the number must appear as standalone data
    if number_in_text_as_word_boundary(num_core, source_norm):
        return True
    
    # Try Chinese numeral conversion
    cn_converted = chinese_numeral_to_arabic(num_clean)
    cn_core = extract_number_core(cn_converted)
    if cn_core and cn_core != num_core:
        if number_in_text_as_word_boundary(cn_core, source_norm):
            return True
    
    return False


# Standard terminology that should be exempt from invented-number checks
# These phrases contain numbers that are part of terminology, not data claims
EXEMPT_NUMBER_PATTERNS = [
    # Grading: ≥3级, grade 3+, 三级及以上
    r'[≥>=]?\s*3\s*级',
    r'grade\s*[≥>=]?\s*[3-5]',
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
    # Gene/protein identifiers with numbers: p38, CD19, IL-6, PD-1
    r'\bp\d+\b',  # p38, p53
    r'CD\d+',  # CD4, CD8, CD19, CD318
    r'IL-?\d+',  # IL-6, IL-2
    r'PD-?\d+',  # PD-1, PD-L1
    # Frequency phrases: 一次/周, once a week, 每周1次
    r'[一二三四五六七八九十]\s*次\s*[/／每]\s*(周|天|月|日)',
    r'\d\s*次\s*[/／每]\s*(周|天|月|日)',
    r'once\s+a\s+(week|day|month)',
    r'twice\s+(weekly|daily|a\s+week)',
    r'每\s*(周|天|日|月)\s*[一二三四五六七八九十\d]+\s*次',
    # Trial IDs: NCT\d+, RPCEC\d+
    r'NCT\d+',
    r'RPCEC\d+',
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
        match = pattern.search(context)
        if match:
            # Verify the number is actually within or adjacent to the matched pattern
            matched_text = match.group(0)
            if number in matched_text or str(int(float(number)) if '.' not in number else number) in matched_text:
                return True
    
    # Check for "原文未给出/未报告" - any number in these phrases is exempt
    if re.search(r'原文未给出|原文未报告|未读到|未给出|未报告', context):
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


# Contradictory metric pairs - if output uses one and source uses the other, it's a mismatch
# These are pairs where using the same number would be semantically wrong
CONTRADICTORY_METRIC_PAIRS = [
    # Response rate vs adverse events - completely different metrics
    ({"response", "缓解", "orr", "crr", "cr", "pr", "客观缓解", "完全缓解", "部分缓解", "有效"},
     {"adverse", "不良", "ae", "toxicity", "毒性", "side effect", "副作用", "trae", "teae"}),
    # Response rate vs mortality - one is good, one is bad
    ({"response", "缓解", "orr", "有效"},
     {"死亡", "mortality", "death", "致死"}),
    # Survival vs adverse events
    ({"survival", "生存", "os", "pfs", "存活"},
     {"adverse", "不良", "ae", "toxicity", "毒性"}),
]

# Unit patterns that indicate count vs rate/percentage
# 例, 名, 人, patients -> count
# %, rate, 率 -> percentage
# Also match fractional expressions like N/M (e.g., 19/36) as counts
COUNT_UNIT_PATTERNS = re.compile(r'例|名|人|位|patients|subjects|participants|cases|\d+\s*/\s*\d+', re.IGNORECASE)
RATE_UNIT_PATTERNS = re.compile(r'%|％|率|rate|percent', re.IGNORECASE)


def extract_metric_keywords(context: str) -> set[str]:
    """Extract specific metric keywords from context (not categories).
    
    Returns a set of found keywords, lowercase.
    """
    context_lower = context.lower()
    keywords = set()
    
    # Key metrics to detect
    all_keywords = [
        "response", "缓解", "orr", "crr", "cr", "pr", "客观缓解", "完全缓解", "部分缓解",
        "有效", "efficacy",
        "survival", "生存", "os", "pfs", "dfs", "efs", "存活",
        "死亡", "mortality", "death", "致死",
        "adverse", "不良", "ae", "toxicity", "毒性", "safety", "side effect", "副作用",
        "trae", "teae",
    ]
    
    for kw in all_keywords:
        if kw in context_lower:
            keywords.add(kw)
    
    return keywords


def number_meaning_matches_source(num_str: str, output_context: str, source_text: str) -> tuple[bool, str]:
    """Check if a number is used with a matching metric in source.
    
    Only flags CONTRADICTORY metric usage when a number is DIRECTLY attributed
    to a contradicting metric (e.g., "死亡率28%" when source says "28% adverse events").
    
    Uses a narrow context window (8 chars before number) to avoid false positives
    from distant text.
    
    Args:
        num_str: The number string (e.g., "28%")
        output_context: Context window around number in output  
        source_text: Full source text
        
    Returns:
        (matches, reason) tuple. matches=True if meaning is consistent or unclear.
    """
    num_core = extract_number_core(num_str)
    if not num_core:
        return True, ""
    
    # Only look at IMMEDIATE context (8 chars before number) for metric keywords
    # This catches "死亡率28%" or "CR rate 28%" but not distant mentions
    # Find the number in the output context and look at what's immediately before it
    num_match = re.search(rf'{re.escape(num_core)}', output_context)
    if not num_match:
        return True, ""
    
    # Get the 8 characters immediately before the number
    immediate_start = max(0, num_match.start() - 8)
    immediate_context = output_context[immediate_start:num_match.end()].lower()
    
    output_keywords = extract_metric_keywords(immediate_context)
    
    # Find all occurrences of this number in source 
    source_norm = source_text.lower()
    
    # Find number in source with moderate context (30 chars before/after)
    pattern = rf'(?<![a-zA-Z0-9]){re.escape(num_core)}(?![a-zA-Z0-9])'
    
    # First, check for unit type mismatch: 例 (count) vs % (percentage)
    # This is checked BEFORE keyword check since units are more reliable
    # Only look at the unit IMMEDIATELY attached to this number (within 3 chars after)
    output_immediate_end = min(len(output_context), num_match.end() + 3)
    output_immediate_unit = output_context[num_match.start():output_immediate_end].lower()
    
    output_is_count = bool(COUNT_UNIT_PATTERNS.search(output_immediate_unit))
    output_is_rate = bool(RATE_UNIT_PATTERNS.search(output_immediate_unit))
    
    # Check unit types if we can determine the output type
    if output_is_count or output_is_rate:
        source_has_count = False
        source_has_rate = False
        for match in re.finditer(pattern, source_norm):
            start = max(0, match.start() - 15)
            end = min(len(source_norm), match.end() + 15)
            source_context = source_norm[start:end]
            if COUNT_UNIT_PATTERNS.search(source_context):
                source_has_count = True
            if RATE_UNIT_PATTERNS.search(source_context):
                source_has_rate = True
        
        # Flag mismatch: output says count, but source only has rate (or vice versa)
        if output_is_count and source_has_rate and not source_has_count:
            return False, f"数字 '{num_str}' 单位不匹配：输出为人数（例/名），原文为百分比（%）"
        if output_is_rate and source_has_count and not source_has_rate:
            return False, f"数字 '{num_str}' 单位不匹配：输出为百分比（%），原文为人数（例/名）"
    
    # Now check for metric keyword contradictions
    if not output_keywords:
        return True, ""  # No recognizable metric immediately attached
    
    source_keywords = set()
    for match in re.finditer(pattern, source_norm):
        start = max(0, match.start() - 30)
        end = min(len(source_norm), match.end() + 15)
        source_context = source_norm[start:end]
        source_keywords.update(extract_metric_keywords(source_context))
    
    if not source_keywords:
        return True, ""  # Number not found with metrics in source
    
    # Check for contradictory pairs
    for set1, set2 in CONTRADICTORY_METRIC_PAIRS:
        output_has_set1 = bool(output_keywords & set1)
        output_has_set2 = bool(output_keywords & set2)
        source_has_set1 = bool(source_keywords & set1)
        source_has_set2 = bool(source_keywords & set2)
        
        # Contradiction: output uses set1 keywords, source only has set2 (or vice versa)
        if output_has_set1 and source_has_set2 and not source_has_set1:
            return False, f"数字 '{num_str}' 含义不匹配：输出用于{output_keywords & set1}类指标，原文用于{source_keywords & set2}类指标"
        if output_has_set2 and source_has_set1 and not source_has_set2:
            return False, f"数字 '{num_str}' 含义不匹配：输出用于{output_keywords & set2}类指标，原文用于{source_keywords & set1}类指标"
    
    return True, ""


def extract_numbers_with_context(text: str) -> list[tuple[str, str]]:
    """Extract numbers from text with their surrounding context.
    
    Returns list of (number_string, context_window) tuples.
    Context window is ~20 chars before and after for unit/meaning verification.
    """
    results = []
    
    # Arabic numbers with optional units
    number_pattern = r'(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?(?:\s*(?:%|％|倍|年|个月|天|周|小时|例|名|mg|kg|mL|µg|nM|pM|µM|mM|μg|μL))?'
    for match in re.finditer(number_pattern, text):
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
    
    # Chinese numerals with units (these ARE data)
    cn_data_pattern = r'[零一二三四五六七八九十百千万亿两]+(?:多)?(?:年|倍|%|％|个月|天|周|小时|例|名|位|人|剂|次|万|亿)'
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
    
    # Extract identifiers from source (these are allowed to have digits)
    source_identifiers = extract_identifiers_from_source(raw_material)
    
    # Collect ALL text from output
    all_text_parts = [
        art.get("title", ""),
        art.get("one_liner", ""),
        art.get("background", ""),
        art.get("design", ""),
        *art.get("results", []),
        art.get("mechanism", ""),
        art.get("significance", ""),
        *art.get("limitations", []),
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
    
    # Validate data_points
    norm = normalize_whitespace(raw_material)
    norm_english = english_number_to_arabic(norm)
    
    for dp in art.get("data_points", []):
        value = dp.get("value", "").strip()
        quote = dp.get("source_quote", "").strip()
        meaning = dp.get("meaning", "").strip()
        
        # Reject gaming: non-numeric values
        # data_points must contain actual numeric data, not:
        # - Vague quantifiers (millions, tens of)
        # - Disease names or other non-numeric content
        # - Placeholder values
        
        # Check if value contains at least one digit
        if not re.search(r'\d', value):
            # No digits at all - reject
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
            r'疾病控制率',  # Disease Control Rate (DCR)
            r'无病生存',    # Disease-Free Survival (DFS)
            r'疾病进展',    # Disease Progression
            r'disease\s*control\s*rate',
            r'disease\s*free\s*survival',
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
        if quote_norm not in norm and quote_norm_english not in norm_english:
            problems.append(f"data_point 无法回溯：{value} (quote: {quote_norm[:50]}...)")
            continue
        
        # Check value appears in quote
        value_core = extract_number_core(value)
        if value_core:
            quote_for_check = chinese_numeral_to_arabic(english_number_to_arabic(quote))
            if not number_in_text_as_word_boundary(value_core, quote_for_check):
                problems.append(f"data_point value 不在 quote 中：{value}")
    
    # Extract ALL numbers from output text and check each against source
    # EXEMPT: Standard terminology (≥3级, 95%CI, phase 3, p38, etc.)
    # EXEMPT: Numbers inside "原文未给出/未报告" phrases
    # Every other numeric token must appear in source (after normalization)
    
    # Normalize unit spacing in source for matching: "12 nM" = "12nM"
    source_norm_units = normalize_unit_spacing(source_norm)
    
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
        
        if not number_exists_in_source(num, source_norm_units, source_identifiers, context_norm):
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
        
        if not number_in_text_as_word_boundary(arabic_core, source_norm_units):
            problems.append(f"中文数字 '{cn_num}' ({arabic}) 在原始材料中未找到")
        else:
            # Chinese number exists - also check meaning
            meaning_ok, meaning_reason = number_meaning_matches_source(cn_num, context, raw_material)
            if not meaning_ok:
                problems.append(meaning_reason)
    
    # Limitations count and quality
    min_limits = 3 if tier == "deep" else 1
    limitations = art.get("limitations", [])
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
                problems.append(f"数据卡字段为空：{field}（应写'原文未给出'或'不适用'）")
    else:
        problems.append("datacard 字段格式错误（应为对象）")
    
    # Character count check
    body_parts = [
        art.get("one_liner", ""),
        art.get("background", ""),
        art.get("design", ""),
        *art.get("results", []),
        art.get("mechanism", ""),
        art.get("significance", ""),
    ]
    body = " ".join(body_parts)
    total_chars = cn_len(body) + cn_len(" ".join(limitations))
    
    # Character count validation - hard limits
    # Brief: minimum 450 Han chars (no tolerance)
    # Deep: 1400-1900 with 15% tolerance
    if tier == "deep":
        if total_chars < 1400 * 0.85:
            problems.append(f"deep 档正文 {total_chars} 字，低于下限 1190 字")
        elif total_chars > 1900 * 1.15:
            problems.append(f"deep 档正文 {total_chars} 字，超过上限 2185 字")
    else:
        # Hard minimum for brief - no tolerance below 450
        if total_chars < 450:
            problems.append(f"brief 档正文 {total_chars} 字，低于下限 450 字")
        elif total_chars > 650 * 1.15:
            problems.append(f"brief 档正文 {total_chars} 字，超过上限 748 字")
    
    # Evidence level check
    if isinstance(datacard, dict):
        evidence = art.get("evidence_level", datacard.get("evidence_level", "abstract"))
    else:
        evidence = art.get("evidence_level", "abstract")
    if tier == "deep" and evidence in ("press", "secondary"):
        problems.append("仅有新闻稿，不得写成深度解读")
    
    # Results must contain at least one verifiable number from source
    results_text = " ".join(art.get("results", []))
    results_numbers = extract_numbers_with_context(results_text)
    found_valid_number = False
    for num, context in results_numbers:
        num_core = extract_number_core(num)
        if num_core and number_in_text_as_word_boundary(num_core, source_norm):
            found_valid_number = True
            break
    
    if not found_valid_number and results_numbers:
        # Has numbers but none match source
        problems.append("结果字段中的数字无法在原文中核实")
    elif not results_numbers:
        # No numbers at all in results
        problems.append("结果字段应包含至少一个可核实的数字（来自原文）")
    
    # Check for excessive "未给出" boilerplate (soft warning)
    # If an article has too many "未给出" phrases, it may lack substantive content
    not_given_count = all_text.count("未给出") + all_text.count("未报告") + all_text.count("未提供")
    if not_given_count > 8:
        problems.append(f"文章含有过多「未给出/未报告」({not_given_count}处)，内容可能过于空洞")
    
    return problems


def validate_names(art: dict, raw_material: str) -> list[str]:
    """Check that proper names in output appear in source.
    
    Per spec B, we check ONLY:
    1. Latin-script tokens (author names, drug names, company names)
    2. Chinese institution suffix patterns with a preceding proper name
    3. 'X等' author patterns
    
    We do NOT flag:
    - Generic terms like '单中心', '中心数', '多中心'
    - Common Chinese words that happen to end in institution suffixes
    """
    problems = []
    norm = normalize_whitespace(raw_material).lower()
    
    all_text = " ".join([
        art.get("title", ""),
        art.get("one_liner", ""),
        art.get("background", ""),
        art.get("design", ""),
        *art.get("results", []),
        art.get("mechanism", ""),
        art.get("significance", ""),
        art.get("authors", ""),
        *art.get("limitations", []),
    ])
    
    # 1. Latin-script author names: "Zhang 等", "Li 等", "Smith 等"
    latin_author_pattern = r'([A-Z][a-z]+)\s*等'
    for match in re.finditer(latin_author_pattern, all_text):
        name = match.group(1).lower()
        if len(name) >= 2 and name not in norm:
            # Check with various boundaries
            if not any(x in norm for x in [f"{name},", f"{name} ", f"{name}.", f" {name}"]):
                problems.append(f"作者姓氏 '{match.group(1)}' 在原始材料中未找到")
    
    # 2. Chinese 'X等' author patterns (single surname + 等)
    # ONLY flag when the pattern looks like an author reference, not enumeration
    # 
    # Examples that ARE author patterns (flag if not in source):
    #   - "张等发现" (Zhang et al. found)
    #   - "由李等报道" (reported by Li et al.)
    #
    # Examples that are NOT author patterns (don't flag):
    #   - "乏力、皮疹等" (fatigue, rash, etc.) - list enumeration
    #   - "细胞因子等" (cytokines, etc.) - noun enumeration
    #   - "活动等" (activities, etc.) - noun enumeration
    #
    # Heuristic: "X等" is likely an author pattern only if:
    # - Preceded by a sentence boundary (。？！), comma (，), or start of text
    # - AND followed by a verb or attribution word (发现, 报道, 称, 指出, 认为)
    
    chinese_surname_pattern = r'([\u4e00-\u9fff])等'
    # List of verbs that indicate author attribution (expanded to include 报告)
    author_verbs = r'发现|报道|报告|称|指出|认为|表示|提出|观察|测定|检测|分析|开展|证明|证实'
    author_context_pattern = rf'(?:^|[。？！，、])\s*[\u4e00-\u9fff]等\s*(?:{author_verbs})'
    
    # Only flag if we find author-context pattern
    author_contexts = set(re.findall(rf'([\u4e00-\u9fff])等(?=\s*(?:{author_verbs}))', all_text))
    for char in author_contexts:
        if char not in raw_material:
            problems.append(f"中文作者姓氏 '{char}' 在原始材料中未找到")
    
    # 3. Chinese institution patterns: PROPER NAME + suffix
    # Only match if there's a clear proper name before the suffix
    # Proper name indicators: capitalized/title case, or known institution name patterns
    # E.g., "北京大学", "哈佛医院", but NOT "单中心", "中心数"
    
    # 3. Chinese institution patterns: SKIP
    # 
    # We no longer flag Chinese institution names because:
    # 1. Translated institution names (哈佛医学院 for Harvard Medical School) are legitimate
    # 2. Real institutions mentioned in affiliations are standard practice
    # 3. False positives (e.g., "分子免疫中心" flagged for "子") cause article drops
    #
    # Instead, we rely on number/data validation to catch fabrication.
    # Institutional affiliation fabrication is rare and lower priority than data fabrication.
    
    # 4. Latin-script drug/compound names (specific patterns)
    drug_pattern = r'\b([A-Z][a-z]+(?:mab|nib|lib|zumab|ximab|tinib|ciclib|lizumab))\b'
    for match in re.finditer(drug_pattern, all_text):
        drug = match.group(1).lower()
        if drug not in norm:
            problems.append(f"药物名 '{match.group(1)}' 在原始材料中未找到")
    
    # 5. Terminology check: flag incorrect translations
    for eng_term, (correct, incorrect_list) in TERMINOLOGY_GLOSSARY.items():
        # Check if source mentions the English term
        if eng_term.lower() in norm:
            # Check if output uses an incorrect translation
            for wrong in incorrect_list:
                if wrong in all_text:
                    problems.append(f"术语翻译错误：'{wrong}' 应为 '{correct}'（英文：{eng_term}）")
    
    # 5. Latin-script company names
    company_pattern = r'([A-Z][A-Za-z]+(?:\s+[A-Z][A-Za-z]+)?)\s*(?:公司|Inc\.?|Ltd\.?|Corp\.?|Therapeutics|Pharma|Biopharma)'
    for match in re.finditer(company_pattern, all_text):
        company = match.group(1).lower()
        if len(company) >= 3 and company not in norm:
            # Skip very common English words
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
            # Ensure all items are strings
            coerced = []
            for item in val:
                if isinstance(item, str):
                    coerced.append(item)
                elif isinstance(item, dict):
                    # Try to extract text
                    coerced.append(str(item.get("text", item)))
                elif isinstance(item, (int, float)):
                    coerced.append(str(item))
                else:
                    coerced.append(str(item))
            art[field] = coerced
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
    
    art = draft_single_article(enriched_item, tier, config)
    if not art:
        logging.warning("Failed to draft article for: %s", url)
        return None
    
    art["field"] = field
    
    problems = validate_depth(art, raw_material)
    name_problems = validate_names(art, raw_material)
    problems.extend(name_problems)
    
    if problems:
        logging.warning("Validation issues for %s: %s", url, problems)
        
        # Classify first draft problems
        first_hard_problems = [p for p in problems if any(x in p for x in [
            '未找到', '无法回溯', '编造', '营销词汇', '新闻稿', '含义不匹配'
        ])]
        first_soft_problems = [p for p in problems if p not in first_hard_problems]
        first_has_soft_only = len(first_hard_problems) == 0 and len(first_soft_problems) > 0
        
        # Keep first draft as fallback for soft-only failures
        first_art = art.copy()
        first_problems = problems.copy()
        
        # Targeted redraft with specific problems listed
        logging.info("Targeted redraft with %d problems listed...", len(problems))
        retry_art = draft_single_article(enriched_item, tier, config, problems=problems)
        
        if retry_art is None:
            logging.error("Targeted redraft failed for %s", url)
            if tier == "deep":
                # Try brief as fallback
                logging.info("Trying brief fallback for: %s", url)
                retry_art = draft_single_article(enriched_item, "brief", config)
                if retry_art is None:
                    logging.error("Brief fallback also failed, dropping: %s", url)
                    return None
                tier = "brief"
            else:
                # For brief: if first draft had soft-only problems, keep it
                if first_has_soft_only:
                    logging.warning("Redraft failed but first draft had soft-only problems, keeping first: %s", url)
                    art = first_art
                    problems = first_problems
                else:
                    return None
        else:
            retry_art["field"] = field
            retry_problems = validate_depth(retry_art, raw_material)
            retry_problems.extend(validate_names(retry_art, raw_material))
            
            # Compare first and retry drafts - publish the better one
            retry_hard = [p for p in retry_problems if any(x in p for x in [
                '未找到', '无法回溯', '编造', '营销词汇', '新闻稿', '含义不匹配'
            ])]
            retry_soft = [p for p in retry_problems if p not in retry_hard]
            
            # Determine which draft is better:
            # 1. Fewer hard problems is better
            # 2. If tied on hard, fewer total problems is better
            first_score = (len(first_hard_problems), len(first_problems))
            retry_score = (len(retry_hard), len(retry_problems))
            
            if retry_score <= first_score:
                # Retry is same or better
                art = retry_art
                problems = retry_problems
                logging.info("Using retry draft (score %s vs first %s): %s", retry_score, first_score, url)
            else:
                # First draft is better, keep it
                art = first_art
                problems = first_problems
                logging.info("Keeping first draft (score %s vs retry %s): %s", first_score, retry_score, url)
            
            if problems:
                # Re-classify the selected draft's problems
                hard_problems = [p for p in problems if any(x in p for x in [
                    '未找到', '无法回溯', '编造', '营销词汇', '新闻稿', '含义不匹配'
                ])]
                soft_problems = [p for p in problems if p not in hard_problems]
                
                if hard_problems:
                    # Hard problems: downgrade or drop
                    if tier == "deep":
                        logging.warning("Downgrading %s from deep to brief after retry - hard problems: %s", url, hard_problems)
                        brief_art = draft_single_article(enriched_item, "brief", config, problems=problems)
                        if brief_art is None:
                            logging.error("Brief targeted redraft failed, dropping: %s", url)
                            return None
                        brief_art["field"] = field
                        art = brief_art
                        tier = "brief"
                        problems = validate_depth(art, raw_material)
                        problems.extend(validate_names(art, raw_material))
                        hard_problems = [p for p in problems if any(x in p for x in [
                            '未找到', '无法回溯', '编造', '营销词汇', '新闻稿', '含义不匹配'
                        ])]
                        if hard_problems:
                            logging.error("Dropping %s after brief redraft - hard problems: %s", url, hard_problems)
                            return None
                        # Soft-only problems after downgrade: accept with warning
                        if problems:
                            logging.warning("Accepting %s with soft problems: %s", url, problems)
                    else:
                        logging.error("Dropping %s after retry - hard problems: %s", url, hard_problems)
                        return None
                else:
                    # Only soft problems: accept with a warning
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


def _escape_html(text: str) -> str:
    """Escape HTML special characters."""
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
        if val and val != "不适用":
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
    authors = art.get("authors", "")
    journal = art.get("journal", "")
    if authors and "原文未给出" not in authors:
        parts.append(f'<p style="font-size:13px;color:#666;margin:1em 0;">{_escape_html(authors)} · {_escape_html(journal)}</p>')
    elif journal:
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
            # Don't include per-article AI disclaimer; use single footer disclaimer
            parts.append(wechat_html_article(art, include_ai_disclaimer=False))
    
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
