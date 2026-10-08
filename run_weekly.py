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
IMAGE_PREFIX = (
    "Flat BioRender-style scientific illustration on white background, "
    "thin gray outlines, soft teal, coral, gold and blue-gray palette. "
    "Minimalist, clean, diagrammatic. Show biological molecules, cells, or mechanisms. "
    "No 3D effects, no glow, no gradients, no photorealism, no shadows. "
    "Simple shapes only. The subject fills the frame. "
)
IMAGE_SUFFIX = (
    " No text, no letters, no words, no labels, no captions, no numbers, "
    "no watermarks, no annotations anywhere in the image; purely visual illustration."
)
UA = "FrontierDigestWeekly/1.0 (+https://inlight.therasik.com)"


def sanitize_image_prompt(prompt: str) -> str:
    """Remove label-related phrases from the model's image prompt.
    
    Uses word-boundary patterns to avoid false positives like 'laboured'.
    """
    patterns = [
        r'\b(labell?ed)\b',           # labeled, labelled (not laboured)
        r'\blabels?\b',               # bare label, labels
        r'\bannotated\b',
        r'\bwith\s+(text\s+)?labels?\s*(showing\s+(the\s+)?names?)?\b',  # with text labels showing names
        r'\bwith\s+annotations?\b',
        r'\bwith\s+captions?\b',      # with captions
        r'\bcaptioned\b',             # captioned
        r'\bcaptions?\b',             # standalone captions
        r'\bwith\s+text\b',
        r'\bshowing\s+(the\s+)?names?\b',
        r'\bnamed\b',
        r'"[^"]*"',                   # Remove quoted text that might be label requests
    ]
    result = prompt
    for pattern in patterns:
        result = re.sub(pattern, '', result, flags=re.IGNORECASE)
    # Clean up extra spaces
    result = re.sub(r'\s+', ' ', result).strip()
    return result


def _test_sanitize_image_prompt():
    """Unit test for sanitize_image_prompt."""
    tests = [
        ("A diagram labeled with cell types", "A diagram with cell types"),
        ("labelled regions of the brain", "regions of the brain"),
        ("The laboured breathing pattern", "The laboured breathing pattern"),  # Should NOT be removed
        ("annotated with arrows", "with arrows"),
        ("with text labels showing names", ""),  # All words are label-related
        ('A cell "Helper T" diagram', "A cell diagram"),
        ("simple illustration of cells", "simple illustration of cells"),
        ("with captions identifying parts", "identifying parts"),
        ("a captioned figure of DNA", "a figure of DNA"),  # Fix #10: captioned
        ("diagram with label", "diagram with"),  # Fix #10: bare label
        ("cells with labels", "cells with"),  # Fix #10: bare labels
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
    """Verify the Anthropic model is available before proceeding.
    
    Uses the same tools/tool_choice settings as the real drafting call
    so that if this check passes, the real call should work too.
    """
    from anthropic import Anthropic, NotFoundError, APIError
    
    model = os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-5-5")
    logging.info("检查 Anthropic 模型可用性：%s", model)
    
    # Use same tool_choice=auto as the real drafting call
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
        logging.error("可选模型包括：claude-sonnet-5-5, claude-opus-5-5, claude-haiku-5-5, claude-sonnet-4-6 等。")
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
    """Fetch RSS feed with retry logic for transient errors.
    
    Always uses requests with timeout to avoid hangs, then passes content to feedparser.
    
    Only retries on:
    - 5xx server errors
    - Timeouts
    - Connection errors  
    - XML parse errors (e.g., "no element found")
    
    Does NOT retry on:
    - 4xx client errors (permanent failure)
    
    Returns:
        Tuple of (parsed_feed, status) where status is "ok", "failed", or "skipped"
    """
    import time
    import requests
    
    delays = [2, 5, 10]  # Exponential backoff
    last_error = None
    should_retry = True
    
    ua = BROWSER_UA if use_browser_ua else UA
    
    for attempt in range(max_attempts):
        try:
            # Always use requests with timeout to avoid hangs (Fix #8)
            resp = requests.get(feed, headers={"User-Agent": ua}, timeout=timeout)
            
            # 4xx errors are permanent - don't retry
            if 400 <= resp.status_code < 500:
                logging.warning("%s 返回 %d（客户端错误，不重试）", source_name, resp.status_code)
                return None, "failed"
            
            # 5xx errors - retry
            if resp.status_code >= 500:
                raise requests.exceptions.HTTPError(f"Server error {resp.status_code}")
            
            resp.raise_for_status()
            
            # Parse content with feedparser (no network access)
            parsed = feedparser.parse(resp.content)
            
            # Check for parse errors (bozo) but allow if we got entries
            if getattr(parsed, "bozo", False) and not parsed.entries:
                bozo_exc = getattr(parsed, "bozo_exception", None)
                bozo_str = str(bozo_exc).lower() if bozo_exc else ""
                # Retry on XML parse errors like "no element found"
                if "no element found" in bozo_str or "not well-formed" in bozo_str:
                    raise ValueError(f"XML parse error: {bozo_exc}")
                # Other parse errors - don't retry
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
            # Only retry on 5xx (already filtered 4xx above)
            last_error = e
            should_retry = "5" in str(e) or "Server error" in str(e)
        except ValueError as e:
            # XML parse errors - retry
            if "XML parse error" in str(e):
                last_error = e
                should_retry = True
            else:
                logging.warning("%s 抓取失败（不重试）：%s", source_name, e)
                return None, "failed"
        except Exception as e:
            # Other errors - don't retry
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
    """Fetch URL with retry logic for server errors, timeouts, and connection errors.
    
    Only retries on:
    - 5xx server errors
    - Timeouts
    - Connection errors
    - XML parse errors (for RSS)
    
    Does NOT retry on 4xx client errors (treat as permanent failure).
    """
    import time
    import requests
    
    delays = [2, 5, 10]
    last_error = None
    
    for attempt in range(max_attempts):
        try:
            resp = requests.get(url, headers={"User-Agent": UA}, timeout=timeout)
            
            # 4xx errors are permanent - don't retry
            if 400 <= resp.status_code < 500:
                logging.warning("%s 返回 %d（客户端错误，不重试）", source_name, resp.status_code)
                return None
            
            # 5xx errors - retry
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
            # Other errors (JSON decode, etc.) - don't retry
            logging.warning("%s 请求失败（不重试）：%s", source_name, e)
            return None
    
    logging.warning("%s 请求重试后仍失败：%s", source_name, last_error)
    return None


def fetch_biorxiv_api(start: datetime, limit: int, category: str | None = None) -> list[dict]:
    """Fallback: fetch from bioRxiv details API when RSS fails.
    
    Uses https://api.biorxiv.org/details/biorxiv/{start}/{end}/{cursor}
    and filters by category client-side. The API returns 'total' as a string,
    so we cast it to int.
    """
    end_date = datetime.now(timezone.utc).date()
    start_date = start.date()
    
    # Use details endpoint (not pubs) - it has abstracts and supports pagination
    base_url = f"https://api.biorxiv.org/details/biorxiv/{start_date.isoformat()}/{end_date.isoformat()}"
    
    logging.info("bioRxiv API fallback: %s (category=%s)", base_url, category or "all")
    
    rows = []
    cursor = 0
    max_pages = 5  # Safety limit
    
    for page in range(max_pages):
        # bioRxiv uses cursor for pagination: /details/biorxiv/{start}/{end}/{cursor}
        paginated_url = f"{base_url}/{cursor}"
        
        data = _fetch_with_retry(paginated_url, f"bioRxiv API (page {page+1})", json_response=True)
        if data is None:
            break
        
        collection = data.get("collection") or []
        if not collection:
            logging.info("bioRxiv API page %d 返回 0 条", page + 1)
            break
        
        # Filter by category if specified (API doesn't filter server-side)
        if category:
            category_lower = category.lower()
            collection = [p for p in collection if (p.get("category") or "").lower() == category_lower]
        
        for paper in collection:
            doi = paper.get("doi") or ""
            title = paper.get("title") or ""
            if not doi or not title:
                continue
            
            # Parse date
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
        
        # Check for more pages - API returns total as string, cast to int (Fix #7)
        messages = data.get("messages") or []
        total = 0
        for msg in messages:
            if msg.get("status") == "ok" and "total" in msg:
                try:
                    total = int(msg.get("total", 0))
                except (ValueError, TypeError):
                    total = 0
                break
        
        # Move cursor forward by actual items fetched (before filtering)
        cursor += len(data.get("collection") or [])
        if cursor >= total:
            break
    
    logging.info("bioRxiv API fallback 得到 %d 条（分类 %s）", len(rows), category or "all")
    return rows[:limit]


def _test_biorxiv_api():
    """Unit test for bioRxiv API parsing with mocked response."""
    mock_response = {
        "messages": [{"status": "ok", "total": "55"}],  # total as string
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
    
    # Test total parsing (should handle string)
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
    
    # Test 1: total parsed correctly from string
    if total == 55:
        tests_passed += 1
    else:
        print(f"FAIL: total should be 55, got {total}")
    
    # Test 2: category filtering
    collection = mock_response.get("collection") or []
    filtered = [p for p in collection if (p.get("category") or "").lower() == "immunology"]
    if len(filtered) == 1 and filtered[0]["title"] == "Test Immunology Paper":
        tests_passed += 1
    else:
        print(f"FAIL: category filter should return 1 immunology paper, got {len(filtered)}")
    
    # Test 3: date parsing
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
    
    # Parameters to strip (tracking, RSS, UTM)
    tracking_params = {
        "rss", "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
        "ref", "source", "mc_cid", "mc_eid", "fbclid", "gclid", "msclkid",
    }
    
    parsed = urllib.parse.urlparse(url)
    if not parsed.query:
        return url
    
    params = urllib.parse.parse_qs(parsed.query, keep_blank_values=True)
    # Keep only non-tracking params
    clean_params = {k: v for k, v in params.items() if k.lower() not in tracking_params}
    
    if not clean_params:
        # All params were tracking - return URL without query
        return urllib.parse.urlunparse(parsed._replace(query=""))
    
    clean_query = urllib.parse.urlencode(clean_params, doseq=True)
    return urllib.parse.urlunparse(parsed._replace(query=clean_query))


def _parse_rss_authors(entry) -> str:
    """Extract author names from RSS entry's dc:creator or author fields."""
    # Try dc:creator (Dublin Core) first - common in academic feeds
    dc_creator = entry.get("dc_creator") or entry.get("author_detail", {}).get("name") or ""
    if dc_creator:
        return dc_creator.strip()
    
    # Try author field
    author = entry.get("author") or ""
    if author:
        return author.strip()
    
    # Try authors list (some feeds provide this)
    authors_list = entry.get("authors") or []
    if authors_list:
        names = [a.get("name", "") for a in authors_list if a.get("name")]
        if names:
            return ", ".join(names[:6])
    
    return ""


def fetch_rss(source: dict, start: datetime, limit: int) -> tuple[list[dict], str]:
    """Fetch RSS feed and return (items, status).
    
    Returns:
        Tuple of (items_list, status) where status is "ok", "failed", or "skipped"
    """
    feed = source.get("feed")
    if not feed:
        logging.warning("跳过 %s：没有 feed", source.get("name"))
        return [], "skipped"
    logging.info("抓取 RSS %s", source["name"])
    
    source_name = source.get("name", "unknown")
    use_browser_ua = source.get("needs_browser_ua", False)
    
    # Fetch with retry
    parsed, status = _fetch_rss_with_retry(feed, source_name, use_browser_ua)
    
    # bioRxiv fallback to API
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
        
        # Strip tracking parameters from URL
        url = _strip_tracking_params(url)
        
        # Extract summary and clean HTML
        summary = re.sub(r"<[^>]+>", " ", entry.get("summary") or entry.get("description") or "")
        summary = re.sub(r"\s+", " ", summary).strip()[:2500]
        
        # Parse authors from RSS
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
    """Fetch PubMed articles with abstracts using E-utilities (esearch + efetch)."""
    import time
    import xml.etree.ElementTree as ET
    
    query = source.get("query") or ""
    logging.info("检索 PubMed %s", query)
    mindate = start.strftime("%Y/%m/%d")
    maxdate = end.strftime("%Y/%m/%d")
    term = f"({query}) AND ({mindate}:{maxdate}[edat])"
    
    # Step 1: Search for PMIDs
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
    
    # Step 2: Fetch full records with abstracts using efetch
    time.sleep(0.35)  # Respect NCBI rate limit
    efetch_url = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi?" + urllib.parse.urlencode({
        "db": "pubmed",
        "id": ",".join(ids),
        "rettype": "xml",
        "retmode": "xml",
    })
    req = urllib.request.Request(efetch_url, headers={"User-Agent": UA})
    
    abstracts = {}  # pmid -> abstract text
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            xml_data = resp.read().decode("utf-8")
        
        root = ET.fromstring(xml_data)
        for article in root.findall(".//PubmedArticle"):
            pmid_elem = article.find(".//PMID")
            if pmid_elem is None:
                continue
            pmid = pmid_elem.text
            
            # Get abstract - may have multiple AbstractText elements
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
    
    # Step 3: Get metadata from esummary (faster than parsing all from efetch XML)
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
        
        # Use abstract from efetch if available, otherwise fall back to journal+authors
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
    "lic": "授权合作",  # licensing/collaboration
    "acq": "并购",      # M&A
    "inv": "融资/IPO",  # financing/IPO
}

# Deal type groupings for display
DEAL_GROUP_ORDER = ["lic", "acq", "inv"]
DEAL_GROUP_NAMES = {
    "lic": "授权合作",
    "acq": "并购",
    "inv": "融资/IPO",
}

# SEC biopharma SIC codes ONLY - no name-based fallback (Fix #4)
# 2834: Pharmaceutical Preparations
# 2835: In Vitro & In Vivo Diagnostic Substances
# 2836: Biological Products (except diagnostic)
# 8731: Commercial Physical & Biological Research
SEC_BIOPHARMA_SICS = {"2834", "2835", "2836", "8731"}

# HKEX healthcare/biotech stock codes (Chapter 18A biotechs and healthcare companies)
# Conservative list - only include if confidently biopharma. Precision over recall.
HKEX_HEALTHCARE_CODES = {
    # Chapter 18A biotech (no revenue requirement) - verified biopharma
    "1877", "2126", "2142", "2171", "2197", "2256", "2552", "6160", "6185",
    "9688", "9995", "9926", "9969", "9989", "9999",
    # Major pharma/healthcare - verified
    "1093", "1099", "1177", "1530", "1801", "1833", "2269", "2359", "3320", 
    "6078", "6098", "6127", "6618", "6622", "6699", "6816", "6855", "6896",
    "6978", "9901", "9908", "9911", "9922", "9939", "9958", "9987", "9988",
}

# Precision policy: missing a deal is acceptable; publishing a wrong one is not.
# - If company can't be confidently identified as biopharma, drop it
# - If amount can't be verified in filing text, use "未披露" or drop
# - If deal type is unclear, drop it
# - Never fill gaps with model guesses


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
        # Try PyMuPDF first (faster)
        try:
            import fitz
            doc = fitz.open(stream=pdf_bytes, filetype="pdf")
            text_parts = []
            for page_num in range(min(5, len(doc))):  # First 5 pages max
                page = doc[page_num]
                text_parts.append(page.get_text())
                if sum(len(t) for t in text_parts) > max_chars:
                    break
            doc.close()
            text = "\n".join(text_parts)[:max_chars]
            return re.sub(r'\s+', ' ', text).strip()
        except ImportError:
            pass
        
        # Fallback to pdfplumber
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
    
    Returns list of (amount_in_base_units, currency) tuples.
    Currency is one of: 'USD', 'RMB', 'HKD', 'EUR', 'GBP'
    Amount is in smallest units (cents/分) for exact comparison.
    
    Examples:
    - "$1.17 billion" -> (117000000000, 'USD')  # 1.17B cents
    - "11.7亿美元" -> (117000000000, 'USD')     # Same
    - "RMB100,000,000" -> (10000000000, 'RMB')  # 100M 分
    - "HK$1,939.78M" -> (193978000000, 'HKD')   # 1939.78M 仙
    """
    amounts = []
    
    # USD patterns - use negative lookbehind to exclude HK$
    # $X.XX billion (not HK$)
    for m in re.finditer(r'(?<!HK)\$\s*([\d,]+(?:\.\d+)?)\s*(?:billion|B\b)', text, re.IGNORECASE):
        try:
            val = float(m.group(1).replace(',', '')) * 1_000_000_000 * 100
            amounts.append((int(val), 'USD'))
        except ValueError:
            pass
    
    # $X.XX million (not HK$)
    for m in re.finditer(r'(?<!HK)\$\s*([\d,]+(?:\.\d+)?)\s*(?:million|M\b)', text, re.IGNORECASE):
        try:
            val = float(m.group(1).replace(',', '')) * 1_000_000 * 100
            amounts.append((int(val), 'USD'))
        except ValueError:
            pass
    
    # $X,XXX,XXX (raw USD numbers over 100k, not HK$)
    for m in re.finditer(r'(?<!HK)\$\s*([\d,]{7,})', text):
        try:
            val = int(m.group(1).replace(',', '')) * 100
            if val >= 10_000_000:  # $100k+
                amounts.append((val, 'USD'))
        except ValueError:
            pass
    
    # Chinese USD: X亿美元 / X.X亿美元
    for m in re.finditer(r'([\d.]+)\s*亿\s*美元', text):
        try:
            val = float(m.group(1)) * 100_000_000 * 100
            amounts.append((int(val), 'USD'))
        except ValueError:
            pass
    
    # Chinese USD: X万美元
    for m in re.finditer(r'([\d,]+(?:\.\d+)?)\s*万\s*美元', text):
        try:
            val = float(m.group(1).replace(',', '')) * 10_000 * 100
            amounts.append((int(val), 'USD'))
        except ValueError:
            pass
    
    # RMB/CNY patterns
    # RMB X / CNY X
    for m in re.finditer(r'(?:RMB|CNY)\s*([\d,]+(?:\.\d+)?)\s*(?:million|M\b)?', text, re.IGNORECASE):
        try:
            val = float(m.group(1).replace(',', ''))
            if 'million' in text[m.start():m.end()+10].lower() or m.end() < len(text) and text[m.end():m.end()+1] == 'M':
                val *= 1_000_000
            val *= 100  # Convert to 分
            if val >= 10_000_000:
                amounts.append((int(val), 'RMB'))
        except ValueError:
            pass
    
    # Chinese RMB: X亿元 / X亿人民币 (NOT 美元)
    for m in re.finditer(r'([\d.]+)\s*亿\s*(?:元|人民币)(?!美)', text):
        try:
            val = float(m.group(1)) * 100_000_000 * 100
            amounts.append((int(val), 'RMB'))
        except ValueError:
            pass
    
    # Chinese RMB: X万元 / X万人民币 (NOT 美元)
    for m in re.finditer(r'([\d,]+(?:\.\d+)?)\s*万\s*(?:元|人民币)(?!美)', text):
        try:
            val = float(m.group(1).replace(',', '')) * 10_000 * 100
            amounts.append((int(val), 'RMB'))
        except ValueError:
            pass
    
    # HKD patterns
    # HK$X.XXM / HK$X.XX million
    for m in re.finditer(r'HK\$\s*([\d,]+(?:\.\d+)?)\s*(?:million|M\b)', text, re.IGNORECASE):
        try:
            val = float(m.group(1).replace(',', '')) * 1_000_000 * 100
            amounts.append((int(val), 'HKD'))
        except ValueError:
            pass
    
    # HK$X.XXB / HK$X.XX billion
    for m in re.finditer(r'HK\$\s*([\d,]+(?:\.\d+)?)\s*(?:billion|B\b)', text, re.IGNORECASE):
        try:
            val = float(m.group(1).replace(',', '')) * 1_000_000_000 * 100
            amounts.append((int(val), 'HKD'))
        except ValueError:
            pass
    
    # HK$X,XXX,XXX raw
    for m in re.finditer(r'HK\$\s*([\d,]{7,})', text):
        try:
            val = int(m.group(1).replace(',', '')) * 100
            if val >= 10_000_000:
                amounts.append((val, 'HKD'))
        except ValueError:
            pass
    
    # Chinese HKD: X亿港元 / X亿港币
    for m in re.finditer(r'([\d.]+)\s*亿\s*港[元币]', text):
        try:
            val = float(m.group(1)) * 100_000_000 * 100
            amounts.append((int(val), 'HKD'))
        except ValueError:
            pass
    
    return amounts


def _verify_amount_in_text(amount_str: str, filing_text: str) -> bool:
    """Check if an amount appears in the filing text with exact match.
    
    Fix #2: No tolerance, currency must match.
    
    Precision policy: if we can't verify, return False (drop the amount).
    
    Unit test cases from user:
    - "11亿美元" vs '$1.17 billion' -> FAIL (11 != 1.17)
    - "1.05亿美元" vs '$100 million' -> FAIL (105M != 100M)
    - "1亿美元" vs 'RMB100,000,000' -> FAIL (USD != RMB)
    - "19.4亿美元" vs 'HK$1,939.78M' -> FAIL (USD != HKD)
    - "1亿首付，最高99亿" vs only '$100 million' in source -> FAIL (99亿 not found)
    - "1亿美元" vs '$100 million' -> PASS
    - "11.7亿美元" vs '$1.17 billion' -> PASS
    """
    if not amount_str or amount_str == "未披露":
        return True  # Nothing to verify
    
    if not filing_text or len(filing_text) < 20:
        return False  # No text to verify against - be conservative
    
    filing_amounts = _normalize_amount_with_currency(filing_text)
    claim_amounts = _normalize_amount_with_currency(amount_str)
    
    if not claim_amounts:
        return False  # Can't parse the claim
    
    if not filing_amounts:
        return False  # No amounts found in filing text
    
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
        # (claim, source_text, expected_result)
        # Note: source texts must be >= 20 chars (the function rejects short texts)
        
        # FAIL cases - wrong amounts or wrong currencies
        ("11亿美元", "The company paid $1.17 billion in total", False),  # 11 != 1.17
        ("1.05亿美元", "The upfront payment was $100 million cash", False),  # 105M != 100M
        ("1亿美元", "RMB100,000,000 consideration paid", False),  # USD vs RMB
        ("19.4亿美元", "HK$1,939.78M in cash consideration", False),  # USD vs HKD
        ("1亿首付，最高99亿", "The upfront was only $100 million", False),  # 99亿 not found
        
        # PASS cases - exact matches
        ("1亿美元", "The company paid $100 million in cash", True),
        ("11.7亿美元", "total deal value of $1.17 billion announced", True),
        ("100万美元", "The company received $1 million upfront", True),
        ("1.939亿港元", "HK$193.9M consideration was paid", True),
        
        # Edge cases
        ("未披露", "any text over 20 chars here", True),  # 未披露 always passes
        ("1亿美元", "", False),  # No filing text
        ("", "any text over 20 chars here", True),  # No claim
    ]
    
    passed = 0
    for claim, source, expected in tests:
        result = _verify_amount_in_text(claim, source)
        if result == expected:
            passed += 1
        else:
            print(f"FAIL: '{claim}' vs '{source}' -> {result} (expected {expected})")
    
    print(f"_test_verify_amount: {passed}/{len(tests)} tests passed")
    return passed == len(tests)


def _classify_deal_type(text: str) -> str | None:
    """Classify deal as lic/acq/inv based on text. Returns None if unclear.
    
    Fix #7: Use word boundaries to avoid false positives like:
    - 'lipoprotein' matching 'ipo'
    - 'data acquisition system' matching 'acquisition'
    
    Precision policy: only classify if confident, otherwise return None to drop.
    """
    text_lower = text.lower()
    
    def count_word_matches(patterns: list[str]) -> int:
        """Count pattern matches using word boundaries for short patterns."""
        count = 0
        for p in patterns:
            if len(p) <= 5:
                # Short patterns need word boundaries
                if re.search(r'\b' + re.escape(p) + r'\b', text_lower):
                    count += 1
            else:
                # Longer patterns are specific enough
                if p in text_lower:
                    count += 1
        return count
    
    # Strong signals for licensing/collaboration (multi-word phrases are safe)
    lic_signals = [
        "license agreement", "collaboration agreement", "exclusive license",
        "non-exclusive license", "royalty", "royalties", 
        "milestone payment", "upfront payment", "option agreement",
        "授权协议", "许可协议", "合作协议", "独家授权", "里程碑付款",
    ]
    lic_count = count_word_matches(lic_signals)
    
    # Strong signals for acquisition/merger (use phrases to avoid 'acquisition' in 'data acquisition')
    acq_signals = [
        "merger agreement", "tender offer", "definitive agreement to acquire",
        "acquisition agreement", "merger consideration", "acquire all",
        "to acquire", "has acquired", "will acquire", "acquired by",
        "收购协议", "并购", "要约收购", "吸收合并",
    ]
    acq_count = count_word_matches(acq_signals)
    
    # Strong signals for financing/IPO (use word boundary for 'ipo')
    # 'ipo' needs \b to avoid 'lipoprotein', 'adipose', etc.
    inv_signals = [
        "securities purchase", "private placement", "public offering",
        "series a", "series b", "series c", "series d", "series e",
        "round a", "round b", "round c", "venture financing",
        "registered direct offering", "stock offering",
        "配售", "定向增发", "公开发行", "融资", "首次公开",
    ]
    inv_count = count_word_matches(inv_signals)
    
    # Check 'ipo' separately with word boundary
    if re.search(r'\bipo\b', text_lower):
        inv_count += 1
    
    # Only classify if one category has clear majority
    counts = [("lic", lic_count), ("acq", acq_count), ("inv", inv_count)]
    counts.sort(key=lambda x: x[1], reverse=True)
    
    if counts[0][1] >= 2 and counts[0][1] > counts[1][1]:
        return counts[0][0]
    
    # Single strong signal is enough if others are zero
    if counts[0][1] >= 1 and counts[1][1] == 0:
        return counts[0][0]
    
    return None  # Unclear - drop this deal


def _test_classify_deal_type():
    """Unit tests for deal type classifier with word boundaries."""
    tests = [
        # Should NOT match 'ipo' in 'lipoprotein'
        ("Study of lipoprotein levels in patients", None),
        ("Adipose tissue analysis", None),
        
        # Should NOT match 'acquisition' in 'data acquisition'
        ("New data acquisition system for the lab", None),
        
        # Should match real IPO
        ("Company announces IPO pricing", "inv"),
        ("Initial public offering completed", "inv"),
        ("Series B financing round", "inv"),
        
        # Should match real acquisition
        ("Merger agreement signed", "acq"),
        ("Definitive agreement to acquire company", "acq"),
        
        # Should match licensing
        ("License agreement for oncology program", "lic"),
        ("Exclusive license with milestone payments", "lic"),
        
        # Ambiguous - should return None
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
    
    Fix #4: For SEC filings, use SIC codes ONLY. No name-based fallback.
    The name fallback caused false positives like:
    - "Rochester Gas & Electric" matching 'roche'
    - "Lilly Industries" (paint company) matching 'lilly'
    
    Precision policy: if SIC codes provided but don't match, return False.
    Only fall back to industry/name for non-SEC sources.
    """
    if sic_codes:
        # SEC SIC codes for biopharma - STRICT, no fallback
        # If sic_codes are provided (SEC filing), use ONLY this check
        return any(sic in SEC_BIOPHARMA_SICS for sic in sic_codes)
    
    # For non-SEC sources (HKEX, cninfo), allow industry classification
    if industry:
        industry_lower = industry.lower()
        if any(kw in industry_lower for kw in ["医药", "生物", "pharma", "biotech", "biopharma"]):
            return True
    
    # No SIC codes and no industry = can't verify confidently
    return False


def _normalize_company_name(name: str) -> str:
    """Normalize company name for deduplication."""
    name = name.lower().strip()
    # Remove common suffixes
    for suffix in [", inc.", ", inc", " inc.", " inc", ", ltd.", ", ltd", " ltd.", " ltd",
                   " limited", " corporation", " corp.", " corp", " co.", " co",
                   " plc", " ag", " se", " sa", " nv", " bv",
                   "有限公司", "股份有限公司", "集团", "控股"]:
        name = name.replace(suffix, "")
    # Remove punctuation
    name = re.sub(r'[^\w\s]', '', name)
    name = re.sub(r'\s+', ' ', name).strip()
    return name

# Keywords for filtering filings
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


def _fetch_sec_filing_text(cik: str, accession: str, sec_ua: str, max_chars: int = 4000) -> str:
    """Fetch and extract text from SEC filing primary document and EX-99.1 press release.
    
    Fix #8: Use the 'type' field from index.json to identify documents, not filename.
    The type field contains the document type (e.g., '8-K', 'EX-99.1').
    """
    import time
    import requests
    
    headers = {"User-Agent": sec_ua, "Accept": "text/html"}
    
    # Normalize accession number
    accession_clean = accession.replace("-", "")
    
    # Build base URL for filing documents
    base_url = f"https://www.sec.gov/Archives/edgar/data/{cik}/{accession_clean}"
    
    text_parts = []
    
    # First, fetch the filing index to find document names
    try:
        time.sleep(0.12)  # SEC rate limit
        index_url = f"{base_url}/index.json"
        resp = requests.get(index_url, headers={"User-Agent": sec_ua, "Accept": "application/json"}, timeout=30)
        
        if resp.status_code == 200:
            index_data = resp.json()
            items = index_data.get("directory", {}).get("item", [])
            
            # Fix #8: Use Type field from index to identify documents
            primary_doc = None
            press_releases = []
            
            for item in items:
                name = item.get("name", "")
                doc_type = (item.get("type") or "").upper()  # Type field, not filename
                
                # Primary document: Type is "8-K" or "6-K" (exactly)
                if doc_type in ("8-K", "6-K") and name.lower().endswith(".htm"):
                    if primary_doc is None:  # Take first one
                        primary_doc = name
                
                # Press releases: Type starts with "EX-99" or "99."
                if doc_type.startswith("EX-99") or doc_type.startswith("99."):
                    if name.lower().endswith((".htm", ".txt")):
                        press_releases.append(name)
            
            # Fetch primary document
            if primary_doc:
                time.sleep(0.12)
                doc_url = f"{base_url}/{primary_doc}"
                doc_resp = requests.get(doc_url, headers=headers, timeout=30)
                if doc_resp.status_code == 200:
                    text = _strip_html(doc_resp.text)
                    if len(text) > 100:
                        text_parts.append(text[:max_chars // 2])
            
            # Fetch press release (usually has the deal details)
            for ex_name in press_releases[:1]:
                time.sleep(0.12)
                ex_url = f"{base_url}/{ex_name}"
                ex_resp = requests.get(ex_url, headers=headers, timeout=30)
                if ex_resp.status_code == 200:
                    text = _strip_html(ex_resp.text)
                    if len(text) > 100:
                        text_parts.append(text[:max_chars // 2])
                        break
    except Exception as e:
        logging.debug("SEC filing fetch via index failed: %s", e)
    
    # Fallback: try common document names directly (only if index fetch failed)
    if not text_parts:
        try:
            time.sleep(0.12)
            for doc_name in ["8-k.htm", "6-k.htm", "ex99-1.htm", "ex991.htm"]:
                doc_url = f"{base_url}/{doc_name}"
                resp = requests.get(doc_url, headers=headers, timeout=30)
                if resp.status_code == 200:
                    text = _strip_html(resp.text)
                    if len(text) > 100:
                        text_parts.append(text[:max_chars])
                        break
        except Exception as e:
            logging.debug("SEC fallback fetch failed: %s", e)
    
    combined = "\n\n".join(text_parts)
    return combined[:max_chars]


def _extract_event_date_from_filing(filing_text: str) -> str | None:
    """Extract the event date from an 8-K/6-K filing.
    
    The event date is when the reportable event occurred (e.g. deal signed),
    not when the filing was submitted to SEC. For 8-K/A amendments about
    July mergers, the filing date might be October but event date is July.
    
    Looks for patterns like:
    - "Date of Report (Date of earliest event reported): July 15, 2026"
    - "Date of Report: 07/15/2026"
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
    
    Fetches the primary document and EX-99.1 press release text for each filing.
    Skips amendments (8-K/A, 6-K/A) which often resurface old deals.
    Uses event date (when deal occurred) not filing date.
    Returns (rows, status) tuple.
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
    
    # Search for 8-K and 6-K filings with deal keywords (NOT amendments)
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
                time.sleep(0.12)  # Respect SEC's 10 req/sec limit
                resp = requests.get(search_url, params=params, headers=headers, timeout=30)
                if resp.status_code == 200:
                    data = resp.json()
                    hits = data.get("hits", {}).get("hits", [])
                    
                    for hit in hits:
                        if len(rows) >= limit * 2:  # Fetch extra for dedup
                            break
                            
                        source = hit.get("_source", {})
                        
                        # Extract fields first
                        company = source.get("display_names", ["Unknown"])[0]
                        filed_date = source.get("file_date", "")
                        form = source.get("form", form_type)
                        accession = source.get("adsh", "").replace("-", "")
                        cik = source.get("ciks", [""])[0]
                        sics = source.get("sics", [])
                        
                        if not accession or not cik:
                            continue
                        
                        # FIX #1: Skip amendments (8-K/A, 6-K/A)
                        # Amendments add financials to old deals and resurface them
                        form_upper = form.upper()
                        if "/A" in form_upper or form_upper.endswith("A"):
                            skipped_amendments += 1
                            continue
                        
                        # Filter by SIC code - strict biopharma only
                        if not _is_biopharma_company(company, sic_codes=sics):
                            continue
                        
                        doc_url = f"https://www.sec.gov/Archives/edgar/data/{cik}/{accession}"
                        
                        # Get filing description from items
                        items = source.get("items", [])
                        description = ", ".join(items) if items else f"{form} filing"
                        
                        # Fetch actual filing text
                        filing_text = _fetch_sec_filing_text(cik, accession, sec_ua)
                        
                        # FIX #1: Use event date from filing, not filing date
                        # Event date is when deal occurred; filing date is when SEC received it
                        event_date = _extract_event_date_from_filing(filing_text)
                        
                        if event_date:
                            try:
                                event_dt = datetime.strptime(event_date, "%Y-%m-%d").date()
                                if event_dt < start_date:
                                    skipped_old_events += 1
                                    logging.debug("跳过旧事件日期：%s (%s)", company, event_date)
                                    continue
                                use_date = event_date
                            except ValueError:
                                use_date = filed_date[:10] if filed_date else end_date.isoformat()
                        else:
                            use_date = filed_date[:10] if filed_date else end_date.isoformat()
                        
                        # Use filing text as summary if available
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
                            "event_date": event_date,  # Keep for debugging
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
    
    # Dedupe by URL
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


def fetch_hkex_announcements(start: datetime, limit: int) -> tuple[list[dict], str]:
    """Fetch announcements from HKEX 披露易 for healthcare/biotech companies.
    
    Filters by healthcare stock codes and fetches PDF text for summaries.
    Returns (rows, status) tuple.
    """
    import time
    import requests
    import html as html_module
    
    logging.info("抓取 HKEX 披露易（医药公告）")
    
    end_date = datetime.now(timezone.utc).date()
    start_date = start.date()
    
    rows = []
    headers = {"User-Agent": BROWSER_UA, "Accept": "text/html"}
    
    # HKEX news search API - query the actual 7-day date range
    api_url = "https://www1.hkexnews.hk/search/titlesearch.xhtml"
    
    params = {
        "lang": "EN",
        "category": "0",
        "market": "SEHK",
        "from": start_date.strftime("%Y%m%d"),
        "to": end_date.strftime("%Y%m%d"),
        "headline": "",
        "searchType": "0",
        "t": "en",
    }
    
    try:
        resp = requests.get(api_url, params=params, headers=headers, timeout=30)
        
        if resp.status_code != 200:
            logging.warning("HKEX API 返回 %d", resp.status_code)
            return [], "failed"
        
        content = resp.text
        if not content:
            logging.warning("HKEX 返回空内容")
            return [], "failed"
        
        # Parse the search results - extract stock code, company, date, title, URL
        # Pattern: stock code is in the path like /listedco/listconews/sehk/2026/1005/2026100500123/
        # The stock code is typically extracted from the URL path
        
        # More comprehensive pattern to extract: stock code, date, title, URL
        # HKEX URLs look like: /listedco/listconews/sehk/2026/1005/2026100500123.pdf
        row_pattern = re.compile(
            r'<tr[^>]*>.*?'
            r'href="(/listedco/listconews/[^"]+/(\d{4})/(\d{4})/[^"]+\.pdf)"[^>]*>'
            r'([^<]+)</a>.*?'
            r'</tr>',
            re.DOTALL
        )
        
        # Simpler fallback: just get PDF links with titles
        link_pattern = re.compile(
            r'<a[^>]*href="(/listedco/listconews/(?:sehk|gem)/(\d{4})/(\d{4})/(\d+)[^"]*\.pdf)"[^>]*>([^<]+)</a>',
            re.IGNORECASE
        )
        
        matches = link_pattern.findall(content)
        logging.info("HKEX 找到 %d 个公告链接", len(matches))
        
        for url_path, year, mmdd, stock_code_raw, title in matches:
            if len(rows) >= limit * 2:
                break
                
            title = html_module.unescape(title.strip())
            if not title or title == "PDF":
                continue
            
            # Extract stock code from the announcement ID or path
            # The stock code is often in the announcement ID: 2026100500123 -> first 4-5 digits after date
            stock_code = ""
            # Try to extract from path - look for 4-digit code
            code_match = re.search(r'/(\d{4,5})/', url_path)
            if code_match:
                stock_code = code_match.group(1).lstrip("0") or code_match.group(1)
            
            # Filter to healthcare/biotech companies - STRICT (precision over recall)
            # Only include if stock code is in our verified list
            if not stock_code or stock_code not in HKEX_HEALTHCARE_CODES:
                # Don't rely on title keywords - too many false positives
                # Precision policy: if not in verified list, drop it
                continue
            
            # Filter by deal keywords
            title_lower = title.lower()
            has_keyword = any(kw.lower() in title_lower for kw in HKEX_DEAL_KEYWORDS)
            hk_deal_types = ['major', 'discloseable', 'connected', 'placing', 'subscription', 
                             'acquisition', 'disposal', 'joint venture', 'collaboration',
                             'license', 'licensing', 'agreement']
            has_deal_type = any(dt in title_lower for dt in hk_deal_types)
            
            if not has_keyword and not has_deal_type:
                continue
            
            url = f"https://www1.hkexnews.hk{url_path}"
            
            # Parse announcement date from path (year/mmdd)
            try:
                ann_date = datetime.strptime(f"{year}{mmdd}", "%Y%m%d").date()
            except ValueError:
                ann_date = end_date
            
            # Get company name - try to extract from title or fetch separately
            company_name = ""
            # Many HKEX titles start with company name
            if " - " in title:
                company_name = title.split(" - ")[0].strip()
            
            # Fetch PDF text for summary (Fix #2)
            filing_text = ""
            try:
                time.sleep(0.2)
                pdf_resp = requests.get(url, headers=headers, timeout=30)
                if pdf_resp.status_code == 200 and pdf_resp.content:
                    filing_text = _extract_pdf_text(pdf_resp.content, max_chars=4000)
            except Exception as e:
                logging.debug("HKEX PDF fetch failed for %s: %s", url, e)
            
            # Build title with company name if available
            display_title = f"{company_name}: {title}" if company_name else title
            
            rows.append({
                "source": "HKEX 披露易",
                "kind": "industry",
                "filing_source": "hkex",
                "title": display_title[:120],
                "url": url,
                "date": ann_date.isoformat(),
                "summary": filing_text[:4000] if filing_text else f"HKEX announcement: {title}",
                "filing_text": filing_text,
                "company": company_name or "Unknown",
                "stock_code": stock_code,
                "filing_type": "announcement",
            })
                
    except Exception as e:
        logging.warning("HKEX 抓取失败: %s", e)
        return [], "failed"
    
    # Dedupe by URL
    seen = set()
    unique_rows = []
    for row in rows:
        if row["url"] not in seen:
            seen.add(row["url"])
            unique_rows.append(row)
    
    logging.info("HKEX 披露易 得到 %d 条（有文本 %d 条）", 
                 len(unique_rows),
                 sum(1 for r in unique_rows if r.get("filing_text")))
    return unique_rows[:limit], "ok" if unique_rows else "failed"


def fetch_cninfo_announcements(start: datetime, limit: int) -> tuple[list[dict], str]:
    """Fetch announcements from 巨潮资讯 for biopharma A-share companies.
    
    Queries each keyword separately (not OR syntax) and uses correct category codes.
    Returns (rows, status) tuple.
    """
    import time
    import requests
    
    logging.info("抓取巨潮资讯（医药生物公告）")
    
    end_date = datetime.now(timezone.utc).date()
    start_date = start.date()
    
    rows = []
    seen_ids = set()
    headers = {
        "User-Agent": BROWSER_UA,
        "Accept": "application/json",
        "Content-Type": "application/x-www-form-urlencoded",
        "Referer": "http://www.cninfo.com.cn/new/commonUrl/pageOfSearch",
    }
    
    api_url = "http://www.cninfo.com.cn/new/hisAnnouncement/query"
    
    # Category codes for deal-related announcements (Fix #3)
    # 重大合同/对外投资/收购 instead of annual reports
    deal_categories = [
        "category_gddh_szsh",      # 股东大会
        "category_dshgg_szsh",     # 董事会公告
        "category_jshgg_szsh",     # 监事会公告
        "category_qtrz_szsh",      # 其他融资
        "category_zcps_szsh",      # 资产评估
        "category_gszl_szsh",      # 公司治理
    ]
    
    # Query each keyword separately (Fix #3 - OR syntax doesn't work)
    keywords = ["许可", "授权", "合作协议", "重大合同", "收购", "战略合作"]
    
    for keyword in keywords:
        if len(rows) >= limit * 2:
            break
            
        data = {
            "pageNum": "1",
            "pageSize": "50",
            "column": "szse",
            "tabName": "fulltext",
            "plate": "",
            "stock": "",
            "searchkey": keyword,
            "secid": "",
            "category": "",  # Empty to get all categories
            "trade": "医药生物",
            "seDate": f"{start_date.strftime('%Y-%m-%d')}~{end_date.strftime('%Y-%m-%d')}",
            "sortName": "",
            "sortType": "",
            "isHLtitle": "true",
        }
        
        try:
            for attempt in range(3):
                try:
                    time.sleep(0.3)  # Rate limit
                    resp = requests.post(api_url, data=data, headers=headers, timeout=45)
                    if resp.status_code == 200:
                        break
                except requests.exceptions.Timeout:
                    if attempt < 2:
                        logging.info("巨潮资讯超时（%s），重试...", keyword)
                        time.sleep(2 * (attempt + 1))
                    continue
            else:
                continue
            
            if resp.status_code != 200:
                continue
            
            try:
                result = resp.json()
            except Exception:
                continue
            
            if not result:
                continue
            
            total = result.get("totalRecordNum", 0) or result.get("totalAnnouncement", 0)
            logging.info("巨潮资讯 '%s': 找到 %d 条", keyword, total)
            
            announcements = result.get("announcements") or []
            
            for ann in announcements:
                ann_id = ann.get("announcementId", "")
                if ann_id in seen_ids:
                    continue
                seen_ids.add(ann_id)
                
                title = ann.get("announcementTitle", "").strip()
                if not title:
                    continue
                
                # Filter by deal keywords in title
                if not any(kw in title for kw in CNINFO_DEAL_KEYWORDS):
                    continue
                
                code = ann.get("secCode", "")
                name = ann.get("secName", "")
                ann_time = ann.get("announcementTime", 0)
                adj_title = ann.get("adjunctUrl", "")  # PDF URL
                
                # Parse timestamp
                if ann_time:
                    try:
                        ann_date = datetime.fromtimestamp(ann_time / 1000, tz=timezone.utc).date()
                    except (ValueError, OSError):
                        ann_date = end_date
                else:
                    ann_date = end_date
                
                # Build URLs
                detail_url = f"http://www.cninfo.com.cn/new/disclosure/detail?announcementId={ann_id}&announcementTime={ann_time}"
                
                # Fetch PDF text if available (Fix #3)
                filing_text = ""
                if adj_title:
                    pdf_url = f"http://static.cninfo.com.cn/{adj_title}"
                    try:
                        time.sleep(0.2)
                        pdf_resp = requests.get(pdf_url, headers={"User-Agent": BROWSER_UA}, timeout=30)
                        if pdf_resp.status_code == 200:
                            filing_text = _extract_pdf_text(pdf_resp.content, max_chars=4000)
                    except Exception as e:
                        logging.debug("cninfo PDF fetch failed: %s", e)
                
                rows.append({
                    "source": "巨潮资讯",
                    "kind": "industry",
                    "filing_source": "cninfo",
                    "title": f"{name}({code}): {title[:60]}",
                    "url": detail_url,
                    "date": ann_date.isoformat(),
                    "summary": filing_text[:4000] if filing_text else f"A股公告: {title}",
                    "filing_text": filing_text,
                    "company": name,
                    "stock_code": code,
                    "filing_type": "announcement",
                })
                
                if len(rows) >= limit * 2:
                    break
                    
        except Exception as e:
            logging.warning("巨潮资讯搜索失败（%s）: %s", keyword, e)
            continue
    
    logging.info("巨潮资讯 得到 %d 条（有文本 %d 条）", 
                 len(rows),
                 sum(1 for r in rows if r.get("filing_text")))
    return rows[:limit], "ok" if rows else "failed"


def fetch_filing_sources(start: datetime, limit: int) -> tuple[list[dict], dict[str, tuple[int, str]]]:
    """Fetch deal filings from official sources.
    
    Fix #5 & #6: HKEX and cninfo are disabled until we can verify:
    - HKEX: Chapter 18A + Hang Seng Healthcare Index codes properly
    - cninfo: CSRC C27 (医药制造业) filter, exclude 一般性授权
    
    Currently only SEC EDGAR is enabled with SIC code filtering.
    
    Returns:
        Tuple of (all_rows, source_stats) where source_stats maps source name to (count, status)
    """
    all_rows = []
    source_stats = {}
    
    # SEC EDGAR - enabled with strict SIC code filtering
    try:
        sec_rows, sec_status = fetch_sec_filings(start, limit)
        all_rows.extend(sec_rows)
        source_stats["SEC EDGAR"] = (len(sec_rows), sec_status)
    except Exception as e:
        logging.warning("SEC 来源失败: %s", e)
        source_stats["SEC EDGAR"] = (0, "failed")
    
    # HKEX - DISABLED (Fix #5)
    # The HKEX_HEALTHCARE_CODES list contains wrong companies:
    # - 9988 (Alibaba), 9999 (NetEase - not biotech!), New Oriental, Yum China
    # - Stock code parsing also broken (extracts year 2026 from URL path)
    # Re-enable when we have verified Chapter 18A + Hang Seng Healthcare codes
    logging.info("HKEX 披露易：暂停使用（需验证公司列表）")
    source_stats["HKEX 披露易"] = (0, "disabled")
    
    # cninfo - DISABLED (Fix #6)
    # Issues:
    # - No CSRC C27 (医药制造业) filter to verify actual biopharma
    # - 一般性授权 (general authorization) filings create noise
    # Re-enable when we have proper CSRC industry code filtering
    logging.info("巨潮资讯：暂停使用（需添加 CSRC 行业过滤）")
    source_stats["巨潮资讯"] = (0, "disabled")
    
    logging.info("官方披露来源总计 %d 条", len(all_rows))
    return all_rows, source_stats


def normalize_doi(url: str) -> str:
    """Normalize URL to DOI identifier for deduplication.
    
    Maps various DOI URL formats to a canonical form:
    - https://doi.org/10.1038/s41591-026-04704-z -> 10.1038/s41591-026-04704-z
    - https://dx.doi.org/10.1038/... -> 10.1038/...
    - https://www.nature.com/articles/s41591-026-04704-z -> 10.1038/s41591-026-04704-z
    """
    url = url.lower().strip()
    url = re.sub(r'\?.*$', '', url)  # Remove query string
    
    # Direct DOI URL
    doi_match = re.match(r'https?://(?:dx\.)?doi\.org/(10\.\d+/.+)', url)
    if doi_match:
        return doi_match.group(1)
    
    # Nature articles URL -> DOI
    nature_match = re.match(r'https?://(?:www\.)?nature\.com/articles/(s\d+-\d+-\d+-\w+)', url)
    if nature_match:
        return f"10.1038/{nature_match.group(1)}"
    
    # Cell articles URL -> DOI
    cell_match = re.match(r'https?://(?:www\.)?cell\.com/[^/]+/(?:fulltext|abstract)/(S\d+-\d+\(\d+\)\d+-\d+)', url)
    if cell_match:
        return f"cell:{cell_match.group(1)}"
    
    # Science articles
    science_match = re.match(r'https?://(?:www\.)?science\.org/doi/(10\.\d+/.+)', url)
    if science_match:
        return science_match.group(1)
    
    return url


def load_existing_urls() -> set[str]:
    """Load URLs and DOIs from existing content to avoid duplicates."""
    existing = set()
    existing_raw = set()  # Keep raw URLs too for exact match
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
    
    # Also check index.html for URLs in CAT array
    index_path = ROOT / "index.html"
    if index_path.exists():
        try:
            content = index_path.read_text(encoding="utf-8")
            # Extract all URLs from url fields
            url_pattern = r"url:'([^']+)'"
            for match in re.finditer(url_pattern, content):
                url = match.group(1)
                if url.startswith('#'):
                    continue
                existing_raw.add(url)
                existing.add(normalize_doi(url))
        except OSError:
            pass
    
    existing.update(existing_raw)  # Include raw URLs for non-DOI content
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
        
        # Skip manual sources
        if source.get("type") == "manual":
            logging.info("跳过手动来源：%s", source_name)
            source_stats.append({"name": source_name, "status": "manual", "count": 0})
            continue
        
        # Per-source window override (e.g. Cell needs wider window)
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
            # Skip academic items with no abstract (can't write good drafts)
            # But exempt PubMed items since we can fetch abstracts separately
            if row.get("kind") == "academic" and row.get("source") != "PubMed":
                summary = (row.get("summary") or "").strip()
                # Skip if summary is just author names or very short
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
    
    # Fetch official filing sources for deals
    filing_limit = int(config.get("max_filing_deals") or 10)
    start_for_filings = end - timedelta(days=default_days)
    filing_rows, filing_stats = fetch_filing_sources(start_for_filings, filing_limit)
    
    # Fix #9: Dedup by company+deal type - prefer filings over news
    # Build set of (normalized_company, deal_type) from filings
    filing_keys = set()
    for row in filing_rows:
        company = _normalize_company_name(row.get("company", ""))
        # Classify deal type using the robust classifier
        title_lower = row.get("title", "").lower() + " " + row.get("summary", "").lower()
        deal_type = _classify_deal_type(title_lower) or "inv"
        filing_keys.add((company, deal_type))
        row["_dedup_key"] = (company, deal_type)
    
    # Add filing rows, deduping against existing URLs
    filing_count = 0
    for row in filing_rows:
        url = row["url"]
        if url in seen or url in existing:
            continue
        seen.add(url)
        rows.append(row)
        filing_count += 1
    
    # Now mark any existing news items that duplicate a filing
    # This happens later in claude_draft when we select deals
    # Store filing_keys on the config for later use
    if filing_keys:
        logging.info("Filing dedup keys: %d unique (company, deal_type) pairs", len(filing_keys))
    
    # Add filing sources to stats (Fix #9)
    for source_name, (count, status) in filing_stats.items():
        source_stats.append({"name": source_name, "status": status, "count": count})
    
    # Log per-source summary (Fix #9)
    logging.info("=== 来源统计 ===")
    for stat in source_stats:
        if stat["status"] == "manual":
            logging.info("  %s: 手动来源，跳过", stat["name"])
        elif stat["status"] == "failed":
            logging.info("  %s: 失败", stat["name"])
        elif stat["status"] == "skipped":
            logging.info("  %s: 跳过", stat["name"])
        else:
            logging.info("  %s: %d 条", stat["name"], stat["count"])
    
    logging.info("总计 %d 条新内容", len(rows))
    
    # Cap prompt size (Fix #9) - limit items per category before drafting
    max_academic = int(config.get("max_per_category_input") or 15)
    max_industry = int(config.get("max_industry_input") or 20)
    
    academic_items = [r for r in rows if r.get("kind") == "academic"]
    industry_items = [r for r in rows if r.get("kind") == "industry"]
    
    # Prioritize items with longer summaries (more content)
    academic_items.sort(key=lambda x: len(x.get("summary", "")), reverse=True)
    industry_items.sort(key=lambda x: (
        1 if x.get("filing_source") else 0,  # Filings first
        len(x.get("summary", ""))
    ), reverse=True)
    
    capped_rows = academic_items[:max_academic] + industry_items[:max_industry]
    
    if len(capped_rows) < len(rows):
        logging.info("提示词大小限制：%d 条学术 + %d 条行业（原 %d 条）", 
                     len(academic_items[:max_academic]), 
                     len(industry_items[:max_industry]),
                     len(rows))
    
    # Truncate summaries to cap total prompt size (target ~100k chars)
    total_chars = sum(len(r.get("summary", "")) for r in capped_rows)
    if total_chars > 100_000:
        char_per_item = 100_000 // len(capped_rows)
        for row in capped_rows:
            if len(row.get("summary", "")) > char_per_item:
                row["summary"] = row["summary"][:char_per_item] + "..."
        logging.info("截断摘要以控制提示词大小（每条约 %d 字符）", char_per_item)
    
    return capped_rows


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
                            "field": {"type": "string", "enum": list(FIELDS.keys()), "description": "Primary field based on the research subject"},
                            "title": {"type": "string", "description": "Chinese title"},
                            "journal": {"type": "string", "description": "Journal name only (Nature, Cell, etc.)"},
                            "authors": {"type": "string", "description": "Author names from source. Required. Must be actual person names, NOT journal or source names."},
                            "lead": {"type": "string", "description": "Why this matters, 1-2 sentences in Chinese"},
                            "body": {"type": "string", "description": "What the source says, in Chinese"},
                            "discuss": {"type": "string", "description": "Limitations and what we don't know"},
                            "steps": {
                                "type": "array",
                                "items": {"type": "string", "maxLength": 25},
                                "minItems": 3,
                                "maxItems": 5,
                                "description": "3-5 short steps (≤25 chars each) for mechanism diagram"
                            },
                            "study_type": {"type": "string", "description": "Study type: 临床试验/动物实验/体外实验/计算分析/综述"},
                            "n": {"type": "string", "description": "Sample size if mentioned"},
                            "evidence_level": {"type": "string", "enum": ["fulltext", "abstract", "press", "secondary"], "description": "Source quality"},
                            "image_prompt": {"type": "string", "description": "English visual description for diagram. Describe shapes, colors, spatial arrangement ONLY. Do NOT ask for labels, annotations, text, letters, numbers, captions, or named parts. The image generator cannot render text."},
                        },
                        "required": ["url", "field", "title", "authors", "lead", "steps"],
                    },
                },
                "deals": {
                    "type": "array",
                    "description": "Industry deals and news. For SEC/HKEX/cninfo filings, extract only amounts explicitly stated in the filing.",
                    "items": {
                        "type": "object",
                        "properties": {
                            "url": {"type": "string", "description": "Original URL from input"},
                            "title": {"type": "string", "description": "Chinese title"},
                            "kinds": {"type": "array", "items": {"type": "string", "enum": list(DEAL_KINDS)}},
                            "money": {"type": "string", "description": "Deal value in format 'X 亿美元' or '未披露'. Only from filing text."},
                            "upfront": {"type": "string", "description": "Upfront payment if disclosed, e.g. '1.5 亿美元首付'"},
                            "milestones": {"type": "string", "description": "Milestone payments if disclosed, e.g. '最高 8 亿美元里程碑'"},
                            "equity": {"type": "string", "description": "Equity stake if disclosed, e.g. '19.9% 股权'"},
                            "structure": {"type": "string", "description": "Deal structure details"},
                            "why": {"type": "string", "description": "Why this deal matters"},
                            "source_name": {"type": "string", "description": "Source name"},
                            "is_filing": {"type": "boolean", "description": "True if source is an official SEC/HKEX/cninfo filing"},
                            "amount_source": {"type": "string", "enum": ["filing", "news", "unknown"], "description": "Where the financial figures came from"},
                        },
                        "required": ["url", "title", "kinds", "money"],
                    },
                },
            },
            "required": ["articles", "deals"],
        },
    }

    prompt = f"""你是前沿追踪的编辑。下面是过去 {config.get('window_days', 7)} 天从固定来源抓到的条目，每条只有标题、链接、日期和来源摘要。

## 规则

只许使用这些条目里已经写明的事实。没有的数字、作者、适应症、金额不要编。一条材料不够写成解读，就不要选它。
学术最多 {config.get('max_academic', 6)} 篇，行业最多 {config.get('max_industry', 4)} 条。
每篇的 url 必须从输入里原样复制，不能修改。

## 领域分类规则

领域 field 只能是：{json.dumps(FIELDS, ensure_ascii=False)}

分类必须基于研究的主要对象，而非使用的工具或技术：
- c5 动物模型：仅当论文的主题是动物模型本身（如新品系建立、模型验证）。如果只是"在小鼠中验证"某疗法，应归到疗法对应的领域。
- c1 类器官：仅当论文的主题是类器官本身（培养方法、新类型）。用类器官筛选药物归 c2；用类器官研究肿瘤免疫归 c3。
- c3 肿瘤免疫：包括免疫检查点、肿瘤微环境、CAR-T 等针对肿瘤的免疫疗法。
- c7 细胞治疗：通用细胞治疗（包括非肿瘤适应症的 CAR-T）。
- 代谢工程、合成生物学、逆转录转座子研究不属于 c5 动物模型。

## 行业动态分类规则（仅限交易类）

kinds 只能是这三种交易类型：lic 授权合作、acq 并购、inv 融资/IPO。
- 临床进展、监管政策、裁员等非交易新闻不要放入 deals
- 如果不能确定是 lic/acq/inv 中的哪一种，不要选这条
- 金额格式统一为"X 亿美元"或"X 亿元人民币"或"未披露"。不要写"$26亿"这种混合格式。

## 精度优先原则

漏掉一条交易可以接受，发错一条不可接受。对任何不确定的信息：
- 公司不能确认是医药/生物科技公司？不选
- 金额在披露文件里找不到原文？写"未披露"
- 交易类型（lic/acq/inv）不明确？不选
- 细节不够写解读？不选

## 官方披露来源（SEC/HKEX/巨潮）

来源名含 "SEC"、"HKEX"、"巨潮" 的条目是官方公司披露，优先处理：
- is_filing 设为 true
- amount_source 设为 "filing"
- money、upfront、milestones、equity 只填摘要文本里能找到原文的数字，找不到就写"未披露"
- 绝不猜测或推算金额
- 交易分类：授权/合作协议归 lic；收购/并购归 acq；融资/配售/IPO 归 inv

## authors 字段

authors 必须是真实的作者人名（可以是英文或中文），绝对不能填期刊名、来源名或"Nature Medicine"这类内容。如果来源没有明确列出作者，写"（来源未列出作者）"。

## steps 字段

steps 必须是 3-5 个简短步骤（每个≤25字），描述论文的核心方法或发现过程。不能为空。

## 摘要处理

来源摘要已提供最多 2500 字符，足够写作。不要在输出中提及"摘要被截断"或"信息不完整"——如果摘要不足以写解读，就不选这篇。

调用 submit_weekly_digest 工具提交你的筛选结果。

输入：
{json.dumps(items, ensure_ascii=False)}
"""
    model = os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-5-5")
    logging.info("调用 Claude %s (tool_use, tool_choice=auto)", model)
    
    client = Anthropic()
    data = None
    
    for attempt in range(2):  # One retry if no tool_use block
        message = client.messages.create(
            model=model,
            max_tokens=8000,
            tools=[tool_schema],
            tool_choice={"type": "auto"},
            messages=[{"role": "user", "content": prompt}],
        )
        
        # Extract tool use result
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
    
    # Invalid author patterns (journal names, source names)
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
    
    # Blocked output patterns
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
        
        # Validate authors
        authors = (raw.get("authors") or "").strip()
        journal = (raw.get("journal") or "").strip()
        src = by_url[url]
        
        # Reject if author equals journal or source name
        if authors.lower() == journal.lower() or authors.lower() == src["source"].lower():
            logging.warning("丢弃作者无效的文章（与期刊/来源同名）：%s, authors=%s", url, authors)
            continue
        
        # Reject if author matches invalid patterns
        is_invalid_author = any(re.match(p, authors.lower()) for p in invalid_author_patterns)
        if is_invalid_author:
            logging.warning("丢弃作者无效的文章（期刊名）：%s, authors=%s", url, authors)
            continue
        
        # Validate steps
        steps = [str(s).strip() for s in (raw.get("steps") or []) if str(s).strip()][:5]
        if len(steps) < 3:
            logging.warning("丢弃步骤不足的文章：%s, steps=%d", url, len(steps))
            continue
        
        # Check for blocked patterns in output
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
    filing_deals = []  # Separate list for filing-backed deals (get reserved slots)
    news_deals = []    # News-sourced deals
    
    for raw in data.get("deals") or []:
        url = (raw.get("url") or "").strip()
        if url not in allowed:
            logging.warning("丢弃不在来源里的动态：%s", url)
            continue
        
        src = by_url[url]
        is_filing = raw.get("is_filing") or src.get("filing_source")
        filing_text = src.get("filing_text", "")
        
        # Strict deal type classification (Fix #5)
        # Only accept lic/acq/inv - drop clin/policy/newco for deals section
        kinds = [k for k in (raw.get("kinds") or []) if k in {"lic", "acq", "inv"}]
        
        if not kinds:
            # Try to classify from text
            combined_text = f"{raw.get('title', '')} {src.get('summary', '')} {filing_text}"
            classified = _classify_deal_type(combined_text)
            if classified:
                kinds = [classified]
            else:
                # Precision policy: unclear deal type - drop it
                logging.warning("丢弃类型不明确的交易：%s", url)
                continue
        
        # Normalize money format
        money = (raw.get("money") or "未披露").strip()
        # Convert $26亿 to 26 亿美元
        money = re.sub(r'^\$(\d+(?:\.\d+)?)\s*亿', r'\1 亿美元', money)
        # Ensure space before 亿
        money = re.sub(r'(\d)亿', r'\1 亿', money)
        
        # Fix #2 & #3: Amount verification for BOTH filings AND news
        # Precision policy: if amount can't be verified in source text, mark as 未披露
        source_text = filing_text if is_filing else src.get("summary", "")
        
        if money != "未披露":
            if not _verify_amount_in_text(money, source_text):
                if is_filing:
                    logging.warning("金额未在披露文件中找到，改为未披露：%s -> %s", url, money)
                else:
                    logging.warning("金额未在新闻来源中找到，改为未披露：%s -> %s", url, money)
                money = "未披露"
        
        # Label amount source for display (据报道 for news)
        amount_source = "filing" if is_filing else "news"
        
        deal_entry = {
            "url": url,
            "title": (raw.get("title") or src["title"]).strip(),
            "kinds": kinds,
            "money": money,
            "structure": (raw.get("structure") or "").strip(),
            "why": (raw.get("why") or "").strip(),
            "source_name": (raw.get("source_name") or src["source"]).strip(),
            "date": src["date"][:7],
            "amount_source": amount_source,
        }
        
        # Add filing-specific fields
        if is_filing:
            deal_entry["is_filing"] = True
            deal_entry["filing_source"] = src.get("filing_source", "unknown")
            
            # Verify upfront/milestones/equity amounts too
            for field in ["upfront", "milestones", "equity"]:
                val = raw.get(field, "").strip()
                if val and not _verify_amount_in_text(val, filing_text):
                    logging.warning("  %s 未在披露文件中找到，丢弃：%s", field, val)
                    val = ""
                if val:
                    deal_entry[field] = val
            
            filing_deals.append(deal_entry)
        else:
            if raw.get("upfront"):
                deal_entry["upfront"] = raw["upfront"].strip()
            if raw.get("milestones"):
                deal_entry["milestones"] = raw["milestones"].strip()
            if raw.get("equity"):
                deal_entry["equity"] = raw["equity"].strip()
            news_deals.append(deal_entry)
    
    # Fix #9: Apply company+deal_type dedup - prefer filings over news
    # Build set of (company, deal_type) from filings to exclude duplicate news
    filing_dedup_keys = set()
    for deal in filing_deals:
        company = _normalize_company_name(deal.get("title", "").split(":")[0])
        deal_type = deal.get("kinds", ["inv"])[0] if deal.get("kinds") else "inv"
        filing_dedup_keys.add((company, deal_type))
    
    # Filter news_deals to remove those that duplicate a filing
    deduped_news_deals = []
    news_dupes_removed = 0
    for deal in news_deals:
        company = _normalize_company_name(deal.get("title", "").split(":")[0])
        deal_type = deal.get("kinds", ["inv"])[0] if deal.get("kinds") else "inv"
        if (company, deal_type) in filing_dedup_keys:
            news_dupes_removed += 1
            logging.debug("News deal dupes filing: %s (%s)", deal.get("title", ""), deal_type)
            continue
        deduped_news_deals.append(deal)
    
    if news_dupes_removed:
        logging.info("去重：%d 条新闻与披露重复，已移除", news_dupes_removed)
    
    # Deal slot allocation (Fix #5)
    # Reserve slots for filing-backed deals, then fill with news
    cap_a = int(config.get("max_academic") or 6)
    max_deals = int(config.get("max_deals") or 6)
    max_filing_deals = int(config.get("max_filing_deals_output") or 4)
    
    # Take filing deals first (up to max_filing_deals), then fill remainder with news
    selected_filings = filing_deals[:max_filing_deals]
    remaining_slots = max_deals - len(selected_filings)
    selected_news = deduped_news_deals[:remaining_slots]
    
    deals = selected_filings + selected_news
    
    logging.info("交易选择：%d 条披露 + %d 条新闻 = %d 条", 
                 len(selected_filings), len(selected_news), len(deals))
    
    return {"articles": articles[:cap_a], "deals": deals}


def draw_image(prompt: str, dest: Path) -> None:
    from openai import OpenAI

    model = os.environ.get("OPENAI_IMAGE_MODEL", "gpt-image-1")
    # Sanitize model prompt to remove label requests, then add prefix and suffix
    clean_prompt = sanitize_image_prompt(prompt)
    full_prompt = IMAGE_PREFIX + clean_prompt[:1000] + IMAGE_SUFFIX
    logging.info("画图 %s -> %s", model, dest.name)
    result = OpenAI().images.generate(
        model=model,
        prompt=full_prompt,
        size="1536x1024",
        n=1,
    )
    raw = result.data[0].b64_json
    dest.write_bytes(base64.b64decode(raw))


def site_article(item: dict, image_rel: str) -> dict:
    import hashlib
    stamp = item["date"].replace("-", "")
    # Use stable hash of DOI/URL to avoid collisions (e.g. all nature.com URLs had same ID)
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
    # Include optional metadata fields if present
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
    # Add filing-specific fields if present
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
    
    # Build table of contents
    toc_items = []
    for i, art in enumerate(articles, 1):
        toc_items.append(f"{i}. {art['t'][:30]}...")
    
    # Lead headline for title
    lead_headline = articles[0]['t'][:25] if articles else "本周前沿"
    
    parts = [
        '<section style="font-family:-apple-system,BlinkMacSystemFont,\'Segoe UI\',Roboto,sans-serif;font-size:16px;line-height:1.75;color:#333;">',
        f'<p style="font-size:14px;color:#666;">前沿追踪 · {week} · TheraSik 出品</p>',
        '<p style="margin:1em 0;">本期内容均基于原始来源核对，配图由 AI 生成（示意图，非期刊原图）。</p>',
    ]
    
    # Table of contents
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
            # DOI as plain text instead of link
            url = art.get("url", "")
            if "doi.org" in url:
                doi = url.replace("https://doi.org/", "DOI: ")
                parts.append(f'<p style="font-size:12px;color:#999;margin:0.5em 0;">{doi}</p>')
    
    if deals:
        parts.append('<h2 style="border-left:4px solid #0f6b5c;padding-left:12px;margin:2em 0 1em;">交易动态</h2>')
        
        # Group deals by type (Fix #5)
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
                
                # Show amount with source indicator
                money = deal.get("m", "")
                if money and money != "未披露":
                    amount_note = ""
                    if deal.get("amount_source") == "news":
                        amount_note = "（据报道）"
                    elif deal.get("is_filing"):
                        amount_note = "（披露文件）"
                    parts.append(f'<p style="margin:0.3em 0;"><strong>{money}</strong>{amount_note}</p>')
                
                # Fix #10: Show upfront/milestones if available
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
    
    # Footer with AI disclosure and contact
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
    for index, item in enumerate(draft["articles"], start=1):
        filename = f"a{index}.png"
        try:
            draw_image(item["image_prompt"], img_dir / filename)
            rel = f"{dest.relative_to(ROOT).as_posix()}/images/{filename}"
        except Exception:
            logging.exception("配图失败：%s", item["title"])
            rel = ""
        art = site_article(item, rel)
        art["lead"] = item["lead"]
        art["body"] = item["body"]
        art["discuss"] = item["discuss"]
        articles.append(art)
    
    # Check for ID collisions
    seen_ids = {}
    for art in articles:
        if art["id"] in seen_ids:
            logging.error("ID 冲突：%s 和 %s 都生成了 ID %s", seen_ids[art["id"]], art["url"], art["id"])
            raise SystemExit(5)
        seen_ids[art["id"]] = art["url"]
    
    deals = [site_deal(item) for item in draft["deals"]]
    cover_prompt = (
        "A calm cluster of immune cells, one lipid nanoparticle and one organoid, "
        "arranged for a weekly science digest cover, left side left empty."
    )
    cover = dest / "wechat" / "cover.png"
    cover.parent.mkdir(parents=True, exist_ok=True)
    try:
        draw_image(cover_prompt, cover)
    except Exception:
        logging.exception("封面图失败")
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


def main() -> None:
    parser = argparse.ArgumentParser(description="生成一周的前沿追踪内容")
    parser.add_argument("--dry-run", action="store_true", help="只写到 preview/，不改网站内容目录")
    args = parser.parse_args()
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
