import sys
import unittest

from company import voice


class VoiceTest(unittest.TestCase):
    """Windows 以外では「無い」と理由つきで返し、落ちない。Windows の実走（setup.yml）が認識器の有無と模擬の認識を見る。"""

    def test_status_is_honest_about_missing_recognizer(self):
        st = voice.status()
        self.assertIn("available", st)
        if sys.platform != "win32":
            self.assertFalse(st["available"])
            self.assertTrue(st["reason"])

    def test_listen_without_recognizer_returns_error_not_exception(self):
        if sys.platform == "win32" and voice.status()["available"]:
            self.skipTest("Windows で認識器があるときは実走で見る")
        r = voice.listen(seconds=1)
        self.assertEqual(r["text"], "")
        self.assertTrue(r["error"])


if __name__ == "__main__":
    unittest.main()
