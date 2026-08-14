# Script Usage Manual

Run commands from the repository root after completing the
[deployment guide](../docs/DEPLOYMENT.md). Use the locked environment for
Python commands:

```bash
uv sync --frozen --python 3.12
```

Most scripts read `config.yaml` and `.env`. Never put passwords, provider keys,
bot tokens, or encryption keys directly in a command because they may remain in
shell history. Commands that use the admin API read `ADMIN_PASSWORD` from
`.env` by default.

## Quick Reference

| Script | Audience | Purpose | Service state |
|---|---|---|---|
| `laptop-demo` | Evaluator/admin | Install, start, stop, and inspect the laptop deployment | Manages both services |
| `download_models.py` | Evaluator/admin | Cache and validate embedding and reranker models | Stopped recommended |
| `smoke_provider.py` | Evaluator/admin | Test the configured LLM and local models | Either |
| `reset_demo.py` | Evaluator/admin | Erase demo content and usage while preserving credentials | API must be running |
| `check_channel_acceptance.py` | Admin/tester | Measure real channel delivery and latency | API and channel running |
| `build_teams_app.py` | Teams admin | Build a Teams sideload package | Either |
| `ingest.py` | Developer | Ingest files directly and inspect all three indexes | API must be stopped |
| `ask.py` | Developer | Print a full classification and retrieval trace | API stopped recommended |
| `calibrate.py` | Developer | Run a bounded threshold sanity check | API must be stopped |
| `make_fixtures.py` | Test maintainer | Regenerate committed synthetic test files | Services stopped recommended |
| `migrate_keychain_credentials.py` | Legacy macOS migration only | Move old Keychain credentials into encrypted SQLite | API must be stopped |

## Laptop Deployment Helper

`scripts/laptop-demo` is the recommended macOS/Linux entry point. Windows
users should follow the two-terminal commands in the deployment guide.

```bash
./scripts/laptop-demo help
./scripts/laptop-demo install
./scripts/laptop-demo download-models
./scripts/laptop-demo preflight
./scripts/laptop-demo start
./scripts/laptop-demo status
./scripts/laptop-demo logs
./scripts/laptop-demo stop
./scripts/laptop-demo restart
```

Important behavior:

- `install` installs the exact versions from `uv.lock`.
- `download-models` downloads and executes both local retrieval models.
- `preflight` makes a real request to the configured LLM and uses cached local
  models only.
- `start` runs preflight before launching one API worker and Streamlit.
- `logs` follows both runtime logs; press `Control-C` to stop following them.
- `stop` stops processes without deleting the database or uploads.
- The helper remembers its last addresses in `.run/laptop-demo/runtime.env`.

HTTPS is the helper default. For the portable evaluator HTTP path:

```bash
INTELLIKNOW_HTTPS=0 ./scripts/laptop-demo start
```

For trusted HTTPS on macOS:

```bash
./scripts/laptop-demo setup-https
INTELLIKNOW_HTTPS=1 ./scripts/laptop-demo restart
```

`setup-https` installs a local CA in the macOS login Keychain and may request
Touch ID or the macOS login password. Fully restart browsers afterward.

Override a port when another application already uses the default:

```bash
INTELLIKNOW_HTTPS=0 INTELLIKNOW_API_PORT=8012 INTELLIKNOW_UI_PORT=8502 \
  ./scripts/laptop-demo start
```

## Model and Provider Checks

### `download_models.py`

Downloads and validates the models selected in `config.yaml`:

```bash
uv run python scripts/download_models.py
```

Success ends with `Both local models are cached and executable.` Re-running is
safe and reuses cached files. Prefer `laptop-demo download-models` on
macOS/Linux because it also validates required configuration and forwards
interrupts cleanly.

### `smoke_provider.py`

Checks the embedding dimension, reranker output, and one schema-constrained LLM
classification:

```bash
uv run python scripts/smoke_provider.py
```

Success ends with `smoke check OK`. Anthropic or OpenAI configurations require
the corresponding key and incur one small API request. A local LLM
configuration requires its configured server to be running.

## Demo Reset

`reset_demo.py` deletes documents, chunks, custom intents, managed uploads,
query history, analytics, manual labels, and integration errors. It preserves
the five baseline intents and encrypted frontend credentials.

Keep the API running, then execute:

```bash
uv run python scripts/reset_demo.py --yes
```

The script reads the API URL saved by `laptop-demo`. For a manually started
server, provide it explicitly:

```bash
uv run python scripts/reset_demo.py --yes \
  --api-url http://127.0.0.1:8000
```

For custom HTTPS, add `--ca-cert /path/to/rootCA.pem`. The command refuses to
erase anything unless `--yes` is present and verifies that content and usage
tables are empty before reporting success.

## Channel Acceptance

Before measuring a channel:

1. Configure and enable it in **Frontend Integration**.
2. Send the bot a real message so IntelliKnow records a reply destination.
3. Upload documents that answer the acceptance questions.

Run one question several times:

```bash
uv run python scripts/check_channel_acceptance.py \
  --channel telegram \
  --question "How many days of annual leave do employees receive?" \
  --runs 4
```

Or use the maintained question file:

```bash
uv run python scripts/check_channel_acceptance.py \
  --channel whatsapp \
  --questions-file superpowers/evidence/acceptance-questions.txt \
  --runs 4 \
  --require-real-platform
```

Supported channels are `telegram`, `whatsapp`, and `teams`. The default target
is 3,000 ms; change it with `--target-ms`. The command exits nonzero when
delivery fails, p95 exceeds the target, or `--require-real-platform` detects a
local Teams Emulator destination.

## Microsoft Teams Package

Build a Teams package only after obtaining a Microsoft application ID and a
public HTTPS callback URL:

```bash
uv run python scripts/build_teams_app.py \
  --app-id 00000000-0000-0000-0000-000000000000 \
  --public-url https://kms.example.com
```

The default output is `dist/intelliknow-teams.zip`. Use `--output PATH` to
choose another location. The command prints the matching Azure Bot messaging
endpoint, which ends in `/api/messages`.

## Direct Pipeline Diagnostics

These tools open the configured SQLite and FAISS stores directly. Stop the API
before using a command that writes those stores; the MVP supports one owner of
storage state at a time.

### `ingest.py`

Ingests one or more documents without the admin API and prints classification,
chunk previews, FTS5 integrity, and FAISS counts:

```bash
./scripts/laptop-demo stop
uv run python scripts/ingest.py demo-docs/hr/handbook.pdf \
  demo-docs/finance/budget.xlsx
```

This modifies the real paths configured under `storage` in `config.yaml`. Use
the UI for normal administration; this script is intended for debugging the
write path.

### `ask.py`

Runs one question through the production query pipeline and prints
classification, routing, dense and keyword hits, fusion, reranking, relevance
gate, answer, citations, and timings:

```bash
uv run python scripts/ask.py \
  "How many days of annual leave do full-time employees receive?"
```

Force one intent and bypass classification when isolating retrieval:

```bash
uv run python scripts/ask.py "What is the annual leave allowance?" --space hr
```

The configured database must already contain indexed documents. This diagnostic
does not add a row to admin query history.

### `calibrate.py`

Runs a small qualitative sweep of centroid temperature and relevance floor:

```bash
./scripts/laptop-demo stop
uv run python scripts/calibrate.py
```

This is a bounded sanity check over the current local corpus, not a production
accuracy evaluation. It does not modify `config.yaml`; review the output before
manually changing thresholds.

## Test Fixture Maintenance

`make_fixtures.py` deterministically regenerates files under
`tests/fixtures/docs/`:

```bash
uv run python -m scripts.make_fixtures
uv run pytest -q tests/test_fixtures.py
```

This is a test-maintainer command, not a way to create demo documents. Review
the generated Git diff before committing fixture changes.

## Legacy Keychain Migration

`migrate_keychain_credentials.py` exists only for databases created by the
removed macOS Keychain credential implementation. Fresh installations must not
run it.

For an affected old installation, stop IntelliKnow and back up both `data/`
and `.env`, then run:

```bash
./scripts/laptop-demo stop
uv run python scripts/migrate_keychain_credentials.py
```

Optional arguments are `--config`, `--env`, and `--service`. After each channel
is committed to encrypted SQLite, the script deletes that legacy Keychain
entry. It is macOS-only and safe to rerun when no legacy records remain.

## Command Help

Commands with arguments expose concise built-in help:

```bash
uv run python scripts/ask.py --help
uv run python scripts/build_teams_app.py --help
uv run python scripts/check_channel_acceptance.py --help
uv run python scripts/migrate_keychain_credentials.py --help
uv run python scripts/reset_demo.py --help
./scripts/laptop-demo help
```
