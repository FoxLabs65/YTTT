"""
Trend Discovery Agent
Scrapes YouTube and TikTok for trending short-form content,
extracts tags, calculates trend scores, and stores results.
"""

import json
import logging
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from models.config import get as cfg
from models.database import (
    get_connection,
    insert_trend,
    update_trending_tag,
    get_top_trends,
)

logger = logging.getLogger(__name__)

# yt-dlp output template for metadata extraction
_YT_DLP = "yt-dlp"
MAX_DURATION = 120  # seconds -- filter Shorts candidates


def _run_ytdlp(args: list[str], timeout: int = 120) -> dict | list | None:
    cmd = [_YT_DLP, "--no-download", "--dump-json", "--no-warnings", "--quiet"] + args
    logger.debug("Running: %s", " ".join(cmd))
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout, encoding="utf-8"
        )
        if result.returncode != 0:
            stderr = result.stderr[:500].strip()
            if stderr:
                logger.debug("yt-dlp stderr: %s", stderr)
            return None

        lines = [l.strip() for l in result.stdout.strip().split("\n") if l.strip()]
        entries = []
        for line in lines:
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return entries if entries else None
    except subprocess.TimeoutExpired:
        logger.warning("yt-dlp timed out for args: %s", args[:5])
        return None
    except FileNotFoundError:
        logger.error("yt-dlp not found. Install via: pip install yt-dlp")
        return None


def _calculate_trend_score(view_count: int, publish_date_str: str | None) -> float:
    if not publish_date_str or not view_count:
        return float(view_count or 0)
    try:
        publish_dt = datetime.fromisoformat(publish_date_str.replace("Z", "+00:00"))
        hours = max((datetime.now(timezone.utc) - publish_dt).total_seconds() / 3600, 1)
        return view_count / hours
    except (ValueError, TypeError):
        return float(view_count or 0)


def _extract_tags_from_text(text: str) -> list[str]:
    if not text:
        return []
    return re.findall(r"#(\w+)", text)


# Tag/keyword -> category mapping for trend classification
_CATEGORY_KEYWORDS = {
    "motivational": ("motivation", "motivational", "inspire", "inspiring", "quote", "mindset", "success", "goals"),
    "funny": ("funny", "humor", "comedy", "laugh", "joke", "hilarious"),
    "meme": ("meme", "memes", "relatable", "pov", "viral"),
    "news": ("news", "breaking", "update", "reaction", "headline"),
    "storytime": ("storytime", "story", "storytelling", "anecdote", "happened"),
    "howto": ("howto", "how to", "tutorial", "tips", "did you know", "life hack", "guide", "learn"),
    "pov": ("pov", "point of view", "relatable", "the moment you", "when you"),
    "roblox": ("roblox", "blox", "brookhaven", "adopt me", "bloxburg"),
    "gaming": ("gaming", "game", "games", "gamer", "playthrough", "stream", "twitch", "esports", "fortnite", "minecraft"),
}


def _infer_category(tags: list[str], title: str) -> str:
    """Infer content category from tags and title. Returns 'other' if no match."""
    combined = " ".join(t.lower() for t in tags) + " " + (title or "").lower()
    for cat, keywords in _CATEGORY_KEYWORDS.items():
        if any(kw in combined for kw in keywords):
            return cat
    return "other"


def scrape_youtube_shorts(queries: list[str] | None = None, max_per_query: int = 20) -> list[dict]:
    queries = queries or cfg("discovery.youtube_queries") or ["trending shorts"]
    max_per_query = max_per_query or cfg("discovery.max_results_per_query") or 20
    conn = get_connection()
    all_trends = []

    for query in queries:
        logger.info("YouTube search: %s", query)
        search_url = f"ytsearch{max_per_query}:{query} shorts"
        entries = _run_ytdlp([search_url], timeout=180)

        if entries:
            entries = [e for e in entries if (e.get("duration") or 999) <= MAX_DURATION]

        if not entries:
            logger.warning("No results for YouTube query: %s", query)
            continue

        for entry in entries:
            video_id = entry.get("id") or entry.get("url", "")
            title = entry.get("title", "")
            description = entry.get("description", "")
            tags = entry.get("tags") or []
            extra_tags = _extract_tags_from_text(title + " " + description)
            all_tags = list(set(tags + extra_tags))

            publish_date = entry.get("upload_date")
            if publish_date and len(publish_date) == 8:
                publish_date = f"{publish_date[:4]}-{publish_date[4:6]}-{publish_date[6:]}"

            view_count = entry.get("view_count") or 0
            trend_score = _calculate_trend_score(view_count, publish_date)

            trend_data = dict(
                platform="youtube",
                video_id=video_id,
                title=title,
                description=description[:2000] if description else None,
                tags=all_tags,
                category=_infer_category(all_tags, title),
                view_count=view_count,
                like_count=entry.get("like_count") or 0,
                comment_count=entry.get("comment_count") or 0,
                channel_name=entry.get("channel") or entry.get("uploader") or "",
                publish_date=publish_date,
                trend_score=trend_score,
            )
            row_id = insert_trend(conn, **trend_data)
            if row_id:
                all_trends.append(trend_data)

            for tag in all_tags:
                update_trending_tag(conn, tag.lower(), "youtube")

    conn.close()
    logger.info("YouTube: scraped %d new trends", len(all_trends))
    return all_trends


def scrape_tiktok_trending(hashtags: list[str] | None = None, max_per_tag: int = 20) -> list[dict]:
    hashtags = hashtags or cfg("discovery.tiktok_hashtags") or ["#viral"]
    max_per_tag = max_per_tag or cfg("discovery.max_results_per_query") or 20
    conn = get_connection()
    all_trends = []

    for hashtag in hashtags:
        tag_clean = hashtag.lstrip("#")
        logger.info("TikTok hashtag search: %s", hashtag)

        search_url = f"https://www.tiktok.com/tag/{tag_clean}"
        entries = _run_ytdlp([
            "--impersonate", "chrome",
            search_url,
            "--playlist-items", f"1:{max_per_tag}",
        ], timeout=180)

        if not entries:
            logger.info("TikTok tag page failed for %s, trying direct search via YouTube", hashtag)
            yt_proxy_query = f"ytsearch{max_per_tag}:tiktok {tag_clean} trending"
            entries = _run_ytdlp([yt_proxy_query], timeout=180)
            if entries:
                entries = [e for e in entries if (e.get("duration") or 999) <= MAX_DURATION]

        if not entries:
            logger.warning("No TikTok results for: %s", hashtag)
            continue

        for entry in entries:
            video_id = entry.get("id") or entry.get("url", "")
            title = entry.get("title") or entry.get("description", "")
            description = entry.get("description", "")
            all_tags = _extract_tags_from_text(title + " " + description)

            publish_date = entry.get("upload_date")
            if publish_date and len(publish_date) == 8:
                publish_date = f"{publish_date[:4]}-{publish_date[4:6]}-{publish_date[6:]}"

            view_count = entry.get("view_count") or 0
            trend_score = _calculate_trend_score(view_count, publish_date)

            trend_data = dict(
                platform="tiktok",
                video_id=video_id,
                title=title[:500] if title else None,
                description=description[:2000] if description else None,
                tags=all_tags,
                category=_infer_category(all_tags, title),
                view_count=view_count,
                like_count=entry.get("like_count") or 0,
                comment_count=entry.get("comment_count") or 0,
                share_count=entry.get("repost_count") or entry.get("share_count") or 0,
                channel_name=entry.get("uploader") or entry.get("creator") or "",
                publish_date=publish_date,
                trend_score=trend_score,
                sound_name=entry.get("track") or entry.get("music", {}).get("title") if isinstance(entry.get("music"), dict) else None,
            )
            row_id = insert_trend(conn, **trend_data)
            if row_id:
                all_trends.append(trend_data)

            for tag in all_tags:
                update_trending_tag(conn, tag.lower(), "tiktok")

    conn.close()
    logger.info("TikTok: scraped %d new trends", len(all_trends))
    return all_trends


def run_discovery() -> dict:
    """Run full discovery pipeline. Returns summary dict."""
    logger.info("Starting trend discovery...")
    yt_trends = scrape_youtube_shorts()
    tt_trends = scrape_tiktok_trending()

    conn = get_connection()
    top = get_top_trends(conn, limit=20)
    conn.close()

    summary = {
        "youtube_new": len(yt_trends),
        "tiktok_new": len(tt_trends),
        "top_trends": len(top),
    }
    logger.info("Discovery complete: %s", summary)
    return summary


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        handlers=[
            logging.StreamHandler(),
            logging.FileHandler(Path(__file__).parent.parent / "logs" / "discovery.log"),
        ],
    )
    from models.database import init_db
    init_db()
    result = run_discovery()
    print(json.dumps(result, indent=2))
