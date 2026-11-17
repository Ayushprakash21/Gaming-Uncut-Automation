import sys
from pathlib import Path
import subprocess

# Add project root
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.core.config import settings
from src.video.compositor import VideoCompositor
from src.video.subtitle_builder import generate_ass_subtitles
from src.ingestion.watcher import probe_video

def test_video_generation():
    test_dir = settings.TEMP_DIR / "test"
    test_dir.mkdir(parents=True, exist_ok=True)
    
    input_video = test_dir / "sample_1080p.mp4"
    output_vertical = test_dir / "rendered_vertical_1080x1920.mp4"
    ass_subs = test_dir / "test_subs.ass"
    thumb = test_dir / "test_thumb.jpg"

    print("Step 1: Generating 5s test 1920x1080 horizontal gameplay clip...")
    cmd = [
        "ffmpeg", "-y",
        "-f", "lavfi", "-i", "testsrc=size=1920x1080:rate=60",
        "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=16000",
        "-t", "5",
        "-c:v", "libx264", "-c:a", "aac",
        str(input_video)
    ]
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    print("Step 2: Generating sample dynamic ASS karaoke subtitles...")
    words = [
        {"word": "GAMING", "start": 0.5, "end": 1.2},
        {"word": "UNCUT", "start": 1.2, "end": 2.0},
        {"word": "IS", "start": 2.0, "end": 2.5},
        {"word": "LIVE", "start": 2.5, "end": 3.5}
    ]
    generate_ass_subtitles(words, ass_subs)

    print("Step 3: Rendering 1080x1920 vertical video with NVENC & blurred backdrop...")
    comp = VideoCompositor()
    comp.render_blurred_bg(
        input_path=input_video,
        output_path=output_vertical,
        start_sec=0,
        duration_sec=4,
        ass_subtitles_path=ass_subs,
        watermark_text="GAMING UNCUT"
    )

    print("Step 4: Extracting thumbnail...")
    comp.generate_thumbnail(output_vertical, thumb, timestamp_sec=1.5)

    print("Step 5: Verifying generated vertical file specs with ffprobe...")
    meta = probe_video(output_vertical)
    print(f"Output dimensions: {meta['width']}x{meta['height']} @ {meta['fps']} FPS, Duration: {meta['duration']}s")
    
    assert meta["width"] == 1080, f"Expected 1080 width, got {meta['width']}"
    assert meta["height"] == 1920, f"Expected 1920 height, got {meta['height']}"
    assert meta["duration"] > 3.0, f"Expected >3s duration, got {meta['duration']}"
    assert thumb.exists(), "Thumbnail was not created"

    print("\nSUCCESS: 1080x1920 vertical video pipeline verified with NVENC hardware acceleration!")

if __name__ == "__main__":
    test_video_generation()
