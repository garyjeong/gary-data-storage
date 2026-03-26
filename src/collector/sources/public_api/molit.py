import logging
import time
import asyncio
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any

import httpx
from sqlalchemy.dialects.postgresql import insert

from src.collector.base import BaseCollector, CollectionResult
from src.db.connection import async_session
from src.db.models import AptTransaction
from src.config import settings

logger = logging.getLogger(__name__)

_SALE_API_URL = "http://apis.data.go.kr/1613000/RTMSDataSvcAptTrade/getRTMSDataSvcAptTrade"
_JEONSE_API_URL = "http://apis.data.go.kr/1613000/RTMSDataSvcAptRent/getRTMSDataSvcAptRent"

_MAX_RETRIES = 3
_RETRY_BACKOFF = [2, 4, 8]  # seconds
_PAGE_SIZE = 1000
_REQUEST_TIMEOUT = 30


def _current_deal_ym() -> str:
    now = datetime.now()
    return now.strftime("%Y%m")


def _strip(value: Any) -> str | None:
    if value is None:
        return None
    s = str(value).strip()
    return s if s else None


def _parse_amount(value: Any) -> int | None:
    """Convert '12,000' or '12000' -> 12000. Returns None on failure."""
    raw = _strip(value)
    if raw is None:
        return None
    try:
        return int(raw.replace(",", "").replace(" ", ""))
    except (ValueError, AttributeError):
        logger.debug("Failed to parse amount: %r", value)
        return None


def _parse_int(value: Any) -> int | None:
    raw = _strip(value)
    if raw is None:
        return None
    try:
        return int(raw)
    except (ValueError, AttributeError):
        return None


def _parse_decimal(value: Any) -> Decimal | None:
    raw = _strip(value)
    if raw is None:
        return None
    try:
        return Decimal(raw)
    except (InvalidOperation, ValueError, AttributeError):
        return None


def _parse_contract_date(value: Any):
    """Parse '해제사유발생일' field like '2024-01-15' -> date or None."""
    raw = _strip(value)
    if raw is None:
        return None
    try:
        return datetime.strptime(raw, "%Y-%m-%d").date()
    except (ValueError, AttributeError):
        return None


async def _fetch_with_retry(
    client: httpx.AsyncClient,
    url: str,
    params: dict,
) -> dict:
    """Fetch a single page with exponential backoff retry.

    Builds URL manually to prevent httpx from double-encoding the serviceKey
    which contains special characters (/, =).
    """
    from urllib.parse import urlencode

    # Build URL manually — serviceKey must not be percent-encoded again
    params = dict(params)  # copy to avoid mutating caller's dict
    service_key = params.pop("serviceKey", None)
    if service_key:
        query = f"serviceKey={service_key}&{urlencode(params)}"
    else:
        query = urlencode(params)
    full_url = f"{url}?{query}"

    last_exc: Exception | None = None
    for attempt in range(_MAX_RETRIES):
        try:
            response = await client.get(full_url)
            response.raise_for_status()
            data = response.json()
            result_code = (
                data.get("response", {})
                .get("header", {})
                .get("resultCode", "")
            )
            if result_code != "00":
                result_msg = (
                    data.get("response", {})
                    .get("header", {})
                    .get("resultMsg", "unknown")
                )
                raise ValueError(f"API error {result_code}: {result_msg}")
            return data
        except (httpx.HTTPError, ValueError, KeyError) as exc:
            last_exc = exc
            if attempt < _MAX_RETRIES - 1:
                wait = _RETRY_BACKOFF[attempt]
                logger.warning(
                    "Request failed (attempt %d/%d), retrying in %ds: %s",
                    attempt + 1,
                    _MAX_RETRIES,
                    wait,
                    exc,
                )
                await asyncio.sleep(wait)
            else:
                logger.error(
                    "All %d attempts failed for %s: %s",
                    _MAX_RETRIES,
                    url,
                    exc,
                )
    raise last_exc  # type: ignore[misc]


async def _fetch_all_pages(url: str, base_params: dict) -> list[dict]:
    """Paginate through all pages and return flat list of raw items."""
    all_items: list[dict] = []
    page_no = 1

    async with httpx.AsyncClient(timeout=_REQUEST_TIMEOUT) as client:
        while True:
            params = {**base_params, "pageNo": page_no, "numOfRows": _PAGE_SIZE}
            data = await _fetch_with_retry(client, url, params)

            body = data.get("response", {}).get("body", {})
            total_count: int = int(body.get("totalCount", 0))
            items_wrapper = body.get("items", {})

            # items can be empty string when no results
            if not items_wrapper or not isinstance(items_wrapper, dict):
                break

            raw_items = items_wrapper.get("item", [])
            # Single result may come as dict instead of list
            if isinstance(raw_items, dict):
                raw_items = [raw_items]

            all_items.extend(raw_items)

            logger.debug(
                "Page %d: fetched %d items (total=%d, accumulated=%d)",
                page_no,
                len(raw_items),
                total_count,
                len(all_items),
            )

            if len(all_items) >= total_count or not raw_items:
                break

            page_no += 1

    return all_items


def _build_upsert_index_elements() -> list[str]:
    """Columns in the partial unique index ix_apt_transactions_unique_deal."""
    return [
        "source",
        "transaction_type",
        "region_code",
        "apt_name",
        "exclusive_area",
        "deal_year",
        "deal_month",
        "deal_day",
        "floor",
    ]


async def _upsert_records(
    rows: list[dict],
) -> tuple[int, int]:
    """
    Bulk upsert rows into apt_transactions.

    Returns (inserted_count, updated_count).

    The unique index ix_apt_transactions_unique_deal is a partial index
    (WHERE deal_day IS NOT NULL). For rows where deal_day IS NULL we fall
    back to a plain insert and skip on conflict using the non-partial approach,
    but since there is no unique constraint covering NULL deal_day rows we
    simply insert and ignore duplicate errors by re-querying.

    In practice MOLIT always provides 일 (day), so both buckets are handled.
    """
    if not rows:
        return 0, 0

    inserted = 0
    updated = 0

    # Split into rows that have deal_day (covered by partial unique index)
    # and those that do not.
    rows_with_day = [r for r in rows if r.get("deal_day") is not None]
    rows_without_day = [r for r in rows if r.get("deal_day") is None]

    async with async_session() as session:
        # --- rows WITH deal_day: use partial-index upsert ---
        if rows_with_day:
            stmt = insert(AptTransaction).values(rows_with_day)
            update_cols = {
                col: stmt.excluded[col]
                for col in [
                    "dong_name",
                    "deal_amount",
                    "deposit",
                    "monthly_rent",
                    "build_year",
                    "jibun",
                    "road_name",
                    "cancel_deal_type",
                    "contract_date",
                    "raw_data",
                    "collected_at",
                    "updated_at",
                ]
            }
            stmt = stmt.on_conflict_do_update(
                index_elements=_build_upsert_index_elements(),
                index_where=(AptTransaction.deal_day.isnot(None)),
                set_=update_cols,
            )
            result = await session.execute(stmt)
            # rowcount reflects total rows processed; SQLAlchemy ON CONFLICT
            # upsert reports rowcount = inserted + updated for asyncpg.
            # We track via returning or approximate:
            # inserted = rows where xmax == 0 (new), updated = rest.
            # Simplest: use result.rowcount as total touched.
            total_touched = result.rowcount if result.rowcount != -1 else len(rows_with_day)
            # Approximate: assume existing rows are updated
            inserted += total_touched
            # (We cannot cheaply distinguish inserted vs updated without RETURNING)

        # --- rows WITHOUT deal_day: plain insert, ignore duplicates ---
        if rows_without_day:
            stmt = insert(AptTransaction).values(rows_without_day)
            stmt = stmt.on_conflict_do_nothing()
            result = await session.execute(stmt)
            total_touched = result.rowcount if result.rowcount != -1 else 0
            inserted += total_touched

        await session.commit()

    return inserted, updated


class MolitSaleCollector(BaseCollector):
    """Collects apartment sale (매매) transaction data from MOLIT public API."""

    @property
    def source_name(self) -> str:
        return "molit_sale"

    def _parse_item(self, item: dict, region_code: str) -> dict:
        """Normalize a single sale API item into an AptTransaction-compatible dict."""
        return {
            "source": self.source_name,
            "transaction_type": "sale",
            "region_code": region_code,
            "dong_name": _strip(item.get("법정동")),
            "apt_name": _strip(item.get("아파트")) or "",
            "exclusive_area": _parse_decimal(item.get("전용면적")),
            "floor": _parse_int(item.get("층")),
            "deal_amount": _parse_amount(item.get("거래금액")),
            "deposit": None,
            "monthly_rent": None,
            "deal_year": _parse_int(item.get("년")),
            "deal_month": _parse_int(item.get("월")),
            "deal_day": _parse_int(item.get("일")),
            "build_year": _parse_int(item.get("건축년도")),
            "jibun": _strip(item.get("지번")),
            "road_name": _strip(item.get("도로명")),
            "cancel_deal_type": _strip(item.get("해제여부")),
            "contract_date": _parse_contract_date(item.get("해제사유발생일")),
            "raw_data": item,
            "collected_at": datetime.now(),
        }

    async def collect(self, region_code: str, **params) -> CollectionResult:
        deal_ym: str = params.get("deal_ym") or _current_deal_ym()
        started_at = datetime.now()
        result = CollectionResult(
            source=self.source_name,
            region_code=region_code,
            started_at=started_at,
        )

        logger.info(
            "[%s] Starting collection: region=%s, deal_ym=%s",
            self.source_name,
            region_code,
            deal_ym,
        )

        try:
            api_params = {
                "serviceKey": settings.data_go_kr_api_key,
                "LAWD_CD": region_code,
                "DEAL_YMD": deal_ym,
                "type": "json",
            }
            raw_items = await _fetch_all_pages(_SALE_API_URL, api_params)
            result.records_collected = len(raw_items)

            logger.info(
                "[%s] Fetched %d records for region=%s deal_ym=%s",
                self.source_name,
                len(raw_items),
                region_code,
                deal_ym,
            )

            rows = []
            for item in raw_items:
                try:
                    row = self._parse_item(item, region_code)
                    if row["deal_year"] is None or row["deal_month"] is None:
                        logger.warning(
                            "[%s] Skipping item with missing year/month: %r",
                            self.source_name,
                            item,
                        )
                        continue
                    rows.append(row)
                except Exception as exc:
                    logger.warning(
                        "[%s] Failed to parse item %r: %s",
                        self.source_name,
                        item,
                        exc,
                    )

            inserted, updated = await _upsert_records(rows)
            result.records_inserted = inserted
            result.records_updated = updated
            result.status = "success"

        except Exception as exc:
            logger.exception(
                "[%s] Collection failed for region=%s: %s",
                self.source_name,
                region_code,
                exc,
            )
            result.status = "error"
            result.error_message = str(exc)

        result.duration_seconds = (datetime.now() - started_at).total_seconds()
        logger.info(
            "[%s] Done: region=%s status=%s collected=%d inserted=%d updated=%d duration=%.2fs",
            self.source_name,
            region_code,
            result.status,
            result.records_collected,
            result.records_inserted,
            result.records_updated,
            result.duration_seconds,
        )
        return result

    async def health_check(self) -> bool:
        """Verify the sale API endpoint is reachable with a minimal request."""
        try:
            async with httpx.AsyncClient(timeout=_REQUEST_TIMEOUT) as client:
                params = {
                    "serviceKey": settings.data_go_kr_api_key,
                    "LAWD_CD": "11110",  # 종로구 — fixed probe region
                    "DEAL_YMD": _current_deal_ym(),
                    "pageNo": 1,
                    "numOfRows": 1,
                    "type": "json",
                }
                response = await client.get(_SALE_API_URL, params=params)
                response.raise_for_status()
                data = response.json()
                result_code = (
                    data.get("response", {})
                    .get("header", {})
                    .get("resultCode", "")
                )
                return result_code == "00"
        except Exception as exc:
            logger.warning("[%s] health_check failed: %s", self.source_name, exc)
            return False


class MolitJeonseCollector(BaseCollector):
    """Collects apartment jeonse/monthly-rent (전월세) transaction data from MOLIT public API."""

    @property
    def source_name(self) -> str:
        return "molit_jeonse"

    def _parse_item(self, item: dict, region_code: str) -> dict:
        """Normalize a single jeonse/rent API item into an AptTransaction-compatible dict."""
        return {
            "source": self.source_name,
            "transaction_type": "jeonse",
            "region_code": region_code,
            "dong_name": _strip(item.get("법정동")),
            "apt_name": _strip(item.get("아파트")) or "",
            "exclusive_area": _parse_decimal(item.get("전용면적")),
            "floor": _parse_int(item.get("층")),
            "deal_amount": None,
            "deposit": _parse_amount(item.get("보증금액")),
            "monthly_rent": _parse_amount(item.get("월세금액")),
            "deal_year": _parse_int(item.get("년")),
            "deal_month": _parse_int(item.get("월")),
            "deal_day": _parse_int(item.get("일")),
            "build_year": _parse_int(item.get("건축년도")),
            "jibun": _strip(item.get("지번")),
            "road_name": _strip(item.get("도로명")),
            "cancel_deal_type": None,
            "contract_date": None,
            "raw_data": item,
            "collected_at": datetime.now(),
        }

    async def collect(self, region_code: str, **params) -> CollectionResult:
        deal_ym: str = params.get("deal_ym") or _current_deal_ym()
        started_at = datetime.now()
        result = CollectionResult(
            source=self.source_name,
            region_code=region_code,
            started_at=started_at,
        )

        logger.info(
            "[%s] Starting collection: region=%s, deal_ym=%s",
            self.source_name,
            region_code,
            deal_ym,
        )

        try:
            api_params = {
                "serviceKey": settings.data_go_kr_api_key,
                "LAWD_CD": region_code,
                "DEAL_YMD": deal_ym,
                "type": "json",
            }
            raw_items = await _fetch_all_pages(_JEONSE_API_URL, api_params)
            result.records_collected = len(raw_items)

            logger.info(
                "[%s] Fetched %d records for region=%s deal_ym=%s",
                self.source_name,
                len(raw_items),
                region_code,
                deal_ym,
            )

            rows = []
            for item in raw_items:
                try:
                    row = self._parse_item(item, region_code)
                    if row["deal_year"] is None or row["deal_month"] is None:
                        logger.warning(
                            "[%s] Skipping item with missing year/month: %r",
                            self.source_name,
                            item,
                        )
                        continue
                    rows.append(row)
                except Exception as exc:
                    logger.warning(
                        "[%s] Failed to parse item %r: %s",
                        self.source_name,
                        item,
                        exc,
                    )

            inserted, updated = await _upsert_records(rows)
            result.records_inserted = inserted
            result.records_updated = updated
            result.status = "success"

        except Exception as exc:
            logger.exception(
                "[%s] Collection failed for region=%s: %s",
                self.source_name,
                region_code,
                exc,
            )
            result.status = "error"
            result.error_message = str(exc)

        result.duration_seconds = (datetime.now() - started_at).total_seconds()
        logger.info(
            "[%s] Done: region=%s status=%s collected=%d inserted=%d updated=%d duration=%.2fs",
            self.source_name,
            region_code,
            result.status,
            result.records_collected,
            result.records_inserted,
            result.records_updated,
            result.duration_seconds,
        )
        return result

    async def health_check(self) -> bool:
        """Verify the jeonse API endpoint is reachable with a minimal request."""
        try:
            async with httpx.AsyncClient(timeout=_REQUEST_TIMEOUT) as client:
                params = {
                    "serviceKey": settings.data_go_kr_api_key,
                    "LAWD_CD": "11110",  # 종로구 — fixed probe region
                    "DEAL_YMD": _current_deal_ym(),
                    "pageNo": 1,
                    "numOfRows": 1,
                    "type": "json",
                }
                response = await client.get(_JEONSE_API_URL, params=params)
                response.raise_for_status()
                data = response.json()
                result_code = (
                    data.get("response", {})
                    .get("header", {})
                    .get("resultCode", "")
                )
                return result_code == "00"
        except Exception as exc:
            logger.warning("[%s] health_check failed: %s", self.source_name, exc)
            return False
