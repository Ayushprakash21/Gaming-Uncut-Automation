import logging
import threading
from pathlib import Path
from typing import List, Dict, Any, Tuple

logger = logging.getLogger("gaming_uncut.transcriber")

_whisper_instance = None

def get_whisper_model():
    global _whisper_instance
    if _whisper_instance is None:
        try:
            from faster_whisper import WhisperModel
            logger.info("Initializing faster-whisper with CUDA acceleration...")
            try:
                _whisper_instance = WhisperModel("base", device="cuda", compute_type="float16")
            except Exception as e:
                logger.warning(f"CUDA whisper initialization failed, using CPU: {e}")
                _whisper_instance = WhisperModel("base", device="cpu", compute_type="int8")
        except ImportError:
            logger.warning("faster-whisper is not installed. Using text hook fallback for gameplay.")
            return None
    return _whisper_instance

def _execute_transcription(wav_path: Path) -> Tuple[str, List[Dict[str, Any]]]:
    model = get_whisper_model()
    if model is None:
        return "", []
    segments, info = model.transcribe(
        str(wav_path),
        word_timestamps=True,
        language="en",
        vad_filter=True
    )
    
    full_text_list = []
    words_data = []

    for seg in segments:
        full_text_list.append(seg.text)
        if seg.words:
            for w in seg.words:
                words_data.append({
                    "word": w.word.strip(),
                    "start": round(w.start, 2),
                    "end": round(w.end, 2),
                    "probability": round(w.probability, 2)
                })

    full_text = " ".join(full_text_list).strip()
    logger.info(f"Transcribed {len(words_data)} words. Language: {getattr(info, 'language', 'unknown')}")
    return full_text, words_data

def transcribe_audio_words(wav_path: Path, timeout_seconds: float = 20.0) -> Tuple[str, List[Dict[str, Any]]]:
    """
    Transcribes audio file with strict bounded daemon thread timeout to prevent pipeline stalling.
    Returns:
    (full_text, list of words with [{"word": str, "start": float, "end": float}])
    """
    if not wav_path.exists():
        return "", []

    result: List[Tuple[str, List[Dict[str, Any]]]] = []

    def _worker():
        try:
            res = _execute_transcription(wav_path)
            result.append(res)
        except Exception as e:
            logger.warning(f"Transcription error: {e}")

    thread = threading.Thread(target=_worker, daemon=True)
    thread.start()
    thread.join(timeout=timeout_seconds)

    if thread.is_alive() or not result:
        logger.info(
            f"Transcription bounded at {timeout_seconds}s for {wav_path.name}. "
            "Proceeding with gameplay telemetry signals."
        )
        return "", []

    return result[0]
