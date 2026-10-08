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
    abstract: str = ""
    fulltext_results: str = ""
    fig_captions: str = ""
    methods_design: str = ""
    evidence_level: str = "press"
    rss_summary: str = ""
    source_trace: list[str] = field(default_factory=list)


def extract_doi(url: str) -> str:
    """Extract DOI from various URL formats."""
    url = url.strip()
    patterns = [
        r'https?://(?:dx\.)?doi\.org/(10\.\d+/[^\s?#]+)',
        r'https?://(?:www\.)?nature\.com/articles/(s\d+-\d+-\d+-\w+)',
        r'https?://(?:www\.)?cell\.com/[^/]+/(?:fulltext|abstract)/(S[\d\-\(\)]+)',
        r'https?://(?:www\.)?science\.org/doi/(10\.\d+/[^\s?#]+)',
        r'https?://(?:www\.)?thelancet\.com/journals/[^/]+/article/(PIIS[\d]+)',
        r'https?://(?:www\.)?nejm\.org/doi/(10\.\d+/[^\s?#]+)',
        r'https?://(?:www\.)?biorxiv\.org/content/(10\.\d+/[^\s?#]+)',
        r'https?://(?:www\.)?medrxiv\.org/content/(10\.\d+/[^\s?#]+)',
    ]
    for pattern in patterns:
        match = re.search(pattern, url, re.IGNORECASE)
        if match:
            doi = match.group(1)
            if doi.startswith("s") or doi.startswith("S"):
                return f"10.1038/{doi}"
            return doi
    doi_match = re.search(r'(10\.\d+/[^\s?#]+)', url)
    if doi_match:
        return doi_match.group(1)
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


def extract_sections_from_xml(xml_text: str, section_names: tuple[str, ...]) -> str:
    """Extract specific sections from PMC XML."""
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
                    if p.text:
                        text_parts.append(p.text.strip())
                    for sub in p:
                        if sub.tail:
                            text_parts.append(sub.tail.strip())
                sections.append(" ".join(text_parts))
    return "\n\n".join(sections)


def extract_fig_captions_from_xml(xml_text: str, max_chars: int = 6000) -> str:
    """Extract figure captions from PMC XML."""
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
            text_parts = []
            for elem in cap.iter():
                if elem.text:
                    text_parts.append(elem.text.strip())
                if elem.tail:
                    text_parts.append(elem.tail.strip())
            if text_parts:
                captions.append(" ".join(text_parts))
    
    result = "\n\n".join(captions)
    return result[:max_chars]


def extract_design_methods_from_xml(xml_text: str, max_chars: int = 4000) -> str:
    """Extract study design related methods from PMC XML."""
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
                                if p.text:
                                    text_parts.append(p.text.strip())
                            if text_parts:
                                sections.append(" ".join(text_parts))
    
    result = "\n\n".join(sections)
    return result[:max_chars]


def pubmed_efetch_abstract(pmid: str) -> str:
    """Fetch abstract from PubMed efetch API."""
    if not pmid:
        return ""
    url = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi?" + urllib.parse.urlencode({
        "db": "pubmed",
        "id": pmid,
        "rettype": "abstract",
        "retmode": "text",
    })
    data = _http_get(url)
    if data:
        text = data.decode("utf-8", errors="replace")
        lines = text.strip().split("\n")
        abstract_lines = []
        in_abstract = False
        for line in lines:
            if line.strip() and not line.startswith("Author information") and not line.startswith("PMID:"):
                if in_abstract or (len(line) > 50 and not any(line.startswith(x) for x in ["1.", "2.", "3."])):
                    in_abstract = True
                    abstract_lines.append(line.strip())
        return " ".join(abstract_lines)
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
            item.evidence_level = "abstract"
            item.source_trace.append(f"bioRxiv API: {len(abstract)} chars")
    
    if not item.abstract:
        item.abstract = item.rss_summary
        item.evidence_level = "press"
        item.source_trace.append("Fallback to RSS summary")
    
    return item


def extract_numbers_from_text(text: str) -> set[str]:
    """Extract all numbers from text, handling %, 倍, units, Chinese numerals."""
    numbers = set()
    
    patterns = [
        r'\d+(?:\.\d+)?(?:\s*(?:%|％))',
        r'\d+(?:\.\d+)?(?:\s*倍)',
        r'\d+(?:\.\d+)?(?:\s*(?:个月|天|周|年|岁|例|名|只|条|个|位|人|mg|kg|µg|ng|mL|L|µL|nM|pM|µM|mM))',
        r'(?:HR|OR|RR|CI|P|p)\s*[=<>≤≥]\s*\d+(?:\.\d+)?',
        r'\d+(?:\.\d+)?\s*[×x]\s*10\^?\d+',
        r'\d+/\d+',
        r'\d+(?:\.\d+)?',
    ]
    
    for pattern in patterns:
        for match in re.finditer(pattern, text, re.IGNORECASE):
            num = match.group(0).strip()
            if num and len(num) > 0:
                numbers.add(num)
    
    chinese_num_pattern = r'[零一二三四五六七八九十百千万亿]+'
    for match in re.finditer(chinese_num_pattern, text):
        numbers.add(match.group(0))
    
    return numbers


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

## 不可违反的三条

1. 只能写材料里**已经写明**的事实。材料里没有的数字、作者、适应症、剂量、人群、金额，一律不要写。
2. 材料里查不到的字段，写「原文未给出」或「未读到该部分」，**不要留空，不要推测，不要用相近的数字代替**。
3. 你引用的每一个数字，都必须能在材料里找到对应的原句。把这些原句逐字放进 data_points[].source_quote。
   凑不出 source_quote 的数字，就不要写进正文。

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


def validate_depth(art: dict, raw_material: str) -> list[str]:
    """Validate generated article meets depth requirements.
    
    Returns list of problems. Empty list means validation passed.
    """
    problems = []
    norm = normalize_whitespace(raw_material)
    tier = art.get("tier", "brief")
    
    if MARKETING_BLOCKLIST.search(art.get("title", "")):
        problems.append("标题含有营销词汇")
    if MARKETING_BLOCKLIST.search(art.get("one_liner", "")):
        problems.append("一句话结论含有营销词汇")
    
    for dp in art.get("data_points", []):
        quote = normalize_whitespace(dp.get("source_quote", ""))
        if len(quote) < 10:
            problems.append(f"data_point source_quote 过短：{dp.get('value')}")
        elif quote not in norm:
            problems.append(f"data_point 无法回溯：{dp.get('value')} (quote: {quote[:50]}...)")
    
    body_parts = [
        art.get("one_liner", ""),
        art.get("background", ""),
        art.get("design", ""),
        *art.get("results", []),
        art.get("mechanism", ""),
        art.get("significance", ""),
    ]
    body = " ".join(body_parts)
    
    declared_values = set()
    for dp in art.get("data_points", []):
        declared_values.add(dp.get("value", ""))
    
    body_numbers = extract_numbers_from_text(body)
    for num in body_numbers:
        num_clean = num.strip()
        if not any(num_clean in dv or dv in num_clean for dv in declared_values):
            if re.search(r'\d', num_clean):
                problems.append(f"正文数字未登记：{num_clean}")
    
    for i, para in enumerate(art.get("results", [])):
        if not re.search(r'\d', para):
            problems.append(f"results 第 {i+1} 段没有任何数字")
    
    min_limits = 3 if tier == "deep" else 1
    limitations = art.get("limitations", [])
    if len(limitations) < min_limits:
        problems.append(f"局限条数不足：需要 {min_limits} 条，实际 {len(limitations)} 条")
    
    empty_phrases = r"仍需(更多)?研究|有待(进一步)?验证$|期待后续|需要更大规模"
    for lim in limitations:
        if re.search(empty_phrases, lim) and len(lim) < 30:
            problems.append(f"局限为空话：{lim}")
    
    datacard = art.get("datacard", {})
    required_fields = ["study_type", "n", "control", "intervention", "followup",
                      "primary_endpoint", "primary_endpoint_result", "statistics", "safety"]
    for field in required_fields:
        val = str(datacard.get(field, "")).strip()
        if not val:
            problems.append(f"数据卡字段为空：{field}（应写'原文未给出'或'不适用'）")
    
    total_chars = cn_len(body) + cn_len(" ".join(limitations))
    if tier == "deep":
        if total_chars < 1400 * 0.85:
            problems.append(f"deep 档正文 {total_chars} 字，低于下限 1190 字")
        elif total_chars > 1900 * 1.15:
            problems.append(f"deep 档正文 {total_chars} 字，超过上限 2185 字")
    else:
        if total_chars < 450 * 0.85:
            problems.append(f"brief 档正文 {total_chars} 字，低于下限 383 字")
        elif total_chars > 650 * 1.15:
            problems.append(f"brief 档正文 {total_chars} 字，超过上限 748 字")
    
    evidence = art.get("evidence_level", art.get("datacard", {}).get("evidence_level", "abstract"))
    if tier == "deep" and evidence in ("press", "secondary"):
        problems.append("仅有新闻稿，不得写成深度解读")
    
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


def draft_single_article(item: EnrichedItem, tier: str, config: dict) -> dict | None:
    """Draft a single article using Claude.
    
    Returns the article dict or None on failure.
    """
    from anthropic import Anthropic
    
    prompt = build_article_prompt(item, tier)
    model = os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-5-5")
    max_tokens = 4000 if tier == "deep" else 2000
    
    logging.info("Drafting %s article for: %s", tier, item.title[:50])
    
    client = Anthropic()
    message = client.messages.create(
        model=model,
        max_tokens=max_tokens,
        tools=[ARTICLE_TOOL_SCHEMA],
        tool_choice={"type": "tool", "name": "submit_article"},
        messages=[{"role": "user", "content": prompt}],
    )
    
    for block in message.content:
        if block.type == "tool_use" and block.name == "submit_article":
            art = block.input
            art["source"] = item.source
            art["evidence_level"] = item.evidence_level
            art["source_trace"] = item.source_trace
            return art
    
    logging.warning("Article draft did not return tool_use")
    return None


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
        
        enriched_item = url_to_enriched.get(url)
        if not enriched_item:
            logging.warning("Skipping unknown URL from triage: %s", url)
            continue
        
        if enriched_item.evidence_level in ("press", "secondary") and tier == "deep":
            logging.warning("Downgrading %s from deep to brief (evidence: %s)", url, enriched_item.evidence_level)
            tier = "brief"
        
        raw_material = "\n".join([
            enriched_item.abstract or "",
            enriched_item.fulltext_results or "",
            enriched_item.fig_captions or "",
            enriched_item.methods_design or "",
            enriched_item.rss_summary or "",
        ])
        
        art = draft_single_article(enriched_item, tier, config)
        if not art:
            logging.warning("Failed to draft article for: %s", url)
            continue
        
        art["field"] = field
        
        problems = validate_depth(art, raw_material)
        if problems:
            logging.warning("Validation issues for %s: %s", url, problems)
            
            logging.info("Retrying with problems listed...")
            retry_prompt = build_article_prompt(enriched_item, tier) + f"\n\n## 上次的问题\n\n" + "\n".join(f"- {p}" for p in problems)
            
            from anthropic import Anthropic
            client = Anthropic()
            model = os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-5-5")
            max_tokens = 4000 if tier == "deep" else 2000
            
            message = client.messages.create(
                model=model,
                max_tokens=max_tokens,
                tools=[ARTICLE_TOOL_SCHEMA],
                tool_choice={"type": "tool", "name": "submit_article"},
                messages=[{"role": "user", "content": retry_prompt}],
            )
            
            for block in message.content:
                if block.type == "tool_use" and block.name == "submit_article":
                    art = block.input
                    art["source"] = enriched_item.source
                    art["evidence_level"] = enriched_item.evidence_level
                    art["source_trace"] = enriched_item.source_trace
                    art["field"] = field
                    break
            
            problems = validate_depth(art, raw_material)
            if problems:
                if tier == "deep":
                    logging.warning("Downgrading %s from deep to brief after retry", url)
                    art["tier"] = "brief"
                    problems = validate_depth(art, raw_material)
                    if problems:
                        logging.error("Dropping %s after downgrade: %s", url, problems)
                        continue
                else:
                    logging.error("Dropping %s after retry: %s", url, problems)
                    continue
        
        articles.append(art)
    
    return {"articles": articles, "deals": industry_items}


def wechat_html_article(art: dict) -> str:
    """Generate WeChat-compatible HTML for a single article."""
    tier = art.get("tier", "brief")
    datacard = art.get("datacard", {})
    
    parts = []
    
    parts.append(f'<h3 style="font-size:18px;margin:1.5em 0 0.5em;color:#1d2a27;border-left:4px solid #0f6b5c;padding-left:12px;">{art.get("title", "")}</h3>')
    
    if art.get("one_liner"):
        parts.append(f'<p style="margin:0.5em 0;font-weight:700;color:#0f6b5c;">{art["one_liner"]}</p>')
    
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
            datacard_rows.append(f'<tr><td style="padding:6px 10px;border:1px solid #eee;font-weight:700;width:80px;">{label}</td><td style="padding:6px 10px;border:1px solid #eee;">{val}</td></tr>')
    
    if datacard_rows:
        parts.append('<table style="width:100%;border-collapse:collapse;margin:1em 0;font-size:14px;background:#f9f9f9;">')
        parts.extend(datacard_rows)
        parts.append('</table>')
    
    evidence = art.get("evidence_level", "abstract")
    parts.append(f'<p style="font-size:12px;color:#999;margin:0.5em 0;">证据等级：{evidence}</p>')
    
    if art.get("background"):
        parts.append('<h4 style="font-size:15px;margin:1.2em 0 0.3em;color:#333;">研究背景</h4>')
        parts.append(f'<p style="margin:0.5em 0;">{art["background"]}</p>')
    
    if art.get("design"):
        parts.append('<h4 style="font-size:15px;margin:1.2em 0 0.3em;color:#333;">研究设计</h4>')
        parts.append(f'<p style="margin:0.5em 0;">{art["design"]}</p>')
    
    results = art.get("results", [])
    if results:
        parts.append('<h4 style="font-size:15px;margin:1.2em 0 0.3em;color:#333;">核心结果</h4>')
        for para in results:
            parts.append(f'<p style="margin:0.5em 0;">{para}</p>')
    
    if tier == "deep" and art.get("mechanism"):
        parts.append('<h4 style="font-size:15px;margin:1.2em 0 0.3em;color:#333;">机制解读</h4>')
        parts.append(f'<p style="margin:0.5em 0;">{art["mechanism"]}</p>')
    
    limitations = art.get("limitations", [])
    if limitations:
        parts.append('<h4 style="font-size:15px;margin:1.2em 0 0.3em;color:#333;">局限与不确定</h4>')
        parts.append('<div style="background:#f5f5f5;padding:10px 14px;border-radius:8px;margin:0.5em 0;">')
        parts.append('<ul style="margin:0;padding-left:18px;">')
        for lim in limitations:
            parts.append(f'<li style="margin:4px 0;font-size:14px;color:#666;">{lim}</li>')
        parts.append('</ul>')
        parts.append('</div>')
    
    if art.get("significance"):
        parts.append('<h4 style="font-size:15px;margin:1.2em 0 0.3em;color:#333;">临床/产业意义</h4>')
        parts.append(f'<p style="margin:0.5em 0;">{art["significance"]}</p>')
    
    parts.append('<p style="font-size:13px;color:#666;margin:1em 0;">')
    parts.append(f'{art.get("authors", "")} · {art.get("journal", "")}')
    parts.append('</p>')
    
    url = art.get("url", "")
    if "doi.org" in url:
        doi = url.replace("https://doi.org/", "DOI: ")
        parts.append(f'<p style="font-size:12px;color:#999;margin:0.5em 0;">{doi}</p>')
    
    parts.append('<p style="font-size:11px;color:#999;margin:0.5em 0;font-style:italic;">本文由 Claude 起草，编辑核对后发布。</p>')
    
    return "\n".join(parts)


def wechat_html_full(articles: list[dict], deals: list[dict], week: str) -> str:
    """Generate complete WeChat HTML for all articles and deals."""
    parts = [
        '<section style="font-family:-apple-system,BlinkMacSystemFont,\'Segoe UI\',Roboto,sans-serif;font-size:16px;line-height:1.75;color:#333;">',
        f'<p style="font-size:14px;color:#666;">前沿追踪 · {week} · TheraSik 出品</p>',
        '<p style="margin:1em 0;">本期内容均基于原始来源核对，配图由 AI 生成（示意图，非期刊原图）。</p>',
    ]
    
    toc_items = []
    for i, art in enumerate(articles, 1):
        title = art.get("title", "")[:30]
        tier_label = "深度" if art.get("tier") == "deep" else "速览"
        toc_items.append(f"{i}. [{tier_label}] {title}...")
    
    if toc_items:
        parts.append('<div style="margin:1em 0;padding:1em;background:#f5f5f5;border-radius:8px;">')
        parts.append('<strong>本期目录</strong><br>')
        parts.append('<br>'.join(toc_items))
        parts.append('</div>')
    
    if articles:
        parts.append('<h2 style="border-left:4px solid #0f6b5c;padding-left:12px;margin:2em 0 1em;">学术</h2>')
        for art in articles:
            parts.append(wechat_html_article(art))
    
    if deals:
        parts.append('<h2 style="border-left:4px solid #0f6b5c;padding-left:12px;margin:2em 0 1em;">行业</h2>')
        for deal in deals:
            parts.append(f'<h3 style="font-size:18px;margin:1.5em 0 0.5em;color:#1d2a27;">{deal.get("title", "")}</h3>')
            if deal.get("money") and deal["money"] != "未披露":
                parts.append(f'<p style="margin:0.5em 0;"><strong>{deal["money"]}</strong></p>')
            if deal.get("why"):
                parts.append(f'<p style="margin:0.5em 0;">{deal["why"]}</p>')
            parts.append(f'<p style="font-size:14px;color:#666;margin:0.5em 0;">来源：{deal.get("source_name", "未注明")}</p>')
    
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
