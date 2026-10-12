#!/usr/bin/env python3
"""Build static article pages, sitemap, and the live catalog overlay.

    python build_pages.py          # write pages + patch index.html catalog
    python build_pages.py --dry-run

Curated r8 解读 articles are rendered in the structured format. Hidden
ids (no_fulltext / excluded) are omitted from public pages and sitemap.
"""

from __future__ import annotations

import argparse
import html
import json
import re
from pathlib import Path

import inlight_catalog as cat

ROOT = cat.ROOT
PAGES_DIR = ROOT / "pages" / "article"
SITE_URL = cat.SITE_URL
CATALOG_MARK_BEGIN = "/* @@INLIGHT_CATALOG_BEGIN */"
CATALOG_MARK_END = "/* @@INLIGHT_CATALOG_END */"


def js_str(s: str) -> str:
    return json.dumps(s, ensure_ascii=False)


def catalog_js_block(catalog: dict) -> str:
    fields = cat.load_fields()
    published = [cat._public_card(a) for a in cat.published_articles(catalog)]
    fnames = {f["k"]: f["n"] for f in fields}
    field_list = [
        {"k": f["k"], "n": f["n"], "d": f["d"], "color": f["color"], "field": f["field"]}
        for f in fields
    ]
    locked = cat.locked_ids(catalog)
    hidden = cat.hidden_ids(catalog)
    lines = [
        CATALOG_MARK_BEGIN,
        f"const FNAMES={json.dumps(fnames, ensure_ascii=False)};",
        f"const FIELDS={json.dumps(field_list, ensure_ascii=False)};",
        f"const LOCKED=new Set({json.dumps(locked, ensure_ascii=False)});",
        f"const HIDDEN=new Set({json.dumps(hidden, ensure_ascii=False)});",
        f"const CAT={json.dumps(published, ensure_ascii=False)};",
        CATALOG_MARK_END,
    ]
    return "\n".join(lines)


def write_site_articles_js(catalog: dict, bodies: dict[str, str]) -> Path:
    payload = {
        "fields": cat.load_fields(),
        "locked": cat.locked_ids(catalog),
        "hidden": cat.hidden_ids(catalog),
        "published": [a["id"] for a in cat.published_articles(catalog)],
        "bodies": bodies,
    }
    path = ROOT / "content" / "site_articles.js"
    path.write_text(
        "window.INLIGHT_SITE=" + json.dumps(payload, ensure_ascii=False) + ";\n",
        encoding="utf-8",
    )
    return path


def write_published_json(catalog: dict) -> Path:
    cards = [cat._public_card(a) for a in cat.published_articles(catalog)]
    path = ROOT / "content" / "published.json"
    path.write_text(json.dumps({
        "generated": "r8-golive",
        "articles": cards,
        "locked": cat.locked_ids(catalog),
        "hidden": cat.hidden_ids(catalog),
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def article_nav(active: str = "") -> str:
    """Same labels as the homepage nav; links go to the homepage views."""
    def item(href: str, label: str, key: str) -> str:
        on = ' class="on"' if key == active else ""
        return f'<a href="{href}"{on}>{label}</a>'
    return f"""<nav><div class="navshell"><div class="nav-in">
  <a class="qy-made" href="/#about" title="启元智研（北京）生物科技有限公司"><img src="../../assets/brand/qiyuan_mark_A_tight.svg" alt="" aria-hidden="true" width="28" height="26"><span>启元智研 出品</span></a>
  <a class="brand" href="/#home">
    <span class="bmark-wrap"><img src="../../img/logo.png" alt=""></span>
    <span class="btext"><span class="bn">前沿追踪</span><span class="bs">FRONTIER DIGEST</span></span>
  </a>
  <div class="nav-tabs">
    {item('/#home', '首页', 'home')}
    {item('/#fields', '学术进展', 'fields')}
    {item('/#deals', '经济动态', 'deals')}
    {item('/#services', '服务', 'services')}
    {item('/#about', '关于我们', 'about')}
  </div>
  <a class="navsub" href="/#support">订阅</a>
</div></div></nav>"""


ARTICLE_FOOT = """<footer>
  <div class="footshell"><div class="foot-in">
    <div class="fbrand">
      <b>前沿追踪</b>
      <p class="fco" style="display:flex;align-items:center;gap:8px"><img src="../../assets/brand/qiyuan_mark_A_tight.svg" alt="" aria-hidden="true" width="28" height="26" style="display:block;height:26px;width:auto;flex:none"><span>启元智研 出品。九个领域的前沿进展与经济动态，中文整理。</span></p>
    </div>
    <div class="footnav">
      <a href="/#home">首页</a><a href="/#fields">学术进展</a><a href="/#deals">经济动态</a><a href="/#archive">存档</a><a href="/#about">关于我们</a><a href="/#services">服务</a><a href="/#support">订阅</a>
    </div>
    <p class="footmail">广告、赞助、纠错都写 <a href="mailto:contact@therasik.com">contact@therasik.com</a></p>
  </div></div>
</footer>"""

FEED_LINK = f'<link rel="alternate" type="application/rss+xml" title="前沿追踪" href="{SITE_URL}/feed.xml">'


def _short_date(ds: str) -> str:
    return ds[:10].replace("-", ".") if len(ds or "") >= 10 else ""


def _end_link(a: dict, cls: str, label: str) -> str:
    title = a.get("card_title") or a.get("title") or a.get("t") or a["id"]
    rec = cat.field_record(a["field"])
    meta = " · ".join(x for x in (rec["n"], _short_date(a.get("ds") or "")) if x)
    return (
        f'<a class="pn {cls}" href="{html.escape(a["id"])}.html">'
        f'<span class="k">{label}</span><b>{html.escape(title)}</b>'
        f'<em>{html.escape(meta)}</em></a>'
    )


def related_articles(article: dict, published: list[dict], limit: int = 3) -> list[dict]:
    """Same primary field, nearest publication date first; published ids only."""
    from datetime import date

    def day(a: dict) -> int:
        ds = a.get("ds") or ""
        try:
            return date(int(ds[:4]), int(ds[5:7]), int(ds[8:10])).toordinal()
        except (ValueError, IndexError):
            return 0
    me = day(article)
    pool = [a for a in published if a["id"] != article["id"] and a.get("field") == article.get("field")]
    pool.sort(key=lambda a: (abs(day(a) - me), a["id"]))
    return pool[:limit]


def article_end_html(article: dict, published: list[dict]) -> str:
    """上一篇 / 下一篇 (site 最新 order), 相关阅读 (same field), 订阅."""
    ids = [a["id"] for a in published]
    i = ids.index(article["id"])
    prev_a = published[i - 1] if i > 0 else None
    next_a = published[i + 1] if i + 1 < len(published) else None
    pn = []
    pn.append(_end_link(prev_a, "prev", "← 上一篇") if prev_a else '<span class="pn prev none"><span class="k">← 上一篇</span><b>已是第一篇</b></span>')
    pn.append(_end_link(next_a, "next", "下一篇 →") if next_a else '<span class="pn next none"><span class="k">下一篇 →</span><b>已是最后一篇</b></span>')
    rel = related_articles(article, published)
    rel_items = "".join(
        f'<li><a href="{html.escape(a["id"])}.html">{html.escape(a.get("card_title") or a.get("title") or a["id"])}</a>'
        f'<span>{html.escape(" · ".join(x for x in ((a.get("j") or ""), _short_date(a.get("ds") or "")) if x))}</span></li>'
        for a in rel
    )
    rec = cat.field_record(article["field"])
    rel_html = (
        f'<div class="art-rel"><h2>相关阅读<small>{html.escape(rec["n"])}</small></h2><ul>{rel_items}</ul></div>'
        if rel_items else ""
    )
    return f"""<section class="art-end" aria-label="继续阅读">
 <div class="art-pn">{''.join(pn)}</div>
 {rel_html}
 <div class="art-sub"><div><b>每周一封，中文</b><p>九个领域的解读摘要加经济动态，正文附原文链接。免费，可随时退订。</p></div><a class="btn" href="/#support">订阅每周更新</a></div>
</section>"""


def rebase_img_paths(body: str, prefix: str) -> str:
    return body.replace('src="img/', f'src="{prefix}img/')


def render_article_main(article: dict) -> str:
    data, md = cat.load_article_bundle(article["id"])
    inner = data.get("article") or {}
    cover = article.get("cover_caption") or ""
    mech_image = article.get("mech_image")
    return cat.render_structured_md(md, article["id"], cover, mech_image=mech_image), inner, data


def build_article_page(article: dict, body_root: str, inner: dict, data: dict, end_html: str = "") -> str:
    aid = article["id"]
    title = article.get("title") or inner.get("title") or aid
    rec = cat.field_record(article["field"])
    chips = cat.chips_html(article, header=True)
    ds = article.get("ds") or ""
    date_cn = ""
    if len(ds) >= 10:
        date_cn = f"{ds[:4]} 年 {int(ds[5:7])} 月 {int(ds[8:10])} 日"
    citation = inner.get("citation") or article.get("citation") or ""
    doi = cat.doi_of(citation, article.get("url") or "")
    journal = article.get("j") or cat.journal_of(citation, "")
    one = inner.get("one_liner") or article.get("sum") or ""
    og_image = f"{SITE_URL}/" + (article.get("card_image") or f"img/{aid}_card.webp")
    page_url = f"{SITE_URL}/pages/article/{aid}.html"
    body = rebase_img_paths(body_root, "../../")
    parts = cat.split_structured_body(body)
    quick_look = article.get("quick_look") or inner.get("quick_look") or data.get("quick_look") or ""
    author_intro = article.get("author_intro") or inner.get("author_intro") or data.get("author_intro") or ""
    rail = cat.render_article_rail(
        article,
        parts,
        journal=journal,
        date_cn=date_cn,
        url=article.get("url") or doi,
        field_label=rec["n"],
        related=cat.related_labels(article),
        quick_look=quick_look,
        author_intro=author_intro,
    )
    preprint_bit = " · 预印本（未经同行评审）" if article.get("preprint") else ""
    og_title = html.escape(f"{title} - 前沿追踪")
    og_desc = html.escape((quick_look or one)[:200] if (quick_look or one) else rec["n"])
    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<title>{html.escape(title)} - 前沿追踪</title>
<meta name="description" content="{og_desc}">
<meta property="og:title" content="{og_title}">
<meta property="og:description" content="{og_desc}">
<meta property="og:type" content="article">
<meta property="og:url" content="{html.escape(page_url)}">
<meta property="og:image" content="{html.escape(og_image)}">
<meta property="og:locale" content="zh_CN">
<meta property="og:site_name" content="前沿追踪">
<meta name="twitter:card" content="summary_large_image">
<meta name="twitter:title" content="{og_title}">
<meta name="twitter:description" content="{og_desc}">
<meta name="twitter:image" content="{html.escape(og_image)}">
<meta property="article:published_time" content="{html.escape(ds)}">
<meta property="article:section" content="{html.escape(rec['n'])}">
<link rel="canonical" href="{html.escape(page_url)}">
{FEED_LINK}
<link rel="icon" type="image/png" href="../../img/favicon.png">
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Noto+Serif+SC:wght@600;800&family=Noto+Sans+SC:wght@400;500;700&display=swap">
<link rel="stylesheet" href="../../site.css?v=2">
</head>
<body>
{article_nav()}
<div class="pv-shell pv-art"><div class="wrap">
<a class="back" href="/#home">← 返回首页</a>
<article>
 <div class="ahead">
  <div class="eyebrow">{html.escape(rec['n'])}{preprint_bit}</div>
  <h1 class="zh-title">{cat.inline_markup(title)}</h1>
  {chips}
 </div>
 <div class="abody">
  {rail}
  <div class="amain">
{parts['body']}
  </div>
 </div>
</article>
{end_html}
</div></div>
{ARTICLE_FOOT}
</body>
</html>
"""


def hidden_stub_page(article: dict) -> str:
    """Keep a share URL for hidden ids but do not list them publicly."""
    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="robots" content="noindex,nofollow,noarchive">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>未作为完整解读发布 · InLight</title>
<link rel="canonical" href="{SITE_URL}/">
</head>
<body>
<p>这篇条目未作为完整解读发布。<a href="{SITE_URL}/">返回首页</a></p>
</body>
</html>
"""


def write_sitemap(public_ids: list[str]) -> Path:
    urls = [f"{SITE_URL}/", f"{SITE_URL}/#fields", f"{SITE_URL}/#deals", f"{SITE_URL}/#archive"]
    urls.extend(f"{SITE_URL}/pages/article/{aid}.html" for aid in public_ids)
    parts = ['<?xml version="1.0" encoding="UTF-8"?>', '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">']
    for u in urls:
        parts.append(f"  <url><loc>{html.escape(u)}</loc></url>")
    parts.append("</urlset>\n")
    path = ROOT / "sitemap.xml"
    path.write_text("\n".join(parts), encoding="utf-8")
    return path


def _rfc822(ds: str) -> str:
    from datetime import datetime, timedelta, timezone
    dt = datetime(int(ds[:4]), int(ds[5:7]), int(ds[8:10]), 0, 0, tzinfo=timezone(timedelta(hours=8)))
    return dt.strftime("%a, %d %b %Y %H:%M:%S +0800")


def write_feed(published: list[dict]) -> Path:
    """RSS 2.0 of published 解读 only (hidden ids never appear)."""
    esc = html.escape
    dated = [a for a in published if len(a.get("ds") or "") >= 10]
    last = max((a["ds"] for a in dated), default="")
    items = []
    for a in published:
        url = f"{SITE_URL}/pages/article/{a['id']}.html"
        title = a.get("title") or a.get("t") or a["id"]
        desc = a.get("quick_look") or a.get("sum") or ""
        rec = cat.field_record(a["field"])
        bits = [
            "  <item>",
            f"    <title>{esc(title)}</title>",
            f"    <link>{esc(url)}</link>",
            f'    <guid isPermaLink="true">{esc(url)}</guid>',
        ]
        if len(a.get("ds") or "") >= 10:
            bits.append(f"    <pubDate>{_rfc822(a['ds'])}</pubDate>")
        bits.append(f"    <category>{esc(rec['n'])}</category>")
        if desc:
            bits.append(f"    <description>{esc(desc)}</description>")
        bits.append("  </item>")
        items.append("\n".join(bits))
    head = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<rss version="2.0" xmlns:atom="http://www.w3.org/2005/Atom">',
        "<channel>",
        "  <title>前沿追踪 Frontier Digest</title>",
        f"  <link>{SITE_URL}/</link>",
        "  <description>启元智研出品。九个领域的前沿进展与经济动态，中文整理。</description>",
        "  <language>zh-CN</language>",
        f'  <atom:link href="{SITE_URL}/feed.xml" rel="self" type="application/rss+xml"/>',
    ]
    if last:
        head.append(f"  <lastBuildDate>{_rfc822(last)}</lastBuildDate>")
    text = "\n".join(head + items + ["</channel>", "</rss>"]) + "\n"
    path = ROOT / "feed.xml"
    path.write_text(text, encoding="utf-8")
    return path


def patch_index_catalog(index_html: str, block: str) -> str:
    if CATALOG_MARK_BEGIN in index_html and CATALOG_MARK_END in index_html:
        pattern = re.compile(
            re.escape(CATALOG_MARK_BEGIN) + r".*?" + re.escape(CATALOG_MARK_END),
            re.DOTALL,
        )
        return pattern.sub(block, index_html, count=1)
    # First go-live: replace the original FNAMES / FIELDS / CAT declarations.
    pattern = re.compile(
        r"const FNAMES=\{.*?\};\nconst FIELDS=\[.*?\];\nconst CAT=\[.*?\];\n",
        re.DOTALL,
    )
    if not pattern.search(index_html):
        raise ValueError("Could not find FNAMES/FIELDS/CAT block in index.html")
    return pattern.sub(block + "\n", index_html, count=1)


def write_site_css() -> Path:
    """Emit the live site.css used by standalone article pages (index styles + r8)."""
    index_html = (ROOT / "index.html").read_text(encoding="utf-8")
    styles = re.findall(r"<style[^>]*>(.*?)</style>", index_html, re.S)
    css = "\n".join(styles)
    path = ROOT / "site.css"
    path.write_text(css, encoding="utf-8")
    return path


def main() -> None:
    parser = argparse.ArgumentParser(description="Build r8 go-live article pages and catalog")
    parser.add_argument("--dry-run", action="store_true", help="Preview without writing files")
    args = parser.parse_args()

    catalog = cat.load_catalog()
    published = cat.published_articles(catalog)
    hidden = [a for a in cat.articles(catalog) if a["id"] in set(cat.hidden_ids(catalog))]

    print(f"Published {len(published)} · hidden {len(hidden)}")
    if len(published) < 1:
        raise SystemExit("catalog has no published articles")

    bodies: dict[str, str] = {}
    generated = 0
    for article in published:
        body, inner, data = render_article_main(article)
        bodies[article["id"]] = body
        page = build_article_page(article, body, inner, data, article_end_html(article, published))
        dest = PAGES_DIR / f"{article['id']}.html"
        print(f"  {article['id']} -> {dest.relative_to(ROOT)}")
        if not args.dry_run:
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(page, encoding="utf-8")
        generated += 1

    for article in hidden:
        dest = PAGES_DIR / f"{article['id']}.html"
        print(f"  hidden stub {article['id']}")
        if not args.dry_run:
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(hidden_stub_page(article), encoding="utf-8")

    if args.dry_run:
        print(f"\nDry-run: would write {generated} article pages")
        return

    js_path = write_site_articles_js(catalog, bodies)
    pub_path = write_published_json(catalog)
    sm_path = write_sitemap([a["id"] for a in published])
    feed_path = write_feed(published)

    index_path = ROOT / "index.html"
    index_html = index_path.read_text(encoding="utf-8")
    deals_before = cat.extract_deals_block(index_html)
    patched = patch_index_catalog(index_html, catalog_js_block(catalog))
    deals_after = cat.extract_deals_block(patched)
    if deals_before != deals_after:
        raise SystemExit("Refusing to write index.html: DEALS block changed")
    index_path.write_text(patched, encoding="utf-8")
    css_path = write_site_css()

    print(f"\nGenerated {generated} article pages")
    print(f"  {js_path.relative_to(ROOT)}")
    print(f"  {pub_path.relative_to(ROOT)}")
    print(f"  {sm_path.relative_to(ROOT)}")
    print(f"  {feed_path.relative_to(ROOT)}")
    print(f"  {css_path.relative_to(ROOT)}")
    print("  index.html catalog patched (DEALS unchanged)")


if __name__ == "__main__":
    main()
