"""MCP presentation helpers for typed desktop results."""

from __future__ import annotations

import base64
import struct
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from fastmcp.tools.base import ToolResult
from mcp.types import ContentBlock, ImageContent, TextContent

from .capabilities import Capability, CapabilitySnapshot, CapabilityState
from .errors import DesktopError, ErrorCategory, ErrorCode, error_from_decision


PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


def shot_image(shot: Any, enhance: bool = False) -> ImageContent:
    """The image block for one published shot, checked against its record.

    A client acts on this picture through `shot=`, which maps its pixels with
    the recorded scale. So the block must be a PNG with exactly the recorded
    scaled size; anything else is a delivery failure, raised here rather than
    handing over a picture whose pixels no longer mean what the record says.

    `enhance` (step 8.5) brightens and stretches a dark frame in the copy sent
    to the client only; the file on disk stays raw, and the size check covers
    the enhanced picture too.
    """
    label = getattr(shot, "id", "") or str(getattr(shot, "path", ""))
    try:
        data = Path(shot.path).read_bytes()
    except OSError as exc:
        raise _undelivered(label, f"the PNG could not be read ({exc.strerror or exc})") from exc
    if len(data) < 24 or data[:8] != PNG_SIGNATURE or data[12:16] != b"IHDR":
        raise _undelivered(label, "the file is not a valid PNG")
    width, height = struct.unpack(">II", data[16:24])
    expected = tuple(shot.scaled)
    if (width, height) != expected:
        raise _undelivered(
            label,
            f"PNG {width}x{height}, kayit {expected[0]}x{expected[1]} diyor",
        )
    if enhance:
        from . import capture as capturelib

        try:
            data, _stats = capturelib.enhanced_png(Path(shot.path))
        except (OSError, capturelib.CaptureError) as exc:
            raise _undelivered(label, f"iyilestirilemedi ({exc})") from exc
        if struct.unpack(">II", data[16:24]) != expected:
            raise _undelivered(label, "the enhancement changed the size")
    return ImageContent(
        type="image",
        data=base64.b64encode(data).decode("ascii"),
        mimeType="image/png",
    )


def _undelivered(label: str, reason: str) -> DesktopError:
    return DesktopError(
        code=ErrorCode.IMAGE_DELIVERY_FAILED,
        message=f"{label}: {reason}",
        category=ErrorCategory.CAPTURE,
        retryable=True,
        suggested_action=(
            "Take a fresh screenshot; do not act on coordinates from this one."
        ),
        permission_scope="os.capture",
        backend="pcbridge.mcp",
    )


def undelivered_error(errors: Sequence[DesktopError]) -> DesktopError:
    """One typed error naming every image that did not reach the client."""
    if len(errors) == 1:
        return errors[0]
    first = errors[0]
    return DesktopError(
        code=first.code,
        message="; ".join(error.message for error in errors),
        category=first.category,
        retryable=first.retryable,
        suggested_action=first.suggested_action,
        permission_scope=first.permission_scope,
        backend=first.backend,
    )


def desktop_error_result(
    error: DesktopError,
    *,
    text: str | None = None,
    permission_scope: str | None = None,
    extra: dict[str, Any] | None = None,
    content: list[ContentBlock] | None = None,
) -> ToolResult:
    """Keep readable content while attaching a stable desktop error object."""
    error_data = error.to_dict()
    if permission_scope and "permission_scope" not in error_data:
        error_data["permission_scope"] = permission_scope
    structured: dict[str, Any] = {
        "type": "pcbridge.desktop",
        "error": error_data,
    }
    if extra:
        structured.update(extra)
    blocks = content if content is not None else [
        TextContent(type="text", text=text if text is not None else error.message)
    ]
    return ToolResult(
        content=blocks,
        structured_content=structured,
        is_error=True,
    )


def decision_error(decision: Any) -> DesktopError:
    """Turn a typed SafetyGate decision into the common desktop taxonomy."""
    return error_from_decision(decision)


def capability_error(
    capability: Capability | None,
    *,
    message: str,
    scope: str,
    backend: str | None = None,
) -> DesktopError:
    """Build an error from probe evidence without classifying human text."""
    state = capability.state if capability else CapabilityState.UNAVAILABLE
    code = capability.reason_code if capability else None
    permission = state == CapabilityState.PERMISSION_REQUIRED or code in {
        ErrorCode.PERMISSION_REQUIRED,
        ErrorCode.PERMISSION_DENIED,
        ErrorCode.DEVICE_NOT_GRANTED,
    }
    if state == CapabilityState.UNSUPPORTED:
        action = "Use a supported desktop capability."
    elif permission:
        action = f"Grant the required {scope} permission and retry."
    else:
        action = "Restore the desktop backend and retry."
    return DesktopError(
        code=code or (
            ErrorCode.UNSUPPORTED
            if state == CapabilityState.UNSUPPORTED
            else ErrorCode.BACKEND_UNAVAILABLE
        ),
        message=message,
        category=ErrorCategory.PERMISSION if permission else ErrorCategory.CAPABILITY,
        retryable=state != CapabilityState.UNSUPPORTED,
        suggested_action=action,
        permission_scope=scope,
        backend=backend or (capability.backend if capability else None),
    )


def execution_error(
    error: Exception,
    *,
    category: ErrorCategory,
    scope: str,
    backend: str | None = None,
) -> DesktopError:
    """Wrap a legacy provider exception at the MCP presentation boundary."""
    if isinstance(error, DesktopError):
        return error
    return DesktopError(
        code=ErrorCode.EXECUTION_UNKNOWN,
        message=str(error),
        category=category,
        retryable=False,
        suggested_action="Inspect the desktop state before retrying the operation.",
        permission_scope=scope,
        backend=backend,
    )


def capabilities_result(snapshot: CapabilitySnapshot) -> ToolResult:
    """Present a side-effect-free capability snapshot for humans and agents."""
    lines = ["**Desktop capabilities**", ""]
    for name, value in sorted(snapshot.capabilities.items()):
        line = f"- `{name}`: {value.state.value} (`{value.backend}`)"
        if value.reason_code:
            line += f" · {value.reason_code.value}"
        lines.append(line)
    authorization = snapshot.authorization
    lines += [
        "",
        "**Yetkilendirme**",
        f"- configuration: {'enabled' if authorization.desktop_enabled else 'disabled'}",
        f"- grant: {authorization.grant_remaining_seconds} s left",
        f"- screen lock: {authorization.screen_lock_state}",
    ]
    return ToolResult(
        content=[TextContent(type="text", text="\n".join(lines))],
        structured_content={
            "type": "pcbridge.desktop.capabilities",
            **snapshot.as_dict(),
        },
    )


__all__ = [
    "capabilities_result",
    "capability_error",
    "decision_error",
    "desktop_error_result",
    "execution_error",
    "shot_image",
    "undelivered_error",
]
