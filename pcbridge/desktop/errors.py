"""Stable error taxonomy for desktop provider boundaries."""

from __future__ import annotations

from enum import Enum
from typing import Any


class ErrorCategory(str, Enum):
    SAFETY = "safety"
    PERMISSION = "permission"
    CAPABILITY = "capability"
    CAPTURE = "capture"
    COORDINATE = "coordinate"
    ACCESSIBILITY = "accessibility"
    EXECUTION = "execution"
    IPC = "ipc"


class ErrorCode(str, Enum):
    DESKTOP_DISABLED = "DESKTOP_DISABLED"
    GRANT_REQUIRED = "GRANT_REQUIRED"
    GRANT_EXPIRED = "GRANT_EXPIRED"
    REVOKED = "REVOKED"
    SCREEN_LOCKED = "SCREEN_LOCKED"
    LOCK_STATE_UNKNOWN = "LOCK_STATE_UNKNOWN"
    USER_ACTIVE = "USER_ACTIVE"
    ACTIVITY_UNKNOWN = "ACTIVITY_UNKNOWN"
    RATE_LIMITED = "RATE_LIMITED"

    PERMISSION_REQUIRED = "PERMISSION_REQUIRED"
    PERMISSION_DENIED = "PERMISSION_DENIED"
    DEVICE_NOT_GRANTED = "DEVICE_NOT_GRANTED"

    UNSUPPORTED = "UNSUPPORTED"
    BACKEND_UNAVAILABLE = "BACKEND_UNAVAILABLE"
    DEPENDENCY_MISSING = "DEPENDENCY_MISSING"

    FRAME_TIMEOUT = "FRAME_TIMEOUT"
    STALE_FRAME = "STALE_FRAME"
    FRAME_FORMAT_UNSUPPORTED = "FRAME_FORMAT_UNSUPPORTED"
    FRAME_TOO_LARGE = "FRAME_TOO_LARGE"
    DISPLAY_CHANGED = "DISPLAY_CHANGED"
    DISPLAY_MAPPING_UNKNOWN = "DISPLAY_MAPPING_UNKNOWN"
    # The capture succeeded but its image did not reach the client intact.
    IMAGE_DELIVERY_FAILED = "IMAGE_DELIVERY_FAILED"

    SHOT_NOT_FOUND = "SHOT_NOT_FOUND"
    SHOT_INVALID = "SHOT_INVALID"
    SHOT_STALE = "SHOT_STALE"
    AMBIGUOUS_COORDINATE = "AMBIGUOUS_COORDINATE"

    PASSWORD_FIELD = "PASSWORD_FIELD"
    CONFIRMATION_REQUIRED = "CONFIRMATION_REQUIRED"

    TARGET_MISMATCH = "TARGET_MISMATCH"
    ELEMENT_STALE = "ELEMENT_STALE"
    ELEMENT_AMBIGUOUS = "ELEMENT_AMBIGUOUS"
    ACTION_UNSUPPORTED = "ACTION_UNSUPPORTED"
    # Task 6.3: the field was written but holds other text than was sent
    # (cut short, or changed by the application). Not a stale target.
    TEXT_MISMATCH = "TEXT_MISMATCH"

    TIMEOUT = "TIMEOUT"
    CANCELLED = "CANCELLED"
    EXECUTION_UNKNOWN = "EXECUTION_UNKNOWN"
    BUSY = "BUSY"

    NATIVE_NOT_FOUND = "NATIVE_NOT_FOUND"
    PROTOCOL_MISMATCH = "PROTOCOL_MISMATCH"
    NATIVE_CRASHED = "NATIVE_CRASHED"
    INVALID_FRAME = "INVALID_FRAME"


class DesktopError(RuntimeError):
    """A provider failure with stable machine-readable fields and safe text."""

    def __init__(
        self,
        *,
        code: ErrorCode,
        message: str,
        category: ErrorCategory,
        retryable: bool,
        suggested_action: str,
        permission_scope: str | None = None,
        backend: str | None = None,
        execution_state: str | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.category = category
        self.retryable = bool(retryable)
        self.suggested_action = suggested_action
        self.permission_scope = permission_scope
        self.backend = backend
        self.execution_state = execution_state

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "code": self.code.value,
            "message": self.message,
            "category": self.category.value,
            "retryable": self.retryable,
            "suggested_action": self.suggested_action,
        }
        for name in ("permission_scope", "backend", "execution_state"):
            value = getattr(self, name)
            if value is not None:
                data[name] = value
        return data


def error_from_decision(decision: Any) -> DesktopError:
    """Turn a typed `SafetyGate` decision into the common taxonomy.

    Duck-typed on purpose: contract tests hand in look-alike decisions, and
    this module must not import `safety`.
    """
    return DesktopError(
        code=getattr(decision, "code", None) or ErrorCode.EXECUTION_UNKNOWN,
        message=str(getattr(decision, "reason", "") or ""),
        category=getattr(decision, "category", ErrorCategory.SAFETY),
        retryable=bool(getattr(decision, "retryable", False)),
        suggested_action=(
            getattr(decision, "suggested_action", "")
            or "Review the desktop authorization state and retry."
        ),
        permission_scope=getattr(decision, "permission_scope", None),
    )


__all__ = ["DesktopError", "ErrorCategory", "ErrorCode", "error_from_decision"]
