# Local Microsoft Teams Demo

This guide demonstrates IntelliKnow's Microsoft Teams conversation flow on one Mac using Microsoft Bot Framework Emulator. It does not require a Microsoft 365 account, Azure registration, public URL, Teams app package, application ID, or client secret.

The Emulator sends the same Bot Framework activity shape used by Teams. IntelliKnow receives the question at `/api/messages`, shows a typing indicator, runs the normal knowledge pipeline, and returns a cited answer.

## What this demo proves

- The Bot Framework endpoint receives and normalizes a message.
- IntelliKnow searches the existing knowledge base.
- Typing and answer activities return to the same conversation.
- The answer uses Teams-compatible formatting and includes sources.
- Query status, citations, reply destination, and delivery latency are recorded.

It does not prove Azure connectivity, Teams tenant approval, or real Microsoft Teams delivery.

## 1. Install Bot Framework Emulator on macOS

Microsoft no longer publishes a macOS artifact with its newest Emulator release. The last official Mac build is version 4.14.1.

1. Download [Bot Framework Emulator 4.14.1 for macOS](https://github.com/microsoft/BotFramework-Emulator/releases/download/v4.14.1/BotFramework-Emulator-4.14.1-mac.dmg).
2. Open the downloaded `.dmg` file.
3. Drag **Bot Framework Emulator** into **Applications**.
4. Open it from Applications.
5. If macOS blocks it, open **System Settings > Privacy & Security**, find the blocked-app message, and select **Open Anyway**.
6. On an Apple Silicon Mac, install Rosetta if macOS requests it.

The download is hosted in Microsoft's [official Emulator repository](https://github.com/microsoft/BotFramework-Emulator/releases/tag/v4.14.1).

## 2. Prepare IntelliKnow

Open Terminal and move to the repository:

```bash
cd /Users/aaron/workspace/intelliKnow
```

Confirm that `.env` contains:

```dotenv
ADMIN_PASSWORD=a-private-admin-password
CREDENTIAL_ENCRYPTION_KEY=a-valid-fernet-key
```

Generate a Fernet key when one is not already available:

```bash
uv run python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Paste the generated value after `CREDENTIAL_ENCRYPTION_KEY=` in `.env`. Keep it private and do not commit `.env`.

The configured AI provider must also be available:

- For `llm.provider: local`, start the configured local OpenAI-compatible model server before IntelliKnow.
- For `llm.provider: anthropic`, set `ANTHROPIC_API_KEY` in `.env` and configure supported Anthropic model names in `config.yaml`.
- For `llm.provider: openai`, set `OPENAI_API_KEY` in `.env` and configure supported OpenAI model names.

Confirm that `config.yaml` enables Teams:

```yaml
channels:
  teams:
    enabled: true
    max_message_chars: 28000
```

No `TEAMS_APP_ID` or `TEAMS_APP_PASSWORD` is needed for the local Emulator demo.

## 3. Start IntelliKnow

Use the laptop deployment helper from the repository root:

```bash
./scripts/laptop-demo start
```

Wait for:

```text
IntelliKnow is ready.
Admin console: http://127.0.0.1:8501
API docs:     http://127.0.0.1:8000/docs
```

The helper runs provider checks, starts exactly one API worker and the admin console, verifies both processes, and writes logs under `.run/laptop-demo/`.

Verify that the running build contains the Teams endpoint by opening:

[http://127.0.0.1:8000/docs](http://127.0.0.1:8000/docs)

The page should list `POST /api/messages`.

## 4. Connect the Emulator

1. Open **Bot Framework Emulator**.
2. Select **Open Bot**.
3. Enter this bot URL:

   ```text
   http://localhost:8000/api/messages
   ```

4. Leave **Microsoft App ID** empty.
5. Leave **Microsoft App Password** empty.
6. Select **Connect**.

Credential-free requests are accepted only when the caller, IntelliKnow URL, and Bot Framework service URL are local. Public requests still require Bot Framework authentication.

## 5. Ask demo questions

Send one of these questions in the Emulator chat:

```text
How many days of annual leave do full-time employees receive?
```

```text
What is the daily meal reimbursement limit and which form must I submit?
```

```text
Does VPN access require manager approval?
```

Expected behavior:

1. A typing indicator appears.
2. A grounded answer is returned.
3. A **Sources** section names one or more indexed documents.
4. The Emulator log shows a successful `POST /api/messages` exchange.

For the annual-leave question, the answer should say that full-time employees receive 25 days per calendar year and cite `handbook.pdf` or `wrapped_table.docx`.

## 6. Finish the demo

Stop IntelliKnow and then close the Emulator:

```bash
./scripts/laptop-demo stop
```

## Troubleshooting

### The Emulator cannot connect

- Confirm IntelliKnow is still running.
- Use `http://localhost:8000/api/messages`, including `/api/messages`.
- Confirm the App ID and password fields are empty.
- Open `http://127.0.0.1:8000/docs` and verify `POST /api/messages` is listed.
- If port `8000` is occupied, start IntelliKnow with `INTELLIKNOW_API_PORT=8011 ./scripts/laptop-demo start` and use `http://localhost:8011/api/messages` in the Emulator.

### `Teams is disabled`

Set `channels.teams.enabled` to `true` in `config.yaml`, then restart IntelliKnow. If the integration was previously disabled in persistent state, re-enable it through the integration API when Task 04 is available or use a clean local demo database.

### IntelliKnow starts but cannot answer

The Teams transport is working, but the configured AI provider is unavailable. Start the local model server or configure a valid remote provider and API key.

### macOS will not open the Emulator

Use **System Settings > Privacy & Security > Open Anyway**. The Mac build is older because Microsoft stopped attaching macOS packages after Emulator 4.14.1.

### An answer has no expected information

Confirm the relevant document is indexed in IntelliKnow. Ask with wording that appears in the document, such as "annual leave", "meal reimbursement", or "VPN approval".

## Moving from Emulator to real Teams

A real Teams deployment additionally needs:

- a Microsoft 365 work or school tenant;
- Microsoft Entra application and client secret;
- Azure Bot resource with the Teams channel enabled;
- a public HTTPS IntelliKnow address ending in `/api/messages`;
- a Teams app package approved or uploaded in the tenant.

Emulator success is a useful local check, but it is not real Teams delivery acceptance.
