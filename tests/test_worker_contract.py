"""Contract test for the worker subprocess: state/log files and exit codes."""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent


class TestWorkerContract(unittest.TestCase):
    def _run(self, kind: str):
        directory = Path(tempfile.mkdtemp(prefix="tender-worker-"))
        (directory / "request.json").write_text(
            json.dumps({"kind": kind, "university": "", "options": {}, "tenders": []}),
            encoding="utf-8",
        )
        env = os.environ.copy()
        existing = env.get("PYTHONPATH", "")
        env["PYTHONPATH"] = str(PROJECT_ROOT) + (os.pathsep + existing if existing else "")
        process = subprocess.run(
            [
                sys.executable,
                "-u",
                "-m",
                "web.worker",
                "--job-dir",
                str(directory),
            ],
            cwd=str(PROJECT_ROOT),
            env=env,
            capture_output=True,
            text=True,
            timeout=60,
        )
        state = json.loads((directory / "state.json").read_text(encoding="utf-8"))
        log = (directory / "job.log").read_text(encoding="utf-8") if (directory / "job.log").exists() else ""
        return process, state, log

    def test_selftest_succeeds(self):
        process, state, _log = self._run("selftest")
        self.assertEqual(process.returncode, 0)
        self.assertEqual(state["status"], "succeeded")
        self.assertTrue(state["result"]["ok"])

    def test_unknown_kind_fails_with_a_traceback(self):
        process, state, log = self._run("not-a-kind")
        self.assertEqual(process.returncode, 1)
        self.assertEqual(state["status"], "failed")
        self.assertIn("Unknown job kind", state["error"])
        self.assertIn("Unknown job kind", log)


if __name__ == "__main__":
    unittest.main()
