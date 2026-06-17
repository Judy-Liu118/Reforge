"""Sandbox execution backends.

The SandboxBackend Protocol decouples code execution mechanism from the
SandboxExecutor facade. Default backend is subprocess (fast, zero deps).
Docker backend (opt-in) provides filesystem/network/cpu/memory isolation.
"""

from reforge.runtime.infrastructure.execution.backends.docker_backend import (
    DockerBackend,
    DockerUnavailableError,
)
from reforge.runtime.infrastructure.execution.backends.protocol import SandboxBackend
from reforge.runtime.infrastructure.execution.backends.subprocess_backend import (
    SubprocessBackend,
)

__all__ = [
    "DockerBackend",
    "DockerUnavailableError",
    "SandboxBackend",
    "SubprocessBackend",
]
