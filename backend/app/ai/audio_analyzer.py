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
    raw_str = str(video_path).strip().strip('\"\'')
    clean = raw_str.replace("\\", "/").lower()
    if (
        clean.startswith(("http://", "https://", "http:/", "https:/", "ftp://", "rtmp://"))
        or "://" in clean
        or "youtube.com" in clean
        or "youtu.be" in clean
    ):
        raise ValueError(
            f"Cannot extract audio from a remote URL. Video must be downloaded locally first: '{raw_str}'"
        )

    resolved_path = Path(raw_str).resolve()
    if not resolved_path.exists() or not resolved_path.is_file():
        raise FileNotFoundError(f"Input video file not found for audio extraction: '{resolved_path}'")

    output_wav.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "ffmpeg", "-y",
        "-i", str(resolved_path),
        "-vn",
        "-ac", "1",
        "-ar", "16000",
        "-f", "wav",
        str(output_wav)
    ]
    res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if res.returncode != 0:
        err_msg = res.stderr.strip() if res.stderr else f"FFmpeg exited with code {res.returncode}"
        logger.error(f"FFmpeg audio extraction error (code {res.returncode}): {err_msg}")
        raise RuntimeError(f"FFmpeg audio extraction failed (exit code {res.returncode}): {err_msg}")

    if not output_wav.exists() or output_wav.stat().st_size == 0:
        raise FileNotFoundError(f"FFmpeg audio extraction completed but output WAV is empty or missing: '{output_wav}'")

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
