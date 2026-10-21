"""Candidate-test execution behind an explicit, inspectable sandbox policy.

Three tiers exist and are never conflated:

``disabled``
    The default. Candidate tests are generated and stored with
    ``execution_disabled`` and a reason. No process is started and no result is
    invented.

``subprocess``
    Explicit opt-in on the host (``ALLOW_LOCAL_TEST_EXECUTION=true``). It applies a
    timeout, a sanitised environment, a fresh working directory and best-effort
    POSIX resource limits. **It is not a security boundary**: generated code can
    still read the host filesystem and reach the network. The API and every stored
    run say so.

``docker``
    A container with the repository mounted read-only, no network by default,
    dropped capabilities, a read-only root filesystem, a tmpfs and memory/CPU/PID
    limits. The configured image must contain ``pytest`` and the target project's
    runtime dependencies. If the daemon or image is unavailable the run is marked
    ``execution_disabled`` with the probe error; it is never reported as passed.

Results are written only after an actual process has been started and its exit
status collected. ``executed_at`` is therefore evidence, not a timestamp for
"generation happened".
"""

from __future__ import annotations

import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import uuid
from contextlib import suppress
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from patchlens.core.config import Settings
from patchlens.core.enums import TestCandidateStatus
from patchlens.core.logging import describe_exception, get_logger, log_extra, redact
from patchlens.core.security import is_safe_relative_path, resolve_within, safe_subprocess_env
from patchlens.db.models.repository import Repository
from patchlens.db.models.testing import TestCandidate

_resource: Any
try:  # POSIX only; the project targets macOS/Linux
    import resource as _resource
except ImportError:  # pragma: no cover - Windows is not a supported deployment target
    _resource = None

#: ``Any`` so the optional import does not make the fallback branch look unreachable.
resource: Any = _resource

logger = get_logger(__name__)

PYTEST_ARGS = ("-q", "--disable-warnings", "--maxfail=1", "-p", "no:cacheprovider")
MAX_OUTPUT_TAIL_BYTES = 256 * 1024
_COUNT_RE = re.compile(r"(\d+) (passed|failed|error|errors|skipped)")

#: Subprocess mode cannot be made safe by this module; the API repeats this text.
SUBPROCESS_WARNING = (
    "subprocess mode executes generated code on the host with a timeout, a sanitised "
    "environment and best-effort resource limits. It is NOT a security boundary and "
    "must only be enabled for code you trust."
)


@dataclass(frozen=True, slots=True)
class SandboxStatus:
    requested_mode: str
    effective_mode: str
    available: bool
    reason: str | None
    detail: str
    limits: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "requested_mode": self.requested_mode,
            "effective_mode": self.effective_mode,
            "available": self.available,
            "reason": self.reason,
            "detail": self.detail,
            "limits": self.limits,
        }


@dataclass(slots=True)
class ProcessResult:
    command: list[str]
    exit_code: int | None
    timed_out: bool
    duration_ms: int
    stdout_tail: str
    stderr_tail: str
    output_truncated: bool
    spawn_error: str | None = None


@dataclass(slots=True)
class ExecutionOutcome:
    candidate: TestCandidate
    executed: bool
    status: str
    detail: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "candidate_id": self.candidate.id,
            "executed": self.executed,
            "status": self.status,
            "detail": self.detail,
            "sandbox_mode": self.candidate.sandbox_mode,
            "exit_code": self.candidate.exit_code,
        }


def _limits(settings: Settings) -> dict[str, Any]:
    return {
        "timeout_seconds": settings.test_execution_timeout_seconds,
        "cpu_seconds": settings.test_execution_cpu_seconds,
        "memory_mb": settings.test_execution_memory_mb,
        "max_output_bytes": settings.test_execution_max_output_bytes,
        "network_enabled": settings.test_execution_network_enabled,
        "docker_image": settings.test_execution_docker_image,
    }


def resolve_sandbox(settings: Settings) -> SandboxStatus:
    """Resolve the configured tier to what can actually run *now*.

    A Docker probe is performed so an unavailable daemon or missing CLI produces a
    precise reason instead of a failed container that looks like a test failure.
    """
    mode = settings.test_execution_mode
    limits = _limits(settings)
    if mode == "disabled":
        return SandboxStatus(
            requested_mode=mode,
            effective_mode="disabled",
            available=False,
            reason=(
                "test execution is disabled (TEST_EXECUTION_MODE=disabled); candidates will "
                "not be executed until a tier is enabled"
            ),
            detail="enable docker with TEST_EXECUTION_MODE=docker to execute candidates in a container",
            limits=limits,
        )
    if mode == "subprocess":
        if not settings.allow_local_test_execution:
            return SandboxStatus(
                requested_mode=mode,
                effective_mode="disabled",
                available=False,
                reason=(
                    "subprocess execution requires ALLOW_LOCAL_TEST_EXECUTION=true; candidates will "
                    "not be executed until it is acknowledged"
                ),
                detail=SUBPROCESS_WARNING,
                limits=limits,
            )
        return SandboxStatus(
            requested_mode=mode,
            effective_mode="subprocess",
            available=True,
            reason=None,
            detail=SUBPROCESS_WARNING,
            limits=limits,
        )
    # docker
    docker_path = shutil.which("docker")
    if docker_path is None:
        return SandboxStatus(
            requested_mode=mode,
            effective_mode="disabled",
            available=False,
            reason="the docker CLI was not found on PATH; candidate tests were not run",
            detail="install Docker or set TEST_EXECUTION_MODE=disabled with an explicit explanation",
            limits=limits,
        )
    docker_env = safe_subprocess_env()
    if docker_host := os.environ.get("DOCKER_HOST"):
        docker_env["DOCKER_HOST"] = docker_host
    try:
        probe = subprocess.run(  # noqa: S603 - fixed argv, no shell; docker path resolved by which()
            [docker_path, "version", "--format", "{{.Server.Version}}"],
            capture_output=True,
            text=True,
            timeout=8,
            check=False,
            env=docker_env,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return SandboxStatus(
            requested_mode=mode,
            effective_mode="disabled",
            available=False,
            reason=f"Docker probe failed ({type(exc).__name__}: {exc}); candidate tests were not run",
            detail="start the Docker daemon or use an explicit fake runner in tests",
            limits=limits,
        )
    if probe.returncode != 0:
        message = (probe.stderr or probe.stdout or "docker daemon did not respond").strip()
        return SandboxStatus(
            requested_mode=mode,
            effective_mode="disabled",
            available=False,
            reason=f"Docker is not available ({redact(message)[:300]}); candidate tests were not run",
            detail="start the Docker daemon or use an explicit fake runner in tests",
            limits=limits,
        )
    network = "enabled (explicitly configured)" if settings.test_execution_network_enabled else "disabled"
    return SandboxStatus(
        requested_mode=mode,
        effective_mode="docker",
        available=True,
        reason=None,
        detail=(
            f"Docker sandbox, image {settings.test_execution_docker_image!r}, network {network}, "
            "repository mounted read-only, capabilities dropped"
        ),
        limits=limits,
    )


def mark_execution_disabled(
    session: Session,
    candidate: TestCandidate,
    sandbox: SandboxStatus,
    *,
    reason: str | None = None,
) -> ExecutionOutcome:
    """Record that the sandbox was unavailable. This is not a test result."""
    detail = reason or sandbox.reason or "the execution sandbox is unavailable"
    candidate.status = TestCandidateStatus.EXECUTION_DISABLED.value
    candidate.sandbox_mode = sandbox.effective_mode
    candidate.sandbox_detail = sandbox.detail[:2_000]
    candidate.execution_disabled_reason = detail[:2_000]
    candidate.executed_at = None
    candidate.exit_code = None
    candidate.duration_ms = None
    candidate.stdout_tail = None
    candidate.stderr_tail = None
    candidate.output_truncated = False
    candidate.timed_out = False
    candidate.collected_tests = None
    candidate.run_metadata = {"sandbox": sandbox.to_dict()}
    session.add(candidate)
    session.flush()
    logger.warning(
        "candidate test execution disabled",
        extra=log_extra(candidate_id=candidate.id, mode=sandbox.effective_mode),
    )
    return ExecutionOutcome(candidate=candidate, executed=False, status=candidate.status, detail=detail)


def mark_execution_error(candidate: TestCandidate, detail: str, *, mode: str | None = None) -> None:
    """Record an infrastructure/validation error without pretending a run happened."""
    candidate.status = TestCandidateStatus.ERROR.value
    candidate.sandbox_mode = mode
    candidate.sandbox_detail = detail[:2_000]
    candidate.execution_disabled_reason = None
    candidate.exit_code = None
    candidate.duration_ms = None
    candidate.timed_out = False
    candidate.run_metadata = {**(candidate.run_metadata or {}), "error": detail[:2_000]}


def execute_candidate(
    session: Session,
    candidate: TestCandidate,
    settings: Settings,
) -> ExecutionOutcome:
    """Run ``candidate`` in the currently available sandbox, or record why not."""
    repository = session.get(Repository, candidate.repository_id)
    if repository is None:
        mark_execution_error(candidate, "repository row no longer exists")
        session.add(candidate)
        session.flush()
        return ExecutionOutcome(
            candidate, executed=False, status=candidate.status, detail="repository missing"
        )

    sandbox = resolve_sandbox(settings)
    if not sandbox.available:
        return mark_execution_disabled(session, candidate, sandbox)

    validation_error = _validate_execution_input(candidate, settings)
    if validation_error is not None:
        mark_execution_error(candidate, validation_error, mode=sandbox.effective_mode)
        session.add(candidate)
        session.flush()
        return ExecutionOutcome(candidate, executed=False, status=candidate.status, detail=validation_error)

    repository_root = Path(repository.local_path).expanduser() if repository.local_path else None
    if repository_root is None or not repository_root.is_dir():
        detail = "repository has no local checkout; generated code was not executed"
        mark_execution_error(candidate, detail, mode=sandbox.effective_mode)
        session.add(candidate)
        session.flush()
        return ExecutionOutcome(candidate, executed=False, status=candidate.status, detail=detail)

    candidate.status = TestCandidateStatus.RUNNING.value
    candidate.execution_disabled_reason = None
    candidate.sandbox_mode = sandbox.effective_mode
    candidate.sandbox_detail = sandbox.detail[:2_000]
    candidate.run_metadata = {"sandbox": sandbox.to_dict()}
    session.add(candidate)
    session.commit()  # make the running state durable before spawning code

    started = time.perf_counter()
    try:
        with tempfile.TemporaryDirectory(prefix="patchlens-candidate-") as tempdir:
            # macOS exposes /var as a symlink to /private/var; resolve both the
            # root and the generated file before computing the relative pytest path.
            root = Path(tempdir).resolve()
            test_file = _write_candidate_test(root, candidate)
            if sandbox.effective_mode == "subprocess":
                process = _run_subprocess(
                    test_file=test_file,
                    tempdir=root,
                    repository_root=repository_root,
                    settings=settings,
                )
            else:
                process = _run_docker(
                    test_file=test_file,
                    tempdir=root,
                    repository_root=repository_root,
                    settings=settings,
                )
    except Exception as exc:
        detail = f"execution setup failed: {describe_exception(exc)['message']}"
        mark_execution_error(candidate, detail, mode=sandbox.effective_mode)
        candidate.duration_ms = int((time.perf_counter() - started) * 1000)
        session.add(candidate)
        session.flush()
        logger.exception("candidate test setup failed", extra=log_extra(candidate_id=candidate.id))
        return ExecutionOutcome(candidate, executed=False, status=candidate.status, detail=detail)

    _apply_process_result(candidate, process, sandbox)
    session.add(candidate)
    session.flush()
    logger.info(
        "candidate test executed",
        extra=log_extra(
            candidate_id=candidate.id,
            status=candidate.status,
            exit_code=candidate.exit_code,
            duration_ms=candidate.duration_ms,
            mode=sandbox.effective_mode,
        ),
    )
    return ExecutionOutcome(
        candidate=candidate,
        executed=True,
        status=candidate.status,
        detail=candidate.sandbox_detail or "",
    )


def run_test_source(
    *,
    source: str,
    test_path: str,
    repository_root: Path,
    settings: Settings,
) -> ProcessResult:
    """Run generated test source in a fresh temporary directory.

    This is the execution primitive used by the evaluation harness. It applies the
    same subprocess timeout/environment/limits as candidate execution, but does not
    write to the repository or require a ``TestCandidate`` row. Docker evaluation is
    not implemented; callers must check that subprocess mode is explicitly enabled.
    """
    if not is_safe_relative_path(test_path) or not test_path.endswith(".py"):
        raise ValueError("test_path must be a safe relative Python path")
    if not repository_root.is_dir():
        raise ValueError("repository_root must be an existing directory")
    with tempfile.TemporaryDirectory(prefix="patchlens-eval-test-") as tempdir:
        root = Path(tempdir).resolve()
        test_file = resolve_within(root, test_path)
        test_file.parent.mkdir(parents=True, exist_ok=True)
        test_file.write_text(source, encoding="utf-8")
        return _run_subprocess(
            test_file=test_file,
            tempdir=root,
            repository_root=repository_root,
            settings=settings,
        )


def _validate_execution_input(candidate: TestCandidate, settings: Settings) -> str | None:
    if not candidate.source.strip():
        return "candidate has no test source"
    if len(candidate.source) > settings.test_generation_max_test_chars:
        return f"candidate source exceeds the {settings.test_generation_max_test_chars}-character limit"
    if not is_safe_relative_path(candidate.suggested_test_path):
        return "candidate test path is not a safe relative path"
    if not candidate.suggested_test_path.endswith(".py"):
        return "candidate test path is not a Python file"
    if re.search(r"(?m)^\s*(?:async\s+)?def\s+test_\w+\s*\(", candidate.source):
        return None
    return "candidate source contains no pytest test function"


def _write_candidate_test(tempdir: Path, candidate: TestCandidate) -> Path:
    try:
        target = resolve_within(tempdir, candidate.suggested_test_path)
    except Exception as exc:
        raise ValueError(f"unsafe generated test path: {exc}") from exc
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(candidate.source, encoding="utf-8")
    return target


def _run_subprocess(
    *,
    test_file: Path,
    tempdir: Path,
    repository_root: Path,
    settings: Settings,
) -> ProcessResult:
    relative = test_file.relative_to(tempdir).as_posix()
    command = [sys.executable, "-m", "pytest", relative, *PYTEST_ARGS]
    env = safe_subprocess_env(
        {
            "PYTHONPATH": str(repository_root.resolve()),
            "HOME": str(tempdir),
            "TMPDIR": str(tempdir),
            "PYTHONHASHSEED": "0",
            "LC_ALL": "C.UTF-8",
        }
    )
    return _run_process(
        command,
        cwd=tempdir,
        env=env,
        timeout_seconds=settings.test_execution_timeout_seconds,
        max_output_bytes=settings.test_execution_max_output_bytes,
        preexec_fn=_preexec_limits(settings),
    )


def _preexec_limits(settings: Settings) -> Any:
    """Best-effort POSIX limits applied in the child between fork and exec.

    The calls are deliberately tiny and allocation-free. Subprocess mode is an
    explicit development escape hatch, not a sandbox, and this is stated in the
    status endpoint and documentation.
    """
    if resource is None:
        return None
    memory = settings.test_execution_memory_mb * 1024 * 1024
    cpu = settings.test_execution_cpu_seconds
    file_size = max(settings.test_execution_max_output_bytes * 4, 8 * 1024 * 1024)
    nofile = 256

    def apply() -> None:
        for limit, value in (
            (getattr(resource, "RLIMIT_CORE", None), (0, 0)),
            (getattr(resource, "RLIMIT_CPU", None), (cpu, cpu + 1)),
            (getattr(resource, "RLIMIT_AS", None), (memory, memory)),
            (getattr(resource, "RLIMIT_FSIZE", None), (file_size, file_size)),
            (getattr(resource, "RLIMIT_NOFILE", None), (nofile, nofile)),
        ):
            if limit is None:
                continue
            try:
                resource.setrlimit(limit, value)
            except (ValueError, OSError, TypeError):
                # Some hosts do not permit raising/lowering a limit; the timeout and
                # the explicit "not a sandbox" warning still apply.
                continue

    return apply


def build_docker_command(
    *,
    settings: Settings,
    repository_root: Path,
    tempdir: Path,
    test_file: Path,
    name: str | None = None,
) -> list[str]:
    """Build the isolated docker invocation (kept public for inspection/tests)."""
    docker = shutil.which("docker") or "docker"
    relative = test_file.relative_to(tempdir).as_posix()
    network = "bridge" if settings.test_execution_network_enabled else "none"
    container = name or f"patchlens-test-{uuid.uuid4().hex[:12]}"
    return [
        docker,
        "run",
        "--rm",
        "--name",
        container,
        "--network",
        network,
        "--memory",
        f"{settings.test_execution_memory_mb}m",
        "--memory-swap",
        f"{settings.test_execution_memory_mb}m",
        "--cpus",
        "1",
        "--pids-limit",
        "128",
        "--cap-drop",
        "ALL",
        "--security-opt",
        "no-new-privileges",
        "--read-only",
        "--tmpfs",
        "/tmp:rw,noexec,nosuid,size=64m",  # noqa: S108 - inside the container, not a host temp path
        "-e",
        "PYTHONPATH=/repo",
        "-e",
        "PYTHONDONTWRITEBYTECODE=1",
        "-e",
        "HOME=/tmp",
        "-v",
        f"{repository_root.resolve()}:/repo:ro",
        "-v",
        f"{tempdir.resolve()}:/tests:ro",
        "-w",
        "/tests",
        settings.test_execution_docker_image,
        "python",
        "-m",
        "pytest",
        relative,
        *PYTEST_ARGS,
    ]


def _run_docker(
    *,
    test_file: Path,
    tempdir: Path,
    repository_root: Path,
    settings: Settings,
) -> ProcessResult:
    container = f"patchlens-test-{uuid.uuid4().hex[:12]}"
    command = build_docker_command(
        settings=settings,
        repository_root=repository_root,
        tempdir=tempdir,
        test_file=test_file,
        name=container,
    )
    env = safe_subprocess_env()
    if docker_host := os.environ.get("DOCKER_HOST"):
        env["DOCKER_HOST"] = docker_host
    cleanup = _docker_cleanup(container)
    return _run_process(
        command,
        cwd=None,
        env=env,
        timeout_seconds=settings.test_execution_timeout_seconds,
        max_output_bytes=settings.test_execution_max_output_bytes,
        cleanup=cleanup,
    )


def _docker_cleanup(container: str) -> Any:
    docker = shutil.which("docker")
    if docker is None:
        return None

    def cleanup() -> None:
        with subprocess.Popen(  # noqa: S603 - fixed argv, container name is generated
            [docker, "rm", "-f", container],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        ) as process:
            try:
                process.wait(timeout=8)
            except subprocess.TimeoutExpired:
                process.kill()

    return cleanup


def _run_process(
    command: list[str],
    *,
    cwd: Path | None,
    env: dict[str, str],
    timeout_seconds: float,
    max_output_bytes: int,
    preexec_fn: Any = None,
    cleanup: Any = None,
) -> ProcessResult:
    """Run a bounded process with output captured to files and tailed on read."""
    started = time.perf_counter()
    with tempfile.TemporaryFile() as stdout_file, tempfile.TemporaryFile() as stderr_file:
        try:
            process = subprocess.Popen(  # noqa: S603 - argv is constructed, never shell=True
                command,
                cwd=str(cwd) if cwd is not None else None,
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=stdout_file,
                stderr=stderr_file,
                start_new_session=True,
                preexec_fn=preexec_fn,
            )
        except OSError as exc:
            duration = int((time.perf_counter() - started) * 1000)
            return ProcessResult(
                command=_safe_command(command),
                exit_code=None,
                timed_out=False,
                duration_ms=duration,
                stdout_tail="",
                stderr_tail="",
                output_truncated=False,
                spawn_error=f"{type(exc).__name__}: {exc}",
            )

        timed_out = False
        try:
            process.wait(timeout=timeout_seconds)
        except subprocess.TimeoutExpired:
            timed_out = True
            _kill_process_group(process)
            with suppress(subprocess.TimeoutExpired):  # pragma: no cover - SIGKILL should win
                process.wait(timeout=5)
            if cleanup is not None:
                try:
                    cleanup()
                except Exception:
                    logger.warning("sandbox cleanup failed", extra=log_extra(container=command[-1]))
        duration = int((time.perf_counter() - started) * 1000)
        stdout, stdout_cut = _read_tail(stdout_file, max_output_bytes)
        stderr, stderr_cut = _read_tail(stderr_file, max_output_bytes)
        return ProcessResult(
            command=_safe_command(command),
            exit_code=process.returncode,
            timed_out=timed_out,
            duration_ms=duration,
            stdout_tail=stdout,
            stderr_tail=stderr,
            output_truncated=stdout_cut or stderr_cut,
        )


def _read_tail(file: Any, limit: int) -> tuple[str, bool]:
    file.seek(0, os.SEEK_END)
    size = file.tell()
    file.seek(max(0, size - limit))
    data = file.read(limit)
    return data.decode("utf-8", errors="replace"), size > limit


def _kill_process_group(process: subprocess.Popen[bytes]) -> None:
    try:
        os.killpg(os.getpgid(process.pid), signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        with suppress(ProcessLookupError):  # pragma: no cover - already gone
            process.kill()


def _safe_command(command: list[str]) -> list[str]:
    """Do not persist host paths or the Docker CLI location in run metadata."""
    safe: list[str] = []
    for part in command:
        if "/" in part or "\\" in part:
            safe.append(Path(part).name)
        else:
            safe.append(part)
    return safe


def _apply_process_result(candidate: TestCandidate, process: ProcessResult, sandbox: SandboxStatus) -> None:
    now = datetime.now(UTC)
    candidate.executed_at = now
    candidate.sandbox_mode = sandbox.effective_mode
    candidate.sandbox_detail = sandbox.detail[:2_000]
    candidate.execution_disabled_reason = None
    candidate.exit_code = process.exit_code
    candidate.duration_ms = process.duration_ms
    candidate.stdout_tail = process.stdout_tail
    candidate.stderr_tail = process.stderr_tail
    candidate.output_truncated = process.output_truncated
    candidate.timed_out = process.timed_out
    candidate.collected_tests = _collected_count(process.stdout_tail + "\n" + process.stderr_tail)
    candidate.run_metadata = {
        "sandbox": sandbox.to_dict(),
        "command": process.command,
        "output_truncated": process.output_truncated,
        "spawn_error": process.spawn_error,
    }

    if process.spawn_error:
        candidate.status = TestCandidateStatus.ERROR.value
        candidate.executed_at = None
        candidate.run_metadata = {**candidate.run_metadata, "detail": process.spawn_error}
        return
    if process.timed_out:
        candidate.status = TestCandidateStatus.ERROR.value
        candidate.run_metadata = {
            **candidate.run_metadata,
            "detail": f"execution exceeded the {sandbox.limits.get('timeout_seconds')}s timeout",
        }
        return
    if process.exit_code == 0:
        candidate.status = TestCandidateStatus.PASSED.value
        return
    if process.exit_code == 1:
        candidate.status = TestCandidateStatus.FAILED.value
        return
    if process.exit_code == 5:
        candidate.status = TestCandidateStatus.ERROR.value
        candidate.run_metadata = {**candidate.run_metadata, "detail": "pytest collected no tests"}
        return
    candidate.status = TestCandidateStatus.ERROR.value
    candidate.run_metadata = {
        **candidate.run_metadata,
        "detail": f"pytest exited with status {process.exit_code}",
    }


def _collected_count(output: str) -> int | None:
    counts: dict[str, int] = {}
    for match in _COUNT_RE.finditer(output):
        key = match.group(2).rstrip("s")
        counts[key] = counts.get(key, 0) + int(match.group(1))
    total = sum(counts.get(key, 0) for key in ("passed", "failed", "error", "skipped"))
    return total if counts else None


__all__ = [
    "MAX_OUTPUT_TAIL_BYTES",
    "SUBPROCESS_WARNING",
    "ExecutionOutcome",
    "ProcessResult",
    "SandboxStatus",
    "build_docker_command",
    "execute_candidate",
    "mark_execution_disabled",
    "mark_execution_error",
    "resolve_sandbox",
    "run_test_source",
]
