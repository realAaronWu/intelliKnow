## Why

Enterprise knowledge is fragmented across documents nobody can find, and the people who need it live in chat tools, not in yet another portal. IntelliKnow KMS closes that gap: admins drop documents into a web console, the system parses and indexes them into named intent domains, and employees ask questions from Telegram or Microsoft Teams and get cited answers drawn from those documents. This change defines the complete MVP — there is no existing system, so everything here is new.

## What Changes

- **New backend service** (FastAPI + SQLite + FAISS) exposing an internal admin API, two chat webhook endpoints, and a synchronous query pipeline.
- **Document-driven knowledge base**: admins upload PDF, DOCX, and XLSX files; the system parses them (including embedded tables), chunks the content, generates embeddings, assigns an intent space, and indexes the chunks for semantic search. Documents can be re-parsed and deleted.
- **Intent spaces**: HR, Legal, Finance, and General ship as defaults; admins can create, rename, describe, and delete custom spaces. General is a protected fallback space that cannot be deleted.
- **Query orchestrator**: every inbound question is classified into exactly one intent space with a confidence score. Above the configurable threshold (default 0.70) retrieval is hard-filtered to that space; below it, the query falls back to General, which searches every space.
- **Cited answer generation**: retrieved chunks are synthesized into a concise answer with document citations, formatted for the originating chat channel, with an explicit "no match" response when retrieval finds nothing relevant.
- **Two chat frontends**: Telegram (Bot API) and Microsoft Teams (Bot Framework). Admins store bot credentials encrypted at rest through the console, monitor per-channel connection status, and run an end-to-end self-test from the UI.
- **Pluggable AI provider layer**: a narrow `LLMProvider` / `EmbeddingProvider` interface with Anthropic, OpenAI, and local implementations selected by environment variable, so generation, classification, and embeddings can each be pointed at a different backend without touching call sites.
- **Analytics and history**: every query is logged with timestamp, channel, classified intent, confidence, fallback flag, answer, latency, and the chunks that were retrieved. The console reports intent distribution, classification confidence, most-accessed documents, and no-match rate, and exports the log as CSV.
- **Streamlit admin console** with five screens: Dashboard, Frontend Integrations, Knowledge Base, Intent Configuration, and Analytics.
- **Local deployment**: Docker Compose runs the API and console; a cloudflared tunnel supplies the public HTTPS URL the Teams and Telegram webhooks require.

## Capabilities

### New Capabilities

- `ai-provider`: Provider-agnostic interfaces for text generation and embeddings, backend selection by configuration, startup validation, and failure surfacing.
- `document-ingestion`: Upload, parse, table extraction, chunking, embedding, indexing, re-parsing, and deletion of PDF/DOCX/XLSX source documents.
- `intent-management`: Lifecycle of intent spaces, protected defaults, per-space document association, and the configurable classification confidence threshold.
- `query-orchestration`: Intent classification of inbound questions, confidence scoring, threshold enforcement, and routing to the correct knowledge domain.
- `knowledge-retrieval`: Semantic search within the routed domain, answer synthesis with citations, no-match handling, and channel-aware response formatting.
- `frontend-integration`: Telegram and Microsoft Teams adapters, encrypted credential storage, inbound/outbound message handling, connection status monitoring, and the admin-triggered end-to-end test.
- `analytics-and-history`: Query logging, retrieval-hit tracking, aggregate metrics, and CSV export.
- `admin-console`: The five admin screens, admin authentication, and the interactions each screen has with the backend API.

### Modified Capabilities

None — this is the first change in the project.

## Impact

- **New repository layout**: `app/` (FastAPI service), `admin/` (Streamlit console), `tests/`, `docs/`, `sample_docs/`, `docker-compose.yml`, `Dockerfile`, `.env.example`.
- **New runtime dependencies**: `fastapi`, `uvicorn`, `streamlit`, `faiss-cpu`, `sentence-transformers`, `pypdf`, `pdfplumber`, `python-docx`, `openpyxl`, `anthropic`, `openai`, `cryptography`, `httpx`, `botbuilder-core`, `pydantic-settings`, `sqlalchemy`.
- **New persistent state**: a SQLite database file and a directory of FAISS index files, both mounted as Docker volumes so they survive container restarts.
- **New external dependencies**: a Telegram bot token from BotFather, an Azure Bot registration (App ID + password) for Teams, an AI provider API key, and a cloudflared tunnel for public HTTPS ingress.
- **Secrets handling**: bot credentials are encrypted with a Fernet key supplied by environment variable; the encryption key and provider API keys never enter the database or the repository.
