"""Offline tests for tools/audit_weekly.py (publish gate). No network."""
import contextlib
import importlib.util
import io
import json
import re
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("audit_weekly", ROOT / "tools" / "audit_weekly.py")
aw = importlib.util.module_from_spec(_spec)
sys.modules["audit_weekly"] = aw
_spec.loader.exec_module(aw)


def ctx():
    return aw.Ctx(ROOT, None, None, False, False)


def statuses(c, check):
    return [f.status for f in c.findings if f.check == check]


class TextRulesTest(unittest.TestCase):
    def test_self_talk_and_editor_notes_fail(self):
        for bad in ("用户提供全文，本文据此改写。", "摘录未给出具体数值，本文不补。", "Supplementary未读。",
                    "编辑备注：待补图", "本页列出本周交易", "InSynBio 提供论文写作服务"):
            c = ctx()
            aw.check_text_rules(c, "x", bad, True)
            self.assertIn("FAIL", statuses(c, "C1-禁用语"), bad)

    def test_scientific_occupancy_is_not_placeholder(self):
        c = ctx()
        aw.check_text_rules(c, "x", "STAT5B 与 p300 共占位；CCR5 占位洗脱后 ATI。", True)
        self.assertEqual([f for f in c.findings if f.status == "FAIL"], [])

    def test_brand_spelling(self):
        c = ctx()
        aw.check_text_rules(c, "x", "由 TheraSik 出品", True)
        self.assertIn("FAIL", statuses(c, "C7-TheraSik"))
        c = ctx()
        aw.check_text_rules(c, "x", "访问 inlight.therasik.com", True)
        self.assertEqual(statuses(c, "C7-TheraSik"), [])

    def test_disclaimer_patterns_count(self):
        page = "机制图由 AI 生成。……示意图由AI生成。"
        self.assertEqual(len(re.findall(aw.DISCLAIMERS["ai_figure"], page)), 2)


class HelpersTest(unittest.TestCase):
    def test_numbers_and_urls(self):
        self.assertEqual(aw.numbers_in("ORR 67%，n=1,234，第6天"), ["67", "1234"])
        self.assertEqual(aw.norm_url("https://doi.org/10.1/ABC"), aw.norm_url("http://dx.doi.org/10.1/abc/"))

    def test_parse_live_deals(self):
        deals = aw.parse_deals((ROOT / "index.html").read_text(encoding="utf-8"))
        self.assertEqual(len(deals), 22)
        self.assertTrue(all(set(d["kinds"]) <= aw.DEAL_KINDS for d in deals))


class DealRulesTest(unittest.TestCase):
    def deal(self, **kw):
        d = {"d": "2026-10", "kinds": ["lic"], "t": "甲公司授权乙公司", "m": "首付款 1 亿美元", "ms": "里程碑至多 10 亿美元",
             "why": "x", "src": "公司公告 2026-10-05", "url": "https://www.sec.gov/x"}
        d.update(kw)
        return d

    def run_deals(self, deals):
        c = ctx()
        site = SimpleNamespace(deals=deals)
        aw.check_deals(c, site, site, {aw.deal_key(d) for d in deals}, {})
        return c

    def test_newco_tag_requires_new_company_and_equity(self):
        c = self.run_deals([self.deal(kinds=["newco"])])
        self.assertIn("FAIL", statuses(c, "F5-NewCo 判定"))
        c = self.run_deals([self.deal(kinds=["newco", "lic"], why="双方成立新公司，甲方持股 30% 并完成 A 轮融资")])
        self.assertEqual(statuses(c, "F5-NewCo 判定"), [])

    def test_untagged_newco_warns(self):
        c = self.run_deals([self.deal(why="授权资产装入新公司，甲方获股权")])
        self.assertIn("WARN", statuses(c, "F5-NewCo 判定"))

    def test_order_kinds_money(self):
        c = self.run_deals([self.deal(d="2026-08"), self.deal(d="2026-09", url="https://www.sec.gov/y", t="丙", kinds=["zzz"], m="$3亿")])
        self.assertIn("FAIL", statuses(c, "H5-排序"))
        self.assertIn("FAIL", statuses(c, "F4-交易类型"))
        self.assertIn("FAIL", statuses(c, "H4-金额写法"))


class GateTest(unittest.TestCase):
    def run_main(self, extra):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "out"
            with contextlib.redirect_stdout(io.StringIO()):
                code = aw.main(["--root", str(ROOT), "--offline", "--skip-images", "--out", str(out), *extra(tmp)])
            report = json.loads((out / "audit_report.json").read_text(encoding="utf-8"))
            return code, report

    def test_current_site_passes_with_baseline(self):
        code, rep = self.run_main(lambda tmp: [])
        self.assertEqual(code, 0, [f for f in rep["findings"] if f["status"] == "FAIL"])
        self.assertTrue(rep["publish_allowed"])
        self.assertTrue(any(f["status"] == "LEGACY" for f in rep["findings"]))

    def test_bad_week_blocks_publish(self):
        def week(tmp):
            w = Path(tmp) / "2026-10-18"
            w.mkdir()
            url = "https://doi.org/10.1/new"
            (w / "candidates.json").write_text(json.dumps([{"url": url, "summary": "ORR 40%"}]), encoding="utf-8")
            (w / "articles.json").write_text(json.dumps([{
                "id": "w-1", "f": "c3", "t": "重磅突破", "ds": "2026-10-18", "disp": "2026.10", "j": "Nature",
                "url": url, "au": "A", "tags": [], "sum": "用户提供全文。ORR 40%。", "img": "",
                "status": "published", "evidence_level": "abstract"}], ensure_ascii=False), encoding="utf-8")
            (w / "deals.json").write_text(json.dumps([{
                "d": "2026-10", "kinds": ["newco"], "t": "某药企授权", "m": "未披露", "ms": "未披露",
                "why": "x", "src": "媒体", "url": "https://example.com/not-in-candidates"}], ensure_ascii=False), encoding="utf-8")
            return ["--week", str(w)]
        code, rep = self.run_main(week)
        self.assertEqual(code, 1)
        failed = {f["check"] for f in rep["findings"] if f["status"] == "FAIL"}
        for cid in ("C1-禁用语", "A2-全文准入", "A2-允许来源", "E1-图片存在", "F5-NewCo 判定"):
            self.assertIn(cid, failed)
        self.assertTrue(all(f["new"] for f in rep["findings"] if f["status"] == "FAIL"))

    def test_baseline_never_covers_new_items(self):
        c = ctx()
        c.add("C1-禁用语", "文风与禁用语", "w-1", "FAIL", "内部/流程用语：…", new=True)
        c.add("C1-禁用语", "文风与禁用语", "old-1", "FAIL", "内部/流程用语：…", new=False)
        c.add("D4-段落长度", "格式排版", "old-2", "WARN", "1 段超过 150 字", new=False)
        base = {(f.check, f.item, aw.msg_hash(f.msg)) for f in c.findings}
        aw.apply_baseline(c.findings, base, strict=False)
        self.assertEqual([f.status for f in c.findings], ["FAIL", "LEGACY", "LEGACY"])
        c.add("D4-段落长度", "格式排版", "w-2", "WARN", "x", new=True)
        aw.apply_baseline(c.findings, set(), strict=True)
        self.assertEqual(c.findings[-1].status, "FAIL")

    def test_every_check_has_catalog_entry(self):
        src = (ROOT / "tools" / "audit_weekly.py").read_text(encoding="utf-8")
        used = set(re.findall(r'"([A-J]\d-[^"]+)"', src))
        listed = {c[0] for c in aw.AUTO_CHECKS} | {c[0] for c in aw.MANUAL_ITEMS} | {"E0-图像工具"}
        self.assertEqual(used - listed, set())
        doc = (ROOT / "docs" / "PUBLISH_CHECKLIST.md").read_text(encoding="utf-8")
        for cid in listed - {"E0-图像工具"}:
            self.assertIn(cid.split("-")[0], doc, cid)


if __name__ == "__main__":
    unittest.main()
