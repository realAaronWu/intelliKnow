# Test plan — IntelliKnow KMS

Covers the change `add-intelliknow-kms`: 92 requirements and 235 scenarios across 9 capabilities. Every scenario in `specs/*/spec.md` is written in WHEN/THEN form and is therefore directly executable as a test case; this plan says *how* each class of them gets executed, what has to exist first, and what "done" means.

## 1. The central problem: testing a system whose core is nondeterministic

Three of the four things this system does are LLM calls — intent classification, table restructuring, answer generation — and a fourth (embedding) is a model whose outputs are stable but opaque. A test suite that calls a real model is slow, costs money, fails offline, and gives different results on consecutive runs. A test suite that never calls a real model proves nothing about whether the system actually answers questions.

The plan splits accordingly:

| Layer | Model calls | Determinism | Runs |
| --- | --- | --- | --- |
| **L1 Unit** | None — fakes only | Fully deterministic | Every commit, < 30s |
| **L2 Integration** | Fakes, plus real local embeddings | Deterministic | Every commit, < 3 min |
| **L3 Model-quality** | Real LLM | Statistical, thresholded | On demand + before delivery |
| **L4 End-to-end / demo** | Real everything | Manual, scripted | Before delivery |

L1 and L2 verify *logic*: routing, fusion, gating, formatting, error handling. L3 verifies *quality*: does classification actually pick the right space, does retrieval actually find the right chunk. Conflating them is the most common way a RAG test suite ends up both flaky and uninformative.

## 2. Test doubles — the thing to build first

These are a prerequisite for almost every test below, so they are day-1 work (task 1.11), not an afterthought.

**`FakeLLMProvider`** implements `LLMProvider` with a scripted response queue:

```python
fake = FakeLLMProvider()
fake.expect_schema(IntentClassification, {"intent_slug": "hr", "confidence": 0.91, "reasoning": "..."})
fake.expect_text("Annual leave is 20 days. [S1]")
fake.fail_next(ProviderError.timeout)     # for error-path tests
```

It records every call so tests can assert on the *prompt* — that intent keywords were included, that the channel profile was passed, that retrieved chunks were delimited.

**`FakeEmbeddingProvider`** returns deterministic vectors derived from a hash of the input text, normalized, at a configurable dimension. Similarity between any two texts is then fixed and known, which lets relevance-gate and ranking tests assert exact scores. A `.set_vector(text, vec)` escape hatch lets a test place two chunks at chosen similarities.

**Real local embeddings in L2.** A small number of integration tests use the actual `all-MiniLM-L6-v2` model, because the fake cannot prove that semantically similar text actually retrieves. These are marked `@pytest.mark.slow` and excluded from the fast loop.

**Why this matters for the design under test:** the provider abstraction (`spec: ai-provider`) is what makes this possible at all. If prompts were built inline at call sites, none of the above would work. The test plan is a reason the abstraction earns its place, not just the config flexibility.

## 3. Fixtures and test data

**Sample documents** (`sample_docs/`, also the demo corpus — task 7.1). Each has *known* content so assertions can be exact:

| File | Format | Space | Contains, specifically |
| --- | --- | --- | --- |
| `employee_handbook.pdf` | PDF | HR | Headings; a **salary-grid table with merged cells** (bands L1–L5 with figures); an annual-leave clause stating "20 days" |
| `salary_bands.pdf` | PDF | HR | A clean bordered table — the control case against which the ragged-table fallback is compared |
| `nda_template.docx` | DOCX | Legal | Heading hierarchy, a numbered clause "Section 4.2", a table |
| `expense_policy.docx` | DOCX | Finance | Reimbursement limits, a "Form 16" reference |
| `budget_2026.xlsx` | XLSX | Finance | Three sheets, one with formulas, one with merged header cells |
| `corrupt.pdf` | PDF | — | Truncated bytes, for the parse-failure path |
| `scanned.pdf` | PDF | — | Image-only, zero extractable text |
| `duplicate.pdf` | PDF | — | Byte-identical copy of `salary_bands.pdf` |

**Golden question set** (`tests/data/questions.yaml`) — 40 labelled questions, ~8 per intent space, each tagged with its expected space and expected source document, plus 10 deliberately ambiguous ones labelled `expect_fallback`. This is the fixture that makes L3 measurable rather than anecdotal.

**Adversarial fixtures**: a question with a rare exact token that dense search alone misses (proves hybrid retrieval earns its place); a question about a topic in no document (no-match); a document containing text that looks like an instruction (grounding).

## 4. L1 — Unit tests

Deterministic, no I/O beyond a temp SQLite file. Grouped by the component boundaries in `design.md § Component duties`, which is what makes them unit-testable at all.

### 4.1 Configuration (`spec: configuration`)
- Valid file loads; every documented default is present.
- Out-of-range threshold, unknown field, malformed YAML → startup refused, error names the field.
- Missing file → defaults written and service starts.
- Atomic write: kill between temp-write and rename → original intact; backup file created.
- Runtime updates accept intent spaces, confidence threshold, and relevance floor only; restart-required and invalid updates leave both disk and memory unchanged.
- Embedding model change refused while `index_meta.json` records documents; permitted when empty.
- Effective-config read contains no secret values.

### 4.2 Provider layer (`spec: ai-provider`)
- `embed` returns N vectors for N inputs, in order, all of length `dimension`, all unit-norm.
- `complete` with schema returns a parsed object; malformed output retries once then raises `ProviderError`.
- Each error category maps correctly: timeout, rate-limit, auth, backend.
- Retry uses exponential backoff and stops at the configured maximum.
- Unknown provider name → startup refused, message lists valid values.
- Missing API key for a remote provider → startup refused, names the variable; both-local → starts clean.
- Classification and generation calls use their separately configured models.

### 4.3 Loaders and chunker (`spec: document-ingestion`)
- Each loader produces ordered typed blocks with correct source references (`p. 4`, `¶ 12`, `Sheet1!A1:F20`).
- DOCX headings emerge as heading blocks, not paragraphs.
- Table → markdown preserves row and column count.
- **Ragged detection**: inconsistent column counts and majority-empty cells both trigger the LLM path; a clean table does not (assert zero calls on the fake — this is the regression guard for cost).
- LLM restructuring failure → raw text used, ingestion continues.
- Chunker: overlap present within a block run; **no table row ever split** (assert on the merged-cell salary grid specifically); table under 1.5× target kept whole; heading path prefixed; no overlap across a heading boundary.
- Chunk boundaries are stable across runs for the same input.

### 4.4 Retrieval mechanics (`spec: knowledge-retrieval`)
- RRF: hand-computed fixture — chunk in both lists outranks an equally-ranked chunk in one; `k` change shifts ordering as expected; identical inputs give identical output.
- `keyword_top_n: 0` → dense-only path, no error.
- Relevance gate uses **dense cosine, not the fused score** — construct a case where fused rank is high but cosine is below the floor and assert no-match. (This is the assertion that protects the whole no-hallucination property.)
- ContextBuilder: near-duplicates dropped; ordering is document-then-ordinal, not score; budget enforced by dropping lowest-ranked; every chunk tagged with marker, title, source ref, heading path.
- CitationVerifier: `[S2]` resolves; `[S9]` when only 5 supplied is stripped; multi-document answers list each document once.

### 4.5 Orchestration (`spec: query-orchestration`)
- Threshold: above → classified space; below → General only with fallback logged; **exactly at threshold → classified space** (the boundary case).
- Classified General with high confidence → General only, fallback flag false.
- Unknown slug → retryable failure, anomaly recorded.
- Classification error and timeout → retryable failure, no retrieval or generation, failure logged.
- Classification prompt contains each space's name, description, **and keywords**; editing keywords changes the next prompt with no restart.
- Exact normalized reviewed question uses its expected intent and records `review`; bounded reviewed examples reach centroids and escalation prompts; deleted-intent labels are ignored.
- Retrieval receives an explicit space list and never computes it itself.

### 4.6 Formatting (`spec: knowledge-retrieval`, `frontend-integration`)
- Telegram: MarkdownV2 reserved characters escaped; a 5,000-char answer truncates under 4,096 at a word boundary with a visible marker.
- Teams: bullet lists render; length limit respected.
- Channel profile reaches the generation prompt (assert on the fake's recorded prompt).

### 4.7 Credential storage (`spec: frontend-integration`)
- Saved credential is Fernet-encrypted before SQLite persistence; the database contains ciphertext and no plaintext bundle.
- API and console reads return last-4 only; plaintext never crosses the API boundary.
- Missing or invalid encryption key rejects startup and never creates a plaintext fallback.
- Wrong-key or corrupted ciphertext → sanitized error and channel Disconnected.
- Save, replacement, clear, and restart preserve the documented channel behavior.

### 4.8 Logging (`spec: analytics-and-history`)
- Each status recorded correctly: `success`, `no_match`, `failed`.
- Log write happens **after** delivery (assert call ordering).
- Logging exception is swallowed — user still gets the answer.
- CSV export: header row correct; empty log exports headers only.

## 5. L2 — Integration tests

Real SQLite, real FAISS, real FTS5, real local embeddings; fake LLM.

### 5.1 Index consistency (`spec: document-ingestion`)
The invariant: `chunk` rows, FTS5 rows, and FAISS vectors always agree.
- Ingest → counts match across all three.
- Delete document → removed from all three; `query_log` history intact.
- Reassign space → vectors move between index files, chunk count unchanged, **no re-embedding** (assert zero embed calls).
- Re-parse → old chunks gone, new chunks present, document id and space preserved.
- Re-parse failure midway → status `failed`, no orphaned vectors.
- Full re-index → all documents re-embedded, `index_meta.json` updated.
- Delete an intent space with documents → refused with count; empty space → index file removed.
- FAISS round-trip: write, reload from disk, search returns identical results.

### 5.2 Hybrid retrieval (real embeddings)
- **Semantic**: "how much time off do I get" retrieves the annual-leave clause that shares no keywords.
- **Lexical**: "Band L4" retrieves the salary-grid row — and the same query with `keyword_top_n: 0` does *not*. This pair is the empirical justification for hybrid retrieval; if it ever passes in both configurations, the BM25 half is dead weight and should be reconsidered.
- **Isolation**: a Finance-routed query cannot return HR chunks, via either retriever.
- **Isolation**: an uncertain or failed classification invokes no retrieval function.
- **Empty**: query against a space with no chunks → no-match, no error.
- Cross-space scores are comparable (same embedding model, normalized vectors).

### 5.3 Full query pipeline (fake LLM, real retrieval)
- End-to-end through `POST /admin/test-query`: returns intent, confidence, answer, sources, latency, and delivers to no channel.
- Classification and embedding run concurrently — assert elapsed time is materially less than the sum of the two stubbed latencies.
- No-match path makes **zero** generation calls.
- Generation failure → user-facing message, status `failed`.

### 5.4 Admin API
- Document upload: accepted formats, oversize rejection, duplicate SHA-256 rejection, unsupported extension.
- Search by name; filter by format, date range, intent space; combined filters.
- Intent space CRUD: create, duplicate-slug rejection, keyword edit, General delete refused.
- Config update round-trip through the API.

### 5.5 Channel adapters (protocol-level, no live platform)
- Telegram: a captured `getUpdates` payload → correct `InboundMessage`; outbound call has correct chat id and escaped text; non-text update → "text only" reply and no pipeline run.
- Teams: a captured Bot Framework activity → correct normalization and reply shape.
- Inbound failure → platform still acknowledged, user still gets an error message.
- Status transitions Connected ↔ Disconnected with last-success time and last error.

## 6. L3 — Model-quality tests

Real LLM. Not run per-commit. These are the only tests that can tell you whether the system is *good*, as opposed to *correct*.

### 6.1 Classification accuracy
Run the 40-question golden set through classification. Report a confusion matrix over intent spaces.

- **Gate: ≥ 80% correct on the 30 unambiguous questions.** The brief requires a ≥70% *confidence threshold*, which is a different quantity — this is measured accuracy against human labels, and it needs a higher bar than the routing threshold to be meaningful.
- **Gate: ≥ 70% of the 10 ambiguous questions fall back** rather than being confidently misrouted. A classifier that is confidently wrong is worse than one that abstains.
- Report mean confidence for correct vs incorrect classifications. If they are not separated, the threshold mechanism is not doing useful work and `design § Decision 7`'s stated calibration caveat has become a real problem — record the finding either way.
- **Admin-guided tuning loop**: take the worst-performing space, add keywords or expected-intent labels from a training subset, then re-run a disjoint holdout set. Holdout accuracy should improve. This proves the feedback controls are useful without counting memorized exact repeats as generalization.

### 6.2 Answer quality
For each question with a known expected source document:
- **Gate: ≥ 85% of answers cite the expected document.**
- **Gate: 100% of citations resolve to a retrieved chunk** — CitationVerifier makes this structurally guaranteed, so any failure is a real bug, not a quality miss.
- **Grounding: 0 tolerance.** Ask 5 questions whose answers are absent from the corpus but adjacent to it; every one must produce no-match or an explicit "not covered". A fluent invented answer here is a release blocker.
- Table questions ("what does Band L4 pay") return the correct figure — verifying the entire salary-grid path from merged-cell extraction to citation.

### 6.3 Table restructuring
Ingest the merged-cell salary grid with the LLM fallback enabled and disabled. Compare retrieved content for a band-specific question. The fallback should produce a materially better answer; if it does not, the feature is not earning its cost and that should be recorded.

## 7. L4 — End-to-end and demo verification

Manual, scripted, run before delivery. This is the graded path.

**7.1 Cold start.** Fresh clone, `uv sync`, `.env` from example, both processes up. No `data/`. Console reachable; password gate rejects wrong password.

**7.2 Configuration.** Add Telegram credentials in the console → verify ciphertext in the DB, last-4 in the UI, Connected status. Repeat for Teams. Run each channel's test button; both report success with measured latency.

**7.3 Ingestion.** Upload all sample documents by drag-and-drop. Each reaches Processed. `corrupt.pdf` and `scanned.pdf` reach Error with readable messages. `duplicate.pdf` rejected naming the original. Progress indicator visible throughout. Search and each filter return correct subsets.

**7.4 Routing.** Ask the golden set from Telegram. Spot-check the classification log: detected space, confidence, and status correct. Ask an ambiguous question; confirm it fails as unclassified without retrieval and is visible in the log.

**7.5 Answers.** Verify citations on both channels; verify Telegram truncation on a deliberately long answer; verify Teams bullet rendering; verify the no-match message names the searched domain.

**7.6 Latency.** 20 queries per channel; record p50 and p95. **Gate: p95 ≤ 3s.** If missed, switch `model_classify` to a faster model and re-measure — this is the specified remedy, and the measurement is what tells you whether it was needed.

**7.7 Console.** All five screens; restrained module styling; intent views show document count and reviewed accuracy or `Not enough reviewed data`; Analytics shows distribution and most-accessed documents; CSV export opens and matches the log.

**7.8 Traceability.** Walk `traceability.md` end to end; confirm every clause is satisfied or listed as a deviation.

**7.9 Recovery.** Delete a document → gone from retrieval, present in history. Restart both processes → indexes reload, credentials still decrypt, history intact.

## 8. Coverage map

| Capability | Req | Scen | L1 | L2 | L3 | L4 |
| --- | ---: | ---: | :-: | :-: | :-: | :-: |
| configuration | 7 | 16 | ● | ○ | | ● |
| ai-provider | 6 | 15 | ● | | ○ | |
| document-ingestion | 16 | 38 | ● | ● | ○ | ● |
| intent-management | 9 | 22 | ● | ● | ○ | ● |
| query-orchestration | 9 | 17 | ● | ● | ● | ● |
| knowledge-retrieval | 11 | 29 | ● | ● | ● | ● |
| frontend-integration | 13 | 38 | ● | ● | | ● |
| analytics-and-history | 8 | 16 | ● | ○ | | ● |
| admin-console | 13 | 44 | | ○ | | ● |
| **Total** | **92** | **235** | | | | |

● primary coverage ○ partial

`admin-console` is deliberately L4-heavy: Streamlit UI is expensive to test automatically and cheap to verify by hand, and the console holds no business logic (`spec: admin-console` requires it to be a pure API client) — so the logic it would exercise is already covered at L1/L2 through the API.

## 9. Exit criteria

Delivery requires all of:

1. L1 and L2 green, no skips outside the `slow` marker.
2. L3 gates met: classification ≥80% / fallback ≥70% / citation-of-expected-document ≥85% / **zero ungrounded answers**.
3. L4 script completed with every step passing, p95 latency ≤ 3s on both channels.
4. Every `#### Scenario:` in `specs/` either has a corresponding test or is listed in § 10 with a reason.
5. `traceability.md` fully walked.

## 10. Scenarios not automated

Recorded so the gaps are deliberate and visible.

| Scenario area | Why | Compensating check |
| --- | --- | --- |
| Live Telegram and Teams delivery | Requires real platform accounts and network | L4 § 7.2, 7.4, 7.5 manual; adapters unit-tested against captured payloads |
| Bot Framework JWT validation | Owned by `botbuilder-core`, not our code | Captured invalid-token test plus emulator diagnostic and real Teams L4 round trip |
| Visual styling (radius, padding, colours) | Not meaningfully assertable without screenshot testing, which is disproportionate here | L4 § 7.7 visual check |
| Drag-and-drop interaction | Streamlit widget behaviour | L4 § 7.3 manual |
| Provider rate-limit behaviour under real load | Cannot be induced reliably or cheaply | L1 fake-injected `rate_limit` category |
| Concurrent multi-user querying | Out of scope — single-process MVP, no concurrency requirement in the brief | None |

## 11. Tooling

`pytest` with `pytest-asyncio`; markers `slow` (real embeddings) and `live` (real LLM, L3). Default run excludes both. Fixtures in `tests/conftest.py` provide a temp-directory app instance with fakes wired in, so no test touches developer state. FastAPI endpoints tested through `TestClient`. L3 emits a markdown report (`tests/reports/`) with the confusion matrix and gate results — that report is the evidence for the brief's "classification accuracy" deliverable, and doubles as source material for `docs/AI_USAGE.md`.
