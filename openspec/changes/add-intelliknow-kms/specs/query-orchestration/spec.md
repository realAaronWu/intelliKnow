## Purpose

Decides which knowledge domain answers each incoming question by classifying it into exactly one intent space with a confidence score, then enforcing the confidence threshold to either hard-filter retrieval to that space or fall back to searching everything.

## ADDED Requirements

### Requirement: Intent classification

The system SHALL classify every inbound question into exactly one existing intent space using the LLM provider with a structured output schema returning an intent slug, a confidence value between 0.0 and 1.0, and a short reasoning string.

#### Scenario: Question classified into a space

- **WHEN** a user asks "how many vacation days do I get after 3 years"
- **THEN** the classifier returns an intent slug, a confidence value, and a reasoning string
- **AND** the slug names one of the currently configured intent spaces

#### Scenario: Classification considers admin descriptions

- **WHEN** classification runs
- **THEN** the prompt includes every intent space's name and admin-authored description
- **AND** editing a description changes subsequent classification behavior without a restart

#### Scenario: Model returns an unknown slug

- **WHEN** the classifier returns a slug that does not match any configured intent space
- **THEN** the query is treated as below threshold and routed to the General fallback
- **AND** the anomaly is recorded in the query log

### Requirement: Confidence threshold enforcement

The system SHALL compare the classification confidence against the configured threshold and SHALL restrict retrieval to the classified intent space only when the confidence meets or exceeds it.

#### Scenario: Confidence at or above threshold

- **WHEN** classification returns the Finance space with confidence 0.91 and the threshold is 0.70
- **THEN** retrieval searches only the Finance space
- **AND** the query is logged with `fallback_used` false

#### Scenario: Confidence below threshold

- **WHEN** classification returns confidence 0.42 and the threshold is 0.70
- **THEN** retrieval searches every intent space
- **AND** the query is logged against the General space with `fallback_used` true

#### Scenario: Confidence exactly at the threshold

- **WHEN** classification confidence equals the configured threshold exactly
- **THEN** the classified space is used
- **AND** the fallback is not triggered

#### Scenario: Threshold change takes effect immediately

- **WHEN** an admin raises the threshold and a new query arrives
- **THEN** the new query is evaluated against the updated threshold

### Requirement: General classification searches all spaces

The system SHALL treat a classification result of the General space as a request to search every intent space, regardless of the confidence value.

#### Scenario: Confidently classified as General

- **WHEN** classification returns the General space with high confidence
- **THEN** retrieval searches every intent space
- **AND** the query is logged against General with `fallback_used` true

### Requirement: Classification failure falls back rather than failing

The system SHALL route a query to the General fallback when the classification call fails, rather than returning an error to the user.

#### Scenario: Provider error during classification

- **WHEN** the LLM provider raises an error during classification
- **THEN** the query is routed to the General fallback and answered
- **AND** the query log records the classification failure

#### Scenario: Classification timeout

- **WHEN** the classification call exceeds its timeout
- **THEN** the query proceeds through the General fallback
- **AND** the user still receives an answer

### Requirement: Concurrent classification and query embedding

The system SHALL issue the classification call and the query embedding call concurrently, since the embedding does not depend on the classification result.

#### Scenario: Both calls overlap

- **WHEN** a query arrives
- **THEN** classification and query embedding are issued concurrently
- **AND** retrieval begins once both have completed

### Requirement: Routing hand-off to retrieval

The system SHALL pass retrieval an explicit list of intent spaces to search — a single space when the threshold is met, or all spaces on fallback — so that retrieval never makes the routing decision itself.

#### Scenario: Single-space routing

- **WHEN** the confidence threshold is met for the HR space
- **THEN** retrieval receives a list containing only the HR space

#### Scenario: Fallback routing

- **WHEN** the fallback is triggered
- **THEN** retrieval receives a list containing every configured intent space

### Requirement: Per-query routing record

The system SHALL record, for every query, the classified intent space, the confidence value, the classifier's reasoning, and whether the fallback was used, so that routing decisions are auditable after the fact.

#### Scenario: Routing decision is auditable

- **WHEN** any query completes
- **THEN** its log entry contains the classified space, the confidence, the reasoning, and the fallback flag
- **AND** an admin can determine from the log alone why the query was routed as it was
