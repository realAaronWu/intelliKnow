## Purpose

Connects the KMS to the chat tools people already use — Telegram and Microsoft Teams — by receiving messages over each platform's protocol, normalizing them for the orchestrator, delivering answers in each platform's native format, and giving admins connection status, error logging, and a test that proves the whole path works.

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

### Requirement: Telegram long-polling by default

The system SHALL operate the Telegram integration in long-polling mode by default, requiring no publicly reachable URL, and SHALL support webhook mode as a configurable alternative.

#### Scenario: Polling mode needs no public URL

- **WHEN** Telegram is configured in the default polling mode
- **THEN** inbound messages are retrieved and answered
- **AND** no public base URL or webhook registration is required

#### Scenario: Webhook mode available

- **WHEN** an operator sets Telegram to webhook mode and supplies a public base URL
- **THEN** the webhook is registered with Telegram and inbound messages arrive over it

#### Scenario: Only one mode active

- **WHEN** polling mode is active
- **THEN** no webhook is registered
- **AND** messages are not processed twice

### Requirement: Teams Bot Framework integration

The system SHALL expose a Bot Framework messaging endpoint for Microsoft Teams, SHALL rely on the Bot Framework SDK to authenticate inbound activities, and SHALL be operable against the Bot Framework Emulator without a Microsoft 365 tenant.

#### Scenario: Activity received and answered

- **WHEN** a Bot Framework activity carrying a question arrives at the messaging endpoint
- **THEN** the question is processed and the answer is returned to the same conversation

#### Scenario: Local operation without a tenant

- **WHEN** the Bot Framework Emulator is pointed at the messaging endpoint
- **THEN** questions can be asked and answered without any Azure or Microsoft 365 tenant

#### Scenario: Unauthenticated activity rejected

- **WHEN** an activity fails Bot Framework authentication
- **THEN** it is rejected
- **AND** no query is processed

### Requirement: Admin credential configuration

The system SHALL allow an admin to enter, update, and clear each channel's credentials from the console — a bot token for Telegram, an application identifier and password for Teams — and SHALL apply saved credentials without a service restart.

#### Scenario: Telegram credentials configured from the console

- **WHEN** an admin saves a Telegram bot token
- **THEN** the channel becomes usable without a restart

#### Scenario: Teams credentials configured from the console

- **WHEN** an admin saves a Teams application identifier and password
- **THEN** the channel becomes usable without a restart

#### Scenario: Credentials cleared

- **WHEN** an admin clears a channel's credentials
- **THEN** the stored values are removed
- **AND** the channel reports Disconnected

#### Scenario: Channel enabled state configurable

- **WHEN** an admin disables a channel
- **THEN** inbound messages on that channel are not processed
- **AND** the stored credentials are retained for later re-enabling

### Requirement: Secure credential storage

The system SHALL store chat platform credentials encrypted at rest using a symmetric key supplied by environment variable, SHALL never return a credential value in plaintext through the API or the console, and SHALL fail startup when the encryption key is missing or invalid rather than falling back to plaintext.

#### Scenario: Credential encrypted on save

- **WHEN** an admin saves a credential
- **THEN** the value is encrypted before being persisted
- **AND** the persisted value is not readable without the encryption key

#### Scenario: Credential returned masked

- **WHEN** the API returns a channel's configuration
- **THEN** only the last four characters of the credential are included
- **AND** the full value is never sent to the console

#### Scenario: Missing encryption key blocks startup

- **WHEN** the service starts without a valid credential encryption key
- **THEN** startup fails with an error naming the missing environment variable
- **AND** no credential is stored or read unencrypted

#### Scenario: Encryption key never persisted

- **WHEN** credentials are stored
- **THEN** the encryption key is read from the environment only
- **AND** it does not appear in the database or the configuration file

#### Scenario: Undecryptable credential reported

- **WHEN** a stored credential cannot be decrypted with the current key
- **THEN** the channel reports Disconnected with a message stating the credential must be re-entered
- **AND** the service continues running

#### Scenario: Environment fallback for first run

- **WHEN** no credential is stored for a channel and a corresponding environment variable is set
- **THEN** that value is used and the console indicates it came from the environment

### Requirement: Channel-appropriate outbound formatting

The system SHALL format each answer for its destination channel, respecting that channel's message length limit and markup syntax, and SHALL escape characters reserved in that channel's markup.

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

The system SHALL provide an admin-triggered test per channel that sends a sample query through the full pipeline and delivers it to that channel, and SHALL report the outcome, the failing stage on failure, and the measured round-trip latency.

#### Scenario: Test passes

- **WHEN** an admin runs the test for a configured channel
- **THEN** a sample question is processed through classification, retrieval, generation, and delivery
- **AND** the result reports success with the measured latency

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
