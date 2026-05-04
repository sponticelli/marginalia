"""Hook dispatcher: fire registered shell hooks on lifecycle events (design §14).

Contract:

- JSON context piped to the hook's stdin.
- Hook reads stdin, processes, exits with a code.
- ``timeout_s`` enforced via ``subprocess.run(timeout=...)`` — timeout
  treated as failure under both blocking and non-blocking modes.
- Blocking + non-zero exit (or timeout) → ``HookFailure`` raised. The
  calling handler propagates this to the worker so the job fails.
- Non-blocking + non-zero exit → audit event written, return is still
  success. Stdout/stderr captured and stored on failure.

The dispatcher does not own the audit DB — when a hook fails non-
blockingly, an ``AuditWriter`` is asked to record the event. The
caller passes a writer-or-None.
"""

from __future__ import annotations

import json
import os
import shlex
import subprocess
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from engine.hooks.config import Hook, HookConfig

if TYPE_CHECKING:
    from engine.audit.writer import AuditWriter


@dataclass(frozen=True)
class HookResult:
    """Outcome of one hook invocation."""

    event: str
    command: str
    blocking: bool
    exit_code: int
    duration_ms: int
    stdout: str
    stderr: str
    timed_out: bool

    @property
    def succeeded(self) -> bool:
        return self.exit_code == 0 and not self.timed_out


class HookFailure(RuntimeError):
    """Raised when a blocking hook exits non-zero or times out.

    The handler in ``engine/jobs/dispatchers.py`` lets this propagate;
    the worker converts it into a failed job via the standard retry
    ladder.
    """

    def __init__(self, result: HookResult):
        self.result = result
        super().__init__(
            f"blocking hook {result.event!r} failed "
            f"(exit={result.exit_code}, timed_out={result.timed_out}): "
            f"{result.stderr.strip()[:200] or '(no stderr)'}"
        )


class HookDispatcher:
    """Fire hooks defined in ``HookConfig`` on lifecycle events."""

    def __init__(
        self,
        config: HookConfig | None,
        *,
        audit_writer: AuditWriter | None = None,
    ):
        self.config = config
        self.audit_writer = audit_writer

    def fire(
        self,
        event: str,
        context: dict[str, Any],
        *,
        job_id: str | None = None,
    ) -> HookResult | None:
        """Fire the hook for ``event`` (if registered).

        Returns ``None`` if no hook is registered (silent no-op).
        Otherwise returns a ``HookResult``. On blocking failure,
        raises ``HookFailure`` after writing an audit event.
        """
        if self.config is None:
            return None
        hook = self.config.for_event(event)
        if hook is None:
            return None

        result = self._run(hook, event, context)

        if not result.succeeded:
            self._record_failure(result, job_id=job_id)
            if hook.blocking:
                raise HookFailure(result)
        return result

    def _run(self, hook: Hook, event: str, context: dict[str, Any]) -> HookResult:
        """Execute one hook via subprocess and capture the outcome."""
        import time

        command = os.path.expanduser(hook.command)
        argv = shlex.split(command) if " " in command else [command]
        payload = json.dumps(context, default=str)

        t0 = time.perf_counter()
        timed_out = False
        stdout = stderr = ""
        try:
            proc = subprocess.run(
                argv,
                input=payload,
                capture_output=True,
                text=True,
                timeout=hook.timeout_s,
                check=False,
            )
            exit_code = proc.returncode
            stdout = proc.stdout or ""
            stderr = proc.stderr or ""
        except subprocess.TimeoutExpired as exc:
            timed_out = True
            exit_code = -1
            stdout = (exc.stdout or b"").decode("utf-8", errors="replace") if exc.stdout else ""
            stderr = f"hook timed out after {hook.timeout_s}s\n" + (
                (exc.stderr or b"").decode("utf-8", errors="replace") if exc.stderr else ""
            )
        except FileNotFoundError as exc:
            timed_out = False
            exit_code = -2
            stderr = f"hook command not found: {exc}"
        duration_ms = int((time.perf_counter() - t0) * 1000)

        return HookResult(
            event=event,
            command=hook.command,
            blocking=hook.blocking,
            exit_code=exit_code,
            duration_ms=duration_ms,
            stdout=stdout,
            stderr=stderr,
            timed_out=timed_out,
        )

    def _record_failure(self, result: HookResult, *, job_id: str | None) -> None:
        """Write a `hook_failed` audit_events row when an audit writer is set."""
        if self.audit_writer is None:
            return
        self.audit_writer.record_event(
            event_type="hook_failed",
            metadata={
                "event": result.event,
                "command": result.command,
                "blocking": result.blocking,
                "exit_code": result.exit_code,
                "timed_out": result.timed_out,
                "duration_ms": result.duration_ms,
                "stdout": result.stdout[-2000:],
                "stderr": result.stderr[-2000:],
            },
            job_id=job_id,
        )


__all__ = ["HookDispatcher", "HookFailure", "HookResult"]
