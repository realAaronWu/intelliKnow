# IntelliKnow KMS — Independent Code Review

**Reviewed:** `/Users/aaron/workspace/intelliKnow` @ `a15d354` (107 commits, 2026-08-08 → 2026-08-12)
**Source of truth:** `AD, Tech Lead, AKP.docx` — Tech Lead (Gen AI Focus) Interview Project Specification
**Reviewer stance:** requirement-first. Design docs and README were read for intent, but the brief decides what counts.

---

## 1. Verdict up front

**Hire — yes**, with one substantive reservation that I would raise in the offer conversation.

The core engineering is clearly above the bar for a Tech Lead: the RAG read path is thought through rather than copy-pasted, the test suite is adversarial rather than decorative, and the candidate is unusually honest about what has and has not been proven. Almost every functional requirement in §3.1 is implemented and demonstrable.

The reservation is scope discipline. The brief says twice — in the constraints and again in the tech-stack prohibition — *MVP-focused, no over-engineering, lightweight only, no cloud services*. The final commit spent its budget building an Azure Key Vault / macOS Keychain secret-management subsystem with versioning, staged rotation and an audit design, and in doing so **broke the ability to run the project on anything but macOS** — while three delivery acceptance gates in the candidate's own task list remained unchecked. For a role whose job is to stop other engineers from doing exactly that, this is the finding that matters most.

Detail follows.

---

## 2. What was verified, and how

| Check | Result |
| --- | --- |
| `uv run pytest` | **631 passed**, 2 deselected (slow), 4.85s |
| `openspec validate add-intelliknow-kms --strict` | **valid** |
| Live query history in `data/intelliknow.db` | 17 real queries: 13 Telegram, 1 Teams, 3 admin |
| Code read | all of `app/`, `streamlit_app.py`, `config.yaml`, `pyproject.toml`, openspec specs, all `docs/` |

Volume: ~9.4k lines `app/`, 933 lines Streamlit, ~13.9k lines tests, ~5.7k lines docs/specs. Test-to-code ratio is roughly 1.5:1 — appropriate, not padded.

---

## 3. Requirements coverage

### 3.1 Met, and met well

| Brief requirement | Evidence |
| --- | --- |
| Tech stack Option A (FastAPI/Streamlit + SQLite/FAISS) | Exactly this. No LangChain (deviation reasoned in `traceability.md`), no Docker requirement. |
| Document KB, 2+ formats | **3** — PDF, DOCX, XLSX. Structure-aware loaders preserving headings, pages, sheets, table geometry. |
| AI-powered parsing/structuring | `app/rag/tables.py` uses the LLM only for *ragged* table repair, schema-validated before entering the KB. This is the brief's own AI Scenario 1 (PDF salary grids), implemented as described. |
| Manual updates + re-parsing, reassign, delete | `app/ingest/lifecycle.py` + `/documents` API + KB screen actions. |
| Semantic search | Dense FAISS + FTS5 BM25 + reciprocal-rank fusion + cross-encoder rerank. |
| Intent spaces: 3 defaults + custom CRUD | 5 defaults (HR, Legal, Finance, Operations, General) + create/edit/delete via the console. |
| Configurable ≥70% confidence | `orchestrator.confidence_threshold: 0.7`, editable live from the Intent screen with no restart. |
| Admin-guided accuracy improvement | Keyword edits rebuild classification centroids on the very next query, no re-index. This is a genuinely elegant reading of the requirement. |
| Cited responses, "no match" messaging | `verify_citations` strips unresolvable markers; **zero surviving citations ⇒ `no_match`, generated text discarded.** Correct and non-obvious. |
| Adapt format to frontend | `ChannelProfile` per adapter; truncation reserves room for at least one verified source. This is the brief's AI Scenario 2. |
| Analytics & history + export | timestamp, channel, question, intent, confidence, status, latency, per-stage timings, citations, error; intent distribution, most-accessed documents, CSV export. |
| 5 admin screens | Dashboard, Frontend Integration, Knowledge Base, Intent Configuration, Analytics. |
| §2 visual guidance | 12px radius, 16px padding, neutral base, blue/green/purple accents, document table with the exact columns the brief lists, drag-drop upload with progress, search + format/intent/date filters, intent card view with doc count + accuracy, classification log, integration cards with `****last4` masking and a Test button. Close to a literal implementation of §2. |
| AI Usage Reflection | `docs/AI_USAGE.md` covers key moments, iteration speed, and adjustments to AI output. |

Three things deserve specific credit:

- **The relevance gate** (`app/rag/retrieve/gate.py`) compares the normalized cross-encoder score, never the rank-derived fusion score, with a test written specifically for the failure mode a naive implementation would pass. That is the single highest-leverage safeguard in a small RAG system and the candidate identified it.
- **Reviewed accuracy vs. confidence** are kept apart on purpose. The UI shows "Not enough reviewed data" rather than dressing up mean confidence as accuracy. The calibration report explicitly refuses to publish an accuracy number because no labelled set exists.
- **`traceability.md`** maps every clause of the brief to a spec, and separately lists what the brief does *not* require and was deliberately not built. Very few candidates produce this.

### 3.2 Not met

**(a) "Fallback to the General space" was removed — a direct requirement violation.**

`app/orchestrator/route.py` and `classify.py` raise `ClassificationError` on provider failure, invalid slug, or sub-threshold confidence. The user gets *"I couldn't classify that question reliably, so I did not search the knowledge base."* The brief states: *Classification Logic: AI-powered (≥70% configurable confidence), **fallback to "General" space***, under a heading marked **Non-Negotiable**.

The candidate documents this as a deliberate safety trade-off (design decision 12) and it is a defensible engineering opinion — searching the wrong domain confidently is worse than saying nothing. But two things weaken it:

- `traceability.md` § *Deviations* says *"Two remain, both confirmed with the project owner"* and this deviation is **not one of the two**. It is described in a different table with no sign-off recorded. A tech lead who removes a non-negotiable requirement needs the acceptance in writing, in the same place as the others.
- A safer compromise existed and was not taken: route to General *and* let the relevance gate produce the no-match. The gate already stops the bad answer. The requirement and the safety goal were not actually in conflict.
- `orchestrator.fallback_space: general` is still in `config.yaml`, still validated, and now silently means "the protected space that can't be deleted". `fallback_used` is `False` in every code path yet still ships in the DB schema, the API payloads, and the CSV export. Dead, misleading surface area.

**(b) The ≤3s latency requirement is not met.** From the live query log (real Telegram traffic, shipped `claude-haiku-4-5` config):

| Telegram end-to-end latency | |
| --- | --- |
| Samples | 13 |
| Over 3000 ms | **8 of 13** |
| Range | 2398 – 4946 ms |
| Median | ~3379 ms |

Stage breakdown from the recorded timings: generation 1.4–2.2s, Telegram delivery ~510ms, embedding up to 290ms — and **LLM classification escalation ~2.5s** whenever centroid confidence falls below threshold (query IDs 15–17: 2474/2693/2593 ms in `classification_routing` alone). The instrumentation is excellent and the README is honest — *"The three-second channel target … depends on the selected provider and model"* — but honesty is not compliance. The obvious mitigations (parallelize classification with retrieval, or skip escalation and route to General as the brief asked) were not taken.

**(c) Microsoft Teams was never proven against a real tenant.** The integrations table shows Teams with `credential_status = NULL` and a single 1580ms exchange whose delivery leg took **4ms** — the Bot Framework Emulator on localhost, not Teams. The candidate's own `tasks.md` is honest about it: task **5.8** (*verify real Telegram and real Teams round trips*) and **6.7** (*full demo, latency checks, five-view acceptance*) are unchecked, as is **2.3** (labelled routing question set).

Telegram *is* genuinely proven — 13 real round trips with ~510ms platform delivery. So the delivery requirement of *"working demo … with 2 frontend integrations"* is one-and-a-half met, not two. Teams is a hard target (Azure Bot registration, public HTTPS, tenant approval) and the brief did allow WhatsApp as an alternative; picking the two hardest-to-verify platforms was itself a scoping decision worth questioning.

Also minor: shipped `config.yaml` has `channels.teams.enabled: false`, so a fresh clone starts with one of the two required frontends off.

---

## 4. Over-engineering

The brief is unambiguous: *"MVP-focused (1-person workload); … no over-engineering"* and *"Prohibition: Complex frameworks/cloud services; lightweight only."*

**4.1 The secret-management subsystem (the main finding).**

The final commit replaced a *working* Fernet-encrypted credential store — which already satisfied "secure storage … API key last 4 digits" — with a `SecretStore` abstraction plus:

- `app/secrets/azure_key_vault.py` — Azure Key Vault via managed identity (**a cloud service**)
- `app/secrets/macos_keychain.py` — 123 lines of `ctypes` FFI into the macOS Security framework
- versioned references, staged replacement, one-way migration, bounded caching
- `docs/PRODUCTION-INTEGRATION-CREDENTIALS.md` — a 164-line production design covering rotation, rollback, emergency disable, audit records, "SQLite or PostgreSQL"
- `azure-identity` and `azure-keyvault-secrets` as **mandatory runtime dependencies** for every install

Tasks 7.4, 7.5 and 7.6 for this subsystem are unfinished. So the candidate started an out-of-scope enterprise feature, left it half-built, and did not finish the in-scope acceptance gates (5.8, 6.7, 2.3) instead.

**4.2 And it broke portability — the most damaging finding in this review.**

`SecretStoreConfig.provider` accepts only `macos-keychain` (default) or `azure-key-vault`. `MacOSKeychainSecretStore.__init__` raises `SecretStoreError("macOS Keychain is available only on macOS")` off Darwin, and `build_secret_store` is called unconditionally during `app.main:app` construction with no fallback.

Consequence: **clone the public repo on Linux or Windows, follow the README, and the API will not start.** The only alternative requires an Azure subscription. The README's Setup section says *"Requirements: Python 3.12 and `uv`"* and never mentions macOS. For a take-home whose delivery requirement is *"Public GitHub repo … working demo … detailed README (setup …)"*, this is the failure most likely to sink the submission before anyone reads the code. It was introduced by the last commit, so it is also a regression against a previously portable design.

**4.3 Repository bloat.** `demo-docs/` is 11MB. The brief asks for *2+ sample documents*; the repo ships 12, including a **6.7MB NVM Express 1.3 specification PDF** and ~5MB of scraped NVIDIA HTML/Markdown under `demo-docs/tech/source/` that the application never reads. Beyond weight, redistributing third-party specification and vendor documentation in a public repo is a licensing question a tech lead should have asked before committing.

**4.4 Smaller items.** The mkcert/HTTPS laptop-demo tooling (~470 lines of shell) is polished but past MVP. `reportlab` is a fixture-generation dependency declared as a runtime dependency. Every document upload pays an extra LLM preflight call before the file is even written, so a 10-file batch upload costs 10 additional round trips synchronously.

---

## 5. Design and implementation defects

| # | Finding | Severity |
| --- | --- | --- |
| 1 | `confidence_threshold` is applied to two incomparable scales: a temperature-0.05 softmax probability over centroid cosine similarities, and the LLM's *self-reported* confidence. Query 14 (`centroid`, 0.9926) and query 15 (`llm`, 0.85) are not on the same axis, yet both are gated by 0.7 and both feed `high_confidence_share`. | Medium |
| 2 | `MacOSKeychainSecretStore` / `build_secret_store` — no portable provider; startup fails off macOS (§4.2). | **High** |
| 3 | `fallback_space` / `fallback_used` are vestigial: the field name no longer matches its meaning, and the flag is hard-`False` everywhere while still exposed in the DB, API and CSV export. | Medium |
| 4 | `traceability.md` is stale: it says credentials are *"Fernet-encrypted at rest"* (now SecretStore), and lists *"a cross-encoder re-ranker"* among things deliberately **not** built — it is built, shipped, and loaded at startup. | Medium |
| 5 | `ChannelStore.load_credentials` recurses into itself after `_migrate_legacy_credentials`; if that migration's `UPDATE` matches 0 rows it disables the secret without raising, and the recursion never terminates. Narrow (needs a concurrent write) but unbounded. | Low |
| 6 | `pipeline._classification_failure_outcome` records `classified_by="llm"` for *any* classification-stage error, including embedding-provider failures and the escalation-disabled path. Corrupts the telemetry the analytics screen presents. | Low |
| 7 | `AdminService.analytics()` and `export_csv()` `SELECT` the entire `query_log` into memory with no cap. Fine at MVP scale, wrong by construction. | Low |
| 8 | Teams `/api/messages` skips Bot Framework authentication entirely when no credentials are stored and client host, request host and `service_url` all look local. Narrowly guarded and documented, but it is an unauthenticated public route by design. | Low |
| 9 | The 933-line Streamlit console has **zero** automated tests; there is no CI configuration in the repo at all. | Low |
| 10 | Shipped `config.yaml` contains a typo in the `tech` space keywords (`architecutre`) — it feeds the classification centroid. | Cosmetic |

---

## 6. Process observations

- **Commit hygiene is strong.** Messages are intent-first (`Fix FTS5 implicit-AND defect: keyword retrieval was dead for real questions`), increments are merged deliberately, and self-found defects are committed as explicit fixes with IDs traceable to review notes. This reads like someone who reviews their own work seriously.
- **Comment quality is unusually high.** Module docstrings explain *why* a decision was made and which spec clause it serves. `app/rag/retrieve/gate.py` and `app/rag/citations.py` are better documented than most production code.
- **Bookkeeping honesty.** Unfinished tasks are left unchecked rather than quietly ticked. The calibration report opens with *"Status: bounded sanity check only … No accuracy number is reported here; none should be trusted if one shows up elsewhere."* That is the right instinct.
- **But the plan was not defended.** The same discipline that produced `traceability.md` on day 1 did not prevent a day-5 pivot into Azure Key Vault while the acceptance gates sat unchecked. Writing the plan is the easy half.

---

## 7. If I were the interviewer

**Recommendation: Hire.**

What I would be buying: an engineer who reads a specification closely, builds the correct architecture on the first attempt, tests the failure modes rather than the happy path, and tells you the truth about what is unproven. The RAG core, the citation/relevance safeguards, and the accuracy-vs-confidence discipline are senior-level judgment that a lot of candidates would not reach in seven days.

What I would probe hard in the follow-up, in this order:

1. **"Walk me through the last commit."** Why Azure Key Vault in a project that prohibits cloud services, and did you notice it made the repo macOS-only? I want to hear the candidate catch it themselves. If they defend it, that is a real concern for a lead role — the job is to protect scope, not to gold-plate it.
2. **"You dropped the General fallback. Who signed off?"** The engineering argument is good; the change-control is not. A lead removing a non-negotiable requirement needs it recorded where the other deviations are.
3. **"Your Telegram median is 3.4s against a 3s requirement. What ships next?"** I want to hear "parallelize classification with retrieval, or drop the 2.5s escalation" — not "it depends on the provider."
4. **"Teams was never round-tripped in a tenant. Would you have picked Teams again?"** Tests whether they scope for verifiability.

I would rate the *building* as strong-hire and the *scoping judgment* as borderline. Because the role is Tech Lead, the second half carries real weight — but it is a correctable habit in someone this rigorous, and it presents as excess care rather than carelessness. I would extend the offer and make the scope-discipline conversation an explicit part of the first ninety days.

**Fastest fixes before this repo is shown to anyone else:** add a portable default secret store (or fall back to Fernet off macOS) so the project starts on Linux; drop the 11MB `demo-docs/tech/source/` tree; and reconcile `traceability.md` with what actually shipped.
