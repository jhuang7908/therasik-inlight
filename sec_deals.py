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
    """Normalize whitespace and quotes for quote matching.
    
    Handles:
    - Multiple whitespace → single space
    - Fancy Unicode quotes (U+201C, U+201D, U+2018, U+2019) → ASCII quotes
    - En/em dashes (U+2013, U+2014) → regular dash
    """
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


def verify_counterparty_in_quotes(
    counterparty: str, 
    counterparty_quote: str, 
    type_quote: str,
    filing_text: str = ""
) -> bool:
    """Verify counterparty name appears in quotes, supporting defined terms resolution.
    
    SEC filings often use defined terms like "the parties", "the Lenders", "Licensor"
    after establishing them earlier in the document. This function allows:
    1. Direct counterparty name match in both quotes, OR
    2. Counterparty in counterparty_quote AND a defined term in type_quote if:
       - The defined term is established in the filing (e.g., 'Sanofi ("Sanofi")')
       - OR the defined term appears near counterparty name in same paragraph
    """
    if not counterparty:
        return False
    
    # Counterparty must be in counterparty_quote
    if not counterparty_quote:
        return False
    if not match_company_whole_word(counterparty, counterparty_quote):
        return False
    
    # Check if counterparty is directly in type_quote
    if type_quote and match_company_whole_word(counterparty, type_quote):
        return True
    
    # If not direct match, check for defined terms that could refer to counterparty
    # Task 2: STRICT - must be via definition sentence itself, NOT paragraph co-occurrence
    if type_quote and filing_text:
        # Common defined terms in SEC filings
        defined_term_patterns = [
            r'\bthe\s+parties\b',
            r'\bthe\s+lenders?\b',
            r'\bthe\s+licensor\b',
            r'\bthe\s+licensee\b',
            r'\bthe\s+investor(?:s)?\b',
            r'\bthe\s+purchaser\b',
            r'\bthe\s+seller\b',
            r'\bthe\s+borrower\b',
            r'\bthe\s+agent\b',
        ]
        
        type_quote_lower = type_quote.lower()
        
        for pattern in defined_term_patterns:
            if re.search(pattern, type_quote_lower):
                # STRICT: Only accept via DEFINITION SENTENCE patterns
                # Must have explicit definition: 'Sanofi ("Sanofi")' or 'Hercules Capital, Inc. (the "Lender")'
                # DO NOT accept paragraph co-occurrence
                counterparty_normalized = normalize_company_name(counterparty).lower()
                filing_lower = filing_text.lower()
                
                # Definition sentence patterns ONLY
                # Pattern: 'Company Name ("Defined Term")' or 'Company Name (the "Defined Term")'
                def_patterns = [
                    # Company ("Company") or Company (the "Lender") - defined term in parens right after company
                    rf'{re.escape(counterparty_normalized)}\s*\(\s*["\u201c]?(?:the\s+)?(?:{pattern[2:-2]}|{re.escape(counterparty_normalized)})["\u201d]?\s*\)',
                    # With additional text between: Company, a corporation ("Company")
                    rf'{re.escape(counterparty_normalized)}[^()]*\(\s*["\u201c]?(?:the\s+)?(?:{pattern[2:-2]}|{re.escape(counterparty_normalized)})["\u201d]?\s*\)',
                    # Herein/hereinafter patterns
                    rf'{re.escape(counterparty_normalized)}\s*(?:,\s*)?(?:herein|hereinafter)\s+(?:referred\s+to\s+as\s+)?["\u201c]?(?:the\s+)?{pattern[2:-2]}["\u201d]?',
                ]
                
                for def_pattern in def_patterns:
                    if re.search(def_pattern, filing_lower, re.IGNORECASE):
                        logging.debug("Counterparty '%s' verified via definition sentence", counterparty)
                        return True
                
                # DO NOT fall back to paragraph proximity - that was the bug
                # Paragraph co-occurrence is NOT sufficient for defined term resolution
    
    # No type_quote means we can't verify
    if not type_quote:
        return False
    
    # Strict: counterparty must be in type_quote directly if no defined term match
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
    estimate_patterns = [
        r'\b(?:analyst|analysts|media|estimate[sd]?|estimated|valuation|valued at|worth|potentially|reportedly|sources?\s+(?:say|said|report))\b',
        r'\b(?:according\s+to|per|sources?\s+familiar)\b',
        r'\b(?:market\s+(?:cap|capitalization|value)|stock\s+(?:price|value))\b',
    ]
    for pattern in estimate_patterns:
        if re.search(pattern, text):
            logging.debug("Amount dropped: analyst/media estimate: %s", quote[:50])
            return None
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
LICENSE_GRANT_PATTERNS = [
    r'\bgrants?\s+.*\b(?:an?\s+)?(?:exclusive\s+)?licen[sc]e\b',
    r'\bexclusive\s+(?:worldwide\s+)?licen[sc]e\b',
    r'\blicen[sc]e\s+agreement\b',
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
    """Disambiguate between license and buyout based on explicit language.
    
    Returns:
        (DealType, None) if clear determination
        (None, reason) if ambiguous and should be dropped
    
    Rules:
    - License ONLY if has license grant language AND no buyout language
    - Buyout if has buyout language (regardless of other language)
    - If both or neither → drop with reason
    """
    if not type_quote:
        return None, "empty type_quote"
    
    text_lower = type_quote.lower()
    
    has_license_grant = any(re.search(p, text_lower) for p in LICENSE_GRANT_PATTERNS)
    has_buyout = any(re.search(p, text_lower) for p in BUYOUT_TERMINATION_PATTERNS)
    
    if has_buyout and not has_license_grant:
        return DealType.OBLIGATION_BUYOUT, None
    
    if has_license_grant and not has_buyout:
        return DealType.LICENSE_COLLABORATION, None
    
    if has_buyout and has_license_grant:
        return None, "ambiguous: both license grant and buyout language present"
    
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
) -> DealType | None:
    """Validate and potentially correct the claimed deal type based on type_quote content.
    
    Returns:
        - The validated deal type (may be different from claimed if quote supports it)
        - None if the type cannot be validated and should be dropped
    
    Rules:
    - Acquisition requires explicit acquire/merger/purchase language with acquirer as subject
    - License vs buyout: disambiguate using explicit patterns (A6a/A6d rule)
    - Equity financing allowed only for investment-specific language
    - Debt facility requires credit/loan language
    - Obligation buyout requires explicit buyout/termination of obligations
    """
    if not type_quote:
        return None
    
    text_lower = type_quote.lower()
    
    # A6a/A6d: License vs buyout disambiguation - strict rule
    # If claim is license or buyout, use disambiguation
    if claimed_type in (DealType.LICENSE_COLLABORATION, DealType.OBLIGATION_BUYOUT):
        resolved_type, drop_reason = disambiguate_license_vs_buyout(type_quote)
        if drop_reason and "ambiguous" in drop_reason:
            logging.info("Deal dropped: %s", drop_reason)
            return None
        if resolved_type is not None:
            if resolved_type != claimed_type:
                logging.info("Deal type corrected from %s to %s via disambiguation", 
                            claimed_type.value, resolved_type.value)
            return resolved_type
        # Neither license nor buyout detected - fall through to other validation
    
    # Check for validators if the claimed type has specific requirements
    if claimed_type in DEAL_TYPE_VALIDATORS:
        patterns = DEAL_TYPE_VALIDATORS[claimed_type]
        has_support = any(re.search(p, text_lower) for p in patterns)
        if not has_support:
            logging.info("Deal type '%s' not supported by type_quote", claimed_type.value)
            # Try to infer correct type
            return infer_deal_type_from_quote(type_quote)
    
    # Additional validation for equity_financing - reject payment/consideration language
    if claimed_type == DealType.EQUITY_FINANCING:
        for pattern in EQUITY_FINANCING_REJECT_PATTERNS:
            if re.search(pattern, text_lower):
                logging.info("Deal type equity_financing rejected: payment/consideration language")
                return infer_deal_type_from_quote(type_quote)
    
    # Additional validation for acquisitions - the acquirer must be the grammatical subject
    if claimed_type == DealType.ACQUISITION:
        # Check for patterns where someone other than the filer/counterparty is acquiring
        if re.search(r'\bpreviously\s+(?:entered|agreed|signed)\b', text_lower):
            logging.info("Deal dropped: type_quote describes historical agreement")
            return None
    
    return claimed_type


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


def is_historical_agreement(type_quote: str) -> bool:
    """Check if the type_quote describes a historical/past agreement rather than current event.
    
    Key insight: When the current 8-K event IS an amendment to a prior agreement,
    references to the original agreement's date (e.g., "dated as of November 29, 2017")
    are NOT historical - the current event is the amendment itself.
    
    BUT: "previously entered into...as amended" is STILL historical - "as amended" is just
    a parenthetical describing the prior agreement's state, not indicating a current amendment event.
    
    We look for CURRENT amendment language: "entered into Amendment No. X" or "entered into 
    the Sixth Amendment" - these indicate the current event IS an amendment.
    """
    if not type_quote:
        return False
    
    text_lower = type_quote.lower()
    
    # First check for CURRENT amendment event patterns:
    # "entered into Amendment No. X" or "entered into the Sixth Amendment"
    # These indicate the current 8-K is ABOUT an amendment, so it's NOT historical
    current_amendment_patterns = [
        # Explicit amendment action: "entered into Amendment No. 4"
        r'\b(?:enter(?:ed|s)?|execut(?:ed|es)?|sign(?:ed|s)?)\s+(?:into\s+)?(?:the\s+)?(?:amendment\s+no\.?\s*\d+|(?:first|second|third|fourth|fifth|sixth|seventh|eighth|ninth|tenth)\s+amendment)\b',
        # "The Amendment removes" - describing current amendment action
        r'\bthe\s+amendment\s+(?:removes?|eliminates?|terminates?|provides?|grants?)\b',
    ]
    
    for pattern in current_amendment_patterns:
        if re.search(pattern, text_lower):
            return False
    
    # Now check for historical patterns - these indicate past, not current event:
    historical_patterns = [
        # "previously entered" clearly indicates past, not current
        r'\bpreviously\s+(?:entered|agreed|executed|signed)\b',
        # "original agreement" when not in context of amendment
        r'\boriginal\s+agreement\b',
        # Any ", as amended" parenthetical (describing state of a prior agreement)
        # This catches "previously entered...as amended" which is still historical
        r',\s*as\s+amended\b',
        # "as amended through" or "prior to" (describing history)
        r'\bas\s+amended\s+(?:and\s+restated\s+)?(?:from\s+time\s+to\s+time\s+)?(?:through|prior\s+to)\b',
    ]
    
    for pattern in historical_patterns:
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
            if 'upfront' in amount.raw_quote.lower():
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
    
    # Verification 1a: Check for non-deal agreements (services, lease, etc.)
    if is_non_deal_agreement(type_quote):
        logging.info("Deal dropped: non-deal agreement type (services/lease/employment): %s", type_quote[:80])
        return None
    
    # Verification 1b: Check if this describes a historical agreement
    if is_historical_agreement(type_quote):
        logging.info("Deal dropped: type_quote describes historical agreement: %s", type_quote[:80])
        return None
    
    # Verification 1c: Validate deal type against type_quote content
    validated_type = validate_deal_type_from_quote(deal_type, type_quote)
    if validated_type is None:
        logging.info("Deal dropped: could not validate deal type from type_quote")
        return None
    deal_type = validated_type
    
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
    
    # Verification 3b: counterparty must NOT equal filer (self-deal check)
    filer_normalized = normalize_company_name(filer_name).lower()
    counterparty_normalized = normalize_company_name(counterparty).lower()
    if filer_normalized == counterparty_normalized or filer_normalized in counterparty_normalized or counterparty_normalized in filer_normalized:
        logging.info("Deal dropped: counterparty '%s' same as filer '%s'", counterparty, filer_name)
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
        
        # Verification: quote must be in filing
        if not verify_quote_in_filing(quote, filing_text):
            logging.debug("Amount dropped: quote not found in filing: %s", quote[:50])
            continue
        
        # Verification: amount quote must name counterparty OR be in a paragraph that names counterparty
        # AND the amount should be in a section discussing the main deal (near type_quote)
        # Per design: "Each amount quote must name or be within the same paragraph as counterparty"
        counterparty_in_quote = match_company_whole_word(counterparty, quote)
        
        # Check if the amount quote's paragraph contains the counterparty name
        counterparty_in_same_para = False
        amount_para_idx = -1
        if not counterparty_in_quote:
            # Find the paragraph containing this amount quote
            paragraphs = split_into_paragraphs(filing_text)
            norm_quote = normalize_whitespace(quote).lower()
            for idx, (_, _, para_text) in enumerate(paragraphs):
                norm_para = normalize_whitespace(para_text).lower()
                if norm_quote in norm_para:
                    amount_para_idx = idx
                    # Found the paragraph - check if counterparty is in it
                    if match_company_whole_word(counterparty, para_text):
                        counterparty_in_same_para = True
                        break
        
        # STRICT: Amount must be in SAME PARAGRAPH as counterparty, or in same Item/Exhibit section
        # where the counterparty is mentioned (for defined terms like "The Term Loans")
        in_same_item_section = False
        if not counterparty_in_quote and not counterparty_in_same_para:
            # Find the Item/Exhibit section containing the amount
            amount_pos = find_quote_position(quote, filing_text)
            if amount_pos is not None:
                # Find enclosing Item section for amount
                amount_section = _find_item_section(filing_text, amount_pos)
                if amount_section:
                    # Check if counterparty is mentioned in this same section
                    section_text = _extract_item_section_text(filing_text, amount_section)
                    if section_text and match_company_whole_word(counterparty, section_text):
                        in_same_item_section = True
        
        if not counterparty_in_quote and not counterparty_in_same_para and not in_same_item_section:
            logging.debug("Amount dropped: not in same paragraph or Item section as counterparty: %s", quote[:50])
            continue
        
        # Additional check: amount quote should be contextually near the type_quote
        # This prevents picking up amounts from unrelated transactions in the same filing
        if type_quote:
            type_pos = find_quote_position(type_quote, filing_text)
            amount_pos = find_quote_position(quote, filing_text)
            if type_pos is not None and amount_pos is not None:
                distance = abs(amount_pos - type_pos)
                # If amount is very far from type_quote (>10000 chars), it might be from a different transaction
                # Allow if it's in an exhibit or press release section
                if distance > 10000:
                    # Check if amount is in a different Item section
                    type_item_match = re.search(r'Item\s+\d+\.\d+', filing_text[max(0, type_pos-500):type_pos+100], re.IGNORECASE)
                    amount_item_match = re.search(r'Item\s+\d+\.\d+', filing_text[max(0, amount_pos-500):amount_pos+100], re.IGNORECASE)
                    if type_item_match and amount_item_match:
                        type_item = type_item_match.group().lower()
                        amount_item = amount_item_match.group().lower()
                        if type_item != amount_item:
                            logging.debug("Amount dropped: in different Item section (type: %s, amount: %s): %s", 
                                         type_item, amount_item, quote[:50])
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
    
    # Build headline amount (first non-conditional amount)
    # Conditional amounts ("may receive up to") should NOT be used as headline
    # They can only appear in detail lines (e.g., milestones)
    headline_amount = None
    for amount in verified_amounts:
        # Skip conditional (up_to) amounts for headline - these are uncertain
        if amount.up_to:
            continue
        if amount.kind in (AmountKind.PURCHASE_PRICE, AmountKind.UPFRONT):
            headline_amount = render_amount_chinese(amount)
            break
    # For debt facility deals ONLY, facility_size (even with up_to) IS appropriate as headline
    if not headline_amount and deal_type == DealType.DEBT_FACILITY:
        for amount in verified_amounts:
            if amount.kind == AmountKind.FACILITY_SIZE:
                headline_amount = render_amount_chinese(amount)
                break
    # Still no headline? Use first non-conditional, non-milestone, non-facility_size amount
    if not headline_amount:
        for amount in verified_amounts:
            if not amount.up_to and amount.kind not in (AmountKind.MILESTONES_TOTAL, AmountKind.FACILITY_SIZE):
                headline_amount = render_amount_chinese(amount)
                break
    
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
    
    # Use event_date if available, fallback to filing_date
    display_date = event_date[:7] if event_date else (filing_date[:7] if filing_date else '')
    
    return {
        'url': filing_url,
        'title': title,
        'company': filer_name,
        'counterparty': counterparty,
        'kinds': deal_kinds_map.get(deal_type, ['lic']),
        'money': headline_amount or '',
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
                deals.append(deal)
                logging.info("Deal extracted: %s", deal['title'])
                
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
    
    # Arabic numerals with optional decimal - these are clear quantities
    for m in re.finditer(r'[\d,]+(?:\.\d+)?', text):
        num = m.group().replace(',', '')
        if num and num != '.':
            numbers.add(num)
            # Also add without decimals for matching
            if '.' in num:
                numbers.add(num.split('.')[0])
    
    # Chinese numerals - only extract when they represent QUANTITIES
    # Pattern: Chinese number + unit suffix (亿/万/百/千 + currency or 例/名/人/组 etc.)
    # This matches "三亿美元" and "两百亿" but not "一种方法"
    # Note: \b doesn't work reliably with CJK, so we use explicit end patterns
    quantity_patterns = [
        # Amount with currency: 三亿美元, 1.5亿美元
        r'([零一二三四五六七八九十百千万亿两〇]+)\s*(?:亿|万|百|千)?\s*(?:美元|欧元|英镑|元|人民币|港币|日元)',
        # Standalone large numbers followed by non-number CJK or end of word
        # "两百亿市场" → extracts "两百亿"
        r'([零一二三四五六七八九十百千万亿两〇]+)\s*(?:亿|万)(?![零一二三四五六七八九十百千万亿两〇])',
        # Count units: 三例患者, 120名
        r'([零一二三四五六七八九十百千万亿两〇]+)\s*(?:例|名|位|人|个|家|项|条|篇|份|次|组|年|月|日|周|天)(?![零一二三四五六七八九十百千万亿两〇])',
    ]
    
    for pattern in quantity_patterns:
        for m in re.finditer(pattern, text):
            numbers.add(m.group(1))
    
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
    
    Note: Positions returned by split_into_sentences are in the original text.
    """
    if not text or not source_text:
        return text
    
    source_numbers = extract_numbers_from_text(source_text)
    sentences = split_into_sentences(text)
    
    if not sentences:
        return text
    
    kept_sentences = []
    
    for start, end, sentence in sentences:
        sentence_numbers = extract_numbers_from_text(sentence)
        
        # Check if all numbers in sentence are verified
        unverified = sentence_numbers - source_numbers
        
        if unverified:
            # This sentence has unverified numbers - skip it
            logging.debug("Stripping sentence with unverified numbers %s: %s", 
                         unverified, sentence[:50])
        else:
            # Keep this sentence
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
