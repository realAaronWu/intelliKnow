## Purpose

Decides which knowledge domain answers each incoming question by classifying it into exactly one intent space with a confidence score, then enforcing the configured threshold to either hard-filter retrieval to that space or fall back to searching everything.

## ADDED Requirements

### Requirement: Intent space centroids

The system SHALL maintain one centroid vector per intent space, computed by embedding that space's name, description, and keywords, and SHALL rebuild a centroid whenever its space's configuration changes.

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

#### Scenario: Escalated result also below threshold

- **WHEN** the LLM's returned confidence is also below the threshold
- **THEN** the query is routed to the fallback space

#### Scenario: Escalation disabled

- **WHEN** escalation is disabled in configuration and centroid confidence is below the threshold
- **THEN** the query is routed to the fallback space
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
- **THEN** retrieval searches every intent space
- **AND** the query is logged against the fallback space with the fallback flag true

#### Scenario: Confidence exactly at the threshold

- **WHEN** classification confidence equals the configured threshold exactly
- **THEN** the classified space is used and the fallback is not triggered

#### Scenario: Threshold change takes effect immediately

- **WHEN** an admin raises the threshold and a new query arrives
- **THEN** the new query is evaluated against the updated threshold

### Requirement: Fallback space searches all spaces

The system SHALL treat a classification result of the fallback space as a request to search every intent space, regardless of the confidence value.

#### Scenario: Confidently classified as General

- **WHEN** classification returns the General space with high confidence
- **THEN** retrieval searches every intent space
- **AND** the query is logged against General with the fallback flag true

### Requirement: Classification failure falls back rather than failing

The system SHALL route a query to the fallback space when an escalation call fails or times out, rather than returning an error to the user.

#### Scenario: Provider error during escalation

- **WHEN** the LLM provider raises an error during escalation
- **THEN** the query is routed to the fallback space and answered
- **AND** the query log records the classification failure

#### Scenario: Escalation timeout

- **WHEN** the escalation call exceeds its configured timeout
- **THEN** the query proceeds through the fallback
- **AND** the user still receives an answer

### Requirement: Routing hand-off to retrieval

The system SHALL pass retrieval an explicit list of intent spaces to search — a single space when the threshold is met, or all spaces on fallback — so that retrieval never makes the routing decision itself.

#### Scenario: Single-space routing

- **WHEN** the confidence threshold is met for the HR space
- **THEN** retrieval receives a list containing only the HR space

#### Scenario: Fallback routing

- **WHEN** the fallback is triggered
- **THEN** retrieval receives a list containing every configured intent space

### Requirement: Per-query routing record

The system SHALL record, for every query, the classified intent space, the confidence value, which mechanism produced the classification, the reasoning when an LLM produced it, and whether the fallback was used.

#### Scenario: Fast-path query records its mechanism

- **WHEN** a query is classified by centroid alone
- **THEN** its log entry records the mechanism as centroid
- **AND** no reasoning string is present

#### Scenario: Escalated query records reasoning

- **WHEN** a query is escalated to the LLM
- **THEN** its log entry records the mechanism as LLM
- **AND** the reasoning string returned by the model is stored

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
