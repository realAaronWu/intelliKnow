## Purpose

Isolates every call to an AI backend behind two narrow interfaces so that answer generation, intent classification, table restructuring, and embedding generation can each be pointed at Anthropic, OpenAI, or a local model through configuration alone, without changing any calling code.

## ADDED Requirements

### Requirement: Text generation interface

The system SHALL expose an `LLMProvider` interface with a single `complete` operation accepting a system prompt, a user prompt, an optional JSON schema, and a maximum token count, and returning generated text together with the model identifier and token usage.

#### Scenario: Free-form generation

- **WHEN** `complete` is called without a schema
- **THEN** the provider returns the generated text, the model identifier that produced it, and input and output token counts

#### Scenario: Structured generation

- **WHEN** `complete` is called with a JSON schema
- **THEN** the provider returns a parsed object conforming to that schema
- **AND** the caller receives a typed result rather than a raw string

#### Scenario: Structured generation returns malformed output

- **WHEN** a provider returns content that does not validate against the requested schema
- **THEN** the provider retries the call once
- **AND** if the retry also fails validation a `ProviderError` is raised naming the schema that was violated

### Requirement: Embedding interface

The system SHALL expose an `EmbeddingProvider` interface with an `embed` operation accepting a list of texts and returning one vector per input text in the same order, and a `dimension` property reporting the vector length.

#### Scenario: Batch embedding preserves order

- **WHEN** `embed` is called with N texts
- **THEN** exactly N vectors are returned
- **AND** the vector at index i corresponds to the text at index i

#### Scenario: Reported dimension matches produced vectors

- **WHEN** `embed` returns vectors
- **THEN** every vector has a length equal to the provider's `dimension` property

#### Scenario: Vectors normalized for cosine comparison

- **WHEN** embeddings are produced for indexing or querying
- **THEN** they are unit-normalized so that inner product equals cosine similarity

### Requirement: Backend selection from configuration

The system SHALL select provider implementations from the configuration file, supporting Anthropic, OpenAI, and a local backend for generation, and a local or OpenAI backend for embeddings, with separately configured models for classification and generation.

#### Scenario: Provider chosen from configuration

- **WHEN** the configured LLM provider is Anthropic
- **THEN** all generation and classification calls route to the Anthropic implementation
- **AND** no other backend is contacted

#### Scenario: Separate classification and generation models

- **WHEN** the classification model and generation model are configured differently
- **THEN** classification calls use the classification model
- **AND** answer generation calls use the generation model

#### Scenario: Unknown provider name

- **WHEN** the configured provider name matches no implementation
- **THEN** the service refuses to start
- **AND** the error names the invalid value and lists the supported values

### Requirement: Startup credential validation

The system SHALL verify at startup that the credentials required by the selected providers are present, and SHALL fail with an actionable message when they are not.

#### Scenario: Missing API key for a remote provider

- **WHEN** the service starts with a remote provider selected and no corresponding API key set
- **THEN** startup fails
- **AND** the error names the missing environment variable

#### Scenario: Local providers need no key

- **WHEN** both the generation and embedding providers are local implementations
- **THEN** the service starts with no API key configured

### Requirement: Timeout, retry, and error normalization

The system SHALL apply the configured per-call timeout, SHALL retry transient failures with exponential backoff up to the configured maximum, and SHALL translate every backend-specific failure into a common `ProviderError` carrying a category of `timeout`, `rate_limit`, `auth`, or `backend`.

#### Scenario: Transient failure is retried

- **WHEN** a provider call fails with a rate-limit or server error
- **THEN** the call is retried with exponential backoff
- **AND** a subsequent success is returned as a normal result

#### Scenario: Retries exhausted

- **WHEN** every retry attempt fails
- **THEN** a `ProviderError` is raised carrying the failure category
- **AND** the caller can distinguish an authentication failure from a timeout without knowing which backend was in use

#### Scenario: Call exceeds the timeout

- **WHEN** a provider call does not return within the configured timeout
- **THEN** the call is aborted and a `ProviderError` with category `timeout` is raised

### Requirement: Provider status reporting

The system SHALL expose the active provider names, the configured classification and generation models, the embedding model, and the embedding dimension through the admin API, without disclosing any credential value.

#### Scenario: Console reads provider status

- **WHEN** the console requests provider status
- **THEN** the active providers, both models, the embedding model, and the embedding dimension are returned
- **AND** no API key or secret value appears in the response
