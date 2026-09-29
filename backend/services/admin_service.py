"""Read-only admin reporting, built from the SQLite source of truth.

Every answer here is derived from persisted rows (requests, workflow
checkpoints, orders, current plans and the catalog). Nothing calls an agent or
an LLM, and nothing is written. The admin console reads:

- requests: one row per customer request with an operator-facing outcome
- journey: what each agent actually did for one request, with every catalog
  fact re-checked against the current catalog tables
- customers, agents and catalog: aggregates over the same rows

Customer-facing text persisted by the agents (e.g. the recommendation reason,
written to the customer as "you") is returned as a quote, never rephrased as
if it were an admin statement.
"""

from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from contextlib import closing
from datetime import datetime, timezone

from backend.database.db import SearchDatabase

STALLED_AFTER_MINUTES = 30

# Operator-facing outcome of a request, in the order the console lists them.
OUTCOMES = ("applied", "awaiting", "applying", "in_progress", "no_fit", "declined",
            "apply_failed", "failed", "stalled")
OUTCOME_LABELS = {
    "applied": "Plan changed", "awaiting": "Waiting for customer", "applying": "Applying",
    "in_progress": "Agents working", "no_fit": "No fitting solution", "declined": "Declined by customer",
    "apply_failed": "Apply failed", "failed": "Technical failure", "stalled": "Stalled",
}
NEED_KEYS = ("primary_need", "secondary_need", "third_need")
DISPLAY_NEED = {"Multiple Devices": "Multi-device / Extra SIM"}
CHECK_LABELS = {
    "catalog_validity": "Pieces exist in the catalog",
    "composition_validity": "One package + up to 2 add-ons",
    "price_integrity": "Total re-calculated from catalog",
    "budget_hard_limit": "Within budget + 15%",
}
# Older requests recorded package/add-on compatibility and stacking checks. Every add-on
# now works with every package, so those checks no longer exist and are not reported.
RETIRED_CHECKS = {"catalog_compatibility", "addon_stacking"}
POLICY_LABELS = {
    "REQUIREMENT_FIT": "Fit every need",
    "CORE_FOCUS": "Focus on top priorities",
    "CAPABILITY_HEADROOM": "Extra headroom",
}
# Capability names used in coverage evidence -> catalog column.
EVIDENCE_COLUMNS = {"eligible_for": "eligibility"}
_EVIDENCE = re.compile(r"^(?P<name>.+?) \((?P<kind>package|add-on)\): (?P<field>[a-z_]+) = (?P<value>.+)$")


# ---------------------------------------------------------------- helpers

def _json(value, default):
    if not value:
        return default
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return default


def _moment(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        moment = datetime.fromisoformat(str(value).replace("Z", "+00:00").replace(" ", "T", 1))
    except ValueError:
        return None
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


def _minutes_since(value: str | None) -> float | None:
    moment = _moment(value)
    return None if moment is None else (datetime.now(timezone.utc) - moment).total_seconds() / 60


def _number(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _same_value(a, b) -> bool:
    x, y = _number(a), _number(b)
    if x is not None and y is not None:
        return abs(x - y) < 1e-9
    return str(a).strip().lower() == str(b).strip().lower()


def _needs(requirements: dict) -> list[dict]:
    out = []
    for priority, key in enumerate(NEED_KEYS, 1):
        item = requirements.get(key)
        if item:
            out.append({"priority": priority, "need": DISPLAY_NEED.get(item.get("need"), item.get("need")),
                        "level": item.get("level")})
    return out


def _plan(snapshot: dict | None) -> dict | None:
    """Both snapshot shapes exist in SQLite: package_names[] and a single package_name."""
    if not snapshot:
        return None
    names = snapshot.get("package_names") or ([snapshot["package_name"]] if snapshot.get("package_name") else [])
    ids = snapshot.get("package_ids") or ([snapshot["package_id"]] if snapshot.get("package_id") else [])
    return {"package_id": ids[0] if ids else None, "name": names[0] if names else None,
            "addons": snapshot.get("addon_names") or [], "total_price": snapshot.get("total_price")}


def _change(previous: dict | None, new: dict | None) -> float | None:
    if not previous or not new:
        return None
    a, b = _number(previous.get("total_price")), _number(new.get("total_price"))
    return None if a is None or b is None else round(b - a, 2)


class _Catalog:
    """The current catalog, loaded once per report, for independent re-checks."""

    def __init__(self, connection):
        self.packages = {row["package_id"]: dict(row) for row in connection.execute("SELECT * FROM packages")}
        self.addons = {row["addon_id"]: dict(row) for row in connection.execute("SELECT * FROM addons")}
        self.package_by_name = {row["package_name"]: row for row in self.packages.values()}
        self.addon_by_name = {row["addon_name"]: row for row in self.addons.values()}

    def piece(self, kind: str, piece_id: str | None, name: str | None = None, price=None) -> dict:
        """Re-check one piece a agent used: it must exist with the same name and price."""
        table = self.packages if kind == "package" else self.addons
        row = table.get(piece_id or "")
        name_key = "package_name" if kind == "package" else "addon_name"
        if row is None:
            return {"in_catalog": False, "matches": False, "issue": f"{piece_id} is not in the catalog"}
        issues = []
        if name is not None and row[name_key] != name:
            issues.append(f"name differs (catalog: {row[name_key]})")
        if price is not None and not _same_value(row["monthly_price_sar"], price):
            issues.append(f"price differs (catalog: {row['monthly_price_sar']} SAR)")
        return {"in_catalog": True, "matches": not issues, "issue": "; ".join(issues) or None}

    def evidence(self, text: str) -> list[dict]:
        """Split coverage evidence into catalog facts and re-check each against its row."""
        facts = []
        for part in [p.strip() for p in str(text or "").split(";") if p.strip()]:
            match = _EVIDENCE.match(part)
            if not match:
                facts.append({"text": part, "verified": None})
                continue
            kind, field, value = match["kind"], match["field"], match["value"].strip()
            row = (self.package_by_name if kind == "package" else self.addon_by_name).get(match["name"])
            column = EVIDENCE_COLUMNS.get(field, field)
            if row is None or column not in row:
                facts.append({"text": part, "piece": match["name"], "kind": kind, "field": field,
                              "value": value, "verified": False,
                              "catalog_value": None})
                continue
            facts.append({"text": part, "piece": match["name"], "kind": kind, "field": field, "value": value,
                          "verified": _same_value(row[column], value), "catalog_value": row[column]})
        return facts


# requests.error_message is written for the customer (see backend.api.app._failure_message
# and RequestService.INTERRUPTED_MESSAGE). The console shows the operator cause it stands for,
# and keeps the original text as what the customer saw. Matched by phrase so older wordings work.
FAILURE_CAUSES = (
    ("OPENAI_API_KEY is not set", "OPENAI_API_KEY is not set on the server."),
    ("could not sign in", "OpenAI rejected the API key (OPENAI_API_KEY)."),
    ("cannot use the selected models", "The OpenAI key has no access to a configured model."),
    ("model was not found", "A configured OpenAI model does not exist (OPENAI_MODEL / JUDGE_MODEL / DESIGN_MODEL)."),
    ("rate or quota", "OpenAI rate or quota limit reached."),
    ("connection issue", "The server could not connect to OpenAI (network error)."),
    ("could not reach OpenAI", "The server could not connect to OpenAI (network error)."),
    ("timed out", "An OpenAI call timed out."),
    ("interrupted", "The server stopped or restarted while the agents were running."),
    ("adjust your needs and try again", "The workflow stopped with an unrecoverable error (older request, exact cause not recorded)."),
    ("couldn't complete your solution this time", "Unexpected error inside the workflow. Check the server log or the LangSmith trace."),
)


def failure_cause(message: str | None) -> str | None:
    if not message:
        return None
    lowered = message.lower()
    return next((cause for phrase, cause in FAILURE_CAUSES if phrase.lower() in lowered), message)


# ---------------------------------------------------------------- outcome

def _outcome(row: dict, pool: dict | None, final: dict | None) -> str:
    workflow, stage, business = row.get("status"), row.get("stage"), row.get("business_status")
    if business == "ACTIVE" or workflow == "COMPLETED":
        return "applied"
    if business == "CANCELLED" or workflow == "CANCELLED":
        return "declined"
    if business == "FAILED" and (row.get("order_id") or row.get("selected_solution_id")):
        return "apply_failed"
    if workflow == "ERROR" or business == "FAILED":
        return "failed"
    if workflow == "WAITING_FOR_USER":
        return "awaiting"
    if workflow == "NEEDS_ADJUSTMENT":
        return "no_fit"
    idle = _minutes_since(row.get("updated_at"))
    if idle is not None and idle > STALLED_AFTER_MINUTES:
        return "stalled"
    if business == "CUSTOMER_SELECTED" or stage == "APPLYING":
        return "applying"
    return "in_progress"


def _failure_stage(outcome: str, pool, candidates, validation) -> str | None:
    """Which agent the request stopped at, from what was persisted before it stopped."""
    if outcome in {"failed", "stalled"}:
        return "design" if pool is not None else "search"
    if outcome == "apply_failed":
        return "apply"
    if outcome == "no_fit":
        if not pool or pool.get("search_status") != "SUCCESS":
            return "search"
        if validation and all(item.get("overall") == "FAIL" for item in validation):
            return "guardrails"
        return "design"
    return None


def _rejections(validation) -> list[dict]:
    out = []
    for item in validation or []:
        if item.get("overall") != "FAIL":
            continue
        failed = [{"check": name, "label": CHECK_LABELS.get(name, name.replace("_", " ")),
                   "reason": check.get("reason")}
                  for name, check in (item.get("checks") or {}).items()
                  if check.get("status") == "FAIL" and name not in RETIRED_CHECKS]
        out.append({"strategy_id": item.get("strategy_id"), "failed": failed})
    return out


def _headline(outcome: str, row: dict, pool, validation, final, failure_stage) -> str:
    """One operator-facing sentence: what happened and, when it matters, why."""
    solutions = (final or {}).get("solutions", [])
    shown = len(solutions)
    recommended = next((s for s in solutions if s.get("recommended")), None)
    if outcome == "applied":
        chosen = next((s for s in solutions if s.get("solution_id") == row.get("selected_solution_id")), None)
        which = ("the recommended solution" if chosen and chosen.get("recommended")
                 else "an alternative (not the recommended one)" if chosen else "a solution")
        return f"Customer applied {which} of {shown} shown. Order is active."
    if outcome == "awaiting":
        text = f"{shown} solution{'s' if shown != 1 else ''} shown; the customer has not chosen yet."
        if not (final or {}).get("recommendation_available", True):
            text += " No recommendation was produced."
        return text
    if outcome == "applying":
        return "Customer chose a solution; the Apply tool is changing the plan."
    if outcome == "in_progress":
        return {"SEARCHING": "Search Agent is shortlisting catalog pieces.",
                "DESIGNING": "Solution Design Agent is building and checking solutions."}.get(
                    row.get("stage"), "The agents are working on this request.")
    if outcome == "declined":
        return (f"Customer saw {shown} solution{'s' if shown != 1 else ''} and declined all of them. Plan unchanged."
                if shown else "Request cancelled. Plan unchanged.")
    if outcome == "no_fit":
        if failure_stage == "search":
            return "Search Agent found no usable package in the catalog for these needs and budget."
        if failure_stage == "guardrails":
            rejected = _rejections(validation)
            first = rejected[0]["failed"][0] if rejected and rejected[0]["failed"] else None
            return (f"All {len(rejected)} designed solution{'s' if len(rejected) != 1 else ''} were stopped by the safety checks"
                    + (f": {first['reason'] or first['label']}" if first else "."))
        return "No solution fitted these needs and budget."
    if outcome == "apply_failed":
        return (f"Apply failed; the previous plan was kept. {failure_cause(row.get('error_message')) or ''}").strip()
    if outcome == "failed":
        where = {"search": "in Search", "design": "in Solution Design"}.get(failure_stage, "")
        cause = failure_cause(row.get("error_message")) or "The cause was not recorded (older request)."
        return f"Technical failure {where}. {cause}".replace("  ", " ")
    if outcome == "stalled":
        return (f"No progress for over {STALLED_AFTER_MINUTES} minutes while "
                f"{(row.get('stage') or 'processing').lower()}. The server most likely stopped mid-run.")
    return ""


# ---------------------------------------------------------------- loading

_REQUEST_SQL = """SELECT r.*, c.candidate_pool_json, c.candidate_solutions_json, c.solution_validation_json,
        c.final_solutions_json, o.status AS order_status, o.applied_at AS order_applied_at,
        o.total_price AS order_total_price, cu.name AS catalog_customer_name
    FROM requests r LEFT JOIN workflow_checkpoints c ON c.request_id=r.request_id
    LEFT JOIN orders o ON o.order_id=r.order_id
    LEFT JOIN customers cu ON cu.customer_id=r.customer_id"""


def _load(connection, where: str = "", args: tuple = ()) -> list[dict]:
    rows = connection.execute(f"{_REQUEST_SQL} {where} ORDER BY julianday(r.created_at) DESC, r.request_id DESC",
                              args).fetchall()
    out = []
    for raw in rows:
        row = dict(raw)
        row["requirements"] = _json(row.get("requirements_json"), {})
        row["pool"] = _json(row.get("candidate_pool_json"), None)
        row["candidates"] = _json(row.get("candidate_solutions_json"), None)
        row["validation"] = _json(row.get("solution_validation_json"), None)
        row["final"] = _json(row.get("final_solutions_json"), None)
        row["previous"] = _plan(_json(row.get("previous_plan_json"), {}))
        row["new"] = _plan(_json(row.get("new_plan_json"), {}))
        out.append(row)
    return out


def _summary(row: dict, catalog: _Catalog) -> dict:
    """The list-row view of one request."""
    pool, final, validation = row["pool"], row["final"], row["validation"]
    outcome = _outcome(row, pool, final)
    stage = _failure_stage(outcome, pool, row["candidates"], validation)
    solutions = (final or {}).get("solutions", [])
    recommended = next((s for s in solutions if s.get("recommended")), None)
    selected = row.get("selected_solution_id")
    grounding = _grounding(row, catalog)
    new_plan = row["new"] if outcome == "applied" else None
    return {
        "request_id": row["request_id"], "customer_id": row["customer_id"],
        "customer_name": row.get("customer_name") or row.get("catalog_customer_name"),
        "outcome": outcome, "created_at": row.get("created_at"), "updated_at": row.get("updated_at"),
        "outcome_at": (row.get("order_applied_at") or row.get("completed_at") or row.get("updated_at")),
        "needs": _needs(row["requirements"]), "budget": row["requirements"].get("preferred_budget_sar"),
        "previous_plan": row["previous"], "new_plan": new_plan,
        "price_change": _change(row["previous"], new_plan),
        "reached": _reached(row, outcome),
        "failure_stage": stage,
        "search": ({"packages": len(pool.get("package_candidates", [])), "addons": len(pool.get("addon_candidates", [])),
                    "status": pool.get("search_status")} if pool else None),
        "designed": len(row["candidates"] or []),
        "rejected": len(_rejections(validation)),
        "shown": len(solutions),
        "recommendation_available": bool((final or {}).get("recommendation_available", True)) if final else None,
        "recommended": ({"solution_id": recommended["solution_id"], "name": _solution_name(recommended),
                         "total_price": recommended.get("total_price_sar")} if recommended else None),
        "selected_solution_id": selected,
        "chose_recommended": (bool(recommended and selected == recommended["solution_id"]) if selected else None),
        "order_id": row.get("order_id"), "order_status": row.get("order_status"),
        "grounded": grounding["ok"], "grounding_checked": grounding["checked"],
        "headline": _headline(outcome, row, pool, validation, final, stage),
    }


def _loss_label(summary: dict) -> str:
    """Why a request stopped short of the next pipeline step, in operator words."""
    outcome, stage = summary["outcome"], summary["failure_stage"]
    where = {"search": "Search", "design": "Solution Design", "apply": "Apply"}.get(stage, "")
    return {
        "failed": f"failed in {where} (technical)", "stalled": f"got stuck in {where}",
        "apply_failed": "could not be applied",
        "no_fit": {"search": "found no usable package", "guardrails": "had every solution stopped by safety checks"}.get(
            stage, "had no fitting solution"),
        "awaiting": "still deciding", "declined": "declined",
        "applying": "being applied", "in_progress": "still running",
    }.get(outcome, OUTCOME_LABELS.get(outcome, outcome).lower())


def _reached(row: dict, outcome: str) -> list[str]:
    """Pipeline steps this request completed: search, design, shown, chosen, applied."""
    steps = []
    if row["pool"] is not None and row["pool"].get("search_status") == "SUCCESS":
        steps.append("search")
    if row["candidates"]:
        steps.append("design")
    if (row["final"] or {}).get("solutions") and outcome != "no_fit":
        steps.append("shown")
    if row.get("selected_solution_id"):
        steps.append("chosen")
    if outcome == "applied":
        steps.append("applied")
    return steps


def _solution_name(solution: dict) -> str:
    return " + ".join([solution.get("package", {}).get("name") or "?",
                       *[a.get("name") or "?" for a in solution.get("addons", [])]])


def _grounding(row: dict, catalog: _Catalog) -> dict:
    """Independent re-check that every persisted piece and total matches the catalog."""
    checked, issues = 0, []
    pool, final = row["pool"] or {}, row["final"] or {}
    for kind, key, id_key in (("package", "package_candidates", "package_id"), ("add-on", "addon_candidates", "addon_id")):
        for item in pool.get(key, []):
            checked += 1
            result = catalog.piece("package" if kind == "package" else "addon", item.get(id_key),
                                   item.get("name"), item.get("price_sar"))
            if not result["matches"]:
                issues.append(f"Search {kind} {item.get(id_key)}: {result['issue']}")
    for solution in final.get("solutions", []):
        checked += 1
        package = catalog.packages.get(solution.get("package", {}).get("package_id"))
        addons = [catalog.addons.get(a.get("addon_id")) for a in solution.get("addons", [])]
        if package is None or any(a is None for a in addons):
            issues.append(f"Solution {solution.get('solution_id')}: a piece is not in the catalog")
            continue
        total = _number(package["monthly_price_sar"]) + sum(_number(a["monthly_price_sar"]) or 0 for a in addons)
        if not _same_value(total, solution.get("total_price_sar")):
            issues.append(f"Solution {solution.get('solution_id')}: total {solution.get('total_price_sar')} SAR "
                          f"differs from catalog total {total:g} SAR")
    return {"checked": checked, "ok": not issues, "issues": issues}


# ---------------------------------------------------------------- public API

class AdminService:
    def __init__(self, database: SearchDatabase | None = None) -> None:
        self.database = database or SearchDatabase()

    def _connect(self):
        return closing(self.database._connect())

    # ---- requests -------------------------------------------------------
    def requests(self) -> dict:
        with self._connect() as connection:
            catalog = _Catalog(connection)
            rows = [_summary(row, catalog) for row in _load(connection)]
        counts = {key: 0 for key in OUTCOMES}
        for row in rows:
            counts[row["outcome"]] += 1
        return {"labels": OUTCOME_LABELS, "counts": counts, "total": len(rows), "requests": rows}

    def overview(self) -> dict:
        data = self.requests()
        rows = data["requests"]
        with self._connect() as connection:
            known = {row[0] for row in connection.execute("SELECT customer_id FROM customers")}
        total = len(rows)
        steps = [("requested", "Requests"), ("search", "Search found pieces"), ("shown", "Solutions shown"),
                 ("chosen", "Customer chose one"), ("applied", "Plan changed")]
        reached = lambda r, key: key == "requested" or key in r["reached"]
        funnel = []
        for index, (key, label) in enumerate(steps):
            entry = {"key": key, "label": label, "count": sum(reached(r, key) for r in rows), "lost": []}
            if index:
                previous = steps[index - 1][0]
                lost = Counter((r["outcome"], _loss_label(r)) for r in rows if reached(r, previous) and not reached(r, key))
                entry["lost"] = [{"outcome": o, "label": l, "count": n} for (o, l), n in lost.most_common()]
            funnel.append(entry)
        applied = [r for r in rows if r["outcome"] == "applied" and r["price_change"] is not None]
        chosen = [r for r in rows if r["chose_recommended"] is not None]
        decided = [r for r in rows if r["outcome"] in {"applied", "declined", "apply_failed"} and r["shown"]]
        attention = [r for r in rows if r["outcome"] in {"failed", "stalled", "apply_failed"}]
        waiting = sorted((r for r in rows if r["outcome"] == "awaiting"), key=lambda r: r["updated_at"] or "")
        need_counts: dict[str, dict] = {}
        for r in rows:
            for n in r["needs"]:
                entry = need_counts.setdefault(n["need"], {"need": n["need"], "requests": 0, "top_priority": 0,
                                                           "no_fit": 0, "applied": 0})
                entry["requests"] += 1
                entry["top_priority"] += n["priority"] == 1
                entry["no_fit"] += r["outcome"] == "no_fit"
                entry["applied"] += r["outcome"] == "applied"
        failures = Counter(r["failure_stage"] for r in rows if r["outcome"] in {"failed", "stalled", "apply_failed"})
        return {
            "labels": OUTCOME_LABELS, "counts": data["counts"], "total": total,
            # Test/legacy requests may carry IDs that are not catalog customers; count them apart.
            "customers": len({r["customer_id"] for r in rows} & known),
            "other_ids": len({r["customer_id"] for r in rows} - known),
            "funnel": funnel,
            "revenue": {
                "monthly_change": round(sum(r["price_change"] for r in applied), 2),
                "upgrades": sum(r["price_change"] > 0 for r in applied),
                "downgrades": sum(r["price_change"] < 0 for r in applied),
                "same": sum(r["price_change"] == 0 for r in applied),
                "applied": len(applied),
            },
            "recommendation": {
                "accepted": sum(1 for r in chosen if r["chose_recommended"]), "choices": len(chosen),
            },
            "decisions": {"decided": len(decided), "applied": sum(r["outcome"] == "applied" for r in decided),
                          "declined": sum(r["outcome"] == "declined" for r in decided)},
            "grounding": {"requests_checked": sum(1 for r in rows if r["grounding_checked"]),
                          "requests_with_issues": sum(1 for r in rows if r["grounding_checked"] and not r["grounded"])},
            "failures_by_stage": dict(failures),
            "attention": attention[:12],
            # The same failure repeats across many requests; group by cause so each shows once.
            "attention_groups": _group_attention(attention),
            "waiting": waiting[:8],
            "needs": sorted(need_counts.values(), key=lambda e: -e["requests"]),
            "daily": _daily(rows, 14),
            "recent": rows[:6],
        }

    def journey(self, request_id: str) -> dict | None:
        with self._connect() as connection:
            catalog = _Catalog(connection)
            rows = _load(connection, "WHERE r.request_id=?", (request_id,))
            if not rows:
                return None
            row = rows[0]
            history = [dict(item) for item in connection.execute(
                "SELECT status, changed_at FROM order_status_history WHERE order_id=? ORDER BY id",
                (row.get("order_id") or "",))]
            customer = connection.execute("SELECT customer_id, name, city, segment, tenure_months FROM customers "
                                          "WHERE customer_id=?", (row["customer_id"],)).fetchone()
        summary = _summary(row, catalog)
        return {**summary,
                "customer": dict(customer) if customer else None,
                "search_step": _search_step(row, catalog),
                "design_step": _design_step(row, catalog),
                "decision_step": _decision_step(row, summary),
                "apply_step": _apply_step(row, summary, history),
                "grounding": _grounding(row, catalog),
                "failure_cause": failure_cause(row.get("error_message")),
                "customer_message": row.get("error_message")}

    # ---- customers ------------------------------------------------------
    def customers(self) -> list[dict]:
        with self._connect() as connection:
            catalog = _Catalog(connection)
            requests = [_summary(row, catalog) for row in _load(connection)]
            customers = [dict(row) for row in connection.execute("SELECT * FROM customers ORDER BY customer_id")]
            plans = {row["customer_id"]: dict(row) for row in connection.execute("SELECT * FROM current_plans")}
        by_customer = defaultdict(list)
        for r in requests:
            by_customer[r["customer_id"]].append(r)
        return [_customer_row(c, plans.get(c["customer_id"]), by_customer.get(c["customer_id"], []), catalog)
                for c in customers]

    def customer(self, customer_id: str) -> dict | None:
        with self._connect() as connection:
            catalog = _Catalog(connection)
            customer = connection.execute("SELECT * FROM customers WHERE customer_id=?", (customer_id,)).fetchone()
            if customer is None:
                return None
            plan = connection.execute("SELECT * FROM current_plans WHERE customer_id=?", (customer_id,)).fetchone()
            requests = [_summary(row, catalog) for row in _load(connection, "WHERE r.customer_id=?", (customer_id,))]
            orders = [dict(row) for row in connection.execute(
                "SELECT order_id, request_id, status, total_price, previous_total_price, package_names_json, "
                "addon_names_json, previous_package_names_json, previous_addon_names_json, created_at, applied_at "
                "FROM orders WHERE customer_id=? ORDER BY julianday(created_at) DESC", (customer_id,))]
        customer = dict(customer)
        row = _customer_row(customer, dict(plan) if plan else None, requests, catalog)
        usage = [{"month": key.removeprefix("usage_"), "gb": _number(customer[key])}
                 for key in sorted(k for k in customer if k.startswith("usage_2"))]
        for order in orders:
            for key in ("package_names_json", "addon_names_json", "previous_package_names_json", "previous_addon_names_json"):
                order[key.removesuffix("_json")] = _json(order.pop(key), [])
        return {**row, "usage": usage, "usage_pattern": customer.get("usage_pattern"),
                "eligibility": customer.get("eligibility"),
                "plan_detail": _plan_detail(plan, catalog) if plan else None,
                "requests": requests, "orders": orders}

    # ---- agents ---------------------------------------------------------
    def agents(self) -> dict:
        with self._connect() as connection:
            catalog = _Catalog(connection)
            raw = _load(connection)
            orders = [dict(row) for row in connection.execute(
                "SELECT order_id, status, total_price, package_ids_json, addon_ids_json FROM orders")]
        rows = [(row, _summary(row, catalog)) for row in raw]
        return {"search": _search_stats(rows, catalog), "design": _design_stats(rows, catalog),
                "guardrails": _guardrail_stats(rows), "recommendation": _recommendation_stats(rows),
                "apply": _apply_stats(rows, orders, catalog), "total_requests": len(rows)}

    # ---- catalog --------------------------------------------------------
    def catalog(self) -> dict:
        with self._connect() as connection:
            catalog = _Catalog(connection)
            raw = _load(connection)
            plans = [dict(row) for row in connection.execute("SELECT package_id, addon_ids_json FROM current_plans")]
        usage = defaultdict(lambda: {"shortlisted": 0, "in_solutions": 0, "recommended": 0, "applied": 0, "active": 0})
        for row in raw:
            summary_outcome = _outcome(row, row["pool"], row["final"])
            for item in (row["pool"] or {}).get("package_candidates", []):
                usage[("package", item.get("package_id"))]["shortlisted"] += 1
            for item in (row["pool"] or {}).get("addon_candidates", []):
                usage[("addon", item.get("addon_id"))]["shortlisted"] += 1
            for solution in (row["final"] or {}).get("solutions", []):
                pieces = [("package", solution.get("package", {}).get("package_id"))] + \
                         [("addon", a.get("addon_id")) for a in solution.get("addons", [])]
                for piece in pieces:
                    usage[piece]["in_solutions"] += 1
                    usage[piece]["recommended"] += bool(solution.get("recommended"))
                    usage[piece]["applied"] += (summary_outcome == "applied"
                                                and solution.get("solution_id") == row.get("selected_solution_id"))
        for plan in plans:
            usage[("package", plan["package_id"])]["active"] += 1
            for addon_id in _json(plan["addon_ids_json"], []):
                usage[("addon", addon_id)]["active"] += 1
        packages = [{"id": pid, "name": p["package_name"], "type": p.get("package_type"),
                     "segment": p.get("target_segment"), "price": _number(p["monthly_price_sar"]),
                     "data_gb": p.get("data_gb"), "voice_minutes": p.get("voice_minutes"),
                     "features": _package_features(p), **usage[("package", pid)]}
                    for pid, p in catalog.packages.items()]
        addons = [{"id": aid, "name": a["addon_name"], "category": a.get("category"),
                   "price": _number(a["monthly_price_sar"]), "features": _addon_features(a),
                   "eligibility": a.get("eligibility"), **usage[("addon", aid)]}
                  for aid, a in catalog.addons.items()]
        requested = len(raw)
        return {"packages": packages, "addons": addons, "requests": requested,
                "totals": {"packages": len(packages), "addons": len(addons),
                           "packages_used": sum(1 for p in packages if p["shortlisted"]),
                           "addons_used": sum(1 for a in addons if a["shortlisted"])}}


# ---------------------------------------------------------------- journey steps

def _search_step(row: dict, catalog: _Catalog) -> dict:
    pool = row["pool"]
    needs = _needs(row["requirements"])
    if pool is None:
        ran = row.get("status") not in {"PROCESSING"} or row.get("stage") != "SEARCHING"
        return {"ran": False, "status": "error" if row.get("status") == "ERROR" else "pending",
                "note": ("Search did not finish, so no shortlist was saved." if ran else "Search is running.")}

    def pieces(key, id_key, kind):
        out = []
        for rank, item in enumerate(pool.get(key, []), 1):
            check = catalog.piece(kind, item.get(id_key), item.get("name"), item.get("price_sar"))
            out.append({"rank": rank, "id": item.get(id_key), "name": item.get("name"), "price": item.get("price_sar"),
                        "matched_needs": [DISPLAY_NEED.get(n.get("need"), n.get("need")) for n in item.get("matched_needs", [])],
                        "capabilities": item.get("capabilities", {}), **check})
        return out

    packages = pieces("package_candidates", "package_id", "package")
    addons = pieces("addon_candidates", "addon_id", "addon")
    coverage = []
    for need in needs:
        by = [p["name"] for p in packages + addons if need["need"] in p["matched_needs"]]
        coverage.append({**need, "pieces": by})
    return {"ran": True, "status": pool.get("search_status"), "assessment_status": pool.get("assessment_status"),
            "packages": packages, "addons": addons, "notes": pool.get("search_notes") or [],
            "coverage": coverage,
            "catalog_size": {"packages": len(catalog.packages), "addons": len(catalog.addons)}}


def _design_step(row: dict, catalog: _Catalog) -> dict:
    candidates, validation, final = row["candidates"], row["validation"] or [], row["final"] or {}
    if not candidates:
        return {"ran": False, "note": ("Solution Design did not run because Search found no usable package."
                                       if row["pool"] and row["pool"].get("search_status") != "SUCCESS"
                                       else "Solution Design did not finish, so no solutions were saved."
                                       if row["pool"] else "Solution Design did not start.")}
    pool = row["pool"] or {}
    names = {item.get("package_id"): item for item in pool.get("package_candidates", [])}
    names.update({item.get("addon_id"): item for item in pool.get("addon_candidates", [])})
    checks = {item.get("strategy_id"): item for item in validation}
    shown = {item.get("solution_id"): item for item in final.get("solutions", [])}
    solutions = []
    for item in candidates:
        strategy = item.get("strategy", {})
        sid = strategy.get("strategy_id")
        guard = checks.get(sid) or {}
        final_entry = shown.get(sid)
        package = names.get(item.get("package_id")) or {}
        coverage = []
        for need in (item.get("review") or {}).get("needs_coverage", []):
            coverage.append({"need": DISPLAY_NEED.get(need.get("need"), need.get("need")),
                             "priority": need.get("need_priority"), "level": need.get("requested_level"),
                             "covered": need.get("coverage") == "COVERED",
                             "evidence": catalog.evidence(need.get("evidence"))})
        solutions.append({
            "solution_id": sid,
            "policy": strategy.get("decision_policy"),
            "policy_label": POLICY_LABELS.get(strategy.get("decision_policy"), strategy.get("decision_policy")),
            "intent": strategy.get("intent"),
            "package": {"id": item.get("package_id"), "name": package.get("name"), "price": package.get("price_sar")},
            "addons": [{"id": aid, "name": (names.get(aid) or {}).get("name"), "price": (names.get(aid) or {}).get("price_sar")}
                       for aid in item.get("addon_ids", [])],
            "total_price": item.get("total_price_sar"), "budget_difference": item.get("budget_difference_sar"),
            "budget_fit": (item.get("review") or {}).get("budget_fit"),
            "coverage": coverage,
            "trade_off": (item.get("review") or {}).get("trade_off_summary"),
            "converged": len(item.get("also_converged_strategies") or []),
            "guardrails": {"overall": guard.get("overall"), "hard_limit": guard.get("hard_budget_limit_sar"),
                           "recalculated_total": guard.get("recalculated_total_price_sar"),
                           "checks": [{"key": key, "label": CHECK_LABELS.get(key, key), "status": value.get("status"),
                                       "reason": value.get("reason")}
                                      for key, value in (guard.get("checks") or {}).items()
                                      if key not in RETIRED_CHECKS]},
            "shown": final_entry is not None,
            "recommended": bool(final_entry and final_entry.get("recommended")),
            # Written by the agent to the customer ("you"); shown to the admin as a quote.
            "told_customer": final_entry.get("recommendation_reason") if final_entry else None,
        })
    return {"ran": True, "solutions": solutions,
            "recommendation_available": bool(final.get("recommendation_available", True)) if final else None}


def _decision_step(row: dict, summary: dict) -> dict:
    outcome = summary["outcome"]
    if summary["shown"] == 0:
        return {"reached": False}
    return {"reached": True, "shown": summary["shown"], "selected_solution_id": summary["selected_solution_id"],
            "chose_recommended": summary["chose_recommended"],
            "state": ("chosen" if summary["selected_solution_id"] else "declined" if outcome == "declined"
                      else "waiting" if outcome == "awaiting" else "none"),
            "waiting_minutes": _minutes_since(row.get("updated_at")) if outcome == "awaiting" else None}


def _apply_step(row: dict, summary: dict, history: list[dict]) -> dict:
    if not row.get("selected_solution_id") and not row.get("order_id"):
        return {"reached": False}
    return {"reached": True, "order_id": row.get("order_id"), "order_status": row.get("order_status"),
            "applied_at": row.get("order_applied_at"), "history": history,
            "previous_plan": row["previous"], "new_plan": row["new"],
            "price_change": _change(row["previous"], row["new"])}


# ---------------------------------------------------------------- aggregates

def _group_attention(rows: list[dict]) -> list[dict]:
    groups: dict[str, dict] = {}
    for r in rows:  # rows are newest first, so the first one seen is the latest
        group = groups.setdefault(r["headline"], {"headline": r["headline"], "outcome": r["outcome"],
                                                  "failure_stage": r["failure_stage"], "count": 0,
                                                  "latest_request_id": r["request_id"], "latest_at": r["created_at"],
                                                  "customers": set()})
        group["count"] += 1
        group["customers"].add(r["customer_name"] or r["customer_id"])
    out = sorted(groups.values(), key=lambda g: -g["count"])
    for g in out:
        g["customers"] = sorted(g["customers"])
    return out


def _daily(rows: list[dict], days: int) -> list[dict]:
    today = datetime.now(timezone.utc).date()
    buckets = {}
    for offset in range(days - 1, -1, -1):
        day = today.fromordinal(today.toordinal() - offset).isoformat()
        buckets[day] = {"date": day, "requests": 0, "applied": 0, "failed": 0}
    for r in rows:
        moment = _moment(r["created_at"])
        if moment is None:
            continue
        bucket = buckets.get(moment.date().isoformat())
        if bucket:
            bucket["requests"] += 1
            bucket["applied"] += r["outcome"] == "applied"
            bucket["failed"] += r["outcome"] in {"failed", "stalled", "apply_failed"}
    return list(buckets.values())


def _customer_row(customer: dict, plan: dict | None, requests: list[dict], catalog: _Catalog) -> dict:
    latest = requests[0] if requests else None
    package = catalog.packages.get(plan["package_id"]) if plan else None
    return {
        "customer_id": customer["customer_id"], "name": customer["name"], "city": customer.get("city"),
        "segment": customer.get("segment"), "tenure_months": customer.get("tenure_months"),
        "latest_usage_gb": _number(customer.get("latest_usage_gb")),
        "plan": ({"package_id": plan["package_id"], "name": plan["package_name"],
                  "addons": _json(plan.get("addon_names_json"), []), "total_price": _number(plan.get("total_price")),
                  "data_gb": package.get("data_gb") if package else plan.get("package_data_gb"),
                  "changed_by_telora": any(r["outcome"] == "applied" for r in requests),
                  "updated_at": plan.get("updated_at")} if plan else None),
        "requests": len(requests),
        "applied": sum(r["outcome"] == "applied" for r in requests),
        "open": sum(r["outcome"] in {"awaiting", "applying", "in_progress"} for r in requests),
        "problems": sum(r["outcome"] in {"failed", "stalled", "apply_failed"} for r in requests),
        "last_request": ({"request_id": latest["request_id"], "outcome": latest["outcome"],
                          "created_at": latest["created_at"]} if latest else None),
    }


def _plan_detail(plan, catalog: _Catalog) -> dict:
    plan = dict(plan)
    package = catalog.packages.get(plan["package_id"]) or {}
    return {"package": {"id": plan["package_id"], "name": plan["package_name"],
                        "price": _number(package.get("monthly_price_sar")), "features": _package_features(package)},
            "addons": [{"id": a.get("addon_id"), "name": a.get("addon_name"), "price": _number(a.get("monthly_price_sar")),
                        "category": a.get("category")} for a in _json(plan.get("addon_details_json"), [])],
            "total_price": _number(plan.get("total_price")), "updated_at": plan.get("updated_at")}


def _has(value) -> bool:
    return value not in (None, "", 0, "0", "None", "No") and _number(value) != 0


def _amount(value, unit: str, label: str) -> str:
    """'40 GB social', or 'Unlimited social' when the catalog says Unlimited."""
    return f"Unlimited {label}" if str(value).lower() == "unlimited" else f"{value} {unit} {label}".replace("  ", " ")


def _package_features(p: dict) -> list[str]:
    if not p:
        return []
    out = []
    if _has(p.get("data_gb")):
        out.append(_amount(p["data_gb"], "GB", "data"))
    if _has(p.get("voice_minutes")):
        out.append(_amount(p["voice_minutes"], "min", "calls"))
    if _has(p.get("social_data_gb")):
        out.append(_amount(p["social_data_gb"], "GB", "social"))
    if _has(p.get("streaming_benefit")):
        out.append(str(p["streaming_benefit"]))
    if _has(p.get("gaming_benefit")):
        out.append(str(p["gaming_benefit"]))
    if _has(p.get("roaming_data_gb")):
        out.append(_amount(p["roaming_data_gb"], "GB", "roaming"))
    if _has(p.get("international_minutes")):
        out.append(_amount(p["international_minutes"], "min", "intl"))
    if p.get("multi_sim") == "Yes":
        out.append("Extra SIM")
    if p.get("data_sharing") == "Yes":
        out.append("Data sharing")
    return out


def _addon_features(a: dict) -> list[str]:
    out = []
    for key, unit, label in (("data_gb", "GB", "data"), ("social_data_gb", "GB", "social"),
                             ("roaming_data_gb", "GB", "roaming"), ("international_minutes", "min", "intl")):
        if _has(a.get(key)):
            out.append(_amount(a[key], unit, label))
    if _has(a.get("streaming_benefit")):
        out.append(str(a["streaming_benefit"]))
    if _has(a.get("gaming_benefit")):
        out.append(str(a["gaming_benefit"]))
    if a.get("multi_sim") == "Yes":
        out.append("Extra SIM")
    return out


def _search_stats(rows, catalog) -> dict:
    ran = [(row, s) for row, s in rows if row["pool"] is not None or s["failure_stage"] == "search"]
    with_pool = [row for row, _ in ran if row["pool"] is not None]
    found = sum(1 for row in with_pool if row["pool"].get("search_status") == "SUCCESS")
    no_results = sum(1 for row in with_pool if row["pool"].get("search_status") != "SUCCESS")
    errors = sum(1 for row, s in ran if row["pool"] is None)
    unmatched = Counter()
    judged_thin = sum(1 for row in with_pool if row["pool"].get("assessment_status") in {"INSUFFICIENT", "NO_RESULTS"})
    for row in with_pool:
        matched = {DISPLAY_NEED.get(n.get("need"), n.get("need"))
                   for key in ("package_candidates", "addon_candidates")
                   for item in row["pool"].get(key, []) for n in item.get("matched_needs", [])}
        for need in _needs(row["requirements"]):
            if need["need"] not in matched:
                unmatched[need["need"]] += 1
    pieces = sum(len(row["pool"].get("package_candidates", [])) + len(row["pool"].get("addon_candidates", []))
                 for row in with_pool)
    grounded_pieces = 0
    for row in with_pool:
        for item in row["pool"].get("package_candidates", []):
            grounded_pieces += catalog.piece("package", item.get("package_id"), item.get("name"), item.get("price_sar"))["matches"]
        for item in row["pool"].get("addon_candidates", []):
            grounded_pieces += catalog.piece("addon", item.get("addon_id"), item.get("name"), item.get("price_sar"))["matches"]
    causes = Counter(failure_cause(row.get("error_message")) or "Cause not recorded (older request)"
                     for row, _ in ran if row["pool"] is None)
    distinct_packages = {item.get("package_id") for row in with_pool for item in row["pool"].get("package_candidates", [])}
    distinct_addons = {item.get("addon_id") for row in with_pool for item in row["pool"].get("addon_candidates", [])}
    return {"runs": len(ran), "found": found, "no_results": no_results, "errors": errors,
            # `pieces` counts every pick across requests; the same product picked twice counts twice.
            "shortlists": len(with_pool), "distinct_packages": len(distinct_packages),
            "distinct_addons": len(distinct_addons),
            "error_causes": [{"cause": k, "requests": v} for k, v in causes.most_common()],
            "judged_thin": judged_thin,
            "avg_packages": _avg([len(r["pool"].get("package_candidates", [])) for r in with_pool], 1),
            "avg_addons": _avg([len(r["pool"].get("addon_candidates", [])) for r in with_pool], 1),
            "pieces": pieces, "grounded_pieces": grounded_pieces,
            "unmatched_needs": [{"need": k, "requests": v} for k, v in unmatched.most_common()]}


def _design_stats(rows, catalog) -> dict:
    ran = [(row, s) for row, s in rows if row["candidates"] or (row["pool"] is not None and s["failure_stage"] == "design")]
    with_output = [row for row, _ in ran if row["candidates"]]
    policies = Counter()
    coverage = {1: [0, 0], 2: [0, 0], 3: [0, 0]}
    facts, verified = 0, 0
    converged = 0
    for row in with_output:
        for item in row["candidates"]:
            policies[(item.get("strategy") or {}).get("decision_policy")] += 1
            converged += bool(item.get("also_converged_strategies"))
            for need in (item.get("review") or {}).get("needs_coverage", []):
                bucket = coverage.setdefault(need.get("need_priority"), [0, 0])
                bucket[1] += 1
                bucket[0] += need.get("coverage") == "COVERED"
                for fact in catalog.evidence(need.get("evidence")):
                    if fact["verified"] is not None:
                        facts += 1
                        verified += bool(fact["verified"])
    designed = sum(len(row["candidates"]) for row in with_output)
    causes = Counter(failure_cause(row.get("error_message")) or "Cause not recorded (older request)"
                     for row, _ in ran if not row["candidates"])
    return {"runs": len(ran), "completed": len(with_output), "errors": len(ran) - len(with_output),
            "error_causes": [{"cause": k, "requests": v} for k, v in causes.most_common()],
            "designed": designed, "avg_designed": _avg([len(row["candidates"]) for row in with_output], 1),
            "converged": converged,
            "policies": [{"policy": k, "label": POLICY_LABELS.get(k, k), "count": v} for k, v in policies.most_common()],
            "coverage_by_priority": [{"priority": p, "covered": c, "total": t} for p, (c, t) in sorted(coverage.items()) if t],
            "evidence_facts": facts, "evidence_verified": verified}


def _guardrail_stats(rows) -> dict:
    checks = defaultdict(lambda: {"pass": 0, "fail": 0, "last_reason": None})
    solutions = passed = 0
    blocked_requests = 0
    stopped = []
    for row, s in rows:
        validation = row["validation"] or []
        all_blocked = bool(validation) and all(item.get("overall") == "FAIL" for item in validation)
        blocked_requests += all_blocked
        for item in validation:
            solutions += 1
            passed += item.get("overall") == "PASS"
            if item.get("overall") == "FAIL":
                stopped.append({
                    "request_id": row["request_id"], "customer_name": s["customer_name"] or row["customer_id"],
                    "created_at": row.get("created_at"),
                    "total_price": _number(item.get("recalculated_total_price_sar")),
                    "limit": _number(item.get("hard_budget_limit_sar")),
                    "budget": _number(row["requirements"].get("preferred_budget_sar")),
                    "failed": [CHECK_LABELS.get(k, k) for k, v in (item.get("checks") or {}).items()
                               if v.get("status") == "FAIL" and k not in RETIRED_CHECKS],
                    "all_blocked": all_blocked})
            for key, value in (item.get("checks") or {}).items():
                if key in RETIRED_CHECKS:
                    continue
                entry = checks[key]
                if value.get("status") == "PASS":
                    entry["pass"] += 1
                else:
                    entry["fail"] += 1
                    entry["last_reason"] = value.get("reason") or entry["last_reason"]
    return {"solutions": solutions, "passed": passed, "blocked": solutions - passed,
            "requests_fully_blocked": blocked_requests, "stopped": stopped,
            "checks": [{"key": k, "label": CHECK_LABELS.get(k, k), **v}
                       for k, v in sorted(checks.items(), key=lambda kv: list(CHECK_LABELS).index(kv[0])
                                          if kv[0] in CHECK_LABELS else 99)]}


def _recommendation_stats(rows) -> dict:
    shown = [(row, s) for row, s in rows if s["shown"]]
    available = sum(1 for _, s in shown if s["recommendation_available"])
    chosen = [s for _, s in shown if s["chose_recommended"] is not None]
    return {"requests_shown": len(shown), "available": available, "missing": len(shown) - available,
            "choices": len(chosen), "accepted": sum(1 for s in chosen if s["chose_recommended"]),
            "avg_shown": _avg([s["shown"] for _, s in shown], 1)}


def _apply_stats(rows, orders, catalog) -> dict:
    statuses = Counter(order["status"] for order in orders)
    price_ok = 0
    for order in orders:
        ids = _json(order["package_ids_json"], [])
        package = catalog.packages.get(ids[0]) if ids else None
        addons = [catalog.addons.get(a) for a in _json(order["addon_ids_json"], [])]
        if package and all(addons):
            total = _number(package["monthly_price_sar"]) + sum(_number(a["monthly_price_sar"]) for a in addons)
            price_ok += _same_value(total, order["total_price"])
    attempted = [s for _, s in rows if s["selected_solution_id"]]
    return {"orders": len(orders), "active": statuses.get("ACTIVE", 0), "failed": statuses.get("FAILED", 0),
            "in_progress": statuses.get("PENDING", 0) + statuses.get("PROCESSING", 0),
            "price_matches_catalog": price_ok, "attempted": len(attempted)}


def _avg(values, digits: int = 2):
    values = [v for v in values if v is not None]
    return round(sum(values) / len(values), digits) if values else None
