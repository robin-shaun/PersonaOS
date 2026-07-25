"""Verify PostgreSQL owner row security against the live Compose database."""

from __future__ import annotations

import argparse
import json
import sys
from uuid import uuid4

from sqlalchemy import select, update
from sqlalchemy.exc import DBAPIError

from core.config import Settings
from core.storage.database import Database
from core.storage.models import (
    AnswerCitationRecord,
    ConversationMessageRecord,
    DocumentChunkRecord,
    PersonaRecord,
    SourceDocumentRecord,
    TaskRecord,
    UserRecord,
)


class RowSecurityError(RuntimeError):
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RowSecurityError(message)


def account_id(database: Database, username: str) -> str:
    with database.session(system=True) as session:
        account = session.scalar(
            select(UserRecord).where(UserRecord.username == username)
        )
        if account is None:
            raise RowSecurityError(f"account not found: {username}")
        return account.id


def run(owner_a_username: str, owner_b_username: str) -> dict[str, object]:
    settings = Settings.from_env()
    database = Database(settings.database_url)
    require(
        database.engine.dialect.name == "postgresql",
        "RLS smoke requires PostgreSQL",
    )
    owner_a = account_id(database, owner_a_username)
    owner_b = account_id(database, owner_b_username)

    with database.session(system=True) as session:
        all_rows = list(
            session.scalars(
                select(PersonaRecord).order_by(PersonaRecord.id)
            )
        )
        all_tasks = list(session.scalars(select(TaskRecord)))
        all_documents = list(session.scalars(select(SourceDocumentRecord)))
        all_chunks = list(session.scalars(select(DocumentChunkRecord)))
        all_messages = list(
            session.scalars(select(ConversationMessageRecord))
        )
        all_citations = list(
            session.scalars(select(AnswerCitationRecord))
        )
    require(
        {item.owner_id for item in all_rows} >= {owner_a, owner_b},
        "system scope did not find both account workspaces",
    )
    owner_b_persona = next(
        item for item in all_rows if item.owner_id == owner_b
    )
    persona_owners = {item.id: item.owner_id for item in all_rows}
    message_owners = {item.id: item.owner_id for item in all_messages}

    with database.session(owner_id=owner_a) as session:
        owner_a_rows = list(session.scalars(select(PersonaRecord)))
        owner_a_tasks = list(session.scalars(select(TaskRecord)))
        owner_a_documents = list(
            session.scalars(select(SourceDocumentRecord))
        )
        owner_a_chunks = list(session.scalars(select(DocumentChunkRecord)))
        owner_a_citations = list(
            session.scalars(select(AnswerCitationRecord))
        )
        hidden = session.get(PersonaRecord, owner_b_persona.id)
        cross_update = session.execute(
            update(PersonaRecord)
            .where(PersonaRecord.id == owner_b_persona.id)
            .values(display_name="RLS must suppress this update")
        )
        cross_update_rowcount = cross_update.rowcount
    require(
        owner_a_rows and all(item.owner_id == owner_a for item in owner_a_rows),
        "owner scope returned another account's row",
    )
    require(hidden is None, "primary-key lookup crossed RLS")
    require(cross_update_rowcount == 0, "cross-owner update crossed RLS")
    require(
        owner_a_tasks
        and all(item.user_id == owner_a for item in owner_a_tasks),
        "direct task policy returned another account's row",
    )
    require(
        owner_a_documents
        and all(item.owner_id == owner_a for item in owner_a_documents),
        "direct document policy returned another account's row",
    )
    require(
        owner_a_chunks
        and all(
            persona_owners[item.persona_id] == owner_a
            for item in owner_a_chunks
        ),
        "indirect document chunk policy returned another account's row",
    )
    require(
        owner_a_citations
        and all(
            message_owners[item.assistant_message_id] == owner_a
            for item in owner_a_citations
        ),
        "indirect citation policy returned another account's row",
    )
    require(
        {item.user_id for item in all_tasks} >= {owner_a, owner_b}
        and {item.owner_id for item in all_documents} >= {owner_a, owner_b},
        "system scope did not find both accounts' tasks and documents",
    )
    require(
        all_chunks and all_citations,
        "system scope did not find expected indirect evidence rows",
    )

    with database.session() as session:
        unscoped_counts = {
            "personas": len(
                list(session.scalars(select(PersonaRecord.id)))
            ),
            "tasks": len(list(session.scalars(select(TaskRecord.id)))),
            "chunks": len(
                list(session.scalars(select(DocumentChunkRecord.id)))
            ),
            "citations": len(
                list(session.scalars(select(AnswerCitationRecord.id)))
            ),
        }
    require(
        all(value == 0 for value in unscoped_counts.values()),
        "unscoped transaction did not default deny",
    )

    insert_sqlstate = ""
    try:
        with database.session(owner_id=owner_a) as session:
            session.add(
                PersonaRecord(
                    id=str(uuid4()),
                    owner_id=owner_b,
                    display_name="RLS rejected cross-owner insert",
                    description="",
                    simulation_notice="test",
                    allowed_model_boundaries=["local"],
                    status="active",
                    version=1,
                )
            )
            session.flush()
    except DBAPIError as exc:
        insert_sqlstate = str(getattr(exc.orig, "sqlstate", "") or "")
    require(
        insert_sqlstate == "42501",
        "cross-owner insert was not rejected by PostgreSQL RLS",
    )

    return {
        "dialect": database.engine.dialect.name,
        "system_persona_count": len(all_rows),
        "owner_a_visible_count": len(owner_a_rows),
        "owner_a_task_count": len(owner_a_tasks),
        "owner_a_document_count": len(owner_a_documents),
        "owner_a_chunk_count": len(owner_a_chunks),
        "owner_a_citation_count": len(owner_a_citations),
        "unscoped_visible_counts": unscoped_counts,
        "cross_update_rowcount": cross_update_rowcount,
        "cross_insert_sqlstate": insert_sqlstate,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--owner-a", required=True)
    parser.add_argument("--owner-b", required=True)
    args = parser.parse_args()
    try:
        result = run(args.owner_a, args.owner_b)
    except RowSecurityError as exc:
        print(f"PersonaOS RLS smoke failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
