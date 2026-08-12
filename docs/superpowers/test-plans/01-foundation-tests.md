# Test plan — Increment 01 Foundation

Covers `spec: configuration` (7 req / 16 scen) and `spec: ai-provider` (6 req / 15 scen).

Sections match the task numbers in `plans/2026-08-08-01-foundation.md`. Each row is one test. No test in this increment touches the network.

## §1 Configuration schema

| # | Test | Expected |
|---|---|---|
| 1.1 | Load shipped `config.yaml` | `llm.model_classify` and `llm.model_generate` both `claude-opus-5`; `embedding.model` `all-MiniLM-L6-v2`; `embedding.dimension` 384; `orchestrator.confidence_threshold` 0.70; `rag.relevance_floor` 0.45 |
| 1.2 | Default intent spaces | slugs include `hr`, `legal`, `finance`, `operations`, `general` |
| 1.3 | Each space is complete | non-empty `description`; `keywords` is a list |
| 1.4 | Threshold above 1.0 | validation error |
| 1.5 | Threshold below 0.0 | validation error |
| 1.6 | Relevance floor above 1.0 | validation error |
| 1.7 | Unknown top-level field | validation error naming the field |
| 1.8 | Unknown `llm.provider` value | validation error |
| 1.9 | `chunk_overlap_chars` ≥ `chunk_chars` | validation error |
| 1.10 | Slug not kebab-case | validation error |
| 1.11 | Duplicate slugs | validation error |
| 1.12 | `fallback_space` names no existing space | validation error mentioning the slug |
| 1.13 | Missing config file | defaults written, load succeeds |
| 1.14 | `.env.example` contents | lists provider keys, `CREDENTIAL_ENCRYPTION_KEY`, `ADMIN_PASSWORD`; contains no real values |

## §2 ConfigService

| # | Test | Expected |
|---|---|---|
| 2.1 | Update threshold to 0.85 | `current` reflects it; a fresh load from disk also reflects it |
| 2.2 | Update with threshold 9.9 | raises; `current` still 0.70; file bytes unchanged |
| 2.3 | Update writes backup | `.yaml.bak` exists containing the previous value |
| 2.4 | Update leaves no temp file | no `*.tmp` in the config directory afterwards |
| 2.5 | Partial patch merges | `rag.final_top_k` changes; `rag.relevance_floor` unchanged |
| 2.6 | Update intent space keywords | new keyword present on that space after update |
| 2.7 | Reload picks up an external edit | `current` reflects a change made to the file by another writer |
| 2.8 | Failed update leaves no backup churn | a rejected update does not overwrite an existing `.bak` |

## §3 Provider protocols

| # | Test | Expected |
|---|---|---|
| 3.1 | Each error constructor | `.category` is `timeout` / `rate_limit` / `auth` / `backend` respectively; message preserved |
| 3.2 | `ProviderError` is raisable | catchable as `Exception` |
| 3.3 | `LLMResult` immutability | attribute assignment raises |
| 3.4 | `normalize([[3,4]])` | unit length; first component 0.6 |
| 3.5 | `normalize` on a zero vector | returned unchanged, no division error |
| 3.6 | `normalize` preserves order and count | N in, N out, index-aligned |

## §4 Test doubles

| # | Test | Expected |
|---|---|---|
| 4.1 | Queued texts return in order | first call "one", second "two" |
| 4.2 | Queued schema response | `parsed` equals the queued object |
| 4.3 | Calls recorded | `calls[0]` has the exact `system`, `user`, `max_tokens` passed |
| 4.4 | `fail_next` then recover | first call raises with the queued category; next call returns the queued text |
| 4.5 | Empty queue | raises an assertion mentioning no queued response — must not silently return a default |
| 4.6 | Embedding determinism | same text twice yields identical vectors |
| 4.7 | Embedding is unit-norm | length 1.0 within 1e-6 |
| 4.8 | Order and count preserved | 3 texts → 3 distinct vectors, index-aligned |
| 4.9 | `set_vector` pins | pinned text returns exactly the pinned vector |

## §5 Retry policy

| # | Test | Expected |
|---|---|---|
| 5.1 | Success first try | returns value; function called once |
| 5.2 | Rate limit twice then success | returns value; function called three times |
| 5.3 | Auth error | raises immediately; function called **once** |
| 5.4 | Backend error | raises immediately; function called once |
| 5.5 | Retries exhausted | raises the last error with category intact |
| 5.6 | Backoff schedule | recorded sleeps are exactly `[0.5, 1.0, 2.0]` for `max_retries=3` |

## §6 Providers and factory

| # | Test | Expected |
|---|---|---|
| 6.1 | Free-form completion | `text`, `model`, and both token counts come from the response |
| 6.2 | Schema request | `parsed` is the decoded object; the request carries the schema in its structured-output field |
| 6.3 | Unparseable schema response | `ProviderError` with category `backend` |
| 6.4 | Auth exception from SDK | mapped to category `auth` |
| 6.5 | Rate-limit exception | mapped to `rate_limit` |
| 6.6 | Timeout exception | mapped to `timeout` |
| 6.7 | Other API exception | mapped to `backend` |
| 6.8 | Effort setting | request asks for low effort; **`thinking` is not set to disabled** |
| 6.9 | `role="classify"` vs `"generate"` | providers carry `model_classify` and `model_generate` respectively |
| 6.10 | Missing API key | `RuntimeError` naming the environment variable |
| 6.11 | Unknown provider name | `RuntimeError` listing supported values |
| 6.12 | Local embedding with empty env | constructs without a key |
| 6.13 | Embedding batching | a 200-text call issues ceil(200/batch_size) model calls |

## §7 Database schema

| # | Test | Expected |
|---|---|---|
| 7.1 | Tables created | `documents`, `chunks`, `query_log`, `integrations`, `chunk_fts` all present |
| 7.2 | WAL enabled | `PRAGMA journal_mode` returns `wal` |
| 7.3 | FTS5 match | inserted chunk text is found by a term query on its rowid |
| 7.4 | BM25 available | `bm25(chunk_fts)` returns a float |
| 7.5 | Cascade delete | deleting a document leaves zero chunks |
| 7.6 | History survives | deleting a document leaves its `query_log` rows intact |
| 7.7 | Duplicate sha256 | unique constraint rejects the second insert |

## §8 Live smoke check — manual, not automated

Run against the real backend with a key set. Confirm: a schema request returns a parsed object; the embedding call returns vectors of the configured dimension; the reported model id matches config. **A stub cannot verify the SDK accepts our call shape — this is the only check that can.**

## Not automatable in this increment

| Scenario | Why | Compensating check |
|---|---|---|
| Real provider rate-limit behaviour under load | Cannot be induced cheaply or reliably | §5 with an injected `rate_limit` error |
| SDK acceptance of the structured-output request shape | Requires a live call | §8 smoke check |

## Exit criteria

All §1–§7 tests green with no skips. §8 smoke check passes. Clean tester report accepted with no open implementation-defect verdicts.
