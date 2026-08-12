## Purpose

Connects the KMS to the chat tools people already use — Telegram and Microsoft Teams — by receiving messages over each platform's protocol, normalizing them for the orchestrator, delivering answers in each platform's native format, and giving admins a way to configure credentials, watch connection health, and prove the whole path works.

## ADDED Requirements

### Requirement: Two chat channel integrations

The system SHALL provide message integrations for Telegram and Microsoft Teams, each able to receive user questions and deliver answers independently of the other.

#### Scenario: Telegram question answered

- **WHEN** a user sends a question to the configured Telegram bot
- **THEN** the question is processed through the query pipeline
- **AND** the answer is delivered back in the same Telegram conversation

#### Scenario: Teams question answered

- **WHEN** a user sends a question to the configured Teams bot
- **THEN** the question is processed through the query pipeline
- **AND** the answer is delivered back in the same Teams conversation

#### Scenario: One channel unconfigured does not affect the other

- **WHEN** only one channel has valid credentials configured
- **THEN** that channel continues to serve queries normally
- **AND** the unconfigured channel reports status `unconfigured` without affecting the other

### Requirement: Normalized inbound message

The system SHALL translate each platform's inbound payload into a common internal message carrying the channel name, an external user identifier, the message text, and the conversation reference needed to reply, so that no downstream component handles platform-specific payloads.

#### Scenario: Platform payload normalized

- **WHEN** a message arrives from either platform
- **THEN** it is converted into the common internal message shape before reaching the orchestrator

#### Scenario: Non-text message ignored gracefully

- **WHEN** an inbound message contains no usable text, such as an image or sticker
- **THEN** the user receives a short message explaining that only text questions are supported
- **AND** no query pipeline run is started

### Requirement: Inbound request authentication

The system SHALL verify the authenticity of every inbound webhook request — Telegram via its configured secret token header, Teams via Bot Framework JWT validation — and SHALL reject unverified requests.

#### Scenario: Valid Telegram request accepted

- **WHEN** a Telegram webhook request carries the correct secret token header
- **THEN** the request is processed

#### Scenario: Invalid Telegram secret rejected

- **WHEN** a Telegram webhook request carries a missing or incorrect secret token
- **THEN** the request is rejected with an authorization error
- **AND** no query is processed

#### Scenario: Teams JWT validated

- **WHEN** a Teams request arrives with a valid Bot Framework token
- **THEN** the request is processed

#### Scenario: Invalid Teams token rejected

- **WHEN** a Teams request carries an invalid or expired token
- **THEN** the request is rejected with an authorization error

### Requirement: Encrypted credential storage

The system SHALL store chat platform credentials encrypted at rest using a key supplied by environment variable, SHALL never return credential values in plaintext through the API, and SHALL fail startup when the encryption key is missing or invalid.

#### Scenario: Credentials encrypted on save

- **WHEN** an admin saves a bot token
- **THEN** the value is encrypted before being persisted
- **AND** the stored value is not readable without the encryption key

#### Scenario: Credentials masked on read

- **WHEN** the admin console displays configured credentials
- **THEN** only a masked form showing the last few characters is returned
- **AND** the full value is never sent to the console

#### Scenario: Missing encryption key blocks startup

- **WHEN** the service starts without a valid credential encryption key
- **THEN** startup fails with an error naming the missing configuration
- **AND** credentials are never stored unencrypted as a fallback

### Requirement: Admin credential configuration

The system SHALL allow an admin to enter, update, and clear the credentials for each channel — a bot token for Telegram, an application identifier and password for Teams — and to enable or disable each channel independently.

#### Scenario: Telegram credentials configured

- **WHEN** an admin saves a Telegram bot token
- **THEN** the channel becomes usable without a service restart

#### Scenario: Teams credentials configured

- **WHEN** an admin saves a Teams application identifier and password
- **THEN** the channel becomes usable without a service restart

#### Scenario: Channel disabled

- **WHEN** an admin disables a channel
- **THEN** inbound messages on that channel are rejected
- **AND** the stored credentials are retained for later re-enabling

### Requirement: Telegram webhook registration

The system SHALL register its public webhook URL with Telegram when credentials and a public base URL are configured, and SHALL re-register on startup so that a changed tunnel URL does not silently break the integration.

#### Scenario: Webhook registered on configuration

- **WHEN** an admin saves a Telegram token together with a public base URL
- **THEN** the webhook is registered with Telegram
- **AND** the outcome is reported to the admin

#### Scenario: Webhook re-registered on startup

- **WHEN** the service starts with Telegram configured and a public base URL set
- **THEN** the webhook registration is refreshed

#### Scenario: Registration failure surfaced

- **WHEN** webhook registration fails
- **THEN** the channel status becomes `error` with the reported reason
- **AND** the admin sees the failure in the console

### Requirement: Telegram polling mode

The system SHALL support a configurable polling mode for Telegram that retrieves messages without requiring a public URL, so the channel can be demonstrated without a tunnel.

#### Scenario: Polling mode receives messages

- **WHEN** Telegram is configured in polling mode
- **THEN** inbound messages are retrieved and answered without any webhook registration

#### Scenario: Only one mode active

- **WHEN** polling mode is enabled
- **THEN** no Telegram webhook is registered
- **AND** messages are not processed twice

### Requirement: Channel-appropriate outbound formatting

The system SHALL format each answer for its destination channel, respecting that channel's message length limit, markup syntax, and list rendering, and SHALL escape characters that are reserved in that channel's markup.

#### Scenario: Telegram limit respected

- **WHEN** an answer is delivered to Telegram
- **THEN** the message is within Telegram's maximum message length
- **AND** any reserved markup characters are escaped

#### Scenario: Teams formatting applied

- **WHEN** an answer is delivered to Teams
- **THEN** the message uses formatting that Teams renders correctly, including bullet lists for enumerated content

#### Scenario: Citations rendered per channel

- **WHEN** an answer carries citations
- **THEN** the citations are rendered in a form appropriate to the destination channel

### Requirement: Delivery acknowledgement during processing

The system SHALL send the destination channel's typing or activity indicator before beginning model calls, so users see immediate acknowledgement while the answer is being produced.

#### Scenario: Indicator sent before model calls

- **WHEN** an inbound question is accepted
- **THEN** a typing indicator is sent to the originating channel before classification begins

#### Scenario: Indicator failure does not block the answer

- **WHEN** sending the typing indicator fails
- **THEN** query processing continues
- **AND** the answer is still delivered

### Requirement: Connection status monitoring

The system SHALL maintain a per-channel status of `unconfigured`, `ok`, or `error`, along with the time of the last successful exchange and the most recent error message, and SHALL expose them through the admin API.

#### Scenario: Status becomes ok after a successful exchange

- **WHEN** a channel successfully receives and answers a message
- **THEN** its status is `ok` and its last-success timestamp is updated

#### Scenario: Status becomes error on failure

- **WHEN** a channel fails to deliver a message
- **THEN** its status becomes `error`
- **AND** the failure reason is stored and displayed to the admin

#### Scenario: Status visible without sending a message

- **WHEN** an admin opens the integrations screen
- **THEN** each channel's current status, last success time, and last error are shown

### Requirement: End-to-end integration test

The system SHALL provide an admin-triggered test per channel that exercises credential validity, the full query pipeline, and outbound delivery, and SHALL report the outcome together with the measured round-trip latency.

#### Scenario: Test passes

- **WHEN** an admin runs the test for a configured channel
- **THEN** a test question is processed through classification, retrieval, generation, and delivery
- **AND** the result reports success with the measured latency in milliseconds

#### Scenario: Test fails on invalid credentials

- **WHEN** an admin runs the test for a channel whose credentials are rejected by the platform
- **THEN** the result reports failure naming the credential problem
- **AND** the channel status becomes `error`

#### Scenario: Test identifies the failing stage

- **WHEN** the test fails partway through
- **THEN** the reported result names the stage that failed

### Requirement: Inbound error isolation

The system SHALL catch any failure during inbound message processing, SHALL return a success acknowledgement to the platform so it does not retry indefinitely, and SHALL deliver a user-facing error message rather than leaving the user without a reply.

#### Scenario: Pipeline failure produces a user-facing message

- **WHEN** query processing raises an unexpected error
- **THEN** the user receives a short message stating that the question could not be answered
- **AND** the error is recorded

#### Scenario: Platform receives acknowledgement

- **WHEN** processing fails
- **THEN** the webhook still returns a success acknowledgement to the platform
- **AND** the platform does not enter a retry loop
