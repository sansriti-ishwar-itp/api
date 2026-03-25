from __future__ import annotations

from typing import Any

from fastapi import HTTPException

from app.core.errors import openstack_exception_to_http
from app.models.responses import CreateServerResponse, VmActionResponse
from app.services.openstack_client import OpenStackVMClient


class VMService:
    """
    Application service that exposes lifecycle operations in terms of the API contract.
    """

    def __init__(self, client: OpenStackVMClient):
        self._client = client

    def create_vm(self, attrs: dict[str, Any]) -> CreateServerResponse:
        try:
            server = self._client.create_server(attrs)
            return CreateServerResponse(
                server_id=str(getattr(server, "id")),
                status=getattr(server, "status", None),
            )
        except Exception as exc:  # noqa: BLE001
            http_exc: HTTPException = openstack_exception_to_http(exc)
            raise http_exc

    def start_vm(self, server_id: str) -> VmActionResponse:
        try:
            self._client.start_server(server_id)
            return VmActionResponse(server_id=server_id, action="start")
        except Exception as exc:  # noqa: BLE001
            http_exc: HTTPException = openstack_exception_to_http(exc)
            raise http_exc

    def stop_vm(self, server_id: str) -> VmActionResponse:
        try:
            self._client.stop_server(server_id)
            return VmActionResponse(server_id=server_id, action="stop")
        except Exception as exc:  # noqa: BLE001
            http_exc: HTTPException = openstack_exception_to_http(exc)
            raise http_exc

    def delete_vm(self, server_id: str, *, force: bool = False) -> None:
        try:
            self._client.delete_server(server_id, force=force)
        except Exception as exc:  # noqa: BLE001
            http_exc: HTTPException = openstack_exception_to_http(exc)
            raise http_exc

