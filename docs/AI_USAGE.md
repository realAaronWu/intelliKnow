# AI Usage Reflection

IntelliKnow uses AI where it changes the product outcome, while deterministic code handles validation, storage, lifecycle operations, and security.

## Document parsing and structure

PDF, DOCX, and XLSX loaders first preserve headings, pages, sheets, and tables with deterministic parsers. A language model is used only for table repair when extracted rows are structurally inconsistent. The repaired output is schema-validated before it can enter the knowledge base. This keeps ordinary documents fast and reproducible while giving difficult salary grids and merged tables a bounded recovery path.

The first generated approach treated most extracted content as plain text. It was adjusted to retain source references and table structure because an answer containing the correct number without its row or column context is not reliable knowledge retrieval.

## Intent classification

Intent spaces are represented by their name, description, and admin-editable keywords. Their embeddings form centroids, so the common path can classify a query without an LLM call. When confidence is below the configured threshold, an LLM performs constrained classification against the same current intent definitions.

The implementation was iterated to reuse the query embedding for classification and retrieval, rebuild centroids after live keyword edits, and stop before retrieval when classification is uncertain or unavailable. General is accepted only as an explicit above-threshold model result. Admin review records expected intent and correctness. Only reviewed rows contribute to displayed accuracy; raw confidence is kept as a separate diagnostic.

## Grounded response generation

The model receives only retrieved, reranked context and is asked to answer concisely with source markers. Citation verification removes invalid references. If no citation survives verification, the generated text is discarded and the user receives a clear no-match response. This adjustment prevents fluent but unsupported text from being presented as knowledge.

Formatting is selected by the pipeline's channel profile. Telegram receives escaped, length-limited text; Teams receives its compatible list formatting. Truncation reserves room for at least one verified source, so a long answer cannot become an uncited answer.

## Development acceleration

AI-assisted development was used to compare the original brief with the implementation plan, identify concurrency and security gaps, generate focused test cases, and inspect failure traces. Suggestions were accepted only after repository inspection and automated verification. In particular, broad webhook support, distributed queues, provider hot-swapping, and a second administration backend were rejected because they added complexity without improving the seven-day MVP.

## Limits and human judgment

- AI does not decide whether a classification is correct; an administrator supplies review feedback.
- Accuracy is unavailable until reviewed samples exist, rather than inferred from model confidence.
- HR, legal, and financial answers remain source-backed assistance, not professional advice.
- Live Telegram and Teams acceptance depends on platform credentials and tenant configuration outside the codebase.
