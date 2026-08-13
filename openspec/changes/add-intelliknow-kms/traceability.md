# Requirements traceability

Source: `AD, Tech Lead, AKP.docx` — "Tech Lead (Gen AI Focus) Interview Project Specification – Knowledge Management System".

Every clause of the source document is listed below with where it is covered. `spec: X` refers to `specs/X/spec.md` in this change.

## §1 Scenario — three core pain points

| # | Source clause | Covered by |
| --- | --- | --- |
| 1 | Seamless integration with common frontend communication tools (Telegram, WhatsApp, Teams) | spec: frontend-integration — Telegram + Teams |
| 2 | Backend automatically building/updating a KB from uploaded documents (PDF, Word, Excel) | spec: document-ingestion |
| 3 | Orchestrator categorising queries into predefined intent spaces to route to the relevant domain | spec: query-orchestration, intent-management |
| — | Intuitive knowledge ingestion | spec: admin-console — upload area, search/filter |
| — | Reliable multi-frontend integration | spec: frontend-integration — status, error logging, test |
| — | Precise query classification | spec: query-orchestration — threshold, keywords |
| — | Fast, relevant response delivery | spec: frontend-integration — 3s latency; design § Latency budget |

## §1 Target Outcome (measurable)

| # | Source clause | Covered by |
| --- | --- | --- |
| 1 | Configure and use via ≥2 frontend tools | spec: frontend-integration; spec: admin-console — Frontend Integration screen |
| 2 | Upload documents to build/update KB, ≥2 formats | spec: document-ingestion — PDF, DOCX, XLSX (3 formats) |
| 3 | Define and manage intent spaces | spec: intent-management — create/edit/delete |
| 4 | Submit queries via frontends, categorised into the correct space | spec: query-orchestration |
| 5 | Receive accurate, context-aware responses from the KB | spec: knowledge-retrieval — hybrid retrieval, grounded generation |
| 6 | View query history, classification accuracy, KB analytics (most accessed docs, common intent spaces) | spec: analytics-and-history — log, feedback, distribution, most accessed; spec: intent-management — per-space reviewed accuracy |

## §1 Constraints

| Source clause | Covered by |
| --- | --- |
| Timeline: 7 calendar days | design § Context; tasks.md is ordered as a 7-day plan |
| Solo work | design § Context |
| MVP-focused, no over-engineering | design § Goals / Non-Goals; § Security kept minimal |
| AI guidance — leverage AI for parsing, classification, response generation; document strategic usage | spec: document-ingestion (table restructuring), query-orchestration (classification), knowledge-retrieval (generation); tasks.md — `docs/AI_USAGE.md` |

## §2 Visual Reference Guidance

| Source clause | Covered by |
| --- | --- |
| Modular dashboard, 4 sections + navigation | spec: admin-console — Five core screens |
| Neutral base, per-module accent colours (blue/green/purple) | spec: admin-console — Modular card layout and visual scheme |
| Cards: 12px radius, 16px padding, clear headings | spec: admin-console — same requirement |
| Prioritise key actions | spec: admin-console — "Primary actions are prominent" |
| KB: table columns Name, Upload Date, Format, Size, Status, Actions | spec: admin-console — Knowledge Base Management screen |
| KB: status Processed / Pending / Error | spec: admin-console — "Status values displayed"; design § Admin UI layout maps internal states |
| KB: actions View / Delete / Update | spec: admin-console — row actions, view detail, update re-parses |
| KB: drag-and-drop upload zone, supported formats stated, progress indicator | spec: admin-console — Document upload area |
| KB: search by name/keyword; filter by format, date, intent space | spec: admin-console — Document search and filter |
| Intent: name, description, #documents, classification accuracy | spec: admin-console — Intent Space Configuration; spec: intent-management — count + reviewed accuracy or unavailable state |
| Intent: query classification log (recent queries, detected space, confidence, status) | spec: analytics-and-history — Query classification log; placed on the Intent screen per spec: admin-console |
| Intent: editor form (name, description, keywords) | spec: intent-management — Classification keywords; spec: admin-console — editor form |
| Frontend: card per tool, Connected/Disconnected, API key last 4 digits, test button | spec: frontend-integration; spec: admin-console — Frontend Integration screen |

## §3.1 Functional Requirements

| Source clause | Covered by |
| --- | --- |
| **Multi-Frontend Integration** — integrate 2 tools | spec: frontend-integration |
| Admin credential configuration (secure storage) | spec: frontend-integration — Admin credential configuration, Secure credential storage (Fernet-encrypted at rest, console-managed, last-4 masking) |
| Real-time query/response sync ≤3s latency | spec: frontend-integration — Response latency; design § Latency budget |
| Status monitoring + error logging | spec: frontend-integration — Connection status monitoring, Channel error logging |
| End-to-end test function | spec: frontend-integration — End-to-end integration test |
| **Document-Driven Backend KB** — 2+ formats (PDF, DOCX) | spec: document-ingestion — PDF, DOCX, XLSX |
| AI-powered parsing/structuring of content | spec: document-ingestion — AI-assisted recovery of poorly extracted tables; Structured document loading |
| Intent space association | spec: document-ingestion — Intent space assignment at ingest |
| Manual updates + re-parsing | spec: document-ingestion — Re-parsing, Full re-index; spec: admin-console — update action |
| Semantic search | spec: knowledge-retrieval — Dense vector retrieval (+ keyword retrieval and fusion) |
| Basic error handling | spec: document-ingestion — Ingestion error handling |
| **Orchestrator** — 3 default spaces (HR, Legal, Finance) + custom add/edit/delete | spec: intent-management — 5 defaults incl. Operations and General; custom CRUD |
| AI-powered classification, ≥70% configurable confidence | spec: query-orchestration — embedding-centroid classification with softmax confidence, escalating to an LLM below threshold; spec: intent-management — threshold default 0.70. Confidence is a real probability distribution rather than a model self-report. |
| Fallback to "General" space | Deliberate safety deviation: General is used only for an explicit above-threshold classification; failed or uncertain classification returns a retryable error before retrieval. See design decision 12. |
| Admin-guided accuracy improvement | spec: intent-management — Classification keywords; design § Decision 13 |
| Route queries to relevant KB domains post-classification | spec: query-orchestration — Routing hand-off |
| **Knowledge Retrieval & Response** — concise, cited responses from KB | spec: knowledge-retrieval — Grounded answer generation, Citation verification |
| Adapt format to frontend tools | spec: knowledge-retrieval — Channel-aware answer formatting; spec: frontend-integration — outbound formatting |
| Clear "no match" messaging | spec: knowledge-retrieval — No-match response |
| **Analytics & History** — log queries + metrics (timestamp, intent, confidence, response) | spec: analytics-and-history — Query logging |
| Track KB usage | spec: analytics-and-history — Knowledge base usage |
| Exportable data | spec: analytics-and-history — Data export |
| **Admin UI/UX** — 5 core screens | spec: admin-console — Five core screens |
| Clean, intuitive | spec: admin-console — Modular card layout and visual scheme |
| Mobile-responsive (optional) | Not specified — the source marks it optional |

## §3.1 Tech Stack

| Source clause | Covered by |
| --- | --- |
| Option A: Python (FastAPI/Streamlit) + SQLite/FAISS + AI tools | design § Architecture, § Storage |
| Prohibition: complex frameworks / cloud services; lightweight only | design § Goals / Non-Goals; Docker and tunnel demoted to optional |

## §3 Delivery Requirements

| Source clause | Covered by |
| --- | --- |
| Public GitHub repo (code, docs, AI Usage Reflection) | tasks.md — final group |
| Working demo (deployed/local) with 2 frontend integrations, 2+ sample docs, testable query flow | tasks.md — demo verification; design § Migration Plan |
| Detailed README (setup, tech stack, integration guide) | tasks.md — README task |
| AI Usage Reflection: key moments, faster iteration, adjustments to AI output | tasks.md — `docs/AI_USAGE.md` |
| AI scenario 1: PDF tables (HR salary grids) → structured data, searchable | spec: document-ingestion — AI-assisted recovery of poorly extracted tables; design § RAG write path |
| AI scenario 2: adapt responses to each frontend's format constraints | spec: knowledge-retrieval — Channel-aware answer formatting; design § Decision 8 |

## Deviations from the source document

Two remain, both confirmed with the project owner.

| # | Source says | This spec does | Why |
| --- | --- | --- | --- |
| 1 | "fully functional, **deployed** KMS"; references Render/Vercel | Local run is the supported path; Docker and a public tunnel are optional extras | Confirmed with the project owner. The source's own Delivery section permits "deployed/**local**", so a local demo satisfies it. |
| 2 | "Key Reference: LangChain Document Loaders" | Direct use of `pypdf`/`pdfplumber`/`python-docx`/`openpyxl`, no LangChain | Listed as a reference, not a mandate. LangChain adds a large dependency tree and its own abstractions for a surface we use thinly, against the "lightweight only" prohibition. |

**Resolved:** an earlier draft stored chat credentials in `.env` without encryption. The project owner confirmed that "secure storage" is core functionality, so the spec now requires console-managed, Fernet-encrypted-at-rest credentials with last-4 masking and fail-fast on a missing key.

## Items the source does not require, and that this spec does not build

Recorded so their absence is visibly deliberate: multi-tenancy, RBAC, per-user document permissions, rate limiting, audit logging, conversational memory, streaming responses, automatic document sync from external drives, horizontal scale, OCR for scanned PDFs, and a cross-encoder re-ranker (see design § RAG read path for why the last one is deferred).
