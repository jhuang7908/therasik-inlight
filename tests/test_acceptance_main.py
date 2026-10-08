"""TheraSik InLight — FIXED acceptance suite v2 for the SEC-deal pipeline (PR #7, precision-first scope).

Scope (owner's precision-first rule): the deals section publishes ONLY (a) newly entered license or
collaboration agreements and (b) definitive acquisition or merger agreements. Loans / credit facilities, ATMs,
equity offerings, warrants, PIPEs, amendments, terminations, assignments and divestitures are out of scope and
are never published. Share / warrant components are never rendered.

Every published deal must have passed the independent second Claude call (tool ``verify_deal``), which FAILS
CLOSED. The suite runs with the verifier ENABLED: it fails if a verifier kill-switch (INLIGHT_NO_VERIFIER, …) is set
in the environment, if ``sec_deals.VERIFIER_ENABLED`` ends up False, or if a deal is published without a passing
verifier answer for that filing. See README_FOR_CURSOR.md, "Verifier contract".

Every test runs the REAL ``run_weekly.main()`` (``--dry-run`` into a temporary ROOT). Only I/O is mocked:
EDGAR HTTP (``requests.get``), the Anthropic client (real ``anthropic.types.Message`` objects with
``tool_use`` blocks), image generation (``draw_image``), RSS/PubMed fetchers and ``time.sleep``.
Any socket use fails the test. A crash anywhere in ``main()`` fails the test.

The expectations in ``acceptance_fixtures/cases.json`` are audited ground truth and are NON-NEGOTIABLE:
fix the production code, never the expectations or this harness.

Run from the repo root:  ``pytest tests/test_acceptance_main.py``
"""
from __future__ import annotations

import base64
import copy
import html as html_lib
import importlib
import json
import os
import re
import socket
import sys
import time
from pathlib import Path
from typing import Any

import pytest

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parent
FIX = HERE / "acceptance_fixtures"
_FIXTURE = json.loads((FIX / "cases.json").read_text(encoding="utf-8"))
CASES: list[dict] = _FIXTURE["cases"]
MIN_CASES = int(_FIXTURE["min_cases"])
MIRROR: dict = json.loads((FIX / "edgar_mirror.json").read_text(encoding="utf-8"))
PNG_1x1 = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg=="
)


KNOWN_TOOLS = ("test_tool", "extract_deal", "verify_deal", "submit_weekly_digest")
MAX_VERIFIER_CALLS_PER_FILING = 3
VERIFIER_KILL_SWITCH_RE = re.compile(r"NO_?VERIF|SKIP_?VERIF|DISABLE_?VERIF|VERIF\w*_(?:OFF|DISABLED?)", re.I)
# Published deal-line label -> amount kind the verifier must have passed.
LINE_LABEL_KIND = {"首付": "upfront", "初期付款": "upfront", "里程碑": "milestones_total",
                   "收购对价": "purchase_price", "合并对价": "purchase_price"}
TITLE_TYPE = (("授权合作", "license_collaboration"), ("合并", "merger"), ("收购", "acquisition"))
IN_SCOPE_TITLE_RE = re.compile(r"^.+(?:与.+授权合作|收购.+|与.+合并)(?:（[^）]+）)?$")
OUT_OF_SCOPE_WORDS = ("贷款", "授信", "信贷", "买断", "融资", "增发", "认股权证", "股份", "股", "终止", "转让", "剥离",
                      "出售", "修订", "修正")


# =========================================================================== mocks
def _canon(url: str) -> str:
    return re.sub(r"/data/0*(\d+)/", r"/data/\1/", url.split("?")[0])


def _plain(text: str) -> str:
    """Entity-decoded, quote/dash-folded, whitespace-normalised text (same folding as a verbatim-quote check)."""
    t = html_lib.unescape(text or "")
    t = t.replace("\u201c", '"').replace("\u201d", '"').replace("\u2018", "'").replace("\u2019", "'")
    t = t.replace("\u2013", "-").replace("\u2014", "-").replace("\xa0", " ")
    return re.sub(r"\s+", " ", t).strip()


def _route_name(name: str) -> str:
    """Routing key for 'The filing company is: X' (case/ticker/suffix-insensitive)."""
    n = re.sub(r"\s*\(.*$", "", name or "").strip().lower()
    n = re.sub(r"[^a-z0-9]+", " ", n)
    n = re.sub(r"\b(inc|incorporated|corp|corporation|co|ltd|limited|plc|llc|sa|se|ag|nv)\b", " ", n)
    return re.sub(r"\s+", " ", n).strip()


class _HttpResponse:
    def __init__(self, status: int, text: str = "", js: Any = None):
        self.status_code = status
        self.text = text
        self.content = text.encode("utf-8")
        self.headers: dict = {}
        self._js = js

    def json(self):
        if self._js is None:
            raise ValueError("no JSON body")
        return self._js

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


def _anthropic_message(content: list[dict], stop_reason: str, usage: dict | None = None, model: str = "claude-sonnet-5-5"):
    """Build a REAL anthropic.types.Message (the object production receives from messages.create)."""
    from anthropic.types import Message

    payload = {
        "id": "msg_acceptance",
        "type": "message",
        "role": "assistant",
        "model": model,
        "content": content,
        "stop_reason": stop_reason,
        "stop_sequence": None,
        "usage": usage or {"input_tokens": 1000, "output_tokens": 200},
    }
    try:
        return Message.model_validate(payload)
    except Exception:
        # Older SDKs without ThinkingBlock: drop thinking blocks (production ignores them anyway).
        payload["content"] = [b for b in content if b.get("type") != "thinking"]
        return Message.model_validate(payload)


def _tool_use(name: str, inp: dict, bid: str) -> dict:
    return {"type": "tool_use", "id": bid, "name": name, "input": inp}


def _edgar_mirror_for(case: dict) -> dict:
    mirror = {_canon(k): v for k, v in MIRROR.items()}
    for f in case["filings"].values():
        if f["real"]:
            continue
        cik = str(int(f["cik"]))
        acc = f["adsh"].replace("-", "")
        base = f"https://www.sec.gov/Archives/edgar/data/{cik}/{acc}"
        cover = ["UNITED STATES SECURITIES AND EXCHANGE COMMISSION", "Washington, D.C. 20549", "FORM 8-K", "CURRENT REPORT"]
        if f.get("event"):
            cover.append(f"Date of Report (Date of earliest event reported): {f['event']}")
        cover += [f["registrant"], "(Exact name of registrant as specified in its charter)",
                  "Item 1.01 Entry into a Material Definitive Agreement"]
        doc = ("<html><body>" + "".join(f"<p>{p}</p>" for p in cover + f["paras"])
               + "<p>Item 9.01 Financial Statements and Exhibits</p></body></html>")
        rows = (f'<tr><td>1</td><td>8-K</td><td><a href="/Archives/edgar/data/{cik}/{acc}/doc8k.htm">doc8k.htm</a>'
                f"</td><td>8-K</td></tr>")
        mirror[_canon(f"{base}/doc8k.htm")] = {"status": 200, "text": doc}
        if f.get("ex99"):
            ex = "<html><body>" + "".join(f"<p>{p}</p>" for p in f["ex99"]) + "</body></html>"
            rows += (f'<tr><td>2</td><td>EX-99.1</td><td><a href="/Archives/edgar/data/{cik}/{acc}/ex991.htm">'
                     f"ex991.htm</a></td><td>EX-99.1</td></tr>")
            mirror[_canon(f"{base}/ex991.htm")] = {"status": 200, "text": ex}
        mirror[_canon(f"{base}/{f['adsh']}-index.htm")] = {"status": 200, "text": f"<html><table>{rows}</table></html>"}
    return mirror


class _Harness:
    """Per-test state: fresh run_weekly/sec_deals modules, mocks, temp ROOT."""

    def __init__(self, case: dict, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        self.case = case
        self.mp = monkeypatch
        self.root = tmp_path / "site"
        self.claude_calls: list[dict] = []
        self.write_output_calls: list[str] = []
        self.network_attempts: list[str] = []
        self.unexpected_http: list[str] = []
        self.unexpected_tools: list[str] = []
        self.verifier_calls: list[dict] = []        # one record per verify_deal request (see _fake_verifier)
        self.verifier_violations: list[str] = []
        self._plain_cache: dict[str, str] = {}
        self.recorded = None
        if case.get("claude_recorded"):
            self.recorded = json.loads(
                (FIX / "recorded_claude" / f"{case['claude_recorded']}.json").read_text(encoding="utf-8"))["responses"]

    # ------------------------------------------------------------------ setup
    def setup(self):
        mp = self.mp
        # Fresh production modules for every test (no state leaks between cases).
        for mod in ("run_weekly", "sec_deals"):
            mp.delitem(sys.modules, mod, raising=False)
        mp.syspath_prepend(str(REPO_ROOT))
        rw = importlib.import_module("run_weekly")
        assert Path(rw.__file__).resolve() == (REPO_ROOT / "run_weekly.py").resolve(), rw.__file__
        self.rw = rw

        # Temp ROOT: production config + an EMPTY prior latest.json (no index.html, so nothing is deduped).
        (self.root / "content").mkdir(parents=True)
        (self.root / "sources.yaml").write_text((REPO_ROOT / "sources.yaml").read_text(encoding="utf-8"), encoding="utf-8")
        (self.root / "content" / "latest.json").write_text(
            json.dumps({"generated": "", "articles": [], "deals": []}), encoding="utf-8")
        mp.setattr(rw, "ROOT", self.root)

        for k, v in {"ANTHROPIC_API_KEY": "test-not-a-key", "OPENAI_API_KEY": "test-not-a-key",
                     "ANTHROPIC_MODEL": "claude-sonnet-5-5",
                     "SEC_USER_AGENT": "TheraSik InLight acceptance-tests@example.com"}.items():
            mp.setenv(k, v)
        mp.delenv("INLIGHT_NO_DEALS", raising=False)
        switches = sorted(k for k in os.environ if VERIFIER_KILL_SWITCH_RE.search(k))
        if switches:
            pytest.fail(f"verifier kill-switch set in the environment: {switches}. The acceptance suite only runs with "
                        "the independent verifier ENABLED (the default production path).", pytrace=False)
        mp.setattr(sys, "argv", ["run_weekly.py", "--dry-run"])

        # No real network: any socket connect is recorded and refused.
        def _no_connect(sock, address, *a, **k):
            self.network_attempts.append(repr(address))
            raise OSError(f"acceptance tests: network disabled ({address!r})")
        mp.setattr(socket.socket, "connect", _no_connect)
        mp.setattr(socket.socket, "connect_ex", _no_connect)

        def _no_dns(host, *a, **k):
            self.network_attempts.append(f"DNS {host!r}")
            raise OSError(f"acceptance tests: network disabled (DNS {host!r})")
        mp.setattr(socket, "getaddrinfo", _no_dns)
        mp.setattr(time, "sleep", lambda *_a, **_k: None)

        import requests
        mp.setattr(requests, "get", self._fake_get)
        import anthropic
        harness = self

        class _FakeMessages:
            def create(self, **kw):
                return harness._fake_claude(**kw)

        class _FakeAnthropic:
            def __init__(self, *a, **k):
                self.messages = _FakeMessages()

        mp.setattr(anthropic, "Anthropic", _FakeAnthropic)

        def _fake_draw(prompt, dest, max_retries=2):
            Path(dest).parent.mkdir(parents=True, exist_ok=True)
            Path(dest).write_bytes(PNG_1x1)
            return True
        mp.setattr(rw, "draw_image", _fake_draw)
        mp.setattr(rw, "fetch_rss", self._fake_rss)
        mp.setattr(rw, "fetch_pubmed", lambda *a, **k: [])

        real_write_output = rw.write_output

        def _counting_write_output(*a, **k):
            self.write_output_calls.append(str(a[1] if len(a) > 1 else k.get("dest")))
            return real_write_output(*a, **k)
        mp.setattr(rw, "write_output", _counting_write_output)

        self.mirror = _edgar_mirror_for(self.case)
        self.hits = [{"_id": f"{f['adsh']}:doc.htm",
                      "_source": {"display_names": [f["display"]], "file_date": f["file_date"], "form": "8-K",
                                  "adsh": f["adsh"], "ciks": [f["cik"]], "sics": [f.get("sic", "2834")],
                                  "items": list(f.get("items") or ["1.01"])}}
                     for f in self.case["filings"].values()]
        self.name2key = {}
        for key, f in self.case["filings"].items():
            self.name2key[_route_name(f["display"])] = key
            if f.get("registrant"):
                self.name2key[_route_name(f["registrant"])] = key

    # ------------------------------------------------------------------ mocks
    def _fake_get(self, url, params=None, headers=None, timeout=None, **kw):
        if "efts.sec.gov" in url:
            return _HttpResponse(200, "", {"hits": {"hits": self.hits}})
        v = self.mirror.get(_canon(url))
        if v and v.get("status") == 200:
            return _HttpResponse(200, v["text"])
        if "sec.gov" not in url:
            self.unexpected_http.append(url)
        return _HttpResponse(404, "")

    def _fake_rss(self, source, start, limit):
        if source.get("name") != "Nature":
            return ([], "ok")
        day = time.strftime("%Y-%m-%d")
        return ([{"source": "Nature", "kind": "academic", "title": a["title_en"], "url": a["url"], "date": day,
                  "summary": a["summary"], "authors": "Chen L, Wang Y"} for a in self.case["academic"]], "ok")

    @staticmethod
    def _prompt_text(kw: dict) -> str:
        """All text the model would see: system prompt + every message (plain string or content blocks)."""
        parts: list[str] = []
        sysp = kw.get("system")
        if isinstance(sysp, str):
            parts.append(sysp)
        elif isinstance(sysp, list):
            parts += [b.get("text", "") for b in sysp if isinstance(b, dict)]
        for msg in kw.get("messages") or []:
            c = msg.get("content") if isinstance(msg, dict) else None
            if isinstance(c, str):
                parts.append(c)
            elif isinstance(c, list):
                parts += [b["text"] for b in c if isinstance(b, dict) and isinstance(b.get("text"), str)]
        return "\n".join(parts)

    def _fake_claude(self, **kw):
        """Dispatch by the TOOL offered and the filer named in the prompt — never by call order or call count."""
        names = [t.get("name") for t in (kw.get("tools") or []) if isinstance(t, dict)]
        known = [x for x in names if x in KNOWN_TOOLS]
        tool = known[0] if len(known) == 1 else None
        if tool is None:
            self.unexpected_tools.append(repr(names))
            raise AssertionError(f"acceptance harness: unexpected Claude call with tools {names!r}")
        prompt = self._prompt_text(kw)
        n = len(self.claude_calls)
        if tool == "test_tool":
            self.claude_calls.append({"tool": tool})
            return _anthropic_message([_tool_use("test_tool", {"ok": True}, f"toolu_check_{n}")], "tool_use")
        if tool == "extract_deal":
            m = re.search(r"The filing company is: (.*)\n", prompt + "\n")
            filer = m.group(1).strip() if m else ""
            key = self.name2key.get(_route_name(filer))
            self.claude_calls.append({"tool": tool, "filer": filer, "key": key})
            if self.recorded is not None:
                rec = self.recorded.get(key)
                assert rec is not None, f"no recorded Claude response for filer {filer!r}"
                return _anthropic_message(copy.deepcopy(rec["content"]), rec["stop_reason"], rec["usage"], rec["model"])
            resp = copy.deepcopy(self.case["claude"].get(key, {"deal_type": "none"}))
            return _anthropic_message([_tool_use("extract_deal", resp, f"toolu_deal_{n}")], "tool_use")
        if tool == "verify_deal":
            return self._fake_verifier(prompt, n)
        self.claude_calls.append({"tool": tool})
        field = list(self.rw.FIELDS)[0]
        arts = []
        for a in self.case["academic"]:
            art = a["article"]
            arts.append({"url": a["url"], "field": field, "journal": "Nature", "authors": "Chen L, Wang Y",
                         "evidence_level": "abstract", "image_prompt": "abstract circles", "title": art["title"],
                         "lead": art["lead"], "body": art["body"], "discuss": art["discuss"],
                         "steps": list(art["steps"])})
        return _anthropic_message([_tool_use("submit_weekly_digest", {"articles": arts}, f"toolu_digest_{n}")],
                                  "tool_use")

    # ------------------------------------------------------------------ verifier mock
    def _filing_plain(self, key: str) -> str:
        """Plain, whitespace-normalised, lower-cased text of every mirrored document of one filing."""
        if key in self._plain_cache:
            return self._plain_cache[key]
        f = self.case["filings"][key]
        acc = f["adsh"].replace("-", "")
        docs = [v.get("text", "") for k, v in self.mirror.items()
                if acc in k and isinstance(v, dict) and v.get("status") == 200 and not k.endswith("-index.htm")]
        self._plain_cache[key] = _plain(re.sub(r"<[^>]+>", " ", " ".join(docs)))
        return self._plain_cache[key]

    def _verbatim(self, key: str, quote: str) -> bool:
        q = _plain(quote or "").lower()
        return bool(q) and q in self._filing_plain(key).lower()

    def _extract_input(self, key: str) -> dict:
        """The extract_deal tool input this case feeds production for one filing (constructed or recorded)."""
        if self.recorded is not None:
            for b in (self.recorded.get(key) or {}).get("content", []):
                if b.get("type") == "tool_use" and b.get("name") == "extract_deal":
                    return b.get("input") or {}
            return {}
        return self.case["claude"].get(key) or {}

    def _fake_verifier(self, prompt: str, n: int):
        """Independent verifier (tool ``verify_deal``), answered per case with a constructed Message.

        Request (b7ebcfe contract): the prompt carries a ``FILING TEXT:`` section and a ``DEAL TO VERIFY:`` block with
        ``- Company (filer):``, ``- Counterparty:``, ``- Deal type:``, ``- Date:`` and ``- Amounts:`` (``  - <kind>:
        <rendered>``) lines. Routing is by the filer named there, never by call order.

        Default reply ("deceived verifier"): every requested field "supported", quoting the SAME verbatim evidence the
        extractor quoted. It adds no signal of its own, so every trap in this suite must still be rejected by the
        deterministic rules; it is NOT an oracle and cannot be probed for the truth. Per-case ``verifier`` entries
        replace it with: a field marked unsupported, a non-verbatim quote, an exception / timeout, a reply without
        tool_use, a missing / malformed verdict, an empty quote, max_tokens, or amounts in a different order.
        """
        m = re.search(r"Company \(filer\):[ \t]*(.*)", prompt) or re.search(r"The filing company is:[ \t]*(.*)", prompt)
        filer = m.group(1).strip() if m else ""
        key = self.name2key.get(_route_name(filer))
        rec: dict = {"filer": filer, "key": key, "mode": None, "status": None, "counterparty": None,
                     "deal_type": None, "kinds": []}
        self.verifier_calls.append(rec)
        self.claude_calls.append({"tool": "verify_deal", "filer": filer, "key": key})
        if key is None:
            self.verifier_violations.append(f"verify_deal request not routable to a filing (filer line {filer!r})")
            raise AssertionError("acceptance harness: verify_deal request not routable to a filing")
        if sum(1 for c in self.verifier_calls if c["key"] == key) > MAX_VERIFIER_CALLS_PER_FILING:
            self.verifier_violations.append(f"[{key}] more than {MAX_VERIFIER_CALLS_PER_FILING} verify_deal calls (verifier probing)")
        cp = re.search(r"- Counterparty:[ \t]*(.*)", prompt)
        dt = re.search(r"- Deal type:[ \t]*(.*)", prompt)
        am = prompt.split("- Amounts:", 1)[1] if "- Amounts:" in prompt else ""
        kinds = re.findall(r"^[ \t]+-[ \t]+([a-z_]+):[ \t]*(.+)$", am, re.M)
        rec.update(counterparty=cp.group(1).strip() if cp else None, deal_type=dt.group(1).strip() if dt else None,
                   kinds=[k for k, _ in kinds])
        if not cp or not dt:
            self.verifier_violations.append(f"[{key}] verify_deal request lacks '- Counterparty:' / '- Deal type:' lines")
        ft = prompt.split("FILING TEXT:", 1)[1] if "FILING TEXT:" in prompt else ""
        ext = self._extract_input(key)
        tq = _plain(str(ext.get("type_quote") or "")).lower()
        if not tq or tq not in _plain(ft).lower():
            self.verifier_violations.append(f"[{key}] verify_deal request must show the verifier the filing text "
                                            "(FILING TEXT: section containing the deal's type_quote)")
        cfg = (self.case.get("verifier") or {}).get(key) or {}
        mode = cfg.get("mode") or "answer"
        rec["mode"] = mode
        if mode in ("raise", "timeout"):
            import anthropic
            exc_cls = getattr(anthropic, "APITimeoutError" if mode == "timeout" else "APIConnectionError", RuntimeError)
            exc = exc_cls.__new__(exc_cls)
            Exception.__init__(exc, "Request timed out." if mode == "timeout" else "Connection error.")
            for attr, val in (("message", str(exc)), ("request", None), ("body", None)):
                try:
                    setattr(exc, attr, val)
                except Exception:
                    pass
            raise exc
        if mode == "no_tool":
            return _anthropic_message([{"type": "text", "text": "All fields look supported to me."}], "end_turn")

        f = self.case["filings"][key]
        company_q = f.get("registrant") or re.sub(r"\s*\(.*$", "", f["display"]).strip()
        plain = self._filing_plain(key)
        pos = plain.lower().find(tq) if tq else -1
        dates = re.findall(r"(?:January|February|March|April|May|June|July|August|September|October|November|December)"
                           r" \d{1,2}, 20\d\d", plain[max(0, pos - 400): pos + 400] if pos >= 0 else plain)
        date_q = f.get("event") or (dates[0] if dates else "")
        ex_amounts = [a for a in (ext.get("amounts") or []) if isinstance(a, dict)]
        body: dict = {
            "company": {"verdict": "supported", "quote": company_q},
            "counterparty": {"verdict": "supported", "quote": str(ext.get("counterparty_quote") or "")},
            "deal_type": {"verdict": "supported", "quote": str(ext.get("type_quote") or "")},
            "date": {"verdict": "supported", "quote": date_q},
            "amounts": [],
        }
        for kind, rendered in kinds:
            q = next((a.get("quote") for a in ex_amounts if a.get("kind") == kind), None)
            body["amounts"].append({"role": kind, "value": rendered.strip(),
                                    "verdict": "supported" if q else "unsupported", "quote": q or ""})
        for sel in cfg.get("reject") or []:
            tgt = [a for a in body["amounts"] if a["role"] == sel.get("role")] if sel["field"] == "amount" else [body[sel["field"]]]
            for t in tgt:
                t["verdict"] = "unsupported"
                t["quote"] = sel.get("quote", t["quote"])
        for sel in cfg.get("quote") or []:
            tgt = [a for a in body["amounts"] if a["role"] == sel.get("role")] if sel["field"] == "amount" else [body[sel["field"]]]
            for t in tgt:
                t["quote"] = sel["text"]
        if mode == "missing_field":
            body.pop(cfg.get("field", "counterparty"), None)
        if mode == "bad_verdict":
            body[cfg.get("field", "counterparty")]["verdict"] = cfg.get("verdict", "likely")
        if mode == "empty_quote":
            body[cfg.get("field", "deal_type")]["quote"] = ""
        if mode == "amounts_short":
            body["amounts"] = body["amounts"][:-1]
        if mode == "amounts_reordered":
            body["amounts"] = list(reversed(body["amounts"]))

        # What a correct fail-closed implementation may treat as passed (the publication gate uses this).
        def ok(x):
            return isinstance(x, dict) and x.get("verdict") == "supported" and self._verbatim(key, x.get("quote", ""))
        good = mode in ("answer", "amounts_reordered", "amounts_short")
        rec["status"] = {
            "core": good and all(ok(body.get(k)) for k in ("company", "counterparty", "deal_type", "date")),
            "kinds": {a["role"] for a in body.get("amounts", []) if ok(a)} if good else set(),
        }
        stop = "max_tokens" if mode == "max_tokens" else "tool_use"
        if mode == "max_tokens":
            rec["status"] = {"core": False, "kinds": set()}
        return _anthropic_message([_tool_use("verify_deal", body, f"toolu_verify_{n}")], stop)

    # ------------------------------------------------------------------ run
    def run(self) -> dict:
        code = 0
        try:
            self.rw.main()          # <- the real production entry point; any non-SystemExit exception fails the test
        except SystemExit as e:
            code = e.code if isinstance(e.code, int) else (0 if e.code is None else 1)
        sd = sys.modules.get("sec_deals")
        if sd is not None and getattr(sd, "VERIFIER_ENABLED", True) is not True:
            self.verifier_violations.append("sec_deals.VERIFIER_ENABLED is not True after main(): verifier was switched off")
        out: dict[str, Any] = {"exit": code, "deals": None, "articles": None, "wechat": None}
        weeks = sorted((self.root / "preview" / "weekly").glob("*")) if (self.root / "preview" / "weekly").exists() else []
        if weeks:
            w = weeks[-1]
            if (w / "deals.json").exists():
                out["deals"] = json.loads((w / "deals.json").read_text(encoding="utf-8"))
            if (w / "articles.json").exists():
                out["articles"] = json.loads((w / "articles.json").read_text(encoding="utf-8"))
            if (w / "wechat" / "article.html").exists():
                out["wechat"] = html_lib.unescape((w / "wechat" / "article.html").read_text(encoding="utf-8"))
        return out


# =========================================================================== expectations
def _norm(s: str) -> str:
    return re.sub(r"\s+", "", s or "")


def _spec_ok(deal: dict, spec: dict) -> bool:
    t, m, ms, d = deal.get("t", ""), deal.get("m", ""), deal.get("ms", ""), deal.get("d", "")
    kind = spec["kind"]
    if kind == "exact":
        return (t, m, ms) == (spec["t"], spec["m"], spec["ms"])
    if kind == "match":
        return (re.fullmatch(spec["t_re"], t) is not None and m == spec["m"]
                and (spec.get("ms") is None or ms == spec["ms"])
                and (spec.get("ms_re") is None or re.fullmatch(spec["ms_re"], ms) is not None)
                and d.startswith(spec.get("d_prefix", "")))
    if kind == "truthful":
        if re.fullmatch(spec["t_re"], t) is None or m not in spec["m_allowed"]:
            return False
        if (m and not t.endswith(f"（{m}）")) or (not m and "（" in t):
            return False
        lines = [x.strip() for x in ms.split("|") if x.strip()] if ms else []
        if len(set(lines)) != len(lines):
            return False
        if m and not any(m in x for x in lines):
            return False
        return all(any(re.fullmatch(p, x) for p in spec["lines_re"]) for x in lines)
    raise AssertionError(f"unknown spec kind {kind}")


def _describe(deal: dict | None) -> str:
    return "NO DEAL" if deal is None else f"t={deal.get('t')!r} m={deal.get('m')!r} ms={deal.get('ms')!r} d={deal.get('d')!r}"


def _verifier_gate_errors(h: _Harness, key: str, d: dict) -> list[str]:
    """A published deal needs a verify_deal answer for the SAME filing that (a) was asked about the published
    counterparty and deal type, (b) passed company / counterparty / deal_type / date with supported verdicts and
    verbatim quotes, and (c) passed every published amount kind. Failed verifier calls never count."""
    t, ms = d.get("t") or "", d.get("ms") or ""
    want_type = next((ty for word, ty in TITLE_TYPE if word in t), None)
    kinds = [LINE_LABEL_KIND.get(x.strip().split("：", 1)[0]) for x in ms.split("|") if x.strip()]
    title_norm = _route_name(re.sub(r"（[^）]*）$", "", t))
    reasons = []
    for c in h.verifier_calls:
        if c["key"] != key:
            continue
        st = c.get("status") or {"core": False, "kinds": set()}
        cp = _route_name(c.get("counterparty") or "")
        if not st["core"]:
            reasons.append(f"{c['mode']}: company/counterparty/deal_type/date not passed")
        elif c.get("deal_type") != want_type:
            reasons.append(f"verified deal type {c.get('deal_type')!r} != published {want_type!r}")
        elif not cp or cp not in title_norm:
            reasons.append(f"verified counterparty {c.get('counterparty')!r} is not the published one")
        elif [k for k in kinds if k not in st["kinds"]]:
            reasons.append(f"published amount kinds {[k for k in kinds if k not in st['kinds']]} not passed by the verifier")
        else:
            return []
    return [f"[verifier-contract] [{key}] deal published without a passing verify_deal answer for this filing: "
            f"{_describe(d)}; reasons: {reasons or ['verify_deal was never called for this filing']}"]


def _check(case: dict, h: _Harness, out: dict, log_text: str) -> list[str]:
    errs: list[str] = []
    exp = case["expect"]
    if out["exit"] not in exp["exit"]:
        errs.append(f"main() exit code {out['exit']} not in {exp['exit']}")
    if h.network_attempts:
        errs.append(f"network access attempted: {h.network_attempts}")
    if h.unexpected_http:
        errs.append(f"unexpected HTTP GETs: {h.unexpected_http}")
    if h.unexpected_tools:
        errs.append(f"unexpected Claude tool calls: {h.unexpected_tools}")
    for v in h.verifier_violations:
        errs.append(f"[verifier-contract] {v}")
    if out["exit"] == 0:
        if len(h.write_output_calls) != 1:
            errs.append(f"write_output called {len(h.write_output_calls)} times (expected 1): main() did not run through")
        if out["deals"] is None or out["articles"] is None or out["wechat"] is None:
            errs.append("main() exited 0 but deals.json / articles.json / wechat/article.html were not all written")
    if not any(c["tool"] == "submit_weekly_digest" for c in h.claude_calls):
        errs.append("main() never asked Claude for the weekly digest (pipeline not exercised)")

    deals = out["deals"] or []
    by_filing: dict[str, list[dict]] = {k: [] for k in case["filings"]}
    for d in deals:
        owner = [k for k, f in case["filings"].items() if f["adsh"].replace("-", "") in (d.get("url") or "")]
        if len(owner) != 1:
            errs.append(f"deal not attributable to exactly one input filing: {_describe(d)} url={d.get('url')!r}")
            continue
        by_filing[owner[0]].append(d)
    for key, allowed in exp["filings"].items():
        got = by_filing.get(key, [])
        if len(got) > 1:
            errs.append(f"[{key}] {len(got)} deals published for one filing: {[_describe(x) for x in got]}")
            continue
        deal = got[0] if got else None
        ok = (deal is None and None in allowed) or (deal is not None and any(s and _spec_ok(deal, s) for s in allowed))
        if not ok:
            errs.append(f"[{key}] got {_describe(deal)}\n      allowed: "
                        + " OR ".join("NO DEAL" if s is None else json.dumps(s, ensure_ascii=False) for s in allowed))

    # Scope: only license/collaboration or acquisition/merger deals; share / warrant components are never rendered.
    for d in deals:
        t, ms = d.get("t") or "", d.get("ms") or ""
        if not IN_SCOPE_TITLE_RE.fullmatch(t):
            errs.append(f"[scope] deal title is not a license/collaboration or acquisition/merger title: {t!r}")
        bad = [w for w in OUT_OF_SCOPE_WORDS if w in t or w in ms]
        if bad:
            errs.append(f"[scope] out-of-scope wording {bad} in published deal: {_describe(d)}")
        for line in [x.strip() for x in ms.split("|") if x.strip()]:
            if line.split("：", 1)[0] not in LINE_LABEL_KIND:
                errs.append(f"[scope] deal line {line!r} is not an upfront / milestone / purchase-price line")

    # Every published deal must have passed the independent verifier for its own filing (fail closed).
    for key, got in by_filing.items():
        for d in got:
            errs += _verifier_gate_errors(h, key, d)

    # WeChat must mirror deals.json
    wechat = out["wechat"]
    if wechat is not None:
        if not deals and "交易动态" in wechat:
            errs.append("WeChat HTML has a 交易动态 section although no deal was published")
        for d in deals:
            if d.get("t") and d["t"] not in wechat:
                errs.append(f"deal title missing from WeChat HTML: {d['t']!r}")
            for line in [x.strip() for x in (d.get("ms") or "").split("|") if x.strip()]:
                if line not in wechat:
                    errs.append(f"deal line missing from WeChat HTML: {line!r}")

    # Articles
    arts = out["articles"] or []
    for a in arts:
        for fld in ("t", "lead", "body"):
            if not (a.get(fld) or "").strip():
                errs.append(f"article {a.get('t')!r} published with EMPTY {fld}")
    aexp = exp.get("articles") or {}
    texts = [(a.get("t") or "") + "\n" + (a.get("lead") or "") + "\n" + (a.get("body") or "") + "\n"
             + (a.get("discuss") or "") + "\n" + "\n".join(a.get("steps") or []) for a in arts]
    blob = _norm("\n".join(texts))
    wblob = _norm(wechat or "")
    for s in aexp.get("must_contain", []):
        if _norm(s) not in blob:
            errs.append(f"honest text was stripped / not published: {s!r}")
    for s in aexp.get("must_not_contain", []):
        if _norm(s) in blob or _norm(s) in wblob:
            errs.append(f"invented text was published: {s!r}")
    for t in aexp.get("titles_present", []):
        if not any(a.get("t") == t for a in arts):
            errs.append(f"article {t!r} missing; published titles: {[a.get('t') for a in arts]}")
    for e in aexp.get("exact", []):
        hit = [a for a in arts if a.get("t") == e["title"]]
        if not hit:
            errs.append(f"article {e['title']!r} not published; published titles: {[a.get('t') for a in arts]}")
            continue
        for fld in ("lead", "body", "discuss"):
            if _norm(hit[0].get(fld)) != _norm(e[fld]):
                errs.append(f"article {e['title']!r} {fld} altered:\n      expected {e[fld]!r}\n      got      {hit[0].get(fld)!r}")
    if errs:
        drops = [ln for ln in log_text.splitlines()
                 if re.search(r"Deal |dropped|丢弃|未捕获|Error|Traceback|out_of_scope|[Vv]erif", ln)][-25:]
        errs.append("relevant log lines:\n      " + "\n      ".join(drops))
    return errs


# =========================================================================== tests
@pytest.mark.parametrize("case", CASES, ids=[c["id"] for c in CASES])
def test_acceptance(case, tmp_path, monkeypatch, caplog):
    caplog.set_level("INFO")
    h = _Harness(case, tmp_path, monkeypatch)
    h.setup()
    out = h.run()
    errs = _check(case, h, out, caplog.text)
    assert not errs, f"\n[{case['id']}] {case['desc']}\n  - " + "\n  - ".join(errs)


def test_guard_main_really_runs(tmp_path, monkeypatch, caplog):
    """Guard: the suite must exercise the real main(): production module, write_output called, files written."""
    caplog.set_level("INFO")
    case = next(c for c in CASES if c["id"] == "R2_alec_honest")
    h = _Harness(case, tmp_path, monkeypatch)
    h.setup()
    assert h.rw.main.__module__ == "run_weekly"
    assert Path(h.rw.main.__code__.co_filename).resolve() == (REPO_ROOT / "run_weekly.py").resolve()
    out = h.run()
    assert out["exit"] == 0, caplog.text[-3000:]
    assert len(h.write_output_calls) == 1, "write_output was not called exactly once by main()"
    assert [c["tool"] for c in h.claude_calls].count("extract_deal") >= 1
    assert out["deals"] and out["articles"] and out["wechat"]
    assert len(CASES) >= MIN_CASES >= 89, f"acceptance fixtures truncated: {len(CASES)} cases"
    assert any(c["tool"] == "verify_deal" for c in h.claude_calls), "the independent verifier was never called"
    assert not h.verifier_violations, h.verifier_violations


def test_guard_verifier_kill_switch_rejected(tmp_path, monkeypatch):
    """Guard: a verifier kill-switch in the environment makes the suite fail instead of silently passing."""
    case = next(c for c in CASES if c["id"] == "R2_alec_honest")
    monkeypatch.setenv("INLIGHT_NO_VERIFIER", "1")
    h = _Harness(case, tmp_path, monkeypatch)
    with pytest.raises(pytest.fail.Exception):
        h.setup()
