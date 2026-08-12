## Context

Greenfield project, empty repository, one developer. See `proposal.md` — Why for the three goals that define scope.

| Constraint | Source |
| --- | --- |
| **7 calendar days, solo** | Project brief §1 Constraints |
| Lightweight stack only — no managed cloud services, no heavy frameworks | Project brief |
| Two chat frontends, ≥2 document formats, ≥3 intent spaces | Project brief |
| Query round-trip ≤ 3s | Project brief |
| Confidence threshold configurable, default ≥ 0.70 | Project brief |
| Admin UI follows the brief's visual guidance (§2) | Project brief §2 |
| Python + FastAPI + Streamlit + SQLite + FAISS | Chosen (Option A) |
| Pluggable AI provider layer | Chosen |
| Telegram + Microsoft Teams | Chosen |
| All tunables in one configuration file | Chosen |

**Running the system is not a goal.** Two commands (`uvicorn`, `streamlit run`) plus `config.yaml` and `.env` is the supported path. A Dockerfile and compose file are a convenience that may be added at the end; nothing in the design depends on them.

**No public tunnel is required.** Telegram runs in long-polling mode by default, which needs no inbound URL at all. Teams is developed and demoed against the Bot Framework Emulator on localhost. A tunnel is needed only to put Teams in front of a real Microsoft 365 tenant, and that is an optional deployment step, not part of the architecture.

## Goals / Non-Goals

**Goals:**

- A RAG pipeline whose stages are separately testable and separately tunable.
- Intent routing that is visible — any logged query shows which space it went to and how confident the classifier was.
- One file an operator edits to change any model, threshold, or retrieval parameter.
- Swapping AI backends is a configuration change, not a code change.

**Non-Goals:**

- Multi-tenancy, RBAC, per-user document permissions, rate limiting, audit logging.
- Conversational memory. Each query is answered independently.
- Streaming responses.
- Automatic document sync from Drive/SharePoint.
- Horizontal scale. Single process, in-process locking.
- Analytics beyond the query history table (see § Decision 12).

## Architecture

```
  Telegram user                                Teams user
       │ long-poll (no inbound URL)                 │ Bot Framework
       ▼                                            ▼
╔═══════════════════ FastAPI service (:8000) ═══════════════════════════╗
║                                                                       ║
║  ┌──────────────────────┐                 ┌────────────────────────┐ ║
║  │   Channel Adapters   │                 │     Admin REST API     │ ║
║  │  TelegramAdapter     │                 │ /config /intents       │ ║
║  │  TeamsAdapter        │                 │ /documents /history    │ ║
║  └──────────┬───────────┘                 │ /test-query ───────┐   │ ║
║             │ InboundMessage              └────────────────────┼───┘ ║
║             │                                                  │     ║
║             ▼                                                  │     ║
║  ┌────────────────────────┐                                    │     ║
║  │   Query Orchestrator   │◄───────────────────────────────────┘     ║
║  │  classify → threshold  │   (the ONLY admin→orchestrator path;     ║
║  │  → space list          │    powers "Try a query" + channel test)  ║
║  └───────────┬────────────┘                                          ║
║              │ spaces[]                                              ║
║              ▼                                                       ║
║  ╭────────────────────── RAG Engine ──────────────────────────────╮  ║
║  │                                                                │  ║
║  │  READ PATH                          WRITE PATH                 │  ║
║  │  ┌──────────────────┐               ┌────────────────────┐    │  ║
║  │  │ HybridRetriever  │               │  DocumentLoader    │    │  ║
║  │  │  ├ VectorSearch  │◄──┐        ┌─►│  (pdf/docx/xlsx)   │    │  ║
║  │  │  ├ KeywordSearch │◄─┐│        │  └─────────┬──────────┘    │  ║
║  │  │  └ RRF Fusion    │  ││        │            ▼               │  ║
║  │  └────────┬─────────┘  ││        │  ┌────────────────────┐    │  ║
║  │           ▼            ││        │  │ StructuralChunker  │    │  ║
║  │  ┌──────────────────┐  ││        │  └─────────┬──────────┘    │  ║
║  │  │ RelevanceGate    │  ││        │            ▼               │  ║
║  │  └────────┬─────────┘  ││        │  ┌────────────────────┐    │  ║
║  │           ▼            ││        └──┤     Embedder       │    │  ║
║  │  ┌──────────────────┐  ││           └─────────┬──────────┘    │  ║
║  │  │ ContextBuilder   │  ││                     ▼               │  ║
║  │  └────────┬─────────┘  ││        ┌────────────────────────┐   │  ║
║  │           ▼            ││        │      IndexWriter       │   │  ║
║  │  ┌──────────────────┐  ││        └───┬────────────────┬───┘   │  ║
║  │  │ AnswerGenerator  │  ││            │                │       │  ║
║  │  └────────┬─────────┘  ││            ▼                ▼       │  ║
║  │           ▼            │└──── FAISS (per space)   SQLite FTS5 │  ║
║  │  ┌──────────────────┐  └───────────────────────────────┘      │  ║
║  │  │ CitationVerifier │                                          │  ║
║  │  └──────────────────┘                                          │  ║
║  ╰────────────────────────────────────────────────────────────────╯  ║
║                        │                    │                        ║
║              ┌─────────┴────────┐   ┌───────┴────────┐               ║
║              │  Provider Layer  │   │  ConfigService │               ║
║              │ LLM / Embedding  │   │  config.yaml   │               ║
║              └─────────┬────────┘   └────────────────┘               ║
╚════════════════════════╪═════════════════════════════════════════════╝
                         ▼                              ▲
             Anthropic / OpenAI / local                 │ HTTP
                                             ╔══════════╧═══════════╗
                                             ║  Streamlit console   ║
                                             ║  (:8501) — 5 screens ║
                                             ╚══════════════════════╝
```

### The admin path into the orchestrator

Exactly one endpoint crosses from the admin API into the orchestrator: **`POST /admin/test-query`**. It runs a question through the full pipeline and returns intent, confidence, answer, sources, and latency *without* delivering to any chat channel. Two features require it:

- The **"Try a query"** box on the Dashboard — verify the knowledge base after uploading without opening Telegram, and close the keyword-tuning loop in seconds.
- The **per-channel connection test** — proves the whole path works, not just that a credential is valid.

Every other admin endpoint talks only to its own service. Documents, intents, config, and analytics never reach the orchestrator.

### Console → backend interaction

The console is a pure HTTP client. Each screen maps to a fixed set of endpoints, and each endpoint to one owning component:

```
┌─ Dashboard ─────────────┐   GET  /stats ──────────────► DocumentStore + QueryLog
│ counts, statuses,       │   GET  /integrations ───────► ChannelStatus
│ provider summary,       │   GET  /config ────────────► ConfigService
│ "Try a query"           │   POST /admin/test-query ──► ORCHESTRATOR ──► RAG Engine
└─────────────────────────┘

┌─ Frontend Integration ──┐   GET  /integrations ───────► ChannelStatus + CredentialStore
│ cards, status, last-4,  │   PUT  /integrations/{ch} ──► CredentialStore (encrypt)
│ test button             │   DEL  /integrations/{ch} ──► CredentialStore
└─────────────────────────┘   POST /integrations/{ch}/test ─► ChannelAdapter
                                                              └► ORCHESTRATOR ──► RAG Engine

┌─ Knowledge Base ────────┐   POST /documents ─────────► IngestionWorker (background)
│ table, upload zone,     │                                └► Loader → Chunker → Embedder
│ search, filters,        │                                   → IndexWriter → FAISS + FTS5
│ view/update/delete      │   GET  /documents?q=&format=&space=&from=&to= ─► DocumentStore
└─────────────────────────┘   GET  /documents/{id} ─────► DocumentStore + chunks
                              PATCH /documents/{id} ────► IndexWriter.reassign (moves vectors)
                              POST /documents/{id}/reparse ─► IngestionWorker
                              DEL  /documents/{id} ─────► IndexWriter.remove
                              POST /documents/reindex ──► IngestionWorker (all)

┌─ Intent Configuration ──┐   GET  /intents ───────────► ConfigService + QueryLog (counts,
│ space cards, editor,    │                               accuracy rate)
│ classification log,     │   POST/PATCH/DEL /intents ─► ConfigService ──► config.yaml
│ thresholds              │                               └► VectorStore (create/delete index)
└─────────────────────────┘   PATCH /config ───────────► ConfigService ──► config.yaml
                              GET  /analytics/log ─────► QueryLog

┌─ Analytics ─────────────┐   GET /analytics/distribution ─► QueryLog
│ period selector,        │   GET /analytics/documents ───► QueryLog + DocumentStore
│ distribution, top docs, │   GET /analytics/log ─────────► QueryLog
│ log, export             │   GET /analytics/export.csv ──► QueryLog
└─────────────────────────┘
```

Three properties this makes visible:

- **Only two screens reach the orchestrator**, and both go through the same endpoint. Everything else is CRUD over a single owning service.
- **Intent edits have a side effect beyond config**: creating or deleting a space also creates or deletes its FAISS index, and reassigning a document moves vectors. Those are the only console actions that mutate the vector store.
- **Upload returns before work starts.** `POST /documents` returns 202 and the rest is background; the table polls status. No console action blocks on the RAG pipeline except the test-query.

### Component duties

| Component | Owns | Must not |
| --- | --- | --- |
| **Channel Adapters** | Protocol specifics: polling/webhook, payload → `InboundMessage`, `OutboundAnswer` → channel formatting, delivery, typing indicators, per-channel status. | Know about intents, retrieval, or prompts. |
| **Query Orchestrator** | Classify → threshold check → produce the space list → invoke the RAG read path → emit the log record. Only reader of the confidence threshold. | Perform retrieval or build answer prompts. |
| **DocumentLoader** | Format detection and extraction into an ordered block list (heading / paragraph / table) with source references. | Chunk or embed. |
| **StructuralChunker** | Packing blocks into overlapping chunks under the structural rules in § RAG write path. | Know about embeddings or storage. |
| **Embedder** | Batching text → vectors via the provider layer; normalization. | Decide what gets embedded. |
| **IndexWriter** | Keeping the FAISS index and the FTS5 table consistent with the `chunk` table; add, remove, move-between-spaces. | Search. |
| **HybridRetriever** | Vector search, keyword search, and RRF fusion within the given spaces. | Decide *which* spaces — it receives that. |
| **RelevanceGate** | The answer/no-answer decision from the best dense score. | Generate anything. |
| **ContextBuilder** | Selecting, deduping, ordering, tagging, and budgeting the chunks that reach the prompt. | Call the LLM. |
| **AnswerGenerator** | Prompt assembly and the generation call. | Decide what context to use. |
| **CitationVerifier** | Mapping citation markers back to real retrieved chunks and dropping unverifiable ones. | Alter the answer body beyond citations. |
| **Provider Layer** | The only place that speaks to an AI backend. Selection, retries, timeouts, error normalization. | Contain domain logic or prompt text. |
| **ConfigService** | Loading, validating, exposing, and rewriting `config.yaml`; reload on change. | Hold secrets. |
| **Admin REST API** | AuthN for the console, validation, delegating to services. | Duplicate rules the services own. |
| **Streamlit Console** | Rendering and admin interaction. | Hold business rules or reach past the API. |

## The RAG engine

This is the core of the system, so it is specified stage by stage rather than as one retrieval step.

### Storage: what lives where, and why

| Store | Holds | Why this one |
| --- | --- | --- |
| **SQLite** (`document`, `chunk`, `query_log`) | Document metadata, chunk text, source refs, query history | Already required; single file; zero setup |
| **SQLite FTS5** (`chunk_fts`) | Full-text keyword index over chunk text, BM25 built in | Ships inside Python's `sqlite3` — a keyword index for zero new dependencies |
| **FAISS** (`data/faiss/{space}.index`) | Dense vectors, one index per intent space | Named in the brief; no server; exact search at this scale |

**Vector store decision.** Considered: FAISS, Chroma, sqlite-vec, and a hosted store (Qdrant/pgvector).

- *Hosted stores* are excluded by the brief's "no cloud services, lightweight only".
- *Chroma* bundles its own embedding and persistence opinions and a large dependency tree for functionality we need a thin slice of.
- *sqlite-vec* is genuinely attractive — one file for everything, and metadata filtering becomes a plain SQL `WHERE`. It is the strongest alternative and is recorded here as the fallback if FAISS packaging causes trouble.
- **FAISS wins** because the brief names it, it needs no server, and at this scale it is *exact*: `IndexFlatIP` is an exhaustive scan, so recall is 100% with no ANN parameters to tune. A 5,000-chunk knowledge base at 384 dimensions is 7.7 MB of float32 — brute force over that is well under a millisecond. **We deliberately do not use an approximate index.** IVF/HNSW solve a scale problem this system does not have, and would add recall loss and tuning burden for no gain.

Vectors are L2-normalized before insertion, so inner product equals cosine similarity and scores are directly comparable.

**One index per intent space.** Routing becomes index selection, so hard filtering costs nothing and needs no filter predicate. Reassigning a document moves its vectors between two files; deleting a space deletes one file. The alternative — one global index with a FAISS `IDSelector` — is the textbook answer, but selector support on `IndexFlat` varies across `faiss-cpu` releases and it turns reassignment into ID bookkeeping. Cost of the chosen design: a General fallback query searches N indexes instead of one, which is a few milliseconds and is safe because every space shares one embedding model (§ Decision 6 enforces this).

### RAG write path (indexing)

```
upload → DocumentLoader → StructuralChunker → Embedder → IndexWriter
                                                    ├→ FAISS (space index)
                                                    └→ SQLite chunk + chunk_fts
```

**1. DocumentLoader** produces an ordered list of typed blocks, each with a source reference:

| Format | Library | Blocks produced | Source ref |
| --- | --- | --- | --- |
| PDF | `pypdf` text + `pdfplumber` tables | paragraph, table | `p. 4` |
| DOCX | `python-docx` | heading, paragraph, table | `¶ 12` |
| XLSX | `openpyxl` | table (one per sheet region) | `Sheet1!A1:F20` |

Tables are rendered to markdown so that row/column structure survives into the chunk text and is therefore embeddable and keyword-searchable. When deterministic table extraction comes out ragged — inconsistent column counts, or a majority of empty cells, which is exactly what merged-cell HR salary grids produce — that region's raw text is passed to the LLM with a schema requesting a clean table. This is the brief's first named AI usage scenario, and it is what makes numeric and tabular content searchable at all. If the model fails, the raw text is used and ingestion still completes.

**2. StructuralChunker** packs blocks into chunks under four rules:

- Target 800 characters with 100 characters of overlap. (~200 tokens; small enough that a chunk is one idea, large enough to carry a full policy clause.)
- **Never split a table row.** A table under 1.5× target stays whole even if oversized.
- **Prepend the heading path.** A chunk under "Leave Policy › Annual Leave" is stored with that path as a prefix, so the embedding carries the context the raw sentence lacks and the citation can show where it came from.
- Overlap is applied only within a block run, never across a heading boundary — bleeding the end of Legal into the start of Finance is worse than a short chunk.

**3. Embedder** — `sentence-transformers/all-MiniLM-L6-v2`, 384 dimensions, batch size 64, normalized. Chosen over `all-mpnet-base-v2` (768-dim): MiniLM is ~80 MB vs ~420 MB, roughly 5× faster on CPU, and the quality gap does not show at this corpus size. It is a one-line config change if retrieval quality proves short.

**4. IndexWriter** writes each chunk to three places in one transaction-ish sequence: the `chunk` row, the `chunk_fts` FTS5 row, and the space's FAISS index (`IndexIDMap2` keyed by `chunk.id`, persisted with `faiss.write_index` after each document completes).

### RAG read path (query)

```
question ──┬─► Embedder ──► VectorSearch (top 20 per space)  ─┐
           │                                                   ├─► RRF ─► top 5
           └─► KeywordSearch — FTS5 BM25 (top 20, intent-filtered) ─┘
                                                                     │
                            RelevanceGate ◄────────────────────────  ┘
                                 │ pass
                                 ▼
                          ContextBuilder ─► AnswerGenerator ─► CitationVerifier
```

**1. Dual retrieval.** Dense vector search over the routed space indexes, and BM25 keyword search over `chunk_fts` filtered to the same spaces by SQL. Both return their top 20.

**Why hybrid, and not pure vector.** This is the single most important RAG decision here. Embeddings are good at paraphrase and bad at rare exact tokens — and enterprise knowledge is full of exact tokens: "Band L4", "Form 16", "Section 4.2", "Policy HR-2019-03", a specific salary figure. A user asking "what does Band L4 pay" against a pure-vector system gets chunks that are semantically about compensation but not the row they asked for. BM25 matches that token exactly. Conversely BM25 fails on "how much time off do I get" → "annual leave entitlement", which embeddings handle. Running both and fusing covers both failure modes, and it directly serves the brief's HR-salary-grid scenario.

**2. Reciprocal Rank Fusion.** `score(chunk) = Σ_lists 1 / (k + rank)`, with `k = 60`.

RRF is used instead of a weighted score blend because cosine similarity and BM25 live on incomparable scales — cosine is bounded roughly 0–1, BM25 is unbounded and corpus-dependent — so any weighted sum needs normalization constants that must be re-tuned whenever the corpus changes. RRF only reads *rank*, so it needs no normalization and no tuning, and it is about five lines of code. Chunks found by both retrievers naturally rise to the top, which is the behavior we want.

**3. RelevanceGate.** Compares the best **dense cosine score** — not the fused score — against `relevance_floor` (default 0.35). The fused score is rank-derived and unitless, so it cannot express "nothing here is actually relevant"; the raw cosine can. Below the floor the query returns no-match and **no generation call is made**. This is what stops a confident misroute from producing a fluent, wrong, fully-cited answer.

**4. ContextBuilder** takes the top 5 fused chunks and:
- drops near-duplicates (chunks from the same document with heavy overlap),
- re-sorts them by document and ordinal so the model reads them in the order they were written rather than in score order,
- caps total context at 6,000 characters,
- tags each as `[S1] Employee Handbook — p. 4 — Leave Policy › Annual Leave`.

**5. AnswerGenerator** builds a prompt with the grounding rules, the channel's formatting profile (§ Decision 8), the tagged context, and the question. It instructs the model to answer only from the supplied context, to cite with the `[S#]` markers, and to say so plainly when the context does not contain the answer.

**6. CitationVerifier** parses `[S#]` markers out of the answer, maps them back to the chunks that were actually supplied, drops any marker that does not resolve, and attaches the resulting document names and source references. A confident answer citing a document that was never retrieved is the main failure mode of a small RAG system, and this check costs no extra model call.

### Reranking, and what the critical path is actually spending on

**Open decision — needs a call before plan 04 is executed.**

The critical path carries two sequential model calls. What each buys is very different:

| Call | Cost | Output | What it determines |
| --- | ---: | --- | --- |
| Classification | ~900 ms | ~30 tokens | *Which index to search* |
| Answer generation | ~1400 ms | the answer | What the user reads |
| *(candidate)* Cross-encoder rerank | ~150–250 ms | 20 scores | *Which 5 chunks the answer is written from* |

Classification is the expensive call per unit of value. It spends ~900 ms — dominated by time-to-first-token, not generation — to emit thirty tokens that pick a directory of vectors. Reranking would spend a fraction of that on the step that decides the factual content of the answer. Excluding a 200 ms reranker while paying 900 ms to route is not a coherent latency position.

Two further points argue for a reranker specifically here:

- **Corpus size is now in the range where it pays.** 32 documents, several hundred pages, ~5,000 chunks, with cross-space confusables deliberately included (IRS payroll tables under Finance, GitLab compensation under HR). Bi-encoder cosine makes exactly the ranking mistakes in that band that a cross-encoder corrects. At three documents it would have been pointless; at this size it is not.
- **It would improve the relevance gate, which is the safety-critical component.** The gate currently reads bi-encoder cosine, chosen because it is the only absolute-scale signal available. A cross-encoder relevance score is a materially better-calibrated answer to "is this chunk actually relevant to this question" — which is precisely what the gate is asking. Adding a reranker would let the gate read the better signal.

**Options:**

| | Change | Classify | Rerank | Total | Trade |
| --- | --- | ---: | ---: | ---: | --- |
| **A** | Cheap routing + reranker: replace LLM classification with embedding-centroid + keyword scoring, escalating to the LLM only when the top-2 margin is narrow | ~5 ms | ~200 ms | **~1.9 s** | Fastest and best-ranked, but redesigns the orchestrator and loses the classifier's `reasoning` string that the classification log shows. Cold start solved by seeding centroids from each space's description + keywords rather than its documents. |
| **B** | Keep LLM classification on a faster model, add reranker | ~350 ms | ~200 ms | **~2.3 s** | One new component, one config change. Keeps `reasoning`, keeps keywords feeding the prompt as the brief describes. |
| **C** | Status quo, no reranker | ~900 ms | — | ~2.6 s | Simplest; leaves ranking precision on the table and the gate on the weaker signal. |

**Recommendation: B.** It buys the ranking and gate improvements for less total latency than the status quo, adds one component rather than restructuring the orchestrator, and preserves the classifier reasoning that makes the classification log diagnostic. A is the better end state and is recorded as the upgrade path; it is not the right thing to attempt inside a 7-day build alongside everything else.

If B is adopted: the reranker is `cross-encoder/ms-marco-MiniLM-L-6-v2` (22M params, ~90 MB), scoring the fused top-20 in one batch, feeding the top-5 to the context builder, and supplying the gate's relevance score in place of dense cosine.

## Configuration

**One file, `config.yaml`, is the single source of truth for every tunable.** Secrets live in `.env` and nowhere else. There is no settings table in the database.

```yaml
llm:
  provider: anthropic              # anthropic | openai | local
  model_classify: claude-opus-5
  model_generate: claude-opus-5
  timeout_seconds: 20
  max_retries: 2

embedding:
  provider: local                  # local | openai
  model: all-MiniLM-L6-v2
  dimension: 384
  batch_size: 64

rag:
  chunk_chars: 800
  chunk_overlap_chars: 100
  vector_top_n: 20
  keyword_top_n: 20
  rrf_k: 60
  final_top_k: 5
  max_context_chars: 6000
  relevance_floor: 0.35

orchestrator:
  confidence_threshold: 0.70
  fallback_space: general

intent_spaces:
  - slug: hr
    name: HR
    description: "Employee policies, leave, benefits, payroll, onboarding."
    keywords: [leave, vacation, salary, band, benefits, onboarding, appraisal]
  - slug: legal
    name: Legal
    description: "Contracts, compliance, data protection, terms."
    keywords: [contract, NDA, GDPR, compliance, liability, clause]
  - slug: finance
    name: Finance
    description: "Expenses, reimbursement, budgets, invoicing, salary bands."
    keywords: [expense, reimbursement, invoice, budget, procurement, tax]
  - slug: operations
    name: Operations
    description: "Internal processes, tooling, facilities, IT requests."
    keywords: [access, laptop, VPN, ticket, facilities, process]
  - slug: general
    name: General
    description: "Fallback — searches every space."
    keywords: []

channels:
  telegram: {enabled: true,  mode: polling, max_message_chars: 4096}
  teams:    {enabled: false,                max_message_chars: 28000}

ingestion:
  max_upload_mb: 25
  allowed_extensions: [".pdf", ".docx", ".xlsx"]

storage:
  sqlite_path: ./data/intelliknow.db
  faiss_dir: ./data/faiss
  upload_dir: ./data/uploads

public_base_url: null              # only for Telegram webhook mode or real-tenant Teams
```

`.env` holds only: `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `TELEGRAM_BOT_TOKEN`, `TEAMS_APP_ID`, `TEAMS_APP_PASSWORD`, `ADMIN_PASSWORD`.

**Intent spaces are configuration, not database rows.** They are declarations — a slug, a name, and a description the classifier reads. Putting them in `config.yaml` means every knob an operator touches is in one file, and it removes a table plus its foreign keys. Documents reference a space by slug. The console's Intent Configuration screen edits this list and writes the file back; deleting a space that still has documents is rejected with the count, and the admin reassigns first.

The console edits `config.yaml` through the API; `ConfigService` validates, writes atomically, and reloads in place. Changing a threshold takes effect on the next query with no restart. Changing `embedding.model` while documents exist is refused (§ Decision 6).

## Admin UI layout

The brief's §2 visual guidance is a requirement, not a suggestion, so it is fixed here rather than left to implementation taste.

**Five screens**, reachable from a persistent nav: Dashboard, Frontend Integration, Knowledge Base Management, Intent Space Configuration, Analytics.

**Visual scheme.** Neutral white/light-grey base. Every section is a card: 12 px radius, 16 px padding, clear heading. Per-module accent colours — Frontend Integration blue, Knowledge Base green, Intent Space purple. Primary actions ("Upload Document", "Create Intent Space", "Test") are visually prominent. Streamlit gets this via one injected CSS block plus `st.container(border=True)`; the accent is applied per page.

**Where each element lives** — note the classification log sits under Intent Space Configuration, per the brief, not under Analytics:

| Screen | Elements |
| --- | --- |
| Dashboard | KB size, per-space counts, channel status, recent query volume, provider/model summary, "Try a query" box |
| Frontend Integration | One card per tool: Connected/Disconnected indicator, credential last-4, test button, setup guidance |
| Knowledge Base Management | Document table (Name, Upload Date, Format, Size, Status, Actions View/Update/Delete); drag-and-drop upload zone with supported formats and a processing progress indicator; search bar; filters by format, date, intent space |
| Intent Space Configuration | Card per space (name, description, associated document count, classification accuracy rate); **query classification log** (recent queries, detected space, confidence, status); editor form (name, description, **keywords**); threshold controls |
| Analytics | Period selector, intent space distribution, most accessed documents, query log with detail, CSV export |

**Document status vocabulary.** Internally `pending | parsing | indexed | failed`; the UI renders these as **Pending / Pending / Processed / Error** to match the brief's wording.

**Classification accuracy rate**, shown per intent-space card, is the share of queries classified into that space whose confidence met the threshold. This is a real, cheap measurement — but it is *not* human-verified correctness, so the UI states its derivation next to the figure. The brief also asks for "admin-guided accuracy improvement"; the mechanism is the per-space **keywords** field, which feeds the classifier prompt and can be edited and re-tested in seconds via the Dashboard's "Try a query" box.

## Data model

Five tables. SQLite via SQLAlchemy Core, WAL mode, timestamps UTC ISO-8601.

```
document(id, filename, ext, size_bytes, sha256, intent_slug, status,
         error_message, chunk_count, uploaded_at, indexed_at)

chunk(id, document_id → document, intent_slug, ordinal, text,
      heading_path, source_ref, char_count)

chunk_fts(rowid → chunk.id, text)            -- FTS5 virtual table, BM25

query_log(id, created_at, channel, user_ref, question, intent_slug,
          confidence, fallback_used, status, answer, citations_json,
          retrieved_doc_ids_json, latency_ms, error)

integration(channel PK, display_name, enabled, credentials_encrypted,
            status, last_ok_at, last_error, updated_at)
```

`integration.credentials_encrypted` holds a Fernet-encrypted JSON blob so one column covers both Telegram's single token and Teams' id/password pair. It is the only encrypted column in the database, and the only reason the schema is not four tables.

Alongside the FAISS directory sits `data/index_meta.json`, holding the embedding model name and dimension recorded at first ingest. It is a file rather than a table because it belongs to the index, not to the relational data — deleting `data/` resets both together.

`status` is `success | no_match | failed` and is what the log's Status column renders. `retrieved_doc_ids_json` carries which documents answered the query — a JSON list rather than a join table, which is enough to rank most-accessed documents at MVP volumes and keeps the history readable as a single row per query. `document.sha256` deduplicates re-uploads. `ON DELETE CASCADE` from `document` to `chunk`; `query_log` holds no foreign key to `document`, so deleting a document does not erase the history of it having been used.

## Request flows

### Query (the ≤3s path)

```
1. Telegram poll / Teams activity → InboundMessage{channel, user_ref, text}
2. Adapter        send typing indicator (fire-and-forget)
3. Orchestrator   classify(question) ‖ embed(question)          ← concurrent
4. Orchestrator   confidence >= threshold and slug != general
                     ? spaces = [slug]   (fallback_used = false)
                     : spaces = all      (fallback_used = true)
5. Retriever      vector top-20 over spaces ‖ FTS5 BM25 top-20 over spaces
6. Retriever      RRF fuse → top 5
7. Gate           best dense cosine < relevance_floor → no_match, skip to 10
8. Context        dedupe, reorder, tag, budget
9. Generator      answer; CitationVerifier drops unresolvable markers
10. Adapter       format for channel, deliver
11. Log           write query_log row
```

Steps 3 and 5 each run two operations concurrently. The classification result is needed only to *choose indexes*, not to embed, so the query embedding overlaps the classification LLM call for free.

### Ingestion

```
1. Console → POST /documents (multipart)
2. API      validate extension + size, sha256, reject exact duplicate
3. API      insert document(status=pending), return 202
4. Worker   status=parsing → DocumentLoader → blocks
5. Worker   ragged table regions → LLM restructure (fallback: raw text)
6. Worker   StructuralChunker → chunks with heading_path + source_ref
7. Worker   suggest intent space from names/descriptions + first 2000 chars
8. Worker   Embedder (batched) → IndexWriter → FAISS + chunk + chunk_fts
9. Worker   status=indexed, chunk_count, indexed_at
   on error status=failed, error_message; document row kept for retry
```

A FastAPI `BackgroundTask`, not a queue — a single-process MVP needs no broker. The KB screen polls status.

## Latency budget

Target ≤ 3s. Concurrency per § Request flows.

| Stage | Default (`claude-opus-5` both) | With `claude-haiku-4-5` classify |
| --- | --- | --- |
| Inbound handling | ~30 ms | ~30 ms |
| Classify ‖ embed | ~900 ms | ~350 ms |
| Vector ‖ BM25 search + RRF | ~30 ms | ~30 ms |
| Context build | ~5 ms | ~5 ms |
| Answer generation | ~1400 ms | ~1400 ms |
| Format + deliver | ~250 ms | ~250 ms |
| **Total** | **~2.6 s** | **~2.05 s** |

The default meets the budget with ~400 ms of headroom, which is thin. Two mitigations are specified rather than assumed: the typing indicator goes out before any model call, and `model_classify` is independently configurable — classification emits ~30 tokens and needs far less capability than answer synthesis, so it is the call to make cheap. The channel test reports measured latency so this table can be verified on real hardware instead of trusted.

## Security

Minimal by intent — this is a single-admin demo system — with one exception where the brief is explicit.

**Credential storage is the exception.** The brief names *"Admin credential configuration (secure storage)"* as a core capability, so chat credentials are admin-managed and encrypted at rest:

- Bot tokens live in the `integration` table, encrypted with `cryptography.fernet` using `CREDENTIAL_ENCRYPTION_KEY` from the environment. The key is never persisted.
- The API returns only the last four characters. The plaintext never reaches the console.
- A missing or invalid key **fails startup** rather than silently falling back to plaintext — a fallback would defeat the requirement while appearing to work.
- A credential that cannot be decrypted (key rotated or lost) marks the channel Disconnected with "re-enter credential" rather than crashing the service.
- On first run, if no credential is stored but the matching environment variable is set, that value is used and the console says so. This keeps setup one step without making `.env` the storage mechanism.

Everything else stays at the low bar:

- The console requires one password, `ADMIN_PASSWORD` from `.env`.
- AI provider API keys stay in `.env` — they are operator infrastructure, not admin-configurable integration credentials, so the brief's clause does not apply to them.
- Uploads are checked for extension and size. Crash prevention, not defense.
- Teams inbound activities are authenticated by `botbuilder-core` because the Bot Framework protocol requires it — that comes from the SDK, not from us.

Explicitly not done: admin API tokens, rate limiting, prompt-injection hardening, audit logging, per-user authorization.

## Decisions

1. **Two-method provider interface.** `LLMProvider.complete(system, user, schema?, max_tokens)` and `EmbeddingProvider.embed(texts) / .dimension`. `schema` is the only branch inside implementations. Rejected LangChain — a large dependency tree and its own chain concepts for a surface expressible in ~40 lines.
2. **Hybrid retrieval with RRF.** See § RAG read path. Rejected pure vector (misses exact tokens) and weighted score blending (needs corpus-specific normalization constants).
3. **Exact FAISS index, one per intent space.** See § Storage. Rejected ANN (solves a problem we don't have) and a global index with selectors (version-fragile, awkward reassignment).
4. **Hard filter with General fallback.** Above threshold → search that space only; below threshold or classified General → search all. Rejected soft re-ranking over a global search: more forgiving of misclassification, but it makes routing cosmetic and undemonstrable, and goal 3 is explicitly about routing.
5. **One intent space per document, admin-overridable.** The LLM suggests at upload from space descriptions plus the first 2000 characters; reassignment moves vectors without re-parsing. Rejected per-chunk intent — more accurate for genuinely mixed documents, but it costs an LLM call per chunk and gives the admin no practical way to correct it. Known limitation: a mixed handbook must pick one space; the workaround is splitting the file, documented in the README.
6. **Embedding model pinned once documents exist.** Vectors from different models are not comparable, and cross-index score comparison depends on one shared model. The model name and dimension are recorded on first ingest; a mismatch fails startup with an error naming both models. Recovery is an explicit re-index.
7. **LLM structured-output classification** returning `{intent_slug, confidence, reasoning}`, prompted with each space's config description. Rejected embedding-centroid classification — better calibrated numerically, but weak on short queries and it needs enough documents per space to form a centroid, which a fresh install lacks. Trade-off stated plainly: LLM self-reported confidence is not a calibrated probability, so 0.70 is a tunable heuristic — which is why it is in `config.yaml` and why the history view shows every confidence score.
8. **Channel-aware generation plus deterministic enforcement.** The prompt carries the channel's length limit and markup flavor so the model writes to fit; the adapter then escapes and hard-truncates at a word boundary. Both, because prompt-only makes a hard protocol limit probabilistic.
9. **Telegram long-polling by default.** No inbound URL, no tunnel, no webhook registration to go stale. Webhook mode is available in config for anyone who wants it. Teams has no equivalent — Bot Framework needs a reachable endpoint, which the Emulator provides locally.
10. **`config.yaml` is the single source of truth**, including intent spaces. No settings table. Console edits write the file back and reload in place.
11. **Logging is off the critical path.** The log row is written after the answer is handed to the adapter, and a logging failure is swallowed — an analytics problem must not cost a user their answer.
12. **Analytics is scoped to what the brief names, and nothing more.** The centre of gravity is the query classification log — time, channel, question, detected space, confidence, status — which lives on the Intent Space Configuration screen per §2. The Analytics screen adds only the three things the brief names explicitly: intent space distribution ("common intent spaces"), most accessed documents, and CSV export ("exportable data"), all computed from `query_log` with no extra tables. Cut from the earlier draft, and staying cut: the admin accuracy-review workflow, latency percentiles, unused-document tracking, and no-match question analysis. Per-space "classification accuracy rate" is the confident-classification share defined in § Admin UI layout — one `GROUP BY`, not a review queue.

13. **Keywords are the accuracy-improvement mechanism.** The brief asks for "admin-guided accuracy improvement" and specifies a keywords field in the intent-space editor. Keywords go into the classification prompt alongside name and description. This is why classification is prompt-based rather than embedding-based: a keyword edit changes behaviour on the very next query with no re-indexing, which makes the tuning loop (edit → "Try a query" → observe confidence) a few seconds long.

## Risks / Trade-offs

| Risk | Mitigation |
| --- | --- |
| LLM confidence is uncalibrated; 0.70 may be meaningless in practice | Threshold in `config.yaml`, editable from the console; the history table shows every confidence score so it can be tuned against real traffic. |
| Hard filtering turns a misclassification into a no-match | The relevance gate catches weak retrieval; the General fallback catches low confidence; the history table makes misroutes visible. |
| Two sequential LLM calls threaten the 3s budget | Classify ‖ embed overlap; independently configurable classify model; typing indicator for perceived latency; channel test reports measured latency. |
| Hybrid retrieval doubles the moving parts in the read path | Each retriever is independently testable, and RRF is parameter-free. If BM25 proves useless on the sample corpus, `keyword_top_n: 0` disables it without a code change. |
| Without a reranker, ranking precision is capped and the gate reads the weaker signal | Open decision in § Reranking — recommendation is to add one, which costs less total latency than the status quo. |
| `faiss-cpu` wheels are architecture-sensitive (Apple Silicon) | Pin a known-good version; a smoke test asserts an index round-trips at startup; sqlite-vec is the recorded fallback. |
| Teams against a real tenant needs Azure Bot registration the developer may not have | Develop and demo against the Bot Framework Emulator, which needs no tenant. Azure is an optional deployment step, verified early (task 1.2) so it never blocks the build. |
| Streamlit reruns on every interaction, making upload and polling awkward | Console is a thin API client with no local state; uploads return immediately and status is polled, so a rerun mid-ingest costs nothing. |
| Console writes to `config.yaml` could corrupt it | Validate against the schema before writing; write atomically via temp file + rename; keep the previous version as `config.yaml.bak`. |
| Changing the embedding model silently corrupts retrieval | Model and dimension recorded on first ingest; mismatch fails startup; explicit re-index is the only supported path. |

## Migration Plan

Greenfield. To run:

1. `uv sync`
2. `cp .env.example .env` and set the provider API key, `TELEGRAM_BOT_TOKEN`, and `ADMIN_PASSWORD`
3. `uv run uvicorn app.main:app --port 8000`
4. `uv run streamlit run admin/Home.py` (port 8501)
5. Upload `sample_docs/`, wait for each to reach `indexed`
6. Ask the Telegram bot a question; verify a cited answer and a logged row

Teams: point the Bot Framework Emulator at `http://localhost:8000/api/messages`. A real tenant additionally needs an Azure Bot registration and a public HTTPS URL.

Rollback: stop both processes. Deleting `data/` resets all state.

## Open Questions

**Blocking plan 04: reranking (§ Reranking).** Options A / B / C with a recommendation of B. This changes `spec: knowledge-retrieval` — a new reranking requirement, and the relevance gate reading the cross-encoder score instead of dense cosine — so it must be settled before plan 04 is executed. Plans 01–03 are unaffected either way.

Not blocking: the embedding-model choice is resolved in § RAG write path (`all-MiniLM-L6-v2`, with the swap documented).
