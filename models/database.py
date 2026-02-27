import json
import os
import sqlite3
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

DB_PATH = Path(__file__).parent.parent / "data" / "yttt.db"


def get_connection() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db():
    conn = get_connection()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS trends (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            platform TEXT NOT NULL,          -- 'youtube' or 'tiktok'
            video_id TEXT,
            title TEXT,
            description TEXT,
            tags TEXT,                       -- JSON array
            category TEXT,                   -- motivational, funny, meme, news, storytime, other
            view_count INTEGER DEFAULT 0,
            like_count INTEGER DEFAULT 0,
            comment_count INTEGER DEFAULT 0,
            share_count INTEGER DEFAULT 0,
            channel_name TEXT,
            publish_date TEXT,
            trend_score REAL DEFAULT 0.0,    -- views / hours_since_publish
            sound_name TEXT,                 -- TikTok trending sound
            scraped_at TEXT NOT NULL,
            UNIQUE(platform, video_id)
        );

        CREATE TABLE IF NOT EXISTS trending_tags (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            tag TEXT NOT NULL,
            platform TEXT NOT NULL,
            frequency INTEGER DEFAULT 1,
            first_seen TEXT NOT NULL,
            last_seen TEXT NOT NULL,
            UNIQUE(tag, platform)
        );

        CREATE TABLE IF NOT EXISTS scripts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            category TEXT NOT NULL,
            hook TEXT NOT NULL,
            script_body TEXT NOT NULL,
            cta TEXT NOT NULL,
            suggested_tags TEXT,             -- JSON array
            visual_cues TEXT,                -- JSON array of search terms
            estimated_duration INTEGER,
            title_variants TEXT,             -- JSON array of alternative titles
            trend_source_ids TEXT,           -- JSON array of trend IDs that inspired this
            status TEXT DEFAULT 'pending_assets',  -- pending_assets, assets_ready, composing, composed, approved, rejected, uploaded
            created_at TEXT NOT NULL,
            updated_at TEXT
        );

        CREATE TABLE IF NOT EXISTS assets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            script_id INTEGER,
            asset_type TEXT NOT NULL,        -- 'video', 'image', 'music', 'voiceover'
            source TEXT NOT NULL,            -- 'pexels', 'pixabay', 'edge-tts', 'local'
            source_id TEXT,                  -- API ID from source
            source_url TEXT,
            local_path TEXT NOT NULL,
            search_query TEXT,
            duration REAL,                   -- seconds, for video/audio
            width INTEGER,
            height INTEGER,
            mood TEXT,                       -- for music: uplifting, dramatic, etc.
            downloaded_at TEXT NOT NULL,
            FOREIGN KEY (script_id) REFERENCES scripts(id)
        );

        CREATE TABLE IF NOT EXISTS videos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            script_id INTEGER NOT NULL,
            file_path TEXT NOT NULL,
            thumbnail_path TEXT,
            duration REAL,
            resolution TEXT,                 -- '1080x1920'
            file_size_mb REAL,
            yt_title TEXT,
            yt_description TEXT,
            yt_tags TEXT,                    -- JSON array
            tt_caption TEXT,
            status TEXT DEFAULT 'pending',   -- pending, approved, rejected, uploaded
            rejection_reason TEXT,
            created_at TEXT NOT NULL,
            reviewed_at TEXT,
            FOREIGN KEY (script_id) REFERENCES scripts(id)
        );

        CREATE TABLE IF NOT EXISTS uploads (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            video_id INTEGER NOT NULL,
            platform TEXT NOT NULL,          -- 'youtube' or 'tiktok'
            platform_video_id TEXT,          -- YouTube/TikTok video ID after upload
            platform_url TEXT,
            upload_status TEXT DEFAULT 'pending',  -- pending, uploading, success, failed
            error_message TEXT,
            scheduled_time TEXT,
            uploaded_at TEXT,
            FOREIGN KEY (video_id) REFERENCES videos(id)
        );

        CREATE INDEX IF NOT EXISTS idx_trends_platform ON trends(platform);
        CREATE INDEX IF NOT EXISTS idx_trends_category ON trends(category);
        CREATE INDEX IF NOT EXISTS idx_trends_score ON trends(trend_score DESC);
        CREATE INDEX IF NOT EXISTS idx_scripts_status ON scripts(status);
        CREATE INDEX IF NOT EXISTS idx_assets_script ON assets(script_id);
        CREATE INDEX IF NOT EXISTS idx_videos_status ON videos(status);
        CREATE INDEX IF NOT EXISTS idx_uploads_status ON uploads(upload_status);
    """)
    _migrate_schema(conn)
    conn.commit()
    conn.close()


def _migrate_schema(conn: sqlite3.Connection):
    """Add new columns and tables to existing databases."""
    try:
        conn.execute("ALTER TABLE scripts ADD COLUMN voice_override TEXT")
    except sqlite3.OperationalError:
        pass  # Column exists
    try:
        conn.execute("ALTER TABLE scripts ADD COLUMN music_override_path TEXT")
    except sqlite3.OperationalError:
        pass
    try:
        conn.execute("ALTER TABLE scripts ADD COLUMN user_selected_asset_paths TEXT")
    except sqlite3.OperationalError:
        pass
    # Rejection history: trend IDs from rejected videos/scripts — excluded from future ideation
    conn.execute("""
        CREATE TABLE IF NOT EXISTS rejected_trend_ids (
            trend_id INTEGER PRIMARY KEY,
            rejected_at TEXT NOT NULL,
            reason TEXT
        )
    """)
    # Rejection feedback: per-asset rejection counts for negative scoring
    conn.execute("""
        CREATE TABLE IF NOT EXISTS rejection_feedback (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            video_id INTEGER,
            script_id INTEGER NOT NULL,
            category TEXT NOT NULL,
            rejection_reason TEXT NOT NULL,
            rejected_at TEXT NOT NULL
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS rejection_assets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            rejection_id INTEGER NOT NULL,
            asset_type TEXT NOT NULL,
            filename TEXT NOT NULL,
            source TEXT,
            source_id TEXT,
            size_bytes INTEGER,
            mtime_real REAL,
            FOREIGN KEY (rejection_id) REFERENCES rejection_feedback(id)
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_rejection_assets_lookup ON rejection_assets(asset_type, filename, source)")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# --- Trends ---

def insert_trend(conn: sqlite3.Connection, **kwargs) -> int | None:
    kwargs.setdefault("scraped_at", _now())
    if "tags" in kwargs and isinstance(kwargs["tags"], list):
        kwargs["tags"] = json.dumps(kwargs["tags"])

    cols = ", ".join(kwargs.keys())
    placeholders = ", ".join(["?"] * len(kwargs))
    try:
        cur = conn.execute(
            f"INSERT OR IGNORE INTO trends ({cols}) VALUES ({placeholders})",
            list(kwargs.values()),
        )
        conn.commit()
        return cur.lastrowid if cur.lastrowid else None
    except sqlite3.Error:
        return None


def get_top_trends(
    conn: sqlite3.Connection,
    limit: int = 50,
    hours: int = 48,
    exclude_trend_ids: list[int] | None = None,
) -> list[dict]:
    """Return top trends. Optionally exclude trend IDs that led to rejected content."""
    cutoff = datetime.now(timezone.utc).isoformat()
    if exclude_trend_ids:
        placeholders = ",".join("?" * len(exclude_trend_ids))
        rows = conn.execute(
            f"""SELECT * FROM trends
               WHERE scraped_at >= datetime(?, '-' || ? || ' hours')
                 AND id NOT IN ({placeholders})
               ORDER BY trend_score DESC LIMIT ?""",
            (cutoff, hours, *exclude_trend_ids, limit),
        ).fetchall()
    else:
        rows = conn.execute(
            """SELECT * FROM trends
               WHERE scraped_at >= datetime(?, '-' || ? || ' hours')
               ORDER BY trend_score DESC LIMIT ?""",
            (cutoff, hours, limit),
        ).fetchall()
    return [dict(r) for r in rows]


def get_trends_by_ids(conn: sqlite3.Connection, trend_ids: list[int]) -> list[dict]:
    """Fetch trends by ID list. Returns [] if ids empty or not found."""
    if not trend_ids:
        return []
    ids = [i for i in trend_ids if isinstance(i, int)]
    if not ids:
        return []
    placeholders = ",".join("?" * len(ids))
    rows = conn.execute(
        f"SELECT * FROM trends WHERE id IN ({placeholders})",
        ids,
    ).fetchall()
    return [dict(r) for r in rows]


def get_rejected_trend_ids(conn: sqlite3.Connection) -> set[int]:
    """Return trend IDs that led to rejected content (excluded from ideation)."""
    rows = conn.execute("SELECT trend_id FROM rejected_trend_ids").fetchall()
    return {r["trend_id"] for r in rows}


def record_rejected_trend_sources(conn: sqlite3.Connection, script_id: int, reason: str | None = None):
    """Record trend IDs from a rejected script so they are excluded from future ideation."""
    row = conn.execute(
        "SELECT trend_source_ids FROM scripts WHERE id = ?", (script_id,)
    ).fetchone()
    if not row or not row["trend_source_ids"]:
        return
    try:
        ids = json.loads(row["trend_source_ids"])
    except (json.JSONDecodeError, TypeError):
        return
    if not isinstance(ids, list):
        return
    now = _now()
    for tid in ids:
        if isinstance(tid, int):
            conn.execute(
                "INSERT OR IGNORE INTO rejected_trend_ids (trend_id, rejected_at, reason) VALUES (?, ?, ?)",
                (tid, now, reason or ""),
            )
    conn.commit()


def insert_rejection_feedback(
    conn: sqlite3.Connection,
    video_id: int | None,
    script_id: int,
    category: str,
    reason: str,
) -> int:
    """Record a rejection event. Returns rejection_id."""
    cur = conn.execute(
        """INSERT INTO rejection_feedback (video_id, script_id, category, rejection_reason, rejected_at)
           VALUES (?, ?, ?, ?, ?)""",
        (video_id, script_id, category, reason, _now()),
    )
    conn.commit()
    return cur.lastrowid


def insert_rejection_assets(
    conn: sqlite3.Connection,
    rejection_id: int,
    asset_type: str,
    filename: str,
    source: str | None = None,
    source_id: str | None = None,
    size_bytes: int | None = None,
    mtime_real: float | None = None,
) -> None:
    """Record an asset that was part of a rejected video."""
    conn.execute(
        """INSERT INTO rejection_assets (rejection_id, asset_type, filename, source, source_id, size_bytes, mtime_real)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (rejection_id, asset_type, filename, source, source_id, size_bytes, mtime_real),
    )
    conn.commit()


PENALTY_PER_REJECTION = 0.5


def get_rejection_penalty(
    conn: sqlite3.Connection,
    filename: str,
    asset_type: str,
    category: str,
    reason_filter: str,
    *,
    source: str | None = None,
    source_id: str | None = None,
    size_bytes: int | None = None,
    mtime_real: float | None = None,
) -> int:
    """Return count of rejections for this asset. For user/local: require size+mtime match."""
    if not filename and not (source and source_id):
        return 0
    if size_bytes is not None and mtime_real is not None:
        # User/local asset: match filename + size + mtime
        row = conn.execute(
            """SELECT COUNT(*) FROM rejection_assets ra
               JOIN rejection_feedback rf ON ra.rejection_id = rf.id
               WHERE ra.asset_type = ? AND rf.category = ? AND rf.rejection_reason LIKE ?
                 AND ra.filename = ? AND ra.size_bytes = ? AND ra.mtime_real = ?""",
            (asset_type, category, f"%{reason_filter}%", filename, size_bytes, mtime_real),
        ).fetchone()
    else:
        # Scraped asset: match filename OR (source, source_id)
        if filename:
            row = conn.execute(
                """SELECT COUNT(*) FROM rejection_assets ra
                   JOIN rejection_feedback rf ON ra.rejection_id = rf.id
                   WHERE ra.asset_type = ? AND rf.category = ? AND rf.rejection_reason LIKE ?
                     AND ra.filename = ? AND ra.size_bytes IS NULL""",
                (asset_type, category, f"%{reason_filter}%", filename),
            ).fetchone()
        elif source and source_id:
            row = conn.execute(
                """SELECT COUNT(*) FROM rejection_assets ra
                   JOIN rejection_feedback rf ON ra.rejection_id = rf.id
                   WHERE ra.asset_type = ? AND rf.category = ? AND rf.rejection_reason LIKE ?
                     AND ra.source = ? AND ra.source_id = ? AND ra.size_bytes IS NULL""",
                (asset_type, category, f"%{reason_filter}%", source, source_id),
            ).fetchone()
        else:
            return 0
    return row[0] if row else 0


SCRIPT_REJECTION_REASONS = ("Script not engaging", "Inappropriate content")


def get_rejection_insights_by_category(conn: sqlite3.Connection) -> dict[str, list[tuple[str, int]]]:
    """Returns {category: [(reason, count), ...]} for script-related rejection reasons."""
    placeholders = ",".join("?" * len(SCRIPT_REJECTION_REASONS))
    rows = conn.execute(
        f"""SELECT category, rejection_reason, COUNT(*) AS cnt
            FROM rejection_feedback
            WHERE rejection_reason IN ({placeholders})
            GROUP BY category, rejection_reason
            ORDER BY category, cnt DESC""",
        SCRIPT_REJECTION_REASONS,
    ).fetchall()
    result: dict[str, list[tuple[str, int]]] = {}
    for r in rows:
        cat = r["category"] or "other"
        if cat not in result:
            result[cat] = []
        result[cat].append((r["rejection_reason"], r["cnt"]))
    return result


def get_rejection_guidance_for_category(conn: sqlite3.Connection, category: str) -> str:
    """Return guidance text to prepend to ideation prompt, or '' if none."""
    placeholders = ",".join("?" * len(SCRIPT_REJECTION_REASONS))
    rows = conn.execute(
        f"""SELECT rejection_reason, COUNT(*) AS cnt
            FROM rejection_feedback
            WHERE category = ? AND rejection_reason IN ({placeholders})
            GROUP BY rejection_reason
            ORDER BY cnt DESC""",
        (category, *SCRIPT_REJECTION_REASONS),
    ).fetchall()
    if not rows:
        return ""
    parts = [f"{r['rejection_reason']} ({r['cnt']})" for r in rows]
    return f"Previous rejections cited: {', '.join(parts)}. Ensure stronger hooks and more engaging content."


# Topics that can influence asset selection (music, videos, images)
ASSET_TOPICS = frozenset({"gaming", "roblox"})


def get_dominant_trend_topic(conn: sqlite3.Connection, trend_source_ids: list[int] | str | None) -> str | None:
    """Return the dominant trend category for asset selection, or None if not a known topic.
    Used to augment music/video/image queries with topic-specific terms (e.g. gaming, roblox).
    """
    if not trend_source_ids:
        return None
    if isinstance(trend_source_ids, str):
        try:
            trend_source_ids = json.loads(trend_source_ids)
        except (json.JSONDecodeError, TypeError):
            return None
    if not isinstance(trend_source_ids, list):
        return None
    ids = [i for i in trend_source_ids if isinstance(i, int)]
    if not ids:
        return None
    trends = get_trends_by_ids(conn, ids)
    if not trends:
        return None
    categories = [t.get("category") or "other" for t in trends]
    counts = Counter(categories)
    for cat, _ in counts.most_common():
        if cat in ASSET_TOPICS:
            return cat
    return None


def get_trend_categories(conn: sqlite3.Connection, hours: int = 168) -> list[str]:
    """Return distinct category values from trends (for filter dropdown)."""
    cutoff = datetime.now(timezone.utc).isoformat()
    rows = conn.execute(
        """SELECT DISTINCT COALESCE(category, 'other') AS cat FROM trends
           WHERE scraped_at >= datetime(?, '-' || ? || ' hours')
           ORDER BY cat""",
        (cutoff, hours),
    ).fetchall()
    return [r["cat"] for r in rows]


def update_trending_tag(conn: sqlite3.Connection, tag: str, platform: str):
    now = _now()
    conn.execute(
        """INSERT INTO trending_tags (tag, platform, frequency, first_seen, last_seen)
           VALUES (?, ?, 1, ?, ?)
           ON CONFLICT(tag, platform) DO UPDATE SET
             frequency = frequency + 1,
             last_seen = ?""",
        (tag, platform, now, now, now),
    )
    conn.commit()


def get_top_tags(conn: sqlite3.Connection, limit: int = 30) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM trending_tags ORDER BY frequency DESC, last_seen DESC LIMIT ?",
        (limit,),
    ).fetchall()
    return [dict(r) for r in rows]


def purge_discovery_data(conn: sqlite3.Connection) -> tuple[int, int]:
    """Delete all trends and trending_tags. Returns (trends_deleted, tags_deleted)."""
    cur_t = conn.execute("DELETE FROM trends")
    cur_tag = conn.execute("DELETE FROM trending_tags")
    conn.commit()
    return cur_t.rowcount, cur_tag.rowcount


# --- Scripts ---

def insert_script(conn: sqlite3.Connection, **kwargs) -> int:
    kwargs.setdefault("created_at", _now())
    for key in ("suggested_tags", "visual_cues", "title_variants", "trend_source_ids", "user_selected_asset_paths"):
        if key in kwargs and kwargs[key] is not None and isinstance(kwargs[key], list):
            kwargs[key] = json.dumps(kwargs[key])

    cols = ", ".join(kwargs.keys())
    placeholders = ", ".join(["?"] * len(kwargs))
    cur = conn.execute(
        f"INSERT INTO scripts ({cols}) VALUES ({placeholders})",
        list(kwargs.values()),
    )
    conn.commit()
    return cur.lastrowid


def get_scripts_by_status(conn: sqlite3.Connection, status: str) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM scripts WHERE status = ? ORDER BY created_at DESC", (status,)
    ).fetchall()
    return [dict(r) for r in rows]


def update_script_status(conn: sqlite3.Connection, script_id: int, status: str):
    conn.execute(
        "UPDATE scripts SET status = ?, updated_at = ? WHERE id = ?",
        (status, _now(), script_id),
    )
    conn.commit()


# --- Assets ---

def insert_asset(conn: sqlite3.Connection, **kwargs) -> int:
    kwargs.setdefault("downloaded_at", _now())
    cols = ", ".join(kwargs.keys())
    placeholders = ", ".join(["?"] * len(kwargs))
    cur = conn.execute(
        f"INSERT INTO assets ({cols}) VALUES ({placeholders})",
        list(kwargs.values()),
    )
    conn.commit()
    return cur.lastrowid


def get_assets_for_script(conn: sqlite3.Connection, script_id: int) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM assets WHERE script_id = ? ORDER BY asset_type, id", (script_id,)
    ).fetchall()
    return [dict(r) for r in rows]


# --- Videos ---

def insert_video(conn: sqlite3.Connection, **kwargs) -> int:
    kwargs.setdefault("created_at", _now())
    for key in ("yt_tags",):
        if key in kwargs and isinstance(kwargs[key], list):
            kwargs[key] = json.dumps(kwargs[key])

    cols = ", ".join(kwargs.keys())
    placeholders = ", ".join(["?"] * len(kwargs))
    cur = conn.execute(
        f"INSERT INTO videos ({cols}) VALUES ({placeholders})",
        list(kwargs.values()),
    )
    conn.commit()
    return cur.lastrowid


def get_videos_by_status(conn: sqlite3.Connection, status: str) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM videos WHERE status = ? ORDER BY created_at DESC", (status,)
    ).fetchall()
    return [dict(r) for r in rows]


def update_video_status(conn: sqlite3.Connection, video_id: int, status: str, **extras):
    extras["status"] = status
    extras["reviewed_at"] = _now()
    sets = ", ".join(f"{k} = ?" for k in extras.keys())
    conn.execute(
        f"UPDATE videos SET {sets} WHERE id = ?",
        list(extras.values()) + [video_id],
    )
    conn.commit()


# --- Uploads ---

def insert_upload(conn: sqlite3.Connection, **kwargs) -> int:
    cols = ", ".join(kwargs.keys())
    placeholders = ", ".join(["?"] * len(kwargs))
    cur = conn.execute(
        f"INSERT INTO uploads ({cols}) VALUES ({placeholders})",
        list(kwargs.values()),
    )
    conn.commit()
    return cur.lastrowid


def update_upload(conn: sqlite3.Connection, upload_id: int, **kwargs):
    sets = ", ".join(f"{k} = ?" for k in kwargs.keys())
    conn.execute(
        f"UPDATE uploads SET {sets} WHERE id = ?",
        list(kwargs.values()) + [upload_id],
    )
    conn.commit()


def get_pending_uploads(conn: sqlite3.Connection, platform: str | None = None) -> list[dict]:
    query = "SELECT u.*, v.file_path, v.yt_title, v.yt_description, v.yt_tags, v.tt_caption, v.thumbnail_path FROM uploads u JOIN videos v ON u.video_id = v.id WHERE u.upload_status = 'pending'"
    params = []
    if platform:
        query += " AND u.platform = ?"
        params.append(platform)
    query += " ORDER BY u.scheduled_time ASC NULLS LAST"
    rows = conn.execute(query, params).fetchall()
    return [dict(r) for r in rows]


# --- Stuck Job Recovery ---

def get_stuck_scripts(conn: sqlite3.Connection, status: str, stuck_minutes: int = 30) -> list[dict]:
    """Find scripts stuck in a transient status for longer than stuck_minutes."""
    rows = conn.execute(
        """SELECT * FROM scripts
           WHERE status = ?
             AND updated_at < datetime('now', '-' || ? || ' minutes')
           ORDER BY updated_at ASC""",
        (status, stuck_minutes),
    ).fetchall()
    return [dict(r) for r in rows]


def reset_stuck_scripts(conn: sqlite3.Connection, from_status: str, to_status: str, stuck_minutes: int = 30) -> int:
    """Reset scripts stuck in from_status back to to_status. Returns count reset."""
    now = _now()
    cur = conn.execute(
        """UPDATE scripts SET status = ?, updated_at = ?
           WHERE status = ?
             AND updated_at < datetime('now', '-' || ? || ' minutes')""",
        (to_status, now, from_status, stuck_minutes),
    )
    conn.commit()
    return cur.rowcount


# --- Cleanup Queries ---

def get_rejected_videos(conn: sqlite3.Connection) -> list[dict]:
    """Get all rejected videos that still have files on disk."""
    rows = conn.execute(
        "SELECT v.*, s.id as sid FROM videos v JOIN scripts s ON v.script_id = s.id WHERE v.status = 'rejected'"
    ).fetchall()
    return [dict(r) for r in rows]


def get_uploaded_videos(conn: sqlite3.Connection) -> list[dict]:
    """Get uploaded videos that haven't been archived yet (file still in pending dir)."""
    rows = conn.execute(
        """SELECT v.*, s.id as sid,
                  u.platform_video_id, u.platform_url, u.platform
           FROM videos v
           JOIN scripts s ON v.script_id = s.id
           JOIN uploads u ON u.video_id = v.id
           WHERE v.status = 'uploaded'
             AND u.upload_status = 'success'
           ORDER BY v.id"""
    ).fetchall()
    return [dict(r) for r in rows]


def get_script_asset_paths(conn: sqlite3.Connection, script_id: int) -> list[dict]:
    """Get all asset file paths for a script."""
    rows = conn.execute(
        "SELECT id, local_path, asset_type FROM assets WHERE script_id = ?", (script_id,)
    ).fetchall()
    return [dict(r) for r in rows]


def is_asset_shared(conn: sqlite3.Connection, local_path: str, exclude_script_id: int) -> bool:
    """Check if an asset file is referenced by another script (shared B-roll)."""
    row = conn.execute(
        "SELECT COUNT(*) FROM assets WHERE local_path = ? AND script_id != ?",
        (local_path, exclude_script_id),
    ).fetchone()
    return row[0] > 0


def delete_assets_for_script(conn: sqlite3.Connection, script_id: int):
    """Remove asset DB records for a script."""
    conn.execute("DELETE FROM assets WHERE script_id = ?", (script_id,))
    conn.commit()


def mark_video_archived(conn: sqlite3.Connection, video_id: int, archive_path: str):
    """Update the video record with the archived file path."""
    conn.execute(
        "UPDATE videos SET file_path = ?, status = 'archived', reviewed_at = ? WHERE id = ?",
        (archive_path, _now(), video_id),
    )
    conn.commit()


def delete_rejected_video_record(conn: sqlite3.Connection, video_id: int):
    """Remove video and upload records for a rejected video (call after files are deleted)."""
    conn.execute("DELETE FROM uploads WHERE video_id = ?", (video_id,))
    conn.execute("DELETE FROM videos WHERE id = ?", (video_id,))
    conn.commit()


def delete_script(conn: sqlite3.Connection, script_id: int) -> bool:
    """Delete a script and all related assets, videos, uploads. Removes DB records only; orphan files cleaned by --cleanup."""
    row = conn.execute("SELECT id FROM scripts WHERE id = ?", (script_id,)).fetchone()
    if not row:
        return False
    video_ids = [r[0] for r in conn.execute("SELECT id FROM videos WHERE script_id = ?", (script_id,)).fetchall()]
    for vid in video_ids:
        conn.execute("DELETE FROM uploads WHERE video_id = ?", (vid,))
    conn.execute("DELETE FROM videos WHERE script_id = ?", (script_id,))
    conn.execute("DELETE FROM assets WHERE script_id = ?", (script_id,))
    conn.execute("DELETE FROM scripts WHERE id = ?", (script_id,))
    conn.commit()
    return True


# --- Stats ---

def get_stats(conn: sqlite3.Connection) -> dict:
    stats = {}
    stats["total_trends"] = conn.execute("SELECT COUNT(*) FROM trends").fetchone()[0]
    stats["total_scripts"] = conn.execute("SELECT COUNT(*) FROM scripts").fetchone()[0]
    stats["total_videos"] = conn.execute("SELECT COUNT(*) FROM videos").fetchone()[0]
    stats["pending_review"] = conn.execute("SELECT COUNT(*) FROM videos WHERE status = 'pending'").fetchone()[0]
    stats["approved"] = conn.execute("SELECT COUNT(*) FROM videos WHERE status = 'approved'").fetchone()[0]
    stats["uploaded"] = conn.execute("SELECT COUNT(*) FROM videos WHERE status = 'uploaded'").fetchone()[0]
    stats["rejected"] = conn.execute("SELECT COUNT(*) FROM videos WHERE status = 'rejected'").fetchone()[0]
    stats["archived"] = conn.execute("SELECT COUNT(*) FROM videos WHERE status = 'archived'").fetchone()[0]
    return stats
