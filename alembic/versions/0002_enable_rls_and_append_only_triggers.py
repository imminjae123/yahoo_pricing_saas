"""
Alembic migration: Enable PostgreSQL Row-Level Security + Append-Only triggers.

This migration must run AFTER the initial table creation migration.

Security model
--------------
1. App DB role (`app_user`) has normal DML rights but NO BYPASSRLS.
2. Migration DB role (`migration_user`) has BYPASSRLS + schema owner rights.
   alembic/env.py connects as migration_user.

RLS policies implemented here
------------------------------
- tenant_isolation policy on all tenant-scoped tables:
    tenant_id = current_setting('app.current_tenant_id')::uuid
- The app layer sets this via:
    SET LOCAL app.current_tenant_id = '<uuid>';
  inside an explicit transaction (SET LOCAL resets at transaction end —
  unlike SET SESSION which would persist across pooled connections).

Append-only enforcement
-----------------------
PostgreSQL triggers on `price_histories` and `audit_logs` raise an
exception on any UPDATE or DELETE attempt, making immutability a
DB-level guarantee rather than an application convention.
"""

from alembic import op

revision: str = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


# ── Tenant-scoped tables that need RLS ────────────────────────────────────────
TENANT_SCOPED_TABLES = [
    "users",
    "my_products",
    "product_mappings",
    "pricing_rules",
    "price_histories",
    "audit_logs",
]

# ── Tables that are append-only (no UPDATE or DELETE ever) ───────────────────
APPEND_ONLY_TABLES = [
    "price_histories",
    "audit_logs",
]


def upgrade() -> None:
    # ── 1. Grant app_user usage on public schema ───────────────────────────────
    op.execute("GRANT USAGE ON SCHEMA public TO app_user")
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO app_user"
    )

    # ── 2. Enable RLS on every tenant-scoped table ────────────────────────────
    for table in TENANT_SCOPED_TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        # FORCE ensures even the table owner (migration_user) is subject to RLS
        # when connected as app_user.  The migration_user bypasses via BYPASSRLS.
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")

        # SELECT / INSERT / UPDATE / DELETE policy — one unified USING clause.
        # WITH CHECK ensures inserted/updated rows also satisfy the predicate.
        op.execute(f"""
            CREATE POLICY tenant_isolation ON {table}
                AS PERMISSIVE
                FOR ALL
                TO app_user
                USING (
                    tenant_id = current_setting('app.current_tenant_id', true)::uuid
                )
                WITH CHECK (
                    tenant_id = current_setting('app.current_tenant_id', true)::uuid
                )
        """)

    # ── 3. Create append-only trigger function ────────────────────────────────
    op.execute("""
        CREATE OR REPLACE FUNCTION enforce_append_only()
        RETURNS TRIGGER
        LANGUAGE plpgsql
        AS $$
        BEGIN
            IF TG_OP = 'UPDATE' THEN
                RAISE EXCEPTION
                    'UPDATE is not allowed on append-only table "%"', TG_TABLE_NAME
                    USING ERRCODE = 'restrict_violation';
            END IF;
            IF TG_OP = 'DELETE' THEN
                RAISE EXCEPTION
                    'DELETE is not allowed on append-only table "%"', TG_TABLE_NAME
                    USING ERRCODE = 'restrict_violation';
            END IF;
            RETURN NULL;
        END;
        $$
    """)

    # ── 4. Attach trigger to append-only tables ───────────────────────────────
    for table in APPEND_ONLY_TABLES:
        op.execute(f"""
            CREATE TRIGGER trg_{table}_append_only
            BEFORE UPDATE OR DELETE ON {table}
            FOR EACH ROW EXECUTE FUNCTION enforce_append_only()
        """)

    # ── 5. Extra security: revoke UPDATE/DELETE from app_user on audit_logs ──
    # Belt-and-suspenders — the trigger is the primary guard, this is secondary.
    op.execute("REVOKE UPDATE, DELETE ON audit_logs FROM app_user")
    op.execute("REVOKE UPDATE, DELETE ON price_histories FROM app_user")


def downgrade() -> None:
    # Remove triggers
    for table in APPEND_ONLY_TABLES:
        op.execute(f"DROP TRIGGER IF EXISTS trg_{table}_append_only ON {table}")

    op.execute("DROP FUNCTION IF EXISTS enforce_append_only()")

    # Restore grants before removing policies
    op.execute("GRANT UPDATE, DELETE ON audit_logs TO app_user")
    op.execute("GRANT UPDATE, DELETE ON price_histories TO app_user")

    # Drop RLS policies and disable RLS
    for table in TENANT_SCOPED_TABLES:
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {table}")
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")
