The implementation is organized by delivered increments, followed by a stabilization gate and the two remaining feature increments. Checked items are already present on `main`; Task 0 is the next required gate.

## 1. Foundation (complete)

- [x] 1.1 Typed configuration, atomic writes, secret separation, and documented defaults
- [x] 1.2 Provider protocols, Anthropic/OpenAI/local adapters, factory, retries, and normalized errors
- [x] 1.3 SQLite schema with WAL, document/chunk/query/integration tables, and FTS5
- [x] 1.4 Foundation unit and integration tests

## 2. Test corpus (partially complete)

- [x] 2.1 Synthetic PDF, DOCX, and XLSX fixtures for deterministic tests
- [x] 2.2 Corpus utilities and fixture validation
- [ ] 2.3 Label the routing and retrieval question set before claiming model-quality accuracy

## 3. RAG write path (complete)

- [x] 3.1 PDF, DOCX, and XLSX loaders with structural blocks and source references
- [x] 3.2 Structure-aware chunking and table preservation
- [x] 3.3 FAISS/FTS5 index writing, metadata compatibility, upload validation, and background ingestion
- [x] 3.4 Re-parse, reassignment, deletion, re-index, and document admin endpoints
- [x] 3.5 Write-path tests and failure cases

## 4. RAG read path (complete)

- [x] 4.1 Dense and keyword retrieval, reciprocal-rank fusion, reranking, and relevance gate
- [x] 4.2 Context assembly, grounded generation, citation verification, and channel profiles
- [x] 4.3 Intent centroid classification, LLM escalation, routing, and admin test query
- [x] 4.4 Read-path and routing tests

## 0. Stabilization gate

- [x] 0.1 Declare `uvicorn`, `streamlit`, `cryptography`, `botbuilder-core`, and the SDK-required `aiohttp` as direct runtime dependencies
- [x] 0.2 Protect production administrative routes with `ADMIN_PASSWORD` bearer authentication
- [x] 0.3 Serialize all FAISS search and mutation operations with one process-wide `RLock`
- [x] 0.4 Restrict runtime configuration updates to intent spaces, confidence threshold, and relevance floor; make ingestion read the current config snapshot
- [x] 0.5 Return `no_match` when generation has zero verified citations
- [x] 0.6 Preserve at least one compact verified source when channel output is truncated
- [x] 0.7 Mark documents stranded in `pending` or `parsing` as failed and retryable at startup
- [x] 0.8 Run focused tests, the full suite, and strict OpenSpec validation

## 5. Channels

- [x] 5.1 Implement Fernet-encrypted integration credentials with masked output ready for the admin API
- [x] 5.2 Persist per-channel status, recent errors, last success, and last reply reference
- [x] 5.3 Implement normalized inbound contracts and one shared channel handler
- [ ] 5.4 Implement Telegram long polling, typing indication, delivery, and duplicate-offset protection
- [ ] 5.5 Implement the authenticated Bot Framework messaging endpoint for Teams
- [x] 5.6 Log query results after delivery and isolate analytics failures from user replies
- [ ] 5.7 Add authenticated integration status/configuration/test endpoints
- [ ] 5.8 Verify real Telegram and real Teams round trips and measure latency through send completion

## 6. Admin console and delivery

- [ ] 6.1 Implement one authenticated admin router for intents, safe config, analytics, feedback, and dashboard summaries
- [ ] 6.2 Implement one Streamlit app with five sidebar-selected views
- [ ] 6.3 Complete Dashboard and Frontend Integration workflows
- [ ] 6.4 Complete Knowledge Base Management workflows
- [ ] 6.5 Complete Intent Space Configuration, reviewed feedback, and honest accuracy reporting
- [ ] 6.6 Complete Analytics, query detail, filters, and CSV export
- [ ] 6.7 Run full demo, labelled quality checks, latency checks, and five-view acceptance
- [ ] 6.8 Complete README, integration/setup guides, troubleshooting, and AI usage notes
