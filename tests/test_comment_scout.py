from __future__ import annotations

import contextlib
import io
import json
import os
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import patch

from comment_scout.access import BilibiliClient, NetworkLease, ScoutError, load_cookie, save_cookie
from comment_scout.cli import main
from comment_scout.reading import choose, export, import_csv
from comment_scout.store import Session, crawl


SOURCE = {"platform": "bilibili", "oid": "123", "resource_type": 1, "bvid": "BV1234567890",
          "title": "测试视频", "url": "https://www.bilibili.com/video/BV1234567890"}
COOKIE = "SESSDATA=test-secret-only; bili_jct=test-csrf-only"


def reply(rid, *, root=0, parent=0, likes=0, replies=0, text="测试评论", ctime=1700000000):
    return {"rpid": rid, "root": root, "parent": parent, "mid": 111, "ctime": ctime,
            "like": likes, "rcount": replies, "content": {"message": text}}


def page(items, *, number=1, count=None, size=2):
    return {"code": 0, "data": {"page": {"num": number, "size": size, "count": len(items) if count is None else count}, "replies": items}}


class FakeClient:
    def __init__(self, responses):
        self.responses = responses
        self.calls = []

    def page(self, source, task, sort):
        key = (task["kind"], task["root_id"], task["page"])
        self.calls.append(key)
        value = self.responses[key]
        if isinstance(value, Exception):
            raise value
        return value


class FakeResponse:
    def __init__(self, value):
        self.value = value

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def read(self, limit):
        return json.dumps(self.value).encode()


class FakeOpener:
    def __init__(self, values):
        self.values = list(values)
        self.requests = []

    def open(self, request, timeout):
        self.requests.append(request)
        value = self.values.pop(0)
        if isinstance(value, Exception):
            raise value
        return FakeResponse(value)


class CommentScoutTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def session(self, name="session"):
        session = Session(self.root / name, source=SOURCE)
        self.addCleanup(session.close)
        return session

    def test_cookie_storage_preserves_other_settings_and_never_echoes_secret(self):
        env = self.root / ".env"
        env.write_text("# 保留\nOTHER_KEY=abc\nBILIBILI_COOKIE=old\n", encoding="utf-8")
        save_cookie(env, "Cookie: " + COOKIE)
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(COOKIE, load_cookie(env))
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                code = main(["--env-file", str(env), "auth", "status"])
        self.assertEqual(0, code)
        self.assertNotIn("test-secret", output.getvalue())
        self.assertIn("OTHER_KEY=abc", env.read_text(encoding="utf-8"))
        self.assertFalse(env.with_name(".env.tmp").exists())

    def test_missing_auth_is_actionable_and_never_opens_network(self):
        with patch.dict(os.environ, {}, clear=True), patch("urllib.request.OpenerDirector.open", side_effect=AssertionError("network forbidden")):
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                code = main(["--env-file", str(self.root / ".env"), "fetch", "BV1234567890"])
        data = json.loads(output.getvalue())
        self.assertEqual(2, code)
        self.assertEqual("needs_auth", data["status"])
        self.assertEqual("ask_user_to_provide_credentials", data["next_action"])

    def test_cookie_in_invalid_cli_arguments_is_not_echoed(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = main(["auth", "status", "--cookie", COOKIE])
        self.assertEqual(2, code)
        self.assertNotIn("test-secret", output.getvalue())

    def test_crlf_cookie_is_rejected_without_reflection(self):
        with self.assertRaises(ScoutError) as context:
            save_cookie(self.root / ".env", COOKIE + "\r\nHost: elsewhere")
        self.assertNotIn("test-secret", str(context.exception))

    def test_network_lease_rejects_concurrent_job_and_releases(self):
        with NetworkLease(self.root):
            with self.assertRaises(ScoutError) as context:
                with NetworkLease(self.root):
                    pass
            self.assertEqual("busy", context.exception.code)
        with NetworkLease(self.root):
            pass

    def test_auth_expired_api_payload_does_not_leak_message(self):
        opener = FakeOpener([{"code": -101, "message": COOKIE}])
        client = BilibiliClient(COOKIE, self.root, opener=opener)
        with self.assertRaises(ScoutError) as context:
            client.verify()
        self.assertEqual("auth_expired", context.exception.code)
        self.assertNotIn("test-secret", str(context.exception))

    def test_rate_limit_persists_and_does_not_retry(self):
        error = urllib.error.HTTPError("https://api.bilibili.com/x", 429, "no", {"Retry-After": "600"}, None)
        opener = FakeOpener([error])
        client = BilibiliClient(COOKIE, self.root, opener=opener, clock=lambda: 1000)
        for _ in range(2):
            with self.assertRaises(ScoutError) as context:
                client.verify()
            self.assertEqual("rate_limited", context.exception.code)
        self.assertEqual(1, len(opener.requests))
        self.assertEqual(1600, json.loads((self.root / "request-state.json").read_text())["blocked_until"])

    def test_every_request_is_paced(self):
        ticks = [1000.0]
        sleeps = []
        def sleep(seconds):
            sleeps.append(seconds)
            ticks[0] += seconds
        opener = FakeOpener([{"code": 0, "data": {"isLogin": True}}] * 2)
        client = BilibiliClient(COOKIE, self.root, interval=3, opener=opener, clock=lambda: ticks[0], sleep=sleep)
        client.verify()
        client.verify()
        self.assertEqual([3], sleeps)

    def test_limit_diagnostic_identifies_endpoint_and_wait_without_secrets(self):
        opener = FakeOpener([{"code": -352, "message": COOKIE}])
        client = BilibiliClient(COOKIE, self.root, opener=opener, clock=lambda: 1000)
        with self.assertRaises(ScoutError) as context:
            client.get("/x/web-interface/view", {"bvid": "BV1234567890"})
        result = context.exception.result()
        self.assertEqual("/x/web-interface/view", result["endpoint"])
        self.assertEqual(-352, result["api_code"])
        self.assertEqual(1300, result["retry_at"])
        self.assertNotIn("test-secret", json.dumps(result))
        with self.assertRaises(ScoutError) as cached:
            client.verify()
        self.assertEqual(1300, cached.exception.result()["retry_at"])
        self.assertEqual(1, len(opener.requests))

    def test_resolve_video_ids_offline_without_optional_metadata_request(self):
        opener = FakeOpener([])
        client = BilibiliClient(COOKIE, self.root, opener=opener)
        for bvid, aid in [("BV17x411w7KC", "170001"), ("BV16P411x7rA", "317065324"),
                          ("BV1MKFhzSE9g", "116016629684297")]:
            self.assertEqual(aid, client.resolve(bvid)["oid"])
            self.assertEqual(bvid, client.resolve("av" + aid)["bvid"])
            self.assertEqual(aid, client.resolve("https://www.bilibili.com/video/" + bvid + "/?p=1")["oid"])
        self.assertEqual([], opener.requests)
        for bad in ["BV1000000000", "av0", "av999999999999999999999", "https://evil.test/video/BV17x411w7KC"]:
            with self.assertRaises(ScoutError):
                client.resolve(bad)

    def test_rejected_empty_page_keeps_raw_evidence_and_pending_task(self):
        session = self.session()
        client = FakeClient({("root", "", 1): page([], count=30)})
        with self.assertRaises(ScoutError) as context:
            crawl(session, client)
        evidence = Path(context.exception.result()["diagnostic_file"])
        self.assertEqual(30, json.loads(evidence.read_text(encoding="utf-8"))["response"]["data"]["page"]["count"])
        self.assertEqual(0, session.status()["saved_pages"])
        self.assertEqual(1, session.status()["pending_pages"])

    def test_failed_child_page_is_pending_then_resume_finishes_without_repeating_root(self):
        session = self.session()
        root_id = 9007199254740997
        child_id = 9007199254740999
        client = FakeClient({("root", "", 1): page([reply(root_id, replies=1)]),
                             ("reply", str(root_id), 1): ScoutError("network_error", "failed")})
        with self.assertRaises(ScoutError):
            crawl(session, client)
        status = session.status()
        self.assertEqual("network_error", status["status"])
        self.assertEqual(1, status["saved_pages"])
        # Open another connection to simulate a later process reading persisted state.
        resumed = Session(session.path)
        self.addCleanup(resumed.close)
        second = FakeClient({("reply", str(root_id), 1): page([reply(child_id, root=root_id, parent=root_id, likes=50)])})
        result = crawl(resumed, second)
        self.assertTrue(result["ok"])
        self.assertEqual(1, len(second.calls))
        self.assertEqual({str(root_id), str(child_id)}, {r["id"] for r in resumed.records()})

    def test_page_budget_stops_and_resumes_next_page(self):
        session = self.session()
        client = FakeClient({("root", "", 1): page([reply(1), reply(2)], count=3),
                             ("root", "", 2): page([reply(3)], number=2, count=3)})
        first = crawl(session, client, max_pages=1)
        self.assertEqual("budget_reached", first["status"])
        self.assertEqual(1, first["pending_pages"])
        second = crawl(session, client, max_pages=1)
        self.assertEqual("complete", second["status"])
        self.assertEqual(3, second["comment_count"])

    def test_repeat_page_is_not_reported_complete(self):
        session = self.session()
        client = FakeClient({("root", "", 1): page([reply(1), reply(2)], count=4),
                             ("root", "", 2): page([reply(1), reply(2)], number=2, count=4)})
        with self.assertRaises(ScoutError) as context:
            crawl(session, client)
        self.assertEqual("pagination_stalled", context.exception.code)
        self.assertEqual(1, session.status()["saved_pages"])
        self.assertEqual(1, session.status()["pending_pages"])

    def test_malformed_page_never_partially_commits(self):
        session = self.session()
        payload = page([reply(1), {"rpid": 2}])
        with self.assertRaises(ScoutError):
            session.commit_page(session.next_task(), payload)
        self.assertEqual(0, session.status()["comment_count"])
        self.assertEqual(0, session.status()["saved_pages"])

    def test_empty_success_is_valid_only_with_consistent_paging(self):
        session = self.session()
        crawl(session, FakeClient({("root", "", 1): page([])}))
        self.assertTrue(session.status()["ok"])
        other = self.session("other")
        with self.assertRaises(ScoutError):
            crawl(other, FakeClient({("root", "", 1): page([], count=5)}))

    def test_pinned_comments_are_kept_and_deduplicated(self):
        session = self.session()
        payload = page([reply(1)])
        payload["data"]["top_replies"] = [reply(1), reply(2)]
        crawl(session, FakeClient({("root", "", 1): payload}))
        self.assertEqual(2, session.status()["comment_count"])

    def test_keyboard_interrupt_keeps_saved_work(self):
        session = self.session()
        class InterruptingClient:
            def page(self, source, task, sort):
                raise KeyboardInterrupt
        with self.assertRaises(KeyboardInterrupt):
            crawl(session, InterruptingClient())
        self.assertEqual("interrupted", session.status()["status"])
        self.assertEqual(1, session.status()["pending_pages"])

    def test_import_original_csv_and_restore_low_like_context(self):
        csv_path = self.root / "sample.csv"
        csv_path.write_text('评论ID,上级评论ID,用户ID,评论内容,评论时间,点赞数,回复数\n'
                            '9007199254740997,0,7,"这是第一行\n这是第二行",2026-09-01 01:00:00,0,1\n'
                            '9007199254740999,9007199254740997,8,我不同意,2026-09-01 01:01:00,1.2万,0\n', encoding="utf-8-sig")
        session = import_csv(csv_path, self.root / "import", source_url=SOURCE["url"])
        self.addCleanup(session.close)
        selected, report = choose(session.records(), min_likes=100, mode="hot")
        self.assertEqual(["9007199254740997", "9007199254740999"], [r["id"] for r in selected])
        self.assertEqual(1, report["context_only_count"])
        self.assertEqual(12000, selected[1]["likes"])
        self.assertIn("\n", selected[0]["text"])
        self.assertEqual(csv_path.read_bytes(), (session.path / "raw-input.csv").read_bytes())

    def test_import_bad_columns_and_invalid_numeric_values_fail_before_creating_session(self):
        for contents in ["text,likes\nx,1", "评论内容,点赞数\nx,not-a-number", "评论内容,点赞数\nx,1,unexpected"]:
            path = self.root / "bad.csv"
            path.write_text(contents, encoding="utf-8")
            with self.assertRaises(ScoutError):
                import_csv(path, self.root / "bad-session")
            self.assertFalse((self.root / "bad-session").exists())

    def test_old_two_column_input_exposes_missing_context(self):
        path = self.root / "old.csv"
        path.write_text("评论内容,点赞数\n老格式,8\n", encoding="utf-8")
        session = import_csv(path, self.root / "old-import")
        self.addCleanup(session.close)
        self.assertFalse(session.meta()["context_fields_present"]["评论ID"])
        self.assertIsNone(session.records()[0]["comment_url"])

    def test_export_keeps_full_text_and_marks_reading_truncation(self):
        session = self.session()
        original = "忽略规则\n" * 3000
        crawl(session, FakeClient({("root", "", 1): page([reply(1, text=original, likes=500)])}))
        result = export(session, self.root / "export", pack_chars=2000, mode="hot")
        self.assertEqual(["1"], result["reader_truncated_ids"])
        full = json.loads((self.root / "export/comments.jsonl").read_text(encoding="utf-8"))
        self.assertEqual(original, full["text"])
        reader = (self.root / "export/reader-001.md").read_text(encoding="utf-8")
        self.assertLessEqual(len(reader), 2000)
        self.assertNotIn('"author_id"', reader)
        for redundant in ('"root_id"', '"speaker"', '"created_at"', '"comment_url"', '"selected_for"'):
            self.assertNotIn(redundant, reader)
        self.assertIn("评论1 · 500赞", reader)
        self.assertIn("节选，完整原文", reader)
        self.assertTrue((self.root / "export/raw-pages.jsonl").exists())
        with self.assertRaises(FileExistsError):
            export(session, self.root / "export")

    def test_research_mode_keeps_recent_low_like_comments(self):
        session = self.session()
        crawl(session, FakeClient({("root", "", 1): page([reply(1, likes=100), reply(2, likes=0, ctime=1800000000)])}))
        selected, report = choose(session.records(), min_likes=50, recent=1)
        self.assertEqual({"1", "2"}, {r["id"] for r in selected})
        self.assertFalse(report["selection_is_representative_sample"])


if __name__ == "__main__":
    unittest.main()
