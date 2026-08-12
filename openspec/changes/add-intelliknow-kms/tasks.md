Ordered as a 7-day solo plan per the brief's timeline constraint. Each day ends with something demonstrable.

## 1. Day 1 — Foundation, configuration, provider layer

- [ ] 1.1 Initialize the project with `uv` (Python 3.12) and pinned dependencies: fastapi, uvicorn, streamlit, faiss-cpu, sentence-transformers, pypdf, pdfplumber, python-docx, openpyxl, anthropic, openai, httpx, botbuilder-core, pydantic, pyyaml, sqlalchemy, pytest
- [ ] 1.2 Create the Telegram bot via BotFather and capture the token; confirm the Bot Framework Emulator runs locally (Azure Bot registration is optional and only needed for a real Teams tenant)
- [ ] 1.3 Define the typed configuration schema and write `config.yaml` with all documented defaults, including the five intent spaces with descriptions and keywords
- [ ] 1.4 Implement `ConfigService`: load, validate, expose, atomic write with backup, in-place reload; reject unknown fields and out-of-range values at startup
- [ ] 1.5 Write `.env.example` for secrets only (provider keys, `TELEGRAM_BOT_TOKEN`, `TEAMS_APP_ID`, `TEAMS_APP_PASSWORD`, `ADMIN_PASSWORD`)
- [ ] 1.6 Define `LLMProvider` / `EmbeddingProvider` protocols, `LLMResult`, and `ProviderError` with its four categories
- [ ] 1.7 Implement `AnthropicLLM` with structured output, plus `OpenAILLM` and `LocalLLM` against the same interface
- [ ] 1.8 Implement `SentenceTransformerEmbedding` (default, normalized), `OpenAIEmbedding`, and `LocalEmbedding`
- [ ] 1.9 Implement the provider factory: selection from config, separate classify/generate models, unknown-name rejection, startup credential validation
- [ ] 1.10 Implement shared timeout, exponential-backoff retry, schema-validation retry, and error normalization
- [ ] 1.11 Unit-test the provider layer against fakes: order preservation, dimension match, schema retry, each error category
- [ ] 1.12 Create the SQLite schema (`document`, `chunk`, `chunk_fts`, `query_log`), enable WAL, and verify the FTS5 virtual table builds

## 2. Day 2 — Document loading and chunking

- [ ] 2.1 Define the `Block` model (heading / paragraph / table) with source reference, and the loader interface
- [ ] 2.2 Implement the PDF loader: `pypdf` body text plus `pdfplumber` table extraction, per-page source references
- [ ] 2.3 Implement the DOCX loader: `python-docx` headings, paragraphs, and tables with paragraph references
- [ ] 2.4 Implement the XLSX loader: `openpyxl` sheets rendered as tables with sheet-range references
- [ ] 2.5 Implement markdown table rendering shared by all three loaders
- [ ] 2.6 Implement ragged-table detection (inconsistent column counts, majority-empty cells) and the LLM restructuring fallback, falling back to raw text on model failure
- [ ] 2.7 Implement `StructuralChunker`: target size and overlap from config, table rows never split, small tables kept whole, heading path prefixed, no overlap across heading boundaries
- [ ] 2.8 Test loaders and chunker against an HR PDF with a salary-grid table, a Legal DOCX with headings, and a Finance XLSX; assert table rows survive intact

## 3. Day 3 — Indexing and the RAG write path

- [ ] 3.1 Implement the FAISS index store: per-space create, load, add, remove-by-id, search, move-vectors-between-spaces, delete, and disk persistence
- [ ] 3.2 Add a startup smoke check asserting a FAISS index round-trips through write and reload
- [ ] 3.3 Implement `IndexWriter` keeping `chunk`, `chunk_fts`, and the space's FAISS index consistent on add, remove, and reassign
- [ ] 3.4 Implement batched embedding at the configured batch size
- [ ] 3.5 Implement `data/index_meta.json`: record embedding model and dimension at first ingest; refuse config changes to either while documents exist; update on full re-index
- [ ] 3.6 Implement upload validation: configured extensions, configured size cap, SHA-256 duplicate rejection
- [ ] 3.7 Implement LLM-suggested intent assignment from space names, descriptions, and keywords plus the document's first 2000 characters, defaulting to the fallback space on provider failure
- [ ] 3.8 Implement the background ingestion worker with the full status machine and per-stage error capture
- [ ] 3.9 Implement re-parse, reassign (vectors move, no re-embed), delete (history preserved), and full re-index
- [ ] 3.10 Expose document endpoints on the admin API, including list with search by name/keyword and filters by format, upload date, and intent space
- [ ] 3.11 Test ingestion end to end plus the failure cases: corrupt file, scanned PDF with no text, duplicate upload, embedding provider outage

## 4. Day 4 — RAG read path and orchestrator

- [ ] 4.1 Implement dense retrieval across the supplied space indexes with per-space top-N and score-ordered merge
- [ ] 4.2 Implement BM25 keyword retrieval over `chunk_fts` filtered to the same spaces, disabled cleanly when the configured count is zero
- [ ] 4.3 Implement reciprocal rank fusion with the configured constant and final top-K selection
- [ ] 4.4 Implement the relevance gate on best dense cosine, returning no-match with no generation call
- [ ] 4.5 Implement `ContextBuilder`: near-duplicate removal, document-and-ordinal ordering, `[S#]` tagging with title/source ref/heading path, character budget enforcement
- [ ] 4.6 Implement `AnswerGenerator` with grounding rules, channel formatting profile, and citation instructions
- [ ] 4.7 Implement `CitationVerifier` resolving `[S#]` markers to supplied chunks and dropping unresolvable ones
- [ ] 4.8 Implement the no-match response naming the searched domain, and generation-failure handling
- [ ] 4.9 Implement classification with the structured schema, prompt built from live space names, descriptions, and keywords
- [ ] 4.10 Implement threshold enforcement, General-means-all-spaces, unknown-slug handling, and classification failure/timeout fallback
- [ ] 4.11 Run classification and query embedding concurrently
- [ ] 4.12 Implement the `POST /admin/test-query` operation returning intent, confidence, answer, sources, and latency without any channel
- [ ] 4.13 Test the read path: single-space isolation, hybrid finding an exact token dense search misses, below-floor no-match, citation verification dropping an unretrieved document, empty knowledge base
- [ ] 4.14 Test routing: above threshold, below threshold, exactly at threshold, General-classified, unknown slug, provider failure

## 5. Day 5 — Chat channels

- [ ] 5.1 Define `InboundMessage` / `OutboundAnswer` and the channel adapter interface
- [ ] 5.2 Implement the Telegram adapter in long-polling mode: receive, send, typing indicator, MarkdownV2 escaping, 4096-character enforcement
- [ ] 5.3 Implement Telegram webhook mode as the configurable alternative, mutually exclusive with polling
- [ ] 5.4 Implement the Teams adapter on `botbuilder-core` mounted on a FastAPI route, with typing activity and Teams-compatible formatting
- [ ] 5.5 Implement the deterministic per-channel formatter: escaping, list translation, word-boundary truncation with marker
- [ ] 5.6 Implement non-text message handling and inbound error isolation with platform acknowledgement
- [ ] 5.7 Implement per-channel Connected/Disconnected status with last success time, and channel error logging
- [ ] 5.8 Implement the per-channel end-to-end test reporting outcome, failing stage, and measured latency
- [ ] 5.9 Implement query logging after delivery with failure suppression, recording status, confidence, fallback flag, citations, retrieved document ids, and latency
- [ ] 5.10 Verify both channels end to end — Telegram against a real bot, Teams against the Bot Framework Emulator

## 6. Day 6 — Admin console

- [ ] 6.1 Build the Streamlit shell: password gate, sign-out, five-screen navigation, API client, and the shared CSS for 12px-radius / 16px-padding cards on a neutral base with per-module accent colours
- [ ] 6.2 Build the Dashboard: KB size, per-space counts, channel status, recent query volume, provider/model summary, problem highlighting with links, and the "Try a query" box
- [ ] 6.3 Build Frontend Integration: one card per tool with Connected/Disconnected indicator, credential last-4, setup guidance, and the test button reporting latency
- [ ] 6.4 Build Knowledge Base Management: document table (Name, Upload Date, Format, Size, Status, Actions), drag-and-drop upload zone stating supported formats, processing progress indicator, and status rendered as Processed/Pending/Error
- [ ] 6.5 Build the KB search bar and filters by format, upload date, and intent space
- [ ] 6.6 Build the KB row actions: View (intent space, chunk count, extracted chunks), Update (re-parse), Delete with confirmation, and error message display
- [ ] 6.7 Build Intent Space Configuration: card per space with name, description, document count, and classification accuracy rate with its derivation stated
- [ ] 6.8 Build the intent editor form (name, description, keywords) with the note that description and keywords drive classification, plus threshold and relevance-floor controls and protected-space handling
- [ ] 6.9 Build the query classification log on the Intent screen: recent queries, detected space, confidence, status, with filters
- [ ] 6.10 Build Analytics: period selector, intent space distribution, most accessed documents, query log with detail view, CSV export, and empty state
- [ ] 6.11 Implement readable error feedback preserving entered values, and destructive-action confirmations

## 7. Day 7 — Verification, documentation, delivery

- [ ] 7.1 Assemble `sample_docs/`: an HR PDF with a salary-grid table, a Legal DOCX, and a Finance XLSX
- [ ] 7.2 Run the full demo path: start both processes, upload all samples, verify each reaches Processed, query from Telegram and Teams, confirm cited answers
- [ ] 7.3 Verify routing against a scripted question set covering every intent space plus deliberately ambiguous questions; tune keywords and re-test via "Try a query"
- [ ] 7.4 Measure end-to-end latency against the ≤3s target on both channels; if missed, switch `model_classify` to a faster model and re-measure
- [ ] 7.5 Verify the classification log, intent distribution, most accessed documents, and CSV export all reflect the demo traffic
- [ ] 7.6 Walk `traceability.md` end to end and confirm every source-document clause is satisfied or listed as a deviation
- [ ] 7.7 Write the README: architecture overview, tech stack, setup, configuration reference, both integration guides, and troubleshooting
- [ ] 7.8 Write `docs/AI_USAGE.md`: key moments AI was used, how it sped up iteration, adjustments made to AI output, and the two named scenarios (salary-grid table extraction, channel-adaptive response formatting)
- [ ] 7.9 Push to the public GitHub repository with code, docs, and sample documents
- [ ] 7.10 Optional if time remains: add `Dockerfile` and `docker-compose.yml`, and document the cloudflared tunnel path for Teams against a real tenant
