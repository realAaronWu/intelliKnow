## Purpose

Turns admin-uploaded documents into searchable knowledge: validating the upload, extracting text and tabular content, splitting it into chunks, generating embeddings, filing the result under an intent space, and keeping the document's processing state visible and correctable.

## ADDED Requirements

### Requirement: Supported upload formats

The system SHALL accept uploads in PDF (`.pdf`), Word (`.docx`), and Excel (`.xlsx`) formats, and SHALL reject any other format with an error naming the accepted formats.

#### Scenario: PDF accepted

- **WHEN** an admin uploads a `.pdf` file
- **THEN** the upload is accepted and queued for parsing

#### Scenario: DOCX accepted

- **WHEN** an admin uploads a `.docx` file
- **THEN** the upload is accepted and queued for parsing

#### Scenario: XLSX accepted

- **WHEN** an admin uploads an `.xlsx` file
- **THEN** the upload is accepted and queued for parsing

#### Scenario: Unsupported format rejected

- **WHEN** an admin uploads a file whose extension is not `.pdf`, `.docx`, or `.xlsx`
- **THEN** the upload is rejected with an error listing the accepted formats
- **AND** no document record is created

### Requirement: Upload validation

The system SHALL enforce a maximum upload size of 25 MB, SHALL verify that the file's detected content type matches its extension, and SHALL sanitize the filename before it is used on disk.

#### Scenario: Oversized upload rejected

- **WHEN** an admin uploads a file larger than 25 MB
- **THEN** the upload is rejected with an error stating the size limit

#### Scenario: Extension does not match content

- **WHEN** an uploaded file's detected content type does not match its extension
- **THEN** the upload is rejected with an error describing the mismatch

#### Scenario: Duplicate content detected

- **WHEN** an admin uploads a file whose content hash matches an existing indexed document
- **THEN** the upload is rejected with an error naming the existing document
- **AND** no duplicate chunks are created

### Requirement: Asynchronous processing with visible status

The system SHALL accept an upload and return immediately, SHALL process it in the background, and SHALL expose a document status of `pending`, `parsing`, `indexed`, or `failed` throughout.

#### Scenario: Upload returns before parsing completes

- **WHEN** an admin uploads a document
- **THEN** the request returns promptly with a document identifier and status `pending`
- **AND** the admin is not blocked while parsing proceeds

#### Scenario: Status progresses to indexed

- **WHEN** background processing completes successfully
- **THEN** the document status becomes `indexed`
- **AND** the document reports its chunk count and indexed timestamp

### Requirement: Text and table extraction

The system SHALL extract body text from every supported format, and SHALL additionally extract tabular content — PDF tables, DOCX tables, and XLSX sheets — rendering each table into a text representation that preserves its row and column structure.

#### Scenario: PDF body text extracted

- **WHEN** a text-bearing PDF is parsed
- **THEN** its body text is extracted and available for chunking

#### Scenario: PDF table preserved as structure

- **WHEN** a PDF containing a bordered table is parsed
- **THEN** the table is extracted as a structured text representation with its rows and columns intact
- **AND** the values are searchable rather than collapsed into an unordered run of text

#### Scenario: Excel sheets extracted per sheet

- **WHEN** a workbook with multiple sheets is parsed
- **THEN** each sheet is extracted as a separate structured region
- **AND** each region records its sheet name for citation

### Requirement: AI-assisted recovery of poorly extracted tables

The system SHALL detect when deterministic table extraction produces a ragged result — inconsistent column counts across rows, or a majority of empty cells — and SHALL pass that region's raw text to the LLM provider with a schema requesting a clean structured table, using the result in place of the ragged extraction.

#### Scenario: Ragged table is restructured

- **WHEN** table extraction produces rows with inconsistent column counts
- **THEN** the region is sent to the LLM provider for restructuring
- **AND** the returned structured table is used as the chunk text

#### Scenario: Restructuring failure falls back to raw text

- **WHEN** the LLM provider fails or returns an invalid structure for a ragged region
- **THEN** the raw extracted text is used instead
- **AND** the document still completes ingestion rather than failing

#### Scenario: Clean tables are not sent to the model

- **WHEN** table extraction produces a consistent, well-formed table
- **THEN** no LLM call is made for that region

### Requirement: Chunking

The system SHALL split extracted content into overlapping chunks of approximately 800 characters with approximately 100 characters of overlap, SHALL never split a table row across two chunks, and SHALL record a source reference for each chunk identifying its page, paragraph, or sheet of origin.

#### Scenario: Long text is chunked with overlap

- **WHEN** a document's extracted text substantially exceeds the chunk size
- **THEN** multiple chunks are produced with overlapping content at their boundaries

#### Scenario: Table rows stay intact

- **WHEN** a table region would fall across a chunk boundary
- **THEN** the boundary is adjusted so that no table row is split

#### Scenario: Source reference recorded

- **WHEN** a chunk is created from a PDF page, a DOCX paragraph, or an XLSX sheet
- **THEN** the chunk records a source reference naming that location
- **AND** the reference is available for citation in answers

### Requirement: Intent space assignment at ingest

The system SHALL suggest an intent space for each uploaded document using the LLM provider, presenting the available spaces with their descriptions and a sample of the document's content, and SHALL allow the admin to override the suggestion.

#### Scenario: Space suggested at upload

- **WHEN** a document finishes parsing
- **THEN** an intent space is assigned based on the model's suggestion
- **AND** the suggestion is visible to the admin

#### Scenario: Admin overrides the suggestion

- **WHEN** an admin reassigns a document to a different intent space
- **THEN** the document's chunks are moved to the destination space's index
- **AND** the document is not re-parsed or re-embedded

#### Scenario: Suggestion unavailable

- **WHEN** the LLM provider fails during intent suggestion
- **THEN** the document is assigned to the General space
- **AND** ingestion completes so the admin can reassign it manually

### Requirement: Embedding and indexing

The system SHALL generate an embedding for every chunk using the configured embedding provider, SHALL add each vector to the index belonging to the document's intent space, and SHALL batch embedding calls rather than issuing one call per chunk.

#### Scenario: Chunks embedded and indexed

- **WHEN** a document's chunks are generated
- **THEN** every chunk receives an embedding
- **AND** every vector is written to the index of the document's assigned intent space

#### Scenario: Embedding calls are batched

- **WHEN** a document produces many chunks
- **THEN** embeddings are requested in batches rather than individually

### Requirement: Re-parsing an existing document

The system SHALL allow an admin to re-parse an already-ingested document, replacing all of its chunks and vectors with freshly generated ones while preserving the document's identity and intent space assignment.

#### Scenario: Re-parse replaces prior chunks

- **WHEN** an admin re-parses an indexed document
- **THEN** its previous chunks and vectors are removed
- **AND** newly generated chunks and vectors replace them
- **AND** the document keeps its identifier and intent space

#### Scenario: Re-parse failure preserves the prior state

- **WHEN** re-parsing fails partway through
- **THEN** the document's status becomes `failed` with an error message
- **AND** the document remains listed so the admin can retry

### Requirement: Document deletion

The system SHALL allow an admin to delete a document, removing its chunks from the database and its vectors from the intent space index, while preserving historical query log entries that referenced it.

#### Scenario: Deletion removes chunks and vectors

- **WHEN** an admin deletes a document
- **THEN** its chunks are removed from the database
- **AND** its vectors are removed from the intent space index
- **AND** it no longer appears in retrieval results

#### Scenario: History survives deletion

- **WHEN** a document that had been retrieved by past queries is deleted
- **THEN** existing query log entries remain intact
- **AND** analytics can still report that the document was accessed historically

### Requirement: Ingestion error handling

The system SHALL capture any failure during parsing, chunking, embedding, or indexing, SHALL set the document status to `failed`, SHALL store a human-readable error message, and SHALL leave the rest of the knowledge base unaffected.

#### Scenario: Corrupt file fails cleanly

- **WHEN** an uploaded file cannot be parsed
- **THEN** the document status becomes `failed` with an explanatory message
- **AND** no partial chunks are left in the index

#### Scenario: Provider outage during embedding

- **WHEN** the embedding provider is unavailable while a document is being processed
- **THEN** the document status becomes `failed` with an error naming the provider failure
- **AND** other indexed documents remain searchable

#### Scenario: Scanned PDF with no extractable text

- **WHEN** a PDF yields no extractable text
- **THEN** the document status becomes `failed` with a message stating that no text could be extracted and that scanned documents are not supported

### Requirement: Embedding model consistency

The system SHALL record the embedding model name and vector dimension on first ingest, and SHALL refuse to start when the configured embedding model differs from the recorded one while indexed documents exist.

#### Scenario: Model recorded on first ingest

- **WHEN** the first document is indexed
- **THEN** the embedding model name and dimension are persisted

#### Scenario: Mismatched model blocks startup

- **WHEN** the service starts with an embedding model different from the recorded one and indexed documents exist
- **THEN** the service refuses to start
- **AND** the error names both models and directs the operator to re-index

#### Scenario: Re-index clears the mismatch

- **WHEN** an admin runs a full re-index
- **THEN** every document is re-embedded with the currently configured model
- **AND** the recorded model and dimension are updated
