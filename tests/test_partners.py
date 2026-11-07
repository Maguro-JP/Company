"""同じ作者のツールとの連携（company/partners.py）。

本物の相手（NetVision、AdaptiveAIStudio）はこの環境に無いので、
標準入出力の偽の MCP サーバーと、Company 自身の /mcp（HTTP）で経路を通す。"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient  # noqa: E402

from company import partners, server  # noqa: E402

FAKE_MCP = textwrap.dedent('''
    import json, sys
    for line in sys.stdin:
        m = json.loads(line)
        if "id" not in m:
            continue
        if m["method"] == "initialize":
            r = {"protocolVersion": "2024-11-05", "capabilities": {"tools": {}}, "serverInfo": {"name": "fake", "version": "1"}}
        elif m["method"] == "tools/list":
            r = {"tools": [{"name": "device_summary", "description": "台帳の内訳", "inputSchema": {"type": "object", "properties": {}}}]}
        elif m["method"] == "tools/call":
            r = {"content": [{"type": "text", "text": json.dumps({"total": 3, "args": m["params"]["arguments"]})}], "isError": False}
        else:
            r = None
        sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": m["id"], "result": r}) + "\\n")
        sys.stdout.flush()
''')


class StdioMcpTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.script = Path(self.tmp.name) / "fake_mcp.py"
        self.script.write_text(FAKE_MCP, encoding="utf-8")
        self.spec = {"command": [sys.executable, "-I", str(self.script)], "cwd": self.tmp.name}

    def tearDown(self):
        self.tmp.cleanup()

    def test_list_and_call_over_stdio(self):
        with partners.McpClient(self.spec) as c:
            self.assertEqual(c.server_info.get("name"), "fake")
            tools = c.list_tools()
            self.assertEqual([t["name"] for t in tools], ["device_summary"])
            out = c.call("device_summary", {"q": "core"})
        self.assertFalse(out["isError"])
        self.assertIn('"total": 3', out["content"][0]["text"])

    def test_dead_command_is_a_partner_error(self):
        spec = {"command": [sys.executable, "-c", "import sys; sys.exit(3)"], "cwd": self.tmp.name}
        with self.assertRaises(partners.PartnerError):
            with partners.McpClient(spec):
                pass

    def test_toolset_names_tools_by_partner(self):
        entries = [{"id": "ledger", "name": "台帳", "mcp": self.spec}]
        with mock.patch.object(partners, "builtin", return_value=[]), \
             mock.patch.object(partners, "PARTNERS_FILE", Path(self.tmp.name) / "partners.json"):
            partners.save_extra(entries)
            tools = partners.toolset()
            self.assertIn("ledger__device_summary", tools)
            spec, fn = tools["ledger__device_summary"]
            self.assertTrue(spec.description.startswith("[台帳]"))
            text = fn({"q": "x"})
        self.assertIn('"total": 3', text)

    def test_probe_reports_tools_and_dead_http(self):
        p = {"id": "ledger", "name": "台帳", "http": {"base_url": "http://127.0.0.1:9", "probe": "/x"}, "mcp": self.spec}
        out = partners.probe(p)
        self.assertEqual(out["tools"], 1)
        self.assertTrue(out["alive"])
        self.assertIn("つながりません", out["detail"])


class HttpMcpTest(unittest.TestCase):
    """Company 自身の /mcp を相手に見立てて、HTTP の経路を通す。"""

    def test_http_client_against_company_mcp(self):
        client = TestClient(server.app)

        def fake_urlopen(req, timeout=0):
            r = client.post(req.full_url, content=req.data, headers=dict(req.header_items()))

            class Resp:
                status = r.status_code
                headers = r.headers

                def read(self):
                    return r.content

                def __enter__(self):
                    return self

                def __exit__(self, *a):
                    return False
            return Resp()

        with mock.patch.object(partners.urllib.request, "urlopen", fake_urlopen):
            with partners.McpClient({"url": "http://testserver/mcp"}) as c:
                names = [t["name"] for t in c.list_tools()]
                out = c.call("list_roles", {})
        self.assertIn("start_cycle", names)
        self.assertIn("roles", out["content"][0]["text"])


class AasRegisterTest(unittest.TestCase):
    def test_writes_company_into_mcp_servers_and_keeps_others(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "aas.config.json"
            path.write_text(json.dumps({"model": "x", "mcp": {"servers": {"github": {"url": "https://g/"}}}}), encoding="utf-8")
            r = partners.register_with_aas("http://127.0.0.1:8765/mcp", path)
            self.assertTrue(r["changed"])
            got = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(got["model"], "x")
            self.assertEqual(got["mcp"]["servers"]["github"], {"url": "https://g/"})
            self.assertEqual(got["mcp"]["servers"]["company"], {"url": "http://127.0.0.1:8765/mcp", "enabled": True})
            again = partners.register_with_aas("http://127.0.0.1:8765/mcp", path)
            self.assertFalse(again["changed"])

    def test_broken_json_is_reported_not_overwritten(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "aas.config.json"
            path.write_text("{oops", encoding="utf-8")
            r = partners.register_with_aas("http://x/mcp", path)
            self.assertIn("error", r)
            self.assertEqual(path.read_text(encoding="utf-8"), "{oops")

    def test_config_path_follows_aas_order(self):
        self.assertEqual(partners.aas_config_path({"AAS_CONFIG": "/c/a.json"}), Path("/c/a.json"))
        self.assertEqual(partners.aas_config_path({"AAS_HOME": "/h"}), Path("/h/aas.config.json"))
        self.assertEqual(partners.aas_config_path({"USERPROFILE": "/u"}), Path("/u/.aas/aas.config.json"))


class PartnersApiTest(unittest.TestCase):
    def test_list_and_register_endpoints(self):
        client = TestClient(server.app)
        with tempfile.TemporaryDirectory() as d:
            with mock.patch.dict(os.environ, {"AAS_CONFIG": str(Path(d) / "aas.config.json")}):
                r = client.get("/api/partners").json()
                ids = [p["id"] for p in r["partners"]]
                self.assertEqual(ids[:3], ["aas", "netvision", "characterdesigner"])
                self.assertIn("ciel", ids)
                planned = [p for p in r["partners"] if p["planned"]]
                self.assertIn("video_gen", [p["id"] for p in planned])
                self.assertIn("tts", [p["id"] for p in planned])
                self.assertEqual(len(planned), len(partners.PLANNED_SEATS))
                self.assertTrue(all(p["dept"] for p in r["partners"] if p["id"] != "ciel" or True))
                self.assertTrue(all(not p["alive"] for p in planned))
                reg = client.post("/api/partners/aas/register").json()
                self.assertEqual(reg["url"], "http://testserver/mcp")
                self.assertTrue(Path(reg["path"]).exists())
        self.assertEqual(client.get("/api/partners/nope/tools").status_code, 404)
        self.assertEqual(client.get("/api/partners/ciel/tools").json()["tools"], [])


if __name__ == "__main__":
    unittest.main()


class DiscoveryTest(unittest.TestCase):
    def test_netvision_found_from_bat_or_backend_without_user_input(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d) / "NetVision"
            (root / "backend" / "app").mkdir(parents=True)
            (root / "backend" / "app" / "mcp_server.py").write_text("", encoding="utf-8")
            with mock.patch.dict(os.environ, {"NETVISION_HOME": str(root)}):
                got = partners.find_netvision_mcp()
            self.assertEqual(got["cwd"], str(root / "backend"))
            self.assertEqual(got["command"][1:], ["-m", "app.mcp_server"])
            (root / "NetVision-mcp.bat").write_text("", encoding="utf-8")
            with mock.patch.dict(os.environ, {"NETVISION_HOME": str(root)}):
                got = partners.find_netvision_mcp()
            self.assertEqual(got["command"], [str(root / "NetVision-mcp.bat")])

    def test_auto_register_only_when_aas_is_installed(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = Path(d) / "nope" / "aas.config.json"
            with mock.patch.dict(os.environ, {"AAS_CONFIG": str(cfg)}):
                self.assertIsNone(partners.auto_register_aas("http://127.0.0.1:1/mcp"))
                self.assertFalse(cfg.exists())
            cfg.parent.mkdir()
            with mock.patch.dict(os.environ, {"AAS_CONFIG": str(cfg)}):
                r = partners.auto_register_aas("http://127.0.0.1:1/mcp")
            self.assertTrue(r["changed"])
            self.assertIn("company", json.loads(cfg.read_text(encoding="utf-8"))["mcp"]["servers"])


class AggregateMcpTest(unittest.TestCase):
    """Company の /mcp を 1 つ繋げば、子会社の道具も出る（売り）。偽の標準入出力の MCP で。"""

    def test_company_mcp_lists_and_calls_partner_tools(self):
        with tempfile.TemporaryDirectory() as d:
            script = Path(d) / "fake_mcp.py"
            script.write_text(FAKE_MCP, encoding="utf-8")
            entries = [{"id": "ledger", "name": "台帳", "mcp": {"command": [sys.executable, "-I", str(script)], "cwd": d}}]
            with mock.patch.object(partners, "builtin", return_value=[]), \
                 mock.patch.object(partners, "PARTNERS_FILE", Path(d) / "partners.json"):
                partners.save_extra(entries)
                client = TestClient(server.app)
                listed = client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"}).json()
                names = [t["name"] for t in listed["result"]["tools"]]
                self.assertIn("start_cycle", names)
                self.assertIn("ledger__device_summary", names)
                called = client.post("/mcp", json={"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                                                   "params": {"name": "ledger__device_summary", "arguments": {"q": "x"}}}).json()
                self.assertFalse(called["result"]["isError"])
                self.assertIn('"total": 3', called["result"]["content"][0]["text"])
                missing = client.post("/mcp", json={"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                                                    "params": {"name": "nobody__x", "arguments": {}}}).json()
                self.assertTrue(missing["result"]["isError"])
