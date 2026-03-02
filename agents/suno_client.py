"""
Suno API client for AI music generation.
Generates background music from text prompts using script metadata.
"""

import logging
import time
from pathlib import Path

import requests

logger = logging.getLogger(__name__)

SUNO_BASE = "https://api.sunoapi.org/api/v1"
POLL_INTERVAL = 15
MAX_POLL_TIME = 300  # 5 minutes


def _build_suno_prompt(
    category: str,
    script_keywords: list[str] | None = None,
    tags: list[str] | None = None,
    trend_topic: str | None = None,
) -> str:
    """Build a Suno prompt from script and scraping metadata.
    Used when suno_prompt_override is empty.
    Max 500 chars for non-custom mode.
    """
    from agents.music_scraper import CATEGORY_MUSIC_MAP, TOPIC_MUSIC_MODIFIERS

    music_config = CATEGORY_MUSIC_MAP.get(category, CATEGORY_MUSIC_MAP["storytime"])
    mood = music_config.get("mood", "chill")
    fs_tags = music_config.get("freesound_tags", [])[:4]
    fs_snippet = ", ".join(fs_tags) if fs_tags else mood

    kw_snippet = ""
    if script_keywords:
        kw_snippet = ", ".join(script_keywords[:8])
    if tags:
        tag_str = ", ".join(str(t) for t in tags[:5])
        kw_snippet = f"{kw_snippet}, {tag_str}".strip(", ")

    trend_mod = ""
    if trend_topic and trend_topic in TOPIC_MUSIC_MODIFIERS:
        trend_mod = ", " + ", ".join(TOPIC_MUSIC_MODIFIERS[trend_topic])

    prompt = f"{mood} instrumental background music, {fs_snippet}"
    if kw_snippet:
        prompt += f", {kw_snippet}"
    prompt += trend_mod

    return prompt[:500].strip()


def _build_suno_style(
    category: str,
    style_override: str | None = None,
) -> str:
    """Build style string for Suno (custom mode)."""
    if style_override and style_override.strip():
        return style_override.strip()[:1000]

    from agents.music_scraper import CATEGORY_MUSIC_MAP

    music_config = CATEGORY_MUSIC_MAP.get(category, CATEGORY_MUSIC_MAP["storytime"])
    mood = music_config.get("mood", "chill")
    first_query = music_config.get("queries", [""])[0] if music_config.get("queries") else ""
    return f"{mood}, {first_query}"[:1000]


def _build_suno_title(mood: str, title_override: str | None = None) -> str:
    """Build title for Suno (custom mode)."""
    if title_override and title_override.strip():
        return title_override.strip()[:100]
    return f"ShortsBg_{mood}"[:80]


def generate_music(
    prompt: str,
    *,
    api_key: str,
    model: str = "V4_5ALL",
    instrumental: bool = True,
    custom_mode: bool = False,
    style: str = "",
    title: str = "",
    negative_tags: str = "",
    weirdness: float = 0.5,
    style_weight: float = 0.65,
    dest_path: Path,
) -> Path | None:
    """Submit Suno job, poll until complete, download first track.
    Returns local path or None on failure.
    """
    if not api_key or api_key.startswith("YOUR_"):
        logger.warning("Suno API key not configured")
        return None

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    payload = {
        "model": model,
        "customMode": custom_mode,
        "instrumental": instrumental,
        "callBackUrl": "https://example.com/callback",
        "weirdnessConstraint": round(weirdness, 2),
        "styleWeight": round(style_weight, 2),
    }

    if prompt:
        payload["prompt"] = prompt[:500]

    if custom_mode:
        payload["style"] = style or "Instrumental background"
        payload["title"] = title or "ShortsBg"
        if not instrumental and not prompt:
            payload["prompt"] = "Instrumental background music"

    if negative_tags:
        payload["negativeTags"] = negative_tags[:500]

    try:
        resp = requests.post(
            f"{SUNO_BASE}/generate",
            json=payload,
            headers=headers,
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()
        if data.get("code") != 200:
            logger.warning("Suno API error: %s", data.get("msg", "Unknown"))
            return None

        task_id = data.get("data", {}).get("taskId")
        if not task_id:
            logger.warning("Suno API: no taskId in response")
            return None

        logger.info("Suno job submitted: %s", task_id)

        elapsed = 0
        while elapsed < MAX_POLL_TIME:
            time.sleep(POLL_INTERVAL)
            elapsed += POLL_INTERVAL

            status_resp = requests.get(
                f"{SUNO_BASE}/generate/record-info",
                params={"taskId": task_id},
                headers=headers,
                timeout=30,
            )
            status_resp.raise_for_status()
            status_data = status_resp.json()

            if status_data.get("code") != 200:
                continue

            inner = status_data.get("data", {})
            status = inner.get("status")

            if status in ("SUCCESS", "FIRST_SUCCESS"):
                # API returns sunoData (camelCase) with audioUrl; some versions use response.data
                resp_obj = inner.get("response", {})
                response_data = resp_obj.get("sunoData") or resp_obj.get("data", [])
                if not response_data:
                    logger.warning("Suno: success but no audio in response (missing sunoData)")
                    return None

                first = response_data[0] if isinstance(response_data[0], dict) else {}
                audio_url = first.get("audioUrl") or first.get("audio_url")
                if not audio_url:
                    logger.warning("Suno: no audioUrl in response")
                    return None

                dest_path.parent.mkdir(parents=True, exist_ok=True)
                dl_resp = requests.get(audio_url, timeout=120, stream=True)
                dl_resp.raise_for_status()
                with open(dest_path, "wb") as f:
                    for chunk in dl_resp.iter_content(chunk_size=8192):
                        f.write(chunk)

                if dest_path.exists() and dest_path.stat().st_size > 1000:
                    logger.info("Suno: downloaded %s (%d bytes)", dest_path.name, dest_path.stat().st_size)
                    return dest_path
                return None

            if status in ("FAILED", "ERROR"):
                logger.warning("Suno generation failed: %s", status)
                return None

            logger.debug("Suno: still generating (%s), waiting...", status)

        logger.warning("Suno: timed out after %ds", MAX_POLL_TIME)
        return None

    except requests.RequestException as e:
        logger.error("Suno API request failed: %s", e)
        return None
