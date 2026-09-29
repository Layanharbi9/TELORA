# TELORA

## Supported workflow

SQLite Request ? reconstructed SharedWorkflowState ? SearchWorkflow ? Search Agent
? CandidatePool + SearchTrace ? RequestService ? workflow checkpoint ? reconstructed state.

Run the full customer flow from the terminal:

```bash
python -m backend.main
```

The form collects the customer ID and requirements, creates a persisted request,
runs Search and Solution Design, displays the final solutions, records the
customer's choice, and invokes the deterministic Apply tool. To continue an
existing request (including one created before Search ran), use:

```bash
python -m backend.main --resume REQUEST_ID
```

Search and Solution Design require configured OpenAI credentials. The
standalone Search-only entry point remains available as
`python -m backend.search_main REQUEST_ID`. The programmatic entry point is
`backend.services.search_workflow.run_search_for_request(request_id)`.
Optional `request_service`, `llm`, and `judge_llm` arguments support injection;
catalog reads retain the Search Tools' existing database configuration.

Only PROCESSING/SEARCHING requests may start Search. SUCCESS persists the pool
and advances to DESIGNING. INSUFFICIENT/NO_RESULTS persist the pool and move to
NEEDS_ADJUSTMENT/REQUIREMENTS. ERROR and execution failures mark ERROR while
preserving prior checkpoint outputs; execution exceptions are re-raised.

`SearchWorkflowResult` contains reconstructed `state` and in-memory `search_trace`.
The Search Agent returns its result without performing persistence. RequestService
owns lifecycle writes. Traces are excluded from business persistence and runtime
SharedWorkflowState. Search stops before Solution Design.

## Catalog and database

```bash
python -m backend.database.seed_data
```

Explicit seeding imports 100 packages, 12 add-ons, and 50 customers from
`data/TELORA_Synthetic_Telecom_Packages.xlsx`. Repeated seeding upserts original IDs;
`--reset` replaces catalog rows. Both preserve Current Plan snapshots, requests,
orders, and workflow checkpoints.
`--dataset` and `--database` select alternative paths. Runtime tools read SQLite
only and do not automatically seed. Empty catalogs produce a structured error.

Initialization applies transactional SQLite migrations (currently schema version 14).
The migrations preserve the original catalogs and existing workflow requests/checkpoints.
Version 13 adds customer/order foreign keys to requests and protects immutable order
snapshot fields and order deletion. Legacy request rows with customer IDs absent from
the catalog are preserved by migration; new requests must reference a real catalog
customer.

## Customer selection, Apply, and history

TELORA keeps one SQLite database as its source of truth:

- `current_plans` contains one complete current package snapshot per customer.
  It stores package allowances and benefits resolved from `packages`, plus add-on
  IDs, names, and catalog details as JSON text. Initialization inserts missing
  customers only, so it does not replace a plan already changed through TELORA.
- `requests` remains the existing workflow request table and now also stores
  customer-facing request history, requirements, recommended/selected solution
  IDs, previous/new full plan snapshots, business status, errors, and timestamps.
  The previous snapshot is captured when the request starts. The selected target
  snapshot is resolved from SQLite and saved when the customer chooses a solution,
  so it remains visible while application is pending or after a failure.
- `orders` is historical and stores the selected composition, prices
  recomputed from the original catalog, previous/new full plan snapshots, status,
  and dates. Database triggers prevent changes to its identity, original composition,
  plan snapshots, price, and creation time, and prevent deletion. Lifecycle status,
  error, and completion fields remain mutable.
- `order_status_history` records each order transition (`PENDING`, `PROCESSING`,
  then `ACTIVE` or `FAILED`).

The request's business status progresses through `STARTED`, `SOLUTIONS_PRESENTED`,
`CUSTOMER_SELECTED`, `PENDING`, `PROCESSING`, then `ACTIVE` or `FAILED`. The
existing workflow status/stage fields remain responsible for Search/Design and
move through `WAITING_FOR_USER/SELECTION`, `PROCESSING/APPLYING`, then
`COMPLETED/COMPLETED` or `ERROR/ERROR`.

When initializing original customer plans, TELORA uses `customers.current_price_sar`
as the recorded current total and resolves the package name/allowances from
`packages`. The source customer field `requested_addon_id` is not treated as an
active add-on; the original data does not identify a current add-on. New selected
plans calculate the total from `packages.monthly_price_sar` and each selected
`addons.monthly_price_sar`. No client-supplied price or name is trusted.

The existing flow ends after Solution Design persists its final solutions and
waits at `WAITING_FOR_USER/SELECTION`. The application boundary is deterministic:

```python
from backend.database.db import SearchDatabase
from backend.services.request_service import RequestService
from backend.tools.apply_tool import apply_selected_solution

database = SearchDatabase()
requests = RequestService(database)

# A customer starts a request; this validates the customer and stores history.
request = requests.start_customer_request(customer_id, requirements)

# Run the existing search and solution-design workflows. Once final solutions
# are ready, present them and pass the customer's choice to the apply service.
checkpoint = requests.get_checkpoint(request.request_id)
chosen = next(s for s in checkpoint.final_solutions["solutions"]
              if s["solution_id"] == selected_solution_id)
requests.select_solution(request.request_id, chosen)
result = apply_selected_solution(
    customer_id, request.request_id, selected_solution_id,
    package_ids=[chosen["package"]["package_id"]],
    addon_ids=[a["addon_id"] for a in chosen["addons"]],
    database=database,
)
```

`RequestService.create_request` verifies the customer against SQLite and resolves
`customer_name` from the customers catalog; customer entry generates request IDs on
the backend. Apply verifies that the choice is in the persisted final-solution output, checks
the customer and catalog IDs and add-on compatibility, generates the order ID,
and uses catalog values for the complete new Current Plan snapshot. A repeated
application for a request returns its existing order. The prototype activation
is simulated locally; it does not activate a real telecom service. Activation or
database failure records a failed order/request and leaves `current_plans`
unchanged. The successful order, plan snapshot, request history, and checkpoint
result are committed together. If SQLite also rejects failure bookkeeping, Apply
returns `status="ERROR"` and `failure_recorded=false`; it does not claim FAILED was
persisted.

For LangChain tool calling, `backend.tools.apply_tool.apply_tool` is the decorated
`StructuredTool` and can be bound to a model with `model.bind_tools([apply_tool])`.
The customer-selection step must first persist the customer's choice with
`RequestService.select_solution`; the tool checks that saved choice and will not
choose a solution itself. The existing workflow does not make an LLM call after
selection, so application remains deterministic when called by the backend.

`HistoryService` provides `get_current_plan`, customer request/order history,
owner-scoped request/order detail lookups, paginated admin request/order lists,
filters, and status counts. Detail methods require both the record ID and
customer ID. The FastAPI customer API uses the existing service for plan and
request history. The browser's customer selector sets an HttpOnly demo cookie;
this is a local prototype session and is not authentication. Bind the customer
ID to a real authenticated principal before public deployment. Admin reporting
remains service-only; the supplied web design's Admin screen is still a
coming-soon placeholder and no public admin endpoints are exposed.

## FastAPI web application

The supplied browser interface is in `frontend/web` and is served by the same
FastAPI application as the JSON API, so browser calls are same-origin and need
no separate CORS configuration. Install dependencies and start it from the
repository root:

```bash
python -m pip install -r requirements.txt
uvicorn backend.api.app:app --reload
```

Open `http://127.0.0.1:8000`. The customer flow reads customers, current plans,
and request history from the project's SQLite database. `POST /api/build`
validates the three ordered needs and monthly budget, creates the historical
request, then runs the existing Search → Solution Design graph in a background
task. `GET /api/build/{request_id}/events` streams progress and the persisted
recommendations. `POST /api/requests/{request_id}/apply` validates ownership and
the chosen persisted solution, then calls the existing deterministic Apply
service. A successful apply refreshes the SQLite Current Plan and history; no
client-provided package names or prices are trusted.

The selection screen is explicitly a Demo Mode customer picker. Its cookie is
only an ownership selector, not identity verification. Do not expose this demo
session as production authentication. The Admin screen is not yet connected.

Run the database migration by opening `SearchDatabase` (all app services do this
automatically). After importing or refreshing catalogs, `seed_data` creates any
missing Current Plan snapshots without replacing existing ones.

## LangSmith tracing and evaluation

LangSmith is the central trace and experiment service. Set credentials in the
shell or deployment environment; TELORA never stores the key in SQLite or source
files:

```bash
export LANGSMITH_API_KEY="your-langsmith-api-key"
export LANGSMITH_PROJECT="TELORA"
export LANGSMITH_TRACING="true"
export TELORA_ENVIRONMENT="development"
# Optional for a self-hosted endpoint; the SDK default is used otherwise.
export LANGSMITH_ENDPOINT="https://api.smith.langchain.com"
```

When tracing is disabled, unavailable, or missing its API key, the business
workflow continues without remote tracing. `workflow_trace_roots` (migration
14) stores one root run ID per request. The root covers request creation,
Search, Solution Design, selection, Apply, and the final persisted state. If a
customer leaves at selection and later runs `--resume`, that resumed span is
attached to the saved root and its output is refreshed with the terminal state.
The root metadata contains `workflow_id`, `request_id`, `customer_id`, system,
environment, workflow version, and final status/latency in its output.

Trace shape:

```text
telora.workflow
├── request.created / request.lifecycle_transition
├── LangGraph Search node
│   ├── search_agent
│   │   ├── llm_operation.plan → package_search / addon_search
│   │   ├── llm_operation.assess
│   │   ├── llm_operation.judge (the in-runtime Search Judge)
│   │   └── llm_operation.reassess (only when Judge verdict is INSUFFICIENT)
│   ├── search.stage.* events: RETRIEVE, LOOKUP, GROUNDING, final pool, etc.
│   └── request.updated.search
├── LangGraph Solution Design node
│   ├── solution_design_agent and llm_operation.solution_design_*
│   ├── solution_design.stage.* and final_solutions
│   └── request.updated.solution-design
├── customer.selection
└── apply_tool
    ├── validation events
    ├── order.pending → order.processing → order.active / order.failed
    ├── activation.simulation
    ├── current_plan.before_apply → current_plan.after_apply
    ├── request.completed / request.failed
    └── transaction.commit / transaction.rollback
```

LangChain/LangGraph model and tool runs are children when their callbacks are
active. TELORA also records explicit operation spans for each bounded LLM
attempt; technical retries include operation, attempt, max attempts, error
category, retryability, and final result. The Runtime Judge remains part of
Search and is separate from external LangSmith LLM evaluators. Semantic
reassessment is still limited to one and does not repeat retrieval.

Each live Search Agent, Solution Design Agent, and Apply Tool run also gets a
child run named `evaluation` in this same trace. Open the agent/tool run in the
LangSmith trace tree and expand its `evaluation` child to see every metric key,
score, PASS/PARTIAL/FAIL value, and a short evidence comment. The same numeric
scores are attached to the agent/tool run as LangSmith feedback, so they appear
in the run's feedback/scores panel as well. Runtime scores are deterministic
checks only; semantic LLM judgments remain opt-in in the separate Experiments
view so normal customer requests do not trigger evaluator model calls.

Live runtime metrics:

- **Search Agent:** plan contract, retrieval correctness, candidate ID validity,
  canonical integrity, needs coverage, Runtime Judge contract, reassessment
  correctness, and grounded catalog data.
- **Solution Design Agent:** solution completeness, candidate/catalog
  constraints, budget compliance, canonical price, and needs coverage.
- **Apply Tool:** input/selection validation, canonical price when applicable,
  request/order/Current Plan consistency, and failure rollback.

Needs coverage is a fraction from 0 to 1; remaining runtime checks are binary.
The evaluation child reports their arithmetic mean as `aggregate_score` and
includes per-metric evidence. A metric that does not apply to a failed
pre-validation Apply is labeled `NOT_APPLICABLE` and is excluded from the mean.

Traces include structured requirements, budgets, candidate IDs/catalog values,
validated outputs, state transitions, and errors. They omit customer names,
credentials, secret-like values, and private reasoning fields. Model
configuration is recorded by model name, never by API key. Customer ID is
included as requested for trace filtering. Do not put free-form personal
information into structured requirements.

### Evaluation metrics and explicit rubrics

Rubrics live in `backend/evaluation/rubrics.py`. Each metric defines its
meaning, expected behavior, PASS and FAIL criteria, scoring rule, evidence, and
evaluator type. Deterministic criteria score 1.0 only when the structured
evidence proves PASS; otherwise they score 0.0. Semantic graders are opt-in and
return only 0.0, 0.5, or 1.0 plus a short evidence statement; they do not emit
chain-of-thought.

- **Search:** relevance, needs coverage, diversity, retrieval correctness,
  candidate ID validity, canonical integrity, groundedness, insufficient-result
  handling, reassessment correctness, no hallucinated catalog values, and
  runtime Judge verdict/gap correctness.
- **Solution Design:** requirements alignment, needs coverage, budget compliance,
  solution completeness, groundedness, constraint satisfaction, internal
  consistency, and solution quality.
- **Apply:** input/customer/solution/package/add-on validation, canonical price,
  previous-plan preservation, order correctness, Current Plan update, request
  history, transaction integrity, and failure rollback.
- **Workflow:** stage transitions, state propagation, agent handoff, tool
  invocation, required inputs, unexpected outputs, request/order relationship,
  and final persisted state.
- **Final system:** relevance, needs coverage, diversity, groundedness, solution
  quality, budget compliance, constraint satisfaction, and final-state
  consistency.

Catalog IDs/prices/fields in dataset references are generated from the existing
SQLite database at dataset-sync time. Other expected values are explicit
business contracts (for example, a simulated activation failure must preserve
the Current Plan). No generated solution is presented as a golden answer.
Adversarial invalid IDs are marked as synthetic validation fixtures.

Create the five requested datasets once; repeated runs add only missing
`case_id`s and do not duplicate existing examples:

```bash
python -m backend.evaluation.cli sync-datasets
```

Run one experiment explicitly:

```bash
python -m backend.evaluation.cli run --dataset search_agent_eval
python -m backend.evaluation.cli run --dataset solution_design_eval
python -m backend.evaluation.cli run --dataset apply_tool_eval
python -m backend.evaluation.cli run --dataset workflow_eval
python -m backend.evaluation.cli run --dataset end_to_end_eval
```

Search, Solution Design, workflow, and end-to-end targets make live model calls.
`apply_tool_eval` uses isolated SQLite copies and deterministic simulated
activation. Add `--semantic` to run the optional external LLM graders; it uses
`EVALUATION_MODEL` or `OPENAI_MODEL` and can incur model charges. These commands
are never part of the normal unit-test suite. Experiments and traces appear in
the configured LangSmith project; inspect each named metric and its evidence,
not only the aggregate score. Technical failure, business failure, and
evaluator failure are represented separately by run errors, structured
statuses, and metric scores.

### Trace/evaluation coverage matrix

| Component | Input Traced | Output Traced | Errors Traced | Deterministic Eval | LLM Eval | Dataset | Tests |
|---|---|---|---|---|---|---|---|
| Request creation/lifecycle | customer/request IDs, structured requirements | created and transitioned status/stage | persistence and transition exceptions | lifecycle and request/order contract | — | workflow, end-to-end | request/workflow persistence + observability |
| Search Agent | requirements, budget, model names | CandidatePool and SearchTrace | span errors and retry category/attempt | IDs, catalog identity, bounds, Judge contract | relevance, grounding, Judge review | search_agent_eval | search layer + retry + observability |
| Search tools/stages | tool arguments, needs, budgets | retrieval IDs/counts, assessment, canonical lookup, Judge, reassessment, grounding | tool/stage status and raised errors | retrieval membership, no second retrieval, at most one reassessment | semantic relevance/diversity | search_agent_eval | search layer + observability |
| Solution Design | requirements, CandidatePool, prior-plan summary | solutions, IDs, prices, trade-offs, guardrails | agent/LLM errors | schema, catalog membership/price, budget and constraints | alignment, coverage, groundedness, quality | solution_design_eval | solution pipeline + observability |
| Customer selection | request/customer IDs and presented/selected solution IDs | selection validation and canonical composition | rejected or invalid selection | persisted recommendation membership | — | apply_tool_eval, workflow_eval | request/apply + observability |
| Apply Tool | IDs and submitted package/add-on IDs | order/request status, plan snapshots, timestamps | validation, activation, rollback, failure-persistence errors | catalog/price/order/current-plan/history/transaction checks | — | apply_tool_eval | post-recommendation + observability |
| SQLite state events | request/order/customer references | request, order, Current Plan before/after state | transaction and persistence failures | final state consistency | — | workflow_eval, end_to_end_eval | DB/service + observability |
| Final workflow | request ID and workflow inputs | final response and persisted request/order/plan | root and child run errors | stage/relationship/final-state consistency | final solution quality (opt-in) | end_to_end_eval | workflow + observability |
| External evaluators | prediction plus catalog/business reference | named scores and short evidence | evaluator run failure | rubric-specific deterministic checks | strict semantic criteria only | all five | rubric/dataset tests |

## Tests

```bash
python -m unittest discover -s tests -v
```

Focused current-plan and post-recommendation coverage:

```bash
python -m unittest tests.test_current_plan_service tests.test_post_recommendation_workflow
```

### Search stages

PLAN and RETRIEVE are driven by native LLM tool calling: the Search Agent model is
bound to the `package_search`/`addon_search` tools (`bind_tools`) and itself decides
whether to call one, both, or which original needs go to each call. Python never
constructs this decision; it only grounds each call against the original
requirements (original need names, levels, priorities, and budget only -- no
invented, altered, or renumbered values) before executing it, and returns the
result as a `ToolMessage` to the same conversation so the model can continue
reasoning. Invalid or ungrounded calls are rejected rather than repaired, using
the existing bounded technical retry. The native tool-calling loop is bounded by
`max_tool_rounds`. `SearchPlan` (`tasks`: `tool_name`/`need_priorities`, plus a
concise `assessment_focus`) is reconstructed afterward from the calls the model
actually made, purely for tracing and downstream ASSESS context; coverage of every
requested need and at-most-once-per-tool use are still validated. Retrieval runs
once per Search; an optional single Re-ASSESS reuses these original results.

Assessment interprets usage levels contextually, prioritizes Primary over Secondary
over Third, and treats budget as a soft preference. It ranks packages and add-ons
separately while retaining meaningful alternatives; it does not select or calculate
a final solution or decide final eligibility/compatibility.

The plan, task-linked tool results, stage timings/statuses, ordered shortlist IDs,
and concise outcome notes are stored in `SearchTrace`, not CandidatePool. Private
chain-of-thought is not requested or recorded.
Traces remain in memory; technical failures are handled by SearchWorkflow. `max_tool_rounds`
(default 6) bounds the native tool-calling loop the model drives during PLAN.

Assessment returns ordered package/add-on IDs, status, and notes only. Lookup
preserves selection order and rejects unknown or duplicate IDs; it never fills,
filters, scores, or ranks the pool. Traces distinguish retrieved IDs, ASSESS
selection, LOOKUP reconstruction, and final grounded OUTPUT. CandidatePool remains
the unchanged business output. Lookup failures, like existing parsing/grounding
failures, abort without saving a completed search state.

### Runtime sufficiency Judge

After ASSESS and canonical lookup, one LLM Judge evaluates the shortlist's needs
coverage, contextual usage fit, priority alignment, soft budget relevance, and
useful diversity potential. This is runtime routing, not external evaluation.
SUFFICIENT proceeds to grounding. INSUFFICIENT supplies validated
WEAK_COVERAGE or LIMITED_OPTIONS gaps referencing original need priorities.

Exactly one gap-targeted Re-ASSESS may reconsider the full original tool outputs,
original requirements, initial selection, and Judge feedback. It returns IDs only;
Python preserves its choices/order through canonical lookup. No second retrieval
or Judge follows. The final reassessment status is used unchanged: SUCCESS means
it considers the selection sufficient; INSUFFICIENT means meaningful options with
unresolved limitations; NO_RESULTS means no meaningful selection; ERROR indicates
technical failure. Tool-level SUCCESS retains its existing retrieval meaning.
On the sufficient branch the initial selection status is preserved, including any
technical error; Judge sufficiency does not silently upgrade technical failures.

JUDGE, REASSESS, and REASSESS_LOOKUP stage details capture feedback, both sets of
notes, selected IDs, changes, timings, and the refinement trigger. No business
schema or external evaluation logic is added. Invalid structured output, invalid
gap references, and unknown IDs fail explicitly; a bounded technical/schema retry is applied to individual LLM operations. SearchWorkflow marks technical failures ERROR through RequestService.

The existing `OPENAI_MODEL` setting (default `gpt-4o-mini`) controls PLAN,
ASSESS, and Re-ASSESS. `JUDGE_MODEL` independently controls the runtime Judge
(default `gpt-5.6-terra`). Both use the existing OpenAI client configuration.
The configured model ID must be available through your API account/provider;
these defaults have not been live-verified. Tests can inject separate `llm` and
`judge_llm` clients without calling the API.

### Bounded technical retries

PLAN, ASSESS, JUDGE, and REASSESS each allow at most two attempts: the original
call plus one immediate retry with the same model and inputs. Production Search
and Judge clients set `max_retries=0` to disable underlying SDK retries. Custom
injected clients must likewise disable their own retries to retain this bound.

The explicit allow-list covers Pydantic/structured-output parsing failures,
invalid model-generated plan/Judge requirement references, OpenAI timeouts and
connection failures, temporary rate limits identified by structured codes/types
(`rate_limit_exceeded`, `rate_limit_error`, `requests`, `tokens`), and server
statuses 500, 502, 503, and 504. Credit/quota exhaustion codes are excluded even
when accompanied by a rate-limit type. Unknown HTTP 429s, authentication,
permission/model access, bad requests, and unlisted failures fail immediately.
Unknown selected catalog IDs still fail in canonical lookup, outside LLM retry.

A valid business result, including INSUFFICIENT, NO_RESULTS, or ERROR, never
triggers technical retry. Retrieval remains once and semantic Re-ASSESS remains
at most once. No additional Judge routing decision is introduced.

Each successful LLM stage records `llm_retry` with operation, attempts, retry
count/reason, sanitized failure categories, and final technical result. Terminal
failures re-raise the original exception, attach this metadata as `llm_retry`, and
log only the sanitized metadata. SearchWorkflow persists the ERROR lifecycle without saving a failed CandidatePool. No raw API error messages or credentials are copied into retry metadata.
## Solution Design catalog context

Solution Design works only from the CandidatePool that Search persisted. Each
candidate already carries its canonical SQLite capabilities and price, so no
separate retrieval step or vector index is used. SQLite remains authoritative for
IDs, prices, and structured catalog values.
