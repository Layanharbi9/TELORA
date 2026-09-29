# TELORA technical handoff

Repository snapshot inspected: 2026-09-22. This document describes the current code, not a proposed architecture. Paths are relative to the project root. No production code was changed to prepare it.

## 1. PROJECT PURPOSE

TELORA is a personalized telecom-plan MVP. It captures ordered customer needs, usage levels, and a preferred monthly budget; searches a synthetic telecom catalog; composes candidate package/add-on solutions; describes their fit; recommends one candidate; and reports deterministic guardrail results.

Input is a customer identity plus `Requirements`: one required primary need, optional secondary/third needs, and a positive SAR budget. The current in-memory Solution Design output contains planned strategies, grounded/priced designs, assessed candidate solutions and comparison, one recommendation, and separate guardrail results. Persisted lifecycle contracts also anticipate user selection and application, but the corresponding application workflow is not implemented.

## 2. CURRENT END-TO-END WORKFLOW

Implemented persisted path:

```text
backend.main: interactive requirements -> Request + empty checkpoint
  PROCESSING / SEARCHING
backend.search_main: existing request -> Search Agent -> CandidatePool
  successful Search persists CandidatePool -> PROCESSING / DESIGNING
```

Implemented, separately callable in-memory path:

```text
Requirements + CandidatePool
  -> PLAN -> DESIGN -> QUALITY CHECK -> RECOMMENDATION -> GUARDRAILS
  -> SolutionPipelineResult
```

`backend.services.search_workflow.run_search_for_request()` integrates Search with persistence. It does not start Solution Design. `backend.agents.solution_design_agent.run_solution_design()` does not read requests, persist outputs, or transition lifecycle state. The manual live script reconstructs inputs and calls it in memory.

`RequestService.design_completed()` exists and would persist `candidate_solutions` and advance to VALIDATING, but the Solution Design runner does not call it. Graph/nodes/routing, final validation, selection guardrail, application tool, evaluation modules, and frontend are placeholders. The implemented solution guardrails are distinct from the placeholder downstream workflow validation stage.

## 3. REPOSITORY MAP

| Location | Current responsibility |
| --- | --- |
| `backend/main.py` | Interactive requirement capture and persisted request creation. |
| `backend/search_main.py` | CLI Search for a persisted request ID. |
| `backend/agents/search_agent.py` | Search planning, tool retrieval, ID assessment/lookup, sufficiency Judge, optional single reassessment, grounding. |
| `backend/agents/solution_design_agent.py` | Entire in-memory Solution Design pipeline and runtime result containers; 708 lines including blank lines. |
| `backend/schemas/requirements.py`, `search.py`, `solution.py` | Customer, retrieval, and Solution Design contracts. |
| `backend/schemas/request.py`, `checkpoint.py`, `state.py` | Lifecycle, business checkpoint, runtime state, and trace contracts. |
| `backend/tools/search_tool.py`, `addon_search_tool.py` | SQLite-backed package/add-on retrieval tools. |
| `backend/tools/calculation_tool.py` | Canonical ID resolution and Decimal pricing. |
| `backend/services/request_service.py` | Lifecycle event coordination and shared-state reconstruction. |
| `backend/services/search_workflow.py` | Persisted Search orchestration. |
| `backend/services/llm_retry.py` | Shared two-attempt technical retry policy. |
| `backend/database/db.py` | SQLite catalog access and workflow-persistence mixin integration. |
| `backend/database/workflow_persistence.py` | Transactional request/checkpoint reads and writes. |
| `backend/database/migrations.py`, `seed_data.py` | Schema migration and explicit Excel-to-SQLite catalog seeding. |
| `backend/guardrails/solution_guardrails.py` | Implemented four-check deterministic solution guardrails and their result models. |
| `backend/guardrails/final_validation.py`, `selection_guardrail.py` | Placeholders. |
| `backend/evaluation/search_evaluation.py`, `solution_evaluation.py` | Placeholders; no external evaluation implementation. |
| `backend/workflow/graph.py`, `nodes.py`, `routing.py` | Placeholders, not the active orchestration. |
| `backend/services/tracing_service.py` | Placeholder; actual traces are constructed by agents/tools. |
| `backend/schemas/catalog.py`, `validation.py`, `application.py`; `backend/tools/apply_tool.py` | Placeholders. |
| `frontend/` | Placeholder application/pages/components, not an integrated UI. |
| `tests/` | `unittest` automated coverage using fake/injected LLM responses and temporary databases. |
| `live_solution_test.py` | Explicit manual real-LLM test with read-only request access and text logging. |
| `data/TELORA_Synthetic_Telecom_Packages.xlsx`, `data/telora.sqlite3` | Seed dataset and default SQLite database. |
| `telora-solution-live-*.txt`, `telora-step5b-*.txt` | Saved observations; older STEP 5B logs concern the removed architecture. |

## 4. DATA FLOW AND CORE SCHEMAS

| Object / definition | Producer -> consumer; important fields |
| --- | --- |
| `Requirements`, `NeedSelection`, `NeedLevel` — `schemas/requirements.py` | CLI/persisted reconstruction -> Search and all Solution Design stages. Frozen dataclasses; primary/secondary/third selections, Low/Medium/High, positive finite preferred budget. Duplicate needs rejected. |
| `SearchNeed` — `schemas/search.py` | `search_needs_from_requirements()` -> tools/prompts/validation. Preserves original slot as priority 1–3. |
| `PackageCandidate`, `AddonCandidate` — `schemas/search.py` | Search tools -> CandidatePool/calculation/assessment. ID, name, float `price_sar`, matched needs, match reasons, capability dictionary. |
| `CandidatePool` — `schemas/search.py` | Search canonical lookup/grounding -> checkpoint and Solution Design. Package/add-on lists, `search_status`, `search_notes`; no trace fields. |
| `SolutionStrategy`, `SolutionDesignPlan` — `schemas/solution.py` | PLAN -> independent DESIGN calls. Strategy ID, intent, focus priorities, intended tradeoffs; plan has 1–3 unique IDs. |
| `DesignedSolution` — `schemas/solution.py` | Structured basic proposal -> grounding/calculation. Original strategy, one package ID, 0–2 distinct add-on IDs; no model-generated prices. |
| `CalculationResult` — `schemas/solution.py` | `calculate_solution()` -> design tool trace, assessment, guardrails. Canonical package/add-on records plus Decimal total, preferred budget, difference. Component capabilities remain separate. |
| `CalculatedDesign` — `schemas/solution.py` | `_calculate_design()` -> final design/assessment. Composition plus Decimal pricing, no assessment. |
| `RequirementAlignment`, `SolutionAssessment` — `schemas/solution.py` | `assess_solution()` -> candidate output/comparison/recommendation. Original need/priority/level and STRONG/MODERATE/WEAK evidence; priority alignment/summary, budget fit/summary, strategy achievement/summary. |
| `CandidateSolution` — `schemas/solution.py` | `assess_solutions()` -> comparison/recommendation/guardrails. Strategy, IDs, three pricing fields, required assessment. These business pricing fields are floats, unlike calculation's Decimal fields. |
| `SolutionComparison`, `CandidateSolutions` — `schemas/solution.py` | Comparison and Quality Check -> recommendation/guardrails. Tradeoffs, diversity/summary; 1–3 assessed solutions. Unique strategy IDs and consistent requirement signatures/budgets enforced. |
| `SolutionRecommendation` — `schemas/solution.py` | `recommend_solution()` -> pipeline output. `recommended_solution_id` is an existing **strategy ID**; `why_recommended` is explanatory text. |
| `GuardrailCheck`, `SolutionGuardrailResult` — `guardrails/solution_guardrails.py` | Deterministic guardrails -> pipeline output/trace. Strategy ID, named check results, overall result, hard budget limit, optional recalculated total. |
| `SharedWorkflowState` — `schemas/state.py` | RequestService reconstruction -> workflow callers. Request/customer IDs, status/stage, Requirements, optional CandidatePool and downstream outputs. Not a separate store. |
| `StageTrace`, `ToolTrace` — `schemas/state.py` | Agent/tool execution -> callers/manual log. Dataclasses with status, timing, and structured details or input/output. |

Solution models forbid extra fields and non-finite numeric values. `CandidateSolution` checks budget difference (with a small float-arithmetic tolerance), budget-fit consistency, and focus priorities against assessed requirements. `CandidateSolutions` checks single-solution diversity consistency. Context-dependent reference validation is also performed by the consuming functions; schema validation alone does not establish factual correctness.

Runtime dataclasses in `solution_design_agent.py` are `CalculatedDesignResult`, `DesignRuntimeTrace`, `StrategyDesignResult`, `MultipleStrategyDesignResult`, `MultipleSolutionAssessmentResult`, and `SolutionPipelineResult`. The final container fields are `plan`, `designs`, `quality`, `recommendation`, `guardrails`, and `stage_traces`; `quality` is the business `CandidateSolutions` object.

## 5. SEARCH AGENT -> SOLUTION DESIGN HANDOFF

Search returns `TELORAState` containing the original requirements, grounded CandidatePool, and separate SearchTrace. The persisted Search service stores the pool through RequestService and returns reconstructed `SharedWorkflowState` plus the in-memory trace.

Search retrieval reads SQLite, identifies presence of relevant capability fields, and orders retrieved records by matched-need count, price, then ID. A matched need means capability presence, not proof that the requested Level is satisfied. Package capability dictionaries include data/social data, streaming/gaming benefits, voice, roaming/international allowances, multi-SIM, and data sharing. Add-on dictionaries include category and their available capability fields. They are selected projections of catalog rows, not the entire catalog (for example, compatibility/eligibility columns are not all transferred).

Search's LLM assessment selects ordered IDs; `resolve_assessment_selection()` reconstructs records from tool outputs and rejects unknown IDs. A Search sufficiency Judge may trigger one reassessment using the same retrieval evidence. No second retrieval or Judge follows that reassessment. Final grounding preserves canonical facts and selection order.

Solution Design receives the full supplied CandidatePool JSON in PLAN and basic DESIGN; it does not query the database or run Search. `resolve_composition()` builds separate package/add-on indexes, rejects ambiguous duplicate catalog IDs (including unselected duplicates), unknown/blank IDs, duplicate selected add-ons, and more than two add-ons. Facts are canonical **relative to the supplied pool**, not refreshed against today's database.

The programmatic Solution Design runner has no lifecycle or `search_status == SUCCESS` gate. The manual live-test wrapper checks both successful pool status and PROCESSING/DESIGNING before invoking it.

## 6. SOLUTION DESIGN AGENT — ACTUAL CURRENT IMPLEMENTATION

Entry point: `run_solution_design(requirements, candidate_pool, llm=None) -> SolutionPipelineResult`. All stages operate in memory. The optional injected LLM is propagated through the pipeline.

### PLAN

- **Purpose/input:** Derive customer-specific intents from original requirements and the full pool, including canonical prices.
- **Function/output:** `create_solution_design_plan()` -> `SolutionDesignPlan` with 1–3 `SolutionStrategy` objects.
- **Decision:** LLM structured function-calling output; Python validates schema, unique IDs, and focus-priority references.
- **Guidance:** Contextual Level targets, priority protection, meaningful strategy diversity, no generic fixed templates or committed composition; only explicitly requested needs.
- **Trace/retry:** `run_llm_operation('SOLUTION_DESIGN_PLAN', ...)`; success metadata is currently discarded. PLAN is returned as business data, not appended as a StageTrace. Terminal exceptions retain retry metadata.

### DESIGN

```text
Strategy -> Propose Composition ONCE -> Ground
         -> Deterministic Calculation ONCE -> DESIGN_FINAL
```

- **Functions:** `design_strategies()` executes plan order sequentially; each `design_strategy()` calls `create_basic_design()` and `_calculate_design()`.
- **Inputs/output:** Original Requirements, CandidatePool, one strategy -> `StrategyDesignResult` containing `CalculatedDesign` and separate runtime trace.
- **Decision:** LLM chooses IDs only; Python verifies the strategy is unchanged and resolves IDs. The Calculation Tool owns final money values.
- **Guidance:** Requirement fit rather than maximum capability; gap-driven add-ons; use canonical prices prospectively; preferred budget is soft. No numeric mappings from Low/Medium/High.
- **Invariants:** Exactly one base ID; 0–2 unique add-on IDs; focus priorities must exist; unknown or ambiguous IDs cannot proceed. Grounding is performed in basic design, again before calculation, and by the calculation tool; the pricing calculation itself occurs once in DESIGN.
- **Trace:** PROPOSAL, GROUND, DESIGN_FINAL; one successful `calculate_solution` ToolTrace. Exceptions add DESIGN/ERROR with error type and available retry metadata. A failed calculation does not create a successful tool observation.
- **Retries:** Only the proposal's eligible technical/schema failures receive the shared bounded retry. Unknown IDs fail during grounding outside that retry. No semantic retry, review, redesign, or budget-triggered loop.
- **Multiple-strategy failure:** Original exception carries `failed_strategy_id`, `completed_strategy_results`, and available `design_trace`; no silently partial successful return.

There is no executed DESIGN_REVIEW, KEEP/REVISE decision, revision composition, Revision Acceptance safeguard, ReAct action loop, Solution Design Judge, automatic refinement, or re-planning path. The review schema is removed. Some historical wording/logs remain, but do not create execution paths. The Search Agent's separate Judge remains implemented.

`create_calculated_design()` remains a callable helper for basic proposal plus calculation; the main runner uses `design_strategy()` to add stage tracing.

### QUALITY CHECK

- **Purpose/input:** Describe every completed design against original requirements and pool, then describe differences between the candidates. Weak or over-budget solutions are preserved.
- **Functions/output:** `quality_check()` -> `assess_solutions()` -> `assess_solution()` per strategy, then `compare_solutions()` -> `CandidateSolutions`.
- **Decision:** LLM contextual assessments; LLM comparison for multiple candidates. A single candidate gets deterministic SINGLE_SOLUTION comparison without a comparison LLM call.
- **Grounding:** Assessment consumes the existing successful tool observation, validates it against canonical selected records and original budget, and does not recalculate. Business composition/pricing are assembled by Python, not regenerated by the LLM.
- **Invariants:** Every original need/priority/level must appear exactly; strategy focus must reference real needs. Python sets budget fit/summary from calculated values. Comparison validates candidate requirement signatures, resolves canonical components, and computes same-composition/same-price pair facts. Duplicate compositions require OVERLAPPING; otherwise diversity is contextual.
- **Trace/retry:** ASSESS per strategy, COMPARE, then QUALITY_CHECK aggregate. ASSESS/COMPARE carry technical retry metadata. Original exceptions retain partial assessed solutions and assessment/comparison/quality traces. No ranking, filtering, or redesign here.

### RECOMMENDATION

- **Purpose/input:** Choose one existing candidate using Requirements and the completed CandidateSolutions evidence.
- **Function/output:** `recommend_solution()` -> `SolutionRecommendation`.
- **Decision/invariants:** LLM structured output; Python requires an existing strategy ID. It cannot return new components or pricing through this schema. Candidate data remains unchanged.
- **Trace/retry:** RECOMMENDATION/STARTED followed by SUCCESS or ERROR; shared technical retry and metadata.

### GUARDRAILS

- **Purpose/input:** Report deterministic validity of all candidates, after recommendation. Inputs are Requirements, CandidatePool, CandidateSolutions, and final CalculationResult evidence keyed by strategy ID, reconstructed from design tool traces.
- **Function/output:** `check_solution_guardrails()` -> ordered `list[SolutionGuardrailResult]`.
- **Decision:** Python with deterministic canonical price recalculation; no LLM, retries, redesign, fallback, or recommendation change.
- **Trace:** One GUARDRAILS/PASS or FAIL StageTrace per candidate. All four results remain present even when one fails. Rules are detailed below.

## 7. LLM AND TOOL BOUNDARIES

| Responsibility | Owner |
| --- | --- |
| Strategy intents and prospective composition choice | PLAN/DESIGN LLM calls. |
| Contextual requirement/priority/strategy assessment | Assessment LLM; budget facts supplied/overwritten by Python. |
| Descriptive comparison | Comparison LLM except deterministic single-candidate case; duplicate facts supplied by Python. |
| Recommended existing candidate and explanation | Recommendation LLM. |
| Structured validation, reference integrity, order, failures | Python. |
| Canonical IDs/records | Python `resolve_composition()` against CandidatePool. |
| Final total and budget difference | Decimal Calculation Tool. |
| Four guardrail verdicts | Deterministic Python, including canonical recalculation. |
| Lifecycle/checkpoint writes | RequestService and database persistence, outside Solution Design. |

Solution Design's factory uses `OPENAI_MODEL`, default `gpt-4o-mini`, temperature 0, and `max_retries=0`. Structured calls use `with_structured_output(..., method='function_calling')`. No independent Judge client is used by Solution Design. Search uses the same main-model default and a separate `JUDGE_MODEL` default `gpt-5.6-terra`, with `reasoning_effort='none'`, temperature 0, and SDK retries disabled. These are repository configuration defaults, not assertions of API-account availability.

## 8. GROUNDING AND HALLUCINATION CONTROL

The DESIGN response contains strategy and IDs, not names, capabilities, or prices. Python requires strategy equality, then resolves canonical components. `calculate_solution()` rejects invalid/negative/non-finite selected prices and invalid/nonpositive budget, uses `Decimal(str(value))` and sufficient local arithmetic precision, and deep-copies canonical records into its result. It sums component prices and subtracts the preferred budget; it does not mathematically merge heterogeneous capabilities.

This prevents unknown selected IDs and substituted model-generated totals from becoming final business data. It does not deterministically prove the semantic quality of strategy intents, assessment evidence/summaries, comparison tradeoffs, or recommendation explanations. Those remain LLM-generated free text. Capability matching and pool membership do not establish compatibility, eligibility, or externally defined usage-level thresholds. No hidden chain-of-thought is requested or stored.

## 9. GUARDRAILS

`BUDGET_TOLERANCE = Decimal('0.15')` is defined directly in `solution_guardrails.py`.

| Check key | Actual check |
| --- | --- |
| `catalog_validity` | Canonical calculation resolves selected IDs from the pool. Missing/ambiguous IDs or invalid selected prices fail. Final calculation evidence must exist and its package/add-on records must equal canonical records, including facts/prices. |
| `composition_validity` | One nonblank package ID; list of 0–2 nonblank, distinct add-on IDs. |
| `price_integrity` | Stored solution total, converted through `Decimal(str(...))`, must exactly equal the recalculated canonical total. |
| `budget_hard_limit` | Canonical total must be <= original Requirements budget × 1.15. Equality passes. For 320 SAR the limit is 368 SAR. |

If canonical total is unavailable, price and budget checks fail explicitly. Expected business-rule failures return FAIL/reason; technical errors may propagate. Overall PASS requires all checks PASS. Results include strategy ID, check dictionary, overall status, hard budget limit, and optional canonical total. The preferred budget is available from Requirements/business solutions rather than duplicated in this result model.

Guardrails preserve every candidate and its assessment/comparison, consume no recommendation object, and never replace the recommendation—even when the recommended candidate fails. The four checks do not implement telecom compatibility, eligibility, or other catalog business rules.

## 10. TRACING / OBSERVABILITY

- `StageTrace`: stage, status, execution time, details. `ToolTrace`: tool name, input, output, status, execution time, optional Search task index.
- Design traces live under each `StrategyDesignResult`. Pipeline-level `stage_traces` begin with assessment, not PLAN/DESIGN; they continue through comparison, Quality Check, recommendation, and guardrails.
- Retry metadata contains operation, attempts, retry_count/reason, sanitized failure categories, and final technical result. `run_llm_operation()` allows one immediate retry (two total attempts) for parsing/schema errors, connection/timeouts, explicitly identified transient rate limits, and selected server errors. Quota/auth/access/unknown rate-limit errors are not generally retried.
- Successful PLAN/basic-DESIGN retry metadata is discarded by those functions. Assessment, comparison, and recommendation retain it. Terminal LLM exceptions carry `llm_retry`.
- Traces expose selected IDs, canonical tool observations, descriptive assessments, duplicate facts, recommendation and guardrail outcomes. They do not retain full raw rejected LLM payloads or every prompt/response, and are not an independent audit database.
- SearchTrace and Solution Design traces stay in memory; `tracing_service.py` is a placeholder. The manual script serializes available results/failure evidence into a local text file.
- Proposal/ground/final and guardrail trace timings are currently recorded as 0.0; calculation and several LLM stages have measured timings.

## 11. SHARED STATE / PERSISTENCE

Default database: `data/telora.sqlite3`. `SearchDatabase` initialization creates schema/applies migrations; explicit seeding reads the Excel dataset. Runtime retrieval reads SQLite, not Excel. `WorkflowPersistence` stores request metadata/Requirements separately from latest checkpoint outputs. Transitions use transactions and expected source status/stage pairs. Reconstruction reads request/checkpoint from a consistent snapshot and validates without repairing corrupt data.

Statuses: PROCESSING, WAITING_FOR_USER, NEEDS_ADJUSTMENT, COMPLETED, ERROR. Stages: REQUIREMENTS, SEARCHING, DESIGNING, VALIDATING, PREPARING_SOLUTIONS, SELECTION, APPLYING, COMPLETED, ERROR.

Service lifecycle events:

```text
create_request -> PROCESSING/SEARCHING
search_succeeded -> PROCESSING/DESIGNING
search_needs_adjustment -> NEEDS_ADJUSTMENT/REQUIREMENTS
resubmit_requirements -> PROCESSING/SEARCHING (clears checkpoint outputs)
design_completed -> PROCESSING/VALIDATING
validation_completed -> PROCESSING/PREPARING_SOLUTIONS
solutions_ready -> WAITING_FOR_USER/SELECTION
select_solution -> PROCESSING/APPLYING
apply_completed -> COMPLETED/COMPLETED
mark_error -> ERROR/ERROR from allowed active states, retaining outputs
```

The existence of an event method does not mean its producing agent is integrated. Search orchestration calls its events; Solution Design does not. `WorkflowCheckpoint` and `SharedWorkflowState` still use `TemporaryWorkflowOutput` (JSON dict/list) for candidate_solutions, solution_validation, final_solutions, selected_solution, and applied_solution, despite typed Solution Design business models now existing. Neither has dedicated recommendation/guardrail fields. No serialization/transition adapter from SolutionPipelineResult to those persisted downstream outputs is currently wired.

## 12. TESTING

Last completed full-suite verification in this session: **169 passed, 0 failed**. The initial sandbox run could not access temporary SQLite directories; rerunning with the required filesystem permission completed successfully. Earlier simplification verification: **22 focused DESIGN tests** and **81 relevant tests**, all passed. Tests were not rerun for this documentation task.

| Test files under `tests/` | Main coverage |
| --- | --- |
| `test_requirements.py` | Need/budget/submission validation and serialization. |
| `test_search_layer.py`, `test_search_workflow.py` | Retrieval grounding, Search contracts/Judge/reassessment, persisted Search lifecycle. |
| `test_solution_design_plan.py` | Plan schema, original customer/pool context, prompt guidance, retry/configuration. |
| `test_basic_design.py`, `test_calculated_design.py`, `test_strategy_design.py`, `test_multiple_strategy_design.py` | Grounded composition, one proposal/calculation, technical retry, order/independence, partial failure evidence. |
| `test_calculation_tool.py`, `test_solution_schemas.py` | Decimal arithmetic, ambiguous/invalid IDs/prices, structural and JSON contracts. |
| `test_solution_assessment.py`, `test_multi_solution_assessment.py`, `test_solution_comparison.py` | Independent assessments, original requirement references, pricing evidence, duplicate/diversity constraints. |
| `test_quality_check.py`, `test_recommendation.py` | Descriptive output preservation, recommendation references, final evidence/order and guardrail integration. |
| `test_solution_guardrails.py` | Four checks, exact budget boundary, missing/mismatched evidence, no mutation. |
| `test_llm_retry.py` | Retry allowlist/bounds and sanitized terminal metadata. |
| `test_database.py`, `test_workflow_persistence.py`, `test_request_service.py`, `test_shared_state.py`, `test_legacy_cleanup.py` | Catalog round trips, migrations, transactions, lifecycle guards, reconstruction and retired persistence separation. |

Commands from project root in PowerShell:

```powershell
# Automated suite; fake/injected LLMs, no live API requests.
.\.venv\Scripts\python.exe -B -m unittest discover -s tests -q

# Explicit manual live run; real configured LLM/API credentials required.
.\.venv\Scripts\python.exe -B .\live_solution_test.py
```

Fake LLM tests verify deterministic contracts, routing, and the guidance supplied to models; they do not establish real-model semantic consistency. The manual script uses the real configured clients, existing request/pool, no Search, and SQLite `mode=ro` plus `PRAGMA query_only`. It snapshots request/checkpoint rows before/after, prints results or available exception evidence, and tees stdout/stderr to `telora-solution-live-YYYYMMDD-HHMMSS-microseconds.txt`. It catches exceptions for reporting, so the console/log—not just process exit status—identifies success.

## 13. LATEST LIVE TEST

Latest saved Solution Design run found in the repository: **`telora-solution-live-20260922-134143-260487.txt`**. This section reports that artifact, not a newly executed test or an independent certification of semantic correctness.

- Request: `REQ-0AF9870D628F47D0BB964654903894DA`.
- Requirements: P1 Data = Medium; P2 Streaming = High; P3 Gaming = Low; preferred budget 320 SAR.
- Persisted input in the log: PROCESSING/DESIGNING; SUCCESS CandidatePool, **88 packages and 1 add-on**.

Recorded plan intents:

| ID | Intent as recorded |
| --- | --- |
| STRATEGY-1 | Focus on satisfying the Medium Data and High Streaming needs while exploring options for the Low Gaming need if budget allows. |
| STRATEGY-2 | Prioritize the High Streaming need while ensuring the Medium Data need is met, with a potential Gaming add-on if budget permits. |
| STRATEGY-3 | Maximize the Data and Streaming needs while evaluating the feasibility of the Gaming add-on within the budget constraints. |

All three strategies produced the same initial and final composition: **TEL-P047 (Relay Youth 30) + ADD-GAME**, total **178.25 SAR**, budget difference **-141.75 SAR**. Each design trace contains PROPOSAL -> GROUND -> DESIGN_FINAL and one calculation observation; no review/revision.

Quality Check reported all three requested needs STRONG for each solution, priority ALIGNED, budget WITHIN, and strategy ACHIEVED. These are recorded assessment outputs. Duplicate detection marked identical compositions/pricing for all three pairs; diversity was OVERLAPPING, with the summary that all solutions provide identical components/pricing and strong alignment.

Recorded comparison tradeoffs:

1. Explore options for Gaming add-on if budget allows after satisfying Data and Streaming needs.
2. Consider sacrificing enhancements for Gaming if necessary to maintain budget for Streaming and Data.
3. Assess the impact of including the Gaming add-on on the overall budget after securing Data and Streaming needs.

Recommendation was **STRATEGY-1**; its explanation cited Medium Data/High Streaming fit, Low Gaming support if budget allows, strong alignment/priorities, and operation within budget. Guardrails reported all four checks PASS and overall PASS for every candidate; canonical total 178.25 SAR, preferred budget 320 SAR, hard limit 368 SAR.

Pipeline-level recorded trace order:

```text
ASSESS SUCCESS x3 -> COMPARE SUCCESS -> QUALITY_CHECK SUCCESS
-> RECOMMENDATION STARTED -> RECOMMENDATION SUCCESS -> GUARDRAILS PASS x3
```

The log printed `RECOMMENDATION -> GUARDRAILS: True`, `PERSISTED REQUEST/CHECKPOINT UNCHANGED: True`, and final PROCESSING/DESIGNING. The database was not re-read during this handoff task; these state observations come from the saved run. The log does not establish the exact model override/API account used, nor contain a full raw LLM transcript.

## 14. IMPORTANT CURRENT CONSTRAINTS / DESIGN ASSUMPTIONS

- Level means desired strength/amount; Priority means relative importance under tradeoffs. These semantic rules are prompt guidance, not deterministic numeric fit rules.
- Only original needs should drive planning/fit; no fixed GB-to-Low/Medium/High thresholds or scoring is implemented.
- One successful composition proposal per strategy; technical retries may repeat that same operation after eligible failure. There are no design/refinement iteration bounds because those semantic loops no longer exist.
- Canonical pool prices drive prospective choice and deterministic final pricing; extra capability is not automatically better fit under the prompt guidance.
- Selected IDs and pricing are validated; free-text claims are not exhaustively fact-checked.
- Exactly one package and at most two distinct add-ons; capabilities remain separate rather than summed across heterogeneous categories.
- All candidate solutions are retained through Quality Check, Recommendation, and Guardrails, including duplicates, weak fits, and budget failures.
- Recommendation precedes Guardrails; a failed recommended candidate is not replaced.
- Preferred budget is soft in DESIGN/assessment; the separate Guardrail hard limit is +15%.
- Typed solution models support JSON serialization. Decimal calculation/design values serialize as strings; CandidateSolution pricing uses floats.
- Shared state is reconstructed runtime memory. Solution Design does not automatically persist or advance lifecycle state.

## 15. CURRENT IMPLEMENTATION STATUS

| Component | Status | Main entry point | Tests |
| --- | --- | --- | --- |
| Requirements | Implemented terminal capture and models | `backend.main`, `Requirements` | requirements, request service |
| Search Agent | Implemented and persistence-integrated | `run_search_for_request()` / `run_search_agent()` | search layer/workflow |
| Solution Planning | Implemented in memory | `create_solution_design_plan()` | solution design plan |
| Solution Design | Implemented single-proposal design | `design_strategy()` / `design_strategies()` | basic/calculated/strategy/multiple design |
| Quality Check | Implemented descriptive assessment/comparison | `quality_check()` | assessment/comparison/quality |
| Recommendation | Implemented in memory | `recommend_solution()` | recommendation/quality |
| Guardrails | Implemented and appended to in-memory pipeline | `check_solution_guardrails()` | solution guardrails/quality |
| Workflow Integration | Search integrated; later lifecycle events exist, Solution Design not connected; graph placeholders | `RequestService`, `run_search_for_request()` | service/state/persistence/search workflow |
| Evaluation / LLM Evaluation | External evaluation modules are placeholders; Search runtime Judge exists separately | `evaluation/*.py`; Search `judge_shortlist()` | Search Judge tests only, no external evaluation module implementation |
| UI | Placeholder | `frontend/app.py` | No functional UI coverage identified |

## START HERE FOR A NEW ENGINEER

Read order (navigation only):

1. `backend/schemas/requirements.py` and `backend/schemas/search.py` — actual input contracts.
2. `backend/schemas/solution.py` — business output and intermediate types.
3. `backend/agents/solution_design_agent.py` — start at `run_solution_design()`, then PLAN, `design_strategies()`/`design_strategy()`/`create_basic_design()`, Quality Check, and Recommendation.
4. `backend/tools/calculation_tool.py` and `backend/guardrails/solution_guardrails.py` — canonical facts, arithmetic, deterministic checks.
5. `backend/schemas/state.py` and `backend/services/llm_retry.py` — observable evidence and technical failure contract.
6. `tests/test_strategy_design.py`, `test_quality_check.py`, `test_solution_guardrails.py` — current pipeline boundaries in executable tests.
7. `live_solution_test.py` and the latest saved log named above — manual invocation and recorded real output.
8. `backend/services/search_workflow.py`, `backend/agents/search_agent.py`, and the two search tools — CandidatePool provenance.
9. `backend/services/request_service.py`, `backend/schemas/request.py`, `checkpoint.py`, `backend/database/workflow_persistence.py` — existing lifecycle integration boundary.

Verification limits: current behavior was inspected from source and the saved live log; full-suite counts come from the completed verification immediately preceding this handoff. No new tests/API calls/database reads were performed for the handoff. Recorded live semantic claims are reproduced as observations, not certified conclusions; placeholder modules do not establish future implementation behavior.
