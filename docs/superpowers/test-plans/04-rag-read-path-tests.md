# Test plan — Increment 04 RAG read path and orchestrator

Covers `spec: query-orchestration` (9 req / 17 scen) and `spec: knowledge-retrieval` (11 req / 29 scen).

L1 uses the plan-01 fakes with pinned vectors so similarity scores are exact. L2 (`slow`) uses real FAISS, FTS5, and local embeddings with a fake LLM.

## §1 Dense retrieval

| # | Test | Expected |
|---|---|---|
| 1.1 | Single-space search | only that space's chunks returned |
| 1.2 | **Isolation** | a chunk in an unsupplied space **cannot** appear, at any score |
| 1.3 | Multi-space merge | results from all supplied spaces, ranked by score |
| 1.4 | Empty space | contributes nothing, no error |
| 1.5 | `top_n` respected | at most `top_n` per space |
| 1.6 | Pinned vectors give exact scores | with `set_vector`, returned score matches the hand-computed cosine |

## §2 Keyword retrieval

| # | Test | Expected |
|---|---|---|
| 2.1 | Exact rare token | a chunk containing a band label is returned for a query naming it |
| 2.2 | Space filter | only chunks in the supplied spaces |
| 2.3 | `top_n = 0` | returns empty; no error |
| 2.4 | FTS5 operator characters in the question | escaped; no syntax error |
| 2.5 | BM25 ordering | more term-relevant chunk ranks higher |

## §3 Fusion

| # | Test | Expected |
|---|---|---|
| 3.1 | Formula | fused score equals the hand-computed sum of `1/(k+rank)` |
| 3.2 | Present in both lists | outranks an equally-ranked single-list chunk |
| 3.3 | **No normalization** | changing the absolute magnitude of dense scores, keeping order, does not change fused ordering |
| 3.4 | `dense_score` carried through | preserved unchanged on the fused hit |
| 3.5 | Keyword-only hit | `dense_score` is absent, not zero |
| 3.6 | Deterministic | identical input yields identical output including tie order |
| 3.7 | `top_k` respected | exactly `top_k` returned when available |

## §3a Reranker

| # | Test | Expected |
|---|---|---|
| 3a.1 | Whole pool scored in one batch | model invoked once per query, not once per candidate |
| 3a.2 | Reordering is real | a lower-fused candidate with a higher cross-encoder score moves up |
| 3a.3 | `top_k` respected | exactly `final_top_k` returned when available |
| 3a.4 | Fewer candidates than pool size | all reranked, no error |
| 3a.5 | `relevance` normalized | every value in 0–1; raw score retained |
| 3a.6 | Model loads once | constructing the pipeline twice does not reload the model per query |

## §4 Relevance gate

| # | Test | Expected |
|---|---|---|
| 4.1 | Best normalized reranker score above floor | passes |
| 4.2 | Best below floor | fails |
| 4.3 | Exactly at floor | passes |
| 4.4 | **Gate ignores the fused score** | construct high fused rank with sub-floor reranker score → **fails**. A gate reading the fused score would pass this |
| 4.5 | Empty hits | fails |

4.4 is the single most important test in this increment. It is what prevents a confident misroute from producing a fluent, wrong, fully-cited answer.

## §5 Context builder

| # | Test | Expected |
|---|---|---|
| 5.1 | Near-duplicates dropped | heavily overlapping same-document chunks collapse to one |
| 5.2 | **Ordering** | chunks ordered by document then position, **not** by score |
| 5.3 | Tagging | each source carries marker, title, source ref, heading path |
| 5.4 | Budget enforced | over-budget input drops lowest-ranked until within `max_context_chars` |
| 5.5 | Markers unique and stable | same input yields same markers |
| 5.6 | Content delimited | chunk text appears inside delimiters in the prompt block |

## §6 Answer generation

| # | Test | Expected |
|---|---|---|
| 6.1 | Grounding instruction | recorded prompt instructs answering only from context |
| 6.2 | Citation instruction | prompt asks for the markers |
| 6.3 | Channel profile in prompt | recorded prompt contains the channel's length limit and markup capability |
| 6.4 | Context in prompt | every source marker appears |
| 6.5 | Provider failure | raises for the caller to convert |

## §7 Citation verification

| # | Test | Expected |
|---|---|---|
| 7.1 | Valid marker resolves | citation carries the right document and source ref |
| 7.2 | **Unresolvable marker stripped** | a marker not in the supplied context is removed from the answer text and yields no citation |
| 7.3 | Remaining citations kept | valid ones survive alongside a stripped one |
| 7.4 | Multiple documents | each appears once, in first-cited order |
| 7.5 | No markers | empty citation list, no error |

## §8 Channel formatting

| # | Test | Expected |
|---|---|---|
| 8.1 | Reserved characters escaped | Telegram MarkdownV2 specials escaped |
| 8.2 | **Always within the limit** | a 5000-char answer for a 4096-limit channel is truncated below the limit |
| 8.3 | Word-boundary truncation | truncation does not split a word; a visible marker is appended |
| 8.4 | Under-limit answer untouched | no truncation marker added |
| 8.5 | Teams lists | enumerated content renders as bullets |
| 8.6 | Citations rendered | present in channel-appropriate form |

## §8a Centroid index

| # | Test | Expected |
|---|---|---|
| 8a.1 | Centroid per space | one per configured space |
| 8a.2 | **Works on an empty knowledge base** | centroids exist and classify with zero documents indexed |
| 8a.3 | Keyword edit rebuilds | after a config update, the centroid changes and the next classification differs — no restart, no re-index |
| 8a.4 | Probabilities sum to 1 | softmax output is a distribution |
| 8a.5 | Lower temperature sharpens | top probability rises as temperature falls, same inputs |
| 8a.6 | New space becomes a target | created space gets a centroid and can be classified into |

## §9 Classification

| # | Test | Expected |
|---|---|---|
| 9.1 | **High centroid confidence makes no LLM call** | fake LLM call count is **0**; `classified_by` is `centroid` |
| 9.2 | Low centroid confidence escalates | LLM called once; `classified_by` is `llm`; reasoning populated |
| 9.3 | **Escalation prompt built from live config** | recorded prompt contains every space's name, description, and keywords |
| 9.4 | Keyword edit takes effect | after `ConfigService.update`, the next escalation prompt contains the new keyword — no restart |
| 9.5 | Escalation disabled | low confidence routes to fallback with **0** LLM calls |
| 9.6 | Escalated result also below threshold | routed to fallback |
| 9.7 | Unknown slug from LLM | treated as below-threshold; anomaly recorded |
| 9.8 | Provider failure during escalation | `failed=True`, fallback slug, **no exception raised** |
| 9.9 | Timeout | same as 9.8 |
| 9.10 | Uses classify model | escalation provider constructed with `model_classify` |

## §10 Routing

| # | Test | Expected |
|---|---|---|
| 10.1 | Confidence 0.91, threshold 0.70 | single space; `fallback_used` false |
| 10.2 | Confidence 0.42 | all spaces; `fallback_used` true; logged slug is the fallback |
| 10.3 | **Confidence exactly 0.70** | single space; `fallback_used` **false** |
| 10.4 | Classified General, high confidence | all spaces; `fallback_used` true |
| 10.5 | Unknown slug | all spaces; fallback true |
| 10.6 | Classification failed | all spaces; fallback true |
| 10.7 | Threshold raised at runtime | next decision uses the new value |

10.3 is the boundary case most likely to be implemented as strict greater-than. The spec says meets-or-exceeds.

## §11 Pipeline

| # | Test | Expected |
|---|---|---|
| 11.1 | **One embedding call** | the fake embedder is invoked exactly once per query, serving both classification and retrieval |
| 11.2 | Gate rejection | `status="no_match"`; fake LLM generation call count is **0** |
| 11.3 | No-match message | names the searched domain |
| 11.4 | Generation failure | `status="failed"`; user-facing message returned |
| 11.5 | Success | `status="success"`, verified citations, retrieved document ids populated |
| 11.6 | Latency recorded | non-zero and plausible |

## §12 Admin test-query

| # | Test | Expected |
|---|---|---|
| 12.1 | Returns full result | intent, confidence, answer, sources, latency |
| 12.2 | **No channel involved** | no adapter send is invoked |
| 12.3 | Empty knowledge base | no-match, not an error |

## L2 integration (`slow`)

| # | Test | Expected |
|---|---|---|
| L2.1 | Semantic retrieval | "how much time off do I get" retrieves the annual-leave clause despite sharing no keywords |
| L2.2 | **Hybrid justification** | an exact-token question retrieves its chunk with keyword retrieval on, and **fails to** with `keyword_top_n: 0` |
| L2.3 | Real isolation | a Finance-routed query returns no HR chunk via either retriever |
| L2.4 | Fallback breadth | an all-spaces query returns hits from more than one space |

**L2.2 is designed to be able to falsify its own feature.** If it passes in both configurations on the real corpus, BM25 is contributing nothing and the hybrid design should be reconsidered — report that rather than deleting the test.

## §11a Calibration — manual, gates L3

| Check | Pass condition |
|---|---|
| Temperature sweep | accuracy, escalation rate, and fast-path share recorded across the range; a value chosen with evidence |
| Floor sweep | no-match rate on negative questions and false-no-match rate on unambiguous questions recorded; a value chosen with evidence |
| Values written | chosen values in `config.yaml`, sweep report committed |

**No L3 gate is meaningful until this passes.** Both numbers ship as guesses on scales introduced by this increment.

## Not automatable

| Scenario | Why | Compensating check |
|---|---|---|
| Answer quality and factual grounding | Requires a real LLM and human judgement | L3 gates in `test-plan.md` §6 |
| Classification accuracy against human labels | Requires the golden set and a real LLM | L3 §6.1 |

## Exit criteria

All sections green including `slow`. L2.2 demonstrates the hybrid contribution, or its absence is reported. Clean tester report accepted against both capability specs.
