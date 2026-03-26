import logging
from datetime import datetime, timedelta

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger

from src.collector.manager import CollectorManager
from src.db.connection import async_session

logger = logging.getLogger(__name__)

scheduler = AsyncIOScheduler()


async def collection_job(manager: CollectorManager):
    """Job that runs the full collection cycle."""
    logger.info("Starting scheduled collection...")
    async with async_session() as db:
        try:
            results = await manager.collect_all(db, triggered_by="scheduler")
            await db.commit()
            success = sum(1 for r in results if r.status == "success")
            errors = sum(1 for r in results if r.status == "error")
            logger.info(
                f"Scheduled collection complete: {success} success, {errors} errors, {len(results)} total"
            )
        except Exception as e:
            logger.error(f"Scheduled collection failed: {e}")
            await db.rollback()


def setup_scheduler(manager: CollectorManager, interval_minutes: int = 30) -> AsyncIOScheduler:
    """Setup and return the scheduler with the collection job."""
    scheduler.add_job(
        collection_job,
        trigger=IntervalTrigger(minutes=interval_minutes),
        args=[manager],
        id="collection_job",
        name="Real Estate Collection",
        replace_existing=True,
        next_run_time=datetime.now() + timedelta(minutes=interval_minutes),  # Don't run immediately on startup
    )
    return scheduler


def update_interval(interval_minutes: int):
    """Update the collection interval dynamically."""
    job = scheduler.get_job("collection_job")
    if job:
        scheduler.reschedule_job(
            "collection_job",
            trigger=IntervalTrigger(minutes=interval_minutes),
        )
        logger.info(f"Collection interval updated to {interval_minutes} minutes")


def get_scheduler_status() -> dict:
    """Get current scheduler status."""
    job = scheduler.get_job("collection_job")
    return {
        "running": scheduler.running,
        "job_exists": job is not None,
        "next_run_time": str(job.next_run_time) if job else None,
        "interval_minutes": (
            job.trigger.interval.total_seconds() / 60
            if job and hasattr(job.trigger, "interval")
            else None
        ),
    }
