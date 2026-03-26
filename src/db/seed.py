import yaml
from pathlib import Path
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models import Region, Schedule


async def seed_regions(db: AsyncSession) -> int:
    """Seed regions from config/regions.yaml. Returns count of new regions."""
    config_path = Path(__file__).parent.parent.parent / "config" / "regions.yaml"
    if not config_path.exists():
        return 0

    with open(config_path) as f:
        data = yaml.safe_load(f)

    count = 0
    for parent_area, regions in data.get("regions", {}).items():
        for region in regions:
            # Check if already exists
            result = await db.execute(
                select(Region).where(Region.region_code == region["code"])
            )
            existing = result.scalar_one_or_none()
            if not existing:
                db.add(Region(
                    name=region["name"],
                    region_code=region["code"],
                    parent_area=parent_area,
                    is_active=True,
                ))
                count += 1

    await db.flush()
    return count


async def seed_default_schedule(db: AsyncSession) -> bool:
    """Create default schedule if none exists. Returns True if created."""
    result = await db.execute(select(Schedule).limit(1))
    if result.scalar_one_or_none():
        return False

    db.add(Schedule(
        name="기본 수집",
        source_type=None,
        interval_minutes=30,
        is_active=True,
    ))
    await db.flush()
    return True


async def run_seed(db: AsyncSession) -> dict:
    """Run all seed operations."""
    regions_count = await seed_regions(db)
    schedule_created = await seed_default_schedule(db)
    return {
        "regions_added": regions_count,
        "default_schedule_created": schedule_created,
    }
