# IntelliKnow KMS

IntelliKnow is an AI-assisted knowledge management system for small internal
knowledge bases. Administrators upload PDF, DOCX, and XLSX documents; the
system classifies them into intent spaces, retrieves relevant passages, and
returns concise, source-backed answers through the admin console, Telegram,
WhatsApp, or Microsoft Teams.

This repository is an MVP designed to run on one laptop with one API worker.
It favors a reproducible local deployment and observable behavior over cloud
infrastructure.

## Capabilities

- Five-view admin console: Dashboard, Frontend Integration, Knowledge Base,
  Intent Configuration, and Analytics
- Background document parsing, AI classification, chunking, indexing,
  reassignment, reprocessing, and deletion
- Hybrid FAISS and SQLite FTS5 retrieval, reciprocal-rank fusion, local
  cross-encoder reranking, relevance gating, and verified citations
- Configurable intent confidence with General fallback and admin-reviewed
  expected-intent examples
- Telegram long polling, WhatsApp Cloud API webhooks, and Microsoft Teams Bot
  Framework messages
- Encrypted channel credentials, eight-hour admin sessions, query history,
  latency metrics, usage analytics, and CSV export

## Architecture

```text
 Telegram polling -----\
 WhatsApp webhook ------> ChannelHandler -> QueryPipeline -> cited answer
 Teams /api/messages ---/                       |
                                                +-> SQLite query history

 Streamlit console -> authenticated FastAPI admin API
                          |-> document ingestion and lifecycle
                          |-> intent configuration and review labels
                          |-> FAISS vectors + SQLite FTS5
                          |-> encrypted integration credentials
                          +-> analytics and delivery tests
```

The API is the only owner of SQLite, FAISS, ingestion, and channel state.
Streamlit is an API client and does not access storage directly.

For the complete component design, including responsibilities, runtime
boundaries, RAG ingestion and query flows, channel interactions, data
ownership, security, failure handling, and MVP trade-offs, read the
[Detailed System Design](docs/ARCHITECTURE.md).

## Tech Stack

| Layer | Technology |
|---|---|
| Admin UI | Streamlit |
| API and validation | FastAPI, Uvicorn, Pydantic |
| LLM providers | Anthropic by default; OpenAI and local OpenAI-compatible providers supported |
| Embeddings | Sentence Transformers, `all-MiniLM-L6-v2` |
| Retrieval | FAISS, SQLite FTS5, reciprocal-rank fusion |
| Reranking | `cross-encoder/ms-marco-MiniLM-L-6-v2` |
| Persistence | SQLite, local FAISS indexes, managed upload directory |
| Document parsing | pypdf/pdfplumber, python-docx, openpyxl |
| Messaging | Telegram Bot API, WhatsApp Cloud API, Microsoft Bot Framework |
| Credential storage | Fernet-encrypted channel secrets; key supplied separately through `.env` |
| Packaging and tests | `uv`, locked dependencies, pytest, OpenSpec |

## Quick Start

For complete macOS, Linux, and Windows instructions, including HTTPS, proxy,
model-download, reset, and troubleshooting steps, read the
[Deployment Guide](docs/DEPLOYMENT.md).

Prerequisites: Git, `uv`, an x64 or supported ARM computer, network access to
the configured AI provider, and about 1 GB of free disk space.

1. Clone the repository and install the locked Python 3.12 environment:

   ```bash
   git clone https://github.com/realAaronWu/intelliKnow.git
   cd intelliKnow
   uv python install 3.12
   uv sync --frozen --python 3.12
   ```

2. Copy `.env.example` to `.env` (`Copy-Item .env.example .env` in Windows
   PowerShell), then set `ANTHROPIC_API_KEY`, `ADMIN_PASSWORD`, and a generated
   `CREDENTIAL_ENCRYPTION_KEY`. The deployment guide provides exact commands.

3. Download and validate the two local retrieval models:

   ```bash
   uv run python scripts/download_models.py
   uv run python scripts/smoke_provider.py
   ```

4. Start the API in one terminal:

   ```bash
   uv run uvicorn app.main:app --host 127.0.0.1 --port 8000
   ```

5. Start the console in a second terminal:

   ```bash
   uv run streamlit run app/ui/streamlit_app.py --server.address 127.0.0.1 --server.port 8501
   ```

Open [http://127.0.0.1:8501](http://127.0.0.1:8501), keep the default API
address `http://127.0.0.1:8000`, and sign in with `ADMIN_PASSWORD`.

The Bash lifecycle helper remains available for macOS and Linux:

```bash
INTELLIKNOW_HTTPS=0 ./scripts/laptop-demo install
INTELLIKNOW_HTTPS=0 ./scripts/laptop-demo download-models
INTELLIKNOW_HTTPS=0 ./scripts/laptop-demo start
```

## First Demo

1. Open **Knowledge Base** and upload documents from `demo-docs`.
2. Wait until every selected file is marked **Processed**.
3. Open **Dashboard** and ask a question answered explicitly in one document.
4. Confirm the intent, confidence, answer, source citation, and latency.
5. Open **Intent Configuration**, select a query, and record its expected
   intent to demonstrate admin-guided classifier improvement.
6. Open **Analytics** to inspect history, source usage, and CSV export.

## Frontend Integrations

| Channel | MVP transport | What the administrator needs |
|---|---|---|
| Telegram | Long polling | Bot token from BotFather; no public endpoint required |
| WhatsApp | Cloud API webhook | Meta app, business sender, access token, phone-number ID, app secret, verify token, and public HTTPS tunnel |
| Teams | Bot Framework endpoint | Bot Framework Emulator for a local demo, or Azure Bot registration and Microsoft 365 approval for real Teams |

Credentials are entered on **Frontend Integration**, encrypted before they are
stored in SQLite, and returned to the UI only in masked form. Employee and
administrator steps are in the consolidated
[Messaging Integrations Guide](docs/INTEGRATIONS.md), including a tenant-free
Teams Emulator walkthrough.

## Configuration and Data

- `config.yaml`: providers, models, thresholds, intents, channel modes, and
  storage paths
- `.env`: private provider keys, admin password, encryption key, and optional
  proxy settings; never commit this file
- `data/intelliknow.db`: documents, chunks, analytics, labels, and encrypted
  integration credentials
- `data/faiss`: vector indexes
- `data/uploads`: managed copies of uploaded documents

Keep `CREDENTIAL_ENCRYPTION_KEY` separate from database backups. Losing it
makes saved channel credentials unreadable; changing it requires clearing and
re-entering those credentials.

## Verification

```bash
uv run pytest
openspec validate add-intelliknow-kms --strict --no-interactive
```

The default pytest configuration excludes tests marked `slow`; run them with
`uv run pytest -m slow` when local model execution is required.

## MVP Operating Boundaries

- Run exactly one API worker. The SQLite/FAISS coordination lock is
  process-local.
- The admin password is an MVP bootstrap credential, not production identity
  management or role-based access control.
- Telegram has no user allowlist. Use non-sensitive material unless an access
  control layer is added.
- WhatsApp and real Teams require externally managed platform accounts and a
  public HTTPS callback.
- Local files are not replicated or backed up automatically.
- The three-second channel target includes provider and public-network time.

See [Requirements Audit](openspec/changes/add-intelliknow-kms/requirements-audit.md) for the implementation
assessment and production gaps.

## Documentation

- [Detailed system design](docs/ARCHITECTURE.md)
- [Deployment guide](docs/DEPLOYMENT.md)
- [Telegram, WhatsApp, and Teams guide](docs/INTEGRATIONS.md)
- [AI usage reflection](AI_USAGE.md)
- [Script usage manual](scripts/README.md)

Engineering artifacts are intentionally separate from user documentation:

- [OpenSpec design and requirements traceability](openspec/changes/add-intelliknow-kms/)
- [Superpowers plans, test plans, and acceptance evidence](superpowers/)
