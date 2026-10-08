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
    "No text, no letters, no numbers, no labels, no watermark, no annotations. "
    "Simple shapes only. The subject fills the frame. "
)
UA = "FrontierDigestWeekly/1.0 (+https://inlight.therasik.com)"


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


def fetch_rss(source: dict, start: datetime, limit: int) -> list[dict]:
    feed = source.get("feed")
    if not feed:
        logging.warning("跳过 %s：没有 feed", source.get("name"))
        return []
    logging.info("抓取 RSS %s", source["name"])
    parsed = feedparser.parse(feed, agent=UA)
    if getattr(parsed, "bozo", False) and not parsed.entries:
        logging.warning("%s 的 feed 打不开：%s", source["name"], getattr(parsed, "bozo_exception", ""))
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


def load_existing_urls() -> set[str]:
    """Load URLs and DOIs from existing content to avoid duplicates."""
    existing = set()
    latest_path = ROOT / "content" / "latest.json"
    if latest_path.exists():
        try:
            data = json.loads(latest_path.read_text(encoding="utf-8"))
            for art in data.get("articles") or []:
                if art.get("url"):
                    existing.add(art["url"])
            for deal in data.get("deals") or []:
                if deal.get("url"):
                    existing.add(deal["url"])
        except (json.JSONDecodeError, OSError):
            pass
    
    # Also check index.html for DOIs in CAT array
    index_path = ROOT / "index.html"
    if index_path.exists():
        try:
            content = index_path.read_text(encoding="utf-8")
            # Extract DOIs from url fields
            doi_pattern = r"url:'(https://doi\.org/[^']+)'"
            for match in re.finditer(doi_pattern, content):
                existing.add(match.group(1))
        except OSError:
            pass
    
    logging.info("已有 %d 个去重 URL/DOI", len(existing))
    return existing


def fetch_all(config: dict) -> list[dict]:
    days = int(config.get("window_days") or 7)
    limit = int(config.get("max_per_source") or 6)
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=days)
    rows: list[dict] = []
    seen = set()
    existing = load_existing_urls()
    
    for source in config["sources"]:
        try:
            if source.get("type") == "pubmed":
                batch = fetch_pubmed(source, start.date(), end.date(), limit)
            else:
                batch = fetch_rss(source, start, limit)
        except Exception:
            logging.exception("来源失败：%s", source.get("name"))
            continue
        for row in batch:
            url = row["url"]
            if url in seen:
                continue
            if url in existing:
                logging.debug("跳过已有内容：%s", url)
                continue
            seen.add(url)
            rows.append(row)
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
                            "field": {"type": "string", "enum": list(FIELDS.keys()), "description": "Primary field"},
                            "title": {"type": "string", "description": "Chinese title"},
                            "journal": {"type": "string", "description": "Journal name"},
                            "authors": {"type": "string", "description": "Author names if available"},
                            "lead": {"type": "string", "description": "Why this matters, 1-2 sentences in Chinese"},
                            "body": {"type": "string", "description": "What the source says, in Chinese"},
                            "discuss": {"type": "string", "description": "Limitations and what we don't know"},
                            "steps": {"type": "array", "items": {"type": "string"}, "description": "Up to 4 short steps for mechanism diagram"},
                            "image_prompt": {"type": "string", "description": "English prompt for mechanism diagram, no text/labels"},
                        },
                        "required": ["url", "field", "title", "lead"],
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
                            "money": {"type": "string", "description": "Deal value or '未披露'"},
                            "structure": {"type": "string", "description": "Deal structure details"},
                            "why": {"type": "string", "description": "Why this deal matters"},
                            "source_name": {"type": "string", "description": "Source name"},
                        },
                        "required": ["url", "title", "kinds"],
                    },
                },
            },
            "required": ["articles", "deals"],
        },
    }

    prompt = f"""你是前沿追踪的编辑。下面是过去 {config.get('window_days', 7)} 天从固定来源抓到的条目，每条只有标题、链接、日期和来源摘要。

只许使用这些条目里已经写明的事实。没有的数字、作者、适应症、金额不要编。一条材料不够写成解读，就不要选它。
学术最多 {config.get('max_academic', 6)} 篇，行业最多 {config.get('max_industry', 4)} 条。
每篇的 url 必须从输入里原样复制，不能修改。
领域 field 只能是：{json.dumps(FIELDS, ensure_ascii=False)}
行业 kinds 只能是这些词的子集：acq 收购、lic 授权、newco NewCo、clin 临床进展、inv 投资融资、policy 监管政策。

学术文章用中文写 lead、body、discuss。image_prompt 用英文，描述一张没有任何文字和标签的机制图。
行业动态用中文。金额没写就写「未披露」。

调用 submit_weekly_digest 工具提交你的筛选结果。

输入：
{json.dumps(items, ensure_ascii=False)}
"""
    model = os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-4-20250514")
    logging.info("调用 Claude %s (tool_use)", model)
    
    message = Anthropic().messages.create(
        model=model,
        max_tokens=8000,
        tools=[tool_schema],
        tool_choice={"type": "tool", "name": "submit_weekly_digest"},
        messages=[{"role": "user", "content": prompt}],
    )
    
    # Extract tool use result
    data = None
    for block in message.content:
        if block.type == "tool_use" and block.name == "submit_weekly_digest":
            data = block.input
            break
    
    if data is None:
        logging.error("Claude did not return tool_use block")
        raise ValueError("No tool_use response from Claude")
    allowed = {row["url"] for row in items}
    by_url = {row["url"]: row for row in items}
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
        src = by_url[url]
        articles.append({
            "url": url,
            "field": field,
            "title": (raw.get("title") or src["title"]).strip(),
            "journal": (raw.get("journal") or src["source"]).strip(),
            "authors": (raw.get("authors") or "").strip(),
            "lead": (raw.get("lead") or "").strip(),
            "body": (raw.get("body") or "").strip(),
            "discuss": (raw.get("discuss") or "").strip(),
            "steps": [str(s).strip() for s in (raw.get("steps") or []) if str(s).strip()][:4],
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
        deals.append({
            "url": url,
            "title": (raw.get("title") or src["title"]).strip(),
            "kinds": kinds,
            "money": (raw.get("money") or "未披露").strip(),
            "structure": (raw.get("structure") or "").strip(),
            "why": (raw.get("why") or "").strip(),
            "source_name": (raw.get("source_name") or src["source"]).strip(),
            "date": src["date"][:7],
        })
    cap_a = int(config.get("max_academic") or 6)
    cap_d = int(config.get("max_industry") or 4)
    return {"articles": articles[:cap_a], "deals": deals[:cap_d]}


def draw_image(prompt: str, dest: Path) -> None:
    from openai import OpenAI

    model = os.environ.get("OPENAI_IMAGE_MODEL", "gpt-image-1")
    logging.info("画图 %s -> %s", model, dest.name)
    result = OpenAI().images.generate(
        model=model,
        prompt=IMAGE_PREFIX + prompt[:1200],
        size="1536x1024",
        n=1,
    )
    raw = result.data[0].b64_json
    dest.write_bytes(base64.b64decode(raw))


def site_article(item: dict, image_rel: str) -> dict:
    stamp = item["date"].replace("-", "")
    slug = re.sub(r"[^a-z0-9]+", "-", item["url"].lower())[:24].strip("-") or "item"
    item_id = f"w-{stamp}-{slug}"
    return {
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
    parts = [
        "<section>",
        f"<p>前沿追踪 · {week}</p>",
        "<p>这一期只写来源页面里能核对的内容。点标题到原文。</p>",
    ]
    if articles:
        parts.append("<h2>学术</h2>")
        for art in articles:
            parts.append(f"<h3>{art['t']}</h3>")
            if art.get("img"):
                parts.append(f"<p><img src=\"{art['img']}\" alt=\"\"></p>")
            parts.append(f"<p>{art.get('lead') or ''}</p>")
            if art.get("body"):
                parts.append(f"<p>{art['body']}</p>")
            if art.get("discuss"):
                parts.append(f"<p><b>讨论</b> {art['discuss']}</p>")
            parts.append(f"<p>{art.get('au') or ''} · {art.get('j') or ''}</p>")
            parts.append(f"<p><a href=\"{art['url']}\">原文</a></p>")
    if deals:
        parts.append("<h2>行业</h2>")
        for deal in deals:
            parts.append(f"<h3>{deal['t']}</h3>")
            parts.append(f"<p>{deal.get('m') or ''}</p>")
            parts.append(f"<p>{deal.get('why') or ''}</p>")
            parts.append(f"<p><a href=\"{deal['url']}\">{deal.get('src') or '出处'}</a></p>")
    parts.append("<p>栏位赞助写信到 contact@therasik.com。</p></section>")
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
