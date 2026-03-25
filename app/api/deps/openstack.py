from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated, Optional

from fastapi import Depends, HTTPException, Security
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from keystoneauth1 import session as ks_session
from keystoneauth1.identity import v3
from openstack import connection as os_connection
from openstack.exceptions import ServiceDiscoveryException

from app.core.config import Settings, get_settings
from app.services.openstack_client import OpenStackVMClient
from app.services.vm_service import VMService

# auto_error=False: return None when the header is missing so we can return a clear 401
# (HTTPBearer(auto_error=True) yields 403 "Not authenticated", which confuses Swagger users).
bearer_scheme = HTTPBearer(
    auto_error=False,
    description=(
        "Keystone token from: openstack token issue -f value -c id. "
        "Paste the token only (Swagger adds the Bearer prefix). "
        "If you pasted 'Bearer …' by mistake, it is accepted too."
    ),
)


@dataclass(frozen=True)
class OpenStackContext:
    conn: os_connection.Connection
    ks_session: ks_session.Session


def _normalize_raw_token(value: str) -> str:
    """Strip whitespace and accidental duplicate 'Bearer ' prefix (common in Swagger)."""
    t = value.strip()
    lower = t.lower()
    if lower.startswith("bearer "):
        t = t[7:].strip()
    return t


def _token_from_credentials(
    credentials: HTTPAuthorizationCredentials,
) -> str:
    if credentials.scheme.lower() != "bearer":
        raise HTTPException(status_code=401, detail="Invalid authorization scheme")
    token = _normalize_raw_token(credentials.credentials)
    if not token:
        raise HTTPException(
            status_code=401,
            detail="Empty bearer token after Authorization header",
        )
    return token


def get_openstack_context(
    credentials: Annotated[Optional[HTTPAuthorizationCredentials], Security(bearer_scheme)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> OpenStackContext:
    if credentials is None:
        raise HTTPException(
            status_code=401,
            detail=(
                "Missing Authorization header. Use: Authorization: Bearer <keystone_token>. "
                "In Swagger (/docs): click Authorize, paste the token only, then Execute."
            ),
        )
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
        # keystoneauth1's v3.Token uses `domain_id`/`domain_name` (not `user_domain_id`).
        # Keep our Settings naming for backward compatibility, but map to the SDK arg.
        auth_kwargs["domain_id"] = settings.openstack_user_domain_id
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
