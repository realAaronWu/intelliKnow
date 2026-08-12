# Test plan — Increment 02 Test corpus

Covers the test infrastructure in `test-plan.md` §3 and the corpus prerequisites for §6. No spec capability maps here — this increment builds the means of testing, and its correctness is proven by plans 03–06 consuming it.

Sections match task numbers in `plans/2026-08-08-02-test-corpus.md`. **No test contacts the network** — the fetcher is exercised through a mock transport.

## §1 Fixture generator

| # | Test | Expected |
|---|---|---|
| 1.1 | All fixtures written | exactly the nine expected filenames; each non-empty |
| 1.2 | Byte reproducibility | two builds into different directories produce byte-identical files for every fixture |
| 1.3 | Duplicate matches source | `duplicate.pdf` bytes equal `salary_bands.pdf` bytes |
| 1.4 | Handbook contains the known leave figure | extracted text contains the module's `ANNUAL_LEAVE_DAYS` constant |
| 1.5 | Salary table has every band | extracted text contains each band label and its known mid value |
| 1.6 | Ragged grid is genuinely ragged | extracted rows have differing column counts |
| 1.7 | Scanned PDF yields no text | text extraction returns empty or whitespace only |
| 1.8 | Corrupt PDF fails to parse | a PDF reader raises on it |
| 1.9 | Budget workbook sheets | expected sheet names present, numeric cells readable |

**1.2 is load-bearing.** If reproducibility breaks, `duplicate.pdf` stops matching and every downstream hash assertion becomes flaky. Debug the cause — do not loosen the assertion.

## §2 Corpus manifest

| # | Test | Expected |
|---|---|---|
| 2.1 | Manifest loads | ≥32 entries |
| 2.2 | Licences permitted | every entry's licence is in the allowed set |
| 2.3 | Per-space count | each of hr/legal/finance/operations has ≥8 entries |
| 2.4 | Format variety | each space has ≥2 distinct formats |
| 2.5 | Unique ids | no duplicates |
| 2.6 | HTTPS only | every URL starts `https://` |
| 2.7 | Confusables present | at least one entry per space carries a note marking it a cross-space confusable |

## §3 Corpus fetcher

| # | Test | Expected |
|---|---|---|
| 3.1 | Writes to the right path | `dest/<space>/<id>.<format>` with the response body |
| 3.2 | User-Agent sent | request carries the declared identity header |
| 3.3 | Cached file skipped | an existing file is returned and **the transport is never called** |
| 3.4 | Checksum mismatch | raises mentioning checksum; no file written |
| 3.5 | Checksum match | file written |
| 3.6 | Non-200 | raises with the status code in the message |
| 3.7 | `fetch_all` continues past a failure | one failing entry appears in `failed`; other entries still fetched |
| 3.8 | Redirects followed | a 302 to a 200 resolves to the final body |

## §4 Corpus health report

| # | Test | Expected |
|---|---|---|
| 4.1 | All targets met | `evaluate_targets` returns empty |
| 4.2 | Space under 8 documents | problem string naming that space |
| 4.3 | Under 300 pages | problem string mentioning pages |
| 4.4 | Under 5 table documents | problem string mentioning tables |
| 4.5 | Under-target message explains why | the per-space message states that precision is unmeasurable below the threshold |

**Manual, after the real fetch:** run the fetcher and the report against live sources. Resolve every 404 by finding the current URL. **Do not delete failing entries to reach green** — that defeats the thresholds. Record checksums once stable.

## §5 Golden question set

| # | Test | Expected |
|---|---|---|
| 5.1 | Set is large enough | ≥30 unambiguous, ≥10 ambiguous, ≥10 negative |
| 5.2 | Unambiguous fully labelled | every one has a valid `expected_space` **and** an `expected_doc_id` |
| 5.3 | Ambiguous unlabelled | every one has `expected_space` unset |
| 5.4 | Negative has no document | every one has no `expected_doc_id` |
| 5.5 | Space coverage | unambiguous questions cover all four content spaces |
| 5.6 | Unique ids | no duplicates |
| 5.7 | Expected documents exist | every `expected_doc_id` matches a manifest entry id |

**5.2 is the methodological guard.** If unlabelled questions reach `questions.yaml`, the L3 accuracy figure measures the model agreeing with itself rather than accuracy. The test must fail on an unlabelled entry, not warn.

## Not automatable

| Scenario | Why | Compensating check |
|---|---|---|
| Live source availability | Network, and URLs move between fiscal years | Manual fetch run in §4 |
| Question quality and ambiguity judgement | Requires human reading of the corpus | Hand-labelling step in §5 |

## Exit criteria

§1–§5 green. Real fetch complete with all corpus-health targets met. `questions.yaml` hand-labelled and passing. No clean tester run — this increment has no spec behaviour to verify.
