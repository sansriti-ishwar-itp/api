from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from fastapi import Depends, HTTPException, Security
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from keystoneauth1 import session as ks_session
from keystoneauth1.identity import v3
from openstack import connection as os_connection
from openstack.exceptions import ServiceDiscoveryException

from app.core.config import Settings, get_settings
from app.services.openstack_client import OpenStackVMClient
from app.services.vm_service import VMService

bearer_scheme = HTTPBearer(auto_error=True)


@dataclass(frozen=True)
class OpenStackContext:
    conn: os_connection.Connection
    ks_session: ks_session.Session


def _token_from_credentials(
    credentials: HTTPAuthorizationCredentials,
) -> str:
    if not credentials.scheme.lower() == "bearer":
        # HTTPBearer only parses the scheme/value; we still enforce Bearer semantics.
        raise HTTPException(status_code=401, detail="Invalid authorization scheme")
    return credentials.credentials


def get_openstack_context(
    credentials: HTTPAuthorizationCredentials = Security(bearer_scheme),
    settings: Settings = Depends(get_settings),
) -> OpenStackContext:
    token = _token_from_credentials(credentials)

    # Use keystoneauth1's "Token" plugin to authenticate via an existing Keystone token.
    # openstacksdk will reuse this session for service discovery and API calls.
    auth_kwargs: dict[str, Optional[str] | bool] = {
        "auth_url": settings.openstack_auth_url,
        "token": token,
        "reauthenticate": True,
        "include_catalog": True,
    }

    if settings.openstack_project_id:
        auth_kwargs["project_id"] = settings.openstack_project_id
    if settings.openstack_user_domain_id:
        auth_kwargs["user_domain_id"] = settings.openstack_user_domain_id
    if settings.openstack_project_domain_id:
        auth_kwargs["project_domain_id"] = settings.openstack_project_domain_id

    auth = v3.Token(**auth_kwargs)
    ks_sess = ks_session.Session(auth=auth)

    try:
        conn = os_connection.Connection(
            session=ks_sess,
            region_name=settings.openstack_region_name,
            compute_api_version=settings.openstack_compute_api_version,
            identity_interface=settings.openstack_identity_interface,
        )
    except ServiceDiscoveryException as e:
        raise HTTPException(status_code=502, detail=f"OpenStack service discovery failed: {e}")

    return OpenStackContext(conn=conn, ks_session=ks_sess)


def get_vm_service(ctx: OpenStackContext = Depends(get_openstack_context)) -> VMService:
    client = OpenStackVMClient(conn=ctx.conn, ks_session=ctx.ks_session)
    return VMService(client=client)

