from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class CollectionResult:
    """Result of a single collection operation."""
    source: str
    region_code: str | None
    records_collected: int = 0
    records_inserted: int = 0
    records_updated: int = 0
    status: str = "success"  # "success" | "error" | "partial"
    error_message: str | None = None
    duration_seconds: float = 0.0
    started_at: datetime = field(default_factory=datetime.now)


class BaseCollector(ABC):
    """All collectors must implement this interface."""

    @property
    @abstractmethod
    def source_name(self) -> str:
        """Unique source identifier (e.g., 'molit_sale')"""

    @abstractmethod
    async def collect(self, region_code: str, **params) -> CollectionResult:
        """Collect data for a single region. Must handle own errors internally."""

    @abstractmethod
    async def health_check(self) -> bool:
        """Check if the source API/site is reachable."""
