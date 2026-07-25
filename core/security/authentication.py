from __future__ import annotations

import hmac
import re
import secrets
import unicodedata
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from typing import Any

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from argon2.profiles import RFC_9106_LOW_MEMORY

from core.security.auth_key import LocalAuthKey
from core.storage.auth_repository import AuthRepository
from core.storage.models import utc_now

SESSION_COOKIE_NAME = "personaos_session"
CSRF_HEADER_NAME = "X-CSRF-Token"

_USERNAME = re.compile(r"^[a-z0-9][a-z0-9._-]{2,31}$")
_COMMON_PASSWORDS = frozenset(
    {
        "123456789012345",
        "adminadminadmin",
        "changemechangeme",
        "correcthorsebatterystaple",
        "letmeinletmeinletmein",
        "passwordpassword",
        "password123456",
        "personaospersonaos",
        "qwertyqwertyqwerty",
    }
)


class AuthenticationError(PermissionError):
    pass


class InvalidCredentialsError(AuthenticationError):
    pass


class InvalidSessionError(AuthenticationError):
    pass


class InvalidCsrfError(AuthenticationError):
    pass


class RecentReauthenticationRequired(AuthenticationError):
    pass


class PasswordPolicyError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class SessionPrincipal:
    account: dict[str, Any]
    session: dict[str, Any]
    token_hash: str

    @property
    def account_id(self) -> str:
        return str(self.account["id"])

    @property
    def session_id(self) -> str:
        return str(self.session["id"])


@dataclass(frozen=True, slots=True)
class SessionGrant:
    principal: SessionPrincipal
    raw_token: str
    csrf_token: str


class AuthenticationService:
    """Password, session, CSRF, reauthentication and account policy boundary."""

    def __init__(
        self,
        *,
        repository: AuthRepository,
        auth_key: LocalAuthKey,
        idle_seconds: int,
        absolute_seconds: int,
        reauthentication_seconds: int,
        max_sessions: int,
        failure_limit: int,
        lockout_seconds: int,
        password_hasher: PasswordHasher | None = None,
    ) -> None:
        self._repository = repository
        self._auth_key = auth_key
        self._idle_seconds = idle_seconds
        self._absolute_seconds = absolute_seconds
        self._reauthentication_seconds = reauthentication_seconds
        self._max_sessions = max_sessions
        self._failure_limit = failure_limit
        self._lockout_seconds = lockout_seconds
        self._password_hasher = password_hasher or PasswordHasher.from_parameters(
            RFC_9106_LOW_MEMORY
        )
        self._dummy_hash: str | None = None

    @property
    def idle_seconds(self) -> int:
        return self._idle_seconds

    @property
    def absolute_seconds(self) -> int:
        return self._absolute_seconds

    @property
    def reauthentication_seconds(self) -> int:
        return self._reauthentication_seconds

    def setup_required(self) -> bool:
        return self._repository.setup_required()

    def create_account(
        self,
        *,
        username: str,
        display_name: str,
        password: str,
        role: str,
        actor: SessionPrincipal | None = None,
        request_id: str | None = None,
    ) -> dict[str, Any]:
        normalized_username = normalize_username(username)
        normalized_display_name = normalize_display_name(display_name)
        normalized_password = validate_password(password)
        if role not in {"admin", "member"}:
            raise ValueError("role must be admin or member")
        if actor is not None:
            if (
                actor.account.get("status") != "active"
                or actor.account.get("role") != "admin"
            ):
                raise PermissionError("admin role is required")
            self.require_recent(
                actor,
                request_id=request_id,
                action="account.create",
                resource_type="account",
                resource_id=normalized_username,
            )
        return self._repository.create_account(
            username=normalized_username,
            display_name=normalized_display_name,
            password_hash=self._password_hasher.hash(normalized_password),
            role=role,
            actor_id=actor.account_id if actor is not None else None,
            request_id=request_id,
        )

    def login(
        self,
        *,
        username: str,
        password: str,
        current_raw_token: str | None,
        request_id: str | None,
        user_agent: str | None,
    ) -> SessionGrant:
        normalized_username = normalize_username(username)
        normalized_password = normalize_password_for_verification(password)
        candidate = self._repository.login_account(normalized_username)
        encoded_hash = (
            str(candidate["password_hash"])
            if candidate is not None and candidate.get("password_hash")
            else self._dummy_password_hash()
        )
        verified = self._verify(encoded_hash, normalized_password)
        account = candidate["account"] if candidate is not None else None
        account_id = str(account["id"]) if account is not None else None
        locked_until = candidate.get("locked_until") if candidate else None
        locked = (
            isinstance(locked_until, datetime)
            and _aware(locked_until) > utc_now()
        )
        usable = (
            verified
            and account is not None
            and account.get("status") == "active"
            and not locked
        )
        subject_hash = self._opaque_hash(
            "login-subject",
            normalized_username,
        )
        if not usable:
            if locked and account_id is not None:
                self._repository.record_authorization_event(
                    account_id=account_id,
                    request_id=request_id,
                    action="session.login",
                    outcome="failed",
                    detail={"temporarily_locked": True},
                )
            else:
                self._repository.record_login_failure(
                    account_id=account_id,
                    request_id=request_id,
                    subject_hash=subject_hash,
                    failure_limit=self._failure_limit,
                    lockout_seconds=self._lockout_seconds,
                )
            raise InvalidCredentialsError("invalid username or password")

        if self._password_hasher.check_needs_rehash(encoded_hash):
            self._repository.update_password_hash(
                account_id,
                self._password_hasher.hash(normalized_password),
            )
        raw_token = self._new_session_token()
        token_hash = self.hash_session_token(raw_token)
        account_result, session_result = self._repository.create_session(
            account_id=account_id,
            token_hash=token_hash,
            idle_seconds=self._idle_seconds,
            absolute_seconds=self._absolute_seconds,
            max_sessions=self._max_sessions,
            request_id=request_id,
            user_agent_hash=(
                self._opaque_hash("user-agent", user_agent)
                if user_agent
                else None
            ),
            replacement_token_hash=(
                self.hash_session_token(current_raw_token)
                if current_raw_token
                else None
            ),
            replacement_reason="replaced_by_login",
        )
        principal = SessionPrincipal(
            account=account_result,
            session=session_result,
            token_hash=token_hash,
        )
        return SessionGrant(
            principal=principal,
            raw_token=raw_token,
            csrf_token=self.csrf_token(principal),
        )

    def authenticate(self, raw_token: str | None) -> SessionPrincipal:
        if not raw_token or len(raw_token) > 256:
            raise InvalidSessionError("authentication required")
        token_hash = self.hash_session_token(raw_token)
        result = self._repository.resolve_session(
            token_hash=token_hash,
            idle_seconds=self._idle_seconds,
        )
        if result is None:
            raise InvalidSessionError("authentication required")
        account, session = result
        return SessionPrincipal(
            account=account,
            session=session,
            token_hash=token_hash,
        )

    def verify_csrf(
        self,
        principal: SessionPrincipal,
        submitted_token: str | None,
    ) -> None:
        expected = self.csrf_token(principal)
        if (
            not submitted_token
            or len(submitted_token) > 256
            or not hmac.compare_digest(expected, submitted_token)
        ):
            raise InvalidCsrfError("CSRF validation failed")

    def csrf_token(self, principal: SessionPrincipal) -> str:
        payload = (
            f"personaos-csrf-v1\0{principal.session_id}\0"
            f"{principal.token_hash}"
        ).encode("utf-8")
        return hmac.new(
            self._auth_key.get(),
            payload,
            sha256,
        ).hexdigest()

    def reauthenticate(
        self,
        *,
        principal: SessionPrincipal,
        password: str,
        request_id: str | None,
    ) -> SessionGrant:
        normalized_password = normalize_password_for_verification(password)
        username = str(principal.account["username"])
        candidate = self._repository.login_account(username)
        encoded_hash = (
            str(candidate["password_hash"])
            if candidate is not None and candidate.get("password_hash")
            else self._dummy_password_hash()
        )
        verified = self._verify(encoded_hash, normalized_password)
        locked_until = candidate.get("locked_until") if candidate else None
        locked = (
            isinstance(locked_until, datetime)
            and _aware(locked_until) > utc_now()
        )
        matches = (
            candidate is not None
            and candidate["account"]["id"] == principal.account_id
            and candidate["account"]["status"] == "active"
            and not locked
        )
        if not verified or not matches:
            if locked:
                self._repository.record_authorization_event(
                    account_id=principal.account_id,
                    request_id=request_id,
                    action="session.reauthenticated",
                    outcome="failed",
                    detail={"temporarily_locked": True},
                )
            else:
                self._repository.record_login_failure(
                    account_id=principal.account_id,
                    request_id=request_id,
                    subject_hash=self._opaque_hash(
                        "login-subject",
                        username,
                    ),
                    failure_limit=self._failure_limit,
                    lockout_seconds=self._lockout_seconds,
                    action="session.reauthenticated",
                )
            raise InvalidCredentialsError("invalid username or password")
        if self._password_hasher.check_needs_rehash(encoded_hash):
            self._repository.update_password_hash(
                principal.account_id,
                self._password_hasher.hash(normalized_password),
            )
        raw_token = self._new_session_token()
        token_hash = self.hash_session_token(raw_token)
        account, auth_session = self._repository.rotate_after_reauthentication(
            session_id=principal.session_id,
            account_id=principal.account_id,
            token_hash=principal.token_hash,
            new_token_hash=token_hash,
            idle_seconds=self._idle_seconds,
            request_id=request_id,
        )
        rotated = SessionPrincipal(
            account=account,
            session=auth_session,
            token_hash=token_hash,
        )
        return SessionGrant(
            principal=rotated,
            raw_token=raw_token,
            csrf_token=self.csrf_token(rotated),
        )

    def logout(
        self,
        *,
        principal: SessionPrincipal,
        request_id: str | None,
    ) -> None:
        self._repository.revoke_session(
            token_hash=principal.token_hash,
            reason="user_logout",
            request_id=request_id,
        )

    def require_recent(
        self,
        principal: SessionPrincipal,
        *,
        request_id: str | None,
        action: str,
        resource_type: str,
        resource_id: str,
    ) -> None:
        reauthenticated_at = _aware(principal.session["reauthenticated_at"])
        age = (utc_now() - reauthenticated_at).total_seconds()
        if age <= self._reauthentication_seconds:
            return
        self._repository.record_authorization_event(
            account_id=principal.account_id,
            request_id=request_id,
            action="authorization.reauthentication_required",
            outcome="denied",
            detail={
                "requested_action": action,
                "resource_type": resource_type,
                "resource_id": resource_id,
                "reauthentication_age_seconds": max(0, int(age)),
            },
        )
        raise RecentReauthenticationRequired("recent reauthentication required")

    def list_accounts(self, actor: SessionPrincipal) -> list[dict[str, Any]]:
        if actor.account.get("role") != "admin":
            raise PermissionError("admin role is required")
        return self._repository.list_accounts()

    def list_accounts_from_trusted_host(self) -> list[dict[str, Any]]:
        return self._repository.list_accounts()

    @staticmethod
    def hash_session_token(raw_token: str) -> str:
        return sha256(raw_token.encode("ascii", errors="strict")).hexdigest()

    @staticmethod
    def _new_session_token() -> str:
        return secrets.token_urlsafe(32)

    def _opaque_hash(self, domain: str, value: str) -> str:
        return hmac.new(
            self._auth_key.get(),
            f"{domain}\0{value}".encode("utf-8"),
            sha256,
        ).hexdigest()

    def _dummy_password_hash(self) -> str:
        if self._dummy_hash is None:
            self._dummy_hash = self._password_hasher.hash(
                secrets.token_urlsafe(24)
            )
        return self._dummy_hash

    def _verify(self, encoded_hash: str, password: str) -> bool:
        try:
            return bool(self._password_hasher.verify(encoded_hash, password))
        except (InvalidHashError, VerificationError):
            return False


def normalize_username(value: str) -> str:
    normalized = value.strip().lower()
    if not _USERNAME.fullmatch(normalized):
        raise ValueError(
            "username must be 3-32 lowercase letters, digits, dots, "
            "underscores or hyphens and start with a letter or digit"
        )
    return normalized


def normalize_display_name(value: str) -> str:
    normalized = " ".join(value.split())
    if not normalized:
        raise ValueError("display_name must not be empty")
    if len(normalized) > 200:
        raise ValueError("display_name must not exceed 200 characters")
    return normalized


def validate_password(value: str) -> str:
    try:
        normalized = normalize_password_for_verification(value)
    except InvalidCredentialsError as exc:
        raise PasswordPolicyError(
            "password must contain between 1 and 1024 UTF-8 bytes"
        ) from exc
    if len(normalized) < 15:
        raise PasswordPolicyError("password must contain at least 15 characters")
    folded = re.sub(r"[\s_-]+", "", normalized.casefold())
    if folded in _COMMON_PASSWORDS:
        raise PasswordPolicyError("password is too common")
    return normalized


def normalize_password_for_verification(value: str) -> str:
    normalized = unicodedata.normalize("NFC", value)
    encoded = normalized.encode("utf-8")
    if not normalized or len(encoded) > 1024:
        raise InvalidCredentialsError("invalid username or password")
    return normalized


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)
