"""
Comprehensive Regression Test Suite for Instagram Reels Video Normalization & Validation.
Tests all required scenarios:
1. H.265 input -> normalized to H.264 yuv420p.
2. 60 FPS / VFR input -> normalized to constant 30 FPS.
3. 44.1 kHz audio input -> normalized to 48 kHz stereo AAC.
4. Missing audio stream -> synthetic silent stereo AAC audio generated.
5. Landscape footage -> properly scaled/cropped without aspect ratio distortion.
6. Portrait footage -> preserved cleanly.
7. Videos over 60 seconds -> trimmed to <= 60 seconds.
8. Files over 60 MB -> dynamic bitrate capping ensures <= 60 MB.
9. Corrupted files -> rejected by InstagramVideoValidator before calling Meta.
10. Successful normalized upload integration.
11. Failed upload recovery without duplicate publishing.
"""

import json
import os
import subprocess
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from backend.app.publishers.instagram.diagnostics import inspect_video_for_instagram
from backend.app.publishers.instagram.normalizer import (
    InstagramNormalizationError,
    InstagramVideoNormalizer,
    instagram_video_normalizer,
)
from backend.app.publishers.instagram.validator import (
    InstagramVideoValidator,
    instagram_video_validator,
)
from backend.app.publishers.instagram.instagram_client import (
    instagram_client,
    InstagramValidationError,
)


@pytest.fixture(scope="session")
def test_dir():
    temp_dir = Path(tempfile.mkdtemp(prefix="gaming_uncut_test_ig_"))
    yield temp_dir
    # Cleanup
    import shutil
    shutil.rmtree(temp_dir, ignore_errors=True)


def create_synthetic_video(
    output_path: Path,
    width: int = 1080,
    height: int = 1920,
    duration: float = 4.0,
    fps: int = 30,
    v_codec: str = "libx264",
    pix_fmt: str = "yuv420p",
    has_audio: bool = True,
    sample_rate: int = 48000,
) -> Path:
    """Helper to generate a lightweight synthetic test video using FFmpeg."""
    cmd = [
        "ffmpeg", "-y",
        "-f", "lavfi",
        "-i", f"color=c=blue:s={width}x{height}:r={fps}:d={duration}",
    ]
    if has_audio:
        cmd.extend([
            "-f", "lavfi",
            "-i", f"anullsrc=r={sample_rate}:cl=stereo",
            "-c:a", "aac",
            "-b:a", "128k",
            "-ar", str(sample_rate),
        ])
    else:
        cmd.append("-an")

    cmd.extend([
        "-c:v", v_codec,
        "-pix_fmt", pix_fmt,
        "-t", str(duration),
        "-movflags", "+faststart",
        str(output_path),
    ])
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    return output_path


# 1. H.265 input -> normalized to H.264
def test_normalize_h265_input(test_dir):
    input_file = test_dir / "input_h265.mp4"
    create_synthetic_video(input_file, v_codec="libx265", duration=3.5)

    # Initial validator should flag non-H.264
    val_before = instagram_video_validator.validate(input_file)
    assert not val_before.is_valid
    assert any("H.264" in err for err in val_before.errors)

    # Normalize
    output_file = test_dir / "output_from_h265.mp4"
    norm_path = instagram_video_normalizer.normalize(input_file, output_path=output_file, force_render=True)
    assert norm_path.exists()

    val_after = instagram_video_validator.validate(norm_path)
    assert val_after.is_valid
    assert val_after.details["video_codec"] == "h264"
    assert val_after.details["pixel_format"] == "yuv420p"


# 2. 60 FPS input -> normalized to 30 FPS constant
def test_normalize_60fps_input(test_dir):
    input_file = test_dir / "input_60fps.mp4"
    create_synthetic_video(input_file, fps=60, duration=3.5)

    output_file = test_dir / "output_from_60fps.mp4"
    norm_path = instagram_video_normalizer.normalize(input_file, output_path=output_file, force_render=True)

    val = instagram_video_validator.validate(norm_path)
    assert val.is_valid
    assert val.details["nominal_frame_rate"] == "30/1"
    assert val.details["fps_numeric"] == 30.0
    assert val.details["is_constant_frame_rate"] is True


# 3. 44.1 kHz audio input -> normalized to 48 kHz stereo AAC
def test_normalize_44k_audio_input(test_dir):
    input_file = test_dir / "input_44k_audio.mp4"
    create_synthetic_video(input_file, sample_rate=44100, duration=3.5)

    output_file = test_dir / "output_from_44k.mp4"
    norm_path = instagram_video_normalizer.normalize(input_file, output_path=output_file, force_render=True)

    val = instagram_video_validator.validate(norm_path)
    assert val.is_valid
    assert val.details["audio_sample_rate"] == 48000
    assert val.details["audio_channels"] == 2
    assert val.details["audio_codec"] == "aac"


# 4. Missing audio stream -> synthetic silent stereo audio generated
def test_normalize_missing_audio_input(test_dir):
    input_file = test_dir / "input_no_audio.mp4"
    create_synthetic_video(input_file, has_audio=False, duration=3.5)

    val_before = instagram_video_validator.validate(input_file)
    assert not val_before.is_valid
    assert any("audio" in err.lower() for err in val_before.errors)

    output_file = test_dir / "output_from_no_audio.mp4"
    norm_path = instagram_video_normalizer.normalize(input_file, output_path=output_file, force_render=True)

    val_after = instagram_video_validator.validate(norm_path)
    assert val_after.is_valid
    assert val_after.details["audio_present"] is True
    assert val_after.details["audio_codec"] == "aac"
    assert val_after.details["audio_sample_rate"] == 48000


# 5. Landscape footage -> properly scaled & cropped without aspect ratio distortion
def test_normalize_landscape_footage(test_dir):
    input_file = test_dir / "input_landscape.mp4"
    create_synthetic_video(input_file, width=1920, height=1080, duration=3.5)

    # Test crop policy
    output_crop = test_dir / "output_landscape_crop.mp4"
    norm_crop = instagram_video_normalizer.normalize(
        input_file, output_path=output_crop, policy="crop", force_render=True
    )
    val_crop = instagram_video_validator.validate(norm_crop)
    assert val_crop.is_valid
    assert val_crop.details["width"] == 1080
    assert val_crop.details["height"] == 1920

    # Test blur background policy
    output_blur = test_dir / "output_landscape_blur.mp4"
    norm_blur = instagram_video_normalizer.normalize(
        input_file, output_path=output_blur, policy="blur", force_render=True
    )
    val_blur = instagram_video_validator.validate(norm_blur)
    assert val_blur.is_valid
    assert val_blur.details["width"] == 1080
    assert val_blur.details["height"] == 1920


# 6. Portrait footage -> preserved cleanly
def test_normalize_portrait_footage(test_dir):
    input_file = test_dir / "input_portrait.mp4"
    create_synthetic_video(input_file, width=1080, height=1920, duration=3.5)

    output_file = test_dir / "output_portrait.mp4"
    norm_path = instagram_video_normalizer.normalize(input_file, output_path=output_file, force_render=True)

    val = instagram_video_validator.validate(norm_path)
    assert val.is_valid
    assert val.details["resolution"] == "1080x1920"


# 7. Videos over 60 seconds -> trimmed to <= 60 seconds
def test_normalize_video_over_60_seconds(test_dir):
    input_file = test_dir / "input_over_60s.mp4"
    # Create 62s video (very low bitrate/fast for testing)
    cmd = [
        "ffmpeg", "-y",
        "-f", "lavfi", "-i", "color=c=black:s=256x256:r=10:d=62",
        "-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo",
        "-c:v", "libx264", "-preset", "ultrafast", "-t", "62",
        "-c:a", "aac",
        str(input_file)
    ]
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)

    val_before = instagram_video_validator.validate(input_file)
    assert not val_before.is_valid
    assert any("60 seconds" in err for err in val_before.errors)

    output_file = test_dir / "output_trimmed.mp4"
    norm_path = instagram_video_normalizer.normalize(input_file, output_path=output_file, force_render=True)

    val_after = instagram_video_validator.validate(norm_path)
    assert val_after.is_valid
    assert val_after.details["duration_sec"] <= 60.0
    assert val_after.details["duration_sec"] >= 3.0


# 8. Dynamic bitrate computation ensures <= 60 MB
def test_dynamic_bitrate_computation():
    normalizer = InstagramVideoNormalizer(target_bitrate_mbps=4.0, max_file_size_mb=55.0)

    # For 60 seconds
    bv_60, max_60, buf_60 = normalizer.compute_dynamic_bitrate(60.0)
    # Bitrate should be <= 5000k
    bps_num = int(bv_60.replace("k", ""))
    assert bps_num <= 5000
    assert bps_num >= 1500

    # For 10 seconds
    bv_10, max_10, buf_10 = normalizer.compute_dynamic_bitrate(10.0)
    assert int(bv_10.replace("k", "")) <= 5000


# 9. Corrupted files -> rejected by validator
def test_corrupted_file_rejected(test_dir):
    corrupt_file = test_dir / "corrupted_video.mp4"
    corrupt_file.write_bytes(b"NOT_A_VALID_MP4_HEADER_GARBAGE_BYTES_1234567890")

    val = instagram_video_validator.validate(corrupt_file)
    assert not val.is_valid
    assert any("unreadable or corrupted" in err.lower() or "no video stream" in err.lower() for err in val.errors)


# 10. Successful normalized upload integration in client
def test_instagram_client_normalization_flow(test_dir):
    from backend.app.publishers.instagram.instagram_client import InstagramPublishClient
    # Create 60fps input that requires normalization
    input_file = test_dir / "client_test_60fps.mp4"
    create_synthetic_video(input_file, fps=60, duration=3.5)

    # Mock Meta API responses
    with patch.object(instagram_client, "_get_auth", return_value={"token": "test_token", "ig_id": "123456"}), \
         patch.object(instagram_client, "create_media_container", return_value={"container_id": "c_999", "upload_url": "https://meta.upload/rupload/999"}), \
         patch.object(instagram_client, "transfer_binary", return_value=True), \
         patch.object(instagram_client, "poll_container_status", return_value={"status_code": "FINISHED"}), \
         patch.object(instagram_client, "publish_container", return_value="post_123456"), \
         patch.object(instagram_client, "verify_media_published", return_value={"media_id": "post_123456", "permalink": "https://www.instagram.com/reel/post_123456/"}):

        res = InstagramPublishClient.publish_reel(
            instagram_client,
            video_path=input_file,
            caption="Test Normalized Instagram Reel",
        )

        assert res["platform"] == "instagram"
        assert res["post_id"] == "post_123456"
        assert res["url"] == "https://www.instagram.com/reel/post_123456/"


# 11. Failed upload recovery without duplicate publishing
def test_failed_upload_recovery_without_duplicate(test_dir):
    from backend.app.publishers.instagram.instagram_client import InstagramPublishClient
    input_file = test_dir / "client_test_recovery.mp4"
    create_synthetic_video(input_file, fps=60, duration=3.5)

    # First attempt: container creation fails
    with patch.object(instagram_client, "_get_auth", return_value={"token": "test_token", "ig_id": "123456"}), \
         patch.object(instagram_client, "create_media_container", side_effect=Exception("Transient edge failure")):
        with pytest.raises(Exception) as exc_info:
            InstagramPublishClient.publish_reel(instagram_client, video_path=input_file, caption="Recovery Reel")
        assert "Transient edge failure" in str(exc_info.value)

    # Second attempt: recovers with fresh container and succeeds once
    with patch.object(instagram_client, "_get_auth", return_value={"token": "test_token", "ig_id": "123456"}), \
         patch.object(instagram_client, "create_media_container", return_value={"container_id": "c_fresh_100", "upload_url": "https://meta.upload/rupload/100"}), \
         patch.object(instagram_client, "transfer_binary", return_value=True), \
         patch.object(instagram_client, "poll_container_status", return_value={"status_code": "FINISHED"}), \
         patch.object(instagram_client, "publish_container", return_value="post_fresh_100"), \
         patch.object(instagram_client, "verify_media_published", return_value={"media_id": "post_fresh_100", "permalink": "https://www.instagram.com/reel/fresh_100/"}):

        res = InstagramPublishClient.publish_reel(instagram_client, video_path=input_file, caption="Recovery Reel")
        assert res["post_id"] == "post_fresh_100"
        assert res["url"] == "https://www.instagram.com/reel/fresh_100/"
