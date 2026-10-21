"""Behaviour of the subprocess job manager."""

import tempfile
import time
import unittest
from pathlib import Path

from web.jobs import JobError, JobManager, TERMINAL_STATUSES


def _wait(manager: JobManager, job_id: str, timeout: float = 30.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        state = manager.poll(job_id)
        if state.get("status") in TERMINAL_STATUSES:
            return state
        time.sleep(0.1)
    raise AssertionError(f"job {job_id} did not finish: {manager.poll(job_id)}")


class TestJobManager(unittest.TestCase):
    def test_runs_and_reports_a_result(self):
        with tempfile.TemporaryDirectory() as tmp:
            manager = JobManager(Path(tmp) / "jobs")
            job = manager.start(
                "selftest",
                started_by="tester",
                workspace=Path(tmp) / "workspace",
            )
            state = _wait(manager, job.job_id)
            self.assertEqual(state["status"], "succeeded")
            self.assertTrue(state["result"]["ok"])
            self.assertEqual(state["started_by"], "tester")
            self.assertIn("selftest", manager.get(job.job_id).log_tail())

    def test_rejects_a_second_active_job(self):
        with tempfile.TemporaryDirectory() as tmp:
            manager = JobManager(Path(tmp) / "jobs")
            first = manager.start(
                "selftest",
                options={"sleep": 2},
                workspace=Path(tmp) / "workspace",
            )
            try:
                with self.assertRaises(JobError):
                    manager.start(
                        "selftest",
                        workspace=Path(tmp) / "workspace",
                    )
            finally:
                manager.cancel(first.job_id)
            state = _wait(manager, first.job_id)
            self.assertIn(state["status"], {"cancelled", "succeeded"})

    def test_cancel_marks_the_job_cancelled(self):
        with tempfile.TemporaryDirectory() as tmp:
            manager = JobManager(Path(tmp) / "jobs")
            job = manager.start(
                "selftest",
                options={"sleep": 30},
                workspace=Path(tmp) / "workspace",
            )
            self.assertTrue(manager.cancel(job.job_id))
            state = _wait(manager, job.job_id, timeout=15)
            self.assertEqual(state["status"], "cancelled")


if __name__ == "__main__":
    unittest.main()
