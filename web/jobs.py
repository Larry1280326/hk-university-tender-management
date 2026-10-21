"""Run each scrape/download/draft as a separate subprocess.

Streamlit reruns its script on every interaction, so browser work cannot live in that
thread. A one-shot worker process also makes cancellation and memory reclamation
possible: the worker's whole process group (Chromium included) can be killed.
"""

import json
import os
import signal
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional

PROJECT_ROOT = Path(__file__).resolve().parent.parent
TERMINAL_STATUSES = {"succeeded", "failed", "cancelled"}
ACTIVE_STATUSES = {"queued", "running", "cancelling"}


class JobError(RuntimeError):
    """A job could not be started (usually because another one is active)."""


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


@dataclass
class Job:
    job_id: str
    directory: Path

    @property
    def state(self) -> Dict[str, Any]:
        return _read_json(self.directory / "state.json", {})

    @property
    def request(self) -> Dict[str, Any]:
        return _read_json(self.directory / "request.json", {})

    def log_tail(self, max_bytes: int = 64 * 1024) -> str:
        path = self.directory / "job.log"
        try:
            size = path.stat().st_size
            with path.open("rb") as handle:
                if size > max_bytes:
                    handle.seek(size - max_bytes)
                return handle.read().decode("utf-8", errors="replace")
        except OSError:
            return ""


class JobManager:
    """Filesystem-backed job registry; one active job at a time."""

    def __init__(self, root: Optional[Path] = None) -> None:
        if root is None:
            try:
                import config

                root = Path(config.RUNTIME_DIR) / "jobs"
            except Exception:
                root = PROJECT_ROOT / ".runtime" / "jobs"
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        # Retain the Popen handles so finished children are reaped. Without this a
        # terminated worker stays a zombie and os.kill(pid, 0) keeps reporting it alive.
        self._processes: Dict[str, subprocess.Popen] = {}

    # -- lifecycle ---------------------------------------------------------

    def start(
        self,
        kind: str,
        *,
        university: str = "",
        options: Optional[Dict[str, Any]] = None,
        tenders: Optional[list] = None,
        workspace: Optional[Path] = None,
        started_by: str = "",
    ) -> Job:
        active = self.active_job()
        if active is not None:
            raise JobError(
                f"{active.state.get('kind', 'a job')} is already running "
                f"(started by {active.state.get('started_by') or 'unknown'})"
            )

        job_id = uuid.uuid4().hex
        directory = self.root / job_id
        directory.mkdir(parents=True, exist_ok=False)

        request = {
            "kind": kind,
            "university": university,
            "options": options or {},
            "tenders": tenders or [],
            "workspace": str(workspace or ""),
            "started_by": started_by,
        }
        _write_json(directory / "request.json", request)

        state = {
            "job_id": job_id,
            "kind": kind,
            "university": university,
            "status": "queued",
            "stage": "queued",
            "progress": {"current": 0, "total": 0, "message": ""},
            "started_by": started_by,
            "started_at": _now(),
            "updated_at": _now(),
            "cancel_requested": False,
            "pid": 0,
        }
        _write_json(directory / "state.json", state)

        env = os.environ.copy()
        existing = env.get("PYTHONPATH", "")
        env["PYTHONPATH"] = str(PROJECT_ROOT) + (os.pathsep + existing if existing else "")
        env["PYTHONUNBUFFERED"] = "1"

        process = subprocess.Popen(
            [
                sys.executable,
                "-u",
                "-m",
                "web.worker",
                "--job-dir",
                str(directory),
                "--parent-pid",
                str(os.getpid()),
            ],
            cwd=str(PROJECT_ROOT),
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )

        state["pid"] = process.pid
        state["status"] = "running"
        state["stage"] = "starting"
        state["updated_at"] = _now()
        _write_json(directory / "state.json", state)
        self._processes[job_id] = process
        return Job(job_id=job_id, directory=directory)

    # -- inspection --------------------------------------------------------

    def jobs(self, limit: int = 50) -> list:
        directories = sorted(
            (path for path in self.root.iterdir() if path.is_dir()),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )
        return [Job(path.name, path) for path in directories[:limit]]

    def get(self, job_id: str) -> Optional[Job]:
        directory = self.root / job_id
        return Job(job_id, directory) if directory.is_dir() else None

    def _worker_alive(self, job: Job, state: Dict[str, Any]) -> bool:
        process = self._processes.get(job.job_id)
        if process is not None:
            return process.poll() is None
        pid = int(state.get("pid") or 0)
        return bool(pid and _pid_alive(pid))

    def active_job(self) -> Optional[Job]:
        for job in self.jobs():
            state = job.state
            status = state.get("status")
            if status not in ACTIVE_STATUSES:
                continue
            if self._worker_alive(job, state):
                return job
            if status == "queued" and not state.get("pid"):
                # Give a just-created worker a moment to publish its pid.
                if _now() - float(state.get("updated_at") or 0) < 5:
                    return job
            self._mark_orphaned(job)
        return None

    def poll(self, job_id: str) -> Dict[str, Any]:
        job = self.get(job_id)
        if job is None:
            return {"job_id": job_id, "status": "failed", "error": "job not found"}
        state = job.state
        status = state.get("status")
        if status in TERMINAL_STATUSES:
            self._reap(job.job_id)
            return state

        if not self._worker_alive(job, state):
            self._mark_orphaned(job)
            self._reap(job.job_id)
            state = job.state
        return state

    def _reap(self, job_id: str) -> None:
        process = self._processes.get(job_id)
        if process is not None and process.poll() is not None:
            self._processes.pop(job_id, None)

    def _mark_orphaned(self, job: Job) -> None:
        state = job.state
        if not state:
            state = {
                "job_id": job.job_id,
                "status": "running",
                "started_at": _now(),
                "updated_at": _now(),
            }
        if state.get("status") in TERMINAL_STATUSES:
            return
        cancel_marker = (job.directory / "cancel.requested").exists()
        state["status"] = (
            "cancelled"
            if state.get("cancel_requested") or cancel_marker
            else "failed"
        )
        state["stage"] = "finished"
        state["error"] = state.get("error") or "worker exited without a final state"
        state["updated_at"] = _now()
        _write_json(job.directory / "state.json", state)

    # -- cancellation ------------------------------------------------------

    def cancel(self, job_id: str) -> bool:
        job = self.get(job_id)
        if job is None:
            return False
        state = job.state
        if state.get("status") in TERMINAL_STATUSES:
            return True

        # A separate marker survives the worker's own state writes, which cannot see the
        # parent's update between read and replace.
        try:
            (job.directory / "cancel.requested").write_text("1", encoding="utf-8")
        except OSError:
            pass

        state["cancel_requested"] = True
        state["status"] = "cancelling"
        state["updated_at"] = _now()
        _write_json(job.directory / "state.json", state)

        process = self._processes.get(job_id)
        pid = int(state.get("pid") or 0)
        if process is not None:
            try:
                os.killpg(os.getpgid(process.pid), signal.SIGTERM)
            except (ProcessLookupError, PermissionError, OSError):
                process.terminate()
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(os.getpgid(process.pid), signal.SIGKILL)
                except (ProcessLookupError, PermissionError, OSError):
                    process.kill()
                try:
                    process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    pass
        elif pid and _pid_alive(pid):
            try:
                os.killpg(os.getpgid(pid), signal.SIGTERM)
            except (ProcessLookupError, PermissionError, OSError):
                try:
                    os.kill(pid, signal.SIGTERM)
                except OSError:
                    pass

        current = job.state
        if current.get("status") not in TERMINAL_STATUSES:
            current["status"] = "cancelled"
            current["stage"] = "finished"
            current["cancel_requested"] = True
            current["error"] = current.get("error") or "cancelled by the operator"
            current["updated_at"] = _now()
            _write_json(job.directory / "state.json", current)
        self._reap(job_id)
        return True

    # -- housekeeping ------------------------------------------------------

    def cleanup(self, keep: int = 20, max_age_hours: float = 48) -> None:
        active = self.active_job()
        active_id = active.job_id if active else None
        cutoff = _now() - max_age_hours * 3600

        jobs = self.jobs(limit=10_000)
        for index, job in enumerate(jobs):
            if job.job_id == active_id:
                continue
            state = job.state
            if state.get("status") not in TERMINAL_STATUSES:
                continue
            too_old = float(state.get("updated_at") or 0) < cutoff
            if index < keep and not too_old:
                continue
            self._remove(job)

    def _remove(self, job: Job) -> None:
        self._processes.pop(job.job_id, None)
        for path in sorted(job.directory.rglob("*"), reverse=True):
            try:
                path.unlink() if path.is_file() else path.rmdir()
            except OSError:
                pass
        try:
            job.directory.rmdir()
        except OSError:
            pass
