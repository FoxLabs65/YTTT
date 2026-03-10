"""
Asset Sourcing Agent
Downloads B-roll footage, background images, music, and generates
voiceovers for pending scripts.
"""

import asyncio
import json
import logging
import random
import re
import subprocess
import uuid
from pathlib import Path

import requests

from models.config import get as cfg
from models.database import (
    get_connection,
    get_scripts_by_status,
    get_assets_for_script,
    get_trends_by_ids,
    insert_asset,
    update_script_status,
    reset_stuck_scripts,
    get_rejection_penalty,
    PENALTY_PER_REJECTION,
)

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).parent.parent
ASSETS_DIR = PROJECT_ROOT / "assets"
STOCK_DIR = ASSETS_DIR / "stock_footage"
IMAGES_DIR = ASSETS_DIR / "images"
MUSIC_DIR = ASSETS_DIR / "music"
VOICEOVER_DIR = ASSETS_DIR / "voiceovers"
USER_VIDEO_DIR = ASSETS_DIR / "stock_footage" / "user"
USER_IMAGE_DIR = ASSETS_DIR / "images" / "user"

VIDEO_EXTENSIONS = (".mp4", ".mov", ".webm")
IMAGE_EXTENSIONS = (".jpg", ".jpeg", ".png", ".webp")


def _ensure_dirs():
    for d in (STOCK_DIR, IMAGES_DIR, MUSIC_DIR, VOICEOVER_DIR, USER_VIDEO_DIR, USER_IMAGE_DIR):
        d.mkdir(parents=True, exist_ok=True)


def _download_trend_video(video_id: str, trend_id: int, platform: str, dest_dir: Path) -> Path | None:
    """Download a trend video (YouTube/TikTok short) with yt-dlp. Returns path on success, None on failure."""
    if not video_id:
        return None
    dest_dir.mkdir(parents=True, exist_ok=True)
    stem = f"trend_{trend_id}_{video_id}"
    for ext in (".mp4", ".webm", ".mkv"):
        existing = dest_dir / f"{stem}{ext}"
        if existing.exists():
            return existing

    if platform == "youtube":
        url = f"https://www.youtube.com/watch?v={video_id}"
    elif platform == "tiktok":
        url = f"https://www.tiktok.com/@placeholder/video/{video_id}"  # yt-dlp can resolve
    else:
        url = f"https://www.youtube.com/watch?v={video_id}"

    out_template = str(dest_dir / f"trend_{trend_id}_{video_id}.%(ext)s")
    cmd = [
        "yt-dlp",
        "-f", "best[height<=1080]/best",
        "--no-playlist",
        "--no-warnings",
        "--quiet",
        "-o", out_template,
        url,
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=180, encoding="utf-8")
        if result.returncode != 0:
            logger.warning("yt-dlp trend download failed for %s: %s", url[:50], result.stderr[:200] if result.stderr else "unknown")
            return None
        stem = f"trend_{trend_id}_{video_id}"
        for ext in (".mp4", ".webm", ".mkv"):
            candidate = dest_dir / f"{stem}{ext}"
            if candidate.exists() and candidate.stat().st_size > 10000:
                logger.info("Downloaded trend video: %s", candidate.name)
                return candidate
        logger.warning("yt-dlp produced no valid file for %s", url[:50])
        return None
    except subprocess.TimeoutExpired:
        logger.warning("yt-dlp timed out for trend %s", video_id)
        return None
    except FileNotFoundError:
        logger.warning("yt-dlp not found on PATH")
        return None


# --- Pexels API ---

def search_pexels_videos(query: str, count: int = 5) -> list[dict]:
    api_key = cfg("pexels_api_key")
    if not api_key or api_key.startswith("YOUR_"):
        logger.warning("Pexels API key not configured")
        return []

    url = "https://api.pexels.com/videos/search"
    headers = {"Authorization": api_key}
    params = {"query": query, "per_page": min(count, 15), "orientation": "portrait", "size": "medium"}

    try:
        resp = requests.get(url, headers=headers, params=params, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        return data.get("videos", [])
    except requests.RequestException as e:
        logger.warning("Pexels video search failed for '%s': %s", query, e)
        return []


def search_pexels_images(query: str, count: int = 5) -> list[dict]:
    api_key = cfg("pexels_api_key")
    if not api_key or api_key.startswith("YOUR_"):
        return []

    url = "https://api.pexels.com/v1/search"
    headers = {"Authorization": api_key}
    params = {"query": query, "per_page": min(count, 15), "orientation": "portrait"}

    try:
        resp = requests.get(url, headers=headers, params=params, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        return data.get("photos", [])
    except requests.RequestException as e:
        logger.warning("Pexels image search failed for '%s': %s", query, e)
        return []


def download_pexels_video(video: dict, dest_dir: Path) -> Path | None:
    """Download the best vertical video file from a Pexels video entry."""
    video_files = video.get("video_files", [])
    # Prefer HD portrait
    best = None
    for vf in video_files:
        w = vf.get("width", 0)
        h = vf.get("height", 0)
        if h > w and (best is None or h > best.get("height", 0)):
            best = vf
    if not best:
        best = video_files[0] if video_files else None
    if not best:
        return None

    url = best.get("link")
    if not url:
        return None

    filename = f"pexels_{video.get('id', uuid.uuid4().hex)}.mp4"
    dest = dest_dir / filename
    if dest.exists():
        return dest

    try:
        resp = requests.get(url, timeout=120, stream=True)
        resp.raise_for_status()
        with open(dest, "wb") as f:
            for chunk in resp.iter_content(chunk_size=1024 * 64):
                f.write(chunk)
        logger.info("Downloaded Pexels video: %s", filename)
        return dest
    except requests.RequestException as e:
        logger.warning("Failed to download Pexels video %s: %s", url[:80], e)
        return None


def download_pexels_image(photo: dict, dest_dir: Path) -> Path | None:
    url = photo.get("src", {}).get("large2x") or photo.get("src", {}).get("original")
    if not url:
        return None

    filename = f"pexels_{photo.get('id', uuid.uuid4().hex)}.jpg"
    dest = dest_dir / filename
    if dest.exists():
        return dest

    try:
        resp = requests.get(url, timeout=60)
        resp.raise_for_status()
        with open(dest, "wb") as f:
            f.write(resp.content)
        logger.info("Downloaded Pexels image: %s", filename)
        return dest
    except requests.RequestException as e:
        logger.warning("Failed to download Pexels image: %s", e)
        return None


# --- Coverr API (free stock videos, non-commercial; attribution required) ---

def search_coverr_videos(query: str, count: int = 5) -> list[dict]:
    api_key = cfg("coverr_api_key")
    if not api_key or api_key.startswith("YOUR_"):
        return []

    url = "https://api.coverr.co/videos"
    params = {
        "query": query,
        "page_size": min(count, 20),
        "urls": "true",
        "api_key": api_key,
    }

    try:
        resp = requests.get(url, params=params, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        return data.get("hits", [])
    except requests.RequestException as e:
        logger.warning("Coverr video search failed for '%s': %s", query, e)
        return []


def download_coverr_video(video: dict, dest_dir: Path) -> Path | None:
    urls = video.get("urls", {})
    download_url = urls.get("mp4_download") or urls.get("mp4")
    if not download_url:
        return None

    vid_id = video.get("id", uuid.uuid4().hex[:12])
    filename = f"coverr_{vid_id}.mp4"
    dest = dest_dir / filename
    if dest.exists():
        return dest

    try:
        resp = requests.get(download_url, timeout=120, stream=True)
        resp.raise_for_status()
        with open(dest, "wb") as f:
            for chunk in resp.iter_content(chunk_size=1024 * 64):
                f.write(chunk)
        logger.info("Downloaded Coverr video: %s", filename)
        return dest
    except requests.RequestException as e:
        logger.warning("Failed to download Coverr video %s: %s", vid_id, e)
        return None


# --- Unsplash API (photos only; attribution required) ---

def search_unsplash_images(query: str, count: int = 5) -> list[dict]:
    api_key = cfg("unsplash_access_key")
    if not api_key or api_key.startswith("YOUR_"):
        return []

    url = "https://api.unsplash.com/search/photos"
    headers = {"Authorization": f"Client-ID {api_key}"}
    params = {"query": query, "per_page": min(count, 15), "orientation": "portrait"}

    try:
        resp = requests.get(url, headers=headers, params=params, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        return data.get("results", [])
    except requests.RequestException as e:
        logger.warning("Unsplash image search failed for '%s': %s", query, e)
        return []


def download_unsplash_image(photo: dict, dest_dir: Path) -> Path | None:
    urls = photo.get("urls", {})
    url = urls.get("regular") or urls.get("full") or urls.get("small")
    if not url:
        return None

    photo_id = photo.get("id", uuid.uuid4().hex)
    filename = f"unsplash_{photo_id}.jpg"
    dest = dest_dir / filename
    if dest.exists():
        return dest

    try:
        resp = requests.get(url, timeout=60)
        resp.raise_for_status()
        with open(dest, "wb") as f:
            f.write(resp.content)
        logger.info("Downloaded Unsplash image: %s", filename)
        return dest
    except requests.RequestException as e:
        logger.warning("Failed to download Unsplash image: %s", e)
        return None


# --- Openverse API (images + audio; CC-licensed; attribution required) ---

OPENVERSE_BASE = "https://api.openverse.org/v1"

def search_openverse_images(query: str, count: int = 5) -> list[dict]:
    url = f"{OPENVERSE_BASE}/images/"
    params = {"q": query, "page_size": min(count, 20), "license_type": "commercial,modification"}

    try:
        resp = requests.get(url, params=params, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        return data.get("results", [])
    except requests.RequestException as e:
        logger.warning("Openverse image search failed for '%s': %s", query, e)
        return []


def download_openverse_image(result: dict, dest_dir: Path) -> Path | None:
    url = result.get("url")
    if not url:
        detail_url = f"{OPENVERSE_BASE}/images/{result.get('id', '')}/"
        try:
            dr = requests.get(detail_url, timeout=15)
            dr.raise_for_status()
            detail = dr.json()
            url = detail.get("url")
        except requests.RequestException:
            pass
    if not url:
        return None

    img_id = result.get("id", uuid.uuid4().hex)[:12]
    ext = "jpg" if "jpg" in (result.get("filetype") or "").lower() else "png"
    filename = f"openverse_{img_id}.{ext}"
    dest = dest_dir / filename
    if dest.exists():
        return dest

    try:
        resp = requests.get(url, timeout=60)
        resp.raise_for_status()
        with open(dest, "wb") as f:
            f.write(resp.content)
        logger.info("Downloaded Openverse image: %s", filename)
        return dest
    except requests.RequestException as e:
        logger.warning("Failed to download Openverse image: %s", e)
        return None


# --- Pixabay API ---

def search_pixabay_images(query: str, count: int = 5) -> list[dict]:
    api_key = cfg("pixabay_api_key")
    if not api_key or api_key.startswith("YOUR_"):
        return []

    url = "https://pixabay.com/api/"
    params = {"key": api_key, "q": query, "per_page": min(count, 10), "safesearch": "true", "image_type": "photo"}

    try:
        resp = requests.get(url, params=params, timeout=30)
        resp.raise_for_status()
        return resp.json().get("hits", [])
    except requests.RequestException as e:
        logger.warning("Pixabay image search failed for '%s': %s", query, e)
        return []


def download_pixabay_image(hit: dict, dest_dir: Path) -> Path | None:
    url = hit.get("largeImageURL") or hit.get("webformatURL") or hit.get("previewURL")
    if not url:
        return None

    filename = f"pixabay_{hit.get('id', uuid.uuid4().hex)}.jpg"
    dest = dest_dir / filename
    if dest.exists():
        return dest

    try:
        resp = requests.get(url, timeout=60)
        resp.raise_for_status()
        with open(dest, "wb") as f:
            f.write(resp.content)
        logger.info("Downloaded Pixabay image: %s", filename)
        return dest
    except requests.RequestException as e:
        logger.warning("Failed to download Pixabay image: %s", e)
        return None


def search_pixabay_videos(query: str, count: int = 3) -> list[dict]:
    api_key = cfg("pixabay_api_key")
    if not api_key or api_key.startswith("YOUR_"):
        return []

    url = "https://pixabay.com/api/videos/"
    params = {"key": api_key, "q": query, "per_page": min(count, 10), "safesearch": "true", "video_type": "film"}

    try:
        resp = requests.get(url, params=params, timeout=30)
        resp.raise_for_status()
        return resp.json().get("hits", [])
    except requests.RequestException as e:
        logger.warning("Pixabay video search failed for '%s': %s", query, e)
        return []


def download_pixabay_video(hit: dict, dest_dir: Path) -> Path | None:
    videos = hit.get("videos", {})
    # Prefer medium quality
    video_url = None
    for quality in ("medium", "small", "large", "tiny"):
        entry = videos.get(quality, {})
        if entry.get("url"):
            video_url = entry["url"]
            break
    if not video_url:
        return None

    filename = f"pixabay_{hit.get('id', uuid.uuid4().hex)}.mp4"
    dest = dest_dir / filename
    if dest.exists():
        return dest

    try:
        resp = requests.get(video_url, timeout=120, stream=True)
        resp.raise_for_status()
        with open(dest, "wb") as f:
            for chunk in resp.iter_content(chunk_size=1024 * 64):
                f.write(chunk)
        logger.info("Downloaded Pixabay video: %s", filename)
        return dest
    except requests.RequestException as e:
        logger.warning("Failed to download Pixabay video: %s", e)
        return None


def search_pixabay_music(query: str, count: int = 3) -> list[dict]:
    """Search Pixabay for royalty-free music. Uses the Pixabay music endpoint (undocumented but functional)."""
    api_key = cfg("pixabay_api_key")
    if not api_key or api_key.startswith("YOUR_"):
        return []

    url = "https://pixabay.com/api/"
    params = {"key": api_key, "q": query, "per_page": min(count, 10), "safesearch": "true", "category": "music"}

    try:
        resp = requests.get(url, params=params, timeout=30)
        resp.raise_for_status()
        return resp.json().get("hits", [])
    except requests.RequestException:
        return []


# --- Voiceover (edge-tts) ---

async def _generate_voiceover_async(
    text: str,
    voice: str,
    output_path: Path,
    rate: str = "+0%",
    pitch: str = "+0Hz",
    volume: str = "+0%",
    meta_path: Path | None = None,
):
    import edge_tts
    communicate = edge_tts.Communicate(
        text,
        voice,
        rate=rate,
        pitch=pitch or "+0Hz",
        volume=volume or "+0%",
        boundary="WordBoundary",
    )
    await communicate.save(str(output_path), metadata_fname=str(meta_path) if meta_path else None)


def _expand_pause_markup(text: str) -> str:
    """Convert script markup to TTS pause cues. Markup is stripped for display elsewhere.
    [[pause]] -> ... (short pause)
    [[long pause]] -> .... (longer dramatic beat)
    """
    text = re.sub(r"\[\[long\s*pause\]\]", ".... ", text, flags=re.IGNORECASE)
    text = re.sub(r"\[\[pause\]\]", "... ", text, flags=re.IGNORECASE)
    return text


def _humanise_text(text: str) -> str:
    """Insert natural pauses and breathing breaks so TTS sounds less robotic.

    edge-tts respects commas, periods, and ellipses as pause cues.
    This function:
      - Inserts medium pauses between sentences by adding an ellipsis after periods
      - Adds a longer pause before dramatic sentences starting with "But", "So", "And"
      - Converts "..." in the script to a real hesitation pause
    ([[pause]] markup is expanded by caller before this.)
    """
    # Normalise whitespace
    text = re.sub(r"\s+", " ", text).strip()

    # Add a beat between sentences: ". X" -> "... X" (edge-tts pauses on ellipsis)
    text = re.sub(r"\.(\s+)([A-Z])", r".\1... \2", text)

    # Dramatic connectors get an extra pause: "... But" etc.
    for word in ("But", "So", "And", "Now", "Then", "However", "Yet", "Still", "Look"):
        text = text.replace(f"... {word} ", f"... ... {word}, ")

    # Existing ellipses in the original script should be a real pause
    text = re.sub(r"\.{4,}", "...", text)

    # Add a tiny breath after question marks
    text = re.sub(r"\?(\s+)", r"? ... \1", text)

    return text


def generate_voiceover(
    text: str, category: str, script_id: int, voice_override: str | None = None
) -> tuple[Path | None, Path | None]:
    """Generate TTS voiceover and word-boundary metadata for a script.
    Returns (audio_path, metadata_path) or (None, None) on failure.
    voice_override: if set, use this voice instead of category default.
    Voice selection: voice_override > voiceover_voice_pool (random) > voiceover_voices[category].
    When voiceover_voices[category] is a list, picks randomly for variety.
    """
    voice = voice_override
    if not voice:
        pool = cfg("sourcing.voiceover_voice_pool")
        if pool and isinstance(pool, list) and pool:
            voice = random.choice(pool)
        else:
            voices = cfg("sourcing.voiceover_voices") or {}
            cat_voice = voices.get(category, "en-US-AndrewMultilingualNeural")
            if isinstance(cat_voice, list) and cat_voice:
                voice = random.choice(cat_voice)
            else:
                voice = cat_voice if isinstance(cat_voice, str) else "en-US-AndrewMultilingualNeural"
    # Prosody: category preset overrides global defaults
    prosody = cfg("sourcing.voiceover_prosody") or {}
    cat_prosody = prosody.get(category, {}) if isinstance(prosody, dict) else {}
    rate = cat_prosody.get("rate") or cfg("sourcing.voiceover_rate") or "-5%"
    pitch = cat_prosody.get("pitch") or cfg("sourcing.voiceover_pitch") or "+0Hz"
    volume = cat_prosody.get("volume") or cfg("sourcing.voiceover_volume") or "+0%"

    stem = f"vo_{script_id}_{uuid.uuid4().hex[:8]}"
    output_path = VOICEOVER_DIR / f"{stem}.mp3"
    meta_path = VOICEOVER_DIR / f"{stem}.json"

    # Expand pause markup first (before stripping, since [.*?] would match inside [[pause]])
    clean_text = _expand_pause_markup(text)

    # Strip visual cues from script for voiceover
    clean_text = re.sub(r"\[.*?\]", "", clean_text).strip()
    clean_text = re.sub(r"\s+", " ", clean_text)

    # Remove trailing period from Mr/Mrs/Ms/Dr etc. — TTS treats "." as sentence end and adds long pause
    clean_text = re.sub(r"\b(Mr|Mrs|Ms|Dr|Drs|Prof|Sr|Jr)\.", r"\1", clean_text)

    if not clean_text:
        logger.warning("Empty text after cleaning for script %d", script_id)
        return None, None

    # Humanise text with natural pauses (skip _expand_pause_markup - already done above)
    clean_text = _humanise_text(clean_text)

    try:
        asyncio.run(
            _generate_voiceover_async(
                clean_text, voice, output_path, rate, pitch, volume, meta_path
            )
        )
        logger.info(
            "Generated voiceover: %s (%s, rate=%s, pitch=%s)",
            output_path.name, voice, rate, pitch,
        )
        return output_path, meta_path
    except Exception as e:
        logger.error("Voiceover generation failed for script %d: %s", script_id, e)
        return None, None


# --- Main sourcing pipeline ---

STOP_WORDS = frozenset({
    "a", "an", "the", "is", "are", "was", "were", "be", "been", "being",
    "have", "has", "had", "do", "does", "did", "will", "would", "could",
    "should", "shall", "may", "might", "must", "can", "need",
    "i", "me", "my", "we", "our", "you", "your", "he", "him", "his",
    "she", "her", "it", "its", "they", "them", "their", "this", "that",
    "what", "which", "who", "whom", "how", "when", "where", "why",
    "not", "no", "nor", "but", "and", "or", "so", "if", "then", "than",
    "too", "very", "just", "about", "above", "after", "again", "all",
    "also", "any", "as", "at", "back", "because", "before", "between",
    "both", "by", "come", "each", "even", "every", "for", "from",
    "get", "give", "go", "going", "here", "in", "into", "like", "look",
    "make", "more", "most", "much", "of", "off", "on", "one", "only",
    "other", "out", "over", "own", "really", "right", "same", "say",
    "see", "some", "still", "such", "take", "tell", "thing", "think",
    "through", "to", "up", "us", "use", "want", "way", "well", "with",
    "know", "don", "doesn", "didn", "won", "wouldn", "couldn", "shouldn",
    "show", "ever", "never", "literally", "actually", "basically",
    # Contractions (apostrophe-normalized): you're->youre, it's->its, etc.
    "youre", "youve", "theyre", "were", "hes", "shes", "theres", "thats",
    "whats", "whos", "ive", "weve", "theyve", "wed", "youd", "theyd",
    "isnt", "arent", "wasnt", "werent", "hasnt", "hadnt", "doesnt", "didnt",
    "wont", "cant", "couldnt", "shouldnt", "wouldnt",
})

# Words too vague for stock search (includes STOP_WORDS + contractions)
VAGUE_SEARCH_TERMS = STOP_WORDS


def _normalize_for_stop_check(text: str) -> str:
    """Lowercase and collapse apostrophes for stop-word lookup (e.g. you're -> youre)."""
    return re.sub(r"'", "", text.lower())


def _extract_script_keywords(script_body: str, title: str, tags: list[str]) -> list[str]:
    """Extract specific, searchable visual keywords from the script text.
    Focuses on [show X] cues, concrete nouns, named entities, and visual objects
    that stock libraries can match.
    """
    keywords = []

    # Extract [show X] cues FIRST (highest priority - explicit LLM visual directions)
    bracket_cues = re.findall(r"\[(.*?)\]", script_body)
    for cue in bracket_cues:
        clean = re.sub(
            r"\b(show|footage|clip|stock|effect|animation|visual|graphic)\b",
            "",
            cue,
            flags=re.IGNORECASE,
        )
        clean = re.sub(r"\s+", " ", clean).strip()
        if len(clean) > 3:
            keywords.append(clean)

    # Extract noun phrases from title
    title_clean = re.sub(r"[^\w\s]", "", title)
    title_words = [w for w in title_clean.split() if w.lower() not in STOP_WORDS and len(w) > 2]
    if title_words:
        keywords.append(" ".join(title_words[:4]))

    # Extract concrete visual terms from script body (strip [visual cues] for word analysis)
    body = re.sub(r"\[.*?\]", " ", script_body)
    body = re.sub(r"[^\w\s'-]", " ", body)
    words = body.split()

    # Find capitalized proper nouns / names (e.g. "Prince", "Mrs Henderson")
    # Skip contractions (You're, It's, There's) via normalized stop check
    for i, w in enumerate(words):
        w_norm = _normalize_for_stop_check(w) if w else ""
        if w and w[0].isupper() and w_norm not in STOP_WORDS and len(w) > 2:
            if i > 0 and words[i - 1] and words[i - 1][0].isupper():
                keywords.append(f"{words[i-1]} {w}")
            else:
                keywords.append(w)

    # Extract 2-word concrete phrases (adjective+noun patterns)
    concrete_indicators = {
        "cold", "dark", "bright", "loud", "quiet", "fast", "slow", "big",
        "empty", "full", "broken", "calm", "dramatic", "live", "national",
        "surprised", "confused", "happy", "sad", "angry", "tired", "excited",
        "scared", "nervous", "relaxed", "busy", "modern", "old", "young",
    }
    for i in range(len(words) - 1):
        w1, w2 = words[i].lower(), words[i + 1].lower()
        if w1 in concrete_indicators and w2 not in STOP_WORDS and len(w2) > 2:
            keywords.append(f"{words[i]} {words[i+1]}")

    # Extract standalone concrete nouns (objects you can film/photograph)
    visual_nouns = {
        "room", "desk", "building", "house", "school", "classroom", "stage",
        "phone", "camera", "screen", "mirror", "window", "door", "car",
        "ocean", "mountain", "forest", "city", "street", "sunset", "sunrise",
        "rain", "fire", "water", "sky", "crowd", "person", "people", "face",
        "hand", "hands", "eyes", "brain", "heart", "moth", "earthquake",
        "television", "microphone", "guitar", "piano", "playlist", "flute",
        "sketch", "newspaper", "anchor", "teacher", "student", "police",
        "dog", "cat", "pet", "game", "controller", "computer", "laptop",
        "office", "kitchen", "food", "coffee", "book", "baby", "child",
        "doctor", "hospital", "garden", "park", "beach", "snow", "cloud",
        "letter", "box", "uniform", "map", "attic", "submarine", "barracks",
    }
    for w in words:
        if w and w.lower() in visual_nouns:
            keywords.append(w.lower())

    # Extract action verbs (visually filmable actions for stock footage)
    action_verbs = {
        "run", "running", "walk", "walking", "jump", "jumping", "sit", "sitting",
        "stand", "standing", "cry", "crying", "laugh", "laughing", "smile", "smiling",
        "type", "typing", "write", "writing", "read", "reading", "cook", "cooking",
        "drive", "driving", "point", "pointing", "wave", "waving", "hold", "holding",
        "open", "opening", "close", "closing", "look", "looking", "watch", "watching",
        "listen", "listening", "talk", "talking", "yell", "yelling", "salute", "saluting",
        "fold", "folding", "push", "pushing", "pull", "pulling", "climb", "climbing",
        "fall", "falling", "dance", "dancing",
    }
    for w in words:
        wl = w.lower() if w else ""
        if wl in action_verbs:
            keywords.append(wl)
    for i in range(len(words) - 1):
        w1, w2 = words[i].lower(), words[i + 1].lower()
        if w1 in action_verbs and w2 not in STOP_WORDS and len(w2) > 2:
            keywords.append(f"{words[i]} {words[i+1]}")

    # Extract objects from "a/the X" and "verb + object" patterns
    for i in range(len(words) - 1):
        w1, w2 = words[i].lower(), words[i + 1].lower()
        if w1 in ("a", "an", "the", "my", "his", "her") and len(w2) > 2 and w2 not in STOP_WORDS:
            keywords.append(words[i + 1].lower())
        if w1 in action_verbs and len(w2) > 2 and w2 not in STOP_WORDS:
            keywords.append(words[i + 1].lower())

    # Add tag-derived terms (cleaned hashtags)
    for tag in tags[:5]:
        clean = tag.lstrip("#").replace("_", " ")
        if len(clean) > 2 and clean.lower() not in STOP_WORDS:
            keywords.append(clean)

    # Deduplicate while preserving order; reject vague/non-visual keywords
    seen = set()
    unique = []
    for k in keywords:
        k_lower = k.lower().strip()
        k_norm = _normalize_for_stop_check(k)
        if not k_lower or k_lower in seen or len(k_lower) <= 2:
            continue
        if k_norm in VAGUE_SEARCH_TERMS:
            continue
        seen.add(k_lower)
        unique.append(k)

    return unique[:25]


# Topic modifiers for video/image search. Empty — no static topics; user discovery queries drive trends.
TOPIC_VISUAL_MODIFIERS: dict[str, list[str]] = {}

# Words to drop when shortening search queries for stock APIs (short phrases match better)
SEARCH_FILLER_WORDS = {
    "the", "a", "an", "of", "for", "in", "on", "at", "to", "and", "or",
    "technical", "close-up", "close", "analysis", "highlight", "footage",
    "shot", "view", "scene", "showing", "shows", "show", "effect", "visual",
}

# Max words per search query (stock APIs like Pexels/Pixabay match shorter phrases better)
MAX_SEARCH_QUERY_WORDS = 4


def _shorten_keywords_for_search(keywords: list[str], max_words: int = MAX_SEARCH_QUERY_WORDS) -> list[str]:
    """Shorten keyword phrases for stock API search. Prefer 2-4 word core phrases.
    Drops vague terms (contractions, pronouns) that produce irrelevant results."""
    result = []
    seen = set()
    for kw in keywords:
        if not kw or len(kw) < 3:
            continue
        words = kw.lower().split()
        # Drop filler and vague words
        kept = [
            w for w in words
            if w not in SEARCH_FILLER_WORDS
            and _normalize_for_stop_check(w) not in VAGUE_SEARCH_TERMS
            and len(w) > 1
        ]
        if not kept:
            kept = [w for w in words if _normalize_for_stop_check(w) not in VAGUE_SEARCH_TERMS]
        if not kept:
            kept = words[-max_words:] if len(words) > max_words else words
        short = " ".join(kept[:max_words]) if kept else kw
        short = re.sub(r"\s+", " ", short).strip()
        short_norm = _normalize_for_stop_check(short)
        if short and short not in seen and len(short) > 2 and short_norm not in VAGUE_SEARCH_TERMS:
            seen.add(short)
            result.append(short)
        # Also add a 2-word variant for long phrases (e.g. "ball trajectory" from "technical diagram of ball trajectory")
        if len(kept) > 3:
            core = " ".join(kept[-2:])  # last two words often = main noun phrase
            core_norm = _normalize_for_stop_check(core) if core else ""
            if core and core not in seen and len(core) > 3 and core_norm not in VAGUE_SEARCH_TERMS:
                seen.add(core)
                result.append(core)
    return result[:20]


CATEGORY_VISUAL_FALLBACKS = {
    "motivational": ["sunrise inspiration", "person walking forward", "mountain peak", "ocean waves calm"],
    "funny": ["laughing people", "funny reaction", "comedy stage", "colorful confetti"],
    "meme": ["internet culture", "trending pop culture", "social media phone", "neon signs"],
    "news": ["city skyline", "newspaper headlines", "breaking news background", "world globe"],
    "storytime": ["cozy room aesthetic", "cinematic close up face", "dramatic lighting", "night city lights"],
    "howto": ["hands demonstrating", "step by step tutorial", "notebook tips", "clean workspace"],
    "pov": ["first person perspective", "relatable moment", "phone screen pov", "everyday life"],
    "reaction": ["news discussion", "commentary background", "debate panel", "social media reaction"],
    "wellness": ["meditation nature", "yoga sunrise", "peaceful garden", "calm water ripples"],
    "wellbeing": ["self care routine", "healthy lifestyle", "nature walk", "morning sunlight"],
    "viral": ["social media trending", "crowd reaction", "neon lights", "fast motion city"],
}

# Lighting/mood cues per category for AI image prompts (Segmind Flux/Qwen guides)
CATEGORY_LIGHTING_MAP = {
    "motivational": "golden hour, uplifting atmosphere",
    "funny": "bright natural light, cheerful",
    "meme": "neon accents, vibrant colors",
    "news": "professional lighting, dramatic contrast",
    "storytime": "soft cinematic lighting, warm tones",
    "howto": "clean studio light, clear and focused",
    "pov": "natural daylight, relatable setting",
    "reaction": "studio setup, focused lighting",
    "wellness": "soft morning light, peaceful",
    "wellbeing": "warm natural light, serene",
    "viral": "dynamic lighting, eye-catching",
}


def _build_ai_video_prompt(search_queries: list[str], category: str) -> str:
    """Build structured video prompt per Segmind Veo guides: subject → motion → camera → style."""
    subject = search_queries[0] if search_queries else "cinematic scene"
    motion = search_queries[1] if len(search_queries) > 1 else "smooth movement"
    style = search_queries[2] if len(search_queries) > 2 else "soft cinematic light"
    lighting = CATEGORY_LIGHTING_MAP.get(category, "soft cinematic light")
    return f"Medium shot of {subject}. {motion.capitalize()}. Camera tracks smoothly from the side. {lighting}, ambient atmosphere."


def _build_ai_image_prompt(search_queries: list[str], category: str) -> str:
    """Build structured image prompt per Segmind Flux/Qwen guides: subject → style → environment → lighting."""
    subject = search_queries[0] if search_queries else "cinematic scene"
    env = search_queries[1] if len(search_queries) > 1 else "aesthetic background"
    mood = search_queries[2] if len(search_queries) > 2 else "high quality"
    lighting = CATEGORY_LIGHTING_MAP.get(category, "soft lighting")
    return f"{subject}, cinematic vertical composition, {env}, {lighting}, 9:16 portrait, {mood}"


def _expand_visual_queries(visual_cues: list[str], title: str, category: str,
                           script_keywords: list[str] | None = None,
                           trend_topic: str | None = None) -> list[str]:
    """Build a broad set of search queries from script keywords, cues, title, topic, and category fallbacks.
    Priority order: topic modifiers > script-derived keywords > simplified cues > title > category fallbacks.
    """
    queries = []

    # Topic modifiers (when script inspired by gaming/roblox trends)
    if trend_topic and trend_topic in TOPIC_VISUAL_MODIFIERS:
        queries.extend(TOPIC_VISUAL_MODIFIERS[trend_topic])

    # Script-derived keywords: use shortened forms for stock APIs (2-4 words match better)
    if script_keywords:
        queries.extend(_shorten_keywords_for_search(script_keywords))

    # Simplify visual cues: extract core nouns/phrases, shorten for search
    for cue in visual_cues:
        clean = re.sub(r"\b(show|effect|animation|graphic|footage|visual|close-up|slow|fast|aesthetic)\b", "", cue, flags=re.IGNORECASE)
        clean = re.sub(r"\s+", " ", clean).strip()
        if len(clean) > 3:
            for short in _shorten_keywords_for_search([clean]):
                queries.append(short)
        if len(cue.split()) <= MAX_SEARCH_QUERY_WORDS:
            queries.append(cue)

    # Extract 2-3 word noun phrases from the title
    title_clean = re.sub(r"[^\w\s]", "", title)
    title_words = [w for w in title_clean.split() if len(w) > 3 and w.lower() not in STOP_WORDS]
    if len(title_words) >= 2:
        queries.append(" ".join(title_words[:3]))

    # Category-based fallbacks (lowest priority, broadest)
    fallbacks = CATEGORY_VISUAL_FALLBACKS.get(category, CATEGORY_VISUAL_FALLBACKS.get("motivational", []))
    queries.extend(fallbacks)

    # Deduplicate while preserving order
    seen = set()
    unique = []
    for q in queries:
        q_lower = q.lower().strip()
        if q_lower and q_lower not in seen:
            seen.add(q_lower)
            unique.append(q)

    return unique


def _load_meta_json(path: Path) -> dict:
    """Load optional .meta.json alongside a file. Returns {} on missing or error."""
    meta_path = path.with_name(path.stem + ".meta.json")
    if not meta_path.exists():
        return {}
    try:
        data = json.loads(meta_path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, OSError):
        return {}


def _write_stock_meta(
    path: Path,
    *,
    search_query: str,
    source: str,
    asset_type: str,
    keywords: list[str] | None = None,
) -> None:
    """Write .meta.json alongside stock download for reuse scoring in future runs."""
    keywords = keywords or ([search_query] if search_query else [])
    meta_path = path.with_name(path.stem + ".meta.json")
    try:
        meta_path.write_text(
            json.dumps({
                "keywords": keywords,
                "search_query": search_query,
                "source": source,
                "asset_type": asset_type,
            }, indent=2),
            encoding="utf-8",
        )
    except OSError as e:
        logger.debug("Could not write stock meta for %s: %s", path.name, e)


def _scan_stock_videos() -> list[dict]:
    """Scan assets/stock_footage/ for downloaded videos with metadata (for reuse)."""
    candidates = []
    if not STOCK_DIR.exists():
        return candidates
    for p in STOCK_DIR.iterdir():
        if not p.is_file() or p.suffix.lower() not in VIDEO_EXTENSIONS:
            continue
        meta = _load_meta_json(p)
        if not meta:
            continue
        keywords = meta.get("keywords", [])
        search_query = meta.get("search_query", "")
        searchable = p.stem.lower()
        if keywords:
            searchable += " " + " ".join(str(k).lower() for k in keywords)
        if search_query:
            searchable += " " + search_query.lower()
        candidates.append({
            "path": p,
            "searchable_text": searchable.strip(),
            "source": meta.get("source", "stock"),
            "asset_type": "video",
        })
    return candidates


def _scan_stock_images() -> list[dict]:
    """Scan assets/images/ for downloaded images with metadata (for reuse)."""
    candidates = []
    if not IMAGES_DIR.exists():
        return candidates
    for p in IMAGES_DIR.iterdir():
        if not p.is_file() or p.suffix.lower() not in IMAGE_EXTENSIONS:
            continue
        meta = _load_meta_json(p)
        if not meta:
            continue
        keywords = meta.get("keywords", [])
        search_query = meta.get("search_query", "")
        searchable = p.stem.lower()
        if keywords:
            searchable += " " + " ".join(str(k).lower() for k in keywords)
        if search_query:
            searchable += " " + search_query.lower()
        candidates.append({
            "path": p,
            "searchable_text": searchable.strip(),
            "source": meta.get("source", "stock"),
            "asset_type": "image",
        })
    return candidates


def _scan_ai_videos() -> list[dict]:
    """Scan assets/stock_footage/ai/ for AI-generated videos (for reuse)."""
    ai_dir = STOCK_DIR / "ai"
    candidates = []
    if not ai_dir.exists():
        return candidates
    for p in ai_dir.iterdir():
        if not p.is_file() or p.suffix.lower() not in VIDEO_EXTENSIONS:
            continue
        meta = _load_meta_json(p)
        keywords = meta.get("keywords", [])
        category = meta.get("category", "")
        searchable = p.stem.lower()
        if keywords:
            searchable += " " + " ".join(str(k).lower() for k in keywords)
        if category:
            searchable += " " + category.lower()
        candidates.append({
            "path": p,
            "searchable_text": searchable.strip(),
            "source": meta.get("provider", "ai_video"),
            "asset_type": "video",
        })
    return candidates


def _scan_ai_images() -> list[dict]:
    """Scan assets/images/ai/ for AI-generated images (for reuse)."""
    ai_dir = IMAGES_DIR / "ai"
    candidates = []
    if not ai_dir.exists():
        return candidates
    for p in ai_dir.iterdir():
        if not p.is_file() or p.suffix.lower() not in IMAGE_EXTENSIONS:
            continue
        meta = _load_meta_json(p)
        keywords = meta.get("keywords", [])
        category = meta.get("category", "")
        searchable = p.stem.lower()
        if keywords:
            searchable += " " + " ".join(str(k).lower() for k in keywords)
        if category:
            searchable += " " + category.lower()
        candidates.append({
            "path": p,
            "searchable_text": searchable.strip(),
            "source": "ai_image",
            "asset_type": "image",
        })
    return candidates


def _scan_user_videos() -> list[dict]:
    """Scan assets/stock_footage/user/, return list of {path, searchable_text, source: 'user'}."""
    candidates = []
    if not USER_VIDEO_DIR.exists():
        return candidates
    for p in USER_VIDEO_DIR.iterdir():
        if p.is_file() and p.suffix.lower() in VIDEO_EXTENSIONS:
            meta = _load_meta_json(p)
            keywords = meta.get("keywords", [])
            mood = meta.get("mood", "")
            searchable = p.stem.lower()
            if keywords:
                searchable += " " + " ".join(str(k).lower() for k in keywords)
            if mood:
                searchable += " " + str(mood).lower()
            candidates.append({
                "path": p,
                "searchable_text": searchable.strip(),
                "source": "user",
                "asset_type": "video",
            })
    return candidates


def _scan_user_images() -> list[dict]:
    """Scan assets/images/user/, return list of {path, searchable_text, source: 'user'}."""
    candidates = []
    if not USER_IMAGE_DIR.exists():
        return candidates
    for p in USER_IMAGE_DIR.iterdir():
        if p.is_file() and p.suffix.lower() in IMAGE_EXTENSIONS:
            meta = _load_meta_json(p)
            keywords = meta.get("keywords", [])
            mood = meta.get("mood", "")
            searchable = p.stem.lower()
            if keywords:
                searchable += " " + " ".join(str(k).lower() for k in keywords)
            if mood:
                searchable += " " + str(mood).lower()
            candidates.append({
                "path": p,
                "searchable_text": searchable.strip(),
                "source": "user",
                "asset_type": "image",
            })
    return candidates


def _score_visual_candidate(
    candidate: dict,
    search_queries: list[str],
    category: str,
) -> float:
    """Score a video/image candidate by query matches. Uses CATEGORY_VISUAL_FALLBACKS for mood."""
    text = (candidate.get("searchable_text") or "").lower()
    score = 0.0
    q_set = {q.lower() for q in search_queries if q}
    for q in q_set:
        if q in text:
            score += 0.5
    fallbacks = CATEGORY_VISUAL_FALLBACKS.get(category, [])
    for fb in fallbacks:
        if fb and fb.lower() in text:
            score += 0.3
    return score


def source_assets_for_script(script: dict) -> bool:
    """Download all required assets for a single script. Returns True if successful.
    Pipeline order: extract keywords from script -> search videos/images -> music -> voiceover.
    """
    _ensure_dirs()
    conn = get_connection()
    script_id = script["id"]
    category = script.get("category", "motivational")
    title = script.get("title", "")
    script_body = script.get("script_body", "")

    # Parse tags
    script_tags = script.get("suggested_tags", "[]")
    if isinstance(script_tags, str):
        try:
            script_tags = json.loads(script_tags)
        except json.JSONDecodeError:
            script_tags = []

    # Step 1: Extract keywords from the actual script content for targeted searching
    script_keywords = _extract_script_keywords(script_body, title, script_tags)
    logger.info("Script #%d: extracted %d keywords: %s", script_id, len(script_keywords), script_keywords[:8])

    # No static trend topic augmentation — discovery and asset selection use only user-provided queries
    trend_topic = None

    visual_cues = script.get("visual_cues", "[]")
    if isinstance(visual_cues, str):
        try:
            visual_cues = json.loads(visual_cues)
        except json.JSONDecodeError:
            visual_cues = []

    if not visual_cues:
        visual_cues = ["aesthetic background", "cinematic footage"]

    # Step 2: Build search queries — topic > script keywords > cues > fallbacks
    search_queries = _expand_visual_queries(
        visual_cues, title, category,
        script_keywords=script_keywords,
        trend_topic=trend_topic,
    )

    videos_per_script = cfg("sourcing.pexels_videos_per_script") or 5
    assets_saved = 0
    images_saved = 0
    video_providers = cfg("sourcing.video_providers") or ["pexels", "pixabay", "coverr"]
    force_ai_video = script.get("force_ai_video_override") == 1
    force_ai_image = script.get("force_ai_image_override") == 1

    # User-selected override: insert user-picked assets first, in order
    user_selected = script.get("user_selected_asset_paths")
    if isinstance(user_selected, str):
        try:
            user_selected = json.loads(user_selected)
        except (json.JSONDecodeError, TypeError):
            user_selected = []
    if isinstance(user_selected, list) and user_selected:
        for item in user_selected:
            typ = item.get("type")
            path_str = item.get("path")
            if typ not in ("video", "image") or not path_str:
                continue
            path = Path(path_str)
            if not path.is_absolute():
                path = PROJECT_ROOT / path
            if path.exists():
                insert_asset(
                    conn, script_id=script_id, asset_type=typ, source="user_selected",
                    source_id=None, local_path=str(path.resolve()),
                )
                if typ == "video":
                    assets_saved += 1
                elif typ == "image":
                    images_saved += 1
                logger.info("Script #%d: using user-selected %s %s", script_id, typ, path.name)

    # Reaction: download trend video as first asset
    if category == "reaction":
        trend_ids = script.get("trend_source_ids")
        if isinstance(trend_ids, str):
            try:
                trend_ids = json.loads(trend_ids)
            except (json.JSONDecodeError, TypeError):
                trend_ids = []
        if isinstance(trend_ids, list) and trend_ids:
            trends = get_trends_by_ids(conn, [trend_ids[0]])
            if trends:
                t = trends[0]
                vid = t.get("video_id")
                plat = t.get("platform", "youtube")
                tid = t.get("id")
                if vid and tid:
                    path = _download_trend_video(vid, tid, plat, STOCK_DIR)
                    if path:
                        insert_asset(
                            conn, script_id=script_id, asset_type="video", source="trend",
                            source_id=vid, local_path=str(path), search_query="trend_clip",
                        )
                        assets_saved += 1
                    else:
                        logger.warning("Script #%d: trend video download failed, using stock B-roll only", script_id)
                else:
                    logger.warning("Script #%d: trend missing video_id or id", script_id)
            else:
                logger.warning("Script #%d: trend not found in DB, using stock B-roll only", script_id)

    # Two-phase video selection: user pool first, then external pool
    # When force_ai_video, skip stock/scraped and go directly to AI
    user_videos = _scan_user_videos()
    stock_videos = [] if force_ai_video else _scan_stock_videos()
    ai_videos = _scan_ai_videos()
    scraped_videos = []
    seen_scraped = set()
    if not force_ai_video:
        for i, cue in enumerate(search_queries[:15]):
            prov = video_providers[i % len(video_providers)] if video_providers else "pexels"
            if prov == "pexels":
                for item in search_pexels_videos(cue, count=3):
                    kid = ("pexels", str(item.get("id", "")))
                    if kid not in seen_scraped:
                        seen_scraped.add(kid)
                        scraped_videos.append({"source": "pexels", "raw": item, "searchable_text": cue})
            elif prov == "pixabay":
                for item in search_pixabay_videos(cue, count=3):
                    kid = ("pixabay", str(item.get("id", "")))
                    if kid not in seen_scraped:
                        seen_scraped.add(kid)
                        scraped_videos.append({"source": "pixabay", "raw": item, "searchable_text": cue})
            elif prov == "coverr":
                for item in search_coverr_videos(cue, count=3):
                    kid = ("coverr", str(item.get("id", "")))
                    if kid not in seen_scraped:
                        seen_scraped.add(kid)
                        scraped_videos.append({"source": "coverr", "raw": item, "searchable_text": cue})

    def _score_and_penalize_video(candidates: list) -> None:
        for c in candidates:
            c["_score"] = _score_visual_candidate(c, search_queries, category)
        for c in candidates:
            if c.get("path") and not c.get("raw"):
                p = c.get("path")
                fname = Path(p).name if p else ""
                size_bytes, mtime_real = None, None
                try:
                    if p and Path(p).exists():
                        st = Path(p).stat()
                        size_bytes, mtime_real = st.st_size, st.st_mtime
                except (OSError, TypeError):
                    pass
                penalty = get_rejection_penalty(conn, fname, "video", category, "Poor visual quality", size_bytes=size_bytes, mtime_real=mtime_real)
            else:
                raw = c.get("raw", {})
                fname = f"{c['source']}_{raw.get('id', '')}.mp4"
                penalty = get_rejection_penalty(conn, fname, "video", category, "Poor visual quality")
            c["_score"] -= penalty * PENALTY_PER_REJECTION

    def _pick_videos_from_candidates(candidates: list) -> int:
        n = 0
        for c in candidates:
            if assets_saved + n >= videos_per_script:
                break
            if c.get("path") and not c.get("raw") and Path(c["path"]).exists():
                path = c["path"]
                src = c.get("source", "stock")
                insert_asset(
                    conn, script_id=script_id, asset_type="video", source=src,
                    source_id=None, local_path=str(Path(path).resolve()), search_query=c.get("searchable_text", "reuse"),
                )
                n += 1
                logger.info("Script #%d: using %s video (score %.1f) %s", script_id, src, c["_score"], Path(path).name)
            elif c.get("raw"):
                prov = c["source"]
                item = c["raw"]
                cue = c.get("searchable_text", "")
                path = None
                if prov == "pexels":
                    path = download_pexels_video(item, STOCK_DIR)
                elif prov == "pixabay":
                    path = download_pixabay_video(item, STOCK_DIR)
                elif prov == "coverr":
                    path = download_coverr_video(item, STOCK_DIR)
                if path:
                    _write_stock_meta(path, search_query=cue, source=prov, asset_type="video", keywords=[cue])
                    if prov == "pexels":
                        insert_asset(
                            conn, script_id=script_id, asset_type="video", source="pexels",
                            source_id=str(item.get("id")), source_url=item.get("url"),
                            local_path=str(path), search_query=cue,
                            duration=item.get("duration"), width=item.get("width"), height=item.get("height"),
                        )
                    elif prov == "pixabay":
                        insert_asset(
                            conn, script_id=script_id, asset_type="video", source="pixabay",
                            source_id=str(item.get("id")), local_path=str(path), search_query=cue,
                        )
                    elif prov == "coverr":
                        insert_asset(
                            conn, script_id=script_id, asset_type="video", source="coverr",
                            source_id=str(item.get("id")), local_path=str(path), search_query=cue,
                            duration=item.get("duration"), width=item.get("max_width"), height=item.get("max_height"),
                        )
                    n += 1
        return n

    # Phase 1: fill from user pool first
    user_pool = user_videos
    _score_and_penalize_video(user_pool)
    user_pool.sort(key=lambda x: x["_score"], reverse=True)
    assets_saved += _pick_videos_from_candidates(user_pool)

    # Phase 2: fill remaining from external pool
    external_pool = stock_videos + ai_videos + scraped_videos
    _score_and_penalize_video(external_pool)
    external_pool.sort(key=lambda x: x["_score"], reverse=True)
    assets_saved += _pick_videos_from_candidates(external_pool)

    if not force_ai_video and assets_saved < 3:
        for cue in search_queries[:6]:
            if assets_saved >= videos_per_script:
                break
            for prov in ["pexels", "pixabay", "coverr"]:
                if prov not in video_providers:
                    continue
                if prov == "pexels":
                    for item in search_pexels_videos(cue, count=2):
                        kid = ("pexels", str(item.get("id", "")))
                        if kid in seen_scraped:
                            continue
                        path = download_pexels_video(item, STOCK_DIR)
                        if path:
                            _write_stock_meta(path, search_query=cue, source="pexels", asset_type="video", keywords=[cue])
                            seen_scraped.add(kid)
                            insert_asset(conn, script_id=script_id, asset_type="video", source="pexels",
                                source_id=str(item.get("id")), source_url=item.get("url"),
                                local_path=str(path), search_query=cue,
                                duration=item.get("duration"), width=item.get("width"), height=item.get("height"))
                            assets_saved += 1
                            break
                elif prov == "pixabay":
                    for item in search_pixabay_videos(cue, count=2):
                        kid = ("pixabay", str(item.get("id", "")))
                        if kid in seen_scraped:
                            continue
                        path = download_pixabay_video(item, STOCK_DIR)
                        if path:
                            _write_stock_meta(path, search_query=cue, source="pixabay", asset_type="video", keywords=[cue])
                            seen_scraped.add(kid)
                            insert_asset(conn, script_id=script_id, asset_type="video", source="pixabay",
                                source_id=str(item.get("id")), local_path=str(path), search_query=cue)
                            assets_saved += 1
                            break
                elif prov == "coverr":
                    for item in search_coverr_videos(cue, count=2):
                        kid = ("coverr", str(item.get("id", "")))
                        if kid in seen_scraped:
                            continue
                        path = download_coverr_video(item, STOCK_DIR)
                        if path:
                            _write_stock_meta(path, search_query=cue, source="coverr", asset_type="video", keywords=[cue])
                            seen_scraped.add(kid)
                            insert_asset(conn, script_id=script_id, asset_type="video", source="coverr",
                                source_id=str(item.get("id")), local_path=str(path), search_query=cue,
                                duration=item.get("duration"), width=item.get("max_width"), height=item.get("max_height"))
                            assets_saved += 1
                            break
                if assets_saved >= videos_per_script:
                    break

    # AI video fallback when stock insufficient, or when force_ai_video override
    ai_video_providers = cfg("sourcing.ai_video_providers") or []
    if assets_saved < videos_per_script and ai_video_providers:
        ai_fallback = cfg("sourcing.ai_video_fallback_only") is not False
        if force_ai_video or ai_fallback or assets_saved == 0:
            from agents.ai_video_providers import generate_ai_video, write_ai_video_meta
            est_dur = script.get("estimated_duration") or 30
            ai_dir = STOCK_DIR / "ai"
            ai_dir.mkdir(parents=True, exist_ok=True)
            while assets_saved < videos_per_script:
                try:
                    prompt = _build_ai_video_prompt(search_queries, category)
                    path = generate_ai_video(prompt, duration=min(8, est_dur), dest_dir=ai_dir)
                    if path and path.exists():
                        write_ai_video_meta(path, keywords=search_queries[:5] or [prompt], category=category, prompt=prompt, provider="segmind")
                        insert_asset(conn, script_id=script_id, asset_type="video", source="segmind",
                            source_id=None, local_path=str(path), search_query=prompt)
                        assets_saved += 1
                        logger.info("Script #%d: AI video generated via Segmind", script_id)
                    else:
                        break
                except Exception as e:
                    logger.warning("AI video fallback failed: %s", e)
                    break

    # Two-phase image selection: user pool first, then external pool
    # When force_ai_image, skip stock/scraped and go directly to AI
    user_images = _scan_user_images()
    stock_images = [] if force_ai_image else _scan_stock_images()
    ai_images = _scan_ai_images()
    scraped_images = []
    seen_img = set()
    if not force_ai_image:
        image_providers = cfg("sourcing.image_providers") or ["pexels", "pixabay", "unsplash", "openverse"]
        for i, q in enumerate(search_queries[:10]):
            prov = image_providers[i % len(image_providers)] if image_providers else "pexels"
            items = []
            if prov == "pexels":
                items = search_pexels_images(q, count=3)
            elif prov == "pixabay":
                items = search_pixabay_images(q, count=3)
            elif prov == "unsplash":
                items = search_unsplash_images(q, count=3)
            elif prov == "openverse":
                items = search_openverse_images(q, count=3)
            for item in items[:2]:
                kid = (prov, str(item.get("id", "")))
                if kid not in seen_img:
                    seen_img.add(kid)
                    scraped_images.append({"source": prov, "raw": item, "searchable_text": q})

    def _score_and_penalize_image(candidates: list) -> None:
        for c in candidates:
            c["_score"] = _score_visual_candidate(c, search_queries, category)
        for c in candidates:
            if c.get("path") and not c.get("raw"):
                p = c.get("path")
                fname = Path(p).name if p else ""
                size_bytes, mtime_real = None, None
                try:
                    if p and Path(p).exists():
                        st = Path(p).stat()
                        size_bytes, mtime_real = st.st_size, st.st_mtime
                except (OSError, TypeError):
                    pass
                penalty = get_rejection_penalty(conn, fname, "image", category, "Poor visual quality", size_bytes=size_bytes, mtime_real=mtime_real)
            else:
                raw = c.get("raw", {})
                fname = f"{c['source']}_{raw.get('id', '')}.jpg"
                penalty = get_rejection_penalty(conn, fname, "image", category, "Poor visual quality")
            c["_score"] -= penalty * PENALTY_PER_REJECTION

    def _pick_images_from_candidates(candidates: list) -> int:
        n = 0
        for c in candidates:
            if images_saved + n >= 3:
                break
            if c.get("path") and not c.get("raw") and Path(c["path"]).exists():
                path = c["path"]
                src = c.get("source", "stock")
                insert_asset(
                    conn, script_id=script_id, asset_type="image", source=src,
                    source_id=None, local_path=str(Path(path).resolve()), search_query=c.get("searchable_text", "reuse"),
                )
                n += 1
                logger.info("Script #%d: using %s image (score %.1f) %s", script_id, src, c.get("_score", 0), Path(path).name)
            elif c.get("raw"):
                prov = c["source"]
                item = c["raw"]
                q = c.get("searchable_text", "")
                path = None
                if prov == "pexels":
                    path = download_pexels_image(item, IMAGES_DIR)
                elif prov == "pixabay":
                    path = download_pixabay_image(item, IMAGES_DIR)
                elif prov == "unsplash":
                    path = download_unsplash_image(item, IMAGES_DIR)
                elif prov == "openverse":
                    path = download_openverse_image(item, IMAGES_DIR)
                if path:
                    _write_stock_meta(path, search_query=q, source=prov, asset_type="image", keywords=[q] if q else [])
                    if prov == "pexels":
                        insert_asset(conn, script_id=script_id, asset_type="image", source="pexels",
                            source_id=str(item.get("id")), source_url=item.get("url"),
                            local_path=str(path), search_query=q, width=item.get("width"), height=item.get("height"))
                    elif prov == "pixabay":
                        insert_asset(conn, script_id=script_id, asset_type="image", source="pixabay",
                            source_id=str(item.get("id")), local_path=str(path), search_query=q,
                            width=item.get("webformatWidth"), height=item.get("webformatHeight"))
                    elif prov == "unsplash":
                        insert_asset(conn, script_id=script_id, asset_type="image", source="unsplash",
                            source_id=str(item.get("id")), source_url=item.get("urls", {}).get("full"),
                            local_path=str(path), search_query=q, width=item.get("width"), height=item.get("height"))
                    elif prov == "openverse":
                        insert_asset(conn, script_id=script_id, asset_type="image", source="openverse",
                            source_id=str(item.get("id")), source_url=item.get("foreign_landing_url"),
                            local_path=str(path), search_query=q, width=item.get("width"), height=item.get("height"))
                    n += 1
        return n

    # Phase 1: fill from user pool first
    user_img_pool = user_images
    _score_and_penalize_image(user_img_pool)
    user_img_pool.sort(key=lambda x: x["_score"], reverse=True)
    images_saved += _pick_images_from_candidates(user_img_pool)

    # Phase 2: fill remaining from external pool
    external_img_pool = stock_images + ai_images + scraped_images
    _score_and_penalize_image(external_img_pool)
    external_img_pool.sort(key=lambda x: x["_score"], reverse=True)
    images_saved += _pick_images_from_candidates(external_img_pool)

    if not force_ai_image and images_saved < 3:
        for q in search_queries[:4]:
            if images_saved >= 3:
                break
            for photo in search_pexels_images(q, count=2)[:1]:
                kid = ("pexels", str(photo.get("id", "")))
                if kid in seen_img:
                    continue
                path = download_pexels_image(photo, IMAGES_DIR)
                if path:
                    _write_stock_meta(path, search_query=q, source="pexels", asset_type="image", keywords=[q])
                    seen_img.add(kid)
                    insert_asset(conn, script_id=script_id, asset_type="image", source="pexels",
                        source_id=str(photo.get("id")), source_url=photo.get("url"),
                        local_path=str(path), search_query=q, width=photo.get("width"), height=photo.get("height"))
                    images_saved += 1
                    if images_saved >= 3:
                        break

    # AI image fallback when stock insufficient, or when force_ai_image override
    ai_image_providers = cfg("sourcing.ai_image_providers") or []
    if images_saved < 3 and ai_image_providers:
        ai_fallback = cfg("sourcing.ai_image_fallback_only") is not False
        if force_ai_image or ai_fallback or images_saved == 0:
            from agents.ai_image_providers import generate_ai_image, write_ai_image_meta
            ai_dir = IMAGES_DIR / "ai"
            ai_dir.mkdir(parents=True, exist_ok=True)
            while images_saved < 3:
                try:
                    prompt = _build_ai_image_prompt(search_queries, category)
                    path, img_provider = generate_ai_image(prompt, aspect_ratio=cfg("sourcing.ai_image_aspect_ratio") or "9:16", dest_dir=ai_dir)
                    if path and path.exists():
                        write_ai_image_meta(path, keywords=search_queries[:5] or [prompt[:80]], category=category, prompt=prompt, provider=img_provider or "flux")
                        insert_asset(conn, script_id=script_id, asset_type="image", source=img_provider or "flux",
                            source_id=None, local_path=str(path), search_query=prompt)
                        images_saved += 1
                        logger.info("Script #%d: AI image generated via %s", script_id, img_provider or "flux")
                    else:
                        break
                except Exception as e:
                    logger.warning("AI image fallback failed: %s", e)
                    break

    # Step 4: Source background music (with script keywords and topic augmentation)
    music_override = script.get("music_override_path")
    if music_override and Path(music_override).expanduser().exists():
        from agents.music_scraper import CATEGORY_MUSIC_MAP
        override_path = Path(music_override).expanduser().resolve()
        music_config = CATEGORY_MUSIC_MAP.get(category, CATEGORY_MUSIC_MAP["storytime"])
        insert_asset(
            conn,
            script_id=script_id,
            asset_type="music",
            source="user_selected",
            source_id=None,
            local_path=str(override_path),
            mood=music_config["mood"],
        )
        logger.info("Script #%d: using music override %s", script_id, override_path.name)
    else:
        from agents.music_scraper import source_music_for_script
        force_ai = script.get("force_ai_audio_override") == 1 or cfg("sourcing.music.force_ai_audio") or False
        source_music_for_script(
            script_id, category, tags=script_tags, trend_topic=trend_topic,
            script_keywords=script_keywords,
            force_ai_audio=force_ai,
        )

    # Step 5: Generate voiceover with word-boundary metadata for caption sync.
    full_text = script_body.strip()

    vo_path, vo_meta_path = generate_voiceover(
        full_text, category, script_id, voice_override=script.get("voice_override")
    )
    if vo_path:
        # Store metadata path in source_url field for the composer to read
        insert_asset(
            conn,
            script_id=script_id,
            asset_type="voiceover",
            source="edge-tts",
            source_url=str(vo_meta_path) if vo_meta_path else None,
            local_path=str(vo_path),
        )

    # Update script status
    all_assets = get_assets_for_script(conn, script_id)
    has_video = any(a["asset_type"] == "video" for a in all_assets)
    has_voiceover = any(a["asset_type"] == "voiceover" for a in all_assets)

    if has_video and has_voiceover:
        update_script_status(conn, script_id, "assets_ready")
        logger.info("Script #%d: all assets ready (%d assets)", script_id, len(all_assets))
        conn.close()
        return True
    else:
        logger.warning("Script #%d: incomplete assets (video=%s, vo=%s)", script_id, has_video, has_voiceover)
        conn.close()
        return False


def run_sourcing() -> dict:
    """Source assets for all pending scripts, including any stuck incomplete jobs."""
    logger.info("Starting asset sourcing...")
    conn = get_connection()

    # Recover scripts stuck in 'pending_assets' that might have partially failed:
    # re-check if they already have enough assets from a prior partial run
    partial = get_scripts_by_status(conn, "pending_assets")
    for script in partial:
        existing = get_assets_for_script(conn, script["id"])
        has_video = any(a["asset_type"] == "video" for a in existing)
        has_vo = any(a["asset_type"] == "voiceover" for a in existing)
        if has_video and has_vo:
            logger.info("Script #%d: already has assets from prior run — promoting to assets_ready", script["id"])
            update_script_status(conn, script["id"], "assets_ready")

    scripts = get_scripts_by_status(conn, "pending_assets")
    conn.close()

    if not scripts:
        logger.info("No scripts pending assets")
        return {"scripts_processed": 0, "success": 0}

    success = 0
    errors = []
    for script in scripts:
        try:
            if source_assets_for_script(script):
                success += 1
            else:
                errors.append(f"script #{script.get('id', '?')}: returned False")
        except Exception as e:
            errors.append(f"script #{script.get('id', '?')}: {e}")
            logger.error("Asset sourcing error for script #%s: %s", script.get("id", "?"), e)

    summary = {"scripts_processed": len(scripts), "success": success}
    if scripts and success == 0:
        summary["error"] = f"All {len(scripts)} scripts failed asset sourcing: {'; '.join(errors[:3])}"
    logger.info("Sourcing complete: %s", summary)
    return summary


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        handlers=[
            logging.StreamHandler(),
            logging.FileHandler(PROJECT_ROOT / "logs" / "sourcing.log"),
        ],
    )
    from models.database import init_db
    init_db()
    result = run_sourcing()
    print(json.dumps(result, indent=2))
