"""Field taxonomy: one primary per article, exclusions, migration counts."""

from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace

import pytest

from inlight_fields import (
    CORRECTIONS_PATH,
    EXPECTED_PRIMARY_COUNTS,
    EXCLUDE_NO_MATCH,
    FIELD_ORDER,
    FIELDS,
    articles_in_field,
    apply_corrections_to_articles,
    classification_from_correction,
    classify_draft_articles,
    corrections_by_id,
    is_visible,
    load_corrections,
    migrate_index_html,
    normalize_classification,
    parse_cat,
    parse_classifier_tool_result,
    patch_frontend_constants,
    section_counts,
    site_classification_fields,
)

ROOT = Path(__file__).resolve().parents[1]


def test_corrections_file_is_v3_and_complete():
    data = load_corrections()
    assert data["version"] == 3
    by_id = corrections_by_id(data)
    html = (ROOT / "index.html").read_text(encoding="utf-8")
    cat_ids = {a["id"] for a in parse_cat(html)}
    assert set(by_id) == cat_ids
    assert all(row.get("needs_review") is False for row in by_id.values())


def test_audit_csv_is_never_imported():
    for rel in ("inlight_fields.py", "run_weekly.py"):
        tree = ast.parse((ROOT / rel).read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                text = ast.dump(node)
                assert "audit" not in text


def test_normalize_none_and_invalid_are_excluded():
    none = normalize_classification({"primary_field": "none", "related_fields": ["f1"]})
    assert none.excluded and none.primary_field is None
    assert none.exclude_reason == EXCLUDE_NO_MATCH
    bad = normalize_classification({"primary_field": "c7", "related_fields": []})
    assert bad.excluded and bad.needs_review
    flagged = normalize_classification(
        {"primary_field": None, "excluded": True, "exclude_reason": EXCLUDE_NO_MATCH}
    )
    assert flagged.excluded and flagged.primary_field is None


def test_related_fields_drop_primary_and_unknown():
    cls = normalize_classification(
        {"primary_field": "f4", "related_fields": ["f4", "f3", "nope", "f3"]}
    )
    assert cls.primary_field == "f4"
    assert cls.related_fields == ["f3"]
    assert not cls.excluded


def test_excluded_flag_in_corrections_is_generic():
    cls = classification_from_correction(
        {
            "id": "demo-out",
            "primary_field": "f4",
            "related_fields": ["f3"],
            "excluded": True,
            "exclude_reason": EXCLUDE_NO_MATCH,
            "needs_review": False,
        }
    )
    assert cls.excluded
    assert cls.primary_field is None
    assert cls.related_fields == []


def test_c7_cell_5_is_excluded_not_deleted():
    row = corrections_by_id()["c7-cell-5"]
    cls = classification_from_correction(row)
    assert cls.excluded
    assert cls.primary_field is None
    assert cls.exclude_reason == EXCLUDE_NO_MATCH
    html = (ROOT / "index.html").read_text(encoding="utf-8")
    assert "c7-cell-5" in html
    assert "STEM-PD" in html


def test_organoid_cancer_resources_are_f9():
    by_id = corrections_by_id()
    assert by_id["c1-org-2"]["primary_field"] == "f9"
    assert by_id["c1-org-3"]["primary_field"] == "f9"
    assert by_id["c1-org-1"]["primary_field"] == "f1"
    assert by_id["c1-org-4"]["primary_field"] == "f1"
    assert by_id["c1-org-5"]["primary_field"] == "f1"


def test_organoid_vs_precision_tie_break_on_new_items():
    resource = parse_classifier_tool_result(
        {
            "primary_field": "f9",
            "related_fields": ["f1"],
            "needs_review": False,
        }
    )
    biology = parse_classifier_tool_result(
        {
            "primary_field": "f1",
            "related_fields": [],
            "needs_review": False,
        }
    )
    assert resource.primary_field == "f9"
    assert biology.primary_field == "f1"


def test_apply_corrections_builds_expected_section_counts():
    html = (ROOT / "index.html").read_text(encoding="utf-8")
    raw = parse_cat(html)
    migrated = apply_corrections_to_articles(raw)
    counts = section_counts(migrated)
    assert counts == EXPECTED_PRIMARY_COUNTS
    assert sum(counts.values()) == 45
    assert sum(1 for a in migrated if a.get("excluded")) == 1
    assert not is_visible(next(a for a in migrated if a["id"] == "c7-cell-5"))
    for art in migrated:
        if art.get("excluded"):
            continue
        assert art["f"] in FIELDS
        assert art["f"] not in art.get("rf", [])
        assert articles_in_field(migrated, art["f"])
    # An article is in exactly one section
    for field_id in FIELD_ORDER:
        ids = [a["id"] for a in articles_in_field(migrated, field_id)]
        assert len(ids) == len(set(ids))
    shown_ids = [a["id"] for a in migrated if is_visible(a)]
    assert len(shown_ids) == 45


def test_migrate_index_html_patches_cat_and_hides_excluded(tmp_path):
    html = (ROOT / "index.html").read_text(encoding="utf-8")
    if "const FNAMES={f1:" not in html:
        html = patch_frontend_constants(html)
    migrated = migrate_index_html(html)
    cat = parse_cat(migrated)
    assert section_counts(cat) == EXPECTED_PRIMARY_COUNTS
    stem = next(a for a in cat if a["id"] == "c7-cell-5")
    assert stem["excluded"] is True
    assert stem["f"] is None
    org = next(a for a in cat if a["id"] == "c1-org-2")
    assert org["f"] == "f9"
    assert "f1" in org["rf"]
    trap = next(a for a in cat if a["id"] == "a-trap")
    assert trap["f"] == "f4"
    assert articles_in_field(cat, "f4")
    assert all(a["id"] != "c7-cell-5" for a in articles_in_field(cat, "f4"))
    dest = tmp_path / "index.html"
    dest.write_text(migrated, encoding="utf-8")
    assert "c7-cell-5" in dest.read_text(encoding="utf-8")


def test_site_classification_fields_for_weekly_none():
    fields = site_classification_fields(
        {
            "field": "none",
            "primary_field": "none",
            "related_fields": [],
            "needs_review": False,
        }
    )
    assert fields["f"] is None
    assert fields["excluded"] is True


def test_classify_draft_none_is_excluded_not_forced(monkeypatch):
    class Block:
        type = "tool_use"
        name = "submit_field_classification"
        input = {"primary_field": "none", "related_fields": [], "needs_review": False}

    class Client:
        class messages:
            @staticmethod
            def create(**kwargs):
                assert "temperature" not in kwargs
                return SimpleNamespace(content=[Block()])

    articles = classify_draft_articles(
        [{"url": "https://doi.org/10.0/x", "title": "STEM-PD", "field": "c7"}],
        sources_by_url={"https://doi.org/10.0/x": {"summary": "Parkinson cell replacement"}},
        client=Client(),
        model="claude-sonnet-5-5",
    )
    assert articles[0]["excluded"] is True
    assert articles[0]["field"] is None
    assert articles[0]["primary_field"] is None


def test_uncertain_new_item_sets_needs_review(monkeypatch):
    class Block:
        type = "tool_use"
        name = "submit_field_classification"
        input = {"primary_field": "none", "related_fields": [], "needs_review": True}

    class Client:
        class messages:
            @staticmethod
            def create(**kwargs):
                return SimpleNamespace(content=[Block()])

    articles = classify_draft_articles(
        [{"url": "u", "title": "unclear"}],
        sources_by_url={"u": {"summary": "???"}},
        client=Client(),
        model="claude-sonnet-5-5",
    )
    assert articles[0]["needs_review"] is True
    assert articles[0]["excluded"] is True


def test_corrections_path_is_v3_not_v2():
    assert CORRECTIONS_PATH.name == "corrections_v3.json"
    assert not (ROOT / "data" / "corrections_v2.json").exists()
