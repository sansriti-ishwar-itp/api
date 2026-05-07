"""In-memory adapter for demos/tests (no OpenStack needed).

Supports realistic latency and simple failure injection for deterministic tests.
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any

from app.adapters.base import (
    AdapterError,
    HealthResult,
    ServerInfo,
    StandbyUnavailableError,
    VMAdapter,
    VMNotFoundError,
)


def _short_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


class MockAdapter(VMAdapter):
    name = "mock"

    def __init__(self, latency_ms: int = 250) -> None:
        self._latency_s = max(0.0, latency_ms / 1000.0)
        self._servers: dict[str, ServerInfo] = {}
        self._snapshots: dict[str, str] = {}  # snapshot_id -> source server_id
        self._unhealthy: set[str] = set()
        self._standby_nodes: list[str] = ["standby-az1", "standby-az2", "standby-az3"]
        self._standby_round_robin = 0

        # Failure-injection switches for deterministic tests.
        self.fail_next_snapshot = False
        self.fail_next_boot = False
        self.fail_next_stop = False
        self.no_standby = False

    async def _sleep(self, multiplier: float = 1.0) -> None:
        await asyncio.sleep(self._latency_s * multiplier)

    async def create_server(self, attrs: dict[str, Any]) -> ServerInfo:
        await self._sleep()
        sid = _short_id("vm")
        info = ServerInfo(
            id=sid,
            name=str(attrs.get("name") or sid),
            status="ACTIVE",
            host=attrs.get("host") or "compute-az1",
            metadata=dict(attrs.get("metadata") or {}),
        )
        self._servers[sid] = info
        return info

    async def get_server(self, server_id: str) -> ServerInfo:
        await self._sleep(0.2)
        info = self._servers.get(server_id)
        if info is None:
            raise VMNotFoundError(f"server '{server_id}' not found")
        return info

    async def list_servers(self, limit: int = 50) -> list[ServerInfo]:
        await self._sleep(0.2)
        return list(self._servers.values())[:limit]

    async def start_server(self, server_id: str) -> None:
        info = await self.get_server(server_id)
        info.status = "ACTIVE"

    async def stop_server(self, server_id: str) -> None:
        if self.fail_next_stop:
            self.fail_next_stop = False
            raise AdapterError("injected: stop failed")
        info = await self.get_server(server_id)
        info.status = "SHUTOFF"

    async def delete_server(self, server_id: str, *, force: bool = False) -> None:
        await self._sleep(0.2)
        self._servers.pop(server_id, None)

    async def ping_vm(self, server_id: str) -> HealthResult:
        await self._sleep(0.1)
        if server_id not in self._servers:
            return HealthResult(healthy=False, detail="not found")
        if server_id in self._unhealthy:
            return HealthResult(healthy=False, latency_ms=int(self._latency_s * 1000), detail="probe failed")
        return HealthResult(healthy=True, latency_ms=int(self._latency_s * 1000))

    async def create_snapshot(self, server_id: str, *, name: str | None = None) -> str:
        if self.fail_next_snapshot:
            self.fail_next_snapshot = False
            raise AdapterError("injected: snapshot failed")
        await self._sleep(2.0)
        if server_id not in self._servers:
            raise VMNotFoundError(f"server '{server_id}' not found")
        snap_id = _short_id("snap")
        self._snapshots[snap_id] = server_id
        return snap_id

    async def find_standby_node(self) -> str:
        await self._sleep(0.1)
        if self.no_standby:
            raise StandbyUnavailableError("no standby capacity")
        node = self._standby_nodes[self._standby_round_robin % len(self._standby_nodes)]
        self._standby_round_robin += 1
        return node

    async def boot_from_snapshot(
        self, snapshot_id: str, standby_node: str, *, name: str | None = None
    ) -> ServerInfo:
        if self.fail_next_boot:
            self.fail_next_boot = False
            raise AdapterError("injected: boot failed")
        await self._sleep(3.0)
        if snapshot_id not in self._snapshots:
            raise AdapterError(f"snapshot '{snapshot_id}' not found")
        new_id = _short_id("vm")
        info = ServerInfo(
            id=new_id,
            name=name or _short_id("recovered"),
            status="ACTIVE",
            host=standby_node,
            metadata={"recovered_from": snapshot_id},
        )
        self._servers[new_id] = info
        return info

    # Test helpers
    def mark_unhealthy(self, server_id: str) -> None:
        self._unhealthy.add(server_id)

    def mark_healthy(self, server_id: str) -> None:
        self._unhealthy.discard(server_id)

    def seed_server(self, server_id: str, name: str = "seeded") -> ServerInfo:
        info = ServerInfo(id=server_id, name=name, status="ACTIVE", host="compute-az1")
        self._servers[server_id] = info
        return info
