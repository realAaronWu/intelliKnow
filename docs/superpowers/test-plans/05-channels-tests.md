# Test plan — Increment 05 Chat channels

Covers `spec: frontend-integration` (13 req / 38 scen) and the logging requirements of `spec: analytics-and-history`.

**No test contacts a live platform.** Adapters are exercised against captured payloads; delivery is asserted against a stub transport.

## §1 Credential store

| # | Test | Expected |
|---|---|---|
| 1.1 | Save then load | round-trips the original dict |
| 1.2 | **Ciphertext at rest** | the raw database column does **not** contain the plaintext token substring |
| 1.3 | Masked read | returns last four characters only; full value absent |
| 1.4 | Missing key | construction raises; startup fails |
| 1.5 | Malformed key | construction raises |
| 1.6 | **No plaintext fallback** | with no key, saving raises — it never writes plaintext instead |
| 1.7 | Undecryptable credential | raises `CredentialError`, distinguishable from "not configured" |
| 1.8 | Clear | stored value removed; load returns none |
| 1.9 | Environment fallback | with nothing stored and an env var set, that value is used and flagged env-sourced |
| 1.10 | Stored beats environment | a stored credential takes precedence over the env var |

1.2 and 1.6 are the requirement. Every other test here would pass on an implementation that stores plaintext.

## §2 Channel abstractions

| # | Test | Expected |
|---|---|---|
| 2.1 | Types carry required fields | channel, user ref, text, reply ref |
| 2.2 | Non-text payload | `normalize` returns `None` |
| 2.3 | Profile values | max chars and markup match config for each channel |

## §3 Telegram adapter

| # | Test | Expected |
|---|---|---|
| 3.1 | Captured update normalized | correct chat id, user ref, text |
| 3.2 | Send targets the chat | outbound request carries the right chat id and body |
| 3.3 | MarkdownV2 escaping | reserved characters escaped in the sent body |
| 3.4 | 4096 limit enforced | sent body never exceeds the limit |
| 3.5 | Typing sent first | typing action precedes the pipeline call |
| 3.6 | **Typing failure is non-fatal** | typing raises → answer still delivered |
| 3.7 | Sticker/photo update | normalizes to `None` |
| 3.8 | Polling offset | acknowledged offset advances so an update is not reprocessed |

## §4 Telegram webhook mode

| # | Test | Expected |
|---|---|---|
| 4.1 | Correct secret token | request processed |
| 4.2 | Wrong token | rejected; no pipeline run |
| 4.3 | Missing token | rejected |
| 4.4 | Registration on startup | webhook registered when mode is webhook and a base URL is set |
| 4.5 | Registration failure | channel disconnected with the reported reason |
| 4.6 | **Modes are exclusive** | with polling active, no webhook is registered and no message is processed twice |

## §5 Teams adapter

| # | Test | Expected |
|---|---|---|
| 5.1 | Captured activity normalized | correct conversation reference and text |
| 5.2 | Reply targets the conversation | outbound activity carries the same reference |
| 5.3 | Typing activity sent | precedes the pipeline call |
| 5.4 | Bullet rendering | enumerated content formatted as a list |
| 5.5 | Unauthenticated activity | rejected; no pipeline run |

## §6 Inbound handler

| # | Test | Expected |
|---|---|---|
| 6.1 | Happy path ordering | normalize → typing → pipeline → format → send → log |
| 6.2 | **Pipeline failure** | user receives a short failure message; platform still acknowledged |
| 6.3 | No retry loop | the acknowledgement is a success response even on failure |
| 6.4 | Disabled channel | inbound not processed |
| 6.5 | Non-text input | "text only" reply; **zero pipeline calls** |

## §7 Status and error logging

| # | Test | Expected |
|---|---|---|
| 7.1 | Success sets connected | state connected; last-success updated |
| 7.2 | Delivery failure | state disconnected; reason retained |
| 7.3 | Unconfigured | disconnected, not an error state |
| 7.4 | Errors readable without traffic | recent errors retrievable with no message sent |
| 7.5 | Error record fields | channel, timestamp, reason all present |

## §8 Query logging

| # | Test | Expected |
|---|---|---|
| 8.1 | All fields recorded | timestamp, channel, user ref, question, intent, confidence, fallback flag, status, answer, citations, retrieved doc ids, latency |
| 8.2 | **Written after delivery** | assert call ordering — send precedes the log write |
| 8.3 | **Logging failure swallowed** | log write raises → user still received the answer; no exception escapes |
| 8.4 | Status values | exactly one of `success`, `no_match`, `failed` |
| 8.5 | Failed query records the error | error message stored |

## §9 Integrations API

| # | Test | Expected |
|---|---|---|
| 9.1 | List | per-channel status, masked credentials, last success, last error |
| 9.2 | Save applies without restart | a saved credential is usable on the next request |
| 9.3 | Response never carries plaintext | masked only |
| 9.4 | Clear | credentials removed; status disconnected |
| 9.5 | Test succeeds | reports success with measured latency |
| 9.6 | Test fails on bad credentials | failure names the credential problem; status disconnected |
| 9.7 | **Test names the failing stage** | a mid-pipeline failure reports which stage failed |

## Manual verification

| Check | How |
|---|---|
| Real Telegram round trip | Ask the live bot a question; confirm a cited answer within 3s |
| Teams round trip | Point the Bot Framework Emulator at the messaging endpoint; confirm answer and bullet rendering |
| Latency | 20 queries per channel; record p50 and p95 against the 3s target |

## Not automatable

| Scenario | Why | Compensating check |
|---|---|---|
| Live platform delivery | Requires real accounts and network | Manual verification above |
| Bot Framework JWT validation | Owned by the SDK, not our code | Emulator round trip |

## Exit criteria

§1–§9 green. Manual round trips pass on both channels. p95 latency recorded. Clean tester report accepted against `spec: frontend-integration`.
