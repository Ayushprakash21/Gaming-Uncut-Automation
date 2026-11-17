"""
Audio Signal & Excitement Peak Detection for Gaming Uncut.
Extracts audio tracks, calculates energy/RMS decibel envelopes, and isolates
high-intensity clusters (screams, clutch gunfire, boss battle climaxes).
"""

import math
import wave
import struct
import subprocess
import logging
from pathlib import Path
from typing import List, Tuple
from src.core.config import settings

logger = logging.getLogger("gaming_uncut.audio_detector")

def extract_audio_track(video_path: Path, output_wav: Path) -> Path:
    """Extracts 16kHz mono WAV audio track for fast analysis."""
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

def analyze_audio_peaks(wav_path: Path, window_sec: float = 1.0) -> List[Tuple[float, float]]:
    """
    Computes RMS decibel level per window.
    Returns list of (timestamp_sec, db_level).
    """
    scores = []
    with wave.open(str(wav_path), "rb") as wf:
        n_channels = wf.getnchannels()
        sampwidth = wf.getsampwidth()
        framerate = wf.getframerate()
        frames_per_window = int(framerate * window_sec)
        
        timestamp = 0.0
        while True:
            raw_frames = wf.readframes(frames_per_window)
            if not raw_frames:
                break
            
            # Unpack 16-bit signed PCM
            count = len(raw_frames) // 2
            if count == 0:
                break
            samples = struct.unpack(f"{count}h", raw_frames)
            
            # Calculate RMS
            sum_squares = sum(s * s for s in samples)
            rms = math.sqrt(sum_squares / count)
            # Convert to dB relative to full scale
            db = 20 * math.log10(rms) if rms > 0 else -100.0
            scores.append((timestamp, db))
            timestamp += window_sec
            
    return scores

def find_candidate_moments(
    peaks: List[Tuple[float, float]],
    target_duration: float = settings.TARGET_CLIP_DURATION,
    min_clip_duration: float = settings.MIN_CLIP_DURATION,
    top_n: int = 5
) -> List[dict]:
    """
    Identifies top N highlight segments based on peak energy clusters.
    Structures clips with an optimal short-form pacing curve:
    (Hook -> Build-up -> Climax -> Cool-down/Reaction).
    """
    if not peaks:
        return []

    # Calculate baseline average dB
    dbs = [p[1] for p in peaks if p[1] > -80.0]
    if not dbs:
        return []
    avg_db = sum(dbs) / len(dbs)
    std_db = math.sqrt(sum((x - avg_db) ** 2 for x in dbs) / len(dbs)) if len(dbs) > 1 else 1.0
    threshold = avg_db + 1.2 * std_db  # Moments significantly above average volume

    total_duration = peaks[-1][0]
    candidates = []

    # Scan for peak clusters
    for i, (ts, db) in enumerate(peaks):
        if db >= threshold:
            # Anchor climax around 70% into the clip so there's build up + resolution
            start_ts = max(0.0, ts - (target_duration * 0.70))
            end_ts = min(total_duration, start_ts + target_duration)
            
            # Avoid overly short clips
            if end_ts - start_ts < min_clip_duration:
                continue

            # Calculate average excitement score within candidate window
            window_dbs = [p[1] for p in peaks if start_ts <= p[0] <= end_ts]
            window_score = sum(window_dbs) / len(window_dbs) if window_dbs else avg_db
            
            # Normalized score (0 - 100)
            normalized_score = min(100.0, max(0.0, ((window_score - avg_db) / (2.5 * std_db + 1e-5)) * 50.0 + 50.0))

            candidates.append({
                "start_sec": round(start_ts, 2),
                "end_sec": round(end_ts, 2),
                "duration_sec": round(end_ts - start_ts, 2),
                "score": round(normalized_score, 1),
                "peak_sec": round(ts, 2),
                "detection_type": "audio_peak"
            })

    # Deduplicate overlapping candidates (keep highest score in overlapping windows)
    candidates.sort(key=lambda x: x["score"], reverse=True)
    selected = []
    
    for cand in candidates:
        overlap = False
        for s in selected:
            # Overlap check
            if not (cand["end_sec"] < s["start_sec"] or cand["start_sec"] > s["end_sec"]):
                overlap = True
                break
        if not overlap:
            selected.append(cand)
            if len(selected) >= top_n:
                break
                
    # Sort chronologically
    selected.sort(key=lambda x: x["start_sec"])
    return selected
