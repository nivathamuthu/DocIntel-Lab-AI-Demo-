# Evaluation report

All numbers below come from real runs. No mock-provider result is included.

- LLM: `google` / `gemini-3.5-flash-lite`
- Embeddings: `local` / `BAAI/bge-small-en-v1.5` (384 dimensions)
- Graph store: Neo4j
- Run dates (UTC): 2026-11-09 for Modules 2 and 8; 2026-11-11 for Modules 3 to 7. Each section gives the time and the stored `EvalRun` id.
- Corpus on 2026-11-11: five documents, 28 chunks. Document 13, "Aviation_Document_Intelligence_Sample" (12 chunks), was uploaded at 06:42 UTC, after the Q&A generation and before the Module 3, 4, 5 and 7 reruns.

The raw reports are written to `evals/results/` by each eval command. They are not tracked in git. The latest result for each module is also shown at http://localhost:8010/results/.

## Freshness

Some of these runs predate later code or corpus changes:

| Eval | Current with the code? | Rerun |
|---|---|---|
| Prompts | Yes. The samples carry their own chunks. Since the run, the task `extract_rule` was renamed `extract_single_rule`; the prompt text is unchanged. | `docker compose exec web python manage.py eval_prompts` |
| Retrieval | Yes. Rerun 2026-11-11 07:04 on the current five documents. The hit@1 and MRR check below was not rerun. | `.\make.ps1 eval` |
| GraphRAG | Yes. Rerun 2026-11-11 07:04 on graph build 11, rebuilt from all 28 chunks with `build_graph --extract --force`. | `docker compose exec web python manage.py build_graph --extract --force`, then `docker compose exec web python manage.py eval_kg` |
| Rules | Yes. Rerun 2026-11-11 07:07 with `--force`, so every chunk of the three gold documents was extracted again. | `docker compose exec web python manage.py eval_rules --force` |
| Q&A | Partly. Generation and evaluation (06:09 and 06:18) predate the upload of document 13 at 06:42, so its 12 chunks have no pairs. | `docker compose exec web python manage.py generate_qa`, then `docker compose exec web python manage.py eval_qa` |
| Codegen | Yes. Rerun 2026-11-11 07:09 with the new triage step. The rules come from the gold documents, which did not change. | `docker compose exec web python manage.py eval_codegen` |
| Agent | Code yes, corpus no. It predates the upload of document 13. | `.\make.ps1 eval-agent` |

No questions, gold files or prompt texts were changed for these reruns. Two prompts were added for the codegen repair fix: `codegen_triage` v1 (judges whether the code, the test or the rule is at fault) and `codegen_repair` v2 (adds the triage verdicts and the line "Never change a number that appears in the rule text"). `codegen_repair` v1 is kept unchanged.

---

## Module 2: Prompts, v1 vs v2

`EvalRun` 9, 2026-11-09 15:36 UTC. Embeddings are not used by this eval.

| sample | task | v1 | v2 |
| --- | --- | --- | --- |
| a1 | answer_with_citations | 1.0 | 1.0 |
| a2 | answer_with_citations | 1.0 | 1.0 |
| a3 | answer_with_citations | 1.0 | 1.0 |
| a4 | answer_with_citations | 1.0 | 1.0 |
| a5 | answer_with_citations | 1.0 | 1.0 |
| r1 | extract_single_rule | 1.0 | 1.0 |
| r2 | extract_single_rule | 1.0 | 1.0 |
| r3 | extract_single_rule | 1.0 | 1.0 |
| r4 | extract_single_rule | 0.0 | 1.0 |
| r5 | extract_single_rule | 0.0 | 1.0 |
| **mean** | | **0.8** | **1.0** |

Caveats:

- There are 10 samples. Answering is saturated: both versions abstain correctly on the two unanswerable questions.
- The whole gap comes from r4 and r5, where v1 invents a rule for a chunk that states none.
- The stored run and the raw report label the task `extract_rule`, its name at the time.

## Module 3: Hybrid retrieval

`EvalRun` 18, 2026-11-11 07:04 UTC, on the current five documents. 15 questions. The result is unchanged from the previous run (`EvalRun` 10, 2026-11-09).

| mode | hit@5 |
| --- | --- |
| vector | 1.0 |
| bm25 | 1.0 |
| hybrid | 1.0 |
| hybrid_rerank | 1.0 |

hit@5 is saturated on this corpus, so I also ran a rank check over the same 15 questions on 2026-11-09. That check is ad hoc, is not stored as an `EvalRun`, and was not rerun on the current corpus:

| mode | hit@1 | MRR |
| --- | --- | --- |
| bm25 | 14/15 | 0.967 |
| hybrid_rerank | 14/15 | 0.967 |
| hybrid | 11/15 | |
| vector | 11/15 | |

Caveats:

- At hit@1, plain hybrid (reciprocal rank fusion) does not beat BM25. Many questions carry exact identifiers (part and directive numbers), where BM25 is strong.
- Only hybrid with rerank ties BM25. The agent uses `hybrid_rerank` for that reason.

## Module 4: Knowledge graph and GraphRAG

`EvalRun` 17, 2026-11-11 07:04 UTC. The graph is Neo4j build 11, rebuilt from all 28 chunks with `build_graph --extract --force`: extraction prompt v2, 31 nodes, 29 edges, 12 merged, 1 dropped relation. Each score is the share of expected strings found in the answer. The previous run (`EvalRun` 11, build 8, 27 nodes) is shown for comparison.

| question | hybrid | graphrag | graphrag, previous run |
| --- | --- | --- | --- |
| k1 | 0.0 | 0.0 | 0.5 |
| k2 | 0.0 | 1.0 | 1.0 |
| k3 | 1.0 | 1.0 | 1.0 |
| k4 | 0.0 | 0.0 | 0.5 |
| k5 | 0.0 | 0.0 | 0.0 |
| k6 | 1.0 | 1.0 | 1.0 |
| k7 | 0.5 | 1.0 | 0.5 |
| k8 | 0.5 | 0.5 | 1.0 |
| **mean** | **0.375** | **0.562** | **0.688** |

GraphRAG is better on 2 questions, worse on 0 and tied on 6. Plain hybrid is unchanged at 0.375.

Caveats:

- **GraphRAG fell from 0.688 to 0.562.** It lost k1, k4 and k8 and gained k7. On k1 and k4 it now answers "I don't know". The rebuilt graph includes document 13, which adds look-alike facts such as "12,000 simulated operating hours" and a training document EO-2291; the k2 answer lists that interval next to the real ones. The questions and the graph code did not change.
- The sample is small: 8 questions.
- The score is recall only, by string match. A GraphRAG answer can also name extra, loosely linked entities without penalty.
- The fact ranking was tuned while the questions were being written.

## Module 5: Business rules

`EvalRun` 19, 2026-11-11 07:07 UTC, with `--force`, so every chunk was extracted again rather than read from the cache. Prompt `extract_rules` v1 vs v2, scored against 12 hand-written gold rules from three documents. The eval reads only those three documents, so document 13 does not affect it.

- precision = matched / kept rules;
- recall = matched / gold rules;
- one prediction matches at most one gold rule.

| prompt | raw rules | dropped (quote not found) | kept (deduplicated) | matched gold | precision | recall |
| --- | --- | --- | --- | --- | --- | --- |
| v1 | 37 | 1 | 36 | 6 | 0.167 | 0.5 |
| v2 | 17 | 0 | 17 | 8 | 0.471 | 0.667 |

The previous run (`EvalRun` 12, 2026-11-09) scored v1 at precision 0.273 and recall 0.75, and v2 at 0.474 and 0.75.

Caveats:

- **Recall dropped on the same input.** The chunks, the prompts and the gold file are unchanged, so the drop (v1 from 9 to 6 matches, v2 from 9 to 8) is run-to-run variation in the model, which ignores the temperature setting. A single run of this eval is not a stable measure.
- v1 misses g01 to g06. v2 misses g01, g06, g09 and g12.
- Precision is a lower bound. The gold set is not exhaustive, and several of v2's 9 unmatched rules are correct extractions of instructions the gold file leaves out. The raw report lists every unmatched rule.

## Module 6: Generated Q&A, and Search evaluated with it

Generation: 2026-11-11 06:09 UTC, prompts `generate_qa` v2 and `judge_groundedness` v2, keep at groundedness ≥ 0.8. The model read 16 chunks (two factual questions and one unanswerable question each) and 8 chunk pairs linked through the Module 4 graph (one multi-hop question each).

| question type | generated | kept | dropped (quote not found) | dropped (low groundedness) |
| --- | --- | --- | --- | --- |
| factual | 32 | 32 | 0 | 0 |
| multi_hop | 8 | 6 | 1 | 1 |
| unanswerable | 16 | 15 | 0 | 1 |
| **all** | **56** | **53** | **1** | **2** |

No pair has been approved by a person yet. The evaluation therefore uses kept pairs that nobody has reviewed.

Evaluation: `EvalRun` 15, 2026-11-11 06:18 UTC. The run asked Search 22 kept pairs (up to 8 per type) in three modes. For an answerable question, the model compared Search's answer with the reference. For an unanswerable question, the answer counted as correct only when Search said it did not know.

| question type | n | vector | bm25 | hybrid_rerank |
| --- | --- | --- | --- | --- |
| factual | 8 | 1.0 | 1.0 | 1.0 |
| multi_hop | 6 | 0.833 | 0.833 | 0.833 |
| unanswerable | 8 | 0.75 | 1.0 | 0.875 |
| **all** | 22 | **0.864** | **0.955** | **0.909** |

bm25 answered 96% correctly, the best of the modes tried. Multi-hop questions were the hardest (83% at best).

Caveats:

- **One model plays every role.** The same model (`gemini-3.5-flash-lite`) wrote the questions, judged their groundedness and graded the answers, so the judge may share the generator's blind spots.
- **The questions copy the source wording.** They are written from the chunks themselves, which favours exact-word retrieval and explains part of bm25's lead.
- **Unanswerable questions are where the modes differ.** vector answered 2 of 8 questions the documents do not answer, and hybrid_rerank answered 1 of 8.
- **The judge made at least one mistake.** One dropped unanswerable question asked for "the exact torque value" for the pin. The judge scored it 0.0 and called it answerable, but the source only says to use "the value in the component maintenance manual". That is a judge error, not a generator error.
- **Small sample.** One question moves a cell by 12 to 17 points.
- **Prompts changed for this eval.** The eval adds a `judge_answer` prompt (v1) that the spec did not list. It is needed to grade free-text answers.

## Module 7: Rules to code

`EvalRun` 20, 2026-11-11 07:09 UTC. Five cases (one run each) covering six rules: the conflicting_pair case uses two rules, and each of the other four cases uses one. At most 3 attempts per run, sandbox timeout 30 s. The rules are picked by text in `evals/datasets/codegen_cases.yaml`. All six are pending, so every run is labelled "unreviewed".

| case | rules | result | tests passed | attempts | conflict surfaced | mutation check |
| --- | --- | --- | --- | --- | --- | --- |
| exception | R-0009 | passed | 3/3 | 1 | n/a | not run (no threshold in the code) |
| numeric_threshold | R-0012 | passed | 3/3 | 1 | n/a | caught (11000 → 11001) |
| conflicting_pair | R-0015, R-0020 | passed | 6/6 | 1 | yes | caught (12000 → 12001) |
| mel_placard | R-0018 | passed | 3/3 | 1 | n/a | not run (no threshold in the code) |
| c_check_inspection | R-0016 | passed | 2/2 | 1 | n/a | not run (no threshold in the code) |

Totals: all tests passed in 5 of 5 runs, mean attempts 1.0, conflicts surfaced 1 of 1. The mutation check ran on 2 of 5 cases and caught both mutations.

**The repair step was changed after the previous run.** In `EvalRun` 16 (06:21 UTC), the conflicting_pair run needed 2 attempts: one generated test, `r0015_just_below_threshold`, expected "required" at 11999 flight hours although R-0015 says "at least 12000", and the repair step lowered the R-0015 branch to `>= 11500` to make that test pass. The run now works like this when a test fails:

1. A separate call (prompt `codegen_triage` v1) compares each failing test and the code with the rule's condition, action and exception, and returns a verdict: the code, the test, or the rule text is at fault.
2. A test at fault is dropped and the code is kept. The run records the dropped test and why.
3. If the rule text is ambiguous, the run stops and is marked "Needs review" with the reason. The code is not changed.
4. Only code faults go to repair (prompt `codegen_repair` v2). A repair that removes or replaces a number from the rule text, or compares against a new number that is not in the rule text, is rejected, the code is kept, and the run is marked "Needs review".

In this run, conflicting_pair passed on attempt 1, so the triage step did not run. Its final code compares against 12000 and 11500, both taken from the rule text. The unit tests cover the triage path: a forced bad test (expects "required" at 11999 for a 12000 threshold) is dropped without changing the code, and genuinely buggy code is still repaired within 3 attempts.

Caveats:

- **Passing self-written tests is not correctness.** It proves the code is consistent with the model's reading of the rule, not that the rule, or that reading, is correct.
- **The triage judge is the same model.** It sees the rule text, so it can catch a test that contradicts a stated threshold, but it can share the test writer's misreading.
- **The mutation check is narrow.** It raises one threshold by one. It only runs when a rule threshold appears as a number in the code, which is true for 2 of the 5 cases.
- **The sandbox is a demo sandbox, not a security boundary.** It uses a static import check, `python -I`, a temporary folder, no secrets in the environment, disabled sockets and a timeout.

## Module 8: Compliance agent

`EvalRun` 14, 2026-11-09 17:01 UTC. Retrieval mode `hybrid_rerank`, at most 2 loops.

Totals: intent 6/6, answer 6/6, citations verified 5/5, conflict check 2/2, loops used 2.

| case | intent correct | answer correct | citations verified | conflict surfaced | loops used | status |
| --- | --- | --- | --- | --- | --- | --- |
| lookup-part | yes (lookup) | yes | yes | no (not scored) | 0 | done |
| lookup-inspection | yes (lookup) | yes | yes | no (not scored) | 0 | done |
| compliance-mel | yes (compliance_check) | yes | yes | yes (not scored) | 2 | done |
| compliance-exception | yes (compliance_check) | yes | yes | yes (not scored) | 0 | done |
| conflict-limit | yes (compliance_check) | yes | yes | yes (expected yes) | 0 | done |
| out-of-scope | yes (out_of_scope) | yes | n/a | no (expected no) | 0 | refused |

Caveats:

- **The eval is lenient.**
  - Answer correctness is a required-substring check.
  - The command approves every paused run itself.
  - There are only six cases.
  - The same model drafts and judges the citations.
- **One prompt sentence was tuned after seeing a failure.**
  - The previous run (`EvalRun` 13, 16:57 UTC) failed the conflict-limit case (conflict check 1/2).
  - After that run, the `relevant` field was added to the rule check.
  - This sentence was added to the `agent_rule_check` prompt after seeing the compliance-mel case: "The question is itself a fact: when the rule's condition is the subject the question asks about ... the rule applies."
  - Run 14 is the first run after both changes.
- **The two compliance-mel loops came from the conflict-acknowledgement check.** The draft did not first mention the 11500 vs 12000 conflict. The final answer is correct, but the case is the weakest of the six.
- **Results vary between runs.** The model ignores the temperature setting.
