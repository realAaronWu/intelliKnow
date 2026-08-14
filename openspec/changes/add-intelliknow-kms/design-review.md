# IntelliKnow KMS Design Review

> Historical review of the original plan. Resolved decisions and current
> implementation status live in `design.md`, `traceability.md`, and
> `docs/REQUIREMENTS-AUDIT.md`; this file is retained as review history.

Reviewed against `AD, Tech Lead, AKP.docx` and the seven-day, solo-developer MVP constraint.

## Overall assessment

The design covers most requirements thoughtfully, especially grounding, secure credential storage, traceability, and the admin-console surface. However, it should not be implemented unchanged. It is over-scoped for a seven-day solo project, contains contradictory descriptions of the query path, and misses several acceptance-level requirements.

## Required corrections

### P1: Demonstrate two real frontend integrations

The design currently treats Bot Framework Emulator usage as the second frontend integration and makes a real Microsoft Teams tenant optional (`design.md` lines 18-20 and 586-602). The emulator validates the Bot Framework adapter but is not Microsoft Teams. This does not clearly satisfy the requirement that users configure and use the KMS through at least two common frontend tools.

Required refinement:

- Make real Teams message delivery an end-to-end acceptance criterion, or select another frontend that can be demonstrated live.
- Define how the admin-triggered channel test chooses its delivery destination.
- For Telegram, store or configure a test chat ID.
- For Teams, store the conversation/service reference required for proactive delivery, or define a user-initiated test flow.
- Do not count a protocol emulator alone as a completed frontend integration.

### P1: Report genuine classification accuracy

The design defines classification accuracy as the share of predictions whose confidence meets the configured threshold (`design.md` lines 443-445 and 570). That measures high-confidence routing coverage, not correctness. A classifier can be confidently wrong and still report 100 percent.

Required refinement:

- Rename the current metric to `high-confidence routing rate` or similar.
- Add a source of ground truth, such as an admin correction on query-log rows or a labelled evaluation question set.
- Compute accuracy as correct labelled classifications divided by all labelled classifications.
- Show `Not enough reviewed queries` when no ground-truth labels exist.
- Do not describe centroid softmax output as a calibrated correctness probability unless calibration evidence supports that claim.

### P1: Reconcile the query architecture

Several sections describe different implementations:

- The component table says `RelevanceGate` uses the best dense score (`design.md` line 178).
- The detailed RAG path says the gate uses a sigmoid cross-encoder score (`design.md` lines 267-269).
- The request flow omits the reranker and returns to dense cosine gating (`design.md` lines 476-494).
- The risks still discuss two sequential LLM calls (`design.md` lines 578-580), despite centroid routing removing classification from the common LLM path.
- Decision 13 says classification is prompt-based (`design.md` line 572), contradicting the centroid-first design.

Required refinement:

- Select one classification, retrieval, reranking, and relevance-gating flow.
- Update the architecture diagram, component duties, request flow, latency budget, decisions, risks, specs, tasks, traceability, and test plan to describe that same flow.
- Remove stale tests and assumptions from earlier architecture revisions.

### P1: Persist integration error history

The `integration` table stores only `last_error`. The requirements call for integration errors to be recorded with channel, timestamp, and reason, with recent errors visible to the admin. A single current error is status state, not an error log.

Required refinement:

- Add a lightweight table such as `integration_error(id, channel, created_at, stage, reason)`.
- Record authentication, inbound polling/webhook, platform API, and outbound delivery failures.
- Expose the most recent errors per channel in the admin API and Frontend Integration screen.
- Keep retention simple, for example the latest 100 records per channel.

### P2: Define real document update semantics

`PATCH /documents/{id}` currently reassigns an intent space, while `/reparse` processes the already stored source file. There is no operation for replacing changed document content while preserving the document record. This leaves the brief's `manual updates + re-parsing` requirement ambiguous.

Required refinement:

- Add a replacement upload operation that preserves the document ID and intent association, then reparses and reindexes the new source.
- Alternatively, explicitly document a delete-and-reupload workflow and change the UI action label so it is not presented as `Update`.
- Keep intent reassignment as a separate action from document-content replacement.

### P2: Enforce cited responses

`CitationVerifier` only removes citation markers that do not resolve to retrieved chunks. The system could still deliver a successful answer with no valid citations, which does not satisfy the requirement for cited KB responses.

Required refinement:

- Require at least one verified citation for every successful knowledge answer.
- If generation returns no valid citation, retry once with a stricter prompt or return a no-match/grounding-failure response.
- Log the grounding failure distinctly for diagnosis.

### P2: Reduce the seven-day implementation scope

The current plan combines hybrid retrieval, RRF, cross-encoder reranking, centroid classification with LLM escalation, multiple calibrated thresholds, three AI providers, three document formats, per-space FAISS lifecycle management, Telegram polling and webhook modes, hot configuration reload, full reindexing, and five admin screens. The associated `tasks.md` contains 81 implementation tasks.

This is excessive schedule risk for a solo seven-day MVP, even if each individual decision is defensible.

## Recommended MVP architecture

Keep these elements:

- FastAPI backend and Streamlit admin console.
- SQLite for metadata, query history, analytics, credentials, and integration errors.
- FAISS with one local embedding model.
- PDF and DOCX ingestion with source-aware chunks and table handling.
- Background ingestion status so the UI can show progress and errors.
- Intent CRUD, configurable confidence threshold, General fallback, and visible routing results.
- Telegram plus a genuinely usable second frontend.
- Fernet-encrypted chat credentials and masked API responses.
- Grounded generation, verified citations, no-match behavior, channel formatting, and query logging.
- The five required admin screens and required analytics/export.

Cut or defer until the core demo passes:

- XLSX as a third format.
- Cross-encoder reranking.
- Supporting Anthropic, OpenAI, and local generation backends simultaneously. Keep a small provider protocol for testing, but implement one production backend first.
- Centroid classification plus LLM escalation as two classifiers. Use one classification approach for the MVP.
- Telegram webhook mode; long polling is sufficient.
- Runtime editing of every retrieval/model parameter.
- Full knowledge-base reindex controls unless required by the chosen embedding workflow.
- Per-space FAISS files if one exact global index plus SQLite intent metadata can meet routing tests more simply.

Hybrid BM25 retrieval should be added only if the sample/golden questions prove that dense retrieval misses required exact tokens such as policy identifiers, salary bands, or section numbers. It is a sensible enhancement, but it should earn its place through a failing test rather than be mandatory from day one.

## Suggested implementation order

1. Establish the real two-channel demo path and credential setup early, since platform access is the largest external risk.
2. Implement PDF/DOCX ingestion, chunk storage, semantic retrieval, and source citations.
3. Implement one intent classifier with threshold and General fallback.
4. Complete the end-to-end query path and enforce the three-second measurement.
5. Build the five required admin screens around the working APIs.
6. Add genuine classification feedback/accuracy, integration error history, analytics, and CSV export.
7. Add retrieval or provider sophistication only when acceptance tests expose a concrete need.

## Acceptance checks before implementation is considered complete

- A real user can ask and receive an answer in two actual frontend tools.
- Each channel's admin test reaches a real destination and reports measured end-to-end latency and the failing stage.
- PDF and DOCX replacement/reparse workflows update retrieval results.
- Intent classification accuracy is based on labelled correctness, not confidence alone.
- Successful answers contain at least one verified citation; absent knowledge produces a clear no-match response.
- Integration errors retain channel, timestamp, stage, and reason and are visible in the admin console.
- Query history records timestamp, channel, intent, confidence, response, citations, status, and latency.
- Analytics show common intent spaces and most accessed documents and can be exported.
- The complete demo path meets the three-second target under a documented test setup.
