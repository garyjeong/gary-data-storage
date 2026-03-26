import asyncio
from typing import List, Optional

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel
from sqlalchemy import select, desc
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.connection import get_db
from src.db.models import CollectionLog

router = APIRouter()


class TriggerRequest(BaseModel):
    sources: Optional[List[str]] = None
    region_codes: Optional[List[str]] = None


async def run_collection(manager, sources: Optional[List[str]], region_codes: Optional[List[str]]):
    """Run collection in background using CollectorManager."""
    import logging
    from src.db.connection import async_session

    logger = logging.getLogger(__name__)
    try:
        if manager is None:
            return
        async with async_session() as db:
            if sources and sources != ["all"]:
                for source_name in sources:
                    results = await manager.collect_source(
                        db, source_name, region_codes=region_codes, triggered_by="manual"
                    )
                    success = sum(1 for r in results if r.status == "success")
                    logger.info(f"[manual] {source_name}: {success}/{len(results)} success")
            else:
                results = await manager.collect_all(db, triggered_by="manual")
                success = sum(1 for r in results if r.status == "success")
                logger.info(f"[manual] all sources: {success}/{len(results)} success")
            await db.commit()
    except Exception as exc:
        logger.error(f"[trigger] collection error: {exc}", exc_info=True)


@router.post("/collect")
async def trigger_collection(
    request: Request,
    body: TriggerRequest,
    db: AsyncSession = Depends(get_db),
):
    manager = request.app.state.manager
    asyncio.create_task(run_collection(manager, body.sources, body.region_codes))
    return {
        "status": "started",
        "sources": body.sources,
        "region_codes": body.region_codes,
    }


@router.get("/logs")
async def get_logs(db: AsyncSession = Depends(get_db), limit: int = 50):
    result = await db.execute(
        select(CollectionLog).order_by(desc(CollectionLog.started_at)).limit(limit)
    )
    logs = result.scalars().all()
    return [
        {
            "id": log.id,
            "source": log.source,
            "region_code": log.region_code,
            "started_at": log.started_at.isoformat() if log.started_at else None,
            "finished_at": log.finished_at.isoformat() if log.finished_at else None,
            "records_collected": log.records_collected,
            "status": log.status,
            "error_message": log.error_message,
        }
        for log in logs
    ]


@router.get("/status")
async def get_status(request: Request):
    from src.scheduler.jobs import get_scheduler_status

    manager = request.app.state.manager
    return {
        "scheduler": get_scheduler_status(),
        "registered_sources": list(manager.collectors.keys()) if manager else [],
    }
