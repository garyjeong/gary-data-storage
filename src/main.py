import logging
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI

from src.config import settings

logging.basicConfig(
    level=getattr(logging, settings.log_level.upper()),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


async def run_migrations():
    """Create all tables on startup. Uses SQLAlchemy metadata.create_all for simplicity."""
    from src.db.connection import engine
    from src.db.models import Base

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    logger.info("Database tables created/verified")


async def seed_data():
    """Seed initial data."""
    from src.db.connection import async_session
    from src.db.seed import run_seed

    async with async_session() as db:
        result = await run_seed(db)
        await db.commit()
        logger.info(f"Seed complete: {result}")


def create_collector_manager():
    """Instantiate CollectorManager and register all collectors.

    Import errors for individual collectors are caught and logged so that
    the rest of the collectors can still be registered.
    """
    from src.collector.manager import CollectorManager

    collector_imports = [
        ("src.collector.sources.public_api.molit", ["MolitSaleCollector", "MolitJeonseCollector"]),
        ("src.collector.sources.public_api.building", ["BuildingCollector"]),
        ("src.collector.sources.public_api.price", ["OfficialPriceCollector"]),
        ("src.collector.sources.public_api.reb", ["RebCollector"]),
        ("src.collector.sources.public_api.seoul", ["SeoulCollector"]),
        ("src.collector.sources.public_api.gyeonggi", ["GyeonggiCollector"]),
        ("src.collector.sources.naver.crawler", ["NaverCollector"]),
        ("src.collector.sources.zigbang.crawler", ["ZigbangCollector"]),
        ("src.collector.sources.hogangnono.crawler", ["HogangnonoCollector"]),
    ]

    manager = CollectorManager()

    for module_path, class_names in collector_imports:
        try:
            import importlib

            module = importlib.import_module(module_path)
            for class_name in class_names:
                cls = getattr(module, class_name)
                manager.register(cls())
        except Exception as e:
            logger.warning(f"Failed to load collector(s) from {module_path}: {e}")

    return manager


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    await run_migrations()
    await seed_data()

    manager = create_collector_manager()
    app.state.manager = manager

    from src.scheduler.jobs import setup_scheduler

    sched = setup_scheduler(manager, settings.collection_interval_minutes)
    sched.start()
    app.state.scheduler = sched

    logger.info(f"App started. Scheduler interval: {settings.collection_interval_minutes}min")

    yield

    # Shutdown
    sched.shutdown()
    logger.info("App shutdown complete")


def create_app() -> FastAPI:
    app = FastAPI(
        title="Real Estate Collector Admin",
        version="0.1.0",
        lifespan=lifespan,
    )

    from src.admin.app import admin_router

    app.include_router(admin_router)

    return app


app = create_app()

if __name__ == "__main__":
    uvicorn.run(
        "src.main:app",
        host="0.0.0.0",
        port=settings.admin_port,
        reload=False,
    )
