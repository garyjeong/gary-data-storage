from fastapi import APIRouter, Request, Depends, Query
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select, func, desc, and_
from sqlalchemy.ext.asyncio import AsyncSession
from pathlib import Path
from typing import Optional

from src.db.connection import get_db
from src.db.models import AptTransaction, PriceStatistic, AptListing, AptComplex, Building, Region

router = APIRouter()
templates = Jinja2Templates(directory=str(Path(__file__).parent.parent / "templates"))


@router.get("/data", response_class=HTMLResponse)
async def data_page(request: Request, db: AsyncSession = Depends(get_db)):
    """Data viewer page."""
    # Get regions for filter dropdown
    result = await db.execute(
        select(Region).where(Region.is_active == True).order_by(Region.parent_area, Region.name)
    )
    regions = result.scalars().all()

    # Quick summary counts
    summary = {
        "transactions": await db.scalar(select(func.count(AptTransaction.id))) or 0,
        "listings": await db.scalar(select(func.count(AptListing.id))) or 0,
        "complexes": await db.scalar(select(func.count(AptComplex.id))) or 0,
        "buildings": await db.scalar(select(func.count(Building.id))) or 0,
        "statistics": await db.scalar(select(func.count(PriceStatistic.id))) or 0,
    }

    return templates.TemplateResponse(
        request,
        "data.html",
        {"regions": regions, "summary": summary},
    )


@router.get("/api/data/transactions")
async def get_transactions(
    db: AsyncSession = Depends(get_db),
    region_code: Optional[str] = Query(None),
    transaction_type: Optional[str] = Query(None),
    deal_year: Optional[int] = Query(None),
    deal_month: Optional[int] = Query(None),
    apt_name: Optional[str] = Query(None),
    sort: str = Query("deal_date_desc"),
    page: int = Query(1, ge=1),
    per_page: int = Query(50, ge=10, le=200),
):
    """Query apartment transactions with filters and pagination."""
    query = select(AptTransaction)
    count_query = select(func.count(AptTransaction.id))

    conditions = []
    if region_code:
        conditions.append(AptTransaction.region_code == region_code)
    if transaction_type:
        conditions.append(AptTransaction.transaction_type == transaction_type)
    if deal_year:
        conditions.append(AptTransaction.deal_year == deal_year)
    if deal_month:
        conditions.append(AptTransaction.deal_month == deal_month)
    if apt_name:
        conditions.append(AptTransaction.apt_name.ilike(f"%{apt_name}%"))

    if conditions:
        query = query.where(and_(*conditions))
        count_query = count_query.where(and_(*conditions))

    total = await db.scalar(count_query) or 0

    # Sorting
    if sort == "price_desc":
        query = query.order_by(desc(AptTransaction.deal_amount))
    elif sort == "price_asc":
        query = query.order_by(AptTransaction.deal_amount)
    elif sort == "area_desc":
        query = query.order_by(desc(AptTransaction.exclusive_area))
    else:  # deal_date_desc
        query = query.order_by(
            desc(AptTransaction.deal_year),
            desc(AptTransaction.deal_month),
            desc(AptTransaction.deal_day),
        )

    offset = (page - 1) * per_page
    query = query.offset(offset).limit(per_page)

    result = await db.execute(query)
    rows = result.scalars().all()

    return {
        "total": total,
        "page": page,
        "per_page": per_page,
        "total_pages": max(1, (total + per_page - 1) // per_page),
        "items": [
            {
                "id": r.id,
                "source": r.source,
                "transaction_type": r.transaction_type,
                "region_code": r.region_code,
                "dong_name": r.dong_name,
                "apt_name": r.apt_name,
                "exclusive_area": float(r.exclusive_area) if r.exclusive_area else None,
                "floor": r.floor,
                "deal_amount": r.deal_amount,
                "deposit": r.deposit,
                "monthly_rent": r.monthly_rent,
                "deal_year": r.deal_year,
                "deal_month": r.deal_month,
                "deal_day": r.deal_day,
                "build_year": r.build_year,
            }
            for r in rows
        ],
    }


@router.get("/api/data/statistics")
async def get_statistics(
    db: AsyncSession = Depends(get_db),
    stat_type: Optional[str] = Query(None),
    region_name: Optional[str] = Query(None),
    period_from: Optional[str] = Query(None),
    period_to: Optional[str] = Query(None),
    page: int = Query(1, ge=1),
    per_page: int = Query(50, ge=10, le=200),
):
    """Query price statistics with filters."""
    query = select(PriceStatistic)
    count_query = select(func.count(PriceStatistic.id))

    conditions = []
    if stat_type:
        conditions.append(PriceStatistic.stat_type == stat_type)
    if region_name:
        conditions.append(PriceStatistic.region_name.ilike(f"%{region_name}%"))
    if period_from:
        conditions.append(PriceStatistic.period >= period_from)
    if period_to:
        conditions.append(PriceStatistic.period <= period_to)

    if conditions:
        query = query.where(and_(*conditions))
        count_query = count_query.where(and_(*conditions))

    total = await db.scalar(count_query) or 0

    query = query.order_by(desc(PriceStatistic.period), PriceStatistic.region_name)
    offset = (page - 1) * per_page
    query = query.offset(offset).limit(per_page)

    result = await db.execute(query)
    rows = result.scalars().all()

    return {
        "total": total,
        "page": page,
        "per_page": per_page,
        "total_pages": max(1, (total + per_page - 1) // per_page),
        "items": [
            {
                "id": r.id,
                "source": r.source,
                "stat_type": r.stat_type,
                "region_name": r.region_name,
                "period": r.period,
                "value": float(r.value) if r.value else None,
                "base_date": r.base_date,
            }
            for r in rows
        ],
    }


@router.get("/api/data/summary")
async def get_summary(db: AsyncSession = Depends(get_db)):
    """Quick summary of all collected data."""
    tx_by_type = {}
    result = await db.execute(
        select(
            AptTransaction.transaction_type,
            func.count(AptTransaction.id),
            func.min(AptTransaction.deal_amount),
            func.max(AptTransaction.deal_amount),
            func.round(func.avg(AptTransaction.deal_amount)),
        ).group_by(AptTransaction.transaction_type)
    )
    for row in result.all():
        tx_by_type[row[0]] = {
            "count": row[1],
            "min_price": row[2],
            "max_price": row[3],
            "avg_price": int(row[4]) if row[4] else None,
        }

    stat_by_type = {}
    result2 = await db.execute(
        select(
            PriceStatistic.stat_type,
            func.count(PriceStatistic.id),
            func.min(PriceStatistic.period),
            func.max(PriceStatistic.period),
        ).group_by(PriceStatistic.stat_type)
    )
    for row in result2.all():
        stat_by_type[row[0]] = {
            "count": row[1],
            "period_from": row[2],
            "period_to": row[3],
        }

    return {
        "transactions": tx_by_type,
        "statistics": stat_by_type,
        "totals": {
            "transactions": await db.scalar(select(func.count(AptTransaction.id))) or 0,
            "listings": await db.scalar(select(func.count(AptListing.id))) or 0,
            "complexes": await db.scalar(select(func.count(AptComplex.id))) or 0,
            "buildings": await db.scalar(select(func.count(Building.id))) or 0,
            "statistics": await db.scalar(select(func.count(PriceStatistic.id))) or 0,
        },
    }
