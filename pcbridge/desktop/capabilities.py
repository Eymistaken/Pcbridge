"""Typed desktop capability snapshots and evidence-aware caching."""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import Enum
import threading
from typing import Any, Callable, Hashable

from .errors import ErrorCode


class CapabilityState(str, Enum):
    SUPPORTED = "supported"
    UNSUPPORTED = "unsupported"
    UNAVAILABLE = "unavailable"
    PERMISSION_REQUIRED = "permission_required"
    DEGRADED = "degraded"


class CapabilityEvidence(str, Enum):
    PROBE = "probe"
    OPERATION = "operation"


@dataclass(frozen=True)
class Capability:
    name: str
    state: CapabilityState
    backend: str
    scope: str
    reason_code: ErrorCode | None
    limitations: tuple[str, ...]
    observed_at: float
    evidence: CapabilityEvidence
    usable_now: bool

    def as_dict(self) -> dict[str, Any]:
        return {
            "state": self.state.value,
            "backend": self.backend,
            "scope": self.scope,
            "reason_code": self.reason_code.value if self.reason_code else None,
            "limitations": list(self.limitations),
            "observed_at": self.observed_at,
            "evidence": self.evidence.value,
            "usable_now": self.usable_now,
        }


@dataclass(frozen=True)
class AuthorizationStatus:
    desktop_enabled: bool
    grant_remaining_seconds: int
    hard_remaining_seconds: int
    screen_lock_state: str
    revoke_epoch: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "desktop_enabled": self.desktop_enabled,
            "grant_remaining_seconds": self.grant_remaining_seconds,
            "hard_remaining_seconds": self.hard_remaining_seconds,
            "screen_lock_state": self.screen_lock_state,
            "revoke_epoch": self.revoke_epoch,
        }


@dataclass(frozen=True)
class CapabilitySnapshot:
    capabilities: dict[str, Capability]
    last_operations: dict[str, Capability]
    authorization: AuthorizationStatus

    def as_dict(self) -> dict[str, Any]:
        return {
            "capabilities": {
                name: value.as_dict()
                for name, value in sorted(self.capabilities.items())
            },
            "last_operations": {
                name: value.as_dict()
                for name, value in sorted(self.last_operations.items())
            },
            "authorization": self.authorization.as_dict(),
        }


class CapabilityRegistry:
    """Cache probes while retaining operation evidence in a separate map."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._token: Hashable | None = None
        self._probes: dict[str, Capability] = {}
        self._operations: dict[str, Capability] = {}

    def snapshot(
        self,
        *,
        token: Hashable,
        probe: Callable[[], dict[str, Capability]],
        authorization: AuthorizationStatus,
    ) -> CapabilitySnapshot:
        with self._lock:
            if self._token != token:
                self._token = token
                self._probes = probe()
                self._operations.clear()
            return CapabilitySnapshot(
                capabilities=dict(self._probes),
                last_operations=dict(self._operations),
                authorization=authorization,
            )

    def record_operation(self, capability: Capability) -> None:
        with self._lock:
            self._operations[capability.name] = replace(
                capability,
                evidence=CapabilityEvidence.OPERATION,
            )

    def invalidate(self) -> None:
        with self._lock:
            self._token = None
            self._probes.clear()
            self._operations.clear()


__all__ = [
    "AuthorizationStatus",
    "Capability",
    "CapabilityEvidence",
    "CapabilityRegistry",
    "CapabilitySnapshot",
    "CapabilityState",
]
