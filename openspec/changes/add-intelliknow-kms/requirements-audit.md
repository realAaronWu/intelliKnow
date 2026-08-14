# Requirements Audit and Hiring Review

Audit date: 2026-08-13

Source: `AD, Tech Lead, AKP.docx`

## Executive assessment

IntelliKnow covers nearly all functional surfaces in the interview brief and is
stronger than a typical seven-day prototype in failure handling, tests, source
grounding, and operator documentation. It is not yet a fully evidenced
submission: real Microsoft Teams delivery, the labelled model-quality report,
and the final full-demo acceptance remain open.

Hiring-manager score: **83/100 - strong pass, with a required technical
follow-up on evaluation discipline and MVP prioritization.**

| Area | Score | Assessment |
| --- | ---: | --- |
| Requirement and product fit | 22/25 | Broad coverage; fail-closed classification is an explicit owner-directed safety deviation from General fallback |
| Architecture and technical judgment | 18/20 | Clear single-process boundaries and grounded RAG; credential storage has been returned to a portable MVP-sized design |
| Implementation quality and reliability | 18/20 | Focused components, fail-fast behavior, recovery paths, and 636 passing automated tests |
| AI quality and evidence | 12/20 | Good AI placement and citation controls; no completed labelled holdout/confusion-matrix report |
| UX, operations, and delivery | 13/15 | Five usable views, guides, HTTPS, and lifecycle helper; real Teams and final acceptance evidence remain open |

## Requirement status

| Brief outcome | Status | Evidence or gap |
| --- | --- | --- |
| Two frontend integrations | Partial acceptance | Telegram polling and Teams Bot Framework endpoint/emulator are implemented. Real Teams tenant send/receive is not verified. |
| Secure admin channel credential configuration | Meets MVP | Fernet ciphertext in SQLite, a database-external key, last-four masking, and fail-closed reads provide portable laptop storage. Cloud vault lifecycle is explicitly out of scope. |
| Channel response within 3 seconds | Partial acceptance | End-to-end timings are logged and recent average is close to the limit. A repeatable two-channel latency report under stated normal conditions is still required. |
| PDF and DOCX ingestion | Meets | PDF, DOCX, and XLSX parsing, structural blocks, tables, validation, reparse, reassign, delete, and hybrid indexes are implemented. |
| AI-powered parsing | Meets | Deterministic parsing handles normal content and schema-constrained LLM repair handles structurally inconsistent tables. |
| Intent CRUD and configurable threshold | Meets | Default/custom intents, protected General, validated CRUD, live keywords, and threshold controls are implemented. |
| Admin-guided classifier improvement | Meets after audit revision | Expected-intent review labels now affect exact repeats, bounded centroid examples, and low-confidence LLM escalation. A separate holdout must measure generalization. |
| Relevant, concise, cited responses | Meets structurally; quality evidence open | Hybrid retrieval, reranking, relevance gate, grounded generation, citation verification, and no-match behavior are implemented. Expected-document accuracy has not been reported on a labelled set. |
| Query history and analytics | Meets | Query outcomes, intent/confidence, stage and end-to-end timings, best relevance, reviewed accuracy, document usage, filters, details, and CSV export are implemented. |
| Five-view admin console | Meets | All five required views exist. KB upload accepts multiple files and exposes required search/filter controls. Intent editing sits beside cards; review is at the bottom. |
| Local demo and documentation | Mostly meets | Laptop deployment, integration guides, local Teams demo, and AI reflection exist. Final clean-machine run evidence remains to be recorded. |

## What is notably good

- The RAG answer path rejects generated text without verified citations instead
  of presenting plausible unsupported prose.
- Classification and document ingestion fail before retrieval/index writes when
  the model is unavailable or below the required confidence. This deliberate
  owner override is safer than the brief's automatic General fallback.
- Query embeddings are reused, local models are preloaded, and stage timings
  make latency investigations concrete.
- Streamlit remains an API client. It does not bypass service boundaries by
  editing SQLite, FAISS, or configuration directly.
- Tests scale with risk: protocol payloads, real SQLite/FTS5/FAISS behavior with
  fakes, encrypted credentials, lifecycle operations, and UI-facing APIs are
  covered.
- The AI usage reflection distinguishes deterministic work, model work, human
  labels, and the limits of confidence-as-accuracy.

## Missing or still weak

1. Build the labelled routing/retrieval question set and publish a report with a
   confusion matrix, expected-document citation rate, no-match checks, and a
   disjoint before/after feedback evaluation. Do not grade on reviewed examples
   that the classifier has already seen.
2. Complete one real Teams tenant round trip and record setup, payload outcome,
   cited answer, and full delivery latency. The emulator proves the adapter, not
   Teams integration acceptance.
3. Run and record the final clean-laptop scenario with at least two uploaded
   source documents, both frontend paths, all five admin views, and repeated
   latency measurements. The current clean database is useful for manual upload
   testing but is not submission evidence.
4. Keep measuring channel latency. The three-second requirement is met only when
   outbound delivery finishes within the budget under a documented provider,
   corpus, network, and warm-model condition.

## Over-engineering assessment

The original brief is a local seven-day MVP. Azure Key Vault, managed identity,
certificate-based Teams authentication, and credential rollback/audit were
removed because they obscured the unfinished quality and Teams acceptance
gates. Local HTTPS and eight-hour browser sessions remain owner-requested
extensions with direct laptop-demo usability value.

Hybrid FAISS/FTS5 retrieval and cross-encoder reranking are more sophisticated
than the minimum brief, but they directly improve semantic/exact-token retrieval
and are instrumented. Keep them while they stay inside the measured latency
budget. The three LLM-provider adapters are useful seams but should not grow into
runtime provider hot-swapping during this MVP.

## Release recommendation

Use the current build for a local hiring demo. Describe it as functionally broad
and well tested, not production complete. A top-tier score requires the labelled
quality report and real Teams evidence; finishing more infrastructure before
those two items would be the wrong priority.
