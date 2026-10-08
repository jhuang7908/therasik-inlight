# PR #7 acceptance suite: read this before touching anything

## What to drop into the repo (new files only)

    tests/test_acceptance_main.py
    tests/acceptance_fixtures/cases.json            # 89 cases, each with its audited expected outcome
    tests/acceptance_fixtures/edgar_mirror.json     # offline copies of the 4 real EDGAR filings (REGN, ALEC, IMNM, RCKT)
    tests/acceptance_fixtures/recorded_claude/*.json  # real Claude extract_deal responses captured on Hetzner (no keys or headers)

## How to run

From the repo root, with no network access needed:

    pytest tests/test_acceptance_main.py

Requirements: the repo's `requirements.txt` (`anthropic`, `requests`, `pyyaml`, `feedparser`) plus `pytest`.
pytest is not in requirements.txt, so install it with `pip install pytest`.

## What it does

* Every case calls the real `run_weekly.main()` with `sys.argv = ["run_weekly.py", "--dry-run"]`. It runs in a temporary
  ROOT that contains a copy of `sources.yaml` and an empty `content/latest.json`. The production modules are imported
  fresh for every test.
* Only I/O is mocked:
  * `requests.get`: EDGAR full-text search and filing documents, served from the fixtures.
  * `anthropic.Anthropic`: responses are real `anthropic.types.Message` objects with `tool_use` blocks.
  * `draw_image`, `fetch_rss`, `fetch_pubmed` and `time.sleep`.
* Any socket connect or DNS lookup fails the test.
* Outcomes are read from what `main()` writes:
  * `preview/weekly/<date>/deals.json`
  * `preview/weekly/<date>/articles.json`
  * `preview/weekly/<date>/wechat/article.html`
* Each deal is mapped to its source filing by accession number. For each filing, the result must be "no deal" or the
  exact title and structure line, as `cases.json` specifies. There is no pass-if-empty logic: "no deal" passes only
  where the audit allows it.
* An exception inside `main()` fails the test, because `main()` turns it into exit code 4. A raise before main's own
  `try` propagates and also fails the test. `test_guard_main_really_runs` checks that the real `run_weekly.main` ran and
  called `write_output` exactly once. It also checks that the fixtures are not truncated.
* A failure message lists each wrong filing or article, the outcomes that were allowed, and the relevant log lines,
  such as the "Deal dropped" reasons.

## Case groups

* **original76 (76):** the 76 audited adversarial cases. N1 and P4 are among them.
* **probe (3):** N2, N3 and P1e.
* **honest (6):** honest text that must not be stripped:
  * H1: 两组 and 一项.
  * H2: "$3 billion" rendered as 30 亿美元.
  * H3: a contract that says "$10.00 per share".
  * H4: a contract that says "potentially eligible to receive up to $1.17 billion".
  * H5: a contract that says "according to the terms".
  * H6: the article's only lead is invented. It must not be published with an empty lead, and the honest second
    article must be published.
* **real_replay (4):** Claude responses recorded on Hetzner for PR #7 at heads 7511f48 and ac66a9c, two runs each,
  replayed verbatim against the 4 real filings:
  * Alector must render `首付：1 亿美元 | 里程碑：最高 11.7 亿美元`.
  * Rocket must render the Hercules loan, `贷款额度：最高 1.5 亿美元 | 已提取：3,500 万美元`, with Rocket as the
    borrower.
  * Regeneron and Immunome must be exactly correct or no deal.

## The expectations are NON-NEGOTIABLE

The expected outcomes are audited ground truth. If a test fails, fix the production code (`run_weekly.py` /
`sec_deals.py`). Do NOT do any of the following:

* edit, delete, skip, xfail or parametrize away any case, or change anything in `acceptance_fixtures/`;
* loosen a comparison, add "accept empty" branches, or catch exceptions around `main()`;
* change the mocks so that production code paths are bypassed (for example, by patching `sec_deals` / `run_weekly`
  internals, `extract_deals_from_filings`, `claude_draft` or `write_output`);
* special-case test names, URLs, accession numbers or company names in production code.

The PR is acceptable only when `pytest tests/test_acceptance_main.py` reports all 90 tests passed (89 cases + 1 guard)
on an unmodified copy of these files.
