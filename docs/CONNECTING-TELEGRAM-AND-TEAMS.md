# Using IntelliKnow in Telegram and Microsoft Teams

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
   token under **Telegram**, and select **Save**. IntelliKnow stores it in the
   configured secret manager (macOS Keychain for the laptop demo).

5. If Telegram requires your organization's proxy, add the proxy variables used by your network. Example:

   ```dotenv
   ALL_PROXY=socks5://127.0.0.1:8119
   HTTPS_PROXY=socks5://127.0.0.1:8119
   HTTP_PROXY=socks5://127.0.0.1:8119
   ```

6. In `config.yaml`, confirm `channels.telegram.enabled` is `true` and `mode` is `polling`.
7. Restart IntelliKnow.
8. Open the bot in Telegram, select **Start**, and send a question whose answer is in an uploaded document.
9. Confirm the answer includes a source.

Do not email, paste into chat, or commit the bot token to Git. If it is exposed, use BotFather's `/revoke` command and replace it in `.env`.

By default, anyone who discovers the bot can send it a private message. Use only non-sensitive demo documents until IntelliKnow has an approved user allowlist or another access-control layer.

## For administrators: connect Microsoft Teams

This setup normally requires help from a Microsoft 365 or Azure administrator. You need an Azure subscription or approved Azure Bot resource, permission to upload or publish a Teams app, and a public HTTPS address for IntelliKnow.

1. Create or choose the Microsoft Entra application used by the bot.
2. Record its **Application (client) ID**.
3. Create a client secret and copy its **Value** immediately. Do not use the secret's identifier.
4. Create or configure an [Azure Bot resource](https://learn.microsoft.com/en-us/azure/bot-service/bot-service-quickstart-registration?view=azure-bot-service-4.0) for that application.
5. Enable the **Microsoft Teams** channel on the Azure Bot resource.
6. Publish IntelliKnow at a public HTTPS address. Its messaging endpoint is:

   ```text
   https://your-intelliknow-address/api/messages
   ```

7. Enter that full address as the bot's messaging endpoint in Azure.
8. In IntelliKnow's **Frontend Integration** page, enter the application ID and
   client-secret value under **Microsoft Teams**, then select **Save**. Do not
   enter the secret ID.

9. In `config.yaml`, set `channels.teams.enabled` to `true` and set `public_base_url` to the public HTTPS base address.
10. Restart IntelliKnow.
11. In the [Teams Developer Portal](https://dev.teams.microsoft.com/), create or update an app package that contains the bot's application ID and supports personal chat.
12. Publish the app to your organization or use **Preview in Teams** if your tenant permits it.
13. Open IntelliKnow in Teams and send a question whose answer is in an uploaded document.
14. Confirm the answer includes a source.

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
- Review cited documents for HR, legal, compliance, and financial decisions.
- Remove outdated documents from IntelliKnow promptly.

## When setup fails

**Telegram does not answer:** confirm the token, proxy, and `enabled` setting; ensure no Telegram webhook is configured because polling and webhooks cannot run together.

**Teams reports unauthorized:** confirm the application ID, client-secret value, Azure Bot registration, and messaging endpoint. A secret identifier is not a secret value.

**Teams cannot reach IntelliKnow:** the endpoint must use public HTTPS with a valid certificate. `localhost` works only with the Emulator on the IntelliKnow computer.

**The answer has no expected information:** confirm the document is uploaded and indexed, then test a question using the exact wording in the document.
