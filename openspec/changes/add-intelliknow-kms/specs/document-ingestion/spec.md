## Purpose

The RAG write path: turns admin-uploaded documents into retrievable knowledge by extracting text and tables, chunking with structural awareness, embedding, and writing both a dense vector index and a keyword index — while keeping each document's processing state visible and correctable.

## ADDED Requirements

### Requirement: Supported upload formats

The system SHALL accept uploads in the formats listed in configuration — PDF, DOCX, and XLSX by default — and SHALL reject any other format with an error naming the accepted formats.

#### Scenario: PDF accepted

- **WHEN** an admin uploads a `.pdf` file
- **THEN** the upload is accepted and queued for processing

#### Scenario: DOCX accepted

- **WHEN** an admin uploads a `.docx` file
- **THEN** the upload is accepted and queued for processing

#### Scenario: XLSX accepted

- **WHEN** an admin uploads an `.xlsx` file
- **THEN** the upload is accepted and queued for processing

#### Scenario: Unsupported format rejected

- **WHEN** an admin uploads a file whose extension is not in the configured list
- **THEN** the upload is rejected with an error listing the accepted formats
- **AND** no document record is created

### Requirement: Upload validation

The system SHALL enforce the configured maximum upload size and SHALL reject a file whose content hash matches an already-indexed document.

#### Scenario: Oversized upload rejected

- **WHEN** an admin uploads a file larger than the configured limit
- **THEN** the upload is rejected with an error stating the limit

#### Scenario: Duplicate content rejected

- **WHEN** an admin uploads a file whose content hash matches an existing indexed document
- **THEN** the upload is rejected with an error naming the existing document
- **AND** no duplicate chunks are created

### Requirement: Asynchronous processing with visible status

The system SHALL accept an upload and return immediately, SHALL process it in the background, and SHALL expose a document status of `pending`, `parsing`, `indexed`, or `failed` throughout.

#### Scenario: Upload returns before processing completes

- **WHEN** an admin uploads a document
- **THEN** the request returns promptly with a document identifier and status `pending`

#### Scenario: Status progresses to indexed

- **WHEN** background processing completes successfully
- **THEN** the status becomes `indexed`
- **AND** the document reports its chunk count and indexed timestamp

#### Scenario: Interrupted work is made retryable on startup

- **WHEN** the service starts and a document is still marked `pending` or `parsing` from an earlier process
- **THEN** the document is marked `failed`
- **AND** its error message explains that processing was interrupted and may be retried

### Requirement: Structured document loading

The system SHALL extract each document into an ordered sequence of typed blocks — heading, paragraph, and table — each carrying a source reference identifying its page, paragraph, or sheet range of origin.

#### Scenario: PDF loaded into blocks

- **WHEN** a text-bearing PDF is loaded
- **THEN** its paragraphs and tables are produced as ordered blocks
- **AND** each block carries its page number as a source reference

#### Scenario: DOCX headings preserved

- **WHEN** a Word document containing headings is loaded
- **THEN** headings are produced as heading blocks distinct from paragraphs

#### Scenario: Excel sheets loaded per sheet

- **WHEN** a workbook with multiple sheets is loaded
- **THEN** each sheet produces its own table block
- **AND** each carries its sheet name and cell range as a source reference

### Requirement: Table structure preservation

The system SHALL render extracted tables into a text representation that preserves rows and columns, so that tabular and numeric content is both embeddable and keyword-searchable.

#### Scenario: Table rendered with structure intact

- **WHEN** a document containing a bordered table is processed
- **THEN** the table becomes text whose rows and columns remain distinguishable
- **AND** individual cell values are searchable rather than collapsed into an unordered run of text

### Requirement: AI-assisted recovery of poorly extracted tables

The system SHALL detect when deterministic table extraction produces a ragged result — inconsistent column counts across rows, or a majority of empty cells — and SHALL pass that region's raw text to the LLM provider with a schema requesting a clean structured table, using the result in place of the ragged extraction.

#### Scenario: Ragged table restructured

- **WHEN** table extraction produces rows with inconsistent column counts
- **THEN** the region is sent to the LLM provider for restructuring
- **AND** the returned structured table is used as the block content

#### Scenario: Restructuring failure falls back to raw text

- **WHEN** the LLM provider fails or returns an invalid structure for a ragged region
- **THEN** the raw extracted text is used instead
- **AND** the document still completes ingestion rather than failing

#### Scenario: Clean tables are not sent to the model

- **WHEN** table extraction produces a consistent, well-formed table
- **THEN** no LLM call is made for that region

### Requirement: Structure-aware chunking

The system SHALL split blocks into chunks of the configured target size with the configured overlap, SHALL never split a table row across chunks, SHALL keep a table smaller than 1.5 times the target size whole, SHALL prefix each chunk with the heading path it falls under, and SHALL not apply overlap across a heading boundary.

#### Scenario: Long text chunked with overlap

- **WHEN** a run of paragraphs substantially exceeds the target chunk size
- **THEN** multiple chunks are produced with overlapping content at their boundaries

#### Scenario: Table rows never split

- **WHEN** a table would fall across a chunk boundary
- **THEN** the boundary is adjusted so that no table row is divided

#### Scenario: Small table kept whole

- **WHEN** a table is larger than the target chunk size but under 1.5 times it
- **THEN** the table is stored as a single chunk

#### Scenario: Heading path carried into the chunk

- **WHEN** a chunk is created from content under a heading hierarchy
- **THEN** the chunk text is prefixed with that heading path
- **AND** the path is stored so it can be shown in citations

#### Scenario: Overlap does not cross headings

- **WHEN** a chunk boundary coincides with a heading boundary
- **THEN** no overlap content is carried across it

### Requirement: Chunk source references

The system SHALL record a source reference on every chunk identifying where in the document it came from, and SHALL make it available for citation.

#### Scenario: Source reference recorded

- **WHEN** a chunk is created
- **THEN** it stores the page, paragraph, or sheet reference of its originating blocks

### Requirement: Intent space assignment at ingest

The system SHALL classify each uploaded document using the LLM provider, presenting the configured spaces with their descriptions and a sample of the document's content, SHALL accept only a configured slug at or above the classification confidence threshold, and SHALL allow the admin to override an accepted assignment.

#### Scenario: Space suggested at upload

- **WHEN** a document finishes loading
- **THEN** an intent space is assigned from the model's suggestion
- **AND** the suggestion is visible to the admin

#### Scenario: Admin overrides the suggestion

- **WHEN** an admin reassigns a document to a different intent space
- **THEN** its chunks are moved to the destination space's vector index and their recorded space is updated
- **AND** the document is not re-parsed or re-embedded

#### Scenario: Suggestion unavailable

- **WHEN** the LLM provider fails during intent suggestion
- **THEN** the document is not assigned to General or indexed
- **AND** it is marked `failed` and `unclassified` with a retryable error

#### Scenario: Suggestion is below the confidence threshold

- **WHEN** the provider returns a configured intent with confidence below the threshold
- **THEN** the document is not indexed
- **AND** the error reports the returned and required confidence so the admin can review and retry

#### Scenario: Upload preflight fails before persistence

- **WHEN** the classification provider is unavailable when an upload is submitted
- **THEN** the upload request returns a retryable service-unavailable error
- **AND** no document row or uploaded file is created

### Requirement: Dual index writes

The system SHALL write every chunk to both the dense vector index of its intent space and the full-text keyword index, and SHALL keep both consistent with the stored chunk records.

#### Scenario: Chunk indexed in both stores

- **WHEN** a chunk is persisted
- **THEN** its embedding is added to its intent space's vector index
- **AND** its text is added to the keyword index

#### Scenario: Deletion removes from both stores

- **WHEN** a chunk is removed
- **THEN** it is removed from the vector index and from the keyword index

#### Scenario: Reassignment moves vector entries only

- **WHEN** a document's intent space changes
- **THEN** its vectors move between space indexes
- **AND** its keyword index entries remain valid with their recorded space updated

### Requirement: Batched embedding

The system SHALL generate chunk embeddings through the configured embedding provider in batches of the configured size rather than one call per chunk.

#### Scenario: Embedding calls batched

- **WHEN** a document produces many chunks
- **THEN** embeddings are requested in batches

### Requirement: Embedding model recorded at first ingest

The system SHALL record the embedding model name and vector dimension when the first document is indexed, and SHALL make that record available so that a later configuration mismatch can be detected.

#### Scenario: Model recorded on first ingest

- **WHEN** the first document is indexed
- **THEN** the embedding model name and dimension are persisted alongside the indexes

#### Scenario: Record updated by a full re-index

- **WHEN** a full re-index completes
- **THEN** the recorded model name and dimension reflect the currently configured model

### Requirement: Re-parsing an existing document

The system SHALL allow an admin to re-parse an already-ingested document, replacing all of its chunks and index entries with freshly generated ones while preserving its identity and intent space.

#### Scenario: Re-parse replaces prior chunks

- **WHEN** an admin re-parses an indexed document
- **THEN** its previous chunks and index entries are removed and replaced
- **AND** the document keeps its identifier and intent space

#### Scenario: Re-parse failure is visible

- **WHEN** re-parsing fails partway through
- **THEN** the status becomes `failed` with an error message
- **AND** the document remains listed so the admin can retry

### Requirement: Full re-index

The system SHALL provide an operation that re-embeds and re-indexes every document using the currently configured embedding model, without re-uploading the source files.

#### Scenario: Re-index rebuilds all indexes

- **WHEN** an admin runs a full re-index
- **THEN** every document's chunks are re-embedded with the configured model
- **AND** the vector and keyword indexes are rebuilt

### Requirement: Document deletion

The system SHALL allow an admin to delete a document, removing its chunks and index entries, while preserving query history that referenced it.

#### Scenario: Deletion removes chunks and index entries

- **WHEN** an admin deletes a document
- **THEN** its chunks and both index entries are removed
- **AND** it no longer appears in retrieval results

#### Scenario: History survives deletion

- **WHEN** a document that had been retrieved by past queries is deleted
- **THEN** existing query log entries remain intact

### Requirement: Ingestion error handling

The system SHALL capture any failure during loading, chunking, embedding, or indexing, SHALL set the document status to `failed` with a human-readable message, and SHALL leave the rest of the knowledge base unaffected.

#### Scenario: Corrupt file fails cleanly

- **WHEN** an uploaded file cannot be parsed
- **THEN** the status becomes `failed` with an explanatory message
- **AND** no partial chunks remain in either index

#### Scenario: Provider outage during embedding

- **WHEN** the embedding provider is unavailable while a document is processing
- **THEN** the status becomes `failed` with an error naming the provider failure
- **AND** other indexed documents remain searchable

#### Scenario: Scanned PDF with no extractable text

- **WHEN** a PDF yields no extractable text
- **THEN** the status becomes `failed` with a message stating that no text could be extracted and that scanned documents are not supported
