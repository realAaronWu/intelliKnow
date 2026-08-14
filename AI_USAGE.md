# AI Usage Reflection

Estimated reading time: 5 minutes.

## How I used AI

I used AI as an engineering force multiplier across requirements analysis,
specification, architecture, implementation, testing, review, debugging, and
demo preparation. I did not treat the model as the source of truth. The source
brief, executable specifications, tests, live platform behavior, and my own
technical judgment formed the evidence hierarchy around AI-generated work.

My working loop was:

```text
source brief
  -> OpenSpec requirements and design
  -> Superpowers implementation and test plans
  -> small test-driven increments
  -> independent review
  -> live user demo and measured feedback
  -> specification and code correction
```

This structure prevented chat history from becoming the specification and
prevented plausible-looking generated code from being accepted without proof.

## OpenSpec: turning prose into a contract

The interview brief mixed business goals, detailed behavior, visual guidance,
constraints, examples, and delivery expectations in one document. I used AI to
decompose it into an OpenSpec proposal, design decisions, capability specs,
traceability, tasks, and a layered test plan.

That process found details that were easy to miss in a high-level reading:
editable intent keywords, reviewed accuracy per intent, document lifecycle
actions, multiple-file upload, classification history, source citations, and
specific UI views. Each important clause became a scenario or an explicitly
recorded deviation.

OpenSpec improved acceptance quality in three ways:

1. It made scope visible before coding began.
2. It gave implementation agents stable behavioral boundaries.
3. It made later reviews requirement-first instead of code-first.

The specification was not static. When live use exposed a conflict, such as my
temporary removal of the required General fallback, I corrected both the design
and implementation. That kept documentation from becoming a historical story
that no longer matched the product.

## Superpowers: disciplined AI execution

I used Superpowers to split the work into dependency-ordered increments with a
separate test plan for each one. The plans defined behavior, acceptance gates,
and likely failure modes without dictating every implementation detail.

I also separated AI roles. An implementation agent wrote code against the plan;
a clean tester derived adversarial checks from the capability spec; and an
independent reviewer challenged both the code and the design. This reduced the
confirmation bias that appears when one long-running agent reviews its own
assumptions.

The result is a repository with more than 100 focused commits and 650 automated
tests. The important point is not the count. The tests cover protocol payloads,
real SQLite/FTS5/FAISS behavior, document lifecycle operations, encrypted
credentials, retrieval and citation gates, and API contracts. Slow model tests
remain explicit rather than making the normal development loop unreliable.

## Where AI created the most leverage

### Requirements and architecture

AI was strong at exhaustive comparison and dependency mapping. It helped
separate deterministic parsing from model-assisted table repair, classification
from retrieval relevance, and application processing from platform delivery.
Those distinctions produced a small modular architecture instead of one opaque
RAG function.

### Coding and tests

AI accelerated repetitive provider adapters, protocol normalization, fixtures,
and edge-case tests. I kept changes bounded to existing module boundaries and
required focused tests before broader integration. This let breadth increase
without giving up reviewability.

### Debugging real behavior

The best iterations came from real use, not synthetic tests. AI helped correlate
logs, query timings, provider behavior, proxy routes, and platform responses,
but measurements decided the fix.

- A Finance answer failure traced back to document classification, not
  retrieval.
- Telegram's `getUpdates` conflict revealed two active pollers using one token.
- End-to-end latency was separated into classification, retrieval, generation,
  and channel delivery instead of being guessed from one total.
- WhatsApp debugging required matching the Meta sender, Phone-number ID, WABA
  subscription, webhook, and recipient state.

These were useful Tech Lead exercises because the fault crossed product,
application, model, network, and third-party boundaries.

## What went well

- **Traceability stayed durable.** Requirements moved from conversation into
  OpenSpec scenarios and tests.
- **Plans made AI work reviewable.** Superpowers kept increments small and made
  unfinished acceptance gates visible.
- **Independent roles found real mistakes.** A separate review challenged
  requirement drift, over-engineering, and unsupported quality claims.
- **The product failed visibly.** Provider and classification failures became
  actionable errors rather than silent bad data.
- **Live demos drove useful changes.** Multi-file upload, clearer intent review,
  channel diagnostics, model preloading, and deployment cleanup all came from
  actual operator friction.

## What did not go well

### I overrode a non-negotiable requirement

I initially preferred fail-closed query classification and removed the General
fallback. The reasoning was defensible, but the brief was explicit. The better
solution was to keep the fallback and let the relevance gate prevent unsupported
answers. Lesson: label requirements as mandatory, configurable, or negotiable
before optimizing them.

### I over-engineered credential storage

AI helped design an Azure Key Vault and macOS Keychain abstraction with rotation
states before the core acceptance gates were finished. It violated the local
MVP constraint and harmed portability. I replaced it with Fernet-encrypted
SQLite values and a separate environment key. Lesson: every new subsystem must
name the requirement it satisfies and the higher-priority work it displaces.

### I selected Teams before proving access

The adapter and Emulator flow work, but I could not complete real Teams tenant
acceptance without a business tenant. WhatsApp later provided the second real
channel. Lesson: validate external accounts, permissions, and test environments
before committing the delivery plan.

### I trusted synthetic confidence too early

Classifier confidence is not accuracy, and reviewed examples are not a clean
holdout set. I corrected the UI language and kept labelled quality evaluation as
an explicit gap. Lesson: AI quality claims require independent labelled data,
not model self-reporting or examples already used for guidance.

## What I would do next time

1. Freeze mandatory clauses and acceptance environments before implementation.
2. Prove one thin end-to-end path, including a real channel, before expanding.
3. Build the labelled evaluation set alongside the first classifier.
4. Review scope after every increment and remove infrastructure that does not
   improve an acceptance gate.
5. Run UI and deployment acceptance continuously, not only near the end.

## Final reflection

This project reinforced that expert AI usage is primarily a management and
verification skill. Prompt quality matters, but decomposition, durable
artifacts, independent perspectives, and evidence matter more. OpenSpec gave the
agents a behavioral contract; Superpowers gave them an execution discipline;
real users, documents, networks, and platform APIs challenged the result.

The capability I would bring to a Tech Lead role is not simply generating more
code. It is using AI aggressively for leverage while retaining human
accountability for scope, architecture, security, evidence, and user outcomes.

## Evidence

- [OpenSpec design and capability specs](openspec/changes/add-intelliknow-kms/)
- [Superpowers plans and independent test protocol](superpowers/)
- [Requirements audit](openspec/changes/add-intelliknow-kms/requirements-audit.md)
- [Deployment guide](docs/DEPLOYMENT.md)
