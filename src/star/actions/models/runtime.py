"""Runtime execution models for STAR actions."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import Mapping
from uuid import UUID

from star.actions.models.security import CommandTokenSource, InvocationTokenRole


@dataclass(frozen=True, slots=True)
class ActionExecutionResult:
    """Execution result returned by the STAR runtime executor.

    Attributes:
        returncode: Process return code.
        stdout: Raw stdout bytes.
        stderr: Raw stderr bytes.
        exec_time: Total execution time in seconds.
        pid: Process identifier when available.
    """

    returncode: int
    stdout: bytes
    stderr: bytes
    exec_time: float
    pid: int | None = None


@dataclass(frozen=True, slots=True)
class ActionExecutionOutput:
    """Sanitized execution result safe for external exposure.

    Attributes:
        returncode: Process return code.
        stdout: Sanitized stdout bytes.
        stderr: Sanitized stderr bytes.
        exec_time: Total execution time in seconds.
        pid: Process identifier when available.
        truncated: Whether stdout or stderr was truncated.
        redacted: Whether output redaction was applied.
    """

    returncode: int
    stdout: bytes
    stderr: bytes
    exec_time: float
    pid: int | None

    truncated: bool
    redacted: bool


@dataclass(frozen=True, slots=True)
class RenderedArgvToken:
    """One immutable rendered argv token with retained origin metadata.

    Attributes:
        value: Final string passed to the subprocess.
        template_index: Command-template position that produced the token.
        source: Structural source of the rendered value.
        role: Extension policy role, or ``None`` for an unconstrained core token.
        reference: Referenced arg, flag, or output name when applicable.
        template_references: Ordered const-template placeholder names.
        managed_file_id: Managed input or output UUID when applicable.
    """

    value: str = field(repr=False)
    template_index: int
    source: CommandTokenSource
    role: InvocationTokenRole | None = None
    reference: str | None = None
    template_references: tuple[str, ...] = ()
    managed_file_id: UUID | None = None


@dataclass(frozen=True, slots=True)
class RenderedAction:
    """Rendered action state produced before command execution.

    Attributes:
        tokens: Canonical typed argv tokens passed to executor.
        output_files: Mapping of output name to STAR file id for `file + command`.
        stdin_data: Optional bytes written to subprocess stdin.
        secret_redactions: Secret values that must be redacted from output.
        secret_files: Ephemeral secret file paths owned by this invocation.
    """

    tokens: tuple[RenderedArgvToken, ...]
    output_files: Mapping[str, UUID]
    stdin_data: bytes | None = field(default=None, repr=False, compare=False)
    secret_redactions: tuple[str, ...] = field(
        default_factory=tuple,
        repr=False,
        compare=False,
    )
    secret_files: tuple[Path, ...] = field(
        default_factory=tuple,
        repr=False,
        compare=False,
    )

    def __post_init__(self) -> None:
        """Freeze a defensive copy of invocation-owned output identities."""

        object.__setattr__(self, "tokens", tuple(self.tokens))
        object.__setattr__(
            self,
            "output_files",
            MappingProxyType(dict(self.output_files)),
        )

    @property
    def argv(self) -> list[str]:
        """Return a compatibility list derived from canonical typed tokens."""

        return [token.value for token in self.tokens]

    def __iter__(self):
        """Iterate over argv tokens for backward-compatible list semantics."""

        return iter(self.argv)

    def __len__(self) -> int:
        """Return argv token count for backward-compatible list semantics."""

        return len(self.argv)

    def __getitem__(self, index: int) -> str:
        """Return one argv token by index for backward-compatible access."""

        return self.argv[index]

    def __eq__(self, other: object) -> bool:
        """Compare with another RenderedAction or a plain argv-like list."""

        if isinstance(other, RenderedAction):
            return (
                self.tokens == other.tokens and self.output_files == other.output_files
            )
        if isinstance(other, list):
            return self.argv == other
        return False
