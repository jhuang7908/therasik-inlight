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

try:
    from inlight_fields import (
        FIELD_PROMPT_RULES,
        FIELDS,
        classify_draft_articles,
        claude_create_kwargs,
        site_classification_fields,
    )
    FIELD_MODULE_AVAILABLE = True
except ImportError:
    FIELD_MODULE_AVAILABLE = False
    FIELDS = {
        "f1": "类器官",
        "f2": "动物模型",
        "f3": "AI 药物设计",
        "f4": "肿瘤免疫与细胞治疗",
        "f5": "自身免疫与移植免疫",
        "f6": "疫苗与感染免疫",
        "f7": "抗体工程",
        "f8": "核酸与基因治疗（含 LNP 递送）",
        "f9": "精准肿瘤与临床转化",
    }
    FIELD_PROMPT_RULES = "领域 field 只能是：" + ", ".join(FIELDS)

ROOT = Path(__file__).resolve().parent
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
    """Remove label-related phrases from the model's image prompt."""
    patterns = [
        r'\bwith labels?\b',
        r'\blabou?r?e?l?e?d\b',
        r'\bannotated?\b',
        r'\bwith annotations?\b',
        r'\bwith captions?\b',
        r'\bcaptioned\b',
        r'\bwith text\b',
        r'\bshowing (?:the )?names?\b',
        r'\bnamed\b',
        r'"[^"]*"',  # Remove quoted text that might be label requests
    ]
    result = prompt
    for pattern in patterns:
        result = re.sub(pattern, '', result, flags=re.IGNORECASE)
    # Clean up extra spaces
    result = re.sub(r'\s+', ' ', result).strip()
    return result


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
        create_kwargs = dict(
            model=model,
            max_tokens=50,
            tools=[test_tool],
            tool_choice={"type": "auto"},
            messages=[{"role": "user", "content": "Call test_tool with ok=true"}],
        )
        if FIELD_MODULE_AVAILABLE:
            create_kwargs = claude_create_kwargs(**create_kwargs)
        client.messages.create(**create_kwargs)
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


def _fetch_rss_with_retry(feed: str, source_name: str, use_browser_ua: bool, max_attempts: int = 3) -> feedparser.FeedParserDict | None:
    """Fetch RSS feed with retry logic for transient errors."""
    import time
    import requests
    
    delays = [2, 5, 10]  # Exponential backoff
    last_error = None
    
    for attempt in range(max_attempts):
        try:
            if use_browser_ua:
                resp = requests.get(feed, headers={"User-Agent": BROWSER_UA}, timeout=30)
                resp.raise_for_status()
                parsed = feedparser.parse(resp.content)
            else:
                parsed = feedparser.parse(feed, agent=UA)
            
            # Check for parse errors (bozo) but allow if we got entries
            if getattr(parsed, "bozo", False) and not parsed.entries:
                bozo_exc = getattr(parsed, "bozo_exception", None)
                # Retry on XML parse errors like "no element found"
                if bozo_exc and "no element found" in str(bozo_exc).lower():
                    raise ValueError(f"XML parse error: {bozo_exc}")
                logging.warning("%s 的 feed 解析失败：%s", source_name, bozo_exc)
                return None
            
            return parsed
            
        except Exception as e:
            last_error = e
            if attempt < max_attempts - 1:
                delay = delays[min(attempt, len(delays) - 1)]
                logging.info("%s 抓取失败，%d秒后重试（第%d次）：%s", source_name, delay, attempt + 1, e)
                time.sleep(delay)
    
    logging.warning("%s 的 feed 重试后仍失败：%s", source_name, last_error)
    return None


def fetch_biorxiv_api(start: datetime, limit: int, categories: list[str] | None = None) -> list[dict]:
    """Fallback: fetch from bioRxiv details API when RSS fails."""
    import requests
    import time
    
    end_date = datetime.now(timezone.utc).date()
    start_date = start.date()
    
    api_url = f"https://api.biorxiv.org/details/biorxiv/{start_date.isoformat()}/{end_date.isoformat()}"
    logging.info("bioRxiv API fallback: %s", api_url)
    
    try:
        resp = requests.get(api_url, headers={"User-Agent": UA}, timeout=60)
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        logging.warning("bioRxiv API 请求失败：%s", e)
        return []
    
    collection = data.get("collection") or []
    if not collection:
        logging.info("bioRxiv API 返回 0 条")
        return []
    
    # Filter by category if specified
    if categories:
        categories_lower = [c.lower() for c in categories]
        collection = [p for p in collection if (p.get("category") or "").lower() in categories_lower]
    
    rows = []
    for paper in collection[:limit]:
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
        abstract = (paper.get("abstract") or "")[:700]
        category = paper.get("category") or "bioRxiv"
        
        rows.append({
            "source": f"bioRxiv {category}",
            "kind": "academic",
            "title": title,
            "url": f"https://doi.org/{doi}",
            "date": pub_date.isoformat(),
            "summary": abstract if abstract else f"{authors[:200]}",
        })
    
    logging.info("bioRxiv API fallback 得到 %d 条", len(rows))
    return rows


def fetch_rss(source: dict, start: datetime, limit: int) -> list[dict]:
    feed = source.get("feed")
    if not feed:
        logging.warning("跳过 %s：没有 feed", source.get("name"))
        return []
    logging.info("抓取 RSS %s", source["name"])
    
    source_name = source.get("name", "unknown")
    use_browser_ua = source.get("needs_browser_ua", False)
    
    # Fetch with retry
    parsed = _fetch_rss_with_retry(feed, source_name, use_browser_ua)
    
    # bioRxiv fallback to API
    if parsed is None and "biorxiv" in source_name.lower():
        category = source.get("biorxiv_category") or "immunology"
        logging.info("%s RSS 失败，尝试 API fallback（分类：%s）", source_name, category)
        return fetch_biorxiv_api(start, limit, categories=[category])
    
    if parsed is None:
        return []
    
    rows = []
    for entry in parsed.entries:
        when = _parse_struct(entry)
        if not _within(when, start):
            continue
        url = (entry.get("link") or "").strip()
        title = (entry.get("title") or "").strip()
        if not url or not title:
            continue
        summary = re.sub(r"<[^>]+>", " ", entry.get("summary") or entry.get("description") or "")
        summary = re.sub(r"\s+", " ", summary).strip()[:700]
        rows.append({
            "source": source["name"],
            "kind": source.get("kind") or "academic",
            "title": title,
            "url": url,
            "date": when.date().isoformat(),
            "summary": summary,
        })
        if len(rows) >= limit:
            break
    logging.info("%s 得到 %d 条", source["name"], len(rows))
    return rows


def fetch_pubmed(source: dict, start: date, end: date, limit: int) -> list[dict]:
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
    with urllib.request.urlopen(req, timeout=30) as resp:
        found = json.loads(resp.read().decode("utf-8"))
    ids = found.get("esearchresult", {}).get("idlist") or []
    if not ids:
        logging.info("PubMed 没有命中")
        return []
    sum_url = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi?" + urllib.parse.urlencode({
        "db": "pubmed",
        "id": ",".join(ids),
        "retmode": "json",
    })
    req = urllib.request.Request(sum_url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=30) as resp:
        summary = json.loads(resp.read().decode("utf-8"))
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
        rows.append({
            "source": "PubMed",
            "kind": "academic",
            "title": title,
            "url": f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/",
            "date": raw_day if len(raw_day) == 10 else end.isoformat(),
            "summary": f"{journal}. {authors}".strip(),
        })
    logging.info("PubMed 得到 %d 条", len(rows))
    return rows


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
            else:
                batch = fetch_rss(source, start, limit)
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
            if row.get("kind") == "academic":
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
        source_stats.append({"name": source_name, "status": "ok", "count": count})
    
    # Log per-source summary
    logging.info("=== 来源统计 ===")
    for stat in source_stats:
        if stat["status"] == "manual":
            logging.info("  %s: 手动来源，跳过", stat["name"])
        elif stat["status"] == "failed":
            logging.info("  %s: 失败", stat["name"])
        else:
            logging.info("  %s: %d 条", stat["name"], stat["count"])
    logging.info("总计 %d 条新内容", len(rows))
    
    return rows


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
                            "field": {"type": "string", "enum": [*FIELDS.keys(), "none"], "description": "Primary field based on the research subject, or none if out of scope"},
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
                    "description": "Industry deals and news",
                    "items": {
                        "type": "object",
                        "properties": {
                            "url": {"type": "string", "description": "Original URL from input"},
                            "title": {"type": "string", "description": "Chinese title"},
                            "kinds": {"type": "array", "items": {"type": "string", "enum": list(DEAL_KINDS)}},
                            "money": {"type": "string", "description": "Deal value in format 'X 亿美元' or '未披露'"},
                            "structure": {"type": "string", "description": "Deal structure details"},
                            "why": {"type": "string", "description": "Why this deal matters"},
                            "source_name": {"type": "string", "description": "Source name"},
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

领域 field 只能是：{json.dumps(FIELDS, ensure_ascii=False)}，或 none。
{FIELD_PROMPT_RULES}

## 行业动态分类规则

kinds 只能是这些词的子集：acq 收购、lic 授权、newco NewCo、clin 临床进展、inv 投资融资、policy 监管政策。
- policy 仅限监管机构的正式政策或审批决定。裁员、战略调整、公司重组不是 policy。
- 金额格式统一为"X 亿美元"或"X 亿元人民币"或"未披露"。不要写"$26亿"这种混合格式。

## authors 字段

authors 必须是真实的作者人名（可以是英文或中文），绝对不能填期刊名、来源名或"Nature Medicine"这类内容。如果来源没有明确列出作者，写"（来源未列出作者）"。

## steps 字段

steps 必须是 3-5 个简短步骤（每个≤25字），描述论文的核心方法或发现过程。不能为空。

调用 submit_weekly_digest 工具提交你的筛选结果。

输入：
{json.dumps(items, ensure_ascii=False)}
"""
    model = os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-5-5")
    logging.info("调用 Claude %s (tool_use, tool_choice=auto)", model)
    
    client = Anthropic()
    data = None
    
    for attempt in range(2):  # One retry if no tool_use block
        create_kwargs = dict(
            model=model,
            max_tokens=8000,
            tools=[tool_schema],
            tool_choice={"type": "auto"},
            messages=[{"role": "user", "content": prompt}],
        )
        if FIELD_MODULE_AVAILABLE:
            create_kwargs = claude_create_kwargs(**create_kwargs)
        message = client.messages.create(**create_kwargs)
        
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
        if field not in FIELDS and field != "none":
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
    for raw in data.get("deals") or []:
        url = (raw.get("url") or "").strip()
        if url not in allowed:
            logging.warning("丢弃不在来源里的动态：%s", url)
            continue
        kinds = [k for k in (raw.get("kinds") or []) if k in DEAL_KINDS]
        if not kinds:
            kinds = ["clin"]
        src = by_url[url]
        
        # Normalize money format
        money = (raw.get("money") or "未披露").strip()
        # Convert $26亿 to 26 亿美元
        money = re.sub(r'^\$(\d+(?:\.\d+)?)\s*亿', r'\1 亿美元', money)
        # Ensure space before 亿
        money = re.sub(r'(\d)亿', r'\1 亿', money)
        
        deals.append({
            "url": url,
            "title": (raw.get("title") or src["title"]).strip(),
            "kinds": kinds,
            "money": money,
            "structure": (raw.get("structure") or "").strip(),
            "why": (raw.get("why") or "").strip(),
            "source_name": (raw.get("source_name") or src["source"]).strip(),
            "date": src["date"][:7],
        })
    cap_a = int(config.get("max_academic") or 6)
    cap_d = int(config.get("max_industry") or 4)
    articles = articles[:cap_a]
    if FIELD_MODULE_AVAILABLE:
        articles = classify_draft_articles(articles, by_url, client=client, model=model)
    return {"articles": articles, "deals": deals[:cap_d]}


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
        "tags": [item["field"]] if item.get("field") else [],
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
    if FIELD_MODULE_AVAILABLE:
        result.update(site_classification_fields(item))
        if result.get("rf"):
            primary = result.get("f")
            result["tags"] = [primary, *result["rf"]] if primary else list(result["rf"])
    return result


def site_deal(item: dict) -> dict:
    return {
        "d": item["date"],
        "kinds": item["kinds"],
        "t": item["title"],
        "m": item["money"],
        "ms": item["structure"],
        "why": item["why"],
        "src": item["source_name"],
        "url": item["url"],
    }


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
        parts.append('<h2 style="border-left:4px solid #0f6b5c;padding-left:12px;margin:2em 0 1em;">行业</h2>')
        for deal in deals:
            parts.append(f'<h3 style="font-size:18px;margin:1.5em 0 0.5em;color:#1d2a27;">{deal["t"]}</h3>')
            if deal.get("m") and deal["m"] != "未披露":
                parts.append(f'<p style="margin:0.5em 0;"><strong>{deal["m"]}</strong></p>')
            parts.append(f'<p style="margin:0.5em 0;">{deal.get("why") or ""}</p>')
            parts.append(f'<p style="font-size:14px;color:#666;margin:0.5em 0;">来源：{deal.get("src") or "未注明"}</p>')
    
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
