from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    database_url: str = "postgresql+asyncpg://collector:collector_pass@postgres:5432/realestate"
    data_go_kr_api_key: str = ""
    vworld_api_key: str = ""
    reb_api_key: str = ""
    seoul_api_key: str = ""
    gyeonggi_api_key: str = ""
    collection_interval_minutes: int = 30
    admin_port: int = 8080
    log_level: str = "INFO"
    private_crawler_delay: float = 2.0

    model_config = {"env_file": ".env"}


settings = Settings()
