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


class UpdaterTest(unittest.TestCase):
    """更新の確認は、手元の VERSION より新しい版の Setup.exe だけを答える。"""

    def setUp(self):
        import json
        import threading
        from http.server import BaseHTTPRequestHandler, HTTPServer

        payload = {"tag_name": "v9.9.9", "html_url": "https://example.invalid/r",
                   "assets": [{"name": "Company-Setup.exe", "browser_download_url": "https://example.invalid/Company-Setup.exe"}],
                   "body": "notes"}

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                out = json.dumps(payload).encode()
                self.send_response(200); self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(out))); self.end_headers(); self.wfile.write(out)

        self.server = HTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.server.server_port}/latest"

    def tearDown(self):
        self.server.shutdown()

    def test_newer_compares_numbers(self):
        from company import updater
        self.assertTrue(updater.newer("v0.2.0", "0.1.9"))
        self.assertFalse(updater.newer("0.2.0", "0.2.0"))
        self.assertFalse(updater.newer("0.1.0", "0.2.0"))

    def test_check_returns_setup_when_newer(self):
        from company import updater
        found = updater.check(self.url)
        self.assertEqual(found["version"], "9.9.9")
        self.assertTrue(found["setup"].endswith("Company-Setup.exe"))

    def test_check_is_quiet_when_unreachable(self):
        from company import updater
        self.assertIsNone(updater.check("http://127.0.0.1:9/latest", timeout=1))
        with self.assertRaises(Exception):
            updater.check("http://127.0.0.1:9/latest", timeout=1, quiet=False)


class RefreshBundledTest(unittest.TestCase):
    """更新で同梱物が変わったとき、手の入っていない物だけが新しくなり、消えた物は消え、直した物は残る。"""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.app = fake_install(self.tmp / "pf")
        self.ws = self.tmp / "pd" / "Company"
        migrate.ensure_workspace(self.app, self.ws)
        self.old_company = migrate._sha256(self.app / "company.yaml")
        self.old_a = migrate._sha256(self.app / "product" / "a.py")

    def _known(self, *extra: str) -> set[str]:
        return {self.old_company, self.old_a, *extra}

    def test_untouched_file_is_updated_and_edited_file_is_kept(self):
        (self.app / "company.yaml").write_text("name: new\n", encoding="utf-8")
        (self.ws / "product" / "a.py").write_text("print('mine')\n", encoding="utf-8")
        (self.app / "product" / "a.py").write_text("print(9)\n", encoding="utf-8")
        r = migrate.refresh_bundled(self.app, self.ws, self._known())
        self.assertEqual(r, {"updated": 1, "removed": 0, "kept": 1})
        self.assertEqual((self.ws / "company.yaml").read_text(encoding="utf-8"), "name: new\n")
        self.assertEqual((self.ws / "product" / "a.py").read_text(encoding="utf-8"), "print('mine')\n")

    def test_file_dropped_from_bundle_is_removed_only_if_untouched(self):
        (self.app / "product" / "a.py").unlink()
        r = migrate.refresh_bundled(self.app, self.ws, self._known())
        self.assertEqual(r["removed"], 1)
        self.assertFalse((self.ws / "product" / "a.py").exists())

    def test_without_known_table_nothing_changes(self):
        (self.app / "company.yaml").write_text("name: new\n", encoding="utf-8")
        self.assertEqual(migrate.refresh_bundled(self.app, self.ws, set())["updated"], 0)

    def test_known_table_covers_current_bundle(self):
        """コミットの前に tools/known_bundled.py を走らせ忘れると、配った版が表に無く、次の更新で上書きできない。"""
        root = Path(__file__).resolve().parents[1]
        known = migrate.known_hashes(root)
        missing = [str(rel) for rel, src in migrate._bundled_files(root) if migrate._sha256(src) not in known]
        self.assertEqual(missing, [], "python tools/known_bundled.py を走らせる")
