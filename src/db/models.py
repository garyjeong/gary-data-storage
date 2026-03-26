from datetime import datetime, date
from decimal import Decimal
from typing import Optional

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, mapped_column, Mapped


class Base(DeclarativeBase):
    pass


class Region(Base):
    __tablename__ = "regions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(50), nullable=False)
    region_code: Mapped[str] = mapped_column(String(5), nullable=False, unique=True)
    parent_area: Mapped[str] = mapped_column(String(20), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class Schedule(Base):
    __tablename__ = "schedules"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    source_type: Mapped[Optional[str]] = mapped_column(String(30), nullable=True)
    interval_minutes: Mapped[int] = mapped_column(Integer, nullable=False, default=30)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    last_run_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    next_run_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class AptTransaction(Base):
    __tablename__ = "apt_transactions"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    source: Mapped[str] = mapped_column(String(20), nullable=False)
    transaction_type: Mapped[str] = mapped_column(String(10), nullable=False)
    region_code: Mapped[str] = mapped_column(String(5), nullable=False)
    dong_name: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    apt_name: Mapped[str] = mapped_column(String(100), nullable=False)
    exclusive_area: Mapped[Optional[Decimal]] = mapped_column(
        Numeric(10, 2), nullable=True
    )
    floor: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    deal_amount: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    deposit: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    monthly_rent: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    deal_year: Mapped[int] = mapped_column(Integer, nullable=False)
    deal_month: Mapped[int] = mapped_column(Integer, nullable=False)
    deal_day: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    build_year: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    jibun: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    road_name: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    cancel_deal_type: Mapped[Optional[str]] = mapped_column(String(10), nullable=True)
    contract_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    raw_data: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    collected_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        Index(
            "ix_apt_transactions_unique_deal",
            "source",
            "transaction_type",
            "region_code",
            "apt_name",
            "exclusive_area",
            "deal_year",
            "deal_month",
            "deal_day",
            "floor",
            unique=True,
            postgresql_where=text("deal_day IS NOT NULL"),
        ),
        Index(
            "ix_apt_transactions_region_type",
            "region_code",
            "transaction_type",
        ),
        Index(
            "ix_apt_transactions_deal_period",
            "deal_year",
            "deal_month",
        ),
        Index(
            "ix_apt_transactions_apt_region",
            "apt_name",
            "region_code",
        ),
    )


class AptListing(Base):
    __tablename__ = "apt_listings"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    source: Mapped[str] = mapped_column(String(20), nullable=False)
    listing_type: Mapped[str] = mapped_column(String(10), nullable=False)
    region_code: Mapped[str] = mapped_column(String(5), nullable=False)
    dong_name: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    apt_name: Mapped[str] = mapped_column(String(100), nullable=False)
    exclusive_area: Mapped[Optional[Decimal]] = mapped_column(
        Numeric(10, 2), nullable=True
    )
    floor: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    asking_price: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    deposit: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    source_listing_id: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    listing_url: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    listed_at: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    raw_data: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    collected_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        Index(
            "ix_apt_listings_unique_source_id",
            "source",
            "source_listing_id",
            unique=True,
            postgresql_where=text("source_listing_id IS NOT NULL"),
        ),
        Index(
            "ix_apt_listings_region_type",
            "region_code",
            "listing_type",
        ),
    )


class AptComplex(Base):
    __tablename__ = "apt_complexes"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    source: Mapped[str] = mapped_column(String(20), nullable=False)
    region_code: Mapped[str] = mapped_column(String(5), nullable=False)
    dong_name: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    apt_name: Mapped[str] = mapped_column(String(100), nullable=False)
    address: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    total_units: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    total_dong: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    build_year: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    floor_area_max: Mapped[Optional[Decimal]] = mapped_column(
        Numeric(10, 2), nullable=True
    )
    floor_area_min: Mapped[Optional[Decimal]] = mapped_column(
        Numeric(10, 2), nullable=True
    )
    latitude: Mapped[Optional[Decimal]] = mapped_column(Numeric(10, 7), nullable=True)
    longitude: Mapped[Optional[Decimal]] = mapped_column(Numeric(10, 7), nullable=True)
    source_complex_id: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    raw_data: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    collected_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        Index(
            "ix_apt_complexes_unique_source_id",
            "source",
            "source_complex_id",
            unique=True,
            postgresql_where=text("source_complex_id IS NOT NULL"),
        ),
        Index(
            "ix_apt_complexes_region_code",
            "region_code",
        ),
    )


class Building(Base):
    __tablename__ = "buildings"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    region_code: Mapped[str] = mapped_column(String(5), nullable=False)
    dong_code: Mapped[Optional[str]] = mapped_column(String(10), nullable=True)
    apt_name: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    main_purpose: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    structure: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    ground_floors: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    underground_floors: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    total_area: Mapped[Optional[Decimal]] = mapped_column(Numeric(12, 2), nullable=True)
    build_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    raw_data: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    collected_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class OfficialPrice(Base):
    __tablename__ = "official_prices"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    region_code: Mapped[str] = mapped_column(String(5), nullable=False)
    dong_name: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    apt_name: Mapped[str] = mapped_column(String(100), nullable=False)
    exclusive_area: Mapped[Optional[Decimal]] = mapped_column(
        Numeric(10, 2), nullable=True
    )
    price_year: Mapped[int] = mapped_column(Integer, nullable=False)
    official_price: Mapped[int] = mapped_column(Integer, nullable=False)
    raw_data: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    collected_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        UniqueConstraint(
            "region_code",
            "apt_name",
            "exclusive_area",
            "price_year",
            name="uq_official_prices_key",
        ),
    )


class PriceStatistic(Base):
    __tablename__ = "price_statistics"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    source: Mapped[str] = mapped_column(String(20), nullable=False, default="reb")
    stat_type: Mapped[str] = mapped_column(String(30), nullable=False)
    region_code: Mapped[Optional[str]] = mapped_column(String(5), nullable=True)
    region_name: Mapped[str] = mapped_column(String(50), nullable=False)
    period: Mapped[str] = mapped_column(String(7), nullable=False)
    value: Mapped[Optional[Decimal]] = mapped_column(Numeric(12, 4), nullable=True)
    base_date: Mapped[Optional[str]] = mapped_column(String(10), nullable=True)
    raw_data: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    collected_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        UniqueConstraint(
            "source",
            "stat_type",
            "region_name",
            "period",
            name="uq_price_statistics_key",
        ),
    )


class CollectionLog(Base):
    __tablename__ = "collection_logs"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    source: Mapped[str] = mapped_column(String(30), nullable=False)
    region_code: Mapped[Optional[str]] = mapped_column(String(5), nullable=True)
    status: Mapped[str] = mapped_column(String(10), nullable=False)
    records_collected: Mapped[int] = mapped_column(Integer, default=0)
    records_inserted: Mapped[int] = mapped_column(Integer, default=0)
    records_updated: Mapped[int] = mapped_column(Integer, default=0)
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    duration_seconds: Mapped[Optional[Decimal]] = mapped_column(
        Numeric(8, 2), nullable=True
    )
    triggered_by: Mapped[str] = mapped_column(String(10), nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    finished_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    __table_args__ = (
        Index(
            "ix_collection_logs_source_started",
            "source",
            "started_at",
            postgresql_ops={"started_at": "DESC"},
        ),
        Index(
            "ix_collection_logs_started_at",
            "started_at",
            postgresql_ops={"started_at": "DESC"},
        ),
    )
