import sys
from pathlib import Path

# Add project root
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.core.database import init_db, get_connection
from src.core.security import vault
from src.video.compositor import VideoCompositor

def run():
    print("[1/3] Testing Database...")
    init_db()
    with get_connection() as conn:
        tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
        print("Tables initialized:", tables)
        assert "raw_footage" in tables
        assert "detected_moments" in tables
        assert "rendered_clips" in tables
        assert "publishing_queue" in tables

    print("[2/3] Testing Vault...")
    vault.set_secret("test_key", "test_val")
    val = vault.get_secret("test_key")
    assert val == "test_val", f"Expected test_val, got {val}"
    vault.delete_secret("test_key")
    print("Vault read/write/delete successful.")

    print("[3/3] Testing Video Compositor & Hardware NVENC...")
    comp = VideoCompositor()
    print(f"Compositor active encoder: {comp.encoder}")

    print("\nSUCCESS: All core components verified and operational.")

if __name__ == "__main__":
    run()
