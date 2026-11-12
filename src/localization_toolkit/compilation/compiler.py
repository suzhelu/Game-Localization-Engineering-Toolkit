"""Compiler adapters: safe argv-based local execution and an offline mock."""
from __future__ import annotations

import shutil
import os
import subprocess
import time
from pathlib import Path
from typing import Callable, Sequence

from .models import CompilerExecution, CompilerRequest, CompilerVersion


class CompilerAdapterError(RuntimeError):
    pass


def _bounded(text: str | bytes | None, limit: int = 64_000) -> str:
    if text is None:
        return ""
    if isinstance(text, bytes):
        text = text.decode("utf-8", errors="replace")
    return text[-limit:]


class CommandLineCompilerAdapter:
    """Invoke a caller-installed tool with an argv list; never uses a shell.

    Arguments may contain `{input}` and `{output}` substitutions. The values
    are individual argument text, not shell fragments. Version probing is an
    explicit separate command and the caller decides how the version string is
    interpreted.
    """

    def __init__(
        self,
        executable: str | Path,
        arguments: Sequence[str],
        *,
        version_arguments: Sequence[str],
        compiler_id: str,
        cwd: str | Path | None = None,
    ) -> None:
        self.executable = str(executable)
        self.arguments = self._validate_arguments(arguments)
        self.version_arguments = self._validate_arguments(version_arguments)
        if not compiler_id or not compiler_id.strip():
            raise ValueError("compiler_id is required")
        self._compiler_id = compiler_id.strip()
        self.cwd = Path(cwd).resolve() if cwd is not None else None

    @staticmethod
    def _validate_arguments(arguments: Sequence[str]) -> tuple[str, ...]:
        if isinstance(arguments, (str, bytes)) or not arguments:
            raise ValueError("Compiler arguments must be a non-empty sequence of strings")
        result = tuple(arguments)
        if any(not isinstance(value, str) or "\0" in value for value in result):
            raise ValueError("Compiler arguments must be NUL-free strings")
        import re
        unknown = [match.group(0) for arg in result for match in re.finditer(r"\{[^{}]*\}", arg)
                   if match.group(0) not in {"{input}", "{output}"}]
        if unknown:
            raise ValueError(f"Unsupported compiler argument placeholder: {unknown[0]}")
        return result

    @property
    def compiler_id(self) -> str:
        return self._compiler_id

    def _resolve_executable(self) -> str:
        found = shutil.which(self.executable)
        if found:
            return found
        path = Path(self.executable)
        if path.is_file():
            return str(path.resolve())
        raise CompilerAdapterError(f"Compiler executable was not found: {self.executable}")

    def get_version(self, timeout_seconds: float) -> CompilerVersion:
        executable = self._resolve_executable()
        try:
            completed = subprocess.run(
                [executable, *self.version_arguments],
                shell=False,
                cwd=self.cwd,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=timeout_seconds,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise CompilerAdapterError("Compiler version probe timed out") from exc
        except OSError as exc:
            raise CompilerAdapterError(f"Compiler version probe failed: {exc}") from exc
        output = _bounded(completed.stdout).strip() or _bounded(completed.stderr).strip()
        if completed.returncode != 0 or not output:
            raise CompilerAdapterError(f"Compiler version probe failed with exit code {completed.returncode}")
        first_line = output.splitlines()[0].strip()
        return CompilerVersion(self.compiler_id, first_line)

    def compile(self, request: CompilerRequest, timeout_seconds: float) -> CompilerExecution:
        executable = self._resolve_executable()
        input_path = os.path.abspath(os.fspath(request.input_path))
        output_path = os.path.abspath(os.fspath(request.output_path))
        argv = [executable, *(arg.replace("{input}", input_path).replace("{output}", output_path)
                              for arg in self.arguments)]
        started = time.monotonic()
        try:
            completed = subprocess.run(
                argv,
                shell=False,
                cwd=self.cwd,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=timeout_seconds,
                check=False,
            )
            return CompilerExecution(
                command_label=self.compiler_id,
                exit_code=completed.returncode,
                timed_out=False,
                stdout=_bounded(completed.stdout),
                stderr=_bounded(completed.stderr),
                duration_seconds=time.monotonic() - started,
            )
        except subprocess.TimeoutExpired as exc:
            return CompilerExecution(
                command_label=self.compiler_id,
                exit_code=None,
                timed_out=True,
                stdout=_bounded(exc.stdout),
                stderr=_bounded(exc.stderr),
                duration_seconds=time.monotonic() - started,
            )
        except OSError as exc:
            return CompilerExecution(
                command_label=self.compiler_id,
                exit_code=None,
                timed_out=False,
                stdout="",
                stderr=_bounded(str(exc)),
                duration_seconds=time.monotonic() - started,
            )


CompileFunction = Callable[[CompilerRequest], tuple[int, str, str]]


class MockCompilerAdapter:
    """Offline adapter for synthetic build tests and examples only."""

    compiler_id = "synthetic-mock-compiler"

    def __init__(self, version: str = "mock-1", compile_function: CompileFunction | None = None) -> None:
        self.version = version
        self.compile_function = compile_function or self._copy_input

    @staticmethod
    def _copy_input(request: CompilerRequest) -> tuple[int, str, str]:
        request.output_path.write_bytes(request.input_path.read_bytes())
        return 0, "synthetic compile completed", ""

    def get_version(self, timeout_seconds: float) -> CompilerVersion:
        return CompilerVersion(self.compiler_id, self.version)

    def compile(self, request: CompilerRequest, timeout_seconds: float) -> CompilerExecution:
        started = time.monotonic()
        try:
            code, stdout, stderr = self.compile_function(request)
            return CompilerExecution(self.compiler_id, code, False, stdout, stderr, time.monotonic() - started)
        except TimeoutError as exc:
            return CompilerExecution(self.compiler_id, None, True, "", _bounded(str(exc)), time.monotonic() - started)
        except Exception as exc:  # synthetic failures are surfaced in the audit report
            return CompilerExecution(self.compiler_id, 1, False, "", _bounded(f"{type(exc).__name__}: {exc}"), time.monotonic() - started)
