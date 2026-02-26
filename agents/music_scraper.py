"""
Royalty-Free Music Scraper
Searches and downloads royalty-free music from multiple sources:
  - YouTube (no-copyright channels) via yt-dlp
  - Freesound API (Creative Commons Zero licensed tracks)
  - Local manual .mp3 files in assets/music/

The scraper builds a local music library in assets/music/ organized
into mood subdirectories. Tracks are randomly selected to ensure variety.
"""

import json
import logging
import random
import re
import subprocess
import time
import uuid
from pathlib import Path

import requests as http_requests

from models.config import get as cfg
from models.database import get_connection, insert_asset

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).parent.parent
MUSIC_DIR = PROJECT_ROOT / "assets" / "music"

CATEGORY_MUSIC_MAP = {
    "motivational": {
        "queries": [
            "no copyright inspirational background music",
            "royalty free motivational cinematic music",
            "epic uplifting music no copyright",
            "inspiring piano background no copyright",
            "uplifting corporate music royalty free",
            "positive acoustic guitar background free",
            "hopeful orchestral music no copyright",
            "ambient motivational music for videos",
        ],
        "freesound_tags": ["inspirational", "uplifting", "motivational", "cinematic", "epic", "hopeful"],
        "mood": "uplifting",
    },
    "funny": {
        "queries": [
            "no copyright funny background music",
            "royalty free comedy music",
            "quirky happy music no copyright",
            "playful ukulele background music free",
            "silly cartoon music no copyright",
            "upbeat fun music royalty free",
            "happy whistling background music free",
            "lighthearted jazz background no copyright",
        ],
        "freesound_tags": ["comedy", "funny", "happy", "playful", "ukulele", "quirky"],
        "mood": "funny",
    },
    "meme": {
        "queries": [
            "no copyright meme music",
            "royalty free quirky electronic beat",
            "trap beat no copyright short",
            "lo-fi beat background no copyright",
            "retro 8-bit chiptune music free",
            "vaporwave aesthetic music no copyright",
            "bass boosted meme music royalty free",
            "funny electronic music no copyright short",
        ],
        "freesound_tags": ["electronic", "retro", "chiptune", "lo-fi", "beat", "synth"],
        "mood": "quirky",
    },
    "news": {
        "queries": [
            "no copyright news background music",
            "royalty free corporate news music",
            "breaking news intro music no copyright",
            "serious documentary background music free",
            "neutral corporate background music royalty free",
            "modern news broadcast music free",
            "technology background music no copyright",
            "investigation documentary music royalty free",
        ],
        "freesound_tags": ["news", "corporate", "documentary", "technology", "broadcast", "neutral"],
        "mood": "dramatic",
    },
    "storytime": {
        "queries": [
            "no copyright chill lo-fi background",
            "royalty free ambient storytelling music",
            "soft acoustic background music no copyright",
            "gentle piano background music free",
            "warm cozy acoustic guitar no copyright",
            "calm atmospheric music royalty free",
            "indie folk background music no copyright",
            "dreamy ambient pad music free",
        ],
        "freesound_tags": ["ambient", "calm", "acoustic", "lo-fi", "gentle", "atmospheric"],
        "mood": "chill",
    },
}

MIN_DURATION = 20
MAX_DURATION = 180
_YT_DLP = "yt-dlp"


def _ensure_dirs():
    MUSIC_DIR.mkdir(parents=True, exist_ok=True)
    for mood in ("uplifting", "funny", "quirky", "dramatic", "chill", "general"):
        (MUSIC_DIR / mood).mkdir(exist_ok=True)


def _safe_filename(title: str) -> str:
    return re.sub(r"[^\w\s-]", "", title)[:50].strip().replace(" ", "_")


def _search_youtube_music(query: str, max_results: int = 5) -> list[dict]:
    """Search YouTube for no-copyright music tracks using yt-dlp."""
    search_url = f"ytsearch{max_results}:{query}"
    cmd = [
        _YT_DLP, "--no-download", "--dump-json", "--no-warnings", "--quiet",
        search_url,
    ]
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=120, encoding="utf-8",
        )
        if result.returncode != 0:
            logger.warning("yt-dlp music search failed: %s", result.stderr[:300])
            return []

        entries = []
        for line in result.stdout.strip().split("\n"):
            if not line.strip():
                continue
            try:
                entry = json.loads(line)
                entries.append(entry)
            except json.JSONDecodeError:
                continue
        return entries
    except subprocess.TimeoutExpired:
        logger.warning("yt-dlp music search timed out for: %s", query)
        return []
    except FileNotFoundError:
        logger.error("yt-dlp not found on PATH")
        return []


def _download_audio(video_url: str, dest_path: Path) -> bool:
    """Download audio-only as MP3 from a YouTube URL."""
    cmd = [
        _YT_DLP,
        "--extract-audio",
        "--audio-format", "mp3",
        "--audio-quality", "5",  # reasonable quality, smaller file
        "--output", str(dest_path.with_suffix(".%(ext)s")),
        "--no-warnings", "--quiet",
        "--no-playlist",
        video_url,
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120, encoding="utf-8")
        # yt-dlp may produce the file with .mp3 extension
        expected = dest_path.with_suffix(".mp3")
        if expected.exists() and expected.stat().st_size > 10000:
            return True
        # Check for other possible extensions
        for ext in (".mp3", ".m4a", ".opus", ".webm"):
            alt = dest_path.with_suffix(ext)
            if alt.exists() and alt.stat().st_size > 10000:
                if ext != ".mp3":
                    alt.rename(expected)
                return True
        logger.warning("Audio download produced no valid file for %s", video_url[:60])
        return False
    except subprocess.TimeoutExpired:
        logger.warning("Audio download timed out for %s", video_url[:60])
        return False
    except FileNotFoundError:
        logger.error("yt-dlp not found on PATH")
        return False


def _search_freesound(tags: list[str], max_results: int = 5) -> list[dict]:
    """Search Freesound.org for Creative Commons Zero licensed music.
    Uses Token authentication (simple API key) -- no OAuth2 or callback URL needed.
    Only downloads preview MP3s which are freely accessible with Token auth.
    """
    api_key = cfg("freesound_api_key")
    if not api_key or api_key.startswith("YOUR_"):
        return []

    query = " ".join(tags[:3])
    url = "https://freesound.org/apiv2/search/"
    params = {
        "query": query,
        "filter": 'duration:[20 TO 180] license:"Creative Commons 0"',
        "fields": "id,name,duration,previews,tags",
        "page_size": min(max_results, 150),
        "token": api_key,
    }

    try:
        resp = http_requests.get(url, params=params, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        return data.get("results", [])
    except http_requests.RequestException as e:
        logger.debug("Freesound search failed for '%s': %s", query, e)
        return []


def _download_freesound_preview(sound: dict, dest_dir: Path) -> Path | None:
    """Download the HQ OGG preview from Freesound and save as mp3."""
    previews = sound.get("previews", {})
    preview_url = previews.get("preview-hq-mp3") or previews.get("preview-lq-mp3")
    if not preview_url:
        return None

    sound_id = sound.get("id", uuid.uuid4().hex[:8])
    safe_name = _safe_filename(sound.get("name", "track"))
    dest = dest_dir / f"fs_{sound_id}_{safe_name}.mp3"
    if dest.exists():
        return dest

    try:
        resp = http_requests.get(preview_url, timeout=60)
        resp.raise_for_status()
        with open(dest, "wb") as f:
            f.write(resp.content)
        if dest.stat().st_size > 10000:
            logger.info("Downloaded Freesound track: %s (%.0f KB)", dest.name, dest.stat().st_size / 1024)
            return dest
        dest.unlink(missing_ok=True)
    except http_requests.RequestException as e:
        logger.debug("Freesound download failed for %s: %s", sound.get("name"), e)
    return None


def search_and_download_music(category: str, count: int = 3) -> list[Path]:
    """Search for royalty-free music matching a content category from multiple sources.
    Randomizes query order for variety across runs.
    """
    _ensure_dirs()
    music_config = CATEGORY_MUSIC_MAP.get(category, CATEGORY_MUSIC_MAP["storytime"])
    queries = list(music_config["queries"])
    freesound_tags = music_config.get("freesound_tags", [])
    mood = music_config["mood"]
    mood_dir = MUSIC_DIR / mood

    downloaded = []

    # Shuffle queries so each run picks different tracks
    random.shuffle(queries)

    # Source 1: YouTube no-copyright channels
    for query in queries:
        if len(downloaded) >= count:
            break

        logger.info("Searching YouTube for music: '%s'", query)
        entries = _search_youtube_music(query, max_results=5)
        # Shuffle results for variety
        random.shuffle(entries)

        for entry in entries:
            if len(downloaded) >= count:
                break

            duration = entry.get("duration") or 0
            if duration < MIN_DURATION or duration > MAX_DURATION:
                continue

            video_id = entry.get("id", "")
            title = entry.get("title", "track")
            safe_title = _safe_filename(title)
            dest_stem = mood_dir / f"yt_{video_id}_{safe_title}"
            final_path = dest_stem.with_suffix(".mp3")

            if final_path.exists():
                logger.debug("Track already cached: %s", final_path.name)
                downloaded.append(final_path)
                continue

            video_url = entry.get("webpage_url") or f"https://www.youtube.com/watch?v={video_id}"
            logger.info("Downloading music: %s (%ds)", title[:50], duration)

            if _download_audio(video_url, dest_stem):
                if final_path.exists():
                    logger.info("Saved: %s -> %s/ (%.0f KB)",
                                final_path.name, mood, final_path.stat().st_size / 1024)
                    downloaded.append(final_path)

        time.sleep(1)

    # Source 2: Freesound (CC0 licensed -- no attribution needed)
    if len(downloaded) < count and freesound_tags:
        random.shuffle(freesound_tags)
        fs_results = _search_freesound(freesound_tags[:3], max_results=8)
        random.shuffle(fs_results)

        for sound in fs_results:
            if len(downloaded) >= count:
                break
            path = _download_freesound_preview(sound, mood_dir)
            if path:
                downloaded.append(path)

    return downloaded


# --- Local Library Management ---

def scan_local_library() -> dict[str, list[Path]]:
    """Scan the local music library and return tracks organized by mood."""
    _ensure_dirs()
    library = {}

    # Scan mood subdirectories
    for mood_dir in MUSIC_DIR.iterdir():
        if mood_dir.is_dir() and mood_dir.name != "__pycache__":
            tracks = list(mood_dir.glob("*.mp3"))
            if tracks:
                library[mood_dir.name] = tracks

    # Scan root music dir for manually placed files
    root_tracks = list(MUSIC_DIR.glob("*.mp3"))
    if root_tracks:
        library.setdefault("general", []).extend(root_tracks)

    return library


def find_best_music(category: str, script_tags: list[str] | None = None) -> Path | None:
    """Find a matching music track for a script's category and tags.
    Randomizes selection within each priority tier to avoid reusing the same
    track across every video.

    Priority:
    1. Tag-matched tracks from the correct mood folder
    2. Random track from the correct mood folder
    3. Manually placed .mp3 files in the root music dir
    4. Any available track from another mood as fallback
    """
    music_config = CATEGORY_MUSIC_MAP.get(category, CATEGORY_MUSIC_MAP["storytime"])
    target_mood = music_config["mood"]

    library = scan_local_library()

    # Priority 1 & 2: mood-matched tracks
    mood_tracks = library.get(target_mood, [])
    if mood_tracks:
        if script_tags:
            tag_set = {t.lower() for t in script_tags}
            tag_matches = [t for t in mood_tracks if any(tag in t.stem.lower() for tag in tag_set)]
            if tag_matches:
                pick = random.choice(tag_matches)
                logger.info("Tag-matched music: %s (mood: %s)", pick.name, target_mood)
                return pick
        pick = random.choice(mood_tracks)
        logger.info("Mood-matched music: %s (mood: %s)", pick.name, target_mood)
        return pick

    # Priority 3: general/root directory tracks
    general_tracks = library.get("general", [])
    if general_tracks:
        pick = random.choice(general_tracks)
        logger.info("Using general music: %s", pick.name)
        return pick

    # Priority 4: any available mood
    all_tracks = [t for tracks in library.values() for t in tracks]
    if all_tracks:
        pick = random.choice(all_tracks)
        logger.info("Fallback music: %s", pick.name)
        return pick

    return None


def ensure_music_for_category(category: str, min_tracks: int = 2) -> list[Path]:
    """Ensure we have enough music tracks for a given category.
    Downloads from Pixabay Music if the local library is insufficient.
    Returns list of available tracks.
    """
    music_config = CATEGORY_MUSIC_MAP.get(category, CATEGORY_MUSIC_MAP["storytime"])
    target_mood = music_config["mood"]

    library = scan_local_library()
    existing = library.get(target_mood, []) + library.get("general", [])

    if len(existing) >= min_tracks:
        return existing

    needed = min_tracks - len(existing)
    logger.info("Need %d more '%s' tracks for category '%s', searching...", needed, target_mood, category)

    downloaded = search_and_download_music(category, count=needed)
    return existing + downloaded


# --- Integration with sourcing pipeline ---

def source_music_for_script(script_id: int, category: str, tags: list[str] | None = None) -> Path | None:
    """Full music sourcing pipeline for a script:
    1. Check local library for matching track
    2. If insufficient, download from Pixabay Music
    3. Insert asset record into database
    4. Return path to selected track
    """
    # Try local first
    track_path = find_best_music(category, tags)

    # Download if nothing available
    if not track_path:
        logger.info("No local music for category '%s', downloading...", category)
        downloaded = search_and_download_music(category, count=2)
        if downloaded:
            track_path = downloaded[0]

    if not track_path:
        logger.warning("Could not source music for script #%d (category: %s)", script_id, category)
        return None

    # Record in database
    music_config = CATEGORY_MUSIC_MAP.get(category, CATEGORY_MUSIC_MAP["storytime"])
    conn = get_connection()
    insert_asset(
        conn,
        script_id=script_id,
        asset_type="music",
        source="pixabay" if "pixabay" in track_path.name else "local",
        source_id=track_path.stem,
        local_path=str(track_path),
        mood=music_config["mood"],
    )
    conn.close()

    logger.info("Music sourced for script #%d: %s", script_id, track_path.name)
    return track_path


# --- CLI ---

def run_music_scraper(categories: list[str] | None = None, tracks_per_category: int = 3) -> dict:
    """Pre-populate the music library with tracks for all categories."""
    categories = categories or list(CATEGORY_MUSIC_MAP.keys())
    summary = {}

    for category in categories:
        tracks = ensure_music_for_category(category, min_tracks=tracks_per_category)
        summary[category] = len(tracks)
        logger.info("Category '%s': %d tracks available", category, len(tracks))

    logger.info("Music library summary: %s", summary)
    return summary


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        handlers=[
            logging.StreamHandler(),
            logging.FileHandler(PROJECT_ROOT / "logs" / "music_scraper.log"),
        ],
    )
    from models.database import init_db
    init_db()

    import sys
    categories = sys.argv[1:] if len(sys.argv) > 1 else None
    result = run_music_scraper(categories)
    print(json.dumps(result, indent=2))
