"""
Korea Real Estate Board (한국부동산원) Statistics Collector

Collects apartment price index data (매매/전세) from the REB R-ONE open API
(www.reb.or.kr/r-one/openapi/).

The REB API returns ALL regions in a single response (not per-region), so
this collector fetches once per collection cycle and caches the result to
avoid redundant API calls when the manager iterates over 56 regions.
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

# ---------------------------------------------------------------------------
# R-ONE API configuration
# ---------------------------------------------------------------------------

_BASE_URL = "https://www.reb.or.kr/r-one/openapi/SttsApiTblData.do"

_MAX_RETRIES = 3
_RETRY_BACKOFF = [2, 4, 8]
_REQUEST_TIMEOUT = 30

# STATBL_ID values for apartment price indexes
_STAT_TABLES = {
    "sale_index": "A_2024_00178",      # 아파트 매매가격지수
    "jeonse_index": "A_2024_00182",    # 아파트 전세가격지수
}

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


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
    """Convert 'YYYYMM' to 'YYYY-MM'."""
    v = _clean(raw)
    if not v or len(v) < 6:
        return None
    try:
        return f"{v[:4]}-{v[4:6]}"
    except (IndexError, TypeError):
        return None


# ---------------------------------------------------------------------------
# Collector
# ---------------------------------------------------------------------------


class RebCollector(BaseCollector):
    """Collects Korea Real Estate Board price index statistics from reb.or.kr.

    The R-ONE API returns all regions in one response, so we fetch once and
    cache the result for the current collection cycle.  Subsequent calls with
    different region_codes within the same cycle return immediately.
    """

    def __init__(self):
        self._last_collected_cycle: str | None = None  # "YYYYMM" of last fetch

    @property
    def source_name(self) -> str:
        return "reb"

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def collect(self, region_code: str, **params) -> CollectionResult:
        started_at = datetime.now()
        result = CollectionResult(
            source=self.source_name,
            region_code=region_code,
            started_at=started_at,
        )

        current_cycle = datetime.now().strftime("%Y%m%d%H")

        # Skip if already collected in this cycle (avoid 56 duplicate calls)
        if self._last_collected_cycle == current_cycle:
            result.status = "success"
            result.duration_seconds = 0.0
            return result

        now = datetime.now()
        end_period = now.strftime("%Y%m")
        start_year = now.year - 1
        start_period = f"{start_year}{now.strftime('%m')}"

        start_period = params.get("start_period", start_period)
        end_period = params.get("end_period", end_period)

        logger.info(
            "[reb] Starting collection: period=%s~%s",
            start_period,
            end_period,
        )

        try:
            all_records: list[dict] = []

            async with httpx.AsyncClient(timeout=_REQUEST_TIMEOUT) as client:
                for stat_type, statbl_id in _STAT_TABLES.items():
                    records = await self._fetch_stat_table(
                        client, statbl_id, stat_type, start_period, end_period
                    )
                    all_records.extend(records)
                    await asyncio.sleep(1)  # polite delay between requests

            result.records_collected = len(all_records)

            if all_records:
                inserted, updated = await self._upsert_statistics(all_records)
                result.records_inserted = inserted
                result.records_updated = updated

            result.status = "success"
            self._last_collected_cycle = current_cycle

            logger.info(
                "[reb] Done: collected=%d inserted=%d updated=%d duration=%.2fs",
                result.records_collected,
                result.records_inserted,
                result.records_updated,
                (datetime.now() - started_at).total_seconds(),
            )

        except Exception as exc:
            logger.error("[reb] Collection failed: %s", exc)
            result.status = "error"
            result.error_message = str(exc)

        result.duration_seconds = (datetime.now() - started_at).total_seconds()
        return result

    async def health_check(self) -> bool:
        """Verify REB R-ONE API is reachable."""
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                params = {
                    "KEY": settings.reb_api_key,
                    "Type": "json",
                    "STATBL_ID": _STAT_TABLES["sale_index"],
                    "DTACYCLE_CD": "MM",
                    "WRTTIME_IDTFR_ID": datetime.now().strftime("%Y%m"),
                }
                resp = await client.get(_BASE_URL, params=params)
                return resp.status_code == 200
        except Exception as exc:
            logger.warning("[reb] health_check failed: %s", exc)
            return False

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    async def _fetch_stat_table(
        self,
        client: httpx.AsyncClient,
        statbl_id: str,
        stat_type: str,
        start_period: str,
        end_period: str,
    ) -> list[dict]:
        """Fetch all monthly data for a stat table across the given period range.

        The R-ONE API accepts WRTTIME_IDTFR_ID for a single month.
        We iterate month by month from start to end.
        """
        records: list[dict] = []
        periods = self._generate_periods(start_period, end_period)

        for period_yyyymm in periods:
            items = await self._fetch_period(client, statbl_id, period_yyyymm)
            for item in items:
                parsed = self._parse_record(item, stat_type, period_yyyymm)
                if parsed:
                    records.append(parsed)
            # Small delay between month requests
            if len(periods) > 1:
                await asyncio.sleep(0.5)

        logger.info(
            "[reb] Fetched %d records for %s (%s~%s)",
            len(records),
            stat_type,
            start_period,
            end_period,
        )
        return records

    async def _fetch_period(
        self,
        client: httpx.AsyncClient,
        statbl_id: str,
        period_yyyymm: str,
    ) -> list[dict]:
        """Fetch data for a single period with retry."""
        params = {
            "KEY": settings.reb_api_key,
            "Type": "json",
            "STATBL_ID": statbl_id,
            "DTACYCLE_CD": "MM",
            "WRTTIME_IDTFR_ID": period_yyyymm,
        }

        last_exc: Exception | None = None
        for attempt in range(_MAX_RETRIES):
            try:
                resp = await client.get(_BASE_URL, params=params)
                resp.raise_for_status()
                data = resp.json()
                return self._extract_items(data)
            except (httpx.HTTPError, ValueError, KeyError) as exc:
                last_exc = exc
                if attempt < _MAX_RETRIES - 1:
                    wait = _RETRY_BACKOFF[attempt]
                    logger.warning(
                        "[reb] Fetch failed (attempt %d/%d) for %s/%s: %s, retrying in %ds",
                        attempt + 1,
                        _MAX_RETRIES,
                        statbl_id,
                        period_yyyymm,
                        exc,
                        wait,
                    )
                    await asyncio.sleep(wait)

        logger.error(
            "[reb] All %d attempts failed for %s/%s: %s",
            _MAX_RETRIES,
            statbl_id,
            period_yyyymm,
            last_exc,
        )
        return []

    def _extract_items(self, data: dict) -> list[dict]:
        """Extract the data rows from REB JSON response.

        R-ONE response shape:
        {
            "SttsApiTblData": [
                {"head": [{"list_total_count": N}, {"RESULT": {"CODE": "INFO-000", ...}}]},
                {"row": [{...}, {...}, ...]}
            ]
        }
        """
        try:
            svc = data.get("SttsApiTblData", [])
            if not isinstance(svc, list) or len(svc) < 2:
                return []

            # Check result code
            head = svc[0].get("head", [])
            if len(head) >= 2:
                result_info = head[1].get("RESULT", {})
                code = result_info.get("CODE", "")
                if code != "INFO-000":
                    logger.warning("[reb] API returned code=%s: %s", code, result_info.get("MESSAGE", ""))
                    return []

            rows = svc[1].get("row", [])
            return rows if isinstance(rows, list) else []

        except (KeyError, TypeError, IndexError) as exc:
            logger.warning("[reb] Failed to extract items: %s", exc)
            return []

    def _parse_record(
        self,
        item: dict,
        stat_type: str,
        period_yyyymm: str,
    ) -> dict | None:
        """Parse a single REB data row into a DB-ready dict."""
        try:
            region_name = _clean(
                item.get("CLS_NM")
                or item.get("ITEM_NM1")
                or item.get("region_nm")
            )
            if not region_name:
                return None

            period = _period_from_yyyymm(period_yyyymm)
            if not period:
                return None

            value = _to_decimal(
                item.get("DTA_VAL")
                or item.get("STTS_VAL")
                or item.get("price_index")
            )

            return {
                "source": "reb",
                "stat_type": stat_type,
                "region_code": None,  # REB uses region names, not codes
                "region_name": region_name,
                "period": period,
                "value": value,
                "base_date": _clean(item.get("WRTTIME_IDTFR_ID")),
                "raw_data": item,
            }
        except Exception as exc:
            logger.debug("[reb] Skipping record: %s", exc)
            return None

    async def _upsert_statistics(self, records: list[dict]) -> tuple[int, int]:
        """Upsert price statistic records; returns (inserted, updated)."""
        if not records:
            return 0, 0

        # Deduplicate within batch (same source + stat_type + region_name + period)
        seen: dict[tuple, int] = {}
        for i, r in enumerate(records):
            key = (r["source"], r["stat_type"], r["region_name"], r["period"])
            seen[key] = i
        deduped = [records[i] for i in sorted(seen.values())]

        inserted = 0
        async with async_session() as session:
            for record in deduped:
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
                await session.execute(stmt)
                inserted += 1
            await session.commit()

        return inserted, 0

    @staticmethod
    def _generate_periods(start_yyyymm: str, end_yyyymm: str) -> list[str]:
        """Generate list of YYYYMM strings from start to end (inclusive)."""
        periods: list[str] = []
        try:
            sy, sm = int(start_yyyymm[:4]), int(start_yyyymm[4:6])
            ey, em = int(end_yyyymm[:4]), int(end_yyyymm[4:6])
            y, m = sy, sm
            while (y, m) <= (ey, em):
                periods.append(f"{y}{m:02d}")
                m += 1
                if m > 12:
                    m = 1
                    y += 1
        except (ValueError, IndexError):
            logger.warning("[reb] Invalid period range: %s ~ %s", start_yyyymm, end_yyyymm)
        return periods
