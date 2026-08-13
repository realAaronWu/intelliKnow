# Task 0: Stabilization Plan

**Goal:** Remove concrete correctness and operability risks before concurrent chat traffic and the admin console are added.

## Work

1. Declare direct runtime dependencies for the API server, console, credential encryption, Teams adapter, and the adapter SDK's undeclared `aiohttp` import.
2. Add a shared bearer-token dependency using `ADMIN_PASSWORD`; production admin routers require it while public channel endpoints remain separate.
3. Guard every FAISS search, mutation, load, rebuild, and persist operation with one re-entrant process lock.
4. Add a runtime-config allow-list for intent spaces, confidence threshold, and relevance floor. Reject other live changes with a restart-required error and make ingestion obtain the latest accepted config per operation.
5. Treat an answer with zero verified citations as `no_match`.
6. Reserve room for one compact verified source before truncating a channel response.
7. On startup, mark stale `pending` and `parsing` documents `failed` with a retryable interruption message.

## Verification

- Focused tests demonstrate authentication, concurrent FAISS exclusion, config allow-listing/live reads, citation failure behavior, source-preserving truncation, and startup recovery.
- `uv run pytest` passes.
- `openspec validate add-intelliknow-kms --strict --no-interactive` passes.
