# AI usage

This repo was built with heavy AI assistance. I set the requirements module by module, reviewed the output, ran it, and asked for fixes. An AI coding agent (Cursor) wrote most of the code.

## What was written with AI assistance

- **Application code.** This covers all Django apps, `core/`, the Celery tasks, the LangGraph agent and the templates.
- **Prompt templates.** These are the files under `prompts/`.
- **Eval datasets.** These are the files under `evals/datasets/`: prompt samples, retrieval questions, KG questions, rule gold set, codegen cases and agent cases.
  - The datasets were drafted with AI help and checked by hand against the synthetic corpus.
  - Because the same process wrote the prompts and the questions, the evals can flatter the system. See the caveats in [evals/REPORT.md](evals/REPORT.md).
- **Synthetic corpus generator.** This is `scripts/seed_demo.py` and the synthetic maintenance documents. The documents are fictional; they are not real airline data.
- **Tests and documentation.** This includes the tests and this documentation (README, ARCHITECTURE, MODULES, REPORT).

## How outputs were verified

- **Offline test suite.** `.\make.ps1 test` runs pytest in Docker with mock LLM and embedding providers. It covers:
  - schema validation and retry;
  - fusion math;
  - entity resolution, including a regression test for serial ranges;
  - the rule quote check, merging and conflict labels;
  - the agent graph and its approval interrupt;
  - every page.

  The GitHub Actions workflow runs the same suite on push.
- **Real-provider evals.** Every number in [evals/REPORT.md](evals/REPORT.md) comes from a real run with Gemini and local embeddings. The provider and model are recorded on each `EvalRun`, and mock results were excluded.
- **Checks in code, not by the model.**
  - Rule source quotes, graph evidence quotes, Q&A supporting quotes and agent citation quotes are matched against the chunk text by code.
  - A real provider with a missing key stops at startup. It does not fall back to the mock.
- **Manual review.**
  - I opened each page in the browser after each module.
  - I read the eval reports, including every unmatched rule and every agent answer.
  - I compared the conflict verdicts with the source documents.
- **Disclosed tuning.** One sentence in the agent's rule-check prompt was added after seeing a failing case. It is disclosed in the report.

## What is not verified

- **Providers and embeddings.** Anthropic and OpenAI LLMs, and OpenAI embeddings, are implemented but were not run end to end for the recorded results. Only Gemini was.
- **Rerank.** The cross-encoder reranker (`RERANK_ENABLED=true`) was not part of the recorded runs. Hybrid+rerank used the lexical rerank.
- **Public corpus.** The optional public FAA corpus (`scripts/fetch_corpus.py`) was not part of the recorded results.
- **Scale and security.** Performance on more than a handful of documents, concurrent users, and security were not tested. No view has authentication.
- **Eval size.** The eval sets are small (6 to 15 items). They show that the pipeline works, not how accurate it is in general.
- **Generated content.** No generated rule, graph edge or answer has been reviewed by an aviation maintenance professional.
