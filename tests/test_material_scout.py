from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from material_scout.models import AssetCandidate, AssetKind, RightsStatus
from material_scout.providers import BilibiliSearchAdapter, YtDlpSearchAdapter
from material_scout.service import MaterialScout, load_candidates, select_candidates
from material_scout.watermarks import decide_mark_treatment


class MaterialScoutTests(unittest.TestCase):
    def test_production_acquisition_requires_recorded_rights(self) -> None:
        candidate = AssetCandidate(
            candidate_id="youtube:abc",
            kind=AssetKind.VIDEO,
            source="youtube",
            remote_id="abc",
            title="Example",
            source_url="https://example.test/abc",
            search_query="example",
        )

        def runner(_: list[str]) -> subprocess.CompletedProcess[str]:
            raise AssertionError("Downloader must not run before the rights gate")

        with tempfile.TemporaryDirectory() as directory:
            scout = MaterialScout(Path(directory) / "library", command_runner=runner)
            with self.assertRaises(PermissionError):
                scout.acquire(
                    candidate,
                    RightsStatus.UNKNOWN,
                    purpose="production",
                    media="master",
                )

    def test_bilibili_search_retries_transient_412(self) -> None:
        payload = {"entries": [{"id": "BV1test", "title": "Recovered"}]}
        attempts = 0

        def runner(_: list[str]) -> subprocess.CompletedProcess[str]:
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                return subprocess.CompletedProcess([], 1, "", "HTTP Error 412: Precondition Failed")
            return subprocess.CompletedProcess([], 0, json.dumps(payload), "")

        adapter = YtDlpSearchAdapter(
            "bilibili", "bilisearch", runner, retry_delay_seconds=0
        )
        candidates = adapter.search("AI agent video editing", 1)
        self.assertEqual(2, attempts)
        self.assertEqual("BV1test", candidates[0].remote_id)

    def test_bilibili_search_enriches_cover_and_bvid(self) -> None:
        search_payload = {
            "entries": [
                {
                    "id": "12345",
                    "title": "Flat result",
                    "url": "http://www.bilibili.com/video/av12345",
                }
            ]
        }

        def runner(_: list[str]) -> subprocess.CompletedProcess[str]:
            return subprocess.CompletedProcess([], 0, json.dumps(search_payload), "")

        def metadata(_: str) -> dict:
            return {
                "aid": 12345,
                "bvid": "BV1test",
                "title": "Enriched result",
                "pic": "http://i0.hdslb.com/test.jpg",
                "duration": 99,
                "owner": {"name": "Uploader"},
                "stat": {"view": 42},
            }

        adapter = BilibiliSearchAdapter(
            "bilibili", "bilisearch", runner, metadata_fetcher=metadata
        )
        candidate = adapter.search("AI", 1)[0]
        self.assertEqual("bilibili:BV1test", candidate.candidate_id)
        self.assertEqual("https://i0.hdslb.com/test.jpg", candidate.thumbnail_url)
        self.assertEqual("https://www.bilibili.com/video/BV1test", candidate.source_url)

    def test_search_normalizes_and_deduplicates_candidates(self) -> None:
        payload = {
            "entries": [
                {
                    "id": "abc123",
                    "title": "A useful video",
                    "duration": 61,
                    "uploader": "Creator",
                    "thumbnail": None,
                }
            ]
        }

        def runner(_: list[str]) -> subprocess.CompletedProcess[str]:
            return subprocess.CompletedProcess([], 0, json.dumps(payload), "")

        adapter = YtDlpSearchAdapter("youtube", "ytsearch", runner)
        with tempfile.TemporaryDirectory() as directory:
            scout = MaterialScout(
                Path(directory) / "library",
                adapters={"youtube": adapter},
                command_runner=runner,
            )
            session = scout.search(
                ["agent", "agent"],
                ["youtube"],
                output_dir=Path(directory) / "session",
            )
            self.assertEqual(1, len(session.candidates))
            self.assertEqual(AssetKind.VIDEO, session.candidates[0].kind)
            self.assertEqual("https://www.youtube.com/watch?v=abc123", session.candidates[0].source_url)
            self.assertTrue((session.output_dir / "candidates.json").exists())
            self.assertTrue((session.output_dir / "contact-sheet-01.jpg").exists())

    def test_candidate_selection_accepts_number_and_remote_id(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            manifest = Path(directory) / "candidates.json"
            manifest.write_text(
                json.dumps(
                    {
                        "candidates": [
                            {
                                "candidate_id": "youtube:abc",
                                "kind": "video",
                                "source": "youtube",
                                "remote_id": "abc",
                                "title": "Example",
                                "source_url": "https://example.test/abc",
                                "search_query": "example",
                                "creator": None,
                                "duration_seconds": None,
                                "published_at": None,
                                "thumbnail_url": None,
                                "description": None,
                                "metadata": {},
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            candidates = load_candidates(manifest)
            self.assertEqual(candidates, select_candidates(candidates, ["#001"]))
            self.assertEqual(candidates, select_candidates(candidates, ["abc"]))

    def test_local_registration_supports_non_video_assets(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            image = Path(directory) / "brand.png"
            image.write_bytes(b"not-a-real-image-but-a-real-file")
            scout = MaterialScout(Path(directory) / "library", adapters={})
            asset = scout.register_local(image, RightsStatus.OWNED)
            self.assertEqual(AssetKind.IMAGE, asset.kind)
            self.assertEqual("owned", scout.list_assets()[0]["rights_status"])

    def test_mark_treatment_requires_rights_and_explicit_authorization(self) -> None:
        self.assertFalse(decide_mark_treatment("unknown", "allowed", "inpaint").allowed)
        self.assertFalse(decide_mark_treatment("owned", "unknown", "inpaint").allowed)
        self.assertTrue(decide_mark_treatment("owned", "allowed", "inpaint").allowed)
        self.assertTrue(decide_mark_treatment("unknown", "unknown", "preserve").allowed)


if __name__ == "__main__":
    unittest.main()
