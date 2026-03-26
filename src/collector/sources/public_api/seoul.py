"""Seoul Open Data Plaza apartment transaction collector (서울 열린데이터광장).

API endpoint pattern:
  http://openapi.seoul.go.kr:8088/{KEY}/json/tbLnOpendataRtmsarss/{startIndex}/{endIndex}/{RCPT_YR}/{CGG_CD}

Authentication: Uses data_go_kr_api_key (user may need to register separately at
  https://data.seoul.go.kr for a Seoul-specific key with the same value).

Seoul region codes: 5-digit codes starting with "11" (e.g., 11110 = 종로구).
CGG_CD (구 코드) is derived as the last 3 characters of the 5-digit code.
"""

import asyncio
import logging
import time
from datetime import date, datetime

import httpx
from sqlalchemy.dialects.postgresql import insert

from src.collector.base import BaseCollector, CollectionResult
from src.config import settings
from src.db.connection import async_session
from src.db.models import AptTransaction

logger = logging.getLogger(__name__)

# Seoul 열린데이터광장 base URL
_BASE_URL = "http://openapi.seoul.go.kr:8088"
_SERVICE = "tbLnOpendataRtmsarss"

# Maximum records per API page (Seoul API limit is 1000)
_PAGE_SIZE = 1000

# Retry configuration
_MAX_RETRIES = 3
_RETRY_BASE_DELAY = 1.0  # seconds, doubles each attempt


def _derive_cgg_cd(region_code: str) -> str | None:
    """Derive Seoul district code (CGG_CD) from a 5-digit region code.

    Seoul region codes start with "11". The CGG_CD used by the Seoul Open Data
    API is typically the 3-digit suffix (e.g., "11110" -> "110").
    """
    if not region_code.startswith("11") or len(region_code) != 5:
        return None
    return region_code[2:]  # last 3 digits


def _parse_deal_amount(raw: str | None) -> int | None:
    """Parse a Korean money string like '85,000' (만원 단위) into an integer (만원)."""
    if not raw:
        return None
    try:
        return int(raw.replace(",", "").strip())
    except (ValueError, AttributeError):
        return None


def _parse_int(raw: str | None) -> int | None:
    """Safe integer parse."""
    if not raw:
        return None
    try:
        return int(str(raw).replace(",", "").strip())
    except (ValueError, AttributeError):
        return None


def _parse_float(raw: str | None) -> float | None:
    """Safe float parse."""
    if not raw:
        return None
    try:
        return float(str(raw).replace(",", "").strip())
    except (ValueError, AttributeError):
        return None


def _parse_deal_date(deal_ymd: str | None) -> tuple[int | None, int | None, int | None]:
    """Parse DEAL_YMD field (YYYYMMDD or YYYY-MM-DD) into (year, month, day)."""
    if not deal_ymd:
        return None, None, None
    s = deal_ymd.replace("-", "").strip()
    if len(s) == 8:
        try:
            return int(s[:4]), int(s[4:6]), int(s[6:8])
        except ValueError:
            pass
    return None, None, None


def _parse_contract_date(year: int | None, month: int | None, day: int | None) -> date | None:
    """Build a date object from parsed components."""
    if year and month and day:
        try:
            return date(year, month, day)
        except ValueError:
            pass
    return None


def _map_row_to_record(row: dict, region_code: str) -> dict | None:
    """Map a single Seoul API row to an AptTransaction insert dict.

    Seoul response fields:
      CGG_NM  : 구명 (district name)
      BLDG_NM : 건물명 (building/apt name)
      THING_AMT : 물건금액 (만원)
      BLDG_AREA : 건물면적 (㎡)
      FLOOR   : 층
      BUILD_YEAR : 건축년도
      RCPT_YR : 접수년도 (year)
      DEAL_YMD : 계약일 (YYYYMMDD)
    """
    apt_name = (row.get("BLDG_NM") or "").strip()
    if not apt_name:
        return None

    deal_year, deal_month, deal_day = _parse_deal_date(row.get("DEAL_YMD"))

    # Fallback: use RCPT_YR when DEAL_YMD is missing or unparseable
    if deal_year is None:
        deal_year = _parse_int(row.get("RCPT_YR"))

    if deal_year is None:
        return None

    if deal_month is None:
        deal_month = 1  # unknown month defaults to January

    contract_date = _parse_contract_date(deal_year, deal_month, deal_day)

    return {
        "source": "seoul",
        "transaction_type": "sale",
        "region_code": region_code,
        "dong_name": (row.get("CGG_NM") or "").strip() or None,
        "apt_name": apt_name,
        "exclusive_area": _parse_float(row.get("BLDG_AREA")),
        "floor": _parse_int(row.get("FLOOR")),
        "deal_amount": _parse_deal_amount(row.get("THING_AMT")),
        "deposit": None,
        "monthly_rent": None,
        "deal_year": deal_year,
        "deal_month": deal_month,
        "deal_day": deal_day,
        "build_year": _parse_int(row.get("BUILD_YEAR")),
        "jibun": None,
        "road_name": None,
        "cancel_deal_type": None,
        "contract_date": contract_date,
        "raw_data": row,
    }


class SeoulCollector(BaseCollector):
    """Collector for Seoul Open Data Plaza real estate transaction data."""

    @property
    def source_name(self) -> str:
        return "seoul"

    async def collect(self, region_code: str, **params) -> CollectionResult:
        """Collect Seoul apartment transaction data for the given region code.

        Only processes region codes starting with "11" (Seoul). For other codes
        an empty successful result is returned immediately.
        """
        started_at = datetime.now()

        cgg_cd = _derive_cgg_cd(region_code)
        if cgg_cd is None:
            logger.debug(
                "SeoulCollector: region_code=%s is not a Seoul code, skipping", region_code
            )
            return CollectionResult(
                source=self.source_name,
                region_code=region_code,
                status="success",
                started_at=started_at,
            )

        api_key = settings.seoul_api_key
        if not api_key:
            return CollectionResult(
                source=self.source_name,
                region_code=region_code,
                status="error",
                error_message="data_go_kr_api_key is not configured",
                started_at=started_at,
            )

        # Collect year from params or default to current year
        rcpt_yr = params.get("year", datetime.now().year)

        try:
            all_rows = await self._fetch_all_pages(api_key, cgg_cd, rcpt_yr)
        except Exception as exc:
            logger.error(
                "SeoulCollector: fetch failed for region_code=%s: %s", region_code, exc
            )
            return CollectionResult(
                source=self.source_name,
                region_code=region_code,
                status="error",
                error_message=str(exc),
                started_at=started_at,
            )

        records = []
        for row in all_rows:
            mapped = _map_row_to_record(row, region_code)
            if mapped is not None:
                records.append(mapped)

        if not records:
            return CollectionResult(
                source=self.source_name,
                region_code=region_code,
                records_collected=0,
                status="success",
                started_at=started_at,
            )

        inserted, updated = await self._upsert_records(records)

        return CollectionResult(
            source=self.source_name,
            region_code=region_code,
            records_collected=len(records),
            records_inserted=inserted,
            records_updated=updated,
            status="success",
            started_at=started_at,
        )

    async def health_check(self) -> bool:
        """Verify the Seoul Open Data API is reachable."""
        api_key = settings.seoul_api_key
        if not api_key:
            return False
        url = f"{_BASE_URL}/{api_key}/json/{_SERVICE}/1/1/{datetime.now().year}/110"
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(url)
                return resp.status_code == 200
        except Exception:
            return False

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _fetch_all_pages(
        self, api_key: str, cgg_cd: str, rcpt_yr: int
    ) -> list[dict]:
        """Paginate through all records for the given district and year."""
        all_rows: list[dict] = []
        start_index = 1

        async with httpx.AsyncClient(timeout=30.0) as client:
            while True:
                end_index = start_index + _PAGE_SIZE - 1
                url = (
                    f"{_BASE_URL}/{api_key}/json/{_SERVICE}"
                    f"/{start_index}/{end_index}/{rcpt_yr}/{cgg_cd}"
                )

                data = await self._request_with_retry(client, url)

                # Seoul API wraps results under the service name key
                service_data = data.get(_SERVICE, {})
                list_total = service_data.get("list_total_count", 0)
                result_code = (
                    service_data.get("RESULT", {}).get("CODE", "") or
                    service_data.get("result", {}).get("code", "")
                )

                # INFO-000 means success; other codes mean no data or error
                if result_code and result_code not in ("INFO-000",):
                    logger.warning(
                        "SeoulCollector: API result code=%s for cgg_cd=%s year=%s",
                        result_code, cgg_cd, rcpt_yr,
                    )
                    break

                rows: list[dict] = service_data.get("row", [])
                if not rows:
                    break

                all_rows.extend(rows)

                if len(all_rows) >= list_total or len(rows) < _PAGE_SIZE:
                    break

                start_index += _PAGE_SIZE

        return all_rows

    async def _request_with_retry(
        self, client: httpx.AsyncClient, url: str
    ) -> dict:
        """GET request with exponential-backoff retry (up to _MAX_RETRIES attempts)."""
        last_exc: Exception | None = None
        for attempt in range(_MAX_RETRIES):
            try:
                resp = await client.get(url)
                resp.raise_for_status()
                return resp.json()
            except (httpx.HTTPStatusError, httpx.RequestError) as exc:
                last_exc = exc
                if attempt < _MAX_RETRIES - 1:
                    delay = _RETRY_BASE_DELAY * (2 ** attempt)
                    logger.warning(
                        "SeoulCollector: attempt %d failed (%s), retrying in %.1fs",
                        attempt + 1, exc, delay,
                    )
                    await asyncio.sleep(delay)
        raise last_exc  # type: ignore[misc]

    async def _upsert_records(self, records: list[dict]) -> tuple[int, int]:
        """Bulk upsert records into apt_transactions, returns (inserted, updated)."""
        inserted = 0
        updated = 0

        async with async_session() as session:
            async with session.begin():
                stmt = (
                    insert(AptTransaction)
                    .values(records)
                    .on_conflict_do_update(
                        index_elements=[
                            "source",
                            "transaction_type",
                            "region_code",
                            "apt_name",
                            "exclusive_area",
                            "deal_year",
                            "deal_month",
                            "deal_day",
                            "floor",
                        ],
                        set_={
                            "deal_amount": insert(AptTransaction).excluded.deal_amount,
                            "dong_name": insert(AptTransaction).excluded.dong_name,
                            "build_year": insert(AptTransaction).excluded.build_year,
                            "contract_date": insert(AptTransaction).excluded.contract_date,
                            "raw_data": insert(AptTransaction).excluded.raw_data,
                            "updated_at": datetime.now(),
                        },
                        # Only conflict on rows where deal_day IS NOT NULL
                        # (mirrors the partial unique index definition)
                        index_where=(AptTransaction.deal_day.isnot(None)),
                    )
                )
                result = await session.execute(stmt)
                # rowcount reflects total rows touched; approximate split
                total_touched = result.rowcount if result.rowcount >= 0 else len(records)
                inserted = total_touched
                updated = 0

        return inserted, updated
