"""
AI Video Generation Providers
Optional fallback when stock video search returns low scores or no results.
"""

import json
import logging
import time
import uuid
from pathlib import Path

import requests

from models.config import get as cfg

logger = logging.getLogger(__name__)

AI_VIDEO_PROVIDERS: dict[str, callable] = {}


def _write_ai_video_meta(
    path: Path,
    *,
    keywords: list[str],
    category: str,
    prompt: str = "",
    provider: str = "segmind",
) -> None:
    """Write .meta.json alongside AI video for reuse scoring in future runs."""
    meta_path = path.with_name(path.stem + ".meta.json")
    try:
        meta_path.write_text(
            json.dumps({
                "keywords": keywords,
                "category": category,
                "prompt": prompt[:500] if prompt else "",
                "provider": provider,
            }, indent=2),
            encoding="utf-8",
        )
    except OSError as e:
        logger.debug("Could not write AI video meta for %s: %s", path.name, e)


def _generate_segmind(prompt: str, duration: int, aspect_ratio: str, dest_dir: Path) -> Path | None:
    """Generate video via Segmind Veo. Returns local path or None."""
    api_key = cfg("segmind_api_key") or ""
    if not api_key or api_key.startswith("YOUR_"):
        return None

    # Segmind video endpoint - check docs for exact URL (e.g. /v1/veo-2 or /v1/veo-3)
    url = "https://api.segmind.com/v1/veo-2"
    headers = {"x-api-key": api_key, "Content-Type": "application/json"}
    payload = {
        "prompt": prompt[:500],
        "duration": min(duration, 8),
        "aspect_ratio": aspect_ratio.replace("x", ":") if "x" in aspect_ratio else aspect_ratio,
    }

    try:
        r = requests.post(url, headers=headers, json=payload, timeout=60)
        r.raise_for_status()
        data = r.json()
        video_url = data.get("url") or data.get("video_url") or (data.get("output", [{}])[0] if isinstance(data.get("output"), list) else None)
        if isinstance(video_url, dict):
            video_url = video_url.get("url")
        if not video_url or not str(video_url).startswith("http"):
            logger.warning("Segmind: no video URL in response")
            return None

        resp = requests.get(video_url, timeout=120, stream=True)
        resp.raise_for_status()
        stem = f"segmind_{uuid.uuid4().hex[:12]}"
        dest = dest_dir / f"{stem}.mp4"
        with open(dest, "wb") as f:
            for chunk in resp.iter_content(chunk_size=65536):
                f.write(chunk)
        if dest.stat().st_size < 10000:
            dest.unlink(missing_ok=True)
            return None
        logger.info("AI video generated: %s", dest.name)
        return dest
    except requests.RequestException as e:
        logger.warning("Segmind video generation failed: %s", e)
        return None
    except Exception as e:
        logger.warning("Segmind video error: %s", e)
        return None


AI_VIDEO_PROVIDERS["segmind"] = _generate_segmind


def generate_ai_video(
    prompt: str,
    duration: int = 8,
    aspect_ratio: str = "9:16",
    dest_dir: Path | None = None,
) -> Path | None:
    """Try configured AI video providers in order. Returns local path or None."""
    providers = cfg("sourcing.ai_video_providers") or []
    if not providers:
        return None
    dest_dir = dest_dir or Path(__file__).parent.parent / "assets" / "stock_footage" / "ai"
    dest_dir.mkdir(parents=True, exist_ok=True)
    max_dur = int(cfg("sourcing.ai_video_max_duration") or 8)

    for name in providers:
        fn = AI_VIDEO_PROVIDERS.get(name)
        if fn:
            path = fn(prompt[:500], min(duration, max_dur), aspect_ratio or "9:16", dest_dir)
            if path and path.exists():
                return path
    return None


def write_ai_video_meta(
    path: Path,
    *,
    keywords: list[str],
    category: str,
    prompt: str = "",
    provider: str = "segmind",
) -> None:
    """Public alias for metadata writing."""
    _write_ai_video_meta(path, keywords=keywords, category=category, prompt=prompt, provider=provider)
