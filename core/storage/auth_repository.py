from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

from sqlalchemy import func, select

from core.storage.database import Database
from core.storage.models import (
    AuthEventRecord,
    AuthSessionRecord,
    UserRecord,
    utc_now,
)


def _new_id() -> str:
    return str(uuid4())


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def _account_dict(record: UserRecord) -> dict[str, Any]:
    return {
        "id": record.id,
        "username": record.username,
        "display_name": record.display_name,
        "role": record.role,
        "status": record.status,
        "created_at": _aware(record.created_at).isoformat(),
        "updated_at": _aware(record.updated_at).isoformat(),
        "password_changed_at": (
            _aware(record.password_changed_at).isoformat()
            if record.password_changed_at is not None
            else None
        ),
        "last_login_at": (
            _aware(record.last_login_at).isoformat()
            if record.last_login_at is not None
            else None
        ),
    }


def _session_dict(record: AuthSessionRecord) -> dict[str, Any]:
    return {
        "id": record.id,
        "user_id": record.user_id,
        "token_hash": record.token_hash,
        "authenticated_at": _aware(record.authenticated_at),
        "reauthenticated_at": _aware(record.reauthenticated_at),
        "last_seen_at": _aware(record.last_seen_at),
        "idle_expires_at": _aware(record.idle_expires_at),
        "absolute_expires_at": _aware(record.absolute_expires_at),
        "revoked_at": (
            _aware(record.revoked_at)
            if record.revoked_at is not None
            else None
        ),
    }


class AuthRepository:
    """System-scoped persistence for credentials and revocable sessions."""

    def __init__(self, database: Database) -> None:
        self.database = database

    def setup_required(self) -> bool:
        with self.database.session(system=True) as session:
            count = session.scalar(
                select(func.count(UserRecord.id)).where(
                    UserRecord.username.is_not(None),
                    UserRecord.status.in_(("active", "disabled")),
                )
            )
            return int(count or 0) == 0

    def create_account(
        self,
        *,
        username: str,
        display_name: str,
        password_hash: str,
        role: str,
        actor_id: str | None,
        request_id: str | None,
    ) -> dict[str, Any]:
        now = utc_now()
        with self.database.session(system=True) as session:
            existing = session.scalar(
                select(UserRecord).where(UserRecord.username == username)
            )
            if existing is not None:
                raise ValueError("username is already in use")
            account_count = int(
                session.scalar(
                    select(func.count(UserRecord.id)).where(
                        UserRecord.username.is_not(None),
                        UserRecord.status.in_(("active", "disabled")),
                    )
                )
                or 0
            )
            if account_count == 0 and role != "admin":
                raise ValueError("the first account must be an admin")
            if account_count > 0 and actor_id is not None:
                actor = session.get(UserRecord, actor_id)
                if (
                    actor is None
                    or actor.status != "active"
                    or actor.role != "admin"
                ):
                    raise PermissionError("admin role is required")
            account = UserRecord(
                id=_new_id(),
                username=username,
                display_name=display_name,
                password_hash=password_hash,
                role=role,
                status="active",
                failed_login_count=0,
                password_changed_at=now,
                created_at=now,
                updated_at=now,
            )
            session.add(account)
            session.flush()
            self._add_event(
                session,
                account_id=account.id,
                actor_id=actor_id or account.id,
                request_id=request_id,
                action="account.created",
                outcome="succeeded",
                subject_hash=None,
                detail={"role": role, "created_via": "api" if actor_id else "cli"},
            )
            return _account_dict(account)

    def login_account(self, username: str) -> dict[str, Any] | None:
        with self.database.session(system=True) as session:
            account = session.scalar(
                select(UserRecord).where(UserRecord.username == username)
            )
            if account is None:
                return None
            return {
                "account": _account_dict(account),
                "password_hash": account.password_hash,
                "failed_login_count": account.failed_login_count,
                "locked_until": (
                    _aware(account.locked_until)
                    if account.locked_until is not None
                    else None
                ),
            }

    def record_login_failure(
        self,
        *,
        account_id: str | None,
        request_id: str | None,
        subject_hash: str,
        failure_limit: int,
        lockout_seconds: int,
        action: str = "session.login",
    ) -> None:
        now = utc_now()
        with self.database.session(system=True) as session:
            account = (
                session.scalar(
                    select(UserRecord)
                    .where(UserRecord.id == account_id)
                    .with_for_update()
                )
                if account_id is not None
                else None
            )
            locked = False
            if account is not None:
                account.failed_login_count += 1
                if account.failed_login_count >= failure_limit:
                    account.locked_until = now + timedelta(
                        seconds=lockout_seconds
                    )
                    locked = True
                account.updated_at = now
            self._add_event(
                session,
                account_id=account.id if account is not None else None,
                actor_id=account.id if account is not None else None,
                request_id=request_id,
                action=action,
                outcome="failed",
                subject_hash=subject_hash,
                detail={"temporarily_locked": locked},
            )

    def create_session(
        self,
        *,
        account_id: str,
        token_hash: str,
        idle_seconds: int,
        absolute_seconds: int,
        max_sessions: int,
        request_id: str | None,
        user_agent_hash: str | None,
        replacement_token_hash: str | None,
        replacement_reason: str,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        now = utc_now()
        with self.database.session(system=True) as session:
            account = session.scalar(
                select(UserRecord)
                .where(UserRecord.id == account_id)
                .with_for_update()
            )
            if (
                account is None
                or account.status != "active"
                or account.password_hash is None
            ):
                raise PermissionError("invalid username or password")
            if replacement_token_hash is not None:
                previous = session.scalar(
                    select(AuthSessionRecord)
                    .where(
                        AuthSessionRecord.token_hash == replacement_token_hash,
                        AuthSessionRecord.revoked_at.is_(None),
                    )
                    .with_for_update()
                )
                if previous is not None:
                    previous.revoked_at = now
                    previous.revoke_reason = replacement_reason

            active_sessions = list(
                session.scalars(
                    select(AuthSessionRecord)
                    .where(
                        AuthSessionRecord.user_id == account.id,
                        AuthSessionRecord.revoked_at.is_(None),
                        AuthSessionRecord.absolute_expires_at > now,
                        AuthSessionRecord.idle_expires_at > now,
                    )
                    .order_by(
                        AuthSessionRecord.created_at.desc(),
                        AuthSessionRecord.id.desc(),
                    )
                )
            )
            for stale in active_sessions[max(0, max_sessions - 1) :]:
                stale.revoked_at = now
                stale.revoke_reason = "concurrent_session_limit"

            auth_session = AuthSessionRecord(
                id=_new_id(),
                user_id=account.id,
                token_hash=token_hash,
                created_at=now,
                authenticated_at=now,
                reauthenticated_at=now,
                last_seen_at=now,
                idle_expires_at=now + timedelta(seconds=idle_seconds),
                absolute_expires_at=now + timedelta(seconds=absolute_seconds),
                request_id=request_id,
                user_agent_hash=user_agent_hash,
            )
            account.failed_login_count = 0
            account.locked_until = None
            account.last_login_at = now
            account.updated_at = now
            session.add(auth_session)
            self._add_event(
                session,
                account_id=account.id,
                actor_id=account.id,
                request_id=request_id,
                action="session.login",
                outcome="succeeded",
                subject_hash=None,
                detail={"session_id": auth_session.id},
            )
            session.flush()
            return _account_dict(account), _session_dict(auth_session)

    def resolve_session(
        self,
        *,
        token_hash: str,
        idle_seconds: int,
        touch_interval_seconds: int = 60,
    ) -> tuple[dict[str, Any], dict[str, Any]] | None:
        now = utc_now()
        with self.database.session(system=True) as session:
            auth_session = session.scalar(
                select(AuthSessionRecord)
                .where(AuthSessionRecord.token_hash == token_hash)
                .with_for_update()
            )
            if auth_session is None or auth_session.revoked_at is not None:
                return None
            account = session.get(UserRecord, auth_session.user_id)
            expired = (
                _aware(auth_session.absolute_expires_at) <= now
                or _aware(auth_session.idle_expires_at) <= now
            )
            if (
                expired
                or account is None
                or account.status != "active"
                or account.password_hash is None
            ):
                auth_session.revoked_at = now
                auth_session.revoke_reason = (
                    "expired" if expired else "account_unavailable"
                )
                return None
            if (now - _aware(auth_session.last_seen_at)).total_seconds() >= (
                touch_interval_seconds
            ):
                auth_session.last_seen_at = now
                auth_session.idle_expires_at = min(
                    _aware(auth_session.absolute_expires_at),
                    now + timedelta(seconds=idle_seconds),
                )
            return _account_dict(account), _session_dict(auth_session)

    def rotate_after_reauthentication(
        self,
        *,
        session_id: str,
        account_id: str,
        token_hash: str,
        new_token_hash: str,
        idle_seconds: int,
        request_id: str | None,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        now = utc_now()
        with self.database.session(system=True) as session:
            auth_session = session.scalar(
                select(AuthSessionRecord)
                .where(AuthSessionRecord.id == session_id)
                .with_for_update()
            )
            account = session.scalar(
                select(UserRecord)
                .where(UserRecord.id == account_id)
                .with_for_update()
            )
            if (
                auth_session is None
                or auth_session.user_id != account_id
                or auth_session.token_hash != token_hash
                or auth_session.revoked_at is not None
                or account is None
                or account.status != "active"
            ):
                raise PermissionError("session is no longer valid")
            auth_session.token_hash = new_token_hash
            auth_session.reauthenticated_at = now
            auth_session.last_seen_at = now
            auth_session.idle_expires_at = min(
                _aware(auth_session.absolute_expires_at),
                now + timedelta(seconds=idle_seconds),
            )
            account.failed_login_count = 0
            account.locked_until = None
            account.updated_at = now
            self._add_event(
                session,
                account_id=account.id,
                actor_id=account.id,
                request_id=request_id,
                action="session.reauthenticated",
                outcome="succeeded",
                subject_hash=None,
                detail={"session_id": auth_session.id, "token_rotated": True},
            )
            session.flush()
            return _account_dict(account), _session_dict(auth_session)

    def update_password_hash(self, account_id: str, password_hash: str) -> None:
        with self.database.session(system=True) as session:
            account = session.scalar(
                select(UserRecord)
                .where(UserRecord.id == account_id)
                .with_for_update()
            )
            if account is None:
                raise KeyError(f"UserRecord not found: {account_id}")
            account.password_hash = password_hash
            account.updated_at = utc_now()

    def revoke_session(
        self,
        *,
        token_hash: str,
        reason: str,
        request_id: str | None,
        record_event: bool = True,
    ) -> bool:
        now = utc_now()
        with self.database.session(system=True) as session:
            auth_session = session.scalar(
                select(AuthSessionRecord)
                .where(AuthSessionRecord.token_hash == token_hash)
                .with_for_update()
            )
            if auth_session is None:
                return False
            changed = auth_session.revoked_at is None
            if changed:
                auth_session.revoked_at = now
                auth_session.revoke_reason = reason
            if record_event:
                self._add_event(
                    session,
                    account_id=auth_session.user_id,
                    actor_id=auth_session.user_id,
                    request_id=request_id,
                    action="session.logout",
                    outcome="succeeded",
                    subject_hash=None,
                    detail={
                        "session_id": auth_session.id,
                        "idempotent_replay": not changed,
                    },
                )
            return changed

    def list_accounts(self) -> list[dict[str, Any]]:
        with self.database.session(system=True) as session:
            records = session.scalars(
                select(UserRecord)
                .where(UserRecord.username.is_not(None))
                .order_by(UserRecord.created_at, UserRecord.id)
            )
            return [_account_dict(item) for item in records]

    def record_authorization_event(
        self,
        *,
        account_id: str,
        request_id: str | None,
        action: str,
        outcome: str,
        detail: dict[str, Any],
    ) -> None:
        with self.database.session(system=True) as session:
            self._add_event(
                session,
                account_id=account_id,
                actor_id=account_id,
                request_id=request_id,
                action=action,
                outcome=outcome,
                subject_hash=None,
                detail=detail,
            )

    @staticmethod
    def _add_event(
        session,
        *,
        account_id: str | None,
        actor_id: str | None,
        request_id: str | None,
        action: str,
        outcome: str,
        subject_hash: str | None,
        detail: dict[str, Any],
    ) -> None:
        session.add(
            AuthEventRecord(
                id=_new_id(),
                account_id=account_id,
                actor_id=actor_id,
                request_id=request_id,
                action=action,
                outcome=outcome,
                subject_hash=subject_hash,
                detail=detail,
            )
        )
