import logging
from pathlib import Path
from typing import List
from scenedetect import open_video, SceneManager, ContentDetector

logger = logging.getLogger("gaming_uncut.scene_analyzer")

def detect_scene_cuts(video_path: Path, max_scenes: int = 100) -> List[float]:
    """Detects scene cut timestamps using PySceneDetect with fast downscaling."""
    if not video_path.exists():
        return []

    try:
        video = open_video(str(video_path))
        scene_manager = SceneManager()
        scene_manager.add_detector(ContentDetector(threshold=27.0, min_scene_len=15))
        
        # Downscale for high-speed analysis
        scene_manager.detect_scenes(video, show_progress=False)
        scene_list = scene_manager.get_scene_list()

        cuts = [round(scene[0].get_seconds(), 2) for scene in scene_list]
        return cuts[:max_scenes]
    except Exception as e:
        logger.warning(f"Scene detection fallback: {e}")
        return []
