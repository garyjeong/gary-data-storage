"""
Building Ledger Collector (건축물대장)

Collects building registry data from the Korean government open API
(apis.data.go.kr - BldRgstHubService/getBrTitleInfo).
"""
import asyncio
import logging
from datetime import datetime, date
from decimal import Decimal, InvalidOperation

import httpx
from sqlalchemy.dialects.postgresql import insert

from src.collector.base import BaseCollector, CollectionResult
from src.config import settings
from src.db.connection import async_session
from src.db.models import Building

logger = logging.getLogger(__name__)

_BASE_URL = "http://apis.data.go.kr/1613000/BldRgstHubService/getBrTitleInfo"
_NUM_OF_ROWS = 100
_MAX_RETRIES = 3


def _clean(value) -> str | None:
    """Strip whitespace and return None for empty strings."""
    if value is None:
        return None
    stripped = str(value).strip()
    return stripped if stripped else None


def _to_int(value) -> int | None:
    try:
        v = _clean(value)
        return int(v) if v else None
    except (ValueError, TypeError):
        return None


def _to_decimal(value) -> Decimal | None:
    try:
        v = _clean(value)
        return Decimal(v) if v else None
    except (InvalidOperation, TypeError):
        return None


def _parse_build_date(value: str | None) -> date | None:
    """Convert YYYYMMDD string to date. Returns None on any failure."""
    v = _clean(value)
    if not v or len(v) < 8:
        return None
    try:
        return date(int(v[:4]), int(v[4:6]), int(v[6:8]))
    except (ValueError, TypeError):
        return None


class BuildingCollector(BaseCollector):
    """Collects building ledger (건축물대장) data from data.go.kr."""

    @property
    def source_name(self) -> str:
        return "building"

    async def _fetch_page(
        self,
        client: httpx.AsyncClient,
        sigungu_cd: str,
        bjdong_cd: str,
        page_no: int,
    ) -> dict:
        """Fetch a single page with retry + exponential backoff."""
        params = {
            "serviceKey": settings.data_go_kr_api_key,
            "sigunguCd": sigungu_cd,
            "bjdongCd": bjdong_cd,
            "platGbCd": "0",
            "buldKindCd": "2",
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
                    "building fetch attempt %d/%d failed (%s); retrying in %ds",
                    attempt + 1,
                    _MAX_RETRIES,
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

    def _parse_record(self, item: dict, region_code: str) -> dict | None:
        """Parse a single API item into a DB-ready dict. Returns None to skip."""
        try:
            dong_code_raw = _clean(item.get("bjdongCd"))
            apt_name = _clean(item.get("bldNm"))
            main_purpose = _clean(item.get("mainPurpsCdNm"))
            structure = _clean(item.get("strctCdNm"))
            ground_floors = _to_int(item.get("grndFlrCnt"))
            underground_floors = _to_int(item.get("ugrndFlrCnt"))
            total_area = _to_decimal(item.get("totArea"))
            build_date = _parse_build_date(item.get("useAprDay"))

            return {
                "region_code": region_code,
                "dong_code": dong_code_raw,
                "apt_name": apt_name,
                "main_purpose": main_purpose,
                "structure": structure,
                "ground_floors": ground_floors,
                "underground_floors": underground_floors,
                "total_area": total_area,
                "build_date": build_date,
                "raw_data": item,
            }
        except Exception as exc:
            logger.debug("Skipping building record due to parse error: %s", exc)
            return None

    async def collect(self, region_code: str, **params) -> CollectionResult:
        started_at = datetime.now()
        result = CollectionResult(
            source=self.source_name,
            region_code=region_code,
            started_at=started_at,
        )

        # sigungu_cd is the 5-digit region code; bjdong_cd defaults to "00000" (all dongs)
        sigungu_cd = region_code
        bjdong_cd = params.get("bjdong_cd", "00000")

        try:
            async with httpx.AsyncClient() as client:
                records: list[dict] = []

                # Fetch first page to determine total count
                first_page = await self._fetch_page(client, sigungu_cd, bjdong_cd, 1)
                total_count = self._total_count(first_page)
                items = self._extract_items(first_page)

                for item in items:
                    parsed = self._parse_record(item, region_code)
                    if parsed:
                        records.append(parsed)

                # Fetch remaining pages
                total_pages = max(1, (total_count + _NUM_OF_ROWS - 1) // _NUM_OF_ROWS)
                for page_no in range(2, total_pages + 1):
                    page_data = await self._fetch_page(
                        client, sigungu_cd, bjdong_cd, page_no
                    )
                    for item in self._extract_items(page_data):
                        parsed = self._parse_record(item, region_code)
                        if parsed:
                            records.append(parsed)

            result.records_collected = len(records)

            if records:
                inserted, updated = await self._upsert_buildings(records)
                result.records_inserted = inserted
                result.records_updated = updated

        except Exception as exc:
            logger.error(
                "BuildingCollector failed for region %s: %s", region_code, exc
            )
            result.status = "error"
            result.error_message = str(exc)

        result.duration_seconds = (datetime.now() - started_at).total_seconds()
        return result

    async def _upsert_buildings(self, records: list[dict]) -> tuple[int, int]:
        """Upsert building records; returns (inserted, updated) counts."""
        inserted = 0
        updated = 0

        async with async_session() as session:
            async with session.begin():
                for record in records:
                    stmt = (
                        insert(Building)
                        .values(
                            region_code=record["region_code"],
                            dong_code=record["dong_code"],
                            apt_name=record["apt_name"],
                            main_purpose=record["main_purpose"],
                            structure=record["structure"],
                            ground_floors=record["ground_floors"],
                            underground_floors=record["underground_floors"],
                            total_area=record["total_area"],
                            build_date=record["build_date"],
                            raw_data=record["raw_data"],
                        )
                        .on_conflict_do_nothing()
                    )
                    result_proxy = await session.execute(stmt)
                    if result_proxy.rowcount > 0:
                        inserted += 1
                    else:
                        updated += 1

        return inserted, updated

    async def health_check(self) -> bool:
        """Verify that the building ledger API is reachable."""
        try:
            async with httpx.AsyncClient() as client:
                params = {
                    "serviceKey": settings.data_go_kr_api_key,
                    "sigunguCd": "11110",
                    "bjdongCd": "00000",
                    "platGbCd": "0",
                    "buldKindCd": "2",
                    "pageNo": "1",
                    "numOfRows": "1",
                    "type": "json",
                }
                response = await client.get(_BASE_URL, params=params, timeout=10.0)
                response.raise_for_status()
                return True
        except Exception as exc:
            logger.warning("BuildingCollector health check failed: %s", exc)
            return False
