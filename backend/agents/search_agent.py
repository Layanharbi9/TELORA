"""LLM-powered Search Agent with native LangChain tool calling."""

from __future__ import annotations

import json
import os
from time import perf_counter
from typing import Any, Callable, Optional

from langchain_core.messages import HumanMessage, SystemMessage, ToolMessage
from langchain_openai import ChatOpenAI
from pydantic import ValidationError

from backend.schemas.requirements import Requirements
from backend.schemas.search import AddonCandidate, AddonSearchInput, AssessmentSelection, CandidatePool, PackageCandidate, PackageSearchInput, SearchJudgeResult, SearchNeed, SearchPlan, SearchStatus, SearchTask, search_needs_from_requirements
from backend.schemas.state import StageTrace, SearchTrace, TELORAState, ToolTrace
from backend.services.llm_retry import InvalidStructuredOutput, run_llm_operation
from backend.tools.addon_search_tool import addon_search
from backend.tools.search_tool import NEED_RELEVANT_FIELDS, package_search


SEARCH_AGENT_SYSTEM_PROMPT = """
You are TELORA's Search Agent. Plan retrieval, retrieve factual catalog candidates,
and assess and shortlist them. Original user requirements are authoritative.
NEVER invent or modify catalog facts. Only tool-returned records may appear in
CandidatePool. Tools retrieve broad factual results from SQLite; a capability
match does not by itself establish suitability.
Candidate shortlisting is required, but final solution selection is prohibited.
Do not design solutions, combine packages and add-ons into solutions, choose a
final solution for the customer, calculate final totals, or make final eligibility
or compatibility decisions. Whether a package plus up to two add-ons together can
serve every requested need is Solution Design's responsibility, not yours -- do
not require one package to cover every need before shortlisting it. Do not provide
private chain-of-thought.
"""

PLAN_PROMPT = """
PLAN + RETRIEVE: decide which Search Tools to call -- package_search, addon_search,
or both -- and call them yourself using the supplied tool-calling mechanism. Do not
describe a plan in prose or in the tool-call arguments; actually call the tools.

For each tool call, choose the original needs relevant to that tool and pass them
exactly as given: original need name, level, and priority. Do not invent, rename,
relevel, or renumber a need, and do not alter preferred_budget_sar. Cover every
requested need across your tool calls. Call each tool at most once.

Once you have called every tool you need and are finished retrieving, stop making
tool calls. Your final message (with no further tool calls) must contain only
assessment_focus: a concise task specification for comparing candidates against
these requirements, not hidden reasoning or a candidate claim. Do not invent
thresholds, scores, or numeric weights.
"""

FINAL_CANDIDATE_POOL_PROMPT = """
ASSESS + SHORTLIST: return AssessmentSelection directly. Compare retrieved candidates
against each other using actual capabilities and Need relevance, not merely the
number of matched needs. Follow original requirements over conflicting plan prose.

Interpret three distinct dimensions together:
- Priority is importance between needs: Primary > Secondary > Third.
- Level is usage intensity within each need. Interpret Low / Medium / High
  contextually, without hardcoded usage thresholds, scores, or numeric weights.
- Preferred Budget is a meaningful soft price preference, not a hard cutoff.

For example, with Primary Data / Medium, Secondary Streaming / High, and a
220 SAR preferred budget, Data has higher decision priority. Medium Data does not
mean more data is always better. Streaming is secondary, but High usage means
strong streaming support can materially distinguish otherwise suitable candidates.
The 220 SAR preference should meaningfully influence ranking, while stronger
above-budget alternatives may remain when catalog facts justify their relevant
advantage. This example is illustrative; use the actual supplied requirements.

Perform comparative shortlisting, not broad relevance filtering. Return the
smallest useful set of strong, meaningfully distinct alternatives. Retain a
candidate when it has strong, grounded relevance to one or more requested needs
OR represents a distinct, material tradeoff not already represented by a
stronger retained candidate for the need(s) it addresses. A candidate does not
need to serve every requested need by itself to be worth retaining -- combining
a package with add-ons to cover the full need set is Solution Design's task,
not yours. Capability presence alone is not sufficient for inclusion. Apply
redundancy removal even among candidates with strong relevance.

When candidates expose equivalent relevant capabilities, prefer the one that
better satisfies the requirements and budget. Remove a more expensive equivalent
when available catalog facts show no relevant advantage. Do not invent differences
to justify keeping a candidate. Do not assume more allowance is always better,
higher price means stronger suitability, every Primary-need match should survive,
or every plausible match is a meaningful alternative.

Above-budget candidates may remain only when available facts show a material,
requirement-relevant advantage not adequately represented by a closer-budget
alternative. Substantially above-budget candidates require especially clear
factual justification. Do not convert the budget preference into a hard cutoff.

Stop adding candidates when another candidate would repeat an already represented
benefit, provide no meaningful requirement-relevant advantage, or represent a
weaker version of an existing tradeoff. Do not preserve tool-result ordering by
default. Rank retained packages and add-ons independently by contextual suitability.
Preserve meaningful alternatives where genuine tradeoffs exist. Do not force a
fixed shortlist count such as Top 3, Top 5, or Top 10; the objective is the smallest
useful set of strong, meaningfully distinct alternatives.

Return only ordered package_ids, ordered addon_ids, search_status, and
search_notes. Copy selected IDs exactly from tool results. Do not reproduce
candidate objects, names, prices, capabilities, matched needs, or reasons.
Use search_notes for concise shortlist-level explanations of major tradeoffs,
important uncertainty in available catalog facts, and why any substantially
above-budget alternative was retained. Do not provide private chain-of-thought.
Preserve tool failures and no-result states in search_status and search_notes.
Operational metadata is recorded separately in traces. search_status describes
this shortlist's own quality only, for observability -- it does not itself
decide whether Solution Design runs next; do not withhold a genuinely useful
candidate merely to justify a particular search_status.

Shortlisting is not final solution selection. Do not design final solutions,
combine packages and add-ons into solutions, choose a final customer solution,
calculate final solution totals, or make final eligibility or compatibility
decisions.
"""


JUDGE_PROMPT = """
You are the runtime sufficiency Judge within TELORA's Search Agent, not external
quality evaluation. Your question is whether Search retrieved a sufficiently
useful and relevant candidate set for Solution Design to work with next -- NOT
whether a complete final solution can already be proven from the shortlist
alone. Return SearchJudgeResult. Evaluate the canonical shortlist against
original authoritative requirements: meaningful needs coverage, contextual
Low/Medium/High usage fit, Primary > Secondary > Third importance, soft budget
relevance, useful diversity potential for later solution design, and whether
retained candidates remain meaningfully distinct rather than redundant.
Judge coverage across the WHOLE shortlist -- packages and add-ons together --
never require any single package to cover every requested need by itself; a
need represented only by an add-on still counts as represented.
WEAK_COVERAGE means no retained candidate (package or add-on) represents a
need, level, or priority anywhere in the shortlist.
LIMITED_OPTIONS means insufficient meaningful alternatives, never a minimum count.
EXCESSIVE_REDUNDANCY means the shortlist still contains multiple candidates that
do not offer a materially different tradeoff for these requirements, obscuring
the genuinely distinct alternatives; it concerns undifferentiated bulk, not a
large candidate count by itself.
Reference original need_priority only. SUFFICIENT requires no gaps; INSUFFICIENT
requires at least one gap. Give concise factual outcome reasons and a summary,
not private chain-of-thought. Do not redefine requirements, select replacements,
score, assign weights, impose usage thresholds, or design/combine final solutions.
Your verdict decides only whether Re-ASSESS is worth attempting; it does not
decide whether Solution Design runs.
"""

REASSESS_PROMPT = """
This is the single allowed gap-targeted Re-ASSESS, over the FULL original retrieved
pool. Reconsider the initial selection against the Judge gaps and original
requirements. You may retain, remove, replace, add original retrieved IDs, or reorder
them to improve fit and useful diversity. Do not fill a quota. Return the same
IDs-only AssessmentSelection. No more retrieval or Judge calls will follow.
Use SUCCESS if this selection is sufficient; INSUFFICIENT if meaningful candidates
remain but coverage/useful-choice limitations persist; NO_RESULTS if no meaningful
selection remains; ERROR for technical failure. This status describes shortlist
quality only, for observability -- it does not itself stop the workflow.
Solution Design still runs whenever your final selection includes at least one
package, regardless of this status. Preserve technical failure context.
Use concise search_notes explaining remaining tradeoffs, changes, and uncertainties,
not private chain-of-thought. Original requirements remain authoritative.
"""


SEARCH_TOOLS = [package_search, addon_search]
SEARCH_TOOL_MAP = {tool.name: tool for tool in SEARCH_TOOLS}
# Ground truth for validating native tool-call arguments; independent of whatever
# object a test may have substituted into SEARCH_TOOL_MAP for isolation.
SEARCH_TOOL_INPUT_SCHEMAS = {"package_search": PackageSearchInput, "addon_search": AddonSearchInput}


def build_search_context(requirements: Requirements) -> dict[str, Any]:
    """Build structured context for the LLM without choosing catalog candidates."""
    return {
        "user_requirements": requirements.to_dict(),
        "search_needs": [need.model_dump(mode="json") for need in search_needs_from_requirements(requirements)],
    }


def create_search_llm(model_name: Optional[str] = None) -> ChatOpenAI:
    """Create the configured chat model used by the Search Agent."""
    return ChatOpenAI(
        model=model_name or os.getenv("OPENAI_MODEL", "gpt-4o-mini"),
        temperature=0,
        max_retries=0,
    )


def create_judge_llm(model_name: Optional[str] = None) -> ChatOpenAI:
    """Create the separately configured runtime Judge client."""
    return ChatOpenAI(
        model=model_name or os.getenv("JUDGE_MODEL", "gpt-5.6-terra"),
        temperature=0,
        max_retries=0,
        reasoning_effort="none",
    )


def _ground_candidate_pool(pool: CandidatePool, tool_outputs: dict[str, dict[str, Any]]) -> CandidatePool:
    package_records = {
        candidate["package_id"]: candidate
        for candidate in tool_outputs.get("package_search", {}).get("candidates", [])
    }
    addon_records = {
        candidate["addon_id"]: candidate
        for candidate in tool_outputs.get("addon_search", {}).get("candidates", [])
    }
    grounded_packages = []
    for candidate in pool.package_candidates:
        record = package_records.get(candidate.package_id)
        if record is None:
            raise ValueError(f"Ungrounded or modified package candidate: {candidate.package_id}")
        grounded_packages.append(PackageCandidate.model_validate(record))
    grounded_addons = []
    for candidate in pool.addon_candidates:
        record = addon_records.get(candidate.addon_id)
        if record is None:
            raise ValueError(f"Ungrounded or modified add-on candidate: {candidate.addon_id}")
        grounded_addons.append(AddonCandidate.model_validate(record))
    return pool.model_copy(
        update={
            "package_candidates": grounded_packages,
            "addon_candidates": grounded_addons,
        }
    )


def plan_and_retrieve(
    requirements: Requirements, model: ChatOpenAI, max_tool_rounds: int,
) -> tuple[SearchPlan, dict[str, dict[str, Any]], list[ToolTrace]]:
    """Let the model natively decide and call Search Tools; Python only grounds and executes.

    The same LLM reasons over the original requirements and calls package_search
    and/or addon_search itself via native tool calling (bind_tools). Every tool
    call's needs/budget are validated against the original requirements before
    execution -- invented needs, altered levels/priorities, or an altered budget
    raise InvalidStructuredOutput rather than being silently repaired. Rounds are
    bounded by max_tool_rounds. SearchPlan is reconstructed afterward from the
    calls actually made, purely for tracing and downstream ASSESS context.
    """
    canonical_needs = search_needs_from_requirements(requirements)
    canonical_keys = {(need.need, need.level, need.priority) for need in canonical_needs}
    context = build_search_context(requirements)
    context["available_tools"] = [
        {"tool_name": tool.name, "description": tool.description} for tool in SEARCH_TOOLS
    ]
    messages: list[Any] = [
        SystemMessage(content=SEARCH_AGENT_SYSTEM_PROMPT),
        SystemMessage(content=PLAN_PROMPT),
        HumanMessage(content=json.dumps(context)),
    ]
    model_with_tools = model.bind_tools(SEARCH_TOOLS)

    outputs: dict[str, dict[str, Any]] = {}
    traces: list[ToolTrace] = []
    tasks: list[SearchTask] = []
    used_tool_names: set[str] = set()

    for _ in range(max_tool_rounds):
        response = model_with_tools.invoke(messages)
        messages.append(response)
        tool_calls = response.tool_calls or []
        if not tool_calls:
            plan = SearchPlan(tasks=tasks, assessment_focus=(response.content or "").strip())
            try:
                plan.validate_requirements(requirements)
            except ValueError as error:
                raise InvalidStructuredOutput("Invalid plan requirement references") from error
            return plan, outputs, traces

        for call in tool_calls:
            name, args, call_id = call["name"], call["args"], call["id"]
            input_schema = SEARCH_TOOL_INPUT_SCHEMAS.get(name)
            if input_schema is None or name not in SEARCH_TOOL_MAP:
                raise InvalidStructuredOutput(f"Unknown tool requested: {name}")
            if name in used_tool_names:
                raise InvalidStructuredOutput(f"Tool {name} was already called; each tool may be used at most once")
            try:
                validated = input_schema.model_validate(args)
            except ValidationError as error:
                raise InvalidStructuredOutput(f"Invalid arguments for {name}") from error
            if len({need.priority for need in validated.needs}) != len(validated.needs):
                raise InvalidStructuredOutput(f"Tool call for {name} references duplicate need priorities")
            unknown_needs = [need for need in validated.needs
                             if (need.need, need.level, need.priority) not in canonical_keys]
            if unknown_needs:
                raise InvalidStructuredOutput(f"Tool call for {name} referenced invented or altered needs: {unknown_needs}")
            if float(validated.preferred_budget_sar) != float(requirements.preferred_budget_sar):
                raise InvalidStructuredOutput(f"Tool call for {name} altered preferred_budget_sar")

            used_tool_names.add(name)
            args_dict = validated.model_dump(mode="json")
            started = perf_counter()
            result = SEARCH_TOOL_MAP[name].invoke(args_dict)
            elapsed = round((perf_counter() - started) * 1000, 3)
            output = result.model_dump(mode="json")
            outputs[name] = output
            traces.append(ToolTrace(
                tool_name=name, input=args_dict, output=output,
                status=output["status"], execution_time_ms=elapsed, task_index=len(traces) + 1,
            ))
            tasks.append(SearchTask(tool_name=name, need_priorities=sorted({need.priority for need in validated.needs})))
            messages.append(ToolMessage(content=json.dumps(output), tool_call_id=call_id, name=name))

    raise InvalidStructuredOutput("Exceeded max_tool_rounds without completing retrieval")


def assess_candidates(requirements: Requirements, plan: SearchPlan, outputs: dict, model: ChatOpenAI, *,
                      initial_selection: Optional[AssessmentSelection] = None,
                      judge_result: Optional[SearchJudgeResult] = None) -> AssessmentSelection:
    context = build_search_context(requirements)
    context.update(search_plan=plan.model_dump(mode="json"), tool_outputs=outputs)
    if (initial_selection is None) != (judge_result is None):
        raise ValueError("Re-ASSESS requires both initial selection and Judge result")
    refinement_messages = []
    if judge_result is not None:
        judge_result.validate_requirements(requirements)
        context.update(initial_selection=initial_selection.model_dump(mode="json"),
                       judge_result=judge_result.model_dump(mode="json"))
        refinement_messages = [SystemMessage(content=REASSESS_PROMPT)]
    selection = model.with_structured_output(AssessmentSelection, method="function_calling").invoke([
        SystemMessage(content=SEARCH_AGENT_SYSTEM_PROMPT.replace("CandidatePool", "AssessmentSelection")),
        SystemMessage(content=FINAL_CANDIDATE_POOL_PROMPT),
        *refinement_messages,
        HumanMessage(content=json.dumps(context)),
    ])

    return AssessmentSelection.model_validate(selection)


def judge_shortlist(requirements: Requirements, pool: CandidatePool, model: ChatOpenAI) -> SearchJudgeResult:
    context = build_search_context(requirements)
    context["shortlist"] = pool.model_dump(mode="json")
    result = model.with_structured_output(SearchJudgeResult, method="function_calling").invoke([
        SystemMessage(content=JUDGE_PROMPT), HumanMessage(content=json.dumps(context)),
    ])
    result = SearchJudgeResult.model_validate(result)
    try:
        result.validate_requirements(requirements)
    except ValueError as error:
        raise InvalidStructuredOutput("Invalid Judge requirement references") from error
    return result


def resolve_assessment_selection(selection: AssessmentSelection, outputs: dict) -> CandidatePool:
    """Resolve only selected IDs, in their original order; never choose candidates."""
    def resolve(ids: list[str], tool_name: str, id_field: str) -> list[dict]:
        if len(ids) != len(set(ids)):
            raise ValueError(f"Duplicate selected {id_field} values")
        records = {record[id_field]: record for record in outputs.get(tool_name, {}).get("candidates", [])}
        unknown = [selected_id for selected_id in ids if selected_id not in records]
        if unknown:
            # A schema-valid LLM response can still put a real add-on ID in the
            # package list (or vice versa). Treat that as invalid structured
            # output so the existing bounded technical retry can correct the
            # assessment against the same retrieved candidates.
            raise InvalidStructuredOutput(f"Unknown selected {id_field} values: {unknown}")
        return [records[selected_id] for selected_id in ids]

    return CandidatePool(
        package_candidates=resolve(selection.package_ids, "package_search", "package_id"),
        addon_candidates=resolve(selection.addon_ids, "addon_search", "addon_id"),
        search_status=selection.search_status, search_notes=selection.search_notes,
    )


def _numeric(value: Any) -> Optional[float]:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _relevant_fields(needs: list[SearchNeed]) -> tuple[str, ...]:
    """Union of capability fields tied to the supplied needs, in first-seen order."""
    fields: list[str] = []
    for need in needs:
        for field in NEED_RELEVANT_FIELDS.get(need.need, ()):
            if field not in fields:
                fields.append(field)
    return tuple(fields)


def _at_least_as_good(candidate: dict[str, Any], other: dict[str, Any], relevant_fields: tuple[str, ...]) -> bool:
    """True if `candidate` is no worse than `other` on every relevant field and no pricier."""
    capabilities, other_capabilities = candidate.get("capabilities", {}), other.get("capabilities", {})
    for field in relevant_fields:
        value, other_value = capabilities.get(field), other_capabilities.get(field)
        numeric, other_numeric = _numeric(value), _numeric(other_value)
        if numeric is not None and other_numeric is not None:
            if numeric < other_numeric:
                return False
        elif value != other_value:
            return False
    return candidate.get("price_sar", 0) <= other.get("price_sar", 0)


def reduce_dominated_candidates(candidates: list[dict[str, Any]], relevant_fields: tuple[str, ...]) -> list[dict[str, Any]]:
    """Deterministically drop candidates strictly Pareto-dominated by another.

    A candidate is removed only when another candidate matches or exceeds it on
    every capability field relevant to the requested needs at an equal-or-lower
    price, with a strict improvement on at least one of those. Exact ties never
    remove either side, so this never drops the last representative of a
    genuine option and never invents a preference between equally-good
    alternatives. Order and all other candidate fields are preserved.
    """
    if len(candidates) < 2 or not relevant_fields:
        return list(candidates)
    survivors = []
    for candidate in candidates:
        dominated = any(
            other is not candidate
            and _at_least_as_good(other, candidate, relevant_fields)
            and not _at_least_as_good(candidate, other, relevant_fields)
            for other in candidates
        )
        if not dominated:
            survivors.append(candidate)
    return survivors


def run_search_agent(
    state: TELORAState,
    llm: Optional[ChatOpenAI] = None,
    max_tool_rounds: int = 6,
    judge_llm: Optional[ChatOpenAI] = None,
    on_phase: Optional[Callable[[str], None]] = None,
) -> TELORAState:
    """Let the LLM natively call Search Tools once, assess, and return a grounded pool.

    max_tool_rounds bounds the native tool-calling loop the model drives during
    PLAN: the model decides which tool(s) to call and with which original needs,
    Python grounds and executes each call, and results return as ToolMessages to
    the same conversation until the model stops calling tools (or the bound is hit).
    """
    model = llm or create_search_llm()
    stages: list[StageTrace] = []
    started = perf_counter()
    (plan, tool_outputs, tool_traces), retry = run_llm_operation(
        "PLAN", lambda: plan_and_retrieve(state.user_requirements, model, max_tool_rounds))
    stages.append(StageTrace("PLAN", "SUCCESS", round((perf_counter() - started) * 1000, 3), {"llm_retry": retry}))

    started = perf_counter()
    retrieval_status = "ERROR" if any(trace.status == "ERROR" for trace in tool_traces) else "SUCCESS"
    stages.append(StageTrace("RETRIEVE", retrieval_status, round((perf_counter() - started) * 1000, 3), {
        "package_ids": [record["package_id"] for record in tool_outputs.get("package_search", {}).get("candidates", [])],
        "addon_ids": [record["addon_id"] for record in tool_outputs.get("addon_search", {}).get("candidates", [])],
    }))

    started = perf_counter()
    task_needs = {task.tool_name: [need for need in search_needs_from_requirements(state.user_requirements)
                                   if need.priority in task.need_priorities] for task in plan.tasks}
    package_candidates = tool_outputs.get("package_search", {}).get("candidates", [])
    addon_candidates = tool_outputs.get("addon_search", {}).get("candidates", [])
    reduced_packages = reduce_dominated_candidates(package_candidates, _relevant_fields(task_needs.get("package_search", [])))
    reduced_addons = reduce_dominated_candidates(addon_candidates, _relevant_fields(task_needs.get("addon_search", [])))
    # New dicts only; never mutate tool_outputs, which the RETRIEVE ToolTrace already references.
    reduced_outputs = {name: {**output, "candidates": reduced} for name, output, reduced in (
        ("package_search", tool_outputs.get("package_search"), reduced_packages),
        ("addon_search", tool_outputs.get("addon_search"), reduced_addons),
    ) if output is not None}
    reduced_package_ids = {record["package_id"] for record in reduced_packages}
    reduced_addon_ids = {record["addon_id"] for record in reduced_addons}
    stages.append(StageTrace("REDUCE", "SUCCESS", round((perf_counter() - started) * 1000, 3), {
        "package_ids": [record["package_id"] for record in reduced_packages],
        "addon_ids": [record["addon_id"] for record in reduced_addons],
        "package_removed_ids": [record["package_id"] for record in package_candidates
                                if record["package_id"] not in reduced_package_ids],
        "addon_removed_ids": [record["addon_id"] for record in addon_candidates
                              if record["addon_id"] not in reduced_addon_ids],
    }))

    def assess_and_resolve(*, initial_selection=None, judge_result=None):
        selection = assess_candidates(
            state.user_requirements, plan, reduced_outputs, model,
            initial_selection=initial_selection, judge_result=judge_result,
        )
        if not selection.package_ids and reduced_outputs.get("package_search", {}).get("candidates", []):
            # Retrieval found packages; a selection that drops every one of them
            # is not genuine NO_RESULTS -- it's invalid ASSESS/REASSESS output,
            # eligible for the same bounded technical retry as any other.
            raise InvalidStructuredOutput(
                "Assessment selected zero packages despite retrieved package candidates")
        return selection, resolve_assessment_selection(selection, reduced_outputs)

    # Real FINDING -> CHECKING boundary: retrieval and the deterministic reduce
    # are complete; assessment/Judge/reassess/grounding/routing start now.
    # Observability only -- the callback cannot influence any Search decision.
    if on_phase is not None:
        on_phase("CHECKING")

    started = perf_counter()
    (selection, pool), retry = run_llm_operation(
        "ASSESS", lambda: assess_and_resolve())
    stages.append(StageTrace("ASSESS", "SUCCESS", round((perf_counter() - started) * 1000, 3), {**selection.model_dump(mode="json"), "llm_retry": retry}))

    started = perf_counter()
    stages.append(StageTrace("LOOKUP", "SUCCESS", round((perf_counter() - started) * 1000, 3), {
        "package_ids": [candidate.package_id for candidate in pool.package_candidates],
        "addon_ids": [candidate.addon_id for candidate in pool.addon_candidates],
        "search_status": pool.search_status.value, "search_notes": pool.search_notes,
    }))

    started = perf_counter()
    judge_model = judge_llm if judge_llm is not None else create_judge_llm()
    judgment, retry = run_llm_operation("JUDGE", lambda: judge_shortlist(state.user_requirements, pool, judge_model))
    refine = judgment.verdict == "INSUFFICIENT"
    stages.append(StageTrace("JUDGE", "SUCCESS", round((perf_counter() - started) * 1000, 3), {
        **judgment.model_dump(mode="json"), "refinement_triggered": refine, "llm_retry": retry,
    }))
    if refine:
        initial_selection = selection
        started = perf_counter()
        (selection, pool), retry = run_llm_operation(
            "REASSESS", lambda: assess_and_resolve(
                initial_selection=initial_selection, judge_result=judgment))
        stages.append(StageTrace("REASSESS", "SUCCESS", round((perf_counter() - started) * 1000, 3), {
            **selection.model_dump(mode="json"), "llm_retry": retry,
            "changes": {
                kind: {"before": getattr(initial_selection, kind), "after": getattr(selection, kind),
                       "added": [key for key in getattr(selection, kind) if key not in getattr(initial_selection, kind)],
                       "removed": [key for key in getattr(initial_selection, kind) if key not in getattr(selection, kind)]}
                for kind in ("package_ids", "addon_ids")
            },
        }))
        started = perf_counter()
        stages.append(StageTrace("REASSESS_LOOKUP", "SUCCESS", round((perf_counter() - started) * 1000, 3), selection.model_dump(mode="json")))

    started = perf_counter()
    pool = _ground_candidate_pool(pool, reduced_outputs)
    stages.append(StageTrace("OUTPUT", "SUCCESS", round((perf_counter() - started) * 1000, 3), {
        "grounding": "SUCCESS", "search_status": pool.search_status.value,
        "package_ids": [candidate.package_id for candidate in pool.package_candidates],
        "addon_ids": [candidate.addon_id for candidate in pool.addon_candidates],
    }))

    # Deterministic workflow routing (approved policy): computed only from
    # observable Search facts -- a retrieval tool error, or the final grounded
    # package count -- never from the LLM's own assessment_status, which is
    # preserved for observability but stays advisory and never controls
    # routing. This is the only place search_status is decided for the pool
    # that leaves this function.
    started = perf_counter()
    assessment_status = selection.search_status
    package_count = len(pool.package_candidates)
    addon_count = len(pool.addon_candidates)
    tool_error = any(trace.status == "ERROR" for trace in tool_traces)
    if tool_error:
        routing_status, routing_reason = SearchStatus.ERROR, "TOOL_ERROR"
    elif package_count == 0:
        routing_status, routing_reason = SearchStatus.NO_RESULTS, "NO_PACKAGES_RETRIEVED"
    else:
        routing_status, routing_reason = SearchStatus.SUCCESS, "PACKAGES_AVAILABLE"
    covered_needs = {item.need for candidate in (*pool.package_candidates, *pool.addon_candidates)
                     for item in candidate.matched_needs}
    uncovered_needs = [need.need for need in search_needs_from_requirements(state.user_requirements)
                       if need.need not in covered_needs]
    pool = pool.model_copy(update={"search_status": routing_status, "assessment_status": assessment_status})
    stages.append(StageTrace("ROUTE", "SUCCESS", round((perf_counter() - started) * 1000, 3), {
        "routing_status": routing_status.value, "routing_reason": routing_reason,
        "assessment_status": assessment_status.value, "package_count": package_count,
        "addon_count": addon_count, "uncovered_needs": uncovered_needs,
    }))

    trace = SearchTrace(agent_name="llm_search_agent", tool_traces=tool_traces, search_plan=plan, stages=stages)
    result = TELORAState(
        request_id=state.request_id, user_requirements=state.user_requirements,
        candidate_pool=pool, search_trace=trace,
    )
    return result

