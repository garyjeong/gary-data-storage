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

_MAX_RETRIES = 2
_RETRY_BACKOFF = [2, 4]  # seconds
_REQUEST_DELAY = 2.0  # seconds between requests
_REQUEST_TIMEOUT = 30

# Hogangnono internal API endpoint candidates.
# The exact shape is reverse-engineered and may drift; we try each in order.
_REGION_LIST_URL = "https://hogangnono.com/api/apt/region"
_COMPLEX_LIST_URL = "https://hogangnono.com/api/apt/list"
_COMPLEX_DETAIL_URL = "https://hogangnono.com/api/apt/complex"
_COMPLEX_PRICE_URL = "https://hogangnono.com/api/apt/price"
_HEALTH_PROBE_URL = "https://hogangnono.com/api/apt/list"


# ---------------------------------------------------------------------------
# Utility helpers
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
        return Decimal(str(raw))
    except (InvalidOperation, ValueError, TypeError):
        return None


def _to_10digit(region_code: str) -> str:
    """Convert a 5-digit region code to a 10-digit code by appending '00000'."""
    code = region_code.strip()
    if len(code) == 5:
        return code + "00000"
    return code  # already 10-digit or unexpected length; pass through


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------

async def _fetch_with_retry(
    client: httpx.AsyncClient,
    url: str,
    params: dict | None = None,
) -> dict | list | None:
    """
    GET *url* with retry + exponential backoff.

    Returns parsed JSON (dict or list) on success.
    Returns None on 403 / 429 (rate-limit / access-denied) so the caller can
    gracefully skip rather than crash the whole collection run.
    Raises the last exception after exhausting retries for other errors.
    """
    last_exc: Exception | None = None
    for attempt in range(_MAX_RETRIES + 1):
        try:
            response = await client.get(url, params=params)

            if 400 <= response.status_code < 500:
                logger.warning(
                    "[hogangnono] HTTP %d from %s — skipping (client error)",
                    response.status_code,
                    url,
                )
                return None

            response.raise_for_status()
            return response.json()

        except (httpx.HTTPStatusError, httpx.HTTPError) as exc:
            last_exc = exc
            if attempt < _MAX_RETRIES:
                wait = _RETRY_BACKOFF[attempt]
                logger.warning(
                    "[hogangnono] Request failed (attempt %d/%d), retrying in %ds: %s",
                    attempt + 1,
                    _MAX_RETRIES + 1,
                    wait,
                    exc,
                )
                await asyncio.sleep(wait)
            else:
                logger.error(
                    "[hogangnono] All %d attempts failed for %s: %s",
                    _MAX_RETRIES + 1,
                    url,
                    exc,
                )

    raise last_exc  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Region complex list fetching
# ---------------------------------------------------------------------------

async def _fetch_complex_list(client: httpx.AsyncClient, region_code: str) -> list[dict]:
    """
    Try multiple known endpoint patterns to retrieve the apartment complex list
    for *region_code*.  Returns a flat list of raw complex dicts (may be empty).
    """
    code_10 = _to_10digit(region_code)

    # Attempt 1: /api/apt/region?code=<10-digit>
    data = await _fetch_with_retry(client, _REGION_LIST_URL, params={"code": code_10})
    items = _extract_list(data)
    if items:
        logger.debug("[hogangnono] Got %d complexes via /apt/region (code=%s)", len(items), code_10)
        return items

    await asyncio.sleep(_REQUEST_DELAY)

    # Attempt 2: /api/apt/list?cortarNo=<10-digit>
    data = await _fetch_with_retry(client, _COMPLEX_LIST_URL, params={"cortarNo": code_10})
    items = _extract_list(data)
    if items:
        logger.debug("[hogangnono] Got %d complexes via /apt/list (cortarNo=%s)", len(items), code_10)
        return items

    await asyncio.sleep(_REQUEST_DELAY)

    # Attempt 3: /api/apt/region/{region_code} (path param, 5-digit)
    region_path_url = f"https://hogangnono.com/api/apt/region/{region_code}"
    data = await _fetch_with_retry(client, region_path_url)
    items = _extract_list(data)
    if items:
        logger.debug(
            "[hogangnono] Got %d complexes via /apt/region/{code} (code=%s)",
            len(items),
            region_code,
        )
        return items

    logger.warning(
        "[hogangnono] Could not retrieve complex list for region=%s — all endpoint attempts returned empty",
        region_code,
    )
    return []


def _extract_list(data: dict | list | None) -> list[dict]:
    """
    Hogangnono may wrap the list inside various envelope shapes.
    This function tries common patterns and always returns a plain list.
    """
    if data is None:
        return []
    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)]
    if isinstance(data, dict):
        # { "data": [...] }  or  { "result": [...] }  or  { "list": [...] }
        for key in ("data", "result", "list", "items", "complexes", "apts"):
            candidate = data.get(key)
            if isinstance(candidate, list):
                return [item for item in candidate if isinstance(item, dict)]
        # { "data": { "list": [...] } }
        nested = data.get("data")
        if isinstance(nested, dict):
            for key in ("list", "items", "complexes", "apts"):
                candidate = nested.get(key)
                if isinstance(candidate, list):
                    return [item for item in candidate if isinstance(item, dict)]
    return []


# ---------------------------------------------------------------------------
# Complex detail / price fetching
# ---------------------------------------------------------------------------

async def _fetch_complex_detail(
    client: httpx.AsyncClient,
    complex_id: str,
) -> dict | None:
    """Fetch detailed info for a single complex. Returns raw dict or None."""
    data = await _fetch_with_retry(
        client, _COMPLEX_DETAIL_URL, params={"id": complex_id}
    )
    if data is None:
        return None
    if isinstance(data, dict):
        # unwrap common envelope
        for key in ("data", "result"):
            candidate = data.get(key)
            if isinstance(candidate, dict):
                return candidate
        return data
    return None


async def _fetch_complex_price(
    client: httpx.AsyncClient,
    complex_id: str,
) -> dict | None:
    """Fetch price / trend info for a single complex. Returns raw dict or None."""
    data = await _fetch_with_retry(
        client, _COMPLEX_PRICE_URL, params={"id": complex_id}
    )
    if data is None:
        return None
    if isinstance(data, dict):
        for key in ("data", "result"):
            candidate = data.get(key)
            if isinstance(candidate, dict):
                return candidate
        return data
    return None


# ---------------------------------------------------------------------------
# Row builders
# ---------------------------------------------------------------------------

def _extract_complex_id(raw: dict) -> str | None:
    """Try common field names that hold the complex identifier."""
    for key in ("id", "aptId", "complexId", "apt_id", "complex_id", "hgnnNo"):
        val = raw.get(key)
        if val is not None:
            return str(val)
    return None


def _build_complex_row(
    raw: dict,
    detail: dict | None,
    region_code: str,
) -> dict:
    """
    Map raw API fields to AptComplex column dict.

    Field names are best-effort guesses based on typical Hogangnono API shapes.
    Any unknown or missing field gracefully falls back to None.
    The full raw payload is stored in raw_data JSONB for future reference.
    """
    merged = {**raw}
    if detail:
        merged.update(detail)

    complex_id = _extract_complex_id(merged)

    # apt_name candidates
    apt_name = (
        _strip(merged.get("name"))
        or _strip(merged.get("aptName"))
        or _strip(merged.get("apt_name"))
        or _strip(merged.get("complexName"))
        or ""
    )

    # address candidates
    address = (
        _strip(merged.get("address"))
        or _strip(merged.get("addr"))
        or _strip(merged.get("roadAddr"))
        or _strip(merged.get("jibunAddr"))
    )

    # dong_name: extract from address or dedicated field
    dong_name = (
        _strip(merged.get("dong"))
        or _strip(merged.get("dongName"))
        or _strip(merged.get("legalDong"))
    )

    # coordinates
    lat = _parse_decimal(
        merged.get("lat") or merged.get("latitude") or merged.get("y")
    )
    lng = _parse_decimal(
        merged.get("lng") or merged.get("longitude") or merged.get("x")
    )

    # numeric fields
    total_units = _parse_int(
        merged.get("totalUnit")
        or merged.get("total_unit")
        or merged.get("totalHousehold")
        or merged.get("세대수")
    )
    total_dong = _parse_int(
        merged.get("dongCount")
        or merged.get("dong_count")
        or merged.get("totalDong")
        or merged.get("동수")
    )
    build_year = _parse_int(
        merged.get("buildYear")
        or merged.get("build_year")
        or merged.get("useApproveYmd", "")[:4]
        or merged.get("준공연도")
    )

    # floor area range
    floor_area_max = _parse_decimal(
        merged.get("areaMax")
        or merged.get("area_max")
        or merged.get("maxExclusiveArea")
    )
    floor_area_min = _parse_decimal(
        merged.get("areaMin")
        or merged.get("area_min")
        or merged.get("minExclusiveArea")
    )

    return {
        "source": "hogangnono",
        "region_code": region_code,
        "dong_name": dong_name,
        "apt_name": apt_name,
        "address": address,
        "total_units": total_units,
        "total_dong": total_dong,
        "build_year": build_year,
        "floor_area_max": floor_area_max,
        "floor_area_min": floor_area_min,
        "latitude": lat,
        "longitude": lng,
        "source_complex_id": complex_id,
        "raw_data": merged,
        "collected_at": datetime.now(),
    }


def _build_listing_rows_from_price(
    price_data: dict | None,
    complex_row: dict,
    region_code: str,
) -> list[dict]:
    """
    Hogangnono is primarily an analytics site, but recent transaction /
    asking-price data may be present in the price endpoint response.

    If usable listing data is found, map it into AptListing rows.
    Otherwise return an empty list — the data will still be in raw_data.
    """
    if not price_data:
        return []

    rows: list[dict] = []
    apt_name = complex_row["apt_name"]
    dong_name = complex_row["dong_name"]
    complex_id = complex_row["source_complex_id"]

    # Try common list shapes inside price payload
    candidates: list[dict] = []
    for key in ("list", "items", "prices", "data", "recentPrices", "tradeList"):
        val = price_data.get(key)
        if isinstance(val, list):
            candidates = [v for v in val if isinstance(v, dict)]
            break

    for idx, item in enumerate(candidates):
        # Determine listing type
        trade_type = (
            _strip(item.get("tradeType"))
            or _strip(item.get("type"))
            or _strip(item.get("dealType"))
            or "sale"
        ).lower()

        # Normalise trade_type to listing_type domain values
        if trade_type in ("매매", "sale", "a1"):
            listing_type = "sale"
        elif trade_type in ("전세", "jeonse", "b1"):
            listing_type = "jeonse"
        elif trade_type in ("월세", "monthly", "b2"):
            listing_type = "monthly"
        else:
            listing_type = "sale"

        asking_price = _parse_int(
            item.get("price")
            or item.get("dealAmount")
            or item.get("askingPrice")
            or item.get("거래금액")
        )
        deposit = _parse_int(
            item.get("deposit") or item.get("보증금")
        )
        exclusive_area = _parse_decimal(
            item.get("area")
            or item.get("exclusiveArea")
            or item.get("전용면적")
        )
        floor_ = _parse_int(
            item.get("floor") or item.get("층")
        )

        # Build a deterministic source_listing_id so upsert works correctly.
        source_listing_id = (
            _strip(item.get("id"))
            or _strip(item.get("listingId"))
            or (f"{complex_id}_{listing_type}_{idx}" if complex_id else None)
        )

        rows.append({
            "source": "hogangnono",
            "listing_type": listing_type,
            "region_code": region_code,
            "dong_name": dong_name,
            "apt_name": apt_name,
            "exclusive_area": exclusive_area,
            "floor": floor_,
            "asking_price": asking_price,
            "deposit": deposit,
            "description": None,
            "source_listing_id": source_listing_id,
            "listing_url": (
                f"https://hogangnono.com/apt/{complex_id}" if complex_id else None
            ),
            "is_active": True,
            "listed_at": None,
            "raw_data": item,
            "collected_at": datetime.now(),
        })

    return rows


# ---------------------------------------------------------------------------
# DB upsert helpers
# ---------------------------------------------------------------------------

async def _upsert_complexes(rows: list[dict]) -> tuple[int, int]:
    """Upsert AptComplex rows. Returns (inserted, updated) approximate counts."""
    if not rows:
        return 0, 0

    async with async_session() as session:
        stmt = insert(AptComplex).values(rows)
        update_cols = {
            col: stmt.excluded[col]
            for col in [
                "dong_name",
                "apt_name",
                "address",
                "total_units",
                "total_dong",
                "build_year",
                "floor_area_max",
                "floor_area_min",
                "latitude",
                "longitude",
                "raw_data",
                "collected_at",
            ]
        }
        stmt = stmt.on_conflict_do_update(
            index_elements=["source", "source_complex_id"],
            index_where=(AptComplex.source_complex_id.isnot(None)),
            set_=update_cols,
        )
        result = await session.execute(stmt)
        await session.commit()
        total = result.rowcount if result.rowcount != -1 else len(rows)
        return total, 0


async def _upsert_listings(rows: list[dict]) -> tuple[int, int]:
    """Upsert AptListing rows. Returns (inserted, updated) approximate counts."""
    if not rows:
        return 0, 0

    rows_with_id = [r for r in rows if r.get("source_listing_id") is not None]
    rows_without_id = [r for r in rows if r.get("source_listing_id") is None]

    inserted = 0

    async with async_session() as session:
        if rows_with_id:
            stmt = insert(AptListing).values(rows_with_id)
            update_cols = {
                col: stmt.excluded[col]
                for col in [
                    "listing_type",
                    "exclusive_area",
                    "floor",
                    "asking_price",
                    "deposit",
                    "is_active",
                    "raw_data",
                    "collected_at",
                ]
            }
            stmt = stmt.on_conflict_do_update(
                index_elements=["source", "source_listing_id"],
                index_where=(AptListing.source_listing_id.isnot(None)),
                set_=update_cols,
            )
            result = await session.execute(stmt)
            inserted += result.rowcount if result.rowcount != -1 else len(rows_with_id)

        if rows_without_id:
            stmt = insert(AptListing).values(rows_without_id)
            stmt = stmt.on_conflict_do_nothing()
            result = await session.execute(stmt)
            inserted += result.rowcount if result.rowcount != -1 else 0

        await session.commit()

    return inserted, 0


# ---------------------------------------------------------------------------
# Collector
# ---------------------------------------------------------------------------

class HogangnonoCollector(BaseCollector):
    """
    Collects apartment complex data from Hogangnono (호갱노노).

    Hogangnono is primarily an analytics / price-trend platform, so the main
    output is AptComplex enrichment.  If the price endpoint returns listing-
    level data, those records are stored in AptListing as well.

    All collected raw payloads are stored in raw_data JSONB so that schema
    changes on Hogangnono's side do not permanently lose data.
    """

    BASE_URL = "https://hogangnono.com/api"
    HEADERS = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        ),
        "Accept": "application/json",
        "Referer": "https://hogangnono.com/",
    }

    @property
    def source_name(self) -> str:
        return "hogangnono"

    async def collect(self, region_code: str, **params) -> CollectionResult:
        started_at = datetime.now()
        result = CollectionResult(
            source=self.source_name,
            region_code=region_code,
            started_at=started_at,
        )

        logger.info(
            "[%s] Starting collection: region=%s",
            self.source_name,
            region_code,
        )

        try:
            async with httpx.AsyncClient(
                headers=self.HEADERS,
                timeout=_REQUEST_TIMEOUT,
                follow_redirects=True,
            ) as client:
                # Step 1: fetch complex list for the region
                raw_complexes = await _fetch_complex_list(client, region_code)
                result.records_collected = len(raw_complexes)

                logger.info(
                    "[%s] Found %d complexes for region=%s",
                    self.source_name,
                    len(raw_complexes),
                    region_code,
                )

                complex_rows: list[dict] = []
                listing_rows: list[dict] = []

                for raw in raw_complexes:
                    try:
                        complex_id = _extract_complex_id(raw)

                        # Step 2: fetch detail per complex (with rate-limit delay)
                        await asyncio.sleep(_REQUEST_DELAY)
                        detail: dict | None = None
                        price_data: dict | None = None

                        if complex_id:
                            detail = await _fetch_complex_detail(client, complex_id)
                            await asyncio.sleep(_REQUEST_DELAY)
                            price_data = await _fetch_complex_price(client, complex_id)

                        # Step 3: build AptComplex row
                        complex_row = _build_complex_row(raw, detail, region_code)
                        complex_rows.append(complex_row)

                        # Step 4: extract any listing data from price payload
                        listings = _build_listing_rows_from_price(
                            price_data, complex_row, region_code
                        )
                        listing_rows.extend(listings)

                    except Exception as exc:
                        logger.warning(
                            "[%s] Failed to process complex %r: %s",
                            self.source_name,
                            raw,
                            exc,
                        )

                # Step 5: persist to DB
                inserted_c, updated_c = await _upsert_complexes(complex_rows)
                inserted_l, updated_l = await _upsert_listings(listing_rows)

                result.records_inserted = inserted_c + inserted_l
                result.records_updated = updated_c + updated_l
                result.status = "success"

                logger.info(
                    "[%s] Stored %d complexes, %d listing rows for region=%s",
                    self.source_name,
                    len(complex_rows),
                    len(listing_rows),
                    region_code,
                )

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
        """
        Verify that Hogangnono's API is reachable.

        Uses 종로구 (11110 → 1111000000) as a fixed probe region.
        A 403 / 429 is treated as "reachable but blocked" and returns True
        so the caller can decide whether to proceed.
        """
        probe_code = _to_10digit("11110")
        try:
            async with httpx.AsyncClient(
                headers=self.HEADERS,
                timeout=_REQUEST_TIMEOUT,
                follow_redirects=True,
            ) as client:
                response = await client.get(
                    _HEALTH_PROBE_URL,
                    params={"cortarNo": probe_code},
                )
                if response.status_code in (200, 403, 429):
                    logger.debug(
                        "[%s] health_check status=%d", self.source_name, response.status_code
                    )
                    return True
                return False
        except Exception as exc:
            logger.warning("[%s] health_check failed: %s", self.source_name, exc)
            return False
