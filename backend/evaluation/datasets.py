"""Catalog-derived LangSmith evaluation dataset specifications.

Dataset references are structural business invariants, not invented golden
recommendations. Catalog IDs/prices are read from SQLite at sync time.
"""

from __future__ import annotations

import shutil
import sqlite3
import tempfile
from pathlib import Path

from backend.database.db import DEFAULT_DATABASE_PATH, SearchDatabase
from backend.schemas.requirements import NeedLevel, NeedSelection, Requirements
from backend.schemas.search import AddonSearchInput, CandidatePool, PackageSearchInput, SearchStatus, search_needs_from_requirements
from backend.tools.addon_search_tool import _search_addons_impl
from backend.tools.search_tool import _search_packages_impl


def _requirements(primary: str, budget: int, secondary: str | None = None,
                  third: str | None = None) -> Requirements:
    def pick(need, level):
        return NeedSelection(need, NeedLevel(level)) if need else None
    return Requirements(pick(primary, "High"), pick(secondary, "Medium"), pick(third, "Low"), budget)


def _scenarios():
    return [
        ("normal_request", _requirements("Data", 300, "Calls")),
        ("low_budget", _requirements("Data", 50)),
        ("high_data_usage", _requirements("Data", 700)),
        ("high_voice_usage", _requirements("Calls", 500)),
        ("roaming_requirement", _requirements("Roaming", 700)),
        ("international_calling", _requirements("International Calls", 700)),
        ("multiple_addons", _requirements("International Calls", 900, "Streaming", "Data")),
        ("insufficient_results", _requirements("Data", 1)),
        ("reassessment_required", _requirements("Social Media", 1, "Gaming")),
        ("no_suitable_candidate", _requirements("Data", 1)),
    ]


def build_dataset_specs(database_path: Path | str = DEFAULT_DATABASE_PATH) -> dict[str, list[dict]]:
    """Build references from canonical SQLite tables and explicit validation rules."""
    database_path = Path(database_path).resolve()
    with sqlite3.connect(f"file:{database_path}?mode=ro", uri=True) as connection:
        connection.row_factory = sqlite3.Row
        package_prices = {row["package_id"]: float(row["monthly_price_sar"])
                          for row in connection.execute("SELECT package_id,monthly_price_sar FROM packages")
                          if row["monthly_price_sar"] is not None}
        addon_prices = {row["addon_id"]: float(row["monthly_price_sar"])
                        for row in connection.execute("SELECT addon_id,monthly_price_sar FROM addons")
                        if row["monthly_price_sar"] is not None}
        package_catalog = {row["package_id"]: {
            "name": row["package_name"], "price_sar": float(row["monthly_price_sar"] or 0),
            "capabilities": {key: row[key] for key in ("data_gb", "social_data_gb", "streaming_benefit",
                "gaming_benefit", "voice_minutes", "roaming_data_gb", "international_minutes",
                "multi_sim", "data_sharing")},
        } for row in connection.execute("SELECT * FROM packages")}
        addon_catalog = {row["addon_id"]: {
            "name": row["addon_name"], "price_sar": float(row["monthly_price_sar"] or 0),
            "capabilities": {key: row[key] for key in ("category", "data_gb", "social_data_gb",
                "roaming_data_gb", "international_minutes", "multi_sim", "streaming_benefit",
                "gaming_benefit", "eligibility", "min_tenure_months")},
        } for row in connection.execute("SELECT * FROM addons")}
        customer = connection.execute("SELECT customer_id,current_package_id FROM customers ORDER BY customer_id LIMIT 1").fetchone()
    if customer is None:
        raise ValueError("Evaluation datasets require at least one catalog customer")

    # Search tool helpers are exercised against an isolated copy because their
    # constructor may initialize/migrate a DB. Dataset generation never writes
    # the project's source database.
    with tempfile.TemporaryDirectory(prefix="telora-eval-catalog-") as directory:
        isolated_db = Path(directory) / "telora.sqlite3"
        shutil.copy2(database_path, isolated_db)
        SearchDatabase(isolated_db)
        examples = []
        for case_id, requirements in _scenarios():
            needs = search_needs_from_requirements(requirements)
            packages = _search_packages_impl(PackageSearchInput(
                needs=needs, preferred_budget_sar=requirements.preferred_budget_sar), isolated_db)
            addons = _search_addons_impl(AddonSearchInput(
                needs=needs, preferred_budget_sar=requirements.preferred_budget_sar), isolated_db)
            pool = CandidatePool(
                package_candidates=packages.candidates[:12],
                addon_candidates=addons.candidates[:12],
                search_status=SearchStatus.SUCCESS if packages.candidates else SearchStatus.NO_RESULTS,
                search_notes=[],
            )
            if case_id == "insufficient_results":
                pool = CandidatePool(package_candidates=packages.candidates[:1], addon_candidates=[],
                                     search_status=SearchStatus.INSUFFICIENT,
                                     search_notes=["Evaluation fixture: intentionally limited candidate pool."])
            elif case_id == "no_suitable_candidate":
                pool = CandidatePool(package_candidates=[], addon_candidates=[],
                                     search_status=SearchStatus.NO_RESULTS, search_notes=[])
            examples.append({
                "case_id": case_id,
                "requirements": requirements.to_dict(),
                "candidate_pool": pool.model_dump(mode="json"),
                "package_prices": {item.package_id: package_prices[item.package_id]
                                   for item in pool.package_candidates if item.package_id in package_prices},
                "addon_prices": {item.addon_id: addon_prices[item.addon_id]
                                 for item in pool.addon_candidates if item.addon_id in addon_prices},
                "retrieved_package_ids": list(package_prices),
                "retrieved_addon_ids": list(addon_prices),
                "search_package_ids": [item.package_id for item in packages.candidates],
                "search_addon_ids": [item.addon_id for item in addons.candidates],
                "package_catalog": package_catalog,
                "addon_catalog": addon_catalog,
                "requested_needs": [need.need for need in needs],
                "budget_sar": requirements.preferred_budget_sar,
            })

    search_examples = [{
        "inputs": {"case_id": item["case_id"], "requirements": item["requirements"]},
        "outputs": {"requested_needs": item["requested_needs"], "budget_sar": item["budget_sar"],
                    "retrieved_package_ids": item["search_package_ids"],
                    "retrieved_addon_ids": item["search_addon_ids"],
                    "package_catalog": item["package_catalog"],
                    "addon_catalog": item["addon_catalog"],
                    "source": "SQLite canonical catalog and Requirements business contract"},
        "metadata": {"case_id": item["case_id"], "labels_are_catalog_derived": True},
    } for item in examples]
    design_examples = [{
        "inputs": {"case_id": item["case_id"], "requirements": item["requirements"],
                   "candidate_pool": item["candidate_pool"]},
        "outputs": {"requested_needs": item["requested_needs"], "budget_sar": item["budget_sar"],
                    "candidate_package_ids": [row["package_id"] for row in item["candidate_pool"]["package_candidates"]],
                    "candidate_addon_ids": [row["addon_id"] for row in item["candidate_pool"]["addon_candidates"]],
                    "package_prices": item["package_prices"], "addon_prices": item["addon_prices"],
                    "source": "SQLite canonical catalog and CandidatePool contract"},
        "metadata": {"case_id": item["case_id"], "labels_are_catalog_derived": True},
    } for item in examples]

    default_pool = next(item["candidate_pool"] for item in examples if item["case_id"] == "normal_request")
    package_id = next((item["package_id"] for item in default_pool["package_candidates"]
                       if item["package_id"] != customer["current_package_id"]),
                      default_pool["package_candidates"][0]["package_id"])
    base_apply = {"customer_id": customer["customer_id"], "package_id": package_id, "addon_ids": []}
    apply_scenarios = [
        ("successful_application", {**base_apply, "activation_succeeds": True}),
        ("apply_failure", {**base_apply, "activation_succeeds": False}),
        ("invalid_selection", {**base_apply, "invalid_selection": True, "activation_succeeds": True}),
        ("catalog_mismatch", {**base_apply, "catalog_mismatch": True, "activation_succeeds": True}),
        ("invalid_package", {**base_apply, "invalid_package": True, "activation_succeeds": True}),
        ("invalid_addon", {**base_apply, "invalid_addon": True, "activation_succeeds": True}),
    ]
    apply_examples = [{"inputs": {"case_id": name, **case},
                       "outputs": {"case_id": name,
                                   "activation_succeeds": case["activation_succeeds"],
                                   "expected_success": not (not case["activation_succeeds"] or any(
                                       case.get(key, False) for key in
                                       ("invalid_selection", "catalog_mismatch", "invalid_package", "invalid_addon"))),
                                   "failure_expected": not case["activation_succeeds"] or any(
                                       case.get(key, False) for key in
                                       ("invalid_selection", "catalog_mismatch", "invalid_package", "invalid_addon")),
                                   "package_id": case["package_id"], "source": "SQLite catalog plus deterministic Apply validation rules"},
                       "metadata": {"case_id": name, "fixture_mutations_explicit": True}}
                      for name, case in apply_scenarios]
    workflow_examples = [{
        "inputs": {"case_id": item["case_id"], "requirements": item["requirements"],
                   "customer_id": customer["customer_id"], "package_id": package_id,
                   "addon_ids": [], "activation_succeeds": True,
                   "expected_stage_contract": ["SEARCHING", "DESIGNING", "SELECTION", "APPLYING", "COMPLETED"]},
        "outputs": {"case_id": item["case_id"], "expected_terminal_status": "ACTIVE",
                    "required_request_order_link": True, "required_retrieval_count": 1,
                    "max_semantic_reassessment_count": 1,
                    "source": "RequestService transition table and SearchAgent flow"},
        "metadata": {"case_id": item["case_id"], "labels_are_business_contracts": True},
    } for item in examples[:7]]
    e2e_examples = [
        {"inputs": {"case_id": "successful_end_to_end", **base_apply, "activation_succeeds": True,
                    "requirements": _requirements("Data", 300, "Calls").to_dict()},
         "outputs": {"expected_terminal_status": "ACTIVE", "current_plan_must_change": True,
                     "request_order_link_required": True, "source": "Apply transaction and lifecycle contract"},
         "metadata": {"case_id": "successful_end_to_end", "activation_is_simulated": True}},
        {"inputs": {"case_id": "failed_end_to_end", **base_apply, "activation_succeeds": False,
                    "requirements": _requirements("Data", 300).to_dict()},
         "outputs": {"expected_terminal_status": "FAILED", "current_plan_must_change": False,
                     "request_order_link_required": True, "source": "Apply rollback contract"},
         "metadata": {"case_id": "failed_end_to_end", "activation_is_simulated": True}},
    ]
    return {
        "search_agent_eval": search_examples,
        "solution_design_eval": design_examples,
        "apply_tool_eval": apply_examples,
        "workflow_eval": workflow_examples,
        "end_to_end_eval": e2e_examples,
    }


def sync_langsmith_datasets(client, database_path: Path | str = DEFAULT_DATABASE_PATH) -> dict[str, int]:
    """Create missing LangSmith datasets and examples; never duplicates existing cases."""
    specs = build_dataset_specs(database_path)
    existing = {dataset.name: dataset for dataset in client.list_datasets()}
    counts = {}
    for name, examples in specs.items():
        dataset = existing.get(name)
        if dataset is None:
            dataset = client.create_dataset(
                name, description=f"TELORA {name}: catalog-derived references and explicit business contracts.",
                metadata={"owner": "TELORA", "label_policy": "SQLite-derived or deterministic business rule"},
            )
            existing[name] = dataset
        current = {example.metadata.get("case_id") for example in
                   client.list_examples(dataset_id=dataset.id, limit=10000)}
        created = 0
        for example in examples:
            case_id = example["metadata"]["case_id"]
            if case_id in current:
                continue
            client.create_example(dataset_id=dataset.id, **example)
            current.add(case_id)
            created += 1
        counts[name] = created
    return counts
