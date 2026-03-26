"""initial create all tables

Revision ID: 001_initial
Revises:
Create Date: 2026-03-26 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "001_initial"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # --- regions ---
    op.create_table(
        "regions",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("name", sa.String(50), nullable=False),
        sa.Column("region_code", sa.String(5), nullable=False),
        sa.Column("parent_area", sa.String(20), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=True,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=True,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("region_code"),
    )

    # --- schedules ---
    op.create_table(
        "schedules",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("source_type", sa.String(30), nullable=True),
        sa.Column("interval_minutes", sa.Integer(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=True),
        sa.Column("last_run_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("next_run_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=True,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=True,
        ),
        sa.PrimaryKeyConstraint("id"),
    )

    # --- apt_transactions ---
    op.create_table(
        "apt_transactions",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("source", sa.String(20), nullable=False),
        sa.Column("transaction_type", sa.String(10), nullable=False),
        sa.Column("region_code", sa.String(5), nullable=False),
        sa.Column("dong_name", sa.String(50), nullable=True),
        sa.Column("apt_name", sa.String(100), nullable=False),
        sa.Column("exclusive_area", sa.Numeric(10, 2), nullable=True),
        sa.Column("floor", sa.Integer(), nullable=True),
        sa.Column("deal_amount", sa.Integer(), nullable=True),
        sa.Column("deposit", sa.Integer(), nullable=True),
        sa.Column("monthly_rent", sa.Integer(), nullable=True),
        sa.Column("deal_year", sa.Integer(), nullable=False),
        sa.Column("deal_month", sa.Integer(), nullable=False),
        sa.Column("deal_day", sa.Integer(), nullable=True),
        sa.Column("build_year", sa.Integer(), nullable=True),
        sa.Column("jibun", sa.String(50), nullable=True),
        sa.Column("road_name", sa.String(100), nullable=True),
        sa.Column("cancel_deal_type", sa.String(10), nullable=True),
        sa.Column("contract_date", sa.Date(), nullable=True),
        sa.Column("raw_data", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column(
            "collected_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=True,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=True,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_apt_transactions_unique_deal",
        "apt_transactions",
        ["source", "transaction_type", "region_code", "apt_name", "exclusive_area",
         "deal_year", "deal_month", "deal_day", "floor"],
        unique=True,
        postgresql_where=sa.text("deal_day IS NOT NULL"),
    )
    op.create_index(
        "ix_apt_transactions_region_type",
        "apt_transactions",
        ["region_code", "transaction_type"],
        unique=False,
    )
    op.create_index(
        "ix_apt_transactions_deal_period",
        "apt_transactions",
        ["deal_year", "deal_month"],
        unique=False,
    )
    op.create_index(
        "ix_apt_transactions_apt_region",
        "apt_transactions",
        ["apt_name", "region_code"],
        unique=False,
    )

    # --- apt_listings ---
    op.create_table(
        "apt_listings",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("source", sa.String(20), nullable=False),
        sa.Column("listing_type", sa.String(10), nullable=False),
        sa.Column("region_code", sa.String(5), nullable=False),
        sa.Column("dong_name", sa.String(50), nullable=True),
        sa.Column("apt_name", sa.String(100), nullable=False),
        sa.Column("exclusive_area", sa.Numeric(10, 2), nullable=True),
        sa.Column("floor", sa.Integer(), nullable=True),
        sa.Column("asking_price", sa.Integer(), nullable=True),
        sa.Column("deposit", sa.Integer(), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("source_listing_id", sa.String(50), nullable=True),
        sa.Column("listing_url", sa.Text(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=True),
        sa.Column("listed_at", sa.Date(), nullable=True),
        sa.Column("raw_data", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column(
            "collected_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=True,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=True,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_apt_listings_unique_source_id",
        "apt_listings",
        ["source", "source_listing_id"],
        unique=True,
        postgresql_where=sa.text("source_listing_id IS NOT NULL"),
    )
    op.create_index(
        "ix_apt_listings_region_type",
        "apt_listings",
        ["region_code", "listing_type"],
        unique=False,
    )

    # --- apt_complexes ---
    op.create_table(
        "apt_complexes",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("source", sa.String(20), nullable=False),
        sa.Column("region_code", sa.String(5), nullable=False),
        sa.Column("dong_name", sa.String(50), nullable=True),
        sa.Column("apt_name", sa.String(100), nullable=False),
        sa.Column("address", sa.Text(), nullable=True),
        sa.Column("total_units", sa.Integer(), nullable=True),
        sa.Column("total_dong", sa.Integer(), nullable=True),
        sa.Column("build_year", sa.Integer(), nullable=True),
        sa.Column("floor_area_max", sa.Numeric(10, 2), nullable=True),
        sa.Column("floor_area_min", sa.Numeric(10, 2), nullable=True),
        sa.Column("latitude", sa.Numeric(10, 7), nullable=True),
        sa.Column("longitude", sa.Numeric(10, 7), nullable=True),
        sa.Column("source_complex_id", sa.String(50), nullable=True),
        sa.Column("raw_data", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column(
            "collected_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=True,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=True,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_apt_complexes_unique_source_id",
        "apt_complexes",
        ["source", "source_complex_id"],
        unique=True,
        postgresql_where=sa.text("source_complex_id IS NOT NULL"),
    )
    op.create_index(
        "ix_apt_complexes_region_code",
        "apt_complexes",
        ["region_code"],
        unique=False,
    )

    # --- buildings ---
    op.create_table(
        "buildings",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("region_code", sa.String(5), nullable=False),
        sa.Column("dong_code", sa.String(10), nullable=True),
        sa.Column("apt_name", sa.String(100), nullable=True),
        sa.Column("main_purpose", sa.String(50), nullable=True),
        sa.Column("structure", sa.String(50), nullable=True),
        sa.Column("ground_floors", sa.Integer(), nullable=True),
        sa.Column("underground_floors", sa.Integer(), nullable=True),
        sa.Column("total_area", sa.Numeric(12, 2), nullable=True),
        sa.Column("build_date", sa.Date(), nullable=True),
        sa.Column("raw_data", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column(
            "collected_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=True,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=True,
        ),
        sa.PrimaryKeyConstraint("id"),
    )

    # --- official_prices ---
    op.create_table(
        "official_prices",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("region_code", sa.String(5), nullable=False),
        sa.Column("dong_name", sa.String(50), nullable=True),
        sa.Column("apt_name", sa.String(100), nullable=False),
        sa.Column("exclusive_area", sa.Numeric(10, 2), nullable=True),
        sa.Column("price_year", sa.Integer(), nullable=False),
        sa.Column("official_price", sa.Integer(), nullable=False),
        sa.Column("raw_data", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column(
            "collected_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=True,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=True,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "region_code",
            "apt_name",
            "exclusive_area",
            "price_year",
            name="uq_official_prices_key",
        ),
    )

    # --- price_statistics ---
    op.create_table(
        "price_statistics",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("source", sa.String(20), nullable=False),
        sa.Column("stat_type", sa.String(30), nullable=False),
        sa.Column("region_code", sa.String(5), nullable=True),
        sa.Column("region_name", sa.String(50), nullable=False),
        sa.Column("period", sa.String(7), nullable=False),
        sa.Column("value", sa.Numeric(12, 4), nullable=True),
        sa.Column("base_date", sa.String(10), nullable=True),
        sa.Column("raw_data", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column(
            "collected_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=True,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=True,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "source",
            "stat_type",
            "region_name",
            "period",
            name="uq_price_statistics_key",
        ),
    )

    # --- collection_logs ---
    op.create_table(
        "collection_logs",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("source", sa.String(30), nullable=False),
        sa.Column("region_code", sa.String(5), nullable=True),
        sa.Column("status", sa.String(10), nullable=False),
        sa.Column("records_collected", sa.Integer(), nullable=True),
        sa.Column("records_inserted", sa.Integer(), nullable=True),
        sa.Column("records_updated", sa.Integer(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("duration_seconds", sa.Numeric(8, 2), nullable=True),
        sa.Column("triggered_by", sa.String(10), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_collection_logs_source_started",
        "collection_logs",
        ["source", "started_at"],
        unique=False,
        postgresql_ops={"started_at": "DESC"},
    )
    op.create_index(
        "ix_collection_logs_started_at",
        "collection_logs",
        ["started_at"],
        unique=False,
        postgresql_ops={"started_at": "DESC"},
    )


def downgrade() -> None:
    # Drop indexes first, then tables (reverse order of creation)
    op.drop_index("ix_collection_logs_started_at", table_name="collection_logs")
    op.drop_index("ix_collection_logs_source_started", table_name="collection_logs")
    op.drop_table("collection_logs")

    op.drop_table("price_statistics")

    op.drop_table("official_prices")

    op.drop_table("buildings")

    op.drop_index("ix_apt_complexes_region_code", table_name="apt_complexes")
    op.drop_index("ix_apt_complexes_unique_source_id", table_name="apt_complexes")
    op.drop_table("apt_complexes")

    op.drop_index("ix_apt_listings_region_type", table_name="apt_listings")
    op.drop_index("ix_apt_listings_unique_source_id", table_name="apt_listings")
    op.drop_table("apt_listings")

    op.drop_index("ix_apt_transactions_apt_region", table_name="apt_transactions")
    op.drop_index("ix_apt_transactions_deal_period", table_name="apt_transactions")
    op.drop_index("ix_apt_transactions_region_type", table_name="apt_transactions")
    op.drop_index("ix_apt_transactions_unique_deal", table_name="apt_transactions")
    op.drop_table("apt_transactions")

    op.drop_table("schedules")

    op.drop_table("regions")
