## Purpose

Finds the passages that answer a question within the intent spaces the orchestrator selected, and turns them into a concise, cited, channel-appropriate answer — or an explicit no-match message when the knowledge base does not contain the answer.

## ADDED Requirements

### Requirement: Semantic search within routed spaces

The system SHALL embed the question with the configured embedding provider and SHALL search only the vector indexes of the intent spaces supplied by the orchestrator, merging results by similarity score and returning the top 5 chunks.

#### Scenario: Single-space search

- **WHEN** retrieval receives one intent space
- **THEN** only that space's index is searched
- **AND** chunks belonging to other spaces cannot appear in the results

#### Scenario: Multi-space search merges by score

- **WHEN** retrieval receives every intent space on fallback
- **THEN** each index is searched
- **AND** results are merged and ranked by similarity score across all of them

#### Scenario: Empty knowledge base

- **WHEN** retrieval runs against intent spaces that contain no indexed chunks
- **THEN** no results are returned
- **AND** the query resolves to a no-match response rather than an error

### Requirement: Relevance floor gates answering

The system SHALL compare the highest similarity score among retrieved chunks against the configured relevance floor and SHALL return a no-match response without generating an answer when the best score falls below it.

#### Scenario: Best result below the floor

- **WHEN** the top chunk's similarity score is below the relevance floor
- **THEN** no answer generation call is made
- **AND** a no-match response is returned

#### Scenario: Best result at or above the floor

- **WHEN** the top chunk's similarity score meets or exceeds the relevance floor
- **THEN** answer generation proceeds with the retrieved chunks

#### Scenario: Relevance floor is independent of confidence

- **WHEN** classification confidence was high but retrieval scores are below the floor
- **THEN** a no-match response is returned
- **AND** no answer is fabricated from weakly related content

### Requirement: Grounded answer generation

The system SHALL generate answers using only the retrieved chunks as source material, SHALL instruct the model not to draw on knowledge outside those chunks, and SHALL keep answers concise.

#### Scenario: Answer derived from retrieved content

- **WHEN** relevant chunks are retrieved
- **THEN** the generated answer addresses the question using those chunks
- **AND** the prompt instructs the model to answer only from the supplied context

#### Scenario: Context insufficient despite passing the floor

- **WHEN** the retrieved chunks do not actually contain the answer
- **THEN** the response states that the knowledge base does not cover the question
- **AND** no answer is invented

### Requirement: Citations

The system SHALL attach citations to every generated answer identifying the source document and its location reference, and SHALL discard any citation naming a document that was not among the retrieved chunks.

#### Scenario: Answer carries source citations

- **WHEN** an answer is generated from retrieved chunks
- **THEN** it includes citations naming the source documents and their page, paragraph, or sheet references

#### Scenario: Unverifiable citation discarded

- **WHEN** the model produces a citation referencing a document that was not retrieved
- **THEN** that citation is removed before the answer is delivered
- **AND** the remaining verified citations are retained

#### Scenario: Multiple documents cited

- **WHEN** the answer draws on chunks from more than one document
- **THEN** each contributing document appears in the citation list

### Requirement: No-match response

The system SHALL return a clear, non-technical no-match message when no sufficiently relevant content is found, SHALL name the intent space that was searched, and SHALL never present a no-match as a system error.

#### Scenario: No-match message is explicit

- **WHEN** retrieval finds nothing above the relevance floor
- **THEN** the user receives a message stating that the knowledge base does not contain an answer
- **AND** the message names the knowledge domain that was searched

#### Scenario: No-match is not an error

- **WHEN** a no-match occurs
- **THEN** the interaction is recorded as a successful query with a no-match flag
- **AND** no error is surfaced to the user

### Requirement: Channel-aware answer formatting

The system SHALL pass the destination channel's formatting profile — maximum length, markdown flavor, and list support — into the generation prompt so answers are written to fit the destination, and SHALL additionally apply a deterministic formatting pass that guarantees the channel's constraints are met.

#### Scenario: Generation is channel-aware

- **WHEN** an answer is generated for a given channel
- **THEN** the prompt includes that channel's length limit and formatting capabilities

#### Scenario: Deterministic truncation guarantees the limit

- **WHEN** a generated answer exceeds the channel's maximum length despite the prompt
- **THEN** the answer is truncated at a word boundary with a visible truncation marker
- **AND** the delivered message is within the channel limit

#### Scenario: Markup escaped for the destination

- **WHEN** an answer contains characters that are reserved in the destination channel's markup
- **THEN** those characters are escaped so the message renders correctly rather than failing to send

### Requirement: Retrieval hit recording

The system SHALL record which chunks were retrieved for each query, along with their rank and similarity score, so that document-level usage can be measured.

#### Scenario: Hits recorded per query

- **WHEN** retrieval returns chunks for a query
- **THEN** each retrieved chunk is recorded with its rank, score, and source document
- **AND** the record is linked to the query log entry

#### Scenario: No hits recorded for a no-match below the floor

- **WHEN** a query resolves to no-match because every score is below the relevance floor
- **THEN** the query is still logged
- **AND** no chunk is credited as having answered it

### Requirement: Generation failure handling

The system SHALL return a user-facing failure message rather than an unhandled error when the LLM provider fails during answer generation, and SHALL record the failure in the query log.

#### Scenario: Provider failure during generation

- **WHEN** the LLM provider raises an error while generating an answer
- **THEN** the user receives a message stating that the answer could not be generated and to try again
- **AND** the query log records the error
