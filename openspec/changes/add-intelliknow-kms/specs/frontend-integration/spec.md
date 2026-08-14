## Purpose

Connects the KMS to the chat tools people already use — Telegram, WhatsApp, and Microsoft Teams — by receiving messages over each platform's protocol, normalizing them for the orchestrator, delivering answers in each platform's native format, and giving admins connection status, error logging, and a test that proves the whole path works.

## ADDED Requirements

### Requirement: Two chat channel integrations

The system SHALL provide message integrations for Telegram, WhatsApp, and Microsoft Teams. Telegram and WhatsApp SHALL form the two demonstrated real-user frontends; Teams SHALL remain independently configurable with local adapter verification.

#### Scenario: Telegram question answered

- **WHEN** a user sends a question to the configured Telegram bot
- **THEN** the question is processed through the query pipeline
- **AND** the answer is delivered back in the same Telegram conversation

#### Scenario: Teams question answered

- **WHEN** a user sends a question to the configured Teams bot
- **THEN** the question is processed through the query pipeline
- **AND** the answer is delivered back in the same Teams conversation

#### Scenario: WhatsApp question answered

- **WHEN** a user sends a text question to the configured WhatsApp business sender
- **THEN** the signed webhook payload is processed through the query pipeline
- **AND** the answer is delivered back to the same WhatsApp user

#### Scenario: One channel unconfigured does not affect the other

- **WHEN** only one channel has credentials configured
- **THEN** that channel continues to serve queries normally
- **AND** the other reports Disconnected without affecting it

### Requirement: Response latency

The system SHALL deliver a response to the originating channel within 3 seconds of receiving a question under normal operation, and SHALL record the measured end-to-end latency for every query.

#### Scenario: Round trip within budget

- **WHEN** a user asks a question on either channel
- **THEN** the answer is delivered within 3 seconds under normal operation

#### Scenario: Latency recorded

- **WHEN** any query completes
- **THEN** its measured end-to-end latency is recorded and reportable

### Requirement: Normalized inbound message

The system SHALL translate each platform's inbound payload into a common internal message carrying the channel name, an identifier for the asking user, the message text, and the reference needed to reply, so that no downstream component handles platform-specific payloads.

#### Scenario: Platform payload normalized

- **WHEN** a message arrives from either platform
- **THEN** it is converted into the common internal message shape before reaching the orchestrator

#### Scenario: Non-text message handled gracefully

- **WHEN** an inbound message contains no usable text, such as an image or sticker
- **THEN** the user receives a short message explaining that only text questions are supported
- **AND** no query pipeline run is started

### Requirement: Telegram long-polling

The system SHALL operate the Telegram integration in long-polling mode, requiring no publicly reachable URL.

#### Scenario: Polling mode needs no public URL

- **WHEN** Telegram is configured in the default polling mode
- **THEN** inbound messages are retrieved and answered
- **AND** no public base URL or webhook registration is required

#### Scenario: Polling offset prevents duplicate processing

- **WHEN** a Telegram update has been handled
- **THEN** the next polling request advances beyond that update
- **AND** the message is not processed twice

### Requirement: Teams Bot Framework integration

The system SHALL expose a Bot Framework messaging endpoint for Microsoft Teams, SHALL rely on the Bot Framework SDK to authenticate inbound activities, and SHALL support local adapter verification with the Bot Framework Emulator.

#### Scenario: Activity received and answered

- **WHEN** a Bot Framework activity carrying a question arrives at the messaging endpoint
- **THEN** the question is processed and the answer is returned to the same conversation

#### Scenario: Local operation without a tenant

- **WHEN** the Bot Framework Emulator is pointed at the messaging endpoint
- **THEN** questions can be asked and answered without any Azure or Microsoft 365 tenant

#### Scenario: Real Teams delivery acceptance

- **WHEN** project acceptance evidence is reviewed
- **THEN** Emulator success is identified as local adapter verification only
- **AND** real Teams tenant delivery is not claimed unless a target-tenant round trip has actually completed

#### Scenario: Unauthenticated activity rejected

- **WHEN** an activity fails Bot Framework authentication
- **THEN** it is rejected
- **AND** no query is processed

### Requirement: Admin credential configuration

The system SHALL allow an admin to enter, update, and clear each channel's credentials from the console — a bot token for Telegram; an access token, Phone-number ID, app secret, and verify token for WhatsApp; and an application identifier, password, and directory tenant identifier for Teams — and SHALL apply saved credentials without a service restart.

#### Scenario: Telegram credentials configured from the console

- **WHEN** an admin saves a Telegram bot token
- **THEN** the channel becomes usable without a restart

#### Scenario: Teams credentials configured from the console

- **WHEN** an admin saves a Teams application identifier, password, and directory tenant identifier
- **THEN** the channel becomes usable without a restart

#### Scenario: WhatsApp credentials configured from the console

- **WHEN** an admin saves a WhatsApp access token, Phone-number ID, app secret, and verify token
- **THEN** the webhook verifies signed callbacks and the channel becomes usable without a restart

### Requirement: WhatsApp Cloud API integration

The system SHALL expose a WhatsApp Cloud API webhook, verify Meta's challenge token, authenticate POST bodies with the app-secret HMAC, acknowledge callbacks promptly, ignore status-only events, and deliver text replies through the configured Phone-number ID.

#### Scenario: Signed WhatsApp text received

- **WHEN** Meta sends a valid signed text-message callback
- **THEN** the callback is acknowledged and the normalized question reaches the shared handler

#### Scenario: Invalid WhatsApp signature rejected

- **WHEN** a callback's `X-Hub-Signature-256` does not match the raw body
- **THEN** the callback is rejected
- **AND** no query is processed

### Requirement: Measured channel acceptance

The system SHALL provide a repeatable real-channel acceptance command that
measures from accepted inbound question through completed platform send and
fails when p95 exceeds three seconds.

#### Scenario: Teams Emulator cannot pass real acceptance

- **WHEN** Teams acceptance is run with real-platform verification enabled
- **AND** the stored conversation reference came from a loopback Emulator
- **THEN** the acceptance command fails and identifies the destination as local

#### Scenario: Demonstrated channels pass real acceptance

- **WHEN** Telegram and WhatsApp representative questions complete through their real platforms
- **THEN** each result records full delivery latency and platform mode

#### Scenario: Real channel latency passes

- **WHEN** representative questions complete through the real platform
- **AND** p95 end-to-end latency is at most 3000 milliseconds
- **THEN** the acceptance command exits successfully and reports p50, p95, and maximum latency

#### Scenario: Credentials cleared

- **WHEN** an admin clears a channel's credentials
- **THEN** the stored values are removed
- **AND** the channel reports Disconnected

#### Scenario: Channel enabled state configurable

- **WHEN** an admin disables a channel
- **THEN** inbound messages on that channel are not processed
- **AND** the stored credentials are retained for later re-enabling

### Requirement: Secure credential storage

The system SHALL encrypt chat platform credentials with Fernet before storing
them in SQLite, SHALL keep the encryption key outside the database, and SHALL
never return a usable credential through the API or console.

#### Scenario: Credential encrypted at rest

- **WHEN** an admin saves a credential
- **THEN** SQLite stores Fernet ciphertext rather than the plaintext value
- **AND** the encryption key remains in the process environment or private `.env`

#### Scenario: Credential returned masked

- **WHEN** the API returns a channel's configuration
- **THEN** only the last four characters of the credential are included
- **AND** the full value is never sent to the console

#### Scenario: Encryption configuration fails closed

- **WHEN** the credential-encryption key is missing or invalid
- **THEN** startup fails with a clear configuration error
- **AND** no plaintext fallback is written to SQLite, configuration, or logs

#### Scenario: Secret value never persisted in configuration

- **WHEN** credentials are stored
- **THEN** the plaintext credential value does not appear in the database or configuration file

#### Scenario: Credential cannot be decrypted

- **WHEN** stored ciphertext does not match the configured encryption key
- **THEN** the channel reports Disconnected with a sanitized decryption error
- **AND** the service continues running

### Requirement: Channel-appropriate outbound formatting

The query pipeline SHALL format each answer exactly once for its destination channel, respecting that channel's message length limit and markup syntax, and SHALL escape characters reserved in that channel's markup. Channel adapters SHALL deliver the returned text without a second formatting pass.

#### Scenario: Telegram limit respected

- **WHEN** an answer is delivered to Telegram
- **THEN** the message is within Telegram's maximum message length
- **AND** reserved markup characters are escaped

#### Scenario: Teams formatting applied

- **WHEN** an answer is delivered to Teams
- **THEN** the message uses formatting Teams renders correctly, including bullet lists for enumerated content

#### Scenario: Citations rendered per channel

- **WHEN** an answer carries citations
- **THEN** they are rendered in a form appropriate to the destination channel

#### Scenario: Telegram markup escaped once

- **WHEN** the query pipeline returns Telegram-safe text
- **THEN** the Telegram adapter sends that text without escaping it again

### Requirement: Delivery acknowledgement during processing

The system SHALL send the destination channel's typing or activity indicator before beginning model calls, so users see immediate acknowledgement while the answer is produced.

#### Scenario: Indicator sent before model calls

- **WHEN** an inbound question is accepted
- **THEN** a typing indicator is sent to the originating channel before classification begins

#### Scenario: Indicator failure does not block the answer

- **WHEN** sending the typing indicator fails
- **THEN** query processing continues and the answer is still delivered

### Requirement: Connection status monitoring

The system SHALL maintain a per-channel status of Connected or Disconnected, along with the time of the last successful exchange and the most recent error, and SHALL expose them through the admin API.

#### Scenario: Status becomes Connected after a successful exchange

- **WHEN** a channel successfully receives and answers a message
- **THEN** its status is Connected and its last-success time is updated

#### Scenario: Status becomes Disconnected on failure

- **WHEN** a channel fails to receive or deliver a message
- **THEN** its status becomes Disconnected
- **AND** the failure reason is retained for display

#### Scenario: Status visible without sending a message

- **WHEN** an admin opens the integration screen
- **THEN** each channel's current status, last success time, and last error are shown

### Requirement: Channel error logging

The system SHALL record integration errors — authentication failures, delivery failures, and platform API errors — with the channel, timestamp, and reason, and SHALL make the most recent errors visible to the admin.

#### Scenario: Delivery failure recorded

- **WHEN** an outbound message fails to send
- **THEN** the error is recorded with the channel, timestamp, and reason

#### Scenario: Recent errors visible

- **WHEN** an admin views a channel with recorded errors
- **THEN** the most recent errors are shown

### Requirement: End-to-end integration test

The system SHALL remember the most recent successful reply destination for each channel and SHALL provide an admin-triggered test that sends a sample query through the full pipeline to that destination. It SHALL report the outcome, failing stage, and measured latency through completion of delivery.

#### Scenario: Test passes

- **WHEN** an admin runs the test for a configured channel
- **THEN** a sample question is processed through classification, retrieval, generation, and delivery
- **AND** the result reports success with the measured latency

#### Scenario: Test has no delivery destination

- **WHEN** an admin runs a channel test before any user has messaged that bot
- **THEN** the test reports that a real user must message the bot first
- **AND** it does not report a delivery success

#### Scenario: Test fails on invalid credentials

- **WHEN** the platform rejects the channel's credentials
- **THEN** the result reports failure naming the credential problem
- **AND** the channel status becomes Disconnected

#### Scenario: Test identifies the failing stage

- **WHEN** the test fails partway through
- **THEN** the reported result names the stage that failed

### Requirement: Inbound error isolation

The system SHALL catch any failure during inbound message processing, SHALL acknowledge the platform so it does not retry indefinitely, and SHALL deliver a user-facing error message rather than leaving the user without a reply.

#### Scenario: Pipeline failure produces a user-facing message

- **WHEN** query processing raises an unexpected error
- **THEN** the user receives a short message stating the question could not be answered
- **AND** the error is recorded

#### Scenario: Platform receives acknowledgement

- **WHEN** processing fails
- **THEN** the platform still receives a success acknowledgement
- **AND** does not enter a retry loop
