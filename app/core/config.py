from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict

# Resolve `.env` from the repository root (parent of `app/`), not the process cwd.
_REPO_ROOT = Path(__file__).resolve().parents[2]
_ENV_FILE = _REPO_ROOT / ".env"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=_ENV_FILE,
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Adapter selection drives whether real OpenStack credentials are required.
    # `mock` lets a reviewer run the full DR pipeline with zero infra.
    adapter_mode: Literal["mock", "openstack"] = "mock"

    # SQLAlchemy async URL. Default is a file-backed SQLite so docker compose
    # can persist DR jobs and audit events across restarts.
    database_url: str = "sqlite+aiosqlite:///./dr.db"

    # DR pipeline defaults.
    default_rto_minutes: int = 15
    mock_latency_ms: int = 250

    # OpenStack settings (only required when adapter_mode == "openstack").
    openstack_auth_url: str | None = None
    openstack_region_name: str = "RegionOne"
    openstack_compute_api_version: str = "2"
    openstack_identity_interface: str = "internal"
    openstack_project_id: str | None = None
    openstack_user_domain_id: str | None = None
    openstack_project_domain_id: str | None = None


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
