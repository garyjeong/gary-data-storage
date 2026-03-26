from fastapi import APIRouter

admin_router = APIRouter()

# Import and include sub-routers
from src.admin.routes.dashboard import router as dashboard_router
from src.admin.routes.regions import router as regions_router
from src.admin.routes.schedules import router as schedules_router
from src.admin.routes.triggers import router as triggers_router

admin_router.include_router(dashboard_router, tags=["dashboard"])
admin_router.include_router(regions_router, prefix="/api/regions", tags=["regions"])
admin_router.include_router(schedules_router, prefix="/api/schedules", tags=["schedules"])
admin_router.include_router(triggers_router, prefix="/api", tags=["triggers"])
