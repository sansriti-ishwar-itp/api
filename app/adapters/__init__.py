"""Adapter layer: a swappable VM backend (mock or real OpenStack)."""

from app.adapters.base import (
    AdapterError,
    HealthResult,
    ServerInfo,
    VMAdapter,
    VMNotFoundError,
)
from app.adapters.factory import build_adapter

__all__ = [
    "AdapterError",
    "HealthResult",
    "ServerInfo",
    "VMAdapter",
    "VMNotFoundError",
    "build_adapter",
]
