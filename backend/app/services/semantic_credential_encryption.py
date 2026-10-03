"""Provider-neutral authenticated encryption for per-user semantic credentials."""

import base64
import binascii
import os

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM


class SemanticCredentialEncryptionError(RuntimeError):
    """Safe failure without credential or ciphertext details."""


class SemanticCredentialEncryption:
    _version = 1

    def __init__(self, encoded_key: str | None) -> None:
        self._encoded_key = encoded_key

    def configured(self) -> bool:
        try:
            self._key()
        except SemanticCredentialEncryptionError:
            return False
        return True

    def encrypt(self, user_id: str, provider: str, credential: str) -> tuple[bytes, bytes, int]:
        nonce = os.urandom(12)
        ciphertext = AESGCM(self._key()).encrypt(nonce, credential.encode(), self._aad(user_id, provider))
        return nonce, ciphertext, self._version

    def decrypt(self, user_id: str, provider: str, nonce: bytes, ciphertext: bytes, version: int) -> str:
        if version != self._version:
            raise SemanticCredentialEncryptionError("The saved semantic credential cannot be opened with the current encryption configuration.")
        try:
            value = AESGCM(self._key()).decrypt(nonce, ciphertext, self._aad(user_id, provider)).decode()
        except (InvalidTag, ValueError, UnicodeDecodeError) as exc:
            raise SemanticCredentialEncryptionError("The saved semantic credential cannot be opened with the current encryption configuration.") from exc
        return value

    def _key(self) -> bytes:
        value = self._encoded_key
        if not isinstance(value, str) or not value.strip():
            raise SemanticCredentialEncryptionError("Per-user semantic credential storage is not configured.")
        normalized = value.strip()
        try:
            key = base64.b64decode(normalized + "=" * (-len(normalized) % 4), altchars=b"-_", validate=True)
        except (binascii.Error, ValueError) as exc:
            raise SemanticCredentialEncryptionError("Per-user semantic credential storage is not configured correctly.") from exc
        if len(key) != 32:
            raise SemanticCredentialEncryptionError("Per-user semantic credential storage is not configured correctly.")
        return key

    @classmethod
    def _aad(cls, user_id: str, provider: str) -> bytes:
        return f"career-trans:semantic-credential:user:{user_id}:provider:{provider}:v{cls._version}".encode()
