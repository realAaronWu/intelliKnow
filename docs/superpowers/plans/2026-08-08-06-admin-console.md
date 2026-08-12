# Admin Console Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development. Steps use checkbox (`- [ ]`) syntax.
>
> **No implementation code in this plan.** Test expectations: `docs/superpowers/test-plans/06-admin-console-tests.md`.

**Goal:** Build the five admin screens the project brief specifies, with the layout and visual scheme its visual guidance describes.

**Architecture:** Streamlit as a pure HTTP client of the admin API. It holds no business rules and never touches the database, the indexes, or the configuration file directly — so every rule lives in exactly one place and the console stays testable by testing the API.

**Tech Stack:** Streamlit, `httpx`, FastAPI (remaining admin endpoints)

## Global Constraints

- The console reads and writes **only** through the backend API.
- Five screens, persistent navigation: Dashboard, Frontend Integration, Knowledge Base Management, Intent Space Configuration, Analytics.
- Cards: 12px corner radius, 16px padding, clear headings, neutral white/light-grey base.
- Module accents: Frontend Integration blue, Knowledge Base green, Intent Space purple.
- Document status renders as **Processed / Pending / Error** regardless of internal state names.
- Destructive actions require confirmation.
- Business logic is tested at the API layer; the console is verified manually (see `test-plan.md` §8 for why).

---

### Task 1: Remaining admin API endpoints

**Files:** Create `app/api/intents.py`, `app/api/config.py`, `app/api/analytics.py` · Test `tests/test_intents_api.py`, `tests/test_config_api.py`, `tests/test_analytics_api.py`

**Behaviour:**
- **Intents**: list with per-space document count and classification accuracy rate; create, edit (name, description, keywords), delete. Duplicate slug rejected. General cannot be deleted or re-slugged. Deleting a space with documents is refused with the count.
- **Accuracy rate** is the share of queries classified into that space at or above the threshold, over a period. Not human-verified correctness — the API returns the derivation string alongside so the UI can state it.
- **Config**: read effective configuration with **all secrets omitted**; update with validation; embedding-model change refused while documents exist.
- **Analytics**: query log paginated newest-first with intent and status filters; detail by id; intent distribution; most-accessed documents; CSV export including the header-only empty case.

This is API work, so it is genuinely unit-testable — everything the console displays is asserted here rather than through the UI.

- [ ] Write failing tests · [ ] Confirm fail · [ ] Implement · [ ] Confirm green · [ ] Commit

---

### Task 2: Console shell

**Files:** Create `admin/Home.py`, `admin/lib/api.py`, `admin/lib/theme.py` · Test `tests/test_admin_api_client.py`

**Behaviour:**
- Password gate; nothing is rendered before authentication, including configuration values.
- Sign-out returns to the gate.
- Persistent navigation across the five screens.
- Shared CSS: 12px radius, 16px padding, neutral base, per-module accent.
- API client with a readable error path — a backend that is unreachable shows a connection error and **never presents stale data as current**.

- [ ] Write failing tests for the client · [ ] Confirm fail · [ ] Implement · [ ] Confirm green · [ ] Commit

---

### Task 3: Dashboard

**Files:** Create `admin/pages/1_Dashboard.py`

**Behaviour:**
- Knowledge base size, per-space document counts, per-channel status, recent query volume, active provider and models, current thresholds.
- Problems — a disconnected channel, a failed document — surfaced prominently with a link to the screen that resolves them.
- **"Try a query"** box: submits to the admin test-query endpoint and shows detected space, confidence, answer, sources, latency. This is the fast half of the keyword-tuning loop: edit keywords, re-ask, watch the confidence move.

- [ ] Build · [ ] Verify against the running API · [ ] Commit

---

### Task 4: Frontend Integration screen

**Files:** Create `admin/pages/2_Frontend_Integration.py`

**Behaviour:**
- One card per tool, accented blue.
- Connected / Disconnected indicator, last success, last error.
- Credential entry and save; redisplay masked to last four characters; clear with confirmation.
- Test button showing outcome and measured latency on the card.
- Unconfigured channels show what the platform requires and how to obtain the credential.

- [ ] Build · [ ] Verify · [ ] Commit

---

### Task 5: Knowledge Base Management screen

**Files:** Create `admin/pages/3_Knowledge_Base.py`

**Behaviour:**
- Accented green. Table columns: Document Name, Upload Date, Format, Size, Status, Actions.
- Prominent drag-and-drop upload zone stating supported formats, with a processing progress indicator until Processed or Error.
- Search bar by name or keyword; filters by format, upload date, intent space.
- Row actions: View (intent space, chunk count, extracted chunks), Update (re-parse), Delete with confirmation.
- Error status shows the recorded message with retry and delete available inline.
- Intent reassignment from the row.

- [ ] Build · [ ] Verify · [ ] Commit

---

### Task 6: Intent Space Configuration screen

**Files:** Create `admin/pages/4_Intent_Configuration.py`

**Behaviour:**
- Accented purple. **Card view** per space: name, description, associated document count, classification accuracy rate — with the derivation stated next to the figure so it is not mistaken for human-verified accuracy.
- Editor form: name, description, **keywords**, stating that description and keywords drive classification.
- **Query classification log lives on this screen**, per the brief's visual guidance: recent queries, detected space, confidence score, response status, filterable.
- Confidence threshold and relevance floor controls.
- General shows no delete action and states that it is the required fallback.

- [ ] Build · [ ] Verify · [ ] Commit

---

### Task 7: Analytics screen

**Files:** Create `admin/pages/5_Analytics.py`

**Behaviour:**
- Period selector governing every metric on the screen.
- Intent space distribution; most accessed documents.
- Query log, newest first, filterable, with a detail view showing full answer, citations, latency, and the error message for failed queries.
- CSV export.
- Empty period shows an empty state, not an error.

- [ ] Build · [ ] Verify · [ ] Commit

---

### Task 8: Cross-cutting UI behaviour

**Files:** Modify all pages

**Behaviour:**
- Every destructive action confirms first; cancelling changes nothing.
- Backend validation errors display in plain language and **preserve the admin's entered values** for correction.
- No raw status codes or tracebacks reach the screen.

- [ ] Build · [ ] Verify · [ ] Commit

---

## Increment exit

1. Task 1 API tests green.
2. All five screens manually verified against `test-plan.md` §7.
3. `superpowers:requesting-code-review`.
4. **Clean tester run** against `spec: admin-console` (13 requirements, 44 scenarios) — API-backed scenarios automated, UI scenarios reported as manual with the check performed.

## Self-review

**Spec coverage.** `spec: admin-console` in full; the aggregate and export requirements of `spec: analytics-and-history`; the console-facing requirements of `spec: intent-management` and `spec: configuration`.

**Deliberate coverage choice.** 13 requirements and 44 scenarios sit almost entirely at manual verification. Streamlit UI is expensive to automate and cheap to eyeball, and the spec requires the console to hold no business logic — so what automation would exercise is already covered through the API in Task 1. This is recorded in `test-plan.md` §8 and §10 rather than left implicit. If automated UI tests are wanted, that is a real addition to the schedule and should be decided before this increment starts.

**Type consistency.** Every screen consumes the API surfaces from plans 03 (documents), 05 (integrations), and Task 1 here (intents, config, analytics). The console defines no domain types of its own.
