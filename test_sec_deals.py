#!/usr/bin/env python3
"""End-to-end tests for sec_deals module.

Tests that call the real production entry points with mocked Claude responses.
All tests use real asserts so pytest can catch failures.
"""

import json
import logging
import sys
import os
from pathlib import Path
from unittest.mock import MagicMock, patch
import pytest

sys.path.insert(0, str(Path(__file__).parent))

import sec_deals

logging.basicConfig(level=logging.INFO)

FIXTURES_DIR = Path(__file__).parent / "test_fixtures"


def load_fixture(name: str) -> str:
    """Load a filing text fixture."""
    return (FIXTURES_DIR / name).read_text()


# =============================================================================
# MOCK CLAUDE RESPONSES FOR REAL FILINGS
# =============================================================================

REGENERON_SANOFI_RESPONSE = {
    "deal_type": "license_collaboration",
    "counterparty_name": "Sanofi",
    "type_quote": 'entered into a Sixth Amendment (the "Amendment") to the Antibody License and Collaboration Agreement, dated as of January 2007, as amended (the "Collaboration Agreement"), with Sanofi S.A. ("Sanofi")',
    "counterparty_quote": 'Pursuant to the Amendment, Sanofi will pay Regeneron an upfront payment of $1.0 billion',
    "amounts": [
        {"kind": "upfront", "quote": "Sanofi will pay Regeneron an upfront payment of $1.0 billion in cash"},
        {"kind": "milestones_total", "quote": "eligible to receive up to an aggregate of $7.0 billion in development, regulatory and commercial milestone payments"}
    ]
}

ALECTOR_GENENTECH_RESPONSE = {
    "deal_type": "license_collaboration",
    "counterparty_name": "Genentech",
    "type_quote": 'the Company granted Genentech an exclusive, worldwide license to develop, manufacture and commercialize antibody products targeting progranulin',
    "counterparty_quote": 'entered into a Collaboration Agreement (the "Agreement") with Genentech, Inc. ("Genentech")',
    "amounts": [
        {"kind": "upfront", "quote": "the Company will receive an upfront payment of $100 million in cash"},
        {"kind": "milestones_total", "quote": "eligible to receive up to an aggregate of $1.17 billion in potential development, regulatory and commercial milestone payments"}
    ]
}

IMMUNOME_BMS_RESPONSE = {
    "deal_type": "obligation_buyout",
    "counterparty_name": "Bristol-Myers Squibb",
    # type_quote must contain buyout language to validate the deal type
    "type_quote": 'the Company paid BMS $20.0 million in cash and issued 4,425,487 shares of the Company\'s common stock to BMS. In consideration for such payments, the Amendment removes the Company\'s obligation to make any milestone and royalty payments to BMS',
    "counterparty_quote": 'entered into Amendment No. 4 (the "Amendment") to the License and Collaboration Agreement, dated as of October 2018, as amended (the "Original Agreement"), with Bristol-Myers Squibb Company ("BMS")',
    "amounts": [
        {"kind": "purchase_price", "quote": "the Company paid BMS $20.0 million in cash"},
        {"kind": "equity", "quote": "issued 4,425,487 shares of the Company's common stock to BMS"}
    ]
}

ROCKET_HERCULES_RESPONSE = {
    "deal_type": "debt_facility",
    "counterparty_name": "Hercules Capital",
    "type_quote": 'entered into a Loan and Security Agreement (the "Credit Agreement") with Hercules Capital, Inc. ("Hercules")',
    "counterparty_quote": 'a Loan and Security Agreement (the "Credit Agreement") with Hercules Capital, Inc. ("Hercules"), as administrative agent and collateral agent',
    "amounts": [
        {"kind": "facility_size", "quote": "term loan commitments in an aggregate principal amount of up to $150.0 million"},
        {"kind": "drawn", "quote": "The first tranche of $35.0 million (Tranche 1-A) was funded on September 30, 2026"}
    ]
}


# =============================================================================
# REAL FILING TESTS
# =============================================================================

class TestRealFilings:
    """Tests against real SEC filing fixtures."""
    
    def test_regeneron_sanofi_deal(self):
        """Test Regeneron-Sanofi $1B upfront deal extraction."""
        filing_text = load_fixture("regeneron_sanofi.txt")
        
        deal = sec_deals.process_sec_deal(
            filing_text=filing_text,
            filer_name="Regeneron Pharmaceuticals",
            filing_url="https://www.sec.gov/test/regeneron",
            filing_date="2026-10-06",
            event_date="2026-10-01",
            claude_response=REGENERON_SANOFI_RESPONSE
        )
        
        assert deal is not None, "Regeneron-Sanofi deal should be extracted"
        assert deal["counterparty"] == "Sanofi"
        assert deal["deal_type"] == "license_collaboration"
        
        # Check amounts
        amounts = deal.get("verified_amounts", [])
        assert len(amounts) >= 1, "Should have at least 1 verified amount"
        
        # Should have $1B upfront (1000 million)
        upfront_amounts = [a for a in amounts if a["kind"] == "upfront"]
        assert len(upfront_amounts) == 1
        assert upfront_amounts[0]["value_millions"] == 1000
        
        # Money should show $1B upfront
        assert "10 亿美元" in deal["money"]
    
    def test_alector_genentech_deal(self):
        """Test Alector-Genentech $100M upfront deal extraction."""
        filing_text = load_fixture("alector_genentech.txt")
        
        deal = sec_deals.process_sec_deal(
            filing_text=filing_text,
            filer_name="Alector, Inc.",
            filing_url="https://www.sec.gov/test/alector",
            filing_date="2026-10-05",
            event_date="2026-09-30",
            claude_response=ALECTOR_GENENTECH_RESPONSE
        )
        
        assert deal is not None, "Alector-Genentech deal should be extracted"
        assert deal["counterparty"] == "Genentech"
        assert deal["filer_role"] == "licensor"
        
        # Check amounts
        amounts = deal.get("verified_amounts", [])
        assert len(amounts) >= 1
        
        # Check milestones
        milestone_amounts = [a for a in amounts if a["kind"] == "milestones_total"]
        assert len(milestone_amounts) == 1
        assert milestone_amounts[0]["value_millions"] == 1170  # $1.17B
    
    def test_immunome_bms_buyout(self):
        """Test Immunome-BMS obligation buyout with shares."""
        filing_text = load_fixture("immunome_bms.txt")
        
        deal = sec_deals.process_sec_deal(
            filing_text=filing_text,
            filer_name="Immunome, Inc.",
            filing_url="https://www.sec.gov/test/immunome",
            filing_date="2026-10-05",
            event_date="2026-10-02",
            claude_response=IMMUNOME_BMS_RESPONSE
        )
        
        assert deal is not None, "Immunome-BMS deal should be extracted"
        assert deal["deal_type"] == "obligation_buyout"
        assert deal["filer_role"] == "payer"
        
        # Check amounts
        amounts = deal.get("verified_amounts", [])
        
        # Should have $20M cash
        cash_amounts = [a for a in amounts if a["kind"] == "purchase_price"]
        assert len(cash_amounts) == 1
        assert cash_amounts[0]["value_millions"] == 20
        
        # Should have shares - verify they're NOT 100x wrong
        share_amounts = [a for a in amounts if a["currency"] == "SHARES"]
        assert len(share_amounts) == 1
        assert share_amounts[0]["value_millions"] == 4425487  # Raw count
        
        # Verify share rendering is correct (约 442.5 万股, NOT 4.4 亿股)
        # The rendered string is stored in the 'rendered' key
        share_rendered = share_amounts[0]["rendered"]
        assert "442" in share_rendered, f"Should show ~442万, got {share_rendered}"
        assert "亿" not in share_rendered, f"Should NOT show 亿, got {share_rendered}"
    
    def test_rocket_hercules_credit_facility(self):
        """Test Rocket-Hercules credit facility extraction."""
        filing_text = load_fixture("rocket_hercules.txt")
        
        deal = sec_deals.process_sec_deal(
            filing_text=filing_text,
            filer_name="Rocket Pharmaceuticals, Inc.",
            filing_url="https://www.sec.gov/test/rocket",
            filing_date="2026-10-06",
            event_date="2026-09-30",
            claude_response=ROCKET_HERCULES_RESPONSE
        )
        
        assert deal is not None, "Rocket-Hercules deal should be extracted"
        assert deal["deal_type"] == "debt_facility"
        assert deal["counterparty"] == "Hercules Capital"
        assert deal["filer_role"] == "borrower"


# =============================================================================
# ADVERSARIAL TESTS
# =============================================================================

class TestAdversarial:
    """Tests for adversarial/malicious model responses."""
    
    def test_fabricated_type_quote_dropped(self):
        """Model returns a quote that doesn't exist in filing - should drop."""
        filing_text = load_fixture("alector_genentech.txt")
        
        deal = sec_deals.process_sec_deal(
            filing_text=filing_text,
            filer_name="Alector, Inc.",
            filing_url="https://test",
            filing_date="2026-10-01",
            event_date="2026-10-01",
            claude_response={
                "deal_type": "license_collaboration",
                "counterparty_name": "Genentech",
                "type_quote": "Alector granted Genentech exclusive worldwide rights",  # NOT IN FILING
                "counterparty_quote": "Agreement with Genentech",
                "amounts": []
            }
        )
        
        assert deal is None, "Deal with fabricated type_quote should be dropped"
    
    def test_wrong_counterparty_dropped(self):
        """Model names a counterparty not in the filing - should drop."""
        filing_text = load_fixture("alector_genentech.txt")
        
        deal = sec_deals.process_sec_deal(
            filing_text=filing_text,
            filer_name="Alector, Inc.",
            filing_url="https://test",
            filing_date="2026-10-01",
            event_date="2026-10-01",
            claude_response={
                "deal_type": "license_collaboration",
                "counterparty_name": "Pfizer",  # NOT IN FILING
                "type_quote": "the Company granted Genentech an exclusive, worldwide license",
                "counterparty_quote": "Agreement with Pfizer",  # NOT IN FILING
                "amounts": []
            }
        )
        
        assert deal is None, "Deal with wrong counterparty should be dropped"
    
    def test_empty_counterparty_quote_dropped(self):
        """Empty counterparty quote should drop the deal."""
        filing_text = load_fixture("alector_genentech.txt")
        
        deal = sec_deals.process_sec_deal(
            filing_text=filing_text,
            filer_name="Alector, Inc.",
            filing_url="https://test",
            filing_date="2026-10-01",
            event_date="2026-10-01",
            claude_response={
                "deal_type": "license_collaboration",
                "counterparty_name": "Genentech",
                "type_quote": "the Company granted Genentech an exclusive, worldwide license",
                "counterparty_quote": "",  # EMPTY
                "amounts": []
            }
        )
        
        assert deal is None, "Deal with empty counterparty_quote should be dropped"
    
    def test_az_amazon_no_match(self):
        """AstraZeneca should NOT match Amazon via character class."""
        filing_text = "Amazon Web Services announced a cloud partnership."
        
        deal = sec_deals.process_sec_deal(
            filing_text=filing_text,
            filer_name="Amazon.com",
            filing_url="https://test",
            filing_date="2026-10-01",
            event_date="2026-10-01",
            claude_response={
                "deal_type": "license_collaboration",
                "counterparty_name": "AstraZeneca",
                "type_quote": "Amazon Web Services announced a cloud partnership",
                "counterparty_quote": "Amazon Web Services announced",
                "amounts": []
            }
        )
        
        assert deal is None, "AstraZeneca should not match via substring"
    
    def test_acquisition_type_requires_acquire_language(self):
        """Claiming acquisition when quote only shows license should fail or correct."""
        filing_text = load_fixture("alector_genentech.txt")
        
        deal = sec_deals.process_sec_deal(
            filing_text=filing_text,
            filer_name="Alector, Inc.",
            filing_url="https://test",
            filing_date="2026-10-01",
            event_date="2026-10-01",
            claude_response={
                "deal_type": "acquisition",  # WRONG - this is a license
                "counterparty_name": "Genentech",
                "type_quote": "the Company granted Genentech an exclusive, worldwide license",
                "counterparty_quote": "Agreement with Genentech, Inc.",
                "amounts": []
            }
        )
        
        # Should either drop or correct to license_collaboration
        if deal is not None:
            assert deal["deal_type"] != "acquisition", "Should not allow acquisition without acquire language"


# =============================================================================
# NON-DEAL FILTERING TESTS
# =============================================================================

class TestNonDealFiltering:
    """Tests for filtering non-deal agreements."""
    
    def test_services_agreement_dropped(self):
        """Master Services Agreement should be dropped."""
        filing_text = """
        Helio Bio entered into a Master Services Agreement with WuXi AppTec
        for $12.0 million in research services.
        """
        
        deal = sec_deals.process_sec_deal(
            filing_text=filing_text,
            filer_name="Helio Bio, Inc.",
            filing_url="https://test",
            filing_date="2026-10-01",
            event_date="2026-10-01",
            claude_response={
                "deal_type": "license_collaboration",
                "counterparty_name": "WuXi AppTec",
                "type_quote": "entered into a Master Services Agreement with WuXi AppTec",
                "counterparty_quote": "Master Services Agreement with WuXi AppTec",
                "amounts": [{"kind": "upfront", "quote": "$12.0 million"}]
            }
        )
        
        assert deal is None, "Services agreement should be dropped"
    
    def test_lease_agreement_dropped(self):
        """Office lease should be dropped."""
        filing_text = """
        The Company entered into a lease agreement with Alexandria Real Estate
        for $4.5 million in annual rent.
        """
        
        deal = sec_deals.process_sec_deal(
            filing_text=filing_text,
            filer_name="Helio Bio, Inc.",
            filing_url="https://test",
            filing_date="2026-10-01",
            event_date="2026-10-01",
            claude_response={
                "deal_type": "license_collaboration",
                "counterparty_name": "Alexandria Real Estate",
                "type_quote": "entered into a lease agreement with Alexandria Real Estate",
                "counterparty_quote": "lease agreement with Alexandria Real Estate",
                "amounts": []
            }
        )
        
        assert deal is None, "Lease agreement should be dropped"


# =============================================================================
# AMOUNT PARSING TESTS
# =============================================================================

class TestAmountParsing:
    """Tests for amount parsing edge cases."""
    
    def test_bare_dollars_not_scaled_to_millions(self):
        """$7,500,000 without 'million' should be 7.5 million, not 7500 million."""
        parsed = sec_deals.parse_amount_from_quote("$7,500,000", sec_deals.AmountKind.UPFRONT)
        
        assert parsed is not None
        assert abs(parsed.value_in_millions - 7.5) < 0.01, f"Expected 7.5M, got {parsed.value_in_millions}M"
    
    def test_explicit_million_works(self):
        """$7.5 million should parse correctly."""
        parsed = sec_deals.parse_amount_from_quote("$7.5 million", sec_deals.AmountKind.UPFRONT)
        
        assert parsed is not None
        assert parsed.value_in_millions == 7.5
    
    def test_billion_scales_correctly(self):
        """$1.5 billion should be 1500 million."""
        parsed = sec_deals.parse_amount_from_quote("$1.5 billion", sec_deals.AmountKind.UPFRONT)
        
        assert parsed is not None
        assert parsed.value_in_millions == 1500
    
    def test_sanity_cap_rejects_over_100b(self):
        """Amounts over $100 billion should be rejected."""
        parsed = sec_deals.parse_amount_from_quote("$500 billion", sec_deals.AmountKind.UPFRONT)
        
        assert parsed is None, "Amounts over $100B should be rejected"
    
    def test_conditional_words_rejected_for_upfront(self):
        """'may receive up to' should fail for upfront (only allowed for milestones)."""
        parsed = sec_deals.parse_amount_from_quote(
            "may receive up to $50 million", 
            sec_deals.AmountKind.UPFRONT
        )
        
        assert parsed is None, "Conditional words should fail for upfront"
    
    def test_conditional_words_allowed_for_milestones(self):
        """'may receive up to' should work for milestones_total."""
        parsed = sec_deals.parse_amount_from_quote(
            "may receive up to $50 million", 
            sec_deals.AmountKind.MILESTONES_TOTAL
        )
        
        assert parsed is not None
        assert parsed.up_to is True
        assert parsed.value_in_millions == 50
    
    def test_eur_symbol_parsed(self):
        """€120 million should parse as EUR."""
        parsed = sec_deals.parse_amount_from_quote("€120 million", sec_deals.AmountKind.UPFRONT)
        
        assert parsed is not None
        assert parsed.currency == "EUR"
        assert parsed.value_in_millions == 120
    
    def test_gbp_symbol_parsed(self):
        """£80 million should parse as GBP."""
        parsed = sec_deals.parse_amount_from_quote("£80 million", sec_deals.AmountKind.UPFRONT)
        
        assert parsed is not None
        assert parsed.currency == "GBP"
        assert parsed.value_in_millions == 80
    
    def test_tranche_1a_not_matched_as_eur(self):
        """'Tranche 1-A' should NOT match as EUR via [EUR] character class."""
        parsed = sec_deals.parse_amount_from_quote(
            "Tranche 1-A", 
            sec_deals.AmountKind.EQUITY
        )
        
        # Should either be None or be SHARES, never EUR
        if parsed is not None:
            assert parsed.currency != "EUR", "Tranche 1-A should not be parsed as EUR"


# =============================================================================
# AMOUNT RENDERING TESTS  
# =============================================================================

class TestAmountRendering:
    """Tests for Chinese amount rendering."""
    
    def test_shares_rendered_correctly(self):
        """4,425,487 shares should be ~442.5万股, not 4.4亿股."""
        parsed = sec_deals.ParsedAmount(
            value=4425487,
            scale=1,
            currency="SHARES",
            up_to=False,
            kind=sec_deals.AmountKind.EQUITY,
            raw_quote="test"
        )
        
        result = sec_deals.render_amount_chinese(parsed)
        
        assert "442" in result, f"Should contain 442, got {result}"
        assert "万股" in result, f"Should contain 万股, got {result}"
        assert "亿股" not in result, f"Should NOT contain 亿股, got {result}"
    
    def test_35_million_renders_3500_wan(self):
        """$35.0 million should render as 3,500 万美元."""
        parsed = sec_deals.ParsedAmount(
            value=35.0,
            scale=1,
            currency="USD",
            up_to=False,
            kind=sec_deals.AmountKind.UPFRONT,
            raw_quote="test"
        )
        
        result = sec_deals.render_amount_chinese(parsed)
        assert "3,500 万美元" in result or "3500" in result
    
    def test_1_5_billion_renders_15_yi(self):
        """$1.5 billion should render as 15 亿美元."""
        parsed = sec_deals.ParsedAmount(
            value=1.5,
            scale=1000,  # billion
            currency="USD",
            up_to=False,
            kind=sec_deals.AmountKind.UPFRONT,
            raw_quote="test"
        )
        
        result = sec_deals.render_amount_chinese(parsed)
        assert "15 亿美元" in result or "15亿" in result
    
    def test_up_to_renders_zuigao(self):
        """up_to amounts should have 最高 prefix."""
        parsed = sec_deals.ParsedAmount(
            value=150.0,
            scale=1,
            currency="USD",
            up_to=True,
            kind=sec_deals.AmountKind.FACILITY_SIZE,
            raw_quote="test"
        )
        
        result = sec_deals.render_amount_chinese(parsed)
        assert "最高" in result
    
    def test_empty_amount_renders_empty_not_undisclosed(self):
        """When amounts are dropped, render empty string, never '未披露'."""
        result = sec_deals.render_amount_chinese(None)
        assert result == "", f"Expected empty string, got '{result}'"


# =============================================================================
# PARAGRAPH/QUOTE VERIFICATION TESTS
# =============================================================================

class TestParagraphVerification:
    """Tests for same-paragraph and quote verification."""
    
    def test_quotes_in_same_paragraph_detected(self):
        """Quotes in the same paragraph should be detected."""
        text = """First paragraph with $100 million and Genentech.

Second paragraph about something else."""
        
        result = sec_deals.quotes_in_same_paragraph(
            "$100 million",
            "Genentech",
            text
        )
        
        assert result is True
    
    def test_quotes_in_different_paragraphs_detected(self):
        """Quotes in different paragraphs should be detected."""
        text = """First paragraph with $100 million.

Second paragraph about Genentech."""
        
        result = sec_deals.quotes_in_same_paragraph(
            "$100 million",
            "Genentech",
            text
        )
        
        assert result is False
    
    def test_quote_verification_exact_match(self):
        """Exact quote should be found in filing."""
        filing = "The Company paid $100 million upfront."
        
        assert sec_deals.verify_quote_in_filing("$100 million upfront", filing) is True
        assert sec_deals.verify_quote_in_filing("$200 million upfront", filing) is False


# =============================================================================
# RUN TESTS
# =============================================================================

if __name__ == "__main__":
    pytest.main([__file__, "-v"])
