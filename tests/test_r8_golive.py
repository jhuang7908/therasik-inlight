#!/usr/bin/env python3
"""Guards for the r8 go-live catalog: curated lock, hidden ids, deals."""

from __future__ import annotations

import hashlib
import json
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import inlight_catalog as cat  # noqa: E402


HIDDEN_SAMPLE = ["c7-cell-5", "c5-am-6", "c4-ai-4", "a-invivocar", "c8-vac-5"]
PREPRINT_IDS = {"c6-ab-4", "c6-ab-5"}


def _webp_size(path: Path):
    import struct
    data = path.read_bytes()
    if data[0:4] != b"RIFF" or data[8:12] != b"WEBP":
        return None
    i = 12
    while i + 8 <= len(data):
        chunk = data[i:i + 4]
        size = struct.unpack_from("<I", data, i + 4)[0]
        payload = data[i + 8:i + 8 + size]
        if chunk == b"VP8X" and len(payload) >= 10:
            w = 1 + int.from_bytes(payload[4:7], "little")
            h = 1 + int.from_bytes(payload[7:10], "little")
            return w, h
        if chunk == b"VP8 " and len(payload) >= 10:
            w = struct.unpack_from("<H", payload, 6)[0] & 0x3FFF
            h = struct.unpack_from("<H", payload, 8)[0] & 0x3FFF
            return w, h
        if chunk == b"VP8L" and len(payload) >= 5:
            bits = struct.unpack_from("<I", payload, 1)[0]
            w = (bits & 0x3FFF) + 1
            h = ((bits >> 14) & 0x3FFF) + 1
            return w, h
        i += 8 + size + (size & 1)
    return None


class R8GoLiveTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.catalog = cat.load_catalog()
        cls.index = (ROOT / "index.html").read_text(encoding="utf-8")
        cls.manifest = json.loads((ROOT / "content" / "r8_manifest.json").read_text(encoding="utf-8"))
        cls.sitemap = (ROOT / "sitemap.xml").read_text(encoding="utf-8") if (ROOT / "sitemap.xml").exists() else ""

    def test_thirty_six_published(self):
        ids = cat.published_ids(self.catalog)
        self.assertEqual(len(ids), 36)
        self.assertEqual(ids, [a["id"] for a in self.manifest["articles"]])
        labels = [a.get("field_label") or a.get("field") for a in cat.published_articles(self.catalog)]
        from collections import Counter
        counts = Counter(labels)
        self.assertEqual(counts["类器官"], 4)
        self.assertEqual(counts["疾病模型"], 3)
        self.assertEqual(counts["AI药物设计"], 5)
        self.assertEqual(counts["肿瘤免疫与细胞治疗"], 5)
        self.assertEqual(counts["自身免疫与移植"], 4)
        self.assertEqual(counts["疫苗与感染免疫"], 4)
        self.assertEqual(counts["抗体工程"], 4)
        self.assertEqual(counts["核酸与基因治疗"], 4)
        self.assertEqual(counts["精准肿瘤与临床转化"], 3)
        self.assertNotIn("动物模型", labels)

    def test_card_titles_at_most_30(self):
        for a in cat.published_articles(self.catalog):
            self.assertLessEqual(len(a["card_title"]), 30, a["id"])

    def test_preprint_chips(self):
        for a in cat.published_articles(self.catalog):
            if a["id"] in PREPRINT_IDS:
                self.assertTrue(a["preprint"], a["id"])
            else:
                self.assertFalse(a["preprint"], a["id"])

    def test_images_keep_pixel_dimensions(self):
        for row in self.manifest["articles"]:
            for key, pxkey in (("card_image", "card_px"), ("mech_image", "mech_px")):
                path = ROOT / "img" / Path(row[key]).name
                self.assertTrue(path.exists(), path)
                self.assertEqual(_webp_size(path), tuple(row[pxkey]), (row["id"], key))

    def test_hidden_ids_absent_from_public_surfaces(self):
        hidden = set(cat.hidden_ids(self.catalog))
        self.assertTrue({"c7-cell-5", "c5-am-6"} <= hidden)
        cat_ids = {a["id"] for a in cat.extract_cat_array(self.index)}
        self.assertEqual(len(cat_ids), 36)
        for hid in HIDDEN_SAMPLE:
            self.assertIn(hid, hidden)
            self.assertNotIn(hid, cat_ids)
            self.assertNotIn(f"/pages/article/{hid}.html", self.sitemap)

    def test_hidden_data_kept_in_catalog(self):
        lookup = cat.by_id(self.catalog)
        self.assertEqual(lookup["c7-cell-5"]["status"], "excluded")
        self.assertEqual(lookup["c5-am-6"]["status"], "excluded")
        self.assertEqual(lookup["a-invivocar"]["status"], "no_fulltext")
        self.assertTrue(lookup["c7-cell-5"].get("legacy"))
        self.assertTrue((ROOT / "img" / "papers" / "c7-cell-5.jpg").exists())

    def test_deals_byte_identical_and_count_22(self):
        block = cat.extract_deals_block(self.index)
        self.assertEqual(cat.count_deals(block), 22)
        digest = hashlib.sha256(block.encode("utf-8")).hexdigest()
        # Seed 22-deal block from main; this PR must not touch it.
        self.assertEqual(digest, "bf29e0b21cb5fa50e1f25d41c23f4d5a804012d266b6fa06d43712c9ecb454a6")
        self.assertEqual(len(block), 11513)

    def test_weekly_cannot_overwrite_or_unhide(self):
        curated = cat.published_articles(self.catalog)[0]
        incoming = {
            "generated": "2099-01-01",
            "articles": [
                {
                    "id": curated["id"],
                    "t": "HACKED TITLE",
                    "url": "https://evil.example/overwrite",
                    "f": "c2",
                    "status": "weekly",
                },
                {
                    "id": "c7-cell-5",
                    "t": "should stay hidden",
                    "url": "https://evil.example/unhide",
                    "f": "c7",
                    "status": "weekly",
                },
                {
                    "id": "c4-ai-4",
                    "t": "no_fulltext must stay hidden",
                    "url": "https://evil.example/brief",
                    "f": "c4",
                },
                {
                    "id": "w-20990101-newitem",
                    "t": "brand new weekly piece",
                    "url": "https://example.com/new",
                    "f": "c2",
                    "status": "weekly",
                },
            ],
            "deals": [{"url": "https://example.com/deal", "t": "x", "kinds": ["lic"]}],
        }
        previous = {"articles": [], "deals": []}
        merged = cat.protect_latest_payload(previous, incoming, self.catalog)
        ids = [a["id"] for a in merged["articles"]]
        self.assertIn(curated["id"], ids)
        kept = next(a for a in merged["articles"] if a["id"] == curated["id"])
        self.assertNotEqual(kept.get("t") or kept.get("title"), "HACKED TITLE")
        self.assertEqual(kept.get("status"), "published")
        self.assertNotIn("c7-cell-5", ids)
        self.assertNotIn("c4-ai-4", ids)
        self.assertIn("w-20990101-newitem", ids)
        weekly = next(a for a in merged["articles"] if a["id"] == "w-20990101-newitem")
        self.assertEqual(weekly["f"], "ai")
        self.assertEqual(weekly["field"], "AI 药物设计")
        self.assertTrue(weekly["field_color"].startswith("#"))
        self.assertEqual(merged["deals"], incoming["deals"])

    def test_weekly_card_cover_uses_img_then_papers(self):
        weekly = {"id": "w-20990101-newitem", "img": "content/weekly/2099-01-01/images/w.jpg"}
        self.assertEqual(cat.card_cover_src(weekly), weekly["img"])
        no_img = {"id": "w-20990101-newitem"}
        self.assertEqual(cat.card_cover_src(no_img), "img/papers/w-20990101-newitem.jpg?v=5")
        curated = {"id": "a-trap", "card_img": "img/a-trap_card.webp", "img": "img/papers/a-trap.jpg"}
        self.assertEqual(cat.card_cover_src(curated), "img/a-trap_card.webp")
        src = (ROOT / "index.html").read_text(encoding="utf-8")
        self.assertIn("function cardCoverSrc", src)
        self.assertIn("a.img", src)
        self.assertIn("img/papers/", src)

    def test_normalize_f_when_field_set_but_old_code(self):
        row = cat.normalize_article_fields({
            "id": "w-x",
            "f": "c2",
            "field": "AI 药物设计",
            "t": "weekly",
        })
        self.assertEqual(row["f"], "ai")
        self.assertEqual(row["field_label"], "AI药物设计")
        self.assertEqual(row["field_color"], "#44489A")

    def test_hidden_stub_title_is_neutral(self):
        html = (ROOT / "pages" / "article" / "c7-cell-5.html").read_text(encoding="utf-8")
        self.assertIn("noindex", html)
        self.assertIn("<title>未作为完整解读发布 · InLight</title>", html)
        self.assertNotIn("帕金森", html)

    def test_build_accepts_catalog_count_not_fixed_25(self):
        src = (ROOT / "build_pages.py").read_text(encoding="utf-8")
        self.assertIn("if len(published) < 1", src)
        self.assertNotIn("!= 25", src)

    def test_article_pages_and_images_resolve(self):
        for a in cat.published_articles(self.catalog):
            page = ROOT / "pages" / "article" / f"{a['id']}.html"
            self.assertTrue(page.exists(), page)
            html = page.read_text(encoding="utf-8")
            self.assertIn(a["title"][:12], html)
            self.assertIn(f"{a['id']}_card.webp", html)
            self.assertIn(f"{a['id']}_mech.webp", html)
            self.assertNotIn("noindex", html)
            self.assertTrue((ROOT / "img" / f"{a['id']}_card.webp").exists())
            self.assertTrue((ROOT / "img" / f"{a['id']}_mech.webp").exists())

    def test_primary_field_only_in_catalog_cards(self):
        for a in cat.published_articles(self.catalog):
            card = cat._public_card(a)
            self.assertEqual(card["tags"], [card["f"]])
            self.assertTrue(card["field_color"].startswith("#"))

    def test_ai_note_once_under_hero(self):
        for a in cat.published_articles(self.catalog):
            html = (ROOT / "pages" / "article" / f"{a['id']}.html").read_text(encoding="utf-8")
            hero = re.search(r'<div class="pv-hero">.*?</div>', html, re.S)
            self.assertIsNotNone(hero, a["id"])
            self.assertIn("AI 生成", hero.group(0), a["id"])
            self.assertNotIn("配图由 AI 生成，依据论文流程绘制", html, a["id"])

    def test_structured_files_untouched_source(self):
        for a in cat.published_articles(self.catalog):
            self.assertTrue(cat.article_json_path(a["id"]).exists())
            self.assertTrue(cat.article_md_path(a["id"]).exists())

    def test_disease_model_label_shown_animal_id_stable(self):
        self.assertIn('"animal": "疾病模型"', self.index)
        self.assertNotIn('"animal": "动物模型"', self.index)
        fields = cat.load_fields()
        animal = next(f for f in fields if f["k"] == "animal")
        self.assertEqual(animal["n"], "疾病模型")
        self.assertEqual(animal["field"], "疾病模型")
        self.assertEqual(cat.FIELD_KEYS["疾病模型"], "animal")
        self.assertEqual(cat.FIELD_KEYS["动物模型"], "animal")

    def test_article_rail_quick_look_author_and_coop(self):
        """Left rail: 速览 100–200 chars, 作者介绍 present, 合作 box; 作者节已移出正文."""
        expected = [
            "研究背景与待解问题",
            "研究设计",
            "核心结果",
            "机制解读",
            "局限与不确定",
            "临床/产业意义",
        ]
        for a in cat.published_articles(self.catalog):
            page = (ROOT / "pages" / "article" / f"{a['id']}.html").read_text(encoding="utf-8")
            self.assertIn('class="meta side2"', page, a["id"])
            look = re.search(r'<section class="sbox look">.*?<p>(.*?)</p>', page, re.S)
            self.assertIsNotNone(look, a["id"])
            text = re.sub(r"<[^>]+>", "", look.group(1))
            n = len(re.sub(r"\s+", "", text))
            self.assertGreaterEqual(n, 100, (a["id"], n))
            self.assertLessEqual(n, 200, (a["id"], n))
            self.assertIn('<span class="k">作者介绍</span>', page, a["id"])
            self.assertTrue((a.get("author_intro") or "").strip(), a["id"])
            self.assertIn("InSynBio · 前沿追踪", page, a["id"])
            self.assertIn("contact@therasik.com", page, a["id"])
            self.assertIn("科技新闻实时更新", page, a["id"])
            self.assertIn('class="sbox coop"', page, a["id"])
            amain = re.search(r'<div class="amain">(.*)</div>\s*</div>\s*</article>', page, re.S)
            self.assertIsNotNone(amain, a["id"])
            body = amain.group(1)
            self.assertNotIn("作者、出处与核对", body, a["id"])
            self.assertNotIn('class="pv-card"', body, a["id"])
            secs = re.findall(r'<div class="sec"><h2>([^<]+)</h2>', body)
            self.assertEqual(secs, expected, a["id"])
            self.assertIn("研究设计", body, a["id"])
            self.assertIn("局限与不确定", body, a["id"])
        src = (ROOT / "index.html").read_text(encoding="utf-8")
        self.assertIn("function renderPaper", src)
        self.assertIn("sbox look", src)
        self.assertIn("COOP_AD", src)
        self.assertIn("作者、出处与核对", src)


if __name__ == "__main__":
    unittest.main()
