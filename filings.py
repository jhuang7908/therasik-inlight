#!/usr/bin/env python3
"""Listed-company filings for the weekly digest (SEC EDGAR, HKEXnews, cninfo).

fetch_filings(config, start, end) returns rows in the same shape as the RSS rows
in run_weekly.py, with kind="filing", so they join the allowed-URL list and the
writer can cite the filing itself instead of a media report.

Every source is fail-soft: an error in one source is logged and the others still run.
Only public, unauthenticated endpoints are used. Read-only.
"""

from __future__ import annotations

import json
import logging
import re
import shutil
import subprocess
import tempfile
import time
from datetime import datetime, timedelta, timezone
from html import unescape

import requests

SEC_UA = "InLight Bio weekly digest contact@inlightbio.com"
BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)
EDGAR_FTS = "https://efts.sec.gov/LATEST/search-index"
HKEX_SEARCH = "https://www1.hkexnews.hk/search/titleSearchServlet.do"
HKEX_LATEST = "https://www1.hkexnews.hk/ncms/json/eds/lcisehk1relsdc_1.json"
HKEX_BASE = "https://www1.hkexnews.hk"
CNINFO_QUERY = "https://www.cninfo.com.cn/new/hisAnnouncement/query"
CNINFO_STATIC = "https://static.cninfo.com.cn/"

DEFAULT_HK_WATCHLIST = ["02162", "01276", "03692", "09995", "02142", "06990", "01093", "01177", "01801", "09926"]
# Deal-like title terms (traditional + simplified Chinese, English)
HK_DEAL_RE = r"許可協議|授權許可|獨家許可|授權協議|許可及合作|許可與合作|合作協議|合作及許可|新公司|NewCo|合資|首付|里程碑|LICEN[CS]E|COLLABORAT|NEWCO"
HK_WATCH_EXTRA_RE = r"自願公告|內幕消息|須予披露|業務更新|VOLUNTARY ANNOUNCEMENT|INSIDE INFORMATION|DISCLOSEABLE"
HK_EXCLUDE_RE = r"月報表|一般授權|特別授權|購回授權|授權代表|上市許可申請|註冊證|生產許可|回購|Monthly Return|Next Day Disclosure"
HK_PHARMA_NAME_RE = r"製藥|藥業|醫藥|生物|醫療|健康|藥|基因|細胞|腫瘤|抗體|BIO|PHARM|THERA|ONCO|GENE"
CN_DEFAULT_KEYWORDS = ["授权许可", "独家许可", "合作协议", "合资", "设立公司", "首付款", "里程碑"]
CN_DEFAULT_EXCLUDE = ["生产许可证", "上市许可申请", "注册证"]
CN_PHARMA_RE = r"药|生物|医|健康|疫苗|基因|细胞|抗体|制药|临床|适应症|治疗"
STRONG_RE = r"(?i)licen[cs]e|newco|upfront|首付|許可|许可|授權|授权|合資|合资|新公司"


def _norm_date(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).date().isoformat()


def _get(session: requests.Session, url: str, **kw) -> requests.Response:
    resp = session.get(url, timeout=kw.pop("timeout", 45), **kw)
    resp.raise_for_status()
    return resp


# ---------------------------------------------------------------- SEC EDGAR

PHARMA_SICS = ["2833", "2834", "2835", "2836", "8731", "8733"]


def _edgar_doc_pref(file_type: str, form: str) -> int:
    ft = (file_type or "").upper()
    if ft.startswith("EX-99"):
        return 0  # press release exhibit
    if ft == (form or "").upper():
        return 1  # main document
    return 2


def parse_edgar_hits(payload: dict, query: str, sics: list[str] | None = None) -> list[dict]:
    """One row per filing (accession), pharma/biotech SIC codes only, press-release exhibit preferred."""
    allowed_sics = set(sics if sics is not None else PHARMA_SICS)
    best: dict[str, tuple[int, dict]] = {}
    for hit in (payload.get("hits") or {}).get("hits") or []:
        src = hit.get("_source") or {}
        if allowed_sics and not (set(src.get("sics") or []) & allowed_sics):
            continue
        hid = hit.get("_id") or ""
        if ":" not in hid:
            continue
        adsh, filename = hid.split(":", 1)
        ciks = src.get("ciks") or []
        if not ciks:
            continue
        cik = str(int(ciks[0]))
        names = src.get("display_names") or [""]
        name = names[0]
        m = re.match(r"^(.*?)\s*\(([^)]*)\)\s*\(CIK", name)
        company = (m.group(1) if m else re.sub(r"\s*\(CIK.*$", "", name)).strip()
        ticker = (m.group(2) if m else "").split(",")[0].strip()
        form = src.get("form") or src.get("file_type") or ""
        desc = (src.get("file_description") or src.get("file_type") or "").strip()
        pref = _edgar_doc_pref(src.get("file_type") or "", form)
        row = {
            "date": src.get("file_date") or "",
            "company": company,
            "ticker": ticker,
            "exchange": "SEC EDGAR",
            "form": form,
            "title": f"{company} {form} {desc}".strip(),
            "url": f"https://www.sec.gov/Archives/edgar/data/{cik}/{adsh.replace('-', '')}/{filename}",
            "match": query,
        }
        if adsh not in best or pref < best[adsh][0]:
            best[adsh] = (pref, row)
    return [row for _, row in best.values()]


def fetch_edgar(cfg: dict, start: datetime, end: datetime, session: requests.Session) -> list[dict]:
    queries = cfg.get("queries") or [
        {"q": '"exclusive license" "Greater China"', "forms": "8-K,6-K,10-Q,20-F"},
        {"q": '"exclusive license agreement"', "forms": "8-K,6-K"},
        {"q": '"NewCo"', "forms": "8-K,6-K"},
        {"q": '"upfront payment" "license agreement"', "forms": "8-K,6-K"},
    ]
    sics = cfg.get("sics") if "sics" in cfg else PHARMA_SICS
    min_gap = 1.0 / float(cfg.get("max_rps") or 5)  # SEC allows <=10 req/s
    rows: list[dict] = []
    for spec in queries:
        params = {
            "q": spec["q"],
            "forms": spec.get("forms") or "8-K,6-K",
            "dateRange": "custom",
            "startdt": _norm_date(start),
            "enddt": _norm_date(end),
        }
        try:
            resp = _get(session, EDGAR_FTS, params=params, headers={"User-Agent": SEC_UA, "Accept": "application/json"})
            got = parse_edgar_hits(resp.json(), spec["q"], sics)
            logging.info("  EDGAR %s [%s]: %d 条", spec["q"], params["forms"], len(got))
            rows.extend(got)
        except Exception as e:  # fail soft per query
            logging.warning("  EDGAR 查询失败 %s：%s", spec["q"], e)
        time.sleep(min_gap)
    return rows


# ---------------------------------------------------------------- HKEXnews

def _hk_keep(title: str, code: str, name: str, watch: set[str]) -> bool:
    if re.search(HK_EXCLUDE_RE, title, re.I):
        return False
    if code in watch:
        return bool(re.search(HK_DEAL_RE, title, re.I) or re.search(HK_WATCH_EXTRA_RE, title, re.I))
    return bool(re.search(HK_DEAL_RE, title, re.I) and re.search(HK_PHARMA_NAME_RE, name, re.I))


def parse_hkex_search(payload: dict, watch: set[str]) -> list[dict]:
    raw = payload.get("result")
    items = json.loads(raw) if isinstance(raw, str) and raw not in ("", "null") else (raw or [])
    rows = []
    for x in items:
        title = re.sub(r"\s+", " ", unescape(x.get("TITLE") or "")).strip()
        code = (x.get("STOCK_CODE") or "").split("<br/>")[0].strip()
        name = re.sub(r"<[^>]+>", " ", unescape(x.get("STOCK_NAME") or "")).strip()
        if not _hk_keep(title, code, name, watch):
            continue
        try:
            day = datetime.strptime((x.get("DATE_TIME") or "")[:10], "%d/%m/%Y").date().isoformat()
        except ValueError:
            day = ""
        rows.append({
            "date": day,
            "company": name,
            "ticker": code,
            "exchange": "HKEX",
            "form": "公告",
            "title": title,
            "url": HKEX_BASE + (x.get("FILE_LINK") or ""),
            "match": "watchlist" if code in watch else "title",
        })
    return rows


def parse_hkex_latest(payload: dict, watch: set[str], start: datetime) -> list[dict]:
    rows = []
    for x in payload.get("newsInfoLst") or []:
        title = re.sub(r"\s+", " ", unescape(x.get("title") or "")).strip()
        stocks = x.get("stock") or [{}]
        code = (stocks[0].get("sc") or "").strip()
        name = (stocks[0].get("sn") or "").strip()
        if not _hk_keep(title, code, name, watch):
            continue
        try:
            when = datetime.strptime(x.get("relTime") or "", "%d/%m/%Y %H:%M").replace(tzinfo=timezone(timedelta(hours=8)))
        except ValueError:
            continue
        if when < start:
            continue
        rows.append({
            "date": when.date().isoformat(), "company": name, "ticker": code, "exchange": "HKEX",
            "form": "公告", "title": title, "url": HKEX_BASE + (x.get("webPath") or ""),
            "match": "watchlist" if code in watch else "title",
        })
    return rows


def fetch_hkex(cfg: dict, start: datetime, end: datetime, session: requests.Session) -> list[dict]:
    watch = set(cfg.get("watchlist") or DEFAULT_HK_WATCHLIST)
    hkt = timezone(timedelta(hours=8))
    rows: list[dict] = []
    params = {
        "sortDir": "0", "sortByOptions": "DateTime", "category": "0", "market": "SEHK",
        "stockId": "-1", "documentType": "-1",
        "fromDate": start.astimezone(hkt).strftime("%Y%m%d"), "toDate": end.astimezone(hkt).strftime("%Y%m%d"),
        "title": "", "searchType": "0", "t1code": "-2", "t2Gcode": "-2", "t2code": "-2",
        "rowRange": str(cfg.get("row_range") or 8000), "lang": "ZH",
    }
    try:
        payload = _get(session, HKEX_SEARCH, params=params, headers={"User-Agent": BROWSER_UA}, timeout=90).json()
        got = parse_hkex_search(payload, watch)
        logging.info("  HKEX 按日期检索：共 %s 条，保留 %d 条", payload.get("recordCnt"), len(got))
        rows.extend(got)
    except Exception as e:
        logging.warning("  HKEX 日期检索失败：%s", e)
    try:
        payload = _get(session, cfg.get("latest_feed") or HKEX_LATEST, headers={"User-Agent": BROWSER_UA}).json()
        got = parse_hkex_latest(payload, watch, start)
        logging.info("  HKEX 最新披露：保留 %d 条", len(got))
        rows.extend(got)
    except Exception as e:
        logging.warning("  HKEX 最新披露 feed 失败：%s", e)
    return rows


# ---------------------------------------------------------------- cninfo

def parse_cninfo(payload: dict, keyword: str, exclude: list[str]) -> list[dict]:
    rows = []
    for x in payload.get("announcements") or []:
        title = re.sub(r"<[^>]+>", "", x.get("announcementTitle") or "").strip()
        name = (x.get("secName") or "").strip()
        if any(bad in title for bad in exclude):
            continue
        if not (re.search(CN_PHARMA_RE, name) or re.search(CN_PHARMA_RE, title)):
            continue
        ts = x.get("announcementTime")
        day = datetime.fromtimestamp(ts / 1000, tz=timezone(timedelta(hours=8))).date().isoformat() if ts else ""
        rows.append({
            "date": day, "company": name, "ticker": x.get("secCode") or "", "exchange": "cninfo（沪深）",
            "form": "公告", "title": title, "url": CNINFO_STATIC + (x.get("adjunctUrl") or ""),
            "match": keyword,
        })
    return rows


def fetch_cninfo(cfg: dict, start: datetime, end: datetime, session: requests.Session) -> list[dict]:
    keywords = cfg.get("keywords") or CN_DEFAULT_KEYWORDS
    exclude = cfg.get("exclude") or CN_DEFAULT_EXCLUDE
    cst = timezone(timedelta(hours=8))
    se = f"{start.astimezone(cst).date().isoformat()}~{end.astimezone(cst).date().isoformat()}"
    rows: list[dict] = []
    for kw in keywords:
        try:
            resp = session.post(CNINFO_QUERY, timeout=45, headers={"User-Agent": BROWSER_UA}, data={
                "pageNum": "1", "pageSize": "30", "column": "szse", "tabName": "fulltext",
                "searchkey": kw, "seDate": se, "isHLtitle": "true",
            })
            resp.raise_for_status()
            payload = resp.json()
            got = parse_cninfo(payload, kw, exclude)
            logging.info("  cninfo %s：命中 %s 条，保留 %d 条", kw, payload.get("totalAnnouncement"), len(got))
            rows.extend(got)
        except Exception as e:
            logging.warning("  cninfo 查询失败 %s：%s", kw, e)
        time.sleep(0.5)
    return rows


# ---------------------------------------------------------------- excerpts

def _text_snippet(text: str, limit: int = 700) -> str:
    text = re.sub(r"\s+", " ", text).strip()
    # pdftotext often spaces out CJK characters; join them back
    text = re.sub(r"(?<=[\u3000-\u9fff\uff00-\uffef])\s+(?=[\u3000-\u9fff\uff00-\uffef])", "", text)
    m = re.search(STRONG_RE, text)
    if m:
        s = max(0, m.start() - 150)
        return text[s:s + limit]
    return text[:limit]


def fetch_excerpt(row: dict, session: requests.Session, max_bytes: int = 6_000_000) -> str:
    url = row["url"]
    headers = {"User-Agent": SEC_UA if "sec.gov" in url else BROWSER_UA}
    resp = _get(session, url, headers=headers, timeout=60)
    body = resp.content[:max_bytes]
    if url.lower().endswith(".pdf") or body[:4] == b"%PDF":
        if not shutil.which("pdftotext"):
            return ""
        with tempfile.NamedTemporaryFile(suffix=".pdf") as fh:
            fh.write(body)
            fh.flush()
            out = subprocess.run(["pdftotext", "-l", "4", "-enc", "UTF-8", fh.name, "-"],
                                 capture_output=True, timeout=60)
        return _text_snippet(out.stdout.decode("utf-8", "ignore"))
    html = body.decode(resp.encoding or "utf-8", "ignore")
    html = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", html)
    return _text_snippet(unescape(re.sub(r"<[^>]+>", " ", html)))


# ---------------------------------------------------------------- entry point

def _rank(row: dict) -> tuple:
    strong = 1 if re.search(STRONG_RE, row["title"] + " " + row.get("match", "")) else 0
    watch = 1 if row.get("match") == "watchlist" else 0
    return (-strong, -watch, row.get("date") or "")


def to_source_row(row: dict) -> dict:
    label = {"SEC EDGAR": "美国 SEC", "HKEX": "港交所披露易", "cninfo（沪深）": "巨潮资讯"}.get(row["exchange"], row["exchange"])
    summary = f"{row['company']}（{row['ticker']}，{row['exchange']}）{row['form']}：{row['title']}"
    if row.get("excerpt"):
        summary += f"。摘录：{row['excerpt']}"
    return {
        "source": f"{row['company']} {row['form']}（{label}）",
        "kind": "filing",
        "title": row["title"],
        "url": row["url"],
        "date": row["date"],
        "summary": summary[:900],
        "company": row["company"],
        "ticker": row["ticker"],
        "exchange": row["exchange"],
    }


def fetch_filings(config: dict, start: datetime, end: datetime, session: requests.Session | None = None) -> tuple[list[dict], list[dict]]:
    """Return (rows, stats). rows are run_weekly-style source rows with kind='filing'."""
    cfg = config.get("filings") or {}
    if not cfg.get("enabled", False):
        return [], []
    session = session or requests.Session()
    fetchers = {"edgar": fetch_edgar, "hkex": fetch_hkex, "cninfo": fetch_cninfo}
    raw: list[dict] = []
    stats: list[dict] = []
    for key, fn in fetchers.items():
        sub = cfg.get(key) or {}
        if sub.get("enabled", True) is False:
            stats.append({"name": f"公告·{key}", "status": "disabled", "count": 0})
            continue
        logging.info("抓取公告 %s", key)
        try:
            got = fn(sub, start, end, session)
            stats.append({"name": f"公告·{key}", "status": "ok", "count": len(got)})
            raw.extend(got)
        except Exception:
            logging.exception("公告来源失败：%s", key)
            stats.append({"name": f"公告·{key}", "status": "failed", "count": 0})
    seen, uniq = set(), []
    for row in sorted(raw, key=_rank):
        if row["url"] in seen:
            continue
        seen.add(row["url"])
        uniq.append(row)
    cap = int(cfg.get("max_items") or 12)
    per = int(cfg.get("max_per_exchange") or 5)
    kept, used = [], {}
    for row in uniq:
        ex = row["exchange"]
        if used.get(ex, 0) >= per:
            continue
        used[ex] = used.get(ex, 0) + 1
        kept.append(row)
        if len(kept) >= cap:
            break
    if cfg.get("fetch_excerpts", True):
        for row in kept:
            try:
                row["excerpt"] = fetch_excerpt(row, session)
            except Exception as e:
                logging.info("  摘录失败 %s：%s", row["url"], e)
            time.sleep(0.2)
    logging.info("公告合计 %d 条，去重后 %d 条，送筛选 %d 条", len(raw), len(uniq), len(kept))
    return [to_source_row(r) for r in kept], stats
