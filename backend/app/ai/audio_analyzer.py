import math
import wave
import struct
import subprocess
import logging
from pathlib import Path
from typing import List, Dict, Any

logger = logging.getLogger("gaming_uncut.audio_analyzer")

def extract_pcm_wav(video_path: Path, output_wav: Path) -> Path:
    """Extracts 16kHz mono 16-bit PCM WAV for signal processing."""
    output_wav.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "ffmpeg", "-y",
        "-i", str(video_path),
        "-vn",
        "-ac", "1",
        "-ar", "16000",
        "-f", "wav",
        str(output_wav)
    ]
    subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
    return output_wav

def calculate_loudness_peaks(wav_path: Path, window_sec: float = 1.0) -> List[Dict[str, Any]]:
    """Calculates RMS dB per window and returns peaks exceeding moving average threshold."""
    if not wav_path.exists():
        return []

    peaks = []
    with wave.open(str(wav_path), "rb") as wf:
        n_channels = wf.getnchannels()
        sampwidth = wf.getsampwidth()
        framerate = wf.getframerate()
        frames_per_window = int(framerate * window_sec)
        
        timestamp = 0.0
        while True:
            raw = wf.readframes(frames_per_window)
            if not raw:
                break
            count = len(raw) // 2
            if count == 0:
                break
            samples = struct.unpack(f"{count}h", raw)
            sq_sum = sum(s * s for s in samples)
            rms = math.sqrt(sq_sum / count)
            db = 20 * math.log10(rms) if rms > 0 else -100.0
            peaks.append({"ts": round(timestamp, 2), "db": round(db, 2)})
            timestamp += window_sec

    # Filter for values above average
    valid_dbs = [p["db"] for p in peaks if p["db"] > -75.0]
    if not valid_dbs:
        return peaks

    avg_db = sum(valid_dbs) / len(valid_dbs)
    std_db = math.sqrt(sum((x - avg_db) ** 2 for x in valid_dbs) / len(valid_dbs)) if len(valid_dbs) > 1 else 1.0
    threshold = avg_db + 1.1 * std_db

    return [p for p in peaks if p["db"] >= threshold]
