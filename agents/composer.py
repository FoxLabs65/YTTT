"""
Video Composer Agent
Assembles downloaded assets into polished vertical Shorts
using MoviePy and FFmpeg.
"""

import json
import logging
import os
import re
import uuid
from pathlib import Path

from moviepy import (
    VideoFileClip,
    VideoClip,
    ImageClip,
    ColorClip,
    AudioFileClip,
    TextClip,
    CompositeVideoClip,
    CompositeAudioClip,
    concatenate_videoclips,
    vfx,
)
from PIL import Image, ImageDraw, ImageFont

from models.config import get as cfg
from models.database import (
    get_connection,
    get_scripts_by_status,
    get_assets_for_script,
    update_script_status,
    insert_video,
    reset_stuck_scripts,
)

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).parent.parent
OUTPUT_DIR = PROJECT_ROOT / "output" / "pending"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

WIDTH = 1080
HEIGHT = 1920
FPS = 30


FONT_PATHS = {
    "segoe-ui-bold": "C:/Windows/Fonts/segoeuib.ttf",
    "segoe-ui-emoji": "C:/Windows/Fonts/seguiemj.ttf",
    "impact": "C:/Windows/Fonts/impact.ttf",
    "arial-bold": "C:/Windows/Fonts/arialbd.ttf",
    "arial": "C:/Windows/Fonts/arial.ttf",
    "calibri-bold": "C:/Windows/Fonts/calibrib.ttf",
    "tahoma-bold": "C:/Windows/Fonts/tahomabd.ttf",
    "verdana-bold": "C:/Windows/Fonts/verdanab.ttf",
}

# Regex matching most emoji Unicode ranges
_EMOJI_RE = re.compile(
    "["
    "\U0001F600-\U0001F64F"  # emoticons
    "\U0001F300-\U0001F5FF"  # misc symbols & pictographs
    "\U0001F680-\U0001F6FF"  # transport & map
    "\U0001F1E0-\U0001F1FF"  # flags
    "\U0001F900-\U0001F9FF"  # supplemental symbols
    "\U0001FA00-\U0001FA6F"  # chess, extended-A
    "\U0001FA70-\U0001FAFF"  # extended-A continued
    "\U00002702-\U000027B0"  # dingbats
    "\U0000FE00-\U0000FE0F"  # variation selectors
    "\U0000200D"             # zero width joiner
    "\U000020E3"             # combining enclosing keycap
    "\U00002600-\U000026FF"  # misc symbols (⚡ etc.)
    "\U00002300-\U000023FF"  # misc technical
    "\U00002B50"             # star
    "\U0000203C-\U00003299"  # other symbols
    "]+",
    flags=re.UNICODE,
)


def _strip_emoji(text: str) -> str:
    """Remove emoji characters that can't render in standard fonts."""
    return _EMOJI_RE.sub("", text).strip()


def _resolve_font(name_or_path: str, size: int) -> ImageFont.FreeTypeFont:
    """Resolve a font name to a PIL FreeTypeFont, with cascading fallbacks."""
    key = name_or_path.lower().replace(" ", "-")

    # Direct path
    if Path(name_or_path).suffix in (".ttf", ".otf") and Path(name_or_path).exists():
        return ImageFont.truetype(name_or_path, size)

    # Known alias
    if key in FONT_PATHS and Path(FONT_PATHS[key]).exists():
        return ImageFont.truetype(FONT_PATHS[key], size)

    # Try as system font name (PIL shorthand)
    try:
        return ImageFont.truetype(f"{name_or_path}.ttf", size)
    except OSError:
        pass

    # Fallback chain: Segoe UI Bold -> Arial Bold -> default
    for fb in ("segoeuib.ttf", "arialbd.ttf", "arial.ttf"):
        try:
            return ImageFont.truetype(fb, size)
        except OSError:
            continue

    return ImageFont.load_default()


def _get_config():
    return {
        "width": cfg("composer.resolution.width") or WIDTH,
        "height": cfg("composer.resolution.height") or HEIGHT,
        "fps": cfg("composer.fps") or FPS,
        "transition_duration": cfg("composer.transition_duration") or 0.3,
        "music_volume": cfg("composer.music_volume") or 0.05,
        "font": cfg("composer.font") or "segoe-ui-bold",
        "caption_font_size": cfg("composer.caption_font_size") or 48,
        "hook_font_size": cfg("composer.hook_font_size") or 72,
        "watermark_text": cfg("composer.watermark_text") or "",
    }


def _crop_to_vertical(clip, target_w: int, target_h: int):
    """Crop and resize a clip to vertical 9:16 aspect ratio."""
    cw, ch = clip.size
    target_ratio = target_w / target_h  # 0.5625

    clip_ratio = cw / ch
    if clip_ratio > target_ratio:
        # Clip is wider than needed: crop width
        new_w = int(ch * target_ratio)
        x_center = cw // 2
        clip = clip.cropped(x1=x_center - new_w // 2, x2=x_center + new_w // 2)
    elif clip_ratio < target_ratio:
        # Clip is taller than needed: crop height
        new_h = int(cw / target_ratio)
        y_center = ch // 2
        clip = clip.cropped(y1=y_center - new_h // 2, y2=y_center + new_h // 2)

    return clip.resized((target_w, target_h))


def _make_ken_burns_clip(image_path: str, duration: float, target_w: int, target_h: int):
    """Create a Ken Burns (slow zoom) clip from a still image.
    Pre-renders the oversized image once and uses numpy slicing per frame for speed.
    """
    import numpy as np
    from PIL import Image as PILImage

    start_scale = 1.15
    end_scale = 1.0
    big_w = int(target_w * start_scale)
    big_h = int(target_h * start_scale)

    pil_img = PILImage.open(image_path).convert("RGB").resize((big_w, big_h), PILImage.LANCZOS)
    frame_array = np.array(pil_img)

    def make_frame(t):
        progress = t / max(duration, 0.001)
        scale = start_scale + (end_scale - start_scale) * progress
        crop_w = int(target_w * scale)
        crop_h = int(target_h * scale)
        x_off = (big_w - crop_w) // 2
        y_off = (big_h - crop_h) // 2
        cropped = frame_array[y_off : y_off + crop_h, x_off : x_off + crop_w]
        resized = np.array(PILImage.fromarray(cropped).resize((target_w, target_h), PILImage.BILINEAR))
        return resized

    return VideoClip(make_frame, duration=duration).with_fps(FPS)


def _create_text_image(text: str, font_size: int, target_w: int,
                       font_name: str = "segoe-ui-bold",
                       color: str = "white") -> "np.ndarray":
    """Render text to a RGBA numpy array using PIL (no ImageMagick dependency).
    Strips emoji characters and adds a semi-transparent background pill for readability.
    Keeps captions on-screen by reducing font size when text would overflow.
    """
    import numpy as np

    text = _strip_emoji(text)
    text = re.sub(r"\s+", " ", text).strip()
    if not text:
        text = " "

    max_text_w = target_w - 120
    max_caption_h = 220  # Keep captions on-screen (bottom area ~280px)
    font = _resolve_font(font_name, font_size)

    def _wrap_and_layout(fs: int):
        f = _resolve_font(font_name, fs)
        words = text.split()
        lines = []
        current = ""
        dummy_img = Image.new("RGBA", (1, 1))
        dummy_draw = ImageDraw.Draw(dummy_img)
        for word in words:
            test = f"{current} {word}".strip()
            bbox = dummy_draw.textbbox((0, 0), test, font=f)
            if bbox[2] - bbox[0] > max_text_w and current:
                lines.append(current)
                current = word
            else:
                current = test
        if current:
            lines.append(current)
        return lines, f

    lines, font = _wrap_and_layout(font_size)
    line_height = font_size + 10

    # Reduce font size if caption would go off-screen
    while len(lines) * line_height > max_caption_h - 32 and font_size > 24:
        font_size = max(24, font_size - 4)
        lines, font = _wrap_and_layout(font_size)
        line_height = font_size + 10
    padding = 16
    text_block_h = line_height * len(lines)
    img_h = text_block_h + padding * 2
    img = Image.new("RGBA", (target_w, img_h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    # Semi-transparent background pill behind text for readability
    widest_line = 0
    for line in lines:
        bbox = draw.textbbox((0, 0), line, font=font)
        widest_line = max(widest_line, bbox[2] - bbox[0])
    pill_w = min(widest_line + 60, target_w - 40)
    pill_x = (target_w - pill_w) // 2
    draw.rounded_rectangle(
        [pill_x, 0, pill_x + pill_w, img_h],
        radius=16,
        fill=(0, 0, 0, 140),
    )

    y = padding
    for line in lines:
        bbox = draw.textbbox((0, 0), line, font=font)
        text_w = bbox[2] - bbox[0]
        x = (target_w - text_w) // 2
        # Black outline for contrast
        for dx in (-2, -1, 0, 1, 2):
            for dy in (-2, -1, 0, 1, 2):
                if dx != 0 or dy != 0:
                    draw.text((x + dx, y + dy), line, fill=(0, 0, 0, 200), font=font)
        draw.text((x, y), line, fill=(255, 255, 255, 255), font=font)
        y += line_height

    return np.array(img)


TICKS_PER_SEC = 10_000_000  # edge-tts word boundary offsets are in 100ns ticks


def _load_word_boundaries(meta_path: str | Path | None) -> list[dict] | None:
    """Load word boundary timestamps from edge-tts metadata JSON.
    Returns list of {text, start_s, end_s} or None if unavailable.
    """
    if not meta_path:
        return None
    p = Path(meta_path)
    if not p.exists():
        return None

    words = []
    try:
        for line in p.read_text(encoding="utf-8").strip().splitlines():
            entry = json.loads(line)
            if entry.get("type") == "WordBoundary":
                start = entry["offset"] / TICKS_PER_SEC
                dur = entry["duration"] / TICKS_PER_SEC
                words.append({"text": entry["text"], "start_s": start, "end_s": start + dur})
    except Exception as e:
        logger.warning("Failed to parse word boundaries from %s: %s", p, e)
        return None

    return words if words else None


def _match_segments_to_boundaries(parts: list[str], boundaries: list[dict]) -> list[float]:
    """Map each text segment to its spoken duration using word boundaries.
    Returns a list of durations (seconds) for each part.
    """
    durations = []
    bi = 0  # current position in boundary list

    for part in parts:
        part_words = re.sub(r"[^\w\s']", "", part.lower()).split()
        if not part_words:
            durations.append(1.5)
            continue

        # Find the first boundary word that matches the start of this segment
        seg_start = None
        seg_end = None
        matched = 0

        for j in range(bi, len(boundaries)):
            bw = re.sub(r"[^\w']", "", boundaries[j]["text"].lower())
            if not bw:
                continue

            if seg_start is None:
                # Look for first word of segment
                if matched < len(part_words) and bw == part_words[matched]:
                    seg_start = boundaries[j]["start_s"]
                    seg_end = boundaries[j]["end_s"]
                    matched += 1
            else:
                seg_end = boundaries[j]["end_s"]
                matched += 1

            # Check if we've matched enough words to consider segment complete
            if matched >= len(part_words):
                bi = j + 1
                break

        if seg_start is not None and seg_end is not None:
            # Add a small buffer for the natural pause after the last word
            next_start = boundaries[bi]["start_s"] if bi < len(boundaries) else seg_end + 0.3
            dur = next_start - seg_start
            durations.append(max(dur, 1.0))
        else:
            durations.append(1.5)

    return durations


def _parse_script_segments(script: dict, voiceover_duration: float | None = None,
                           word_boundaries: list[dict] | None = None) -> list[dict]:
    """Break a script into timed segments based on visual cues.

    When word_boundaries are available (from edge-tts metadata), each segment's
    duration is derived from the exact spoken timestamps — giving precise
    caption-to-audio sync. Falls back to word-count weighting otherwise.
    """
    hook = script.get("hook", "")
    body = script.get("script_body", "")
    cta = script.get("cta", "")
    estimated = script.get("estimated_duration") or 30
    if isinstance(estimated, str):
        estimated = int(estimated)

    segments = []
    hook_dur = 3.0

    if hook:
        segments.append({"text": hook, "type": "hook", "duration": hook_dur})

    # Split body on visual cues or sentences
    body_clean = re.sub(r"\[.*?\]", "|||", body)
    parts = [p.strip() for p in body_clean.split("|||") if p.strip()]
    if not parts:
        parts = [s.strip() + "." for s in body.replace("[", "").replace("]", "").split(".") if s.strip()]
    if not parts:
        parts = [body]

    cta_dur = 3.0 if cta else 0.0

    if word_boundaries:
        # Precise timing from actual speech timestamps
        durations = _match_segments_to_boundaries(parts, word_boundaries)
        for part, dur in zip(parts, durations):
            segments.append({"text": part, "type": "body", "duration": dur})
        logger.info("Caption timing: using word-boundary timestamps (%d segments)", len(parts))
    else:
        # Fallback: weight by word count
        if voiceover_duration and voiceover_duration > 0:
            body_time = voiceover_duration
        else:
            body_time = max(estimated - hook_dur - cta_dur, 5.0)

        word_counts = [max(len(p.split()), 1) for p in parts]
        total_words = sum(word_counts)

        for part, wc in zip(parts, word_counts):
            proportion = wc / total_words
            dur = max(body_time * proportion, 1.5)
            segments.append({"text": part, "type": "body", "duration": dur})
        logger.info("Caption timing: word-count estimate fallback (%d segments)", len(parts))

    if cta:
        segments.append({"text": cta, "type": "cta", "duration": cta_dur})

    return segments


def compose_video(script: dict, assets: list[dict]) -> Path | None:
    """Compose a single video from a script and its assets."""
    conf = _get_config()
    w, h = conf["width"], conf["height"]
    script_id = script["id"]

    video_assets = [a for a in assets if a["asset_type"] == "video" and Path(a["local_path"]).exists()]
    image_assets = [a for a in assets if a["asset_type"] == "image" and Path(a["local_path"]).exists()]
    voiceover_assets = [a for a in assets if a["asset_type"] == "voiceover" and Path(a["local_path"]).exists()]
    music_assets = [a for a in assets if a["asset_type"] == "music" and Path(a["local_path"]).exists()]

    if not video_assets and not image_assets:
        logger.warning("Script #%d: no video or image assets found, skipping", script_id)
        return None

    # Load voiceover duration and word-boundary metadata for precise caption sync
    vo_duration = None
    word_boundaries = None
    if voiceover_assets:
        try:
            _vo_probe = AudioFileClip(voiceover_assets[0]["local_path"])
            vo_duration = _vo_probe.duration
            _vo_probe.close()
            logger.info("Script #%d: voiceover is %.1fs", script_id, vo_duration)
        except Exception:
            pass

        # Word boundary metadata stored in source_url field by sourcing agent
        meta_path = voiceover_assets[0].get("source_url")
        word_boundaries = _load_word_boundaries(meta_path)
        if word_boundaries:
            logger.info("Script #%d: loaded %d word boundaries for caption sync", script_id, len(word_boundaries))

    segments = _parse_script_segments(script, voiceover_duration=vo_duration, word_boundaries=word_boundaries)
    if not segments:
        logger.warning("Script #%d: no segments parsed (empty hook/body/cta), skipping", script_id)
        return None

    total_duration = sum(s["duration"] for s in segments)
    if total_duration <= 0:
        logger.warning("Script #%d: total duration <= 0, skipping", script_id)
        return None

    # Calculate how long the hook visual lasts (voiceover starts after this)
    hook_offset = 0.0
    for seg in segments:
        if seg["type"] == "hook":
            hook_offset = seg["duration"]
            break

    # Build clip sequence
    clips = []
    asset_idx = 0

    for seg in segments:
        dur = seg["duration"]
        if dur <= 0:
            dur = 1.5  # avoid zero-duration clips that cause MoviePy buffer errors

        # Pick a visual source
        if video_assets and asset_idx < len(video_assets):
            asset = video_assets[asset_idx % len(video_assets)]
            try:
                clip = VideoFileClip(asset["local_path"])
                clip = _crop_to_vertical(clip, w, h)
                # Trim to segment duration
                if clip.duration > dur:
                    clip = clip.subclipped(0, dur)
                elif clip.duration < dur:
                    clip = clip.with_effects([vfx.Loop(duration=dur)])
                clips.append(clip)
                asset_idx += 1
                continue
            except Exception as e:
                logger.warning("Failed to load video asset %s: %s", asset["local_path"], e)

        if image_assets:
            asset = image_assets[asset_idx % len(image_assets)]
            try:
                clip = _make_ken_burns_clip(asset["local_path"], dur, w, h)
                clips.append(clip)
                asset_idx += 1
                continue
            except Exception as e:
                logger.warning("Failed to load image asset %s: %s", asset["local_path"], e)

        # Fallback: dark background
        clip = ColorClip(size=(w, h), color=(20, 20, 30), duration=dur).with_fps(FPS)
        clips.append(clip)

    if not clips:
        logger.warning("Script #%d: could not create any clips", script_id)
        return None

    # Filter out zero-duration clips (can cause index-out-of-bounds in MoviePy)
    valid_clips = [c for c in clips if hasattr(c, "duration") and c.duration and c.duration > 0]
    if not valid_clips:
        logger.warning("Script #%d: all clips have zero/empty duration", script_id)
        for c in clips:
            try:
                c.close()
            except Exception:
                pass
        return None

    # Concatenate base video
    try:
        base_video = concatenate_videoclips(valid_clips, method="compose")
    except Exception as e:
        logger.error("Failed to concatenate clips for script #%d: %s", script_id, e)
        return None

    # Add text overlays per segment using PIL-rendered images
    overlay_clips = [base_video]
    font_name = conf["font"]
    current_time = 0.0
    for seg in segments:
        text = seg["text"]
        dur = seg["duration"]

        if seg["type"] == "hook":
            font_size = conf["hook_font_size"]
            y_pos = h // 3  # hook centred upper-third
        elif seg["type"] == "cta":
            font_size = conf["caption_font_size"]
            y_pos = h - 320  # CTA near bottom
        else:
            font_size = conf["caption_font_size"]
            y_pos = h - 280  # body captions at bottom of frame

        try:
            text_arr = _create_text_image(text, font_size, w, font_name=font_name)
            if text_arr.size == 0 or (len(text_arr.shape) >= 2 and (text_arr.shape[0] == 0 or text_arr.shape[1] == 0)):
                logger.warning("Script #%d: text overlay produced empty image for segment, skipping", script_id)
            else:
                txt_clip = (
                    ImageClip(text_arr, transparent=True)
                    .with_duration(dur)
                    .with_start(current_time)
                    .with_position(("center", y_pos))
                )
                overlay_clips.append(txt_clip)
        except Exception as e:
            logger.warning("Text overlay failed for script #%d: %s", script_id, e)

        current_time += dur

    # Composite video with overlays
    final_video = CompositeVideoClip(overlay_clips, size=(w, h))

    # Audio: voiceover (delayed by hook_offset) + background music
    audio_tracks = []

    if voiceover_assets:
        try:
            vo = AudioFileClip(voiceover_assets[0]["local_path"])
            if vo.duration <= 0:
                logger.warning("Script #%d: voiceover has zero duration, skipping", script_id)
                vo.close()
            else:
                max_vo = final_video.duration - hook_offset
                if max_vo <= 0:
                    vo.close()
                else:
                    if vo.duration > max_vo:
                        vo = vo.subclipped(0, max_vo)
                    # Delay voiceover to start after the visual-only hook
                    if hook_offset > 0:
                        vo = vo.with_start(hook_offset)
                        logger.info("Script #%d: voiceover delayed %.1fs for hook", script_id, hook_offset)
                    audio_tracks.append(vo)
        except Exception as e:
            logger.warning("Failed to load voiceover for script #%d: %s", script_id, e)

    music_use = music_assets
    override_path = script.get("music_override_path")
    if override_path and Path(override_path).exists():
        music_use = [{"local_path": override_path}]
    if music_use:
        try:
            music = AudioFileClip(music_use[0]["local_path"])
            music_path = music_use[0]["local_path"]
            min_dur = float(cfg("sourcing.music.min_music_duration_seconds") or 5)
            if music.duration <= 0:
                logger.warning("Script #%d: music has zero duration, trying fallback: %s", script_id, Path(music_path).name)
                music.close()
                from agents.music_scraper import get_fallback_music_for_script
                fallback_path = get_fallback_music_for_script(script_id, script, excluded_path=music_path)
                if fallback_path and fallback_path.exists():
                    music = AudioFileClip(str(fallback_path))
                    music_path = str(fallback_path)
                else:
                    music = None
            elif music.duration < min_dur:
                logger.warning(
                    "Script #%d: music too short (%.1fs), trying fallback to avoid render buffer errors: %s",
                    script_id, music.duration, Path(music_path).name,
                )
                music.close()
                from agents.music_scraper import get_fallback_music_for_script
                fallback_path = get_fallback_music_for_script(script_id, script, excluded_path=music_path)
                if fallback_path and fallback_path.exists():
                    music = AudioFileClip(str(fallback_path))
                    music_path = str(fallback_path)
                else:
                    music = None
            if music is not None and music.duration >= min_dur:
                if music.duration < final_video.duration:
                    music = music.with_effects([vfx.Loop(duration=final_video.duration)])
                else:
                    music = music.subclipped(0, final_video.duration)
                music = music.with_volume_scaled(conf["music_volume"])
                audio_tracks.append(music)
            elif music is not None:
                music.close()
        except Exception as e:
            logger.warning("Failed to load music for script #%d: %s", script_id, e)

    if audio_tracks:
        final_audio = CompositeAudioClip(audio_tracks)
        final_video = final_video.with_audio(final_audio)

    # Export
    output_filename = f"short_{script_id}_{uuid.uuid4().hex[:8]}.mp4"
    output_path = OUTPUT_DIR / output_filename

    logger.info("Rendering script #%d to %s (%.1fs)...", script_id, output_filename, final_video.duration)
    try:
        final_video.write_videofile(
            str(output_path),
            fps=conf["fps"],
            codec="libx264",
            audio_codec="aac",
            preset="medium",
            threads=4,
            logger=None,
        )
    except Exception as e:
        logger.error(
            "Render failed for script #%d: %s (videos=%d, images=%d, voiceover=%s, music=%s)",
            script_id, e,
            len(video_assets), len(image_assets),
            "yes" if voiceover_assets else "no",
            "yes" if music_use else "no",
        )
        return None
    finally:
        final_video.close()
        for c in clips:
            try:
                c.close()
            except Exception:
                pass

    logger.info("Rendered: %s (%.1f MB)", output_filename, output_path.stat().st_size / 1024 / 1024)
    return output_path


def generate_thumbnail(
    video_path: Path,
    title: str,
    source_assets: list[dict] | None = None,
    frame_time: float | None = None,
) -> Path | None:
    """Generate a clean thumbnail from a raw source asset (no captions baked in)
    and overlay only the title text.
    frame_time: when extracting from video, use this time in seconds (0 = start).
    """
    try:
        frame = None
        conf = _get_config()
        w, h = conf["width"], conf["height"]

        # Try to grab a frame from a raw source clip (before text overlays)
        if source_assets:
            for asset in source_assets:
                if asset.get("asset_type") == "video" and Path(asset["local_path"]).exists():
                    try:
                        raw_clip = VideoFileClip(asset["local_path"])
                        t = frame_time if frame_time is not None else min(2.0, raw_clip.duration / 2)
                        t = min(max(0, t), raw_clip.duration - 0.01)
                        cropped = _crop_to_vertical(raw_clip, w, h)
                        frame = cropped.get_frame(t)
                        raw_clip.close()
                        break
                    except Exception:
                        continue
                elif asset.get("asset_type") == "image" and Path(asset["local_path"]).exists():
                    try:
                        pil_img = Image.open(asset["local_path"]).convert("RGB").resize((w, h), Image.LANCZOS)
                        import numpy as np
                        frame = np.array(pil_img)
                        break
                    except Exception:
                        continue

        # Fallback: extract from the rendered video
        if frame is None:
            clip = VideoFileClip(str(video_path))
            t = frame_time if frame_time is not None else min(0.5, clip.duration / 4)
            t = min(max(0, t), clip.duration - 0.01)
            frame = clip.get_frame(t)
            clip.close()

        img = Image.fromarray(frame)

        # Add semi-transparent overlay bar in the centre
        bar_height = 240
        bar_y = img.height // 2 - bar_height // 2
        overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
        overlay_draw = ImageDraw.Draw(overlay)
        overlay_draw.rectangle(
            [0, bar_y, img.width, bar_y + bar_height],
            fill=(0, 0, 0, 160),
        )
        img = img.convert("RGBA")
        img = Image.alpha_composite(img, overlay)

        # Draw title text only (no subtitle/caption text)
        title = _strip_emoji(title)
        draw = ImageDraw.Draw(img)
        font = _resolve_font(conf["font"], 68)

        max_text_w = img.width - 120
        words = title.split()
        lines = []
        current = ""
        for word in words:
            test = f"{current} {word}".strip()
            bbox = draw.textbbox((0, 0), test, font=font)
            if bbox[2] - bbox[0] > max_text_w:
                if current:
                    lines.append(current)
                current = word
            else:
                current = test
        if current:
            lines.append(current)

        # Centre the text block vertically within the bar
        line_height = 78
        total_text_height = line_height * len(lines)
        y = bar_y + (bar_height - total_text_height) // 2

        for line in lines:
            bbox = draw.textbbox((0, 0), line, font=font)
            text_w = bbox[2] - bbox[0]
            x = (img.width - text_w) // 2
            # Bold outline
            for dx in (-3, -2, -1, 0, 1, 2, 3):
                for dy in (-3, -2, -1, 0, 1, 2, 3):
                    if dx != 0 or dy != 0:
                        draw.text((x + dx, y + dy), line, fill=(0, 0, 0, 220), font=font)
            draw.text((x, y), line, fill="white", font=font)
            y += line_height

        thumb_path = video_path.with_suffix(".jpg")
        img.convert("RGB").save(str(thumb_path), "JPEG", quality=92)
        logger.info("Thumbnail saved: %s", thumb_path.name)
        return thumb_path
    except Exception as e:
        logger.warning("Thumbnail generation failed: %s", e)
        return None


def _build_description(script: dict) -> tuple[str, str, str]:
    """Build YouTube description, TikTok caption, and tag list."""
    tags = script.get("suggested_tags", "[]")
    if isinstance(tags, str):
        try:
            tags = json.loads(tags)
        except json.JSONDecodeError:
            tags = []

    yt_tags = tags + (cfg("upload.youtube.default_tags") or [])
    tag_str = " ".join(f"#{t.lstrip('#')}" for t in tags[:10])
    footer = cfg("upload.youtube.description_footer") or ""

    yt_desc = f"{script.get('hook', '')}\n\n{tag_str}\n\n{footer}".strip()
    tt_caption = f"{script.get('hook', '')} {tag_str}".strip()

    return yt_desc, tt_caption, json.dumps(yt_tags)


def compose_for_script(script: dict) -> bool:
    """Full compose pipeline for one script."""
    conn = get_connection()
    assets = get_assets_for_script(conn, script["id"])

    update_script_status(conn, script["id"], "composing")
    video_path = compose_video(script, assets)

    if not video_path:
        update_script_status(conn, script["id"], "assets_ready")
        conn.close()
        return False

    thumb_path = generate_thumbnail(video_path, script.get("title", ""), source_assets=assets)
    yt_desc, tt_caption, yt_tags = _build_description(script)

    file_size = video_path.stat().st_size / (1024 * 1024)
    try:
        probe = VideoFileClip(str(video_path))
        duration = probe.duration
        probe.close()
    except Exception:
        duration = script.get("estimated_duration") or 30

    insert_video(
        conn,
        script_id=script["id"],
        file_path=str(video_path),
        thumbnail_path=str(thumb_path) if thumb_path else None,
        duration=duration,
        resolution=f"{_get_config()['width']}x{_get_config()['height']}",
        file_size_mb=round(file_size, 2),
        yt_title=script.get("title", "Untitled"),
        yt_description=yt_desc,
        yt_tags=yt_tags,
        tt_caption=tt_caption,
        status="pending",
    )

    update_script_status(conn, script["id"], "composed")
    conn.close()
    logger.info("Script #%d composed successfully", script["id"])
    return True


def run_composer(script_id: int | None = None) -> dict:
    """Compose videos for scripts with ready assets.
    When script_id is provided (e.g. from regenerate), only that script is processed.
    """
    logger.info("Starting video composition...")
    conn = get_connection()

    # Recover scripts stuck in 'composing' (crashed mid-render) back to 'assets_ready'
    reset_count = reset_stuck_scripts(conn, from_status="composing", to_status="assets_ready", stuck_minutes=30)
    if reset_count:
        logger.info("Recovered %d script(s) stuck in 'composing' status", reset_count)

    scripts = get_scripts_by_status(conn, "assets_ready")
    if script_id is not None:
        scripts = [s for s in scripts if s.get("id") == script_id]
        if not scripts:
            logger.info("Script #%d not in assets_ready, nothing to compose", script_id)
            conn.close()
            return {"scripts_processed": 0, "success": 0}
        logger.info("Composing only script #%d (regenerate)", script_id)
    conn.close()

    if not scripts:
        logger.info("No scripts ready for composition")
        return {"scripts_processed": 0, "success": 0}

    success = 0
    errors = []
    for script in scripts:
        try:
            if compose_for_script(script):
                success += 1
            else:
                errors.append(f"script #{script.get('id', '?')}: returned False")
        except Exception as e:
            errors.append(f"script #{script.get('id', '?')}: {e}")
            logger.error("Composition failed for script #%d: %s", script["id"], e)

    summary = {"scripts_processed": len(scripts), "success": success}
    if scripts and success == 0:
        summary["error"] = f"All {len(scripts)} scripts failed composition: {'; '.join(errors[:3])}"
    logger.info("Composition complete: %s", summary)
    return summary


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        handlers=[
            logging.StreamHandler(),
            logging.FileHandler(PROJECT_ROOT / "logs" / "composer.log"),
        ],
    )
    from models.database import init_db
    init_db()
    result = run_composer()
    print(json.dumps(result, indent=2))
