## Purpose

Decides which knowledge domain answers each incoming question by classifying it into exactly one intent space with a confidence score, then enforcing the configured threshold before retrieval. A valid but uncertain classification falls back to General; an unavailable or invalid classifier stops with a retryable error.

## ADDED Requirements

### Requirement: Intent space centroids

The system SHALL maintain one centroid vector per intent space, computed as the normalized mean of that space's definition embedding and bounded recent admin-reviewed example embeddings, and SHALL rebuild centroids whenever those inputs change.

#### Scenario: Centroids available on an empty knowledge base

- **WHEN** the service starts with intent spaces configured and no documents indexed
- **THEN** every space has a centroid
- **AND** classification functions

#### Scenario: Editing keywords rebuilds the centroid

- **WHEN** an admin adds a keyword to a space and saves
- **THEN** that space's centroid is recomputed
- **AND** the next query is classified against the updated centroid without a restart

#### Scenario: Adding a space adds a centroid

- **WHEN** a new intent space is created
- **THEN** a centroid is computed for it and it becomes a classification target

#### Scenario: Centroids use the configured embedding model

- **WHEN** centroids are computed
- **THEN** they are produced by the same embedding provider used for chunks and queries

#### Scenario: Reviewed examples change centroids

- **WHEN** an admin records or corrects an expected intent
- **THEN** the next query rebuilds the affected classifier inputs from the bounded review set
- **AND** no document re-indexing is required

### Requirement: Centroid-based classification

The system SHALL classify a question by comparing its embedding against every intent space centroid and converting the similarities into a probability distribution using a temperature-scaled softmax, taking the highest-probability space as the classification and its probability as the confidence.

#### Scenario: Question classified without an LLM call

- **WHEN** a question's top centroid probability meets or exceeds the confidence threshold
- **THEN** that space is used
- **AND** no LLM call is made

#### Scenario: Confidence is a distribution

- **WHEN** a question is classified
- **THEN** the per-space probabilities sum to 1
- **AND** the reported confidence is the highest of them

#### Scenario: Query embedding is reused

- **WHEN** a question is classified
- **THEN** the embedding computed for retrieval is reused
- **AND** no additional embedding call is made for classification

#### Scenario: Temperature is configurable

- **WHEN** an operator changes the softmax temperature
- **THEN** subsequent classifications use the new value without a restart

### Requirement: Escalation to LLM classification

The system SHALL escalate to an LLM classification call when centroid confidence falls below the configured threshold and escalation is enabled, and SHALL apply the threshold to the LLM's returned confidence.

#### Scenario: Low centroid confidence escalates

- **WHEN** the top centroid probability is below the threshold
- **THEN** an LLM classification call is made
- **AND** its result supersedes the centroid result

#### Scenario: Escalation prompt carries space configuration

- **WHEN** an escalation call is made
- **THEN** the prompt includes every intent space's name, description, and keywords as currently configured
- **AND** it includes the bounded valid admin-reviewed examples when any exist

#### Scenario: Escalated result also below threshold

- **WHEN** the LLM's returned confidence is also below the threshold
- **THEN** the query falls back to the configured General space
- **AND** retrieval is restricted to General
- **AND** the query is logged with the fallback flag true

#### Scenario: Escalation disabled

- **WHEN** escalation is disabled in configuration and centroid confidence is below the threshold
- **THEN** the query falls back to the configured General space
- **AND** no LLM call is made

#### Scenario: Escalation uses the classification model

- **WHEN** an escalation call is made
- **THEN** it uses the configured classification model rather than the generation model

### Requirement: Confidence threshold enforcement

The system SHALL compare the classification confidence against the configured threshold and SHALL restrict retrieval to the classified intent space only when the confidence meets or exceeds it.

#### Scenario: Confidence at or above threshold

- **WHEN** classification returns the Finance space with confidence 0.91 and the threshold is 0.70
- **THEN** retrieval searches only the Finance space
- **AND** the query is logged with the fallback flag false

#### Scenario: Confidence below threshold after escalation

- **WHEN** both centroid and escalated confidence fall below the threshold
- **THEN** retrieval searches only the configured General space
- **AND** the query is logged against General with the fallback flag true

#### Scenario: Confidence exactly at the threshold

- **WHEN** classification confidence equals the configured threshold exactly
- **THEN** the classified space is used and the fallback is not triggered

#### Scenario: Threshold change takes effect immediately

- **WHEN** an admin raises the threshold and a new query arrives
- **THEN** the new query is evaluated against the updated threshold

### Requirement: General is the uncertainty fallback

The system SHALL search General when a valid classification remains below the configured confidence threshold. It SHALL search only General, not every intent space, so uncertain routing cannot expose unrelated specialist documents. Provider failures, malformed responses, and invalid intent slugs SHALL NOT trigger fallback.

#### Scenario: Uncertain classification falls back

- **WHEN** a valid classification remains below the configured threshold
- **THEN** retrieval searches only General
- **AND** the original confidence is retained in the query log
- **AND** the fallback flag is true

#### Scenario: Confidently classified as General

- **WHEN** classification returns the General space with high confidence
- **THEN** retrieval searches only the General space
- **AND** the query is logged against General with the fallback flag false

### Requirement: Classification failure stops before retrieval

The system SHALL fail closed when classification fails, times out, or returns an invalid intent, and SHALL return a retryable error without retrieval or generation. A valid below-threshold result is uncertainty and SHALL use the General fallback instead.

#### Scenario: Provider error during escalation

- **WHEN** the LLM provider raises an error during escalation
- **THEN** the query returns a retryable classification error
- **AND** the query log records the failure as unclassified

#### Scenario: Escalation timeout

- **WHEN** the escalation call exceeds its configured timeout
- **THEN** the query stops before retrieval and generation
- **AND** the user receives a retryable error

### Requirement: Routing hand-off to retrieval

The system SHALL pass retrieval an explicit one-item list containing either the accepted classified space or the configured General fallback, so retrieval never makes or broadens the routing decision itself.

#### Scenario: Single-space routing

- **WHEN** the confidence threshold is met for the HR space
- **THEN** retrieval receives a list containing only the HR space

#### Scenario: No routing on classification failure

- **WHEN** classification fails because the provider is unavailable or its result is invalid
- **THEN** retrieval is not invoked

### Requirement: Per-query routing record

The system SHALL record, for every query, the classified intent space, the confidence value, which mechanism produced the classification, a concise selection summary when an LLM produced it, and whether the fallback was used. The classification response SHALL be bounded to the slug and confidence fields so audit prose does not consume the interactive latency budget.

#### Scenario: Fast-path query records its mechanism

- **WHEN** a query is classified by centroid alone
- **THEN** its log entry records the mechanism as centroid
- **AND** no reasoning string is present

#### Scenario: Escalated query records selection summary

- **WHEN** a query is escalated to the LLM
- **THEN** its log entry records the mechanism as LLM
- **AND** a concise summary of the model-selected slug and confidence is stored

#### Scenario: Reviewed exact match records its mechanism

- **WHEN** a query exactly matches a normalized admin-reviewed question
- **THEN** its log entry records the mechanism as review
- **AND** the expected intent is used without an LLM classification call

#### Scenario: Routing decision is auditable

- **WHEN** any query completes
- **THEN** an admin can determine from the log alone which space it went to, how confident the system was, which mechanism decided, and whether the fallback fired

### Requirement: Pipeline invocation without a chat channel

The system SHALL expose a single administrative operation that runs a question through classification and the full retrieval pipeline and returns the intent space, confidence, answer, sources, and latency without delivering to any chat channel.

#### Scenario: Admin test query

- **WHEN** an admin submits a question through the administrative test operation
- **THEN** the intent space, confidence, answer, sources, and measured latency are returned
- **AND** no message is sent to Telegram or Teams

#### Scenario: Channel test reuses the same path

- **WHEN** a per-channel integration test runs
- **THEN** it exercises the same classification and retrieval pipeline as a real query
