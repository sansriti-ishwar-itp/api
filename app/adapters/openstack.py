"""OpenStack-backed adapter used in `ADAPTER_MODE=openstack`.

Wraps blocking openstacksdk calls with `asyncio.to_thread`.
"""

from __future__ import annotations

import asyncio
from typing import Any

from app.adapters.base import (
    AdapterError,
    HealthResult,
    ServerInfo,
    StandbyUnavailableError,
    VMAdapter,
    VMNotFoundError,
)
from app.services.openstack_client import OpenStackVMClient


class OpenStackAdapter(VMAdapter):
    name = "openstack"

    def __init__(self, vm_client: OpenStackVMClient) -> None:
        self._client = vm_client
        self._conn = vm_client._conn  # noqa: SLF001 - internal coupling is the point of composition
        self._ks = vm_client._ks_session  # noqa: SLF001

    @staticmethod
    def _to_info(server: Any) -> ServerInfo:
        return ServerInfo(
            id=str(getattr(server, "id", "")),
            name=str(getattr(server, "name", "")) or "",
            status=str(getattr(server, "status", "") or "UNKNOWN"),
            host=getattr(server, "compute_host", None) or getattr(server, "hypervisor_hostname", None),
            metadata=dict(getattr(server, "metadata", {}) or {}),
        )

    async def create_server(self, attrs: dict[str, Any]) -> ServerInfo:
        server = await asyncio.to_thread(self._client.create_server, attrs)
        return self._to_info(server)

    async def get_server(self, server_id: str) -> ServerInfo:
        try:
            server = await asyncio.to_thread(self._conn.compute.get_server, server_id)
        except Exception as exc:  # noqa: BLE001
            raise VMNotFoundError(str(exc)) from exc
        return self._to_info(server)

    async def list_servers(self, limit: int = 50) -> list[ServerInfo]:
        def _list() -> list[Any]:
            return list(self._conn.compute.servers(limit=limit))

        servers = await asyncio.to_thread(_list)
        return [self._to_info(s) for s in servers]

    async def start_server(self, server_id: str) -> None:
        await asyncio.to_thread(self._client.start_server, server_id)

    async def stop_server(self, server_id: str) -> None:
        await asyncio.to_thread(self._client.stop_server, server_id)

    async def delete_server(self, server_id: str, *, force: bool = False) -> None:
        await asyncio.to_thread(self._client.delete_server, server_id, force=force)

    async def ping_vm(self, server_id: str) -> HealthResult:
        # Best-effort: server existence + active status. A production-grade probe
        # would also exercise an SSH/HTTP path against a known port, which we
        # call out explicitly in the README's limitations.
        try:
            info = await self.get_server(server_id)
        except VMNotFoundError:
            return HealthResult(healthy=False, detail="not found")
        healthy = info.status.upper() == "ACTIVE"
        return HealthResult(healthy=healthy, detail=info.status)

    async def create_snapshot(self, server_id: str, *, name: str | None = None) -> str:
        snap_name = name or f"dr-snap-{server_id[:8]}"

        def _snapshot() -> str:
            server = self._conn.compute.get_server(server_id)
            image = self._conn.compute.create_server_image(server, name=snap_name)
            return str(getattr(image, "id", ""))

        try:
            return await asyncio.to_thread(_snapshot)
        except Exception as exc:  # noqa: BLE001
            raise AdapterError(f"snapshot failed: {exc}") from exc

    async def find_standby_node(self) -> str:
        # Walk hypervisors and return the one with the most free vCPUs.
        # Tries openstacksdk first; falls back to a deterministic placeholder
        # if the deployment doesn't expose hypervisors to the caller's role.
        def _pick() -> str:
            try:
                hypervisors = list(self._conn.compute.hypervisors(details=True))
            except Exception:
                return "standby-default"
            if not hypervisors:
                return "standby-default"
            best = max(
                hypervisors,
                key=lambda h: (
                    int(getattr(h, "vcpus", 0) or 0)
                    - int(getattr(h, "vcpus_used", 0) or 0)
                ),
            )
            return str(getattr(best, "name", "") or getattr(best, "hypervisor_hostname", "") or "standby-default")

        node = await asyncio.to_thread(_pick)
        if not node:
            raise StandbyUnavailableError("no standby capacity")
        return node

    async def boot_from_snapshot(
        self, snapshot_id: str, standby_node: str, *, name: str | None = None
    ) -> ServerInfo:
        boot_name = name or f"recovered-{snapshot_id[:8]}"

        def _boot() -> Any:
            return self._conn.compute.create_server(
                name=boot_name,
                image_id=snapshot_id,
                # Caller's project must permit availability_zone targeting on
                # the recovered VM; if not, the deployment will hint via 403.
                availability_zone=f"nova:{standby_node}",
            )

        try:
            server = await asyncio.to_thread(_boot)
        except Exception as exc:  # noqa: BLE001
            raise AdapterError(f"boot from snapshot failed: {exc}") from exc
        return self._to_info(server)
