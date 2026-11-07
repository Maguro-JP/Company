"""起動（app.py）。ショートカット（pythonw）では stdout が無い。その形で落ちないことを見る。"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import uvicorn  # noqa: E402

import app  # noqa: E402


class NoConsoleTest(unittest.TestCase):
    def test_uvicorn_logging_without_console(self):
        """pythonw と同じく stdout が None でも、記録の設定で落ちない（log_config=None）。
        既定の設定は isatty を呼んで落ちる。Windows ランナーで実際に落ちた形。"""
        with mock.patch.object(sys, "stdout", None), mock.patch.object(sys, "stderr", None):
            with self.assertRaises(ValueError):
                uvicorn.Config("company.server:app", log_level="warning").configure_logging()
            uvicorn.Config("company.server:app", log_level="warning", log_config=None).configure_logging()
        src = Path(app.__file__).read_text(encoding="utf-8")
        self.assertIn("log_config=None", src)

    def test_crash_is_written_to_log(self):
        with tempfile.TemporaryDirectory() as d:
            with mock.patch.dict("os.environ", {"COMPANY_HOME": d}):
                app._report_crash(RuntimeError("試しの失敗"), dialog=False)
                text = (Path(d) / "logs" / "app.log").read_text(encoding="utf-8")
        self.assertIn("起動に失敗", text)
        self.assertIn("試しの失敗", text)


if __name__ == "__main__":
    unittest.main()
