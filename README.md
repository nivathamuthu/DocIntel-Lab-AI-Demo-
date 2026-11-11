<<<<<<< HEAD
# DocIntel Lab

A document-AI platform for aviation maintenance documents.
Built with Django, LangChain/LangGraph, Gemini and local embeddings, running in Docker.
It turns uploaded PDFs, DOCX and PPTX files into searchable chunks, a knowledge graph, reviewable business rules, generated Q&A for evaluation, tested code from rules, and a cited compliance agent.

## What it does

| Module | What it proves | Page URL |
|---|---|---|
| 1. Ingest pipeline | Upload, detect, parse, clean, chunk, enrich, embed and index run as Celery stages with retries, dedupe and per-stage timing | http://localhost:8010/ingest/ |
| 2. Prompt library | Versioned Jinja2 prompts with Pydantic-validated output, retry on validation failure, and a v1 vs v2 eval | http://localhost:8010/prompts/ |
| 3. Hybrid RAG | Vector, BM25, hybrid (reciprocal rank fusion) and hybrid+rerank retrieval with cited answers and "I don't know" on weak context | http://localhost:8010/rag/ |
| 4. Knowledge graph and GraphRAG | LLM entity and relation extraction, entity resolution, Neo4j storage, and graph-augmented answers | http://localhost:8010/kg/ |
| 5. Business rules | IF/THEN rule extraction with verbatim source quotes, merge, conflict detection, and an approve/edit/reject review screen | http://localhost:8010/rules/ |
| 6. Q&A generation | Factual, multi-hop and unanswerable questions per chunk, a verbatim-quote check, an LLM groundedness judge, human review, CSV/JSON export, and Search evaluated with the kept set | http://localhost:8010/qa/ |
| 7. Rules to code | Rules turned into a Python function plus pytest tests written from the rule text only, run in a subprocess sandbox with a LangGraph repair loop (max 3 attempts), conflict-aware output and a mutation check | http://localhost:8010/codegen/ |
| 8. Compliance agent | A LangGraph workflow with tools, citation verification, a retry loop and human approval with a Postgres checkpointer | http://localhost:8010/agents/ |

The landing page (http://localhost:8010/) links every module and shows the active LLM and embeddings. http://localhost:8010/results/ shows the latest saved eval for each module.

## Architecture

See [ARCHITECTURE.md](ARCHITECTURE.md) for the components and the data flow from upload to agent, and [docs/pipeline.md](docs/pipeline.md) for the ingest stages.

## Quick start

Requirements: Docker Desktop and PowerShell. Everything, including the tests, runs in containers. There is no host virtualenv.

1. Copy `.env.example` to `.env`.
2. In `.env`, set a real provider. Never commit `.env` or the key.
   ```
   LLM_PROVIDER=google
   LLM_MODEL=gemini-3.5-flash-lite
   GOOGLE_API_KEY=<your key from https://aistudio.google.com/apikey>
   EMBEDDINGS_PROVIDER=local
   EMBEDDINGS_MODEL=BAAI/bge-small-en-v1.5
   INSTALL_ML=1
   ```
   Anthropic and OpenAI are also supported (`LLM_PROVIDER=anthropic` or `openai` with the matching key). `LLM_PROVIDER=mock` exists for the offline test suite only; `demo-reset` refuses to run with it.
3. Start the stack and load the demo corpus:
   ```powershell
   .\make.ps1 up          # build and start web, worker, postgres, redis, neo4j; run migrations
   .\make.ps1 demo-reset  # ingest the 4 corpus documents, extract rules, build the graph
   ```
4. Open http://localhost:8010/.

Run the offline tests with `.\make.ps1 test`. Per-module commands, evals and options are in [docs/MODULES.md](docs/MODULES.md). The recording order for a walkthrough is in [docs/DEMO_ORDER.md](docs/DEMO_ORDER.md).

## Results

Full tables, models, dates and caveats are in [evals/REPORT.md](evals/REPORT.md). All numbers come from real runs with Gemini (`gemini-3.5-flash-lite`) and local `BAAI/bge-small-en-v1.5` embeddings.

| Eval | Result |
|---|---|
| Prompts (10 samples) | v1 mean 0.8, v2 mean 1.0 |
| Retrieval (15 questions) | hit@5 = 1.0 for all four modes; at hit@1 (earlier corpus), BM25 and hybrid+rerank 14/15, hybrid and vector 11/15 |
| GraphRAG (8 multi-hop questions) | hybrid 0.375, GraphRAG 0.562 (graph build 11, 28 chunks) |
| Rules (12 gold rules) | v1 precision 0.167 / recall 0.5; v2 precision 0.471 / recall 0.667 |
| Q&A (56 generated, 53 kept; 22 asked) | Search accuracy: bm25 0.955, hybrid_rerank 0.909, vector 0.864; multi-hop hardest (0.833) |
| Rules to code (5 cases covering 6 rules) | all tests passed 5/5, mean attempts 1.0, conflict surfaced 1/1; mutation check ran on 2 of 5 cases and caught both |
| Agent (6 cases) | intent 6/6, answer 6/6, citations verified 5/5, conflict check 2/2 |

## Limitations

- The eval sets are small (6 to 15 items each), so a single item moves a score a lot.
- Where an eval uses an LLM judge, the same model generates and judges.
- Results vary between runs. The Gemini models used ignore the temperature setting.
- No view has authentication. This is a demo build.
- `DJANGO_DEBUG=true` and the default `DJANGO_SECRET_KEY` are for local use only.
- The Module 7 test sandbox is a demo sandbox, not a security boundary.
- Module 7's tests are written by the same model as the code. Passing them shows consistency with the model's reading of a rule, not that the rule is correct. A failing test is first judged against the rule text: a wrong test is dropped, an ambiguous rule stops the run for review, and a repair may not change a number from the rule text (see [evals/REPORT.md](evals/REPORT.md)).
- Module 6's Q&A pairs have not been reviewed by a person. The evaluation uses kept, unreviewed pairs.

## Applies to insurance and finance

This is a design note. It is not demonstrated in this repo.

The pipeline is not aviation-specific. The domain lives in the prompts, the entity types and the eval sets.

- **Ingest:** policy wordings, endorsements, claim forms and loss-adjuster reports are PDF, DOCX and scanned files. They go through the same stages, with identifiers such as policy numbers and claim numbers in place of part numbers and directive numbers.
- **Rules:** coverage conditions, exclusions and sub-limits are IF/THEN rules with exceptions. The verbatim-quote check and the human review screen apply unchanged.
- **Knowledge graph:** entity types become Policy, Insured, Coverage, Exclusion, Claim and Peril, with relations such as COVERS, EXCLUDES and ENDORSES.
- **Conflict detection:** this finds contradictions such as an endorsement that changes a limit stated in the base wording.
- **Agent:** the agent can answer "Is this claim covered?" by retrieving the policy, checking the applicable rules, citing clauses and pausing for adjuster approval.
=======
# DocIntel-Lab-AI-Demo-
AI Based AVIATION PROJECT
>>>>>>> c561e02e504ac1f1c5f4e4a16a4ce599ee82a4b8
