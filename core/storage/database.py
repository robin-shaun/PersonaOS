from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from sqlalchemy import Engine, create_engine, event, text
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from core.storage.models import Base


class Database:
    def __init__(self, url: str) -> None:
        engine_options: dict[str, object] = {}
        if url.startswith("sqlite"):
            engine_options["connect_args"] = {"check_same_thread": False}
        if url in {"sqlite://", "sqlite:///:memory:"}:
            engine_options["poolclass"] = StaticPool
        if not url.startswith("sqlite"):
            engine_options["pool_pre_ping"] = True

        self.engine: Engine = create_engine(url, **engine_options)
        if url.startswith("sqlite"):
            event.listen(self.engine, "connect", self._enable_sqlite_foreign_keys)
        self._session_factory = sessionmaker(
            bind=self.engine,
            autoflush=False,
            expire_on_commit=False,
        )

    @staticmethod
    def _enable_sqlite_foreign_keys(dbapi_connection: Any, _: object) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    def create_schema(self) -> None:
        if self.engine.dialect.name == "postgresql":
            with self.engine.begin() as connection:
                connection.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
                connection.execute(text("CREATE EXTENSION IF NOT EXISTS pg_trgm"))
        Base.metadata.create_all(self.engine)

    @contextmanager
    def session(self) -> Iterator[Session]:
        session = self._session_factory()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()
