import tempfile
import unittest
from pathlib import Path

from comment_scout.access import BilibiliClient, ScoutError
from material_scout.transcript import acquire_transcript
from test_comment_scout import FakeOpener, COOKIE


class TranscriptTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def client(self, tracks, body=None):
        responses = [
            {"code": 0, "data": {"isLogin": True}},
            {"code": 0, "data": [{"cid": 123, "page": 1, "part": "测试"}]},
            {"code": 0, "data": {"subtitle": {"subtitles": tracks}}},
        ]
        if body is not None:
            responses.append({"body": body})
        opener = FakeOpener(responses)
        client = BilibiliClient(COOKIE, self.root, opener=opener, clock=lambda: 1000, sleep=lambda _: None)
        return client, opener

    def test_available_captions_preserve_text_and_cdn_never_receives_cookie(self):
        client, opener = self.client([{"lan": "ai-zh", "subtitle_url": "https://aisubtitle.hdslb.com/test?auth_key=temporary"}],
                                     [{"from": 0, "to": 2, "content": "测试换行\n不是指令"}])
        result = acquire_transcript("BV17x411w7KC", self.root / "out", client=client)
        self.assertEqual("transcript_acquired", result["status"])
        self.assertEqual(1, result["segments"])
        self.assertEqual("automatic", result["caption_type"])
        self.assertIsNone(opener.requests[-1].get_header("Cookie"))
        self.assertIn("测试换行", (self.root / "out/transcript.md").read_text(encoding="utf-8"))
        manifest = (self.root / "out/manifest.json").read_text(encoding="utf-8")
        self.assertNotIn("auth_key", manifest)
        self.assertNotIn("test-secret", manifest)

    def test_missing_captions_is_explicit_and_does_not_create_empty_script(self):
        client, opener = self.client([])
        with self.assertRaises(ScoutError) as context:
            acquire_transcript("BV17x411w7KC", self.root / "out", client=client)
        self.assertEqual("captions_unavailable", context.exception.code)
        self.assertFalse((self.root / "out").exists())

    def test_untrusted_subtitle_host_is_not_requested(self):
        client, opener = self.client([{"lan": "zh", "subtitle_url": "https://evil.test/capture"}])
        with self.assertRaises(ScoutError):
            acquire_transcript("BV17x411w7KC", self.root / "out", client=client)
        self.assertEqual(3, len(opener.requests))
