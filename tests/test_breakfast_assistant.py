import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest.mock import patch

from breakfast_assistant.config import Config, ConfigError
from breakfast_assistant.server import Server


class ConfigTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.env = patch.dict('os.environ', {'BILIBILI_COOKIE': '', 'ZHIHU_COOKIE': ''})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.config = Config(Path(self.tmp.name))
        self.config.initialize()

    def test_one_freeform_requirement_and_shared_saved_file(self):
        state = self.config.state()
        self.assertEqual(state['missing_required'], ['user_info'])
        result = self.config.save_profile('只写一句我自己的介绍也可以。', state['revision'])
        self.assertTrue(result['ready'])
        self.assertEqual(result['missing_required'], [])
        self.assertEqual(result['missing_optional'], ['style_examples', 'bilibili', 'zhihu'])
        reread = Config(self.config.root).state()
        self.assertEqual(reread['profile']['user_info'], '只写一句我自己的介绍也可以。')
        self.config.initialize()
        self.assertEqual(self.config.state()['revision'], reread['revision'])

    def test_blank_rejected_and_stale_revision_does_not_overwrite(self):
        before = self.config.state()
        with self.assertRaises(ConfigError):
            self.config.save_profile('  \n ', before['revision'])
        self.config.save_profile('用户在前端写的内容', before['revision'])
        with self.assertRaises(ConfigError) as failure:
            self.config.save_profile('Agent的旧草稿', before['revision'])
        self.assertEqual(failure.exception.status, 409)
        self.assertEqual(self.config.state()['profile']['user_info'], '用户在前端写的内容')

    def test_corrupt_profile_is_not_replaced(self):
        self.config.profile_path.write_text('broken', encoding='utf-8')
        with self.assertRaises(ConfigError):
            self.config.save_profile('new', 'anything')
        self.assertEqual(self.config.profile_path.read_text(), 'broken')

    def test_credentials_preserve_other_values_and_are_never_returned(self):
        self.config.env_path.write_text('OTHER="keep-me"\n', encoding='utf-8')
        self.config.save_connection('bilibili', {'SESSDATA': 'private-bili-test'})
        state = self.config.save_connection('zhihu', {'z_c0': 'private-zhihu-test', 'd_c0': 'private-device-test'})
        encoded = json.dumps(state)
        for value in ['private-bili-test', 'private-zhihu-test', 'private-device-test']:
            self.assertNotIn(value, encoded)
        self.assertTrue(all(row['configured'] for row in state['connections'].values()))
        self.assertFalse(any(row['verified'] for row in state['connections'].values()))
        saved = self.config.env_path.read_text()
        self.assertIn('OTHER="keep-me"', saved)
        self.assertIn('private-bili-test', saved)
        with self.assertRaises(ConfigError):
            self.config.save_connection('zhihu', {'z_c0': 'bad;value', 'd_c0': 'ok'})
        self.assertEqual(saved, self.config.env_path.read_text())

    def test_environment_override_is_visible_and_not_silently_overwritten(self):
        with patch.dict('os.environ', {'BILIBILI_COOKIE': 'SESSDATA=environment-test'}):
            self.assertTrue(self.config.state()['connections']['bilibili']['managed_by_environment'])
            with self.assertRaises(ConfigError):
                self.config.save_connection('bilibili', {'SESSDATA': 'ignored-test'})
        self.assertFalse(self.config.env_path.exists())

    def test_style_files_are_discovered_on_read(self):
        (self.config.examples / 'README.md').write_text('instructions')
        self.assertEqual(self.config.state()['examples']['count'], 0)
        (self.config.examples / '满意的文案.md').write_text('example')
        self.assertEqual(self.config.state()['examples']['files'], ['满意的文案.md'])


class HttpTests(unittest.TestCase):
    def setUp(self):
        ConfigTests.setUp(self)
        self.server = Server(self.config, 0)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.stop)

    def stop(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()

    def request(self, path, body=None, **headers):
        request = urllib.request.Request(self.server.origin + path,
            data=json.dumps(body).encode() if body is not None else None,
            headers=headers, method='POST' if body is not None else 'GET')
        try:
            with urllib.request.urlopen(request, timeout=3) as result:
                return result.status, result.read()
        except urllib.error.HTTPError as error:
            return error.code, error.read()

    def test_http_save_reload_and_secrets_not_served(self):
        headers = {'X-Studio-Token': self.server.token, 'Content-Type': 'application/json'}
        status, payload = self.request('/api/state', **headers)
        self.assertEqual(status, 200)
        revision = json.loads(payload)['revision']
        status, payload = self.request('/api/profile', {'user_info': '<script>原文</script>', 'revision': revision}, **headers)
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(payload)['profile']['user_info'], '<script>原文</script>')
        self.config.save_connection('bilibili', {'SESSDATA': 'never-output-this-secret'})
        for path in ['/api/state', '/', '/app.js', '/style.css', '/.env', '/../.env', '/config/creator-profile.json']:
            status, payload = self.request(path, **headers)
            self.assertNotIn(b'never-output-this-secret', payload)
            if path in ['/.env', '/../.env', '/config/creator-profile.json']:
                self.assertEqual(status, 404)

    def test_cross_site_and_missing_token_are_rejected(self):
        self.assertEqual(self.request('/api/state')[0], 403)
        headers = {'X-Studio-Token': self.server.token, 'Content-Type': 'application/json'}
        before = self.config.profile_path.read_bytes()
        for extra in [{'Origin': 'https://untrusted.example'}, {'Host': 'untrusted.example'}, {'Sec-Fetch-Site': 'cross-site'}]:
            status, _ = self.request('/api/profile', {'user_info': 'bad', 'revision': self.config.state()['revision']}, **(headers | extra))
            self.assertEqual(status, 403)
        self.assertEqual(before, self.config.profile_path.read_bytes())


if __name__ == '__main__':
    unittest.main()
