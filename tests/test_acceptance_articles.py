"""TheraSik InLight — FIXED acceptance suite for the article-depth pipeline (PR #5).

Every case runs the REAL ``run_weekly.main()`` with ``--dry-run --use-new-pipeline`` in a temporary ROOT.
Only the network edge is replaced:

* HTTP: ``urllib.request.OpenerDirector.open`` (urlopen, feedparser) and ``requests.Session.request``
  answer from recorded real payloads (RSS feeds, PubMed esearch/esummary/efetch, Europe PMC, Nature
  abstract pages, bioRxiv details API) rebuilt from the captured server runs in ``weeks/*.json``.
* Anthropic: ``anthropic.Anthropic`` returns REAL ``anthropic.types.Message`` objects whose ``tool_use``
  inputs are the REAL captured replies in ``recorded_claude/*.json`` (plus the explicit, documented
  mutations declared per case in ``cases.json``).
* OpenAI images: ``openai.OpenAI().images.generate`` returns a real ``ImagesResponse`` with a 1x1 PNG.
* ``time.sleep`` is a no-op. Any real socket connect / DNS lookup is recorded and FAILS the test.

Outcomes are read only from what ``main()`` writes: ``preview/weekly/<date>/articles.json``,
``deals.json``, ``wechat/article.html`` and ``logs/*.log``.

The expectations in ``acceptance_articles_fixtures/`` are audited ground truth and NON-NEGOTIABLE:
fix the production code, never the expectations, the fixtures or this harness.

Run from the repo root:  ``pytest tests/test_acceptance_articles.py``
"""
from __future__ import annotations

import base64
import copy
import email.message
import hashlib
import html as html_lib
import importlib
import io
import json
import logging
import re
import socket
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import urllib.response
from email.utils import format_datetime
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from xml.sax.saxutils import escape as xml_escape

import pytest

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parent
FIX = HERE / "acceptance_articles_fixtures"
_CASES_DOC = json.loads((FIX / "cases.json").read_text(encoding="utf-8"))
CASES: list[dict] = _CASES_DOC["cases"]
TRIAGE_META: dict = _CASES_DOC["triage_meta"]
PNG_B64 = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg=="

HAN = re.compile(r"[\u4e00-\u9fff]")
BRIEF_MIN_HAN = 450
DEEP_MAX_HAN = 1900
LENGTH_FIELDS = ("one_liner", "background", "design", "results", "mechanism", "significance", "limitations")
NOT_VISIBLE = {"id", "url", "img", "source_trace", "tags", "f", "ds", "disp", "tier", "evidence_level",
               "data_points", "image_prompt", "source", "date", "field"}
WRONG_NAMES = ("美康酸", "梅萨康酸", "梅沙康酸")
REASON_KEYWORDS = {
    "invented_number": ("未找到", "not found", "编造", "无法回溯", "invent", "fabricat", "不在原文", "not in source", "not in the source"),
    "meaning": ("含义", "meaning", "语义", "mismatch"),
    "meaning_or_invented": ("含义", "meaning", "语义", "mismatch", "未找到", "not found", "编造", "无法回溯", "invent"),
    "name": ("人名", "作者", "name", "未出现", "not in source", "编造", "无法回溯", "invent"),
    "length": ("字数", "汉字", "过短", "过长", "上限", "下限", "length", "too short", "too long", "chars", "characters"),
}
_SOURCE_NUM = re.compile(r"(?<![A-Za-z0-9.\-])\d+(?:[.,]\d+)?(?![A-Za-z0-9])")


def _load(rel: str) -> Any:
    return json.loads((FIX / rel).read_text(encoding="utf-8"))


_CACHE: dict = {}


def _week(name: str) -> dict:
    if ("week", name) not in _CACHE:
        _CACHE[("week", name)] = _load(f"weeks/{name}.json")
    return _CACHE[("week", name)]


def _rec(run: str) -> list[dict]:
    if ("rec", run) not in _CACHE:
        _CACHE[("rec", run)] = _load(f"recorded_claude/{run}.json")["calls"]
    return _CACHE[("rec", run)]


def _canon(url: str) -> str:
    return (url or "").split("?")[0].split("#")[0].rstrip("/").lower()


# =========================================================================== Claude replies
def _anthropic_message(content: list[dict], stop_reason: str, usage: dict | None = None,
                       model: str = "claude-sonnet-5-5"):
    """A REAL anthropic.types.Message (exactly what messages.create returns)."""
    from anthropic.types import Message

    u = dict(usage or {})
    u = {"input_tokens": int(u.get("input_tokens") or 0), "output_tokens": int(u.get("output_tokens") or 0),
         **{k: v for k, v in u.items() if k in ("cache_creation_input_tokens", "cache_read_input_tokens") and v is not None}}
    return Message.model_validate({"id": "msg_acceptance", "type": "message", "role": "assistant", "model": model,
                                   "content": content, "stop_reason": stop_reason, "stop_sequence": None, "usage": u})


def _walk(obj: Any, path: list):
    for p in path[:-1]:
        obj = obj[p]
    return obj, path[-1]


def apply_mutations(call: dict, muts: list[dict]) -> tuple[list[dict], str]:
    """Apply the declared mutations of cases.json to a recorded reply. Fails loudly if a mutation no longer applies."""
    content = copy.deepcopy(call["content"])
    stop = call["stop_reason"]
    tu = [b for b in content if b["type"] == "tool_use"]
    for m in muts or []:
        op = m["op"]
        if op == "text_only":
            content = [{"type": "text", "text": m["text"]}]
            stop = "end_turn"
            tu = []
            continue
        if op == "stop_reason":
            stop = m["value"]
            continue
        inp = tu[-1]["input"]
        if op == "set_input":
            tu[-1]["input"] = copy.deepcopy(m["value"])
        elif op == "set":
            parent, key = _walk(inp, m["path"])
            parent[key] = copy.deepcopy(m["value"])
        elif op == "delete":
            parent, key = _walk(inp, m["path"])
            del parent[key]
        elif op == "list_append":
            parent, key = _walk(inp, m["path"])
            parent[key] = list(parent[key]) + [m["value"]]
        elif op in ("replace", "append"):
            parent, key = _walk(inp, m["path"])
            val = parent[key]
            assert isinstance(val, str), f"mutation {m} targets non-string"
            if op == "append":
                parent[key] = val + m["text"]
            else:
                assert m["old"] in val, f"mutation {m} no longer applies"
                parent[key] = val.replace(m["old"], m["new"])
        elif op == "replace_everywhere":
            hits = [0]

            def rep(v):
                if isinstance(v, str):
                    hits[0] += v.count(m["old"])
                    return v.replace(m["old"], m["new"])
                if isinstance(v, list):
                    return [rep(x) for x in v]
                if isinstance(v, dict):
                    return {k: rep(x) for k, x in v.items()}
                return v
            tu[-1]["input"] = rep(inp)
            assert hits[0], f"mutation {m} no longer applies"
        else:
            raise AssertionError(f"unknown mutation op {op}")
    return content, stop


def recorded_reply(ref: dict):
    call = _rec(ref["rec"])[ref["i"]]
    content, stop = apply_mutations(call, ref.get("mut") or [])
    return _anthropic_message(content, stop, call.get("usage"), call.get("model") or "claude-sonnet-5-5")


# =========================================================================== network mirror
def _rfc822(day: str) -> str:
    return format_datetime(datetime.strptime(day, "%Y-%m-%d").replace(hour=12, tzinfo=timezone.utc), usegmt=True)


def build_rss(source_name: str, items: list[dict]) -> bytes:
    parts = ['<?xml version="1.0" encoding="UTF-8"?>', '<rss version="2.0"><channel>',
             f"<title>{xml_escape(source_name)}</title><link>https://example.invalid/</link><description>feed</description>"]
    for it in items:
        r = it["row"]
        parts.append("<item>"
                     f"<title>{xml_escape(r['title'])}</title>"
                     f"<link>{xml_escape(r['url'])}</link>"
                     f"<guid isPermaLink=\"false\">{xml_escape(r['url'])}</guid>"
                     f"<description>{xml_escape(r['summary'])}</description>"
                     f"<pubDate>{_rfc822(r['date'])}</pubDate>"
                     "</item>")
    parts.append("</channel></rss>")
    return "".join(parts).encode("utf-8")


def build_efetch(items: list[dict]) -> bytes:
    out = ['<?xml version="1.0" ?>', "<PubmedArticleSet>"]
    for it in items:
        es = it["esummary"]
        out.append(f"<PubmedArticle><MedlineCitation><PMID>{es['uid']}</PMID><Article><Journal><Title>"
                   f"{xml_escape(es['fulljournalname'])}</Title></Journal><ArticleTitle>{xml_escape(es['title'])}"
                   "</ArticleTitle><Abstract>")
        for p in it.get("efetch_parts") or []:
            lab = f' Label="{xml_escape(p["label"])}"' if p["label"] else ""
            out.append(f"<AbstractText{lab}>{xml_escape(p['text'])}</AbstractText>")
        out.append("</Abstract></Article></MedlineCitation></PubmedArticle>")
    out.append("</PubmedArticleSet>")
    return "".join(out).encode("utf-8")


def build_nature_html(abstract: str) -> bytes:
    return ('<!DOCTYPE html><html><head><title>Nature</title></head><body><section aria-labelledby="Abs1">'
            f'<div class="c-article-section__content" id="Abs1-content"><p>{html_lib.escape(abstract, quote=False)}</p></div>'
            "</section></body></html>").encode("utf-8")


class _Mirror:
    """Answers HTTP from the recorded week payloads restricted to the case's items."""

    def __init__(self, week: dict, urls: set[str] | None, feeds: dict[str, str]):
        self.items = [it for it in week["items"] if urls is None or it["row"]["url"] in urls]
        self.feeds = feeds  # feed url -> source name
        self.hits: list[str] = []
        self.unexpected: list[str] = []

    def _items_for(self, source: str) -> list[dict]:
        return [it for it in self.items if it["row"]["source"] == source]

    def route(self, url: str) -> tuple[int, bytes, str]:
        self.hits.append(url)
        if url in self.feeds or urllib.parse.unquote(url) in {urllib.parse.unquote(f) for f in self.feeds}:
            name = self.feeds.get(url) or {urllib.parse.unquote(f): n for f, n in self.feeds.items()}[urllib.parse.unquote(url)]
            return 200, build_rss(name, self._items_for(name)), "application/rss+xml"
        u = urllib.parse.urlsplit(url)
        q = urllib.parse.parse_qs(u.query)
        pub = [it for it in self.items if "esummary" in it]
        if u.netloc == "eutils.ncbi.nlm.nih.gov" and u.path.endswith("esearch.fcgi"):
            ids = [it["esummary"]["uid"] for it in pub]
            return 200, json.dumps({"header": {"type": "esearch"}, "esearchresult": {
                "count": str(len(ids)), "retmax": str(len(ids)), "retstart": "0", "idlist": ids}}).encode(), "application/json"
        if u.netloc == "eutils.ncbi.nlm.nih.gov" and u.path.endswith("esummary.fcgi"):
            want = ",".join(q.get("id", [""])).split(",")
            res: dict = {"uids": []}
            for it in pub:
                if it["esummary"]["uid"] in want:
                    res["uids"].append(it["esummary"]["uid"])
                    res[it["esummary"]["uid"]] = it["esummary"]
            return 200, json.dumps({"header": {"type": "esummary"}, "result": res}).encode(), "application/json"
        if u.netloc == "eutils.ncbi.nlm.nih.gov" and u.path.endswith("efetch.fcgi"):
            want = ",".join(q.get("id", [""])).split(",")
            return 200, build_efetch([it for it in pub if it["esummary"]["uid"] in want and it.get("efetch_parts")]), "text/xml"
        if u.netloc == "www.ebi.ac.uk" and "/europepmc/webservices/rest/search" in u.path:
            m = re.search(r'DOI:"([^"]+)"', q.get("query", [""])[0])
            doi = (m.group(1) if m else "").lower()
            hits = [it["epmc_hit"] for it in self.items if it.get("epmc_hit") and it["epmc_hit"]["doi"].lower() == doi]
            return 200, json.dumps({"version": "6.9", "hitCount": len(hits), "resultList": {"result": hits[:1]}}).encode(), "application/json"
        if u.netloc == "www.ebi.ac.uk" and u.path.endswith("/fullTextXML"):
            return 404, b"", "text/plain"
        if u.netloc.endswith("nature.com") and u.path.startswith("/articles/"):
            for it in self.items:
                if _canon(it["row"]["url"]) == _canon(url) and it.get("nature_abstract"):
                    return 200, build_nature_html(it["nature_abstract"]), "text/html"
            return 404, b"", "text/html"
        if u.netloc == "api.biorxiv.org":
            for it in self.items:
                if it.get("biorxiv_abstract") and it["row"]["url"].split("/content/")[-1].split("v")[0] in url:
                    return 200, json.dumps({"collection": [{"abstract": it["biorxiv_abstract"]}]}).encode(), "application/json"
            return 200, json.dumps({"messages": [{"status": "no posts found"}], "collection": []}).encode(), "application/json"
        self.unexpected.append(url)
        return 404, b"", "text/plain"


# =========================================================================== harness
class _Harness:
    """Fresh production modules, network mirror, recorded Claude replies, temp ROOT — per test."""

    def __init__(self, case: dict, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, new_pipeline: bool = True,
                 digest: dict | None = None):
        self.case = case
        self.mp = monkeypatch
        self.root = tmp_path / "site"
        self.new_pipeline = new_pipeline
        self.digest = digest
        self.claude_calls: list[dict] = []
        self.network_attempts: list[str] = []
        self.unexpected_claude: list[str] = []
        self.article_counts: dict[str, int] = {}
        replay = case.get("replay")
        week = _week(case["week"])
        if replay:
            self.urls = None
            self.queues = replay["queues"]
            self.items_cfg = []
        else:
            self.items_cfg = case.get("items") or []
            self.urls = {i["url"] for i in self.items_cfg} if self.items_cfg else None
            self.queues = {i["url"]: i["queue"] for i in self.items_cfg}
        self.week = week
        self.by_url = {it["row"]["url"]: it for it in week["items"]}

    # ------------------------------------------------------------------ setup
    def setup(self):
        mp = self.mp
        # No real network: any socket connect / DNS lookup is recorded and refused.
        def _no_connect(sock, address, *a, **k):
            self.network_attempts.append(repr(address))
            raise OSError(f"acceptance tests: network disabled ({address!r})")

        def _no_dns(host, *a, **k):
            self.network_attempts.append(f"DNS {host!r}")
            raise OSError(f"acceptance tests: network disabled (DNS {host!r})")

        def _no_create(address, *a, **k):
            self.network_attempts.append(f"create_connection {address!r}")
            raise OSError("acceptance tests: network disabled")
        mp.setattr(socket.socket, "connect", _no_connect)
        mp.setattr(socket.socket, "connect_ex", _no_connect)
        mp.setattr(socket, "create_connection", _no_create)
        mp.setattr(socket, "getaddrinfo", _no_dns)
        mp.setattr(socket, "gethostbyname", _no_dns)
        mp.setattr(time, "sleep", lambda *_a, **_k: None)

        # Temp ROOT: fixture sources.yaml (window widened so recorded dates stay inside it), empty latest.json, no index.html.
        (self.root / "content").mkdir(parents=True)
        (self.root / "sources.yaml").write_text((FIX / "sources.yaml").read_text(encoding="utf-8"), encoding="utf-8")
        (self.root / "content" / "latest.json").write_text(json.dumps({"generated": "", "articles": [], "deals": []}),
                                                           encoding="utf-8")
        import yaml
        cfg = yaml.safe_load((FIX / "sources.yaml").read_text(encoding="utf-8"))
        feeds = {s["feed"]: s["name"] for s in cfg["sources"] if s.get("feed")}
        self.mirror = _Mirror(self.week, self.urls, feeds)

        # HTTP edge: urllib (urlopen + feedparser) and requests.
        harness = self

        def _open(opener, fullurl, data=None, timeout=None, *a, **k):
            url = fullurl.full_url if isinstance(fullurl, urllib.request.Request) else str(fullurl)
            code, body, ctype = harness.mirror.route(url)
            hdrs = email.message.Message()
            hdrs["Content-Type"] = ctype
            hdrs["Content-Length"] = str(len(body))
            if code >= 400:
                raise urllib.error.HTTPError(url, code, "Not Found", hdrs, io.BytesIO(body))
            return urllib.response.addinfourl(io.BytesIO(body), hdrs, url, code)
        mp.setattr(urllib.request.OpenerDirector, "open", _open)

        import requests
        import requests.structures

        def _request(session, method, url, *a, **k):
            code, body, ctype = harness.mirror.route(url)
            r = requests.models.Response()
            r.status_code = code
            r._content = body
            r.headers = requests.structures.CaseInsensitiveDict({"Content-Type": ctype})
            r.url = url
            r.encoding = "utf-8"
            r.reason = "OK" if code < 400 else "Not Found"
            r.request = requests.Request(method, url).prepare()
            return r
        mp.setattr(requests.sessions.Session, "request", _request)

        # Anthropic: real Message objects built from recorded replies.
        import anthropic

        class _FakeMessages:
            def create(self, *a, **kw):
                return harness._claude(kw)

        class _FakeAnthropic:
            def __init__(self, *a, **k):
                self.messages = _FakeMessages()

            def with_options(self, *a, **k):
                return self
        mp.setattr(anthropic, "Anthropic", _FakeAnthropic)
        mp.setattr(anthropic, "Client", _FakeAnthropic, raising=False)
        mp.setattr(anthropic.resources.messages.Messages, "create", lambda _self, *a, **kw: harness._claude(kw))

        # OpenAI images: real ImagesResponse with a 1x1 PNG.
        import openai
        from openai.types import ImagesResponse

        class _FakeImages:
            def generate(self, *a, **kw):
                harness.claude_calls.append({"tool": "<openai.images.generate>"})
                return ImagesResponse.model_validate({"created": 0, "data": [{"b64_json": PNG_B64}]})

        class _FakeOpenAI:
            def __init__(self, *a, **k):
                self.images = _FakeImages()
        mp.setattr(openai, "OpenAI", _FakeOpenAI)
        mp.setattr(openai, "Client", _FakeOpenAI, raising=False)

        for k, v in {"ANTHROPIC_API_KEY": "test-not-a-key", "OPENAI_API_KEY": "test-not-a-key",
                     "ANTHROPIC_MODEL": "claude-sonnet-5-5"}.items():
            mp.setenv(k, v)
        for k in ("ANTHROPIC_TRIAGE_MODEL", "OPENAI_IMAGE_MODEL", "ANTHROPIC_BASE_URL", "OPENAI_BASE_URL"):
            mp.delenv(k, raising=False)
        argv = ["run_weekly.py", "--dry-run"] + (["--use-new-pipeline"] if self.new_pipeline else [])
        mp.setattr(sys, "argv", argv)
        mp.chdir(self.root)

        # Fresh production modules (imported AFTER the network edge is patched).
        for name, mod in list(sys.modules.items()):
            f = getattr(mod, "__file__", None) or ""
            try:
                inside = Path(f).resolve().is_relative_to(REPO_ROOT) and not Path(f).resolve().is_relative_to(HERE)
            except (OSError, ValueError):
                inside = False
            if inside or name in ("run_weekly", "inlight_articles"):
                mp.delitem(sys.modules, name, raising=False)
        mp.syspath_prepend(str(REPO_ROOT))
        rw = importlib.import_module("run_weekly")
        assert Path(rw.__file__).resolve() == (REPO_ROOT / "run_weekly.py").resolve(), rw.__file__
        self.rw = rw
        for name, mod in list(sys.modules.items()):
            f = getattr(mod, "__file__", None) or ""
            if f and Path(f).resolve().parent == REPO_ROOT and isinstance(getattr(mod, "ROOT", None), Path):
                mp.setattr(mod, "ROOT", self.root)

    # ------------------------------------------------------------------ Claude router
    def _prompt_of(self, kw: dict) -> str:
        parts = []
        if isinstance(kw.get("system"), str):
            parts.append(kw["system"])
        for msg in kw.get("messages") or []:
            c = msg.get("content")
            if isinstance(c, str):
                parts.append(c)
            elif isinstance(c, list):
                for b in c:
                    if isinstance(b, dict) and isinstance(b.get("text"), str):
                        parts.append(b["text"])
        return "\n".join(parts)

    def _match_url(self, prompt: str) -> str | None:
        cands = list(self.queues)
        hit = [u for u in cands if u in prompt]
        if not hit:
            hit = [u for u in cands if _canon(u) in prompt.lower()]
        if not hit:
            hit = [u for u in cands if self.by_url.get(u) and self.by_url[u]["row"]["title"] in prompt]
        return max(hit, key=len) if hit else None

    def _claude(self, kw: dict):
        tools = [t.get("name") for t in (kw.get("tools") or []) if isinstance(t, dict)]
        prompt = self._prompt_of(kw)
        n = len(self.claude_calls)
        rec: dict = {"tools": tools, "prompt": prompt, "model": kw.get("model"), "max_tokens": kw.get("max_tokens"),
                     "tool_choice": kw.get("tool_choice"), "tool_schemas": kw.get("tools")}
        self.claude_calls.append(rec)
        if "test_tool" in tools:
            rec["tool"] = "test_tool"
            return _anthropic_message([{"type": "tool_use", "id": f"toolu_check_{n}", "name": "test_tool",
                                        "input": {"ok": True}}], "tool_use", {"input_tokens": 30, "output_tokens": 10})
        if "submit_triage" in tools:
            rec["tool"] = "submit_triage"
            replay = self.case.get("replay")
            if replay:
                return recorded_reply({"rec": replay["rec"], "i": replay["triage_i"]})
            sels = []
            for it in self.items_cfg:
                if it["url"] in prompt or _canon(it["url"]) in prompt.lower():
                    meta = TRIAGE_META.get(it["url"], {})
                    sels.append({"url": it["url"], "tier": it["tier"], "field": meta.get("field", "c3"),
                                 "reason": meta.get("reason", "")})
            return _anthropic_message([{"type": "tool_use", "id": f"toolu_triage_{n}", "name": "submit_triage",
                                        "input": {"selections": sels}}], "tool_use", {"input_tokens": 9000, "output_tokens": 900})
        if "submit_article" in tools:
            rec["tool"] = "submit_article"
            url = self._match_url(prompt)
            rec["url"] = url
            if url is None:
                self.unexpected_claude.append("submit_article for an unknown item")
                return _anthropic_message([{"type": "text", "text": "no fixture"}], "end_turn")
            k = self.article_counts.get(url, 0)
            self.article_counts[url] = k + 1
            q = self.queues[url]
            ref = q[min(k, len(q) - 1)]
            rec["served"] = ref
            return recorded_reply(ref)
        if "submit_weekly_digest" in tools:
            rec["tool"] = "submit_weekly_digest"
            replay = self.case.get("replay")
            if self.digest is not None:
                d = self.digest
                return _anthropic_message(copy.deepcopy(d["content"]), d["stop_reason"], d.get("usage"))
            if replay:
                return recorded_reply({"rec": replay["rec"], "i": replay["digest_i"]})
            return _anthropic_message([{"type": "tool_use", "id": f"toolu_digest_{n}", "name": "submit_weekly_digest",
                                        "input": {"articles": [], "deals": []}}], "tool_use")
        rec["tool"] = None
        self.unexpected_claude.append(f"unexpected Claude call with tools {tools}")
        return _anthropic_message([{"type": "text", "text": "unsupported in acceptance harness"}], "end_turn")

    # ------------------------------------------------------------------ run
    def run(self) -> dict:
        root_logger = logging.getLogger()
        saved = (root_logger.handlers[:], root_logger.level)
        root_logger.handlers = []          # let production's logging.basicConfig attach its own file handler
        code = 0
        try:
            self.rw.main()                 # <- the REAL production entry point; non-SystemExit exceptions fail the test
        except SystemExit as e:
            code = e.code if isinstance(e.code, int) else (0 if e.code is None else 1)
        finally:
            for h in root_logger.handlers:
                try:
                    h.close()
                except Exception:
                    pass
            root_logger.handlers, _lvl = saved
            root_logger.setLevel(saved[1])
        out: dict[str, Any] = {"exit": code, "dir": None, "articles": None, "deals": None, "wechat": None,
                               "articles_raw": None, "wechat_raw": None}
        weekly = self.root / "preview" / "weekly"
        dirs = sorted(p for p in weekly.glob("*") if p.is_dir()) if weekly.exists() else []
        if dirs:
            w = dirs[-1]
            out["dir"] = w
            if (w / "articles.json").exists():
                out["articles_raw"] = (w / "articles.json").read_text(encoding="utf-8")
                out["articles"] = json.loads(out["articles_raw"])
            if (w / "deals.json").exists():
                out["deals"] = json.loads((w / "deals.json").read_text(encoding="utf-8"))
            if (w / "wechat" / "article.html").exists():
                out["wechat_raw"] = (w / "wechat" / "article.html").read_text(encoding="utf-8")
                out["wechat"] = html_lib.unescape(out["wechat_raw"])
        logs = sorted((self.root / "logs").glob("*.log")) if (self.root / "logs").exists() else []
        out["log"] = "\n".join(p.read_text(encoding="utf-8", errors="replace") for p in logs)
        out["log_files"] = logs
        return out


# =========================================================================== helpers for expectations
def _strings(v: Any):
    if isinstance(v, str):
        yield v
    elif isinstance(v, list):
        for x in v:
            yield from _strings(x)
    elif isinstance(v, dict):
        for x in v.values():
            yield from _strings(x)


def visible_text(art: dict) -> str:
    return "\n".join(s for k, v in art.items() if k not in NOT_VISIBLE for s in _strings(v))


def han_len(art: dict) -> int:
    return sum(len(HAN.findall(s)) for k in LENGTH_FIELDS for s in _strings(art.get(k)))


def _find(arts: list[dict], url: str) -> dict | None:
    for a in arts or []:
        if a.get("url") == url:
            return a
    for a in arts or []:
        if _canon(a.get("url", "")) == _canon(url):
            return a
    return None


def _source_abstract(item: dict) -> str:
    if item.get("epmc_hit") and item["epmc_hit"].get("abstractText"):
        return item["epmc_hit"]["abstractText"]
    if item.get("nature_abstract"):
        return item["nature_abstract"]
    if item.get("efetch_parts"):
        return " ".join(p["text"] for p in item["efetch_parts"])
    if item.get("biorxiv_abstract"):
        return item["biorxiv_abstract"]
    return ""


def _dedup_snippet(item: dict) -> str | None:
    """A 60-char window present verbatim in BOTH the RSS summary and the fetched abstract."""
    ab, rss = _source_abstract(item), item["row"]["summary"]
    for start in range(0, max(1, len(ab) - 60), 10):
        s = ab[start:start + 60]
        if "<" not in s and ">" not in s and s in rss:
            return s
    return None


def _num_tokens(text: str) -> set[str]:
    return {t.replace(",", "") for t in _SOURCE_NUM.findall(text or "")}


def _short(a: dict | None) -> str:
    return "—" if not a else f"{a.get('tier')} {han_len(a)}汉字 «{str(a.get('t'))[:40]}»"


def _invariants(h: _Harness, out: dict) -> list[str]:
    """Rules every published article must satisfy, in every case."""
    errs = []
    for a in out["articles"] or []:
        url = a.get("url", "")
        tag = f"[{_canon(url)[-28:]}]"
        vis = visible_text(a)
        tier = a.get("tier")
        n = han_len(a)
        if tier not in ("deep", "brief"):
            errs.append(f"{tag} published article has no tier (got {tier!r})")
        if tier == "brief" and n < BRIEF_MIN_HAN:
            errs.append(f"{tag} brief published with {n} Chinese characters (< {BRIEF_MIN_HAN})")
        if tier == "deep" and n > DEEP_MAX_HAN:
            errs.append(f"{tag} deep published with {n} Chinese characters (> {DEEP_MAX_HAN})")
        if "未给出" in vis:
            ctx = vis[max(0, vis.find("未给出") - 30):vis.find("未给出") + 10].replace("\n", " ")
            errs.append(f"{tag} published text contains 未给出 (must be omitted): …{ctx}…")
        if any("\\n" in s for s in _strings(a)):
            errs.append(f"{tag} literal backslash-n in articles.json")
        for w in WRONG_NAMES:
            if w in vis:
                errs.append(f"{tag} wrong compound name {w} (mesaconate = 中康酸)")
        item = h.by_url.get(url) or next((it for u, it in h.by_url.items() if _canon(u) == _canon(url)), None)
        if item:
            src_nums = _num_tokens(_source_abstract(item))
            res = "\n".join(_strings(a.get("results")))
            if src_nums and not (_num_tokens(res) & src_nums):
                errs.append(f"{tag} results contain no number from the source (source has {sorted(src_nums)[:8]})")
            if item.get("esummary"):
                jn = item["esummary"]["fulljournalname"]
                if jn.lower() not in str(a.get("j", "")).lower():
                    errs.append(f"{tag} PubMed journal must come from the RSS item ({jn!r}); got {a.get('j')!r}")
    if out["wechat"] is not None:
        if "\\n" in out["wechat"]:
            errs.append("WeChat HTML contains a literal backslash-n")
        if "未给出" in out["wechat"]:
            errs.append("WeChat HTML contains 未给出")
        for w in WRONG_NAMES:
            if w in out["wechat"]:
                errs.append(f"WeChat HTML contains wrong compound name {w}")
    return errs


def _log_lines_for(out: dict, h: _Harness, url: str) -> str:
    keys = {url, _canon(url)}
    row = h.by_url.get(url)
    if row:
        keys.add(row["row"]["title"][:50])
    return "\n".join(l for l in out["log"].splitlines() if any(k and k.lower() in l.lower() for k in keys))


def _check_item(h: _Harness, out: dict, it: dict) -> list[str]:
    e = it["expect"]
    url = it["url"]
    tag = f"[{_canon(url)[-28:]}]"
    errs = []
    a = _find(out["articles"], url)
    vis = visible_text(a) if a else ""
    wx = out["wechat"] or ""
    if e["outcome"] == "publish":
        if a is None:
            served = [f"{c['served']['rec']}#{c['served']['i']}{'+mut' if c['served'].get('mut') else ''}"
                      for c in h.claude_calls if c.get("url") == url and c.get("served")]
            errs.append(f"{tag} expected PUBLISH, but it was not published (drafts served: {served})")
        else:
            if e.get("tier") and a.get("tier") != e["tier"]:
                errs.append(f"{tag} expected tier {e['tier']}, got {a.get('tier')!r}")
            for s in e.get("required") or []:
                if s not in vis:
                    errs.append(f"{tag} required text missing: {s!r}  (published: {_short(a)})")
            res = "\n".join(_strings(a.get("results")))
            for s in e.get("required_in_results") or []:
                if s not in res:
                    errs.append(f"{tag} results must contain {s!r}")
            if e.get("journal") and e["journal"].lower() not in str(a.get("j", "")).lower():
                errs.append(f"{tag} journal must be {e['journal']!r}, got {a.get('j')!r}")
    else:
        if a is not None:
            errs.append(f"{tag} expected DROP, but it was published: {_short(a)}")
        reason = e.get("reason")
        if reason:
            lines = _log_lines_for(out, h, url)
            if not any(k.lower() in lines.lower() for k in REASON_KEYWORDS[reason]):
                errs.append(f"{tag} drop reason class {reason!r} not found in logs/*.log lines for this item")
    for s in e.get("forbidden") or []:
        if s in vis or (a is not None and s in wx) or (e["outcome"] == "drop" and s in wx):
            errs.append(f"{tag} forbidden text present: {s!r}")
    return errs


def _common(h: _Harness, out: dict) -> list[str]:
    errs = []
    if h.network_attempts:
        errs.append(f"REAL network attempted: {h.network_attempts[:5]}")
    if out["exit"] != 0:
        errs.append(f"main() exited with code {out['exit']} (expected a clean run, exit 0); log tail: "
                    f"{out['log'][-600:]!r}")
    if out["articles"] is None or out["wechat"] is None:
        errs.append("main() did not write articles.json and wechat/article.html")
    if not any(c.get("tool") == "test_tool" for c in h.claude_calls):
        errs.append("guard: the real main() model check (test_tool) never ran")
    if not out["log_files"]:
        errs.append("guard: main() wrote no log file under ROOT/logs")
    if h.unexpected_claude:
        errs.append(f"unexpected Claude traffic: {h.unexpected_claude[:3]}")
    return errs


def run_case(case: dict, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[_Harness, dict, list[str]]:
    h = _Harness(case, tmp_path, monkeypatch)
    h.setup()
    out = h.run()
    errs = _common(h, out)
    if out["articles"] is not None:
        errs += _invariants(h, out)
    if case.get("replay"):
        ex = case["expect_replay"]
        if out["deals"] is not None and len(out["deals"]) != ex["deals"]:
            errs.append(f"expected {ex['deals']} deals, got {len(out['deals'])}")
        for e in ex["articles"]:
            errs += _check_item(h, out, {"url": e["url"], "expect": e})
    else:
        for it in case["items"]:
            errs += _check_item(h, out, it)
    if case.get("dedup"):
        for c in h.claude_calls:
            if c.get("tool") != "submit_article" or not c.get("url"):
                continue
            item = h.by_url[c["url"]]
            snippet = _dedup_snippet(item)
            assert snippet, "fixture: no common RSS/abstract snippet for the dedup check"
            cnt = c["prompt"].count(snippet)
            if cnt != 1:
                errs.append(f"[{_canon(c['url'])[-28:]}] abstract appears {cnt}x in the article prompt (must be exactly once)")
    return h, out, errs


@pytest.mark.parametrize("case", CASES, ids=[c["id"] for c in CASES])
def test_acceptance(case, tmp_path, monkeypatch):
    h, out, errs = run_case(case, tmp_path, monkeypatch)
    assert not errs, f"{case['id']} — {case['why']}\n  - " + "\n  - ".join(errs)


# =========================================================================== legacy path == main
def _norm_schema(v: Any) -> Any:
    if isinstance(v, dict):
        return {k: (sorted(x) if k == "enum" and isinstance(x, list) else _norm_schema(x)) for k, x in v.items()}
    if isinstance(v, list):
        return [_norm_schema(x) for x in v]
    return v


def observe_legacy(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict:
    """Run main() WITHOUT --use-new-pipeline on the full recorded week and capture prompt + output files."""
    case = {"id": "legacy", "week": "pr5f", "replay": None, "items": []}
    h = _Harness(case, tmp_path, monkeypatch, new_pipeline=False, digest=_load("recorded_claude/legacy_digest.json"))
    h.setup()
    out = h.run()
    week = out["dir"].name if out["dir"] else "<none>"

    def norm(s: str) -> str:
        return s.replace(week, "<WEEK>")
    digests = [c for c in h.claude_calls if c.get("tool") == "submit_weekly_digest"]
    obs = {"exit": out["exit"], "network_attempts": h.network_attempts,
           "test_tool_called": any(c.get("tool") == "test_tool" for c in h.claude_calls),
           "claude_tools_called": [c.get("tool") for c in h.claude_calls],
           "digest_requests": [{"prompt": norm(c["prompt"]), "model": c["model"], "max_tokens": c["max_tokens"],
                                "tool_choice": c["tool_choice"], "tools": _norm_schema(c["tool_schemas"])} for c in digests],
           "files": {}}
    if out["dir"]:
        for p in sorted(out["dir"].rglob("*")):
            if p.is_file():
                rel = p.relative_to(out["dir"]).as_posix()
                obs["files"][rel] = (norm(p.read_text(encoding="utf-8")) if p.suffix in (".json", ".html")
                                     else "sha256:" + hashlib.sha256(p.read_bytes()).hexdigest())
    return obs


def test_legacy_path_byte_identical_to_main(tmp_path, monkeypatch):
    base = _load("legacy_baseline_main.json")
    obs = observe_legacy(tmp_path, monkeypatch)
    errs = []
    if obs["network_attempts"]:
        errs.append(f"REAL network attempted: {obs['network_attempts'][:3]}")
    if obs["exit"] != base["observation"]["exit"]:
        errs.append(f"exit {obs['exit']} != main's {base['observation']['exit']}")
    if obs["claude_tools_called"] != base["observation"]["claude_tools_called"]:
        errs.append(f"Claude calls {obs['claude_tools_called']} != main's {base['observation']['claude_tools_called']}")
    bd, od = base["observation"]["digest_requests"], obs["digest_requests"]
    if len(bd) != len(od):
        errs.append(f"{len(od)} legacy digest requests != main's {len(bd)}")
    for i, (b, o) in enumerate(zip(bd, od)):
        for k in ("model", "max_tokens", "tool_choice", "tools"):
            if b[k] != o[k]:
                errs.append(f"digest request {i}: {k} differs from main")
        if b["prompt"] != o["prompt"]:
            j = next((x for x in range(min(len(b["prompt"]), len(o["prompt"]))) if b["prompt"][x] != o["prompt"][x]),
                     min(len(b["prompt"]), len(o["prompt"])))
            errs.append(f"digest request {i}: prompt differs from main ({len(o['prompt'])} vs {len(b['prompt'])} chars); "
                        f"first difference at {j}: main …{b['prompt'][max(0, j - 60):j + 60]!r}… / PR …{o['prompt'][max(0, j - 60):j + 60]!r}…")
    bf, of = base["observation"]["files"], obs["files"]
    if sorted(bf) != sorted(of):
        errs.append(f"output files {sorted(of)} != main's {sorted(bf)}")
    for name in sorted(set(bf) & set(of)):
        if bf[name] != of[name]:
            errs.append(f"output file {name} differs from main's")
    assert not errs, "legacy (no flag) path must be byte-identical to main:\n  - " + "\n  - ".join(errs)


# =========================================================================== 44 legacy pages keep main's headings
HEADINGS_JS = r"""()=>{const v=document.getElementById('v-paper'); if(!v) return null; const m=v.querySelector('.amain');
 if(!m) return null; return [...m.querySelectorAll(':scope > .sec h2, :scope > details summary')].map(h=>h.textContent.trim())}"""


def observe_headings(repo_root: Path, ids: list[str] | None = None) -> dict:
    from playwright.sync_api import sync_playwright  # required: missing Playwright is a FAILURE, not a skip

    url = (repo_root / "index.html").resolve().as_uri()
    out: dict = {}
    with sync_playwright() as p:
        b = p.chromium.launch()
        pg = b.new_page(viewport={"width": 1280, "height": 900})
        pg.route("**/*", lambda route: route.continue_() if route.request.url.startswith("file:") else route.abort())
        pg.goto(url + "#home")
        pg.wait_for_timeout(600)
        if ids is None:
            ids = pg.evaluate("()=>CAT.filter(a=>!a.deep && !a.datacard && !a.tier).map(a=>a.id)")
        for i in ids:
            pg.evaluate(f"()=>{{location.hash='#p-{i}'}}")
            pg.wait_for_timeout(40)
            out[i] = pg.evaluate(HEADINGS_JS)
        b.close()
    return out


def test_legacy_pages_keep_main_headings():
    base = _load("legacy_headings_main.json")
    expected: dict = base["pages"]
    got = observe_headings(REPO_ROOT, list(expected))
    bad = {i: (expected[i], got.get(i)) for i in expected if got.get(i) != expected[i]}
    assert not bad, (f"{len(bad)} of {len(expected)} legacy pages lost main's headings "
                     f"{base['canonical']}:\n" + "\n".join(f"  {i}: main={m} PR={g}" for i, (m, g) in list(bad.items())[:12]))


# =========================================================================== guard
def test_guard_main_really_runs(tmp_path, monkeypatch):
    case = next(c for c in CASES if c["id"] == "fp_cd6_runA_ge3_once_dcr")
    h, out, _errs = run_case(case, tmp_path, monkeypatch)
    assert Path(h.rw.main.__code__.co_filename).resolve() == (REPO_ROOT / "run_weekly.py").resolve()
    assert out["log_files"] and out["log"].strip(), "main() must write its own log under ROOT/logs"
    assert any(c.get("tool") == "test_tool" for c in h.claude_calls), "main()'s model check did not run"
    assert any(u in h.mirror.feeds for u in h.mirror.hits), "main() never fetched an RSS feed through the network edge"
    assert any("eutils.ncbi.nlm.nih.gov" in u for u in h.mirror.hits), "main() never queried PubMed"
    assert any(c.get("tool") == "submit_article" for c in h.claude_calls), "main() never drafted an article"
    assert out["dir"] is not None and (out["dir"] / "articles.json").exists(), "main() wrote no preview output"
    assert not h.network_attempts
