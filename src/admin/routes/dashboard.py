from fastapi import APIRouter, Request, Depends
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select, func, desc
from sqlalchemy.ext.asyncio import AsyncSession
from pathlib import Path

from src.db.connection import get_db
from src.db.models import Region, Schedule, CollectionLog

router = APIRouter()
templates = Jinja2Templates(directory=str(Path(__file__).parent.parent / "templates"))


@router.get("/", response_class=HTMLResponse)
async def dashboard(request: Request, db: AsyncSession = Depends(get_db)):
    # Get stats
    total_regions = await db.scalar(select(func.count(Region.id)))
    active_regions = await db.scalar(
        select(func.count(Region.id)).where(Region.is_active == True)
    )

    # Get recent logs
    result = await db.execute(
        select(CollectionLog).order_by(desc(CollectionLog.started_at)).limit(20)
    )
    recent_logs = result.scalars().all()

    # Get scheduler status
    from src.scheduler.jobs import get_scheduler_status

    scheduler_status = get_scheduler_status()

    return templates.TemplateResponse(
        request,
        "dashboard.html",
        {
            "total_regions": total_regions,
            "active_regions": active_regions,
            "recent_logs": recent_logs,
            "scheduler": scheduler_status,
        },
    )


@router.get("/regions", response_class=HTMLResponse)
async def regions_page(request: Request, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(Region).order_by(Region.parent_area, Region.name)
    )
    regions = result.scalars().all()
    return templates.TemplateResponse(
        request,
        "regions.html",
        {
            "regions": regions,
        },
    )


@router.get("/schedules", response_class=HTMLResponse)
async def schedules_page(request: Request, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Schedule).order_by(Schedule.id))
    schedules = result.scalars().all()

    # Get registered source names from app state
    manager = request.app.state.manager
    source_names = list(manager.collectors.keys()) if manager else []

    return templates.TemplateResponse(
        request,
        "schedules.html",
        {
            "schedules": schedules,
            "source_names": source_names,
        },
    )
