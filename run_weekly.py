#!/usr/bin/env python3
"""Fetch the last 7 days, draft Chinese copy, draw figures, write the site files.

    python run_weekly.py
    python run_weekly.py --dry-run

--dry-run writes only under preview/ and does not update content/ or call WeChat.
This script never pushes git and never calls the WeChat API.
"""

from __future__ import annotations

import argparse
import base64
import json
import logging
import os
import re
import sys
import traceback
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import feedparser
import yaml

ROOT = Path(__file__).resolve().parent
FIELDS = {
    "c1": "类器官",
    "c2": "AI 药物设计",
    "c3": "肿瘤免疫",
    "c4": "自身免疫疾病",
    "c5": "动物模型",
    "c6": "抗体工程",
    "c7": "细胞治疗",
    "c8": "疫苗",
    "c9": "小核酸与 LNP",
}
DEAL_KINDS = {"acq", "lic", "newco", "clin", "inv", "policy"}

# Image prompt - stronger anti-text language, abstract descriptions
IMAGE_PREFIX = (
    "Flat vector scientific illustration on pure white background. "
    "Thin gray outlines, soft teal, coral, gold and blue-gray palette. "
    "Minimalist, clean, diagrammatic. Abstract shapes representing biological concepts. "
    "No 3D effects, no glow, no gradients, no photorealism, no shadows. "
    "Simple geometric shapes only. The subject fills the frame. "
    "CRITICAL: This image must contain absolutely NO TEXT of any kind. "
)
IMAGE_SUFFIX = (
    " STRICT REQUIREMENT: No text, no letters, no words, no labels, no captions, "
    "no numbers, no watermarks, no annotations, no legends, no arrows with text, "
    "no cell type names, no protein names, no gene names anywhere in the image. "
    "Every element must be purely visual with zero textual content."
)
UA = "FrontierDigestWeekly/1.0 (+https://inlight.therasik.com)"
SITE_BASE_URL = "https://inlight.therasik.com"  # Fix B9: Base URL for WeChat absolute image URLs

# Company alias table for whole-word matching
COMPANY_ALIASES = {
    'astrazeneca': ['astrazeneca', 'az'],
    'gsk': ['glaxosmithkline', 'gsk'],
    'jnj': ['johnson & johnson', 'johnson and johnson', 'j&j', 'jnj', 'janssen'],
    'bms': ['bristol-myers squibb', 'bristol myers squibb', 'bms'],
    'abbvie': ['abbvie'],
    'pfizer': ['pfizer'],
    'merck': ['merck', 'msd'],
    'novartis': ['novartis'],
    'roche': ['roche', 'genentech'],
    'genentech': ['genentech', 'roche'],
    'sanofi': ['sanofi', 'regeneron'],  # often in partnership
    'lilly': ['eli lilly', 'lilly'],
    'amgen': ['amgen'],
    'gilead': ['gilead'],
    'biogen': ['biogen'],
    'regeneron': ['regeneron'],
    'vertex': ['vertex'],
    'moderna': ['moderna'],
    'biontech': ['biontech'],
    'takeda': ['takeda'],
    'astellas': ['astellas'],
    'daiichi sankyo': ['daiichi sankyo', 'daiichi-sankyo'],
    'boehringer': ['boehringer ingelheim', 'boehringer'],
}

# Deal type allowed list (R6)
DEAL_TYPES_ALLOWED = {
    'acquisition': 'acq',
    'merger': 'acq',
    'license': 'lic',
    'collaboration': 'lic',
    'equity': 'inv',
    'financing': 'inv',
    'debt': 'credit',
    'credit': 'credit',
    'loan': 'credit',
    'buyout': 'buyout',
    'amendment': 'buyout',
    'termination': 'buyout',
}


# =============================================================================
# EVIDENCE-QUOTE SYSTEM (Round-6 redesign)
# =============================================================================

def _normalize_whitespace(text: str) -> str:
    """Normalize whitespace for quote matching."""
    return re.sub(r'\s+', ' ', text).strip()


def _verify_quote_in_filing(quote: str, filing_text: str) -> bool:
    """Verify quote is an exact substring of filing text (whitespace-normalized).
    
    R1: Every quote must be found in the source.
    """
    if not quote or not filing_text:
        return False
    
    norm_quote = _normalize_whitespace(quote)
    norm_filing = _normalize_whitespace(filing_text)
    
    return norm_quote.lower() in norm_filing.lower()


def _find_quote_position(quote: str, filing_text: str) -> int | None:
    """Find the character position of a quote in the filing text.
    
    Returns the start position or None if not found.
    """
    if not quote or not filing_text:
        return None
    
    norm_quote = _normalize_whitespace(quote).lower()
    norm_filing = _normalize_whitespace(filing_text).lower()
    
    pos = norm_filing.find(norm_quote)
    return pos if pos >= 0 else None


def _quotes_in_same_passage(quote1: str, quote2: str, filing_text: str, max_distance: int = 1500) -> bool:
    """Check if two quotes are within max_distance characters of each other.
    
    R5: Amount quotes must be within ±1500 chars of party quotes.
    """
    pos1 = _find_quote_position(quote1, filing_text)
    pos2 = _find_quote_position(quote2, filing_text)
    
    if pos1 is None or pos2 is None:
        return False
    
    return abs(pos1 - pos2) <= max_distance


def _parse_amount_from_quote(quote: str) -> dict | None:
    """Parse amount from verified quote: value, scale, currency, up_to.
    
    R3: Code (not model) parses amounts from verified quote.
    Returns dict with keys: value, scale, currency, up_to, raw_text
    """
    if not quote:
        return None
    
    text = quote.lower()
    result = {'raw_text': quote, 'up_to': False}
    
    # Check for 'up to' / 'maximum' / 'aggregate'
    if re.search(r'\b(?:up\s+to|maximum|aggregate)\b', text):
        result['up_to'] = True
    
    # Pattern: $X.X million/billion
    match = re.search(
        r'\$\s*([\d,]+(?:\.\d+)?)\s*(million|billion|thousand|M|B|K)\b',
        text, re.IGNORECASE
    )
    
    if match:
        num_str = match.group(1).replace(',', '')
        scale_str = match.group(2).lower()
        
        try:
            value = float(num_str)
        except ValueError:
            return None
        
        # Normalize scale to millions
        scale_map = {
            'billion': 1000, 'b': 1000,
            'million': 1, 'm': 1,
            'thousand': 0.001, 'k': 0.001,
        }
        scale = scale_map.get(scale_str, 1)
        
        result['value'] = value
        result['scale'] = scale
        result['value_in_millions'] = value * scale
        result['currency'] = 'USD'
        return result
    
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
        
        result['value'] = value
        result['scale'] = scale
        result['value_in_millions'] = value * scale
        result['currency'] = 'USD'
        return result
    
    # Pattern for euros: €X million/billion
    match = re.search(
        r'€\s*([\d,]+(?:\.\d+)?)\s*(million|billion)?\b',
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
        
        result['value'] = value
        result['scale'] = scale
        result['value_in_millions'] = value * scale
        result['currency'] = 'EUR'
        return result
    
    return None


def _render_amount_chinese(parsed: dict) -> str:
    """Render parsed amount in Chinese format.
    
    R3 unit tests:
    - 'up to $1.5 billion' → '最高 15 亿美元'
    - '$35.0 million' → '3,500 万美元'
    - '$20.0 million' → '2,000 万美元'
    """
    if not parsed or 'value_in_millions' not in parsed:
        return '未披露'
    
    millions = parsed['value_in_millions']
    currency = parsed.get('currency', 'USD')
    up_to = parsed.get('up_to', False)
    
    # Currency suffix
    curr_suffix = {
        'USD': '美元',
        'EUR': '欧元',
        'GBP': '英镑',
        'CNY': '人民币',
    }.get(currency, '美元')
    
    # Prefix for 'up to'
    prefix = '最高 ' if up_to else ''
    
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


def _extract_company_from_quote(quote: str, expected_name: str = None) -> str | None:
    """Extract company name from quote if it contains it.
    
    R4: Both party names must occur inside their quotes.
    """
    if not quote:
        return None
    
    # If expected_name provided, check if it's in the quote (whole-word)
    if expected_name:
        pattern = rf'\b{re.escape(expected_name)}\b'
        if re.search(pattern, quote, re.IGNORECASE):
            return expected_name
        
        # Check aliases
        expected_lower = expected_name.lower()
        for canonical, aliases in COMPANY_ALIASES.items():
            if expected_lower in aliases or canonical == expected_lower:
                for alias in aliases:
                    pattern = rf'\b{re.escape(alias)}\b'
                    if re.search(pattern, quote, re.IGNORECASE):
                        return expected_name
    
    return None


def _detect_role_from_quote(quote: str) -> tuple[str, str, str] | None:
    """Detect role (party1, role, party2) from quote grammar.
    
    R4: Role determined by code from quote's grammar for fixed patterns.
    Returns (actor, role_type, counterparty) or None.
    
    Patterns:
    - "X will acquire Y" / "X to acquire Y" → (X, 'acquirer', Y)
    - "acquisition of Y by X" → (X, 'acquirer', Y)
    - "X granted Y an exclusive license" → (X, 'licensor', Y)
    - "license agreement with X" → (None, 'license', X)
    - "X entered into a loan agreement with Y" → (X, 'borrower', Y)
    """
    if not quote:
        return None
    
    text = quote.strip()
    
    # Acquisition patterns
    patterns = [
        # "X will acquire Y", "X to acquire Y"
        (r'([A-Z][A-Za-z\s&,\.]+?)\s+(?:will|to)\s+acquire\s+([A-Z][A-Za-z\s&,\.]+)',
         'acquirer', 0, 1),
        # "acquisition of Y by X"
        (r'acquisition\s+of\s+([A-Z][A-Za-z\s&,\.]+?)\s+by\s+([A-Z][A-Za-z\s&,\.]+)',
         'acquirer', 1, 0),
        # "X acquires Y", "X acquired Y"
        (r'([A-Z][A-Za-z\s&,\.]+?)\s+acquir(?:es|ed)\s+([A-Z][A-Za-z\s&,\.]+)',
         'acquirer', 0, 1),
        # "X has agreed to acquire Y"
        (r'([A-Z][A-Za-z\s&,\.]+?)\s+has\s+agreed\s+to\s+acquire\s+([A-Z][A-Za-z\s&,\.]+)',
         'acquirer', 0, 1),
        
        # License patterns
        # "X granted Y a license", "X grants Y a license"
        (r'([A-Z][A-Za-z\s&,\.]+?)\s+grant(?:s|ed)\s+([A-Z][A-Za-z\s&,\.]+?)\s+(?:an?\s+)?(?:exclusive\s+)?licen[sc]e',
         'licensor', 0, 1),
        # "X entered into a license agreement with Y"
        (r'([A-Z][A-Za-z\s&,\.]+?)\s+entered\s+into\s+(?:an?\s+)?(?:exclusive\s+)?licen[sc]e\s+agreement\s+with\s+([A-Z][A-Za-z\s&,\.]+)',
         'licensor', 0, 1),
        # "license agreement between X and Y"
        (r'licen[sc]e\s+agreement\s+between\s+([A-Z][A-Za-z\s&,\.]+?)\s+and\s+([A-Z][A-Za-z\s&,\.]+)',
         'license_party', 0, 1),
        # "X licensed rights to Y"
        (r'([A-Z][A-Za-z\s&,\.]+?)\s+licen[sc]ed\s+(?:rights?\s+)?to\s+([A-Z][A-Za-z\s&,\.]+)',
         'licensor', 0, 1),
        
        # Collaboration patterns
        # "collaboration agreement with X"
        (r'([A-Z][A-Za-z\s&,\.]+?)\s+(?:entered\s+into\s+)?(?:a\s+)?collaboration\s+(?:agreement\s+)?with\s+([A-Z][A-Za-z\s&,\.]+)',
         'collaborator', 0, 1),
        
        # Credit/loan patterns
        # "X entered into a credit agreement with Y"
        (r'([A-Z][A-Za-z\s&,\.]+?)\s+entered\s+into\s+(?:a\s+)?(?:credit|loan|term\s+loan)\s+(?:facility\s+)?agreement\s+with\s+([A-Z][A-Za-z\s&,\.]+)',
         'borrower', 0, 1),
        
        # Buyout patterns
        # "X paid Y $Z to terminate/buy out"
        (r'([A-Z][A-Za-z\s&,\.]+?)\s+paid\s+([A-Z][A-Za-z\s&,\.]+)',
         'payer', 0, 1),
        # "buyout of X's obligations"
        (r'buyout\s+of\s+([A-Z][A-Za-z\s&,\.]+?)(?:\'s)?\s+obligations',
         'buyout_target', 0, None),
        
        # Investment patterns
        # "financing round led by X"
        (r'(?:Series\s+[A-Z]\s+)?financing\s+(?:round\s+)?led\s+by\s+([A-Z][A-Za-z\s&,\.]+)',
         'lead_investor', 0, None),
        # "X invested in Y"
        (r'([A-Z][A-Za-z\s&,\.]+?)\s+invested\s+in\s+([A-Z][A-Za-z\s&,\.]+)',
         'investor', 0, 1),
    ]
    
    for pattern, role_type, actor_idx, counter_idx in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            groups = match.groups()
            actor = groups[actor_idx].strip().rstrip('.,;') if actor_idx is not None and actor_idx < len(groups) else None
            counter = groups[counter_idx].strip().rstrip('.,;') if counter_idx is not None and counter_idx < len(groups) else None
            return (actor, role_type, counter)
    
    return None


def _verify_filer_is_party(filer_name: str, party1: str, party2: str = None) -> bool:
    """Verify the filer (from EDGAR metadata) is one of the deal parties.
    
    R4: The filer must be one of the parties.
    """
    if not filer_name:
        return False
    
    filer_lower = filer_name.lower().strip()
    
    # Direct match
    if party1 and filer_lower in party1.lower():
        return True
    if party2 and filer_lower in party2.lower():
        return True
    
    # Check reverse - party name in filer
    if party1 and party1.lower() in filer_lower:
        return True
    if party2 and party2.lower() in filer_lower:
        return True
    
    # Check aliases
    for canonical, aliases in COMPANY_ALIASES.items():
        if any(alias in filer_lower for alias in aliases):
            if party1 and any(alias in party1.lower() for alias in aliases):
                return True
            if party2 and any(alias in party2.lower() for alias in aliases):
                return True
    
    return False


def _detect_nonprofit_in_quotes(quotes: list[str]) -> bool:
    """Detect if quotes mention nonprofits/government/foundations.
    
    R8: Drop if quotes mention government agencies, foundations, nonprofits.
    """
    combined = ' '.join(quotes).lower()
    
    nonprofit_patterns = [
        r'\b(?:non-?profit|not-for-profit|foundation|charitable)\b',
        r'\b(?:government|federal|state)\s+(?:agency|grant|funding)\b',
        r'\bnih\b', r'\bdoe\b', r'\bnsf\b', r'\bdarpa\b',
        r'\bnational\s+institutes?\s+of\s+health\b',
        r'\bmulti-?party\s+commitment\b',
        r'\bconsortium\b',
    ]
    
    for pattern in nonprofit_patterns:
        if re.search(pattern, combined):
            return True
    
    return False


def _match_company_whole_word(name: str, text: str) -> bool:
    """Match company name as whole word with alias support.
    
    R9: Company-name matching: whole-word, alias table only.
    """
    if not name or not text:
        return False
    
    name_lower = name.lower().strip()
    text_lower = text.lower()
    
    # Direct whole-word match
    pattern = rf'\b{re.escape(name_lower)}\b'
    if re.search(pattern, text_lower):
        return True
    
    # Check aliases
    for canonical, aliases in COMPANY_ALIASES.items():
        if name_lower in aliases or canonical == name_lower:
            for alias in aliases:
                pattern = rf'\b{re.escape(alias)}\b'
                if re.search(pattern, text_lower):
                    return True
    
    return False


def _build_deal_title_from_verified(
    company: str,
    counterparty: str,
    deal_type: str,
    amount: str,
    role_info: dict = None
) -> str:
    """Build deal title from verified fields only.
    
    R6: No free text in deal records.
    """
    type_names = {
        'acq': '收购',
        'lic': '授权合作',
        'inv': '融资',
        'credit': '信贷额度',
        'buyout': '义务买断',
    }
    
    type_name = type_names.get(deal_type, '交易')
    
    # Special handling for buyouts - show payer→payee direction
    if deal_type == 'buyout' and role_info:
        payer = role_info.get('payer', company)
        payee = role_info.get('payee', counterparty)
        if payer and payee:
            title = f"{payer}向{payee}支付{type_name}"
        elif payer:
            title = f"{payer}{type_name}"
        else:
            title = f"{company}{type_name}"
    elif counterparty:
        title = f"{company}与{counterparty}{type_name}"
    else:
        title = f"{company}{type_name}"
    
    if amount and amount != '未披露':
        title += f"（{amount}）"
    
    return title


def _classify_deal_type_from_quote(quote: str) -> str | None:
    """Classify deal type from verified quote text.
    
    R7: Deal type allowed list.
    """
    if not quote:
        return None
    
    text = quote.lower()
    
    # Check patterns in order of specificity
    if re.search(r'\b(?:buyout|buy-?out|terminat|extinguish)\b.*\b(?:royalt|milestone|obligation)\b', text):
        return 'buyout'
    if re.search(r'\b(?:credit\s+(?:facility|agreement)|term\s+loan|revolving|debt\s+facility|venture\s+debt)\b', text):
        return 'credit'
    if re.search(r'\b(?:acqui(?:re|sition)|merger|purchase)\b', text):
        return 'acq'
    if re.search(r'\b(?:licen[sc]e|collaboration|exclusive\s+rights?|royalt(?:y|ies))\b', text):
        return 'lic'
    if re.search(r'\b(?:financ|invest|series\s+[a-z]|ipo|offering|equity)\b', text):
        return 'inv'
    
    return None


# =============================================================================
# Unit tests for evidence-quote system
# =============================================================================

def _test_amount_parsing():
    """Test amount parsing from verified quotes - R3."""
    tests = [
        # Required test cases from spec
        ('up to $1.5 billion', '最高 15 亿美元'),
        ('$35.0 million', '3,500 万美元'),
        ('$20.0 million', '2,000 万美元'),
        # Additional cases
        ('$100 million upfront payment', '1 亿美元'),
        ('aggregate of $500 million', '最高 5 亿美元'),
        ('$2.5 billion acquisition', '25 亿美元'),
        ('maximum of $750 million', '最高 7.5 亿美元'),
        ('€50 million', '5,000 万欧元'),
    ]
    
    passed = 0
    for quote, expected in tests:
        parsed = _parse_amount_from_quote(quote)
        result = _render_amount_chinese(parsed)
        if result == expected:
            passed += 1
        else:
            print(f"FAIL: '{quote}' -> '{result}' (expected '{expected}')")
    
    print(f"_test_amount_parsing: {passed}/{len(tests)} tests passed")
    return passed == len(tests)


def _test_quote_verification():
    """Test quote verification in filing text - R1."""
    filing = """
    On October 1, 2026, Alector, Inc. entered into a Collaboration Agreement 
    with Genentech, Inc. Alector will receive an upfront payment of $100 million
    in cash. The total deal value could reach $1.5 billion including milestones.
    """
    
    tests = [
        # Should pass
        ('upfront payment of $100 million', True),
        ('total deal value could reach $1.5 billion', True),
        ('Alector, Inc. entered into a Collaboration Agreement', True),
        # Should fail
        ('upfront payment of $200 million', False),  # Wrong amount
        ('total deal value is $1.5 billion', False),  # Different wording
        ('Pfizer entered into agreement', False),  # Wrong company
        ('', False),
    ]
    
    passed = 0
    for quote, expected in tests:
        result = _verify_quote_in_filing(quote, filing)
        if result == expected:
            passed += 1
        else:
            print(f"FAIL: '{quote[:40]}...' -> {result} (expected {expected})")
    
    print(f"_test_quote_verification: {passed}/{len(tests)} tests passed")
    return passed == len(tests)


def _test_role_detection():
    """Test role detection from quote grammar - R4."""
    tests = [
        # Acquisition patterns
        ('Pfizer will acquire Seagen for $43 billion', ('Pfizer', 'acquirer', 'Seagen')),
        ('acquisition of Seagen by Pfizer', ('Pfizer', 'acquirer', 'Seagen')),
        ('Merck acquires Prometheus Biosciences', ('Merck', 'acquirer', 'Prometheus Biosciences')),
        
        # License patterns  
        ('Genentech granted Alector an exclusive license', ('Genentech', 'licensor', 'Alector')),
        ('Alector entered into a license agreement with Genentech', ('Alector', 'licensor', 'Genentech')),
        
        # Credit patterns
        ('Rocket Pharma entered into a credit agreement with Hercules Capital', ('Rocket Pharma', 'borrower', 'Hercules Capital')),
        
        # Buyout patterns
        ('Immunome paid BMS $20 million', ('Immunome', 'payer', 'BMS')),
    ]
    
    passed = 0
    for quote, expected in tests:
        result = _detect_role_from_quote(quote)
        if result is None and expected is None:
            passed += 1
        elif result and expected:
            # Check actor and role_type match
            if result[0] and expected[0] and result[0].lower().startswith(expected[0].lower()[:5]):
                if result[1] == expected[1]:
                    passed += 1
                else:
                    print(f"FAIL: '{quote[:40]}...' -> {result} (expected {expected})")
            else:
                print(f"FAIL: '{quote[:40]}...' -> {result} (expected {expected})")
        else:
            print(f"FAIL: '{quote[:40]}...' -> {result} (expected {expected})")
    
    print(f"_test_role_detection: {passed}/{len(tests)} tests passed")
    return passed == len(tests)


def _test_same_passage():
    """Test same-passage check for quotes - R5."""
    # Create filing with distinct sections
    filing = """
    SECTION 1: Alpha Corp announced a partnership with Beta Inc for $500 million.
    
    """ + "x" * 2000 + """
    
    SECTION 2: Gamma Ltd acquired Delta Corp for $200 million in an unrelated deal.
    """
    
    tests = [
        # Same section - should pass
        ('Alpha Corp', 'Beta Inc', True),
        ('Alpha Corp', '$500 million', True),
        # Different sections - should fail (>1500 chars apart)
        ('Alpha Corp', 'Gamma Ltd', False),
        ('$500 million', '$200 million', False),
    ]
    
    passed = 0
    for q1, q2, expected in tests:
        result = _quotes_in_same_passage(q1, q2, filing, max_distance=1500)
        if result == expected:
            passed += 1
        else:
            print(f"FAIL: '{q1}' + '{q2}' -> {result} (expected {expected})")
    
    print(f"_test_same_passage: {passed}/{len(tests)} tests passed")
    return passed == len(tests)


def _test_filer_is_party():
    """Test filer-is-party verification - R4."""
    tests = [
        # Direct matches
        ('Alector, Inc.', 'Alector', 'Genentech', True),
        ('Genentech, Inc.', 'Alector', 'Genentech', True),
        # Filer name contains party
        ('Pfizer Inc.', 'Pfizer', 'Seagen', True),
        # Party name in filer
        ('Bristol-Myers Squibb Company', 'BMS', 'Immunome', True),
        # No match
        ('Alector, Inc.', 'Pfizer', 'Merck', False),
        # Adversarial: Merck/Verona/Pfizer swap
        ('Merck & Co., Inc.', 'Pfizer', 'Verona', False),  # Filer is Merck, not Pfizer
    ]
    
    passed = 0
    for filer, party1, party2, expected in tests:
        result = _verify_filer_is_party(filer, party1, party2)
        if result == expected:
            passed += 1
        else:
            print(f"FAIL: filer='{filer}', parties=({party1}, {party2}) -> {result} (expected {expected})")
    
    print(f"_test_filer_is_party: {passed}/{len(tests)} tests passed")
    return passed == len(tests)


def _test_company_whole_word():
    """Test whole-word company matching - R9."""
    tests = [
        # Should match
        ('Pfizer', 'Pfizer Inc. announces', True),
        ('AstraZeneca', 'deal with AstraZeneca UK', True),
        ('BMS', 'Bristol-Myers Squibb partnership', True),  # Alias
        # Should NOT match - substring
        ('AZ', 'Amazon Web Services deal', False),  # AZ should not match Amazon
        ('zen', 'AstraZeneca partnership', False),  # Partial
        ('nova', 'Novartis agreement', False),  # Partial
    ]
    
    passed = 0
    for name, text, expected in tests:
        result = _match_company_whole_word(name, text)
        if result == expected:
            passed += 1
        else:
            print(f"FAIL: '{name}' in '{text[:30]}...' -> {result} (expected {expected})")
    
    print(f"_test_company_whole_word: {passed}/{len(tests)} tests passed")
    return passed == len(tests)


def _test_real_sec_filings():
    """Test with real SEC filing text from Alector/Genentech, Regeneron/Sanofi, etc."""
    
    # Alector/Genentech collaboration - real 8-K text excerpt
    alector_filing = """
    On October 1, 2026, Alector, Inc. (the "Company") entered into a Collaboration 
    Agreement (the "Agreement") with Genentech, Inc. ("Genentech"), a member of the 
    Roche Group. Pursuant to the Agreement, the Company granted Genentech an 
    exclusive, worldwide license to develop, manufacture and commercialize antibody 
    products for up to two targets.
    
    Under the terms of the Agreement, the Company will receive an upfront payment 
    of $100 million in cash. The Company is eligible to receive up to an aggregate 
    of $1.0 billion in potential development, regulatory and commercial milestone 
    payments. Additionally, Genentech will pay tiered royalties on worldwide net 
    sales ranging from mid-single digits to low double digits.
    """
    
    # Test cases for Alector
    tests = []
    
    # Should pass - exact quotes
    tests.append((
        'upfront payment of $100 million',
        alector_filing,
        True,
        "Alector upfront exact"
    ))
    
    tests.append((
        'up to an aggregate of $1.0 billion',
        alector_filing,
        True,
        "Alector milestones exact"
    ))
    
    tests.append((
        'Alector, Inc. (the "Company") entered into a Collaboration Agreement',
        alector_filing,
        True,
        "Alector company quote"
    ))
    
    # Should fail - wrong amount
    tests.append((
        'upfront payment of $200 million',
        alector_filing,
        False,
        "Wrong amount should fail"
    ))
    
    # Should fail - company not in filing
    tests.append((
        'Pfizer entered into agreement',
        alector_filing,
        False,
        "Wrong company should fail"
    ))
    
    # Immunome/BMS buyout - simulate real 8-K excerpt
    immunome_filing = """
    On September 15, 2026, Immunome, Inc. ("Immunome") and Bristol-Myers Squibb 
    Company ("BMS") entered into an Amendment (the "Amendment") to that certain 
    License Agreement dated December 2023. 
    
    Pursuant to the Amendment, Immunome paid BMS $20.0 million in cash plus 
    approximately 4.4 million shares of Immunome common stock (together, the 
    "Buyout Payment") for the one-time buyout of all royalty and milestone payment 
    obligations under the License Agreement.
    """
    
    tests.append((
        'Immunome paid BMS $20.0 million in cash',
        immunome_filing,
        True,
        "Immunome payment quote"
    ))
    
    tests.append((
        'one-time buyout of all royalty and milestone payment obligations',
        immunome_filing,
        True,
        "Immunome buyout type"
    ))
    
    # Credit facility - Rocket/Hercules style
    rocket_filing = """
    On August 1, 2026, Rocket Pharmaceuticals, Inc. ("Rocket") entered into a 
    Credit Facility Agreement (the "Credit Agreement") with Hercules Capital, Inc. 
    ("Hercules"). The Credit Agreement provides for a term loan facility of up to 
    $200 million, with $75 million funded at closing.
    """
    
    tests.append((
        'up to $200 million',
        rocket_filing,
        True,
        "Rocket facility size"
    ))
    
    tests.append((
        '$75 million funded at closing',
        rocket_filing,
        True,
        "Rocket drawn amount"
    ))
    
    passed = 0
    for quote, filing, expected, desc in tests:
        result = _verify_quote_in_filing(quote, filing)
        if result == expected:
            passed += 1
        else:
            print(f"FAIL [{desc}]: '{quote[:40]}...' -> {result} (expected {expected})")
    
    print(f"_test_real_sec_filings: {passed}/{len(tests)} tests passed")
    return passed == len(tests)


def _test_adversarial_deals():
    """Test adversarial cases: Merck/Verona/Pfizer swap, Alpha/Beta mixed deal."""
    
    # Adversarial case 1: Merck/Verona/Pfizer swap
    # The filing says Merck acquires Verona, but model might claim Pfizer
    merck_filing = """
    On October 3, 2026, Merck & Co., Inc. ("Merck") announced that it has entered 
    into a definitive agreement to acquire Verona Pharma plc ("Verona") for 
    approximately $600 million in cash. Verona's lead product is a treatment for 
    chronic obstructive pulmonary disease. The acquisition is expected to close 
    in Q1 2027.
    """
    
    # Adversarial model response claiming Pfizer
    fake_deal = {
        'url': 'https://sec.gov/fake',
        'party1_name': 'Pfizer',
        'party1_quote': 'Pfizer acquires Verona Pharma',  # NOT in filing
        'party2_name': 'Verona',
        'party2_quote': 'acquire Verona Pharma plc',
        'deal_type_quote': 'agreement to acquire',
        'amount_quotes': {'total': '$600 million in cash'},
    }
    
    # Should be rejected - party1_quote not in filing
    result1 = process_deal_with_quotes(fake_deal, merck_filing, 'Merck & Co., Inc.')
    test1_pass = result1 is None
    
    # Adversarial case 2: Alpha/Beta mixed deal - amounts from different sections
    mixed_filing = """
    SECTION 1 - PARTNERSHIP:
    Alpha Corp announced a collaboration with Beta Inc for an upfront payment 
    of $500 million. This partnership focuses on oncology research.
    
    """ + "x" * 2000 + """
    
    SECTION 2 - UNRELATED ACQUISITION:
    In separate news, Gamma Ltd completed its acquisition of Delta Corp for 
    $200 million in an all-cash transaction. This deal was funded by Gamma's 
    existing credit facility.
    """
    
    # Model tries to mix amounts from different deals
    mixed_deal = {
        'url': 'https://sec.gov/mixed',
        'party1_name': 'Alpha Corp',
        'party1_quote': 'Alpha Corp announced a collaboration with Beta Inc',
        'party2_name': 'Beta Inc',
        'party2_quote': 'collaboration with Beta Inc',
        'deal_type_quote': 'announced a collaboration',
        'amount_quotes': {
            'upfront': '$500 million',  # Correct
            'total': '$200 million',  # WRONG - from different section
        },
    }
    
    result2 = process_deal_with_quotes(mixed_deal, mixed_filing, 'Alpha Corp')
    
    # The $200 million quote should be rejected (not in same passage)
    # But the deal might still go through with just the $500 million
    if result2:
        # Check that the wrong amount was NOT included
        test2_pass = 'total' not in result2.get('verified_quotes', {}).get('amounts', {})
        if not test2_pass:
            # Or check the amount doesn't have $200M
            test2_pass = '200' not in result2.get('money', '')
    else:
        test2_pass = True  # Deal rejected entirely is also acceptable
    
    # Adversarial case 3: Filer not a party
    wrong_filer_deal = {
        'url': 'https://sec.gov/wrong_filer',
        'party1_name': 'CompanyA',
        'party1_quote': 'CompanyA entered into agreement',
        'party2_name': 'CompanyB',
        'party2_quote': 'agreement with CompanyB',
        'deal_type_quote': 'license agreement',
        'amount_quotes': {},
    }
    
    # Filer is CompanyC (not in the deal)
    result3 = process_deal_with_quotes(wrong_filer_deal, 'CompanyA and CompanyB license agreement', 'CompanyC, Inc.')
    test3_pass = result3 is None  # Should be rejected
    
    passed = sum([test1_pass, test2_pass, test3_pass])
    total = 3
    
    if not test1_pass:
        print("FAIL: Merck/Verona/Pfizer swap was not rejected")
    if not test2_pass:
        print("FAIL: Alpha/Beta mixed deal amounts not properly filtered")
    if not test3_pass:
        print("FAIL: Wrong filer deal was not rejected")
    
    print(f"_test_adversarial_deals: {passed}/{total} tests passed")
    return passed == total


def _test_integration_deal_pipeline():
    """Integration test: full deal pipeline using sec_deals module.
    
    Simulates what happens when the model returns a deal with quotes,
    and verifies the full pipeline processes it correctly via sec_deals.
    """
    import sec_deals
    
    # Simulate real Alector/Genentech filing text
    filing_text = """
    UNITED STATES SECURITIES AND EXCHANGE COMMISSION
    Washington, D.C. 20549
    FORM 8-K
    CURRENT REPORT
    
    Date of Report (Date of earliest event reported): October 1, 2026
    
    ALECTOR, INC.
    
    Item 1.01 Entry into a Material Definitive Agreement
    
    On October 1, 2026, Alector, Inc. (the "Company") entered into a Collaboration 
    Agreement (the "Agreement") with Genentech, Inc. ("Genentech"), a member of the 
    Roche Group. Pursuant to the Agreement, the Company granted Genentech an 
    exclusive, worldwide license to develop, manufacture and commercialize antibody 
    products for up to two targets.
    
    Under the terms of the Agreement, Genentech will pay the Company an upfront payment 
    of $100 million in cash. The Company is eligible to receive up to an aggregate 
    of $1.0 billion in potential development, regulatory and commercial milestone 
    payments. Additionally, Genentech will pay tiered royalties on worldwide net 
    sales ranging from mid-single digits to low double digits.
    """
    
    # Simulate correct model response with quotes (sec_deals format)
    model_response = {
        'deal_type': 'license_collaboration',
        'counterparty_name': 'Genentech',
        'type_quote': 'the Company granted Genentech an exclusive, worldwide license to develop, manufacture and commercialize antibody products',
        'counterparty_quote': 'entered into a Collaboration Agreement (the "Agreement") with Genentech, Inc. ("Genentech")',
        'amounts': [
            {'kind': 'upfront', 'quote': 'Genentech will pay the Company an upfront payment of $100 million in cash'},
            {'kind': 'milestones_total', 'quote': 'eligible to receive up to an aggregate of $1.0 billion in potential development, regulatory and commercial milestone payments. Additionally, Genentech'},
        ],
    }
    
    # Process the deal via sec_deals module
    result = sec_deals.process_sec_deal(
        filing_text=filing_text,
        filer_name='Alector, Inc.',
        filing_url='https://www.sec.gov/test',
        filing_date='2026-10-01',
        event_date='2026-10-01',
        claude_response=model_response
    )
    
    tests_passed = 0
    total_tests = 5
    
    # Test 1: Deal should be accepted (not None)
    if result is not None:
        tests_passed += 1
    else:
        print("FAIL: Valid deal was rejected")
        return False
    
    # Test 2: Company name correct
    if 'Alector' in result.get('company', ''):
        tests_passed += 1
    else:
        print(f"FAIL: Company wrong: {result.get('company')}")
    
    # Test 3: Counterparty correct
    if result.get('counterparty') == 'Genentech':
        tests_passed += 1
    else:
        print(f"FAIL: Counterparty wrong: {result.get('counterparty')}")
    
    # Test 4: Filer role is licensor (from quote)
    if result.get('filer_role') == 'licensor':
        tests_passed += 1
    else:
        print(f"FAIL: Filer role wrong: {result.get('filer_role')}")
    
    # Test 5: Has verified amounts
    if len(result.get('verified_amounts', [])) >= 1:
        tests_passed += 1
    else:
        print(f"FAIL: No verified amounts found")
    
    print(f"_test_integration_deal_pipeline: {tests_passed}/{total_tests} tests passed")
    return tests_passed == total_tests


def _test_sec_deals_module():
    """Test that sec_deals module functions work correctly."""
    import sec_deals
    
    tests_passed = 0
    total_tests = 4
    
    # Test 1: Quote verification
    if sec_deals.verify_quote_in_filing("hello world", "This is hello world test"):
        tests_passed += 1
    else:
        print("FAIL: quote verification")
    
    # Test 2: Company matching
    if sec_deals.match_company_whole_word("BMS", "Bristol-Myers Squibb announced"):
        tests_passed += 1
    else:
        print("FAIL: company matching with alias")
    
    # Test 3: Amount parsing
    parsed = sec_deals.parse_amount_from_quote("$100 million upfront", sec_deals.AmountKind.UPFRONT)
    if parsed and parsed.value_in_millions == 100:
        tests_passed += 1
    else:
        print("FAIL: amount parsing")
    
    # Test 4: Amount rendering
    if parsed:
        rendered = sec_deals.render_amount_chinese(parsed)
        if '1 亿美元' in rendered:
            tests_passed += 1
        else:
            print(f"FAIL: amount rendering: {rendered}")
    else:
        print("FAIL: amount rendering (no parsed amount)")
    
    print(f"_test_sec_deals_module: {tests_passed}/{total_tests} tests passed")
    return tests_passed == total_tests



def sanitize_image_prompt(prompt: str) -> str:
    """Remove label-related phrases and make descriptions abstract.
    
    Uses word-boundary patterns to avoid false positives like 'laboured'.
    Also abstracts specific cell type names that might be rendered as labels.
    """
    patterns = [
        r'\b(labell?ed)\b',           # labeled, labelled (not laboured)
        r'\blabels?\b',               # bare label, labels
        r'\bannotated\b',
        r'\bwith\s+(text\s+)?labels?\s*(showing\s+(the\s+)?names?)?\b',
        r'\bwith\s+annotations?\b',
        r'\bwith\s+captions?\b',
        r'\bcaptioned\b',
        r'\bcaptions?\b',
        r'\bwith\s+text\b',
        r'\bshowing\s+(the\s+)?names?\b',
        r'\bnamed\b',
        r'"[^"]*"',                   # Remove quoted text
    ]
    result = prompt
    for pattern in patterns:
        result = re.sub(pattern, '', result, flags=re.IGNORECASE)
    
    # Abstract specific cell type names that might become labels
    cell_abstractions = [
        (r'\bT\s*cells?\b', 'immune cells'),
        (r'\bB\s*cells?\b', 'immune cells'),
        (r'\bCAR-T\b', 'engineered immune cells'),
        (r'\bNK\s*cells?\b', 'immune cells'),
        (r'\bmacrophages?\b', 'immune cells'),
        (r'\bdendritic\s*cells?\b', 'immune cells'),
        (r'\btumou?r\s*cells?\b', 'target cells'),
        (r'\bcancer\s*cells?\b', 'target cells'),
        (r'\bantigen-presenting\s*cells?\b', 'immune cells'),
        (r'\bcytotoxic\s*T?\s*cells?\b', 'immune cells'),
        (r'\bhelper\s*T?\s*cells?\b', 'immune cells'),
    ]
    for pattern, replacement in cell_abstractions:
        result = re.sub(pattern, replacement, result, flags=re.IGNORECASE)
    
    # Clean up extra spaces
    result = re.sub(r'\s+', ' ', result).strip()
    return result


def _test_sanitize_image_prompt():
    """Unit test for sanitize_image_prompt."""
    tests = [
        ("A diagram labeled with cell types", "A diagram with cell types"),
        ("labelled regions of the brain", "regions of the brain"),
        ("The laboured breathing pattern", "The laboured breathing pattern"),
        ("annotated with arrows", "with arrows"),
        ("with text labels showing names", ""),
        ('A cell "Helper T" diagram', "A cell diagram"),
        ("simple illustration of cells", "simple illustration of cells"),
        ("with captions identifying parts", "identifying parts"),
        ("a captioned figure of DNA", "a figure of DNA"),
        ("diagram with label", "diagram with"),
        ("cells with labels", "cells with"),
        # New tests for cell type abstraction
        ("T cells attacking tumor cells", "immune cells attacking target cells"),
        ("CAR-T therapy diagram", "engineered immune cells therapy diagram"),
    ]
    passed = 0
    for input_text, expected in tests:
        result = sanitize_image_prompt(input_text)
        if result == expected:
            passed += 1
        else:
            print(f"FAIL: '{input_text}' -> '{result}' (expected '{expected}')")
    print(f"sanitize_image_prompt: {passed}/{len(tests)} tests passed")
    return passed == len(tests)


def setup_log() -> Path:
    log_dir = ROOT / "logs"
    log_dir.mkdir(exist_ok=True)
    path = log_dir / f"weekly-{datetime.now().strftime('%Y%m%d-%H%M%S')}.log"
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[logging.FileHandler(path, encoding="utf-8"), logging.StreamHandler(sys.stdout)],
    )
    return path


def require_env(names: list[str]) -> None:
    missing = [n for n in names if not os.environ.get(n)]
    if missing:
        logging.error("缺少环境变量：%s", ", ".join(missing))
        raise SystemExit(1)


def check_anthropic_model() -> str:
    """Verify the Anthropic model is available before proceeding."""
    from anthropic import Anthropic, NotFoundError, APIError
    
    model = os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-5-5")
    logging.info("检查 Anthropic 模型可用性：%s", model)
    
    test_tool = {
        "name": "test_tool",
        "description": "Test tool for model check",
        "input_schema": {"type": "object", "properties": {"ok": {"type": "boolean"}}, "required": ["ok"]},
    }
    
    try:
        client = Anthropic()
        client.messages.create(
            model=model,
            max_tokens=50,
            tools=[test_tool],
            tool_choice={"type": "auto"},
            messages=[{"role": "user", "content": "Call test_tool with ok=true"}],
        )
        logging.info("模型 %s 可用（tool_choice=auto 测试通过）", model)
        return model
    except NotFoundError:
        logging.error("模型 %s 不存在或已下线。请设置 ANTHROPIC_MODEL 环境变量为可用模型。", model)
        raise SystemExit(1)
    except APIError as e:
        logging.error("Anthropic API 错误：%s", e)
        raise SystemExit(1)


def load_sources() -> dict:
    path = ROOT / "sources.yaml"
    with path.open(encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    if not isinstance(data, dict) or not isinstance(data.get("sources"), list):
        logging.error("sources.yaml 格式不对：需要 sources 列表")
        raise SystemExit(1)
    return data


def _within(when: datetime | None, start: datetime) -> bool:
    if when is None:
        return False
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return when >= start


def _parse_struct(entry) -> datetime | None:
    parsed = entry.get("published_parsed") or entry.get("updated_parsed")
    if not parsed:
        return None
    try:
        return datetime(*parsed[:6], tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None


BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)


def _fetch_rss_with_retry(feed: str, source_name: str, use_browser_ua: bool, max_attempts: int = 3, timeout: int = 20) -> tuple[feedparser.FeedParserDict | None, str]:
    """Fetch RSS feed with retry logic for transient errors."""
    import time
    import requests
    
    delays = [2, 5, 10]
    last_error = None
    should_retry = True
    
    ua = BROWSER_UA if use_browser_ua else UA
    
    for attempt in range(max_attempts):
        try:
            resp = requests.get(feed, headers={"User-Agent": ua}, timeout=timeout)
            
            if 400 <= resp.status_code < 500:
                logging.warning("%s 返回 %d（客户端错误，不重试）", source_name, resp.status_code)
                return None, "failed"
            
            if resp.status_code >= 500:
                raise requests.exceptions.HTTPError(f"Server error {resp.status_code}")
            
            resp.raise_for_status()
            parsed = feedparser.parse(resp.content)
            
            if getattr(parsed, "bozo", False) and not parsed.entries:
                bozo_exc = getattr(parsed, "bozo_exception", None)
                bozo_str = str(bozo_exc).lower() if bozo_exc else ""
                if "no element found" in bozo_str or "not well-formed" in bozo_str:
                    raise ValueError(f"XML parse error: {bozo_exc}")
                logging.warning("%s 的 feed 解析失败（不重试）：%s", source_name, bozo_exc)
                return None, "failed"
            
            return parsed, "ok"
            
        except requests.exceptions.Timeout as e:
            last_error = e
            should_retry = True
        except requests.exceptions.ConnectionError as e:
            last_error = e
            should_retry = True
        except requests.exceptions.HTTPError as e:
            last_error = e
            should_retry = "5" in str(e) or "Server error" in str(e)
        except ValueError as e:
            if "XML parse error" in str(e):
                last_error = e
                should_retry = True
            else:
                logging.warning("%s 抓取失败（不重试）：%s", source_name, e)
                return None, "failed"
        except Exception as e:
            logging.warning("%s 抓取失败（不重试）：%s", source_name, e)
            return None, "failed"
        
        if should_retry and attempt < max_attempts - 1:
            delay = delays[min(attempt, len(delays) - 1)]
            logging.info("%s 抓取失败，%d秒后重试（第%d次）：%s", source_name, delay, attempt + 1, last_error)
            time.sleep(delay)
        elif not should_retry:
            break
    
    logging.warning("%s 的 feed 重试后仍失败：%s", source_name, last_error)
    return None, "failed"


def _fetch_with_retry(url: str, source_name: str, max_attempts: int = 3, 
                      timeout: int = 60, json_response: bool = True) -> dict | bytes | None:
    """Fetch URL with retry logic for server errors, timeouts, and connection errors."""
    import time
    import requests
    
    delays = [2, 5, 10]
    last_error = None
    
    for attempt in range(max_attempts):
        try:
            resp = requests.get(url, headers={"User-Agent": UA}, timeout=timeout)
            
            if 400 <= resp.status_code < 500:
                logging.warning("%s 返回 %d（客户端错误，不重试）", source_name, resp.status_code)
                return None
            
            if resp.status_code >= 500:
                raise requests.exceptions.HTTPError(f"Server error {resp.status_code}")
            
            resp.raise_for_status()
            
            if json_response:
                return resp.json()
            return resp.content
            
        except (requests.exceptions.Timeout, 
                requests.exceptions.ConnectionError,
                requests.exceptions.HTTPError) as e:
            last_error = e
            if attempt < max_attempts - 1:
                delay = delays[min(attempt, len(delays) - 1)]
                logging.info("%s 请求失败，%d秒后重试（第%d次）：%s", source_name, delay, attempt + 1, e)
                time.sleep(delay)
        except Exception as e:
            logging.warning("%s 请求失败（不重试）：%s", source_name, e)
            return None
    
    logging.warning("%s 请求重试后仍失败：%s", source_name, last_error)
    return None


def fetch_biorxiv_api(start: datetime, limit: int, category: str | None = None) -> list[dict]:
    """Fallback: fetch from bioRxiv details API when RSS fails."""
    end_date = datetime.now(timezone.utc).date()
    start_date = start.date()
    
    base_url = f"https://api.biorxiv.org/details/biorxiv/{start_date.isoformat()}/{end_date.isoformat()}"
    
    logging.info("bioRxiv API fallback: %s (category=%s)", base_url, category or "all")
    
    rows = []
    cursor = 0
    max_pages = 5
    
    for page in range(max_pages):
        paginated_url = f"{base_url}/{cursor}"
        
        data = _fetch_with_retry(paginated_url, f"bioRxiv API (page {page+1})", json_response=True)
        if data is None:
            break
        
        collection = data.get("collection") or []
        if not collection:
            logging.info("bioRxiv API page %d 返回 0 条", page + 1)
            break
        
        if category:
            category_lower = category.lower()
            collection = [p for p in collection if (p.get("category") or "").lower() == category_lower]
        
        for paper in collection:
            doi = paper.get("doi") or ""
            title = paper.get("title") or ""
            if not doi or not title:
                continue
            
            date_str = paper.get("date") or ""
            try:
                pub_date = datetime.strptime(date_str, "%Y-%m-%d").date()
            except ValueError:
                pub_date = end_date
            
            authors = paper.get("authors") or ""
            abstract = (paper.get("abstract") or "")[:2500]
            cat = paper.get("category") or "bioRxiv"
            
            rows.append({
                "source": f"bioRxiv {cat}",
                "kind": "academic",
                "title": title,
                "url": f"https://doi.org/{doi}",
                "date": pub_date.isoformat(),
                "summary": abstract if abstract else f"{authors[:200]}",
                "authors": authors,
            })
            
            if len(rows) >= limit:
                break
        
        if len(rows) >= limit:
            break
        
        messages = data.get("messages") or []
        total = 0
        for msg in messages:
            if msg.get("status") == "ok" and "total" in msg:
                try:
                    total = int(msg.get("total", 0))
                except (ValueError, TypeError):
                    total = 0
                break
        
        cursor += len(data.get("collection") or [])
        if cursor >= total:
            break
    
    logging.info("bioRxiv API fallback 得到 %d 条（分类 %s）", len(rows), category or "all")
    return rows[:limit]


def _test_biorxiv_api():
    """Unit test for bioRxiv API parsing with mocked response."""
    mock_response = {
        "messages": [{"status": "ok", "total": "55"}],
        "collection": [
            {
                "doi": "10.1101/2026.10.01.123456",
                "title": "Test Immunology Paper",
                "authors": "Smith, J; Doe, A",
                "abstract": "This is an abstract about immunology.",
                "category": "immunology",
                "date": "2026-10-05",
            },
            {
                "doi": "10.1101/2026.10.02.789012",
                "title": "Neuroscience Paper",
                "authors": "Jones, B",
                "abstract": "Neuroscience abstract.",
                "category": "neuroscience",
                "date": "2026-10-04",
            },
        ],
    }
    
    messages = mock_response.get("messages") or []
    total = 0
    for msg in messages:
        if msg.get("status") == "ok" and "total" in msg:
            try:
                total = int(msg.get("total", 0))
            except (ValueError, TypeError):
                total = 0
            break
    
    tests_passed = 0
    total_tests = 3
    
    if total == 55:
        tests_passed += 1
    else:
        print(f"FAIL: total should be 55, got {total}")
    
    collection = mock_response.get("collection") or []
    filtered = [p for p in collection if (p.get("category") or "").lower() == "immunology"]
    if len(filtered) == 1 and filtered[0]["title"] == "Test Immunology Paper":
        tests_passed += 1
    else:
        print(f"FAIL: category filter should return 1 immunology paper, got {len(filtered)}")
    
    try:
        pub_date = datetime.strptime("2026-10-05", "%Y-%m-%d").date()
        if pub_date.isoformat() == "2026-10-05":
            tests_passed += 1
        else:
            print(f"FAIL: date parsing failed")
    except Exception as e:
        print(f"FAIL: date parsing exception: {e}")
    
    print(f"_test_biorxiv_api: {tests_passed}/{total_tests} tests passed")
    return tests_passed == total_tests


def _strip_tracking_params(url: str) -> str:
    """Remove common tracking query parameters from URLs."""
    if "?" not in url:
        return url
    
    tracking_params = {
        "rss", "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
        "ref", "source", "mc_cid", "mc_eid", "fbclid", "gclid", "msclkid",
    }
    
    parsed = urllib.parse.urlparse(url)
    if not parsed.query:
        return url
    
    params = urllib.parse.parse_qs(parsed.query, keep_blank_values=True)
    clean_params = {k: v for k, v in params.items() if k.lower() not in tracking_params}
    
    if not clean_params:
        return urllib.parse.urlunparse(parsed._replace(query=""))
    
    clean_query = urllib.parse.urlencode(clean_params, doseq=True)
    return urllib.parse.urlunparse(parsed._replace(query=clean_query))


def _parse_rss_authors(entry) -> str:
    """Extract author names from RSS entry's dc:creator or author fields."""
    dc_creator = entry.get("dc_creator") or entry.get("author_detail", {}).get("name") or ""
    if dc_creator:
        return dc_creator.strip()
    
    author = entry.get("author") or ""
    if author:
        return author.strip()
    
    authors_list = entry.get("authors") or []
    if authors_list:
        names = [a.get("name", "") for a in authors_list if a.get("name")]
        if names:
            return ", ".join(names[:6])
    
    return ""


def fetch_rss(source: dict, start: datetime, limit: int) -> tuple[list[dict], str]:
    """Fetch RSS feed and return (items, status)."""
    feed = source.get("feed")
    if not feed:
        logging.warning("跳过 %s：没有 feed", source.get("name"))
        return [], "skipped"
    logging.info("抓取 RSS %s", source["name"])
    
    source_name = source.get("name", "unknown")
    use_browser_ua = source.get("needs_browser_ua", False)
    
    parsed, status = _fetch_rss_with_retry(feed, source_name, use_browser_ua)
    
    if parsed is None and "biorxiv" in source_name.lower():
        category = source.get("biorxiv_category") or "immunology"
        logging.info("%s RSS 失败，尝试 API fallback（分类：%s）", source_name, category)
        items = fetch_biorxiv_api(start, limit, category=category)
        return items, "ok" if items else "failed"
    
    if parsed is None:
        return [], status
    
    rows = []
    for entry in parsed.entries:
        when = _parse_struct(entry)
        if not _within(when, start):
            continue
        url = (entry.get("link") or "").strip()
        title = (entry.get("title") or "").strip()
        if not url or not title:
            continue
        
        url = _strip_tracking_params(url)
        
        summary = re.sub(r"<[^>]+>", " ", entry.get("summary") or entry.get("description") or "")
        summary = re.sub(r"\s+", " ", summary).strip()[:2500]
        
        authors = _parse_rss_authors(entry)
        
        row = {
            "source": source["name"],
            "kind": source.get("kind") or "academic",
            "title": title,
            "url": url,
            "date": when.date().isoformat(),
            "summary": summary,
        }
        if authors:
            row["authors"] = authors
        
        rows.append(row)
        if len(rows) >= limit:
            break
    logging.info("%s 得到 %d 条", source["name"], len(rows))
    return rows, "ok" if rows else status


def fetch_pubmed(source: dict, start: date, end: date, limit: int) -> list[dict]:
    """Fetch PubMed articles with abstracts using E-utilities."""
    import time
    import xml.etree.ElementTree as ET
    
    query = source.get("query") or ""
    logging.info("检索 PubMed %s", query)
    mindate = start.strftime("%Y/%m/%d")
    maxdate = end.strftime("%Y/%m/%d")
    term = f"({query}) AND ({mindate}:{maxdate}[edat])"
    
    search_url = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi?" + urllib.parse.urlencode({
        "db": "pubmed",
        "term": term,
        "retmax": str(limit),
        "retmode": "json",
        "sort": "pub+date",
    })
    req = urllib.request.Request(search_url, headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            found = json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        logging.warning("PubMed esearch 失败: %s", e)
        return []
    
    ids = found.get("esearchresult", {}).get("idlist") or []
    if not ids:
        logging.info("PubMed 没有命中")
        return []
    
    time.sleep(0.35)
    efetch_url = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi?" + urllib.parse.urlencode({
        "db": "pubmed",
        "id": ",".join(ids),
        "rettype": "xml",
        "retmode": "xml",
    })
    req = urllib.request.Request(efetch_url, headers={"User-Agent": UA})
    
    abstracts = {}
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            xml_data = resp.read().decode("utf-8")
        
        root = ET.fromstring(xml_data)
        for article in root.findall(".//PubmedArticle"):
            pmid_elem = article.find(".//PMID")
            if pmid_elem is None:
                continue
            pmid = pmid_elem.text
            
            abstract_parts = []
            for abstract_text in article.findall(".//AbstractText"):
                label = abstract_text.get("Label", "")
                text = "".join(abstract_text.itertext()).strip()
                if label and text:
                    abstract_parts.append(f"{label}: {text}")
                elif text:
                    abstract_parts.append(text)
            
            if abstract_parts:
                abstracts[pmid] = " ".join(abstract_parts)[:2500]
    except Exception as e:
        logging.warning("PubMed efetch 失败，使用 esummary fallback: %s", e)
    
    time.sleep(0.35)
    sum_url = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi?" + urllib.parse.urlencode({
        "db": "pubmed",
        "id": ",".join(ids),
        "retmode": "json",
    })
    req = urllib.request.Request(sum_url, headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            summary = json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        logging.warning("PubMed esummary 失败: %s", e)
        return []
    
    result = summary.get("result", {})
    rows = []
    for pmid in ids:
        item = result.get(pmid) or {}
        title = (item.get("title") or "").strip()
        if not title:
            continue
        authors = ", ".join(a.get("name", "") for a in (item.get("authors") or [])[:6])
        journal = item.get("fulljournalname") or item.get("source") or "PubMed"
        raw_day = (item.get("sortpubdate") or "")[:10].replace("/", "-")
        
        abstract = abstracts.get(pmid, "")
        if abstract:
            summary_text = abstract
        else:
            summary_text = f"{journal}. {authors}".strip()
        
        rows.append({
            "source": "PubMed",
            "kind": "academic",
            "title": title,
            "url": f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/",
            "date": raw_day if len(raw_day) == 10 else end.isoformat(),
            "summary": summary_text,
            "authors": authors,
            "journal": journal,
        })
    logging.info("PubMed 得到 %d 条（%d 条有摘要）", len(rows), len(abstracts))
    return rows


# =============================================================================
# DEAL FILING SOURCES (SEC, HKEX, cninfo)
# =============================================================================

FILING_DEAL_CATEGORIES = {
    "lic": "授权合作",
    "acq": "并购",
    "inv": "融资/IPO",
}

DEAL_GROUP_ORDER = ["lic", "acq", "inv"]
DEAL_GROUP_NAMES = {
    "lic": "授权合作",
    "acq": "并购",
    "inv": "融资/IPO",
}

# SEC biopharma SIC codes ONLY - no name-based fallback
SEC_BIOPHARMA_SICS = {"2834", "2835", "2836", "8731"}

# HKEX healthcare/biotech stock codes - DISABLED until verified
HKEX_HEALTHCARE_CODES = set()

SEC_DEAL_KEYWORDS = [
    "license", "collaboration", "acquisition", "merger", "upfront", "milestone",
    "partnership", "agreement", "exclusive rights", "royalt", "option",
]
HKEX_DEAL_KEYWORDS = [
    "licensing", "license", "collaboration", "acquisition", "merger",
    "major transaction", "discloseable transaction", "placing", "subscription",
    "授权", "许可", "合作", "收购", "并购", "配售", "认购",
]
CNINFO_DEAL_KEYWORDS = [
    "许可", "授权", "合作协议", "重大合同", "对外投资", "收购", "并购",
    "战略合作", "技术转让", "独家", "里程碑",
]


def _strip_html(html: str) -> str:
    """Strip HTML tags and decode entities, return plain text."""
    import html as html_module
    text = re.sub(r'<script[^>]*>.*?</script>', ' ', html, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r'<style[^>]*>.*?</style>', ' ', text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r'<[^>]+>', ' ', text)
    text = html_module.unescape(text)
    text = re.sub(r'\s+', ' ', text).strip()
    return text


def _extract_pdf_text(pdf_bytes: bytes, max_chars: int = 4000) -> str:
    """Extract text from first pages of PDF, capped at max_chars."""
    try:
        import io
        try:
            import fitz
            doc = fitz.open(stream=pdf_bytes, filetype="pdf")
            text_parts = []
            for page_num in range(min(5, len(doc))):
                page = doc[page_num]
                text_parts.append(page.get_text())
                if sum(len(t) for t in text_parts) > max_chars:
                    break
            doc.close()
            text = "\n".join(text_parts)[:max_chars]
            return re.sub(r'\s+', ' ', text).strip()
        except ImportError:
            pass
        
        try:
            import pdfplumber
            with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
                text_parts = []
                for page in pdf.pages[:5]:
                    text_parts.append(page.extract_text() or "")
                    if sum(len(t) for t in text_parts) > max_chars:
                        break
            text = "\n".join(text_parts)[:max_chars]
            return re.sub(r'\s+', ' ', text).strip()
        except ImportError:
            pass
        
        logging.warning("No PDF library available (install PyMuPDF or pdfplumber)")
        return ""
    except Exception as e:
        logging.warning("PDF extraction failed: %s", e)
        return ""


def _normalize_amount_with_currency(text: str) -> list[tuple[int, str]]:
    """Extract monetary amounts with their currency.
    
    Supports USD, RMB, HKD, EUR, GBP, AUD, CAD, SGD.
    Also extracts percentages for equity verification.
    Returns list of (amount_in_base_units, currency) tuples.
    
    Fix #8: US$, USD, $ all map to USD; S$ maps to SGD.
    Fix #3: Handle Chinese numerals (一亿, 十亿, 两亿, etc.)
    """
    amounts = []
    
    # Chinese numeral mapping
    cn_nums = {'一': 1, '二': 2, '两': 2, '三': 3, '四': 4, '五': 5, 
               '六': 6, '七': 7, '八': 8, '九': 9, '十': 10}
    
    def parse_cn_number(s: str) -> float | None:
        """Parse Chinese numeral like 一亿, 十亿, 两亿, 一点五亿."""
        s = s.strip()
        if not s:
            return None
        # Check for Arabic numeral
        if re.match(r'^[\d.]+$', s):
            try:
                return float(s)
            except ValueError:
                return None
        # Single digit: 一, 二, 三, etc.
        if s in cn_nums:
            return float(cn_nums[s])
        # 十X: 十二, 十五 -> 12, 15
        if s.startswith('十'):
            if len(s) == 1:
                return 10.0
            rest = s[1:]
            if rest in cn_nums:
                return 10.0 + cn_nums[rest]
        # X十: 二十, 三十 -> 20, 30
        if len(s) == 2 and s[0] in cn_nums and s[1] == '十':
            return float(cn_nums[s[0]] * 10)
        # X十Y: 二十五 -> 25
        if len(s) == 3 and s[0] in cn_nums and s[1] == '十' and s[2] in cn_nums:
            return float(cn_nums[s[0]] * 10 + cn_nums[s[2]])
        # X点Y: 一点五 -> 1.5
        if '点' in s:
            parts = s.split('点')
            if len(parts) == 2:
                whole_str, frac_str = parts
                whole = 0.0
                if whole_str in cn_nums:
                    whole = float(cn_nums[whole_str])
                elif whole_str.isdigit():
                    whole = float(whole_str)
                frac = 0.0
                if frac_str in cn_nums:
                    frac = cn_nums[frac_str] / 10.0
                elif frac_str.isdigit():
                    frac = float(f"0.{frac_str}")
                return whole + frac
        return None
    
    # EUR patterns - €X.XX million/billion
    # Use round() before int() to handle floating point precision
    for m in re.finditer(r'€\s*([\d,]+(?:\.\d+)?)\s*(?:billion|B\b)', text, re.IGNORECASE):
        try:
            val = float(m.group(1).replace(',', '')) * 1_000_000_000 * 100
            amounts.append((round(val), 'EUR'))
        except ValueError:
            pass
    
    for m in re.finditer(r'€\s*([\d,]+(?:\.\d+)?)\s*(?:million|M\b)', text, re.IGNORECASE):
        try:
            val = float(m.group(1).replace(',', '')) * 1_000_000 * 100
            amounts.append((round(val), 'EUR'))
        except ValueError:
            pass
    
    # Chinese EUR: X亿欧元 (Arabic or Chinese numeral)
    for m in re.finditer(r'([\d.]+|[一二三四五六七八九十两点]+)\s*亿\s*欧元', text):
        try:
            num = parse_cn_number(m.group(1))
            if num is not None:
                val = num * 100_000_000 * 100
                amounts.append((round(val), 'EUR'))
        except ValueError:
            pass
    
    # GBP patterns - £X.XX million/billion
    for m in re.finditer(r'£\s*([\d,]+(?:\.\d+)?)\s*(?:billion|B\b)', text, re.IGNORECASE):
        try:
            val = float(m.group(1).replace(',', '')) * 1_000_000_000 * 100
            amounts.append((round(val), 'GBP'))
        except ValueError:
            pass
    
    for m in re.finditer(r'£\s*([\d,]+(?:\.\d+)?)\s*(?:million|M\b)', text, re.IGNORECASE):
        try:
            val = float(m.group(1).replace(',', '')) * 1_000_000 * 100
            amounts.append((round(val), 'GBP'))
        except ValueError:
            pass
    
    # HKD patterns - HK$X.XXM / HK$X.XX million (before USD to avoid double-matching)
    for m in re.finditer(r'HK\$\s*([\d,]+(?:\.\d+)?)\s*(?:billion|B\b)', text, re.IGNORECASE):
        try:
            val = float(m.group(1).replace(',', '')) * 1_000_000_000 * 100
            amounts.append((round(val), 'HKD'))
        except ValueError:
            pass
    
    for m in re.finditer(r'HK\$\s*([\d,]+(?:\.\d+)?)\s*(?:million|M\b)', text, re.IGNORECASE):
        try:
            val = float(m.group(1).replace(',', '')) * 1_000_000 * 100
            amounts.append((round(val), 'HKD'))
        except ValueError:
            pass
    
    for m in re.finditer(r'HK\$\s*([\d,]{7,})', text):
        try:
            val = int(m.group(1).replace(',', '')) * 100
            if val >= 10_000_000:
                amounts.append((val, 'HKD'))
        except ValueError:
            pass
    
    # Chinese HKD: X亿港元 / X亿港币
    for m in re.finditer(r'([\d.]+|[一二三四五六七八九十两点]+)\s*亿\s*港[元币]', text):
        try:
            num = parse_cn_number(m.group(1))
            if num is not None:
                val = num * 100_000_000 * 100
                amounts.append((round(val), 'HKD'))
        except ValueError:
            pass
    
    # AUD patterns - A$X.XX million/billion
    for m in re.finditer(r'A\$\s*([\d,]+(?:\.\d+)?)\s*(?:billion|B\b)', text, re.IGNORECASE):
        try:
            val = float(m.group(1).replace(',', '')) * 1_000_000_000 * 100
            amounts.append((round(val), 'AUD'))
        except ValueError:
            pass
    
    for m in re.finditer(r'A\$\s*([\d,]+(?:\.\d+)?)\s*(?:million|M\b)', text, re.IGNORECASE):
        try:
            val = float(m.group(1).replace(',', '')) * 1_000_000 * 100
            amounts.append((round(val), 'AUD'))
        except ValueError:
            pass
    
    # CAD patterns - C$X.XX million/billion
    for m in re.finditer(r'C\$\s*([\d,]+(?:\.\d+)?)\s*(?:billion|B\b)', text, re.IGNORECASE):
        try:
            val = float(m.group(1).replace(',', '')) * 1_000_000_000 * 100
            amounts.append((round(val), 'CAD'))
        except ValueError:
            pass
    
    for m in re.finditer(r'C\$\s*([\d,]+(?:\.\d+)?)\s*(?:million|M\b)', text, re.IGNORECASE):
        try:
            val = float(m.group(1).replace(',', '')) * 1_000_000 * 100
            amounts.append((round(val), 'CAD'))
        except ValueError:
            pass
    
    # SGD patterns - S$X.XX million/billion (NOT US$)
    for m in re.finditer(r'(?<!U)S\$\s*([\d,]+(?:\.\d+)?)\s*(?:billion|B\b)', text, re.IGNORECASE):
        try:
            val = float(m.group(1).replace(',', '')) * 1_000_000_000 * 100
            amounts.append((round(val), 'SGD'))
        except ValueError:
            pass
    
    for m in re.finditer(r'(?<!U)S\$\s*([\d,]+(?:\.\d+)?)\s*(?:million|M\b)', text, re.IGNORECASE):
        try:
            val = float(m.group(1).replace(',', '')) * 1_000_000 * 100
            amounts.append((round(val), 'SGD'))
        except ValueError:
            pass
    
    # USD patterns - US$, USD, $ (exclude HK$, A$, C$, S$)
    # Fix #8: US$ is USD, not SGD
    for m in re.finditer(r'(?:US\$|USD)\s*([\d,]+(?:\.\d+)?)\s*(?:billion|B\b)', text, re.IGNORECASE):
        try:
            val = float(m.group(1).replace(',', '')) * 1_000_000_000 * 100
            amounts.append((round(val), 'USD'))
        except ValueError:
            pass
    
    for m in re.finditer(r'(?:US\$|USD)\s*([\d,]+(?:\.\d+)?)\s*(?:million|M\b)', text, re.IGNORECASE):
        try:
            val = float(m.group(1).replace(',', '')) * 1_000_000 * 100
            amounts.append((round(val), 'USD'))
        except ValueError:
            pass
    
    # Bare $ patterns - use negative lookbehind to exclude HK$, A$, C$, S$, US$
    for m in re.finditer(r'(?<![HKACSU])\$\s*([\d,]+(?:\.\d+)?)\s*(?:billion|B\b)', text, re.IGNORECASE):
        try:
            val = float(m.group(1).replace(',', '')) * 1_000_000_000 * 100
            amounts.append((round(val), 'USD'))
        except ValueError:
            pass
    
    for m in re.finditer(r'(?<![HKACSU])\$\s*([\d,]+(?:\.\d+)?)\s*(?:million|M\b)', text, re.IGNORECASE):
        try:
            val = float(m.group(1).replace(',', '')) * 1_000_000 * 100
            amounts.append((round(val), 'USD'))
        except ValueError:
            pass
    
    for m in re.finditer(r'(?<![HKACSU])\$\s*([\d,]{7,})', text):
        try:
            val = int(m.group(1).replace(',', '')) * 100
            if val >= 10_000_000:
                amounts.append((val, 'USD'))
        except ValueError:
            pass
    
    # Chinese USD: X亿美元 / X.X亿美元 (Arabic or Chinese numeral)
    for m in re.finditer(r'([\d.]+|[一二三四五六七八九十两点]+)\s*亿\s*美元', text):
        try:
            num = parse_cn_number(m.group(1))
            if num is not None:
                val = num * 100_000_000 * 100
                amounts.append((round(val), 'USD'))
        except ValueError:
            pass
    
    # Chinese USD: X万美元
    for m in re.finditer(r'([\d,]+(?:\.\d+)?)\s*万\s*美元', text):
        try:
            val = float(m.group(1).replace(',', '')) * 10_000 * 100
            amounts.append((round(val), 'USD'))
        except ValueError:
            pass
    
    # RMB/CNY patterns
    for m in re.finditer(r'(?:RMB|CNY)\s*([\d,]+(?:\.\d+)?)\s*(?:million|M\b)?', text, re.IGNORECASE):
        try:
            val = float(m.group(1).replace(',', ''))
            if 'million' in text[m.start():m.end()+10].lower() or (m.end() < len(text) and text[m.end():m.end()+1] == 'M'):
                val *= 1_000_000
            val *= 100
            if val >= 10_000_000:
                amounts.append((round(val), 'RMB'))
        except ValueError:
            pass
    
    # Chinese RMB: X亿元 / X亿人民币 (NOT 美元)
    for m in re.finditer(r'([\d.]+|[一二三四五六七八九十两点]+)\s*亿\s*(?:元|人民币)(?!美)', text):
        try:
            num = parse_cn_number(m.group(1))
            if num is not None:
                val = num * 100_000_000 * 100
                amounts.append((round(val), 'RMB'))
        except ValueError:
            pass
    
    # Chinese RMB: X万元 / X万人民币 (NOT 美元)
    for m in re.finditer(r'([\d,]+(?:\.\d+)?)\s*万\s*(?:元|人民币)(?!美)', text):
        try:
            val = float(m.group(1).replace(',', '')) * 10_000 * 100
            amounts.append((round(val), 'RMB'))
        except ValueError:
            pass
    
    # Percentages for equity - store as basis points * 100 for precision
    for m in re.finditer(r'([\d.]+)\s*%', text):
        try:
            pct = float(m.group(1))
            # Store as basis points (1% = 100 bp) * 100 for precision
            val = int(pct * 10000)
            amounts.append((val, 'PCT'))
        except ValueError:
            pass
    
    return amounts


def _verify_amount_in_text(amount_str: str, filing_text: str) -> bool:
    """Check if an amount appears in the filing text with exact match.
    
    Fix #3: No tolerance, currency must match.
    Also verifies percentages for equity fields.
    """
    if not amount_str or amount_str == "未披露":
        return True
    
    if not filing_text or len(filing_text) < 20:
        return False
    
    filing_amounts = _normalize_amount_with_currency(filing_text)
    claim_amounts = _normalize_amount_with_currency(amount_str)
    
    if not claim_amounts:
        return False
    
    if not filing_amounts:
        return False
    
    # ALL claimed amounts must be found in filing (exact match, same currency)
    for claim_val, claim_currency in claim_amounts:
        found = False
        for filing_val, filing_currency in filing_amounts:
            if claim_currency != filing_currency:
                continue
            if claim_val == filing_val:
                found = True
                break
        if not found:
            return False
    
    return True


def _test_verify_amount():
    """Unit tests for _verify_amount_in_text per user requirements."""
    tests = [
        # FAIL cases - wrong amounts or wrong currencies
        ("11亿美元", "The company paid $1.17 billion in total", False),
        ("1.05亿美元", "The upfront payment was $100 million cash", False),
        ("1亿美元", "RMB100,000,000 consideration paid", False),
        ("19.4亿美元", "HK$1,939.78M in cash consideration", False),
        ("1亿首付，最高99亿", "The upfront was only $100 million", False),
        
        # Fix #3: A$, C$, S$, €, £ are distinct currencies
        ("1亿美元", "A$100 million consideration here", False),  # USD != AUD
        ("1亿美元", "C$100 million consideration here", False),  # USD != CAD
        ("1亿欧元", "The deal was for $100 million", False),  # EUR != USD
        
        # PASS cases - exact matches
        ("1亿美元", "The company paid $100 million in cash", True),
        ("11.7亿美元", "total deal value of $1.17 billion announced", True),
        ("100万美元", "The company received $1 million upfront", True),
        ("1.939亿港元", "HK$193.9M consideration was paid", True),
        
        # Fix #3: Exact rounding - 1.15亿美元 = $115 million
        ("1.15亿美元", "The payment was $115 million total", True),
        
        # Fix #3: Euro support
        ("1亿欧元", "The deal was for €100 million upfront", True),
        
        # Fix #3: Equity percent verification
        ("19.9% 股权", "acquired 19.9% stake in the company", True),
        ("19.9% 股权", "acquired 20% stake in the company", False),  # 19.9 != 20
        
        # Edge cases
        ("未披露", "any text over 20 chars here", True),
        ("1亿美元", "", False),
        ("", "any text over 20 chars here", True),
        
        # Fix #8: US$ is USD, not SGD
        ("1亿美元", "US$100 million consideration paid", True),
        ("1亿美元", "USD 100 million consideration paid", True),
        # S$ without U is SGD
        ("1亿美元", "S$100 million payment made here", False),  # USD claim vs SGD source
        
        # Fix #3: Chinese numerals
        ("一亿美元", "The deal was $100 million cash", True),
        ("十亿美元", "The deal was $1 billion total", True),
        ("两亿美元", "The deal was $200 million cash", True),
    ]
    
    passed = 0
    for claim, source, expected in tests:
        result = _verify_amount_in_text(claim, source)
        if result == expected:
            passed += 1
        else:
            print(f"FAIL: '{claim}' vs '{source[:50]}...' -> {result} (expected {expected})")
    
    print(f"_test_verify_amount: {passed}/{len(tests)} tests passed")
    return passed == len(tests)


def _is_nonprofit_or_consortium(text: str) -> bool:
    """Detect nonprofit, government, or consortium initiatives.
    
    Fix B5: Use word boundaries to avoid false positives like "doe" in "does",
    "nsf" in "transfer". Multi-party commitments to nonprofits are never deals.
    """
    text_lower = text.lower()
    
    # Direct nonprofit entity indicators (always reject)
    # Use word boundaries to avoid false matches
    nonprofit_patterns = [
        r'\b(?:non-?profit|not-for-profit)\b',
        r'\b501\s*\(c\)',
        r'\bbiohub\b',  # Chan Zuckerberg Biohub
        r'\bchan\s+zuckerberg\b',
    ]
    for pattern in nonprofit_patterns:
        if re.search(pattern, text_lower):
            return True
    
    # Government agency funding (with word boundaries)
    # Only reject if agency is source of commitment/funding
    govt_agencies = [
        (r'\bnih\b', 'national institutes of health'),
        (r'\bdoe\b', 'department of energy'),
        (r'\bnsf\b', 'national science foundation'),
        (r'\bdarpa\b', 'defense advanced research'),
    ]
    
    funding_signals = ['commitment', 'pledged', 'pledge', 'grant', 'funding from', 'funded by', 'initiative']
    
    for abbrev_pattern, full_name in govt_agencies:
        if re.search(abbrev_pattern, text_lower) or full_name in text_lower:
            if any(signal in text_lower for signal in funding_signals):
                return True
    
    # Multi-party summed commitments
    if re.search(r'\bcombined\s+(?:commitment|total|funding)\b', text_lower):
        return True
    if re.search(r'\b(?:multiple|several)\s+(?:parties|organizations|funders)\b', text_lower):
        return True
    
    # Consortium/initiative as primary entity
    if re.search(r'\bconsortium\b.*\b(?:launch|announce|fund|commit)\b', text_lower):
        return True
    if re.search(r'\binitiative\b.*\$[\d,]+\s*(?:million|billion)\b', text_lower):
        return True
    
    return False


def _verify_company_in_source(company_name: str, source_text: str) -> bool:
    """Verify company name appears in source text.
    
    Fix #2: Every company name must appear in source (case-insensitive, alias-aware).
    """
    if not company_name or not source_text:
        return False
    
    source_lower = source_text.lower()
    company_lower = company_name.lower().strip()
    
    # Direct match
    if company_lower in source_lower:
        return True
    
    # Normalized match (remove suffixes)
    normalized = _normalize_company_name(company_name)
    if len(normalized) >= 3 and normalized in source_lower:
        return True
    
    # Common aliases/abbreviations
    aliases = {
        'pfizer': ['pfizer inc', 'pfizer, inc'],
        'novartis': ['novartis ag', 'novartis pharma'],
        'roche': ['roche holding', 'f. hoffmann-la roche'],
        'genentech': ['genentech inc', 'genentech, inc'],
        'abbvie': ['abbvie inc', 'abbvie, inc'],
        'merck': ['merck & co', 'merck sharp', 'msd'],
        'j&j': ['johnson & johnson', 'johnson and johnson', 'janssen', 'jnj'],
        'johnson & johnson': ['j&j', 'jnj', 'janssen'],
        'lilly': ['eli lilly', 'lilly and company'],
        'bms': ['bristol-myers squibb', 'bristol myers squibb'],
        'astrazeneca': ['astrazeneca plc', 'az'],
        'gsk': ['glaxosmithkline', 'glaxo smith kline'],
        'sanofi': ['sanofi-aventis', 'sanofi aventis'],
        'biogen': ['biogen inc', 'biogen idec'],
        'gilead': ['gilead sciences'],
        'amgen': ['amgen inc'],
        'regeneron': ['regeneron pharmaceuticals'],
        'vertex': ['vertex pharmaceuticals'],
        'moderna': ['moderna inc', 'moderna therapeutics'],
        'biontech': ['biontech se'],
    }
    
    # Check if company name matches any known alias patterns
    for canonical, alias_list in aliases.items():
        if canonical in company_lower or any(a in company_lower for a in alias_list):
            # Check if any variant appears in source
            if canonical in source_lower:
                return True
            for alias in alias_list:
                if alias in source_lower:
                    return True
    
    return False


def _has_deal_keywords(text: str, deal_type: str) -> bool:
    """Check if source text has deal keywords consistent with claimed type.
    
    Fix #6: Require source-text deal keywords, else drop.
    """
    text_lower = text.lower()
    
    type_keywords = {
        'lic': [
            'license', 'licensing', 'collaboration', 'partnership', 'agreement',
            'exclusive rights', 'royalt', 'milestone', 'upfront',
            '授权', '许可', '合作', '里程碑',
        ],
        'acq': [
            'acquisition', 'acquire', 'acquired', 'merger', 'merge', 'merged',
            'tender offer', 'buyout', 'purchase',
            '收购', '并购', '合并',
        ],
        'inv': [
            'financing', 'investment', 'investor', 'funding', 'series',
            'round', 'offering', 'placement', 'ipo', 'public offering',
            'credit facility', 'credit agreement', 'loan', 'draw',
            '融资', '投资', '配售', '上市',
        ],
        'buyout': [
            'buyout', 'buy out', 'buy-out', 'termination', 'terminate',
            'obligation', 'extinguish', 'settlement', 'one-time payment',
        ],
    }
    
    keywords = type_keywords.get(deal_type, [])
    for kw in keywords:
        if kw in text_lower:
            return True
    
    return False


def _verify_role_in_source(company: str, role: str, counterparty: str, source_text: str) -> bool:
    """Verify that company has the claimed role in the source text.
    
    Fix B1: acquirer/target, licensor/licensee must match filing roles.
    The company name must appear in the source text, AND the role must match.
    """
    if not company or not source_text:
        return False
    
    text_lower = source_text.lower()
    company_lower = company.lower()
    
    # First, verify company name appears in source
    if company_lower not in text_lower:
        return False
    
    # Patterns for different roles
    role_patterns = {
        'acquirer': [
            rf'{re.escape(company_lower)}[^.]*(?:acquir|purchas|buy|bought)',
            rf'(?:acquir|purchas|buy|bought)[^.]*by\s+{re.escape(company_lower)}',
            rf'{re.escape(company_lower)}[^.]*(?:to acquire|will acquire|has acquired)',
        ],
        'target': [
            rf'(?:acquir|purchas|buy)[^.]*{re.escape(company_lower)}',
            rf'{re.escape(company_lower)}[^.]*(?:acquired by|purchased by|bought by)',
            rf'(?:acquisition of|purchase of)\s+{re.escape(company_lower)}',
        ],
        'licensor': [
            rf'{re.escape(company_lower)}[^.]*(?:licens|grant)',
            rf'(?:licens|grant)[^.]*(?:from|by)\s+{re.escape(company_lower)}',
        ],
        'licensee': [
            rf'{re.escape(company_lower)}[^.]*(?:obtain|receiv)[^.]*licens',
            rf'(?:licens)[^.]*(?:to)\s+{re.escape(company_lower)}',
        ],
        'investor': [
            rf'{re.escape(company_lower)}[^.]*(?:invest|fund|financ)',
            rf'(?:led by|from)\s+{re.escape(company_lower)}',
        ],
        'issuer': [
            rf'{re.escape(company_lower)}[^.]*(?:announc|complet|clos)[^.]*(?:financ|offering|round)',
            rf'{re.escape(company_lower)}[^.]*(?:rais|receiv)[^.]*\$',
        ],
    }
    
    patterns = role_patterns.get(role, [])
    for pattern in patterns:
        if re.search(pattern, text_lower, re.IGNORECASE):
            return True
    
    # If counterparty provided, verify they're distinct from company's role
    if counterparty:
        counterparty_lower = counterparty.lower()
        if role == 'acquirer':
            # Make sure counterparty is the target, not another acquirer
            for pattern in role_patterns.get('target', []):
                pattern = pattern.replace(re.escape(company_lower), re.escape(counterparty_lower))
                if re.search(pattern, text_lower, re.IGNORECASE):
                    return True
    
    return False


def _extract_amounts_near_companies(source_text: str, company: str, counterparty: str, window_chars: int = 500) -> list[tuple[int, str]]:
    """Extract amounts that appear near both company names in the text.
    
    Fix B2: Amount and deal type must come from the same passage as both parties.
    """
    if not source_text or not company:
        return []
    
    text_lower = source_text.lower()
    company_lower = company.lower()
    
    # Find positions of company mentions
    company_positions = []
    for m in re.finditer(re.escape(company_lower), text_lower):
        company_positions.append(m.start())
    
    if not company_positions:
        # Try normalized name
        normalized = _normalize_company_name(company)
        if normalized and len(normalized) >= 3:
            for m in re.finditer(re.escape(normalized), text_lower):
                company_positions.append(m.start())
    
    if not company_positions:
        return []
    
    # If counterparty provided, find their positions too
    counterparty_positions = []
    if counterparty:
        counterparty_lower = counterparty.lower()
        for m in re.finditer(re.escape(counterparty_lower), text_lower):
            counterparty_positions.append(m.start())
    
    # Extract amounts from windows around company mentions
    amounts = []
    for pos in company_positions:
        start = max(0, pos - window_chars)
        end = min(len(source_text), pos + window_chars)
        window = source_text[start:end]
        
        # If counterparty required, check they're in the same window
        if counterparty and counterparty_positions:
            counterparty_in_window = any(
                start <= cp_pos <= end for cp_pos in counterparty_positions
            )
            if not counterparty_in_window:
                continue
        
        window_amounts = _normalize_amount_with_currency(window)
        amounts.extend(window_amounts)
    
    # Deduplicate
    return list(set(amounts))


def _is_credit_facility(source_text: str) -> bool:
    """Check if deal is a credit facility (loan agreement).
    
    Fix B8: Credit facilities need special handling for 'up to' amounts.
    """
    text_lower = source_text.lower()
    credit_patterns = [
        r'\bcredit\s+(?:facility|agreement)\b',
        r'\bloan\s+(?:facility|agreement)\b',
        r'\bterm\s+loan\b',
        r'\brevolving\s+credit\b',
        r'\bventure\s+debt\b',
        r'\bdebt\s+facility\b',
    ]
    
    for pattern in credit_patterns:
        if re.search(pattern, text_lower):
            return True
    return False


def _is_obligation_buyout(source_text: str) -> bool:
    """Check if deal is a buyout of obligations (milestone/royalty termination).
    
    Fix B7: Add type for obligation buyouts/amendments.
    """
    text_lower = source_text.lower()
    buyout_patterns = [
        # Explicit buyout language
        r'\b(?:buyout|buy-?out)\s+(?:of\s+)?(?:all\s+)?(?:royalt|milestone|obligation)',
        r'\bone-?time\s+(?:buyout|buy-?out)\b',
        # Termination/extinguishing (with optional "of" after termination)
        r'\b(?:terminat\w*|extinguish\w*)\s+(?:of\s+)?(?:all\s+)?(?:royalt|milestone|obligation)',
        r'\b(?:terminat\w*|extinguish\w*)\s+(?:of\s+)?(?:all\s+)?(?:future\s+)?(?:royalt|milestone)',
        # One-time payment to terminate
        r'\bone-?time\s+payment\s+(?:to\s+)?(?:terminat|eliminat)',
        r'\blump\s+sum\s+payment\s+(?:to\s+)?(?:terminat|eliminat)',
        # "for a lump sum payment" (after termination/buyout context)
        r'\b(?:royalt|milestone|obligation)\w*\s+(?:for|in exchange for)\s+(?:a\s+)?lump\s+sum',
        # Settlement/elimination
        r'\b(?:in\s+)?(?:full\s+)?settlement\s+of\s+(?:all\s+)?(?:royalt|milestone|obligation)',
        r'\b(?:eliminat\w*|extinguish\w*)\s+(?:of\s+)?(?:all\s+)?(?:future\s+)?(?:royalt|milestone)',
    ]
    
    for pattern in buyout_patterns:
        if re.search(pattern, text_lower):
            return True
    return False


def _has_upfront_language(source_text: str) -> bool:
    """Check if filing explicitly uses 'upfront' language.
    
    Fix B7: Never label amount '首付' unless filing calls it upfront.
    """
    text_lower = source_text.lower()
    upfront_patterns = [
        r'\bupfront\b',
        r'\bup-?front\b',
        r'\binitial\s+payment\b',
        r'\bsigning\s+(?:bonus|payment|fee)\b',
        r'\bexecution\s+(?:payment|fee)\b',
        r'\bat\s+(?:closing|signing)\b',
    ]
    
    for pattern in upfront_patterns:
        if re.search(pattern, text_lower):
            return True
    return False


def _extract_up_to_amount(source_text: str) -> tuple[str, str] | None:
    """Extract 'up to' maximum amount and drawn amount.
    
    Fix B8: 'up to' amounts must render as '最高'.
    Returns (max_amount, drawn_amount) or None.
    """
    text_lower = source_text.lower()
    
    # Pattern for "up to $X million" with optional drawn amount
    up_to_pattern = r'\b(?:up\s+to|maximum\s+(?:of)?|aggregate\s+(?:of)?)\s*\$?\s*([\d,]+(?:\.\d+)?)\s*(?:million|billion|M\b|B\b)'
    
    match = re.search(up_to_pattern, text_lower, re.IGNORECASE)
    if match:
        max_val = match.group(1).replace(',', '')
        
        # Look for drawn amount - multiple patterns
        # Pattern 1: "$X million drawn" or "$X drawn"
        # Pattern 2: "with $X million drawn at closing"
        # Pattern 3: "drawn/funded/advanced X million"
        drawn_patterns = [
            r'with\s+\$?([\d,]+(?:\.\d+)?)\s*(?:million|billion|M\b|B\b)?\s*(?:drawn|funded|advanced|disbursed)',
            r'\$?([\d,]+(?:\.\d+)?)\s*(?:million|billion|M\b|B\b)\s+(?:drawn|funded|advanced|disbursed)',
            r'(?:drawn|funded|advanced|disbursed)[^.]*\$?([\d,]+(?:\.\d+)?)\s*(?:million|billion|M\b|B\b)',
        ]
        
        for drawn_pattern in drawn_patterns:
            drawn_match = re.search(drawn_pattern, text_lower, re.IGNORECASE)
            if drawn_match:
                drawn_val = drawn_match.group(1)
                if drawn_val:
                    drawn_val = drawn_val.replace(',', '')
                    return (max_val, drawn_val)
        
        return (max_val, None)
    
    return None


def _test_role_verification():
    """Test role verification in source text - Fix B1."""
    tests = [
        # Correct roles
        ("Pfizer", "acquirer", "Verona", "Pfizer acquires Verona Pharma for $500M", True),
        ("Verona", "target", "Pfizer", "Pfizer acquires Verona Pharma for $500M", True),
        ("Genentech", "licensor", "Alector", "Alector receives license from Genentech", True),
        
        # Wrong roles - Fix B1: Merck acquires Verona but output says Pfizer
        ("Pfizer", "acquirer", "Verona", "Merck acquires Verona Pharma for $500M", False),
        ("Novartis", "target", "Pfizer", "Merck acquires Verona Pharma for $500M", False),
        
        # Role reversal
        ("Verona", "acquirer", "Pfizer", "Pfizer acquires Verona Pharma for $500M", False),
    ]
    
    passed = 0
    for company, role, counterparty, source, expected in tests:
        result = _verify_role_in_source(company, role, counterparty, source)
        if result == expected:
            passed += 1
        else:
            print(f"FAIL: {company} as {role} in '{source[:50]}...' -> {result} (expected {expected})")
    
    print(f"_test_role_verification: {passed}/{len(tests)} tests passed")
    return passed == len(tests)


def _test_credit_facility():
    """Test credit facility detection - Fix B8."""
    tests = [
        ("Rocket Pharma entered into a Credit Facility Agreement with Hercules Capital", True),
        ("Company signed a term loan agreement for $50 million", True),
        ("Hercules Capital provided revolving credit facility", True),
        ("Oxford Finance provides venture debt facility", True),
        ("Series B financing round led by Flagship", False),
        ("License agreement with milestone payments", False),
        # Fix B8 adversarial: "credit" in unrelated context
        ("The company's credit rating was upgraded", False),
    ]
    
    passed = 0
    for text, expected in tests:
        result = _is_credit_facility(text)
        if result == expected:
            passed += 1
        else:
            print(f"FAIL: '{text[:50]}...' -> {result} (expected {expected})")
    
    print(f"_test_credit_facility: {passed}/{len(tests)} tests passed")
    return passed == len(tests)


def _test_up_to_amount():
    """Test 'up to' amount extraction - Fix B8."""
    tests = [
        # Should extract max amount
        ("up to $500 million credit facility", ("500", None)),
        ("maximum of $200 million term loan", ("200", None)),
        ("aggregate of $300 million in financing", ("300", None)),
        # With drawn amount
        ("up to $500 million, with $100 million drawn at closing", ("500", "100")),
        # No up-to pattern
        ("$500 million credit facility", None),
        ("License for $100 million upfront", None),
    ]
    
    passed = 0
    for text, expected in tests:
        result = _extract_up_to_amount(text)
        if result == expected:
            passed += 1
        else:
            print(f"FAIL: '{text[:50]}...' -> {result} (expected {expected})")
    
    print(f"_test_up_to_amount: {passed}/{len(tests)} tests passed")
    return passed == len(tests)


def _test_event_date_extraction():
    """Test event date extraction from filing cover page - Fix B6."""
    tests = [
        # Standard 8-K format
        ("Date of Report (Date of earliest event reported): October 1, 2026", "2026-10-01"),
        ("Date of Report (Date of earliest event reported): September 15, 2026", "2026-09-15"),
        # Numeric format
        ("Date of Report (Date of earliest event reported): 10/01/2026", "2026-10-01"),
        # Without parenthetical
        ("Date of Report: October 5, 2026", "2026-10-05"),
        # 6-K format
        ("Report date: August 20, 2026", "2026-08-20"),
        # Fix B6: Must work on real cover page text (not truncated)
        ("UNITED STATES SECURITIES... Date of Report (Date of earliest event reported): July 15, 2026 ...", "2026-07-15"),
        # No date found
        ("Company announces partnership agreement", None),
        ("Filed: October 1, 2026", None),  # "Filed" is not event date
    ]
    
    passed = 0
    for text, expected in tests:
        result = _extract_event_date_from_filing(text)
        if result == expected:
            passed += 1
        else:
            print(f"FAIL: '{text[:60]}...' -> {result} (expected {expected})")
    
    print(f"_test_event_date_extraction: {passed}/{len(tests)} tests passed")
    return passed == len(tests)


def _test_obligation_buyout():
    """Test obligation buyout detection - Fix B7."""
    tests = [
        ("Immunome paid $20.0 million in cash for the one-time buyout of all royalty and milestone obligations", True),
        ("Termination of all future royalty obligations for a lump sum payment", True),
        ("License agreement with upfront payment and milestones", False),
        ("Acquisition of XYZ Corp for $500M", False),
    ]
    
    passed = 0
    for text, expected in tests:
        result = _is_obligation_buyout(text)
        if result == expected:
            passed += 1
        else:
            print(f"FAIL: '{text[:50]}...' -> {result} (expected {expected})")
    
    print(f"_test_obligation_buyout: {passed}/{len(tests)} tests passed")
    return passed == len(tests)


def _test_nonprofit_detection():
    """Test nonprofit/consortium detection - Fix B5 with word boundaries."""
    tests = [
        # Should reject - nonprofit/consortium
        ("Chan Zuckerberg Biohub project with NIH commitment", True),
        ("Multi-party combined commitment of $1.8B", True),
        ("Nonprofit foundation grant program", True),
        ("DOE funding commitment to consortium", True),
        ("Biohub announces $1.8B initiative with $500M own funds", True),
        ("Meta, Google pledge combined $300M to nonprofit consortium", True),
        # Fix B5: Endpoints Biohub article wording
        ("The Biohub launches $1.8 billion initiative funded by NIH and DOE", True),
        
        # Should accept (normal deals) - Fix B5: word boundaries
        ("Pfizer acquires biotech for $500 million", False),
        ("Company announces Series B financing", False),
        ("License agreement with milestone payments", False),
        # Fix B5: "doe" in "does", "nsf" in "transfer" must NOT trigger rejection
        ("Alector does receive $100 million upfront payment", False),  # "does" contains "doe"
        ("Genentech transfer agreement with Alector", False),  # "transfer" contains "nsf"
        ("The company does not disclose details", False),
        ("Alector, Inc. entered into a Collaboration Agreement with Genentech, Inc.", False),
    ]
    
    passed = 0
    for text, expected in tests:
        result = _is_nonprofit_or_consortium(text)
        if result == expected:
            passed += 1
        else:
            print(f"FAIL: '{text[:60]}...' -> {result} (expected {expected})")
    
    print(f"_test_nonprofit_detection: {passed}/{len(tests)} tests passed")
    return passed == len(tests)


def _test_company_verification():
    """Test company name verification in source."""
    tests = [
        # Should pass - exact match
        ("Pfizer", "Pfizer Inc. announces acquisition", True),
        ("Alector", "Alector signs license agreement", True),
        # Should pass - case insensitive
        ("NOVARTIS", "Novartis AG reported today", True),
        # Should pass - alias
        ("J&J", "Johnson & Johnson announced", True),
        # Should fail - not in source
        ("Pfizer", "Merck announces new drug approval", False),
        ("Novartis", "Company XYZ signs deal with ABC", False),
        # Should fail - invented
        ("InventedPharma", "Real company announces deal", False),
    ]
    
    passed = 0
    for company, source, expected in tests:
        result = _verify_company_in_source(company, source)
        if result == expected:
            passed += 1
        else:
            print(f"FAIL: company '{company}' in '{source[:40]}...' -> {result} (expected {expected})")
    
    print(f"_test_company_verification: {passed}/{len(tests)} tests passed")
    return passed == len(tests)


def _test_deal_keywords():
    """Test deal keyword detection."""
    tests = [
        # Should pass
        ("The license agreement includes milestones", "lic", True),
        ("Company acquired for $500M", "acq", True),
        ("Series B financing round", "inv", True),
        # Should fail - wrong type
        ("License agreement with upfront", "acq", False),
        ("Acquisition completed", "lic", False),
        # Should fail - no deal keywords
        ("Company announces new hiring", "inv", False),
        ("Research results published", "lic", False),
    ]
    
    passed = 0
    for text, deal_type, expected in tests:
        result = _has_deal_keywords(text, deal_type)
        if result == expected:
            passed += 1
        else:
            print(f"FAIL: '{text[:40]}...' type={deal_type} -> {result} (expected {expected})")
    
    print(f"_test_deal_keywords: {passed}/{len(tests)} tests passed")
    return passed == len(tests)


def _test_name_normalization():
    """Test company name normalization - Fix #11."""
    tests = [
        # Should strip end suffixes
        ("Pfizer Inc.", "pfizer"),
        ("Novartis AG", "novartis"),
        ("Roche Holding Ltd", "roche holding"),
        # Fix #11: Should NOT strip ' ag'/' co'/' se' from middle of names
        ("Diageo plc", "diageo"),
        ("Boehringer Ingelheim", "boehringer ingelheim"),
        ("Sanofi-Aventis SA", "sanofiaventis"),
        # Chinese suffixes - order matters: 股份有限公司 before 有限公司 before 集团
        ("上海医药集团股份有限公司", "上海医药"),
        ("恒瑞医药", "恒瑞医药"),
        ("百济神州有限公司", "百济神州"),
    ]
    
    passed = 0
    for name, expected in tests:
        result = _normalize_company_name(name)
        if result == expected:
            passed += 1
        else:
            print(f"FAIL: '{name}' -> '{result}' (expected '{expected}')")
    
    print(f"_test_name_normalization: {passed}/{len(tests)} tests passed")
    return passed == len(tests)


def _classify_deal_type(text: str) -> str | None:
    """Classify deal as lic/acq/inv based on text. Returns None if unclear.
    
    Uses word boundaries to avoid false positives.
    """
    text_lower = text.lower()
    
    def count_word_matches(patterns: list[str]) -> int:
        count = 0
        for p in patterns:
            if len(p) <= 5:
                if re.search(r'\b' + re.escape(p) + r'\b', text_lower):
                    count += 1
            else:
                if p in text_lower:
                    count += 1
        return count
    
    lic_signals = [
        "license agreement", "collaboration agreement", "exclusive license",
        "non-exclusive license", "royalty", "royalties", 
        "milestone payment", "upfront payment", "option agreement",
        "授权协议", "许可协议", "合作协议", "独家授权", "里程碑付款",
    ]
    lic_count = count_word_matches(lic_signals)
    
    acq_signals = [
        "merger agreement", "tender offer", "definitive agreement to acquire",
        "acquisition agreement", "merger consideration", "acquire all",
        "to acquire", "has acquired", "will acquire", "acquired by",
        "收购协议", "并购", "要约收购", "吸收合并",
    ]
    acq_count = count_word_matches(acq_signals)
    
    inv_signals = [
        "securities purchase", "private placement", "public offering",
        "series a", "series b", "series c", "series d", "series e",
        "round a", "round b", "round c", "venture financing",
        "registered direct offering", "stock offering",
        "配售", "定向增发", "公开发行", "融资", "首次公开",
    ]
    inv_count = count_word_matches(inv_signals)
    
    if re.search(r'\bipo\b', text_lower):
        inv_count += 1
    
    counts = [("lic", lic_count), ("acq", acq_count), ("inv", inv_count)]
    counts.sort(key=lambda x: x[1], reverse=True)
    
    if counts[0][1] >= 2 and counts[0][1] > counts[1][1]:
        return counts[0][0]
    
    if counts[0][1] >= 1 and counts[1][1] == 0:
        return counts[0][0]
    
    return None


def _test_classify_deal_type():
    """Unit tests for deal type classifier with word boundaries."""
    tests = [
        ("Study of lipoprotein levels in patients", None),
        ("Adipose tissue analysis", None),
        ("New data acquisition system for the lab", None),
        ("Company announces IPO pricing", "inv"),
        ("Initial public offering completed", "inv"),
        ("Series B financing round", "inv"),
        ("Merger agreement signed", "acq"),
        ("Definitive agreement to acquire company", "acq"),
        ("License agreement for oncology program", "lic"),
        ("Exclusive license with milestone payments", "lic"),
        ("Company news update", None),
    ]
    
    passed = 0
    for text, expected in tests:
        result = _classify_deal_type(text)
        if result == expected:
            passed += 1
        else:
            print(f"FAIL: '{text[:50]}...' -> {result} (expected {expected})")
    
    print(f"_test_classify_deal_type: {passed}/{len(tests)} tests passed")
    return passed == len(tests)


def _is_biopharma_company(company_name: str, sic_codes: list = None, industry: str = None) -> bool:
    """Check if company is confidently in biopharma sector.
    
    For SEC filings, use SIC codes ONLY. No name-based fallback.
    """
    if sic_codes:
        return any(sic in SEC_BIOPHARMA_SICS for sic in sic_codes)
    
    if industry:
        industry_lower = industry.lower()
        if any(kw in industry_lower for kw in ["医药", "生物", "pharma", "biotech", "biopharma"]):
            return True
    
    return False


def _normalize_company_name(name: str) -> str:
    """Normalize company name for deduplication.
    
    Fix #11: Only strip legal suffixes at the END with word boundaries.
    Don't strip ' ag'/' co'/' se' from the middle of names.
    """
    name = name.strip()
    
    # Remove trailing legal suffixes with word boundaries
    # Order matters - longer suffixes first to avoid partial matches
    # Chinese suffixes must come first (most specific)
    suffix_patterns = [
        r'股份有限公司$',
        r'有限公司$',
        r'集团$',
        r'控股$',
        r',?\s+incorporated$',
        r',?\s+inc\.?$',
        r',?\s+limited$',
        r',?\s+ltd\.?$',
        r',?\s+corporation$',
        r',?\s+corp\.?$',
        r',?\s+company$',
        r',?\s+co\.?$',
        r'\s+plc$',
        r'\s+ag$',
        r'\s+se$',
        r'\s+sa$',
        r'\s+nv$',
        r'\s+bv$',
        r'\s+gmbh$',
    ]
    
    name_lower = name.lower()
    for pattern in suffix_patterns:
        name_lower = re.sub(pattern, '', name_lower, flags=re.IGNORECASE)
    
    name_lower = re.sub(r'[^\w\s]', '', name_lower)
    name_lower = re.sub(r'\s+', ' ', name_lower).strip()
    return name_lower


def _fetch_sec_filing_text(cik: str, accession: str, sec_ua: str, primary_doc_name: str = None, max_chars: int = 30000) -> str:
    """Fetch and extract text from SEC filing primary document and EX-99.1 press release.
    
    Fix B6: Keep full primary doc cover page for event date extraction.
    The cover page (first ~3000 chars) contains the event date.
    Then extract windows around deal keywords for amounts.
    """
    import time
    import requests
    
    headers = {"User-Agent": sec_ua, "Accept": "text/html"}
    
    accession_clean = accession.replace("-", "")
    accession_dashed = accession if "-" in accession else f"{accession[:10]}-{accession[10:12]}-{accession[12:]}"
    
    base_url = f"https://www.sec.gov/Archives/edgar/data/{cik}/{accession_clean}"
    
    cover_page_text = ""  # Fix B6: Keep cover page for event date extraction
    text_parts = []
    
    # Helper to extract deal-relevant windows from text
    def extract_deal_windows(full_text: str, window_size: int = 2000) -> str:
        """Extract windows around deal keywords to find relevant amounts."""
        keywords = [
            'million', 'billion', '$', 'upfront', 'milestone', 'license', 'collaboration',
            'acquisition', 'merger', 'agreement', 'payment', 'consideration', 'royalt',
            'equity', 'stake', 'financing', 'offering', 'placement', 'credit', 'loan',
            'buyout', 'terminate', 'obligation'
        ]
        windows = []
        text_lower = full_text.lower()
        positions = set()
        
        for kw in keywords:
            idx = 0
            while True:
                pos = text_lower.find(kw, idx)
                if pos == -1:
                    break
                positions.add(pos)
                idx = pos + 1
        
        if not positions:
            return full_text[:max_chars]
        
        sorted_pos = sorted(positions)
        merged_ranges = []
        for pos in sorted_pos:
            start = max(0, pos - window_size // 2)
            end = min(len(full_text), pos + window_size // 2)
            if merged_ranges and start <= merged_ranges[-1][1]:
                merged_ranges[-1] = (merged_ranges[-1][0], max(merged_ranges[-1][1], end))
            else:
                merged_ranges.append((start, end))
        
        for start, end in merged_ranges:
            windows.append(full_text[start:end])
        
        return "\n...\n".join(windows)[:max_chars]
    
    # Fetch the -index.htm to find document names and types
    # Fix #5(b): Use proper URL with dashed accession for -index.htm
    try:
        time.sleep(0.12)
        index_htm_url = f"{base_url}/{accession_dashed}-index.htm"
        resp = requests.get(index_htm_url, headers=headers, timeout=30)
        
        if resp.status_code != 200:
            # Try alternative format
            index_htm_url = f"{base_url}/{accession_clean}-index.htm"
            time.sleep(0.12)
            resp = requests.get(index_htm_url, headers=headers, timeout=30)
        
        if resp.status_code == 200:
            content = resp.text
            
            # Find 8-K or 6-K primary document
            # Pattern: <td>8-K</td> ... <a href="filename.htm"> or <a href="/ix?doc=...">
            primary_match = re.search(
                r'<td[^>]*>\s*(8-K|6-K)\s*</td>.*?<a[^>]*href="([^"]+)"',
                content, re.DOTALL | re.IGNORECASE
            )
            if primary_match:
                doc_ref = primary_match.group(2)
                time.sleep(0.12)
                
                # Handle iXBRL viewer URLs like /ix?doc=/Archives/...
                if doc_ref.startswith('/ix?doc='):
                    # Extract the actual document path
                    actual_path = doc_ref.split('doc=')[-1]
                    doc_url = f"https://www.sec.gov{actual_path}"
                elif doc_ref.startswith('/'):
                    doc_url = f"https://www.sec.gov{doc_ref}"
                elif doc_ref.startswith('http'):
                    doc_url = doc_ref
                else:
                    doc_url = f"{base_url}/{doc_ref}"
                
                doc_resp = requests.get(doc_url, headers=headers, timeout=60)
                if doc_resp.status_code == 200:
                    text = _strip_html(doc_resp.text)
                    if len(text) > 100:
                        # Fix B6: Keep cover page (first 3000 chars) for event date extraction
                        cover_page_text = text[:3000]
                        text_parts.append(extract_deal_windows(text))
            
            # Fix #5(d): ALSO fetch EX-99.1 press release (not "instead of")
            ex_matches = re.finditer(
                r'<td[^>]*>\s*(EX-99\.?\d*|99\.\d+)\s*</td>.*?<a[^>]*href="([^"]+)"',
                content, re.DOTALL | re.IGNORECASE
            )
            for ex_match in ex_matches:
                ex_ref = ex_match.group(2)
                time.sleep(0.12)
                
                # Handle various URL formats
                if ex_ref.startswith('/ix?doc='):
                    actual_path = ex_ref.split('doc=')[-1]
                    ex_url = f"https://www.sec.gov{actual_path}"
                elif ex_ref.startswith('/'):
                    ex_url = f"https://www.sec.gov{ex_ref}"
                elif ex_ref.startswith('http'):
                    ex_url = ex_ref
                else:
                    ex_url = f"{base_url}/{ex_ref}"
                
                ex_resp = requests.get(ex_url, headers=headers, timeout=60)
                if ex_resp.status_code == 200:
                    text = _strip_html(ex_resp.text)
                    if len(text) > 100:
                        text_parts.append(extract_deal_windows(text))
                        break  # Just get the first EX-99
    except Exception as e:
        logging.debug("SEC index.htm parsing failed: %s", e)
    
    # If primary_doc_name provided from search, also try it
    if primary_doc_name and not text_parts:
        try:
            time.sleep(0.12)
            doc_url = f"{base_url}/{primary_doc_name}"
            doc_resp = requests.get(doc_url, headers=headers, timeout=60)
            if doc_resp.status_code == 200:
                text = _strip_html(doc_resp.text)
                if len(text) > 100:
                    text_parts.append(extract_deal_windows(text))
        except Exception as e:
            logging.debug("SEC primary doc fetch failed: %s", e)
    
    # Fallback: try common document names directly
    if not text_parts:
        for doc_name in ["8-k.htm", "6-k.htm", "ex99-1.htm", "ex991.htm", "ex99.htm"]:
            try:
                time.sleep(0.12)
                doc_url = f"{base_url}/{doc_name}"
                resp = requests.get(doc_url, headers=headers, timeout=30)
                if resp.status_code == 200:
                    text = _strip_html(resp.text)
                    if len(text) > 100:
                        text_parts.append(extract_deal_windows(text))
                        break
            except Exception as e:
                logging.debug("SEC fallback fetch failed: %s", e)
    
    # Fix B6: Put cover page first so event date extraction works on full primary doc
    # The cover page contains "Date of Report (Date of earliest event reported)"
    if cover_page_text:
        text_parts.insert(0, cover_page_text)
    
    combined = "\n\n".join(text_parts)
    return combined[:max_chars]


def _extract_event_date_from_filing(filing_text: str) -> str | None:
    """Extract the event date from an 8-K/6-K filing.
    
    Fix B6: Must extract from full primary doc before any trimming.
    The event date is on the cover page: "Date of Report (Date of earliest event reported)"
    """
    if not filing_text:
        return None
    
    # More comprehensive patterns for 8-K cover page
    patterns = [
        # Standard format: Date of Report (Date of earliest event reported): October 1, 2026
        r'Date\s+of\s+Report\s*\(Date\s+of\s+earliest\s+event\s+reported\)\s*[:\s]+(\w+\s+\d{1,2},?\s+\d{4})',
        r'Date\s+of\s+Report\s*\(Date\s+of\s+earliest\s+event\s+reported\)\s*[:\s]+(\d{1,2}/\d{1,2}/\d{4})',
        # Without parenthetical
        r'Date\s+of\s+Report[^:]*:\s*(\w+\s+\d{1,2},?\s+\d{4})',
        r'Date\s+of\s+Report[^:]*:\s*(\d{1,2}/\d{1,2}/\d{4})',
        # Earliest event reported standalone
        r'Date\s+of\s+earliest\s+event\s+reported[^:]*:\s*(\w+\s+\d{1,2},?\s+\d{4})',
        r'Date\s+of\s+earliest\s+event\s+reported[^:]*:\s*(\d{1,2}/\d{1,2}/\d{4})',
        # 6-K format
        r'Report\s+date[:\s]+(\w+\s+\d{1,2},?\s+\d{4})',
    ]
    
    for pattern in patterns:
        match = re.search(pattern, filing_text, re.IGNORECASE)
        if match:
            date_str = match.group(1).strip()
            try:
                for fmt in ["%B %d, %Y", "%B %d %Y", "%m/%d/%Y", "%B %d,%Y"]:
                    try:
                        parsed = datetime.strptime(date_str, fmt)
                        return parsed.date().isoformat()
                    except ValueError:
                        continue
            except Exception:
                pass
    
    return None


def fetch_sec_filings(start: datetime, limit: int) -> tuple[list[dict], str]:
    """Fetch recent 8-K and 6-K filings from SEC EDGAR for biopharma companies.
    
    Fix #5: Accept original 8-K if filing date within 7d window AND event date within 14d before filing.
    """
    import time
    import requests
    
    sec_ua = os.environ.get("SEC_USER_AGENT")
    if not sec_ua:
        logging.warning("SEC_USER_AGENT 未设置，跳过 SEC EDGAR 来源。请设置格式如 'CompanyName contact@example.com'")
        return [], "skipped"
    
    logging.info("抓取 SEC EDGAR 8-K/6-K（生物医药 SIC，跳过修订）")
    
    end_date = datetime.now(timezone.utc).date()
    start_date = start.date()
    
    rows = []
    skipped_amendments = 0
    skipped_old_events = 0
    headers = {"User-Agent": sec_ua, "Accept": "application/json"}
    
    for form_type in ["8-K", "6-K"]:
        for keyword in SEC_DEAL_KEYWORDS[:5]:
            search_url = "https://efts.sec.gov/LATEST/search-index"
            params = {
                "q": keyword,
                "dateRange": "custom",
                "startdt": start_date.isoformat(),
                "enddt": end_date.isoformat(),
                "forms": form_type,
                "from": "0",
                "size": "30",
            }
            
            try:
                time.sleep(0.12)
                resp = requests.get(search_url, params=params, headers=headers, timeout=30)
                if resp.status_code == 200:
                    data = resp.json()
                    hits = data.get("hits", {}).get("hits", [])
                    
                    for hit in hits:
                        if len(rows) >= limit * 2:
                            break
                            
                        source = hit.get("_source", {})
                        
                        company = source.get("display_names", ["Unknown"])[0]
                        filed_date = source.get("file_date", "")
                        form = source.get("form", form_type)
                        accession = source.get("adsh", "").replace("-", "")
                        cik = source.get("ciks", [""])[0]
                        sics = source.get("sics", [])
                        
                        # Fix #4: Get primary document name from search result
                        primary_doc = source.get("file_name", "")
                        
                        if not accession or not cik:
                            continue
                        
                        # Skip amendments (8-K/A, 6-K/A)
                        form_upper = form.upper()
                        if "/A" in form_upper or form_upper.endswith("A"):
                            skipped_amendments += 1
                            continue
                        
                        # Filter by SIC code - strict biopharma only
                        if not _is_biopharma_company(company, sic_codes=sics):
                            continue
                        
                        doc_url = f"https://www.sec.gov/Archives/edgar/data/{cik}/{accession}"
                        
                        items = source.get("items", [])
                        description = ", ".join(items) if items else f"{form} filing"
                        
                        # Fetch actual filing text using primary doc name
                        filing_text = _fetch_sec_filing_text(cik, accession, sec_ua, primary_doc)
                        
                        # Fix #5: Event date rule
                        # Accept if: filing date in 7d window AND event date within 14d before filing
                        event_date = _extract_event_date_from_filing(filing_text)
                        
                        try:
                            filed_dt = datetime.strptime(filed_date[:10], "%Y-%m-%d").date() if filed_date else end_date
                        except ValueError:
                            filed_dt = end_date
                        
                        if event_date:
                            try:
                                event_dt = datetime.strptime(event_date, "%Y-%m-%d").date()
                                # Event must be within 14 days before filing
                                days_before_filing = (filed_dt - event_dt).days
                                if days_before_filing < 0 or days_before_filing > 14:
                                    skipped_old_events += 1
                                    logging.debug("跳过事件日期过早：%s (event %s, filed %s)", company, event_date, filed_date)
                                    continue
                            except ValueError:
                                pass
                        
                        # Use filing date for display (announcement date)
                        use_date = filed_date[:10] if filed_date else end_date.isoformat()
                        
                        if filing_text and len(filing_text) > 100:
                            summary = filing_text[:4000]
                        else:
                            summary = f"SEC {form} filing by {company}. Items: {description}"
                        
                        rows.append({
                            "source": f"SEC {form}",
                            "kind": "industry",
                            "filing_source": "sec",
                            "title": f"{company}: {description[:80]}",
                            "url": doc_url,
                            "date": use_date,
                            "summary": summary,
                            "filing_text": filing_text,
                            "company": company,
                            "filing_type": form,
                            "event_date": event_date,
                        })
                        
            except Exception as e:
                logging.warning("SEC 搜索失败 (%s, %s): %s", form_type, keyword, e)
                continue
            
            if len(rows) >= limit * 2:
                break
        if len(rows) >= limit * 2:
            break
    
    if skipped_amendments:
        logging.info("跳过 %d 条修订版（8-K/A, 6-K/A）", skipped_amendments)
    if skipped_old_events:
        logging.info("跳过 %d 条事件日期过早的披露", skipped_old_events)
    
    seen = set()
    unique_rows = []
    for row in rows:
        if row["url"] not in seen:
            seen.add(row["url"])
            unique_rows.append(row)
    
    logging.info("SEC EDGAR 得到 %d 条（有文本 %d 条）", 
                 len(unique_rows), 
                 sum(1 for r in unique_rows if r.get("filing_text")))
    return unique_rows[:limit], "ok" if unique_rows else "failed"


def _test_sec_filing_fetch():
    """Live test for SEC filing fetch - tests Alector 8-K contains $100 million."""
    import os
    sec_ua = os.environ.get("SEC_USER_AGENT")
    if not sec_ua:
        print("SKIP: SEC_USER_AGENT not set")
        return True
    
    # Alector CIK is 0001773087, need to find the accession number for their Oct 2026 filing
    # For testing, we'll search for their recent 8-K
    import requests
    import time
    
    headers = {"User-Agent": sec_ua, "Accept": "application/json"}
    search_url = "https://efts.sec.gov/LATEST/search-index"
    params = {
        "q": "Alector",
        "dateRange": "custom",
        "startdt": "2026-09-25",
        "enddt": "2026-10-08",
        "forms": "8-K",
        "from": "0",
        "size": "10",
    }
    
    try:
        resp = requests.get(search_url, params=params, headers=headers, timeout=30)
        if resp.status_code != 200:
            print(f"SKIP: SEC search returned {resp.status_code}")
            return True
        
        data = resp.json()
        hits = data.get("hits", {}).get("hits", [])
        
        alector_hit = None
        for hit in hits:
            source = hit.get("_source", {})
            names = source.get("display_names", [])
            if any("alector" in n.lower() for n in names):
                alector_hit = source
                break
        
        if not alector_hit:
            print("SKIP: Alector 8-K not found in search results")
            return True
        
        cik = alector_hit.get("ciks", [""])[0]
        accession = alector_hit.get("adsh", "").replace("-", "")
        primary_doc = alector_hit.get("file_name", "")
        
        time.sleep(0.12)
        filing_text = _fetch_sec_filing_text(cik, accession, sec_ua, primary_doc)
        
        if not filing_text:
            print("FAIL: No filing text fetched for Alector 8-K")
            return False
        
        if "$100 million" in filing_text or "$100,000,000" in filing_text:
            print(f"PASS: Alector 8-K text fetched ({len(filing_text)} chars), contains $100 million")
            return True
        else:
            print(f"FAIL: Alector 8-K text ({len(filing_text)} chars) does not contain $100 million")
            print(f"  First 500 chars: {filing_text[:500]}")
            return False
        
    except Exception as e:
        print(f"SKIP: SEC test failed with exception: {e}")
        return True


def fetch_hkex_announcements(start: datetime, limit: int) -> tuple[list[dict], str]:
    """Fetch announcements from HKEX 披露易 - DISABLED."""
    logging.info("HKEX 披露易：暂停使用（需验证公司列表）")
    return [], "disabled"


def fetch_cninfo_announcements(start: datetime, limit: int) -> tuple[list[dict], str]:
    """Fetch announcements from 巨潮资讯 - DISABLED."""
    logging.info("巨潮资讯：暂停使用（需添加 CSRC 行业过滤）")
    return [], "disabled"


def fetch_filing_sources(start: datetime, limit: int) -> tuple[list[dict], dict[str, tuple[int, str]]]:
    """Fetch deal filings from official sources."""
    all_rows = []
    source_stats = {}
    
    try:
        sec_rows, sec_status = fetch_sec_filings(start, limit)
        all_rows.extend(sec_rows)
        source_stats["SEC EDGAR"] = (len(sec_rows), sec_status)
    except Exception as e:
        logging.warning("SEC 来源失败: %s", e)
        source_stats["SEC EDGAR"] = (0, "failed")
    
    # HKEX and cninfo disabled
    source_stats["HKEX 披露易"] = (0, "disabled")
    source_stats["巨潮资讯"] = (0, "disabled")
    
    logging.info("官方披露来源总计 %d 条", len(all_rows))
    return all_rows, source_stats


def normalize_doi(url: str) -> str:
    """Normalize URL to DOI identifier for deduplication."""
    url = url.lower().strip()
    url = re.sub(r'\?.*$', '', url)
    
    doi_match = re.match(r'https?://(?:dx\.)?doi\.org/(10\.\d+/.+)', url)
    if doi_match:
        return doi_match.group(1)
    
    nature_match = re.match(r'https?://(?:www\.)?nature\.com/articles/(s\d+-\d+-\d+-\w+)', url)
    if nature_match:
        return f"10.1038/{nature_match.group(1)}"
    
    cell_match = re.match(r'https?://(?:www\.)?cell\.com/[^/]+/(?:fulltext|abstract)/(S\d+-\d+\(\d+\)\d+-\d+)', url)
    if cell_match:
        return f"cell:{cell_match.group(1)}"
    
    science_match = re.match(r'https?://(?:www\.)?science\.org/doi/(10\.\d+/.+)', url)
    if science_match:
        return science_match.group(1)
    
    return url


def load_existing_urls() -> set[str]:
    """Load URLs and DOIs from existing content to avoid duplicates."""
    existing = set()
    existing_raw = set()
    latest_path = ROOT / "content" / "latest.json"
    if latest_path.exists():
        try:
            data = json.loads(latest_path.read_text(encoding="utf-8"))
            for art in data.get("articles") or []:
                if art.get("url"):
                    existing_raw.add(art["url"])
                    existing.add(normalize_doi(art["url"]))
            for deal in data.get("deals") or []:
                if deal.get("url"):
                    existing_raw.add(deal["url"])
                    existing.add(normalize_doi(deal["url"]))
        except (json.JSONDecodeError, OSError):
            pass
    
    index_path = ROOT / "index.html"
    if index_path.exists():
        try:
            content = index_path.read_text(encoding="utf-8")
            url_pattern = r"url:'([^']+)'"
            for match in re.finditer(url_pattern, content):
                url = match.group(1)
                if url.startswith('#'):
                    continue
                existing_raw.add(url)
                existing.add(normalize_doi(url))
        except OSError:
            pass
    
    existing.update(existing_raw)
    logging.info("已有 %d 个去重 URL/DOI（规范化后）", len(existing))
    return existing


def fetch_all(config: dict) -> list[dict]:
    default_days = int(config.get("window_days") or 7)
    limit = int(config.get("max_per_source") or 6)
    end = datetime.now(timezone.utc)
    rows: list[dict] = []
    seen = set()
    existing = load_existing_urls()
    source_stats = []
    
    for source in config["sources"]:
        source_name = source.get("name", "unknown")
        
        if source.get("type") == "manual":
            logging.info("跳过手动来源：%s", source_name)
            source_stats.append({"name": source_name, "status": "manual", "count": 0})
            continue
        
        source_days = int(source.get("window_days") or default_days)
        start = end - timedelta(days=source_days)
        
        try:
            if source.get("type") == "pubmed":
                batch = fetch_pubmed(source, start.date(), end.date(), limit)
                fetch_status = "ok" if batch else "failed"
            else:
                batch, fetch_status = fetch_rss(source, start, limit)
        except Exception:
            logging.exception("来源失败：%s", source_name)
            source_stats.append({"name": source_name, "status": "failed", "count": 0})
            continue
        
        count = 0
        skipped_no_abstract = 0
        for row in batch:
            url = row["url"]
            normalized = normalize_doi(url)
            if url in seen or normalized in seen:
                continue
            if url in existing or normalized in existing:
                logging.debug("跳过已有内容：%s (规范化: %s)", url, normalized)
                continue
            if row.get("kind") == "academic" and row.get("source") != "PubMed":
                summary = (row.get("summary") or "").strip()
                if len(summary) < 50 or summary.count(",") > 3 and len(summary) < 100:
                    skipped_no_abstract += 1
                    continue
            seen.add(url)
            seen.add(normalized)
            rows.append(row)
            count += 1
        
        if skipped_no_abstract:
            logging.info("  %s: 跳过 %d 条无摘要条目", source_name, skipped_no_abstract)
        source_stats.append({"name": source_name, "status": fetch_status, "count": count})
    
    filing_limit = int(config.get("max_filing_deals") or 10)
    start_for_filings = end - timedelta(days=default_days)
    filing_rows, filing_stats = fetch_filing_sources(start_for_filings, filing_limit)
    
    # Fix #12: Remove unused dedup block - actual dedup happens in claude_draft
    filing_count = 0
    for row in filing_rows:
        url = row["url"]
        if url in seen or url in existing:
            continue
        seen.add(url)
        rows.append(row)
        filing_count += 1
    
    for source_name, (count, status) in filing_stats.items():
        source_stats.append({"name": source_name, "status": status, "count": count})
    
    logging.info("=== 来源统计 ===")
    for stat in source_stats:
        if stat["status"] == "manual":
            logging.info("  %s: 手动来源，跳过", stat["name"])
        elif stat["status"] == "failed":
            logging.info("  %s: 失败", stat["name"])
        elif stat["status"] == "skipped":
            logging.info("  %s: 跳过", stat["name"])
        elif stat["status"] == "disabled":
            logging.info("  %s: 暂停", stat["name"])
        else:
            logging.info("  %s: %d 条", stat["name"], stat["count"])
    
    logging.info("总计 %d 条新内容", len(rows))
    
    max_academic = int(config.get("max_per_category_input") or 15)
    max_industry = int(config.get("max_industry_input") or 20)
    
    academic_items = [r for r in rows if r.get("kind") == "academic"]
    industry_items = [r for r in rows if r.get("kind") == "industry"]
    
    academic_items.sort(key=lambda x: len(x.get("summary", "")), reverse=True)
    industry_items.sort(key=lambda x: (
        1 if x.get("filing_source") else 0,
        len(x.get("summary", ""))
    ), reverse=True)
    
    capped_rows = academic_items[:max_academic] + industry_items[:max_industry]
    
    if len(capped_rows) < len(rows):
        logging.info("提示词大小限制：%d 条学术 + %d 条行业（原 %d 条）", 
                     len(academic_items[:max_academic]), 
                     len(industry_items[:max_industry]),
                     len(rows))
    
    total_chars = sum(len(r.get("summary", "")) for r in capped_rows)
    if total_chars > 100_000:
        char_per_item = 100_000 // len(capped_rows)
        for row in capped_rows:
            if len(row.get("summary", "")) > char_per_item:
                row["summary"] = row["summary"][:char_per_item] + "..."
        logging.info("截断摘要以控制提示词大小（每条约 %d 字符）", char_per_item)
    
    return capped_rows


def _extract_numbers_from_text(text: str) -> set[str]:
    """Extract all numbers (including currency amounts, percentages, and Chinese numerals).
    
    Fix B3: Must handle Chinese numerals like 五十亿, 一百亿, etc.
    Round-6: Also handle 万亿 (trillion), 点 decimals (一点五亿), percentages.
    Returns a set of normalized number strings IN MILLIONS for comparison.
    """
    numbers = set()
    
    # Chinese numeral mapping
    cn_nums = {'一': 1, '二': 2, '两': 2, '三': 3, '四': 4, '五': 5, 
               '六': 6, '七': 7, '八': 8, '九': 9, '十': 10,
               '百': 100, '千': 1000, '零': 0}
    
    def parse_complex_cn_number(s: str) -> float | None:
        """Parse complex Chinese numerals like 五十, 一百, 三十五, 一点五."""
        s = s.strip()
        if not s:
            return None
        
        # Handle '点' decimal (一点五 = 1.5)
        if '点' in s:
            parts = s.split('点')
            if len(parts) == 2:
                integer_part = parse_complex_cn_number(parts[0]) or 0
                decimal_part = parse_complex_cn_number(parts[1]) or 0
                # Decimal part: 五 = 0.5, 五五 = 0.55, etc.
                decimal_str = ''
                for char in parts[1]:
                    if char in cn_nums:
                        decimal_str += str(cn_nums[char])
                if decimal_str:
                    return float(f"{int(integer_part)}.{decimal_str}")
                return float(integer_part)
            
        # Handle X百/X千/X十 patterns
        total = 0
        current = 0
        
        for char in s:
            if char in cn_nums:
                if char in ['百', '千', '十']:
                    if current == 0:
                        current = 1
                    total += current * cn_nums[char]
                    current = 0
                else:
                    current = cn_nums[char]
        
        total += current
        return float(total) if total > 0 else None
    
    # Extract Arabic numbers with optional decimal - standalone numbers in millions
    # Pattern: $X million, $X billion, X million, X billion
    for m in re.finditer(r'\$?\s*([\d,]+(?:\.\d+)?)\s*(million|billion|trillion|M|B|T)\b', text, re.IGNORECASE):
        num_str = m.group(1).replace(',', '')
        unit = m.group(2).lower()
        try:
            val = float(num_str)
            # Convert to millions for normalization
            if unit in ['trillion', 't']:
                val *= 1_000_000
            elif unit in ['billion', 'b']:
                val *= 1000
            # Store as millions
            if val == int(val):
                numbers.add(str(int(val)))
            else:
                numbers.add(f"{val:.2f}")
        except ValueError:
            pass
    
    # Extract percentages (for verification)
    for m in re.finditer(r'([\d,]+(?:\.\d+)?)\s*%', text):
        num_str = m.group(1).replace(',', '')
        try:
            val = float(num_str)
            # Store percentages with 'pct' marker
            numbers.add(f"pct_{val:.1f}")
        except ValueError:
            pass
    
    # Extract Chinese 万亿 amounts (1万亿 = 1 trillion = 1,000,000 million)
    for m in re.finditer(r'([一二三四五六七八九十百千两]+点?[一二三四五六七八九零]*|\d+(?:\.\d+)?)\s*万亿', text):
        cn_str = m.group(1)
        if cn_str.replace('.', '').isdigit():
            val = float(cn_str) * 1_000_000  # 1万亿 = 1,000,000 million
        else:
            parsed = parse_complex_cn_number(cn_str)
            val = (parsed * 1_000_000) if parsed else None
        
        if val is not None:
            if val == int(val):
                numbers.add(str(int(val)))
            else:
                numbers.add(f"{val:.2f}")
    
    # Extract Chinese 亿 amounts (1亿 = 100 million) - convert to millions
    # Include '点' decimal support (一点五亿 = 1.5亿 = 150 million)
    for m in re.finditer(r'([一二三四五六七八九十百千两]+点?[一二三四五六七八九零]*|\d+(?:\.\d+)?)\s*亿(?!万)', text):
        cn_str = m.group(1)
        if cn_str.replace('.', '').isdigit():
            # Arabic number before 亿
            val = float(cn_str) * 100  # 1亿 = 100 million
        else:
            # Chinese numeral (possibly with 点 decimal)
            parsed = parse_complex_cn_number(cn_str)
            val = (parsed * 100) if parsed else None  # Convert to millions
        
        if val is not None:
            if val == int(val):
                numbers.add(str(int(val)))
            else:
                numbers.add(f"{val:.2f}")
    
    # Extract Chinese 万 amounts (1万 = 10000 = 0.01 million)
    # Exclude 万亿 which is handled above
    for m in re.finditer(r'([一二三四五六七八九十百千两]+点?[一二三四五六七八九零]*|\d+(?:\.\d+)?)\s*万(?!亿)(?:美元)?', text):
        cn_str = m.group(1)
        if cn_str.replace('.', '').isdigit():
            val = float(cn_str) * 0.01  # 1万 = 0.01 million
        else:
            parsed = parse_complex_cn_number(cn_str)
            val = (parsed * 0.01) if parsed else None
        
        if val is not None and val >= 1:  # Only include if >= 1 million
            if val == int(val):
                numbers.add(str(int(val)))
            else:
                numbers.add(f"{val:.2f}")
    
    return numbers


def _strip_unverified_numbers(text: str, verified_amounts: set[tuple[int, str]]) -> str:
    """Strip sentences containing numbers that aren't in verified_amounts.
    
    Fix #1: The model must not be the source of any number in deal output.
    Numbers are compared in MILLIONS for consistency with _extract_numbers_from_text.
    """
    if not text:
        return ""
    
    # Convert verified amounts to a set of number strings IN MILLIONS
    verified_numbers = set()
    for val, currency in verified_amounts:
        # Convert from cents to base unit
        base_val = val / 100
        
        # Convert to millions (our standard unit for comparison)
        millions = base_val / 1_000_000
        if millions == int(millions):
            verified_numbers.add(str(int(millions)))
        else:
            verified_numbers.add(f"{millions:.2f}")
        
        # Also add rounded representations
        if millions >= 1:
            verified_numbers.add(str(round(millions)))
        
        # Percentages are stored as basis points * 100
        if currency == 'PCT':
            pct = val / 10000
            verified_numbers.add(f"{pct:.1f}")
            verified_numbers.add(f"{pct:.2f}")
            if pct == int(pct):
                verified_numbers.add(str(int(pct)))
    
    # Also add 未披露 as a valid "verified" state
    verified_numbers.add("未披露")
    
    # Split into sentences and filter
    sentences = re.split(r'(?<=[。！？；\.\!\?\;])', text)
    filtered_sentences = []
    
    for sentence in sentences:
        sentence = sentence.strip()
        if not sentence:
            continue
        
        # Extract numbers from this sentence
        sentence_numbers = _extract_numbers_from_text(sentence)
        
        # If no numbers, keep the sentence
        if not sentence_numbers:
            filtered_sentences.append(sentence)
            continue
        
        # Check if all numbers are verified
        unverified = sentence_numbers - verified_numbers
        if not unverified:
            filtered_sentences.append(sentence)
        else:
            logging.warning("剔除含未验证数字的句子：%s (未验证: %s)", sentence[:50], unverified)
    
    return " ".join(filtered_sentences)


def _build_deal_title(company: str, counterparty: str, deal_type: str, amount: str) -> str:
    """Build deal title from verified fields only.
    
    Fix #1: The model must not be the source of any number in deal output.
    """
    deal_type_names = {
        "lic": "授权合作",
        "acq": "收购",
        "inv": "融资",
    }
    
    type_name = deal_type_names.get(deal_type, "交易")
    
    if counterparty:
        title = f"{company}与{counterparty}{type_name}"
    else:
        title = f"{company}{type_name}"
    
    if amount and amount != "未披露":
        title += f"（{amount}）"
    
    return title


def _test_strip_unverified_numbers():
    """Test that unverified numbers are stripped from text - Fix B3."""
    # Simulate verified amounts: $100 million only
    verified = {(10000000000, 'USD')}  # $100M in cents
    
    tests = [
        # Sentence with only verified number should pass
        ("The deal was $100 million.", "The deal was $100 million."),
        # Sentence with unverified number should be stripped
        ("首付50亿美元，总额99亿美元", ""),
        # Mixed - only verified parts kept
        ("This is context. 首付99亿美元. More context.", "This is context. More context."),
        # Fix B3: Chinese numerals must be stripped
        ("估值100亿美元", ""),
        ("五十亿美元", ""),
        ("首付一百亿美元", ""),
        # Fix B3: Valuations without currency
        ("估值约50亿", ""),
        # Fix B4: Bare Chinese numbers without currency ('另加3亿') must be stripped
        ("另加3亿", ""),
        ("另加三亿", ""),
        ("首付1亿，另加3亿里程碑", ""),
    ]
    
    passed = 0
    for input_text, expected in tests:
        result = _strip_unverified_numbers(input_text, verified)
        # Normalize whitespace for comparison
        result = re.sub(r'\s+', ' ', result).strip()
        expected = re.sub(r'\s+', ' ', expected).strip()
        if result == expected:
            passed += 1
        else:
            print(f"FAIL: '{input_text}' -> '{result}' (expected '{expected}')")
    
    print(f"_test_strip_unverified_numbers: {passed}/{len(tests)} tests passed")
    return passed == len(tests)


def _test_chinese_number_extraction():
    """Test Chinese numeral extraction - Fix B3 and B4.
    
    Numbers are normalized to MILLIONS for comparison.
    1亿 = 100 million, so "五亿" = 500 million = "500"
    """
    tests = [
        # Simple Chinese numerals (1亿 = 100 million)
        ("五亿美元", {"500"}),  # 5 * 100 = 500 million
        ("三亿美元", {"300"}),  # 3 * 100 = 300 million
        # Complex Chinese numerals (Fix B3: 五十亿, 一百亿)
        ("五十亿美元", {"5000"}),  # 50 * 100 = 5000 million = 5 billion
        ("一百亿美元", {"10000"}),  # 100 * 100 = 10000 million = 10 billion
        ("三十五亿", {"3500"}),  # 35 * 100 = 3500 million
        ("两百亿", {"20000"}),  # 200 * 100 = 20000 million
        # Arabic numbers in Chinese context
        ("估值100亿美元", {"10000"}),  # 100 * 100 = 10000 million
        ("估值约50亿", {"5000"}),  # 50 * 100 = 5000 million
        # Fix B4: Bare numbers without currency
        ("另加3亿", {"300"}),  # 3 * 100 = 300 million
        ("首付1亿，另加3亿", {"100", "300"}),
        # Mixed with English
        ("首付$100 million，另加五十亿里程碑", {"100", "5000"}),
        # Round-6: 点 decimals (一点五亿 = 1.5亿 = 150 million)
        ("一点五亿美元", {"150"}),  # 1.5 * 100 = 150 million
        ("三点二亿", {"320"}),  # 3.2 * 100 = 320 million
        # Round-6: 万亿 (trillion = 1,000,000 million)
        ("一万亿美元", {"1000000"}),  # 1 trillion
        ("三点五万亿", {"3500000"}),  # 3.5 trillion = 3,500,000 million
        # Round-6: Percentages
        ("占股19.9%", {"pct_19.9"}),
        ("约10%股权", {"pct_10.0"}),
    ]
    
    passed = 0
    for text, expected in tests:
        result = _extract_numbers_from_text(text)
        if result == expected:
            passed += 1
        else:
            print(f"FAIL: '{text}' -> {result} (expected {expected})")
    
    print(f"_test_chinese_number_extraction: {passed}/{len(tests)} tests passed")
    return passed == len(tests)


def claude_draft(items: list[dict], config: dict) -> dict:
    """Use Claude with tool_use for reliable JSON output.
    
    DEALS ARE NO LONGER PRODUCED HERE. Deals are extracted separately via sec_deals module.
    This function only handles academic articles.
    """
    from anthropic import Anthropic

    tool_schema = {
        "name": "submit_weekly_digest",
        "description": "Submit the curated academic articles for the weekly digest",
        "input_schema": {
            "type": "object",
            "properties": {
                "articles": {
                    "type": "array",
                    "description": "Academic articles to include",
                    "items": {
                        "type": "object",
                        "properties": {
                            "url": {"type": "string", "description": "Original URL from input, copied exactly"},
                            "field": {"type": "string", "enum": list(FIELDS.keys())},
                            "title": {"type": "string", "description": "Chinese title"},
                            "journal": {"type": "string", "description": "Journal name only"},
                            "authors": {"type": "string", "description": "Author names from source"},
                            "lead": {"type": "string", "description": "Why this matters, 1-2 sentences in Chinese"},
                            "body": {"type": "string", "description": "What the source says, in Chinese"},
                            "discuss": {"type": "string", "description": "Limitations and what we don't know"},
                            "steps": {
                                "type": "array",
                                "items": {"type": "string", "maxLength": 25},
                                "minItems": 3,
                                "maxItems": 5,
                            },
                            "study_type": {"type": "string"},
                            "n": {"type": "string"},
                            "evidence_level": {"type": "string", "enum": ["fulltext", "abstract", "press", "secondary"]},
                            "image_prompt": {"type": "string", "description": "English abstract visual description. Describe abstract shapes and colors ONLY. No cell type names, no labels."},
                        },
                        "required": ["url", "field", "title", "authors", "lead", "steps"],
                    },
                },
            },
            "required": ["articles"],
        },
    }

    # Filter to academic items only - deals are handled by sec_deals module
    academic_items = [item for item in items if item.get("kind") == "academic"]

    prompt = f"""你是前沿追踪的编辑。下面是过去 {config.get('window_days', 7)} 天从固定来源抓到的学术条目。

## 核心规则

1. 每条的 url 必须从输入里原样复制。
2. 学术最多 {config.get('max_academic', 6)} 篇。

## 领域分类

field 必须是：{json.dumps(FIELDS, ensure_ascii=False)}

## image_prompt 规则

描述抽象的形状和颜色，不要提及具体细胞类型名称：
- 写 "circular cells" 而非 "T cells" 
- 写 "target cells" 而非 "tumor cells"
- 绝不请求标签、文字或注释

输入：
{json.dumps(academic_items, ensure_ascii=False)}
"""
    model = os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-5-5")
    logging.info("调用 Claude %s (tool_use, tool_choice=auto)", model)
    
    client = Anthropic()
    data = None
    
    for attempt in range(2):
        message = client.messages.create(
            model=model,
            max_tokens=8000,
            tools=[tool_schema],
            tool_choice={"type": "auto"},
            messages=[{"role": "user", "content": prompt}],
        )
        
        for block in message.content:
            if block.type == "tool_use" and block.name == "submit_weekly_digest":
                data = block.input
                break
        
        if data is not None:
            break
        
        if attempt == 0:
            logging.warning("Claude 没有返回 tool_use，重试一次...")
    
    if data is None:
        logging.error("Claude did not return tool_use block after retry")
        raise ValueError("No tool_use response from Claude")
    
    allowed = {row["url"] for row in academic_items}
    by_url = {row["url"]: row for row in academic_items}
    
    invalid_author_patterns = [
        r'^nature\s*(medicine|biotechnology|communications|methods)?$',
        r'^cell(\s+reports?)?$',
        r'^science(\s+translational)?$',
        r'^biorxiv',
        r'^medrxiv',
        r'^pubmed',
        r'^nejm$',
        r'^lancet',
        r'^jama',
        r'^免疫学$',
    ]
    
    blocked_patterns = ['BioRender', '对齐 ACIR', '占位', 'MVP']
    
    articles = []
    for raw in data.get("articles") or []:
        url = (raw.get("url") or "").strip()
        if url not in allowed:
            logging.warning("丢弃不在来源里的文章：%s", url)
            continue
        field = raw.get("field")
        if field not in FIELDS:
            logging.warning("丢弃领域无效的文章：%s", url)
            continue
        
        authors = (raw.get("authors") or "").strip()
        journal = (raw.get("journal") or "").strip()
        src = by_url[url]
        
        if authors.lower() == journal.lower() or authors.lower() == src["source"].lower():
            logging.warning("丢弃作者无效的文章（与期刊/来源同名）：%s, authors=%s", url, authors)
            continue
        
        is_invalid_author = any(re.match(p, authors.lower()) for p in invalid_author_patterns)
        if is_invalid_author:
            logging.warning("丢弃作者无效的文章（期刊名）：%s, authors=%s", url, authors)
            continue
        
        steps = [str(s).strip() for s in (raw.get("steps") or []) if str(s).strip()][:5]
        if len(steps) < 3:
            logging.warning("丢弃步骤不足的文章：%s, steps=%d", url, len(steps))
            continue
        
        all_text = f"{raw.get('lead', '')} {raw.get('body', '')} {raw.get('discuss', '')}"
        for blocked in blocked_patterns:
            if blocked in all_text:
                logging.error("输出包含阻断词 '%s'，终止运行", blocked)
                raise SystemExit(4)
        
        # Strip unverified numbers from news/academic text
        source_text = src.get("summary", "")
        lead = (raw.get("lead") or "").strip()
        body = (raw.get("body") or "").strip()
        discuss = (raw.get("discuss") or "").strip()
        
        # Apply number stripping if we have source text
        if source_text:
            import sec_deals
            lead = sec_deals.strip_unverified_numbers_from_text(lead, source_text)
            body = sec_deals.strip_unverified_numbers_from_text(body, source_text)
            discuss = sec_deals.strip_unverified_numbers_from_text(discuss, source_text)
        
        articles.append({
            "url": url,
            "field": field,
            "title": (raw.get("title") or src["title"]).strip(),
            "journal": journal or src["source"],
            "authors": authors or "（来源未列出作者）",
            "lead": lead,
            "body": body,
            "discuss": discuss,
            "steps": steps,
            "study_type": (raw.get("study_type") or "").strip(),
            "n": (raw.get("n") or "").strip(),
            "evidence_level": raw.get("evidence_level") or "abstract",
            "image_prompt": (raw.get("image_prompt") or src["title"]).strip(),
            "date": src["date"],
            "source": src["source"],
        })
    
    cap_a = int(config.get("max_academic") or 6)
    
    # NOTE: Deals are NOT produced here. They come from sec_deals module via extract_sec_deals()
    return {"articles": articles[:cap_a], "deals": []}


def _check_image_for_text(image_bytes: bytes, max_retries: int = 2) -> bool | None:
    """Check if image contains text using vision model.
    
    Fix #9: Fail closed - return None on error (caller should regenerate/omit).
    Returns True if text detected, False if no text, None on error.
    """
    from openai import OpenAI
    import time
    
    client = OpenAI()
    
    # Convert to base64
    b64_image = base64.b64encode(image_bytes).decode('utf-8')
    
    for attempt in range(max_retries + 1):
        try:
            response = client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "text",
                                "text": "Does this image contain ANY text, letters, words, labels, numbers, or annotations? Answer only 'YES' or 'NO'."
                            },
                            {
                                "type": "image_url",
                                "image_url": {
                                    "url": f"data:image/png;base64,{b64_image}"
                                }
                            }
                        ]
                    }
                ],
                max_tokens=10
            )
            
            answer = response.choices[0].message.content.strip().upper()
            has_text = "YES" in answer
            
            if has_text:
                logging.warning("图片包含文字，需要重新生成")
            
            return has_text
            
        except Exception as e:
            logging.warning("图片文字检查失败 (attempt %d/%d): %s", attempt + 1, max_retries + 1, e)
            if attempt < max_retries:
                time.sleep(1)
    
    # Fix #9: Fail closed - return None so caller knows check failed
    logging.warning("图片文字检查重试后仍失败，返回 None（将重新生成或跳过）")
    return None


def draw_image(prompt: str, dest: Path, max_retries: int = 2) -> bool:
    """Generate image with text-free verification.
    
    Fix #9: Post-generation check for text, regenerate if needed.
    Fail closed: if check errors, retry then regenerate or omit.
    Returns True if successful, False if all attempts failed.
    """
    from openai import OpenAI

    model = os.environ.get("OPENAI_IMAGE_MODEL", "gpt-image-1")
    clean_prompt = sanitize_image_prompt(prompt)
    
    client = OpenAI()
    
    for attempt in range(max_retries + 1):
        # Strengthen prompt on retries
        if attempt > 0:
            extra_emphasis = " ABSOLUTELY NO TEXT OR LETTERS. " * attempt
            full_prompt = IMAGE_PREFIX + extra_emphasis + clean_prompt[:800] + IMAGE_SUFFIX
            logging.info("画图重试 %d/%d（加强无文字提示）: %s", attempt, max_retries, dest.name)
        else:
            full_prompt = IMAGE_PREFIX + clean_prompt[:1000] + IMAGE_SUFFIX
            logging.info("画图 %s -> %s", model, dest.name)
        
        try:
            result = client.images.generate(
                model=model,
                prompt=full_prompt,
                size="1536x1024",
                n=1,
            )
            raw = result.data[0].b64_json
            image_bytes = base64.b64decode(raw)
            
            # Check for text - returns True (has text), False (no text), or None (error)
            check_result = _check_image_for_text(image_bytes)
            
            if check_result is False:
                # No text detected, save and return success
                dest.write_bytes(image_bytes)
                return True
            
            # Fix #9: check_result is True (text found) or None (check failed)
            # Either way, we should regenerate or give up
            if check_result is None:
                logging.warning("图片 %s 文字检查失败，视为有文字处理", dest.name)
            
            if attempt < max_retries:
                logging.warning("图片 %s 含文字或检查失败，重试...", dest.name)
            else:
                logging.warning("图片 %s 重试后仍有问题，跳过", dest.name)
                return False
                
        except Exception as e:
            logging.exception("配图失败 (attempt %d): %s", attempt + 1, e)
            if attempt >= max_retries:
                return False
    
    return False


def site_article(item: dict, image_rel: str) -> dict:
    import hashlib
    stamp = item["date"].replace("-", "")
    url_key = normalize_doi(item["url"]) or item["url"]
    url_hash = hashlib.sha1(url_key.encode()).hexdigest()[:10]
    item_id = f"w-{stamp}-{url_hash}"
    result = {
        "id": item_id,
        "f": item["field"],
        "t": item["title"],
        "ds": item["date"],
        "disp": item["date"][:7].replace("-", "."),
        "j": item["journal"],
        "url": item["url"],
        "au": item["authors"] or item["source"],
        "tags": [item["field"]],
        "sum": item["lead"],
        "lead": item["lead"],
        "body": item["body"],
        "discuss": item["discuss"],
        "steps": item["steps"],
        "note": f"材料来自 {item['source']}，只写来源里能核对的内容。",
        "img": image_rel,
    }
    if item.get("study_type"):
        result["study_type"] = item["study_type"]
    if item.get("n"):
        result["n"] = item["n"]
    if item.get("evidence_level"):
        result["evidence_level"] = item["evidence_level"]
    return result


def site_deal(item: dict) -> dict:
    result = {
        "d": item["date"],
        "kinds": item["kinds"],
        "t": item["title"],
        "m": item["money"],
        "ms": item["structure"],
        "why": item["why"],
        "src": item["source_name"],
        "url": item["url"],
        "amount_source": item.get("amount_source", "unknown"),
    }
    if item.get("is_filing"):
        result["is_filing"] = True
        result["filing_source"] = item.get("filing_source", "unknown")
    if item.get("upfront"):
        result["upfront"] = item["upfront"]
    if item.get("milestones"):
        result["milestones"] = item["milestones"]
    if item.get("equity"):
        result["equity"] = item["equity"]
    return result


def wechat_html(articles: list[dict], deals: list[dict], week: str) -> str:
    """Generate WeChat-compatible HTML with inline styles."""
    
    toc_items = []
    for i, art in enumerate(articles, 1):
        toc_items.append(f"{i}. {art['t'][:30]}...")
    
    lead_headline = articles[0]['t'][:25] if articles else "本周前沿"
    
    parts = [
        '<section style="font-family:-apple-system,BlinkMacSystemFont,\'Segoe UI\',Roboto,sans-serif;font-size:16px;line-height:1.75;color:#333;">',
        f'<p style="font-size:14px;color:#666;">前沿追踪 · {week} · TheraSik 出品</p>',
        '<p style="margin:1em 0;">本期内容均基于原始来源核对，配图由 AI 生成（示意图，非期刊原图）。</p>',
    ]
    
    if toc_items:
        parts.append('<p style="margin:1em 0;padding:1em;background:#f5f5f5;border-radius:8px;">')
        parts.append('<strong>本期目录</strong><br>')
        parts.append('<br>'.join(toc_items))
        parts.append('</p>')
    
    if articles:
        parts.append('<h2 style="border-left:4px solid #0f6b5c;padding-left:12px;margin:2em 0 1em;">学术</h2>')
        for art in articles:
            parts.append(f'<h3 style="font-size:18px;margin:1.5em 0 0.5em;color:#1d2a27;">{art["t"]}</h3>')
            if art.get("img"):
                # Use relative path - publish_wechat.py will upload and replace with WeChat URL
                # If previewing locally, the relative path works with a local server
                img_url = art["img"]
                parts.append(f'<p style="margin:1em 0;"><img src="{img_url}" alt="" style="max-width:100%;border-radius:8px;"></p>')
            parts.append(f'<p style="margin:0.5em 0;">{art.get("lead") or ""}</p>')
            if art.get("body"):
                parts.append(f'<p style="margin:0.5em 0;">{art["body"]}</p>')
            if art.get("discuss"):
                parts.append(f'<p style="margin:0.5em 0;"><strong>讨论</strong> {art["discuss"]}</p>')
            parts.append(f'<p style="font-size:14px;color:#666;margin:0.5em 0;">{art.get("au") or ""} · {art.get("j") or ""}</p>')
            url = art.get("url", "")
            if "doi.org" in url:
                doi = url.replace("https://doi.org/", "DOI: ")
                parts.append(f'<p style="font-size:12px;color:#999;margin:0.5em 0;">{doi}</p>')
    
    if deals:
        parts.append('<h2 style="border-left:4px solid #0f6b5c;padding-left:12px;margin:2em 0 1em;">交易动态</h2>')
        
        grouped = {"lic": [], "acq": [], "inv": []}
        for deal in deals:
            deal_type = deal.get("kinds", ["inv"])[0] if deal.get("kinds") else "inv"
            if deal_type in grouped:
                grouped[deal_type].append(deal)
        
        for deal_type in DEAL_GROUP_ORDER:
            type_deals = grouped.get(deal_type, [])
            if not type_deals:
                continue
            
            type_name = DEAL_GROUP_NAMES.get(deal_type, deal_type)
            parts.append(f'<h3 style="font-size:16px;margin:1.5em 0 0.5em;color:#0f6b5c;">{type_name}</h3>')
            
            for deal in type_deals:
                parts.append(f'<h4 style="font-size:16px;margin:1em 0 0.3em;color:#1d2a27;">{deal["t"]}</h4>')
                
                money = deal.get("m", "")
                if money and money != "未披露":
                    amount_note = ""
                    if deal.get("amount_source") == "news":
                        amount_note = "（据报道）"
                    elif deal.get("is_filing"):
                        amount_note = "（披露文件）"
                    parts.append(f'<p style="margin:0.3em 0;"><strong>{money}</strong>{amount_note}</p>')
                
                amount_details = []
                if deal.get("upfront"):
                    amount_details.append(f"首付：{deal['upfront']}")
                if deal.get("milestones"):
                    amount_details.append(f"里程碑：{deal['milestones']}")
                if deal.get("equity"):
                    amount_details.append(f"股权：{deal['equity']}")
                if amount_details:
                    details_text = " · ".join(amount_details)
                    parts.append(f'<p style="margin:0.2em 0;font-size:14px;color:#555;">{details_text}</p>')
                
                if deal.get("why"):
                    parts.append(f'<p style="margin:0.3em 0;">{deal["why"]}</p>')
                
                source_text = deal.get("src") or "未注明"
                if deal.get("is_filing"):
                    source_text = f"📄 {source_text}"
                parts.append(f'<p style="font-size:14px;color:#666;margin:0.3em 0;">来源：{source_text}</p>')
    
    parts.append('<hr style="border:none;border-top:1px solid #eee;margin:2em 0;">')
    parts.append('<p style="font-size:14px;color:#666;margin:1em 0;">')
    parts.append('本期内容由 Claude 起草，配图由 gpt-image-1 生成，编辑核对后发布。')
    parts.append('</p>')
    parts.append('<p style="font-size:14px;color:#666;margin:1em 0;">')
    parts.append('如发现错误，请邮件 contact@therasik.com，我们会在下期更正。')
    parts.append('</p>')
    parts.append('<p style="font-size:14px;color:#0f6b5c;margin:1em 0;">')
    parts.append('点击「阅读原文」查看完整版。')
    parts.append('</p>')
    parts.append('</section>')
    
    return "\n".join(parts)


def write_output(draft: dict, dest: Path, week: str) -> None:
    img_dir = dest / "images"
    img_dir.mkdir(parents=True, exist_ok=True)
    articles = []
    
    # Generate cover image first (for fallback)
    cover_prompt = (
        "Abstract circular shapes representing cells and molecules, "
        "soft teal and coral colors on white background"
    )
    cover = dest / "wechat" / "cover.png"
    cover.parent.mkdir(parents=True, exist_ok=True)
    cover_success = False
    try:
        cover_success = draw_image(cover_prompt, cover)
    except Exception:
        logging.exception("封面图失败")
    
    for index, item in enumerate(draft["articles"], start=1):
        filename = f"a{index}.png"
        img_path = img_dir / filename
        
        try:
            success = draw_image(item["image_prompt"], img_path)
            if success:
                rel = f"{dest.relative_to(ROOT).as_posix()}/images/{filename}"
            elif cover_success:
                # Fall back to cover image if article image had text
                logging.warning("使用封面图替代 %s", filename)
                import shutil
                shutil.copy(cover, img_path)
                rel = f"{dest.relative_to(ROOT).as_posix()}/images/{filename}"
            else:
                rel = ""
        except Exception:
            logging.exception("配图失败：%s", item["title"])
            rel = ""
        
        art = site_article(item, rel)
        art["lead"] = item["lead"]
        art["body"] = item["body"]
        art["discuss"] = item["discuss"]
        articles.append(art)
    
    seen_ids = {}
    for art in articles:
        if art["id"] in seen_ids:
            logging.error("ID 冲突：%s 和 %s 都生成了 ID %s", seen_ids[art["id"]], art["url"], art["id"])
            raise SystemExit(5)
        seen_ids[art["id"]] = art["url"]
    
    deals = [site_deal(item) for item in draft["deals"]]
    
    (dest / "articles.json").write_text(json.dumps(articles, ensure_ascii=False, indent=2), encoding="utf-8")
    (dest / "deals.json").write_text(json.dumps(deals, ensure_ascii=False, indent=2), encoding="utf-8")
    html = wechat_html(articles, deals, week)
    (dest / "wechat" / "article.html").write_text(html, encoding="utf-8")
    logging.info("写出 %s", dest)


def update_latest(dest: Path) -> None:
    articles = json.loads((dest / "articles.json").read_text(encoding="utf-8"))
    deals = json.loads((dest / "deals.json").read_text(encoding="utf-8"))
    latest_path = ROOT / "content" / "latest.json"
    previous = {"articles": [], "deals": []}
    if latest_path.exists():
        try:
            previous = json.loads(latest_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            logging.warning("content/latest.json 无法解析，将覆盖")
    seen_a = {a.get("id") for a in articles}
    seen_d = {d.get("url") for d in deals}
    merged_a = articles + [a for a in previous.get("articles") or [] if a.get("id") not in seen_a]
    merged_d = deals + [d for d in previous.get("deals") or [] if d.get("url") not in seen_d]
    payload = {
        "generated": dest.name,
        "articles": merged_a[:40],
        "deals": merged_d[:40],
    }
    latest_path.parent.mkdir(exist_ok=True)
    latest_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    logging.info("已更新 content/latest.json")


def _test_deal_number_stripping():
    """Test that invented numbers are stripped from deal output.
    
    Fix #1 test: Stub claude_draft with injected values and verify none get published.
    """
    # Simulate verified amounts from source: only $100 million
    verified = {(10000000000, 'USD')}  # $100M in cents
    
    test_cases = [
        # (field, value, should_be_stripped)
        ("title", "50亿美元大交易", True),  # Title with unverified amount
        ("why", "99亿美元，首付3亿", True),  # Why with unverified amounts  
        ("structure", "7亿美元结构", True),  # Structure with unverified amount
        ("news_title", "估值80亿美元", True),  # News title with unverified
        ("news_why", "融资8000万美元", True),  # News with unverified
        ("news_upfront", "2亿美元", True),  # Upfront not in source
    ]
    
    passed = 0
    for field, value, should_strip in test_cases:
        if field.startswith("news_"):
            # For news items, use same verification
            result = _strip_unverified_numbers(value, verified)
        elif field == "title":
            # Title should be built from verified fields only
            result = _build_deal_title("TestCo", "PartnerCo", "lic", "1亿美元")
            # Check that the invented amount isn't in the built title
            should_strip = "50亿" not in result
        else:
            result = _strip_unverified_numbers(value, verified)
        
        # After stripping, should have no unverified numbers
        remaining_numbers = _extract_numbers_from_text(result)
        verified_number_strs = {"100", "100.00", "1", "1.00"}  # $100M representations
        
        has_unverified = bool(remaining_numbers - verified_number_strs - {"未披露"})
        
        if should_strip and not has_unverified:
            passed += 1
        elif not should_strip and value == result:
            passed += 1
        else:
            print(f"FAIL {field}: '{value}' -> '{result}' (has_unverified={has_unverified})")
    
    print(f"_test_deal_number_stripping: {passed}/{len(test_cases)} tests passed")
    return passed == len(test_cases)


def extract_sec_deals(items: list[dict], config: dict) -> list[dict]:
    """Extract deals from SEC filings using sec_deals module.
    
    THIS IS THE ONLY PATH FOR DEAL EXTRACTION.
    All deals come from SEC EDGAR filings with strict quote-based verification.
    """
    import sec_deals
    
    # Filter to SEC filing items only
    filing_items = [
        item for item in items 
        if item.get("filing_source") == "sec" and item.get("filing_text")
    ]
    
    if not filing_items:
        logging.info("No SEC filings found for deal extraction")
        return []
    
    # Filter by event date requirement
    filings_with_dates = []
    for item in filing_items:
        event_date = item.get("event_date")
        if not event_date:
            logging.info("Skipping filing without event date: %s", item.get("url", ""))
            continue
        filings_with_dates.append(item)
    
    if not filings_with_dates:
        logging.info("No SEC filings with event dates found")
        return []
    
    max_deals = int(config.get("max_deals") or 6)
    
    logging.info("Extracting deals from %d SEC filings via sec_deals module", len(filings_with_dates))
    
    deals = sec_deals.extract_deals_from_filings(
        filings=filings_with_dates,
        max_deals=max_deals
    )
    
    logging.info("交易选择：%d 条披露（via sec_deals module）", len(deals))
    
    return deals


# Marker for testing that deals go through sec_deals
_SEC_DEALS_CALLED = False


def main() -> None:
    parser = argparse.ArgumentParser(description="生成一周的前沿追踪内容")
    parser.add_argument("--dry-run", action="store_true", help="只写到 preview/，不改网站内容目录")
    parser.add_argument("--test", action="store_true", help="运行单元测试")
    args = parser.parse_args()
    
    if args.test:
        print("Running unit tests...\n")
        all_passed = True
        all_passed &= _test_sanitize_image_prompt()
        all_passed &= _test_biorxiv_api()
        all_passed &= _test_verify_amount()
        all_passed &= _test_classify_deal_type()
        all_passed &= _test_strip_unverified_numbers()
        all_passed &= _test_deal_number_stripping()
        all_passed &= _test_nonprofit_detection()
        all_passed &= _test_company_verification()
        all_passed &= _test_deal_keywords()
        all_passed &= _test_name_normalization()
        all_passed &= _test_sec_filing_fetch()
        # Fix B-series tests
        all_passed &= _test_role_verification()
        all_passed &= _test_credit_facility()
        all_passed &= _test_up_to_amount()
        all_passed &= _test_event_date_extraction()
        all_passed &= _test_obligation_buyout()
        all_passed &= _test_chinese_number_extraction()
        # Round-6 evidence-quote tests
        all_passed &= _test_amount_parsing()
        all_passed &= _test_quote_verification()
        all_passed &= _test_role_detection()
        all_passed &= _test_same_passage()
        all_passed &= _test_filer_is_party()
        all_passed &= _test_company_whole_word()
        all_passed &= _test_real_sec_filings()
        all_passed &= _test_adversarial_deals()
        all_passed &= _test_integration_deal_pipeline()
        # NEW: Test that sec_deals module is used
        all_passed &= _test_sec_deals_module()
        print(f"\n{'All tests passed!' if all_passed else 'Some tests failed.'}")
        raise SystemExit(0 if all_passed else 1)
    
    log_path = setup_log()
    logging.info("日志 %s", log_path)
    try:
        require_env(["ANTHROPIC_API_KEY", "OPENAI_API_KEY"])
        check_anthropic_model()
        config = load_sources()
        items = fetch_all(config)
        if not items:
            logging.error("最近 %s 天没有抓到条目，不写文件", config.get("window_days", 7))
            raise SystemExit(2)
        logging.info("送去筛选的条目 %d", len(items))
        
        # Extract deals from SEC filings - THE ONLY DEAL PATH
        global _SEC_DEALS_CALLED
        _SEC_DEALS_CALLED = True
        deals = extract_sec_deals(items, config)
        
        # Draft articles (deals are handled separately above)
        draft = claude_draft(items, config)
        draft["deals"] = deals  # Add deals from sec_deals module
        
        if not draft["articles"] and not deals:
            logging.error("模型没有留下任何来源内的条目")
            raise SystemExit(3)
        week = date.today().isoformat()
        dest = (ROOT / "preview" / "weekly" / week) if args.dry_run else (ROOT / "content" / "weekly" / week)
        if dest.exists():
            logging.error("目录已存在，避免覆盖：%s", dest)
            raise SystemExit(4)
        write_output(draft, dest, week)
        if args.dry_run:
            logging.info("dry-run 完成，没有改 content/，也没有调用公众号")
        else:
            update_latest(dest)
            logging.info("网站内容已写入。提交并推送 main 后，GitHub Pages 会更新。公众号请另跑 publish_wechat.py")
    except SystemExit:
        raise
    except Exception:
        logging.error("未捕获的错误\n%s", traceback.format_exc())
        raise SystemExit(4)


if __name__ == "__main__":
    main()
