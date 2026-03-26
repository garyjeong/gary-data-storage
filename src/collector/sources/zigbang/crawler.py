"""
Zigbang apartment listing crawler.

Collects AptComplex and AptListing records from Zigbang's internal API.

API flow:
  1. POST /v3/locals/apartments  →  list of apartment complexes for a dong_code
  2. GET  /v2/items/apartments    →  listings per complex, per sales_type (매매 / 전세)

These are reverse-engineered private endpoints and may change without notice.
All failures are handled gracefully so that partial data is preserved.
"""

import asyncio
import logging
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any

import httpx
from sqlalchemy.dialects.postgresql import insert

from src.collector.base import BaseCollector, CollectionResult
from src.db.connection import async_session
from src.db.models import AptComplex, AptListing

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_BASE_URL = "https://apis.zigbang.com"
_COMPLEXES_URL = f"{_BASE_URL}/v3/locals/apartments"
_ITEMS_URL = f"{_BASE_URL}/v2/items/apartments"

_SALES_TYPES = ["매매", "전세"]

_REQUEST_TIMEOUT = 30
_REQUEST_DELAY = 2.0      # seconds between outbound requests
_MAX_RETRIES = 2
_RETRY_BACKOFF = [3, 6]   # seconds; length must equal _MAX_RETRIES

_ITEMS_PAGE_SIZE = 50

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json",
    "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
    "Referer": "https://www.zigbang.com/",
    "Origin": "https://www.zigbang.com",
}

# ---------------------------------------------------------------------------
# Low-level parsing helpers
# ---------------------------------------------------------------------------


def _strip(value: Any) -> str | None:
    if value is None:
        return None
    s = str(value).strip()
    return s if s else None


def _parse_int(value: Any) -> int | None:
    raw = _strip(value)
    if raw is None:
        return None
    try:
        return int(float(raw))
    except (ValueError, TypeError):
        return None


def _parse_decimal(value: Any) -> Decimal | None:
    raw = _strip(value)
    if raw is None:
        return None
    try:
        return Decimal(raw)
    except (InvalidOperation, ValueError, TypeError):
        return None


def _parse_price_manwon(value: Any) -> int | None:
    """
    Zigbang prices are in 만원 as integers or strings.
    Returns the value as-is (만원), or None on failure.
    """
    raw = _strip(value)
    if raw is None:
        return None
    try:
        return int(str(raw).replace(",", ""))
    except (ValueError, TypeError):
        return None


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------


async def _get_with_retry(
    client: httpx.AsyncClient,
    url: str,
    *,
    params: dict | None = None,
) -> dict | list | None:
    """GET with retry/backoff. Returns parsed JSON or None on terminal failure."""
    last_exc: Exception | None = None
    for attempt in range(_MAX_RETRIES):
        try:
            response = await client.get(url, params=params)
            response.raise_for_status()
            return response.json()
        except (httpx.HTTPError, ValueError) as exc:
            last_exc = exc
            if attempt < _MAX_RETRIES - 1:
                wait = _RETRY_BACKOFF[attempt]
                logger.warning(
                    "[zigbang] GET %s failed (attempt %d/%d), retry in %ds: %s",
                    url, attempt + 1, _MAX_RETRIES, wait, exc,
                )
                await asyncio.sleep(wait)
    logger.error("[zigbang] GET %s: all %d attempts failed: %s", url, _MAX_RETRIES, last_exc)
    return None


async def _post_with_retry(
    client: httpx.AsyncClient,
    url: str,
    *,
    json_body: dict,
) -> dict | list | None:
    """POST with retry/backoff. Returns parsed JSON or None on terminal failure."""
    last_exc: Exception | None = None
    for attempt in range(_MAX_RETRIES):
        try:
            response = await client.post(url, json=json_body)
            response.raise_for_status()
            return response.json()
        except (httpx.HTTPError, ValueError) as exc:
            last_exc = exc
            if attempt < _MAX_RETRIES - 1:
                wait = _RETRY_BACKOFF[attempt]
                logger.warning(
                    "[zigbang] POST %s failed (attempt %d/%d), retry in %ds: %s",
                    url, attempt + 1, _MAX_RETRIES, wait, exc,
                )
                await asyncio.sleep(wait)
    logger.error("[zigbang] POST %s: all %d attempts failed: %s", url, _MAX_RETRIES, last_exc)
    return None


# ---------------------------------------------------------------------------
# API fetch functions
# ---------------------------------------------------------------------------


async def _fetch_complexes(client: httpx.AsyncClient, dong_code: str) -> list[dict]:
    """
    Fetch apartment complex list for a dong_code.

    Tries POST /v3/locals/apartments first; falls back to GET with query param
    if the response shape is unexpected, since Zigbang's internal API has varied
    across versions.
    """
    await asyncio.sleep(_REQUEST_DELAY)
    data = await _post_with_retry(
        client,
        _COMPLEXES_URL,
        json_body={"code": dong_code, "sales_type": _SALES_TYPES},
    )

    complexes = _extract_complex_list(data)
    if complexes is not None:
        return complexes

    # Fallback: GET variant seen in older API versions
    logger.debug("[zigbang] POST complexes returned unexpected shape; trying GET fallback")
    await asyncio.sleep(_REQUEST_DELAY)
    data = await _get_with_retry(
        client,
        _COMPLEXES_URL,
        params={"code": dong_code, "sales_type": "매매"},
    )
    return _extract_complex_list(data) or []


def _extract_complex_list(data: Any) -> list[dict] | None:
    """
    Zigbang complex API response has varied shapes across versions.
    Returns a list of raw complex dicts, or None if the shape is unrecognised.
    """
    if data is None:
        return None
    # Shape A: {"items": [...]}
    if isinstance(data, dict):
        items = data.get("items") or data.get("data") or data.get("result")
        if isinstance(items, list):
            return items
        # Shape B: {"apartments": [...]}
        apts = data.get("apartments")
        if isinstance(apts, list):
            return apts
    # Shape C: raw list
    if isinstance(data, list):
        return data
    return None


async def _fetch_items_for_complex(
    client: httpx.AsyncClient,
    complex_id: str | int,
    sales_type: str,
) -> list[dict]:
    """
    Fetch all listing items for a given complex_id and sales_type, paginating
    until the API returns an empty page.
    """
    all_items: list[dict] = []
    page = 1

    while True:
        await asyncio.sleep(_REQUEST_DELAY)
        params = {
            "complex_id": complex_id,
            "sales_type": sales_type,
            "page": page,
            "size": _ITEMS_PAGE_SIZE,
        }
        data = await _get_with_retry(client, _ITEMS_URL, params=params)
        page_items = _extract_item_list(data)
        if not page_items:
            break
        all_items.extend(page_items)
        logger.debug(
            "[zigbang] complex=%s type=%s page=%d fetched=%d total=%d",
            complex_id, sales_type, page, len(page_items), len(all_items),
        )
        if len(page_items) < _ITEMS_PAGE_SIZE:
            # Last page
            break
        page += 1

    return all_items


def _extract_item_list(data: Any) -> list[dict]:
    """
    Extract listing items from an API response. Returns empty list on failure.
    """
    if data is None:
        return []
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for key in ("items", "data", "result", "list"):
            candidate = data.get(key)
            if isinstance(candidate, list):
                return candidate
    return []


# ---------------------------------------------------------------------------
# Parsing — complex
# ---------------------------------------------------------------------------


def _parse_complex_row(raw: dict, region_code: str) -> dict | None:
    """
    Normalise a raw Zigbang complex object into an AptComplex-compatible dict.
    Returns None if the mandatory apt_name field cannot be resolved.
    """
    # Zigbang uses various field names across API versions
    complex_id = (
        _strip(raw.get("id"))
        or _strip(raw.get("complex_id"))
        or _strip(raw.get("apartment_complex_id"))
    )
    apt_name = (
        _strip(raw.get("name"))
        or _strip(raw.get("complex_name"))
        or _strip(raw.get("apt_name"))
        or _strip(raw.get("apartment_name"))
    )
    if not apt_name:
        return None

    address = (
        _strip(raw.get("address"))
        or _strip(raw.get("road_address"))
        or _strip(raw.get("jibun_address"))
    )

    # Build year — may be stored as full year int or string like "2005"
    build_year = _parse_int(
        raw.get("build_year")
        or raw.get("buildYear")
        or raw.get("completion_year")
        or raw.get("use_approve_ymd", "")[:4]  # "20051231" → "2005"
    )

    total_units = _parse_int(
        raw.get("total_household")
        or raw.get("household_count")
        or raw.get("total_units")
        or raw.get("세대수")
    )
    total_dong = _parse_int(
        raw.get("total_dong")
        or raw.get("dong_count")
    )

    lat = _parse_decimal(raw.get("latitude") or raw.get("lat") or raw.get("y"))
    lng = _parse_decimal(raw.get("longitude") or raw.get("lng") or raw.get("x"))

    # Floor area range: Zigbang may expose min/max exclusive_area across unit types
    floor_area_min = _parse_decimal(
        raw.get("floor_area_min")
        or raw.get("exclusive_area_min")
        or raw.get("min_area")
    )
    floor_area_max = _parse_decimal(
        raw.get("floor_area_max")
        or raw.get("exclusive_area_max")
        or raw.get("max_area")
    )

    dong_name = (
        _strip(raw.get("dong_name"))
        or _strip(raw.get("dong"))
        or _strip(raw.get("법정동"))
    )

    return {
        "source": "zigbang",
        "region_code": region_code,
        "dong_name": dong_name,
        "apt_name": apt_name,
        "address": address,
        "total_units": total_units,
        "total_dong": total_dong,
        "build_year": build_year,
        "floor_area_min": floor_area_min,
        "floor_area_max": floor_area_max,
        "latitude": lat,
        "longitude": lng,
        "source_complex_id": str(complex_id) if complex_id else None,
        "raw_data": raw,
        "collected_at": datetime.now(),
    }


# ---------------------------------------------------------------------------
# Parsing — listing item
# ---------------------------------------------------------------------------


def _parse_listing_row(
    raw: dict,
    region_code: str,
    complex_row: dict,
    sales_type: str,
) -> dict | None:
    """
    Normalise a raw Zigbang item object into an AptListing-compatible dict.
    Returns None if mandatory fields are absent.
    """
    listing_id = (
        _strip(raw.get("item_id"))
        or _strip(raw.get("id"))
        or _strip(raw.get("listing_id"))
    )
    apt_name = complex_row.get("apt_name") or _strip(raw.get("complex_name")) or ""
    if not apt_name:
        return None

    # listing_type maps Zigbang sales_type to our schema value
    listing_type_map = {"매매": "sale", "전세": "jeonse", "월세": "monthly"}
    raw_sales_type = (
        _strip(raw.get("sales_type"))
        or _strip(raw.get("type"))
        or sales_type
    )
    listing_type = listing_type_map.get(raw_sales_type, raw_sales_type)

    # Price fields: Zigbang stores all prices in 만원
    # For 매매: price = asking_price, deposit = None
    # For 전세: price = deposit, deposit used as such
    # For 월세: price = deposit (보증금), monthly_rent is separate (not in our schema, stored in raw_data)
    price_raw = (
        raw.get("price")
        or raw.get("asking_price")
        or raw.get("deal_price")
    )
    deposit_raw = (
        raw.get("deposit")
        or raw.get("보증금")
    )

    if raw_sales_type == "매매":
        asking_price = _parse_price_manwon(price_raw)
        deposit = None
    elif raw_sales_type in ("전세", "월세"):
        asking_price = None
        deposit = _parse_price_manwon(deposit_raw or price_raw)
    else:
        asking_price = _parse_price_manwon(price_raw)
        deposit = _parse_price_manwon(deposit_raw)

    exclusive_area = _parse_decimal(
        raw.get("exclusive_area")
        or raw.get("area")
        or raw.get("전용면적")
    )
    floor = _parse_int(
        raw.get("floor")
        or raw.get("층")
    )
    description = (
        _strip(raw.get("description"))
        or _strip(raw.get("memo"))
        or _strip(raw.get("content"))
    )

    # Build listing URL from item_id if available
    listing_url: str | None = None
    if listing_id:
        listing_url = f"https://www.zigbang.com/home/apt/items/{listing_id}"

    dong_name = complex_row.get("dong_name") or _strip(raw.get("dong_name"))

    # listed_at: Zigbang may return registration_date or similar
    listed_at = None
    for date_key in ("registration_date", "listed_at", "created_at", "reg_date"):
        raw_date = _strip(raw.get(date_key))
        if raw_date:
            for fmt in ("%Y-%m-%d", "%Y%m%d", "%Y-%m-%dT%H:%M:%S"):
                try:
                    listed_at = datetime.strptime(raw_date[:10], fmt[:len(raw_date[:10])]).date()
                    break
                except ValueError:
                    continue
            if listed_at:
                break

    return {
        "source": "zigbang",
        "listing_type": listing_type,
        "region_code": region_code,
        "dong_name": dong_name,
        "apt_name": apt_name,
        "exclusive_area": exclusive_area,
        "floor": floor,
        "asking_price": asking_price,
        "deposit": deposit,
        "description": description,
        "source_listing_id": str(listing_id) if listing_id else None,
        "listing_url": listing_url,
        "is_active": True,
        "listed_at": listed_at,
        "raw_data": raw,
        "collected_at": datetime.now(),
    }


# ---------------------------------------------------------------------------
# DB upsert helpers
# ---------------------------------------------------------------------------


async def _upsert_complexes(rows: list[dict]) -> tuple[int, int]:
    """
    Bulk upsert AptComplex rows.
    Conflict target: partial unique index on (source, source_complex_id)
    WHERE source_complex_id IS NOT NULL.
    Returns (total_touched, 0) — we cannot cheaply distinguish insert vs update.
    """
    if not rows:
        return 0, 0

    rows_with_id = [r for r in rows if r.get("source_complex_id") is not None]
    rows_without_id = [r for r in rows if r.get("source_complex_id") is None]

    total = 0
    async with async_session() as session:
        if rows_with_id:
            stmt = insert(AptComplex).values(rows_with_id)
            stmt = stmt.on_conflict_do_update(
                index_elements=["source", "source_complex_id"],
                index_where=(AptComplex.source_complex_id.isnot(None)),
                set_={
                    col: stmt.excluded[col]
                    for col in [
                        "dong_name",
                        "apt_name",
                        "address",
                        "total_units",
                        "total_dong",
                        "build_year",
                        "floor_area_min",
                        "floor_area_max",
                        "latitude",
                        "longitude",
                        "raw_data",
                        "collected_at",
                    ]
                },
            )
            result = await session.execute(stmt)
            total += result.rowcount if result.rowcount != -1 else len(rows_with_id)

        if rows_without_id:
            stmt = insert(AptComplex).values(rows_without_id)
            stmt = stmt.on_conflict_do_nothing()
            result = await session.execute(stmt)
            total += result.rowcount if result.rowcount != -1 else 0

        await session.commit()

    return total, 0


async def _upsert_listings(rows: list[dict]) -> tuple[int, int]:
    """
    Bulk upsert AptListing rows.
    Conflict target: partial unique index on (source, source_listing_id)
    WHERE source_listing_id IS NOT NULL.
    Returns (total_touched, 0).
    """
    if not rows:
        return 0, 0

    rows_with_id = [r for r in rows if r.get("source_listing_id") is not None]
    rows_without_id = [r for r in rows if r.get("source_listing_id") is None]

    total = 0
    async with async_session() as session:
        if rows_with_id:
            stmt = insert(AptListing).values(rows_with_id)
            stmt = stmt.on_conflict_do_update(
                index_elements=["source", "source_listing_id"],
                index_where=(AptListing.source_listing_id.isnot(None)),
                set_={
                    col: stmt.excluded[col]
                    for col in [
                        "listing_type",
                        "dong_name",
                        "apt_name",
                        "exclusive_area",
                        "floor",
                        "asking_price",
                        "deposit",
                        "description",
                        "listing_url",
                        "is_active",
                        "listed_at",
                        "raw_data",
                        "collected_at",
                    ]
                },
            )
            result = await session.execute(stmt)
            total += result.rowcount if result.rowcount != -1 else len(rows_with_id)

        if rows_without_id:
            stmt = insert(AptListing).values(rows_without_id)
            stmt = stmt.on_conflict_do_nothing()
            result = await session.execute(stmt)
            total += result.rowcount if result.rowcount != -1 else 0

        await session.commit()

    return total, 0


# ---------------------------------------------------------------------------
# Collector class
# ---------------------------------------------------------------------------


class ZigbangCollector(BaseCollector):
    """
    Collects apartment complex info and active listings from Zigbang.

    The region_code passed to collect() is treated as a Zigbang dong_code
    (typically a 10-digit legal dong code, e.g. '1168010600' for 개포1동).
    Zigbang's internal API accepts both 5-digit sigungu codes and longer
    dong-level codes; we pass the value through directly so the caller can
    control granularity.
    """

    @property
    def source_name(self) -> str:
        return "zigbang"

    async def collect(self, region_code: str, **params) -> CollectionResult:
        """
        Collect all apartment listings for region_code.

        Steps:
          1. Fetch complex list for the dong_code.
          2. For each complex, fetch listings for 매매 and 전세.
          3. Upsert AptComplex and AptListing records.
        """
        started_at = datetime.now()
        result = CollectionResult(
            source=self.source_name,
            region_code=region_code,
            started_at=started_at,
        )

        logger.info("[zigbang] Starting collection: region=%s", region_code)

        try:
            async with httpx.AsyncClient(
                headers=_HEADERS,
                timeout=_REQUEST_TIMEOUT,
                follow_redirects=True,
            ) as client:
                # --- Step 1: Fetch complex list ---
                raw_complexes = await _fetch_complexes(client, region_code)
                logger.info(
                    "[zigbang] region=%s: found %d complexes",
                    region_code, len(raw_complexes),
                )

                complex_rows: list[dict] = []
                for raw_c in raw_complexes:
                    try:
                        row = _parse_complex_row(raw_c, region_code)
                        if row:
                            complex_rows.append(row)
                    except Exception as exc:
                        logger.warning(
                            "[zigbang] Failed to parse complex %r: %s", raw_c, exc
                        )

                # --- Step 2: Fetch listings per complex ---
                listing_rows: list[dict] = []
                for complex_row in complex_rows:
                    complex_id = complex_row.get("source_complex_id")
                    if not complex_id:
                        continue

                    for sales_type in _SALES_TYPES:
                        try:
                            raw_items = await _fetch_items_for_complex(
                                client, complex_id, sales_type
                            )
                            for raw_item in raw_items:
                                try:
                                    listing = _parse_listing_row(
                                        raw_item, region_code, complex_row, sales_type
                                    )
                                    if listing:
                                        listing_rows.append(listing)
                                except Exception as exc:
                                    logger.warning(
                                        "[zigbang] Failed to parse item %r: %s",
                                        raw_item, exc,
                                    )
                        except Exception as exc:
                            logger.warning(
                                "[zigbang] Failed to fetch items complex=%s type=%s: %s",
                                complex_id, sales_type, exc,
                            )

            result.records_collected = len(listing_rows)
            logger.info(
                "[zigbang] region=%s: %d complexes, %d listings to upsert",
                region_code, len(complex_rows), len(listing_rows),
            )

            # --- Step 3: Upsert ---
            c_inserted, _ = await _upsert_complexes(complex_rows)
            l_inserted, _ = await _upsert_listings(listing_rows)

            result.records_inserted = c_inserted + l_inserted
            result.records_updated = 0  # Not tracked separately (see upsert helpers)
            result.status = "success"

        except Exception as exc:
            logger.exception(
                "[zigbang] Collection failed for region=%s: %s", region_code, exc
            )
            result.status = "error"
            result.error_message = str(exc)

        result.duration_seconds = (datetime.now() - started_at).total_seconds()
        logger.info(
            "[zigbang] Done: region=%s status=%s collected=%d inserted=%d duration=%.2fs",
            region_code,
            result.status,
            result.records_collected,
            result.records_inserted,
            result.duration_seconds,
        )
        return result

    async def health_check(self) -> bool:
        """
        Verify that the Zigbang API is reachable by probing the complex endpoint
        with a known stable dong_code (강남구 개포1동: 1168010600).
        """
        probe_code = "1168010600"
        try:
            async with httpx.AsyncClient(
                headers=_HEADERS,
                timeout=_REQUEST_TIMEOUT,
                follow_redirects=True,
            ) as client:
                data = await _post_with_retry(
                    client,
                    _COMPLEXES_URL,
                    json_body={"code": probe_code, "sales_type": _SALES_TYPES},
                )
                if data is None:
                    return False
                complexes = _extract_complex_list(data)
                # A non-None return (even empty list) means the endpoint responded
                return complexes is not None
        except Exception as exc:
            logger.warning("[zigbang] health_check failed: %s", exc)
            return False
