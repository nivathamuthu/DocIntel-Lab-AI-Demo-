# DocIntel Lab

**Document AI for aviation maintenance documents.** Upload PDFs, Word files and PowerPoints; get searchable answers with citations, a knowledge graph, reviewable business rules, generated test questions, tested code, and an AI agent that checks compliance questions with human approval.

Built with Django, LangChain/LangGraph, Gemini, local embeddings (BGE), PostgreSQL + pgvector, Neo4j and Docker.

> Demo build. No login or user permissions. Not for production use.

---

## What it does

```
Upload -> Parse -> Chunk -> Embed + Index -> Search / Graph / Rules -> Agent -> Human approval -> Cited answer
```

| # | Module | What it does | Page |
|---|--------|--------------|------|
| 1 | Ingestion pipeline | PDF, DOCX and PPTX are parsed, cleaned and split into chunks tagged with page, section and part numbers. Duplicates are skipped, failed stages retry. | `/ingest/` |
| 2 | Prompt library | Versioned prompts (v1 simple, v2 engineered) with examples and validated output. | `/prompts/` |
| 3 | Hybrid search | Keyword + vector search, fusion and re-ranking. Answers cite document, page and section, and say "I don't know" when unsure. | `/rag/` |
| 4 | Knowledge graph | Entities and relations are extracted automatically, duplicates merged, every link traced to its source. Answers multi-hop questions. | `/kg/` |
| 5 | Business rules | IF / THEN rules with a word-for-word source quote. Contradictions between documents are flagged. Approve, edit, reject, export. | `/rules/` |
| 6 | Q&A generation | Creates factual, multi-hop and unanswerable questions from the documents and uses them to test the search. | `/qa/` |
| 7 | Rules to code | Turns rules into Python and tests, with a repair loop. Tests come from the rule text, not from the code. | `/codegen/` |
| 8 | Compliance agent | Plans, searches, checks the graph and rules, drafts, verifies citations, then waits for human approval. | `/agents/` |

**Example:** *"Part 65-45210-3 has 11,800 flight hours. Is it due for replacement?"*
The agent finds 12,000 hours in the directive and procedure, finds 11,500 in an engineering order, reports both with their documents, warns about the conflict, and waits for approval.

---

## Quick start

Requires Docker Desktop and a Google AI Studio API key.

```powershell
copy .env.example .env        # then set GOOGLE_API_KEY in .env (never commit it)
.\make.ps1 up                 # start Postgres, Redis, Neo4j, web, worker
.\make.ps1 demo-reset         # ingest the corpus, extract rules, build the graph
```

Open http://localhost:8010/ (Neo4j browser: http://localhost:7474).

Other commands:

```powershell
.\make.ps1 test               # offline test suite (mock model, no key needed)
.\make.ps1 eval               # run the evaluations
docker compose exec web python manage.py generate_qa
docker compose exec web python manage.py eval_qa
docker compose exec web python manage.py eval_codegen
```

More per-module commands: `docs/MODULES.md`.

---

## Results

Every number comes from real runs (Gemini `gemini-3.5-flash-lite`, local `BAAI/bge-small-en-v1.5`). Full tables, dates and caveats: **[evals/REPORT.md](evals/REPORT.md)**.

| Module | Finding |
|--------|---------|
| Prompts | Engineered prompt scored 1.0 vs 0.8. The simple prompt invented rules where the text had none. |
| Search | Keyword search and hybrid + re-ranking beat vector-only on exact part numbers and directive references. |
| Q&A evaluation | 22 generated questions: bm25 0.955, hybrid + re-rank 0.909, vector 0.864. |
| Rules to code | All tests passed in 5 of 5 cases. The conflicting pair returned "conflict" and did not pick a side. |

---

## Limitations

- Small test sets (8-22 items). One question moves a score a lot.
- One model writes, judges and grades the Q&A, so the judge may share its blind spots.
- Passing the generated tests shows the code matches the model's reading of a rule, not that the rule is correct.
- The code sandbox is a demo sandbox, not a security boundary.
- Results vary between runs, because the model ignores temperature.
- Text documents only. Video and scanned images are not supported.
- No authentication. Debug mode and the default secret key are for local use only.

---

## Beyond aviation

The same pipeline (ingest, rules, knowledge graph, agent) fits insurance and finance: policy and claims documents become searchable, rules and a graph, and an agent answers compliance questions with sources. This is a design idea; this repo demonstrates aviation only.

---

## More

- [ARCHITECTURE.md](ARCHITECTURE.md): components and data flow
- [AI_USAGE.md](AI_USAGE.md): how AI assistance was used and what was verified
- `docs/demo_scripts/`: step-by-step demo for each module

License: MIT