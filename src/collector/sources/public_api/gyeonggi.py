"""Gyeonggi Data Dream apartment transaction collector (경기데이터드림).

API endpoint: https://openapi.gg.go.kr/AptTradeSvc
Authentication: KEY parameter (uses data_go_kr_api_key; user may need separate
  registration at https://data.gg.go.kr).

Gyeonggi region codes start with "41" (5-digit). The SIGUN_CD parameter accepts
the same 5-digit code.

Response envelope:
  {
    "AptTradeSvc": [
      {"head": [{"list_total_count": N}, {"RESULT": {"CODE": "INFO-000", "MESSAGE": "정상"}}]},
      {"row": [...]}
    ]
  }

Row fields used:
  SIGUN_CD   : 시군 코드 (5-digit)
  SIGUN_NM   : 시군 명칭
  DEAL_YMD   : 계약일 (YYYYMMDD)
  DEAL_AMT   : 거래금액 (만원, comma-separated string)
  BUILD_YEAR : 건축년도
  APT_NM     : 아파트명
  EXCLU_USE_AR : 전용면적 (㎡)
  FLOOR      : 층
  JIBUN      : 지번
"""

import asyncio
import logging
from datetime import date, datetime

import httpx
from sqlalchemy.dialects.postgresql import insert

from src.collector.base import BaseCollector, CollectionResult
from src.config import settings
from src.db.connection import async_session
from src.db.models import AptTransaction

logger = logging.getLogger(__name__)

_API_URL = "https://openapi.gg.go.kr/AptTradeSvc"
_PAGE_SIZE = 100

# Retry configuration
_MAX_RETRIES = 3
_RETRY_BASE_DELAY = 1.0  # seconds, doubles each attempt


def _parse_int(raw: str | None) -> int | None:
    """Safe integer parse (handles comma-formatted numbers)."""
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
    """Parse DEAL_YMD field (YYYYMMDD) into (year, month, day)."""
    if not deal_ymd:
        return None, None, None
    s = str(deal_ymd).replace("-", "").strip()
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


def _map_row_to_record(row: dict) -> dict | None:
    """Map a single Gyeonggi API row to an AptTransaction insert dict."""
    apt_name = (row.get("APT_NM") or "").strip()
    if not apt_name:
        return None

    region_code = str(row.get("SIGUN_CD") or "").strip()
    if not region_code:
        return None

    deal_year, deal_month, deal_day = _parse_deal_date(row.get("DEAL_YMD"))
    if deal_year is None:
        return None
    if deal_month is None:
        deal_month = 1

    contract_date = _parse_contract_date(deal_year, deal_month, deal_day)

    return {
        "source": "gyeonggi",
        "transaction_type": "sale",
        "region_code": region_code,
        "dong_name": (row.get("SIGUN_NM") or "").strip() or None,
        "apt_name": apt_name,
        "exclusive_area": _parse_float(row.get("EXCLU_USE_AR")),
        "floor": _parse_int(row.get("FLOOR")),
        "deal_amount": _parse_int(row.get("DEAL_AMT")),
        "deposit": None,
        "monthly_rent": None,
        "deal_year": deal_year,
        "deal_month": deal_month,
        "deal_day": deal_day,
        "build_year": _parse_int(row.get("BUILD_YEAR")),
        "jibun": (row.get("JIBUN") or "").strip() or None,
        "road_name": None,
        "cancel_deal_type": None,
        "contract_date": contract_date,
        "raw_data": row,
    }


def _extract_envelope(data: dict) -> tuple[int, list[dict]]:
    """Extract (list_total_count, rows) from the Gyeonggi API response envelope.

    The envelope structure is:
      {"AptTradeSvc": [
        {"head": [{"list_total_count": N}, {"RESULT": {...}}]},
        {"row": [...]}
      ]}

    Returns (0, []) on unexpected structures.
    """
    outer = data.get("AptTradeSvc")
    if not isinstance(outer, list):
        return 0, []

    list_total = 0
    rows: list[dict] = []

    for section in outer:
        if "head" in section:
            for head_item in section["head"]:
                if "list_total_count" in head_item:
                    list_total = int(head_item["list_total_count"])
                if "RESULT" in head_item:
                    code = head_item["RESULT"].get("CODE", "")
                    if code not in ("INFO-000",):
                        logger.warning(
                            "GyeonggiCollector: API result code=%s message=%s",
                            code, head_item["RESULT"].get("MESSAGE", ""),
                        )
                        return 0, []
        elif "row" in section:
            rows = section["row"] if isinstance(section["row"], list) else []

    return list_total, rows


class GyeonggiCollector(BaseCollector):
    """Collector for Gyeonggi Data Dream apartment transaction data."""

    @property
    def source_name(self) -> str:
        return "gyeonggi"

    async def collect(self, region_code: str, **params) -> CollectionResult:
        """Collect Gyeonggi apartment transaction data for the given region code.

        Only processes region codes starting with "41" (Gyeonggi-do). For other
        codes an empty successful result is returned immediately.
        """
        started_at = datetime.now()

        if not region_code.startswith("41"):
            logger.debug(
                "GyeonggiCollector: region_code=%s is not a Gyeonggi code, skipping",
                region_code,
            )
            return CollectionResult(
                source=self.source_name,
                region_code=region_code,
                status="success",
                started_at=started_at,
            )

        api_key = settings.gyeonggi_api_key
        if not api_key:
            return CollectionResult(
                source=self.source_name,
                region_code=region_code,
                status="error",
                error_message="data_go_kr_api_key is not configured",
                started_at=started_at,
            )

        try:
            all_rows = await self._fetch_all_pages(api_key, region_code)
        except Exception as exc:
            logger.error(
                "GyeonggiCollector: fetch failed for region_code=%s: %s", region_code, exc
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
            mapped = _map_row_to_record(row)
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
        """Verify the Gyeonggi Data Dream API is reachable."""
        api_key = settings.gyeonggi_api_key
        if not api_key:
            return False
        params = {
            "KEY": api_key,
            "Type": "json",
            "pIndex": 1,
            "pSize": 1,
            "SIGUN_CD": "41110",  # 수원시 (Gyeonggi sample code)
        }
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(_API_URL, params=params)
                return resp.status_code == 200
        except Exception:
            return False

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _fetch_all_pages(self, api_key: str, sigun_cd: str) -> list[dict]:
        """Paginate through all records for the given Gyeonggi district code."""
        all_rows: list[dict] = []
        p_index = 1

        async with httpx.AsyncClient(timeout=30.0) as client:
            while True:
                params = {
                    "KEY": api_key,
                    "Type": "json",
                    "pIndex": p_index,
                    "pSize": _PAGE_SIZE,
                    "SIGUN_CD": sigun_cd,
                }
                data = await self._request_with_retry(client, params)
                list_total, rows = _extract_envelope(data)

                if not rows:
                    break

                all_rows.extend(rows)

                if len(all_rows) >= list_total or len(rows) < _PAGE_SIZE:
                    break

                p_index += 1

        return all_rows

    async def _request_with_retry(
        self, client: httpx.AsyncClient, params: dict
    ) -> dict:
        """GET request with exponential-backoff retry (up to _MAX_RETRIES attempts)."""
        last_exc: Exception | None = None
        for attempt in range(_MAX_RETRIES):
            try:
                resp = await client.get(_API_URL, params=params)
                resp.raise_for_status()
                return resp.json()
            except (httpx.HTTPStatusError, httpx.RequestError) as exc:
                last_exc = exc
                if attempt < _MAX_RETRIES - 1:
                    delay = _RETRY_BASE_DELAY * (2 ** attempt)
                    logger.warning(
                        "GyeonggiCollector: attempt %d failed (%s), retrying in %.1fs",
                        attempt + 1, exc, delay,
                    )
                    await asyncio.sleep(delay)
        raise last_exc  # type: ignore[misc]

    async def _upsert_records(self, records: list[dict]) -> tuple[int, int]:
        """Bulk upsert records into apt_transactions, returns (inserted, updated)."""
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
                            "jibun": insert(AptTransaction).excluded.jibun,
                            "contract_date": insert(AptTransaction).excluded.contract_date,
                            "raw_data": insert(AptTransaction).excluded.raw_data,
                            "updated_at": datetime.now(),
                        },
                        index_where=(AptTransaction.deal_day.isnot(None)),
                    )
                )
                result = await session.execute(stmt)
                total_touched = result.rowcount if result.rowcount >= 0 else len(records)
                inserted = total_touched
                updated = 0

        return inserted, updated
