from __future__ import annotations

from fastapi import HTTPException, status
from openstack import exceptions as os_exc

#maps openstacksdk exceptions to appropriate HTTPException for API responses
def openstack_exception_to_http(exc: Exception) -> HTTPException:
    if isinstance(exc, os_exc.NotFoundException):
        return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Server not found")

    if isinstance(exc, os_exc.BadRequestException):
        return HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid request for OpenStack")

    if isinstance(exc, os_exc.ForbiddenException):
        return HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden by OpenStack")

    if isinstance(exc, os_exc.ConflictException):
        return HTTPException(status_code=status.HTTP_409_CONFLICT, detail="OpenStack reported a conflict")

    # Fall back for timeouts, endpoint discovery failures, or general HTTP errors.
    return HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc) or "OpenStack operation failed")

