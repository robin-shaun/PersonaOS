from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import Mock

from core.storage.database import POSTGRES_RUNTIME_ROLE, Database


def test_postgresql_owner_session_assumes_runtime_role_before_scope() -> None:
    session = Mock()
    database = _postgres_database(session)

    with database.session(owner_id="account-a") as active_session:
        assert active_session is session

    assert len(session.execute.call_args_list) == 2
    role_statement = str(session.execute.call_args_list[0].args[0])
    scope_statement = str(session.execute.call_args_list[1].args[0])
    scope_parameters = session.execute.call_args_list[1].args[1]
    assert role_statement == f'SET LOCAL ROLE "{POSTGRES_RUNTIME_ROLE}"'
    assert "set_config('personaos.owner_id'" in scope_statement
    assert scope_parameters == {
        "bypass": "off",
        "owner_id": "account-a",
    }
    session.commit.assert_called_once_with()
    session.rollback.assert_not_called()
    session.close.assert_called_once_with()


def test_postgresql_system_session_keeps_login_role_and_sets_bypass() -> None:
    session = Mock()
    database = _postgres_database(session)

    with database.session(system=True):
        pass

    session.execute.assert_called_once()
    statement, parameters = session.execute.call_args.args
    assert "SET LOCAL ROLE" not in str(statement)
    assert parameters == {
        "bypass": "on",
        "owner_id": "",
    }


def _postgres_database(session: Mock) -> Database:
    database = Database.__new__(Database)
    database.engine = SimpleNamespace(
        dialect=SimpleNamespace(name="postgresql")
    )
    database._session_factory = Mock(return_value=session)
    return database
