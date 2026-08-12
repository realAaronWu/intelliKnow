## Purpose

Defines the named knowledge domains that queries are routed into and documents are filed under — their declaration in configuration, the classification keywords that let an admin improve routing accuracy without code changes, the protected General fallback, and the per-space vector index lifecycle.

## ADDED Requirements

### Requirement: Intent spaces declared in configuration

The system SHALL declare intent spaces in the configuration file, each with a slug, a display name, a description, and a list of classification keywords, and SHALL ship HR, Legal, Finance, Operations, and General as defaults.

#### Scenario: Default spaces present on first start

- **WHEN** the service starts with no existing configuration
- **THEN** HR, Legal, Finance, Operations, and General intent spaces exist
- **AND** each has a default description and keyword list

#### Scenario: Spaces are configuration, not database rows

- **WHEN** an intent space is added, edited, or removed
- **THEN** the change is written to the configuration file
- **AND** no intent space table exists in the database

#### Scenario: Documents reference a space by slug

- **WHEN** a document is assigned to an intent space
- **THEN** the document stores the space's slug

### Requirement: Classification keywords

The system SHALL store a list of admin-editable keywords on each intent space and SHALL use them in classification, so that an admin can improve classification accuracy without changing code.

#### Scenario: Keywords supplied to the classifier

- **WHEN** a query is classified
- **THEN** the space's keywords contribute to its centroid alongside its name and description

#### Scenario: Editing keywords changes routing

- **WHEN** an admin adds a keyword to a space and saves
- **THEN** subsequent queries containing that term are more likely to be classified into that space
- **AND** no restart or re-indexing occurs

#### Scenario: Keywords are optional

- **WHEN** an intent space has an empty keyword list
- **THEN** classification still functions using its name and description

### Requirement: General is a protected fallback space

The system SHALL mark the General space as protected, SHALL prevent its removal, and SHALL prevent its slug from changing.

#### Scenario: Removing General is refused

- **WHEN** an admin attempts to delete the General space
- **THEN** the request is rejected with an error explaining that General is the required fallback space
- **AND** the space remains

#### Scenario: General may still be renamed and described

- **WHEN** an admin edits the display name, description, or keywords of the General space
- **THEN** the change is saved
- **AND** its slug remains unchanged

### Requirement: Custom intent space management

The system SHALL allow an admin to create, edit, and delete custom intent spaces through the console, with unique lowercase kebab-case slugs.

#### Scenario: Create a custom space

- **WHEN** an admin creates a space named "Operations" with a description and keywords
- **THEN** the space is created with slug `operations`
- **AND** it becomes immediately available as a classification target and a document assignment target

#### Scenario: Duplicate slug rejected

- **WHEN** an admin creates a space whose slug collides with an existing space
- **THEN** the request is rejected with an error naming the conflict
- **AND** no space is created

#### Scenario: Edit form covers name, description, and keywords

- **WHEN** an admin edits an intent space
- **THEN** the name, description, and keyword list are all editable in one form

### Requirement: Deleting a space requires reassigning its documents

The system SHALL refuse to delete an intent space that still has documents assigned, reporting the count, and SHALL require the admin to reassign them first.

#### Scenario: Deleting a space with documents refused

- **WHEN** an admin deletes a space that has documents assigned to it
- **THEN** the request is rejected with an error stating how many documents are assigned
- **AND** the space and its documents are unchanged

#### Scenario: Deleting an empty space succeeds

- **WHEN** an admin deletes a space that has no documents
- **THEN** the space and its vector index are removed

### Requirement: Per-space document count

The system SHALL report the number of documents currently assigned to each intent space.

#### Scenario: Count shown per space

- **WHEN** an admin views the intent spaces
- **THEN** each shows how many documents are assigned to it

### Requirement: Per-space classification accuracy rate

The system SHALL report a classification accuracy rate for each intent space, computed as the proportion of queries classified into that space whose confidence met or exceeded the configured threshold, over a period, and SHALL state how the figure is derived so it is not mistaken for human-verified accuracy.

#### Scenario: Accuracy rate reported per space

- **WHEN** an admin views the intent spaces
- **THEN** each shows its classification accuracy rate over the reporting period

#### Scenario: Derivation is stated

- **WHEN** the accuracy rate is displayed
- **THEN** the interface states that it is the share of queries classified into that space at or above the confidence threshold

#### Scenario: No queries yet

- **WHEN** a space has had no queries classified into it
- **THEN** the accuracy rate is shown as not yet available rather than as zero

### Requirement: Keywords drive both centroid and prompt

The system SHALL use each intent space's keywords both in computing its classification centroid and in the escalation prompt, so that a keyword edit changes routing behaviour immediately.

#### Scenario: Keyword edit moves the centroid

- **WHEN** an admin edits a space's keywords and saves
- **THEN** that space's centroid is recomputed
- **AND** the next query is scored against the updated centroid with no restart and no re-indexing

#### Scenario: Keyword edit reaches the escalation prompt

- **WHEN** an escalation call is made after a keyword edit
- **THEN** the prompt contains the updated keywords

### Requirement: Per-space vector index lifecycle

The system SHALL maintain one vector index per intent space, SHALL create it when the space is created, SHALL remove it when the space is deleted, and SHALL move a document's vectors between indexes when the document's intent space changes.

#### Scenario: Index created with the space

- **WHEN** a new intent space is created
- **THEN** an empty vector index is created for it

#### Scenario: Reassigning a document moves its vectors

- **WHEN** a document is reassigned from one intent space to another
- **THEN** all of its chunk vectors move from the source index to the destination index
- **AND** the chunks are not re-parsed or re-embedded

#### Scenario: Index removed with the space

- **WHEN** an intent space is deleted
- **THEN** its vector index is removed from storage

### Requirement: Classification threshold in configuration

The system SHALL read the classification confidence threshold from configuration, SHALL default it to 0.70, SHALL accept any value between 0.0 and 1.0, and SHALL apply changes to subsequent queries without a restart.

#### Scenario: Threshold defaults to 0.70

- **WHEN** the service starts with default configuration
- **THEN** the confidence threshold is 0.70

#### Scenario: Threshold changed at runtime

- **WHEN** an admin sets the threshold to 0.85
- **THEN** the next query is evaluated against 0.85
- **AND** no restart is required
