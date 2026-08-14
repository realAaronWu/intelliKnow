# AI Usage Reflection

## Executive summary

I used AI as an engineering force multiplier across requirements analysis,
specification, architecture, implementation, testing, code review, debugging,
and demo preparation. I did not treat the model as the source of truth or ask it
to generate the whole application in one pass. The source requirements,
OpenSpec artifacts, executable tests, live platform behavior, and my own product
judgment formed a hierarchy of evidence around the AI-generated work.

The most valuable result was not simply faster code generation. AI let me keep
several perspectives active at once: product analyst, architect, implementer,
adversarial tester, operator, and reviewer. I used those perspectives to expose
conflicts early, such as the difference between classification confidence and
knowledge relevance, and to diagnose cross-layer failures later, such as a
Telegram query whose application processing was fast but whose proxy delivery
was slow.

The process was not perfect. I allowed the design to drift from a required
General fallback, briefly over-engineered credential storage, selected Teams
before confirming that I could perform a real tenant test, and deferred the
labelled quality set for too long. Those mistakes were useful because they made
the central lesson concrete: AI can increase implementation velocity much
faster than it increases product judgment. Strong AI-driven development
therefore needs explicit scope, traceability, independent review, and measured
acceptance gates.

At the time of this reflection, the repository contains more than 100 focused
commits and 650 automated tests, with two slow tests deliberately excluded from
the default run. Telegram and WhatsApp have been exercised as real channels;
the Teams adapter is tested against the Bot Framework Emulator but has not been
accepted in a real tenant. The labelled model-quality evaluation and final
full-demo evidence remain open, and I do not present them as complete.

## My operating model for AI-driven development

I organized the work as a controlled loop:

```text
source brief
    -> OpenSpec proposal, design, capability specs, and traceability
    -> Superpowers implementation and test plans
    -> small test-driven increments
    -> independent review and clean-tester checks
    -> live user-facing demo
    -> measured defects and requirement feedback
    -> spec + implementation + test remediation
```

This avoided two common failure modes: letting chat history become the only
specification, and accepting generated code because it looked plausible. Every
important behavior had to move from conversation into a durable artifact or an
executable check.

I also deliberately separated AI roles. An implementation agent could use the
plan and write tests and code, while a clean tester was instructed to derive
its tests from the capability spec without first reading the implementation.
An independent reviewer then evaluated the repository requirement-first and
was allowed to challenge both the code and my design decisions. This role
separation reduced shared-context confirmation bias.

## 1. Requirements and specification writing with OpenSpec

The interview brief mixed business outcomes, detailed functional requirements,
visual guidance, examples, constraints, and delivery expectations in one Word
document. My first use of AI was to decompose that prose into a structure that
could be reviewed and tested.

I used OpenSpec to create:

- A proposal defining the MVP outcome and boundaries.
- A design documenting architectural decisions and trade-offs.
- Nine capability specifications covering providers, ingestion, retrieval,
  orchestration, intents, channels, analytics, configuration, and the console.
- A task list with dependency order and acceptance checkpoints.
- A traceability matrix mapping every source clause to a specification.
- A four-layer test plan covering deterministic behavior, real local storage,
  model quality, and live end-to-end acceptance.

The detailed pass over the original document exposed requirements that a
high-level summary had missed: editable intent keywords, reviewed accuracy per
space, document filters, size and lifecycle actions, classification history on
the Intent screen, upload progress, and specific UI layout cues. This was an
important demonstration of AI's value in exhaustive comparison, but also of
the need to give it the actual source document rather than an abbreviated
prompt.

The OpenSpec artifacts eventually represented 92 requirements and 235
scenarios. More important than the count was the precision of the scenarios.
Examples included confidence exactly at the 0.70 boundary, a clean table making
zero LLM calls, document reassignment making zero embedding calls, and a
generated answer being rejected if no citation could be verified.

OpenSpec improved quality in four ways:

1. **Traceability.** I could identify whether a feature came from the brief, a
   design choice, or a later owner request.
2. **Change control.** When behavior changed, I could update the design,
   capability scenario, task status, implementation, and tests together.
3. **Reviewability.** Reviewers could challenge a requirement independently of
   the implementation.
4. **Honest completion.** Unfinished labelled-quality and full-demo tasks stayed
   unchecked instead of being hidden behind a green unit-test suite.

A strong example is commit `4eafb79`. Earlier iterations changed uncertain
classification to fail closed, even though the brief explicitly required a
fallback to General. An independent review identified the drift. I restored a
narrow General-only fallback for a *valid but low-confidence* result while
keeping provider outages, malformed output, document uploads, and intent
mutations fail-fast. I updated the OpenSpec design, orchestration scenarios,
test plan, traceability, code, and tests in the same remediation. That was both
a technical correction and a lesson in requirement governance.

## 2. Architecture and planning with Superpowers

After specifying what to build, I used the Superpowers planning discipline to
decide how to build it safely within a solo, seven-day MVP. The work was split
into increments for foundation, test data, the RAG write path, the RAG read
path, channels, and the admin console, with a stabilization gate before the
last two.

The inherited rules were explicit:

- Test-driven development: observe a meaningful failure before production code.
- Isolated worktrees for parallel or risky increments.
- Systematic debugging before proposing a fix.
- Verification before claiming completion.
- Task-level and increment-level code review.

My first plans became too detailed and contained implementation and test code.
I recognized that this encouraged agents to transcribe the plan rather than
reason about the requirement. I rewrote the plans to be code-free: execution
plans described behavior, contracts, and dependencies; separate test plans
described observable expectations; only public interfaces were fixed in
advance. This was a meaningful improvement in how I directed AI, not just in
what the AI produced.

I also defined a clean-tester protocol. The tester could read capability specs,
the design, test plans, and public interfaces, but not the implementation before
writing tests. It classified failures as implementation defects, test defects,
or specification ambiguities and was forbidden from weakening assertions to
reach green. This made disagreement useful evidence rather than friction.

The architecture itself was refined through AI-assisted comparison and human
selection:

- FastAPI and Streamlit matched the brief and kept the UI as an API client.
- SQLite stored metadata, history, FTS5 data, and encrypted integration bundles.
- Exact FAISS indexes per intent space kept isolation and lifecycle operations
  simple at MVP scale.
- Dense and keyword results were combined with reciprocal rank fusion, then
  reranked with a small cross-encoder.
- Query embeddings were reused for centroid classification and retrieval.
- Classification confidence and knowledge relevance remained separate gates.
- Deterministic parsers handled normal content; the LLM repaired only
  structurally irregular tables.
- Citation verification, not model instruction alone, decided whether a
  generated answer was allowed to reach the user.

AI was especially helpful in surfacing an incoherent early latency decision. I
had excluded reranking as “too expensive” while still paying for sequential LLM
classification and answer generation. The design comparison showed that moving
classification to embedding centroids on the common path and adding a roughly
200 ms cross-encoder could be both faster and more accurate. This became the
design in commit `328a0c2`.

## 3. Coding with bounded AI autonomy

I used AI to implement narrow increments rather than issue a repository-wide
generation prompt. Commits were kept behavior-oriented so a later reviewer
could reconstruct why a change existed. Foundation interfaces and deterministic
test doubles came first; ingestion, retrieval, orchestration, channels, and UI
were layered afterward.

The AI was productive at writing repetitive provider adapters, Pydantic models,
API payload normalization, channel adapters, and test matrices. My role was to
control boundaries and scrutinize places where plausible code could silently
be wrong: retries, storage consistency, model output validation, citations,
configuration reloads, authentication, and external API delivery semantics.

Several concrete corrections show why that supervision mattered:

- Structured output was initially parsed without validating against the caller's
  requested schema. Tests forced actual schema validation.
- FTS5 used implicit AND for natural-language questions, which made keyword
  retrieval effectively dead. A real demo query exposed it; commit `208cf95`
  fixed token handling and added a regression test.
- Ingestion and query composition accidentally constructed separate vector-store
  instances. The code looked locally correct, but live state did not agree.
  Commit `a4b8d7b` repaired the composition root.
- Pipeline dependencies captured configuration at construction time, so UI
  threshold changes did not affect the next query. The pipeline was changed to
  read live validated configuration.
- A thinking-enabled model exhausted a small `max_tokens` budget before
  producing a complete visible answer. I increased the budget while retaining
  an explicit concise-answer instruction instead of accepting partial text.
- Telegram Markdown formatting caused delivery failures despite a successful
  answer pipeline. Telegram delivery was changed to robust plain text, while
  citations and length limits remained deterministic.

These were not cases where AI “failed” and I replaced it. They were cases where
AI generated a reasonable local solution, and system-level evidence revealed
that the local solution violated a cross-component contract.

## 4. Testing strategy and acceptance discipline

I used AI to expand tests, but not to manufacture confidence. The test strategy
separated four kinds of evidence:

| Layer | Purpose | Evidence |
| --- | --- | --- |
| L1 | Fast deterministic behavior | Unit tests with fake LLM and embedding providers |
| L2 | Real local infrastructure | SQLite, FTS5, FAISS, parsers, encryption, and API integration |
| L3 | Nondeterministic model quality | Human-labelled routing/retrieval set and statistical gates |
| L4 | User acceptance | Real documents, UI workflows, and live channel round trips |

The deterministic provider doubles were important. They allowed retries,
timeouts, malformed structured output, exact vector scores, and call counts to
be tested without turning every commit into a network experiment. At the same
time, I avoided mocking away the storage behavior most likely to fail: tests
used real SQLite/FTS5 and FAISS for write, delete, move, search, and recovery
paths.

I asked AI to write adversarial tests, not only happy paths. Representative
examples were:

- Prove chunks, FTS5 rows, and FAISS vectors agree after every mutation.
- Reject a high fused retrieval rank when the independent relevance score is
  below the answer floor.
- Verify a clean table causes no model cost.
- Verify reassignment moves existing vectors without re-embedding.
- Strip hallucinated citation markers and discard an answer with no verified
  source.
- Confirm encrypted credentials never appear as plaintext in SQLite or API
  responses.
- Confirm a logging failure cannot replace a successfully delivered answer.
- Run the keyword-retrieval feature test in a way that can prove the feature is
  unnecessary, rather than constructing a test that must pass.

I also learned that automated tests are necessary but not sufficient. Manual
use of the five UI screens found an indentation error, unreadable light-theme
text, a crash when `unclassified` was not present in the intent selector,
uppercase slug usability problems, missing multi-file upload, and a session
that demanded login after every refresh. Live Telegram and WhatsApp tests found
polling conflicts, proxy effects, webhook configuration mistakes, expiring
access tokens, and platform delivery costs that adapter tests could not reveal.

The remaining weakness is L3. The repository has strong deterministic coverage,
but the human-labelled holdout set and confusion-matrix report were not
completed. I explicitly avoid calling confidence “accuracy” or using the
model's own labels as ground truth. This is an area I would finish before
claiming production model quality.

## 5. Iteration through real challenges

### Reliable document ingestion

The first parsing approach treated too much content as plain text. AI-assisted
analysis helped separate deterministic structure extraction from bounded model
repair. PDF, DOCX, and XLSX loaders preserve pages, headings, sheets, tables,
and source references. Only ragged tables trigger schema-constrained repair;
clean tables stay deterministic and free of model latency.

Real PDFs exposed subtleties that generated code and synthetic tests missed.
Table text had to be excluded by bounding boxes rather than string matching,
and heading detection needed font-size heuristics. Failed classification also
had to leave no chunks or vectors behind. These issues led to lifecycle tests
that assert consistency across all three stores.

### Classification and admin-guided improvement

The common classification path evolved from an LLM call to centroid routing,
with constrained LLM escalation only for ambiguous queries. Admin-editable
descriptions and keywords rebuild centroids immediately. The UI lets an admin
record the expected intent for a query; exact reviewed repeats and a bounded
set of recent examples influence later routing.

A difficult lesson was separating four different conditions:

1. High-confidence valid classification: route to that intent.
2. Valid classification below threshold: use the required General fallback.
3. Classifier/provider failure: return a retryable error rather than pretending
   General was selected.
4. Low retrieved relevance: return a clear no-match even after valid routing.

Earlier iterations conflated uncertainty with failure in the name of safety.
The final distinction satisfies the requirement without silently indexing a
document or searching unrelated specialist spaces when a provider is broken.

### End-to-end latency

When channel queries exceeded three seconds, I did not begin with speculative
optimization. I added timing for embedding, classification, dense and keyword
retrieval, fusion, reranking, relevance gating, context assembly, generation,
citation verification, formatting, typing indication, pipeline wait, and
platform delivery.

The data showed that generation and occasional LLM classification dominated
application time, while the proxy could add significant channel delivery time.
I reduced context and candidate counts, reused the query embedding, moved typing
off the critical path, reused persistent Telegram and WhatsApp clients, warmed
connections, isolated Telegram polling from delivery pools, and applied retries
only where duplicate sends were not a risk. One measured pipeline benchmark
improved from roughly 2.23-3.09 seconds to 1.51-1.77 seconds, and a real Telegram
trial completed in 2.398 seconds. Later warm platform delivery was approximately
0.5-0.6 seconds for Telegram and below one second for WhatsApp on the demo
network.

This exercise also raised an integrity issue. A proxy-adjusted latency can be a
useful diagnostic, but it must not replace the real acceptance number. Under
demo pressure, subtracting a fixed network allowance would make the dashboard
look better without making the user experience faster. I retained true
end-to-end latency and exposed delivery-free processing latency as a separately
labelled diagnostic instead.

### External channel integration

Telegram polling was the fastest path to a real demo, but it still required
solving duplicate `getUpdates` consumers, proxy configuration, token handling,
and delivery formatting. Teams was implementable with Bot Framework Emulator,
but I could not prove it in a real tenant with a personal account. I added
WhatsApp Cloud API as an accessible second real platform, including challenge
verification, HMAC signature validation, asynchronous webhook handling,
encrypted credentials, and destination-aware tests.

Using AI interactively while configuring Meta and Cloudflare was useful because
the external console differed from older documentation and the failure surface
crossed browser settings, tokens, recipient allowlists, webhooks, tunnels, and
local logs. The important discipline was to confirm each step with an actual
message rather than infer success from a saved configuration screen.

## What went well

### Specifications stayed executable

OpenSpec prevented the design from living only in prompts. The traceability
matrix made omissions visible, strict validation kept the artifacts internally
consistent, and scenario wording supplied concrete test boundaries. Updating
the specification during remediations made it a living contract rather than a
ceremonial document written before coding.

### Superpowers improved both speed and acceptance quality

The Superpowers workflow gave AI agents bounded tasks, explicit TDD behavior,
systematic debugging rules, and verification gates. Rewriting plans to remove
prewritten code reduced copy bias. The clean tester and independent reviewer
created a second interpretation of the requirements, which found real issues
that an implementation-shaped test suite could miss.

### AI accelerated breadth without erasing technical depth

Within a short project I could implement structured ingestion, hybrid retrieval,
reranking, grounded generation, three channel adapters, encrypted credential
management, analytics, and a five-screen console. The resulting code still has
clear ownership boundaries and deterministic lifecycle behavior because AI was
directed at small contracts rather than an undifferentiated application prompt.

### Real usage drove the most valuable iterations

The strongest fixes came from uploading actual files, asking realistic questions,
using Telegram and WhatsApp, watching the Streamlit UI, and reading stage timing
logs. AI made diagnosis faster, but live evidence decided what was true.

### The record is honest

Commit messages explain intent, review findings remain in the repository, and
unfinished acceptance items are still marked unfinished. I consider that an
important Tech Lead behavior: AI makes it easy to produce a persuasive story,
so the engineering record must make unsupported claims difficult.

## What did not go well, pitfalls, and lessons

### 1. I temporarily let a safety preference override a non-negotiable requirement

I removed low-confidence fallback to General and failed closed. The safety
argument was reasonable, but the change was not recorded with the same owner
sign-off as other deviations, and the relevance gate already provided a safer
way to prevent unsupported answers.

**Lesson:** Never silently “improve” a contractual requirement. Record the
conflict, obtain explicit acceptance, and look for a design that satisfies both
the requirement and the risk concern.

### 2. I over-engineered integration credential storage

A working portable Fernet design was replaced with macOS Keychain and Azure Key
Vault abstractions, staged versions, migration, and cloud dependencies. This
contradicted the lightweight, no-cloud MVP constraint, broke portability, and
consumed effort while real acceptance gates remained open. Independent review
caught it, and commit `060bc4c` returned the project to Fernet-encrypted SQLite
bundles with the key stored outside the database.

**Lesson:** Production-minded does not mean maximizing infrastructure. For an
MVP, choose the smallest design that satisfies the explicit threat model and
protect time for user-visible acceptance.

### 3. Planning itself became a form of over-engineering

The first Superpowers plans were thousands of lines and included code. That
created false precision and risked turning agents into typists.

**Lesson:** A good AI plan fixes behavior, interfaces, risks, and checks while
leaving implementation reasoning to the implementer. Plan detail should reduce
uncertainty, not pre-generate the project.

### 4. I chose an integration before proving access to its acceptance environment

Teams was a valid requirement option but a poor demo choice without a business
tenant. The emulator proved adapter behavior, not real Teams delivery. WhatsApp
was added later as the second real channel.

**Lesson:** Treat external account access, approval, test recipients, and token
lifetime as architecture constraints on day one. Select the two channels that
can actually be accepted within the schedule.

### 5. I initially mixed deterministic correctness with model quality

A large green suite proves contracts and failure handling, not classification
or retrieval accuracy on unseen questions. The initial corpus ambition also
grew far beyond the brief before the labelled set was complete.

**Lesson:** Build a small human-labelled holdout early. Never let the same model
create and grade labels, never call confidence accuracy, and report unavailable
quality honestly when there is no independent sample.

### 6. I underestimated provider- and network-specific behavior

Thinking token budgets, proxy handshakes, Telegram polling exclusivity, Meta
temporary tokens, and Teams tenant rules cannot be inferred reliably from local
unit tests. Some failures only appeared during a live demo.

**Lesson:** Add provider preflight, connection warm-up, stage instrumentation,
and real smoke tests early. Separate application processing, platform delivery,
and true end-to-end latency instead of arguing from a single total.

### 7. UI acceptance came too late

The backend had extensive tests while the Streamlit application still contained
discoverability, contrast, upload, session, and runtime-state issues.

**Lesson:** For an admin product, each increment needs a short user journey in
addition to API tests. A function that exists but cannot be found or understood
does not meet the requirement.

### 8. Demo data is part of the system

Very short HR, Legal, and Finance documents did not support convincing answers,
and a Legal demo document initially emphasized finance language enough to be
classified incorrectly. I had to inspect the text actually sent to the
classifier and rewrite the document around its legal purpose.

**Lesson:** Curate demo documents and questions together. Verify ingestion,
classification, retrieval, citation, and wording before relying on a scenario
in a live presentation.

## How I would improve the process next time

1. Convert the brief to OpenSpec and secure agreement on every deviation before
   implementation starts.
2. Verify external accounts and choose two genuinely testable channels on day
   one.
3. Create a small human-labelled routing and retrieval holdout before tuning the
   classifier or relevance floor.
4. Keep Superpowers plans contract-focused and cap planning effort per increment.
5. Define latency as true inbound-to-outbound delivery, instrument each stage
   from the first vertical slice, and state the test network/provider baseline.
6. Complete one thin end-to-end path early: upload one document, ask one real
   channel question, receive one cited answer, and inspect it in analytics.
7. Run UI acceptance after every screen is added, not only after backend work is
   complete.
8. Require every new subsystem to answer: which explicit requirement does this
   satisfy, what acceptance gate does it improve, and what unfinished core work
   will it displace?

## Final reflection

This project deepened my view that expert AI usage is primarily a management
and verification skill. Prompt quality matters, but artifact quality,
decomposition, independent perspectives, and evidence matter more. OpenSpec
gave the work a durable behavioral contract. Superpowers gave AI agents a
disciplined execution loop. Real documents, users, networks, and platform APIs
then challenged both.

What I am most satisfied with is not that AI helped produce a large amount of
working code quickly. It is that the process could detect and reverse my own
bad decisions: requirement drift, unnecessary infrastructure, misleading
metrics, and unproven acceptance. That feedback loop is the capability I would
bring to a Tech Lead role: using AI aggressively for leverage while preserving
human accountability for scope, architecture, security, quality claims, and
the user outcome.

## Evidence map

The following repository artifacts make this reflection auditable:

| Evidence | Artifact |
| --- | --- |
| Architecture decisions and trade-offs | [`openspec/changes/add-intelliknow-kms/design.md`](../openspec/changes/add-intelliknow-kms/design.md) |
| Clause-by-clause coverage and deviations | [`openspec/changes/add-intelliknow-kms/traceability.md`](../openspec/changes/add-intelliknow-kms/traceability.md) |
| Requirements and scenario-level behavior | [`openspec/changes/add-intelliknow-kms/specs/`](../openspec/changes/add-intelliknow-kms/specs/) |
| Layered verification strategy | [`openspec/changes/add-intelliknow-kms/test-plan.md`](../openspec/changes/add-intelliknow-kms/test-plan.md) |
| Completion status and deliberately open gates | [`openspec/changes/add-intelliknow-kms/tasks.md`](../openspec/changes/add-intelliknow-kms/tasks.md) |
| Increment plans and execution discipline | [`docs/superpowers/plans/README.md`](superpowers/plans/README.md) |
| Independent test-role rules | [`docs/superpowers/TESTER-PROTOCOL.md`](superpowers/TESTER-PROTOCOL.md) |
| Per-increment adversarial checks | [`docs/superpowers/test-plans/`](superpowers/test-plans/) |
| Requirement-first self-assessment | [`docs/REQUIREMENTS-AUDIT.md`](REQUIREMENTS-AUDIT.md) |
| Setup and end-to-end demo operations | [`docs/LAPTOP-DEMO-DEPLOYMENT.md`](LAPTOP-DEMO-DEPLOYMENT.md) |
