"""
Dynamic Subtitle Generator for Gaming Uncut.
Produces Advanced SubStation Alpha (.ass) with word-level highlight animation,
safe-zone positioning, and bold styling optimized for YouTube Shorts & Reels.
"""

from pathlib import Path
from typing import List, Dict, Any

def format_ass_timestamp(seconds: float) -> str:
    """Converts seconds float into ASS timestamp H:MM:SS.cs"""
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = seconds % 60
    centis = int(round((secs - int(secs)) * 100))
    if centis >= 100:
        centis = 99
    return f"{hours}:{minutes:02d}:{int(secs):02d}.{centis:02d}"

def generate_ass_subtitles(
    words_data: List[Dict[str, Any]],
    output_ass_path: Path,
    watermark_text: str = "GAMING UNCUT",
    words_per_batch: int = 4,
    font_name: str = "Arial Black",
    font_size: int = 68,
    primary_color: str = "&H00FFFFFF",  # BGR White
    highlight_color: str = "&H0000FFFF", # BGR Yellow
    outline_color: str = "&H00000000",   # BGR Black
    margin_v: int = 380,                 # Vertical position for captions
    clip_duration_sec: float = 60.0
) -> Path:
    """
    Generates dynamic word-by-word highlighted subtitle file (.ass).
    Uses DirectWrite native Windows font provider.
    Includes branded Watermark in top safe zone.
    """
    output_ass_path.parent.mkdir(parents=True, exist_ok=True)
    
    header = f"""[Script Info]
Title: Gaming Uncut Captions
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Captions,{font_name},{font_size},{primary_color},{highlight_color},{outline_color},&H80000000,-1,0,0,0,100,100,1,0,1,6,3,2,60,60,{margin_v},1
Style: Watermark,Arial,34,&H00FFFFFF,&H00000000,&H00000000,&HB0000000,-1,0,0,0,100,100,2,0,3,10,2,8,30,30,120,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""

    dialogue_lines = []

    # Watermark persistent throughout clip (Alignment 8 = Top Center, MarginV 120)
    if watermark_text:
        wm_end = format_ass_timestamp(clip_duration_sec)
        dialogue_lines.append(f"Dialogue: 1,0:00:00.00,{wm_end},Watermark,,0,0,0,,  {watermark_text}  ")

    # Group words into short punchy batches (3-5 words at a time)
    chunks = [words_data[i:i + words_per_batch] for i in range(0, len(words_data), words_per_batch)]

    for chunk in chunks:
        if not chunk:
            continue

        # For each word inside this chunk, highlight it during its time interval
        for active_idx, target_word in enumerate(chunk):
            w_start = format_ass_timestamp(target_word["start"])
            w_end = format_ass_timestamp(target_word["end"])
            
            # Build text where only active_idx has highlight color and scale pop
            rendered_words = []
            for idx, w in enumerate(chunk):
                raw_text = w["word"].strip().upper()
                if idx == active_idx:
                    rendered_words.append(f"{{\\c{highlight_color}\\fscx115\\fscy115}}{raw_text}{{\\r}}")
                else:
                    rendered_words.append(raw_text)

            line_text = " ".join(rendered_words)
            dialogue_lines.append(f"Dialogue: 0,{w_start},{w_end},Captions,,0,0,0,,{line_text}")

    full_content = header + "\n".join(dialogue_lines) + "\n"
    output_ass_path.write_text(full_content, encoding="utf-8")
    return output_ass_path
