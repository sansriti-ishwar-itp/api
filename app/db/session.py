"""Async SQLAlchemy engine, session factory, and FastAPI dependency.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Optional

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from app.core.config import get_settings


class Base(DeclarativeBase):
    """Single declarative base shared by all ORM models."""


_engine: Optional[AsyncEngine] = None
_sessionmaker: Optional[async_sessionmaker[AsyncSession]] = None


def init_engine(database_url: str | None = None) -> AsyncEngine:
    """Initialize the global async engine. Called from app startup."""
    global _engine, _sessionmaker
    url = database_url or get_settings().database_url
    # SQLite needs `check_same_thread=False` even with async; aiosqlite handles it.
    connect_args = {"check_same_thread": False} if url.startswith("sqlite") else {}
    _engine = create_async_engine(url, future=True, connect_args=connect_args)
    _sessionmaker = async_sessionmaker(_engine, expire_on_commit=False)
    return _engine


async def shutdown_engine() -> None:
    """Dispose the global engine. Called from app shutdown."""
    global _engine, _sessionmaker
    if _engine is not None:
        await _engine.dispose()
    _engine = None
    _sessionmaker = None


def get_engine() -> AsyncEngine:
    if _engine is None:
        raise RuntimeError("DB engine not initialized; call init_engine() first")
    return _engine


def get_sessionmaker() -> async_sessionmaker[AsyncSession]:
    if _sessionmaker is None:
        raise RuntimeError("DB sessionmaker not initialized; call init_engine() first")
    return _sessionmaker


async def get_session() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency: yields a session, commits on success, rolls back on error."""
    sm = get_sessionmaker()
    async with sm() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise


async def create_all() -> None:
    """Create tables for prototype (no Alembic). Idempotent."""
    # Import here so models register on Base before create_all runs.
    from app.db import models  # noqa: F401

    engine = get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
