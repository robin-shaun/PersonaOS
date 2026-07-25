from __future__ import annotations

import os
import stat
import threading
from pathlib import Path

_KEY_BYTES = 32


class AuthKeyError(RuntimeError):
    pass


class LocalAuthKey:
    """Lazily load or create the local HMAC key used for CSRF derivation."""

    def __init__(self, path: Path) -> None:
        self._path = path.resolve()
        self._key: bytes | None = None
        self._lock = threading.Lock()

    @property
    def path(self) -> Path:
        return self._path

    def get(self) -> bytes:
        if self._key is not None:
            return self._key
        with self._lock:
            if self._key is not None:
                return self._key
            try:
                key = self._path.read_bytes()
            except FileNotFoundError:
                key = self._create()
            except OSError as exc:
                raise AuthKeyError(
                    f"unable to read authentication key file {self._path}"
                ) from exc
            if len(key) != _KEY_BYTES:
                raise AuthKeyError(
                    f"authentication key file {self._path} must contain 32 bytes"
                )
            self._validate_permissions()
            self._key = key
            return key

    def _create(self) -> bytes:
        self._path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        key = os.urandom(_KEY_BYTES)
        try:
            descriptor = os.open(
                self._path,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                0o600,
            )
        except FileExistsError:
            try:
                return self._path.read_bytes()
            except OSError as exc:
                raise AuthKeyError(
                    f"unable to read authentication key file {self._path}"
                ) from exc
        except OSError as exc:
            raise AuthKeyError(
                f"unable to create authentication key file {self._path}"
            ) from exc
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(key)
                stream.flush()
                os.fsync(stream.fileno())
        except Exception:
            self._path.unlink(missing_ok=True)
            raise
        return key

    def _validate_permissions(self) -> None:
        if os.name != "posix":
            return
        try:
            mode = stat.S_IMODE(self._path.stat().st_mode)
        except OSError as exc:
            raise AuthKeyError(
                f"unable to inspect authentication key file {self._path}"
            ) from exc
        if mode & 0o077:
            raise AuthKeyError(
                f"authentication key file {self._path} must not be "
                "group/world accessible"
            )
