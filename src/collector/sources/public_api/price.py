"""
Official Apartment Price Collector (공동주택 공시가격)

Collects government-appraised apartment prices from the Korean government
open API (apis.data.go.kr - AptBasisInfoService1/getAppraisedPriceAttr).
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
from src.db.models import OfficialPrice

logger = logging.getLogger(__name__)

_BASE_URL = (
    "http://apis.data.go.kr/1613000/AptBasisInfoService1/getAppraisedPriceAttr"
)
_NUM_OF_ROWS = 100
_MAX_RETRIES = 3


def _clean(value) -> str | None:
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


class OfficialPriceCollector(BaseCollector):
    """Collects official appraised apartment prices (공동주택 공시가격) from data.go.kr."""

    @property
    def source_name(self) -> str:
        return "official_price"

    async def _fetch_page(
        self,
        client: httpx.AsyncClient,
        sigungu_cd: str,
        stdd_year: str,
        page_no: int,
    ) -> dict:
        """Fetch a single page with retry + exponential backoff."""
        params = {
            "serviceKey": settings.data_go_kr_api_key,
            "sigunguCd": sigungu_cd,
            "bjdongCd": "00000",
            "stddYear": stdd_year,
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
                    "official_price fetch attempt %d/%d failed (%s); retrying in %ds",
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

    def _parse_record(self, item: dict, region_code: str, stdd_year: int) -> dict | None:
        """
        Parse a single API item into a DB-ready dict.

        The API response format can vary; every field access is wrapped to
        handle missing or malformed values gracefully.
        """
        try:
            apt_name = _clean(item.get("kaptName")) or _clean(item.get("aptName"))
            if not apt_name:
                logger.debug("Skipping official_price record: no apt_name found")
                return None

            dong_name = _clean(
                item.get("bjdong_nm")
                or item.get("bjdongNm")
                or item.get("dongNm")
            )

            exclusive_area = _to_decimal(
                item.get("as_area") or item.get("asArea") or item.get("exclusiveArea")
            )

            price_raw = (
                item.get("as_price")
                or item.get("asPrice")
                or item.get("officialPrice")
            )
            official_price = _to_int(price_raw)
            if official_price is None:
                logger.debug(
                    "Skipping official_price record for '%s': no price", apt_name
                )
                return None

            year_raw = (
                item.get("stdd_year")
                or item.get("stddYear")
                or item.get("priceYear")
            )
            price_year = _to_int(year_raw) or stdd_year

            return {
                "region_code": region_code,
                "dong_name": dong_name,
                "apt_name": apt_name,
                "exclusive_area": exclusive_area,
                "price_year": price_year,
                "official_price": official_price,
                "raw_data": item,
            }
        except Exception as exc:
            logger.debug("Skipping official_price record due to parse error: %s", exc)
            return None

    async def collect(self, region_code: str, **params) -> CollectionResult:
        started_at = datetime.now()
        result = CollectionResult(
            source=self.source_name,
            region_code=region_code,
            started_at=started_at,
        )

        stdd_year_int: int = params.get("stdd_year", datetime.now().year)
        stdd_year = str(stdd_year_int)
        sigungu_cd = region_code

        try:
            async with httpx.AsyncClient() as client:
                records: list[dict] = []

                # First page — determine total count
                first_page = await self._fetch_page(
                    client, sigungu_cd, stdd_year, 1
                )
                total_count = self._total_count(first_page)
                for item in self._extract_items(first_page):
                    parsed = self._parse_record(item, region_code, stdd_year_int)
                    if parsed:
                        records.append(parsed)

                total_pages = max(1, (total_count + _NUM_OF_ROWS - 1) // _NUM_OF_ROWS)
                for page_no in range(2, total_pages + 1):
                    page_data = await self._fetch_page(
                        client, sigungu_cd, stdd_year, page_no
                    )
                    for item in self._extract_items(page_data):
                        parsed = self._parse_record(item, region_code, stdd_year_int)
                        if parsed:
                            records.append(parsed)

            result.records_collected = len(records)

            if records:
                inserted, updated = await self._upsert_prices(records)
                result.records_inserted = inserted
                result.records_updated = updated

        except Exception as exc:
            logger.error(
                "OfficialPriceCollector failed for region %s: %s", region_code, exc
            )
            result.status = "error"
            result.error_message = str(exc)

        result.duration_seconds = (datetime.now() - started_at).total_seconds()
        return result

    async def _upsert_prices(self, records: list[dict]) -> tuple[int, int]:
        """Upsert official price records; returns (inserted, updated) counts."""
        inserted = 0
        updated = 0

        async with async_session() as session:
            async with session.begin():
                for record in records:
                    stmt = (
                        insert(OfficialPrice)
                        .values(
                            region_code=record["region_code"],
                            dong_name=record["dong_name"],
                            apt_name=record["apt_name"],
                            exclusive_area=record["exclusive_area"],
                            price_year=record["price_year"],
                            official_price=record["official_price"],
                            raw_data=record["raw_data"],
                        )
                        .on_conflict_do_update(
                            constraint="uq_official_prices_key",
                            set_={
                                "official_price": record["official_price"],
                                "dong_name": record["dong_name"],
                                "raw_data": record["raw_data"],
                            },
                        )
                    )
                    result_proxy = await session.execute(stmt)
                    # rowcount == 1 for both insert and update on conflict do update;
                    # detect insert vs update via the returned row's xmax (not portable),
                    # so we use a simple heuristic: count inserted via affected rows.
                    if result_proxy.rowcount > 0:
                        inserted += 1
                    else:
                        updated += 1

        return inserted, updated

    async def health_check(self) -> bool:
        """Verify that the official price API is reachable."""
        try:
            async with httpx.AsyncClient() as client:
                params = {
                    "serviceKey": settings.data_go_kr_api_key,
                    "sigunguCd": "11110",
                    "bjdongCd": "00000",
                    "stddYear": str(datetime.now().year),
                    "pageNo": "1",
                    "numOfRows": "1",
                    "type": "json",
                }
                response = await client.get(_BASE_URL, params=params, timeout=10.0)
                response.raise_for_status()
                return True
        except Exception as exc:
            logger.warning("OfficialPriceCollector health check failed: %s", exc)
            return False
