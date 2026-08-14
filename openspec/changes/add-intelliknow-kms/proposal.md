## Why

Enterprise knowledge is fragmented across documents nobody can find, and the people who need it live in chat tools, not in yet another portal. IntelliKnow KMS closes that gap. This change defines the complete MVP — there is no existing system, so everything here is new.

Three goals define the scope. Everything else in this change exists only to serve them:

1. **Seamless integration with common frontend communication tools** — users ask questions from Telegram and WhatsApp and get answers there; a Teams adapter remains available for organizations with a suitable tenant.
2. **A backend that automatically builds and updates a knowledge base from uploaded documents** — PDF, Word, and Excel go in; a searchable, citable knowledge base comes out.
3. **Categorizing user queries into predefined intent spaces** — HR, Legal, Finance, Operations — to route each query to the relevant knowledge domain and produce accurate, context-aware responses.

## What Changes

- **RAG engine built from named, individually testable components** rather than a single retrieval step: structure-aware chunker, embedder, FAISS vector store, SQLite FTS5 keyword index, hybrid retriever with reciprocal-rank fusion, context builder, answer generator, and citation verifier.
- **Document-driven knowledge base**: admins upload PDF, DOCX, and XLSX; the system parses them (including embedded tables), chunks structure-aware, embeds, writes both a vector index and a keyword index, and files the result under an intent space. Documents can be re-parsed, reassigned, and deleted.
- **Hybrid retrieval with cross-encoder reranking**: dense vector search catches paraphrase, BM25 keyword search catches exact tokens (policy numbers, "Band L4", "Section 4.2"), reciprocal-rank fusion merges them without weight tuning, and a cross-encoder reranks the pool and supplies the relevance gate's signal.
- **Intent spaces as configuration**: HR, Legal, Finance, Operations, and General are declared in the config file with a name, description, and **classification keywords**, all editable from the console. Classification compares the query embedding against per-space centroids built from that admin-authored text and bounded reviewed examples, escalating to an LLM only when confidence is low, and assigns exactly one space per query with a confidence score; above the threshold retrieval is hard-filtered to that space, while a valid result below threshold searches only General. Unavailable or invalid classification returns a retryable error before retrieval. Keyword edits and expected-intent review labels are the brief's admin-guided accuracy controls and affect subsequent queries without re-indexing.
- **Two demonstrated chat frontends**: Telegram (long-polling) and WhatsApp (signed Cloud API webhook). Microsoft Teams remains an optional Bot Framework integration with local Emulator coverage, but no real-tenant acceptance claim.
- **Single configuration file**: one `config.yaml` holds every non-secret tunable. Service secrets and the credential-encryption key live in `.env`; admin-managed channel credentials are Fernet-encrypted in SQLite. Intent spaces, confidence threshold, and relevance floor apply without restart; provider, model, storage, chunking, and retrieval-topology changes require restart.
- **Pluggable AI provider layer**: a two-method `LLMProvider` / `EmbeddingProvider` interface with Anthropic, OpenAI, and local implementations selected from config.
- **Query classification log and analytics**: recent queries with timestamp, channel, question, detected intent space, confidence score, status (Success / No match / Failed), and optional reviewed classification feedback, plus intent space distribution, reviewed accuracy, most accessed documents, and CSV export.
- **Streamlit admin console** with the five screens the brief names — Dashboard, Frontend Integration, Knowledge Base Management, Intent Space Configuration, Analytics — laid out as accent-coloured cards with the document table, drag-and-drop upload zone, search and filters, and intent space card view the brief's visual guidance specifies.

## Capabilities

### New Capabilities

- `configuration`: The single config file, its schema and defaults, runtime reload, secret separation, and validation.
- `ai-provider`: Provider-agnostic text generation and embedding interfaces, backend selection from config, and error normalization.
- `document-ingestion`: Upload, parse, table extraction, structure-aware chunking, embedding, dual-index writes, re-parse, reassign, and delete.
- `intent-management`: Intent spaces declared in config with name, description and keywords, protected General, per-space document counts and accuracy rate, index lifecycle, and the classification threshold.
- `query-orchestration`: Intent classification, confidence scoring, threshold enforcement, and routing to the correct knowledge domain.
- `knowledge-retrieval`: The RAG read path — hybrid retrieval, rank fusion, relevance gating, context assembly, grounded answer generation, citation verification, and channel-appropriate formatting.
- `frontend-integration`: Telegram, WhatsApp, and Teams adapters, message normalization, delivery, status, and end-to-end connection tests.
- `analytics-and-history`: Query logging, the query classification log, intent space distribution, most accessed documents, and CSV export.
- `admin-console`: The five admin screens, their layout and visual scheme, and admin sign-in.

### Modified Capabilities

None — this is the first change in the project.

## Impact

- **New repository layout**: `app/` (FastAPI service), `admin/` (Streamlit console), `tests/`, `docs/`, `sample_docs/`, `config.yaml`, `.env.example`.
- **New runtime dependencies**: `fastapi`, `uvicorn`, `streamlit`, `faiss-cpu`, `sentence-transformers`, `pypdf`, `pdfplumber`, `python-docx`, `openpyxl`, `anthropic`, `openai`, `httpx`, `aiohttp`, `botbuilder-core`, `cryptography`, `pydantic`, `pyyaml`, `sqlalchemy`. SQLite FTS5 ships with Python's `sqlite3` — no dependency.
- **New persistent state**: one SQLite file and a directory of FAISS index files under `data/`.
- **New external dependencies**: a Telegram bot token, Meta WhatsApp Cloud API credentials plus a reachable HTTPS callback, an AI provider API key, and an admin password. Teams additionally requires Azure Bot and Microsoft 365 tenant setup when used. A local Fernet key encrypts channel credentials at rest. Docker and cloud secret-management services are not required.
