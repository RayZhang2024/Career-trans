"""Safe, bounded classification for structured ATS acquisition failures."""

import json
import socket
from urllib.error import HTTPError, URLError

from pydantic import ValidationError

from app.schemas.structured_ats_discovery import StructuredAtsFailureKind


class StructuredAtsSourceConfigurationError(RuntimeError):
    """Marks the known provider-factory configuration boundary without exposing detail."""


def classify_structured_ats_failure(exc: Exception) -> StructuredAtsFailureKind:
    """Classify exception types only; never surface exception text or response content."""
    if isinstance(exc, (TimeoutError, socket.timeout)):
        return StructuredAtsFailureKind.TIMEOUT
    if isinstance(exc, HTTPError):
        return StructuredAtsFailureKind.HTTP_FAILURE
    if isinstance(exc, URLError):
        if isinstance(exc.reason, (TimeoutError, socket.timeout)):
            return StructuredAtsFailureKind.TIMEOUT
        return StructuredAtsFailureKind.CONNECTION_FAILURE
    if isinstance(exc, (json.JSONDecodeError, ValidationError)):
        return StructuredAtsFailureKind.PARSE_FAILURE
    if isinstance(exc, StructuredAtsSourceConfigurationError):
        return StructuredAtsFailureKind.CONFIGURATION_FAILURE
    if isinstance(exc, (ValueError, TypeError, KeyError)):
        return StructuredAtsFailureKind.PROVIDER_FAILURE
    if isinstance(exc, (ConnectionError, OSError)):
        return StructuredAtsFailureKind.CONNECTION_FAILURE
    return StructuredAtsFailureKind.UNKNOWN
