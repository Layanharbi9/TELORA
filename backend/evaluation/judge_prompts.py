"""Written grading criteria for TELORA's LLM-as-a-judge evaluators.

Each semantic metric has one plain-language question, a definition for every
allowed score (1.0, 0.5, 0.0) with a TELORA catalog example, and the rules the
judge must follow. The judge also receives the evidence as readable text built
from the run: the customer's needs and budget, then the items or solutions the
agent produced with their catalog facts. Deterministic metrics stay in
rubrics.py and never reach an LLM.

Prompts are keyed by (scope, metric):
  search   -> search_agent_eval   (Search Agent shortlist)
  solution -> solution_design_eval (Solution Design output)
  final    -> end_to_end_eval     (solutions presented in a full run)
"""

from __future__ import annotations

from typing import Any

JUDGE_SYSTEM = (
    "You are a strict evaluator for TELORA, a telecom assistant that builds mobile plans from a catalog "
    "of packages and add-ons. Grade exactly one metric using only the written criteria and the evidence "
    "provided. Never assume a feature, price or allowance that is not listed in the evidence. "
    "Choose the single score whose definition fits best: 1.0, 0.5 or 0.0. "
    "When two definitions seem to fit, choose the lower score. "
    "Return the score and one short sentence that names the specific item, solution or statement that "
    "decided it. Do not explain your reasoning beyond that sentence."
)

_RELEVANCE_LEVEL_NOTE = (
    "Levels mean usage intensity: Low = occasional, Medium = regular, High = heavy. "
    "A capability that exists but is clearly too small for a High need counts as weak, not strong."
)

JUDGE_PROMPTS: dict[tuple[str, str], dict[str, Any]] = {
    # ------------------------------------------------------------------ Search Agent
    ("search", "relevance"): {
        "title": "Relevance of the shortlist",
        "question": "Does every shortlisted item genuinely help with at least one of the customer's requested "
                    "needs, at the level the customer asked for?",
        "levels": {
            "1.0": ("Every item clearly serves at least one requested need with a capability that matters at "
                    "the requested level.",
                    "Customer asks Data (High). Shortlist has Orbit Youth 80 (data_gb=80) and "
                    "Volt Youth 50 (data_gb=50)."),
            "0.5": ("Every item touches a requested need, but at least one serves it only weakly for the "
                    "requested level.",
                    "Customer asks Data (High). Shortlist also includes Nexus Lite 5 (data_gb=5)."),
            "0.0": ("At least one item serves none of the requested needs.",
                    "Customer asks Gaming and Data. Shortlist includes Roam World 5GB, a roaming add-on."),
        },
        "rules": [
            "Judge each item by its listed capabilities, not by its name.",
            "The 'serves' list is the search tool's claim. Check it against the capabilities.",
            "Budget is not part of relevance. Do not lower the score for price.",
            _RELEVANCE_LEVEL_NOTE,
        ],
    },
    ("search", "diversity"): {
        "title": "Diversity of the shortlist",
        "question": "Does the shortlist give Solution Design real, different options instead of near-copies "
                    "of the same item?",
        "levels": {
            "1.0": ("Items differ on something that matters for this request (allowance, price tier, or which "
                    "need they serve) and no item is a strictly worse copy of another.",
                    "Packages at 15, 30 and 50 GB with rising prices, plus one add-on for each secondary need."),
            "0.5": ("Mostly distinct, but one or two items are clearly redundant: same relevant allowances as "
                    "another item at a higher price.",
                    "Two packages both with data_gb=50 and Streaming Data, one costing 40 SAR more with no "
                    "extra relevant capability."),
            "0.0": ("The shortlist is essentially one option repeated, or it cannot support a choice at all "
                    "for a multi-need request.",
                    "Three needs requested, shortlist has a single package and no add-ons although add-ons "
                    "for the other needs are listed as retrieved."),
        },
        "rules": [
            "Do not reward size. A short list of clearly different items scores 1.0.",
            "Compare items only on capabilities that relate to the requested needs.",
        ],
    },
    ("search", "groundedness"): {
        "title": "Groundedness of the agent's notes",
        "question": "Is every factual statement in the Search Agent's notes supported by the listed item facts?",
        "levels": {
            "1.0": ("Every fact in the notes (allowance, benefit, price, which item is cheapest or largest) "
                    "matches the listed data.",
                    "Note says 'TEL-P001 is the most economical option' and TEL-P001 has the lowest listed price."),
            "0.5": ("No statement contradicts the data, but at least one is vague or cannot be checked from it.",
                    "Note says 'offers the best value' without any price or allowance comparison."),
            "0.0": ("At least one statement contradicts the data or claims something the data does not show.",
                    "Note says 'Orbit Youth 15 includes unlimited social data' but social_data_gb=15."),
        },
        "rules": [
            "If there are no notes, return 1.0 with the sentence 'No notes to check.'",
            "Item IDs in notes must refer to items that are in the shortlist or the listed retrieval.",
        ],
    },
    ("search", "runtime_judge_correctness"): {
        "title": "Correctness of the in-workflow Judge",
        "question": "Is the Search Agent's own Judge verdict (SUFFICIENT or INSUFFICIENT) and its list of gaps "
                    "justified by the shortlist?",
        "levels": {
            "1.0": ("SUFFICIENT when every requested need has at least one reasonable item, or INSUFFICIENT "
                    "with gaps that each name a real requested need that truly lacks a good option.",
                    "Verdict INSUFFICIENT, gap: priority 3 Data Sharing has no item; indeed no shortlisted "
                    "item has data_sharing=Yes."),
            "0.5": ("The verdict is defensible, but a gap reason is vague or uses the wrong gap type.",
                    "Gap is labelled LIMITED_OPTIONS when the need actually has no option at all."),
            "0.0": ("The verdict contradicts the shortlist.",
                    "Verdict SUFFICIENT while no item serves Roaming, a requested need; or a gap names a need "
                    "that three items serve well."),
        },
        "rules": [
            "If the Judge step did not run, return 1.0 with the sentence 'Not applicable: Judge did not run.'",
            "Gap priorities refer to the customer's needs: 1 is the first need, 3 is the third.",
        ],
    },
    # ------------------------------------------------------------------ Solution Design Agent
    ("solution", "requirements_alignment"): {
        "title": "Alignment with the customer's requirements",
        "question": "Does each solution address the customer's actual needs, in their priority order, without "
                    "adding things nobody asked for?",
        "levels": {
            "1.0": ("Every solution serves the priority 1 need well and addresses priorities 2 and 3; every "
                    "add-on relates to a requested need.",
                    "Needs: Data (High), Streaming (Medium). Solution: Volt Youth 50 (50 GB, Streaming Data)."),
            "0.5": ("Every solution serves priority 1, but at least one neglects a lower-priority need or "
                    "includes an add-on unrelated to any requested need.",
                    "Needs: Data, Social Media. A solution adds Extra SIM although Multi-device was not requested."),
            "0.0": ("A solution fails the priority 1 need, or is built around a need the customer did not ask for.",
                    "Priority 1 is International Calls and the solution has international_minutes=0 everywhere."),
        },
        "rules": [
            "Priority 1 matters most. A miss on priority 1 is always 0.0.",
            _RELEVANCE_LEVEL_NOTE,
            "Budget is graded elsewhere. Do not lower this score for price.",
        ],
    },
    ("solution", "needs_coverage"): {
        "title": "Truthfulness of the coverage labels",
        "question": "Are the COVERED and NOT_COVERED labels on each solution true, given the capabilities of "
                    "its package and add-ons?",
        "levels": {
            "1.0": ("Every COVERED label is backed by a real capability that makes sense at the requested level, "
                    "and every NOT_COVERED label is correct.",
                    "Roaming (Medium) marked COVERED; the solution includes Roam World 5GB (roaming_data_gb=5)."),
            "0.5": ("Labels are factually true, but at least one COVERED need is served only weakly for its "
                    "level and the evidence text does not say so.",
                    "Streaming (High) marked COVERED only by 'Streaming Data' on a package with data_gb=15."),
            "0.0": ("A COVERED label has no supporting capability, or a requested need is missing from the "
                    "coverage list.",
                    "Roaming marked COVERED while every component has roaming_data_gb=0."),
        },
        "rules": [
            "Check each label against the component capabilities listed under the solution.",
            _RELEVANCE_LEVEL_NOTE,
        ],
    },
    ("solution", "groundedness"): {
        "title": "Groundedness of explanations",
        "question": "Is every factual statement in the recommendation reason, trade-off and coverage evidence "
                    "supported by the listed data?",
        "levels": {
            "1.0": "All statements match the listed prices, allowances, benefits and number of solutions.",
            "0.5": ("Nothing contradicts the data, but at least one statement is vague or promotional and "
                    "cannot be checked.",
                    "Reason says the plan 'aligns perfectly' without naming what it covers."),
            "0.0": ("At least one statement contradicts the data.",
                    "Reason says 'there are no other solutions' while two solutions are listed, or quotes a "
                    "price that differs from the total."),
        },
        "rules": [
            "Compare numbers exactly: prices in SAR and allowances in GB or minutes.",
            "A claim about other solutions must match the solutions listed.",
        ],
    },
    ("solution", "solution_quality"): {
        "title": "Quality of the solution set and recommendation",
        "question": "Are the presented solutions a useful, honest set of choices, and does the recommendation "
                    "reason help the customer decide?",
        "levels": {
            "1.0": ("Solutions differ in a way the customer cares about (price against allowance, or an extra "
                    "need served), trade-offs name that difference, and the reason is specific, addressed to "
                    "the customer as 'you', and free of internal labels. With one solution, the reason explains "
                    "why it fits.",
                    "Reason: 'You get 50 GB for 170 SAR, 30 SAR under your budget, and streaming is included.'"),
            "0.5": ("A useful set with one clear weakness: two solutions are nearly identical, a trade-off is "
                    "generic, or the reason uses internal wording.",
                    "Reason starts with 'Strategy 1 effectively covers...' or mentions 'strategy_2'."),
            "0.0": ("Solutions duplicate each other without saying so, or the recommendation reason is "
                    "misleading or contradicts the facts.",
                    "The recommended solution is 90 SAR over budget and the reason says it is the cheapest."),
        },
        "rules": [
            "Internal labels include strategy IDs, policy names and words like 'Strategy 1'.",
            "Only the recommended solution is expected to have a reason.",
        ],
    },
    # ------------------------------------------------------------------ end-to-end run
    ("final", "diversity"): {
        "title": "Choice for the customer",
        "question": "Do the solutions presented to the customer give them real alternatives?",
        "levels": {
            "1.0": ("Two or more solutions that differ on price, allowance or which need they serve; or a "
                    "single solution whose explanation states why no alternative was offered.",
                    "Orbit Youth 15 + Youth Social Extra at 118.5 SAR against Orbit Youth 15 alone at 103.5 SAR."),
            "0.5": ("Two or more solutions that are nearly identical, or a single solution with no word on why.",
                    "Two solutions with the same package and add-ons in a different order."),
            "0.0": "Solutions are presented as different but are the same composition and price.",
        },
        "rules": ["Compare compositions by package and add-on IDs, then by price."],
    },
}
# The end-to-end run grades the solutions it presented with the Solution Design criteria.
for _metric, _source in (("relevance", "requirements_alignment"), ("needs_coverage", "needs_coverage"),
                         ("groundedness", "groundedness"), ("solution_quality", "solution_quality")):
    JUDGE_PROMPTS[("final", _metric)] = JUDGE_PROMPTS[("solution", _source)]


# ---------------------------------------------------------------- evidence as readable text

def _needs_text(requirements: dict) -> list[str]:
    lines = []
    for priority, key in enumerate(("primary_need", "secondary_need", "third_need"), 1):
        need = (requirements or {}).get(key)
        if need:
            lines.append(f"  {priority}. {need.get('need')} ({need.get('level')})")
    return lines or ["  (not provided)"]


def _caps(capabilities: dict) -> str:
    return ", ".join(f"{k}={v}" for k, v in (capabilities or {}).items()) or "no capabilities listed"


def _item(kind: str, row: dict, id_key: str) -> str:
    serves = ", ".join(n.get("need", "?") for n in row.get("matched_needs", [])) or "none claimed"
    return (f"  - {kind} {row.get(id_key)} · {row.get('name')} · {row.get('price_sar')} SAR\n"
            f"      serves (tool claim): {serves}\n      capabilities: {_caps(row.get('capabilities'))}")


def _search_evidence(inputs: dict, prediction: dict, reference: dict) -> str:
    requirements = inputs.get("requirements", {})
    pool = prediction.get("candidate_pool") or {}
    lines = ["CUSTOMER NEEDS (priority order):", *_needs_text(requirements),
             f"PREFERRED BUDGET: {requirements.get('preferred_budget_sar')} SAR", "",
             f"SHORTLIST (search status: {pool.get('search_status')}):"]
    items = [_item("package", r, "package_id") for r in pool.get("package_candidates", [])]
    items += [_item("add-on", r, "addon_id") for r in pool.get("addon_candidates", [])]
    lines += items or ["  (empty)"]
    retrieved_p = reference.get("retrieved_package_ids") or []
    retrieved_a = reference.get("retrieved_addon_ids") or []
    if retrieved_p or retrieved_a:
        lines += ["", f"RETRIEVED BEFORE SHORTLISTING: {len(retrieved_p)} packages, {len(retrieved_a)} add-ons"]
    notes = pool.get("search_notes") or []
    lines += ["", "AGENT NOTES:", *(f"  - {n}" for n in notes)] if notes else ["", "AGENT NOTES: none"]
    judge = next((s.get("details", {}) for s in prediction.get("stages", []) if s.get("stage") == "JUDGE"), None)
    if judge:
        lines += ["", f"JUDGE VERDICT: {judge.get('verdict')}"]
        lines += [f"  gap: priority {g.get('need_priority')} · {g.get('gap_type')} · {g.get('reason')}"
                  for g in judge.get("gaps", [])] or ["  gaps: none"]
        if judge.get("evaluation_summary"):
            lines.append(f"  summary: {judge.get('evaluation_summary')}")
    else:
        lines += ["", "JUDGE VERDICT: the Judge step did not run"]
    return "\n".join(lines)


def _solution_block(index: int, solution: dict) -> list[str]:
    package = solution.get("package") or {}
    lines = [f"SOLUTION {index}{' (RECOMMENDED)' if solution.get('recommended') else ''}",
             f"  total: {solution.get('total_price_sar')} SAR · budget status: {solution.get('budget_status')}",
             f"  package {package.get('package_id')} · {package.get('name')} · {package.get('price_sar')} SAR",
             f"      capabilities: {_caps(package.get('capabilities'))}"]
    for addon in solution.get("addons", []):
        lines += [f"  add-on {addon.get('addon_id')} · {addon.get('name')} · {addon.get('price_sar')} SAR",
                  f"      capabilities: {_caps(addon.get('capabilities'))}"]
    lines.append("  coverage:")
    for row in solution.get("needs_coverage", []):
        lines.append(f"    - {row.get('need')} ({row.get('requested_level')}, priority {row.get('need_priority')}): "
                     f"{row.get('coverage')} · evidence: {row.get('evidence')}")
    lines.append(f"  trade-off: {solution.get('trade_off')}")
    if solution.get("recommendation_reason"):
        lines.append(f"  reason shown to the customer: {solution.get('recommendation_reason')}")
    return lines


def _solutions_evidence(inputs: dict, solutions: list[dict], note: str = "") -> str:
    requirements = inputs.get("requirements", {})
    lines = ["CUSTOMER NEEDS (priority order):", *_needs_text(requirements),
             f"PREFERRED BUDGET: {requirements.get('preferred_budget_sar')} SAR", "",
             f"SOLUTIONS PRESENTED: {len(solutions)}"]
    if note:
        lines.append(note)
    for i, solution in enumerate(solutions, 1):
        lines += ["", *_solution_block(i, solution)]
    if not solutions:
        lines.append("  (none)")
    return "\n".join(lines)


def build_evidence(scope: str, inputs: dict, prediction: dict, reference: dict) -> str:
    if scope == "search":
        return _search_evidence(inputs, prediction, reference)
    if scope == "solution":
        return _solutions_evidence(inputs, prediction.get("solutions", []))
    solutions = prediction.get("solutions")
    if solutions is None:
        selected = prediction.get("selected_solution")
        return _solutions_evidence(inputs, [selected] if selected else [],
                                   "Only the solution the customer applied is available for this run.")
    return _solutions_evidence(inputs, solutions)


def render_prompt(scope: str, metric: str, inputs: dict, prediction: dict, reference: dict) -> str | None:
    """Full user message for one metric, or None when no written criteria exist for it."""
    prompt = JUDGE_PROMPTS.get((scope, metric))
    if prompt is None:
        return None
    lines = [f"METRIC: {prompt['title']}", "", f"QUESTION: {prompt['question']}", "", "SCORES:"]
    for score in ("1.0", "0.5", "0.0"):
        level = prompt["levels"][score]
        definition, example = (level, None) if isinstance(level, str) else level
        lines.append(f"  {score} = {definition}")
        if example:
            lines.append(f"        Example: {example}")
    lines += ["", "RULES:", *(f"  - {rule}" for rule in prompt["rules"]), "",
              "EVIDENCE", "--------", build_evidence(scope, inputs, prediction, reference)]
    return "\n".join(lines)
