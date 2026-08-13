# Task 11a — calibration (bounded sanity check)

> Relocated from the gitignored `.superpowers/sdd/2026-08-08-04-rag-read-
> path/` bookkeeping directory (M1, whole-branch review) — `config.yaml`'s
> `relevance_floor` and `centroid_temperature` comments cite this file's
> provenance for their shipped defaults, and that pointer dangled on a
> fresh clone while this report lived somewhere gitignored. Content
> otherwise unchanged from the original pass.

**Status: bounded sanity check only. Real calibration against increment
02's labelled question set is still pending** — that set was deferred,
and the shipped corpus is six synthetic fixtures, which cannot support a
meaningful accuracy figure. No accuracy number is reported here; none
should be trusted if one shows up elsewhere claiming to derive from this
run.

## Method

1. Ingested the fixture corpus (`tests/fixtures/docs/*.pdf`, `*.docx`,
   `*.xlsx`, six documents, 13 chunks) via `scripts/ingest.py`, using the
   shipped default config (`llm.provider: local`, `embedding.provider:
   local` — no API key, no real API call).
2. Ran `scripts/calibrate.py`, which:
   - Runs the three sanity checks the project owner specified.
   - Sweeps `centroid_temperature` over `[0.01, 0.03, 0.05, 0.10, 0.20,
     0.50, 1.0]`, reporting confidence on the HR and ambiguous questions
     at each value.
   - Sweeps `relevance_floor` over `[0.30, 0.35, 0.40, 0.45, 0.50, 0.55,
     0.60]`, reporting pass/fail on the in-corpus and out-of-corpus
     questions at each value.
3. No local LLM server (Ollama) was reachable in this environment, so
   every escalation call fails with a provider error. This is visible in
   the sanity check below and does not block it: `classify()`'s
   `failed=True` fallback path is exactly what's being exercised, and is
   already covered directly (with a real assertion on the returned
   `Classification`) by `tests/test_classify.py::test_9_8_9_9_...`.

## Sanity check — result: all three pass

| # | Check | Question | Result |
|---|---|---|---|
| 1 | Clearly-HR scores high centroid confidence | "How many days of annual leave do full-time employees get?" | top space `hr`, confidence **0.9935** — comfortably above the 0.70 threshold |
| 2 | Deliberately ambiguous question falls below threshold and escalates | "Is there an update on my situation from before?" | centroid confidence **0.4688** (below 0.70) → escalation attempted (`classified_by=llm`); the call itself failed only because no local LLM server was reachable in this environment, which is the documented fallback behaviour, not a defect |
| 3 | Content absent from the corpus is rejected by the gate | "What is the departure gate for tomorrow's 9am flight to Tokyo?" | best normalized reranker relevance **0.0000**, against **0.9999** for the in-corpus HR question — the gate at the shipped floor (0.45) rejects it correctly |

The first ambiguous-question wording tried ("What's the process for that
thing from before?") scored 0.8591 confidence — it accidentally shared
vocabulary with the Operations space's description ("Internal
processes..."), so it did *not* exercise the escalation path at all. This
is itself a useful, if small, finding: a question needs to be checked for
accidental keyword overlap with `intent_spaces` descriptions before it can
be trusted as a "genuinely ambiguous" test case — worth carrying into the
real golden-question-set design.

## Temperature sweep

| temperature | HR confidence | ambiguous confidence |
|---|---|---|
| 0.01 | 1.0000 | 0.8957 |
| 0.03 | 0.9999 | 0.5960 |
| **0.05 (shipped default)** | **0.9935** | **0.4688** |
| 0.10 | 0.8666 | 0.3450 |
| 0.20 | 0.5646 | 0.2737 |
| 0.50 | 0.3267 | 0.2294 |
| 1.00 | 0.2585 | 0.2146 |

At the shipped default (0.05), the HR question sits far above the 0.70
threshold and the ambiguous question sits far below it — a wide margin on
both sides. Raising temperature to 0.20 or beyond would push the *HR*
question's confidence below threshold too, which is clearly too soft.
Lowering temperature sharpens further but the shipped value already
separates these two example points cleanly, so there is no evidence here
to move off of it.

**Decision: keep `centroid_temperature: 0.05`.** Two data points is not
a calibration; it is confirmation that the shipped default is not
obviously wrong.

## Floor sweep

| floor | HR question | out-of-corpus question |
|---|---|---|
| 0.30 – 0.60 (every value tried) | pass (0.9999) | reject (0.0000) |

Every candidate floor in the swept range draws the same conclusion,
because the synthetic corpus produces near-total separation (relevance
~1.0 for genuinely relevant content, ~0.0 for the unrelated question) —
there is no close case in this data to locate a precise boundary with.
That is a property of a six-document synthetic fixture set having no
genuinely *hard* negatives (a plausible-sounding but wrong space, a
near-miss topic), not evidence that any floor in this range is equally
good in general.

**Decision: keep `relevance_floor: 0.45`.** It sits safely inside the
only band this sweep can observe. The real discriminating test — a
near-miss negative that a weaker floor would wrongly admit — needs harder
questions than six synthetic fixtures can produce.

## What changed

Nothing in `config.yaml`. Both shipped defaults survive this sanity
check; `scripts/calibrate.py` deliberately does not call
`ConfigService.update()` (which would round-trip the file through
`yaml.safe_dump` and silently discard every hand-written comment in it —
acceptable for an admin API PATCH, not for a one-off calibration pass),
so any future change to these two values should be a direct, reviewed
edit to the two lines in `config.yaml`, informed by evidence like this.
The inline `CALIBRATE` comments next to both values were updated to
point at this report instead of standing as bare TODOs.

## Environment notes for whoever runs this again

- `scripts/ingest.py` and `scripts/calibrate.py` were both run for real
  in this pass — real local `sentence-transformers` embeddings, a real
  `faiss` `VectorStore`, and (in `calibrate.py`'s floor sweep) a real
  `cross-encoder` reranker, all in the same process. Neither aborted.
  This is one successful data point against the interpreter-abort risk
  flagged for Task 13's demo (`sentence_transformers`/torch initializing
  after `faiss` in one process) — see that task's section of the main
  report for the caveat that one success does not make it verified in
  general.
- No local LLM server was reachable, so every escalation attempt in this
  run resolved via the `failed=True` fallback path rather than a real
  model response. Re-running with Ollama (or `.env`'s configured
  Anthropic/OpenAI credentials, after deliberately switching
  `llm.provider` — **not done here**, to avoid any real paid API call in
  an automated pass) would exercise the actual LLM-classification
  content, which this pass could not.
