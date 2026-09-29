"""SQLite-backed customer history and trusted admin reporting queries."""

from __future__ import annotations

import json
from contextlib import closing

from backend.database.db import SearchDatabase
from backend.services.current_plan_service import CurrentPlanService


JSON_FIELDS = (
    "requirements_json", "usage_level_json", "recommended_solution_ids_json",
    "previous_package_ids_json", "previous_package_names_json",
    "previous_addon_ids_json", "previous_addon_names_json",
    "new_package_ids_json", "new_package_names_json", "new_addon_ids_json", "new_addon_names_json",
    "previous_plan_json", "new_plan_json",
    "package_ids_json", "package_names_json", "addon_ids_json", "addon_names_json",
)


def _record(row) -> dict | None:
    if row is None:
        return None
    item = dict(row)
    for key in JSON_FIELDS:
        if key in item:
            value = item.pop(key)
            item[key.removesuffix("_json")] = json.loads(value) if value else []
    return item


class HistoryService:
    """Customer-scoped history queries and trusted server-side admin reports.

    The customer_id argument is an ownership filter, not proof of identity. A
    future authenticated API must bind it to the authenticated principal before
    exposing these methods to a customer-facing client.
    """

    def __init__(self, database: SearchDatabase | None = None) -> None:
        self.database = database or SearchDatabase()

    def get_current_plan(self, customer_id: str) -> dict | None:
        return CurrentPlanService(self.database).get_current_plan(customer_id)

    def get_customer_requests(self, customer_id: str) -> list[dict]:
        with closing(self.database._connect()) as connection:
            rows = connection.execute("SELECT * FROM requests WHERE customer_id=? ORDER BY julianday(created_at) DESC, request_id DESC",
                                      (customer_id,)).fetchall()
        return [_record(row) for row in rows]

    def get_customer_orders(self, customer_id: str) -> list[dict]:
        with closing(self.database._connect()) as connection:
            rows = connection.execute("SELECT * FROM orders WHERE customer_id=? ORDER BY julianday(created_at) DESC, order_id DESC",
                                      (customer_id,)).fetchall()
        return [_record(row) for row in rows]

    def get_request_details(self, request_id: str, customer_id: str) -> dict | None:
        with closing(self.database._connect()) as connection:
            row = connection.execute("""SELECT requests.*, orders.status AS order_status,
                orders.error_message AS order_error_message, orders.created_at AS order_created_at,
                orders.updated_at AS order_updated_at
                FROM requests LEFT JOIN orders ON orders.order_id=requests.order_id
                WHERE requests.request_id=? AND requests.customer_id=?""",
                                     (request_id, customer_id)).fetchone()
        return _record(row)

    def get_order_details(self, order_id: str, customer_id: str) -> dict | None:
        with closing(self.database._connect()) as connection:
            row = connection.execute("SELECT * FROM orders WHERE order_id=? AND customer_id=?",
                                     (order_id, customer_id)).fetchone()
        return _record(row)

    def get_order_status_history(self, order_id: str, customer_id: str) -> list[dict]:
        with closing(self.database._connect()) as connection:
            rows = connection.execute("""SELECT history.status, history.changed_at
                FROM order_status_history AS history JOIN orders ON orders.order_id=history.order_id
                WHERE history.order_id=? AND orders.customer_id=? ORDER BY history.id""",
                                      (order_id, customer_id)).fetchall()
        return [dict(row) for row in rows]

    def admin_request_status_counts(self) -> dict[str, int]:
        return self._counts("requests", "business_status")

    def admin_order_status_counts(self) -> dict[str, int]:
        return self._counts("orders", "status")

    def admin_summary_counts(self) -> dict[str, dict[str, int]]:
        request_counts = self.admin_request_status_counts()
        order_counts = self.admin_order_status_counts()
        for status in ("STARTED", "SOLUTIONS_PRESENTED", "CUSTOMER_SELECTED", "PENDING",
                       "PROCESSING", "ACTIVE", "FAILED", "CANCELLED"):
            request_counts.setdefault(status, 0)
        for status in ("PENDING", "PROCESSING", "ACTIVE", "FAILED", "CANCELLED"):
            order_counts.setdefault(status, 0)
        request_counts["TOTAL"] = sum(request_counts.values())
        order_counts["TOTAL"] = sum(order_counts.values())
        return {"requests": request_counts, "orders": order_counts}

    def _counts(self, table: str, status_column: str) -> dict[str, int]:
        with closing(self.database._connect()) as connection:
            rows = connection.execute(f"SELECT {status_column}, COUNT(*) FROM {table} GROUP BY {status_column}").fetchall()
        return {row[0]: row[1] for row in rows}

    def admin_list_requests(self, *, status: str | None = None, customer_id: str | None = None,
                            start_date: str | None = None, end_date: str | None = None,
                            limit: int = 50, offset: int = 0) -> list[dict]:
        return self._admin_list("requests", status, customer_id, start_date, end_date, limit, offset)

    def admin_list_orders(self, *, status: str | None = None, customer_id: str | None = None,
                          start_date: str | None = None, end_date: str | None = None,
                          limit: int = 50, offset: int = 0) -> list[dict]:
        return self._admin_list("orders", status, customer_id, start_date, end_date, limit, offset)

    def _admin_list(self, table, status, customer_id, start_date, end_date, limit, offset):
        if not 1 <= limit <= 500 or offset < 0:
            raise ValueError("limit must be 1..500 and offset must be non-negative")
        status_column = "business_status" if table == "requests" else "status"
        clauses, values = [], []
        for column, value in ((status_column, status), ("customer_id", customer_id)):
            if value is not None:
                clauses.append(f"{column}=?")
                values.append(value)
        if start_date is not None:
            clauses.append("julianday(created_at)>=julianday(?)")
            values.append(start_date)
        if end_date is not None:
            clauses.append("julianday(created_at)<=julianday(?)")
            values.append(end_date + "T23:59:59.999999+00:00" if len(end_date) == 10 else end_date)
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        with closing(self.database._connect()) as connection:
            rows = connection.execute(f"SELECT * FROM {table}{where} ORDER BY julianday(created_at) DESC LIMIT ? OFFSET ?",
                                      (*values, limit, offset)).fetchall()
        return [_record(row) for row in rows]
