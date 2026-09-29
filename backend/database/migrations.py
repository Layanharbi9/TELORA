"""Versioned SQLite migrations; preserve catalogs and Request workflow data."""

import sqlite3


SCHEMA_VERSION = 16


REQUEST_COLUMNS = (
    "request_id", "customer_id", "requirements_json", "status", "stage", "created_at", "updated_at",
    "customer_name", "business_status", "usage_level_json", "budget",
    "recommended_solution_ids_json", "selected_solution_id", "order_id",
    "previous_package_ids_json", "previous_package_names_json", "previous_addon_ids_json",
    "previous_addon_names_json", "new_package_ids_json", "new_package_names_json",
    "new_addon_ids_json", "new_addon_names_json", "total_price", "error_message", "completed_at",
    "previous_total_price", "previous_plan_json", "new_plan_json",
)


def _migrate_request_integrity(connection: sqlite3.Connection) -> None:
    """Add request identity/order FKs and guard immutable order snapshots.

    Legacy requests may contain development customer IDs absent from the real
    catalog. The copy preserves those rows; SQLite enforces both FKs for new
    records and modified FK values after this migration.
    """
    if connection.in_transaction:
        raise ValueError("Migration requires a connection without an active transaction")
    foreign_keys_were_enabled = connection.execute("PRAGMA foreign_keys").fetchone()[0]
    if foreign_keys_were_enabled:
        connection.execute("PRAGMA foreign_keys = OFF")
    try:
        with connection:
            connection.execute("BEGIN IMMEDIATE")
            version = connection.execute("PRAGMA user_version").fetchone()[0]
            if version > SCHEMA_VERSION:
                raise ValueError(f"Unsupported database version: {version}")
            if version < 13:
                existing_columns = {row[1] for row in connection.execute("PRAGMA table_info(requests)")}
                missing = set(REQUEST_COLUMNS) - existing_columns
                if missing:
                    raise sqlite3.OperationalError(f"Cannot add request integrity constraints; missing columns: {sorted(missing)}")
                connection.execute("DROP TABLE IF EXISTS requests_integrity_v13")
                connection.execute("""CREATE TABLE requests_integrity_v13 (
                    request_id TEXT PRIMARY KEY NOT NULL,
                    customer_id TEXT NOT NULL,
                    requirements_json TEXT NOT NULL,
                    status TEXT NOT NULL,
                    stage TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    customer_name TEXT,
                    business_status TEXT NOT NULL DEFAULT 'STARTED',
                    usage_level_json TEXT,
                    budget NUMERIC,
                    recommended_solution_ids_json TEXT NOT NULL DEFAULT '[]',
                    selected_solution_id TEXT,
                    order_id TEXT,
                    previous_package_ids_json TEXT NOT NULL DEFAULT '[]',
                    previous_package_names_json TEXT NOT NULL DEFAULT '[]',
                    previous_addon_ids_json TEXT NOT NULL DEFAULT '[]',
                    previous_addon_names_json TEXT NOT NULL DEFAULT '[]',
                    new_package_ids_json TEXT NOT NULL DEFAULT '[]',
                    new_package_names_json TEXT NOT NULL DEFAULT '[]',
                    new_addon_ids_json TEXT NOT NULL DEFAULT '[]',
                    new_addon_names_json TEXT NOT NULL DEFAULT '[]',
                    total_price NUMERIC,
                    error_message TEXT,
                    completed_at TEXT,
                    previous_total_price NUMERIC,
                    previous_plan_json TEXT NOT NULL DEFAULT '{}',
                    new_plan_json TEXT NOT NULL DEFAULT '{}',
                    FOREIGN KEY (customer_id) REFERENCES customers(customer_id) DEFERRABLE INITIALLY DEFERRED,
                    FOREIGN KEY (order_id) REFERENCES orders(order_id) DEFERRABLE INITIALLY DEFERRED
                )""")
                columns = ", ".join(f'"{column}"' for column in REQUEST_COLUMNS)
                connection.execute(
                    f"INSERT INTO requests_integrity_v13 ({columns}) SELECT {columns} FROM requests"
                )
                connection.execute("DROP TABLE requests")
                connection.execute("ALTER TABLE requests_integrity_v13 RENAME TO requests")
                connection.execute("CREATE INDEX IF NOT EXISTS idx_requests_customer_id ON requests(customer_id)")
                connection.execute("CREATE INDEX IF NOT EXISTS idx_requests_status ON requests(business_status)")
                connection.execute("CREATE INDEX IF NOT EXISTS idx_requests_workflow_status ON requests(status)")
                connection.execute("CREATE INDEX IF NOT EXISTS idx_requests_created_at ON requests(created_at)")
                connection.execute("CREATE INDEX IF NOT EXISTS idx_requests_order_id ON requests(order_id)")
                allowed = "'STARTED','SOLUTIONS_PRESENTED','CUSTOMER_SELECTED','PENDING','PROCESSING','ACTIVE','FAILED','CANCELLED'"
                connection.execute(f"""CREATE TRIGGER IF NOT EXISTS validate_request_business_status_insert
                    BEFORE INSERT ON requests WHEN NEW.business_status NOT IN ({allowed})
                    BEGIN SELECT RAISE(ABORT, 'invalid request business status'); END""")
                connection.execute(f"""CREATE TRIGGER IF NOT EXISTS validate_request_business_status_update
                    BEFORE UPDATE OF business_status ON requests WHEN NEW.business_status NOT IN ({allowed})
                    BEGIN SELECT RAISE(ABORT, 'invalid request business status'); END""")
                connection.execute("""CREATE TRIGGER IF NOT EXISTS validate_request_customer_identity_insert
                    BEFORE INSERT ON requests
                    WHEN NEW.customer_id IS NULL OR trim(NEW.customer_id) = ''
                      OR NEW.customer_name IS NULL OR trim(NEW.customer_name) = ''
                    BEGIN SELECT RAISE(ABORT, 'request customer identity is required'); END""")
                connection.execute("""CREATE TRIGGER IF NOT EXISTS validate_request_customer_identity_update
                    BEFORE UPDATE OF customer_id,customer_name ON requests
                    WHEN NEW.customer_id IS NULL OR trim(NEW.customer_id) = ''
                      OR NEW.customer_name IS NULL OR trim(NEW.customer_name) = ''
                    BEGIN SELECT RAISE(ABORT, 'request customer identity is required'); END""")
                connection.execute("""CREATE TRIGGER IF NOT EXISTS protect_order_history_fields
                    BEFORE UPDATE OF order_id,request_id,customer_id,customer_name,selected_solution_id,
                        package_ids_json,package_names_json,addon_ids_json,addon_names_json,total_price,
                        previous_package_ids_json,previous_package_names_json,previous_addon_ids_json,
                        previous_addon_names_json,previous_total_price,previous_plan_json,new_plan_json,created_at
                    ON orders
                    WHEN OLD.order_id IS NOT NEW.order_id OR OLD.request_id IS NOT NEW.request_id
                      OR OLD.customer_id IS NOT NEW.customer_id OR OLD.customer_name IS NOT NEW.customer_name
                      OR OLD.selected_solution_id IS NOT NEW.selected_solution_id
                      OR OLD.package_ids_json IS NOT NEW.package_ids_json OR OLD.package_names_json IS NOT NEW.package_names_json
                      OR OLD.addon_ids_json IS NOT NEW.addon_ids_json OR OLD.addon_names_json IS NOT NEW.addon_names_json
                      OR OLD.total_price IS NOT NEW.total_price
                      OR OLD.previous_package_ids_json IS NOT NEW.previous_package_ids_json
                      OR OLD.previous_package_names_json IS NOT NEW.previous_package_names_json
                      OR OLD.previous_addon_ids_json IS NOT NEW.previous_addon_ids_json
                      OR OLD.previous_addon_names_json IS NOT NEW.previous_addon_names_json
                      OR OLD.previous_total_price IS NOT NEW.previous_total_price
                      OR OLD.previous_plan_json IS NOT NEW.previous_plan_json OR OLD.new_plan_json IS NOT NEW.new_plan_json
                      OR OLD.created_at IS NOT NEW.created_at
                    BEGIN SELECT RAISE(ABORT, 'historical order fields are immutable'); END""")
                connection.execute("""CREATE TRIGGER IF NOT EXISTS protect_order_history_delete
                    BEFORE DELETE ON orders
                    BEGIN SELECT RAISE(ABORT, 'historical orders cannot be deleted'); END""")
                connection.execute("PRAGMA user_version = 13")
        # Store a single LangSmith root run reference per TELORA request so
        # terminal/resumed runs can be linked back to the request's root trace.
        if version < 14:
            with connection:
                connection.execute("BEGIN IMMEDIATE")
                connection.execute("""CREATE TABLE IF NOT EXISTS workflow_trace_roots (
                    request_id TEXT PRIMARY KEY NOT NULL,
                    root_run_id TEXT NOT NULL UNIQUE,
                    workflow_id TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY (request_id) REFERENCES requests(request_id) ON DELETE CASCADE
                )""")
                connection.execute("PRAGMA user_version = 14")
        # Nullable progress metadata inside SEARCHING (e.g. CHECKING); not a
        # lifecycle stage. Existing rows stay NULL.
        if version < 15:
            with connection:
                connection.execute("BEGIN IMMEDIATE")
                request_columns = {row[1] for row in connection.execute("PRAGMA table_info(requests)")}
                if "search_phase" not in request_columns:
                    connection.execute("ALTER TABLE requests ADD COLUMN search_phase TEXT")
                connection.execute("PRAGMA user_version = 15")
        # Nullable per-process owner of an in-flight Search/Design run
        # ("web:<boot-uuid>" / "cli:<boot-uuid>"). Existing rows stay NULL and
        # are treated as legacy/unowned by startup reconciliation.
        if version < 16:
            with connection:
                connection.execute("BEGIN IMMEDIATE")
                request_columns = {row[1] for row in connection.execute("PRAGMA table_info(requests)")}
                if "executor_id" not in request_columns:
                    connection.execute("ALTER TABLE requests ADD COLUMN executor_id TEXT")
                connection.execute("PRAGMA user_version = 16")
    finally:
        if foreign_keys_were_enabled:
            connection.execute("PRAGMA foreign_keys = ON")


def migrate(connection: sqlite3.Connection) -> None:
    """Own the transaction so DDL and user_version commit or roll back together."""
    if connection.in_transaction:
        raise ValueError("Migration requires a connection without an active transaction")
    with connection:
        connection.execute("BEGIN IMMEDIATE")
        version = connection.execute("PRAGMA user_version").fetchone()[0]
        if version > SCHEMA_VERSION:
            raise ValueError(f"Unsupported database version: {version}")
        if version == 0:
            connection.execute("""
                CREATE TABLE requests (
                    request_id TEXT PRIMARY KEY NOT NULL,
                    customer_id TEXT NOT NULL,
                    requirements_json TEXT NOT NULL,
                    status TEXT NOT NULL,
                    stage TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
            """)
            connection.execute("""
                CREATE TABLE workflow_checkpoints (
                    request_id TEXT PRIMARY KEY NOT NULL,
                    candidate_pool_json TEXT NULL,
                    candidate_solutions_json TEXT NULL,
                    solution_validation_json TEXT NULL,
                    final_solutions_json TEXT NULL,
                    selected_solution_json TEXT NULL,
                    applied_solution_json TEXT NULL,
                    FOREIGN KEY (request_id) REFERENCES requests(request_id)
                )
            """)
            connection.execute("PRAGMA user_version = 1")

        if version < 2:
            connection.execute("DROP TABLE IF EXISTS search_requests")
            connection.execute("PRAGMA user_version = 2")

        if version < 3:
            # Catalog tables are created by SearchDatabase immediately before this
            # migration is run. Keep a complete, queryable snapshot of the active
            # plan while retaining the source catalog values without coercion.
            connection.execute("""
                CREATE TABLE IF NOT EXISTS current_plans (
                    customer_id TEXT PRIMARY KEY NOT NULL,
                    customer_name TEXT NOT NULL,
                    package_id TEXT,
                    package_name TEXT,
                    package_data_gb,
                    package_voice_minutes,
                    package_sms,
                    package_included_benefits_json TEXT NOT NULL DEFAULT '[]',
                    addon_ids_json TEXT NOT NULL DEFAULT '[]',
                    addon_names_json TEXT NOT NULL DEFAULT '[]',
                    addon_details_json TEXT NOT NULL DEFAULT '[]',
                    total_price,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY (customer_id) REFERENCES customers(customer_id),
                    FOREIGN KEY (package_id) REFERENCES packages(package_id)
                )
            """)
            connection.execute("CREATE INDEX IF NOT EXISTS idx_current_plans_package_id ON current_plans(package_id)")
            # Import each customer's known active package and recorded total.
            # The source data does not identify active add-ons, so do not infer
            # them from requested_addon_id.
            connection.execute("""
                INSERT OR IGNORE INTO current_plans (
                    customer_id, customer_name, package_id, package_name,
                    package_data_gb, package_voice_minutes, package_sms,
                    package_included_benefits_json, addon_ids_json, addon_names_json,
                    addon_details_json, total_price, created_at, updated_at
                )
                SELECT c.customer_id, c.name, p.package_id, p.package_name,
                    p.data_gb, p.voice_minutes, p.sms,
                    json_array(
                        CASE WHEN p.streaming_benefit IS NOT NULL AND p.streaming_benefit != '' AND p.streaming_benefit != 'None' THEN p.streaming_benefit END,
                        CASE WHEN p.gaming_benefit IS NOT NULL AND p.gaming_benefit != '' AND p.gaming_benefit != 'None' THEN p.gaming_benefit END
                    ),
                    '[]', '[]', '[]', c.current_price_sar,
                    CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                FROM customers c
                LEFT JOIN packages p ON p.package_id = c.current_package_id
            """)
            # Remove null placeholders from benefits; JSON array remains valid and
            # retains multiple catalog benefits in a structured representation.
            connection.execute("""
                UPDATE current_plans
                SET package_included_benefits_json = (
                    SELECT json_group_array(value) FROM json_each(package_included_benefits_json)
                    WHERE value IS NOT NULL
                )
            """)
            connection.execute("CREATE INDEX IF NOT EXISTS idx_requests_customer_id ON requests(customer_id)")
            connection.execute("CREATE INDEX IF NOT EXISTS idx_requests_workflow_status ON requests(status)")
            connection.execute("CREATE INDEX IF NOT EXISTS idx_requests_created_at ON requests(created_at)")
            connection.execute("PRAGMA user_version = 3")

        if version < 4:
            # Add TELORA business-history fields to the existing workflow request
            # table so there is one request history, not parallel records.
            request_columns = {row[1] for row in connection.execute("PRAGMA table_info(requests)")}
            additions = {
                "customer_name": "TEXT",
                "business_status": "TEXT NOT NULL DEFAULT 'STARTED'",
                "usage_level_json": "TEXT",
                "budget": "NUMERIC",
                "recommended_solution_ids_json": "TEXT NOT NULL DEFAULT '[]'",
                "selected_solution_id": "TEXT",
                "order_id": "TEXT",
                "previous_package_ids_json": "TEXT NOT NULL DEFAULT '[]'",
                "previous_package_names_json": "TEXT NOT NULL DEFAULT '[]'",
                "previous_addon_ids_json": "TEXT NOT NULL DEFAULT '[]'",
                "previous_addon_names_json": "TEXT NOT NULL DEFAULT '[]'",
                "new_package_ids_json": "TEXT NOT NULL DEFAULT '[]'",
                "new_package_names_json": "TEXT NOT NULL DEFAULT '[]'",
                "new_addon_ids_json": "TEXT NOT NULL DEFAULT '[]'",
                "new_addon_names_json": "TEXT NOT NULL DEFAULT '[]'",
                "total_price": "NUMERIC",
                "error_message": "TEXT",
                "completed_at": "TEXT",
            }
            for column, definition in additions.items():
                if column not in request_columns:
                    connection.execute(f'ALTER TABLE requests ADD COLUMN "{column}" {definition}')
            connection.execute("""
                UPDATE requests SET customer_name = (
                    SELECT name FROM customers WHERE customers.customer_id = requests.customer_id
                ) WHERE customer_name IS NULL
            """)
            connection.execute("""
                CREATE TABLE IF NOT EXISTS orders (
                    order_id TEXT PRIMARY KEY NOT NULL,
                    request_id TEXT NOT NULL UNIQUE,
                    customer_id TEXT NOT NULL,
                    customer_name TEXT NOT NULL,
                    selected_solution_id TEXT NOT NULL,
                    package_ids_json TEXT NOT NULL,
                    package_names_json TEXT NOT NULL,
                    addon_ids_json TEXT NOT NULL,
                    addon_names_json TEXT NOT NULL,
                    total_price NUMERIC NOT NULL,
                    previous_package_ids_json TEXT NOT NULL,
                    previous_package_names_json TEXT NOT NULL,
                    previous_addon_ids_json TEXT NOT NULL,
                    previous_addon_names_json TEXT NOT NULL,
                    status TEXT NOT NULL CHECK(status IN ('PENDING','PROCESSING','ACTIVE','FAILED','CANCELLED')),
                    error_message TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    applied_at TEXT,
                    completed_at TEXT,
                    FOREIGN KEY (request_id) REFERENCES requests(request_id),
                    FOREIGN KEY (customer_id) REFERENCES customers(customer_id)
                )
            """)
            connection.execute("""
                CREATE TABLE IF NOT EXISTS order_status_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    order_id TEXT NOT NULL,
                    status TEXT NOT NULL CHECK(status IN ('PENDING','PROCESSING','ACTIVE','FAILED','CANCELLED')),
                    changed_at TEXT NOT NULL,
                    FOREIGN KEY (order_id) REFERENCES orders(order_id)
                )
            """)
            for table in ("requests", "orders"):
                connection.execute(f"CREATE INDEX IF NOT EXISTS idx_{table}_customer_id ON {table}(customer_id)")
                connection.execute(f"CREATE INDEX IF NOT EXISTS idx_{table}_status ON {table}({ 'business_status' if table == 'requests' else 'status' })")
                connection.execute(f"CREATE INDEX IF NOT EXISTS idx_{table}_created_at ON {table}(created_at)")
            connection.execute("CREATE INDEX IF NOT EXISTS idx_requests_order_id ON requests(order_id)")
            connection.execute("CREATE INDEX IF NOT EXISTS idx_orders_request_id ON orders(request_id)")
            connection.execute("CREATE INDEX IF NOT EXISTS idx_order_status_history_order_id ON order_status_history(order_id)")
            connection.execute("PRAGMA user_version = 4")

        if version < 5:
            current_columns = {row[1] for row in connection.execute("PRAGMA table_info(current_plans)")}
            details_columns = {
                "package_social_data_gb": "",
                "package_roaming_data_gb": "",
                "package_international_minutes": "",
                "package_multi_sim": "TEXT",
                "package_data_sharing": "TEXT",
                "package_contract_months": "",
                "package_details_json": "TEXT NOT NULL DEFAULT '{}'",
            }
            for column, definition in details_columns.items():
                if column not in current_columns:
                    connection.execute(f'ALTER TABLE current_plans ADD COLUMN "{column}" {definition}')
            connection.execute("""
                UPDATE current_plans SET
                    package_social_data_gb=(SELECT social_data_gb FROM packages WHERE packages.package_id=current_plans.package_id),
                    package_roaming_data_gb=(SELECT roaming_data_gb FROM packages WHERE packages.package_id=current_plans.package_id),
                    package_international_minutes=(SELECT international_minutes FROM packages WHERE packages.package_id=current_plans.package_id),
                    package_multi_sim=(SELECT multi_sim FROM packages WHERE packages.package_id=current_plans.package_id),
                    package_data_sharing=(SELECT data_sharing FROM packages WHERE packages.package_id=current_plans.package_id),
                    package_contract_months=(SELECT contract_months FROM packages WHERE packages.package_id=current_plans.package_id),
                    package_details_json=COALESCE((SELECT json_object(
                        'data_gb',data_gb,'social_data_gb',social_data_gb,'voice_minutes',voice_minutes,'sms',sms,
                        'streaming_benefit',streaming_benefit,'gaming_benefit',gaming_benefit,
                        'roaming_data_gb',roaming_data_gb,'international_minutes',international_minutes,
                        'multi_sim',multi_sim,'data_sharing',data_sharing,'contract_months',contract_months
                    ) FROM packages WHERE packages.package_id=current_plans.package_id), '{}')
            """)
            connection.execute("PRAGMA user_version = 5")

        if version < 6:
            connection.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_orders_request_id_unique ON orders(request_id)")
            connection.execute("PRAGMA user_version = 6")

        if version < 7:
            connection.execute("DROP INDEX IF EXISTS idx_requests_status")
            connection.execute("CREATE INDEX idx_requests_status ON requests(business_status)")
            connection.execute("CREATE INDEX IF NOT EXISTS idx_requests_workflow_status ON requests(status)")
            connection.execute("PRAGMA user_version = 7")

        if version < 8:
            allowed = "'STARTED','SOLUTIONS_PRESENTED','CUSTOMER_SELECTED','PENDING','PROCESSING','ACTIVE','FAILED','CANCELLED'"
            connection.execute(f"""CREATE TRIGGER IF NOT EXISTS validate_request_business_status_insert
                BEFORE INSERT ON requests WHEN NEW.business_status NOT IN ({allowed})
                BEGIN SELECT RAISE(ABORT, 'invalid request business status'); END""")
            connection.execute(f"""CREATE TRIGGER IF NOT EXISTS validate_request_business_status_update
                BEFORE UPDATE OF business_status ON requests WHEN NEW.business_status NOT IN ({allowed})
                BEGIN SELECT RAISE(ABORT, 'invalid request business status'); END""")
            connection.execute("PRAGMA user_version = 8")

        if version < 9:
            current_columns = {row[1] for row in connection.execute("PRAGMA table_info(current_plans)")}
            if "package_monthly_price_sar" not in current_columns:
                connection.execute("ALTER TABLE current_plans ADD COLUMN package_monthly_price_sar NUMERIC")
            connection.execute("""
                UPDATE current_plans SET package_monthly_price_sar=(
                    SELECT monthly_price_sar FROM packages WHERE packages.package_id=current_plans.package_id
                )
            """)
            connection.execute("""UPDATE current_plans SET package_details_json=json_set(
                package_details_json, '$.monthly_price_sar', package_monthly_price_sar
            )""")
            connection.execute("PRAGMA user_version = 9")

        if version < 10:
            request_columns = {row[1] for row in connection.execute("PRAGMA table_info(requests)")}
            if "previous_total_price" not in request_columns:
                connection.execute("ALTER TABLE requests ADD COLUMN previous_total_price NUMERIC")
            order_columns = {row[1] for row in connection.execute("PRAGMA table_info(orders)")}
            if "previous_total_price" not in order_columns:
                connection.execute("ALTER TABLE orders ADD COLUMN previous_total_price NUMERIC")
            connection.execute("PRAGMA user_version = 10")

        if version < 11:
            request_columns = {row[1] for row in connection.execute("PRAGMA table_info(requests)")}
            for column in ("previous_plan_json", "new_plan_json"):
                if column not in request_columns:
                    connection.execute(f"ALTER TABLE requests ADD COLUMN \"{column}\" TEXT NOT NULL DEFAULT '{{}}'")
            order_columns = {row[1] for row in connection.execute("PRAGMA table_info(orders)")}
            for column in ("previous_plan_json", "new_plan_json"):
                if column not in order_columns:
                    connection.execute(f"ALTER TABLE orders ADD COLUMN \"{column}\" TEXT NOT NULL DEFAULT '{{}}'")
            connection.execute("PRAGMA user_version = 11")

        if version < 12:
            connection.execute("""UPDATE requests SET business_status=CASE
                WHEN status='WAITING_FOR_USER' THEN 'SOLUTIONS_PRESENTED'
                WHEN status='COMPLETED' THEN 'ACTIVE'
                WHEN status='ERROR' THEN 'FAILED'
                WHEN stage='APPLYING' THEN 'CUSTOMER_SELECTED'
                ELSE business_status END
                WHERE business_status='STARTED'""")
            connection.execute("""UPDATE requests SET recommended_solution_ids_json=(
                SELECT COALESCE(json_group_array(json_extract(solution.value,'$.solution_id')), '[]')
                FROM workflow_checkpoints AS checkpoint,
                     json_each(checkpoint.final_solutions_json, '$.solutions') AS solution
                WHERE checkpoint.request_id=requests.request_id
            ) WHERE recommended_solution_ids_json='[]' AND EXISTS (
                SELECT 1 FROM workflow_checkpoints WHERE workflow_checkpoints.request_id=requests.request_id
                  AND workflow_checkpoints.final_solutions_json IS NOT NULL
            )""")
            connection.execute("""UPDATE requests SET selected_solution_id=COALESCE(
                json_extract((SELECT selected_solution_json FROM workflow_checkpoints
                              WHERE workflow_checkpoints.request_id=requests.request_id), '$.solution_id'),
                json_extract((SELECT selected_solution_json FROM workflow_checkpoints
                              WHERE workflow_checkpoints.request_id=requests.request_id), '$.id')
            ) WHERE selected_solution_id IS NULL""")
            connection.execute("PRAGMA user_version = 12")

    _migrate_request_integrity(connection)
