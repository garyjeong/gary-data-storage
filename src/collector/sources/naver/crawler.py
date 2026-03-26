"""
Naver Real Estate (new.land.naver.com) crawler.

Reverse-engineered internal API. Naver uses its own 'cortarNo' region code
system (10-digit 법정동코드). We derive it by zero-padding our 5-digit
시군구코드 to 10 digits (e.g. "11110" -> "1111000000").

Flow:
  1. Fetch apartment complex list for the cortarNo region.
  2. For each complex, fetch active listings (매매 + 전세).
  3. Upsert AptComplex and AptListing records.

Naver is rate-sensitive; we sleep 1–2 s between requests and respect
429 / 403 responses by backing off and marking the run as "partial".
"""

import asyncio
import logging
import random
from datetime import datetime, date
from decimal import Decimal, InvalidOperation
from typing import Any

import httpx
from sqlalchemy.dialects.postgresql import insert

from src.collector.base import BaseCollector, CollectionResult
from src.db.connection import async_session
from src.db.models import AptListing, AptComplex

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

BASE_URL = "https://new.land.naver.com/api"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
    "Referer": "https://new.land.naver.com/complexes",
}

_REQUEST_TIMEOUT = 20
_MAX_RETRIES = 3
_RETRY_BACKOFF = [3, 6, 12]  # seconds — be gentle on Naver

# Listing pages to fetch per complex (Naver caps at ~200 listings per complex)
_MAX_LISTING_PAGES = 5
_LISTING_PAGE_SIZE = 20

# Trade types to collect: A1=매매, B1=전세
_TRADE_TYPES = ["A1", "B1"]
_TRADE_TYPE_MAP = {"A1": "sale", "B1": "jeonse", "B2": "monthly_rent"}

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _to_cortar_no(region_code: str) -> str:
    """Convert 5-digit 시군구코드 to Naver 10-digit cortarNo."""
    code = region_code.strip()
    if len(code) == 10:
        return code
    # Pad with zeros to 10 digits (법정동코드 형식)
    return code.ljust(10, "0")


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


def _parse_price_manwon(value: Any) -> int | None:
    """
    Naver prices are in 만원. We store as 만원 integer to match other sources.
    Returns None if unparseable.
    """
    raw = _strip(value)
    if raw is None:
        return None
    # Remove commas and whitespace
    raw = raw.replace(",", "").replace(" ", "")
    try:
        return int(float(raw))
    except (ValueError, TypeError):
        return None


def _parse_listed_at(value: Any) -> date | None:
    """Parse Naver article date strings like '20240115' or '2024-01-15'."""
    raw = _strip(value)
    if raw is None:
        return None
    for fmt in ("%Y%m%d", "%Y-%m-%d", "%Y.%m.%d"):
        try:
            return datetime.strptime(raw, fmt).date()
        except (ValueError, TypeError):
            continue
    return None


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------


async def _get_json(
    client: httpx.AsyncClient,
    url: str,
    params: dict | None = None,
) -> dict | list | None:
    """
    GET a JSON endpoint with retry logic.

    Returns parsed JSON on success, None if the resource is empty or
    raises on unrecoverable errors (rate-limit propagated as RateLimitError).
    """
    last_exc: Exception | None = None
    for attempt in range(_MAX_RETRIES):
        try:
            resp = await client.get(url, params=params, headers=HEADERS)

            if resp.status_code in (403, 429):
                wait = _RETRY_BACKOFF[min(attempt, len(_RETRY_BACKOFF) - 1)]
                logger.warning(
                    "Rate-limited (%d) by Naver on %s, waiting %ds (attempt %d/%d)",
                    resp.status_code,
                    url,
                    wait,
                    attempt + 1,
                    _MAX_RETRIES,
                )
                await asyncio.sleep(wait)
                last_exc = httpx.HTTPStatusError(
                    f"HTTP {resp.status_code}",
                    request=resp.request,
                    response=resp,
                )
                continue

            resp.raise_for_status()

            if not resp.content:
                return None

            return resp.json()

        except (httpx.HTTPError, ValueError) as exc:
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
                logger.error("All %d attempts failed for %s: %s", _MAX_RETRIES, url, exc)

    if last_exc is not None:
        raise last_exc
    return None


async def _polite_sleep() -> None:
    """Sleep 1–2 seconds between requests to avoid hammering Naver."""
    await asyncio.sleep(1.0 + random.random())


# ---------------------------------------------------------------------------
# Naver API fetchers
# ---------------------------------------------------------------------------


async def _fetch_complexes(client: httpx.AsyncClient, cortar_no: str) -> list[dict]:
    """
    Fetch apartment complex list for a cortarNo.

    Endpoint: GET /api/regions/complexes
    Returns list of complex dicts. Empty list on failure.
    """
    url = f"{BASE_URL}/regions/complexes"
    params = {
        "cortarNo": cortar_no,
        "realEstateType": "APT",
        "order": "",
    }
    try:
        data = await _get_json(client, url, params=params)
    except Exception as exc:
        logger.warning("Failed to fetch complexes for cortarNo=%s: %s", cortar_no, exc)
        return []

    if not data:
        return []

    # Response shape: {"complexList": [...]} or a bare list
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        # Try common Naver response keys
        for key in ("complexList", "aptList", "list", "data"):
            items = data.get(key)
            if isinstance(items, list):
                return items
    return []


async def _fetch_complex_articles(
    client: httpx.AsyncClient,
    complex_no: str,
    trade_type: str,
    page: int = 1,
) -> dict:
    """
    Fetch listings (articles) for a complex and trade type.

    Endpoint: GET /api/articles/complex/{complexNo}
    Returns raw response dict.
    """
    url = f"{BASE_URL}/articles/complex/{complex_no}"
    params = {
        "realEstateType": "APT",
        "tradeType": trade_type,
        "page": page,
        "sizePerPage": _LISTING_PAGE_SIZE,
        "isNwhMap": "false",
    }
    try:
        data = await _get_json(client, url, params=params)
    except Exception as exc:
        logger.warning(
            "Failed to fetch articles for complex=%s tradeType=%s page=%d: %s",
            complex_no,
            trade_type,
            page,
            exc,
        )
        return {}

    return data if isinstance(data, dict) else {}


# ---------------------------------------------------------------------------
# Parsers
# ---------------------------------------------------------------------------


def _parse_complex(raw: dict, region_code: str) -> dict:
    """
    Normalize a Naver complex dict into an AptComplex-compatible row dict.

    Naver field names observed in the wild (subject to API changes):
      complexNo, complexName, address, totalHouseholdCount, totalDongCount,
      useApproveYmd, representativeExclusiveArea, cortarAddress,
      latitude, longitude, minExclusiveArea, maxExclusiveArea
    """
    complex_no = _strip(raw.get("complexNo") or raw.get("complexId") or raw.get("id"))
    apt_name = _strip(raw.get("complexName") or raw.get("aptName") or raw.get("name")) or ""

    # Address: prefer road address
    address = _strip(
        raw.get("cortarAddress")
        or raw.get("roadAddress")
        or raw.get("address")
        or raw.get("jibunAddress")
    )

    # Build year: from '사용승인일' like '19990101' or 'useApproveYmd'
    build_year: int | None = None
    approve_ymd = _strip(raw.get("useApproveYmd") or raw.get("approveYmd"))
    if approve_ymd and len(approve_ymd) >= 4:
        build_year = _parse_int(approve_ymd[:4])

    # Dong/법정동 name
    dong_name = _strip(
        raw.get("dongName") or raw.get("legalDongName") or raw.get("dong")
    )

    return {
        "source": "naver",
        "region_code": region_code,
        "dong_name": dong_name,
        "apt_name": apt_name,
        "address": address,
        "total_units": _parse_int(
            raw.get("totalHouseholdCount") or raw.get("householdCount")
        ),
        "total_dong": _parse_int(
            raw.get("totalDongCount") or raw.get("dongCount")
        ),
        "build_year": build_year,
        "floor_area_max": _parse_decimal(
            raw.get("maxExclusiveArea") or raw.get("representativeExclusiveArea")
        ),
        "floor_area_min": _parse_decimal(raw.get("minExclusiveArea")),
        "latitude": _parse_decimal(raw.get("latitude") or raw.get("lat")),
        "longitude": _parse_decimal(raw.get("longitude") or raw.get("lng") or raw.get("lon")),
        "source_complex_id": complex_no,
        "raw_data": raw,
        "collected_at": datetime.now(),
    }


def _parse_article(
    raw: dict,
    region_code: str,
    apt_name: str,
    dong_name: str | None,
    listing_type: str,
) -> dict | None:
    """
    Normalize a Naver article (listing) dict into an AptListing-compatible row dict.

    Observed Naver article fields:
      articleNo, articleName, tradeTypeName, floorInfo,
      dealOrWarrantPrc, spc1 (공급면적), spc2 (전용면적),
      articleConfirmYmd, description, articleFeatureDesc, tagList,
      buildingName, etcInfo
    """
    article_no = _strip(raw.get("articleNo") or raw.get("articleId") or raw.get("id"))
    if not article_no:
        return None

    # Floor: Naver gives "5/20" (current/total) in floorInfo
    floor: int | None = None
    floor_info = _strip(raw.get("floorInfo") or raw.get("floor"))
    if floor_info:
        # Take the part before '/' for current floor
        floor = _parse_int(floor_info.split("/")[0])

    # Exclusive area (전용면적): prefer spc2 over spc1
    exclusive_area = _parse_decimal(
        raw.get("spc2") or raw.get("exclusiveArea") or raw.get("area2")
        or raw.get("spc1") or raw.get("supplyArea")
    )

    # Price extraction depends on trade type
    asking_price: int | None = None
    deposit: int | None = None

    price_str = _strip(
        raw.get("dealOrWarrantPrc")
        or raw.get("price")
        or raw.get("dealPrice")
    )
    deposit_str = _strip(raw.get("warrantPrc") or raw.get("deposit"))

    if listing_type == "sale":
        # 매매: asking_price is the sale price
        asking_price = _parse_price_manwon(price_str)
    elif listing_type == "jeonse":
        # 전세: deposit is the jeonse price
        deposit = _parse_price_manwon(price_str)
    elif listing_type == "monthly_rent":
        # 월세: deposit + asking_price (월세)
        deposit = _parse_price_manwon(deposit_str or price_str)
        asking_price = _parse_price_manwon(raw.get("rentPrc") or raw.get("monthlyRent"))

    # Description
    description = _strip(
        raw.get("articleFeatureDesc")
        or raw.get("description")
        or raw.get("etcInfo")
    )

    # Listed date
    listed_at = _parse_listed_at(
        raw.get("articleConfirmYmd")
        or raw.get("listedDate")
        or raw.get("registDate")
    )

    # Listing URL
    article_url = f"https://new.land.naver.com/articles/{article_no}"

    # Override apt_name if article has a more specific building name
    article_apt = _strip(raw.get("buildingName") or raw.get("complexName")) or apt_name

    return {
        "source": "naver",
        "listing_type": listing_type,
        "region_code": region_code,
        "dong_name": dong_name,
        "apt_name": article_apt,
        "exclusive_area": exclusive_area,
        "floor": floor,
        "asking_price": asking_price,
        "deposit": deposit,
        "description": description,
        "source_listing_id": article_no,
        "listing_url": article_url,
        "is_active": True,
        "listed_at": listed_at,
        "raw_data": raw,
        "collected_at": datetime.now(),
    }


# ---------------------------------------------------------------------------
# DB upsert helpers
# ---------------------------------------------------------------------------


async def _upsert_complexes(rows: list[dict]) -> tuple[int, int]:
    """Upsert AptComplex rows. Returns (inserted, updated)."""
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
                "updated_at",
            ]
            if col in AptComplex.__table__.c
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
    """Upsert AptListing rows. Returns (inserted, updated)."""
    if not rows:
        return 0, 0

    async with async_session() as session:
        stmt = insert(AptListing).values(rows)
        update_cols = {
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
                "updated_at",
            ]
            if col in AptListing.__table__.c
        }
        stmt = stmt.on_conflict_do_update(
            index_elements=["source", "source_listing_id"],
            index_where=(AptListing.source_listing_id.isnot(None)),
            set_=update_cols,
        )
        result = await session.execute(stmt)
        await session.commit()
        total = result.rowcount if result.rowcount != -1 else len(rows)
        return total, 0


# ---------------------------------------------------------------------------
# Collector
# ---------------------------------------------------------------------------


class NaverCollector(BaseCollector):
    """
    Collects active apartment listings from Naver Real Estate.

    Covers 매매 (sale) and 전세 (jeonse) listing types.
    Also populates AptComplex records as a side effect.

    region_code: 5-digit 시군구코드 (e.g. "11110" for 서울 종로구).
                 Internally converted to Naver's 10-digit cortarNo.
    """

    @property
    def source_name(self) -> str:
        return "naver"

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

        cortar_no = _to_cortar_no(region_code)
        logger.info(
            "[naver] Starting collection: region=%s cortarNo=%s",
            region_code,
            cortar_no,
        )

        try:
            async with httpx.AsyncClient(timeout=_REQUEST_TIMEOUT) as client:
                # Step 1: get complex list
                complexes = await _fetch_complexes(client, cortar_no)

                if not complexes:
                    logger.warning(
                        "[naver] No complexes found for region=%s cortarNo=%s",
                        region_code,
                        cortar_no,
                    )
                    result.status = "success"
                    result.duration_seconds = (datetime.now() - started_at).total_seconds()
                    return result

                logger.info(
                    "[naver] Found %d complexes for region=%s",
                    len(complexes),
                    region_code,
                )

                # Step 2: upsert complexes
                complex_rows = []
                for raw_complex in complexes:
                    try:
                        row = _parse_complex(raw_complex, region_code)
                        if row["source_complex_id"] and row["apt_name"]:
                            complex_rows.append(row)
                    except Exception as exc:
                        logger.warning("[naver] Failed to parse complex %r: %s", raw_complex, exc)

                if complex_rows:
                    await _upsert_complexes(complex_rows)
                    logger.debug(
                        "[naver] Upserted %d complexes for region=%s",
                        len(complex_rows),
                        region_code,
                    )

                # Step 3: for each complex, fetch listings
                all_listing_rows: list[dict] = []
                rate_limited = False

                for raw_complex in complexes:
                    complex_no = _strip(
                        raw_complex.get("complexNo")
                        or raw_complex.get("complexId")
                        or raw_complex.get("id")
                    )
                    if not complex_no:
                        continue

                    apt_name = _strip(
                        raw_complex.get("complexName")
                        or raw_complex.get("aptName")
                        or raw_complex.get("name")
                    ) or ""
                    dong_name = _strip(
                        raw_complex.get("dongName")
                        or raw_complex.get("legalDongName")
                        or raw_complex.get("dong")
                    )

                    for trade_type in _TRADE_TYPES:
                        listing_type = _TRADE_TYPE_MAP.get(trade_type, trade_type)
                        listing_rows, was_limited = await self._collect_complex_listings(
                            client=client,
                            complex_no=complex_no,
                            trade_type=trade_type,
                            listing_type=listing_type,
                            region_code=region_code,
                            apt_name=apt_name,
                            dong_name=dong_name,
                        )
                        all_listing_rows.extend(listing_rows)
                        if was_limited:
                            rate_limited = True

                        await _polite_sleep()

                # Step 4: bulk upsert listings
                result.records_collected = len(all_listing_rows)

                if all_listing_rows:
                    inserted, updated = await _upsert_listings(all_listing_rows)
                    result.records_inserted = inserted
                    result.records_updated = updated

                result.status = "partial" if rate_limited else "success"
                if rate_limited:
                    result.error_message = "Encountered rate-limiting; some listings may be missing"

        except Exception as exc:
            logger.exception(
                "[naver] Collection failed for region=%s: %s",
                region_code,
                exc,
            )
            result.status = "error"
            result.error_message = str(exc)

        result.duration_seconds = (datetime.now() - started_at).total_seconds()
        logger.info(
            "[naver] Done: region=%s status=%s collected=%d inserted=%d updated=%d duration=%.2fs",
            region_code,
            result.status,
            result.records_collected,
            result.records_inserted,
            result.records_updated,
            result.duration_seconds,
        )
        return result

    async def health_check(self) -> bool:
        """Verify Naver land API is reachable."""
        try:
            async with httpx.AsyncClient(timeout=_REQUEST_TIMEOUT) as client:
                # Probe with a well-known cortarNo (서울시 종로구)
                resp = await client.get(
                    f"{BASE_URL}/regions/complexes",
                    params={"cortarNo": "1111000000", "realEstateType": "APT", "order": ""},
                    headers=HEADERS,
                )
                return resp.status_code in (200, 204)
        except Exception as exc:
            logger.warning("[naver] health_check failed: %s", exc)
            return False

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _collect_complex_listings(
        self,
        client: httpx.AsyncClient,
        complex_no: str,
        trade_type: str,
        listing_type: str,
        region_code: str,
        apt_name: str,
        dong_name: str | None,
    ) -> tuple[list[dict], bool]:
        """
        Fetch all listing pages for a complex + trade type combination.

        Returns (listing_row_list, was_rate_limited).
        """
        rows: list[dict] = []
        was_rate_limited = False

        for page in range(1, _MAX_LISTING_PAGES + 1):
            try:
                data = await _fetch_complex_articles(client, complex_no, trade_type, page)
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code in (403, 429):
                    was_rate_limited = True
                    logger.warning(
                        "[naver] Rate limited fetching complex=%s tradeType=%s page=%d",
                        complex_no,
                        trade_type,
                        page,
                    )
                else:
                    logger.warning(
                        "[naver] HTTP error fetching complex=%s tradeType=%s page=%d: %s",
                        complex_no,
                        trade_type,
                        page,
                        exc,
                    )
                break
            except Exception as exc:
                logger.warning(
                    "[naver] Error fetching complex=%s tradeType=%s page=%d: %s",
                    complex_no,
                    trade_type,
                    page,
                    exc,
                )
                break

            if not data:
                break

            # Naver wraps articles under various keys
            articles = (
                data.get("articleList")
                or data.get("articles")
                or data.get("list")
                or data.get("data")
                or []
            )

            if not articles:
                break

            for raw_article in articles:
                try:
                    row = _parse_article(
                        raw=raw_article,
                        region_code=region_code,
                        apt_name=apt_name,
                        dong_name=dong_name,
                        listing_type=listing_type,
                    )
                    if row:
                        rows.append(row)
                except Exception as exc:
                    logger.debug("[naver] Skipping article %r: %s", raw_article, exc)

            # Check if there are more pages
            total_count = _parse_int(
                data.get("totalCount") or data.get("total") or data.get("count")
            )
            page_size = _parse_int(data.get("pageSize") or _LISTING_PAGE_SIZE)

            if total_count is not None and page_size:
                max_page = (total_count + page_size - 1) // page_size
                if page >= max_page:
                    break
            elif len(articles) < _LISTING_PAGE_SIZE:
                # Received fewer items than requested — this is the last page
                break

            await _polite_sleep()

        return rows, was_rate_limited
