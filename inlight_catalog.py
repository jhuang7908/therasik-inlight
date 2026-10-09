#!/usr/bin/env python3
"""Catalog, field colours, visibility, and weekly-run protection for InLight.

The 25 r8 解读 articles are first-class curated/locked content. Hidden
articles (no full structured text, plus the two excluded ids) stay in
content/catalog.json and are never shown on index, field pages, feeds,
or the sitemap. A Sunday run must not overwrite curated text, revert
card metadata, or un-hide a hidden id.
"""

from __future__ import annotations

import html
import json
import re
from copy import deepcopy
from pathlib import Path

ROOT = Path(__file__).resolve().parent
CATALOG_PATH = ROOT / "content" / "catalog.json"
FIELDS_PATH = ROOT / "content" / "fields_colors.json"
ARTICLES_DIR = ROOT / "content" / "articles"
MANIFEST_PATH = ROOT / "content" / "r8_manifest.json"

SITE_URL = "https://inlight.therasik.com"

# Weekly Claude still emits the original c1–c9 codes. Map onto the r8
# nine-field taxonomy used on the live site after go-live.
WEEKLY_FIELD_MAP = {
    "c1": "类器官",
    "c2": "AI 药物设计",
    "c3": "肿瘤免疫与细胞治疗",
    "c4": "自身免疫与移植免疫",
    "c5": "疾病模型",
    "c6": "抗体工程",
    "c7": "肿瘤免疫与细胞治疗",
    "c8": "疫苗与感染免疫",
    "c9": "核酸与基因治疗（含 LNP 递送）",
}

# Stable keys for CSS / CAT.f. Do not reuse --c1..--c9 (those colours
# belong to the deals tracker and must stay byte-stable).
FIELD_KEYS = {
    "类器官": "organoid",
    "动物模型": "animal",
    "疾病模型": "animal",
    "AI 药物设计": "ai",
    "肿瘤免疫与细胞治疗": "immuno",
    "自身免疫与移植免疫": "autoimm",
    "疫苗与感染免疫": "vaccine",
    "抗体工程": "antibody",
    "核酸与基因治疗（含 LNP 递送）": "nucleic",
    "核酸与基因治疗": "nucleic",
    "精准肿瘤与临床转化": "precision",
}

FIELD_BLURBS = {
    "类器官": "疾病建模、药物筛选、器官芯片",
    "动物模型": "人源化小鼠、基因编辑、PDX",
    "疾病模型": "人源化小鼠、基因编辑、PDX",
    "AI 药物设计": "结构预测、生成式蛋白设计、药效预测",
    "肿瘤免疫与细胞治疗": "检查点、微环境、CAR-T、新抗原",
    "自身免疫与移植免疫": "狼疮、类风湿、移植免疫",
    "疫苗与感染免疫": "mRNA 疫苗、佐剂、感染免疫",
    "抗体工程": "双抗、ADC、纳米抗体、Fc 改造",
    "核酸与基因治疗（含 LNP 递送）": "siRNA、ASO、LNP 递送",
    "精准肿瘤与临床转化": "患者来源模型、临床转化",
}

EXCLUDED_IDS = ("c7-cell-5", "c5-am-6")
STATUS_PUBLISHED = "published"
STATUS_NO_FULLTEXT = "no_fulltext"
STATUS_EXCLUDED = "excluded"
STATUS_WEEKLY = "weekly"


def load_fields() -> list[dict]:
    data = json.loads(FIELDS_PATH.read_text(encoding="utf-8"))
    rows = []
    for item in data["fields"]:
        rows.append({
            "k": FIELD_KEYS[item["field"]],
            "field": item["field"],
            "n": item["label"],
            "color": item["color"],
            "d": FIELD_BLURBS.get(item["field"], ""),
            "contrast_vs_white": item.get("contrast_vs_white"),
        })
    return rows


def field_by_name() -> dict[str, dict]:
    return {f["field"]: f for f in load_fields()}


def field_by_key() -> dict[str, dict]:
    return {f["k"]: f for f in load_fields()}


def field_record(name: str) -> dict:
    if name == "动物模型":
        name = "疾病模型"
    fields = field_by_name()
    if name in fields:
        return fields[name]
    # label-only match (核酸与基因治疗)
    for f in fields.values():
        if f["n"] == name or f["field"] == name:
            return f
    raise KeyError(f"unknown field: {name}")


def load_catalog() -> dict:
    if not CATALOG_PATH.exists():
        raise FileNotFoundError(f"missing {CATALOG_PATH}")
    return json.loads(CATALOG_PATH.read_text(encoding="utf-8"))


def save_catalog(catalog: dict) -> None:
    CATALOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    CATALOG_PATH.write_text(
        json.dumps(catalog, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def articles(catalog: dict | None = None) -> list[dict]:
    cat = catalog if catalog is not None else load_catalog()
    return list(cat.get("articles") or [])


def by_id(catalog: dict | None = None) -> dict[str, dict]:
    return {a["id"]: a for a in articles(catalog) if a.get("id")}


def published_ids(catalog: dict | None = None) -> list[str]:
    return [a["id"] for a in articles(catalog) if a.get("status") == STATUS_PUBLISHED]


def hidden_ids(catalog: dict | None = None) -> list[str]:
    return [
        a["id"]
        for a in articles(catalog)
        if a.get("status") in {STATUS_NO_FULLTEXT, STATUS_EXCLUDED}
    ]


def locked_ids(catalog: dict | None = None) -> list[str]:
    return [a["id"] for a in articles(catalog) if a.get("locked")]


def curated_ids(catalog: dict | None = None) -> list[str]:
    return [a["id"] for a in articles(catalog) if a.get("curated")]


def published_articles(catalog: dict | None = None) -> list[dict]:
    order = (catalog or load_catalog()).get("published_order") or published_ids(catalog)
    lookup = by_id(catalog)
    out = []
    for aid in order:
        a = lookup.get(aid)
        if a and a.get("status") == STATUS_PUBLISHED:
            out.append(a)
    for a in articles(catalog):
        if a.get("status") == STATUS_PUBLISHED and a["id"] not in {x["id"] for x in out}:
            out.append(a)
    return out


def is_public(article: dict) -> bool:
    return article.get("status") == STATUS_PUBLISHED


def article_json_path(article_id: str) -> Path:
    return ARTICLES_DIR / f"{article_id}.json"


def article_md_path(article_id: str) -> Path:
    return ARTICLES_DIR / f"{article_id}.md"


def load_article_bundle(article_id: str) -> tuple[dict, str]:
    jpath = article_json_path(article_id)
    mpath = article_md_path(article_id)
    if not jpath.exists() or not mpath.exists():
        raise FileNotFoundError(f"missing structured files for {article_id}")
    data = json.loads(jpath.read_text(encoding="utf-8"))
    md = mpath.read_text(encoding="utf-8")
    return data, md


def map_weekly_field(code: str) -> str:
    return WEEKLY_FIELD_MAP.get(code, code)


def card_cover_src(article: dict) -> str:
    """Cover path for a catalog / weekly card.

    Curated r8 rows use card_img. Weekly rows still carry the picture in
    ``img`` (same as main: ``a.img || img/papers/<id>.jpg``). The webp
    default is last so a Sunday piece does not point at a missing
    ``<id>_card.webp``.
    """
    aid = article.get("id") or ""
    return (
        article.get("card_img")
        or article.get("img")
        or (f"img/papers/{aid}.jpg?v=5" if aid else "")
        or (f"img/{aid}_card.webp" if aid else "")
    )


def normalize_article_fields(row: dict) -> dict:
    """Ensure f is an r8 field key and colour/label are filled.

    Weekly writers may set ``field`` (Chinese name) while leaving ``f`` as
    an old c1–c9 code, or set only ``f``. Either way the live CAT / field
    sections need the nine-field key + hex.
    """
    out = dict(row)
    f = out.get("f")
    field = out.get("field")
    rec = None
    if field:
        try:
            rec = field_record(field)
        except KeyError:
            rec = None
    if rec is None and f in WEEKLY_FIELD_MAP:
        rec = field_record(WEEKLY_FIELD_MAP[f])
    if rec is None and f:
        rec = field_by_key().get(f)
    if rec:
        out["f"] = rec["k"]
        out["field"] = rec["field"]
        out["field_label"] = rec["n"]
        out["field_color"] = rec["color"]
        tags = out.get("tags")
        if not tags or tags == [f] or (isinstance(tags, list) and tags and tags[0] in WEEKLY_FIELD_MAP):
            out["tags"] = [rec["k"]]
    return out


def public_article_urls(catalog: dict | None = None) -> set[str]:
    urls: set[str] = set()
    for a in articles(catalog):
        url = (a.get("url") or "").strip()
        if url and not url.startswith("#"):
            urls.add(url)
        legacy = a.get("legacy") or {}
        lurl = (legacy.get("url") or "").strip()
        if lurl and not lurl.startswith("#"):
            urls.add(lurl)
    return urls


def protect_latest_payload(previous: dict, incoming: dict, catalog: dict | None = None) -> dict:
    """Merge a weekly latest.json write without touching locked/hidden rows.

    - Incoming rows whose id is locked are dropped (keep the previous copy).
    - Incoming rows whose id is hidden are dropped (stay hidden).
    - Previous locked curated rows are always kept, verbatim.
    - Hidden ids never appear in the public articles list.
    - Deals are passed through unchanged.
    """
    cat = catalog if catalog is not None else load_catalog()
    locked = set(locked_ids(cat))
    hidden = set(hidden_ids(cat))
    curated = set(curated_ids(cat))

    prev_articles = list(previous.get("articles") or [])
    new_articles = list(incoming.get("articles") or [])
    prev_by_id = {a.get("id"): a for a in prev_articles if a.get("id")}

    merged: list[dict] = []
    seen: set[str] = set()

    # Locked / curated rows from the catalog snapshot win over anything
    # the weekly writer just produced.
    for a in articles(cat):
        if a.get("id") in locked or a.get("curated"):
            if a.get("status") == STATUS_PUBLISHED:
                public = _public_card(a)
                merged.append(public)
                seen.add(a["id"])
            else:
                seen.add(a["id"])

    for a in new_articles:
        aid = a.get("id")
        if not aid or aid in seen:
            continue
        if aid in hidden or a.get("status") in {STATUS_NO_FULLTEXT, STATUS_EXCLUDED}:
            continue
        if aid in locked or aid in curated:
            continue
        row = normalize_article_fields(a)
        row.setdefault("curated", False)
        row.setdefault("locked", False)
        row.setdefault("status", STATUS_WEEKLY)
        merged.append(row)
        seen.add(aid)

    for a in prev_articles:
        aid = a.get("id")
        if not aid or aid in seen:
            continue
        if aid in hidden:
            continue
        if a.get("status") in {STATUS_NO_FULLTEXT, STATUS_EXCLUDED}:
            continue
        merged.append(normalize_article_fields(a))
        seen.add(aid)

    return {
        "generated": incoming.get("generated") or previous.get("generated"),
        "articles": merged[:80],
        "deals": list(incoming.get("deals") or previous.get("deals") or []),
    }


def filter_public_rows(rows: list[dict], catalog: dict | None = None) -> list[dict]:
    hidden = set(hidden_ids(catalog))
    out = []
    for a in rows:
        aid = a.get("id")
        if not aid or aid in hidden:
            continue
        if a.get("status") in {STATUS_NO_FULLTEXT, STATUS_EXCLUDED}:
            continue
        out.append(a)
    return out


def _public_card(a: dict) -> dict:
    """Compact card record used on the site and in latest.json."""
    rec = field_record(a["field"]) if a.get("field") else field_by_key().get(a.get("f") or "", {})
    related = []
    for rel in a.get("related") or []:
        if isinstance(rel, dict):
            related.append({
                "field": rel.get("field"),
                "label": rel.get("label") or rel.get("field"),
                "color": rel.get("color") or field_record(rel["field"])["color"],
            })
        else:
            fr = field_record(rel)
            related.append({"field": fr["field"], "label": fr["n"], "color": fr["color"]})
    card = {
        "id": a["id"],
        "f": a.get("f") or rec.get("k"),
        "field": a.get("field") or rec.get("field"),
        "field_label": a.get("field_label") or rec.get("n"),
        "field_color": a.get("field_color") or rec.get("color"),
        "t": a.get("title") or a.get("t"),
        "card_title": a.get("card_title") or a.get("t") or a.get("title"),
        "card_caption": a.get("card_caption") or "",
        "cover_caption": a.get("cover_caption") or "",
        "ds": a.get("ds") or "",
        "disp": a.get("disp") or "",
        "j": a.get("j") or a.get("journal") or "",
        "url": a.get("url") or "",
        "au": a.get("au") or "",
        "tags": [a.get("f") or rec.get("k")] if (a.get("f") or rec.get("k")) else [],
        "related": related,
        "preprint": bool(a.get("preprint")),
        "sum": a.get("sum") or a.get("one_liner") or a.get("card_caption") or "",
        "card_img": a.get("card_image") or f"img/{a['id']}_card.webp",
        "mech_img": a.get("mech_image") or f"img/{a['id']}_mech.webp",
        "curated": True,
        "locked": True,
        "status": STATUS_PUBLISHED,
        "deep": a["id"],
    }
    return card


def journal_of(citation: str, fallback: str = "") -> str:
    if not citation:
        return fallback
    m = re.search(r"\.\s([A-Z][A-Za-z .&-]+?)\.\s(20\d\d)\.", citation)
    if m:
        return m.group(1).strip()
    return fallback


def doi_of(citation: str, fallback: str = "") -> str:
    if citation:
        m = re.search(r"https?://doi\.org/\S+", citation)
        if m:
            return m.group(0).rstrip(").,;")
    if fallback.startswith("http"):
        return fallback
    return fallback


def inline_markup(s: str) -> str:
    s = html.escape(s, quote=False)
    s = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", s)
    s = re.sub(
        r"\[([^\]]+)\]\((https?://[^)\s]+)\)",
        r'<a href="\2" target="_blank" rel="noopener">\1</a>',
        s,
    )
    s = re.sub(
        r'(?<![">])(https?://[^\s<，。；）)]+)',
        r'<a href="\1" target="_blank" rel="noopener">\1</a>',
        s,
    )
    return s


def render_structured_md(md: str, article_id: str, cover_caption: str) -> str:
    """Render r8 markdown to the live article body (hero + sections)."""
    lines = md.split("\n")
    out: list[str] = []
    sec_open = False
    lst = None
    card_done = False
    in_datacard = False

    def close_list() -> None:
        nonlocal lst
        if lst:
            out.append("</ul>")
            lst = None

    def close_sec() -> None:
        nonlocal sec_open, in_datacard
        close_list()
        if sec_open:
            out.append("</div>")
            sec_open = False
            in_datacard = False

    for line in lines:
        if line.startswith("# "):
            continue
        if line.startswith("^^ "):
            continue
        m = re.match(r"!\[(.*?)\]\((.*?)\)", line)
        if m:
            close_sec()
            src = m.group(2)
            if "card" in src and not card_done:
                cap = html.escape(cover_caption)
                out.append(
                    f'<div class="pv-hero"><img src="img/{article_id}_card.webp" width="1600" height="1000" alt="封面示意图">'
                    f'<p class="cap">{cap}</p></div>'
                )
                card_done = True
            else:
                out.append(
                    f'<div class="fig paperfig"><div class="fig-scroll">'
                    f'<img src="img/{article_id}_mech.webp" alt="机制示意图" loading="lazy"></div>'
                )
                out.append("__FIGCAP__")
            continue
        if line.startswith("> "):
            cap = f'<p class="cap">{inline_markup(line[2:])}</p>'
            if out and out[-1] == "__FIGCAP__":
                out[-1] = cap + "</div>"
            else:
                close_list()
                out.append(cap)
            continue
        if out and out[-1] == "__FIGCAP__":
            out[-1] = "</div>"
        if line.startswith("## "):
            close_sec()
            t = line[3:].strip()
            if t == "关键数据卡":
                out.append('<div class="pv-card"><h2>关键数据卡</h2>')
                in_datacard = True
            else:
                out.append(f'<div class="sec"><h2>{inline_markup(t)}</h2>')
            sec_open = True
            continue
        if line.startswith("### "):
            close_list()
            out.append(f"<h3>{inline_markup(line[4:])}</h3>")
            continue
        if re.match(r"^(- |\* |\d+\. )", line):
            if not lst:
                out.append("<ul>")
                lst = "ul"
            out.append("<li>" + inline_markup(re.sub(r"^(- |\* |\d+\. )", "", line)) + "</li>")
            continue
        if not line.strip() or line.strip() in ("---", "***"):
            close_list()
            continue
        close_list()
        if in_datacard:
            close_sec()
        if not sec_open:
            out.append('<div class="sec">')
            sec_open = True
        out.append(f"<p>{inline_markup(line)}</p>")
    if out and out[-1] == "__FIGCAP__":
        out[-1] = "</div>"
    close_sec()
    if not card_done:
        raise AssertionError(f"structured markdown for {article_id} has no card image")
    return "\n".join(out)


def chips_html(article: dict, header: bool = False) -> str:
    rec = field_record(article["field"]) if article.get("field") else field_by_key()[article["f"]]
    parts = [
        f'<span class="pv-chip pri" style="--fc:{rec["color"]}">{html.escape(rec["n"])}</span>'
    ]
    for rel in article.get("related") or []:
        if isinstance(rel, dict):
            color = rel.get("color") or field_record(rel["field"])["color"]
            label = rel.get("label") or field_record(rel["field"])["n"]
        else:
            fr = field_record(rel)
            color, label = fr["color"], fr["n"]
        parts.append(f'<span class="pv-chip rel" style="--fc:{color}">{html.escape(label)}</span>')
    if article.get("preprint"):
        parts.append('<span class="pv-chip pre">预印本</span>')
    return f'<div class="pv-chips">{"".join(parts)}</div>'


def extract_cat_array(index_html: str) -> list[dict]:
    """Extract article objects from the live CAT array (legacy + go-live)."""
    json_cat = re.search(r"const CAT=(\[.*?\]);\n/\* @@INLIGHT_CATALOG_END \*/", index_html, re.DOTALL)
    if json_cat:
        return json.loads(json_cat.group(1))
    found: list[dict] = []
    pattern = re.compile(
        r"\{id:'([^']+)',"
        r"f:'([^']+)',"
        r"t:'([^']+)',"
        r"ds:'([^']+)',"
        r"disp:'([^']+)',"
        r"j:'([^']+)',"
        r"url:'([^']+)',"
        r"au:'([^']+)',"
        r"tags:\[([^\]]*)\]",
        re.DOTALL,
    )
    for match in pattern.finditer(index_html):
        article_id, field, title, date_str, disp, journal, url, author, tags_str = match.groups()
        block_end = index_html.find("\n {id:", match.end())
        if block_end == -1:
            block_end = index_html.find("\n];", match.end())
        block = index_html[match.start():block_end if block_end != -1 else match.end() + 400]
        tags = [t.strip().strip("'") for t in tags_str.split(",") if t.strip()]
        sum_m = re.search(r"sum:'((?:\\'|[^'])*)'", block)
        note_m = re.search(r"note:'((?:\\'|[^'])*)'", block)
        deep_m = re.search(r"deep:'([^']+)'", block)
        sec = ",sec:1" in block or "sec:1" in block
        found.append({
            "id": article_id,
            "f": field,
            "t": title.replace("\\'", "'"),
            "ds": date_str,
            "disp": disp,
            "j": journal,
            "url": url,
            "au": author.replace("\\'", "'"),
            "tags": tags,
            "sum": (sum_m.group(1).replace("\\'", "'") if sum_m else ""),
            "note": (note_m.group(1).replace("\\'", "'") if note_m else ""),
            "deep": deep_m.group(1) if deep_m else "",
            "sec": 1 if sec else 0,
        })
    return found


def extract_deals_block(index_html: str) -> str:
    start = index_html.find("const DEALS=[")
    if start < 0:
        raise ValueError("DEALS array not found")
    end = index_html.find("\n];", start)
    if end < 0:
        raise ValueError("DEALS array terminator not found")
    return index_html[start:end + 3]


def count_deals(deals_block: str) -> int:
    return len(re.findall(r"\n \{d:'", deals_block))
