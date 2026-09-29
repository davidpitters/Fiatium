from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="FIATIUM_", env_file=".env", extra="ignore")
    database_url: str = (
        "mssql+pyodbc://localhost/FiatiumDev?driver=ODBC+Driver+17+for+SQL+Server"
        "&trusted_connection=yes&TrustServerCertificate=yes"
    )
    tokens: dict[str, dict[str, str]] = {}
    broker: str = "localhost:19092"
    topic: str = "fiatium.payments.v1"


@lru_cache
def settings() -> Settings:
    return Settings()
