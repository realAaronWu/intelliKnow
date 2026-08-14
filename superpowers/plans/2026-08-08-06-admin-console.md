# Admin Console and Delivery: Simplified Implementation Plan

**Goal:** Complete the five required administration workflows without duplicating domain logic, then produce credible delivery evidence.

**Depends on:** Task 0 and plan 05.

## Architecture

Use one authenticated FastAPI `/admin` router backed by existing services, and one Streamlit app with sidebar navigation between five view functions. Streamlit is an HTTP client only: it never opens SQLite, touches FAISS, or edits `config.yaml` directly.

## Work

### 1. Complete the admin API

- Add intent list/create/edit/delete with protected General behavior and document-count safeguards.
- Expose effective non-secret configuration; allow only intent spaces, confidence threshold, and relevance floor through the live update operation.
- Add dashboard summary, paginated query history/detail, intent distribution, most-accessed documents, and CSV export.
- Add classification review feedback: expected intent and correct/incorrect outcome.
- Calculate accuracy only from reviewed rows. Expose high-confidence share separately and never label it accuracy.
- Cover endpoint authentication, validation, empty states, feedback, and CSV with API tests.

### 2. Build one Streamlit application

- Centralize API client, bearer token session, sign-in/out, error handling, and restrained shared styling.
- Implement five sidebar views: Dashboard, Frontend Integration, Knowledge Base Management, Intent Space Configuration, Analytics.
- Keep view functions direct. Do not create a component framework for five screens.

### 3. Complete required workflows

- Dashboard: system counts, channel health, provider/model summary, recent volume, problems, and test query.
- Frontend Integration: masked credential setup/clear, status/error display, destination-aware test action.
- Knowledge Base: upload and status polling, search/filters, view chunks, re-parse, reassign, and confirmed delete.
- Intent Configuration: intent edit, safe thresholds, classification log, review feedback, and reviewed accuracy unavailable state.
- Analytics: selected period, intent distribution, most-accessed documents, query detail, filters, and CSV export.

### 4. Delivery evidence

- Label the routing/retrieval question set before reporting accuracy.
- Run automated tests, strict OpenSpec validation, real-channel checks, full workflow checks, and measured latency.
- Finish README setup/configuration/integration/troubleshooting and `AI_USAGE.md`.

## Exit Gate

- All five views work at desktop and narrow viewport sizes without overlap.
- Every administrative request requires authentication and every destructive action confirms.
- Two or more real documents can be uploaded and queried with verified citations.
- Real Telegram and Teams acceptance, labelled quality results, latency results, and known deviations are recorded.
