# Self-check of the fixed acceptance suite against PR #5 head 893bf3d (fresh clone; run 2026-10-08, ET)

72 tests ran (69 parametrized cases plus 3 standalone): **71 failed and 1 passed** (the passing test is `test_guard_main_really_runs`).
Every publishing case also fails the universal "no 未给出" rule, because head writes "原文未给出…" into datacard/journal.
For 22 tests that rule is the ONLY failure.

## Failures and how they map to the pr5f findings

| Cases | Failure at head | pr5f finding |
|---|---|---|
| crash_results_list_of_dicts, crash_limitations_ints, crash_data_point_value_int, crash_background_list, crash_title_none, crash_one_liner_dict, crash_significance_none, crash_empty_input (8) | main() exits 4: uncaught error, no output written | "8 malformed shapes crash the run" |
| crash_real_pr5e_string_fields, crash_title_missing, crash_image_prompt_list, crash_datacard_*, crash_data_points_*, crash_results_none/plain_string, crash_steps_plain_string, crash_text_only_reply, crash_truncated_max_tokens (12) | the run completes, but CD6 is never published, even when the valid real retry is served | malformed reply leads to a silent drop, and the CD6 false positive blocks the retry |
| fp_cd6_real_published_pr5e, fp_cd6_runA_ge3_once_dcr, fp_cd6_runB_ge3, fp_ge3_explicit, fp_unit_spacing_100mg, fp_honest_unit_conversion, journal_from_pubmed_rss, dp_nine_doses_runB | the real CD6 article is not published | CD6 false positives (≥3级 / 疾病控制率 / unit forms) |
| fp_p38_exact | MK2 not published: "数字 '38' 在原始材料中未找到" | p38 false positive |
| fp_95ci, fp_once_a_week_cd14 | CD14 not published | false positives on the "95%CI" and "once a week → 一次" forms |
| inv_mk2_decimal_8, inv_mk25_25 | the mutant is PUBLISHED: an invented "降低8倍" / "降低25%" passes | decimal/identifier hole |
| inv_il6_literal … inv_cd6_six (10 CD6 cases) | they fail only on 未给出. CD6 is dropped, but only because the base draft is already rejected (false positive). The confound-free diagnostic below shows head's validator does NOT catch the injected IL-6, PD-1, CD318, CD318-31, CD8, RPCEC-444 or CD6-six errors | identifier holes (IL-6, PD-1, CD318 "31例" …) |
| mean_dcr_vs_death, mean_25_died, mean_os_vs_pfs, mean_count_vs_percent_25/20 | dropped, but with no meaning-class reason in the logs | meaning mismatches not detected |
| name_wang_invented_author | dropped with no name-class reason in the logs | invented author "王等" not detected |
| q_brief_under_450_r2 (422), q_brief_under_450_csu (433), q_brief_under_450_redraft (438) | brief published under 450 Han | length gate missing |
| q_deep_over_cap_drop | deep piece published at 2044 Han, with a literal \n in articles.json and WeChat | deep cap and \n |
| dp_nine_doses_runA | the draft with "nine doses" data_points is published, with the wrong journal | non-numeric data_points accepted |
| q_brief_under_450_csu, dp_nine_doses_runA, replays | PubMed journal written as "原文未给出期刊名（来源：PubMed）" or "PubMed…" | journal not taken from the RSS item |
| replay_pr5f_runA / runB / pr5e_crash_then_run2 | 2044-Han deep, 447/433-Han briefs, results with no source number, wrong journal, 未给出 | same findings, on the real recorded weeks |
| test_legacy_path_byte_identical_to_main | legacy digest prompt is 42,822 chars vs main's 42,569; first difference is at a PubMed row (a new "journal" key leaks into the legacy prompt) | legacy path not identical to main |
| test_legacy_pages_keep_main_headings | 44 of 44 legacy pages lost main's headings | legacy pages re-rendered |
| 22 others (fp_roman_numeral_mhc_ii, name_real_institution_merck, name_mesaconate_*, name_mei*_redraft, dp_millions, q_deep_over_cap_redraft, q_results_without_number, dedup_abstract_once, retry_keeps_better_draft, inv_* CD6, mean_cr28_death28, mean_28_died) | ONLY the universal 未给出 rule | 未给出 written instead of omitted |

## Confound-free validator diagnostic (head's own validate_depth + validate_names, base vs mutant)
The full output is in `selfcheck/diag_validator_head_v5.txt`.
* Not caught by head: IL-6, PD-1, CD318 count, "CD318阳性患者共31例", CD8 percent, RPCEC00000444, CD6 "six", MK2 8倍, MK2 25%, DCR-as-death, 25% died, OS-vs-PFS, 25例/20例, and 王等.
* Caught by head, through "数字…未找到": 3.2倍, CD19 ("19"), NCT04512345, and the 28% cases.

## Crash injection
`raise RuntimeError("acceptance crash injection")` was added as the first statement of `main()` in a COPY (selfcheck/head_inject_v1).
The result was **72 of 72 tests failed**.
* 71 failed with that RuntimeError: all 69 cases, the legacy test and the guard.
* The headings test does not call main(), so it fails only because head itself lost the headings. On a correct head it would be the single test that survives the injection.

## Known-good outputs
* **On main 884561d:** test_legacy_path_byte_identical_to_main and test_legacy_pages_keep_main_headings PASS (2 of 2).
* **Oracle:** an idealised production was modelled: omit 未给出, drop literal \n, reject malformed drafts, non-numeric data_points, out-of-bounds length, wrong names, results with no source number, and drafts with forbidden strings. It satisfies **69 of 69** case expectations using only the recorded drafts. Every pin can therefore be met.
* **Real published samples:**
  * The REAL published CD6 deep articles (pr5e, and pr5f run A) pass every per-article guard once 未给出 is omitted (1379 and 1615 Han).
  * Raw, they fail only on 未给出, literal \n and the journal placeholder.
  * The real published R2 brief (374 Han after omission) and mesaconate brief (438 Han) are below 450. That is exactly the length gate the suite enforces.
