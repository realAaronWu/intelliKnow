# IntelliKnow KMS - implementation plans

Source of truth for *what* to build: `openspec/changes/add-intelliknow-kms/` (proposal, design, 9 capability specs, traceability, test-plan). These plans are *how* to build it.

## Document set

| Kind | Location | Audience |
|---|---|---|
| Execution plans | `plans/` | Implementer subagents |
| Test plans | `test-plans/` | Implementer (writes the failing test) **and** the clean tester |
| Tester protocol | `TESTER-PROTOCOL.md` | The clean tester agent |

**No implementation or test code appears in these plans, by project decision.** Plans state behaviour and contracts; test plans state expectations. The implementer writes both test and code. This keeps the plan reviewable and stops the implementer from transcribing pre-written code instead of reasoning about the requirement.

## Plan status

Plans 00-06 are implemented on `main`. WhatsApp support and later demo
hardening were added through follow-up OpenSpec updates and focused commits.
OpenSpec is the behavioral source of truth; these files preserve the planned
implementation order and boundaries.

| # | Plan | Produces | Depends on |
|---|---|---|---|
| 01 | [Foundation](2026-08-08-01-foundation.md) | Config service, provider layer, test doubles, DB schema | Complete |
| 02 | [Test corpus](2026-08-08-02-test-corpus.md) | Synthetic fixtures and test inputs | Complete |
| 03 | [RAG write path](2026-08-08-03-rag-write-path.md) | Loaders, chunker, vector store, ingestion | Complete |
| 04 | [RAG read path](2026-08-08-04-rag-read-path.md) | Retrieval, generation, citations, classification | Complete |
| 00 | [Stabilization](2026-08-11-00-stabilization.md) | Auth, concurrency, config boundaries, recovery, grounding fixes | Complete |
| 05 | [Channels](2026-08-08-05-channels.md) | Encrypted credentials, Telegram polling, Teams, delivery logging | Complete |
| 06 | [Admin and delivery](2026-08-08-06-admin-console.md) | One admin router, five views, feedback, acceptance evidence | Complete |

## Execution order

The executed order was `00 -> 05 -> 06 -> final delivery`. A larger independent
labelled set remains necessary before claiming production classifier accuracy;
the included corpus supports deterministic acceptance and bounded calibration.

## The loop, per increment

```
for each task in plan:
    implementer subagent  →  failing test → watch fail → minimal code
                          →  watch pass → commit
    task review (spec compliance + code quality)
        ↓
[ all tasks done, implementer's tests green ]
        ↓
whole-branch code review
        ↓
CLEAN TESTER (fresh agent, has not seen the plan or the source)
    reads spec + test plan → writes its own tests → runs → reports
        ↓
implementer fixes implementation-defect verdicts → re-run
        ↓
green + report accepted → increment done
```

The clean tester is a gate, not a substitute for TDD. The implementer still writes their own failing test first for every task; the tester is an independent second reading of the spec. When they disagree, one of them misread it — and that disagreement is the signal the protocol exists to produce. See `TESTER-PROTOCOL.md`.

## Non-negotiables across all plans

- **`superpowers:test-driven-development` — the Iron Law.** No production code without a failing test first. Wrote code before the test? Delete it and start over.
- **`superpowers:using-git-worktrees`** — isolated workspace before execution begins.
- **`superpowers:verification-before-completion`** — run the command, read the output, then claim. Never assert a result you have not seen.
- **`superpowers:systematic-debugging`** on any failure, before proposing a fix.
- **`superpowers:requesting-code-review`** after each task and across the whole branch at increment end.

## Test layers

Defined in `openspec/changes/add-intelliknow-kms/test-plan.md`:

- **L1** unit, fakes only, deterministic, every commit, < 30s
- **L2** integration, real SQLite/FAISS/FTS5/embeddings with a fake LLM, < 3 min
- **L3** model-quality, real LLM, statistical gates, on demand
- **L4** end-to-end demo script, manual, before delivery

Plans 01–06 build L1 and L2 as their TDD output. L3 and L4 run on day 7 against the corpus plan 02 assembles.
