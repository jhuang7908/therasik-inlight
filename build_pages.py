#!/usr/bin/env python3
"""Generate static HTML pages for individual articles with Open Graph meta tags.

    python build_pages.py          # reads index.html, writes pages/article/*.html
    python build_pages.py --dry-run  # only preview, no writes

This enables social sharing with article-specific titles, descriptions, and images.
Each page uses a meta refresh to redirect to the main site's hash-based article view.
"""

from __future__ import annotations

import argparse
import html
import json
import os
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PAGES_DIR = ROOT / "pages" / "article"
# HTTPS is now live and enforced
SITE_URL = "https://inlight.therasik.com"

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


def extract_cat_array(index_html: str) -> list[dict]:
    """Extract article data from CAT array using regex pattern matching."""
    articles = []
    
    pattern = re.compile(
        r"\{id:'([^']+)',"
        r"f:'([^']+)',"
        r"t:'([^']+)',"
        r"ds:'([^']+)',"
        r"disp:'([^']+)',"
        r"j:'([^']+)',"
        r"url:'([^']+)',"
        r"au:'([^']+)',"
        r"tags:\[([^\]]*)\],"
        r"sum:'([^']*(?:\\'[^']*)*)'",
        re.DOTALL
    )
    
    for match in pattern.finditer(index_html):
        article_id, field, title, date_str, disp, journal, url, author, tags_str, summary = match.groups()
        
        title = title.replace("\\'", "'")
        summary = summary.replace("\\'", "'")
        author = author.replace("\\'", "'")
        
        tags = [t.strip().strip("'") for t in tags_str.split(",") if t.strip()]
        
        articles.append({
            "id": article_id,
            "f": field,
            "t": title,
            "ds": date_str,
            "disp": disp,
            "j": journal,
            "url": url,
            "au": author,
            "tags": tags,
            "sum": summary,
        })
    
    if not articles:
        raise ValueError("Could not find any articles in index.html CAT array")
    
    return articles


def generate_article_page(article: dict, dry_run: bool = False) -> str | None:
    """Generate a static HTML page for one article."""
    article_id = article.get("id", "")
    if not article_id:
        return None

    title = article.get("t", "前沿追踪文章")
    summary = article.get("sum", "")[:200]
    field_id = article.get("f", "")
    field_name = FIELDS.get(field_id, "生物医药")
    author = article.get("au", "")
    journal = article.get("j", "")
    date_str = article.get("ds", "")
    url = article.get("url", "")
    
    # Check for article-specific image
    img_candidates = [
        f"img/papers/{article_id}.jpg",
        f"img/papers/{article_id}.png",
        f"img/figs/{article_id}.jpg",
        f"img/{article_id}.webp",
        f"img/{article_id}.png",
        f"img/{article_id}.jpg",
    ]
    og_image = f"{SITE_URL}/img/og-image.png"
    for img_path in img_candidates:
        if (ROOT / img_path).exists():
            og_image = f"{SITE_URL}/{img_path}"
            break
    
    # Canonical is this static page, redirect goes to #p-{id}
    redirect_url = f"{SITE_URL}/#p-{article_id}"
    page_url = f"{SITE_URL}/pages/article/{article_id}.html"
    
    og_title = html.escape(f"{title} - 前沿追踪")
    og_description = html.escape(summary if summary else f"{field_name}领域研究进展")
    
    page_html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{html.escape(title)} - 前沿追踪</title>
<meta name="description" content="{og_description}">

<!-- Open Graph -->
<meta property="og:title" content="{og_title}">
<meta property="og:description" content="{og_description}">
<meta property="og:type" content="article">
<meta property="og:url" content="{html.escape(page_url)}">
<meta property="og:image" content="{html.escape(og_image)}">
<meta property="og:locale" content="zh_CN">
<meta property="og:site_name" content="前沿追踪">

<!-- Twitter Card -->
<meta name="twitter:card" content="summary_large_image">
<meta name="twitter:title" content="{og_title}">
<meta name="twitter:description" content="{og_description}">
<meta name="twitter:image" content="{html.escape(og_image)}">

<!-- Article metadata -->
<meta property="article:published_time" content="{html.escape(date_str)}">
<meta property="article:section" content="{html.escape(field_name)}">
<meta property="article:tag" content="{html.escape(field_name)}">

<!-- Canonical is this static page for SEO; redirect to SPA view -->
<link rel="canonical" href="{html.escape(page_url)}">
<meta http-equiv="refresh" content="0;url={html.escape(redirect_url)}">

<style>
body {{
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
  max-width: 600px;
  margin: 40px auto;
  padding: 20px;
  background: #f5f7f6;
  color: #1d2a27;
}}
h1 {{ font-size: 1.5em; line-height: 1.4; }}
.meta {{ color: #5c6b67; font-size: 0.9em; margin: 1em 0; }}
.redirect {{ 
  background: #e8f5f2; 
  padding: 1em; 
  border-radius: 8px;
  margin-top: 2em;
}}
a {{ color: #0f6b5c; }}
</style>
</head>
<body>
<article>
<h1>{html.escape(title)}</h1>
<p class="meta">
{html.escape(field_name)} · {html.escape(journal)} · {html.escape(date_str)}<br>
{html.escape(author)}
</p>
<p>{html.escape(summary)}...</p>
<div class="redirect">
正在跳转至完整文章页面...<br>
<a href="{html.escape(redirect_url)}">如未自动跳转，请点击此处</a>
</div>
</article>
<script>
window.location.replace("{redirect_url}");
</script>
</body>
</html>
"""

    if not dry_run:
        page_path = PAGES_DIR / f"{article_id}.html"
        page_path.parent.mkdir(parents=True, exist_ok=True)
        page_path.write_text(page_html, encoding="utf-8")
        return str(page_path)
    
    return f"(dry-run) {PAGES_DIR / article_id}.html"


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate static article pages with OG tags")
    parser.add_argument("--dry-run", action="store_true", help="Preview without writing files")
    args = parser.parse_args()

    index_path = ROOT / "index.html"
    if not index_path.exists():
        print("Error: index.html not found")
        return

    index_html = index_path.read_text(encoding="utf-8")
    
    try:
        articles = extract_cat_array(index_html)
    except ValueError as e:
        print(f"Error: {e}")
        return

    print(f"Found {len(articles)} articles")
    
    generated = 0
    for article in articles:
        result = generate_article_page(article, dry_run=args.dry_run)
        if result:
            print(f"  {result}")
            generated += 1

    print(f"\nGenerated {generated} article pages")
    
    if not args.dry_run:
        print(f"Pages written to: {PAGES_DIR}")
        print("\nTo enable social sharing, configure your server to serve /pages/article/*.html")
        print("Social crawlers will see OG tags, users will be redirected to the main site.")


if __name__ == "__main__":
    main()
