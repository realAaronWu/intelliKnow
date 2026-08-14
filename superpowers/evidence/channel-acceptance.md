# Channel Acceptance

This is the release check for the three-second response requirement and real
chat frontends. Unit tests and the Bot Framework Emulator remain useful, but
neither substitutes for user-visible delivery through a real platform.

## What the timer includes

The channel handler starts timing when it accepts the inbound question and
stops only after the platform send call completes. Query logs include routing,
retrieval, reranking, generation, typing, delivery, and total end-to-end stage
times. The typing acknowledgement is bounded to 400 ms and completes or cancels
before the platform client is used for answer delivery.

The release gate is 20 representative questions per channel with p95 end-to-end
latency at or below 3,000 ms. Use questions whose answers exist in indexed
documents so generation and citation work are included.

## Run the gate

First send the bot one message in the real Telegram, WhatsApp, or Teams app.
This records the destination used by the authenticated admin test. Then run:

```bash
.venv/bin/python scripts/check_channel_acceptance.py \
  --channel telegram \
  --questions-file superpowers/evidence/acceptance-questions.txt \
  --runs 4

.venv/bin/python scripts/check_channel_acceptance.py \
  --channel whatsapp \
  --require-real-platform \
  --questions-file superpowers/evidence/acceptance-questions.txt \
  --runs 4
```

Use `--channel teams` instead of `whatsapp` when a Microsoft 365 tenant is
available. Telegram plus WhatsApp satisfy the MVP's two-real-frontend gate;
Teams Emulator remains a local adapter demonstration only.

The runner prints every trial plus p50, p95, and maximum latency. It exits with
status 1 if delivery fails, p95 exceeds three seconds, or a Teams run uses a
local Emulator destination. The admin console's **Test** button also marks each
individual result as passing or failing the three-second target.

## Prepare real Teams

Real Teams acceptance requires all of the following:

- Microsoft 365 work or school tenant with custom app upload enabled;
- single-tenant Entra application and Azure Bot resource;
- application ID, client-secret value, and directory tenant ID;
- Microsoft Teams channel enabled on the Azure Bot;
- public, trusted HTTPS URL with `/api/messages` configured as the endpoint;
- IntelliKnow app package installed in the target tenant.

Build the package after the public URL and application ID are known:

```bash
.venv/bin/python scripts/build_teams_app.py \
  --app-id YOUR_APPLICATION_ID \
  --public-url https://your-public-host
```

Upload `dist/intelliknow-teams.zip` in Teams, message the bot, and run the Teams
acceptance command above. A service URL such as
`https://smba.trafficmanager.net/...` proves a real Teams destination; a
`localhost` service URL is reported as Local and cannot pass
`--require-real-platform`.

## Interpreting failures

- `credentials`: re-enter the token or all three Teams credential values.
- `destination`: send the bot a message from the target real chat first.
- `local platform`: the last Teams conversation came from the Emulator.
- `pipeline`: inspect the stage timings and provider error in Analytics.
- `delivery`: verify platform reachability, bot registration, and proxy.
- variable Telegram delivery: set `TELEGRAM_PROXY_URL` in `.env` to the tested
  proxy route so polling and tests do not depend on terminal exports.
- p95 above target: separate escalated classification from centroid queries;
  tune intent descriptions and reviewed labels before changing the safety
  threshold.
