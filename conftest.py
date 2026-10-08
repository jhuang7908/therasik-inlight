"""Test-only: production keeps LLM_DENY_GATE_REQUIRED = True."""

import os

import sec_deals

os.environ[sec_deals.LLM_DENY_GATE_TEST_SKIP_ENV] = "1"
sec_deals.LLM_DENY_GATE_REQUIRED = False
