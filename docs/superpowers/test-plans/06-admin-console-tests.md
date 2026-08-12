# Test plan — Increment 06 Admin console

Covers `spec: admin-console` (13 req / 44 scen), the aggregate and export requirements of `spec: analytics-and-history`, and the console-facing requirements of `spec: intent-management` and `spec: configuration`.

**Coverage split, stated up front.** Everything the console *decides* is API behaviour and is automated in §1. Everything the console *renders* is verified manually in §3. This is deliberate: Streamlit UI is expensive to automate and cheap to eyeball, and the spec requires the console to hold no business logic — so automated UI tests would exercise a layer that contains none. If you want automated UI tests anyway, that is a real schedule addition and should be decided before this increment starts.

## §1 API — automated

### Intents

| # | Test | Expected |
|---|---|---|
| 1.1 | List with counts | each space carries its document count |
| 1.2 | Accuracy rate | share of queries into that space at or above threshold, over the period |
| 1.3 | Accuracy derivation string | response includes the text explaining the derivation |
| 1.4 | No queries yet | accuracy reported as unavailable, **not zero** |
| 1.5 | Create | new space usable immediately as a classification and assignment target |
| 1.6 | Duplicate slug | rejected naming the conflict |
| 1.7 | Edit name/description/keywords | all three persist |
| 1.8 | Delete empty space | space and its index removed |
| 1.9 | **Delete space with documents** | refused, message states how many are assigned |
| 1.10 | Delete General | refused, message explains it is the required fallback |
| 1.11 | Re-slug General | refused |

### Config

| # | Test | Expected |
|---|---|---|
| 1.12 | Read effective config | every tunable present |
| 1.13 | **No secrets in response** | no API key, token, or password value anywhere |
| 1.14 | Secret presence indicated | reports whether each required secret is set, and names the variable when missing |
| 1.15 | Update threshold | applies to the next query, no restart |
| 1.16 | Invalid update | rejected; file and running config unchanged |
| 1.17 | Embedding model change with documents | refused, message mentions re-index |

### Analytics

| # | Test | Expected |
|---|---|---|
| 1.18 | Log newest first | ordering correct |
| 1.19 | Pagination | page boundaries correct, no duplicates across pages |
| 1.20 | Filter by intent space | only that space |
| 1.21 | Filter by status | only that status |
| 1.22 | Detail by id | full answer, citations, latency |
| 1.23 | Detail for failed query | recorded error message present |
| 1.24 | Intent distribution | counts per space for the period |
| 1.25 | Most accessed documents | ranked by appearance in retrieved doc ids |
| 1.26 | **Deleted document still attributable** | a deleted document's historical usage still reported |
| 1.27 | CSV export | one row per query; header names every field |
| 1.28 | **Empty period export** | header-only CSV, not an error |
| 1.29 | Period filter honoured | queries outside the range excluded from every metric |

## §2 Console API client — automated

| # | Test | Expected |
|---|---|---|
| 2.1 | Backend unreachable | connection error surfaced; **no stale data returned** |
| 2.2 | Validation error passthrough | backend message preserved for display, not replaced by a status code |
| 2.3 | Auth header attached | admin credential sent with each request |

## §3 Manual verification

Run through with the API and console both running.

### Shell and layout

| Check | Pass condition |
|---|---|
| Password gate | wrong password reveals no screen content or config values |
| Sign-out | returns to the gate |
| Navigation | all five screens reachable and named as the brief specifies |
| Card styling | 12px radius, 16px padding, clear headings, neutral base |
| Module accents | Frontend Integration blue, Knowledge Base green, Intent Space purple |
| Primary actions prominent | Upload, Create Intent Space, Test are visually primary |

### Dashboard

| Check | Pass condition |
|---|---|
| Summary | KB size, per-space counts, channel status, query volume, provider/models, thresholds |
| Problem highlighting | a disconnected channel and a failed document both surface, with links |
| Try a query | returns space, confidence, answer, sources, latency; no chat message sent |

### Frontend Integration

| Check | Pass condition |
|---|---|
| One card per tool | Telegram and Teams each present |
| Status indicator | Connected / Disconnected accurate |
| Credential last-4 | only four characters shown |
| Test button | outcome and latency displayed on the card |
| Unconfigured guidance | states platform requirements and how to obtain the credential |

### Knowledge Base

| Check | Pass condition |
|---|---|
| Columns | Name, Upload Date, Format, Size, Status, Actions |
| Status vocabulary | reads Processed / Pending / Error |
| Drag-and-drop | dropping a supported file starts an upload |
| Supported formats stated | visible in the upload area |
| Progress indicator | reflects processing until Processed or Error |
| Search | name substring narrows the list |
| Filters | format, date, intent space each narrow correctly; combinable |
| View | shows intent space, chunk count, extracted chunks |
| Update | re-parses; status returns to Pending |
| Delete | confirms first; removes from list |
| Error row | shows the message; retry and delete available |

### Intent Space Configuration

| Check | Pass condition |
|---|---|
| Card view | name, description, document count, accuracy rate |
| Derivation stated | accuracy figure carries its explanation |
| Editor form | name, description, keywords all editable |
| Keyword role explained | form states these drive classification |
| Classification log present | recent queries, detected space, confidence, status |
| Log filters | intent space and status both filter |
| Threshold controls | change applies to the next query |
| General protected | no delete action; explanation shown |

### Analytics

| Check | Pass condition |
|---|---|
| Period selector | every metric reflects the range |
| Distribution and top documents | render correctly |
| Log detail | full answer, citations, latency |
| Export | CSV downloads and matches the log |
| Empty period | empty state, not an error |

### Cross-cutting

| Check | Pass condition |
|---|---|
| Destructive confirmation | delete document, delete space, clear credentials all confirm; cancel changes nothing |
| Error feedback | plain language; **entered values preserved** for correction |
| No raw errors | no status codes or tracebacks on screen |

## §4 Keyword tuning loop — end-to-end

The check that proves the brief's "admin-guided accuracy improvement" is functional rather than decorative:

1. Find a question the classifier routes wrongly (use the golden set's misroutes).
2. Add a distinguishing keyword to the correct space and save.
3. Re-ask via "Try a query".
4. **Pass condition:** the detected space changes to the correct one, or confidence for it rises measurably — with no restart and no re-indexing.

## Not automatable

| Scenario | Why | Compensating check |
|---|---|---|
| Visual styling — radius, padding, colours | Screenshot testing is disproportionate here | §3 layout checks |
| Drag-and-drop interaction | Streamlit widget behaviour | §3 KB checks |
| Progress indicator behaviour | Timing-dependent UI | §3 KB checks |

## Exit criteria

§1 and §2 green. Every §3 check passed. §4 loop demonstrated. Clean tester report accepted against `spec: admin-console`, with UI scenarios reported as manual and the manual check recorded as performed.
