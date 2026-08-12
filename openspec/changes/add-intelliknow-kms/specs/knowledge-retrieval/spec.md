## Purpose

The RAG read path: finds the passages that answer a question within the intent spaces the orchestrator selected, by combining dense vector search with keyword search, fusing the two rankings, gating on genuine relevance, assembling a bounded context, and generating a grounded answer whose citations are verified against what was actually retrieved.

## ADDED Requirements

### Requirement: Dense vector retrieval

The system SHALL embed the question with the configured embedding provider and SHALL search the vector index of every intent space supplied by the orchestrator, returning the configured number of nearest chunks per space ranked by cosine similarity.

#### Scenario: Single-space vector search

- **WHEN** retrieval receives one intent space
- **THEN** only that space's vector index is searched
- **AND** chunks belonging to other spaces cannot appear in the vector results

#### Scenario: Multi-space vector search

- **WHEN** retrieval receives every intent space on fallback
- **THEN** each space's index is searched
- **AND** the per-space results are merged into one ranked list by similarity

#### Scenario: Vectors are comparable across spaces

- **WHEN** results from different space indexes are merged
- **THEN** they are ranked by a similarity computed from the same embedding model
- **AND** no space is favored by an index-specific score scale

### Requirement: Keyword retrieval

The system SHALL maintain a full-text keyword index over chunk text and SHALL run a BM25-ranked keyword search restricted to the same intent spaces, returning the configured number of chunks.

#### Scenario: Exact term matched

- **WHEN** a question contains a rare exact token such as a policy code or a salary band name
- **THEN** chunks containing that token are returned by the keyword search
- **AND** they are eligible for the final result set even if dense search ranked them poorly

#### Scenario: Keyword search respects routing

- **WHEN** retrieval is restricted to one intent space
- **THEN** the keyword search returns only chunks belonging to that space

#### Scenario: Keyword retrieval disabled by configuration

- **WHEN** the configured keyword result count is zero
- **THEN** retrieval proceeds using dense results alone
- **AND** no error occurs

### Requirement: Rank fusion

The system SHALL combine the dense and keyword rankings using reciprocal rank fusion with a configurable constant, and SHALL select the configured final number of chunks from the fused ranking.

#### Scenario: Rankings fused without score normalization

- **WHEN** dense and keyword results are combined
- **THEN** each chunk's fused score is computed from its rank in each list
- **AND** no normalization between similarity and BM25 scales is required

#### Scenario: Chunks found by both retrievers rank highest

- **WHEN** a chunk appears in both the dense and keyword result lists
- **THEN** its fused score is higher than an equally-ranked chunk that appears in only one list

#### Scenario: Final selection size honored

- **WHEN** fusion completes
- **THEN** the configured number of top chunks is passed forward

### Requirement: Relevance gate

The system SHALL compare the highest dense similarity score among retrieved chunks against the configured relevance floor, and SHALL return a no-match result without making an answer generation call when it falls below.

#### Scenario: Best result below the floor

- **WHEN** the highest dense similarity is below the relevance floor
- **THEN** no answer generation call is made
- **AND** a no-match result is returned

#### Scenario: Gate uses the similarity score, not the fused score

- **WHEN** the relevance gate evaluates a query
- **THEN** it uses the dense cosine similarity
- **AND** it does not use the rank-derived fused score, which carries no notion of absolute relevance

#### Scenario: Gate is independent of classification confidence

- **WHEN** classification confidence was high but the best similarity is below the floor
- **THEN** a no-match result is returned
- **AND** no answer is generated from weakly related content

#### Scenario: Empty knowledge base

- **WHEN** the routed spaces contain no indexed chunks
- **THEN** a no-match result is returned rather than an error

### Requirement: Context assembly

The system SHALL build the generation context from the selected chunks by removing near-duplicates, ordering them by source document and position rather than by score, tagging each with a citation marker and its provenance, and enforcing a configured total character budget.

#### Scenario: Near-duplicates removed

- **WHEN** two selected chunks from the same document overlap heavily
- **THEN** only one is included in the context

#### Scenario: Chunks ordered for readability

- **WHEN** the context is assembled
- **THEN** chunks are ordered by document and by their position within it
- **AND** not by retrieval score

#### Scenario: Each chunk is tagged with provenance

- **WHEN** a chunk is placed in the context
- **THEN** it carries a citation marker, its document title, its source reference, and its heading path

#### Scenario: Context budget enforced

- **WHEN** the selected chunks exceed the configured character budget
- **THEN** the lowest-ranked chunks are dropped until the budget is met

### Requirement: Grounded answer generation

The system SHALL generate answers using only the assembled context, SHALL instruct the model not to draw on knowledge outside it, SHALL require citation markers for claims, and SHALL keep answers concise.

#### Scenario: Answer derived from context

- **WHEN** relevant context is assembled
- **THEN** the generated answer addresses the question from that context
- **AND** the prompt instructs the model to use no other source

#### Scenario: Context insufficient despite passing the gate

- **WHEN** the assembled context does not actually contain the answer
- **THEN** the response states that the knowledge base does not cover the question
- **AND** no answer is invented

### Requirement: Citation verification

The system SHALL parse the citation markers from the generated answer, SHALL resolve each to a chunk that was actually supplied in the context, SHALL discard markers that do not resolve, and SHALL attach the resolved document titles and source references to the response.

#### Scenario: Citations resolved and attached

- **WHEN** an answer cites supplied context
- **THEN** each marker resolves to a chunk
- **AND** the response carries the corresponding document titles and source references

#### Scenario: Unresolvable citation discarded

- **WHEN** the model emits a citation marker that was not in the supplied context
- **THEN** that marker is removed from the delivered answer
- **AND** the remaining verified citations are retained

#### Scenario: Multiple documents cited

- **WHEN** the answer draws on chunks from more than one document
- **THEN** each contributing document appears in the citation list

### Requirement: No-match response

The system SHALL return a clear, non-technical no-match message when the relevance gate rejects retrieval or the model reports insufficient context, SHALL name the knowledge domain that was searched, and SHALL record the query as a no-match rather than a failure.

#### Scenario: No-match message is explicit

- **WHEN** no sufficiently relevant content is found
- **THEN** the user receives a message stating that the knowledge base does not contain an answer
- **AND** the message names the domain that was searched

#### Scenario: No-match is not an error

- **WHEN** a no-match occurs
- **THEN** the query is recorded with status `no_match`
- **AND** no error is surfaced to the user

### Requirement: Channel-aware answer formatting

The system SHALL pass the destination channel's formatting profile — maximum length and markup capabilities — into the generation prompt so the answer is written to fit, and SHALL additionally apply a deterministic formatting pass that guarantees the channel's limits are met.

#### Scenario: Generation is channel-aware

- **WHEN** an answer is generated for a given channel
- **THEN** the prompt includes that channel's length limit and formatting capabilities

#### Scenario: Deterministic truncation guarantees the limit

- **WHEN** a generated answer exceeds the channel's maximum length despite the prompt
- **THEN** the answer is truncated at a word boundary with a visible truncation marker
- **AND** the delivered message is within the channel limit

#### Scenario: Markup escaped for the destination

- **WHEN** an answer contains characters reserved in the destination channel's markup
- **THEN** those characters are escaped so the message renders rather than failing to send

### Requirement: Retrieval parameters are configuration-driven

The system SHALL read the dense result count, keyword result count, fusion constant, final chunk count, context character budget, and relevance floor from configuration, and SHALL apply changes to subsequent queries without a restart.

#### Scenario: Retrieval tuning without code changes

- **WHEN** an operator changes a retrieval parameter in the configuration file
- **THEN** the next query uses the new value
- **AND** no code change or restart is required

### Requirement: Generation failure handling

The system SHALL return a user-facing failure message rather than an unhandled error when the provider fails during answer generation, and SHALL record the query with status `failed`.

#### Scenario: Provider failure during generation

- **WHEN** the LLM provider raises an error while generating an answer
- **THEN** the user receives a message stating the answer could not be generated and to try again
- **AND** the query is recorded with status `failed` and the error message
