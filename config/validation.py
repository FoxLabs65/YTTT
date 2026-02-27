"""
Configuration validation for startup checks and category consistency.

Warns when categories in ideation.categories are missing from:
- Discovery (_CATEGORY_KEYWORDS) — trends classified as "other"
- Music (CATEGORY_MUSIC_MAP) — falls back to storytime
- Config music_moods — uses fallback
- Config voiceover_voices — uses default voice
- Optional: config/templates/scripts/{category}.txt — uses generic template
"""

import logging
import shutil
from pathlib import Path

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).parent.parent
TEMPLATE_DIR = PROJECT_ROOT / "config" / "templates" / "scripts"
TEMPLATE_DEFAULTS_DIR = PROJECT_ROOT / "config" / "templates" / "scripts_defaults"


def validate_category_config() -> list[str]:
    """Check that all ideation categories are fully configured across the pipeline.

    Returns a list of warning messages. Empty list means all categories are OK.
    """
    warnings: list[str] = []

    try:
        from models.config import load_config

        config = load_config()
    except FileNotFoundError:
        return warnings  # config not set up yet, skip

    ideation_cats = config.get("ideation", {}).get("categories") or []
    if not ideation_cats:
        return warnings

    # Get category lists from each component
    try:
        from agents.discovery import _CATEGORY_KEYWORDS

        discovery_cats = set(_CATEGORY_KEYWORDS.keys())
    except ImportError:
        discovery_cats = set()

    try:
        from agents.music_scraper import CATEGORY_MUSIC_MAP

        music_cats = set(CATEGORY_MUSIC_MAP.keys())
    except ImportError:
        music_cats = set()

    music_moods = config.get("sourcing", {}).get("music_moods") or {}
    voiceover_voices = config.get("sourcing", {}).get("voiceover_voices") or {}

    for cat in ideation_cats:
        if not cat or not isinstance(cat, str):
            continue

        missing: list[str] = []

        if cat not in discovery_cats:
            missing.append("discovery (trends will be classified as 'other')")

        if cat not in music_cats:
            missing.append("music (will fall back to storytime)")

        if cat not in music_moods:
            missing.append("sourcing.music_moods")

        if cat not in voiceover_voices:
            missing.append("sourcing.voiceover_voices")

        template_path = TEMPLATE_DIR / f"{cat}.txt"
        if not template_path.exists():
            missing.append("template (config/templates/scripts/{}.txt)".format(cat))

        if missing:
            warnings.append(
                "Category '{}' missing from: {}".format(cat, "; ".join(missing))
            )

    return warnings


def run_startup_check() -> list[str]:
    """Run validation and return any warnings. Safe to call at startup."""
    return validate_category_config()


def revert_template_to_default(category: str) -> bool:
    """Copy from scripts_defaults/ to scripts/. Returns True on success."""
    default_path = TEMPLATE_DEFAULTS_DIR / f"{category}.txt"
    target_path = TEMPLATE_DIR / f"{category}.txt"
    if default_path.exists():
        shutil.copy(default_path, target_path)
        return True
    return False
