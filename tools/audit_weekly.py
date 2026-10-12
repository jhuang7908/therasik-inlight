#!/usr/bin/env python3
"""发布前强制审计（前沿追踪 / InLight 中文站）。

对照 docs/PUBLISH_CHECKLIST.md，自动检查一周产出和构建好的站点；任何 FAIL 时退出码非零。

    python tools/audit_weekly.py --base origin/main                # 发布前：与上一版已发布内容比对
    python tools/audit_weekly.py --base origin/main --week preview/weekly/2026-10-18
    python tools/audit_weekly.py --offline                         # 不联网（跳过外链可达性）
    python tools/audit_weekly.py --write-baseline                  # 校准：把现有内容的历史问题写入基线
    python tools/audit_weekly.py --live https://inlight.therasik.com --base HEAD~1   # 上线后核验

状态：PASS / FAIL（拦发布）/ WARN（需人工确认，--strict 时视为 FAIL）/ LEGACY（基线里登记过的历史问题，
不拦发布，但只对旧条目生效；新条目永远不进基线）/ MANUAL（只能人工核对的项，报告里列出待签字）。
报告写到 preview/audit/<时间>/audit_report.{md,json}（preview/ 不进 git）。
"""

from __future__ import annotations

import argparse
import colorsys
import concurrent.futures as cf
import glob
import hashlib
import json
import math
import re
import shutil
import subprocess
import sys
import tempfile
import unicodedata
import warnings
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

warnings.filterwarnings("ignore", category=DeprecationWarning)
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# ------------------------------------------------------------------ rule tables

# 自说自话 / 编辑过程 / 版权自辩 / 页面版式解释 / 处理说明（AUTOMATION.md「写作规则」+ 用户规则 + run_weekly 提示词）
BANNED_PHRASES: list[tuple[str, str]] = [
    (r"用户提供(?:的)?全文", "编辑过程：用户提供全文"),
    (r"本文不补", "编辑过程：本文不补"),
    (r"两处均保留", "编辑过程：两处均保留"),
    (r"Supplementary\s*(?:未读|没读|未看)", "编辑过程：Supplementary 未读"),
    (r"(?:附录|补充材料)(?:未读|没读)", "编辑过程：补充材料未读"),
    (r"摘录未给出", "编辑过程：摘录未给出"),
    (r"未读原文|二手核实|二手：", "非全文条目标记（只发全文）"),
    (r"抓取(?:失败|情况|不到|到的)|资料收集", "抓取/资料收集说明"),
    (r"未核到|单位来自作者栏", "核对过程元注释"),
    (r"编辑备注|内部稿|占位(?:符|文字|图|内容)|（占位）|\[占位\]|\bMVP\b|对齐\s*ACIR|BioRender", "内部/流程用语"),
    (r"许可为\s*CC|CC[ -]?BY[^。]{0,20}改写|正文为改写|未逐句抄录|逐句抄录", "许可/版权自辩"),
    (r"本文按|口径冲突|不作换算|本文采用.{0,6}口径", "数字处理说明（应写“各来源数字不一：A 报 X，B 报 Y。”）"),
    (r"本页列出|下方卡片|上方卡片|点击(?:下方|这里|卡片|查看)|如下表所示|见下方", "解释页面版式"),
    (r"前沿追踪即为|样例|自我介绍", "自我介绍"),
    (r"保持独立|我们独立于|不受赞助影响", "为自身独立性辩护"),
    (r"InSynBio|论文写作|智能中转服务|栏位可用于赞助|启曜生科", "商业块/旧品牌"),
]
SLANG = r"yyds|绝绝子|炸裂|王炸|封神|吊打|碾压|秒杀|天花板|硬核|杀疯|爆款|干货满满|宝藏|太香了|狠狠|拿捏|遥遥领先"
HYPE_TITLE = r"重磅|颠覆|突破性?|里程碑式|划时代"
BOILERPLATE = r"原文未报告|原文未给出|未给出"
DISCLAIMERS = {
    "ai_figure": r"由\s*AI\s*生成",
    "not_advice": r"不构成投资建议",
    "check_record": r"核对记录",
}
BRAND_RE = re.compile(r"TheraSik|Therasik|THERASIK|TheraSIK")  # 品牌写法；小写域名 therasik.com 允许
DEAL_KINDS = {"acq", "lic", "newco", "clin", "inv", "policy"}
FILING_DOMAINS = ("sec.gov", "hkexnews.hk", "cninfo.com.cn", "sse.com.cn", "szse.cn")
NEWCO_TEXT = r"NewCo|新设公司|新公司|新成立|成立.{0,8}(?:公司|平台)|装入|借壳|反向合并|launch(?:es|ed)? with|newly formed"
NEWCO_EQUITY = r"股权|持股|股份|equity|A 轮|A轮|Series A|融资"
ACADEMIC_PER_WEEK = (4, 5)
SITE_GREEN_HUE = (0.25, 0.5)
WARM_OFFWHITE = (0xF7, 0xF4, 0xEA)

REQUIRED_MD_SECTIONS = ["关键数据卡", "研究背景与待解问题", "研究设计", "核心结果", "机制解读", "局限与不确定", "作者、出处与核对"]
CSS_REQUIRED = [
    (r"\.pv-art \.sec p\{[^}]*text-align:justify[^}]*text-indent:2em", "文章正文段落两端对齐 + 首行缩进两字"),
    (r"\.feed-details-panel p:not\(\.msline\)\{[^}]*text-align:justify[^}]*text-indent:2em", "经济动态详情正文两端对齐 + 缩进"),
    (r"\.textpage \.panel p\{[^}]*text-align:justify[^}]*text-indent:2em", "文字页正文两端对齐 + 缩进"),
    (r"\.art-end p,\.art-end li\{[^}]*text-indent:0", "文末区块不缩进"),
]

STATUS_ORDER = ["FAIL", "WARN", "LEGACY", "MANUAL", "PASS"]


@dataclass
class Finding:
    check: str
    section: str
    item: str
    status: str
    msg: str = ""
    new: bool = False


@dataclass
class Ctx:
    root: Path
    base: str | None
    week: Path | None
    online: bool
    strict: bool
    findings: list[Finding] = field(default_factory=list)
    checks_run: Counter = field(default_factory=Counter)

    def add(self, check, section, item, status, msg="", new=False):
        self.checks_run[check] += 1
        self.findings.append(Finding(check, section, item, status, msg, new))

    def ok(self, check, section, item="*", new=False):
        self.checks_run[check] += 1


# ------------------------------------------------------------------ helpers

def han_count(s: str) -> int:
    return sum(1 for ch in s if "\u4e00" <= ch <= "\u9fff")


def numbers_in(s: str) -> list[str]:
    s = s.replace("，", ",")
    out = []
    for m in re.finditer(r"(?<![A-Za-z\d])(\d{1,3}(?:,\d{3})+|\d+(?:\.\d+)?)", s):
        v = m.group(1).replace(",", "")
        if len(v) == 1:
            continue  # 单个数字太常见（分组、步骤号），不做溯源
        out.append(v)
    return out


def norm_url(u: str) -> str:
    u = (u or "").strip().lower()
    u = re.sub(r"[?#].*$", "", u).rstrip("/")
    m = re.match(r"https?://(?:dx\.)?doi\.org/(10\.\d+/.+)", u)
    if m:
        return "doi:" + m.group(1)
    m = re.match(r"https?://(?:www\.)?nature\.com/articles/(s\d+-\d+-\d+-\w+)", u)
    if m:
        return "doi:10.1038/" + m.group(1)
    return re.sub(r"^https?://(www\d?\.)?", "", u)


def norm_title(t: str) -> str:
    t = unicodedata.normalize("NFKC", t or "").lower()
    return re.sub(r"[\s\W_]+", "", t)


def git_show(root: Path, ref: str, path: str) -> str | None:
    try:
        return subprocess.run(["git", "-C", str(root), "show", f"{ref}:{path}"], capture_output=True,
                              check=True).stdout.decode("utf-8")
    except subprocess.CalledProcessError:
        return None


def parse_js_obj(src: str) -> dict:
    """Parse one DEALS entry ({d:'..',kinds:['a','b'],t:'..'}) without eval."""
    obj = {}
    for m in re.finditer(r"(\w+):(?:'((?:[^'\\]|\\.)*)'|\[([^\]]*)\]|(true|false|null|-?\d+(?:\.\d+)?))", src):
        key, sval, arr, lit = m.groups()
        if sval is not None:
            obj[key] = sval.encode("utf-8").decode("unicode_escape").encode("latin-1").decode("utf-8") if "\\" in sval else sval
        elif arr is not None:
            obj[key] = re.findall(r"'((?:[^'\\]|\\.)*)'", arr)
        else:
            obj[key] = json.loads(lit)
    return obj


def parse_deals(index_html: str) -> list[dict]:
    start = index_html.find("const DEALS=[")
    if start < 0:
        return []
    end = index_html.find("\n];", start)
    block = index_html[start:end]
    rows = []
    for line in block.split("\n"):
        line = line.strip()
        if line.startswith("{d:'"):
            rows.append(parse_js_obj(line.rstrip(",")))
    return rows


def parse_deal_art(index_html: str) -> dict:
    m = re.search(r"const DEAL_ART_V7=(\{.*?\});", index_html, re.S)
    return json.loads(m.group(1)) if m else {}


# ------------------------------------------------------------------ site model

class Site:
    def __init__(self, root: Path, ref: str | None = None):
        self.root, self.ref = root, ref
        self.index = self._read("index.html") or ""
        cat_txt = self._read("content/catalog.json")
        self.catalog = json.loads(cat_txt) if cat_txt else {"articles": []}
        latest = self._read("content/latest.json")
        self.latest = json.loads(latest) if latest else {"articles": [], "deals": []}
        fields = self._read("content/fields_colors.json")
        self.fields = json.loads(fields)["fields"] if fields else []
        self.deals = parse_deals(self.index)
        self.deal_art = parse_deal_art(self.index)

    def _read(self, rel: str) -> str | None:
        if self.ref:
            return git_show(self.root, self.ref, rel)
        p = self.root / rel
        return p.read_text(encoding="utf-8") if p.exists() else None

    def published(self) -> list[dict]:
        return [a for a in self.catalog.get("articles", []) if a.get("status") == "published"]

    def article_md(self, aid: str) -> str:
        return self._read(f"content/articles/{aid}.md") or ""

    def article_json(self, aid: str) -> dict:
        t = self._read(f"content/articles/{aid}.json")
        return json.loads(t) if t else {}

    def page(self, aid: str) -> str:
        return self._read(f"pages/article/{aid}.html") or ""

    def weekly_articles(self) -> list[dict]:
        return list(self.latest.get("articles") or [])

    def weekly_deals(self) -> list[dict]:
        return list(self.latest.get("deals") or [])


def deal_key(d: dict) -> str:
    return d.get("url") or ("t:" + d.get("t", ""))


# ------------------------------------------------------------------ image metrics

def image_metrics(path: Path) -> dict | None:
    try:
        from PIL import Image, ImageFilter
    except ImportError:
        return None
    im = Image.open(path)
    fmt = im.format
    rgb = im.convert("RGB")
    w, h = rgb.size
    small = rgb.resize((160, max(1, int(160 * h / w))))
    px = list(small.getdata())
    n = len(px)
    lab = list(small.convert("LAB").getdata())
    L = sum(p[0] for p in lab) / n * 100 / 255
    a = sum(p[1] - 128 for p in lab) / n
    b = sum(p[2] - 128 for p in lab) / n
    red = green = blue = 0
    for r, g, bb in px:
        H, S, V = colorsys.rgb_to_hsv(r / 255, g / 255, bb / 255)
        if S > 0.35 and V > 0.3 and (H < 0.06 or H > 0.94):
            red += 1
        elif S > 0.15 and 0.2 < H < 0.5:
            green += 1
        elif S > 0.15 and 0.5 < H < 0.72:
            blue += 1
    corners = [rgb.getpixel(xy) for xy in ((4, 4), (w - 5, 4), (4, h - 5), (w - 5, h - 5))]
    corner = tuple(sorted(c[i] for c in corners)[1] for i in range(3))  # robust-ish
    grains = []
    gray = rgb.convert("L")
    for x0, y0 in ((8, 8), (w - 72, 8), (8, h - 72), (w - 72, h - 72)):
        patch = gray.crop((x0, y0, x0 + 64, y0 + 64))
        blur = patch.filter(ImageFilter.BoxBlur(2))
        diff = [abs(p - q) for p, q in zip(patch.getdata(), blur.getdata())]
        mean = sum(diff) / len(diff)
        grains.append(mean)
    return {"size": [w, h], "format": fmt, "L": L, "a": a, "b": b, "red": red / n, "green": green / n,
            "blue": blue / n, "corner": list(corner), "grain": min(grains)}


def delta_e(c1, c2) -> float:
    def to_lab(c):
        def lin(v):
            v /= 255
            return ((v + 0.055) / 1.055) ** 2.4 if v > 0.04045 else v / 12.92
        r, g, b = (lin(x) for x in c)
        X = (0.4124 * r + 0.3576 * g + 0.1805 * b) / 0.95047
        Y = 0.2126 * r + 0.7152 * g + 0.0722 * b
        Z = (0.0193 * r + 0.1192 * g + 0.9505 * b) / 1.08883
        f = lambda t: t ** (1 / 3) if t > 0.008856 else 7.787 * t + 16 / 116
        return 116 * f(Y) - 16, 500 * (f(X) - f(Y)), 200 * (f(Y) - f(Z))
    l1, l2 = to_lab(c1), to_lab(c2)
    return math.dist(l1, l2)


def ocr_chars(path: Path) -> int | None:
    if not shutil.which("tesseract"):
        return None
    try:
        out = subprocess.run(["tesseract", str(path), "-", "-l", "chi_sim+eng", "--psm", "11", "tsv"],
                             capture_output=True, timeout=60).stdout.decode("utf-8", "ignore")
    except Exception:
        return None
    n = 0
    for line in out.splitlines()[1:]:
        cols = line.split("\t")
        if len(cols) == 12 and cols[11].strip():
            try:
                conf = float(cols[10])
            except ValueError:
                continue
            txt = re.sub(r"[^0-9A-Za-z\u4e00-\u9fff]", "", cols[11])
            if conf >= 80 and len(txt) >= 2:
                n += len(txt)
    return n


# ------------------------------------------------------------------ checks

_SOURCE_AUDIT: dict[str, str] | None = None


def source_audit_numbers(site: "Site", aid: str) -> set[str]:
    """Numbers recorded as MATCH for this article in docs/source_audit.md (原文核对记录)."""
    global _SOURCE_AUDIT
    if _SOURCE_AUDIT is None:
        txt = site._read("docs/source_audit.md") or ""
        _SOURCE_AUDIT = {}
        for part in re.split(r"^### ", txt, flags=re.M)[1:]:
            _SOURCE_AUDIT[part.split(" ", 1)[0].strip()] = part
    sec = _SOURCE_AUDIT.get(aid, "")
    out = set()
    for line in sec.splitlines():
        cols = [c.strip() for c in line.split("|")]
        if len(cols) >= 6 and cols[-2] == "MATCH":
            out.update(numbers_in(cols[1]) + [cols[1].replace(",", "")])
    return out


def check_text_rules(ctx: Ctx, item: str, text: str, new: bool, sec="文风与禁用语"):
    hit = False
    for pat, label in BANNED_PHRASES:
        m = re.search(pat, text)
        if m:
            hit = True
            ctx.add("C1-禁用语", sec, item, "FAIL", f"{label}：…{text[max(0, m.start()-12):m.end()+12]}…", new)
    m = re.search(SLANG, text)
    if m:
        hit = True
        ctx.add("C5-口语自评", sec, item, "FAIL", f"口语/俚语：{m.group(0)}", new)
    if BRAND_RE.search(text):
        hit = True
        ctx.add("C7-TheraSik", sec, item, "FAIL", "出现 TheraSik 品牌写法", new)
    if not hit:
        ctx.ok("C1-禁用语", sec, item, new)


def check_articles(ctx: Ctx, site: Site, ref: Site, new_ids: set[str]):
    vocab = {f["field"]: f for f in site.fields}
    labels = {f["label"]: f for f in site.fields}
    ref_pub = ref.published()
    ref_keys = Counter(k for a in ref_pub for k in a.keys())
    core_keys = {k for k, c in ref_keys.items() if c >= max(1, int(0.9 * len(ref_pub)))}
    dist = defaultdict(list)
    for a in ref_pub:
        dist["title"].append(len(a.get("title", "")))
        dist["card_title"].append(len(a.get("card_title", "")))
        dist["one_liner"].append(len(a.get("one_liner") or a.get("sum") or ""))
        dist["author_intro"].append(len(a.get("author_intro") or ""))
    try:
        import inlight_catalog as cat
        field_keys = cat.FIELD_KEYS
    except Exception:
        field_keys = {}

    for a in site.published():
        aid = a["id"]
        new = aid in new_ids
        md = site.article_md(aid)
        page = site.page(aid)
        js = site.article_json(aid)
        fields_text = "\n".join(str(a.get(k) or "") for k in
                                ("title", "card_title", "sum", "one_liner", "quick_look", "author_intro", "card_caption", "cover_caption"))
        # --- 来源与事实
        url = a.get("url") or ""
        if not url.startswith("http"):
            ctx.add("A1-来源链接", "来源与事实", aid, "FAIL", "缺少原文链接 url", new)
        else:
            ctx.ok("A1-来源链接", "来源与事实", aid, new)
        card = re.search(r"\*\*证据等级\*\*：([^\n]+)", md)
        if not md:
            ctx.add("A2-全文准入", "来源与事实", aid, "FAIL", "缺少 content/articles/<id>.md 正文", new)
        elif not card or "全文" not in card.group(1):
            ctx.add("A2-全文准入", "来源与事实", aid, "FAIL", f"证据等级不是全文：{card.group(1) if card else '未写'}", new)
        else:
            ctx.ok("A2-全文准入", "来源与事实", aid, new)
        if not re.search(r"\*\*核对记录\*\*：", md):
            ctx.add("A3-核对记录", "来源与事实", aid, "FAIL", "关键数据卡缺“核对记录”", new)
        # --- 数字核对：卡片/导语/速览中的数字必须出现在正文
        body_nums = set(numbers_in(md))
        missing = []
        for key in ("title", "card_title", "one_liner", "quick_look"):
            for v in numbers_in(str(a.get(key) or "")):
                if v not in body_nums:
                    missing.append(f"{key}:{v}")
        audited = source_audit_numbers(site, aid)
        hard = [x for x in missing if x.split(":", 1)[1] not in audited]
        soft = [x for x in missing if x.split(":", 1)[1] in audited]
        if hard:
            ctx.add("B1-数字与正文一致", "数字核对", aid, "FAIL", "以下数字在正文/数据卡中找不到，也没有原文核对记录：" + "、".join(sorted(set(hard))[:12]), new)
        if soft:
            ctx.add("B1-数字与正文一致", "数字核对", aid, "WARN", "以下数字已在 docs/source_audit.md 对原文核对，但正文/数据卡里没有（速览应只用正文数字）：" + "、".join(sorted(set(soft))[:12]), new)
        if missing:
            pass
        else:
            ctx.ok("B1-数字与正文一致", "数字核对", aid, new)
        ql = a.get("quick_look") or ""
        if re.search(r"百分之|零点", ql) or (ql and not re.search(r"\d", ql)):
            ctx.add("B2-阿拉伯数字", "数字核对", aid, "FAIL", "速览用汉字写数字或没有数字", new)
        # --- 文风
        check_text_rules(ctx, aid, md + "\n" + fields_text, new)
        n_bp = len(re.findall(BOILERPLATE, md))
        if n_bp > 2:
            ctx.add("C2-套话上限", "文风与禁用语", aid, "FAIL", f"“原文未报告/未给出”出现 {n_bp} 次（上限 2）", new)
        title = a.get("title") or ""
        if re.search(HYPE_TITLE, title):
            ctx.add("C3-标题禁用词", "文风与禁用语", aid, "FAIL", f"标题含夸张词：{title}", new)
        if "首次" in title or "首个" in title:
            ctx.add("C3-标题禁用词", "文风与禁用语", aid, "WARN", "标题含“首次/首个”：须原文明写", new)
        if page:
            for key, pat in DISCLAIMERS.items():
                n = len(re.findall(pat, re.sub(r"<[^>]+>", "", page)))
                if n > 1:
                    ctx.add("C4-声明只出现一次", "文风与禁用语", aid, "FAIL", f"页面中“{pat}”类说明出现 {n} 次", new)
        long_paras = [p for p in re.split(r"\n\s*\n", md) if not p.lstrip().startswith(("#", "-", "!", ">", "|", "1.", "**")) and han_count(p) > 150]
        if long_paras:
            ctx.add("D4-段落长度", "格式排版", aid, "WARN", f"{len(long_paras)} 段超过 150 字（手机可读性）", new)
        # --- 结构
        secs = re.findall(r"^## (.+)$", md, re.M)
        miss = [s for s in REQUIRED_MD_SECTIONS if not any(s in x for x in secs)]
        if md and miss:
            ctx.add("D3-小节结构", "格式排版", aid, "FAIL", "缺小节：" + "、".join(miss), new)
        # --- 排版：页面中正文 <p> 都在 .sec 内（才有两端对齐+缩进），图注 .cap 在图下紧贴
        if page:
            amain = page.split('<div class="amain">', 1)[-1].split('class="art-end"', 1)[0]
            stray = 0
            depth_html = re.sub(r'<div class="sec">.*?</div>', "", amain, flags=re.S)
            for m in re.finditer(r"<p(?: class=\"([^\"]*)\")?>", depth_html):
                if "cap" not in (m.group(1) or ""):
                    stray += 1
            if stray:
                ctx.add("D1-正文缩进对齐", "格式排版", aid, "FAIL", f"{stray} 个正文段落不在 .sec 内（不会两端对齐/缩进）", new)
            for m in re.finditer(r'<div class="fig paperfig">.*?</div>\s*</div>(.{0,40})', amain, re.S):
                nxt = m.group(1).lstrip()
                if not (nxt.startswith("<ul") or nxt.startswith('<p class="cap')):
                    ctx.add("D2-图注紧贴", "格式排版", aid, "FAIL", "机制图后不是紧跟步骤列表/图注", new)
        else:
            ctx.add("G1-文章页存在", "链接与资源", aid, "FAIL", "pages/article/<id>.html 不存在", new)
        # --- 分类
        fld = a.get("field")
        if fld not in vocab:
            ctx.add("F1-主领域", "分类标签", aid, "FAIL", f"主领域不在九领域词表：{fld}", new)
        else:
            exp = vocab[fld]
            if a.get("field_color") != exp["color"] or a.get("field_label") != exp["label"]:
                ctx.add("F1-主领域", "分类标签", aid, "FAIL", "field_label/field_color 与词表不一致", new)
            if field_keys and a.get("f") != field_keys.get(fld):
                ctx.add("F1-主领域", "分类标签", aid, "FAIL", f"f={a.get('f')} 与领域 {fld} 的代码 {field_keys.get(fld)} 不符", new)
        for r in a.get("related") or []:
            if r.get("field") not in vocab or r.get("field") == fld:
                ctx.add("F2-相关领域", "分类标签", aid, "FAIL", f"相关领域无效或与主领域重复：{r.get('field')}", new)
        art = js.get("article") or {}
        if art and art.get("field") and art.get("field") != fld:
            ctx.add("F1-主领域", "分类标签", aid, "FAIL", f"正文 JSON 领域 {art.get('field')} 与目录 {fld} 不一致", new)
        # --- 一致性：字段、日期、长度
        missing_keys = core_keys - set(a.keys())
        if missing_keys:
            ctx.add("H1-字段一致", "一致性比对", aid, "FAIL", "缺少已发布条目都有的字段：" + "、".join(sorted(missing_keys)), new)
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", a.get("ds") or "") or a.get("disp") != (a.get("ds") or "")[:7].replace("-", "."):
            ctx.add("H2-日期格式", "一致性比对", aid, "FAIL", f"ds/disp 格式不符：{a.get('ds')} / {a.get('disp')}", new)
        if len(a.get("card_title") or "") > 30:
            ctx.add("H3-长度", "一致性比对", aid, "FAIL", f"卡片标题 {len(a['card_title'])} 字 > 30", new)
        qh = han_count(ql)
        if not 100 <= qh <= 200:
            ctx.add("H3-长度", "一致性比对", aid, "FAIL", f"速览汉字 {qh}（要求 100–200）", new)
        if new:
            for key, vals in dist.items():
                if not vals:
                    continue
                v = len(str(a.get(key) or (a.get("sum") if key == "one_liner" else "") or ""))
                lo, hi = min(vals) * 0.85, max(vals) * 1.15
                if not lo <= v <= hi:
                    ctx.add("H3-长度", "一致性比对", aid, "WARN", f"{key} 长度 {v} 超出已发布范围 {min(vals)}–{max(vals)}", new)
        if a.get("preprint") and "bioRxiv" not in (a.get("j") or "") and "medRxiv" not in (a.get("j") or ""):
            ctx.add("F3-预印本标记", "分类标签", aid, "WARN", "标了预印本但期刊不是 bioRxiv/medRxiv", new)


def image_groups(site: Site) -> dict[str, list[tuple[str, Path]]]:
    groups: dict[str, list[tuple[str, Path]]] = {"card": [], "mech": [], "deal": []}
    for a in site.published():
        aid = a["id"]
        c = a.get("card_image") or a.get("card_img")
        if c:
            groups["card"].append((aid, site.root / c.split("?")[0]))
        page = site.page(aid)
        for m in set(re.findall(r'src="\.\./\.\./(img/[\w./-]+_mech[\w-]*\.webp)"', page)):
            groups["mech"].append((aid, site.root / m))
    for d in site.deals:
        p = site.deal_art.get(d.get("t", ""))
        groups["deal"].append((deal_key(d), site.root / p if p else Path("__missing__")))
    return groups


def check_images(ctx: Ctx, site: Site, ref_groups: dict, new_items: set[str], week_imgs: list[tuple[str, Path, str, bool]]):
    try:
        import PIL  # noqa: F401
    except ImportError:
        ctx.add("E0-图像工具", "图片", "*", "FAIL", "未安装 Pillow，无法核对图片（pip install pillow）")
        return
    ref_metrics: dict[str, list[dict]] = {}
    cache: dict[Path, dict] = {}

    def met(p: Path):
        if p not in cache:
            cache[p] = image_metrics(p)
        return cache[p]

    for g, rows in ref_groups.items():
        ref_metrics[g] = [m for _, p in rows if p.exists() and (m := met(p))]
    cur = image_groups(site)
    todo = [(g, item, p, item in new_items) for g, rows in cur.items() for item, p in rows]
    todo += [(g, item, p, new) for item, p, g, new in week_imgs]
    for g, item, p, new in todo:
        if not p.exists():
            ctx.add("E1-图片存在", "图片", f"{item} [{g}]", "FAIL", f"图片不存在：{p.relative_to(site.root) if p.is_absolute() and site.root in p.parents else p}", new)
            continue
        m = met(p)
        refs = ref_metrics.get(g) or []
        if not refs:
            continue
        fmts = Counter(r["format"] for r in refs)
        if m["format"] not in fmts:
            ctx.add("E2-尺寸格式", "图片", f"{item} [{g}]", "FAIL", f"格式 {m['format']} 与已发布同类 {dict(fmts)} 不同", new)
        widths = {r["size"][0] for r in refs}
        heights = [r["size"][1] for r in refs]
        if m["size"][0] not in widths or not (min(heights) <= m["size"][1] <= max(heights)):
            ctx.add("E2-尺寸格式", "图片", f"{item} [{g}]", "FAIL", f"尺寸 {m['size']} 不在已发布范围（宽 {sorted(widths)}，高 {min(heights)}–{max(heights)}）", new)
        # 调色：均值 L*/a*/b* 在已发布范围内（各放宽 15% 区间）
        bad = []
        for k in ("L", "a", "b"):
            vals = [r[k] for r in refs]
            span = max(vals) - min(vals)
            lo, hi = min(vals) - 0.15 * span - 1, max(vals) + 0.15 * span + 1
            if not lo <= m[k] <= hi:
                bad.append(f"{k}*={m[k]:.1f}（范围 {min(vals):.1f}–{max(vals):.1f}）")
        if bad:
            ctx.add("E3-色调范围", "图片", f"{item} [{g}]", "FAIL", "；".join(bad), new)
        # 分区配色硬规则
        if g in ("card", "mech"):
            if m["blue"] > 0.01:
                ctx.add("E4-分区配色", "图片", f"{item} [{g}]", "FAIL", f"学术进展图含蓝色 {m['blue']:.1%}（应为绿 + 赤陶红）", new)
            if m["green"] < 0.01:
                ctx.add("E4-分区配色", "图片", f"{item} [{g}]", "FAIL", f"学术进展图几乎没有绿色（{m['green']:.1%}）", new)
            if m["red"] < 0.0005:
                ctx.add("E4-分区配色", "图片", f"{item} [{g}]", "WARN", "未检出赤陶红强调色", new)
        if g == "deal":
            if m["blue"] < 0.1:
                ctx.add("E4-分区配色", "图片", f"{item} [{g}]", "FAIL", f"经济动态图蓝色占比 {m['blue']:.1%} < 10%（应为 v4 蓝 + 红）", new)
            if m["green"] > 0.01:
                ctx.add("E4-分区配色", "图片", f"{item} [{g}]", "FAIL", f"经济动态图含绿色 {m['green']:.1%}", new)
        # 底色：卡片图为暖米白 #f7f4ea；其他组与已发布底色一致
        if g == "card":
            de = delta_e(m["corner"], WARM_OFFWHITE)
            if de > 6:
                ctx.add("E5-暖米白底", "图片", f"{item} [{g}]", "FAIL", f"四角底色 {tuple(m['corner'])} 与 #f7f4ea 色差 ΔE={de:.1f} > 6", new)
        else:
            des = [delta_e(r["corner"], WARM_OFFWHITE) for r in refs]
            de = delta_e(m["corner"], WARM_OFFWHITE)
            if de > max(des) + 2:
                ctx.add("E5-暖米白底", "图片", f"{item} [{g}]", "FAIL", f"底色 ΔE={de:.1f} 超出已发布最大 {max(des):.1f}", new)
            elif de > 6:
                ctx.add("E5-暖米白底", "图片", f"{item} [{g}]", "WARN", f"底色 {tuple(m['corner'])} 不是暖米白 #f7f4ea（ΔE={de:.1f}）", new)
        if g in ("card", "deal"):
            gs = sorted(r["grain"] for r in refs)
            floor = gs[len(gs) // 10] * 0.5
            if m["grain"] < floor:
                ctx.add("E6-颗粒质感", "图片", f"{item} [{g}]", "WARN", f"空白区颗粒度 {m['grain']:.2f} 低于已发布下限 {floor:.2f}（可能缺少纸纹颗粒）", new)
        if new and g in ("card", "deal"):
            n = ocr_chars(p)
            if n is None:
                ctx.add("E7-图内无字", "图片", f"{item} [{g}]", "MANUAL", "没有 tesseract，人工确认图内无文字", new)
            elif n >= 4:
                ctx.add("E7-图内无字", "图片", f"{item} [{g}]", "WARN", f"OCR 在图内识别到约 {n} 个字符，确认不是烘进图里的文字", new)


def check_deals(ctx: Ctx, site: Site, ref: Site, new_keys: set[str], candidates: dict[str, dict], check_order: bool = True):
    ref_keys = Counter(k for d in ref.deals for k in d.keys())
    core = {k for k, c in ref_keys.items() if c >= max(1, int(0.9 * len(ref.deals)))}
    dist = defaultdict(list)
    for d in ref.deals:
        for k in ("t", "m", "ms", "why"):
            dist[k].append(len(d.get(k) or ""))
    money_ok = re.compile(r"亿美元|万美元|亿元|万元|亿欧元|万欧元|未披露|无交易金额|美元|欧元")
    prev_d = "9999-99"
    for i, d in enumerate(site.deals):
        key = deal_key(d)
        new = key in new_keys
        item = d.get("t", key)[:40]
        text = " ".join(str(d.get(k) or "") for k in ("t", "m", "ms", "why", "src"))
        check_text_rules(ctx, item, text, new)
        if not (d.get("url") or "").startswith("http"):
            ctx.add("A1-来源链接", "来源与事实", item, "FAIL", "缺少来源链接", new)
        kinds = d.get("kinds") or []
        if not kinds or not set(kinds) <= DEAL_KINDS:
            ctx.add("F4-交易类型", "分类标签", item, "FAIL", f"kinds 无效：{kinds}", new)
        newco_txt = re.search(NEWCO_TEXT, text, re.I)
        if "newco" in kinds and not (newco_txt and re.search(NEWCO_EQUITY, text, re.I)):
            ctx.add("F5-NewCo 判定", "分类标签", item, "FAIL", "标了 newco，但正文没写新设公司 + 股权/融资", new)
        elif "newco" not in kinds and newco_txt and re.search(r"许可|授权|licen", text, re.I) and re.search(NEWCO_EQUITY, text, re.I):
            ctx.add("F5-NewCo 判定", "分类标签", item, "WARN", "正文像 NewCo（新公司 + 授权 + 股权），但未标 newco", new)
        missing = core - set(d.keys())
        if missing:
            ctx.add("H1-字段一致", "一致性比对", item, "FAIL", "缺字段：" + "、".join(sorted(missing)), new)
        if not re.fullmatch(r"\d{4}-\d{2}", d.get("d") or ""):
            ctx.add("H2-日期格式", "一致性比对", item, "FAIL", f"d 应为 YYYY-MM：{d.get('d')}", new)
        elif check_order and d["d"] > prev_d:
            ctx.add("H5-排序", "一致性比对", item, "FAIL", f"经济动态未按日期倒序（{d['d']} 排在 {prev_d} 之后）", new)
        prev_d = d.get("d") or prev_d
        m_src = re.search(r"(\d{4}-\d{2})-\d{2}", d.get("src") or "")
        if m_src and d.get("d") and m_src.group(1) != d["d"]:
            ctx.add("H2-日期格式", "一致性比对", item, "WARN", f"d={d['d']} 与来源日期 {m_src.group(0)} 月份不同", new)
        if not money_ok.search(d.get("m") or ""):
            ctx.add("H4-金额写法", "一致性比对", item, "WARN", f"金额写法与既有条目不同：{d.get('m')}", new)
        if re.search(r"\$\d+(\.\d+)?\s*亿", d.get("m", "") + d.get("ms", "")):
            ctx.add("H4-金额写法", "一致性比对", item, "FAIL", "混合写法“$X亿”", new)
        if new:
            for k, vals in dist.items():
                v = len(d.get(k) or "")
                if vals and not (min(vals) * 0.85 <= v <= max(vals) * 1.15):
                    ctx.add("H3-长度", "一致性比对", item, "WARN", f"{k} 长度 {v} 超出已发布范围 {min(vals)}–{max(vals)}", new)
            url = d.get("url") or ""
            is_filing = any(dom in url for dom in FILING_DOMAINS)
            if "公告" in (d.get("src") or "") and not is_filing and "引" not in (d.get("src") or ""):
                ctx.add("A4-公告优先", "来源与事实", item, "WARN", "来源写“公告”但链接不是交易所/SEC 公告", new)
            if candidates and not is_filing:
                words = {w for w in re.findall(r"[A-Za-z][A-Za-z0-9.-]{2,}|[\u4e00-\u9fff]{2,4}", d.get("t", ""))}
                for c in candidates.values():
                    if c.get("kind") == "filing" and any(w and w in (c.get("company", "") + c.get("title", "")) for w in words):
                        ctx.add("A4-公告优先", "来源与事实", item, "FAIL", f"本周候选里有同一公司的公告（{c.get('url')}），应先引公告再引媒体", new)
                        break
            if candidates and url and url not in candidates:
                ctx.add("A2-允许来源", "来源与事实", item, "FAIL", "来源链接不在本周抓取的候选列表里", new)
            nums = [v for v in numbers_in(d.get("m", "") + " " + d.get("ms", "")) if v not in numbers_in(d.get("why", "") + d.get("t", ""))]
            src_txt = (candidates.get(url) or {}).get("summary", "") if candidates else ""
            if src_txt:
                miss = [v for v in numbers_in(text) if v not in numbers_in(src_txt) and not v.startswith("20")]
                if miss:
                    ctx.add("B3-数字溯源", "数字核对", item, "WARN", "以下数字未在来源摘录中找到，需对原文核对：" + "、".join(sorted(set(miss))[:10]), new)
            elif new:
                ctx.add("B3-数字溯源", "数字核对", item, "MANUAL", "金额/里程碑/提成逐一对照原文", new)


def check_duplicates(ctx: Ctx, site: Site, ref: Site, new_ids: set[str], new_deals: set[str], week_items: list[dict]):
    seen_url, seen_title = {}, {}
    for a in site.published():
        for k, store in ((norm_url(a.get("url", "")), seen_url), (norm_title(a.get("title", "")), seen_title)):
            if k and k in store:
                ctx.add("H6-去重", "一致性比对", a["id"], "FAIL", f"与 {store[k]} 重复（同 DOI/URL 或同标题）", a["id"] in new_ids)
            store.setdefault(k, a["id"])
    for d in site.deals:
        k = norm_url(d.get("url", ""))
        if k in seen_url:
            ctx.add("H6-去重", "一致性比对", d.get("t", "")[:40], "FAIL", f"与 {seen_url[k]} 同链接", deal_key(d) in new_deals)
        seen_url.setdefault(k, d.get("t", "")[:40])
        t = norm_title(d.get("t", ""))
        if t in seen_title:
            ctx.add("H6-去重", "一致性比对", d.get("t", "")[:40], "FAIL", "同标题重复", deal_key(d) in new_deals)
        seen_title.setdefault(t, d.get("t", "")[:40])
    for w in week_items:
        for k in (norm_url(w.get("url", "")), norm_title(w.get("t", ""))):
            if k and (k in seen_url or k in seen_title):
                ctx.add("H6-去重", "一致性比对", w.get("t", w.get("url", ""))[:40], "FAIL", "与已发布内容重复", True)
    ctx.ok("H6-去重", "一致性比对")


def check_site_static(ctx: Ctx, site: Site):
    root = site.root
    css = (root / "site.css").read_text(encoding="utf-8") if (root / "site.css").exists() else ""
    for pat, label in CSS_REQUIRED:
        if not re.search(pat, css):
            ctx.add("D1-正文缩进对齐", "格式排版", "site.css", "FAIL", f"缺少样式：{label}")
    m = re.search(r"\.cap\{([^}]*)\}", css)
    if m and "text-indent:2em" in m.group(1):
        ctx.add("D2-图注紧贴", "格式排版", "site.css", "FAIL", "图注 .cap 不应首行缩进")
    m = re.search(r"--accent:(#[0-9A-Fa-f]{6})", css)
    if m:
        r, g, b = (int(m.group(1)[i:i + 2], 16) / 255 for i in (1, 3, 5))
        h = colorsys.rgb_to_hsv(r, g, b)[0]
        if not SITE_GREEN_HUE[0] <= h <= SITE_GREEN_HUE[1]:
            ctx.add("D5-站点绿色", "格式排版", "site.css", "FAIL", f"--accent {m.group(1)} 不是绿色系")
    # TheraSik 品牌 + 本地资源 + sitemap
    files = [root / "index.html", root / "feed.xml", root / "sitemap.xml", root / "site.css"] + \
        sorted(root.glob("pages/article/*.html")) + sorted(root.glob("content/articles/*.md")) + \
        [root / "content/catalog.json", root / "content/latest.json"] + sorted(root.glob("content/weekly/*/*.json"))
    for f in files:
        if not f.exists():
            continue
        txt = f.read_text(encoding="utf-8", errors="ignore")
        if BRAND_RE.search(txt):
            ctx.add("C7-TheraSik", "文风与禁用语", str(f.relative_to(root)), "FAIL", "出现 TheraSik 品牌写法")
        if f.suffix == ".html":
            base = f.parent
            for ref in set(re.findall(r'(?:src|href)="([^"#]+)"', txt)) | set(re.findall(r"'(img/[^'\s]+\.(?:webp|jpg|png|svg))", txt)):
                if re.match(r"(https?:|mailto:|data:|//|javascript:)", ref) or ref.startswith("${"):
                    continue
                path = ref.split("?")[0]
                target = (root / path.lstrip("/")) if path.startswith("/") else (base / path)
                if path.endswith("/"):
                    target = target / "index.html"
                if not target.exists() and "${" not in path:
                    ctx.add("G2-本地资源", "链接与资源", str(f.relative_to(root)), "FAIL", f"引用的文件不存在：{ref}")
    sm = root / "sitemap.xml"
    if sm.exists():
        for loc in re.findall(r"<loc>https?://[^/]+/([^<]*)</loc>", sm.read_text(encoding="utf-8")):
            loc = loc.split("#", 1)[0].split("?", 1)[0]
            p = root / loc
            if loc and not (p.exists() or (p / "index.html").exists()):
                ctx.add("G3-站点地图", "链接与资源", "sitemap.xml", "FAIL", f"sitemap 指向不存在的页面：{loc}")
    ctx.ok("G2-本地资源", "链接与资源")


def check_links_online(ctx: Ctx, items: list[tuple[str, str, bool]]):
    import requests
    ua = {"User-Agent": "Mozilla/5.0 (InLight publish audit; contact@inlightbio.com)"}

    def fetch(url, method):
        """Follow redirects by hand; if a hop points to plain http:// (port 80 is often
        blocked from build boxes, e.g. doi.org -> http://www.nejm.org), try https first."""
        from urllib.parse import urljoin
        cur = url
        for _ in range(10):
            kw = dict(headers=ua, timeout=20, allow_redirects=False)
            if method == "GET":
                kw["stream"] = True
            r = requests.request(method, cur, **kw)
            if r.status_code in (301, 302, 303, 307, 308) and r.headers.get("location"):
                nxt = urljoin(cur, r.headers["location"])
                if nxt.startswith("http://"):
                    nxt = "https://" + nxt[len("http://"):]
                cur = nxt
                continue
            return r.status_code
        return 310

    def probe(url):
        last = ""
        for method in ("HEAD", "GET"):
            try:
                code = fetch(url, method)
                if method == "HEAD" and (code >= 400 or code == 405):
                    continue
                return code, ""
            except Exception as e:  # network
                last = str(e)[:120]
        return None, last

    with cf.ThreadPoolExecutor(8) as ex:
        futs = {ex.submit(probe, u): (item, u, new) for item, u, new in items}
        for f in cf.as_completed(futs):
            item, u, new = futs[f]
            code, err = f.result()
            if code is None:
                ctx.add("G4-外链可达", "链接与资源", item, "FAIL", f"打不开：{u}（{err}）", new)
            elif code in (401, 403, 429, 999):
                ctx.add("G4-外链可达", "链接与资源", item, "WARN", f"HTTP {code}（站点拦截机器访问，人工点开确认）：{u}", new)
            elif code >= 400:
                ctx.add("G4-外链可达", "链接与资源", item, "FAIL", f"HTTP {code}：{u}", new)
            else:
                ctx.ok("G4-外链可达", "链接与资源", item, new)


def load_week(week: Path) -> tuple[list[dict], list[dict], dict[str, dict]]:
    arts = json.loads((week / "articles.json").read_text(encoding="utf-8")) if (week / "articles.json").exists() else []
    deals = json.loads((week / "deals.json").read_text(encoding="utf-8")) if (week / "deals.json").exists() else []
    cands = load_candidates(week / "candidates.json") if (week / "candidates.json").exists() else {}
    return arts, deals, cands


def load_candidates(path: Path) -> dict[str, dict]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, dict):
        data = data.get("items") or data.get("candidates") or []
    return {c["url"]: c for c in data if isinstance(c, dict) and c.get("url")}


def check_week(ctx: Ctx, site: Site, ref: Site, arts: list[dict], deals: list[dict], cands: dict):
    """周更条目（--week 目录 + content/latest.json）。每条带 _new 标记：True 表示本次新增。"""
    ref_art_keys = {"id", "f", "t", "ds", "disp", "j", "url", "au", "tags", "sum", "img", "status"}
    try:
        import inlight_catalog as cat
        valid_f = set(cat.FIELD_KEYS.values()) | {f"c{i}" for i in range(1, 10)}
    except Exception:
        valid_f = {f"c{i}" for i in range(1, 10)}
    for a in arts:
        new = a.get("_new", True)
        item = a.get("id") or a.get("url", "")[:40]
        text = " ".join(str(a.get(k) or "") for k in ("t", "sum", "lead", "body", "discuss", "note"))
        check_text_rules(ctx, item, text, new)
        if re.search(HYPE_TITLE, a.get("t") or ""):
            ctx.add("C3-标题禁用词", "文风与禁用语", item, "FAIL", f"标题含夸张词：{a.get('t')}", new)
        n_bp = len(re.findall(BOILERPLATE, text))
        if n_bp > 2:
            ctx.add("C2-套话上限", "文风与禁用语", item, "FAIL", f"“原文未报告/未给出”出现 {n_bp} 次（上限 2）", new)
        if not (a.get("url") or "").startswith("http"):
            ctx.add("A1-来源链接", "来源与事实", item, "FAIL", "缺少原文链接 url", new)
        miss = ref_art_keys - set(a.keys())
        if miss:
            ctx.add("H1-字段一致", "一致性比对", item, "FAIL", "缺字段：" + "、".join(sorted(miss)), new)
        if a.get("evidence_level") != "fulltext":
            ctx.add("A2-全文准入", "来源与事实", item, "FAIL", f"evidence_level={a.get('evidence_level')}（只发全文）", new)
        if cands and a.get("url") not in cands:
            ctx.add("A2-允许来源", "来源与事实", item, "FAIL", "链接不在本周候选列表里", new)
        if a.get("f") not in valid_f:
            ctx.add("F1-主领域", "分类标签", item, "FAIL", f"领域代码无效：{a.get('f')}", new)
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", a.get("ds") or ""):
            ctx.add("H2-日期格式", "一致性比对", item, "FAIL", f"ds 格式：{a.get('ds')}", new)
        src = (cands.get(a.get("url")) or {}).get("summary", "")
        if src:
            miss_n = [v for v in numbers_in(text) if v not in numbers_in(src)]
            if miss_n:
                ctx.add("B3-数字溯源", "数字核对", item, "WARN", "来源摘录里没有这些数字，需对全文核对：" + "、".join(sorted(set(miss_n))[:10]), new)
    if deals:
        # 周更交易与首页 DEALS 用同一套规则（NewCo、金额、日期、公告优先、数字溯源……）
        fake = SimpleNamespace(deals=deals)
        check_deals(ctx, fake, ref, {deal_key(d) for d in deals if d.get("_new", True)}, cands, check_order=False)
        for d in deals:
            miss = {"d", "kinds", "t", "m", "ms", "why", "src", "url"} - set(d.keys())
            if miss:
                ctx.add("H1-字段一致", "一致性比对", d.get("t", "")[:40], "FAIL", "缺字段：" + "、".join(sorted(miss)), d.get("_new", True))


def run_tests(ctx: Ctx, root: Path):
    py = sys.executable
    r = subprocess.run([py, "-m", "unittest", "discover", "-s", "tests", "-t", "."], cwd=root, capture_output=True, text=True)
    tail = (r.stderr or r.stdout).strip().splitlines()[-3:]
    if r.returncode:
        ctx.add("I1-单元测试", "构建与测试", "tests", "FAIL", " / ".join(tail))
    else:
        ctx.ok("I1-单元测试", "构建与测试")
    r = subprocess.run([py, "build_pages.py", "--dry-run"], cwd=root, capture_output=True, text=True)
    if r.returncode:
        ctx.add("I2-构建预演", "构建与测试", "build_pages.py", "FAIL", (r.stderr or r.stdout).strip()[-300:])
    else:
        ctx.ok("I2-构建预演", "构建与测试")


def check_live(ctx: Ctx, site: Site, live: str, new_ids: set[str], new_deals: set[str]):
    import requests
    live = live.rstrip("/")
    try:
        idx = requests.get(live + "/", timeout=30).text
    except Exception as e:
        ctx.add("J1-线上首页", "上线后核验", live, "FAIL", f"首页打不开：{e}")
        return
    def status(url, head=False):
        try:
            return (requests.head if head else requests.get)(url, timeout=30).status_code
        except Exception as e:  # network
            return f"打不开（{str(e)[:80]}）"

    live_deals = parse_deals(idx)
    if len(live_deals) != len(site.deals):
        ctx.add("J3-线上首页收录", "上线后核验", "DEALS", "FAIL", f"线上经济动态 {len(live_deals)} 条，本地 {len(site.deals)} 条（部署未完成或版本不一致）", True)
    for a in site.published():
        aid = a["id"]
        if aid not in new_ids and aid not in idx and f"{aid}.html" not in idx:
            ctx.add("J3-线上首页收录", "上线后核验", aid, "WARN", "线上首页找不到这篇（检查首页是否静态包含文章 id）")
    for aid in new_ids:
        code = status(f"{live}/pages/article/{aid}.html")
        if code != 200:
            ctx.add("J2-线上文章页", "上线后核验", aid, "FAIL", f"HTTP {code}", True)
        if aid not in idx:
            ctx.add("J3-线上首页收录", "上线后核验", aid, "FAIL", "线上首页没有这篇", True)
    for d in site.deals:
        if deal_key(d) in new_deals and d.get("t", "") not in idx:
            ctx.add("J3-线上首页收录", "上线后核验", d.get("t", "")[:40], "FAIL", "线上首页没有这条经济动态", True)
    for g, rows in image_groups(site).items():
        for item, p in rows:
            if item in new_ids | new_deals and p.exists():
                rel = p.relative_to(site.root).as_posix()
                code = status(f"{live}/{rel}", head=True)
                if code != 200:
                    ctx.add("J4-线上图片", "上线后核验", item, "FAIL", f"{rel} HTTP {code}", True)
    ctx.ok("J1-线上首页", "上线后核验")


# 自动检查目录：(编号, 分组, 说明, 运行条件)。运行条件 None=总是；week=给了 --week；
# base=给了 --base；online=未加 --offline；tests=--run-tests；live=--live；images=未加 --skip-images
AUTO_CHECKS = [
    ("A1-来源链接", "来源与事实", "每篇文章/每条交易都有 http(s) 原文链接", None),
    ("A2-全文准入", "来源与事实", "数据卡“证据等级”写明全文；有正文 .md", None),
    ("A2-允许来源", "来源与事实", "新条目链接必须来自本周抓取的候选列表", "week"),
    ("A3-核对记录", "来源与事实", "关键数据卡有“核对记录”", None),
    ("A4-公告优先", "来源与事实", "候选里有同公司公告时，交易必须先引公告", "week"),
    ("B1-数字与正文一致", "数字核对", "标题/卡片标题/导语/速览里的数字都能在正文或数据卡找到", None),
    ("B2-阿拉伯数字", "数字核对", "速览用阿拉伯数字，不写“百分之/零点”", None),
    ("B3-数字溯源", "数字核对", "新交易的数字能在来源摘录里找到", "week"),
    ("C1-禁用语", "文风与禁用语", "无自我辩护/内部编辑备注/版面说明/商业推广/授权声明等禁用语", None),
    ("C2-套话上限", "文风与禁用语", "“原文未报告/未给出”每篇 ≤2 次", None),
    ("C3-标题禁用词", "文风与禁用语", "标题无“重磅/颠覆/突破”等", None),
    ("C4-声明只出现一次", "文风与禁用语", "AI 生成/投资建议/核对记录类说明每页至多一次", None),
    ("C5-口语自评", "文风与禁用语", "无俚语、口语化自我评价", None),
    ("C7-TheraSik", "文风与禁用语", "页面内容不出现 TheraSik 品牌写法", None),
    ("D1-正文缩进对齐", "格式排版", "CSS 有两端对齐+首行缩进两字；正文段落都在 .sec 内", None),
    ("D2-图注紧贴", "格式排版", "机制图后紧跟步骤/图注；图注不缩进", None),
    ("D3-小节结构", "格式排版", "正文含“研究设计”“局限与不确定”等必备小节", None),
    ("D4-段落长度", "格式排版", "正文段落 ≤150 字（WARN）", None),
    ("D5-站点绿色", "格式排版", "站点主色 --accent 为绿色系", None),
    ("E1-图片存在", "图片", "引用的卡片图/机制图/交易图文件存在", "images"),
    ("E2-尺寸格式", "图片", "格式与尺寸同已发布同类图一致", "images"),
    ("E3-色调范围", "图片", "L*/a*/b* 在已发布同类图范围内（±15%）", "images"),
    ("E4-分区配色", "图片", "学术进展：绿+赤陶红、无蓝；经济动态：蓝为主、无绿", "images"),
    ("E5-暖米白底", "图片", "底色接近 #f7f4ea", "images"),
    ("E6-颗粒质感", "图片", "留白区有纸纹颗粒", "images"),
    ("E7-图内无字", "图片", "新卡片/交易图 OCR 不出字（文字由程序叠加）", "images"),
    ("F1-主领域", "分类标签", "主领域在九领域词表内，标签/颜色/代码一致", None),
    ("F2-相关领域", "分类标签", "相关领域有效且不与主领域重复", None),
    ("F3-预印本标记", "分类标签", "预印本标记与期刊一致", None),
    ("F4-交易类型", "分类标签", "交易 kinds 在既定类型内", None),
    ("F5-NewCo 判定", "分类标签", "newco 标签与“新设公司 + 授权 + 股权/融资”一致", None),
    ("F6-每周篇数", "分类标签", "本次新增学术进展 4–5 篇", "count"),
    ("G1-文章页存在", "链接与资源", "每篇已发布文章都有 pages/article/<id>.html", None),
    ("G2-本地资源", "链接与资源", "页面引用的本地图片/文件都存在", None),
    ("G3-站点地图", "链接与资源", "sitemap 收录的页面都存在", None),
    ("G4-外链可达", "链接与资源", "原文/来源链接可打开（403/429 记 WARN 人工点开）", "online"),
    ("H1-字段一致", "一致性比对", "字段齐全，与已发布条目一致", None),
    ("H2-日期格式", "一致性比对", "日期格式 YYYY-MM / YYYY-MM-DD / YYYY.MM", None),
    ("H3-长度", "一致性比对", "卡片标题 ≤30；速览 100–200 汉字；新条目长度在已发布范围内", None),
    ("H4-金额写法", "一致性比对", "金额写法与既有条目一致，不写“$X亿”", None),
    ("H5-排序", "一致性比对", "经济动态按日期倒序", None),
    ("H6-去重", "一致性比对", "不与已发布内容同 DOI/URL/标题", None),
    ("I1-单元测试", "构建与测试", "python -m unittest discover -s tests 全部通过", "tests"),
    ("I2-构建预演", "构建与测试", "build_pages.py --dry-run 成功", "tests"),
    ("J1-线上首页", "上线后核验", "线上首页可打开", "live"),
    ("J2-线上文章页", "上线后核验", "新文章线上页面可打开", "live"),
    ("J3-线上首页收录", "上线后核验", "线上首页含新文章/新交易", "live"),
    ("J4-线上图片", "上线后核验", "新图片线上可取到", "live"),
]

MANUAL_ITEMS = [
    ("A5-事实核对", "来源与事实", "每条事实主张对照全文：无 contradicted/unsupported；“趋势/相关/推测”保留原文语气"),
    ("A6-一手优先", "来源与事实", "交易先引公司公告/交易所披露，再引媒体；媒体只作补充"),
    ("B4-全量数字核对", "数字核对", "正文、数据卡、图上每个数字逐一对原文（含单位、n、时点、对照）；记录写入 docs/source_audit.md"),
    ("C6-数字不一致写法", "文风与禁用语", "来源数字不一时只写一次“各来源数字不一：A 报 X，B 报 Y。”"),
    ("E8-图像内容", "图片", "学术图：无字插画 + 程序叠加中文；经济动态图：经济主题（不以分子/细胞为主体）；同期图互不重复；与文章内容对应"),
    ("D6-版面目检", "格式排版", "桌面与手机各看一遍：正文两端对齐、首行缩进两字；列表/图注/标题/数据卡不缩进；图注紧贴图下"),
    ("J5-线上目检", "上线后核验", "发布后打开线上首页、学术进展、经济动态和每篇新文章，确认无破图、链接可点"),
]


# ------------------------------------------------------------------ main

def load_baseline(path: Path) -> set[tuple[str, str, str]]:
    if not path.exists():
        return set()
    data = json.loads(path.read_text(encoding="utf-8"))
    return {(e["check"], e["item"], e.get("msg_hash", "")) for e in data.get("entries", [])}


def msg_hash(msg: str) -> str:
    return hashlib.sha1(re.sub(r"\d+(\.\d+)?", "#", msg).encode("utf-8")).hexdigest()[:10]


def apply_baseline(findings: list[Finding], baseline: set[tuple[str, str, str]], strict: bool = False):
    """基线里的旧问题记 LEGACY（不拦发布）；新条目一律不吃基线。--strict 时 WARN 升为 FAIL。"""
    for f in findings:
        if f.status in ("FAIL", "WARN") and not f.new and (f.check, f.item, msg_hash(f.msg)) in baseline:
            f.status = "LEGACY"
        if strict and f.status == "WARN":
            f.status = "FAIL"


def audit(args) -> tuple[Ctx, dict]:
    root = Path(args.root).resolve()
    ctx = Ctx(root, args.base, Path(args.week).resolve() if args.week else None, not args.offline, args.strict)
    site = Site(root)
    ref = Site(root, args.base) if args.base else site
    if args.base and not ref.index:
        raise SystemExit(f"找不到基准版本 {args.base}")
    base_ids = {a["id"] for a in ref.published()}
    base_deals = {deal_key(d) for d in ref.deals}
    new_ids = {a["id"] for a in site.published()} - base_ids if args.base else set()
    new_deals = {deal_key(d) for d in site.deals} - base_deals if args.base else set()

    week_arts, week_deals, cands = load_week(ctx.week) if ctx.week else ([], [], {})
    if args.candidates:
        cands.update(load_candidates(Path(args.candidates)))
    elif ctx.week and not cands:
        guess = root / "preview" / "weekly" / ctx.week.name / "candidates.json"
        if guess.exists():
            cands.update(load_candidates(guess))
    for x in week_arts + week_deals:
        x["_new"] = True
    # content/latest.json 是网站实际读取的周更内容。若已用 --week 指向本周目录，
    # 以周目录为准，不再合并 latest，避免把已写入 latest 的条目拿去对照另一份
    # --week 夹具候选（单元测试）或重复计数。
    if not ctx.week:
        ref_latest = {norm_url(x.get("url", "")) for x in ref.weekly_articles() + ref.weekly_deals()} if args.base else None
        seen_week = {norm_url(x.get("url", "")) for x in week_arts + week_deals}
        for src, dst in ((site.weekly_articles(), week_arts), (site.weekly_deals(), week_deals)):
            for x in src:
                k = norm_url(x.get("url", ""))
                if k and k in seen_week:
                    continue
                x = dict(x)
                x["_new"] = bool(ref_latest is not None and k not in ref_latest)
                dst.append(x)
    else:
        # 有 --week 时仍可用 --base 判断“相对旧 latest 是否新增”，但条目集合只来自周目录
        pass
    new_week_arts = [a for a in week_arts if a["_new"]]
    check_site_static(ctx, site)
    check_articles(ctx, site, ref, new_ids)
    check_deals(ctx, site, ref, new_deals, cands)
    check_duplicates(ctx, site, ref, new_ids, new_deals, [x for x in week_arts + week_deals if x["_new"]])
    week_imgs = []
    if week_arts or week_deals:
        check_week(ctx, site, ref, week_arts, week_deals, cands)
        for a in week_arts:
            if a.get("img"):
                rel = a["img"]
                p = Path(rel) if Path(rel).is_absolute() else root / rel
                if not p.exists() and ctx.week and (ctx.week / rel).exists():
                    p = ctx.week / rel
                week_imgs.append((a.get("id") or a.get("url", "")[:40], p, "card", a["_new"]))
            else:
                ctx.add("E1-图片存在", "图片", a.get("id") or a.get("url", "")[:40], "FAIL", "周更文章没有配图（首页会显示破图）", a["_new"])
    if args.base or ctx.week:
        n_new = len(new_ids) + len(new_week_arts)
        lo, hi = ACADEMIC_PER_WEEK
        n_new_deals = len(new_deals) + sum(1 for d in week_deals if d["_new"])
        if n_new > hi:
            ctx.add("F6-每周篇数", "分类标签", "本次新增", "FAIL", f"新增学术进展 {n_new} 篇（上限 {hi}）", True)
        elif n_new < lo:
            ctx.add("F6-每周篇数", "分类标签", "本次新增", "WARN", f"新增学术进展 {n_new} 篇（目标 {lo}–{hi}；不足时宁缺毋滥）、经济动态 {n_new_deals} 条", True)
    ref_groups = image_groups(ref) if args.base else image_groups(site)
    if args.base:  # 基准图片读当前工作区文件（git show 取不到二进制路径时用当前文件）
        ref_groups = {g: [(i, p) for i, p in rows if i not in new_ids | new_deals] for g, rows in image_groups(site).items()}
    if not args.skip_images:
        check_images(ctx, site, ref_groups, new_ids | new_deals, week_imgs)
    if ctx.online:
        items = [(a["id"], a["url"], a["id"] in new_ids) for a in site.published() if a.get("url", "").startswith("http")]
        items += [(d.get("t", "")[:40], d["url"], deal_key(d) in new_deals) for d in site.deals if d.get("url", "").startswith("http")]
        items += [(x.get("t", "")[:40], x["url"], x["_new"]) for x in week_arts + week_deals if x.get("url", "").startswith("http")]
        if args.only_new_links:
            items = [x for x in items if x[2]]
        check_links_online(ctx, items)
    if args.run_tests:
        run_tests(ctx, root)
    if args.live:
        check_live(ctx, site, args.live, new_ids, new_deals)
    for cid, sec, msg in MANUAL_ITEMS:
        ctx.add(cid, sec, "人工", "MANUAL", msg)

    apply_baseline(ctx.findings, load_baseline(Path(args.baseline)), ctx.strict)
    meta = {"root": str(root), "base": args.base, "week": str(ctx.week) if ctx.week else None,
            "online": ctx.online, "gates": {"week": bool(ctx.week), "base": bool(args.base), "online": ctx.online,
                                             "tests": bool(args.run_tests), "live": bool(args.live), "images": not args.skip_images,
                                             "count": bool(args.base or ctx.week)},
            "new_articles": sorted(new_ids), "new_deals": sorted(new_deals),
            "new_weekly": sum(1 for x in week_arts + week_deals if x["_new"]),
            "time": datetime.now().isoformat(timespec="seconds")}
    return ctx, meta


def write_reports(ctx: Ctx, meta: dict, out: Path) -> dict:
    out.mkdir(parents=True, exist_ok=True)
    counts = Counter(f.status for f in ctx.findings)
    passed = counts["FAIL"] == 0
    summary = {"publish_allowed": passed, "counts": dict(counts), "checks_run": dict(ctx.checks_run), **meta,
               "findings": [asdict(f) for f in sorted(ctx.findings, key=lambda f: (STATUS_ORDER.index(f.status), f.section, f.check, f.item))]}
    (out / "audit_report.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    lines = [f"# 发布前审计报告 {meta['time']}", "",
             f"结论：**{'通过，可以发布' if passed else '未通过，不得发布'}**", "",
             f"基准：{meta['base'] or '（无，全量审计）'}　周目录：{meta['week'] or '—'}　外链检查：{'开' if meta['online'] else '关'}",
             f"新增文章：{', '.join(meta['new_articles']) or '无'}　新增经济动态：{len(meta['new_deals'])} 条", "",
             "| 状态 | 数量 |", "|---|---|"] + [f"| {s} | {counts.get(s, 0)} |" for s in STATUS_ORDER] + [""]
    worst = {}
    for f in ctx.findings:
        if f.check not in worst or STATUS_ORDER.index(f.status) < STATUS_ORDER.index(worst[f.check]):
            worst[f.check] = f.status
    gates = meta.get("gates", {})
    cover = []
    for cid, sec, desc, gate in AUTO_CHECKS:
        st = worst.get(cid) or ("未运行" if gate and not gates.get(gate) else "PASS")
        cover.append({"check": cid, "section": sec, "desc": desc, "result": st})
    summary["coverage"] = cover
    (out / "audit_report.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    lines += ["## 检查覆盖", "", "| 检查 | 分组 | 内容 | 结果 |", "|---|---|---|---|"]
    lines += [f"| {c['check']} | {c['section']} | {c['desc']} | {c['result']} |" for c in cover] + [""]
    by_sec = defaultdict(list)
    for f in summary["findings"]:
        by_sec[f["section"]].append(f)
    for sec in ["来源与事实", "数字核对", "文风与禁用语", "格式排版", "图片", "分类标签", "链接与资源", "一致性比对", "构建与测试", "上线后核验"]:
        rows = by_sec.get(sec, [])
        lines.append(f"## {sec}")
        if not rows:
            lines.append("全部自动检查通过。\n")
            continue
        lines += ["| 状态 | 检查 | 条目 | 说明 |", "|---|---|---|---|"]
        for f in rows:
            msg = f["msg"].replace("|", "／").replace("\n", " ")
            lines.append(f"| {f['status']}{' 🆕' if f['new'] else ''} | {f['check']} | {f['item']} | {msg} |")
        lines.append("")
    (out / "audit_report.md").write_text("\n".join(lines), encoding="utf-8")
    return summary


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="前沿追踪发布前审计")
    p.add_argument("--root", default=str(ROOT))
    p.add_argument("--base", help="上一版已发布内容的 git 版本（如 origin/main），用于找出新增条目并做一致性比对")
    p.add_argument("--week", help="周更目录（含 articles.json / deals.json / candidates.json）")
    p.add_argument("--offline", action="store_true", help="不检查外链可达性")
    p.add_argument("--candidates", help="本周 candidates.json（默认取 --week 目录或 preview/weekly/<周>/ 下的）")
    p.add_argument("--only-new-links", action="store_true", help="只检查新增条目的外链")
    p.add_argument("--skip-images", action="store_true")
    p.add_argument("--run-tests", action="store_true", help="同时跑单元测试和 build_pages.py --dry-run")
    p.add_argument("--live", help="上线后核验：线上站点根地址")
    p.add_argument("--strict", action="store_true", help="WARN 也算失败")
    p.add_argument("--baseline", default=str(ROOT / "tools" / "audit_baseline.json"))
    p.add_argument("--write-baseline", action="store_true", help="把当前的 FAIL/WARN 登记为历史问题（只用于校准，不含新条目）")
    p.add_argument("--out", help="报告目录，默认 preview/audit/<时间>")
    args = p.parse_args(argv)
    ctx, meta = audit(args)
    out = Path(args.out) if args.out else Path(args.root) / "preview" / "audit" / datetime.now().strftime("%Y%m%d-%H%M%S")
    if args.write_baseline:
        entries = [{"check": f.check, "item": f.item, "msg_hash": msg_hash(f.msg), "status": f.status, "msg": f.msg}
                   for f in ctx.findings if f.status in ("FAIL", "WARN") and not f.new]
        Path(args.baseline).write_text(json.dumps({"created": meta["time"], "note": "历史问题基线：只对已发布旧条目生效，新条目不得登记。修好一条就删一条。",
                                                   "entries": entries}, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"写入基线 {len(entries)} 条：{args.baseline}")
        for f in ctx.findings:
            if f.status in ("FAIL", "WARN") and not f.new:
                f.status = "LEGACY"
    summary = write_reports(ctx, meta, out)
    c = summary["counts"]
    print(f"审计{'通过' if summary['publish_allowed'] else '未通过'}：FAIL {c.get('FAIL', 0)} · WARN {c.get('WARN', 0)} · "
          f"LEGACY {c.get('LEGACY', 0)} · MANUAL {c.get('MANUAL', 0)}　报告：{out / 'audit_report.md'}")
    return 0 if summary["publish_allowed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
