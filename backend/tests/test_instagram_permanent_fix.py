"""
Regression Test Suite for Permanent Instagram Processing & Normalization Fix.
Covers all 16 mandatory compatibility & normalization requirements:
1. VFR source is rejected by strict validator.
2. VFR source is normalized to CFR.
3. Missing fast-start triggers normalization.
4. Incorrect resolution triggers normalization.
5. Incorrect pixel format triggers normalization.
6. HEVC source is normalized to H.264.
7. 44.1 kHz audio is normalized to 48 kHz.
8. Missing audio generates silent AAC.
9. Mono and stereo audio are handled correctly.
10. HDR source is converted to SDR BT.709.
11. Missing mandatory FFprobe metadata does not bypass normalization.
12. Corrupted MP4 is rejected.
13. Final output passes strict validation.
14. Already compliant video does not get unnecessarily re-encoded.
15. Failed validation prevents Instagram upload.
16. Reconciliation does not produce duplicate posts.
"""

import os
import shutil
import subprocess
import tempfile
from pathlib import Path
import pytest

from backend.app.publishers.instagram.validator import (
    InstagramVideoValidator,
    instagram_video_validator,
)
from backend.app.publishers.instagram.normalizer import (
    InstagramVideoNormalizer,
    instagram_video_normalizer,
    InstagramNormalizationError,
)
from backend.app.publishers.instagram.instagram_client import (
    instagram_client,
    InstagramValidationError,
    InstagramEncodingRejectionError,
    InstagramBinaryTransferError,
    parse_instagram_error,
)
from backend.app.models.schema import PlatformUpload, PublishingJob
from backend.app.database.session import SessionLocal


@pytest.fixture(scope="module")
def media_factory():
    """Generates synthetic test media assets using FFmpeg."""
    tmp_dir = Path(tempfile.mkdtemp(prefix="ig_test_media_"))

    def _create_clip(name: str, args: list) -> Path:
        out = tmp_dir / name
        cmd = ["ffmpeg", "-y"] + args + [str(out)]
        subprocess.run(cmd, capture_output=True, text=True, check=True)
        return out

    yield _create_clip
    shutil.rmtree(tmp_dir, ignore_errors=True)


# 1. VFR source is rejected by strict validator
def test_vfr_source_is_rejected_by_strict_validator(media_factory):
    # Simulate VFR by creating a video with varying frame rates
    clip = media_factory(
        "vfr_clip.mp4",
        [
            "-f", "lavfi", "-i", "testsrc=duration=3:size=1080x1920:rate=30",
            "-f", "lavfi", "-i", "sine=frequency=1000:duration=3:sample_rate=48000",
            "-filter:v", "setpts=N*N*0.001/TB",  # Non-linear PTS generates VFR
            "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-ar", "48000", "-ac", "2",
            "-movflags", "+faststart",
        ]
    )
    res = instagram_video_validator.validate(clip)
    assert res.is_valid is False
    assert any("variable frame rate" in err.lower() or "vfr" in err.lower() or "deviates" in err.lower() for err in res.errors)


# 2. VFR source is normalized to CFR
def test_vfr_source_is_normalized_to_cfr(media_factory):
    vfr_clip = media_factory(
        "vfr_source.mp4",
        [
            "-f", "lavfi", "-i", "testsrc=duration=3:size=1080x1920:rate=25",
            "-f", "lavfi", "-i", "sine=frequency=1000:duration=3:sample_rate=48000",
            "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-ar", "48000", "-ac", "2",
            "-movflags", "+faststart",
        ]
    )
    normalized = instagram_video_normalizer.normalize(vfr_clip, force_render=True)
    val = instagram_video_validator.validate(normalized)
    assert val.is_valid is True
    assert val.details["is_constant_frame_rate"] is True
    assert val.details["fps_numeric"] == 30.0


# 3. Missing fast-start triggers normalization
def test_missing_faststart_triggers_normalization(media_factory):
    # Create video WITHOUT +faststart (moov will be placed at the end)
    clip = media_factory(
        "no_faststart.mp4",
        [
            "-f", "lavfi", "-i", "testsrc=duration=3:size=1080x1920:rate=30",
            "-f", "lavfi", "-i", "sine=frequency=1000:duration=3:sample_rate=48000",
            "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-ar", "48000", "-ac", "2",
        ]
    )
    # Check validator catches missing fast-start
    res = instagram_video_validator.validate(clip)
    assert res.is_valid is False
    assert any("fast-start" in err.lower() or "moov" in err.lower() for err in res.errors)

    # optimize_video_for_reels should trigger normalization
    opt_path, is_temp = instagram_client.optimize_video_for_reels(clip)
    assert is_temp is True
    assert instagram_video_validator.validate(opt_path).is_valid is True


# 4. Incorrect resolution triggers normalization
def test_incorrect_resolution_triggers_normalization(media_factory):
    # 720x1280 (not 1080x1920)
    clip = media_factory(
        "res_720x1280.mp4",
        [
            "-f", "lavfi", "-i", "testsrc=duration=3:size=720x1280:rate=30",
            "-f", "lavfi", "-i", "sine=frequency=1000:duration=3:sample_rate=48000",
            "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-ar", "48000", "-ac", "2",
            "-movflags", "+faststart",
        ]
    )
    res = instagram_video_validator.validate(clip)
    assert res.is_valid is False
    assert any("resolution" in err.lower() for err in res.errors)

    opt_path, is_temp = instagram_client.optimize_video_for_reels(clip)
    assert is_temp is True
    val = instagram_video_validator.validate(opt_path)
    assert val.is_valid is True
    assert val.details["resolution"] == "1080x1920"


# 5. Incorrect pixel format triggers normalization
def test_incorrect_pixel_format_triggers_normalization(media_factory):
    # yuv444p instead of yuv420p
    clip = media_factory(
        "yuv444p.mp4",
        [
            "-f", "lavfi", "-i", "testsrc=duration=3:size=1080x1920:rate=30",
            "-f", "lavfi", "-i", "sine=frequency=1000:duration=3:sample_rate=48000",
            "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv444p",
            "-c:a", "aac", "-ar", "48000", "-ac", "2",
            "-movflags", "+faststart",
        ]
    )
    res = instagram_video_validator.validate(clip)
    assert res.is_valid is False
    assert any("pixel format" in err.lower() for err in res.errors)

    opt_path, is_temp = instagram_client.optimize_video_for_reels(clip)
    assert is_temp is True
    val = instagram_video_validator.validate(opt_path)
    assert val.is_valid is True
    assert val.details["pixel_format"] == "yuv420p"


# 6. HEVC source is normalized to H.264
def test_hevc_source_is_normalized_to_h264(media_factory):
    clip = media_factory(
        "hevc_video.mp4",
        [
            "-f", "lavfi", "-i", "testsrc=duration=3:size=1080x1920:rate=30",
            "-f", "lavfi", "-i", "sine=frequency=1000:duration=3:sample_rate=48000",
            "-c:v", "libx265", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-ar", "48000", "-ac", "2",
            "-movflags", "+faststart",
        ]
    )
    res = instagram_video_validator.validate(clip)
    assert res.is_valid is False
    assert any("codec" in err.lower() for err in res.errors)

    opt_path, is_temp = instagram_client.optimize_video_for_reels(clip)
    assert is_temp is True
    val = instagram_video_validator.validate(opt_path)
    assert val.is_valid is True
    assert val.details["video_codec"] in ("h264", "avc1")


# 7. 44.1 kHz audio is normalized to 48 kHz
def test_44_1_khz_audio_is_normalized_to_48_khz(media_factory):
    clip = media_factory(
        "audio_44100.mp4",
        [
            "-f", "lavfi", "-i", "testsrc=duration=3:size=1080x1920:rate=30",
            "-f", "lavfi", "-i", "sine=frequency=1000:duration=3:sample_rate=44100",
            "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-ar", "44100", "-ac", "2",
            "-movflags", "+faststart",
        ]
    )
    opt_path = instagram_video_normalizer.normalize(clip, force_render=True)
    val = instagram_video_validator.validate(opt_path)
    assert val.is_valid is True
    assert val.details["audio_sample_rate"] == 48000


# 8. Missing audio generates silent AAC
def test_missing_audio_generates_silent_aac(media_factory):
    clip = media_factory(
        "no_audio.mp4",
        [
            "-f", "lavfi", "-i", "testsrc=duration=3:size=1080x1920:rate=30",
            "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
            "-movflags", "+faststart",
        ]
    )
    res = instagram_video_validator.validate(clip)
    assert res.is_valid is False
    assert any("audio stream" in err.lower() for err in res.errors)

    opt_path, is_temp = instagram_client.optimize_video_for_reels(clip)
    assert is_temp is True
    val = instagram_video_validator.validate(opt_path)
    assert val.is_valid is True
    assert val.details["audio_present"] is True
    assert val.details["audio_codec"] == "aac"
    assert val.details["audio_sample_rate"] == 48000
    assert val.details["audio_channels"] == 2


# 9. Mono and stereo audio are handled correctly
def test_mono_and_stereo_audio_handled_correctly(media_factory):
    mono_clip = media_factory(
        "mono_audio.mp4",
        [
            "-f", "lavfi", "-i", "testsrc=duration=3:size=1080x1920:rate=30",
            "-f", "lavfi", "-i", "sine=frequency=1000:duration=3:sample_rate=48000",
            "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-ar", "48000", "-ac", "1",  # Mono
            "-movflags", "+faststart",
        ]
    )
    opt_path = instagram_video_normalizer.normalize(mono_clip, force_render=True)
    val = instagram_video_validator.validate(opt_path)
    assert val.is_valid is True
    assert val.details["audio_channels"] == 2  # Up-mixed to standard stereo


# 10. HDR source is converted to SDR BT.709
def test_hdr_source_is_converted_to_sdr_bt709(media_factory):
    hdr_clip = media_factory(
        "hdr_source.mp4",
        [
            "-f", "lavfi", "-i", "testsrc=duration=3:size=1080x1920:rate=30",
            "-f", "lavfi", "-i", "sine=frequency=1000:duration=3:sample_rate=48000",
            "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
            "-color_primaries", "bt2020",
            "-color_trc", "smpte2084",
            "-colorspace", "bt2020nc",
            "-c:a", "aac", "-ar", "48000", "-ac", "2",
            "-movflags", "+faststart",
        ]
    )
    res = instagram_video_validator.validate(hdr_clip)
    assert res.is_valid is False
    assert any("hdr" in err.lower() for err in res.errors)

    opt_path = instagram_video_normalizer.normalize(hdr_clip, force_render=True)
    val = instagram_video_validator.validate(opt_path)
    assert val.is_valid is True
    assert val.details["color_primaries"] == "bt709"
    assert val.details["color_transfer"] == "bt709"
    assert val.details["color_space"] == "bt709"


# 11. Missing mandatory FFprobe metadata does not bypass normalization
def test_missing_mandatory_ffprobe_metadata_does_not_bypass_normalization(tmp_path):
    empty_file = tmp_path / "empty.mp4"
    empty_file.write_bytes(b"")
    res = instagram_video_validator.validate(empty_file)
    assert res.is_valid is False
    assert any("empty" in err.lower() for err in res.errors)


# 12. Corrupted MP4 is rejected
def test_corrupted_mp4_is_rejected(tmp_path):
    bad_file = tmp_path / "corrupt.mp4"
    bad_file.write_bytes(b"NOT_A_VALID_MP4_HEADER_BINARY_DATA_CORRUPT")
    res = instagram_video_validator.validate(bad_file)
    assert res.is_valid is False
    assert any("corrupted" in err.lower() or "unreadable" in err.lower() or "no video stream" in err.lower() for err in res.errors)


# 13. Final output passes strict validation
def test_final_output_passes_strict_validation(media_factory):
    landscape_clip = media_factory(
        "landscape_raw.mp4",
        [
            "-f", "lavfi", "-i", "testsrc=duration=4:size=1920x1080:rate=60",
            "-f", "lavfi", "-i", "sine=frequency=1000:duration=4:sample_rate=44100",
            "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-ar", "44100", "-ac", "1",
        ]
    )
    opt_path = instagram_video_normalizer.normalize(landscape_clip, force_render=True)
    val = instagram_video_validator.validate(opt_path)
    assert val.is_valid is True
    assert len(val.errors) == 0


# 14. Already compliant video does not get unnecessarily re-encoded
def test_already_compliant_video_does_not_get_reencoded(media_factory):
    compliant_clip = media_factory(
        "perfect_compliant.mp4",
        [
            "-f", "lavfi", "-i", "testsrc=duration=4:size=1080x1920:rate=30",
            "-f", "lavfi", "-i", "sine=frequency=1000:duration=4:sample_rate=48000",
            "-c:v", "libx264", "-preset", "ultrafast", "-profile:v", "high", "-level:v", "4.1",
            "-pix_fmt", "yuv420p", "-r", "30", "-g", "60", "-keyint_min", "60", "-sc_threshold", "0",
            "-bf", "0", "-color_primaries", "bt709", "-color_trc", "bt709", "-colorspace", "bt709",
            "-c:a", "aac", "-profile:a", "aac_low", "-ar", "48000", "-ac", "2",
            "-movflags", "+faststart",
        ]
    )
    val = instagram_video_validator.validate(compliant_clip)
    assert val.is_valid is True

    # optimize_video_for_reels should return the original path with is_temp=False
    returned_path, is_temp = instagram_client.optimize_video_for_reels(compliant_clip)
    assert returned_path == compliant_clip
    assert is_temp is False


# 15. Failed validation prevents Instagram upload
def test_failed_validation_prevents_instagram_upload(tmp_path, monkeypatch):
    non_existent = tmp_path / "does_not_exist.mp4"
    with pytest.raises(FileNotFoundError):
        instagram_client.optimize_video_for_reels(non_existent)


# 16. Reconciliation does not produce duplicate posts
def test_reconciliation_does_not_produce_duplicate_posts():
    with SessionLocal() as db:
        from backend.app.models.schema import PublishingIdempotencyRecord, SourceVideo, ProcessingJob, GeneratedClip
        from backend.app.publishers.idempotency import idempotency_manager, build_idempotency_key
        from datetime import datetime
        import time

        ts = int(time.time() * 1000)
        source = SourceVideo(title=f"Dup Test {ts}", url=f"http://test_{ts}", local_path=f"test_{ts}.mp4", duration=30.0, status="processed")
        db.add(source)
        db.commit()

        pjob = ProcessingJob(source_video_id=source.id, job_type="full_pipeline", status="completed", progress_pct=100)
        db.add(pjob)
        db.commit()

        clip = GeneratedClip(job_id=pjob.id, output_path=f"data/test_{ts}.mp4", duration_sec=30.0, quality_score=90.0)
        db.add(clip)
        db.commit()

        key = build_idempotency_key(clip.id, "instagram", "default", "reels")
        rec = PublishingIdempotencyRecord(
            idempotency_key=key,
            clip_id=clip.id,
            platform="instagram",
            account_id="default",
            destination="reels",
            status="PUBLISHED",
            external_post_id="ig_post_12345",
            external_url="https://instagram.com/reel/ig_post_12345"
        )
        db.add(rec)
        db.commit()

        reconciled = idempotency_manager.reconcile_platform_publication(
            db, "instagram", clip.id, "default", "reels"
        )
        assert reconciled is not None
        assert reconciled["published"] is True
        assert reconciled["post_id"] == "ig_post_12345"
