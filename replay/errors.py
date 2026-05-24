class ReplayError(RuntimeError):
    """Base class for replay framework errors."""


class ReplayNotInstalledError(ReplayError):
    """Raised when a replay context is used before install()."""


class ReplayMissError(ReplayError):
    """Raised when strict replay cannot find a matching historical record."""


class AmbiguousReplayError(ReplayError):
    """Raised when replay matching cannot uniquely identify a historical record."""


class InvalidBreakpointError(ReplayError):
    """Raised when a breakpoint record uid is missing or does not point to an LLM record."""


class ToolSerializationError(ReplayError):
    """Raised when a tool input or output cannot be safely recorded for replay."""


class LlmCodecError(ReplayError):
    """Raised when an LLM boundary output cannot be serialized or reconstructed."""


class UnsupportedOverrideInputError(ReplayError):
    """Raised when an override shape is unsupported for the recorded LLM codec."""


class ReplayedToolError(ReplayError):
    """Raised when replaying a tool call that historically failed."""

    def __init__(
        self,
        *,
        tool_name: str | None,
        record_uid: str | None,
        original_type: str | None,
        message: str | None,
        original_repr: str | None,
    ) -> None:
        self.tool_name = tool_name
        self.record_uid = record_uid
        self.original_type = original_type
        self.message = message
        self.original_repr = original_repr
        super().__init__(
            "Replayed tool error"
            f" tool_name={tool_name!r}"
            f" record_uid={record_uid!r}"
            f" original_type={original_type!r}"
            f" message={message!r}"
        )


class UnsupportedStreamingError(ReplayError):
    """Raised when stream=True is used in the first-stage demo."""


class FilesystemCaptureError(ReplayError):
    """Raised when filesystem effects cannot be captured or applied."""


class FilesystemSandboxEscapeError(FilesystemCaptureError):
    """Raised when a captured filesystem path escapes the configured sandbox."""


class FilesystemReplayConflictError(FilesystemCaptureError):
    """Raised when replay would overwrite filesystem state that has diverged."""


class SandboxError(ReplayError):
    """Raised when a managed sandbox cannot be prepared."""


class SandboxSafetyError(SandboxError):
    """Raised when sandbox preparation would touch an unsafe path."""
