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
from models.database import get_connection, insert_asset, get_rejection_penalty, PENALTY_PER_REJECTION

# Topic modifiers for music search (when script inspired by gaming/roblox trends)
TOPIC_MUSIC_MODIFIERS = {
    "gaming": ["gaming", "game"],
    "roblox": ["playful", "roblox"],
}

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
    "howto": {
        "queries": [
            "no copyright educational background music",
            "royalty free tutorial music",
            "calm instructional music no copyright",
            "soft acoustic how-to music free",
            "gentle informative music royalty free",
            "clean minimalist background no copyright",
            "professional explainer music free",
        ],
        "freesound_tags": ["calm", "educational", "soft", "acoustic", "minimal", "clean"],
        "mood": "chill",
    },
    "pov": {
        "queries": [
            "no copyright pov style music",
            "royalty free relatable background music",
            "quirky relatable music no copyright",
            "lo-fi beat background no copyright",
            "viral style music royalty free",
            "trendy electronic music no copyright",
            "playful ukulele background free",
        ],
        "freesound_tags": ["quirky", "relatable", "lo-fi", "electronic", "playful", "trendy"],
        "mood": "quirky",
    },
    "reaction": {
        "queries": [
            "no copyright reaction commentary background",
            "royalty free discussion debate music",
            "neutral documentary background music no copyright",
            "thoughtful analysis music royalty free",
            "balanced commentary background no copyright",
            "news discussion music free",
        ],
        "freesound_tags": ["documentary", "neutral", "discussion", "corporate", "thoughtful", "ambient"],
        "mood": "dramatic",
    },
}

MIN_DURATION = 20
MAX_DURATION = 180
_YT_DLP = "yt-dlp"


def _build_music_queries(
    category: str,
    script_keywords: list[str] | None = None,
    trend_topic: str | None = None,
) -> tuple[list[str], list[str], str]:
    """Build search queries from category, script keywords, and trend topic.
    Returns (youtube/openverse_queries, freesound_tags, mood).
    """
    music_config = CATEGORY_MUSIC_MAP.get(category, CATEGORY_MUSIC_MAP["storytime"])
    base_queries = list(music_config["queries"])
    freesound_tags = list(music_config.get("freesound_tags", []))
    mood = music_config["mood"]

    # Add script keywords as granular search terms (like image/video search)
    if script_keywords:
        kw_queries = [f"{kw} {mood} music no copyright" for kw in script_keywords[:6]]
        base_queries = kw_queries + base_queries
        freesound_tags = list(script_keywords[:4]) + freesound_tags

    if trend_topic and trend_topic in TOPIC_MUSIC_MODIFIERS:
        modifiers = TOPIC_MUSIC_MODIFIERS[trend_topic]
        augmented = [f"{q} {mod}" for q in base_queries[:2] for mod in modifiers[:1]]
        base_queries = augmented + base_queries
        freesound_tags = modifiers + freesound_tags

    return base_queries, freesound_tags, mood


def _normalize_candidate(entry: dict, source: str) -> dict | None:
    """Convert raw API result to a unified candidate for scoring."""
    if source == "youtube":
        duration = entry.get("duration") or 0
        if duration < MIN_DURATION or duration > MAX_DURATION:
            return None
        title = entry.get("title", "")
        video_id = entry.get("id", "")
        url = entry.get("webpage_url") or f"https://www.youtube.com/watch?v={video_id}"
        return {
            "source": "youtube",
            "title": title,
            "searchable_text": title.lower(),
            "duration": duration,
            "raw": entry,
            "url_or_id": url,
        }
    if source == "freesound":
        tags = entry.get("tags", [])
        name = entry.get("name", "")
        searchable = f"{name} {' '.join(tags)}".lower()
        return {
            "source": "freesound",
            "title": name,
            "searchable_text": searchable,
            "duration": entry.get("duration", 0),
            "raw": entry,
            "url_or_id": str(entry.get("id", "")),
        }
    if source == "openverse":
        dur_raw = entry.get("duration") or 0
        duration = dur_raw / 1000 if dur_raw > 1000 else dur_raw
        if duration < MIN_DURATION or duration > MAX_DURATION:
            return None
        title = entry.get("title", "")
        genres = entry.get("genres", []) or []
        tags = entry.get("tags", []) or []
        tag_names = [t.get("name", t) if isinstance(t, dict) else t for t in tags]
        searchable = f"{title} {' '.join(str(g) for g in genres)} {' '.join(str(t) for t in tag_names)}".lower()
        return {
            "source": "openverse",
            "title": title,
            "searchable_text": searchable,
            "duration": duration,
            "raw": entry,
            "url_or_id": str(entry.get("id", "")),
        }
    return None


def _load_music_meta(path: Path) -> dict:
    """Load optional .meta.json alongside a music file. Returns {} on missing or error."""
    meta_path = path.with_name(path.stem + ".meta.json")
    if not meta_path.exists():
        return {}
    try:
        data = json.loads(meta_path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, OSError):
        return {}


def _local_path_to_candidate(path: Path) -> dict:
    """Convert a local file path to the same candidate format as online for unified scoring."""
    stem = path.stem.lower()
    searchable = stem
    meta = _load_music_meta(path)
    if meta:
        keywords = meta.get("keywords", [])
        mood = meta.get("mood", "")
        if keywords:
            searchable += " " + " ".join(str(k).lower() for k in keywords)
        if mood:
            searchable += " " + str(mood).lower()
    return {
        "source": "local",
        "title": path.name,
        "searchable_text": searchable.strip(),
        "path": path,
        "raw": path,
    }


def _score_music_candidate(
    candidate: dict,
    script_keywords: list[str],
    category_config: dict,
) -> float:
    """Score a candidate by keyword matches and mood relevance."""
    score = 0.0
    text = candidate.get("searchable_text", "")
    mood = category_config.get("mood", "").lower()

    # Base score for mood match in title/tags
    if mood and mood in text:
        score += 1.0

    # Script keyword matches (granular relevance)
    kw_set = {k.lower() for k in script_keywords}
    for kw in kw_set:
        if kw in text:
            score += 0.5

    # Category freesound tags match
    fs_tags = category_config.get("freesound_tags", [])
    for tag in fs_tags:
        if tag.lower() in text:
            score += 0.2

    return score


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


def _search_openverse_music(query: str, mood: str, max_results: int = 5) -> list[dict]:
    """Search Openverse for CC-licensed music. Categories: music. Length: medium (1-3 min)."""
    url = "https://api.openverse.org/v1/audio/"
    params = {
        "q": query,
        "categories": "music",
        "page_size": min(max_results, 20),
        "license_type": "commercial,modification",
    }

    try:
        resp = http_requests.get(url, params=params, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        results = data.get("results", [])
        out = []
        for r in results:
            dur_raw = r.get("duration") or 0
            dur = dur_raw / 1000 if dur_raw > 1000 else dur_raw  # Openverse uses ms
            if MIN_DURATION <= dur <= MAX_DURATION:
                out.append(r)
            if len(out) >= max_results:
                break
        return out
    except http_requests.RequestException as e:
        logger.debug("Openverse music search failed for '%s': %s", query, e)
        return []


def _download_openverse_audio(audio: dict, dest_dir: Path) -> Path | None:
    """Download Openverse audio. Fetches detail if url is empty."""
    url = audio.get("url")
    if not url:
        detail_url = f"https://api.openverse.org/v1/audio/{audio.get('id', '')}/"
        try:
            dr = http_requests.get(detail_url, timeout=15)
            dr.raise_for_status()
            url = dr.json().get("url")
        except (http_requests.RequestException, KeyError):
            return None
    if not url:
        return None

    audio_id = audio.get("id", "unknown")[:12]
    title = _safe_filename(audio.get("title", "track"))
    dest = dest_dir / f"ov_{audio_id}_{title}.mp3"
    if dest.exists():
        return dest

    try:
        resp = http_requests.get(url, timeout=90)
        resp.raise_for_status()
        with open(dest, "wb") as f:
            f.write(resp.content)
        if dest.stat().st_size > 10000:
            logger.info("Downloaded Openverse music: %s (%.0f KB)", dest.name, dest.stat().st_size / 1024)
            return dest
        dest.unlink(missing_ok=True)
    except http_requests.RequestException as e:
        logger.debug("Openverse download failed for %s: %s", audio.get("title"), e)
    return None


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


def search_and_download_music(
    category: str,
    count: int = 3,
    trend_topic: str | None = None,
    script_keywords: list[str] | None = None,
) -> list[Path]:
    """Search all sources for royalty-free music, score by relevance, and download top matches.
    Uses script keywords (like image/video search) for granular matching.
    """
    _ensure_dirs()
    music_config = CATEGORY_MUSIC_MAP.get(category, CATEGORY_MUSIC_MAP["storytime"])
    queries, freesound_tags, mood = _build_music_queries(category, script_keywords, trend_topic)
    mood_dir = MUSIC_DIR / mood

    # Collect candidates from all sources
    seen: set[tuple[str, str]] = set()
    candidates: list[dict] = []

    # YouTube
    for query in queries[:6]:
        logger.info("Searching YouTube for music: '%s'", query)
        for entry in _search_youtube_music(query, max_results=5):
            c = _normalize_candidate(entry, "youtube")
            if c and (c["source"], c["url_or_id"]) not in seen:
                seen.add((c["source"], c["url_or_id"]))
                candidates.append(c)
        time.sleep(1)

    # Freesound
    if freesound_tags:
        fs_results = _search_freesound(freesound_tags[:5], max_results=10)
        for entry in fs_results:
            c = _normalize_candidate(entry, "freesound")
            if c and (c["source"], c["url_or_id"]) not in seen:
                seen.add((c["source"], c["url_or_id"]))
                candidates.append(c)

    # Openverse
    ov_queries = [q.replace(" no copyright", "").replace(" royalty free", "") for q in queries[:4]]
    for query in ov_queries:
        ov_results = _search_openverse_music(query, mood, max_results=5)
        for entry in ov_results:
            c = _normalize_candidate(entry, "openverse")
            if c and (c["source"], c["url_or_id"]) not in seen:
                seen.add((c["source"], c["url_or_id"]))
                candidates.append(c)

    # Score and sort by relevance
    kw = script_keywords or []
    for c in candidates:
        c["_score"] = _score_music_candidate(c, kw, music_config)
    candidates.sort(key=lambda x: x["_score"], reverse=True)

    # Add small random factor so we don't always pick the same top track
    if candidates and candidates[0]["_score"] == candidates[-1]["_score"]:
        random.shuffle(candidates)
    else:
        # Shuffle within similar score bands
        top_scores = {c["_score"] for c in candidates[:count * 2]}
        top = [c for c in candidates if c["_score"] in top_scores]
        rest = [c for c in candidates if c["_score"] not in top_scores]
        random.shuffle(top)
        candidates = top + rest

    downloaded = []
    for c in candidates:
        if len(downloaded) >= count:
            break
        raw = c["raw"]
        source = c["source"]
        title = c["title"]

        if source == "youtube":
            video_id = raw.get("id", "")
            safe_title = _safe_filename(title)
            dest_stem = mood_dir / f"yt_{video_id}_{safe_title}"
            final_path = dest_stem.with_suffix(".mp3")
            if final_path.exists():
                downloaded.append(final_path)
                continue
            url = raw.get("webpage_url") or f"https://www.youtube.com/watch?v={video_id}"
            if _download_audio(url, dest_stem) and final_path.exists():
                downloaded.append(final_path)
        elif source == "freesound":
            path = _download_freesound_preview(raw, mood_dir)
            if path:
                downloaded.append(path)
        elif source == "openverse":
            path = _download_openverse_audio(raw, mood_dir)
            if path:
                downloaded.append(path)

    return downloaded


def _collect_all_music_candidates(
    category: str,
    script_keywords: list[str] | None = None,
    trend_topic: str | None = None,
) -> list[dict]:
    """Scrape local library AND online sources, score all candidates, return sorted by relevance.
    Does not download — returns candidate dicts; caller downloads if needed.
    """
    _ensure_dirs()
    music_config = CATEGORY_MUSIC_MAP.get(category, CATEGORY_MUSIC_MAP["storytime"])
    queries, freesound_tags, mood = _build_music_queries(category, script_keywords, trend_topic)
    mood_dir = MUSIC_DIR / mood
    kw = script_keywords or []

    candidates: list[dict] = []
    seen: set[tuple[str, str]] = set()

    # 1. Add all local tracks as candidates
    library = scan_local_library()
    for mood_name, paths in library.items():
        for p in paths:
            c = _local_path_to_candidate(p)
            key = ("local", str(p))
            if key not in seen:
                seen.add(key)
                candidates.append(c)

    # 2. Search YouTube
    for query in queries[:6]:
        logger.info("Searching YouTube for music: '%s'", query)
        for entry in _search_youtube_music(query, max_results=5):
            c = _normalize_candidate(entry, "youtube")
            if c and (c["source"], c["url_or_id"]) not in seen:
                seen.add((c["source"], c["url_or_id"]))
                candidates.append(c)
        time.sleep(1)

    # 3. Search Freesound
    if freesound_tags:
        fs_results = _search_freesound(freesound_tags[:5], max_results=10)
        for entry in fs_results:
            c = _normalize_candidate(entry, "freesound")
            if c and (c["source"], c["url_or_id"]) not in seen:
                seen.add((c["source"], c["url_or_id"]))
                candidates.append(c)

    # 4. Search Openverse
    ov_queries = [q.replace(" no copyright", "").replace(" royalty free", "") for q in queries[:4]]
    for query in ov_queries:
        ov_results = _search_openverse_music(query, mood, max_results=5)
        for entry in ov_results:
            c = _normalize_candidate(entry, "openverse")
            if c and (c["source"], c["url_or_id"]) not in seen:
                seen.add((c["source"], c["url_or_id"]))
                candidates.append(c)

    # 5. Score all candidates (local + online)
    for c in candidates:
        c["_score"] = _score_music_candidate(c, kw, music_config)
    # Apply rejection penalty
    conn = get_connection()
    for c in candidates:
        path = c.get("path")
        src = c.get("source", "")
        raw = c.get("raw", {})
        size_bytes, mtime_real = None, None
        fname = ""
        src_id = None
        if path:
            fname = Path(path).name
            if src in ("local", "user"):
                try:
                    st = Path(path).stat()
                    size_bytes, mtime_real = st.st_size, st.st_mtime
                except (OSError, TypeError):
                    pass
        else:
            raw_id = raw.get("id") if isinstance(raw, dict) else None
            if raw_id is not None:
                src_id = str(raw_id)
                fname = f"{src}_{src_id}.mp3"
        if fname or (src and src_id):
            penalty = get_rejection_penalty(
                conn, fname, "music", category, "Wrong background music",
                source=src if src and src != "local" else None,
                source_id=src_id,
                size_bytes=size_bytes, mtime_real=mtime_real,
            )
            c["_score"] -= penalty * PENALTY_PER_REJECTION
    conn.close()
    candidates.sort(key=lambda x: x["_score"], reverse=True)

    # Shuffle within similar score bands for variety
    if candidates and candidates[0]["_score"] == candidates[-1]["_score"]:
        random.shuffle(candidates)
    else:
        top_scores = {c["_score"] for c in candidates[:6]}
        top = [c for c in candidates if c["_score"] in top_scores]
        rest = [c for c in candidates if c["_score"] not in top_scores]
        random.shuffle(top)
        candidates = top + rest

    return candidates


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


def find_best_music(
    category: str,
    script_tags: list[str] | None = None,
    trend_topic: str | None = None,
    script_keywords: list[str] | None = None,
) -> Path | None:
    """Find a matching music track for a script's category, tags, and script keywords.
    Scores cached tracks by keyword match (like image/video selection) when script_keywords provided.

    Priority:
    1. Topic-matched tracks (when trend_topic set, e.g. gaming/roblox)
    2. Script-keyword-matched tracks (highest score from stem match)
    3. Tag-matched tracks from the correct mood folder
    4. Random track from the correct mood folder
    5. General/root directory tracks
    6. Any available track as fallback
    """
    music_config = CATEGORY_MUSIC_MAP.get(category, CATEGORY_MUSIC_MAP["storytime"])
    target_mood = music_config["mood"]
    library = scan_local_library()
    mood_tracks = library.get(target_mood, [])

    def _score_track(path: Path) -> float:
        stem = path.stem.lower()
        score = 0.0
        if trend_topic and trend_topic in TOPIC_MUSIC_MODIFIERS:
            for term in TOPIC_MUSIC_MODIFIERS[trend_topic]:
                if term in stem:
                    score += 2.0
        if script_keywords:
            for kw in script_keywords:
                if kw.lower() in stem:
                    score += 1.0
        if script_tags:
            for tag in script_tags:
                if tag.lower() in stem:
                    score += 0.5
        return score

    # Priority 1: topic-matched
    if mood_tracks and trend_topic and trend_topic in TOPIC_MUSIC_MODIFIERS:
        topic_terms = TOPIC_MUSIC_MODIFIERS[trend_topic]
        topic_matches = [t for t in mood_tracks if any(term in t.stem.lower() for term in topic_terms)]
        if topic_matches:
            pick = random.choice(topic_matches)
            logger.info("Topic-matched music: %s (topic: %s)", pick.name, trend_topic)
            return pick

    # Priority 2–4: score and pick best from mood folder
    if mood_tracks:
        scored = [(t, _score_track(t)) for t in mood_tracks]
        scored.sort(key=lambda x: x[1], reverse=True)
        best_score = scored[0][1] if scored else 0
        best_tracks = [t for t, s in scored if s == best_score and s > 0]
        if best_tracks:
            pick = random.choice(best_tracks)
            logger.info("Keyword-matched music: %s (mood: %s)", pick.name, target_mood)
            return pick
        pick = random.choice(mood_tracks)
        logger.info("Mood-matched music: %s (mood: %s)", pick.name, target_mood)
        return pick

    general_tracks = library.get("general", [])
    if general_tracks:
        pick = random.choice(general_tracks)
        logger.info("Using general music: %s", pick.name)
        return pick

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

def source_music_for_script(
    script_id: int,
    category: str,
    tags: list[str] | None = None,
    trend_topic: str | None = None,
    script_keywords: list[str] | None = None,
) -> Path | None:
    """Full music sourcing pipeline for a script:
    1. Scrape BOTH local library and online sources (YouTube, Freesound, Openverse)
    2. Score all candidates together by relevance (mood, script keywords, category)
    3. Select the best-scoring track
    4. If best is online, download it; if local, use directly
    5. Insert asset record into database
    """
    candidates = _collect_all_music_candidates(
        category, script_keywords=script_keywords, trend_topic=trend_topic
    )
    if not candidates:
        logger.warning("No music candidates found for script #%d (category: %s)", script_id, category)
        return None

    music_config = CATEGORY_MUSIC_MAP.get(category, CATEGORY_MUSIC_MAP["storytime"])
    mood_dir = MUSIC_DIR / music_config["mood"]
    track_path = None

    for best in candidates:
        if best["source"] == "local":
            track_path = best["path"]
            logger.info("Music: selected local (score %.1f) — %s", best["_score"], track_path.name)
            break
        raw = best["raw"]
        if best["source"] == "youtube":
            video_id = raw.get("id", "")
            title = best["title"]
            dest_stem = mood_dir / f"yt_{video_id}_{_safe_filename(title)}"
            final_path = dest_stem.with_suffix(".mp3")
            if final_path.exists():
                track_path = final_path
            else:
                url = raw.get("webpage_url") or f"https://www.youtube.com/watch?v={video_id}"
                if _download_audio(url, dest_stem):
                    track_path = final_path
        elif best["source"] == "freesound":
            track_path = _download_freesound_preview(raw, mood_dir)
        elif best["source"] == "openverse":
            track_path = _download_openverse_audio(raw, mood_dir)
        if track_path:
            logger.info("Music: selected online %s (score %.1f) — %s", best["source"], best["_score"], track_path.name)
            break

    if not track_path:
        logger.warning("Could not source music for script #%d (category: %s)", script_id, category)
        return None

    # Record in database
    music_config = CATEGORY_MUSIC_MAP.get(category, CATEGORY_MUSIC_MAP["storytime"])
    conn = get_connection()
    src = "local"
    if "yt_" in track_path.name:
        src = "youtube"
    elif "fs_" in track_path.name:
        src = "freesound"
    elif "ov_" in track_path.name:
        src = "openverse"
    elif "pixabay" in track_path.name:
        src = "pixabay"
    insert_asset(
        conn,
        script_id=script_id,
        asset_type="music",
        source=src,
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
