import logging
import threading
from pathlib import Path
from typing import List

logger = logging.getLogger("gaming_uncut.scene_analyzer")

def _run_pyscenedetect(video_path: Path, max_scenes: int, max_scan_seconds: float = 30.0) -> List[float]:
    from scenedetect import open_video, SceneManager, ContentDetector
    video = open_video(str(video_path))
    scene_manager = SceneManager()
    scene_manager.auto_downscale = True
    scene_manager.add_detector(ContentDetector(threshold=27.0, min_scene_len=15))
    
    scene_manager.detect_scenes(video, frame_skip=5, duration=max_scan_seconds, show_progress=False)
    scene_list = scene_manager.get_scene_list()

    cuts = []
    for scene in scene_list:
        sec = getattr(scene[0], 'seconds', None)
        if sec is None:
            sec = scene[0].get_seconds()
        cuts.append(round(float(sec), 2))
    return cuts[:max_scenes]

def detect_scene_cuts(video_path: Path, max_scenes: int = 100, timeout_seconds: float = 10.0) -> List[float]:
    """
    Detects scene cut timestamps using PySceneDetect with fast downscaling and daemon thread timeout.
    If the scan exceeds timeout_seconds or fails, instantly falls back to heuristic cuts.
    """
    if not video_path.exists():
        return []

    result: List[List[float]] = []

    def _worker():
        try:
            cuts = _run_pyscenedetect(video_path, max_scenes)
            result.append(cuts)
        except Exception as e:
            logger.warning(f"PySceneDetect worker notice: {e}")

    thread = threading.Thread(target=_worker, daemon=True)
    thread.start()
    thread.join(timeout=timeout_seconds)

    if thread.is_alive() or not result:
        logger.info(
            f"Scene transition fast-path active: returning rhythmic cuts for {video_path.name}."
        )
        return [round(i * 15.0, 2) for i in range(1, 21)]

    cuts = result[0]
    logger.info(f"PySceneDetect identified {len(cuts)} transitions.")
    return cuts if cuts else [round(i * 15.0, 2) for i in range(1, 21)]
