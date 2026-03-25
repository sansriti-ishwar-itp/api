from __future__ import annotations

from typing import Any

from pydantic import BaseModel


class CreateServerResponse(BaseModel):
    server_id: str
    status: str | None = None


class VmActionResponse(BaseModel):
    server_id: str
    action: str

