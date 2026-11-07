"""置き場の決め方と、配布版の作業場の用意の検証。"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from company import migrate, paths  # noqa: E402


def fake_install(root: Path) -> Path:
    (root / "python").mkdir(parents=True)
    (root / "python" / "python.exe").write_bytes(b"")
    (root / "company.yaml").write_text("company: {name: x}\n", encoding="utf-8")
    (root / "agents").mkdir()
    (root / "agents" / "ceo.md").write_text("---\nrole: ceo\n---\n", encoding="utf-8")
    (root / "product").mkdir()
    (root / "product" / "a.py").write_text("print(1)\n", encoding="utf-8")
    (root / "ui").mkdir()
    (root / "ui" / "index.html").write_text("<html>", encoding="utf-8")
    return root


class PathsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def test_dev_checkout_is_its_own_workspace(self):
        root = self.tmp / "repo"
        (root / ".git").mkdir(parents=True)
        self.assertFalse(paths.is_installed({}, root))
        self.assertEqual(paths.workspace_root({}, root), root)

    def test_installed_uses_programdata_and_shared_models(self):
        root = fake_install(self.tmp / "pf")
        env = {"ProgramData": str(self.tmp / "pd"), "SystemDrive": "C:"}
        self.assertTrue(paths.is_installed(env, root))
        self.assertEqual(paths.workspace_root(env, root), self.tmp / "pd" / "Company")
        self.assertEqual(paths.models_dir(env, root), Path("C:\\") / "LLM" / "gguf")

    def test_env_overrides_win(self):
        env = {"COMPANY_HOME": str(self.tmp / "home"), "LLM_HOME": str(self.tmp / "llm")}
        self.assertEqual(paths.workspace_root(env, self.tmp), self.tmp / "home")
        self.assertEqual(paths.models_dir(env, self.tmp), self.tmp / "llm" / "gguf")

    def test_shared_models_dir_gets_readme_once(self):
        d = paths.ensure_models_dir(self.tmp / "LLM" / "gguf")
        readme = d.parent / "README.txt"
        self.assertTrue(readme.exists())
        readme.write_text("mine", encoding="utf-8")
        paths.ensure_models_dir(d)
        self.assertEqual(readme.read_text(encoding="utf-8"), "mine")


class MigrateTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.app = fake_install(self.tmp / "pf")
        self.ws = self.tmp / "pd" / "Company"

    def test_ensure_workspace_adds_missing_and_keeps_existing(self):
        added = migrate.ensure_workspace(self.app, self.ws)
        self.assertEqual(added, 3)  # company.yaml, agents/ceo.md, product/a.py
        self.assertFalse((self.ws / "ui").exists())  # 画面はプログラム側に残す
        (self.ws / "product" / "a.py").write_text("print(2)\n", encoding="utf-8")
        (self.app / "product" / "b.py").write_text("print(3)\n", encoding="utf-8")
        added = migrate.ensure_workspace(self.app, self.ws)
        self.assertEqual(added, 1)
        self.assertEqual((self.ws / "product" / "a.py").read_text(encoding="utf-8"), "print(2)\n")
        self.assertTrue((self.ws / "product" / "b.py").exists())
        self.assertTrue((self.ws / ".gitignore").exists())

    def test_workspace_equal_to_app_root_is_left_alone(self):
        self.assertEqual(migrate.ensure_workspace(self.app, self.app), 0)

    def test_migrate_old_moves_models_and_copies_records(self):
        old = self.tmp / "old"
        (old / "data" / "models").mkdir(parents=True)
        (old / "data" / "decisions").mkdir()
        (old / "data" / "decisions" / "2026-11-01-build.md").write_text("x", encoding="utf-8")
        (old / "data" / "models" / "tiny.gguf").write_bytes(b"gguf")
        (old / "product").mkdir()
        (old / "product" / "mine.py").write_text("print('mine')\n", encoding="utf-8")
        models = self.tmp / "LLM" / "gguf"
        r = migrate.migrate_old(old, self.ws, models)
        self.assertEqual(r["models"], 1)
        self.assertTrue((models / "tiny.gguf").exists())
        self.assertFalse((old / "data" / "models" / "tiny.gguf").exists())
        self.assertTrue((self.ws / "data" / "decisions" / "2026-11-01-build.md").exists())
        self.assertTrue((self.ws / "product" / "mine.py").exists())
        self.assertTrue((self.ws / "migrate.log").exists())
