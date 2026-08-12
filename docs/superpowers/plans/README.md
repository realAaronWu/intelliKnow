# IntelliKnow KMS — implementation plans

Source of truth for *what* to build: `openspec/changes/add-intelliknow-kms/` (proposal, design, 9 capability specs, traceability, test-plan). These plans are *how* to build it.

## Why six plans, not one

The spec covers 92 requirements across 9 capabilities. A single plan carrying full TDD code for all of it would run to thousands of lines, could not be reviewed meaningfully, and would force a reviewer to accept or reject everything at once. Each plan below produces working, independently testable software and ends at a state you could stop at.

| # | Plan | Produces | Depends on |
|---|---|---|---|
| 01 | [Foundation](2026-08-08-01-foundation.md) | Config service, provider layer, test doubles, DB schema | — |
| 02 | [Test corpus](2026-08-08-02-test-corpus.md) | Synthetic fixtures + real-world corpus fetcher + golden question set | 01 (partial) |
| 03 | RAG write path | Loaders, chunker, embedder, dual-index writer, ingestion worker | 01, 02 |
| 04 | RAG read path + orchestrator | Hybrid retrieval, fusion, gate, context, generation, citations, classification, routing | 01, 02, 03 |
| 05 | Channels | Credential storage, Telegram, Teams, formatting, status, logging | 01, 04 |
| 06 | Admin console | Five screens, styling, all admin API surfaces | 01–05 |

Plans 03–06 are written after 01 and 02 land, so their interfaces are written against code that exists rather than code that is imagined. This is deliberate: the most common failure mode in a long plan is Task 12 referencing a signature Task 3 didn't actually produce.

## Execution order and parallelism

01 → 02 can overlap (02 only needs the config schema from 01). 03 → 04 → 05 are sequential. 06 needs the API surfaces from 03–05.

Against the 7-day budget in `openspec/changes/add-intelliknow-kms/tasks.md`:

| Day | Plan |
|---|---|
| 1 | 01 Foundation |
| 2 | 02 Test corpus, then 03 begins |
| 3 | 03 RAG write path |
| 4 | 04 RAG read path + orchestrator |
| 5 | 05 Channels |
| 6 | 06 Admin console |
| 7 | L3 model-quality run, L4 demo script, README, AI usage reflection |

## How to execute these

Each plan carries the standard header directing the executor to `superpowers:subagent-driven-development` (preferred) or `superpowers:executing-plans`.

Non-negotiable across all plans:

- **`superpowers:test-driven-development` — the Iron Law.** No production code without a failing test first. If you wrote code before the test, delete it and start over. Every task below is structured red → verify red → green → verify green → commit.
- **`superpowers:using-git-worktrees`** for an isolated workspace before execution starts.
- **`superpowers:verification-before-completion`** before any task is marked done. Run the command, read the output, then claim.
- **`superpowers:systematic-debugging`** on any failure — before proposing a fix.
- **`superpowers:requesting-code-review`** after each task and across the whole branch at the end.

## Test layers these plans build toward

Defined in `openspec/changes/add-intelliknow-kms/test-plan.md`:

- **L1** unit, fakes only, deterministic, every commit, < 30s
- **L2** integration, real SQLite/FAISS/FTS5/embeddings with a fake LLM, < 3 min
- **L3** model-quality, real LLM, statistical gates, on demand
- **L4** end-to-end demo script, manual, before delivery

Plans 01–06 build L1 and L2 as they go — that is what TDD produces. L3 and L4 are exercised on day 7 against the corpus that plan 02 assembles.
