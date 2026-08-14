# IntelliKnow KMS

IntelliKnow is a lightweight, document-backed knowledge management system. It ingests PDF, DOCX, and XLSX files, classifies questions into intent spaces, retrieves relevant passages with FAISS and SQLite FTS5, and returns concise answers with verified citations through Telegram, Microsoft Teams, or the admin console.

## What is included

- Five-view Streamlit admin console: Dashboard, Frontend Integration, Knowledge Base, Intent Configuration, and Analytics
- PDF, DOCX, and XLSX ingestion with background processing, re-parsing, reassignment, and deletion
- HR, Legal, Finance, Operations, and protected General intent spaces, plus custom intent CRUD
- Hybrid semantic and keyword retrieval, reranking, relevance gating, and verified citations
- Telegram long polling and a Microsoft Teams Bot Framework endpoint
- Encrypted integration credentials, connection status, retained errors, and delivery tests
- Query history, reviewed classification accuracy, KB usage analytics, and CSV export

## Architecture

```text
Telegram polling ----\
                       ChannelHandler -> QueryPipeline -> cited response
Teams /api/messages --/                       |
                                                +-> SQLite query history

Streamlit console -> authenticated FastAPI admin API
                         |-> config and intents
                         |-> ingestion and FAISS/FTS5
                         |-> integrations
                         +-> analytics and review feedback
```

The application is intentionally one process and one API worker. Streamlit is an HTTP client only; it never opens the database, edits configuration, or accesses FAISS directly.

## Setup

Requirements: Python 3.12 and `uv`.

```bash
git clone https://github.com/realAaronWu/intelliKnow.git
cd intelliKnow
uv sync
cp .env.example .env
```

Set at least these values in `.env`:

```dotenv
ANTHROPIC_API_KEY=your-anthropic-api-key
HF_TOKEN=your-hugging-face-read-token
HF_HUB_DISABLE_XET=1
ADMIN_PASSWORD=choose-a-private-password
```

Each laptop creates its own `.env` from the credential-free template; never copy
or commit another operator's file. `HF_TOKEN` is recommended for authenticated,
higher-limit model downloads. The complete credential source and database
transfer guidance is in [Configure secrets on each laptop](docs/LAPTOP-DEMO-DEPLOYMENT.md#4-configure-secrets-on-each-laptop).

The shipped demo uses Anthropic's `claude-haiku-4-5` for classification and generation, plus local sentence-transformer embeddings. A zero-cost Ollama-compatible path remains available by changing the provider, model names, and `llm.base_url` in `config.yaml`.

## Run

For the recommended laptop deployment, follow [IntelliKnow Laptop Demo Deployment](docs/LAPTOP-DEMO-DEPLOYMENT.md). Its lifecycle helper validates the provider, downloads and exercises both local models, and starts both components with health checks:

```bash
./scripts/laptop-demo install
./scripts/laptop-demo download-models
./scripts/laptop-demo start
```

`start` never downloads models. Run `download-models` once and wait for both
models to report ready; interrupted transfers can be resumed with the same
command.

The equivalent HTTPS commands are below after running
`./scripts/laptop-demo setup-https`.

Start the API with one worker:

```bash
uv run uvicorn app.main:app --host 127.0.0.1 --port 8000 \
  --ssl-certfile .run/laptop-demo/tls/localhost.pem \
  --ssl-keyfile .run/laptop-demo/tls/localhost-key.pem
```

In a second terminal, start the console:

```bash
INTELLIKNOW_API_URL=https://127.0.0.1:8000 \
INTELLIKNOW_CA_CERT=.run/laptop-demo/tls/rootCA.pem \
uv run streamlit run streamlit_app.py \
  --server.sslCertFile .run/laptop-demo/tls/localhost.pem \
  --server.sslKeyFile .run/laptop-demo/tls/localhost-key.pem
```

Open [https://127.0.0.1:8501](https://127.0.0.1:8501) and sign in with
`ADMIN_PASSWORD`. The console keeps that browser signed in for eight hours across
page refreshes. **Sign out** clears the session immediately; the password itself
is never stored in the browser cookie.

## First demo

1. Open **Knowledge Base** and upload at least two PDF, DOCX, or XLSX documents.
2. Wait until their status is **Processed**.
3. Open **Dashboard** and ask a question answered by one of those documents.
4. Confirm the result shows an intent, confidence, response, source, and latency.
5. Open **Intent Configuration** to review classifications and tune keywords or thresholds.
6. Open **Analytics** to inspect history, document usage, and CSV export.

Channel setup is covered in [Connecting Telegram and Microsoft Teams](docs/CONNECTING-TELEGRAM-AND-TEAMS.md). A tenant-free adapter demonstration is covered in [Local Microsoft Teams Demo](docs/LOCAL-TEAMS-DEMO.md).

## Tests

```bash
uv run pytest
uv run openspec validate add-intelliknow-kms --strict
```

Slow tests that load real models are intentionally excluded by the default pytest configuration. Run them explicitly with `uv run pytest -m slow`.

## Operational notes

- Run exactly one API worker; the FAISS synchronization lock is process-local.
- Keep `.env` private. The API and console never return plaintext channel credentials.
- Telegram currently has no user allowlist. Use only documents suitable for anyone who can discover the bot.
- Real Teams delivery requires an Azure Bot registration, public HTTPS endpoint, and Microsoft 365 tenant approval.
- Reviewed classification accuracy is shown only after an admin labels query outcomes. Confidence is reported separately and is not called accuracy.
- The three-second channel target is measured through outbound delivery and depends on the selected provider and model.

## Troubleshooting

**API will not start:** ensure `ADMIN_PASSWORD` and the configured AI-provider key are set. For a legacy database containing encrypted channel credentials, provide its original `CREDENTIAL_ENCRYPTION_KEY` for one migration startup.

**Console cannot connect:** confirm the API is running and `INTELLIKNOW_API_URL` points to the correct port.

**Local model errors:** start Ollama or the configured OpenAI-compatible server, and confirm `llm.base_url` and model names.

**Document remains Error:** open its detail in Knowledge Base, read the parsing error, then re-parse or replace the source file.

**Telegram or Teams is Disconnected:** open Frontend Integration to inspect recent errors and run the destination-aware test.

## Documentation

- [Laptop demo deployment runbook](docs/LAPTOP-DEMO-DEPLOYMENT.md)
- [AI usage reflection](docs/AI_USAGE.md)
- [Telegram and Teams guide](docs/CONNECTING-TELEGRAM-AND-TEAMS.md)
- [Local Teams demo](docs/LOCAL-TEAMS-DEMO.md)
- [OpenSpec design](openspec/changes/add-intelliknow-kms/design.md)
