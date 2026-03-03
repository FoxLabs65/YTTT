"""
AI Image Generation Providers
Optional fallback when stock image search returns low scores or no results.
"""

import json
import logging
import uuid
from pathlib import Path

from models.config import get as cfg

logger = logging.getLogger(__name__)

AI_IMAGE_PROVIDERS: dict[str, callable] = {}


def write_ai_image_meta(
    path: Path,
    *,
    keywords: list[str],
    category: str,
    prompt: str = "",
    provider: str = "flux",
) -> None:
    """Write .meta.json alongside AI image for reuse scoring in future runs."""
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
        logger.debug("Could not write AI image meta for %s: %s", path.name, e)


def _generate_flux(prompt: str, aspect_ratio: str, dest_dir: Path) -> Path | None:
    """Generate image via Replicate Flux Schnell. Returns local path or None."""
    token = cfg("replicate_api_token") or ""
    if not token or token.startswith("YOUR_"):
        return None
    try:
        import os
        os.environ["REPLICATE_API_TOKEN"] = token
        import replicate
    except ImportError:
        logger.warning("replicate package not installed: pip install replicate")
        return None

    try:
        output = replicate.run(
            "black-forest-labs/flux-schnell",
            input={"prompt": prompt, "aspect_ratio": aspect_ratio or "9:16"},
        )
        if not output:
            return None
        # Output: list of FileOutput/URLs, or single file-like
        item = output[0] if isinstance(output, (list, tuple)) else output
        stem = f"flux_{uuid.uuid4().hex[:12]}"
        dest = dest_dir / f"{stem}.png"
        if hasattr(item, "read"):
            dest.write_bytes(item.read())
        elif hasattr(item, "url"):
            import requests
            resp = requests.get(item.url(), timeout=60)
            resp.raise_for_status()
            dest.write_bytes(resp.content)
        else:
            url = str(item)
            if not url.startswith("http"):
                return None
            import requests
            resp = requests.get(url, timeout=60)
            resp.raise_for_status()
            dest.write_bytes(resp.content)
        logger.info("AI image generated: %s", dest.name)
        return dest
    except Exception as e:
        logger.warning("Flux image generation failed: %s", e)
        return None


# Register providers
AI_IMAGE_PROVIDERS["flux"] = _generate_flux


def generate_ai_image(
    prompt: str,
    aspect_ratio: str = "9:16",
    dest_dir: Path | None = None,
) -> Path | None:
    """Try configured AI image providers in order. Returns local path or None."""
    providers = cfg("sourcing.ai_image_providers") or []
    if not providers:
        return None
    dest_dir = dest_dir or Path(__file__).parent.parent / "assets" / "images" / "ai"
    dest_dir.mkdir(parents=True, exist_ok=True)

    for name in providers:
        fn = AI_IMAGE_PROVIDERS.get(name)
        if fn:
            path = fn(prompt, aspect_ratio, dest_dir)
            if path and path.exists():
                return path
    return None
