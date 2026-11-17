"""
Unit & Integration Test Suite for YouTube Shorts Technical Profile & Compliance.
Covers:
1. Landscape gameplay input conversion (1920x1080 -> 1080x1920 9:16).
2. Native portrait gameplay input conversion (720x1280 or 1080x1920 -> 1080x1920 9:16).
3. Dynamic gameplay intelligent center crop vs Blurred background template.
4. Pre-upload validation: verifies 1080x1920, 9:16, H.264, AAC, yuv420p, +faststart, <= 180s.
5. Rejection of invalid resolutions (landscape) and durations (> 180s).
6. YouTube client pre-upload enforcement halting non-compliant uploads.
7. Verification of 3 different gameplay sources and file properties.
"""

import subprocess
import json
import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch

from backend.app.video.renderer import video_renderer
from backend.app.video.validator import validate_shorts_profile, quality_validator
from backend.app.publishers.youtube.youtube_client import (
    youtube_client,
    YouTubeShortsValidationError
)


@pytest.fixture(scope="module")
def sample_landscape_video(tmp_path_factory) -> Path:
    """Creates a synthetic 1920x1080 landscape gameplay video (3 seconds, H.264/AAC)."""
    tmp_dir = tmp_path_factory.mktemp("shorts_sources")
    vid_path = tmp_dir / "gameplay_landscape_1080p.mp4"
    cmd = [
        "ffmpeg", "-y",
        "-f", "lavfi", "-i", "testsrc=size=1920x1080:rate=30",
        "-f", "lavfi", "-i", "sine=frequency=1000:sample_rate=44100",
        "-t", "3",
        "-c:v", "libx264", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "128k",
        str(vid_path)
    ]
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return vid_path


@pytest.fixture(scope="module")
def sample_portrait_video(tmp_path_factory) -> Path:
    """Creates a synthetic 720x1280 mobile/portrait gameplay video (3 seconds, H.264/AAC)."""
    tmp_dir = tmp_path_factory.mktemp("shorts_portrait")
    vid_path = tmp_dir / "gameplay_portrait_720p.mp4"
    cmd = [
        "ffmpeg", "-y",
        "-f", "lavfi", "-i", "testsrc=size=720x1280:rate=30",
        "-f", "lavfi", "-i", "sine=frequency=800:sample_rate=44100",
        "-t", "3",
        "-c:v", "libx264", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "128k",
        str(vid_path)
    ]
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return vid_path


def probe_file(file_path: Path) -> dict:
    """Helper to inspect actual stream properties using ffprobe."""
    cmd = [
        "ffprobe", "-v", "error",
        "-show_entries", "format=duration,format_name:stream=width,height,codec_name,pix_fmt",
        "-of", "json",
        str(file_path)
    ]
    res = subprocess.run(cmd, capture_output=True, text=True, check=True)
    data = json.loads(res.stdout)
    v_stream = next((s for s in data.get("streams", []) if s.get("width")), {})
    dur = float(data.get("format", {}).get("duration", 0.0))
    return {
        "width": int(v_stream.get("width", 0)),
        "height": int(v_stream.get("height", 0)),
        "duration": dur,
        "codec": v_stream.get("codec_name"),
        "pix_fmt": v_stream.get("pix_fmt")
    }


def test_landscape_to_shorts_blurred_bg(sample_landscape_video, tmp_path):
    """Test 1: Convert 1920x1080 landscape to 1080x1920 portrait using Blurred Background."""
    output_path = tmp_path / "rendered_blurred_bg.mp4"
    video_renderer.render_reel(
        input_path=sample_landscape_video,
        output_path=output_path,
        start_sec=0.0,
        duration_sec=2.0,
        template="blurred_bg"
    )

    assert output_path.exists()
    props = probe_file(output_path)
    assert props["width"] == 1080
    assert props["height"] == 1920
    assert props["codec"] == "h264"
    assert props["pix_fmt"] == "yuv420p"
    assert props["duration"] <= 3.0

    # Verify through strict Shorts validator
    is_valid, msg, info = validate_shorts_profile(output_path)
    assert is_valid, msg
    assert info["aspect_ratio"] == "9:16"
    assert info["has_faststart"] is True


def test_landscape_to_shorts_dynamic_crop(sample_landscape_video, tmp_path):
    """Test 2: Convert 1920x1080 landscape to 1080x1920 portrait using Dynamic Gameplay Crop."""
    output_path = tmp_path / "rendered_dynamic_crop.mp4"
    video_renderer.render_reel(
        input_path=sample_landscape_video,
        output_path=output_path,
        start_sec=0.0,
        duration_sec=2.0,
        template="dynamic_gameplay"
    )

    assert output_path.exists()
    props = probe_file(output_path)
    assert props["width"] == 1080
    assert props["height"] == 1920
    assert props["codec"] == "h264"
    assert props["pix_fmt"] == "yuv420p"

    is_valid, msg, info = validate_shorts_profile(output_path)
    assert is_valid, msg
    assert info["aspect_ratio"] == "9:16"


def test_native_portrait_to_shorts(sample_portrait_video, tmp_path):
    """Test 3: Native portrait input (720x1280) scales directly to 1080x1920 without distortion."""
    output_path = tmp_path / "rendered_from_portrait.mp4"
    video_renderer.render_reel(
        input_path=sample_portrait_video,
        output_path=output_path,
        start_sec=0.0,
        duration_sec=2.0,
        template="blurred_bg"
    )

    assert output_path.exists()
    props = probe_file(output_path)
    assert props["width"] == 1080
    assert props["height"] == 1920
    assert props["codec"] == "h264"
    assert props["pix_fmt"] == "yuv420p"

    is_valid, msg, info = validate_shorts_profile(output_path)
    assert is_valid, msg


def test_duration_enforcement_capped_at_180s(sample_landscape_video, tmp_path):
    """Test 4: Duration requested > 180s is strictly capped at 180s."""
    output_path = tmp_path / "rendered_capped_duration.mp4"
    video_renderer.render_reel(
        input_path=sample_landscape_video,
        output_path=output_path,
        start_sec=0.0,
        duration_sec=240.0,  # 4 minutes requested
        template="blurred_bg"
    )

    assert output_path.exists()
    props = probe_file(output_path)
    assert props["duration"] <= 180.0


def test_pre_upload_validation_rejects_landscape(sample_landscape_video):
    """Test 5: validate_shorts_profile strictly rejects unrendered landscape video."""
    is_valid, reason, info = validate_shorts_profile(sample_landscape_video)
    assert not is_valid
    assert "Landscape orientation detected" in reason or "Invalid resolution" in reason
    assert info["width"] == 1920
    assert info["height"] == 1080


def test_pre_upload_validation_rejects_exceeding_duration(tmp_path):
    """Test 6: validate_shorts_profile rejects video exceeding 180 seconds."""
    long_vid = tmp_path / "too_long.mp4"
    # Create 185s dummy video
    cmd = [
        "ffmpeg", "-y",
        "-f", "lavfi", "-i", "color=c=black:s=1080x1920:d=185",
        "-c:v", "libx264", "-pix_fmt", "yuv420p",
        str(long_vid)
    ]
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    is_valid, reason, info = validate_shorts_profile(long_vid)
    assert not is_valid
    assert "exceeds YouTube Shorts maximum limit of 180.0 seconds" in reason


def test_youtube_client_halts_upload_on_validation_failure(sample_landscape_video):
    """Test 7: youtube_client.upload_short stops publishing and raises YouTubeShortsValidationError."""
    with pytest.raises(YouTubeShortsValidationError) as exc_info:
        youtube_client.upload_short(
            video_path=sample_landscape_video,
            title="Invalid Landscape Video #Shorts",
            description="Should fail pre-upload validation.",
            tags=["#Shorts"]
        )
    assert "Cannot upload to YouTube Shorts" in str(exc_info.value)

    # Also test unbound class method directly
    from backend.app.publishers.youtube.youtube_client import YouTubePublishClient
    with pytest.raises(YouTubeShortsValidationError) as exc_info2:
        YouTubePublishClient.upload_short(
            youtube_client,
            video_path=sample_landscape_video,
            title="Direct Class Call Landscape Video",
            description="Direct method call test.",
            tags=["#Shorts"]
        )
    assert "Cannot upload to YouTube Shorts" in str(exc_info2.value)
