"""run_command のコミットの関所。テストが落ちていれば git commit を実行しない。"""

from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from company import tools as toolkit  # noqa: E402

PY = sys.executable


class CommitGuardTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        subprocess.run(["git", "init", "-q", "-b", "main"], cwd=self.tmp, check=True)
        subprocess.run(["git", "-c", "user.email=t@example.com", "-c", "user.name=t", "commit", "-q", "--allow-empty", "-m", "root"], cwd=self.tmp, check=True)
        (self.tmp / "a.txt").write_text("x")
        subprocess.run(["git", "add", "a.txt"], cwd=self.tmp, check=True)
        self._root, self._test = toolkit.ROOT, toolkit.TEST_COMMAND
        toolkit.ROOT = self.tmp

    def tearDown(self):
        toolkit.ROOT, toolkit.TEST_COMMAND = self._root, self._test

    def _commits(self) -> int:
        out = subprocess.run(["git", "rev-list", "--count", "HEAD"], cwd=self.tmp, capture_output=True, text=True)
        return int(out.stdout.strip())

    def _commit(self) -> str:
        return toolkit.run_command({"command": 'git -c user.email=t@example.com -c user.name=t commit -q -m "add a"'})

    def test_failing_tests_block_the_commit(self):
        toolkit.TEST_COMMAND = f'{PY} -c "import sys; print(\'FAILED (failures=1)\'); sys.exit(1)"'
        out = self._commit()
        self.assertIn("コミットを止めました", out)
        self.assertIn("FAILED", out)
        self.assertEqual(self._commits(), 1)

    def test_passing_tests_let_the_commit_through(self):
        toolkit.TEST_COMMAND = f'{PY} -c "print(\'OK\')"'
        out = self._commit()
        self.assertIn("exit code: 0", out)
        self.assertEqual(self._commits(), 2)

    def test_non_commit_commands_do_not_run_tests(self):
        toolkit.TEST_COMMAND = f'{PY} -c "import sys; sys.exit(1)"'
        out = toolkit.run_command({"command": "git status --short"})
        self.assertNotIn("コミットを止めました", out)

    def test_commit_with_global_flags_is_still_guarded(self):
        toolkit.TEST_COMMAND = f'{PY} -c "import sys; sys.exit(1)"'
        out = toolkit.run_command({"command": 'git -C . -c user.email=t@example.com -c user.name=t commit -m x'})
        self.assertIn("コミットを止めました", out)
        self.assertEqual(self._commits(), 1)

    def test_empty_test_command_disables_the_guard(self):
        toolkit.TEST_COMMAND = ""
        self._commit()
        self.assertEqual(self._commits(), 2)


if __name__ == "__main__":
    unittest.main()
