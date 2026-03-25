from __future__ import annotations

from openstack import exceptions as os_exc

from app.core.errors import openstack_exception_to_http


def test_openstack_bad_request_surfaces_provider_message() -> None:
    exc = os_exc.BadRequestException("No valid host was found")
    http_exc = openstack_exception_to_http(exc)

    assert http_exc.status_code == 400
    assert "No valid host was found" in str(http_exc.detail)

