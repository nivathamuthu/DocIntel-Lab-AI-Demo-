# Demo recording order

Record after `.\make.ps1 demo-reset`. That command calls Gemini, so leave several minutes. It wipes documents, rules, the graph and agent runs, then ingests the four corpus files, extracts rules and rebuilds the graph. It keeps every `EvalRun`, so http://localhost:8010/results/ stays on the real tables below.

Read the counts the command prints. A new extraction can change rule keys and the live node count. The eval numbers do not move unless you run an eval again. Do not run `eval`, `eval-agent` or `eval-rules` before recording if you want these tables.

Provider on every real run: **google / gemini-3.5-flash-lite**. Embeddings: **local / BAAI/bge-small-en-v1.5**.

The live graph is build 11 (**31 nodes, 29 edges**, 12 merged, 1 dropped, 28 chunks). It includes the uploaded `Aviation_Document_Intelligence_Sample` (12 chunks) next to the four corpus files (16 chunks). A reset removes that upload, so after a reset the graph is rebuilt from 16 chunks and its counts will differ. Live rules were last **20 extracted, 0 dropped, 3 merged, 4 conflicts**.

## 1. Landing

- URL: http://localhost:8010/
- Click nothing.
- Show: the provider line, then the module table with all eight modules linked.

## 2. Ingest

- URL: http://localhost:8010/ingest/
- Click the `airworthiness_directive_excerpt` row.
- Show: the chunk column. The four corpus files are 4 + 4 + 6 + 2 = **16 chunks**; the uploaded sample adds 12 until a reset. On the document page, show one stage time in milliseconds and a chunk tagged `AD 2023-14-09`.

## 3. Prompts

- URL: http://localhost:8010/results/ (section **2 · Prompts**). The same scores are on http://localhost:8010/prompts/.
- Click nothing. If you use `/prompts/`, scroll to samples **r4** and **r5**.
- Show: **v1 mean 0.8, v2 mean 1.0** (EvalRun 9, 2026-11-09 15:36 UTC). r4 and r5 are 0.0 on v1 and 1.0 on v2.

## 4. Retrieval

- URL: http://localhost:8010/rag/
- Question, mode Hybrid, then the same question on BM25: `What does AD 2023-14-09 require?`
- Then, any mode: `What torque in newton-metres is specified for the pin?`
- Show: the abstain ("I don't know"), then on http://localhost:8010/results/ section **3 · Retrieval** the hit@5 table.
- Number: **hit@5 = 1.0** for vector, bm25, hybrid and hybrid_rerank (EvalRun 18, 2026-11-11 07:04 UTC, 15 questions).
- Caveat, say it: this eval does not separate BM25 from hybrid. A rank check on the same 15 questions (2026-11-09, earlier corpus), not stored as an EvalRun, put BM25 and hybrid_rerank together at **MRR 0.967**. Hybrid does not win here.

## 5. Graph

- URL: http://localhost:8010/kg/
- Question: `Which intervals apply to the tasks for part 65-45210-3?`
- Show: the node and edge counts at the top, then the GraphRAG answer next to the hybrid answer. On the saved eval this question was a GraphRAG win (1.0 vs 0.0, hybrid abstained).
- Number, on http://localhost:8010/results/ section **4 · GraphRAG**: **GraphRAG 0.562 vs hybrid 0.375** (EvalRun 17, 2026-11-11 07:04 UTC, graph build 11).
- Caveat, say it: **8 questions**. The score is how many expected strings appear in the answer, and GraphRAG can name extra entities. The previous build scored 0.688; the rebuild with the uploaded sample document lost k1, k4 and k8 and gained k7. If you reset before recording, the graph on screen is no longer build 11.

## 6. Rules

- URL: http://localhost:8010/rules/
- Click the conflict whose two sides are "replace before 12,000 flight hours" and "do the replacement at the next C-check above 11,000". On the current data that pair is **R-0005 vs R-0006**. After a reset the keys can change; the label follows the text.
- Show: that row reads **possible conflict**, severity **medium**. The 11,500 vs 12,000 rows stay **high** (current keys R-0015 vs R-0020, and R-0005 vs R-0020).
- Number, on http://localhost:8010/results/ section **5 · Rules**: **v2 precision 0.471, recall 0.667**. v1 precision 0.167, recall 0.5 (EvalRun 19, 2026-11-11 07:07 UTC, 12 gold rules).
- Caveat, say it: **precision is a lower bound**. The gold file is not exhaustive, so a correct rule it leaves out counts as a miss. The previous run on the same chunks and prompts had recall 0.75 for both, so a single run is not a stable measure.

## 7. Agent

- URL: http://localhost:8010/agents/
- Click the first example, or paste: `Part 65-45210-3 is at 11,800 flight hours on a narrow-body aircraft. Is it due for replacement now?`
- Click **Ask the agent**. Wait until the page says **Pending approval**. The URL will be `/agents/run/<id>/`.
- Show: the trace (classify, plan, hybrid_retrieve, graph_lookup, rule_check, draft, verify_citations), then the conflict box. The 11,500 vs 12,000 pair is high. If the C-check deferral pair is listed, it is labeled **possible conflict · medium**.
- Note: `Follow EO-2291 until engineering resolves the conflict`. Click **Approve**.
- Show: the citation table, each row verified.
- A verification loop is not guaranteed. The earlier MEL question (`Can the side-stay replacement on part 65-45210-3 be deferred under the MEL?`) did loop once, because a 50-hour maintenance deferral was called an MEL deferral. If this recording does not loop, say so. Do not force one.
- Number, on http://localhost:8010/results/ section **8 · Agent**: **intent 6/6, answer 6/6, citations verified 5/5, conflict check 2/2, loops 2** (EvalRun 14, 2026-11-09 17:01 UTC).
- Caveats, say them:
  - The run before it (EvalRun 13) missed the conflict case. EvalRun 14 is the one on the page, after `relevant` was added to the rule check.
  - `compliance-mel` used both loops because replacement-limit rules were marked relevant to an MEL question, not because a citation failed.
  - Answer correct is a required-string check. The eval approves every paused run. Six cases. The model ignores temperature, so a live answer can differ from the saved one.

## 8. Close on the results page

- URL: http://localhost:8010/results/
- Scroll the seven sections. Modules 6 and 7 have their own clips in `docs/demo_scripts/m6.md` and `m7.md`. Each header is google / gemini-3.5-flash-lite with the date above.
- One screen to hold: the agent totals line, then the retrieval line that hit@5 does not separate BM25 from hybrid.
