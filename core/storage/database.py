from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from sqlalchemy import Engine, create_engine, event, text
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from core.storage.models import Base

POSTGRES_RUNTIME_ROLE = "personaos_runtime"


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
    def session(
        self,
        *,
        owner_id: str | None = None,
        system: bool = False,
    ) -> Iterator[Session]:
        if owner_id is not None:
            owner_id = owner_id.strip()
            if (
                not owner_id
                or len(owner_id) > 64
                or any(character in owner_id for character in "\r\n\t")
            ):
                raise ValueError("database owner scope is invalid")
        if owner_id is not None and system:
            raise ValueError("database session cannot be owner and system scoped")
        session = self._session_factory()
        try:
            if self.engine.dialect.name == "postgresql":
                if not system:
                    # The Compose login owns the schema and is therefore also the
                    # migration/system identity. Drop to a fixed NOLOGIN,
                    # NOBYPASSRLS role before every ordinary transaction so a
                    # privileged connection cannot silently bypass row policies.
                    session.execute(
                        text(f'SET LOCAL ROLE "{POSTGRES_RUNTIME_ROLE}"')
                    )
                session.execute(
                    text(
                        "SELECT "
                        "set_config('personaos.system_bypass', :bypass, true), "
                        "set_config('personaos.owner_id', :owner_id, true)"
                    ),
                    {
                        "bypass": "on" if system else "off",
                        "owner_id": owner_id or "",
                    },
                )
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()
