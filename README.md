# DocIntel Lab

**Document AI for aviation maintenance documents.** Upload PDF, Word and PowerPoint files and get searchable answers with citations, a knowledge graph, reviewable business rules, generated test questions, tested code, and a compliance agent that waits for human approval before it answers.

> Demo build. No login or user permissions. Not for production use.

---

## 1. The problem

Maintenance manuals, directives and engineering orders are long, repetitive and sometimes **contradict each other** (for example 12,000 hours in one document and 11,500 in another). People lose time searching them, and a wrong answer is a safety risk.

## 2. The solution (in one line)

An AI pipeline that turns raw documents into **searchable chunks, a knowledge graph and reviewable rules**, then uses a **LangGraph agent** that answers with citations, reports conflicts, and requires **human approval** before the final answer.

```
Upload -> Parse -> Chunk -> Embed + Index -> Search / Graph / Rules -> Agent -> Human approval -> Cited answer
```

**Example:** *"Part 65-45210-3 has 11,800 flight hours. Is it due for replacement?"*
The agent finds 12,000 hours in the directive and procedure, finds 11,500 in an engineering order, reports both with their documents, warns about the conflict and waits for approval.

---

## 3. Tech stack

| Technology | Used for |
|---|---|
| Django 5 | Web pages, admin, management commands (one app per module) |
| Celery + Redis | Ingest stages, graph builds, rule extraction, agent runs |
| PostgreSQL + pgvector | Data, 384-dimension chunk vectors, agent checkpoints |
| Neo4j | Knowledge graph |
| LangGraph | Codegen repair loop and compliance agent |
| Gemini (`gemini-3.5-flash-lite`) | Extraction, answers, judges |
| BGE (`BAAI/bge-small-en-v1.5`) | Local embeddings |
| Pydantic | Validation of every LLM output |
| Docker Compose | web, worker, postgres, redis, neo4j |

---

## 4. The 8 modules and how they connect

```
Upload -> [1 Ingest] -> chunks + vectors
                         |-> [3 Search / RAG]
                         |-> [4 Graph]   (automatic after ingest)
                         |-> [5 Rules]   (button or command)
[2 Prompts] feeds every LLM step
[6 Q&A] tests [3]  ->  [7 Code] uses [5]  ->  [8 Agent] uses [3] + [4] + [5]
```

| # | Module | What it does | Input -> Output | Page |
|---|---|---|---|---|
| 1 | Ingestion | Seven stages: detect, parse, clean, chunk, enrich, embed, index. Same sha256 hash is skipped. A failed stage retries (up to 2). No LLM used. | File -> chunks (page, section, part numbers) + vectors | `/ingest/` |
| 2 | Prompt library | Versioned prompts (v1 simple, v2 engineered) with examples and schema-validated output. 22 prompt files. | Prompt files -> validated objects | `/prompts/` |
| 3 | Hybrid search | BM25 keyword + pgvector, merged with RRF, then re-ranking. Cites document, page and section. Says "I don't know" when unsure. | Question -> cited answer | `/rag/` |
| 4 | Knowledge graph | LLM extracts entities and relations. A relation is dropped if its evidence quote is not in the chunk. Duplicates merged. Stored in Neo4j. 1-2 hop GraphRAG. | Chunks -> nodes and edges linked to source chunks | `/kg/` |
| 5 | Business rules | IF / THEN rules, kept only if the source quote appears verbatim. Near-duplicates merged. LLM judge flags conflicts. Approve, edit, reject, export. | Chunks -> reviewed rules | `/rules/` |
| 6 | Q&A generation | Creates factual, multi-hop and unanswerable questions, checked by quote and groundedness judge. Used to test search. | Chunks -> test questions + scores | `/qa/` |
| 7 | Rules to code | Rules become Python and tests (tests come from the rule text, never the code). Repair loop of at most 3 attempts. Conflicts return `conflict`. | Rules -> tested Python | `/codegen/` |
| 8 | Compliance agent | classify, plan, search, graph lookup, rule check, draft, verify citations (loop at most 2), human approval, final. State saved in Postgres. | Question -> cited, approved answer | `/agents/` |

### What happens when a file is uploaded

1. A `Document` row is created and the sha256 check skips duplicates.
2. Celery runs the seven ingest stages. Each stage records attempts and duration.
3. Chunks go to Postgres and vectors to pgvector.
4. At the end of `index`, `_after_ingest()` starts graph extraction automatically (`KG_AUTO_BUILD` is true). A graph failure never fails the ingest.
5. Rules, Q&A, codegen and the agent run on demand.

---

## 5. Key design decisions (good interview points)

- **Every LLM output is a Pydantic object.** A validation failure is retried with the error message.
- **Provenance everywhere.** Chunks, graph edges, rules and claims carry document, page and a verbatim quote, and the quote is checked in code, not by the model.
- **Hybrid search.** BM25 wins on exact part numbers and directive IDs, vectors win on meaning.
- **Decoupled modules.** A graph failure never breaks ingest, and each module can be re-run alone.
- **Human in the loop.** It is a safety domain, so the system recommends and a person decides.
- **Conflicts are reported, not resolved.** The agent and the generated code return both values and their sources.
- **Cached extractions** per chunk and prompt version, so reruns do not resend unchanged chunks.
- **Evaluation per module.** Each module has a command that stores an `EvalRun` and a report.

---

## 6. Results

Every number comes from real runs (Gemini `gemini-3.5-flash-lite`, local `BAAI/bge-small-en-v1.5`). Full tables and caveats are in `evals/REPORT.md`.

| Module | Finding |
|---|---|
| Prompts | Engineered v2 scored 1.0 vs 0.8 for v1. The simple prompt invented rules where the text had none. |
| Search | Keyword search and hybrid + re-ranking beat vector-only on exact part numbers and directive references. The 15-question hit@5 eval does not separate BM25 from hybrid. |
| Q&A evaluation | 22 generated questions: bm25 0.955, hybrid + re-rank 0.909, vector 0.864. |
| Rules | Last run: 20 rules extracted, 3 merged, 4 conflicts flagged. |
| Rules to code | All tests passed in 5 of 5 cases. The conflicting pair returned `conflict`. |

---

## 7. How to run

Requires Docker Desktop and a Google AI Studio API key.

```powershell
copy .env.example .env        # set GOOGLE_API_KEY in .env (never commit it)
.\make.ps1 up                 # start Postgres, Redis, Neo4j, web, worker
.\make.ps1 demo-reset         # ingest the corpus, extract rules, build the graph
```

Open http://localhost:8010/ (Neo4j browser: http://localhost:7474).

```powershell
.\make.ps1 test               # offline tests (mock model, no key needed)
.\make.ps1 eval               # run the evaluations
docker compose exec web python manage.py generate_qa
docker compose exec web python manage.py eval_qa
docker compose exec web python manage.py eval_codegen
```

**Demo order:** landing page -> `/ingest/` -> `/results/` -> `/rag/` (show the "I don't know" case) -> `/kg/` -> `/rules/` -> `/qa/` -> `/codegen/` -> `/agents/` (show the conflict and the approval).

---

## 8. Limitations (say these before they ask)

- Small test sets (8-22 items), so one question moves a score a lot.
- One model writes, judges and grades the Q&A, so it may share its own blind spots.
- Passing generated tests shows the code matches the model's reading of a rule, not that the rule is correct.
- The code sandbox is a demo sandbox, not a security boundary.
- Results vary between runs.
- Text documents only. Video and scanned images are not supported.
- No authentication. Debug mode and the default secret key are for local use only.

**Improvements:** larger evaluation sets, a separate judge model, authentication, scanned-document OCR, a real sandbox, and scaling with more Celery workers.

---

## 9. Beyond aviation

The same pipeline (ingest, rules, knowledge graph, agent) fits insurance and finance policy and claims documents. This is a design idea. This repo demonstrates aviation only.

---

## 10. Quick revision

1. Ingest: 7 stages, sha256 dedupe, 2 retries, no LLM.
2. Prompts: versioned, v2 1.0 vs v1 0.8, Pydantic validated.
3. Search: BM25 + vectors + RRF + rerank, cites sources, abstains.
4. Graph: auto after ingest, evidence quote checked, Neo4j, 1-2 hop GraphRAG.
5. Rules: verbatim quote, merge, conflict judge, human review.
6. Q&A: factual, multi-hop and unanswerable questions test the search.
7. Codegen: rules to Python, tests never see code, max 3 attempts.
8. Agent: LangGraph, verify loop of 2, human approval, Postgres checkpoint.

**Numbers:** 8 modules, 7 ingest stages, 22 prompt files, 384-dim vectors, 3 codegen attempts, 2 agent loops, 22 Q&A questions, 5/5 codegen cases.

License: MIT