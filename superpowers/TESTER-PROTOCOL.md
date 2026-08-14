# Clean tester protocol

## Why a separate tester

If the implementer writes the tests, the tests describe what was built. If the tests are handed to the implementer pre-written, the implementer codes to them. Either way the tests and the code share a single interpretation of the spec, and a shared misreading passes silently.

A **clean tester** is a fresh agent that has never seen the implementation plan or the implementation source. It reads the capability specs and the test plan, writes tests from those alone, and runs them against whatever was built. When implementer and tester disagree, one of them misread the spec — and that disagreement is exactly the signal this protocol exists to produce.

## What the tester may and may not read

**May read:**
- `openspec/changes/add-intelliknow-kms/specs/*/spec.md` — the requirements, the source of truth
- `openspec/changes/add-intelliknow-kms/design.md` — for architectural context
- `superpowers/test-plans/<NN>-*-tests.md` — the test plan for this increment
- The **Interfaces** block of the execution plan — public signatures only, so imports and call shapes are correct
- `tests/doubles.py` — the shared test doubles
- Test failure output, tracebacks, and any file a traceback points into

**Must not read before writing tests:**
- The execution plan's task steps
- Implementation source under `app/`

Reading implementation source before writing a test produces a test shaped like the implementation, which cannot detect a wrong implementation. After a test fails, the tester may read source to diagnose — that is debugging, not test design.

## Procedure

1. **Announce.** "I am the clean tester for increment NN. I have read the specs and test plan, not the implementation."
2. **Enumerate.** List every requirement and scenario in the capability specs this increment covers. Build a checklist.
3. **Write tests** into `tests/spec/test_<capability>.py`. One test per scenario where practical. Name each test after its scenario so the mapping is legible: `test_confidence_exactly_at_threshold_uses_classified_space`.
4. **Run** the suite. Record actual output — never assert a result you have not seen (`superpowers:verification-before-completion`).
5. **Triage every failure** into one of three verdicts before reporting:
   - **implementation-defect** — spec says X, code does Y
   - **test-defect** — the test misread the spec
   - **spec-ambiguity** — spec permits both behaviours; needs a human decision
6. **Report** using the template below. Do not fix implementation code. Do not relax an assertion to make it pass.

## Report template

```markdown
## Increment NN — clean tester report

Specs covered: <capability list>
Scenarios enumerated: N
Tests written: N
Passed: N   Failed: N   Not automatable: N

### Failures
| Test | Scenario | Verdict | Evidence |
|---|---|---|---|

### Not automatable
| Scenario | Why | Suggested manual check |
|---|---|---|

### Spec ambiguities found
| Requirement | Both readings | Recommendation |
|---|---|---|

### Coverage gaps
Scenarios with no test, and why.
```

## Rules that keep the check honest

- **Never weaken an assertion to get green.** A failing test is the deliverable working.
- **Never edit `app/`.** The tester reports; the implementer fixes.
- **Report a scenario you could not automate** rather than skipping it silently. Silence reads as coverage.
- **A spec ambiguity is a finding, not a nuisance.** It means two competent readers could build different systems — surface it.
- **Use `superpowers:systematic-debugging`** before concluding a failure is an implementation defect. The most common false verdict is a test-defect misfiled as an implementation-defect.

## Where the tester sits in the loop

```
implementer subagent  →  task review (spec + quality)  →  next task
        ↓
   [ increment complete ]
        ↓
  CLEAN TESTER  →  report  →  implementer fixes defects  →  re-run
        ↓
  green + report accepted → increment done
```

The clean tester runs **once per increment**, after all of that increment's tasks are complete and the implementer's own tests are green. It is a gate, not a substitute for TDD: the implementer still writes their own failing test first for every task.
