from __future__ import annotations

from typing import Any

from keystoneauth1 import session as ks_session
from openstack.connection import Connection

# Actual openstack sdk calls
class OpenStackVMClient:
    """
    Thin wrapper around openstacksdk compute + server actions.

    Keeping OpenStack-specific calls out of the FastAPI layer makes the API
    easier to unit test (via dependency overrides).
    """

    def __init__(self, conn: Connection, ks_session: ks_session.Session):
        self._conn = conn
        self._ks_session = ks_session

    def create_server(self, attrs: dict[str, Any]) -> Any:
        return self._conn.compute.create_server(**attrs)

    def start_server(self, server_id: str) -> None:
        server = self._conn.compute.get_server(server_id)
        # Server actions use keystoneauth1 session.
        server.start(self._ks_session)

    def stop_server(self, server_id: str) -> None:
        server = self._conn.compute.get_server(server_id)
        server.stop(self._ks_session)

    def delete_server(self, server_id: str, *, force: bool = False) -> None:
        self._conn.compute.delete_server(server_id, ignore_missing=True, force=force)

