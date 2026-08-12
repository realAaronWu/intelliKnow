## 1. Project setup and prerequisites

- [ ] 1.1 Initialize the Python project with `uv` (Python 3.12), `pyproject.toml`, and pinned dependencies: fastapi, uvicorn, streamlit, faiss-cpu, sentence-transformers, pypdf, pdfplumber, python-docx, openpyxl, anthropic, openai, cryptography, httpx, botbuilder-core, pydantic-settings, sqlalchemy, pytest
- [ ] 1.2 **Verify Azure Bot registration access before writing any Teams code** — create the Azure Bot resource and capture App ID + password, or report the blocker to the user (this gates all of section 8)
- [ ] 1.3 Create the Telegram bot via BotFather and capture the token
- [ ] 1.4 Write `.env.example` covering provider selection, both model settings, embedding model, admin password, admin API token, Fernet credential key, public base URL, and Telegram mode
- [ ] 1.5 Write `Dockerfile` and `docker-compose.yml` for the API and console services with volumes for `data/sqlite` and `data/faiss`; verify the image builds for the host architecture
- [ ] 1.6 Add a container smoke test asserting FAISS can build, persist, and reload an index

## 2. Data layer

- [ ] 2.1 Define SQLAlchemy models for `intent_space`, `document`, `chunk`, `integration`, `query_log`, `chunk_hit`, and `app_setting` per design.md — Data model
- [ ] 2.2 Implement schema creation on startup, enable WAL mode, and seed the four default intent spaces idempotently
- [ ] 2.3 Implement the `app_setting` accessor with typed defaults for confidence threshold (0.70), relevance floor (0.35), embedding model, and embedding dimension
- [ ] 2.4 Implement the FAISS index store: per-space create, load, add, remove-by-id, search, move-vectors-between-indexes, and delete, with persistence to disk

## 3. AI provider layer

- [ ] 3.1 Define the `LLMProvider` and `EmbeddingProvider` protocols, the `LLMResult` type, and the `ProviderError` type with its four categories
- [ ] 3.2 Implement `AnthropicLLM` using `claude-opus-5` by default, with structured output via `output_config.format` for the schema path
- [ ] 3.3 Implement `OpenAILLM` and `LocalLLM` (Ollama-compatible HTTP) against the same interface
- [ ] 3.4 Implement `SentenceTransformerEmbedding` (default), `OpenAIEmbedding`, and `LocalEmbedding`, each reporting its dimension
- [ ] 3.5 Implement the provider factory: selection from `LLM_PROVIDER` / `EMBEDDING_PROVIDER`, separate classify and generate models, unknown-name rejection, and startup credential validation
- [ ] 3.6 Implement shared timeout, exponential-backoff retry, schema-validation retry, and error normalization across all implementations
- [ ] 3.7 Unit-test the provider layer against fakes: order preservation, dimension match, schema retry, and each error category

## 4. Intent management

- [ ] 4.1 Implement intent space CRUD with slug uniqueness and kebab-case normalization
- [ ] 4.2 Enforce General as protected: block deletion and slug changes, allow name and description edits
- [ ] 4.3 Implement space deletion with required document reassignment, defaulting to General
- [ ] 4.4 Wire index lifecycle to space lifecycle: create on add, delete on remove, move vectors on document reassignment
- [ ] 4.5 Implement runtime threshold and relevance floor settings with range validation and no-restart application
- [ ] 4.6 Expose intent management over the admin API

## 5. Document ingestion

- [ ] 5.1 Implement upload validation: extension allowlist, MIME sniff, 25 MB cap, filename sanitization, and SHA-256 duplicate rejection
- [ ] 5.2 Implement the PDF parser: pypdf body text plus pdfplumber table extraction rendered to markdown tables, with per-page source references
- [ ] 5.3 Implement the DOCX parser: python-docx paragraphs and tables, with paragraph source references
- [ ] 5.4 Implement the XLSX parser: openpyxl sheets rendered to markdown tables, with sheet-range source references
- [ ] 5.5 Implement ragged-table detection and the LLM restructuring fallback, including fallback-to-raw-text on model failure
- [ ] 5.6 Implement the chunker: ~800 chars with ~100-char overlap, table rows never split, source reference carried per chunk
- [ ] 5.7 Implement LLM-suggested intent assignment at ingest, defaulting to General when the provider fails
- [ ] 5.8 Implement batched embedding and index writes into the assigned space
- [ ] 5.9 Implement the background ingestion worker with the full status machine and per-stage error capture
- [ ] 5.10 Implement re-parse (replace all chunks and vectors), delete (preserving `chunk_hit` history), and full re-index
- [ ] 5.11 Implement embedding model consistency: record on first ingest, fail startup on mismatch, clear on re-index
- [ ] 5.12 Expose document management over the admin API
- [ ] 5.13 Test ingestion against sample HR PDF (with a salary-grid table), Legal DOCX, and Finance XLSX; plus corrupt-file, scanned-PDF, and duplicate-upload failure cases

## 6. Query orchestration

- [ ] 6.1 Implement the classification call with the structured schema `{intent_slug, confidence, reasoning}`, building the prompt from live space names and descriptions
- [ ] 6.2 Implement threshold enforcement, General-means-all-spaces handling, and unknown-slug handling
- [ ] 6.3 Implement classification failure and timeout fallback to General without erroring the user
- [ ] 6.4 Run classification and query embedding concurrently
- [ ] 6.5 Implement the routing hand-off passing an explicit space list to retrieval
- [ ] 6.6 Test routing: above threshold, below threshold, exactly at threshold, General-classified, unknown slug, and provider failure

## 7. Knowledge retrieval and answer generation

- [ ] 7.1 Implement multi-index search with score merging and top-5 selection
- [ ] 7.2 Implement the relevance floor gate producing a no-match without a generation call
- [ ] 7.3 Implement the grounded generation prompt: chunks delimited as untrusted data, answer-only-from-context instruction, channel profile injected
- [ ] 7.4 Implement citation construction and verification against the retrieved chunk set
- [ ] 7.5 Implement the no-match response naming the searched domain, recorded as success rather than error
- [ ] 7.6 Implement the deterministic per-channel formatter: markup escaping, list translation, word-boundary truncation with marker
- [ ] 7.7 Implement `chunk_hit` recording
- [ ] 7.8 Implement generation failure handling with a user-facing message
- [ ] 7.9 Test retrieval: single-space isolation, multi-space merge, below-floor no-match, citation verification dropping an unretrieved document, and empty knowledge base

## 8. Frontend integrations

- [ ] 8.1 Implement encrypted credential storage with Fernet, masked API reads, and startup failure on a missing or invalid key
- [ ] 8.2 Define the normalized `InboundMessage` / `OutboundAnswer` types and the channel adapter interface
- [ ] 8.3 Implement the Telegram adapter: webhook endpoint with secret-token verification, send, typing indicator, and MarkdownV2 escaping within the 4096-character limit
- [ ] 8.4 Implement Telegram webhook registration on save and re-registration on startup, surfacing failures to channel status
- [ ] 8.5 Implement Telegram polling mode as an alternative to webhook mode, mutually exclusive with it
- [ ] 8.6 Implement the Teams adapter on `botbuilder-core` `CloudAdapter` mounted on a FastAPI route, with Bot Framework JWT validation, typing activity, and Teams-compatible formatting
- [ ] 8.7 Implement non-text message handling and inbound error isolation returning platform acknowledgement
- [ ] 8.8 Implement per-channel status tracking with last success time and last error
- [ ] 8.9 Implement the per-channel end-to-end test reporting outcome, failing stage, and measured latency
- [ ] 8.10 Test both adapters end to end; verify Teams against the Bot Framework Emulator as well as a real tenant

## 9. Analytics and history

- [ ] 9.1 Implement post-delivery query logging with failure suppression
- [ ] 9.2 Implement history listing with date, channel, intent, fallback, and no-match filters
- [ ] 9.3 Implement classification metrics: intent distribution, confidence distribution, fallback rate
- [ ] 9.4 Implement admin classification review with corrections and measured accuracy over reviewed queries
- [ ] 9.5 Implement KB usage metrics: most-accessed documents, unused documents, no-match rate, top no-match questions
- [ ] 9.6 Implement latency reporting: mean and p95, overall and per channel
- [ ] 9.7 Implement CSV export including the empty-period header-only case
- [ ] 9.8 Expose analytics over the admin API

## 10. Admin console

- [ ] 10.1 Build the Streamlit shell: password gate with constant-time comparison, sign-out, navigation, and the API client holding the bearer token server-side
- [ ] 10.2 Build the Dashboard screen including problem highlighting with links to the resolving screen
- [ ] 10.3 Build the Frontend Integrations screen: credential entry, masked redisplay, enable/disable, status, setup guidance, and the test action
- [ ] 10.4 Build the Knowledge Base screen: upload, list with status polling, reassign, re-parse, delete, and failure-reason display
- [ ] 10.5 Build the Intent Configuration screen: space CRUD, protected-space handling, threshold and floor controls, and the description-affects-routing note
- [ ] 10.6 Build the Analytics screen: date range, all metrics, filterable history with correction controls, CSV export, and empty state
- [ ] 10.7 Implement destructive-action confirmations and readable error feedback that preserves entered values

## 11. Integration, verification, and delivery

- [ ] 11.1 Assemble `sample_docs/` — at minimum an HR PDF containing a salary-grid table, a Legal DOCX, and a Finance XLSX
- [ ] 11.2 Run the full demo path: compose up, tunnel, configure both channels, upload all samples, query from Telegram and Teams, verify cited answers
- [ ] 11.3 Verify routing behavior against a scripted question set covering each intent space plus deliberately ambiguous questions
- [ ] 11.4 Measure end-to-end latency against the ≤3s target on both channels and record the result; tune `LLM_MODEL_CLASSIFY` if the budget is missed
- [ ] 11.5 Verify analytics reflect the demo traffic and that CSV export round-trips
- [ ] 11.6 Write the README: architecture overview, setup, environment variables, both integration guides including Azure Bot registration, the cloudflared tunnel-URL caveat, and troubleshooting
- [ ] 11.7 Write `docs/AI_USAGE.md` covering the required reflection: turning points, iteration speed, adjustments to AI output, and the two named scenarios (table extraction from the HR salary grid, channel-adaptive response formatting)
- [ ] 11.8 Publish the public GitHub repository with code, docs, and sample documents
