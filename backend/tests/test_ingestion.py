import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from backend.app.ingestion.adapters import YouTubeAdapter, DirectUrlAdapter, LocalFileAdapter

def test_source_adapters():
    yt = YouTubeAdapter()
    direct = DirectUrlAdapter()
    local = LocalFileAdapter()

    # YouTube URL validation
    assert yt.can_handle("https://www.youtube.com/watch?v=dQw4w9WgXcQ")
    assert yt.can_handle("https://youtu.be/dQw4w9WgXcQ")
    assert yt.can_handle("https://youtube.com/live/12345678901")
    assert not yt.can_handle("https://vimeo.com/123456")

    # Direct URL validation
    assert direct.can_handle("https://cdn.example.com/stream/gameplay_raw.mp4")
    assert direct.can_handle("http://server.local/video.mkv?token=123")
    assert not direct.can_handle("https://google.com/index.html")

    # Local file validation
    test_file = Path("data/temporary/test_probe.mp4")
    test_file.parent.mkdir(parents=True, exist_ok=True)
    test_file.write_bytes(b"dummy")
    assert local.can_handle(str(test_file))
    test_file.unlink()

    print("TEST PASSED: Ingestion Source Adapters validated.")

if __name__ == "__main__":
    test_source_adapters()
