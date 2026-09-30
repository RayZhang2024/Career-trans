"""AES-GCM encryption for per-user Tavily credentials at rest."""

import base64
import binascii
import os

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM


class TavilyCredentialEncryptionError(RuntimeError):
    """Safe failure for unavailable, invalid, or unreadable credential encryption."""


class TavilyCredentialEncryption:
    _version = 1

    def __init__(self, encoded_key: str | None) -> None:
        self._encoded_key = encoded_key

    def configured(self) -> bool:
        try:
            self._key()
        except TavilyCredentialEncryptionError:
            return False
        return True

    def encrypt(self, user_id: str, credential: str) -> tuple[bytes, bytes, int]:
        key = self._key()
        nonce = os.urandom(12)
        ciphertext = AESGCM(key).encrypt(
            nonce, credential.encode("utf-8"), self._associated_data(user_id, self._version)
        )
        return nonce, ciphertext, self._version

    def decrypt(self, user_id: str, nonce: bytes, ciphertext: bytes, version: int) -> str:
        if version != self._version:
            raise TavilyCredentialEncryptionError(
                "The saved Tavily credential cannot be read with the current encryption configuration."
            )
        try:
            plaintext = AESGCM(self._key()).decrypt(
                nonce, ciphertext, self._associated_data(user_id, version)
            )
            return plaintext.decode("utf-8")
        except (InvalidTag, ValueError, UnicodeDecodeError) as exc:
            raise TavilyCredentialEncryptionError(
                "The saved Tavily credential cannot be read with the current encryption configuration."
            ) from exc

    def _key(self) -> bytes:
        value = self._encoded_key
        if not isinstance(value, str) or not value.strip():
            raise TavilyCredentialEncryptionError(
                "Per-user Tavily credential storage is not configured."
            )
        normalized = value.strip()
        padded = normalized + ("=" * (-len(normalized) % 4))
        try:
            key = base64.b64decode(padded, altchars=b"-_", validate=True)
        except (binascii.Error, ValueError) as exc:
            raise TavilyCredentialEncryptionError(
                "Per-user Tavily credential storage is not configured correctly."
            ) from exc
        if len(key) != 32:
            raise TavilyCredentialEncryptionError(
                "Per-user Tavily credential storage is not configured correctly."
            )
        return key

    @staticmethod
    def _associated_data(user_id: str, version: int) -> bytes:
        return f"career-trans:tavily:user:{user_id}:v{version}".encode("utf-8")
