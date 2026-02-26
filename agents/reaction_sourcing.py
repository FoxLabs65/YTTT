"""
YouTube Reaction Shorts - Optional Module

Discovers long-form YouTube videos, extracts candidate moments for reaction shorts,
and supports human-in-the-loop review before clip extraction and composition.

DISCLAIMER: Downloading YouTube content may violate YouTube ToS. Use at your own risk.
Reaction/commentary content may qualify as fair use; consult legal advice for your use case.
"""

import json
import logging
import re
import subprocess
from pathlib import Path

from models.config import get as cfg

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).parent.parent
REACTION_DATA = PROJECT_ROOT / "data" / "reaction"
CANDIDATES_FILE = REACTION_DATA / "candidates.json"
CLIPS_DIR = PROJECT_ROOT / "assets" / "reaction_clips"

# Source categories with clearer fair-use precedent (gaming, podcasts)
# Exclude high-risk: sports, major TV
DEFAULT_ALLOWED_CATEGORIES = ["gaming", "podcast", "entertainment", "comedy", "education"]


def _ensure_dirs():
    REACTION_DATA.mkdir(parents=True, exist_ok=True)
    CLIPS_DIR.mkdir(parents=True, exist_ok=True)


def _load_candidates() -> list[dict]:
    if not CANDIDATES_FILE.exists():
        return []
    try:
        data = json.loads(CANDIDATES_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else data.get("candidates", [])
    except (json.JSONDecodeError, OSError) as e:
        logger.warning("Could not load reaction candidates: %s", e)
        return []


def _save_candidates(candidates: list[dict]):
    _ensure_dirs()
    CANDIDATES_FILE.write_text(json.dumps(candidates, indent=2), encoding="utf-8")


def discover_candidates(
    max_videos: int = 10,
    min_duration_min: int = 10,
    max_duration_min: int = 120,
    categories: list[str] | None = None,
) -> list[dict]:
    """
    Discover long-form YouTube videos and extract candidate moments.
    Returns list of candidates for human review.
    """
    _ensure_dirs()
    categories = categories or cfg("reaction.allowed_categories") or DEFAULT_ALLOWED_CATEGORIES

    # Use yt-dlp to search (no YouTube API key required for search)
    # yt-dlp ytsearch10:gaming highlights
    candidates = []
    seen_ids = set()

    for cat in categories[:5]:
        query = f"{cat} long form"
        cmd = [
            "yt-dlp",
            "--no-download",
            "--dump-json",
            "--no-warnings",
            "--quiet",
            f"ytsearch{max_videos}:{query}",
        ]
        try:
            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=60, encoding="utf-8",
            )
            if result.returncode != 0:
                continue

            for line in result.stdout.strip().split("\n"):
                if not line.strip():
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue

                vid_id = entry.get("id")
                if not vid_id or vid_id in seen_ids:
                    continue

                duration = entry.get("duration") or 0
                if duration < min_duration_min * 60 or duration > max_duration_min * 60:
                    continue

                seen_ids.add(vid_id)
                title = entry.get("title", "")[:100]
                channel = entry.get("channel") or entry.get("uploader", "Unknown")

                # Try to get chapters from description
                desc = entry.get("description") or ""
                chapters = _parse_chapters_from_description(desc)

                if chapters:
                    for ch in chapters[:5]:  # Max 5 candidates per video
                        candidates.append({
                            "id": f"{vid_id}_{ch['start']}_{ch['end']}",
                            "video_id": vid_id,
                            "video_url": f"https://www.youtube.com/watch?v={vid_id}",
                            "title": title,
                            "channel": channel,
                            "chapter_title": ch.get("title", ""),
                            "start_sec": ch["start"],
                            "end_sec": ch["end"],
                            "duration_sec": ch["end"] - ch["start"],
                            "status": "pending_review",
                            "created_at": __import__("datetime").datetime.now().isoformat(),
                        })
                else:
                    # No chapters: suggest first 30-60s as candidate (user can adjust)
                    candidates.append({
                        "id": f"{vid_id}_0_45",
                        "video_id": vid_id,
                        "video_url": f"https://www.youtube.com/watch?v={vid_id}",
                        "title": title,
                        "channel": channel,
                        "chapter_title": "(no chapters - suggested clip)",
                        "start_sec": 0,
                        "end_sec": min(45, duration),
                        "duration_sec": min(45, duration),
                        "status": "pending_review",
                        "created_at": __import__("datetime").datetime.now().isoformat(),
                    })

                if len(candidates) >= max_videos * 3:
                    break
        except (subprocess.TimeoutExpired, FileNotFoundError) as e:
            logger.warning("Reaction discovery failed for %s: %s", cat, e)

        if len(candidates) >= max_videos * 3:
            break

    existing = {c["id"]: c for c in _load_candidates()}
    for c in candidates:
        if c["id"] not in existing:
            existing[c["id"]] = c
    merged = list(existing.values())
    _save_candidates(merged)
    logger.info("Reaction discovery: %d candidates (pending review)", len(candidates))
    return candidates


def _parse_chapters_from_description(desc: str) -> list[dict]:
    """Parse YouTube-style chapters from description (0:00 Intro, etc.)."""
    chapters = []
    for m in re.finditer(r"(\d{1,2}):(\d{2})(?:\s+[-–—]\s+)?(.+?)(?=\n|\d{1,2}:\d{2}|$)", desc, re.DOTALL):
        start_min, start_sec, title = int(m.group(1)), int(m.group(2)), m.group(3).strip()[:80]
        start = start_min * 60 + start_sec
        if start < 0 or start > 36000:
            continue
        # Estimate end from next chapter or +30s
        end = start + 30
        chapters.append({"start": start, "end": end, "title": title})

    # Fill end times from next chapter
    for i in range(len(chapters) - 1):
        chapters[i]["end"] = chapters[i + 1]["start"]
    if chapters and len(chapters) > 1:
        chapters[-1]["end"] = chapters[-1]["start"] + 60  # Last chapter: 60s default

    return [c for c in chapters if 5 <= (c["end"] - c["start"]) <= 90]


def approve_candidates(ids: list[str]) -> int:
    """Mark candidates as approved for extraction."""
    candidates = _load_candidates()
    count = 0
    for c in candidates:
        if c["id"] in ids:
            c["status"] = "approved"
            count += 1
    _save_candidates(candidates)
    return count


def get_pending_review() -> list[dict]:
    """Get candidates pending human review."""
    return [c for c in _load_candidates() if c.get("status") == "pending_review"]


def get_approved() -> list[dict]:
    """Get approved candidates ready for extraction."""
    return [c for c in _load_candidates() if c.get("status") == "approved"]


def extract_clip(candidate: dict) -> Path | None:
    """Extract a clip from YouTube using yt-dlp --download-sections."""
    _ensure_dirs()
    vid_id = candidate["video_id"]
    start = candidate["start_sec"]
    end = candidate["end_sec"]
    clip_id = candidate["id"]

    out_path = CLIPS_DIR / f"{clip_id}.mp4"
    if out_path.exists():
        return out_path

    url = f"https://www.youtube.com/watch?v={vid_id}"
    section = f"*{start}-{end}"
    cmd = [
        "yt-dlp",
        "--download-sections", section,
        "--output", str(out_path.with_suffix(".%(ext)s")),
        "--no-warnings",
        "--quiet",
        url,
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120, encoding="utf-8")
        if out_path.exists() and out_path.stat().st_size > 10000:
            return out_path
        # yt-dlp may use different extension
        for p in CLIPS_DIR.glob(f"{clip_id}.*"):
            if p.stat().st_size > 10000:
                return p
    except (subprocess.TimeoutExpired, FileNotFoundError) as e:
        logger.warning("Clip extraction failed for %s: %s", clip_id, e)
    return None


def run_extraction(approved_ids: list[str] | None = None) -> list[Path]:
    """Extract clips for approved candidates. Returns list of clip paths."""
    candidates = _load_candidates()
    approved = get_approved() if not approved_ids else [c for c in candidates if c["id"] in (approved_ids or [])]
    extracted = []
    for c in approved:
        path = extract_clip(c)
        if path:
            extracted.append(path)
            c["status"] = "extracted"
            c["local_path"] = str(path)
    _save_candidates(candidates)
    return extracted
