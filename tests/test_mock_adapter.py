from __future__ import annotations

import pytest

from app.adapters.base import (
    AdapterError,
    StandbyUnavailableError,
    VMNotFoundError,
)
from app.adapters.mock import MockAdapter


@pytest.fixture()
def adapter() -> MockAdapter:
    return MockAdapter(latency_ms=0)


async def test_create_and_get_server(adapter: MockAdapter) -> None:
    info = await adapter.create_server({"name": "vm-1"})
    assert info.name == "vm-1"
    assert info.status == "ACTIVE"
    fetched = await adapter.get_server(info.id)
    assert fetched.id == info.id


async def test_get_unknown_raises(adapter: MockAdapter) -> None:
    with pytest.raises(VMNotFoundError):
        await adapter.get_server("missing")


async def test_snapshot_then_boot(adapter: MockAdapter) -> None:
    src = await adapter.create_server({"name": "vm-src"})
    snap_id = await adapter.create_snapshot(src.id)
    standby = await adapter.find_standby_node()
    booted = await adapter.boot_from_snapshot(snap_id, standby)
    assert booted.id != src.id
    assert booted.host == standby
    assert booted.metadata["recovered_from"] == snap_id


async def test_failure_injection(adapter: MockAdapter) -> None:
    src = await adapter.create_server({"name": "vm-fail"})
    adapter.fail_next_snapshot = True
    with pytest.raises(AdapterError):
        await adapter.create_snapshot(src.id)
    # The flag is consumed after one use.
    snap = await adapter.create_snapshot(src.id)
    assert snap.startswith("snap-")


async def test_no_standby(adapter: MockAdapter) -> None:
    adapter.no_standby = True
    with pytest.raises(StandbyUnavailableError):
        await adapter.find_standby_node()


async def test_ping_health_flips(adapter: MockAdapter) -> None:
    src = await adapter.create_server({"name": "vm-ping"})
    ok = await adapter.ping_vm(src.id)
    assert ok.healthy is True

    adapter.mark_unhealthy(src.id)
    bad = await adapter.ping_vm(src.id)
    assert bad.healthy is False

    adapter.mark_healthy(src.id)
    again = await adapter.ping_vm(src.id)
    assert again.healthy is True


async def test_list_servers(adapter: MockAdapter) -> None:
    await adapter.create_server({"name": "a"})
    await adapter.create_server({"name": "b"})
    servers = await adapter.list_servers()
    names = sorted(s.name for s in servers)
    assert names == ["a", "b"]
