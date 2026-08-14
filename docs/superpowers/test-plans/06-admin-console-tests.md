# Admin Console and Delivery Test Plan

## API Tests

- Every `/admin` endpoint rejects missing and incorrect bearer tokens.
- Intent operations enforce unique slugs, protected General behavior, and refusal to delete a space containing documents.
- Runtime configuration accepts intent spaces, confidence threshold, and relevance floor only; invalid and restart-required changes have no side effects.
- Query history is newest-first, paginated, filterable, and exposes detail without secrets.
- Classification feedback records expected intent and correctness. Accuracy includes reviewed rows only; no reviewed rows returns an unavailable state. High-confidence share is never labelled accuracy.
- Analytics period filters apply consistently to distribution, document access, history, and CSV. Empty CSV contains headers.
- Dashboard and integration summaries report failed documents and disconnected channels without crashing on empty data.

## UI Acceptance

- Sign-in hides all admin content until authentication. The browser receives an
  `HttpOnly`, `SameSite=Strict` session cookie that survives page refreshes for
  eight hours; sign-out expires it immediately.
- Sidebar navigation exposes exactly five views at desktop and narrow widths without overlap.
- Dashboard test query shows intent, confidence, grounded answer, sources, and latency.
- Frontend Integration supports masked save/clear/test and readable errors.
- Knowledge Base supports upload/status, search/filter, view, re-parse, reassign, and confirmed delete.
- Intent Configuration supports safe edits, thresholds, query review, and honest unavailable/accuracy states.
- Analytics supports period selection, metrics, query detail, filters, and CSV export.
- Failed requests preserve entered values; destructive actions require confirmation; raw traces and secret values never render.

## Delivery Acceptance

- At least two real documents reach indexed state and answer representative questions with verified citations.
- Real Telegram and Teams round trips pass.
- The labelled question set is run before any accuracy claim.
- Full delivery latency and known deviations are recorded, and setup/integration/troubleshooting documentation is complete.
