# RAG Write Path Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development. Steps use checkbox (`- [ ]`) syntax.
>
> **No implementation code in this plan.** Test expectations: `superpowers/test-plans/03-rag-write-path-tests.md`.

**Goal:** Turn an uploaded document into retrievable knowledge — loaded into structured blocks, chunked with structural awareness, embedded, and written to both a dense vector index and a keyword index.

**Architecture:** A pipeline of single-responsibility components. `DocumentLoader` produces typed blocks with provenance; `StructuralChunker` packs blocks under structural rules; `Embedder` batches through the provider layer; `IndexWriter` keeps three stores consistent. An ingestion worker drives them in the background so uploads return immediately.

**Tech Stack:** `pypdf`, `pdfplumber`, `python-docx`, `openpyxl`, `faiss-cpu`, `reportlab`, SQLAlchemy, FastAPI

**Scheduling note:** increment 02 (test corpus) was deferred by the project owner — the real-world corpus fetch is not needed until the day-7 accuracy run. Its **Task 1 (synthetic fixture generator) is pulled forward into this plan as Task 0**, because every loader and chunker test here asserts against those fixtures' known content. Fixtures are script-generated, offline, and free; only the network fetch is deferred.

## Global Constraints

- All parameters from `config.yaml` — never hard-coded.
- Vectors unit-normalized; FAISS `IndexFlatIP` (exact, no ANN) wrapped for id mapping, one index file per intent space.
- The `chunk` table, the `chunk_fts` keyword index, and the FAISS index must never disagree.
- Ingestion failures are captured per-document; one bad file never affects the rest of the knowledge base.
- L1 tests use `FakeLLMProvider` / `FakeEmbeddingProvider`. L2 tests may use real FAISS, FTS5, and local embeddings.

---

### Task 0: Synthetic document fixtures (pulled forward from plan 02)

**Files:** Create `scripts/make_fixtures.py`, `tests/fixtures/docs/` (generated, committed) · Test `tests/test_fixtures.py`

**Interfaces:** Produces `build_all(out_dir: Path) -> list[Path]` and module-level constants (`SALARY_BANDS`, `ANNUAL_LEAVE_DAYS`, …) that tests import rather than duplicating.

**Behaviour:** see `superpowers/plans/2026-08-08-02-test-corpus.md` § Task 1 for the nine-document table and the byte-reproducibility requirement. Test expectations are in `superpowers/test-plans/02-test-corpus-tests.md` § 1.

**Byte reproducibility is a hard requirement** — fix document metadata and suppress embedded timestamps. Without it `duplicate.pdf` stops matching `salary_bands.pdf` and the duplicate-rejection test in Task 9 becomes flaky.

- [ ] Write failing tests · [ ] Confirm fail · [ ] Implement · [ ] Confirm green · [ ] Commit

---

### Task 1: Block model and loader interface

**Files:** Create `app/rag/blocks.py`, `app/rag/loaders/__init__.py` · Test `tests/test_blocks.py`

**Interfaces:**
- Produces: `Block(kind: Literal["heading","paragraph","table"], text: str, source_ref: str, heading_level: int | None)`; `DocumentLoader` protocol — `load(path: Path) -> list[Block]`; `LoaderError(Exception)`.

**Behaviour:** blocks are ordered and each carries the provenance string that will appear in citations. `heading_level` is set on headings only.

- [ ] Write failing tests · [ ] Confirm fail · [ ] Implement · [ ] Confirm green · [ ] Commit

---

### Task 2: PDF loader

**Files:** Create `app/rag/loaders/pdf.py` · Test `tests/test_pdf_loader.py`

**Behaviour:**
- Body text via `pypdf`, tables via `pdfplumber`, merged into one ordered block list.
- Source refs read `p. N`, 1-indexed.
- Tables render to markdown preserving row and column structure.
- A PDF yielding no extractable text raises `LoaderError` naming that scanned documents are unsupported — this must be distinguishable from a corrupt file.
- A corrupt file raises `LoaderError` without leaving partial state.

Validate against `handbook.pdf`, `salary_bands.pdf`, `scanned.pdf`, `corrupt.pdf` from plan 02.

- [ ] Write failing tests · [ ] Confirm fail · [ ] Implement · [ ] Confirm green · [ ] Commit

---

### Task 3: DOCX and XLSX loaders

**Files:** Create `app/rag/loaders/docx.py`, `app/rag/loaders/xlsx.py` · Test `tests/test_docx_loader.py`, `tests/test_xlsx_loader.py`

**Behaviour:**
- DOCX: headings emerge as `heading` blocks with their level, not as paragraphs — the heading path depends on this. Tables become `table` blocks. Source refs read `¶ N`.
- XLSX: each sheet produces at least one `table` block; source refs read `Sheet1!A1:F20`. Formula cells contribute their computed value, not the formula text.

- [ ] Write failing tests · [ ] Confirm fail · [ ] Implement · [ ] Confirm green · [ ] Commit

---

### Task 4: Ragged table detection and AI restructuring

**Files:** Create `app/rag/tables.py` · Test `tests/test_table_repair.py`

**Interfaces:**
- Produces: `is_ragged(rows: list[list[str]]) -> bool`; `repair_table(raw_text: str, llm: LLMProvider) -> str`.

**Behaviour:**
- Ragged when column counts differ across rows, or when a majority of cells are empty. This is what merged-cell salary grids produce.
- Ragged regions go to the LLM with a schema requesting a clean table; the result replaces the extraction.
- **A clean table must make zero LLM calls** — assert on the fake's call count. This is the cost regression guard.
- Provider failure or invalid structure falls back to the raw text; ingestion still completes.

This is the brief's first named AI usage scenario. Validate against `ragged_salary_grid.pdf` and the control `salary_bands.pdf`.

- [ ] Write failing tests · [ ] Confirm fail · [ ] Implement · [ ] Confirm green · [ ] Commit

---

### Task 5: Structural chunker

**Files:** Create `app/rag/chunker.py` · Test `tests/test_chunker.py`

**Interfaces:**
- Produces: `Chunk(ordinal, text, heading_path, source_ref, char_count)`; `chunk_blocks(blocks: list[Block], cfg: RAGConfig) -> list[Chunk]`.

**Behaviour:**
- Target size and overlap from config.
- **No table row is ever split across chunks.** Assert against the salary grid specifically.
- A table under 1.5× target stays whole even when oversized.
- Each chunk is prefixed with its heading path, so the embedding carries context the raw sentence lacks and the citation can show origin.
- Overlap applies within a block run, never across a heading boundary — bleeding the end of Legal into the start of Finance is worse than a short chunk.
- Chunk boundaries are stable across runs for identical input.

- [ ] Write failing tests · [ ] Confirm fail · [ ] Implement · [ ] Confirm green · [ ] Commit

---

### Task 6: FAISS index store

**Files:** Create `app/rag/vector_store.py` · Test `tests/test_vector_store.py`

**Interfaces:**
- Produces: `VectorStore(faiss_dir: Path, dimension: int)` with `create_space(slug)`, `add(slug, ids, vectors)`, `remove(slug, ids)`, `move(from_slug, to_slug, ids)`, `search(slug, vector, top_n) -> list[tuple[int, float]]`, `delete_space(slug)`, `persist(slug)`, `load(slug)`.

**Behaviour:**
- One index file per intent space; exact inner-product search over normalized vectors, so scores are cosine similarities directly comparable across spaces.
- Round-trip: write, reload from disk, identical search results.
- `move` transfers vectors between space indexes without re-embedding.
- Searching a space with no vectors returns empty, not an error.

- [ ] Write failing tests · [ ] Confirm fail · [ ] Implement · [ ] Confirm green · [ ] Commit

---

### Task 7: Index metadata and embedding immutability

**Files:** Create `app/rag/index_meta.py` · Test `tests/test_index_meta.py`

**Interfaces:**
- Produces: `read_meta(faiss_dir) -> IndexMeta | None`; `write_meta(faiss_dir, model, dimension)`; `assert_compatible(cfg, faiss_dir) -> None`.

**Behaviour:**
- Model name and dimension recorded at first ingest, in `data/index_meta.json`.
- `assert_compatible` raises when the configured model differs from the recorded one and documents exist; the message names both models and points at re-index.
- No recorded meta, or an empty index, permits any model.
- A full re-index updates the record.

**Why this matters:** vectors from different models are not comparable, and cross-space score comparison depends on one shared model. Changing the model silently degrades every answer with no error — the worst failure mode available. This check converts it into a loud startup failure.

- [ ] Write failing tests · [ ] Confirm fail · [ ] Implement · [ ] Confirm green · [ ] Commit

---

### Task 8: Index writer

**Files:** Create `app/rag/index_writer.py` · Test `tests/test_index_writer.py`

**Interfaces:**
- Produces: `IndexWriter(engine, vector_store, embedder)` with `write_document(doc_id, slug, chunks)`, `remove_document(doc_id)`, `reassign_document(doc_id, new_slug)`.

**Behaviour:**
- Every chunk lands in three places: the `chunks` row, the `chunk_fts` row, the space's FAISS index.
- **The three stores must never disagree** — this invariant is the task's whole reason to exist, and every test here asserts counts across all three.
- Removal clears all three; history in `query_log` is untouched.
- Reassignment moves vectors and updates the recorded space **without re-embedding** — assert zero embed calls.
- Embedding is batched at the configured size.

- [ ] Write failing tests · [ ] Confirm fail · [ ] Implement · [ ] Confirm green · [ ] Commit

---

### Task 9: Upload validation and intent suggestion

**Files:** Create `app/ingest/validate.py`, `app/ingest/classify_doc.py` · Test `tests/test_upload_validation.py`, `tests/test_doc_intent.py`

**Behaviour:**
- Extension allowlist and size cap from config; rejection messages name the accepted formats and the limit.
- SHA-256 duplicate rejection names the existing document.
- Intent suggestion prompts the LLM with configured space names, descriptions, and keywords plus the document's first 2000 characters.
- Provider failure defaults to the fallback space and lets ingestion complete, so the admin can reassign by hand.

- [ ] Write failing tests · [ ] Confirm fail · [ ] Implement · [ ] Confirm green · [ ] Commit

---

### Task 10: Ingestion worker

**Files:** Create `app/ingest/worker.py` · Test `tests/test_ingest_worker.py`

**Interfaces:**
- Produces: `ingest_document(doc_id: int, path: Path, deps) -> None`, driving status `pending → parsing → indexed | failed`.

**Behaviour:**
- Full pipeline: load → repair tables → chunk → suggest intent → embed → index → status `indexed` with chunk count and timestamp.
- Any stage failure sets status `failed` with a human-readable message and **leaves no partial chunks or vectors behind**.
- The document row survives a failure so the admin can retry or delete.
- Failure of one document leaves other indexed documents searchable.

- [ ] Write failing tests · [ ] Confirm fail · [ ] Implement · [ ] Confirm green · [ ] Commit

---

### Task 11: Document lifecycle operations

**Files:** Create `app/ingest/lifecycle.py` · Test `tests/test_doc_lifecycle.py`

**Behaviour:**
- **Re-parse**: replaces all chunks and index entries; document keeps its id and intent space; failure mid-way sets `failed` without orphaned vectors.
- **Reassign**: moves vectors, no re-parse, no re-embed.
- **Delete**: clears chunks and both indexes; `query_log` rows referencing it survive.
- **Full re-index**: re-embeds every document with the currently configured model and updates the index metadata.

- [ ] Write failing tests · [ ] Confirm fail · [ ] Implement · [ ] Confirm green · [ ] Commit

---

### Task 12: Document admin API

**Files:** Create `app/api/documents.py`; Modify `app/main.py` · Test `tests/test_documents_api.py`

**Behaviour:**
- `POST /documents` accepts multipart, validates, inserts `pending`, returns 202 immediately with the id, and schedules background ingestion.
- `GET /documents` lists with search by name or keyword and filters by format, upload date range, and intent space, combinable.
- `GET /documents/{id}` returns detail including intent space, chunk count, status, error message, and extracted chunks.
- `POST /documents/{id}/reparse`, `PATCH /documents/{id}` (intent reassignment), `DELETE /documents/{id}`, `POST /documents/reindex`.
- Validation failures return actionable messages, not bare status codes.

- [ ] Write failing tests · [ ] Confirm fail · [ ] Implement · [ ] Confirm green · [ ] Commit

---

### Task 13: Demo CLI

**Files:** Create `scripts/ingest.py`

**Behaviour:** the project owner needs something runnable at the end of this increment. A CLI that takes one or more file paths, runs them through the real ingestion pipeline, and prints per document: status, assigned intent space, chunk count, and the first few chunks with their heading path and source ref. Then prints the totals across all three stores (chunk rows, FTS5 rows, FAISS vectors per space) so the three-stores-agree invariant is visible rather than only asserted in tests.

It must work with the shipped local-default config and no API key — intent suggestion falls back to the fallback space when no LLM is reachable, which is already the specified behaviour.

- [ ] Build · [ ] Run against `tests/fixtures/docs/` · [ ] Commit

---

## Increment exit

1. Tasks 1–12 tests green, including L2 tests against real FAISS, FTS5, and local embeddings.
2. All nine plan-02 fixtures ingest with the expected outcome — six indexed, `corrupt.pdf` and `scanned.pdf` failed with distinguishable messages, `duplicate.pdf` rejected.
3. `superpowers:requesting-code-review` across the branch.
4. **Clean tester run** against `spec: document-ingestion` (16 requirements, 38 scenarios) per `TESTER-PROTOCOL.md`.

## Self-review

**Spec coverage.** `spec: document-ingestion` in full. Partial: `spec: intent-management` (per-space index lifecycle, Tasks 6 and 11) and `spec: configuration` (embedding immutability, Task 7).

**Type consistency.** `Block` (Task 1) is produced by Tasks 2–3 and consumed by Tasks 4–5. `Chunk` (Task 5) is consumed by Task 8. `VectorStore` (Task 6) is used by Tasks 8 and 11 and by plan 04's retriever. `RAGConfig`, `LLMProvider`, `EmbeddingProvider`, and the four tables all come from plan 01 unchanged.
