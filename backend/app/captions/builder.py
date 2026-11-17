"""
Advanced SubStation Alpha (.ass) Subtitle Generator for Gaming Uncut.
Formats word-level animations with native Windows DirectWrite fonts.
Includes Gaming Uncut brand identity in safe-zone header.
"""

from pathlib import Path
from typing import List, Dict, Any

def format_ass_time(seconds: float) -> str:
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = seconds % 60
    cs = int(round((s - int(s)) * 100))
    if cs >= 100:
        cs = 99
    return f"{h}:{m:02d}:{int(s):02d}.{cs:02d}"

def generate_animated_ass(
    words_data: List[Dict[str, Any]],
    output_ass_path: Path,
    brand_text: str = "GAMING UNCUT",
    tagline: str = "NO FILTER. JUST GAMEPLAY.",
    font_name: str = "Arial Black",
    font_size: int = 66,
    primary_color: str = "&H00FFFFFF",  # White
    highlight_color: str = "&H0000FFFF",# Yellow
    outline_color: str = "&H00000000",  # Black
    margin_v: int = 420,                # Safe zone above YouTube/Instagram UI
    duration_sec: float = 60.0
) -> Path:
    output_ass_path.parent.mkdir(parents=True, exist_ok=True)

    header = f"""[Script Info]
Title: Gaming Uncut Dynamic Captions
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Captions,{font_name},{font_size},{primary_color},{highlight_color},{outline_color},&H80000000,-1,0,0,0,100,100,1,0,1,6,3,2,60,60,{margin_v},1
Style: BrandHeader,Arial,30,&H00FFFFFF,&H00000000,&H00000000,&H90000000,-1,0,0,0,100,100,2,0,3,10,2,8,30,30,130,1
Style: BrandTagline,Arial,22,&H0000FFFF,&H00000000,&H00000000,&H90000000,-1,0,0,0,100,100,1,0,3,8,2,8,30,30,180,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    dialogue_lines = []
    end_time_str = format_ass_time(duration_sec)

    # Persistent Brand Header in Top Safe Zone
    if brand_text:
        dialogue_lines.append(f"Dialogue: 1,0:00:00.00,{end_time_str},BrandHeader,,0,0,0,,  {brand_text}  ")
    if tagline:
        dialogue_lines.append(f"Dialogue: 1,0:00:00.00,{end_time_str},BrandTagline,,0,0,0,,  {tagline}  ")

    # Word-level animation batches (3-4 words per line)
    batch_size = 4
    chunks = [words_data[i:i + batch_size] for i in range(0, len(words_data), batch_size)]

    for chunk in chunks:
        if not chunk:
            continue
        for active_idx, target_word in enumerate(chunk):
            w_start = format_ass_time(target_word["start"])
            w_end = format_ass_time(target_word["end"])
            
            words_rendered = []
            for idx, w in enumerate(chunk):
                raw = w["word"].strip().upper()
                if idx == active_idx:
                    # Highlight color & scale pop
                    words_rendered.append(f"{{\\c{highlight_color}\\fscx115\\fscy115}}{raw}{{\\r}}")
                else:
                    words_rendered.append(raw)
                    
            line_str = " ".join(words_rendered)
            dialogue_lines.append(f"Dialogue: 0,{w_start},{w_end},Captions,,0,0,0,,{line_str}")

    output_ass_path.write_text(header + "\n".join(dialogue_lines) + "\n", encoding="utf-8")
    return output_ass_path
