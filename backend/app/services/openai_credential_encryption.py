"""Authenticated encryption for per-user OpenAI credentials at rest."""

import base64
import binascii
import os

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM


class OpenAICredentialEncryptionError(RuntimeError):
    """Safe failure without credential or ciphertext details."""


class OpenAICredentialEncryption:
    _version = 1

    def __init__(self, encoded_key: str | None) -> None:
        self._encoded_key = encoded_key

    def configured(self) -> bool:
        try:
            self._key()
        except OpenAICredentialEncryptionError:
            return False
        return True

    def encrypt(self, user_id: str, credential: str) -> tuple[bytes, bytes, int]:
        nonce = os.urandom(12)
        ciphertext = AESGCM(self._key()).encrypt(nonce, credential.encode(), self._aad(user_id))
        return nonce, ciphertext, self._version

    def decrypt(self, user_id: str, nonce: bytes, ciphertext: bytes, version: int) -> str:
        if version != self._version:
            raise OpenAICredentialEncryptionError("The saved OpenAI credential cannot be opened with the current encryption configuration.")
        try:
            value = AESGCM(self._key()).decrypt(nonce, ciphertext, self._aad(user_id)).decode()
        except (InvalidTag, ValueError, UnicodeDecodeError) as exc:
            raise OpenAICredentialEncryptionError("The saved OpenAI credential cannot be opened with the current encryption configuration.") from exc
        return value

    def _key(self) -> bytes:
        value = self._encoded_key
        if not isinstance(value, str) or not value.strip():
            raise OpenAICredentialEncryptionError("Per-user OpenAI credential storage is not configured.")
        normalized = value.strip()
        try:
            key = base64.b64decode(normalized + "=" * (-len(normalized) % 4), altchars=b"-_", validate=True)
        except (binascii.Error, ValueError) as exc:
            raise OpenAICredentialEncryptionError("Per-user OpenAI credential storage is not configured correctly.") from exc
        if len(key) != 32:
            raise OpenAICredentialEncryptionError("Per-user OpenAI credential storage is not configured correctly.")
        return key

    @classmethod
    def _aad(cls, user_id: str) -> bytes:
        return f"career-trans:openai:user:{user_id}:v{cls._version}".encode()
