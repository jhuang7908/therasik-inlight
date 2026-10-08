#!/usr/bin/env python3
"""Nine-field taxonomy, classification, and index.html migration.

Weekly classification and migration live here so run_weekly.py / index.html
only need small hooks. audit.csv is never imported.
"""

from __future__ import annotations

import argparse
import inspect
import json
import logging
import os
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parent
CORRECTIONS_PATH = ROOT / "data" / "corrections_v3.json"

FIELD_ORDER = ("f1", "f2", "f3", "f4", "f5", "f6", "f7", "f8", "f9")

FIELDS = {
    "f1": "类器官",
    "f2": "动物模型",
    "f3": "AI 药物设计",
    "f4": "肿瘤免疫与细胞治疗",
    "f5": "自身免疫与移植免疫",
    "f6": "疫苗与感染免疫",
    "f7": "抗体工程",
    "f8": "核酸与基因治疗（含 LNP 递送）",
    "f9": "精准肿瘤与临床转化",
}

FIELD_BLURBS = {
    "f1": "疾病建模、培养方法、器官芯片",
    "f2": "人源化小鼠、基因编辑、模型验证",
    "f3": "结构预测、生成式设计、药效预测",
    "f4": "检查点、微环境、抗肿瘤细胞治疗",
    "f5": "自免机制、免疫重置、移植免疫",
    "f6": "疫苗、佐剂、感染免疫",
    "f7": "双抗、ADC 格式、纳米抗体、Fc 改造",
    "f8": "siRNA、ASO、LNP 与体内基因递送",
    "f9": "患者来源肿瘤模型、药敏、临床转化",
}

FIELD_IMG = {
    "f1": "organoid.jpg",
    "f2": "animal-model.jpg",
    "f3": "ai-drug-design.jpg",
    "f4": "tumor-immunology.jpg",
    "f5": "autoimmune.jpg",
    "f6": "vaccine.jpg",
    "f7": "antibody-engineering.jpg",
    "f8": "lnp-oligo.jpg",
    "f9": "cell-therapy.jpg",
}

FIELD_MOTIF = {
    "f1": "organoid",
    "f2": "model",
    "f3": "net",
    "f4": "cell",
    "f5": "antibody",
    "f6": "vaccine",
    "f7": "antibody",
    "f8": "lnp",
    "f9": "clin",
}

# Old c1–c9 → default new id. Corrections override every seed article.
OLD_TO_NEW = {
    "c1": "f1",
    "c2": "f3",
    "c3": "f4",
    "c4": "f5",
    "c5": "f2",
    "c6": "f7",
    "c7": "f4",
    "c8": "f6",
    "c9": "f8",
}

EXPECTED_PRIMARY_COUNTS = {
    "f1": 3,
    "f2": 2,
    "f3": 6,
    "f4": 10,
    "f5": 8,
    "f6": 5,
    "f7": 2,
    "f8": 6,
    "f9": 3,
}

NONE_FIELD = "none"
EXCLUDE_NO_MATCH = "no_matching_field"

CLASSIFY_TOOL_NAME = "submit_field_classification"

FIELD_PROMPT_RULES = """领域 field 只能是 f1–f9 之一，或 none（不属于任何领域，排除、不展示）。
每篇恰好一个主领域 primary_field；related_fields 列出其它相关领域，不得重复主领域。

按研究的主要对象/贡献分类，不按用了什么工具：
- f1 类器官：类器官生物学、培养方法、非肿瘤疾病建模。肿瘤类器官资源库/药敏/基因依赖图谱走 f9。
- f2 动物模型：只有模型本身是贡献（建系、验证）。只是在动物里验证疗法，走疗法对应领域。
- f3 AI 药物设计：生成式设计、结构/亲和力预测、AI 发现的分子。
- f4 肿瘤免疫与细胞治疗：检查点、TME、抗肿瘤 CAR-T/TCR-T/TIL。
- f5 自身免疫与移植免疫：自免机制与治疗、移植排斥、临床异种器官。
- f6 疫苗与感染免疫：疫苗、佐剂、感染免疫。
- f7 抗体工程：格式、可开发性、体积/Fc 改造。AI 方法走 f3；ADC 注册向肿瘤临床走 f9。
- f8 核酸与基因治疗（含 LNP 递送）：核酸化学、LNP、体内基因/mRNA 递送。
- f9 精准肿瘤与临床转化：患者来源肿瘤模型资源、药敏、靶向肿瘤临床转化。

决胜：
- 类器官 vs 精准肿瘤：癌症模型资源/药敏/依赖图谱 → f9；类器官方法学或非肿瘤疾病 → f1。
- 细胞治疗：肿瘤 → f4；自免是主题 → f5；非免疫细胞替代（如帕金森多巴胺前体）→ none。
- 拿不准就 needs_review=true，不要猜。可以返回 none，不要硬塞。
"""


@dataclass
class Classification:
    primary_field: str | None
    related_fields: list[str] = field(default_factory=list)
    needs_review: bool = False
    excluded: bool = False
    exclude_reason: str | None = None

    def to_site(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "f": self.primary_field,
            "rf": list(self.related_fields),
            "needs_review": bool(self.needs_review),
        }
        if self.excluded:
            out["excluded"] = True
            if self.exclude_reason:
                out["exclude_reason"] = self.exclude_reason
        return out


def field_catalog() -> list[dict[str, str]]:
    return [{"k": k, "n": FIELDS[k], "d": FIELD_BLURBS[k]} for k in FIELD_ORDER]


def claude_create_kwargs(**kwargs: Any) -> dict[str, Any]:
    """Keyword arguments for Anthropic messages.create. Never pass temperature."""
    kwargs.pop("temperature", None)
    return kwargs


def anthropic_create_parameters() -> set[str]:
    from anthropic.resources.messages.messages import Messages

    return set(inspect.signature(Messages.create).parameters) - {"self"}


def assert_claude_kwargs_match_sdk(kwargs: dict[str, Any]) -> None:
    """Fail if call kwargs are not accepted by the installed Anthropic SDK."""
    params = anthropic_create_parameters()
    unknown = set(kwargs) - params
    if unknown:
        raise TypeError(f"Claude call kwargs not in Messages.create: {sorted(unknown)}")
    if "temperature" in kwargs:
        raise TypeError("Claude call must not pass temperature")


def load_corrections(path: Path | None = None) -> dict[str, Any]:
    src = path or CORRECTIONS_PATH
    data = json.loads(src.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not isinstance(data.get("articles"), list):
        raise ValueError(f"corrections file missing articles list: {src}")
    return data


def corrections_by_id(data: dict[str, Any] | None = None) -> dict[str, dict[str, Any]]:
    payload = data if data is not None else load_corrections()
    out = {}
    for row in payload["articles"]:
        aid = row.get("id")
        if not aid:
            raise ValueError("correction row missing id")
        if aid in out:
            raise ValueError(f"duplicate correction id: {aid}")
        out[aid] = row
    return out


def _clean_related(primary: str | None, related: Iterable[str] | None) -> list[str]:
    seen: list[str] = []
    for item in related or []:
        if item == NONE_FIELD or item == primary:
            continue
        if item not in FIELDS:
            continue
        if item not in seen:
            seen.append(item)
    return seen


def normalize_classification(raw: dict[str, Any]) -> Classification:
    """Validate a model or corrections row. Invalid/none → exclude, do not guess."""
    excluded = bool(raw.get("excluded"))
    reason = raw.get("exclude_reason")
    needs_review = bool(raw.get("needs_review"))
    primary = raw.get("primary_field")
    if primary == "":
        primary = None
    if isinstance(primary, str):
        primary = primary.strip()

    if excluded or primary in (None, NONE_FIELD):
        return Classification(
            primary_field=None,
            related_fields=[],
            needs_review=needs_review,
            excluded=True,
            exclude_reason=reason or EXCLUDE_NO_MATCH,
        )

    if primary not in FIELDS:
        return Classification(
            primary_field=None,
            related_fields=[],
            needs_review=True,
            excluded=True,
            exclude_reason=EXCLUDE_NO_MATCH,
        )

    return Classification(
        primary_field=primary,
        related_fields=_clean_related(primary, raw.get("related_fields")),
        needs_review=needs_review,
        excluded=False,
        exclude_reason=None,
    )


def classification_from_correction(row: dict[str, Any]) -> Classification:
    return normalize_classification(row)


def is_visible(article: dict[str, Any]) -> bool:
    if article.get("excluded"):
        return False
    return bool(article.get("f") or article.get("primary_field"))


def visible_articles(articles: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    return [a for a in articles if is_visible(a)]


def article_primary(article: dict[str, Any]) -> str | None:
    if article.get("excluded"):
        return None
    return article.get("f") or article.get("primary_field")


def section_counts(articles: Iterable[dict[str, Any]]) -> dict[str, int]:
    counts = {k: 0 for k in FIELD_ORDER}
    for art in visible_articles(articles):
        primary = article_primary(art)
        if primary in counts:
            counts[primary] += 1
    return counts


def articles_in_field(articles: Iterable[dict[str, Any]], field_id: str) -> list[dict[str, Any]]:
    return [a for a in visible_articles(articles) if article_primary(a) == field_id]


def parse_cat(html: str) -> list[dict[str, Any]]:
    """Parse CAT article objects from index.html (single-quoted JS literals)."""
    match = re.search(r"const CAT=\[(.*?)\n\];", html, flags=re.S)
    if not match:
        raise ValueError("CAT array not found in HTML")
    body = match.group(1)
    rows: list[dict[str, Any]] = []
    for block in re.finditer(r"\{id:'([^']+)',(.*?)\}(?=,\n \{id:|\s*$)", body, flags=re.S):
        aid = block.group(1)
        rest = block.group(2)
        rec: dict[str, Any] = {"id": aid}
        fm = re.search(r"\bf:(null|'[^']*')", rest)
        rec["f"] = None if not fm or fm.group(1) == "null" else fm.group(1).strip("'")
        rec["excluded"] = bool(re.search(r"\bexcluded:true", rest))
        rm = re.search(r"\brf:\[([^\]]*)\]", rest)
        if rm:
            rec["rf"] = re.findall(r"'([^']+)'", rm.group(1))
        else:
            rec["rf"] = []
        tm = re.search(r"\btags:\[([^\]]*)\]", rest)
        rec["tags"] = re.findall(r"'([^']+)'", tm.group(1)) if tm else []
        rec["needs_review"] = bool(re.search(r"\bneeds_review:true", rest))
        er = re.search(r"\bexclude_reason:'([^']+)'", rest)
        if er:
            rec["exclude_reason"] = er.group(1)
        rows.append(rec)
    return rows


def _js_str_list(values: Iterable[str]) -> str:
    return "[" + ",".join(f"'{v}'" for v in values) + "]"


def _patch_cat_article(html: str, article_id: str, cls: Classification) -> str:
    pattern = re.compile(
        rf"(\{{id:'{re.escape(article_id)}',)f:(?:null|'[^']*')(?:,excluded:true(?:,exclude_reason:'[^']*')?)?(?:,rf:\[[^\]]*\])?"
    )
    if cls.excluded:
        repl = rf"\1f:null,excluded:true,exclude_reason:'{cls.exclude_reason or EXCLUDE_NO_MATCH}',rf:[]"
    else:
        repl = rf"\1f:'{cls.primary_field}',rf:{_js_str_list(cls.related_fields)}"
    html, n = pattern.subn(repl, html, count=1)
    if n != 1:
        raise ValueError(f"failed to patch primary fields for {article_id}")

    tags = [] if cls.excluded or not cls.primary_field else [cls.primary_field, *cls.related_fields]
    html, n = re.subn(
        rf"(\{{id:'{re.escape(article_id)}',.*?)\btags:\[[^\]]*\]",
        lambda m: m.group(1) + "tags:" + _js_str_list(tags),
        html,
        count=1,
        flags=re.S,
    )
    if n != 1:
        raise ValueError(f"failed to patch tags for {article_id}")
    return html


def apply_corrections_to_articles(
    articles: list[dict[str, Any]],
    corrections: dict[str, dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    by_id = corrections if corrections is not None else corrections_by_id()
    out = []
    for art in articles:
        row = dict(art)
        spec = by_id.get(art["id"])
        if spec is None:
            out.append(row)
            continue
        cls = classification_from_correction(spec)
        row.update(cls.to_site())
        if cls.excluded:
            row["tags"] = []
        else:
            row["tags"] = [cls.primary_field, *cls.related_fields]
        out.append(row)
    return out


def migrate_index_html(html: str, corrections: dict[str, dict[str, Any]] | None = None) -> str:
    by_id = corrections if corrections is not None else corrections_by_id()
    parsed = parse_cat(html)
    missing = [aid for aid in by_id if aid not in {a["id"] for a in parsed}]
    if missing:
        raise ValueError(f"corrections reference unknown articles: {missing}")
    for art in parsed:
        spec = by_id.get(art["id"])
        if spec is None:
            raise ValueError(f"no correction for {art['id']}")
        html = _patch_cat_article(html, art["id"], classification_from_correction(spec))
    return html


def classify_tool_schema() -> dict[str, Any]:
    return {
        "name": CLASSIFY_TOOL_NAME,
        "description": "Submit primary and related field classification for one paper",
        "input_schema": {
            "type": "object",
            "properties": {
                "primary_field": {
                    "type": "string",
                    "enum": [*FIELD_ORDER, NONE_FIELD],
                    "description": "Single primary field, or none if out of scope",
                },
                "related_fields": {
                    "type": "array",
                    "items": {"type": "string", "enum": list(FIELD_ORDER)},
                    "description": "Other relevant fields, never including primary",
                },
                "needs_review": {
                    "type": "boolean",
                    "description": "True if uncertain; do not guess a field",
                },
            },
            "required": ["primary_field", "related_fields", "needs_review"],
        },
    }


def classify_prompt(item: dict[str, Any]) -> str:
    payload = {
        "title": item.get("title") or item.get("t"),
        "journal": item.get("journal") or item.get("j"),
        "summary": item.get("summary") or item.get("lead") or item.get("sum") or item.get("body"),
        "source": item.get("source"),
    }
    return (
        "你是前沿追踪的领域编辑。根据标题和摘要，给出恰好一个主领域，以及其它相关领域。\n\n"
        f"{FIELD_PROMPT_RULES}\n\n"
        "调用 submit_field_classification 提交。\n\n"
        f"条目：\n{json.dumps(payload, ensure_ascii=False)}"
    )


def parse_classifier_tool_result(data: dict[str, Any]) -> Classification:
    return normalize_classification(
        {
            "primary_field": data.get("primary_field"),
            "related_fields": data.get("related_fields") or [],
            "needs_review": bool(data.get("needs_review")),
            "excluded": data.get("primary_field") in (None, NONE_FIELD),
        }
    )


def classify_item_with_client(item: dict[str, Any], client: Any, model: str) -> Classification:
    kwargs = claude_create_kwargs(
        model=model,
        max_tokens=400,
        tools=[classify_tool_schema()],
        tool_choice={"type": "auto"},
        messages=[{"role": "user", "content": classify_prompt(item)}],
    )
    assert_claude_kwargs_match_sdk(kwargs)
    message = client.messages.create(**kwargs)
    payload = None
    for block in getattr(message, "content", []) or []:
        if getattr(block, "type", None) == "tool_use" and getattr(block, "name", None) == CLASSIFY_TOOL_NAME:
            payload = getattr(block, "input", None)
            break
    if not isinstance(payload, dict):
        logging.warning("classifier returned no tool result; marking needs_review")
        return Classification(
            primary_field=None,
            related_fields=[],
            needs_review=True,
            excluded=True,
            exclude_reason=EXCLUDE_NO_MATCH,
        )
    return parse_classifier_tool_result(payload)


def classify_draft_articles(
    articles: list[dict[str, Any]],
    sources_by_url: dict[str, dict[str, Any]] | None = None,
    client: Any | None = None,
    model: str | None = None,
) -> list[dict[str, Any]]:
    """Attach new-taxonomy fields to weekly draft articles. Does not guess."""
    sources_by_url = sources_by_url or {}
    model = model or os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-5-5")
    if client is None:
        from anthropic import Anthropic

        client = Anthropic()
    out = []
    for raw in articles:
        src = dict(sources_by_url.get(raw.get("url"), {}))
        src.update(raw)
        cls = classify_item_with_client(src, client, model)
        item = dict(raw)
        item["field"] = cls.primary_field
        item["primary_field"] = cls.primary_field
        item["related_fields"] = cls.related_fields
        item["needs_review"] = cls.needs_review
        item["excluded"] = cls.excluded
        if cls.exclude_reason:
            item["exclude_reason"] = cls.exclude_reason
        out.append(item)
    return out


def site_classification_fields(item: dict[str, Any]) -> dict[str, Any]:
    cls = normalize_classification(
        {
            "primary_field": item.get("primary_field", item.get("field")),
            "related_fields": item.get("related_fields") or item.get("rf") or [],
            "needs_review": item.get("needs_review", False),
            "excluded": item.get("excluded", False),
            "exclude_reason": item.get("exclude_reason"),
        }
    )
    return cls.to_site()


def js_fields_literal() -> str:
    parts = [
        "{{k:'{k}',n:'{n}',d:'{d}'}}".format(k=k, n=FIELDS[k], d=FIELD_BLURBS[k])
        for k in FIELD_ORDER
    ]
    return "[" + ",".join(parts) + "]"


def js_fnames_literal() -> str:
    return "{" + ",".join(f"{k}:'{FIELDS[k]}'" for k in FIELD_ORDER) + "}"


def patch_frontend_constants(html: str) -> str:
    html = re.sub(
        r"const FNAMES=\{[^;]+\};",
        f"const FNAMES={js_fnames_literal()};",
        html,
        count=1,
    )
    html = re.sub(
        r"const FIELDS=\[[^\]]+\];",
        f"const FIELDS={js_fields_literal()};",
        html,
        count=1,
    )
    html = re.sub(
        r"const FIELD_IMG=\{[^;]+\};",
        "const FIELD_IMG={"
        + ",".join(f"{k}:'{FIELD_IMG[k]}'" for k in FIELD_ORDER)
        + "};",
        html,
        count=1,
    )
    html = re.sub(
        r"const FIELD_MOTIF=\{[^;]+\};",
        "const FIELD_MOTIF={"
        + ",".join(f"{k}:'{FIELD_MOTIF[k]}'" for k in FIELD_ORDER)
        + "};",
        html,
        count=1,
    )
    return html


def migrate_file(index_path: Path | None = None) -> dict[str, int]:
    path = index_path or (ROOT / "index.html")
    html = path.read_text(encoding="utf-8")
    html = migrate_index_html(html)
    html = patch_frontend_constants(html)
    path.write_text(html, encoding="utf-8")
    counts = section_counts(parse_cat(html))
    logging.info("migrated %s section counts=%s", path, counts)
    return counts


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="InLight field taxonomy helpers")
    parser.add_argument("command", choices=["migrate", "counts"])
    parser.add_argument("--index", type=Path, default=ROOT / "index.html")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    if args.command == "migrate":
        counts = migrate_file(args.index)
        print(json.dumps(counts, ensure_ascii=False, indent=2))
        return 0
    articles = parse_cat(args.index.read_text(encoding="utf-8"))
    print(json.dumps(section_counts(articles), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
