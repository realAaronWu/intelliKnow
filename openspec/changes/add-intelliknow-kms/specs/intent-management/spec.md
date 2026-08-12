## Purpose

Defines the named knowledge domains that queries are routed into and documents are filed under, including the protected default spaces, the lifecycle of custom spaces, and the tuning thresholds that govern routing behavior.

## ADDED Requirements

### Requirement: Default intent spaces

The system SHALL create four intent spaces on first startup — `hr`, `legal`, `finance`, and `general` — each with an editable human-readable name and description.

#### Scenario: First startup seeds defaults

- **WHEN** the service starts against an empty database
- **THEN** the HR, Legal, Finance, and General intent spaces exist
- **AND** each has a default description explaining the kinds of questions it covers

#### Scenario: Restart does not duplicate defaults

- **WHEN** the service restarts against a database that already contains the default spaces
- **THEN** no duplicate spaces are created
- **AND** any admin edits to the default spaces are preserved

### Requirement: General is a protected fallback space

The system SHALL mark the `general` space as protected, SHALL prevent its deletion, and SHALL prevent its slug from being changed.

#### Scenario: Deleting General is refused

- **WHEN** an admin attempts to delete the General space
- **THEN** the request is rejected with an error explaining that General is the required fallback space
- **AND** the space still exists

#### Scenario: General may still be renamed and described

- **WHEN** an admin edits the display name or description of the General space
- **THEN** the change is saved
- **AND** the slug remains `general`

### Requirement: Custom intent space management

The system SHALL allow an admin to create, rename, re-describe, and delete custom intent spaces. Slugs SHALL be unique, lowercase, and kebab-case.

#### Scenario: Create a custom space

- **WHEN** an admin creates a space named "Operations" with a description
- **THEN** the space is created with slug `operations`
- **AND** it becomes immediately available as a classification target and a document assignment target

#### Scenario: Duplicate slug rejected

- **WHEN** an admin creates a space whose slug collides with an existing space
- **THEN** the request is rejected with an error naming the conflict
- **AND** no space is created

#### Scenario: Description is editable after creation

- **WHEN** an admin edits a space's description
- **THEN** the new description is used in subsequent classification calls
- **AND** no re-indexing occurs

### Requirement: Deleting a space reassigns its documents

The system SHALL require that deleting a non-protected intent space reassigns every document currently assigned to it, and SHALL offer reassignment to General as the default.

#### Scenario: Deleting a space with documents

- **WHEN** an admin deletes a space that has documents assigned to it
- **THEN** the admin is required to choose a destination space
- **AND** every affected document and its chunks are moved to the destination space before the original space is removed

#### Scenario: Deleting an empty space

- **WHEN** an admin deletes a space that has no documents
- **THEN** the space and its vector index are removed
- **AND** no reassignment prompt is shown

### Requirement: Per-space vector index lifecycle

The system SHALL maintain one vector index per intent space, SHALL create it when the space is created, SHALL delete it when the space is deleted, and SHALL move a document's vectors between indexes when the document's intent space changes.

#### Scenario: Index created with the space

- **WHEN** a new intent space is created
- **THEN** an empty vector index is created for it

#### Scenario: Reassigning a document moves its vectors

- **WHEN** a document is reassigned from one intent space to another
- **THEN** all of its chunk vectors are removed from the source index and added to the destination index
- **AND** the chunks are not re-parsed or re-embedded

#### Scenario: Index removed with the space

- **WHEN** an intent space is deleted
- **THEN** its vector index is removed from storage

### Requirement: Configurable classification confidence threshold

The system SHALL store a classification confidence threshold, SHALL default it to 0.70, SHALL allow an admin to change it at runtime to any value between 0.0 and 1.0, and SHALL apply the new value to subsequent queries without a restart.

#### Scenario: Threshold defaults to 0.70

- **WHEN** the service starts for the first time
- **THEN** the confidence threshold is 0.70

#### Scenario: Threshold changed at runtime

- **WHEN** an admin sets the threshold to 0.85
- **THEN** the next query is evaluated against 0.85
- **AND** no restart is required

#### Scenario: Out-of-range threshold rejected

- **WHEN** an admin submits a threshold outside the range 0.0 to 1.0
- **THEN** the request is rejected
- **AND** the previous threshold remains in effect

### Requirement: Configurable relevance floor

The system SHALL store a retrieval relevance floor, SHALL default it to 0.35, and SHALL allow an admin to change it at runtime, applying it to subsequent queries without a restart.

#### Scenario: Relevance floor is separately adjustable

- **WHEN** an admin changes the relevance floor
- **THEN** the confidence threshold is unaffected
- **AND** subsequent queries use the new floor to decide whether to answer or return no match
