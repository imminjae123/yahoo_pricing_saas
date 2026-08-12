"""
Initial table creation — all 8 tables for Yahoo! Pricing SaaS.

Revision ID: 0001
Revises:     — (first migration)
Create Date: 2025-08

Tables created
--------------
1. tenants
2. users
3. my_products
4. competitor_products
5. product_mappings
6. pricing_rules
7. price_histories
8. audit_logs
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── 0. Enable pgcrypto for gen_random_uuid() ─────────────────────────────
    op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")

    # ── 1. tenants ────────────────────────────────────────────────────────────
    op.create_table(
        "tenants",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            primary_key=True,
            nullable=False,
        ),
        sa.Column("shop_name", sa.String(255), nullable=False),
        sa.Column("shop_code", sa.String(100), nullable=False),
        sa.Column("shop_url", sa.String(500), nullable=True),
        sa.Column(
            "is_active",
            sa.Boolean(),
            server_default=sa.text("true"),
            nullable=False,
        ),
        sa.Column("contact_email", sa.String(255), nullable=True),
        sa.Column("line_user_id", sa.String(100), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_unique_constraint("uq_tenants_shop_code", "tenants", ["shop_code"])

    # ── 2. users ──────────────────────────────────────────────────────────────
    op.create_table(
        "users",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            primary_key=True,
            nullable=False,
        ),
        sa.Column(
            "tenant_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("email", sa.String(255), nullable=False),
        sa.Column("hashed_password", sa.String(255), nullable=False),
        sa.Column("full_name", sa.String(255), nullable=True),
        sa.Column(
            "role",
            sa.String(20),
            server_default=sa.text("'VIEWER'"),
            nullable=False,
            comment="OWNER | MANAGER | VIEWER",
        ),
        sa.Column(
            "is_active",
            sa.Boolean(),
            server_default=sa.text("true"),
            nullable=False,
        ),
        sa.Column("line_user_id", sa.String(100), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index("uq_users_tenant_email", "users", ["tenant_id", "email"], unique=True)
    op.create_index("ix_users_tenant_id", "users", ["tenant_id"])

    # ── 3. my_products ────────────────────────────────────────────────────────
    op.create_table(
        "my_products",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            primary_key=True,
            nullable=False,
        ),
        sa.Column(
            "tenant_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("sku", sa.String(100), nullable=False),
        sa.Column("name", sa.String(500), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column(
            "yahoo_item_code",
            sa.String(255),
            nullable=True,
            comment="Seller's own Yahoo! Shopping item code",
        ),
        sa.Column(
            "cost",
            sa.Numeric(12, 2),
            nullable=False,
            comment="Purchase/manufacturing cost (¥)",
        ),
        sa.Column(
            "min_margin_amount",
            sa.Numeric(12, 2),
            server_default=sa.text("0"),
            nullable=False,
            comment="Minimum profit floor in ¥",
        ),
        sa.Column(
            "min_margin_rate",
            sa.Numeric(6, 4),
            nullable=True,
            comment="Optional margin floor as ratio e.g. 0.15",
        ),
        sa.Column(
            "defensive_price",
            sa.Numeric(12, 2),
            server_default=sa.text("0"),
            nullable=False,
            comment="Pre-computed floor = cost + min_margin_amount",
        ),
        sa.Column(
            "current_price",
            sa.Numeric(12, 2),
            nullable=True,
            comment="Last rule-engine recommended price",
        ),
        sa.Column(
            "is_active",
            sa.Boolean(),
            server_default=sa.text("true"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index(
        "uq_my_products_tenant_sku", "my_products", ["tenant_id", "sku"], unique=True
    )
    op.create_index("ix_my_products_tenant_id", "my_products", ["tenant_id"])

    # ── 4. competitor_products ────────────────────────────────────────────────
    op.create_table(
        "competitor_products",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            primary_key=True,
            nullable=False,
        ),
        sa.Column(
            "yahoo_item_code",
            sa.String(255),
            nullable=False,
            unique=True,
            comment="Yahoo! Shopping item code — stable polling key",
        ),
        sa.Column("shop_code", sa.String(100), nullable=True),
        sa.Column("shop_name", sa.String(255), nullable=True),
        sa.Column("item_name", sa.String(1000), nullable=True),
        sa.Column("item_url", sa.String(1000), nullable=True),
        sa.Column(
            "latest_price",
            sa.Integer(),
            nullable=True,
            comment="Most recent crawled price (¥)",
        ),
        sa.Column(
            "all_time_low_price",
            sa.Integer(),
            nullable=True,
            comment="Triggers LINE notification when updated",
        ),
        sa.Column(
            "is_active",
            sa.Boolean(),
            server_default=sa.text("true"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_competitor_products_item_code",
        "competitor_products",
        ["yahoo_item_code"],
        unique=True,
    )
    op.create_index(
        "ix_competitor_products_shop_code", "competitor_products", ["shop_code"]
    )

    # ── 5. product_mappings ───────────────────────────────────────────────────
    op.create_table(
        "product_mappings",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            primary_key=True,
            nullable=False,
        ),
        sa.Column(
            "tenant_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "my_product_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("my_products.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "competitor_product_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("competitor_products.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "is_primary",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.Column("label", sa.String(100), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index(
        "uq_product_mappings_tenant_pair",
        "product_mappings",
        ["tenant_id", "my_product_id", "competitor_product_id"],
        unique=True,
    )
    op.create_index("ix_product_mappings_tenant_id", "product_mappings", ["tenant_id"])
    op.create_index(
        "ix_product_mappings_my_product_id", "product_mappings", ["my_product_id"]
    )
    op.create_index(
        "ix_product_mappings_competitor_product_id",
        "product_mappings",
        ["competitor_product_id"],
    )

    # ── 6. pricing_rules ──────────────────────────────────────────────────────
    op.create_table(
        "pricing_rules",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            primary_key=True,
            nullable=False,
        ),
        sa.Column(
            "tenant_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "my_product_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("my_products.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column(
            "condition",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            comment="Rule strategy + adjustment + constraints",
        ),
        sa.Column(
            "priority",
            sa.Integer(),
            server_default=sa.text("100"),
            nullable=False,
            comment="Lower = higher priority",
        ),
        sa.Column(
            "is_active",
            sa.Boolean(),
            server_default=sa.text("true"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index(
        "uq_pricing_rules_tenant_name",
        "pricing_rules",
        ["tenant_id", "name"],
        unique=True,
    )
    op.create_index("ix_pricing_rules_tenant_id", "pricing_rules", ["tenant_id"])
    op.create_index(
        "ix_pricing_rules_my_product_id", "pricing_rules", ["my_product_id"]
    )
    op.create_index(
        "ix_pricing_rules_condition_gin",
        "pricing_rules",
        ["condition"],
        postgresql_using="gin",
    )

    # ── 7. price_histories ────────────────────────────────────────────────────
    op.create_table(
        "price_histories",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            primary_key=True,
            nullable=False,
        ),
        sa.Column(
            "tenant_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "product_ref_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
            comment="FK to my_products.id or competitor_products.id",
        ),
        sa.Column(
            "product_type",
            sa.String(20),
            nullable=False,
            comment="MY | COMPETITOR",
        ),
        sa.Column(
            "price",
            sa.Integer(),
            nullable=False,
            comment="Crawled market price (¥)",
        ),
        sa.Column(
            "recommended_price",
            sa.Integer(),
            nullable=True,
            comment="Rule-engine output price (¥)",
        ),
        sa.Column(
            "source",
            sa.String(20),
            server_default=sa.text("'CRAWL'"),
            nullable=False,
            comment="CRAWL | MANUAL | RULE",
        ),
        sa.Column(
            "rule_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("pricing_rules.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "captured_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "is_all_time_low",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_price_histories_product_time",
        "price_histories",
        ["product_ref_id", "captured_at"],
    )
    op.create_index(
        "ix_price_histories_tenant_time",
        "price_histories",
        ["tenant_id", "captured_at"],
    )
    op.create_index("ix_price_histories_rule_id", "price_histories", ["rule_id"])

    # ── 8. audit_logs ─────────────────────────────────────────────────────────
    op.create_table(
        "audit_logs",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            primary_key=True,
            nullable=False,
        ),
        sa.Column(
            "tenant_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "actor_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "entity_type",
            sa.String(50),
            nullable=False,
            comment="PRICING_RULE | MY_PRODUCT | COMPETITOR_PRODUCT | PRICE_RECOMMENDATION",
        ),
        sa.Column(
            "entity_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
            comment="PK of the affected entity row",
        ),
        sa.Column(
            "action",
            sa.String(50),
            nullable=False,
            comment="RULE_CREATED | RULE_UPDATED | RULE_FIRED | ALL_TIME_LOW | ...",
        ),
        sa.Column(
            "details",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "occurred_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
            index=True,
        ),
    )
    op.create_index(
        "ix_audit_logs_tenant_occurred",
        "audit_logs",
        ["tenant_id", "occurred_at"],
    )
    op.create_index("ix_audit_logs_actor_user_id", "audit_logs", ["actor_user_id"])
    op.create_index(
        "ix_audit_logs_entity", "audit_logs", ["entity_type", "entity_id"]
    )
    op.create_index(
        "ix_audit_logs_details_gin",
        "audit_logs",
        ["details"],
        postgresql_using="gin",
    )


def downgrade() -> None:
    op.drop_table("audit_logs")
    op.drop_table("price_histories")
    op.drop_table("pricing_rules")
    op.drop_table("product_mappings")
    op.drop_table("competitor_products")
    op.drop_table("my_products")
    op.drop_table("users")
    op.drop_table("tenants")
