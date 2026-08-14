## Context

IntelliKnow is a seven-day, single-developer MVP for document-backed question answering in Telegram and WhatsApp. The repository also retains an optional Microsoft Teams adapter. It contains the configuration/provider foundation, document ingestion and indexing, RAG read path, three channel adapters, an authenticated admin API, and the five-view Streamlit console. Remaining acceptance work is a labelled quality evaluation and full demo evidence; real Teams tenant delivery is a documented limitation, not a claim.

The implementation uses FastAPI, Streamlit, SQLite/FTS5, FAISS, sentence-transformers, and provider adapters. It is a single-process application with background ingestion jobs. That constraint makes in-process concurrency, startup recovery, and clear runtime-configuration boundaries more important than distributed-system abstractions.

The original design mixed MVP requirements with optional infrastructure: Telegram webhook mode, broad hot reload, multiple small admin routers, and Microsoft Teams emulator success as the completion gate. It also left several production contracts implicit: admin API authentication, FAISS synchronization, recovery of interrupted ingestion, and the behavior of generated answers whose citations all fail verification.

## Goals / Non-Goals

**Goals**

- Deliver cited answers through real Telegram and WhatsApp conversations.
- Retain Teams support without treating Emulator coverage as real-tenant proof.
- Keep document ingestion, classification, retrieval, generation, and analytics understandable in one process.
- Provide the five required admin views through one authenticated API and one Streamlit application.
- Let admins tune intent spaces, classification confidence, and relevance floor without restarting.
- Preserve secure credential handling, observable failures, and a credible end-to-end demo.
- Finish the MVP with a focused test suite rather than duplicate abstractions for hypothetical scale.

**Non-Goals**

- Telegram webhook operation, horizontal scaling, a job queue, or distributed index locking.
- Hot-swapping providers, models, storage paths, embedding settings, or ingestion structure at runtime.
- Treating the Bot Framework Emulator as proof of Microsoft Teams delivery.
- Building a general-purpose configuration editor or a second administration backend.
- Automated model-quality claims without a labelled question set.

## Architecture

```text
Telegram polling ─┐
WhatsApp webhook ─┼─> ChannelHandler ─> QueryPipeline ─> channel send
Teams endpoint ───┘          │                 │
                             │                 ├─> classifier
                             │                 ├─> FAISS + FTS5 retrieval
                             │                 ├─> reranker and relevance gate
                             │                 └─> generation + citation verification
                             └─> status, errors, query log

Streamlit console ─> authenticated /admin API
                         ├─> documents and ingestion
                         ├─> integrations and channel tests
                         ├─> intents and safe runtime settings
                         └─> analytics and review feedback

config.yaml ─> ConfigService       .env ─> service/admin secrets + Fernet key
SQLite ─> metadata, FTS5, logs, encrypted chat credentials
FAISS files ─> per-intent vectors
```

The query pipeline is the sole owner of answer formatting. A channel adapter supplies a `ChannelProfile`; the pipeline returns final channel-safe text and verified citation metadata. The handler must not format or escape the answer a second time.

## Decisions

### 1. Stabilize the single process before adding channels

Task 0 adds declared runtime dependencies, admin API authentication, a process-wide re-entrant lock around FAISS operations, startup recovery for interrupted documents, safe runtime configuration updates, strict citation success rules, and source-preserving truncation.

This is deliberately small. It addresses concrete failure modes introduced by background ingestion and concurrent chat requests without introducing queues, workers, or a database-backed vector service.

### 2. Authenticate every administrative endpoint at the router boundary

Production mounts administrative routes with a shared bearer-token dependency backed by `ADMIN_PASSWORD`. Comparison is constant-time. Public Bot Framework messaging remains outside this boundary and relies on Bot Framework authentication.

Interactive login exchanges `ADMIN_PASSWORD` for an HMAC-signed session token
that expires after eight hours. A one-time, 60-second browser handoff ticket lets
the API place that token in a host-scoped `HttpOnly`, `SameSite=Strict` cookie;
HTTPS deployments also receive the `Secure` flag. Streamlit reads the cookie
from its server-side request context and sends the token as a bearer credential
on every administrative request, so the raw password is not retained by the
browser and a page refresh does not require another login. Sign-out expires the
cookie. Changing `ADMIN_PASSWORD` or passing the token's expiry invalidates the
session.

Direct password bearer authentication remains available for the laptop helper
and administrative scripts. Tests may construct an application without
production authentication when directly exercising isolated route behavior,
but production startup must require a non-empty admin password. This remains a
single-admin MVP session mechanism, not a substitute for production identity,
per-user authorization, revocation, or audit controls.

### 3. Serialize all FAISS access with one `RLock`

FAISS indexes and their on-disk files are process-local shared mutable state. Search, add, remove, move, rebuild, load, and persistence all use the same re-entrant lock. SQLite remains responsible for its own concurrency.

This supports the MVP's one-process deployment. Multi-process serving is explicitly unsupported because an in-memory lock cannot coordinate separate workers.

### 4. Limit no-restart updates to genuinely live settings

The admin API may update only:

- intent-space names, descriptions, and classification keywords;
- `orchestrator.confidence_threshold`;
- `rag.relevance_floor`.

The next classification or query reads those values from the current `ConfigService` snapshot. Provider/model selection, embedding settings, storage paths, chunking, retrieval topology, and channel process settings require restart. Embedding changes additionally require an empty knowledge base or a full re-index.

The configuration service validates a complete candidate document and writes it atomically only after validation. Unsupported runtime fields receive a clear restart-required error.

### 5. A successful answer must have a verified source

Generation is not enough to declare success. If citation verification removes every citation, the pipeline returns `no_match` and does not present the generated text as a grounded answer.

When channel limits require truncation, formatting reserves room for at least one compact verified source. The answer body is shortened first. A cited answer may not become an uncited answer merely because it is long.

### 6. Recover interrupted ingestion on startup

Documents left in `pending` or `parsing` after process termination are marked `failed` at startup with a readable retry message. The admin can then re-parse them. The MVP does not implement durable job resumption.

### 7. Keep channels thin and use polling for Telegram

`InboundMessage` contains channel, user reference, text, and reply reference. `ChannelHandler` owns the shared sequence: validate input, send typing indication, invoke the pipeline once, deliver once, update status, and append analytics after delivery.

Telegram uses long polling only. WhatsApp uses one signed Cloud API webhook with GET challenge verification and POST HMAC validation. Teams uses one FastAPI Bot Framework endpoint. Emulator tests validate the Teams adapter locally, but the project does not claim real Teams tenant acceptance.

Channel latency is measured from accepted inbound message through completion of the outbound send. Pipeline-only latency is retained as diagnostic data but does not satisfy the three-second requirement.

### 8. Persist a usable destination for admin channel tests

Each successful inbound exchange records the most recent reply reference for that channel. The admin-triggered channel test sends to that destination. If none exists, the API reports that a real user must message the bot first instead of reporting a misleading success.

Telegram, WhatsApp, and Teams credential bundles are Fernet-encrypted before being written
to SQLite. `CREDENTIAL_ENCRYPTION_KEY` remains in the private environment,
separate from the database. APIs return masked last-four-character details,
never a usable credential, and credential changes are read on the next channel
operation without a restart. Missing, invalid, or mismatched keys fail closed.

This intentionally small design protects a copied database and avoids
platform-specific or cloud infrastructure in the seven-day MVP. It does not
claim protection after full host compromise or theft of both SQLite and `.env`.
See `docs/PRODUCTION-INTEGRATION-CREDENTIALS.md`.

### 9. Use one admin router and one Streamlit application

The remaining admin endpoints live under one authenticated `/admin` router with service modules for documents, integrations, intents/configuration, and analytics. The Streamlit application uses a sidebar to select five view functions:

1. Dashboard
2. Frontend Integration
3. Knowledge Base Management
4. Intent Space Configuration
5. Analytics

The console is an HTTP client only. It does not open SQLite, mutate FAISS, or edit `config.yaml` directly. Shared API client, authentication state, errors, and simple presentation helpers are centralized; page-specific code stays in each view function.

### 10. Report accuracy only from reviewed outcomes

Confidence is not correctness. The query log stores optional admin feedback: expected intent and whether the classification was correct. Classification accuracy is `reviewed_correct / reviewed_total` for the selected period and space. When no reviewed samples exist, the UI shows `Not enough reviewed data`.

The existing high-confidence share may be shown as a separate confidence metric, but it must not be labelled accuracy. Model-quality acceptance requires a labelled question set; until that set exists, calibration results are diagnostic only.

Reviewed labels also form a bounded classifier feedback set. An exact normalized
repeat uses the admin's expected intent directly. Up to 8 recent examples per
intent (30 total, 240 characters each) contribute individual embeddings to the intent centroid and to low-confidence LLM
escalation. Deleted-intent labels are ignored. This is an immediate MVP feedback
loop, not model fine-tuning; its effect must still be evaluated on a separate
labelled holdout set before accuracy is claimed.

### 11. Prefer focused tests and real acceptance gates

Unit tests cover pure formatting, configuration allow-listing, encrypted credentials, normalization, handlers, auth, analytics, classifier feedback, and failure isolation. Integration tests use real SQLite/FTS5/FAISS with fake providers. Captured platform payloads test adapters without contacting live services.

Final delivery additionally requires:

- OpenSpec strict validation;
- the complete automated test suite;
- real Telegram send and receive;
- real WhatsApp send and receive;
- two or more indexed documents with cited answers;
- measured end-to-end latency;
- all five admin views exercised;
- README, setup guides, and AI-usage notes.

### 12. Fail closed when classification cannot be trusted

General remains a real intent space, but it is no longer an operational fallback. A provider outage, malformed model response, unknown slug, or confidence below the configured threshold produces an explicit `unclassified` failure. Query retrieval and generation do not run, and document ingestion writes no chunks or vectors.

Uploads perform one cheap structured-output preflight before the document row and file are created. Intent-space mutations first validate a complete candidate config, build its centroids in isolation, and preflight the classification provider; only then is the config written and the vector-index lifecycle changed. These probes improve failure visibility but do not claim perfect model accuracy. Semantic accuracy still requires the labelled evaluation set and human review described above.

## Delivery Plan

### Task 0: Stabilization

1. Declare all imported runtime packages.
2. Protect production admin routes with `ADMIN_PASSWORD` bearer authentication.
3. Serialize FAISS operations with one `RLock`.
4. Restrict no-restart config writes to intent spaces, confidence threshold, and relevance floor; make ingestion read the current config snapshot.
5. Convert answers with zero verified citations to `no_match`.
6. Preserve at least one verified source during channel truncation.
7. Mark interrupted `pending`/`parsing` documents failed on startup.

### Task 05: Channels

1. Implement Fernet-encrypted credentials plus channel status/error/reply-reference persistence.
2. Implement normalized messages, the shared handler, and query logging.
3. Implement Telegram polling and captured-payload tests.
4. Implement the Teams Bot Framework endpoint and captured-activity tests.
5. Implement the signed WhatsApp Cloud API webhook and captured-payload tests.
6. Add authenticated integration APIs and a destination-aware end-to-end test action.
7. Verify real Telegram and WhatsApp round trips and record full delivery latency.

### Task 06: Admin and delivery

1. Implement one authenticated admin router for intents, safe configuration, analytics, feedback, and dashboard summaries.
2. Implement one Streamlit application with five sidebar-selected views.
3. Exercise upload, re-parse, reassign, delete, intent tuning, channel configuration/test, analytics, feedback, and CSV workflows.
4. Build the labelled question set, run final quality/latency checks, and complete delivery documentation.

## Risks / Trade-offs

- **One-process lock:** simple and sufficient for the MVP, but running multiple API workers can corrupt or desynchronize FAISS state. Deployment documentation must specify one worker.
- **Synchronous model latency:** the three-second target depends on provider and model choices. Measure full delivery latency and report misses honestly rather than hiding them behind typing indicators.
- **Latency controls:** typing is best-effort and bounded to 400 milliseconds; query
  classification returns only slug and confidence with a 48-token ceiling, and
  concise answers use a 128-token ceiling. The console reports per-channel p50,
  p95, maximum, pass rate, and the three-second gate.
- **Config restarts:** fewer live controls reduce surprise and keep service wiring truthful. The console must label restart-required fields as read-only.
- **Real Teams verification:** it requires an Azure Bot registration, reachable HTTPS endpoint, and tenant setup. This project documents those prerequisites but does not claim a real-tenant round trip.
- **Accuracy sample size:** reviewed accuracy is honest but may initially have no data. The UI must distinguish unavailable accuracy from confidence distribution.
- **Feedback overfitting:** reviewed examples improve immediate routing but can encode label mistakes and repeated wording. Keep them bounded, allow labels to be corrected, and judge quality on a separate labelled set.
- **Interrupted re-parse:** startup recovery marks work failed rather than resuming it. This favors transparent retry behavior over a durable queue outside MVP scope.

## Migration Plan

There is no deployed predecessor. Existing local databases gain additive columns/tables through idempotent schema initialization. On the first startup after Task 0, interrupted documents are marked failed. Existing configuration files remain valid; unsupported fields remain readable but may no longer be changed through the live admin update endpoint. One short-lived prior build stored Keychain references; its explicit migration script converts those values back to Fernet ciphertext atomically.

Rollback is code rollback plus restoring the automatically retained `config.yaml` backup. Database changes are additive and do not require destructive rollback.

## Open Questions

- Which Azure Bot registration and Microsoft 365 tenant will be used for final Teams acceptance?
- Which real provider/model pair is the latency baseline for the three-second target?
- Who will label the initial routing question set and what minimum reviewed sample count should the UI require before displaying accuracy?
