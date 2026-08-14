# Using IntelliKnow in Telegram, WhatsApp, and Microsoft Teams

IntelliKnow answers questions from your organization's approved documents. It is useful for everyday questions such as:

- "How many days of annual leave do employees receive?"
- "Which form is required for meal expenses?"
- "Does VPN access need manager approval?"
- "How long does the NDA remain in effect?"

IntelliKnow shows the source document with its answer. If it cannot find supporting information, it says so instead of guessing.

## For employees: ask IntelliKnow a question

### Telegram

1. Open the IntelliKnow bot link supplied by your administrator.
2. Select **Start**.
3. Type one clear question and send it.
4. Wait for the answer and check the **Sources** shown beneath it.

You never need the Telegram bot token. That token is a private administrator password and must not be shared with users.

### WhatsApp

1. Open the WhatsApp conversation with the IntelliKnow business number supplied
   by your administrator.
2. Type one clear text question and send it.
3. Wait for the answer and check its cited sources.

You do not need an access token or a Meta developer account. Those are private
administrator credentials. During a Meta test setup, your personal number must
be an approved recipient of the business sender.

### Microsoft Teams

1. Open Microsoft Teams.
2. Select **Apps** and search for **IntelliKnow**. Your administrator may also send you a direct installation link.
3. Select **Add** or **Open**.
4. Type one clear question in the IntelliKnow chat and send it.
5. Check the sources shown with the answer.

If IntelliKnow is not visible in Teams, contact your Microsoft 365 or Teams administrator. The app may still need to be approved for your organization.

## Tips for useful questions

- Ask one question at a time.
- Use the same words your organization uses, such as "annual leave", "expense form", or "salary band".
- Include useful detail: "What is the meal allowance for business travel?" is better than "What is the allowance?"
- Read the cited source before making an important HR, legal, or financial decision.
- If the answer says the information was not found, ask the knowledge-base administrator whether the relevant document has been uploaded.

## Common messages

**"Please send a text question."**

IntelliKnow received an image, sticker, attachment, or empty message. Type the question as text.

**"I couldn't find anything..."**

The uploaded documents do not appear to answer the question. IntelliKnow will not invent an answer.

**"Sorry, I couldn't answer that question."**

There was a temporary service or AI-provider problem. Try once more, then contact the administrator if it continues.

## For administrators: connect Telegram

You need access to the computer running IntelliKnow and a Telegram account.

1. In Telegram, open the verified [**@BotFather**](https://t.me/BotFather) account.
2. Send `/newbot` and follow the prompts. Choose a username ending in `bot`.
3. BotFather displays the bot token. Treat it like a password.
4. Open IntelliKnow's admin console, select **Frontend Integration**, enter the
   token under **Telegram**, and select **Save**. IntelliKnow encrypts it in the
   local database; the encryption key stays in the private `.env` file.

5. If Telegram requires your organization's proxy, add the proxy variables used by your network. Example:

   ```dotenv
   TELEGRAM_PROXY_URL=socks5://127.0.0.1:8119
   ```

6. In `config.yaml`, confirm `channels.telegram.enabled` is `true` and `mode` is `polling`.
7. Restart IntelliKnow.
8. Open the bot in Telegram, select **Start**, and send a question whose answer is in an uploaded document.
9. Confirm the answer includes a source.

Do not email, paste into chat, or commit the bot token to Git. If it is exposed, use BotFather's `/revoke` command and replace it in `.env`.

By default, anyone who discovers the bot can send it a private message. Use only non-sensitive demo documents until IntelliKnow has an approved user allowlist or another access-control layer.

## For administrators: connect WhatsApp

This setup uses Meta's WhatsApp Cloud API and requires a public HTTPS callback.
For a laptop demo, a Cloudflare Quick Tunnel is sufficient.

### 1. Prepare Meta

1. Create or open a Meta developer app and add the **WhatsApp** product.
2. In the WhatsApp setup, identify the business **sender** number. Do not use
   **Add new number** for a personal recipient; that action registers a business
   sender and may ask you to migrate an existing WhatsApp account.
3. Note the **Phone-number ID** for the selected sender. The ID and sender must
   remain matched. If Meta displays multiple test or business numbers, select
   the number users will actually message before copying its ID.
4. For a Meta test sender, add each personal WhatsApp number as a test
   **recipient** under the send-message task. A recipient should already have a
   WhatsApp account and does not need to be migrated.
5. Obtain an access token with `whatsapp_business_messaging` and
   `whatsapp_business_management`. A temporary setup token normally expires
   after about 24 hours. A System User token is preferred for a stable demo and
   may have no reported expiry, but remains revocable.
6. Copy the Meta app secret from **App settings > Basic**.
7. Generate a private verify token, for example:

   ```bash
   openssl rand -hex 32
   ```

### 2. Start a public callback

With IntelliKnow running on HTTPS port 8012, start a tunnel:

```bash
cloudflared tunnel --protocol http2 \
  --url https://localhost:8012 \
  --origin-ca-pool .run/laptop-demo/tls/rootCA.pem
```

Record the printed `https://...trycloudflare.com` address. Quick Tunnel URLs
are temporary and change when the tunnel is recreated.

### 3. Save IntelliKnow credentials

Open **Frontend Integration > WhatsApp** and enter:

- **Access token:** the temporary or System User token
- **Phone-number ID:** the ID belonging to the chosen sender
- **Meta app secret:** the app's secret, used to authenticate callbacks
- **Webhook verify token:** the random value created above

Turn on **Enabled** and select **Save**. IntelliKnow encrypts all four values in
SQLite and never returns them through the admin API.

### 4. Configure and subscribe the webhook

In Meta's WhatsApp **Configuration** page, set:

```text
Callback URL: https://YOUR-TUNNEL.trycloudflare.com/api/whatsapp/webhook
Verify token:  the exact token saved in IntelliKnow
```

Select **Verify and save**, then subscribe to the **messages** field.

Meta can create separate WhatsApp Business Accounts (WABAs) for a public test
number and a real business sender. The IntelliKnow app must be subscribed to
the WABA that owns the exact sender users will message. If verification works
but inbound messages never reach the API, check this WABA subscription in
Meta's configuration rather than changing the local webhook.

### 5. Verify the round trip

1. From an approved personal recipient, send a fresh text question to the
   selected business sender.
2. Confirm a cited response arrives in the same WhatsApp conversation.
3. Confirm **Frontend Integration > WhatsApp** reports **Connected**.
4. Use the integration **Test** button only after the first real message has
   stored a reply destination and while the 24-hour customer-service window is
   open.

WhatsApp was verified end to end during the laptop demo. A real query completed
through outbound delivery in 1,829 ms, within the three-second target.

## For administrators: connect Microsoft Teams

The adapter, Bot Framework endpoint, app package, and local Emulator flow are
implemented. Real tenant delivery has not been verified end to end in this
project because it requires a Microsoft 365 work/school tenant with custom-app
permission. Treat the steps below as deployment guidance, not recorded
acceptance evidence.

This setup normally requires help from a Microsoft 365 or Azure administrator. You need an Azure subscription or approved Azure Bot resource, permission to upload or publish a Teams app, and a public HTTPS address for IntelliKnow.

1. Create or choose the Microsoft Entra application used by the bot. For a new
   Azure Bot, use **Single Tenant**.
2. Record its **Application (client) ID** and **Directory (tenant) ID**.
3. Create a client secret and copy its **Value** immediately. Do not use the secret's identifier.
4. Create or configure an [Azure Bot resource](https://learn.microsoft.com/en-us/azure/bot-service/bot-service-quickstart-registration?view=azure-bot-service-4.0) for that application.
5. Enable the **Microsoft Teams** channel on the Azure Bot resource.
6. Publish IntelliKnow at a public HTTPS address. Its messaging endpoint is:

   ```text
   https://your-intelliknow-address/api/messages
   ```

7. Enter that full address as the bot's messaging endpoint in Azure.
8. In IntelliKnow's **Frontend Integration** page, enter the application ID,
   client-secret value, and directory tenant ID under **Microsoft Teams**, then
   select **Save**. Do not enter the secret ID.

9. In `config.yaml`, set `channels.teams.enabled` to `true` and set `public_base_url` to the public HTTPS base address.
10. Restart IntelliKnow.
11. Build the included Teams app package:

   ```bash
   .venv/bin/python scripts/build_teams_app.py \
     --app-id YOUR_APPLICATION_ID \
     --public-url https://your-intelliknow-address
   ```

12. In **Teams > Apps > Manage your apps > Upload an app**, upload
   `dist/intelliknow-teams.zip`. If **Upload an app** is absent, a Teams admin
   must allow custom app upload or publish the package for the organization.
13. Open IntelliKnow in Teams and send a question whose answer is in an uploaded document.
14. Confirm the answer includes a source.
15. Run the measured real-platform check:

   ```bash
   .venv/bin/python scripts/check_channel_acceptance.py \
     --channel teams \
     --require-real-platform \
     --questions-file docs/acceptance-questions.txt \
     --runs 4
   ```

   The command fails unless delivery succeeds from the stored real Teams
   conversation and p95 end-to-end latency is at most three seconds.

A personal Microsoft Teams account cannot upload and run this organization bot.
Use a Microsoft 365 work or school tenant with custom-app permission. The local
Emulator remains the account-free adapter demonstration, not real Teams
acceptance.

The public `/api/messages` endpoint is authenticated by the Microsoft Bot Framework. Do not place the IntelliKnow admin password in Teams or Azure Bot settings.

## Local Teams check before publishing

An administrator or developer can use the Bot Framework Emulator before Azure and Teams publishing is complete. Follow the complete [Local Microsoft Teams Demo](LOCAL-TEAMS-DEMO.md) guide.

1. Start IntelliKnow locally.
2. Open Bot Framework Emulator.
3. Connect to `http://localhost:8000/api/messages`.
4. Leave the Microsoft App ID and password empty for this local-only check.
5. Send a question and confirm that a cited answer returns.

Credential-free Emulator access is accepted only from the same computer. Emulator success proves the local adapter works, but it does not prove that Azure or the real Teams tenant is configured correctly.

## Safety checklist

- Upload only documents that the intended bot users are allowed to read.
- Keep `.env` private and out of source control.
- Restrict who can install or access the Teams app through Microsoft 365 policies.
- Remember that the current Telegram bot has no user allowlist.
- Keep the WhatsApp app secret, access token, and verify token private.
- Confirm that a WhatsApp Phone-number ID belongs to the intended sender.
- Review cited documents for HR, legal, compliance, and financial decisions.
- Remove outdated documents from IntelliKnow promptly.

## When setup fails

**Telegram does not answer:** confirm the token, proxy, and `enabled` setting; ensure no Telegram webhook is configured because polling and webhooks cannot run together.

**WhatsApp receives no inbound message:** confirm the tunnel URL is current,
the webhook is subscribed to **messages**, and the app is subscribed to the
WABA that owns the selected sender.

**WhatsApp reports error 190:** the access token expired or was revoked. Create
a new token and replace it on the Frontend Integration page.

**WhatsApp reports error 131030:** the destination is not an approved test
recipient. Add it as a recipient in Meta's send-message task, not as a new
business sender.

**Meta asks to migrate a personal number:** you opened the business-sender
registration flow. Cancel it and add the number as a recipient instead.

**Teams reports unauthorized:** confirm the application ID, directory tenant ID,
client-secret value, single-tenant Azure Bot registration, and messaging
endpoint. A secret identifier is not a secret value.

**Teams cannot reach IntelliKnow:** the endpoint must use public HTTPS with a valid certificate. `localhost` works only with the Emulator on the IntelliKnow computer.

**The answer has no expected information:** confirm the document is uploaded and indexed, then test a question using the exact wording in the document.
