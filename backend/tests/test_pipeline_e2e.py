import sys
import subprocess
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from backend.app.core.config import settings
from backend.app.ai.audio_analyzer import extract_pcm_wav, calculate_loudness_peaks
from backend.app.captions.builder import generate_animated_ass
from backend.app.video.renderer import video_renderer
from backend.app.video.validator import quality_validator
from backend.app.ai.gemini_provider import gemini_provider

def test_pipeline_e2e():
    test_dir = settings.TEMP_DIR / "test_e2e"
    test_dir.mkdir(parents=True, exist_ok=True)

    raw_video = test_dir / "synthetic_gameplay.mp4"
    audio_wav = test_dir / "synthetic_audio.wav"
    ass_subs = test_dir / "test_animated.ass"
    output_reel = test_dir / "final_reel_1080x1920.mp4"
    thumb = test_dir / "thumb.jpg"

    print("1. Synthesizing 10-second 1920x1080 60FPS test footage...")
    cmd = [
        "ffmpeg", "-y",
        "-f", "lavfi", "-i", "testsrc=size=1920x1080:rate=60",
        "-f", "lavfi", "-i", "sine=frequency=500:sample_rate=16000",
        "-t", "10",
        "-c:v", "libx264", "-c:a", "aac",
        str(raw_video)
    ]
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    print("2. Extracting PCM audio & calculating decibel spikes...")
    extract_pcm_wav(raw_video, audio_wav)
    peaks = calculate_loudness_peaks(audio_wav)
    assert len(peaks) > 0, "Expected audio peaks"

    print("3. Building dynamic word-level animated subtitles & Gaming Uncut watermark...")
    sample_words = [
        {"word": "UNBELIEVABLE", "start": 0.5, "end": 1.5},
        {"word": "CLUTCH", "start": 1.5, "end": 2.2},
        {"word": "VICTORY", "start": 2.2, "end": 3.5}
    ]
    generate_animated_ass(
        sample_words,
        ass_subs,
        brand_text="GAMING UNCUT",
        tagline="NO FILTER. JUST GAMEPLAY.",
        duration_sec=8.0
    )
    assert ass_subs.exists()

    print(f"4. Rendering 1080x1920 vertical video via NVENC ({video_renderer.encoder})...")
    video_renderer.render_reel(
        input_path=raw_video,
        output_path=output_reel,
        start_sec=1.0,
        duration_sec=8.0,
        template="blurred_bg",
        ass_subtitles_path=ass_subs
    )
    assert output_reel.exists()

    print("5. Extracting high-res 1080x1920 thumbnail...")
    video_renderer.extract_thumbnail(output_reel, thumb)
    assert thumb.exists()

    print("6. Validating Quality Control & Playability...")
    meta = gemini_provider.generate_reel_metadata("Test Highlight", "Unbelievable clutch", 8.0)
    passed, quality_score, report = quality_validator.validate_clip(output_reel, metadata=meta)
    
    print(f"Quality Score: {quality_score}/100. Resolution: {report['checks'].get('resolution')}")
    assert report["checks"].get("resolution") == "1080x1920"
    assert quality_score >= 70.0

    # Clean up test files
    for f in [raw_video, audio_wav, ass_subs, output_reel, thumb]:
        if f.exists():
            f.unlink()

    print("\nTEST PASSED: End-to-end video synthesis, animated captions & quality control validated!")

if __name__ == "__main__":
    test_pipeline_e2e()
