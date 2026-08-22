from __future__ import annotations

from app.db.base import Base

__all__ = ["Base", "SessionLocal", "get_db_session", "init_db"]


def __getattr__(name: str):
    if name in {"SessionLocal", "get_db_session", "init_db"}:
        from app.db.session import SessionLocal, get_db_session, init_db

        return {
            "SessionLocal": SessionLocal,
            "get_db_session": get_db_session,
            "init_db": init_db,
        }[name]
    raise AttributeError(f"module 'app.db' has no attribute {name!r}")
