from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.connection import get_db
from src.db.models import Schedule

router = APIRouter()


class ScheduleCreate(BaseModel):
    name: str
    source_type: str | None = None
    interval_minutes: int
    is_active: bool = True


class ScheduleUpdate(BaseModel):
    name: str | None = None
    source_type: str | None = None
    interval_minutes: int | None = None
    is_active: bool | None = None


@router.get("/")
async def list_schedules(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Schedule).order_by(Schedule.id))
    schedules = result.scalars().all()
    return [
        {
            "id": s.id,
            "name": s.name,
            "source_type": s.source_type,
            "interval_minutes": s.interval_minutes,
            "is_active": s.is_active,
        }
        for s in schedules
    ]


@router.post("/", status_code=201)
async def create_schedule(data: ScheduleCreate, db: AsyncSession = Depends(get_db)):
    # Check duplicate name
    existing = await db.execute(select(Schedule).where(Schedule.name == data.name))
    if existing.scalar_one_or_none():
        raise HTTPException(400, "Schedule name already exists")
    schedule = Schedule(**data.model_dump())
    db.add(schedule)
    await db.flush()
    await db.refresh(schedule)
    return {
        "id": schedule.id,
        "name": schedule.name,
        "source_type": schedule.source_type,
        "interval_minutes": schedule.interval_minutes,
        "is_active": schedule.is_active,
    }


@router.put("/{schedule_id}")
async def update_schedule(
    schedule_id: int, data: ScheduleUpdate, db: AsyncSession = Depends(get_db)
):
    schedule = await db.get(Schedule, schedule_id)
    if not schedule:
        raise HTTPException(404, "Schedule not found")

    updates = data.model_dump(exclude_unset=True)
    interval_changed = "interval_minutes" in updates

    for key, value in updates.items():
        setattr(schedule, key, value)

    await db.flush()
    await db.refresh(schedule)

    # Update scheduler interval if interval changed
    if interval_changed:
        try:
            from src.scheduler.jobs import update_interval

            update_interval(schedule.interval_minutes)
        except Exception:
            pass  # Scheduler may not be running; ignore

    return {
        "id": schedule.id,
        "name": schedule.name,
        "source_type": schedule.source_type,
        "interval_minutes": schedule.interval_minutes,
        "is_active": schedule.is_active,
    }


@router.delete("/{schedule_id}")
async def delete_schedule(schedule_id: int, db: AsyncSession = Depends(get_db)):
    schedule = await db.get(Schedule, schedule_id)
    if not schedule:
        raise HTTPException(404, "Schedule not found")
    await db.delete(schedule)
    return {"deleted": True, "id": schedule_id}
