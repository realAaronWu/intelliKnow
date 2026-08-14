# IntelliKnow Messaging Integrations

IntelliKnow answers questions from approved knowledge-base documents through
Telegram, WhatsApp, and Microsoft Teams. Every answer should include a source;
when the indexed material does not support an answer, IntelliKnow reports that
instead of guessing.

## Employee guide

### Telegram

1. Open the IntelliKnow bot link supplied by your administrator.
2. Select **Start**.
3. Send one clear text question.
4. Read the answer and check the **Sources** section.

Employees never need the Telegram bot token. It is an administrator credential
and must not be shared.

### WhatsApp

1. Open the conversation with the IntelliKnow business number supplied by your
   administrator.
2. Send one clear text question.
3. Read the reply and check its cited sources.

Employees do not need a Meta account or access token. During a Meta test setup,
the employee's number must be registered as an approved test recipient.

### Microsoft Teams

1. Open **Teams > Apps** and search for **IntelliKnow**, or follow the
   installation link supplied by your administrator.
2. Select **Add** or **Open**.
3. Send one clear question in the bot chat.
4. Read the answer and check its sources.

If the app is unavailable, a Teams administrator may still need to approve or
publish it for the organization. Personal Teams accounts cannot install this
organization bot.

### Asking useful questions

- Ask one question at a time.
- Use the organization's terminology, such as "annual leave" or "expense
  form".
- Include enough context: "What is the meal allowance for business travel?"
  is better than "What is the allowance?"
- Review the cited source before making an important HR, legal, or financial
  decision.

## Administrator prerequisites

Deploy IntelliKnow first by following [Deployment](DEPLOYMENT.md). Upload and
verify at least one document before testing a channel. Channel credentials are
entered on **Frontend Integration**, encrypted in SQLite, and returned to the
console only in masked form.

## Connect Telegram

Telegram uses long polling, so it does not require a public callback URL.

1. Open the verified [@BotFather](https://t.me/BotFather) account in Telegram.
2. Send `/newbot` and follow the prompts. The username must end in `bot`.
3. Copy the bot token shown by BotFather.
4. Open **Frontend Integration > Telegram**, enter the token, enable the
   integration, and select **Save**.
5. Open the new bot in Telegram, select **Start**, and ask a question whose
   answer exists in the knowledge base.
6. Confirm the reply includes a source and the console reports **Connected**.

Run exactly one IntelliKnow API process for a bot token. Two pollers cause
Telegram's `terminated by other getUpdates request` conflict.

If Telegram requires a proxy, add a dedicated route to `.env` and restart the
API:

```dotenv
TELEGRAM_PROXY_URL=http://127.0.0.1:8118
```

Anyone who discovers the bot can currently send it a private message. Use only
non-sensitive documents until an approved user allowlist is implemented. If a
token is exposed, revoke it with BotFather and save the replacement.

## Connect WhatsApp

WhatsApp uses Meta's Cloud API and requires a public HTTPS webhook. A Cloudflare
Quick Tunnel is sufficient for an MVP demonstration.

### 1. Prepare Meta

1. Create or open a Meta developer app and add the **WhatsApp** product.
2. Select the business sender that users will message and record its
   **Phone-number ID**.
3. For a Meta test sender, add each personal WhatsApp number as a test
   **recipient**. Do not register a personal recipient as a new business
   sender.
4. Obtain an access token with `whatsapp_business_messaging` and
   `whatsapp_business_management`. Temporary setup tokens expire; use a System
   User token when a stable demonstration is required.
5. Copy the Meta app secret from **App settings > Basic**.
6. Create a private webhook verify token. It can be any long random value known
   to Meta and IntelliKnow.

### 2. Expose the webhook

Keep the local API running and start a tunnel:

```text
cloudflared tunnel --url http://127.0.0.1:8000
```

Record the generated `https://...trycloudflare.com` address. Quick Tunnel URLs
change when the tunnel is recreated.

### 3. Save and subscribe

Open **Frontend Integration > WhatsApp** and enter:

- Access token
- Phone-number ID belonging to the selected sender
- Meta app secret
- Webhook verify token

Enable the integration and select **Save**. In Meta's WhatsApp configuration,
set:

```text
Callback URL: https://YOUR-TUNNEL.trycloudflare.com/api/whatsapp/webhook
Verify token:  the exact verify token saved in IntelliKnow
```

Select **Verify and save**, then subscribe the webhook to the **messages**
field. Ensure the app is subscribed to the WhatsApp Business Account that owns
the selected sender.

### 4. Verify the round trip

1. Send a new text question from an approved personal recipient to the selected
   business sender.
2. Confirm a cited response arrives in the same WhatsApp conversation.
3. Confirm **Frontend Integration > WhatsApp** reports **Connected**.
4. Use the console's **Test** action only after a real inbound message has
   recorded a destination and while Meta's customer-service window is open.

## Connect Microsoft Teams

Real Teams delivery requires a Microsoft 365 work or school tenant, custom-app
permission, an Entra application, an Azure Bot resource, and a public HTTPS
endpoint. The included Bot Framework Emulator flow proves the adapter locally
without claiming real Teams acceptance.

### Local Emulator demo without a Teams account

1. Install Microsoft's
   [Bot Framework Emulator](https://github.com/microsoft/BotFramework-Emulator/releases).
   The last official macOS package is version 4.14.1; Windows and Linux builds
   are available from the same release repository.
2. Start IntelliKnow and open **Frontend Integration > Microsoft Teams**.
3. Expand **Configuration** and select **Enable local Emulator**. Do not enter
   an application ID or password.
4. In the Emulator, select **Open Bot** and connect to:

   ```text
   http://localhost:8000/api/messages
   ```

5. Leave **Microsoft App ID** and **Microsoft App Password** empty.
6. Send a question whose answer exists in an uploaded document.
7. Confirm a typing indicator and cited answer appear in the Emulator.

Credential-free requests are accepted only when the caller and Bot Framework
service URL are local. Emulator success proves request parsing, conversation
routing, delivery, and formatting; it does not prove Azure connectivity,
tenant approval, or real Teams delivery.

### Real Teams deployment

1. Create or select a single-tenant Microsoft Entra application.
2. Record its application ID and directory tenant ID.
3. Create a client secret and copy its **value**, not its identifier.
4. Create an
   [Azure Bot resource](https://learn.microsoft.com/en-us/azure/bot-service/bot-service-quickstart-registration?view=azure-bot-service-4.0)
   for that application and enable the Microsoft Teams channel.
5. Publish IntelliKnow through public HTTPS and set the Azure Bot messaging
   endpoint to `https://YOUR-HOST/api/messages`.
6. Enter the application ID, client-secret value, and tenant ID under
   **Frontend Integration > Microsoft Teams**, then enable and save it.
7. Build the Teams package:

   ```text
   uv run python scripts/build_teams_app.py \
     --app-id YOUR_APPLICATION_ID \
     --public-url https://YOUR-HOST
   ```

8. Upload `dist/intelliknow-teams.zip` through **Teams > Apps > Manage your
   apps > Upload an app**, or ask the tenant administrator to publish it.
9. Open the bot in Teams, send a knowledge-base question, and confirm the reply
   includes a source.

## Security checklist

- Keep `.env`, bot tokens, access tokens, app secrets, and client secrets out of
  source control and chat transcripts.
- Upload only documents that every intended bot user is authorized to read.
- Confirm each WhatsApp Phone-number ID belongs to the intended sender.
- Restrict Teams installation and access with Microsoft 365 policies.
- Remove outdated or over-permissive knowledge-base documents promptly.
- Keep the encryption key separate from database backups.

## Troubleshooting

**Telegram does not answer:** confirm the saved token, proxy, enabled state, and
that no second poller or Telegram webhook is active.

**WhatsApp receives no inbound message:** confirm the tunnel URL is current,
the webhook is subscribed to `messages`, and the app is subscribed to the WABA
that owns the selected sender.

**WhatsApp error 190:** the access token expired or was revoked. Replace it.

**WhatsApp error 131030:** add the destination as an approved test recipient.

**Teams reports unauthorized:** verify the application ID, tenant ID,
client-secret value, single-tenant registration, and messaging endpoint.

**The channel answers without expected information:** confirm the relevant
document is indexed and ask a question using terminology present in that
document.
