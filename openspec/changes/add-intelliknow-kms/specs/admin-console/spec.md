## Purpose

Gives the administrator a single web interface to run the system — see its health, connect chat platforms, manage the document knowledge base, configure intent spaces, and read the analytics — without touching the database, the filesystem, or the API directly.

## ADDED Requirements

### Requirement: Five admin screens

The system SHALL provide an admin console with five screens — Dashboard, Frontend Integrations, Knowledge Base, Intent Configuration, and Analytics — reachable from persistent navigation.

#### Scenario: All screens reachable

- **WHEN** an admin signs in to the console
- **THEN** all five screens are available from the navigation
- **AND** each screen can be opened without leaving the console

### Requirement: Admin authentication

The system SHALL require a password before any screen is shown, SHALL compare it in a way that does not leak timing information, and SHALL keep the session authenticated until sign-out.

#### Scenario: Correct password grants access

- **WHEN** an admin enters the configured password
- **THEN** the console becomes accessible

#### Scenario: Incorrect password denied

- **WHEN** an incorrect password is entered
- **THEN** access is refused
- **AND** no screen content or configuration data is revealed

#### Scenario: Sign-out ends the session

- **WHEN** an admin signs out
- **THEN** the password is required again before any screen is shown

### Requirement: Console accesses data only through the backend API

The system SHALL have the console read and write all state through the backend HTTP API, and SHALL NOT have it access the database, the vector indexes, or the filesystem directly.

#### Scenario: Console is a pure API client

- **WHEN** the console displays or modifies any data
- **THEN** it does so through the backend API
- **AND** business rules are enforced by the backend rather than duplicated in the console

#### Scenario: Backend unreachable

- **WHEN** the backend API cannot be reached
- **THEN** the console shows a clear connection error
- **AND** it does not present stale data as if it were current

### Requirement: Dashboard screen

The system SHALL provide a Dashboard screen showing document and chunk counts, per-space document counts, per-channel connection status, recent query volume, the active AI provider and models, and the current threshold settings.

#### Scenario: Dashboard summarizes system state

- **WHEN** an admin opens the Dashboard
- **THEN** knowledge base size, per-space document counts, channel statuses, recent query volume, and provider configuration are shown

#### Scenario: Dashboard highlights problems

- **WHEN** any channel is in an error state or any document failed ingestion
- **THEN** the Dashboard surfaces that condition prominently
- **AND** links to the screen where it can be resolved

### Requirement: Frontend Integrations screen

The system SHALL provide a Frontend Integrations screen where an admin can enter and update per-channel credentials, set the public base URL, enable or disable each channel, view per-channel status with last success time and last error, and run the end-to-end test.

#### Scenario: Credentials entered and saved

- **WHEN** an admin enters credentials for a channel and saves
- **THEN** the credentials are stored encrypted
- **AND** the screen redisplays them masked

#### Scenario: Test run from the screen

- **WHEN** an admin runs the end-to-end test for a channel
- **THEN** the result and the measured latency are displayed on the screen

#### Scenario: Setup guidance shown

- **WHEN** an admin views an unconfigured channel
- **THEN** the screen shows what that platform requires and the endpoint URL to register with it

### Requirement: Knowledge Base screen

The system SHALL provide a Knowledge Base screen for uploading documents, listing them with their intent space, status, chunk count, and upload time, reassigning a document's intent space, re-parsing, deleting, and viewing the error message of a failed document.

#### Scenario: Document uploaded from the screen

- **WHEN** an admin uploads a supported document
- **THEN** it appears in the list with status `pending`
- **AND** the status updates as processing proceeds without a manual page reload being required to eventually see completion

#### Scenario: Failure reason visible

- **WHEN** a document has failed ingestion
- **THEN** its status is shown as failed with its error message
- **AND** the admin can retry or delete it from the same screen

#### Scenario: Intent reassigned from the screen

- **WHEN** an admin changes a document's intent space
- **THEN** the change is applied and reflected in the list

#### Scenario: Document deleted from the screen

- **WHEN** an admin deletes a document and confirms
- **THEN** it is removed from the list and from retrieval

### Requirement: Intent Configuration screen

The system SHALL provide an Intent Configuration screen listing every intent space with its description and document count, allowing creation, editing, and deletion of custom spaces, and allowing the confidence threshold and relevance floor to be adjusted.

#### Scenario: Spaces listed with usage

- **WHEN** an admin opens Intent Configuration
- **THEN** every space is listed with its description and the number of documents assigned to it

#### Scenario: Threshold adjusted from the screen

- **WHEN** an admin changes the confidence threshold and saves
- **THEN** the new value takes effect for subsequent queries
- **AND** the screen confirms the saved value

#### Scenario: Protected space cannot be deleted

- **WHEN** an admin views the General space
- **THEN** no delete action is offered
- **AND** the screen explains that General is the required fallback space

#### Scenario: Description role explained

- **WHEN** an admin edits a space description
- **THEN** the screen states that the description is used by the classifier and affects routing accuracy

### Requirement: Analytics screen

The system SHALL provide an Analytics screen with a date range selector showing intent distribution, confidence distribution, fallback rate, measured classification accuracy, most-accessed documents, unused documents, no-match rate and top no-match questions, latency statistics, the filterable query history, and a CSV export action.

#### Scenario: Metrics shown for a selected period

- **WHEN** an admin selects a date range
- **THEN** every metric on the screen reflects that range

#### Scenario: History reviewed and corrected

- **WHEN** an admin reviews a logged query
- **THEN** the classification can be marked correct or incorrect from the screen
- **AND** an incorrect classification can be assigned the space it should have used

#### Scenario: Export downloaded

- **WHEN** an admin triggers the CSV export
- **THEN** a CSV of the selected period is downloaded

#### Scenario: Empty period handled

- **WHEN** the selected period contains no queries
- **THEN** the screen shows an empty state rather than an error

### Requirement: Destructive actions confirmed

The system SHALL require explicit confirmation before deleting a document, deleting an intent space, or clearing stored credentials.

#### Scenario: Deletion confirmed

- **WHEN** an admin triggers a destructive action
- **THEN** a confirmation is required before it proceeds
- **AND** cancelling leaves state unchanged

### Requirement: Actionable error feedback

The system SHALL surface backend validation and operation errors in the console as readable messages naming what failed and what to do about it, rather than raw status codes or stack traces.

#### Scenario: Validation error displayed readably

- **WHEN** the backend rejects an admin action
- **THEN** the console displays the reason in plain language
- **AND** the admin's entered values are preserved for correction
