from pathlib import Path

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

    openstack_auth_url: str
    openstack_region_name: str = "RegionOne"
    openstack_compute_api_version: str = "2"
    openstack_identity_interface: str = "internal"

    # Optional token scoping fields.
    openstack_project_id: str | None = None
    openstack_user_domain_id: str | None = None
    openstack_project_domain_id: str | None = None


def get_settings() -> Settings:
    return Settings()

