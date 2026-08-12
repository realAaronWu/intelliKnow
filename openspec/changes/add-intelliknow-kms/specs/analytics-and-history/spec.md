## Purpose

Records what was asked, how it was classified, and whether it was answered, then presents that as the query classification log the admin scans to see routing working, plus the knowledge base usage figures and export the project brief requires.

## ADDED Requirements

### Requirement: Query logging

The system SHALL write one log entry per query carrying the timestamp, originating channel, an identifier for the asking user, the question text, the detected intent space, the classification confidence, whether the fallback was used, the response status, the delivered answer, the verified citations, the documents that were retrieved, and the end-to-end latency.

#### Scenario: Answered query logged

- **WHEN** a query is answered
- **THEN** a log entry records every listed field with status `success`

#### Scenario: No-match query logged

- **WHEN** a query resolves to no-match
- **THEN** a log entry is written with status `no_match`
- **AND** the intent space that was searched is recorded

#### Scenario: Failed query logged

- **WHEN** a query fails during processing
- **THEN** a log entry is written with status `failed` and the error message

### Requirement: Response status values

The system SHALL record exactly one status per query from `success`, `no_match`, or `failed`, and SHALL present them as Success, No match, and Failed.

#### Scenario: Statuses are distinguishable

- **WHEN** an admin views the log
- **THEN** an answered query, a query with no matching knowledge, and a query that errored are visually distinct
- **AND** a no-match is not presented as a failure

### Requirement: Logging never blocks the user

The system SHALL write the log entry after the answer has been handed to the channel adapter for delivery, and SHALL suppress any logging failure rather than propagating it to the user.

#### Scenario: Logging occurs after delivery

- **WHEN** a query completes
- **THEN** the answer is delivered before the log entry is written

#### Scenario: Logging failure is contained

- **WHEN** writing the log entry fails
- **THEN** the user still receives their answer
- **AND** the failure is reported only to the service log

### Requirement: Query classification log

The system SHALL expose recent queries newest first as a table showing the timestamp, channel, question, detected intent space, classification confidence score, and response status.

#### Scenario: Log listed newest first

- **WHEN** an admin opens the query classification log
- **THEN** entries appear newest first with those six columns

#### Scenario: Log is paginated

- **WHEN** the log contains more entries than fit on one page
- **THEN** the admin can page through them

#### Scenario: Log filtered by intent space and status

- **WHEN** an admin filters by intent space or by status
- **THEN** only matching entries are listed

### Requirement: Query detail view

The system SHALL allow an admin to open a single logged query and see the full answer, the verified citations with their source documents, and the measured latency.

#### Scenario: Detail opened from the log

- **WHEN** an admin selects a row in the log
- **THEN** the full answer, its citations, and the latency are shown

#### Scenario: Detail for a failed query

- **WHEN** an admin opens a query with status `failed`
- **THEN** the recorded error message is shown

### Requirement: Intent space distribution

The system SHALL report how many queries were classified into each intent space over a selected period.

#### Scenario: Distribution reported

- **WHEN** an admin views analytics for a period
- **THEN** the query count per intent space is shown
- **AND** the most common intent spaces are identifiable

### Requirement: Knowledge base usage

The system SHALL report which documents were retrieved most often across logged queries over a selected period.

#### Scenario: Most accessed documents ranked

- **WHEN** an admin views analytics for a period
- **THEN** documents are ranked by how often they appear among the retrieved documents of logged queries

#### Scenario: Deleted documents remain attributable

- **WHEN** a document that was retrieved in the past has since been deleted
- **THEN** its historical usage is still reported

### Requirement: Data export

The system SHALL export the query log as CSV with one row per query and a header row naming each field.

#### Scenario: Log exported as CSV

- **WHEN** an admin triggers the export
- **THEN** a CSV is produced with one row per logged query

#### Scenario: Empty log exports headers only

- **WHEN** the export runs with no logged queries
- **THEN** a CSV containing only the header row is produced rather than an error
