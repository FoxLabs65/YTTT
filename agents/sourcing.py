"""
Asset Sourcing Agent
Downloads B-roll footage, background images, music, and generates
voiceovers for pending scripts.
"""

import asyncio
import json
import logging
import re
import uuid
from pathlib import Path

import requests

from models.config import get as cfg
from models.database import (
    get_connection,
    get_scripts_by_status,
    get_assets_for_script,
    insert_asset,
    update_script_status,
    reset_stuck_scripts,
)

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).parent.parent
ASSETS_DIR = PROJECT_ROOT / "assets"
STOCK_DIR = ASSETS_DIR / "stock_footage"
IMAGES_DIR = ASSETS_DIR / "images"
MUSIC_DIR = ASSETS_DIR / "music"
VOICEOVER_DIR = ASSETS_DIR / "voiceovers"


def _ensure_dirs():
    for d in (STOCK_DIR, IMAGES_DIR, MUSIC_DIR, VOICEOVER_DIR):
        d.mkdir(parents=True, exist_ok=True)


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


# --- Pixabay API ---

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

async def _generate_voiceover_async(text: str, voice: str, output_path: Path,
                                    rate: str = "+0%", meta_path: Path | None = None):
    import edge_tts
    communicate = edge_tts.Communicate(text, voice, rate=rate, boundary="WordBoundary")
    await communicate.save(str(output_path), metadata_fname=str(meta_path) if meta_path else None)


def _humanise_text(text: str) -> str:
    """Insert natural pauses and breathing breaks so TTS sounds less robotic.

    edge-tts respects commas, periods, and ellipses as pause cues.
    This function:
      - Adds short pauses after commas/colons (edge-tts already handles these)
      - Inserts medium pauses between sentences by adding an ellipsis after periods
      - Adds a longer pause before dramatic sentences starting with "But", "So", "And"
      - Converts "..." in the script to a real hesitation pause
    """
    # Normalise whitespace first
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
    """
    voices = cfg("sourcing.voiceover_voices") or {}
    voice = voice_override or voices.get(category, "en-US-AndrewMultilingualNeural")
    rate = cfg("sourcing.voiceover_rate") or "-5%"

    stem = f"vo_{script_id}_{uuid.uuid4().hex[:8]}"
    output_path = VOICEOVER_DIR / f"{stem}.mp3"
    meta_path = VOICEOVER_DIR / f"{stem}.json"

    # Strip visual cues from script for voiceover
    clean_text = re.sub(r"\[.*?\]", "", text).strip()
    clean_text = re.sub(r"\s+", " ", clean_text)

    # Remove trailing period from Mr/Mrs/Ms/Dr etc. — TTS treats "." as sentence end and adds long pause
    clean_text = re.sub(r"\b(Mr|Mrs|Ms|Dr|Drs|Prof|Sr|Jr)\.", r"\1", clean_text)

    if not clean_text:
        logger.warning("Empty text after cleaning for script %d", script_id)
        return None, None

    # Humanise text with natural pauses
    clean_text = _humanise_text(clean_text)

    try:
        asyncio.run(_generate_voiceover_async(clean_text, voice, output_path, rate, meta_path))
        logger.info("Generated voiceover: %s (%s, rate=%s)", output_path.name, voice, rate)
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
})


def _extract_script_keywords(script_body: str, title: str, tags: list[str]) -> list[str]:
    """Extract specific, searchable visual keywords from the script text.
    Focuses on concrete nouns, named entities, and visual objects that
    stock libraries can match.
    """
    keywords = []

    # Extract noun phrases from title
    title_clean = re.sub(r"[^\w\s]", "", title)
    title_words = [w for w in title_clean.split() if w.lower() not in STOP_WORDS and len(w) > 2]
    if title_words:
        keywords.append(" ".join(title_words[:4]))

    # Extract concrete visual terms from script body (strip [visual cues] first)
    body = re.sub(r"\[.*?\]", " ", script_body)
    body = re.sub(r"[^\w\s'-]", " ", body)
    words = body.split()

    # Find capitalized proper nouns / names (e.g. "Prince", "Mrs Henderson")
    for i, w in enumerate(words):
        if w[0].isupper() and w.lower() not in STOP_WORDS and len(w) > 2:
            if i > 0 and words[i-1][0].isupper():
                keywords.append(f"{words[i-1]} {w}")
            else:
                keywords.append(w)

    # Extract 2-word concrete phrases (adjective+noun patterns)
    concrete_indicators = {
        "cold", "dark", "bright", "loud", "quiet", "fast", "slow", "big",
        "empty", "full", "broken", "calm", "dramatic", "live", "national",
    }
    for i in range(len(words) - 1):
        w1, w2 = words[i].lower(), words[i+1].lower()
        if w1 in concrete_indicators and w2 not in STOP_WORDS and len(w2) > 2:
            keywords.append(f"{words[i]} {words[i+1]}")

    # Extract standalone concrete nouns (things you can film/photograph)
    visual_nouns = {
        "room", "desk", "building", "house", "school", "classroom", "stage",
        "phone", "camera", "screen", "mirror", "window", "door", "car",
        "ocean", "mountain", "forest", "city", "street", "sunset", "sunrise",
        "rain", "fire", "water", "sky", "crowd", "person", "people", "face",
        "hand", "hands", "eyes", "brain", "heart", "moth", "earthquake",
        "television", "microphone", "guitar", "piano", "playlist", "flute",
        "sketch", "newspaper", "anchor", "teacher", "student", "police",
    }
    for w in words:
        if w.lower() in visual_nouns:
            keywords.append(w.lower())

    # Add tag-derived terms (cleaned hashtags)
    for tag in tags[:5]:
        clean = tag.lstrip("#").replace("_", " ")
        if len(clean) > 2 and clean.lower() not in STOP_WORDS:
            keywords.append(clean)

    # Deduplicate while preserving order
    seen = set()
    unique = []
    for k in keywords:
        k_lower = k.lower().strip()
        if k_lower and k_lower not in seen and len(k_lower) > 2:
            seen.add(k_lower)
            unique.append(k)

    return unique[:15]


CATEGORY_VISUAL_FALLBACKS = {
    "motivational": ["sunrise inspiration", "person walking forward", "mountain peak", "ocean waves calm"],
    "funny": ["laughing people", "funny reaction", "comedy stage", "colorful confetti"],
    "meme": ["internet culture", "trending pop culture", "social media phone", "neon signs"],
    "news": ["city skyline", "newspaper headlines", "breaking news background", "world globe"],
    "storytime": ["cozy room aesthetic", "cinematic close up face", "dramatic lighting", "night city lights"],
    "wellness": ["meditation nature", "yoga sunrise", "peaceful garden", "calm water ripples"],
    "wellbeing": ["self care routine", "healthy lifestyle", "nature walk", "morning sunlight"],
    "viral": ["social media trending", "crowd reaction", "neon lights", "fast motion city"],
}


def _expand_visual_queries(visual_cues: list[str], title: str, category: str,
                           script_keywords: list[str] | None = None) -> list[str]:
    """Build a broad set of search queries from script keywords, cues, title, and category fallbacks.
    Priority order: script-derived keywords > simplified cues > title > category fallbacks.
    """
    queries = []

    # Script-derived keywords are highest priority (most relevant to actual content)
    if script_keywords:
        queries.extend(script_keywords)

    # Simplify visual cues: extract core nouns/phrases, drop overly specific adjectives
    for cue in visual_cues:
        clean = re.sub(r"\b(show|effect|animation|graphic|footage|visual|close-up|slow|fast|aesthetic)\b", "", cue, flags=re.IGNORECASE)
        clean = re.sub(r"\s+", " ", clean).strip()
        if len(clean) > 3:
            queries.append(clean)
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

    visual_cues = script.get("visual_cues", "[]")
    if isinstance(visual_cues, str):
        try:
            visual_cues = json.loads(visual_cues)
        except json.JSONDecodeError:
            visual_cues = []

    if not visual_cues:
        visual_cues = ["aesthetic background", "cinematic footage"]

    # Step 2: Build search queries — script keywords first, then cues, then fallbacks
    search_queries = _expand_visual_queries(visual_cues, title, category, script_keywords=script_keywords)

    videos_per_script = cfg("sourcing.pexels_videos_per_script") or 5
    assets_saved = 0

    # Download videos — search with expanded queries
    for cue in search_queries:
        if assets_saved >= videos_per_script:
            break

        pexels_videos = search_pexels_videos(cue, count=3)
        for pv in pexels_videos[:2]:
            path = download_pexels_video(pv, STOCK_DIR)
            if path:
                insert_asset(
                    conn,
                    script_id=script_id,
                    asset_type="video",
                    source="pexels",
                    source_id=str(pv.get("id")),
                    source_url=pv.get("url"),
                    local_path=str(path),
                    search_query=cue,
                    duration=pv.get("duration"),
                    width=pv.get("width"),
                    height=pv.get("height"),
                )
                assets_saved += 1
                if assets_saved >= videos_per_script:
                    break

    # Supplement with Pixabay if Pexels didn't provide enough
    if assets_saved < 3:
        for cue in search_queries[:6]:
            if assets_saved >= videos_per_script:
                break
            pixabay_hits = search_pixabay_videos(cue, count=3)
            for hit in pixabay_hits[:2]:
                path = download_pixabay_video(hit, STOCK_DIR)
                if path:
                    insert_asset(
                        conn,
                        script_id=script_id,
                        asset_type="video",
                        source="pixabay",
                        source_id=str(hit.get("id")),
                        local_path=str(path),
                        search_query=cue,
                    )
                    assets_saved += 1
                    if assets_saved >= videos_per_script:
                        break

    # Download images — search with broader queries and both APIs
    images_saved = 0
    for q in search_queries[:6]:
        if images_saved >= 3:
            break
        photos = search_pexels_images(q, count=3)
        for photo in photos[:2]:
            path = download_pexels_image(photo, IMAGES_DIR)
            if path:
                insert_asset(
                    conn,
                    script_id=script_id,
                    asset_type="image",
                    source="pexels",
                    source_id=str(photo.get("id")),
                    source_url=photo.get("url"),
                    local_path=str(path),
                    search_query=q,
                    width=photo.get("width"),
                    height=photo.get("height"),
                )
                images_saved += 1
                if images_saved >= 3:
                    break

    # Step 4: Source background music
    from agents.music_scraper import source_music_for_script
    source_music_for_script(script_id, category, tags=script_tags)

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
