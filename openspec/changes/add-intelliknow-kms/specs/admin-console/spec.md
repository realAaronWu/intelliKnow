## Purpose

Gives the administrator a single web interface to run the system — see its health, connect chat platforms, manage the document knowledge base, configure intent spaces, and read the analytics — laid out as the modular card-based dashboard the project brief's visual guidance describes.

## ADDED Requirements

### Requirement: Five core screens

The system SHALL provide an admin console with five screens — Dashboard, Frontend Integration, Knowledge Base Management, Intent Space Configuration, and Analytics — all reachable from a persistent top or side navigation menu.

#### Scenario: All screens reachable from navigation

- **WHEN** an admin signs in
- **THEN** all five screens are available from a persistent navigation menu
- **AND** each can be opened without leaving the console

### Requirement: Modular card layout and visual scheme

The system SHALL present each section as a card with rounded corners of 12 pixels and padding of 16 pixels under a clear heading, SHALL use a neutral light background, and SHALL give each module a distinct accent colour — blue for Frontend Integration, green for Knowledge Base, purple for Intent Space Configuration.

#### Scenario: Sections rendered as cards

- **WHEN** any screen is displayed
- **THEN** its sections appear as cards with 12-pixel rounded corners, 16-pixel padding, and clear headings

#### Scenario: Module accent colours applied

- **WHEN** an admin moves between modules
- **THEN** Frontend Integration is accented blue, Knowledge Base green, and Intent Space Configuration purple
- **AND** the page background remains a neutral white or light grey

#### Scenario: Primary actions are prominent

- **WHEN** a screen offers a primary action such as adding an integration, uploading a document, or creating an intent space
- **THEN** that action is visually prominent rather than buried among secondary controls

### Requirement: Admin sign-in

The system SHALL require a password before any screen is shown and SHALL keep the session authenticated until sign-out.

#### Scenario: Correct password grants access

- **WHEN** an admin enters the configured password
- **THEN** the console becomes accessible

#### Scenario: Incorrect password denied

- **WHEN** an incorrect password is entered
- **THEN** access is refused
- **AND** no screen content or configuration data is revealed

#### Scenario: Sign-out ends the session

- **WHEN** an admin signs out
- **THEN** the password is required again

#### Scenario: Backend rejects a missing admin token

- **WHEN** a request reaches any administrative API route without the configured bearer token
- **THEN** the request is rejected before the route handler runs
- **AND** no administrative data is returned

#### Scenario: Public channel endpoint remains separate

- **WHEN** a Bot Framework activity reaches the public Teams messaging endpoint
- **THEN** admin bearer authentication is not required
- **AND** Bot Framework authentication still applies

### Requirement: Console accesses data only through the backend API

The system SHALL have the console read and write all state through the backend HTTP API, and SHALL NOT have it access the database, the indexes, the configuration file, or the filesystem directly.

#### Scenario: Console is a pure API client

- **WHEN** the console displays or modifies any data
- **THEN** it does so through the backend API
- **AND** business rules are enforced by the backend rather than duplicated in the console

#### Scenario: Backend unreachable

- **WHEN** the backend API cannot be reached
- **THEN** the console shows a clear connection error rather than presenting stale data as current

### Requirement: Dashboard screen

The system SHALL provide a Dashboard screen summarising document and chunk counts, per-space document counts, per-channel connection status, recent query volume, the active AI provider and models, and the current threshold settings, and SHALL provide a "Try a query" box that runs a question through the full pipeline without involving a chat channel.

#### Scenario: Dashboard summarises system state

- **WHEN** an admin opens the Dashboard
- **THEN** knowledge base size, per-space document counts, channel statuses, recent query volume, and provider configuration are shown

#### Scenario: Dashboard highlights problems

- **WHEN** any channel is disconnected or any document failed processing
- **THEN** the Dashboard surfaces that condition prominently
- **AND** links to the screen where it can be resolved

#### Scenario: Try a query from the Dashboard

- **WHEN** an admin submits a question in the "Try a query" box
- **THEN** the detected intent space, confidence, answer, sources, and latency are displayed
- **AND** no chat channel is involved

### Requirement: Frontend Integration screen

The system SHALL provide a Frontend Integration screen presenting one card per supported chat tool, each showing a Connected or Disconnected status indicator, the configuration details including only the last four characters of the configured credential, and a test button that sends a sample query to verify the integration.

#### Scenario: One card per tool

- **WHEN** an admin opens Frontend Integration
- **THEN** Telegram and Microsoft Teams each appear as their own card

#### Scenario: Status indicator shown

- **WHEN** a channel has valid credentials and has completed an exchange
- **THEN** its card shows Connected
- **AND** a channel that is unconfigured or failing shows Disconnected with the recorded reason

#### Scenario: Credentials entered from the card

- **WHEN** an admin enters a channel's credentials on its card and saves
- **THEN** the credentials are stored encrypted
- **AND** the card redisplays them masked

#### Scenario: Credential shown only as last four characters

- **WHEN** a card displays its configuration details
- **THEN** only the last four characters of the credential are shown
- **AND** the full value is never displayed

#### Scenario: Credentials cleared with confirmation

- **WHEN** an admin clears a channel's credentials and confirms
- **THEN** the stored credentials are removed and the card shows Disconnected

#### Scenario: Test button verifies the integration

- **WHEN** an admin presses the test button on a card
- **THEN** a sample query is sent through the full pipeline and delivered to that channel
- **AND** the outcome and measured latency are displayed on the card

#### Scenario: Setup guidance for an unconfigured channel

- **WHEN** a channel has no credential configured
- **THEN** the card states what the platform requires and how to obtain the credential

### Requirement: Knowledge Base Management screen

The system SHALL provide a Knowledge Base Management screen listing documents in a table with columns for document name, upload date, format, size, status, and actions.

#### Scenario: Document table columns present

- **WHEN** an admin opens Knowledge Base Management
- **THEN** the table shows document name, upload date, format, size, status, and actions for each document

#### Scenario: Status values displayed

- **WHEN** a document is listed
- **THEN** its status reads Processed, Pending, or Error

#### Scenario: Row actions available

- **WHEN** an admin views a document row
- **THEN** actions to view, update, and delete that document are available

#### Scenario: View action shows document detail

- **WHEN** an admin chooses the view action
- **THEN** the document's assigned intent space, chunk count, and extracted chunks are shown

#### Scenario: Update action re-parses the document

- **WHEN** an admin chooses the update action
- **THEN** the document is re-parsed and re-indexed
- **AND** its status returns to Pending while processing

#### Scenario: Failure reason visible

- **WHEN** a document has status Error
- **THEN** its recorded error message is shown
- **AND** the admin can retry or delete it from the same screen

### Requirement: Document upload area

The system SHALL provide a prominent upload area accepting drag-and-drop or file selection, SHALL state the supported formats, and SHALL show a progress indicator while a document is being processed.

#### Scenario: Drag-and-drop upload

- **WHEN** an admin drags a supported file onto the upload area
- **THEN** the upload begins

#### Scenario: Supported formats stated

- **WHEN** an admin views the upload area
- **THEN** the accepted formats are stated in the interface

#### Scenario: Processing progress indicated

- **WHEN** a document is uploaded
- **THEN** a progress indicator reflects its processing state until it reaches Processed or Error

### Requirement: Document search and filter

The system SHALL allow documents to be found by name or keyword and filtered by format, upload date, and associated intent space.

#### Scenario: Search by name

- **WHEN** an admin types part of a document name into the search bar
- **THEN** only matching documents are listed

#### Scenario: Filter by format

- **WHEN** an admin filters by a document format
- **THEN** only documents of that format are listed

#### Scenario: Filter by intent space

- **WHEN** an admin filters by an intent space
- **THEN** only documents assigned to that space are listed

#### Scenario: Filter by upload date

- **WHEN** an admin filters by an upload date range
- **THEN** only documents uploaded in that range are listed

### Requirement: Intent Space Configuration screen

The system SHALL provide an Intent Space Configuration screen presenting each intent space with its name, description, number of associated documents, and reviewed classification accuracy when enough reviewed data exists, together with the query classification log and an editor form.

#### Scenario: Intent spaces shown as cards

- **WHEN** an admin opens Intent Space Configuration
- **THEN** each space appears with its name, description, associated document count, and reviewed classification accuracy or an unavailable state

#### Scenario: Editor form covers name, description, and keywords

- **WHEN** an admin creates or edits an intent space
- **THEN** a form provides fields for name, description, and classification keywords

#### Scenario: Query classification log present

- **WHEN** an admin opens Intent Space Configuration
- **THEN** a table of recent queries with detected intent space, classification confidence score, and response status is shown

#### Scenario: Protected space cannot be deleted

- **WHEN** an admin views the General space card
- **THEN** no delete action is offered
- **AND** the card states that General is a required protected space

#### Scenario: Thresholds adjustable

- **WHEN** an admin changes the confidence threshold or relevance floor and saves
- **THEN** the new values take effect for subsequent queries
- **AND** the screen confirms the saved values

#### Scenario: Keyword role explained

- **WHEN** an admin edits an intent space
- **THEN** the form states that description and keywords are used by the classifier and affect routing accuracy

### Requirement: Analytics screen

The system SHALL provide an Analytics screen with a period selector showing intent space distribution, most accessed documents, the query log, and a CSV export action.

#### Scenario: Metrics shown for a selected period

- **WHEN** an admin selects a period
- **THEN** intent space distribution and most accessed documents reflect that period

#### Scenario: Query log browsable and filterable

- **WHEN** an admin views the query log
- **THEN** entries are listed newest first and can be filtered by intent space and status
- **AND** selecting an entry shows the full answer, citations, and latency

#### Scenario: Export downloaded

- **WHEN** an admin triggers the CSV export
- **THEN** a CSV of the selected period is downloaded

#### Scenario: Empty period handled

- **WHEN** the selected period contains no queries
- **THEN** the screen shows an empty state rather than an error

### Requirement: Destructive actions confirmed

The system SHALL require explicit confirmation before deleting a document or an intent space.

#### Scenario: Deletion confirmed

- **WHEN** an admin triggers a destructive action
- **THEN** a confirmation is required before it proceeds
- **AND** cancelling leaves state unchanged

### Requirement: Actionable error feedback

The system SHALL surface backend validation and operation errors as readable messages naming what failed and what to do about it, rather than raw status codes or stack traces.

#### Scenario: Validation error displayed readably

- **WHEN** the backend rejects an admin action
- **THEN** the console displays the reason in plain language
- **AND** the admin's entered values are preserved for correction
