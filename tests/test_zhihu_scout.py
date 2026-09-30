import io
import json
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import patch

from zhihu_scout.access import Client, ZhihuError, endpoint_url, load_cookie, save_cookie, validate_url
from zhihu_scout.jobs import Session, create_session, crawl
from zhihu_scout.model import next_url, plain, search_questions, target
from zhihu_scout.reading import export
from zhihu_scout.signing import headers as sign_headers


COOKIE = 'z_c0=test-secret; d_c0="test-device"; _xsrf=test-xsrf'


def answer(rid, likes, content="回答原文"):
    return {"id": rid, "type": "answer", "content": content, "voteup_count": likes,
            "comment_count": 3, "question": {"id": 1, "title": "测试问题"}}


def comment(rid, likes, root=None, children=0):
    return {"id": rid, "type": "comment", "content": f"<p>原句{rid}，不改写。</p>",
            "like_count": likes, "reply_comment_id": root, "child_comment_count": children}


def page(items, following=None, total=None):
    return {"data": items, "paging": {"is_end": following is None, "next": following, "totals": total}}


class FakeClient:
    def __init__(self, responses):
        self.responses = list(responses)
        self.urls = []
        self.request_count = 0

    def get(self, url):
        self.urls.append(url)
        self.request_count += 1
        result = self.responses.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


class Open:
    def __init__(self, values):
        self.values = list(values)
        self.requests = []

    def open(self, request, timeout):
        self.requests.append(request)
        result = self.values.pop(0)
        if isinstance(result, Exception):
            raise result
        return io.BytesIO(json.dumps(result).encode())


class ZhihuTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.config = {"mode": "fetch", "target_kind": "question", "target_id": "1",
                       "min_votes": 100, "top_answers": 1, "answer_pages": 2,
                       "comment_pages": 2, "reply_pages": 2, "replies": True}
        self.sessions = []

    def tearDown(self):
        for session in self.sessions:
            session.close()
        self.temp.cleanup()

    def session(self, config=None):
        result = create_session(self.root, config or self.config)
        self.sessions.append(result)
        return result

    def test_credentials_preserve_other_config_and_no_echo(self):
        path = self.root / ".env"
        path.write_text("OTHER=unchanged\n", encoding="utf-8")
        save_cookie(path, COOKIE)
        with patch.dict("os.environ", {"ZHIHU_COOKIE": ""}):
            self.assertEqual(load_cookie(path), COOKIE)
        self.assertIn("OTHER=unchanged", path.read_text())
        with self.assertRaises(ZhihuError) as error:
            save_cookie(path, "z_c0=do-not-leak")
        result = error.exception.result()
        self.assertEqual(result["next_action"], "ask_user_to_provide_credentials")
        self.assertNotIn("do-not-leak", json.dumps(result))

    def test_target_and_allowlist(self):
        self.assertEqual(target("https://www.zhihu.com/question/1/answer/2"), ("answer", "2"))
        for url in ("https://evil.test/api/v4/me", "http://www.zhihu.com/api/v4/me",
                    "https://www.zhihu.com@evil.test/api/v4/me", "https://www.zhihu.com/api/v4/me/followers"):
            with self.assertRaises(ZhihuError):
                validate_url(url)

    def test_opaque_cursor_preserved_and_foreign_next_rejected(self):
        url = endpoint_url("/api/v4/comment_v5/answers/2/root_comment")
        following = url + "?offset=123_abc%2B%2F%3D&order_by=score"
        self.assertEqual(next_url(page([comment(10, 5)], following), url), following)
        for following in ("https://evil.test/api/v4/me", endpoint_url("/api/v4/me"), url):
            with self.assertRaises(ZhihuError):
                next_url(page([comment(10, 5)], following), url)

    def test_api_host_cursor_maps_to_same_web_endpoint(self):
        current = endpoint_url("/api/v4/search_v3", {"q": "test"})
        following = "https://api.zhihu.com/search_v3?offset=19&search_hash_id=abc&vertical_info=0%2C0"
        expected = "https://www.zhihu.com/api/v4/search_v3?offset=19&search_hash_id=abc&vertical_info=0%2C0"
        self.assertEqual(next_url(page([{"id": 1}], following), current), expected)

    def test_pacing_and_persistent_restriction(self):
        clock = [100.0]
        delays = []
        def sleep(delay):
            delays.append(delay)
            clock[0] += delay
        opener = Open([{"id": "1"}, {"id": "1"}, urllib.error.HTTPError("", 403, "SECRET", {}, None)])
        client = Client(COOKIE, self.root, opener=opener, clock=lambda: clock[0], sleep=sleep)
        client.verify()
        client.verify()
        self.assertEqual(delays, [3.0])
        with self.assertRaises(ZhihuError) as error:
            client.verify()
        self.assertEqual(error.exception.code, "access_restricted")
        self.assertNotIn("SECRET", json.dumps(error.exception.result()))
        with self.assertRaises(ZhihuError):
            Client(COOKIE, self.root, opener=opener, clock=lambda: clock[0]).verify()
        self.assertEqual(len(opener.requests), 3)

    def test_auth_error_is_distinct_from_restriction(self):
        client = Client(COOKIE, self.root, opener=Open([urllib.error.HTTPError("", 401, "secret", {}, None)]))
        with self.assertRaises(ZhihuError) as error:
            client.verify()
        self.assertEqual(error.exception.code, "auth_expired")

    def test_signature_error_diagnostic_retains_code_without_raw_message(self):
        body = io.BytesIO(json.dumps({"error": {"code": 10003, "message": "x-zse-96 secret-value"}}).encode())
        client = Client(COOKIE, self.root, opener=Open([urllib.error.HTTPError("", 403, "", {}, body)]))
        with self.assertRaises(ZhihuError) as error:
            client.verify()
        result = error.exception.result()
        self.assertEqual(result["api_code"], 10003)
        self.assertEqual(result["hint"], "request_signature_required")
        self.assertNotIn("secret-value", json.dumps(result))

    def test_signature_vector_and_credentials_stay_out_of_argv(self):
        value = sign_headers("https://www.zhihu.com/api/v4/questions/1", "z_c0=fake; d_c0=fake")
        self.assertEqual(value["x-zse-96"], "2.0_WoNNmPbkTpSFGaJwgWqxU3BU25wyLjUfU2O3X4m8QPk94Wvr6sUQD=//vjpzO4ub")
        from types import SimpleNamespace
        with patch("zhihu_scout.signing.subprocess.run", return_value=SimpleNamespace(returncode=0, stdout=json.dumps(value))) as run:
            sign_headers("https://www.zhihu.com/api/v4/questions/1", COOKIE)
        call = run.call_args
        self.assertNotIn("test-secret", str(call))
        self.assertNotIn("test-device", str(call.args))
        self.assertIn("test-device", call.kwargs["input"])

    def test_search_questions_from_answers_and_questions(self):
        items = [{"object": {**answer(2, 200), "excerpt": "<em>搜索原句</em>"}},
                 {"object": {"id": 3, "type": "question", "title": "另一个问题"}},
                 {"object": {"id": 4, "type": "article"}}]
        rows, skipped = search_questions(items)
        self.assertEqual([r["kind"] for r in rows], ["question", "hit", "question"])
        self.assertEqual(rows[1]["text"], "搜索原句")
        self.assertEqual(skipped, 1)

    def test_search_question_name_field(self):
        obj = {**answer(2, 200), "question": {"id": "1", "name": "<em>问题</em>标题"}}
        rows, _ = search_questions([{"object": obj}])
        self.assertEqual(rows[0]["title"], "问题标题")

    def test_inspect_returns_metrics_without_fetching_answers_and_preserves_missing(self):
        session = self.session({"mode": "inspect", "question_ids": ["1", "2"]})
        client = FakeClient([{"id": 1, "title": "热门", "answer_count": 2121,
                              "visit_count": "10069914", "follower_count": 4070, "comment_count": 0},
                             {"id": 2, "title": "未知"}])
        result = crawl(session, client, 1)
        self.assertEqual(result["status"], "budget_reached")
        result = crawl(session, client, 1)
        self.assertEqual(result["status"], "scope_complete")
        self.assertEqual(result["counts"], {"question": 2})
        first, second = result["candidates"]
        self.assertEqual(first["visit_count"], 10069914)
        self.assertEqual(first["comment_count"], 0)
        self.assertIsNone(second["visit_count"])
        self.assertEqual(first["metrics_source"], "question_detail")
        self.assertIsNotNone(first["fetched_at"])
        self.assertTrue(all("/answers" not in url for url in client.urls))

    def test_search_placeholder_zero_is_not_used_as_verified_count(self):
        rows, _ = search_questions([{"object": {"type": "question", "id": 1, "title": "占位",
                                                  "answer_count": 0, "visit_count": 0}}])
        self.assertIsNone(rows[0]["answer_count"])
        self.assertEqual(rows[0]["search_metrics"]["answer_count"], 0)
        self.assertEqual(rows[0]["metrics_source"], "search_unverified")

    def test_rank_after_all_answer_pages_then_comments_and_context(self):
        session = self.session()
        following = endpoint_url("/api/v4/questions/1/answers", {"offset": 20})
        first = FakeClient([{"id": 1, "title": "测试"}, page([answer(2, 100)], following)])
        result = crawl(session, first, 2)
        self.assertEqual(result["status"], "budget_reached")
        self.assertEqual(session.meta()["selected_answer_ids"], [])
        rest = FakeClient([page([answer(3, 500)]), page([]),
                           page([comment(10, 1, children=1)]), page([comment(11, 9, root=10)])])
        result = crawl(session, rest, 10)
        self.assertEqual(result["status"], "scope_complete")
        self.assertEqual(session.meta()["selected_answer_ids"], ["3"])
        self.assertFalse(any("answers/2/root_comment" in url for url in rest.urls))
        report = export(session, self.root / "reading")
        self.assertEqual((report["primary_comments"], report["context_comments"]), (1, 1))
        selected = [json.loads(line) for line in (self.root / "reading/selected.jsonl").read_text(encoding="utf-8").splitlines()]
        self.assertEqual([r["id"] for r in selected if r["kind"] == "comment"], ["10", "11"])
        self.assertIn("原句11，不改写。", (self.root / "reading/reader-001.md").read_text(encoding="utf-8"))

    def test_empty_score_fallback_is_saved_and_time_cursor_used(self):
        config = {**self.config, "target_kind": "answer", "target_id": "2"}
        session = self.session(config)
        following = endpoint_url("/api/v4/comment_v5/answers/2/root_comment", {"order_by": "time", "offset": "opaque_A_1"})
        client = FakeClient([answer(2, 100), page([], total=2), page([comment(10, 9)], following), page([comment(11, 8)])])
        result = crawl(session, client, 10)
        self.assertEqual(result["status"], "scope_complete")
        self.assertIn("order_by=time", client.urls[2])
        self.assertEqual(client.urls[3], following)
        self.assertEqual(result["saved_pages"], 4)
        self.assertEqual(result["warnings"][0]["code"], "score_empty_fallback_time")

    def test_invalid_page_not_committed_and_resume_preserves_task(self):
        session = self.session({"mode": "search", "query": "test", "search_pages": 2})
        client = FakeClient([{"data": [], "paging": {"is_end": False}}])
        result = crawl(session, client, 1)
        self.assertEqual(result["status"], "incomplete_page")
        self.assertEqual(result["saved_pages"], 0)
        self.assertEqual(len(list((session.path / "rejected-pages").glob("*.json"))), 1)
        result = crawl(session, FakeClient([page([])]), 1)
        self.assertEqual(result["status"], "scope_complete")

    def test_page_limit_explicit_and_source_preserved(self):
        session = self.session({"mode": "search", "query": "test", "search_pages": 1})
        payload = page([{"object": {"id": 1, "type": "question", "title": "原问题"}}], endpoint_url("/api/v4/search_v3", {"offset": 20}))
        result = crawl(session, FakeClient([payload]), 1)
        self.assertEqual(len(result["limited_streams"]), 1)
        self.assertEqual(result["status"], "scope_complete")
        report = export(session, self.root / "reading")
        saved = json.loads((self.root / "reading/raw-pages.jsonl").read_text(encoding="utf-8"))
        self.assertEqual(saved["payload"], payload)
        self.assertEqual(report["collection"]["limited_streams"], result["limited_streams"])

    def test_request_failure_keeps_pending_and_previous_pages(self):
        session = self.session()
        client = FakeClient([{"id": 1, "title": "测试"}, ZhihuError("network_error", "故障")])
        result = crawl(session, client, 10)
        self.assertEqual(result["saved_pages"], 1)
        self.assertEqual(result["status"], "network_error")
        self.assertEqual(session.next_task()["kind"], "answers")

    def test_original_long_answer_split_without_loss(self):
        config = {**self.config, "target_kind": "answer", "target_id": "2"}
        session = self.session(config)
        content = "甲乙丙丁" * 1000
        crawl(session, FakeClient([answer(2, 100, content), page([])]), 10)
        report = export(session, self.root / "reading", pack_chars=1000)
        self.assertGreater(report["reader_files"], 1)
        rows = [json.loads(s) for s in (self.root / "reading/selected.jsonl").read_text(encoding="utf-8").splitlines()]
        self.assertEqual(next(r for r in rows if r["kind"] == "answer")["text"], content)
        self.assertEqual(plain("<p>第一段</p><script>丢弃</script><p>第二段&amp;原文</p>"), "第一段\n第二段&原文")

    def test_reader_caps_each_source_including_context_and_omits_repeated_metadata(self):
        session = self.session()
        with session.db:
            rows = [{"kind": "question", "id": "1", "title": "问题只出现一次", "text": "问题描述只出现一次",
                     "source_url": "https://www.zhihu.com/question/1"}]
            for aid in ("2", "3"):
                url = f"https://www.zhihu.com/question/1/answer/{aid}"
                rows.append({"kind": "answer", "id": aid, "question_id": "1", "title": "问题只出现一次",
                             "text": "回答原文" + aid, "likes": 100, "text_status": "content", "source_url": url})
                for n in range(15):
                    rid = str(int(aid) * 100 + n)
                    rows.append({"kind": "comment", "id": rid, "root_id": rid, "parent_id": None,
                                 "text": "评论原文" + rid, "likes": 100 - n, "source_url": url, "author": "冗余作者"})
                rows[-1].update(likes=200, root_id=str(int(aid) * 100), parent_id=str(int(aid) * 100))
                rows[-15]["likes"] = 1
            for r in rows:
                session.db.execute("INSERT INTO records VALUES (?,?,?)", (r["kind"], r["id"], json.dumps(r)))
            meta = session.meta()
            meta["selected_answer_ids"] = ["2", "3"]
            session.save_meta(meta)
        report = export(session, self.root / "compact")
        self.assertEqual(list(report["comment_counts_by_source"].values()), [10, 10])
        self.assertEqual(report["primary_comments"] + report["context_comments"], 20)
        reader = (self.root / "compact/reader-001.md").read_text(encoding="utf-8")
        self.assertEqual(reader.count("问题只出现一次"), 1)
        self.assertEqual(reader.count("问题描述只出现一次"), 1)
        self.assertEqual(reader.count("https://www.zhihu.com/question/1/answer/2"), 1)
        self.assertNotIn("冗余作者", reader)
        self.assertNotIn("root_id", reader)
        self.assertIn("回复评论", reader)
        self.assertLess(reader.index("评论原文214"), reader.index("回答原文3"))


if __name__ == "__main__":
    unittest.main()
