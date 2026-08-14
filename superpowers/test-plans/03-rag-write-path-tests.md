# Test plan — Increment 03 RAG write path

Covers `spec: document-ingestion` (16 req / 38 scen), plus the index-lifecycle requirements of `spec: intent-management` and the embedding-immutability requirement of `spec: configuration`.

L1 uses the plan-01 fakes. L2 (marked `slow`) uses real FAISS, FTS5, and local embeddings. Fixtures come from plan 02.

## §1–3 Loaders

| # | Test | Expected |
|---|---|---|
| 1.1 | Blocks are ordered | block order matches document order |
| 1.2 | Heading level set | `heading` blocks carry a level; paragraphs do not |
| 2.1 | PDF body text | `handbook.pdf` paragraphs extracted |
| 2.2 | PDF page refs | source refs read `p. N`, 1-indexed |
| 2.3 | PDF table structure | `salary_bands.pdf` yields a table block whose rows and columns are recoverable; each band label and its mid value present |
| 2.4 | Scanned PDF | `LoaderError` whose message states scanned documents are unsupported |
| 2.5 | Corrupt PDF | `LoaderError`, **distinguishable from the scanned case** |
| 3.1 | DOCX headings | `nda.docx` headings are `heading` blocks with correct levels, not paragraphs |
| 3.2 | DOCX paragraph refs | source refs read `¶ N` |
| 3.3 | DOCX tables | table block with rows and columns intact |
| 3.4 | XLSX per sheet | each sheet in `budget.xlsx` yields at least one table block |
| 3.5 | XLSX refs | source refs name sheet and cell range |
| 3.6 | XLSX formulas | computed values extracted, not formula text |

## §4 Table repair

| # | Test | Expected |
|---|---|---|
| 4.1 | Inconsistent column counts | `is_ragged` true |
| 4.2 | Majority-empty cells | `is_ragged` true |
| 4.3 | Clean table | `is_ragged` false |
| 4.4 | Ragged region repaired | LLM called once; returned structure used as block content |
| 4.5 | **Clean table costs nothing** | `is_ragged` false → fake LLM call count is exactly **0** |
| 4.6 | Provider failure | raw text used; no exception escapes |
| 4.7 | Invalid structure returned | raw text used |
| 4.8 | Real ragged fixture | `ragged_salary_grid.pdf` triggers repair; `salary_bands.pdf` does not |

4.5 is the cost regression guard — without it, a broadened raggedness heuristic silently sends every table to the model.

## §5 Chunker

| # | Test | Expected |
|---|---|---|
| 5.1 | Long run chunks with overlap | multiple chunks; adjacent ones share overlap text |
| 5.2 | **Table rows never split** | no chunk boundary falls inside a row of the salary grid |
| 5.3 | Small oversized table kept whole | a table between 1× and 1.5× target is one chunk |
| 5.4 | Heading path prefixed | chunk text begins with its heading path; `heading_path` stored |
| 5.5 | No overlap across headings | a chunk starting a new heading shares no text with the previous chunk |
| 5.6 | Deterministic | identical input yields identical boundaries across runs |
| 5.7 | Config respected | changing `chunk_chars` changes chunk count |
| 5.8 | Source refs carried | every chunk records the ref of its originating blocks |

## §6 Vector store

| # | Test | Expected |
|---|---|---|
| 6.1 | Add then search | added vector is returned with score ≈1.0 for itself |
| 6.2 | Disk round-trip | write, reload, identical results |
| 6.3 | Remove | removed id no longer returned |
| 6.4 | Move between spaces | vector leaves source index and appears in destination |
| 6.5 | Space isolation | searching space A never returns a chunk in space B |
| 6.6 | Empty space | returns empty, no error |
| 6.7 | Delete space | index file removed |
| 6.8 | Cross-space comparability | identical text in two spaces scores identically against the same query |

## §7 Index metadata

| # | Test | Expected |
|---|---|---|
| 7.1 | Recorded at first ingest | model name and dimension persisted |
| 7.2 | Mismatch with documents present | raises naming **both** models and mentioning re-index |
| 7.3 | No meta recorded | any model permitted |
| 7.4 | Empty index | model change permitted |
| 7.5 | Re-index updates record | recorded model becomes the configured one |

## §8 Index writer

| # | Test | Expected |
|---|---|---|
| 8.1 | **Three stores agree** | after write, chunk-row count == FTS5 row count == FAISS vector count |
| 8.2 | Removal clears all three | all counts drop to zero |
| 8.3 | Removal preserves history | `query_log` rows referencing the document survive |
| 8.4 | **Reassign does not re-embed** | fake embedder call count is **0** during reassignment |
| 8.5 | Reassign moves vectors | destination index gains, source loses; FTS5 space updated |
| 8.6 | Batching | embedding called ceil(n/batch_size) times |

## §9 Upload validation and intent suggestion

| # | Test | Expected |
|---|---|---|
| 9.1 | Allowed extensions | pdf/docx/xlsx accepted |
| 9.2 | Disallowed extension | rejected, message lists accepted formats |
| 9.3 | Oversize | rejected, message states the limit |
| 9.4 | Duplicate hash | rejected, message names the existing document |
| 9.5 | Suggestion prompt | recorded prompt contains every space's name, description, **and keywords** |
| 9.6 | Suggestion applied | returned slug becomes the document's space |
| 9.7 | Provider failure | falls back to the configured fallback space; ingestion continues |

## §10–11 Worker and lifecycle

| # | Test | Expected |
|---|---|---|
| 10.1 | Happy path | status `indexed`, chunk count and timestamp set |
| 10.2 | Loader failure | status `failed` with a readable message |
| 10.3 | **Failure leaves nothing behind** | zero chunks, zero FTS5 rows, zero vectors for that document |
| 10.4 | Embedding provider outage | status `failed`; other documents remain searchable |
| 10.5 | Document row survives failure | still listed, retryable |
| 11.1 | Re-parse replaces | old chunks gone, new present, id and space preserved |
| 11.2 | Re-parse failure | status `failed`, no orphaned vectors |
| 11.3 | Delete | chunks and both indexes cleared |
| 11.4 | Full re-index | every document re-embedded; index meta updated |

## §12 Document API

| # | Test | Expected |
|---|---|---|
| 12.1 | Upload returns immediately | 202 with an id and status `pending` |
| 12.2 | Search by name | only matching documents |
| 12.3 | Filter by format | only that format |
| 12.4 | Filter by intent space | only that space |
| 12.5 | Filter by date range | only that range |
| 12.6 | Combined filters | intersection |
| 12.7 | Detail | intent space, chunk count, status, error, chunks |
| 12.8 | Reassign endpoint | space changes; chunk count unchanged |
| 12.9 | Delete endpoint | removed from list and from retrieval |
| 12.10 | Validation error shape | actionable message, not a bare status code |

## Not automatable

| Scenario | Why | Compensating check |
|---|---|---|
| Real-world PDF extraction quality across the fetched corpus | Depends on live documents; no fixed expected output | Manual spot check during the L4 demo |

## Exit criteria

All sections green including `slow` L2 tests. All nine plan-02 fixtures ingest with the expected outcome. Clean tester report accepted against `spec: document-ingestion`.
