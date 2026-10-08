"""Test-only: production keeps LLM_DENY_GATE_REQUIRED = True."""

import sec_deals

sec_deals.LLM_DENY_GATE_REQUIRED = False
