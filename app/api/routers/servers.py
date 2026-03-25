from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Response, Security

from app.api.deps.openstack import bearer_scheme, get_vm_service
from app.models.requests import CreateServerRequest
from app.models.responses import CreateServerResponse, VmActionResponse
from app.services.vm_service import VMService

# Security on the router so OpenAPI/Swagger shows the lock and sends Authorization on Try it out.
router = APIRouter(
    prefix="/v1",
    tags=["servers"],
    dependencies=[Security(bearer_scheme)],
)


@router.post(
    "/servers",
    status_code=201,
    response_model=CreateServerResponse,
    summary="Create a VM (OpenStack server)",
)
def create_server(
    payload: CreateServerRequest,
    vm_service: VMService = Depends(get_vm_service),
) -> CreateServerResponse:
    return vm_service.create_vm(payload.root)


@router.post(
    "/servers/{server_id}/start",
    status_code=202,
    response_model=VmActionResponse,
    summary="Start a VM",
)
def start_server(
    server_id: str,
    vm_service: VMService = Depends(get_vm_service),
) -> VmActionResponse:
    return vm_service.start_vm(server_id)


@router.post(
    "/servers/{server_id}/stop",
    status_code=202,
    response_model=VmActionResponse,
    summary="Stop a VM",
)
def stop_server(
    server_id: str,
    vm_service: VMService = Depends(get_vm_service),
) -> VmActionResponse:
    return vm_service.stop_vm(server_id)


@router.delete(
    "/servers/{server_id}",
    status_code=204,
    summary="Delete a VM",
)
def delete_server(
    server_id: str,
    force: bool = Query(default=False, description="Force immediate deletion (OpenStack semantics)"),
    vm_service: VMService = Depends(get_vm_service),
) -> Response:
    vm_service.delete_vm(server_id, force=force)
    return Response(status_code=204)

