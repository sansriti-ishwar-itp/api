"""VMAdapter protocoland related types.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable


class AdapterError(Exception):
    """Base error for adapter failures. Subclasses get mapped to HTTP codes."""


class VMNotFoundError(AdapterError):
    """The requested VM/server does not exist on the backend."""


class StandbyUnavailableError(AdapterError):
    """No standby node has capacity for the recovery."""


@dataclass
class ServerInfo:
    id: str
    name: str
    status: str
    host: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class HealthResult:
    healthy: bool
    latency_ms: int | None = None
    detail: str | None = None


@runtime_checkable
class VMAdapter(Protocol):
    """Contract for any backend that can manage a VM lifecycle."""

    name: str

    async def create_server(self, attrs: dict[str, Any]) -> ServerInfo: ...

    async def get_server(self, server_id: str) -> ServerInfo: ...

    async def list_servers(self, limit: int = 50) -> list[ServerInfo]: ...

    async def start_server(self, server_id: str) -> None: ...

    async def stop_server(self, server_id: str) -> None: ...

    async def delete_server(self, server_id: str, *, force: bool = False) -> None: ...

    async def ping_vm(self, server_id: str) -> HealthResult: ...

    async def create_snapshot(self, server_id: str, *, name: str | None = None) -> str: ...

    async def find_standby_node(self) -> str: ...

    async def boot_from_snapshot(
        self, snapshot_id: str, standby_node: str, *, name: str | None = None
    ) -> ServerInfo: ...
