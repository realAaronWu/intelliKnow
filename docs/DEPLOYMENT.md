# IntelliKnow Deployment Guide

This guide deploys the IntelliKnow MVP from a clean clone on macOS, Linux, or
Windows. The recommended evaluator path uses local HTTP bound to `127.0.0.1`.
That path is portable, avoids local-certificate trust differences, and still
allows WhatsApp or Teams callbacks through a public HTTPS tunnel.

## 1. Deployment model

The MVP runs two local processes:

| Component | Default address | Responsibility |
|---|---|---|
| FastAPI API | `http://127.0.0.1:8000` | Authentication, ingestion, RAG, channels, storage, analytics |
| Streamlit console | `http://127.0.0.1:8501` | Administrator interface and API client |

Run one API worker only. SQLite and FAISS are local, and the synchronization
lock protecting their updates is process-local.

### Supported evaluator platforms

| Platform | Recommended shell | Notes |
|---|---|---|
| macOS 13+ | Terminal with zsh/bash | Apple Silicon and Intel are supported |
| Linux | bash | x86_64 and ARM64 glibc distributions are supported by the locked native dependencies |
| Windows 10/11 x64 | PowerShell 7 or Windows PowerShell | Native x64 is supported; use WSL2 on Windows ARM or if a native wheel is unavailable |

Python 3.12 is required. `uv` installs it and creates `.venv`, so a separate
system Python installation is optional.

## 2. Prerequisites

Install:

- Git
- `uv`
- An Anthropic API key for the shipped configuration
- About 1 GB of free disk space for dependencies, model caches, and demo data
- Internet access to PyPI, Hugging Face, and the configured LLM provider

Install `uv` using its
[official installation instructions](https://docs.astral.sh/uv/getting-started/installation/).

macOS or Linux:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

Windows PowerShell:

```powershell
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
```

Open a new terminal if `uv --version` is not immediately available.

## 3. Clone and install

The following commands are identical on all three platforms:

```text
git clone https://github.com/realAaronWu/intelliKnow.git
cd intelliKnow
uv python install 3.12
uv sync --frozen --python 3.12
```

`--frozen` installs the dependency versions recorded in `uv.lock` and refuses
to silently rewrite the lock file.

## 4. Configure private values

Create a local `.env` file.

macOS or Linux:

```bash
cp .env.example .env
```

Windows PowerShell:

```powershell
Copy-Item .env.example .env
```

Generate a Fernet encryption key. This command works in every supported shell:

```text
uv run python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Paste the result into `.env` and set these values:

```dotenv
ANTHROPIC_API_KEY=your-anthropic-api-key
HF_TOKEN=your-optional-hugging-face-read-token
CREDENTIAL_ENCRYPTION_KEY=the-generated-fernet-key
ADMIN_PASSWORD=choose-a-private-password-of-at-least-12-characters
INTELLIKNOW_HTTPS=0
```

Important rules:

- Never commit `.env`; it is already ignored by Git.
- Create a new `.env` on each evaluator laptop instead of sharing personal
  credentials.
- Back up `CREDENTIAL_ENCRYPTION_KEY` separately from
  `data/intelliknow.db`. The encrypted channel credentials cannot be recovered
  without the matching key.
- `HF_TOKEN` is optional but recommended for authenticated Hugging Face
  downloads. A read-only token is sufficient.

### Other LLM providers

The repository defaults to Anthropic. To use OpenAI, set
`llm.provider: openai`, choose the models in `config.yaml`, and set
`OPENAI_API_KEY` in `.env`. To use an OpenAI-compatible local server, set
`llm.provider: local`, update `llm.base_url` and both model names, then ensure
that server is running before preflight.

Do not change the embedding model after indexing documents. The application
checks index metadata at startup and rejects incompatible changes; reset and
re-index the knowledge base when changing embedding models.

## 5. Download local models

IntelliKnow uses two local Hugging Face models:

- Embedding: `all-MiniLM-L6-v2`
- Reranker: `cross-encoder/ms-marco-MiniLM-L-6-v2`

Download and execute both before the first startup:

```text
uv run python scripts/download_models.py
```

Wait for both `Embedding ready` and `Reranker ready`. Re-running the command is
safe and resumes or reuses cached files.

Then test the real configured provider and the cached local models:

```text
uv run python scripts/smoke_provider.py
```

Successful output ends with `smoke check OK`. This preflight makes one small
real LLM classification request and therefore needs a valid provider key.

## 6. Start IntelliKnow

### Terminal 1: API

From the repository root:

```text
uv run uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Wait for Uvicorn to report that startup is complete. API documentation is then
available at [http://127.0.0.1:8000/docs](http://127.0.0.1:8000/docs).

### Terminal 2: admin console

Open a second terminal in the same repository:

```text
uv run streamlit run app/ui/streamlit_app.py --server.address 127.0.0.1 --server.port 8501
```

Open [http://127.0.0.1:8501](http://127.0.0.1:8501). On the login screen:

1. Keep **API address** as `http://127.0.0.1:8000`.
2. Enter the `ADMIN_PASSWORD` from `.env`.
3. Select **Sign in**.

The signed session lasts eight hours and survives normal page refreshes.
Selecting **Sign out** invalidates the browser session immediately.

### Stop

Press **Control-C** once in each terminal. This stops the console and API but
does not delete documents, configuration, credentials, or analytics.

## 7. macOS/Linux lifecycle helper

The Bash helper adds model preflight, process management, health checks, logs,
and optional trusted localhost HTTPS:

```bash
INTELLIKNOW_HTTPS=0 ./scripts/laptop-demo install
INTELLIKNOW_HTTPS=0 ./scripts/laptop-demo download-models
INTELLIKNOW_HTTPS=0 ./scripts/laptop-demo start
./scripts/laptop-demo status
./scripts/laptop-demo logs
./scripts/laptop-demo stop
```

Use the native two-terminal commands in section 6 on Windows. The application
itself is portable; this convenience script requires Bash and uses Unix process
management.

For trusted local HTTPS on macOS, run:

```bash
./scripts/laptop-demo setup-https
./scripts/laptop-demo restart
```

Then open `https://127.0.0.1:8501`. Certificate installation modifies the
local trust store and may require an operating-system password.

## 8. Verify a clean deployment

1. Open **Dashboard** and confirm Documents and Knowledge chunks are zero on a
   fresh database.
2. Open **Knowledge Base** and upload one or more files from `demo-docs`.
3. Wait until every upload is **Processed** and inspect its assigned intent and
   extracted chunks.
4. Ask a question on **Dashboard** whose answer appears explicitly in an
   uploaded document.
5. Confirm the answer includes an intent, confidence, source citation, and
   latency.
6. Open **Intent Configuration** and record an expected intent for one query.
7. Open **Analytics** and verify the query and source document appear.

The application intentionally fails document ingestion when AI classification
is unavailable or invalid; it does not silently index an unclassified upload.
Low-confidence user questions fall back to the configured General intent.

## 9. Connect messaging channels

After the local application is verified, follow
[Messaging Integrations](INTEGRATIONS.md) to connect Telegram, WhatsApp, or
Microsoft Teams. That guide covers employee use, encrypted administrator
credentials, public webhook tunnels, the account-free Teams Emulator flow,
real Teams deployment, security, and troubleshooting.

## 10. Data, backup, and reset

Runtime data is stored under:

```text
data/intelliknow.db   SQLite records and encrypted channel credentials
data/faiss/           vector indexes
data/uploads/         managed document copies
```

For an MVP backup, stop both processes and copy all three locations together.
Store the matching `.env` encryption key separately and securely.

To clear documents, chunks, custom intents, query history, analytics, managed
uploads, and integration errors while preserving built-in intents and channel
credentials, keep the API running and execute:

```text
uv run python scripts/reset_demo.py --yes --api-url http://127.0.0.1:8000
```

This operation is destructive. Files under `demo-docs` are test fixtures and
are not removed.

## 11. Proxy and download troubleshooting

Set proxy variables in `.env` when the network requires them:

```dotenv
ALL_PROXY=http://127.0.0.1:8118
HTTPS_PROXY=http://127.0.0.1:8118
HTTP_PROXY=http://127.0.0.1:8118
TELEGRAM_PROXY_URL=http://127.0.0.1:8118
WHATSAPP_PROXY_URL=http://127.0.0.1:8118
```

Use the proxy URL and port actually provided by the local proxy application.
Prefer `http://` for an HTTP proxy and `socks5h://` when SOCKS remote DNS is
required.

If a Hugging Face Xet transfer stalls, add the following and rerun the model
download command:

```dotenv
HF_HUB_DISABLE_XET=1
```

The model cache is outside the repository. Its location follows the Hugging
Face defaults unless `HF_HOME` is configured.

## 12. Common failures

**`CREDENTIAL_ENCRYPTION_KEY is required`**

Generate a Fernet key using section 4 and place it in `.env` with no quotes or
extra spaces.

**Saved integration credentials cannot be decrypted**

The database was opened with a different encryption key. Restore the original
key or clear and re-enter all channel credentials.

**The console cannot reach the API**

Confirm Terminal 1 is running, use the same scheme and port on the login page,
and check that ports 8000 and 8501 are not already occupied.

**A document remains in Error**

Open its detail in **Knowledge Base**. Classification outages, unsupported
content, password-protected files, and duplicate content are reported there.

**Windows cannot install `faiss-cpu`**

Confirm Windows is x64 and Python is 3.12. On Windows ARM, use an x64 Python
environment or WSL2 with Ubuntu.

**Telegram reports a `getUpdates` conflict**

Stop every other process using the same bot token, including old development
servers, then start exactly one API instance.

**WhatsApp receives messages but sends no reply**

Confirm the tunnel is still running, Meta is subscribed to the `messages`
field, the phone-number ID matches the sender, the recipient is approved for a
test sender, and the access token has not expired.

## 13. Test and acceptance commands

Run the automated suite:

```text
uv run pytest
```

Run real local-model tests separately:

```text
uv run pytest -m slow
```

If OpenSpec CLI is installed, validate the implementation specification:

```text
openspec validate add-intelliknow-kms --strict --no-interactive
```

Before handing the laptop to an evaluator, confirm:

- Provider smoke check passes.
- Both local models are already cached.
- API and console start from a clean terminal.
- At least one document can be uploaded, classified, and cited.
- Admin login survives a page refresh.
- Only one API worker is running.
- Any enabled real channel can receive and display one cited answer.
- `.env`, access tokens, and bot credentials are absent from Git history.

## 14. Production gaps

This deployment demonstrates the MVP, not a production topology. A production
version should add an external identity provider and RBAC, multi-user channel
authorization, a durable job queue, transactional vector storage, centralized
secret management, backups and restore tests, rate limiting, audit retention,
monitoring, horizontal-worker coordination, managed TLS, and deployment
automation.
