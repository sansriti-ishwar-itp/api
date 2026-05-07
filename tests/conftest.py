"""Shared test fixtures.

- Forces `ADAPTER_MODE=mock` and an in-memory SQLite DB so every test starts
  from a clean slate (no fixture files, no leaked state across tests).
- Resets the mock adapter singleton between tests for determinism.
- Provides a `client` fixture wrapping FastAPI `TestClient` so the lifespan
  (which initializes the DB engine + creates tables) runs correctly.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Set env BEFORE importing app modules; pydantic-settings picks them up.
os.environ.setdefault("ADAPTER_MODE", "mock")
os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///:memory:"
os.environ.setdefault("MOCK_LATENCY_MS", "0")  # Snappy tests by default.
os.environ.setdefault("OPENSTACK_AUTH_URL", "https://keystone.example.com:5000/v3")


@pytest.fixture(autouse=True)
def _reset_settings_cache() -> Iterator[None]:
    from app.core.config import get_settings

    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture(autouse=True)
def _reset_mock_adapter() -> Iterator[None]:
    from app.adapters.factory import reset_mock_singleton

    reset_mock_singleton()
    yield
    reset_mock_singleton()


@pytest.fixture()
def client():
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as c:
        yield c


@pytest.fixture()
def auth_headers() -> dict[str, str]:
    """Headers for legacy /v1/servers tests (mock mode ignores them, but the
    legacy router still requires a Bearer header for openapi docs)."""
    return {"Authorization": "Bearer test-token"}
