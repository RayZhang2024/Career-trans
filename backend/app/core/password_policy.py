"""Authoritative policy for passwords that are newly established."""

from dataclasses import dataclass


PASSWORD_POLICY_VERSION = 1
PASSWORD_MIN_LENGTH = 15
PASSWORD_MAX_LENGTH = 128
PASSWORD_COMPOSITION_REQUIREMENTS: tuple[str, ...] = ()

# This is an intentionally small local control, not a complete breach corpus.
# Entries are compared against the entire password using Unicode case folding.
_COMMON_PASSWORDS = frozenset(
    {
        "password",
        "password123",
        "password123456",
        "password1234567",
        "123456789012345",
        "qwertyuiopasdfg",
        "letmein12345678",
        "welcome12345678",
        "admin123456789",
        "iloveyou123456",
    }
)


@dataclass(frozen=True)
class PasswordPolicy:
    version: int
    min_length: int
    max_length: int
    common_passwords_rejected: bool
    composition_requirements: tuple[str, ...]


PASSWORD_POLICY = PasswordPolicy(
    version=PASSWORD_POLICY_VERSION,
    min_length=PASSWORD_MIN_LENGTH,
    max_length=PASSWORD_MAX_LENGTH,
    common_passwords_rejected=True,
    composition_requirements=PASSWORD_COMPOSITION_REQUIREMENTS,
)


class PasswordPolicyError(ValueError):
    """A safe, stable reason code for rejecting a newly established password."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def validate_new_password(password: str) -> None:
    """Validate a new password without trimming or changing its contents.

    Python's ``len`` counts Unicode code points, matching the public policy
    contract. Existing password verification deliberately does not call here.
    """
    length = len(password)
    if length < PASSWORD_POLICY.min_length:
        raise PasswordPolicyError("password_too_short")
    if length > PASSWORD_POLICY.max_length:
        raise PasswordPolicyError("password_too_long")
    if password.casefold() in _COMMON_PASSWORDS:
        raise PasswordPolicyError("password_too_common")
