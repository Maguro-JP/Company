import unittest

from company import engines, hardware


class HardwareTest(unittest.TestCase):
    def test_memory_and_summary_do_not_crash(self):
        st = hardware.status()
        self.assertIn("memory", st)
        self.assertIn("summary", st)
        self.assertTrue(st["summary"])

    def test_fit_says_why_a_big_model_does_not_fit(self):
        mem = {"total": 8 * hardware.GB, "free": 6 * hardware.GB}
        f = hardware.fit(19_851_335_872, 8192, mem, [], "cpu")
        self.assertFalse(f["fits"])
        self.assertIn("入りません", f["note"])
        self.assertIn("Strata", f["note"])

    def test_fit_warns_when_free_memory_is_short(self):
        mem = {"total": 16 * hardware.GB, "free": 2 * hardware.GB}
        f = hardware.fit(4_683_073_536, 8192, mem, [], "cpu")
        self.assertFalse(f["fits"])
        self.assertIn("空き", f["note"])

    def test_fit_prefers_gpu_when_backend_is_cuda_and_vram_is_enough(self):
        mem = {"total": 32 * hardware.GB, "free": 20 * hardware.GB}
        g = [{"name": "RTX", "vram_total": 24 * hardware.GB, "vram_free": 20 * hardware.GB}]
        f = hardware.fit(8_988_110_272, 8192, mem, g, "cuda")
        self.assertTrue(f["fits"])
        self.assertEqual(f["where"], "gpu")

    def test_recommended_is_the_largest_catalog_model_that_fits(self):
        from company import local_llm
        ids = [m["id"] for m in local_llm.list_models(4096)]
        self.assertIn("qwen2.5-coder-32b", ids)
        self.assertIn("qwen2.5-coder-14b", ids)
        rec = local_llm.recommended(4096)
        self.assertTrue(rec is None or rec in ids)


class EnginesTest(unittest.TestCase):
    def test_candidates_include_strata_and_do_not_include_builtin(self):
        ids = [c["id"] for c in engines.candidates()]
        self.assertIn("strata", ids)
        self.assertIn("ollama", ids)
        self.assertNotIn("builtin", ids)

    def test_probe_of_closed_port_is_not_alive_and_is_quick(self):
        import time
        t = time.time()
        r = engines.probe({"id": "x", "label": "x", "base_url": "http://127.0.0.1:9/v1"}, timeout=0.3)
        self.assertFalse(r["alive"])
        self.assertLess(time.time() - t, 3)

    def test_detect_finds_a_fake_openai_server(self):
        import json
        import threading
        from http.server import BaseHTTPRequestHandler, HTTPServer

        class H(BaseHTTPRequestHandler):
            def do_GET(self):
                body = json.dumps({"data": [{"id": "big-model"}]}).encode()
                self.send_response(200); self.send_header("Content-Type", "application/json"); self.end_headers(); self.wfile.write(body)

            def log_message(self, *a):
                pass

        srv = HTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        try:
            r = engines.probe({"id": "strata", "label": "Strata", "base_url": f"http://127.0.0.1:{srv.server_port}/v1"})
            self.assertTrue(r["alive"])
            self.assertEqual(r["models"], ["big-model"])
        finally:
            srv.shutdown()


if __name__ == "__main__":
    unittest.main()
