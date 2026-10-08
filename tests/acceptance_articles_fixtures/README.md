# PR #5 FIXED acceptance suite: article-depth pipeline (`--use-new-pipeline`)

## These expectations are NON-NEGOTIABLE

Everything under `tests/test_acceptance_articles.py` and `tests/acceptance_articles_fixtures/` is audited ground truth.
**Fix production code only.** The PR must pass this suite **unchanged**, so do not edit, regenerate, re-record, loosen,
skip, xfail, parametrize away or "update" any test, fixture, expectation, recorded reply or baseline. Adding new fixtures
of your own does not count as passing either. A PR whose diff touches any file of this suite is rejected as is.
If you believe an expectation is wrong, say so in the PR description and leave the file alone.

Every source, DOI, PMID, abstract and Claude reply in the fixtures comes from the REAL captured server runs
(pr5e and pr5f, plus main's legacy digest). None was invented. Negative cases are explicit, documented **mutations** of those
real replies (see `cases.json`, field `mut` on every queue entry).

## How to run

```
pip install pytest anthropic openai feedparser PyYAML requests playwright && python -m playwright install chromium
pytest tests/test_acceptance_articles.py -p no:cacheprovider
```

Playwright/Chromium is **required**: if it is missing, `test_legacy_pages_keep_main_headings` FAILS. It never skips.
There are 69 parametrized cases plus 3 standalone tests, so **72 tests**. All must pass.

## What each case does

* It calls the real `run_weekly.main()` with `sys.argv = ["run_weekly.py", "--dry-run", "--use-new-pipeline"]`.
  Every repo module with a `Path` attribute `ROOT` gets a fresh temp ROOT, and the working directory is that ROOT.
  The ROOT holds `sources.yaml` from the fixtures, which is production's file with `window_days` widened so the recorded dates stay in the window.
* Only the network edge is mocked:
  * **HTTP:** `urllib.request.OpenerDirector.open` (urlopen and feedparser) and `requests.Session.request` answer from
    payloads rebuilt from the captured runs. That covers RSS feeds, PubMed esearch/esummary/efetch, Europe PMC search, Nature abstract pages and the bioRxiv
    details API. An unknown URL gets a 404 and is recorded. The rebuild was verified to reproduce production's
    `fetch_all` + `enrich_item` output byte-for-byte for both weeks: 73/74 rows and 39 enriched items each.
  * **Anthropic:** `anthropic.Anthropic`/`Client` return real `anthropic.types.Message` objects with `tool_use` blocks.
  * **OpenAI images:** these return a real `ImagesResponse` holding a 1x1 PNG. `time.sleep` is a no-op.
  * **Network:** any real socket connect or DNS lookup is recorded and **fails** the test.
* The outcome is read only from what `main()` writes: `preview/weekly/<date>/articles.json`, `deals.json`,
  `wechat/article.html` and `logs/*.log`.

### Interface contract the harness relies on (all of it is what head 893bf3d already does)

* Claude calls are routed by tool name: `test_tool` (model check), `submit_triage`, `submit_article` and `submit_weekly_digest`.
  Any other tool counts as unexpected traffic and fails the case.
* The `submit_article` prompt must contain the item's URL, its canonical URL or its exact RSS title. The Nth
  `submit_article` call for an item is served the Nth draft of that item's queue, and the last draft repeats after that.
* A drop is checked by its reason class, using the log lines that mention the item's URL, canonical URL or title[:50].
  The log line must contain one of these keywords:
  * `invented_number`: '未找到', 'not found', '编造', '无法回溯', 'invent', 'fabricat', '不在原文', 'not in source', 'not in the source'
  * `meaning`: '含义', 'meaning', '语义', 'mismatch'
  * `meaning_or_invented`: '含义', 'meaning', '语义', 'mismatch', '未找到', 'not found', '编造', '无法回溯', 'invent'
  * `name`: '人名', '作者', 'name', '未出现', 'not in source', '编造', '无法回溯', 'invent'
  * `length`: '字数', '汉字', '过短', '过长', '上限', '下限', 'length', 'too short', 'too long', 'chars', 'characters'

## Universal invariants (checked on EVERY published article in EVERY case)

* `tier` is `deep` or `brief`.
* **Length:** counted as Han characters `[\u4e00-\u9fff]` over the fields one_liner, background, design, results, mechanism, significance, limitations. Titles, datacard,
  journal and authors are not counted.
  * A brief needs **at least 450**. A deep piece may have **at most 1900**.
  * A draft outside these bounds must be redrafted. If the redraft still fails, it is dropped.
* **No `未给出`:** no visible text (every key except ['data_points', 'date', 'disp', 'ds', 'evidence_level', 'f', 'field', 'id', 'image_prompt', 'img', 'source', 'source_trace', 'tags', 'tier', 'url']) and no WeChat HTML may contain `未给出`.
  A missing value is omitted. It is never written out as "原文未给出".
* **No literal `\n`:** no string in articles.json and nothing in the WeChat HTML may contain the two characters backslash-n.
* **Mesaconate:** it is 中康酸. 美康酸, 梅萨康酸 and 梅沙康酸 must never appear. "Nissle 1917" stays verbatim.
* **Source numbers in results:** if the source abstract contains a standalone number, `results` must contain at least one standalone number from that abstract.
* **PubMed journal:** for a PubMed item, the article's `j` must contain the journal name from the RSS/esummary item (`fulljournalname`).
  A model-written placeholder is not acceptable.
* Each case also requires: exit code 0, articles.json and the WeChat HTML written, the `test_tool` model check ran, a log file under ROOT/logs,
  no real network and no unexpected Claude traffic.

## Standalone tests

* **`test_legacy_path_byte_identical_to_main`:** runs main() **without** the flag on the full recorded pr5f week, with
  main's recorded legacy digest reply. It compares four things against `legacy_baseline_main.json`, which was recorded with this exact harness on
  main 884561d (deterministic: two recordings were byte-identical):
  * the exit code;
  * the Claude tools called;
  * the digest request (prompt, model, max_tokens, tool_choice and tool schemas, with enum lists sorted);
  * every output file. The week folder is normalised to `<WEEK>` and PNGs are compared by sha256.
* **`test_legacy_pages_keep_main_headings`:** opens the repo's `index.html` over file:// in Chromium, with all non-file requests
  aborted. For each of the 44 legacy pages it reads the `.amain` section headings and checks that they equal main's exact tuple
  (stored in `legacy_headings_main.json`). 43 pages have 这篇在解决什么 / 他们怎么做、看到了什么 / 讨论 / 编辑备注 / 出处;
  c8-vac-5 has the same tuple without the second heading.
* **`test_guard_main_really_runs`:** proves that the real `run_weekly.main()` from the repo ran. It checks that main wrote its own log,
  ran the model check, fetched RSS and PubMed through the network edge, drafted an article and wrote the preview.

## Documented substitutions (no real source exists for the literal example)

* **"I/II期":** no recorded source has a phase I/II trial. The Roman-numeral guard uses the real "MHC II类"
  (CD4 runB i10). Roman numerals and class/phase labels must not be treated as invented numbers.
* **"12 nM vs 12nM":** no recorded source has a nM value. The spacing guard uses the real "100 mg" vs "100mg".
* **"64例 vs 64%":** the count-vs-percent mismatch uses the real CD6 numbers instead: "25例" written for 25% and "20例" written for 20%.
* **IL-6, CD19 and NCT04512345:** these literals appear in no recorded source. They are injected as **invented** content to test that
  identifier-shaped tokens are traced. They are not presented as real data.

## Case list (69 parametrized cases; groups: crash=20, invented_number=12, meaning=7, names=5, false_positive=10, data_points=3, quality=6, journal=1, dedup=1, retry=1, replay=3)

| case | group | expected | why |
|---|---|---|---|
| `crash_real_pr5e_string_fields` | crash | publish/deep; publish/brief | REAL pr5e malformed reply: datacard/results/limitations/data_points are strings with XML-like tags. The run must complete (exit 0), CD6 must still publish from the valid real retry, the companion must publish. |
| `crash_results_list_of_dicts` | crash | publish/deep; publish/brief | results is a list of dicts. The run must complete (exit 0), CD6 must still publish from the valid real retry, the companion must publish. |
| `crash_limitations_ints` | crash | publish/deep; publish/brief | limitations is a list of ints. The run must complete (exit 0), CD6 must still publish from the valid real retry, the companion must publish. |
| `crash_data_point_value_int` | crash | publish/deep; publish/brief | a data_point value is an int. The run must complete (exit 0), CD6 must still publish from the valid real retry, the companion must publish. |
| `crash_background_list` | crash | publish/deep; publish/brief | background is a list. The run must complete (exit 0), CD6 must still publish from the valid real retry, the companion must publish. |
| `crash_title_none` | crash | publish/deep; publish/brief | title is null. The run must complete (exit 0), CD6 must still publish from the valid real retry, the companion must publish. |
| `crash_title_missing` | crash | publish/deep; publish/brief | title key missing. The run must complete (exit 0), CD6 must still publish from the valid real retry, the companion must publish. |
| `crash_one_liner_dict` | crash | publish/deep; publish/brief | one_liner is a dict. The run must complete (exit 0), CD6 must still publish from the valid real retry, the companion must publish. |
| `crash_significance_none` | crash | publish/deep; publish/brief | significance is null. The run must complete (exit 0), CD6 must still publish from the valid real retry, the companion must publish. |
| `crash_image_prompt_list` | crash | publish/deep; publish/brief | image_prompt is a list. The run must complete (exit 0), CD6 must still publish from the valid real retry, the companion must publish. |
| `crash_datacard_non_json_string` | crash | publish/deep; publish/brief | datacard is a non-JSON string. The run must complete (exit 0), CD6 must still publish from the valid real retry, the companion must publish. |
| `crash_datacard_list` | crash | publish/deep; publish/brief | datacard is a list. The run must complete (exit 0), CD6 must still publish from the valid real retry, the companion must publish. |
| `crash_data_points_dict` | crash | publish/deep; publish/brief | data_points is a dict. The run must complete (exit 0), CD6 must still publish from the valid real retry, the companion must publish. |
| `crash_data_points_strings` | crash | publish/deep; publish/brief | data_points is a list of strings. The run must complete (exit 0), CD6 must still publish from the valid real retry, the companion must publish. |
| `crash_results_none` | crash | publish/deep; publish/brief | results is null. The run must complete (exit 0), CD6 must still publish from the valid real retry, the companion must publish. |
| `crash_results_plain_string` | crash | publish/deep; publish/brief | results is a plain (non-JSON) string. The run must complete (exit 0), CD6 must still publish from the valid real retry, the companion must publish. |
| `crash_steps_plain_string` | crash | publish/deep; publish/brief | steps is a plain string. The run must complete (exit 0), CD6 must still publish from the valid real retry, the companion must publish. |
| `crash_empty_input` | crash | publish/deep; publish/brief | tool_use input is {}. The run must complete (exit 0), CD6 must still publish from the valid real retry, the companion must publish. |
| `crash_text_only_reply` | crash | publish/deep; publish/brief | reply has no tool_use block (text only, end_turn). The run must complete (exit 0), CD6 must still publish from the valid real retry, the companion must publish. |
| `crash_truncated_max_tokens` | crash | publish/deep; publish/brief | stop_reason=max_tokens with a partial input. The run must complete (exit 0), CD6 must still publish from the valid real retry, the companion must publish. |
| `inv_il6_literal` | invented_number | drop(invented_number); publish/brief | IL-6 never appears in the CD6 source; "6例死亡" is invented (6 only exists as "six doses"/CD6) |
| `inv_pd1_hyphen` | invented_number | drop(invented_number); publish/brief | PD-1 is real, but "1例" is not a source number |
| `inv_decimal_3_2` | invented_number | drop(invented_number); publish/brief | "3.2" appears nowhere in the source |
| `inv_cd19_literal` | invented_number | drop(invented_number); publish/brief | CD19 / 19% are not in the source |
| `inv_nct_literal` | invented_number | drop(invented_number); publish/brief | NCT04512345 is invented (the real registration is RPCEC00000444) |
| `inv_cd318_count` | invented_number | drop(invented_number); publish/brief | CD318 is real; "318例" is an invented count |
| `inv_cd318_31` | invented_number | drop(invented_number); publish/brief | the exact pr5f hole: "CD318阳性患者共31例" |
| `inv_cd8_percent` | invented_number | drop(invented_number); publish/brief | CD8 is real; "升高8%" is invented |
| `inv_rpcec_444` | invented_number | drop(invented_number); publish/brief | RPCEC00000444 is real; "444例" is invented |
| `inv_cd6_six` | invented_number | drop(invented_number); publish/brief | CD6/"six doses" are real; "共6例" is an invented count |
| `inv_mk2_decimal_8` | invented_number | drop(invented_number); publish/brief | "2.8 A" is real; "降低8倍" is invented (8 only inside 2.8) |
| `inv_mk25_25` | invented_number | drop(invented_number); publish/brief | "MK-25" is a compound name; "降低25%" is invented |
| `mean_dcr_vs_death` | meaning | drop(meaning); publish/brief | 25% is the A-arm disease-control rate, not a death rate |
| `mean_cr28_death28` | meaning | drop(meaning_or_invented); publish/brief | the user-specified pair 完全缓解率28%/死亡率28% (28 is not in the source at all) |
| `mean_28_died` | meaning | drop(meaning_or_invented); publish/brief | the user-specified "28%的患者死亡" |
| `mean_25_died` | meaning | drop(meaning); publish/brief | 25% = disease control in group A, not deaths |
| `mean_os_vs_pfs` | meaning | drop(meaning); publish/brief | 26% is 1-year (overall) survival, not progression-free survival |
| `mean_count_vs_percent_25` | meaning | drop(meaning); publish/brief | 25% (a rate) rewritten as 25例 (a count) — the 64例 vs 64% pattern on a real number |
| `mean_count_vs_percent_20` | meaning | drop(meaning); publish/brief | 20% rewritten as 20例 |
| `name_wang_invented_author` | names | drop(name); publish/brief | "王等报告": no author named Wang in the source |
| `name_real_institution_merck` | names | publish/brief; publish/brief | Merck is named in the source ("developed at Merck"): must NOT be flagged |
| `name_mesaconate_zhongkangsuan` | names | publish/brief; publish/brief | real run-B mesaconate brief: 中康酸 and "Nissle 1917" verbatim must publish (1917 is a strain name, not an invented number) |
| `name_meikangsuan_redraft` | names | publish/brief; publish/brief | real pr5e draft says 美康酸 (wrong); the redraft (real runB i14) says 中康酸 |
| `name_meisakangsuan_redraft` | names | publish/brief; publish/brief | real run-A draft says 梅萨康酸 (wrong); the redraft (real runB i14) says 中康酸 |
| `fp_cd6_real_published_pr5e` | false_positive | publish/deep; publish/brief | the REAL CD6 deep draft that pr5e published (contains ≥3级, 每周一次, literal \n and 28x 未给出) |
| `fp_cd6_runA_ge3_once_dcr` | false_positive | publish/deep; publish/brief | real run-A CD6 draft: ≥3级, 每周一次 and 疾病控制率 data points must not be flagged |
| `fp_cd6_runB_ge3` | false_positive | publish/deep; publish/brief | real run-B CD6 draft that 893bf3d dropped over "≥3级…原文未给出" |
| `fp_ge3_explicit` | false_positive | publish/deep; publish/brief | "≥3级不良事件" in a kept sentence (the schema itself asks for ≥3级AE) |
| `fp_95ci` | false_positive | publish/brief; publish/brief | "95%CI" is a statistic name, not an invented 95% |
| `fp_p38_exact` | false_positive | publish/brief; publish/brief | "p38" copied exactly from the source ("substrate of p38 MAPK"); also "2.8 Å" for "2.8 A" |
| `fp_unit_spacing_100mg` | false_positive | publish/deep; publish/brief | "100mg" vs source "100 mg" (the 12 nM vs 12nM pattern; no nM in any real source) |
| `fp_once_a_week_cd14` | false_positive | publish/brief; publish/brief | real CD14 first draft: "once a week" written as 每周一次 |
| `fp_honest_unit_conversion` | false_positive | publish/deep; publish/brief | "every 21 days" -> 每3周 and "1-year" -> 12个月: honest conversions |
| `fp_roman_numeral_mhc_ii` | false_positive | publish/brief; publish/brief | "MHC II类" (Roman numeral from "MHC class II"); stands in for I/II期, which no real source contains |
| `dp_nine_doses_runB` | data_points | publish/deep; publish/brief | real runB i3 has data_points "nine"/"five"/"six" -> rejected; the real runB i2 must be the published draft |
| `dp_nine_doses_runA` | data_points | publish/deep; publish/brief | real runA i3 has data_points "100 mg weekly for nine doses" -> rejected; real runA i2 must be published |
| `dp_millions` | data_points | publish/deep; publish/brief | real runB i7 has data_points "millions"/"tens of kilobases" -> rejected; real runB i8 must be published |
| `q_brief_under_450_r2` | quality | drop(length); publish/brief | real R2 brief with 422 Han -> never published |
| `q_brief_under_450_csu` | quality | drop(length); publish/brief | real CSU brief with 433 Han -> never published |
| `q_brief_under_450_redraft` | quality | publish/brief; publish/brief | real mesaconate brief with 438 Han, then the real 461-Han redraft -> the redraft is published |
| `q_deep_over_cap_redraft` | quality | publish/deep; publish/brief | real SGE deep with 2044 Han (> 1900 cap) then the real 1860-Han redraft -> the redraft is published as deep |
| `q_deep_over_cap_drop` | quality | drop(length); publish/brief | only the 2044-Han SGE deep draft is ever returned -> never published |
| `q_results_without_number` | quality | publish/brief; publish/brief | results stripped of the source numbers (4/8 weeks) must fail; the real draft is published |
| `journal_from_pubmed_rss` | journal | publish/deep; publish/brief | PubMed journal comes from the RSS/esummary item, not "原文未给出期刊名" |
| `dedup_abstract_once` | dedup | publish/brief; publish/brief; publish/brief | bioRxiv RSS summary == EPMC abstract: the article prompt must carry it once |
| `retry_keeps_better_draft` | retry | publish/brief; publish/brief | first draft is the real CD14 brief; the retry adds an invented number -> the first draft must be published |
| `replay_pr5f_runA` | replay | publish 42843925 | whole real run A (893bf3d) replayed through main() |
| `replay_pr5f_runB` | replay | publish 42843925 | whole real run B (893bf3d) replayed through main() |
| `replay_pr5e_crash_then_run2` | replay | publish 42843925 | real pr5e week: CD6 first gets the REAL malformed reply, then the real run-2 replies |

## Fixture provenance

* **`weeks/pr5f.json` and `weeks/pr5e.json`:** one entry per fetched row. Each entry holds the RSS row, esummary, Europe PMC hit, efetch
  abstract parts, Nature abstract, bioRxiv abstract and the enriched item as production captured it.
* **`recorded_claude/{pr5e_crash,pr5e_run2,pr5f_runA,pr5f_runB}.json`:** the real Anthropic replies of those server runs
  (the tool_use input, stop_reason, usage and prompt sha256). Thinking blocks are omitted.
* **`recorded_claude/legacy_digest.json`:** main's real legacy digest reply, with 6 articles and 4 deals.
* **`cases.json`:** triage tiers and every case. Each queue entry is `{rec, i, mut?}` = recorded run, call index and
  explicit mutations. The mutation ops are set, delete, list_append, replace, append, replace_everywhere, set_input, text_only and stop_reason.
* **`legacy_baseline_main.json` and `legacy_headings_main.json`:** recorded on main 884561d.
