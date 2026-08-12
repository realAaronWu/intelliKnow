## Context

Greenfield project, empty repository, one developer. See `proposal.md` — Why for motivation.

Fixed constraints going in:

| Constraint | Source |
| --- | --- |
| Lightweight stack only — no managed cloud services, no heavy frameworks | Project brief |
| Two chat frontends, ≥2 document formats, ≥3 intent spaces | Project brief |
| Query round-trip ≤ 3s | Project brief |
| Classification confidence threshold configurable, default ≥ 0.70 | Project brief |
| Python + FastAPI + Streamlit + SQLite + FAISS | Chosen (Option A) |
| Pluggable AI provider layer (Anthropic / OpenAI / local) | Chosen |
| Telegram + Microsoft Teams | Chosen |
| Local Docker Compose + cloudflared tunnel | Chosen |

Two constraints are in tension and shape most of what follows. The ≤3s budget has to absorb *two* sequential model calls (classify, then generate), and the "no over-engineering" instruction argues against the provider abstraction that was nonetheless explicitly requested. The resolutions are a two-model configuration split (§ Decision 8) and a deliberately narrow two-method provider interface (§ Decision 1).

## Goals / Non-Goals

**Goals:**

- One synchronous request path from chat message to cited answer, with every stage individually observable in the query log.
- Intent routing that is *visible* — an admin can look at any logged query and see which space it went to, how confident the classifier was, and whether the fallback fired.
- Swapping AI backends is a configuration change, not a code change.
- The whole system comes up with `docker compose up` plus a `.env` file.

**Non-Goals:**

- Multi-tenancy, RBAC, or per-user document permissions. One admin, one knowledge base, any chat user may query.
- Conversational memory. Each query is answered independently; no follow-up context.
- Streaming responses. Chat adapters send one complete message.
- Incremental/partial re-indexing. Re-parsing a document rebuilds all of its chunks.
- Automatic document sync from Drive/SharePoint. Upload is manual.
- Horizontal scale. Single process, single FAISS index set, in-process locking.

## Architecture

```
   Telegram user                      Teams user
        │                                  │
        │ Bot API                          │ Bot Framework
        ▼                                  ▼
  ┌──────────────────── cloudflared tunnel ────────────────────┐
  │                  public HTTPS ingress                      │
  └────────────────────────────┬───────────────────────────────┘
                               ▼
  ╔════════════════════ FastAPI service (:8000) ═══════════════════════╗
  ║                                                                    ║
  ║  ┌───────────────────┐          ┌──────────────────────────────┐  ║
  ║  │ Channel Adapters  │          │       Admin REST API         │  ║
  ║  │  • TelegramAdapter│          │  /intents /documents         │  ║
  ║  │  • TeamsAdapter   │          │  /integrations /analytics    │  ║
  ║  └─────────┬─────────┘          └───────┬──────────────────────┘  ║
  ║            │ normalized InboundMessage  │                          ║
  ║            ▼                            │                          ║
  ║  ┌───────────────────┐                  │                          ║
  ║  │   Orchestrator    │◄─────────────────┘                          ║
  ║  │ classify → route  │                                             ║
  ║  └────┬─────────┬────┘                                             ║
  ║       │         │                                                  ║
  ║       ▼         ▼                                                  ║
  ║  ┌─────────┐  ┌──────────────┐    ┌───────────────────────────┐   ║
  ║  │Retrieval│  │Intent Service│    │    Ingestion Pipeline     │   ║
  ║  │ + Answer│  └──────────────┘    │ parse→chunk→embed→index   │   ║
  ║  └────┬────┘                      └─────────────┬─────────────┘   ║
  ║       │                                         │                 ║
  ║       └──────────┬──────────────────────────────┘                 ║
  ║                  ▼                                                ║
  ║        ┌──────────────────────┐   ┌───────────────────────────┐  ║
  ║        │   Provider Layer     │   │      Analytics Logger     │  ║
  ║        │ LLMProvider          │   └─────────────┬─────────────┘  ║
  ║        │ EmbeddingProvider    │                 │                ║
  ║        └──────────┬───────────┘                 │                ║
  ╚═══════════════════╪═════════════════════════════╪════════════════╝
                      │                             │
              ┌───────┴────────┐           ┌────────┴──────────┐
              ▼                ▼           ▼                   ▼
      Anthropic / OpenAI   local ST    SQLite (metadata,   FAISS indexes
        (HTTP)             (in-proc)   intents, logs)      (one per space)
                                              ▲
                                              │ HTTP (localhost)
                                   ╔══════════╧═══════════╗
                                   ║ Streamlit console    ║
                                   ║ (:8501) — 5 screens  ║
                                   ╚══════════════════════╝
```

Two processes, one Docker network. Streamlit never touches SQLite or FAISS directly — it is a pure HTTP client of the admin API, so every rule lives in exactly one place.

### Component duties

| Component | Owns | Must not |
| --- | --- | --- |
| **Channel Adapters** | Protocol specifics: signature/JWT verification, payload → `InboundMessage`, `OutboundAnswer` → channel-native formatting, delivery, typing indicators, per-channel status. | Know anything about intents, retrieval, or prompts. |
| **Orchestrator** | The query lifecycle: classify → threshold check → route → call retrieval → hand back an answer → emit the log record. The only component that reads the confidence threshold. | Perform vector search or build answer prompts itself. |
| **Intent Service** | Intent space CRUD, protected-default enforcement, threshold setting, and the per-space FAISS index lifecycle (create on add, delete on remove, move vectors on reassignment). | Classify anything. |
| **Ingestion Pipeline** | Format detection, text + table extraction, chunking, embedding, index writes, document status, and error capture. | Answer queries. |
| **Retrieval + Answer** | Query embedding, per-space vector search, relevance floor, prompt assembly, citation construction, no-match determination. | Decide *which* space to search — it receives that. |
| **Provider Layer** | The only place that speaks to an AI backend. Backend selection, retries, timeouts, and error normalization. | Contain KMS domain logic or prompt text. |
| **Analytics Logger** | Writing `query_log` + `chunk_hit` rows and computing aggregates. | Block the response path. |
| **Admin REST API** | AuthN for the console, request validation, and orchestrating the services above. | Duplicate business rules the services own. |
| **Streamlit Console** | Rendering and admin interaction only. | Hold business rules or reach past the API. |

## Decisions

### 1. Provider abstraction is exactly two interfaces with two methods

```python
class LLMProvider(Protocol):
    def complete(self, *, system: str, user: str,
                 schema: dict | None = None,
                 max_tokens: int = 1024) -> LLMResult: ...

class EmbeddingProvider(Protocol):
    def embed(self, texts: list[str]) -> list[list[float]]: ...
    @property
    def dimension(self) -> int: ...
```

`schema` requests structured JSON output and is the only branch inside implementations — Anthropic uses `output_config.format`, OpenAI uses response formats, the local backend uses a constrained-decode-then-validate path. Everything else (prompt text, retries at the domain level, citation logic) lives outside.

Implementations shipped: `AnthropicLLM`, `OpenAILLM`, `LocalLLM` (Ollama-compatible HTTP); `SentenceTransformerEmbedding` (default), `OpenAIEmbedding`, `LocalEmbedding`. Anthropic has no embeddings endpoint, so `AnthropicLLM` pairs with local embeddings by default.

*Why:* the brief warns against over-engineering, and a pluggable layer was explicitly requested. A two-method interface is the smallest thing that satisfies the request. *Alternative rejected:* LangChain's provider abstractions — they drag in a large dependency tree and their own prompt/chain concepts for a surface we can express in ~40 lines.

*Trade-off, stated plainly:* the local backend will produce measurably worse classification and answers than the API backends. It exists so the demo runs without keys, not as a quality-equivalent option.

### 2. One FAISS index per intent space

Each intent space owns `data/faiss/{space_slug}.index` (`IndexFlatIP` over L2-normalized vectors, wrapped in `IndexIDMap2` keyed by `chunk.id`).

Consequences, all of which are why this was chosen:

- Hard filtering is free — routing *is* index selection, with no filter predicate at all.
- General fallback fans out across every index and merges by score.
- Reassigning a document's intent moves its vectors between two index files.
- Deleting a space deletes one file.

*Alternative rejected:* a single global index with a FAISS `IDSelector` predicate. It is the textbook answer, but selector support on `IndexFlat` varies across `faiss-cpu` releases, and it makes reassignment and deletion into fiddly ID-bookkeeping. With a handful of spaces and a few thousand chunks, the fan-out cost of the per-space design is single-digit milliseconds.

*Trade-off:* General queries do N searches instead of one, and scores are compared across independently-built indexes. Because all indexes use the same embedding model and inner product over normalized vectors, cross-index scores are directly comparable — this holds only while every space shares one embedding model, which § Decision 9 enforces.

### 3. Hard filter with General as the fallback space

```
classify(question) → (space, confidence)
if confidence >= threshold and space != General:  search only that space
else:                                             search all spaces (General behavior)
```

General is a real, undeletable intent space that means "search everything". A document can be assigned to General, in which case it is reachable from every fallback query but from no filtered query.

*Why:* it matches the brief's "route to the relevant KB domain" wording literally, it is trivially explainable to a reviewer, and every routing decision is a single logged row. *Alternative rejected:* soft re-ranking over a global search — more forgiving of misclassification, but it makes routing cosmetic and undemonstrable.

*Risk accepted:* a confidently-wrong classification searches the wrong space and returns a no-match where a global search would have answered. Mitigated by the relevance floor in § Decision 6 and by making misroutes visible in Analytics.

### 4. One intent space per document, admin-overridable

At upload the LLM is shown the space names and descriptions plus the document's first ~2000 characters and returns a suggested space; the admin can accept or override it in the KB screen. Reassignment moves the document's vectors between indexes and rewrites `chunk.intent_space_id` — no re-parse, no re-embed.

*Why:* one field on one row, one filter, one clean attribution path for analytics. *Alternative rejected:* per-chunk intent — more accurate for genuinely mixed documents (an employee handbook covering both HR and Finance), but it costs an LLM call per chunk at ingest and gives the admin no practical way to correct mistakes by hand.

*Known limitation:* a mixed document must pick one space. The admin's workaround is to split the source file before upload. This is documented in the README rather than solved in code.

### 5. LLM structured-output classification

One `complete()` call with a schema returning `{intent_slug, confidence, reasoning}`. The system prompt lists every space with its admin-authored description; the description is therefore a real tuning surface, not decoration, and the Intent Configuration screen says so.

*Why:* it handles novel phrasing, the `reasoning` field makes Analytics genuinely diagnostic, and it needs no training data or seeded centroids. *Alternative rejected:* embedding similarity to per-space centroids — free, fast, and better calibrated numerically, but weak on short or oddly-phrased queries and it needs enough documents per space to form a meaningful centroid, which a fresh install does not have.

*Trade-off, stated plainly:* LLM self-reported confidence is not a calibrated probability. The 0.70 threshold is a tunable heuristic. The spec therefore requires the threshold to be adjustable at runtime and requires Analytics to show the confidence distribution, so an admin can tune against observed behavior rather than trusting the number.

### 6. Relevance floor separate from the confidence threshold

Two independent numbers, deliberately not conflated:

- **Confidence threshold** (default 0.70) — classifier certainty. Gates *routing*.
- **Relevance floor** (default 0.35 cosine) — best chunk similarity. Gates *answering*.

If the top chunk falls below the floor, the system returns "no match" instead of generating from weak context. This is what prevents a confident misroute from producing a fluent, wrong, cited answer.

### 7. Answer generation is grounded and citation-bearing

Prompt: the question, the top-K chunks (K=5) each tagged with its document title and page/sheet reference, and an instruction to answer *only* from those chunks and to cite the tags used. Citations are then verified against the actually-retrieved chunk set — a citation naming a document that was not retrieved is dropped before the answer is sent.

*Why the verification step:* an uncited-but-plausible answer is the main failure mode of a small RAG system, and post-hoc verification is cheap insurance that costs no extra model call.

### 8. Two separately-configured models, both defaulting to `claude-opus-5`

`LLM_MODEL_CLASSIFY` and `LLM_MODEL_GENERATE` are independent settings. Both default to `claude-opus-5`.

*Why separate:* the ≤3s budget covers two sequential model calls. Measured against that budget, classification is the call to make cheap — it produces ~30 tokens and needs far less capability than answer synthesis. `.env.example` documents `claude-haiku-4-5` as the recommended latency optimization for `LLM_MODEL_CLASSIFY`, and § Latency budget below shows both configurations. The default stays on the stronger model so out-of-the-box accuracy is the best it can be; the operator makes the speed trade knowingly.

### 9. Embedding model is immutable once documents exist

Vectors from different embedding models are not comparable, and § Decision 2's cross-index score comparison depends on a single shared model. Changing `EMBEDDING_MODEL` while documents are indexed corrupts retrieval silently — the worst kind of failure, because nothing errors and answers just get subtly worse.

The service therefore stores the embedding model name and dimension in `app_setting` on first ingest and **refuses to start** if the configured model no longer matches, with an error naming the mismatch and pointing at the re-index command. Recovery is an explicit `reindex-all` admin action that re-embeds every document.

### 10. Channel-aware generation plus deterministic enforcement

The generation prompt receives a channel profile (`max_chars`, markdown flavor, whether bullets render) so the model *writes to fit* the destination. Each adapter then applies a deterministic formatter — escaping, bullet translation, and hard truncation at a word boundary with a "…(truncated)" marker.

*Why both:* the AI-authored fit is what makes responses read naturally on each platform; the deterministic pass is what makes "it will never exceed Telegram's 4096-character limit" a guarantee rather than a hope. Relying on the model alone would make a hard protocol limit probabilistic.

### 11. Credentials encrypted at rest with Fernet

Bot tokens are encrypted with `cryptography.fernet` using `CREDENTIAL_ENCRYPTION_KEY` from the environment, stored as ciphertext in `integration.credentials_encrypted`, and returned from the API masked (last 4 characters only). The key itself is never persisted. A missing or invalid key fails startup rather than silently falling back to plaintext.

### 12. Telegram runs in webhook mode with a polling fallback

Webhook is the default and shares the tunnel with Teams. `TELEGRAM_MODE=polling` starts a background long-poll worker instead, which lets Telegram be demonstrated with no tunnel at all — useful when the tunnel is down or unavailable. Teams has no such fallback; Bot Framework requires a reachable HTTPS endpoint.

### 13. Analytics logging is synchronous but off the critical path

The log row is written *after* the answer is handed to the adapter for delivery. A logging failure is caught, logged to stderr, and never propagated to the user — an analytics problem must not cost the user their answer.

## Data model

SQLite via SQLAlchemy Core. Timestamps UTC ISO-8601.

```
intent_space(id, slug UQ, name, description, is_protected, created_at, updated_at)
document(id, filename, mime_type, size_bytes, sha256, intent_space_id → intent_space,
         status[pending|parsing|indexed|failed], error_message, chunk_count,
         uploaded_at, indexed_at)
chunk(id, document_id → document, intent_space_id → intent_space, ordinal,
      text, char_count, source_ref, created_at)
integration(id, channel[telegram|teams] UQ, display_name, enabled,
            credentials_encrypted, status[unconfigured|ok|error],
            last_ok_at, last_error, updated_at)
query_log(id, channel, external_user_id, question, intent_space_id → intent_space,
          confidence, fallback_used, no_match, answer, citations_json,
          latency_ms, error, created_at)
chunk_hit(id, query_log_id → query_log, chunk_id, document_id, rank, score)
app_setting(key PK, value)   -- confidence_threshold, relevance_floor,
                             -- embedding_model, embedding_dimension
```

`chunk_hit` is what makes "most accessed documents" a real measurement of retrieval rather than a proxy derived from intent counts. `chunk.source_ref` carries `p. 4` for PDFs, `¶ 12` for DOCX, `Sheet1!A1:F20` for XLSX, and is what appears in citations.

`document.sha256` deduplicates re-uploads of an identical file. `ON DELETE CASCADE` from `document` to `chunk` and from `query_log` to `chunk_hit`; `chunk_hit.chunk_id` is intentionally *not* a foreign key so that deleting a document does not erase the history of it having been used.

## Request flows

### Query (the ≤3s path)

```
1. Telegram/Teams → POST webhook
2. Adapter          verify signature/JWT → InboundMessage{channel, user_id, text}
3. Adapter          send typing indicator (fire-and-forget)
4. Orchestrator     LLMProvider.complete(schema=IntentClassification)
                    → {slug, confidence, reasoning}
5. Orchestrator     confidence >= threshold and slug != general
                       ? spaces = [slug]        (fallback_used = false)
                       : spaces = all           (fallback_used = true)
6. Retrieval        EmbeddingProvider.embed([question])
7. Retrieval        search each index in `spaces`, merge, take top 5
8. Retrieval        best score < relevance_floor → NO_MATCH, skip to 10
9. Retrieval        LLMProvider.complete(prompt with chunks + channel profile)
                    → answer; verify citations against retrieved chunks
10. Adapter         format for channel, deliver
11. Logger          write query_log + chunk_hit rows
```

Steps 4 and 6 are independent and run concurrently — the query embedding does not depend on the classification result, only the *index selection* does. This overlaps one model call with the embedding call for free.

### Ingestion

```
1. Console → POST /documents (multipart)
2. API          validate extension + size; sha256; reject exact duplicate
3. API          insert document(status=pending); return 202 immediately
4. Worker       status=parsing
5. Parser       PDF  → pypdf text + pdfplumber tables → markdown tables
                DOCX → python-docx paragraphs + tables
                XLSX → openpyxl sheets → markdown tables
6. Parser       ragged/failed table extraction → LLMProvider.complete()
                to restructure that region (see § Table extraction)
7. Chunker      ~800 chars, 100-char overlap, never split a table mid-row
8. Classifier   suggest intent space from name/description + first 2000 chars
9. Embedder     EmbeddingProvider.embed(chunk texts), batched
10. Indexer     write vectors to that space's index; persist chunks
11. Worker      status=indexed, chunk_count, indexed_at
    on failure  status=failed, error_message — document row is kept so the
                admin sees the failure and can retry
```

Ingestion is a FastAPI `BackgroundTask`, not a queue — a single-process MVP does not need a broker, and the KB screen polls document status.

### Table extraction

This is the brief's first named AI usage scenario, so the fallback is a designed behavior rather than an incidental one. `pdfplumber` handles ruled tables well and merged/borderless cells badly — HR salary grids are exactly the badly-handled case. When extraction yields a ragged result (inconsistent column counts across rows, or >30% empty cells), that region's raw text is passed to `LLMProvider.complete()` with a schema requesting a clean markdown table. The result is embedded as chunk text, which is what makes numeric and tabular content semantically searchable at all.

## Latency budget

Target ≤ 3s end-to-end. Steps 4 and 6 overlap per § Request flows.

| Stage | Default (`claude-opus-5` both) | With `claude-haiku-4-5` classify |
| --- | --- | --- |
| Webhook + verify | ~30 ms | ~30 ms |
| Classify ‖ embed (local ST) | ~900 ms | ~350 ms |
| Vector search + merge | ~20 ms | ~20 ms |
| Answer generation | ~1400 ms | ~1400 ms |
| Format + deliver | ~250 ms | ~250 ms |
| **Total** | **~2.6 s** | **~2.05 s** |

The default configuration meets the budget with roughly 400 ms of headroom, which is thin. Two mitigations are specified rather than assumed: the typing indicator goes out before any model call so the user sees immediate acknowledgement, and both model settings are independently tunable. The end-to-end test in the Integrations screen reports measured latency so an operator can verify the budget on their own hardware and network instead of trusting this table.

## Security

- Admin console behind a single password (`ADMIN_PASSWORD`), compared with `secrets.compare_digest`.
- Admin API requires a shared bearer token (`ADMIN_API_TOKEN`); the console holds it server-side.
- Telegram webhooks verified via `X-Telegram-Bot-Api-Secret-Token`; Teams via Bot Framework JWT validation against the Azure AD JWKS.
- Bot credentials Fernet-encrypted at rest, masked in every API response.
- Upload validation: extension allowlist, MIME sniff, 25 MB cap, filename sanitized before it touches the filesystem.
- Document content is untrusted input. Retrieved chunks are wrapped in delimiters in the generation prompt with an explicit instruction to treat them as data, not instructions — a poisoned document must not be able to redirect the model.

Explicitly out of scope: rate limiting, per-user authorization, and audit logging beyond the query log.

## Risks / Trade-offs

| Risk | Mitigation |
| --- | --- |
| Teams needs an Azure Bot registration the developer may not be able to create — hard blocker on half the frontend requirement | Verify Azure access **first** (task 1.2, before any Teams code). Adapter is written against the Bot Framework protocol so the Bot Framework Emulator can drive it locally without a tenant. If access proves impossible, this is a scope decision for the user, not a silent substitution. |
| LLM confidence is uncalibrated; 0.70 may be meaningless in practice | Threshold editable at runtime; Analytics plots the confidence distribution and per-space fallback rate so it can be tuned against real data. |
| Hard filtering turns a misclassification into a no-match | Relevance floor catches weak retrieval; General fallback catches low confidence; misroutes are visible in Analytics with the classifier's `reasoning`. |
| Two sequential model calls threaten the 3s budget | Classify ‖ embed overlap; separately tunable classify model; typing indicator for perceived latency; test function reports measured latency. |
| Cloudflared quick tunnels get a new URL on every restart, silently breaking both webhooks | Startup re-registers the Telegram webhook automatically; the Teams messaging endpoint must be updated by hand in Azure, and the README calls this out as the single most likely demo failure. A named tunnel is documented as the stable alternative. |
| `faiss-cpu` wheels are architecture-sensitive (Apple Silicon) | Pin a known-good version; Docker image builds `linux/arm64` and `linux/amd64`; a smoke test asserts the index round-trips at container start. |
| Streamlit's rerun-on-interaction model makes file upload and polling awkward | Console is a thin API client with no local state; uploads return immediately and status is polled, so a rerun mid-ingest costs nothing. |
| Changing the embedding model silently corrupts retrieval | Model + dimension recorded on first ingest; mismatch fails startup loudly; explicit `reindex-all` is the only supported path. |
| SQLite write contention between ingest and query | WAL mode; ingest batches writes; single-process deployment means no cross-process contention. |

## Migration Plan

No migration — greenfield. Deployment is:

1. `cp .env.example .env`, fill in provider key, admin password/token, and generate a Fernet key.
2. `docker compose up --build` → API on `:8000`, console on `:8501`.
3. `cloudflared tunnel --url http://localhost:8000` → public HTTPS URL.
4. Paste the tunnel URL into the Integrations screen; it registers the Telegram webhook and displays the Teams messaging endpoint to paste into Azure.
5. Upload `sample_docs/`, verify each reaches `indexed`.
6. Run the end-to-end test per channel; confirm both report OK with measured latency.

Rollback is `docker compose down`; deleting `data/` resets all state.

## Open Questions

- Which specific embedding model to default to (`all-MiniLM-L6-v2`, 384-dim, ~80 MB vs `all-mpnet-base-v2`, 768-dim, ~420 MB). Both satisfy every requirement; it is a container-size/quality trade to settle by measuring on the sample documents. Deferrable — § Decision 9 already fixes the *mechanism* for recording and changing it, so the choice does not affect specs, architecture, or task breakdown.
- Whether the Analytics CSV export should stream or buffer. Buffering is fine at MVP data volumes; this only matters past ~100k logged queries.
