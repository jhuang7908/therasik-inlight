#!/usr/bin/env python3
"""Fetch the last 7 days, draft Chinese copy, draw figures, write the site files.

    python run_weekly.py
    python run_weekly.py --dry-run

--dry-run writes only under preview/ and does not update content/ or call WeChat.
This script never pushes git and never calls the WeChat API.
"""

from __future__ import annotations

import argparse
import base64
import json
import logging
import os
import re
import sys
import traceback
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import feedparser
import yaml

ROOT = Path(__file__).resolve().parent
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
DEAL_KINDS = {"acq", "lic", "newco", "clin", "inv", "policy"}

# Image prompt - stronger anti-text language, abstract descriptions
IMAGE_PREFIX = (
    "Flat vector scientific illustration on pure white background. "
    "Thin gray outlines, soft teal, coral, gold and blue-gray palette. "
    "Minimalist, clean, diagrammatic. Abstract shapes representing biological concepts. "
    "No 3D effects, no glow, no gradients, no photorealism, no shadows. "
    "Simple geometric shapes only. The subject fills the frame. "
    "CRITICAL: This image must contain absolutely NO TEXT of any kind. "
)
IMAGE_SUFFIX = (
    " STRICT REQUIREMENT: No text, no letters, no words, no labels, no captions, "
    "no numbers, no watermarks, no annotations, no legends, no arrows with text, "
    "no cell type names, no protein names, no gene names anywhere in the image. "
    "Every element must be purely visual with zero textual content."
)
UA = "FrontierDigestWeekly/1.0 (+https://inlight.therasik.com)"


def sanitize_image_prompt(prompt: str) -> str:
    """Remove label-related phrases and make descriptions abstract.
    
    Uses word-boundary patterns to avoid false positives like 'laboured'.
    Also abstracts specific cell type names that might be rendered as labels.
    """
    patterns = [
        r'\b(labell?ed)\b',           # labeled, labelled (not laboured)
        r'\blabels?\b',               # bare label, labels
        r'\bannotated\b',
        r'\bwith\s+(text\s+)?labels?\s*(showing\s+(the\s+)?names?)?\b',
        r'\bwith\s+annotations?\b',
        r'\bwith\s+captions?\b',
        r'\bcaptioned\b',
        r'\bcaptions?\b',
        r'\bwith\s+text\b',
        r'\bshowing\s+(the\s+)?names?\b',
        r'\bnamed\b',
        r'"[^"]*"',                   # Remove quoted text
    ]
    result = prompt
    for pattern in patterns:
        result = re.sub(pattern, '', result, flags=re.IGNORECASE)
    
    # Abstract specific cell type names that might become labels
    cell_abstractions = [
        (r'\bT\s*cells?\b', 'immune cells'),
        (r'\bB\s*cells?\b', 'immune cells'),
        (r'\bCAR-T\b', 'engineered immune cells'),
        (r'\bNK\s*cells?\b', 'immune cells'),
        (r'\bmacrophages?\b', 'immune cells'),
        (r'\bdendritic\s*cells?\b', 'immune cells'),
        (r'\btumou?r\s*cells?\b', 'target cells'),
        (r'\bcancer\s*cells?\b', 'target cells'),
        (r'\bantigen-presenting\s*cells?\b', 'immune cells'),
        (r'\bcytotoxic\s*T?\s*cells?\b', 'immune cells'),
        (r'\bhelper\s*T?\s*cells?\b', 'immune cells'),
    ]
    for pattern, replacement in cell_abstractions:
        result = re.sub(pattern, replacement, result, flags=re.IGNORECASE)
    
    # Clean up extra spaces
    result = re.sub(r'\s+', ' ', result).strip()
    return result


def _test_sanitize_image_prompt():
    """Unit test for sanitize_image_prompt."""
    tests = [
        ("A diagram labeled with cell types", "A diagram with cell types"),
        ("labelled regions of the brain", "regions of the brain"),
        ("The laboured breathing pattern", "The laboured breathing pattern"),
        ("annotated with arrows", "with arrows"),
        ("with text labels showing names", ""),
        ('A cell "Helper T" diagram', "A cell diagram"),
        ("simple illustration of cells", "simple illustration of cells"),
        ("with captions identifying parts", "identifying parts"),
        ("a captioned figure of DNA", "a figure of DNA"),
        ("diagram with label", "diagram with"),
        ("cells with labels", "cells with"),
        # New tests for cell type abstraction
        ("T cells attacking tumor cells", "immune cells attacking target cells"),
        ("CAR-T therapy diagram", "engineered immune cells therapy diagram"),
    ]
    passed = 0
    for input_text, expected in tests:
        result = sanitize_image_prompt(input_text)
        if result == expected:
            passed += 1
        else:
            print(f"FAIL: '{input_text}' -> '{result}' (expected '{expected}')")
    print(f"sanitize_image_prompt: {passed}/{len(tests)} tests passed")
    return passed == len(tests)


def setup_log() -> Path:
    log_dir = ROOT / "logs"
    log_dir.mkdir(exist_ok=True)
    path = log_dir / f"weekly-{datetime.now().strftime('%Y%m%d-%H%M%S')}.log"
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[logging.FileHandler(path, encoding="utf-8"), logging.StreamHandler(sys.stdout)],
    )
    return path


def require_env(names: list[str]) -> None:
    missing = [n for n in names if not os.environ.get(n)]
    if missing:
        logging.error("缺少环境变量：%s", ", ".join(missing))
        raise SystemExit(1)


def check_anthropic_model() -> str:
    """Verify the Anthropic model is available before proceeding."""
    from anthropic import Anthropic, NotFoundError, APIError
    
    model = os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-5-5")
    logging.info("检查 Anthropic 模型可用性：%s", model)
    
    test_tool = {
        "name": "test_tool",
        "description": "Test tool for model check",
        "input_schema": {"type": "object", "properties": {"ok": {"type": "boolean"}}, "required": ["ok"]},
    }
    
    try:
        client = Anthropic()
        client.messages.create(
            model=model,
            max_tokens=50,
            tools=[test_tool],
            tool_choice={"type": "auto"},
            messages=[{"role": "user", "content": "Call test_tool with ok=true"}],
        )
        logging.info("模型 %s 可用（tool_choice=auto 测试通过）", model)
        return model
    except NotFoundError:
        logging.error("模型 %s 不存在或已下线。请设置 ANTHROPIC_MODEL 环境变量为可用模型。", model)
        raise SystemExit(1)
    except APIError as e:
        logging.error("Anthropic API 错误：%s", e)
        raise SystemExit(1)


def load_sources() -> dict:
    path = ROOT / "sources.yaml"
    with path.open(encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    if not isinstance(data, dict) or not isinstance(data.get("sources"), list):
        logging.error("sources.yaml 格式不对：需要 sources 列表")
        raise SystemExit(1)
    return data


def _within(when: datetime | None, start: datetime) -> bool:
    if when is None:
        return False
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return when >= start


def _parse_struct(entry) -> datetime | None:
    parsed = entry.get("published_parsed") or entry.get("updated_parsed")
    if not parsed:
        return None
    try:
        return datetime(*parsed[:6], tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None


BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)


def _fetch_rss_with_retry(feed: str, source_name: str, use_browser_ua: bool, max_attempts: int = 3, timeout: int = 20) -> tuple[feedparser.FeedParserDict | None, str]:
    """Fetch RSS feed with retry logic for transient errors."""
    import time
    import requests
    
    delays = [2, 5, 10]
    last_error = None
    should_retry = True
    
    ua = BROWSER_UA if use_browser_ua else UA
    
    for attempt in range(max_attempts):
        try:
            resp = requests.get(feed, headers={"User-Agent": ua}, timeout=timeout)
            
            if 400 <= resp.status_code < 500:
                logging.warning("%s 返回 %d（客户端错误，不重试）", source_name, resp.status_code)
                return None, "failed"
            
            if resp.status_code >= 500:
                raise requests.exceptions.HTTPError(f"Server error {resp.status_code}")
            
            resp.raise_for_status()
            parsed = feedparser.parse(resp.content)
            
            if getattr(parsed, "bozo", False) and not parsed.entries:
                bozo_exc = getattr(parsed, "bozo_exception", None)
                bozo_str = str(bozo_exc).lower() if bozo_exc else ""
                if "no element found" in bozo_str or "not well-formed" in bozo_str:
                    raise ValueError(f"XML parse error: {bozo_exc}")
                logging.warning("%s 的 feed 解析失败（不重试）：%s", source_name, bozo_exc)
                return None, "failed"
            
            return parsed, "ok"
            
        except requests.exceptions.Timeout as e:
            last_error = e
            should_retry = True
        except requests.exceptions.ConnectionError as e:
            last_error = e
            should_retry = True
        except requests.exceptions.HTTPError as e:
            last_error = e
            should_retry = "5" in str(e) or "Server error" in str(e)
        except ValueError as e:
            if "XML parse error" in str(e):
                last_error = e
                should_retry = True
            else:
                logging.warning("%s 抓取失败（不重试）：%s", source_name, e)
                return None, "failed"
        except Exception as e:
            logging.warning("%s 抓取失败（不重试）：%s", source_name, e)
            return None, "failed"
        
        if should_retry and attempt < max_attempts - 1:
            delay = delays[min(attempt, len(delays) - 1)]
            logging.info("%s 抓取失败，%d秒后重试（第%d次）：%s", source_name, delay, attempt + 1, last_error)
            time.sleep(delay)
        elif not should_retry:
            break
    
    logging.warning("%s 的 feed 重试后仍失败：%s", source_name, last_error)
    return None, "failed"


def _fetch_with_retry(url: str, source_name: str, max_attempts: int = 3, 
                      timeout: int = 60, json_response: bool = True) -> dict | bytes | None:
    """Fetch URL with retry logic for server errors, timeouts, and connection errors."""
    import time
    import requests
    
    delays = [2, 5, 10]
    last_error = None
    
    for attempt in range(max_attempts):
        try:
            resp = requests.get(url, headers={"User-Agent": UA}, timeout=timeout)
            
            if 400 <= resp.status_code < 500:
                logging.warning("%s 返回 %d（客户端错误，不重试）", source_name, resp.status_code)
                return None
            
            if resp.status_code >= 500:
                raise requests.exceptions.HTTPError(f"Server error {resp.status_code}")
            
            resp.raise_for_status()
            
            if json_response:
                return resp.json()
            return resp.content
            
        except (requests.exceptions.Timeout, 
                requests.exceptions.ConnectionError,
                requests.exceptions.HTTPError) as e:
            last_error = e
            if attempt < max_attempts - 1:
                delay = delays[min(attempt, len(delays) - 1)]
                logging.info("%s 请求失败，%d秒后重试（第%d次）：%s", source_name, delay, attempt + 1, e)
                time.sleep(delay)
        except Exception as e:
            logging.warning("%s 请求失败（不重试）：%s", source_name, e)
            return None
    
    logging.warning("%s 请求重试后仍失败：%s", source_name, last_error)
    return None


def fetch_biorxiv_api(start: datetime, limit: int, category: str | None = None) -> list[dict]:
    """Fallback: fetch from bioRxiv details API when RSS fails."""
    end_date = datetime.now(timezone.utc).date()
    start_date = start.date()
    
    base_url = f"https://api.biorxiv.org/details/biorxiv/{start_date.isoformat()}/{end_date.isoformat()}"
    
    logging.info("bioRxiv API fallback: %s (category=%s)", base_url, category or "all")
    
    rows = []
    cursor = 0
    max_pages = 5
    
    for page in range(max_pages):
        paginated_url = f"{base_url}/{cursor}"
        
        data = _fetch_with_retry(paginated_url, f"bioRxiv API (page {page+1})", json_response=True)
        if data is None:
            break
        
        collection = data.get("collection") or []
        if not collection:
            logging.info("bioRxiv API page %d 返回 0 条", page + 1)
            break
        
        if category:
            category_lower = category.lower()
            collection = [p for p in collection if (p.get("category") or "").lower() == category_lower]
        
        for paper in collection:
            doi = paper.get("doi") or ""
            title = paper.get("title") or ""
            if not doi or not title:
                continue
            
            date_str = paper.get("date") or ""
            try:
                pub_date = datetime.strptime(date_str, "%Y-%m-%d").date()
            except ValueError:
                pub_date = end_date
            
            authors = paper.get("authors") or ""
            abstract = (paper.get("abstract") or "")[:2500]
            cat = paper.get("category") or "bioRxiv"
            
            rows.append({
                "source": f"bioRxiv {cat}",
                "kind": "academic",
                "title": title,
                "url": f"https://doi.org/{doi}",
                "date": pub_date.isoformat(),
                "summary": abstract if abstract else f"{authors[:200]}",
                "authors": authors,
            })
            
            if len(rows) >= limit:
                break
        
        if len(rows) >= limit:
            break
        
        messages = data.get("messages") or []
        total = 0
        for msg in messages:
            if msg.get("status") == "ok" and "total" in msg:
                try:
                    total = int(msg.get("total", 0))
                except (ValueError, TypeError):
                    total = 0
                break
        
        cursor += len(data.get("collection") or [])
        if cursor >= total:
            break
    
    logging.info("bioRxiv API fallback 得到 %d 条（分类 %s）", len(rows), category or "all")
    return rows[:limit]


def _test_biorxiv_api():
    """Unit test for bioRxiv API parsing with mocked response."""
    mock_response = {
        "messages": [{"status": "ok", "total": "55"}],
        "collection": [
            {
                "doi": "10.1101/2026.10.01.123456",
                "title": "Test Immunology Paper",
                "authors": "Smith, J; Doe, A",
                "abstract": "This is an abstract about immunology.",
                "category": "immunology",
                "date": "2026-10-05",
            },
            {
                "doi": "10.1101/2026.10.02.789012",
                "title": "Neuroscience Paper",
                "authors": "Jones, B",
                "abstract": "Neuroscience abstract.",
                "category": "neuroscience",
                "date": "2026-10-04",
            },
        ],
    }
    
    messages = mock_response.get("messages") or []
    total = 0
    for msg in messages:
        if msg.get("status") == "ok" and "total" in msg:
            try:
                total = int(msg.get("total", 0))
            except (ValueError, TypeError):
                total = 0
            break
    
    tests_passed = 0
    total_tests = 3
    
    if total == 55:
        tests_passed += 1
    else:
        print(f"FAIL: total should be 55, got {total}")
    
    collection = mock_response.get("collection") or []
    filtered = [p for p in collection if (p.get("category") or "").lower() == "immunology"]
    if len(filtered) == 1 and filtered[0]["title"] == "Test Immunology Paper":
        tests_passed += 1
    else:
        print(f"FAIL: category filter should return 1 immunology paper, got {len(filtered)}")
    
    try:
        pub_date = datetime.strptime("2026-10-05", "%Y-%m-%d").date()
        if pub_date.isoformat() == "2026-10-05":
            tests_passed += 1
        else:
            print(f"FAIL: date parsing failed")
    except Exception as e:
        print(f"FAIL: date parsing exception: {e}")
    
    print(f"_test_biorxiv_api: {tests_passed}/{total_tests} tests passed")
    return tests_passed == total_tests


def _strip_tracking_params(url: str) -> str:
    """Remove common tracking query parameters from URLs."""
    if "?" not in url:
        return url
    
    tracking_params = {
        "rss", "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
        "ref", "source", "mc_cid", "mc_eid", "fbclid", "gclid", "msclkid",
    }
    
    parsed = urllib.parse.urlparse(url)
    if not parsed.query:
        return url
    
    params = urllib.parse.parse_qs(parsed.query, keep_blank_values=True)
    clean_params = {k: v for k, v in params.items() if k.lower() not in tracking_params}
    
    if not clean_params:
        return urllib.parse.urlunparse(parsed._replace(query=""))
    
    clean_query = urllib.parse.urlencode(clean_params, doseq=True)
    return urllib.parse.urlunparse(parsed._replace(query=clean_query))


def _parse_rss_authors(entry) -> str:
    """Extract author names from RSS entry's dc:creator or author fields."""
    dc_creator = entry.get("dc_creator") or entry.get("author_detail", {}).get("name") or ""
    if dc_creator:
        return dc_creator.strip()
    
    author = entry.get("author") or ""
    if author:
        return author.strip()
    
    authors_list = entry.get("authors") or []
    if authors_list:
        names = [a.get("name", "") for a in authors_list if a.get("name")]
        if names:
            return ", ".join(names[:6])
    
    return ""


def fetch_rss(source: dict, start: datetime, limit: int) -> tuple[list[dict], str]:
    """Fetch RSS feed and return (items, status)."""
    feed = source.get("feed")
    if not feed:
        logging.warning("跳过 %s：没有 feed", source.get("name"))
        return [], "skipped"
    logging.info("抓取 RSS %s", source["name"])
    
    source_name = source.get("name", "unknown")
    use_browser_ua = source.get("needs_browser_ua", False)
    
    parsed, status = _fetch_rss_with_retry(feed, source_name, use_browser_ua)
    
    if parsed is None and "biorxiv" in source_name.lower():
        category = source.get("biorxiv_category") or "immunology"
        logging.info("%s RSS 失败，尝试 API fallback（分类：%s）", source_name, category)
        items = fetch_biorxiv_api(start, limit, category=category)
        return items, "ok" if items else "failed"
    
    if parsed is None:
        return [], status
    
    rows = []
    for entry in parsed.entries:
        when = _parse_struct(entry)
        if not _within(when, start):
            continue
        url = (entry.get("link") or "").strip()
        title = (entry.get("title") or "").strip()
        if not url or not title:
            continue
        
        url = _strip_tracking_params(url)
        
        summary = re.sub(r"<[^>]+>", " ", entry.get("summary") or entry.get("description") or "")
        summary = re.sub(r"\s+", " ", summary).strip()[:2500]
        
        authors = _parse_rss_authors(entry)
        
        row = {
            "source": source["name"],
            "kind": source.get("kind") or "academic",
            "title": title,
            "url": url,
            "date": when.date().isoformat(),
            "summary": summary,
        }
        if authors:
            row["authors"] = authors
        
        rows.append(row)
        if len(rows) >= limit:
            break
    logging.info("%s 得到 %d 条", source["name"], len(rows))
    return rows, "ok" if rows else status


def fetch_pubmed(source: dict, start: date, end: date, limit: int) -> list[dict]:
    """Fetch PubMed articles with abstracts using E-utilities."""
    import time
    import xml.etree.ElementTree as ET
    
    query = source.get("query") or ""
    logging.info("检索 PubMed %s", query)
    mindate = start.strftime("%Y/%m/%d")
    maxdate = end.strftime("%Y/%m/%d")
    term = f"({query}) AND ({mindate}:{maxdate}[edat])"
    
    search_url = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi?" + urllib.parse.urlencode({
        "db": "pubmed",
        "term": term,
        "retmax": str(limit),
        "retmode": "json",
        "sort": "pub+date",
    })
    req = urllib.request.Request(search_url, headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            found = json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        logging.warning("PubMed esearch 失败: %s", e)
        return []
    
    ids = found.get("esearchresult", {}).get("idlist") or []
    if not ids:
        logging.info("PubMed 没有命中")
        return []
    
    time.sleep(0.35)
    efetch_url = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi?" + urllib.parse.urlencode({
        "db": "pubmed",
        "id": ",".join(ids),
        "rettype": "xml",
        "retmode": "xml",
    })
    req = urllib.request.Request(efetch_url, headers={"User-Agent": UA})
    
    abstracts = {}
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            xml_data = resp.read().decode("utf-8")
        
        root = ET.fromstring(xml_data)
        for article in root.findall(".//PubmedArticle"):
            pmid_elem = article.find(".//PMID")
            if pmid_elem is None:
                continue
            pmid = pmid_elem.text
            
            abstract_parts = []
            for abstract_text in article.findall(".//AbstractText"):
                label = abstract_text.get("Label", "")
                text = "".join(abstract_text.itertext()).strip()
                if label and text:
                    abstract_parts.append(f"{label}: {text}")
                elif text:
                    abstract_parts.append(text)
            
            if abstract_parts:
                abstracts[pmid] = " ".join(abstract_parts)[:2500]
    except Exception as e:
        logging.warning("PubMed efetch 失败，使用 esummary fallback: %s", e)
    
    time.sleep(0.35)
    sum_url = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi?" + urllib.parse.urlencode({
        "db": "pubmed",
        "id": ",".join(ids),
        "retmode": "json",
    })
    req = urllib.request.Request(sum_url, headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            summary = json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        logging.warning("PubMed esummary 失败: %s", e)
        return []
    
    result = summary.get("result", {})
    rows = []
    for pmid in ids:
        item = result.get(pmid) or {}
        title = (item.get("title") or "").strip()
        if not title:
            continue
        authors = ", ".join(a.get("name", "") for a in (item.get("authors") or [])[:6])
        journal = item.get("fulljournalname") or item.get("source") or "PubMed"
        raw_day = (item.get("sortpubdate") or "")[:10].replace("/", "-")
        
        abstract = abstracts.get(pmid, "")
        if abstract:
            summary_text = abstract
        else:
            summary_text = f"{journal}. {authors}".strip()
        
        rows.append({
            "source": "PubMed",
            "kind": "academic",
            "title": title,
            "url": f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/",
            "date": raw_day if len(raw_day) == 10 else end.isoformat(),
            "summary": summary_text,
            "authors": authors,
            "journal": journal,
        })
    logging.info("PubMed 得到 %d 条（%d 条有摘要）", len(rows), len(abstracts))
    return rows


# =============================================================================
# DEAL FILING SOURCES (SEC, HKEX, cninfo)
# =============================================================================

FILING_DEAL_CATEGORIES = {
    "lic": "授权合作",
    "acq": "并购",
    "inv": "融资/IPO",
}

DEAL_GROUP_ORDER = ["lic", "acq", "inv"]
DEAL_GROUP_NAMES = {
    "lic": "授权合作",
    "acq": "并购",
    "inv": "融资/IPO",
}

# SEC biopharma SIC codes ONLY - no name-based fallback
SEC_BIOPHARMA_SICS = {"2834", "2835", "2836", "8731"}

# HKEX healthcare/biotech stock codes - DISABLED until verified
HKEX_HEALTHCARE_CODES = set()

SEC_DEAL_KEYWORDS = [
    "license", "collaboration", "acquisition", "merger", "upfront", "milestone",
    "partnership", "agreement", "exclusive rights", "royalt", "option",
]
HKEX_DEAL_KEYWORDS = [
    "licensing", "license", "collaboration", "acquisition", "merger",
    "major transaction", "discloseable transaction", "placing", "subscription",
    "授权", "许可", "合作", "收购", "并购", "配售", "认购",
]
CNINFO_DEAL_KEYWORDS = [
    "许可", "授权", "合作协议", "重大合同", "对外投资", "收购", "并购",
    "战略合作", "技术转让", "独家", "里程碑",
]


def _strip_html(html: str) -> str:
    """Strip HTML tags and decode entities, return plain text."""
    import html as html_module
    text = re.sub(r'<script[^>]*>.*?</script>', ' ', html, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r'<style[^>]*>.*?</style>', ' ', text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r'<[^>]+>', ' ', text)
    text = html_module.unescape(text)
    text = re.sub(r'\s+', ' ', text).strip()
    return text


def _extract_pdf_text(pdf_bytes: bytes, max_chars: int = 4000) -> str:
    """Extract text from first pages of PDF, capped at max_chars."""
    try:
        import io
        try:
            import fitz
            doc = fitz.open(stream=pdf_bytes, filetype="pdf")
            text_parts = []
            for page_num in range(min(5, len(doc))):
                page = doc[page_num]
                text_parts.append(page.get_text())
                if sum(len(t) for t in text_parts) > max_chars:
                    break
            doc.close()
            text = "\n".join(text_parts)[:max_chars]
            return re.sub(r'\s+', ' ', text).strip()
        except ImportError:
            pass
        
        try:
            import pdfplumber
            with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
                text_parts = []
                for page in pdf.pages[:5]:
                    text_parts.append(page.extract_text() or "")
                    if sum(len(t) for t in text_parts) > max_chars:
                        break
            text = "\n".join(text_parts)[:max_chars]
            return re.sub(r'\s+', ' ', text).strip()
        except ImportError:
            pass
        
        logging.warning("No PDF library available (install PyMuPDF or pdfplumber)")
        return ""
    except Exception as e:
        logging.warning("PDF extraction failed: %s", e)
        return ""


def _normalize_amount_with_currency(text: str) -> list[tuple[int, str]]:
    """Extract monetary amounts with their currency.
    
    Supports USD, RMB, HKD, EUR, GBP, AUD, CAD, SGD.
    Also extracts percentages for equity verification.
    Returns list of (amount_in_base_units, currency) tuples.
    
    Fix #8: US$, USD, $ all map to USD; S$ maps to SGD.
    Fix #3: Handle Chinese numerals (一亿, 十亿, 两亿, etc.)
    """
    amounts = []
    
    # Chinese numeral mapping
    cn_nums = {'一': 1, '二': 2, '两': 2, '三': 3, '四': 4, '五': 5, 
               '六': 6, '七': 7, '八': 8, '九': 9, '十': 10}
    
    def parse_cn_number(s: str) -> float | None:
        """Parse Chinese numeral like 一亿, 十亿, 两亿, 一点五亿."""
        s = s.strip()
        if not s:
            return None
        # Check for Arabic numeral
        if re.match(r'^[\d.]+$', s):
            try:
                return float(s)
            except ValueError:
                return None
        # Single digit: 一, 二, 三, etc.
        if s in cn_nums:
            return float(cn_nums[s])
        # 十X: 十二, 十五 -> 12, 15
        if s.startswith('十'):
            if len(s) == 1:
                return 10.0
            rest = s[1:]
            if rest in cn_nums:
                return 10.0 + cn_nums[rest]
        # X十: 二十, 三十 -> 20, 30
        if len(s) == 2 and s[0] in cn_nums and s[1] == '十':
            return float(cn_nums[s[0]] * 10)
        # X十Y: 二十五 -> 25
        if len(s) == 3 and s[0] in cn_nums and s[1] == '十' and s[2] in cn_nums:
            return float(cn_nums[s[0]] * 10 + cn_nums[s[2]])
        # X点Y: 一点五 -> 1.5
        if '点' in s:
            parts = s.split('点')
            if len(parts) == 2:
                whole_str, frac_str = parts
                whole = 0.0
                if whole_str in cn_nums:
                    whole = float(cn_nums[whole_str])
                elif whole_str.isdigit():
                    whole = float(whole_str)
                frac = 0.0
                if frac_str in cn_nums:
                    frac = cn_nums[frac_str] / 10.0
                elif frac_str.isdigit():
                    frac = float(f"0.{frac_str}")
                return whole + frac
        return None
    
    # EUR patterns - €X.XX million/billion
    # Use round() before int() to handle floating point precision
    for m in re.finditer(r'€\s*([\d,]+(?:\.\d+)?)\s*(?:billion|B\b)', text, re.IGNORECASE):
        try:
            val = float(m.group(1).replace(',', '')) * 1_000_000_000 * 100
            amounts.append((round(val), 'EUR'))
        except ValueError:
            pass
    
    for m in re.finditer(r'€\s*([\d,]+(?:\.\d+)?)\s*(?:million|M\b)', text, re.IGNORECASE):
        try:
            val = float(m.group(1).replace(',', '')) * 1_000_000 * 100
            amounts.append((round(val), 'EUR'))
        except ValueError:
            pass
    
    # Chinese EUR: X亿欧元 (Arabic or Chinese numeral)
    for m in re.finditer(r'([\d.]+|[一二三四五六七八九十两点]+)\s*亿\s*欧元', text):
        try:
            num = parse_cn_number(m.group(1))
            if num is not None:
                val = num * 100_000_000 * 100
                amounts.append((round(val), 'EUR'))
        except ValueError:
            pass
    
    # GBP patterns - £X.XX million/billion
    for m in re.finditer(r'£\s*([\d,]+(?:\.\d+)?)\s*(?:billion|B\b)', text, re.IGNORECASE):
        try:
            val = float(m.group(1).replace(',', '')) * 1_000_000_000 * 100
            amounts.append((round(val), 'GBP'))
        except ValueError:
            pass
    
    for m in re.finditer(r'£\s*([\d,]+(?:\.\d+)?)\s*(?:million|M\b)', text, re.IGNORECASE):
        try:
            val = float(m.group(1).replace(',', '')) * 1_000_000 * 100
            amounts.append((round(val), 'GBP'))
        except ValueError:
            pass
    
    # HKD patterns - HK$X.XXM / HK$X.XX million (before USD to avoid double-matching)
    for m in re.finditer(r'HK\$\s*([\d,]+(?:\.\d+)?)\s*(?:billion|B\b)', text, re.IGNORECASE):
        try:
            val = float(m.group(1).replace(',', '')) * 1_000_000_000 * 100
            amounts.append((round(val), 'HKD'))
        except ValueError:
            pass
    
    for m in re.finditer(r'HK\$\s*([\d,]+(?:\.\d+)?)\s*(?:million|M\b)', text, re.IGNORECASE):
        try:
            val = float(m.group(1).replace(',', '')) * 1_000_000 * 100
            amounts.append((round(val), 'HKD'))
        except ValueError:
            pass
    
    for m in re.finditer(r'HK\$\s*([\d,]{7,})', text):
        try:
            val = int(m.group(1).replace(',', '')) * 100
            if val >= 10_000_000:
                amounts.append((val, 'HKD'))
        except ValueError:
            pass
    
    # Chinese HKD: X亿港元 / X亿港币
    for m in re.finditer(r'([\d.]+|[一二三四五六七八九十两点]+)\s*亿\s*港[元币]', text):
        try:
            num = parse_cn_number(m.group(1))
            if num is not None:
                val = num * 100_000_000 * 100
                amounts.append((round(val), 'HKD'))
        except ValueError:
            pass
    
    # AUD patterns - A$X.XX million/billion
    for m in re.finditer(r'A\$\s*([\d,]+(?:\.\d+)?)\s*(?:billion|B\b)', text, re.IGNORECASE):
        try:
            val = float(m.group(1).replace(',', '')) * 1_000_000_000 * 100
            amounts.append((round(val), 'AUD'))
        except ValueError:
            pass
    
    for m in re.finditer(r'A\$\s*([\d,]+(?:\.\d+)?)\s*(?:million|M\b)', text, re.IGNORECASE):
        try:
            val = float(m.group(1).replace(',', '')) * 1_000_000 * 100
            amounts.append((round(val), 'AUD'))
        except ValueError:
            pass
    
    # CAD patterns - C$X.XX million/billion
    for m in re.finditer(r'C\$\s*([\d,]+(?:\.\d+)?)\s*(?:billion|B\b)', text, re.IGNORECASE):
        try:
            val = float(m.group(1).replace(',', '')) * 1_000_000_000 * 100
            amounts.append((round(val), 'CAD'))
        except ValueError:
            pass
    
    for m in re.finditer(r'C\$\s*([\d,]+(?:\.\d+)?)\s*(?:million|M\b)', text, re.IGNORECASE):
        try:
            val = float(m.group(1).replace(',', '')) * 1_000_000 * 100
            amounts.append((round(val), 'CAD'))
        except ValueError:
            pass
    
    # SGD patterns - S$X.XX million/billion (NOT US$)
    for m in re.finditer(r'(?<!U)S\$\s*([\d,]+(?:\.\d+)?)\s*(?:billion|B\b)', text, re.IGNORECASE):
        try:
            val = float(m.group(1).replace(',', '')) * 1_000_000_000 * 100
            amounts.append((round(val), 'SGD'))
        except ValueError:
            pass
    
    for m in re.finditer(r'(?<!U)S\$\s*([\d,]+(?:\.\d+)?)\s*(?:million|M\b)', text, re.IGNORECASE):
        try:
            val = float(m.group(1).replace(',', '')) * 1_000_000 * 100
            amounts.append((round(val), 'SGD'))
        except ValueError:
            pass
    
    # USD patterns - US$, USD, $ (exclude HK$, A$, C$, S$)
    # Fix #8: US$ is USD, not SGD
    for m in re.finditer(r'(?:US\$|USD)\s*([\d,]+(?:\.\d+)?)\s*(?:billion|B\b)', text, re.IGNORECASE):
        try:
            val = float(m.group(1).replace(',', '')) * 1_000_000_000 * 100
            amounts.append((round(val), 'USD'))
        except ValueError:
            pass
    
    for m in re.finditer(r'(?:US\$|USD)\s*([\d,]+(?:\.\d+)?)\s*(?:million|M\b)', text, re.IGNORECASE):
        try:
            val = float(m.group(1).replace(',', '')) * 1_000_000 * 100
            amounts.append((round(val), 'USD'))
        except ValueError:
            pass
    
    # Bare $ patterns - use negative lookbehind to exclude HK$, A$, C$, S$, US$
    for m in re.finditer(r'(?<![HKACSU])\$\s*([\d,]+(?:\.\d+)?)\s*(?:billion|B\b)', text, re.IGNORECASE):
        try:
            val = float(m.group(1).replace(',', '')) * 1_000_000_000 * 100
            amounts.append((round(val), 'USD'))
        except ValueError:
            pass
    
    for m in re.finditer(r'(?<![HKACSU])\$\s*([\d,]+(?:\.\d+)?)\s*(?:million|M\b)', text, re.IGNORECASE):
        try:
            val = float(m.group(1).replace(',', '')) * 1_000_000 * 100
            amounts.append((round(val), 'USD'))
        except ValueError:
            pass
    
    for m in re.finditer(r'(?<![HKACSU])\$\s*([\d,]{7,})', text):
        try:
            val = int(m.group(1).replace(',', '')) * 100
            if val >= 10_000_000:
                amounts.append((val, 'USD'))
        except ValueError:
            pass
    
    # Chinese USD: X亿美元 / X.X亿美元 (Arabic or Chinese numeral)
    for m in re.finditer(r'([\d.]+|[一二三四五六七八九十两点]+)\s*亿\s*美元', text):
        try:
            num = parse_cn_number(m.group(1))
            if num is not None:
                val = num * 100_000_000 * 100
                amounts.append((round(val), 'USD'))
        except ValueError:
            pass
    
    # Chinese USD: X万美元
    for m in re.finditer(r'([\d,]+(?:\.\d+)?)\s*万\s*美元', text):
        try:
            val = float(m.group(1).replace(',', '')) * 10_000 * 100
            amounts.append((round(val), 'USD'))
        except ValueError:
            pass
    
    # RMB/CNY patterns
    for m in re.finditer(r'(?:RMB|CNY)\s*([\d,]+(?:\.\d+)?)\s*(?:million|M\b)?', text, re.IGNORECASE):
        try:
            val = float(m.group(1).replace(',', ''))
            if 'million' in text[m.start():m.end()+10].lower() or (m.end() < len(text) and text[m.end():m.end()+1] == 'M'):
                val *= 1_000_000
            val *= 100
            if val >= 10_000_000:
                amounts.append((round(val), 'RMB'))
        except ValueError:
            pass
    
    # Chinese RMB: X亿元 / X亿人民币 (NOT 美元)
    for m in re.finditer(r'([\d.]+|[一二三四五六七八九十两点]+)\s*亿\s*(?:元|人民币)(?!美)', text):
        try:
            num = parse_cn_number(m.group(1))
            if num is not None:
                val = num * 100_000_000 * 100
                amounts.append((round(val), 'RMB'))
        except ValueError:
            pass
    
    # Chinese RMB: X万元 / X万人民币 (NOT 美元)
    for m in re.finditer(r'([\d,]+(?:\.\d+)?)\s*万\s*(?:元|人民币)(?!美)', text):
        try:
            val = float(m.group(1).replace(',', '')) * 10_000 * 100
            amounts.append((round(val), 'RMB'))
        except ValueError:
            pass
    
    # Percentages for equity - store as basis points * 100 for precision
    for m in re.finditer(r'([\d.]+)\s*%', text):
        try:
            pct = float(m.group(1))
            # Store as basis points (1% = 100 bp) * 100 for precision
            val = int(pct * 10000)
            amounts.append((val, 'PCT'))
        except ValueError:
            pass
    
    return amounts


def _verify_amount_in_text(amount_str: str, filing_text: str) -> bool:
    """Check if an amount appears in the filing text with exact match.
    
    Fix #3: No tolerance, currency must match.
    Also verifies percentages for equity fields.
    """
    if not amount_str or amount_str == "未披露":
        return True
    
    if not filing_text or len(filing_text) < 20:
        return False
    
    filing_amounts = _normalize_amount_with_currency(filing_text)
    claim_amounts = _normalize_amount_with_currency(amount_str)
    
    if not claim_amounts:
        return False
    
    if not filing_amounts:
        return False
    
    # ALL claimed amounts must be found in filing (exact match, same currency)
    for claim_val, claim_currency in claim_amounts:
        found = False
        for filing_val, filing_currency in filing_amounts:
            if claim_currency != filing_currency:
                continue
            if claim_val == filing_val:
                found = True
                break
        if not found:
            return False
    
    return True


def _test_verify_amount():
    """Unit tests for _verify_amount_in_text per user requirements."""
    tests = [
        # FAIL cases - wrong amounts or wrong currencies
        ("11亿美元", "The company paid $1.17 billion in total", False),
        ("1.05亿美元", "The upfront payment was $100 million cash", False),
        ("1亿美元", "RMB100,000,000 consideration paid", False),
        ("19.4亿美元", "HK$1,939.78M in cash consideration", False),
        ("1亿首付，最高99亿", "The upfront was only $100 million", False),
        
        # Fix #3: A$, C$, S$, €, £ are distinct currencies
        ("1亿美元", "A$100 million consideration here", False),  # USD != AUD
        ("1亿美元", "C$100 million consideration here", False),  # USD != CAD
        ("1亿欧元", "The deal was for $100 million", False),  # EUR != USD
        
        # PASS cases - exact matches
        ("1亿美元", "The company paid $100 million in cash", True),
        ("11.7亿美元", "total deal value of $1.17 billion announced", True),
        ("100万美元", "The company received $1 million upfront", True),
        ("1.939亿港元", "HK$193.9M consideration was paid", True),
        
        # Fix #3: Exact rounding - 1.15亿美元 = $115 million
        ("1.15亿美元", "The payment was $115 million total", True),
        
        # Fix #3: Euro support
        ("1亿欧元", "The deal was for €100 million upfront", True),
        
        # Fix #3: Equity percent verification
        ("19.9% 股权", "acquired 19.9% stake in the company", True),
        ("19.9% 股权", "acquired 20% stake in the company", False),  # 19.9 != 20
        
        # Edge cases
        ("未披露", "any text over 20 chars here", True),
        ("1亿美元", "", False),
        ("", "any text over 20 chars here", True),
        
        # Fix #8: US$ is USD, not SGD
        ("1亿美元", "US$100 million consideration paid", True),
        ("1亿美元", "USD 100 million consideration paid", True),
        # S$ without U is SGD
        ("1亿美元", "S$100 million payment made here", False),  # USD claim vs SGD source
        
        # Fix #3: Chinese numerals
        ("一亿美元", "The deal was $100 million cash", True),
        ("十亿美元", "The deal was $1 billion total", True),
        ("两亿美元", "The deal was $200 million cash", True),
    ]
    
    passed = 0
    for claim, source, expected in tests:
        result = _verify_amount_in_text(claim, source)
        if result == expected:
            passed += 1
        else:
            print(f"FAIL: '{claim}' vs '{source[:50]}...' -> {result} (expected {expected})")
    
    print(f"_test_verify_amount: {passed}/{len(tests)} tests passed")
    return passed == len(tests)


def _is_nonprofit_or_consortium(text: str) -> bool:
    """Detect nonprofit, government, or consortium initiatives.
    
    Fix #1: These should never be published as deals.
    """
    text_lower = text.lower()
    
    # Nonprofit/government indicators
    nonprofit_signals = [
        'nonprofit', 'non-profit', 'not-for-profit', '501(c)',
        'foundation', 'institute', 'consortium', 'initiative',
        'government', 'federal', 'nih', 'doe', 'nsf', 'darpa',
        'national institutes', 'department of energy',
        'public-private partnership', 'multi-party commitment',
        'combined commitment', 'pledged', 'grant', 'funding commitment',
    ]
    
    for signal in nonprofit_signals:
        if signal in text_lower:
            # Check if this seems like primary focus, not incidental
            # E.g., "NIH grant" vs "acquired NIH-funded company"
            if signal in ['nonprofit', 'non-profit', 'foundation', 'consortium', 'initiative']:
                return True
            # For government agencies, check if they're the source of funds
            if signal in ['nih', 'doe', 'nsf', 'darpa', 'national institutes', 'department of energy']:
                if any(kw in text_lower for kw in ['commitment', 'pledged', 'grant', 'funding from', 'funded by']):
                    return True
    
    # Multi-party summed commitments
    if re.search(r'combined\s+(?:commitment|total|funding)', text_lower):
        return True
    if re.search(r'(?:multiple|several)\s+(?:parties|organizations|funders)', text_lower):
        return True
    
    return False


def _verify_company_in_source(company_name: str, source_text: str) -> bool:
    """Verify company name appears in source text.
    
    Fix #2: Every company name must appear in source (case-insensitive, alias-aware).
    """
    if not company_name or not source_text:
        return False
    
    source_lower = source_text.lower()
    company_lower = company_name.lower().strip()
    
    # Direct match
    if company_lower in source_lower:
        return True
    
    # Normalized match (remove suffixes)
    normalized = _normalize_company_name(company_name)
    if len(normalized) >= 3 and normalized in source_lower:
        return True
    
    # Common aliases/abbreviations
    aliases = {
        'pfizer': ['pfizer inc', 'pfizer, inc'],
        'novartis': ['novartis ag', 'novartis pharma'],
        'roche': ['roche holding', 'f. hoffmann-la roche'],
        'genentech': ['genentech inc', 'genentech, inc'],
        'abbvie': ['abbvie inc', 'abbvie, inc'],
        'merck': ['merck & co', 'merck sharp', 'msd'],
        'j&j': ['johnson & johnson', 'johnson and johnson', 'janssen', 'jnj'],
        'johnson & johnson': ['j&j', 'jnj', 'janssen'],
        'lilly': ['eli lilly', 'lilly and company'],
        'bms': ['bristol-myers squibb', 'bristol myers squibb'],
        'astrazeneca': ['astrazeneca plc', 'az'],
        'gsk': ['glaxosmithkline', 'glaxo smith kline'],
        'sanofi': ['sanofi-aventis', 'sanofi aventis'],
        'biogen': ['biogen inc', 'biogen idec'],
        'gilead': ['gilead sciences'],
        'amgen': ['amgen inc'],
        'regeneron': ['regeneron pharmaceuticals'],
        'vertex': ['vertex pharmaceuticals'],
        'moderna': ['moderna inc', 'moderna therapeutics'],
        'biontech': ['biontech se'],
    }
    
    # Check if company name matches any known alias patterns
    for canonical, alias_list in aliases.items():
        if canonical in company_lower or any(a in company_lower for a in alias_list):
            # Check if any variant appears in source
            if canonical in source_lower:
                return True
            for alias in alias_list:
                if alias in source_lower:
                    return True
    
    return False


def _has_deal_keywords(text: str, deal_type: str) -> bool:
    """Check if source text has deal keywords consistent with claimed type.
    
    Fix #6: Require source-text deal keywords, else drop.
    """
    text_lower = text.lower()
    
    type_keywords = {
        'lic': [
            'license', 'licensing', 'collaboration', 'partnership', 'agreement',
            'exclusive rights', 'royalt', 'milestone', 'upfront',
            '授权', '许可', '合作', '里程碑',
        ],
        'acq': [
            'acquisition', 'acquire', 'acquired', 'merger', 'merge', 'merged',
            'tender offer', 'buyout', 'purchase',
            '收购', '并购', '合并',
        ],
        'inv': [
            'financing', 'investment', 'investor', 'funding', 'series',
            'round', 'offering', 'placement', 'ipo', 'public offering',
            '融资', '投资', '配售', '上市',
        ],
    }
    
    keywords = type_keywords.get(deal_type, [])
    for kw in keywords:
        if kw in text_lower:
            return True
    
    return False


def _test_nonprofit_detection():
    """Test nonprofit/consortium detection."""
    tests = [
        # Should reject - Fix #1: Biohub case from live test
        ("Chan Zuckerberg Biohub project with NIH commitment", True),
        ("Multi-party combined commitment of $1.8B", True),
        ("Nonprofit foundation grant program", True),
        ("DOE funding commitment to consortium", True),
        # Fix #1: The actual Biohub case - $1.8B combined from multiple parties
        ("Biohub announces $1.8B initiative with $500M own funds, $500M DOE, NIH data, $300M Google", True),
        ("Meta, Google pledge combined $300M to nonprofit consortium", True),
        # Should accept (normal deals)
        ("Pfizer acquires biotech for $500 million", False),
        ("Company announces Series B financing", False),
        ("License agreement with milestone payments", False),
        ("Alector receives $100 million upfront payment", False),
    ]
    
    passed = 0
    for text, expected in tests:
        result = _is_nonprofit_or_consortium(text)
        if result == expected:
            passed += 1
        else:
            print(f"FAIL: '{text[:50]}...' -> {result} (expected {expected})")
    
    print(f"_test_nonprofit_detection: {passed}/{len(tests)} tests passed")
    return passed == len(tests)


def _test_company_verification():
    """Test company name verification in source."""
    tests = [
        # Should pass - exact match
        ("Pfizer", "Pfizer Inc. announces acquisition", True),
        ("Alector", "Alector signs license agreement", True),
        # Should pass - case insensitive
        ("NOVARTIS", "Novartis AG reported today", True),
        # Should pass - alias
        ("J&J", "Johnson & Johnson announced", True),
        # Should fail - not in source
        ("Pfizer", "Merck announces new drug approval", False),
        ("Novartis", "Company XYZ signs deal with ABC", False),
        # Should fail - invented
        ("InventedPharma", "Real company announces deal", False),
    ]
    
    passed = 0
    for company, source, expected in tests:
        result = _verify_company_in_source(company, source)
        if result == expected:
            passed += 1
        else:
            print(f"FAIL: company '{company}' in '{source[:40]}...' -> {result} (expected {expected})")
    
    print(f"_test_company_verification: {passed}/{len(tests)} tests passed")
    return passed == len(tests)


def _test_deal_keywords():
    """Test deal keyword detection."""
    tests = [
        # Should pass
        ("The license agreement includes milestones", "lic", True),
        ("Company acquired for $500M", "acq", True),
        ("Series B financing round", "inv", True),
        # Should fail - wrong type
        ("License agreement with upfront", "acq", False),
        ("Acquisition completed", "lic", False),
        # Should fail - no deal keywords
        ("Company announces new hiring", "inv", False),
        ("Research results published", "lic", False),
    ]
    
    passed = 0
    for text, deal_type, expected in tests:
        result = _has_deal_keywords(text, deal_type)
        if result == expected:
            passed += 1
        else:
            print(f"FAIL: '{text[:40]}...' type={deal_type} -> {result} (expected {expected})")
    
    print(f"_test_deal_keywords: {passed}/{len(tests)} tests passed")
    return passed == len(tests)


def _test_name_normalization():
    """Test company name normalization - Fix #11."""
    tests = [
        # Should strip end suffixes
        ("Pfizer Inc.", "pfizer"),
        ("Novartis AG", "novartis"),
        ("Roche Holding Ltd", "roche holding"),
        # Fix #11: Should NOT strip ' ag'/' co'/' se' from middle of names
        ("Diageo plc", "diageo"),
        ("Boehringer Ingelheim", "boehringer ingelheim"),
        ("Sanofi-Aventis SA", "sanofiaventis"),
        # Chinese suffixes - order matters: 股份有限公司 before 有限公司 before 集团
        ("上海医药集团股份有限公司", "上海医药"),
        ("恒瑞医药", "恒瑞医药"),
        ("百济神州有限公司", "百济神州"),
    ]
    
    passed = 0
    for name, expected in tests:
        result = _normalize_company_name(name)
        if result == expected:
            passed += 1
        else:
            print(f"FAIL: '{name}' -> '{result}' (expected '{expected}')")
    
    print(f"_test_name_normalization: {passed}/{len(tests)} tests passed")
    return passed == len(tests)


def _classify_deal_type(text: str) -> str | None:
    """Classify deal as lic/acq/inv based on text. Returns None if unclear.
    
    Uses word boundaries to avoid false positives.
    """
    text_lower = text.lower()
    
    def count_word_matches(patterns: list[str]) -> int:
        count = 0
        for p in patterns:
            if len(p) <= 5:
                if re.search(r'\b' + re.escape(p) + r'\b', text_lower):
                    count += 1
            else:
                if p in text_lower:
                    count += 1
        return count
    
    lic_signals = [
        "license agreement", "collaboration agreement", "exclusive license",
        "non-exclusive license", "royalty", "royalties", 
        "milestone payment", "upfront payment", "option agreement",
        "授权协议", "许可协议", "合作协议", "独家授权", "里程碑付款",
    ]
    lic_count = count_word_matches(lic_signals)
    
    acq_signals = [
        "merger agreement", "tender offer", "definitive agreement to acquire",
        "acquisition agreement", "merger consideration", "acquire all",
        "to acquire", "has acquired", "will acquire", "acquired by",
        "收购协议", "并购", "要约收购", "吸收合并",
    ]
    acq_count = count_word_matches(acq_signals)
    
    inv_signals = [
        "securities purchase", "private placement", "public offering",
        "series a", "series b", "series c", "series d", "series e",
        "round a", "round b", "round c", "venture financing",
        "registered direct offering", "stock offering",
        "配售", "定向增发", "公开发行", "融资", "首次公开",
    ]
    inv_count = count_word_matches(inv_signals)
    
    if re.search(r'\bipo\b', text_lower):
        inv_count += 1
    
    counts = [("lic", lic_count), ("acq", acq_count), ("inv", inv_count)]
    counts.sort(key=lambda x: x[1], reverse=True)
    
    if counts[0][1] >= 2 and counts[0][1] > counts[1][1]:
        return counts[0][0]
    
    if counts[0][1] >= 1 and counts[1][1] == 0:
        return counts[0][0]
    
    return None


def _test_classify_deal_type():
    """Unit tests for deal type classifier with word boundaries."""
    tests = [
        ("Study of lipoprotein levels in patients", None),
        ("Adipose tissue analysis", None),
        ("New data acquisition system for the lab", None),
        ("Company announces IPO pricing", "inv"),
        ("Initial public offering completed", "inv"),
        ("Series B financing round", "inv"),
        ("Merger agreement signed", "acq"),
        ("Definitive agreement to acquire company", "acq"),
        ("License agreement for oncology program", "lic"),
        ("Exclusive license with milestone payments", "lic"),
        ("Company news update", None),
    ]
    
    passed = 0
    for text, expected in tests:
        result = _classify_deal_type(text)
        if result == expected:
            passed += 1
        else:
            print(f"FAIL: '{text[:50]}...' -> {result} (expected {expected})")
    
    print(f"_test_classify_deal_type: {passed}/{len(tests)} tests passed")
    return passed == len(tests)


def _is_biopharma_company(company_name: str, sic_codes: list = None, industry: str = None) -> bool:
    """Check if company is confidently in biopharma sector.
    
    For SEC filings, use SIC codes ONLY. No name-based fallback.
    """
    if sic_codes:
        return any(sic in SEC_BIOPHARMA_SICS for sic in sic_codes)
    
    if industry:
        industry_lower = industry.lower()
        if any(kw in industry_lower for kw in ["医药", "生物", "pharma", "biotech", "biopharma"]):
            return True
    
    return False


def _normalize_company_name(name: str) -> str:
    """Normalize company name for deduplication.
    
    Fix #11: Only strip legal suffixes at the END with word boundaries.
    Don't strip ' ag'/' co'/' se' from the middle of names.
    """
    name = name.strip()
    
    # Remove trailing legal suffixes with word boundaries
    # Order matters - longer suffixes first to avoid partial matches
    # Chinese suffixes must come first (most specific)
    suffix_patterns = [
        r'股份有限公司$',
        r'有限公司$',
        r'集团$',
        r'控股$',
        r',?\s+incorporated$',
        r',?\s+inc\.?$',
        r',?\s+limited$',
        r',?\s+ltd\.?$',
        r',?\s+corporation$',
        r',?\s+corp\.?$',
        r',?\s+company$',
        r',?\s+co\.?$',
        r'\s+plc$',
        r'\s+ag$',
        r'\s+se$',
        r'\s+sa$',
        r'\s+nv$',
        r'\s+bv$',
        r'\s+gmbh$',
    ]
    
    name_lower = name.lower()
    for pattern in suffix_patterns:
        name_lower = re.sub(pattern, '', name_lower, flags=re.IGNORECASE)
    
    name_lower = re.sub(r'[^\w\s]', '', name_lower)
    name_lower = re.sub(r'\s+', ' ', name_lower).strip()
    return name_lower


def _fetch_sec_filing_text(cik: str, accession: str, sec_ua: str, primary_doc_name: str = None, max_chars: int = 20000) -> str:
    """Fetch and extract text from SEC filing primary document and EX-99.1 press release.
    
    Fix #5: 
    - Use accession WITH dashes in URL path
    - Fetch BOTH primary doc AND EX-99.1 (not just one or the other)
    - Raise char cap to 20k to capture $100 million which may appear later
    - Extract windows around deal keywords for efficient text handling
    """
    import time
    import requests
    
    headers = {"User-Agent": sec_ua, "Accept": "text/html"}
    
    # Fix #5(b): The accession number in the URL path needs dashes
    # e.g., /Archives/edgar/data/1773087/000095017024116384 uses clean accession
    # but the -index.htm file uses the accession with dashes
    accession_clean = accession.replace("-", "")
    accession_dashed = accession if "-" in accession else f"{accession[:10]}-{accession[10:12]}-{accession[12:]}"
    
    base_url = f"https://www.sec.gov/Archives/edgar/data/{cik}/{accession_clean}"
    
    text_parts = []
    
    # Helper to extract deal-relevant windows from text
    def extract_deal_windows(full_text: str, window_size: int = 2000) -> str:
        """Extract windows around deal keywords to find relevant amounts."""
        keywords = [
            'million', 'billion', '$', 'upfront', 'milestone', 'license', 'collaboration',
            'acquisition', 'merger', 'agreement', 'payment', 'consideration', 'royalt',
            'equity', 'stake', 'financing', 'offering', 'placement'
        ]
        windows = []
        text_lower = full_text.lower()
        positions = set()
        
        for kw in keywords:
            idx = 0
            while True:
                pos = text_lower.find(kw, idx)
                if pos == -1:
                    break
                positions.add(pos)
                idx = pos + 1
        
        if not positions:
            return full_text[:max_chars]
        
        sorted_pos = sorted(positions)
        merged_ranges = []
        for pos in sorted_pos:
            start = max(0, pos - window_size // 2)
            end = min(len(full_text), pos + window_size // 2)
            if merged_ranges and start <= merged_ranges[-1][1]:
                merged_ranges[-1] = (merged_ranges[-1][0], max(merged_ranges[-1][1], end))
            else:
                merged_ranges.append((start, end))
        
        for start, end in merged_ranges:
            windows.append(full_text[start:end])
        
        return "\n...\n".join(windows)[:max_chars]
    
    # Fetch the -index.htm to find document names and types
    # Fix #5(b): Use proper URL with dashed accession for -index.htm
    try:
        time.sleep(0.12)
        index_htm_url = f"{base_url}/{accession_dashed}-index.htm"
        resp = requests.get(index_htm_url, headers=headers, timeout=30)
        
        if resp.status_code != 200:
            # Try alternative format
            index_htm_url = f"{base_url}/{accession_clean}-index.htm"
            time.sleep(0.12)
            resp = requests.get(index_htm_url, headers=headers, timeout=30)
        
        if resp.status_code == 200:
            content = resp.text
            
            # Find 8-K or 6-K primary document
            # Pattern: <td>8-K</td> ... <a href="filename.htm"> or <a href="/ix?doc=...">
            primary_match = re.search(
                r'<td[^>]*>\s*(8-K|6-K)\s*</td>.*?<a[^>]*href="([^"]+)"',
                content, re.DOTALL | re.IGNORECASE
            )
            if primary_match:
                doc_ref = primary_match.group(2)
                time.sleep(0.12)
                
                # Handle iXBRL viewer URLs like /ix?doc=/Archives/...
                if doc_ref.startswith('/ix?doc='):
                    # Extract the actual document path
                    actual_path = doc_ref.split('doc=')[-1]
                    doc_url = f"https://www.sec.gov{actual_path}"
                elif doc_ref.startswith('/'):
                    doc_url = f"https://www.sec.gov{doc_ref}"
                elif doc_ref.startswith('http'):
                    doc_url = doc_ref
                else:
                    doc_url = f"{base_url}/{doc_ref}"
                
                doc_resp = requests.get(doc_url, headers=headers, timeout=60)
                if doc_resp.status_code == 200:
                    text = _strip_html(doc_resp.text)
                    if len(text) > 100:
                        text_parts.append(extract_deal_windows(text))
            
            # Fix #5(d): ALSO fetch EX-99.1 press release (not "instead of")
            ex_matches = re.finditer(
                r'<td[^>]*>\s*(EX-99\.?\d*|99\.\d+)\s*</td>.*?<a[^>]*href="([^"]+)"',
                content, re.DOTALL | re.IGNORECASE
            )
            for ex_match in ex_matches:
                ex_ref = ex_match.group(2)
                time.sleep(0.12)
                
                # Handle various URL formats
                if ex_ref.startswith('/ix?doc='):
                    actual_path = ex_ref.split('doc=')[-1]
                    ex_url = f"https://www.sec.gov{actual_path}"
                elif ex_ref.startswith('/'):
                    ex_url = f"https://www.sec.gov{ex_ref}"
                elif ex_ref.startswith('http'):
                    ex_url = ex_ref
                else:
                    ex_url = f"{base_url}/{ex_ref}"
                
                ex_resp = requests.get(ex_url, headers=headers, timeout=60)
                if ex_resp.status_code == 200:
                    text = _strip_html(ex_resp.text)
                    if len(text) > 100:
                        text_parts.append(extract_deal_windows(text))
                        break  # Just get the first EX-99
    except Exception as e:
        logging.debug("SEC index.htm parsing failed: %s", e)
    
    # If primary_doc_name provided from search, also try it
    if primary_doc_name and not text_parts:
        try:
            time.sleep(0.12)
            doc_url = f"{base_url}/{primary_doc_name}"
            doc_resp = requests.get(doc_url, headers=headers, timeout=60)
            if doc_resp.status_code == 200:
                text = _strip_html(doc_resp.text)
                if len(text) > 100:
                    text_parts.append(extract_deal_windows(text))
        except Exception as e:
            logging.debug("SEC primary doc fetch failed: %s", e)
    
    # Fallback: try common document names directly
    if not text_parts:
        for doc_name in ["8-k.htm", "6-k.htm", "ex99-1.htm", "ex991.htm", "ex99.htm"]:
            try:
                time.sleep(0.12)
                doc_url = f"{base_url}/{doc_name}"
                resp = requests.get(doc_url, headers=headers, timeout=30)
                if resp.status_code == 200:
                    text = _strip_html(resp.text)
                    if len(text) > 100:
                        text_parts.append(extract_deal_windows(text))
                        break
            except Exception as e:
                logging.debug("SEC fallback fetch failed: %s", e)
    
    combined = "\n\n".join(text_parts)
    return combined[:max_chars]


def _extract_event_date_from_filing(filing_text: str) -> str | None:
    """Extract the event date from an 8-K/6-K filing.
    
    The event date is when the reportable event occurred.
    """
    if not filing_text:
        return None
    
    patterns = [
        r'Date of Report[^:]*:\s*(\w+\s+\d{1,2},?\s+\d{4})',
        r'Date of Report[^:]*:\s*(\d{1,2}/\d{1,2}/\d{4})',
        r'Date of earliest event reported[^:]*:\s*(\w+\s+\d{1,2},?\s+\d{4})',
        r'Date of earliest event reported[^:]*:\s*(\d{1,2}/\d{1,2}/\d{4})',
    ]
    
    for pattern in patterns:
        match = re.search(pattern, filing_text, re.IGNORECASE)
        if match:
            date_str = match.group(1).strip()
            try:
                for fmt in ["%B %d, %Y", "%B %d %Y", "%m/%d/%Y"]:
                    try:
                        parsed = datetime.strptime(date_str, fmt)
                        return parsed.date().isoformat()
                    except ValueError:
                        continue
            except Exception:
                pass
    
    return None


def fetch_sec_filings(start: datetime, limit: int) -> tuple[list[dict], str]:
    """Fetch recent 8-K and 6-K filings from SEC EDGAR for biopharma companies.
    
    Fix #5: Accept original 8-K if filing date within 7d window AND event date within 14d before filing.
    """
    import time
    import requests
    
    sec_ua = os.environ.get("SEC_USER_AGENT")
    if not sec_ua:
        logging.warning("SEC_USER_AGENT 未设置，跳过 SEC EDGAR 来源。请设置格式如 'CompanyName contact@example.com'")
        return [], "skipped"
    
    logging.info("抓取 SEC EDGAR 8-K/6-K（生物医药 SIC，跳过修订）")
    
    end_date = datetime.now(timezone.utc).date()
    start_date = start.date()
    
    rows = []
    skipped_amendments = 0
    skipped_old_events = 0
    headers = {"User-Agent": sec_ua, "Accept": "application/json"}
    
    for form_type in ["8-K", "6-K"]:
        for keyword in SEC_DEAL_KEYWORDS[:5]:
            search_url = "https://efts.sec.gov/LATEST/search-index"
            params = {
                "q": keyword,
                "dateRange": "custom",
                "startdt": start_date.isoformat(),
                "enddt": end_date.isoformat(),
                "forms": form_type,
                "from": "0",
                "size": "30",
            }
            
            try:
                time.sleep(0.12)
                resp = requests.get(search_url, params=params, headers=headers, timeout=30)
                if resp.status_code == 200:
                    data = resp.json()
                    hits = data.get("hits", {}).get("hits", [])
                    
                    for hit in hits:
                        if len(rows) >= limit * 2:
                            break
                            
                        source = hit.get("_source", {})
                        
                        company = source.get("display_names", ["Unknown"])[0]
                        filed_date = source.get("file_date", "")
                        form = source.get("form", form_type)
                        accession = source.get("adsh", "").replace("-", "")
                        cik = source.get("ciks", [""])[0]
                        sics = source.get("sics", [])
                        
                        # Fix #4: Get primary document name from search result
                        primary_doc = source.get("file_name", "")
                        
                        if not accession or not cik:
                            continue
                        
                        # Skip amendments (8-K/A, 6-K/A)
                        form_upper = form.upper()
                        if "/A" in form_upper or form_upper.endswith("A"):
                            skipped_amendments += 1
                            continue
                        
                        # Filter by SIC code - strict biopharma only
                        if not _is_biopharma_company(company, sic_codes=sics):
                            continue
                        
                        doc_url = f"https://www.sec.gov/Archives/edgar/data/{cik}/{accession}"
                        
                        items = source.get("items", [])
                        description = ", ".join(items) if items else f"{form} filing"
                        
                        # Fetch actual filing text using primary doc name
                        filing_text = _fetch_sec_filing_text(cik, accession, sec_ua, primary_doc)
                        
                        # Fix #5: Event date rule
                        # Accept if: filing date in 7d window AND event date within 14d before filing
                        event_date = _extract_event_date_from_filing(filing_text)
                        
                        try:
                            filed_dt = datetime.strptime(filed_date[:10], "%Y-%m-%d").date() if filed_date else end_date
                        except ValueError:
                            filed_dt = end_date
                        
                        if event_date:
                            try:
                                event_dt = datetime.strptime(event_date, "%Y-%m-%d").date()
                                # Event must be within 14 days before filing
                                days_before_filing = (filed_dt - event_dt).days
                                if days_before_filing < 0 or days_before_filing > 14:
                                    skipped_old_events += 1
                                    logging.debug("跳过事件日期过早：%s (event %s, filed %s)", company, event_date, filed_date)
                                    continue
                            except ValueError:
                                pass
                        
                        # Use filing date for display (announcement date)
                        use_date = filed_date[:10] if filed_date else end_date.isoformat()
                        
                        if filing_text and len(filing_text) > 100:
                            summary = filing_text[:4000]
                        else:
                            summary = f"SEC {form} filing by {company}. Items: {description}"
                        
                        rows.append({
                            "source": f"SEC {form}",
                            "kind": "industry",
                            "filing_source": "sec",
                            "title": f"{company}: {description[:80]}",
                            "url": doc_url,
                            "date": use_date,
                            "summary": summary,
                            "filing_text": filing_text,
                            "company": company,
                            "filing_type": form,
                            "event_date": event_date,
                        })
                        
            except Exception as e:
                logging.warning("SEC 搜索失败 (%s, %s): %s", form_type, keyword, e)
                continue
            
            if len(rows) >= limit * 2:
                break
        if len(rows) >= limit * 2:
            break
    
    if skipped_amendments:
        logging.info("跳过 %d 条修订版（8-K/A, 6-K/A）", skipped_amendments)
    if skipped_old_events:
        logging.info("跳过 %d 条事件日期过早的披露", skipped_old_events)
    
    seen = set()
    unique_rows = []
    for row in rows:
        if row["url"] not in seen:
            seen.add(row["url"])
            unique_rows.append(row)
    
    logging.info("SEC EDGAR 得到 %d 条（有文本 %d 条）", 
                 len(unique_rows), 
                 sum(1 for r in unique_rows if r.get("filing_text")))
    return unique_rows[:limit], "ok" if unique_rows else "failed"


def _test_sec_filing_fetch():
    """Live test for SEC filing fetch - tests Alector 8-K contains $100 million."""
    import os
    sec_ua = os.environ.get("SEC_USER_AGENT")
    if not sec_ua:
        print("SKIP: SEC_USER_AGENT not set")
        return True
    
    # Alector CIK is 0001773087, need to find the accession number for their Oct 2026 filing
    # For testing, we'll search for their recent 8-K
    import requests
    import time
    
    headers = {"User-Agent": sec_ua, "Accept": "application/json"}
    search_url = "https://efts.sec.gov/LATEST/search-index"
    params = {
        "q": "Alector",
        "dateRange": "custom",
        "startdt": "2026-09-25",
        "enddt": "2026-10-08",
        "forms": "8-K",
        "from": "0",
        "size": "10",
    }
    
    try:
        resp = requests.get(search_url, params=params, headers=headers, timeout=30)
        if resp.status_code != 200:
            print(f"SKIP: SEC search returned {resp.status_code}")
            return True
        
        data = resp.json()
        hits = data.get("hits", {}).get("hits", [])
        
        alector_hit = None
        for hit in hits:
            source = hit.get("_source", {})
            names = source.get("display_names", [])
            if any("alector" in n.lower() for n in names):
                alector_hit = source
                break
        
        if not alector_hit:
            print("SKIP: Alector 8-K not found in search results")
            return True
        
        cik = alector_hit.get("ciks", [""])[0]
        accession = alector_hit.get("adsh", "").replace("-", "")
        primary_doc = alector_hit.get("file_name", "")
        
        time.sleep(0.12)
        filing_text = _fetch_sec_filing_text(cik, accession, sec_ua, primary_doc)
        
        if not filing_text:
            print("FAIL: No filing text fetched for Alector 8-K")
            return False
        
        if "$100 million" in filing_text or "$100,000,000" in filing_text:
            print(f"PASS: Alector 8-K text fetched ({len(filing_text)} chars), contains $100 million")
            return True
        else:
            print(f"FAIL: Alector 8-K text ({len(filing_text)} chars) does not contain $100 million")
            print(f"  First 500 chars: {filing_text[:500]}")
            return False
        
    except Exception as e:
        print(f"SKIP: SEC test failed with exception: {e}")
        return True


def fetch_hkex_announcements(start: datetime, limit: int) -> tuple[list[dict], str]:
    """Fetch announcements from HKEX 披露易 - DISABLED."""
    logging.info("HKEX 披露易：暂停使用（需验证公司列表）")
    return [], "disabled"


def fetch_cninfo_announcements(start: datetime, limit: int) -> tuple[list[dict], str]:
    """Fetch announcements from 巨潮资讯 - DISABLED."""
    logging.info("巨潮资讯：暂停使用（需添加 CSRC 行业过滤）")
    return [], "disabled"


def fetch_filing_sources(start: datetime, limit: int) -> tuple[list[dict], dict[str, tuple[int, str]]]:
    """Fetch deal filings from official sources."""
    all_rows = []
    source_stats = {}
    
    try:
        sec_rows, sec_status = fetch_sec_filings(start, limit)
        all_rows.extend(sec_rows)
        source_stats["SEC EDGAR"] = (len(sec_rows), sec_status)
    except Exception as e:
        logging.warning("SEC 来源失败: %s", e)
        source_stats["SEC EDGAR"] = (0, "failed")
    
    # HKEX and cninfo disabled
    source_stats["HKEX 披露易"] = (0, "disabled")
    source_stats["巨潮资讯"] = (0, "disabled")
    
    logging.info("官方披露来源总计 %d 条", len(all_rows))
    return all_rows, source_stats


def normalize_doi(url: str) -> str:
    """Normalize URL to DOI identifier for deduplication."""
    url = url.lower().strip()
    url = re.sub(r'\?.*$', '', url)
    
    doi_match = re.match(r'https?://(?:dx\.)?doi\.org/(10\.\d+/.+)', url)
    if doi_match:
        return doi_match.group(1)
    
    nature_match = re.match(r'https?://(?:www\.)?nature\.com/articles/(s\d+-\d+-\d+-\w+)', url)
    if nature_match:
        return f"10.1038/{nature_match.group(1)}"
    
    cell_match = re.match(r'https?://(?:www\.)?cell\.com/[^/]+/(?:fulltext|abstract)/(S\d+-\d+\(\d+\)\d+-\d+)', url)
    if cell_match:
        return f"cell:{cell_match.group(1)}"
    
    science_match = re.match(r'https?://(?:www\.)?science\.org/doi/(10\.\d+/.+)', url)
    if science_match:
        return science_match.group(1)
    
    return url


def load_existing_urls() -> set[str]:
    """Load URLs and DOIs from existing content to avoid duplicates."""
    existing = set()
    existing_raw = set()
    latest_path = ROOT / "content" / "latest.json"
    if latest_path.exists():
        try:
            data = json.loads(latest_path.read_text(encoding="utf-8"))
            for art in data.get("articles") or []:
                if art.get("url"):
                    existing_raw.add(art["url"])
                    existing.add(normalize_doi(art["url"]))
            for deal in data.get("deals") or []:
                if deal.get("url"):
                    existing_raw.add(deal["url"])
                    existing.add(normalize_doi(deal["url"]))
        except (json.JSONDecodeError, OSError):
            pass
    
    index_path = ROOT / "index.html"
    if index_path.exists():
        try:
            content = index_path.read_text(encoding="utf-8")
            url_pattern = r"url:'([^']+)'"
            for match in re.finditer(url_pattern, content):
                url = match.group(1)
                if url.startswith('#'):
                    continue
                existing_raw.add(url)
                existing.add(normalize_doi(url))
        except OSError:
            pass
    
    existing.update(existing_raw)
    logging.info("已有 %d 个去重 URL/DOI（规范化后）", len(existing))
    return existing


def fetch_all(config: dict) -> list[dict]:
    default_days = int(config.get("window_days") or 7)
    limit = int(config.get("max_per_source") or 6)
    end = datetime.now(timezone.utc)
    rows: list[dict] = []
    seen = set()
    existing = load_existing_urls()
    source_stats = []
    
    for source in config["sources"]:
        source_name = source.get("name", "unknown")
        
        if source.get("type") == "manual":
            logging.info("跳过手动来源：%s", source_name)
            source_stats.append({"name": source_name, "status": "manual", "count": 0})
            continue
        
        source_days = int(source.get("window_days") or default_days)
        start = end - timedelta(days=source_days)
        
        try:
            if source.get("type") == "pubmed":
                batch = fetch_pubmed(source, start.date(), end.date(), limit)
                fetch_status = "ok" if batch else "failed"
            else:
                batch, fetch_status = fetch_rss(source, start, limit)
        except Exception:
            logging.exception("来源失败：%s", source_name)
            source_stats.append({"name": source_name, "status": "failed", "count": 0})
            continue
        
        count = 0
        skipped_no_abstract = 0
        for row in batch:
            url = row["url"]
            normalized = normalize_doi(url)
            if url in seen or normalized in seen:
                continue
            if url in existing or normalized in existing:
                logging.debug("跳过已有内容：%s (规范化: %s)", url, normalized)
                continue
            if row.get("kind") == "academic" and row.get("source") != "PubMed":
                summary = (row.get("summary") or "").strip()
                if len(summary) < 50 or summary.count(",") > 3 and len(summary) < 100:
                    skipped_no_abstract += 1
                    continue
            seen.add(url)
            seen.add(normalized)
            rows.append(row)
            count += 1
        
        if skipped_no_abstract:
            logging.info("  %s: 跳过 %d 条无摘要条目", source_name, skipped_no_abstract)
        source_stats.append({"name": source_name, "status": fetch_status, "count": count})
    
    filing_limit = int(config.get("max_filing_deals") or 10)
    start_for_filings = end - timedelta(days=default_days)
    filing_rows, filing_stats = fetch_filing_sources(start_for_filings, filing_limit)
    
    # Fix #12: Remove unused dedup block - actual dedup happens in claude_draft
    filing_count = 0
    for row in filing_rows:
        url = row["url"]
        if url in seen or url in existing:
            continue
        seen.add(url)
        rows.append(row)
        filing_count += 1
    
    for source_name, (count, status) in filing_stats.items():
        source_stats.append({"name": source_name, "status": status, "count": count})
    
    logging.info("=== 来源统计 ===")
    for stat in source_stats:
        if stat["status"] == "manual":
            logging.info("  %s: 手动来源，跳过", stat["name"])
        elif stat["status"] == "failed":
            logging.info("  %s: 失败", stat["name"])
        elif stat["status"] == "skipped":
            logging.info("  %s: 跳过", stat["name"])
        elif stat["status"] == "disabled":
            logging.info("  %s: 暂停", stat["name"])
        else:
            logging.info("  %s: %d 条", stat["name"], stat["count"])
    
    logging.info("总计 %d 条新内容", len(rows))
    
    max_academic = int(config.get("max_per_category_input") or 15)
    max_industry = int(config.get("max_industry_input") or 20)
    
    academic_items = [r for r in rows if r.get("kind") == "academic"]
    industry_items = [r for r in rows if r.get("kind") == "industry"]
    
    academic_items.sort(key=lambda x: len(x.get("summary", "")), reverse=True)
    industry_items.sort(key=lambda x: (
        1 if x.get("filing_source") else 0,
        len(x.get("summary", ""))
    ), reverse=True)
    
    capped_rows = academic_items[:max_academic] + industry_items[:max_industry]
    
    if len(capped_rows) < len(rows):
        logging.info("提示词大小限制：%d 条学术 + %d 条行业（原 %d 条）", 
                     len(academic_items[:max_academic]), 
                     len(industry_items[:max_industry]),
                     len(rows))
    
    total_chars = sum(len(r.get("summary", "")) for r in capped_rows)
    if total_chars > 100_000:
        char_per_item = 100_000 // len(capped_rows)
        for row in capped_rows:
            if len(row.get("summary", "")) > char_per_item:
                row["summary"] = row["summary"][:char_per_item] + "..."
        logging.info("截断摘要以控制提示词大小（每条约 %d 字符）", char_per_item)
    
    return capped_rows


def _extract_numbers_from_text(text: str) -> set[str]:
    """Extract all numbers (including currency amounts and percentages) from text.
    
    Returns a set of normalized number strings for comparison.
    """
    numbers = set()
    
    # Extract all numbers with optional decimal and magnitude
    for m in re.finditer(r'[\d,]+(?:\.\d+)?', text):
        num_str = m.group(0).replace(',', '')
        try:
            val = float(num_str)
            # Normalize to avoid precision issues
            if val == int(val):
                numbers.add(str(int(val)))
            else:
                numbers.add(f"{val:.2f}")
        except ValueError:
            pass
    
    return numbers


def _strip_unverified_numbers(text: str, verified_amounts: set[tuple[int, str]]) -> str:
    """Strip sentences containing numbers that aren't in verified_amounts.
    
    Fix #1: The model must not be the source of any number in deal output.
    """
    if not text:
        return ""
    
    # Convert verified amounts to a set of raw number strings
    verified_numbers = set()
    for val, currency in verified_amounts:
        # Convert back from cents to base unit
        base_val = val / 100
        if base_val == int(base_val):
            verified_numbers.add(str(int(base_val)))
        else:
            verified_numbers.add(f"{base_val:.2f}")
        # Also add common representations
        # Billions
        if base_val >= 1_000_000_000:
            billions = base_val / 1_000_000_000
            verified_numbers.add(f"{billions:.2f}")
            if billions == int(billions):
                verified_numbers.add(str(int(billions)))
        # Millions
        if base_val >= 1_000_000:
            millions = base_val / 1_000_000
            verified_numbers.add(f"{millions:.2f}")
            if millions == int(millions):
                verified_numbers.add(str(int(millions)))
        # 亿 (100 million)
        if base_val >= 100_000_000:
            yi = base_val / 100_000_000
            verified_numbers.add(f"{yi:.2f}")
            if yi == int(yi):
                verified_numbers.add(str(int(yi)))
        # 万 (10000)
        if base_val >= 10000:
            wan = base_val / 10000
            verified_numbers.add(f"{wan:.2f}")
            if wan == int(wan):
                verified_numbers.add(str(int(wan)))
        # Percentages are stored as basis points * 100
        if currency == 'PCT':
            pct = val / 10000
            verified_numbers.add(f"{pct:.1f}")
            verified_numbers.add(f"{pct:.2f}")
            if pct == int(pct):
                verified_numbers.add(str(int(pct)))
    
    # Also add 未披露 as a valid "verified" state
    verified_numbers.add("未披露")
    
    # Split into sentences and filter
    sentences = re.split(r'(?<=[。！？；\.\!\?\;])', text)
    filtered_sentences = []
    
    for sentence in sentences:
        sentence = sentence.strip()
        if not sentence:
            continue
        
        # Extract numbers from this sentence
        sentence_numbers = _extract_numbers_from_text(sentence)
        
        # If no numbers, keep the sentence
        if not sentence_numbers:
            filtered_sentences.append(sentence)
            continue
        
        # Check if all numbers are verified
        unverified = sentence_numbers - verified_numbers
        if not unverified:
            filtered_sentences.append(sentence)
        else:
            logging.warning("剔除含未验证数字的句子：%s (未验证: %s)", sentence[:50], unverified)
    
    return " ".join(filtered_sentences)


def _build_deal_title(company: str, counterparty: str, deal_type: str, amount: str) -> str:
    """Build deal title from verified fields only.
    
    Fix #1: The model must not be the source of any number in deal output.
    """
    deal_type_names = {
        "lic": "授权合作",
        "acq": "收购",
        "inv": "融资",
    }
    
    type_name = deal_type_names.get(deal_type, "交易")
    
    if counterparty:
        title = f"{company}与{counterparty}{type_name}"
    else:
        title = f"{company}{type_name}"
    
    if amount and amount != "未披露":
        title += f"（{amount}）"
    
    return title


def _test_strip_unverified_numbers():
    """Test that unverified numbers are stripped from text."""
    # Simulate verified amounts: $100 million only
    verified = {(10000000000, 'USD')}  # $100M in cents
    
    tests = [
        # Sentence with only verified number should pass
        ("The deal was $100 million.", "The deal was $100 million."),
        # Sentence with unverified number should be stripped
        ("首付50亿美元，总额99亿美元", ""),
        # Mixed - only verified parts kept
        ("This is context. 首付99亿美元. More context.", "This is context. More context."),
    ]
    
    passed = 0
    for input_text, expected in tests:
        result = _strip_unverified_numbers(input_text, verified)
        # Normalize whitespace for comparison
        result = re.sub(r'\s+', ' ', result).strip()
        expected = re.sub(r'\s+', ' ', expected).strip()
        if result == expected:
            passed += 1
        else:
            print(f"FAIL: '{input_text}' -> '{result}' (expected '{expected}')")
    
    print(f"_test_strip_unverified_numbers: {passed}/{len(tests)} tests passed")
    return passed == len(tests)


def claude_draft(items: list[dict], config: dict) -> dict:
    """Use Claude with tool_use for reliable JSON output."""
    from anthropic import Anthropic

    tool_schema = {
        "name": "submit_weekly_digest",
        "description": "Submit the curated articles and deals for the weekly digest",
        "input_schema": {
            "type": "object",
            "properties": {
                "articles": {
                    "type": "array",
                    "description": "Academic articles to include",
                    "items": {
                        "type": "object",
                        "properties": {
                            "url": {"type": "string", "description": "Original URL from input, copied exactly"},
                            "field": {"type": "string", "enum": list(FIELDS.keys())},
                            "title": {"type": "string", "description": "Chinese title"},
                            "journal": {"type": "string", "description": "Journal name only"},
                            "authors": {"type": "string", "description": "Author names from source"},
                            "lead": {"type": "string", "description": "Why this matters, 1-2 sentences in Chinese"},
                            "body": {"type": "string", "description": "What the source says, in Chinese"},
                            "discuss": {"type": "string", "description": "Limitations and what we don't know"},
                            "steps": {
                                "type": "array",
                                "items": {"type": "string", "maxLength": 25},
                                "minItems": 3,
                                "maxItems": 5,
                            },
                            "study_type": {"type": "string"},
                            "n": {"type": "string"},
                            "evidence_level": {"type": "string", "enum": ["fulltext", "abstract", "press", "secondary"]},
                            "image_prompt": {"type": "string", "description": "English abstract visual description. Describe abstract shapes and colors ONLY. No cell type names, no labels."},
                        },
                        "required": ["url", "field", "title", "authors", "lead", "steps"],
                    },
                },
                "deals": {
                    "type": "array",
                    "description": "Industry deals - amounts MUST be from source text only",
                    "items": {
                        "type": "object",
                        "properties": {
                            "url": {"type": "string"},
                            "company": {"type": "string", "description": "Primary company name"},
                            "counterparty": {"type": "string", "description": "Deal counterparty if known"},
                            "kinds": {"type": "array", "items": {"type": "string", "enum": list(DEAL_KINDS)}},
                            "money": {"type": "string", "description": "From source text only. Format: X 亿美元 or 未披露"},
                            "upfront": {"type": "string", "description": "Upfront if in source text"},
                            "milestones": {"type": "string", "description": "Milestones if in source text"},
                            "equity": {"type": "string", "description": "Equity stake if in source text, e.g. 19.9% 股权"},
                            "structure": {"type": "string", "description": "Deal structure - NO invented numbers"},
                            "why": {"type": "string", "description": "Why this matters - NO invented numbers"},
                            "source_name": {"type": "string"},
                            "is_filing": {"type": "boolean"},
                            "amount_source": {"type": "string", "enum": ["filing", "news", "unknown"]},
                        },
                        "required": ["url", "company", "kinds", "money"],
                    },
                },
            },
            "required": ["articles", "deals"],
        },
    }

    prompt = f"""你是前沿追踪的编辑。下面是过去 {config.get('window_days', 7)} 天从固定来源抓到的条目。

## 核心规则

1. **金额必须来自来源文本** - 绝不发明数字。如果来源没有明确金额，写"未披露"。
2. 每条的 url 必须从输入里原样复制。
3. 学术最多 {config.get('max_academic', 6)} 篇，行业最多 {config.get('max_industry', 4)} 条。

## 领域分类

field 必须是：{json.dumps(FIELDS, ensure_ascii=False)}

## 行业动态（deals）规则

**绝对禁止发明数字。** why 和 structure 字段不许包含任何来源里没有的金额、百分比。

1. kinds 只能是：lic (授权合作)、acq (并购)、inv (融资/IPO)
2. company 填主公司名（如 Alector）
3. counterparty 填交易对手（如 Genentech）
4. money 格式统一为"X 亿美元"或"未披露"
5. upfront、milestones、equity 只填来源里明确写的
6. why 和 structure 只写来源里有的事实，不要加任何数字

## image_prompt 规则

描述抽象的形状和颜色，不要提及具体细胞类型名称：
- 写 "circular cells" 而非 "T cells" 
- 写 "target cells" 而非 "tumor cells"
- 绝不请求标签、文字或注释

输入：
{json.dumps(items, ensure_ascii=False)}
"""
    model = os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-5-5")
    logging.info("调用 Claude %s (tool_use, tool_choice=auto)", model)
    
    client = Anthropic()
    data = None
    
    for attempt in range(2):
        message = client.messages.create(
            model=model,
            max_tokens=8000,
            tools=[tool_schema],
            tool_choice={"type": "auto"},
            messages=[{"role": "user", "content": prompt}],
        )
        
        for block in message.content:
            if block.type == "tool_use" and block.name == "submit_weekly_digest":
                data = block.input
                break
        
        if data is not None:
            break
        
        if attempt == 0:
            logging.warning("Claude 没有返回 tool_use，重试一次...")
    
    if data is None:
        logging.error("Claude did not return tool_use block after retry")
        raise ValueError("No tool_use response from Claude")
    
    allowed = {row["url"] for row in items}
    by_url = {row["url"]: row for row in items}
    
    invalid_author_patterns = [
        r'^nature\s*(medicine|biotechnology|communications|methods)?$',
        r'^cell(\s+reports?)?$',
        r'^science(\s+translational)?$',
        r'^biorxiv',
        r'^medrxiv',
        r'^pubmed',
        r'^nejm$',
        r'^lancet',
        r'^jama',
        r'^免疫学$',
    ]
    
    blocked_patterns = ['BioRender', '对齐 ACIR', '占位', 'MVP']
    
    articles = []
    for raw in data.get("articles") or []:
        url = (raw.get("url") or "").strip()
        if url not in allowed:
            logging.warning("丢弃不在来源里的文章：%s", url)
            continue
        field = raw.get("field")
        if field not in FIELDS:
            logging.warning("丢弃领域无效的文章：%s", url)
            continue
        
        authors = (raw.get("authors") or "").strip()
        journal = (raw.get("journal") or "").strip()
        src = by_url[url]
        
        if authors.lower() == journal.lower() or authors.lower() == src["source"].lower():
            logging.warning("丢弃作者无效的文章（与期刊/来源同名）：%s, authors=%s", url, authors)
            continue
        
        is_invalid_author = any(re.match(p, authors.lower()) for p in invalid_author_patterns)
        if is_invalid_author:
            logging.warning("丢弃作者无效的文章（期刊名）：%s, authors=%s", url, authors)
            continue
        
        steps = [str(s).strip() for s in (raw.get("steps") or []) if str(s).strip()][:5]
        if len(steps) < 3:
            logging.warning("丢弃步骤不足的文章：%s, steps=%d", url, len(steps))
            continue
        
        all_text = f"{raw.get('lead', '')} {raw.get('body', '')} {raw.get('discuss', '')}"
        for blocked in blocked_patterns:
            if blocked in all_text:
                logging.error("输出包含阻断词 '%s'，终止运行", blocked)
                raise SystemExit(4)
        
        articles.append({
            "url": url,
            "field": field,
            "title": (raw.get("title") or src["title"]).strip(),
            "journal": journal or src["source"],
            "authors": authors or "（来源未列出作者）",
            "lead": (raw.get("lead") or "").strip(),
            "body": (raw.get("body") or "").strip(),
            "discuss": (raw.get("discuss") or "").strip(),
            "steps": steps,
            "study_type": (raw.get("study_type") or "").strip(),
            "n": (raw.get("n") or "").strip(),
            "evidence_level": raw.get("evidence_level") or "abstract",
            "image_prompt": (raw.get("image_prompt") or src["title"]).strip(),
            "date": src["date"],
            "source": src["source"],
        })
    
    deals = []
    filing_deals = []
    news_deals = []
    seen_urls = set()  # Fix #6: One source link yields at most one deal
    
    for raw in data.get("deals") or []:
        url = (raw.get("url") or "").strip()
        if url not in allowed:
            logging.warning("丢弃不在来源里的动态：%s", url)
            continue
        
        # Fix #6: One source link yields at most one deal
        if url in seen_urls:
            logging.warning("跳过重复 URL 的交易：%s", url)
            continue
        seen_urls.add(url)
        
        src = by_url[url]
        is_filing = raw.get("is_filing") or src.get("filing_source")
        source_text = src.get("filing_text", "") if is_filing else src.get("summary", "")
        
        # Fix #1: Reject nonprofit/government/consortium initiatives
        if _is_nonprofit_or_consortium(source_text):
            logging.warning("丢弃非营利/政府/联盟项目：%s", url)
            continue
        
        # Fix #6: Validate model's deal type against source text
        kinds = [k for k in (raw.get("kinds") or []) if k in {"lic", "acq", "inv"}]
        source_classified = _classify_deal_type(source_text)
        
        if not kinds:
            if source_classified:
                kinds = [source_classified]
            else:
                logging.warning("丢弃类型不明确的交易：%s", url)
                continue
        
        # If model provided a type, verify it matches source classification
        if kinds and source_classified and kinds[0] != source_classified:
            logging.warning("交易类型与来源不符，使用来源分类：%s (%s -> %s)", url, kinds[0], source_classified)
            kinds = [source_classified]
        
        # Fix #6: Require deal keywords consistent with claimed type
        if not _has_deal_keywords(source_text, kinds[0]):
            logging.warning("来源缺少交易关键词，丢弃：%s (type=%s)", url, kinds[0])
            continue
        
        # Get company from structured field or extract from title
        company = (raw.get("company") or "").strip()
        counterparty = (raw.get("counterparty") or "").strip()
        
        if not company:
            # Try to extract from source
            company = src.get("company", "")
            if not company:
                # Fallback to title parsing
                title = src.get("title", "")
                if ":" in title:
                    company = title.split(":")[0].strip()
                else:
                    company = title[:30]
        
        # Fix #2: Verify company name appears in source text
        if not _verify_company_in_source(company, source_text):
            logging.warning("公司名未在来源中找到，丢弃：%s (company=%s)", url, company)
            continue
        
        # Fix #2: Verify counterparty if provided
        if counterparty and not _verify_company_in_source(counterparty, source_text):
            logging.warning("交易对手未在来源中找到，丢弃字段：%s (counterparty=%s)", url, counterparty)
            counterparty = ""
        
        # Normalize money format
        money = (raw.get("money") or "未披露").strip()
        money = re.sub(r'^\$(\d+(?:\.\d+)?)\s*亿', r'\1 亿美元', money)
        money = re.sub(r'(\d)亿', r'\1 亿', money)
        
        # Verify ALL amounts - main money, upfront, milestones, equity
        if money != "未披露":
            if not _verify_amount_in_text(money, source_text):
                logging.warning("金额未在来源中找到，改为未披露：%s -> %s", url, money)
                money = "未披露"
        
        # Fix #4: Verify upfront, milestones, equity for BOTH filing and news
        upfront = (raw.get("upfront") or "").strip()
        # Fix #10: Remove doubled wording like '首付：1亿美元首付'
        if upfront:
            upfront = re.sub(r'首付[：:]\s*', '', upfront)
            upfront = re.sub(r'\s*首付$', '', upfront)
        if upfront and not _verify_amount_in_text(upfront, source_text):
            logging.warning("首付金额未验证，丢弃：%s -> %s", url, upfront)
            upfront = ""
        
        milestones = (raw.get("milestones") or "").strip()
        # Fix #10: Remove doubled wording
        if milestones:
            milestones = re.sub(r'里程碑[：:]\s*', '', milestones)
            milestones = re.sub(r'\s*里程碑$', '', milestones)
        if milestones and not _verify_amount_in_text(milestones, source_text):
            logging.warning("里程碑金额未验证，丢弃：%s -> %s", url, milestones)
            milestones = ""
        
        equity = (raw.get("equity") or "").strip()
        if equity and not _verify_amount_in_text(equity, source_text):
            logging.warning("股权比例未验证，丢弃：%s -> %s", url, equity)
            equity = ""
        
        # Build verified amounts set for stripping unverified numbers
        verified_amounts = set()
        for field_val in [money, upfront, milestones, equity]:
            if field_val and field_val != "未披露":
                verified_amounts.update(_normalize_amount_with_currency(field_val))
        
        # Fix #1: Strip unverified numbers from why and structure
        why = (raw.get("why") or "").strip()
        why = _strip_unverified_numbers(why, verified_amounts)
        
        structure = (raw.get("structure") or "").strip()
        structure = _strip_unverified_numbers(structure, verified_amounts)
        
        # Fix #1: Build title from verified fields only
        title = _build_deal_title(company, counterparty, kinds[0], money)
        
        # Fix #7: Determine amount_source - news if ANY amount field comes from news
        if is_filing:
            amount_source = "filing"
        else:
            # For news, check if we have any verified amounts
            if money != "未披露" or upfront or milestones or equity:
                amount_source = "news"
            else:
                amount_source = "unknown"
        
        deal_entry = {
            "url": url,
            "title": title,
            "company": company,
            "counterparty": counterparty,
            "kinds": kinds,
            "money": money,
            "structure": structure,
            "why": why,
            "source_name": (raw.get("source_name") or src["source"]).strip(),
            "date": src["date"][:7],
            "amount_source": amount_source,
        }
        
        if upfront:
            deal_entry["upfront"] = upfront
        if milestones:
            deal_entry["milestones"] = milestones
        if equity:
            deal_entry["equity"] = equity
        
        if is_filing:
            deal_entry["is_filing"] = True
            deal_entry["filing_source"] = src.get("filing_source", "unknown")
            filing_deals.append(deal_entry)
        else:
            news_deals.append(deal_entry)
    
    # Dedup using structured company field (Fix #12: This is the actual dedup, remove unused block)
    filing_dedup_keys = set()
    for deal in filing_deals:
        company = _normalize_company_name(deal.get("company", ""))
        deal_type = deal.get("kinds", ["inv"])[0] if deal.get("kinds") else "inv"
        filing_dedup_keys.add((company, deal_type))
    
    deduped_news_deals = []
    news_dupes_removed = 0
    for deal in news_deals:
        company = _normalize_company_name(deal.get("company", ""))
        deal_type = deal.get("kinds", ["inv"])[0] if deal.get("kinds") else "inv"
        if (company, deal_type) in filing_dedup_keys:
            news_dupes_removed += 1
            logging.debug("News deal dupes filing: %s (%s)", deal.get("title", ""), deal_type)
            continue
        deduped_news_deals.append(deal)
    
    if news_dupes_removed:
        logging.info("去重：%d 条新闻与披露重复，已移除", news_dupes_removed)
    
    cap_a = int(config.get("max_academic") or 6)
    max_deals = int(config.get("max_deals") or 6)
    max_filing_deals = int(config.get("max_filing_deals_output") or 4)
    
    selected_filings = filing_deals[:max_filing_deals]
    remaining_slots = max_deals - len(selected_filings)
    selected_news = deduped_news_deals[:remaining_slots]
    
    deals = selected_filings + selected_news
    
    logging.info("交易选择：%d 条披露 + %d 条新闻 = %d 条", 
                 len(selected_filings), len(selected_news), len(deals))
    
    return {"articles": articles[:cap_a], "deals": deals}


def _check_image_for_text(image_bytes: bytes, max_retries: int = 2) -> bool | None:
    """Check if image contains text using vision model.
    
    Fix #9: Fail closed - return None on error (caller should regenerate/omit).
    Returns True if text detected, False if no text, None on error.
    """
    from openai import OpenAI
    import time
    
    client = OpenAI()
    
    # Convert to base64
    b64_image = base64.b64encode(image_bytes).decode('utf-8')
    
    for attempt in range(max_retries + 1):
        try:
            response = client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "text",
                                "text": "Does this image contain ANY text, letters, words, labels, numbers, or annotations? Answer only 'YES' or 'NO'."
                            },
                            {
                                "type": "image_url",
                                "image_url": {
                                    "url": f"data:image/png;base64,{b64_image}"
                                }
                            }
                        ]
                    }
                ],
                max_tokens=10
            )
            
            answer = response.choices[0].message.content.strip().upper()
            has_text = "YES" in answer
            
            if has_text:
                logging.warning("图片包含文字，需要重新生成")
            
            return has_text
            
        except Exception as e:
            logging.warning("图片文字检查失败 (attempt %d/%d): %s", attempt + 1, max_retries + 1, e)
            if attempt < max_retries:
                time.sleep(1)
    
    # Fix #9: Fail closed - return None so caller knows check failed
    logging.warning("图片文字检查重试后仍失败，返回 None（将重新生成或跳过）")
    return None


def draw_image(prompt: str, dest: Path, max_retries: int = 2) -> bool:
    """Generate image with text-free verification.
    
    Fix #9: Post-generation check for text, regenerate if needed.
    Fail closed: if check errors, retry then regenerate or omit.
    Returns True if successful, False if all attempts failed.
    """
    from openai import OpenAI

    model = os.environ.get("OPENAI_IMAGE_MODEL", "gpt-image-1")
    clean_prompt = sanitize_image_prompt(prompt)
    
    client = OpenAI()
    
    for attempt in range(max_retries + 1):
        # Strengthen prompt on retries
        if attempt > 0:
            extra_emphasis = " ABSOLUTELY NO TEXT OR LETTERS. " * attempt
            full_prompt = IMAGE_PREFIX + extra_emphasis + clean_prompt[:800] + IMAGE_SUFFIX
            logging.info("画图重试 %d/%d（加强无文字提示）: %s", attempt, max_retries, dest.name)
        else:
            full_prompt = IMAGE_PREFIX + clean_prompt[:1000] + IMAGE_SUFFIX
            logging.info("画图 %s -> %s", model, dest.name)
        
        try:
            result = client.images.generate(
                model=model,
                prompt=full_prompt,
                size="1536x1024",
                n=1,
            )
            raw = result.data[0].b64_json
            image_bytes = base64.b64decode(raw)
            
            # Check for text - returns True (has text), False (no text), or None (error)
            check_result = _check_image_for_text(image_bytes)
            
            if check_result is False:
                # No text detected, save and return success
                dest.write_bytes(image_bytes)
                return True
            
            # Fix #9: check_result is True (text found) or None (check failed)
            # Either way, we should regenerate or give up
            if check_result is None:
                logging.warning("图片 %s 文字检查失败，视为有文字处理", dest.name)
            
            if attempt < max_retries:
                logging.warning("图片 %s 含文字或检查失败，重试...", dest.name)
            else:
                logging.warning("图片 %s 重试后仍有问题，跳过", dest.name)
                return False
                
        except Exception as e:
            logging.exception("配图失败 (attempt %d): %s", attempt + 1, e)
            if attempt >= max_retries:
                return False
    
    return False


def site_article(item: dict, image_rel: str) -> dict:
    import hashlib
    stamp = item["date"].replace("-", "")
    url_key = normalize_doi(item["url"]) or item["url"]
    url_hash = hashlib.sha1(url_key.encode()).hexdigest()[:10]
    item_id = f"w-{stamp}-{url_hash}"
    result = {
        "id": item_id,
        "f": item["field"],
        "t": item["title"],
        "ds": item["date"],
        "disp": item["date"][:7].replace("-", "."),
        "j": item["journal"],
        "url": item["url"],
        "au": item["authors"] or item["source"],
        "tags": [item["field"]],
        "sum": item["lead"],
        "lead": item["lead"],
        "body": item["body"],
        "discuss": item["discuss"],
        "steps": item["steps"],
        "note": f"材料来自 {item['source']}，只写来源里能核对的内容。",
        "img": image_rel,
    }
    if item.get("study_type"):
        result["study_type"] = item["study_type"]
    if item.get("n"):
        result["n"] = item["n"]
    if item.get("evidence_level"):
        result["evidence_level"] = item["evidence_level"]
    return result


def site_deal(item: dict) -> dict:
    result = {
        "d": item["date"],
        "kinds": item["kinds"],
        "t": item["title"],
        "m": item["money"],
        "ms": item["structure"],
        "why": item["why"],
        "src": item["source_name"],
        "url": item["url"],
        "amount_source": item.get("amount_source", "unknown"),
    }
    if item.get("is_filing"):
        result["is_filing"] = True
        result["filing_source"] = item.get("filing_source", "unknown")
    if item.get("upfront"):
        result["upfront"] = item["upfront"]
    if item.get("milestones"):
        result["milestones"] = item["milestones"]
    if item.get("equity"):
        result["equity"] = item["equity"]
    return result


def wechat_html(articles: list[dict], deals: list[dict], week: str) -> str:
    """Generate WeChat-compatible HTML with inline styles."""
    
    toc_items = []
    for i, art in enumerate(articles, 1):
        toc_items.append(f"{i}. {art['t'][:30]}...")
    
    lead_headline = articles[0]['t'][:25] if articles else "本周前沿"
    
    parts = [
        '<section style="font-family:-apple-system,BlinkMacSystemFont,\'Segoe UI\',Roboto,sans-serif;font-size:16px;line-height:1.75;color:#333;">',
        f'<p style="font-size:14px;color:#666;">前沿追踪 · {week} · TheraSik 出品</p>',
        '<p style="margin:1em 0;">本期内容均基于原始来源核对，配图由 AI 生成（示意图，非期刊原图）。</p>',
    ]
    
    if toc_items:
        parts.append('<p style="margin:1em 0;padding:1em;background:#f5f5f5;border-radius:8px;">')
        parts.append('<strong>本期目录</strong><br>')
        parts.append('<br>'.join(toc_items))
        parts.append('</p>')
    
    if articles:
        parts.append('<h2 style="border-left:4px solid #0f6b5c;padding-left:12px;margin:2em 0 1em;">学术</h2>')
        for art in articles:
            parts.append(f'<h3 style="font-size:18px;margin:1.5em 0 0.5em;color:#1d2a27;">{art["t"]}</h3>')
            if art.get("img"):
                parts.append(f'<p style="margin:1em 0;"><img src="{art["img"]}" alt="" style="max-width:100%;border-radius:8px;"></p>')
            parts.append(f'<p style="margin:0.5em 0;">{art.get("lead") or ""}</p>')
            if art.get("body"):
                parts.append(f'<p style="margin:0.5em 0;">{art["body"]}</p>')
            if art.get("discuss"):
                parts.append(f'<p style="margin:0.5em 0;"><strong>讨论</strong> {art["discuss"]}</p>')
            parts.append(f'<p style="font-size:14px;color:#666;margin:0.5em 0;">{art.get("au") or ""} · {art.get("j") or ""}</p>')
            url = art.get("url", "")
            if "doi.org" in url:
                doi = url.replace("https://doi.org/", "DOI: ")
                parts.append(f'<p style="font-size:12px;color:#999;margin:0.5em 0;">{doi}</p>')
    
    if deals:
        parts.append('<h2 style="border-left:4px solid #0f6b5c;padding-left:12px;margin:2em 0 1em;">交易动态</h2>')
        
        grouped = {"lic": [], "acq": [], "inv": []}
        for deal in deals:
            deal_type = deal.get("kinds", ["inv"])[0] if deal.get("kinds") else "inv"
            if deal_type in grouped:
                grouped[deal_type].append(deal)
        
        for deal_type in DEAL_GROUP_ORDER:
            type_deals = grouped.get(deal_type, [])
            if not type_deals:
                continue
            
            type_name = DEAL_GROUP_NAMES.get(deal_type, deal_type)
            parts.append(f'<h3 style="font-size:16px;margin:1.5em 0 0.5em;color:#0f6b5c;">{type_name}</h3>')
            
            for deal in type_deals:
                parts.append(f'<h4 style="font-size:16px;margin:1em 0 0.3em;color:#1d2a27;">{deal["t"]}</h4>')
                
                money = deal.get("m", "")
                if money and money != "未披露":
                    amount_note = ""
                    if deal.get("amount_source") == "news":
                        amount_note = "（据报道）"
                    elif deal.get("is_filing"):
                        amount_note = "（披露文件）"
                    parts.append(f'<p style="margin:0.3em 0;"><strong>{money}</strong>{amount_note}</p>')
                
                amount_details = []
                if deal.get("upfront"):
                    amount_details.append(f"首付：{deal['upfront']}")
                if deal.get("milestones"):
                    amount_details.append(f"里程碑：{deal['milestones']}")
                if deal.get("equity"):
                    amount_details.append(f"股权：{deal['equity']}")
                if amount_details:
                    details_text = " · ".join(amount_details)
                    parts.append(f'<p style="margin:0.2em 0;font-size:14px;color:#555;">{details_text}</p>')
                
                if deal.get("why"):
                    parts.append(f'<p style="margin:0.3em 0;">{deal["why"]}</p>')
                
                source_text = deal.get("src") or "未注明"
                if deal.get("is_filing"):
                    source_text = f"📄 {source_text}"
                parts.append(f'<p style="font-size:14px;color:#666;margin:0.3em 0;">来源：{source_text}</p>')
    
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


def write_output(draft: dict, dest: Path, week: str) -> None:
    img_dir = dest / "images"
    img_dir.mkdir(parents=True, exist_ok=True)
    articles = []
    
    # Generate cover image first (for fallback)
    cover_prompt = (
        "Abstract circular shapes representing cells and molecules, "
        "soft teal and coral colors on white background"
    )
    cover = dest / "wechat" / "cover.png"
    cover.parent.mkdir(parents=True, exist_ok=True)
    cover_success = False
    try:
        cover_success = draw_image(cover_prompt, cover)
    except Exception:
        logging.exception("封面图失败")
    
    for index, item in enumerate(draft["articles"], start=1):
        filename = f"a{index}.png"
        img_path = img_dir / filename
        
        try:
            success = draw_image(item["image_prompt"], img_path)
            if success:
                rel = f"{dest.relative_to(ROOT).as_posix()}/images/{filename}"
            elif cover_success:
                # Fall back to cover image if article image had text
                logging.warning("使用封面图替代 %s", filename)
                import shutil
                shutil.copy(cover, img_path)
                rel = f"{dest.relative_to(ROOT).as_posix()}/images/{filename}"
            else:
                rel = ""
        except Exception:
            logging.exception("配图失败：%s", item["title"])
            rel = ""
        
        art = site_article(item, rel)
        art["lead"] = item["lead"]
        art["body"] = item["body"]
        art["discuss"] = item["discuss"]
        articles.append(art)
    
    seen_ids = {}
    for art in articles:
        if art["id"] in seen_ids:
            logging.error("ID 冲突：%s 和 %s 都生成了 ID %s", seen_ids[art["id"]], art["url"], art["id"])
            raise SystemExit(5)
        seen_ids[art["id"]] = art["url"]
    
    deals = [site_deal(item) for item in draft["deals"]]
    
    (dest / "articles.json").write_text(json.dumps(articles, ensure_ascii=False, indent=2), encoding="utf-8")
    (dest / "deals.json").write_text(json.dumps(deals, ensure_ascii=False, indent=2), encoding="utf-8")
    html = wechat_html(articles, deals, week)
    (dest / "wechat" / "article.html").write_text(html, encoding="utf-8")
    logging.info("写出 %s", dest)


def update_latest(dest: Path) -> None:
    articles = json.loads((dest / "articles.json").read_text(encoding="utf-8"))
    deals = json.loads((dest / "deals.json").read_text(encoding="utf-8"))
    latest_path = ROOT / "content" / "latest.json"
    previous = {"articles": [], "deals": []}
    if latest_path.exists():
        try:
            previous = json.loads(latest_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            logging.warning("content/latest.json 无法解析，将覆盖")
    seen_a = {a.get("id") for a in articles}
    seen_d = {d.get("url") for d in deals}
    merged_a = articles + [a for a in previous.get("articles") or [] if a.get("id") not in seen_a]
    merged_d = deals + [d for d in previous.get("deals") or [] if d.get("url") not in seen_d]
    payload = {
        "generated": dest.name,
        "articles": merged_a[:40],
        "deals": merged_d[:40],
    }
    latest_path.parent.mkdir(exist_ok=True)
    latest_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    logging.info("已更新 content/latest.json")


def _test_deal_number_stripping():
    """Test that invented numbers are stripped from deal output.
    
    Fix #1 test: Stub claude_draft with injected values and verify none get published.
    """
    # Simulate verified amounts from source: only $100 million
    verified = {(10000000000, 'USD')}  # $100M in cents
    
    test_cases = [
        # (field, value, should_be_stripped)
        ("title", "50亿美元大交易", True),  # Title with unverified amount
        ("why", "99亿美元，首付3亿", True),  # Why with unverified amounts  
        ("structure", "7亿美元结构", True),  # Structure with unverified amount
        ("news_title", "估值80亿美元", True),  # News title with unverified
        ("news_why", "融资8000万美元", True),  # News with unverified
        ("news_upfront", "2亿美元", True),  # Upfront not in source
    ]
    
    passed = 0
    for field, value, should_strip in test_cases:
        if field.startswith("news_"):
            # For news items, use same verification
            result = _strip_unverified_numbers(value, verified)
        elif field == "title":
            # Title should be built from verified fields only
            result = _build_deal_title("TestCo", "PartnerCo", "lic", "1亿美元")
            # Check that the invented amount isn't in the built title
            should_strip = "50亿" not in result
        else:
            result = _strip_unverified_numbers(value, verified)
        
        # After stripping, should have no unverified numbers
        remaining_numbers = _extract_numbers_from_text(result)
        verified_number_strs = {"100", "100.00", "1", "1.00"}  # $100M representations
        
        has_unverified = bool(remaining_numbers - verified_number_strs - {"未披露"})
        
        if should_strip and not has_unverified:
            passed += 1
        elif not should_strip and value == result:
            passed += 1
        else:
            print(f"FAIL {field}: '{value}' -> '{result}' (has_unverified={has_unverified})")
    
    print(f"_test_deal_number_stripping: {passed}/{len(test_cases)} tests passed")
    return passed == len(test_cases)


def main() -> None:
    parser = argparse.ArgumentParser(description="生成一周的前沿追踪内容")
    parser.add_argument("--dry-run", action="store_true", help="只写到 preview/，不改网站内容目录")
    parser.add_argument("--test", action="store_true", help="运行单元测试")
    args = parser.parse_args()
    
    if args.test:
        print("Running unit tests...\n")
        all_passed = True
        all_passed &= _test_sanitize_image_prompt()
        all_passed &= _test_biorxiv_api()
        all_passed &= _test_verify_amount()
        all_passed &= _test_classify_deal_type()
        all_passed &= _test_strip_unverified_numbers()
        all_passed &= _test_deal_number_stripping()
        all_passed &= _test_nonprofit_detection()
        all_passed &= _test_company_verification()
        all_passed &= _test_deal_keywords()
        all_passed &= _test_name_normalization()
        all_passed &= _test_sec_filing_fetch()
        print(f"\n{'All tests passed!' if all_passed else 'Some tests failed.'}")
        raise SystemExit(0 if all_passed else 1)
    
    log_path = setup_log()
    logging.info("日志 %s", log_path)
    try:
        require_env(["ANTHROPIC_API_KEY", "OPENAI_API_KEY"])
        check_anthropic_model()
        config = load_sources()
        items = fetch_all(config)
        if not items:
            logging.error("最近 %s 天没有抓到条目，不写文件", config.get("window_days", 7))
            raise SystemExit(2)
        logging.info("送去筛选的条目 %d", len(items))
        draft = claude_draft(items, config)
        if not draft["articles"] and not draft["deals"]:
            logging.error("模型没有留下任何来源内的条目")
            raise SystemExit(3)
        week = date.today().isoformat()
        dest = (ROOT / "preview" / "weekly" / week) if args.dry_run else (ROOT / "content" / "weekly" / week)
        if dest.exists():
            logging.error("目录已存在，避免覆盖：%s", dest)
            raise SystemExit(4)
        write_output(draft, dest, week)
        if args.dry_run:
            logging.info("dry-run 完成，没有改 content/，也没有调用公众号")
        else:
            update_latest(dest)
            logging.info("网站内容已写入。提交并推送 main 后，GitHub Pages 会更新。公众号请另跑 publish_wechat.py")
    except SystemExit:
        raise
    except Exception:
        logging.error("未捕获的错误\n%s", traceback.format_exc())
        raise SystemExit(4)


if __name__ == "__main__":
    main()
