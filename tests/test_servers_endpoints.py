from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient

from app.api.deps.openstack import get_vm_service
from app.main import app
from app.models.responses import CreateServerResponse, VmActionResponse

# Router-level Security(bearer_scheme) still runs when get_vm_service is overridden.
_AUTH_HEADERS = {"Authorization": "Bearer test-token"}


class FakeVMService:
    def __init__(self) -> None:
        self.created_attrs: dict[str, Any] | None = None
        self.started_server_id: str | None = None
        self.stopped_server_id: str | None = None
        self.deleted_server_id: str | None = None
        self.deleted_force: bool | None = None

    def create_vm(self, attrs: dict[str, Any]) -> CreateServerResponse:
        self.created_attrs = attrs
        return CreateServerResponse(server_id="vm-123", status="BUILD")

    def start_vm(self, server_id: str) -> VmActionResponse:
        self.started_server_id = server_id
        return VmActionResponse(server_id=server_id, action="start")

    def stop_vm(self, server_id: str) -> VmActionResponse:
        self.stopped_server_id = server_id
        return VmActionResponse(server_id=server_id, action="stop")

    def delete_vm(self, server_id: str, *, force: bool = False) -> None:
        self.deleted_server_id = server_id
        self.deleted_force = force


def test_create_start_stop_delete_endpoints() -> None:
    fake = FakeVMService()

    def override_get_vm_service() -> FakeVMService:
        return fake

    app.dependency_overrides[get_vm_service] = override_get_vm_service
    client = TestClient(app)

    create_payload = {
        "name": "vm-1",
        "image_id": "img-1",
        "flavor_id": "flavor-1",
        "networks": [{"uuid": "net-1"}],
    }

    resp = client.post("/v1/servers", json=create_payload, headers=_AUTH_HEADERS)
    assert resp.status_code == 201
    assert resp.json() == {"server_id": "vm-123", "status": "BUILD"}
    assert fake.created_attrs == create_payload

    resp = client.post("/v1/servers/vm-123/start", headers=_AUTH_HEADERS)
    assert resp.status_code == 202
    assert resp.json() == {"server_id": "vm-123", "action": "start"}
    assert fake.started_server_id == "vm-123"

    resp = client.post("/v1/servers/vm-123/stop", headers=_AUTH_HEADERS)
    assert resp.status_code == 202
    assert resp.json() == {"server_id": "vm-123", "action": "stop"}
    assert fake.stopped_server_id == "vm-123"

    resp = client.delete("/v1/servers/vm-123?force=true", headers=_AUTH_HEADERS)
    assert resp.status_code == 204
    assert resp.content == b""
    assert fake.deleted_server_id == "vm-123"
    assert fake.deleted_force is True

    app.dependency_overrides.clear()

