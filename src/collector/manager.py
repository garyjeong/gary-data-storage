import asyncio
import logging
import time
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.collector.base import BaseCollector, CollectionResult
from src.db.models import Region, CollectionLog

logger = logging.getLogger(__name__)


class CollectorManager:
    """Orchestrates collection across all sources and regions."""

    def __init__(self, collectors: list[BaseCollector] | None = None):
        self._collectors: dict[str, BaseCollector] = {}
        if collectors:
            for c in collectors:
                self.register(c)

    def register(self, collector: BaseCollector) -> None:
        """Register a collector source."""
        self._collectors[collector.source_name] = collector
        logger.info(f"Registered collector: {collector.source_name}")

    @property
    def collectors(self) -> dict[str, BaseCollector]:
        return self._collectors

    async def collect_all(self, db: AsyncSession, triggered_by: str = "scheduler") -> list[CollectionResult]:
        """Run all active collectors for all active regions."""
        regions = await self._get_active_regions(db)
        if not regions:
            logger.warning("No active regions found")
            return []

        results = []
        for name, collector in self._collectors.items():
            for region in regions:
                result = await self._run_collector(collector, region.region_code, triggered_by)
                await self._log_result(db, result, triggered_by)
                results.append(result)
                # Rate limiting
                delay = self._get_delay(name)
                if delay > 0:
                    await asyncio.sleep(delay)

        return results

    async def collect_source(
        self, db: AsyncSession, source_name: str,
        region_codes: list[str] | None = None,
        triggered_by: str = "manual"
    ) -> list[CollectionResult]:
        """Run a specific collector for specified or all active regions."""
        collector = self._collectors.get(source_name)
        if not collector:
            return [CollectionResult(
                source=source_name, region_code=None,
                status="error", error_message=f"Unknown source: {source_name}"
            )]

        if region_codes:
            codes = region_codes
        else:
            regions = await self._get_active_regions(db)
            codes = [r.region_code for r in regions]

        results = []
        for code in codes:
            result = await self._run_collector(collector, code, triggered_by)
            await self._log_result(db, result, triggered_by)
            results.append(result)
            delay = self._get_delay(source_name)
            if delay > 0:
                await asyncio.sleep(delay)

        return results

    async def _run_collector(
        self, collector: BaseCollector, region_code: str, triggered_by: str
    ) -> CollectionResult:
        """Run a single collector with error handling."""
        start = time.time()
        try:
            result = await collector.collect(region_code)
            result.duration_seconds = time.time() - start
            return result
        except Exception as e:
            logger.error(f"Collector {collector.source_name} failed for {region_code}: {e}")
            return CollectionResult(
                source=collector.source_name,
                region_code=region_code,
                status="error",
                error_message=str(e),
                duration_seconds=time.time() - start,
            )

    async def _get_active_regions(self, db: AsyncSession) -> list[Region]:
        """Get all active regions from DB."""
        result = await db.execute(
            select(Region).where(Region.is_active == True).order_by(Region.parent_area, Region.name)
        )
        return list(result.scalars().all())

    async def _log_result(self, db: AsyncSession, result: CollectionResult, triggered_by: str) -> None:
        """Save collection result to logs table."""
        log = CollectionLog(
            source=result.source,
            region_code=result.region_code,
            status=result.status,
            records_collected=result.records_collected,
            records_inserted=result.records_inserted,
            records_updated=result.records_updated,
            error_message=result.error_message,
            duration_seconds=result.duration_seconds,
            triggered_by=triggered_by,
            started_at=result.started_at,
            finished_at=datetime.now(),
        )
        db.add(log)
        await db.flush()

    def _get_delay(self, source_name: str) -> float:
        """Return delay between requests (higher for private platforms)."""
        private_sources = {"naver", "zigbang", "hogangnono"}
        if source_name in private_sources:
            return 2.0
        return 0.5
