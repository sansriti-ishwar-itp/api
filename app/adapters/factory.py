"""Adapter factory (mock vs openstack)."""

from __future__ import annotations

from typing import Optional

from app.adapters.base import VMAdapter
from app.adapters.mock import MockAdapter
from app.adapters.openstack import OpenStackAdapter
from app.core.config import Settings, get_settings
from app.services.openstack_client import OpenStackVMClient

_mock_singleton: Optional[MockAdapter] = None


def _get_or_create_mock(latency_ms: int) -> MockAdapter:
    global _mock_singleton
    if _mock_singleton is None:
        _mock_singleton = MockAdapter(latency_ms=latency_ms)
    return _mock_singleton


def reset_mock_singleton() -> None:
    """Test hook: drop the in-memory state between test cases."""
    global _mock_singleton
    _mock_singleton = None


def build_adapter(
    settings: Settings | None = None,
    vm_client: OpenStackVMClient | None = None,
) -> VMAdapter:
    """Build an adapter for the current `ADAPTER_MODE`."""
    s = settings or get_settings()
    if s.adapter_mode == "mock":
        return _get_or_create_mock(s.mock_latency_ms)
    if vm_client is None:
        raise RuntimeError(
            "ADAPTER_MODE=openstack requires a per-request OpenStackVMClient"
        )
    return OpenStackAdapter(vm_client=vm_client)
