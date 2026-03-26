from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.connection import get_db
from src.db.models import Region

router = APIRouter()


class RegionCreate(BaseModel):
    name: str
    region_code: str
    parent_area: str
    is_active: bool = True


class RegionUpdate(BaseModel):
    name: str | None = None
    region_code: str | None = None
    parent_area: str | None = None
    is_active: bool | None = None


@router.get("/")
async def list_regions(db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(Region).order_by(Region.parent_area, Region.name)
    )
    regions = result.scalars().all()
    return [
        {
            "id": r.id,
            "name": r.name,
            "region_code": r.region_code,
            "parent_area": r.parent_area,
            "is_active": r.is_active,
        }
        for r in regions
    ]


@router.post("/", status_code=201)
async def create_region(data: RegionCreate, db: AsyncSession = Depends(get_db)):
    # Check duplicate
    existing = await db.execute(
        select(Region).where(Region.region_code == data.region_code)
    )
    if existing.scalar_one_or_none():
        raise HTTPException(400, "Region code already exists")
    region = Region(**data.model_dump())
    db.add(region)
    await db.flush()
    await db.refresh(region)
    return {
        "id": region.id,
        "name": region.name,
        "region_code": region.region_code,
        "parent_area": region.parent_area,
        "is_active": region.is_active,
    }


@router.put("/{region_id}")
async def update_region(
    region_id: int, data: RegionUpdate, db: AsyncSession = Depends(get_db)
):
    region = await db.get(Region, region_id)
    if not region:
        raise HTTPException(404, "Region not found")
    for key, value in data.model_dump(exclude_unset=True).items():
        setattr(region, key, value)
    await db.flush()
    await db.refresh(region)
    return {
        "id": region.id,
        "name": region.name,
        "region_code": region.region_code,
        "parent_area": region.parent_area,
        "is_active": region.is_active,
    }


@router.delete("/{region_id}")
async def delete_region(region_id: int, db: AsyncSession = Depends(get_db)):
    region = await db.get(Region, region_id)
    if not region:
        raise HTTPException(404, "Region not found")
    await db.delete(region)
    return {"deleted": True, "id": region_id}
