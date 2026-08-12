## Purpose

Records what was asked, how it was routed, and what answered it, then turns that history into the measurements an admin needs to judge whether classification is working and which knowledge is actually being used.

## ADDED Requirements

### Requirement: Per-query logging

The system SHALL write a log entry for every query carrying the timestamp, originating channel, external user identifier, question text, classified intent space, confidence value, fallback flag, no-match flag, delivered answer, citations, and end-to-end latency in milliseconds.

#### Scenario: Successful query logged completely

- **WHEN** a query is answered
- **THEN** a log entry records every listed field
- **AND** the latency reflects the full round trip from inbound message to delivered answer

#### Scenario: No-match query logged

- **WHEN** a query resolves to no-match
- **THEN** a log entry is written with the no-match flag set
- **AND** the searched intent space is recorded

#### Scenario: Failed query logged

- **WHEN** a query fails during processing
- **THEN** a log entry is written recording the error
- **AND** the entry is distinguishable from a successful answer and from a no-match

### Requirement: Logging never blocks the user

The system SHALL write the query log entry after the answer has been handed to the channel adapter for delivery, and SHALL suppress any logging failure rather than propagating it to the user.

#### Scenario: Logging occurs after delivery

- **WHEN** a query completes
- **THEN** the answer is delivered before the log entry is written

#### Scenario: Logging failure is contained

- **WHEN** writing the log entry fails
- **THEN** the user still receives their answer
- **AND** the logging failure is reported to the service log only

### Requirement: Retrieval hit tracking

The system SHALL record every chunk returned by retrieval for a query along with its rank, similarity score, and source document, and SHALL retain these records after the source document is deleted.

#### Scenario: Hits linked to the query

- **WHEN** retrieval returns chunks
- **THEN** each is recorded against the query with its rank, score, and source document

#### Scenario: Hit history survives document deletion

- **WHEN** a document that was previously retrieved is deleted
- **THEN** its historical hit records remain
- **AND** analytics can still attribute past usage to it

### Requirement: Query history browsing

The system SHALL expose the query history to the admin, newest first, with filters for date range, channel, intent space, fallback status, and no-match status.

#### Scenario: History listed newest first

- **WHEN** an admin opens the query history
- **THEN** entries are listed newest first with their question, intent space, confidence, and latency

#### Scenario: Filtering by intent space

- **WHEN** an admin filters by a specific intent space
- **THEN** only queries routed to that space are returned

#### Scenario: Filtering to fallback queries

- **WHEN** an admin filters to queries where the fallback was used
- **THEN** only those queries are returned
- **AND** the admin can review what the classifier was uncertain about

### Requirement: Classification accuracy metrics

The system SHALL report classification metrics over a selected period: the distribution of queries across intent spaces, the mean and distribution of confidence values, and the proportion of queries that used the fallback.

#### Scenario: Intent distribution reported

- **WHEN** an admin opens analytics for a period
- **THEN** the query count per intent space is reported

#### Scenario: Confidence distribution reported

- **WHEN** an admin opens analytics for a period
- **THEN** the mean confidence and its distribution are reported
- **AND** the admin can judge whether the configured threshold is well placed

#### Scenario: Fallback rate reported

- **WHEN** an admin opens analytics for a period
- **THEN** the proportion of queries that fell back to General is reported

### Requirement: Admin-confirmed classification correctness

The system SHALL allow an admin to mark a logged query's classification as correct or incorrect and, when incorrect, to record the space it should have been routed to, and SHALL report measured accuracy over the reviewed queries.

#### Scenario: Query marked incorrect

- **WHEN** an admin marks a query's classification as incorrect and selects the correct space
- **THEN** the correction is stored against that query

#### Scenario: Measured accuracy reported

- **WHEN** queries have been reviewed
- **THEN** analytics reports the proportion judged correct out of those reviewed
- **AND** the reviewed count is shown alongside so the figure is not mistaken for a whole-population measure

#### Scenario: Corrections guide tuning

- **WHEN** an admin views corrections for a space
- **THEN** the misrouted questions are listed so the space's description can be improved

### Requirement: Knowledge base usage metrics

The system SHALL report, over a selected period, the most frequently retrieved documents ranked by hit count, the documents that have never been retrieved, and the no-match rate.

#### Scenario: Most accessed documents ranked

- **WHEN** an admin opens analytics for a period
- **THEN** documents are ranked by how often their chunks were retrieved

#### Scenario: Unused documents identified

- **WHEN** an admin opens analytics for a period
- **THEN** indexed documents that were never retrieved in that period are listed

#### Scenario: No-match rate reported

- **WHEN** an admin opens analytics for a period
- **THEN** the proportion of queries that produced no match is reported
- **AND** the most frequent no-match questions are listed as knowledge gaps

### Requirement: Latency reporting

The system SHALL report mean and 95th-percentile end-to-end query latency over a selected period, broken down by channel.

#### Scenario: Latency percentiles reported

- **WHEN** an admin opens analytics for a period
- **THEN** the mean and 95th-percentile latency are reported
- **AND** the admin can verify the round-trip target against real traffic

#### Scenario: Latency broken down by channel

- **WHEN** queries have arrived from more than one channel
- **THEN** latency is reported per channel as well as overall

### Requirement: Data export

The system SHALL export the query history for a selected period as CSV, including every logged field, with one row per query.

#### Scenario: Export produced for a period

- **WHEN** an admin exports the history for a date range
- **THEN** a CSV file is produced containing one row per query in that range
- **AND** the header row names every exported field

#### Scenario: Empty period exports headers only

- **WHEN** an admin exports a period containing no queries
- **THEN** a CSV containing only the header row is produced rather than an error
