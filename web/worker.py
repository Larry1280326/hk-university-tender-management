"""One-shot job worker: run a single action, write state/log files, exit.

The Streamlit UI never talks to Playwright directly. It spawns this module and polls
``state.json`` and ``job.log``.
"""

import argparse
import fcntl
import json
import os
import sys
import threading
import time
import traceback
from pathlib import Path
from typing import Any, Dict, Optional

TERMINAL = {"succeeded", "failed", "cancelled"}


class JobCancelled(RuntimeError):
    """Raised cooperatively when the parent asks the worker to stop."""


def _now() -> float:
    return time.time()


def _read_json(path: Path, default: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default if default is not None else {}


def _write_json(path: Path, data: Dict[str, Any]) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporary, path)


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


class Reporter:
    """Writes progress to ``state.json`` and log lines to stdout (redirected to a file)."""

    def __init__(self, state_path: Path, request: Dict[str, Any]) -> None:
        self.state_path = Path(state_path)
        self.cancel_marker = self.state_path.parent / "cancel.requested"
        self.request = request
        self.state: Dict[str, Any] = {
            "job_id": self.state_path.parent.name,
            "kind": request.get("kind", ""),
            "university": request.get("university", ""),
            "status": "running",
            "stage": "starting",
            "progress": {"current": 0, "total": 0, "message": ""},
            "result": None,
            "error": "",
            "started_by": request.get("started_by", ""),
            "started_at": _now(),
            "updated_at": _now(),
            "cancel_requested": False,
            "pid": os.getpid(),
        }
        self._last_write = 0.0
        self._last_cancel_check = 0.0
        self._cancelled = False

    # -- output ------------------------------------------------------------

    def log(self, message: str) -> None:
        print(message, flush=True)

    def progress(self, current: int, total: int, message: str = "") -> None:
        self.check_cancelled()
        self.update(
            stage="working",
            current=current,
            total=total,
            message=message,
            force=True,
        )

    # -- state -------------------------------------------------------------

    def update(
        self,
        *,
        stage: Optional[str] = None,
        current: Optional[int] = None,
        total: Optional[int] = None,
        message: Optional[str] = None,
        force: bool = False,
    ) -> None:
        if stage is not None:
            self.state["stage"] = stage
        progress = dict(self.state.get("progress") or {})
        if current is not None:
            progress["current"] = int(current)
        if total is not None:
            progress["total"] = int(total)
        if message is not None:
            progress["message"] = str(message)
        self.state["progress"] = progress

        now = _now()
        if force or now - self._last_write >= 0.5:
            self._last_write = now
            self.state["updated_at"] = now
            self.state["cancel_requested"] = self._is_cancel_requested()
            _write_json(self.state_path, self.state)

    def check_cancelled(self) -> None:
        if self._is_cancel_requested():
            self._cancelled = True
            raise JobCancelled("cancelled by the operator")

    def _is_cancel_requested(self) -> bool:
        if self._cancelled:
            return True
        now = _now()
        if now - self._last_cancel_check >= 0.3:
            self._last_cancel_check = now
            self._cancelled = self.cancel_marker.exists()
        return self._cancelled

    def finish(
        self,
        status: str,
        *,
        result: Optional[Dict[str, Any]] = None,
        error: str = "",
        traceback_text: str = "",
    ) -> None:
        self.state["status"] = status
        self.state["stage"] = "finished"
        self.state["result"] = result
        self.state["error"] = error
        if traceback_text:
            self.state["traceback"] = traceback_text
        self.state["cancel_requested"] = self._is_cancel_requested()
        self.state["updated_at"] = _now()
        self.state["pid"] = os.getpid()
        _write_json(self.state_path, self.state)


def _redirect_output(log_path: Path):
    handle = log_path.open("a", encoding="utf-8", errors="replace", buffering=1)
    sys.stdout = handle
    sys.stderr = handle
    return handle


def _start_parent_watchdog(parent_pid: int) -> None:
    if parent_pid <= 0:
        return

    def watch() -> None:
        while True:
            if os.getppid() != parent_pid and not _pid_alive(parent_pid):
                # The Streamlit server went away (redeploy/restart); do not linger.
                os._exit(3)
            time.sleep(2)

    threading.Thread(target=watch, name="parent-watchdog", daemon=True).start()


def _acquire_lock(lock_path: Path):
    handle = lock_path.open("w", encoding="utf-8")
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        handle.close()
        return None
    return handle


def main(argv: Optional[list] = None) -> int:
    parser = argparse.ArgumentParser(description="Run one tender automation job")
    parser.add_argument("--job-dir", required=True)
    parser.add_argument("--parent-pid", type=int, default=0)
    args = parser.parse_args(argv)

    job_dir = Path(args.job_dir)
    request = _read_json(job_dir / "request.json", {})
    _redirect_output(job_dir / "job.log")

    reporter = Reporter(job_dir / "state.json", request)
    _start_parent_watchdog(args.parent_pid)

    lock = _acquire_lock(job_dir / "job.lock")
    if lock is None:
        reporter.finish("failed", error="another worker is already running this job")
        return 4

    try:
        reporter.update(stage="starting", force=True)
        from web.tasks import dispatch

        result = dispatch(request, reporter)
        reporter.finish("succeeded", result=result)
        return 0
    except JobCancelled as exc:
        reporter.finish("cancelled", error=str(exc))
        return 2
    except BaseException as exc:  # includes SystemExit from a library
        traceback_text = traceback.format_exc()
        print(traceback_text, file=sys.stderr, flush=True)
        reporter.finish(
            "failed",
            error=f"{type(exc).__name__}: {exc}",
            traceback_text=traceback_text,
        )
        return 1
    finally:
        try:
            lock.close()
        except OSError:
            pass


if __name__ == "__main__":
    raise SystemExit(main())
