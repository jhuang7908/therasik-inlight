#!/usr/bin/env python3
"""SEC EDGAR Deal Extraction Module.

THE ONLY path for deal extraction. All deals come from SEC filings with strict
quote-based verification. No model free text is published.

Design principles:
1. Every quote must be an exact whitespace-normalized substring of the filing.
2. Counterparty verification uses whole-word matching with a small explicit alias table.
3. Role/direction is determined by code from type_quote using fixed patterns.
4. Amounts are parsed by code from verified quotes.
5. Output records contain no model free text - titles built from verified fields only.
"""

from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass, field
from datetime import date, timedelta
from enum import Enum
from typing import Any

# =============================================================================
# CONSTANTS
# =============================================================================

class DealType(Enum):
    ACQUISITION = "acquisition"
    MERGER = "merger"
    LICENSE_COLLABORATION = "license_collaboration"
    EQUITY_FINANCING = "equity_financing"
    DEBT_FACILITY = "debt_facility"
    OBLIGATION_BUYOUT = "obligation_buyout"
    NONE = "none"


class AmountKind(Enum):
    """Amount kinds - narrowed to in-scope deal types only."""
    UPFRONT = "upfront"
    PURCHASE_PRICE = "purchase_price"
    MILESTONES_TOTAL = "milestones_total"
    # Out of scope - kept for backwards compatibility but never published
    EQUITY = "equity"
    FACILITY_SIZE = "facility_size"
    DRAWN = "drawn"
    OTHER = "other"

# Only these amount kinds are published
IN_SCOPE_AMOUNT_KINDS = {AmountKind.UPFRONT, AmountKind.PURCHASE_PRICE, AmountKind.MILESTONES_TOTAL}


# Small explicit alias table - NEVER map Roche↔Genentech, Sanofi↔Regeneron, AZ↔Amazon
COMPANY_ALIASES: dict[str, list[str]] = {
    'bristol-myers squibb': ['bristol-myers squibb', 'bristol myers squibb', 'bms'],
    'bms': ['bristol-myers squibb', 'bristol myers squibb', 'bms'],
    'glaxosmithkline': ['glaxosmithkline', 'gsk'],
    'gsk': ['glaxosmithkline', 'gsk'],
    'johnson & johnson': ['johnson & johnson', 'johnson and johnson', 'j&j', 'jnj', 'janssen'],
    'eli lilly': ['eli lilly', 'lilly'],
    'lilly': ['eli lilly', 'lilly'],
    'boehringer ingelheim': ['boehringer ingelheim', 'boehringer'],
    'boehringer': ['boehringer ingelheim', 'boehringer'],
    'daiichi sankyo': ['daiichi sankyo', 'daiichi-sankyo'],
}

# Legal suffixes to normalize at end only
LEGAL_SUFFIXES = [
    r',?\s*Inc\.?$', r',?\s*Ltd\.?$', r',?\s*LLC\.?$', r',?\s*plc\.?$',
    r',?\s*AG$', r',?\s*S\.A\.?$', r',?\s*Corp\.?$', r',?\s*Corporation$',
    r',?\s*Company$', r',?\s*Co\.?$', r',?\s*Limited$', r',?\s*L\.P\.?$',
    r',?\s*N\.V\.?$', r',?\s*GmbH$', r',?\s*SE$',
]

# NARROW SCOPE: Only two deal types are ever published
# (a) License or collaboration agreements NEWLY entered into
# (b) Definitive acquisition or merger agreements
# Everything else is logged as 'out_of_scope' and never published:
# loans, credit facilities, ATMs, equity offerings, warrants, PIPEs,
# amendments (without new license), terminations, assignments, divestitures.

# IN-SCOPE deal types
IN_SCOPE_DEAL_TYPES = {DealType.LICENSE_COLLABORATION, DealType.ACQUISITION, DealType.MERGER}

# Tool schema for Claude - strict quote-based extraction
DEAL_EXTRACTION_SCHEMA = {
    "name": "extract_deal",
    "description": """Extract deal information from SEC filing. ONLY extract:
1. NEW license or collaboration agreements (must have 'grants...license' or 'collaboration agreement' wording)
2. Definitive acquisition or merger agreements (must have 'acquire' or 'merger' wording)

Do NOT extract: loans, credit facilities, ATMs, equity offerings, warrants, PIPEs, amendments to existing agreements, terminations (Item 1.02), assignments, or divestitures.

For each amount, provide a quote containing BOTH the dollar amount AND a role keyword (upfront, milestone, aggregate, purchase price, or per share).""",
    "input_schema": {
        "type": "object",
        "properties": {
            "deal_type": {
                "type": "string",
                "enum": ["acquisition", "merger", "license_collaboration", "none"],
                "description": "Type of deal: acquisition, merger, license_collaboration, or 'none' if no in-scope deal"
            },
            "counterparty_name": {
                "type": "string",
                "description": "Name of the counterparty (the filer is always one party)"
            },
            "type_quote": {
                "type": "string",
                "description": "Exact verbatim quote showing deal type. For licenses: must contain 'grants...license' or 'collaboration'. For acquisitions: must contain 'acquire' or 'merger'. Quote must contain counterparty name."
            },
            "counterparty_quote": {
                "type": "string",
                "description": "Exact verbatim quote from parties clause or defined-term clause containing counterparty name"
            },
            "amounts": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "kind": {
                            "type": "string",
                            "enum": ["upfront", "purchase_price", "milestones_total"],
                            "description": "upfront (initial payment), purchase_price (acquisition price), or milestones_total (aggregate milestone payments)"
                        },
                        "quote": {
                            "type": "string",
                            "description": "Exact verbatim quote containing BOTH the dollar amount AND the role keyword (upfront/milestone/aggregate/purchase price/per share)"
                        }
                    },
                    "required": ["kind", "quote"]
                },
                "description": "Amount quotes with kind and exact verbatim quote containing both amount and role keyword"
            }
        },
        "required": ["deal_type"]
    }
}

# =============================================================================
# INDEPENDENT VERIFIER (Item 3)
# =============================================================================

# Module-level flag for disabling verifier in tests
VERIFIER_ENABLED = True

DEAL_VERIFICATION_SCHEMA = {
    "name": "verify_deal",
    "description": """Verify each field of a deal is supported by the filing text.
For each field, determine if it is SUPPORTED or UNSUPPORTED and provide a verbatim quote from the filing that proves your answer.
A field is SUPPORTED if the filing text contains explicit evidence for that exact value.
A field is UNSUPPORTED if there is no explicit evidence or if the evidence contradicts the claim.""",
    "input_schema": {
        "type": "object",
        "properties": {
            "company": {
                "type": "object",
                "properties": {
                    "verdict": {"type": "string", "enum": ["supported", "unsupported"]},
                    "quote": {"type": "string", "description": "Exact verbatim quote from filing proving the company name"}
                },
                "required": ["verdict", "quote"]
            },
            "counterparty": {
                "type": "object",
                "properties": {
                    "verdict": {"type": "string", "enum": ["supported", "unsupported"]},
                    "quote": {"type": "string", "description": "Exact verbatim quote from filing proving the counterparty name"}
                },
                "required": ["verdict", "quote"]
            },
            "deal_type": {
                "type": "object",
                "properties": {
                    "verdict": {"type": "string", "enum": ["supported", "unsupported"]},
                    "quote": {"type": "string", "description": "Exact verbatim quote from filing proving the deal type"}
                },
                "required": ["verdict", "quote"]
            },
            "amounts": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "role": {"type": "string", "description": "The role/kind of this amount (e.g., upfront, purchase_price, milestones)"},
                        "value": {"type": "string", "description": "The amount value being verified"},
                        "verdict": {"type": "string", "enum": ["supported", "unsupported"]},
                        "quote": {"type": "string", "description": "Exact verbatim quote from filing proving this amount with its role"}
                    },
                    "required": ["role", "value", "verdict", "quote"]
                },
                "description": "Verification for each amount in the deal"
            },
            "date": {
                "type": "object",
                "properties": {
                    "verdict": {"type": "string", "enum": ["supported", "unsupported"]},
                    "quote": {"type": "string", "description": "Exact verbatim quote from filing proving the date"}
                },
                "required": ["verdict", "quote"]
            }
        },
        "required": ["company", "counterparty", "deal_type", "amounts", "date"]
    }
}


def _verifier_field_supported(field_obj: Any, filing_text: str) -> bool:
    """Fail-closed check for a verifier {verdict, quote} object.

    A field passes only if verdict is exactly "supported" AND the quote is
    non-empty and a verbatim whitespace-normalized substring of the filing.
    Missing objects, unknown verdicts, empty quotes, and paraphrases all fail.
    """
    if not isinstance(field_obj, dict):
        return False
    if field_obj.get('verdict') != 'supported':
        return False
    quote = field_obj.get('quote')
    if not isinstance(quote, str) or not quote.strip():
        return False
    return verify_quote_in_filing(quote, filing_text)


def rebuild_published_deal(deal: dict) -> dict:
    """Rebuild title, headline money and deal lines from the amounts that remain.

    Headline money is only a non-conditional upfront or purchase price. Conditional
    milestone totals never become the title suffix. A dropped amount is removed
    from the deal line, the title and the money field — no stale （amount） remains.
    """
    deal = dict(deal)
    amounts = [a for a in (deal.get('verified_amounts') or []) if isinstance(a, dict)]
    deal['verified_amounts'] = amounts

    detail_parts = []
    for amt in amounts:
        kind = amt.get('kind', '')
        rendered = amt.get('rendered', '')
        if kind == 'upfront':
            detail_parts.append(f"首付：{rendered}")
        elif kind == 'purchase_price':
            detail_parts.append(f"收购对价：{rendered}")
        elif kind == 'milestones_total':
            detail_parts.append(f"里程碑：{rendered}")
    deal['structure'] = ' | '.join(detail_parts)

    headline = ''
    for amt in amounts:
        if amt.get('up_to'):
            continue
        if amt.get('kind') in ('upfront', 'purchase_price'):
            headline = amt.get('rendered', '') or ''
            break
    deal['money'] = headline

    title = re.sub(r'（[^）]+）$', '', deal.get('title') or '')
    if headline:
        title = f"{title}（{headline}）"
    deal['title'] = title
    return deal


def verify_deal_with_claude(
    deal: dict,
    filing_text: str,
    claude_client: Any,
    timeout_seconds: float = 30.0
) -> dict | None:
    """Run independent verification of a deal using a second Claude call.

    Fail-closed: any of the following drops the whole deal — exception, timeout,
    reply without verify_deal tool_use, stop_reason other than tool_use, a missing
    company/counterparty/deal_type/date field, a verdict that is not exactly
    "supported", an empty quote, or a quote that is not verbatim in the filing.

    Amounts are matched by ROLE (upfront / milestones_total / purchase_price),
    never by list index. A missing, unsupported, or non-verbatim amount entry
    drops that amount only. The published title and lines are then rebuilt from
    the amounts that remain.
    """
    if not VERIFIER_ENABLED:
        return deal

    title = deal.get('title', '')
    money = deal.get('money', '')
    structure = deal.get('structure', '')

    amounts_desc = []
    for amt in deal.get('verified_amounts', []):
        role = amt.get('kind', 'unknown')
        rendered = amt.get('rendered', '')
        amounts_desc.append(f"  - {role}: {rendered}")
    amounts_str = '\n'.join(amounts_desc) if amounts_desc else '  (no amounts)'

    relevant_text = _select_relevant_filing_sections(filing_text, max_chars=30000)
    type_quote = deal.get('type_quote') or ''
    if type_quote and normalize_whitespace(type_quote).lower() not in normalize_whitespace(relevant_text).lower():
        # The verifier must see the same evidence the extractor quoted.
        passage = _containing_passage(type_quote, filing_text) or type_quote
        relevant_text = f"{passage}\n\n{relevant_text}"

    prompt = f"""Verify each field of this deal is supported by the filing text.

FILING TEXT:
{relevant_text}

DEAL TO VERIFY:
- Company (filer): {deal.get('company', '')}
- Counterparty: {deal.get('counterparty', '')}
- Deal type: {deal.get('deal_type', '')}
- Date: {deal.get('date', '')}
- Chinese headline: {title}
- Money: {money}
- Structure: {structure}
- Amounts:
{amounts_str}

For EACH field, determine if it is SUPPORTED or UNSUPPORTED by explicit evidence in the filing.
Provide an EXACT VERBATIM quote from the filing text that proves your verdict.
Do not paraphrase or modify the quotes - they must be exact substrings of the filing text above."""

    try:
        model = os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-5-5")

        message = claude_client.messages.create(
            model=model,
            max_tokens=4000,
            tools=[DEAL_VERIFICATION_SCHEMA],
            tool_choice={"type": "auto"},
            messages=[{"role": "user", "content": prompt}],
        )

        stop_reason = getattr(message, 'stop_reason', None)
        if stop_reason != 'tool_use':
            logging.warning("Verifier stop_reason=%s (need tool_use), dropping deal", stop_reason)
            return None

        verification = None
        for block in message.content:
            if block.type == "tool_use" and block.name == "verify_deal":
                verification = block.input
                break

        if not verification or not isinstance(verification, dict):
            logging.warning("Verifier did not return structured response, dropping deal")
            return None

        for field_name in ('company', 'counterparty', 'deal_type', 'date'):
            if field_name not in verification:
                logging.info("Verifier: missing field %s, dropping deal", field_name)
                return None
            if not _verifier_field_supported(verification[field_name], filing_text):
                logging.info("Verifier: %s not supported with a verbatim quote, dropping deal", field_name)
                return None

        if 'amounts' not in verification or not isinstance(verification.get('amounts'), list):
            logging.info("Verifier: missing amounts list, dropping deal")
            return None

        original_amounts = [a for a in (deal.get('verified_amounts') or []) if isinstance(a, dict)]
        amounts_v = [a for a in verification['amounts'] if isinstance(a, dict)]
        verified_amounts = []
        for amt in original_amounts:
            role = amt.get('kind')
            amt_v = next((a for a in amounts_v if a.get('role') == role), None)
            if amt_v is None:
                logging.info("Verifier: no entry for amount role %s, dropping amount", role)
                continue
            if not _verifier_field_supported(amt_v, filing_text):
                logging.info("Verifier: amount role %s not supported with a verbatim quote, dropping amount", role)
                continue
            verified_amounts.append(amt)

        deal = rebuild_published_deal({**deal, 'verified_amounts': verified_amounts})
        logging.info("Verifier: deal passed with %d/%d amounts",
                     len(verified_amounts), len(original_amounts))
        return deal

    except Exception as e:
        logging.warning("Verifier error: %s: %s - dropping deal", type(e).__name__, e)
        return None


# =============================================================================
# DATA CLASSES
# =============================================================================

@dataclass
class ParsedAmount:
    """Parsed amount from a verified quote."""
    value: float
    scale: float  # millions
    currency: str
    up_to: bool
    kind: AmountKind
    raw_quote: str
    value_in_millions: float = field(init=False)
    
    def __post_init__(self):
        self.value_in_millions = self.value * self.scale


@dataclass
class VerifiedDeal:
    """A deal with all fields verified from filing text."""
    filer: str
    counterparty: str
    deal_type: DealType
    filer_role: str  # e.g., 'acquirer', 'licensor', 'borrower', 'payer'
    amounts: list[ParsedAmount]
    filing_url: str
    filing_date: str
    event_date: str | None


# =============================================================================
# CHINESE NUMERAL PARSER (Item 5)
# =============================================================================

# Chinese numeral values
CN_DIGITS = {'零': 0, '〇': 0, '一': 1, '二': 2, '三': 3, '四': 4, '五': 5,
             '六': 6, '七': 7, '八': 8, '九': 9, '两': 2}
CN_UNITS = {'十': 10, '百': 100, '千': 1000, '万': 10000, '亿': 100000000}


def chinese_to_int(cn_str: str) -> int | None:
    """Convert Chinese numeral string to integer.

    Correctly handles:
    - 一百二十 = 120 (not 10020)
    - 三十五 = 35 (not 305)
    - 两 = 2
    - 十二 = 12
    - 十二亿 = 1_200_000_000
    - 两千万 = 20_000_000
    - 一百零五 = 105

    Returns None if the string contains non-numeral characters (including 点).
    Use chinese_to_number() for decimals such as 一点五.
    """
    if not cn_str:
        return None

    if len(cn_str) == 1:
        if cn_str in CN_DIGITS:
            return CN_DIGITS[cn_str]
        if cn_str in CN_UNITS:
            return CN_UNITS[cn_str]
        return None

    result = 0
    current_section = 0
    current_num = 0

    for char in cn_str:
        if char in CN_DIGITS:
            current_num = CN_DIGITS[char]
        elif char in CN_UNITS:
            unit = CN_UNITS[char]
            if unit >= 10000:
                if current_num > 0:
                    current_section += current_num
                    current_num = 0
                if current_section == 0:
                    current_section = 1
                result += current_section * unit
                current_section = 0
            else:
                if current_num == 0:
                    current_num = 1
                current_section += current_num * unit
                current_num = 0
        else:
            return None

    if current_num > 0:
        current_section += current_num
    result += current_section
    return result


def chinese_to_number(cn_str: str) -> float | None:
    """Convert a Chinese numeral, including decimals with 点, to a number.

    一点五 = 1.5, 十二 = 12, 十二亿 = 1.2e9. Returns None on mis-parse
    rather than a silently wrong scale.
    """
    if not cn_str:
        return None
    if '点' not in cn_str:
        value = chinese_to_int(cn_str)
        return float(value) if value is not None else None
    left, _, right = cn_str.partition('点')
    if left:
        whole = chinese_to_int(left)
        if whole is None:
            return None
    else:
        whole = 0
    if not right:
        return float(whole)
    frac = 0.0
    place = 0.1
    for char in right:
        if char not in CN_DIGITS:
            return None
        frac += CN_DIGITS[char] * place
        place *= 0.1
    return whole + frac


def parse_chinese_decimal(text: str) -> set[str]:
    """Extract Chinese decimal amounts like 11.7 亿 or 4.125 亿.
    
    Returns set of string representations for verification.
    """
    numbers = set()
    
    # Pattern: Arabic decimal + 亿/万 + currency
    pattern = r'([\d,.]+)\s*(亿|万)\s*(?:美元|欧元|英镑|元|人民币|港币|日元)?'
    for m in re.finditer(pattern, text):
        num_str = m.group(1).replace(',', '')
        unit = m.group(2)
        try:
            num = float(num_str)
            # Store the EXACT decimal representation
            numbers.add(num_str)
            numbers.add(f"{num_str}{unit}")
            # Also store the full value for comparison
            if unit == '亿':
                full_value = num * 100_000_000
            else:  # 万
                full_value = num * 10_000
            numbers.add(str(full_value))
        except ValueError:
            pass
    
    return numbers


def verify_chinese_amount_match(source_text: str, chinese_text: str) -> bool:
    """Verify that Chinese amounts match source amounts EXACTLY.
    
    Rules:
    - $1.17 billion = 11.7 亿美元 (EXACT, not 11 or 12)
    - $200 million = 2 亿美元 (exact)
    - 4.125 亿 must match 4.125, not 4 or 41
    """
    # Extract amounts from source (English)
    source_amounts = set()
    
    # Billions
    for m in re.finditer(r'\$?([\d,.]+)\s*billion', source_text, re.IGNORECASE):
        num = float(m.group(1).replace(',', ''))
        # $X billion = X*10 亿
        yi_value = num * 10
        source_amounts.add(str(yi_value))
        source_amounts.add(f"{yi_value}亿")
    
    # Millions
    for m in re.finditer(r'\$?([\d,.]+)\s*million', source_text, re.IGNORECASE):
        num = float(m.group(1).replace(',', ''))
        # $X million = X/100 亿 (if >= 100M) or X/10000 万
        if num >= 100:
            yi_value = num / 100
            source_amounts.add(str(yi_value))
            source_amounts.add(f"{yi_value}亿")
        wan_value = num * 100  # X万 = X*100 万美元 for millions
        source_amounts.add(str(wan_value))
    
    # Extract Chinese amounts
    chinese_amounts = parse_chinese_decimal(chinese_text)
    
    # Check that all Chinese amounts are in source
    for cn_amt in chinese_amounts:
        if cn_amt not in source_amounts:
            # Check if it's a decimal that needs exact matching
            # e.g., 11.7 should match 11.7, not just 11
            return False
    
    return True


# =============================================================================
# QUOTE VERIFICATION
# =============================================================================

def normalize_whitespace(text: str) -> str:
    """Normalize whitespace and quotes for quote matching.
    
    Handles:
    - HTML entities (&#8220;, &#8221;, &quot;, etc.) → decoded characters
    - Multiple whitespace → single space
    - Fancy Unicode quotes (U+201C, U+201D, U+2018, U+2019) → ASCII quotes
    - En/em dashes (U+2013, U+2014) → regular dash
    """
    import html
    # First decode HTML entities (&#8220; → ", &amp; → &, etc.)
    text = html.unescape(text)
    
    # Normalize Unicode quotes to ASCII (using explicit Unicode escapes)
    text = text.replace('\u201c', '"').replace('\u201d', '"')  # fancy double quotes "" → "
    text = text.replace('\u2018', "'").replace('\u2019', "'")  # fancy single quotes '' → '
    text = text.replace('\u2013', '-').replace('\u2014', '-')  # en/em dashes
    
    # Normalize whitespace
    return re.sub(r'\s+', ' ', text).strip()


def verify_quote_in_filing(quote: str, filing_text: str) -> bool:
    """Verify quote is an exact whitespace-normalized substring of filing text.
    
    This is the core verification: every quote must be found in the source.
    """
    if not quote or not filing_text:
        return False
    
    norm_quote = normalize_whitespace(quote).lower()
    norm_filing = normalize_whitespace(filing_text).lower()
    
    return norm_quote in norm_filing


def find_quote_position(quote: str, filing_text: str) -> int | None:
    """Find the character position of a quote in the filing text."""
    if not quote or not filing_text:
        return None
    
    norm_quote = normalize_whitespace(quote).lower()
    norm_filing = normalize_whitespace(filing_text).lower()
    
    pos = norm_filing.find(norm_quote)
    return pos if pos >= 0 else None


_ABBREV_PERIOD_RE = re.compile(
    r'\b(?:Inc|Incorporated|Ltd|LLC|L\.L\.C|Corp|Corporation|Co|Limited|'
    r'plc|AG|GmbH|S\.r\.l|S\.A|SA|SAS|N\.V|B\.V|K\.K|LP|L\.P|Pte|Pty|'
    r'A/S|AB|NV|BV|KK|SE)\.',
    re.IGNORECASE,
)


def _split_sentences(text: str) -> list[str]:
    """Split sentences without treating Inc./Ltd./B.V./S.A. etc. as ends.

    Still split when an abbreviation period is a real sentence end
    ('Harbor Bio AG. The Company granted').
    """
    if not text:
        return []
    text = re.sub(
        r'\n(?=(?:Item\s+\d|Ex(?:hibit)?[\s._-]*\d|\d{1,2}\.\d+\s+))',
        '. ',
        text,
    )
    protected = re.sub(
        _ABBREV_PERIOD_RE.pattern + r'(?!\s+[A-Z])',
        lambda m: m.group(0)[:-1] + '\u0000',
        text,
        flags=re.IGNORECASE,
    )
    parts = re.split(
        r'(?<=[。！？])|(?<=(?<!\d)\.(?!\d))\s+|(?<=\d{4}\.)\s+',
        protected,
    )
    return [p.replace('\u0000', '.').strip() for p in parts if p.strip()]


def _containing_passage(quote: str, filing_text: str) -> str:
    """Return the sentence (fallback: paragraph) that contains a verified quote.

    Used to judge whether a grant/acquire quote sits inside an amendment,
    termination, historical, or divestiture sentence — the quote alone is
    not enough when the model excerpts only the operative verb.
    """
    if not quote or not filing_text:
        return ''
    paragraphs = split_into_paragraphs(filing_text)
    norm_quote = normalize_whitespace(quote).lower()
    host = ''
    for _, _, para in paragraphs:
        if norm_quote in normalize_whitespace(para).lower():
            host = para
            break
    if not host:
        if verify_quote_in_filing(quote, filing_text):
            return normalize_whitespace(filing_text)
        return ''
    for sent in _split_sentences(host):
        if norm_quote in normalize_whitespace(sent).lower():
            return sent.strip()
    return host.strip()


def _find_item_section(filing_text: str, position: int) -> str | None:
    """Find the Item section (e.g., 'Item 1.01') containing the given position.
    
    Returns the Item identifier (e.g., 'Item 1.01') or None if not in an Item section.
    """
    # Look backwards from position for the nearest Item header
    text_before = filing_text[:position]
    item_pattern = re.compile(r'Item\s+(\d+\.\d+)', re.IGNORECASE)
    
    # Find all Item matches before this position
    matches = list(item_pattern.finditer(text_before))
    if matches:
        return matches[-1].group().lower()
    return None


def _extract_item_section_text(filing_text: str, item_id: str) -> str | None:
    """Extract the full text of an Item section.
    
    Returns the text from the Item header to the next Item header (or end of file).
    """
    if not item_id:
        return None
    
    # Find the start of this Item section
    item_pattern = re.compile(re.escape(item_id), re.IGNORECASE)
    match = item_pattern.search(filing_text)
    if not match:
        return None
    
    start = match.start()
    
    # Find the next Item section (end boundary)
    next_item_pattern = re.compile(r'Item\s+\d+\.\d+', re.IGNORECASE)
    next_match = next_item_pattern.search(filing_text, start + len(item_id))
    
    if next_match:
        end = next_match.start()
    else:
        end = len(filing_text)
    
    return filing_text[start:end]


# =============================================================================
# PARAGRAPH / PASSAGE DETECTION
# =============================================================================

def split_into_paragraphs(text: str) -> list[tuple[int, int, str]]:
    """Split text into paragraphs by blank lines or <p> tags.
    
    Returns list of (start_pos, end_pos, paragraph_text) tuples.
    Paragraph boundaries: blank lines (2+ newlines) or <p> tags.
    """
    # Normalize the text first
    norm_text = text
    
    # Split on <p> tags or multiple newlines
    para_pattern = r'(?:</?p[^>]*>|\n\s*\n)'
    
    paragraphs = []
    last_end = 0
    
    for match in re.finditer(para_pattern, norm_text, re.IGNORECASE):
        if match.start() > last_end:
            para_text = norm_text[last_end:match.start()].strip()
            if para_text:
                paragraphs.append((last_end, match.start(), para_text))
        last_end = match.end()
    
    # Don't forget the last paragraph
    if last_end < len(norm_text):
        para_text = norm_text[last_end:].strip()
        if para_text:
            paragraphs.append((last_end, len(norm_text), para_text))
    
    return paragraphs


def quotes_in_same_paragraph(quote1: str, quote2: str, filing_text: str) -> bool:
    """Check if two quotes appear in the same paragraph.
    
    Paragraphs are split on blank lines or <p> tags, not character windows.
    """
    if not quote1 or not quote2 or not filing_text:
        return False
    
    norm_quote1 = normalize_whitespace(quote1).lower()
    norm_quote2 = normalize_whitespace(quote2).lower()
    
    paragraphs = split_into_paragraphs(filing_text)
    
    for _, _, para_text in paragraphs:
        norm_para = normalize_whitespace(para_text).lower()
        if norm_quote1 in norm_para and norm_quote2 in norm_para:
            return True
    
    return False


# =============================================================================
# COMPANY NAME MATCHING
# =============================================================================

def normalize_company_name(name: str) -> str:
    """Normalize company name by removing legal suffixes at end."""
    normalized = name.strip()
    for suffix_pattern in LEGAL_SUFFIXES:
        normalized = re.sub(suffix_pattern, '', normalized, flags=re.IGNORECASE).strip()
    return normalized


def match_company_whole_word(name: str, text: str) -> bool:
    """Match company name as whole word with alias support.
    
    Legal suffixes (Inc./Ltd./plc/AG/S.A.) are normalized at end only.
    No fuzzy matching except explicit alias table.
    """
    if not name or not text:
        return False
    
    # Normalize the name (remove legal suffixes)
    normalized_name = normalize_company_name(name).lower()
    text_lower = text.lower()
    
    # Try direct whole-word match
    pattern = rf'\b{re.escape(normalized_name)}\b'
    if re.search(pattern, text_lower):
        return True
    
    # Check explicit aliases only
    name_lower = name.lower().strip()
    for canonical, aliases in COMPANY_ALIASES.items():
        if name_lower == canonical or normalized_name == canonical:
            for alias in aliases:
                pattern = rf'\b{re.escape(alias)}\b'
                if re.search(pattern, text_lower):
                    return True
        # Also check if input matches any alias
        if name_lower in aliases or normalized_name in aliases:
            for alias in aliases:
                pattern = rf'\b{re.escape(alias)}\b'
                if re.search(pattern, text_lower):
                    return True
    
    return False


_ROLE_NOUNS = (
    'licensee', 'licensor', 'lenders', 'lender', 'purchaser', 'seller',
    'borrower', 'parties', 'investors', 'investor', 'agent',
)


def _companies_defined_as_role(role: str, filing_text: str) -> list[str]:
    """Return company names that the filing defines as a role noun.

    Handles straight and curly quotes and unquoted forms:
    - Calloway Therapeutics Ltd. (the Licensee)
    - Hercules Capital, Inc. (“Hercules”)
    - Partner Corp (the "Licensee")
    """
    if not role or not filing_text:
        return []
    return list(parse_defined_terms(filing_text).get(role.lower(), []))


def verify_counterparty_in_quotes(
    counterparty: str, 
    counterparty_quote: str, 
    type_quote: str,
    filing_text: str = ""
) -> bool:
    """Verify the claimed counterparty is the party named in the type quote.

    Direct name match in both quotes is enough. If the type quote uses a role
    noun (the Licensee, the Purchaser, …) instead of a name, that noun must
    resolve in the filing to the *claimed* counterparty — not to some other
    company that happens to have a parenthetical short name elsewhere.
    """
    if not counterparty:
        return False
    if not counterparty_quote:
        return False
    if not match_company_whole_word(counterparty, counterparty_quote):
        return False
    if type_quote and match_company_whole_word(counterparty, type_quote):
        return True
    if not type_quote or not filing_text:
        return False
    if verify_defined_term_in_type_quote(counterparty, type_quote, filing_text):
        return True

    type_lower = type_quote.lower()
    for role in _ROLE_NOUNS:
        if not re.search(rf'\b(?:the\s+)?["\u201c]?{re.escape(role)}["\u201d]?\b', type_lower):
            continue
        defined_as = _companies_defined_as_role(role, filing_text)
        for name in defined_as:
            if (match_company_whole_word(counterparty, name)
                    or match_company_whole_word(name, counterparty)):
                return True
        # A role noun is present but does not resolve to the claimed name.
        return False
    return False


# =============================================================================
# ROLE / DIRECTION DETECTION
# =============================================================================

# Fixed pattern set for role detection - order matters (more specific first)
# Note: "the Company" is commonly used in SEC filings to refer to the filer
ROLE_PATTERNS: list[tuple[str, str, str | None]] = [
    # Acquisition patterns
    (r'(the\s+Company|[\w\s&,\.]+?)\s+(?:will|to|has\s+agreed\s+to)\s+acquire\s+([\w\s&,\.]+)', 
     'acquirer', 'target'),
    (r'acquisition\s+of\s+([\w\s&,\.]+?)\s+by\s+([\w\s&,\.]+)',
     'target', 'acquirer'),
    (r'(the\s+Company|[\w\s&,\.]+?)\s+acquir(?:es|ed)\s+([\w\s&,\.]+)',
     'acquirer', 'target'),
    (r'merger\s+(?:of|between)\s+([\w\s&,\.]+?)\s+(?:and|with)\s+([\w\s&,\.]+)',
     'merger_party', 'merger_party'),
    
    # Investment patterns - filer as investor (BEFORE buyout to catch "paid...to purchase shares")
    # "the Company paid X to purchase shares" - filer is investor
    (r'(the\s+Company|[\w\s&,\.]+?)\s+paid\s+([\w\s&,\.]+?)\s+.*?to\s+purchase\s+(?:shares?|equity|stock)',
     'investor', 'investee'),
    (r'(the\s+Company|[\w\s&,\.]+?)\s+invest(?:ed|s)\s+in\s+([\w\s&,\.]+)',
     'investor', 'investee'),
    (r'(the\s+Company|[\w\s&,\.]+?)\s+purchas(?:ed|es)\s+.*?(?:shares?|equity|stock)\s+(?:of|from|in)\s+([\w\s&,\.]+)',
     'investor', 'investee'),
    
    # Buyout / payment patterns - "the Company paid X" (generic, after investment)
    (r'(the\s+Company|[\w\s&,\.]+?)\s+paid\s+([\w\s&,\.]+)',
     'payer', 'payee'),
    (r'(the\s+Company|[\w\s&,\.]+?)\s+(?:will\s+pay)\s+([\w\s&,\.]+)',
     'payer', 'payee'),
    (r'(the\s+Company|[\w\s&,\.]+?)\s+(?:terminated?|bought?\s+out|removed?)\s+(?:its\s+)?(?:milestone|royalty|payment)\s+obligation',
     'payer', None),
    (r'payment\s+(?:by|from)\s+([\w\s&,\.]+?)\s+to\s+([\w\s&,\.]+)',
     'payer', 'payee'),
    
    # Credit/loan patterns - BEFORE generic collaboration pattern
    # "entered into a Loan and Security Agreement with X" - filer is borrower, X is lender
    # The pattern captures (party entering = borrower, with X = lender)
    (r'entered\s+into\s+(?:a\s+)?(?:[\w\s]+\s+)?(?:Loan|Credit|Term\s+Loan)[\w\s]*(?:Agreement|Facility).*?with\s+([\w\s&,\.\"\(\)]+?)(?:\s*[\.\,]|\s*$)',
     'filer_is_borrower', 'lender'),
    (r'(?:Loan|Credit)\s+(?:and\s+Security\s+)?Agreement.*?with\s+([\w\s&,\.\"\(\)]+)',
     'filer_is_borrower', 'lender'),
    
    # License patterns - "the Company granted X a license" / "granting X rights"
    (r'(the\s+Company|[\w\s&,\.]+?)\s+(?:is\s+)?grant(?:s|ed|ing)\s+([\w\s&,\.\"\(\)]+?)\s+(?:an?\s+)?(?:exclusive[,\s]+)?(?:worldwide\s+)?(?:licen[sc]e|rights)',
     'licensor', 'licensee'),
    (r'licen[sc]e\s+(?:and\s+)?(?:collaboration\s+)?agreement\s+(?:by\s+and\s+)?between\s+([\w\s&,\.]+?)\s+and\s+([\w\s&,\.]+)',
     'license_party', 'license_party'),
    (r'(the\s+Company|[\w\s&,\.]+?)\s+licen[sc]ed\s+(?:rights?\s+)?to\s+([\w\s&,\.]+)',
     'licensor', 'licensee'),
    
    # Collaboration patterns - entered into Amendment/Agreement with X (generic, last)
    # For Sixth Amendment to... Agreement with X
    (r'entered\s+into\s+(?:a\s+)?(?:[\w\s]+\s+)?(?:Amendment|Agreement).*?(?:Agreement|Collaboration).*?with\s+([\w\s&,\.\"\(\)]+?)(?:\s*[\.\,]|\s*$)',
     'filer_is_licensor', 'licensee'),
    (r'(the\s+Company|[\w\s&,\.]+?)\s+entered\s+into\s+(?:a\s+)?(?:[\w\s]+\s+)?(?:Agreement|Amendment).*?with\s+([\w\s&,\.\"\(\)]+?)(?:\s*[\.\,]|\s*$)',
     'licensor', 'licensee'),
]


def _filer_noun_pattern(filer: str) -> str:
    """Regex for the filer as a grammatical party: 'the Company' or its name.

    Possessive 'the Company's X' is intentionally excluded — that is property
    of the filer, not the filer itself.
    """
    name = re.escape(normalize_company_name(filer))
    return rf'(?:the\s+Company|the\s+Registrant|{name})'


def _is_merger_or_purchase_quote(text: str) -> bool:
    """True when the quote is about a merger or purchase, not a license."""
    if not text:
        return False
    return bool(re.search(
        r'\b(?:agreement\s+and\s+plan\s+of\s+merger|plan\s+of\s+merger|'
        r'merger\s+agreement|purchase\s+agreement|'
        r'will\s+acquire|has\s+agreed\s+to\s+acquire|agreed\s+to\s+acquire|'
        r'merge\s+with\s+and\s+into|to\s+be\s+acquired)\b',
        text, re.IGNORECASE,
    ))


def detect_acquisition_direction(
    type_quote: str,
    filer: str,
    filing_text: str = '',
) -> str | None:
    """Buyer/target from explicit wording only. None if not explicit.

    'the Company' is the filer only when the filing does not define that
    term as someone else (e.g. the target in the buyer's 8-K).
    """
    if not type_quote or not filer:
        return None
    term_map = parse_defined_terms(f"{type_quote}\n{filing_text or ''}")
    company_is_filer = _the_company_is_filer(term_map, filer)
    if company_is_filer is None:
        return None
    noun = _filer_ref_pattern(filer, term_map)
    # the Company's X is never the filer
    not_possessive = rf'{noun}\b(?![\'\u2019]s)'

    target_patterns = [
        rf'(?:will|has\s+agreed\s+to|agreed\s+to|to)\s+acquire\s+'
        rf'(?:all\s+(?:of\s+)?(?:the\s+)?(?:outstanding\s+)?(?:shares?|stock|equity)\s+of\s+)?'
        rf'{not_possessive}',
        rf'merge(?:s|d)?\s+with\s+and\s+into\s+{not_possessive}',
        rf'{not_possessive}\s+will\s+be\s+acquired\b',
        rf'{not_possessive}\s+will\s+be\s+merged\b',
        rf'acquisition\s+of\s+{not_possessive}',
    ]
    buyer_patterns = [
        rf'{not_possessive}\s+(?:and\s+(?:its|their)\s+\w+\s+)?'
        rf'(?:will|has\s+agreed\s+to|agreed\s+to)\s+acquire\b',
        rf'{not_possessive}\s+acquir(?:es|ed|ing)\b',
        rf"{noun}['\u2019]s\s+(?:indirect\s+)?"
        rf'(?:wholly[-\s]owned\s+)?subsidiary\s+(?:will\s+)?'
        rf'(?:merge\s+(?:with\s+and\s+)?into|acquir)',
        rf'{not_possessive}\s+(?:will\s+)?merge\s+(?:with\s+and\s+)?into\b'
        rf'(?!\s+{noun})',
        rf'acquisition\s+of\s+.{{1,80}}?\s+by\s+{not_possessive}',
    ]

    is_target = any(re.search(p, type_quote, re.IGNORECASE) for p in target_patterns)
    is_buyer = any(re.search(p, type_quote, re.IGNORECASE) for p in buyer_patterns)
    if is_target and not is_buyer:
        return 'target'
    if is_buyer and not is_target:
        return 'acquirer'
    return None


def detect_role_from_quote(
    type_quote: str,
    filer: str,
    counterparty: str,
    deal_type: DealType | None = None,
    filing_text: str = '',
) -> dict | None:
    """Detect role/direction from type_quote using fixed pattern set.
    
    Returns dict with 'filer_role' and optionally 'direction' for payment flows.
    Returns None if role cannot be determined (deal should be dropped).
    
    "the Company" is the filer only when the filing does not define that
    term as another party. Acquisition/merger direction is explicit-wording
    only; generic 'entered into ... Agreement with X' license patterns never
    assign roles on a merger or purchase agreement.
    """
    if not type_quote:
        return None
    
    text = type_quote.strip()
    term_map = parse_defined_terms(f"{text}\n{filing_text or ''}")
    if _the_company_is_filer(term_map, filer) is None:
        return None
    if _defined_roles_conflict(term_map, filer):
        return None

    if deal_type in (DealType.ACQUISITION, DealType.MERGER) or _is_merger_or_purchase_quote(text):
        direction = detect_acquisition_direction(text, filer, filing_text)
        if direction:
            return {
                'filer_role': direction,
                'counterparty_role': 'acquirer' if direction == 'target' else 'target',
                'direction': None,
            }
        if deal_type in (DealType.ACQUISITION, DealType.MERGER) or _is_merger_or_purchase_quote(text):
            return None

    filer_normalized = normalize_company_name(filer).lower()
    counterparty_normalized = normalize_company_name(counterparty).lower() if counterparty else ""
    
    for pattern, role1, role2 in ROLE_PATTERNS:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            groups = match.groups()
            if len(groups) >= 1:
                party1_raw = groups[0].strip() if groups[0] else ""
                party2_raw = groups[1].strip() if len(groups) > 1 and groups[1] else None
                
                party1 = normalize_company_name(party1_raw).lower()
                party2 = normalize_company_name(party2_raw).lower() if party2_raw else None
                
                party1_is_filer = _phrase_is_filer(party1_raw, filer, term_map) or (
                    filer_normalized in party1 or
                    party1 in filer_normalized or
                    _check_alias_match(filer, party1)
                )
                
                party2_is_filer = party2 and (
                    _phrase_is_filer(party2_raw, filer, term_map) or
                    filer_normalized in party2 or
                    party2 in filer_normalized or
                    _check_alias_match(filer, party2)
                )
                
                # Also check if counterparty matches
                party1_is_counterparty = counterparty_normalized and (
                    counterparty_normalized in party1 or
                    party1 in counterparty_normalized or
                    _check_alias_match(counterparty, party1)
                )
                
                party2_is_counterparty = party2 and counterparty_normalized and (
                    counterparty_normalized in party2 or
                    party2 in counterparty_normalized or
                    _check_alias_match(counterparty, party2)
                )
                
                # Handle special cases where pattern implies filer's role
                if role1 == 'filer_is_borrower':
                    return {
                        'filer_role': 'borrower',
                        'counterparty_role': 'lender',
                        'direction': None
                    }
                if role1 == 'filer_is_licensor':
                    return {
                        'filer_role': 'licensor',
                        'counterparty_role': 'licensee',
                        'direction': None
                    }
                
                # Determine roles
                if party1_is_filer:
                    return {
                        'filer_role': role1,
                        'counterparty_role': role2,
                        'direction': _get_payment_direction(role1)
                    }
                elif party2_is_filer and role2:
                    return {
                        'filer_role': role2,
                        'counterparty_role': role1,
                        'direction': _get_payment_direction(role2)
                    }
                elif party1_is_counterparty and role2:
                    # Counterparty is party1, so filer must be party2
                    return {
                        'filer_role': role2,
                        'counterparty_role': role1,
                        'direction': _get_payment_direction(role2)
                    }
                elif party2_is_counterparty:
                    # Counterparty is party2, so filer must be party1
                    return {
                        'filer_role': role1,
                        'counterparty_role': role2,
                        'direction': _get_payment_direction(role1)
                    }
    
    return None


def _check_alias_match(company: str, text: str) -> bool:
    """Check if company matches text via alias table."""
    company_lower = normalize_company_name(company).lower()
    for canonical, aliases in COMPANY_ALIASES.items():
        if company_lower == canonical or company_lower in aliases:
            for alias in aliases:
                if alias in text.lower():
                    return True
    return False


def _get_payment_direction(role: str) -> str | None:
    """Get payment direction based on role."""
    if role in ('payer', 'licensee', 'borrower', 'acquirer', 'target'):
        return 'payer' if role == 'payer' else ('receiver' if role in ('licensor', 'lender', 'target') else None)
    return None


def validate_investment_direction(
    type_quote: str, 
    filer: str, 
    counterparty: str,
    filer_role: str
) -> tuple[bool, str | None]:
    """Validate investment direction for equity_financing deals (X1v2 rule).
    
    X获Y投资 ONLY emitted when quote grammar explicitly shows Y investing in X:
    - "[Y] (purchased|acquired|subscribed for) ... shares/stock of [X]"
    - "[X] (sold|issued) ... to [Y]"
    
    If filer would be the investor → drop.
    Any other phrasing → drop.
    
    Returns:
        (True, None) if valid "X获Y投资" direction
        (False, reason) if should be dropped
    """
    if not type_quote or not filer or not counterparty:
        return False, "missing type_quote, filer, or counterparty"
    
    text_lower = type_quote.lower()
    filer_lower = normalize_company_name(filer).lower()
    counterparty_lower = normalize_company_name(counterparty).lower()
    
    # Pattern 1: "[Y] (purchased|acquired|subscribed for) ... shares/stock of [X]"
    # Y is investor (counterparty), X is investee (filer)
    pattern1 = re.compile(
        r'([\w\s&,\.]+?)\s+(?:purchased?|acquired?|subscribed?\s+for)\s+.*?(?:shares?|stock|equity)\s+(?:of|from|in)\s+([\w\s&,\.]+)',
        re.IGNORECASE
    )
    match1 = pattern1.search(type_quote)
    if match1:
        investor_raw = match1.group(1).strip().lower()
        investee_raw = match1.group(2).strip().lower()
        
        investor_is_counterparty = (
            counterparty_lower in investor_raw or 
            investor_raw in counterparty_lower or
            'the company' not in investor_raw
        )
        investee_is_filer = (
            'the company' in investee_raw or
            filer_lower in investee_raw or
            investee_raw in filer_lower
        )
        
        if investor_is_counterparty and investee_is_filer:
            return True, None
        if 'the company' in investor_raw:
            return False, "filer is investor, not investee"
    
    # Pattern 2: "[X] (sold|issued) ... to [Y]"
    # X is investee (filer), Y is investor (counterparty)
    pattern2 = re.compile(
        r'(the\s+company|[\w\s&,\.]+?)\s+(?:sold|issued)\s+.*?(?:shares?|stock|equity).*?\bto\s+([\w\s&,\.]+)',
        re.IGNORECASE
    )
    match2 = pattern2.search(type_quote)
    if match2:
        issuer_raw = match2.group(1).strip().lower()
        buyer_raw = match2.group(2).strip().lower()
        
        issuer_is_filer = (
            'the company' in issuer_raw or
            filer_lower in issuer_raw or
            issuer_raw in filer_lower
        )
        buyer_is_counterparty = (
            counterparty_lower in buyer_raw or
            buyer_raw in counterparty_lower
        )
        
        if issuer_is_filer and buyer_is_counterparty:
            return True, None
    
    # If we get here, no valid pattern matched
    # Check if filer appears to be investor (should drop)
    if filer_role == 'investor':
        return False, "filer is investor, cannot emit X获Y投资"
    
    return False, "investment direction not clearly established by grammar"


# =============================================================================
# AMOUNT PARSING
# =============================================================================

# Conditional words - amounts with these are only for milestones_total/facility_size
CONDITIONAL_WORDS = frozenset(['may', 'could', 'potential', 'eligible', 'up to'])


def has_conditional_words(quote: str) -> bool:
    """Check if quote contains conditional/forward-looking words."""
    text_lower = quote.lower()
    
    patterns = [
        r'\bmay\s+receive\b',
        r'\bcould\s+receive\b',
        r'\bpotential\b',
        r'\beligible\s+to\s+receive\b',
        r'\bup\s+to\b',
    ]
    
    for pattern in patterns:
        if re.search(pattern, text_lower):
            return True
    
    return False


def parse_amount_from_quote(quote: str, kind: AmountKind) -> ParsedAmount | None:
    """Parse amount from verified quote.
    
    Returns ParsedAmount with value, scale (in millions), currency.
    Handles: $X.X million/billion, X million/billion dollars
    
    Rules:
    - Conditional words (may/could/potential/eligible/up to) only allowed for
      milestones_total/facility_size, rendered as '最高'.
    - Currency: US$/$/USD = USD. GBP/EUR parsed with € or £ symbol or word, CNY ignored.
    - Units must be explicit (million/billion) - bare $7,500,000 is raw dollars, not millions.
    - Sanity cap: amounts > $100 billion are dropped.
    - EUR/GBP require € or £ symbol or EUR/GBP/euro/pound word, not character class matching.
    - Analyst/media estimates are rejected - only contractual amounts allowed.
    """
    if not quote:
        return None
    
    text = quote.lower()
    
    # Reject analyst/media estimates - these are not contractual amounts
    # Be CAREFUL not to reject valid contractual language:
    # - "per share" is a contractual price per unit, not an estimate
    # - "potentially eligible to receive" is contractual milestone language
    # - "according to the terms" is contractual reference
    estimate_patterns = [
        # Analyst/media estimates
        r'\b(?:analyst|analysts|media|estimate[sd]?|estimated|valuation|valued at|worth|reportedly)\b',
        # Media sourcing language
        r'\bsources?\s+(?:say|said|report|familiar)\b',
        # Market/stock value (not deal price)
        r'\b(?:market\s+(?:cap|capitalization|value)|stock\s+(?:price|value))\b',
    ]
    for pattern in estimate_patterns:
        if re.search(pattern, text):
            logging.debug("Amount dropped: analyst/media estimate: %s", quote[:50])
            return None
    up_to = False
    
    # Check for 'up to' / 'maximum' conditional markers
    # 'aggregate' for milestones IS conditional (total possible if all achieved)
    # 'aggregate' for purchase_price is NOT conditional (definite total)
    if re.search(r'\bup\s+to\b', text):
        up_to = True
    elif re.search(r'\bmaximum\b', text):
        up_to = True
    elif re.search(r'\baggregate\b', text) and kind == AmountKind.MILESTONES_TOTAL:
        # Aggregate milestone payments are conditional (must achieve milestones)
        up_to = True
    
    # Check conditional words for non-milestones/facility
    if has_conditional_words(quote):
        if kind not in (AmountKind.MILESTONES_TOTAL, AmountKind.FACILITY_SIZE):
            logging.debug("Amount dropped: conditional words in non-milestone amount: %s", quote[:50])
            return None
        up_to = True
    
    # SANITY CAP: $100 billion = 100,000 million
    SANITY_CAP_MILLIONS = 100_000
    
    # Pattern 1: $X.X million/billion (most common - WITH explicit scale word)
    match = re.search(
        r'(?:US\$|\$|USD)\s*([\d,]+(?:\.\d+)?)\s*(million|billion|thousand)\b',
        text, re.IGNORECASE
    )
    
    if match:
        num_str = match.group(1).replace(',', '')
        scale_str = match.group(2).lower()
        
        try:
            value = float(num_str)
        except ValueError:
            return None
        
        scale_map = {
            'billion': 1000,
            'million': 1,
            'thousand': 0.001,
        }
        scale = scale_map.get(scale_str, 1)
        value_in_millions = value * scale
        
        # Sanity cap
        if value_in_millions > SANITY_CAP_MILLIONS:
            logging.debug("Amount dropped: exceeds $100B sanity cap: %s", quote[:50])
            return None
        
        return ParsedAmount(
            value=value,
            scale=scale,
            currency='USD',
            up_to=up_to,
            kind=kind,
            raw_quote=quote
        )
    
    # Pattern 2: $X,XXX,XXX (bare dollar amount without million/billion word)
    # This is raw dollars, NOT millions. E.g., $7,500,000 = 7.5 million
    match = re.search(
        r'(?:US\$|\$|USD)\s*([\d,]+(?:\.\d+)?)\b(?!\s*(?:million|billion|thousand|M|B|K)\b)',
        text, re.IGNORECASE
    )
    
    if match:
        num_str = match.group(1).replace(',', '')
        
        try:
            value = float(num_str)
        except ValueError:
            return None
        
        # Convert raw dollars to millions
        value_in_millions = value / 1_000_000
        
        # Sanity cap
        if value_in_millions > SANITY_CAP_MILLIONS:
            logging.debug("Amount dropped: exceeds $100B sanity cap: %s", quote[:50])
            return None
        
        # Only accept if it's a reasonable amount (at least $100,000)
        if value < 100_000:
            logging.debug("Amount dropped: bare dollar amount too small: %s", quote[:50])
            return None
        
        return ParsedAmount(
            value=value_in_millions,
            scale=1,  # Already converted to millions
            currency='USD',
            up_to=up_to,
            kind=kind,
            raw_quote=quote
        )
    
    # Pattern 3: X million/billion dollars (no $ sign, with scale word)
    match = re.search(
        r'([\d,]+(?:\.\d+)?)\s*(million|billion|thousand)\s*(?:U\.?S\.?\s*)?dollars?',
        text, re.IGNORECASE
    )
    
    if match:
        num_str = match.group(1).replace(',', '')
        scale_str = match.group(2).lower()
        
        try:
            value = float(num_str)
        except ValueError:
            return None
        
        scale_map = {'billion': 1000, 'million': 1, 'thousand': 0.001}
        scale = scale_map.get(scale_str, 1)
        value_in_millions = value * scale
        
        # Sanity cap
        if value_in_millions > SANITY_CAP_MILLIONS:
            logging.debug("Amount dropped: exceeds $100B sanity cap: %s", quote[:50])
            return None
        
        return ParsedAmount(
            value=value,
            scale=scale,
            currency='USD',
            up_to=up_to,
            kind=kind,
            raw_quote=quote
        )
    
    # Pattern 4: GBP - require £ symbol or explicit GBP/pound word (not [GBP] character class)
    # £X.X million/billion OR X million/billion pounds OR GBP X million
    match = re.search(
        r'(?:£|GBP\s*)\s*([\d,]+(?:\.\d+)?)\s*(million|billion)?\b',
        text, re.IGNORECASE
    )
    if not match:
        match = re.search(
            r'([\d,]+(?:\.\d+)?)\s*(million|billion)\s*(?:British\s+)?pounds?\b',
            text, re.IGNORECASE
        )
    
    if match:
        num_str = match.group(1).replace(',', '')
        scale_str = (match.group(2) or '').lower() if match.lastindex >= 2 else ''
        
        try:
            value = float(num_str)
        except ValueError:
            return None
        
        # Default to million only if explicitly stated or symbol present with million
        if scale_str:
            scale_map = {'billion': 1000, 'million': 1}
            scale = scale_map.get(scale_str, 1)
        else:
            # Bare £80 without million/billion - treat as raw GBP, convert
            scale = 1 / 1_000_000  # Convert raw pounds to millions
        
        value_in_millions = value * scale
        
        # Sanity cap
        if value_in_millions > SANITY_CAP_MILLIONS:
            logging.debug("Amount dropped: exceeds £100B sanity cap: %s", quote[:50])
            return None
        
        return ParsedAmount(
            value=value,
            scale=scale,
            currency='GBP',
            up_to=up_to,
            kind=kind,
            raw_quote=quote
        )
    
    # Pattern 5: EUR - require € symbol or explicit EUR/euro word (not [EUR] character class)
    # €X.X million/billion OR X million/billion euros OR EUR X million
    match = re.search(
        r'(?:€|EUR\s*)\s*([\d,]+(?:\.\d+)?)\s*(million|billion)?\b',
        text, re.IGNORECASE
    )
    if not match:
        match = re.search(
            r'([\d,]+(?:\.\d+)?)\s*(million|billion)\s*euros?\b',
            text, re.IGNORECASE
        )
    
    if match:
        num_str = match.group(1).replace(',', '')
        scale_str = (match.group(2) or '').lower() if match.lastindex >= 2 else ''
        
        try:
            value = float(num_str)
        except ValueError:
            return None
        
        # Default to million only if explicitly stated or symbol present with million
        if scale_str:
            scale_map = {'billion': 1000, 'million': 1}
            scale = scale_map.get(scale_str, 1)
        else:
            # Bare €120 without million/billion - treat as raw EUR, convert
            scale = 1 / 1_000_000  # Convert raw euros to millions
        
        value_in_millions = value * scale
        
        # Sanity cap
        if value_in_millions > SANITY_CAP_MILLIONS:
            logging.debug("Amount dropped: exceeds €100B sanity cap: %s", quote[:50])
            return None
        
        return ParsedAmount(
            value=value,
            scale=scale,
            currency='EUR',
            up_to=up_to,
            kind=kind,
            raw_quote=quote
        )
    
    # Pattern 6: Shares - X shares or X,XXX,XXX shares
    # Only for equity kind
    if kind == AmountKind.EQUITY:
        match = re.search(
            r'([\d,]+)\s*shares?\b',
            text, re.IGNORECASE
        )
        if match:
            num_str = match.group(1).replace(',', '')
            try:
                value = float(num_str)
                # For shares, we store the count directly (NOT scaled)
                return ParsedAmount(
                    value=value,
                    scale=1,  # raw count, NOT millions
                    currency='SHARES',
                    up_to=up_to,
                    kind=kind,
                    raw_quote=quote
                )
            except ValueError:
                pass
    
    return None


def render_amount_chinese(parsed: ParsedAmount) -> str:
    """Render parsed amount in Chinese format.
    
    Rules:
    - $35.0 million → 3,500 万美元
    - up to $1.5 billion → 最高 15 亿美元
    - $20.0 million → 2,000 万美元
    - 4,425,487 shares → 约 442.5 万股 (NOT 4.4 亿股)
    - '首付' only for kind=upfront whose quote contains 'upfront'
    
    Units:
    - 万 (wan) = 10,000
    - 亿 (yi) = 100,000,000 = 10,000 万
    """
    if not parsed:
        return ''
    
    # Handle shares separately
    # parsed.value is the raw share count (e.g., 4425487 for 4,425,487 shares)
    if parsed.currency == 'SHARES':
        shares = int(parsed.value)
        # Use 万 for shares >= 10,000: 4,425,487 → 约 442.5 万股
        # Use 亿 for shares >= 100,000,000: 150,000,000 → 约 1.5 亿股
        if shares >= 100_000_000:  # 1 亿 = 100 million shares
            yi = shares / 100_000_000
            return f"约 {yi:.1f} 亿股".replace('.0 ', ' ')
        elif shares >= 10_000:  # 1 万 = 10,000 shares
            wan = shares / 10_000
            if wan >= 100:
                return f"约 {wan:.1f} 万股".replace('.0 ', ' ')
            else:
                return f"约 {wan:,.1f} 万股".replace('.0 ', ' ')
        else:
            return f"{shares:,} 股"
    
    millions = parsed.value_in_millions
    
    # Currency suffix
    curr_suffix = {
        'USD': '美元',
        'EUR': '欧元',
        'GBP': '英镑',
        'CNY': '人民币',
    }.get(parsed.currency, '美元')
    
    # Prefix for 'up to'
    prefix = '最高 ' if parsed.up_to else ''
    
    # Render based on magnitude
    if millions >= 100:
        # Use 亿 (100 million)
        yi = millions / 100
        if yi == int(yi):
            amount_str = f"{int(yi)} 亿{curr_suffix}"
        else:
            amount_str = f"{yi:.1f} 亿{curr_suffix}".replace('.0 ', ' ')
    else:
        # Use 万 (10 thousand) = millions * 100
        wan = millions * 100
        if wan == int(wan):
            amount_str = f"{int(wan):,} 万{curr_suffix}"
        else:
            amount_str = f"{wan:,.0f} 万{curr_suffix}"
    
    return prefix + amount_str


# =============================================================================
# NONPROFIT / GOVERNMENT DETECTION
# =============================================================================

NONPROFIT_PATTERNS = [
    r'\bnon-?profit\b',
    r'\bnot-for-profit\b',
    r'\bfoundation\b',
    r'\bcharitable\b',
    r'\bgovernment\s+agenc(?:y|ies)\b',
    r'\bfederal\s+(?:agency|grant|funding)\b',
    r'\bstate\s+(?:agency|grant|funding)\b',
    r'\bnih\b',
    r'\bnational\s+institutes?\s+of\s+health\b',
    r'\bdarpa\b',
    r'\bdefense\s+advanced\s+research\b',
    r'\bconsortium\b',
    r'\bmulti-?party\s+commitment\b',
    r'\bjoint\s+funders?\b',
    r'\bmultiple\s+(?:funders?|investors?)\s+committing\s+jointly\b',
]


def detect_nonprofit_or_government(filing_text: str) -> bool:
    """Detect if filing mentions government agencies, foundations, nonprofits, or consortiums."""
    if not filing_text:
        return False
    
    text_lower = filing_text.lower()
    
    for pattern in NONPROFIT_PATTERNS:
        if re.search(pattern, text_lower):
            return True
    
    return False


# =============================================================================
# DEAL TYPE VALIDATION AND NON-DEAL FILTERING
# =============================================================================

# Non-deal agreement types that should be dropped
NON_DEAL_PATTERNS = [
    r'\bmaster\s+services?\s+agreement\b',
    r'\bservices?\s+agreement\b',
    r'\blease\s+agreement\b',
    r'\boffice\s+lease\b',
    r'\bemployment\s+agreement\b',
    r'\bseverance\s+agreement\b',
    r'\bsupply\s+agreement\b',
    r'\bmanufacturing\s+(?:and\s+)?supply\b',
    r'\bconsulting\s+agreement\b',
    r'\badvisory\s+agreement\b',
    r'\bconfidentiality\s+agreement\b',
    r'\bnon-?disclosure\s+agreement\b',
    r'\bsettlement\s+agreement\b',
    r'\bindemnification\s+agreement\b',
]

# Deal type keywords that must be present in type_quote to validate model's claim
DEAL_TYPE_VALIDATORS = {
    DealType.ACQUISITION: [
        r'\bacquir(?:e|es|ed|ing|ition)\b',
        r'\bpurchase\s+(?:of\s+)?(?:all|the)\s+(?:outstanding\s+)?(?:shares?|stock|equity)\b',
        r'\bmerger\b',
        r'\bbuy(?:s|ing)?\s+(?:all|the)\s+(?:outstanding\s+)?(?:shares?|equity)\b',
    ],
    DealType.MERGER: [
        r'\bmerger\b',
        r'\bmerge[sd]?\b',
        r'\bcombination\b',
    ],
    DealType.OBLIGATION_BUYOUT: [
        r'\bbuy-?out\b',
        r'\bterminat(?:e|ed|ion)\b.*\b(?:royalt|milestone|payment|obligation)\b',
        r'\bextinguish\b.*\b(?:royalt|milestone|payment|obligation)\b',
        r'\bpaid\b.*\bfor\s+(?:the\s+)?(?:one-?time\s+)?buy-?out\b',
        # Paid + removes obligation pattern (Immunome-BMS style)
        r'\bpaid\b.*\bremoves?\b.*\b(?:royalt|milestone|payment|obligation)\b',
        r'\bremoves?\s+(?:the\s+)?(?:Company\'?s?\s+)?(?:royalt|milestone|payment|obligation)\b',
        # Amendment removing/eliminating obligations
        r'\bamendment\b.*\b(?:remov|eliminat|terminat)\w*\b.*\b(?:royalt|milestone|payment|obligation)\b',
        # Amendment consideration (payment for amending agreement)
        r'\bamendment\s+consideration\b',
        # Paid + amendment (paying to amend/terminate obligations)
        r'\bpaid\b.*\bamendment\b',
        # "has no...obligations" after payment - indicates buyout completed
        r'\bno\s+(?:further\s+)?(?:milestone|royalt|payment|obligation)s?\b.*\bobligations?\b',
        r'\bhas\s+no\s+(?:milestone|royalt|payment)',
    ],
    DealType.DEBT_FACILITY: [
        r'\b(?:credit|loan|term\s+loan|revolving)\s+(?:facility|agreement)\b',
        r'\bdebt\s+(?:facility|financing|agreement)\b',
        r'\bventure\s+(?:debt|loan)\b',
        r'\bloan\s+and\s+security\s+agreement\b',
        # Patterns for specific lenders like Hercules
        r'\bentered\s+into\b.*\b(?:loan|credit)\b.*\bagreement\b',
        r'\b(?:tranche|draw(?:down)?|fund(?:ing|ed)?)\b.*\b(?:million|loan|facility)\b',
    ],
    DealType.EQUITY_FINANCING: [
        # True equity financing: investment/financing language
        r'\b(?:invest(?:ed|s|ment|ing)?)\s+(?:in|from)\b',
        r'\bseries\s+[a-z]\s+(?:financing|round|funding)\b',
        r'\b(?:equity|venture)\s+(?:financing|investment|round)\b',
        r'\b(?:private\s+placement|public\s+offering|ipo)\b',
        r'\bpurchas(?:ed?|es?|ing)\s+(?:shares?|stock|equity)\s+(?:of|from|in)\b',
    ],
}

# Patterns that REJECT equity_financing - shares issued as payment, not investment
EQUITY_FINANCING_REJECT_PATTERNS = [
    r'\b(?:consideration|payment)\b',
    r'\bissued\s+.*\bshares?\s+.*\b(?:to|as)\b',
    r'\bas\s+(?:partial\s+)?consideration\b',
    r'\bamendment\s+consideration\b',
]

# LICENSE GRANT patterns - explicit license/licence grant language
# NOTE: "license agreement" alone is NOT a grant pattern - it's just a document title.
# A document titled "License Agreement" may be an amendment that terminates obligations.
# The operative language "grants a license" determines license type, not the title.
LICENSE_GRANT_PATTERNS = [
    r'\bgrants?\s+.*\b(?:an?\s+)?(?:exclusive\s+)?licen[sc]e\b',
    r'\bgranted\s+.*\b(?:an?\s+)?exclusive\s+(?:worldwide\s+)?licen[sc]e\b',
    r'\bgrants?\s+.*\brights\b',
    r'\bgranted\s+.*\brights\b',
    r'\bgranting\s+.*\brights\b',
    r'\bexclusive\s+(?:worldwide\s+)?licen[sc]e\b',
    r'\blicen[sc]e\s+to\s+develop\b',
    r'\bgranting\s+.*\blicen[sc]e\b',
    r'\blicen[sc]ed?\s+(?:rights?|technology|ip|patents?)\b',
]

# BUYOUT/TERMINATION patterns - obligation termination language
BUYOUT_TERMINATION_PATTERNS = [
    r'\bin\s+full\s+satisfaction\b',
    r'\bterminat(?:e|ed?|ion|ing)\s+.*\b(?:royalt|milestone|payment|obligation)s?\b',
    r'\bbuy[\s-]?out\b',
    r'\brelease\s+of\s+.*\bobligations?\b',
    r'\bextinguish\b.*\bobligations?\b',
    r'\bhas\s+no\s+(?:further\s+)?(?:milestone|royalt|payment|obligation)s?\b',
    r'\bno\s+(?:further\s+)?(?:milestone|royalt|payment)\s+obligations?\b',
    r'\bremoves?\s+.*\b(?:royalt|milestone|payment|obligation)s?\b',
]


def disambiguate_license_vs_buyout(type_quote: str) -> tuple[DealType | None, str | None]:
    """Disambiguate between license and buyout based on OPERATIVE language.
    
    Returns:
        (DealType, None) if clear determination
        (None, reason) if ambiguous and should be dropped
    
    Rules:
    - Buyout: acquire/purchase all rights/assets, buy out, terminate in exchange for payment, assign
    - License: grants a license or sublicense (operative verb, not document title)
    - Buyout language wins over license language (payment to end obligations is buyout)
    - If both clearly operative, ambiguous
    
    The word "License Agreement" in a document TITLE does not force license type.
    An amendment to a License Agreement that ends obligations is a buyout.
    """
    if not type_quote:
        return None, "empty type_quote"
    
    text_lower = type_quote.lower()
    
    has_license_grant = any(re.search(p, text_lower) for p in LICENSE_GRANT_PATTERNS)
    has_buyout = any(re.search(p, text_lower) for p in BUYOUT_TERMINATION_PATTERNS)
    
    # Check for payment-for-amendment patterns - indicates buyout, not license
    # General pattern: any payment/issuance + consideration/exchange for + amendment/termination
    has_payment_for_amendment = bool(
        re.search(r'\b(?:paid|pay|pays|issued|issue|paying)\b', text_lower) and
        re.search(r'\b(?:consideration|exchange|settlement)\s+(?:for|of)\b', text_lower) and
        re.search(r'\b(?:amendment|terminat|removal|eliminat|release)\b', text_lower)
    )
    
    # Buyout language takes precedence - paying to end obligations is a buyout
    if has_buyout:
        if has_license_grant:
            # Both present - if there's clear buyout language, treat as buyout
            # (e.g., "terminates all royalty obligations" with "license agreement" in title)
            return DealType.OBLIGATION_BUYOUT, None
        return DealType.OBLIGATION_BUYOUT, None
    
    # Payment for amendment without license grant = buyout
    if has_payment_for_amendment and not has_license_grant:
        return DealType.OBLIGATION_BUYOUT, None
    
    if has_license_grant and not has_payment_for_amendment:
        return DealType.LICENSE_COLLABORATION, None
    
    if has_payment_for_amendment and has_license_grant:
        return None, "ambiguous: payment for amendment with license grant language"
    
    # Neither - could still be a valid deal of another type
    return None, None  # Return None,None to allow other type detection


def is_non_deal_agreement(type_quote: str) -> bool:
    """Check if the type_quote describes a non-deal agreement (services, lease, etc.)."""
    if not type_quote:
        return False
    
    text_lower = type_quote.lower()
    
    for pattern in NON_DEAL_PATTERNS:
        if re.search(pattern, text_lower):
            return True
    
    return False


def validate_deal_type_from_quote(
    claimed_type: DealType, 
    type_quote: str
) -> tuple[DealType | None, bool]:
    """Validate and potentially correct the claimed deal type based on type_quote content.
    
    Returns:
        - Tuple of (validated_deal_type, was_corrected)
        - (None, False) if the type cannot be validated and should be dropped
    
    Rules:
    - Acquisition requires explicit acquire/merger/purchase language with acquirer as subject
    - License vs buyout: disambiguate using explicit patterns (A6a/A6d rule)
    - Equity financing allowed only for investment-specific language
    - Debt facility requires credit/loan language
    - Obligation buyout requires explicit buyout/termination of obligations
    """
    if not type_quote:
        return None, False
    
    text_lower = type_quote.lower()
    
    # A6a/A6d: License vs buyout disambiguation - strict rule
    # If claim is license or buyout, use disambiguation
    if claimed_type in (DealType.LICENSE_COLLABORATION, DealType.OBLIGATION_BUYOUT):
        resolved_type, drop_reason = disambiguate_license_vs_buyout(type_quote)
        if drop_reason and "ambiguous" in drop_reason:
            logging.info("Deal dropped: %s", drop_reason)
            return None, False
        if resolved_type is not None:
            was_corrected = (resolved_type != claimed_type)
            if was_corrected:
                logging.info("Deal type corrected from %s to %s via disambiguation", 
                            claimed_type.value, resolved_type.value)
            return resolved_type, was_corrected
        # Neither license nor buyout detected - fall through to other validation
    
    # Check for validators if the claimed type has specific requirements
    if claimed_type in DEAL_TYPE_VALIDATORS:
        patterns = DEAL_TYPE_VALIDATORS[claimed_type]
        has_support = any(re.search(p, text_lower) for p in patterns)
        if not has_support:
            logging.info("Deal type '%s' not supported by type_quote", claimed_type.value)
            # Try to infer correct type - this is a correction
            inferred = infer_deal_type_from_quote(type_quote)
            return (inferred, True) if inferred else (None, False)
    
    # Additional validation for equity_financing - reject payment/consideration language
    if claimed_type == DealType.EQUITY_FINANCING:
        for pattern in EQUITY_FINANCING_REJECT_PATTERNS:
            if re.search(pattern, text_lower):
                logging.info("Deal type equity_financing rejected: payment/consideration language")
                inferred = infer_deal_type_from_quote(type_quote)
                return (inferred, True) if inferred else (None, False)
    
    # Additional validation for acquisitions - the acquirer must be the grammatical subject
    if claimed_type == DealType.ACQUISITION:
        # Check for patterns where someone other than the filer/counterparty is acquiring
        if re.search(r'\bpreviously\s+(?:entered|agreed|signed)\b', text_lower):
            logging.info("Deal dropped: type_quote describes historical agreement")
            return None, False
    
    return claimed_type, False


def infer_deal_type_from_quote(type_quote: str) -> DealType | None:
    """Infer deal type from type_quote content when model's claim doesn't match.
    
    Returns None if no deal type can be inferred (deal should be dropped).
    """
    if not type_quote:
        return None
    
    text_lower = type_quote.lower()
    
    # A6a/A6d: First try license vs buyout disambiguation
    resolved_type, drop_reason = disambiguate_license_vs_buyout(type_quote)
    if drop_reason and "ambiguous" in drop_reason:
        logging.info("Deal dropped in infer: %s", drop_reason)
        return None
    if resolved_type is not None:
        return resolved_type
    
    # Check each type's validators in order of specificity
    for deal_type, patterns in DEAL_TYPE_VALIDATORS.items():
        if any(re.search(p, text_lower) for p in patterns):
            # Skip license/buyout - already handled by disambiguation
            if deal_type in (DealType.LICENSE_COLLABORATION, DealType.OBLIGATION_BUYOUT):
                continue
            return deal_type
    
    # Equity financing
    if re.search(r'\b(?:financ|invest|series\s+[a-z]|equity\s+(?:investment|financing))\b', text_lower):
        return DealType.EQUITY_FINANCING
    
    # Generic agreement without deal-specific language - drop
    return None


_MONTH_NAME_TO_NUM = {
    'january': 1, 'february': 2, 'march': 3, 'april': 4, 'may': 5, 'june': 6,
    'july': 7, 'august': 8, 'september': 9, 'october': 10, 'november': 11,
    'december': 12,
}

_OLD_EVENT_CUTOFF = timedelta(days=365)


def _parse_loose_date(value: str | None) -> date | None:
    """Parse YYYY-MM-DD, YYYY-MM, or YYYY."""
    if not value:
        return None
    m = re.match(r'(\d{4})(?:-(\d{1,2})(?:-(\d{1,2}))?)?', value.strip())
    if not m:
        return None
    try:
        return date(int(m.group(1)), int(m.group(2) or 1), int(m.group(3) or 1))
    except ValueError:
        return None


def _dated_clause_date(text: str) -> date | None:
    """Extract 'dated [as of] Month D, YYYY' from a quote."""
    if not text:
        return None
    m = re.search(
        r'\bdated(?:\s+as\s+of)?\s+'
        r'(january|february|march|april|may|june|july|august|september|october|november|december)'
        r'\s+(\d{1,2}),\s+(\d{4})\b',
        text, re.IGNORECASE,
    )
    if not m:
        return None
    try:
        return date(int(m.group(3)), _MONTH_NAME_TO_NUM[m.group(1).lower()], int(m.group(2)))
    except (ValueError, KeyError):
        return None


_MONTH_ALT = (
    r'(january|february|march|april|may|june|july|august|'
    r'september|october|november|december)'
)


def _parse_month_day_year(month: str, day: str, year: str) -> date | None:
    try:
        return date(int(year), _MONTH_NAME_TO_NUM[month.lower()], int(day))
    except (ValueError, KeyError):
        return None


def _line_at(text: str, pos: int) -> str:
    start = text.rfind('\n', 0, pos) + 1
    end = text.find('\n', pos)
    return text[start:] if end == -1 else text[start:end]


def _is_exhibit_index_line(line: str) -> bool:
    s = line.strip()
    if not s:
        return False
    return bool(re.match(
        r'(?i)(?:ex(?:hibit)?[\s._-]*\d|item\s+(?:6\.|9\.01)|\d{1,2}\.\d+\s+)',
        s,
    )) or bool(re.search(r'(?i)\b(?:exhibit\s+(?:index|no\.?\s*\d)|list\s+of\s+exhibits)\b', s))


_INSTRUMENT_TAIL = (
    r'Agreement|Addendum|Indenture|Amendment|Joinder|Novation|'
    r'Supplement|Modification|Extension|Letter'
)
_TITLE_WORDS = (
    r'(?:[Tt]he\s+)?[A-Z][A-Za-z0-9&]+'
    r'(?:\s+(?:and|&|of|the|to|[A-Z][A-Za-z0-9&]+)){0,10}'
)
_INSTRUMENT_TITLE_RE = re.compile(
    rf'\b(({_TITLE_WORDS}\s+(?:{_INSTRUMENT_TAIL}))|Addendum)\b'
)
_DATED_AS_OF_RE = re.compile(
    rf'((?:the\s+)?(?:[A-Za-z][A-Za-z0-9&]+(?:\s+(?:and|&|of|the|to|[A-Za-z][A-Za-z0-9&]+)){{0,10}})\s+'
    rf'(?:agreement|addendum|indenture|amendment|joinder|novation|supplement|'
    rf'modification|extension|letter)|addendum)'
    rf'\s*,?\s+dated(?:\s+as\s+of)?\s+{_MONTH_ALT}\s+(\d{{1,2}}),\s+(\d{{4}})\b',
    re.IGNORECASE,
)
_DATED_NEAR_TITLE_RE = re.compile(
    rf'dated(?:\s+as\s+of)?\s+{_MONTH_ALT}\s+(\d{{1,2}}),\s+(\d{{4}})\b',
    re.IGNORECASE,
)
_ENTERED_IN_MONTH_YEAR_RE = re.compile(
    rf'entered\s+into\s+in\s+{_MONTH_ALT}\s+(\d{{4}})\b',
    re.IGNORECASE,
)
_YEAR_NAMED_INSTRUMENT_RE = re.compile(
    r'\b(?:the\s+)?((?:19|20)\d{2})\s+'
    r'((?:License|Licence|Collaboration|Merger|Purchase|Supply|Research)'
    r'(?:\s+Agreement)?)',
    re.IGNORECASE,
)
_ON_CALENDAR_DATE_RE = re.compile(
    rf'\bon\s+{_MONTH_ALT}\s+(\d{{1,2}}),\s+(\d{{4}})\b',
    re.IGNORECASE,
)


def _norm_agreement_key(label: str) -> str:
    return re.sub(r'^the\s+', '', re.sub(r'\s+', ' ', (label or '').lower())).strip()


def _alias_instrument_keys(a: str, b: str, aliases: dict[str, set[str]]) -> None:
    ka, kb = _norm_agreement_key(a), _norm_agreement_key(b)
    if not ka or not kb or ka == kb:
        return
    aliases.setdefault(ka, set()).add(kb)
    aliases.setdefault(kb, set()).add(ka)


def _add_instrument_date(
    dates: dict[str, list[date]], key: str, parsed: date | None,
) -> None:
    k = _norm_agreement_key(key)
    if not k or k == 'agreement' or parsed is None:
        return
    dates.setdefault(k, [])
    if parsed not in dates[k]:
        dates[k].append(parsed)


def _collect_instrument_catalog(
    filing_text: str,
) -> tuple[dict[str, set[str]], dict[str, list[date]]]:
    """Map each instrument title/short name to aliases and dated clauses.

    Dates bind only to the title in the same clause and to that title's
    defined short name. A merger title never inherits a license date.
    """
    aliases: dict[str, set[str]] = {}
    dates: dict[str, list[date]] = {}
    if not filing_text:
        return aliases, dates

    skip_shorts = {
        'company', 'the company', 'parent', 'purchaser', 'registrant',
        'licensee', 'licensor', 'merger sub',
    }

    # Any-casing "Title, dated as of <date>" — with or without a following short name.
    # Bind the date only to that title and its own defined short name.
    for m in _DATED_AS_OF_RE.finditer(filing_text):
        parsed = _parse_month_day_year(m.group(2), m.group(3), m.group(4))
        title = m.group(1)
        _add_instrument_date(dates, title, parsed)
        after = filing_text[m.end():m.end() + 80]
        shorts = _quoted_terms_in(after)
        if shorts and _norm_agreement_key(shorts[0]) not in skip_shorts:
            _add_instrument_date(dates, shorts[0], parsed)
            _alias_instrument_keys(title, shorts[0], aliases)

    for m in _DATED_NEAR_TITLE_RE.finditer(filing_text):
        parsed = _parse_month_day_year(m.group(1), m.group(2), m.group(3))
        sent = _sentence_at(filing_text, m.start())
        rel = sent.lower().rfind('dated')
        before = sent[:rel] if rel >= 0 else sent
        titled = list(_INSTRUMENT_TITLE_RE.finditer(before))
        if not titled:
            continue
        title = titled[-1].group(1)
        _add_instrument_date(dates, title, parsed)
        after = filing_text[m.end():m.end() + 80]
        shorts = _quoted_terms_in(after)
        if shorts and _norm_agreement_key(shorts[0]) not in skip_shorts:
            _add_instrument_date(dates, shorts[0], parsed)
            _alias_instrument_keys(title, shorts[0], aliases)

    for start, _end, short in _quoted_term_spans(filing_text):
        if _norm_agreement_key(short) in skip_shorts:
            continue
        before = filing_text[max(0, start - 220):start]
        titled = list(_INSTRUMENT_TITLE_RE.finditer(before))
        if not titled:
            continue
        title_m = titled[-1]
        title = title_m.group(1)
        gap = before[title_m.end():]
        instrument_short = bool(re.search(
            r'(?i)\b(?:agreement|addendum|indenture|joinder|novation|'
            r'letter|amendment|supplement|pact|license|licence)\b',
            short,
        ))
        dm = _DATED_NEAR_TITLE_RE.search(gap)
        if not instrument_short and not dm and not re.match(r'^[\s,]*$', gap):
            continue
        _alias_instrument_keys(title, short, aliases)
        if dm:
            parsed = _parse_month_day_year(dm.group(1), dm.group(2), dm.group(3))
            _add_instrument_date(dates, title, parsed)
            _add_instrument_date(dates, short, parsed)

    for m in _DATED_NEAR_TITLE_RE.finditer(filing_text):
        parsed = _parse_month_day_year(m.group(1), m.group(2), m.group(3))
        line = _line_at(filing_text, m.start())
        titles = list(_INSTRUMENT_TITLE_RE.finditer(line))
        if not titles:
            continue
        # Same line only — do not let a merger inherit a nearby license date.
        nearest = min(titles, key=lambda t: abs(t.start() - (m.start() - (filing_text.rfind('\n', 0, m.start()) + 1))))
        _add_instrument_date(dates, nearest.group(1), parsed)

    for m in _ENTERED_IN_MONTH_YEAR_RE.finditer(filing_text):
        try:
            parsed = date(int(m.group(2)), _MONTH_NAME_TO_NUM[m.group(1).lower()], 1)
        except (ValueError, KeyError):
            continue
        sent = _sentence_at(filing_text, m.start())
        for t in _INSTRUMENT_TITLE_RE.finditer(sent):
            _add_instrument_date(dates, t.group(1), parsed)

    for m in _YEAR_NAMED_INSTRUMENT_RE.finditer(filing_text):
        try:
            parsed = date(int(m.group(1)), 1, 1)
        except ValueError:
            continue
        label = f"{m.group(1)} {m.group(2)}"
        _add_instrument_date(dates, label, parsed)

    return aliases, dates


def _keys_for_instrument(
    label: str,
    aliases: dict[str, set[str]],
    dates: dict[str, list] | None = None,
) -> set[str]:
    key = _norm_agreement_key(label)
    if not key:
        return set()
    out = {key}
    out.update(aliases.get(key, ()))
    # Exact key or an explicit short-name alias only — never a suffix match
    # that would let a merger inherit a license date.
    if dates and key in dates:
        out.add(key)
    return {k for k in out if k and k != 'agreement'}


def _agreement_date_from_filing(type_quote: str, filing_text: str) -> date | None:
    """Dated clause for the agreement the grant quote actually refers to.

    Resolves the referred title and any defined short name to the same date.
    An unrelated indenture or a differently labeled exhibit does not count.
    A date that appears only on the exhibit index for this same instrument does.
    """
    if not type_quote or not filing_text:
        return None
    labels = _referred_agreement_labels(type_quote, filing_text)
    if not labels:
        return None
    aliases, dates = _collect_instrument_catalog(filing_text)
    found: list[date] = []
    seen: set[str] = set()
    for lab in labels:
        for key in _keys_for_instrument(lab, aliases, dates):
            if key in seen:
                continue
            seen.add(key)
            found.extend(dates.get(key, ()))
    return min(found) if found else None


def is_historical_agreement(
    type_quote: str,
    reference_date: str | None = None,
    filing_text: str = '',
) -> bool:
    """True when the quote describes a prior agreement, not a newly entered one.

    Dated-year cutoffs are relative to the filing/event date (not hard-coded
    2020-2025). An agreement dated more than a year before the reference is
    historical. Markers also include 'previously entered', 'had granted'.
    When a grant hangs off a defined agreement, also use that agreement's
    'dated <date>' anywhere in the filing.
    """
    if not type_quote:
        return False

    text_lower = type_quote.lower()

    historical_patterns = [
        r'\bpreviously\s+(?:entered|agreed|executed|signed|disclosed|announced)\b',
        r'\boriginal\s+agreement\b',
        r'\bas\s+amended\s+(?:and\s+restated\s+)?(?:from\s+time\s+to\s+time\s+)?(?:through|prior\s+to)\b',
        r'\bhad\s+granted\b',
        r'\bhad\s+entered\b',
    ]
    if any(re.search(p, text_lower) for p in historical_patterns):
        return True

    dated = _dated_clause_date(type_quote)
    if dated is None and filing_text:
        dated = _dated_clause_date(_containing_passage(type_quote, filing_text))
    if dated is None and filing_text:
        dated = _agreement_date_from_filing(type_quote, filing_text)
    if dated is None:
        return False
    ref = _parse_loose_date(reference_date) or date.today()
    return dated < ref - _OLD_EVENT_CUTOFF


def _new_agreements_are_only_amendments_or_settlements(filing_text: str) -> bool:
    """True when every 'entered into … Agreement' is an amendment or settlement."""
    if not filing_text:
        return False
    hits = re.findall(
        r'entered\s+into\s+(?:an?\s+|the\s+)?([^.;]{0,100}?agreement)',
        filing_text, re.IGNORECASE,
    )
    titles = [re.sub(r'\s+', ' ', h).strip() for h in hits if h.strip()]
    if not titles:
        return False
    return all(_title_is_amendment_or_settlement(t) for t in titles)


def is_amendment_language(text: str) -> bool:
    """True if the text is about amending an existing agreement.

    An amendment is never a new deal: A&R / Amended & Restated, amend(s) the
    Agreement, agreed to amend, restated, supplement to, as expanded/extended.
    """
    if not text:
        return False
    t = text.lower()
    return bool(re.search(
        r'(?:'
        r'\bamendments?\b'
        r'|\bamending\b'
        r'|\bamend(?:s|ed)\b'
        r'|\b(?:first|second|third|fourth|fifth|sixth|seventh|eighth|ninth|tenth)\s+amendment'
        r'|\bamended\s*(?:and|&)\s*restated'
        r'|\ba\s*&\s*r\b'
        r'|\ba&r\b'
        r'|\bas\s+(?:amended|expanded|extended)\b'
        r'|\bagreed\s+to\s+amend'
        r'|\baddendum\b'
        r'|\bexpans(?:ion|ions)\b'
        r'|\bexpand(?:s|ed|ing)\b'
        r'|\bmodif(?:y|ies|ied|ying|ication|ications)\b'
        r'|\bextend(?:s|ed|ing)\b'
        r'|\bextensions?\b'
        r'|\bsupplement(?:al|s|ed|ing)?\b'
        r'|\brestated\b'
        r'|\bside\s+letters?\b'
        r'|\bjoinders?\b'
        r'|\bnovations?\b'
        r'|\bassignment\s+and\s+assumption\b'
        r'|\bconsent\s+to\s+assignment\b'
        r')',
        t,
    ))


def is_wrapper_or_existing_rights_language(text: str) -> bool:
    """Wrappers and payments/exercises under an existing instrument are not new deals."""
    if not text:
        return False
    t = text.lower()
    if is_amendment_language(t):
        return True
    return bool(re.search(
        r'(?:'
        r'\b(?:exercis(?:e|ed|es|ing)\s+(?:an?\s+)?option|option\s+exercise)\b'
        r'.{0,100}\b(?:under|pursuant\s+to|in\s+accordance\s+with)\b'
        r'|\b(?:milestone|royalt\w+)\s+payments?\b'
        r'.{0,100}\b(?:under|pursuant\s+to|in\s+accordance\s+with)\b'
        r'|\bpayments?\s+(?:of\s+)?(?:milestone|royalt\w+)\b'
        r'.{0,100}\b(?:under|pursuant\s+to|in\s+accordance\s+with)\b'
        r')',
        t,
    ))


def _title_is_amendment_or_settlement(title: str) -> bool:
    if not title:
        return False
    return is_amendment_language(title) or bool(re.search(
        r'\b(?:amendment|settlement|supplement)\b', title, re.IGNORECASE,
    ))


_AGREEMENT_LABEL_RE = re.compile(
    rf'\b((?:{_TITLE_WORDS}\s+(?:{_INSTRUMENT_TAIL}))|Addendum)\b',
)
_INTRO_AGREEMENT_RE = re.compile(
    r'(?:entered\s+into|executed|signed)\s+(?:an?\s+|the\s+)?'
    r'(.{0,160}?(?:agreement|addendum|amendment|supplement|modification|'
    r'extension|joinder|novation|letter))\b',
    re.IGNORECASE,
)
_UNDER_INSTRUMENT_RE = re.compile(
    r'(?i:\b(?:under|pursuant\s+to|in\s+accordance\s+with))\s+(?i:the\s+)?'
    r'(?:["\u201c]([^"\u201d]{1,80})["\u201d]|'
    r'([A-Z][A-Za-z0-9&]*(?:\s+(?:and|&|of|the|to|[A-Z][A-Za-z0-9&]+)){0,8}))'
    r'|(?i:\bin\s+the\s+)'
    r'(?:["\u201c]([^"\u201d]{1,80})["\u201d]|'
    r'([A-Z][A-Za-z0-9&]*(?:\s+(?:and|&|of|the|to|[A-Z][A-Za-z0-9&]+)){0,8}))',
)


def _referred_agreement_labels(type_quote: str, filing_text: str = '') -> list[str]:
    """The instrument the grant hangs off — not every title mentioned nearby.

    Wrapper and historical words apply only to this source agreement. A newly
    signed license that mentions an older pact, milestones, or a joinder in
    passing does not inherit those other titles.
    """
    labels: list[str] = []
    aliases, dates = _collect_instrument_catalog(filing_text) if filing_text else ({}, {})
    term_map = parse_defined_terms(filing_text) if filing_text else {}

    def _add_under_labels(src: str) -> None:
        for m in _UNDER_INSTRUMENT_RE.finditer(src):
            for g in m.groups():
                if not g:
                    continue
                key = _norm_agreement_key(g)
                if key in {'company', 'parent', 'registrant', 'purchaser'}:
                    continue
                if key in aliases or key in dates or _INSTRUMENT_TITLE_RE.search(g):
                    labels.append(g)

    if type_quote:
        labels.extend(m.group(1) for m in _AGREEMENT_LABEL_RE.finditer(type_quote))
        _add_under_labels(type_quote)
        for term in list(term_map) + list(aliases):
            if (
                'agreement' in term
                or re.search(
                    r'\b(?:addendum|amendment|supplement|letter|joinder|'
                    r'novation|expansion|modification|extension|pact|'
                    r'licen[sc]e)\b',
                    term,
                )
            ) and re.search(rf'\b{re.escape(term)}\b', type_quote, re.IGNORECASE):
                labels.append(term)
    if filing_text and type_quote:
        passage = _containing_passage(type_quote, filing_text)
        if passage:
            _add_under_labels(passage)
            for m in _YEAR_NAMED_INSTRUMENT_RE.finditer(passage):
                labels.append(f"{m.group(1)} {m.group(2)}")
    if type_quote:
        for m in _YEAR_NAMED_INSTRUMENT_RE.finditer(type_quote):
            labels.append(f"{m.group(1)} {m.group(2)}")
    seen: set[str] = set()
    out: list[str] = []
    for lab in labels:
        lab = re.sub(
            r'(?i)^(pursuant\s+to|under|in\s+accordance\s+with|in)\s+(?:the\s+)?',
            '',
            lab,
        ).strip()
        key = _norm_agreement_key(lab)
        if not key or key in seen or key == 'agreement':
            continue
        seen.add(key)
        out.append(lab)
    return out


def _title_is_referred_label(title: str, keys: set[str]) -> bool:
    """True when this entered-into title *is* the referred agreement, not another."""
    t = _norm_agreement_key(title)
    if not t or t in {'agreement', 'the agreement'}:
        return False
    if t in keys:
        return True
    first = re.match(
        r'((?:[a-z0-9&]+\s+){0,6}(?:agreement|addendum|amendment|supplement|'
        r'modification|extension))',
        t,
    )
    return bool(first and first.group(1).strip() in keys)


def _sentence_at(text: str, pos: int) -> str:
    """The split sentence covering pos, else the physical line."""
    if not text or pos < 0 or pos > len(text):
        return _line_at(text, pos) if text else ''
    cursor = 0
    for sent in _split_sentences(text):
        idx = text.find(sent, cursor)
        if idx == -1:
            idx = text.find(sent)
        if idx == -1:
            continue
        end = idx + len(sent)
        if idx <= pos < end:
            return sent
        cursor = end
    return _line_at(text, pos)


def _quote_position(quote: str, filing_text: str) -> int:
    if not quote or not filing_text:
        return -1
    pos = find_quote_position(quote, filing_text)
    if pos is not None:
        return pos
    idx = filing_text.lower().find(normalize_whitespace(quote).lower())
    return idx


def _instrument_intro_sentences(
    type_quote: str, filing_text: str,
) -> list[str]:
    """Sentences earlier in the filing that introduce or define a referred instrument.

    Used when a grant says under / pursuant to / in accordance with / in the X.
    """
    if not type_quote or not filing_text:
        return []
    labels = _referred_agreement_labels(type_quote, filing_text)
    if not labels:
        return []
    aliases, dates = _collect_instrument_catalog(filing_text)
    keys: set[str] = set()
    for lab in labels:
        keys.update(_keys_for_instrument(lab, aliases, dates))
    keys.discard('agreement')
    keys.discard('')
    if not keys:
        return []
    quote_pos = _quote_position(type_quote, filing_text)
    if quote_pos < 0:
        quote_pos = len(filing_text)
    intros: list[str] = []
    seen: set[str] = set()

    def _keep(pos: int) -> None:
        if pos < 0 or pos >= quote_pos:
            return
        sent = _sentence_at(filing_text, pos)
        key = normalize_whitespace(sent).lower()
        if not sent or key in seen:
            return
        seen.add(key)
        intros.append(sent)

    for m in re.finditer(
        rf'{_DEFINED_TERM_QUOTES}([^"\u201c\u201d\u2018\u2019]+){_DEFINED_TERM_QUOTES}',
        filing_text,
    ):
        if _norm_agreement_key(m.group(1)) in keys:
            _keep(m.start())
    for m in _INTRO_AGREEMENT_RE.finditer(filing_text):
        if _title_is_referred_label(m.group(1), keys):
            _keep(m.start())
    for m in _DATED_AS_OF_RE.finditer(filing_text):
        title = _norm_agreement_key(m.group(1))
        after = filing_text[m.end():m.end() + 80]
        shorts = _quoted_terms_in(after)
        short = _norm_agreement_key(shorts[0]) if shorts else ''
        if title in keys or short in keys:
            _keep(m.start())
    return intros


def _grant_refers_to_amended_agreement(type_quote: str, filing_text: str) -> bool:
    """True when the grant's source agreement is itself a wrapper or amendment.

    Wrapper words on a newly signed license that merely mentions an older
    agreement, milestones, or a joinder do not count.
    """
    if not type_quote or not filing_text:
        return False
    labels = list(_referred_agreement_labels(type_quote, filing_text))
    keys = {_norm_agreement_key(lab) for lab in labels}
    keys.discard('agreement')
    keys.discard('the agreement')
    keys.discard('')
    if not keys:
        return False
    if any(is_wrapper_or_existing_rights_language(lab) for lab in labels):
        return True
    for m in _INTRO_AGREEMENT_RE.finditer(filing_text):
        if not _title_is_referred_label(m.group(1), keys):
            continue
        if is_wrapper_or_existing_rights_language(m.group(0)) or is_amendment_language(m.group(0)):
            return True
    return False


def is_termination_or_assignment_language(text: str) -> bool:
    """True if the text is a termination, assignment, or similar non-new deal."""
    if not text:
        return False
    t = text.lower()
    return bool(re.search(
        r'\b(?:terminat(?:e|ed|es|ing|ion)'
        r'|assign(?:s|ed|ment)'
        r'|waiv(?:e|ed|er|es|ing)'
        r'|item\s+1\.02)\b',
        t,
    ))


def is_divestiture_or_asset_sale(text: str) -> bool:
    """True if the text is a sale of the filer's subsidiary, business or assets.

    'The Company's X' is never the filer itself. Acquiring the Company's
    subsidiary, business, assets, or 'all outstanding shares of the Company's
    subsidiary' is a divestiture (out of scope).

    The Company's subsidiary merging *into* a third party (filer as buyer)
    is not a divestiture.
    """
    if not text:
        return False
    t = text.lower()

    # Filer buying *through* a subsidiary is not a sale of that subsidiary.
    if re.search(
        r"the\s+company['\u2019]s\s+(?:indirect\s+)?"
        r"(?:wholly[-\s]owned\s+)?subsidiary\s+(?:will\s+)?"
        r"(?:merge\s+(?:with\s+and\s+)?into|acquir)",
        t,
    ):
        return False

    if re.search(r'\bdivest(?:iture|ed|s|ing)?\b', t):
        return True
    # Anyone acquiring/buying the Company's property (not the Company).
    if re.search(
        r'(?:acquir|purchas|buy).{0,80}the\s+company[\'\u2019]s\s+',
        t,
    ):
        return True
    if re.search(
        r'(?:acquir|purchas|buy|sale\s+of).{0,80}'
        r'all\s+(?:of\s+)?(?:the\s+)?outstanding\s+shares?\s+of\s+'
        r'(?:the\s+)?company[\'\u2019]s',
        t,
    ):
        return True
    if re.search(
        r'(?:acquir|purchas|buy|sale\s+of).{0,80}'
        r'(?:wholly[-\s]owned\s+)?subsidiary\s+of\s+(?:the\s+)?company\b',
        t,
    ):
        return True
    if re.search(
        r'\b(?:sale|dispos(?:e|al)|sell(?:s|ing)?)\s+of\s+(?:the\s+)?'
        r'(?:company[\'\u2019]s|its)\s+',
        t,
    ):
        return True
    if re.search(r'\bthe\s+company\s+(?:sold|will\s+sell|agreed\s+to\s+sell)\b', t):
        return True
    return False


_VEHICLE_NAME_RE = re.compile(
    r'(?:'
    r'\b(?:merger|acquisition|holdings)\s+'
    r'(?:sub(?:sidiary)?|co(?:mpany)?\.?|corp(?:oration)?\.?)'
    r'|\b(?:purchaser|offeror|bidco)\b'
    r'|\bsub(?:,)?\s+inc\.?'
    r')',
    re.IGNORECASE,
)

_VEHICLE_ROLE_TERMS = frozenset({
    'purchaser', 'merger sub', 'merger subsidiary', 'acquisition sub',
    'acquisition subsidiary', 'acquisition co', 'acquisition corp',
    'merger corp', 'offeror', 'bidco', 'holdings sub',
})

_PARENT_ROLE_TERMS = frozenset({'parent', 'acquiror', 'acquirer'})

# Parent name is bounded: legal suffix optional, then a hard terminator.
# The old fallback `of ([A-Z].{1,80}?)` had no end point and over-captured.
_PARENT_NAME_TOKEN = (
    r'([A-Z][A-Za-z0-9&.\' -]{1,50}?'
    r'(?:,?\s*(?:Inc|Incorporated|Ltd|LLC|L\.L\.C|plc|AG|Corp|'
    r'Corporation|Company|Co|Limited|N\.V|GmbH|SE|L\.P|LP)\.?)?)'
)
_PARENT_NAME_END = (
    r'(?=\s*\(\s*["\u201c]?(?:the\s+)?'
    r'(?:Parent|Buyer|Acquiror|Acquirer)["\u201d]?\s*\)'
    r'|\s*[,.;]'
    r'|\s+and\b'
    r'|\s+will\b'
    r'|\s+agreed\b'
    r'|\s+\('
    r'|$)'
)


_DEFINED_TERM_QUOTES = r'["\u201c\u201d\u2018\u2019]'
_BARE_ROLE_WORDS = frozenset({
    'parent', 'purchaser', 'buyer', 'buyers', 'buyer parties',
    'merger sub', 'merger subsidiary', 'acquisition sub',
    'acquisition subsidiary', 'offeror', 'acquiror', 'acquirer',
    'company', 'the company', 'registrant', 'licensor', 'licensee',
    'licensors', 'licensees',
}) | _VEHICLE_ROLE_TERMS | _PARENT_ROLE_TERMS


_QUOTED_TERM_PATTERNS = (
    re.compile(r'"\s*([^"]+?)\s*"'),
    re.compile(r'\u201c\s*([^\u201d]+?)\s*\u201d'),
    re.compile(r'\u2018\s*([^\u2019]+?)\s*\u2019'),
)


def _quoted_term_spans(text: str) -> list[tuple[int, int, str]]:
    """(start, end, term) for each matched quoted term; open pairs with its close."""
    if not text:
        return []
    spans: list[tuple[int, int, str]] = []
    seen: set[int] = set()
    for rx in _QUOTED_TERM_PATTERNS:
        for m in rx.finditer(text):
            if m.start() in seen:
                continue
            term = re.sub(r'\s+', ' ', m.group(1)).strip()
            if not term:
                continue
            seen.add(m.start())
            spans.append((m.start(), m.end(), term))
    spans.sort(key=lambda s: s[0])
    return spans


def _quoted_terms_in(body: str) -> list[str]:
    """Extract quoted terms. Never pair a closer with the next opener."""
    return [term for _a, _b, term in _quoted_term_spans(body)]


_COMPANY_SUFFIX_RE = re.compile(
    r'(?:,?\s*)?(?:Inc|Incorporated|Ltd|LLC|L\.L\.C|plc|AG|Corp|Corporation|'
    r'Company|Co|Limited|N\.V|GmbH|SE|L\.P|LP|B\.V|K\.K|S\.A|SA|SAS|'
    r'Pty|Pte|AB|Oy|NV|BV|KK|A/S)\.?\s*$',
    re.IGNORECASE,
)
_SUFFIX_ONLY_RE = re.compile(
    r'''^
    (?:the\s+)?
    (?:
        B\.?V\.? | N\.?V\.? | A\.?G\.? | S\.?A\.?S?\.? | plc | Ltd\.? |
        Inc\.? | Corp\.? | LLC | L\.L\.C\.? | GmbH | SE | A/?S | AB |
        KK | K\.K\.? | Co\.? | Limited | Company | Corporation |
        Incorporated | LP | L\.P\.? | Pty | Pte | Oy | NV | BV
    )
    (?:
        \s+(?:
            B\.?V\.? | N\.?V\.? | A\.?G\.? | S\.?A\.?S?\.? | plc | Ltd\.? |
            Inc\.? | Corp\.? | LLC | L\.L\.C\.? | GmbH | SE | A/?S | AB |
            KK | K\.K\.? | Co\.? | Limited | Company | Corporation |
            Incorporated | LP | L\.P\.? | Pty | Pte | Oy | NV | BV
        )
    )*
    \.?
    $''',
    re.IGNORECASE | re.VERBOSE,
)
_LEGAL_FORM_TOKENS = frozenset({
    'corporation', 'company', 'limited', 'liability', 'partnership',
    'incorporated', 'private', 'public', 'unlimited', 'societe', 'société',
    'anonyme', 'kabushiki', 'kaisha', 'besloten', 'vennootschap',
    'naamloze', 'aktiengesellschaft', 'gesellschaft', 'the', 'of', 'and',
    'a', 'an', 'under', 'law', 'laws', 'state', 'kingdom', 'republic',
    'province', 'met', 'beperkte', 'aansprakelijkheid',
    'bv', 'nv', 'ag', 'sa', 'plc', 'ltd', 'inc', 'corp', 'llc', 'gmbh',
    'se', 'ab', 'kk', 'co', 'lp', 'pty', 'pte', 'oy',
})
_JURISDICTION_OR_LAW_RE = re.compile(
    r'\blaws?\b|'
    r'\b(?:organized|incorporated|existing|formed|established)\s+under\b|'
    r'\b(?:state|kingdom|republic|principality|commonwealth|province)\s+of\b',
    re.IGNORECASE,
)
_PLACE_NAME_RE = re.compile(
    r'\b(?:islands?|territor(?:y|ies)|prefecture)\b|'
    r'\b(?:republic|state|commonwealth|kingdom|principality|province|duchy)\s+of\b',
    re.IGNORECASE,
)
# Trailing descriptive clause after the legal name (139767d). Do not cap
# word count: "a private limited liability company organized under …" is long.
_DESCRIPTIVE_CLAUSE_RE = re.compile(
    r""",\s*a(?:n)?\s+
    (?:
        .{0,240}?(?:organized|incorporated|existing|formed|established)\s+under\b.{0,80}
        |
        (?:indirect\s+)?(?:wholly[-\s]owned\s+)?subsidiary\s+of\b.{0,80}
        |
        (?:private|public)\s+limited\b.{0,160}
        |
        (?:[\w.\-]+\s+){0,8}
        (?:corporation|company|partnership|limited(?:\s+liability\s+company)?|
           societ[eé](?:\s+anonyme)?|aktiengesellschaft|kabushiki\s+kaisha|
           besloten\s+vennootschap|naamloze\s+vennootschap)
    )
    \s*$""",
    re.IGNORECASE | re.VERBOSE,
)
_PARTY_LIST_INTRO_RE = re.compile(
    r'(?:'
    r'\bby\s+and\s+among\s+the\s+Company\s*,'
    r'|\bby\s+and\s+between\s+the\s+Company\s*,'
    r'|\bamong\s+the\s+Company\s*,'
    r'|\bbetween\s+the\s+Company\s*,'
    r'|\bwith\s+the\s+Company\s*,'
    r'|\bby\s+and\s+among\b'
    r'|\bby\s+and\s+between\b'
    r')',
    re.IGNORECASE,
)
_UNQUOTED_ROLE_RE = re.compile(
    r'(?:the\s+)?(Purchaser|Parent|Company|Registrant|Merger\s+Sub(?:sidiary)?'
    r'|Acquisition\s+Sub(?:sidiary)?|Offeror|Buyer|Acquiror|Licensor|Licensee)\s*$',
    re.IGNORECASE,
)
_LEGAL_SUFFIX_ALT = (
    r'Inc|Incorporated|Ltd|LLC|L\.L\.C|plc|AG|Corp|Corporation|'
    r'Company|Co|Limited|N\.V|GmbH|SE|L\.P|LP|'
    r'B\.V|K\.K|S\.A|SA|SAS|Pty|Pte|AB|Oy|NV|BV|KK|A/S'
)
# 139767d end-of-clause capture: proper names, or a lowercase brand + suffix.
_DEFINED_NAME_RE = re.compile(
    r'('
    r'[a-z][A-Za-z0-9&.\'-]*(?:\s+[a-z][A-Za-z0-9&.\'-]*){0,4}'
    r'(?:,?\s*(?:' + _LEGAL_SUFFIX_ALT + r')\.?)'
    r'|'
    r'[A-Z][A-Za-z0-9&.\'-]*'
    r'(?:\s+(?:and|&|of(?:\s+the)?)\s+[A-Z][A-Za-z0-9&.\'-]*'
    r'|\s+[A-Z&][A-Za-z0-9&.\'-]*){0,6}'
    r'(?:,?\s*(?:' + _LEGAL_SUFFIX_ALT + r')\.?)?'
    r')\s*$',
)
_NAME_ROLE_LEAK_RE = re.compile(
    r'\b(?:the\s+Company|Parent|Merger\s+Sub(?:sidiary)?|Purchaser|'
    r'Stockholders?|Representatives?|Guarantors?|Offeror|Registrant|'
    r'Holders?)\b',
    re.IGNORECASE,
)
_INSTITUTION_NAME_RE = re.compile(
    r'\b(?:university|college|institute|institution|foundation|'
    r'hospital|academy|school|museum|library)\b',
    re.IGNORECASE,
)
_INSTITUTIONAL_LEGAL_NAME_RE = re.compile(
    r'(?ix)^\s*(?:the\s+)?'
    r'(?:'
    r'president\s+and\s+fellows\s+of\s+\S'
    r'|regents\s+of\s+(?:the\s+)?\S'
    r'|trustees\s+of\s+(?:the\s+)?.{0,80}'
    r'(?:university|college|institute|institution|foundation|hospital|academy|school)'
    r')',
)
_GOVERNANCE_UNIT = (
    r'(?:[\w]+\s+){0,4}(?:committees?|commissions?|boards?)'
    r'(?:\s+of\s+(?:the\s+)?(?:board|directors|managers|trustees|'
    r'supervisors|governors))?'
    r'|(?:supervisory|management|executive)\s+board'
    r'|board\s+of\s+\w+'
    r'|(?:general\s+)?partners?'
    r'|(?:managing\s+)?members?'
    r'|managers?'
    r'|trustees?'
    r'|(?:chief\s+)?(?:\w+\s+){0,2}officers?'
    r'|(?:share|stock)holders?'
    r'|chair(?:man|woman|person)?s?'
    r'|presidents?'
    r'|directors?'
    r'|secretar(?:y|ies)|treasurers?'
)
_GOVERNANCE_NAME_RE = re.compile(
    rf'\b(?:{_GOVERNANCE_UNIT}|agreement\s+and\s+plan\s+of\s+\w+|plan\s+of\s+\w+)\b',
    re.IGNORECASE,
)
_GOVERNANCE_PREFIX_RE = re.compile(
    rf'^(?:the\s+)?(?:{_GOVERNANCE_UNIT})\s+of(?:\s+the)?\s+',
    re.IGNORECASE,
)
_GOVERNANCE_TOKEN_RE = re.compile(
    r'(?ix)^(committee|commission|board|partner|member|manager|trustee|'
    r'officer|director|shareholder|stockholder|chair|chairman|chairwoman|'
    r'chairperson|president|secretary|treasurer|agreement|plan|merger)$',
)
_AGREEMENT_TITLE_HEADING_RE = re.compile(
    r'(?:^|[\n\r:;]\s*|(?<=[.!?]\s))'
    r'(?:(?:Amended\s+and\s+Restated|A\s*&\s*R)\s+)?'
    r'(?:[A-Z][A-Za-z0-9&]+\s+){0,8}'
    r'(?:Agreement(?:\s+and\s+Plan\s+of\s+(?:Merger|Reorganization|'
    r'Business\s+Combination))?'
    r'|Plan\s+of\s+(?:Merger|Reorganization|Business\s+Combination)'
    r'|Business\s+Combination\s+Agreement)',
    re.IGNORECASE,
)
_NAME_JOIN_LEAK_RE = re.compile(r'\b(?:among|between|with)\b', re.IGNORECASE)
_NOT_PARENT_ROLES = frozenset({
    'stockholder', 'stockholders', 'holder', 'holders',
    'representative', 'stockholder representative',
    'holder representative', 'guarantor', 'guarantors',
})
_ROLE_GROUP = {
    'parent': 'buyer_parent',
    'acquiror': 'buyer_parent',
    'acquirer': 'buyer_parent',
    'buyer': 'buyer_parent',
    'merger sub': 'vehicle',
    'merger subsidiary': 'vehicle',
    'purchaser': 'vehicle',
    'offeror': 'vehicle',
    'acquisition sub': 'vehicle',
    'acquisition subsidiary': 'vehicle',
    'acquisition co': 'vehicle',
    'acquisition corp': 'vehicle',
    'bidco': 'vehicle',
    'company': 'company',
    'the company': 'company',
    'registrant': 'company',
    'licensee': 'licensee',
    'licensor': 'licensor',
}


def _strip_descriptive_clause(before: str) -> str:
    """Remove trailing entity-type / jurisdiction clauses so the legal name remains."""
    if not before:
        return ''
    text = before
    text = re.sub(
        rf'\((?!{ _DEFINED_TERM_QUOTES }|the\s+{ _DEFINED_TERM_QUOTES })[^)]{{0,160}}\)\s*$',
        '', text,
    ).strip()
    text = _DESCRIPTIVE_CLAUSE_RE.sub('', text).strip()
    text = re.sub(
        r'(?:,\s*a(?:n)?\s+.{0,200}?)?'
        r'(?:organized|incorporated|existing|formed|established)\s+'
        r'under\b.{0,80}$',
        '', text, flags=re.IGNORECASE,
    ).strip()
    text = re.sub(
        r'\s+under\s+(?:the\s+)?(?:[\w\-]+\s+){0,6}laws?\s*$',
        '', text, flags=re.IGNORECASE,
    ).strip()
    # Own-clause only: cut at the first descriptive comma in THIS piece.
    cut = re.search(
        r',\s*(?:a(?:n)?\s+|organized\b|incorporated\b|existing\b|formed\b)',
        text, re.IGNORECASE,
    )
    if cut:
        text = text[:cut.start()]
    return text.rstrip(',').strip()


def _is_suffix_only_name(name: str) -> bool:
    """True for BV / B.V. / N.V. / AG / plc / Ltd / Inc. and combinations."""
    if not name:
        return True
    compact = re.sub(r'[,\s]+', ' ', name.strip()).strip().rstrip('.')
    return bool(_SUFFIX_ONLY_RE.match(compact))


def _is_place_name(name: str) -> bool:
    if not name:
        return False
    return bool(_PLACE_NAME_RE.search(name))


def _is_jurisdiction_or_legal_form_name(name: str) -> bool:
    """True when a capture is only a place, legal-form word, or 'Law' phrase."""
    if not name:
        return True
    if _is_suffix_only_name(name) or _is_place_name(name):
        return True
    low = name.strip().lower()
    if _JURISDICTION_OR_LAW_RE.search(low):
        return True
    tokens = re.findall(r"[a-zà-ÿ]+", low, flags=re.IGNORECASE)
    if tokens and all(tok in _LEGAL_FORM_TOKENS for tok in tokens):
        return True
    return False


def _looks_like_company_name(name: str) -> bool:
    """True for a legal-style name. Suffix-only and place names are not."""
    if not name or _is_bare_role_word(name) or _is_jurisdiction_or_legal_form_name(name):
        return False
    if _is_suffix_only_name(name):
        return False
    if _COMPANY_SUFFIX_RE.search(name.strip()) and not _is_suffix_only_name(name):
        return True
    if re.search(r'\band\s+Company\b', name, re.IGNORECASE):
        return True
    parts = [
        p for p in re.findall(r"[A-Za-z][A-Za-z0-9&.\'-]*", name)
        if p.lower() not in {'law', 'laws', 'the', 'of', 'and'}
    ]
    return len(parts) >= 2


def _is_defined_term_paren_body(body: str) -> bool:
    if _quoted_terms_in(body):
        return True
    return bool(_UNQUOTED_ROLE_RE.match(body.strip()))


def _own_party_clause(text: str, paren_start: int) -> str:
    """Text belonging to this defined-term parenthesis only.

    Stops at the previous defined-term ')', at a party-list intro
    ('by and among the Company,' / among / between / with the Company,),
    or at a sentence/paragraph start. Never a fixed look-back window.
    """
    before = text[:paren_start]
    start = 0
    for m in re.finditer(r'\(([^)]{0,240})\)', before):
        if _is_defined_term_paren_body(m.group(1)):
            start = m.end()
    piece = before[start:]
    intros = list(_PARTY_LIST_INTRO_RE.finditer(piece))
    if intros:
        piece = piece[intros[-1].end():]
    paras = re.split(r'[\n\r]+', piece)
    piece = paras[-1] if paras else piece
    sents = _split_sentences(piece)
    if sents:
        piece = sents[-1]
    piece = re.sub(r'^and\s+', '', piece.strip(), flags=re.IGNORECASE)
    headings = list(_AGREEMENT_TITLE_HEADING_RE.finditer(piece))
    if headings:
        piece = piece[headings[-1].end():]
    return piece.strip()


def _is_institutional_legal_name(name: str) -> bool:
    """True only when the name STARTS with an institutional legal form."""
    if not name:
        return False
    return bool(_INSTITUTIONAL_LEGAL_NAME_RE.match(name.strip()))


def _name_has_governance_leak(name: str) -> bool:
    """True when a capture is a committee/board/officer phrase, not a party.

    Institutional legal names that include a role word (Regents of a
    university, President and Fellows of a college) are kept intact.
    """
    if not name:
        return False
    if _is_institutional_legal_name(name):
        return False
    return bool(_GOVERNANCE_NAME_RE.search(name))


def _name_is_fragment(name: str) -> bool:
    """True for leftovers such as 'and Fellows…', 'of Parent', 'the board'."""
    if not name:
        return True
    if name[0].islower() and name.lower().startswith(('and ', 'of ', 'the ')):
        return True
    if name.startswith(('& ', '&')):
        return True
    if name.lower().startswith(('and ', 'of ')):
        return True
    return False


def _name_starts_with_role_word(name: str) -> bool:
    if not name or _is_institutional_legal_name(name):
        return False
    first = re.split(r'\s+', name.strip(), maxsplit=1)[0]
    return bool(_GOVERNANCE_TOKEN_RE.match(first.rstrip('.,')))


def _name_contains_agreement_word(name: str) -> bool:
    if not name or _is_institutional_legal_name(name):
        return False
    return bool(re.search(r'\bagreement\b', name, re.IGNORECASE))


def _is_unpublishable_party_name(name: str) -> bool:
    """Never publish a counterparty that is a fragment, heading, or role phrase."""
    if not name:
        return True
    if _name_is_fragment(name) or _name_starts_with_role_word(name):
        return True
    if _name_contains_agreement_word(name):
        return True
    if _name_has_governance_leak(name):
        return True
    return False


def _fallback_clean_party_name(name: str) -> str | None:
    """Strip leading governance/title units; keep a full legal entity or drop.

    Stopping at a role word must not leave a fragment. Institutional names
    that include the role word are returned unchanged.
    """
    if not name:
        return None
    name = re.sub(r'\s+', ' ', name.strip().rstrip(','))
    name = _strip_agreement_title_from_name(name)
    if _is_institutional_legal_name(name):
        return name if _looks_like_company_name(name) else None
    while True:
        m = _GOVERNANCE_PREFIX_RE.match(name)
        if not m:
            break
        name = name[m.end():].strip()
        if _name_is_fragment(name):
            name = re.sub(r'^(?:and|&|of|the)\s+', '', name, flags=re.IGNORECASE).strip()
    name = _strip_agreement_title_from_name(name)
    if _is_unpublishable_party_name(name):
        return None
    if _resolved_name_disallowed(name) or not _looks_like_company_name(name):
        return None
    return name


def _strip_agreement_title_from_name(name: str) -> str:
    name = re.sub(
        r'^(?:(?:Amended\s+and\s+Restated|A\s*&\s*R)\s+)?'
        r'(?:[A-Za-z][A-Za-z0-9&]+\s+){0,8}'
        r'(?:Agreement(?:\s+and\s+Plan\s+of\s+(?:Merger|Reorganization|'
        r'Business\s+Combination))?|Plan\s+of\s+(?:Merger|Reorganization|'
        r'Business\s+Combination)|Business\s+Combination\s+Agreement)\s+',
        '', name, flags=re.IGNORECASE,
    )
    return name.strip()


def _resolved_name_disallowed(name: str) -> bool:
    """Post-validation: suffix-only, place, role leak, join words, sentence break."""
    if not name:
        return True
    if _is_suffix_only_name(name) or _is_place_name(name):
        return True
    if _is_jurisdiction_or_legal_form_name(name) or _is_bare_role_word(name):
        return True
    if _name_has_governance_leak(name):
        return True
    if re.search(r'[.!?。]\s', name):
        return True
    if _NAME_JOIN_LEAK_RE.search(name):
        return True
    if _NAME_ROLE_LEAK_RE.search(name):
        return True
    return False


def _of_extend_allowed(word: str, rest: str) -> bool:
    """Do not walk 'of' over role, governance, or agreement-title words."""
    low = word.lower().rstrip('.')
    if low in {'agreement', 'plan', 'merger'}:
        return False
    candidate = f"{word} of {rest}"
    if _is_institutional_legal_name(candidate):
        return True
    if _is_institutional_legal_name(rest):
        return False
    if _INSTITUTION_NAME_RE.search(rest) and low in {
        'president', 'fellows', 'regents', 'trustees',
    }:
        return True
    return not bool(_GOVERNANCE_TOKEN_RE.match(low))


def _complete_of_name(stripped: str, name: str, start: int) -> str | None:
    """If the capture sits after 'of' / 'of the', extend or stop at a role word.

    Never return a fragment. Role/governance words stop the walk; the
    already-captured party is kept only if it is a full legal entity.
    """
    while start > 0:
        left = stripped[:start]
        m = re.search(
            r'([A-Z][A-Za-z0-9&.\'-]*)\s+(of(?:\s+the)?)\s+$', left,
        )
        if m:
            word = m.group(1)
            if not _of_extend_allowed(word, name):
                break
            name = f"{word} {m.group(2)} {name}"
            start = m.start()
            continue
        if re.search(r'\bof(?:\s+the)?\s+$', left, re.IGNORECASE):
            prev = re.search(r'([A-Za-z][A-Za-z0-9&.\'-]*)\s+of(?:\s+the)?\s+$', left)
            if prev and not _of_extend_allowed(prev.group(1), name):
                break
            return None
        break
    if _name_is_fragment(name) or (
        _is_unpublishable_party_name(name) and not _is_institutional_legal_name(name)
    ):
        cleaned = _fallback_clean_party_name(name)
        return cleaned
    return name


def _accept_captured_name(name: str) -> str | None:
    name = re.sub(r'\s+', ' ', name.strip().rstrip(','))
    name = _strip_agreement_title_from_name(name)
    if _is_institutional_legal_name(name):
        return name if _looks_like_company_name(name) else None
    if _name_has_governance_leak(name) or _is_unpublishable_party_name(name):
        name = _fallback_clean_party_name(name)
        if not name:
            return None
    if _is_unpublishable_party_name(name):
        return None
    if _resolved_name_disallowed(name) or not _looks_like_company_name(name):
        return None
    return name


def _match_name_at_end(stripped: str) -> str | None:
    if not stripped:
        return None
    nm = _DEFINED_NAME_RE.search(stripped)
    if not nm:
        return None
    if nm.start() > 0 and stripped[nm.start() - 1].isalnum():
        return None
    name = _complete_of_name(stripped, nm.group(1), nm.start())
    if not name:
        return None
    return _accept_captured_name(name)


def _name_from_own_clause(clause: str) -> str | None:
    """139767d-style end-of-clause name, plus 'of the' names and lowercase brands."""
    stripped = _strip_descriptive_clause(clause)
    if not stripped:
        return None
    name = _match_name_at_end(stripped)
    if name:
        return name
    # Lowercase brand after the last stop word (with/and/among/between).
    stops = list(re.finditer(r'\b(?:with|and|among|between)\s+', stripped, re.IGNORECASE))
    if stops:
        return _match_name_at_end(stripped[stops[-1].end():])
    return None


def _name_appears_verbatim(name: str, filing_text: str) -> bool:
    """True when the published string appears verbatim in the filing."""
    if not name or not filing_text:
        return False
    if name in filing_text:
        return True
    compact = re.sub(r'\s+', ' ', name).strip()
    return compact in re.sub(r'\s+', ' ', filing_text)


def _appears_as_party_in_filing(name: str, filing_text: str) -> bool:
    if not name or not filing_text:
        return False
    if not match_company_whole_word(name, filing_text):
        return False
    return bool(re.search(
        rf'(?:entered\s+into|agreement\s+with|acquire|between|and|with)\s+'
        rf'.{{0,80}}{re.escape(name)}|{re.escape(name)}.{{0,40}}'
        rf'(?:entered|agreed|acquire|merger|license)',
        filing_text, re.IGNORECASE,
    ))


def is_plausible_party_name(name: str, filing_text: str = '') -> bool:
    """A resolved party must look like a company or appear as a party in the filing.

    Suffix-only strings (BV, N.V., AG, plc, …) and place names never qualify,
    even if they appear in the filing. Governance phrases (Board of Directors,
    Shareholders of X) are never a party name.
    """
    if (
        not name
        or _is_bare_role_word(name)
        or _is_suffix_only_name(name)
        or _is_jurisdiction_or_legal_form_name(name)
        or _is_unpublishable_party_name(name)
    ):
        return False
    if _looks_like_company_name(name):
        return True
    return _appears_as_party_in_filing(name, filing_text)


def parse_defined_terms(text: str) -> dict[str, list[str]]:
    """Map a defined term (lowercased) to the company names that carry it.

    Handles straight and curly quotes, and combined parentheticals such as
    `ABC Inc. ("Parent" and, together with Merger Sub, the "Buyer Parties")`.
    Skips entity-type and jurisdiction clauses so Parent is never "Dutch Law".
    """
    mapping: dict[str, list[str]] = {}
    if not text:
        return mapping

    def _add(term: str, name: str) -> None:
        term = re.sub(r'\s+', ' ', term.strip().lower())
        name = re.sub(r'\s+', ' ', name.strip().rstrip(',').strip())
        name = re.sub(r',\s*a(?:n)?\s+[\w\s.\-]+$', '', name, flags=re.IGNORECASE).strip()
        if not term or not name:
            return
        if (
            _is_suffix_only_name(name)
            or _is_jurisdiction_or_legal_form_name(name)
            or _is_bare_role_word(name)
        ):
            return
        if _resolved_name_disallowed(name):
            return
        if term in _BARE_ROLE_WORDS and not _looks_like_company_name(name):
            return
        mapping.setdefault(term, [])
        if name not in mapping[term]:
            mapping[term].append(name)

    for m in re.finditer(r'\(([^)]{0,240})\)', text):
        body = m.group(1)
        quoted = _quoted_terms_in(body)
        if not quoted:
            um = _UNQUOTED_ROLE_RE.match(body.strip())
            if um:
                quoted = [um.group(1)]
        if not quoted:
            continue
        name = _name_from_own_clause(_own_party_clause(text, m.start()))
        if not name:
            continue
        for term in quoted:
            _add(term, name)
    return mapping


def _defined_roles_conflict(term_map: dict[str, list[str]], filer: str = '') -> bool:
    """True when one company name lands on two distinct roles, or Parent is filer/target."""
    if not term_map:
        return False
    name_groups: dict[str, set[str]] = {}
    for term, names in term_map.items():
        group = _ROLE_GROUP.get(term)
        if not group:
            continue
        for raw in names:
            if not is_plausible_party_name(raw):
                continue
            key = normalize_company_name(raw).lower()
            if not key:
                continue
            name_groups.setdefault(key, set()).add(group)
    if any(len(groups) > 1 for groups in name_groups.values()):
        return True
    parent_names = []
    for term in _PARENT_ROLE_TERMS:
        parent_names.extend(term_map.get(term, []))
    company_names = []
    for key in ('company', 'the company', 'registrant'):
        company_names.extend(term_map.get(key, []))
    for parent in parent_names:
        if company_names and _name_matches_any(parent, company_names):
            return True
    for n in term_map.get('licensee', []):
        if filer and (match_company_whole_word(filer, n) or match_company_whole_word(n, filer)):
            return True
    return False


def _is_bare_role_word(name: str) -> bool:
    """True for a published name that is only a role noun (Parent, Purchaser…)."""
    if not name:
        return True
    raw = name.strip().lower()
    raw = raw.strip('"\u201c\u201d\u2018\u2019')
    raw = re.sub(r'^the\s+', '', raw).strip()
    if raw in _BARE_ROLE_WORDS:
        return True
    n = normalize_company_name(name).lower().strip()
    n = re.sub(r'^the\s+', '', n).strip()
    return n in _BARE_ROLE_WORDS


def _the_company_is_filer(term_map: dict[str, list[str]], filer: str) -> bool | None:
    """True if 'the Company' is the filer, False if defined as someone else.

    None means the definitions conflict (drop the deal).
    When the term is undefined, SEC convention is that it is the filer.
    """
    names: list[str] = []
    for key in ('company', 'the company', 'registrant'):
        names.extend(term_map.get(key, []))
    names = [n for n in names if is_plausible_party_name(n)]
    if not names:
        return True
    filer_hits = [
        n for n in names
        if match_company_whole_word(filer, n) or match_company_whole_word(n, filer)
    ]
    others = [n for n in names if n not in filer_hits]
    if others and not filer_hits:
        return False
    if filer_hits and others:
        return None
    return True


def _filer_ref_pattern(filer: str, term_map: dict[str, list[str]]) -> str:
    """Regex matching the filer by name or by a defined term that names the filer."""
    alts = [re.escape(normalize_company_name(filer))]
    company_is_filer = _the_company_is_filer(term_map, filer)
    if company_is_filer is True:
        alts.append(r'the\s+Company')
        alts.append(r'the\s+Registrant')
    for term, names in term_map.items():
        if _is_bare_role_word(term) and _name_matches_any(filer, names):
            alts.append(re.escape(term))
    return '(?:' + '|'.join(alts) + ')'


def _phrase_is_filer(phrase: str, filer: str, term_map: dict[str, list[str]]) -> bool:
    if not phrase or not filer:
        return False
    if match_company_whole_word(filer, phrase) or match_company_whole_word(phrase, filer):
        return True
    if re.search(r'\bthe\s+company\b|\bthe\s+registrant\b', phrase, re.IGNORECASE):
        return _the_company_is_filer(term_map, filer) is True
    for term, names in term_map.items():
        if re.search(rf'\b{re.escape(term)}\b', phrase, re.IGNORECASE) and _name_matches_any(filer, names):
            return True
    return False


def is_merger_vehicle_name(name: str) -> bool:
    """True for Merger Sub / Purchaser / Acquisition Co / Offeror / Sub, Inc."""
    if not name:
        return False
    raw = name.strip()
    n = normalize_company_name(raw).strip()
    if not raw:
        return False
    if _VEHICLE_NAME_RE.search(raw) or (n and _VEHICLE_NAME_RE.search(n)):
        return True
    return n.lower() in _VEHICLE_ROLE_TERMS or raw.lower() in _VEHICLE_ROLE_TERMS


def _name_matches_any(name: str, candidates: list[str]) -> bool:
    for cand in candidates:
        if match_company_whole_word(name, cand) or match_company_whole_word(cand, name):
            return True
    return False


def _is_defined_as_vehicle(name: str, term_map: dict[str, list[str]]) -> bool:
    if is_merger_vehicle_name(name):
        return True
    for term, names in term_map.items():
        if term not in _VEHICLE_ROLE_TERMS:
            continue
        if _name_matches_any(name, names):
            return True
    return False


def _clean_parent_name(raw: str, term_map: dict[str, list[str]] | None = None) -> str | None:
    if not raw:
        return None
    if _is_bare_role_word(raw):
        if term_map:
            key = re.sub(r'^the\s+', '', raw.strip().lower())
            for n in term_map.get(key, []) + term_map.get(raw.strip().lower(), []):
                if not _is_bare_role_word(n):
                    cleaned = _clean_parent_name(n, None)
                    if cleaned:
                        return cleaned
        return None
    parent = normalize_company_name(raw).strip().rstrip(',')
    parent = re.sub(r'\s+', ' ', parent)
    if _is_unpublishable_party_name(parent) or _name_has_governance_leak(parent):
        parent = _fallback_clean_party_name(parent)
        if not parent:
            return None
    if not parent or is_merger_vehicle_name(parent) or _is_bare_role_word(parent):
        return None
    if _is_jurisdiction_or_legal_form_name(parent) or not is_plausible_party_name(parent):
        return None
    if _resolved_name_disallowed(raw) or _resolved_name_disallowed(parent):
        return None
    if term_map:
        for role in _NOT_PARENT_ROLES:
            if _name_matches_any(raw, term_map.get(role, [])) or _name_matches_any(
                parent, term_map.get(role, [])
            ):
                return None
    return parent


def _parent_from_defined_terms(term_map: dict[str, list[str]]) -> str | None:
    for term in _PARENT_ROLE_TERMS:
        for raw in term_map.get(term, []):
            parent = _clean_parent_name(raw, term_map)
            if parent:
                return parent
    return None


def _extract_parent_near_name(name: str, blob: str, term_map: dict[str, list[str]]) -> str | None:
    """Find the named parent in the same clause as the vehicle."""
    escaped = re.escape(name)
    local_patterns = [
        # NAME, a wholly owned subsidiary of PARENT
        rf'{escaped}[^.]{{0,200}}?(?:an?\s+)?(?:indirect\s+)?'
        rf'wholly[-\s]owned\s+subsidiary\s+of\s+{_PARENT_NAME_TOKEN}{_PARENT_NAME_END}',
        # NAME (a wholly-owned subsidiary of PARENT)
        rf'{escaped}[^.]{{0,120}}?\(\s*(?:an?\s+)?(?:indirect\s+)?'
        rf'wholly[-\s]owned\s+subsidiary\s+of\s+([^)]+?)\)',
        # subsidiary of PARENT ("NAME")
        rf'(?:wholly[-\s]owned\s+)?subsidiary\s+of\s+'
        rf'{_PARENT_NAME_TOKEN}\s*\(\s*["\u201c]?{escaped}',
        # PARENT, through its wholly owned subsidiary NAME
        rf'{_PARENT_NAME_TOKEN},\s+through\s+its\s+'
        rf'(?:indirect\s+)?(?:wholly[-\s]owned\s+)?subsidiary\s+{escaped}',
    ]
    for pat in local_patterns:
        m = re.search(pat, blob, re.IGNORECASE)
        if not m:
            continue
        parent = _clean_parent_name(m.group(1), term_map)
        if parent and not (
            match_company_whole_word(parent, name) or match_company_whole_word(name, parent)
        ):
            return parent
    # "subsidiary of Parent" where Parent is a defined term
    if re.search(
        rf'{escaped}[^.]{{0,200}}?subsidiary\s+of\s+(?:the\s+)?Parent\b',
        blob, re.IGNORECASE,
    ):
        return _parent_from_defined_terms(term_map)
    return None


def _described_as_wholly_owned_sub(name: str, blob: str) -> bool:
    if not name or not blob:
        return False
    return bool(re.search(
        rf'{re.escape(name)}[^.]{{0,200}}?(?:an?\s+)?(?:indirect\s+)?'
        rf'wholly[-\s]owned\s+subsidiary\s+of\b',
        blob, re.IGNORECASE,
    ))


def is_merger_or_tender_vehicle(
    name: str,
    filing_text: str = '',
    type_quote: str = '',
    deal_type: DealType | None = None,
) -> bool:
    """True when the party is a merger/tender vehicle by NAME or by ROLE."""
    if not name:
        return False
    if is_merger_vehicle_name(name):
        return True
    generic_sub = re.match(
        r'^(?:an?\s+)?(?:indirect\s+)?wholly[-\s]owned\s+subsidiary\s+of\s+',
        name.strip(), re.IGNORECASE,
    )
    if generic_sub:
        return True
    blob = f"{type_quote}\n{filing_text or ''}"
    term_map = parse_defined_terms(blob)
    if _is_defined_as_vehicle(name, term_map):
        return True
    merger_context = (
        deal_type in (DealType.ACQUISITION, DealType.MERGER)
        or _is_merger_or_purchase_quote(type_quote)
        or bool(re.search(r'\b(?:tender\s+offer|offeror)\b', blob, re.IGNORECASE))
    )
    if merger_context and _described_as_wholly_owned_sub(name, blob):
        return True
    return False


def resolve_merger_vehicle(
    name: str,
    filing_text: str,
    type_quote: str = "",
    deal_type: DealType | None = None,
) -> str | None:
    """Replace a merger/tender vehicle with the named parent, or None to drop.

    Vehicles are identified by role as well as name: a party defined as
    Purchaser / Merger Sub / Acquisition Sub / Offeror, or described as a
    wholly owned subsidiary of Parent, whatever its legal name.
    Non-vehicles are returned unchanged. If no parent is named, return None.
    """
    if not name:
        return None
    generic_sub = re.match(
        r'^(?:an?\s+)?(?:indirect\s+)?wholly[-\s]owned\s+subsidiary\s+of\s+(.+)$',
        name.strip(), re.IGNORECASE,
    )
    if generic_sub:
        return _clean_parent_name(generic_sub.group(1), parse_defined_terms(filing_text or ''))

    blob = f"{type_quote}\n{filing_text or ''}"
    term_map = parse_defined_terms(blob)
    is_vehicle = is_merger_or_tender_vehicle(name, filing_text, type_quote, deal_type)
    if not is_vehicle:
        return name

    parent = _extract_parent_near_name(name, blob, term_map)
    if parent:
        return None if _is_bare_role_word(parent) else parent
    # Name is a role noun ("Purchaser") — resolve the company defined as that role,
    # then that company's parent.
    role_key = normalize_company_name(name).lower()
    for defined_name in term_map.get(role_key, []):
        if is_merger_vehicle_name(defined_name) or defined_name.lower() == name.lower():
            continue
        nested = _extract_parent_near_name(defined_name, blob, term_map)
        if nested:
            return nested
        # The defined name might itself be the parent if it isn't a vehicle —
        # but a company defined as Purchaser is the vehicle, not the parent.
    parent = _parent_from_defined_terms(term_map)
    if parent and not (
        match_company_whole_word(parent, name) or match_company_whole_word(name, parent)
    ):
        # Only use the defined Parent when this name is tied to it.
        if _described_as_wholly_owned_sub(name, blob) or is_merger_vehicle_name(name):
            if _is_bare_role_word(parent):
                return None
            return parent
    return None


_ENTERED_NEW_AGREEMENT_RE = re.compile(
    r'(?i)(?:entered\s+into|executed|signed)\s+'
    r'(?:an?\s+|the\s+)?(?:that\s+certain\s+)?'
    r'((?:(?!dated\b)[^\.;]){0,160}?'
    r'(?:agreement|addendum|indenture|amendment|joinder|novation|'
    r'letter|pact|licence|license))'
)
_ACQUIRE_POSITIVE_RE = re.compile(
    r'(?i)(?:will|agreed\s+to|agrees\s+to)\s+acquire|'
    r'commenced\s+(?:a\s+)?tender\s+offer'
)


def _title_is_new_license_or_collab(title: str) -> bool:
    if not title or is_wrapper_or_existing_rights_language(title):
        return False
    return bool(re.search(r'\b(?:licen[sc](?:e|ing)|collaboration)\b', title, re.IGNORECASE))


def _title_is_merger_instrument(title: str) -> bool:
    if not title or is_wrapper_or_existing_rights_language(title):
        return False
    return bool(re.search(
        r'(?:agreement\s+and\s+plan\s+of\s+merger|'
        r'business\s+combination\s+agreement|'
        r'plan\s+of\s+(?:merger|reorganization|business\s+combination))',
        title, re.IGNORECASE,
    ))


def _title_is_acquisition_instrument(title: str) -> bool:
    if _title_is_merger_instrument(title):
        return True
    if not title or is_wrapper_or_existing_rights_language(title):
        return False
    return bool(re.search(
        r'\b(?:(?:stock|asset)\s+)?purchase\s+agreement\b|\bmerger\s+agreement\b',
        title, re.IGNORECASE,
    ))


def _date_in_or_near_period(dated: date | None, reference_date: str | None) -> bool:
    """Undated 8-K entered-into counts as current; else require date within a year."""
    if dated is None:
        return True
    ref = _parse_loose_date(reference_date) or date.today()
    return dated >= ref - _OLD_EVENT_CUTOFF


def _on_calendar_date(text: str) -> date | None:
    if not text:
        return None
    m = _ON_CALENDAR_DATE_RE.search(text)
    if not m:
        return None
    return _parse_month_day_year(m.group(1), m.group(2), m.group(3))


def _date_on_entered_into_sentence(title: str, sentence: str) -> date | None:
    """Date stated on this entered-into sentence only — never another instrument's date."""
    dated = _dated_clause_date(sentence) or _on_calendar_date(sentence)
    if dated:
        return dated
    m = _ENTERED_IN_MONTH_YEAR_RE.search(sentence)
    if m:
        try:
            return date(int(m.group(2)), _MONTH_NAME_TO_NUM[m.group(1).lower()], 1)
        except (ValueError, KeyError):
            pass
    for src in (title, sentence):
        ym = _YEAR_NAMED_INSTRUMENT_RE.search(src)
        if ym:
            try:
                return date(int(ym.group(1)), 1, 1)
            except ValueError:
                continue
    return None


def _extend_entered_into_title(title: str, filing_text: str, match_end: int) -> str:
    """Keep 'Agreement and Plan of Merger' from being cut at the first Agreement."""
    rest = filing_text[match_end:match_end + 80]
    ext = re.match(
        r'(?i)\s+and\s+plan\s+of\s+(?:merger|reorganization|business\s+combination)',
        rest,
    )
    if ext:
        return (title + ext.group(0)).strip()
    return title


def _party_tied_to_instrument(
    title: str,
    sentence: str,
    filing_text: str,
    counterparty: str,
) -> bool:
    if _sentence_names_party(sentence, counterparty, filing_text):
        return True
    aliases, dates = _collect_instrument_catalog(filing_text)
    keys = _keys_for_instrument(title, aliases, dates)
    for short in _quoted_terms_in(sentence):
        keys.update(_keys_for_instrument(short, aliases, dates))
    keys.discard('agreement')
    keys.discard('')
    if not keys:
        return False
    for sent in _split_sentences(filing_text):
        if not _sentence_names_party(sent, counterparty, filing_text):
            continue
        if any(re.search(rf'\b{re.escape(k)}\b', sent, re.IGNORECASE) for k in keys):
            return True
        labels = _referred_agreement_labels(sent, filing_text)
        if any(_norm_agreement_key(lab) in keys for lab in labels):
            return True
    return False


def _dated_grant_is_new_license(
    filing_text: str,
    counterparty: str,
    reference_date: str,
    type_quote: str,
) -> bool:
    """8-K shorthand: 'On <date>, the Company granted X an exclusive license'."""
    passage = ''
    if type_quote:
        passage = _containing_passage(type_quote, filing_text) or type_quote
    if not passage:
        passage = filing_text
    grant_text = type_quote or passage
    if not (
        has_license_grant_language(grant_text) or has_license_grant_language(passage)
    ):
        return False
    if not _sentence_names_party(passage, counterparty, filing_text):
        return False
    if type_quote and _grant_refers_to_amended_agreement(type_quote, filing_text):
        return False
    if is_historical_agreement(type_quote or passage, reference_date, filing_text):
        return False
    dated = _dated_clause_date(passage) or _on_calendar_date(passage)
    if dated is None:
        return False
    return _date_in_or_near_period(dated, reference_date)


def _sentence_names_party(sentence: str, counterparty: str, filing_text: str) -> bool:
    if not sentence or not counterparty:
        return False
    if match_company_whole_word(counterparty, sentence):
        return True
    term_map = parse_defined_terms(filing_text)
    for term, names in term_map.items():
        if not re.search(rf'\b{re.escape(term)}\b', sentence, re.IGNORECASE):
            continue
        for n in names:
            if match_company_whole_word(counterparty, n) or match_company_whole_word(n, counterparty):
                return True
    return False


def has_affirmative_new_agreement(
    deal_type: DealType,
    filing_text: str,
    counterparty: str,
    reference_date: str = '',
    type_quote: str = '',
) -> bool:
    """True only when the filing affirms a new in-period agreement with that party.

    License/collaboration: entered into / executed / signed a new license or
    collaboration (or similar) with the counterparty, dated in or near the
    filing period. Grants, payments, updates, options, consents and assignments
    that are not tied to that statement are not enough.

    Merger: entered into an Agreement and Plan of Merger or Business Combination
    Agreement with that counterparty, dated in the period. A merger never
    inherits another instrument's date.

    Acquisition: the merger instruments above, a purchase agreement, a
    commenced tender offer, or will/agreed-to acquire that counterparty.
    """
    if not filing_text or not counterparty:
        return False

    for m in _ENTERED_NEW_AGREEMENT_RE.finditer(filing_text):
        title = _extend_entered_into_title(m.group(1), filing_text, m.end())
        sentence = _sentence_at(filing_text, m.start())
        if not _party_tied_to_instrument(title, sentence, filing_text, counterparty):
            continue
        dated = _date_on_entered_into_sentence(title, sentence)
        if not _date_in_or_near_period(dated, reference_date):
            continue
        if deal_type == DealType.LICENSE_COLLABORATION and _title_is_new_license_or_collab(title):
            return True
        if deal_type == DealType.MERGER and _title_is_merger_instrument(title):
            return True
        if deal_type == DealType.ACQUISITION and _title_is_acquisition_instrument(title):
            return True

    if deal_type == DealType.LICENSE_COLLABORATION and _dated_grant_is_new_license(
        filing_text, counterparty, reference_date, type_quote,
    ):
        return True

    if deal_type == DealType.ACQUISITION:
        for m in _ACQUIRE_POSITIVE_RE.finditer(filing_text):
            sentence = _sentence_at(filing_text, m.start())
            if _sentence_names_party(sentence, counterparty, filing_text):
                dated = _date_on_entered_into_sentence('', sentence)
                if _date_in_or_near_period(dated, reference_date):
                    return True
    return False


def out_of_scope_deal_reason(
    type_quote: str,
    filing_text: str,
    filing_date: str = '',
    event_date: str = '',
) -> str | None:
    """Return a drop reason if the quote's filing context is out of scope.

    Scope is only (a) a newly entered license/collaboration and (b) a
    definitive acquisition or merger of a company. Wrapper and historical
    words apply only to the grant's source agreement, not to passing
    mentions of older pacts, milestones, or joinders.
    """
    if not type_quote:
        return 'empty type_quote'
    passage = _containing_passage(type_quote, filing_text) or type_quote
    reference_date = event_date or filing_date

    if _grant_refers_to_amended_agreement(type_quote, filing_text):
        return 'amendment'
    if _new_agreements_are_only_amendments_or_settlements(filing_text):
        return 'amendment/settlement only'
    if is_historical_agreement(type_quote, reference_date, filing_text):
        return 'historical agreement'
    if is_termination_or_assignment_language(type_quote) or is_termination_or_assignment_language(passage):
        return 'termination/assignment'
    if is_divestiture_or_asset_sale(type_quote) or is_divestiture_or_asset_sale(passage):
        return 'divestiture/asset sale'

    pos = find_quote_position(type_quote, filing_text)
    if pos is not None:
        item = _find_item_section(filing_text, pos)
        if item and item.replace(' ', '') in ('item1.02', 'item2.03'):
            return f'out-of-scope item ({item})'
    return None


# =============================================================================
# TITLE TEMPLATES (NO MODEL FREE TEXT)
# =============================================================================

def build_deal_title(
    filer: str,
    counterparty: str,
    deal_type: DealType,
    filer_role: str,
    headline_amount: str | None
) -> str:
    """Build deal title from verified fields only using code templates.
    
    Templates:
    - '{甲方}收购{乙方}（{金额}）'
    - '{支付方}向{收款方}支付 {金额} 买断里程碑与分成义务'
    - '{借款方}与{出借方}签署最高 {额度} 贷款协议（已提取 {drawn}）'
    """
    amount_suffix = f"（{headline_amount}）" if headline_amount else ""
    
    if deal_type == DealType.ACQUISITION:
        if filer_role == 'acquirer':
            return f"{filer}收购{counterparty}{amount_suffix}"
        else:
            return f"{counterparty}收购{filer}{amount_suffix}"
    
    elif deal_type == DealType.MERGER:
        return f"{filer}与{counterparty}合并{amount_suffix}"
    
    elif deal_type == DealType.LICENSE_COLLABORATION:
        return f"{filer}与{counterparty}授权合作{amount_suffix}"
    
    elif deal_type == DealType.EQUITY_FINANCING:
        # Direction depends on role - filer could be investor or investee
        if filer_role == 'investor':
            return f"{filer}投资{counterparty}{amount_suffix}"
        else:
            return f"{filer}获{counterparty}投资{amount_suffix}"
    
    elif deal_type == DealType.DEBT_FACILITY:
        return f"{filer}与{counterparty}签署贷款协议{amount_suffix}"
    
    elif deal_type == DealType.OBLIGATION_BUYOUT:
        if filer_role == 'payer':
            return f"{filer}向{counterparty}支付{headline_amount or ''}买断里程碑与分成义务"
        else:
            return f"{counterparty}向{filer}支付{headline_amount or ''}买断里程碑与分成义务"
    
    return f"{filer}与{counterparty}交易{amount_suffix}"


def build_deal_lines(
    deal_type: DealType,
    amounts: list[ParsedAmount],
    filer_role: str
) -> list[str]:
    """Build deal detail lines from verified amounts.
    
    Rules:
    - '首付' only for kind=upfront whose quote contains 'upfront'
    - Shares can be shown only if quoted
    - Amount labels must match deal type (no 收购对价 for license deals)
    """
    lines = []
    
    for amount in amounts:
        rendered = render_amount_chinese(amount)
        
        if amount.kind == AmountKind.UPFRONT:
            # For buyouts, use "买断金额" instead of upfront labels
            if deal_type == DealType.OBLIGATION_BUYOUT:
                lines.append(f"买断金额：{rendered}")
            elif 'upfront' in amount.raw_quote.lower():
                lines.append(f"首付：{rendered}")
            else:
                lines.append(f"初期付款：{rendered}")
        
        elif amount.kind == AmountKind.PURCHASE_PRICE:
            # Use appropriate label based on deal type
            if deal_type in (DealType.ACQUISITION, DealType.MERGER):
                lines.append(f"收购对价：{rendered}")
            elif deal_type == DealType.OBLIGATION_BUYOUT:
                lines.append(f"买断金额：{rendered}")
            else:
                # For license/other deals, use generic payment label
                lines.append(f"付款金额：{rendered}")
        
        elif amount.kind == AmountKind.MILESTONES_TOTAL:
            lines.append(f"里程碑：{rendered}")
        
        elif amount.kind == AmountKind.EQUITY:
            if amount.currency == 'SHARES':
                lines.append(f"股份：{rendered}")
            else:
                lines.append(f"股权：{rendered}")
        
        elif amount.kind == AmountKind.FACILITY_SIZE:
            # Facility size label only for debt deals
            if deal_type == DealType.DEBT_FACILITY:
                lines.append(f"贷款额度：{rendered}")
            else:
                # For non-debt deals, conditional amounts go to milestones
                lines.append(f"里程碑：{rendered}")
        
        elif amount.kind == AmountKind.DRAWN:
            # Drawn label only for debt deals
            if deal_type == DealType.DEBT_FACILITY:
                lines.append(f"已提取：{rendered}")
            else:
                lines.append(f"付款金额：{rendered}")
        
        else:
            lines.append(rendered)
    
    return lines


# =============================================================================
# PRECISION-FIRST VERIFICATION HELPERS
# =============================================================================

def has_license_grant_language(type_quote: str) -> bool:
    """Check if type_quote contains license/collaboration grant language.

    Accepts ordinary SEC phrasing such as 'granted X an exclusive worldwide
    license'. Rejects the quote itself when it is an amendment, termination,
    assignment or waiver — those are out of scope even if they grant rights.
    """
    if not type_quote:
        return False
    text = type_quote.lower()

    if is_amendment_language(type_quote) or re.search(
        r'\b(?:terminat(?:e|ed|es|ing|ion)'
        r'|assign(?:s|ed|ment)'
        r'|divest(?:iture|ed|s)?'
        r'|transfer(?:red|s)?'
        r'|waiv(?:e|ed|er|es|ing))\b',
        text,
    ):
        return False

    grant_patterns = [
        r'\bgrants?\s+.{0,80}?\blicen[sc]e\b',
        r'\bgranted\s+.{0,80}?\blicen[sc]e\b',
        r'\bgranting\s+.{0,80}?\blicen[sc]e\b',
        r'\bgrants?\s+.{0,80}?\brights\b',
        r'\bgranted\s+.{0,80}?\brights\b',
        r'\bgranting\s+.{0,80}?\brights\b',
        r'\bgranted\s+.{0,80}?\ban\s+exclusive\s+(?:worldwide\s+)?licen[sc]e\b',
        r'\bexclusive(?:ly)?(?:\s+\w+){0,4}\s+licen[sc]e\b',
        r'\bnon-exclusive(?:ly)?(?:\s+\w+){0,4}\s+licen[sc]e\b',
        r'\bcollaboration\s+(?:and\s+license\s+)?agreement\b',
        r'\blicen[sc]e\s+(?:and\s+collaboration\s+)?agreement\b',
        r'\bentere[ds]\s+into\s+(?:a\s+)?(?:\w+\s+){0,5}(?:license|collaboration)\b',
    ]
    return any(re.search(p, text) for p in grant_patterns)


def has_acquisition_language(type_quote: str) -> bool:
    """Check if type_quote contains acquisition/merger of a company.

    Business-unit sales, assignments and amendments are rejected here.
    """
    if not type_quote:
        return False
    text = type_quote.lower()

    if re.search(
        r'\b(?:amend(?:ment|ed|s)?'
        r'|terminat(?:e|ed|es|ing|ion)'
        r'|assign(?:s|ed|ment)'
        r'|waiv(?:e|ed|er|es|ing))\b',
        text,
    ):
        return False
    if is_divestiture_or_asset_sale(type_quote):
        return False

    acq_patterns = [
        r'\bacquire[sd]?\b',
        r'\bacquisition\b',
        r'\bmerger?\b',
        r'\bmerge[sd]?\b',
        r'\bpurchase\s+(?:of|all)\s+(?:the\s+)?(?:outstanding\s+)?(?:shares|stock|equity)\b',
        r'\bpurchase\s+agreement\b.*\b(?:shares|stock|equity|company)\b',
    ]
    return any(re.search(p, text) for p in acq_patterns)


def has_role_keyword_for_kind(quote: str, kind: AmountKind) -> bool:
    """Check if amount quote contains required role keyword for its kind.
    
    Each amount kind must have supporting role language in its quote:
    - UPFRONT: 'upfront' or 'one-time' or 'signing' or 'initial'
    - PURCHASE_PRICE: 'purchase price' or 'per share' or 'consideration' or
                      'acquire...for $X' or 'for $X billion/million'
    - MILESTONES_TOTAL: 'milestone' or 'aggregate' or 'up to' or 'maximum' or 'potential'
    """
    if not quote:
        return False
    text = quote.lower()
    
    if kind == AmountKind.UPFRONT:
        return bool(re.search(
            r'\b(upfront|up-front|upon\s+signing|at\s+closing|signing)\b', text
        ))
    elif kind == AmountKind.PURCHASE_PRICE:
        # Explicit price keywords
        if re.search(r'\b(purchase\s+price|per\s+share|consideration|merger\s+consideration)\b', text):
            return True
        # "acquire...for $X" pattern (common in acquisition announcements)
        if re.search(r'\bacquire\b.*\bfor\s+\$[\d,.]+\s*(?:billion|million|b(?:n)?|m(?:n)?)\b', text):
            return True
        # "for $X billion/million" with dollar amount explicitly (implicit consideration)
        if re.search(r'\bfor\s+\$[\d,.]+\s*(?:billion|million)\b', text):
            return True
        return False
    elif kind == AmountKind.MILESTONES_TOTAL:
        return bool(re.search(r'\b(milestone|aggregate|up\s+to|maximum|potential)\b', text))
    else:
        # Out of scope kinds never have valid role keywords
        return False


_LICENSE_EQUITY_RE = re.compile(
    r'\b(?:private\s+placement|equity\s+(?:investment|financing|line)|'
    r'stock\s+purchase|'
    r'(?:purchas(?:e|ed|es|ing)|issu(?:e|ed|ance)|subscri(?:be|ption))\s+'
    r'(?:of\s+|for\s+)?(?:[\d,.]+\s+)?'
    r'(?:shares?|common\s+stock|preferred\s+stock|equity)|'
    r'share\s+price|price\s+per\s+share|\bPIPE\b)\b',
    re.IGNORECASE,
)


def is_equity_amount_quote(quote: str, deal_type: DealType | None) -> bool:
    """Stock / private-placement amounts are never licence deal payments."""
    if not quote or deal_type != DealType.LICENSE_COLLABORATION:
        return False
    return bool(_LICENSE_EQUITY_RE.search(quote))


_MILESTONE_NOT_A_MILESTONE_RE = re.compile(
    r'\b(?:reimburs(?:e|ement|ed)|cost\s+caps?|cost[\s-]shar(?:e|ing)|'
    r'shar(?:e|ing)\s+costs?|'
    r'(?:r(?:and|&)\s*d|research(?:\s+and\s+development)?)\s+funding|'
    r'development\s+(?:cost|funding|support)s?)\b',
    re.IGNORECASE,
)
_MILESTONE_INCLUDES_UPFRONT_RE = re.compile(
    r'\b(?:includ(?:e|es|ing|ed)\s+(?:the\s+)?(?:\$[\d,.]+\s+\w+\s+)?'
    r'(?:up-?front|signing(?:\s+payment)?)|'
    r'(?:total|aggregate).{0,50}includ(?:e|es|ing).{0,40}(?:up-?front|signing))\b',
    re.IGNORECASE,
)


def is_valid_milestone_amount(quote: str) -> bool:
    """Milestones only: no reimbursement, cost cap/share, R&D funding, or combined totals."""
    if not quote:
        return False
    if _MILESTONE_NOT_A_MILESTONE_RE.search(quote):
        return False
    if _MILESTONE_INCLUDES_UPFRONT_RE.search(quote):
        return False
    return True


_UPFRONT_TIMING_RE = re.compile(
    r'\b(?:up-?front|upon\s+(?:the\s+)?'
    r'(?:signing|execution|closing|effective\s+date)|'
    r'at\s+(?:the\s+)?closing|'
    r'in\s+connection\s+with\s+(?:the\s+)?'
    r'(?:signing|execution|closing|entry\s+into))\b',
    re.IGNORECASE,
)
_UPFRONT_LATER_EVENT_RE = re.compile(
    r'\b(?:anniversary|milestone|'
    r'year\s+(?:two|three|2|3)|'
    r'(?:second|third|fourth|\d+(?:st|nd|rd|th))\s+(?:anniversary|year)|'
    r'upon\s+(?:the\s+)?(?:achievement|approval|first\s+commercial)|'
    r'within\s+\d+\s+years?|'
    r'(?:due|payable)\s+(?:on|after)\b)',
    re.IGNORECASE,
)
_UPFRONT_REIMBURSE_RE = re.compile(
    r'\b(?:reimburs(?:e|ement|ed)|development\s+costs?|'
    r'research\s+(?:funding|support)|funding)\b',
    re.IGNORECASE,
)


def _party_is(name: str, filer: str, counterparty: str, raw: str) -> str | None:
    """Return 'filer', 'counterparty', 'licensor_role', or None."""
    if not raw:
        return None
    t = raw.lower()
    if re.search(r'\b(?:the\s+)?licensors?\b|\blicensor\(s\)\b', t):
        return 'licensor_role'
    if re.search(r'\bthe\s+company\b', t) or re.search(r'\bthe\s+registrant\b', t):
        return 'filer'
    if filer and (match_company_whole_word(filer, raw) or match_company_whole_word(raw, filer)):
        return 'filer'
    if counterparty and (
        match_company_whole_word(counterparty, raw)
        or match_company_whole_word(raw, counterparty)
    ):
        return 'counterparty'
    # Short defined name: "Genentech" vs "Genentech, Inc."
    if counterparty:
        short = normalize_company_name(counterparty).lower()
        raw_n = normalize_company_name(raw).lower()
        if short and (short == raw_n or short.startswith(raw_n + ' ') or raw_n.startswith(short)):
            if len(raw_n) >= 3:
                return 'counterparty'
    return None


def _extract_upfront_payer_payee(
    quote: str, filer: str, counterparty: str
) -> tuple[str | None, str | None]:
    """Return ('filer'|'counterparty'|None, same) for payer and payee."""
    if not quote:
        return None, None
    patterns = [
        # X will receive ... from Y
        (r'(.+?)\s+(?:will\s+|shall\s+)?receiv(?:e|es|ed)\b.{0,80}?\bfrom\s+(.+?)(?:\s+of\b|\s+a\b|\s+an\b|\$|\.|$)',
         'payee', 'payer'),
        # X will pay Y / X will make a payment to Y
        (r'(.+?)\s+(?:will\s+|shall\s+)?(?:pay|make\s+(?:an?\s+)?(?:up-?front\s+)?payment\s+to)\s+(.+?)(?:\s+of\b|\s+a\b|\$|\.|$)',
         'payer', 'payee'),
        # payment to Y from X
        (r'payment\s+to\s+(.+?)\s+from\s+(.+?)(?:\s+of\b|\$|\.|$)',
         'payee', 'payer'),
        # payment from X to Y
        (r'payment\s+from\s+(.+?)\s+to\s+(.+?)(?:\s+of\b|\$|\.|$)',
         'payer', 'payee'),
        # paid by X to Y
        (r'paid\s+by\s+(.+?)\s+to\s+(.+?)(?:\s+of\b|\$|\.|$)',
         'payer', 'payee'),
        # the Company paid X
        (r'(.+?)\s+paid\s+(.+?)(?:\s+a\b|\s+an\b|\s+\$|$)',
         'payer', 'payee'),
        # will make an upfront cash payment of $X to Y
        (r'(.+?)\s+(?:will\s+|shall\s+)?make\s+an?\s+(?:up-?front\s+)?'
         r'(?:cash\s+)?payment\s+(?:of\s+\$[\d,.]+\s+\w+\s+)?to\s+(.+?)(?:\s+of\b|\$|\.|$)',
         'payer', 'payee'),
    ]
    for pat, role_a, role_b in patterns:
        m = re.search(pat, quote, re.IGNORECASE)
        if not m:
            continue
        a = _party_is(filer, filer, counterparty, m.group(1))
        b = _party_is(filer, filer, counterparty, m.group(2))
        roles = {role_a: a, role_b: b}
        return roles.get('payer'), roles.get('payee')
    return None, None


def _explicit_grant_licensor_side(
    type_quote: str, amount_quote: str, filer: str, counterparty: str
) -> str | None:
    """Who granted the rights, from explicit grant grammar only. None if unknown."""
    blob = f"{type_quote or ''}\n{amount_quote or ''}"
    if not blob.strip():
        return None
    noun = _filer_noun_pattern(filer) if filer else r'the\s+Company'
    # Passive first: "is granted" is the licensee, not the grantor.
    if re.search(rf'{noun}\s+is\s+granted\b', blob, re.IGNORECASE):
        return 'counterparty'
    if re.search(rf'{noun}\s+(?:is\s+granting|grants|granted)\b', blob, re.IGNORECASE):
        return 'filer'
    if counterparty and re.search(
        rf'{re.escape(counterparty)}\s+(?:is\s+)?grant(?:s|ed|ing)\b',
        blob, re.IGNORECASE,
    ):
        return 'counterparty'
    if re.search(
        rf'\bgrant(?:s|ed|ing)\s+(?:to\s+)?(?:the\s+Company|{re.escape(filer) if filer else "the Company"})\b',
        blob, re.IGNORECASE,
    ):
        return 'counterparty'
    # Passive: the Company is granted … → filer is the licensee
    if re.search(rf'{noun}\s+is\s+granted\b', blob, re.IGNORECASE):
        return 'counterparty'
    return None


def is_valid_upfront_amount(
    quote: str,
    type_quote: str,
    filer: str,
    counterparty: str,
    filer_role: str | None,
) -> bool:
    """Upfront must have signing/closing timing AND licensee→licensor direction.

    'One-time' alone is not enough when the payment is due on a later event.
    Reimbursement / funding / development costs are never the upfront.
    Unclear timing or payer → reject (omit the field).
    Grant direction is taken only from explicit 'grants' wording, never from
    a generic 'entered into … Agreement with X' role assignment.
    """
    if not quote:
        return False
    if _UPFRONT_REIMBURSE_RE.search(quote):
        return False
    has_timing = bool(_UPFRONT_TIMING_RE.search(quote))
    has_later = bool(_UPFRONT_LATER_EVENT_RE.search(quote))
    if has_later and not has_timing:
        return False
    if not has_timing:
        return False

    license_like = (
        filer_role in ('licensor', 'licensee', 'filer_is_licensor', 'license_party')
        or has_license_grant_language(type_quote or '')
    )
    if not license_like:
        return True

    payer, payee = _extract_upfront_payer_payee(quote, filer, counterparty)
    if payer is None and payee is None:
        return False

    licensor_side = _explicit_grant_licensor_side(
        type_quote, quote, filer, counterparty
    )
    if payer == 'licensor_role':
        payer = licensor_side
    if payee == 'licensor_role':
        payee = licensor_side
    if licensor_side is None:
        return True
    licensee_side = 'counterparty' if licensor_side == 'filer' else 'filer'
    if payer == licensor_side:
        return False
    if payee == licensee_side:
        return False
    if payer and payer != licensee_side:
        return False
    if payee and payee != licensor_side:
        return False
    return True


def verify_defined_term_in_type_quote(counterparty: str, type_quote: str, filing_text: str) -> bool:
    """Verify counterparty appears in type_quote or as defined term in parties clause.
    
    Returns True if:
    1. counterparty name appears in type_quote directly, OR
    2. counterparty is a defined term in a parties/defined-term clause that
       also appears in type_quote (e.g., 'the "Seller"' defined as Company X)
    """
    if not counterparty or not type_quote:
        return False
    
    # Direct check
    if match_company_whole_word(counterparty, type_quote):
        return True

    term_map = parse_defined_terms(filing_text)
    for term, names in term_map.items():
        if not _name_matches_any(counterparty, names):
            continue
        if re.search(rf'\b{re.escape(term)}\b', type_quote, re.IGNORECASE):
            return True
    
    return False


# =============================================================================
# MAIN DEAL PROCESSING
# =============================================================================

def process_sec_deal(
    filing_text: str,
    filer_name: str,
    filing_url: str,
    filing_date: str,
    event_date: str | None,
    claude_response: dict
) -> dict | None:
    """Process a single SEC filing to extract and verify a deal.
    
    This is the main entry point. Returns a verified deal dict or None.
    
    Args:
        filing_text: Full text of the SEC filing
        filer_name: Company name from EDGAR metadata (always one party)
        filing_url: URL of the filing
        filing_date: Filing date (ISO format)
        event_date: Event date from cover page (ISO format) or None
        claude_response: Claude's tool response with deal_type, quotes, amounts
    
    Returns:
        Verified deal dict ready for output, or None if verification fails.
    """
    if not claude_response:
        logging.debug("No Claude response")
        return None
    
    deal_type_str = claude_response.get('deal_type', 'none')
    if deal_type_str == 'none':
        logging.debug("Claude returned deal_type=none")
        return None
    
    try:
        deal_type = DealType(deal_type_str)
    except ValueError:
        logging.warning("Invalid deal_type: %s", deal_type_str)
        return None
    
    # PRECISION-FIRST: Narrow scope check - only publish license/collab and acquisition/merger
    if deal_type not in IN_SCOPE_DEAL_TYPES:
        logging.info("Deal out_of_scope: deal_type=%s is not in-scope (only license_collaboration, acquisition, merger)", deal_type_str)
        return None
    
    counterparty = claude_response.get('counterparty_name', '').strip()
    type_quote = claude_response.get('type_quote', '').strip()
    counterparty_quote = claude_response.get('counterparty_quote', '').strip()
    raw_amounts = claude_response.get('amounts', [])
    
    # Verification 1: type_quote must exist in filing
    if not type_quote:
        logging.info("Deal dropped: no type_quote")
        return None
    if not verify_quote_in_filing(type_quote, filing_text):
        logging.info("Deal dropped: type_quote not found in filing")
        return None
    
    # Verification 1a: Check for non-deal agreements (services, lease, etc.)
    if is_non_deal_agreement(type_quote):
        logging.info("Deal dropped: non-deal agreement type (services/lease/employment): %s", type_quote[:80])
        return None
    
    # Verification 1b: historical / amendment / termination / assignment / divestiture
    # Look at the quote AND the filing sentence it came from — the model may
    # excerpt only the grant/acquire clause of an out-of-scope event.
    scope_reason = out_of_scope_deal_reason(
        type_quote, filing_text, filing_date=filing_date, event_date=event_date,
    )
    if scope_reason:
        logging.info("Deal out_of_scope: %s (%s)", scope_reason, type_quote[:80])
        return None
    
    # PRECISION-FIRST: Verify deal-type-specific language in type_quote
    # License/collaboration must have grant language
    if deal_type == DealType.LICENSE_COLLABORATION:
        if not has_license_grant_language(type_quote):
            logging.info("Deal dropped: license/collaboration missing grant language in type_quote: %s", type_quote[:80])
            return None
    
    # Acquisition/merger must have acquire/merge language
    if deal_type in (DealType.ACQUISITION, DealType.MERGER):
        if not has_acquisition_language(type_quote):
            logging.info("Deal dropped: acquisition/merger missing acquire/merge language in type_quote: %s", type_quote[:80])
            return None
    
    # Verification 1c: Validate deal type against type_quote content
    validated_type, type_was_corrected = validate_deal_type_from_quote(deal_type, type_quote)
    if validated_type is None:
        logging.info("Deal dropped: could not validate deal type from type_quote")
        return None
    deal_type = validated_type
    
    # Re-verify scope after type correction
    if deal_type not in IN_SCOPE_DEAL_TYPES:
        logging.info("Deal out_of_scope after type correction: deal_type=%s", deal_type.value)
        return None
    
    # Verification 2: counterparty_quote must exist in filing
    if not counterparty_quote:
        logging.info("Deal dropped: no counterparty_quote")
        return None
    if not verify_quote_in_filing(counterparty_quote, filing_text):
        logging.info("Deal dropped: counterparty_quote not found in filing")
        return None
    
    # Verification 3: counterparty must appear in both quotes (with defined term resolution)
    if not counterparty:
        logging.info("Deal dropped: no counterparty name")
        return None
    if not verify_counterparty_in_quotes(counterparty, counterparty_quote, type_quote, filing_text):
        logging.info("Deal dropped: counterparty '%s' not verified in quotes", counterparty)
        return None

    term_map = parse_defined_terms(f"{type_quote}\n{filing_text}")
    if _defined_roles_conflict(term_map, filer_name):
        logging.info("Deal dropped: defined-term roles conflict or Parent is filer/target")
        return None

    role_key = re.sub(r'^the\s+', '', counterparty.strip().lower())
    if role_key in _PARENT_ROLE_TERMS:
        parent = _parent_from_defined_terms(term_map)
        if not parent or _is_bare_role_word(parent):
            logging.info("Deal dropped: Parent role '%s' has no legal name", counterparty)
            return None
        if match_company_whole_word(parent, filer_name) or match_company_whole_word(filer_name, parent):
            logging.info("Deal dropped: Parent resolves to filer")
            return None
        counterparty = parent

    resolved_cp = resolve_merger_vehicle(
        counterparty, filing_text, type_quote, deal_type=deal_type,
    )
    still_vehicle = (
        resolved_cp is None
        or is_merger_or_tender_vehicle(resolved_cp, filing_text, type_quote, deal_type)
    )
    if still_vehicle and is_merger_or_tender_vehicle(
        counterparty, filing_text, type_quote, deal_type
    ):
        logging.info("Deal dropped: merger vehicle '%s' has no named parent", counterparty)
        return None
    if resolved_cp is None:
        logging.info("Deal dropped: merger vehicle '%s' has no named parent", counterparty)
        return None
    if resolved_cp != counterparty:
        if not match_company_whole_word(resolved_cp, filing_text):
            logging.info("Deal dropped: vehicle parent '%s' not in filing", resolved_cp)
            return None
        logging.info("Resolved merger vehicle '%s' to parent '%s'", counterparty, resolved_cp)
        counterparty = resolved_cp
    if _is_bare_role_word(counterparty):
        logging.info("Deal dropped: counterparty is a bare role word '%s'", counterparty)
        return None
    if _is_unpublishable_party_name(counterparty) or _name_has_governance_leak(counterparty):
        written = counterparty
        cleaned = _fallback_clean_party_name(counterparty)
        if cleaned and _name_appears_verbatim(cleaned, filing_text) and not _is_unpublishable_party_name(cleaned):
            logging.info("Cleaned counterparty '%s' → '%s'", written, cleaned)
            counterparty = cleaned
        elif (
            written
            and _name_appears_verbatim(written, filing_text)
            and not _is_unpublishable_party_name(written)
        ):
            counterparty = written
        else:
            logging.info("Deal dropped: unpublishable counterparty '%s'", written)
            return None
    if not _name_appears_verbatim(counterparty, filing_text):
        logging.info("Deal dropped: counterparty '%s' is not a verbatim party name", counterparty)
        return None
    if not is_plausible_party_name(counterparty, filing_text):
        logging.info("Deal dropped: counterparty '%s' is not a company name", counterparty)
        return None
    
    # Verification 3b: counterparty must NOT equal filer (self-deal check)
    filer_normalized = normalize_company_name(filer_name).lower()
    counterparty_normalized = normalize_company_name(counterparty).lower()
    if filer_normalized == counterparty_normalized or filer_normalized in counterparty_normalized or counterparty_normalized in filer_normalized:
        logging.info("Deal dropped: counterparty '%s' same as filer '%s'", counterparty, filer_name)
        return None

    if not has_affirmative_new_agreement(
        deal_type,
        filing_text,
        counterparty,
        reference_date=event_date or filing_date,
        type_quote=type_quote,
    ):
        logging.info(
            "Deal dropped: no affirmative entered-into new agreement for %s with %s",
            deal_type.value, counterparty,
        )
        return None
    
    # Verification 4: filer must be a party (from EDGAR metadata)
    # The filer is always one party - check it appears in the filing
    if not match_company_whole_word(filer_name, filing_text):
        logging.info("Deal dropped: filer '%s' not found in filing", filer_name)
        return None
    
    # Verification 5: detect role from type_quote
    role_info = detect_role_from_quote(
        type_quote, filer_name, counterparty, deal_type=deal_type, filing_text=filing_text,
    )
    if not role_info:
        logging.info("Deal dropped: could not determine filer role from type_quote")
        return None
    
    filer_role = role_info['filer_role']
    
    # Verification 5b: X1v2 - For equity_financing, validate investment direction strictly
    # Only emit "X获Y投资" when grammar explicitly shows Y investing in X
    if deal_type == DealType.EQUITY_FINANCING:
        is_valid, drop_reason = validate_investment_direction(type_quote, filer_name, counterparty, filer_role)
        if not is_valid:
            logging.info("Deal dropped: investment direction validation failed: %s", drop_reason)
            return None
    
    # Verification 6: nonprofit/government check
    if detect_nonprofit_or_government(filing_text):
        # Check if the nonprofit language is in the deal-relevant passages
        # We're conservative: if detected, drop
        logging.info("Deal dropped: nonprofit/government/consortium detected")
        return None
    
    # Process and verify amounts
    verified_amounts: list[ParsedAmount] = []
    
    for raw_amount in raw_amounts:
        kind_str = raw_amount.get('kind', 'other')
        quote = raw_amount.get('quote', '').strip()
        
        if not quote:
            logging.debug("Amount dropped: no quote")
            continue
        
        # PRECISION-FIRST: Parse and check amount kind scope
        try:
            kind = AmountKind(kind_str)
        except ValueError:
            kind = AmountKind.OTHER
        
        # Only process in-scope amount kinds
        if kind not in IN_SCOPE_AMOUNT_KINDS:
            logging.debug("Amount dropped: kind=%s is out of scope (only upfront, purchase_price, milestones_total)", kind_str)
            continue
        
        # PRECISION-FIRST: Amount quote must contain role keyword for its kind
        if not has_role_keyword_for_kind(quote, kind):
            logging.debug("Amount dropped: quote missing role keyword for kind=%s: %s", kind_str, quote[:80])
            continue
        if is_equity_amount_quote(quote, deal_type):
            logging.info("Amount dropped: equity/stock amount is not a deal payment: %s", quote[:80])
            continue
        if kind == AmountKind.UPFRONT and not is_valid_upfront_amount(
            quote, type_quote, filer_name, counterparty, filer_role
        ):
            logging.info("Amount dropped: upfront lacks timing or licensee→licensor payer: %s", quote[:80])
            continue
        if kind == AmountKind.MILESTONES_TOTAL and not is_valid_milestone_amount(quote):
            logging.info("Amount dropped: not a pure milestone (reimbursement/combined total): %s", quote[:80])
            continue
        if deal_type == DealType.LICENSE_COLLABORATION and kind == AmountKind.PURCHASE_PRICE:
            logging.info("Amount dropped: purchase_price is not a licence payment: %s", quote[:80])
            continue
        
        # Verification: quote must be in filing
        if not verify_quote_in_filing(quote, filing_text):
            logging.debug("Amount dropped: quote not found in filing: %s", quote[:50])
            continue
        
        # Verification: amount quote must describe a transaction WITH the counterparty
        # The quote itself must either name the counterparty, or NOT name any other company
        # This prevents accepting quotes like "Alector will pay Spur $15M" for a Genentech deal
        counterparty_in_quote = match_company_whole_word(counterparty, quote)
        
        # GENERAL RULE: Check if quote describes payment to/from a THIRD PARTY
        # The quote must tie the amount to the stated counterparty, not another company.
        different_company_in_quote = False
        if not counterparty_in_quote:
            # General pattern: "pay/paid [Company] $X" where Company is not the counterparty
            # This catches any case where a payment recipient is explicitly named and differs
            payment_recipient_pattern = r'\b(?:pay|paid|pays|paying)\s+([A-Z][A-Za-z]+(?:\s+[A-Z][A-Za-z]+){0,3})\s+(?:a\s+)?(?:\$|one-time|upfront|an?\s+(?:upfront|one-time))'
            for m in re.finditer(payment_recipient_pattern, quote, re.IGNORECASE):
                other_name = m.group(1).strip()
                # Skip common non-company words
                skip_words = {'the', 'a', 'an', 'under', 'for', 'in', 'of', 'company', 'cash', 'and',
                             'time', 'party', 'parties', 'date', 'term', 'amount', 'agreement'}
                if other_name.lower() in skip_words:
                    continue
                # Skip if it matches the counterparty (including partial match)
                if match_company_whole_word(counterparty, other_name):
                    continue
                # Skip if this is the filer name
                if match_company_whole_word(filer_name, other_name):
                    continue
                # This looks like a different company receiving the payment
                logging.debug("Amount dropped: quote describes payment to third party '%s' (counterparty is '%s'): %s",
                             other_name, counterparty, quote[:80])
                different_company_in_quote = True
                break
        
        if different_company_in_quote:
            continue
        
        # GENERAL RULE: Reject amounts from a DIFFERENT company's transaction
        # Pattern: "from/of [Company] acquisition/deal/transaction/merger/payment"
        # This catches exhibit tables that list amounts from unrelated deals
        if not counterparty_in_quote:
            different_transaction_pattern = r'(?:from|of)\s+(?:the\s+)?([A-Z][A-Za-z]+(?:\s+[A-Z][A-Za-z]+){0,3})\s+(?:acquisition|deal|transaction|merger|payment)\b'
            for m in re.finditer(different_transaction_pattern, quote, re.IGNORECASE):
                other_name = m.group(1).strip()
                skip_words = {'the', 'a', 'an', 'this', 'that', 'our', 'their', 'company', 'said', 'such'}
                if other_name.lower() in skip_words:
                    continue
                # If this mentions a different company's transaction, reject
                if not match_company_whole_word(counterparty, other_name) and not match_company_whole_word(filer_name, other_name):
                    logging.debug("Amount dropped: quote describes different company's transaction '%s' (counterparty is '%s'): %s",
                                 other_name, counterparty, quote[:80])
                    different_company_in_quote = True
                    break
            if different_company_in_quote:
                continue
        
        # GENERAL RULE: Reject balance sheet / financial statement items
        # These are not contractual deal terms, they're accounting line items.
        # Patterns: "Total assets", "cash and cash equivalents", "receivable" with balance context
        quote_lower = quote.lower()
        is_accounting_item = False
        
        # Balance sheet line items (not deal terms)
        balance_sheet_patterns = [
            r'\bcash\s+and\s+cash\s+equivalents\b',
            r'\btotal\s+(?:assets|liabilities|equity)\b',
            r'\bnet\s+(?:assets|income|loss)\b',
            r'\b(?:accounts?\s+)?receivable\s+(?:from|as\s+of)\b',
            r'\bpro[\s-]?forma\b',
        ]
        for pattern in balance_sheet_patterns:
            if re.search(pattern, quote_lower):
                logging.debug("Amount dropped: balance sheet/accounting item: %s", quote[:80])
                is_accounting_item = True
                break
        if is_accounting_item:
            continue
        
        # Check if the amount quote's paragraph contains the counterparty name
        counterparty_in_same_para = False
        if not counterparty_in_quote:
            # Find the paragraph containing this amount quote
            paragraphs = split_into_paragraphs(filing_text)
            norm_quote = normalize_whitespace(quote).lower()
            for _, _, para_text in paragraphs:
                norm_para = normalize_whitespace(para_text).lower()
                if norm_quote in norm_para:
                    # Found the paragraph - check if counterparty is in it
                    if match_company_whole_word(counterparty, para_text):
                        counterparty_in_same_para = True
                    break
        
        # GENERAL RULE: Amount must be tied to the counterparty in the SAME context
        # Either the quote itself names the counterparty, or the same paragraph does.
        if not counterparty_in_quote and not counterparty_in_same_para:
            logging.debug("Amount dropped: not in same paragraph as counterparty: %s", quote[:50])
            continue
        
        # Additional check: amount quote should be contextually near the type_quote
        # This prevents picking up amounts from unrelated transactions in the same filing
        if type_quote:
            type_pos = find_quote_position(type_quote, filing_text)
            amount_pos = find_quote_position(quote, filing_text)
            if type_pos is not None and amount_pos is not None:
                distance = abs(amount_pos - type_pos)
                # If amount is very far from type_quote (>10000 chars), drop it
                if distance > 10000:
                    logging.debug("Amount dropped: too far from type_quote (%d chars): %s", distance, quote[:50])
                    continue
        
        # Parse amount from quote (kind already parsed above)
        parsed = parse_amount_from_quote(quote, kind)
        if parsed:
            verified_amounts.append(parsed)
        else:
            logging.debug("Amount dropped: could not parse amount from: %s", quote[:50])
    
    # Headline ONLY from a verified non-conditional upfront or purchase price.
    # No fallback to milestones or any other amount.
    headline_amount = None
    for amount in verified_amounts:
        if amount.up_to:
            continue
        if amount.kind in (AmountKind.PURCHASE_PRICE, AmountKind.UPFRONT):
            headline_amount = render_amount_chinese(amount)
            break
    
    # Build title and lines (no model free text)
    # Always show verified amounts regardless of how type was determined
    title = build_deal_title(filer_name, counterparty, deal_type, filer_role, headline_amount)
    detail_lines = build_deal_lines(deal_type, verified_amounts, filer_role)
    
    # Build output dict - only in-scope deal types
    deal_kinds_map = {
        DealType.ACQUISITION: ['acq'],
        DealType.MERGER: ['acq'],
        DealType.LICENSE_COLLABORATION: ['lic'],
    }
    
    # Use event_date if available, fallback to filing_date
    display_date = event_date[:7] if event_date else (filing_date[:7] if filing_date else '')
    
    output_money = headline_amount or ''
    
    return {
        'url': filing_url,
        'title': title,
        'company': filer_name,
        'counterparty': counterparty,
        'kinds': deal_kinds_map.get(deal_type, ['lic']),
        'money': output_money,
        'structure': ' | '.join(detail_lines) if detail_lines else '',
        'why': '',  # No model free text
        'source_name': 'SEC EDGAR',
        'date': display_date,
        'amount_source': 'filing',
        'is_filing': True,
        'filing_source': 'sec',
        'verified_amounts': [
            {
                'kind': a.kind.value,
                'value_millions': a.value_in_millions,
                'currency': a.currency,
                'up_to': a.up_to,
                'rendered': render_amount_chinese(a)
            }
            for a in verified_amounts
        ],
        'deal_type': deal_type.value,
        'filer_role': filer_role,
        'type_quote': type_quote,
    }


def _select_relevant_filing_sections(filing_text: str, max_chars: int = 40000) -> str:
    """Select relevant sections from SEC filing instead of blind truncation.
    
    SEC 8-K filings have structure:
    - Cover page (important)
    - Item X.XX sections (most important - contain the deal announcement)
    - Exhibits (EX-99.X press releases, EX-10.X agreements)
    
    This function extracts the most relevant portions while staying under max_chars.
    """
    if len(filing_text) <= max_chars:
        return filing_text
    
    # Try to find and extract key sections
    sections = []
    
    # 1. Extract cover/header (first ~3000 chars usually has date, company, item list)
    sections.append(filing_text[:3000])
    
    # 2. Look for Item sections (Item 1.01, Item 2.01, etc.)
    item_pattern = r'(Item\s+\d+\.\d+[^\n]*\n(?:.*?\n){0,50})'
    item_matches = re.findall(item_pattern, filing_text, re.IGNORECASE | re.DOTALL)
    for match in item_matches[:5]:  # Take up to 5 item sections
        if len(match) > 100:  # Skip tiny matches
            sections.append(match[:5000])  # Limit each item section
    
    # 3. Look for Exhibit descriptions or press releases
    exhibit_pattern = r'((?:Exhibit|EX-)\s*(?:99|10)\.\d.*?(?=Exhibit|EX-|$))'
    exhibit_matches = re.findall(exhibit_pattern, filing_text, re.IGNORECASE | re.DOTALL)
    for match in exhibit_matches[:3]:  # Take up to 3 exhibits
        if len(match) > 200:
            sections.append(match[:8000])  # Exhibits can be longer
    
    # 4. Look for specific deal keywords and extract surrounding context
    deal_keywords = [
        r'(?:License|Collaboration|Credit|Loan|Amendment|Agreement).*?Agreement',
        r'\$[\d,]+(?:\.\d+)?\s*(?:million|billion)',
        r'upfront\s+(?:payment|fee)',
        r'milestone\s+payment',
    ]
    
    for keyword_pattern in deal_keywords:
        for match in re.finditer(keyword_pattern, filing_text, re.IGNORECASE):
            start = max(0, match.start() - 500)
            end = min(len(filing_text), match.end() + 500)
            context = filing_text[start:end]
            if context not in ''.join(sections):
                sections.append(f"[...]{context}[...]")
    
    # Join sections and trim to max_chars
    result = '\n\n---\n\n'.join(sections)
    
    if len(result) > max_chars:
        result = result[:max_chars] + "\n[... truncated ...]"
    
    return result


def extract_deals_from_filings(
    filings: list[dict],
    claude_client: Any = None,
    max_deals: int = 6
) -> tuple[list[dict], int]:
    """Extract deals from SEC filings using Claude with strict verification.
    
    This is the main entry point for the production pipeline.
    
    Args:
        filings: List of filing dicts with 'filing_text', 'company', 'url', 'date', 'event_date'
        claude_client: Anthropic client (will create if None)
        max_deals: Maximum number of deals to return
    
    Returns:
        Tuple of (list of verified deal dicts, count of failed filings).
    """
    if not filings:
        return [], 0
    
    if claude_client is None:
        from anthropic import Anthropic
        claude_client = Anthropic()
    
    deals = []
    failed_count = 0
    
    for filing in filings:
        filing_url = filing.get('url', 'unknown')
        filing_text = filing.get('filing_text', '')
        if not filing_text or len(filing_text) < 100:
            continue
        
        filer_name = filing.get('company', '')
        if not filer_name:
            continue
        
        event_date = filing.get('event_date')
        
        # Event date required - if missing, skip
        if not event_date:
            logging.info("Filing skipped: no event date: %s", filing_url)
            continue
        
        # Call Claude with strict tool schema
        try:
            model = os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-5-5")
            
            # Select relevant sections from filing instead of truncating
            # Focus on: Item sections, exhibits, and first portion
            relevant_text = _select_relevant_filing_sections(filing_text)
            
            prompt = f"""Analyze this SEC filing and extract deal information.

CRITICAL: All quotes MUST be EXACT verbatim text from the filing - no paraphrasing, no rewording, no additions.

The filing company is: {filer_name}

Filing text:
{relevant_text}

If there is a significant business deal (acquisition, merger, license, collaboration, financing, loan, or buyout):
1. Identify the deal type
2. Identify the counterparty (the other company, not {filer_name})
3. Extract EXACT quotes from the filing for:
   - type_quote: a sentence describing the deal type (must contain counterparty name)
   - counterparty_quote: a sentence containing the counterparty name
   - amounts: each with kind and EXACT quote containing the dollar amount

If there is no significant deal, return deal_type="none".

IMPORTANT: Every quote must be an EXACT substring of the filing text above. Do not paraphrase or modify quotes."""

            # Use tool_choice="auto" which is compatible with all Claude models
            # Then validate the response contains the expected tool call
            # Retry logic for text-only responses
            max_retries = 2
            claude_response = None
            
            for attempt in range(max_retries):
                message = claude_client.messages.create(
                    model=model,
                    max_tokens=8000,  # Raised from 2000 for longer responses
                    tools=[DEAL_EXTRACTION_SCHEMA],
                    tool_choice={"type": "auto"},
                    messages=[{"role": "user", "content": prompt}],
                )
                
                # Check stop_reason
                stop_reason = message.stop_reason
                if stop_reason == "max_tokens":
                    logging.warning("Filing %s: Claude response truncated (max_tokens), dropping this filing", filing_url)
                    # If truncated, we can't trust the response - treat as no deal
                    failed_count += 1
                    break
                
                # Extract tool response
                for block in message.content:
                    if block.type == "tool_use" and block.name == "extract_deal":
                        claude_response = block.input
                        break
                
                if claude_response:
                    break
                
                # Text-only response - retry once with stronger instruction
                if attempt == 0:
                    logging.info("Filing %s: Claude returned text-only, retrying with tool emphasis", filing_url)
                    prompt = prompt.replace(
                        "If there is no significant deal",
                        "You MUST use the extract_deal tool to respond. If there is no significant deal"
                    )
                else:
                    logging.warning("Filing %s: Claude did not use extract_deal tool after retry", filing_url)
                    failed_count += 1
            
            if not claude_response:
                continue
            
            deal = process_sec_deal(
                filing_text=filing_text,
                filer_name=filer_name,
                filing_url=filing_url,
                filing_date=filing.get('date', ''),
                event_date=event_date,
                claude_response=claude_response
            )
            
            if deal:
                # Run independent verification (second Claude call)
                verified_deal = verify_deal_with_claude(
                    deal=deal,
                    filing_text=filing_text,
                    claude_client=claude_client
                )
                
                if verified_deal:
                    deals.append(verified_deal)
                    logging.info("Deal extracted: %s", verified_deal['title'])
                else:
                    logging.info("Deal dropped by verifier: %s", deal['title'])
                    failed_count += 1
                
                if len(deals) >= max_deals:
                    break
        
        except Exception as e:
            logging.error("Error processing filing %s: %s: %s", 
                         filing_url, type(e).__name__, e)
            failed_count += 1
            continue
    
    if failed_count > 0:
        logging.warning("SEC deal extraction: %d filings failed out of %d processed", 
                       failed_count, len(filings))
    
    return deals, failed_count


# =============================================================================
# NEWS / ACADEMIC TEXT STRIPPING
# =============================================================================

def split_into_sentences(text: str) -> list[tuple[int, int, str]]:
    """Split text into sentences for unverified number stripping.
    
    Split on '。！？' and '. ' followed by space/capital.
    NEVER split on decimal points (e.g., '$35.0 million').
    
    Returns list of (start, end, sentence) tuples where start/end are positions
    in the ORIGINAL text.
    """
    if not text:
        return []
    
    sentences = []
    
    # Pattern for sentence boundaries:
    # - Chinese: 。！？
    # - English: '. ' followed by uppercase or end
    # But NOT: decimal points like '$35.0' or '1.5 billion'
    
    # Find all sentence-ending punctuation, excluding decimal points
    # We look for: 。！？ OR (period followed by space and uppercase/end)
    # We explicitly check that period is NOT preceded by a digit
    
    last_end = 0
    i = 0
    while i < len(text):
        char = text[i]
        
        # Chinese sentence enders
        if char in '。！？':
            sentence = text[last_end:i+1].strip()
            if sentence:
                sentences.append((last_end, i+1, sentence))
            last_end = i + 1
            i += 1
            continue
        
        # English period - check it's not a decimal
        if char == '.':
            # Check if this is a decimal: digit before and digit after
            is_decimal = False
            if i > 0 and i < len(text) - 1:
                if text[i-1].isdigit() and text[i+1].isdigit():
                    is_decimal = True
            
            if not is_decimal:
                # Check if followed by space and uppercase (or end of text)
                rest = text[i+1:]
                if not rest or (rest[0].isspace() and len(rest) > 1 and 
                               (rest.lstrip() and rest.lstrip()[0].isupper())):
                    sentence = text[last_end:i+1].strip()
                    if sentence:
                        sentences.append((last_end, i+1, sentence))
                    last_end = i + 1
        
        i += 1
    
    # Don't forget the last sentence
    if last_end < len(text):
        sentence = text[last_end:].strip()
        if sentence:
            sentences.append((last_end, len(text), sentence))
    
    return sentences


def extract_numbers_from_text(text: str) -> set[str]:
    """Extract QUANTITY numbers from text for verification.
    
    This should only extract numbers that represent amounts, quantities, or measurements.
    It should NOT extract Chinese numerals that are part of common words/idioms.
    
    Chinese numerals to IGNORE (not quantity numbers):
    - 一种 (a kind of), 一些 (some), 一定 (certain), 一般 (general)
    - 进一步 (further), 一步 (one step as idiom)
    - 一项 (an item), 一组 (a group), 一次 (once)
    - 两者 (both), 两方 (both parties)
    - 第一 (first), 第二 (second), etc. - ordinals
    
    Chinese numerals to EXTRACT (quantity numbers):
    - X 亿美元, X 万美元 (amounts)
    - X 例患者, X 名患者 (patient counts)
    - X% (percentages)
    - 三期 (phase 3) when followed by 试验/临床 - but this is ALLOWED in clinical text
    """
    numbers = set()

    def _add_exact(num: str) -> None:
        """Record a quantity exactly — never the truncated integer of a decimal."""
        if num and num != '.':
            numbers.add(num)

    # Arabic numerals with optional decimal. 11.7 is not 11; 4.125 is not 4.
    for m in re.finditer(r'[\d,]+(?:\.\d+)?', text):
        _add_exact(m.group().replace(',', ''))

    # Unit conversion must be EXACT, no rounding:
    # $1.17 billion = 11.7 亿 (NOT 11 or 12)
    # $412.5 million = 4.125 亿 (NOT 4)
    billion_pattern = r'\$?([\d,.]+)\s*billion'
    for m in re.finditer(billion_pattern, text, re.IGNORECASE):
        num_str = m.group(1).replace(',', '')
        try:
            num = float(num_str)
            converted = num * 10
            if converted == int(converted):
                _add_exact(str(int(converted)))
                numbers.add(f"{int(converted)}亿")
            else:
                _add_exact(str(converted))
                numbers.add(f"{converted}亿")
            _add_exact(num_str)
        except ValueError:
            pass

    million_pattern = r'\$?([\d,.]+)\s*million'
    for m in re.finditer(million_pattern, text, re.IGNORECASE):
        num_str = m.group(1).replace(',', '')
        try:
            num = float(num_str)
            converted_yi = num / 100
            if converted_yi >= 1:
                if converted_yi == int(converted_yi):
                    _add_exact(str(int(converted_yi)))
                    numbers.add(f"{int(converted_yi)}亿")
                else:
                    _add_exact(str(converted_yi))
                    numbers.add(f"{converted_yi}亿")
            _add_exact(num_str)
        except ValueError:
            pass

    def _after_decimal_point(start: int) -> bool:
        prefix = text[max(0, start - 8):start]
        return bool(re.search(r'点[零一二三四五六七八九十\d]*$', prefix))

    def _fmt_num(parsed: float) -> str:
        if parsed == int(parsed):
            return str(int(parsed))
        return str(parsed)

    def _record_chinese(cn_num: str) -> None:
        parsed = chinese_to_number(cn_num)
        if parsed is None:
            return
        numbers.add(_fmt_num(parsed))
        numbers.add(cn_num)
        # Keep the coefficient of a trailing 万/亿 so "三亿美元" still
        # records 三 (used when matching Chinese source text).
        if cn_num.endswith(('亿', '万')) and len(cn_num) > 1:
            numbers.add(cn_num[:-1])
        # 十二亿 = 1.2e9 = 12 亿. Record the 亿-scale token so
        # $1.2 billion (12亿) matches by value.
        if '亿' in cn_num and parsed >= 1e6:
            yi = parsed / 1e8
            numbers.add(f"{_fmt_num(yi)}亿")
        elif parsed >= 1e8:
            yi = parsed / 1e8
            numbers.add(f"{_fmt_num(yi)}亿")

    # Patient / person counts: 一百二十名 = 120, 三十五名 = 35
    cn_patient_count_pattern = r'([零一二三四五六七八九十百千两〇]+)\s*(?:名|例|位|人)(?:\s*(?:患者|受试者|病人|对照))?'
    for m in re.finditer(cn_patient_count_pattern, text):
        _record_chinese(m.group(1))

    # 四家中心 / 三家医院 — classifiers that are real counts (not 一种/一项 idioms).
    # Skip 一/一家 which is the idiomatic "a/an".
    cn_jia_pattern = r'([二三四五六七八九十百千两〇][零一二三四五六七八九十百千两〇]*)\s*家'
    for m in re.finditer(cn_jia_pattern, text):
        _record_chinese(m.group(1))

    # "X组" only when it's a large explicit count (三组 or more)
    cn_group_count_pattern = r'([三四五六七八九十百千〇]+[零一二三四五六七八九十百千两〇]*)\s*组'
    for m in re.finditer(cn_group_count_pattern, text):
        _record_chinese(m.group(1))

    # Mixed Arabic + 亿/万 + currency (11.7 亿美元). Keep the exact decimal.
    mixed_currency_pattern = r'([\d,.]+)\s*(亿|万)\s*(?:美元|欧元|英镑|元|人民币|港币|日元)?'
    for m in re.finditer(mixed_currency_pattern, text):
        num = m.group(1).replace(',', '')
        unit = m.group(2)
        numbers.add(f"{num}{unit}")
        _add_exact(num)

    # Chinese decimal amounts: 一点五亿, 十二亿, 两千万
    cn_decimal_amount = r'([零一二三四五六七八九十百千两〇]*点[零一二三四五六七八九十]+)\s*(亿|万)?'
    for m in re.finditer(cn_decimal_amount, text):
        parsed = chinese_to_number(m.group(1))
        if parsed is None:
            continue
        numbers.add(str(parsed))
        numbers.add(m.group(1))
        if m.group(2):
            numbers.add(f"{parsed}{m.group(2)}")

    # Pure Chinese format: 三亿美元 / 十二亿美元
    cn_currency_pattern = r'([零一二三四五六七八九十百千万亿两〇]+)\s*(?:亿|万|百|千)?\s*(?:美元|欧元|英镑|元|人民币|港币|日元)'
    for m in re.finditer(cn_currency_pattern, text):
        cn_num = m.group(1)
        if cn_num in '亿万百千':
            continue
        if _after_decimal_point(m.start()):
            continue
        _record_chinese(cn_num)

    # Large standalone Chinese numbers (市场规模两百亿, 十二亿, 两千万).
    # Do not take a prefix of a longer numeral: 一百 inside 一百二十 is not 100.
    # Do not take 五亿 out of 一点五亿.
    cn_large_pattern = (
        r'([零一二三四五六七八九十两〇][零一二三四五六七八九十百千万亿两〇]*[百千万亿])'
        r'(?![零一二三四五六七八九十百千万亿两〇])'
    )
    for m in re.finditer(cn_large_pattern, text):
        if _after_decimal_point(m.start()):
            continue
        _record_chinese(m.group(1))

    # Counts with 个 (七个国家). Skip 一个 — idiomatic "a/an".
    cn_ge_pattern = r'([二三四五六七八九十百千两〇][零一二三四五六七八九十百千两〇]*)\s*个'
    for m in re.finditer(cn_ge_pattern, text):
        _record_chinese(m.group(1))

    # Fractions: N分之M (三分之二 = 2/3). Record both parts and the ratio.
    # Do not treat 百分之N as a fraction.
    cn_fraction_pattern = (
        r'(?!百分之)([零一二三四五六七八九十千两〇\d]+)\s*分之\s*'
        r'([零一二三四五六七八九十百千两〇\d]+)'
    )
    for m in re.finditer(cn_fraction_pattern, text):
        denom = chinese_to_number(m.group(1))
        numer = chinese_to_number(m.group(2))
        if denom is None:
            try:
                denom = float(m.group(1))
            except ValueError:
                denom = None
        if numer is None:
            try:
                numer = float(m.group(2))
            except ValueError:
                numer = None
        if denom is not None:
            numbers.add(_fmt_num(denom))
        if numer is not None:
            numbers.add(_fmt_num(numer))
        if denom and numer is not None:
            numbers.add(f"{_fmt_num(numer)}/{_fmt_num(denom)}")

    # 百分之N → N%
    cn_percent_pattern = (
        r'百分之\s*([零一二三四五六七八九十百千两〇\d]+(?:点[零一二三四五六七八九十]+)?)'
    )
    for m in re.finditer(cn_percent_pattern, text):
        parsed = chinese_to_number(m.group(1))
        if parsed is None:
            try:
                parsed = float(m.group(1))
            except ValueError:
                continue
        numbers.add(f"{_fmt_num(parsed)}%")
        numbers.add(_fmt_num(parsed))

    # Ordinals with a unit: 第N周 / 第N天. Bare 第一 is idiomatic and skipped.
    cn_ordinal_pattern = (
        r'第\s*([零一二三四五六七八九十百千两〇\d]+)\s*'
        r'(?:周|天|日|期|轮|年|个月|月)'
    )
    for m in re.finditer(cn_ordinal_pattern, text):
        parsed = chinese_to_number(m.group(1))
        if parsed is None:
            try:
                parsed = float(m.group(1))
            except ValueError:
                continue
        numbers.add(_fmt_num(parsed))
    
    # 成: 三成 = 30%. Do not record the bare coefficient (三 ≠ 30).
    cn_cheng_pattern = r'([零一二三四五六七八九十两〇\d]+)\s*成'
    for m in re.finditer(cn_cheng_pattern, text):
        parsed = chinese_to_number(m.group(1))
        if parsed is None:
            try:
                parsed = float(m.group(1))
            except ValueError:
                continue
        if parsed <= 0:
            continue
        pct = parsed * 10
        numbers.add(f"{_fmt_num(pct)}%")
        numbers.add(_fmt_num(pct))
        numbers.add(f"{m.group(1)}成")

    # 倍: 两倍 = 2x / 2-fold. Do not treat 一倍 as a real multiple.
    cn_bei_pattern = r'([二三四五六七八九十两〇][零一二三四五六七八九十两〇]*|\d+)\s*倍'
    for m in re.finditer(cn_bei_pattern, text):
        parsed = chinese_to_number(m.group(1))
        if parsed is None:
            try:
                parsed = float(m.group(1))
            except ValueError:
                continue
        numbers.add(_fmt_num(parsed))
        numbers.add(f"{_fmt_num(parsed)}x")
        numbers.add(f"{m.group(1)}倍")

    # Percentages
    for m in re.finditer(r'\d+(?:\.\d+)?%', text):
        numbers.add(m.group())

    # English fractions 2/3
    for m in re.finditer(r'(\d+)\s*/\s*(\d+)', text):
        numbers.add(f"{m.group(1)}/{m.group(2)}")
        _add_exact(m.group(1))
        _add_exact(m.group(2))

    # English multiples: 2-fold, twofold, 2x, twice
    for m in re.finditer(r'(\d+(?:\.\d+)?)\s*[-]?\s*fold\b', text, re.IGNORECASE):
        _add_exact(m.group(1))
    for m in re.finditer(r'(\d+(?:\.\d+)?)\s*x\b', text, re.IGNORECASE):
        _add_exact(m.group(1))
    if re.search(r'\btwice\b', text, re.IGNORECASE):
        numbers.add('2')
    
    return numbers


def _as_plain_number(token: str) -> float | None:
    if not token:
        return None
    try:
        return float(token.replace(',', '').replace('%', ''))
    except ValueError:
        return chinese_to_number(token)


def _quantity_value(token: str) -> float | None:
    """Numeric magnitude of a token. 十二亿 and 12亿 are 1.2e9; 30% is 30."""
    if not token:
        return None
    if token.endswith('%'):
        return _as_plain_number(token[:-1])
    if '/' in token and not token.startswith('/'):
        parts = token.split('/', 1)
        try:
            return float(parts[0]) / float(parts[1])
        except (ValueError, ZeroDivisionError):
            return None
    if token.endswith('亿'):
        coeff = _as_plain_number(token[:-1])
        return None if coeff is None else coeff * 1e8
    if token.endswith('万'):
        coeff = _as_plain_number(token[:-1])
        return None if coeff is None else coeff * 1e4
    if token.endswith('成'):
        coeff = _as_plain_number(token[:-1])
        return None if coeff is None else coeff * 10
    if token.endswith('倍') or token.endswith('x'):
        return _as_plain_number(token[:-1])
    return _as_plain_number(token)


def _is_yi_scale_token(token: str) -> bool:
    """True for 亿-denominated amounts or raw values >= 1e8 (e.g. 1200000000)."""
    if not token:
        return False
    if '亿' in token:
        return True
    val = _as_plain_number(token)
    return val is not None and val >= 1e8


def _values_equal(a: float, b: float) -> bool:
    scale = max(abs(a), abs(b), 1.0)
    return abs(a - b) / scale < 1e-6


def strip_unverified_numbers_from_text(
    text: str,
    source_text: str
) -> str:
    """Remove sentences containing numbers not present in source.
    
    This runs on news/academic text in production.
    Removes whole sentences (split correctly) that contain numbers not in source.
    
    Note: Positions returned by split_into_sentences are in the original text.
    """
    if not text or not source_text:
        return text
    
    source_numbers = extract_numbers_from_text(source_text)
    sentences = split_into_sentences(text)
    
    if not sentences:
        return text
    
    def _number_supported(token: str) -> bool:
        """A token is supported if it, or the quantity it denotes, is in the source.

        一百二十 (120) is honest when the English source has 120 patients.
        四家 is invented when the source has no 4. Never treat a truncated
        scale (11 for 11.7) as a match — that token is simply absent.
        亿 amounts compare by VALUE: 十二亿美元 == $1.2B, 一点五亿美元 == $150M.
        """
        if token in source_numbers:
            return True
        token_val = _quantity_value(token)
        if token_val is None:
            parsed = chinese_to_number(token)
            if parsed is None:
                return False
            if parsed == int(parsed):
                return str(int(parsed)) in source_numbers
            return str(parsed) in source_numbers
        if _is_yi_scale_token(token):
            for src in source_numbers:
                if not _is_yi_scale_token(src):
                    continue
                src_val = _quantity_value(src)
                if src_val is not None and _values_equal(token_val, src_val):
                    return True
            return False
        if str(int(token_val)) == str(token_val) or token_val == int(token_val):
            if str(int(token_val)) in source_numbers:
                return True
        if str(token_val) in source_numbers:
            return True
        parsed = chinese_to_number(token)
        if parsed is not None:
            if parsed == int(parsed) and str(int(parsed)) in source_numbers:
                return True
            if str(parsed) in source_numbers:
                return True
        return False

    kept_sentences = []
    
    for start, end, sentence in sentences:
        sentence_numbers = extract_numbers_from_text(sentence)
        unverified = {n for n in sentence_numbers if not _number_supported(n)}
        
        if unverified:
            logging.debug("Stripping sentence with unverified numbers %s: %s", 
                         unverified, sentence[:50])
        else:
            kept_sentences.append(sentence)
    
    if not kept_sentences:
        return ''
    
    # Join sentences with space
    result = ' '.join(kept_sentences)
    
    # Clean up any double spaces or orphaned punctuation
    result = re.sub(r'\s+', ' ', result)
    result = re.sub(r'^\s*[,，;；]\s*', '', result)
    
    return result.strip()


# =============================================================================
# TESTING UTILITIES
# =============================================================================

def run_tests() -> bool:
    """Run all unit tests for sec_deals module."""
    all_passed = True
    
    all_passed &= _test_quote_verification()
    all_passed &= _test_company_matching()
    all_passed &= _test_role_detection()
    all_passed &= _test_amount_parsing()
    all_passed &= _test_amount_rendering()
    all_passed &= _test_paragraph_splitting()
    all_passed &= _test_sentence_splitting()
    all_passed &= _test_nonprofit_detection()
    
    return all_passed


def _test_quote_verification():
    """Test quote verification functions."""
    print("Testing quote verification...")
    
    filing = "The Company entered into a license agreement with Genentech, Inc. on October 1, 2026. Under the terms, Genentech will pay $100 million upfront."
    
    tests = [
        # Exact match
        ("entered into a license agreement with Genentech", True),
        # Whitespace normalized
        ("entered   into  a license   agreement with Genentech", True),
        # Case insensitive
        ("ENTERED INTO A LICENSE AGREEMENT WITH GENENTECH", True),
        # Not in filing
        ("entered into an acquisition agreement with Pfizer", False),
        # Partial match
        ("license agreement", True),
    ]
    
    passed = 0
    for quote, expected in tests:
        result = verify_quote_in_filing(quote, filing)
        if result == expected:
            passed += 1
        else:
            print(f"  FAIL: verify_quote_in_filing({quote!r}) = {result}, expected {expected}")
    
    print(f"  quote verification: {passed}/{len(tests)} passed")
    return passed == len(tests)


def _test_company_matching():
    """Test company name matching."""
    print("Testing company matching...")
    
    tests = [
        # Exact whole-word match
        ("Genentech", "agreement with Genentech, Inc.", True),
        # With legal suffix
        ("Genentech, Inc.", "agreement with Genentech", True),
        # Alias match
        ("BMS", "Bristol-Myers Squibb announced", True),
        ("Bristol-Myers Squibb", "BMS will pay", True),
        # Should NOT match (no alias)
        ("AstraZeneca", "Amazon Web Services", False),
        ("Roche", "agreement with Genentech", False),  # No Roche↔Genentech alias
        ("Sanofi", "agreement with Regeneron", False),  # No Sanofi↔Regeneron alias
        # Substring should not match
        ("Gen", "agreement with Genentech", False),  # substring
        ("tech", "Genentech announced", False),  # substring
    ]
    
    passed = 0
    for name, text, expected in tests:
        result = match_company_whole_word(name, text)
        if result == expected:
            passed += 1
        else:
            print(f"  FAIL: match_company_whole_word({name!r}, {text!r}) = {result}, expected {expected}")
    
    print(f"  company matching: {passed}/{len(tests)} passed")
    return passed == len(tests)


def _test_role_detection():
    """Test role/direction detection from quotes."""
    print("Testing role detection...")
    
    tests = [
        # Acquisition
        ("Merck will acquire Verona Pharma", "Merck", "Verona", "acquirer"),
        ("acquisition of Verona by Merck", "Merck", "Verona", "acquirer"),
        # License
        ("Alector granted Genentech an exclusive license", "Alector", "Genentech", "licensor"),
        ("Genentech entered into a license agreement with Alector", "Genentech", "Alector", "licensor"),
        # Buyout
        ("Immunome paid Bristol-Myers Squibb", "Immunome", "Bristol-Myers Squibb", "payer"),
        # Credit
        ("Rocket entered into a credit agreement with Hercules", "Rocket", "Hercules", "borrower"),
    ]
    
    passed = 0
    for quote, filer, counterparty, expected_role in tests:
        result = detect_role_from_quote(quote, filer, counterparty)
        if result and result.get('filer_role') == expected_role:
            passed += 1
        else:
            actual = result.get('filer_role') if result else None
            print(f"  FAIL: detect_role({quote!r}) filer_role = {actual}, expected {expected_role}")
    
    print(f"  role detection: {passed}/{len(tests)} passed")
    return passed == len(tests)


def _test_amount_parsing():
    """Test amount parsing from quotes."""
    print("Testing amount parsing...")
    
    tests = [
        # Basic USD amounts
        ("$35.0 million", AmountKind.UPFRONT, 35.0, 'USD', False),
        ("$100 million upfront", AmountKind.UPFRONT, 100.0, 'USD', False),
        ("up to $1.5 billion", AmountKind.FACILITY_SIZE, 1500.0, 'USD', True),
        ("$20.0 million", AmountKind.PURCHASE_PRICE, 20.0, 'USD', False),
        ("up to $150.0 million", AmountKind.FACILITY_SIZE, 150.0, 'USD', True),
        # Conditional words
        ("may receive up to $50 million", AmountKind.OTHER, None, None, None),  # Should fail for OTHER
        ("may receive up to $50 million", AmountKind.MILESTONES_TOTAL, 50.0, 'USD', True),  # OK for milestones
        # GBP
        ("£800 million", AmountKind.PURCHASE_PRICE, 800.0, 'GBP', False),
        # Shares
        ("4,425,487 shares", AmountKind.EQUITY, 4425487.0, 'SHARES', False),
    ]
    
    passed = 0
    for quote, kind, expected_millions, expected_currency, expected_up_to in tests:
        result = parse_amount_from_quote(quote, kind)
        
        if expected_millions is None:
            if result is None:
                passed += 1
            else:
                print(f"  FAIL: parse({quote!r}, {kind}) should be None, got {result.value_in_millions}")
        elif result and abs(result.value_in_millions - expected_millions) < 0.01 and result.currency == expected_currency:
            if result.up_to == expected_up_to:
                passed += 1
            else:
                print(f"  FAIL: parse({quote!r}) up_to = {result.up_to}, expected {expected_up_to}")
        else:
            actual = result.value_in_millions if result else None
            print(f"  FAIL: parse({quote!r}) = {actual}, expected {expected_millions}")
    
    print(f"  amount parsing: {passed}/{len(tests)} passed")
    return passed == len(tests)


def _test_amount_rendering():
    """Test Chinese amount rendering."""
    print("Testing amount rendering...")
    
    tests = [
        # $35.0 million → 3,500 万美元
        (ParsedAmount(35.0, 1, 'USD', False, AmountKind.UPFRONT, ""), "3,500 万美元"),
        # up to $1.5 billion → 最高 15 亿美元
        (ParsedAmount(1.5, 1000, 'USD', True, AmountKind.FACILITY_SIZE, ""), "最高 15 亿美元"),
        # $20.0 million → 2,000 万美元
        (ParsedAmount(20.0, 1, 'USD', False, AmountKind.PURCHASE_PRICE, ""), "2,000 万美元"),
        # $100 million → 1 亿美元
        (ParsedAmount(100.0, 1, 'USD', False, AmountKind.UPFRONT, ""), "1 亿美元"),
        # $1.17 billion → 11.7 亿美元
        (ParsedAmount(1.17, 1000, 'USD', False, AmountKind.MILESTONES_TOTAL, ""), "11.7 亿美元"),
    ]
    
    passed = 0
    for parsed, expected in tests:
        result = render_amount_chinese(parsed)
        if result == expected:
            passed += 1
        else:
            print(f"  FAIL: render({parsed.value_in_millions}M, up_to={parsed.up_to}) = {result!r}, expected {expected!r}")
    
    print(f"  amount rendering: {passed}/{len(tests)} passed")
    return passed == len(tests)


def _test_paragraph_splitting():
    """Test paragraph splitting for same-passage check."""
    print("Testing paragraph splitting...")
    
    text = """First paragraph with some text.

Second paragraph with $100 million.

Third paragraph with Genentech."""
    
    paragraphs = split_into_paragraphs(text)
    
    tests = [
        (len(paragraphs) == 3, f"Expected 3 paragraphs, got {len(paragraphs)}"),
        ("$100 million" in paragraphs[1][2] if len(paragraphs) > 1 else False, "$100M should be in second paragraph"),
        ("Genentech" in paragraphs[2][2] if len(paragraphs) > 2 else False, "Genentech should be in third paragraph"),
    ]
    
    passed = sum(1 for test, _ in tests if test)
    for test, msg in tests:
        if not test:
            print(f"  FAIL: {msg}")
    
    print(f"  paragraph splitting: {passed}/{len(tests)} passed")
    return passed == len(tests)


def _test_sentence_splitting():
    """Test sentence splitting for number stripping."""
    print("Testing sentence splitting...")
    
    # Key test: don't split on decimal points
    text = "Rocket received $35.0 million. This is tranche 1-A."
    sentences = split_into_sentences(text)
    
    tests = [
        (len(sentences) == 2, f"Expected 2 sentences, got {len(sentences)}"),
        ("$35.0 million" in sentences[0][2] if sentences else False, "$35.0 should not be split"),
    ]
    
    # Chinese sentence splitting
    text_cn = "收购金额为3,500万美元。这是首笔交易。"
    sentences_cn = split_into_sentences(text_cn)
    tests.append((len(sentences_cn) == 2, f"Expected 2 Chinese sentences, got {len(sentences_cn)}"))
    
    passed = sum(1 for test, _ in tests if test)
    for test, msg in tests:
        if not test:
            print(f"  FAIL: {msg}")
    
    print(f"  sentence splitting: {passed}/{len(tests)} passed")
    return passed == len(tests)


def _test_nonprofit_detection():
    """Test nonprofit/government detection."""
    print("Testing nonprofit detection...")
    
    tests = [
        ("funded by the National Institutes of Health", True),
        ("a nonprofit foundation", True),
        ("government agencies", True),
        ("consortium of investors", True),
        ("Genentech paid $100 million", False),
        ("license agreement with BMS", False),
    ]
    
    passed = 0
    for text, expected in tests:
        result = detect_nonprofit_or_government(text)
        if result == expected:
            passed += 1
        else:
            print(f"  FAIL: detect_nonprofit({text!r}) = {result}, expected {expected}")
    
    print(f"  nonprofit detection: {passed}/{len(tests)} passed")
    return passed == len(tests)


if __name__ == "__main__":
    import os
    logging.basicConfig(level=logging.INFO)
    
    if run_tests():
        print("\nAll sec_deals tests passed!")
        raise SystemExit(0)
    else:
        print("\nSome tests failed.")
        raise SystemExit(1)
