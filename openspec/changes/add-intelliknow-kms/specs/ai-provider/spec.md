## Purpose

Isolates every call to an AI backend behind two narrow interfaces so that text generation, intent classification, and embedding generation can each be pointed at Anthropic, OpenAI, or a local model through configuration alone, without changing any calling code.

## ADDED Requirements

### Requirement: Text generation interface

The system SHALL expose an `LLMProvider` interface with a single `complete` operation accepting a system prompt, a user prompt, an optional JSON schema, and a maximum token count, and returning generated text together with the model identifier and token usage.

#### Scenario: Free-form generation

- **WHEN** `complete` is called without a schema
- **THEN** the provider returns the generated text, the model identifier that produced it, and input/output token counts

#### Scenario: Structured generation

- **WHEN** `complete` is called with a JSON schema
- **THEN** the provider returns a parsed object conforming to that schema
- **AND** the caller receives a typed result rather than a raw string

#### Scenario: Structured generation returns malformed output

- **WHEN** a provider returns content that does not validate against the requested schema
- **THEN** the provider retries the call once
- **AND** if the retry also fails validation the provider raises a `ProviderError` naming the schema that was violated

### Requirement: Embedding interface

The system SHALL expose an `EmbeddingProvider` interface with an `embed` operation accepting a list of texts and returning one vector per input text in the same order, and a `dimension` property reporting the vector length.

#### Scenario: Batch embedding preserves order

- **WHEN** `embed` is called with N texts
- **THEN** exactly N vectors are returned
- **AND** the vector at index i corresponds to the text at index i

#### Scenario: Reported dimension matches produced vectors

- **WHEN** `embed` returns vectors
- **THEN** every vector has a length equal to the provider's `dimension` property

### Requirement: Backend selection by configuration

The system SHALL select provider implementations from environment variables — `LLM_PROVIDER` (`anthropic`, `openai`, or `local`) and `EMBEDDING_PROVIDER` (`local`, `openai`) — and SHALL support distinct models for classification and generation via `LLM_MODEL_CLASSIFY` and `LLM_MODEL_GENERATE`.

#### Scenario: Provider chosen at startup

- **WHEN** the service starts with `LLM_PROVIDER=anthropic`
- **THEN** all generation and classification calls are routed to the Anthropic implementation
- **AND** no other backend is contacted

#### Scenario: Separate classification and generation models

- **WHEN** `LLM_MODEL_CLASSIFY` and `LLM_MODEL_GENERATE` are set to different models
- **THEN** classification calls use the classify model
- **AND** answer generation calls use the generate model

#### Scenario: Unknown provider name

- **WHEN** `LLM_PROVIDER` is set to a value that is not a supported implementation
- **THEN** the service refuses to start
- **AND** the error names the invalid value and lists the supported values

### Requirement: Startup credential validation

The system SHALL validate that the credentials required by the selected providers are present at startup and SHALL fail fast with an actionable message when they are not.

#### Scenario: Missing API key for a remote provider

- **WHEN** the service starts with a remote `LLM_PROVIDER` and no corresponding API key is set
- **THEN** the service refuses to start
- **AND** the error names the missing environment variable

#### Scenario: Local providers need no key

- **WHEN** both `LLM_PROVIDER` and `EMBEDDING_PROVIDER` are set to local implementations
- **THEN** the service starts with no API key configured

### Requirement: Timeout, retry, and error normalization

The system SHALL apply a configurable per-call timeout, SHALL retry transient failures with exponential backoff up to a configured maximum, and SHALL translate every backend-specific failure into a common `ProviderError` carrying a category of `timeout`, `rate_limit`, `auth`, or `backend`.

#### Scenario: Transient failure is retried

- **WHEN** a provider call fails with a rate-limit or server error
- **THEN** the call is retried with exponential backoff
- **AND** a subsequent success is returned to the caller as a normal result

#### Scenario: Retries exhausted

- **WHEN** every retry attempt fails
- **THEN** a `ProviderError` is raised carrying the failure category
- **AND** the caller can distinguish an authentication failure from a timeout without knowing which backend was in use

#### Scenario: Call exceeds the timeout

- **WHEN** a provider call does not return within the configured timeout
- **THEN** the call is aborted
- **AND** a `ProviderError` with category `timeout` is raised

### Requirement: Provider health reporting

The system SHALL expose the active provider names, the configured classification and generation models, the embedding model, and the embedding dimension through the admin API for display in the console.

#### Scenario: Console reads provider configuration

- **WHEN** the admin console requests provider status
- **THEN** the response names the active LLM provider, both configured models, the embedding provider, and the embedding dimension
- **AND** no API key or secret value appears in the response
