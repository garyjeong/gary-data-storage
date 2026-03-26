"""Shared utilities for public API collectors."""
import asyncio
import logging
from urllib.parse import urlencode

import httpx

logger = logging.getLogger(__name__)


async def fetch_with_retry(
    client: httpx.AsyncClient,
    url: str,
    params: dict,
    max_retries: int = 3,
    backoff: list[int] | None = None,
) -> httpx.Response:
    """Fetch URL with retry and exponential backoff.

    Builds the full URL manually to avoid httpx double-encoding the serviceKey.
    """
    if backoff is None:
        backoff = [2, 4, 8]

    # Build URL manually — serviceKey contains special chars (/, =) that
    # must NOT be percent-encoded by httpx.
    service_key = params.pop("serviceKey", None)
    if service_key:
        query = f"serviceKey={service_key}&{urlencode(params)}"
    else:
        query = urlencode(params)
    full_url = f"{url}?{query}"

    last_exc: Exception | None = None
    for attempt in range(max_retries):
        try:
            response = await client.get(full_url)
            response.raise_for_status()
            return response
        except (httpx.HTTPError, Exception) as exc:
            last_exc = exc
            if attempt < max_retries - 1:
                wait = backoff[min(attempt, len(backoff) - 1)]
                logger.warning(
                    "Request failed (attempt %d/%d), retrying in %ds: %s",
                    attempt + 1, max_retries, wait, exc,
                )
                await asyncio.sleep(wait)
            else:
                logger.error("All %d attempts failed for %s: %s", max_retries, url, exc)
    raise last_exc  # type: ignore[misc]
