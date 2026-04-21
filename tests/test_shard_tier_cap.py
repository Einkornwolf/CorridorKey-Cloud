"""Tests for tier-aware auto-shard sizing (CRKY-191).

When a user submits a job that auto-shards, the shard count must never
exceed the user's tier concurrent-job cap. Otherwise shards past the cap
get rejected by check_tier_limits at submit time and the user sees a
silently truncated result (partial frames processed).
"""

from __future__ import annotations

from unittest.mock import patch

from web.api.routes.jobs import _build_gvm_jobs, _build_inference_shards


class TestGvmShardTierCap:
    def test_no_cap_uses_full_gpu_count(self):
        # 10 GPUs, plenty of frames, no tier cap -> 10 shards (capped at MAX_AUTO_SHARDS)
        with patch("web.api.routes.jobs._get_available_gpus", return_value=["GPU"] * 10):
            jobs = _build_gvm_jobs("clip", frame_count=1000, tier_concurrent_limit=0)
        assert len(jobs) == 10

    def test_tier_cap_3_limits_to_3_shards(self):
        # Member tier: 3 concurrent. 10 GPUs available, 1000 frames.
        # Must emit at most 3 shards so none get rejected at submit time.
        with patch("web.api.routes.jobs._get_available_gpus", return_value=["GPU"] * 10):
            jobs = _build_gvm_jobs("clip", frame_count=1000, tier_concurrent_limit=3)
        assert len(jobs) == 3

    def test_tier_cap_5_limits_to_5_shards(self):
        # Contributor tier: 5 concurrent.
        with patch("web.api.routes.jobs._get_available_gpus", return_value=["GPU"] * 10):
            jobs = _build_gvm_jobs("clip", frame_count=1000, tier_concurrent_limit=5)
        assert len(jobs) == 5

    def test_tier_cap_higher_than_gpus_uses_gpu_count(self):
        # Tier allows 10, only 2 GPUs available -> 2 shards
        with patch("web.api.routes.jobs._get_available_gpus", return_value=["GPU"] * 2):
            jobs = _build_gvm_jobs("clip", frame_count=1000, tier_concurrent_limit=10)
        assert len(jobs) == 2

    def test_all_frames_covered_across_shards(self):
        # Frame coverage is the key acceptance: every frame must land in a shard
        with patch("web.api.routes.jobs._get_available_gpus", return_value=["GPU"] * 10):
            jobs = _build_gvm_jobs("clip", frame_count=260, tier_concurrent_limit=3)
        assert len(jobs) == 3
        # Each job should have a frame_range covering a slice of 0..260
        ranges = [tuple(j.params["frame_range"]) for j in jobs]
        # First range starts at 0
        assert ranges[0][0] == 0
        # Ranges are contiguous and end at frame_count
        for i in range(len(ranges) - 1):
            assert ranges[i][1] == ranges[i + 1][0]
        assert ranges[-1][1] == 260


class TestInferenceShardTierCap:
    def test_no_cap_uses_full_gpu_count(self):
        with patch("web.api.routes.jobs._get_available_gpus", return_value=["GPU"] * 10):
            jobs = _build_inference_shards("clip", frame_count=5000, tier_concurrent_limit=0)
        assert len(jobs) == 10

    def test_tier_cap_3_limits_to_3_shards(self):
        with patch("web.api.routes.jobs._get_available_gpus", return_value=["GPU"] * 10):
            jobs = _build_inference_shards("clip", frame_count=5000, tier_concurrent_limit=3)
        assert len(jobs) == 3

    def test_frames_covered_with_tier_cap(self):
        with patch("web.api.routes.jobs._get_available_gpus", return_value=["GPU"] * 10):
            jobs = _build_inference_shards("clip", frame_count=500, tier_concurrent_limit=3)
        assert len(jobs) == 3
        ranges = [tuple(j.params["frame_range"]) for j in jobs]
        assert ranges[0][0] == 0
        assert ranges[-1][1] == 500

    def test_small_clip_falls_to_single_job(self):
        # 100 frames, min_shard=50, 10 GPUs, tier_cap=3 -> frames//min_shard = 2 beats both caps
        with patch("web.api.routes.jobs._get_available_gpus", return_value=["GPU"] * 10):
            jobs = _build_inference_shards("clip", frame_count=100, tier_concurrent_limit=3)
        # 100 // 50 = 2, which is the tightest cap here
        assert len(jobs) == 2

    def test_frame_count_below_min_shard_single_unsharded_job(self):
        # 40 frames, min_shard=50 -> frame_count // min_shard = 0 -> fall through to single job
        with patch("web.api.routes.jobs._get_available_gpus", return_value=["GPU"] * 10):
            jobs = _build_inference_shards("clip", frame_count=40, tier_concurrent_limit=3)
        assert len(jobs) == 1
        # Single job has no frame_range (processes whole clip)
        assert jobs[0].params.get("frame_range") is None
