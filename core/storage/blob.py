from __future__ import annotations

import base64
import binascii
import os
import re
import stat
import tempfile
import threading
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Protocol

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

_MAGIC = b"POSB1"
_NONCE_BYTES = 12
_KEY_BYTES = 32
_OBJECT_KEY_PATTERN = re.compile(r"^sha256/[0-9a-f]{2}/[0-9a-f]{64}\.blob$")


class BlobStoreError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class BlobWriteResult:
    object_key: str
    content_sha256: str
    byte_size: int
    created: bool


class BlobStore(Protocol):
    def put(self, content: bytes) -> BlobWriteResult: ...

    def get(self, object_key: str, *, expected_sha256: str) -> bytes: ...

    def delete(self, object_key: str) -> bool: ...


def decode_blob_key(value: str) -> bytes:
    """Decode a 256-bit key from URL-safe base64 or 64 hexadecimal chars."""

    normalized = value.strip()
    if not normalized:
        raise ValueError("PERSONA_BLOB_KEY must not be empty")
    if re.fullmatch(r"[0-9a-fA-F]{64}", normalized):
        key = bytes.fromhex(normalized)
    else:
        padding = "=" * (-len(normalized) % 4)
        try:
            key = base64.urlsafe_b64decode(normalized + padding)
        except (binascii.Error, ValueError) as exc:
            raise ValueError(
                "PERSONA_BLOB_KEY must be URL-safe base64 or 64 hexadecimal chars"
            ) from exc
    if len(key) != _KEY_BYTES:
        raise ValueError("PERSONA_BLOB_KEY must decode to exactly 32 bytes")
    return key


class EncryptedLocalBlobStore:
    """Content-addressed AES-256-GCM storage with no plaintext temp files."""

    def __init__(
        self,
        *,
        root: Path,
        key: bytes | None = None,
        key_path: Path | None = None,
    ) -> None:
        if key is not None and len(key) != _KEY_BYTES:
            raise ValueError("blob encryption key must be exactly 32 bytes")
        self._root = root.resolve()
        self._provided_key = key
        self._key_path = (key_path or self._root.parent / "persona_blob.key").resolve()
        self._key_lock = threading.Lock()

    @property
    def root(self) -> Path:
        return self._root

    def put(self, content: bytes) -> BlobWriteResult:
        if not content:
            raise ValueError("blob content must not be empty")
        digest = sha256(content).hexdigest()
        object_key = f"sha256/{digest[:2]}/{digest}.blob"
        target = self._path_for(object_key)
        target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)

        if target.exists():
            existing = self.get(object_key, expected_sha256=digest)
            if existing != content:
                raise BlobStoreError(
                    f"content-addressed blob collision detected for {digest}"
                )
            return BlobWriteResult(
                object_key=object_key,
                content_sha256=digest,
                byte_size=len(content),
                created=False,
            )

        key = self._load_key()
        nonce = os.urandom(_NONCE_BYTES)
        encrypted = AESGCM(key).encrypt(
            nonce,
            content,
            digest.encode("ascii"),
        )
        payload = _MAGIC + nonce + encrypted
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=".persona-blob-",
            dir=target.parent,
        )
        temporary = Path(temporary_name)
        try:
            os.fchmod(descriptor, 0o600)
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, target)
            target.chmod(0o600)
            self._fsync_directory(target.parent)
        except Exception:
            try:
                os.close(descriptor)
            except OSError:
                pass
            temporary.unlink(missing_ok=True)
            raise
        return BlobWriteResult(
            object_key=object_key,
            content_sha256=digest,
            byte_size=len(content),
            created=True,
        )

    def get(self, object_key: str, *, expected_sha256: str) -> bytes:
        if not re.fullmatch(r"[0-9a-f]{64}", expected_sha256):
            raise ValueError("expected_sha256 must be a lowercase SHA-256 digest")
        path = self._path_for(object_key)
        try:
            payload = path.read_bytes()
        except OSError as exc:
            raise BlobStoreError(f"unable to read blob {object_key}") from exc
        minimum_size = len(_MAGIC) + _NONCE_BYTES + 16
        if len(payload) < minimum_size or not payload.startswith(_MAGIC):
            raise BlobStoreError(f"blob {object_key} has an invalid envelope")
        nonce_start = len(_MAGIC)
        nonce_end = nonce_start + _NONCE_BYTES
        try:
            content = AESGCM(self._load_key()).decrypt(
                payload[nonce_start:nonce_end],
                payload[nonce_end:],
                expected_sha256.encode("ascii"),
            )
        except InvalidTag as exc:
            raise BlobStoreError(f"blob {object_key} failed authentication") from exc
        actual_digest = sha256(content).hexdigest()
        if actual_digest != expected_sha256:
            raise BlobStoreError(f"blob {object_key} failed content hash verification")
        return content

    def delete(self, object_key: str) -> bool:
        path = self._path_for(object_key)
        try:
            path.unlink()
        except FileNotFoundError:
            return False
        except OSError as exc:
            raise BlobStoreError(f"unable to delete blob {object_key}") from exc
        self._fsync_directory(path.parent)
        return True

    def _path_for(self, object_key: str) -> Path:
        if not _OBJECT_KEY_PATTERN.fullmatch(object_key):
            raise ValueError("invalid content-addressed object key")
        path = (self._root / object_key).resolve()
        if not path.is_relative_to(self._root):
            raise ValueError("blob object key escapes the storage root")
        return path

    def _load_key(self) -> bytes:
        if self._provided_key is not None:
            return self._provided_key
        with self._key_lock:
            if self._provided_key is not None:
                return self._provided_key
            try:
                key = self._key_path.read_bytes()
            except FileNotFoundError:
                key = self._create_key_file()
            except OSError as exc:
                raise BlobStoreError(
                    f"unable to read blob key file {self._key_path}"
                ) from exc
            if len(key) != _KEY_BYTES:
                raise BlobStoreError(
                    f"blob key file {self._key_path} must contain 32 bytes"
                )
            self._validate_key_file_permissions()
            self._provided_key = key
            return key

    def _create_key_file(self) -> bytes:
        self._key_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        key = os.urandom(_KEY_BYTES)
        try:
            descriptor = os.open(
                self._key_path,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                0o600,
            )
        except FileExistsError:
            try:
                return self._key_path.read_bytes()
            except OSError as exc:
                raise BlobStoreError(
                    f"unable to read blob key file {self._key_path}"
                ) from exc
        except OSError as exc:
            raise BlobStoreError(
                f"unable to create blob key file {self._key_path}"
            ) from exc
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(key)
                stream.flush()
                os.fsync(stream.fileno())
            self._fsync_directory(self._key_path.parent)
        except Exception:
            self._key_path.unlink(missing_ok=True)
            raise
        return key

    def _validate_key_file_permissions(self) -> None:
        if os.name != "posix":
            return
        try:
            mode = stat.S_IMODE(self._key_path.stat().st_mode)
        except OSError as exc:
            raise BlobStoreError(
                f"unable to inspect blob key file {self._key_path}"
            ) from exc
        if mode & 0o077:
            raise BlobStoreError(
                f"blob key file {self._key_path} must not be group/world accessible"
            )

    @staticmethod
    def _fsync_directory(directory: Path) -> None:
        try:
            descriptor = os.open(directory, os.O_RDONLY)
        except OSError:
            return
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
