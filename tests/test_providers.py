"""プロバイダの一覧と組み立て、役職ごとの割り当ての検証。"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient  # noqa: E402

from company import config as config_module  # noqa: E402
from company import providers, runner, server  # noqa: E402
from company import tools as toolkit  # noqa: E402
from tests.test_loop import FakeLLM  # noqa: E402


class PresetTest(unittest.TestCase):
    def test_every_preset_builds_an_openai_compatible_provider(self):
        for preset in providers.PRESETS:
            with mock.patch.dict(os.environ, {preset["key_env"]: "k"} if preset["key_env"] else {}):
                p = providers.make_provider({"provider": preset["id"], "model": "m"})
            self.assertIsInstance(p, providers.OpenAICompatibleProvider, preset["id"])
            self.assertEqual(p.base_url, preset["base_url"].rstrip("/"), preset["id"])

    def test_cloud_preset_without_key_names_the_env_var(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(providers.ProviderError) as ctx:
                providers.make_provider({"provider": "openai", "model": "gpt"})
        self.assertIn("OPENAI_API_KEY", str(ctx.exception))

    def test_panel_key_beats_env(self):
        with mock.patch.dict(os.environ, {"GROQ_API_KEY": "env"}):
            p = providers.make_provider({"provider": "groq", "model": "m", "api_key": "panel"})
        self.assertEqual(p.api_key, "panel")

    def test_local_presets_need_no_key(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            p = providers.make_provider({"provider": "ollama", "model": "m"})
        self.assertEqual(p.api_key, "")

    def test_catalog_lists_native_and_presets_without_values(self):
        with mock.patch.dict(os.environ, {"OPENAI_API_KEY": "secret"}):
            cat = providers.provider_catalog()
        ids = [c["id"] for c in cat]
        self.assertEqual(ids[:5], ["builtin", "aas", "gemini", "anthropic", "openai_compatible"])
        self.assertIn("openai", ids)
        openai = next(c for c in cat if c["id"] == "openai")
        self.assertTrue(openai["needs_key"] and openai["key_in_env"])
        self.assertNotIn("secret", str(cat))


class RoleSettingsTest(unittest.TestCase):
    def test_override_only_non_empty_values(self):
        base = {"provider": "builtin", "model": "a", "temperature": 0.2, "roles": {"ceo": {"provider": "openai", "model": "", "api_key": ""}}}
        s = runner.settings_for_role(base, "ceo")
        self.assertEqual(s["provider"], "openai")
        self.assertEqual(s["model"], "a")
        self.assertEqual(runner.settings_for_role(base, "ops")["provider"], "builtin")

    def test_save_rejects_non_dict_roles(self):
        tmp = Path(tempfile.mkdtemp())
        orig = runner.SETTINGS_PATH
        runner.SETTINGS_PATH = tmp / "s.json"
        try:
            self.assertEqual(runner.save_settings({"roles": "x"})["roles"], {})
            self.assertEqual(runner.save_settings({"roles": {"ceo": {"model": "m"}}})["roles"], {"ceo": {"model": "m"}})
        finally:
            runner.SETTINGS_PATH = orig


class RoleAssignmentThroughRunTest(unittest.TestCase):
    """build サイクルで、ceo だけ別の AI（別の偽サーバー）に向く。"""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self._root, self._settings, self._runs = toolkit.ROOT, runner.SETTINGS_PATH, config_module.RUNS_PATH
        toolkit.ROOT = self.tmp
        runner.SETTINGS_PATH = self.tmp / "settings.json"
        config_module.RUNS_PATH = self.tmp / "runs.csv"
        self.client = TestClient(server.app)

    def tearDown(self):
        toolkit.ROOT, runner.SETTINGS_PATH, config_module.RUNS_PATH = self._root, self._settings, self._runs

    def test_ceo_goes_to_its_own_provider(self):
        default_llm = FakeLLM([{"text": "builder 側"}])
        ceo_llm = FakeLLM([{"text": "ceo 側"}])
        try:
            self.client.post("/api/settings", json={
                "provider": "openai_compatible", "base_url": default_llm.url, "model": "fake-default",
                "roles": {"ceo": {"provider": "openai_compatible", "base_url": ceo_llm.url, "model": "fake-ceo"}},
            })
            started = self.client.post("/api/runs", json={"kind": "cycle", "target": "build"}).json()
            run = runner.REGISTRY.get(started["id"])
            for _ in range(200):
                if run.status != "running":
                    break
                run.wait_for(len(run.events), 0.1)
        finally:
            default_llm.close(); ceo_llm.close()
        self.assertEqual(run.status, "done")
        self.assertEqual([r["model"] for r in ceo_llm.requests], ["fake-ceo"])
        self.assertEqual([r["model"] for r in default_llm.requests], ["fake-default"])
        starts = [e for e in run.events if e["type"] == "role_start"]
        self.assertEqual([(e["role"], e["model"]) for e in starts], [("ceo", "fake-ceo"), ("builder", "fake-default")])
        rows = (self.tmp / "runs.csv").read_text(encoding="utf-8").splitlines()
        self.assertIn(",ceo,openai_compatible,fake-ceo,", rows[1])

    def test_providers_endpoint(self):
        ids = [p["id"] for p in self.client.get("/api/providers").json()["providers"]]
        self.assertIn("openrouter", ids)


if __name__ == "__main__":
    unittest.main()
