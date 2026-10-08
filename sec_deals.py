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
import re
from dataclasses import dataclass, field
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
    UPFRONT = "upfront"
    PURCHASE_PRICE = "purchase_price"
    MILESTONES_TOTAL = "milestones_total"
    EQUITY = "equity"
    FACILITY_SIZE = "facility_size"
    DRAWN = "drawn"
    OTHER = "other"


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

# Tool schema for Claude - strict quote-based extraction
DEAL_EXTRACTION_SCHEMA = {
    "name": "extract_deal",
    "description": "Extract deal information from SEC filing with exact quotes from the filing text",
    "input_schema": {
        "type": "object",
        "properties": {
            "deal_type": {
                "type": "string",
                "enum": ["acquisition", "merger", "license_collaboration", 
                         "equity_financing", "debt_facility", "obligation_buyout", "none"],
                "description": "Type of deal, or 'none' if no deal in this filing"
            },
            "counterparty_name": {
                "type": "string",
                "description": "Name of the counterparty (the filer is always one party)"
            },
            "type_quote": {
                "type": "string",
                "description": "Exact verbatim quote from filing describing the deal type (must contain counterparty name)"
            },
            "counterparty_quote": {
                "type": "string",
                "description": "Exact verbatim quote from filing containing counterparty name"
            },
            "amounts": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "kind": {
                            "type": "string",
                            "enum": ["upfront", "purchase_price", "milestones_total", 
                                     "equity", "facility_size", "drawn", "other"]
                        },
                        "quote": {
                            "type": "string",
                            "description": "Exact verbatim quote containing the amount"
                        }
                    },
                    "required": ["kind", "quote"]
                },
                "description": "Amount quotes, each with kind and exact verbatim quote"
            }
        },
        "required": ["deal_type"]
    }
}


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
# QUOTE VERIFICATION
# =============================================================================

def normalize_whitespace(text: str) -> str:
    """Normalize whitespace for quote matching."""
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


def verify_counterparty_in_quotes(
    counterparty: str, 
    counterparty_quote: str, 
    type_quote: str
) -> bool:
    """Verify counterparty name appears in both counterparty_quote and type_quote."""
    if not counterparty:
        return False
    
    # Counterparty must be in counterparty_quote
    if not counterparty_quote:
        return False
    if not match_company_whole_word(counterparty, counterparty_quote):
        return False
    
    # Counterparty must also be in type_quote
    if not type_quote:
        return False
    if not match_company_whole_word(counterparty, type_quote):
        return False
    
    return True


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
    
    # Buyout / payment patterns - "the Company paid X" (before license to catch buyouts)
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
    
    # License patterns - "the Company granted X a license"
    (r'(the\s+Company|[\w\s&,\.]+?)\s+grant(?:s|ed)\s+([\w\s&,\.\"\(\)]+?)\s+(?:an?\s+)?(?:exclusive[,\s]+)?(?:worldwide\s+)?licen[sc]e',
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


def detect_role_from_quote(type_quote: str, filer: str, counterparty: str) -> dict | None:
    """Detect role/direction from type_quote using fixed pattern set.
    
    Returns dict with 'filer_role' and optionally 'direction' for payment flows.
    Returns None if role cannot be determined (deal should be dropped).
    
    Note: "the Company" in SEC filings always refers to the filer.
    """
    if not type_quote:
        return None
    
    text = type_quote.strip()
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
                
                # "the Company" always refers to the filer
                party1_is_filer = (
                    'the company' in party1_raw.lower() or
                    filer_normalized in party1 or 
                    party1 in filer_normalized or
                    _check_alias_match(filer, party1)
                )
                
                party2_is_filer = party2 and (
                    'the company' in party2_raw.lower() or
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
    - Currency: US$/$/USD = USD. GBP/EUR parsed, CNY ignored.
    """
    if not quote:
        return None
    
    text = quote.lower()
    up_to = False
    
    # Check for 'up to' / 'maximum' / 'aggregate'
    if re.search(r'\b(?:up\s+to|maximum|aggregate)\b', text):
        up_to = True
    
    # Check conditional words for non-milestones/facility
    if has_conditional_words(quote):
        if kind not in (AmountKind.MILESTONES_TOTAL, AmountKind.FACILITY_SIZE):
            logging.debug("Amount dropped: conditional words in non-milestone amount: %s", quote[:50])
            return None
        up_to = True
    
    # Pattern: $X.X million/billion (most common)
    match = re.search(
        r'(?:US\$|\$|USD)\s*([\d,]+(?:\.\d+)?)\s*(million|billion|thousand|M|B|K)?\b',
        text, re.IGNORECASE
    )
    
    if match:
        num_str = match.group(1).replace(',', '')
        scale_str = (match.group(2) or 'million').lower()
        
        try:
            value = float(num_str)
        except ValueError:
            return None
        
        scale_map = {
            'billion': 1000, 'b': 1000,
            'million': 1, 'm': 1,
            'thousand': 0.001, 'k': 0.001,
        }
        scale = scale_map.get(scale_str, 1)
        
        return ParsedAmount(
            value=value,
            scale=scale,
            currency='USD',
            up_to=up_to,
            kind=kind,
            raw_quote=quote
        )
    
    # Pattern: X million/billion dollars (no $ sign)
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
        
        return ParsedAmount(
            value=value,
            scale=scale,
            currency='USD',
            up_to=up_to,
            kind=kind,
            raw_quote=quote
        )
    
    # Pattern for GBP: £X million/billion
    match = re.search(
        r'[£GBP]\s*([\d,]+(?:\.\d+)?)\s*(million|billion)?\b',
        text, re.IGNORECASE
    )
    if match:
        num_str = match.group(1).replace(',', '')
        scale_str = (match.group(2) or 'million').lower()
        
        try:
            value = float(num_str)
        except ValueError:
            return None
        
        scale_map = {'billion': 1000, 'million': 1}
        scale = scale_map.get(scale_str, 1)
        
        return ParsedAmount(
            value=value,
            scale=scale,
            currency='GBP',
            up_to=up_to,
            kind=kind,
            raw_quote=quote
        )
    
    # Pattern for EUR: €X million/billion
    match = re.search(
        r'[€EUR]\s*([\d,]+(?:\.\d+)?)\s*(million|billion)?\b',
        text, re.IGNORECASE
    )
    if match:
        num_str = match.group(1).replace(',', '')
        scale_str = (match.group(2) or 'million').lower()
        
        try:
            value = float(num_str)
        except ValueError:
            return None
        
        scale_map = {'billion': 1000, 'million': 1}
        scale = scale_map.get(scale_str, 1)
        
        return ParsedAmount(
            value=value,
            scale=scale,
            currency='EUR',
            up_to=up_to,
            kind=kind,
            raw_quote=quote
        )
    
    # Pattern for shares: X shares or X,XXX,XXX shares
    # Only for equity kind
    if kind == AmountKind.EQUITY:
        match = re.search(
            r'([\d,]+)\s*shares?',
            text, re.IGNORECASE
        )
        if match:
            num_str = match.group(1).replace(',', '')
            try:
                value = float(num_str)
                # For shares, we store the count directly
                return ParsedAmount(
                    value=value,
                    scale=1,  # raw count
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
    - '首付' only for kind=upfront whose quote contains 'upfront'
    """
    if not parsed:
        return '未披露'
    
    # Handle shares separately
    if parsed.currency == 'SHARES':
        shares = int(parsed.value)
        if shares >= 10000:
            wan = shares / 10000
            if wan >= 100:
                return f"约 {wan / 100:.1f} 亿股".replace('.0 ', ' ')
            else:
                return f"约 {wan:,.0f} 万股"
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
    """
    lines = []
    
    for amount in amounts:
        rendered = render_amount_chinese(amount)
        
        if amount.kind == AmountKind.UPFRONT:
            if 'upfront' in amount.raw_quote.lower():
                lines.append(f"首付：{rendered}")
            else:
                lines.append(f"初期付款：{rendered}")
        
        elif amount.kind == AmountKind.PURCHASE_PRICE:
            lines.append(f"收购对价：{rendered}")
        
        elif amount.kind == AmountKind.MILESTONES_TOTAL:
            lines.append(f"里程碑：{rendered}")
        
        elif amount.kind == AmountKind.EQUITY:
            if amount.currency == 'SHARES':
                lines.append(f"股份：{rendered}")
            else:
                lines.append(f"股权：{rendered}")
        
        elif amount.kind == AmountKind.FACILITY_SIZE:
            lines.append(f"贷款额度：{rendered}")
        
        elif amount.kind == AmountKind.DRAWN:
            lines.append(f"已提取：{rendered}")
        
        else:
            lines.append(rendered)
    
    return lines


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
    
    # Verification 2: counterparty_quote must exist in filing
    if not counterparty_quote:
        logging.info("Deal dropped: no counterparty_quote")
        return None
    if not verify_quote_in_filing(counterparty_quote, filing_text):
        logging.info("Deal dropped: counterparty_quote not found in filing")
        return None
    
    # Verification 3: counterparty must appear in both quotes
    if not counterparty:
        logging.info("Deal dropped: no counterparty name")
        return None
    if not verify_counterparty_in_quotes(counterparty, counterparty_quote, type_quote):
        logging.info("Deal dropped: counterparty '%s' not verified in quotes", counterparty)
        return None
    
    # Verification 4: filer must be a party (from EDGAR metadata)
    # The filer is always one party - check it appears in the filing
    if not match_company_whole_word(filer_name, filing_text):
        logging.info("Deal dropped: filer '%s' not found in filing", filer_name)
        return None
    
    # Verification 5: detect role from type_quote
    role_info = detect_role_from_quote(type_quote, filer_name, counterparty)
    if not role_info:
        logging.info("Deal dropped: could not determine filer role from type_quote")
        return None
    
    filer_role = role_info['filer_role']
    
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
        
        # Verification: quote must be in filing
        if not verify_quote_in_filing(quote, filing_text):
            logging.debug("Amount dropped: quote not found in filing: %s", quote[:50])
            continue
        
        # Verification: amount quote must name counterparty OR be in same paragraph
        # Per design: "Each amount quote must name or be within the same paragraph"
        counterparty_in_quote = match_company_whole_word(counterparty, quote)
        same_para = quotes_in_same_paragraph(quote, counterparty_quote, filing_text)
        
        if not counterparty_in_quote and not same_para:
            logging.debug("Amount dropped: neither names counterparty nor in same paragraph: %s", quote[:50])
            continue
        
        try:
            kind = AmountKind(kind_str)
        except ValueError:
            kind = AmountKind.OTHER
        
        # Parse amount from quote
        parsed = parse_amount_from_quote(quote, kind)
        if parsed:
            verified_amounts.append(parsed)
        else:
            logging.debug("Amount dropped: could not parse amount from: %s", quote[:50])
    
    # Build headline amount (first amount or facility_size/purchase_price)
    headline_amount = None
    for amount in verified_amounts:
        if amount.kind in (AmountKind.PURCHASE_PRICE, AmountKind.FACILITY_SIZE, AmountKind.UPFRONT):
            headline_amount = render_amount_chinese(amount)
            break
    if not headline_amount and verified_amounts:
        headline_amount = render_amount_chinese(verified_amounts[0])
    
    # Build title and lines (no model free text)
    title = build_deal_title(filer_name, counterparty, deal_type, filer_role, headline_amount)
    detail_lines = build_deal_lines(deal_type, verified_amounts, filer_role)
    
    # Build output dict
    deal_kinds_map = {
        DealType.ACQUISITION: ['acq'],
        DealType.MERGER: ['acq'],
        DealType.LICENSE_COLLABORATION: ['lic'],
        DealType.EQUITY_FINANCING: ['inv'],
        DealType.DEBT_FACILITY: ['inv'],
        DealType.OBLIGATION_BUYOUT: ['lic'],
    }
    
    return {
        'url': filing_url,
        'title': title,
        'company': filer_name,
        'counterparty': counterparty,
        'kinds': deal_kinds_map.get(deal_type, ['lic']),
        'money': headline_amount or '未披露',
        'structure': ' | '.join(detail_lines) if detail_lines else '',
        'why': '',  # No model free text
        'source_name': 'SEC EDGAR',
        'date': filing_date[:7] if filing_date else '',
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
    }


def extract_deals_from_filings(
    filings: list[dict],
    claude_client: Any = None,
    max_deals: int = 6
) -> list[dict]:
    """Extract deals from SEC filings using Claude with strict verification.
    
    This is the main entry point for the production pipeline.
    
    Args:
        filings: List of filing dicts with 'filing_text', 'company', 'url', 'date', 'event_date'
        claude_client: Anthropic client (will create if None)
        max_deals: Maximum number of deals to return
    
    Returns:
        List of verified deal dicts.
    """
    if not filings:
        return []
    
    if claude_client is None:
        from anthropic import Anthropic
        claude_client = Anthropic()
    
    deals = []
    
    for filing in filings:
        filing_text = filing.get('filing_text', '')
        if not filing_text or len(filing_text) < 100:
            continue
        
        filer_name = filing.get('company', '')
        if not filer_name:
            continue
        
        event_date = filing.get('event_date')
        
        # Event date required - if missing, skip
        if not event_date:
            logging.info("Filing skipped: no event date: %s", filing.get('url', ''))
            continue
        
        # Call Claude with strict tool schema
        try:
            model = os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-5-5")
            
            prompt = f"""Analyze this SEC filing and extract deal information.

CRITICAL: All quotes MUST be EXACT verbatim text from the filing - no paraphrasing, no rewording, no additions.

The filing company is: {filer_name}

Filing text:
{filing_text[:25000]}

If there is a significant business deal (acquisition, merger, license, collaboration, financing, loan, or buyout):
1. Identify the deal type
2. Identify the counterparty (the other company, not {filer_name})
3. Extract EXACT quotes from the filing for:
   - type_quote: a sentence describing the deal type (must contain counterparty name)
   - counterparty_quote: a sentence containing the counterparty name
   - amounts: each with kind and EXACT quote containing the dollar amount

If there is no significant deal, return deal_type="none".

IMPORTANT: Every quote must be an EXACT substring of the filing text above. Do not paraphrase or modify quotes."""

            message = claude_client.messages.create(
                model=model,
                max_tokens=2000,
                tools=[DEAL_EXTRACTION_SCHEMA],
                tool_choice={"type": "tool", "name": "extract_deal"},
                messages=[{"role": "user", "content": prompt}],
            )
            
            # Extract tool response
            claude_response = None
            for block in message.content:
                if block.type == "tool_use" and block.name == "extract_deal":
                    claude_response = block.input
                    break
            
            if claude_response:
                deal = process_sec_deal(
                    filing_text=filing_text,
                    filer_name=filer_name,
                    filing_url=filing.get('url', ''),
                    filing_date=filing.get('date', ''),
                    event_date=event_date,
                    claude_response=claude_response
                )
                
                if deal:
                    deals.append(deal)
                    logging.info("Deal extracted: %s", deal['title'])
                    
                    if len(deals) >= max_deals:
                        break
        
        except Exception as e:
            logging.warning("Error processing filing %s: %s", filing.get('url', ''), e)
            continue
    
    return deals


# =============================================================================
# NEWS / ACADEMIC TEXT STRIPPING
# =============================================================================

def split_into_sentences(text: str) -> list[tuple[int, int, str]]:
    """Split text into sentences for unverified number stripping.
    
    Split on '。！？' and '. ' followed by space/capital.
    NEVER split on decimal points (e.g., '$35.0 million').
    
    Returns list of (start, end, sentence) tuples.
    """
    if not text:
        return []
    
    sentences = []
    
    # Pattern for sentence boundaries:
    # - Chinese: 。！？
    # - English: '. ' followed by uppercase or end
    # But NOT: decimal points like '$35.0' or '1.5 billion'
    
    # First, protect decimal numbers by marking them
    protected = text
    decimal_pattern = r'(\d+)\.(\d)'
    placeholder = '__DECIMAL__'
    
    # Temporarily replace decimal points
    decimals = []
    def protect_decimal(m):
        decimals.append((m.group(1), m.group(2)))
        return f"{m.group(1)}{placeholder}{len(decimals) - 1}__"
    
    protected = re.sub(decimal_pattern, protect_decimal, protected)
    
    # Now split on sentence boundaries
    # Chinese sentence enders
    pattern = r'([。！？])|(\.\s+(?=[A-Z]|$))'
    
    last_end = 0
    for m in re.finditer(pattern, protected):
        sentence = protected[last_end:m.end()].strip()
        if sentence:
            sentences.append((last_end, m.end(), sentence))
        last_end = m.end()
    
    # Don't forget the last sentence
    if last_end < len(protected):
        sentence = protected[last_end:].strip()
        if sentence:
            sentences.append((last_end, len(protected), sentence))
    
    # Restore decimal points
    def restore_decimal(s):
        result = s
        for i, (d1, d2) in enumerate(decimals):
            result = result.replace(f"{d1}{placeholder}{i}__", f"{d1}.{d2}")
        return result
    
    return [(start, end, restore_decimal(sentence)) for start, end, sentence in sentences]


def extract_numbers_from_text(text: str) -> set[str]:
    """Extract all numbers (including Chinese) from text for verification."""
    numbers = set()
    
    # Arabic numerals with optional decimal
    for m in re.finditer(r'[\d,]+(?:\.\d+)?', text):
        num = m.group().replace(',', '')
        if num and num != '.':
            numbers.add(num)
            # Also add without decimals for matching
            if '.' in num:
                numbers.add(num.split('.')[0])
    
    # Chinese numerals
    chinese_digits = '零一二三四五六七八九十百千万亿两〇'
    chinese_pattern = rf'[{chinese_digits}]+'
    for m in re.finditer(chinese_pattern, text):
        numbers.add(m.group())
    
    # Percentages
    for m in re.finditer(r'\d+(?:\.\d+)?%', text):
        numbers.add(m.group())
    
    return numbers


def strip_unverified_numbers_from_text(
    text: str,
    source_text: str
) -> str:
    """Remove sentences containing numbers not present in source.
    
    This runs on news/academic text in production.
    Removes whole sentences (split correctly) that contain numbers not in source.
    """
    if not text or not source_text:
        return text
    
    source_numbers = extract_numbers_from_text(source_text)
    sentences = split_into_sentences(text)
    
    kept_parts = []
    last_end = 0
    
    for start, end, sentence in sentences:
        sentence_numbers = extract_numbers_from_text(sentence)
        
        # Check if all numbers in sentence are verified
        unverified = sentence_numbers - source_numbers
        
        if unverified:
            # This sentence has unverified numbers - remove it
            logging.debug("Stripping sentence with unverified numbers %s: %s", 
                         unverified, sentence[:50])
            # Keep any text before this sentence that wasn't part of a previous sentence
            if start > last_end:
                kept_parts.append(text[last_end:start])
        else:
            # Keep this sentence
            kept_parts.append(text[last_end:end])
        
        last_end = end
    
    # Keep any trailing text
    if last_end < len(text):
        kept_parts.append(text[last_end:])
    
    result = ''.join(kept_parts).strip()
    
    # Clean up any double spaces or orphaned punctuation
    result = re.sub(r'\s+', ' ', result)
    result = re.sub(r'^\s*[,，;；]\s*', '', result)
    
    return result


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
