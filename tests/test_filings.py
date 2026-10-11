"""Offline tests for filings.py parsers (no network)."""
import json
import unittest
from datetime import datetime, timezone

import filings


class EdgarParseTest(unittest.TestCase):
    def test_one_row_per_filing_pharma_only_prefers_ex99(self):
        payload = {"hits": {"hits": [
            {"_id": "0001193125-26-413090:alec-20260930.htm", "_source": {
                "ciks": ["0001653087"], "display_names": ["Alector, Inc.  (ALEC)  (CIK 0001653087)"],
                "form": "8-K", "file_type": "8-K", "file_date": "2026-10-05", "sics": ["2836"]}},
            {"_id": "0001193125-26-413090:alec-ex99_01.htm", "_source": {
                "ciks": ["0001653087"], "display_names": ["Alector, Inc.  (ALEC)  (CIK 0001653087)"],
                "form": "8-K", "file_type": "EX-99.01", "file_date": "2026-10-05", "sics": ["2836"]}},
            {"_id": "0001193125-26-413203:d115124d8k.htm", "_source": {
                "ciks": ["0001043277"], "display_names": ["C. H. ROBINSON WORLDWIDE, INC.  (CHRW)  (CIK 0001043277)"],
                "form": "8-K", "file_type": "8-K", "file_date": "2026-10-05", "sics": ["4731"]}},
        ]}}
        rows = filings.parse_edgar_hits(payload, '"NewCo"')
        self.assertEqual(len(rows), 1)
        r = rows[0]
        self.assertEqual(r["company"], "Alector, Inc.")
        self.assertEqual(r["ticker"], "ALEC")
        self.assertEqual(r["exchange"], "SEC EDGAR")
        self.assertEqual(r["url"], "https://www.sec.gov/Archives/edgar/data/1653087/000119312526413090/alec-ex99_01.htm")


class HkexParseTest(unittest.TestCase):
    def test_title_filters_and_watchlist(self):
        items = [
            {"STOCK_CODE": "02162", "STOCK_NAME": "康方生物", "TITLE": "自願公告 - 與某公司訂立獨家許可協議",
             "DATE_TIME": "09/10/2026 18:00", "FILE_LINK": "/listedco/a_c.pdf"},
            {"STOCK_CODE": "02162", "STOCK_NAME": "康方生物", "TITLE": "截至2026年9月30日止月份之股份發行人的證券變動月報表",
             "DATE_TIME": "05/10/2026 16:33", "FILE_LINK": "/listedco/b_c.pdf"},
            {"STOCK_CODE": "00860", "STOCK_NAME": "ＡＰＯＬＬＯ出行", "TITLE": "有關根據一般授權配售新股份的補充公佈",
             "DATE_TIME": "08/10/2026 10:00", "FILE_LINK": "/listedco/c_c.pdf"},
            {"STOCK_CODE": "06955", "STOCK_NAME": "博安生物", "TITLE": "持續關連交易 推廣合作協議",
             "DATE_TIME": "09/10/2026 22:00", "FILE_LINK": "/listedco/d_c.pdf"},
            {"STOCK_CODE": "01236", "STOCK_NAME": "樂動機器人", "TITLE": "自願性公告 簽署合作協議",
             "DATE_TIME": "11/10/2026 08:00", "FILE_LINK": "/listedco/e_c.pdf"},
        ]
        rows = filings.parse_hkex_search({"result": json.dumps(items)}, set(filings.DEFAULT_HK_WATCHLIST))
        self.assertEqual([r["ticker"] for r in rows], ["02162", "06955"])
        self.assertEqual(rows[0]["date"], "2026-10-09")
        self.assertTrue(rows[0]["url"].startswith("https://www1.hkexnews.hk/listedco/"))


class CninfoParseTest(unittest.TestCase):
    def test_noise_excluded_and_pharma_only(self):
        payload = {"announcements": [
            {"secCode": "688373", "secName": "盟科药业", "announcementTitle": "关于与ClearideBio签订授权<em>许可</em>协议的公告",
             "announcementTime": 1791561600000, "adjunctUrl": "finalpage/2026-10-10/1225599947.PDF"},
            {"secCode": "300878", "secName": "维康药业", "announcementTitle": "关于公司药品生产<em>许可</em>证变更的公告",
             "announcementTime": 1791475200000, "adjunctUrl": "finalpage/x.PDF"},
            {"secCode": "600000", "secName": "某银行", "announcementTitle": "关于签署战略合作协议的公告",
             "announcementTime": 1791475200000, "adjunctUrl": "finalpage/y.PDF"},
        ]}
        rows = filings.parse_cninfo(payload, "授权许可", filings.CN_DEFAULT_EXCLUDE)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["title"], "关于与ClearideBio签订授权许可协议的公告")
        self.assertEqual(rows[0]["url"], "https://static.cninfo.com.cn/finalpage/2026-10-10/1225599947.PDF")

    def test_source_row_shape(self):
        row = {"date": "2026-10-10", "company": "盟科药业", "ticker": "688373", "exchange": "cninfo（沪深）",
               "form": "公告", "title": "授权许可协议的公告", "url": "https://static.cninfo.com.cn/x.PDF", "excerpt": "首付款 1000 万美元"}
        out = filings.to_source_row(row)
        self.assertEqual(out["kind"], "filing")
        self.assertIn("巨潮资讯", out["source"])
        self.assertIn("首付款", out["summary"])

    def test_disabled_returns_nothing(self):
        now = datetime.now(timezone.utc)
        self.assertEqual(filings.fetch_filings({"filings": {"enabled": False}}, now, now), ([], []))


if __name__ == "__main__":
    unittest.main()
