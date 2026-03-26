"""
Korea Real Estate Board (한국부동산원) Statistics Collector

Collects apartment price index data (매매/전세) from the Korean government
open API (apis.data.go.kr - B553547/real-estate-info/getRealEstatePriceIndex).
"""
import asyncio
import logging
from datetime import datetime
from decimal import Decimal, InvalidOperation

import httpx
from sqlalchemy.dialects.postgresql import insert

from src.collector.base import BaseCollector, CollectionResult
from src.config import settings
from src.db.connection import async_session
from src.db.models import PriceStatistic

logger = logging.getLogger(__name__)

_BASE_URL = (
    "http://apis.data.go.kr/B553547/real-estate-info/getRealEstatePriceIndex"
)
_NUM_OF_ROWS = 100
_MAX_RETRIES = 3

# tradeType codes
_TRADE_SALE = "01"
_TRADE_JEONSE = "02"

# stat_type values stored in DB
_STAT_SALE = "sale_index"
_STAT_JEONSE = "jeonse_index"

_TRADE_TYPE_MAP = {
    _TRADE_SALE: _STAT_SALE,
    _TRADE_JEONSE: _STAT_JEONSE,
}


def _clean(value) -> str | None:
    if value is None:
        return None
    stripped = str(value).strip()
    return stripped if stripped else None


def _to_decimal(value) -> Decimal | None:
    try:
        v = _clean(value)
        return Decimal(v) if v else None
    except (InvalidOperation, TypeError):
        return None


def _period_from_yyyymm(raw: str | None) -> str | None:
    """Convert 'YYYYMM' to 'YYYY-MM'. Returns None if parsing fails."""
    v = _clean(raw)
    if not v or len(v) < 6:
        return None
    try:
        return f"{v[:4]}-{v[4:6]}"
    except (IndexError, TypeError):
        return None


class RebCollector(BaseCollector):
    """Collects Korea Real Estate Board price index statistics from data.go.kr."""

    @property
    def source_name(self) -> str:
        return "reb"

    async def _fetch_page(
        self,
        client: httpx.AsyncClient,
        region_code: str,
        start_date: str,
        end_date: str,
        trade_type: str,
        page_no: int,
    ) -> dict:
        """Fetch a single page with retry + exponential backoff."""
        params = {
            "serviceKey": settings.reb_api_key,
            "regionCode": region_code,
            "startDate": start_date,
            "endDate": end_date,
            "tradeType": trade_type,
            "houseType": "01",  # 아파트
            "pageNo": str(page_no),
            "numOfRows": str(_NUM_OF_ROWS),
            "type": "json",
        }

        for attempt in range(_MAX_RETRIES):
            try:
                response = await client.get(_BASE_URL, params=params, timeout=30.0)
                response.raise_for_status()
                return response.json()
            except (httpx.HTTPError, Exception) as exc:
                if attempt == _MAX_RETRIES - 1:
                    raise
                wait = 2 ** attempt
                logger.warning(
                    "reb fetch attempt %d/%d failed (trade=%s, %s); retrying in %ds",
                    attempt + 1,
                    _MAX_RETRIES,
                    trade_type,
                    exc,
                    wait,
                )
                await asyncio.sleep(wait)

    def _extract_items(self, data: dict) -> list[dict]:
        """Safely extract item list from nested response structure."""
        try:
            items = data["response"]["body"]["items"]["item"]
            if isinstance(items, dict):
                return [items]
            return items or []
        except (KeyError, TypeError):
            return []

    def _total_count(self, data: dict) -> int:
        try:
            return int(data["response"]["body"]["totalCount"])
        except (KeyError, TypeError, ValueError):
            return 0

    def _parse_record(
        self,
        item: dict,
        region_code: str,
        trade_type: str,
    ) -> dict | None:
        """Parse a single API item into a DB-ready dict. Returns None to skip."""
        try:
            region_name = _clean(
                item.get("region_nm")
                or item.get("regionNm")
                or item.get("regionName")
                or region_code
            )

            date_raw = (
                item.get("date")
                or item.get("stdMt")   # alternate key seen in some responses
                or item.get("baseDate")
            )
            period = _period_from_yyyymm(_clean(date_raw))
            if not period:
                logger.debug("Skipping reb record: unparseable date '%s'", date_raw)
                return None

            index_raw = (
                item.get("price_index")
                or item.get("priceIndex")
                or item.get("index")
            )
            value = _to_decimal(index_raw)

            stat_type = _TRADE_TYPE_MAP.get(trade_type, _STAT_SALE)

            # base_date may be provided separately or derived from the period
            base_date_raw = _clean(item.get("base_date") or item.get("baseDate"))

            return {
                "source": "reb",
                "stat_type": stat_type,
                "region_code": region_code,
                "region_name": region_name,
                "period": period,
                "value": value,
                "base_date": base_date_raw,
                "raw_data": item,
            }
        except Exception as exc:
            logger.debug("Skipping reb record due to parse error: %s", exc)
            return None

    async def _collect_trade_type(
        self,
        client: httpx.AsyncClient,
        region_code: str,
        start_date: str,
        end_date: str,
        trade_type: str,
    ) -> list[dict]:
        """Fetch all pages for one trade type and return parsed records."""
        records: list[dict] = []

        try:
            first_page = await self._fetch_page(
                client, region_code, start_date, end_date, trade_type, 1
            )
            total_count = self._total_count(first_page)
            for item in self._extract_items(first_page):
                parsed = self._parse_record(item, region_code, trade_type)
                if parsed:
                    records.append(parsed)

            total_pages = max(1, (total_count + _NUM_OF_ROWS - 1) // _NUM_OF_ROWS)
            for page_no in range(2, total_pages + 1):
                page_data = await self._fetch_page(
                    client, region_code, start_date, end_date, trade_type, page_no
                )
                for item in self._extract_items(page_data):
                    parsed = self._parse_record(item, region_code, trade_type)
                    if parsed:
                        records.append(parsed)

        except Exception as exc:
            logger.warning(
                "reb trade_type=%s failed for region %s: %s",
                trade_type,
                region_code,
                exc,
            )

        return records

    async def collect(self, region_code: str, **params) -> CollectionResult:
        started_at = datetime.now()
        result = CollectionResult(
            source=self.source_name,
            region_code=region_code,
            started_at=started_at,
        )

        # Default: collect from one year ago to current month
        now = datetime.now()
        default_end = now.strftime("%Y%m")
        default_start_year = now.year - 1
        default_start = f"{default_start_year}{now.strftime('%m')}"

        start_date: str = params.get("start_date", default_start)
        end_date: str = params.get("end_date", default_end)

        try:
            async with httpx.AsyncClient() as client:
                # Collect sale index and jeonse index concurrently
                sale_task = self._collect_trade_type(
                    client, region_code, start_date, end_date, _TRADE_SALE
                )
                jeonse_task = self._collect_trade_type(
                    client, region_code, start_date, end_date, _TRADE_JEONSE
                )
                sale_records, jeonse_records = await asyncio.gather(
                    sale_task, jeonse_task
                )

            all_records = sale_records + jeonse_records
            result.records_collected = len(all_records)

            if all_records:
                inserted, updated = await self._upsert_statistics(all_records)
                result.records_inserted = inserted
                result.records_updated = updated

        except Exception as exc:
            logger.error(
                "RebCollector failed for region %s: %s", region_code, exc
            )
            result.status = "error"
            result.error_message = str(exc)

        result.duration_seconds = (datetime.now() - started_at).total_seconds()
        return result

    async def _upsert_statistics(self, records: list[dict]) -> tuple[int, int]:
        """Upsert price statistic records; returns (inserted, updated) counts."""
        inserted = 0
        updated = 0

        async with async_session() as session:
            async with session.begin():
                for record in records:
                    stmt = (
                        insert(PriceStatistic)
                        .values(
                            source=record["source"],
                            stat_type=record["stat_type"],
                            region_code=record["region_code"],
                            region_name=record["region_name"],
                            period=record["period"],
                            value=record["value"],
                            base_date=record["base_date"],
                            raw_data=record["raw_data"],
                        )
                        .on_conflict_do_update(
                            constraint="uq_price_statistics_key",
                            set_={
                                "value": record["value"],
                                "region_code": record["region_code"],
                                "base_date": record["base_date"],
                                "raw_data": record["raw_data"],
                            },
                        )
                    )
                    result_proxy = await session.execute(stmt)
                    if result_proxy.rowcount > 0:
                        inserted += 1
                    else:
                        updated += 1

        return inserted, updated

    async def health_check(self) -> bool:
        """Verify that the REB price index API is reachable."""
        try:
            now = datetime.now()
            period = now.strftime("%Y%m")
            async with httpx.AsyncClient() as client:
                params = {
                    "serviceKey": settings.reb_api_key,
                    "regionCode": "11",  # Seoul
                    "startDate": period,
                    "endDate": period,
                    "tradeType": _TRADE_SALE,
                    "houseType": "01",
                    "pageNo": "1",
                    "numOfRows": "1",
                    "type": "json",
                }
                response = await client.get(_BASE_URL, params=params, timeout=10.0)
                response.raise_for_status()
                return True
        except Exception as exc:
            logger.warning("RebCollector health check failed: %s", exc)
            return False
