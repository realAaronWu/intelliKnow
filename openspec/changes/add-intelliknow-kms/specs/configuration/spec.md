## Purpose

Puts every tunable in the system — AI models, embedding model, chunking and retrieval parameters, the classification threshold, intent spaces, and channel settings — into a single configuration file that an operator can read and edit in one place, keeping secrets separate and applying changes without a restart.

## ADDED Requirements

### Requirement: Single configuration file

The system SHALL read all non-secret configuration from one `config.yaml` file, and SHALL NOT store tunable settings in the database or scatter them across environment variables.

#### Scenario: All tunables in one file

- **WHEN** an operator opens `config.yaml`
- **THEN** the LLM provider and models, embedding model, chunking parameters, retrieval parameters, confidence threshold, relevance floor, intent spaces, channel settings, upload limits, and storage paths are all present

#### Scenario: No competing settings store

- **WHEN** any component needs a tunable value
- **THEN** it reads it from the configuration service
- **AND** no equivalent value is persisted in the database

### Requirement: Secrets separated from configuration

The system SHALL read secrets — AI provider API keys, chat platform tokens, and the admin password — only from environment variables or a `.env` file, and SHALL NOT read or write them in `config.yaml`.

#### Scenario: Secrets come from the environment

- **WHEN** the service needs an API key or bot token
- **THEN** it reads the value from the environment
- **AND** the value does not appear in `config.yaml`

#### Scenario: Config file is safe to commit

- **WHEN** `config.yaml` is inspected
- **THEN** it contains no credential values

### Requirement: Configuration schema validation

The system SHALL validate `config.yaml` against a typed schema at startup and SHALL refuse to start when validation fails, reporting which field is invalid and why.

#### Scenario: Invalid value rejected at startup

- **WHEN** the service starts with a confidence threshold outside 0.0 to 1.0
- **THEN** startup fails
- **AND** the error names the field and the acceptable range

#### Scenario: Unknown field rejected

- **WHEN** `config.yaml` contains a field the schema does not define
- **THEN** startup fails with an error naming the unrecognized field

#### Scenario: Missing file uses documented defaults

- **WHEN** no `config.yaml` is present
- **THEN** the service writes one containing the documented defaults and starts

### Requirement: Runtime configuration updates

The system SHALL allow configuration to be updated through the admin API, SHALL validate the update before applying it, and SHALL apply accepted changes to subsequent operations without a restart.

#### Scenario: Threshold change applies immediately

- **WHEN** an admin changes the confidence threshold through the console
- **THEN** the next query is evaluated against the new value
- **AND** no restart is required

#### Scenario: Invalid update rejected without side effects

- **WHEN** an admin submits a configuration update that fails validation
- **THEN** the update is rejected with a message naming the invalid field
- **AND** the file on disk and the running configuration are both unchanged

### Requirement: Safe configuration writes

The system SHALL write configuration changes atomically and SHALL retain the previous version of the file so that a bad write cannot leave the system without a usable configuration.

#### Scenario: Atomic write

- **WHEN** the configuration file is updated
- **THEN** it is written to a temporary file and moved into place
- **AND** a partially written file is never left on disk

#### Scenario: Previous version retained

- **WHEN** the configuration file is updated
- **THEN** the prior contents are preserved as a backup file

### Requirement: Immutable embedding settings once documents exist

The system SHALL reject any change to the embedding model or dimension while indexed documents exist, and SHALL allow the change only after a full re-index.

#### Scenario: Embedding model change refused

- **WHEN** an admin attempts to change the embedding model while documents are indexed
- **THEN** the change is rejected with an explanation that existing vectors would become incomparable
- **AND** the message directs the admin to run a full re-index

#### Scenario: Change allowed on an empty knowledge base

- **WHEN** the embedding model is changed while no documents are indexed
- **THEN** the change is accepted

### Requirement: Effective configuration is readable

The system SHALL expose the current effective configuration through the admin API for display in the console, with secret values omitted entirely.

#### Scenario: Console reads effective configuration

- **WHEN** the console requests the current configuration
- **THEN** every tunable value in effect is returned
- **AND** no secret value is included

#### Scenario: Secret presence indicated without disclosure

- **WHEN** the console displays provider or channel setup
- **THEN** it indicates whether each required secret is set
- **AND** it names the environment variable to set when one is missing
