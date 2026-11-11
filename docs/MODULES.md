# Running each module

All commands run from the repo root in PowerShell. `.\make.ps1` is the task runner. It creates `.env` from `.env.example` on the first `up`. Set a real provider in `.env` first (see the Quick start in the [README](../README.md)).

```powershell
.\make.ps1 up          # build, start web + worker + postgres + redis + neo4j, migrate
.\make.ps1 test        # offline pytest inside the web container (mock LLM and embeddings)
.\make.ps1 demo        # synthetic PDF, DOCX and PPTX, then ingest them
.\make.ps1 demo-reset  # wipe documents, rules, graph and agent runs, then rebuild the 4 corpus documents with the configured providers (eval runs are kept)
```

`demo-reset` refuses to run with a mock provider, because its output would not be real. The worker uses Celery's prefork pool. Code is bind-mounted: edit a file and the dev server reloads. Restart the worker (`docker compose restart worker`) after changing task code. Rebuild the image only when `requirements/` changes.

The same steps without the script:

```powershell
docker compose up -d --build
docker compose exec web python manage.py migrate
docker compose exec web pytest
docker compose exec web python scripts/seed_demo.py --reset
```

Check the configured LLM with `docker compose exec web python manage.py check_llm`. A real provider with a missing key or an unknown model stops web, worker and every command at startup. It never falls back to the mock.

---

## Module 1: Ingest

Upload a file and the worker runs seven tasks: detect, parse, clean, chunk, enrich, embed, index. The document page shows the status, attempts and per-stage milliseconds. A file with the same sha256 is not stored twice. Chunks keep their page, section path and the identifiers found in the text (an AD number, a part number). Vectors are 384-dimensional and stored in pgvector.

```powershell
.\make.ps1 up
.\make.ps1 test
.\make.ps1 demo
```

To fetch the optional public FAA circulars and then run a second ingest that picks them up:

```powershell
docker compose exec web python scripts/fetch_corpus.py
.\make.ps1 ingest
```

To show a retry on screen:

1. Set `INGEST_CHAOS_STAGE=parse` in `.env`.
2. Run `docker compose up -d web worker`.
3. Click Reprocess. The parse row ends on attempt 2.
4. Set the variable back to empty afterwards.

The talk track is `docs/demo_scripts/m1.md`. The stage diagram is in [pipeline.md](pipeline.md).

## Module 2: Prompts

`prompts/<task>/v1.md` is a one-line prompt. `v2.md` sets the role, the rules, the JSON schema and the few-shot examples from `examples.yaml`. It must answer "unknown" when the chunk does not contain the answer.

The two tasks are:

- `answer_with_citations`;
- `extract_single_rule`, which extracts one rule from one chunk. Module 5 uses `extract_rules`, which returns every rule in a chunk.

Outputs are Pydantic objects. A validation failure is sent back to the model and retried. Every call is a row in `LLMCall`.

```powershell
docker compose exec web python manage.py eval_prompts
```

The score table is printed, saved under `evals/results/`, and stored on `EvalRun` with the LLM provider and model. Open http://localhost:8010/prompts/ for the side-by-side comparison. The talk track is `docs/demo_scripts/m2.md`.

## Module 3: Hybrid retrieval

Ask a question at http://localhost:8010/rag/. The mode toggle offers:

- vector;
- BM25;
- hybrid (reciprocal rank fusion);
- hybrid+rerank.

Answers cite the document, page and section. Weak context returns "I don't know."

`RERANK_ENABLED` defaults to false, so hybrid+rerank uses a lexical rerank. The cross-encoder and local embeddings need the ML extras in the image:

```powershell
docker compose build --build-arg INSTALL_ML=1
```

Setting `INSTALL_ML=1` in `.env` does the same for `.\make.ps1 up`.

OpenAI embeddings stay 384-dimensional (`text-embedding-3-small`), so the pgvector column does not change:

```powershell
# in .env
EMBEDDINGS_PROVIDER=openai
OPENAI_API_KEY=<your key>
docker compose up -d web worker
docker compose exec web python manage.py reembed
```

Chunks already in the database keep the vectors from whichever provider was active at ingest. Run `reembed` after switching provider, because vectors from different providers are not in the same space.

```powershell
.\make.ps1 eval
```

That writes hit@5 for all four modes. The report names the LLM and the embedding provider and model. The talk track is `docs/demo_scripts/m3.md`.

## Module 4: Knowledge graph and GraphRAG

Each chunk goes through `prompts/extract_kg/v2.md`; v1 is the naive version. The output is validated against `KGOutput` and retried on failure:

- entity types: Aircraft, Component, Task, Regulation, Procedure, Part, Interval;
- relations: APPLIES_TO, REQUIRES, GOVERNED_BY, REPLACES, HAS_INTERVAL, PART_OF.

A relation is dropped, and counted, when an endpoint is missing from the chunk's entity list or its evidence quote is not in the chunk. Results are cached in `KGExtraction`, one row per chunk and prompt version.

Entity resolution unifies case, punctuation, hyphens, part-number prefixes (`PN-77-4418` = `P/N 77-4418`) and flight-hour spellings. It then merges names that share a key or an alias. An alias is accepted only when its normalized identifier tokens (the tokens with digits) equal the name's. So `77-4418` and `P/N 77-4418` merge, but a serial range (`S1000 through S1899`) or another part number (`PN-77-4410`) never merges into a part. Rejected aliases are listed in the merge log, which is stored on `GraphBuild` and printed by `build_graph`. Every node and edge carries its source chunk id, document and page.

The store is one of:

- Neo4j (`GRAPH_BACKEND=neo4j`), the compose service, browsable at http://localhost:7474;
- a NetworkX JSON file (`GRAPH_BACKEND=networkx`).

When an ingest run finishes, the worker extracts the new chunks and rebuilds the graph (`KG_AUTO_BUILD=true`).

```powershell
docker compose exec web python manage.py extract_kg            # cached chunks are skipped; --force re-extracts
docker compose exec web python manage.py build_graph           # rebuild from KGExtraction, no LLM calls
docker compose exec web python manage.py build_graph --extract --force --prompt-version v1
docker compose exec web python manage.py eval_kg
```

http://localhost:8010/kg/ shows:

- the interactive graph (pyvis) and a type filter;
- the node, edge, merged and dropped counts;
- a node panel with source chunks and citations.

The GraphRAG box works in five steps:

1. It links question entities by name, alias and embedding similarity.
2. It walks one or two hops.
3. It adds the linked chunks to the Module 3 hybrid results.
4. It answers with citations.
5. It shows the traversed path under the answer, next to the hybrid-only answer.

`eval_kg` runs the 8 multi-hop questions in `evals/datasets/kg_questions.yaml` through plain hybrid RAG and through GraphRAG. It saves the table to `EvalRun` and `evals/results/`, and names the LLM, the embeddings and the graph build. The talk track is `docs/demo_scripts/m4.md`.

## Module 5: Business rules

Every chunk of every finished document goes through `prompts/extract_rules/v2.md`; v1 is the naive one-line version. The output is a `RuleSet` of `{condition, action, exception, source_quote, confidence}`, validated by Pydantic and retried on failure.

A rule is kept only when its `source_quote` appears verbatim in the chunk. Whitespace and typographic quotes are folded; wording and case are not. Dropped rules are logged and counted. The validated output is cached in `RuleExtraction` per chunk and prompt version. An unchanged chunk is sent to the model again only when you pass `--force`.

Rules get stable keys (`R-0001`, ...):

- A re-extraction that finds the same normalized condition and action, or the same quote, keeps the key and the review state.
- A pending rule that disappears is retired, not deleted.
- Each rule keeps its document, chunk, page, section, prompt version, model and raw model output.

After extraction, near-duplicates are merged into the highest-confidence rule. A merge needs either the same normalized condition and action, or an embedding cosine of at least `RULES_MERGE_SIMILARITY` (0.9) plus word overlap. Rules with different numbers are never merged. The merge log is stored on `RuleRun`.

Conflict candidates are pairs that share a part or directive number, or whose conditions have a cosine of at least `RULES_CONFLICT_SIMILARITY` (0.6). Only candidates go to the judge (`prompts/judge_rule_conflict/v1.md`, schema `ConflictJudgment`). Each verdict is stored as a `RuleConflict` with kind, severity and reason. An unchanged pair is not judged again.

The synthetic corpus includes `engineering_order_EO-2291.docx`. It is a one-paragraph order that gives 11500 flight hours for part 65-45210-3, where the procedure says 12000. It exists so the conflict check has a real contradiction to find.

```powershell
.\make.ps1 extract-rules        # = docker compose exec web python manage.py extract_rules [--force] [--prompt-version v1] [--document 11]
.\make.ps1 eval-rules           # = docker compose exec web python manage.py eval_rules [--force]
```

http://localhost:8010/rules/ shows:

- the counts: extracted, dropped because the quote was not found, merged, conflicts, approved;
- the LLM and embedding provider and model;
- the conflicts;
- the rules table, with status, document and confidence filters;
- the merge log.

"Extract rules" queues the same pipeline on the worker. A rule's page highlights the quote inside the full chunk text and has Approve, Reject (with a reviewer note) and Edit. An edit keeps the original wording. `/rules/export.json` and `/rules/export.csv` export approved rules with their source and model.

**Conflict labels.** The judge's stored verdict is never edited. Two display rules apply:

- A pair where one rule defers a job to the next C-check and the other is a hard replacement limit is labeled **possible conflict**, severity **medium**. On the current corpus this is R-0005 vs R-0006.
- Two different hard limits, such as 11500 vs 12000, stay **conflict**, severity **high**.

The logic is in `apps/rules/services/present.py`.

`eval_rules` scores v1 and v2 against the 12 hand-written rules in `evals/datasets/rules_gold.yaml`:

- precision = matched / kept;
- recall = matched / 12.

The report names the LLM and lists every unmatched prediction, because the gold set is not exhaustive. The talk track is `docs/demo_scripts/m5.md`.

## Module 6: Q&A generation

Each finished chunk goes through `prompts/generate_qa/v2.md` for two factual questions and one unanswerable question. Up to `QA_MAX_PAIRS` (8) chunk pairs from different documents that the Module 4 graph links through the same identifier (a part, directive or interval) get one multi-hop question each. v1 is the naive prompt; v2 asks only what the text answers, answers only from the text and copies the supporting sentence. The output is a `QASet`, validated by Pydantic and retried on failure.

- **Quote check (code).** Every supporting quote must be verbatim in a source chunk; a multi-hop pair needs one quote from each chunk. Failures are dropped and counted as "dropped (quote not found)".
- **Groundedness judge (LLM).** `prompts/judge_groundedness/v2.md` scores each remaining pair 0 to 1 with a reason. Pairs below `QA_MIN_GROUNDEDNESS` (0.8) are dropped and counted.
- **Cache.** `QAGeneration` per chunk (or chunk pair), prompt version, provider and model. An unchanged chunk is not sent again unless you pass `--force`.
- **Review.** Kept pairs start pending. Approve or reject them in the detail panel; only approved pairs are exported.

The evaluation asks Search (Module 3) every sampled kept pair, approved ones first, up to `QA_EVAL_MAX_PER_TYPE` (8) per type, in `vector`, `bm25` and `hybrid_rerank`. An unanswerable question is correct only when Search says it does not know. Other answers are graded against the reference by `prompts/judge_answer/v1.md`. The report gives accuracy per question type and mode, and states that the same model generates and judges.

```powershell
docker compose exec web python manage.py generate_qa [--force] [--prompt-version v1]
docker compose exec web python manage.py eval_qa [--per-type 8]
```

http://localhost:8010/qa/ shows the counts, a Generate button with progress ("Reading chunk 12 of 16"), the pairs table with type and status filters, a detail panel with the source chunk and the quote highlighted, the evaluation table with a one-line takeaway, and `/qa/export.csv` and `/qa/export.json`. The talk track is `docs/demo_scripts/m6.md`.

## Module 7: Rules to code

Input: approved rules. When none are approved, pending rules are offered and every run is labelled "unreviewed". Rules in a `RuleConflict` with a selected rule are added to the run automatically.

`apps/codegen/graph.py` is a LangGraph loop:

```
generate_code -> generate_tests -> run_tests -> (passed or 3 attempts) -> END
                                       \-> triage -> (rule ambiguous) -> END, "Needs review"
                                                  -> (only tests at fault) -> run_tests
                                                  -> (code at fault) -> repair -> (rule number changed) -> END, "Needs review"
                                                                               -> run_tests
```

- `generate_code` (`prompts/codegen_code/v1.md`) returns a `CodeOutput`: one plain function, its typed parameters and the module source. Pydantic parses the code and checks the function and its parameters exist. Comments carry the rule key and source quote.
- `generate_tests` (`prompts/codegen_tests/v1.md`) sees the rule text, the conflicts and the signature, never the code. It returns structured cases (`CheckPlan`); code renders them into a pytest file with the rule key and quote as comments.
- Every function returns the fixed `Decision(outcome, rule_keys, reason)` from `apps/codegen/contract.py`. When conflicting rules both apply the outcome is `conflict`, listing both keys.
- `triage` (`prompts/codegen_triage/v1.md`) runs a separate call for each failing test (up to 4). It compares the test and the code with the rule's condition, action and exception and returns a `FailureVerdict`: `code`, `test` or `rule_ambiguous`, with a reason. A test at fault is dropped and the code is kept. An ambiguous rule stops the run and marks it "Needs review" with the reason.
- `repair` (`prompts/codegen_repair/v2.md`) gets the code, the pytest output and the verdicts for the code faults; it does not edit the tests. A repair that removes or replaces a number from the rule text, or compares against a new number that is not in the rule text, is rejected: the code is kept and the run is marked "Needs review".
- Every attempt (code, tests, pytest output, counts) is stored on `CodegenAttempt`.

**Sandbox (demo, not a security boundary).** A static check refuses imports outside a small allowlist (os, subprocess, socket, requests, shutil and the like are blocked) and names such as `eval`, `open` and `__import__`. Then pytest runs with `python -I` in a temporary folder, with an environment of only PATH, HOME and LANG (no secrets), sockets replaced by a function that raises, resource limits on Linux and a `CODEGEN_TEST_TIMEOUT_SECONDS` (30) timeout.

After a passing run, a mutation check raises the largest rule threshold found in the code by one and reruns the tests; at least one should fail.

```powershell
docker compose exec web python manage.py eval_codegen
```

`eval_codegen` runs the cases in `evals/datasets/codegen_cases.yaml` (five cases covering six rules: an exception, a numeric threshold, a conflicting pair of two rules, and two cases without numbers) and reports tests passed, attempts, conflict surfaced and the mutation check. http://localhost:8010/codegen/ has the rule checklist with search, the Generate button, an attempt timeline with the pytest output (failing lines highlighted), the final code and tests side by side with Copy and download, and the conflict warning. The talk track is `docs/demo_scripts/m7.md`.

## Module 8: Compliance agent (LangGraph)

`apps/agents/graph.py` is a LangGraph `StateGraph`:

```
classify -> plan -> hybrid_retrieve -> graph_lookup -> rule_check -> draft -> verify_citations -> human_approval -> final
classify --out_of_scope--> refuse
verify_citations --failed, loops < AGENT_MAX_LOOPS (2)--> hybrid_retrieve (with a refined query)
```

Every LLM node goes through `core.llm.structured_call`, with the same provider, backoff and concurrency limit as the other modules. Each uses a Pydantic schema from `apps/agents/schemas.py` and a prompt under `prompts/agent_*/v1.md`.

- `classify` returns `{intent: lookup | compliance_check | conflict_check | out_of_scope, entities}`. Out of scope goes to a fixed, polite refusal with no further calls.
- `plan` returns the tools to run. The schema rejects a plan without `hybrid_search` or with a tool listed twice. Compliance and conflict questions always get `rule_lookup` added, and the step output says so when that happens.
- The tools in `apps/agents/tools.py` are plain functions with typed keyword arguments:
  - `hybrid_search(query, k, mode)` uses Module 3. `AGENT_RETRIEVAL_MODE` defaults to `hybrid_rerank`.
  - `kg_query(question, hops, limit)` uses Module 4 entity linking and 2-hop traversal.
  - `rule_lookup(question, entities, limit)` returns Module 5 approved and pending rules with their conflict flags. A conflicting partner rule is always included.
- `rule_check` asks the model, for each candidate rule, two questions: is its condition met (`applies`), and does its outcome bear on the answer (`relevant`). Conflicts of applicable or relevant rules are carried to the draft and the reviewer.
- `draft` answers only from the numbered sources (search, graph and rule chunks). Each claim has a source number and a verbatim quote. `cannot_determine` is a valid status.
- `verify_citations` runs these checks:
  1. Without an LLM, it checks that every cited source is in the retrieved set and that its quote is in that chunk.
  2. A judge (`Verification`) decides whether each quote supports its claim.
  3. If an applicable rule has a conflict, the answer must say so.

  A failure loops back to retrieval with the judge's refined query, at most twice. After that, the draft goes to approval with a warning.
- `human_approval` calls `interrupt()`. The state is saved by `PostgresSaver` (`langgraph-checkpoint-postgres`) in the app database. The thread id is `AgentRun.thread_id`. Approve, or Reject with a required note, resumes the thread on the worker.

`AgentRun` and `AgentStep` keep, for each node:

- input and output;
- tool calls (arguments, result summary, ms);
- LLM calls (schema, attempts, latency, tokens);
- duration and loop number.

http://localhost:8010/agents/ has the question box, the provider and model, and recent runs. A run page shows:

- the trace while the worker runs (HTMX, refreshed every 1.5 s);
- the loop count;
- the pending-approval panel, with the conflict warning and the citation check;
- the final answer, with a citation table (claim, quote, document, page, section, verified).

```powershell
.\make.ps1 ask-agent "Part 65-45210-3 is at 11,800 flight hours on a narrow-body aircraft. Is it due for replacement now?"
.\make.ps1 eval-agent     # = docker compose exec web python manage.py eval_agent [--case conflict-limit]
```

`eval_agent` runs the 6 cases in `evals/datasets/agent_cases.yaml`: 2 lookups, 2 compliance checks, the 11500 vs 12000 conflict and 1 out-of-scope question. It approves every paused run itself. For each case it reports:

- whether the intent is correct;
- whether the answer is correct (required strings);
- whether the citations are verified;
- whether a conflict was surfaced;
- how many loops were used.

The report names the LLM and is saved to `EvalRun` and `evals/results/`. The talk track is `docs/demo_scripts/m8.md`.
