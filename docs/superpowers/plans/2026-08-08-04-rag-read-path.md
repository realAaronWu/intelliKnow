# RAG Read Path and Orchestrator Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development. Steps use checkbox (`- [ ]`) syntax.
>
> **No implementation code in this plan.** Test expectations: `docs/superpowers/test-plans/04-rag-read-path-tests.md`.

**Goal:** Answer a question — classify it into an intent space, retrieve from that space with hybrid search, and generate a grounded, cited answer or an honest no-match.

**Architecture:** The orchestrator classifies and decides *which* spaces to search, then hands an explicit space list to retrieval, which never makes that decision itself. Retrieval runs dense and keyword search in parallel and fuses the rankings by reciprocal rank. A relevance gate decides whether to answer at all. Context assembly, generation, and citation verification follow.

**Tech Stack:** `faiss-cpu`, SQLite FTS5, the plan-01 provider layer, FastAPI

## Global Constraints

- All parameters from `config.yaml`.
- Retrieval never chooses spaces; the orchestrator supplies them.
- The relevance gate reads the **dense cosine similarity**, never the fused score.
- No answer is generated when the gate rejects — zero generation calls on the no-match path.
- Classification failure degrades to fallback, never to a user-visible error.
- L1 uses the fakes; L2 uses real FAISS/FTS5/embeddings with a fake LLM.

---

### Task 1: Dense retrieval

**Files:** Create `app/rag/retrieve/dense.py` · Test `tests/test_dense_retrieval.py`

**Interfaces:**
- Produces: `dense_search(query_vector, spaces: list[str], top_n: int, store: VectorStore) -> list[Hit]` where `Hit(chunk_id: int, score: float, source: Literal["dense","keyword"])`.

**Behaviour:**
- Searches only the supplied spaces. A chunk from an unsupplied space **cannot** appear — this is the isolation property the whole routing design rests on.
- Multi-space results merge into one list ranked by score.
- Scores are comparable across spaces because every index shares one embedding model.
- Spaces with no vectors contribute nothing and cause no error.

- [ ] Write failing tests · [ ] Confirm fail · [ ] Implement · [ ] Confirm green · [ ] Commit

---

### Task 2: Keyword retrieval

**Files:** Create `app/rag/retrieve/keyword.py` · Test `tests/test_keyword_retrieval.py`

**Interfaces:**
- Produces: `keyword_search(question: str, spaces: list[str], top_n: int, engine) -> list[Hit]`

**Behaviour:**
- BM25 over `chunk_fts`, filtered to the supplied spaces by SQL join.
- Rare exact tokens — a band name, a form number, a section reference — retrieve their chunk even when dense search ranks it poorly. **This is the capability the hybrid design exists for.**
- `top_n` of zero returns empty cleanly, disabling the keyword half without a code change.
- Query text is escaped so FTS5 operator characters in a user question cannot produce a syntax error.

- [ ] Write failing tests · [ ] Confirm fail · [ ] Implement · [ ] Confirm green · [ ] Commit

---

### Task 3: Reciprocal rank fusion

**Files:** Create `app/rag/retrieve/fuse.py` · Test `tests/test_fusion.py`

**Interfaces:**
- Produces: `fuse(dense: list[Hit], keyword: list[Hit], k: int, top_k: int) -> list[FusedHit]` where `FusedHit(chunk_id, fused_score, dense_score: float | None, keyword_rank: int | None)`.

**Behaviour:**
- Score is the sum over lists of `1 / (k + rank)`.
- A chunk present in both lists outranks an equally-ranked chunk present in one.
- **No score normalization anywhere.** Cosine and BM25 live on incomparable scales; any weighted blend needs corpus-specific constants that must be retuned whenever the corpus changes. Rank-only fusion needs none.
- `dense_score` is carried through unchanged, because the relevance gate needs it and the fused score cannot supply it.
- Deterministic: identical inputs give identical output, including tie ordering.

- [ ] Write failing tests · [ ] Confirm fail · [ ] Implement · [ ] Confirm green · [ ] Commit

---

### Task 4: Relevance gate

**Files:** Create `app/rag/retrieve/gate.py` · Test `tests/test_relevance_gate.py`

**Interfaces:**
- Produces: `passes_gate(hits: list[FusedHit], floor: float) -> bool`

**Behaviour:**
- Compares the **best dense cosine score** against the floor.
- **Must not use the fused score.** The fused score is rank-derived and unitless — it cannot express "nothing here is actually relevant", so gating on it would let a top-ranked-but-irrelevant chunk through. Construct the adversarial case explicitly: high fused rank, sub-floor cosine, and assert rejection.
- Hits with no dense score (keyword-only) do not satisfy the gate on their own.
- Empty hits fail the gate.

This one function is what stops a confident misroute from producing a fluent, wrong, fully-cited answer. Test it accordingly.

- [ ] Write failing tests · [ ] Confirm fail · [ ] Implement · [ ] Confirm green · [ ] Commit

---

### Task 5: Context builder

**Files:** Create `app/rag/context.py` · Test `tests/test_context_builder.py`

**Interfaces:**
- Produces: `build_context(hits, engine, cfg) -> ContextBundle` with `sources: list[Source]` and `prompt_block: str`; `Source(marker: str, chunk_id, document_id, document_title, source_ref, heading_path, text)`.

**Behaviour:**
- Near-duplicate chunks from the same document are dropped.
- Chunks are ordered by document then position — **not by score** — so the model reads them as written.
- Each is tagged with a stable marker plus title, source ref, and heading path.
- Total characters capped at the configured budget by dropping the lowest-ranked chunks.
- Chunk content is delimited as data in the prompt block.

- [ ] Write failing tests · [ ] Confirm fail · [ ] Implement · [ ] Confirm green · [ ] Commit

---

### Task 6: Answer generation

**Files:** Create `app/rag/generate.py` · Test `tests/test_answer_generation.py`

**Interfaces:**
- Produces: `generate_answer(question, bundle: ContextBundle, channel: ChannelProfile, llm) -> str`; `ChannelProfile(name, max_chars, markup, supports_lists)`.

**Behaviour:**
- The prompt instructs: answer only from supplied context, cite with the markers, say plainly when the context does not contain the answer.
- The channel profile — length limit and markup capability — goes into the prompt so the model writes to fit.
- Assert on the recorded prompt that both the context markers and the channel constraints are present.
- Provider failure raises; the caller turns it into a user-facing message.

- [ ] Write failing tests · [ ] Confirm fail · [ ] Implement · [ ] Confirm green · [ ] Commit

---

### Task 7: Citation verification

**Files:** Create `app/rag/citations.py` · Test `tests/test_citations.py`

**Interfaces:**
- Produces: `verify_citations(answer: str, bundle: ContextBundle) -> tuple[str, list[Citation]]`; `Citation(document_id, document_title, source_ref)`.

**Behaviour:**
- Markers are parsed and resolved against the supplied sources.
- **An unresolvable marker is stripped from the delivered answer** and contributes no citation. A confident answer citing a document that was never retrieved is the main failure mode of a small RAG system, and this check costs no extra model call.
- Each contributing document appears once, in first-cited order.
- An answer with no markers yields an empty citation list without error.

- [ ] Write failing tests · [ ] Confirm fail · [ ] Implement · [ ] Confirm green · [ ] Commit

---

### Task 8: Channel formatting

**Files:** Create `app/rag/format.py` · Test `tests/test_formatting.py`

**Interfaces:**
- Produces: `format_for_channel(answer: str, citations, profile: ChannelProfile) -> str`

**Behaviour:**
- Escapes characters reserved in the destination markup, so a message renders rather than failing to send.
- Truncates at a word boundary with a visible marker when over the limit.
- **The result is always within the channel limit** — the prompt-side fit in Task 6 is best-effort; this pass is the guarantee. A hard protocol limit must not be probabilistic.
- Citations render in a form appropriate to the channel.

- [ ] Write failing tests · [ ] Confirm fail · [ ] Implement · [ ] Confirm green · [ ] Commit

---

### Task 9: Intent classification

**Files:** Create `app/orchestrator/classify.py` · Test `tests/test_classify.py`

**Interfaces:**
- Produces: `classify(question: str, cfg: AppConfig, llm) -> Classification` with `Classification(intent_slug, confidence: float, reasoning: str, failed: bool)`.

**Behaviour:**
- One structured-output call returning slug, confidence, and reasoning.
- The prompt is built from the **current** configured spaces — name, description, and keywords. Assert all three appear in the recorded prompt; a keyword edit must change the next prompt with no restart.
- A slug matching no configured space is treated as below-threshold and the anomaly is recorded.
- Provider failure or timeout returns `failed=True` with the fallback slug — never raises to the user.
- Uses the classify-role provider, so the classify model applies.

- [ ] Write failing tests · [ ] Confirm fail · [ ] Implement · [ ] Confirm green · [ ] Commit

---

### Task 10: Routing decision

**Files:** Create `app/orchestrator/route.py` · Test `tests/test_routing.py`

**Interfaces:**
- Produces: `decide_spaces(classification, cfg) -> RoutingDecision` with `spaces: list[str]`, `logged_slug: str`, `fallback_used: bool`.

**Behaviour:**

| Condition | Spaces searched | `fallback_used` |
|---|---|---|
| confidence > threshold, slug ≠ fallback | that slug only | false |
| confidence **exactly equals** threshold | that slug only | false |
| confidence < threshold | all spaces | true |
| slug = fallback space, any confidence | all spaces | true |
| unknown slug | all spaces | true |
| classification failed | all spaces | true |

The exact-equality row is the boundary case most likely to be implemented wrong; the spec says meets-or-exceeds.

- [ ] Write failing tests · [ ] Confirm fail · [ ] Implement · [ ] Confirm green · [ ] Commit

---

### Task 11: Query pipeline

**Files:** Create `app/orchestrator/pipeline.py` · Test `tests/test_pipeline.py`

**Interfaces:**
- Produces: `answer_question(question, channel: ChannelProfile, deps) -> QueryOutcome` with `answer, citations, intent_slug, confidence, fallback_used, status, retrieved_doc_ids, latency_ms, error`.

**Behaviour:**
- **Classification and query embedding run concurrently** — the embedding does not depend on the classification, only the index selection does. Assert measured elapsed time is materially below the sum of two stubbed latencies; a sequential implementation fails this.
- Gate rejection produces `status="no_match"` with **zero generation calls**, and a message naming the searched domain.
- Generation failure produces `status="failed"` and a user-facing message.
- Success produces `status="success"` with verified citations and retrieved document ids.

- [ ] Write failing tests · [ ] Confirm fail · [ ] Implement · [ ] Confirm green · [ ] Commit

---

### Task 12: Admin test-query endpoint

**Files:** Create `app/api/query.py`; Modify `app/main.py` · Test `tests/test_query_api.py`

**Interfaces:**
- Produces: `POST /admin/test-query` → intent slug, confidence, answer, sources, latency.

**Behaviour:**
- Runs the full pipeline and **delivers to no chat channel**.
- This is the only path from the admin API into the orchestrator; it powers the Dashboard "Try a query" box and the per-channel connection test.
- Reports latency so an operator can verify the 3s budget on their own hardware.

- [ ] Write failing tests · [ ] Confirm fail · [ ] Implement · [ ] Confirm green · [ ] Commit

---

## Increment exit

1. Tasks 1–12 green.
2. **The hybrid justification test passes**: an exact-token question retrieves its chunk with keyword retrieval enabled and fails to with `keyword_top_n: 0`. If it passes both ways on the real corpus, BM25 is dead weight — report that rather than hiding it.
3. `superpowers:requesting-code-review`.
4. **Clean tester run** against `spec: query-orchestration` and `spec: knowledge-retrieval` (20 requirements, 46 scenarios).

## Self-review

**Spec coverage.** `spec: query-orchestration` and `spec: knowledge-retrieval` in full. Channel formatting (Task 8) also covers part of `spec: frontend-integration`.

**Type consistency.** `Hit` (Task 1) is produced by Tasks 1–2 and consumed by Task 3. `FusedHit` (Task 3) is consumed by Tasks 4–5. `ContextBundle` / `Source` (Task 5) are consumed by Tasks 6–7. `ChannelProfile` (Task 6) is used by Task 8 and supplied by plan 05's adapters. `VectorStore` comes from plan 03 unchanged.
