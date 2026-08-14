# Channels Test Plan

## Automated

- Credential storage: valid/missing key, encrypted database value, masked output, clear, undecryptable value, environment fallback.
- Contracts: Telegram and Teams captured payloads normalize to the same message shape; non-text input bypasses the pipeline.
- Handler: typing precedes pipeline; pipeline runs once; formatted output is sent once without re-escaping; typing and analytics failures do not suppress delivery.
- Telegram: polling offsets prevent duplicate processing, API failures update status, and output remains within 4096 characters.
- Teams: Bot Framework authentication rejects invalid activity; valid captured activity replies to its conversation; output uses pipeline-provided formatting.
- Persistence: status, last success, recent error, and last reply reference survive a fresh store instance.
- Admin API: all integration routes require bearer authentication; credentials are never returned; test without a destination explains the prerequisite; test failures name their stage.
- Query history: one row is written after send completion with full end-to-end latency and exact `success`, `no_match`, or `failed` status.

Use fakes and captured platform payloads. Automated tests must not contact Telegram, Microsoft, or an AI provider.

## Manual Acceptance

- A real Telegram user asks a question and receives a cited answer.
- A real Teams user in the target tenant asks a question and receives a cited answer.
- The admin test action delivers to the last real conversation on each channel.
- Record latency from inbound acceptance through send completion. Emulator behavior is a local diagnostic, not Teams delivery acceptance.
